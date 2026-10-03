"""Read one existing publication packet without resuming any external action.

Paths are explicit. This reader never imports a different project's runtime,
opens its databases, creates missing workbench state, or upgrades historical
approval/QA/paid receipts into current execution permission.
"""
from pathlib import Path
import hashlib
import json
import re
import stat
import subprocess

from shared_platform.publication_rounds import canonical_digest, CANONICAL_TIKTOK_TO_WORKBENCH

SKILLS = ('prepare-product-publication', 'prepare-product-images', 'publish-approved-product')


class TakeoverError(ValueError):
    pass


def local_path(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise TakeoverError('absolute local path required')
    # Check ancestors before resolving; a Windows junction is not is_symlink().
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise TakeoverError('linked publication evidence is not allowed')
    return path.resolve()


def read_document(path, observations, *, required=True):
    path = local_path(path)
    if not path.exists() and not required:
        return None
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise TakeoverError('publication document missing or too large: ' + path.name)
    raw = path.read_bytes()
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeError):
        raise TakeoverError('invalid publication JSON: ' + path.name) from None
    if not isinstance(document, dict):
        raise TakeoverError('publication document must be an object: ' + path.name)
    observations[str(path)] = hashlib.sha256(raw).hexdigest()
    return document


def r3_preview_readiness(root):
    """Project the repository-default R3 configuration gate without authority.

    This is deliberately diagnostic: a read-only takeover may bind historic
    facts even while a *new* R3 marketplace preview is blocked.  It never
    reads a personal Skill path, creates a policy, or changes startup config.
    """
    from shared_platform import publication_runtime_config

    config = publication_runtime_config.capture_startup_config(
        root=root, environ={'ORBIT_R3_CONFIG_ROOT': str(root)}
    )
    public, _ = publication_runtime_config.diagnose(config)
    documents = public.get('documents', {})
    return {
        'status': public.get('status'),
        'blockers': list(public.get('blockers', [])),
        'documents': {
            name: {'status': row.get('status')}
            for name, row in documents.items()
            if isinstance(row, dict)
        },
        'new_marketplace_preview_authorized': False,
    }


def source_binding(root, expected_commit):
    root = local_path(root)
    if not re.fullmatch(r'[0-9a-f]{40}', expected_commit):
        raise TakeoverError('exact expected commit required')
    def git(*args):
        return subprocess.check_output(['git', '-c', 'safe.directory=' + root.as_posix(),
            '--no-optional-locks', '-C', str(root), *args], text=True, encoding='utf-8').strip()
    if local_path(git('rev-parse', '--show-toplevel')) != root or git('rev-parse', 'HEAD') != expected_commit:
        raise TakeoverError('publication source root or commit differs')
    from scripts.sync_publish_approved_product_skill import build_manifest
    suites = {name: build_manifest(root / 'skills' / name) for name in SKILLS}
    files = {name: dict(manifest.hashes) for name, manifest in suites.items()}
    runtime_files = {str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (root/'shared_platform/publication_takeover.py',root/'scripts/publication_takeover.py',
                  root/'shared_platform/publication_rounds.py',root/'shared_platform/publication_autopilot.py',
                  root/'shared_platform/publication_r3_image_bridge.py',
                  root/'shared_platform/publication_r2_candidate.py',
                  root/'scripts/sync_publish_approved_product_skill.py')}
    return {'root': str(root), 'commit': expected_commit,
        'working_tree_clean': not bool(git('status', '--porcelain=v1', '-uall')),
        'skill_manifests': files, 'suite_digest': canonical_digest(files),
        'runtime_files':runtime_files,
        'policy_file_present': (root / 'config/product_publication_autopilot_policy.json').is_file(),
        'r3_marketplace_preview_readiness': r3_preview_readiness(root),
        'execution_authority': 'NONE_READ_ONLY_TAKEOVER'}


def inspect_publication(*, offer_id, data_root):
    if type(offer_id) is not str or not re.fullmatch(r'[0-9]{1,32}', offer_id):
        raise TakeoverError('exact digits-only offer_id required')
    root = local_path(data_root)
    directory = root / 'reports/product-preparation' / offer_id
    observed = {}
    snapshot = read_document(directory / 'round1-approved-snapshot.json', observed)
    unsigned = {k:v for k,v in snapshot.items() if k != 'snapshot_digest'}
    if (snapshot.get('schema_version') != 'round1-approved-snapshot/v1'
            or snapshot.get('offer_id') != offer_id or snapshot.get('status') != 'APPROVED'
            or snapshot.get('snapshot_digest') != canonical_digest(unsigned)):
        raise TakeoverError('round1 snapshot identity or digest differs')
    if snapshot.get('image_plan_digest') != canonical_digest(snapshot.get('image_plan', {})):
        raise TakeoverError('round1 image plan digest differs')
    targets = snapshot.get('canonical_targets')
    allowed = set(CANONICAL_TIKTOK_TO_WORKBENCH) | {'shopee:PH','shopee:MY','shopee:TH','shopee:VN','ozon:RU'}
    if (not isinstance(targets, list) or not targets or any(type(t) is not str or t not in allowed for t in targets)
            or len(targets) != len(set(targets))):
        raise TakeoverError('round1 target scope invalid')
    state = read_document(root / 'data/new_product_workbench' / (offer_id + '.json'), observed)
    approval = state.get('product_approval', {})
    facts = snapshot.get('fact_snapshot', {}).get('product_facts', {})
    if (state.get('offer_id') != offer_id or approval.get('status') != 'approved'
            or not snapshot.get('product_approval_id') or not snapshot.get('product_approval_fingerprint')
            or approval.get('approval_id') != snapshot['product_approval_id']
            or approval.get('input_fingerprint') != snapshot['product_approval_fingerprint']
            or state.get('review', {}).get('selected_sites') != snapshot.get('workbench_tiktok_sites')
            or snapshot.get('workbench_tiktok_sites') != [CANONICAL_TIKTOK_TO_WORKBENCH[t] for t in targets if t in CANONICAL_TIKTOK_TO_WORKBENCH]
            or approval.get('seller_sku') != facts.get('seller_sku')):
        raise TakeoverError('round1 approval or target scope superseded')
    first = read_document(directory / 'first-review.json', observed, required=False)
    decision = read_document(directory / 'round1-auto-decision.json', observed, required=False)
    if snapshot.get('human_approval') is False:
        if (snapshot.get('approved_by') != 'product-publication-autopilot'
                or snapshot.get('approval_authority') != 'ACTIVE_AUTOPILOT_POLICY'
                or approval.get('approved_by') != 'product-publication-autopilot'
                or approval.get('approval_authority') != 'ACTIVE_AUTOPILOT_POLICY'
                or not decision or decision.get('schema_version') != 'round1-auto-decision/v1'
                or decision.get('status') != 'AUTO_APPROVED' or decision.get('human_approval') is not False
                or decision.get('decided_by') != 'product-publication-autopilot'
                or decision.get('decision_digest') != canonical_digest({k:v for k,v in decision.items() if k != 'decision_digest'})
                or decision.get('decision_digest') != snapshot.get('decision_receipt_digest')
                or decision.get('first_review_digest') != snapshot.get('first_review_digest')
                or approval.get('decision_digest') != decision.get('decision_digest')
                or not decision.get('checks') or decision.get('exceptions')
                or any(c.get('status') != 'PASS' for c in decision['checks'])):
            raise TakeoverError('historical automatic decision binding differs')
    elif snapshot.get('human_approval') is not True or snapshot.get('approved_by') != 'Kyle' or approval.get('approved_by') != 'Kyle':
        raise TakeoverError('historical human approval identity differs')
    rows = {}
    reports = {}
    for filename in ('brand-image-generation.json','brand-image-translation.json','automated-image-qa.json','round2-blocker.json'):
        value = read_document(directory / filename, observed, required=False)
        if value is None:
            continue
        reports[filename] = value
        if value.get('offer_id') != offer_id:
            raise TakeoverError('cross-product report: ' + filename)
        if value.get('round1_snapshot_digest') not in {None, snapshot['snapshot_digest']}:
            raise TakeoverError('report refers to another round1 snapshot: ' + filename)
        row = {'recorded_status': value.get('status'), 'asset_count': len(value.get('assets', [])),
               'execution_authority': False}
        if 'assets' in value:
            row['recorded_asset_states'] = {}
            row['unknown_outcome_count'] = 0
            for asset in value['assets']:
                status = str(asset.get('status', 'MISSING'))
                row['recorded_asset_states'][status] = row['recorded_asset_states'].get(status, 0) + 1
                row['unknown_outcome_count'] += asset.get('outcome_unknown') is True
            row['keep_semantics'] = 'No automatic keep or image approval inferred from generation status'
        if filename == 'round2-blocker.json':
            ledger = value.get('paid_request_accounting', {})
            row.update(blocking_codes=value.get('blocking_codes', []),
                historical_confirmed_requests=ledger.get('confirmed_requests'),
                historical_request_counts=ledger.get('counts', {}),
                accounting_kind='RECORDED_HISTORICAL_NOT_CURRENT_PROVIDER_READBACK')
        rows[filename] = row
    blocker = rows.get('round2-blocker.json')
    from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS, validate_r2_identity
    reports['round1-approved-snapshot.json'] = snapshot
    reports['first-review.json'] = first
    reports['brand-image-translation-plan.json'] = read_document(
        directory / 'brand-image-translation-plan.json', observed, required=False)
    try:
        identity = validate_r2_identity({key: reports.get(name) for key,name in R2_DOCUMENTS.items()})
        consumer = {'status': 'PASSED', 'identity': identity, 'execution_authority': False}
    except ValueError as error:
        # Producer status words cannot replace the complete R3 consumer contract.
        code = str(error)
        consumer = {'status': 'BLOCKED', 'code': code if re.fullmatch(r'R2_[A-Z_]+', code)
                    else 'R2_DOCUMENT_CONTRACT_INVALID', 'execution_authority': False}
    qa = reports.get('automated-image-qa.json') or {}
    consumer['recorded_failed_checks'] = [row['code'] for row in (qa.get('checks') or [])
        if isinstance(row,dict) and row.get('status') == 'FAILED'
        and row.get('code') in {'ROLE_COVERAGE','FACTUAL_ALIGNMENT','OCR_LANGUAGE','DUPLICATION','TARGET_ROUTING'}]
    next_action = ('INSPECT_ROUND2_HANDOFF' if not blocker and consumer['status'] == 'PASSED'
                   else 'RECONCILE_EXISTING_IMAGE_EVIDENCE')
    # Never treat old QA or an old budget exception as an action instruction.
    for path, digest in observed.items():
        if hashlib.sha256(local_path(path).read_bytes()).hexdigest() != digest:
            raise TakeoverError('evidence changed during takeover')
    return {'schema_version':'publication-takeover/v1','status':'READ_ONLY_BOUND',
        'offer_id':offer_id,'seller_sku':facts.get('seller_sku'),'title':facts.get('title'),
        'snapshot_digest':snapshot['snapshot_digest'],'targets':targets,
        'approved_revision':snapshot.get('approved_product_center_revision'),'current_revision':state.get('_revision'),
        'round1_identity_valid':True,'first_review_matches_frozen':bool(first and canonical_digest(first)==snapshot.get('first_review_digest')),
        'historical_approval_authority':snapshot.get('approval_authority'),
        'records':rows,'r2_consumer':consumer,'next_action':next_action,'observed_files':observed,
        'report_root':str(directory),'data_root':str(root),
        'new_approval_created':False,'paid_requests':0,'business_writes':0,
        'marketplace_publication_authorized':False,'current_target_authorization':'NOT_INFERRED_FROM_HISTORICAL_SNAPSHOT'}
