"""Find all /open/v1 paths in transcript line 191."""
import json
import re
from pathlib import Path

p = Path(
    r"C:\Users\Windows11\.cursor\projects\c-Users-Windows11-Desktop-Agent-PR-tiktok-e-comm"
    r"\agent-transcripts\b490aba3-e64c-49be-a633-01ab5db8774e"
    r"\b490aba3-e64c-49be-a633-01ab5db8774e.jsonl"
)
text = json.loads(p.read_text(encoding="utf-8").splitlines()[190])["message"]["content"][0]["text"]
paths = sorted(set(re.findall(r"/open/v1/[a-zA-Z0-9_/]+", text)))
for path in paths:
    print(path)
