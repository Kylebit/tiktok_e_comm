"""Fetch Miaoshou Apifox shared doc (password in env or default from user)."""
import json
import os
import urllib.request
import http.cookiejar

PROJECT = "fd54e57e-9b98-4c34-bada-306221c39e68"
PWD = os.environ.get("APIFOX_PWD", "d8DUQdqH")

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
with opener.open(auth_req, timeout=20) as r:
    print("auth:", r.status, r.read()[:200])

for slug in ["doc-8586023", "api-446814596"]:
    with opener.open(f"https://s.apifox.cn/{PROJECT}/{slug}.md", timeout=20) as r:
        text = r.read().decode("utf-8", errors="replace")
        out = f"docs/apifox_{slug}.md"
        open(out, "w", encoding="utf-8").write(text)
        print(f"saved {out} ({len(text)} chars)")
        print(text[:2500])
