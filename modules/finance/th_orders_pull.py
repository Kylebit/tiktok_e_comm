# -*- coding: utf-8 -*-
"""TH 试验站：TikTok + Shopee 订单/结算定期 API 拉取。

TikTok → Finance statements → CURSOR/Income_Data/income_TH_*.csv
Shopee → escrow list/detail → outputs/weekly_shopee_profit_*.html

调度：serve 启动后按 settings.orders_pull.interval_hours 后台循环；
也可手动 POST /api/orders-pull 或 CLI scripts/pull_th_orders.py。
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from core.config import get

_job_lock = threading.Lock()
_job: dict[str, Any] = {
    "running": False,
    "message": "",
    "percent": 0,
    "error": None,
    "result": None,
    "started_at": None,
    "finished_at": None,
    "last_success_at": None,
}

_sched_lock = threading.Lock()
_sched_started = False
_sched_stop = threading.Event()


def pull_status() -> dict[str, Any]:
    with _job_lock:
        st = dict(_job)
    cfg = _cfg()
    st["config"] = cfg
    st["scheduler_running"] = _sched_started and not _sched_stop.is_set()
    return st


def _cfg() -> dict[str, Any]:
    raw = get("orders_pull") or {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "interval_hours": float(raw.get("interval_hours") or 6),
        "lookback_days": int(raw.get("lookback_days") or 14),
        "platforms": list(raw.get("platforms") or ["tiktok", "shopee"]),
        "regions": list(raw.get("regions") or ["TH"]),
        "run_on_startup": bool(raw.get("run_on_startup", False)),
    }


def _set_job(**kwargs: Any) -> None:
    with _job_lock:
        _job.update(kwargs)


def _pull_tiktok(start: date, end: date, regions: list[str]) -> dict[str, Any]:
    from tiktok_settlement import get_shops, load_token, pull_period

    tokens = load_token()
    access_token = tokens["access_token"]
    shops = get_shops(access_token)
    wanted = {r.upper() for r in regions}
    filtered = [s for s in shops if str(s.get("region") or "").upper() in wanted]
    if not filtered:
        raise RuntimeError(f"TikTok 无匹配店铺: {sorted(wanted)}")

    def on_progress(i: int, total: int, region: str) -> None:
        pct = 10 + int((i / max(total, 1)) * 40)
        _set_job(percent=pct, message=f"TikTok 拉取 {region} ({i + 1}/{total})…")

    stats = pull_period(
        access_token,
        filtered,
        start,
        end,
        run_profit=False,
        on_progress=on_progress,
    )
    return {
        "ok": True,
        "platform": "tiktok",
        "regions": [s.get("region") for s in stats],
        "shop_count": len(filtered),
        "stats": [
            {
                "region": s.get("region"),
                "total_settlement": s.get("total_settlement"),
                "currency": s.get("currency"),
                "rows": s.get("rows"),
            }
            for s in stats
        ],
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def _pull_shopee(start: date, end: date, regions: list[str]) -> dict[str, Any]:
    from modules.shopee import orders_pull as sp_pull

    results = []
    for idx, region in enumerate(regions):
        def on_progress(sn: str, i: int, total: int, reg=region, base=idx) -> None:
            pct = 55 + int(((base + i / max(total, 1)) / max(len(regions), 1)) * 40)
            _set_job(percent=min(pct, 95), message=f"Shopee {reg} escrow {i}/{total}…")

        results.append(
            sp_pull.pull_region(region, start=start, end=end, on_progress=on_progress)
        )
    return {
        "ok": True,
        "platform": "shopee",
        "results": results,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def run_pull(
    *,
    platforms: list[str] | None = None,
    regions: list[str] | None = None,
    lookback_days: int | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """同步执行一轮拉取（供 CLI / 调度线程调用）。"""
    cfg = _cfg()
    plats = [p.lower() for p in (platforms or cfg["platforms"])]
    regs = [r.upper() for r in (regions or cfg["regions"])]
    end = end or datetime.now(timezone.utc).date()
    lb = int(lookback_days if lookback_days is not None else cfg["lookback_days"])
    start = start or (end - timedelta(days=max(lb, 1) - 1))

    out: dict[str, Any] = {
        "ok": True,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "platforms": {},
        "partial": False,
    }
    errors: list[str] = []

    if any(p in ("tiktok", "tk", "both", "all") for p in plats):
        _set_job(message="TikTok Finance 拉取中…", percent=8)
        try:
            out["platforms"]["tiktok"] = _pull_tiktok(start, end, regs)
        except Exception as exc:  # noqa: BLE001
            out["platforms"]["tiktok"] = {"ok": False, "error": str(exc)}
            errors.append(f"tiktok: {exc}")

    if any(p in ("shopee", "sp", "both", "all") for p in plats):
        _set_job(message="Shopee escrow 拉取中…", percent=55)
        try:
            out["platforms"]["shopee"] = _pull_shopee(start, end, regs)
        except Exception as exc:  # noqa: BLE001
            out["platforms"]["shopee"] = {"ok": False, "error": str(exc)}
            errors.append(f"shopee: {exc}")

    oks = [bool(p.get("ok")) for p in out["platforms"].values()]
    out["ok"] = bool(oks) and any(oks)
    out["partial"] = bool(oks) and (not all(oks))
    if errors:
        out["errors"] = errors
    if not out["ok"]:
        out["error"] = "; ".join(errors) or "拉取失败"
    return out


def start_pull(
    *,
    platforms: list[str] | None = None,
    regions: list[str] | None = None,
    lookback_days: int | None = None,
    start: date | None = None,
    end: date | None = None,
) -> tuple[bool, str]:
    with _job_lock:
        if _job["running"]:
            return False, "订单拉取进行中"
        _job.update(
            running=True,
            message="准备中…",
            percent=0,
            error=None,
            result=None,
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=None,
        )

    def _worker() -> None:
        try:
            result = run_pull(
                platforms=platforms,
                regions=regions,
                lookback_days=lookback_days,
                start=start,
                end=end,
            )
            now = datetime.now(timezone.utc).isoformat()
            _set_job(
                running=False,
                message="完成" if result.get("ok") else "失败",
                percent=100 if result.get("ok") else 0,
                error=None if result.get("ok") else (result.get("error") or "失败"),
                result=result,
                finished_at=now,
                last_success_at=now if result.get("ok") else _job.get("last_success_at"),
            )
        except Exception as exc:  # noqa: BLE001
            _set_job(
                running=False,
                message="",
                percent=0,
                error=str(exc),
                result=None,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )

    threading.Thread(target=_worker, daemon=True).start()
    return True, "已开始拉取 TikTok/Shopee 最新结算订单"


def start_scheduler() -> None:
    """serve 进程内定时拉取。"""
    global _sched_started
    cfg = _cfg()
    if not cfg["enabled"]:
        print("  [orders-pull] 调度未启用（settings.orders_pull.enabled=false）")
        return
    with _sched_lock:
        if _sched_started:
            return
        _sched_started = True
        _sched_stop.clear()

    interval = max(float(cfg["interval_hours"]), 0.25) * 3600

    def _loop() -> None:
        print(
            f"  [orders-pull] 调度已启动：每 {cfg['interval_hours']}h · "
            f"回看 {cfg['lookback_days']} 天 · 平台 {cfg['platforms']} · 区域 {cfg['regions']}"
        )
        if cfg.get("run_on_startup"):
            time.sleep(8)
            if not _sched_stop.is_set():
                ok, msg = start_pull()
                print(f"  [orders-pull] 启动拉取: {msg}" if ok else f"  [orders-pull] {msg}")
        while not _sched_stop.wait(interval):
            with _job_lock:
                busy = _job["running"]
            if busy:
                print("  [orders-pull] 跳过本轮：上一任务仍在运行")
                continue
            ok, msg = start_pull()
            print(f"  [orders-pull] 定时拉取: {msg}" if ok else f"  [orders-pull] {msg}")

    threading.Thread(target=_loop, name="orders-pull-scheduler", daemon=True).start()
