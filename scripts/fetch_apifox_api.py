"""Fetch Apifox API detail via internal API."""
import json
import os
import urllib.request
import http.cookiejar

PROJECT = "fd54e57e-9b98-4c34-bada-306221c39e68"
PWD = os.environ.get("APIFOX_PWD", "d8DUQdqH")

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def post(url, data):
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Origin": "https://s.apifox.cn",
            "Referer": f"https://s.apifox.cn/{PROJECT}/doc-8586023",
        },
    )
    with opener.open(req, timeout=20) as r:
        return r.status, r.read().decode("utf-8", errors="replace")

def get(url):
    req = urllib.request.Request(
        url,
        headers={
            "Origin": "https://s.apifox.cn",
            "Referer": f"https://s.apifox.cn/{PROJECT}/doc-8586023",
        },
    )
    with opener.open(req, timeout=20) as r:
        return r.status, r.read().decode("utf-8", errors="replace")

st, body = post(f"https://s.apifox.cn/api/v1/projects/{PROJECT}/shared-doc-auth", {"password": PWD})
print("auth", st, body[:300])

urls = [
    f"https://s.apifox.cn/api/v1/projects/{PROJECT}/shared-doc",
    f"https://s.apifox.cn/api/v1/shared-docs/{PROJECT}",
    f"https://s.apifox.cn/api/v1/shared-doc/{PROJECT}/doc-8586023",
    f"https://s.apifox.cn/api/v1/shared-doc/{PROJECT}/api-446814596",
    f"https://api.apifox.cn/api/v1/projects/{PROJECT}/shared-doc",
]
for u in urls:
    try:
        st, body = get(u)
        print(f"\nGET {u}\n  -> {st} len={len(body)}")
        print(body[:800])
    except Exception as e:
        print(f"\nGET {u}\n  -> ERR {e}")
