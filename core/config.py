"""加载 config/settings.json（不存在则从 example 复制提示）。"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "settings.json"
EXAMPLE_PATH = ROOT / "config" / "settings.example.json"
FALLBACK_CONFIG_PATHS = [
    Path(os.environ["ORBIT_HIVE_SETTINGS"]) if os.environ.get("ORBIT_HIVE_SETTINGS") else None,
    Path(r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm\config\settings.json"),
]

_cache = None


def settings_path() -> Path:
    for path in [CONFIG_PATH, *[p for p in FALLBACK_CONFIG_PATHS if p]]:
        if path.is_file():
            return path
    return CONFIG_PATH


def settings_base_dir() -> Path:
    path = settings_path()
    return path.parent.parent if path.parent.name == "config" else path.parent


def load_settings() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    path = settings_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"未找到 {CONFIG_PATH}\n"
            f"请复制 config/settings.example.json → config/settings.json 并填写凭据"
        )
    with path.open(encoding="utf-8") as f:
        _cache = json.load(f)
    return _cache


def get(key: str, default=None):
    d = load_settings()
    for part in key.split("."):
        if not isinstance(d, dict) or part not in d:
            return default
        d = d[part]
    return d
