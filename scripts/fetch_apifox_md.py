"""Download Apifox API markdown pages after password auth."""
import json
import urllib.request
import http.cookiejar
from pathlib import Path

PROJECT = "fd54e57e-9b98-4c34-bada-306221c39e68"
PWD = "d8DUQdqH"
OUT = Path(__file__).resolve().parents[1] / "docs" / "apifox"

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
    print("auth", resp.status, resp.read()[:100])

OUT.mkdir(parents=True, exist_ok=True)
for slug in ["api-446814591", "api-446814596", "api-446814582", "doc-8586023"]:
    url = f"https://s.apifox.cn/{PROJECT}/{slug}.md"
    req = urllib.request.Request(
        url,
        headers={
            "Referer": f"https://s.apifox.cn/{PROJECT}/doc-8586023",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with opener.open(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
        path = OUT / f"{slug}.md"
        path.write_text(text, encoding="utf-8")
        print(slug, len(text), text[:120].replace("\n", " "))
