"""Service-owned R1 captures; no caller JSON ingestion or credential persistence."""
from copy import deepcopy
import re

RECEIPT_SCHEMA = 'shopee-category-official-review/v3'
OBSERVATION_SCHEMA = 'round1-shopee-category-observation/v2'
EXTRA_FIELDS = {'account_identity_digest', 'request_trace', 'review_input'}

UI_CAPTURE_SCHEMA = 'round1-category-capture-request/v2'
OPTIONS_SCHEMA = 'round1-category-options-observation/v1'
OPTIONS_REQUEST_SCHEMA = 'round1-category-options-request/v1'
SELECTION_REQUEST_SCHEMA = 'round1-category-capture-request/v3'


def bounded_official_response(value):
    """Technical resource limits, not Shopee business limits."""
    import json
    from shared_platform.round1_category_evidence import CategoryEvidenceError
    stack=[(value,0)];entries=0;size=0
    while stack:
        item,depth=stack.pop();entries+=1
        if depth>32 or entries>50000:
            raise CategoryEvidenceError('CATEGORY_RESPONSE_LIMIT')
        if type(item) is dict:
            if any(type(k) is not str for k in item):
                raise CategoryEvidenceError('CATEGORY_RESPONSE_INVALID')
            stack.extend((x,depth+1) for pair in item.items() for x in pair)
        elif type(item) is list:
            stack.extend((x,depth+1) for x in item)
        elif type(item) is str:
            size+=len(item.encode('utf-8'))
        elif item is not None and type(item) not in {bool,int,float}:
            raise CategoryEvidenceError('CATEGORY_RESPONSE_INVALID')
        if size>4*1024*1024:
            raise CategoryEvidenceError('CATEGORY_RESPONSE_LIMIT')
    try:
        if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8'))>4*1024*1024:
            raise CategoryEvidenceError('CATEGORY_RESPONSE_LIMIT')
    except (ValueError,TypeError):
        raise CategoryEvidenceError('CATEGORY_RESPONSE_INVALID') from None
    return value


def validate_options(record):
    from shared_platform.round1_category_evidence import digest,input_digest,CategoryEvidenceError
    from modules.shopee.global_plan_candidate import _normalize_compact_attribute_rows
    keys={'schema_version','options_reference','review_input','input_digest','source_region','account_identity_digest',
          'observed_at','options','recommended_category_ids','official_read_count','options_digest'}
    try:
        if type(record) is not dict or set(record)!=keys or record['schema_version']!=OPTIONS_SCHEMA:
            raise ValueError()
        if digest({k:v for k,v in record.items() if k!='options_digest'})!=record['options_digest']:
            raise ValueError()
        if input_digest(record['review_input'])!=record['input_digest'] or record['source_region']!=record['review_input']['category_review_context']['source_region']:
            raise ValueError()
        if not re.fullmatch(r'category-options:[A-Za-z0-9_-]{1,96}',record['options_reference']):
            raise ValueError()
        if not re.fullmatch(r'sha256:[0-9a-f]{64}',record['account_identity_digest']):
            raise ValueError()
        from datetime import datetime
        if datetime.fromisoformat(record['observed_at']).tzinfo is None:
            raise ValueError()
        recommended=record['recommended_category_ids']
        if (type(recommended) is not list or not 1<=len(recommended)<=10
                or any(type(x) is not int or x<=0 for x in recommended) or len(set(recommended))!=len(recommended)
                or type(record['official_read_count']) is not int or not 3<=record['official_read_count']<=21):
            raise ValueError()
        if type(record['options']) is not list or not 1<=len(record['options'])<=10:
            raise ValueError()
        seen=set()
        for option in record['options']:
            if set(option)!={'category_id','name','path','attribute_tree','category_identity_digest'}:
                raise ValueError()
            if type(option['category_id']) is not int or option['category_id']<=0 or option['category_id'] in seen:
                raise ValueError()
            seen.add(option['category_id'])
            if option['category_id'] not in recommended or type(option['name']) is not str or not option['name'].strip():
                raise ValueError()
            path=option['path']
            if type(path) is not list or not path:raise ValueError()
            ids=set()
            for node in path:
                if (type(node) is not dict or set(node)!={'category_id','name'} or type(node['category_id']) is not int
                        or node['category_id']<=0 or node['category_id'] in ids or type(node['name']) is not str or not node['name'].strip()):
                    raise ValueError()
                ids.add(node['category_id'])
            if option['path'][-1]!={'category_id':option['category_id'],'name':option['name']}:
                raise ValueError()
            if list(_normalize_compact_attribute_rows(option['attribute_tree']))!=option['attribute_tree']:
                raise ValueError()
            if digest({k:v for k,v in option.items() if k!='category_identity_digest'})!=option['category_identity_digest']:
                raise ValueError()
    except Exception:
        raise CategoryEvidenceError('CATEGORY_OPTIONS_INVALID') from None
    return deepcopy(record)


def options_projection(record):
    from shared_platform.round1_category_evidence import digest
    row=validate_options(record);options=[]
    for option in row['options']:
        attributes=[]
        for attribute in option['attribute_tree']:
            aid=digest({'category':option['category_identity_digest'],'attribute':attribute})
            attributes.append(dict(attribute_identity_digest=aid,label=attribute['original_attribute_name'],required=attribute['is_mandatory'],
                kind=attribute['input_type'],values=[dict(option_identity_digest=digest({'attribute':aid,'value':v}),
                label=v['original_value_name'],unit=v.get('value_unit')) for v in attribute['attribute_value_list']]))
        options.append(dict(category_identity_digest=option['category_identity_digest'],name=option['name'],path=option['path'],
                            recommended=option['category_id'] in row['recommended_category_ids'],attributes=attributes))
    return dict(schema_version='round1-category-options-projection/v1',status='AWAITING_SELECTION',options_reference=row['options_reference'],
                options_digest=row['options_digest'],source_region=row['source_region'],account_identity_digest=row['account_identity_digest'],
                observed_at=row['observed_at'],options=options,selected_category_identity=None,selected_attributes=[])


def resolve_options_selection(record, category_identity, selections):
    from shared_platform.round1_category_evidence import digest,CategoryEvidenceError
    from modules.shopee.global_plan_candidate import _revalidate_attribute_rows
    record=validate_options(record)
    try:
        option=next(x for x in record['options'] if x['category_identity_digest']==category_identity)
        if type(selections) is not list:raise ValueError()
        by_identity={digest({'category':category_identity,'attribute':a}):a for a in option['attribute_tree']}
        result=[];seen=set()
        for selected in selections:
            aid=selected['attribute_identity_digest'];attribute=by_identity[aid]
            if aid in seen:raise ValueError()
            seen.add(aid)
            if attribute['input_type']=='TEXT':
                if set(selected)!={'attribute_identity_digest','text_value'}:raise ValueError()
                values=[dict(value_id=attribute['attribute_value_list'][0]['value_id'],original_value_name=selected['text_value'])]
            else:
                if set(selected)!={'attribute_identity_digest','option_identity_digests'}:raise ValueError()
                offered={digest({'attribute':aid,'value':v}):v for v in attribute['attribute_value_list']}
                values=[deepcopy(offered[key]) for key in selected['option_identity_digests']]
                values.sort(key=lambda x:(x['value_id'],x['original_value_name'],x.get('value_unit') or ''))
            result.append(dict(attribute_id=attribute['attribute_id'],attribute_value_list=values))
        result.sort(key=lambda x:x['attribute_id'])
        if result or any(a['is_mandatory'] for a in option['attribute_tree']):
            result=_revalidate_attribute_rows(result,attribute_tree=tuple(option['attribute_tree']))
        return deepcopy(option),result
    except Exception:
        raise CategoryEvidenceError('CATEGORY_SELECTION_INVALID') from None


def workspace_context(review, account, store):
    from shared_platform.round1_category_evidence import input_digest, digest, shopee_targets, CategoryEvidenceError
    if not shopee_targets(review):
        raise CategoryEvidenceError('CATEGORY_SHOPEE_TARGET_REQUIRED')
    identity = input_digest(review)
    records = store.category_observation_index(offer_id=review['offer_id'], revision=review['product_center_revision'],
        input_digest=identity, region=review['category_review_context']['source_region'])
    for record in records:
        if (record['status']=='REUSABLE' and account['readiness']=='READY'
                and record['account_identity_digest']!=account['account_identity_digest']):
            record['status']='ACCOUNT_MISMATCH'
    return dict(schema_version='round1-category-workspace-context/v1', offer_id=review['offer_id'],
        product_center_revision=review['product_center_revision'], requested_targets=review['target_selection']['requested'],
        product_identity={key:review['product_facts'].get(key) for key in ('title','seller_sku')},
        context_digest=digest(dict(input_digest=identity, account_identity_digest=account['account_identity_digest'],readiness=account['readiness'])),
        source_account=account, observations=records, category_intent={'status':'INTENT_REQUIRED'},
        automatic_capture=False)


def review_input(review):
    from shared_platform.round1_category_evidence import input_digest
    input_digest(review)
    return deepcopy({key: review[key] for key in ('offer_id', 'product_center_revision',
                     'target_selection', 'product_facts', 'category_review_context')})


def build_receipt(observation):
    from shared_platform.round1_category_evidence import FIELDS, digest, CategoryEvidenceError
    if type(observation) is not dict or set(observation) != FIELDS | EXTRA_FIELDS | {'schema_version'}:
        raise CategoryEvidenceError('CATEGORY_OBSERVATION_INVALID')
    if observation['schema_version'] != OBSERVATION_SCHEMA:
        raise CategoryEvidenceError('CATEGORY_OBSERVATION_INVALID')
    receipt = deepcopy(observation)
    receipt['schema_version'] = RECEIPT_SCHEMA
    receipt['observation_digest'] = digest(observation)
    receipt['receipt_digest'] = digest(receipt)
    return receipt


def validate_attributes(document):
    from shared_platform.round1_category_evidence import CategoryEvidenceError, input_digest
    from modules.shopee.global_plan_candidate import (
        _normalize_compact_attribute_rows, _revalidate_attribute_rows,
    )
    try:
        tree = document['attribute_tree']
        if type(tree) is not list:
            raise ValueError()
        normalized = _normalize_compact_attribute_rows(tree)
        if list(normalized) != tree:
            raise ValueError()
        selected = document['selected_attributes']
        if not tree and selected == []:
            validated = []
        elif selected == [] and not any(row['is_mandatory'] for row in tree):
            validated = []
        else:
            validated = _revalidate_attribute_rows(selected, attribute_tree=normalized)
        if (validated != selected or document['attributes_complete'] is not True
                or document['missing_required_attributes'] != []
                or type(document['required_attribute_count']) is not int
                or document['required_attribute_count'] != sum(row['is_mandatory'] for row in tree)):
            raise ValueError()
        account = document['account_identity_digest']
        if type(account) is not str or not re.fullmatch(r'sha256:[0-9a-f]{64}', account):
            raise ValueError()
        if input_digest(document['review_input']) != document['category_input_digest']:
            raise ValueError()
        trace = document['request_trace']
        if type(trace) is not list or len(trace) != document['official_read_count'] or not trace:
            raise ValueError()
        for row in trace:
            if (type(row) is not dict or set(row) != {'endpoint', 'response_digest'}
                    or row['endpoint'] not in {'/api/v2/global_product/get_category', '/api/v2/global_product/get_attribute_tree'}
                    or type(row['response_digest']) is not str
                    or not re.fullmatch(r'sha256:[0-9a-f]{64}', row['response_digest'])):
                raise ValueError()
    except Exception:
        raise CategoryEvidenceError('CATEGORY_CAPTURE_INVALID') from None


def validate_record(observation):
    from shared_platform.round1_category_evidence import validate_receipt
    receipt = build_receipt(observation)
    validate_receipt(observation['review_input'], receipt, observation_resolver=lambda _: observation)
    return deepcopy(observation)


def resolve_record(store, reference, *, review, account_identity_digest):
    from shared_platform.round1_category_evidence import CategoryEvidenceError, input_digest
    from shared_platform.release_store import ReleaseStoreError
    try:
        observed = store.round1_category_observation(reference)
    except ReleaseStoreError:
        raise CategoryEvidenceError('CATEGORY_RECORD_CORRUPT') from None
    if observed is None:
        raise CategoryEvidenceError('CATEGORY_OBSERVATION_NOT_FOUND')
    if observed['account_identity_digest'] != account_identity_digest:
        raise CategoryEvidenceError('CATEGORY_ACCOUNT_MISMATCH')
    if observed['category_input_digest'] != input_digest(review):
        raise CategoryEvidenceError('CATEGORY_CONTEXT_MISMATCH')
    return observed


def default_resolver(review, account_identity_digest):
    """Fixed internal store; callers cannot select paths or register source JSON."""
    from shared_platform.release_store import default_release_store
    frozen = review_input(review)
    return lambda reference: resolve_record(default_release_store(), reference, review=frozen,
                                           account_identity_digest=account_identity_digest)
