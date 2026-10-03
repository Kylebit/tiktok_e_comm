"""Actual server functions, original runner/domain lock, temporary SQLite only.

Server functions are extracted to avoid server import-time application globals.
Provider and authority transport are synthetic; handler socket IO is not tested.
"""
import ast
from copy import deepcopy
import logging
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import pytest
from test_tiktok_continuation_claim import context
from test_tiktok_runner_continuation_result import _result


def server_functions(namespace):
    path=Path(__file__).parents[1]/'modules/products/server.py'
    tree=ast.parse(path.read_text(encoding='utf8'))
    names={'_preview_tiktok_continuation_admission','_execute_product_publication_background'}
    module=ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[])
    exec(compile(module,str(path),'exec'),namespace)
    return namespace


def setup_runtime(tmp_path,monkeypatch):
    from shared_platform.workbench_engine import WorkbenchEngine
    # These tests execute only the continuation admission/runner boundary in a
    # synthetic namespace.  A stable-service identity gate inherited from an
    # earlier test or the invoking shell would otherwise try to validate the
    # deliberately incomplete synthetic ``RUNTIME_IDENTITY`` below.  Keep that
    # separate concern out of this fixture; runtime fail-closed behavior is
    # covered by the dedicated runtime-identity and registered-HTTP tests.
    monkeypatch.delenv('ORBIT_PUBLICATION_REQUIRE_VERIFIED_RUNTIME', raising=False)
    kwargs,trusted=context(tmp_path,monkeypatch)
    snapshot=trusted['snapshot'];snapshot['publication_targets']=[{'target_label':label} for label in trusted['policy']['approved_target_labels']]
    snapshot['skus']=[{'seller_sku':'660001','model_sku':'0001'}]
    candidate=trusted['candidate'];candidate['candidate_digest']=candidate['candidate_digest'].removeprefix('sha256:')
    candidate['write_budget']={'TIKTOK':{'shared_maximum':0,'per_target_maximum':2}}
    approval=trusted['approval'];approval['approval_digest']=approval['approval_digest'].removeprefix('sha256:')
    release=SimpleNamespace(approved_publication_snapshot=lambda **kw:deepcopy(snapshot))
    prepared=kwargs['prepared'];prepared.snapshot=snapshot
    monkeypatch.setattr('shared_platform.product_publication_runner.prepare_product_publication_run',lambda **kw:prepared)
    monkeypatch.setattr('shared_platform.publication_autopilot.load_release_candidate',lambda *a,**kw:deepcopy(candidate))
    monkeypatch.setattr('shared_platform.publication_autopilot.load_final_approval_receipt',lambda *a,**kw:deepcopy(approval))
    engine=WorkbenchEngine(tmp_path/'tasks.db',{'code_version':'1'*40,'environment':'stable','manifest_digest':'2'*64})
    monkeypatch.setattr('shared_platform.operations_domain_guard.engine_for',lambda root:engine)
    calls=[];queue=[]
    from test_tiktok_completion_receipts import write
    from test_tiktok_lineage_protocol import _completion_addendum
    addendum=_completion_addendum()
    write(tmp_path/'authority',kwargs['data']['offer_id'],'execution-authority-addenda',addendum['authority_receipt_digest'],addendum)
    def provider(request,manifest):
        with sqlite3.connect(tmp_path/'tasks.db') as conn:
            assert conn.execute('SELECT COUNT(*) FROM workbench_domain_operations').fetchone()[0]==1
        calls.append(request.run_id)
        return _result()
    namespace=dict(os=os,ROOT=tmp_path,WEB_DIR=tmp_path,RUNTIME_IDENTITY={},Path=Path,
        _TIKTOK_CONTINUATION_HOST={'context_reader':kwargs['context_reader'],'executor':provider,'authority_root':tmp_path/'authority'},
        _release_store=lambda:release,_product_publication_report_store=lambda:kwargs['report_store'],
        _product_publication_run_store=lambda:kwargs['run_store'],
        _product_publication_execution_identity=lambda _:kwargs['execution_identity'],
        _catalog_publication_sync=lambda:None,_launch_product_publication_background=queue.append,
        _PLATFORM_PUBLISH_LOGGER=logging.getLogger('synthetic-continuation'))
    return server_functions(namespace),kwargs,trusted,calls,queue


def test_configured_start_claim_domain_lock_runner_report_and_replay(tmp_path,monkeypatch):
    namespace,kwargs,_,calls,queue=setup_runtime(tmp_path,monkeypatch)
    start=namespace['_preview_tiktok_continuation_admission']
    status,payload=start(kwargs['data'],completion=True)
    assert status==202 and not payload['reused'] and len(queue)==1
    queue.pop()()
    run=kwargs['run_store'].get_run_by_id(run_id=payload['run_id'])
    assert run['state']=='COMPLETED',run
    report=kwargs['report_store'].get_report_by_run(run_id=run['run_id'])
    assert len(report['targets'])==10 and report['continuation_evidence']
    second=start(kwargs['data'],completion=True)
    assert second[0]==202 and second[1]['reused'] and queue==[] and calls==[run['run_id']]


def test_policy_revocation_after_claim_prevents_provider(tmp_path,monkeypatch):
    namespace,kwargs,trusted,calls,queue=setup_runtime(tmp_path,monkeypatch)
    status,payload=namespace['_preview_tiktok_continuation_admission'](kwargs['data'],completion=True)
    assert status==202
    trusted['policy']['retired_target_labels']=[kwargs['data']['continuation_manifest']['completion_target_labels'][0]]
    queue.pop()()
    assert calls==[]


def test_provider_result_persistence_failure_never_requeues(tmp_path,monkeypatch):
    namespace,kwargs,_,calls,queue=setup_runtime(tmp_path,monkeypatch)
    def failed_report(_):raise OSError('synthetic disk failure')
    monkeypatch.setattr(kwargs['report_store'],'store_report',failed_report)
    start=namespace['_preview_tiktok_continuation_admission'];status,payload=start(kwargs['data'],completion=True)
    assert status==202
    queue.pop()()
    assert kwargs['run_store'].get_run_by_id(run_id=payload['run_id'])['state']=='FAILED'
    assert start(kwargs['data'],completion=True)[1]['reused']
    assert len(calls)==1 and queue==[]


def test_untrusted_shape_never_calls_context(tmp_path,monkeypatch):
    namespace,kwargs,_,calls,queue=setup_runtime(tmp_path,monkeypatch)
    namespace['_TIKTOK_CONTINUATION_HOST']['context_reader']=lambda _:pytest.fail('context read before input shape guard')
    kwargs['data']['offer_id']='../../private'
    assert namespace['_preview_tiktok_continuation_admission'](kwargs['data'],completion=True)[0]==409
    assert calls==queue==[]


def test_continuation_refuses_unconfigured_domain_lock(tmp_path,monkeypatch):
    namespace,kwargs,_,calls,queue=setup_runtime(tmp_path,monkeypatch)
    monkeypatch.setattr('shared_platform.operations_domain_guard.engine_for',lambda root:None)
    status,payload=namespace['_preview_tiktok_continuation_admission'](kwargs['data'],completion=True)
    assert status==202
    queue.pop()()
    assert calls==[]
    assert kwargs['run_store'].get_run_by_id(run_id=payload['run_id'])['state']=='FAILED'


def test_depth1_and_depth2_retry_keep_all_locks_and_exact_reports(tmp_path,monkeypatch):
    from shared_platform.tiktok_continuation_admission import _digest
    from shared_platform.tiktok_lineage_recovery import (
        compile_tiktok_approved_first_completion_zero_write_retry,
        compile_tiktok_approved_first_completion_zero_write_retry_depth2)
    from test_tiktok_continuation_report_compatibility import _zero_report,_rehost_receipt
    from test_tiktok_completion_receipts import write
    namespace,kwargs,trusted,calls,queue=setup_runtime(tmp_path,monkeypatch)
    start=namespace['_preview_tiktok_continuation_admission']
    def provider(request,manifest):
        calls.append(request.run_id)
        report=_zero_report(manifest,run_id=request.run_id)
        result=_result();result['targets']=report['targets'];result['continuation_evidence']=report['continuation_evidence']
        return result
    namespace['_TIKTOK_CONTINUATION_HOST']['executor']=provider
    code,first=start(kwargs['data'],completion=True);assert code==202
    queue.pop()()
    original=deepcopy(kwargs['data']['continuation_manifest'])
    transport=_rehost_receipt(original)
    write(tmp_path/'authority',kwargs['data']['offer_id'],'execution-transport-receipts',transport['receipt_digest'],transport)
    previous=first['run_id'];manifest=original
    for depth in (1,2):
        report=kwargs['report_store'].get_report_by_run(run_id=previous)
        assert report is not None
        if depth==1:
            manifest=compile_tiktok_approved_first_completion_zero_write_retry(manifest,report,transport_rehost_receipt=transport)
        else:
            manifest=compile_tiktok_approved_first_completion_zero_write_retry_depth2(manifest,report,
                source_run=kwargs['run_store'].get_run_by_id(run_id=previous))
        kwargs['data']['continuation_manifest']=manifest;kwargs['data']['retry_of_run_id']=previous
        trusted['policy']['manifest_digest']=manifest['manifest_digest']
        trusted['policy']['policy_digest']=_digest({k:v for k,v in trusted['policy'].items() if k!='policy_digest'})
        code,payload=start(kwargs['data'],completion=True)
        assert code==202,payload
        queue.pop()()
        run=kwargs['run_store'].get_run_by_id(run_id=payload['run_id'])
        assert run['state']=='COMPLETED',run
        report=kwargs['report_store'].get_report_by_run(run_id=run['run_id'])
        assert len(report['targets'])==10
        assert next(row for row in report['targets'] if row['target_label']=='tiktok:HB_PH')['status']=='PROCESSING'
        assert report['continuation_evidence']['retry_source']['retry_of_run_id']==previous
        with sqlite3.connect(tmp_path/'tasks.db') as conn:
            assert conn.execute('SELECT COUNT(*) FROM workbench_domain_locks').fetchone()[0]==10
            assert conn.execute("SELECT COUNT(*) FROM workbench_domain_operations WHERE state='inflight'").fetchone()[0]==1
        assert start(kwargs['data'],completion=True)[1]['reused']
        assert queue==[] and len(calls)==depth+1
        previous=run['run_id']


@pytest.mark.parametrize('conflict',['missing','completed','scope'])
def test_retry_handover_conflict_never_dispatches_or_creates_successor_lock(tmp_path,monkeypatch,conflict):
    from shared_platform.tiktok_continuation_admission import _digest
    from shared_platform.tiktok_lineage_recovery import compile_tiktok_approved_first_completion_zero_write_retry
    from test_tiktok_continuation_report_compatibility import _rehost_receipt
    from test_tiktok_completion_receipts import write
    namespace,kwargs,trusted,calls,queue=setup_runtime(tmp_path,monkeypatch)
    start=namespace['_preview_tiktok_continuation_admission']
    code,first=start(kwargs['data'],completion=True);assert code==202;queue.pop()()
    report=kwargs['report_store'].get_report_by_run(run_id=first['run_id'])
    original=kwargs['data']['continuation_manifest'];transport=_rehost_receipt(original)
    manifest=compile_tiktok_approved_first_completion_zero_write_retry(original,report,transport_rehost_receipt=transport)
    write(tmp_path/'authority',kwargs['data']['offer_id'],'execution-transport-receipts',transport['receipt_digest'],transport)
    kwargs['data'].update(continuation_manifest=manifest,retry_of_run_id=first['run_id'])
    trusted['policy']['manifest_digest']=manifest['manifest_digest']
    trusted['policy']['policy_digest']=_digest({k:v for k,v in trusted['policy'].items() if k!='policy_digest'})
    with sqlite3.connect(tmp_path/'tasks.db') as conn:
        if conflict=='missing':
            conn.execute('DELETE FROM workbench_domain_locks');conn.execute('DELETE FROM workbench_domain_operations')
        elif conflict=='completed':conn.execute("UPDATE workbench_domain_operations SET state='completed'")
        else:conn.execute("UPDATE workbench_domain_operations SET resources_json='[]'")
        before=conn.execute('SELECT COUNT(*) FROM workbench_domain_locks').fetchone()[0]
    code,payload=start(kwargs['data'],completion=True);assert code==202;queue.pop()()
    assert len(calls)==1
    assert kwargs['run_store'].get_run_by_id(run_id=payload['run_id'])['state']=='FAILED'
    with sqlite3.connect(tmp_path/'tasks.db') as conn:
        assert conn.execute('SELECT COUNT(*) FROM workbench_domain_locks').fetchone()[0]==before
