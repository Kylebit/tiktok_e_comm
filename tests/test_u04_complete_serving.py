import hashlib
import http.client
import importlib
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import threading
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
import pytest
from test_u04_refresh import project,cli,refresh,git,write,ROOT,command,environment
from test_u04_captured_refresh import captured_inputs,resave

@pytest.fixture
def serving(project,monkeypatch):
    root,a,p,s=captured_inputs(project)
    seed=json.loads((a/'seed.json').read_text())
    for region,config in seed['config'].items():
        config.update(name=region+' synthetic',freightMode='synthetic',currencySymbol='S$',targetDays=30,safetyDays=7,weightRatioKgM3=167,fixedHeadFreightUnitCny=1,taxSavingRate=0,shippingSavingRate=.2,fxToCny=1,demandCoverage='synthetic complete',shippingCoverage='synthetic evidence')
        seed['countries'][region][0]['image']='assets/synthetic.svg'
        s['inbound']['regions'][region]['pages'][0]['rows'][0]['transportDays']=6
    (a/'applied/assets/synthetic.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="#075b46"/><text x="8" y="36" fill="white">TEST</text></svg>')
    resave(a,p,'inbound',s['inbound'])
    resave(a,p,'seed',seed)
    dashboard=root/'domains/supply_chain_operations/dashboard';dashboard.mkdir(parents=True)
    source=ROOT/'domains/supply_chain_operations/dashboard'
    for name in ('index.html','inbound-batches.html','app.js','inbound-batches.js','inbound-timeline.js','transport-history.js','styles.css','captured-bootstrap.js'):
        if (source/name).exists():shutil.copyfile(source/name,dashboard/name)
    (dashboard/'data.js').write_text('window.SUPPLY_CHAIN_DATA = {"legacy":true};')
    (dashboard/'inbound-plan.js').write_text('window.SUPPLY_CHAIN_INBOUND_PLAN = {"legacy":true};')
    git(root,'add','.');git(root,'-c','user.name=U04 fixture','-c','user.email=u04@example.invalid','commit','-m','Synthetic serving consumer')
    p['source_commit']=git(root,'rev-parse','HEAD');write(a/'profile.json',p)
    import core.config as config
    def forbidden(*args,**kwargs):raise AssertionError('unrelated real I/O forbidden')
    monkeypatch.setattr(config,'load_settings',forbidden);monkeypatch.setattr(sqlite3,'connect',forbidden);monkeypatch.setattr(urllib.request,'urlopen',forbidden)
    module=importlib.import_module('modules.products.server');monkeypatch.setattr(module,'ROOT',root)
    server=ThreadingHTTPServer(('127.0.0.1',0),module.Handler);server.daemon_threads=True
    server.supply_chain_capture={'artifact_root':'outputs/refresh','output_root':'applied'}
    connect=socket.socket.connect
    def only_local(sock,address):
        assert address==('127.0.0.1',server.server_port),'outside fixture socket'
        return connect(sock,address)
    monkeypatch.setattr(socket.socket,'connect',only_local)
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.01});thread.start()
    def get(target):
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
        try:
            connection.request('GET',target);response=connection.getresponse();return response.status,dict(response.headers),response.read()
        finally:connection.close()
    try:yield root,a,p,s,server,get,dashboard
    finally:server.shutdown();server.server_close();thread.join(5);assert not thread.is_alive()

def test_actual_complete_page_is_pinned_not_legacy(serving):
    root,a,p,s,server,get,dashboard=serving
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0,stage
    result,applied=cli(root,a,'--captured-apply',stage['stage_digest']);assert result.returncode==0,applied
    status,headers,body=get('/supply-chain/')
    assert status==200 and b'data-captured-version="'+stage['stage_digest'].encode()+b'"' in body
    assert headers['Cache-Control']=='no-store'

def first_version(serving):
    root,a,p,s,server,get,dashboard=serving
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0,stage
    result,applied=cli(root,a,'--captured-apply',stage['stage_digest']);assert result.returncode==0,applied
    return stage['stage_digest']

def next_stage(serving):
    root,a,p,s,server,get,dashboard=serving
    seed=json.loads((a/'applied/data.js').read_text().removeprefix('window.SUPPLY_CHAIN_DATA = ').strip().removesuffix(';'));resave(a,p,'seed',seed)
    s['inventory']['sourceId']='second synthetic inventory'
    for r in refresh.REGIONS:
        s['inventory']['regions'][r]['pages'][0]['rows'][0].update(stock=6,available=6)
        s['inbound']['regions'][r]['pages'][0]['rows'][0]['estimatedAnchorAt']='2026-09-06T00:00:00+00:00'
    resave(a,p,'inventory',s['inventory']);resave(a,p,'inbound',s['inbound'])
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0,stage
    return stage['stage_digest']

def pair_get(get,version):
    bodies={}
    for name in ('data.js','inbound-plan.js'):
        code,headers,body=get('/supply-chain/captured/'+version+'/'+name);assert code==200
        bodies[name]=body
    return bodies

def wait_file(path,process):
    end=time.monotonic()+20
    while not path.exists():
        if process.poll() is not None:
            stdout,stderr=process.communicate()
            write(path.parent/'early-process-exit.json',{'exit':process.returncode,'stdout':stdout.decode(),'stderr':stderr.decode()})
            pytest.fail(stderr.decode() or 'owned process stopped before gate')
        assert time.monotonic()<end,'owned gate timed out'
        time.sleep(.02)

def test_actual_http_between_replacements_crash_recovery_and_reuse(serving):
    root,a,p,s,server,get,dashboard=serving;one=first_version(serving);old=pair_get(get,one);two=next_stage(serving)
    guard=root/'outputs/guard/sitecustomize.py';original=guard.read_text()
    guard.write_text(original+"\nimport os,time\nfrom pathlib import Path\n_replace=os.replace\ndef replace(src,dst):\n _replace(src,dst)\n if str(dst).replace('\\\\','/').endswith('/applied/data.js'):\n  gate=Path(dst).parent.parent\n  (gate/'first-replaced.flag').write_text('paused')\n  while not (gate/'crash-now.flag').exists():time.sleep(.02)\n  os._exit(73)\nos.replace=replace\n",encoding='utf-8')
    process=subprocess.Popen(command(root,a,'--captured-apply',two),cwd=a,env=environment(root),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        wait_file(a/'first-replaced.flag',process)
        assert get('/supply-chain/')[0]==503
        assert pair_get(get,one)==old
        assert (a/'applied/data.js').read_bytes()!=old['data.js'] and (a/'applied/inbound-plan.js').read_bytes()==old['inbound-plan.js']
        (a/'crash-now.flag').write_text('stop');stdout,stderr=process.communicate(timeout=10);assert process.returncode==73
    finally:
        if process.poll() is None:process.kill();process.communicate(timeout=5)
        guard.write_text(original,encoding='utf-8')
    write(a/'owned-apply-process.json',{'pid':process.pid,'exit':process.returncode,'command':process.args,'stdout':stdout.decode(),'stderr':stderr.decode()})
    assert get('/supply-chain/')[0]==503
    result,recovered=cli(root,a,'--captured-apply',two);assert result.returncode==0 and recovered['recovered']
    assert two.encode() in get('/supply-chain/')[2] and pair_get(get,one)==old
    new=pair_get(get,two);assert all(new[f]!=old[f] for f in old)
    result,reused=cli(root,a,'--captured-apply',two);assert result.returncode==0 and reused['state']=='REUSED'
    assert pair_get(get,two)==new

@pytest.mark.parametrize('bad',['missing_journal','bad_journal','journal_hash','missing_index','bad_index','complete_hash','stage_manifest','stage_bytes','live_output','wrong_output','oversize_index','duplicate_version'])
def test_tampering_unavailable_never_legacy_or_zero(serving,bad):
    root,a,p,s,server,get,dashboard=serving;one=first_version(serving)
    if bad=='missing_journal':(a/'captured-apply.json').unlink()
    elif bad=='bad_journal':(a/'captured-apply.json').write_text('{')
    elif bad=='journal_hash':
        journal=json.loads((a/'captured-apply.json').read_text());journal['output_sha256']['data.js']='0'*64;write(a/'captured-apply.json',journal)
    elif bad=='missing_index':(a/'captured-serving.json').unlink()
    elif bad=='bad_index':(a/'captured-serving.json').write_text('{}')
    elif bad=='complete_hash':(a/'captured-stages'/one/'complete.json').write_text('{}')
    elif bad=='stage_manifest':(a/'captured-stages'/one/'stage.json').write_text('{}')
    elif bad=='stage_bytes':(a/'captured-stages'/one/'data.js').write_text('changed')
    elif bad=='live_output':(a/'applied/data.js').write_text('changed')
    elif bad in ('oversize_index','duplicate_version'):
        index=json.loads((a/'captured-serving.json').read_text());index['versions']*=33 if bad=='oversize_index' else 2;write(a/'captured-serving.json',index)
    else:server.supply_chain_capture['output_root']='another'
    code,headers,body=get('/supply-chain/');assert code==503 and b'legacy' not in body and b'SUPPLY_CHAIN_DATA' not in body

@pytest.mark.parametrize('suffix',['captured/'+('0'*64)+'/data.js','captured/../data.js','captured/%2e%2e/data.js','captured/'+('a'*64)+'/../../profile.json','../profile.json','assets/../../profile.json','assets/%2e%2e/profile.json'])
def test_version_and_static_boundary(serving,suffix):
    *_,get,dashboard=serving;first_version(serving)
    assert get('/supply-chain/'+suffix)[0]==404

def test_legacy_without_config_keeps_original_page_and_bytes(serving):
    root,a,p,s,server,get,dashboard=serving;server.supply_chain_capture=None
    for name in ('index.html','inbound-batches.html','data.js','inbound-plan.js'):
        code,headers,body=get('/supply-chain/'+name);assert code==200 and body==(dashboard/name).read_bytes()

def test_unversioned_pair_rejected_and_original_image_preserved(serving):
    root,a,p,s,server,get,dashboard=serving;first_version(serving)
    for name in ('data.js','inbound-plan.js'):assert get('/supply-chain/'+name)[0]==409
    code,headers,body=get('/supply-chain/assets/synthetic.svg')
    assert code==200 and body==(a/'applied/assets/synthetic.svg').read_bytes()

def test_first_incomplete_apply_cannot_be_served(serving):
    root,a,p,s,server,get,dashboard=serving
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0
    write(a/'captured-apply.json',{'state':'APPLYING','stage_digest':stage['stage_digest'],'output_root':str(a/'applied'),'output_sha256':stage['output_sha256']})
    assert get('/supply-chain/')[0]==503 and get('/supply-chain/captured/'+stage['stage_digest']+'/data.js')[0]==503

@pytest.mark.parametrize('width',[1440,390])
def test_actual_browser_refresh_between_data_resources_preserves_overlay(serving,width):
    root,a,p,s,server,get,dashboard=serving;one=first_version(serving)
    node='C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    args=[node,str(ROOT/'tests/u04_complete_browser.cjs'),f'http://127.0.0.1:{server.server_port}',str(a),one,str(width)]
    process=subprocess.Popen(args,cwd=a,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        wait_file(a/'between-resources.flag',process)
        two=next_stage(serving);result,applied=cli(root,a,'--captured-apply',two);assert result.returncode==0,applied
        write(a/'advance.json',{'version':two})
        stdout,stderr=process.communicate(timeout=30)
        write(a/'browser-command.json',{'command':args,'pid':process.pid,'exit':process.returncode,'stdout':stdout.decode(),'stderr':stderr.decode()})
        assert process.returncode==0,stderr.decode()
        proof=json.loads((a/'browser-result.json').read_text());assert proof['interleavedVersion']==one and proof['newVersion']==two and proof['overlayPreserved'] and not proof['pageErrors']
        for version in (one,two):
            expected=json.loads((a/'captured-stages'/version/'stage.json').read_text())['output_sha256']
            for row in proof['responses']:
                if '/'+version+'/' in row['url']:assert row['sha256']==expected[row['url'].rsplit('/',1)[1]]
    finally:
        if process.poll() is None:process.kill();process.communicate(timeout=5)
