"""Read-only history and retained round/closure evidence; never approval authority."""
from pathlib import Path
import hashlib
import json
import re

from modules.sourcing.manual_product_intake import _safe
from shared_platform.product_publication_closure import validate_publication_closure
from shared_platform.product_publication_reports import validate_publication_report


def _read(root, relative):
    path = _safe(root, relative)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('evidence file exceeds the display limit')
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('evidence must be an object')
    return value, hashlib.sha256(raw).hexdigest()


def history(*, root, limit=50):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('history limit must be 1-100')
    folder = _safe(root, 'data/new_product_workbench')
    items, errors = [], []
    paths = sorted(folder.glob('*.json')) if folder.is_dir() else []
    registry = _safe(root, 'data/r2_candidate_reviews')
    registered = list(registry.iterdir()) if registry.is_dir() else []
    offers = sorted({p.stem for p in paths if re.fullmatch(r'[0-9]{1,32}',p.stem)} |
        {p.name for p in registered if re.fullmatch(r'[0-9]{1,32}',p.name)})
    for offer in offers[:1000]:
        try:
            project, binding = _evidence_root(offer, root)
            state, digest = _read(project, f'data/new_product_workbench/{offer}.json')
            if state.get('offer_id') != offer:
                raise ValueError('history identity does not match filename')
            source, review = state.get('source') or {}, state.get('review') or {}
            mode = source.get('source_mode') or 'miaoshou'
            items.append(dict(offer_id=offer, title=str(binding['title'] if binding else review.get('title') or source.get('title_source') or '未命名商品')[:300],
                source_mode=mode, source_authority=source.get('source_authority') or ('1688' if mode=='miaoshou' else 'unknown'),
                revision=state.get('_revision'), updated_at=state.get('updated_at'), source_digest=digest,
                primary_sku=binding['seller_sku'] if binding else review.get('seller_sku'),
                record_source='registered_review' if binding else 'local_archive', execution_authority=False))
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
            errors.append(dict(offer_id=offer,error=str(error)))
    items.sort(key=lambda row:(str(row['updated_at'] or ''),row['offer_id']), reverse=True)
    return dict(ok=True,schema_version='product-workspace-history/v1',items=items[:limit],
        count=len(items[:limit]),errors=errors,truncated=len(offers)>1000 or len(items)>limit,execution_authority=False)


def _evidence_root(offer, root):
    """A present but invalid registration must never expose an older archive."""
    directory = _safe(root, f'data/r2_candidate_reviews/{offer}')
    if not directory.exists():
        return Path(root), None
    from shared_platform.publication_r2_review import _load
    project, registration, takeover, _, record = _load(offer, root)
    return project, dict(title=takeover['title'],seller_sku=registration['seller_sku'],
        binding_sha256=registration['binding_sha256'],revision=record['revision'],
        keep_count=sum(r['action']=='keep' for r in record['decisions']),
        candidate_count=len(record['decisions']),publication_authorized=False)


def product_evidence(offer_id, *, root):
    if not isinstance(offer_id,str) or not re.fullmatch(r'[0-9]{1,32}',offer_id):
        raise ValueError('exact product identity required')
    root, binding = _evidence_root(offer_id, root)
    rounds=[]
    for filename in ('first-review.json','round1-approved-snapshot.json','brand-image-generation.json',
            'brand-image-translation.json','automated-image-qa.json'):
        relative=f'reports/product-preparation/{offer_id}/{filename}'
        try:
            row,digest=_read(root,relative)
            if str(row.get('offer_id') or '')!=offer_id:
                raise ValueError('round evidence belongs to a different product')
            keys=('schema_version','status','offer_id','product_center_revision','product_revision','snapshot_digest',
                'product_facts','target_selection','pricing_review','targets','image_execution_plan','blockers',
                'summary','qa_status','generation_id','translation_id','first_review_digest','copy_review_sets','platform_categories')
            rounds.append(dict(filename=filename,status='AVAILABLE',source_path=relative,source_digest=digest,
                facts={k:row[k] for k in keys if k in row},execution_authority=False))
        except FileNotFoundError:
            rounds.append(dict(filename=filename,status='NOT_PREPARED',execution_authority=False))
        except (OSError,ValueError,TypeError) as error:
            rounds.append(dict(filename=filename,status='INVALID',error=str(error),execution_authority=False))
    closures=[]
    folder=_safe(root,f'reports/product-publication/{offer_id}')
    paths=list(folder.glob('*/closure-*/closure-report.json')) if folder.is_dir() else []
    for path in paths[:100]:
        try:
            value,digest=_read(root,path.relative_to(Path(root)).as_posix())
            closure=validate_publication_closure(value)
            if closure['offer_id']!=offer_id:raise ValueError('closure product identity conflicts')
            sources=[]
            for source in closure['source_reports']:
                report,raw_digest=_read(root,source['report_path'])
                canonical='sha256:'+hashlib.sha256(json.dumps(report,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                matches=(source['report_digest'] in {canonical,'sha256:'+raw_digest} and report.get('run_id')==source['run_id']
                    and report.get('offer_id')==offer_id and report.get('plan_id')==closure['plan_id'])
                try:
                    validated=validate_publication_report(report)
                    matches=matches and validated['snapshot']['digest'].removeprefix('sha256:')==closure['snapshot_digest'].removeprefix('sha256:')
                    target_map={r['target_label']:r for r in validated.get('targets',[])}
                    for target in closure['targets']:
                        if target['source_run_id']!=source['run_id']:continue
                        actual=target_map.get(target['target_label'])
                        matches=matches and bool(actual and actual['status']==target['source_status'])
                        if target['resolution']=='OFFICIAL_READBACK_VERIFIED':
                            matches=matches and target['platform_identity_bound'] and bool(actual and actual['status']=='PUBLISHED'
                                and actual.get('evidence') and actual['evidence'].get('outcome_unknown') is False
                                and validated['summary']['evidence']['readback_completed'])
                except (ValueError,TypeError,KeyError):
                    matches=False
                sources.append(dict(**source,verified=matches))
            closures.append(dict(status='AVAILABLE' if all(s['verified'] for s in sources) else 'RECONCILIATION_REQUIRED',
                source_digest=digest,closure=closure,source_reports=sources,execution_authority=False))
        except (OSError,ValueError,TypeError,KeyError) as error:
            closures.append(dict(status='INVALID',error=str(error),execution_authority=False))
    local_images=[]
    try:
        from modules.sourcing.manual_product_intake import resolve_manual_intake_image
        record,_=_read(root,f'data/product_intake/{offer_id}/record.json')
        if record.get('offer_id')!=offer_id or record.get('source_authority')!='manual-intake':
            raise ValueError('local image source identity conflicts')
        for row in record.get('images',[]):
            verified=resolve_manual_intake_image(offer_id,row.get('filename'),root=root) is not None
            local_images.append(dict(position=row.get('position'),url=row.get('url') if verified else None,
                status='AVAILABLE' if verified else 'INVALID',sha256=row.get('sha256'),approved_for_publication=False))
    except FileNotFoundError:
        pass
    return dict(ok=True,schema_version='product-workspace-evidence/v1',offer_id=offer_id,rounds=rounds,local_images=local_images,
        record_source='registered_review' if binding else 'local_archive',r2_review=binding,
        closures=closures,closure_count=len(closures),execution_authority=False)
