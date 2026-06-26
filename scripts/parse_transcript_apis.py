"""Extract API names/paths from agent transcript."""
import json
import re
from pathlib import Path

p = Path(
    r"C:\Users\Windows11\.cursor\projects\c-Users-Windows11-Desktop-Agent-PR-tiktok-e-comm"
    r"\agent-transcripts\b490aba3-e64c-49be-a633-01ab5db8774e"
    r"\b490aba3-e64c-49be-a633-01ab5db8774e.jsonl"
)
for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
    if i not in (138, 191):
        continue
    obj = json.loads(line)
    text = obj.get("message", {}).get("content", [{}])[0].get("text", "")
    print("=== LINE", i, "len", len(text), "===")
    for m in re.finditer(r"\[(.*?)\]\((https://s\.apifox\.cn/[^)]+)\)", text):
        url = m.group(2)
        api_id = url.rsplit("/", 1)[-1].replace(".md", "")
        print(f"{api_id}\t{m.group(1)}")
    for m in re.finditer(r"/open/v1/[^\s\"`]+", text):
        print("PATH", m.group(0))
    for kw in ("get_shop", "shopList", "productList", "collectBox", "店铺"):
        if kw in text:
            for m in re.finditer(re.escape(kw) + r".{0,80}", text):
                print("KW", m.group(0)[:120])
