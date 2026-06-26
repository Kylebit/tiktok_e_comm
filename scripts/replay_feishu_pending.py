"""补跑 approvals/ 里已记录但未执行的飞书审批/修改意见。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_APPROVALS = Path(
    r"C:\Users\Windows11\Documents\Codex\2026-06-25"
    r"\chrome-plugin-chrome-openai-bundled-file-2\outputs\agent-command-center\approvals"
)

from scripts.orbit_mx_feishu_executor import handle_approve, handle_revision  # noqa: E402


def _extract(record: dict) -> tuple[str, str, str, str]:
    action = str(record.get("action") or "")
    mk = str(record.get("match_key") or "")
    tok = str(record.get("confirm_token") or "")
    note = str(record.get("modify_note") or "")
    raw = record.get("raw") or {}
    ev = raw.get("event") or {}
    act = ev.get("action") or {}
    val = act.get("value") or {}
    form = act.get("form_value") or {}
    if not mk:
        mk = str(val.get("match_key") or "")
    if not tok:
        tok = str(val.get("confirm_token") or "")
    if not note:
        note = str(form.get("modify_note") or "").strip()
    if not action:
        action = str(val.get("action") or "")
    return action, mk, tok, note


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approvals-dir", type=Path, default=DEFAULT_APPROVALS)
    ap.add_argument("--match-key", default="", help="只处理指定 MK")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    d = args.approvals_dir
    if not d.is_dir():
        print(f"missing {d}", file=sys.stderr)
        return 1

    seen: set[tuple[str, str, str]] = set()
    files = sorted(d.glob("APPROVALRESULT-*.json"), key=lambda p: p.stat().st_mtime)
    rc = 0
    for path in files:
        record = json.loads(path.read_text(encoding="utf-8"))
        action, mk, tok, note = _extract(record)
        if not mk or action not in ("approve", "request_revision"):
            continue
        if args.match_key and mk != args.match_key.zfill(4)[-4:]:
            continue
        key = (action, mk, note or tok)
        if key in seen:
            continue
        seen.add(key)
        print(f"{path.name}: {action} {mk} note={note!r} token={tok[:8] if tok else ''}")
        if args.dry_run:
            continue
        if action == "request_revision":
            if not note:
                print("  skip: empty modify_note")
                continue
            rc = max(rc, handle_revision(mk, note))
        else:
            rc = max(rc, handle_approve(mk, tok))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
