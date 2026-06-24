"""商品目录：TK + Shopee + Ozon 一键同步。"""

from __future__ import annotations

from typing import Callable

from core import auth
from modules.catalog.sync_progress import CatalogSyncProgress
from modules.hub.tokens import refresh_all
from modules.products import sync as tk_sync
from modules.shopee.config import ready as shopee_ready
from modules.shopee.sync import sync_all as shopee_sync_all


def run_catalog_sync(
    on_progress: Callable[[str], None] | None = None,
    on_state: Callable[[dict], None] | None = None,
    *,
    mode: str = "fast",
) -> dict:
    """mode: fast=本地缓存增量（默认） · full=强制全量刷新 API。"""
    use_cache = mode != "full"
    force_refresh = mode == "full"
    tracker = CatalogSyncProgress(on_state)

    def prog(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    out: dict = {
        "tiktok": {},
        "shopee": {},
        "ozon": {},
        "errors": [],
        "mode": mode,
    }

    tracker.start_phase("tokens", "Token：自动刷新…")
    prog("Token：自动刷新…")
    tok_result = refresh_all(on_progress=prog)
    if tok_result.get("errors"):
        out["errors"].extend(tok_result["errors"])
    tracker.set_fraction(1.0, "Token 已刷新")

    tracker.start_phase("tiktok", "TikTok：同步商品…")
    prog(f"TikTok：同步商品…（{'快速' if use_cache else '全量'}）")
    try:
        token = auth.ensure_valid_token()["access_token"]

        def tk_prog(current: int, total: int, msg: str) -> None:
            frac = (current / total) if total else 0
            tracker.set_fraction(frac, msg)
            prog(msg)

        out["tiktok"] = tk_sync.sync_all(
            access_token=token,
            fetch_images=True,
            on_progress=tk_prog,
            use_cache=use_cache,
            force_refresh=force_refresh,
        )
        tracker.set_fraction(1.0, "TikTok 同步完成")
    except Exception as e:
        out["errors"].append(f"TikTok: {e}")

    tracker.start_phase("logistics", "物流重量：扫描已完成包裹…")
    prog("物流重量：从 TikTok Fulfillment 拉取实测重量（中位数）…")
    try:
        from modules.catalog.logistics_weights import sync_logistics_weights

        def lw_prog(msg: str) -> None:
            tracker.set_fraction(0.5, msg)
            prog(msg)

        out["logistics_weights"] = sync_logistics_weights(
            on_progress=lw_prog,
            force_refresh=force_refresh,
            max_pages=40 if use_cache else 80,
            days=365,
        )
        n = int((out["logistics_weights"] or {}).get("skus") or 0)
        mk = int((out["logistics_weights"] or {}).get("match_keys") or 0)
        per = (out["logistics_weights"] or {}).get("per_region") or {}
        if per:
            prog("物流重量：" + " · ".join(f"{k} {v}" for k, v in sorted(per.items())))
        tracker.set_fraction(1.0, f"物流重量：{n} SKU / {mk} 对齐码")
    except Exception as e:
        out["errors"].append(f"物流重量: {e}")
        tracker.set_fraction(1.0, "物流重量跳过")

    if shopee_ready():
        tracker.start_phase("shopee", "Shopee：同步四国主店…")
        prog(f"Shopee：同步四国主店…（{'快速' if use_cache else '全量'}）")
        try:

            def sp_prog(current: int, total: int, msg: str) -> None:
                frac = (current / total) if total else 0
                tracker.set_fraction(frac, msg)
                prog(msg)

            out["shopee"] = shopee_sync_all(
                on_progress=sp_prog,
                use_cache=use_cache,
                force_refresh=force_refresh,
            )
            tracker.set_fraction(1.0, "Shopee 同步完成")
        except Exception as e:
            out["errors"].append(f"Shopee: {e}")
    else:
        out["shopee"] = {"skipped": True}
        tracker.start_phase("shopee", "Shopee：未启用，跳过")
        tracker.set_fraction(1.0, "Shopee 跳过")
        prog("Shopee：未启用，跳过")

    tracker.start_phase("ozon", "Ozon：API 拉取…")
    prog(f"Ozon：API 拉取…（{'快速' if use_cache else '全量'}）")
    try:
        from modules.ozon.sync import sync_catalog as ozon_sync

        def ozon_prog(msg: str) -> None:
            prog(msg)

        def ozon_frac(frac: float, msg: str) -> None:
            tracker.set_fraction(frac, msg)
            prog(msg)

        out["ozon"] = ozon_sync(
            on_progress=ozon_prog,
            on_fraction=ozon_frac,
            use_cache=use_cache,
            force_refresh=force_refresh,
        )
        cached = "（缓存）" if out["ozon"].get("cached") else ""
        tracker.set_fraction(1.0, f"Ozon 完成：{out['ozon'].get('offers', 0)} 个商品{cached}")
    except Exception as e:
        out["errors"].append(f"Ozon: {e}")

    summary_parts = []
    tk = out.get("tiktok") or {}
    if tk.get("skus"):
        hits = int(tk.get("cache_hits") or 0)
        summary_parts.append(f"TK {tk.get('skus')} SKU" + (f" 缓存{hits}" if hits else ""))
    sp = out.get("shopee") or {}
    if sp.get("skus"):
        sk = int(sp.get("shops_skipped") or 0)
        summary_parts.append(f"Shopee {sp.get('skus')} SKU" + (f" 跳过{sk}店" if sk else ""))
    oz = out.get("ozon") or {}
    if oz.get("offers"):
        summary_parts.append(f"Ozon {oz.get('offers')}" + (" 缓存" if oz.get("cached") else ""))
    lw = out.get("logistics_weights") or {}
    if lw.get("skus"):
        regions = lw.get("per_region") or {}
        reg_txt = ", ".join(f"{k}:{v}" for k, v in sorted(regions.items()) if v)
        summary_parts.append(f"重量 {lw.get('match_keys') or lw.get('skus')}码" + (f" ({reg_txt})" if reg_txt else ""))
    tracker.finish(" · ".join(summary_parts) or "同步结束")

    return out
