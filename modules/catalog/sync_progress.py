"""商品目录全量同步进度（0–100%）。"""

from __future__ import annotations

from typing import Callable

# 各阶段权重（合计 100）
_WEIGHTS = {
    "tokens": 5,
    "tiktok": 42,
    "logistics": 8,
    "shopee": 30,
    "ozon": 15,
}


class CatalogSyncProgress:
    def __init__(self, on_update: Callable[[dict], None] | None = None):
        self._on_update = on_update
        self._phase = ""
        self._sub = 0.0

    def _emit(self, message: str) -> None:
        if not self._on_update:
            return
        done = 0
        for name, w in _WEIGHTS.items():
            if name == self._phase:
                done += w * max(0.0, min(1.0, self._sub))
                break
            done += w
        pct = 100 if self._phase == "done" else min(99, int(done))
        self._on_update(
            {
                "percent": pct,
                "message": message,
                "phase": self._phase,
            }
        )

    def start_phase(self, phase: str, message: str) -> None:
        self._phase = phase
        self._sub = 0.0
        self._emit(message)

    def set_fraction(self, fraction: float, message: str) -> None:
        self._sub = fraction
        self._emit(message)

    def finish(self, message: str) -> None:
        self._phase = "done"
        self._sub = 1.0
        if self._on_update:
            self._on_update({"percent": 100, "message": message, "phase": "done"})
