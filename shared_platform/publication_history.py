"""Read historical publication evidence without altering current release authority."""
import os
from pathlib import Path
import re
import sqlite3

from shared_platform.product_publication_reports import ProductPublicationReportStore, ProductPublicationReportIntegrityError


def _existing(variable, *, directory=False):
    path = Path(os.environ.get(variable, ''))
    if not path.is_absolute():
        raise ValueError('history path must be explicit and absolute')
    path = path.resolve(strict=True)
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError('invalid history source')
    return path


def history(offer_id):
    if not re.fullmatch(r'[0-9]{1,32}', str(offer_id)):
        raise ValueError('invalid offer_id')
    result = {'ok': True, 'offer_id': str(offer_id), 'items': [], 'count': 0,
              'display_mode': 'HISTORICAL_READ_ONLY', 'execution_authority': False}
    try:
        database = _existing('ORBIT_REPORT_STORE_PATH')
        assets = _existing('ORBIT_PUBLICATION_HISTORY_ROOT', directory=True)
    except (OSError, ValueError):
        return dict(result, history_status='UNAVAILABLE')
    try:
        # Store read methods use mode=ro and validate both index and immutable files.
        store = ProductPublicationReportStore(database, reports_root=assets)
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
            references = connection.execute(
                'SELECT report_id FROM product_publication_reports WHERE offer_id=? '
                'ORDER BY created_at DESC, report_id DESC LIMIT 100', (str(offer_id),)).fetchall()
        rows = []
        blocked = 0
        for (report_id,) in references:
            try:
                row = store.get_report(report_id=report_id, offer_id=str(offer_id))
                if row is None:
                    blocked += 1
                else:
                    rows.append(row)
            except (OSError, ValueError, ProductPublicationReportIntegrityError):
                blocked += 1
        items = [{key: row.get(key) for key in ('report_id', 'run_id', 'offer_id', 'revision',
                                               'plan_id', 'status', 'created_at', 'targets', 'summary')}
                 for row in rows]
        status = ('PARTIALLY_BLOCKED' if items else 'INTEGRITY_BLOCKED') if blocked else 'AVAILABLE'
        return dict(result, history_status=status, items=items, count=len(items), blocked_count=blocked)
    except (OSError, ValueError, sqlite3.Error, ProductPublicationReportIntegrityError):
        return dict(result, history_status='INTEGRITY_BLOCKED')
