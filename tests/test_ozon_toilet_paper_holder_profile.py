"""Replay exact original holder metadata, without credentials or provider access."""
from copy import deepcopy
import json
from pathlib import Path
import socket

import pytest

from shared_platform.product_publication_live_dependencies import (
    LivePublicationDependencyError,
    OfficialOzonFridgeMagnetProfileResolver,
    build_live_ozon_dependencies,
    build_ozon_import_item_from_frozen_variant,
)
from modules.ozon.approved_publication_v4 import project_ozon_v4_variants


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("offline holder regression attempted a network connection")
    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "create_connection", reject)


def facts():
    return json.loads(Path(__file__).with_name("fixtures").joinpath(
        "ozon_toilet_paper_holder_official.json").read_text(encoding="utf-8"))


def snapshot(*, frozen_category=True):
    result = {
        "schema_version": "approved-publication-snapshot/v4",
        "product": {"main_category": {"name": facts()["frozen_category_semantic"]}},
    }
    if frozen_category:
        result["categories_by_target"] = {"ozon:RU": {"category": {
            "id": "17027905", "name": "Sauna & Bath Accessories"}}}
    return result


def replay(data=None):
    retained = deepcopy(data or facts())
    calls = []
    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path == "/v1/description-category/tree":
            return deepcopy(retained["tree"])
        if path == "/v1/description-category/attribute":
            assert (body["description_category_id"], body["type_id"]) == (17027905, 92011)
            return deepcopy(retained["attributes"])
        if path == "/v1/description-category/attribute/values/search":
            assert (body["description_category_id"], body["type_id"]) == (17027905, 92011)
            return deepcopy(retained["dictionaries"][str(body["attribute_id"])])
        raise AssertionError(path)
    return OfficialOzonFridgeMagnetProfileResolver(post=post), calls


def profile():
    return {
        "schema_version": "ozon-official-profile-resolution/v1", "resolution": "EXACT",
        "description_category_id": 17027905, "type_id": 92011, "type_name": "Toilet Paper Holder",
        "required_attributes": {
            "brand": {"attribute_id": 85, "dictionary_value_id": 126745801, "value": "Нет бренда"},
            "model_name": {"attribute_id": 9048},
            "product_type": {"attribute_id": 8229, "dictionary_value_id": 92011, "value": "Toilet Paper Holder"},
        },
    }


def variant():
    # A synthetic final projection: does not allocate SKU, freeze a review or
    # turn the historical planning parcel/derived price into observed facts.
    return {
        "offer_id": "0989", "approved_seller_sku": "0989",
        "title": "Подвесной держатель туалетной бумаги из дерева и верёвки",
        "description": "Деревянный стержень и верёвка с обычным крючком.",
        "images": ["https://example.test/source-06.jpg"],
        "parcel": {"weight_kg": "0.2", "package_cm": ["20", "12", "6"]},
        "category": {"id": "17027905", "name": "Sauna & Bath Accessories"},
        "currency": "CNY", "price": "52", "old_price": "68", "official_profile": profile(),
    }


@pytest.mark.parametrize("frozen_category", [True, False])
def test_holder_frozen_semantic_resolves_exact_official_type(frozen_category):
    original = snapshot(frozen_category=frozen_category)
    before = deepcopy(original)
    resolver, calls = replay()
    resolved = resolver(original)
    assert (resolved["description_category_id"], resolved["type_id"]) == (17027905, 92011)
    assert resolved["required_attributes"]["product_type"]["dictionary_value_id"] == 92011
    assert resolved["required_attributes"]["brand"]["dictionary_value_id"] == 126745801
    assert len(calls) == 4
    assert original == before


def test_holder_builder_preserves_parcel_copy_and_only_proven_required_attributes():
    source = variant()
    before = deepcopy(source)
    item = build_ozon_import_item_from_frozen_variant(source)
    assert (item["description_category_id"], item["type_id"]) == (17027905, 92011)
    attrs = {row["id"]: row["values"] for row in item["attributes"]}
    assert attrs[9048] == [{"dictionary_value_id": 0, "value": "0989-toilet-paper-holder"}]
    assert attrs[8229] == [{"dictionary_value_id": 92011, "value": "Toilet Paper Holder"}]
    assert attrs[85] == [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]
    assert not {4383, 8415, 8416}.intersection(attrs)
    assert (item["weight"], item["depth"], item["width"], item["height"]) == (200, 200, 120, 60)
    assert item["name"] == source["title"]
    assert source == before


def test_original_v4_consumer_projects_holder_without_replacing_frozen_facts():
    # This is a synthetic future final body. It is neither the current draft
    # nor an approval, and it contains no account binding or execution receipt.
    body = snapshot()
    candidate = variant()
    body.update(snapshot_digest="sha256:" + "a" * 64, publication_targets=[{
        "target_label": "ozon:RU", "platform": "ozon", "site": "RU", "store": "RU"}])
    body["product"].update(title=candidate["title"], description=candidate["description"],
        images=candidate["images"], content_by_target={"ozon:RU": {
            "locale": "ru-RU", "title": candidate["title"], "description": candidate["description"]}})
    category = body["categories_by_target"]["ozon:RU"]
    category.update(target_label="ozon:RU", platform="ozon", site="RU", store="RU")
    category["category"]["path"] = [{"id": "17027905", "name": "Sauna & Bath Accessories"}]
    category["decision"] = {"status": "APPROVED"}
    body["skus"] = [{"variant_key": "rod-rope-hook", "seller_sku": "0989", "model_sku": "0989",
        "specification": {"material": "Wooden rod and rope"}, "parcel": candidate["parcel"],
        "variant_images": candidate["images"], "prices": {"ozon:RU": {
            "amount": candidate["price"], "old_price_cny": candidate["old_price"], "currency": "CNY"}}}]
    before = deepcopy(body)
    resolver, _ = replay()
    projected, = project_ozon_v4_variants(body, target_labels=("ozon:RU",),
        official_profile_resolver=resolver)
    item = build_ozon_import_item_from_frozen_variant(projected)
    assert (item["description_category_id"], item["type_id"]) == (17027905, 92011)
    assert item["name"] == candidate["title"]
    assert item["price"] == candidate["price"]
    assert item["offer_id"] == "0989"
    assert body == before


def test_holder_never_accepts_sauna_mat_type_from_same_category():
    retained = facts()
    node = retained["tree"]["result"][0]["children"][0]["children"][0]
    node.update(type_id=93656, type_name="Sauna Mat")
    resolver, calls = replay(retained)
    with pytest.raises(LivePublicationDependencyError):
        resolver(snapshot())
    assert [path for path, _ in calls] == ["/v1/description-category/tree"]


def test_holder_explicit_conflicting_frozen_type_fails_before_metadata():
    data = snapshot()
    data["categories_by_target"]["ozon:RU"]["category"]["type_id"] = 93656
    resolver, calls = replay()
    with pytest.raises(LivePublicationDependencyError):
        resolver(data)
    assert calls == []


@pytest.mark.parametrize("mutation", ["extra_required", "missing_model", "type_dictionary", "brand_dictionary"])
def test_holder_unknown_or_drifted_official_metadata_stays_blocked(mutation):
    retained = facts()
    if mutation == "extra_required":
        retained["attributes"]["result"].append({"id": 999999, "is_required": True})
    elif mutation == "missing_model":
        retained["attributes"]["result"] = [row for row in retained["attributes"]["result"] if row["id"] != 9048]
    else:
        key = "8229" if mutation == "type_dictionary" else "85"
        retained["dictionaries"][key]["result"] = [{"id": 93656, "value": "Sauna Mat"}]
    resolver, _ = replay(retained)
    with pytest.raises(LivePublicationDependencyError):
        resolver(snapshot())


@pytest.mark.parametrize("field,value", [("type_id", 93656), ("dictionary_value_id", 93656), ("brand_id", 123)])
def test_holder_import_rejects_conflicting_type_or_manufacturer_brand(field, value):
    data = variant()
    if field == "type_id":
        data["official_profile"]["type_id"] = value
        data["category"]["type_id"] = 92011
    elif field == "brand_id":
        data["official_profile"]["required_attributes"]["brand"]["dictionary_value_id"] = value
    else:
        data["official_profile"]["required_attributes"]["product_type"][field] = value
    with pytest.raises(LivePublicationDependencyError):
        build_ozon_import_item_from_frozen_variant(data)


def test_holder_support_does_not_replace_native_account_pin_or_shop_join(monkeypatch):
    for name in ("ORBIT_OZON_CREDENTIALS_PATH", "ORBIT_OZON_CREDENTIALS_SHA256", "ORBIT_OZON_EXPECTED_ACCOUNT_ID"):
        monkeypatch.delenv(name, raising=False)
    assert facts()["native_account_to_miaoshou_shop"] == "UNKNOWN"
    with pytest.raises(ValueError, match="requires pinned credentials"):
        build_live_ozon_dependencies()
