"""Fetch Apifox shared OpenAPI export."""
import json
import urllib.request
import http.cookiejar

PROJECT = "fd54e57e-9b98-4c34-bada-306221c39e68"
PWD = "d8DUQdqH"

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
    },
)
with opener.open(auth_req, timeout=20) as resp:
    print("auth", resp.status)

urls = [
    f"https://s.apifox.cn/api/v1/projects/{PROJECT}/export-openapi?version=3.0",
    f"https://s.apifox.cn/api/v1/shared-docs/{PROJECT}/openapi.json",
    f"https://s.apifox.cn/{PROJECT}/openapi.json",
]
for url in urls:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Origin": "https://s.apifox.cn",
                "Referer": f"https://s.apifox.cn/{PROJECT}/doc-8586023",
            },
        )
        with opener.open(req, timeout=30) as resp:
            data = resp.read()
            print(url, resp.status, len(data))
            text = data.decode("utf-8", errors="replace")
            if "paths" in text:
                spec = json.loads(text)
                for p in sorted(spec.get("paths", {})):
                    if "shop" in p or "product" in p or "collect" in p:
                        print(" ", p)
    except Exception as e:
        print(url, "ERR", e)
