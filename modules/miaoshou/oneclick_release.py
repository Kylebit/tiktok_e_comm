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
import logging
import threading
import time
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
PUBLISH_RATE_LIMIT_CODES = frozenset(
    {"accountApiQpsRateLimit", "accountQpsRateLimit"}
)
PUBLISH_MIN_INTERVAL_SECONDS = 1.1
PUBLISH_RATE_LIMIT_RETRY_DELAY_SECONDS = 3.0
_LOGGER = logging.getLogger(__name__)
_publish_lock = threading.Lock()
_publish_wait: Callable[[float], None] = time.sleep
_publish_now: Callable[[], float] = time.monotonic
_last_publish_attempt_at: float | None = None

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
WAREHOUSE_GET_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/"
    "get_shop_warehouse_list"
)
CATEGORY_METADATA_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/"
    "get_category_metadata"
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
from modules.miaoshou.client import MiaoshouBusinessRejectedError
WEB_BATCH_SET_PRICE_PATH = (
    "/api/platform/tiktok/move/collect_box/batchSetPrice"
)
TIKTOK_CATEGORY_DECISION_SCHEMA = "approved-tiktok-category-decision/v1"
TIKTOK_NO_BRAND_ID = "0"
TIKTOK_NO_BRAND_NAME = "No Brand"
TIKTOK_GB_BATCH_CATEGORY_ID = "600338"
TIKTOK_GB_BATCH_ATTRIBUTE_ID = "102255"
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
    verification_policy: str = "exact",
) -> dict[str, object]:
    """Build one immutable Miaoshou Open API storefront binding.

    The three endpoint families below are the current official Apifox
    contracts.  They are kept beside the shop identity so a stored command
    cannot drift from one platform family to another after restart.
    """

    draft_mode = (
        "site"
        if platform == "tiktok" and site in {"PH", "MY", "TH", "VN"}
        else "shop"
    )
    return {
        "key": key,
        "shop": shop,
        "shop_id": shop_id,
        "region": site,
        "site": site,
        "platform": platform,
        "draft_mode": draft_mode,
        "verification_policy": verification_policy,
        "requires_category_attributes": (
            platform == "tiktok"
            and site == "GB"
        ),
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
                (
                    "get_site_collect_item_info"
                    if draft_mode == "site"
                    else "get_shop_collect_item_info"
                )
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
                (
                    "save_site_collect_item_info"
                    if draft_mode == "site"
                    else "save_shop_collect_item_info"
                )
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


def _shop_endpoint_id(
    config: Mapping[str, object], raw_shop_id: object
) -> object:
    """Return the exact JSON type required by the selected draft endpoint."""

    draft_mode = str(config.get("draft_mode") or "shop")
    if (
        str(config.get("platform") or "") == "tiktok"
        and draft_mode == "shop"
    ):
        if isinstance(raw_shop_id, bool) or not str(raw_shop_id).isdigit():
            raise MiaoshouOneClickPreDispatchError(
                "TikTok shop endpoint identity is invalid"
            )
        endpoint_id = int(str(raw_shop_id))
        if endpoint_id <= 0:
            raise MiaoshouOneClickPreDispatchError(
                "TikTok shop endpoint identity is invalid"
            )
        return endpoint_id
    if type(raw_shop_id) not in {str, int} or isinstance(raw_shop_id, bool):
        raise MiaoshouOneClickPreDispatchError(
            "Miaoshou shop endpoint identity is invalid"
        )
    endpoint_id = str(raw_shop_id).strip()
    if not endpoint_id:
        raise MiaoshouOneClickPreDispatchError(
            "Miaoshou shop endpoint identity is invalid"
        )
    return endpoint_id


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
        verification_policy="submit_without_readback_validation",
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
        target_detail_identities: tuple[Mapping[str, object], ...] = (),
    ) -> None:
        super().__init__(detail)
        self.external_writes = writes
        self.external_write_count = write_count
        self.target_results = target_results
        self.target_detail_identities = target_detail_identities


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
    enforce_publish_pacing: bool = False


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
    if target == "miaoshou:COMMON":
        post = _prepare_post()
        command, proof = _prepare_common(
            payload, source_offer_id=source_offer_id, post=post
        )
    elif (
        target.startswith("tiktok:")
        and hasattr(request, "prerequisite_context")
    ):
        command, proof = _prepare_persisted_collectbox_publish(
            seed,
            request,
            target=target,
        )
    elif target in SITE_CONFIG:
        post = _prepare_post()
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


def _sha256(value: object, name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise MiaoshouOneClickPrepareBlocked(
            "collectbox_publish_proof_invalid",
            f"{name} is invalid",
            category="CAPABILITY",
        )
    return value


def _dispatch_sha256(value: object, name: str) -> str:
    try:
        return _sha256(value, name)
    except MiaoshouOneClickPrepareBlocked as error:
        raise MiaoshouOneClickPreDispatchError(str(error)) from error


def _prepare_persisted_collectbox_publish(
    seed: object,
    request: object,
    *,
    target: str,
) -> tuple[dict[str, object], dict[str, object]]:
    prerequisite = getattr(request, "prerequisite_context", None)
    if not isinstance(prerequisite, Mapping):
        raise MiaoshouOneClickPrepareBlocked(
            "collectbox_publish_proof_missing",
            "persisted TikTok collect-box proof is unavailable",
            category="CAPABILITY",
        )
    if prerequisite.get("schema_version") != (
        "collectbox-tiktok-publish-context/v1"
    ):
        raise MiaoshouOneClickPrepareBlocked(
            "collectbox_publish_proof_invalid",
            "persisted TikTok collect-box proof schema is invalid",
            category="CAPABILITY",
        )
    binding = dict(prerequisite)
    supplied_digest = binding.pop("publish_identity_digest", None)
    if _sha256(supplied_digest, "publish_identity_digest") != _digest(binding):
        raise MiaoshouOneClickPrepareBlocked(
            "collectbox_publish_proof_drifted",
            "persisted TikTok collect-box proof digest drifted",
            category="CAPABILITY",
        )
    exact = {
        "plan_id": str(getattr(request, "plan_id", "")),
        "offer_id": str(
            getattr(request, "immutable_plan_payload", {}).get(
                "product_id", ""
            )
        ),
        "product_revision": getattr(request, "product_revision", None),
        "payload_digest": str(getattr(request, "payload_digest", "")),
        "targets_digest": str(getattr(request, "targets_digest", "")),
        "source_identity_digest": str(
            getattr(request, "source_identity_digest", "")
        ),
        "platform": "TIKTOK",
    }
    if any(prerequisite.get(key) != value for key, value in exact.items()):
        raise MiaoshouOneClickPrepareBlocked(
            "collectbox_publish_proof_identity_mismatch",
            "persisted TikTok collect-box proof does not match the plan",
            category="CAPABILITY",
        )
    detail = prerequisite.get("target_detail_identity")
    if not isinstance(detail, Mapping) or set(detail) != {
        "schema_version",
        "target_label",
        "detail_id",
        "shop_id",
        "identity_digest",
    }:
        raise MiaoshouOneClickPrepareBlocked(
            "collectbox_publish_detail_identity_invalid",
            "persisted TikTok target detail identity is invalid",
            category="CAPABILITY",
        )
    detail_binding = dict(detail)
    detail_digest = detail_binding.pop("identity_digest", None)
    if (
        detail.get("schema_version")
        != "collectbox-target-detail-identity/v1"
        or detail.get("target_label") != target
        or _sha256(detail_digest, "detail_identity_digest")
        != _digest(detail_binding)
    ):
        raise MiaoshouOneClickPrepareBlocked(
            "collectbox_publish_detail_identity_drifted",
            "persisted TikTok target detail identity drifted",
            category="CAPABILITY",
        )
    config = DIRECT_STORE_CONFIG[target]
    detail_id = _positive_digit(detail.get("detail_id"), "detail_id")
    shop_id = _positive_digit(detail.get("shop_id"), "shop_id")
    if shop_id != str(config["shop_id"]):
        raise MiaoshouOneClickPrepareBlocked(
            "collectbox_publish_shop_binding_mismatch",
            "persisted TikTok target shop binding drifted",
            category="CAPABILITY",
        )
    command = {
        "schema_version": (
            "oneclick-miaoshou-prepared-collectbox-publish-command/v1"
        ),
        "kind": "PUBLISH_PREPARED_COLLECTBOX",
        "target_label": target,
        "platform": "tiktok",
        "detail_id": detail_id,
        "shop_id": shop_id,
        "collectbox_receipt_digest": _sha256(
            prerequisite.get("receipt_digest"), "receipt_digest"
        ),
        "publish_identity_digest": supplied_digest,
    }
    proof = {
        "schema_version": (
            "oneclick-miaoshou-prepared-collectbox-publish-proof/v1"
        ),
        "target_label": target,
        "detail_identity_digest": detail_digest,
        "collectbox_receipt_digest": command["collectbox_receipt_digest"],
        "publish_identity_digest": supplied_digest,
        "shop_binding_exact": True,
    }
    return command, proof


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
    if kind in {
        "TIKTOK_SITE",
        "DIRECT_STORE",
        "PUBLISH_PREPARED_COLLECTBOX",
    }:
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
    if kind == "PUBLISH_PREPARED_COLLECTBOX":
        if (
            target not in SITE_CONFIG
            or command.get("schema_version")
            != "oneclick-miaoshou-prepared-collectbox-publish-command/v1"
            or command.get("platform") != "tiktok"
            or command.get("shop_id")
            != str(SITE_CONFIG[target]["shop_id"])
        ):
            raise MiaoshouOneClickPreDispatchError(
                "stored collect-box publish identity is invalid"
            )
        _positive_digit(command.get("detail_id"), "detail_id")
        _dispatch_sha256(
            command.get("collectbox_receipt_digest"), "receipt_digest"
        )
        _dispatch_sha256(
            command.get("publish_identity_digest"),
            "publish_identity_digest",
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
    shop_endpoint_id = _shop_endpoint_id(config, config["shop_id"])
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
    detail: Mapping[str, object] = {}
    if detail_id is not None:
        detail, _oss_md5 = _read_shop(
            post,
            detail_id,
            shop_endpoint_id,
            target=target,
        )
        _verify_shop_identity(
            detail,
            detail_id=detail_id,
            shop_id=expected["shop_id"],
        )
        _verify_site_variants(detail, expected)
        snapshot_digest = _digest(_detail_snapshot(detail))
    if config.get("requires_category_attributes") is True:
        expected["product_attributes"] = _tiktok_category_product_attributes(
            post,
            current=detail,
            expected=expected,
            shop_endpoint_id=shop_endpoint_id,
        )
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
    target = str(command["target_label"])
    config = DIRECT_STORE_CONFIG[target]
    if command.get("kind") == "PUBLISH_PREPARED_COLLECTBOX":
        return _dispatch_prepared_collectbox_publish(
            request,
            command,
            config=config,
        )
    platform = str(config["platform"])
    draft_mode = str(config.get("draft_mode") or "shop")
    shop_endpoint_id = _shop_endpoint_id(config, command["shop_id"])
    expected = _mapping(
        command.get("expected"), "Miaoshou direct-store expected payload"
    )
    prepared_attributes: list[dict[str, object]] | None = None
    if config.get("requires_category_attributes") is True:
        prepared_attributes = _validated_prepared_product_attributes(
            expected.get("product_attributes")
        )
    # Required immutable evidence is validated before constructing a client,
    # reading source pages, or opening any external write occurrence.  This
    # keeps old persisted commands fail-closed at exactly zero transport.
    transport = _runtime_transport()
    post = _required_post(transport)
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
                        "shopIds": [shop_endpoint_id],
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
            shop_endpoint_id,
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
        warehouse_id = (
            _tiktok_warehouse_id(post, detail, expected)
            if platform == "tiktok"
            else None
        )
        updated = _apply_expected_for_platform(
            detail,
            expected,
            platform=platform,
            draft_mode=draft_mode,
            warehouse_id=warehouse_id,
        )
        updated = _apply_target_verification_policy(
            detail,
            updated,
            config,
        )
        if config.get("requires_category_attributes") is True:
            assert prepared_attributes is not None
            current_attributes = _tiktok_category_product_attributes(
                post,
                current=detail,
                expected=expected,
                shop_endpoint_id=shop_endpoint_id,
            )
            if current_attributes != prepared_attributes:
                raise MiaoshouOneClickPreDispatchError(
                    "TikTok category attributes changed after preparation"
                )
            updated["productAttributes"] = [
                dict(row) for row in prepared_attributes
            ]
        body = _save_body(
            platform=platform,
            site=str(config["site"]),
            detail_id=detail_id,
            shop_id=shop_endpoint_id,
            updated=updated,
            oss_md5=oss_md5,
            draft_mode=draft_mode,
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
    if config.get("verification_policy") != (
        "submit_without_readback_validation"
    ):
        try:
            if transport.audit_detail is not None:
                if (
                    transport.audit_detail(str(detail_id), shop_endpoint_id)
                    is not True
                ):
                    raise ValueError("injected draft audit mismatch")
            else:
                readback, _ = _read_shop(
                    post,
                    detail_id,
                    shop_endpoint_id,
                    target=target,
                )
                _verify_target_readback(
                    target,
                    readback,
                    expected,
                    platform=platform,
                    strict_collectbox_tiktok=(
                        platform == "tiktok"
                        and type(expected.get("category_id")) is str
                        and bool(str(expected.get("category_id") or ""))
                    ),
                    draft_mode=draft_mode,
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
            transport.publish(str(detail_id), shop_endpoint_id)
            if transport.publish is not None
            else post(
                str(config["publish_path"]),
                {
                    "detailIds": [detail_id],
                    "shopIds": [shop_endpoint_id],
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


def _safe_business_code(error: MiaoshouBusinessRejectedError) -> str:
    code = str(error.code or "business_rejected").strip()
    if not code or len(code) > 80 or not all(
        character.isalnum() or character in {"_", "-"}
        for character in code
    ):
        return "business_rejected"
    return code


def _pace_publish_attempt(*, enabled: bool) -> None:
    global _last_publish_attempt_at
    if not enabled:
        return
    now = _publish_now()
    if _last_publish_attempt_at is not None:
        remaining = (
            PUBLISH_MIN_INTERVAL_SECONDS
            - (now - _last_publish_attempt_at)
        )
        if remaining > 0:
            _publish_wait(remaining)
            now = _publish_now()
    _last_publish_attempt_at = now


def _dispatch_prepared_collectbox_publish(
    request: object,
    command: Mapping[str, object],
    *,
    config: Mapping[str, object],
) -> dict[str, object]:
    if command.get("schema_version") != (
        "oneclick-miaoshou-prepared-collectbox-publish-command/v1"
    ):
        raise MiaoshouOneClickPreDispatchError(
            "prepared collect-box publish command schema is invalid"
        )
    target = str(command.get("target_label") or "")
    if target not in DIRECT_STORE_CONFIG or config.get("platform") != "tiktok":
        raise MiaoshouOneClickPreDispatchError(
            "prepared collect-box publish target is invalid"
        )
    detail_id = _positive_digit(command.get("detail_id"), "detail_id")
    shop_id = _positive_digit(command.get("shop_id"), "shop_id")
    if shop_id != str(config["shop_id"]):
        raise MiaoshouOneClickPreDispatchError(
            "prepared collect-box publish shop binding drifted"
        )
    _dispatch_sha256(
        command.get("collectbox_receipt_digest"), "receipt_digest"
    )
    _dispatch_sha256(
        command.get("publish_identity_digest"), "publish_identity_digest"
    )
    shop_endpoint_id = _shop_endpoint_id(config, shop_id)
    transport = _runtime_transport()
    post = (
        _required_post(transport)
        if transport.publish is None
        else None
    )
    occurrence_state = WriteOccurrenceState()
    external_id = f"{detail_id}:{shop_id}"
    with _publish_lock:
        for attempt in (1, 2):
            _pace_publish_attempt(
                enabled=transport.enforce_publish_pacing
            )
            occurrence = _open_write(
                request,
                occurrence_state,
                f"publish_submit-{attempt}",
                _platform_write("tiktok", "publish:submission"),
                external_id=external_id,
            )
            try:
                submitted = (
                    transport.publish(detail_id, shop_endpoint_id)
                    if transport.publish is not None
                    else post(
                        PUBLISH_PATH,
                        {
                            "detailIds": [int(detail_id)],
                            "shopIds": [shop_endpoint_id],
                        },
                    )
                )
                break
            except MiaoshouBusinessRejectedError as error:
                _reject_write(
                    request,
                    occurrence_state,
                    occurrence,
                    external_id=external_id,
                )
                code = _safe_business_code(error)
                _LOGGER.warning(
                    "miaoshou_tiktok_publish_rejected "
                    "target=%s attempt=%s code=%s",
                    target,
                    attempt,
                    code,
                )
                if code in PUBLISH_RATE_LIMIT_CODES and attempt == 1:
                    _LOGGER.info(
                        "miaoshou_tiktok_publish_rate_retry "
                        "target=%s delay_seconds=%s",
                        target,
                        PUBLISH_RATE_LIMIT_RETRY_DELAY_SECONDS,
                    )
                    _publish_wait(
                        PUBLISH_RATE_LIMIT_RETRY_DELAY_SECONDS
                    )
                    continue
                raise _rejected_write_error(
                    occurrence_state,
                    f"Miaoshou TikTok publish rejected: {code}",
                    external_id=external_id,
                ) from error
            except Exception as error:
                _LOGGER.error(
                    "miaoshou_tiktok_publish_unknown "
                    "target=%s attempt=%s",
                    target,
                    attempt,
                )
                raise _unknown_write_error(
                    occurrence_state,
                    occurrence,
                    "Miaoshou TikTok publish outcome is unknown",
                    external_id=external_id,
                ) from error
        else:  # pragma: no cover - the bounded loop always exits explicitly
            raise MiaoshouOneClickPreDispatchError(
                "Miaoshou TikTok publish retry state is invalid"
            )
    if not isinstance(submitted, Mapping):
        raise _unknown_write_error(
            occurrence_state,
            occurrence,
            "Miaoshou TikTok publish response is malformed",
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
            "Miaoshou TikTok publish was not accepted",
            external_id=external_id,
        )
    _confirm_write(
        request,
        occurrence_state,
        occurrence,
        external_id=external_id,
    )
    _LOGGER.info(
        "miaoshou_tiktok_publish_accepted target=%s attempt=%s",
        target,
        attempt,
    )
    return _receipt(
        "SUBMITTED_UNVERIFIED",
        external_id,
        occurrence_state.external_writes,
        False,
        "miaoshou_submission_accepted",
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
    raw_commercial = facts.get("sku_commercial_facts")
    sku_commercial_facts: dict[str, dict[str, object]] = {}
    if raw_commercial is not None:
        if not isinstance(raw_commercial, Mapping) or set(raw_commercial) != set(
            selected
        ):
            raise MiaoshouOneClickPrepareBlocked(
                "approved_sku_commercial_facts_mismatch",
                "approved SKU commercial facts do not match selected variants",
            )
        for variant in selected:
            row = _mapping(raw_commercial.get(variant), "SKU commercial facts")
            sku_weight = str(_positive_decimal(row.get("weight_kg"), "SKU weight"))
            sku_package = row.get("package_cm")
            if not isinstance(sku_package, list) or len(sku_package) != 3:
                raise MiaoshouOneClickPrepareBlocked(
                    "approved_sku_parcel_missing",
                    "approved SKU parcel is unavailable",
                )
            sku_commercial_facts[variant] = {
                "cost_cny": str(
                    _positive_decimal(row.get("cost_cny"), "SKU cost")
                ),
                "weight": sku_weight,
                "package_cm": [
                    str(_positive_decimal(value, "SKU package dimension"))
                    for value in sku_package
                ],
            }
    else:
        sku_commercial_facts = {
            variant: {
                "weight": weight,
                "package_cm": list(package),
            }
            for variant in selected
        }
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
        "sku_commercial_facts": sku_commercial_facts,
        "product_category": facts.get("category"),
    }


def _approved_plan_source_offer_id(
    payload: Mapping[str, object],
) -> str:
    identity = payload.get("source_product_identity")
    if not isinstance(identity, Mapping):
        raise MiaoshouOneClickPreDispatchError(
            "approved source product identity is unavailable"
        )
    source_offer_id = identity.get("source_offer_id")
    if (
        type(source_offer_id) is not str
        or not source_offer_id.isascii()
        or not source_offer_id.isdecimal()
        or int(source_offer_id) <= 0
    ):
        raise MiaoshouOneClickPreDispatchError(
            "approved source offer identity is invalid"
        )
    return str(int(source_offer_id))


def _approved_tiktok_category_id(
    payload: Mapping[str, object], *, target: str
) -> str | None:
    facts = payload.get("product_facts")
    targets = payload.get("targets")
    if not isinstance(facts, Mapping) or not isinstance(targets, list):
        return None
    expected = approved_tiktok_category_decisions(
        facts.get("category"),
        targets=tuple(targets),
    )
    if not isinstance(expected, Mapping):
        return None
    expected_decision = expected.get(target)
    if not isinstance(expected_decision, Mapping):
        return None
    if "approved_tiktok_category_decisions" not in payload:
        # Legacy approved plans predate the explicit category-decision field.
        # Recover it only from that immutable plan's own category and exact
        # target set; malformed or explicitly supplied decisions still fail
        # closed below.
        decision = expected_decision
    else:
        decisions = payload.get("approved_tiktok_category_decisions")
        if not isinstance(decisions, Mapping):
            return None
        decision = decisions.get(target)
        if decision != expected_decision:
            return None
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
    pricing = _mapping(payload.get("pricing"), "pricing")
    selected_pricing = _mapping(
        pricing.get("selected_targets"), "selected pricing"
    )
    target_pricing = _mapping(selected_pricing.get(target), "target pricing")
    raw_sku_prices = target_pricing.get("sku_prices")
    sku_prices: dict[str, str] = {}
    if raw_sku_prices is not None:
        if not isinstance(raw_sku_prices, list) or any(
            not isinstance(row, Mapping) for row in raw_sku_prices
        ):
            raise MiaoshouOneClickPrepareBlocked(
                "approved_sku_prices_invalid",
                "approved SKU prices are invalid",
            )
        for row in raw_sku_prices:
            variant = _normalize_variant(row.get("variant_key"))
            if (
                variant in sku_prices
                or str(row.get("target_key") or "") != str(config["key"])
                or _text(row.get("currency"), "SKU currency") != currency
            ):
                raise MiaoshouOneClickPrepareBlocked(
                    "approved_sku_prices_mismatch",
                    "approved SKU prices do not match the storefront",
                )
            sku_prices[variant] = str(
                _positive_decimal(row.get("list_price"), "SKU list price")
            )
        if set(sku_prices) != set(common["selected_sku_keys"]):
            raise MiaoshouOneClickPrepareBlocked(
                "approved_sku_prices_mismatch",
                "approved SKU prices do not match selected variants",
            )
    else:
        sku_prices = {
            variant: price for variant in common["selected_sku_keys"]
        }
    result = {
        **common,
        "target_label": target,
        "shop_name": str(config["shop"]),
        "shop_id": str(config["shop_id"]),
        "region": str(config["region"]),
        "platform": str(config["platform"]),
        "title": title,
        "price": price,
        "sku_prices": sku_prices,
        "currency": currency,
    }
    if str(config["platform"]) == "tiktok":
        result["category_id"] = _approved_tiktok_category_id(
            payload, target=target
        )
    return result


def prepare_tiktok_collectbox(
    *,
    common_detail_id: str,
    initial_platform_detail_id: str,
    initial_claim_written: bool,
    approved_plan_payload: Mapping[str, object],
    approved_targets: tuple[str, ...],
    post: Callable[[str, Mapping[str, object]], object] | None = None,
    web_post: Callable[[str, Mapping[str, object]], object] | None = None,
) -> dict[str, object]:
    """Populate only approved TikTok drafts; never publish them."""

    return _prepare_selected_platform_collectbox(
        platform="tiktok",
        common_detail_id=common_detail_id,
        initial_platform_detail_id=initial_platform_detail_id,
        initial_claim_written=initial_claim_written,
        approved_plan_payload=approved_plan_payload,
        approved_targets=approved_targets,
        post=post,
        web_post=web_post,
    )


def prepare_shopee_collectbox(
    *,
    common_detail_id: str,
    initial_platform_detail_id: str,
    initial_claim_written: bool,
    approved_plan_payload: Mapping[str, object],
    approved_targets: tuple[str, ...],
    post: Callable[[str, Mapping[str, object]], object] | None = None,
    web_post: Callable[[str, Mapping[str, object]], object] | None = None,
) -> dict[str, object]:
    """Populate only approved Shopee drafts; never publish them."""

    return _prepare_selected_platform_collectbox(
        platform="shopee",
        common_detail_id=common_detail_id,
        initial_platform_detail_id=initial_platform_detail_id,
        initial_claim_written=initial_claim_written,
        approved_plan_payload=approved_plan_payload,
        approved_targets=approved_targets,
        post=post,
        web_post=web_post,
    )


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
    """Compatibility router for callers that still pass a platform name."""

    prepare = {
        "tiktok": prepare_tiktok_collectbox,
        "shopee": prepare_shopee_collectbox,
    }.get(platform)
    if prepare is None:
        raise MiaoshouCollectBoxPreparationError(
            "collect-box platform is unsupported", writes=(), write_count=0
        )
    return prepare(
        common_detail_id=common_detail_id,
        initial_platform_detail_id=initial_platform_detail_id,
        initial_claim_written=initial_claim_written,
        approved_plan_payload=approved_plan_payload,
        approved_targets=approved_targets,
        post=post,
        web_post=web_post,
    )


def _prepare_selected_platform_collectbox(
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
    """Shared mechanics after the caller has selected one channel."""

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
    known_target_detail_identities: list[dict[str, object]] = []
    if initial_claim_written:
        writes.append(f"miaoshou:collectbox:claim:{platform}")
        write_invocation_count = 1

    def add_write(write_class: str) -> None:
        nonlocal write_invocation_count
        write_invocation_count += 1
        if write_class not in writes:
            writes.append(write_class)

    def remember_target_detail(target: str, detail_id: int) -> None:
        """Persist a provider-issued target identity before later validation.

        Creation/claim establishes the identity.  Content validation may fail
        afterwards, but must not erase the only exact handle needed by a later
        retry or an independent target publication.
        """

        if any(
            row["target_label"] == target
            for row in known_target_detail_identities
        ):
            return
        known_target_detail_identities.append(
            {
                "target_label": target,
                "detail_id": str(detail_id),
                "shop_id": str(DIRECT_STORE_CONFIG[target]["shop_id"]),
            }
        )

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
        draft_mode = str(config.get("draft_mode") or "shop")
        shop_endpoint_id = _shop_endpoint_id(config, config["shop_id"])
        try:
            expected = _approved_site(
                approved_plan_payload,
                target=target,
                config=config,
                source_offer_id=_approved_plan_source_offer_id(
                    approved_plan_payload
                ),
            )
            expected["common_detail_id"] = common_id
        except Exception:
            fail("approved platform draft is invalid")
        if platform == "tiktok" and expected.get("category_id") is None:
            return primary_detail_id, _target_result(
                target,
                "FAILED",
                error_code="category_not_approved",
                detail="approved site category evidence is unavailable",
            )

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
                        "shopIds": [shop_endpoint_id],
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
            remember_target_detail(target, detail_id)

        try:
            detail, oss_md5 = _read_shop(
                client,
                detail_id,
                shop_endpoint_id,
                target=target,
            )
            _verify_shop_identity(
                detail,
                detail_id=detail_id,
                shop_id=str(config["shop_id"]),
            )
            if platform == "tiktok":
                _verify_tiktok_detail_source_identity(detail, expected)
            _verify_site_variants(detail, expected)
            warehouse_id = (
                _tiktok_warehouse_id(client, detail, expected)
                if platform == "tiktok"
                else None
            )
            updated = _apply_expected_for_platform(
                detail,
                expected,
                platform=platform,
                draft_mode=draft_mode,
                warehouse_id=warehouse_id,
            )
            updated = _apply_target_verification_policy(
                detail,
                updated,
                config,
            )
            if config.get("requires_category_attributes") is True:
                expected["product_attributes"] = (
                    _tiktok_category_product_attributes(
                        client,
                        current=detail,
                        expected=expected,
                        shop_endpoint_id=shop_endpoint_id,
                    )
                )
                updated["productAttributes"] = [
                    dict(row) for row in expected["product_attributes"]
                ]
            body = _save_body(
                platform=platform,
                site=str(config["site"]),
                detail_id=detail_id,
                shop_id=shop_endpoint_id,
                updated=updated,
                oss_md5=oss_md5,
                draft_mode=draft_mode,
            )
        except Exception:
            fail(f"{platform} draft preparation failed before update")

        update_class = (
            f"miaoshou:collectbox:{platform}:detail:update:{target}"
        )
        try:
            saved = client(str(config["save_path"]), body)
        except MiaoshouBusinessRejectedError:
            if (
                target == "tiktok:GB"
                and config.get("verification_policy")
                == "submit_without_readback_validation"
            ):
                return detail_id, _target_result(
                    target,
                    "FAILED",
                    error_code="gb_draft_update_rejected_waived",
                    detail=(
                        "GB draft update was rejected; existing exact draft "
                        "identity remains eligible for direct submission"
                    ),
                )
            fail(f"{platform} draft update was rejected")
        except Exception:
            if (
                target == "tiktok:GB"
                and config.get("verification_policy")
                == "submit_without_readback_validation"
            ):
                add_write(update_class)
                write_count_unknown = True
                return detail_id, _target_result(
                    target,
                    "FAILED",
                    error_code="gb_draft_update_unknown_waived",
                    detail=(
                        "GB draft update result is unknown; existing exact "
                        "draft identity remains eligible for direct submission"
                    ),
                )
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
                shop_endpoint_id,
                target=target,
            )
            readback_available = True
            _verify_target_readback(
                target,
                readback,
                expected,
                platform=platform,
                strict_collectbox_tiktok=True,
                draft_mode=draft_mode,
            )
            # SEA continues below to validate its category and then accepts
            # this exact site draft as the final publish input.  MX/GB keep
            # their existing shop-draft return/repair behavior unchanged.
            if not (platform == "tiktok" and draft_mode == "site"):
                return detail_id, _target_result(target, "SUCCEEDED")
        except Exception:
            # The historical SEA publish input is the site draft itself, so
            # every approved field in that readback must be exact before the
            # draft can be submitted.  Only MX/GB shop drafts retain the
            # separate price-repair fallback below.
            if platform == "tiktok" and draft_mode == "site":
                return detail_id, _target_result(
                    target,
                    "FAILED",
                    error_code="approved_site_draft_readback_mismatch",
                    detail="Miaoshou site draft differs from approved plan",
                )
            if platform != "tiktok":
                fail(f"{platform} draft readback did not match approved plan")

        category_error = (
            _tiktok_category_error_code(readback, expected)
            if config.get("verification_policy") == "exact"
            else None
        )
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

        if platform == "tiktok" and draft_mode == "site":
            # The proven SEA dispatch path consumes this exact site draft via
            # ``save_move_collect_task``.  Miaoshou's intermediate list card
            # may continue to show an automatic COMMON-CNY conversion; that
            # card is neither the submitted payload nor a prerequisite for
            # final publication.  Keep the exact site-detail readback above as
            # the hard gate and leave final price verification to the channel
            # publication/readback stage.
            return detail_id, _target_result(target, "SUCCEEDED")

        # Miaoshou's OpenAPI detail-save normalizes the local site price from
        # the common-box CNY origin price.  The web batch-price endpoint is
        # the authoritative persisted local-price operation used by the UI.
        repairable_price = readback_available and (
            draft_mode == "site"
            or not _tiktok_prices_exact(readback, expected)
        )
        if repairable_price:
            try:
                price_body = _tiktok_batch_price_body(
                    detail_id=detail_id,
                    site=str(config["site"]),
                    price=expected["price"],
                    sku_prices=expected.get("sku_prices"),
                )
            except MiaoshouOneClickPreDispatchError:
                # Miaoshou's batch-price endpoint exposes one listing-wide
                # value.  It is safe only when every approved SKU has the
                # same price; otherwise invoking it would silently flatten a
                # valid multi-SKU price matrix.
                return detail_id, _target_result(
                    target,
                    "FAILED",
                    error_code="approved_sku_price_batch_repair_unsupported",
                    detail=(
                        "Miaoshou batch price repair cannot preserve distinct "
                        "approved SKU prices"
                    ),
                )
            try:
                active_price_client = price_client or _prepare_web_price_post()
                repaired = active_price_client(
                    WEB_BATCH_SET_PRICE_PATH,
                    price_body,
                )
            except MiaoshouBusinessRejectedError:
                return detail_id, _target_result(
                    target,
                    "FAILED",
                    error_code="approved_price_batch_repair_rejected",
                    detail="Miaoshou batch price repair was rejected",
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
            if draft_mode == "site":
                try:
                    authoritative_price_exact = (
                        _authoritative_tiktok_list_price_exact(
                            client,
                            detail=readback,
                            expected=expected,
                            detail_id=detail_id,
                            target=target,
                        )
                    )
                except Exception:
                    return detail_id, _target_result(
                        target,
                        "FAILED",
                        error_code=(
                            "approved_price_authoritative_readback_unavailable"
                        ),
                        detail=(
                            "Miaoshou list/display price could not be read exactly"
                        ),
                    )
                if not authoritative_price_exact:
                    return detail_id, _target_result(
                        target,
                        "FAILED",
                        error_code=(
                            "approved_price_authoritative_readback_mismatch"
                        ),
                        detail=(
                            "Miaoshou list/display price differs from approved price"
                        ),
                    )
            repair_readback_available = False
            try:
                readback, _ = _read_shop(
                    client,
                    detail_id,
                    shop_endpoint_id,
                    target=target,
                )
                repair_readback_available = True
                _verify_target_readback(
                    target,
                    readback,
                    expected,
                    platform=platform,
                    strict_collectbox_tiktok=True,
                    draft_mode=draft_mode,
                    verify_price=(draft_mode != "site"),
                )
                return detail_id, _target_result(
                    target,
                    (
                        "SUCCEEDED"
                        if draft_mode == "site"
                        else "REPAIRED_SUCCEEDED"
                    ),
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
            if platform != "tiktok":
                remember_target_detail(target, detail_id)
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
            target_detail_identities=tuple(
                known_target_detail_identities
            ),
        )

    return {
        "schema_version": "miaoshou-platform-collectbox-preparation/v1",
        "platform": platform,
        "primary_platform_detail_id": str(primary_detail_id),
        "target_count": len(selected),
        "platform_detail_count": len(
            {
                row["detail_id"]
                for row in known_target_detail_identities
            }
        ),
        "external_writes": tuple(writes),
        "external_write_count": (
            None if write_count_unknown else write_invocation_count
        ),
        "target_results": target_results,
        "target_detail_identities": known_target_detail_identities,
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
    shop_id: object,
    updated: Mapping[str, object],
    oss_md5: str,
    draft_mode: str = "shop",
) -> dict[str, object]:
    if platform == "tiktok":
        if draft_mode == "site":
            return {
                "detailId": detail_id,
                "site": site,
                "siteCollectItemInfo": dict(updated),
                "ossMd5": oss_md5,
            }
        if draft_mode != "shop":
            raise MiaoshouOneClickPreDispatchError(
                "TikTok draft mode is invalid"
            )
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


def _tiktok_warehouse_id(
    post: Callable[[str, Mapping[str, object]], object],
    detail: Mapping[str, object],
    expected: Mapping[str, object],
) -> str:
    """Return the exact shop warehouse used by the proven draft contract.

    A claimed draft may already contain the binding.  Otherwise the official
    read-only warehouse endpoint is queried before the save.  No default or
    cross-shop warehouse is invented.
    """

    shop_id = str(expected["shop_id"])
    observed: set[str] = set()
    for value in _sku_map(detail).values():
        row = _mapping(value, "sku row")
        shop_map = row.get("shopIdToWarehouseIdAndStockMap")
        if shop_map is None:
            continue
        if not isinstance(shop_map, Mapping):
            raise MiaoshouOneClickPreDispatchError(
                "TikTok warehouse binding is malformed"
            )
        warehouse_map = shop_map.get(shop_id)
        if warehouse_map is None:
            continue
        if not isinstance(warehouse_map, Mapping) or not warehouse_map:
            raise MiaoshouOneClickPreDispatchError(
                "TikTok warehouse binding is malformed"
            )
        for warehouse_id in warehouse_map:
            if type(warehouse_id) is not str or not warehouse_id.strip():
                raise MiaoshouOneClickPreDispatchError(
                    "TikTok warehouse identity is malformed"
                )
            observed.add(warehouse_id.strip())
    if len(observed) == 1:
        return next(iter(observed))
    if len(observed) > 1:
        raise MiaoshouOneClickPreDispatchError(
            "TikTok warehouse identity is ambiguous"
        )

    response = post(WAREHOUSE_GET_PATH, {"shopIds": [shop_id]})
    if not _success(response):
        raise MiaoshouOneClickPreDispatchError(
            "TikTok warehouse read-only GET failed"
        )
    data = response.get("data")
    groups = data.get("shopWarehouseList") if isinstance(data, Mapping) else None
    if (
        not isinstance(groups, list)
        or not groups
        or any(not isinstance(group, Mapping) for group in groups)
    ):
        raise MiaoshouOneClickPreDispatchError(
            "TikTok warehouse response is malformed"
        )
    rows: list[Mapping[str, object]] = []
    for group in groups:
        group_shop_id = str(group.get("shopId") or "")
        if group_shop_id and group_shop_id != shop_id:
            continue
        warehouses = group.get("warehouseList")
        if not isinstance(warehouses, list) or any(
            not isinstance(row, Mapping) for row in warehouses
        ):
            raise MiaoshouOneClickPreDispatchError(
                "TikTok warehouse response is malformed"
            )
        rows.extend(warehouses)
    active = [
        row
        for row in rows
        if str(row.get("warehouseEffectStatus") or "1") == "1"
        and type(row.get("warehouseId")) is str
        and bool(str(row.get("warehouseId") or "").strip())
    ]
    if not active:
        raise MiaoshouOneClickPreDispatchError(
            "TikTok active warehouse is unavailable"
        )
    active.sort(
        key=lambda row: (
            str(row.get("isDefault") or "0") != "1",
            str(row.get("warehouseSubType") or "") == "3",
            str(row.get("warehouseId") or ""),
        )
    )
    return str(active[0]["warehouseId"]).strip()


def _apply_expected_for_platform(
    current: Mapping[str, object],
    expected: Mapping[str, object],
    *,
    platform: str,
    draft_mode: str = "shop",
    warehouse_id: str | None = None,
) -> dict[str, object]:
    if platform in {"tiktok", "shopee"}:
        updated = _apply_expected(current, expected)
        if platform == "tiktok":
            if type(warehouse_id) is not str or not warehouse_id:
                raise MiaoshouOneClickPreDispatchError(
                    "TikTok warehouse identity is unavailable"
                )
            updated.update(
                {
                    "isCodOpen": "0",
                    "sizeChart": "",
                    "sizeChartType": "",
                    "deliveryOptionSetType": str(
                        current.get("deliveryOptionSetType") or "default"
                    ),
                }
            )
            for row in _sku_map(updated).values():
                sku = _mapping(row, "sku row")
                stock = sku.get("stock")
                if (
                    isinstance(stock, bool)
                    or not str(stock or "").isdigit()
                    or int(stock) <= 0
                ):
                    raise MiaoshouOneClickPreDispatchError(
                        "TikTok approved stock is unavailable"
                    )
                sku["stock"] = int(stock)
                sku["shopIdToWarehouseIdAndStockMap"] = {
                    str(expected["shop_id"]): {
                        warehouse_id: str(int(stock))
                    }
                }
        if platform == "tiktok" and draft_mode == "site":
            shop_id = str(expected["shop_id"])
            current_rows = current.get("collectBoxDetailShopList")
            rows = (
                current_rows
                if isinstance(current_rows, list)
                and all(isinstance(row, Mapping) for row in current_rows)
                else []
            )
            matching = [
                dict(row)
                for row in rows
                if str(row.get("shopId") or "") == shop_id
            ]
            template = matching[0] if len(matching) == 1 else {}
            current_brand_id = str(template.get("brandId") or "")
            current_brand_name = str(template.get("brandName") or "").strip()
            template.update(
                {
                    "shopId": shop_id,
                    "site": str(expected["region"]),
                    "brandId": TIKTOK_NO_BRAND_ID,
                    "brandName": (
                        current_brand_name
                        if current_brand_id == TIKTOK_NO_BRAND_ID
                        and current_brand_name
                        else TIKTOK_NO_BRAND_NAME
                    ),
                    "deliveryOptionSetType": str(
                        template.get("deliveryOptionSetType")
                        or updated["deliveryOptionSetType"]
                    ),
                    "deliveryOptionIds": list(
                        template.get("deliveryOptionIds")
                        or current.get("deliveryOptionIds")
                        or []
                    ),
                    "manufacturerIds": list(
                        template.get("manufacturerIds") or []
                    ),
                    "responsiblePersonIds": list(
                        template.get("responsiblePersonIds") or []
                    ),
                }
            )
            updated["site"] = str(expected["region"])
            updated["editModel"] = "site"
            updated["collectBoxDetailShopList"] = [template]
        elif platform == "tiktok" and draft_mode == "shop":
            updated["brandId"] = TIKTOK_NO_BRAND_ID
            current_brand_id = str(current.get("brandId") or "")
            current_brand_name = str(current.get("brandName") or "").strip()
            updated["brandName"] = (
                current_brand_name
                if current_brand_id == TIKTOK_NO_BRAND_ID
                and current_brand_name
                else TIKTOK_NO_BRAND_NAME
            )
        return updated
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


def _apply_target_verification_policy(
    current: Mapping[str, object],
    updated: Mapping[str, object],
    config: Mapping[str, object],
) -> dict[str, object]:
    """Apply only fields that are explicitly waived from GB draft updates.

    GB still requires the approved category and its mandatory official
    attribute in the write request.  The waiver applies only to the title and
    to post-save readback; it must never erase category facts before SAVE.
    """

    result = dict(updated)
    if config.get("verification_policy") != "submit_without_readback_validation":
        return result
    for field in ("title",):
        if field in current:
            result[field] = current[field]
        else:
            result.pop(field, None)
    return result


def _tiktok_category_product_attributes(
    post: Callable[[str, Mapping[str, object]], object],
    *,
    current: Mapping[str, object],
    expected: Mapping[str, object],
    shop_endpoint_id: object,
) -> list[dict[str, object]]:
    """Build the exact TikTok product-attribute payload from official rules.

    Miaoshou validates price, category, and category attributes in one save.
    A missing mandatory attribute therefore rejects the whole update.  We may
    deterministically select an official value only when it is the sole legal
    value; multi-choice business decisions remain fail-closed.
    """

    category_id = expected.get("category_id")
    site = expected.get("region")
    if (
        type(category_id) is not str
        or not category_id.isdigit()
        or int(category_id) <= 0
        or type(site) is not str
        or not site.strip()
        or isinstance(shop_endpoint_id, bool)
        or not str(shop_endpoint_id).isdigit()
        or int(shop_endpoint_id) <= 0
    ):
        raise MiaoshouOneClickPreDispatchError(
            "TikTok category metadata identity is unavailable"
        )
    response = post(
        CATEGORY_METADATA_PATH,
        {
            "site": site.strip().upper(),
            "cid": int(category_id),
            "shopIds": [int(shop_endpoint_id)],
        },
    )
    if not isinstance(response, Mapping) or not _accepted(response):
        raise MiaoshouOneClickPreDispatchError(
            "TikTok category metadata request was rejected"
        )
    data = response.get("data")
    if not isinstance(data, Mapping):
        raise MiaoshouOneClickPreDispatchError(
            "TikTok category metadata response is malformed"
        )
    metadata = data.get("categoryMetadata")
    if not isinstance(metadata, Mapping):
        raise MiaoshouOneClickPreDispatchError(
            "TikTok category metadata response is malformed"
        )
    rules = metadata.get("categoryProductAttrList")
    if not isinstance(rules, list) or any(
        not isinstance(row, Mapping) for row in rules
    ):
        raise MiaoshouOneClickPreDispatchError(
            "TikTok product attribute rules are malformed"
        )

    current_rows = current.get("productAttributes", [])
    if not isinstance(current_rows, list) or any(
        not isinstance(row, Mapping) for row in current_rows
    ):
        raise MiaoshouOneClickPreDispatchError(
            "TikTok current product attributes are malformed"
        )
    current_by_id: dict[str, Mapping[str, object]] = {}
    for row in current_rows:
        raw_id = row.get("attributeId")
        if type(raw_id) is not str or not raw_id.strip():
            raise MiaoshouOneClickPreDispatchError(
                "TikTok current product attributes are malformed"
            )
        attr_id = raw_id.strip()
        if attr_id in current_by_id:
            raise MiaoshouOneClickPreDispatchError(
                "TikTok current product attributes are ambiguous"
            )
        current_by_id[attr_id] = row

    resolved: list[dict[str, object]] = []
    seen_rule_ids: set[str] = set()
    gb_batch_rule_seen = False
    for rule in rules:
        raw_attr_id = rule.get("attrId")
        raw_name = rule.get("name")
        raw_alias = rule.get("attributeNameAlias")
        mandatory = rule.get("isMandatory")
        values = rule.get("values")
        if (
            type(raw_attr_id) is not str
            or not raw_attr_id.strip()
            or type(raw_name) is not str
            or not raw_name.strip()
            or type(raw_alias) is not str
            or type(mandatory) is not bool
            or not isinstance(values, list)
            or any(not isinstance(value, Mapping) for value in values)
        ):
            raise MiaoshouOneClickPreDispatchError(
                "TikTok product attribute rules are malformed"
            )
        attr_id = raw_attr_id.strip()
        if attr_id in seen_rule_ids:
            raise MiaoshouOneClickPreDispatchError(
                "TikTok product attribute rules are ambiguous"
            )
        seen_rule_ids.add(attr_id)
        is_gb_batch_rule = (
            site.strip().upper() == "GB"
            and category_id == TIKTOK_GB_BATCH_CATEGORY_ID
            and attr_id == TIKTOK_GB_BATCH_ATTRIBUTE_ID
        )
        if is_gb_batch_rule:
            gb_batch_rule_seen = True
            if (
                mandatory is not True
                or rule.get("isMultipleSelected") is not False
            ):
                raise MiaoshouOneClickPreDispatchError(
                    "TikTok GB Batch Number rule is not exact"
                )

        official_values: list[dict[str, str]] = []
        official_by_id: dict[str, dict[str, str]] = {}
        for value in values:
            raw_value_id = value.get("id")
            raw_value_name = value.get("name")
            raw_value_alias = value.get("valueNameAlias")
            if (
                type(raw_value_id) is not str
                or not raw_value_id.strip()
                or type(raw_value_name) is not str
                or not raw_value_name.strip()
                or type(raw_value_alias) is not str
            ):
                raise MiaoshouOneClickPreDispatchError(
                    "TikTok product attribute values are malformed"
                )
            value_id = raw_value_id.strip()
            if value_id in official_by_id:
                raise MiaoshouOneClickPreDispatchError(
                    "TikTok product attribute values are ambiguous"
                )
            normalized = {
                "valueName": raw_value_name,
                "valueId": value_id,
                "valueNameAlias": raw_value_alias,
            }
            official_values.append(normalized)
            official_by_id[value_id] = normalized

        if is_gb_batch_rule and len(official_values) != 1:
            raise MiaoshouOneClickPreDispatchError(
                "TikTok GB Batch Number requires one official value"
            )

        selected: list[dict[str, str]] = []
        current_row = current_by_id.get(attr_id)
        if current_row is not None:
            current_values = current_row.get("attributeValues")
            if not isinstance(current_values, list) or any(
                not isinstance(value, Mapping) for value in current_values
            ):
                raise MiaoshouOneClickPreDispatchError(
                    "TikTok current product attributes are malformed"
                )
            selected_ids: set[str] = set()
            for value in current_values:
                raw_value_id = value.get("valueId")
                if type(raw_value_id) is not str or not raw_value_id.strip():
                    raise MiaoshouOneClickPreDispatchError(
                        "TikTok current product attributes are malformed"
                    )
                value_id = raw_value_id.strip()
                if value_id in selected_ids or value_id not in official_by_id:
                    raise MiaoshouOneClickPreDispatchError(
                        "TikTok current product attribute differs from official rules"
                    )
                selected_ids.add(value_id)
                selected.append(dict(official_by_id[value_id]))

        if mandatory and not selected:
            if len(official_values) != 1:
                raise MiaoshouOneClickPreDispatchError(
                    "TikTok mandatory product attribute requires review"
                )
            selected = [dict(official_values[0])]
        resolved.append(
            {
                "attributeId": attr_id,
                "attributeName": raw_name,
                "attributeNameAlias": raw_alias,
                "attributeValues": selected,
            }
        )
    if (
        site.strip().upper() == "GB"
        and category_id == TIKTOK_GB_BATCH_CATEGORY_ID
        and not gb_batch_rule_seen
    ):
        raise MiaoshouOneClickPreDispatchError(
            "TikTok GB Batch Number rule is unavailable"
        )
    return resolved


def _validated_prepared_product_attributes(
    raw_attributes: object,
) -> list[dict[str, object]]:
    """Validate the JSON-only category evidence stored by prepare."""

    if not isinstance(raw_attributes, list) or not raw_attributes:
        raise MiaoshouOneClickPreDispatchError(
            "prepared TikTok category attributes are unavailable"
        )
    normalized: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    batch_rows = 0
    for raw_row in raw_attributes:
        if not isinstance(raw_row, Mapping) or set(raw_row) != {
            "attributeId",
            "attributeName",
            "attributeNameAlias",
            "attributeValues",
        }:
            raise MiaoshouOneClickPreDispatchError(
                "prepared TikTok category attributes are invalid"
            )
        attr_id = raw_row.get("attributeId")
        name = raw_row.get("attributeName")
        alias = raw_row.get("attributeNameAlias")
        raw_values = raw_row.get("attributeValues")
        if (
            type(attr_id) is not str
            or not attr_id.strip()
            or attr_id != attr_id.strip()
            or attr_id in seen_ids
            or type(name) is not str
            or not name.strip()
            or type(alias) is not str
            or not isinstance(raw_values, list)
            or any(not isinstance(value, Mapping) for value in raw_values)
        ):
            raise MiaoshouOneClickPreDispatchError(
                "prepared TikTok category attributes are invalid"
            )
        seen_ids.add(attr_id)
        values: list[dict[str, str]] = []
        seen_value_ids: set[str] = set()
        for raw_value in raw_values:
            if set(raw_value) != {
                "valueName",
                "valueId",
                "valueNameAlias",
            }:
                raise MiaoshouOneClickPreDispatchError(
                    "prepared TikTok category attributes are invalid"
                )
            value_id = raw_value.get("valueId")
            value_name = raw_value.get("valueName")
            value_alias = raw_value.get("valueNameAlias")
            if (
                type(value_id) is not str
                or not value_id.strip()
                or value_id != value_id.strip()
                or value_id in seen_value_ids
                or type(value_name) is not str
                or not value_name.strip()
                or type(value_alias) is not str
            ):
                raise MiaoshouOneClickPreDispatchError(
                    "prepared TikTok category attributes are invalid"
                )
            seen_value_ids.add(value_id)
            values.append(
                {
                    "valueName": value_name,
                    "valueId": value_id,
                    "valueNameAlias": value_alias,
                }
            )
        if attr_id == TIKTOK_GB_BATCH_ATTRIBUTE_ID:
            batch_rows += 1
            if len(values) != 1:
                raise MiaoshouOneClickPreDispatchError(
                    "prepared TikTok GB Batch Number is invalid"
                )
        normalized.append(
            {
                "attributeId": attr_id,
                "attributeName": name,
                "attributeNameAlias": alias,
                "attributeValues": values,
            }
        )
    if batch_rows != 1:
        raise MiaoshouOneClickPreDispatchError(
            "prepared TikTok GB Batch Number is unavailable"
        )
    return normalized


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
        raw_sku_commercial = expected.get("sku_commercial_facts")
        sku_commercial = (
            _mapping(raw_sku_commercial.get(variant), "SKU commercial fact")
            if isinstance(raw_sku_commercial, Mapping)
            and variant in raw_sku_commercial
            else {}
        )
        sku_weight = sku_commercial.get("weight", expected["weight"])
        sku_package = sku_commercial.get(
            "package_cm", expected["package_cm"]
        )
        if not isinstance(sku_package, list) or len(sku_package) != 3:
            raise MiaoshouOneClickPreDispatchError(
                "prepared SKU parcel is invalid"
            )
        row["itemNum"] = expected["model_skus"][variant]
        row["weight"] = float(str(sku_weight))
        row["packageLength"] = float(str(sku_package[0]))
        row["packageWidth"] = float(str(sku_package[1]))
        row["packageHeight"] = float(str(sku_package[2]))
        if "price" in expected:
            raw_sku_prices = expected.get("sku_prices")
            sku_price = (
                raw_sku_prices.get(variant, expected["price"])
                if isinstance(raw_sku_prices, Mapping)
                else expected["price"]
            )
            row["price"] = float(str(sku_price))
            row["priceIncludeVat"] = float(str(sku_price))
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


def _verify_target_readback(
    target: str,
    detail: Mapping[str, object],
    expected: Mapping[str, object],
    **kwargs: object,
) -> None:
    config = DIRECT_STORE_CONFIG.get(target)
    if (
        isinstance(config, Mapping)
        and config.get("verification_policy")
        == "submit_without_readback_validation"
    ):
        return
    _verify_expected_detail(detail, expected, **kwargs)


def _verify_expected_detail(
    detail: Mapping[str, object],
    expected: Mapping[str, object],
    *,
    platform: str = "tiktok",
    strict_collectbox_tiktok: bool = False,
    draft_mode: str = "shop",
    verify_price: bool = True,
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
        (
            str(detail.get("itemNum") or "") == expected["item_num"]
            or (
                platform == "tiktok"
                and strict_collectbox_tiktok
                and detail.get("itemNum") in (None, "")
            )
        ),
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
    expected_sku_commercial = expected.get("sku_commercial_facts")
    if isinstance(expected_sku_commercial, Mapping):
        checks.extend(
            [
                all(
                    _numbers_equal(
                        _mapping(normalized[key], "sku row").get("weight"),
                        _mapping(
                            expected_sku_commercial.get(key),
                            "SKU commercial fact",
                        ).get("weight"),
                    )
                    for key in wanted
                ),
                all(
                    all(
                        _numbers_equal(actual, wanted_value)
                        for actual, wanted_value in zip(
                            (
                                _mapping(normalized[key], "sku row").get(
                                    "packageLength"
                                ),
                                _mapping(normalized[key], "sku row").get(
                                    "packageWidth"
                                ),
                                _mapping(normalized[key], "sku row").get(
                                    "packageHeight"
                                ),
                            ),
                            _mapping(
                                expected_sku_commercial.get(key),
                                "SKU commercial fact",
                            ).get("package_cm") or (),
                        )
                    )
                    for key in wanted
                ),
            ]
        )
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
    if "price" in expected and verify_price:
        expected_sku_prices = expected.get("sku_prices")
        checks.append(
            all(
                _numbers_equal(
                    _mapping(normalized[key], "sku row").get("price"),
                    (
                        expected_sku_prices.get(key, expected["price"])
                        if isinstance(expected_sku_prices, Mapping)
                        else expected["price"]
                    ),
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
                        (
                            expected_sku_prices.get(key, expected["price"])
                            if isinstance(expected_sku_prices, Mapping)
                            else expected["price"]
                        ),
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
    if "product_attributes" in expected:
        checks.append(
            detail.get("productAttributes") == expected["product_attributes"]
        )
    if platform == "tiktok" and draft_mode == "site":
        shop_rows = detail.get("collectBoxDetailShopList")
        checks.extend(
            [
                str(detail.get("site") or "") == str(expected["region"]),
                str(detail.get("editModel") or "") == "site",
                isinstance(shop_rows, list)
                and len(shop_rows) == 1
                and isinstance(shop_rows[0], Mapping)
                and str(shop_rows[0].get("shopId") or "")
                == str(expected["shop_id"]),
                isinstance(shop_rows, list)
                and len(shop_rows) == 1
                and isinstance(shop_rows[0], Mapping)
                and str(shop_rows[0].get("brandId") or "")
                == TIKTOK_NO_BRAND_ID,
                isinstance(shop_rows, list)
                and len(shop_rows) == 1
                and isinstance(shop_rows[0], Mapping)
                and type(shop_rows[0].get("brandName")) is str
                and bool(str(shop_rows[0].get("brandName") or "").strip()),
            ]
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
        expected_sku_prices = expected.get("sku_prices")
        return all(
            _numbers_equal(
                row.get("priceIncludeVat"),
                (
                    expected_sku_prices.get(variant, expected["price"])
                    if isinstance(expected_sku_prices, Mapping)
                    else expected["price"]
                ),
            )
            for variant, row in zip(bindings, rows)
        )
    except (KeyError, TypeError, ValueError, MiaoshouOneClickPreDispatchError):
        return False


def _authoritative_tiktok_list_price_exact(
    post: Callable[[str, Mapping[str, object]], object],
    *,
    detail: Mapping[str, object],
    expected: Mapping[str, object],
    detail_id: int,
    target: str,
) -> bool:
    """Compare the approved price with Miaoshou's list/display authority.

    The site-detail endpoint can echo the submitted SKU price without changing
    the price displayed by Miaoshou's collect-box list.  The documented
    ``search_collect_box_detail_list`` row ``price`` is therefore required
    after the web batch mutation.  Identity is bound before the price is read;
    no title, ordering, or fuzzy matching is permitted.
    """

    expected_source_offer_id = expected.get("source_offer_id")
    if (
        isinstance(expected_source_offer_id, bool)
        or not str(expected_source_offer_id or "").isdecimal()
        or int(expected_source_offer_id) <= 0
    ):
        raise MiaoshouOneClickPreDispatchError(
            "approved TikTok source identity is unavailable"
        )
    source_offer_id = str(int(expected_source_offer_id))
    # Miaoshou's live site/shop detail payloads do not echo the upstream
    # source offer.  Validate it when the vendor supplies it, but bind an
    # opaque response through the exact source-filtered query below plus the
    # server-owned COMMON/detail/shop identities.
    _verify_tiktok_detail_source_identity(detail, expected)
    pages = read_source_offer_pages(
        source_offer_id,
        post=post,
        target=target,
    )
    matches: list[Mapping[str, object]] = []
    for page in pages:
        for row in page["data"]["detailList"]:
            raw_detail_id = row.get("collectBoxDetailId") or row.get(
                "detailId"
            )
            if (
                isinstance(raw_detail_id, bool)
                or not str(raw_detail_id or "").isdecimal()
                or int(raw_detail_id) <= 0
            ):
                raise MiaoshouOneClickPreDispatchError(
                    "authoritative TikTok list detail identity is malformed"
                )
            if int(raw_detail_id) != detail_id:
                continue

            raw_common_id = row.get("commonCollectBoxDetailId")
            if (
                isinstance(raw_common_id, bool)
                or not str(raw_common_id or "").isdecimal()
                or str(int(raw_common_id))
                != str(expected["common_detail_id"])
            ):
                raise MiaoshouOneClickPreDispatchError(
                    "authoritative TikTok list COMMON identity drifted"
                )
            observed_sources: set[str] = set()
            for field in (
                "sourceOfferId",
                "sourceItemId",
                "sourceProductId",
            ):
                value = row.get(field)
                if value is None:
                    continue
                if (
                    isinstance(value, bool)
                    or not str(value).isdecimal()
                    or int(value) <= 0
                ):
                    raise MiaoshouOneClickPreDispatchError(
                        "authoritative TikTok list source identity is malformed"
                    )
                observed_sources.add(str(int(value)))
            if observed_sources and observed_sources != {source_offer_id}:
                raise MiaoshouOneClickPreDispatchError(
                    "authoritative TikTok list source identity drifted"
                )

            expected_site = str(expected["region"]).upper()
            raw_observed_site = row.get("site") or row.get("region")
            observed_site = (
                str(raw_observed_site).upper()
                if raw_observed_site is not None
                else None
            )
            shop_rows = row.get("collectBoxDetailShopList")
            if not isinstance(shop_rows, list) or any(
                not isinstance(item, Mapping) for item in shop_rows
            ):
                raise MiaoshouOneClickPreDispatchError(
                    "authoritative TikTok list shop identity is malformed"
                )
            shop_ids = [str(item.get("shopId") or "") for item in shop_rows]
            observed_shop_sites = {
                str(item.get("site")).upper()
                for item in shop_rows
                if item.get("site") is not None
            }
            if (
                (observed_site is not None and observed_site != expected_site)
                or len(shop_ids) != 1
                or len(set(shop_ids)) != 1
                or set(shop_ids) != {str(expected["shop_id"])}
                or (
                    observed_shop_sites
                    and observed_shop_sites != {expected_site}
                )
            ):
                raise MiaoshouOneClickPreDispatchError(
                    "authoritative TikTok list target identity drifted"
                )
            matches.append(row)

    if len(matches) != 1:
        raise MiaoshouOneClickPreDispatchError(
            "authoritative TikTok list row is unavailable or ambiguous"
        )
    raw_price = matches[0].get("price")
    if isinstance(raw_price, bool) or raw_price is None:
        raise MiaoshouOneClickPreDispatchError(
            "authoritative TikTok list price is malformed"
        )
    try:
        observed_price = Decimal(str(raw_price))
        approved_price = Decimal(str(expected["price"]))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise MiaoshouOneClickPreDispatchError(
            "authoritative TikTok list price is malformed"
        ) from error
    if (
        not observed_price.is_finite()
        or observed_price <= 0
        or not approved_price.is_finite()
        or approved_price <= 0
    ):
        raise MiaoshouOneClickPreDispatchError(
            "authoritative TikTok list price is malformed"
        )
    return observed_price == approved_price


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
    *,
    detail_id: int,
    site: str,
    price: object,
    sku_prices: object = None,
) -> dict[str, object]:
    approved_price = _positive_decimal(price, "approved TikTok price")
    if sku_prices is not None:
        if not isinstance(sku_prices, Mapping) or not sku_prices:
            raise MiaoshouOneClickPreDispatchError(
                "approved TikTok SKU prices are malformed"
            )
        approved_sku_prices = {
            _positive_decimal(value, "approved TikTok SKU price")
            for value in sku_prices.values()
        }
        if len(approved_sku_prices) != 1:
            raise MiaoshouOneClickPreDispatchError(
                "Miaoshou batch price repair cannot preserve distinct SKU prices"
            )
        approved_price = next(iter(approved_sku_prices))
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
    raw_model_skus = expected.get("model_skus")
    raw_sku_prices = expected.get("sku_prices")
    if not isinstance(raw_model_skus, Mapping):
        return False
    if raw_sku_prices is not None:
        if not isinstance(raw_sku_prices, Mapping) or set(raw_sku_prices) != set(
            raw_model_skus
        ):
            return False
        approved_price_by_model_sku = {
            str(raw_model_skus[variant]): raw_sku_prices[variant]
            for variant in raw_model_skus
        }
    else:
        approved_price_by_model_sku = {
            str(model_sku): expected["price"]
            for model_sku in raw_model_skus.values()
        }
    if len(approved_price_by_model_sku) != len(raw_model_skus):
        return False
    observed_price_by_model_sku = {
        str(row.get("seller_sku") or ""): _mapping(
            row.get("price"), "price"
        ).get("sale_price")
        for row in skus
    }
    prices_ok = set(observed_price_by_model_sku) == set(
        approved_price_by_model_sku
    ) and all(
        _numbers_equal(
            observed_price_by_model_sku[model_sku],
            approved_price,
        )
        for model_sku, approved_price in approved_price_by_model_sku.items()
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

    return MiaoshouRuntimeTransport(
        post=post_open,
        enforce_publish_pacing=True,
    )


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
    shop_id: object,
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
    draft_mode = (
        str(config.get("draft_mode") or "shop")
        if isinstance(config, Mapping)
        else "shop"
    )
    if platform == "tiktok" and draft_mode == "site":
        body = {
            "detailId": int(detail_id),
            "site": str(config["site"]),
        }
        data_field = "siteCollectItemInfo"
    elif platform == "tiktok" and draft_mode == "shop":
        body = {"detailId": int(detail_id), "shopId": shop_id}
        data_field = "shopCollectItemInfo"
    elif platform == "tiktok":
        raise MiaoshouOneClickPreDispatchError(
            "TikTok draft mode is invalid"
        )
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


def _verify_tiktok_detail_source_identity(
    detail: Mapping[str, object], expected: Mapping[str, object]
) -> None:
    expected_source = expected.get("source_offer_id")
    observed_source = detail.get("sourceOfferId")
    if (
        type(expected_source) is not str
        or not expected_source.isascii()
        or not expected_source.isdecimal()
        or int(expected_source) <= 0
    ):
        raise MiaoshouOneClickPreDispatchError(
            "approved TikTok source offer identity is invalid"
        )
    if observed_source is None:
        return
    if (
        isinstance(observed_source, bool)
        or not str(observed_source).isdecimal()
        or int(observed_source) <= 0
        or str(int(observed_source)) != expected_source
    ):
        raise MiaoshouOneClickPreDispatchError(
            "TikTok source offer identity drifted before update"
        )


def _verify_site_variants(
    detail: Mapping[str, object], expected: Mapping[str, object]
) -> None:
    _approved_variant_key_bindings(detail, expected)


def _approved_variant_key_bindings(
    detail: Mapping[str, object], expected: Mapping[str, object]
) -> dict[str, object]:
    """Bind Miaoshou's raw SKU-map keys to approved variants exactly.

    Some claimed TikTok drafts replace the human-readable variant key with
    opaque attrValue IDs and temporarily repeat the source offer ID in every
    ``itemNum``.  Exact raw-key matching remains preferred.  The next
    authority is the draft's own skuPropertyList, which losslessly maps those
    opaque IDs back to the approved variant labels.  Unique model SKU matching
    is retained only as the final exact fallback.  Position, title and fuzzy
    matching are never used.
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

    property_labels: dict[str, str] = {}
    raw_properties = detail.get("skuPropertyList")
    if isinstance(raw_properties, list) and all(
        isinstance(prop, Mapping) for prop in raw_properties
    ):
        for prop in raw_properties:
            raw_values = prop.get("attrValueList")
            if not isinstance(raw_values, list) or any(
                not isinstance(value, Mapping) for value in raw_values
            ):
                property_labels = {}
                break
            for value in raw_values:
                value_id = value.get("attrValueId")
                label = value.get("attrValue")
                if (
                    type(value_id) is not str
                    or not value_id.strip()
                    or type(label) is not str
                    or not label.strip()
                    or value_id.strip() in property_labels
                ):
                    property_labels = {}
                    break
                property_labels[value_id.strip()] = label.strip()
            if not property_labels:
                break
    if property_labels:
        raw_by_property_signature: dict[str, object] = {}
        for raw_key in sku_map:
            if type(raw_key) is not str:
                raw_by_property_signature = {}
                break
            value_ids = [value for value in raw_key.split(";") if value]
            if not value_ids or any(
                value_id not in property_labels for value_id in value_ids
            ):
                raw_by_property_signature = {}
                break
            signature = _normalize_variant(
                ";".join(property_labels[value_id] for value_id in value_ids)
            )
            if not signature or signature in raw_by_property_signature:
                raw_by_property_signature = {}
                break
            raw_by_property_signature[signature] = raw_key
        if set(raw_by_property_signature) == set(variants):
            return {
                variant: raw_by_property_signature[variant]
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
        "site": detail.get("site"),
        "edit_model": detail.get("editModel"),
        "site_shops": detail.get("collectBoxDetailShopList"),
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
