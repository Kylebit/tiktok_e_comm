"""后台拉取 TikTok 结算 CSV。"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone

_pull_lock = threading.Lock()
_pull_job: dict = {
    "running": False,
    "message": "",
    "percent": 0,
    "current_day": "",
    "error": None,
    "result": None,
}


def pull_status() -> dict:
    with _pull_lock:
        return dict(_pull_job)


def _run_pull(start: date, end: date) -> None:
    global _pull_job
    try:
        from tiktok_settlement import get_shops, load_token, pull_period

        tokens = load_token()
        access_token = tokens["access_token"]
        shops = get_shops(access_token)
        if not shops:
            raise RuntimeError("未获取到店铺")

        def on_progress(i: int, total: int, region: str) -> None:
            with _pull_lock:
                pct = int((i / total) * 90) if total else 0
                _pull_job["percent"] = pct
                _pull_job["current_day"] = f"{start.isoformat()}~{end.isoformat()}"
                _pull_job["message"] = (
                    f"拉取 {start.isoformat()} ~ {end.isoformat()} · {region} ({i + 1}/{total})…"
                )

        with _pull_lock:
            _pull_job["message"] = f"拉取 {start.isoformat()} ~ {end.isoformat()}（每国 1 份 CSV）…"
            _pull_job["percent"] = 5
            _pull_job["current_day"] = f"{start.isoformat()}~{end.isoformat()}"

        stats = pull_period(
            access_token, shops, start, end, run_profit=False, on_progress=on_progress
        )

        _pull_job.update(
            running=False,
            message=f"完成 · {len(stats)} 国 · 各国 1 份 CSV",
            percent=100,
            error=None,
            result={
                "regions": len(stats),
                "shops": len(shops),
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )
    except Exception as e:
        _pull_job.update(running=False, message="", error=str(e), result=None)


def start_pull(start: date, end: date) -> tuple[bool, str]:
    if end < start:
        return False, "结束日期不能早于开始日期"
    with _pull_lock:
        if _pull_job["running"]:
            return False, "结算拉取进行中"
        _pull_job.update(
            running=True,
            message="准备中…",
            percent=0,
            current_day="",
            error=None,
            result=None,
        )
    t = threading.Thread(target=_run_pull, args=(start, end), daemon=True)
    t.start()
    return True, f"已开始拉取 {start} ~ {end}（每国 1 份 CSV）"


def default_period() -> tuple[date, date]:
    """默认：上一个「15日~15日」结算周期。"""
    today = datetime.now(timezone.utc).date()
    if today.day >= 15:
        end = date(today.year, today.month, 15)
    else:
        prev = today.replace(day=1) - timedelta(days=1)
        end = date(prev.year, prev.month, 15)
    if end.month == 1:
        start = date(end.year - 1, 12, 15)
    else:
        start = date(end.year, end.month - 1, 15)
    return start, end
