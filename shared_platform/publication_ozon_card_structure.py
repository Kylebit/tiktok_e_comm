"""Startup-pinned Ozon-only structure successor; never publication authority.

The immutable R1 and source/alias binding remain unchanged. A concrete reader
rechecks the signed native adoption and the separately pinned user decision on
every compiler/executor read. JSON supplied by a request cannot install it.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

SCHEMA = 'ozon-card-structure-successor/v1'
PROJECTION_SCHEMA = 'publication-ozon-card-structure/v1'
TARGET = 'ozon:RU'
_INSTALLED = None


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')


def _digest(value):
    return 'sha256:' + hashlib.sha256(_bytes(value)).hexdigest()


def _pin(value):
    value = str(value or '').removeprefix('sha256:')
    if not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('OZON_STRUCTURE_PIN_REQUIRED')
    return value


def independent_card(model_sku):
    if type(model_sku) is not str or not re.fullmatch('[0-9]{1,32}', model_sku):
        raise ValueError('OZON_STRUCTURE_MODEL_INVALID')
    return {'model_sku': model_sku, 'model_attribute_id': 9048,
        'model_name': model_sku + '-refrigerator-cover',
        'quantity_attribute_id': 8513, 'quantity_per_pack': 1}


def build_successor(*, round1_snapshot, identity_binding, source_binding_sha256,
                    decision_receipt, decision_receipt_path, decision_receipt_sha256):
    """Inert local producer; the reader must independently verify these sources."""
    from shared_platform.publication_variant_identity import resolve_explicit_variant_aliases
    r1, binding, receipt = round1_snapshot, identity_binding, decision_receipt
    offer = r1['offer_id']
    identity = resolve_explicit_variant_aliases(offer_id=offer,
        round1_snapshot=r1, identity_binding=binding)
    from shared_platform.release_store import _target_labels
    targets = list(_target_labels(r1['canonical_targets']))
    aliases = identity['canonical_to_provider_sku']
    decision = receipt.get('ozon_decision') or {}
    if (receipt.get('schema_version') != 'orbit-user-decisions/v1'
            or receipt.get('actor') != 'USER' or receipt.get('offer_id') != offer
            or receipt.get('round1_snapshot_digest') != r1['snapshot_digest']
            or (receipt.get('source_identity_binding') or {}).get('sha256') != _pin(source_binding_sha256)
            or (receipt.get('round1_snapshot_file') or {}).get('sha256') != binding['snapshot']['sha256']
            or receipt.get('approved_for_publication') is not False
            or receipt.get('sole_final_review_still_required') is not True
            or decision.get('decision') != 'OZON_THREE_INDEPENDENT_B_CARDS'
            or decision.get('target') != TARGET or decision.get('approved_for_publication') is not False
            or decision.get('preserve_other_14_targets') is not True
            or decision.get('preserve_original_A') is not True
            or decision.get('product_content_cost_parcel_prices_and_gallery_unchanged') is not True
            or decision.get('other_target_structure') != 'ONE_PRODUCT_THREE_VARIANTS'
            or any(type(decision.get(k)) is not int or decision[k] != n for k, n in (
                ('links_per_ozon_shop', 3), ('variants_per_ozon_card', 1), ('quantity_per_variant', 1),
                ('target_count_unchanged', 15), ('category_id', 53968796), ('type_id', 970663046)))
            or type(targets) is not list or len(targets) != 15 or len(set(targets)) != 15
            or TARGET not in targets or 'miaoshou:COMMON' in targets
            or sorted(aliases) != ['0431', '0432', '0433']
            or decision.get('canonical_skus') != sorted(aliases)
            or decision.get('model_skus') != [aliases[k] for k in sorted(aliases)]):
        raise ValueError('OZON_STRUCTURE_EXACT_USER_DECISION_REQUIRED')
    cards = [{'canonical_sku': identity['variant_key_to_internal_sku'][key],
        'variant_key': key, **independent_card(model)}
        for key, model in identity['variant_key_to_model_sku'].items()]
    result = {'schema_version': SCHEMA, 'offer_id': offer,
        'round1_snapshot_digest': r1['snapshot_digest'],
        'source_identity_binding_sha256': _pin(source_binding_sha256),
        'decision_receipt': {'path': str(Path(decision_receipt_path)),
            'sha256': _pin(decision_receipt_sha256)},
        'selected_target_labels': deepcopy(targets),
        'unchanged_target_labels': [t for t in targets if t != TARGET],
        'target_label': TARGET, 'mode': 'THREE_INDEPENDENT_B_CARDS',
        'category_id': '53968796', 'type_id': '970663046', 'cards': cards,
        'other_target_structure': 'ONE_PRODUCT_THREE_VARIANTS',
        'marketplace_approval': False, 'sole_final_review_required': True}
    return {**result, 'successor_digest': _digest(result)}


def projection(successor):
    fields = ('offer_id', 'round1_snapshot_digest', 'source_identity_binding_sha256',
        'selected_target_labels', 'unchanged_target_labels', 'target_label', 'mode',
        'category_id', 'type_id', 'cards', 'other_target_structure', 'marketplace_approval',
        'sole_final_review_required', 'successor_digest')
    result = {'schema_version': PROJECTION_SCHEMA,
        **{k: deepcopy(successor[k]) for k in fields},
        'decision_receipt_sha256': successor['decision_receipt']['sha256']}
    return {**result, 'structure_digest': _digest(result)}


def validate_structure(value, *, offer_id, targets, models, variant_keys=None):
    """Validate durable snapshot shape; live admission is separately required."""
    if isinstance(value,dict) and value.get('schema_version')=='publication-ozon-card-structure/api-selection-v1':
        original=value['original_structure'];reduction=value['api_target_selection']
        validate_structure(original,offer_id=offer_id,targets=original['selected_target_labels'],models=models,variant_keys=variant_keys)
        expected=project_selected_structure(original,reduction)
        if value!=expected or value['selected_target_labels']!=list(targets):
            raise ValueError('OZON_STRUCTURE_API_SCOPE_CHANGED')
        return deepcopy(value)
    row = deepcopy(value)
    if not isinstance(row, dict):
        raise ValueError('OZON_STRUCTURE_PROJECTION_INVALID')
    digest = row.pop('structure_digest', None)
    cards = row.get('cards')
    fields = {'schema_version','offer_id','round1_snapshot_digest','source_identity_binding_sha256',
        'selected_target_labels','unchanged_target_labels','target_label','mode','category_id','type_id',
        'cards','other_target_structure','marketplace_approval','sole_final_review_required',
        'successor_digest','decision_receipt_sha256'}
    if (set(row) != fields or row.get('schema_version') != PROJECTION_SCHEMA or digest != _digest(row)
            or row.get('offer_id') != offer_id or row.get('target_label') != TARGET
            or row.get('selected_target_labels') != list(targets)
            or len(targets) != 15 or len(set(targets)) != 15 or TARGET not in targets
            or row.get('unchanged_target_labels') != [t for t in targets if t != TARGET]
            or row.get('mode') != 'THREE_INDEPENDENT_B_CARDS'
            or row.get('category_id') != '53968796' or row.get('type_id') != '970663046'
            or row.get('other_target_structure') != 'ONE_PRODUCT_THREE_VARIANTS'
            or row.get('marketplace_approval') is not False or row.get('sole_final_review_required') is not True
            or type(cards) is not list or len(cards) != 3 or any(type(c) is not dict for c in cards)
            or [c.get('model_sku') for c in cards] != list(models)
            or variant_keys is not None and [c.get('variant_key') for c in cards] != list(variant_keys)
            or len(set(models)) != 3 or len({c.get('variant_key') for c in cards}) != 3
            or sorted(c.get('canonical_sku', '') for c in cards) != ['0431', '0432', '0433']
            or any(type(c.get('quantity_per_pack')) is not int or
                   type(c.get('model_attribute_id')) is not int or
                   type(c.get('quantity_attribute_id')) is not int or
                   {k:v for k,v in c.items() if k not in {'canonical_sku','variant_key'}}
                   != independent_card(c.get('model_sku')) for c in cards)):
        raise ValueError('OZON_STRUCTURE_PROJECTION_INVALID')
    for key in ('source_identity_binding_sha256', 'decision_receipt_sha256',
                'round1_snapshot_digest', 'successor_digest'):
        _pin(row.get(key))
    return deepcopy(value)


def project_selected_structure(original,reduction):
    from shared_platform.prepared_target_reduction import API_SCHEMA,select_marketplace_targets
    if reduction.get('schema_version')!=API_SCHEMA:raise ValueError('OZON_STRUCTURE_API_SELECTION_REQUIRED')
    labels=select_marketplace_targets(original['selected_target_labels'],offer_id=original['offer_id'],
        r2_identity=reduction['predecessor_lineage']['predecessor_R2_identity'],reduction=reduction)
    from shared_platform.release_store import _target_labels
    labels=list(_target_labels(labels))
    if TARGET not in labels:raise ValueError('OZON_STRUCTURE_TARGET_NOT_SELECTED')
    result={**deepcopy(original),'schema_version':'publication-ozon-card-structure/api-selection-v1',
        'original_structure':deepcopy(original),'api_target_selection':deepcopy(reduction),
        'selected_target_labels':labels,'unchanged_target_labels':[label for label in labels if label!=TARGET]}
    result.pop('structure_digest')
    result['structure_digest']=_digest(result)
    return result


class OzonCardStructureReader:
    def __init__(self, service, *, path, expected_sha256):
        from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
        if type(service) is not AgentPreparedCommonService:
            raise ValueError('OZON_STRUCTURE_NATIVE_SERVICE_REQUIRED')
        self.service, self.path, self.sha256 = service, Path(path), _pin(expected_sha256)
        if not self.path.is_absolute():
            raise ValueError('OZON_STRUCTURE_ABSOLUTE_SIDECAR_REQUIRED')
        self.offer_id = self.read()['offer_id']

    def read(self):
        from shared_platform.immutable_approval_files import require_local_path
        task, record = self.service._current() # signed origin, source pins, genuine task
        require_local_path(self.path, root=self.path.parent)
        raw = self.path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError('OZON_STRUCTURE_SIDECAR_CHANGED')
        row = json.loads(raw.decode('utf-8-sig'))
        facts = record['facts']; directory = Path(self.service.profile.root) / 'reports/product-preparation' / record['offer_id']
        r1path, bindingpath = directory/'round1-approved-snapshot.json', directory/'SOURCE_AND_IDENTITY_BINDING.json'
        for path in (r1path, bindingpath):
            require_local_path(path, root=directory)
        r1 = json.loads(r1path.read_text(encoding='utf-8-sig'))
        binding_raw = bindingpath.read_bytes(); binding = json.loads(binding_raw.decode('utf-8-sig'))
        if (hashlib.sha256(binding_raw).hexdigest() != facts['source_identity_binding_sha256']
                or hashlib.sha256(r1path.read_bytes()).hexdigest() != binding['snapshot']['sha256']
                or r1['snapshot_digest'] != facts['snapshot_digest']
                or sorted(r1['canonical_targets']) != sorted(record['targets'])
                or task['scope']['offer_id'] != record['offer_id']):
            raise ValueError('OZON_STRUCTURE_ADOPTED_SOURCE_CHANGED')
        receipt_path = Path(row['decision_receipt']['path'])
        if not receipt_path.is_absolute():
            raise ValueError('OZON_STRUCTURE_ABSOLUTE_RECEIPT_REQUIRED')
        require_local_path(receipt_path, root=receipt_path.parent)
        receipt_raw = receipt_path.read_bytes()
        if hashlib.sha256(receipt_raw).hexdigest() != row['decision_receipt']['sha256']:
            raise ValueError('OZON_STRUCTURE_USER_RECEIPT_CHANGED')
        rebuilt = build_successor(round1_snapshot=r1, identity_binding=binding,
            source_binding_sha256=facts['source_identity_binding_sha256'],
            decision_receipt=json.loads(receipt_raw.decode('utf-8-sig')),
            decision_receipt_path=receipt_path, decision_receipt_sha256=hashlib.sha256(receipt_raw).hexdigest())
        if rebuilt != row:
            raise ValueError('OZON_STRUCTURE_LINEAGE_CHANGED')
        return projection(row)


def configured_structure(offer_id):
    reader = _INSTALLED
    if reader is None or reader.offer_id != str(offer_id):
        return None
    from modules.products import server
    if Path(server.ROOT).resolve() != Path(reader.service.profile.root).resolve():
        raise ValueError('OZON_STRUCTURE_SERVICE_ROOT_CHANGED')
    return reader.read()


@contextmanager
def installed_structure(service, config):
    global _INSTALLED
    selected = config.get('ozon_card_structure_successor')
    if selected is None:
        yield
        return
    if type(selected) is not dict or set(selected) != {'path','sha256'}:
        raise ValueError('OZON_STRUCTURE_STARTUP_PIN_REQUIRED')
    reader = OzonCardStructureReader(service, path=selected['path'], expected_sha256=selected['sha256'])
    if _INSTALLED is not None:
        raise ValueError('OZON_STRUCTURE_READER_ALREADY_INSTALLED')
    _INSTALLED = reader
    try:
        yield
    finally:
        _INSTALLED = None


def require_verified_snapshot_structure(snapshot):
    supplied = (snapshot.get('product') or {}).get('ozon_card_structure')
    expected = configured_structure(snapshot.get('offer_id'))
    if supplied is not None and supplied.get('schema_version')=='publication-ozon-card-structure/api-selection-v1':
        from shared_platform.prepared_target_consumer import configured_target_consumer
        consumer=configured_target_consumer(snapshot.get('offer_id'))
        if expected is None or consumer is None:raise ValueError('OZON_STRUCTURE_TRUSTED_API_SCOPE_REQUIRED')
        expected=project_selected_structure(expected,consumer.read_verified(snapshot['offer_id']))
    if supplied is None or expected is None or supplied != expected:
        raise ValueError('OZON_STRUCTURE_TRUSTED_BINDING_REQUIRED')
    return validate_structure(supplied, offer_id=snapshot['offer_id'],
        targets=[t['target_label'] for t in snapshot['publication_targets']],
        models=[s['model_sku'] for s in snapshot['skus']],
        variant_keys=[s['variant_key'] for s in snapshot['skus']])


def card_readback_matches(card, observed):
    attributes = observed.get('attributes') or {}
    return (attributes.get('9048') == [{'dictionary_value_id':0,'value':card['model_name']}]
            or attributes.get('9048') == [{'value':card['model_name']}]) and (
        attributes.get('8513') == [{'dictionary_value_id':0,'value':'1'}]
        or attributes.get('8513') == [{'value':'1'}])
