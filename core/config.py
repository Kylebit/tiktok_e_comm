"""加载 config/settings.json（不存在则从 example 复制提示）。"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_BINDING_CONTRACT = "orbit-settings-binding/v1"
CONFIG_PATH = ROOT / "config" / "settings.json"
EXAMPLE_PATH = ROOT / "config" / "settings.example.json"
# Compatibility callers may explicitly inject fallback paths. Never guess a
# different physical checkout or capture an environment selection at import.
FALLBACK_CONFIG_PATHS = []

_cache = None
# Non-secret provenance of the loaded cache; health must never infer it from
# the next settings_path() selection or initialize configuration to obtain it.
_cache_source = None
_cache_source_stat = None
_cache_explicit_source = None


def settings_path() -> Path:
    explicit = os.environ.get("ORBIT_HIVE_SETTINGS")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            raise ValueError("EXPLICIT_SETTINGS_MUST_BE_ABSOLUTE")
        if not path.is_file():
            raise FileNotFoundError("EXPLICIT_SETTINGS_MISSING")
        selected = path.resolve()
    else:
        selected = next((path for path in [CONFIG_PATH, *[p for p in FALLBACK_CONFIG_PATHS if p]]
                         if path.is_file()), CONFIG_PATH)
    # Token/base-dir callers must reject a new selection too, before any get()
    # could combine the old settings cache with this new directory.
    if (_cache is not None and (explicit or _cache_explicit_source is not None)
            and _cache_source != selected.resolve()):
        raise RuntimeError("SETTINGS_CACHE_SOURCE_CHANGED: use a fresh process")
    return selected


def settings_base_dir() -> Path:
    path = settings_path()
    return path.parent.parent if path.parent.name == "config" else path.parent


def load_settings() -> dict:
    global _cache, _cache_source, _cache_source_stat, _cache_explicit_source
    if _cache is not None:
        settings_path()
        return _cache
    path = settings_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"未找到 {CONFIG_PATH}\n"
            f"请复制 config/settings.example.json → config/settings.json 并填写凭据"
        )
    with path.open(encoding="utf-8") as f:
        _cache = json.load(f)
    _cache_source = path.resolve()
    _cache_explicit_source = _cache_source if os.environ.get("ORBIT_HIVE_SETTINGS") else None
    stat = path.stat()
    _cache_source_stat = (stat.st_size, stat.st_mtime_ns)
    return _cache


def get(key: str, default=None):
    d = load_settings()
    for part in key.split("."):
        if not isinstance(d, dict) or part not in d:
            return default
        d = d[part]
    return d
