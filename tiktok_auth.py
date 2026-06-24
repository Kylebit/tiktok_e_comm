"""
TikTok Shop OAuth 授权脚本
运行方式: python3 tiktok_auth.py  或  python3 main.py auth

注意：浏览器授权 URL 里的 service_id ≠ app_key（常见误配导致「此服务不存在」）。
service_id 在 Partner Center → App 详情 → 应用名称下方的 ID，或点「复制授权链接」。
"""

from __future__ import annotations

import json
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.config import load_settings

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

AUTH_PAGE = "https://services.tiktokshop.com/open/authorize"
TOKEN_FILE = "tiktok_tokens.json"

auth_code_holder = {"code": None}


def _credentials() -> dict:
    cfg = load_settings()
    app_key = (cfg.get("app_key") or "").strip()
    app_secret = (cfg.get("app_secret") or "").strip()
    service_id = (cfg.get("service_id") or app_key).strip()
    redirect = (cfg.get("redirect_url") or "http://localhost:8080/callback").strip()
    token_file = cfg.get("token_file") or TOKEN_FILE
    if not app_key or not app_secret:
        raise RuntimeError("请先在 config/settings.json 填写 app_key / app_secret")
    if not service_id:
        raise RuntimeError("请填写 service_id（Partner Center 应用名称下方的 ID）")
    return {
        "app_key": app_key,
        "app_secret": app_secret,
        "service_id": service_id,
        "redirect": redirect,
        "token_file": token_file,
    }


def auth_url(service_id: str) -> str:
    return f"{AUTH_PAGE}?service_id={urllib.parse.quote(service_id)}"


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        if code:
            auth_code_holder["code"] = code
            body = "<h2>✅ 授权成功！可以关闭此窗口了。</h2>".encode("utf-8")
        else:
            body = "<h2>❌ 未收到授权码，请重试。</h2>".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def get_token(auth_code: str, cred: dict) -> dict:
    url = "https://auth.tiktok-shops.com/api/v2/token/get"
    params = {
        "app_key": cred["app_key"],
        "app_secret": cred["app_secret"],
        "auth_code": auth_code,
        "grant_type": "authorized_code",
    }
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{query}", method="GET")
    with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as resp:
        return json.loads(resp.read())


def main():
    import sys as _sys
    if len(_sys.argv) > 2 and _sys.argv[1] == "--code":
        cred = _credentials()
        code = _sys.argv[2]
        print(f"使用手动 code 换 token…")
        result = get_token(code, cred)
        if result.get("code") != 0:
            print(f"❌ {result}")
            return
        data = result["data"]
        tokens = {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "access_token_expire_in": data.get("access_token_expire_in"),
            "refresh_token_expire_in": data.get("refresh_token_expire_in"),
            "open_id": data.get("open_id", ""),
            "seller_name": data.get("seller_name", ""),
            "authorized_shops": data.get("authorized_shops", []),
            "saved_at": int(time.time()),
        }
        out = Path(cred["token_file"])
        if not out.is_absolute():
            out = ROOT / out
        with out.open("w", encoding="utf-8") as f:
            json.dump(tokens, f, ensure_ascii=False, indent=2)
        print(f"✅ Token 已保存: {out}")
        return

    cred = _credentials()
    redirect = cred["redirect"]
    port = urllib.parse.urlparse(redirect).port or 8080

    print("=" * 50)
    print("  TikTok Shop API 授权工具")
    print("=" * 50)
    print(f"  app_key     = {cred['app_key']}")
    print(f"  service_id  = {cred['service_id']}")
    if cred["service_id"] == cred["app_key"]:
        print("  ⚠️  service_id 与 app_key 相同；若浏览器报「此服务不存在」，")
        print("     请到 Partner Center 复制正确的 Service ID 填入 settings.json")
    print(f"  redirect    = {redirect}")

    url = auth_url(cred["service_id"])
    server = HTTPServer(("localhost", port), CallbackHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"\n[1] 本地回调已启动 ({redirect})")
    print("[2] 正在打开授权页…")
    webbrowser.open(url)
    print("    若报「此服务不存在」，不要自己拼链接！请：")
    print("    Partner Center → 你的 App → 复制授权链接 → 粘贴到浏览器")
    print(f"    脚本生成的链接: {url}\n")

    print("[3] 等待授权（最多 5 分钟）…")
    print("    授权完成后浏览器会跳转到 localhost:8080 并显示「授权成功」")
    for _ in range(600):
        if auth_code_holder["code"]:
            break
        time.sleep(0.5)
    server.shutdown()

    if not auth_code_holder["code"]:
        print("\n❌ 超时未收到授权码。")
        print("   常见原因：")
        print("   1. 浏览器报「此服务不存在」→ Partner Center 复制授权链接，不要用 app_key 当 service_id")
        print("   2. Redirect URL 未设为 http://localhost:8080/callback")
        print("   3. 授权页未点同意就关了")
        print("   若跳转 URL 里已有 code=，可手动：python3 tiktok_auth.py --code <code>")
        return

    print("    ✅ 收到授权码")
    print("[4] 换取 Access Token…")
    try:
        result = get_token(auth_code_holder["code"], cred)
    except Exception as e:
        print(f"\n❌ 获取 Token 失败: {e}")
        return

    if result.get("code") != 0:
        print(f"\n❌ API 返回错误: {result}")
        return

    data = result["data"]
    tokens = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "access_token_expire_in": data.get("access_token_expire_in"),
        "refresh_token_expire_in": data.get("refresh_token_expire_in"),
        "open_id": data.get("open_id", ""),
        "seller_name": data.get("seller_name", ""),
        "authorized_shops": data.get("authorized_shops", []),
        "saved_at": int(time.time()),
    }
    out = Path(cred["token_file"])
    if not out.is_absolute():
        out = ROOT / out
    with out.open("w", encoding="utf-8") as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Token 已保存: {out}")
    shops = tokens.get("authorized_shops") or []
    if shops:
        print("\n已授权店铺:")
        for shop in shops:
            print(f"  - {shop.get('shop_name', '?')} [{shop.get('region', '')}]")
    print("\n下一步: python3 main.py status")


if __name__ == "__main__":
    main()
