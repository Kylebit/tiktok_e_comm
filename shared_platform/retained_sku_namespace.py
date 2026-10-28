"""Current local SKU check; normal release-plan transaction owns reservation."""
from pathlib import Path
import json
import sqlite3
import hashlib

from shared_platform.internal_catalog_sku import aliases_for


class RetainedSkuNamespaceReader:
    def __init__(self, continuation, *, catalog_path):
        from shared_platform.original_prepared_continuation import OriginalPreparedTaskContinuation
        if type(continuation) is not OriginalPreparedTaskContinuation:
            raise ValueError('RETAINED_SKU_CONCRETE_CONTINUATION_REQUIRED')
        self.continuation = continuation
        self.catalog_path = Path(catalog_path).resolve(strict=True)

    def read_verified(self, sku):
        from shared_platform.catalog_sku_costs import members
        from shared_platform.release_control import _local_seller_sku_reservations
        continuation = self.continuation
        continuation.verify_sources()
        aliases = aliases_for(sku)
        if not aliases or len(str(sku)) != 4:
            raise ValueError('RETAINED_SKU_EXACT_INTERNAL_ID_REQUIRED')
        with sqlite3.connect(self.catalog_path.as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            if members(db, sku):
                raise ValueError('RETAINED_SKU_CATALOG_CONFLICT')
        marks = ','.join('?' for _ in aliases)
        with continuation.release_store._connect_readonly() as db:
            db.execute('BEGIN')
            rows = db.execute('SELECT product_id FROM release_sku_reservations WHERE status=? AND sku_key IN (' + marks + ')',
                              ('ACTIVE', *aliases)).fetchall()
            source_rows = db.execute('SELECT r.reservation_digest FROM release_source_sku_reservation_keys k '
                'JOIN release_source_sku_reservations r ON r.reservation_digest=k.reservation_digest '
                'WHERE k.status=? AND k.sku_key IN (' + marks + ')', ('ACTIVE', *aliases)).fetchall()
            if any(str(row['product_id']) != continuation.pins.offer_id for row in rows):
                raise ValueError('RETAINED_SKU_RELEASE_RESERVATION_CONFLICT')
            owned_source_reservations = set()
            for plan in db.execute('SELECT payload_json,payload_digest FROM release_plans WHERE product_id=? AND status<>?',
                                   (continuation.pins.offer_id, 'SUPERSEDED')):
                if hashlib.sha256(plan['payload_json'].encode()).hexdigest() != plan['payload_digest']:
                    raise ValueError('RETAINED_SKU_OWNER_PLAN_BYTES_CHANGED')
                payload = json.loads(plan['payload_json'])
                stage = payload.get('r3_stage_binding') or {}
                if stage.get('r2_identity') == json.loads(continuation.pins.r2_identity_json):
                    from shared_platform.release_store import _sku_assignment_contract, _sku_reservation_contract
                    lineage = payload.get('sku_lineage') or {}
                    if lineage:
                        reservation = _sku_reservation_contract(lineage=lineage, reservation=lineage['reservation'],
                            assignment=_sku_assignment_contract(lineage['assignment']))
                        owned_source_reservations.add(reservation.payload()['reservation_digest'])
            if any(row['reservation_digest'] not in owned_source_reservations for row in source_rows):
                raise ValueError('RETAINED_SKU_RELEASE_SOURCE_RESERVATION_CONFLICT')
        for role in continuation.pins.roles:
            reservations = _local_seller_sku_reservations(role.profile.root, exclude_offer_id=continuation.pins.offer_id)
            if any(str(row['seller_sku']) in aliases for row in reservations):
                raise ValueError('RETAINED_SKU_LOCAL_APPROVED_STATE_CONFLICT')
        with sqlite3.connect(continuation.engine.store.path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            for row in db.execute('SELECT task_id,scope_json,state FROM workbench_execution WHERE task_id<>?', (continuation.pins.task_id,)):
                if row['state'] not in ('completed', 'cancelled') and any(value in aliases for value in json.loads(row['scope_json']).get('skus', [])):
                    raise ValueError('RETAINED_SKU_OTHER_TASK_CONFLICT')
        return {'status': 'LOCAL_RETAINED_NAMESPACE_CHECKED', 'sku': sku,
                'allocation_performed': False, 'external_history_proven': False, 'execution_authority': False}
