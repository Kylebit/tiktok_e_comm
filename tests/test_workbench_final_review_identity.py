"""Synthetic task-ledger gates; no ReleaseStore, HTTP, or provider is used."""
import hashlib
import pytest

from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.workbench_store import _json


@pytest.fixture
def engine(tmp_path):
    e = WorkbenchEngine(tmp_path / "tasks.db", {"code_version": "synthetic"})
    e.register_executor("fixture", ["publication"], e.release)
    return e


def frozen(offer="offer-1"):
    return {"offer_id": offer, "revision": "r1", "sku": "0001",
            "round1_digest": "r1-digest", "round2_digest": "r2-digest",
            "targets": ["tiktok:MY", "shopee:MY"], "common_plan_id": "plan-1",
            "common_payload_digest": "payload-digest", "common_token_digest": "token-digest",
            "preview_digest": "preview-digest"}


def release_action(engine, key, offer="offer-1"):
    task = engine.create({"template": "publication", "source_key": key,
                          "scope": {"offer_id": offer, "skus": ["0001"],
                                    "shops": ["tiktok:MY", "shopee:MY"]}})["task_id"]
    token = engine.claim(task, "fixture")["lease_token"]
    engine.complete_step(task, token, expected_step="facts", checkpoint={"r1": "synthetic"})
    engine.complete_step(task, token, expected_step="images", checkpoint={"r2": "synthetic"})
    action = engine.wait_for_final_review(task, token, label="终审", reason="frozen",
                                          receipt_binding=frozen(offer))
    return task, action


def decision(task, action):
    binding = action["receipt_binding"]
    return {"receipt_id": "final-review:decision-1:" + task + ":" + action["action_id"],
            "task_id": task, "action_id": action["action_id"],
            "generation": binding["generation"], "step_index": binding["step_index"],
            "binding_digest": hashlib.sha256(_json(binding).encode()).hexdigest(),
            "decision_id": "decision-1", "contract_digest": "contract-1",
            "common_plan_id": binding["common_plan_id"], "preview_digest": binding["preview_digest"]}


def verified(receipt, binding):
    return receipt["contract_digest"] == "contract-1" and binding["common_plan_id"] == "plan-1"


def test_original_action_is_durable_and_receipt_recovers_once(engine):
    task, action = release_action(engine, "first")
    assert engine.get(task)["review_mode"] == "single-final-review/v1"
    assert engine.get(task)["generation"] == 1
    assert engine.store.events(task)[0]["detail"] == action
    restarted = WorkbenchEngine(engine.store.path, engine.release)
    receipt = decision(task, action)
    assert restarted.accept_final_review_receipt(task, receipt, verified)
    assert not restarted.accept_final_review_receipt(task, receipt, verified)
    assert restarted.get(task)["current_step"] == "release"
    assert restarted.get(task)["execution_state"] == "awaiting_execution_authority"
    assert restarted.get(task)["generation"] == 2
    assert restarted.get(task)["required_action"] is None
    assert restarted.claim(task, "fixture") is None
    with pytest.raises(ValueError, match="execution authorization"):
        restarted.begin_domain_operation("would-execute", owner_task_id=task,
                                           skus=["0001"], shops=["tiktok:MY"])
    with pytest.raises(ValueError):
        restarted.accept_domain_receipt(task, {"receipt_id": "generic"}, lambda *_: True)


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(action_id="stale"),
    lambda r: r.update(generation=0),
    lambda r: r.update(step_index=1),
    lambda r: r.update(binding_digest="stale"),
    lambda r: r.update(common_plan_id="other"),
    lambda r: r.update(preview_digest="other"),
    lambda r: r.update(task_id="other"),
])
def test_stale_receipt_fails_without_consuming_action(engine, mutate):
    task, action = release_action(engine, "first")
    receipt = decision(task, action)
    mutate(receipt)
    with pytest.raises(ValueError):
        engine.accept_final_review_receipt(task, receipt, verified)
    assert engine.get(task)["required_action"] == action


def test_copied_action_to_second_task_has_no_original_event(engine):
    task, action = release_action(engine, "first")
    second = engine.create({"template": "publication", "source_key": "second",
                            "scope": {"offer_id": "offer-1", "skus": ["0001"],
                                      "shops": ["tiktok:MY", "shopee:MY"]}})["task_id"]
    with engine.transaction() as conn:
        conn.execute("INSERT INTO workbench_review_identity VALUES(?,?,?)",
                     (second, "single-final-review/v1", 1))
        conn.execute("UPDATE workbench_execution SET step_index=2,state='waiting_user',action_json=? WHERE task_id=?",
                     (_json(action), second))
    forged = decision(second, action)
    with pytest.raises(ValueError):
        engine.accept_final_review_receipt(second, forged, verified)
    assert engine.get(task)["required_action"] == action


@pytest.mark.parametrize("change", [
    "missing_event", "changed_scope", "changed_step", "changed_generation", "changed_action",
])
def test_current_transaction_rejects_ledger_drift(engine, change):
    task, action = release_action(engine, "first")
    with engine.transaction() as conn:
        if change == "missing_event":
            conn.execute("DELETE FROM workbench_events WHERE task_id=? AND event_type='user_action_required'", (task,))
        elif change == "changed_scope":
            conn.execute("UPDATE workbench_execution SET scope_json=? WHERE task_id=?",
                         (_json({"offer_id": "other", "skus": [], "shops": []}), task))
        elif change == "changed_step":
            conn.execute("UPDATE workbench_execution SET step_index=3 WHERE task_id=?", (task,))
        elif change == "changed_generation":
            conn.execute("UPDATE workbench_review_identity SET generation=2 WHERE task_id=?", (task,))
        else:
            changed = dict(action, action_id="new-action")
            conn.execute("UPDATE workbench_execution SET action_json=? WHERE task_id=?", (_json(changed), task))
    with pytest.raises(ValueError):
        engine.accept_final_review_receipt(task, decision(task, action), verified)


def test_receipt_identity_conflict_and_legacy_unchanged(engine):
    task, action = release_action(engine, "first")
    receipt = decision(task, action)
    assert engine.accept_final_review_receipt(task, receipt, verified)
    with pytest.raises(ValueError):
        engine.accept_final_review_receipt(task, dict(receipt, contract_digest="other"), verified)
    legacy = engine.create({"template": "publication", "source_key": "legacy",
                            "scope": {"offer_id": "legacy-offer"}})
    assert legacy["review_mode"] == "legacy" and legacy["generation"] == 0
    with pytest.raises(ValueError):
        engine.accept_final_review_receipt(legacy["task_id"], receipt, verified)


def test_cancel_invalidates_original_action_and_advances_epoch(engine):
    task, action = release_action(engine, "first")
    engine.user_action(task, "cancel")
    saved = engine.get(task)
    assert saved["generation"] == 2
    assert saved["required_action"] is None
    with pytest.raises(ValueError):
        engine.accept_final_review_receipt(task, decision(task, action), verified)
