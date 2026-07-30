"""03-owned pure inputs for the 00 one-click prepare/dispatch control plane.

This module deliberately contains no store, server, or worker imports.  00
supplies typed ``PrepareTargetRequest``/``DispatchTargetRequest``; 03 turns
that immutable identity into a narrow seed and its production provider
rehydrates the channel client for official read-only preparation.

Keeping this conversion pure is intentional.  A missing provider must remain
``BLOCKED_CAPABILITY`` at the typed integration boundary rather than silently
falling back to a legacy publish/retry path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json

from domains.channel_operations.oneclick_channel_preparation import (
    OneClickPreparationError,
    prepare_shopee_plan_native_first_attempt,
    prepare_tiktok_source_query_from_canonical_identity,
)


class OneClickAdapterInputError(ValueError):
    """Immutable request data is not safe to pass to an official provider."""


class OneClickProviderPreDispatchError(RuntimeError):
    """A dedicated provider stopped before its first merchant invocation."""


class OneClickProviderDispatchError(RuntimeError):
    """A provider preserves writes already performed at its own boundary."""

    def __init__(
        self,
        detail: str,
        *,
        external_writes: tuple[str, ...],
        external_id: str | None = None,
        dispatch_outcome_unknown: bool = False,
        external_write_count: int | None = None,
        confirmed_external_write_count_lower_bound: int = 0,
        possible_external_write_count_upper_bound: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.external_writes = external_writes
        self.external_id = external_id
        self.dispatch_outcome_unknown = dispatch_outcome_unknown
        self.external_write_count = external_write_count
        self.confirmed_external_write_count_lower_bound = (
            confirmed_external_write_count_lower_bound
        )
        self.possible_external_write_count_upper_bound = (
            possible_external_write_count_upper_bound
        )


TIKTOK_MIAOSHOU_TARGETS = frozenset(
    {
        "miaoshou:COMMON",
        "tiktok:LH_PH",
        "tiktok:LH_MY",
        "tiktok:LH_TH",
        "tiktok:LH_VN",
        "tiktok:MX",
        "tiktok:GB",
        "tiktok:HB_PH",
        "tiktok:HB_MY",
        "tiktok:HB_TH",
        "tiktok:HB_VN",
    }
)
API_LESS_TIKTOK_TARGETS = frozenset(
    {
        "tiktok:MX",
        "tiktok:GB",
        "tiktok:HB_PH",
        "tiktok:HB_MY",
        "tiktok:HB_TH",
        "tiktok:HB_VN",
    }
)
SHOPEE_GLOBAL_TARGET = "shopee:GLOBAL"
SHOPEE_REGIONAL_TARGETS = frozenset(
    {"shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN"}
)
SHOPEE_TARGETS = frozenset(
    {SHOPEE_GLOBAL_TARGET, *SHOPEE_REGIONAL_TARGETS}
)


@dataclass(frozen=True)
class OneClickPrepareSeed:
    """A redacted, digest-bound input to a future read-only provider."""

    target_label: str
    idempotency_key: str
    source_identity_digest: str
    command: Mapping[str, object]
    seed_digest: str


@dataclass(frozen=True)
class OneClickProvider:
    """03-owned, injectable channel primitive factory.

    Tests supply a fixture provider.  Production composition supplies this
    factory itself; the 00 control plane owns only typed request/receipt
    orchestration and never provides a marketplace client.
    """

    prepare_tiktok_miaoshou: Callable[[OneClickPrepareSeed, object], Mapping[str, object]]
    dispatch_tiktok_miaoshou: Callable[[object], Mapping[str, object]]
    prepare_shopee: Callable[[OneClickPrepareSeed, object], Mapping[str, object]]
    dispatch_shopee: Callable[[object], Mapping[str, object]]


_provider_factory: Callable[[], OneClickProvider] | None = None


def configure_provider_factory(factory: Callable[[], OneClickProvider] | None) -> None:
    """Install the 03-owned provider factory; primarily used by fixtures."""
    global _provider_factory
    _provider_factory = factory


def observe_channel_category_options(
    request: Mapping[str, object],
) -> dict[str, object]:
    """Read official Shopee NEW_GLOBAL category options without mutation."""

    from modules.shopee.global_plan_candidate import (
        observe_channel_category_options as observe_official,
    )

    return observe_official(request)


def observe_shopee_global_plan_candidate(
    request: Mapping[str, object],
) -> object:
    """Return the shared read-only Shopee global-plan candidate contract.

    The product server owns every approved source fact in ``candidate_seed``.
    This seam adds only official, no-refresh channel observations.  It never
    creates a command and never calls a mutation endpoint.

    The currently audited production reader can prove the complete
    NORMAL/UNLIST/BANNED model-SKU identity scan.  Category, attribute, brand,
    seller-stock and location facts still require the explicitly configured
    first-party observer.  In particular, generated SDK metadata is never
    promoted to authority.
    """

    from modules.shopee.oneclick_release import (
        ShopeeOneClickPreDispatchError,
        ShopeeOneClickPrepareBlocked,
        _global_candidate_observer_factory,
        _observe_existing_global_candidate_availability,
        _prepare_transport,
        _scan_global_model_candidates,
    )
    from modules.shopee.global_plan_candidate import (
        ShopeeGlobalPlanCandidateError,
        build_official_new_global_candidate,
    )
    from shared_platform.shopee_global_plan import (
        BLOCKED_CAPABILITY,
        EXISTING_GLOBAL,
        NEW_GLOBAL,
        READY,
        ShopeeGlobalPlanCandidate,
        ShopeeGlobalPlanObservationError,
        build_shopee_existing_current_snapshot_candidate,
    )

    seed, targets, model_skus = _global_observer_request(request)
    region = next(
        (
            value
            for value in ("PH", "MY", "TH", "VN")
            if f"shopee:{value}" in targets
        ),
        None,
    )
    if region is None:
        raise OneClickAdapterInputError(
            "shopee_global_observer_target_invalid"
        )
    try:
        transport = _prepare_transport(region)
    except ShopeeOneClickPreDispatchError as error:
        if "credential" in str(error).casefold():
            raise ShopeeGlobalPlanObservationError(
                category="AUTH",
                code="shopee_prepared_credentials_unavailable",
            ) from error
        raise ShopeeGlobalPlanObservationError(
            category="CAPABILITY",
            code="shopee_official_global_observation_unavailable",
        ) from error

    observed_by_sku = {
        sku: _scan_global_model_candidates(transport, model_sku=sku)
        for sku in model_skus
    }
    unique_ids = {
        item_id
        for values in observed_by_sku.values()
        for item_id in values
    }
    if any(len(values) > 1 for values in observed_by_sku.values()):
        raise ShopeeOneClickPrepareBlocked(
            "shopee_existing_global_model_ambiguous",
            "an approved model SKU resolves to multiple official global items",
            category="CONTENT",
        )
    if not unique_ids:
        mode = NEW_GLOBAL
        official_context: dict[str, object] = {
            "mode": mode,
            "global_scan_exact_zero": True,
            "model_sku_count": len(model_skus),
            "model_sku_scan_digest": _digest(
                {"model_sku_matches": observed_by_sku}
            ),
        }
    elif (
        len(unique_ids) == 1
        and all(len(values) == 1 for values in observed_by_sku.values())
    ):
        mode = EXISTING_GLOBAL
        global_item_id = next(iter(unique_ids))
        official_context = {
            "mode": mode,
            "global_scan_exact_one": True,
            "model_sku_count": len(model_skus),
            "global_item_id": global_item_id,
            "global_item_identity_digest": hashlib.sha256(
                global_item_id.encode("utf-8")
            ).hexdigest(),
            "model_sku_scan_digest": _digest(
                {"model_sku_matches": observed_by_sku}
            ),
        }
    else:
        raise ShopeeOneClickPrepareBlocked(
            "shopee_existing_global_model_set_drift",
            "approved model SKUs do not resolve to one exact global item",
            category="CONTENT",
        )

    if _global_candidate_observer_factory is None:
        if mode == EXISTING_GLOBAL:
            current = _observe_existing_global_candidate_availability(
                transport,
                global_item_id=official_context["global_item_id"],
                seed=seed,
                expected_model_skus=model_skus,
            )
            candidate = build_shopee_existing_current_snapshot_candidate(
                observation_authority="shopee_official_open_api",
                observation_schema_version=(
                    "shopee-official-global-plan-observation/v1"
                ),
                observation_evidence_digest=current[
                    "observation_evidence_digest"
                ],
                source_identity_schema_version=seed[
                    "source_identity_schema_version"
                ],
                source_identity_digest=seed["source_identity_digest"],
                sku_lineage_schema_version=seed[
                    "sku_lineage_schema_version"
                ],
                sku_lineage_digest=seed["sku_lineage_digest"],
                content_package_digest=seed["content_package_digest"],
                title=seed["title"],
                description=seed["description"],
                approved_copy_digest=seed["approved_copy_digest"],
                ordered_approved_images=seed[
                    "ordered_approved_images"
                ],
                approved_source_image_manifest_digest=seed[
                    "approved_source_image_manifest_digest"
                ],
                selected_image_positions=seed[
                    "selected_image_positions"
                ],
                parcel=seed["parcel"],
                target_pricing=seed["target_pricing"],
                policy_digest=seed["policy_digest"],
                expected_model_skus=list(model_skus),
                existing_global_item=current["existing_global_item"],
                existing_global_models=current["existing_global_models"],
                existing_global_identity_evidence_digest=current[
                    "existing_global_identity_evidence_digest"
                ],
            )
            if (
                type(candidate) is not ShopeeGlobalPlanCandidate
                or candidate.status != READY
                or candidate.mode != EXISTING_GLOBAL
            ):
                raise ShopeeOneClickPrepareBlocked(
                    "shopee_official_existing_snapshot_invalid",
                    "official Shopee existing-global snapshot is invalid",
                    category="CONTENT",
                )
            return candidate
        observer = build_official_new_global_candidate
    else:
        observer = _global_candidate_observer_factory
    try:
        candidate = observer(
            request,
            {
                **seed,
                "mode": mode,
                "official_identity_observation": official_context,
            },
            transport,
        )
    except ShopeeGlobalPlanCandidateError as error:
        if error.reason_category not in {"AUTH", "CAPABILITY"}:
            raise ShopeeOneClickPrepareBlocked(
                error.reason_code,
                "official Shopee selected category evidence drifted",
                category=error.reason_category,
            ) from error
        raise ShopeeGlobalPlanObservationError(
            category=error.reason_category,
            code=error.reason_code,
        ) from error
    if (
        type(candidate) is ShopeeGlobalPlanCandidate
        and candidate.status == BLOCKED_CAPABILITY
        and candidate.mode == mode
    ):
        return candidate
    if (
        type(candidate) is not ShopeeGlobalPlanCandidate
        or candidate.status != READY
        or candidate.mode != mode
        or candidate._plan is None
    ):
        raise ShopeeOneClickPrepareBlocked(
            "shopee_official_global_candidate_invalid",
            "first-party Shopee observer did not return a READY shared candidate",
            category="CAPABILITY",
        )
    plan = candidate._plan.payload()
    observed_models = plan.get("global_model")
    if (
        not isinstance(observed_models, list)
        or sorted(
            row.get("global_model_sku")
            for row in observed_models
            if isinstance(row, Mapping)
        )
        != sorted(model_skus)
    ):
        raise ShopeeOneClickPrepareBlocked(
            "shopee_official_global_model_contract_drift",
            "first-party candidate model identities drifted",
            category="CONTENT",
        )
    if mode == EXISTING_GLOBAL:
        observed_id = plan.get("existing_global_item_id")
        if str(observed_id or "") != official_context["global_item_id"]:
            raise ShopeeOneClickPrepareBlocked(
                "shopee_official_global_identity_drift",
                "first-party candidate global identity drifted",
                category="CONTENT",
            )
    elif plan.get("existing_global_item_id") is not None:
        raise ShopeeOneClickPrepareBlocked(
            "shopee_new_global_identity_drift",
            "NEW_GLOBAL candidate unexpectedly contains an existing identity",
            category="CONTENT",
        )
    return candidate


def build_tiktok_miaoshou_prepare_seed(request: object) -> OneClickPrepareSeed:
    """Use only the control-plane canonical source identity for source search."""
    target_label, idempotency_key, source_identity, source_digest = _request_identity(
        request
    )
    if target_label not in TIKTOK_MIAOSHOU_TARGETS:
        raise OneClickAdapterInputError("tiktok_miaoshou_target_unsupported")
    try:
        query = prepare_tiktok_source_query_from_canonical_identity(source_identity)
    except OneClickPreparationError as error:
        raise OneClickAdapterInputError("systemic_source_identity_invalid") from error
    command = {
        "schema_version": "oneclick-tiktok-miaoshou-prepare-seed/v1",
        "target_label": target_label,
        "idempotency_key": idempotency_key,
        "source_query": query,
    }
    return OneClickPrepareSeed(
        target_label=target_label,
        idempotency_key=idempotency_key,
        source_identity_digest=source_digest,
        command=command,
        seed_digest=_digest(command),
    )


def build_shopee_prepare_seed(
    request: object,
    immutable_shopee_command: Mapping[str, object],
) -> OneClickPrepareSeed:
    """Bind an already plan-native v2 Shopee command to control-plane identity.

    This cannot call ``publish_match_key``, ``_find_tk_for_global`` or a
    TikTok/local-products lookup.  Those paths are excluded by the underlying
    command guard before an official Shopee observation can be requested.
    """
    target_label, idempotency_key, _source_identity, source_digest = _request_identity(
        request
    )
    if target_label not in SHOPEE_TARGETS:
        raise OneClickAdapterInputError("shopee_target_unsupported")
    if immutable_shopee_command.get("target_label") != target_label:
        raise OneClickAdapterInputError("shopee_target_identity_drift")
    if (
        immutable_shopee_command.get("schema_version")
        == "oneclick-approved-shopee-global-seed/v1"
    ):
        try:
            canonical = json.loads(
                json.dumps(
                    immutable_shopee_command,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        except (TypeError, ValueError) as error:
            raise OneClickAdapterInputError(
                "approved_shopee_global_seed_invalid"
            ) from error
        command = {
            "schema_version": "oneclick-shopee-prepare-seed/v2",
            "target_label": target_label,
            "idempotency_key": idempotency_key,
            "approved_global": canonical,
        }
        return OneClickPrepareSeed(
            target_label=target_label,
            idempotency_key=idempotency_key,
            source_identity_digest=source_digest,
            command=command,
            seed_digest=_digest(command),
        )
    try:
        prepared = prepare_shopee_plan_native_first_attempt(
            immutable_shopee_command
        )
    except OneClickPreparationError as error:
        raise OneClickAdapterInputError("shopee_plan_native_command_invalid") from error
    command = {
        "schema_version": "oneclick-shopee-prepare-seed/v1",
        "target_label": target_label,
        "idempotency_key": idempotency_key,
        "prepared": prepared,
    }
    return OneClickPrepareSeed(
        target_label=target_label,
        idempotency_key=idempotency_key,
        source_identity_digest=source_digest,
        command=command,
        seed_digest=_digest(command),
    )


def prepare_oneclick_target(
    request: object,
    *,
    provider_factory: Callable[[], OneClickProvider] | None = None,
) -> Mapping[str, object]:
    """Prepare exactly one channel target through an owned read-only provider.

    The return shape is deliberately accepted by 00's
    ``PrepareTargetResult.from_value`` after the final control-plane commit is
    integrated.  No generic release executor, claim JSON, database, or write
    primitive is reachable from this function.
    """
    target = getattr(request, "target_label", None)
    provider = _resolve_provider(provider_factory)
    if target in TIKTOK_MIAOSHOU_TARGETS:
        seed = build_tiktok_miaoshou_prepare_seed(request)
        observed = _provider_prepare(
            provider.prepare_tiktok_miaoshou, seed, request
        )
        if _is_blocked_provider_result(observed):
            return _blocked_provider_result(observed)
        return _ready_prepare_result(
            target=target,
            seed=seed,
            observed=observed,
            manual_after_submit=target in API_LESS_TIKTOK_TARGETS,
        )
    if target in SHOPEE_TARGETS:
        try:
            command = _immutable_shopee_command(request)
        except OneClickAdapterInputError as error:
            if str(error) == "approved_shopee_existing_v2_required":
                return _blocked_prepare_result(
                    "BLOCKED_CAPABILITY",
                    "CONTENT",
                    "approved_shopee_existing_v2_required",
                    "existing Shopee global facts require a fresh official v2 observation and approval",
                )
            if str(error) == "approved_shopee_plan_facts_incomplete":
                return _blocked_prepare_result(
                    "BLOCKED_CAPABILITY",
                    "CONTENT",
                    "approved_shopee_plan_facts_incomplete",
                    "approved Shopee copy, parcel, images, price, or SKU facts are incomplete",
                )
            raise
        try:
            seed = build_shopee_prepare_seed(request, command)
        except OneClickAdapterInputError as error:
            if str(error) == "shopee_plan_native_command_invalid":
                return _blocked_prepare_result(
                    "BLOCKED_CAPABILITY",
                    "CONTENT",
                    "approved_shopee_write_facts_invalid",
                    "approved Shopee write facts violate the platform contract",
                )
            raise
        observed = _provider_prepare(provider.prepare_shopee, seed, request)
        if _is_blocked_provider_result(observed):
            return _blocked_provider_result(observed)
        return _ready_prepare_result(
            target=target,
            seed=seed,
            observed=observed,
            manual_after_submit=False,
        )
    if target == "ozon:RU":
        return _blocked_prepare_result(
            "BLOCKED_INVENTORY",
            "INVENTORY",
            "approved_inventory_decision_missing",
            "Ozon needs an approved READY inventory decision; no default stock is used",
        )
    return _blocked_prepare_result(
        "BLOCKED_CAPABILITY",
        "CAPABILITY",
        "target_channel_unsupported",
        "no dedicated one-click channel primitive is registered",
    )


def dispatch_oneclick_target(
    request: object,
    *,
    provider_factory: Callable[[], OneClickProvider] | None = None,
) -> Mapping[str, object]:
    """Dispatch one stored command, preserving the provider's exact boundary."""
    target = getattr(request, "target_label", None)
    provider = _resolve_provider(provider_factory)
    try:
        if target in TIKTOK_MIAOSHOU_TARGETS:
            receipt = provider.dispatch_tiktok_miaoshou(request)
        elif target in SHOPEE_TARGETS:
            receipt = provider.dispatch_shopee(request)
        elif target == "ozon:RU":
            return {
                "canonical_status": "BLOCKED_INVENTORY",
                "reason_category": "INVENTORY",
                "reason_scope": "TARGET",
                "reason_code": "approved_inventory_decision_missing",
                "reason_detail": "Ozon inventory is not approved",
                "external_writes": (),
            }
        else:
            return {
                "canonical_status": "BLOCKED_CAPABILITY",
                "reason_category": "CAPABILITY",
                "reason_scope": "TARGET",
                "reason_code": "target_channel_unsupported",
                "reason_detail": "no dedicated one-click channel primitive is registered",
                "external_writes": (),
            }
    except OneClickProviderPreDispatchError as error:
        _raise_pre_dispatch(error)
    except OneClickProviderDispatchError as error:
        _raise_post_dispatch(error)
    except Exception as error:
        # The owned concrete primitives carry the same narrow attributes. Do
        # not use a broad fallback to guess that a write occurred.
        writes = getattr(error, "external_writes", None)
        unknown = getattr(error, "dispatch_outcome_unknown", None)
        if isinstance(writes, tuple) and type(unknown) is bool:
            _raise_post_dispatch(
                OneClickProviderDispatchError(
                    str(error),
                    external_writes=writes,
                    external_id=getattr(error, "external_id", None),
                    dispatch_outcome_unknown=unknown,
                    external_write_count=getattr(
                        error, "external_write_count", None
                    ),
                    confirmed_external_write_count_lower_bound=getattr(
                        error,
                        "confirmed_external_write_count_lower_bound",
                        0,
                    ),
                    possible_external_write_count_upper_bound=getattr(
                        error,
                        "possible_external_write_count_upper_bound",
                        None,
                    ),
                )
            )
        if type(error).__name__ in {
            "MiaoshouOneClickPreDispatchError",
            "ShopeeOneClickPreDispatchError",
        }:
            _raise_pre_dispatch(error)
        raise
    if not isinstance(receipt, Mapping):
        raise OneClickProviderPreDispatchError("provider dispatch receipt is invalid")
    # Providers must return the final control-plane canonical receipt shape;
    # coercion/validation remains owned by 00's DispatchTargetResult.
    return dict(receipt)


def production_adapter_registry(
    *,
    provider_factory: Callable[[], OneClickProvider] | None = None,
) -> dict[str, object]:
    """Return typed 00 registrations after the final control-plane is present.

    Importing the contract here (rather than at module import) keeps the 03
    preparation branch independently testable until the final 00 hash lands.
    """
    try:
        from shared_platform.oneclick_release_controlplane import AdapterRegistration
    except ImportError as error:  # pragma: no cover - only before 00 integration
        raise RuntimeError("one-click control-plane contract is not integrated") from error
    policy_digest = _digest(
        {
            "schema_version": "oneclick-channel-operations-policy/v1",
            "channels": ["miaoshou", "tiktok", "shopee", "ozon"],
            "source_identity": "source-product-identity/v1",
            "shopee": "plan-native-v2-no-legacy-match",
            "ozon": "approved-inventory-only",
        }
    )
    factory = provider_factory or _production_provider_factory
    registrations = {
        "new_product_workbench_miaoshou_commit": (
            tuple(sorted(label for label in TIKTOK_MIAOSHOU_TARGETS if label == "miaoshou:COMMON"))
        ),
        "miaoshou_tiktok_publish": tuple(sorted(label for label in TIKTOK_MIAOSHOU_TARGETS if label.startswith("tiktok:"))),
        "shopee_cnsc_publish": tuple(
            sorted(
                SHOPEE_TARGETS,
                key=lambda label: (
                    label != SHOPEE_GLOBAL_TARGET,
                    label,
                ),
            )
        ),
        "ozon_product_publish": ("ozon:RU",),
    }
    registry = {
        name: AdapterRegistration(
            adapter_name=name,
            target_labels=labels,
            prepare=lambda request, factory=factory: prepare_oneclick_target(
                request, provider_factory=factory
            ),
            dispatch=lambda request, factory=factory: dispatch_oneclick_target(
                request, provider_factory=factory
            ),
            policy_digest=policy_digest,
            prepare_is_read_only=True,
            consumes_prepared_command=True,
            preserves_idempotency_key=True,
            reports_truthful_receipt=True,
        )
        for name, labels in registrations.items()
    }
    from modules.tiktok.oneclick_promotion import (
        promotion_adapter_policy_digest,
    )

    promotion_targets = tuple(
        f"promotion:{channel}:{site}"
        for channel, site in (
            ("shopee", "MY"),
            ("shopee", "PH"),
            ("shopee", "TH"),
            ("shopee", "VN"),
            ("tiktok", "LH_MY"),
            ("tiktok", "LH_PH"),
            ("tiktok", "LH_TH"),
            ("tiktok", "LH_VN"),
        )
    )
    registry["postpublish_promotion"] = AdapterRegistration(
        adapter_name="postpublish_promotion",
        target_labels=promotion_targets,
        prepare=_prepare_postpublish_promotion,
        dispatch=_dispatch_postpublish_promotion,
        policy_digest=promotion_adapter_policy_digest(),
        prepare_is_read_only=True,
        consumes_prepared_command=True,
        preserves_idempotency_key=True,
        reports_truthful_receipt=True,
    )
    return registry


def _prepare_postpublish_promotion(request: object) -> Mapping[str, object]:
    from modules.tiktok.oneclick_promotion import (
        TikTokPromotionBlocked,
        prepare_postpublish_promotion,
    )
    from shared_platform.postpublish_promotions import (
        PostpublishPromotionContractError,
    )

    try:
        return prepare_postpublish_promotion(request)
    except TikTokPromotionBlocked as error:
        return {
            "classification": error.classification,
            "reason_category": error.reason_category,
            "reason_scope": "TARGET",
            "reason_code": error.reason_code,
            "reason_detail": error.reason_detail,
            "command": None,
            "proof": None,
            "manual_after_submit": False,
        }
    except PostpublishPromotionContractError as error:
        return {
            "classification": "BLOCKED_CAPABILITY",
            "reason_category": "SYSTEMIC_CONTRACT",
            "reason_scope": "TARGET",
            "reason_code": "approved_promotion_policy_invalid",
            "reason_detail": str(error),
            "command": None,
            "proof": None,
            "manual_after_submit": False,
        }


def _dispatch_postpublish_promotion(
    request: object,
) -> Mapping[str, object]:
    from modules.tiktok.oneclick_promotion import (
        TikTokPromotionBlocked,
        TikTokPromotionDispatchError,
        TikTokPromotionPreDispatchError,
        dispatch_postpublish_promotion,
    )
    from shared_platform.postpublish_promotions import (
        PostpublishPromotionContractError,
    )
    from shared_platform.oneclick_release_controlplane import (
        DispatchInvocationError,
        PreDispatchInvocationError,
    )

    try:
        return dispatch_postpublish_promotion(request)
    except TikTokPromotionPreDispatchError as error:
        raise PreDispatchInvocationError(str(error)) from error
    except (
        TikTokPromotionBlocked,
        PostpublishPromotionContractError,
    ) as error:
        raise PreDispatchInvocationError(str(error)) from error
    except TikTokPromotionDispatchError as error:
        raise DispatchInvocationError(
            str(error),
            external_writes=error.external_writes,
            dispatch_outcome_unknown=error.dispatch_outcome_unknown,
            external_write_count=error.external_write_count,
            confirmed_external_write_count_lower_bound=(
                error.confirmed_external_write_count_lower_bound
            ),
            possible_external_write_count_upper_bound=(
                error.possible_external_write_count_upper_bound
            ),
        ) from error


def _production_provider_factory() -> OneClickProvider:
    """03-owned production factory; imports clients only when a worker calls it.

    The concrete source/detail and plan-native Shopee primitives are kept
    separate from the control plane.  They are deliberately not invoked by
    unit tests or while the worker feature gate is disabled.
    """
    from modules.miaoshou.oneclick_release import (
        dispatch_tiktok_miaoshou_prepared_target,
        prepare_tiktok_miaoshou_target,
    )
    from modules.shopee.oneclick_release import (
        dispatch_plan_native_target,
        prepare_plan_native_target,
    )
    return OneClickProvider(
        prepare_tiktok_miaoshou=prepare_tiktok_miaoshou_target,
        dispatch_tiktok_miaoshou=dispatch_tiktok_miaoshou_prepared_target,
        prepare_shopee=prepare_plan_native_target,
        dispatch_shopee=dispatch_plan_native_target,
    )


def _resolve_provider(
    factory: Callable[[], OneClickProvider] | None,
) -> OneClickProvider:
    selected = factory or _provider_factory or _production_provider_factory
    provider = selected()
    if not isinstance(provider, OneClickProvider):
        raise OneClickAdapterInputError("oneclick_provider_factory_invalid")
    return provider


def _provider_prepare(
    callback: Callable[[OneClickPrepareSeed, object], Mapping[str, object]],
    seed: OneClickPrepareSeed,
    request: object,
) -> Mapping[str, object]:
    try:
        observed = callback(seed, request)
    except OneClickProviderDispatchError as error:
        raise OneClickAdapterInputError(
            "prepare_provider_reported_external_write"
        ) from error
    except Exception as error:
        classification = getattr(error, "classification", None)
        category = getattr(error, "reason_category", None)
        code = getattr(error, "reason_code", None)
        scope = getattr(error, "reason_scope", "TARGET")
        if (
            classification
            in {
                "BLOCKED_CAPABILITY",
                "BLOCKED_AUTH",
                "BLOCKED_INVENTORY",
            }
            and type(category) is str
            and category
            and type(code) is str
            and code
            and type(scope) is str
            and scope
        ):
            return {
                "classification": classification,
                "reason_category": category,
                "reason_scope": scope,
                "reason_code": code,
                "reason_detail": str(error),
                "external_writes_performed": [],
            }
        if isinstance(error, OneClickProviderPreDispatchError):
            raise
        raise
    if not isinstance(observed, Mapping):
        raise OneClickAdapterInputError("prepare_provider_receipt_invalid")
    if observed.get("external_writes_performed", ()) not in ((), []):
        raise OneClickAdapterInputError("prepare_provider_must_be_read_only")
    if _is_blocked_provider_result(observed):
        return observed
    command = observed.get("command")
    proof = observed.get("proof")
    if not isinstance(command, Mapping) or not isinstance(proof, Mapping):
        raise OneClickAdapterInputError("prepare_provider_command_or_proof_missing")
    return observed


def _is_blocked_provider_result(value: Mapping[str, object]) -> bool:
    return value.get("classification") in {
        "BLOCKED_CAPABILITY",
        "BLOCKED_AUTH",
        "BLOCKED_INVENTORY",
    }


def _blocked_provider_result(value: Mapping[str, object]) -> dict[str, object]:
    classification = value.get("classification")
    category = value.get("reason_category")
    scope = value.get("reason_scope")
    code = value.get("reason_code")
    detail = value.get("reason_detail")
    if (
        type(classification) is not str
        or type(category) is not str
        or type(scope) is not str
        or type(code) is not str
        or type(detail) is not str
        or not all((classification, category, scope, code, detail))
    ):
        raise OneClickAdapterInputError("prepare_provider_blocker_invalid")
    return {
        "classification": classification,
        "reason_category": category,
        "reason_scope": scope,
        "reason_code": code,
        "reason_detail": detail,
        "command": None,
        "proof": None,
        "manual_after_submit": False,
    }


def _ready_prepare_result(
    *,
    target: str,
    seed: OneClickPrepareSeed,
    observed: Mapping[str, object],
    manual_after_submit: bool,
) -> Mapping[str, object]:
    provider_command = dict(observed["command"])
    return {
        "classification": "READY_SUBMIT_MANUAL" if manual_after_submit else "EXACT_READY_AUTOMATIC",
        "reason_category": "CAPABILITY",
        "reason_scope": "TARGET",
        "reason_code": "dedicated_channel_prepare_verified",
        "reason_detail": "official read-only channel proof is exact",
        "command": {
            "seed": dict(seed.command),
            "seed_digest": seed.seed_digest,
            "provider_command": provider_command,
        },
        "proof": {
            "seed_digest": seed.seed_digest,
            "provider_proof": dict(observed["proof"]),
        },
        "shared_resource": (
            dict(observed["shared_resource"])
            if isinstance(observed.get("shared_resource"), Mapping)
            else None
        ),
        "write_occurrence_plan": _write_occurrence_plan(
            target, provider_command
        ),
        "manual_after_submit": manual_after_submit,
    }


def _write_occurrence_plan(
    target: str,
    provider_command: Mapping[str, object],
) -> dict[str, object]:
    rows: list[dict[str, str]]
    if target == "miaoshou:COMMON":
        rows = [
            {
                "occurrence_id": "common_update-1",
                "write_class": "miaoshou:COMMON:immutable_plan_write",
            }
        ]
    elif target in TIKTOK_MIAOSHOU_TARGETS:
        rows = []
        if provider_command.get("action") == "CREATE_AND_CLAIM":
            rows.extend(
                [
                    {
                        "occurrence_id": "detail_create-1",
                        "write_class": "miaoshou:tiktok_detail:create",
                    },
                    {
                        "occurrence_id": "shop_claim-1",
                        "write_class": "miaoshou:tiktok_shop:claim",
                    },
                ]
            )
        rows.extend(
            [
                {
                    "occurrence_id": "detail_update-1",
                    "write_class": "miaoshou:tiktok_detail:update",
                },
                {
                    "occurrence_id": "publish_submit-1",
                    "write_class": "miaoshou:tiktok_publish:submission",
                },
            ]
        )
    elif target == SHOPEE_GLOBAL_TARGET:
        selected = provider_command.get("selected_image_positions")
        if provider_command.get("kind") == "GLOBAL_EXISTING":
            rows = []
        elif (
            provider_command.get("kind") == "GLOBAL_NEW"
            and isinstance(selected, list)
            and selected
        ):
            rows = [
                {
                    "occurrence_id": f"image_upload-{index}",
                    "write_class": "shopee:image:upload",
                }
                for index in range(1, len(selected) + 1)
            ]
            rows.extend(
                [
                    {
                        "occurrence_id": "global_create-1",
                        "write_class": "shopee:global_master:create",
                    },
                    {
                        "occurrence_id": "model_init-1",
                        "write_class": "shopee:global_model:init",
                    },
                ]
            )
        else:
            raise OneClickAdapterInputError(
                "shopee_global_occurrence_plan_invalid"
            )
    elif target in SHOPEE_REGIONAL_TARGETS:
        rows = [
            {
                "occurrence_id": "regional_publish-1",
                "write_class": "shopee:regional_publish",
            }
        ]
    elif target == "ozon:RU":
        rows = [
            {
                "occurrence_id": "stock_update-1",
                "write_class": "ozon:stock:update",
            }
        ]
    else:
        rows = []
    return {
        "schema_version": "oneclick-write-occurrence-plan/v1",
        "occurrences": rows,
    }


def _blocked_prepare_result(
    classification: str,
    category: str,
    code: str,
    detail: str,
) -> Mapping[str, object]:
    return {
        "classification": classification,
        "reason_category": category,
        "reason_scope": "TARGET",
        "reason_code": code,
        "reason_detail": detail,
        "command": None,
        "proof": None,
        "manual_after_submit": False,
    }


def _immutable_shopee_command(request: object) -> Mapping[str, object]:
    payload = getattr(request, "immutable_plan_payload", None)
    if not isinstance(payload, Mapping):
        raise OneClickAdapterInputError("immutable_plan_payload_missing")
    if "approved_shopee_global_plan" in payload:
        return _canonical_shopee_global_seed(request, payload)
    command = payload.get("oneclick_shopee_command")
    if isinstance(command, Mapping):
        return command
    target = getattr(request, "target_label", None)
    facts = payload.get("product_facts")
    listing = payload.get("listing_copy")
    pricing_root = payload.get("pricing")
    selected = (
        pricing_root.get("selected_targets")
        if isinstance(pricing_root, Mapping)
        else None
    )
    target_pricing = (
        selected.get(target) if isinstance(selected, Mapping) else None
    )
    store_prices = (
        target_pricing.get("store_prices")
        if isinstance(target_pricing, Mapping)
        else None
    )
    title = _approved_shopee_title(listing)
    description = (
        listing.get("shopee_description_en")
        if isinstance(listing, Mapping)
        else None
    )
    images = payload.get("images")
    seller_sku = payload.get("seller_sku")
    model_sku = _approved_single_model_sku(payload, seller_sku)
    if (
        target not in SHOPEE_TARGETS
        or not isinstance(facts, Mapping)
        or type(title) is not str
        or not title
        or type(description) is not str
        or not description
        or not isinstance(images, list)
        or not images
        or any(not isinstance(row, Mapping) for row in images)
        or not isinstance(store_prices, list)
        or len(store_prices) != 1
        or not isinstance(store_prices[0], Mapping)
        or type(seller_sku) is not str
        or not seller_sku.strip()
        or type(model_sku) is not str
        or not model_sku
    ):
        raise OneClickAdapterInputError(
            "approved_shopee_plan_facts_incomplete"
        )
    image_rows = [
        {"position": index, "image_url": row.get("image_url")}
        for index, row in enumerate(images, start=1)
    ]
    result = {
        "target_label": target,
        "seller_sku": seller_sku.strip(),
        "model_sku": model_sku,
        "listing_copy": {
            "title": title,
            "description": description,
        },
        "images": image_rows,
        "parcel": {
            "weight_kg": facts.get("weight_kg"),
            "package_cm": facts.get("package_cm"),
        },
        "target_pricing": {
            "local_original_price": store_prices[0].get(
                "list_price",
                store_prices[0].get("original_price"),
            ),
            "currency": store_prices[0].get("currency"),
        },
        "policy": {
            "schema_version": "oneclick-shopee-plan-policy/v1",
            "policy_digest": _digest(
                {
                    "adapter_policy_digest": getattr(
                        request, "adapter_policy_digest", ""
                    ),
                    "payload_digest": getattr(request, "payload_digest", ""),
                    "target_label": target,
                }
            ),
        },
    }
    global_create = payload.get("oneclick_shopee_global_create")
    if global_create is None and isinstance(facts, Mapping):
        global_create = facts.get("oneclick_shopee_global_create")
    if global_create is not None:
        if not isinstance(global_create, Mapping):
            raise OneClickAdapterInputError(
                "approved_shopee_global_create_facts_invalid"
            )
        result["global_create"] = dict(global_create)
    return result


def _canonical_shopee_global_seed(
    request: object,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    compact = payload.get("approved_shopee_global_plan")
    record = payload.get("_approved_shopee_global_plan_record")
    target = getattr(request, "target_label", None)
    expected_keys = {
        "schema_version",
        "mode",
        "candidate_digest",
        "approved_plan_digest",
        "selected_image_positions",
        "selected_source_image_manifest_digest",
        "record_digest",
    }
    if (
        isinstance(compact, Mapping)
        and compact.get("schema_version")
        == "approved-shopee-global-plan/v1"
        and compact.get("mode") == "EXISTING_GLOBAL"
    ):
        raise OneClickAdapterInputError(
            "approved_shopee_existing_v2_required"
        )
    if (
        target not in SHOPEE_TARGETS
        or not isinstance(compact, Mapping)
        or set(compact) != expected_keys
        or (
            compact.get("schema_version"),
            compact.get("mode"),
        )
        not in {
            ("approved-shopee-global-plan/v1", "NEW_GLOBAL"),
            ("approved-shopee-global-plan/v2", "EXISTING_GLOBAL"),
        }
        or type(record) is not str
        or not record
        or any(
            not _is_digest(compact.get(key))
            for key in (
                "candidate_digest",
                "approved_plan_digest",
                "selected_source_image_manifest_digest",
                "record_digest",
            )
        )
        or hashlib.sha256(record.encode("utf-8")).hexdigest()
        != compact.get("record_digest")
    ):
        raise OneClickAdapterInputError(
            "approved_shopee_global_binding_invalid"
        )
    try:
        from shared_platform.shopee_global_plan import (
            rehydrate_approved_shopee_global_plan,
        )

        approved = rehydrate_approved_shopee_global_plan(record)
        stored = json.loads(record)["approved_plan"]["plan"]
    except (KeyError, TypeError, ValueError) as error:
        raise OneClickAdapterInputError(
            "approved_shopee_global_record_invalid"
        ) from error
    selected_positions = compact.get("selected_image_positions")
    bindings = (
        stored.get("bindings") if isinstance(stored, Mapping) else None
    )
    lineage = payload.get("sku_lineage")
    reservation = (
        lineage.get("reservation")
        if isinstance(lineage, Mapping)
        else None
    )
    try:
        bound_source_digest = _canonical_binding_digest(
            getattr(request, "source_identity_digest", None)
        )
        bound_lineage_digest = _canonical_binding_digest(
            reservation.get("reservation_digest")
            if isinstance(reservation, Mapping)
            else getattr(request, "sku_lineage_digest", None)
        )
    except OneClickAdapterInputError:
        raise OneClickAdapterInputError(
            "approved_shopee_global_binding_drift"
        ) from None
    if (
        approved.mode != compact["mode"]
        or approved.candidate_digest != compact["candidate_digest"]
        or approved.approved_plan_digest
        != compact["approved_plan_digest"]
        or not isinstance(selected_positions, list)
        or not selected_positions
        or len(selected_positions) > 9
        or any(
            type(position) is not int or position < 1
            for position in selected_positions
        )
        or len(selected_positions) != len(set(selected_positions))
        or stored.get("selected_image_positions") != selected_positions
        or stored.get("selected_source_image_manifest_digest")
        != compact["selected_source_image_manifest_digest"]
        or not isinstance(bindings, Mapping)
        or bindings.get("source_identity_digest")
        != bound_source_digest
        or bindings.get("sku_lineage_digest")
        != bound_lineage_digest
    ):
        raise OneClickAdapterInputError(
            "approved_shopee_global_binding_drift"
        )
    result: dict[str, object] = {
        "schema_version": "oneclick-approved-shopee-global-seed/v1",
        "target_label": target,
        "approved_global_plan_record": record,
        "approved_global_plan": dict(compact),
        "seller_sku": payload.get("seller_sku"),
    }
    if target in SHOPEE_REGIONAL_TARGETS:
        pricing_root = payload.get("pricing")
        selected = (
            pricing_root.get("selected_targets")
            if isinstance(pricing_root, Mapping)
            else None
        )
        target_pricing = (
            selected.get(target) if isinstance(selected, Mapping) else None
        )
        store_prices = (
            target_pricing.get("store_prices")
            if isinstance(target_pricing, Mapping)
            else None
        )
        if (
            type(payload.get("seller_sku")) is not str
            or not str(payload["seller_sku"]).strip()
            or not isinstance(store_prices, list)
            or len(store_prices) != 1
            or not isinstance(store_prices[0], Mapping)
        ):
            raise OneClickAdapterInputError(
                "approved_shopee_regional_facts_incomplete"
            )
        result["target_pricing"] = {
            "local_original_price": store_prices[0].get(
                "list_price",
                store_prices[0].get("original_price"),
            ),
            "currency": store_prices[0].get("currency"),
        }
    return result


def _approved_shopee_title(listing: object) -> str | None:
    if not isinstance(listing, Mapping):
        return None
    direct = listing.get("shopee_title_en")
    if type(direct) is str and direct.strip():
        return direct.strip()
    candidates = listing.get("candidates")
    if not isinstance(candidates, list) or any(
        not isinstance(row, Mapping) for row in candidates
    ):
        return None
    rows = [
        str(row.get("title") or "").strip()
        for row in candidates
        if str(row.get("channel") or "").casefold() == "shopee"
        and str(row.get("site") or "").upper() in {"GLOBAL", "CNSC"}
        and row.get("policy_check") == "passed"
        and str(row.get("title") or "").strip()
    ]
    return rows[0] if len(rows) == 1 else None


def _approved_single_model_sku(
    payload: Mapping[str, object], seller_sku: object
) -> str | None:
    lineage = payload.get("sku_lineage")
    assignment = (
        lineage.get("assignment") if isinstance(lineage, Mapping) else None
    )
    rows = (
        assignment.get("model_skus")
        if isinstance(assignment, Mapping)
        else None
    )
    if not isinstance(rows, list) or len(rows) != 1:
        return None
    row = rows[0]
    if not isinstance(row, Mapping):
        return None
    variant_key = row.get("variant_key")
    model_sku = row.get("model_sku")
    if (
        type(variant_key) is not str
        or not variant_key.strip()
        or type(model_sku) is not str
        or not model_sku.strip()
    ):
        return None
    return model_sku.strip()


def _raise_pre_dispatch(error: Exception) -> None:
    try:
        from shared_platform.oneclick_release_controlplane import PreDispatchInvocationError
    except ImportError:
        raise error
    raise PreDispatchInvocationError(str(error)) from error


def _raise_post_dispatch(error: OneClickProviderDispatchError) -> None:
    try:
        from shared_platform.oneclick_release_controlplane import DispatchInvocationError
    except ImportError:
        raise error
    raise DispatchInvocationError(
        str(error),
        external_writes=error.external_writes,
        external_id=error.external_id,
        dispatch_outcome_unknown=error.dispatch_outcome_unknown,
        external_write_count=error.external_write_count,
        confirmed_external_write_count_lower_bound=(
            error.confirmed_external_write_count_lower_bound
        ),
        possible_external_write_count_upper_bound=(
            error.possible_external_write_count_upper_bound
        ),
    ) from error


def _global_observer_request(
    request: Mapping[str, object],
) -> tuple[dict[str, object], tuple[str, ...], tuple[str, ...]]:
    expected_request_keys = {
        "schema_version",
        "offer_id",
        "product_revision",
        "targets",
        "source_identity",
        "sku_lineage",
        "candidate_seed",
    }
    required_seed_keys = {
        "source_identity_schema_version",
        "source_identity_digest",
        "sku_lineage_schema_version",
        "sku_lineage_digest",
        "content_package_digest",
        "title",
        "description",
        "approved_copy_digest",
        "ordered_approved_images",
        "approved_source_image_manifest_digest",
        "selected_image_positions",
        "parcel",
        "target_pricing",
        "policy_digest",
    }
    optional_seed_keys = {"category_decision_execution"}
    if (
        not isinstance(request, Mapping)
        or set(request) != expected_request_keys
        or request.get("schema_version")
        != "shopee-global-plan-observer-request/v1"
        or type(request.get("offer_id")) is not str
        or not str(request["offer_id"]).isdigit()
        or type(request.get("product_revision")) is not int
        or request["product_revision"] <= 0
    ):
        raise OneClickAdapterInputError(
            "shopee_global_observer_request_invalid"
        )
    raw_targets = request.get("targets")
    if (
        not isinstance(raw_targets, list)
        or not raw_targets
        or any(
            type(value) is not str
            or value
            not in {"shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN"}
            for value in raw_targets
        )
        or len(raw_targets) != len(set(raw_targets))
    ):
        raise OneClickAdapterInputError(
            "shopee_global_observer_targets_invalid"
        )
    source = request.get("source_identity")
    lineage = request.get("sku_lineage")
    seed = request.get("candidate_seed")
    reservation = (
        lineage.get("reservation")
        if isinstance(lineage, Mapping)
        else None
    )
    assignment = (
        lineage.get("assignment")
        if isinstance(lineage, Mapping)
        else None
    )
    model_rows = (
        assignment.get("model_skus")
        if isinstance(assignment, Mapping)
        else None
    )
    if (
        not isinstance(source, Mapping)
        or not isinstance(lineage, Mapping)
        or not isinstance(reservation, Mapping)
        or not isinstance(seed, Mapping)
        or set(seed) - required_seed_keys - optional_seed_keys
        or not required_seed_keys.issubset(seed)
        or source.get("schema_version")
        != seed.get("source_identity_schema_version")
        or source.get("identity_digest")
        != seed.get("source_identity_digest")
        or reservation.get("schema_version")
        != seed.get("sku_lineage_schema_version")
        or reservation.get("reservation_digest")
        != seed.get("sku_lineage_digest")
        or not all(
            _is_digest(seed.get(key))
            for key in (
                "source_identity_digest",
                "sku_lineage_digest",
                "content_package_digest",
                "approved_copy_digest",
                "approved_source_image_manifest_digest",
                "policy_digest",
            )
        )
        or not isinstance(model_rows, list)
        or not model_rows
        or any(not isinstance(row, Mapping) for row in model_rows)
    ):
        raise OneClickAdapterInputError(
            "shopee_global_observer_lineage_invalid"
        )
    if "category_decision_execution" in seed and not isinstance(
        seed["category_decision_execution"], Mapping
    ):
        raise OneClickAdapterInputError(
            "shopee_global_observer_selected_category_invalid"
        )
    model_skus: list[str] = []
    seen_variants: set[str] = set()
    for row in model_rows:
        variant = row.get("variant_key")
        sku = row.get("model_sku")
        if (
            type(variant) is not str
            or not variant
            or type(sku) is not str
            or not sku
            or variant in seen_variants
            or sku in model_skus
        ):
            raise OneClickAdapterInputError(
                "shopee_global_observer_model_identity_invalid"
            )
        seen_variants.add(variant)
        model_skus.append(sku)
    # JSON round-tripping proves the server-owned facts can be passed to the
    # first-party observer without callbacks, tokens, or response objects.
    try:
        normalized_seed = json.loads(
            json.dumps(
                dict(seed),
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as error:
        raise OneClickAdapterInputError(
            "shopee_global_observer_seed_invalid"
        ) from error
    return (
        normalized_seed,
        tuple(raw_targets),
        tuple(model_skus),
    )


def _request_identity(
    request: object,
) -> tuple[str, str, Mapping[str, object], str]:
    """Validate the common typed request fields without importing 00 early."""
    target_label = getattr(request, "target_label", None)
    idempotency_key = getattr(request, "idempotency_key", None)
    source_identity = getattr(request, "source_identity", None)
    source_digest = getattr(request, "source_identity_digest", None)
    if (
        type(target_label) is not str
        or not target_label
        or type(idempotency_key) is not str
        or not idempotency_key
        or not isinstance(source_identity, Mapping)
        or type(source_digest) is not str
        or not source_digest
    ):
        raise OneClickAdapterInputError("controlplane_prepare_request_invalid")
    return target_label, idempotency_key, source_identity, source_digest


def _digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_digest(value: object) -> bool:
    return bool(
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _canonical_binding_digest(value: object) -> str:
    if type(value) is str and value.startswith("sha256:"):
        value = value[7:]
    if not _is_digest(value):
        raise OneClickAdapterInputError(
            "approved_shopee_global_binding_digest_invalid"
        )
    return value
