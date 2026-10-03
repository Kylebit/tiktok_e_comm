from concurrent.futures import ThreadPoolExecutor

import pytest

from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.workbench_http import dispatch


@pytest.fixture
def engine(tmp_path):
    clock = [1000.0]
    e = WorkbenchEngine(tmp_path / "tasks.db", {"code_version": "v1", "skill_version": "s1"}, clock=lambda: clock[0])
    e.test_clock = clock
    for worker in ("a", "b"):
        e.register_executor(worker, ["publication", "delisting", "profit"], e.release)
    return e


def create(e, key="one", template="delisting", sku="0001"):
    return e.create(dict(template=template, source_key=key, scope=dict(skus=[sku], shops=["tiktok:MY:shop-1"], month="2026-08")))


def test_idempotency_and_changed_request(engine):
    a = create(engine)
    assert create(engine)["task_id"] == a["task_id"]
    with pytest.raises(ValueError):
        create(engine, sku="0002")


def test_empty_dashboard_reports_live_executor_and_expiry(engine):
    assert engine.dashboard()['tasks'] == []
    assert engine.dashboard()['domain_guard'] == {
        'unresolved_operation_count': 0,
        'unattached_operation_count': 0,
        'locked_resource_count': 0,
    }
    assert engine.dashboard()['executor'] == {'connected': True, 'templates': ['delisting', 'profit', 'publication']}
    engine.test_clock[0] += 301
    assert engine.dashboard()['executor'] == {'connected': False, 'templates': []}


def test_dashboard_shows_unresolved_domain_locks_without_marking_them_complete(engine):
    engine.begin_domain_operation('unattached', skus=['0001'], shops=['tiktok:MY:shop-1'])
    task = create(engine, key='other', sku='0002')['task_id']
    engine.begin_domain_operation('owned', owner_task_id=task,
                                  skus=['0002'], shops=['tiktok:MY:shop-1'])
    assert engine.dashboard()['domain_guard'] == {
        'unresolved_operation_count': 2,
        'unattached_operation_count': 1,
        'locked_resource_count': 2,
    }
    rows = {row['operation_id']: row for row in engine.dashboard()['domain_operations']}
    assert rows['unattached']['owner_task_id'] is None
    assert rows['unattached']['affected_targets'] == [{'target': 'tiktok:MY:shop-1', 'sku': '0001'}]
    assert rows['unattached']['unparsed_resource_count'] == 0
    assert rows['owned']['owner_task_id'] == task
    assert rows['owned']['affected_targets'] == [{'target': 'tiktok:MY:shop-1', 'sku': '0002'}]
    assert all(row['state'] == 'inflight' and row['resource_count'] == 1
               and row['locked_resource_count'] == 1 for row in rows.values())
    engine.complete_domain_operation('unattached', provider_readback_ref='synthetic-readback')
    assert engine.dashboard()['domain_guard'] == {
        'unresolved_operation_count': 1,
        'unattached_operation_count': 0,
        'locked_resource_count': 1,
    }
    assert [row['operation_id'] for row in engine.dashboard()['domain_operations']] == ['owned']


def test_same_unresolved_offer_cannot_overwrite_parallel_review(engine):
    def new(key, offer):
        return engine.create({'template': 'publication', 'source_key': key, 'scope': {'offer_id': offer}})['task_id']
    first, second, other = new('first', '123'), new('second', '123'), new('other', '456')
    token = engine.claim(first, 'a')['lease_token']
    assert engine.claim(second, 'b') is None
    assert engine.claim(other, 'b')
    engine.wait_for_user(first, token, kind='review', label='review', reason='R1', receipt_binding={'offer_id': '123'})
    assert engine.claim(second, 'b') is None
    engine.user_action(first, 'cancel')
    assert engine.claim(second, 'a')


def test_parallel_claim_and_same_target_exclusion(engine):
    a, b = create(engine), create(engine, "two", sku="0002")
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda x: engine.claim(*x), [(a["task_id"], "a"), (b["task_id"], "b")]))
    assert all(claims)
    duplicate = create(engine, "three", template="publication")
    assert engine.claim(duplicate["task_id"], "b") is None
    profit = create(engine, "four", template="profit")
    assert engine.claim(profit["task_id"], "b")


def test_same_task_claim_is_idempotent_and_single_owner(engine):
    task = create(engine)["task_id"]
    first = engine.claim(task, "a")
    assert engine.claim(task, "a") == first
    assert engine.claim(task, "b") is None


def test_unknown_write_expiry_blocks_retry_and_keeps_lock(engine):
    task = create(engine)["task_id"]
    claim = engine.claim(task, "a", ttl=1)
    engine.mark_external_started(task, claim["lease_token"])
    engine.test_clock[0] += 2
    assert engine.get(task)["execution_state"] == "reconciliation_required"
    for action in ("retry", "cancel"):
        with pytest.raises(ValueError):
            engine.user_action(task, action)
    assert engine.claim(create(engine, "two")["task_id"], "b") is None
    with pytest.raises(ValueError):
        engine.reconcile_external(task, {"outcome": "not_applied", "provider_readback_ref": "x"}, lambda *_: False)
    engine.reconcile_external(task, {"outcome": "not_applied", "provider_readback_ref": "x"}, lambda *_: True)
    assert engine.claim(task, "b")


def test_retry_projection_only_admits_original_local_identification(engine):
    task = create(engine, key="local-failure")["task_id"]
    token = engine.claim(task, "a")["lease_token"]
    engine.fail(task, token, "local parser failure")
    assert engine.get(task)["allowed_actions"]["retry"] is True
    assert next(t for t in engine.dashboard()["tasks"] if t["task_id"] == task)["allowed_actions"]["retry"] is True

    engine.test_clock[0] += 301
    assert engine.get(task)["allowed_actions"]["retry"] is False
    engine.register_executor("a", ["delisting"], engine.release)
    assert engine.get(task)["allowed_actions"]["retry"] is True
    assert engine.user_action(task, "retry")["execution_state"] == "queued"

    owned = create(engine, key="owned-operation", sku="0002")["task_id"]
    token = engine.claim(owned, "a")["lease_token"]
    engine.fail(owned, token, "local failure")
    engine.begin_domain_operation("unresolved", owner_task_id=owned,
                                  skus=["0002"], shops=["tiktok:MY:shop-1"])
    assert engine.get(owned)["allowed_actions"]["retry"] is False
    engine.complete_domain_operation("unresolved", provider_readback_ref="synthetic-readback")
    assert engine.get(owned)["allowed_actions"]["retry"] is False
    with pytest.raises(ValueError, match="only known safe failures"):
        engine.user_action(owned, "retry")


def test_retry_projection_fails_closed_on_approval_and_target_evidence_gap(engine):
    task = create(engine, key="later-step")["task_id"]
    token = engine.claim(task, "a")["lease_token"]
    engine.complete_step(task, token, expected_step="identify", checkpoint={"scope": "checked"})
    engine.fail(task, token, "local failure after identification")
    assert engine.get(task)["allowed_actions"]["retry"] is False
    with pytest.raises(ValueError, match="only known safe failures"):
        engine.user_action(task, "retry")

    profit = create(engine, key="profit-failure", template="profit")["task_id"]
    token = engine.claim(profit, "b")["lease_token"]
    engine.fail(profit, token, "coverage unknown")
    assert engine.get(profit)["allowed_actions"]["retry"] is False

    publication = create(engine, key="publication-failure", template="publication", sku="0003")["task_id"]
    token = engine.claim(publication, "a")["lease_token"]
    engine.fail(publication, token, "R1 unknown")
    assert engine.get(publication)["allowed_actions"]["retry"] is False


def test_retry_projection_checks_overlap_even_when_a_legacy_lock_is_missing(engine):
    task = create(engine, key="missing-lock")["task_id"]
    token = engine.claim(task, "a")["lease_token"]
    engine.fail(task, token, "local failure")
    with engine.transaction() as conn:
        resource = conn.execute("SELECT resource FROM workbench_resource_locks WHERE task_id=?", (task,)).fetchone()[0]
        conn.execute("DELETE FROM workbench_resource_locks WHERE task_id=?", (task,))
    engine.begin_domain_operation("unattached-overlap", skus=["0001"], shops=["tiktok:MY:shop-1"])
    assert engine.get(task)["allowed_actions"]["retry"] is False
    engine.complete_domain_operation("unattached-overlap", provider_readback_ref="synthetic-readback")
    other = create(engine, key="other-lock", sku="9999")["task_id"]
    with engine.transaction() as conn:
        conn.execute("INSERT INTO workbench_resource_locks VALUES(?,?)", (resource, other))
    assert engine.get(task)["allowed_actions"]["retry"] is False


def test_retry_projection_rejects_receipt_without_corresponding_event(engine):
    task = create(engine, key="receipt-without-event")["task_id"]
    token = engine.claim(task, "a")["lease_token"]
    engine.fail(task, token, "local failure")
    assert engine.get(task)["allowed_actions"]["retry"] is True
    with engine.transaction() as conn:
        conn.execute("INSERT INTO workbench_receipts VALUES(?,?,?)", ("prior-approval", task, "digest"))
    assert engine.get(task)["allowed_actions"]["retry"] is False
    with pytest.raises(ValueError, match="only known safe failures"):
        engine.user_action(task, "retry")


def test_read_only_expiry_resumes_with_checkpoint(engine):
    task = create(engine, template="profit")["task_id"]
    claim = engine.claim(task, "a", ttl=1)
    engine.record_checkpoint(task, claim["lease_token"], {"source": "snapshot-sha"})
    engine.test_clock[0] += 2
    assert engine.get(task)["execution_state"] == "queued"
    assert engine.claim(task, "b")
    assert engine.get(task)["checkpoint"] == {"source": "snapshot-sha"}


def test_approval_not_generic_and_receipt_replay_is_noop(engine):
    task = create(engine)["task_id"]
    token = engine.claim(task, "a")["lease_token"]
    engine.wait_for_user(task, token, kind="review", label="核对下架范围", reason="审核", url="/catalog", receipt_binding={"digest": "freeze1"})
    with pytest.raises(ValueError):
        engine.user_action(task, "provide-input", {"note": "approved"})
    assert dispatch(engine, "POST", "/api/orbit/tasks/" + task + "/approve", {"approved": True})[0] == 404
    for mutation in (lambda: engine.store.update_task(task, {"approval_status": "approved"}), lambda: engine.store.transition(task, "done")):
        with pytest.raises(ValueError):
            mutation()
    receipt = {"receipt_id": "receipt-1", "digest": "freeze1"}
    with pytest.raises(ValueError):
        engine.accept_domain_receipt(task, receipt, lambda *_: False)
    assert engine.accept_domain_receipt(task, receipt, lambda r, b: r["digest"] == b["digest"])
    assert not engine.accept_domain_receipt(task, receipt, lambda *_: True)
    assert engine.claim(task, "b")


def test_input_stale_action_id_rejected(engine):
    task = create(engine, template="profit")["task_id"]
    token = engine.claim(task, "a")["lease_token"]
    engine.wait_for_user(task, token, kind="input", label="提供广告账单", reason="缺失")
    with pytest.raises(ValueError):
        engine.user_action(task, "provide-input", {"note": "report", "action_id": "stale"})
    action = engine.get(task)["required_action"]
    engine.user_action(task, "provide-input", {"note": "report", "action_id": action["action_id"]})
    assert engine.get(task)["execution_state"] == "queued"


def test_version_pinned_and_restart_preserves_completed_steps(engine):
    task = create(engine, template="profit")["task_id"]
    token = engine.claim(task, "a")["lease_token"]
    engine.complete_step(task, token, expected_step="coverage", checkpoint={"end": "2026-08-20"})
    engine.fail(task, token, "local failure")
    assert engine.get(task)["allowed_actions"]["retry"] is False
    restarted = WorkbenchEngine(engine.store.path, {"code_version": "v2"}, clock=engine.clock)
    restarted.register_executor("new", ["profit"], restarted.release)
    assert restarted.claim(task, "new") is None
    restored = restarted.get(task)
    assert restored["version"]["code_version"] == "v1"
    assert restored["steps"][0]["checkpoint"] == {"end": "2026-08-20"}


def test_old_version_cannot_be_requeued_from_current_task_page(engine):
    failed = create(engine, key="failed", template="profit")["task_id"]
    token = engine.claim(failed, "a")["lease_token"]
    engine.fail(failed, token, "local failure")
    waiting = create(engine, key="waiting", template="profit")["task_id"]
    token = engine.claim(waiting, "a")["lease_token"]
    engine.wait_for_user(waiting, token, kind="input", label="补充资料", reason="缺文件")
    new = WorkbenchEngine(engine.store.path, {"code_version": "v2"}, clock=engine.clock)
    with pytest.raises(ValueError, match="another pinned runtime version"):
        new.user_action(failed, "retry")
    with pytest.raises(ValueError, match="another pinned runtime version"):
        new.user_action(waiting, "provide-input", {
            "note": "文件已经补齐",
            "action_id": engine.get(waiting)["required_action"]["action_id"],
        })
    assert engine.get(failed)["execution_state"] == "failed"
    assert engine.get(waiting)["execution_state"] == "waiting_user"


def test_completed_result_and_events_durable(engine):
    task = create(engine, template="profit")["task_id"]
    token = engine.claim(task, "a")["lease_token"]
    for i in range(4):
        engine.complete_step(task, token, expected_step=["coverage", "inputs", "calculate", "report"][i], checkpoint={"step": i}, result_url="/profit/report")
    restarted = WorkbenchEngine(engine.store.path, engine.release, clock=engine.clock)
    assert restarted.get(task)["execution_state"] == "completed"
    assert restarted.get(task)["result_url"] == "/profit/report"
    assert len([x for x in restarted.store.events(task) if x["event_type"] == "step_completed"]) == 4


def test_external_attachment_not_claimed_and_offline_is_honest(engine):
    task = create(engine, template="profit")["task_id"]
    result = engine.attach_external(task, external_id="existing-thread", owner="05", observed_at="2026-09-09T10:00:00+00:00", observed_status="running")
    assert result["execution_state"] == "external_task"
    assert result["external_task"]["live_status"] == "unknown"
    assert engine.claim(task, "a") is None
    other = create(engine, "second")["task_id"]
    engine.test_clock[0] += 100
    assert engine.get(other)["execution_state"] == "executor_offline"


def test_link_rejection_and_scope_conflict(engine):
    a = create(engine)["task_id"]
    engine.claim(a, "a")
    b = engine.create({"template": "publication", "source_key": "offer", "scope": {"offer_id": "123"}})["task_id"]
    token = engine.claim(b, "b")["lease_token"]
    with pytest.raises(ValueError):
        engine.bind_scope(b, token, {"offer_id": "123", "skus": ["0001"], "shops": ["tiktok:MY:shop-1"]})
    with pytest.raises(ValueError):
        engine.complete_step(b, token, expected_step="facts", checkpoint={}, result_url="//evil.example")


def test_external_step_requires_readback(engine):
    task = create(engine)["task_id"]
    token = engine.claim(task, "a")["lease_token"]
    engine.mark_external_started(task, token)
    with pytest.raises(ValueError):
        engine.complete_step(task, token, expected_step="identify", checkpoint={})
    engine.complete_step(task, token, expected_step="identify", checkpoint={"provider_readback_ref": "synthetic-readback"})


def test_step_completion_response_loss_does_not_finish_next_step(engine):
    task = create(engine, template="profit")["task_id"]
    token = engine.claim(task, "a")["lease_token"]
    for _ in range(2):
        result = engine.complete_step(task, token, expected_step="coverage", checkpoint={"source": "sha"})
    assert result["current_step"] == "inputs"
    assert result["steps"][1]["state"] == "running"
    with pytest.raises(ValueError):
        engine.complete_step(task, token, expected_step="report", checkpoint={})
    with pytest.raises(ValueError):
        engine.complete_step(task, token, expected_step="coverage", checkpoint={"source": "other"})


def test_bound_month_cannot_change_or_claim_product_lock(engine):
    task = create(engine, template="profit")["task_id"]
    token = engine.claim(task, "a")["lease_token"]
    scope = engine.get(task)["scope"]
    with pytest.raises(ValueError):
        engine.bind_scope(task, token, {**scope, "month": "2026-07"})
    engine.bind_scope(task, token, scope)
    assert engine.claim(create(engine, "commerce")["task_id"], "b")
    with pytest.raises(ValueError):
        engine.create({"template": "publication", "source_key": "invalid", "scope": {"offer_id": {"id": "123"}}})


def test_existing_domain_operation_and_engine_tasks_share_scope_lock(engine):
    scope = dict(skus=["0001"], shops=["tiktok:MY:shop-1"])
    first = engine.begin_domain_operation("frozen-write-1", **scope)
    assert first["acquired"]
    assert not engine.begin_domain_operation("frozen-write-1", **scope)["acquired"]
    task = create(engine)["task_id"]
    assert engine.claim(task, "a") is None
    engine.test_clock[0] += 1000
    engine.register_executor("a", ["delisting"], engine.release)
    # Wall clock expiry never releases an unknown commerce operation.
    assert engine.claim(task, "a") is None
    with pytest.raises(ValueError):
        engine.complete_domain_operation("frozen-write-1", provider_readback_ref="")
    engine.complete_domain_operation("frozen-write-1", provider_readback_ref="verified-readback")
    assert not engine.begin_domain_operation("frozen-write-1", **scope)["acquired"]
    assert engine.claim(task, "a")
    with pytest.raises(ValueError):
        engine.begin_domain_operation("another-write", **scope)
    assert engine.begin_domain_operation("owned-write", owner_task_id=task, **scope)["acquired"]


def test_exact_domain_completion_rejects_scope_drift_and_closes_atomically(engine):
    scope = dict(skus=["0988"], shops=["shopee:MY", "shopee:TH", "shopee:VN"])
    assert engine.begin_domain_operation("known-zero", **scope)["acquired"]
    with pytest.raises(ValueError, match="lock set drifted"):
        engine.complete_domain_operation_exact(
            "known-zero", skus=["0988"], shops=["shopee:MY"],
            provider_readback_ref="receipt:bad")
    assert engine.begin_domain_operation("known-zero", **scope)["state"] == "inflight"
    engine.complete_domain_operation_exact(
        "known-zero", **scope, provider_readback_ref="receipt:exact")
    completed = engine.begin_domain_operation("known-zero", **scope)
    assert completed["state"] == "completed" and completed["provider_readback_ref"] == "receipt:exact"


def test_old_domain_write_prevents_owner_cancellation_or_false_completion(engine):
    task = create(engine)["task_id"]
    token = engine.claim(task, "a")["lease_token"]
    engine.wait_for_user(task, token, kind="review", label="审核范围", reason="确认", receipt_binding={"freeze": "sha"})
    engine.begin_domain_operation("legacy-operation", owner_task_id=task, skus=["0001"], shops=["tiktok:MY:shop-1"])
    with pytest.raises(ValueError):
        engine.user_action(task, "cancel")
    with pytest.raises(ValueError):
        engine.complete_step(task, token, expected_step="identify", checkpoint={})


def test_provider_processing_wait_is_not_a_user_approval(engine):
    task = create(engine)["task_id"]
    token = engine.claim(task, "a")["lease_token"]
    engine.await_domain(task, token, label="等待官方回读", reason="PROCESSING", receipt_binding={"plan": "frozen"})
    waiting = engine.get(task)
    assert waiting["execution_state"] == "waiting_domain"
    assert waiting["required_action"] is None
    assert waiting["worker"] is None
    assert waiting["last_executor"] == "a"
    assert waiting["last_claimed_at"]
    assert waiting["pending_observation"]["receipt_binding"] == {"plan": "frozen"}
    assert engine.claim(task, "b") is None
    with pytest.raises(ValueError):
        engine.user_action(task, "cancel")
    assert engine.accept_domain_receipt(task, {"receipt_id": "verified-domain"}, lambda *_: True)
    assert engine.claim(task, "b")


def test_domain_unknown_observation_blocks_retry_without_fabricating_write(engine):
    task = create(engine)["task_id"]
    token = engine.claim(task, "a")["lease_token"]
    engine.observe_reconciliation(task, token, "domain readback unknown")
    assert engine.get(task)["execution_state"] == "reconciliation_required"
    with pytest.raises(ValueError):
        engine.user_action(task, "retry")
    event = engine.store.events(task)[0]
    assert event["event_type"] == "domain_unknown_observed"
    assert event["detail"]["request_sent_by_this_observer"] is False


def test_new_engine_version_cannot_accept_old_domain_receipt(engine):
    task = create(engine)["task_id"]
    token = engine.claim(task, "a")["lease_token"]
    engine.wait_for_user(task, token, kind="review", label="review", reason="fixture", receipt_binding={"id": "frozen"})
    new = WorkbenchEngine(engine.store.path, {"code_version": "v2"}, clock=engine.clock)
    with pytest.raises(ValueError):
        new.accept_domain_receipt(task, {"receipt_id": "from-new-version"}, lambda *_: True)
    assert engine.get(task)["execution_state"] == "waiting_user"
