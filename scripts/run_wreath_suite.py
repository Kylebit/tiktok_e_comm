#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run full suite pipeline for autumn wreath product (upload -> plan -> shots -> gen)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.sourcing.image_shot_prompts import (  # noqa: E402
    build_shot_prompts,
    save_shot_prompts,
)
from modules.sourcing.image_suite_plan import (  # noqa: E402
    analyze_and_plan_suite,
    save_plan,
)
# Import generation helpers without requiring scripts as a package
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "explore_generate_shots",
    ROOT / "scripts" / "explore_generate_shots.py",
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)
download = _mod.download
gen_one = _mod.gen_one
sanitize_prompt = _mod.sanitize_prompt

OUT = ROOT / "outputs" / "image_suite_plan" / "wreath_autumn"
SRC = OUT / "source"
CFG = json.loads((ROOT / "config" / "toapis.local.json").read_text(encoding="utf-8"))
KEY = CFG["api_key"]
BASE = CFG.get("base_url", "https://toapis.com/v1").rstrip("/")
PROXY = "http://127.0.0.1:10808"
TITLE = "秋季南瓜枫叶花环门窗挂饰 感恩节庭院向日葵枫叶藤圈壁"


def ensure_proxy() -> None:
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.setdefault(k, PROXY)


def upload_image(path: Path) -> str:
    boundary = f"----Orbit{uuid.uuid4().hex}"
    data = path.read_bytes()
    mime = "image/png"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("ascii")
    tmp = OUT / f"_upload_{path.stem}.bin"
    tmp.write_bytes(body)
    cmd = [
        "curl",
        "-s",
        "--max-time",
        "120",
        "-L",
        f"{BASE}/uploads/images",
        "-H",
        f"Authorization: Bearer {KEY}",
        "-H",
        f"Content-Type: multipart/form-data; boundary={boundary}",
        "--data-binary",
        f"@{tmp}",
    ]
    r = subprocess.run(cmd, capture_output=True)
    text = r.stdout.decode("utf-8", errors="replace")
    try:
        obj = json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"upload non-json: {text[:400]}") from exc
    url = ((obj.get("data") or {}).get("url")) or obj.get("url")
    if not url:
        raise RuntimeError(f"upload failed: {text[:500]}")
    print("uploaded", path.name, "->", url)
    return str(url)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ensure_proxy()
    OUT.mkdir(parents=True, exist_ok=True)

    hero = SRC / "hero_white.png"
    parts = SRC / "parts_detail.png"
    if not hero.is_file() or not parts.is_file():
        raise SystemExit("missing source images")

    print("=== 1) upload source images ===")
    hero_url = upload_image(hero)
    parts_url = upload_image(parts)
    (OUT / "source_urls.json").write_text(
        json.dumps({"hero_white": hero_url, "parts_detail": parts_url, "title": TITLE}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("=== 2) analyze + suite plan (vision) ===")
    # Use parts_detail for richer understanding (components + dims), title for context
    plan = analyze_and_plan_suite(
        parts_url,
        title=TITLE,
        model="gemini-3-pro-official",
        max_tokens=6000,
    )
    # Prefer clean hero as generation reference
    plan.setdefault("_meta", {})
    plan["_meta"]["image_url"] = hero_url
    plan["_meta"]["parts_detail_url"] = parts_url
    plan["_meta"]["title"] = TITLE
    plan["_meta"]["analyze_image_url"] = parts_url
    save_plan(plan, OUT)
    print(plan.get("suite", {}).get("summary"))

    print("=== 3) shot prompts ===")
    bundle = build_shot_prompts(plan)
    # Force reference to clean hero for generation consistency
    for sh in bundle["shots"]:
        sh["reference_image_url"] = hero_url
    bundle["reference_image_url"] = hero_url
    save_shot_prompts(bundle, OUT)
    print("shots", bundle["count"])

    print("=== 4) generate images ===")
    gen_dir = OUT / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for shot in bundle["shots"]:
        sid = shot["id"]
        print(f"\n--- gen {sid} {shot.get('title')} ---")
        t0 = time.time()
        g = gen_one(
            "nano_banana",
            sanitize_prompt(str(shot.get("prompt") or "")),
            str(shot.get("aspect_ratio") or "1:1"),
            hero_url,
        )
        elapsed = round(time.time() - t0, 1)
        row = {
            "id": sid,
            "type": shot.get("type"),
            "title": shot.get("title"),
            "focus": shot.get("focus"),
            "model": "nano_banana",
            "elapsed_sec": elapsed,
            **g,
            "local_path": None,
        }
        if g.get("ok") and g.get("urls"):
            dest = gen_dir / f"{sid}.png"
            ok = download(g["urls"][0], dest)
            row["local_path"] = str(dest.relative_to(ROOT)).replace("\\", "/") if ok else None
            row["download_ok"] = ok
            print("OK" if ok else "DL FAIL", dest)
        else:
            print("FAIL", g)
        results.append(row)
        time.sleep(2)

    (gen_dir / "generation_result.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ok_n = sum(1 for r in results if r.get("ok") and r.get("local_path"))
    print(f"\ndone {ok_n}/{len(results)}")
    return 0 if ok_n == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
