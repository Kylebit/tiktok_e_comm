"""Local consumption of target readback references, never account authority.

The report retains a reference, not raw provider bytes or a caller's official
flag. A service-owned reader must reopen the retained packet and original bytes.
This first contract verifies binding and retention only. Native account/endpoint
authority is not installed here; even consistent PUBLISHED bytes are not success.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re


_HEX = re.compile('[a-f0-9]{64}')
_STATUS = {'PUBLISHED', 'PROCESSING', 'FAILED', 'UNAVAILABLE'}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _lineage(report, label):
    authority = report.get('release_authorization') or {}
    lineage = {key: report[key] for key in ('run_id', 'report_id', 'offer_id', 'revision', 'plan_id')} | {
        'snapshot_digest': report['snapshot']['digest'].removeprefix('sha256:'),
        'candidate_digest': str(authority.get('candidate_digest', '')).removeprefix('sha256:'),
        'approval_digest': str(authority.get('approval_digest', '')).removeprefix('sha256:'),
        'target_label': label,
    }
    if any(not _HEX.fullmatch(lineage[key]) for key in
           ('snapshot_digest', 'candidate_digest', 'approval_digest')):
        raise ValueError('TARGET_OBSERVATION_AUTHORIZATION_UNBOUND')
    return lineage


@dataclass(frozen=True)
class RetainedTargetReadback:
    """Reader result with actual bytes. Its type does not certify the account."""
    packet: dict
    response_bytes: bytes


def retained_reference(report, label, retained):
    """Used by an installed service-owned producer after retaining its packet."""
    if type(retained) is not RetainedTargetReadback:
        raise ValueError('TARGET_OBSERVATION_RETAINED_BYTES_REQUIRED')
    packet = retained.packet
    expected = {'schema_version', 'lineage', 'observed_status', 'observed_at',
                'response_sha256', 'response_byte_count', 'source_reference',
                'account_authority', 'execution_authority'}
    if type(packet) is not dict or set(packet) != expected:
        raise ValueError('TARGET_OBSERVATION_PACKET_INVALID')
    if (packet['schema_version'] != 'retained-target-readback/v1'
            or packet['lineage'] != _lineage(report, label)
            or packet['observed_status'] not in _STATUS
            or packet['account_authority'] != 'NOT_ESTABLISHED'
            or packet['execution_authority'] is not False):
        raise ValueError('TARGET_OBSERVATION_BINDING_INVALID')
    if any(not _HEX.fullmatch(packet['lineage'][key]) for key in
           ('snapshot_digest', 'candidate_digest', 'approval_digest')):
        raise ValueError('TARGET_OBSERVATION_AUTHORIZATION_UNBOUND')
    if label not in {t['target_label'] for t in report['targets']}:
        raise ValueError('TARGET_OBSERVATION_TARGET_UNBOUND')
    if type(packet['source_reference']) is not str or not re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', packet['source_reference']):
        raise ValueError('TARGET_OBSERVATION_SOURCE_REFERENCE_INVALID')
    observed = datetime.fromisoformat(packet['observed_at'].replace('Z', '+00:00'))
    if observed.tzinfo is None:
        raise ValueError('TARGET_OBSERVATION_TIME_UNZONED')
    raw = retained.response_bytes
    if (type(raw) is not bytes or not 0 < len(raw) <= 1024 * 1024
            or type(packet['response_byte_count']) is not int
            or packet['response_byte_count'] != len(raw)
            or packet['response_sha256'] != hashlib.sha256(raw).hexdigest()):
        raise ValueError('TARGET_OBSERVATION_BYTES_INVALID')
    packet_digest = _digest(packet)
    return {'target_label': label, 'reference': 'target-readback:' + packet_digest,
            'packet_digest': packet_digest, 'lineage_digest': _digest(packet['lineage'])}


def validate_references(value, report):
    if type(value) is not list or not value:
        raise ValueError('TARGET_OBSERVATION_REFERENCES_INVALID')
    labels = {t['target_label'] for t in report['targets']}
    seen = set()
    safe = []
    for ref in value:
        if type(ref) is not dict or set(ref) != {'target_label', 'reference', 'packet_digest', 'lineage_digest'}:
            raise ValueError('TARGET_OBSERVATION_REFERENCE_INVALID')
        label = ref['target_label']
        if type(label) is not str or label not in labels or label in seen:
            raise ValueError('TARGET_OBSERVATION_TARGET_UNBOUND')
        if (type(ref['packet_digest']) is not str or not _HEX.fullmatch(ref['packet_digest'])
                or ref['reference'] != 'target-readback:' + ref['packet_digest']
                or ref['lineage_digest'] != _digest(_lineage(report, label))):
            raise ValueError('TARGET_OBSERVATION_REFERENCE_BINDING_INVALID')
        seen.add(label)
        safe.append(deepcopy(ref))
    return safe


def collect_references(report, reader):
    """Local-only seam. A reader cannot substitute a naked official dictionary."""
    if reader is None:
        return []
    refs = []
    for target in report['targets']:
        label = target['target_label']
        try:
            retained = reader.retained_for_report(report=deepcopy(report), target_label=label)
            if retained is not None:
                refs.append(retained_reference(report, label, retained))
        except Exception:
            # A failed local observation may not erase the provider result or
            # cause redispatch. This target remains without an admitted ref.
            continue
    return refs


def consume_target(report, label, reader):
    refs = validate_references(report['target_observations'], report) if 'target_observations' in report else []
    reference = next((ref for ref in refs if ref['target_label'] == label), None)
    result = {'observed_status': None, 'observation_retention_verified': False,
              'account_authority': 'NOT_ESTABLISHED', 'official_success': False,
              'observation_reference': reference, 'observation_blocker': 'TARGET_OFFICIAL_READBACK_UNAVAILABLE'}
    if reference is None:
        return result
    if reader is None:
        result['observation_blocker'] = 'TARGET_OBSERVATION_READER_UNAVAILABLE'
        return result
    try:
        retained = reader.read_retained(reference=deepcopy(reference), report=deepcopy(report), target_label=label)
        if type(retained) is not RetainedTargetReadback:
            raise ValueError('TARGET_OBSERVATION_RETAINED_BYTES_REQUIRED')
        retained = RetainedTargetReadback(deepcopy(retained.packet), retained.response_bytes)
        if retained_reference(report, label, retained) != reference:
            raise ValueError('TARGET_OBSERVATION_REFERENCE_CHANGED')
    except Exception:
        result['observation_blocker'] = 'TARGET_OBSERVATION_UNAVAILABLE_OR_CHANGED'
        return result
    result.update(observed_status=retained.packet['observed_status'],
                  observation_retention_verified=True,
                  observation_blocker='TARGET_ACCOUNT_AUTHORITY_NOT_ESTABLISHED')
    return result
