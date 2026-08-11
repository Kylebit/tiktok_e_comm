"""Live provider dependencies for the frozen-v4 publication executors.

The module is deliberately a thin production edge.  Product copy, SKU facts,
prices, parcel data and target selection come only from the validated
``approved-publication-snapshot/v4`` passed to an executor.  The adapters add
provider identity, official category/readback facts and HTTP transport only.

No adapter starts another platform, reads a dashboard, or repairs an approved
snapshot.  Missing official facts fail closed before a provider write.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from domains.product_operations import (
    ApprovedPublicationSnapshotError,
    validate_approved_publication_snapshot,
)
from modules.miaoshou.client import post_open
from modules.miaoshou.oneclick_release import CATEGORY_TREE_PATH, SOURCE_LIST_PATH
from modules.miaoshou.tiktok_publisher import (
    CATEGORY_METADATA_PATH,
    EXPECTED_SHOP_ID_BY_TARGET,
    production_tiktok_publisher,
)
from modules.miaoshou.tiktok_v4_drafts import (
    DraftWriteFact,
    MiaoshouOpenApiTikTokV4DraftTransport,
    TikTokV4DraftTransport,
    prepare_tiktok_v4_drafts,
)
from modules.ozon.approved_publication_v4 import OzonDispatchFact
from modules.ozon.client import ozon_post
from modules.shopee.client import merchant_get
from modules.catalog.sku_key import parse_search_key
from modules.shopee.global_sku_map import load_map
from modules.shopee.global_v4_executor import ShopeeGlobalV4Resolver
from modules.shopee.global_v4_live_runtime import (
    build_official_shopee_global_v4_runtime,
)
from modules.shopee.skill_regions import (
    OfficialShopeeRegionRuntime,
    REGIONAL_TARGETS,
    ShopeeRegionRuntime,
    selected_region_targets,
)
from shared_platform.product_publication_executors import (
    OzonV4ExecutorDependencies,
    ShopeeRegionExecutorDependencies,
    TikTokV4ExecutorDependencies,
)


TIKTOK_CATEGORY_RESOLUTION_SCHEMA = "tiktok-official-category-resolution/v1"
MIAOSHOU_TIKTOK_SEED_IDENTITY_SCHEMA = (
    "miaoshou-tiktok-v4-seed-identity/v2"
)
MIAOSHOU_COMMON_LIST_PATH = (
    "/open/v1/product/common_collect_box/common_collect_box/"
    "get_common_collect_box_list"
)
MIAOSHOU_TIKTOK_LIST_PATH = SOURCE_LIST_PATH
OZON_IMPORT_PATH = "/v3/product/import"
OZON_READBACK_PATH = "/v3/product/info/list"
OZON_CATEGORY_TREE_PATH = "/v1/description-category/tree"
OZON_CATEGORY_ATTRIBUTES_PATH = "/v1/description-category/attribute"
OZON_ATTRIBUTE_VALUES_SEARCH_PATH = (
    "/v1/description-category/attribute/values/search"
)

ProviderPost = Callable[[str, dict[str, object]], Mapping[str, object]]
ShopeeMerchantGet = Callable[
    [str, int, str, dict[str, object]], Mapping[str, object]
]
OzonPost = Callable[[str, dict[str, object]], Mapping[str, object]]
OzonImportItemBuilder = Callable[[Mapping[str, Any]], Mapping[str, object]]
TikTokDraftTransportFactory = Callable[
    [object, Callable[[str, DraftWriteFact], None]], TikTokV4DraftTransport
]
TikTokDraftSeedIdentityResolver = Callable[[object], Mapping[str, object]]


class LivePublicationDependencyError(ValueError):
    """Provider identity or immutable execution facts are incomplete."""


_TIKTOK_SITE_BY_TARGET = {
    "tiktok:LH_PH": "PH",
    "tiktok:LH_MY": "MY",
    "tiktok:LH_TH": "TH",
    "tiktok:LH_VN": "VN",
    "tiktok:HB_PH": "PH",
    "tiktok:HB_MY": "MY",
    "tiktok:HB_TH": "TH",
    "tiktok:HB_VN": "VN",
    "tiktok:MX": "MX",
    "tiktok:GB": "GB",
}

# Exact semantic aliases only.  They are read from the frozen main category;
# product title/description are intentionally excluded from category choice.
_TIKTOK_EXACT_CATEGORY_PROFILES = (
    {
        "aliases": frozenset(
            {
                "refrigeratormagnet",
                "refrigeratormagnets",
                "fridgemagnet",
                "fridgemagnets",
                "冰箱贴",
            }
        ),
        "primary": "854536",
        "fallbacks": (),
    },
    {
        "aliases": frozenset(
            {
                "tablecloth",
                "tablecloths",
                "tablerunner",
                "tablerunners",
                "kitchenlinen",
                "kitchenlinens",
                "桌布",
                "桌旗",
                "桌布桌旗",
            }
        ),
        "primary": "600204",
        # This single fallback was explicitly approved by Kyle.  It is not a
        # generic nearest-category rule.
        "fallbacks": ("600009",),
    },
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _semantic_key(value: object) -> str:
    if type(value) is not str:
        return ""
    return "".join(re.findall(r"[a-z0-9\u3400-\u9fff]+", value.casefold()))


def _provider_data(response: object, operation: str) -> Mapping[str, object]:
    if not isinstance(response, Mapping):
        raise LivePublicationDependencyError(f"{operation} response is malformed")
    error = str(response.get("error") or "").strip()
    if error and error != "-":
        raise LivePublicationDependencyError(f"{operation} was rejected")
    result = str(response.get("result") or "").strip().casefold()
    if result and result not in {"success", "ok"}:
        raise LivePublicationDependencyError(f"{operation} was rejected")
    code = response.get("code")
    normalized_code = code.casefold().strip() if type(code) is str else code
    if normalized_code not in {None, 0, "0", 200, "200", "success", "ok"}:
        raise LivePublicationDependencyError(f"{operation} was rejected")
    data = response.get("data")
    if not isinstance(data, Mapping):
        raise LivePublicationDependencyError(f"{operation} response is malformed")
    return data


def _main_category_semantics(product: Mapping[str, object]) -> set[str]:
    category = product.get("main_category")
    if not isinstance(category, Mapping):
        raise LivePublicationDependencyError("exact frozen main category is unavailable")
    values: list[object] = [category.get("id"), category.get("name")]
    path = category.get("path")
    if isinstance(path, Sequence) and not isinstance(path, (str, bytes, bytearray)):
        for row in path:
            if isinstance(row, Mapping):
                values.extend((row.get("id"), row.get("name")))
    # A frozen category may carry its exact breadcrumb in ``name`` instead of
    # a structured ``path``.  Treat each explicitly delimited component as an
    # exact semantic value; do not infer from product copy or use substrings.
    for value in tuple(values):
        if type(value) is str:
            parts = re.split(r"\s*(?:>|›|→|/)\s*", value)
            if len(parts) > 1:
                values.extend(part for part in parts if part)
    semantics = {_semantic_key(value) for value in values}
    semantics.discard("")
    if not semantics:
        raise LivePublicationDependencyError("exact frozen main category is unavailable")
    return semantics


def _tiktok_profile(product: Mapping[str, object]) -> Mapping[str, object]:
    semantics = _main_category_semantics(product)
    matches = [
        profile
        for profile in _TIKTOK_EXACT_CATEGORY_PROFILES
        if semantics.intersection(profile["aliases"])
    ]
    if len(matches) != 1:
        raise LivePublicationDependencyError(
            "exact frozen main category has no unique TikTok mapping"
        )
    return matches[0]


def _node_id(raw_key: object, node: Mapping[str, object]) -> str:
    value = node.get("cid", node.get("category_id", raw_key))
    if isinstance(value, bool):
        return ""
    text = str(value or "").strip()
    return text if text.isdigit() and int(text) > 0 else ""


def _node_name(node: Mapping[str, object], node_id: str) -> str:
    for field in ("name", "nameEnglish", "nameChinese", "category_name"):
        value = node.get(field)
        if type(value) is str and value.strip():
            return value.strip()
    return node_id


def _tree_entries(value: object) -> list[tuple[object, Mapping[str, object]]]:
    if isinstance(value, Mapping):
        return [
            (key, row)
            for key, row in value.items()
            if isinstance(row, Mapping)
        ]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            (index, row)
            for index, row in enumerate(value)
            if isinstance(row, Mapping)
        ]
    return []


def _find_category_node(
    tree: object,
    category_id: str,
    *,
    parent_path: tuple[dict[str, str], ...] = (),
) -> tuple[Mapping[str, object], list[dict[str, str]]] | None:
    for raw_key, node in _tree_entries(tree):
        node_id = _node_id(raw_key, node)
        path = parent_path
        if node_id:
            path = (*parent_path, {"id": node_id, "name": _node_name(node, node_id)})
            if node_id == category_id:
                return node, list(path)
        children = node.get("children", node.get("child", node.get("categories")))
        found = _find_category_node(children, category_id, parent_path=path)
        if found is not None:
            return found
    return None


class OfficialMiaoshouTikTokCategoryResolver:
    """Resolve one exact frozen product family through official site facts."""

    def __init__(self, *, post: ProviderPost = post_open) -> None:
        if not callable(post):
            raise TypeError("TikTok category transport must be callable")
        self._post = post

    def resolve(
        self,
        *,
        target: dict[str, str],
        product: dict[str, object],
        skus: list[dict[str, object]],
    ) -> Mapping[str, object]:
        del skus  # SKU facts cannot influence product category selection.
        label = str(target.get("target_label") or "").strip()
        site = _TIKTOK_SITE_BY_TARGET.get(label)
        expected_suffix = label.split(":", 1)[1] if ":" in label else ""
        if (
            site is None
            or target.get("platform") != "tiktok"
            or target.get("site") != expected_suffix
            or target.get("store") != expected_suffix
        ):
            raise LivePublicationDependencyError("TikTok target identity is invalid")
        shop_id = EXPECTED_SHOP_ID_BY_TARGET.get(label)
        if not shop_id or not str(shop_id).isdigit():
            raise LivePublicationDependencyError("TikTok shop identity is unavailable")

        profile = _tiktok_profile(product)
        data = _provider_data(
            self._post(CATEGORY_TREE_PATH, {"site": site}),
            "TikTok official category tree",
        )
        tree = data.get("cateTree", data.get("categoryTree"))
        if not isinstance(tree, (Mapping, list, tuple)):
            raise LivePublicationDependencyError(
                "TikTok official category tree is malformed"
            )

        selected: tuple[str, Mapping[str, object], list[dict[str, str]]] | None = None
        resolution = "EXACT"
        for index, category_id in enumerate(
            (profile["primary"], *profile["fallbacks"])
        ):
            found = _find_category_node(tree, str(category_id))
            if found is None:
                continue
            node, path = found
            if node.get("disabled") is not False:
                continue
            selected = (str(category_id), node, path)
            resolution = "EXACT" if index == 0 else "USER_APPROVED_FALLBACK"
            break
        if selected is None:
            raise LivePublicationDependencyError(
                "exact frozen TikTok category is not enabled for this site"
            )

        category_id, node, path = selected
        metadata_data = _provider_data(
            self._post(
                CATEGORY_METADATA_PATH,
                {
                    "site": site,
                    "cid": int(category_id),
                    "shopIds": [int(str(shop_id))],
                },
            ),
            "TikTok official category metadata",
        )
        metadata = metadata_data.get("categoryMetadata")
        rules = (
            metadata.get("categoryProductAttrList")
            if isinstance(metadata, Mapping)
            else None
        )
        if not isinstance(rules, list) or any(
            not isinstance(row, Mapping) for row in rules
        ):
            raise LivePublicationDependencyError(
                "TikTok official category metadata is malformed"
            )
        category = {
            "id": category_id,
            "name": _node_name(node, category_id),
            "path": path,
        }
        body: dict[str, object] = {
            "schema_version": TIKTOK_CATEGORY_RESOLUTION_SCHEMA,
            "target_label": label,
            "category": category,
            "enabled": True,
            "metadata_valid": True,
            "resolution": resolution,
        }
        body["evidence_digest"] = "sha256:" + _digest(body)
        return body


class TikTokUnavailableStorefrontReadback:
    """Truthfully represent the current lack of authoritative storefront GET."""

    def readback(
        self,
        *,
        command: Mapping[str, object],
        dispatch: Mapping[str, object],
    ) -> Mapping[str, object]:
        del dispatch
        label = str(command.get("target_label") or "").strip()
        if label not in _TIKTOK_SITE_BY_TARGET:
            raise LivePublicationDependencyError("TikTok readback target is invalid")
        return {
            "target_label": label,
            "authority": "UNAVAILABLE",
            "status": "UNAVAILABLE",
            "exact": False,
        }


_SAFE_RUN_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CHECKPOINT_SCHEMA = "tiktok-v4-draft-checkpoint/v1"


def _checkpoint_identity(request: object) -> dict[str, object]:
    snapshot = _request_snapshot(request, "TIKTOK")
    run_id = str(getattr(request, "run_id", "") or "").strip()
    offer_id = str(snapshot.get("offer_id") or "").strip()
    revision = snapshot.get("product_revision")
    digest = str(snapshot.get("snapshot_digest") or "").strip()
    if (
        not _SAFE_RUN_PART.fullmatch(run_id)
        or not offer_id.isdigit()
        or type(revision) is not int
        or revision <= 0
        or not digest.startswith("sha256:")
        or len(digest) != 71
    ):
        raise LivePublicationDependencyError(
            "TikTok checkpoint run identity is invalid"
        )
    return {
        "run_id": run_id,
        "offer_id": offer_id,
        "product_revision": revision,
        "snapshot_digest": digest,
    }


class TikTokV4DraftCheckpointStore:
    """Atomic credential-free checkpoint below an offer/revision/run path."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    def _path(self, request: object) -> Path:
        identity = _checkpoint_identity(request)
        return self.root.joinpath(
            str(identity["offer_id"]),
            str(identity["product_revision"]),
            str(identity["run_id"]),
            "tiktok-draft-checkpoint.json",
        )

    def _empty(self, request: object) -> dict[str, object]:
        return {
            "schema_version": _CHECKPOINT_SCHEMA,
            **_checkpoint_identity(request),
            "events": [],
            "receipt": None,
            "external_write_count": 0,
        }

    @staticmethod
    def _verified(raw: object, request: object) -> dict[str, object]:
        if not isinstance(raw, Mapping):
            raise LivePublicationDependencyError("TikTok checkpoint is malformed")
        checkpoint = deepcopy(dict(raw))
        supplied = checkpoint.pop("checkpoint_digest", None)
        if supplied != "sha256:" + _digest(checkpoint):
            raise LivePublicationDependencyError("TikTok checkpoint was modified")
        expected = _checkpoint_identity(request)
        if (
            checkpoint.get("schema_version") != _CHECKPOINT_SCHEMA
            or any(checkpoint.get(key) != value for key, value in expected.items())
            or not isinstance(checkpoint.get("events"), list)
            or any(not isinstance(row, Mapping) for row in checkpoint["events"])
        ):
            raise LivePublicationDependencyError(
                "TikTok checkpoint identity conflicts"
            )
        checkpoint["checkpoint_digest"] = supplied
        return checkpoint

    def load(self, request: object) -> dict[str, object]:
        path = self._path(request)
        if not path.is_file():
            return self._empty(request)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LivePublicationDependencyError(
                "TikTok checkpoint cannot be read"
            ) from error
        return self._verified(raw, request)

    def _write(self, request: object, checkpoint: Mapping[str, object]) -> None:
        path = self._path(request)
        body = deepcopy(dict(checkpoint))
        body.pop("checkpoint_digest", None)
        body["checkpoint_digest"] = "sha256:" + _digest(body)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                delete=False,
                dir=path.parent,
                prefix=".tiktok-draft-",
                suffix=".tmp",
            ) as handle:
                temporary = handle.name
                handle.write(_canonical_json(body) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and Path(temporary).exists():
                Path(temporary).unlink()

    @staticmethod
    def _write_count(events: Sequence[Mapping[str, object]]) -> int | None:
        if any(row.get("outcome") == "UNKNOWN" for row in events):
            return None
        return sum(
            row.get("outcome") == "ACCEPTED"
            and row.get("operation") != "IDENTITY_OBSERVED"
            for row in events
        )

    def record_fact(
        self, request: object, target_label: str, fact: DraftWriteFact
    ) -> None:
        if type(fact) is not DraftWriteFact:
            raise TypeError("TikTok checkpoint fact is invalid")
        if target_label not in EXPECTED_SHOP_ID_BY_TARGET:
            raise LivePublicationDependencyError(
                "TikTok checkpoint target identity is invalid"
            )
        expected_shop = str(EXPECTED_SHOP_ID_BY_TARGET[target_label])
        if fact.shop_id is not None and fact.shop_id != expected_shop:
            raise LivePublicationDependencyError(
                "TikTok checkpoint shop identity conflicts"
            )
        checkpoint = self.load(request)
        events = list(checkpoint["events"])
        events.append(
            {
                "target_label": target_label,
                "operation": fact.operation,
                "outcome": fact.outcome,
                "detail_id": fact.detail_id,
                "shop_id": fact.shop_id,
            }
        )
        checkpoint["events"] = events
        checkpoint["external_write_count"] = self._write_count(events)
        self._write(request, checkpoint)

    def finalize(self, request: object, receipt: Mapping[str, object]) -> None:
        checkpoint = self.load(request)
        checkpoint["receipt"] = deepcopy(dict(receipt))
        observed_count = self._write_count(checkpoint["events"])
        receipt_count = receipt.get("external_write_count")
        checkpoint["external_write_count"] = (
            None if receipt_count is None else observed_count
        )
        self._write(request, checkpoint)

    def target_events(
        self, request: object, target_label: str
    ) -> list[Mapping[str, object]]:
        return [
            row
            for row in self.load(request)["events"]
            if row.get("target_label") == target_label
        ]


def _fact_from_event(
    event: Mapping[str, object], *, operation: str | None = None
) -> DraftWriteFact:
    return DraftWriteFact(
        operation or str(event.get("operation") or "CLAIM_OR_CREATE"),
        str(event.get("outcome") or "UNKNOWN"),
        detail_id=event.get("detail_id"),
        shop_id=event.get("shop_id"),
    )


class _ResumableTikTokDraftTransport:
    def __init__(
        self,
        *,
        request: object,
        store: TikTokV4DraftCheckpointStore,
        transport: TikTokV4DraftTransport,
    ) -> None:
        self._request = request
        self._store = store
        self._transport = transport

    def claim_or_create(
        self, *, target: Mapping[str, object], ordinal: int
    ) -> DraftWriteFact | Sequence[DraftWriteFact]:
        label = str(target.get("target_label") or "")
        events = self._store.target_events(self._request, label)
        claim_events = [row for row in events if row.get("operation") != "SAVE_DRAFT"]
        if claim_events:
            if any(row.get("outcome") == "UNKNOWN" for row in claim_events):
                identity = next(
                    (
                        row
                        for row in reversed(claim_events)
                        if row.get("detail_id") and row.get("shop_id")
                    ),
                    {},
                )
                return DraftWriteFact(
                    "CLAIM_OR_CREATE",
                    "UNKNOWN",
                    detail_id=identity.get("detail_id"),
                    shop_id=identity.get("shop_id"),
                )
            accepted = [
                row
                for row in claim_events
                if row.get("outcome") == "ACCEPTED"
                and row.get("detail_id")
                and row.get("shop_id")
            ]
            claim_accepted = any(
                row.get("operation")
                in {"IDENTITY_OBSERVED", "CLAIM_TO_SHOP", "CLAIM_OR_CREATE"}
                and row.get("outcome") == "ACCEPTED"
                for row in claim_events
            )
            if accepted and claim_accepted:
                return _fact_from_event(accepted[-1], operation="CLAIM_OR_CREATE")
            # A create response was durably observed but the following claim
            # was not.  Creating another draft blindly would duplicate work.
            if accepted:
                return DraftWriteFact(
                    "CLAIM_OR_CREATE",
                    "UNKNOWN",
                    detail_id=accepted[-1].get("detail_id"),
                    shop_id=accepted[-1].get("shop_id"),
                )
        return self._transport.claim_or_create(target=target, ordinal=ordinal)

    def save_draft(
        self,
        *,
        identity: Mapping[str, str],
        draft: Mapping[str, object],
    ) -> DraftWriteFact:
        label = str(identity.get("target_label") or "")
        saves = [
            row
            for row in self._store.target_events(self._request, label)
            if row.get("operation") == "SAVE_DRAFT"
        ]
        if any(row.get("outcome") == "UNKNOWN" for row in saves):
            return _fact_from_event(saves[-1])
        accepted = [row for row in saves if row.get("outcome") == "ACCEPTED"]
        if accepted:
            return _fact_from_event(accepted[-1])
        return self._transport.save_draft(identity=identity, draft=draft)


class DurableTikTokV4DraftPreparer:
    """Prepare v4 drafts with durable identity and external-write evidence."""

    def __init__(
        self,
        *,
        checkpoint_store: TikTokV4DraftCheckpointStore,
        category_resolver: object,
        transport_factory: TikTokDraftTransportFactory,
    ) -> None:
        if not callable(transport_factory):
            raise TypeError("TikTok v4 draft transport factory must be callable")
        self._store = checkpoint_store
        self._category_resolver = category_resolver
        self._transport_factory = transport_factory

    def __call__(self, request: object) -> Mapping[str, object]:
        snapshot = _request_snapshot(request, "TIKTOK")

        def observe(target_label: str, fact: DraftWriteFact) -> None:
            self._store.record_fact(request, target_label, fact)

        transport = self._transport_factory(request, observe)
        resumable = _ResumableTikTokDraftTransport(
            request=request,
            store=self._store,
            transport=transport,
        )
        receipt = prepare_tiktok_v4_drafts(
            snapshot,
            category_resolver=self._category_resolver,
            transport=resumable,
        )
        checkpoint = self._store.load(request)
        receipt = deepcopy(dict(receipt))
        receipt["external_write_count"] = (
            None
            if receipt.get("external_write_count") is None
            else checkpoint["external_write_count"]
        )
        body = dict(receipt)
        body.pop("receipt_digest", None)
        receipt["receipt_digest"] = "sha256:" + _digest(body)
        self._store.finalize(request, receipt)
        return receipt

    def write_count(self, request: object) -> int | None:
        """Return durable mutation certainty even if preparation raised."""

        return self._store.load(request)["external_write_count"]


def _positive_provider_id(value: object, name: str) -> str:
    if isinstance(value, bool):
        raise LivePublicationDependencyError(f"{name} is malformed")
    rendered = str(value or "").strip()
    if not rendered.isdigit() or int(rendered) <= 0:
        raise LivePublicationDependencyError(f"{name} is malformed")
    return str(int(rendered))


def _seed_source_ids(row: Mapping[str, object], operation: str) -> set[str]:
    values: list[object] = []
    for field in ("sourceOfferId", "sourceItemId", "sourceProductId"):
        if field in row:
            values.append(row.get(field))
    if "sourceList" in row:
        source_list = row.get("sourceList")
        if not isinstance(source_list, list) or any(
            not isinstance(item, Mapping) for item in source_list
        ):
            raise LivePublicationDependencyError(
                f"{operation} source identity is malformed"
            )
        for item in source_list:
            for field in ("sourceOfferId", "sourceItemId", "sourceProductId"):
                if field in item:
                    values.append(item.get(field))
    if not values:
        raise LivePublicationDependencyError(
            f"{operation} source identity is unavailable"
        )
    return {
        _positive_provider_id(value, f"{operation} source identity")
        for value in values
    }


def _seed_has_source_identity(row: Mapping[str, object]) -> bool:
    return any(
        field in row
        for field in (
            "sourceOfferId",
            "sourceItemId",
            "sourceProductId",
            "sourceList",
        )
    )


def _seed_list_rows(
    *,
    post: ProviderPost,
    path: str,
    source_offer_id: str,
    common_list: bool,
    page_size: int,
    max_pages: int,
) -> list[Mapping[str, object]]:
    rows: list[Mapping[str, object]] = []
    for page_no in range(1, max_pages + 1):
        filters: dict[str, object] = {"sourceItemIdKeyword": source_offer_id}
        if common_list:
            filters = {"tabPaneName": "all", **filters}
        data = _provider_data(
            post(
                path,
                {
                    "pageNo": page_no,
                    "pageSize": page_size,
                    "filter": filters,
                },
            ),
            "Miaoshou seed identity list",
        )
        page_rows = data.get("detailList", data.get("list"))
        if not isinstance(page_rows, list) or any(
            not isinstance(row, Mapping) for row in page_rows
        ):
            raise LivePublicationDependencyError(
                "Miaoshou seed identity list is malformed"
            )
        rows.extend(deepcopy(list(page_rows)))

        total_present = "totalCount" in data or "total" in data
        total = data.get("totalCount", data.get("total"))
        if total_present and (
            isinstance(total, bool) or type(total) is not int or total < 0
        ):
            raise LivePublicationDependencyError(
                "Miaoshou seed identity pagination is malformed"
            )
        has_next_present = "hasNextPage" in data
        has_next = data.get("hasNextPage")
        if has_next_present and type(has_next) is not bool:
            raise LivePublicationDependencyError(
                "Miaoshou seed identity pagination is malformed"
            )
        if (
            (has_next_present and has_next is False)
            or (total_present and len(rows) >= total)
            or (
                not has_next_present
                and not total_present
                and len(page_rows) < page_size
            )
        ):
            return rows
    raise LivePublicationDependencyError(
        "Miaoshou seed identity pagination is incomplete"
    )


def _platform_claim_evidence(
    row: Mapping[str, object],
) -> tuple[bool, set[str]]:
    evidence_seen = False
    observed_shop_ids: set[str] = set()
    if "collectBoxDetailShopList" in row:
        evidence_seen = True
        shops = row.get("collectBoxDetailShopList")
        if not isinstance(shops, list) or any(
            not isinstance(item, Mapping) for item in shops
        ):
            raise LivePublicationDependencyError(
                "TikTok platform claim identity is malformed"
            )
        for item in shops:
            observed_shop_ids.add(
                _positive_provider_id(
                    item.get("shopId"), "TikTok platform shop identity"
                )
            )
        if len(observed_shop_ids) != len(shops):
            raise LivePublicationDependencyError(
                "TikTok platform claim identity is ambiguous"
            )
    for field in ("claimToShopIds", "shopIds"):
        if field not in row:
            continue
        evidence_seen = True
        values = row.get(field)
        if not isinstance(values, list):
            raise LivePublicationDependencyError(
                "TikTok platform claim identity is malformed"
            )
        normalized = {
            _positive_provider_id(value, "TikTok platform shop identity")
            for value in values
        }
        if len(normalized) != len(values):
            raise LivePublicationDependencyError(
                "TikTok platform claim identity is ambiguous"
            )
        observed_shop_ids.update(normalized)
    explicit = row.get("claimed") if "claimed" in row else None
    if explicit is not None:
        evidence_seen = True
        if type(explicit) is not bool:
            raise LivePublicationDependencyError(
                "TikTok platform claim identity is malformed"
            )
        if explicit is False and observed_shop_ids:
            raise LivePublicationDependencyError(
                "TikTok platform claim identity conflicts"
            )
    if not evidence_seen:
        raise LivePublicationDependencyError(
            "TikTok platform claim identity is unavailable; reconciliation required"
        )
    return bool(observed_shop_ids) or explicit is True, observed_shop_ids


def _platform_claimed(row: Mapping[str, object]) -> bool:
    return _platform_claim_evidence(row)[0]


def _created_sort_key(row: Mapping[str, object]) -> tuple[float, int]:
    raw = row.get("gmtCreate")
    if type(raw) is not str or not raw.strip():
        raise LivePublicationDependencyError(
            "TikTok platform creation time is malformed"
        )
    value = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise LivePublicationDependencyError(
            "TikTok platform creation time is malformed"
        ) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    detail_id = _positive_provider_id(
        row.get("collectBoxDetailId", row.get("detailId")),
        "TikTok platform detail identity",
    )
    return parsed.timestamp(), int(detail_id)


class OfficialMiaoshouTikTokV4SeedIdentityResolver:
    """Resolve one exact reusable TikTok seed through official read-only lists.

    A source offer and Miaoshou common-detail identity use different
    namespaces.  This resolver never substitutes one for the other and never
    creates or claims a platform row while identity is missing or ambiguous.
    """

    def __init__(
        self,
        *,
        post: ProviderPost = post_open,
        page_size: int = 100,
        max_pages: int = 20,
    ) -> None:
        if not callable(post):
            raise TypeError("TikTok seed identity transport must be callable")
        if (
            type(page_size) is not int
            or page_size <= 0
            or type(max_pages) is not int
            or max_pages <= 0
        ):
            raise TypeError("TikTok seed identity pagination is invalid")
        self._post = post
        self._page_size = page_size
        self._max_pages = max_pages

    def __call__(self, request: object) -> Mapping[str, object]:
        raw_snapshot = _request_snapshot(request, "TIKTOK")
        try:
            snapshot = validate_approved_publication_snapshot(raw_snapshot).payload()
        except ApprovedPublicationSnapshotError as error:
            raise LivePublicationDependencyError(
                "approved v4 snapshot is invalid"
            ) from error
        product = snapshot.get("product")
        source = product.get("source_identity") if isinstance(product, Mapping) else None
        if not isinstance(source, Mapping):
            raise LivePublicationDependencyError(
                "frozen source identity is unavailable"
            )
        source_offer_id = _positive_provider_id(
            source.get("source_offer_id"), "frozen source offer identity"
        )

        common_rows = _seed_list_rows(
            post=self._post,
            path=MIAOSHOU_COMMON_LIST_PATH,
            source_offer_id=source_offer_id,
            common_list=True,
            page_size=self._page_size,
            max_pages=self._max_pages,
        )
        if len(common_rows) != 1:
            raise LivePublicationDependencyError(
                "source COMMON identity is unavailable or ambiguous"
            )
        common_row = common_rows[0]
        if _seed_source_ids(common_row, "COMMON") != {source_offer_id}:
            raise LivePublicationDependencyError("COMMON source identity conflicts")
        common_detail_id = _positive_provider_id(
            common_row.get("commonCollectBoxDetailId"),
            "COMMON detail identity",
        )

        platform_rows = _seed_list_rows(
            post=self._post,
            path=MIAOSHOU_TIKTOK_LIST_PATH,
            source_offer_id=source_offer_id,
            common_list=False,
            page_size=self._page_size,
            max_pages=self._max_pages,
        )
        seen_detail_ids: set[str] = set()
        unclaimed: list[Mapping[str, object]] = []
        claimed_without_identity: list[str] = []
        selected_labels = [
            row["target_label"]
            for row in snapshot["publication_targets"]
            if row["platform"] == "tiktok"
        ]
        label_by_shop_id = {
            str(EXPECTED_SHOP_ID_BY_TARGET[label]): label
            for label in selected_labels
        }
        platform_detail_ids_by_target: dict[str, str] = {}
        for row in platform_rows:
            # The production TikTok list omits source fields and exposes the
            # exact COMMON foreign key instead.  When source fields are
            # present they remain mandatory and must agree; when absent, the
            # already source-verified COMMON row is the authoritative join.
            if _seed_has_source_identity(row) and _seed_source_ids(
                row, "TikTok platform"
            ) != {source_offer_id}:
                raise LivePublicationDependencyError(
                    "TikTok platform source identity conflicts"
                )
            observed_common = _positive_provider_id(
                row.get("commonCollectBoxDetailId"),
                "TikTok platform COMMON identity",
            )
            if observed_common != common_detail_id:
                raise LivePublicationDependencyError(
                    "TikTok platform COMMON identity conflicts"
                )
            detail_id = _positive_provider_id(
                row.get("collectBoxDetailId", row.get("detailId")),
                "TikTok platform detail identity",
            )
            if detail_id in seen_detail_ids:
                raise LivePublicationDependencyError(
                    "TikTok platform detail identity is ambiguous"
                )
            seen_detail_ids.add(detail_id)
            claimed, shop_ids = _platform_claim_evidence(row)
            if claimed:
                selected_shops = sorted(set(shop_ids).intersection(label_by_shop_id))
                if not selected_shops:
                    if not shop_ids:
                        claimed_without_identity.append(detail_id)
                    continue
                for shop_id in selected_shops:
                    label = label_by_shop_id[shop_id]
                    if label in platform_detail_ids_by_target:
                        raise LivePublicationDependencyError(
                            "claimed TikTok target identity is ambiguous"
                        )
                    platform_detail_ids_by_target[label] = detail_id
            else:
                unclaimed.append(row)
        if claimed_without_identity:
            raise LivePublicationDependencyError(
                "claimed TikTok platform identity exists; reconciliation required"
            )

        initial_platform_detail_id: str | None = None
        if unclaimed and set(selected_labels).difference(platform_detail_ids_by_target):
            selected = max(unclaimed, key=_created_sort_key)
            initial_platform_detail_id = _positive_provider_id(
                selected.get("collectBoxDetailId", selected.get("detailId")),
                "TikTok platform detail identity",
            )
        body: dict[str, object] = {
            "schema_version": MIAOSHOU_TIKTOK_SEED_IDENTITY_SCHEMA,
            "snapshot_digest": snapshot["snapshot_digest"],
            "common_detail_id": common_detail_id,
            "initial_platform_detail_id": initial_platform_detail_id,
            "platform_detail_ids_by_target": platform_detail_ids_by_target,
        }
        body["identity_digest"] = "sha256:" + _digest(body)
        return body


class MiaoshouTikTokV4DraftTransportFactory:
    """Build the real v4 draft transport from one exact control identity.

    The seed resolver is deliberately injected.  A Miaoshou common-detail ID
    is provider control identity, not a product fact, and therefore cannot be
    guessed from the frozen source offer ID or read from a mutable dashboard.
    """

    def __init__(
        self,
        *,
        seed_identity_resolver: TikTokDraftSeedIdentityResolver | None = None,
        post: ProviderPost = post_open,
    ) -> None:
        if seed_identity_resolver is not None and not callable(seed_identity_resolver):
            raise TypeError("TikTok v4 draft seed resolver is invalid")
        if not callable(post):
            raise TypeError("TikTok v4 draft transport dependencies are invalid")
        self._seed_identity_resolver = seed_identity_resolver or (
            OfficialMiaoshouTikTokV4SeedIdentityResolver(post=post)
        )
        self._post = post

    def __call__(
        self,
        request: object,
        observer: Callable[[str, DraftWriteFact], None],
    ) -> TikTokV4DraftTransport:
        snapshot = _request_snapshot(request, "TIKTOK")
        identity = self._seed_identity_resolver(request)
        required = {
            "schema_version",
            "snapshot_digest",
            "common_detail_id",
            "initial_platform_detail_id",
            "platform_detail_ids_by_target",
            "identity_digest",
        }
        if not isinstance(identity, Mapping) or set(identity) != required:
            raise LivePublicationDependencyError(
                "TikTok v4 common-detail identity is malformed"
            )
        body = dict(identity)
        supplied = body.pop("identity_digest", None)
        common_detail_id = body.get("common_detail_id")
        initial_detail_id = body.get("initial_platform_detail_id")
        target_detail_ids = body.get("platform_detail_ids_by_target")
        if (
            body.get("schema_version") != MIAOSHOU_TIKTOK_SEED_IDENTITY_SCHEMA
            or body.get("snapshot_digest") != snapshot.get("snapshot_digest")
            or isinstance(common_detail_id, bool)
            or not str(common_detail_id or "").isdigit()
            or int(str(common_detail_id)) <= 0
            or (
                initial_detail_id is not None
                and (
                    isinstance(initial_detail_id, bool)
                    or not str(initial_detail_id).isdigit()
                    or int(str(initial_detail_id)) <= 0
                )
            )
            or not isinstance(target_detail_ids, Mapping)
            or any(
                label not in EXPECTED_SHOP_ID_BY_TARGET
                or label not in getattr(request, "target_labels", ())
                or isinstance(detail_id, bool)
                or not str(detail_id).isdigit()
                or int(str(detail_id)) <= 0
                for label, detail_id in target_detail_ids.items()
            )
            or supplied != "sha256:" + _digest(body)
        ):
            raise LivePublicationDependencyError(
                "TikTok v4 common-detail identity conflicts"
            )
        return MiaoshouOpenApiTikTokV4DraftTransport(
            common_detail_id=str(common_detail_id),
            initial_platform_detail_id=(
                str(initial_detail_id) if initial_detail_id is not None else None
            ),
            platform_detail_ids_by_target={
                str(label): str(detail_id)
                for label, detail_id in target_detail_ids.items()
            },
            post=self._post,
            fact_observer=observer,
        )


def _request_snapshot(request: object, platform: str) -> Mapping[str, object]:
    if getattr(request, "platform", None) != platform:
        raise LivePublicationDependencyError("publication platform identity conflicts")
    snapshot = getattr(request, "snapshot", None)
    if not isinstance(snapshot, Mapping):
        raise LivePublicationDependencyError("approved v4 snapshot is unavailable")
    if snapshot.get("schema_version") != "approved-publication-snapshot/v4":
        raise LivePublicationDependencyError("approved v4 snapshot schema is invalid")
    return snapshot


def _decimal_text(value: object, name: str) -> str:
    if isinstance(value, bool) or type(value) not in {str, int, float, Decimal}:
        raise LivePublicationDependencyError(f"{name} is invalid")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise LivePublicationDependencyError(f"{name} is invalid") from None
    if not number.is_finite() or number <= 0:
        raise LivePublicationDependencyError(f"{name} is invalid")
    rendered = format(number.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _same_decimal(left: object, right: object) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _shopee_expected_models(
    snapshot: Mapping[str, object], targets: Sequence[str]
) -> list[dict[str, object]]:
    rows = snapshot.get("skus")
    if not isinstance(rows, list) or not rows:
        raise LivePublicationDependencyError("approved Shopee SKU facts are unavailable")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    variation_name: str | None = None
    for row in rows:
        if not isinstance(row, Mapping):
            raise LivePublicationDependencyError("approved Shopee SKU is malformed")
        model_sku = str(row.get("model_sku") or "").strip()
        if not model_sku or model_sku in seen:
            raise LivePublicationDependencyError("approved Shopee model identity is ambiguous")
        seen.add(model_sku)
        specification = row.get("specification")
        if not isinstance(specification, Mapping) or len(specification) != 1:
            raise LivePublicationDependencyError(
                "approved Shopee variation must have one exact dimension"
            )
        current_name, current_option = next(iter(specification.items()))
        if (
            type(current_name) is not str
            or not current_name.strip()
            or type(current_option) is not str
            or not current_option.strip()
        ):
            raise LivePublicationDependencyError(
                "approved Shopee variation identity is incomplete"
            )
        if variation_name is None:
            variation_name = current_name.strip()
        elif variation_name != current_name.strip():
            raise LivePublicationDependencyError(
                "approved Shopee variation names conflict"
            )
        global_prices: set[str] = set()
        prices = row.get("prices")
        if not isinstance(prices, Mapping):
            raise LivePublicationDependencyError("approved Shopee prices are unavailable")
        for target in targets:
            price = prices.get(target)
            if not isinstance(price, Mapping):
                raise LivePublicationDependencyError(
                    f"approved Shopee price is unavailable for {target}"
                )
            global_prices.add(
                _decimal_text(
                    price.get("global_original_price_cny"),
                    "approved Shopee CNSC price",
                )
            )
        if len(global_prices) != 1:
            raise LivePublicationDependencyError(
                "approved Shopee CNSC price lineage conflicts across targets"
            )
        images = row.get("variant_images")
        if not isinstance(images, list) or not images or any(
            type(url) is not str or not url.startswith("https://") for url in images
        ):
            raise LivePublicationDependencyError(
                "approved Shopee variant image is unavailable"
            )
        result.append(
            {
                "model_sku": model_sku,
                "variation_name": variation_name,
                "option": current_option.strip(),
                "global_price_cny": next(iter(global_prices)),
                "requires_variant_image": True,
            }
        )
    return result


def _shopee_parcel_envelope(
    snapshot: Mapping[str, object],
) -> tuple[str, tuple[str, str, str]]:
    rows = snapshot.get("skus")
    if not isinstance(rows, list) or not rows:
        raise LivePublicationDependencyError("approved Shopee parcel is unavailable")
    weights: list[Decimal] = []
    dimensions: list[tuple[Decimal, Decimal, Decimal]] = []
    for row in rows:
        parcel = row.get("parcel") if isinstance(row, Mapping) else None
        package = parcel.get("package_cm") if isinstance(parcel, Mapping) else None
        if not isinstance(package, (list, tuple)) or len(package) != 3:
            raise LivePublicationDependencyError(
                "approved Shopee SKU parcel is incomplete"
            )
        weights.append(Decimal(_decimal_text(parcel.get("weight_kg"), "SKU weight")))
        dimensions.append(
            tuple(
                Decimal(_decimal_text(value, "SKU package dimension"))
                for value in package
            )
        )
    return (
        _decimal_text(max(weights), "Shopee parcel weight"),
        tuple(
            _decimal_text(
                max(package[index] for package in dimensions),
                "Shopee parcel dimension",
            )
            for index in range(3)
        ),
    )


def _default_shopee_mapping_lookup(model_sku: str) -> str | None:
    key = parse_search_key(model_sku)
    if not key:
        return None
    candidates: set[str] = set()
    for global_item_id, entry in load_map().items():
        if not isinstance(entry, Mapping):
            continue
        keys = {parse_search_key(str(entry.get("match_key") or ""))}
        extra = entry.get("match_keys")
        if isinstance(extra, list):
            keys.update(parse_search_key(str(value or "")) for value in extra)
        keys.discard("")
        if key in keys:
            candidates.add(str(global_item_id).strip())
    if len(candidates) > 1:
        raise LivePublicationDependencyError(
            "Shopee global item mapping is ambiguous"
        )
    return next(iter(candidates)) if candidates else None


def _shopee_response(response: object, operation: str) -> Mapping[str, object]:
    if not isinstance(response, Mapping):
        raise LivePublicationDependencyError(f"{operation} response is malformed")
    error = str(response.get("error") or "").strip()
    if error and error != "-":
        raise LivePublicationDependencyError(f"{operation} was rejected")
    data = response.get("response")
    if not isinstance(data, Mapping):
        raise LivePublicationDependencyError(f"{operation} response is malformed")
    return data


def _global_price(row: Mapping[str, object]) -> object:
    price_info = row.get("price_info")
    if isinstance(price_info, Mapping):
        return price_info.get("original_price")
    if isinstance(price_info, list):
        for candidate in price_info:
            if isinstance(candidate, Mapping) and str(
                candidate.get("currency") or "CNY"
            ).upper() == "CNY":
                return candidate.get("original_price")
    return row.get("original_price")


class ShopeeExactGlobalItemResolver:
    """Resolve a local exact key only after official CNSC readback matches v4."""

    def __init__(
        self,
        *,
        runtime: ShopeeRegionRuntime | None = None,
        mapping_lookup: Callable[[str], str | None] = _default_shopee_mapping_lookup,
        merchant_get_transport: ShopeeMerchantGet = merchant_get,
    ) -> None:
        if not callable(mapping_lookup) or not callable(merchant_get_transport):
            raise TypeError("Shopee live transports must be callable")
        self._runtime = runtime or OfficialShopeeRegionRuntime()
        self._mapping_lookup = mapping_lookup
        self._merchant_get = merchant_get_transport

    def __call__(self, request: object) -> str:
        snapshot = _request_snapshot(request, "SHOPEE")
        targets = selected_region_targets(snapshot)
        labels = getattr(request, "target_labels", None)
        if not isinstance(labels, tuple) or tuple(targets) != labels:
            raise LivePublicationDependencyError("Shopee regional target scope conflicts")
        if not targets or any(target not in REGIONAL_TARGETS for target in targets):
            raise LivePublicationDependencyError("Shopee regional target scope is empty")
        expected = _shopee_expected_models(snapshot, targets)

        mapped_rows = [
            self._mapping_lookup(str(row["model_sku"])) for row in expected
        ]
        if any(not str(value or "").strip() for value in mapped_rows):
            raise LivePublicationDependencyError(
                "exact Shopee global item mapping is missing for an approved SKU"
            )
        mapped = {str(value).strip() for value in mapped_rows}
        if len(mapped) != 1:
            raise LivePublicationDependencyError(
                "Shopee global item mapping is ambiguous"
            )
        global_item_id = next(iter(mapped))
        if not global_item_id.isdigit() or int(global_item_id) <= 0:
            raise LivePublicationDependencyError(
                "Shopee global item identity is invalid"
            )

        contexts = [
            self._runtime.context(target.split(":", 1)[1]) for target in targets
        ]
        if len({context.merchant_id for context in contexts}) != 1:
            raise LivePublicationDependencyError(
                "Shopee regional targets do not share one CNSC merchant"
            )
        context = contexts[0]
        item_data = _shopee_response(
            self._merchant_get(
                "/api/v2/global_product/get_global_item_info",
                context.merchant_id,
                context.merchant_token,
                {"global_item_id_list": global_item_id},
            ),
            "Shopee official global item readback",
        )
        item_rows = item_data.get("global_item_list")
        if not isinstance(item_rows, list) or len(item_rows) != 1 or not isinstance(
            item_rows[0], Mapping
        ):
            raise LivePublicationDependencyError(
                "Shopee official global item is missing or ambiguous"
            )
        item = item_rows[0]
        if str(item.get("global_item_id") or "") != global_item_id:
            raise LivePublicationDependencyError(
                "Shopee official global item identity conflicts"
            )
        status = str(item.get("global_item_status") or "").strip().upper()
        if not status:
            raise LivePublicationDependencyError(
                "Shopee official global item status is unavailable"
            )
        if status != "NORMAL":
            raise LivePublicationDependencyError(
                "Shopee official global item is deleted or not executable"
            )
        product = snapshot.get("product")
        if not isinstance(product, Mapping):
            raise LivePublicationDependencyError("approved Shopee product is unavailable")
        if (
            item.get("global_item_name") != product.get("title")
            or (item.get("description") or item.get("global_item_description"))
            != product.get("description")
        ):
            raise LivePublicationDependencyError(
                "Shopee official global copy does not match approved v4 facts"
            )
        approved_images = product.get("images")
        official_image = item.get("image")
        official_images = (
            official_image.get("image_url_list")
            if isinstance(official_image, Mapping)
            else None
        )
        official_image_ids = (
            official_image.get("image_id_list")
            if isinstance(official_image, Mapping)
            else None
        )
        if (
            not isinstance(approved_images, list)
            or not approved_images
            or not isinstance(official_images, list)
            or len(official_images) != len(approved_images)
            or any(not str(value or "").strip() for value in official_images)
            or not isinstance(official_image_ids, list)
            or len(official_image_ids) != len(approved_images)
            or len({str(value).strip() for value in official_image_ids})
            != len(official_image_ids)
            or any(not str(value or "").strip() for value in official_image_ids)
        ):
            raise LivePublicationDependencyError(
                "Shopee official global image coverage is incomplete"
            )
        expected_weight, expected_package = _shopee_parcel_envelope(snapshot)
        dimension = item.get("dimension")
        if (
            not isinstance(dimension, Mapping)
            or not _same_decimal(item.get("weight"), expected_weight)
            or not _same_decimal(
                dimension.get("package_length"), expected_package[0]
            )
            or not _same_decimal(
                dimension.get("package_width"), expected_package[1]
            )
            or not _same_decimal(
                dimension.get("package_height"), expected_package[2]
            )
        ):
            raise LivePublicationDependencyError(
                "Shopee official global parcel does not match approved facts"
            )

        model_data = _shopee_response(
            self._merchant_get(
                "/api/v2/global_product/get_global_model_list",
                context.merchant_id,
                context.merchant_token,
                {"global_item_id": int(global_item_id)},
            ),
            "Shopee official global model readback",
        )
        models = model_data.get("global_model")
        if not isinstance(models, list) or any(
            not isinstance(row, Mapping) for row in models
        ):
            raise LivePublicationDependencyError(
                "Shopee official global model response is malformed"
            )
        by_sku = {
            str(row.get("global_model_sku") or "").strip(): row for row in models
        }
        expected_skus = {str(row["model_sku"]) for row in expected}
        if set(by_sku) != expected_skus or len(models) != len(expected):
            raise LivePublicationDependencyError(
                "Shopee official global SKU coverage is incomplete"
            )

        tiers = model_data.get("tier_variation")
        if not isinstance(tiers, list) or len(tiers) != 1 or not isinstance(
            tiers[0], Mapping
        ):
            raise LivePublicationDependencyError(
                "Shopee official global variation is malformed"
            )
        tier = tiers[0]
        if tier.get("name") != expected[0]["variation_name"]:
            raise LivePublicationDependencyError(
                "Shopee official variation name does not match approved facts"
            )
        options = tier.get("option_list")
        if not isinstance(options, list) or len(options) != len(expected) or any(
            not isinstance(option, Mapping) for option in options
        ):
            raise LivePublicationDependencyError(
                "Shopee official global variation options are incomplete"
            )
        used_tiers: set[int] = set()
        for facts in expected:
            observed = by_sku[str(facts["model_sku"])]
            global_model_id = observed.get("global_model_id")
            if (
                isinstance(global_model_id, bool)
                or not str(global_model_id or "").isdigit()
                or int(str(global_model_id)) <= 0
            ):
                raise LivePublicationDependencyError(
                    "Shopee official global model identity is unavailable"
                )
            tier_index = observed.get("tier_index")
            if (
                not isinstance(tier_index, (list, tuple))
                or len(tier_index) != 1
                or type(tier_index[0]) is not int
                or tier_index[0] < 0
                or tier_index[0] >= len(options)
                or tier_index[0] in used_tiers
            ):
                raise LivePublicationDependencyError(
                    "Shopee official global model tier identity is invalid"
                )
            used_tiers.add(tier_index[0])
            option = options[tier_index[0]]
            image = option.get("image") if isinstance(option, Mapping) else None
            if option.get("option") != facts["option"] or not isinstance(
                image, Mapping
            ) or not str(image.get("image_id") or "").strip():
                raise LivePublicationDependencyError(
                    "Shopee official variant option or image does not match approved facts"
                )
            if not _same_decimal(
                _global_price(observed), facts["global_price_cny"]
            ):
                raise LivePublicationDependencyError(
                    "Shopee official global model price does not match approved facts"
                )
        return global_item_id


def _positive_task_id(response: Mapping[str, object]) -> str | None:
    result = response.get("result")
    if not isinstance(result, Mapping):
        return None
    value = result.get("task_id")
    if isinstance(value, bool):
        return None
    text = str(value or "").strip()
    return text if text.isdigit() and int(text) > 0 else None


def _normalize_number(value: object) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise LivePublicationDependencyError("Ozon official numeric fact is invalid") from None
    if not number.is_finite():
        raise LivePublicationDependencyError("Ozon official numeric fact is invalid")
    rendered = format(number.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _ozon_weight_kg(item: Mapping[str, object]) -> str:
    if item.get("weight_kg") is not None:
        return _normalize_number(item.get("weight_kg"))
    weight = Decimal(str(item.get("weight")))
    unit = str(item.get("weight_unit") or "g").strip().casefold()
    if unit in {"g", "gram", "grams"}:
        weight /= Decimal("1000")
    elif unit not in {"kg", "kilogram", "kilograms"}:
        raise LivePublicationDependencyError("Ozon official weight unit is unsupported")
    return _normalize_number(weight)


def _ozon_package_cm(item: Mapping[str, object]) -> list[str]:
    direct = item.get("package_cm")
    if isinstance(direct, (list, tuple)) and len(direct) == 3:
        return [_normalize_number(value) for value in direct]
    dimensions = [item.get("depth"), item.get("width"), item.get("height")]
    if any(value is None for value in dimensions):
        raise LivePublicationDependencyError(
            "Ozon official package dimensions are unavailable"
        )
    unit = str(item.get("dimension_unit") or "mm").strip().casefold()
    divisor = Decimal("10") if unit in {"mm", "millimeter", "millimeters"} else Decimal("1")
    if unit not in {"mm", "millimeter", "millimeters", "cm", "centimeter", "centimeters"}:
        raise LivePublicationDependencyError(
            "Ozon official dimension unit is unsupported"
        )
    return [_normalize_number(Decimal(str(value)) / divisor) for value in dimensions]


class OfficialOzonFridgeMagnetProfileResolver:
    """Resolve the exact enabled Ozon fridge-magnet profile from official facts.

    Product semantics come only from the frozen main category.  The title is
    deliberately not used to guess a category.  The current account must
    expose one enabled ``Fridge Magnet`` type and the exact required metadata
    before a dispatch item can be built.
    """

    _CATEGORY_ID = 17028743
    _TYPE_ID = 93785
    _SEMANTIC_ALIASES = frozenset(
        {
            "fridge magnet",
            "fridge magnets",
            "home > fridge magnets",
            "居家日用 > 冰箱贴",
            "冰箱贴",
        }
    )

    def __init__(self, *, post: OzonPost = ozon_post) -> None:
        if not callable(post):
            raise TypeError("Ozon profile transport must be callable")
        self._post = post

    @staticmethod
    def _main_category_name(snapshot: Mapping[str, Any]) -> str:
        product = snapshot.get("product")
        category = product.get("main_category") if isinstance(product, Mapping) else None
        name = str(category.get("name") or "").strip() if isinstance(category, Mapping) else ""
        normalized = " ".join(name.casefold().split())
        if normalized not in OfficialOzonFridgeMagnetProfileResolver._SEMANTIC_ALIASES:
            raise LivePublicationDependencyError(
                "frozen main category is not the approved fridge-magnet semantic"
            )
        return name

    @staticmethod
    def _exact_value(rows: object, *, expected_id: int, labels: set[str]) -> dict[str, Any]:
        if not isinstance(rows, list):
            raise LivePublicationDependencyError("Ozon dictionary response is malformed")
        matches = [
            row
            for row in rows
            if isinstance(row, Mapping)
            and row.get("id") == expected_id
            and str(row.get("value") or "").strip().casefold() in labels
        ]
        if len(matches) != 1:
            raise LivePublicationDependencyError("Ozon dictionary value is not exact")
        return {
            "dictionary_value_id": expected_id,
            "value": str(matches[0]["value"]).strip(),
        }

    def __call__(self, snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(snapshot, Mapping) or snapshot.get("schema_version") != "approved-publication-snapshot/v4":
            raise LivePublicationDependencyError("approved Ozon snapshot is invalid")
        self._main_category_name(snapshot)
        tree_response = self._post(OZON_CATEGORY_TREE_PATH, {"language": "EN"})
        roots = tree_response.get("result") if isinstance(tree_response, Mapping) else None
        if not isinstance(roots, list):
            raise LivePublicationDependencyError("Ozon category tree is malformed")
        matches: list[tuple[list[dict[str, str]], Mapping[str, object]]] = []

        def walk(nodes: object, path: list[dict[str, str]], enabled: bool) -> None:
            if not isinstance(nodes, list):
                return
            for raw in nodes:
                if not isinstance(raw, Mapping):
                    continue
                row_enabled = enabled and raw.get("disabled") is not True
                next_path = list(path)
                category_id = raw.get("description_category_id")
                category_name = str(raw.get("category_name") or "").strip()
                if type(category_id) is int and category_id > 0 and category_name:
                    next_path.append({"id": str(category_id), "name": category_name})
                if (
                    row_enabled
                    and raw.get("type_id") == self._TYPE_ID
                    and str(raw.get("type_name") or "").strip() == "Fridge Magnet"
                    and next_path
                    and next_path[-1]["id"] == str(self._CATEGORY_ID)
                ):
                    matches.append((next_path, raw))
                walk(raw.get("children"), next_path, row_enabled)

        walk(roots, [], True)
        if len(matches) != 1:
            raise LivePublicationDependencyError(
                "Ozon official fridge-magnet type is missing or ambiguous"
            )
        category_path, _type_node = matches[0]
        category_name = category_path[-1]["name"]

        attributes_response = self._post(
            OZON_CATEGORY_ATTRIBUTES_PATH,
            {
                "description_category_id": self._CATEGORY_ID,
                "type_id": self._TYPE_ID,
                "language": "EN",
            },
        )
        attributes = (
            attributes_response.get("result")
            if isinstance(attributes_response, Mapping)
            else None
        )
        if not isinstance(attributes, list) or any(
            not isinstance(row, Mapping) for row in attributes
        ):
            raise LivePublicationDependencyError("Ozon category attributes are malformed")
        required_ids = {
            row.get("id") for row in attributes if row.get("is_required") is True
        }
        if required_ids != {85, 9048, 8229}:
            raise LivePublicationDependencyError(
                "Ozon required attribute coverage changed"
            )

        def search(attribute_id: int, value: str) -> object:
            response = self._post(
                OZON_ATTRIBUTE_VALUES_SEARCH_PATH,
                {
                    "attribute_id": attribute_id,
                    "description_category_id": self._CATEGORY_ID,
                    "type_id": self._TYPE_ID,
                    "language": "EN",
                    "limit": 20,
                    "value": value,
                },
            )
            return response.get("result") if isinstance(response, Mapping) else None

        brand = self._exact_value(
            search(85, "No brand"),
            expected_id=126745801,
            labels={"no brand", "нет бренда"},
        )
        product_type = self._exact_value(
            search(8229, "Fridge Magnet"),
            expected_id=self._TYPE_ID,
            labels={"fridge magnet"},
        )
        return {
            "schema_version": "ozon-official-profile-resolution/v1",
            "resolution": "EXACT",
            "description_category_id": self._CATEGORY_ID,
            "category_name": category_name,
            "category_path": category_path,
            "type_id": self._TYPE_ID,
            "type_name": "Fridge Magnet",
            "required_attributes": {
                "brand": {"attribute_id": 85, **brand},
                "model_name": {"attribute_id": 9048},
                "product_type": {"attribute_id": 8229, **product_type},
            },
        }


def build_ozon_import_item_from_frozen_variant(
    variant: Mapping[str, Any],
) -> Mapping[str, object]:
    """Build one Ozon import item solely from a projected frozen variant."""

    if not isinstance(variant, Mapping):
        raise LivePublicationDependencyError("Ozon frozen variant is invalid")
    profile = variant.get("official_profile")
    if (
        not isinstance(profile, Mapping)
        or profile.get("schema_version") != "ozon-official-profile-resolution/v1"
        or profile.get("resolution") != "EXACT"
        or profile.get("description_category_id") != 17028743
        or profile.get("type_id") != 93785
    ):
        raise LivePublicationDependencyError("Ozon official profile is unavailable")
    required = profile.get("required_attributes")
    if not isinstance(required, Mapping) or set(required) != {
        "brand",
        "model_name",
        "product_type",
    }:
        raise LivePublicationDependencyError("Ozon required attribute profile conflicts")
    offer_id = str(variant.get("offer_id") or "").strip()
    seller_sku = str(variant.get("approved_seller_sku") or "").strip()
    title = str(variant.get("title") or "").strip()
    description = str(variant.get("description") or "").strip()
    images = variant.get("images")
    parcel = variant.get("parcel")
    if (
        not offer_id
        or not seller_sku
        or not title
        or not description
        or not isinstance(images, list)
        or not images
        or any(type(url) is not str or not url.startswith("https://") for url in images)
        or not isinstance(parcel, Mapping)
        or not isinstance(parcel.get("package_cm"), list)
        or len(parcel["package_cm"]) != 3
    ):
        raise LivePublicationDependencyError("Ozon frozen variant facts are incomplete")

    def attribute(attribute_id: int, value: str, dictionary_value_id: int = 0) -> dict:
        return {
            "complex_id": 0,
            "id": attribute_id,
            "values": [
                {
                    "dictionary_value_id": dictionary_value_id,
                    "value": value,
                }
            ],
        }

    brand = required["brand"]
    product_type = required["product_type"]
    if not isinstance(brand, Mapping) or not isinstance(product_type, Mapping):
        raise LivePublicationDependencyError("Ozon dictionary profile is malformed")
    weight_kg = _normalize_number(parcel.get("weight_kg"))
    package_cm = [_normalize_number(value) for value in parcel["package_cm"]]
    return {
        "attributes": [
            attribute(85, str(brand.get("value") or ""), int(brand.get("dictionary_value_id") or 0)),
            attribute(9048, seller_sku + "-fridge-magnet"),
            attribute(8229, str(product_type.get("value") or ""), int(product_type.get("dictionary_value_id") or 0)),
            attribute(4180, title),
            attribute(4191, description),
            attribute(9024, offer_id),
        ],
        "description_category_id": int(profile["description_category_id"]),
        "type_id": int(profile["type_id"]),
        "color_image": images[0],
        "currency_code": str(variant.get("currency") or ""),
        "depth": package_cm[0],
        "width": package_cm[1],
        "height": package_cm[2],
        "dimension_unit": "cm",
        "weight": weight_kg,
        "weight_unit": "kg",
        "images": deepcopy(images),
        "name": title,
        "offer_id": offer_id,
        "old_price": _normalize_number(variant.get("old_price")),
        "price": _normalize_number(variant.get("price")),
        "vat": "0",
        "promotions": [{"operation": "DISABLE", "type": "REVIEWS_PROMO"}],
    }


class OfficialOzonV4Transport:
    """Official Ozon import/readback transport with an immutable profile seam.

    The default builder accepts only an exact official profile attached by the
    Skill-owned resolver and otherwise returns a known REJECTED pre-write fact.
    A custom builder is allowed only when it consumes the projected variant
    argument alone.
    """

    def __init__(
        self,
        *,
        post: OzonPost = ozon_post,
        import_item_builder: OzonImportItemBuilder | None = None,
    ) -> None:
        if not callable(post):
            raise TypeError("Ozon transport must be callable")
        if import_item_builder is not None and not callable(import_item_builder):
            raise TypeError("Ozon import item builder must be callable")
        self._post = post
        self._import_item_builder = (
            import_item_builder or build_ozon_import_item_from_frozen_variant
        )

    def dispatch_variant(self, variant: dict[str, Any]) -> OzonDispatchFact:
        try:
            item = self._import_item_builder(deepcopy(variant))
            if not isinstance(item, Mapping):
                raise LivePublicationDependencyError(
                    "Ozon immutable import profile is malformed"
                )
            item = deepcopy(dict(item))
            offer_id = str(variant.get("offer_id") or "").strip()
            category = variant.get("category")
            category_id = (
                str(category.get("id") or "").strip()
                if isinstance(category, Mapping)
                else ""
            )
            if (
                not offer_id
                or item.get("offer_id") != offer_id
                or str(item.get("description_category_id") or "") != category_id
                or type(item.get("type_id")) is not int
                or int(item["type_id"]) <= 0
                or not isinstance(item.get("attributes"), list)
                or not item["attributes"]
                or any(not isinstance(row, Mapping) for row in item["attributes"])
                or item.get("name") != variant.get("title")
                or not _same_decimal(item.get("price"), variant.get("price"))
                or not _same_decimal(
                    item.get("old_price"), variant.get("old_price")
                )
                or item.get("currency_code") != variant.get("currency")
                or item.get("images") != variant.get("images")
                or not _same_decimal(
                    item.get("weight"), variant.get("parcel", {}).get("weight_kg")
                )
                or str(item.get("weight_unit") or "").casefold() != "kg"
                or str(item.get("dimension_unit") or "").casefold() != "cm"
                or not _same_decimal(
                    item.get("depth"), variant.get("parcel", {}).get("package_cm", [None] * 3)[0]
                )
                or not _same_decimal(
                    item.get("width"), variant.get("parcel", {}).get("package_cm", [None] * 3)[1]
                )
                or not _same_decimal(
                    item.get("height"), variant.get("parcel", {}).get("package_cm", [None] * 3)[2]
                )
            ):
                raise LivePublicationDependencyError(
                    "Ozon immutable type_id/attributes do not match the frozen variant"
                )
        except Exception:
            return OzonDispatchFact(outcome="REJECTED")

        try:
            response = self._post(OZON_IMPORT_PATH, {"items": [item]})
        except Exception:
            return OzonDispatchFact(outcome="UNKNOWN")
        if not isinstance(response, Mapping):
            return OzonDispatchFact(outcome="UNKNOWN")
        error = response.get("error") or response.get("message")
        if error and not response.get("result"):
            return OzonDispatchFact(outcome="REJECTED")
        task_id = _positive_task_id(response)
        return (
            OzonDispatchFact(outcome="ACCEPTED", task_id=task_id)
            if task_id is not None
            else OzonDispatchFact(outcome="UNKNOWN")
        )

    def readback_variants(
        self, offer_ids: tuple[str, ...]
    ) -> list[Mapping[str, Any]]:
        if (
            not isinstance(offer_ids, tuple)
            or not offer_ids
            or len(offer_ids) != len(set(offer_ids))
            or any(type(value) is not str or not value.strip() for value in offer_ids)
        ):
            raise LivePublicationDependencyError("Ozon readback scope is invalid")
        response = self._post(
            OZON_READBACK_PATH,
            {"offer_id": list(offer_ids), "limit": 1000, "visibility": "ALL"},
        )
        if not isinstance(response, Mapping):
            raise LivePublicationDependencyError("Ozon official readback is malformed")
        if response.get("error"):
            raise LivePublicationDependencyError("Ozon official readback was rejected")
        items = response.get("items")
        if not isinstance(items, list) or any(
            not isinstance(item, Mapping) for item in items
        ):
            raise LivePublicationDependencyError("Ozon official readback is malformed")
        normalized: list[Mapping[str, Any]] = []
        for item in items:
            statuses = item.get("statuses")
            images = item.get("images")
            if not isinstance(statuses, Mapping) or not isinstance(images, list):
                raise LivePublicationDependencyError(
                    "Ozon official product facts are incomplete"
                )
            normalized.append(
                {
                    "offer_id": str(item.get("offer_id") or "").strip(),
                    # ``id`` is authoritative.  Do not fall back to the legacy
                    # product_id field.
                    "id": item.get("id"),
                    "statuses": deepcopy(dict(statuses)),
                    "name": item.get("name"),
                    "price": item.get("price"),
                    "old_price": item.get("old_price"),
                    "images": deepcopy(images),
                    "category_id": str(
                        item.get("description_category_id")
                        or item.get("category_id")
                        or ""
                    ),
                    "weight_kg": _ozon_weight_kg(item),
                    "package_cm": _ozon_package_cm(item),
                }
            )
        return normalized


def build_live_tiktok_dependencies(
    *,
    collectbox_context_resolver: Callable[
        [object], Mapping[str, Mapping[str, object]]
    ]
    | None = None,
    draft_preparer: DurableTikTokV4DraftPreparer | None = None,
    category_resolver: OfficialMiaoshouTikTokCategoryResolver | None = None,
    publisher: object | None = None,
    storefront_readback: TikTokUnavailableStorefrontReadback | None = None,
) -> TikTokV4ExecutorDependencies:
    """Build only TikTok dependencies; no Shopee/Ozon object is touched."""

    return TikTokV4ExecutorDependencies(
        collectbox_context_resolver=collectbox_context_resolver,
        category_resolver=category_resolver
        or OfficialMiaoshouTikTokCategoryResolver(),
        publisher=publisher or production_tiktok_publisher(),
        storefront_readback=storefront_readback
        or TikTokUnavailableStorefrontReadback(),
        draft_preparer=draft_preparer,
    )


def build_live_shopee_dependencies(
    *,
    resolver: Callable[[object], object] | None = None,
    runtime: ShopeeRegionRuntime | None = None,
    poll_attempts: int = 3,
) -> ShopeeRegionExecutorDependencies:
    """Build only Shopee regional dependencies from one official runtime."""

    live_runtime = runtime or OfficialShopeeRegionRuntime()
    return ShopeeRegionExecutorDependencies(
        global_item_id_resolver=resolver
        or ShopeeGlobalV4Resolver(
            runtime=build_official_shopee_global_v4_runtime()
        ),
        runtime=live_runtime,
        poll_attempts=poll_attempts,
    )


def build_live_ozon_dependencies(
    *,
    transport: OfficialOzonV4Transport | None = None,
    official_profile_resolver: OfficialOzonFridgeMagnetProfileResolver | None = None,
) -> OzonV4ExecutorDependencies:
    """Build only Ozon dependencies; no other platform object is touched."""

    live_transport = transport or OfficialOzonV4Transport()
    return OzonV4ExecutorDependencies(
        dispatch_variant=live_transport.dispatch_variant,
        readback_variants=live_transport.readback_variants,
        official_profile_resolver=(
            official_profile_resolver or OfficialOzonFridgeMagnetProfileResolver()
        ),
    )


__all__ = [
    "CATEGORY_METADATA_PATH",
    "CATEGORY_TREE_PATH",
    "DurableTikTokV4DraftPreparer",
    "LivePublicationDependencyError",
    "MIAOSHOU_COMMON_LIST_PATH",
    "MIAOSHOU_TIKTOK_LIST_PATH",
    "MIAOSHOU_TIKTOK_SEED_IDENTITY_SCHEMA",
    "MiaoshouTikTokV4DraftTransportFactory",
    "OfficialMiaoshouTikTokCategoryResolver",
    "OfficialMiaoshouTikTokV4SeedIdentityResolver",
    "OfficialOzonFridgeMagnetProfileResolver",
    "OfficialOzonV4Transport",
    "ShopeeExactGlobalItemResolver",
    "TikTokUnavailableStorefrontReadback",
    "TikTokV4DraftCheckpointStore",
    "build_live_ozon_dependencies",
    "build_live_shopee_dependencies",
    "build_live_tiktok_dependencies",
    "build_ozon_import_item_from_frozen_variant",
]
