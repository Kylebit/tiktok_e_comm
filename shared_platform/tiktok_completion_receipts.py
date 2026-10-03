"""Read-only trusted receipt verification for first-completion successors."""
from copy import deepcopy
import json
from pathlib import Path
from shared_platform.tiktok_lineage_recovery import (
    APPROVED_COMPLETION_SCHEMA_VERSION,
    _canonical_digest,
    completion_evidence_from_addendum,
    validate_tiktok_first_completion_authority_addendum,
    validate_tiktok_lineage_recovery_manifest,
    validate_tiktok_approved_completion_zero_write_retry_source,
    compile_tiktok_approved_first_completion_zero_write_retry,
    compile_tiktok_approved_first_completion_zero_write_retry_depth2,
    predecessor_tiktok_approved_completion_retry_manifest,
)


def _read_receipt(root, offer_id, directory, digest):
    root=Path(root).resolve(strict=True)
    if not offer_id.isascii() or not offer_id.isdigit():
        raise ValueError('invalid receipt offer identity')
    hex_digest=digest.removeprefix('sha256:')
    if len(hex_digest)!=64 or any(c not in '0123456789abcdef' for c in hex_digest):
        raise ValueError('invalid receipt digest')
    path=root/offer_id/directory/(hex_digest+'.json')
    if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
        raise ValueError('receipt is outside trusted authority root')
    return json.loads(path.read_text(encoding='utf8'))


class CompletionReceiptError(ValueError):
    def __init__(self, code):
        self.code=code
        super().__init__(code)


def verify_completion_receipts(manifest, *, authority_root, run_store, report_store):
    try:
        return _verify_completion_receipts(manifest,authority_root=authority_root,
                                           run_store=run_store,report_store=report_store)
    except CompletionReceiptError:
        raise
    except OSError as error:
        raise CompletionReceiptError('CONTINUATION_SOURCE_EVIDENCE_UNAVAILABLE') from error
    except (ValueError,KeyError,TypeError) as error:
        raise CompletionReceiptError('CONTINUATION_SOURCE_EVIDENCE_MISMATCH') from error


def _verify_completion_receipts(manifest, *, authority_root, run_store, report_store):
    manifest=validate_tiktok_lineage_recovery_manifest(manifest)
    if manifest.get('operation_kind')!='approved_first_completion':
        raise ValueError('completion receipts require first-completion manifest')
    receipt=validate_tiktok_first_completion_authority_addendum(_read_receipt(
        authority_root,manifest['lineage']['offer_id'],'execution-authority-addenda',manifest['authority_addendum_digest']))
    if receipt['authority_receipt_digest']!=manifest['authority_addendum_digest']:
        raise ValueError('trusted completion authority digest differs')
    lineage=manifest['lineage']; approved=receipt['approved_lineage']
    if (str(receipt['offer_id'])!=str(lineage['offer_id']) or str(receipt['revision'])!=str(lineage['revision'])
            or receipt['subject']!=manifest['authority_subject']
            or any(approved[key]!=lineage[key] for key in ('plan_id','candidate_digest','approval_digest'))
            or approved['execution_snapshot_digest']!=lineage['snapshot_digest']):
        raise ValueError('trusted completion authority lineage differs')
    if [row['target_label'] for row in receipt['scope']['included_targets']]!=manifest['completion_target_labels']:
        raise ValueError('trusted completion scope differs')
    evidence=completion_evidence_from_addendum(receipt)
    binding=manifest.get('zero_write_retry')
    if binding:
        run=run_store.get_run_by_id(run_id=binding['source_run_id'])
        report=report_store.get_report_by_run(run_id=binding['source_run_id'])
        if not run or run['state']!='COMPLETED' or not report or _canonical_digest(report)!=binding['source_report_digest']:
            raise ValueError('retry source report is unresolved or differs')
        if binding.get('retry_depth')==2:
            predecessor=predecessor_tiktok_approved_completion_retry_manifest(manifest)
            verify_completion_receipts(predecessor,authority_root=authority_root,
                                       run_store=run_store,report_store=report_store)
            rebuilt=compile_tiktok_approved_first_completion_zero_write_retry_depth2(predecessor,report,source_run=run)
            replacement=predecessor['zero_write_retry']['replacement']
        else:
            transport=_read_receipt(authority_root,manifest['lineage']['offer_id'],
                                   'execution-transport-receipts',binding['transport_rehost_receipt_digest'])
            original=deepcopy(manifest);original.pop('zero_write_retry')
            original['schema_version']=APPROVED_COMPLETION_SCHEMA_VERSION
            replacement=binding['replacement']
            command=next(row for row in original['commands'] if row['target_label']==replacement['target_label'])
            assets=[row for row in command['transport_binding']['assets']
                    if row['url']==replacement['new_url'] and row['content_digest']==replacement['content_digest']]
            if len(assets)!=1:raise ValueError('retry transport replacement differs')
            assets[0]['url']=replacement['old_url'];assets[0]['verification_digest']=replacement['old_verification_digest']
            command['transport_binding']['verification_receipt_digest']=_canonical_digest({
                key:command['transport_binding'][key] for key in ('kind','assets')})
            command['command_digest']=_canonical_digest({k:v for k,v in command.items() if k!='command_digest'})
            original['evidence_bindings']['targets'][replacement['target_label']]['transport_binding']=deepcopy(command['transport_binding'])
            original['manifest_digest']=_canonical_digest({k:v for k,v in original.items() if k!='manifest_digest'})
            if original['manifest_digest']!=binding['source_manifest_digest']:
                raise ValueError('retry original manifest digest differs')
            validate_tiktok_approved_completion_zero_write_retry_source(report=report,original_manifest=original,source_run=run)
            rebuilt=compile_tiktok_approved_first_completion_zero_write_retry(original,report,transport_rehost_receipt=transport)
        if rebuilt!=manifest:raise ValueError('retry trusted source recompilation differs')
        evidence['targets'][replacement['target_label']]['transport_binding']=deepcopy(
            manifest['evidence_bindings']['targets'][replacement['target_label']]['transport_binding'])
    if evidence!=manifest['evidence_bindings']:
        raise ValueError('trusted completion evidence differs')
    return {'manifest_digest':manifest['manifest_digest'],
            'authority_digest':receipt['authority_receipt_digest'],
            'source_run_id':binding['source_run_id'] if binding else None}
