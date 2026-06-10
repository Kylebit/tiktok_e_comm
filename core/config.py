"""加载 config/settings.json（不存在则从 example 复制提示）。"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "settings.json"
EXAMPLE_PATH = ROOT / "config" / "settings.example.json"

_cache = None


def load_settings() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(
            f"未找到 {CONFIG_PATH}\n"
            f"请复制 config/settings.example.json → config/settings.json 并填写凭据"
        )
    with CONFIG_PATH.open(encoding="utf-8") as f:
        _cache = json.load(f)
    return _cache


def get(key: str, default=None):
    d = load_settings()
    for part in key.split("."):
        if not isinstance(d, dict) or part not in d:
            return default
        d = d[part]
    return d
