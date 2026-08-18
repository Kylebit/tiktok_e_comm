"""Plan-native Shopee one-click existing-global primitive.

Prepare uses official GET-only item/model/shop/logistics reads and returns a
JSON-only command.  Dispatch rehydrates current no-refresh credentials,
rechecks the exact proof-bound identities, invokes at most one regional
publish task, and performs bounded official readback.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import ipaddress
import json
import socket
import ssl
import time
from typing import Any
import urllib.parse
import urllib.request

from domains.channel_operations.oneclick_write_occurrences import (
    OpenWriteOccurrence,
    WriteOccurrenceRecordingError,
    WriteOccurrenceState,
)

GLOBAL_WRITE = "shopee:global_master:create"
IMAGE_UPLOAD_WRITE = "shopee:image:upload"
GLOBAL_MODEL_WRITE = "shopee:global_model:init"
REGIONAL_WRITE = "shopee:regional_publish"
SHOPEE_GLOBAL_TARGET = "shopee:GLOBAL"
SHOPEE_REGIONAL_TARGETS = frozenset(
    {"shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN"}
)
GLOBAL_LIST_PATH = "/api/v2/global_product/get_global_item_list"
GLOBAL_ITEM_PATH = "/api/v2/global_product/get_global_item_info"
GLOBAL_MODEL_PATH = "/api/v2/global_product/get_global_model_list"
REGIONAL_TASK_PATH = "/api/v2/global_product/create_publish_task"
REGIONAL_TASK_RESULT_PATH = "/api/v2/global_product/get_publish_task_result"
GLOBAL_CREATE_PATH = "/api/v2/global_product/add_global_item"
GLOBAL_MODEL_INIT_PATH = "/api/v2/global_product/init_tier_variation"
GLOBAL_SCAN_MAX_WORKERS = 8


class ShopeeOneClickPreDispatchError(RuntimeError):
    pass


class ShopeeOneClickPrepareBlocked(RuntimeError):
    def __init__(
        self, code: str, detail: str, *, category: str = "CONTENT"
    ) -> None:
        super().__init__(detail)
        self.classification = {
            "AUTH": "BLOCKED_AUTH",
            "INVENTORY": "BLOCKED_INVENTORY",
        }.get(category, "BLOCKED_CAPABILITY")
        self.reason_category = category
        self.reason_scope = "TARGET"
        self.reason_code = code


class ShopeeOneClickDispatchError(RuntimeError):
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


@dataclass(frozen=True)
class ShopeeCredentials:
    region: str
    shop_id: int
    shop_token: str
    merchant_id: int
    merchant_token: str


@dataclass(frozen=True)
class ShopeePreparedImage:
    """Runtime-only verified bytes paired with the exact upload format."""

    content: bytes = field(repr=False)
    media_type: str
    suffix: str


@dataclass(frozen=True)
class ShopeePrepareTransport:
    credentials: ShopeeCredentials
    merchant_get: Callable[[str, Mapping[str, object]], object]
    shop_get: Callable[[str, Mapping[str, object] | None], object]


@dataclass(frozen=True)
class ShopeeRuntimeTransport:
    """Fixture-friendly dispatch transport.

    ``verify_pre_dispatch``/``regional_publish``/``readback`` are sufficient
    for restart/fault tests.  Production composition populates them from the
    official clients and current no-refresh credential store.
    """

    verify_pre_dispatch: Callable[[Mapping[str, object]], bool] | None = None
    regional_publish: Callable[[Mapping[str, object]], object] | None = None
    readback: Callable[[str, Mapping[str, object]], bool] | None = None
    global_update: Callable[[Mapping[str, object]], object] | None = None
    prepare_image: Callable[[str, int], ShopeePreparedImage] | None = None
    upload_image: Callable[[ShopeePreparedImage, int], object] | None = None
    add_global_item: Callable[[Mapping[str, object]], object] | None = None
    init_global_model: Callable[
        [str, Mapping[str, object]], object
    ] | None = None
    verify_created_global: Callable[
        [str, Mapping[str, object]], Mapping[str, object]
    ] | None = None
    resolve_existing_global: Callable[
        [Mapping[str, object]], Mapping[str, object] | None
    ] | None = None


_prepare_transport_factory: Callable[[str], ShopeePrepareTransport] | None = None
_runtime_transport_factory: Callable[[], ShopeeRuntimeTransport] | None = None
_global_candidate_observer_factory: Callable[
    [object, Mapping[str, object], ShopeePrepareTransport], object
] | None = None


def configure_prepare_transport_factory(
    factory: Callable[[str], ShopeePrepareTransport] | None,
) -> None:
    global _prepare_transport_factory
    _prepare_transport_factory = factory


def configure_runtime_transport_factory(
    factory: Callable[[], ShopeeRuntimeTransport] | None,
) -> None:
    global _runtime_transport_factory
    _runtime_transport_factory = factory


def configure_global_candidate_observer_factory(
    factory: Callable[
        [object, Mapping[str, object], ShopeePrepareTransport], object
    ]
    | None,
) -> None:
    """Inject an audited first-party observer for NEW_GLOBAL fixtures.

    Production defaults remain fail closed until the first-party category,
    attribute, brand, and seller-location response schema is audited.
    """

    global _global_candidate_observer_factory
    _global_candidate_observer_factory = factory


def prepare_plan_native_target(seed, request) -> dict[str, object]:
    """Find and prove one exact official global master without a local map."""
    try:
        return _prepare_plan_native_target(seed, request)
    except ShopeeOneClickPrepareBlocked:
        raise
    except ShopeeOneClickPreDispatchError as error:
        if "credential" in str(error).casefold():
            raise ShopeeOneClickPrepareBlocked(
                "shopee_prepared_credentials_unavailable",
                "prepared Shopee credentials are missing, expired, or invalid",
                category="AUTH",
            ) from error
        raise ShopeeOneClickPrepareBlocked(
            "shopee_official_prepare_proof_unavailable",
            "official Shopee read-only proof is unavailable",
            category="CAPABILITY",
        ) from error
    except (TimeoutError, OSError, RuntimeError) as error:
        raise ShopeeOneClickPrepareBlocked(
            "shopee_official_prepare_transport_unavailable",
            "official Shopee read-only transport is unavailable",
            category="CAPABILITY",
        ) from error


def _prepare_plan_native_target(seed, request) -> dict[str, object]:
    if (
        isinstance(seed.command, Mapping)
        and seed.command.get("schema_version")
        == "oneclick-shopee-prepare-seed/v2"
    ):
        return _prepare_approved_global_target(seed, request)
    prepared = seed.command.get("prepared")
    approved = (
        prepared.get("approved") if isinstance(prepared, Mapping) else None
    )
    if not isinstance(approved, Mapping):
        raise ShopeeOneClickPrepareBlocked(
            "shopee_plan_native_command_invalid",
            "approved Shopee plan-native command is unavailable",
        )
    target = str(seed.target_label)
    region = target.rsplit(":", 1)[1]
    transport = _prepare_transport(region)
    model_sku = _nonempty(approved.get("model_sku"), "model SKU")
    candidates = _scan_global_model_candidates(
        transport, model_sku=model_sku
    )
    if not candidates:
        missing = _missing_create_facts(approved)
        if missing:
            return {
                "classification": "BLOCKED_CAPABILITY",
                "reason_category": "CONTENT",
                "reason_scope": "TARGET",
                "reason_code": "shopee_global_create_facts_missing",
                "reason_detail": (
                    "approved Shopee global create facts are incomplete: "
                    + ",".join(missing)
                ),
                "external_writes_performed": [],
            }
        regional = _scan_regional_sku(
            transport,
            seller_sku=_nonempty(approved.get("seller_sku"), "seller SKU"),
        )
        if regional["matches"]:
            raise ShopeeOneClickPrepareBlocked(
                "shopee_regional_sku_without_global_identity",
                "regional SKU exists while the approved global model is absent",
                category="CAPABILITY",
            )
        selected_logistics = _compatible_logistics(
            transport, approved=approved, region=region
        )
        if not selected_logistics:
            raise ShopeeOneClickPrepareBlocked(
                "shopee_compatible_logistics_missing",
                "no enabled official logistics channel accepts the approved parcel",
                category="LOGISTICS",
            )
        create_facts = _mapping(
            approved.get("global_create"), "global create facts"
        )
        command = {
            "schema_version": "oneclick-shopee-new-global-command/v1",
            "kind": "NEW_GLOBAL",
            "target_label": target,
            "region": region,
            "shop_id": transport.credentials.shop_id,
            "approved": dict(approved),
            "selected_logistics_ids": selected_logistics,
            "global_create_payload": _global_create_body(
                approved, create_facts=create_facts, image_ids=[]
            ),
            "global_model_payload": _global_model_body(
                approved, create_facts=create_facts, global_item_id=None
            ),
            "proof_snapshot_digest": _digest(
                {
                    "global_full_scan_exact_zero": True,
                    "regional_scan_digest": regional["scan_digest"],
                    "selected_logistics_ids": selected_logistics,
                    "global_create_facts_digest": _digest(create_facts),
                }
            ),
        }
        proof = {
            "schema_version": "oneclick-shopee-new-global-proof/v1",
            "global_full_scan_exact_zero": True,
            "regional_full_scan_exact_zero": True,
            "regional_scan_digest": regional["scan_digest"],
            "selected_logistics_digest": _digest(
                {"ids": selected_logistics}
            ),
            "selected_logistics_count": len(selected_logistics),
            "global_create_facts_digest": _digest(create_facts),
            "approved_image_count": len(approved["ordered_images"]),
            "no_refresh": True,
            "proof_snapshot_digest": command["proof_snapshot_digest"],
        }
        json.loads(json.dumps(command, ensure_ascii=False, sort_keys=True))
        json.loads(json.dumps(proof, ensure_ascii=False, sort_keys=True))
        return {
            "command": command,
            "proof": proof,
            "external_writes_performed": [],
        }
    if len(candidates) != 1:
        raise ShopeeOneClickPreDispatchError(
            "official global model SKU identity is ambiguous"
        )
    global_item_id = str(candidates[0])
    master = _read_global_master(
        transport,
        global_item_id=global_item_id,
        approved=approved,
    )
    regional = _scan_regional_sku(
        transport,
        seller_sku=_nonempty(approved.get("seller_sku"), "seller SKU"),
    )
    if regional["matches"]:
        raise ShopeeOneClickPrepareBlocked(
            "shopee_regional_sku_not_zero",
            "regional SKU already exists and requires reconciliation",
            category="CAPABILITY",
        )
    selected_logistics = _compatible_logistics(
        transport, approved=approved, region=region
    )
    if not selected_logistics:
        raise ShopeeOneClickPrepareBlocked(
            "shopee_compatible_logistics_missing",
            "no enabled official logistics channel accepts the approved parcel",
            category="LOGISTICS",
        )
    command = {
        "schema_version": "oneclick-shopee-existing-global-command/v1",
        "kind": "EXISTING_GLOBAL",
        "target_label": target,
        "region": region,
        "shop_id": transport.credentials.shop_id,
        "global_item_id": global_item_id,
        "global_model_id": master["global_model_id"],
        "global_tier_index": master["tier_index"],
        "global_image_snapshot_digest": master["image_snapshot_digest"],
        "global_image_observation": master["image_observation"],
        "global_image_outcome": master["image_outcome"],
        "selected_logistics_ids": selected_logistics,
        "approved": dict(approved),
        "regional_publish_payload": _regional_body(
            approved,
            region=region,
            shop_id=transport.credentials.shop_id,
            global_item_id=global_item_id,
            tier_index=master["tier_index"],
            selected_logistics=selected_logistics,
        ),
        "proof_snapshot_digest": _digest(
            {
                "global": master["summary"],
                "regional_scan_digest": regional["scan_digest"],
                "selected_logistics_ids": selected_logistics,
            }
        ),
    }
    proof = {
        "schema_version": "oneclick-shopee-existing-global-proof/v1",
        "global_item_identity_digest": _text_digest(global_item_id),
        "global_model_identity_digest": _digest(
            {
                "global_model_id": master["global_model_id"],
                "tier_index": master["tier_index"],
                "model_sku": model_sku,
            }
        ),
        "official_copy_digest": master["copy_digest"],
        "official_image_id_snapshot_digest": master[
            "image_snapshot_digest"
        ],
        "global_image_observation_digest": master["image_observation"][
            "evidence_digest"
        ],
        "global_image_outcome_digest": master["image_outcome"][
            "evidence_digest"
        ],
        "global_image_status": master["image_outcome"][
            "global_image_status"
        ],
        "global_image_verification_scope": master["image_outcome"][
            "global_image_verification_scope"
        ],
        "global_image_url_identity_exact": False,
        "global_image_approved_order_exact": master["image_outcome"][
            "global_image_approved_order_exact"
        ],
        "manual_review_required": master["image_outcome"][
            "manual_review_required"
        ],
        "approved_image_count": master["image_count"],
        "regional_full_scan_exact_zero": True,
        "regional_scan_digest": regional["scan_digest"],
        "selected_logistics_digest": _digest(
            {"ids": selected_logistics}
        ),
        "selected_logistics_count": len(selected_logistics),
        "no_refresh": True,
        "proof_snapshot_digest": command["proof_snapshot_digest"],
    }
    json.loads(json.dumps(command, ensure_ascii=False, sort_keys=True))
    json.loads(json.dumps(proof, ensure_ascii=False, sort_keys=True))
    return {
        "command": command,
        "proof": proof,
        "external_writes_performed": [],
    }


def _prepare_approved_global_target(seed, request) -> dict[str, object]:
    """Prepare one synthetic GLOBAL owner or one region from the approved plan."""

    approved_seed = seed.command.get("approved_global")
    if not isinstance(approved_seed, Mapping):
        raise ShopeeOneClickPrepareBlocked(
            "approved_shopee_global_seed_invalid",
            "approved Shopee global plan seed is unavailable",
        )
    record = approved_seed.get("approved_global_plan_record")
    compact = approved_seed.get("approved_global_plan")
    target = str(seed.target_label)
    try:
        from shared_platform.shopee_global_plan import (
            ShopeeGlobalPlanCandidate,
            rehydrate_approved_shopee_global_plan,
        )

        approved = rehydrate_approved_shopee_global_plan(record)
        stored_plan = json.loads(record)["approved_plan"]["plan"]
    except (KeyError, TypeError, ValueError) as error:
        raise ShopeeOneClickPrepareBlocked(
            "approved_shopee_global_record_invalid",
            "approved Shopee global plan record is invalid",
        ) from error
    if (
        not isinstance(compact, Mapping)
        or approved.mode != compact.get("mode")
        or approved.candidate_digest != compact.get("candidate_digest")
        or approved.approved_plan_digest
        != compact.get("approved_plan_digest")
        or stored_plan.get("selected_image_positions")
        != compact.get("selected_image_positions")
        or stored_plan.get("selected_source_image_manifest_digest")
        != compact.get("selected_source_image_manifest_digest")
    ):
        raise ShopeeOneClickPrepareBlocked(
            "approved_shopee_global_binding_drift",
            "approved Shopee global compact binding drifted",
        )
    plan = _mapping(stored_plan, "approved Shopee global plan")
    if target == SHOPEE_GLOBAL_TARGET:
        region = _global_control_region(request)
        transport = _prepare_transport(region)
        candidate, official = _current_approved_global_candidate(
            approved,
            plan,
            transport,
        )
        if not isinstance(candidate, ShopeeGlobalPlanCandidate):
            raise ShopeeOneClickPrepareBlocked(
                "shopee_current_global_candidate_invalid",
                "current official Shopee candidate is invalid",
                category="CAPABILITY",
            )
        try:
            execution = approved.server_owned_execution_payload(candidate)
        except Exception as error:
            raise ShopeeOneClickPrepareBlocked(
                "approved_shopee_global_candidate_drift",
                "approved Shopee global plan no longer matches current facts",
                category="CONTENT",
            ) from error
        selected = plan.get("selected_image_positions")
        if not isinstance(selected, list):
            raise ShopeeOneClickPrepareBlocked(
                "approved_shopee_image_selection_invalid",
                "approved Shopee image selection is invalid",
                category="CONTENT",
            )
        from shared_platform.oneclick_release_controlplane import (
            SHARED_RESOURCE_SCHEMA,
            SHOPEE_GLOBAL_MASTER_POLICY,
            shopee_shared_resource_owner_key,
        )

        master_lineage_digest = approved.approved_plan_digest
        owner_key = shopee_shared_resource_owner_key(
            request, master_lineage_digest
        )
        common = {
            "schema_version": SHARED_RESOURCE_SCHEMA,
            "policy_version": SHOPEE_GLOBAL_MASTER_POLICY,
            "owner_key": owner_key,
            "master_lineage_digest": master_lineage_digest,
            "approved_selected_image_count": len(selected),
        }
        if approved.mode == "EXISTING_GLOBAL":
            global_item_id = _positive_identity(
                official.get("global_item_id"),
                "approved existing global item identity",
            )
            declaration = {
                **common,
                "mode": "EXISTING_GLOBAL",
                "expected_external_write_count": 0,
                "global_identity_digest": _text_digest(global_item_id),
                "master_evidence_digest": _nonempty_digest(
                    official.get("master_evidence_digest"),
                    "official existing master evidence",
                ),
            }
            command = {
                "schema_version": "oneclick-shopee-global-owner-command/v1",
                "kind": "GLOBAL_EXISTING",
                "target_label": SHOPEE_GLOBAL_TARGET,
                "region": region,
                "shop_id": transport.credentials.shop_id,
                "approved_plan_digest": approved.approved_plan_digest,
                "approved_global_plan_record": record,
                "shared_resource": declaration,
            }
        else:
            approved_contract = _approved_regional_contract(
                {
                    "seller_sku": (
                        approved_seed.get("seller_sku") or "GLOBAL"
                    ),
                    "target_pricing": {
                        "local_original_price": _mapping(
                            plan.get("pricing"), "approved pricing"
                        ).get("global_original_price"),
                        "currency": "CNY",
                    },
                },
                plan,
            )
            declaration = {
                **common,
                "mode": "ENSURE_NEW",
                "expected_external_write_count": len(selected) + 2,
            }
            command = {
                "schema_version": "oneclick-shopee-global-owner-command/v1",
                "kind": "GLOBAL_NEW",
                "target_label": SHOPEE_GLOBAL_TARGET,
                "region": region,
                "shop_id": transport.credentials.shop_id,
                "approved_execution": execution,
                "approved_global_plan_record": record,
                "approved": approved_contract,
                "selected_image_positions": list(selected),
                "global_create_payload": _canonical_global_create_body(
                    plan
                ),
                "global_model_payload": _canonical_global_model_body(
                    plan
                ),
                "model_contract_digest": _digest(
                    plan.get("global_model")
                ),
                "shared_resource": declaration,
                "proof_snapshot_digest": _digest(
                    {
                        "candidate_digest": approved.candidate_digest,
                        "approved_plan_digest": (
                            approved.approved_plan_digest
                        ),
                        "official_observation": official,
                    }
                ),
            }
        proof = {
            "schema_version": "oneclick-shopee-global-owner-proof/v1",
            "candidate_digest": approved.candidate_digest,
            "approved_plan_digest": approved.approved_plan_digest,
            "official_observation_digest": _digest(official),
            "shared_resource_digest": _digest(declaration),
            "no_refresh": True,
        }
        return {
            "command": command,
            "proof": proof,
            "shared_resource": declaration,
            "external_writes_performed": [],
        }
    if target not in SHOPEE_REGIONAL_TARGETS:
        raise ShopeeOneClickPrepareBlocked(
            "shopee_target_unsupported",
            "Shopee target is not supported",
        )
    region = target.rsplit(":", 1)[1]
    transport = _prepare_transport(region)
    approved_regional = _approved_regional_contract(
        approved_seed, plan
    )
    regional = _scan_regional_sku(
        transport,
        seller_sku=_nonempty(
            approved_regional.get("seller_sku"), "seller SKU"
        ),
    )
    if regional["matches"]:
        raise ShopeeOneClickPrepareBlocked(
            "shopee_regional_sku_not_zero",
            "regional SKU already exists and requires reconciliation",
            category="CAPABILITY",
        )
    selected_logistics = _compatible_logistics(
        transport, approved=approved_regional, region=region
    )
    if not selected_logistics:
        raise ShopeeOneClickPrepareBlocked(
            "shopee_compatible_logistics_missing",
            "no enabled official logistics channel accepts the approved parcel",
            category="LOGISTICS",
        )
    command = {
        "schema_version": "oneclick-shopee-region-command/v1",
        "kind": "REGION_FROM_SHARED",
        "target_label": target,
        "region": region,
        "shop_id": transport.credentials.shop_id,
        "approved_plan_digest": approved.approved_plan_digest,
        "approved_global_plan_payload": dict(plan),
        "approved": approved_regional,
        "expected_models": list(
            _approved_plan_models(plan)
        ),
        "model_contract_digest": _digest(
            _approved_plan_models(plan)
        ),
        "selected_logistics_ids": selected_logistics,
        "regional_scan_digest": regional["scan_digest"],
        "proof_snapshot_digest": _digest(
            {
                "approved_plan_digest": approved.approved_plan_digest,
                "regional_scan_digest": regional["scan_digest"],
                "selected_logistics_ids": selected_logistics,
            }
        ),
    }
    proof = {
        "schema_version": "oneclick-shopee-region-proof/v1",
        "approved_plan_digest": approved.approved_plan_digest,
        "regional_full_scan_exact_zero": True,
        "regional_scan_digest": regional["scan_digest"],
        "selected_logistics_digest": _digest(
            {"ids": selected_logistics}
        ),
        "selected_logistics_count": len(selected_logistics),
        "no_refresh": True,
        "proof_snapshot_digest": command["proof_snapshot_digest"],
    }
    return {
        "command": command,
        "proof": proof,
        "external_writes_performed": [],
    }


def _current_approved_global_candidate(
    approved: object,
    plan: Mapping[str, object],
    transport: ShopeePrepareTransport,
) -> tuple[object, dict[str, object]]:
    models = _approved_plan_models(plan)
    if not isinstance(models, list) or not models or any(
        not isinstance(row, Mapping) for row in models
    ):
        raise ShopeeOneClickPrepareBlocked(
            "approved_shopee_model_contract_invalid",
            "approved Shopee model contract is invalid",
            category="CONTENT",
        )
    model_skus = [
        _nonempty(row.get("global_model_sku"), "approved model SKU")
        for row in models
    ]
    observed_by_sku = {
        sku: _scan_global_model_candidates(transport, model_sku=sku)
        for sku in model_skus
    }
    if approved.mode == "NEW_GLOBAL":
        if any(observed_by_sku.values()):
            raise ShopeeOneClickPrepareBlocked(
                "shopee_new_global_identity_now_exists",
                "an approved model SKU now resolves to an official global item",
                category="CONTENT",
            )
        if _global_candidate_observer_factory is None:
            raise ShopeeOneClickPrepareBlocked(
                "shopee_official_global_candidate_fixture_required",
                "first-party category, attribute, brand, and location proof is unavailable",
                category="CAPABILITY",
            )
        candidate = _global_candidate_observer_factory(
            approved, plan, transport
        )
        return candidate, {
            "mode": "NEW_GLOBAL",
            "global_scan_exact_zero": True,
            "model_sku_count": len(model_skus),
            "candidate_digest": getattr(candidate, "candidate_digest", None),
        }
    snapshot = plan.get("current_snapshot")
    expected_id = _positive_identity(
        (
            snapshot.get("global_item_id")
            if isinstance(snapshot, Mapping)
            else plan.get("existing_global_item_id")
        ),
        "approved existing global item identity",
    )
    if any(values != [expected_id] for values in observed_by_sku.values()):
        raise ShopeeOneClickPrepareBlocked(
            "shopee_existing_global_identity_drift",
            "official global model identities no longer match approval",
            category="CONTENT",
        )
    if isinstance(snapshot, Mapping):
        candidate, current = _current_existing_snapshot_candidate(
            plan, transport
        )
        official = _read_approved_global_contract(
            transport,
            global_item_id=expected_id,
            plan=plan,
            approved_plan_digest=approved.approved_plan_digest,
            exact_current=(candidate, current),
        )
        return candidate, official
    official = _read_approved_global_contract(
        transport,
        global_item_id=expected_id,
        plan=plan,
        approved_plan_digest=approved.approved_plan_digest,
    )
    return _candidate_from_plan_payload(plan), official


def _current_existing_snapshot_candidate(
    plan: Mapping[str, object],
    transport: ShopeePrepareTransport,
) -> tuple[object, dict[str, object]]:
    from shared_platform.shopee_global_plan import (
        OFFICIAL_AUTHORITY,
        OFFICIAL_OBSERVATION_SCHEMA_VERSION,
        READY,
        build_shopee_existing_current_snapshot_candidate,
    )

    snapshot = _mapping(
        plan.get("current_snapshot"), "approved current snapshot"
    )
    bindings = _mapping(plan.get("bindings"), "approved plan bindings")
    copy = _mapping(plan.get("copy"), "approved copy")
    parcel = _mapping(plan.get("parcel"), "approved parcel")
    package = _mapping(parcel.get("package_cm"), "approved package")
    pricing = _mapping(plan.get("pricing"), "approved pricing")
    models = snapshot.get("global_model")
    if not isinstance(models, list) or not models:
        raise ShopeeOneClickPrepareBlocked(
            "approved_shopee_existing_models_invalid",
            "approved existing Shopee model contract is invalid",
            category="CONTENT",
        )
    expected_model_skus = [
        _nonempty(row.get("global_model_sku"), "approved model SKU")
        for row in models
    ]
    global_item_id = _positive_identity(
        snapshot.get("global_item_id"),
        "approved existing global item identity",
    )
    try:
        current = _observe_existing_global_candidate_availability(
            transport,
            global_item_id=global_item_id,
            seed={
                "approved_copy_digest": bindings.get(
                    "approved_copy_digest"
                ),
                "selected_image_positions": plan.get(
                    "selected_image_positions"
                ),
                "ordered_approved_images": plan.get("approved_images"),
            },
            expected_model_skus=tuple(expected_model_skus),
        )
    except ShopeeOneClickPreDispatchError as error:
        raise ShopeeOneClickPrepareBlocked(
            "shopee_existing_current_snapshot_drift",
            "official Shopee existing-global snapshot drifted",
            category="CONTENT",
        ) from error
    candidate = build_shopee_existing_current_snapshot_candidate(
        observation_authority=OFFICIAL_AUTHORITY,
        observation_schema_version=OFFICIAL_OBSERVATION_SCHEMA_VERSION,
        observation_evidence_digest=current[
            "observation_evidence_digest"
        ],
        source_identity_schema_version=bindings.get(
            "source_identity_schema_version"
        ),
        source_identity_digest=bindings.get("source_identity_digest"),
        sku_lineage_schema_version=bindings.get(
            "sku_lineage_schema_version"
        ),
        sku_lineage_digest=bindings.get("sku_lineage_digest"),
        content_package_digest=bindings.get("content_package_digest"),
        title=copy.get("title"),
        description=copy.get("description"),
        approved_copy_digest=bindings.get("approved_copy_digest"),
        ordered_approved_images=plan.get("approved_images"),
        approved_source_image_manifest_digest=plan.get(
            "approved_source_image_manifest_digest"
        ),
        selected_image_positions=plan.get("selected_image_positions"),
        parcel={
            "weight_kg": parcel.get("weight_kg"),
            "length_cm": package.get("length"),
            "width_cm": package.get("width"),
            "height_cm": package.get("height"),
            "contract_digest": parcel.get("contract_digest"),
        },
        target_pricing={
            "currency": pricing.get("currency"),
            "global_original_price": pricing.get(
                "global_original_price"
            ),
            "contract_digest": pricing.get("target_pricing_digest"),
        },
        policy_digest=plan.get("policy_digest"),
        expected_model_skus=expected_model_skus,
        existing_global_item=current["existing_global_item"],
        existing_global_models=current["existing_global_models"],
        existing_global_identity_evidence_digest=current[
            "existing_global_identity_evidence_digest"
        ],
    )
    if candidate.status != READY:
        raise ShopeeOneClickPrepareBlocked(
            "shopee_existing_current_snapshot_drift",
            "official Shopee existing-global snapshot drifted",
            category="CONTENT",
        )
    return candidate, current


def _candidate_from_plan_payload(plan: Mapping[str, object]) -> object:
    from shared_platform.shopee_global_plan import (
        OFFICIAL_AUTHORITY,
        OFFICIAL_OBSERVATION_SCHEMA_VERSION,
        build_shopee_global_plan_candidate,
    )

    bindings = _mapping(plan.get("bindings"), "approved plan bindings")
    parcel = _mapping(plan.get("parcel"), "approved parcel")
    package = _mapping(parcel.get("package_cm"), "approved package")
    pricing = _mapping(plan.get("pricing"), "approved pricing")
    return build_shopee_global_plan_candidate(
        mode=plan.get("mode"),
        observation_authority=OFFICIAL_AUTHORITY,
        observation_schema_version=OFFICIAL_OBSERVATION_SCHEMA_VERSION,
        observation_evidence_digest=plan.get(
            "observation_evidence_digest"
        ),
        source_identity_schema_version=bindings.get(
            "source_identity_schema_version"
        ),
        source_identity_digest=bindings.get("source_identity_digest"),
        sku_lineage_schema_version=bindings.get(
            "sku_lineage_schema_version"
        ),
        sku_lineage_digest=bindings.get("sku_lineage_digest"),
        content_package_digest=bindings.get("content_package_digest"),
        title=_mapping(plan.get("copy"), "approved copy").get("title"),
        description=_mapping(
            plan.get("copy"), "approved copy"
        ).get("description"),
        approved_copy_digest=bindings.get("approved_copy_digest"),
        ordered_approved_images=plan.get("approved_images"),
        approved_source_image_manifest_digest=plan.get(
            "approved_source_image_manifest_digest"
        ),
        selected_image_positions=plan.get("selected_image_positions"),
        parcel={
            "weight_kg": parcel.get("weight_kg"),
            "length_cm": package.get("length"),
            "width_cm": package.get("width"),
            "height_cm": package.get("height"),
            "contract_digest": parcel.get("contract_digest"),
        },
        target_pricing={
            "currency": pricing.get("currency"),
            "global_original_price": pricing.get(
                "global_original_price"
            ),
            "contract_digest": pricing.get("target_pricing_digest"),
        },
        policy_digest=plan.get("policy_digest"),
        category=plan.get("category"),
        attributes=plan.get("attribute_list"),
        attributes_complete=plan.get("attributes_complete"),
        attribute_tree_digest=plan.get("attribute_tree_digest"),
        brand=plan.get("brand"),
        seller_stock=plan.get("seller_stock"),
        location=plan.get("location"),
        condition=plan.get("condition"),
        preorder=plan.get("preorder"),
        variations=plan.get("tier_variation"),
        variations_complete=plan.get("variations_complete"),
        models=plan.get("global_model"),
        existing_global_item_id=plan.get("existing_global_item_id"),
        existing_global_identity_evidence_digest=plan.get(
            "existing_global_identity_evidence_digest"
        ),
    )


def _global_control_region(request: object) -> str:
    payload = getattr(request, "immutable_plan_payload", None)
    targets = payload.get("targets") if isinstance(payload, Mapping) else None
    if not isinstance(targets, list):
        raise ShopeeOneClickPrepareBlocked(
            "shopee_global_control_region_unavailable",
            "approved Shopee storefront targets are unavailable",
            category="CAPABILITY",
        )
    regions = sorted(
        {
            label.rsplit(":", 1)[1]
            for label in targets
            if type(label) is str and label in SHOPEE_REGIONAL_TARGETS
        }
    )
    if not regions:
        raise ShopeeOneClickPrepareBlocked(
            "shopee_global_control_region_unavailable",
            "approved Shopee storefront targets are unavailable",
            category="CAPABILITY",
        )
    return regions[0]


def _approved_regional_contract(
    approved_seed: Mapping[str, object],
    plan: Mapping[str, object],
) -> dict[str, object]:
    selected_urls = plan.get("selected_image_urls")
    models = _approved_plan_models(plan)
    pricing = approved_seed.get("target_pricing")
    parcel = _mapping(plan.get("parcel"), "approved parcel")
    package = _mapping(parcel.get("package_cm"), "approved package")
    copy = _mapping(plan.get("copy"), "approved copy")
    if (
        not isinstance(selected_urls, list)
        or not selected_urls
        or any(type(value) is not str or not value for value in selected_urls)
        or not isinstance(models, list)
        or not models
        or any(not isinstance(row, Mapping) for row in models)
        or not isinstance(pricing, Mapping)
    ):
        raise ShopeeOneClickPrepareBlocked(
            "approved_shopee_regional_contract_invalid",
            "approved Shopee regional facts are invalid",
            category="CONTENT",
        )
    return {
        "seller_sku": _nonempty(
            approved_seed.get("seller_sku"), "seller SKU"
        ),
        "model_sku": _nonempty(
            models[0].get("global_model_sku"), "model SKU"
        ),
        "model_skus": [
            {
                "model_sku": _nonempty(
                    row.get("global_model_sku"), "model SKU"
                ),
                "tier_index": list(row.get("tier_index") or ()),
            }
            for row in models
        ],
        "listing_copy": {
            "title": copy.get("title"),
            "description": copy.get("description"),
            "approved_copy_digest": copy.get("approved_copy_digest"),
            "approved_master_digest": copy.get("approved_copy_digest"),
        },
        "ordered_images": [
            {"position": index, "image_url": url}
            for index, url in enumerate(selected_urls, start=1)
        ],
        "parcel": {
            "weight_kg": parcel.get("weight_kg"),
            "package_cm": [
                package.get("length"),
                package.get("width"),
                package.get("height"),
            ],
        },
        "target_pricing": dict(pricing),
    }


def _read_approved_global_contract(
    transport: ShopeePrepareTransport,
    *,
    global_item_id: str,
    plan: Mapping[str, object],
    approved_plan_digest: str,
    exact_current: tuple[object, Mapping[str, object]] | None = None,
) -> dict[str, object]:
    approved = _approved_regional_contract(
        {
            "seller_sku": "GLOBAL",
            "target_pricing": {
                "local_original_price": _mapping(
                    plan.get("pricing"), "approved pricing"
                ).get("global_original_price"),
                "currency": "CNY",
            },
        },
        plan,
    )
    master = _read_global_master(
        transport,
        global_item_id=global_item_id,
        approved=approved,
    )
    official_models = _global_models(transport, global_item_id)
    expected_models = _approved_plan_models(plan)
    if not isinstance(expected_models, list) or not expected_models:
        raise ShopeeOneClickPrepareBlocked(
            "approved_shopee_model_contract_invalid",
            "approved Shopee model contract is invalid",
            category="CONTENT",
        )
    expected = {
        (
            _nonempty(row.get("global_model_sku"), "approved model SKU"),
            tuple(row.get("tier_index") or ()),
        )
        for row in expected_models
    }
    observed = {
        (
            _nonempty(row.get("global_model_sku"), "official model SKU"),
            tuple(row.get("tier_index") or ()),
        )
        for row in official_models
    }
    if expected != observed or len(expected) != len(expected_models):
        raise ShopeeOneClickPrepareBlocked(
            "shopee_existing_global_model_contract_drift",
            "official global models no longer match approval",
            category="CONTENT",
        )
    snapshot = plan.get("current_snapshot")
    if isinstance(snapshot, Mapping):
        if exact_current is None:
            exact_current = _current_existing_snapshot_candidate(
                plan, transport
            )
        current_candidate, current_observation = exact_current
        current_plan = getattr(current_candidate, "_plan", None)
        current_payload = (
            current_plan.payload()
            if current_plan is not None
            and callable(getattr(current_plan, "payload", None))
            else None
        )
        if (
            not isinstance(current_payload, Mapping)
            or current_payload.get("current_snapshot_digest")
            != plan.get("current_snapshot_digest")
            or str(
                _mapping(
                    current_observation.get("existing_global_item"),
                    "official current item",
                ).get("global_item_id")
            )
            != global_item_id
        ):
            raise ShopeeOneClickPrepareBlocked(
                "shopee_existing_current_snapshot_drift",
                "official Shopee existing-global snapshot drifted",
                category="CONTENT",
            )
    summary = {
        "global_item_id": global_item_id,
        "copy_digest": master["copy_digest"],
        "image_snapshot_digest": master["image_snapshot_digest"],
        "image_count": master["image_count"],
        "model_contract_digest": _digest(sorted(expected)),
    }
    return {
        **summary,
        "global_model_id": master["global_model_id"],
        "tier_index": master["tier_index"],
        "image_outcome": master["image_outcome"],
        "master_evidence_digest": _master_evidence_digest(
            approved_plan_digest=approved_plan_digest,
            global_item_id=global_item_id,
            image_snapshot_digest=master["image_snapshot_digest"],
            image_count=master["image_count"],
            model_contract_digest=_digest(expected_models),
        ),
    }


def dispatch_plan_native_target(request) -> dict[str, object]:
    command = _provider_command(request)
    kind = command.get("kind")
    if kind == "GLOBAL_NEW":
        return _dispatch_global_owner(request, command)
    if kind == "GLOBAL_EXISTING":
        return _dispatch_existing_global_owner(command)
    transport: ShopeeRuntimeTransport
    if kind == "REGION_FROM_SHARED":
        transport = _runtime_transport(command)
        command = _resolved_shared_region_command(
            request, command, transport
        )
        kind = "EXISTING_GLOBAL"
    else:
        transport = _runtime_transport(command)
    if kind not in {"EXISTING_GLOBAL", "NEW_GLOBAL"}:
        raise ShopeeOneClickPreDispatchError(
            "prepared Shopee command is incomplete"
        )
    if transport.verify_pre_dispatch is None or (
        transport.verify_pre_dispatch(command) is not True
    ):
        raise ShopeeOneClickPreDispatchError(
            "official Shopee proof drifted before dispatch"
        )
    occurrence_state = WriteOccurrenceState()
    image_upload_count = 0
    global_item_id: str
    tier_index: list[int]
    global_master_evidence: Mapping[str, object] | None = None
    if kind == "NEW_GLOBAL":
        approved = _mapping(command.get("approved"), "approved command")
        image_rows = approved.get("ordered_images")
        if (
            not isinstance(image_rows, list)
            or not image_rows
            or any(not isinstance(row, Mapping) for row in image_rows)
        ):
            raise ShopeeOneClickPreDispatchError(
                "approved global images are invalid"
            )
        resolver = transport.resolve_existing_global
        try:
            reusable = resolver(command) if resolver is not None else None
        except Exception as error:
            raise ShopeeOneClickPreDispatchError(
                "runtime exact-global resolution failed before writes"
            ) from error
        if reusable is not None:
            (
                global_item_id,
                tier_index,
                global_master_evidence,
            ) = _validated_reusable_global(reusable, len(image_rows))
        else:
            (
                global_item_id,
                tier_index,
                global_master_evidence,
                image_upload_count,
            ) = _create_new_global_master(
                request=request,
                command=command,
                transport=transport,
                image_rows=image_rows,
                occurrence_state=occurrence_state,
            )
    else:
        global_item_id = _positive_identity(
            command.get("global_item_id"), "global item identity"
        )
        tier = command.get("global_tier_index")
        if (
            not isinstance(tier, list)
            or not tier
            or any(type(value) is not int or value < 0 for value in tier)
        ):
            raise ShopeeOneClickPreDispatchError(
                "prepared global tier identity is invalid"
            )
        tier_index = list(tier)

    publish = transport.regional_publish
    if publish is None:
        if occurrence_state.external_writes:
            raise ShopeeOneClickDispatchError(
                "Shopee regional publish client is unavailable after global writes",
                writes=occurrence_state.external_writes,
                unknown=False,
                external_id=global_item_id,
                external_write_count=(
                    occurrence_state.external_write_count
                ),
                confirmed_lower_bound=(
                    occurrence_state.external_write_count
                ),
                possible_upper_bound=(
                    occurrence_state.external_write_count
                ),
            )
        raise ShopeeOneClickPreDispatchError(
            "Shopee regional publish client is unavailable"
        )
    try:
        regional_body = (
            _mapping(
                command.get("regional_publish_payload"),
                "regional publish payload",
            )
            if kind == "EXISTING_GLOBAL"
            else _regional_body(
                _mapping(command.get("approved"), "approved command"),
                region=_nonempty(command.get("region"), "region"),
                shop_id=_positive_int(
                    command.get("shop_id"), "shop identity"
                ),
                global_item_id=global_item_id,
                tier_index=tier_index,
                selected_logistics=list(
                    command["selected_logistics_ids"]
                ),
            )
        )
    except Exception as error:
        if occurrence_state.external_writes:
            raise ShopeeOneClickDispatchError(
                "regional publish payload is invalid after global writes",
                writes=occurrence_state.external_writes,
                unknown=False,
                external_id=global_item_id,
                external_write_count=(
                    occurrence_state.external_write_count
                ),
                confirmed_lower_bound=(
                    occurrence_state.external_write_count
                ),
                possible_upper_bound=(
                    occurrence_state.external_write_count
                ),
            ) from error
        raise
    occurrence = _open_write(
        request,
        occurrence_state,
        "regional_publish-1",
        REGIONAL_WRITE,
        external_id=global_item_id,
        evidence={
            "image_upload_invocation_count": image_upload_count,
            "image_upload_evidence_digest": _digest(
                {
                    "approved_count": len(
                        _mapping(
                            command.get("approved"), "approved command"
                        )["ordered_images"]
                    ),
                    "invoked_count": image_upload_count,
                }
            ),
        },
    )
    try:
        response = publish(regional_body)
    except Exception as error:
        raise _unknown_write_error(
            occurrence_state,
            occurrence,
            "regional publish transport outcome is unknown",
            external_id=global_item_id,
        ) from error
    external_id = _accepted_regional_identity(response)
    if external_id is None:
        if _explicit_write_rejection(response):
            _reject_write(
                request,
                occurrence_state,
                occurrence,
                external_id=global_item_id,
            )
            raise _rejected_write_error(
                occurrence_state,
                "regional publish was explicitly rejected",
                external_id=global_item_id,
            )
        raise _unknown_write_error(
            occurrence_state,
            occurrence,
            "regional publish response is not verified",
            external_id=global_item_id,
        )
    _confirm_write(
        request,
        occurrence_state,
        occurrence,
        external_id=external_id,
        evidence={
            "image_upload_invocation_count": image_upload_count,
            "image_upload_evidence_digest": _digest(
                {
                    "approved_count": len(
                        _mapping(
                            command.get("approved"), "approved command"
                        )["ordered_images"]
                    ),
                    "invoked_count": image_upload_count,
                }
            ),
        },
    )
    readback = transport.readback
    if readback is None:
        raise ShopeeOneClickDispatchError(
            "regional readback client is unavailable",
            writes=occurrence_state.external_writes,
            unknown=True,
            external_id=external_id,
            external_write_count=occurrence_state.external_write_count,
            confirmed_lower_bound=occurrence_state.external_write_count,
            possible_upper_bound=occurrence_state.external_write_count,
        )
    try:
        readback_command = dict(command)
        if kind == "NEW_GLOBAL":
            readback_command.update(
                {
                    "global_item_id": global_item_id,
                    "global_tier_index": tier_index,
                    "global_image_snapshot_digest": (
                        global_master_evidence["image_snapshot_digest"]
                    ),
                    "global_image_outcome": global_master_evidence[
                        "image_outcome"
                    ],
                }
            )
        observed_readback = readback(external_id, readback_command)
    except Exception as error:
        raise ShopeeOneClickDispatchError(
            "regional readback is unknown",
            writes=occurrence_state.external_writes,
            unknown=True,
            external_id=external_id,
            external_write_count=occurrence_state.external_write_count,
            confirmed_lower_bound=occurrence_state.external_write_count,
            possible_upper_bound=occurrence_state.external_write_count,
        ) from error
    if observed_readback is True:
        readback_evidence: Mapping[str, object] = {
            "verified": True,
            "manual_review_required": False,
            "matched_rule_ids": [],
            "observation_evidence_digest": _digest(
                {"legacy_fixture_verified": True}
            ),
        }
    elif isinstance(observed_readback, Mapping):
        readback_evidence = observed_readback
    else:
        readback_evidence = {"verified": False}
    if readback_evidence.get("verified") is not True:
        raise ShopeeOneClickDispatchError(
            "regional readback mismatch",
            writes=occurrence_state.external_writes,
            unknown=False,
            external_id=external_id,
            external_write_count=occurrence_state.external_write_count,
            confirmed_lower_bound=occurrence_state.external_write_count,
            possible_upper_bound=occurrence_state.external_write_count,
        )
    manual_review = (
        readback_evidence.get("manual_review_required") is True
        or _global_image_outcome(
            command, global_master_evidence
        ).get("manual_review_required")
        is True
    )
    global_image_outcome = _global_image_outcome(
        command, global_master_evidence
    )
    return {
        "canonical_status": (
            "SUCCEEDED_MANUAL_REVIEW"
            if manual_review
            else "SUCCEEDED"
        ),
        "reason_category": "POST_WRITE",
        "reason_scope": "TARGET",
        "reason_code": "shopee_plan_native_verified",
        "reason_detail": "official regional readback verified",
        "external_writes": occurrence_state.external_writes,
        "external_write_count": occurrence_state.external_write_count,
        "confirmed_external_write_count_lower_bound": (
            occurrence_state.external_write_count
        ),
        "possible_external_write_count_upper_bound": (
            occurrence_state.external_write_count
        ),
        "external_id": external_id,
        "submission_accepted": True,
        "readback_verified": True,
        "dispatch_outcome_unknown": False,
        "evidence": {
            "schema_version": "shopee-oneclick-redacted-evidence/v1",
            "external_writes_performed": list(
                occurrence_state.external_writes
            ),
            "source_copy_verified": True,
            "manual_review": manual_review,
            "rule_ids": sorted(
                {
                    str(value)
                    for value in (
                        list(
                            readback_evidence.get("matched_rule_ids") or ()
                        )
                        + list(
                            global_image_outcome.get("matched_rule_ids")
                            or ()
                        )
                    )
                    if str(value)
                }
            ),
            "observation_evidence_digest": readback_evidence.get(
                "observation_evidence_digest"
            ),
            "derived_translation_status": readback_evidence.get(
                "derived_translation_status"
            ),
            "derived_image_status": readback_evidence.get(
                "derived_image_status"
            ),
            "semantic_equivalence": "unverified",
            "profit_status": "unverified",
            "global_image_status": global_image_outcome.get(
                "global_image_status"
            ),
            "global_image_verification_scope": global_image_outcome.get(
                "global_image_verification_scope"
            ),
            "global_image_url_identity_exact": False,
            "global_image_approved_order_exact": global_image_outcome.get(
                "global_image_approved_order_exact"
            )
            is True,
            "global_image_snapshot_digest": (
                command.get("global_image_snapshot_digest")
                if kind == "EXISTING_GLOBAL"
                else global_master_evidence.get("image_snapshot_digest")
            ),
            "selected_logistics_count": len(
                command["selected_logistics_ids"]
            ),
            "image_upload_invocation_count": image_upload_count,
            "image_upload_evidence_digest": _digest(
                {
                    "approved_count": len(
                        _mapping(
                            command.get("approved"), "approved command"
                        )["ordered_images"]
                    ),
                    "invoked_count": image_upload_count,
                }
            ),
        },
    }


def _dispatch_existing_global_owner(
    command: Mapping[str, object],
) -> dict[str, object]:
    """Re-read an approved existing master and publish zero channel writes.

    The synthetic GLOBAL owner is still dispatched by the durable control
    plane so it can produce the shared-resource identity consumed by regional
    targets.  Its dispatch is an official GET-only verification: no global
    create, update, model-init, image upload, or stock-update callable is
    reachable from this path.
    """

    declaration = _mapping(
        command.get("shared_resource"), "shared resource declaration"
    )
    if (
        declaration.get("mode") != "EXISTING_GLOBAL"
        or declaration.get("expected_external_write_count") != 0
    ):
        raise ShopeeOneClickPreDispatchError(
            "Shopee existing GLOBAL declaration is invalid"
        )
    region = _nonempty(command.get("region"), "region")
    transport = _prepare_transport(region)
    if transport.credentials.shop_id != command.get("shop_id"):
        raise ShopeeOneClickPreDispatchError(
            "prepared Shopee shop binding drifted"
        )
    record = command.get("approved_global_plan_record")
    try:
        from shared_platform.shopee_global_plan import (
            rehydrate_approved_shopee_global_plan,
        )

        approved = rehydrate_approved_shopee_global_plan(record)
        plan = _mapping(
            _mapping(
                json.loads(record).get("approved_plan"),
                "approved plan record",
            ).get("plan"),
            "approved global plan",
        )
        candidate, official = _current_approved_global_candidate(
            approved,
            plan,
            transport,
        )
        approved.server_owned_execution_payload(candidate)
    except Exception as error:
        raise ShopeeOneClickPreDispatchError(
            "official existing Shopee GLOBAL proof drifted before dispatch"
        ) from error
    global_item_id = _positive_identity(
        official.get("global_item_id"),
        "official existing global item identity",
    )
    global_identity_digest = _text_digest(global_item_id)
    master_evidence_digest = _nonempty_digest(
        official.get("master_evidence_digest"),
        "official existing master evidence",
    )
    if (
        approved.mode != "EXISTING_GLOBAL"
        or approved.approved_plan_digest
        != command.get("approved_plan_digest")
        or declaration.get("master_lineage_digest")
        != approved.approved_plan_digest
        or declaration.get("global_identity_digest")
        != global_identity_digest
        or declaration.get("master_evidence_digest")
        != master_evidence_digest
    ):
        raise ShopeeOneClickPreDispatchError(
            "official existing Shopee GLOBAL identity drifted"
        )
    shared = {
        "schema_version": declaration["schema_version"],
        "policy_version": declaration["policy_version"],
        "mode": "EXISTING_GLOBAL",
        "owner_key": declaration["owner_key"],
        "master_lineage_digest": declaration["master_lineage_digest"],
        "global_identity_digest": global_identity_digest,
        "master_evidence_digest": master_evidence_digest,
    }
    return {
        "canonical_status": "SUCCEEDED",
        "reason_category": "PRE_SUBMIT",
        "reason_scope": "TARGET",
        "reason_code": "shopee_existing_global_verified_no_write",
        "reason_detail": "official existing global master readback verified",
        "external_writes": (),
        "external_write_count": 0,
        "confirmed_external_write_count_lower_bound": 0,
        "possible_external_write_count_upper_bound": 0,
        "external_id": "sha256:" + global_identity_digest,
        "submission_accepted": False,
        "readback_verified": True,
        "dispatch_outcome_unknown": False,
        "evidence": {"shared_resource": shared},
    }


def _dispatch_global_owner(
    request: object,
    command: Mapping[str, object],
) -> dict[str, object]:
    declaration = _mapping(
        command.get("shared_resource"), "shared resource declaration"
    )
    if (
        declaration.get("mode") != "ENSURE_NEW"
        or declaration.get("expected_external_write_count")
        != len(command.get("selected_image_positions") or ()) + 2
    ):
        raise ShopeeOneClickPreDispatchError(
            "Shopee GLOBAL declaration is invalid"
        )
    transport = _runtime_transport(command)
    if transport.verify_pre_dispatch is None or (
        transport.verify_pre_dispatch(command) is not True
    ):
        raise ShopeeOneClickPreDispatchError(
            "official Shopee GLOBAL proof drifted before dispatch"
        )
    approved = _mapping(command.get("approved"), "approved command")
    image_rows = approved.get("ordered_images")
    if not isinstance(image_rows, list) or not image_rows:
        raise ShopeeOneClickPreDispatchError(
            "approved global images are invalid"
        )
    occurrence_state = WriteOccurrenceState()
    (
        global_item_id,
        _tier_index,
        master,
        image_upload_count,
    ) = _create_new_global_master(
        request=request,
        command=command,
        transport=transport,
        image_rows=image_rows,
        occurrence_state=occurrence_state,
    )
    if (
        occurrence_state.external_write_count
        != declaration["expected_external_write_count"]
    ):
        raise ShopeeOneClickDispatchError(
            "Shopee GLOBAL write count does not match approval",
            writes=occurrence_state.external_writes,
            unknown=False,
            external_id=global_item_id,
            external_write_count=occurrence_state.external_write_count,
            confirmed_lower_bound=occurrence_state.external_write_count,
            possible_upper_bound=occurrence_state.external_write_count,
        )
    global_identity_digest = _text_digest(global_item_id)
    master_evidence_digest = _master_evidence_digest(
        approved_plan_digest=_nonempty_digest(
            command.get("approved_plan_digest"),
            "approved plan digest",
        ),
        global_item_id=global_item_id,
        image_snapshot_digest=_nonempty_digest(
            master.get("image_snapshot_digest"),
            "image snapshot digest",
        ),
        image_count=_positive_int(
            master.get("image_count"), "official image count"
        ),
        model_contract_digest=_nonempty_digest(
            command.get("model_contract_digest"),
            "model contract digest",
        ),
    )
    shared = {
        "schema_version": declaration["schema_version"],
        "policy_version": declaration["policy_version"],
        "mode": "ENSURE_NEW",
        "owner_key": declaration["owner_key"],
        "master_lineage_digest": declaration[
            "master_lineage_digest"
        ],
        "global_identity_digest": global_identity_digest,
        "master_evidence_digest": master_evidence_digest,
    }
    return {
        "canonical_status": "SUCCEEDED",
        "reason_category": "POST_WRITE",
        "reason_scope": "TARGET",
        "reason_code": "shopee_global_master_verified",
        "reason_detail": "official global master creation and readback verified",
        "external_writes": occurrence_state.external_writes,
        "external_write_count": occurrence_state.external_write_count,
        "confirmed_external_write_count_lower_bound": (
            occurrence_state.external_write_count
        ),
        "possible_external_write_count_upper_bound": (
            occurrence_state.external_write_count
        ),
        "external_id": "sha256:" + global_identity_digest,
        "submission_accepted": True,
        "readback_verified": True,
        "dispatch_outcome_unknown": False,
        "evidence": {"shared_resource": shared},
    }


def _resolved_shared_region_command(
    request: object,
    command: Mapping[str, object],
    transport: ShopeeRuntimeTransport,
) -> dict[str, object]:
    context = getattr(request, "shared_resource_context", None)
    if (
        not isinstance(context, Mapping)
        or set(context)
        != {
            "schema_version",
            "policy_version",
            "owner_key",
            "master_lineage_digest",
            "global_identity_digest",
            "master_evidence_digest",
        }
    ):
        raise ShopeeOneClickPreDispatchError(
            "verified Shopee shared resource is unavailable"
        )
    resolver = transport.resolve_existing_global
    try:
        resolved = resolver(command) if resolver is not None else None
    except Exception as error:
        raise ShopeeOneClickPreDispatchError(
            "official shared global resolution failed before regional dispatch"
        ) from error
    if not isinstance(resolved, Mapping) or resolved.get("verified") is not True:
        raise ShopeeOneClickPreDispatchError(
            "official shared global proof is unavailable"
        )
    global_item_id = _positive_identity(
        resolved.get("global_item_id"), "resolved global item identity"
    )
    if (
        _text_digest(global_item_id)
        != context.get("global_identity_digest")
        or resolved.get("master_evidence_digest")
        != context.get("master_evidence_digest")
        or command.get("approved_plan_digest")
        != context.get("master_lineage_digest")
    ):
        raise ShopeeOneClickPreDispatchError(
            "official shared global identity drifted"
        )
    expected_models = command.get("expected_models")
    if not isinstance(expected_models, list) or not expected_models:
        raise ShopeeOneClickPreDispatchError(
            "approved regional model contract is invalid"
        )
    resolved_command = dict(command)
    resolved_command.update(
        {
            "kind": "EXISTING_GLOBAL",
            "global_item_id": global_item_id,
            "global_model_id": resolved.get("global_model_id"),
            "global_tier_index": list(
                expected_models[0].get("tier_index") or ()
            ),
            "global_image_snapshot_digest": resolved.get(
                "image_snapshot_digest"
            ),
            "global_image_outcome": resolved.get("image_outcome"),
            "regional_publish_payload": _regional_body_models(
                _mapping(command.get("approved"), "approved command"),
                region=_nonempty(command.get("region"), "region"),
                shop_id=_positive_int(
                    command.get("shop_id"), "shop identity"
                ),
                global_item_id=global_item_id,
                models=expected_models,
                selected_logistics=list(
                    command["selected_logistics_ids"]
                ),
            ),
        }
    )
    return resolved_command


def _validated_reusable_global(
    evidence: object, approved_image_count: int
) -> tuple[str, list[int], Mapping[str, object]]:
    if not isinstance(evidence, Mapping) or evidence.get("verified") is not True:
        raise ShopeeOneClickPreDispatchError(
            "runtime reusable global proof is invalid"
        )
    global_item_id = _positive_identity(
        evidence.get("global_item_id"), "reusable global item identity"
    )
    tier = evidence.get("tier_index")
    if (
        not isinstance(tier, list)
        or not tier
        or any(type(value) is not int or value < 0 for value in tier)
        or evidence.get("image_count") != approved_image_count
        or type(evidence.get("image_snapshot_digest")) is not str
        or len(evidence["image_snapshot_digest"]) != 64
        or not isinstance(evidence.get("image_outcome"), Mapping)
    ):
        raise ShopeeOneClickPreDispatchError(
            "runtime reusable global proof is invalid"
        )
    return global_item_id, list(tier), evidence


def _create_new_global_master(
    *,
    request: object,
    command: Mapping[str, object],
    transport: ShopeeRuntimeTransport,
    image_rows: list[Mapping[str, object]],
    occurrence_state: WriteOccurrenceState,
) -> tuple[
    str,
    list[int],
    Mapping[str, object],
    int,
]:
    prepare_image = transport.prepare_image
    upload = transport.upload_image
    add_global = transport.add_global_item
    init_model = transport.init_global_model
    verify_created = transport.verify_created_global
    if (
        prepare_image is None
        or upload is None
        or add_global is None
        or init_model is None
        or verify_created is None
    ):
        raise ShopeeOneClickPreDispatchError(
            "Shopee global-create runtime is unavailable"
        )
    prepared_images: list[ShopeePreparedImage] = []
    try:
        for index, row in enumerate(image_rows, start=1):
            payload = prepare_image(
                _nonempty(row.get("image_url"), "approved image URL"),
                index,
            )
            if (
                not isinstance(payload, ShopeePreparedImage)
                or not payload.content
                or _image_format(payload.content)
                != (payload.media_type, payload.suffix)
            ):
                raise ValueError("prepared image payload is invalid")
            prepared_images.append(payload)
    except Exception as error:
        raise ShopeeOneClickPreDispatchError(
            "approved image source could not be prepared before dispatch"
        ) from error
    image_upload_count = 0
    uploaded_ids: list[str] = []
    for index, payload in enumerate(prepared_images, start=1):
        image_upload_count += 1
        occurrence = _open_write(
            request,
            occurrence_state,
            f"image_upload-{index}",
            IMAGE_UPLOAD_WRITE,
            evidence=_image_upload_progress(
                len(image_rows), image_upload_count
            ),
        )
        try:
            response = upload(payload, index)
        except Exception as error:
            raise _unknown_write_error(
                occurrence_state,
                occurrence,
                "global image upload outcome is unknown",
            ) from error
        image_id = _accepted_image_identity(response)
        if image_id is None or image_id in uploaded_ids:
            if _explicit_write_rejection(response):
                _reject_write(
                    request,
                    occurrence_state,
                    occurrence,
                )
                raise _rejected_write_error(
                    occurrence_state,
                    "global image upload was explicitly rejected",
                )
            raise _unknown_write_error(
                occurrence_state,
                occurrence,
                "global image upload response is not verified",
            )
        _confirm_write(
            request,
            occurrence_state,
            occurrence,
            evidence=_image_upload_progress(
                len(image_rows), image_upload_count
            ),
        )
        uploaded_ids.append(image_id)
    create_body = dict(
        _mapping(
            command.get("global_create_payload"), "global create payload"
        )
    )
    create_body["image"] = {"image_id_list": uploaded_ids}
    occurrence = _open_write(
        request,
        occurrence_state,
        "global_create-1",
        GLOBAL_WRITE,
        evidence=_image_upload_progress(
            len(image_rows), image_upload_count
        ),
    )
    try:
        create_response = add_global(create_body)
    except Exception as error:
        raise _unknown_write_error(
            occurrence_state,
            occurrence,
            "global item create outcome is unknown",
        ) from error
    global_item_id = _accepted_global_identity(create_response)
    if global_item_id is None:
        if _explicit_write_rejection(create_response):
            _reject_write(
                request,
                occurrence_state,
                occurrence,
            )
            raise _rejected_write_error(
                occurrence_state,
                "global item create was explicitly rejected",
            )
        raise _unknown_write_error(
            occurrence_state,
            occurrence,
            "global item create response is not verified",
        )
    _confirm_write(
        request,
        occurrence_state,
        occurrence,
        external_id=global_item_id,
        evidence=_image_upload_progress(
            len(image_rows), image_upload_count
        ),
    )
    model_body = dict(
        _mapping(
            command.get("global_model_payload"), "global model payload"
        )
    )
    model_body["global_item_id"] = int(global_item_id)
    occurrence = _open_write(
        request,
        occurrence_state,
        "model_init-1",
        GLOBAL_MODEL_WRITE,
        external_id=global_item_id,
        evidence=_image_upload_progress(
            len(image_rows), image_upload_count
        ),
    )
    try:
        model_response = init_model(global_item_id, model_body)
    except Exception as error:
        raise _unknown_write_error(
            occurrence_state,
            occurrence,
            "global model initialization outcome is unknown",
            external_id=global_item_id,
        ) from error
    if _accepted_empty_write_response(model_response) is not True:
        if _explicit_write_rejection(model_response):
            _reject_write(
                request,
                occurrence_state,
                occurrence,
                external_id=global_item_id,
            )
            raise _rejected_write_error(
                occurrence_state,
                "global model initialization was explicitly rejected",
                external_id=global_item_id,
            )
        raise _unknown_write_error(
            occurrence_state,
            occurrence,
            "global model initialization response is not verified",
            external_id=global_item_id,
        )
    _confirm_write(
        request,
        occurrence_state,
        occurrence,
        external_id=global_item_id,
        evidence=_image_upload_progress(
            len(image_rows), image_upload_count
        ),
    )
    try:
        master = verify_created(global_item_id, command)
    except Exception as error:
        raise ShopeeOneClickDispatchError(
            "created global master readback is unknown",
            writes=occurrence_state.external_writes,
            unknown=True,
            external_id=global_item_id,
            external_write_count=occurrence_state.external_write_count,
            confirmed_lower_bound=occurrence_state.external_write_count,
            possible_upper_bound=occurrence_state.external_write_count,
        ) from error
    if (
        not isinstance(master, Mapping)
        or master.get("verified") is not True
        or master.get("image_snapshot_digest")
        != _image_id_snapshot_digest(uploaded_ids)
        or master.get("image_count") != len(uploaded_ids)
    ):
        raise ShopeeOneClickDispatchError(
            "created global master readback mismatch",
            writes=occurrence_state.external_writes,
            unknown=False,
            external_id=global_item_id,
            external_write_count=occurrence_state.external_write_count,
            confirmed_lower_bound=occurrence_state.external_write_count,
            possible_upper_bound=occurrence_state.external_write_count,
        )
    tier = master.get("tier_index")
    if (
        not isinstance(tier, list)
        or not tier
        or any(type(value) is not int or value < 0 for value in tier)
    ):
        raise ShopeeOneClickDispatchError(
            "created global model tier readback is invalid",
            writes=occurrence_state.external_writes,
            unknown=False,
            external_id=global_item_id,
            external_write_count=occurrence_state.external_write_count,
            confirmed_lower_bound=occurrence_state.external_write_count,
            possible_upper_bound=occurrence_state.external_write_count,
        )
    return (
        global_item_id,
        list(tier),
        master,
        image_upload_count,
    )


def _image_upload_progress(
    approved_count: int, invocation_count: int
) -> dict[str, object]:
    return {
        "image_upload_invocation_count": invocation_count,
        "image_upload_evidence_digest": _digest(
            {
                "approved_count": approved_count,
                "invoked_count": invocation_count,
            }
        ),
    }


def _master_evidence_digest(
    *,
    approved_plan_digest: str,
    global_item_id: str,
    image_snapshot_digest: str,
    image_count: int,
    model_contract_digest: str,
) -> str:
    return _digest(
        {
            "approved_plan_digest": approved_plan_digest,
            "global_identity_digest": _text_digest(global_item_id),
            "image_snapshot_digest": image_snapshot_digest,
            "image_count": image_count,
            "model_contract_digest": model_contract_digest,
        }
    )


def validate_readonly_observation(
    observation: Mapping[str, object],
    *,
    expected_global_item_id: str,
    expected_model_sku: str,
    selected_logistics: tuple[int, ...],
) -> dict[str, object]:
    """Compatibility fixture validator retained as a strict shape gate."""
    if (
        not isinstance(observation, Mapping)
        or observation.get("error") not in (None, "")
    ):
        raise ShopeeOneClickPreDispatchError(
            "official Shopee top-level error"
        )
    global_item = observation.get("global_item")
    models = observation.get("models")
    logistics = observation.get("logistics")
    if (
        not isinstance(global_item, Mapping)
        or str(global_item.get("global_item_id") or "")
        != expected_global_item_id
    ):
        raise ShopeeOneClickPreDispatchError(
            "official global identity is invalid"
        )
    title = global_item.get("global_item_name")
    description = global_item.get("description")
    image = global_item.get("image")
    urls = image.get("image_url_list") if isinstance(image, Mapping) else None
    ids = image.get("image_id_list") if isinstance(image, Mapping) else None
    if (
        type(title) is not str
        or not title.strip()
        or type(description) is not str
        or not description
        or not _exact_string_list(urls)
        or not _exact_string_list(ids)
        or len(urls) != len(ids)
    ):
        raise ShopeeOneClickPreDispatchError(
            "official global copy/image shape is invalid"
        )
    if not isinstance(models, list) or any(
        not isinstance(row, Mapping) for row in models
    ):
        raise ShopeeOneClickPreDispatchError(
            "official model shape is invalid"
        )
    matches = [
        row
        for row in models
        if row.get("global_model_sku") == expected_model_sku
    ]
    if (
        len(matches) != 1
        or not str(matches[0].get("global_model_id") or "").isdigit()
    ):
        raise ShopeeOneClickPreDispatchError(
            "official model identity is invalid"
        )
    if not isinstance(logistics, list) or any(
        not isinstance(row, Mapping) for row in logistics
    ):
        raise ShopeeOneClickPreDispatchError(
            "official logistics shape is invalid"
        )
    enabled = []
    for row in logistics:
        identifier = row.get("logistic_id")
        if (
            type(identifier) is not int
            or identifier <= 0
            or type(row.get("enabled")) is not bool
        ):
            raise ShopeeOneClickPreDispatchError(
                "official logistics row is invalid"
            )
        if row["enabled"]:
            enabled.append(identifier)
    if (
        len(enabled) != len(set(enabled))
        or tuple(sorted(enabled)) != tuple(sorted(selected_logistics))
    ):
        raise ShopeeOneClickPreDispatchError(
            "official logistics set drifted"
        )
    return {
        "global_item_id": expected_global_item_id,
        "global_model_id": str(matches[0]["global_model_id"]),
        "image_count": len(urls),
        "enabled_logistics_count": len(enabled),
    }


def _prepare_transport(region: str) -> ShopeePrepareTransport:
    if _prepare_transport_factory is not None:
        value = _prepare_transport_factory(region)
        if isinstance(value, ShopeePrepareTransport):
            return value
    credentials = _current_credentials(region)
    from modules.shopee.client import merchant_get, shop_get

    return ShopeePrepareTransport(
        credentials=credentials,
        merchant_get=lambda path, params: merchant_get(
            path,
            credentials.merchant_id,
            credentials.merchant_token,
            dict(params),
        ),
        shop_get=lambda path, params=None: shop_get(
            path,
            credentials.shop_id,
            credentials.shop_token,
            dict(params or {}),
        ),
    )


def _runtime_transport(command: Mapping[str, object]) -> ShopeeRuntimeTransport:
    if _runtime_transport_factory is not None:
        value = _runtime_transport_factory()
        if isinstance(value, ShopeeRuntimeTransport):
            return value
    region = _nonempty(command.get("region"), "region")
    prepare = _prepare_transport(region)
    if prepare.credentials.shop_id != command.get("shop_id"):
        raise ShopeeOneClickPreDispatchError(
            "prepared Shopee shop binding drifted"
        )
    from modules.shopee.client import (
        merchant_get,
        merchant_post,
        resolve_global_item_id,
    )

    def verify(current: Mapping[str, object]) -> bool:
        if current.get("kind") == "GLOBAL_NEW":
            try:
                from shared_platform.shopee_global_plan import (
                    rehydrate_approved_shopee_global_plan,
                )

                approved_plan = rehydrate_approved_shopee_global_plan(
                    current.get("approved_global_plan_record")
                )
                plan = _mapping(
                    _mapping(
                        json.loads(
                            current["approved_global_plan_record"]
                        ).get("approved_plan"),
                        "approved plan record",
                    ).get("plan"),
                    "approved global plan",
                )
                candidate, _official = _current_approved_global_candidate(
                    approved_plan,
                    plan,
                    prepare,
                )
                approved_plan.server_owned_execution_payload(candidate)
                regional_matches = []
                targets = current.get("approved_storefront_targets", [])
                if targets:
                    regional_matches = _scan_regional_sku(
                        prepare,
                        seller_sku=str(
                            _mapping(
                                current.get("approved"),
                                "approved command",
                            )["seller_sku"]
                        ),
                    )["matches"]
            except Exception:
                return False
            return not regional_matches
        approved = _mapping(current.get("approved"), "approved command")
        try:
            if current.get("kind") == "NEW_GLOBAL":
                candidates = _scan_global_model_candidates(
                    prepare,
                    model_sku=_nonempty(
                        approved.get("model_sku"), "model SKU"
                    ),
                )
                master = (
                    _read_global_master(
                        prepare,
                        global_item_id=candidates[0],
                        approved=approved,
                    )
                    if len(candidates) == 1
                    else None
                )
            else:
                candidates = []
                master = _read_global_master(
                    prepare,
                    global_item_id=str(current["global_item_id"]),
                    approved=approved,
                )
            regional = _scan_regional_sku(
                prepare,
                seller_sku=str(approved["seller_sku"]),
            )
            logistics = _compatible_logistics(
                prepare, approved=approved, region=region
            )
        except Exception:
            return False
        if current.get("kind") == "NEW_GLOBAL":
            return bool(
                len(candidates) <= 1
                and not regional["matches"]
                and logistics == list(current["selected_logistics_ids"])
                and not _missing_create_facts(approved)
            )
        return bool(
            master["global_model_id"] == str(current["global_model_id"])
            and master["tier_index"] == list(current["global_tier_index"])
            and master["image_snapshot_digest"]
            == current["global_image_snapshot_digest"]
            and master["image_observation"]["evidence_digest"]
            == _mapping(
                current.get("global_image_observation"),
                "global image observation",
            ).get("evidence_digest")
            and master["image_outcome"]["evidence_digest"]
            == _mapping(
                current.get("global_image_outcome"),
                "global image outcome",
            ).get("evidence_digest")
            and not regional["matches"]
            and logistics == list(current["selected_logistics_ids"])
        )

    def prepare_image(url: str, _position: int) -> ShopeePreparedImage:
        return _download_public_https_image(url)

    def upload(payload: ShopeePreparedImage, position: int) -> object:
        from pathlib import Path
        import tempfile
        from modules.shopee.client import upload_image

        with tempfile.TemporaryDirectory(
            prefix="oneclick_shopee_image_"
        ) as directory:
            path = Path(directory) / f"image_{position}{payload.suffix}"
            path.write_bytes(payload.content)
            return upload_image(
                path, scene="normal" if position == 1 else "desc"
            )

    def add_global(body: Mapping[str, object]) -> object:
        return merchant_post(
            GLOBAL_CREATE_PATH,
            prepare.credentials.merchant_id,
            prepare.credentials.merchant_token,
            dict(body),
        )

    def init_model(
        _global_item_id: str, body: Mapping[str, object]
    ) -> object:
        return merchant_post(
            GLOBAL_MODEL_INIT_PATH,
            prepare.credentials.merchant_id,
            prepare.credentials.merchant_token,
            dict(body),
        )

    def verify_created(
        global_item_id: str, current: Mapping[str, object]
    ) -> Mapping[str, object]:
        if current.get("kind") == "GLOBAL_NEW":
            try:
                record = json.loads(
                    current["approved_global_plan_record"]
                )
                plan = _mapping(
                    _mapping(
                        record.get("approved_plan"),
                        "approved plan record",
                    ).get("plan"),
                    "approved global plan",
                )
                master = _read_approved_global_contract(
                    prepare,
                    global_item_id=global_item_id,
                    plan=plan,
                    approved_plan_digest=_nonempty_digest(
                        current.get("approved_plan_digest"),
                        "approved plan digest",
                    ),
                )
            except Exception as error:
                raise ShopeeOneClickPreDispatchError(
                    "created global master exact proof is unavailable"
                ) from error
            return {
                "verified": True,
                "global_item_id": global_item_id,
                **master,
            }
        approved = _mapping(current.get("approved"), "approved command")
        master = _read_global_master(
            prepare,
            global_item_id=global_item_id,
            approved=approved,
        )
        return {
            "verified": True,
            "global_item_id": global_item_id,
            "global_model_id": master["global_model_id"],
            "tier_index": master["tier_index"],
            "image_snapshot_digest": master["image_snapshot_digest"],
            "image_count": master["image_count"],
            "image_outcome": master["image_outcome"],
        }

    def resolve_existing(
        current: Mapping[str, object]
    ) -> Mapping[str, object] | None:
        if current.get("kind") == "REGION_FROM_SHARED":
            plan = _mapping(
                current.get("approved_global_plan_payload"),
                "approved global plan",
            )
            models = current.get("expected_models")
            if not isinstance(models, list) or not models:
                raise ShopeeOneClickPreDispatchError(
                    "approved global model contract is unavailable"
                )
            candidates_by_sku = [
                _scan_global_model_candidates(
                    prepare,
                    model_sku=_nonempty(
                        row.get("global_model_sku"),
                        "approved model SKU",
                    ),
                )
                for row in models
            ]
            if (
                any(len(values) != 1 for values in candidates_by_sku)
                or len({values[0] for values in candidates_by_sku}) != 1
            ):
                raise ShopeeOneClickPreDispatchError(
                    "official shared global model identity is ambiguous"
                )
            global_id = candidates_by_sku[0][0]
            master = _read_approved_global_contract(
                prepare,
                global_item_id=global_id,
                plan=plan,
                approved_plan_digest=_nonempty_digest(
                    current.get("approved_plan_digest"),
                    "approved plan digest",
                ),
            )
            return {
                "verified": True,
                "global_item_id": global_id,
                **master,
            }
        approved = _mapping(current.get("approved"), "approved command")
        candidates = _scan_global_model_candidates(
            prepare,
            model_sku=_nonempty(approved.get("model_sku"), "model SKU"),
        )
        if not candidates:
            return None
        if len(candidates) != 1:
            raise ShopeeOneClickPreDispatchError(
                "runtime global model identity is ambiguous"
            )
        master = _read_global_master(
            prepare,
            global_item_id=candidates[0],
            approved=approved,
        )
        return {
            "verified": True,
            "global_item_id": candidates[0],
            "global_model_id": master["global_model_id"],
            "tier_index": master["tier_index"],
            "image_snapshot_digest": master["image_snapshot_digest"],
            "image_count": master["image_count"],
            "image_outcome": master["image_outcome"],
        }

    def publish(body: Mapping[str, object]) -> object:
        response = merchant_post(
            REGIONAL_TASK_PATH,
            prepare.credentials.merchant_id,
            prepare.credentials.merchant_token,
            dict(body),
        )
        if not isinstance(response, Mapping) or response.get("error"):
            return response
        payload = response.get("response")
        task_id = (
            payload.get("publish_task_id")
            if isinstance(payload, Mapping)
            else None
        )
        if isinstance(task_id, bool) or not str(task_id or "").isdigit():
            return response
        for _ in range(3):
            result = merchant_get(
                REGIONAL_TASK_RESULT_PATH,
                prepare.credentials.merchant_id,
                prepare.credentials.merchant_token,
                {"publish_task_id": int(task_id)},
            )
            if not isinstance(result, Mapping) or result.get("error"):
                raise RuntimeError("publish task readback is invalid")
            task = result.get("response")
            if not isinstance(task, Mapping):
                raise RuntimeError("publish task response is invalid")
            status = str(task.get("publish_status") or "").lower()
            if status == "success":
                item_id = task.get("item_id")
                if item_id is None and isinstance(task.get("success"), Mapping):
                    item_id = task["success"].get("item_id")
                return {"accepted": True, "external_id": str(item_id or "")}
            if status == "failed":
                return {"accepted": False}
        raise RuntimeError("publish task did not converge")

    def readback(
        item_id: str, current: Mapping[str, object]
    ) -> Mapping[str, object]:
        from modules.shopee.client import shop_get

        base = shop_get(
            "/api/v2/product/get_item_base_info",
            prepare.credentials.shop_id,
            prepare.credentials.shop_token,
            {"item_id_list": item_id},
        )
        rows = (
            (base.get("response") or {}).get("item_list")
            if isinstance(base, Mapping)
            else None
        )
        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], Mapping)
            or str(rows[0].get("item_id") or "") != item_id
        ):
            return {"verified": False, "reason_code": "regional_item_ambiguous"}
        item = rows[0]
        resolved = resolve_global_item_id(
            prepare.credentials.shop_id,
            prepare.credentials.merchant_id,
            prepare.credentials.merchant_token,
            item_id,
        )
        if type(item.get("has_model")) is not bool:
            return {
                "verified": False,
                "reason_code": "regional_model_shape_invalid",
            }
        model_rows: object = []
        if item["has_model"]:
            model = shop_get(
                "/api/v2/product/get_model_list",
                prepare.credentials.shop_id,
                prepare.credentials.shop_token,
                {"item_id": int(item_id)},
            )
            model_data = (
                model.get("response")
                if isinstance(model, Mapping) and not model.get("error")
                else None
            )
            model_rows = (
                model_data.get("model")
                if isinstance(model_data, Mapping)
                else None
            )
        return _regional_readback_evidence(
            item=item,
            models=model_rows,
            resolved_global_item_id=resolved,
            command=current,
            item_id=item_id,
        )

    return ShopeeRuntimeTransport(
        verify_pre_dispatch=verify,
        regional_publish=publish,
        readback=readback,
        prepare_image=prepare_image,
        upload_image=upload,
        add_global_item=add_global,
        init_global_model=init_model,
        verify_created_global=verify_created,
        resolve_existing_global=resolve_existing,
    )


def _regional_readback_evidence(
    *,
    item: Mapping[str, object],
    models: object,
    resolved_global_item_id: object,
    command: Mapping[str, object],
    item_id: str,
) -> dict[str, object]:
    """Validate official regional hard facts and return redacted observations."""
    from shared_platform.target_scoped_release_contracts import (
        evaluate_shopee_regional_copy_observation,
        evaluate_shopee_regional_image_observation,
        shopee_regional_observation_outcome,
    )

    approved = _mapping(command.get("approved"), "approved command")
    listing_copy = _mapping(approved.get("listing_copy"), "listing copy")
    pricing = _mapping(approved.get("target_pricing"), "target pricing")
    expected_seller_sku = _nonempty(
        approved.get("seller_sku"), "seller SKU"
    )
    expected_model_rows = command.get("expected_models")
    if expected_model_rows is None:
        expected_model_rows = [
            {
                "global_model_sku": _nonempty(
                    approved.get("model_sku"), "model SKU"
                ),
                "tier_index": command.get("global_tier_index"),
            }
        ]
    if not isinstance(expected_model_rows, list) or not expected_model_rows:
        raise ShopeeOneClickPreDispatchError(
            "approved model contract is unavailable"
        )
    expected_models: set[tuple[str, tuple[int, ...]]] = set()
    for row in expected_model_rows:
        if not isinstance(row, Mapping):
            raise ShopeeOneClickPreDispatchError(
                "approved model contract is malformed"
            )
        sku = _nonempty(
            row.get("global_model_sku", row.get("model_sku")),
            "model SKU",
        )
        tier = row.get("tier_index")
        if (
            not isinstance(tier, list)
            or not tier
            or any(type(value) is not int or value < 0 for value in tier)
            or (sku, tuple(tier)) in expected_models
        ):
            raise ShopeeOneClickPreDispatchError(
                "approved model contract is malformed"
            )
        expected_models.add((sku, tuple(tier)))
    expected_currency = _nonempty(
        pricing.get("currency"), "target currency"
    )
    expected_price = _finite_positive_decimal(
        pricing.get("local_original_price"), "target original price"
    )
    expected_logistics = command.get("selected_logistics_ids")
    if (
        not isinstance(expected_logistics, list)
        or not expected_logistics
        or any(
            type(value) is not int or value <= 0
            for value in expected_logistics
        )
        or len(expected_logistics) != len(set(expected_logistics))
    ):
        raise ShopeeOneClickPreDispatchError(
            "prepared regional hard facts are invalid"
        )

    hard: dict[str, bool] = {
        "item_identity_exact": (
            _positive_identity(item.get("item_id"), "regional item identity")
            == item_id
        ),
        "global_linkage_exact": (
            _positive_identity(
                resolved_global_item_id, "resolved global item identity"
            )
            == str(command["global_item_id"])
        ),
        "normal_status_exact": (
            type(item.get("item_status")) is str
            and item["item_status"] == "NORMAL"
        ),
        "seller_sku_exact": (
            type(item.get("item_sku")) is str
            and item["item_sku"] == expected_seller_sku
        ),
    }

    if item.get("has_model") is not True:
        hard["model_identity_exact"] = False
        model_rows: list[Mapping[str, object]] = []
    elif not isinstance(models, list) or any(
        not isinstance(row, Mapping) for row in models
    ):
        raise ShopeeOneClickPreDispatchError(
            "regional model list is malformed"
        )
    else:
        model_rows = list(models)
    seen_model_ids: set[str] = set()
    seen_model_skus: set[str] = set()
    observed_models: set[tuple[str, tuple[int, ...]]] = set()
    for row in model_rows:
        model_id = _positive_identity(
            row.get("model_id"), "regional model identity"
        )
        model_sku = row.get("model_sku")
        tier = row.get("tier_index")
        if (
            type(model_sku) is not str
            or not model_sku
            or not isinstance(tier, list)
            or not tier
            or any(type(value) is not int or value < 0 for value in tier)
            or model_id in seen_model_ids
            or model_sku in seen_model_skus
        ):
            raise ShopeeOneClickPreDispatchError(
                "regional model identity shape is malformed"
            )
        seen_model_ids.add(model_id)
        seen_model_skus.add(model_sku)
        observed_models.add((model_sku, tuple(tier)))
    hard["model_identity_exact"] = observed_models == expected_models

    price_rows = item.get("price_info")
    if not isinstance(price_rows, list) or not price_rows or any(
        not isinstance(row, Mapping) for row in price_rows
    ):
        raise ShopeeOneClickPreDispatchError(
            "regional price list is malformed"
        )
    currency_matches: list[Decimal] = []
    seen_currencies: set[str] = set()
    for row in price_rows:
        currency = row.get("currency")
        if (
            type(currency) is not str
            or not currency
            or currency in seen_currencies
        ):
            raise ShopeeOneClickPreDispatchError(
                "regional price currency shape is malformed"
            )
        seen_currencies.add(currency)
        original_price = _finite_positive_decimal(
            row.get("original_price"), "regional original price"
        )
        if currency == expected_currency:
            currency_matches.append(original_price)
    hard["local_price_currency_exact"] = (
        len(currency_matches) == 1
        and currency_matches[0] == expected_price
    )

    logistics = item.get("logistic_info")
    if not isinstance(logistics, list) or any(
        not isinstance(row, Mapping) for row in logistics
    ):
        raise ShopeeOneClickPreDispatchError(
            "regional logistics list is malformed"
        )
    enabled: list[int] = []
    seen_logistics: set[int] = set()
    for row in logistics:
        identifier = row.get("logistic_id")
        is_enabled = row.get("enabled")
        if (
            type(identifier) is not int
            or identifier <= 0
            or type(is_enabled) is not bool
            or identifier in seen_logistics
        ):
            raise ShopeeOneClickPreDispatchError(
                "regional logistics identity shape is malformed"
            )
        seen_logistics.add(identifier)
        if is_enabled:
            enabled.append(identifier)
    hard["selected_logistics_exact"] = sorted(enabled) == sorted(
        expected_logistics
    )

    image = item.get("image")
    image_urls = (
        image.get("image_url_list")
        if isinstance(image, Mapping)
        else None
    )
    approved_images = approved.get("ordered_images")
    if not isinstance(approved_images, list) or not approved_images:
        raise ShopeeOneClickPreDispatchError(
            "approved image count is invalid"
        )
    hard["image_count_primary_exact"] = bool(
        _exact_string_list(image_urls)
        and len(image_urls) == len(approved_images)
        and image_urls[0]
    )
    hard_exact = all(hard.values())
    source_title = listing_copy.get("title")
    source_description = listing_copy.get("description")
    regional_title = item.get("item_name")
    regional_description = item.get("description")
    copy_observation = evaluate_shopee_regional_copy_observation(
        source_title=source_title,
        source_description=source_description,
        source_global_master_digest=listing_copy.get(
            "approved_master_digest"
        ),
        regional_title=regional_title,
        regional_description=regional_description,
        site=command["region"],
    )
    image_observation = evaluate_shopee_regional_image_observation(
        approved_count=len(approved_images),
        regional_image_urls=image_urls,
        global_linkage_verified=hard["global_linkage_exact"],
    )
    outcome = shopee_regional_observation_outcome(
        listing_hard_exact=hard_exact,
        copy_observation=copy_observation,
        image_observation=image_observation,
    )
    return {
        "verified": outcome.get("outcome") == "SUCCEEDED",
        "manual_review_required": (
            outcome.get("manual_review_required") is True
        ),
        "derived_translation_status": outcome.get(
            "derived_translation_status"
        ),
        "derived_image_status": outcome.get("derived_image_status"),
        "semantic_equivalence": "unverified",
        "profit_status": "unverified",
        "matched_rule_ids": list(outcome.get("matched_rule_ids") or ()),
        "observation_evidence_digest": outcome.get("evidence_digest"),
        "hard_check_digest": _digest(hard),
        "enabled_logistics_count": len(enabled),
        "image_count": len(image_urls) if isinstance(image_urls, list) else 0,
    }


def _current_credentials(region: str) -> ShopeeCredentials:
    from domains.channel_operations.target_scoped_retry_adapters import (
        _prepared_shopee_credentials,
    )
    from modules.shopee.auth import load_tokens

    try:
        shop_id, shop_token = _prepared_shopee_credentials(region)
        store = load_tokens()
        shop = (store.get("shops") or {}).get(str(shop_id)) or {}
        merchant_id = shop.get("merchant_id")
        merchant = (
            (store.get("merchants") or {}).get(str(merchant_id)) or {}
        )
        merchant_token = merchant.get("access_token")
        if (
            type(shop_id) is not int
            or shop_id <= 0
            or type(shop_token) is not str
            or not shop_token
            or type(merchant_id) is not int
            or merchant_id <= 0
            or type(merchant_token) is not str
            or not merchant_token
            or type(shop.get("expire_at")) is not int
            or shop["expire_at"] < int(time.time()) + 120
            or type(merchant.get("expire_at")) is not int
            or merchant["expire_at"] < int(time.time()) + 120
        ):
            raise ValueError("credential identity or expiry is invalid")
    except Exception as error:
        raise ShopeeOneClickPreDispatchError(
            "prepared Shopee no-refresh credentials are unavailable"
        ) from error
    return ShopeeCredentials(
        region=region,
        shop_id=shop_id,
        shop_token=shop_token,
        merchant_id=merchant_id,
        merchant_token=merchant_token,
    )


def _scan_global_model_candidates(
    transport: ShopeePrepareTransport,
    *,
    model_sku: str,
    page_size: int = 50,
    max_pages: int = 100,
) -> list[str]:
    """Scan the official Global Product list with its opaque cursor contract.

    Shopee's current v2 endpoint does not accept ``item_status`` and does not
    use an integer page offset.  The first request contains only
    ``page_size``; every later request echoes the opaque string returned in
    ``response.offset`` by the preceding page.
    """
    matches: list[str] = []
    single_sku_matches: list[str] = []
    item_ids: set[str] = set()
    cursor: str | None = None
    seen_cursors: set[str] = set()
    observed_count = 0
    expected_total: int | None = None
    for _ in range(max_pages):
        params: dict[str, object] = {"page_size": page_size}
        if cursor is not None:
            params["offset"] = cursor
        raw = transport.merchant_get(GLOBAL_LIST_PATH, params)
        if not isinstance(raw, Mapping) or raw.get("error"):
            raise ShopeeOneClickPreDispatchError(
                "official global item list failed"
            )
        data = raw.get("response")
        if not isinstance(data, Mapping):
            raise ShopeeOneClickPreDispatchError(
                "official global item response is malformed"
            )
        total = data.get("total_count")
        has_next = data.get("has_next_page")
        rows = data.get("global_item_list")
        if (
            rows is None
            and "global_item_list" not in data
            and type(total) is int
            and total == 0
            and has_next is False
        ):
            rows = []
        terminal_cursor = data.get("offset")
        if (
            type(total) is not int
            or total < 0
            or type(has_next) is not bool
            or not isinstance(rows, list)
            or any(not isinstance(row, Mapping) for row in rows)
            or (
                has_next is False
                and terminal_cursor is not None
                and type(terminal_cursor) is not str
            )
        ):
            raise ShopeeOneClickPreDispatchError(
                "official global item list is malformed"
            )
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise ShopeeOneClickPreDispatchError(
                "official global item total changed during pagination"
            )
        ids = [
            _positive_identity(
                row.get("global_item_id"), "global item identity"
            )
            for row in rows
        ]
        if len(ids) != len(set(ids)) or item_ids.intersection(ids):
            raise ShopeeOneClickPreDispatchError(
                "official global item identity is duplicated"
            )
        item_ids.update(ids)
        observed_count += len(ids)
        with ThreadPoolExecutor(
            max_workers=min(GLOBAL_SCAN_MAX_WORKERS, max(1, len(ids)))
        ) as executor:
            futures = [
                executor.submit(
                    _global_sku_match_kind,
                    transport,
                    global_item_id=global_id,
                    model_sku=model_sku,
                )
                for global_id in ids
            ]
            match_kinds = [future.result() for future in futures]
        for global_id, match_kind in zip(ids, match_kinds):
            if match_kind == "MODEL":
                matches.append(global_id)
            elif match_kind == "SINGLE":
                single_sku_matches.append(global_id)
        if has_next is False:
            if observed_count != total:
                raise ShopeeOneClickPreDispatchError(
                    "official global item pagination is incomplete"
                )
            break
        next_cursor = data.get("offset")
        if type(next_cursor) is not str or not next_cursor:
            raise ShopeeOneClickPreDispatchError(
                "official global item pagination cursor is invalid"
            )
        if next_cursor == cursor or next_cursor in seen_cursors:
            raise ShopeeOneClickPreDispatchError(
                "global item pagination cursor looped"
            )
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    else:
        raise ShopeeOneClickPreDispatchError(
            "official global item pagination exceeded its bound"
        )
    if len(matches) != len(set(matches)):
        raise ShopeeOneClickPreDispatchError(
            "official global model identity repeated across pages"
        )
    if single_sku_matches:
        combined = [*matches, *single_sku_matches]
        if len(combined) != 1:
            return combined
        raise ShopeeOneClickPrepareBlocked(
            "shopee_existing_global_model_initialization_required",
            (
                "an official single-SKU global item already owns the "
                "approved SKU but has no model identity for regional publish"
            ),
            category="CAPABILITY",
        )
    return matches


def _global_sku_match_kind(
    transport: ShopeePrepareTransport,
    *,
    global_item_id: str,
    model_sku: str,
) -> str | None:
    """Return the exact official SKU identity kind for one global item."""

    raw_models = transport.merchant_get(
        GLOBAL_MODEL_PATH,
        {"global_item_id": int(global_item_id)},
    )
    model_response = (
        raw_models.get("response")
        if isinstance(raw_models, Mapping) and not raw_models.get("error")
        else None
    )
    if (
        isinstance(model_response, Mapping)
        and "global_model" not in model_response
    ):
        tier = model_response.get("tier_variation")
        standard_tier = model_response.get(
            "standardise_tier_variation"
        )
        if (
            not isinstance(tier, list)
            or not isinstance(standard_tier, list)
            or any(not isinstance(row, Mapping) for row in tier)
            or any(
                not isinstance(row, Mapping)
                for row in standard_tier
            )
        ):
            raise ShopeeOneClickPreDispatchError(
                "official global model list is malformed"
            )
        return (
            "SINGLE"
            if _single_sku_global_item(
                transport, global_item_id=global_item_id
            )
            == model_sku
            else None
        )
    models = _global_models(
        transport, global_item_id, raw=raw_models
    )
    exact_models = [
        row for row in models if row.get("global_model_sku") == model_sku
    ]
    if len(exact_models) > 1:
        raise ShopeeOneClickPreDispatchError(
            "official global model SKU is ambiguous"
        )
    return "MODEL" if exact_models else None


def _observe_existing_global_candidate_availability(
    transport: ShopeePrepareTransport,
    *,
    global_item_id: str,
    seed: Mapping[str, object],
    expected_model_skus: tuple[str, ...],
) -> dict[str, object]:
    """Read and validate the complete existing-global candidate shape.

    This function deliberately returns only normalized current-fact bindings
    and digests.  Item-level seller stock/location is delegated to the shared
    official-existing contract.  The official item still cannot manufacture
    the server-owned approval lineage required for category path/tree,
    attributes, brand, condition/preorder, or variation/image choices.
    """

    from shared_platform.target_scoped_release_contracts import (
        approved_shopee_copy_digest,
    )
    from shared_platform.shopee_global_plan import (
        ShopeeGlobalPlanContractError,
        build_shopee_official_existing_global_seller_stock,
    )

    raw = transport.merchant_get(
        GLOBAL_ITEM_PATH, {"global_item_id_list": global_item_id}
    )
    if not isinstance(raw, Mapping) or raw.get("error"):
        raise ShopeeOneClickPreDispatchError(
            "official global item GET failed"
        )
    response = raw.get("response")
    rows = (
        response.get("global_item_list")
        if isinstance(response, Mapping)
        else None
    )
    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], Mapping)
        or str(rows[0].get("global_item_id") or "") != global_item_id
    ):
        raise ShopeeOneClickPreDispatchError(
            "official global item identity is not unique"
        )
    item = rows[0]
    title = item.get("global_item_name")
    description = item.get("description")
    if (
        type(title) is not str
        or not title.strip()
        or type(description) is not str
        or not description
        or approved_shopee_copy_digest(title, description)
        != seed.get("approved_copy_digest")
    ):
        raise ShopeeOneClickPreDispatchError(
            "official global copy is invalid or drifted"
        )

    image = item.get("image")
    urls = image.get("image_url_list") if isinstance(image, Mapping) else None
    ids = image.get("image_id_list") if isinstance(image, Mapping) else None
    selected = seed.get("selected_image_positions")
    approved_images = seed.get("ordered_approved_images")
    if (
        not _exact_string_list(urls)
        or not _exact_string_list(ids)
        or len(urls) != len(ids)
        or not isinstance(selected, list)
        or not selected
        or any(type(value) is not int or value <= 0 for value in selected)
        or len(selected) != len(set(selected))
        or not isinstance(approved_images, list)
        or any(not isinstance(row, Mapping) for row in approved_images)
        or any(value > len(approved_images) for value in selected)
        or len(ids) != len(selected)
    ):
        raise ShopeeOneClickPreDispatchError(
            "official global image selection shape is invalid"
        )

    category_id = item.get("category_id")
    attributes = item.get("attribute_list")
    brand = item.get("brand")
    stock = item.get("seller_stock")
    condition = item.get("condition")
    preorder = item.get("pre_order")
    variations = item.get("tier_variation")
    if (
        type(category_id) is not int
        or category_id <= 0
        or not isinstance(attributes, list)
        or not attributes
        or any(not isinstance(row, Mapping) for row in attributes)
        or "brand" not in item
        or not (
            brand is None
            or (
                isinstance(brand, Mapping)
                and type(brand.get("brand_id")) is int
                and brand["brand_id"] >= 0
                and type(brand.get("original_brand_name")) is str
                and bool(brand["original_brand_name"].strip())
            )
        )
        or type(condition) is not str
        or not condition.strip()
        or not isinstance(preorder, Mapping)
        or type(preorder.get("is_pre_order")) is not bool
        or type(preorder.get("days_to_ship")) is not int
        or preorder["days_to_ship"] < 0
        or not isinstance(variations, list)
        or not variations
        or any(not isinstance(row, Mapping) for row in variations)
    ):
        raise ShopeeOneClickPreDispatchError(
            "official existing-global candidate shape is incomplete"
        )

    models = _global_models(transport, global_item_id)
    model_skus = [
        row.get("global_model_sku")
        for row in models
        if isinstance(row, Mapping)
    ]
    if (
        len(model_skus) != len(models)
        or sorted(model_skus) != sorted(expected_model_skus)
    ):
        raise ShopeeOneClickPreDispatchError(
            "official global model contract drifted"
        )
    projected_models = [
        {
            "global_model_id": int(
                _positive_identity(
                    row.get("global_model_id"),
                    "global model identity",
                )
            ),
            "global_model_sku": _nonempty(
                row.get("global_model_sku"), "global model SKU"
            ),
            "tier_index": list(row.get("tier_index") or ()),
        }
        for row in models
    ]
    identity_evidence_digest = _digest(
        {
            "schema_version": (
                "shopee-official-existing-global-identity/v1"
            ),
            "global_item_id": global_item_id,
            "model_skus": sorted(model_skus),
        }
    )
    observation_evidence_digest = _digest(
        {
            "schema_version": (
                "shopee-official-existing-global-observation/v1"
            ),
            "global_item_identity_digest": _text_digest(global_item_id),
            "existing_global_identity_evidence_digest": (
                identity_evidence_digest
            ),
            "copy_digest": seed["approved_copy_digest"],
            "official_image_id_snapshot_digest": _digest(
                {"ordered_image_ids": ids}
            ),
            "selected_image_count": len(ids),
            "category_id": category_id,
            "attribute_shape_digest": _digest(attributes),
            "brand_shape_digest": _digest(brand),
            "condition": condition,
            "pre_order": preorder,
            "tier_variation_shape_digest": _digest(variations),
            "model_shape_digest": _digest(projected_models),
        }
    )
    try:
        stock_location = (
            build_shopee_official_existing_global_seller_stock(
                observation_evidence_digest=observation_evidence_digest,
                existing_global_item_id=int(global_item_id),
                existing_global_identity_evidence_digest=(
                    identity_evidence_digest
                ),
                seller_stock_rows=stock,
            )
        )
    except ShopeeGlobalPlanContractError as error:
        raise ShopeeOneClickPreDispatchError(
            "official existing-global seller stock shape is invalid"
        ) from error
    return {
        "existing_global_item": {
            "global_item_id": int(global_item_id),
            "global_item_name": title,
            "description": description,
            "image": {
                "image_url_list": list(urls),
                "image_id_list": list(ids),
            },
            "category_id": category_id,
            "attribute_list": [dict(row) for row in attributes],
            "brand": dict(brand) if isinstance(brand, Mapping) else None,
            "seller_stock": [dict(row) for row in stock],
            "condition": condition,
            "pre_order": dict(preorder),
            "tier_variation": [dict(row) for row in variations],
        },
        "existing_global_models": projected_models,
        "global_item_identity_digest": _text_digest(global_item_id),
        "existing_global_identity_evidence_digest": (
            identity_evidence_digest
        ),
        "observation_evidence_digest": observation_evidence_digest,
        "copy_digest": seed["approved_copy_digest"],
        "official_image_id_snapshot_digest": _digest(
            {"ordered_image_ids": ids}
        ),
        "selected_image_count": len(ids),
        "category_id_digest": _digest({"category_id": category_id}),
        "attribute_shape_digest": _digest(attributes),
        "brand_shape_digest": _digest(brand),
        "seller_stock": dict(stock_location["seller_stock"]),
        "location": dict(stock_location["location"]),
        "condition_preorder_digest": _digest(
            {"condition": condition, "pre_order": preorder}
        ),
        "variation_model_shape_digest": _digest(
            {
                "tier_variation": variations,
                "models": projected_models,
            }
        ),
    }


def _approved_plan_models(
    plan: Mapping[str, object],
) -> object:
    snapshot = plan.get("current_snapshot")
    if isinstance(snapshot, Mapping):
        return snapshot.get("global_model")
    return plan.get("global_model")


def _read_global_master(
    transport: ShopeePrepareTransport,
    *,
    global_item_id: str,
    approved: Mapping[str, object],
) -> dict[str, object]:
    from shared_platform.target_scoped_release_contracts import (
        approved_shopee_copy_digest,
        evaluate_shopee_global_image_observation,
        shopee_global_image_observation_outcome,
    )

    raw = transport.merchant_get(
        GLOBAL_ITEM_PATH, {"global_item_id_list": global_item_id}
    )
    if not isinstance(raw, Mapping) or raw.get("error"):
        raise ShopeeOneClickPreDispatchError(
            "official global item GET failed"
        )
    data = raw.get("response")
    rows = (
        data.get("global_item_list") if isinstance(data, Mapping) else None
    )
    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], Mapping)
        or str(rows[0].get("global_item_id") or "") != global_item_id
    ):
        raise ShopeeOneClickPreDispatchError(
            "official global item identity is not unique"
        )
    item = rows[0]
    title = item.get("global_item_name")
    description = item.get("description")
    if type(title) is not str or not title.strip() or type(
        description
    ) is not str or not description:
        raise ShopeeOneClickPreDispatchError(
            "official global copy shape is invalid"
        )
    approved_copy = _mapping(approved.get("listing_copy"), "listing copy")
    copy_digest = approved_shopee_copy_digest(title, description)
    if copy_digest != approved_copy.get("approved_copy_digest"):
        raise ShopeeOneClickPrepareBlocked(
            "shopee_global_copy_drift",
            "official global copy does not match approved English master",
        )
    image = item.get("image")
    urls = image.get("image_url_list") if isinstance(image, Mapping) else None
    ids = image.get("image_id_list") if isinstance(image, Mapping) else None
    approved_images = approved.get("ordered_images")
    if (
        not _exact_string_list(urls)
        or not _exact_string_list(ids)
        or len(urls) != len(ids)
        or not isinstance(approved_images, list)
        or len(ids) != len(approved_images)
    ):
        raise ShopeeOneClickPreDispatchError(
            "official global image identity shape is invalid"
        )
    image_observation = evaluate_shopee_global_image_observation(
        approved_count=len(approved_images),
        official_image_urls=urls,
        official_image_ids=ids,
        prior_mapping_digest=None,
    )
    image_outcome = shopee_global_image_observation_outcome(
        global_hard_facts_exact=True,
        image_observation=image_observation,
    )
    if image_outcome.get("execution_allowed") is not True:
        raise ShopeeOneClickPrepareBlocked(
            "shopee_global_image_observation_unacceptable",
            "official global image observation requires reconciliation",
        )
    models = _global_models(transport, global_item_id)
    model_sku = str(approved["model_sku"])
    matches = [
        row
        for row in models
        if str(row.get("global_model_sku") or "") == model_sku
    ]
    if len(matches) != 1:
        raise ShopeeOneClickPreDispatchError(
            "official global model SKU is not unique"
        )
    model_id = str(matches[0].get("global_model_id") or "")
    tier = matches[0].get("tier_index")
    if (
        not model_id.isdigit()
        or not isinstance(tier, list)
        or not tier
        or any(type(value) is not int or value < 0 for value in tier)
    ):
        raise ShopeeOneClickPreDispatchError(
            "official global model identity is invalid"
        )
    snapshot = _digest({"ordered_image_ids": ids})
    return {
        "global_model_id": model_id,
        "tier_index": list(tier),
        "copy_digest": copy_digest,
        "image_snapshot_digest": snapshot,
        "image_count": len(ids),
        "image_observation": image_observation,
        "image_outcome": image_outcome,
        "summary": {
            "global_item_identity_digest": _text_digest(global_item_id),
            "global_model_identity_digest": _digest(
                {"model_id": model_id, "tier": tier, "sku": model_sku}
            ),
            "copy_digest": copy_digest,
            "image_snapshot_digest": snapshot,
            "image_count": len(ids),
            "global_image_status": image_outcome["global_image_status"],
            "global_image_verification_scope": image_outcome[
                "global_image_verification_scope"
            ],
            "global_image_observation_digest": image_observation[
                "evidence_digest"
            ],
            "global_image_outcome_digest": image_outcome[
                "evidence_digest"
            ],
        },
    }


def _single_sku_global_item(
    transport: ShopeePrepareTransport, *, global_item_id: str
) -> str:
    raw = transport.merchant_get(
        GLOBAL_ITEM_PATH, {"global_item_id_list": global_item_id}
    )
    if not isinstance(raw, Mapping) or raw.get("error"):
        raise ShopeeOneClickPreDispatchError(
            "official single-SKU global item GET failed"
        )
    response = raw.get("response")
    rows = (
        response.get("global_item_list")
        if isinstance(response, Mapping)
        else None
    )
    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], Mapping)
        or _positive_identity(
            rows[0].get("global_item_id"), "global item identity"
        )
        != global_item_id
        or rows[0].get("has_model") is not False
        or type(rows[0].get("global_item_status")) is not str
        or not rows[0]["global_item_status"].strip()
        or type(rows[0].get("global_item_sku")) is not str
        or not rows[0]["global_item_sku"].strip()
    ):
        raise ShopeeOneClickPreDispatchError(
            "official single-SKU global item identity is malformed"
        )
    return rows[0]["global_item_sku"]


def _global_models(
    transport: ShopeePrepareTransport,
    global_item_id: str,
    *,
    raw: object = None,
) -> list[Mapping[str, object]]:
    if raw is None:
        raw = transport.merchant_get(
            GLOBAL_MODEL_PATH, {"global_item_id": int(global_item_id)}
        )
    if not isinstance(raw, Mapping) or raw.get("error"):
        raise ShopeeOneClickPreDispatchError(
            "official global model GET failed"
        )
    data = raw.get("response")
    rows = data.get("global_model") if isinstance(data, Mapping) else None
    if not isinstance(rows, list) or any(
        not isinstance(row, Mapping) for row in rows
    ):
        raise ShopeeOneClickPreDispatchError(
            "official global model list is malformed"
        )
    seen: set[str] = set()
    seen_skus: set[str] = set()
    for row in rows:
        model_id = _positive_identity(
            row.get("global_model_id"), "global model identity"
        )
        sku = row.get("global_model_sku")
        tier = row.get("tier_index")
        if (
            type(sku) is not str
            or not sku.strip()
            or model_id in seen
            or sku in seen_skus
            or not isinstance(tier, list)
            or not tier
            or any(type(value) is not int or value < 0 for value in tier)
        ):
            raise ShopeeOneClickPreDispatchError(
                "official global model identity is malformed"
            )
        seen.add(model_id)
        seen_skus.add(sku)
    return list(rows)


def _scan_regional_sku(
    transport: ShopeePrepareTransport,
    *,
    seller_sku: str,
    max_pages: int = 100,
) -> dict[str, object]:
    matches: list[str] = []
    evidence: list[object] = []
    all_item_ids: set[str] = set()
    for status in ("NORMAL", "UNLIST", "BANNED"):
        offset = 0
        seen: set[int] = set()
        observed_count = 0
        for _ in range(max_pages):
            if offset in seen:
                raise ShopeeOneClickPreDispatchError(
                    "regional item pagination cursor looped"
                )
            seen.add(offset)
            raw = transport.shop_get(
                "/api/v2/product/get_item_list",
                {
                    "offset": offset,
                    "page_size": 100,
                    "item_status": status,
                },
            )
            if not isinstance(raw, Mapping) or raw.get("error"):
                raise ShopeeOneClickPreDispatchError(
                    "regional item list failed"
                )
            data = raw.get("response")
            if not isinstance(data, Mapping):
                raise ShopeeOneClickPreDispatchError(
                    "regional item response is malformed"
                )
            total = data.get("total_count")
            has_next = data.get("has_next_page")
            rows = data.get("item")
            if (
                rows is None
                and "item" not in data
                and type(total) is int
                and total == 0
                and has_next is False
            ):
                rows = []
            if (
                type(total) is not int
                or total < 0
                or type(has_next) is not bool
                or not isinstance(rows, list)
                or any(not isinstance(row, Mapping) for row in rows)
            ):
                raise ShopeeOneClickPreDispatchError(
                    "regional item list is malformed"
                )
            ids = [
                _positive_identity(row.get("item_id"), "regional item identity")
                for row in rows
            ]
            if (
                len(ids) != len(set(ids))
                or all_item_ids.intersection(ids)
            ):
                raise ShopeeOneClickPreDispatchError(
                    "regional item identities are duplicated"
                )
            all_item_ids.update(ids)
            observed_count += len(ids)
            for start in range(0, len(ids), 50):
                batch = ids[start : start + 50]
                base = transport.shop_get(
                    "/api/v2/product/get_item_base_info",
                    {"item_id_list": ",".join(batch)},
                )
                base_data = (
                    base.get("response")
                    if isinstance(base, Mapping) and not base.get("error")
                    else None
                )
                base_rows = (
                    base_data.get("item_list")
                    if isinstance(base_data, Mapping)
                    else None
                )
                if not isinstance(base_rows, list) or any(
                    not isinstance(row, Mapping) for row in base_rows
                ):
                    raise ShopeeOneClickPreDispatchError(
                        "regional base-info batch is malformed"
                    )
                returned = [
                    _positive_identity(
                        row.get("item_id"), "regional base item identity"
                    )
                    for row in base_rows
                ]
                if len(returned) != len(set(returned)) or set(returned) != set(
                    batch
                ):
                    raise ShopeeOneClickPreDispatchError(
                        "regional base-info batch is incomplete"
                    )
                for row in base_rows:
                    item_id = _positive_identity(
                        row.get("item_id"), "regional base item identity"
                    )
                    item_sku = row.get("item_sku")
                    has_model = row.get("has_model")
                    if (
                        type(item_sku) is not str
                        or type(has_model) is not bool
                    ):
                        raise ShopeeOneClickPreDispatchError(
                            "regional base-info identity shape is malformed"
                        )
                    matched = item_sku == seller_sku
                    if has_model:
                        model = transport.shop_get(
                            "/api/v2/product/get_model_list",
                            {"item_id": int(item_id)},
                        )
                        model_data = (
                            model.get("response")
                            if isinstance(model, Mapping)
                            and not model.get("error")
                            else None
                        )
                        model_rows = (
                            model_data.get("model")
                            if isinstance(model_data, Mapping)
                            else None
                        )
                        if not isinstance(model_rows, list) or any(
                            not isinstance(value, Mapping)
                            for value in model_rows
                        ):
                            raise ShopeeOneClickPreDispatchError(
                                "regional model list is malformed"
                            )
                        model_ids: set[str] = set()
                        model_skus: set[str] = set()
                        for value in model_rows:
                            model_id = _positive_identity(
                                value.get("model_id"),
                                "regional model identity",
                            )
                            model_sku = value.get("model_sku")
                            if type(model_sku) is not str or not model_sku:
                                raise ShopeeOneClickPreDispatchError(
                                    "regional model SKU shape is malformed"
                                )
                            if (
                                model_id in model_ids
                                or model_sku in model_skus
                            ):
                                raise ShopeeOneClickPreDispatchError(
                                    "regional model identity is duplicated"
                                )
                            model_ids.add(model_id)
                            model_skus.add(model_sku)
                            if model_sku == seller_sku:
                                matched = True
                    if matched:
                        matches.append(item_id)
            evidence.append((status, offset, ids))
            if has_next is False:
                if observed_count != total:
                    raise ShopeeOneClickPreDispatchError(
                        "regional item pagination is incomplete"
                    )
                break
            next_offset = data.get("next_offset")
            if type(next_offset) is not int or next_offset <= offset:
                raise ShopeeOneClickPreDispatchError(
                    "regional item pagination is non-terminating"
                )
            offset = next_offset
        else:
            raise ShopeeOneClickPreDispatchError(
                "regional item pagination exceeded its bound"
            )
    if len(matches) != len(set(matches)):
        raise ShopeeOneClickPreDispatchError(
            "regional exact SKU identity is ambiguous"
        )
    return {"matches": matches, "scan_digest": _digest(evidence)}


def _compatible_logistics(
    transport: ShopeePrepareTransport,
    *,
    approved: Mapping[str, object],
    region: str,
) -> list[int]:
    raw = transport.shop_get("/api/v2/logistics/get_channel_list", None)
    data = (
        raw.get("response")
        if isinstance(raw, Mapping) and not raw.get("error")
        else None
    )
    rows = (
        data.get("logistics_channel_list")
        if isinstance(data, Mapping)
        else None
    )
    if not isinstance(rows, list) or any(
        not isinstance(row, Mapping) for row in rows
    ):
        raise ShopeeOneClickPreDispatchError(
            "official logistics list is malformed"
        )
    parcel = _mapping(approved.get("parcel"), "approved parcel")
    weight = float(str(parcel["weight_kg"]))
    dimensions = tuple(float(str(value)) for value in parcel["package_cm"])
    from modules.shopee.publish import _channel_supports_parcel

    enabled: list[int] = []
    for row in rows:
        identifier = row.get("logistics_channel_id", row.get("logistic_id"))
        if (
            type(row.get("enabled")) is not bool
            or type(identifier) is not int
            or identifier <= 0
        ):
            raise ShopeeOneClickPreDispatchError(
                "official logistics identity is malformed"
            )
        if not row["enabled"]:
            continue
        if region == "VN" and identifier == 50052:
            continue
        if _channel_supports_parcel(
            row,
            region=region,
            weight_kg=weight,
            dimensions_cm=dimensions,
        ):
            enabled.append(identifier)
    if len(enabled) != len(set(enabled)):
        raise ShopeeOneClickPreDispatchError(
            "official logistics identities are duplicated"
        )
    return sorted(enabled)


def _regional_body(
    approved: Mapping[str, object],
    *,
    region: str,
    shop_id: int,
    global_item_id: str,
    tier_index: list[int],
    selected_logistics: list[int],
) -> dict[str, object]:
    pricing = _mapping(approved.get("target_pricing"), "target pricing")
    price = str(pricing["local_original_price"])
    return {
        "global_item_id": int(global_item_id),
        "shop_id": shop_id,
        "shop_region": region,
        "item": {
            "item_status": "NORMAL",
            "original_price": price,
            "logistic": [
                {"logistic_id": value, "enabled": True}
                for value in selected_logistics
            ],
            "model": [
                {"tier_index": list(tier_index), "original_price": price}
            ],
        },
    }


def _regional_body_models(
    approved: Mapping[str, object],
    *,
    region: str,
    shop_id: int,
    global_item_id: str,
    models: list[Mapping[str, object]],
    selected_logistics: list[int],
) -> dict[str, object]:
    pricing = _mapping(approved.get("target_pricing"), "target pricing")
    price = str(
        _finite_positive_decimal(
            pricing.get("local_original_price"),
            "target original price",
        )
    )
    normalized_models: list[dict[str, object]] = []
    seen: set[tuple[int, ...]] = set()
    for row in models:
        if not isinstance(row, Mapping):
            raise ShopeeOneClickPreDispatchError(
                "approved model contract is invalid"
            )
        tier = row.get("tier_index")
        if (
            not isinstance(tier, list)
            or not tier
            or any(type(value) is not int or value < 0 for value in tier)
            or tuple(tier) in seen
        ):
            raise ShopeeOneClickPreDispatchError(
                "approved model tier identity is invalid"
            )
        seen.add(tuple(tier))
        normalized_models.append(
            {"tier_index": list(tier), "original_price": price}
        )
    return {
        "global_item_id": int(global_item_id),
        "shop_id": shop_id,
        "shop_region": region,
        "item": {
            "item_status": "NORMAL",
            "original_price": price,
            "logistic": [
                {"logistic_id": value, "enabled": True}
                for value in selected_logistics
            ],
            "model": normalized_models,
        },
    }


def _canonical_global_create_body(
    plan: Mapping[str, object],
) -> dict[str, object]:
    copy = _mapping(plan.get("copy"), "approved copy")
    parcel = _mapping(plan.get("parcel"), "approved parcel")
    package = _mapping(parcel.get("package_cm"), "approved package")
    pricing = _mapping(plan.get("pricing"), "approved pricing")
    category = _mapping(plan.get("category"), "approved category")
    brand = _mapping(plan.get("brand"), "approved brand")
    seller_stock = _mapping(
        plan.get("seller_stock"), "approved seller stock"
    )
    location = _mapping(plan.get("location"), "approved location")
    preorder = _mapping(plan.get("preorder"), "approved preorder")
    attributes = plan.get("attribute_list")
    if (
        category.get("path_complete") is not True
        or plan.get("attributes_complete") is not True
        or plan.get("variations_complete") is not True
        or not isinstance(attributes, list)
        or not attributes
        or any(not isinstance(row, Mapping) for row in attributes)
        or type(preorder.get("is_pre_order")) is not bool
        or type(preorder.get("days_to_ship")) is not int
        or preorder["days_to_ship"] < 0
    ):
        raise ShopeeOneClickPreDispatchError(
            "approved global create contract is incomplete"
        )
    return {
        "category_id": _positive_int(
            category.get("category_id"), "approved category"
        ),
        "global_item_name": _nonempty(
            copy.get("title"), "approved global title"
        ),
        "description": _nonempty_exact(
            copy.get("description"), "approved global description"
        ),
        "original_price": float(
            _finite_positive_decimal(
                pricing.get("global_original_price"),
                "approved CNY original price",
            )
        ),
        "weight": float(
            _finite_positive_decimal(
                parcel.get("weight_kg"), "approved weight"
            )
        ),
        "dimension": {
            "package_length": float(
                _finite_positive_decimal(
                    package.get("length"), "approved package length"
                )
            ),
            "package_width": float(
                _finite_positive_decimal(
                    package.get("width"), "approved package width"
                )
            ),
            "package_height": float(
                _finite_positive_decimal(
                    package.get("height"), "approved package height"
                )
            ),
        },
        "image": {"image_id_list": []},
        "attribute_list": [dict(row) for row in attributes],
        "brand": {
            "brand_id": brand.get("brand_id"),
            "original_brand_name": brand.get("original_brand_name"),
        },
        "condition": _nonempty(
            plan.get("condition"), "approved condition"
        ),
        "seller_stock": [
            {
                "location_id": _nonempty(
                    location.get("location_id"),
                    "approved stock location",
                ),
                "stock": _positive_int(
                    seller_stock.get("quantity"),
                    "approved Shopee stock",
                ),
            }
        ],
        "pre_order": {
            "is_pre_order": preorder["is_pre_order"],
            "days_to_ship": preorder["days_to_ship"],
        },
    }


def _canonical_global_model_body(
    plan: Mapping[str, object],
) -> dict[str, object]:
    tiers = plan.get("tier_variation")
    models = plan.get("global_model")
    seller_stock = _mapping(
        plan.get("seller_stock"), "approved seller stock"
    )
    location = _mapping(plan.get("location"), "approved location")
    if (
        not isinstance(tiers, list)
        or not isinstance(models, list)
        or not models
        or any(not isinstance(row, Mapping) for row in (*tiers, *models))
    ):
        raise ShopeeOneClickPreDispatchError(
            "approved global model contract is incomplete"
        )
    normalized: list[dict[str, object]] = []
    seen_skus: set[str] = set()
    seen_tiers: set[tuple[int, ...]] = set()
    for row in models:
        sku = _nonempty(
            row.get("global_model_sku"), "approved model SKU"
        )
        tier = row.get("tier_index")
        if (
            sku in seen_skus
            or not isinstance(tier, list)
            or not tier
            or any(type(value) is not int or value < 0 for value in tier)
            or tuple(tier) in seen_tiers
        ):
            raise ShopeeOneClickPreDispatchError(
                "approved global model identity is invalid"
            )
        seen_skus.add(sku)
        seen_tiers.add(tuple(tier))
        normalized.append(
            {
                "tier_index": list(tier),
                "global_model_sku": sku,
                "original_price": float(
                    _finite_positive_decimal(
                        row.get("original_price_cny"),
                        "approved model original price",
                    )
                ),
                "seller_stock": [
                    {
                        "location_id": _nonempty(
                            location.get("location_id"),
                            "approved stock location",
                        ),
                        "stock": _positive_int(
                            row.get(
                                "seller_stock_quantity",
                                seller_stock.get("quantity"),
                            ),
                            "approved model stock",
                        ),
                    }
                ],
            }
        )
    return {
        "tier_variation": [dict(row) for row in tiers],
        "global_model": normalized,
    }


def _global_create_body(
    approved: Mapping[str, object],
    *,
    create_facts: Mapping[str, object],
    image_ids: list[str],
) -> dict[str, object]:
    listing = _mapping(approved.get("listing_copy"), "listing copy")
    parcel = _mapping(approved.get("parcel"), "approved parcel")
    brand = _mapping(create_facts.get("brand"), "approved brand")
    stock = _mapping(create_facts.get("seller_stock"), "approved stock")
    pre_order = _mapping(
        create_facts.get("pre_order"), "approved preorder"
    )
    dimensions = parcel.get("package_cm")
    attributes = create_facts.get("attribute_list")
    if (
        not isinstance(dimensions, list)
        or len(dimensions) != 3
        or not isinstance(attributes, list)
        or not attributes
        or any(not isinstance(row, Mapping) for row in attributes)
    ):
        raise ShopeeOneClickPreDispatchError(
            "approved global create facts are invalid"
        )
    return {
        "category_id": _positive_int(
            create_facts.get("category_id"), "approved category"
        ),
        "global_item_name": _nonempty(
            listing.get("title"), "approved global title"
        ),
        "description": _nonempty_exact(
            listing.get("description"), "approved global description"
        ),
        "original_price": float(
            _finite_positive_decimal(
                create_facts.get("original_price_cny"),
                "approved CNY original price",
            )
        ),
        "weight": float(
            _finite_positive_decimal(
                parcel.get("weight_kg"), "approved weight"
            )
        ),
        "dimension": {
            "package_length": float(
                _finite_positive_decimal(
                    dimensions[0], "approved package length"
                )
            ),
            "package_width": float(
                _finite_positive_decimal(
                    dimensions[1], "approved package width"
                )
            ),
            "package_height": float(
                _finite_positive_decimal(
                    dimensions[2], "approved package height"
                )
            ),
        },
        "image": {"image_id_list": list(image_ids)},
        "attribute_list": [dict(row) for row in attributes],
        "brand": dict(brand),
        "condition": _nonempty(
            create_facts.get("condition"), "approved condition"
        ),
        "seller_stock": [
            {
                "location_id": _nonempty(
                    stock.get("location_id"),
                    "approved stock location",
                ),
                "stock": _positive_int(
                    stock.get("stock"), "approved Shopee stock"
                ),
            }
        ],
        "pre_order": {
            "days_to_ship": _positive_int(
                pre_order.get("days_to_ship"),
                "approved days to ship",
            )
        },
    }


def _global_model_body(
    approved: Mapping[str, object],
    *,
    create_facts: Mapping[str, object],
    global_item_id: str | None,
) -> dict[str, object]:
    stock = _mapping(create_facts.get("seller_stock"), "approved stock")
    body: dict[str, object] = {
        "tier_variation": [
            {"name": "Model", "option_list": [{"option": "Default"}]}
        ],
        "global_model": [
            {
                "tier_index": [0],
                "global_model_sku": _nonempty(
                    approved.get("model_sku"), "approved model SKU"
                ),
                "original_price": float(
                    _finite_positive_decimal(
                        create_facts.get("original_price_cny"),
                        "approved CNY original price",
                    )
                ),
                "seller_stock": [
                    {
                        "location_id": _nonempty(
                            stock.get("location_id"),
                            "approved stock location",
                        ),
                        "stock": _positive_int(
                            stock.get("stock"), "approved Shopee stock"
                        ),
                    }
                ],
            }
        ],
    }
    if global_item_id is not None:
        body["global_item_id"] = int(
            _positive_identity(global_item_id, "global item identity")
        )
    return body


def _accepted_regional_identity(response: object) -> str | None:
    if not isinstance(response, Mapping):
        return None
    if response.get("accepted") is True:
        value = response.get("external_id")
        return (
            str(value)
            if type(value) in {str, int} and str(value).isdigit()
            else None
        )
    return None


def _accepted_image_identity(response: object) -> str | None:
    if not isinstance(response, Mapping) or response.get("error"):
        return None
    payload = response.get("response")
    payload = payload if isinstance(payload, Mapping) else response
    info = payload.get("image_info")
    if not isinstance(info, Mapping):
        rows = payload.get("image_info_list")
        if (
            isinstance(rows, list)
            and len(rows) == 1
            and isinstance(rows[0], Mapping)
        ):
            info = rows[0].get("image_info")
    if not isinstance(info, Mapping):
        return None
    value = info.get("image_id")
    return value if type(value) is str and value else None


def _accepted_global_identity(response: object) -> str | None:
    if not isinstance(response, Mapping) or response.get("error"):
        return None
    payload = response.get("response")
    value = (
        payload.get("global_item_id")
        if isinstance(payload, Mapping)
        else None
    )
    try:
        return _positive_identity(value, "created global item identity")
    except ShopeeOneClickPreDispatchError:
        return None


def _accepted_empty_write_response(response: object) -> bool:
    return bool(
        isinstance(response, Mapping)
        and response.get("error") in (None, "", "-")
        and isinstance(response.get("response", {}), Mapping)
    )


def _explicit_write_rejection(response: object) -> bool:
    """Return true only for an unambiguous official no-write rejection."""

    if not isinstance(response, Mapping):
        return False
    return (
        response.get("accepted") is False
        and response.get("write_applied") is False
    )


def _append_write(
    writes: tuple[str, ...], write_class: str
) -> tuple[str, ...]:
    return writes if write_class in writes else (*writes, write_class)


def _image_id_snapshot_digest(image_ids: list[str]) -> str:
    from shared_platform.target_scoped_release_contracts import (
        shopee_global_image_id_mapping_digest,
    )

    return shopee_global_image_id_mapping_digest(image_ids)


def _global_image_outcome(
    command: Mapping[str, object],
    created_evidence: Mapping[str, object] | None,
) -> Mapping[str, object]:
    if created_evidence is not None:
        return _mapping(
            created_evidence.get("image_outcome"),
            "created global image outcome",
        )
    return _mapping(
        command.get("global_image_outcome"), "global image outcome"
    )


def _missing_create_facts(approved: object) -> list[str]:
    if not isinstance(approved, Mapping):
        return ["approved_command"]
    create = approved.get("global_create")
    if not isinstance(create, Mapping):
        return ["global_create"]
    required = {
        "category_id",
        "attribute_list",
        "brand",
        "seller_stock",
        "original_price_cny",
        "condition",
        "pre_order",
    }
    return sorted(required.difference(create))


def _provider_command(request) -> Mapping[str, Any]:
    command = getattr(request, "command", None)
    payload = command.get("payload") if isinstance(command, Mapping) else None
    provider = (
        payload.get("provider_command") if isinstance(payload, Mapping) else None
    )
    if not isinstance(provider, Mapping):
        raise ShopeeOneClickPreDispatchError(
            "stored Shopee command is invalid"
        )
    try:
        restored = json.loads(
            json.dumps(provider, ensure_ascii=False, sort_keys=True)
        )
    except (TypeError, ValueError) as error:
        raise ShopeeOneClickPreDispatchError(
            "stored Shopee command is not JSON-safe"
        ) from error
    if not isinstance(restored, Mapping):
        raise ShopeeOneClickPreDispatchError(
            "stored Shopee command is invalid"
        )
    return restored


def _occurrence_evidence(
    occurrence_id: str,
    *,
    external_id: str | None = None,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "schema_version": "oneclick-channel-write-occurrence/v1",
        "occurrence_id": occurrence_id,
    }
    if extra:
        evidence.update(dict(extra))
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
    evidence: Mapping[str, object] | None = None,
) -> OpenWriteOccurrence:
    try:
        return state.open(
            request,
            occurrence_id=occurrence_id,
            write_class=write_class,
            evidence=_occurrence_evidence(
                occurrence_id,
                external_id=external_id,
                extra=evidence,
            ),
        )
    except WriteOccurrenceRecordingError as error:
        if error.external_write_count == 0:
            raise ShopeeOneClickPreDispatchError(
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
    evidence: Mapping[str, object] | None = None,
) -> None:
    try:
        state.confirm(
            request,
            occurrence,
            evidence=_occurrence_evidence(
                occurrence.occurrence_id,
                external_id=external_id,
                extra=evidence,
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
            raise ShopeeOneClickPreDispatchError(
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
) -> ShopeeOneClickDispatchError:
    writes, exact, lower, upper = state.unknown_bounds(occurrence)
    return ShopeeOneClickDispatchError(
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
        return ShopeeOneClickPreDispatchError(detail)
    return ShopeeOneClickDispatchError(
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
) -> ShopeeOneClickDispatchError:
    return ShopeeOneClickDispatchError(
        str(error),
        writes=error.external_writes,
        unknown=error.external_write_count is None,
        external_id=external_id,
        external_write_count=error.external_write_count,
        confirmed_lower_bound=error.confirmed_lower_bound,
        possible_upper_bound=error.possible_upper_bound,
    )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ShopeeOneClickPreDispatchError(f"{name} is invalid")
    return value


def _nonempty(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ShopeeOneClickPreDispatchError(f"{name} is invalid")
    return value.strip()


def _nonempty_digest(value: object, name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(
            character not in "0123456789abcdef"
            for character in value
        )
    ):
        raise ShopeeOneClickPreDispatchError(f"{name} is invalid")
    return value


def _nonempty_exact(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ShopeeOneClickPreDispatchError(f"{name} is invalid")
    return value


def _positive_identity(value: object, name: str) -> str:
    if (
        isinstance(value, bool)
        or type(value) not in {str, int}
        or not str(value).isdigit()
        or int(str(value)) <= 0
    ):
        raise ShopeeOneClickPreDispatchError(f"{name} is invalid")
    return str(value)


def _positive_int(value: object, name: str) -> int:
    return int(_positive_identity(value, name))


def _finite_positive_decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ShopeeOneClickPreDispatchError(f"{name} is invalid")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ShopeeOneClickPreDispatchError(
            f"{name} is invalid"
        ) from error
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise ShopeeOneClickPreDispatchError(f"{name} is invalid")
    return decimal_value


def _exact_string_list(value: object) -> bool:
    return bool(
        isinstance(value, list)
        and value
        and all(type(item) is str and bool(item) for item in value)
        and len(value) == len(set(value))
    )


class _NoImageRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _image_format(payload: bytes) -> tuple[str, str]:
    """Return the supported format proven by magic bytes."""
    if type(payload) is not bytes:
        raise ValueError("approved image payload is invalid")
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if (
        len(payload) >= 12
        and payload.startswith(b"RIFF")
        and payload[8:12] == b"WEBP"
    ):
        return "image/webp", ".webp"
    raise ValueError("approved image format is unsupported")


def _download_public_https_image(
    url: object,
    *,
    timeout: float = 8,
    max_bytes: int = 12 * 1024 * 1024,
    opener: object = None,
) -> ShopeePreparedImage:
    """Fetch one approved image without redirects or private-network access."""
    if type(url) is not str:
        raise ValueError("approved image URL is invalid")
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
    ):
        raise ValueError("approved image URL must be public HTTPS")
    try:
        addresses = {
            row[4][0]
            for row in socket.getaddrinfo(
                parsed.hostname,
                443,
                type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror as error:
        raise ValueError("approved image host cannot be resolved") from error
    if not addresses or any(
        not ipaddress.ip_address(address).is_global
        for address in addresses
    ):
        raise ValueError("approved image host is not public")
    selected_opener = opener or urllib.request.build_opener(
        _NoImageRedirects(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": "Orbit-OneClick/1.0",
            "Accept": "image/png,image/jpeg,image/webp,image/*;q=0.8",
        },
    )
    with selected_opener.open(request, timeout=timeout) as response:
        content_type = str(
            response.headers.get("Content-Type") or ""
        ).split(";", 1)[0].strip().lower()
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("approved URL is not a supported image")
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except (TypeError, ValueError) as error:
                raise ValueError("approved image size is invalid") from error
            if declared <= 0 or declared > max_bytes:
                raise ValueError("approved image size is invalid")
        payload = response.read(max_bytes + 1)
    if type(payload) is not bytes or not payload or len(payload) > max_bytes:
        raise ValueError("approved image size is invalid")
    detected_media_type, suffix = _image_format(payload)
    if detected_media_type != content_type:
        raise ValueError("approved image Content-Type does not match its bytes")
    return ShopeePreparedImage(
        content=payload,
        media_type=detected_media_type,
        suffix=suffix,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
