"""Reference metadata for K00; deliberately not a review manifest or approval."""
from hashlib import sha256
import json
from typing import Any, Mapping


def profit_report_reference(report: Mapping[str, Any]) -> dict[str, Any]:
    raw = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    digest = 'sha256:' + sha256(raw).hexdigest()
    return {
        'schema_version': 'profit-knowledge-reference/v1',
        'report_refs': ['audit://profit-report/' + digest.split(':', 1)[1]],
        'report_digest': digest,
        'report_id': report.get('report_id'),
        'report_version': report.get('idempotency_key'),
        'platform': report.get('platform'),
        'period': dict(report.get('period') or {}),
        'report_status': report.get('status'),
        'result_scope': report.get('result_scope'),
        'calculation_kind': report.get('calculation_kind'),
        'usage': 'reference',
        'knowledge_approval_status': 'not_requested',
        'current_execution_authority': False,
        'fresh_business_facts_verified': False,
        'facts': {'basis': 'captured_settlement_rows', 'source': dict(report.get('source') or {})},
        'assumptions': {'advertising': report.get('advertising') or report.get('assumptions') or {},
                        'cost_warnings': list(report.get('assumption_warnings') or [])},
        'quality_issues': list(report.get('quality_issues') or []),
    }
