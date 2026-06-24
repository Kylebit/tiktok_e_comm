"""1688 选品 → TikTok Shop 上传（图片 URI + 标题/描述）。"""

from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

from core import auth, shops
from core.api_client import get as api_get
from core.api_client import put as api_put
from core.api_client import sign
from core.config import ROOT, load_settings
from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import urlopen as urlopen_retry

from modules.catalog.sku_edit import _build_local_product_edit_body, _leaf_category
from modules.sourcing.image_workbench import build_description_html, get_workbench, load_state
from modules.sourcing.pipeline import load_draft, offer_dir

UPLOAD_PATH = "/product/202309/images/upload"
TITLE_MAX = 255


def _credentials() -> tuple[str, str]:
    s = load_settings()
    return s["app_key"], s["app_secret"]


def upload_product_image(
    file_path: Path,
    *,
    use_case: str = "MAIN_IMAGE",
) -> dict:
    """上传本地图片到 TikTok，返回 {uri, url}。此接口不需要 shop_cipher。"""
    if not file_path.is_file():
        raise FileNotFoundError(str(file_path))

    token = auth.access_token()
    app_key, app_secret = _credentials()
    path = UPLOAD_PATH
    params = {
        "app_key": app_key,
        "timestamp": str(int(time.time())),
    }
    params["sign"] = sign(path, params, app_secret, body="")

    mime = mimetypes.guess_type(str(file_path))[0] or "image/jpeg"
    boundary = f"----TTS{uuid.uuid4().hex[:16]}"
    file_bytes = file_path.read_bytes()
    parts: list[bytes] = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="data"; filename="{file_path.name}"\r\nContent-Type: {mime}\r\n\r\n'.encode(),
        file_bytes,
        f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="use_case"\r\n\r\n{use_case}\r\n'.encode(),
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    url = f"https://open-api.tiktokglobalshop.com{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("x-tts-access-token", token)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    try:
        with urlopen_retry(req, timeout=120, context=SSL_CTX) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"TikTok 上传 HTTP {e.code}: {err[:400]}") from e

    if result.get("code") != 0:
        raise RuntimeError(result.get("message") or str(result))
    data = result.get("data") or {}
    uri = data.get("uri") or data.get("id") or ""
    url_out = ""
    for img in (data.get("images") or []):
        url_out = (img.get("urls") or img.get("thumb_urls") or [""])[0]
        uri = uri or img.get("uri") or ""
    url_out = url_out or data.get("url") or ""
    if not uri and not url_out:
        raise RuntimeError(f"上传成功但无 uri: {json.dumps(data)[:200]}")
    return {"uri": uri, "url": url_out, "raw": data}


def _resolve_local(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / path
    if not p.is_file():
        raise FileNotFoundError(path)
    return p


def _copy_block(draft: dict | None, region: str) -> dict:
    copy = (draft or {}).get("copy") or {}
    reg = (region or "MY").upper()
    if reg == "PH":
        return (copy.get("tiktok") or {}).get("PH") or {}
    if reg == "RU":
        return copy.get("ozon") or {}
    return (copy.get("tiktok") or {}).get("MY") or copy.get("shopee") or {}


def build_publish_payload(offer_id: str, *, region: str = "MY") -> dict:
    """组装待推送 payload（本地路径，未上传）。"""
    wb = get_workbench(offer_id)
    draft = load_draft(offer_id)
    block = _copy_block(draft, region)
    return {
        "offer_id": offer_id,
        "region": region.upper(),
        "title": (block.get("title") or wb.get("title") or "")[:TITLE_MAX],
        "description_html": build_description_html(
            block.get("description_html") or "",
            [x["path"] for x in (wb.get("final") or {}).get("tiktok_description") or []],
            offer_id,
        ),
        "main_image_paths": [x["path"] for x in (wb.get("final") or {}).get("tiktok_main") or []],
        "description_image_paths": [
            x["path"] for x in (wb.get("final") or {}).get("tiktok_description") or []
        ],
    }


def publish_to_product(
    offer_id: str,
    *,
    product_id: str,
    shop_cipher: str,
    region: str = "MY",
    progress=None,
) -> dict:
    """上传图片 + 更新已有 TK 商品的标题、描述、主图。"""
    def _log(msg: str) -> None:
        if progress:
            progress(msg)

    payload = build_publish_payload(offer_id, region=region)
    if not payload["main_image_paths"]:
        raise ValueError("未选定 TK 主图，请先在工作台选用")

    token = auth.access_token()
    main_uris: list[dict] = []
    for i, rel in enumerate(payload["main_image_paths"]):
        _log(f"上传主图 {i + 1}/{len(payload['main_image_paths'])}")
        up = upload_product_image(_resolve_local(rel), use_case="MAIN_IMAGE")
        if up.get("uri"):
            main_uris.append({"uri": up["uri"]})
        time.sleep(0.35)

    desc_img_urls: list[str] = []
    for i, rel in enumerate(payload["description_image_paths"]):
        _log(f"上传详情图 {i + 1}/{len(payload['description_image_paths'])}")
        up = upload_product_image(_resolve_local(rel), use_case="DESCRIPTION_IMAGE")
        img_url = up.get("url") or up.get("uri") or ""
        if img_url:
            desc_img_urls.append(img_url)
        time.sleep(0.35)

    intro = _copy_block(load_draft(offer_id), region).get("description_html") or ""
    desc_html = build_description_html(intro, payload["description_image_paths"], offer_id)
    # 将本地 preview URL 替换为 TikTok CDN URL
    if desc_img_urls:
        blocks = [intro] if intro else []
        for u in desc_img_urls:
            blocks.append(f'<p><img src="{u}" alt="detail" style="max-width:100%"/></p>')
        desc_html = "\n".join(blocks)

    detail = api_get(
        f"/product/202309/products/{product_id}",
        token,
        {"shop_cipher": shop_cipher},
    ).get("data") or {}
    if not detail:
        raise RuntimeError("无法读取 TikTok 商品详情")

    sku_id = str((detail.get("skus") or [{}])[0].get("id") or "")
    seller_sku = str((detail.get("skus") or [{}])[0].get("seller_sku") or "")
    body = _build_local_product_edit_body(detail, sku_id, seller_sku)
    body["title"] = payload["title"] or body.get("title") or ""
    body["description"] = desc_html or body.get("description") or "<p></p>"
    if main_uris:
        body["main_images"] = main_uris

    _log("提交商品更新…")
    resp = api_put(
        f"/product/202309/products/{product_id}",
        token,
        {"shop_cipher": shop_cipher},
        body,
    )
    ok = resp.get("code") == 0
    result = {
        "ok": ok,
        "product_id": product_id,
        "shop_cipher": shop_cipher,
        "region": region.upper(),
        "title": body["title"],
        "main_images": len(main_uris),
        "description_images": len(desc_img_urls),
        "message": resp.get("message") or "",
        "raw": resp,
    }
    if not ok:
        raise RuntimeError(result["message"] or str(resp))
    return result


def export_publish_bundle(offer_id: str, *, region: str = "MY") -> Path:
    """导出 zip：主图/详情文件夹 + publish.json（供人工上传或后续 API 建品）。"""
    payload = build_publish_payload(offer_id, region=region)
    out_dir = offer_dir(offer_id) / "tk_export"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {**payload, "note": "新建商品需 Create Product API（类目/属性/仓库）；更新已有商品用 publish_to_product"}
    (out_dir / "publish.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    zip_path = offer_dir(offer_id) / f"tk_export_{region.lower()}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("publish.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for i, p in enumerate(payload["main_image_paths"], 1):
            fp = _resolve_local(p)
            zf.write(fp, f"main/{i:02d}_{fp.name}")
        for i, p in enumerate(payload["description_image_paths"], 1):
            fp = _resolve_local(p)
            zf.write(fp, f"description/{i:02d}_{fp.name}")
    return zip_path


def list_shop_options() -> list[dict]:
    token = auth.access_token()
    out = []
    for s in shops.list_shops(token):
        out.append(
            {
                "cipher": s.get("cipher") or s.get("shop_cipher") or "",
                "name": s.get("name") or "",
                "region": s.get("region") or "",
                "id": s.get("id") or "",
            }
        )
    return [x for x in out if x.get("cipher")]
