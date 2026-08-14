from copy import deepcopy
import pytest

from test_approved_publication_snapshot_integration import _approved_full_store


HB = {"tiktok:HB_PH": ("15173238", "648", "PHP"), "tiktok:HB_MY": ("16770639", "45", "MYR"), "tiktok:HB_TH": ("16770557", "356", "THB"), "tiktok:HB_VN": ("16783702", "370000", "VND")}


def _additions(model_sku):
    answer = {}
    for label, (shop_id, price, currency) in HB.items():
        site = label.split(":", 1)[1]
        answer[label] = {"category": {"target_label": label, "platform": "tiktok", "site": site, "store": site, "category": {"id": "600338", "name": "Decorative Stickers", "path": [{"id": "600338", "name": "Decorative Stickers"}]}, "decision": {"status": "APPROVED", "decision_digest": "a" * 64}}, "pricing": {"status": "ready", "shop_id": shop_id, "sku_prices": [{"model_sku": model_sku, "list_price": price, "currency": currency}]}}
    return answer


def test_target_only_successor_preserves_predecessor_and_appends_hb(tmp_path, monkeypatch):
    store, _payload, response = _approved_full_store(tmp_path, monkeypatch)
    predecessor_id = response["approval"]["plan_id"]
    old = deepcopy(store.get_plan(predecessor_id)["payload"])
    model = old["sku_lineage"]["assignment"]["model_skus"][0]["model_sku"]
    successor = store.create_target_only_successor(predecessor_id, additions=_additions(model))
    body = successor["payload"]
    for label in old["targets"]:
        assert body["product_facts"]["categories_by_target"][label] == old["product_facts"]["categories_by_target"][label]
        assert body["pricing"]["selected_targets"][label] == old["pricing"]["selected_targets"][label]
    assert body["product_facts"]["title"] == old["product_facts"]["title"]
    assert body["product_facts"]["description"] == old["product_facts"]["description"]
    assert body["product_facts"]["image_urls"] == old["product_facts"]["image_urls"]
    assert {body["product_facts"]["categories_by_target"][key]["category"]["id"] for key in HB} == {"600338"}
    approval = store.approve_plan(successor["plan_id"], approved_by="Kyle", user_approved=True, confirmation_token=successor["confirmation_token"])
    assert approval["publication_snapshot"] is not None
    assert store.get_plan(predecessor_id)["status"] == "SUPERSEDED"


def test_target_only_successor_rejects_existing_target_before_write(tmp_path, monkeypatch):
    store, _payload, response = _approved_full_store(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="already exists"):
        store.create_target_only_successor(response["approval"]["plan_id"], additions={"tiktok:MX": _additions("0952")["tiktok:HB_PH"]})
