"""
TikTok Shop OAuth 授权脚本
运行方式: python3 tiktok_auth.py
"""

import webbrowser
import json
import hashlib
import hmac
import time
import urllib.parse
import urllib.request
import ssl
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# 跳过 SSL 验证（macOS 本地使用，安全无问题）
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

# ── 你的应用凭据 ──────────────────────────────────────
APP_KEY    = "6k71hqd3nnei4"
APP_SECRET = "a1b93a5cef4fb591bc5b020ad990b387289816f8"
REDIRECT   = "http://localhost:8080/callback"
TOKEN_FILE = "tiktok_tokens.json"
# ──────────────────────────────────────────────────────

auth_code_holder = {"code": None}

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
        pass  # 静默日志


def get_token(auth_code):
    url = "https://auth.tiktok-shops.com/api/v2/token/get"
    params = {
        "app_key": APP_KEY,
        "app_secret": APP_SECRET,
        "auth_code": auth_code,
        "grant_type": "authorized_code",
    }
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{query}", method="GET")
    with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as resp:
        return json.loads(resp.read())


def main():
    print("=" * 50)
    print("  TikTok Shop API 授权工具")
    print("=" * 50)

    # 启动本地回调服务器
    server = HTTPServer(("localhost", 8080), CallbackHandler)
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()
    print("\n[1] 本地服务器已启动 (localhost:8080)")

    # 打开授权页面
    auth_url = f"https://services.tiktokshop.com/open/authorize?service_id={APP_KEY}"
    print(f"[2] 正在打开授权页面...")
    webbrowser.open(auth_url)
    print("    如果浏览器未自动打开，请手动访问：")
    print(f"    {auth_url}\n")

    # 等待回调
    print("[3] 等待你在浏览器中完成授权（最多60秒）...")
    for _ in range(120):
        if auth_code_holder["code"]:
            break
        time.sleep(0.5)

    server.shutdown()

    if not auth_code_holder["code"]:
        print("\n❌ 超时未收到授权码，请重新运行脚本。")
        return

    print(f"    ✅ 收到授权码")

    # 换取 Token
    print("[4] 正在获取 Access Token...")
    try:
        result = get_token(auth_code_holder["code"])
    except Exception as e:
        print(f"\n❌ 获取 Token 失败: {e}")
        return

    if result.get("code") != 0:
        print(f"\n❌ API 返回错误: {result}")
        return

    data = result["data"]
    tokens = {
        "access_token":  data["access_token"],
        "refresh_token": data["refresh_token"],
        "access_token_expire_in":  data.get("access_token_expire_in"),
        "refresh_token_expire_in": data.get("refresh_token_expire_in"),
        "open_id":   data.get("open_id", ""),
        "seller_name": data.get("seller_name", ""),
        "authorized_shops": data.get("authorized_shops", []),
        "saved_at": int(time.time()),
    }

    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 授权成功！Token 已保存到 {TOKEN_FILE}")
    print(f"\n已授权店铺:")
    for shop in tokens["authorized_shops"]:
        print(f"  - {shop.get('shop_name', '未知')} [{shop.get('region', '')}]  ID: {shop.get('shop_id', '')}")

    print("\n下一步：运行 python3 tiktok_data.py 开始拉取数据")


if __name__ == "__main__":
    main()
