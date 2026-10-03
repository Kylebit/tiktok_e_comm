"""Read-only normalization of an existing, still-unapproved COMMON plan.

This adapter neither approves a Store row nor confers authority to its caller.
The current payload must come from the current authoritative COMMON producer;
future approval consumers must recheck it under their own durable CAS boundary.
"""

from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any, Mapping

from shared_platform.publication_final_review_preview import (
    FinalReviewPreview, build_final_review_preview,
    require_current_final_review_preview,
)
from shared_platform.release_store import ReleaseStore, _sku_key, preview_release_plan


_EXACT_PLAN_FIELDS = (
    'plan_id', 'product_id', 'seller_sku', 'product_package_id',
    'content_package_id', 'targets', 'payload', 'payload_digest',
    'confirmation_token',
)


def _stream_digest(source) -> str:
    digest = hashlib.sha256()
    source.seek(0)
    for chunk in iter(lambda: source.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()


def _file_digest(path: Path) -> str:
    with path.open('rb') as source:
        return _stream_digest(source)


def _require_plain_source_path(path: Path) -> Path:
    """Reject links/junctions in the original spelling before resolving it."""
    raw = Path(path)
    if (not raw.is_absolute() or '..' in raw.parts or os.name != 'nt'):
        raise ValueError('FINAL_REVIEW_PENDING_SOURCE_UNSTABLE')
    for component in (*reversed(raw.parents), raw):
        info = component.lstat()
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400
                or (component == raw and not stat.S_ISREG(info.st_mode))
                or (component != raw and not stat.S_ISDIR(info.st_mode))):
            raise ValueError('FINAL_REVIEW_PENDING_SOURCE_UNSTABLE')
    return raw.resolve(strict=True)


def _sidecar_present(path: Path) -> bool:
    try:
        path.lstat()  # Unlike exists/is_symlink, sees dangling junctions.
    except FileNotFoundError:
        return False
    return True


def _path_identity(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


@contextmanager
def _source_read_barrier(path: Path):
    """Deny concurrent Windows writers while copying a DB-only source.

    SQLite's mode=ro can create WAL/SHM files. A Windows read handle sharing
    READ only cannot coexist with an open writer and prevents new write opens.
    Other platforms fail closed until an equivalent barrier is implemented.
    """
    if os.name != 'nt':
        raise ValueError('FINAL_REVIEW_PENDING_SOURCE_UNSTABLE')
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class _FileTime(ctypes.Structure):
        _fields_ = [('low', wintypes.DWORD), ('high', wintypes.DWORD)]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ('attributes', wintypes.DWORD), ('created', _FileTime),
            ('accessed', _FileTime), ('written', _FileTime),
            ('volume_serial', wintypes.DWORD), ('size_high', wintypes.DWORD),
            ('size_low', wintypes.DWORD), ('number_of_links', wintypes.DWORD),
            ('file_index_high', wintypes.DWORD), ('file_index_low', wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                       wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel32.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    inspect = kernel32.GetFileInformationByHandle
    inspect.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation))
    inspect.restype = wintypes.BOOL
    handle = create(str(path), 0x80000000, 0x00000001, None, 3, 0x00200000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ValueError('FINAL_REVIEW_PENDING_SOURCE_UNSTABLE')
    try:
        native = _ByHandleFileInformation()
        if (not inspect(handle, ctypes.byref(native))
                or native.number_of_links != 1
                or native.attributes & 0x400
                or native.attributes & 0x10):
            raise ValueError('FINAL_REVIEW_PENDING_SOURCE_UNSTABLE')
        identity = (native.volume_serial, native.file_index_high,
                    native.file_index_low)

        def still_single_link() -> bool:
            current = _ByHandleFileInformation()
            return (bool(inspect(handle, ctypes.byref(current)))
                    and current.number_of_links == 1
                    and not current.attributes & (0x400 | 0x10)
                    and (current.volume_serial, current.file_index_high,
                         current.file_index_low) == identity)

        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except (OSError, ValueError):
            raise ValueError('FINAL_REVIEW_PENDING_SOURCE_UNSTABLE') from None
    except BaseException:
        close(handle)
        raise
    with os.fdopen(descriptor, 'rb') as stream:
        yield stream, still_single_link


class _SnapshotReleaseStore(ReleaseStore):
    """Track SQLite readers because Connection.__exit__ does not close them."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self._snapshot_connections = []

    def _connect_readonly(self):
        connection = super()._connect_readonly()
        self._snapshot_connections.append(connection)
        return connection

    def close_snapshot(self) -> None:
        for connection in reversed(self._snapshot_connections):
            connection.close()
        self._snapshot_connections.clear()


@contextmanager
def _snapshot_pending_store(store: ReleaseStore):
    """Read an unchanged DB-only source through an isolated SQLite copy.

    Any WAL/SHM or rollback journal, including a currently active WAL with
    newer commits, is an explicit evidence gap. Never open SQLite against the
    source directory.
    """
    try:
        source = _require_plain_source_path(store.path)
        with tempfile.TemporaryDirectory(prefix='final-review-pending-') as directory:
            with _source_read_barrier(source) as pinned:
                if pinned is None:
                    raise ValueError('FINAL_REVIEW_PENDING_SOURCE_UNSTABLE')
                stream, still_single_link = pinned
                sidecars = tuple(Path(str(source) + suffix)
                                 for suffix in ('-wal', '-shm', '-journal'))
                if any(_sidecar_present(path) for path in sidecars):
                    raise ValueError('FINAL_REVIEW_PENDING_SOURCE_UNSTABLE')
                before = _path_identity(source)
                opened_stat = os.fstat(stream.fileno())
                if before != (opened_stat.st_dev, opened_stat.st_ino,
                              opened_stat.st_size, opened_stat.st_mtime_ns):
                    raise ValueError('FINAL_REVIEW_PENDING_SOURCE_UNSTABLE')
                before_digest = _stream_digest(stream)
                snapshot_path = Path(directory) / 'release.db'
                stream.seek(0)
                with snapshot_path.open('wb') as target:
                    shutil.copyfileobj(stream, target)
                if (any(_sidecar_present(path) for path in sidecars)
                        or not still_single_link()
                        or _require_plain_source_path(store.path) != source
                        or _path_identity(source) != before
                        or _stream_digest(stream) != before_digest
                        or _file_digest(snapshot_path) != before_digest):
                    raise ValueError('FINAL_REVIEW_PENDING_SOURCE_UNSTABLE')
            snapshot = _SnapshotReleaseStore(snapshot_path)
            try:
                yield snapshot
            finally:
                snapshot.close_snapshot()
    except OSError:
        raise ValueError('FINAL_REVIEW_PENDING_SOURCE_UNSTABLE') from None


@contextmanager
def pending_store_snapshot(store: ReleaseStore):
    """Expose one validated, isolated DB-only image for all pre-adapter reads.

    The context owns its SQLite connections and temporary directory. The
    original directory is never opened through SQLite; WAL or uncertain source
    identity remains an explicit evidence gap.
    """
    if type(store) is not ReleaseStore:
        raise ValueError('FINAL_REVIEW_PENDING_INPUT_INVALID')
    with _snapshot_pending_store(store) as snapshot:
        yield snapshot


def normalize_pending_common_view_from_snapshot(*, snapshot: _SnapshotReleaseStore,
                                                current_payload: Mapping[str, Any],
                                                common_view: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the current producer and COMMON view against this one image."""
    if type(snapshot) is not _SnapshotReleaseStore or not isinstance(common_view, Mapping):
        raise ValueError('FINAL_REVIEW_PENDING_INPUT_INVALID')
    common = common_view.get('common')
    if (common_view.get('ok') is not True
            or common_view.get('external_writes_performed') != []
            or not isinstance(common, Mapping)
            or common.get('status') != 'APPROVAL_REQUIRED'
            or common.get('run') is not None
            or common.get('reconciliation_reference') is not None
            or (common_view.get('marketplace') or {}).get('status') != 'NOT_FROZEN'):
        raise ValueError('FINAL_REVIEW_PENDING_STAGE_NOT_UNAPPROVED')
    view_plan = common.get('plan')
    if not isinstance(view_plan, Mapping):
        raise ValueError('FINAL_REVIEW_PENDING_PLAN_REQUIRED')
    expected = preview_release_plan(current_payload)
    plan_id = expected['plan_id']
    durable = snapshot.get_plan(plan_id)
    if not isinstance(durable, dict) or durable != view_plan:
        raise ValueError('FINAL_REVIEW_PENDING_STORE_VIEW_CONFLICT')
    if (durable.get('status') != 'PENDING_APPROVAL'
            or durable.get('approval') is not None
            or durable.get('approved_at') is not None
            or durable.get('superseded_at') is not None
            or durable.get('superseded_by_plan_id') is not None
            or durable.get('supersede_reason') is not None
            or 'approved' in durable or 'persisted' in durable):
        raise ValueError('FINAL_REVIEW_PENDING_NOT_PRISTINE')
    if (any(durable.get(field) != expected.get(field) for field in _EXACT_PLAN_FIELDS)
            or durable.get('sku_key') != _sku_key(expected['seller_sku'])):
        raise ValueError('FINAL_REVIEW_PENDING_IDENTITY_CONFLICT')
    active = snapshot.active_plan_for_product(expected['product_id'])
    if not isinstance(active, dict) or active != durable:
        raise ValueError('FINAL_REVIEW_PENDING_NOT_CURRENT')
    if snapshot.get_run('release-run:' + expected['payload_digest'][:24]) is not None:
        raise ValueError('FINAL_REVIEW_PENDING_RUN_EXISTS')
    # The projection is an ordinary unpersisted, unapproved preview. The
    # physical pending plan and its reservations remain untouched in the Store.
    normalized = deepcopy(dict(common_view))
    normalized['common']['plan'] = expected
    return normalized


def normalize_pending_common_view(*, store: ReleaseStore, current_payload: Mapping[str, Any],
                                  common_view: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve the public fail-closed wrapper, with one self-owned snapshot."""
    if type(store) is not ReleaseStore:
        raise ValueError('FINAL_REVIEW_PENDING_INPUT_INVALID')
    with pending_store_snapshot(store) as snapshot:
        return normalize_pending_common_view_from_snapshot(
            snapshot=snapshot, current_payload=current_payload, common_view=common_view)


def build_pending_final_review_preview_from_snapshot(*, documents: Mapping[str, Any],
                                                     dashboard: Mapping[str, Any],
                                                     snapshot: _SnapshotReleaseStore,
                                                     current_payload: Mapping[str, Any],
                                                     common_view: Mapping[str, Any]) -> FinalReviewPreview:
    normalized = normalize_pending_common_view_from_snapshot(
        snapshot=snapshot, current_payload=current_payload, common_view=common_view)
    return build_final_review_preview(documents=documents, dashboard=dashboard,
                                      common_view=normalized)


def build_pending_final_review_preview(*, documents: Mapping[str, Any], dashboard: Mapping[str, Any],
                                       store: ReleaseStore, current_payload: Mapping[str, Any],
                                       common_view: Mapping[str, Any]) -> FinalReviewPreview:
    normalized = normalize_pending_common_view(store=store, current_payload=current_payload,
                                               common_view=common_view)
    return build_final_review_preview(documents=documents, dashboard=dashboard,
                                      common_view=normalized)


def require_current_pending_final_review_preview_from_snapshot(preview: FinalReviewPreview, *,
                                                               documents: Mapping[str, Any],
                                                               dashboard: Mapping[str, Any],
                                                               snapshot: _SnapshotReleaseStore,
                                                               current_payload: Mapping[str, Any],
                                                               common_view: Mapping[str, Any]) -> bool:
    normalized = normalize_pending_common_view_from_snapshot(
        snapshot=snapshot, current_payload=current_payload, common_view=common_view)
    return require_current_final_review_preview(preview, documents=documents,
                                                dashboard=dashboard, common_view=normalized)


def require_current_pending_final_review_preview(preview: FinalReviewPreview, *,
                                                 documents: Mapping[str, Any],
                                                 dashboard: Mapping[str, Any],
                                                 store: ReleaseStore,
                                                 current_payload: Mapping[str, Any],
                                                 common_view: Mapping[str, Any]) -> bool:
    normalized = normalize_pending_common_view(store=store, current_payload=current_payload,
                                               common_view=common_view)
    return require_current_final_review_preview(preview, documents=documents,
                                                dashboard=dashboard, common_view=normalized)
