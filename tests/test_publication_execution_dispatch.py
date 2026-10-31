"""Real durable dispatch state/control identity; only HTTP edge is a fixture."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading

import pytest

from shared_platform import publication_execution_dispatch as dispatch
from test_product_center_publication_wrapper import _published_report, PUBLICATION


def _start(platform):
    run_id = "speed-" + platform.lower()
    return 202, {"ok": True, "schema_version": PUBLICATION.START_SCHEMA,
        "platform": platform, "run_id": run_id, "report_id": f"publication-report:{run_id}"}


def _run(tmp_path, request, **options):
    return dispatch.run_platform_dispatch(base_url="http://127.0.0.1:49100",
        offer_id="987650123", plan_id="fresh-three-model-plan",
        platforms=("TIKTOK", "SHOPEE", "OZON"), journal_root=tmp_path / "dispatch",
        request=request, poll_timeout_seconds=0, **options)


def _platform(url):
    if "publication-report?" in url:
        return url.rsplit("speed-", 1)[1].upper()
    return next(p for p, endpoint in PUBLICATION.PLATFORM_ENDPOINTS.items() if url.endswith(endpoint))


def test_durable_starts_precede_all_polls_and_reentry_is_get_only(tmp_path):
    calls = []
    def wire(url, *, payload=None, **options):
        platform = _platform(url)
        calls.append(("POST" if payload is not None else "GET", platform))
        return _start(platform) if payload is not None else (200, _published_report(platform))
    assert all(row["status"] == "PUBLISHED" for row in _run(tmp_path, wire))
    assert {p for method, p in calls[:3] if method == "POST"} == {"TIKTOK", "SHOPEE", "OZON"}
    assert all(row["status"] == "PUBLISHED" for row in _run(tmp_path, wire))
    assert sum(method == "POST" for method, _ in calls) == 3
    journal = dispatch.DispatchJournal(tmp_path / "dispatch")
    with sqlite3.connect(journal.path) as connection:
        rows = connection.execute("SELECT kind,raw_body FROM publication_dispatch_responses WHERE kind='START'").fetchall()
    assert len(rows) == 3 and all(json.loads(body)["run_id"].startswith("speed-") for _, body in rows)


def test_unknown_one_platform_never_reposts_and_others_finish(tmp_path):
    posts = []
    def wire(url, *, payload=None, **options):
        platform = _platform(url)
        if payload is not None:
            posts.append(platform)
            if platform == "SHOPEE":
                raise TimeoutError("original start may have been accepted")
            return _start(platform)
        return 200, _published_report(platform)
    first = _run(tmp_path, wire)
    second = _run(tmp_path, wire)
    assert posts.count("SHOPEE") == 1
    assert [row["status"] for row in first] == ["PUBLISHED", "PROCESSING", "PUBLISHED"]
    assert second[1]["reason_code"] == "START_OUTCOME_UNKNOWN"
    journal = dispatch.DispatchJournal(tmp_path / "dispatch")
    key, _ = dispatch._bound("http://127.0.0.1:49100", "987650123", "fresh-three-model-plan", "SHOPEE", None)
    state = journal.get(key)
    assert state["phase"] == "UNKNOWN" and state["next_action"] == "RECONCILE_ORIGINAL_START"
    assert state["owner"].startswith("pid:") and state["revision"] >= 2 and state["next_check_at"]
    spans=journal.spans(key)
    assert len(spans)==1 and spans[0]["kind"]=='START'
    assert spans[0]["error_type"]=='TimeoutError' and spans[0]["elapsed_seconds"]>=0


def test_body_persists_before_start_identity_decode_then_local_reconcile(tmp_path, monkeypatch):
    calls = []
    def wire(url, *, payload=None, **options):
        platform = _platform(url)
        calls.append(("POST" if payload is not None else "GET", platform))
        return _start(platform) if payload is not None else (200, _published_report(platform))
    control = dispatch._control()
    original = control._start_identity
    monkeypatch.setattr(control, "_start_identity", lambda *a, **k: (_ for _ in ()).throw(ValueError("decoder interrupted")))
    assert all(row["reason_code"] == "START_IDENTITY_INVALID" for row in _run(tmp_path, wire))
    journal = dispatch.DispatchJournal(tmp_path / "dispatch")
    key, _ = dispatch._bound("http://127.0.0.1:49100", "987650123", "fresh-three-model-plan", "SHOPEE", None)
    assert json.loads(journal.original_start(key)[1])["run_id"] == "speed-shopee"
    monkeypatch.setattr(control, "_start_identity", original)
    assert all(row["status"] == "PUBLISHED" for row in _run(tmp_path, wire, allow_start=False))
    assert sum(method == "POST" for method, _ in calls) == 3


def test_same_scope_two_process_equivalents_only_one_start(tmp_path):
    dispatch.DispatchJournal(tmp_path / "dispatch").initialize()
    entered = threading.Event()
    release = threading.Event()
    posts = []
    def wire(url, *, payload=None, **options):
        if payload is not None:
            posts.append(url)
            entered.set()
            assert release.wait(3)
            return _start("SHOPEE")
        return 200, _published_report("SHOPEE")
    def run():
        return dispatch.run_platform_dispatch(base_url="http://127.0.0.1:49100", offer_id="987650123",
            plan_id="fresh-three-model-plan", platforms=("SHOPEE",), journal_root=tmp_path / "dispatch",
            request=wire, poll_timeout_seconds=0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run)
        assert entered.wait(2)
        second = pool.submit(run)
        try:
            assert second.result(timeout=2)[0]["status"] == "PROCESSING"
        finally:
            release.set()
        assert first.result(timeout=3)[0]["status"] == "PUBLISHED"
    assert len(posts) == 1


def test_server_500_is_unknown_and_status_does_not_create_state(tmp_path):
    untouched = tmp_path / "untouched"
    assert dispatch.DispatchJournal(untouched).get("absent") is None
    assert not untouched.exists()
    calls = []
    def wire(url, **options):
        calls.append(url)
        return 500, b'{"error":"could be after acceptance"}'
    rows = _run(tmp_path, wire)
    assert all(row["reason_code"] == "START_OUTCOME_UNKNOWN" for row in rows)
    _run(tmp_path, wire, allow_start=False)
    assert len(calls) == 3


def test_invalid_bounds_are_rejected_before_any_artifact_or_http(tmp_path):
    for options in ({"max_parallel": 4}, {"request_timeout_seconds": float("nan")}):
        with pytest.raises(ValueError):
            _run(tmp_path, lambda *a, **k: pytest.fail("no HTTP"), **options)
    assert not (tmp_path / "dispatch").exists()


def test_existing_schema_mismatch_holds_before_wire_without_migrating(tmp_path):
    journal=dispatch.DispatchJournal(tmp_path/'dispatch')
    dispatch.ensure_private_runtime_directory(journal.root,
        dispatch._key({'schema':dispatch.SCHEMA,'root':str(journal.root)}))
    with sqlite3.connect(journal.path) as connection:
        connection.execute('CREATE TABLE publication_dispatch(scope_key TEXT)')
    before=journal.path.read_bytes()
    with pytest.raises(ValueError,match='JOURNAL_SCHEMA_HOLD'):
        _run(tmp_path,lambda *a,**k:pytest.fail('schema mismatch must prevent HTTP'))
    assert journal.path.read_bytes()==before
