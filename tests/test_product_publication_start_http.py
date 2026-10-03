from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
import urllib.error
import urllib.parse
import urllib.request

import pytest

from domains.product_operations import build_approved_publication_snapshot
from modules.products import server as product_server
from shared_platform.product_publication_reports import ProductPublicationReportStore
from shared_platform.product_publication_runs import ProductPublicationRunStore
from test_approved_publication_snapshot import _approved_plan


@pytest.fixture(autouse=True)
def isolated_catalog_sink(monkeypatch, tmp_path):
    """HTTP runner tests never discover credentials or the real catalog DB."""
    monkeypatch.delenv("ORBIT_PUBLICATION_REQUIRE_VERIFIED_RUNTIME", raising=False)
    class SyntheticCatalogSink:
        def __init__(self):
            self.requests = []

        def begin(self, request):
            self.requests.append(request)

    sink = SyntheticCatalogSink()
    credential_path = tmp_path / "ozon-publication-credentials.json"
    credential_path.write_text(
        json.dumps({"client_id": "4953064", "api_key": "synthetic-secret"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORBIT_OZON_CREDENTIALS_PATH", str(credential_path))
    monkeypatch.setenv(
        "ORBIT_OZON_CREDENTIALS_SHA256",
        hashlib.sha256(credential_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setenv("ORBIT_OZON_EXPECTED_ACCOUNT_ID", "4953064")
    monkeypatch.setattr(product_server, "_catalog_publication_sync", lambda: sink)
    try:
        from shared_platform import operations_domain_guard
    except ImportError:
        pass  # The historical HTTP baseline predates operations coordination.
    else:
        monkeypatch.setattr(operations_domain_guard, "engine_for", lambda _root: None)
    return sink


class _SnapshotStore:
    def __init__(self, snapshot: dict) -> None:
        self.snapshot = deepcopy(snapshot)

    def approved_publication_snapshot(self, **_kwargs):
        return deepcopy(self.snapshot)


def _result(request, status: str = "PUBLISHED") -> dict:
    return {
        "schema_version": "product-publication-platform-result/v1",
        "platform": request.platform,
        "targets": [
            {"target_label": target, "status": status}
            for target in request.target_labels
        ],
        "dispatch_attempted": True,
        "readback_completed": True,
        "external_write_count": len(request.target_labels),
        "requires_human_action": status == "FAILED",
    }


@pytest.fixture
def publication_start_server(tmp_path: Path, monkeypatch):
    snapshot = build_approved_publication_snapshot(_approved_plan()).payload()
    report_store = ProductPublicationReportStore(
        tmp_path / "orbit_platform.db",
        reports_root=tmp_path / "reports" / "product-publication",
    )
    run_store = ProductPublicationRunStore(tmp_path / "orbit_platform.db")
    calls: list[object] = []

    def executor(request):
        calls.append(request)
        return _result(request)

    monkeypatch.setattr(product_server, "_release_store", lambda: _SnapshotStore(snapshot))
    monkeypatch.setattr(
        product_server, "_product_publication_report_store", lambda: report_store
    )
    monkeypatch.setattr(
        product_server, "_product_publication_run_store", lambda: run_store
    )
    monkeypatch.setattr(
        product_server,
        "_product_publication_platform_executors",
        lambda: {"TIKTOK": executor, "SHOPEE": executor, "OZON": executor},
    )
    for legacy_name in (
        "_start_tiktok_release",
        "_start_shopee_global_release",
        "_start_ozon_release",
    ):
        monkeypatch.setattr(
            product_server,
            legacy_name,
            lambda _data, name=legacy_name: pytest.fail(f"legacy route called: {name}"),
        )

    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"http://127.0.0.1:{server.server_address[1]}",
            snapshot,
            report_store,
            calls,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(base_url: str, path: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _get(base_url: str, path: str, query: dict[str, str]) -> tuple[int, dict]:
    url = base_url + path + "?" + urllib.parse.urlencode(query)
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, json.loads(response.read())


def _wait_for_report(report_store, *, report_id: str, offer_id: str, timeout=3):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        report = report_store.get_report(report_id=report_id, offer_id=offer_id)
        if report is not None:
            return report
        sleep(0.01)
    raise AssertionError("publication report did not reach a terminal fact")


@pytest.mark.parametrize(
    ("path", "platform"),
    [
        ("/api/product-workspace/publish-tiktok", "TIKTOK"),
        ("/api/product-workspace/publish-shopee-global", "SHOPEE"),
        ("/api/product-workspace/publish-ozon", "OZON"),
    ],
)
def test_each_product_center_button_starts_exactly_one_runner_platform(
    publication_start_server, path, platform
):
    base_url, snapshot, report_store, calls = publication_start_server

    status, body = _post(
        base_url,
        path,
        {
            "offer_id": snapshot["offer_id"],
            "plan_id": snapshot["plan_id"],
            "platform": "ATTACKER_CONTROLLED",
            "run_id": "attacker-run",
            "platform_executors": {"OZON": "not-callable"},
        },
    )

    assert status == 202
    assert body == {
        "ok": True,
        "schema_version": "product-publication-start/v1",
        "platform": platform,
        "report_id": body["report_id"],
        "run_id": body["run_id"],
    }
    assert body["report_id"] == f"publication-report:{body['run_id']}"
    assert body["run_id"] != "attacker-run"
    persisted = _wait_for_report(
        report_store,
        report_id=body["report_id"], offer_id=snapshot["offer_id"]
    )
    assert len(calls) == 1
    assert calls[0].platform == platform
    assert persisted["status"] == "PUBLISHED"
    assert [row["platform"] for row in persisted["summary"]["platforms"]] == [
        platform
    ]
    get_status, get_body = _get(
        base_url,
        "/api/product-workspace/publication-report",
        {"offer_id": snapshot["offer_id"], "report_id": body["report_id"]},
    )
    assert get_status == 200
    assert get_body["report"]["run_id"] == body["run_id"]
    assert get_body["report"]["status"] == "PUBLISHED"


def test_unavailable_server_executor_persists_failed_report(tmp_path, monkeypatch):
    snapshot = build_approved_publication_snapshot(_approved_plan()).payload()
    report_store = ProductPublicationReportStore(
        tmp_path / "orbit_platform.db",
        reports_root=tmp_path / "reports" / "product-publication",
    )
    run_store = ProductPublicationRunStore(tmp_path / "orbit_platform.db")
    monkeypatch.setattr(product_server, "_release_store", lambda: _SnapshotStore(snapshot))
    monkeypatch.setattr(
        product_server, "_product_publication_report_store", lambda: report_store
    )
    monkeypatch.setattr(
        product_server, "_product_publication_run_store", lambda: run_store
    )
    monkeypatch.setattr(
        product_server, "_product_publication_platform_executors", lambda: {}
    )

    status, body = product_server._start_product_publication(
        {"offer_id": snapshot["offer_id"], "plan_id": snapshot["plan_id"]},
        platform="SHOPEE",
    )

    assert status == 202
    report = _wait_for_report(
        report_store,
        report_id=body["report_id"],
        offer_id=snapshot["offer_id"],
    )
    assert report["status"] == "FAILED"
    assert report["summary"]["platforms"][0]["status"] == "FAILED"
    assert report["summary"]["evidence"] == {
        "snapshot_verified": True,
        "dispatch_attempted": False,
        "readback_completed": False,
        "external_write_count": 0,
    }


def test_start_returns_before_provider_work_finishes(tmp_path, monkeypatch):
    """The HTTP start seam must not inherit provider latency.

    The caller runs in a test thread so the baseline can be released after the
    assertion without hanging the suite.
    """

    snapshot = build_approved_publication_snapshot(_approved_plan()).payload()
    report_store = ProductPublicationReportStore(
        tmp_path / "orbit_platform.db",
        reports_root=tmp_path / "reports" / "product-publication",
    )
    run_store = ProductPublicationRunStore(tmp_path / "orbit_platform.db")
    release_executor = Event()
    entered_executor = Event()

    def slow_executor(request):
        entered_executor.set()
        assert release_executor.wait(timeout=2)
        return _result(request)

    monkeypatch.setattr(product_server, "_release_store", lambda: _SnapshotStore(snapshot))
    monkeypatch.setattr(
        product_server, "_product_publication_report_store", lambda: report_store
    )
    monkeypatch.setattr(
        product_server, "_product_publication_run_store", lambda: run_store
    )
    monkeypatch.setattr(
        product_server,
        "_product_publication_platform_executors",
        lambda: {"TIKTOK": slow_executor},
    )

    result: dict[str, object] = {}

    def invoke_start():
        result["value"] = product_server._start_product_publication(
            {"offer_id": snapshot["offer_id"], "plan_id": snapshot["plan_id"]},
            platform="TIKTOK",
        )

    caller = Thread(target=invoke_start, daemon=True)
    caller.start()
    assert entered_executor.wait(timeout=1)
    caller.join(timeout=0.1)
    returned_before_provider = not caller.is_alive()
    try:
        if returned_before_provider:
            status, body = result["value"]
            report_before_provider = report_store.get_report(
                report_id=body["report_id"], offer_id=snapshot["offer_id"]
            )
        else:
            status = body = report_before_provider = None
    finally:
        release_executor.set()
        caller.join(timeout=2)

    assert returned_before_provider is True
    assert status == 202
    assert report_before_provider is None
    _wait_for_report(
        report_store,
        report_id=body["report_id"],
        offer_id=snapshot["offer_id"],
    )


def test_worker_launch_failure_is_durable_failed_without_platform_call(
    tmp_path, monkeypatch
):
    snapshot = build_approved_publication_snapshot(_approved_plan()).payload()
    report_store = ProductPublicationReportStore(
        tmp_path / "orbit_platform.db",
        reports_root=tmp_path / "reports" / "product-publication",
    )
    run_store = ProductPublicationRunStore(tmp_path / "orbit_platform.db")
    monkeypatch.setattr(product_server, "_release_store", lambda: _SnapshotStore(snapshot))
    monkeypatch.setattr(
        product_server, "_product_publication_report_store", lambda: report_store
    )
    monkeypatch.setattr(
        product_server, "_product_publication_run_store", lambda: run_store
    )
    monkeypatch.setattr(
        product_server,
        "_product_publication_platform_executors",
        lambda: {"OZON": lambda _request: pytest.fail("executor must not run")},
    )
    monkeypatch.setattr(
        product_server,
        "_launch_product_publication_background",
        lambda _callback: (_ for _ in ()).throw(RuntimeError("thread unavailable")),
    )

    status, body = product_server._start_product_publication(
        {"offer_id": snapshot["offer_id"], "plan_id": snapshot["plan_id"]},
        platform="OZON",
    )

    assert status == 202
    run = run_store.get_run(
        report_id=body["report_id"], offer_id=snapshot["offer_id"]
    )
    assert run["state"] == "FAILED"
    assert run["failure_code"] == "WORKER_LAUNCH_FAILED"
    assert report_store.get_report(
        report_id=body["report_id"], offer_id=snapshot["offer_id"]
    ) is None


def test_execution_identity_drift_fails_before_running_or_dispatch(tmp_path, monkeypatch):
    snapshot = build_approved_publication_snapshot(_approved_plan()).payload()
    db_path = tmp_path / "orbit_platform.db"
    report_store = ProductPublicationReportStore(
        db_path, reports_root=tmp_path / "reports" / "product-publication"
    )
    run_store = ProductPublicationRunStore(db_path)
    calls = []
    expected = {"skill_digest": "1" * 64, "git_commit": "2" * 40, "code_digest": "3" * 64}
    current = {**expected, "code_digest": "4" * 64}
    created = run_store.create_run(
        run_id="identity-drift-run",
        offer_id=snapshot["offer_id"],
        revision=snapshot["product_revision"],
        plan_id=snapshot["plan_id"],
        snapshot_digest=snapshot["snapshot_digest"],
        platform_scope=("TIKTOK",),
        target_count=2,
        execution_identity=expected,
    )
    monkeypatch.setattr(product_server, "_product_publication_execution_identity", lambda _platform: current)

    product_server._execute_product_publication_background(
        run_id=created.run_id,
        offer_id=snapshot["offer_id"],
        snapshot_digest=snapshot["snapshot_digest"],
        platform="TIKTOK",
        executor=lambda request: calls.append(request),
        release_store=_SnapshotStore(snapshot),
        report_store=report_store,
        run_store=run_store,
        expected_execution_identity=expected,
    )

    run = run_store.get_run_by_id(run_id=created.run_id)
    assert run["state"] == "FAILED"
    assert run["failure_code"] == "EXECUTION_IDENTITY_DRIFT"
    assert run["event_count"] == 2
    assert calls == []
    assert report_store.get_report_by_run(run_id=created.run_id) is None


def test_invalid_frozen_identity_fails_before_run_or_platform(tmp_path, monkeypatch):
    snapshot = build_approved_publication_snapshot(_approved_plan()).payload()
    db_path = tmp_path / "orbit_platform.db"
    run_store = ProductPublicationRunStore(db_path)
    monkeypatch.setattr(product_server, "_release_store", lambda: _SnapshotStore(snapshot))
    monkeypatch.setattr(
        product_server,
        "_product_publication_report_store",
        lambda: ProductPublicationReportStore(
            db_path,
            reports_root=tmp_path / "reports" / "product-publication",
        ),
    )
    monkeypatch.setattr(
        product_server, "_product_publication_run_store", lambda: run_store
    )
    monkeypatch.setattr(
        product_server,
        "_product_publication_platform_executors",
        lambda: {"SHOPEE": lambda _request: pytest.fail("executor must not run")},
    )

    status, body = product_server._start_product_publication(
        {"offer_id": snapshot["offer_id"], "plan_id": "omnichannel:wrong"},
        platform="SHOPEE",
    )

    assert status == 409
    assert body["ok"] is False
    assert "plan identity conflicts" in body["error"]
    assert db_path.exists() is False


def test_recovery_start_creates_new_authority_run_then_replays_exact_request(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from shared_platform import product_publication_runner as runner_module
    from shared_platform import publication_autopilot
    from shared_platform import shopee_regional_recovery
    from modules.shopee import skill_regions

    labels = ("shopee:MY", "shopee:TH", "shopee:VN")
    snapshot_digest = "sha256:" + "a" * 64
    manifest_digest = "sha256:" + "4" * 64
    execution_identity = {"skill_digest": "b" * 64, "git_commit": "c" * 40,
                          "code_digest": "d" * 64}
    prepared = SimpleNamespace(
        offer_id="3956742887", revision=5, plan_id="plan-1",
        snapshot_digest=snapshot_digest, platform_scope=("SHOPEE",),
        target_labels_by_platform={"SHOPEE": labels}, snapshot={"snapshot_digest": snapshot_digest},
    )
    db_path = tmp_path / "runs.db"
    run_store = ProductPublicationRunStore(db_path)
    original = run_store.create_run(
        run_id="product-center-shopee-old30c", offer_id=prepared.offer_id,
        revision=prepared.revision, plan_id=prepared.plan_id,
        snapshot_digest=prepared.snapshot_digest, platform_scope=("SHOPEE",),
        target_count=4, execution_identity=execution_identity,
    )
    run_store.mark_running(run_id=original.run_id)
    run_store.mark_completed(run_id=original.run_id, final_report_id=original.report_id)
    old_report = {
        "offer_id": prepared.offer_id, "plan_id": prepared.plan_id,
        "snapshot": {"digest": prepared.snapshot_digest}, "report_id": original.report_id,
        "status": "PARTIAL", "summary": {"evidence": {"external_write_count": 39}},
        "targets": [
            {"target_label": "shopee:PH", "status": "PUBLISHED", "evidence": {}},
            *[{"target_label": label, "status": "FAILED", "evidence": {}} for label in labels],
        ],
    }
    reports_root = tmp_path / "reports" / "product-publication"
    manifest_path = reports_root.parent / "product-preparation" / prepared.offer_id \
        / "shopee-recovery-candidates" / (manifest_digest.removeprefix("sha256:") + ".json")
    manifest_path.parent.mkdir(parents=True)
    manifest = {
        "manifest_digest": manifest_digest, "target_labels": list(labels),
        "prior_run_id": original.run_id, "prior_report_digest": "sha256:" + "9" * 64,
        "targets": [{"target_label": label,
                     "mutation_budget": {"shared_maximum": 0, "target_maximum": 3}}
                    for label in labels],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    operations_root = tmp_path / "operations"; operations_root.mkdir()

    class Reports:
        def __init__(self):
            self.reports_root = reports_root
        def get_report_by_run(self, *, run_id):
            return deepcopy(old_report) if run_id == original.run_id else None

    candidate = {"candidate_digest": "6" * 64}
    approval = {"approval_digest": "7" * 64}
    launches = []
    provider_calls = []
    monkeypatch.setenv("ORBIT_SHOPEE_RECOVERY_ENABLED", "1")
    monkeypatch.setenv("ORBIT_OPERATIONS_DATA_ROOT", str(operations_root))
    monkeypatch.setattr(runner_module, "prepare_product_publication_run", lambda **_: prepared)
    monkeypatch.setattr(product_server, "_release_store", lambda: object())
    monkeypatch.setattr(product_server, "_product_publication_report_store", lambda: Reports())
    monkeypatch.setattr(product_server, "_product_publication_run_store", lambda: run_store)
    monkeypatch.setattr(product_server, "_product_publication_execution_identity", lambda _p: execution_identity)
    monkeypatch.setattr(publication_autopilot, "resolve_persisted_execution_authority",
                        lambda **_: (candidate, approval))
    monkeypatch.setattr(shopee_regional_recovery, "validate_recovery_manifest",
                        lambda value, **_: deepcopy(dict(value)))
    monkeypatch.setattr(shopee_regional_recovery, "build_recovery_executor",
                        lambda **_: (lambda request: provider_calls.append(request)))
    monkeypatch.setattr(skill_regions, "OfficialShopeeRegionRuntime", lambda: object())
    monkeypatch.setattr(product_server, "_launch_product_publication_background",
                        lambda callback: launches.append(callback))
    request = {
        "offer_id": prepared.offer_id, "plan_id": prepared.plan_id,
        "snapshot_digest": prepared.snapshot_digest, "candidate_digest": candidate["candidate_digest"],
        "target_scope": list(labels), "recovery_manifest_digest": manifest_digest,
    }
    status, first = product_server._start_product_publication(request, platform="SHOPEE")
    assert status == 202 and first.get("reused") is not True
    assert first["run_id"] != original.run_id
    assert len(launches) == 1 and provider_calls == []
    created = run_store.get_run_by_id(run_id=first["run_id"])
    assert created["request_identity"] == {
        "kind": "SHOPEE_RECOVERY", "authority_digest": manifest_digest}

    status, second = product_server._start_product_publication(request, platform="SHOPEE")
    assert status == 202 and second["reused"] is True
    assert second["run_id"] == first["run_id"]
    assert len(launches) == 1 and provider_calls == []


def test_product_center_script_polls_report_and_uses_only_public_report_states():
    script = Path("web/static/product_workspace.js").read_text(encoding="utf-8")

    assert "/api/product-workspace/publication-report?" in script
    assert "report_id" in script
    assert "run_id" in script
    assert "PUBLICATION_REPORT_STATUSES" in script
    for status in ("PUBLISHED", "PROCESSING", "PARTIAL", "FAILED"):
        assert f'"{status}"' in script
    platform_state = script[
        script.index("const platformPublish = {"):
        script.index("const platformPublishNames = {")
    ]
    renderer = script[
        script.index("function renderOneClickExecution("):
        script.index("function focusOneClickTarget(")
    ]
    for forbidden in ("IDLE", "PUBLISHING", "SUCCEEDED"):
        assert forbidden not in platform_state
        assert forbidden not in renderer


REAL_REPORTLESS_REPO = Path(__file__).parents[1]
REAL_REPORTLESS_OPS = Path(r"D:\OrbitHive\runtime\operations-stable-1c5f5260")
REAL_REPORTLESS_MANIFEST = REAL_REPORTLESS_REPO / r"reports\product-preparation\3956742887\shopee-recovery-candidates\d7c993cb34165105d56eeae287529309bc12a6945cdafec8f4f2a9272a22f173.json"
REAL_REPORTLESS_RELEASE_DB = Path(
    r"D:\OrbitHive\candidate-20260908\frozen-shopee-master-price\data\orbit_platform.db")


@pytest.mark.skipif(not all(path.is_file() for path in (
    REAL_REPORTLESS_MANIFEST, REAL_REPORTLESS_RELEASE_DB,
    REAL_REPORTLESS_OPS / "tasks.db", REAL_REPORTLESS_REPO / "data" / "orbit_platform.db",
)), reason="exact attested reportless continuation is unavailable")
def test_reportless_continuation_http_claims_once_without_provider(
        tmp_path, monkeypatch):
    import shutil
    import sqlite3
    from modules.shopee import skill_regions
    from shared_platform.release_store import ReleaseStore

    manifest = json.loads(REAL_REPORTLESS_MANIFEST.read_text(encoding="utf-8"))
    copied_db = tmp_path / "runs.db"
    shutil.copy2(REAL_REPORTLESS_REPO / "data" / "orbit_platform.db", copied_db)
    # The canonical database now contains the production attempt for this exact
    # authority.  Remove only that request identity from the isolated copy so
    # this regression continues to exercise first-claim creation and replay.
    with sqlite3.connect(copied_db) as connection:
        rows = connection.execute(
            "SELECT run_id, request_identity_json FROM product_publication_runs"
        ).fetchall()
        claimed = [
            run_id for run_id, raw_identity in rows
            if raw_identity is not None
            and json.loads(raw_identity).get("kind") == "SHOPEE_RECOVERY_CONTINUATION"
            and json.loads(raw_identity).get("authority_digest") == manifest["manifest_digest"]
        ]
        for run_id in claimed:
            connection.execute(
                "DELETE FROM product_publication_run_events WHERE run_id = ?", (run_id,)
            )
            connection.execute(
                "DELETE FROM product_publication_runs WHERE run_id = ?", (run_id,)
            )
        connection.commit()
    run_store = ProductPublicationRunStore(copied_db)
    report_store = ProductPublicationReportStore(
        copied_db, reports_root=REAL_REPORTLESS_REPO / "reports" / "product-publication")
    launches = []
    provider_calls = []
    monkeypatch.setenv("ORBIT_SHOPEE_RECOVERY_ENABLED", "1")
    monkeypatch.setenv("ORBIT_OPERATIONS_DATA_ROOT", str(REAL_REPORTLESS_OPS))
    monkeypatch.setattr(product_server, "_release_store",
                        lambda: ReleaseStore(REAL_REPORTLESS_RELEASE_DB))
    monkeypatch.setattr(product_server, "_product_publication_report_store",
                        lambda: report_store)
    monkeypatch.setattr(product_server, "_product_publication_run_store",
                        lambda: run_store)
    monkeypatch.setattr(product_server, "_launch_product_publication_background",
                        lambda callback: launches.append(callback))
    monkeypatch.setattr(skill_regions, "OfficialShopeeRegionRuntime", lambda: object())
    request = {
        "offer_id": manifest["offer_id"], "plan_id": manifest["plan_id"],
        "snapshot_digest": manifest["execution_snapshot_digest"],
        "candidate_digest": manifest["candidate_digest"],
        "target_scope": manifest["target_labels"],
        "recovery_manifest_digest": manifest["manifest_digest"],
    }
    status, first = product_server._start_product_publication(request, platform="SHOPEE")
    assert status == 202 and first.get("reused") is not True
    assert first["run_id"] not in {
        manifest["direct_predecessor_run_id"], manifest["original_publication_run_id"],
        "product-center-shopee-1e6e675a650ad03208a6f8d8996408a6",
    }
    assert len(launches) == 1 and provider_calls == []
    stored = run_store.get_run_by_id(run_id=first["run_id"])
    assert stored["request_identity"] == {
        "kind": "SHOPEE_RECOVERY_CONTINUATION",
        "authority_digest": manifest["manifest_digest"],
        "predecessor_run_id": manifest["direct_predecessor_run_id"],
        "source_receipt_digest": manifest["source_receipt_digest"],
        "preflight_digest": manifest["preflight_digest"],
        "evidence_relocation_digest": manifest["evidence_relocation_digest"],
    }
    status, second = product_server._start_product_publication(request, platform="SHOPEE")
    assert status == 202 and second["reused"] is True
    assert second["run_id"] == first["run_id"]
    assert len(launches) == 1 and provider_calls == []


REAL_KNOWN_ZERO_MANIFEST = REAL_REPORTLESS_REPO / (
    "reports/product-preparation/3956742887/shopee-recovery-candidates/"
    "6dfad2346f1e491564e7fcc6b0695dbd981fe65af7e50d8d840ce38bc733842d.json"
)


@pytest.mark.skipif(not all(path.is_file() for path in (
    REAL_KNOWN_ZERO_MANIFEST, REAL_REPORTLESS_RELEASE_DB,
    REAL_REPORTLESS_OPS / "tasks.db", REAL_REPORTLESS_REPO / "data" / "orbit_platform.db",
)), reason="exact attested known-zero continuation is unavailable")
def test_known_zero_continuation_http_claims_once_without_provider(
        tmp_path, monkeypatch):
    import shutil
    import sqlite3
    from modules.shopee import skill_regions
    from shared_platform import operations_domain_guard
    from shared_platform import product_publication_runner as runner_module
    from shared_platform.release_store import ReleaseStore
    from shared_platform.workbench_engine import WorkbenchEngine

    manifest = json.loads(REAL_KNOWN_ZERO_MANIFEST.read_text(encoding="utf-8"))
    copied_db = tmp_path / "runs.db"
    shutil.copy2(REAL_REPORTLESS_REPO / "data" / "orbit_platform.db", copied_db)
    run_store = ProductPublicationRunStore(copied_db)
    report_store = ProductPublicationReportStore(
        copied_db, reports_root=REAL_REPORTLESS_REPO / "reports" / "product-publication")
    launches = []
    provider_calls = []
    monkeypatch.setenv("ORBIT_SHOPEE_RECOVERY_ENABLED", "1")
    monkeypatch.setenv("ORBIT_OPERATIONS_DATA_ROOT", str(REAL_REPORTLESS_OPS))
    monkeypatch.setattr(product_server, "_release_store",
                        lambda: ReleaseStore(REAL_REPORTLESS_RELEASE_DB))
    monkeypatch.setattr(product_server, "_product_publication_report_store",
                        lambda: report_store)
    monkeypatch.setattr(product_server, "_product_publication_run_store",
                        lambda: run_store)
    monkeypatch.setattr(product_server, "_launch_product_publication_background",
                        lambda callback: launches.append(callback))
    monkeypatch.setattr(skill_regions, "OfficialShopeeRegionRuntime", lambda: object())
    request = {
        "offer_id": manifest["offer_id"], "plan_id": manifest["plan_id"],
        "snapshot_digest": manifest["execution_snapshot_digest"],
        "candidate_digest": manifest["candidate_digest"],
        "target_scope": manifest["target_labels"],
        "recovery_manifest_digest": manifest["manifest_digest"],
    }
    status, first = product_server._start_product_publication(request, platform="SHOPEE")
    assert status == 202 and first.get("reused") is not True, first
    assert first["run_id"] not in {
        manifest["direct_predecessor_run_id"], manifest["original_publication_run_id"],
        "product-center-shopee-4bc697d923e8cbb569e8533a3c63e932",
        "product-center-shopee-1e6e675a650ad03208a6f8d8996408a6",
        "product-center-shopee-30c4f4c7c06a92aadfbf15a3b5389531",
    }
    assert len(launches) == 1 and provider_calls == []
    stored = run_store.get_run_by_id(run_id=first["run_id"])
    assert stored["request_identity"] == {
        "kind": "SHOPEE_KNOWN_ZERO_CONTINUATION",
        "authority_digest": manifest["manifest_digest"],
        "predecessor_run_id": manifest["direct_predecessor_run_id"],
        "source_receipt_digest": manifest["source_receipt_digest"],
        "source_execution_manifest_digest": manifest[
            "source_execution_manifest_digest"
        ],
        "preflight_digest": manifest["preflight_digest"],
        "evidence_relocation_digest": manifest["evidence_relocation_digest"],
    }
    status, second = product_server._start_product_publication(request, platform="SHOPEE")
    assert status == 202 and second["reused"] is True
    assert second["run_id"] == first["run_id"]
    assert len(launches) == 1 and provider_calls == []

    tasks_copy = tmp_path / "tasks.db"
    shutil.copy2(REAL_REPORTLESS_OPS / "tasks.db", tasks_copy)
    domain_engine = WorkbenchEngine(
        tasks_copy, {"code_version": "known-zero-http-callback-test"}
    )
    observed = {}
    expected_resources = (
        'product:["shopee:MY","0988"]',
        'product:["shopee:TH","0988"]',
        'product:["shopee:VN","0988"]',
    )

    def stop_before_executor_mutation(_runner, **_kwargs):
        with sqlite3.connect(tasks_copy) as connection:
            rows = connection.execute(
                "SELECT operation_id, resource FROM workbench_domain_locks "
                "WHERE resource IN (?,?,?) ORDER BY resource",
                expected_resources,
            ).fetchall()
            operations = connection.execute(
                "SELECT operation_id, state, readback_ref FROM workbench_domain_operations "
                "WHERE operation_id IN (SELECT DISTINCT operation_id FROM workbench_domain_locks "
                "WHERE resource IN (?,?,?))",
                expected_resources,
            ).fetchall()
        observed["locks"] = rows
        observed["operations"] = operations
        raise RuntimeError("transport trap before executor mutation")

    monkeypatch.setattr(operations_domain_guard, "engine_for", lambda _root: domain_engine)
    monkeypatch.setattr(runner_module.ProductPublicationRunner, "run",
                        stop_before_executor_mutation)
    launches[0]()

    failed = run_store.get_run_by_id(run_id=first["run_id"])
    assert failed["state"] == "FAILED"
    assert failed["failure_code"] == "RUNNER_INFRASTRUCTURE_FAILED"
    assert len(observed["operations"]) == 1
    assert observed["operations"][0][1:] == ("inflight", "")
    assert {resource for _, resource in observed["locks"]} == set(expected_resources)
    assert {operation_id for operation_id, _ in observed["locks"]} == {
        observed["operations"][0][0]
    }
    assert provider_calls == []
