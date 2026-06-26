"""Fetch single Apifox API definition after shared-doc auth."""
import json
import urllib.request
import http.cookiejar

PROJECT = "fd54e57e-9b98-4c34-bada-306221c39e68"
PWD = "d8DUQdqH"
API_IDS = ["446814591", "446814582", "446814596"]

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

auth_req = urllib.request.Request(
    f"https://s.apifox.cn/api/v1/projects/{PROJECT}/shared-doc-auth",
    data=json.dumps({"password": PWD}).encode(),
    method="POST",
    headers={
        "Content-Type": "application/json",
        "Origin": "https://s.apifox.cn",
        "Referer": f"https://s.apifox.cn/{PROJECT}/doc-8586023",
        "User-Agent": "Mozilla/5.0",
    },
)
with opener.open(auth_req, timeout=20) as resp:
    print("auth", resp.status, resp.read()[:120])

for api_id in API_IDS:
    urls = [
        f"https://s.apifox.cn/api/v1/projects/{PROJECT}/apis/{api_id}",
        f"https://s.apifox.cn/api/v1/shared-doc/{PROJECT}/api-{api_id}",
        f"https://s.apifox.cn/{PROJECT}/api-{api_id}.md",
        f"https://s.apifox.cn/{PROJECT}/api-{api_id}.json",
    ]
    for url in urls:
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Referer": f"https://s.apifox.cn/{PROJECT}/doc-8586023",
                    "User-Agent": "Mozilla/5.0",
                },
            )
            with opener.open(req, timeout=30) as resp:
                data = resp.read()
                print(f"\n{api_id} {url} -> {resp.status} len={len(data)}")
                text = data.decode("utf-8", errors="replace")
                if len(text) > 50:
                    print(text[:600])
                    if "/open/v1/" in text:
                        import re
                        for p in re.findall(r"/open/v1/[a-zA-Z0-9_/]+", text):
                            print(" PATH", p)
        except Exception as e:
            print(f"\n{api_id} {url} ERR {e}")
