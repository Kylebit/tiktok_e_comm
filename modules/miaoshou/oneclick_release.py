"""Plan-bound Miaoshou direct-store one-click primitives.

The prepare half performs Miaoshou read-only observations only for TikTok,
Shopee, and Ozon.  The dispatch half accepts a JSON-only command, rehydrates
the Miaoshou client at runtime, rechecks the observed identity, and records
every invoked write boundary.  It never calls a marketplace API and never
loads or writes a workbench claim file.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

from domains.channel_operations.oneclick_write_occurrences import (
    OpenWriteOccurrence,
    WriteOccurrenceRecordingError,
    WriteOccurrenceState,
)

COMMON_WRITE = "miaoshou:COMMON:immutable_plan_write"
DETAIL_CREATE_WRITE = "miaoshou:tiktok_detail:create"
SHOP_CLAIM_WRITE = "miaoshou:tiktok_shop:claim"
DETAIL_UPDATE_WRITE = "miaoshou:tiktok_detail:update"
PUBLISH_WRITE = "miaoshou:tiktok_publish:submission"

COMMON_GET_PATH = (
    "/open/v1/product/common_collect_box/common_collect_box/"
    "get_common_collect_box_detail"
)
COMMON_EDIT_PATH = (
    "/open/v1/product/common_collect_box/common_collect_box/"
    "edit_common_collect_box_detail"
)
SOURCE_LIST_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/"
    "search_collect_box_detail_list"
)
DETAIL_CREATE_PATH = (
    "/open/v1/product/common_collect_box/common_collect_box/claimed"
)
SHOP_CLAIM_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/claim_to_shop"
)
SHOP_GET_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/"
    "get_shop_collect_item_info"
)
SHOP_SAVE_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/"
    "save_shop_collect_item_info"
)
PUBLISH_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/save_move_collect_task"
)
WEB_BATCH_SET_PRICE_PATH = (
    "/api/platform/tiktok/move/collect_box/batchSetPrice"
)
TIKTOK_CATEGORY_DECISION_SCHEMA = "approved-tiktok-category-decision/v1"
_TIKTOK_CATEGORY_BY_APPROVED_PRODUCT_CATEGORY = {
    "贴饰>墙贴": "600338",
    "墙贴": "600338",
    "wallsticker": "600338",
    "wallstickers": "600338",
}

def _direct_store_config(
    *,
    key: str,
    shop: str,
    shop_id: int,
    platform: str,
    site: str,
) -> dict[str, object]:
    """Build one immutable Miaoshou Open API storefront binding.

    The three endpoint families below are the current official Apifox
    contracts.  They are kept beside the shop identity so a stored command
    cannot drift from one platform family to another after restart.
    """

    return {
        "key": key,
        "shop": shop,
        "shop_id": shop_id,
        "region": site,
        "site": site,
        "platform": platform,
        # Every direct-store target is intentionally API-less from the
        # marketplace perspective.  Acceptance is Miaoshou submission only.
        "api": False,
        "search_path": (
            f"/open/v1/product/collect_box/{platform}/collect_box/"
            "search_collect_box_detail_list"
        ),
        "get_path": (
            f"/open/v1/product/collect_box/{platform}/collect_box/"
            + (
                "get_shop_collect_item_info"
                if platform == "tiktok"
                else (
                    "get_site_detail_simple_data"
                    if platform == "shopee"
                    else "get_site_collect_item_info"
                )
            )
        ),
        "save_path": (
            f"/open/v1/product/collect_box/{platform}/collect_box/"
            + (
                "save_shop_collect_item_info"
                if platform == "tiktok"
                else "save_site_detail_data"
            )
        ),
        "publish_path": (
            (
                "/open/v1/product/collect_box/tiktok/collect_box/"
                "save_move_collect_task"
            )
            if platform == "tiktok"
            else (
                f"/open/v1/product/collect_box/{platform}/"
                "move_collect/save_move_collect_task"
            )
        ),
    }


DIRECT_STORE_CONFIG: dict[str, dict[str, object]] = {
    "tiktok:LH_PH": _direct_store_config(
        key="lh_ph", shop="LivelyHive", shop_id=7676267,
        platform="tiktok", site="PH",
    ),
    "tiktok:LH_MY": _direct_store_config(
        key="lh_my", shop="LivelyHive", shop_id=13295169,
        platform="tiktok", site="MY",
    ),
    "tiktok:LH_TH": _direct_store_config(
        key="lh_th", shop="LivelyHive", shop_id=13295228,
        platform="tiktok", site="TH",
    ),
    "tiktok:LH_VN": _direct_store_config(
        key="lh_vn", shop="LivelyHive", shop_id=13295291,
        platform="tiktok", site="VN",
    ),
    "tiktok:MX": _direct_store_config(
        key="mx", shop="LivelyHive", shop_id=16265910,
        platform="tiktok", site="MX",
    ),
    "tiktok:GB": _direct_store_config(
        key="gb", shop="LivelyHive", shop_id=10204699,
        platform="tiktok", site="GB",
    ),
    "tiktok:HB_PH": _direct_store_config(
        key="hb_ph", shop="HomeBloom", shop_id=15173238,
        platform="tiktok", site="PH",
    ),
    "tiktok:HB_MY": _direct_store_config(
        key="hb_my", shop="HomeBloom", shop_id=16770639,
        platform="tiktok", site="MY",
    ),
    "tiktok:HB_TH": _direct_store_config(
        key="hb_th", shop="HomeBloom", shop_id=16770557,
        platform="tiktok", site="TH",
    ),
    "tiktok:HB_VN": _direct_store_config(
        key="hb_vn", shop="HomeBloom", shop_id=16783702,
        platform="tiktok", site="VN",
    ),
    "shopee:PH": _direct_store_config(
        key="ph", shop="LivelyHive", shop_id=7808255,
        platform="shopee", site="PH",
    ),
    "shopee:MY": _direct_store_config(
        key="my", shop="LivelyHive", shop_id=13295318,
        platform="shopee", site="MY",
    ),
    "shopee:TH": _direct_store_config(
        key="th", shop="LivelyHive", shop_id=13295319,
        platform="shopee", site="TH",
    ),
    "shopee:VN": _direct_store_config(
        key="vn", shop="LivelyHive", shop_id=13295320,
        platform="shopee", site="VN",
    ),
    # Miaoshou's shop-list contract uses the literal site value ``OZON``.
    "ozon:RU": _direct_store_config(
        key="ru", shop="LivelyHive_OZON", shop_id=16075432,
        platform="ozon", site="OZON",
    ),
}

# Backward-compatible symbol for focused tests and older imports.  Its
# semantics are now all direct-store targets, not TikTok-only sites.
SITE_CONFIG = DIRECT_STORE_CONFIG
API_LESS_TIKTOK_TARGETS = frozenset(
    target
    for target, config in SITE_CONFIG.items()
    if config["api"] is not True
)
HOMEBLOOM_API_LESS_TARGETS = frozenset(
    target
    for target, config in SITE_CONFIG.items()
    if config["shop"] == "HomeBloom" and config["api"] is not True
)


class MiaoshouOneClickPreDispatchError(RuntimeError):
    pass


class MiaoshouOneClickPrepareBlocked(RuntimeError):
    """A target-local immutable-content/capability blocker."""

    def __init__(self, code: str, detail: str, *, category: str = "CONTENT") -> None:
        super().__init__(detail)
        self.classification = {
            "AUTH": "BLOCKED_AUTH",
            "INVENTORY": "BLOCKED_INVENTORY",
        }.get(category, "BLOCKED_CAPABILITY")
        self.reason_category = category
        self.reason_scope = "TARGET"
        self.reason_code = code


class MiaoshouOneClickDispatchError(RuntimeError):
    def __init__(
        self,
        detail: str,
        *,
        writes: tuple[str, ...],
        unknown: bool,
        external_id: str | None = None,
        external_write_count: int | None = None,
        confirmed_lower_bound: int | None = None,
        possible_upper_bound: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.external_writes = writes
        self.dispatch_outcome_unknown = unknown
        self.external_id = external_id
        self.external_write_count = (
            external_write_count
            if external_write_count is not None or unknown
            else len(writes)
        )
        self.confirmed_external_write_count_lower_bound = (
            (
                len(writes)
                if not unknown
                else max(0, len(writes) - 1)
            )
            if confirmed_lower_bound is None
            else confirmed_lower_bound
        )
        self.possible_external_write_count_upper_bound = (
            (
                self.external_write_count
                if self.external_write_count is not None
                else max(
                    len(writes),
                    self.confirmed_external_write_count_lower_bound + 1,
                )
            )
            if possible_upper_bound is None
            else possible_upper_bound
        )


class MiaoshouCollectBoxPreparationError(RuntimeError):
    """Truthful boundary for platform-collect-box preparation only."""

    def __init__(
        self,
        detail: str,
        *,
        writes: tuple[str, ...],
        write_count: int | None,
        target_results: tuple[tuple[str, str], ...] = (),
    ) -> None:
        super().__init__(detail)
        self.external_writes = writes
        self.external_write_count = write_count
        self.target_results = target_results


@dataclass(frozen=True)
class MiaoshouRuntimeTransport:
    """Fixture-friendly runtime transport.

    ``post`` is the production primitive.  The four legacy-shaped callables
    remain optional so older focused fault tests can inject a narrow boundary.
    """

    post: Callable[[str, Mapping[str, object]], object] | None = None
    tiktok_readback: Callable[[Mapping[str, object]], bool] | None = None
    update_detail: Callable[[Mapping[str, object]], object] | None = None
    audit_detail: Callable[[str, str], bool] | None = None
    publish: Callable[[str, str], object] | None = None


_runtime_transport_factory: Callable[[], MiaoshouRuntimeTransport] | None = None
_prepare_post_factory: Callable[[], Callable[[str, Mapping[str, object]], object]] | None = None


def configure_runtime_transport_factory(
    factory: Callable[[], MiaoshouRuntimeTransport] | None,
) -> None:
    global _runtime_transport_factory
    _runtime_transport_factory = factory


def configure_prepare_post_factory(
    factory: Callable[[], Callable[[str, Mapping[str, object]], object]] | None,
) -> None:
    global _prepare_post_factory
    _prepare_post_factory = factory


def prepare_tiktok_miaoshou_target(seed, request) -> dict[str, object]:
    """Build a restart-safe command from the approved plan and official reads."""
    try:
        return _prepare_tiktok_miaoshou_target(seed, request)
    except MiaoshouOneClickPrepareBlocked:
        raise
    except (FileNotFoundError, KeyError, PermissionError) as error:
        raise MiaoshouOneClickPrepareBlocked(
            "miaoshou_credentials_unavailable",
            "prepared Miaoshou credentials are missing or invalid",
            category="AUTH",
        ) from error
    except MiaoshouOneClickPreDispatchError as error:
        raise MiaoshouOneClickPrepareBlocked(
            "miaoshou_official_prepare_proof_unavailable",
            "official Miaoshou read-only proof is unavailable",
            category="CAPABILITY",
        ) from error
    except (TimeoutError, OSError, RuntimeError) as error:
        raise MiaoshouOneClickPrepareBlocked(
            "miaoshou_official_prepare_transport_unavailable",
            "official Miaoshou read-only transport is unavailable",
            category="CAPABILITY",
        ) from error


def _prepare_tiktok_miaoshou_target(seed, request) -> dict[str, object]:
    payload = getattr(request, "immutable_plan_payload", None)
    if not isinstance(payload, Mapping):
        raise MiaoshouOneClickPrepareBlocked(
            "immutable_plan_payload_missing",
            "approved immutable plan payload is unavailable",
        )
    source_offer_id = str(seed.command["source_query"]["source_offer_id"])
    target = str(seed.target_label)
    post = _prepare_post()
    if target == "miaoshou:COMMON":
        command, proof = _prepare_common(
            payload, source_offer_id=source_offer_id, post=post
        )
    elif target in SITE_CONFIG:
        command, proof = _prepare_site(
            payload,
            target=target,
            source_offer_id=source_offer_id,
            post=post,
        )
    else:  # defensive; the registry already owns the label set
        raise MiaoshouOneClickPrepareBlocked(
            "miaoshou_target_configuration_missing",
            "fixed Miaoshou target configuration is unavailable",
            category="CAPABILITY",
        )
    if command.get("kind") == "DIRECT_STORE":
        identity_binding = {
            "target_label": target,
            "shop_id": command["shop_id"],
            "platform": command["platform"],
            "idempotency_key": str(getattr(seed, "idempotency_key", "")),
            "source_identity_digest": str(
                getattr(seed, "source_identity_digest", "")
            ),
            "payload_digest": str(
                getattr(request, "payload_digest", "")
            ),
            "adapter_policy_digest": str(
                getattr(request, "adapter_policy_digest", "")
            ),
        }
        if any(not value for value in identity_binding.values()):
            raise MiaoshouOneClickPrepareBlocked(
                "direct_store_identity_binding_incomplete",
                "direct-store immutable identity binding is incomplete",
                category="CAPABILITY",
            )
        command["identity_binding"] = identity_binding
        proof["identity_binding_digest"] = _digest(identity_binding)
    json.loads(json.dumps(command, ensure_ascii=False, sort_keys=True))
    json.loads(json.dumps(proof, ensure_ascii=False, sort_keys=True))
    return {
        "command": command,
        "proof": proof,
        "external_writes_performed": [],
    }


def read_source_offer_pages(
    source_offer_id: str,
    *,
    post: Callable[[str, Mapping[str, object]], object],
    target: str | None = None,
    page_size: int = 100,
    max_pages: int = 20,
) -> tuple[dict[str, object], ...]:
    """Full-paginate only the canonical source offer; malformed pages stop."""
    if (
        type(source_offer_id) is not str
        or not source_offer_id.isdecimal()
        or int(source_offer_id) <= 0
    ):
        raise MiaoshouOneClickPreDispatchError("canonical source offer is invalid")
    if (
        type(page_size) is not int
        or page_size <= 0
        or type(max_pages) is not int
        or max_pages <= 0
    ):
        raise MiaoshouOneClickPreDispatchError("source pagination policy is invalid")
    pages: list[dict[str, object]] = []
    seen: set[int] = set()
    page_no = 1
    for _ in range(max_pages):
        if page_no in seen:
            raise MiaoshouOneClickPreDispatchError("source query cursor loop")
        seen.add(page_no)
        config = DIRECT_STORE_CONFIG.get(target) if target is not None else None
        search_path = (
            str(config["search_path"])
            if isinstance(config, Mapping)
            else SOURCE_LIST_PATH
        )
        response = post(
            search_path,
            {
                "pageNo": page_no,
                "pageSize": page_size,
                "filter": {"sourceItemIdKeyword": source_offer_id},
            },
        )
        if not _success(response):
            raise MiaoshouOneClickPreDispatchError("source query top-level error")
        data = response.get("data")
        if not isinstance(data, Mapping):
            raise MiaoshouOneClickPreDispatchError(
                "source query response is malformed"
            )
        rows = data.get("detailList")
        if rows is None:
            rows = data.get("list")
        total_present = "totalCount" in data or "total" in data
        total = data.get("totalCount", data.get("total"))
        has_next_present = "hasNextPage" in data
        has_next = data.get("hasNextPage")
        if (
            (total_present and (type(total) is not int or total < 0))
            or not isinstance(rows, list)
            or any(not isinstance(row, Mapping) for row in rows)
        ):
            raise MiaoshouOneClickPreDispatchError(
                "source query response is malformed"
            )
        pages.append(
            {
                "result": "success",
                "data": {
                    "detailList": [dict(row) for row in rows],
                    "totalCount": total if total_present else None,
                    "hasNextPage": has_next if has_next_present else None,
                    "nextPageToken": data.get(
                        "nextPageToken", data.get("nextPage")
                    ),
                },
            }
        )
        if has_next is False or (
            not has_next_present and len(rows) < page_size
        ):
            observed = sum(
                len(page["data"]["detailList"]) for page in pages
            )
            if total_present and total != observed:
                raise MiaoshouOneClickPreDispatchError(
                    "source query total is incomplete"
                )
            return tuple(pages)
        next_page = data.get("nextPage", data.get("nextPageToken"))
        if (
            has_next is not True
            or type(next_page) is not int
            or next_page <= page_no
        ):
            raise MiaoshouOneClickPreDispatchError(
                "source query cursor is invalid"
            )
        page_no = next_page
    raise MiaoshouOneClickPreDispatchError(
        "source query exceeded bounded pagination"
    )


def dispatch_tiktok_miaoshou_prepared_target(request) -> dict[str, object]:
    """Dispatch COMMON or one Miaoshou storefront with cumulative evidence."""
    command = _provider_command(request)
    _verify_stored_command_identity(request, command)
    kind = command.get("kind")
    if kind == "COMMON":
        return _dispatch_common(request, command)
    if kind in {"TIKTOK_SITE", "DIRECT_STORE"}:
        return _dispatch_site(request, command)
    raise MiaoshouOneClickPreDispatchError(
        "prepared Miaoshou command is incomplete"
    )


def _verify_stored_command_identity(
    request: object,
    command: Mapping[str, object],
) -> None:
    """Rebind a stored command to the fixed 03 shop contract before clients."""

    target = command.get("target_label")
    request_target = getattr(request, "target_label", None)
    if (
        type(target) is not str
        or not target
        or type(request_target) is not str
        or request_target != target
    ):
        raise MiaoshouOneClickPreDispatchError(
            "stored Miaoshou target identity is invalid"
        )
    kind = command.get("kind")
    if kind == "COMMON":
        if (
            target != "miaoshou:COMMON"
            or command.get("schema_version")
            != "oneclick-miaoshou-common-command/v1"
        ):
            raise MiaoshouOneClickPreDispatchError(
                "stored Miaoshou COMMON identity is invalid"
            )
        return
    if kind not in {"TIKTOK_SITE", "DIRECT_STORE"} or target not in SITE_CONFIG:
        raise MiaoshouOneClickPreDispatchError(
            "stored Miaoshou site identity is invalid"
        )
    expected_schema = (
        "oneclick-miaoshou-tiktok-command/v1"
        if kind == "TIKTOK_SITE"
        else "oneclick-miaoshou-direct-store-command/v1"
    )
    if command.get("schema_version") != expected_schema:
        raise MiaoshouOneClickPreDispatchError(
            "stored Miaoshou site schema is invalid"
        )
    if kind == "DIRECT_STORE":
        binding = command.get("identity_binding")
        expected_binding = {
            "target_label": request_target,
            "shop_id": command.get("shop_id"),
            "platform": command.get("platform"),
            "idempotency_key": getattr(request, "idempotency_key", None),
            "source_identity_digest": getattr(
                request, "source_identity_digest", None
            ),
            "payload_digest": getattr(request, "payload_digest", None),
            "adapter_policy_digest": getattr(
                request, "adapter_policy_digest", None
            ),
        }
        if (
            not isinstance(binding, Mapping)
            or dict(binding) != expected_binding
            or any(
                type(value) is not str or not value
                for value in expected_binding.values()
            )
        ):
            raise MiaoshouOneClickPreDispatchError(
                "stored Miaoshou immutable identity binding drifted"
            )
    config = SITE_CONFIG[target]
    expected = command.get("expected")
    if not isinstance(expected, Mapping):
        raise MiaoshouOneClickPreDispatchError(
            "stored Miaoshou expected identity is invalid"
        )
    fixed_identity = (
        command.get("shop_id") == str(config["shop_id"])
        and expected.get("target_label") == target
        and expected.get("shop_id") == str(config["shop_id"])
        and expected.get("shop_name") == config["shop"]
        and expected.get("region") == config["region"]
        and expected.get("platform") in {None, config["platform"]}
        and command.get("platform") in {None, config["platform"]}
        and command.get("api_less") is (config["api"] is not True)
    )
    source_offer_id = command.get("source_offer_id")
    common_detail_id = command.get("common_detail_id")
    identity_exact = (
        type(source_offer_id) is str
        and source_offer_id.isdecimal()
        and int(source_offer_id) > 0
        and expected.get("source_offer_id") == source_offer_id
        and type(common_detail_id) is str
        and common_detail_id.isdecimal()
        and int(common_detail_id) > 0
        and expected.get("common_detail_id") == common_detail_id
    )
    action = command.get("action")
    detail_id = command.get("detail_id")
    action_exact = (
        action == "CREATE_AND_CLAIM" and detail_id is None
    ) or (
        action == "USE_EXISTING"
        and type(detail_id) is str
        and detail_id.isdecimal()
        and int(detail_id) > 0
    )
    if not fixed_identity or not identity_exact or not action_exact:
        raise MiaoshouOneClickPreDispatchError(
            "stored Miaoshou shop/source identity drifted"
        )


def _prepare_common(
    payload: Mapping[str, object],
    *,
    source_offer_id: str,
    post: Callable[[str, Mapping[str, object]], object],
) -> tuple[dict[str, object], dict[str, object]]:
    expected = _approved_common(payload, source_offer_id)
    detail, _oss_md5 = _read_common(post, expected["common_detail_id"])
    _verify_common_identity(
        detail,
        common_detail_id=expected["common_detail_id"],
        source_offer_id=source_offer_id,
    )
    current_skus = _sku_map(detail)
    wanted = set(expected["selected_sku_keys"])
    if {_normalize_variant(key) for key in current_skus} != wanted:
        raise MiaoshouOneClickPrepareBlocked(
            "common_variant_identity_mismatch",
            "approved variants do not exactly match Miaoshou COMMON",
        )
    snapshot = _detail_snapshot(detail)
    command = {
        "schema_version": "oneclick-miaoshou-common-command/v1",
        "kind": "COMMON",
        "target_label": "miaoshou:COMMON",
        "source_offer_id": source_offer_id,
        "common_detail_id": expected["common_detail_id"],
        "expected": expected,
        "observed_snapshot_digest": _digest(snapshot),
    }
    proof = {
        "schema_version": "oneclick-miaoshou-common-proof/v1",
        "identity_exact": True,
        "source_offer_id_digest": _text_digest(source_offer_id),
        "observed_snapshot_digest": command["observed_snapshot_digest"],
        "expected_digest": _digest(expected),
        "image_count": len(expected["images"]),
        "variant_count": len(wanted),
    }
    return command, proof


def _prepare_site(
    payload: Mapping[str, object],
    *,
    target: str,
    source_offer_id: str,
    post: Callable[[str, Mapping[str, object]], object],
) -> tuple[dict[str, object], dict[str, object]]:
    config = SITE_CONFIG[target]
    expected = _approved_site(
        payload,
        target=target,
        config=config,
        source_offer_id=source_offer_id,
    )
    pages = read_source_offer_pages(
        source_offer_id, post=post, target=target
    )
    observed_common_detail_id = _resolve_common_detail_id_from_pages(
        pages,
        source_offer_id=source_offer_id,
    )
    # The approved product_id is the business product/offer identity.  It is
    # not Miaoshou's platform-specific COMMON detail identity.  Bind the
    # latter only from the exact canonical-source query.
    expected["common_detail_id"] = observed_common_detail_id
    detail_id = _resolve_detail_from_pages(
        pages,
        common_detail_id=observed_common_detail_id,
        shop_id=expected["shop_id"],
        target=target,
    )
    action = "USE_EXISTING" if detail_id is not None else "CREATE_AND_CLAIM"
    snapshot_digest = None
    if detail_id is not None:
        detail, _oss_md5 = _read_shop(
            post,
            detail_id,
            expected["shop_id"],
            target=target,
        )
        _verify_shop_identity(
            detail,
            detail_id=detail_id,
            shop_id=expected["shop_id"],
        )
        _verify_site_variants(detail, expected)
        snapshot_digest = _digest(_detail_snapshot(detail))
    command = {
        "schema_version": "oneclick-miaoshou-direct-store-command/v1",
        "kind": "DIRECT_STORE",
        "target_label": target,
        "platform": config["platform"],
        "site": config["site"],
        "source_offer_id": source_offer_id,
        "common_detail_id": expected["common_detail_id"],
        "shop_id": expected["shop_id"],
        "action": action,
        "detail_id": str(detail_id) if detail_id is not None else None,
        "api_less": config["api"] is not True,
        "expected": expected,
        "observed_snapshot_digest": snapshot_digest,
    }
    proof = {
        "schema_version": "oneclick-miaoshou-direct-store-proof/v1",
        "target_label": target,
        "platform": config["platform"],
        "shop_binding_exact": True,
        "source_offer_id_digest": _text_digest(source_offer_id),
        "detail_action": action,
        "detail_identity_digest": (
            _text_digest(f"{detail_id}:{expected['shop_id']}")
            if detail_id is not None
            else None
        ),
        "observed_snapshot_digest": snapshot_digest,
        "expected_digest": _digest(expected),
        "image_count": len(expected["images"]),
        "model_sku_count": len(expected["model_skus"]),
    }
    return command, proof


def _dispatch_common(
    request: object, command: Mapping[str, object]
) -> dict[str, object]:
    transport = _runtime_transport()
    post = _required_post(transport)
    expected = _mapping(command.get("expected"), "COMMON expected payload")
    detail, oss_md5 = _read_common(post, str(command["common_detail_id"]))
    _verify_common_identity(
        detail,
        common_detail_id=str(command["common_detail_id"]),
        source_offer_id=str(command["source_offer_id"]),
    )
    if _digest(_detail_snapshot(detail)) != command.get(
        "observed_snapshot_digest"
    ):
        raise MiaoshouOneClickPreDispatchError(
            "Miaoshou COMMON changed after read-only preparation"
        )
    updated = _apply_expected(detail, expected)
    body = {
        "commonCollectBoxDetailId": int(str(command["common_detail_id"])),
        "editCommonCollectBoxDetail": updated,
        "ossMd5": oss_md5,
    }
    occurrence_state = WriteOccurrenceState()
    occurrence = _open_write(
        request,
        occurrence_state,
        "common_update-1",
        COMMON_WRITE,
        external_id=str(command["common_detail_id"]),
    )
    try:
        response = post(COMMON_EDIT_PATH, body)
    except Exception as error:
        raise _unknown_write_error(
            occurrence_state,
            occurrence,
            "COMMON write transport outcome is unknown",
            external_id=str(command["common_detail_id"]),
        ) from error
    if not isinstance(response, Mapping):
        raise _unknown_write_error(
            occurrence_state,
            occurrence,
            "COMMON write response is malformed",
            external_id=str(command["common_detail_id"]),
        )
    if not _success(response):
        _reject_write(
            request,
            occurrence_state,
            occurrence,
            external_id=str(command["common_detail_id"]),
        )
        raise _rejected_write_error(
            occurrence_state,
            "COMMON write was not accepted",
            external_id=str(command["common_detail_id"]),
        )
    _confirm_write(
        request,
        occurrence_state,
        occurrence,
        external_id=str(command["common_detail_id"]),
    )
    try:
        verified, _ = _read_common(post, str(command["common_detail_id"]))
        _verify_expected_detail(verified, expected)
    except Exception as error:
        raise MiaoshouOneClickDispatchError(
            "COMMON write readback is unknown",
            writes=occurrence_state.external_writes,
            unknown=True,
            external_id=str(command["common_detail_id"]),
            external_write_count=occurrence_state.external_write_count,
            confirmed_lower_bound=occurrence_state.external_write_count,
            possible_upper_bound=occurrence_state.external_write_count,
        ) from error
    return _receipt(
        "SUCCEEDED",
        str(command["common_detail_id"]),
        occurrence_state.external_writes,
        True,
        "miaoshou_common_verified",
        write_count=occurrence_state.external_write_count,
    )


def _dispatch_site(
    request: object, command: Mapping[str, object]
) -> dict[str, object]:
    transport = _runtime_transport()
    post = _required_post(transport)
    target = str(command["target_label"])
    config = DIRECT_STORE_CONFIG[target]
    platform = str(config["platform"])
    expected = _mapping(
        command.get("expected"), "Miaoshou direct-store expected payload"
    )
    occurrence_state = WriteOccurrenceState()
    external_id: str | None = None

    pages = read_source_offer_pages(
        str(command["source_offer_id"]), post=post, target=target
    )
    detail_id = _resolve_detail_from_pages(
        pages,
        common_detail_id=str(command["common_detail_id"]),
        shop_id=str(command["shop_id"]),
        target=target,
    )
    if detail_id is None:
        if command.get("action") != "CREATE_AND_CLAIM":
            raise MiaoshouOneClickPreDispatchError(
                "prepared Miaoshou detail identity disappeared"
            )
        occurrence = _open_write(
            request,
            occurrence_state,
            "detail_create-1",
            _platform_write(platform, "detail:create"),
        )
        try:
            created = post(
                DETAIL_CREATE_PATH,
                {
                    "detailSerialNumberPlatformList": [
                        {
                            "detailId": int(str(command["common_detail_id"])),
                            "platform": platform,
                            "serialNumber": 1,
                        }
                    ]
                },
            )
        except Exception as error:
            raise _unknown_write_error(
                occurrence_state,
                occurrence,
                f"Miaoshou {platform} detail creation outcome is unknown",
            ) from error
        if not isinstance(created, Mapping):
            raise _unknown_write_error(
                occurrence_state,
                occurrence,
                f"Miaoshou {platform} detail creation response is malformed",
            )
        if not _success(created):
            _reject_write(request, occurrence_state, occurrence)
            raise _rejected_write_error(
                occurrence_state,
                f"Miaoshou {platform} detail creation was not accepted",
            )
        _confirm_write(request, occurrence_state, occurrence)
        try:
            detail_id = _created_detail_id(
                created,
                str(command["common_detail_id"]),
                platform=platform,
            )
        except Exception as error:
            raise MiaoshouOneClickDispatchError(
                f"Miaoshou {platform} detail creation identity is unknown",
                writes=occurrence_state.external_writes,
                unknown=False,
                external_write_count=occurrence_state.external_write_count,
                confirmed_lower_bound=occurrence_state.external_write_count,
                possible_upper_bound=occurrence_state.external_write_count,
            ) from error
        external_id = str(detail_id)
        if platform == "tiktok":
            occurrence = _open_write(
                request,
                occurrence_state,
                "shop_claim-1",
                _platform_write(platform, "shop:claim"),
                external_id=external_id,
            )
            try:
                claimed = post(
                    SHOP_CLAIM_PATH,
                    {
                        "detailIds": [detail_id],
                        "shopIds": [str(command["shop_id"])],
                    },
                )
            except Exception as error:
                raise _unknown_write_error(
                    occurrence_state,
                    occurrence,
                    "TikTok shop claim outcome is unknown",
                    external_id=external_id,
                ) from error
            if not isinstance(claimed, Mapping):
                raise _unknown_write_error(
                    occurrence_state,
                    occurrence,
                    "TikTok shop claim response is malformed",
                    external_id=external_id,
                )
            if not _success(claimed):
                _reject_write(
                    request,
                    occurrence_state,
                    occurrence,
                    external_id=external_id,
                )
                raise _rejected_write_error(
                    occurrence_state,
                    "TikTok shop claim was not accepted",
                    external_id=external_id,
                )
            _confirm_write(
                request,
                occurrence_state,
                occurrence,
                external_id=external_id,
            )
    elif command.get("detail_id") is not None and str(detail_id) != str(
        command["detail_id"]
    ):
        raise MiaoshouOneClickPreDispatchError(
            "prepared Miaoshou detail identity drifted"
        )

    try:
        external_id = f"{detail_id}:{command['shop_id']}"
        detail, oss_md5 = _read_shop(
            post,
            detail_id,
            str(command["shop_id"]),
            target=target,
        )
        _verify_shop_identity(
            detail, detail_id=detail_id, shop_id=str(command["shop_id"])
        )
        if command.get("observed_snapshot_digest") is not None and _digest(
            _detail_snapshot(detail)
        ) != command.get("observed_snapshot_digest"):
            raise MiaoshouOneClickPreDispatchError(
                f"Miaoshou {platform} detail changed after preparation"
            )
        _verify_site_variants(detail, expected)
        updated = _apply_expected_for_platform(
            detail, expected, platform=platform
        )
        body = _save_body(
            platform=platform,
            site=str(config["site"]),
            detail_id=detail_id,
            shop_id=str(command["shop_id"]),
            updated=updated,
            oss_md5=oss_md5,
        )
    except Exception as error:
        if occurrence_state.external_write_count:
            raise MiaoshouOneClickDispatchError(
                f"Miaoshou {platform} claimed detail verification is unknown",
                writes=occurrence_state.external_writes,
                unknown=True,
                external_id=external_id,
                external_write_count=occurrence_state.external_write_count,
                confirmed_lower_bound=occurrence_state.external_write_count,
                possible_upper_bound=occurrence_state.external_write_count,
            ) from error
        raise
    occurrence = _open_write(
        request,
        occurrence_state,
        "detail_update-1",
        _platform_write(platform, "detail:update"),
        external_id=external_id,
    )
    try:
        saved = (
            transport.update_detail(body)
            if transport.update_detail is not None
            else post(str(config["save_path"]), body)
        )
    except Exception as error:
        raise _unknown_write_error(
            occurrence_state,
            occurrence,
            f"Miaoshou {platform} detail update outcome is unknown",
            external_id=external_id,
        ) from error
    if not isinstance(saved, Mapping):
        raise _unknown_write_error(
            occurrence_state,
            occurrence,
            f"Miaoshou {platform} detail update response is malformed",
            external_id=external_id,
        )
    if not _accepted(saved):
        _reject_write(
            request,
            occurrence_state,
            occurrence,
            external_id=external_id,
        )
        raise _rejected_write_error(
            occurrence_state,
            f"Miaoshou {platform} detail update was not accepted",
            external_id=external_id,
        )
    _confirm_write(
        request,
        occurrence_state,
        occurrence,
        external_id=external_id,
    )
    try:
        if transport.audit_detail is not None:
            if transport.audit_detail(str(detail_id), str(command["shop_id"])) is not True:
                raise ValueError("injected draft audit mismatch")
        else:
            readback, _ = _read_shop(
                post,
                detail_id,
                str(command["shop_id"]),
                target=target,
            )
            _verify_expected_detail(
                readback, expected, platform=platform
            )
    except Exception as error:
        raise MiaoshouOneClickDispatchError(
            f"Miaoshou {platform} detail update readback is unknown",
            writes=occurrence_state.external_writes,
            # The update itself is already confirmed and no later write was
            # invoked.  Reconciliation is required for readback, but the
            # external write count is exact rather than dispatch-unknown.
            unknown=False,
            external_id=external_id,
            external_write_count=occurrence_state.external_write_count,
            confirmed_lower_bound=occurrence_state.external_write_count,
            possible_upper_bound=occurrence_state.external_write_count,
        ) from error

    occurrence = _open_write(
        request,
        occurrence_state,
        "publish_submit-1",
        _platform_write(platform, "publish:submission"),
        external_id=external_id,
    )
    try:
        submitted = (
            transport.publish(str(detail_id), str(command["shop_id"]))
            if transport.publish is not None
            else post(
                str(config["publish_path"]),
                {
                    "detailIds": [detail_id],
                    "shopIds": [str(command["shop_id"])],
                },
            )
        )
    except Exception as error:
        raise _unknown_write_error(
            occurrence_state,
            occurrence,
            f"Miaoshou {platform} publish outcome is unknown",
            external_id=external_id,
        ) from error
    if not isinstance(submitted, Mapping):
        raise _unknown_write_error(
            occurrence_state,
            occurrence,
            f"Miaoshou {platform} publish response is malformed",
            external_id=external_id,
        )
    if not _accepted(submitted):
        _reject_write(
            request,
            occurrence_state,
            occurrence,
            external_id=external_id,
        )
        raise _rejected_write_error(
            occurrence_state,
            f"Miaoshou {platform} publish was not accepted",
            external_id=external_id,
        )
    _confirm_write(
        request,
        occurrence_state,
        occurrence,
        external_id=external_id,
    )
    return _receipt(
        "SUBMITTED_UNVERIFIED",
        external_id,
        occurrence_state.external_writes,
        False,
        "miaoshou_submission_recorded",
        write_count=occurrence_state.external_write_count,
    )


def _approved_common(
    payload: Mapping[str, object], source_offer_id: str
) -> dict[str, object]:
    product_id = _positive_digit(payload.get("product_id"), "product_id")
    seller_sku = _text(payload.get("seller_sku"), "seller_sku")
    facts = _mapping(payload.get("product_facts"), "product_facts")
    title = _text(facts.get("title"), "approved title")
    weight, package = _parcel(facts)
    images = _images(payload)
    video = _video(payload)
    selected = _selected_variants(facts)
    model_skus = _model_skus(payload, selected, seller_sku)
    description = _description(payload)
    return {
        "common_detail_id": product_id,
        "source_offer_id": source_offer_id,
        "title": title,
        "item_num": seller_sku,
        "weight": weight,
        "package_cm": package,
        "images": images,
        "notes": _notes(description, images),
        "simple_description": description,
        "video_url": video,
        "selected_sku_keys": selected,
        "model_skus": model_skus,
        "product_category": facts.get("category"),
    }


def _approved_tiktok_category_id(
    payload: Mapping[str, object], *, target: str
) -> str | None:
    decisions = payload.get("approved_tiktok_category_decisions")
    if not isinstance(decisions, Mapping):
        return None
    decision = decisions.get(target)
    if not isinstance(decision, Mapping) or set(decision) != {
        "category_id",
        "evidence_digest",
    }:
        return None
    category_id = decision.get("category_id")
    evidence_digest = decision.get("evidence_digest")
    if (
        type(category_id) is not str
        or not category_id.isascii()
        or not category_id.isdigit()
        or int(category_id) <= 0
        or type(evidence_digest) is not str
        or len(evidence_digest) != 64
        or any(char not in "0123456789abcdef" for char in evidence_digest)
    ):
        return None
    return category_id


def approved_tiktok_category_decisions(
    product_category: object,
    *,
    targets: tuple[str, ...],
) -> dict[str, dict[str, str]] | None:
    """Project one approved product category into exact TikTok site evidence."""

    if not isinstance(product_category, Mapping):
        return None
    raw_name = product_category.get("name")
    if type(raw_name) is not str or not raw_name.strip():
        return None
    normalized = "".join(raw_name.split()).lower()
    category_id = _TIKTOK_CATEGORY_BY_APPROVED_PRODUCT_CATEGORY.get(normalized)
    if category_id is None:
        return None
    selected = tuple(
        target
        for target in targets
        if type(target) is str and target.startswith("tiktok:")
    )
    if not selected:
        return {}
    result: dict[str, dict[str, str]] = {}
    for target in selected:
        config = DIRECT_STORE_CONFIG.get(target)
        if not isinstance(config, Mapping) or config.get("platform") != "tiktok":
            return None
        evidence = {
            "schema_version": TIKTOK_CATEGORY_DECISION_SCHEMA,
            "approved_product_category": raw_name.strip(),
            "target_label": target,
            "site": str(config["site"]),
            "category_id": category_id,
        }
        result[target] = {
            "category_id": category_id,
            "evidence_digest": _digest(evidence),
        }
    return result


def _approved_site(
    payload: Mapping[str, object],
    *,
    target: str,
    config: Mapping[str, object],
    source_offer_id: str,
) -> dict[str, object]:
    common = _approved_common(payload, source_offer_id)
    title = _candidate_title(payload, target)
    price, currency = _price(payload, target, str(config["key"]))
    result = {
        **common,
        "target_label": target,
        "shop_name": str(config["shop"]),
        "shop_id": str(config["shop_id"]),
        "region": str(config["region"]),
        "platform": str(config["platform"]),
        "title": title,
        "price": price,
        "currency": currency,
    }
    if str(config["platform"]) == "tiktok":
        result["category_id"] = _approved_tiktok_category_id(
            payload, target=target
        )
    return result


def prepare_selected_platform_collectbox(
    *,
    platform: str,
    common_detail_id: str,
    initial_platform_detail_id: str,
    initial_claim_written: bool,
    approved_plan_payload: Mapping[str, object],
    approved_targets: tuple[str, ...],
    post: Callable[[str, Mapping[str, object]], object] | None = None,
    web_post: Callable[[str, Mapping[str, object]], object] | None = None,
) -> dict[str, object]:
    """Populate approved platform drafts without moving/publishing them."""

    if platform not in {"tiktok", "shopee"}:
        raise MiaoshouCollectBoxPreparationError(
            "collect-box platform is unsupported", writes=(), write_count=0
        )
    if not isinstance(approved_plan_payload, Mapping):
        raise MiaoshouCollectBoxPreparationError(
            "approved plan payload is unavailable", writes=(), write_count=0
        )
    if type(approved_targets) is not tuple or any(
        type(value) is not str for value in approved_targets
    ):
        raise MiaoshouCollectBoxPreparationError(
            "approved target list is invalid", writes=(), write_count=0
        )
    selected = tuple(
        target
        for target in approved_targets
        if target.startswith(f"{platform}:")
    )
    if not selected or any(
        target not in DIRECT_STORE_CONFIG
        or DIRECT_STORE_CONFIG[target]["platform"] != platform
        for target in selected
    ):
        raise MiaoshouCollectBoxPreparationError(
            "approved platform targets are unavailable",
            writes=(),
            write_count=0,
        )
    try:
        common_id = _positive_digit(common_detail_id, "common_detail_id")
        primary_detail_id = int(
            _positive_digit(
                initial_platform_detail_id, "initial_platform_detail_id"
            )
        )
    except Exception as error:
        raise MiaoshouCollectBoxPreparationError(
            "platform collect-box identity is invalid",
            writes=(),
            write_count=0,
        ) from error

    client = post or _prepare_post()
    price_client = web_post
    writes: list[str] = []
    write_count_unknown = False
    write_invocation_count = 0
    if initial_claim_written:
        writes.append(f"miaoshou:collectbox:claim:{platform}")
        write_invocation_count = 1

    def add_write(write_class: str) -> None:
        nonlocal write_invocation_count
        write_invocation_count += 1
        if write_class not in writes:
            writes.append(write_class)

    def fail(detail: str, *, current_unknown: str | None = None) -> None:
        nonlocal write_count_unknown
        if current_unknown is not None:
            add_write(current_unknown)
            write_count_unknown = True
        raise MiaoshouCollectBoxPreparationError(
            detail,
            writes=tuple(writes),
            write_count=(
                None if write_count_unknown else write_invocation_count
            ),
        )

    def prepare_target(
        target: str, index: int
    ) -> tuple[int, dict[str, object]]:
        nonlocal write_count_unknown
        config = DIRECT_STORE_CONFIG[target]
        try:
            expected = _approved_site(
                approved_plan_payload,
                target=target,
                config=config,
                source_offer_id=common_id,
            )
            expected["common_detail_id"] = common_id
        except Exception:
            fail("approved platform draft is invalid")

        detail_id = primary_detail_id
        if platform == "tiktok" and index > 0:
            create_class = (
                f"miaoshou:collectbox:tiktok:detail:create:{target}"
            )
            try:
                created = client(
                    DETAIL_CREATE_PATH,
                    {
                        "detailSerialNumberPlatformList": [
                            {
                                "detailId": int(common_id),
                                "platform": platform,
                                "serialNumber": index + 1,
                            }
                        ]
                    },
                )
            except Exception:
                fail(
                    "TikTok platform-detail creation outcome is unknown",
                    current_unknown=create_class,
                )
            if not isinstance(created, Mapping):
                fail(
                    "TikTok platform-detail creation response is malformed",
                    current_unknown=create_class,
                )
            if not _success(created):
                fail("TikTok platform-detail creation was rejected")
            add_write(create_class)
            try:
                detail_id = _created_detail_id(
                    created, common_id, platform=platform
                )
            except Exception:
                fail("TikTok platform-detail identity is unavailable")

        if platform == "tiktok":
            claim_class = f"miaoshou:collectbox:tiktok:shop:claim:{target}"
            try:
                claimed = client(
                    SHOP_CLAIM_PATH,
                    {
                        "detailIds": [detail_id],
                        "shopIds": [str(config["shop_id"])],
                    },
                )
            except Exception:
                fail(
                    "TikTok shop-claim outcome is unknown",
                    current_unknown=claim_class,
                )
            if not isinstance(claimed, Mapping):
                fail(
                    "TikTok shop-claim response is malformed",
                    current_unknown=claim_class,
                )
            if not _accepted(claimed):
                fail("TikTok shop claim was rejected")
            add_write(claim_class)

        try:
            detail, oss_md5 = _read_shop(
                client,
                detail_id,
                str(config["shop_id"]),
                target=target,
            )
            _verify_shop_identity(
                detail,
                detail_id=detail_id,
                shop_id=str(config["shop_id"]),
            )
            _verify_site_variants(detail, expected)
            updated = _apply_expected_for_platform(
                detail, expected, platform=platform
            )
            body = _save_body(
                platform=platform,
                site=str(config["site"]),
                detail_id=detail_id,
                shop_id=str(config["shop_id"]),
                updated=updated,
                oss_md5=oss_md5,
            )
        except Exception:
            fail(f"{platform} draft preparation failed before update")

        update_class = (
            f"miaoshou:collectbox:{platform}:detail:update:{target}"
        )
        try:
            saved = client(str(config["save_path"]), body)
        except Exception:
            fail(
                f"{platform} draft update outcome is unknown",
                current_unknown=update_class,
            )
        if not isinstance(saved, Mapping):
            fail(
                f"{platform} draft update response is malformed",
                current_unknown=update_class,
            )
        if not _accepted(saved):
            fail(f"{platform} draft update was rejected")
        add_write(update_class)
        readback: Mapping[str, object] = {}
        readback_oss_md5 = ""
        readback_available = False
        try:
            readback, readback_oss_md5 = _read_shop(
                client,
                detail_id,
                str(config["shop_id"]),
                target=target,
            )
            readback_available = True
            _verify_expected_detail(
                readback,
                expected,
                platform=platform,
                strict_collectbox_tiktok=True,
            )
            return detail_id, _target_result(target, "SUCCEEDED")
        except Exception:
            if platform != "tiktok":
                fail(f"{platform} draft readback did not match approved plan")

        category_error = _tiktok_category_error_code(readback, expected)
        if category_error is not None:
            return detail_id, _target_result(
                target,
                "FAILED",
                error_code=category_error,
                detail=(
                    "approved site category evidence is unavailable"
                    if category_error == "category_not_approved"
                    else "official category readback differs from approved site category"
                ),
            )

        # Miaoshou's OpenAPI detail-save normalizes the local site price from
        # the common-box CNY origin price.  The web batch-price endpoint is
        # the authoritative persisted local-price operation used by the UI.
        repairable_price = readback_available and not _tiktok_prices_exact(
            readback, expected
        )
        if repairable_price:
            try:
                active_price_client = price_client or _prepare_web_price_post()
                repaired = active_price_client(
                    WEB_BATCH_SET_PRICE_PATH,
                    _tiktok_batch_price_body(
                        detail_id=detail_id,
                        site=str(config["site"]),
                        price=expected["price"],
                    ),
                )
            except Exception:
                add_write(
                    f"miaoshou:collectbox:tiktok:price:update:{target}"
                )
                write_count_unknown = True
                return detail_id, _target_result(
                    target,
                    "FAILED",
                    error_code="approved_price_batch_repair_unknown",
                    detail="Miaoshou batch price repair outcome is unknown",
                )
            if not isinstance(repaired, Mapping):
                add_write(
                    f"miaoshou:collectbox:tiktok:price:update:{target}"
                )
                write_count_unknown = True
                return detail_id, _target_result(
                    target,
                    "FAILED",
                    error_code="approved_price_batch_repair_unknown",
                    detail="Miaoshou batch price repair response is malformed",
                )
            if not _accepted(repaired):
                return detail_id, _target_result(
                    target,
                    "FAILED",
                    error_code="approved_price_batch_repair_rejected",
                    detail="Miaoshou batch price repair was rejected",
                )
            add_write(
                f"miaoshou:collectbox:tiktok:price:update:{target}"
            )
            repair_readback_available = False
            try:
                readback, _ = _read_shop(
                    client,
                    detail_id,
                    str(config["shop_id"]),
                    target=target,
                )
                repair_readback_available = True
                _verify_expected_detail(
                    readback,
                    expected,
                    platform=platform,
                    strict_collectbox_tiktok=True,
                )
                return detail_id, _target_result(
                    target, "REPAIRED_SUCCEEDED"
                )
            except Exception:
                pass
            if not repair_readback_available:
                return detail_id, _target_result(
                    target,
                    "FAILED",
                    error_code="official_readback_unavailable",
                    detail=(
                        "official target draft readback is unavailable after repair"
                    ),
                )

        if not readback_available:
            return detail_id, _target_result(
                target,
                "FAILED",
                error_code="official_readback_unavailable",
                detail="official target draft readback is unavailable",
            )

        if not _tiktok_prices_exact(readback, expected):
            return detail_id, _target_result(
                target,
                "FAILED",
                error_code="approved_price_readback_mismatch",
                detail="official price readback differs after one bounded repair",
            )
        return detail_id, _target_result(
            target,
            "FAILED",
            error_code="approved_detail_readback_mismatch",
            detail="official draft readback differs from approved target",
        )

    detail_ids: list[int] = []
    target_results: list[dict[str, object]] = []
    for index, target in enumerate(selected):
        try:
            detail_id, target_result = prepare_target(target, index)
            detail_ids.append(detail_id)
            target_results.append(target_result)
        except MiaoshouCollectBoxPreparationError as error:
            target_results.append(
                _target_result(
                    target,
                    "FAILED",
                    error_code="target_preparation_failed",
                    detail=str(error),
                )
            )
            continue

    if not detail_ids:
        raise MiaoshouCollectBoxPreparationError(
            "all approved platform drafts failed preparation",
            writes=tuple(writes),
            write_count=(
                None if write_count_unknown else write_invocation_count
            ),
            target_results=tuple(
                (str(row["target_label"]), "RECONCILIATION_REQUIRED")
                for row in target_results
            ),
        )

    return {
        "schema_version": "miaoshou-platform-collectbox-preparation/v1",
        "platform": platform,
        "primary_platform_detail_id": str(primary_detail_id),
        "target_count": len(selected),
        "platform_detail_count": len(set(detail_ids)),
        "external_writes": tuple(writes),
        "external_write_count": (
            None if write_count_unknown else write_invocation_count
        ),
        "target_results": target_results,
        "checks": {
            "approved_targets_exact": True,
            "approved_prices_exact": True,
            "approved_content_exact": True,
            "readback_exact": all(
                row["status"] in {"SUCCEEDED", "REPAIRED_SUCCEEDED"}
                for row in target_results
            ),
            "publish_not_invoked": True,
        },
    }


def _platform_write(platform: str, operation: str) -> str:
    if platform not in {"tiktok", "shopee", "ozon"}:
        raise MiaoshouOneClickPreDispatchError(
            "Miaoshou platform write class is invalid"
        )
    return f"miaoshou:{platform}_{operation}"


def _save_body(
    *,
    platform: str,
    site: str,
    detail_id: int,
    shop_id: str,
    updated: Mapping[str, object],
    oss_md5: str,
) -> dict[str, object]:
    if platform == "tiktok":
        return {
            "detailId": detail_id,
            "shopId": shop_id,
            "shopCollectItemInfo": dict(updated),
            "ossMd5": oss_md5,
        }
    if platform == "shopee":
        if not oss_md5:
            raise MiaoshouOneClickPreDispatchError(
                "Shopee site edit identity is incomplete"
            )
        return {
            "detailId": detail_id,
            "site": site,
            "ossMd5": oss_md5,
            "siteDetailSimpleData": dict(updated),
            "syncSites": [],
            "syncSiteFields": [],
            "shopIdAndSizeChartIdMap": {},
        }
    if platform == "ozon":
        return {
            "detailId": detail_id,
            "siteCollectItemInfo": dict(updated),
        }
    raise MiaoshouOneClickPreDispatchError(
        "Miaoshou platform save payload is invalid"
    )


def _apply_expected_for_platform(
    current: Mapping[str, object],
    expected: Mapping[str, object],
    *,
    platform: str,
) -> dict[str, object]:
    if platform in {"tiktok", "shopee"}:
        return _apply_expected(current, expected)
    if platform != "ozon":
        raise MiaoshouOneClickPreDispatchError(
            "Miaoshou platform detail is unsupported"
        )
    updated = dict(current)
    updated.update(
        {
            "title": expected["title"],
            "itemNum": expected["item_num"],
            "notes": expected["notes"],
            "mainImgVideoUrl": expected["video_url"],
            "packageInfoType": "ALL",
            "packageInfo": {
                "depth": float(str(expected["package_cm"][0])),
                "width": float(str(expected["package_cm"][1])),
                "height": float(str(expected["package_cm"][2])),
                "dimensionUnit": "CENTIMETER",
            },
            "weightInfo": {
                "weight": float(str(expected["weight"])),
                "weightUnit": "KILOGRAM",
            },
        }
    )
    current_skus = _sku_map(current)
    bindings = _approved_variant_key_bindings(current, expected)
    updated_skus: dict[str, object] = {}
    for variant in expected["selected_sku_keys"]:
        raw_key = bindings[variant]
        row = dict(_mapping(current_skus[raw_key], "sku row"))
        row.update(
            {
                "itemNum": expected["model_skus"][variant],
                "price": float(str(expected["price"])),
                "marketPrice": float(str(expected["price"])),
                "originPrice": float(str(expected["price"])),
                "imgUrls": list(expected["images"]),
                "packageInfo": dict(updated["packageInfo"]),
                "weightInfo": dict(updated["weightInfo"]),
            }
        )
        updated_skus[raw_key] = row
    updated["skuMap"] = updated_skus
    return updated


def _apply_expected(
    current: Mapping[str, object], expected: Mapping[str, object]
) -> dict[str, object]:
    updated = dict(current)
    updated.update(
        {
            "title": expected["title"],
            "itemNum": expected["item_num"],
            "weight": float(str(expected["weight"])),
            "packageLength": float(str(expected["package_cm"][0])),
            "packageWidth": float(str(expected["package_cm"][1])),
            "packageHeight": float(str(expected["package_cm"][2])),
            "imgUrls": list(expected["images"]),
            "notes": expected["notes"],
            "mainImgVideoUrl": expected["video_url"],
        }
    )
    if "simple_description" in expected:
        updated["notesText"] = expected["simple_description"]
    if type(expected.get("category_id")) is str and expected["category_id"]:
        updated["cid"] = expected["category_id"]
    current_skus = _sku_map(current)
    bindings = _approved_variant_key_bindings(current, expected)
    updated_skus: dict[str, object] = {}
    for variant in expected["selected_sku_keys"]:
        raw_key = bindings[variant]
        row = dict(_mapping(current_skus[raw_key], "sku row"))
        row["itemNum"] = expected["model_skus"][variant]
        row["weight"] = float(str(expected["weight"]))
        row["packageLength"] = float(str(expected["package_cm"][0]))
        row["packageWidth"] = float(str(expected["package_cm"][1]))
        row["packageHeight"] = float(str(expected["package_cm"][2]))
        if "price" in expected:
            row["price"] = float(str(expected["price"]))
            row["priceIncludeVat"] = float(str(expected["price"]))
        updated_skus[raw_key] = row
    updated["skuMap"] = updated_skus
    selected_raw_keys = set(bindings.values())
    for map_name in ("colorMap", "sizeMap", "saleProp3Map"):
        if isinstance(updated.get(map_name), Mapping):
            updated[map_name] = {
                key: value
                for key, value in updated[map_name].items()
                if key in selected_raw_keys
                or _normalize_variant(key)
                in set(expected["selected_sku_keys"])
            }
    return updated


def _verify_expected_detail(
    detail: Mapping[str, object],
    expected: Mapping[str, object],
    *,
    platform: str = "tiktok",
    strict_collectbox_tiktok: bool = False,
) -> None:
    sku_map = _sku_map(detail)
    bindings = _approved_variant_key_bindings(detail, expected)
    normalized = {
        variant: sku_map[raw_key]
        for variant, raw_key in bindings.items()
    }
    wanted = set(expected["selected_sku_keys"])
    if platform == "ozon":
        package = detail.get("packageInfo")
        weight = detail.get("weightInfo")
        package_values = (
            (
                package.get("depth"),
                package.get("width"),
                package.get("height"),
            )
            if isinstance(package, Mapping)
            else (None, None, None)
        )
        actual_weight = (
            weight.get("weight") if isinstance(weight, Mapping) else None
        )
    else:
        package_values = (
            detail.get("packageLength"),
            detail.get("packageWidth"),
            detail.get("packageHeight"),
        )
        actual_weight = detail.get("weight")
    checks = [
        str(detail.get("title") or "") == expected["title"],
        str(detail.get("itemNum") or "") == expected["item_num"],
        _numbers_equal(actual_weight, expected["weight"]),
        all(
            _numbers_equal(actual, wanted_value)
            for actual, wanted_value in zip(
                package_values,
                expected["package_cm"],
            )
        ),
        _normalize_notes(detail.get("notes"))
        == _normalize_notes(expected["notes"]),
        str(detail.get("mainImgVideoUrl") or "")
        == str(expected["video_url"]),
        set(normalized) == wanted,
        all(
            str(_mapping(normalized[key], "sku row").get("itemNum") or "")
            == expected["model_skus"][key]
            for key in wanted
        ),
    ]
    if platform == "shopee":
        checks.append(
            str(detail.get("notesText") or "")
            == str(expected.get("simple_description") or "")
        )
    if platform != "ozon":
        checks.append(
            list(detail.get("imgUrls") or []) == list(expected["images"])
        )
    else:
        checks.append(
            all(
                list(
                    _mapping(normalized[key], "sku row").get("imgUrls") or []
                )
                == list(expected["images"])
                for key in wanted
            )
        )
    if "price" in expected:
        checks.append(
            all(
                _numbers_equal(
                    _mapping(normalized[key], "sku row").get("price"),
                    expected["price"],
                )
                for key in wanted
            )
        )
        if platform == "tiktok" and strict_collectbox_tiktok:
            checks.append(
                all(
                    _numbers_equal(
                        _mapping(normalized[key], "sku row").get(
                            "priceIncludeVat"
                        ),
                        expected["price"],
                    )
                    for key in wanted
                )
            )
    if platform == "tiktok" and strict_collectbox_tiktok:
        expected_category = expected.get("category_id")
        checks.append(
            type(expected_category) is str
            and bool(expected_category)
            and str(detail.get("cid") or "") == expected_category
        )
    if not all(checks):
        raise MiaoshouOneClickPreDispatchError(
            "official Miaoshou readback does not match approved command"
        )


def _tiktok_prices_exact(
    detail: Mapping[str, object], expected: Mapping[str, object]
) -> bool:
    try:
        sku_map = _sku_map(detail)
        bindings = _approved_variant_key_bindings(detail, expected)
        rows = [sku_map[raw_key] for raw_key in bindings.values()]
        if (
            len(bindings) != len(expected["selected_sku_keys"])
            or any(not isinstance(row, Mapping) for row in rows)
        ):
            return False
        return all(
            _numbers_equal(
                row.get("priceIncludeVat"), expected["price"]
            )
            for row in rows
        )
    except (KeyError, TypeError, ValueError, MiaoshouOneClickPreDispatchError):
        return False


def _tiktok_category_error_code(
    detail: Mapping[str, object], expected: Mapping[str, object]
) -> str | None:
    category_id = expected.get("category_id")
    if type(category_id) is not str or not category_id:
        return "category_not_approved"
    if str(detail.get("cid") or "") != category_id:
        return "category_readback_mismatch"
    return None


def _tiktok_batch_price_body(
    *, detail_id: int, site: str, price: object
) -> dict[str, object]:
    approved_price = _positive_decimal(price, "approved TikTok price")
    integral_price = approved_price.to_integral_value()
    if approved_price != integral_price:
        raise MiaoshouOneClickPreDispatchError(
            "approved TikTok price must be an integer for batch repair"
        )
    return {
        "collectBoxDetailIds": [detail_id],
        "site": site,
        "priceConfig": {
            "price": {
                "modifyMode": "newValue",
                "newValue": int(integral_price),
            }
        },
    }


def _target_result(
    target: str,
    status: str,
    *,
    error_code: str | None = None,
    detail: str | None = None,
) -> dict[str, object]:
    if status not in {"SUCCEEDED", "REPAIRED_SUCCEEDED", "FAILED"}:
        raise ValueError("collect-box target result status is invalid")
    if status == "FAILED":
        if type(error_code) is not str or not error_code or not detail:
            raise ValueError("failed collect-box target result is incomplete")
        detail_digest = _digest(
            {
                "schema_version": "miaoshou-target-error/v1",
                "target_label": target,
                "error_code": error_code,
                "detail": detail,
            }
        )
    else:
        if error_code is not None or detail is not None:
            raise ValueError("successful collect-box target result has error")
        detail_digest = None
    return {
        "target_label": target,
        "status": status,
        "error_code": error_code,
        "detail_digest": detail_digest,
    }


def _default_tiktok_readback(expected: Mapping[str, object]) -> bool:
    """Official API readback with no local-catalog mutation."""
    from core import auth, shops
    from core.api_client import get as tiktok_get
    from core.api_client import post as tiktok_post

    token = auth.access_token()
    region = str(expected["region"])
    configured_shop_id = str(expected["shop_id"])
    configured_shop_name = str(expected["shop_name"])
    shop_rows = shops.list_shops(token)
    if not isinstance(shop_rows, list) or any(
        not isinstance(row, Mapping) for row in shop_rows
    ):
        return False
    candidates = []
    for row in shop_rows:
        observed_id = str(row.get("id") or row.get("shop_id") or "")
        observed_region = str(
            row.get("region") or row.get("region_code") or ""
        ).upper()
        observed_name = str(
            row.get("name") or row.get("shop_name") or ""
        )
        if (
            observed_id == configured_shop_id
            and observed_region == region
            and observed_name == configured_shop_name
        ):
            candidates.append(row)
    if len(candidates) != 1:
        return False
    shop = candidates[0]
    cipher = str(shop.get("cipher") or shop.get("shop_cipher") or "")
    if not cipher:
        return False
    model_skus = sorted(set(expected["model_skus"].values()))
    products: list[Mapping[str, object]] = []
    seen_tokens: set[str] = set()
    seen_products: set[str] = set()
    page_token = ""
    declared_total: int | None = None
    for _ in range(20):
        if page_token in seen_tokens:
            return False
        seen_tokens.add(page_token)
        params = {"shop_cipher": cipher, "page_size": 100}
        if page_token:
            params["page_token"] = page_token
        search = tiktok_post(
            "/product/202309/products/search",
            token,
            params,
            {"status": "ACTIVATE", "seller_skus": model_skus},
        )
        if not isinstance(search, Mapping) or search.get("code") != 0:
            return False
        data = search.get("data")
        page_rows = (
            data.get("products")
            if isinstance(data, Mapping)
            else None
        )
        total = data.get("total_count") if isinstance(data, Mapping) else None
        next_token = (
            data.get("next_page_token")
            if isinstance(data, Mapping)
            else None
        )
        if (
            not isinstance(page_rows, list)
            or any(not isinstance(row, Mapping) for row in page_rows)
            or type(total) is not int
            or total < 0
            or type(next_token) is not str
            or (declared_total is not None and total != declared_total)
        ):
            return False
        declared_total = total
        for row in page_rows:
            product_identity = str(
                row.get("id") or row.get("product_id") or ""
            )
            if not product_identity or product_identity in seen_products:
                return False
            seen_products.add(product_identity)
            products.append(row)
        if not next_token:
            if len(products) != declared_total:
                return False
            break
        page_token = next_token
    else:
        return False
    exact = []
    for product in products:
        if not isinstance(product, Mapping):
            return False
        skus = product.get("skus")
        if not isinstance(skus, list) or any(
            not isinstance(row, Mapping) for row in skus
        ):
            return False
        if {str(row.get("seller_sku") or "") for row in skus} == set(
            model_skus
        ):
            exact.append(product)
    if len(exact) != 1:
        return False
    product_id = str(
        exact[0].get("id") or exact[0].get("product_id") or ""
    )
    if not product_id:
        return False
    response = tiktok_get(
        f"/product/202309/products/{product_id}",
        token,
        {"shop_cipher": cipher},
    )
    detail = response.get("data") if isinstance(response, Mapping) else None
    if response.get("code") != 0 or not isinstance(detail, Mapping):
        return False
    skus = detail.get("skus")
    images = detail.get("main_images") or detail.get("images")
    if (
        not isinstance(skus, list)
        or any(not isinstance(row, Mapping) for row in skus)
        or not isinstance(images, list)
        or any(not isinstance(row, Mapping) for row in images)
    ):
        return False
    prices_ok = all(
        _numbers_equal(
            (_mapping(row.get("price"), "price").get("sale_price")),
            expected["price"],
        )
        for row in skus
    )
    parcel_exact = _official_tiktok_parcel_exact(detail, expected)
    image_shape_exact = all(
        type(row.get("uri") or row.get("url")) is str
        and bool(row.get("uri") or row.get("url"))
        for row in images
    )
    return bool(
        str(detail.get("title") or "") == expected["title"]
        and {str(row.get("seller_sku") or "") for row in skus}
        == set(model_skus)
        and prices_ok
        and parcel_exact
        and image_shape_exact
        and len(images) == len(expected["images"])
        and str(
            detail.get("status") or detail.get("product_status") or ""
        ).upper()
        in {"ACTIVATE", "LIVE"}
    )


def _official_tiktok_parcel_exact(
    detail: Mapping[str, object], expected: Mapping[str, object]
) -> bool:
    weight = detail.get("package_weight", detail.get("weight"))
    dimensions = detail.get(
        "package_dimensions", detail.get("dimensions")
    )
    if not isinstance(weight, Mapping) or not isinstance(
        dimensions, Mapping
    ):
        return False
    weight_unit = str(weight.get("unit") or "").upper()
    dimension_unit = str(dimensions.get("unit") or "").upper()
    if weight_unit not in {"KG", "KILOGRAM"} or dimension_unit not in {
        "CM",
        "CENTIMETER",
    }:
        return False
    weight_value = weight.get("value")
    if not _numbers_equal(weight_value, expected["weight"]):
        return False
    observed_dimensions = []
    for field in ("length", "width", "height"):
        value = dimensions.get(field)
        if isinstance(value, Mapping):
            value = value.get("value")
        observed_dimensions.append(value)
    return all(
        _numbers_equal(observed, approved)
        for observed, approved in zip(
            observed_dimensions, expected["package_cm"]
        )
    )


def _prepare_post() -> Callable[[str, Mapping[str, object]], object]:
    if _prepare_post_factory is not None:
        callback = _prepare_post_factory()
        if callable(callback):
            return callback
    from modules.miaoshou.client import post_open

    return post_open


def _prepare_web_price_post() -> Callable[[str, Mapping[str, object]], object]:
    from modules.miaoshou.client import (
        ensure_web_batch_price_auth_available,
        web_batch_set_tiktok_price,
    )

    ensure_web_batch_price_auth_available()

    def post_price(path: str, body: Mapping[str, object]) -> object:
        if path != WEB_BATCH_SET_PRICE_PATH:
            raise MiaoshouOneClickPreDispatchError(
                "unsupported Miaoshou web price operation"
            )
        return web_batch_set_tiktok_price(dict(body))

    return post_price


def _runtime_transport() -> MiaoshouRuntimeTransport:
    if _runtime_transport_factory is not None:
        value = _runtime_transport_factory()
        if isinstance(value, MiaoshouRuntimeTransport):
            return value
    from modules.miaoshou.client import post_open

    return MiaoshouRuntimeTransport(post=post_open)


def _required_post(
    transport: MiaoshouRuntimeTransport,
) -> Callable[[str, Mapping[str, object]], object]:
    if transport.post is not None:
        return transport.post

    def legacy_post(path: str, body: Mapping[str, object]) -> object:
        if path == SHOP_SAVE_PATH and transport.update_detail is not None:
            return transport.update_detail(body)
        if path == PUBLISH_PATH and transport.publish is not None:
            return transport.publish(
                str(body["detailIds"][0]), str(body["shopIds"][0])
            )
        raise MiaoshouOneClickPreDispatchError(
            "injected Miaoshou transport has no generic client"
        )

    return legacy_post


def _read_common(post, common_detail_id: str) -> tuple[dict[str, object], str]:
    response = post(
        COMMON_GET_PATH,
        {"commonCollectBoxDetailId": int(common_detail_id)},
    )
    if not _success(response):
        raise MiaoshouOneClickPreDispatchError("COMMON read-only GET failed")
    data = response.get("data")
    detail = (
        data.get("editCommonCollectBoxDetail")
        if isinstance(data, Mapping)
        else None
    )
    oss_md5 = str(data.get("ossMd5") or "") if isinstance(data, Mapping) else ""
    if not isinstance(detail, Mapping) or not detail or not oss_md5:
        raise MiaoshouOneClickPreDispatchError(
            "COMMON read-only response is malformed"
        )
    return dict(detail), oss_md5


def _read_shop(
    post,
    detail_id: int,
    shop_id: str,
    *,
    target: str | None = None,
) -> tuple[dict[str, object], str]:
    config = DIRECT_STORE_CONFIG.get(target) if target is not None else None
    platform = (
        str(config["platform"]) if isinstance(config, Mapping) else "tiktok"
    )
    get_path = (
        str(config["get_path"])
        if isinstance(config, Mapping)
        else SHOP_GET_PATH
    )
    if platform == "tiktok":
        body = {"detailId": int(detail_id), "shopId": str(shop_id)}
        data_field = "shopCollectItemInfo"
    elif platform == "shopee":
        body = {"detailId": int(detail_id), "site": str(config["site"])}
        data_field = "siteDetailSimpleData"
    else:
        body = {"detailId": int(detail_id)}
        data_field = "siteCollectItemInfo"
    response = post(
        get_path,
        body,
    )
    if not _success(response):
        raise MiaoshouOneClickPreDispatchError(
            f"Miaoshou {platform} detail read-only GET failed"
        )
    data = response.get("data")
    detail = (
        data.get(data_field)
        if isinstance(data, Mapping)
        else None
    )
    oss_md5 = str(data.get("ossMd5") or "") if isinstance(data, Mapping) else ""
    # Ozon's documented site-detail response does not require an ossMd5.
    if (
        not isinstance(detail, Mapping)
        or not detail
        or (platform != "ozon" and not oss_md5)
    ):
        raise MiaoshouOneClickPreDispatchError(
            f"Miaoshou {platform} detail response is malformed"
        )
    return dict(detail), oss_md5


def _resolve_detail_from_pages(
    pages: tuple[dict[str, object], ...],
    *,
    common_detail_id: str,
    shop_id: str,
    target: str | None = None,
) -> int | None:
    config = DIRECT_STORE_CONFIG.get(target) if target is not None else None
    expected_site = (
        str(config["site"]) if isinstance(config, Mapping) else None
    )
    matched: set[int] = set()
    for page in pages:
        rows = page["data"]["detailList"]
        for row in rows:
            raw_detail = row.get("collectBoxDetailId") or row.get("detailId")
            if (
                isinstance(raw_detail, bool)
                or not str(raw_detail or "").isdigit()
                or int(raw_detail) <= 0
            ):
                raise MiaoshouOneClickPreDispatchError(
                    "source detail identity is malformed"
                )
            row_common = str(row.get("commonCollectBoxDetailId") or "")
            if not row_common.isdigit():
                raise MiaoshouOneClickPreDispatchError(
                    "source COMMON identity is malformed"
                )
            shops = row.get("collectBoxDetailShopList")
            site = str(row.get("site") or row.get("region") or "").upper()
            if shops is None and expected_site is not None:
                row_matches_target = site in {"", expected_site.upper()}
            else:
                if not isinstance(shops, list) or any(
                    not isinstance(item, Mapping) for item in shops
                ):
                    raise MiaoshouOneClickPreDispatchError(
                        "source shop identity list is malformed"
                    )
                shop_ids = [str(item.get("shopId") or "") for item in shops]
                if any(not value.isdigit() for value in shop_ids) or len(
                    shop_ids
                ) != len(set(shop_ids)):
                    raise MiaoshouOneClickPreDispatchError(
                        "source shop identity list is malformed"
                    )
                row_matches_target = shop_id in shop_ids
            if row_common == common_detail_id and row_matches_target:
                matched.add(int(raw_detail))
    if len(matched) > 1:
        raise MiaoshouOneClickPreDispatchError(
            "source detail identity is ambiguous"
        )
    return next(iter(matched)) if matched else None


def _resolve_common_detail_id_from_pages(
    pages: tuple[dict[str, object], ...],
    *,
    source_offer_id: str,
) -> str:
    """Resolve Miaoshou COMMON identity from the exact source-offer result.

    ``source_offer_id`` is the upstream product identity used only by the
    exact search filter.  ``commonCollectBoxDetailId`` is Miaoshou's internal
    identity and must be observed from that response; the two namespaces are
    never interchangeable.
    """

    observed: set[str] = set()
    for page in pages:
        rows = page["data"]["detailList"]
        for row in rows:
            raw_common = row.get("commonCollectBoxDetailId")
            if (
                isinstance(raw_common, bool)
                or not str(raw_common or "").isdigit()
                or int(raw_common) <= 0
            ):
                raise MiaoshouOneClickPreDispatchError(
                    "source COMMON identity is malformed"
                )

            source_values: list[object] = []
            for field in (
                "sourceOfferId",
                "sourceItemId",
                "sourceProductId",
            ):
                if field in row and row.get(field) is not None:
                    source_values.append(row.get(field))
            source_list = row.get("sourceList")
            if source_list is not None:
                if not isinstance(source_list, list) or any(
                    not isinstance(item, Mapping) for item in source_list
                ):
                    raise MiaoshouOneClickPreDispatchError(
                        "canonical source identity evidence is malformed"
                    )
                for item in source_list:
                    for field in (
                        "sourceOfferId",
                        "sourceItemId",
                        "sourceProductId",
                    ):
                        if field in item and item.get(field) is not None:
                            source_values.append(item.get(field))

            canonical_sources: set[str] = set()
            for value in source_values:
                if (
                    isinstance(value, bool)
                    or not str(value or "").isdigit()
                    or int(value) <= 0
                ):
                    raise MiaoshouOneClickPreDispatchError(
                        "canonical source identity evidence is malformed"
                    )
                canonical_sources.add(str(int(value)))
            if canonical_sources and source_offer_id not in canonical_sources:
                raise MiaoshouOneClickPreDispatchError(
                    "canonical source offer identity drifted"
                )
            observed.add(str(int(raw_common)))

    if len(observed) != 1:
        raise MiaoshouOneClickPreDispatchError(
            "source COMMON identity is unavailable or ambiguous"
        )
    return next(iter(observed))


def _verify_common_identity(
    detail: Mapping[str, object],
    *,
    common_detail_id: str,
    source_offer_id: str,
) -> None:
    observed_common = (
        detail.get("commonCollectBoxDetailId")
        or detail.get("commonCollectBoxId")
        or detail.get("id")
    )
    observed_source = (
        detail.get("sourceOfferId")
        or detail.get("offerId")
        or detail.get("sourceProductId")
    )
    if observed_common is not None and str(observed_common) != str(
        common_detail_id
    ):
        raise MiaoshouOneClickPreDispatchError("COMMON identity drifted")
    if observed_source is not None and str(observed_source) != source_offer_id:
        raise MiaoshouOneClickPreDispatchError(
            "canonical source offer identity drifted"
        )


def _verify_shop_identity(
    detail: Mapping[str, object], *, detail_id: int, shop_id: str
) -> None:
    observed_detail = detail.get("detailId") or detail.get(
        "collectBoxDetailId"
    )
    observed_shop = detail.get("shopId")
    if observed_detail is not None and str(observed_detail) != str(detail_id):
        raise MiaoshouOneClickPreDispatchError(
            "TikTok detail identity drifted"
        )
    if observed_shop is not None and str(observed_shop) != str(shop_id):
        raise MiaoshouOneClickPreDispatchError("TikTok shop identity drifted")


def _verify_site_variants(
    detail: Mapping[str, object], expected: Mapping[str, object]
) -> None:
    _approved_variant_key_bindings(detail, expected)


def _approved_variant_key_bindings(
    detail: Mapping[str, object], expected: Mapping[str, object]
) -> dict[str, object]:
    """Bind Miaoshou's raw SKU-map keys to approved variants exactly.

    Some claimed TikTok drafts replace the human-readable variant key with an
    opaque Miaoshou key.  The model SKU survives that transformation.  Exact
    raw-key matching remains preferred; otherwise every observed and approved
    model SKU must be a unique built-in string and the two sets must match.
    No title, position, or fuzzy matching is permitted.
    """

    sku_map = _sku_map(detail)
    variants = expected.get("selected_sku_keys")
    model_skus = expected.get("model_skus")
    if (
        not isinstance(variants, list)
        or not variants
        or any(type(value) is not str or not value for value in variants)
        or len(variants) != len(set(variants))
        or not isinstance(model_skus, Mapping)
        or set(model_skus) != set(variants)
    ):
        raise MiaoshouOneClickPreDispatchError(
            "approved variant identity does not match the draft"
        )

    raw_by_normalized: dict[str, object] = {}
    for raw_key in sku_map:
        normalized = _normalize_variant(raw_key)
        if not normalized or normalized in raw_by_normalized:
            raise MiaoshouOneClickPreDispatchError(
                "Miaoshou variant identity is ambiguous"
            )
        raw_by_normalized[normalized] = raw_key
    if set(raw_by_normalized) == set(variants):
        return {
            variant: raw_by_normalized[variant]
            for variant in variants
        }

    expected_by_model: dict[str, str] = {}
    for variant in variants:
        model_sku = model_skus.get(variant)
        if (
            type(model_sku) is not str
            or not model_sku
            or model_sku != model_sku.strip()
            or model_sku in expected_by_model
        ):
            raise MiaoshouOneClickPreDispatchError(
                "approved model SKU identity is ambiguous"
            )
        expected_by_model[model_sku] = variant

    observed_by_model: dict[str, object] = {}
    for raw_key, row in sku_map.items():
        model_sku = row.get("itemNum")
        if (
            type(model_sku) is not str
            or not model_sku
            or model_sku != model_sku.strip()
            or model_sku in observed_by_model
        ):
            raise MiaoshouOneClickPreDispatchError(
                "Miaoshou model SKU identity is ambiguous"
            )
        observed_by_model[model_sku] = raw_key
    if set(observed_by_model) != set(expected_by_model):
        raise MiaoshouOneClickPreDispatchError(
            "TikTok model SKU identity does not match approved plan"
        )
    return {
        variant: observed_by_model[model_sku]
        for model_sku, variant in expected_by_model.items()
    }


def _created_detail_id(
    response: Mapping[str, object],
    common_id: str,
    *,
    platform: str = "tiktok",
) -> int:
    data = response.get("data")
    root = (
        data.get("platformCollectBoxDetailIdMap")
        if isinstance(data, Mapping)
        else None
    )
    mapping = root.get(platform) if isinstance(root, Mapping) else None
    raw = None
    if isinstance(mapping, Mapping):
        raw = mapping.get(common_id)
        if raw is None and common_id.isdigit():
            raw = mapping.get(int(common_id))
    if isinstance(raw, bool) or not str(raw or "").isdigit() or int(raw) <= 0:
        raise MiaoshouOneClickDispatchError(
            f"Miaoshou {platform} detail creation returned no identity",
            writes=(_platform_write(platform, "detail:create"),),
            unknown=False,
        )
    return int(raw)


def _provider_command(request) -> Mapping[str, Any]:
    command = getattr(request, "command", None)
    payload = command.get("payload") if isinstance(command, Mapping) else None
    provider = (
        payload.get("provider_command") if isinstance(payload, Mapping) else None
    )
    if not isinstance(provider, Mapping):
        raise MiaoshouOneClickPreDispatchError(
            "stored Miaoshou command is invalid"
        )
    try:
        restored = json.loads(
            json.dumps(provider, ensure_ascii=False, sort_keys=True)
        )
    except (TypeError, ValueError) as error:
        raise MiaoshouOneClickPreDispatchError(
            "stored Miaoshou command is not JSON-safe"
        ) from error
    if not isinstance(restored, Mapping):
        raise MiaoshouOneClickPreDispatchError(
            "stored Miaoshou command is invalid"
        )
    return restored


def _occurrence_evidence(
    occurrence_id: str,
    *,
    external_id: str | None = None,
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "schema_version": "oneclick-channel-write-occurrence/v1",
        "occurrence_id": occurrence_id,
    }
    if external_id:
        evidence["external_identity_digest"] = _text_digest(external_id)
    return evidence


def _open_write(
    request: object,
    state: WriteOccurrenceState,
    occurrence_id: str,
    write_class: str,
    *,
    external_id: str | None = None,
) -> OpenWriteOccurrence:
    try:
        return state.open(
            request,
            occurrence_id=occurrence_id,
            write_class=write_class,
            evidence=_occurrence_evidence(
                occurrence_id, external_id=external_id
            ),
        )
    except WriteOccurrenceRecordingError as error:
        if error.external_write_count == 0:
            raise MiaoshouOneClickPreDispatchError(
                "mandatory durable write intent could not be opened"
            ) from error
        raise _occurrence_recording_dispatch_error(
            error, external_id=external_id
        ) from error


def _confirm_write(
    request: object,
    state: WriteOccurrenceState,
    occurrence: OpenWriteOccurrence,
    *,
    external_id: str | None = None,
) -> None:
    try:
        state.confirm(
            request,
            occurrence,
            evidence=_occurrence_evidence(
                occurrence.occurrence_id,
                external_id=external_id,
            ),
        )
    except WriteOccurrenceRecordingError as error:
        raise _occurrence_recording_dispatch_error(
            error, external_id=external_id
        ) from error


def _reject_write(
    request: object,
    state: WriteOccurrenceState,
    occurrence: OpenWriteOccurrence,
    *,
    external_id: str | None = None,
) -> None:
    try:
        state.reject(
            request,
            occurrence,
            evidence=_occurrence_evidence(
                occurrence.occurrence_id,
                external_id=external_id,
            ),
        )
    except WriteOccurrenceRecordingError as error:
        if error.external_write_count == 0:
            raise MiaoshouOneClickPreDispatchError(
                "official API rejected the first write without mutation"
            ) from error
        raise _occurrence_recording_dispatch_error(
            error, external_id=external_id
        ) from error


def _unknown_write_error(
    state: WriteOccurrenceState,
    occurrence: OpenWriteOccurrence,
    detail: str,
    *,
    external_id: str | None = None,
) -> MiaoshouOneClickDispatchError:
    writes, exact, lower, upper = state.unknown_bounds(occurrence)
    return MiaoshouOneClickDispatchError(
        detail,
        writes=writes,
        unknown=True,
        external_id=external_id,
        external_write_count=exact,
        confirmed_lower_bound=lower,
        possible_upper_bound=upper,
    )


def _rejected_write_error(
    state: WriteOccurrenceState,
    detail: str,
    *,
    external_id: str | None = None,
) -> Exception:
    if state.external_write_count == 0:
        return MiaoshouOneClickPreDispatchError(detail)
    return MiaoshouOneClickDispatchError(
        detail,
        writes=state.external_writes,
        unknown=False,
        external_id=external_id,
        external_write_count=state.external_write_count,
        confirmed_lower_bound=state.external_write_count,
        possible_upper_bound=state.external_write_count,
    )


def _occurrence_recording_dispatch_error(
    error: WriteOccurrenceRecordingError,
    *,
    external_id: str | None,
) -> MiaoshouOneClickDispatchError:
    return MiaoshouOneClickDispatchError(
        str(error),
        writes=error.external_writes,
        unknown=error.external_write_count is None,
        external_id=external_id,
        external_write_count=error.external_write_count,
        confirmed_lower_bound=error.confirmed_lower_bound,
        possible_upper_bound=error.possible_upper_bound,
    )


def _receipt(
    status: str,
    external_id: str,
    writes: tuple[str, ...],
    verified: bool,
    code: str,
    *,
    write_count: int | None = None,
) -> dict[str, object]:
    exact_count = len(writes) if write_count is None else write_count
    return {
        "canonical_status": status,
        "reason_category": "POST_WRITE",
        "reason_scope": "TARGET",
        "reason_code": code,
        "reason_detail": "single target receipt recorded",
        "external_writes": writes,
        "external_write_count": exact_count,
        "confirmed_external_write_count_lower_bound": exact_count,
        "possible_external_write_count_upper_bound": exact_count,
        "external_id": external_id,
        "submission_accepted": True,
        "readback_verified": verified,
        "dispatch_outcome_unknown": False,
        "evidence": {
            "schema_version": "miaoshou-oneclick-redacted-evidence/v1",
            "external_writes_performed": list(writes),
            "manual_acceptance_required": status == "SUBMITTED_UNVERIFIED",
            "write_count": exact_count,
        },
    }


def _detail_snapshot(detail: Mapping[str, object]) -> dict[str, object]:
    return {
        "detail_id": detail.get("detailId")
        or detail.get("collectBoxDetailId")
        or detail.get("commonCollectBoxDetailId"),
        "shop_id": detail.get("shopId"),
        "source_offer_id": detail.get("sourceOfferId")
        or detail.get("offerId")
        or detail.get("sourceProductId"),
        "title": detail.get("title"),
        "item_num": detail.get("itemNum"),
        "weight": detail.get("weight"),
        "package": [
            detail.get("packageLength"),
            detail.get("packageWidth"),
            detail.get("packageHeight"),
        ],
        "images": list(detail.get("imgUrls") or ()),
        "notes": _normalize_notes(detail.get("notes")),
        "video": detail.get("mainImgVideoUrl"),
        "sku_map": detail.get("skuMap"),
    }


def _candidate_title(payload: Mapping[str, object], target: str) -> str:
    site = target.split(":", 1)[1]
    config = SITE_CONFIG[target]
    region = str(config["region"])
    channel = str(config["platform"])
    candidates = _mapping(
        payload.get("listing_copy"), "listing_copy"
    ).get("candidates")
    if not isinstance(candidates, list) or any(
        not isinstance(row, Mapping) for row in candidates
    ):
        raise MiaoshouOneClickPrepareBlocked(
            "approved_storefront_title_missing",
            "approved storefront title candidates are unavailable",
        )
    exact = [
        row
        for row in candidates
        if str(row.get("channel") or "").casefold() == channel
        and str(row.get("site") or "").upper() in {site, region}
        and row.get("policy_check") == "passed"
        and type(row.get("title")) is str
        and row["title"].strip()
    ]
    # The approved listing contract intentionally carries one Shopee CNSC
    # master title.  Regional Miaoshou details all inherit that exact approved
    # title; they do not require four duplicated title approvals.
    if not exact and channel == "shopee":
        exact = [
            row
            for row in candidates
            if str(row.get("channel") or "").casefold() == "shopee"
            and str(row.get("site") or "").upper() == "CNSC"
            and row.get("policy_check") == "passed"
            and type(row.get("title")) is str
            and row["title"].strip()
        ]
    if len(exact) != 1:
        raise MiaoshouOneClickPrepareBlocked(
            "approved_storefront_title_not_unique",
            "exactly one approved storefront title is required",
        )
    return exact[0]["title"].strip()


def _price(
    payload: Mapping[str, object], target: str, target_key: str
) -> tuple[str, str]:
    pricing = _mapping(payload.get("pricing"), "pricing")
    selected = _mapping(pricing.get("selected_targets"), "selected pricing")
    row = _mapping(selected.get(target), "target pricing")
    store_prices = row.get("store_prices")
    if store_prices is not None:
        if (
            not isinstance(store_prices, list)
            or len(store_prices) != 1
            or not isinstance(store_prices[0], Mapping)
            or str(store_prices[0].get("target_key") or "") != target_key
        ):
            raise MiaoshouOneClickPrepareBlocked(
                "approved_store_price_not_unique",
                "exactly one approved target price is required",
            )
        price = _positive_decimal(
            store_prices[0].get("list_price"), "approved list price"
        )
        currency = _text(
            store_prices[0].get("currency"), "approved currency"
        )
        config = DIRECT_STORE_CONFIG.get(target)
        expected_currency = {
            "MX": "MXN",
            "GB": "GBP",
            "PH": "PHP",
            "MY": "MYR",
            "TH": "THB",
            "VN": "VND",
            "OZON": "RUB",
        }.get(str(config.get("region"))) if isinstance(config, Mapping) else None
        if currency != expected_currency:
            raise MiaoshouOneClickPrepareBlocked(
                "approved_store_currency_mismatch",
                "approved target currency does not match the storefront",
            )
        return str(price), currency

    derived = row.get("derived_preview")
    expected_site = target.split(":", 1)[1]
    if (
        not isinstance(derived, Mapping)
        or type(row.get("selected_source_target_key")) is not str
        or not row["selected_source_target_key"].strip()
        or row.get("target_site") != expected_site
    ):
        raise MiaoshouOneClickPrepareBlocked(
            "approved_store_price_not_unique",
            "exactly one approved target price is required",
        )
    if target.startswith("shopee:"):
        price_field = "local_original_price"
        currency = {
            "PH": "PHP",
            "MY": "MYR",
            "TH": "THB",
            "VN": "VND",
        }.get(expected_site)
    elif target == "ozon:RU":
        price_field = "price_cny"
        currency = "CNY"
    else:
        price_field = ""
        currency = None
    if type(currency) is not str:
        raise MiaoshouOneClickPrepareBlocked(
            "approved_store_price_not_unique",
            "exactly one approved target price is required",
        )
    price = _positive_decimal(
        derived.get(price_field), "approved derived target price"
    )
    return str(price), currency


def _model_skus(
    payload: Mapping[str, object],
    variants: list[str],
    seller_sku: str,
) -> dict[str, str]:
    lineage = payload.get("sku_lineage")
    assignment = (
        lineage.get("assignment") if isinstance(lineage, Mapping) else None
    )
    rows = (
        assignment.get("model_skus")
        if isinstance(assignment, Mapping)
        else None
    )
    if rows is None and len(variants) == 1:
        return {variants[0]: seller_sku}
    if not isinstance(rows, list) or any(
        not isinstance(row, Mapping) for row in rows
    ):
        raise MiaoshouOneClickPrepareBlocked(
            "approved_model_sku_lineage_missing",
            "approved model SKU lineage is unavailable",
        )
    result: dict[str, str] = {}
    for row in rows:
        variant = _normalize_variant(row.get("variant_key"))
        model = _text(row.get("model_sku"), "model_sku")
        if variant in result:
            raise MiaoshouOneClickPrepareBlocked(
                "approved_model_sku_lineage_ambiguous",
                "approved model SKU lineage is ambiguous",
            )
        result[variant] = model
    if set(result) != set(variants) or len(set(result.values())) != len(result):
        raise MiaoshouOneClickPrepareBlocked(
            "approved_model_sku_lineage_mismatch",
            "approved model SKU lineage does not match selected variants",
        )
    return result


def _selected_variants(facts: Mapping[str, object]) -> list[str]:
    values = facts.get("selected_sku_keys")
    if not isinstance(values, list) or not values:
        raise MiaoshouOneClickPrepareBlocked(
            "approved_variant_identity_missing",
            "approved selected variants are unavailable",
        )
    result = [_normalize_variant(value) for value in values]
    if any(not value for value in result) or len(result) != len(set(result)):
        raise MiaoshouOneClickPrepareBlocked(
            "approved_variant_identity_invalid",
            "approved selected variants are invalid",
        )
    return result


def _parcel(facts: Mapping[str, object]) -> tuple[str, list[str]]:
    weight = _positive_decimal(facts.get("weight_kg"), "weight")
    package = facts.get("package_cm")
    if not isinstance(package, list) or len(package) != 3:
        raise MiaoshouOneClickPrepareBlocked(
            "approved_parcel_missing", "approved parcel is unavailable"
        )
    return str(weight), [
        str(_positive_decimal(value, "package dimension")) for value in package
    ]


def _images(payload: Mapping[str, object]) -> list[str]:
    rows = payload.get("images")
    if not isinstance(rows, list) or not rows or any(
        not isinstance(row, Mapping) for row in rows
    ):
        raise MiaoshouOneClickPrepareBlocked(
            "approved_images_missing", "approved ordered images are unavailable"
        )
    urls = [row.get("image_url") for row in rows]
    if any(
        type(url) is not str or not url.startswith("https://") for url in urls
    ) or len(urls) != len(set(urls)):
        raise MiaoshouOneClickPrepareBlocked(
            "approved_images_invalid", "approved ordered images are invalid"
        )
    return list(urls)


def _video(payload: Mapping[str, object]) -> str:
    values = payload.get("video_urls")
    if values is None:
        return ""
    if not isinstance(values, list) or any(
        type(value) is not str
        or (value and not value.startswith("https://"))
        for value in values
    ):
        raise MiaoshouOneClickPrepareBlocked(
            "approved_video_invalid", "approved video decision is invalid"
        )
    return values[0] if values else ""


def _description(payload: Mapping[str, object]) -> str:
    listing = _mapping(payload.get("listing_copy"), "listing_copy")
    value = listing.get("shopee_description_en")
    if value is None:
        return ""
    if type(value) is not str:
        raise MiaoshouOneClickPrepareBlocked(
            "approved_description_invalid",
            "approved description is invalid",
        )
    return value


def _notes(description: str, images: list[str]) -> str:
    body = (
        f"<p>{description.replace(chr(13) + chr(10), chr(10)).replace(chr(10), '<br>')}</p>"
        if description.strip()
        else ""
    )
    return body + "".join(f'<p><img src="{url}"></p>' for url in images)


def _sku_map(detail: Mapping[str, object]) -> Mapping[str, object]:
    value = detail.get("skuMap")
    if not isinstance(value, Mapping) or not value or any(
        not isinstance(row, Mapping) for row in value.values()
    ):
        raise MiaoshouOneClickPreDispatchError(
            "Miaoshou SKU map is malformed"
        )
    return value


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MiaoshouOneClickPrepareBlocked(
            "approved_content_shape_invalid", f"{name} is invalid"
        )
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise MiaoshouOneClickPrepareBlocked(
            "approved_content_field_missing", f"{name} is unavailable"
        )
    return value.strip()


def _positive_digit(value: object, name: str) -> str:
    if isinstance(value, bool) or not str(value or "").isdigit() or int(value) <= 0:
        raise MiaoshouOneClickPrepareBlocked(
            "approved_identity_invalid", f"{name} is invalid"
        )
    return str(value)


def _positive_decimal(value: object, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise MiaoshouOneClickPrepareBlocked(
            "approved_numeric_field_invalid", f"{name} is invalid"
        )
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise MiaoshouOneClickPrepareBlocked(
            "approved_numeric_field_invalid", f"{name} is invalid"
        ) from error
    if not number.is_finite() or number <= 0:
        raise MiaoshouOneClickPrepareBlocked(
            "approved_numeric_field_invalid", f"{name} is invalid"
        )
    return number


def _normalize_variant(value: object) -> str:
    return str(value or "").strip().strip(";")


def _normalize_notes(value: object) -> str:
    return "".join(str(value or "").replace("\r\n", "\n").split())


def _numbers_equal(left: object, right: object) -> bool:
    try:
        return abs(Decimal(str(left)) - Decimal(str(right))) <= Decimal("0.0001")
    except (InvalidOperation, TypeError, ValueError):
        return False


def _success(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("result") == "success"


def _accepted(value: object) -> bool:
    return bool(
        isinstance(value, Mapping)
        and (
            value.get("accepted") is True
            or value.get("result") == "success"
            or value.get("success") is True
        )
    )


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
