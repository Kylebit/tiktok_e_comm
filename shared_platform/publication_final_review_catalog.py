"""DB-only Product Center catalog image for the pending final-review seam.

The source is never passed to SQLite. An active journal is an evidence gap,
because a raw copy of the main database could omit committed WAL rows.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import tempfile

from shared_platform.publication_final_review_pending import (
    _file_digest, _path_identity, _require_plain_source_path,
    _sidecar_present, _source_read_barrier, _stream_digest,
)


@contextmanager
def catalog_snapshot(database_path: str | Path):
    """Yield a private shop.db image after a stable Windows DB-only copy.

    All SQLite connections on the yielded path must close before context exit.
    The existing ReleaseStore source barrier provides a read handle that denies
    concurrent writers, checks single-link identity, and rejects reparse paths.
    """
    try:
        source = _require_plain_source_path(Path(database_path))
        with tempfile.TemporaryDirectory(prefix='final-review-catalog-') as directory:
            with _source_read_barrier(source) as (stream, still_single_link):
                sidecars = tuple(Path(str(source) + suffix)
                                 for suffix in ('-wal', '-shm', '-journal'))
                if any(_sidecar_present(path) for path in sidecars):
                    raise ValueError('FINAL_REVIEW_CATALOG_SOURCE_UNSTABLE')
                before = _path_identity(source)
                opened = os.fstat(stream.fileno())
                if before != (opened.st_dev, opened.st_ino,
                              opened.st_size, opened.st_mtime_ns):
                    raise ValueError('FINAL_REVIEW_CATALOG_SOURCE_UNSTABLE')
                digest = _stream_digest(stream)
                copied = Path(directory) / 'shop.db'
                stream.seek(0)
                with copied.open('wb') as target:
                    shutil.copyfileobj(stream, target)
                if (any(_sidecar_present(path) for path in sidecars)
                        or not still_single_link()
                        or _require_plain_source_path(Path(database_path)) != source
                        or _path_identity(source) != before
                        or _stream_digest(stream) != digest
                        or _file_digest(copied) != digest):
                    raise ValueError('FINAL_REVIEW_CATALOG_SOURCE_UNSTABLE')
            yield copied
    except (OSError, ValueError) as error:
        if isinstance(error, ValueError) and not str(error).startswith('FINAL_REVIEW_PENDING_SOURCE_UNSTABLE'):
            raise
        raise ValueError('FINAL_REVIEW_CATALOG_SOURCE_UNSTABLE') from error
