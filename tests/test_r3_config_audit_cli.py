import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]


def audit(root,*args):
    env=dict(os.environ,ORBIT_R3_CONFIG_ROOT=str(ROOT/'must-not-be-read'),PYTHONIOENCODING='ascii')
    return subprocess.run([sys.executable,'-B',str(ROOT/'scripts/publication_config_audit.py'),
        '--config-root',str(root),*args],cwd=ROOT,env=env,capture_output=True)


def test_missing_files_are_reported_without_creating_defaults(tmp_path):
    before=list(tmp_path.rglob('*'))
    result=audit(tmp_path)
    assert result.returncode==2,result.stderr
    value=json.loads(result.stdout.decode('utf-8'))
    assert value['execution_authority'] is False
    assert value['configuration']['status']=='BLOCKED'
    assert {r['status'] for r in value['configuration']['documents'].values()}=={'MISSING'}
    assert list(tmp_path.rglob('*'))==before


def test_valid_old_documents_are_redacted_and_do_not_grant_authority(tmp_path,monkeypatch):
    from test_b4b_publication_preview import governed_files
    governed_files(tmp_path,monkeypatch)
    p=tmp_path/'policy.json';value=json.loads(p.read_bytes())
    value['authority']['approved_by']='PRIVATE_AUTHORITY_MARKER'
    p.write_text(json.dumps(value),encoding='utf-8')
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result=audit(tmp_path,'--policy-path','policy.json','--incident-registry-path','incidents.json')
    assert result.returncode==0,result.stderr
    data=json.loads(result.stdout.decode('utf-8'))
    assert data['configuration']['status']=='CONFIGURED'
    assert data['execution_authority'] is False and data['current_request_authority']=='NOT_INFERRED'
    assert data['configuration']['documents']['policy']['content_digest']==hashlib.sha256(p.read_bytes()).hexdigest()
    assert b'PRIVATE_AUTHORITY_MARKER' not in result.stdout
    assert before=={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('relative',['../policy.json','C:/private/policy.json','policy.json:stream'])
def test_explicit_unsafe_config_path_is_rejected(tmp_path,relative):
    result=audit(tmp_path,'--policy-path',relative)
    assert result.returncode==2,result.stderr
    assert json.loads(result.stdout)['configuration']['documents']['policy']['status']=='UNSAFE_PATH'


def test_project_examples_cannot_be_executed_as_config():
    result=audit(ROOT,'--policy-path','config/examples/product_publication_autopilot_policy.example.json',
        '--incident-registry-path','config/examples/publication_incident_registry.example.json')
    assert result.returncode==2,result.stderr
    value=json.loads(result.stdout)
    assert value['configuration']['documents']['policy']['status']=='AUTHORITY_UNCONFIRMED'
    assert value['configuration']['documents']['incident_registry']['status']=='INVALID'
