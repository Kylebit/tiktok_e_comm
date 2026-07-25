#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-off, auditable reference-image generation for the autumn wreath exploration.

This is intentionally not a product workflow.  It records every external action in a
standalone HTML report and only performs paid calls when --execute-paid is present.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "toapis.local.json"
OUT = ROOT / "outputs" / "wreath_exploration_20260722"
TITLE = "秋季南瓜枫叶花环门窗挂饰 感恩节庭院向日葵枫叶藤圈壁"
PRIMARY_IMAGE = Path(r"C:\Users\Windows11\Desktop\O1CN01E8AfGv2MZhBuBx3OR_!!2208436169842-0-cib.jpg")
SPEC_IMAGE = Path(r"C:\Users\Windows11\Desktop\O1CN01nEnM0K2MZhBu25LpW_!!2208436169842-0-cib.jpg")
LOCAL_PROXY = "http://127.0.0.1:10808"

COMMON_LOCK = """Use the supplied reference image as the single source of truth for the product.
Preserve the exact wreath identity: circular dark-green base, autumn maple leaves in orange,
yellow, brown and burgundy, pale blush flowers, small yellow sunflower accents, orange mini
pumpkins, white mini pumpkins, red berry clusters, and the same dense handcrafted arrangement.
Do not redesign, simplify, recolor, replace, add, or remove product components. Do not use the
alternate orange-sunflower product version shown in other source material. No words, letters,
dimensions, labels, logos, watermark, packaging, collage panels, or infographic layout."""

SHOTS = [
    {
        "id": "01_white_hero",
        "name": "白底主图",
        "size": "1:1",
        "direction": """Create a premium ecommerce hero image. Front-facing top-down product
photograph of the wreath, centered and fully visible, with the full circular silhouette inside the
frame. Pure clean white background, soft diffused studio lighting, subtle natural contact shadow,
sharp texture detail, balanced composition. No props. No seasonal background. No text.""",
    },
    {
        "id": "02_door_scene",
        "name": "门挂场景图",
        "size": "2:3",
        "direction": """Create a realistic autumn home entrance lifestyle photograph. Hang the
exact wreath at eye level in the center of a warm light-beige wooden front door. Soft
late-afternoon sunlight, modest porch setting, a few out-of-focus fallen maple leaves on the ground
only. The wreath is the clear hero, naturally sized and unobstructed. Warm, inviting Thanksgiving
and autumn atmosphere. Do not add ribbons, signs, extra pumpkins, furniture, people, animals, or
any text. Do not cover the wreath with foreground props.""",
    },
    {
        "id": "03_indoor_scene",
        "name": "室内场景图",
        "size": "2:3",
        "direction": """Create a realistic warm indoor autumn lifestyle photograph. Hang the exact
wreath above a simple light-stone fireplace mantel in a bright neutral living room. Add only subtle
autumn ambience: a small cream candle and a few unfocused dried leaves on the mantel, kept far from
the wreath. Natural window light, editorial home decor photography, vertical composition. The wreath
must remain fully visible, unobstructed, and visually identical to the supplied reference. No people,
no pets, no text, no signs, no additional wreaths, no extra pumpkins or ribbons.""",
    },
    {
        "id": "04_selling_point",
        "name": "卖点图",
        "size": "1:1",
        "direction": """Create a premium square ecommerce selling-point image. Show the complete
wreath in a three-quarter front view against a clean warm ivory studio background. Make the dense
layered autumn arrangement clearly readable: pumpkins, berries, flowers and maple leaves should all
remain crisp and recognizable. Add a restrained warm shadow and refined commercial lighting that
emphasizes depth and the rich harvest color palette. Leave clean negative space around the product for
later layout. No captions, labels, measurements, arrows, claims, text, packaging, watermark, or
separate products.""",
    },
    {
        "id": "05_macro_detail",
        "name": "细节图",
        "size": "1:1",
        "direction": """Create a premium macro product-detail photograph of the exact same wreath.
Frame a natural close-up area containing one orange mini pumpkin, one white mini pumpkin, red berry
clusters, a pale blush flower with a dark center, and layered maple leaves. Preserve the original
materials, colors, leaf vein markings, proportions and arrangement. Soft side studio light, shallow
depth of field, high-resolution tactile e-commerce detail photography. No text, labels, arrows,
measurements, watermark, packaging, or invented product components.""",
    },
    {
        "id": "06_size_card",
        "name": "尺寸图",
        "size": "1:1",
        "postprocess": "dimension_overlay",
        "direction": """Create a clean technical e-commerce base image of the exact wreath. Use a
front-facing top-down view, centered on a pure white background with generous empty margin around the
entire outer edge and a clearly visible inner opening. Keep the full wreath silhouette visible and
unchanged. Do not generate any text, numbers, arrows, dimension lines, logo, watermark, packaging or
extra props. This image will receive verified measurement graphics in a separate local step.""",
    },
]


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def clean(value: Any) -> Any:
    """Keep evidence useful while excluding credential-bearing or temporary URL details."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items() if str(k).lower() not in {"authorization", "api_key", "key"}}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str) and value.startswith("https://"):
        return value.split("?", 1)[0]
    return value


def safe_text(value: Any) -> str:
    return html.escape(json.dumps(clean(value), ensure_ascii=False, indent=2), quote=False)


def call_curl(
    config: dict[str, Any],
    method: str,
    path: str,
    *,
    body_path: Path | None = None,
    content_type: str = "application/json; charset=utf-8",
    timeout: int = 180,
) -> tuple[int, dict[str, Any]]:
    base = str(config.get("base_url") or "https://toapis.com/v1").rstrip("/")
    key = str(config["api_key"])
    command = [
        "curl", "-sS", "--max-time", str(timeout), "-L", f"{base}{path}", "-X", method,
        "-H", f"Authorization: Bearer {key}", "-H", f"Content-Type: {content_type}",
    ]
    if body_path is not None:
        command.extend(["--data-binary", f"@{body_path}"])
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    raw = completed.stdout.strip()
    try:
        response: dict[str, Any] = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        response = {"_non_json_response": raw[:2000], "_stderr": completed.stderr[:1000]}
    if completed.returncode:
        response.setdefault("_curl_returncode", completed.returncode)
        response.setdefault("_stderr", completed.stderr[:1000])
    return completed.returncode, response


def write_json_body(name: str, payload: dict[str, Any]) -> Path:
    path = OUT / "request_bodies" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def upload(config: dict[str, Any], source: Path, trace: list[dict[str, Any]]) -> str:
    mime = mimetypes.guess_type(source.name)[0] or "image/jpeg"
    boundary = f"----OrbitWreath{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{source.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8") + source.read_bytes() + f"\r\n--{boundary}--\r\n".encode("ascii")
    body_path = OUT / "request_bodies" / f"upload_{source.stem}.bin"
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_path.write_bytes(body)
    started = time.monotonic()
    code, response = call_curl(
        config, "POST", "/uploads/images", body_path=body_path,
        content_type=f"multipart/form-data; boundary={boundary}", timeout=120,
    )
    record = {
        "at": now(), "step": "upload_primary_reference", "method": "POST", "path": "/uploads/images",
        "input": {"file": source.name, "bytes": source.stat().st_size, "mime": mime},
        "curl_returncode": code, "elapsed_sec": round(time.monotonic() - started, 2), "response": clean(response),
    }
    trace.append(record)
    url = ((response.get("data") or {}).get("url")) or response.get("url")
    if code or not isinstance(url, str) or not url.startswith("https://"):
        raise RuntimeError(f"upload did not return a usable URL: {json.dumps(clean(response), ensure_ascii=False)[:800]}")
    return url


def download(url: str, destination: Path) -> tuple[bool, str]:
    if destination.exists():
        destination.unlink()
    try:
        response = requests.get(
            url,
            proxies={"http": LOCAL_PROXY, "https": LOCAL_PROXY},
            timeout=120,
        )
        response.raise_for_status()
        destination.write_bytes(response.content)
    except requests.RequestException as exc:
        return False, str(exc)[:1000]
    return destination.is_file() and destination.stat().st_size > 0, ""


def get_task_via_requests(config: dict[str, Any], task_id: str) -> tuple[int, dict[str, Any]]:
    base = str(config.get("base_url") or "https://toapis.com/v1").rstrip("/")
    try:
        response = requests.get(
            f"{base}/images/generations/{task_id}",
            headers={"Authorization": f"Bearer {config['api_key']}"},
            proxies={"http": LOCAL_PROXY, "https": LOCAL_PROXY},
            timeout=90,
        )
        try:
            data = response.json()
        except ValueError:
            data = {"_non_json_response": response.text[:2000]}
        return response.status_code, data if isinstance(data, dict) else {"_non_object": data}
    except requests.RequestException as exc:
        return 0, {"_request_error": str(exc)[:1000]}


def recover_downloads(config: dict[str, Any], trace: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    """Retry downloads for already-completed tasks without creating a new paid task."""
    for row in results:
        task_id = row.get("task_id")
        shot = row.get("shot") or {}
        if not isinstance(task_id, str):
            continue
        code, fresh = get_task_via_requests(config, task_id)
        data = ((fresh.get("result") or {}).get("data") or []) if isinstance(fresh, dict) else []
        image_url = data[0].get("url") if data and isinstance(data[0], dict) else None
        trace.append({"at": now(), "step": "recovery_get_completed_task", "shot": shot.get("id"), "method": "GET", "path": f"/images/generations/{task_id}", "curl_returncode": code, "response": clean(fresh)})
        if code != 200 or fresh.get("status") not in {"completed", "succeeded"} or not isinstance(image_url, str):
            continue
        row["final"] = clean(fresh)
        raw_destination = OUT / "generated" / f"{shot['id']}_raw.png" if shot.get("postprocess") else OUT / "generated" / f"{shot['id']}.png"
        destination = OUT / "generated" / f"{shot['id']}.png"
        ok, error = download(image_url, raw_destination)
        if ok and shot.get("postprocess") == "dimension_overlay":
            add_dimension_overlay(raw_destination, destination)
            row["raw_download_path"] = str(raw_destination.relative_to(ROOT)).replace("\\", "/")
            row["local_postprocess"] = "dimension_overlay using source-image-2 annotations: outer approx. 48cm, inner approx. 23cm, weight approx. 168g"
        row["download_path"] = str(destination.relative_to(ROOT)).replace("\\", "/") if ok else None
        row["download_error"] = error or None
        trace.append({"at": now(), "step": "recovery_download_result", "shot": shot.get("id"), "source_url": clean(image_url), "ok": ok, "destination": row["download_path"], "error": error or None})


def _font(size: int):
    for candidate in (Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\arial.ttf")):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def add_dimension_overlay(source: Path, destination: Path) -> None:
    """Add deterministic measurements sourced from the second user-provided image."""
    image = Image.open(source).convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    accent = (166, 93, 24)
    font = _font(max(22, width // 38))
    small = _font(max(17, width // 52))
    margin = max(42, width // 18)
    bottom = height - margin
    center_y = height // 2

    # Outer diameter line and arrowheads.
    draw.line((margin, bottom, width - margin, bottom), fill=accent, width=max(3, width // 400))
    draw.polygon([(margin, bottom), (margin + 18, bottom - 10), (margin + 18, bottom + 10)], fill=accent)
    draw.polygon([(width - margin, bottom), (width - margin - 18, bottom - 10), (width - margin - 18, bottom + 10)], fill=accent)
    outer = "外径约 48 cm / 18.9 in"
    box = draw.textbbox((0, 0), outer, font=font)
    draw.rectangle(((width - (box[2] - box[0])) // 2 - 10, bottom - (box[3] - box[1]) - 42, (width + (box[2] - box[0])) // 2 + 10, bottom - 20), fill=(255, 255, 255))
    draw.text(((width - (box[2] - box[0])) // 2, bottom - (box[3] - box[1]) - 32), outer, font=font, fill=accent)

    # Inner diameter guide. The position is a visual guide, not image-derived metrology.
    inner_left, inner_right = int(width * 0.40), int(width * 0.60)
    draw.line((inner_left, center_y, inner_right, center_y), fill=accent, width=max(3, width // 450))
    draw.polygon([(inner_left, center_y), (inner_left + 15, center_y - 8), (inner_left + 15, center_y + 8)], fill=accent)
    draw.polygon([(inner_right, center_y), (inner_right - 15, center_y - 8), (inner_right - 15, center_y + 8)], fill=accent)
    draw.text((inner_left, center_y - 36), "内径约 23 cm / 9 in", font=small, fill=accent)
    draw.text((margin, margin), "重量约 168 g（以实物复核为准）", font=small, fill=accent)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, quality=95)


def generate(config: dict[str, Any], shot: dict[str, str], reference_url: str, trace: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = f"{COMMON_LOCK}\n\n{shot['direction']}"
    payload = {
        "model": "gpt-image-2", "prompt": prompt, "n": 1, "size": shot["size"],
        "resolution": "1k", "response_format": "url", "reference_images": [reference_url],
        "client_business_id": f"wreath-explore-{shot['id']}",
    }
    body_path = write_json_body(shot["id"], payload)
    started = time.monotonic()
    code, created = call_curl(config, "POST", "/images/generations", body_path=body_path)
    task_id = created.get("id") if isinstance(created, dict) else None
    trace.append({
        "at": now(), "step": "create_generation", "shot": shot["id"], "method": "POST",
        "path": "/images/generations", "payload": clean(payload), "curl_returncode": code,
        "response": clean(created),
    })
    row: dict[str, Any] = {
        "shot": shot, "task_id": task_id, "created": clean(created), "status_history": [],
        "final": None, "download_path": None, "elapsed_sec": None,
    }
    if code or not isinstance(task_id, str):
        row["final"] = {"status": "not_created", "error": clean(created)}
        row["elapsed_sec"] = round(time.monotonic() - started, 2)
        return row

    deadline = time.monotonic() + 210
    while time.monotonic() < deadline:
        time.sleep(3)
        poll_code, polled = call_curl(config, "GET", f"/images/generations/{task_id}", timeout=90)
        status = polled.get("status") if isinstance(polled, dict) else None
        status_event = {"at": now(), "curl_returncode": poll_code, "status": status, "response": clean(polled)}
        row["status_history"].append(status_event)
        trace.append({"at": now(), "step": "poll_generation", "shot": shot["id"], "method": "GET", "path": f"/images/generations/{task_id}", **status_event})
        if status in {"completed", "succeeded", "failed", "error"}:
            row["final"] = clean(polled)
            break
    if row["final"] is None:
        row["final"] = {"status": "timeout", "message": "No terminal status within 210 seconds."}
    row["elapsed_sec"] = round(time.monotonic() - started, 2)

    data = ((row["final"].get("result") or {}).get("data") or []) if isinstance(row["final"], dict) else []
    image_url = data[0].get("url") if data and isinstance(data[0], dict) else None
    if row["final"].get("status") in {"completed", "succeeded"} and isinstance(image_url, str):
        raw_destination = OUT / "generated" / f"{shot['id']}_raw.png" if shot.get("postprocess") else OUT / "generated" / f"{shot['id']}.png"
        destination = OUT / "generated" / f"{shot['id']}.png"
        raw_destination.parent.mkdir(parents=True, exist_ok=True)
        ok, error = download(image_url, raw_destination)
        if ok and shot.get("postprocess") == "dimension_overlay":
            add_dimension_overlay(raw_destination, destination)
        row["download_path"] = str(destination.relative_to(ROOT)).replace("\\", "/") if ok else None
        if ok and shot.get("postprocess"):
            row["raw_download_path"] = str(raw_destination.relative_to(ROOT)).replace("\\", "/")
            row["local_postprocess"] = "dimension_overlay using source-image-2 annotations: outer approx. 48cm, inner approx. 23cm, weight approx. 168g"
        row["download_error"] = error or None
        trace.append({"at": now(), "step": "download_result", "shot": shot["id"], "source_url": clean(image_url), "ok": ok, "destination": row["download_path"], "error": error or None})
    return row


def image_html(path: Path, label: str) -> str:
    if not path.is_file():
        return f"<p class='missing'>{html.escape(label)}: unavailable</p>"
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"<figure><img src='data:{mime};base64,{encoded}' alt='{html.escape(label)}'><figcaption>{html.escape(label)}</figcaption></figure>"


def render_report(trace: list[dict[str, Any]], results: list[dict[str, Any]], uploaded_url: str | None, execution_error: str | None) -> Path:
    generated = []
    result_sections = []
    for row in results:
        shot = row["shot"]
        local = ROOT / row["download_path"] if row.get("download_path") else Path()
        image_label = f"{shot['id']} {shot['name']}"
        image_block = (
            image_html(local, image_label)
            if row.get("download_path")
            else '<p class="missing">未获得可嵌入的生成图片。</p>'
        )
        if row.get("download_path"):
            generated.append(image_html(local, image_label))
        result_sections.append(
            f"<section><h3>{html.escape(shot['id'])} {html.escape(shot['name'])}</h3>"
            f"<p>比例：<code>{html.escape(shot['size'])}</code>；任务：<code>{html.escape(str(row.get('task_id') or '未创建'))}</code>；耗时：{html.escape(str(row.get('elapsed_sec')))} 秒</p>"
            f"<h4>生成方向</h4><pre>{html.escape(shot['direction'])}</pre>"
            f"<h4>最终返回</h4><pre>{safe_text(row.get('final'))}</pre>"
            f"<h4>轮询历史</h4><pre>{safe_text(row.get('status_history'))}</pre>"
            f"{image_block}</section>"
        )
    trace_rows = "".join(
        f"<details><summary>{html.escape(item.get('at', ''))} | {html.escape(item.get('step', ''))} | {html.escape(item.get('shot', ''))}</summary><pre>{safe_text(item)}</pre></details>"
        for item in trace
    )
    error_block = f"<p class='error'>{html.escape(execution_error)}</p>" if execution_error else "<p class='ok'>流程运行完毕；结果以每张图的终态为准。</p>"
    body = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>秋季花环 ToAPI 生成探索报告</title>
<style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;max-width:1100px;margin:32px auto;padding:0 22px;line-height:1.6;color:#252525}}h1,h2,h3{{color:#733d12}}section,details{{border:1px solid #ddd;border-radius:8px;padding:16px;margin:16px 0}}figure{{margin:18px 0}}img{{max-width:100%;height:auto;border:1px solid #ddd;border-radius:6px;background:#fafafa}}pre{{white-space:pre-wrap;word-break:break-word;background:#f7f7f7;padding:12px;border-radius:6px;overflow:auto}}code{{background:#f4eee8;padding:2px 4px}}.ok{{background:#e9f7ec;padding:12px}}.error,.missing{{background:#fff0f0;padding:12px;color:#8a1d1d}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}}</style></head><body>
<h1>秋季南瓜枫叶花环：ToAPI 参考图生图探索报告</h1><p>生成时间：{html.escape(now())}</p>{error_block}
<h2>1. 输入来源</h2><table><tr><th>输入</th><th>内容与用途</th><th>是否发送给 ToAPI</th></tr>
<tr><td>标题</td><td>{html.escape(TITLE)}</td><td>是，作为人工策划依据；本轮提示词用可见外观作更严格锁定。</td></tr>
<tr><td>图 1</td><td>白底花环主图。作为唯一视觉参考，锁定产品外观。</td><td>是。先上传为 URL，再放入 <code>reference_images</code>。</td></tr>
<tr><td>图 2</td><td>尺寸和部件示意图。与图 1 存在花型差异。</td><td>否。只作人工规格核验，避免模型混合两个版本。</td></tr></table>
{image_html(PRIMARY_IMAGE, '输入图 1：主视觉参考')}{image_html(SPEC_IMAGE, '输入图 2：规格/部件人工核验')}
<h2>2. 人工事实卡与处理决策</h2><ul><li>确认：秋季环形装饰花环、深绿基底、枫叶、南瓜、红果和花朵的组合。</li><li>图 1 为产品身份基准；图 2 的外径约 48cm、内径约 23cm、重量约 168g 仅作待复核规格。</li><li>不将“环保、耐候、手工、可重复使用”等未证实说法写入提示词。</li><li>禁止模型生成尺寸、文字、品牌、包装或水印；文案应在后处理阶段叠加。</li></ul>
<h2>3. 发给 ToAPI 的方式</h2><p>模型：<code>gpt-image-2</code>。端点：<code>POST /v1/uploads/images</code> 上传主参考图，再用 <code>POST /v1/images/generations</code> 创建异步任务，使用 <code>GET /v1/images/generations/&lt;task_id&gt;</code> 轮询。鉴权凭据从未写入本报告。</p>
<p>上传后得到的参考图 URL（已移除查询参数）：<code>{html.escape((uploaded_url or '未上传').split('?', 1)[0])}</code></p>
<h2>4. 六张图的真实返回与下载结果</h2><p>尺寸图在 ToAPI 返回无文字底图后，才由本地叠加图 2 中可见的尺寸标注；其余五张没有本地改图。</p>{''.join(result_sections) or '<p>尚未创建生成任务。</p>'}
<h2>5. 全量操作追踪</h2><p>以下记录包含每次外部请求的路径、脱敏后的 payload、任务 ID、轮询状态和下载结果。不会包含 API Key 或临时 URL 查询参数。</p>{trace_rows}
</body></html>"""
    report = OUT / "wreath_toapi_exploration_report.html"
    report.write_text(body, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-paid", action="store_true", help="Permit upload and paid generation calls.")
    parser.add_argument("--recover-downloads", action="store_true", help="Retry downloads for existing completed tasks only.")
    args = parser.parse_args()
    if args.recover_downloads:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        trace_path, result_path = OUT / "trace.json", OUT / "results.json"
        if not trace_path.is_file() or not result_path.is_file():
            raise SystemExit("No prior execution record exists to recover.")
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        results = json.loads(result_path.read_text(encoding="utf-8"))
        recover_downloads(config, trace, results)
        (OUT / "trace.json").write_text(json.dumps(clean(trace), ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT / "results.json").write_text(json.dumps(clean(results), ensure_ascii=False, indent=2), encoding="utf-8")
        report = render_report(trace, results, None, None)
        print(report)
        return 0 if all(row.get("download_path") for row in results) else 1
    if not args.execute_paid:
        print("Dry safety stop: pass --execute-paid to create external tasks.")
        return 2
    if not PRIMARY_IMAGE.is_file() or not SPEC_IMAGE.is_file():
        raise SystemExit("Expected user-provided wreath input images are missing.")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "inputs").mkdir(exist_ok=True)
    shutil.copy2(PRIMARY_IMAGE, OUT / "inputs" / PRIMARY_IMAGE.name)
    shutil.copy2(SPEC_IMAGE, OUT / "inputs" / SPEC_IMAGE.name)
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    trace: list[dict[str, Any]] = [{"at": now(), "step": "local_input_capture", "title": TITLE, "primary_image": {"path": str(PRIMARY_IMAGE), "bytes": PRIMARY_IMAGE.stat().st_size}, "spec_image": {"path": str(SPEC_IMAGE), "bytes": SPEC_IMAGE.stat().st_size}, "decision": "Only the primary image will be transmitted to avoid mixing visibly different variants."}]
    results: list[dict[str, Any]] = []
    uploaded_url: str | None = None
    execution_error: str | None = None
    try:
        uploaded_url = upload(config, PRIMARY_IMAGE, trace)
        for shot in SHOTS:
            results.append(generate(config, shot, uploaded_url, trace))
    except Exception as exc:
        execution_error = f"Execution stopped: {type(exc).__name__}: {exc}"
        trace.append({"at": now(), "step": "execution_error", "error": execution_error})
    finally:
        (OUT / "trace.json").write_text(json.dumps(clean(trace), ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT / "results.json").write_text(json.dumps(clean(results), ensure_ascii=False, indent=2), encoding="utf-8")
        report = render_report(trace, results, uploaded_url, execution_error)
        print(report)
    return 1 if execution_error or any(not row.get("download_path") for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
