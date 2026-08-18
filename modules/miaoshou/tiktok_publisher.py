"""Miaoshou transport for the independent TikTok publisher."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
import re
import time
from typing import Callable, Mapping

from domains.channel_operations.tiktok_publisher import (
    TikTokBatchPreflightError,
    TikTokPreWritePreparationError,
)
from modules.miaoshou.client import post_open
from modules.miaoshou.tiktok_variant_binding import (
    TikTokVariantBindingError,
    approved_variant_key_bindings,
)


READ_SITE_DRAFT_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/"
    "get_site_collect_item_info"
)
READ_SHOP_DRAFT_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/"
    "get_shop_collect_item_info"
)
SAVE_SITE_DRAFT_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/"
    "save_site_collect_item_info"
)
SAVE_SHOP_DRAFT_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/"
    "save_shop_collect_item_info"
)
PUBLISH_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/"
    "save_move_collect_task"
)
CATEGORY_METADATA_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/get_category_metadata"
)
WAREHOUSE_GET_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/"
    "get_shop_warehouse_list"
)

EXPECTED_SHOP_ID_BY_TARGET = {
    "tiktok:LH_PH": "7676267",
    "tiktok:LH_MY": "13295169",
    "tiktok:LH_TH": "13295228",
    "tiktok:LH_VN": "13295291",
    "tiktok:MX": "16265910",
    "tiktok:GB": "10204699",
    "tiktok:HB_PH": "15173238",
    "tiktok:HB_MY": "16770639",
    "tiktok:HB_TH": "16770557",
    "tiktok:HB_VN": "16783702",
}

_SITE_BY_TARGET = {
    "tiktok:LH_PH": "PH",
    "tiktok:LH_MY": "MY",
    "tiktok:LH_TH": "TH",
    "tiktok:LH_VN": "VN",
    "tiktok:MX": "MX",
    "tiktok:GB": "GB",
    "tiktok:HB_PH": "PH",
    "tiktok:HB_MY": "MY",
    "tiktok:HB_TH": "TH",
    "tiktok:HB_VN": "VN",
}
_SITE_DRAFT_TARGETS = {
    "tiktok:LH_PH",
    "tiktok:LH_MY",
    "tiktok:LH_TH",
    "tiktok:LH_VN",
    "tiktok:HB_PH",
    "tiktok:HB_MY",
    "tiktok:HB_TH",
    "tiktok:HB_VN",
}


class MiaoshouTikTokTransport:
    """Translate approved target rows into the exact Miaoshou Open API calls."""

    def __init__(
        self,
        *,
        post: Callable[[str, dict[str, object]], Mapping[str, object]] = post_open,
        publish_interval_seconds: float = 0.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._post = post
        self._publish_interval_seconds = publish_interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._last_publish_at: float | None = None

    def read_draft(self, target: Mapping[str, object]) -> Mapping[str, object]:
        label = self._label(target)
        detail_id = int(str(target["detail_id"]))
        if label in _SITE_DRAFT_TARGETS:
            response = self._post(
                READ_SITE_DRAFT_PATH,
                {"detailId": detail_id, "site": _SITE_BY_TARGET[label]},
            )
            data = self._mapping(response.get("data"), "read data")
            info = self._mapping(data.get("siteCollectItemInfo"), "site draft")
        else:
            response = self._post(
                READ_SHOP_DRAFT_PATH,
                {
                    "detailId": detail_id,
                    "shopId": int(str(target["shop_id"])),
                },
            )
            data = self._mapping(response.get("data"), "read data")
            info = self._mapping(data.get("shopCollectItemInfo"), "shop draft")
        if str(info.get("detailId") or detail_id) != str(detail_id):
            raise ValueError("Miaoshou draft detail identity drifted")
        return {"info": dict(info), "oss_md5": str(data.get("ossMd5") or "")}

    def draft_matches(
        self, target: Mapping[str, object], draft: Mapping[str, object]
    ) -> bool:
        return self._draft_matches(
            target,
            draft,
            accept_post_submit_projection=False,
        )

    def post_submit_draft_matches(
        self, target: Mapping[str, object], draft: Mapping[str, object]
    ) -> bool:
        """Compare the provider's normalized post-submit draft projection.

        This is deliberately separate from ``draft_matches``: a pre-submit
        draft with missing delivery/size-chart fields must still be repaired,
        while the confirmed normalization performed after an accepted submit
        must not turn a successful publish into a false mismatch.
        """

        return self._draft_matches(
            target,
            draft,
            accept_post_submit_projection=True,
        )

    def _draft_matches(
        self,
        target: Mapping[str, object],
        draft: Mapping[str, object],
        *,
        accept_post_submit_projection: bool,
    ) -> bool:
        info = self._mapping(draft.get("info"), "draft info")
        expected_category = self._category_id(target, info)
        if str(info.get("cid") or "") != expected_category:
            return False
        if str(info.get("title") or "") != self._target_text(
            target, "expected_title"
        ):
            return False
        expected_description = self._target_text(target, "expected_description")
        if str(info.get("notes") or "") != expected_description:
            return False
        notes_text = info.get("notesText")
        notes_text_matches = str(notes_text or "") == expected_description
        notes_text_is_confirmed_projection = (
            accept_post_submit_projection and notes_text in (None, "")
        )
        if not notes_text_matches and not notes_text_is_confirmed_projection:
            return False
        expected_images = self._target_images(target)
        observed_images = info.get("imgUrls")
        if not isinstance(observed_images, list) or observed_images != expected_images:
            return False
        delivery_type = info.get("deliveryOptionSetType")
        if delivery_type != "default" and not (
            accept_post_submit_projection and delivery_type in (None, "")
        ):
            return False
        if str(info.get("sizeChart") or ""):
            return False
        size_chart_type = info.get("sizeChartType")
        if size_chart_type not in (None, "") and not (
            accept_post_submit_projection and size_chart_type == "image"
        ):
            return False
        expected_weight, expected_package = self._parent_parcel(target)
        provider_package = tuple(
            self._package_dimension(value) for value in expected_package
        )
        try:
            parent_matches = (
                self._decimal(info.get("weight")) == expected_weight
                and all(
                    self._decimal(info.get(field)) == expected
                    for field, expected in zip(
                        ("packageLength", "packageWidth", "packageHeight"),
                        provider_package,
                    )
                )
            )
        except ValueError:
            parent_matches = False
        if not parent_matches:
            return False
        sku_map = self._sku_map(info)
        expected_by_key = self._expected_rows_by_draft_key(target, info, sku_map)
        try:
            expected_specifications = self._bound_variant_specifications(
                target, info, sku_map
            )
            specifications_match = self._variant_specifications_match(
                info,
                expected_specifications,
                accept_provider_omission=accept_post_submit_projection,
            )
        except TikTokPreWritePreparationError:
            return False
        return specifications_match and all(
            (
                expected_by_key[str(key)][0] is None
                or str(row.get("itemNum") or "").strip()
                == expected_by_key[str(key)][0]
            )
            and self._decimal(row.get("price")) == expected_by_key[str(key)][1]
            and self._decimal(row.get("priceIncludeVat"))
            == expected_by_key[str(key)][1]
            and self._sku_parcel_matches(
                row,
                expected_weight=expected_by_key[str(key)][2],
                expected_package=(
                    tuple(
                        self._package_dimension(value)
                        for value in expected_by_key[str(key)][3]
                    )
                    if expected_by_key[str(key)][3] is not None
                    else None
                ),
            )
            for key, row in sku_map.items()
        )

    def prepare_approved_draft(
        self, target: Mapping[str, object], draft: Mapping[str, object]
    ) -> Mapping[str, object]:
        try:
            label = self._label(target)
            detail_id = int(str(target["detail_id"]))
            info = deepcopy(dict(self._mapping(draft.get("info"), "draft info")))
            category_id = self._category_id(target, info)
            info["cid"] = category_id
            info["title"] = self._target_text(target, "expected_title")
            expected_description = self._target_text(target, "expected_description")
            info["notes"] = expected_description
            info["notesText"] = expected_description
            info["imgUrls"] = self._target_images(target)
            parent_weight, parent_package = self._parent_parcel(target)
            info["weight"] = float(parent_weight)
            info["packageLength"] = self._package_dimension(parent_package[0])
            info["packageWidth"] = self._package_dimension(parent_package[1])
            info["packageHeight"] = self._package_dimension(parent_package[2])
            sku_map = self._sku_map(info)
            expected_by_key = self._expected_rows_by_draft_key(target, info, sku_map)
            expected_specifications = self._bound_variant_specifications(
                target, info, sku_map
            )
            warehouse_id = self._warehouse_id(
                info, shop_id=str(target["shop_id"])
            )
            updated_skus: dict[str, object] = {}
            for key, raw_row in sku_map.items():
                row = deepcopy(dict(raw_row))
                model_sku, expected_price, sku_weight, sku_package = (
                    expected_by_key[str(key)]
                )
                if model_sku is not None:
                    row["itemNum"] = model_sku
                price = float(expected_price)
                row["price"] = price
                row["priceIncludeVat"] = price
                if sku_weight is not None and sku_package is not None:
                    row["weight"] = float(sku_weight)
                    row["packageLength"] = self._package_dimension(sku_package[0])
                    row["packageWidth"] = self._package_dimension(sku_package[1])
                    row["packageHeight"] = self._package_dimension(sku_package[2])
                stock = raw_row.get("stock")
                if isinstance(stock, bool) or not str(stock or "").isdigit() or int(str(stock)) <= 0:
                    raise TikTokPreWritePreparationError(
                        "Miaoshou approved stock is unavailable",
                        code="warehouse_binding_invalid",
                    )
                row["stock"] = int(str(stock))
                row["shopIdToWarehouseIdAndStockMap"] = {
                    str(target["shop_id"]): {warehouse_id: str(int(str(stock)))}
                }
                updated_skus[str(key)] = row
            info["skuMap"] = updated_skus
            self._apply_variant_specifications(info, expected_specifications)
            info["deliveryOptionSetType"] = "default"
            info["sizeChart"] = ""
            info["sizeChartType"] = ""
            if label == "tiktok:GB":
                info["isCodOpen"] = "0"
                metadata_target = dict(target)
                metadata_target["expected_category_id"] = category_id
                info["productAttributes"] = self._gb_required_attributes(
                    metadata_target
                )
        except TikTokPreWritePreparationError:
            raise
        except Exception as error:
            raise TikTokPreWritePreparationError(
                "Miaoshou draft repair preparation is invalid",
                code="draft_repair_preparation_invalid",
            ) from error
        oss_md5 = str(draft.get("oss_md5") or "")
        if label in _SITE_DRAFT_TARGETS:
            prepared = {
                "path": SAVE_SITE_DRAFT_PATH,
                "body": {
                    "detailId": detail_id,
                    "site": _SITE_BY_TARGET[label],
                    "siteCollectItemInfo": info,
                    "ossMd5": oss_md5,
                },
            }
        else:
            prepared = {
                "path": SAVE_SHOP_DRAFT_PATH,
                "body": {
                    "detailId": detail_id,
                    "shopId": int(str(target["shop_id"])),
                    "shopCollectItemInfo": info,
                    "ossMd5": oss_md5,
                },
            }
        try:
            json.dumps(
                prepared,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise TikTokBatchPreflightError(
                "TikTok prepared SAVE payload is not JSON serializable"
            ) from error
        return prepared

    def save_prepared_draft(
        self,
        target: Mapping[str, object],
        prepared: Mapping[str, object],
    ) -> Mapping[str, object]:
        label = self._label(target)
        path = prepared.get("path")
        body = prepared.get("body")
        expected_path = (
            SAVE_SITE_DRAFT_PATH if label in _SITE_DRAFT_TARGETS else SAVE_SHOP_DRAFT_PATH
        )
        if path != expected_path or not isinstance(body, Mapping):
            raise TikTokPreWritePreparationError(
                "Miaoshou prepared SAVE is invalid",
                code="draft_repair_preparation_invalid",
            )
        if str(body.get("detailId") or "") != str(target["detail_id"]):
            raise TikTokPreWritePreparationError(
                "Miaoshou prepared SAVE identity drifted",
                code="draft_repair_preparation_invalid",
            )
        return self._post(str(path), deepcopy(dict(body)))

    def save_approved_draft(
        self, target: Mapping[str, object], draft: Mapping[str, object]
    ) -> Mapping[str, object]:
        """Compatibility helper; formal publisher uses prepare then send."""
        return self.save_prepared_draft(
            target,
            self.prepare_approved_draft(target, draft),
        )

    def _gb_required_attributes(
        self, target: Mapping[str, object]
    ) -> list[dict[str, object]]:
        response = self._post(
            CATEGORY_METADATA_PATH,
            {
                "site": "GB",
                "cid": int(str(target["expected_category_id"])),
                "shopIds": [int(str(target["shop_id"]))],
            },
        )
        data = self._mapping(response.get("data"), "category metadata data")
        metadata = self._mapping(
            data.get("categoryMetadata"), "category metadata"
        )
        raw_attributes = metadata.get("categoryProductAttrList")
        if not isinstance(raw_attributes, list):
            raise ValueError("Miaoshou category attributes are malformed")
        required: list[dict[str, object]] = []
        for raw in raw_attributes:
            if not isinstance(raw, Mapping) or raw.get("isMandatory") is not True:
                continue
            values = raw.get("values")
            if (
                not isinstance(values, list)
                or len(values) != 1
                or not isinstance(values[0], Mapping)
            ):
                raise ValueError(
                    "Miaoshou mandatory category attribute lacks one exact value"
                )
            value = values[0]
            attr_id = str(raw.get("attrId") or "").strip()
            attr_name = str(raw.get("name") or "").strip()
            value_id = str(value.get("id") or "").strip()
            value_name = str(value.get("name") or "").strip()
            if not all((attr_id, attr_name, value_id, value_name)):
                raise ValueError("Miaoshou mandatory category attribute is malformed")
            required.append(
                {
                    "attributeId": attr_id,
                    "attributeName": attr_name,
                    "attributeNameAlias": str(
                        raw.get("attributeNameAlias") or attr_name
                    ),
                    "attributeValues": [
                        {
                            "valueName": value_name,
                            "valueId": value_id,
                            "valueNameAlias": str(
                                value.get("valueNameAlias") or value_name
                            ),
                        }
                    ],
                }
            )
        return required

    def _warehouse_id(self, info: Mapping[str, object], *, shop_id: str) -> str:
        observed: set[str] = set()
        for row in self._sku_map(info).values():
            bindings = row.get("shopIdToWarehouseIdAndStockMap")
            if bindings is None:
                continue
            if not isinstance(bindings, Mapping):
                raise TikTokPreWritePreparationError(
                    "Miaoshou warehouse binding is malformed",
                    code="warehouse_binding_invalid",
                )
            warehouse_map = bindings.get(shop_id)
            if warehouse_map is None:
                continue
            if not isinstance(warehouse_map, Mapping) or not warehouse_map:
                raise TikTokPreWritePreparationError(
                    "Miaoshou warehouse binding is malformed",
                    code="warehouse_binding_invalid",
                )
            observed.update(str(key).strip() for key in warehouse_map if str(key).strip())
        if len(observed) == 1:
            return next(iter(observed))
        if len(observed) > 1:
            raise TikTokPreWritePreparationError(
                "Miaoshou warehouse identity is ambiguous",
                code="warehouse_binding_invalid",
            )
        response = self._post(WAREHOUSE_GET_PATH, {"shopIds": [shop_id]})
        data = self._mapping(response.get("data"), "warehouse data")
        groups = data.get("shopWarehouseList")
        if not isinstance(groups, list):
            raise TikTokPreWritePreparationError(
                "Miaoshou warehouse response is malformed",
                code="warehouse_binding_invalid",
            )
        active: list[Mapping[str, object]] = []
        for group in groups:
            if not isinstance(group, Mapping):
                raise TikTokPreWritePreparationError(
                    "Miaoshou warehouse response is malformed",
                    code="warehouse_binding_invalid",
                )
            if str(group.get("shopId") or "") != shop_id:
                continue
            rows = group.get("warehouseList")
            if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
                raise TikTokPreWritePreparationError("Miaoshou warehouse response is malformed", code="warehouse_binding_invalid")
            active.extend(row for row in rows if str(row.get("warehouseEffectStatus") or "1") == "1" and str(row.get("warehouseId") or "").strip())
        mainland_pickup = [
            row for row in active
            if self._is_china_mainland_pickup_warehouse(row)
        ]
        if len(mainland_pickup) != 1:
            raise TikTokPreWritePreparationError(
                "Miaoshou China mainland pickup warehouse is unavailable",
                code="warehouse_binding_invalid",
            )
        return str(mainland_pickup[0]["warehouseId"]).strip()

    @staticmethod
    def _is_china_mainland_pickup_warehouse(row: Mapping[str, object]) -> bool:
        name = str(row.get("warehouseName") or row.get("name") or "")
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", name.lower())
        return (
            "chinesemainland" in normalized and "pickupwarehouse" in normalized
        ) or ("中国大陆" in normalized and "揽收仓" in normalized)

    def submit(self, target: Mapping[str, object]) -> Mapping[str, object]:
        label = self._label(target)
        now = self._monotonic()
        if self._last_publish_at is not None:
            remaining = self._publish_interval_seconds - (now - self._last_publish_at)
            if remaining > 0:
                self._sleep(remaining)
                now = self._monotonic()
        self._last_publish_at = now
        shop_id: object = str(target["shop_id"])
        if label not in _SITE_DRAFT_TARGETS:
            shop_id = int(str(shop_id))
        return self._post(
            PUBLISH_PATH,
            {
                "detailIds": [int(str(target["detail_id"]))],
                "shopIds": [shop_id],
            },
        )

    @staticmethod
    def _category_id(
        target: Mapping[str, object], info: Mapping[str, object]
    ) -> str:
        value = target.get("expected_category_id")
        if value is None:
            value = info.get("cid")
        clean = str(value or "").strip()
        if not clean.isascii() or not clean.isdigit() or int(clean) <= 0:
            raise ValueError("Miaoshou draft has no official category candidate")
        return clean

    @staticmethod
    def _label(target: Mapping[str, object]) -> str:
        label = str(target.get("target_label") or "")
        expected_shop = EXPECTED_SHOP_ID_BY_TARGET.get(label)
        if expected_shop is None or str(target.get("shop_id") or "") != expected_shop:
            raise ValueError("TikTok target shop binding drifted")
        return label

    @staticmethod
    def _target_text(target: Mapping[str, object], field: str) -> str:
        value = target.get(field)
        if not isinstance(value, str) or not value.strip():
            raise TikTokPreWritePreparationError(
                f"approved TikTok {field} is invalid",
                code="approved_copy_invalid",
            )
        return value

    @staticmethod
    def _target_images(target: Mapping[str, object]) -> list[str]:
        value = target.get("expected_images")
        if not isinstance(value, list) or not value or any(
            not isinstance(image, str) or not image.strip() for image in value
        ):
            raise TikTokPreWritePreparationError(
                "approved TikTok images are invalid",
                code="approved_images_invalid",
            )
        return list(value)

    @staticmethod
    def _mapping(value: object, name: str) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise ValueError(f"Miaoshou {name} is malformed")
        return value

    @classmethod
    def _sku_map(cls, info: Mapping[str, object]) -> Mapping[str, Mapping[str, object]]:
        sku_map = info.get("skuMap")
        if not isinstance(sku_map, Mapping) or not sku_map:
            raise TikTokPreWritePreparationError(
                "Miaoshou SKU map is malformed",
                code="sku_price_binding_invalid",
            )
        if any(not isinstance(row, Mapping) for row in sku_map.values()):
            raise TikTokPreWritePreparationError(
                "Miaoshou SKU map is malformed",
                code="sku_price_binding_invalid",
            )
        return sku_map  # type: ignore[return-value]

    @classmethod
    def _expected_rows_by_draft_key(
        cls,
        target: Mapping[str, object],
        info: Mapping[str, object],
        sku_map: Mapping[str, Mapping[str, object]],
    ) -> dict[
        str,
        tuple[
            str | None,
            Decimal,
            Decimal | None,
            tuple[Decimal, Decimal, Decimal] | None,
        ],
    ]:
        raw_prices = target.get("expected_sku_prices")
        if not raw_prices:
            price = cls._decimal(target["expected_price"])
            return {str(key): (None, price, None, None) for key in sku_map}
        if not isinstance(raw_prices, Mapping):
            raise TikTokPreWritePreparationError(
                "approved per-SKU prices are malformed",
                code="sku_price_binding_invalid",
            )
        approved = {
            str(model_sku).strip(): cls._decimal(price)
            for model_sku, price in raw_prices.items()
            if type(model_sku) is str and model_sku.strip()
        }
        if len(approved) != len(raw_prices):
            raise TikTokPreWritePreparationError(
                "approved per-SKU prices are malformed",
                code="sku_price_binding_invalid",
            )
        variant_model_skus = target.get("expected_variant_model_skus")
        if variant_model_skus:
            if not isinstance(variant_model_skus, Mapping):
                raise TikTokPreWritePreparationError(
                    "approved variant lineage is malformed",
                    code="sku_price_binding_invalid",
                )
            try:
                bindings = approved_variant_key_bindings(
                    info,
                    selected_sku_keys=list(variant_model_skus),
                    model_skus=variant_model_skus,
                    specifications=target.get("expected_variant_specifications"),
                )
            except TikTokVariantBindingError as error:
                raise TikTokPreWritePreparationError(
                    str(error), code="sku_price_binding_invalid"
                ) from error
            if set(variant_model_skus.values()) != set(approved):
                raise TikTokPreWritePreparationError(
                    "approved variant price coverage drifted",
                    code="sku_price_binding_invalid",
                )
            raw_parcels = target.get("expected_sku_parcels")
            if not isinstance(raw_parcels, Mapping) or set(raw_parcels) != set(
                variant_model_skus
            ):
                raise TikTokPreWritePreparationError(
                    "approved per-SKU parcel coverage drifted",
                    code="sku_parcel_binding_invalid",
                )
            result = {}
            for variant in variant_model_skus:
                raw_parcel = raw_parcels.get(variant)
                if not isinstance(raw_parcel, Mapping):
                    raise TikTokPreWritePreparationError(
                        "approved per-SKU parcel is malformed",
                        code="sku_parcel_binding_invalid",
                    )
                sku_weight = cls._decimal(raw_parcel.get("weight_kg"))
                sku_package = cls._package(raw_parcel.get("package_cm"))
                result[str(bindings[variant])] = (
                    str(variant_model_skus[variant]),
                    approved[str(variant_model_skus[variant])],
                    sku_weight,
                    sku_package,
                )
            return result
        draft_by_model: dict[str, str] = {}
        for key, row in sku_map.items():
            model_sku = str(row.get("itemNum") or "").strip()
            if not model_sku or model_sku in draft_by_model:
                raise TikTokPreWritePreparationError(
                    "Miaoshou model SKU identity is malformed",
                    code="sku_price_binding_invalid",
                )
            draft_by_model[model_sku] = str(key)
        if set(draft_by_model) != set(approved):
            raise TikTokPreWritePreparationError(
                "Miaoshou model SKU identity does not match approved prices",
                code="sku_price_binding_invalid",
            )
        return {
            draft_key: (model_sku, approved[model_sku], None, None)
            for model_sku, draft_key in draft_by_model.items()
        }

    @classmethod
    def _bound_variant_specifications(
        cls,
        target: Mapping[str, object],
        info: Mapping[str, object],
        sku_map: Mapping[str, Mapping[str, object]],
    ) -> dict[str, tuple[dict[str, str], str]]:
        """Bind frozen display facts only through the exact Model-SKU contract."""

        raw_models = target.get("expected_variant_model_skus")
        raw_specifications = target.get("expected_variant_specifications")
        if raw_specifications in (None, {}):
            return {}
        if not isinstance(raw_models, Mapping) or not isinstance(
            raw_specifications, Mapping
        ) or not raw_models or set(raw_models) != set(raw_specifications):
            raise TikTokPreWritePreparationError(
                "approved variant display specification coverage is invalid",
                code="sku_price_binding_invalid",
            )
        models = {str(key): value for key, value in raw_models.items()}
        if set(models) != set(raw_models) or any(
            type(value) is not str or not value.strip() for value in models.values()
        ):
            raise TikTokPreWritePreparationError(
                "approved variant model SKU identity is invalid",
                code="sku_price_binding_invalid",
            )
        specifications: dict[str, dict[str, str]] = {}
        for variant, raw_specification in raw_specifications.items():
            if not isinstance(raw_specification, Mapping):
                raise TikTokPreWritePreparationError(
                    "approved variant display specification is invalid",
                    code="sku_price_binding_invalid",
                )
            specification = {
                str(key): value
                for key, value in raw_specification.items()
                if type(key) is str and type(value) is str and value.strip()
            }
            if set(specification) != set(raw_specification) or not specification:
                raise TikTokPreWritePreparationError(
                    "approved variant display specification is invalid",
                    code="sku_price_binding_invalid",
                )
            display = specification.get("option")
            if type(display) is not str or not display.strip():
                raise TikTokPreWritePreparationError(
                    "approved variant display option is invalid",
                    code="sku_price_binding_invalid",
                )
            specifications[str(variant)] = specification
        try:
            bindings = approved_variant_key_bindings(
                info,
                selected_sku_keys=list(models),
                model_skus=models,
                specifications=specifications,
            )
        except TikTokVariantBindingError as error:
            raise TikTokPreWritePreparationError(
                str(error), code="sku_price_binding_invalid"
            ) from error
        result = {
            str(bindings[variant]): (
                specifications[variant], specifications[variant]["option"]
            )
            for variant in models
        }
        if set(result) != set(str(key) for key in sku_map):
            raise TikTokPreWritePreparationError(
                "Miaoshou variant display specification binding is incomplete",
                code="sku_price_binding_invalid",
            )
        return result

    @classmethod
    def _display_value_by_sku_key(
        cls,
        info: Mapping[str, object],
        expected: Mapping[str, tuple[dict[str, str], str]],
    ) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
        """Return the single final-sale property value for every bound SKU row."""

        raw_properties = info.get("skuPropertyList")
        raw_skus = info.get("skuMap")
        if not isinstance(raw_properties, list) or not raw_properties or not isinstance(
            raw_skus, Mapping
        ):
            raise TikTokPreWritePreparationError(
                "Miaoshou display specification projection is unavailable",
                code="sku_price_binding_invalid",
            )
        target_property = raw_properties[-1]
        if not isinstance(target_property, dict):
            raise TikTokPreWritePreparationError(
                "Miaoshou final sale property is malformed",
                code="sku_price_binding_invalid",
            )
        raw_values = target_property.get("attrValueList")
        if not isinstance(raw_values, list) or not raw_values:
            raise TikTokPreWritePreparationError(
                "Miaoshou final sale property values are malformed",
                code="sku_price_binding_invalid",
            )
        values_by_id: dict[str, dict[str, object]] = {}
        for value in raw_values:
            if not isinstance(value, dict):
                raise TikTokPreWritePreparationError(
                    "Miaoshou final sale property values are malformed",
                    code="sku_price_binding_invalid",
                )
            value_id = value.get("attrValueId")
            if type(value_id) is not str or not value_id.strip() or value_id in values_by_id:
                raise TikTokPreWritePreparationError(
                    "Miaoshou final sale property identity is ambiguous",
                    code="sku_price_binding_invalid",
                )
            values_by_id[value_id] = value
        if set(expected) != set(str(key) for key in raw_skus):
            raise TikTokPreWritePreparationError(
                "Miaoshou display specification SKU coverage is incomplete",
                code="sku_price_binding_invalid",
            )
        by_sku_key: dict[str, dict[str, object]] = {}
        used_ids: set[str] = set()
        for raw_key in expected:
            value_ids = [part for part in raw_key.split(";") if part]
            matching_ids = [value_id for value_id in value_ids if value_id in values_by_id]
            if len(matching_ids) != 1 or matching_ids[0] in used_ids:
                raise TikTokPreWritePreparationError(
                    "Miaoshou final sale property binding is ambiguous",
                    code="sku_price_binding_invalid",
                )
            used_ids.add(matching_ids[0])
            by_sku_key[raw_key] = values_by_id[matching_ids[0]]
        return target_property, by_sku_key

    @classmethod
    def _apply_variant_specifications(
        cls,
        info: dict[str, object],
        expected: Mapping[str, tuple[dict[str, str], str]],
    ) -> None:
        if not expected:
            return
        target_property, values_by_sku_key = cls._display_value_by_sku_key(info, expected)
        raw_properties = info["skuPropertyList"]
        raw_skus = info["skuMap"]
        assert isinstance(raw_properties, list) and isinstance(raw_skus, Mapping)
        target_property["attrName"] = "Specification"
        for raw_key, (specification, display) in expected.items():
            value = values_by_sku_key[raw_key]
            value["attrValue"] = display
            row = raw_skus.get(raw_key)
            if not isinstance(row, Mapping):
                raise TikTokPreWritePreparationError(
                    "Miaoshou display specification SKU row is malformed",
                    code="sku_price_binding_invalid",
                )
            row["specification"] = deepcopy(specification)
        raw_properties[-1] = target_property

    @classmethod
    def _variant_specifications_match(
        cls,
        info: Mapping[str, object],
        expected: Mapping[str, tuple[dict[str, str], str]],
        *,
        accept_provider_omission: bool,
    ) -> bool:
        if not expected:
            return True
        target_property, values_by_sku_key = cls._display_value_by_sku_key(info, expected)
        if target_property.get("attrName") != "Specification":
            return False
        raw_skus = info.get("skuMap")
        if not isinstance(raw_skus, Mapping):
            return False
        for raw_key, (specification, display) in expected.items():
            row = raw_skus.get(raw_key)
            if not isinstance(row, Mapping) or values_by_sku_key[raw_key].get(
                "attrValue"
            ) != display:
                return False
            observed_specification = row.get("specification")
            if observed_specification is None and accept_provider_omission:
                continue
            if observed_specification != specification:
                return False
        return True

    @classmethod
    def _parent_parcel(
        cls, target: Mapping[str, object]
    ) -> tuple[Decimal, tuple[Decimal, Decimal, Decimal]]:
        try:
            return cls._decimal(target.get("expected_weight_kg")), cls._package(
                target.get("expected_package_cm")
            )
        except (TypeError, ValueError) as error:
            raise TikTokPreWritePreparationError(
                "approved parent parcel is malformed",
                code="parent_parcel_invalid",
            ) from error

    @classmethod
    def _package(
        cls, value: object
    ) -> tuple[Decimal, Decimal, Decimal]:
        if not isinstance(value, list) or len(value) != 3:
            raise ValueError("Miaoshou package is malformed")
        return tuple(cls._decimal(item) for item in value)  # type: ignore[return-value]

    @staticmethod
    def _package_dimension(value: Decimal) -> int:
        return int(value.to_integral_value(rounding=ROUND_CEILING))

    @classmethod
    def _sku_parcel_matches(
        cls,
        row: Mapping[str, object],
        *,
        expected_weight: Decimal | None,
        expected_package: tuple[Decimal, Decimal, Decimal] | None,
    ) -> bool:
        if expected_weight is None or expected_package is None:
            return True
        try:
            if cls._decimal(row.get("weight")) != expected_weight:
                return False
        except ValueError:
            return False
        values = [
            row.get("packageLength"),
            row.get("packageWidth"),
            row.get("packageHeight"),
        ]
        if all(value in (None, "") for value in values):
            return True
        if any(value in (None, "") for value in values):
            return False
        try:
            return all(
                cls._decimal(actual) == expected
                for actual, expected in zip(values, expected_package)
            )
        except ValueError:
            return False

    @staticmethod
    def _decimal(value: object) -> Decimal:
        if isinstance(value, bool):
            raise ValueError("Miaoshou price is malformed")
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError) as error:
            raise ValueError("Miaoshou price is malformed") from error
        if not result.is_finite() or result <= 0:
            raise ValueError("Miaoshou price is malformed")
        return result


def production_tiktok_publisher():
    """Build the channel-owned publisher without importing a control plane."""

    from domains.channel_operations.tiktok_publisher import TikTokPublisher

    # Miaoshou throttles this mutation endpoint.  Pacing is transport hygiene,
    # not a cross-target dependency: a rejection still continues immediately
    # to the next independent target.
    return TikTokPublisher(
        transport=MiaoshouTikTokTransport(publish_interval_seconds=1.1)
    )
