import json, time, hmac, hashlib, ssl, urllib.parse, urllib.request, urllib.error

APP_KEY    = "6k71hqd3nnei4"
APP_SECRET = "a1b93a5cef4fb591bc5b020ad990b387289816f8"
BASE_URL   = "https://open-api.tiktokglobalshop.com"

with open("/Users/wangyin/Desktop/e-commercial/tiktok_e_comm/tiktok_tokens.json") as f:
    token = json.load(f)["access_token"]

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

def sign(path, all_params):
    """Sign ALL params (query + body merged together)"""
    keys = sorted(k for k in all_params if k != "sign")
    base = APP_SECRET + path + "".join(k + str(all_params[k]) for k in keys) + APP_SECRET
    return hmac.new(APP_SECRET.encode(), base.encode(), hashlib.sha256).hexdigest()

def post_with_body_sign(path, body_dict):
    """POST where body params are also included in signing"""
    qp = {"app_key": APP_KEY, "timestamp": str(int(time.time()))}
    # Merge qp + body for signing
    all_for_sign = {**qp, **body_dict}
    qp["sign"] = sign(path, all_for_sign)
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(qp)}"
    data = json.dumps(body_dict).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("x-tts-access-token", token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return {"err": e.code, "raw": raw[:300]}

shops_r = post_with_body_sign("/authorization/202309/shops", {})
if "err" in shops_r:
    # GET for shops
    p = {"app_key": APP_KEY, "timestamp": str(int(time.time()))}
    p["sign"] = sign("/authorization/202309/shops", p)
    url = f"{BASE_URL}/authorization/202309/shops?{urllib.parse.urlencode(p)}"
    req = urllib.request.Request(url)
    req.add_header("x-tts-access-token", token)
    with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as r:
        shops_r = json.loads(r.read())

shops = shops_r.get("data", {}).get("shops", [])
c = shops[0]["cipher"]
region = shops[0]["region"]
now = int(time.time())
print(f"Testing POST signing with body params included — shop: {region}\n")

body = {"shop_cipher": c, "create_time_ge": now-86400*7, "create_time_lt": now, "page_size": 5}
r = post_with_body_sign("/order/202309/orders/search", body)
print(f"Orders (body params in sign):")
print(f"  code={r.get('code')} err={r.get('err','')} msg={r.get('message','')[:150]}")
print(f"  raw={r.get('raw','')[:200]}")
if r.get("code") == 0:
    print(f"  ✅ SUCCESS! orders={len(r.get('data',{}).get('orders',[]))}")
