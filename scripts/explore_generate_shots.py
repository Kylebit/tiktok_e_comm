#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate images for selected shot_prompts.json via ToAPIs (explore)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CFG = json.loads((ROOT / "config" / "toapis.local.json").read_text(encoding="utf-8"))
KEY = CFG["api_key"]
BASE = CFG.get("base_url", "https://toapis.com/v1").rstrip("/")
PROXY = "http://127.0.0.1:10808"


def _ensure_proxy() -> None:
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.setdefault(k, PROXY)


def curl_json(method: str, path: str, payload=None, timeout: int = 180):
    url = BASE + path
    cmd = [
        "curl",
        "-s",
        "--max-time",
        str(timeout),
        "-L",
        url,
        "-H",
        f"Authorization: Bearer {KEY}",
        "-H",
        "Content-Type: application/json; charset=utf-8",
    ]
    tmp = None
    if method == "POST":
        # Write UTF-8 body to disk so Windows/curl doesn't mangle non-ASCII.
        tmp = ROOT / "outputs" / "_tmp_toapis_body.json"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        tmp.write_text(body, encoding="utf-8")
        cmd += ["-X", "POST", "--data-binary", f"@{tmp}"]
    else:
        cmd += ["-X", "GET"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return r.returncode, json.loads(r.stdout)
    except Exception:
        return r.returncode, {"_raw": (r.stdout or "")[:800], "_stderr": (r.stderr or "")[:200]}


def sanitize_prompt(s: str) -> str:
    """Drop bytes/chars that historically break toapis PG UTF8 checks."""
    repl = {
        "\u2014": "-",
        "\u2013": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u2022": "*",
        "\u00b7": "-",  # middle dot 0xb7 source of prior PG errors
        "\u00a0": " ",
        "·": "-",
    }
    out = s
    for k, v in repl.items():
        out = out.replace(k, v)
    # Keep unicode text but force valid UTF-8 roundtrip
    return out.encode("utf-8", "ignore").decode("utf-8")


def gen_one(model: str, prompt: str, size: str, ref_url: str, attempts: int = 3):
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size or "1:1",
        "resolution": "1k",
        "n": 1,
        "response_format": "url",
    }
    if ref_url.startswith("https://"):
        payload["reference_images"] = [ref_url]

    last = None
    for i in range(attempts):
        rc, body = curl_json("POST", "/images/generations", payload)
        if rc != 0 or "id" not in body:
            last = {"ok": False, "error": str(body)[:400]}
            time.sleep(6)
            continue
        tid = body["id"]
        for _ in range(50):
            rc2, b2 = curl_json("GET", f"/images/generations/{tid}", timeout=60)
            if not isinstance(b2, dict):
                time.sleep(4)
                continue
            st = b2.get("status")
            if st in ("completed", "succeeded"):
                data = (b2.get("result") or {}).get("data") or []
                urls = [d.get("url") for d in data if isinstance(d, dict) and d.get("url")]
                return {"ok": True, "task_id": tid, "urls": urls, "raw_status": st, "attempt": i + 1}
            if st in ("failed", "error"):
                last = {"ok": False, "error": str(b2)[:400], "task_id": tid, "attempt": i + 1}
                break
            time.sleep(4)
        else:
            last = {"ok": False, "error": "polling timeout", "task_id": tid}
        time.sleep(8)
    return last or {"ok": False, "error": "unknown"}


def download(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["curl", "-s", "--max-time", "90", "-L", url, "-o", str(dest)],
        capture_output=True,
    )
    return dest.is_file() and dest.stat().st_size > 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    _ensure_proxy()
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--shots",
        default=str(ROOT / "outputs/image_suite_plan/660007_my/shot_prompts.json"),
    )
    ap.add_argument("--model", default="nano_banana")
    ap.add_argument("--ids", default="", help="comma ids; default all")
    args = ap.parse_args()

    shots_path = Path(args.shots)
    bundle = json.loads(shots_path.read_text(encoding="utf-8"))
    out_dir = shots_path.parent / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)

    allow = {x.strip() for x in args.ids.split(",") if x.strip()} or None
    results = []
    for shot in bundle.get("shots") or []:
        sid = str(shot.get("id") or "")
        if allow and sid not in allow:
            continue
        print(f"\n=== generating {sid} {shot.get('title')} via {args.model} ===")
        t0 = time.time()
        g = gen_one(
            args.model,
            sanitize_prompt(str(shot.get("prompt") or "")),
            str(shot.get("aspect_ratio") or "1:1"),
            str(shot.get("reference_image_url") or ""),
        )
        elapsed = round(time.time() - t0, 1)
        row = {
            "id": sid,
            "type": shot.get("type"),
            "title": shot.get("title"),
            "focus": shot.get("focus"),
            "model": args.model,
            "elapsed_sec": elapsed,
            **g,
            "local_path": None,
        }
        if g.get("ok") and g.get("urls"):
            dest = out_dir / f"{sid}.png"
            ok = download(g["urls"][0], dest)
            if ok:
                try:
                    rel = dest.resolve().relative_to(ROOT.resolve())
                except ValueError:
                    rel = Path("outputs") / dest.name
                row["local_path"] = str(rel).replace("\\", "/")
            row["download_ok"] = ok
            print("OK", dest if ok else "download failed", "task", g.get("task_id"))
        else:
            print("FAIL", g)
        results.append(row)
        # small gap between jobs
        time.sleep(2)

    result_path = out_dir / "generation_result.json"
    # Merge into existing results when generating a subset of ids
    if allow and result_path.is_file():
        try:
            prev = json.loads(result_path.read_text(encoding="utf-8"))
            by_id = {str(r.get("id")): r for r in prev if isinstance(r, dict)}
            for r in results:
                by_id[str(r.get("id"))] = r
            # preserve prior order, append new ids at end
            order = [str(r.get("id")) for r in prev if isinstance(r, dict)]
            for rid in by_id:
                if rid not in order:
                    order.append(rid)
            results = [by_id[i] for i in order if i in by_id]
        except Exception:
            pass
    result_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved {result_path}")
    ok_n = sum(1 for r in results if r.get("ok") and r.get("local_path"))
    print(f"success {ok_n}/{len(results)}")
    return 0 if ok_n == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
