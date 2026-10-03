"""Read one original publication review action from an isolated task ledger image.

This is a source package, not an approval or execution authority. No Workbench
transaction, lease expiration, HTTP handler, or domain store is invoked here.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import stat

from shared_platform.workbench_engine import ReceiptSnapshotUnavailable, _receipt_database_image
from shared_platform.immutable_approval_files import require_local_path
from shared_platform.publication_final_review_pending import _source_read_barrier


@dataclass(frozen=True)
class FinalReviewReference:
    offer_id: str
    revision: str
    sku: str
    round1_digest: str
    round2_digest: str
    targets: tuple[str, ...]
    common_plan_id: str
    common_payload_digest: str
    common_token_digest: str
    preview_digest: str
    scope_digest: str


@dataclass(frozen=True)
class FinalReviewTaskEvidence:
    task_id: str
    action_id: str
    generation: int
    step_index: int
    review_mode: str
    scope_offer_id: str
    scope_skus: tuple[str, ...]
    scope_shops: tuple[str, ...]
    original_event_id: int
    original_event_digest: str
    binding_digest: str
    reference: FinalReviewReference


def _object(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('final review ledger object required')
    return value


def _directory_evidence(root):
    """Bounded tree inventory of identity metadata and SHA, excluding atime.

    Windows can update access time on a read. This detects changed entries and
    bytes but does not promise literal full-lstat immutability.
    """
    entries = []
    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            info = require_local_path(child, root=root, allow_directory=True)
            if info is None or info.st_nlink != 1:
                raise ReceiptSnapshotUnavailable('task ledger source has an unsafe entry')
            relative = str(child.relative_to(root))
            signature = (info.st_mode, info.st_dev, info.st_ino, info.st_nlink,
                         info.st_size, info.st_ctime_ns, info.st_mtime_ns,
                         getattr(info, 'st_file_attributes', None))
            if stat.S_ISDIR(info.st_mode):
                entries.append((relative, signature, None))
                pending.append(child)
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > 256 * 1024 * 1024:
                    raise ReceiptSnapshotUnavailable('task ledger directory exceeds evidence limit')
                entries.append((relative, signature, hashlib.sha256(child.read_bytes()).digest()))
            else:
                raise ReceiptSnapshotUnavailable('task ledger source has an unsafe entry')
            if len(entries) > 512:
                raise ReceiptSnapshotUnavailable('task ledger directory exceeds evidence limit')
    root_info = require_local_path(root, root=root, allow_directory=True)
    return ((root_info.st_mode, root_info.st_dev, root_info.st_ino,
             root_info.st_size, root_info.st_ctime_ns, root_info.st_mtime_ns,
             getattr(root_info, 'st_file_attributes', None)),
            tuple(sorted(entries)))


def _verified_task_image(path):
    source = require_local_path(path, root=path.parent)
    if source is None:
        raise KeyError('task ledger database missing')
    if source.st_nlink != 1:
        raise ReceiptSnapshotUnavailable('task ledger database has another hard link')
    try:
        with _source_read_barrier(path) as (_, still_single_link):
            before = _directory_evidence(path.parent)
            image = _receipt_database_image(path)
            if not still_single_link() or before != _directory_evidence(path.parent):
                raise ReceiptSnapshotUnavailable('task ledger source changed during image capture')
            return image
    except (OSError, ValueError) as error:
        if isinstance(error, ReceiptSnapshotUnavailable):
            raise
        raise ReceiptSnapshotUnavailable('task ledger read barrier unavailable') from error


def read_final_review_task_evidence(database_path, task_id):
    """Return immutable evidence for the one current original action, or fail closed.

    SQLite only opens an in-memory deserialization of bytes captured under the
    source read barrier. The caller supplies a task id, never a projected task.
    """
    if not isinstance(task_id, str) or not task_id:
        raise ValueError('exact task id required')
    path = Path(database_path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('absolute task ledger path required')
    image = _verified_task_image(path)
    if not hasattr(sqlite3.Connection, 'deserialize'):
        raise ReceiptSnapshotUnavailable('runtime cannot load an in-memory database image')
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA temp_store=MEMORY')
        conn.deserialize(image)
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        row = conn.execute('SELECT e.*, t.status AS task_status, i.review_mode, i.generation '
                           'FROM workbench_execution e '
                           'JOIN workbench_tasks t USING(task_id) '
                           'JOIN workbench_review_identity i USING(task_id) '
                           'WHERE e.task_id=?', (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        steps = json.loads(row['steps_json'])
        scope = _object(row['scope_json'])
        action = _object(row['action_json']) if row['action_json'] else {}
        binding = action.get('receipt_binding')
        if (row['template'] != 'publication' or row['state'] != 'waiting_user'
                or row['task_status'] != 'waiting_approval'
                or row['step_index'] != 2 or not isinstance(steps, list)
                or len(steps) <= 2 or not isinstance(steps[2], dict)
                or steps[2].get('key') != 'release'
                or row['review_mode'] != 'single-final-review/v1'
                or type(row['generation']) is not int or row['generation'] != 1
                or row['external_started'] or row['worker'] is not None
                or row['lease_token'] is not None or row['lease_until'] is not None
                or not isinstance(binding, dict)
                or action.get('kind') != 'review'
                or not isinstance(action.get('action_id'), str)
                or not action['action_id']
                or conn.execute('SELECT 1 FROM workbench_external_tasks WHERE task_id=?',
                                (task_id,)).fetchone()
                or conn.execute('SELECT 1 FROM workbench_domain_operations '
                                "WHERE owner_task_id=? AND state<>'completed'", (task_id,)).fetchone()):
            raise ValueError('final review task is not the current idle release action')
        required = {'offer_id', 'revision', 'sku', 'round1_digest', 'round2_digest',
                    'targets', 'common_plan_id', 'common_payload_digest',
                    'common_token_digest', 'preview_digest', 'adapter', 'review_mode',
                    'task_id', 'action_id', 'generation', 'step_index', 'scope_digest'}
        fields = required - {'targets', 'generation', 'step_index'}
        targets = binding.get('targets')
        scope_digest = hashlib.sha256(row['scope_json'].encode()).hexdigest()
        if (set(binding) != required
                or any(not isinstance(binding.get(key), str) or not binding[key] for key in fields)
                or not isinstance(targets, list) or not targets
                or any(not isinstance(item, str) or not item for item in targets)
                or len(set(targets)) != len(targets)
                or binding['adapter'] != 'single-final-review/v1'
                or binding['review_mode'] != row['review_mode']
                or binding['task_id'] != task_id
                or binding['action_id'] != action['action_id']
                or type(binding['generation']) is not int
                or binding['generation'] != row['generation']
                or type(binding['step_index']) is not int or binding['step_index'] != 2
                or binding['scope_digest'] != scope_digest
                or scope.get('offer_id') != binding['offer_id']
                or scope.get('skus') != [binding['sku']]
                or scope.get('shops') != sorted(targets)):
            raise ValueError('final review binding or scope drifted')
        events = conn.execute("SELECT id,task_id,detail_json FROM workbench_events "
                              "WHERE event_type='user_action_required' ORDER BY id").fetchall()
        owners = []
        for event in events:
            detail = _object(event['detail_json'])
            if detail.get('action_id') == action['action_id']:
                owners.append(event)
        if len(owners) != 1 or owners[0]['task_id'] != task_id \
                or _object(owners[0]['detail_json']) != action:
            raise ValueError('original final review action event unavailable')
        reference = FinalReviewReference(
            binding['offer_id'], binding['revision'], binding['sku'],
            binding['round1_digest'], binding['round2_digest'], tuple(targets),
            binding['common_plan_id'], binding['common_payload_digest'],
            binding['common_token_digest'], binding['preview_digest'], scope_digest)
        return FinalReviewTaskEvidence(
            task_id, action['action_id'], row['generation'], 2, row['review_mode'],
            scope['offer_id'], tuple(scope['skus']), tuple(scope['shops']),
            owners[0]['id'], hashlib.sha256(owners[0]['detail_json'].encode()).hexdigest(),
            hashlib.sha256(json.dumps(binding, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
            reference)
    finally:
        conn.close()
