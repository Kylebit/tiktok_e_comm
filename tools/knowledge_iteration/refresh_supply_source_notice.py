"""Check or refresh this repository's dated supply Skill checksum disclosure.

Only the fixed project Skill, sync manifest and guide are read. --update changes
the guide's project checksums; personal installation and historical observations
remain unchanged. No execution authority, Skill installation or Vault writes.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'domains/supply_chain_operations/skills/manage-seaya-replenishment/SKILL.md'
SYNC = SKILL.parent / 'references/dashboard-sync.json'
GUIDE = ROOT / 'web/static/knowledge_guides.json'
RAW_LABEL = '本工程英文 Skill 原始字节'
LF_LABEL = '配置清单记录的本工程 LF 规范化文本'
INSTALLED_LABEL = '本机安装版原始字节'


def inspect_notice() -> tuple[str, dict, dict[str, str]]:
    text = GUIDE.read_text(encoding='utf-8')
    notice = json.loads(text)['entries']['manage-seaya-replenishment']['source_notice']
    rows = {row['label']: row for row in notice['checksums']}
    if set(rows) != {RAW_LABEL, LF_LABEL, INSTALLED_LABEL}:
        raise ValueError('unexpected checksum roles; do not rewrite the notice')
    raw = SKILL.read_bytes()
    digests = {RAW_LABEL: hashlib.sha256(raw).hexdigest(),
               LF_LABEL: hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()}
    sync = json.loads(SYNC.read_text(encoding='utf-8'))
    if (sync.get('digest_policy') != 'sha256-utf8-crlf-to-lf/v1'
            or sync['sha256'].get('skills/manage-seaya-replenishment/SKILL.md') != digests[LF_LABEL]):
        raise ValueError('project Skill and fixed sync manifest differ; repair the source first')
    return text, notice, digests


def refreshed_notice(notice: dict, digests: dict[str, str], observed_at: str) -> dict:
    if date.fromisoformat(observed_at).isoformat() != observed_at:
        raise ValueError('explicit observation date must be YYYY-MM-DD')
    result = deepcopy(notice)
    # Keep the original 9/28 comparison and installed observation, even on rerun.
    if 'historical_checksums' not in result:
        result['historical_checksums'] = [dict(row, observed_at=notice['observed_at'])
                                        for row in notice['checksums']]
    result['project_snapshot_observed_at'] = observed_at
    for row in result['checksums']:
        if row['label'] in digests:
            row.update(sha256=digests[row['label']], observed_at=observed_at)
        else:
            row.setdefault('observed_at', notice['observed_at'])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update', action='store_true')
    parser.add_argument('--observed-at', help='actual project source check date; required for --update')
    args = parser.parse_args()
    text, notice, digests = inspect_notice()
    current = {row['label']: row['sha256'] for row in notice['checksums']}
    matched = all(current[label] == digest for label, digest in digests.items())
    if args.update:
        if not args.observed_at:
            parser.error('--update requires the actual --observed-at date')
        updated = refreshed_notice(notice, digests, args.observed_at)
        # Replace only this object, preserving all other guides and formatting.
        marker = '"manage-seaya-replenishment":'
        start = text.index('"source_notice":', text.index(marker)) + len('"source_notice":')
        while text[start].isspace():
            start += 1
        original, consumed = json.JSONDecoder().raw_decode(text[start:])
        if original != notice:
            raise ValueError('unexpected source notice position')
        rendered = json.dumps(updated, ensure_ascii=False, indent=2)
        rendered = rendered.replace('\n', '\n      ')
        guide_text = text[:start] + rendered + text[start + consumed:]
        GUIDE.write_bytes(guide_text.encode('utf-8'))
    print(json.dumps({'state': 'UPDATED_PROJECT_SNAPSHOT' if args.update else 'MATCH' if matched else 'PROJECT_NOTICE_DRIFT',
                      'project_checksums': digests, 'installed_observation_refreshed': False,
                      'historical_notice_retained': True, 'execution_authority': False}, ensure_ascii=False))
    return 0 if args.update or matched else 1


if __name__ == '__main__':
    raise SystemExit(main())
