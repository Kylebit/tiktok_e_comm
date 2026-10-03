"""Local R1 evidence binding. A self-consistent file is never source authority."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Callable, Mapping

SCHEMA = 'shopee-category-official-review/v2'
OBSERVATION_SCHEMA = 'round1-shopee-category-observation/v1'
SCOPE = 'SHOPEE_GLOBAL_CATEGORY'
REGIONS = {'PH', 'MY', 'TH', 'VN'}
LIMIT = 2 * 1024 * 1024
Resolver = Callable[[str], Mapping | None]
FIELDS = {'offer_id', 'product_center_revision', 'requested_targets', 'source_region',
          'observation_scope', 'category_input_digest', 'observer_reference', 'observed_at',
          'authority', 'category', 'attribute_tree', 'selected_attributes',
          'attributes_complete', 'required_attribute_count', 'missing_required_attributes',
          'regional_publishability', 'business_write_count', 'auth_writes', 'official_read_count'}


class CategoryEvidenceError(ValueError):
    """Safe constant code, suitable for a decision packet."""


def digest(value):
    try:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        raise CategoryEvidenceError('CATEGORY_JSON_INVALID') from None
    return 'sha256:' + hashlib.sha256(data.encode()).hexdigest()


def _object(value, keys, code):
    if type(value) is not dict or set(value) != keys:
        raise CategoryEvidenceError(code)


def _positive(value):
    return type(value) is int and value > 0


def shopee_targets(review):
    selection = review.get('target_selection')
    if type(selection) is not dict:
        raise CategoryEvidenceError('CATEGORY_TARGETS_INVALID')
    values = selection.get('requested')
    if type(values) is not list:
        raise CategoryEvidenceError('CATEGORY_TARGETS_INVALID')
    targets = []
    for item in values:
        if type(item) is not str:
            raise CategoryEvidenceError('CATEGORY_TARGETS_INVALID')
        if item.lower().startswith(('shopee:', 'shopee_')):
            region = item[7:].upper()
            if region not in REGIONS:
                raise CategoryEvidenceError('CATEGORY_TARGETS_INVALID')
            targets.append('shopee:' + region)
    if len(set(targets)) != len(targets):
        raise CategoryEvidenceError('CATEGORY_TARGETS_INVALID')
    return targets


def input_digest(review):
    offer = review.get('offer_id'); revision = review.get('product_center_revision')
    context = review.get('category_review_context')
    facts = review.get('product_facts')
    if (type(offer) is not str or not re.fullmatch(r'[0-9]{1,32}', offer) or int(offer) <= 0
            or type(revision) is not int or revision < 0):
        raise CategoryEvidenceError('CATEGORY_PRODUCT_IDENTITY_INVALID')
    if (type(context) is not dict or set(context) != {'source_region', 'observation_scope'}
            or type(context.get('source_region')) is not str
            or context.get('source_region') not in REGIONS or context.get('observation_scope') != SCOPE):
        raise CategoryEvidenceError('CATEGORY_SOURCE_REGION_REQUIRED')
    if (type(facts) is not dict or type(facts.get('title')) is not str or not facts['title'].strip()
            or type(facts.get('category_semantic')) is not str or not facts['category_semantic'].strip()):
        raise CategoryEvidenceError('CATEGORY_INPUT_FACTS_REQUIRED')
    shopee_targets(review)
    return digest({'offer_id': offer, 'product_center_revision': revision,
                   'title': facts['title'], 'category_semantic': facts['category_semantic'],
                   'requested_targets': review['target_selection']['requested'], **context})


def read_receipt(path: Path):
    """Read one bounded reference only; this function grants no trust."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise CategoryEvidenceError('CATEGORY_DUPLICATE_JSON_FIELD')
            result[key] = value
        return result
    try:
        with path.open('rb') as stream:
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise CategoryEvidenceError('CATEGORY_RECEIPT_TOO_LARGE')
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(CategoryEvidenceError('CATEGORY_JSON_INVALID')))
        if type(value) is not dict:
            raise CategoryEvidenceError('CATEGORY_JSON_INVALID')
        return value
    except CategoryEvidenceError:
        raise
    except (OSError, ValueError):
        raise CategoryEvidenceError('CATEGORY_RECEIPT_UNAVAILABLE') from None


def build_receipt(observation):
    """Form an untrusted reference from an observation; validation needs a resolver."""
    _object(observation, FIELDS | {'schema_version'}, 'CATEGORY_OBSERVATION_INVALID')
    if observation['schema_version'] != OBSERVATION_SCHEMA:
        raise CategoryEvidenceError('CATEGORY_OBSERVATION_INVALID')
    receipt = {key: deepcopy(observation[key]) for key in FIELDS}
    receipt.update(schema_version=SCHEMA, observation_digest=digest(observation))
    receipt['receipt_digest'] = digest(receipt)
    return receipt


def validate_receipt(review, receipt, *, observation_resolver: Resolver | None = None):
    """Validate exact local facts and resolve their provenance outside the JSON file."""
    if type(receipt) is dict and receipt.get('schema_version') == 'shopee-category-official-review/v1':
        raise CategoryEvidenceError('CATEGORY_LEGACY_UNBOUND')
    from shared_platform.round1_category_observations import RECEIPT_SCHEMA, EXTRA_FIELDS, OBSERVATION_SCHEMA as RICH_SCHEMA
    rich = type(receipt) is dict and receipt.get('schema_version') == RECEIPT_SCHEMA
    fields = FIELDS | EXTRA_FIELDS if rich else FIELDS
    _object(receipt, fields | {'schema_version', 'observation_digest', 'receipt_digest'}, 'CATEGORY_RECEIPT_INVALID')
    document = deepcopy(receipt)
    if document['schema_version'] != (RECEIPT_SCHEMA if rich else SCHEMA):
        raise CategoryEvidenceError('CATEGORY_RECEIPT_INVALID')
    unsigned = {k: v for k, v in document.items() if k != 'receipt_digest'}
    if digest(unsigned) != document['receipt_digest']:
        raise CategoryEvidenceError('CATEGORY_RECEIPT_DIGEST_MISMATCH')
    if (document['offer_id'] != review.get('offer_id')
            or type(document['product_center_revision']) is not int
            or document['product_center_revision'] != review.get('product_center_revision')
            or document['requested_targets'] != shopee_targets(review)
            or not document['requested_targets']
            or document['category_input_digest'] != input_digest(review)
            or document['source_region'] != review['category_review_context']['source_region']
            or document['observation_scope'] != SCOPE):
        raise CategoryEvidenceError('CATEGORY_CONTEXT_MISMATCH')
    if (document['authority'] != 'shopee_official_category_get'
            or document['regional_publishability'] != 'NOT_VERIFIED'
            or type(document['business_write_count']) is not int or document['business_write_count'] != 0
            or not isinstance(document['observer_reference'], str)
            or not re.fullmatch(r'category-observation:[A-Za-z0-9_-]{1,96}', document['observer_reference'])):
        raise CategoryEvidenceError('CATEGORY_OBSERVATION_INVALID')
    try:
        observed = datetime.fromisoformat(document['observed_at'])
        if observed.tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError):
        raise CategoryEvidenceError('CATEGORY_OBSERVATION_TIME_INVALID') from None
    for name in ('auth_writes', 'official_read_count'):
        count = document[name]
        if count is not None and (type(count) is not int or count < (1 if name == 'official_read_count' else 0)):
            raise CategoryEvidenceError('CATEGORY_REQUEST_COUNTS_INVALID')
    category = document['category']
    _object(category, {'id', 'name', 'path', 'path_complete', 'is_leaf', 'publishable'}, 'CATEGORY_LEAF_INVALID')
    if (not _positive(category['id']) or type(category['name']) is not str or not category['name'].strip()
            or any(category[k] is not True for k in ('path_complete', 'is_leaf', 'publishable'))
            or type(category['path']) is not list or not category['path']):
        raise CategoryEvidenceError('CATEGORY_LEAF_INVALID')
    path_ids = []
    for row in category['path']:
        _object(row, {'id', 'name'}, 'CATEGORY_LEAF_INVALID')
        if not _positive(row['id']) or type(row['name']) is not str or not row['name'].strip():
            raise CategoryEvidenceError('CATEGORY_LEAF_INVALID')
        path_ids.append(row['id'])
    if len(set(path_ids)) != len(path_ids) or category['path'][-1] != {'id': category['id'], 'name': category['name']}:
        raise CategoryEvidenceError('CATEGORY_LEAF_INVALID')
    if rich:
        from shared_platform.round1_category_observations import validate_attributes
        validate_attributes(document)
    else:
        _validate_legacy_attributes(document)
    observation = {k: deepcopy(document[k]) for k in fields}
    observation['schema_version'] = RICH_SCHEMA if rich else OBSERVATION_SCHEMA
    if digest(observation) != document['observation_digest']:
        raise CategoryEvidenceError('CATEGORY_OBSERVATION_DIGEST_MISMATCH')
    if observation_resolver is None:
        raise CategoryEvidenceError('SOURCE_UNVERIFIED')
    try:
        trusted = deepcopy(observation_resolver(document['observer_reference']))
    except Exception:
        raise CategoryEvidenceError('SOURCE_UNVERIFIED') from None
    if type(trusted) is not dict or trusted != observation:
        raise CategoryEvidenceError('CATEGORY_SOURCE_REFERENCE_MISMATCH')
    return document


def _validate_legacy_attributes(document):
    tree = document['attribute_tree']; selected = document['selected_attributes']
    if (document['attributes_complete'] is not True or document['missing_required_attributes'] != []
            or type(tree) is not list or type(selected) is not list
            or type(document['required_attribute_count']) is not int):
        raise CategoryEvidenceError('CATEGORY_ATTRIBUTES_INVALID')
    options = {}; mandatory = set()
    for row in tree:
        _object(row, {'attribute_id', 'is_mandatory', 'value_ids'}, 'CATEGORY_ATTRIBUTES_INVALID')
        key = row['attribute_id']; values = row['value_ids']
        if (not _positive(key) or key in options or type(row['is_mandatory']) is not bool
                or type(values) is not list or any(not _positive(v) for v in values)
                or len(set(values)) != len(values)):
            raise CategoryEvidenceError('CATEGORY_ATTRIBUTES_INVALID')
        options[key] = set(values)
        if row['is_mandatory']:
            mandatory.add(key)
    chosen = set()
    for row in selected:
        _object(row, {'attribute_id', 'value_ids'}, 'CATEGORY_ATTRIBUTES_INVALID')
        key = row['attribute_id']; values = row['value_ids']
        if (not _positive(key) or key in chosen or key not in options or type(values) is not list or not values
                or any(not _positive(v) for v in values) or len(set(values)) != len(values)
                or not set(values).issubset(options[key])):
            raise CategoryEvidenceError('CATEGORY_ATTRIBUTES_INVALID')
        chosen.add(key)
    if not mandatory.issubset(chosen) or len(mandatory) != document['required_attribute_count']:
        raise CategoryEvidenceError('CATEGORY_ATTRIBUTES_INVALID')
