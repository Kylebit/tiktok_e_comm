"""Technical metadata supplement for an unchanged, completed native COMMON.

This is neither a category choice nor approval. The original R1 identifiers,
R2 identity and stored COMMON completion are revalidated at every consumption.
Only the server writer's explicitly admitted original capture manifest is an
input. Its safe projections are NOT raw provider responses: response hashes
remain original ledger references, never falsely re-computed from projections.
There is no HTTP, registration, approval or automatic source-admission endpoint.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re

SCHEMA = 'r3-technical-category-evidence/v1'
FILES = {'discovery', 'ledger', 'tiktok_taxonomy', 'tiktok_metadata',
         'ozon_tree', 'ozon_metadata', 'ozon_dictionaries'}
TREE = '/open/v1/product/collect_box/tiktok/collect_box/get_category_tree_by_site'
META = '/open/v1/product/collect_box/tiktok/collect_box/get_category_metadata'


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _digest(value):
    return 'sha256:' + hashlib.sha256(_bytes(value)).hexdigest()


def _fail(code):
    raise ValueError('R3_TECHNICAL_CATEGORY_' + code)


def _checked(path):
    path = Path(os.path.abspath(path))
    for parent in reversed((path, *path.parents)):
        if parent.exists():
            stat = parent.lstat()
            if parent.is_symlink() or getattr(stat, 'st_file_attributes', 0) & 1024:
                _fail('REPARSE_PATH')
    return path


def _read(path):
    path = _checked(path)
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        _fail('EVIDENCE_CHANGED_DURING_READ')
    return raw


def _selection(category, label):
    text = str(category.get('candidate') or '')
    match = re.fullmatch(r'([0-9]+)(?:\s*/\s*type\s*([0-9]+))?\s*[|·]\s*(.+)', text)
    if (category.get('status') not in {'PROPOSED', 'DECISION_REQUIRED'} or not match
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(category.get('evidence_digest') or ''))):
        _fail('FROZEN_SELECTION_REQUIRED:' + label)
    cid, type_id, _ = match.groups()
    if int(cid) <= 0 or (type_id is not None and int(type_id) <= 0):
        _fail('FROZEN_SELECTION_INVALID')
    if (label == 'ozon:RU') != (type_id is not None):
        _fail('FROZEN_TYPE_REQUIRED:' + label)
    return cid, type_id


def _context(store, db, documents, common_plan_id, common_run_id):
    from shared_platform.release_store import ReleaseStore
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    from shared_platform.native_common_retained_completion import read_retained_completion
    from shared_platform.native_common_budget_facts import _validate_plan_source
    from shared_platform.publication_r3_image_bridge import validate_r2_identity
    if type(store) is not ReleaseStore or not store.path.is_file():
        _fail('SERVICE_STORE_REQUIRED')
    reader = NativeCommonSourceReader(store)
    reader.validate_context(db)
    completion = read_retained_completion(reader, db, common_plan_id, common_run_id)
    row = db.execute('SELECT payload_json,payload_digest FROM release_plans WHERE plan_id=?',
                     (common_plan_id,)).fetchone()
    payload = json.loads(row['payload_json'])
    source = _validate_plan_source(db, payload)
    run = db.execute('SELECT technical_admission_json FROM release_runs WHERE run_id=?', (common_run_id,)).fetchone()
    signing = (json.loads(run['technical_admission_json']) or {}).get('native_account_context')
    if type(signing) is not dict:
        _fail('ORIGINAL_COMMON_SIGNING_CONTEXT_REQUIRED')
    identity = validate_r2_identity(documents)
    if (source is None or source['round1_snapshot'] != documents['round1_snapshot']
            or source['offer_id'] != documents['first_review']['offer_id']
            or identity != payload['r3_stage_binding']['r2_identity']
            or _digest(documents['first_review']) != documents['round1_snapshot']['first_review_digest']
            or _digest(payload).removeprefix('sha256:') != row['payload_digest']):
        _fail('FROZEN_COMMON_SOURCE_CHANGED')
    return {'offer_id': source['offer_id'], 'release_store_path': str(_checked(store.path)), 'common_plan_id': common_plan_id,
        'common_run_id': common_run_id, 'common_payload_digest': row['payload_digest'],
        'common_readback_digest': completion.readback_digest,
        'prepared_reference': source['prepared_reference'],
        'account_identity_digest': source['account_identity_digest'],
        'common_signing_context_digest': signing['common_signing_context_digest'],
        'common_config_raw_digest': signing['config_raw_digest'],
        'r2_identity': identity, 'round1_snapshot_digest': source['round1_snapshot_digest']}


def _path(store, offer_id, plan_id):
    if not re.fullmatch(r'[0-9]+', str(offer_id)):
        _fail('OFFER_INVALID')
    return _checked(store.path.absolute().parent / 'r3-category-evidence' / offer_id /
                    (hashlib.sha256(plan_id.encode()).hexdigest() + '.json'))


def _observed(receipt, *, provider, endpoint, body, ledger):
    if type(receipt) is not dict or receipt.get('event') != 'response':
        _fail('ORIGINAL_RESPONSE_REQUIRED')
    key = receipt.get('request_scope_digest')
    intents = [r for r in ledger if r.get('event') == 'intent' and r.get('request_scope_digest') == key]
    responses = [r for r in ledger if r.get('event') != 'intent' and r.get('request_scope_digest') == key]
    if (len(intents) != 1 or len(responses) != 1 or responses[0] != receipt
            or any(receipt.get(k) != v for k, v in {'provider': provider, 'endpoint': endpoint}.items())
            or any(intents[0].get(k) != v for k, v in {'provider': provider, 'endpoint': endpoint, 'body': body}.items())
            or not re.fullmatch(r'[0-9a-f]{64}', str(receipt.get('response_sha256_canonical') or ''))):
        _fail('ORIGINAL_REQUEST_RESPONSE_CHANGED')
    scope = {'provider': provider, 'endpoint': endpoint, 'body': body}
    if 'approved_read_pass' in intents[0]:
        scope['approved_read_pass'] = intents[0]['approved_read_pass']
    if _digest(scope).removeprefix('sha256:') != key:
        _fail('ORIGINAL_REQUEST_SCOPE_CHANGED')
    return {'provider': provider, 'endpoint': endpoint, 'request_body': deepcopy(body),
        'request_scope_digest': key, 'response_sha256_canonical': receipt['response_sha256_canonical'],
        'response_digest_basis': 'ORIGINAL_CAPTURE_LEDGER_REFERENCE_RAW_RESPONSE_NOT_RETAINED',
        'observed_at': receipt['as_of_utc']}


def _evidence(refs):
    if type(refs) is not dict or set(refs) != FILES:
        _fail('EXACT_EVIDENCE_SET_REQUIRED')
    result = {}
    for key, ref in refs.items():
        raw = _read(ref['path'])
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:
            _fail('ORIGINAL_FILE_CHANGED:' + key)
        result[key] = ([json.loads(line) for line in raw.splitlines() if line.strip()]
                       if key == 'ledger' else json.loads(raw))
    return result


def _targets(documents, evidence):
    from modules.miaoshou.tiktok_publisher import EXPECTED_SHOP_ID_BY_TARGET
    ledger = evidence['ledger']
    output = {}
    for frozen in documents['first_review']['targets']:
        label = frozen['target']
        if not label.startswith('tiktok:') and label != 'ozon:RU':
            continue
        cid, type_id = _selection(frozen['category'], label)
        calls = []
        if label.startswith('tiktok:'):
            rows = [r for r in evidence['tiktok_metadata']['rows'] if r['target_label'] == label]
            if len(rows) != 1:
                _fail('EXACT_TARGET_METADATA_REQUIRED:' + label)
            row = rows[0]; shop = EXPECTED_SHOP_ID_BY_TARGET.get(label)
            site = label.rsplit('_', 1)[-1].split(':')[-1]
            if (str(row['shop_id']) != str(shop) or row['site'] != site or str(row['category_id']) != cid):
                _fail('EXACT_SHOP_CATEGORY_CHANGED')
            calls.append(_observed(row['receipt'], provider='miaoshou', endpoint=META,
                body={'site': site, 'cid': int(cid), 'shopIds': [int(shop)]}, ledger=ledger))
            trees = [r for r in evidence['tiktok_taxonomy']['sites'] if r['site'] == site]
            if len(trees) != 1:
                _fail('SITE_TREE_REQUIRED')
            tree = trees[0]
            calls.append(_observed(tree['receipt'], provider='miaoshou', endpoint=TREE, body={'site': site}, ledger=ledger))
            leaves = [r for r in tree['nodes'] if str(r['node'].get('cid')) == cid]
            if (len(leaves) != 1 or leaves[0]['node'].get('disabled') is not False
                    or any(any(str(n['id']) == cid for n in r['path'][:-1]) for r in tree['nodes'])):
                _fail('ENABLED_LEAF_REQUIRED')
            leaf = leaves[0]; metadata = row['category_metadata']; required = []
            for group in ('categoryProductAttrList', 'categorySaleAttrList'):
                if type(metadata.get(group)) is not list:
                    _fail('METADATA_SCHEMA_CHANGED')
                for attr in metadata[group]:
                    if attr.get('isMandatory') is not True:
                        continue
                    values = attr.get('values')
                    if (attr.get('attributeType') != 3 or attr.get('isCertificationAttr') is not False
                            or type(values) is not list or len(values) != 1
                            or not str(values[0].get('id') or '').isdigit()):
                        _fail('REQUIRED_ATTRIBUTE_SELECTION_MISSING:' + label)
                    required.append({'attribute_id': str(attr['attrId']), 'name': attr['name'],
                        'value_id': str(values[0]['id']), 'value': values[0]['name'],
                        'value_kind': 'OFFICIAL_SINGLE_OPTION_ENUM', 'manufacturing_claim': False})
            config = metadata.get('categoryConfig') or {}
            package_required = any(str(config.get(k, '0')) == '1' for k in
                ('packageDimensionIsRequired', 'packageDimensionRequired'))
            parcels = []
            if package_required:
                for sku in documents['first_review']['product_facts']['skus']:
                    dims = sku.get('package_cm')
                    try:
                        valid = type(dims) is list and len(dims) == 3 and all(
                            Decimal(str(v)).is_finite() and Decimal(str(v)) > 0 for v in dims)
                    except (InvalidOperation, ValueError):
                        valid = False
                    if not valid:
                        _fail('FROZEN_PACKAGE_DIMENSIONS_REQUIRED:' + label)
                    parcels.append({'model_sku': sku['seller_sku'], 'package_cm': deepcopy(dims)})
            category = {'id': cid, 'name': leaf['node']['name'], 'path': deepcopy(leaf['path'])}
            extra = {'shop_id': str(shop), 'site': site, 'package_dimensions_required': package_required,
                'frozen_package_dimensions': parcels}
        else:
            tree = evidence['ozon_tree']['ozon']
            calls.append(_observed(tree['receipt'], provider='ozon', endpoint='/v1/description-category/tree',
                body={'language': 'EN'}, ledger=ledger))
            leaves = [r for r in tree['matched_nodes'] if str(r['node'].get('type_id')) == type_id
                and any(str(n['id']) == cid for n in r['path'][:-1])]
            if len(leaves) != 1 or leaves[0]['node'].get('disabled') is not False:
                _fail('OZON_ENABLED_TYPE_REQUIRED')
            leaf = leaves[0]; metadata = evidence['ozon_metadata']
            if str(metadata['description_category_id']) != cid or str(metadata['type_id']) != type_id:
                _fail('OZON_CATEGORY_TYPE_CHANGED')
            calls.append(_observed(metadata['receipt'], provider='ozon', endpoint='/v1/description-category/attribute',
                body={'description_category_id': int(cid), 'type_id': int(type_id), 'language': 'EN'}, ledger=ledger))
            attrs = {a['id']: a for a in metadata['attributes'] if a.get('is_required') is True}
            if set(attrs) != {85, 8229, 9048} or attrs[9048].get('type') != 'String' or attrs[9048].get('dictionary_id') != 0:
                _fail('OZON_REQUIRED_ATTRIBUTES_CHANGED')
            required = []
            for aid, expected_id, expected_name in ((85, 126745801, None), (8229, int(type_id), leaf['node']['type_name'])):
                rows = [r for r in evidence['ozon_dictionaries']['rows'] if r['attribute_id'] == aid]
                if len(rows) != 1:
                    _fail('OZON_DICTIONARY_REQUIRED')
                row = rows[0]
                calls.append(_observed(row['receipt'], provider='ozon', endpoint='/v1/description-category/attribute/values/search',
                    body={'attribute_id': aid, 'description_category_id': int(cid), 'type_id': int(type_id),
                          'language': 'EN', 'limit': 20, 'value': row['search']}, ledger=ledger))
                matches = [v for v in row['values'] if v['id'] == expected_id]
                if (len(matches) != 1 or expected_name is not None and matches[0]['value'].casefold() != expected_name.casefold()
                        or aid == 85 and matches[0]['value'].casefold() not in {'no brand', 'нет бренда'}):
                    _fail('OZON_EXACT_DICTIONARY_VALUE_REQUIRED')
                required.append({'attribute_id': aid, 'dictionary_value_id': expected_id, 'value': matches[0]['value']})
            from shared_platform.product_publication_live_dependencies import _OZON_IMPORT_PROFILE_REGISTRY
            slug = _OZON_IMPORT_PROFILE_REGISTRY.get((int(cid), int(type_id)))
            if slug is None:
                _fail('OZON_EXISTING_IMPORT_PROFILE_REQUIRED')
            seller_sku = documents['first_review']['product_facts']['seller_sku']
            if not re.fullmatch(r'[0-9]{4}', seller_sku):
                _fail('FROZEN_SELLER_SKU_REQUIRED')
            required.append({'attribute_id': 9048, 'value': seller_sku + '-' + slug,
                'value_kind': 'EXISTING_IMPORT_PROFILE_SELLER_SKU_RULE',
                'manufacturer_model_claim': False})
            category = {'id': cid, 'type_id': type_id, 'name': leaf['node']['type_name'], 'path': deepcopy(leaf['path'])}
            extra = {}
        output[label] = {'frozen_category_digest': _digest(frozen['category']), 'category': category,
            'required_attributes': required, 'original_reads': calls, **extra}
    if not output:
        _fail('TARGETS_REQUIRED')
    return output


def _receipt(context, documents, manifest):
    if (type(manifest) is not dict or set(manifest) != {'schema_version','source_binding','evidence_files','capture_scripts'}
            or manifest['schema_version'] != 'root-owned-original-category-safe-capture/v1'
            or manifest['source_binding'] != context
            or type(manifest['capture_scripts']) is not list or len(manifest['capture_scripts']) != 3):
        _fail('ROOT_ADMITTED_CAPTURE_MANIFEST_REQUIRED')
    # The explicit local writer supplies the previously registered ORIGINAL
    # digests. Do not manufacture admission by hashing arbitrary caller paths.
    for ref in manifest['capture_scripts']:
        if (set(ref) != {'path','sha256'} or Path(ref['path']).suffix != '.py'
                or hashlib.sha256(_read(ref['path'])).hexdigest() != ref['sha256']):
            _fail('ORIGINAL_CAPTURE_PRODUCER_CHANGED')
    refs = manifest['evidence_files']
    evidence = _evidence(refs)
    discovery = evidence['discovery']
    if discovery.get('offer_id') != context['offer_id']:
        _fail('DISCOVERY_OFFER_CHANGED')
    bindings = discovery.get('config_binding')
    if type(bindings) is not list or len(bindings) != 2:
        _fail('ORIGINAL_CONFIG_BINDING_REQUIRED')
    for binding in bindings:
        if hashlib.sha256(_read(binding['path'])).hexdigest() != binding['sha256']:
            _fail('ORIGINAL_CONFIG_CHANGED')
    by_name = {Path(b['path']).name: b for b in bindings}
    if set(by_name) != {'miaoshou.local.json','ozon.local.json'}:
        _fail('ORIGINAL_CONFIG_IDENTITIES_REQUIRED')
    miaoshou = by_name['miaoshou.local.json']
    value = json.loads(_read(miaoshou['path']))
    app_scope = hashlib.sha256(('miaoshou-app-scope:' + str(value.get('app_id') or '')).encode()).hexdigest()
    if (miaoshou['sha256'] != context['common_config_raw_digest']
            or app_scope != context['common_signing_context_digest']
            or value.get('base_url','https://openapi-erp.91miaoshou.com').rstrip('/') != 'https://openapi-erp.91miaoshou.com'):
        _fail('ORIGINAL_COMMON_APP_IDENTITY_CHANGED')
    from shared_platform.ozon_runtime_credentials import required_pinned_ozon_credentials
    pinned = required_pinned_ozon_credentials().public()
    ozon = by_name['ozon.local.json']; value = json.loads(_read(ozon['path']))
    if (str(value.get('client_id')) != pinned['ozon_account_id']
            or ozon.get('account_id') != pinned['ozon_account_id']
            or ozon['sha256'] != pinned['ozon_credentials_sha256']):
        _fail('ORIGINAL_OZON_ACCOUNT_IDENTITY_CHANGED')
    body = {'schema_version': SCHEMA, 'execution_authority': False, 'final_approval': False,
        'source': context, 'capture_manifest': deepcopy(manifest), 'config_bindings': deepcopy(bindings),
        'evidence_kind': 'ROOT_ADMITTED_ORIGINAL_SAFE_PROJECTIONS_WITH_LEDGER_REFERENCES_NOT_RAW_RESPONSES',
        'targets': _targets(documents, evidence)}
    return {**body, 'receipt_digest': _digest(body)}


def _checked_receipt(store, db, context, documents, manifest):
    """Reuse the one SKU correction proof, retaining the old capture literally."""
    if manifest.get('schema_version') != 'sku-only-category-capture-derivation/v1':
        return _receipt(context, documents, manifest)
    from shared_platform.publication_rounds import REPORTS_ROOT
    from shared_platform.sku_only_publication_correction import verify_generation
    record = verify_generation(store, reports_root=REPORTS_ROOT, offer_id=context['offer_id'], connection=db)
    qa_proof = documents['image_qa'].get('sku_only_derivation') or {}
    if (record is None or manifest.get('correction_digest') != record['correction_digest']
            or (qa_proof.get('correction_binding') or {}).get('correction_digest') != record['correction_digest']
            or manifest.get('source_binding') != context):
        _fail('SKU_CORRECTION_BINDING_CHANGED')
    old_documents = qa_proof['original_documents']
    common = record['common']
    old_context = _context(store, db, old_documents, common['plan_id'], common['run_id'])
    original_path = _path(store, context['offer_id'], common['plan_id'])
    raw = _read(original_path)
    if (manifest.get('original_receipt') != {'path': str(original_path), 'sha256': hashlib.sha256(raw).hexdigest()}
            or set(manifest) != {'schema_version','source_binding','original_receipt','correction_digest'}):
        _fail('SKU_CORRECTION_ORIGINAL_RECEIPT_CHANGED')
    original = json.loads(raw)
    if original != _receipt(old_context, old_documents, original['capture_manifest']):
        _fail('SKU_CORRECTION_ORIGINAL_CAPTURE_CHANGED')
    # Re-use exactly the original safe projections and request/response ledger.
    # No capture-manifest source binding is rewritten to claim another GET.
    targets = _targets(documents, _evidence(original['capture_manifest']['evidence_files']))
    from shared_platform.sku_only_publication_correction import replace_sku_identity
    expected_targets = replace_sku_identity(original['targets'], record['old_sku'], record['new_sku'])
    # Attribute 9048 is the existing deterministic Seller-SKU import-profile
    # identity, not a new manufacturer/model or category decision.
    for attribute in expected_targets.get('ozon:RU', {}).get('required_attributes', []):
        if attribute.get('attribute_id') == 9048 and attribute.get('value_kind') == 'EXISTING_IMPORT_PROFILE_SELLER_SKU_RULE':
            value = attribute.get('value') or ''
            if not value.startswith(record['old_sku'] + '-'):
                _fail('SKU_CORRECTION_ORIGINAL_MODEL_IDENTITY_CHANGED')
            attribute['value'] = record['new_sku'] + value[len(record['old_sku']):]
    if targets != expected_targets:
        _fail('SKU_CORRECTION_CATEGORY_FACTS_CHANGED')
    body = {'schema_version': SCHEMA, 'execution_authority': False, 'final_approval': False,
        'source': context, 'capture_manifest': deepcopy(manifest),
        'config_bindings': deepcopy(original['config_bindings']), 'targets': targets,
        'evidence_kind': 'SKU_ONLY_RETAINED_ORIGINAL_CAPTURE_NO_NEW_PROVIDER_READ',
        'sku_only_correction': {'correction_digest': record['correction_digest'],
            'original_receipt_digest': original['receipt_digest'], 'provider_reads_added': 0}}
    return {**body, 'receipt_digest': _digest(body)}


def prepare_sku_only_category_supplement(store, *, documents, common_plan_id, common_run_id):
    """Root-owned local reseal after the corrected COMMON official readback."""
    from shared_platform.publication_rounds import REPORTS_ROOT
    from shared_platform.sku_only_publication_correction import verify_generation
    record = verify_generation(store, reports_root=REPORTS_ROOT,
                               offer_id=documents['first_review']['offer_id'])
    if record is None:
        _fail('SKU_CORRECTION_REQUIRED')
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        context = _context(store, db, documents, common_plan_id, common_run_id)
    original_path = _path(store, record['offer_id'], record['common']['plan_id'])
    manifest = {'schema_version': 'sku-only-category-capture-derivation/v1',
        'source_binding': context, 'correction_digest': record['correction_digest'],
        'original_receipt': {'path': str(original_path), 'sha256': hashlib.sha256(_read(original_path)).hexdigest()}}
    return prepare_technical_category_supplement(store, documents=documents,
        common_plan_id=common_plan_id, common_run_id=common_run_id, capture_manifest=manifest)


def prepare_technical_category_supplement(store, *, documents, common_plan_id, common_run_id, capture_manifest):
    """Server-writer-only local producer with an explicit original input seal.

    The root writer admits the exact previously captured package. This function
    validates that fixed input; it does not discover/admit arbitrary paths and
    is not wired to HTTP. Never change R1, COMMON, approvals or the DB.
    """
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        context = _context(store, db, documents, common_plan_id, common_run_id)
        receipt = _checked_receipt(store, db, context, documents, capture_manifest)
    path = _path(store, context['offer_id'], common_plan_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _checked(path)
    raw = _bytes(receipt)
    if path.exists():
        if _read(path) != raw:
            _fail('SUPPLEMENT_ALREADY_EXISTS_DIFFERENT')
    else:
        with path.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    return {'path': str(path), 'receipt_digest': receipt['receipt_digest'],
            'targets': list(receipt['targets']), 'execution_authority': False}


def read_technical_categories(*, documents, common_plan_id, store, db):
    """One read/validation per stage transaction, never one taxonomy per shop."""
    if not common_plan_id or store is None:
        return None
    path = _path(store, documents['first_review']['offer_id'], common_plan_id)
    if not path.is_file():
        return None
    supplied = json.loads(_read(path))
    context = _context(store, db, documents, common_plan_id, supplied['source']['common_run_id'])
    expected = _checked_receipt(store, db, context, documents, supplied['capture_manifest'])
    if supplied != expected:
        _fail('SUPPLEMENT_CHANGED')
    return expected


def project_technical_category(label, category, *, receipt):
    """Project only an already revalidated local stage receipt, no new choice."""
    row = receipt['targets'][label]
    if row['frozen_category_digest'] != _digest(category):
        _fail('FROZEN_CATEGORY_CHANGED')
    selected = row['category']
    display = selected['id'] + (' / type' + selected['type_id'] if 'type_id' in selected else '') + ' · ' + selected['name']
    return {**deepcopy(category), 'status': 'TECHNICAL_EVIDENCE_BOUND',
        'candidate': display, 'evidence_digest': receipt['receipt_digest'],
        'authority': 'ORIGINAL_OFFICIAL_METADATA_TECHNICAL_SUPPLEMENT_NOT_APPROVAL',
        'note': 'Original selected category is unchanged; original official metadata and mandatory-field evidence are bound at R3. This is not human approval or publication authority.',
        'technical_metadata_projection': {'schema_version': SCHEMA, 'receipt_digest': receipt['receipt_digest'],
            'source': deepcopy(receipt['source']), 'target': label, **deepcopy(row)}}
