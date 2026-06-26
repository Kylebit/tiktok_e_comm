"""Dump get_shop_list section from transcript line 191."""
import json
from pathlib import Path

p = Path(
    r"C:\Users\Windows11\.cursor\projects\c-Users-Windows11-Desktop-Agent-PR-tiktok-e-comm"
    r"\agent-transcripts\b490aba3-e64c-49be-a633-01ab5db8774e"
    r"\b490aba3-e64c-49be-a633-01ab5db8774e.jsonl"
)
line = p.read_text(encoding="utf-8").splitlines()[190]
text = json.loads(line)["message"]["content"][0]["text"]
idx = text.find("get_shop_list")
print(text[idx - 500 : idx + 8000])
