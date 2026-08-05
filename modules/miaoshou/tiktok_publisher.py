"""Miaoshou transport for the independent TikTok publisher."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import time
from typing import Callable, Mapping

from modules.miaoshou.client import post_open


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
        info = self._mapping(draft.get("info"), "draft info")
        if str(info.get("cid") or "") != str(target["expected_category_id"]):
            return False
        expected = self._decimal(target["expected_price"])
        sku_map = self._sku_map(info)
        return all(
            self._decimal(row.get("price")) == expected
            and self._decimal(row.get("priceIncludeVat")) == expected
            for row in sku_map.values()
        )

    def save_approved_draft(
        self, target: Mapping[str, object], draft: Mapping[str, object]
    ) -> Mapping[str, object]:
        label = self._label(target)
        detail_id = int(str(target["detail_id"]))
        info = deepcopy(dict(self._mapping(draft.get("info"), "draft info")))
        info["cid"] = str(target["expected_category_id"])
        price = float(self._decimal(target["expected_price"]))
        sku_map = self._sku_map(info)
        updated_skus: dict[str, object] = {}
        for key, raw_row in sku_map.items():
            row = deepcopy(dict(raw_row))
            row["price"] = price
            row["priceIncludeVat"] = price
            updated_skus[str(key)] = row
        info["skuMap"] = updated_skus
        if label == "tiktok:GB":
            info["isCodOpen"] = "0"
            info["sizeChart"] = ""
            info["sizeChartType"] = ""
            info["deliveryOptionSetType"] = str(
                info.get("deliveryOptionSetType") or "default"
            )
            info["productAttributes"] = self._gb_required_attributes(target)
        oss_md5 = str(draft.get("oss_md5") or "")
        if label in _SITE_DRAFT_TARGETS:
            return self._post(
                SAVE_SITE_DRAFT_PATH,
                {
                    "detailId": detail_id,
                    "site": _SITE_BY_TARGET[label],
                    "siteCollectItemInfo": info,
                    "ossMd5": oss_md5,
                },
            )
        return self._post(
            SAVE_SHOP_DRAFT_PATH,
            {
                "detailId": detail_id,
                "shopId": int(str(target["shop_id"])),
                "shopCollectItemInfo": info,
                "ossMd5": oss_md5,
            },
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
        if not required:
            raise ValueError("Miaoshou mandatory category attribute is unavailable")
        return required

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
    def _label(target: Mapping[str, object]) -> str:
        label = str(target.get("target_label") or "")
        expected_shop = EXPECTED_SHOP_ID_BY_TARGET.get(label)
        if expected_shop is None or str(target.get("shop_id") or "") != expected_shop:
            raise ValueError("TikTok target shop binding drifted")
        return label

    @staticmethod
    def _mapping(value: object, name: str) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise ValueError(f"Miaoshou {name} is malformed")
        return value

    @classmethod
    def _sku_map(cls, info: Mapping[str, object]) -> Mapping[str, Mapping[str, object]]:
        sku_map = info.get("skuMap")
        if not isinstance(sku_map, Mapping) or not sku_map:
            raise ValueError("Miaoshou SKU map is malformed")
        if any(not isinstance(row, Mapping) for row in sku_map.values()):
            raise ValueError("Miaoshou SKU map is malformed")
        return sku_map  # type: ignore[return-value]

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
