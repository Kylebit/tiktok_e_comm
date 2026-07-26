import ast
import inspect

import pytest

from domains.channel_operations.omnichannel_orchestrator import (
    PublicationAuthorizationError,
    build_omnichannel_publication_plan,
)
from shared_platform.contracts import (
    ApprovalRecord,
    ApprovedProductPackage,
    ContentPackage,
    ProductRecord,
)


def _product_package(
    *,
    approved: bool = True,
    source_reference: str | None = "miaoshou:collect-box:3828540231",
) -> ApprovedProductPackage:
    return ApprovedProductPackage(
        package_id="product-package:3828540231:r1",
        product=ProductRecord(
            product_id="3828540231",
            seller_sku="0947",
            title="Black line-art wall decal",
            sku_ids=("source-sku-1",),
            attributes={"cost_cny": "8.50", "weight_g": "120"},
        ),
        approval=ApprovalRecord(
            approval_id="approval:product:3828540231:r1",
            subject_type="product",
            subject_id="3828540231",
            status="approved" if approved else "pending",
            approved_by="Kyle",
        ),
        source_reference=source_reference,
    )


def _content_package(
    *,
    product_id: str = "3828540231",
    image_urls: tuple[str, ...] = (
        "https://images.example/hero.jpg",
        "https://images.example/size.jpg",
    ),
) -> ContentPackage:
    return ContentPackage(
        package_id="content-package:3828540231:r1",
        product_id=product_id,
        copy={"en": "A simple black line-art wall decal.", "ru": "Настенная наклейка."},
        image_urls=image_urls,
        approval=ApprovalRecord(
            approval_id="approval:content:3828540231:r1",
            subject_type="content_package",
            subject_id="content-package:3828540231:r1",
            status="approved",
            approved_by="Kyle",
        ),
    )


ALL_TARGETS = {
    "miaoshou": ("COMMON",),
    "tiktok": ("MX", "GB"),
    "ozon": ("RU",),
}


def test_3828540231_builds_one_dry_run_approval_for_all_selected_targets():
    plan = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection=ALL_TARGETS,
    )

    assert plan.dry_run is True
    assert plan.execution_authorized is False
    assert plan.adapter_calls_performed is False
    assert plan.all_preflights_passed is True
    assert plan.approval.collect_box_id == "3828540231"
    assert plan.approval.target_labels == (
        "miaoshou:COMMON",
        "tiktok:GB",
        "tiktok:MX",
        "ozon:RU",
    )
    assert plan.approval.image_count == 2
    assert plan.approval.confirmation_token.startswith("PUBLISH-")
    assert len({target.idempotency_key for target in plan.targets}) == 4
    assert all(target.steps for target in plan.targets)
    assert all(target.executable for target in plan.targets)
    assert plan.targets[0].depends_on == ()
    assert next(target for target in plan.targets if target.channel == "tiktok").depends_on == (
        "miaoshou:COMMON:verified_draft",
    )
    assert next(target for target in plan.targets if target.channel == "ozon").depends_on == (
        "tiktok:MX:verified_readback",
    )


def test_target_order_and_idempotency_are_stable_across_mapping_order():
    first = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection=ALL_TARGETS,
    )
    second = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection={
            "ozon": ["RU"],
            "tiktok": ["GB", "MX"],
            "miaoshou": ["COMMON"],
        },
    )

    assert first.plan_id == second.plan_id
    assert first.approval.confirmation_token == second.approval.confirmation_token
    assert [target.idempotency_key for target in first.targets] == [
        target.idempotency_key for target in second.targets
    ]


@pytest.mark.parametrize(
    ("user_approved", "token", "message"),
    [
        (False, None, "literal user_approved"),
        (True, None, "dry-run confirmation token"),
        (True, "PUBLISH-WRONG", "does not match"),
    ],
)
def test_execute_requires_both_literal_approval_and_exact_bound_token(
    user_approved, token, message
):
    with pytest.raises(PublicationAuthorizationError, match=message):
        build_omnichannel_publication_plan(
            _product_package(),
            _content_package(),
            site_selection=ALL_TARGETS,
            execute=True,
            user_approved=user_approved,
            confirmation_token=token,
        )


def test_exact_preview_token_authorizes_but_never_performs_adapter_calls():
    preview = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection=ALL_TARGETS,
    )
    authorised = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection=ALL_TARGETS,
        execute=True,
        user_approved=True,
        confirmation_token=preview.approval.confirmation_token,
    )

    assert authorised.dry_run is False
    assert authorised.execution_authorized is True
    assert authorised.adapter_calls_performed is False
    assert authorised.plan_id == preview.plan_id


def test_token_is_invalidated_by_image_order_or_target_change():
    preview = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection=ALL_TARGETS,
    )
    reversed_images = _content_package(
        image_urls=tuple(reversed(_content_package().image_urls))
    )

    changed_images = build_omnichannel_publication_plan(
        _product_package(),
        reversed_images,
        site_selection=ALL_TARGETS,
    )
    changed_targets = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection={"tiktok": ("GB",)},
    )

    assert changed_images.approval.confirmation_token != preview.approval.confirmation_token
    assert changed_targets.approval.confirmation_token != preview.approval.confirmation_token


def test_token_is_invalidated_by_commercial_pricing_scope_change():
    first = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection=ALL_TARGETS,
        commercial_scope={"pricing_digest": "price-v1"},
    )
    changed = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection=ALL_TARGETS,
        commercial_scope={"pricing_digest": "price-v2"},
    )

    assert first.plan_id != changed.plan_id
    assert first.approval.confirmation_token != changed.approval.confirmation_token


def test_token_binds_exact_store_price_and_fx_snapshot():
    selection = {
        "miaoshou": ("COMMON",),
        "tiktok": ("LH_TH",),
        "shopee": ("TH",),
    }
    first = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection=selection,
        commercial_scope={
            "selected_store_prices": [
                {"target_key": "lh_th", "list_price": 159, "currency": "THB"},
            ],
            "workbench_exchange_rates": {"THB": 0.2218},
        },
    )
    changed_fx = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection=selection,
        commercial_scope={
            "selected_store_prices": [
                {"target_key": "lh_th", "list_price": 159, "currency": "THB"},
            ],
            "workbench_exchange_rates": {"THB": 0.225},
        },
    )
    changed_price = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection=selection,
        commercial_scope={
            "selected_store_prices": [
                {"target_key": "lh_th", "list_price": 169, "currency": "THB"},
            ],
            "workbench_exchange_rates": {"THB": 0.2218},
        },
    )

    assert first.plan_id != changed_fx.plan_id
    assert first.plan_id != changed_price.plan_id
    assert (
        first.approval.confirmation_token
        != changed_fx.approval.confirmation_token
    )
    assert (
        first.approval.confirmation_token
        != changed_price.approval.confirmation_token
    )


def test_preflight_blocks_unapproved_identity_or_unaudited_site():
    plan = build_omnichannel_publication_plan(
        _product_package(approved=False, source_reference=None),
        _content_package(product_id="another-product"),
        site_selection={"tiktok": ("TH",)},
    )

    failed_codes = {
        check.code
        for check in plan.targets[0].preflight
        if not check.passed
    }
    assert failed_codes == {
        "product_approval",
        "product_content_identity",
        "collect_box_lineage",
        "audited_adapter_site",
        "upstream_target_selected",
    }
    assert plan.all_preflights_passed is False
    assert plan.targets[0].executable is False

    with pytest.raises(PublicationAuthorizationError, match="preflight failed"):
        build_omnichannel_publication_plan(
            _product_package(approved=False, source_reference=None),
            _content_package(product_id="another-product"),
            site_selection={"tiktok": ("TH",)},
            execute=True,
            user_approved=True,
            confirmation_token=plan.approval.confirmation_token,
        )


def test_rejects_unknown_channels_empty_targets_and_duplicate_sites():
    with pytest.raises(ValueError, match="unsupported"):
        build_omnichannel_publication_plan(
            _product_package(), _content_package(), site_selection={"amazon": ("US",)}
        )
    with pytest.raises(ValueError, match="at least one"):
        build_omnichannel_publication_plan(
            _product_package(), _content_package(), site_selection={}
        )
    with pytest.raises(ValueError, match="duplicates"):
        build_omnichannel_publication_plan(
            _product_package(),
            _content_package(),
            site_selection={"ozon": ("RU", "ru")},
        )


def test_derived_channel_cannot_execute_without_a_tiktok_master_target():
    preview = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection={
            "miaoshou": ("COMMON",),
            "shopee": ("TH",),
            "ozon": ("RU",),
        },
    )

    assert preview.all_preflights_passed is False
    for target in preview.targets:
        if target.channel in {"shopee", "ozon"}:
            assert target.executable is False
            assert any(
                check.code == "upstream_target_selected" and not check.passed
                for check in target.preflight
            )


def test_store_level_tiktok_targets_bind_token_idempotency_and_same_country_dependencies():
    scope = {
        "miaoshou": ("COMMON",),
        "tiktok": ("LH_PH", "HB_PH", "LH_TH"),
        "shopee": ("PH", "TH", "MY"),
        "ozon": ("RU",),
    }

    plan = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection=scope,
        commercial_scope={
            "selected_store_prices": [
                {"target_key": "lh_ph", "list_price": 199},
                {"target_key": "hb_ph", "list_price": 209},
                {"target_key": "lh_th", "list_price": 159},
            ],
            "fx_snapshot": {"PHP": 0.118, "THB": 0.2218},
        },
    )

    labels = set(plan.approval.target_labels)
    assert {"tiktok:LH_PH", "tiktok:HB_PH", "tiktok:LH_TH"} <= labels
    assert len({target.idempotency_key for target in plan.targets}) == len(
        plan.targets
    )
    shopee = {
        target.site: target
        for target in plan.targets
        if target.channel == "shopee"
    }
    assert next(
        check.passed
        for check in shopee["PH"].preflight
        if check.code == "upstream_target_selected"
    )
    assert next(
        check.passed
        for check in shopee["TH"].preflight
        if check.code == "upstream_target_selected"
    )
    assert not next(
        check.passed
        for check in shopee["MY"].preflight
        if check.code == "upstream_target_selected"
    )
    assert shopee["PH"].depends_on == ("tiktok:LH_PH:verified_readback",)
    assert shopee["TH"].depends_on == ("tiktok:LH_TH:verified_readback",)
    assert next(
        target.depends_on
        for target in plan.targets
        if target.channel == "ozon"
    ) == ("tiktok:LH_PH:verified_readback",)

    other_store = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection={
            "miaoshou": ("COMMON",),
            "tiktok": ("HB_TH",),
            "shopee": ("TH",),
        },
        commercial_scope={
            "selected_store_prices": [
                {"target_key": "hb_th", "list_price": 169},
            ],
            "fx_snapshot": {"THB": 0.2218},
        },
    )
    original_store = build_omnichannel_publication_plan(
        _product_package(),
        _content_package(),
        site_selection={
            "miaoshou": ("COMMON",),
            "tiktok": ("LH_TH",),
            "shopee": ("TH",),
        },
        commercial_scope={
            "selected_store_prices": [
                {"target_key": "lh_th", "list_price": 159},
            ],
            "fx_snapshot": {"THB": 0.2218},
        },
    )
    assert (
        other_store.approval.confirmation_token
        != original_store.approval.confirmation_token
    )
    assert (
        next(
            target.idempotency_key
            for target in other_store.targets
            if target.channel == "tiktok"
        )
        != next(
            target.idempotency_key
            for target in original_store.targets
            if target.channel == "tiktok"
        )
    )


def test_orchestrator_has_no_client_database_filesystem_or_network_imports():
    import domains.channel_operations.omnichannel_orchestrator as orchestrator

    tree = ast.parse(inspect.getsource(orchestrator))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )

    forbidden = {"sqlite3", "requests", "urllib", "socket", "pathlib"}
    assert not forbidden.intersection(imports)
    assert not any(name.startswith("modules.") for name in imports)
