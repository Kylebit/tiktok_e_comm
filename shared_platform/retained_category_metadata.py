"""Exact retained 403 category metadata projection; no marketplace authority.

Frozen PROPOSED records stay unchanged. Metadata authenticates the technical
candidate, not a human decision, publication permission or regional eligibility.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

OFFER = '4039931504'
ROOT = Path('C:/Users/Windows11/Documents/Codex/2026-10-04/task/4039931504-preparation/official-category-followup-20261004T1720Z')
SUMMARY_SHA = '00ee98706e170f1b4957260cf12e8f2ef32f98ae1e0845e6e52fad01963c7f2c'
PINS = {
    'current-product-category-and-history-followup.json': SUMMARY_SHA,
    'readback-summary.json': 'd58b2c4b54609604f010ce6359d0453ed3dc79bbc1c235cd33bb0c31a8c7641d',
    'explicit-runtime-ozon-category-readback.json': '991b617287aa06cb4853b8cd778e2c20b814f1ee2bd0acc953033049e3b090fd',
    'ozon-toilet-paper-holder-attributes-readback.json': '8195496318a50f3afb7a810c1908d56f808811530e2ba883901c6d1b9536e38b',
    'ozon-holder-required-dictionary-readback.json': '085dedb10465d4095cb79b1a65aef5954a02e0ecc25ae2ef001d49a6256f1b4d',
    'ozon-toilet-paper-holder-attributes.json': '423c73f20ed31daab9f04fdf8ce49ba750439b6ef2cc5c3bac77b9babc733371',
    'ozon-explicit-runtime-description-category-tree.json': 'de770952148a607c28e8c87b4ce36a3eded024262c03c74b4ce4151b242fd77c',
}

def _read(name, expected=None):
    path = ROOT / name
    raw = path.read_bytes()
    if path.resolve().parent != ROOT.resolve() or hashlib.sha256(raw).hexdigest() != (expected or PINS[name]):
        raise ValueError('RETAINED_OFFICIAL_CATEGORY_BYTES_CHANGED')
    return json.loads(raw)

def _bound(first_review, common_payload, store, connection):
    from shared_platform.original_prepared_continuation import OriginalPreparedTaskContinuation, configured_original_continuation
    from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS, validate_r2_identity
    from shared_platform.native_common_budget_facts import _validate_plan_source
    from shared_platform.release_store import ReleaseStore
    continuation = configured_original_continuation(OFFER)
    if (type(continuation) is not OriginalPreparedTaskContinuation or type(store) is not ReleaseStore
            or connection is None or continuation.pins.offer_id != OFFER
            or store.path.resolve() != continuation.release_store.path.resolve()):
        raise ValueError('RETAINED_OFFICIAL_CATEGORY_CONCRETE_SOURCE_REQUIRED')
    refs = continuation.pins.documents
    directory = continuation.r2_profile.root / 'reports/product-preparation' / OFFER
    if len(refs) != len(R2_DOCUMENTS) or {ref.path.resolve() for ref in refs} != {directory / name for name in R2_DOCUMENTS.values()}:
        raise ValueError('RETAINED_OFFICIAL_CATEGORY_R2_SET_CHANGED')
    documents = {key: json.loads(next(ref for ref in refs if ref.path.name == name).read(continuation.r2_profile.root))
                 for key, name in R2_DOCUMENTS.items()}
    identity = validate_r2_identity(documents)
    source = _validate_plan_source(connection, common_payload)
    common_identity = identity
    successor = getattr(continuation, 'image_successor', None)
    if successor is not None:
        from shared_platform.original_images_successor import OriginalImagesSuccessor
        if type(successor) is not OriginalImagesSuccessor or successor.continuation is not continuation:
            raise ValueError('RETAINED_OFFICIAL_CATEGORY_IMAGE_SUCCESSOR_CHANGED')
        if identity != successor.current_identity or documents != successor.current_documents():
            raise ValueError('RETAINED_OFFICIAL_CATEGORY_IMAGE_SUCCESSOR_DOCUMENTS_CHANGED')
        common_identity = successor.old_identity
        if documents['round1_snapshot'] != successor.predecessor_documents()['round1_snapshot']:
            raise ValueError('RETAINED_OFFICIAL_CATEGORY_IMAGE_SUCCESSOR_R1_CHANGED')
    if (documents['first_review'] != first_review or identity != json.loads(continuation.pins.r2_identity_json)
            or source is None or source['offer_id'] != OFFER
            or source['round1_snapshot'] != documents['round1_snapshot']
            or source['prepared_reference'] != continuation.pins.prepared_reference
            or common_payload['r3_stage_binding']['r2_identity'] != common_identity):
        raise ValueError('RETAINED_OFFICIAL_CATEGORY_FROZEN_SOURCE_CHANGED')
    state = json.loads(continuation.pins.original_state.read(continuation.original_profile.root))
    if str(state['source']['collect_box']['source_item_id']) != '1050277205528':
        raise ValueError('RETAINED_OFFICIAL_CATEGORY_SUPPLIER_CHANGED')
    return state

def project_retained_category(label, category, *, first_review, common_payload, store, connection):
    """Adapt only exact old display records; never change their PROPOSED status."""
    if (first_review.get('offer_id') != OFFER or category.get('status') != 'PROPOSED'
            or category.get('evidence_digest') != 'sha256:' + SUMMARY_SHA):
        return category
    state = _bound(first_review, common_payload, store, connection)
    frozen = next((row for row in first_review['targets'] if row['target'] == label), None)
    if frozen is None or frozen['category'] != category:
        raise ValueError('RETAINED_OFFICIAL_CATEGORY_TARGET_CHANGED')
    summary = _read('current-product-category-and-history-followup.json')
    if summary['COMMON'] != OFFER or summary['source_1688'] != '1050277205528':
        raise ValueError('RETAINED_OFFICIAL_CATEGORY_IDENTITY_CHANGED')
    projection = deepcopy(category)
    proof = {'schema_version': 'retained-official-category-projection/v1', 'offer_id': OFFER,
             'target': label, 'source_evidence_digest': category['evidence_digest'],
             'source_status': category['status'], 'final_marketplace_approval': False,
             'regional_publication_verified': False, 'required_attributes': []}
    if label.startswith('tiktok:'):
        leaf = summary['tiktok_candidate']
        if leaf['category_id'] != 853256 or leaf['name'] != 'Bathroom Gadgets' or category['candidate'] != 'Bathroom Gadgets (853256)':
            raise ValueError('RETAINED_TIKTOK_CATEGORY_SELECTION_CHANGED')
        shops = [row for row in leaf['shops'] if row['target'] == label]
        if len(shops) != 1:
            raise ValueError('RETAINED_TIKTOK_CATEGORY_TARGET_UNKNOWN')
        shop = shops[0]
        readback = _read('readback-summary.json')
        calls = [row for row in readback['read_calls'] if row.get('target') == label]
        if (len(calls) != 1 or calls[0]['status'] != 'SUCCESS'
                or calls[0]['endpoint'] != '/open/v1/product/collect_box/tiktok/collect_box/get_category_metadata'
                or calls[0]['body'] != {'site': label.split(':')[1].split('_')[-1], 'cid': 853256, 'shopIds': [int(shop['shop_id'])]}
                or Path(shop['metadata_path']).resolve() != (ROOT / ('tiktok-' + label.split(':')[1] + '-metadata.json')).resolve()):
            raise ValueError('RETAINED_TIKTOK_OFFICIAL_REQUEST_CHANGED')
        metadata = _read(Path(shop['metadata_path']).name, shop['metadata_sha256'])
        mandatory = [row for group in ('categoryProductAttrList', 'categorySaleAttrList')
                     for row in metadata.get(group, []) if row.get('isMandatory') is True]
        if label == 'tiktok:GB':
            if (len(mandatory) != 1 or mandatory[0]['attrId'] != '102255'
                    or mandatory[0]['attributeType'] != 3 or mandatory[0]['isCertificationAttr'] is not False
                    or mandatory[0]['values'] != [{'id': '1000256', 'name': '1', 'valueNameAlias': '1'}]):
                raise ValueError('RETAINED_GB_ENUM_METADATA_CHANGED')
            proof['required_attributes'] = [{'attribute_id': '102255', 'name': 'Batch number',
                'value_id': '1000256', 'value': '1', 'value_kind': 'OFFICIAL_SINGLE_OPTION_ENUM',
                'manufacturing_batch_value': None}]
        elif mandatory:
            raise ValueError('RETAINED_TIKTOK_REQUIRED_ATTRIBUTES_CHANGED')
        proof.update(shop_id=shop['shop_id'], metadata_sha256=shop['metadata_sha256'], category_id='853256')
        projection['candidate'] = '853256 · Bathroom Gadgets'
    elif label == 'ozon:RU':
        if category['candidate'] != 'Toilet Paper Holder; description_category_id17027905/type_id92011':
            raise ValueError('RETAINED_OZON_CATEGORY_SELECTION_CHANGED')
        leaf = summary['ozon_candidate']
        tree = _read('explicit-runtime-ozon-category-readback.json')
        raw_tree = _read('ozon-explicit-runtime-description-category-tree.json')
        attributes = _read('ozon-toilet-paper-holder-attributes-readback.json')
        raw_attributes = _read('ozon-toilet-paper-holder-attributes.json')
        dictionaries = _read('ozon-holder-required-dictionary-readback.json')
        credential = Path(tree['credential_reference'])
        if (credential.resolve() != Path('D:/Users/Windows11/Desktop/Agent_PR/tiktok_e_comm/modules/ozon/legacy_webapp/data/credentials.local.json').resolve()
                or hashlib.sha256(credential.read_bytes()).hexdigest() != '1d47669e405f0383d3a7bdd7f23d9983c2bec20cf375d453622a065122688bcf'):
            raise ValueError('RETAINED_OZON_ACCOUNT_SOURCE_CHANGED')
        if (leaf['description_category_id'] != 17027905 or leaf['type_id'] != 92011
                or tree['account_identity_digest'] != leaf['configured_native_account_digest']
                or attributes['account_identity_digest'] != tree['account_identity_digest']
                or dictionaries['account_identity_digest'] != tree['account_identity_digest']
                or attributes['request_body'] != {'description_category_id': 17027905, 'type_id': 92011, 'language': 'EN'}
                or {row['id'] for row in attributes['attributes'] if row['is_required']} != {9048, 8229, 85}
                or attributes['raw_attribute_sha256'] != PINS['ozon-toilet-paper-holder-attributes.json']
                or {row['id'] for row in raw_attributes if row['is_required']} != {9048, 8229, 85}
                or tree['tree_sha256'] != PINS['ozon-explicit-runtime-description-category-tree.json']):
            raise ValueError('RETAINED_OZON_OFFICIAL_METADATA_CHANGED')
        selected = []
        for attr_id, value_id, value in ((8229, 92011, 'Toilet Paper Holder'), (85, 126745801, 'Нет бренда')):
            reads = [row for row in dictionaries['reads'] if row['request_body']['attribute_id'] == attr_id
                     and row['request_body']['description_category_id'] == 17027905 and row['request_body']['type_id'] == 92011]
            if len(reads) != 1 or reads[0]['status'] != 'OFFICIAL_DICTIONARY_OBSERVED' or sum(row['id'] == value_id and row['value'] == value for row in reads[0]['rows']) != 1:
                raise ValueError('RETAINED_OZON_DICTIONARY_VALUE_CHANGED')
            selected.append({'attribute_id': attr_id, 'dictionary_value_id': value_id, 'value': value})
        code = state['source']['attributes'].get('货号')
        if code != '260506-1' or state['source']['attributes'].get('品牌') != '无':
            raise ValueError('RETAINED_OZON_SOURCE_MODEL_OR_BRAND_CHANGED')
        if common_payload['seller_sku'] != '0989':
            raise ValueError('RETAINED_OZON_MODEL_SKU_CHANGED')
        proof.update(category_id='17027905', type_id='92011', account_identity_digest=tree['account_identity_digest'],
            source_model_basis={'original_supplier_item_code': code, 'manufacturing_model': None},
            required_attributes=selected + [{'attribute_id': 9048, 'value': '0989-toilet-paper-holder',
                'value_kind': 'EXISTING_MODEL_SKU_PROFILE_RULE', 'manufacturer_model_claim': False}])
        projection['candidate'] = '17027905 / type92011 · Toilet Paper Holder'
    else:
        raise ValueError('RETAINED_OFFICIAL_CATEGORY_PLATFORM_UNKNOWN')
    projection['retained_metadata_projection'] = proof
    return projection
