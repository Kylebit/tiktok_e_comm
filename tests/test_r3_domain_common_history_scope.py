"""Actual retained ReleaseStore COMMON scope; no HTTP/provider/worker."""
import pytest

from test_r3_domain_common_proof import domain_private, build_fixture
from shared_platform.r3_frozen_review_producer import DomainReviewBlocked


@pytest.mark.parametrize("kind", ["mixed-common-draft", "pure-common-draft"])
def test_any_retained_common_plan_blocks_fixture_history_completeness(domain_private, kind):
    authority, _reader, _receipt, market, _documents = domain_private
    labels = ["miaoshou:COMMON", "ozon:RU"] if kind == "mixed-common-draft" else ["miaoshou:COMMON"]
    payload = {"plan_id": "fixture-extra:" + kind, "product_id": market["product_id"],
               "seller_sku": "1099", "product_package_id": "history-extra-product",
               "content_package_id": "history-extra-content", "product_revision": 1,
               "targets": labels}
    # The real domain store must accept and retain the additional plan before
    # it can be meaningful evidence of a reader's missed history. No SQL clone
    # or monkeypatch substitutes for its validation/immutable write contract.
    extra = authority.store.create_plan(payload)
    retained = authority.store.get_plan(extra["plan_id"])
    assert retained["product_id"] == market["product_id"]
    assert "miaoshou:COMMON" in retained["targets"]
    assert retained["status"] == "PENDING_APPROVAL"
    with authority._transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM release_runs WHERE plan_id=?", (extra["plan_id"],)).fetchone()[0] == 0
    with pytest.raises(DomainReviewBlocked, match="DOMAIN_FIXTURE_HISTORY_INCOMPLETE_OR_CHANGED"):
        build_fixture(domain_private)
    with authority._transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM private_final_decisions").fetchone()[0] == 0
