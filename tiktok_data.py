"""
TikTok Shop 数据拉取脚本
运行方式: python3 tiktok_data.py
"""

import json, time, hmac, hashlib, ssl, urllib.parse, urllib.request

# ── 凭据 ──────────────────────────────────────────────
APP_KEY    = "6k71hqd3nnei4"
APP_SECRET = "a1b93a5cef4fb591bc5b020ad990b387289816f8"
TOKEN_FILE = "tiktok_tokens.json"
BASE_URL   = "https://open-api.tiktokglobalshop.com"
# ──────────────────────────────────────────────────────

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

def load_token():
    with open(TOKEN_FILE, encoding="utf-8") as f:
        return json.load(f)

def sign(path: str, params: dict, secret: str, body: str = "", debug: bool = False) -> str:
    """TikTok Shop API v202309 签名：query 参数排序拼接，POST 再追加 JSON body"""
    keys = sorted(k for k in params if k not in ("sign", "access_token"))
    base = secret + path + "".join(k + str(params[k]) for k in keys) + body + secret
    if debug:
        print(f"  [DEBUG] 签名原文: {base[:200]}")
    return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()

def api_call(path: str, access_token: str, query_params: dict = None, body: dict = None, debug: bool = False) -> dict:
    params = {"app_key": APP_KEY, "timestamp": str(int(time.time()))}
    if query_params:
        params.update(query_params)
    body_str = json.dumps(body, separators=(",", ":")) if body else ""
    params["sign"] = sign(path, params, APP_SECRET, body=body_str, debug=debug)
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    data = body_str.encode() if body_str else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    req.add_header("x-tts-access-token", access_token)
    req.add_header("Content-Type", "application/json")
    if debug:
        print(f"  → {'POST' if body is not None else 'GET'} {url[:100]}...")
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="ignore")
        print(f"  → HTTP {e.code} 错误: {body_err[:300]}")
        raise

def api_get(path, access_token, extra_params=None, debug=False):
    return api_call(path, access_token, query_params=extra_params, debug=debug)

def get_shops(access_token: str) -> list:
    result = api_get("/authorization/202309/shops", access_token, debug=True)
    print(f"  获取店铺响应: {json.dumps(result, ensure_ascii=False)[:300]}")
    if result.get("code") == 0:
        return result["data"].get("shops", result["data"].get("list", []))
    return []

def get_orders(access_token: str, shop_cipher: str, days: int = 7) -> list:
    now = int(time.time())
    start = now - days * 86400
    result = api_call(
        "/order/202309/orders/search", access_token,
        query_params={"shop_cipher": shop_cipher, "page_size": 50},
        body={"create_time_ge": start, "create_time_lt": now},
        debug=True,
    )
    if result.get("code") == 0:
        return result["data"].get("orders", [])
    print(f"  订单查询错误: {result}")
    return []

def main():
    print("=" * 50)
    print("  TikTok Shop 数据拉取工具")
    print("=" * 50)

    tokens = load_token()
    access_token = tokens["access_token"]
    print(f"\n✅ 已加载 Token，卖家: {tokens.get('seller_name', '未知')}")

    # 1. 获取授权店铺列表
    print("\n[1] 获取授权店铺...")
    shops = get_shops(access_token)

    if not shops:
        print("  ⚠️  未获取到店铺列表，尝试直接拉取数据...")
        # 把原始响应存下来看看
        result = api_get("/authorization/202309/shops", access_token)
        with open("debug_shops.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"  完整响应已保存到 debug_shops.json")
        return

    print(f"  找到 {len(shops)} 个店铺:")
    for s in shops:
        print(f"  - {s.get('name', s.get('shop_name','?'))} [{s.get('region')}] cipher: {s.get('cipher','')[:20]}...")

    # 2. 每个店铺拉近7天订单
    all_data = {}
    print("\n[2] 拉取近7天订单...")
    for shop in shops:
        name   = shop.get("name", shop.get("shop_name", "未知"))
        region = shop.get("region", "")
        cipher = shop.get("cipher") or shop.get("shop_cipher", "")
        print(f"\n  {name} ({region})...")
        orders = get_orders(access_token, cipher, days=7)
        print(f"  → 获取到 {len(orders)} 条订单")
        all_data[f"{name}_{region}"] = orders

    # 3. 保存结果
    out_file = "tiktok_orders.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    total = sum(len(v) for v in all_data.values())
    print(f"\n✅ 数据已保存到 {out_file}")
    print(f"   共 {total} 条订单")

    try:
        from build_orders_page import main as build_page
        build_page()
    except Exception as e:
        print(f"   ⚠️  订单页面生成失败: {e}（可手动运行 python3 build_orders_page.py）")

if __name__ == "__main__":
    main()
