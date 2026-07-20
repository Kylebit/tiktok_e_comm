"""Live FX helpers for Orbit Treasury (CNY per 1 unit of foreign currency)."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.config import ROOT

from modules.sourcing.new_product_workbench import DEFAULT_FX_RATES, default_fx_rates

CACHE_PATH = ROOT / "data" / "fx_rates_cache.json"
CACHE_TTL_SEC = int(os.environ.get("ORBIT_FX_CACHE_TTL_SEC") or 600)  # 10 minutes
UA = "OrbitTreasury/1.0 (+local; exchange-rates)"
TARGET_CURRENCIES = ("PHP", "MYR", "THB", "VND", "USD")

_lock = threading.Lock()
_memory_cache: dict[str, Any] | None = None


def _now() -> float:
    return time.time()


def _read_disk_cache() -> dict[str, Any] | None:
    if not CACHE_PATH.is_file():
        return None
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("rates"), dict):
        return None
    return data


def _write_disk_cache(payload: dict[str, Any]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _http_json(url: str, *, timeout: float = 12.0, headers: dict[str, str] | None = None) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(2 * 1024 * 1024)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("unexpected FX payload")
    return data


def _to_cny_per_local(foreign_per_cny: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for code in TARGET_CURRENCIES:
        per_cny = float(foreign_per_cny.get(code) or 0)
        if per_cny <= 0:
            continue
        # Keep enough precision for VND.
        digits = 8 if code == "VND" else 6
        out[code] = round(1.0 / per_cny, digits)
    return out


def _fetch_open_er_api() -> dict[str, Any]:
    data = _http_json("https://open.er-api.com/v6/latest/CNY")
    if str(data.get("result") or "").lower() != "success":
        raise ValueError(f"open.er-api result={data.get('result')}")
    raw_rates = data.get("rates") or {}
    foreign_per_cny = {code: float(raw_rates[code]) for code in TARGET_CURRENCIES if code in raw_rates}
    rates = _to_cny_per_local(foreign_per_cny)
    if len(rates) < 3:
        raise ValueError("open.er-api missing SEA currencies")
    return {
        "provider": "open.er-api.com (ExchangeRate-API free, no key)",
        "provider_url": "https://www.exchangerate-api.com/docs/free",
        "as_of": data.get("time_last_update_utc") or data.get("time_last_update_unix"),
        "base": "CNY",
        "rates": rates,
        "quote": "CNY_per_1_foreign",
    }


def _fetch_fawaz_jsdelivr() -> dict[str, Any]:
    data = _http_json(
        "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/cny.min.json"
    )
    block = data.get("cny") or {}
    foreign_per_cny: dict[str, float] = {}
    for code in TARGET_CURRENCIES:
        key = code.lower()
        if key in block:
            foreign_per_cny[code] = float(block[key])
    rates = _to_cny_per_local(foreign_per_cny)
    if len(rates) < 3:
        raise ValueError("fawaz currency-api missing SEA currencies")
    return {
        "provider": "jsdelivr/@fawazahmed0/currency-api",
        "provider_url": "https://github.com/fawazahmed0/currency-api",
        "as_of": data.get("date"),
        "base": "CNY",
        "rates": rates,
        "quote": "CNY_per_1_foreign",
    }


def _fetch_custom_endpoint() -> dict[str, Any] | None:
    """Optional: ORBIT_FX_API_URL returning JSON with rates as foreign-per-CNY or cny_per_local.

    Env:
      ORBIT_FX_API_URL   full URL
      ORBIT_FX_API_KEY   optional Bearer / query key (sent as Authorization: Bearer ...)
      ORBIT_FX_API_MODE  foreign_per_cny (default) | cny_per_local
    """
    url = (os.environ.get("ORBIT_FX_API_URL") or "").strip()
    if not url:
        return None
    headers: dict[str, str] = {}
    key = (os.environ.get("ORBIT_FX_API_KEY") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = _http_json(url, headers=headers)
    raw = data.get("rates") or data.get("conversion_rates") or {}
    mode = (os.environ.get("ORBIT_FX_API_MODE") or "foreign_per_cny").strip().lower()
    if mode == "cny_per_local":
        rates = {
            code: float(raw[code])
            for code in TARGET_CURRENCIES
            if code in raw and float(raw[code]) > 0
        }
    else:
        foreign_per_cny = {
            code: float(raw[code])
            for code in TARGET_CURRENCIES
            if code in raw and float(raw[code]) > 0
        }
        rates = _to_cny_per_local(foreign_per_cny)
    if len(rates) < 3:
        raise ValueError("custom FX endpoint missing currencies")
    return {
        "provider": "custom ORBIT_FX_API_URL",
        "provider_url": url.split("?")[0],
        "as_of": data.get("date") or data.get("time_last_update_utc") or data.get("as_of"),
        "base": "CNY",
        "rates": rates,
        "quote": "CNY_per_1_foreign",
    }


def _fetch_live() -> dict[str, Any]:
    errors: list[str] = []
    for fetcher in (_fetch_custom_endpoint, _fetch_open_er_api, _fetch_fawaz_jsdelivr):
        try:
            payload = fetcher()
            if not payload:
                continue
            merged = default_fx_rates()
            merged.update(payload["rates"])
            payload["rates"] = {k: float(merged[k]) for k in TARGET_CURRENCIES if k in merged}
            payload["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            payload["cache_ttl_sec"] = CACHE_TTL_SEC
            return payload
        except Exception as exc:  # noqa: BLE001 - degrade across providers
            if fetcher is _fetch_custom_endpoint and not (os.environ.get("ORBIT_FX_API_URL") or "").strip():
                continue
            errors.append(f"{fetcher.__name__}: {exc}")
    raise RuntimeError("; ".join(errors) or "all FX providers failed")


def get_exchange_rates(*, force_refresh: bool = False) -> dict[str, Any]:
    """Return CNY-per-foreign rates with cache. Never raises — degrades to defaults."""
    global _memory_cache
    now = _now()
    with _lock:
        cached = _memory_cache or _read_disk_cache()
        if (
            not force_refresh
            and cached
            and isinstance(cached.get("rates"), dict)
            and now - float(cached.get("cached_at") or 0) < CACHE_TTL_SEC
        ):
            out = dict(cached)
            out["ok"] = True
            out["live"] = bool(cached.get("live", True))
            out["cached"] = True
            out["stale"] = False
            out["defaults"] = dict(DEFAULT_FX_RATES)
            return out

        try:
            live = _fetch_live()
            payload = {
                **live,
                "ok": True,
                "live": True,
                "cached": False,
                "stale": False,
                "cached_at": now,
                "defaults": dict(DEFAULT_FX_RATES),
                "error": None,
            }
            _memory_cache = payload
            _write_disk_cache(payload)
            return dict(payload)
        except Exception as exc:  # noqa: BLE001
            if cached and isinstance(cached.get("rates"), dict):
                out = dict(cached)
                out["ok"] = True
                out["live"] = bool(cached.get("live", False))
                out["cached"] = True
                out["stale"] = True
                out["error"] = str(exc)
                out["defaults"] = dict(DEFAULT_FX_RATES)
                return out
            rates = default_fx_rates()
            return {
                "ok": True,
                "live": False,
                "cached": False,
                "stale": False,
                "provider": "local-defaults",
                "provider_url": None,
                "as_of": None,
                "base": "CNY",
                "quote": "CNY_per_1_foreign",
                "rates": {k: float(rates[k]) for k in TARGET_CURRENCIES if k in rates},
                "defaults": dict(DEFAULT_FX_RATES),
                "cache_ttl_sec": CACHE_TTL_SEC,
                "fetched_at": None,
                "cached_at": now,
                "error": str(exc),
                "degraded": True,
            }
