"""Bounded new requests preserve the unresolved original image attempts."""
import hashlib

import pytest

from modules.sourcing import brand_image_lingshi_generation as brand
from modules.sourcing.image_generation_checkpoint import ImageCheckpoint, digest
from shared_platform.publication_paid_requests import PaidRequestBlocked
from shared_platform.publication_rounds import canonical_digest
from test_publication_paid_entry import Workflow, OFFER, write, read, result_bytes


def _unknown_workflow(tmp_path, monkeypatch):
    w = Workflow(tmp_path, monkeypatch)
    for b in w.round1['image_plan']['brand_plans']:
        b['generated_assets'].append({'role':'material_detail','quantity':1,'brief':'Faithful material detail'})
    reuse=read(w.directory/'brand-image-reuse-plan.json')
    reuse['items'].extend({'brand_id':b['id'],'role':'material_detail','decision':'GENERATE'}
        for b in w.round1['image_plan']['brand_plans'])
    write(w.directory/'brand-image-reuse-plan.json',reuse)
    first={'offer_id':OFFER,'status':'FIRST_REVIEW_READY',
        'target_selection':{'requested':w.round1['canonical_targets']},
        'targets':[{'target':t,'category':{},'copy':{},'price':{}} for t in w.round1['canonical_targets']],
        'product_facts':w.round1['fact_snapshot']['product_facts'],
        'image_execution_plan':w.round1['image_plan']}
    w.round1['first_review_digest']=canonical_digest(first)
    w.round1.pop('snapshot_digest')
    w.round1['snapshot_digest']=canonical_digest(w.round1)
    write(w.directory/'round1-approved-snapshot.json',w.round1)
    write(w.directory/'first-review.json',first)
    client = brand.LingshiClient
    real = client.create_media_generation
    failed = []
    def timeout(self, **kwargs):
        # Match the real delivery's sixteen roles: five unknown, eleven retained.
        if len(failed) < 5:
            failed.append(True)
            raise TimeoutError('synthetic original submission outcome unknown')
        return real(self, **kwargs)
    monkeypatch.setattr(client, 'create_media_generation', timeout)
    assert w.masters()['status'] == 'PROVIDER_RECONCILIATION_REQUIRED'
    monkeypatch.setattr(client, 'create_media_generation', real)
    context = w.context()
    cps = []
    attempts = []
    identities = {digest({'kind':'brand','offer_id':OFFER,'brand_id':b['id'],'role':r['role']}):
        (b['id'],r['role']) for b in w.round1['image_plan']['brand_plans'] for r in b['generated_assets']}
    for path in (w.directory/'brand-image-checkpoints-lingshi').glob('*.json'):
        cp = ImageCheckpoint.from_path(path)
        if cp.read()['status'] == 'SUBMISSION_UNKNOWN':
            cps.append(path)
            brand_id, role = identities[cp.business_digest]
            attempts.append({'ledger_key':context.image_key(cp,cp.read(),'brand_image_generation'),
                'checkpoint_path':str(path),'checkpoint_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'request_digest':cp.request_digest,'brand_id':brand_id,'role':role})
    assert len(cps) == 5 and context.summary()['unknown'] == 5
    decision = {'schema': 'test-conversation-decision/v1',
        'action_class': 'AUTHORIZE_UNKNOWN_IMAGE_SUCCESSOR', 'offer_id': OFFER,
        'round1_snapshot_digest': w.round1['snapshot_digest'],
        'original_unknown_attempts': attempts,
        'maximum_new_requests': 5, 'per_role_maximum_new_requests': 1,
        'risk_may_duplicate_original_charge': True, 'unknown_outcome_retained': True,
        'shared_cap': 40}
    receipt = tmp_path/'decision.json'; write(receipt, decision)
    return w, context, cps, receipt


def _grant(context, cps, receipt):
    from shared_platform.publication_image_successor import prepare_unknown_image_successor_grant
    return prepare_unknown_image_successor_grant(context, decision_path=receipt,
        decision_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(), checkpoint_paths=cps)


def test_bounded_successors_keep_originals_and_reuse_successes(tmp_path, monkeypatch):
    w, context, cps, receipt = _unknown_workflow(tmp_path, monkeypatch)
    protected = {p: p.read_bytes() for cp in cps for p in
        (cp, cp.with_suffix('.identity'), cp.with_suffix('.events.jsonl'))}
    originals = {r['artifact_path']: open(r['artifact_path'], 'rb').read()
        for r in read(w.directory/'brand-image-generation.json')['assets']}
    grant = _grant(context, cps, receipt)
    ledger_preimage=context.path.read_bytes()
    result = w.entry.run(w.args(execute_brand_generation=True, unknown_successor_grant=grant))
    assert result['completed_brand_image_count'] == 16
    assert result['new_paid_request_count'] == 5
    assert result['paid_requests']['planned'] == 5
    assert read(w.directory/'brand-image-generation.json')['provider_preview']['planned_paid_task_count'] == 5
    assert context.summary()['occupied'] == 21 and context.summary()['unknown'] == 5
    assert context.path.read_bytes().startswith(ledger_preimage)
    assert all(p.read_bytes() == raw for p, raw in protected.items())
    assert all(open(p, 'rb').read() == raw for p, raw in originals.items())
    before = context.path.read_bytes()
    assert w.entry.run(w.args(execute_brand_generation=True,
        unknown_successor_grant=grant))['new_paid_request_count'] == 0
    assert context.path.read_bytes() == before
    from shared_platform.publication_image_successor import completed_successor_receipt
    proof = completed_successor_receipt(context)
    assert len(proof['covered_original_keys']) == 5 and proof['unknown_outcomes_retained'] is True
    from shared_platform.publication_image_successor import verify_successor_adoption
    adopted = verify_successor_adoption(context)
    assert adopted['receipt'] == proof
    assert all((w.root/p).is_file() for p in adopted['source_files'])
    assert w.master_qa()['status'] == 'PASSED'
    assert verify_successor_adoption(context) == adopted
    # Complete the original zero-task localization path and consume it in R3.
    w.entry.run(w.args(approve_translation_images='none',dimension_only_images='none'))
    assert w.entry.run(w.args(execute_paid=True))['new_paid_request_count'] == 0
    assert w.master_qa()['status'] == 'PASSED'
    from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS, validate_r2_identity
    documents={key:read(w.directory/name) for key,name in R2_DOCUMENTS.items()}
    identity=validate_r2_identity(documents)
    assert identity['round1_snapshot_digest'] == w.round1['snapshot_digest']
    from shared_platform.agent_prepared_publication_adoption import _verify_paid_history
    paid, accepted = _verify_paid_history(context)
    assert paid['unknown'] == 5 and paid['occupied'] <= 40
    assert accepted == adopted
    context.reserve(purpose='image_quality_assurance',model='fixture-qa',
        business={'phase':'unrelated-unknown'},request={'owned':'unresolved'})
    from shared_platform.agent_prepared_publication_adoption import AdoptionBlocked
    with pytest.raises(AdoptionBlocked): _verify_paid_history(context)


@pytest.mark.parametrize('drift', ['decision', 'checkpoint', 'grant', 'model', 'reference'])
def test_drift_blocks_before_new_post(tmp_path, monkeypatch, drift):
    w, context, cps, receipt = _unknown_workflow(tmp_path, monkeypatch)
    grant = _grant(context, cps, receipt)
    if drift == 'decision': receipt.write_bytes(receipt.read_bytes()+b' ')
    elif drift == 'checkpoint': cps[0].write_bytes(cps[0].read_bytes()+b' ')
    elif drift == 'grant':
        data = read(grant); data['rows'][0]['role'] = 'other'; write(grant, data)
    elif drift == 'model':
        monkeypatch.setattr(w.entry, 'resolve_brand_image_model', lambda c: ('other-model', {}), raising=False)
        monkeypatch.setattr(brand, 'resolve_brand_image_model', lambda c: ('other-model', {}))
    else:
        monkeypatch.setattr(w.entry, '_download_source', lambda url: result_bytes('https://fixture.example/99.png'))
    calls = len(brand.LingshiClient.calls)
    try:
        result = w.entry.run(w.args(execute_brand_generation=True, unknown_successor_grant=grant))
        assert result['status'] == 'PROVIDER_RECONCILIATION_REQUIRED'
    except PaidRequestBlocked:
        pass
    assert len(brand.LingshiClient.calls) == calls


def test_unknown_successor_never_gets_second_successor(tmp_path, monkeypatch):
    w, context, cps, receipt = _unknown_workflow(tmp_path, monkeypatch)
    grant = _grant(context, cps, receipt)
    def timeout(self, **kwargs):
        self.record('unknown-successor')
        raise TimeoutError('synthetic new submission unknown')
    monkeypatch.setattr(brand.LingshiClient, 'create_media_generation', timeout)
    w.entry.run(w.args(execute_brand_generation=True, unknown_successor_grant=grant))
    occupied = context.summary()['occupied']; calls = len(brand.LingshiClient.calls)
    w.entry.run(w.args(execute_brand_generation=True, unknown_successor_grant=grant))
    assert context.summary()['occupied'] == occupied == 21
    assert len(brand.LingshiClient.calls) == calls
    from shared_platform.publication_image_successor import completed_successor_receipt
    with pytest.raises(PaidRequestBlocked): completed_successor_receipt(context)


def test_without_grant_original_unknown_still_blocks(tmp_path, monkeypatch):
    w, context, cps, receipt = _unknown_workflow(tmp_path, monkeypatch)
    calls = len(brand.LingshiClient.calls)
    assert w.masters()['status'] == 'PROVIDER_RECONCILIATION_REQUIRED'
    assert context.summary()['occupied'] == 16
    assert len(brand.LingshiClient.calls) == calls


def test_crash_after_reservation_never_posts_on_reentry(tmp_path, monkeypatch):
    w, context, cps, receipt = _unknown_workflow(tmp_path, monkeypatch)
    grant = _grant(context, cps, receipt)
    from shared_platform.publication_paid_requests import PaidRequestContext
    original = PaidRequestContext.invoke
    def crash(self, key, call):
        if self.entry(key).get('unknown_successor'):
            raise RuntimeError('synthetic crash after durable reserve before provider')
        return original(self,key,call)
    monkeypatch.setattr(PaidRequestContext,'invoke',crash)
    calls=len(brand.LingshiClient.calls)
    w.entry.run(w.args(execute_brand_generation=True,unknown_successor_grant=grant))
    monkeypatch.setattr(PaidRequestContext,'invoke',original)
    w.entry.run(w.args(execute_brand_generation=True,unknown_successor_grant=grant))
    assert context.summary()['occupied'] == 21
    assert len(brand.LingshiClient.calls) == calls


def test_original_shared_cap_counts_all_unknowns_and_reservations(tmp_path, monkeypatch):
    w, context, cps, receipt = _unknown_workflow(tmp_path, monkeypatch)
    grant = _grant(context, cps, receipt)
    for i in range(24):
        context.reserve(purpose='image_quality_assurance',model='fixture-qa',
            business={'phase':'cap-occupied','batch':i},request={'owned':i})
    calls=len(brand.LingshiClient.calls)
    result=w.entry.run(w.args(execute_brand_generation=True,unknown_successor_grant=grant))
    assert result['status'] == 'PROVIDER_RECONCILIATION_REQUIRED'
    assert context.summary()['occupied'] == 40 and len(brand.LingshiClient.calls) == calls
