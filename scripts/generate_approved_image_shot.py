#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Execute one approved ToAPIs image shot and save an auditable Chinese report.

The paid action is deliberately blocked unless --execute-paid is present.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.sourcing.image_shot_prompts import english_dimension_label


PROXY = "http://127.0.0.1:10808"


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def clean(value: Any) -> Any:
    """Remove credentials and query signatures before saving audit records."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items() if str(k).lower() not in {"authorization", "api_key", "key"}}
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, str) and value.startswith("https://"):
        parsed = urlsplit(value)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return value


def curl_json(config: dict[str, Any], method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 180) -> tuple[int, dict[str, Any]]:
    base = str(config.get("base_url") or "https://toapis.com/v1").rstrip("/")
    key = str(config["api_key"])
    command = [
        "curl", "-sS", "--max-time", str(timeout), "-L", f"{base}{path}", "-X", method,
        "-H", f"Authorization: Bearer {key}", "-H", "Content-Type: application/json; charset=utf-8",
    ]
    temp_path: Path | None = None
    if payload is not None:
        temp_path = ROOT / "outputs" / "_tmp_toapis_body.json"
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        command.extend(["--data-binary", f"@{temp_path}"])
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
    try:
        data = json.loads(completed.stdout) if completed.stdout.strip() else {}
    except json.JSONDecodeError:
        data = {"_non_json_response": completed.stdout[:1000], "_stderr": completed.stderr[:1000]}
    if completed.returncode:
        data.setdefault("_curl_returncode", completed.returncode)
        data.setdefault("_stderr", completed.stderr[:1000])
    return completed.returncode, data


def download(url: str, destination: Path) -> tuple[bool, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = ["curl", "-sS", "--max-time", "120", "-L", url, "-o", str(destination)]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode or not destination.is_file() or destination.stat().st_size == 0:
        return False, (completed.stderr or "empty download")[:1000]
    try:
        with Image.open(destination) as image:
            image.verify()
        with Image.open(destination) as image:
            return True, f"verified image: {image.format} {image.width}x{image.height}, {destination.stat().st_size} bytes"
    except Exception as exc:
        return False, f"downloaded file did not verify as an image: {exc}"


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in (Path("C:/Windows/Fonts/arialbd.ttf"), Path("C:/Windows/Fonts/arial.ttf")):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def apply_size_overlay(path: Path, dimensions: str) -> str:
    label = english_dimension_label(dimensions)
    if not label:
        raise ValueError("confirmed dimensions are required for a size card")
    backup = path.with_name(path.stem + "_model" + path.suffix)
    shutil.copy2(path, backup)
    with Image.open(path) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    width, height = image.size
    band_height = max(96, int(height * 0.14))
    draw.rectangle((0, height - band_height, width, height), fill=(255, 255, 255, 238))
    font = _font(max(26, int(width * 0.045)))
    box = draw.textbbox((0, 0), label, font=font)
    text_width = box[2] - box[0]
    text_height = box[3] - box[1]
    draw.text(((width - text_width) / 2, height - band_height + (band_height - text_height) / 2 - box[1]), label, fill=(24, 38, 51, 255), font=font)
    image.save(path, format="PNG", optimize=True)
    return label


def upload_image(config: dict[str, Any], path: Path) -> str:
    base = str(config.get("base_url") or "https://toapis.com/v1").rstrip("/")
    command = [
        "curl", "-sS", "--max-time", "180", "-L", f"{base}/uploads/images",
        "-H", f"Authorization: Bearer {config['api_key']}", "-F", f"file=@{path}",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    data = json.loads(completed.stdout) if completed.stdout.strip() else {}
    url = str((data.get("data") or {}).get("url") or "")
    if completed.returncode or not data.get("success") or not url.startswith("https://"):
        raise RuntimeError((completed.stderr or data.get("message") or "ToAPI image upload failed")[:1000])
    return url


def embed_image(path: Path) -> str:
    if not path.is_file():
        return "<p class='missing'>生成图片未能下载或验证。</p>"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"<img class='generated' src='data:image/png;base64,{encoded}' alt='生成的白底主图'>"


def render_report(out_dir: Path, shot: dict[str, Any], audit: dict[str, Any], artifact_id: str) -> Path:
    result_path = out_dir / "generated" / f"{artifact_id}.png"
    status_rows = "".join(
        f"<li>{html.escape(str(row.get('at')))}：{html.escape(str(row.get('status')))}</li>"
        for row in audit.get("status_history") or []
    ) or "<li>没有获得轮询状态。</li>"
    payload = html.escape(json.dumps(audit.get("payload"), ensure_ascii=False, indent=2))
    final = html.escape(json.dumps(audit.get("final_response"), ensure_ascii=False, indent=2))
    english_prompt = html.escape(str(audit.get("payload", {}).get("prompt") or ""))
    body = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>白底主图生成审计 - {html.escape(str(shot.get('id')))}</title><style>
body{{max-width:1060px;margin:30px auto;padding:0 20px;font-family:'Microsoft YaHei','Segoe UI',Arial,sans-serif;color:#17232d;line-height:1.65;background:#f5f7f8}}section{{background:#fff;border:1px solid #d8dfe4;padding:18px;margin:16px 0}}h1,h2{{color:#095f8b}}.ok{{color:#176b4d;font-weight:700}}.warn{{background:#fff4dc;border-left:4px solid #9a6100;padding:12px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f7;padding:12px;font:12px/1.55 Consolas,monospace}}.generated{{display:block;max-width:640px;width:100%;background:#fff;border:1px solid #d8dfe4}}details{{margin-top:14px}}code{{background:#edf2f5;padding:2px 4px}}
</style></head><body><h1>白底主图：真实生成审计</h1><p>采集箱 ID：<code>{html.escape(str(audit['collect_box_id']))}</code>｜分镜：<code>{html.escape(str(shot.get('id')))}</code>｜创建时间：{html.escape(str(audit['created_at']))}</p>
<section><h2>结果</h2><p class='{ 'ok' if audit.get('download_verified') else 'warn' }'>{html.escape(str(audit.get('result_summary') or ''))}</p>{embed_image(result_path)}</section>
<section><h2>本次批准的范围</h2><p>仅执行白底主图。没有创建客厅、卧室、卖点、细节或尺寸图任务；它们仍需等白底图身份审核通过后才可执行。</p><p>使用第 1 张妙手来源图作为唯一的商品身份参考。未上传或混用其余 5 张来源图。</p></section>
<section><h2>任务与下载核验</h2><p>ToAPI 任务 ID：<code>{html.escape(str(audit.get('task_id') or '未创建'))}</code></p><p>下载核验：{html.escape(str(audit.get('download_note') or '未下载'))}</p><h3>轮询状态</h3><ul>{status_rows}</ul></section>
<section><h2>中文执行说明</h2><p>生成电商白底主图：商品完整居中、纯白无缝背景、柔和棚拍光线、无道具、无文字。严格保留来源图中热带植物墙贴的平面形态、图案、配色、切边排版和比例；不得变成装饰画、门贴、立体摆件或另一款植物图案。</p><p class='warn'>这张图用于核对商品身份。未通过人工核对前，不生成其余套图；尺寸、箭头和英文卖点仍由后期确定性叠加。</p></section>
<details><summary>实际英文 Prompt（审计用）</summary><pre>{english_prompt}</pre></details><details><summary>脱敏后的真实请求 payload</summary><pre>{payload}</pre></details><details><summary>脱敏后的最终 API 返回</summary><pre>{final}</pre></details>
</body></html>"""
    report = out_dir / f"generation_report_{artifact_id}.html"
    report.write_text(body, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute one approved ToAPIs image shot with audit output.")
    parser.add_argument("--shots", help="shot_prompts.json path")
    parser.add_argument("--payload-file", help="runtime JSON containing one exact preflight payload")
    parser.add_argument("--id", help="approved shot ID")
    parser.add_argument("--execute-paid", action="store_true", help="permit a paid ToAPIs generation request")
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--artifact-id", default="", help="distinct local artifact ID for an audited retry")
    args = parser.parse_args()
    if not args.execute_paid:
        raise SystemExit("Refusing to create a paid task without --execute-paid")

    os.environ.setdefault("HTTP_PROXY", PROXY)
    os.environ.setdefault("HTTPS_PROXY", PROXY)
    if bool(args.shots) == bool(args.payload_file):
        raise SystemExit("provide exactly one of --shots or --payload-file")
    if args.payload_file:
        runtime_path = Path(args.payload_file)
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        shot = runtime.get("shot") if isinstance(runtime.get("shot"), dict) else {}
        payload = runtime.get("payload") if isinstance(runtime.get("payload"), dict) else {}
        if not shot or not payload:
            raise SystemExit("payload-file must contain shot and payload objects")
        out_dir = runtime_path.parent
        collect_box_id = str(runtime.get("collect_box_id") or out_dir.name)
        shot_id = str(shot.get("id") or "")
    else:
        shots_path = Path(args.shots)
        bundle = json.loads(shots_path.read_text(encoding="utf-8"))
        shot = next((row for row in bundle.get("shots") or [] if str(row.get("id") or "") == args.id), None)
        if not isinstance(shot, dict):
            raise SystemExit(f"approved shot {args.id!r} not found")
        reference = str(shot.get("reference_image_url") or "")
        if not reference.startswith("https://"):
            raise SystemExit("shot has no public HTTPS identity reference")
        payload = {
            "model": args.model,
            "prompt": str(shot.get("prompt") or ""),
            "n": 1,
            "size": str(shot.get("aspect_ratio") or "1:1"),
            "resolution": "1k",
            "response_format": "url",
            "reference_images": [reference],
            "client_business_id": f"miaoshou-{args.id}-{int(time.time())}",
        }
        out_dir = shots_path.parent
        collect_box_id = out_dir.name
        shot_id = str(shot.get("id") or args.id or "")
    if not shot_id:
        raise SystemExit("shot ID is required")
    artifact_id = str(args.artifact_id or shot_id).strip()
    if not artifact_id.replace("_", "").replace("-", "").isalnum():
        raise SystemExit("artifact-id may contain only letters, digits, hyphens, and underscores")

    config = json.loads((ROOT / "config" / "toapis.local.json").read_text(encoding="utf-8"))
    audit: dict[str, Any] = {
        "created_at": now(),
        "mode": "paid_generation_authorized_by_user",
        "collect_box_id": collect_box_id,
        "shot_id": shot_id,
        "payload": clean(payload),
        "task_id": None,
        "create_response": None,
        "status_history": [],
        "final_response": None,
        "download_verified": False,
        "download_note": "",
    }
    code, created = curl_json(config, "POST", "/images/generations", payload)
    audit["create_response"] = clean(created)
    task_id = created.get("id") if isinstance(created, dict) else None
    audit["task_id"] = task_id
    if code or not isinstance(task_id, str):
        audit["result_summary"] = "任务未创建；详情见脱敏返回。"
        audit["final_response"] = clean(created)
    else:
        deadline = time.monotonic() + 240
        final: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            time.sleep(3)
            poll_code, polled = curl_json(config, "GET", f"/images/generations/{task_id}", timeout=90)
            status = polled.get("status") if isinstance(polled, dict) else None
            audit["status_history"].append({"at": now(), "status": status, "curl_returncode": poll_code, "response": clean(polled)})
            if status in {"completed", "succeeded", "failed", "error"}:
                final = polled
                break
        audit["final_response"] = clean(final or {"status": "timeout"})
        data = ((final or {}).get("result") or {}).get("data") or []
        url = data[0].get("url") if data and isinstance(data[0], dict) else ""
        if (final or {}).get("status") in {"completed", "succeeded"} and isinstance(url, str):
            generated_path = out_dir / "generated" / f"{artifact_id}.png"
            ok, note = download(url, generated_path)
            if ok and str(shot.get("type") or "") == "size_card":
                try:
                    label = apply_size_overlay(generated_path, str(shot.get("human_dimensions") or ""))
                    final_url = upload_image(config, generated_path)
                    audit["model_output_url"] = clean(url)
                    audit["postprocess"] = {"type": "deterministic_english_size_overlay", "label": label, "uploaded": True}
                    if data and isinstance(data[0], dict):
                        data[0]["url"] = final_url
                    audit["final_response"] = clean(final)
                    note += f"; deterministic English size overlay applied: {label}"
                except Exception as exc:
                    ok = False
                    note += f"; size-card post-processing failed: {exc}"
            audit["download_verified"] = ok
            audit["download_note"] = note
            audit["result_summary"] = "任务已完成，图片已下载并通过本地文件核验。" if ok else "任务已完成，但本地下载或图片核验失败。"
        else:
            audit["result_summary"] = "任务没有成功完成；未把图片作为生成成功处理。"
    audit_path = out_dir / f"generation_audit_{artifact_id}.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    report = render_report(out_dir, shot, audit, artifact_id)
    print(json.dumps({
        "task_id": audit.get("task_id"), "download_verified": audit.get("download_verified"),
        "result_summary": audit.get("result_summary"), "audit": str(audit_path), "report": str(report),
    }, ensure_ascii=False, indent=2))
    return 0 if audit.get("download_verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
