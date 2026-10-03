"""Frozen rollback targets must fail before any live-service lookup or stop."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import pytest

@pytest.mark.parametrize('drift,message',[('head','Rollback HEAD drift'),('dirty','Rollback tree dirty'),('receipt','Rollback receipt drift'),('profile','Rollback file drift')])
def test_rollback_drift_never_reaches_service_control(tmp_path,monkeypatch,drift,message):
    source=Path(__file__).resolve().parents[1]/'scripts/local_console.py'
    spec=importlib.util.spec_from_file_location('rollback_ops',source);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    root=tmp_path/'main';root.mkdir();(tmp_path/'environment/Scripts').mkdir(parents=True);(tmp_path/'environment/Scripts/python.exe').write_bytes(b'not executed')
    old=tmp_path/'old';(old/'scripts').mkdir(parents=True);script=old/'scripts/catalog_review_preview.py';script.write_text('# fixture\n')
    private=tmp_path/'private';private.mkdir();db=private/'catalog.db';override=private/'weight.json';override.write_text('{}')
    meta=old/'metadata.json';meta.write_text(json.dumps({'snapshot_database':str(db)}));profile=old/'profile.json';profile.write_text('{}')
    def git(*args):return subprocess.check_output(['git','-C',str(old),*args],text=True).strip()
    git('init');git('add','.');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture');head=git('rev-parse','HEAD')
    command=[sys.executable,'-I','-B','-X','utf8',str(script),'--metadata',str(meta),'--profile',str(profile),'--weight-overrides',str(override),'--port','49303']
    receipt=tmp_path/'receipt.json';receipt.write_text(json.dumps({'rollback_args':command}))
    frozen={'root':str(old),'head':head,'receipt_sha256':m.digest(receipt),'command_sha256':hashlib.sha256(json.dumps(command,separators=(',',':')).encode()).hexdigest(),'file_hashes':{str(p):m.digest(p) for p in [script,meta,profile,override]}}
    if drift=='head':script.write_text('# changed commit\n');git('add','.');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','changed')
    elif drift=='dirty':script.write_text('# dirty script\n')
    elif drift=='receipt':frozen['receipt_sha256']='0'*64
    else:frozen['file_hashes'][str(profile)]='0'*64
    cfg={'code_root':str(root),'state_dir':str(tmp_path/'state'),'metadata':str(meta),'override':str(override),'port':49303,'expected_head':'a'*40,'profile_id':'test','rollback_receipt':str(receipt),'rollback_binding':frozen}
    config=tmp_path/'config.json';config.write_text(json.dumps(cfg));monkeypatch.setattr(m,'ROOT',root);monkeypatch.setattr(m,'BASE',tmp_path)
    actual=subprocess.check_output
    def checked(args,**kw):
        if args[:3]==['git','-C',str(root)]:return 'a'*40+'\n'
        return actual(args,**kw)
    monkeypatch.setattr(m.subprocess,'check_output',checked)
    calls=[]
    def forbidden(*args):calls.append(args);raise RuntimeError('service gate reached')
    monkeypatch.setattr(m,'health',forbidden);monkeypatch.setattr(m,'listener',forbidden)
    monkeypatch.setattr(sys,'argv',['local_console.py','rollback','--config',str(config),'--execute'])
    with pytest.raises(AssertionError,match=message):m.main()
    assert calls==[]
