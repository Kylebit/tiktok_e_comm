"""Read-only, per-source evidence inventory for a future final-review preview.

This manifest is deliberately not a decision, a cross-store snapshot, or CAS
authority. In particular, a writer can change a source after the last check.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from shared_platform.publication_final_review_pending import (
    _require_plain_source_path, _sidecar_present, _source_read_barrier,
)
from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS


_MAX_FILE = 256 * 1024 * 1024
_DB_SUFFIXES = ('-wal', '-shm', '-journal')


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _directory_identity(path: Path) -> tuple[int, int, int]:
    source = path
    if not source.is_absolute() or '..' in source.parts:
        raise ValueError('FINAL_REVIEW_MANIFEST_PATH_UNSTABLE')
    # Validate every component without resolving through a junction.
    for component in (*reversed(source.parents), source):
        info = component.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_nlink != 1
                or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise ValueError('FINAL_REVIEW_MANIFEST_PATH_UNSTABLE')
    info = source.stat()
    return info.st_dev, info.st_ino, info.st_mtime_ns


def _capture_file(path: Path, *, database: bool = False) -> tuple[dict[str, Any], bytes]:
    """Copy one pinned regular file, rejecting alternate links and DB journals."""
    try:
        source = _require_plain_source_path(path)
        if database and any(_sidecar_present(Path(str(source) + suffix))
                            for suffix in _DB_SUFFIXES):
            raise ValueError('FINAL_REVIEW_MANIFEST_SIDECAR_PRESENT')
        with _source_read_barrier(source) as (stream, still_single_link):
            before = source.stat()
            opened = os.fstat(stream.fileno())
            identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            if (before.st_nlink != 1 or before.st_size > _MAX_FILE
                    or identity != (opened.st_dev, opened.st_ino,
                                    opened.st_size, opened.st_mtime_ns)):
                raise ValueError('FINAL_REVIEW_MANIFEST_SOURCE_UNSTABLE')
            raw = stream.read(_MAX_FILE + 1)
            after = source.stat()
            if (len(raw) != before.st_size or len(raw) > _MAX_FILE
                    or not still_single_link()
                    or _require_plain_source_path(path) != source
                    or identity != (after.st_dev, after.st_ino,
                                    after.st_size, after.st_mtime_ns)
                    or (database and any(_sidecar_present(Path(str(source) + suffix))
                                         for suffix in _DB_SUFFIXES))):
                raise ValueError('FINAL_REVIEW_MANIFEST_SOURCE_UNSTABLE')
        return ({'path': str(source), 'file_id': [before.st_dev, before.st_ino],
                 'size': before.st_size, 'mtime_ns': before.st_mtime_ns,
                 'sha256': hashlib.sha256(raw).hexdigest()}, raw)
    except (OSError, FileNotFoundError) as error:
        raise ValueError('FINAL_REVIEW_MANIFEST_SOURCE_UNSTABLE') from error
    except ValueError as error:
        if str(error) == 'FINAL_REVIEW_PENDING_SOURCE_UNSTABLE':
            raise ValueError('FINAL_REVIEW_MANIFEST_SOURCE_UNSTABLE') from error
        raise


def collect_final_review_evidence_manifest(*, offer_id: str, targets: tuple[str, ...],
                                           reports_root: Path, product_state: Path,
                                           catalog_db: Path, release_db: Path,
                                           task_db: Path) -> dict[str, Any]:
    """Inventory required files and verify they still match at the end of capture.

    The caller must supply absolute, server-owned paths. No SQLite source is
    opened, and no action/plan is approved or inferred from these bytes.
    """
    if (not isinstance(offer_id, str) or not re.fullmatch(r'[0-9]{1,32}', offer_id)
            or not isinstance(targets, tuple) or not targets
            or any(not isinstance(t, str) or not t for t in targets)
            or len(set(targets)) != len(targets)):
        raise ValueError('FINAL_REVIEW_MANIFEST_SCOPE_INVALID')
    paths = (reports_root, product_state, catalog_db, release_db, task_db)
    if any(not isinstance(p, Path) or not p.is_absolute() or '..' in p.parts for p in paths):
        raise ValueError('FINAL_REVIEW_MANIFEST_PATH_UNSTABLE')
    report_dir = reports_root / offer_id
    before_dir = _directory_identity(report_dir)
    captured: dict[str, tuple[dict[str, Any], bytes]] = {}

    def add(key: str, path: Path, *, database: bool = False) -> bytes:
        evidence = _capture_file(path, database=database)
        captured[key] = evidence
        return evidence[1]

    documents = {}
    for key, name in R2_DOCUMENTS.items():
        try:
            value = json.loads(add('report:' + key, report_dir / name))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError('FINAL_REVIEW_MANIFEST_REPORT_INVALID') from error
        if not isinstance(value, dict) or value.get('offer_id') != offer_id:
            raise ValueError('FINAL_REVIEW_MANIFEST_OFFER_CONFLICT')
        documents[key] = value
    round1_targets = documents['round1_snapshot'].get('canonical_targets')
    first_targets = (documents['first_review'].get('target_selection') or {}).get('requested')
    if round1_targets != list(targets) or first_targets != list(targets):
        raise ValueError('FINAL_REVIEW_MANIFEST_TARGET_CONFLICT')

    generated = documents['generation_result'].get('assets')
    plan = documents['translation_plan']
    translation = documents['translation_result']
    tasks = plan.get('tasks')
    localized = translation.get('assets')
    if not isinstance(generated, list) or not generated:
        raise ValueError('FINAL_REVIEW_MANIFEST_IMAGE_COVERAGE_UNKNOWN')
    # The R2 receipt permits zero localized images only when the approved
    # translation scope is explicitly empty. This is a file-inventory check,
    # not validation of the complete R2 semantic graph.
    if (not isinstance(tasks, list) or not isinstance(localized, list)
            or type(plan.get('approved_task_count')) is not int
            or plan['approved_task_count'] != len(tasks)
            or translation.get('approved_tasks') != tasks
            or type(translation.get('approved_task_count')) is not int
            or translation['approved_task_count'] != len(tasks)
            or len(localized) != len(tasks)
            or (not tasks and translation.get('status') != 'NOT_REQUIRED')
            or (tasks and translation.get('status') != 'LOCALIZED_IMAGE_REVIEW_REQUIRED')):
        raise ValueError('FINAL_REVIEW_MANIFEST_IMAGE_COVERAGE_UNKNOWN')
    assets = [*generated, *localized]
    image_paths = set()
    for index, row in enumerate(assets):
        if not isinstance(row, dict) or not isinstance(row.get('artifact_path'), str):
            raise ValueError('FINAL_REVIEW_MANIFEST_IMAGE_COVERAGE_UNKNOWN')
        path = Path(row['artifact_path'])
        if not path.is_absolute() or not path.is_relative_to(report_dir):
            raise ValueError('FINAL_REVIEW_MANIFEST_IMAGE_PATH_CONFLICT')
        if path in image_paths:
            raise ValueError('FINAL_REVIEW_MANIFEST_IMAGE_DUPLICATE')
        image_paths.add(path)
        digest = row.get('artifact_digest')
        if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            raise ValueError('FINAL_REVIEW_MANIFEST_IMAGE_COVERAGE_UNKNOWN')
        raw = add('image:' + str(index), path)
        if 'sha256:' + hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('FINAL_REVIEW_MANIFEST_IMAGE_DIGEST_CONFLICT')

    try:
        state = json.loads(add('product_state', product_state))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('FINAL_REVIEW_MANIFEST_STATE_INVALID') from error
    if (not isinstance(state, dict) or state.get('offer_id') != offer_id
            or type(state.get('_revision')) is not int or state['_revision'] < 0):
        raise ValueError('FINAL_REVIEW_MANIFEST_STATE_CONFLICT')
    for key, path in (('catalog_db', catalog_db), ('release_db', release_db),
                      ('task_db', task_db)):
        raw = add(key, path, database=True)
        if len(raw) < 100 or raw[:16] != b'SQLite format 3\x00' or raw[18:20] != b'\x01\x01':
            raise ValueError('FINAL_REVIEW_MANIFEST_DB_IMAGE_UNKNOWN')

    # This second pass catches replacement or byte drift between sequential
    # reads; it does not create a transaction across source owners.
    if _directory_identity(report_dir) != before_dir:
        raise ValueError('FINAL_REVIEW_MANIFEST_DIRECTORY_DRIFT')
    for key, (original, raw) in captured.items():
        current, current_raw = _capture_file(Path(original['path']), database=key.endswith('_db'))
        if current != original or current_raw != raw:
            raise ValueError('FINAL_REVIEW_MANIFEST_SOURCE_DRIFT')
    if _directory_identity(report_dir) != before_dir:
        raise ValueError('FINAL_REVIEW_MANIFEST_DIRECTORY_DRIFT')

    result = {
        'schema_version': 'final-review-evidence-manifest/v1',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'offer_id': offer_id, 'targets': list(targets),
        'product_revision': state['_revision'],
        'sources': {key: original for key, (original, _) in sorted(captured.items())},
        'translation_assets': {
            'count': len(localized),
            'absence_reason': 'NO_APPROVED_TRANSLATION_TASKS' if not localized else None,
        },
        'coverage': 'AUDITED_FILES_ONLY',
        'unverified': ['r2_semantic_identity', 'task_original_action', 'active_pending_plan',
                       'cross_source_atomicity_at_cas'],
        'execution_authority': False,
    }
    result['manifest_digest'] = 'sha256:' + hashlib.sha256(_canonical({
        key: value for key, value in result.items()
        if key not in {'captured_at', 'manifest_digest'}
    })).hexdigest()
    return result
