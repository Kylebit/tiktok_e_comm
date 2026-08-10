from __future__ import annotations

from types import SimpleNamespace

import pytest

from modules.products import server as product_server
from shared_platform import product_publication_executors as executor_mod
from shared_platform import product_publication_live_dependencies as live_mod
from shared_platform.product_publication_reports import (
    DEFAULT_PRODUCT_PUBLICATION_REPORT_ROOT,
)


def test_production_bootstrap_registers_three_independent_frozen_v4_executors(
    monkeypatch,
):
    events: list[tuple] = []

    class CheckpointStore:
        def __init__(self, root):
            events.append(("checkpoint", root))
            self.root = root

    category_resolver = object()
    transport_factory = object()
    class Preparer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    preparer = Preparer()

    monkeypatch.setattr(live_mod, "TikTokV4DraftCheckpointStore", CheckpointStore)
    monkeypatch.setattr(
        live_mod,
        "OfficialMiaoshouTikTokCategoryResolver",
        lambda: events.append(("category",)) or category_resolver,
    )
    monkeypatch.setattr(
        live_mod,
        "MiaoshouTikTokV4DraftTransportFactory",
        lambda: events.append(("transport", "default-seed-resolver"))
        or transport_factory,
    )

    def make_preparer(**kwargs):
        events.append(("preparer", kwargs))
        preparer.kwargs = kwargs
        return preparer

    monkeypatch.setattr(live_mod, "DurableTikTokV4DraftPreparer", make_preparer)

    dependencies = {
        "TIKTOK": SimpleNamespace(name="tiktok"),
        "SHOPEE": SimpleNamespace(name="shopee"),
        "OZON": SimpleNamespace(name="ozon"),
    }

    def build_tiktok(**kwargs):
        events.append(("live", "TIKTOK", kwargs))
        return dependencies["TIKTOK"]

    monkeypatch.setattr(live_mod, "build_live_tiktok_dependencies", build_tiktok)
    monkeypatch.setattr(
        live_mod,
        "build_live_shopee_dependencies",
        lambda: events.append(("live", "SHOPEE", {}))
        or dependencies["SHOPEE"],
    )
    monkeypatch.setattr(
        live_mod,
        "build_live_ozon_dependencies",
        lambda: events.append(("live", "OZON", {})) or dependencies["OZON"],
    )

    def compose(*, platform_scope, tiktok=None, shopee=None, ozon=None):
        platform = platform_scope[0]
        selected = {"TIKTOK": tiktok, "SHOPEE": shopee, "OZON": ozon}[platform]
        events.append(("compose", tuple(platform_scope), selected))
        return {platform: lambda request, value=platform: (value, request)}

    monkeypatch.setattr(
        executor_mod, "build_product_publication_platform_executors", compose
    )
    monkeypatch.setattr(product_server, "_PRODUCT_PUBLICATION_PLATFORM_EXECUTORS", {})

    registered = product_server._initialize_product_publication_platform_executors()

    assert set(registered) == {"TIKTOK", "SHOPEE", "OZON"}
    assert set(product_server._product_publication_platform_executors()) == {
        "TIKTOK",
        "SHOPEE",
        "OZON",
    }
    assert ("checkpoint", DEFAULT_PRODUCT_PUBLICATION_REPORT_ROOT) in events
    assert ("transport", "default-seed-resolver") in events
    checkpoint = preparer.kwargs["checkpoint_store"]
    assert checkpoint.root == DEFAULT_PRODUCT_PUBLICATION_REPORT_ROOT
    assert preparer.kwargs == {
        "checkpoint_store": checkpoint,
        "category_resolver": category_resolver,
        "transport_factory": transport_factory,
    }
    tiktok_live = next(
        row for row in events if row[0] == "live" and row[1] == "TIKTOK"
    )[2]
    assert tiktok_live == {
        "draft_preparer": preparer,
        "category_resolver": category_resolver,
    }
    assert [row[1] for row in events if row[0] == "compose"] == [
        ("TIKTOK",),
        ("SHOPEE",),
        ("OZON",),
    ]


def test_production_bootstrap_keeps_other_platforms_when_one_composition_fails(
    monkeypatch, caplog
):
    monkeypatch.setattr(
        live_mod,
        "TikTokV4DraftCheckpointStore",
        lambda _root: object(),
    )
    monkeypatch.setattr(
        live_mod, "OfficialMiaoshouTikTokCategoryResolver", lambda: object()
    )
    monkeypatch.setattr(
        live_mod, "MiaoshouTikTokV4DraftTransportFactory", lambda: object()
    )
    monkeypatch.setattr(
        live_mod, "DurableTikTokV4DraftPreparer", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        live_mod, "build_live_tiktok_dependencies", lambda **_kwargs: "tiktok"
    )
    monkeypatch.setattr(
        live_mod,
        "build_live_shopee_dependencies",
        lambda: (_ for _ in ()).throw(RuntimeError("secret provider detail")),
    )
    monkeypatch.setattr(
        live_mod, "build_live_ozon_dependencies", lambda: "ozon"
    )

    def compose(*, platform_scope, **_kwargs):
        platform = platform_scope[0]
        return {platform: lambda _request: platform}

    monkeypatch.setattr(
        executor_mod, "build_product_publication_platform_executors", compose
    )
    monkeypatch.setattr(product_server, "_PRODUCT_PUBLICATION_PLATFORM_EXECUTORS", {})

    registered = product_server._initialize_product_publication_platform_executors()

    assert set(registered) == {"TIKTOK", "OZON"}
    assert "secret provider detail" not in caplog.text
    assert "SHOPEE" in caplog.text
    assert "RuntimeError" in caplog.text


def test_serve_initializes_publication_executors_before_listening(
    tmp_path, monkeypatch
):
    events: list[str] = []

    class StopServe(RuntimeError):
        pass

    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "costs.html").write_text("ready", encoding="utf-8")
    monkeypatch.setattr(product_server, "WEB_DIR", web_dir)
    monkeypatch.setattr(
        product_server,
        "_initialize_product_publication_platform_executors",
        lambda: events.append("initialize") or {},
    )

    def stop_listen(*_args, **_kwargs):
        events.append("listen")
        raise StopServe

    monkeypatch.setattr(product_server, "ThreadingHTTPServer", stop_listen)

    with pytest.raises(StopServe):
        product_server.serve(open_browser=False, startup_refresh=False)

    assert events == ["initialize", "listen"]
