"""Real server target loop + temporary ReleaseStore; authority/provider edges named.

These component tests do NOT replace the new-package signed-decision integration
gate. Only the already-authorized gate and external adapter results are fixtures.
The server filtering, locks, target claims/results and SQLite aggregation are real.
"""
from concurrent.futures import ThreadPoolExecutor
import threading
from pathlib import Path

import pytest

from domains.channel_operations.release_executor import AdapterExecutionResult, AdapterRegistration
from modules.products import server, release_adapters
from shared_platform.native_sole_final_execution import _NativeExecution, _ACTIVE
from shared_platform.publication_platform_scheduler import run_native_platform_partitions
from shared_platform.release_store import ReleaseStore
from test_release_store import _plan


def _case(tmp_path, monkeypatch, adapter, *, first_gate_barrier=None):
    store = ReleaseStore(tmp_path / "fresh.db")
    labels = ["tiktok:LH_MY", "shopee:MY", "ozon:RU"]
    plan = store.create_plan(_plan(plan_id="fresh-native-partition-component",
        product_id="9753102468", seller_sku="7301", targets=labels,
        product_package_id="product:9753102468:7301", content_package_id="content:fresh:3models"))
    store.approve_plan(plan["plan_id"], approved_by="Kyle", user_approved=True,
                       confirmation_token=plan["confirmation_token"])
    registry = {name: AdapterRegistration(name, adapter, True, True, True, True)
                for name in release_adapters.ADAPTER_NAMES.values()}
    def invoke(platform):
        # Named fixture authority edge; no caller/HTTP can construct this scope.
        active = object.__new__(_NativeExecution)
        active.store = store
        active.plan_id = None  # This component fixture uses a legacy-approved synthetic plan.
        active.platform_scope = (platform,)
        first_gate = True
        own = [label for label in labels if label.startswith(platform.lower() + ":")]
        def gate(data, *, store):
            nonlocal first_gate
            assert data["confirmation_token"] == plan["confirmation_token"]
            assert data["offer_id"] == plan["product_id"]
            run = store.get_run("release-run:" + plan["payload_digest"][:24])
            if first_gate and first_gate_barrier is not None:
                first_gate = False
                assert run is None
                first_gate_barrier.wait(timeout=3)
            return {"dashboard": {"frozen_review_projection": True},
                "payload": plan["payload"], "plan": plan,
                "run": run,
                "registry": registry, "predecessor_run": None,
                "target_rows": [{"channel": x.split(":")[0], "site": x.split(":")[1],
                    "adapter": release_adapters.ADAPTER_NAMES[x.split(":")[0]]} for x in own],
                "execution_partition": {"platform": platform, "target_labels": own}}, None
        active.gate = gate
        active.common_dependency_verified = lambda _gate: True
        token = _ACTIVE.set(active)
        try:
            return server._publish_selected_release({"offer_id": plan["product_id"],
                "plan_id": plan["plan_id"], "confirmation_token": plan["confirmation_token"],
                "confirm_publish": True}, native_gate=active)
        finally:
            _ACTIVE.reset(token)
    return store, plan, invoke


def _result(label):
    return AdapterExecutionResult(True, True, "closed provider readback fixture",
        external_reference="synthetic:" + label,
        readback_evidence={"provider_identity": "synthetic:" + label,
                           "verified": True})


def test_actual_server_loop_other_platforms_finish_while_one_is_running(tmp_path, monkeypatch):
    tk_running = threading.Event()
    release_tk = threading.Event()
    others_returned = threading.Event()
    called = []
    finished = {}
    guard = threading.Lock()
    def adapter(request):
        with guard:
            called.append(request.target_label)
        if request.channel == "tiktok":
            tk_running.set()
            assert release_tk.wait(5)
        else:
            assert tk_running.wait(3)
        return _result(request.target_label)
    store, plan, invoke = _case(tmp_path, monkeypatch, adapter,
                                first_gate_barrier=threading.Barrier(3))
    def observe(platform):
        result = invoke(platform)
        with guard:
            finished[platform] = result
            if "SHOPEE" in finished and "OZON" in finished:
                others_returned.set()
        return result
    with ThreadPoolExecutor(max_workers=1) as outer:
        future = outer.submit(run_native_platform_partitions,
            ("TIKTOK", "SHOPEE", "OZON"), observe)
        try:
            assert others_returned.wait(3), (finished, future.result() if future.done() else "pending")
            run = store.get_run("release-run:" + plan["payload_digest"][:24])
            states = {x["target_label"]: x["status"] for x in run["targets"]}
            assert states == {"tiktok:LH_MY": "RUNNING", "shopee:MY": "SUCCEEDED", "ozon:RU": "SUCCEEDED"}
            assert finished["SHOPEE"][1]["completed"] is False
            assert finished["OZON"][1]["run"]["status"] == "RUNNING"
        finally:
            release_tk.set()
        result = future.result(timeout=5)
    assert all(code == 200 for code, _ in result.values())
    assert sorted(called) == sorted(plan["targets"])
    assert store.get_run(run["run_id"])["status"] == "SUCCEEDED"


def test_uncertain_platform_never_replays_and_does_not_block_others(tmp_path, monkeypatch):
    calls = []
    def adapter(request):
        calls.append(request.target_label)
        if request.channel == "shopee":
            raise TimeoutError("accepted outcome must be reconciled")
        return _result(request.target_label)
    store, plan, invoke = _case(tmp_path, monkeypatch, adapter)
    run_native_platform_partitions(("TIKTOK", "SHOPEE", "OZON"), invoke)
    run_id = "release-run:" + plan["payload_digest"][:24]
    before = store.get_run(run_id)
    rows = {row["target_label"]: row for row in before["targets"]}
    assert rows["shopee:MY"]["status"] == "FAILED"
    assert rows["tiktok:LH_MY"]["status"] == rows["ozon:RU"]["status"] == "SUCCEEDED"
    assert invoke("SHOPEE")[0] == 409
    after = store.get_run(run_id)
    assert {row["target_label"]: row["attempts"] for row in after["targets"]} == {
        "tiktok:LH_MY": 1, "shopee:MY": 1, "ozon:RU": 1}
    assert sorted(calls) == sorted(plan["targets"])


def test_same_native_partition_is_single_flight_and_cannot_replay(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []
    def adapter(request):
        calls.append(request.target_label)
        entered.set()
        assert release.wait(3)
        return _result(request.target_label)
    store, plan, invoke = _case(tmp_path, monkeypatch, adapter)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke, "SHOPEE")
        assert entered.wait(2), first.result() if first.done() else "pending"
        second = pool.submit(invoke, "SHOPEE")
        release.set()
        assert first.result(timeout=5)[0] == 200
        assert second.result(timeout=5)[0] == 200
    row = next(r for r in store.get_run("release-run:" + plan["payload_digest"][:24])["targets"]
               if r["target_label"] == "shopee:MY")
    assert calls == ["shopee:MY"]
    assert row["attempts"] == 1 and row["status"] == "SUCCEEDED"


def test_partition_exception_never_synthesizes_safe_zero_write_or_success():
    calls = []
    def invoke(platform):
        calls.append(platform)
        if platform == "SHOPEE":
            raise TimeoutError("possibly submitted")
        return 202, {"ok": True, "state": "SUBMITTED"}
    results = run_native_platform_partitions(("TIKTOK", "SHOPEE", "OZON"), invoke)
    assert set(calls) == {"TIKTOK", "SHOPEE", "OZON"}
    assert results["SHOPEE"][1]["outcome_unknown"] is True
    assert "external_writes_performed" not in results["SHOPEE"][1]
    assert results["OZON"][0] == 202 and results["OZON"][1]["state"] == "SUBMITTED"
    span=results["SHOPEE"][1]["orchestration_span"]
    assert span["elapsed_seconds"] >= 0 and span["started_at"] <= span["finished_at"]
    assert span["next_action"] == "RECONCILE_ORIGINAL_PARTITION_READONLY"


def test_partition_contract_rejects_untrusted_or_changed_target_scope():
    class Trusted:
        platform_scope = ("SHOPEE",)
    gate = {"plan": {"targets": ["shopee:MY", "ozon:RU"]},
        "target_rows": [{"channel": "shopee", "site": "MY"}],
        "execution_partition": {"platform": "SHOPEE", "target_labels": ["shopee:MY"]}}
    assert server._native_execution_partition(gate, Trusted())["target_labels"] == ["shopee:MY"]
    with pytest.raises(ValueError):
        server._native_execution_partition(gate, None)
    gate["execution_partition"]["target_labels"].append("ozon:RU")
    with pytest.raises(ValueError):
        server._native_execution_partition(gate, Trusted())
