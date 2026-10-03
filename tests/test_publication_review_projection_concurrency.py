import threading
import time
from concurrent.futures import ThreadPoolExecutor


def test_same_offer_dashboard_builds_are_serial_and_read_only(tmp_path, monkeypatch):
    from shared_platform import publication_review_projection as projection
    from shared_platform import release_control

    ledger = tmp_path / "release-ledger.db"
    ledger.write_bytes(b"immutable-ledger-sentinel")
    before = ledger.read_bytes()
    active = 0
    maximum = 0
    counter_lock = threading.Lock()

    registration = {
        "offer_id": "123",
        "approved_revision": 7,
        "snapshot_digest": "sha256:snapshot",
        "binding_sha256": "binding",
        "targets": ["tiktok:LH_MY"],
    }
    first = {
        "targets": [{"target": "tiktok:LH_MY", "selection_key": "lh_my", "price": {}}],
        "copy_review_sets": [],
        "image_execution_plan": {},
    }

    def build_release_dashboard(**kwargs):
        nonlocal active, maximum
        with counter_lock:
            active += 1
            maximum = max(maximum, active)
            if active > 1:
                active -= 1
                raise MemoryError
        try:
            # Keep the overlap window wide enough that an unlocked 8-worker
            # implementation deterministically enters the MemoryError branch.
            time.sleep(0.02)
            assert ledger.read_bytes() == before
            return {
                "ok": True,
                "product": {"offer_id": kwargs["offer_id"], "seller_sku_governance": {}},
                "publication_scope": {},
                "listing_copy": {},
                "actual_release_gate": {"ready": True},
                "approval_rehearsal": {"ready": True},
            }
        finally:
            with counter_lock:
                active -= 1

    monkeypatch.setattr(
        projection,
        "_load",
        lambda offer_id, runtime_root: (tmp_path, registration, {}, {}, {"revision": 3}),
    )
    monkeypatch.setattr(projection, "read_document", lambda path, default: first)
    monkeypatch.setattr(
        projection,
        "_view",
        lambda registration, takeover, candidate, record: {
            "offer_id": registration["offer_id"],
            "revision": record["revision"],
        },
    )
    monkeypatch.setattr(projection, "_master_report", lambda registration: {"assets": []})
    monkeypatch.setattr(release_control, "build_release_dashboard", build_release_dashboard)

    started = threading.Barrier(8)

    def read_dashboard(_):
        started.wait(timeout=2)
        return projection.dashboard("123", runtime_root=tmp_path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(read_dashboard, range(8)))

    identities = {
        (row["product"]["offer_id"], row["r2_candidate_review"]["revision"])
        for row in results
    }
    assert maximum == 1
    assert identities == {("123", 3)}
    assert all(row["frozen_first_review"]["approved_revision"] == 7 for row in results)
    assert ledger.read_bytes() == before
    assert projection._dashboard_gates == {}


def test_different_offer_dashboard_builds_are_not_globally_serialized():
    from shared_platform.publication_review_projection import _dashboard_build_gate

    overlap = threading.Barrier(2)

    def enter(offer_id):
        with _dashboard_build_gate(offer_id):
            overlap.wait(timeout=1)
            return offer_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert set(pool.map(enter, ("123", "456"))) == {"123", "456"}
