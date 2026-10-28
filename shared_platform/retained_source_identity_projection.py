"""Exact retained prepared-source projection; no writes/provider calls."""
from copy import deepcopy
import json
from pathlib import Path
from typing import Mapping

def retained_prepared_source(continuation,state,frozen_round1):
    from shared_platform.original_prepared_continuation import OriginalPreparedTaskContinuation
    from domains.product_operations import resolve_source_product_identity
    if type(continuation) is not OriginalPreparedTaskContinuation:
        raise ValueError('REAL_ORIGINAL_CONTINUATION_SOURCE_REQUIRED')
    original=json.loads(continuation.pins.original_state.read(continuation.original_profile.root))
    r1_refs=[ref for ref in continuation.pins.documents if ref.path.name=='round1-approved-snapshot.json']
    if len(r1_refs)!=1:raise ValueError('EXACT_PINNED_R1_SOURCE_DOCUMENT_REQUIRED')
    r1=json.loads(r1_refs[0].read(continuation.r2_profile.root))
    if frozen_round1 is not None and frozen_round1!=r1:
        raise ValueError('RETAINED_R1_SOURCE_DOCUMENT_CHANGED')
    if original.get('offer_id')!=continuation.pins.offer_id or state.get('offer_id')!=continuation.pins.offer_id or r1.get('offer_id')!=continuation.pins.offer_id:
        raise ValueError('RETAINED_SOURCE_OFFER_CHANGED')
    def inputs(document):
        source=document.get('source')
        if not isinstance(source,Mapping) or source.get('source_mode')!='prepared_common_state':
            raise ValueError('EXACT_RETAINED_PREPARED_SOURCE_REQUIRED')
        if any(not isinstance(source.get(key),Mapping) for key in ('collect_box','precollect','source_record')):
            raise ValueError('RETAINED_SOURCE_IDENTITY_INPUTS_INCOMPLETE')
        return {key:deepcopy(dict(source[key])) for key in ('collect_box','precollect','source_record')}|{'source_authority':source.get('source_authority')}
    old_inputs,current_inputs=inputs(original),inputs(state)
    original_resolution=resolve_source_product_identity(**old_inputs)
    current_resolution=resolve_source_product_identity(**current_inputs)
    if not original_resolution.ready or not current_resolution.ready:
        raise ValueError('RETAINED_SOURCE_IDENTITY_NOT_READY')
    if original_resolution.identity.payload()!=current_resolution.identity.payload():
        raise ValueError('RETAINED_R2_SOURCE_IDENTITY_DRIFT')
    digests=set()
    def walk(value):
        if isinstance(value,Mapping):
            for key,item in value.items():
                if key=='source_identity_digest':digests.add(item)
                walk(item)
        elif isinstance(value,list):
            for item in value:walk(item)
    walk(r1['fact_snapshot'])
    expected=original_resolution.identity.identity_digest
    if digests!={expected}:raise ValueError('FROZEN_R1_RETAINED_SOURCE_IDENTITY_DIGEST_CONFLICT')
    if original['source'] != state['source']:
        raise ValueError('RETAINED_R2_PREPARED_SOURCE_DRIFT')
    return deepcopy(dict(original['source']))

def retained_source_identity_inputs(continuation,state,frozen_round1):
    source=retained_prepared_source(continuation,state,frozen_round1)
    return {key:deepcopy(dict(source[key])) for key in ('collect_box','precollect','source_record')}|{'source_authority':source.get('source_authority')}
