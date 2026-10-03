"""One real-Chrome acceptance run against the original isolated HTTP Handler."""
import hashlib,json,os,subprocess,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCES=['web/profit_center.html','web/static/profit_center.js','web/static/profit_sku.js','web/static/profit_center.css','web/static/profit_review_model.js','web/static/operations_shell.js','shared_platform/orbit_registry.py','modules/products/server.py','tests/u05_finance_preview.py','tests/u05_finance_guard.py','tests/u05_finance_browser.cjs','tests/test_u05_sku_evidence.py','domains/data_operations/profit_settlement/captured_sku.py','modules/finance/sku_profit_model.py','domains/data_operations/profit_settlement/captured_waterfall.py','tests/test_u05_waterfall.py','web/static/profit_samples.js','domains/data_operations/profit_settlement/captured_samples.py','tests/test_u05_sample_audit.py']

def test_u05_real_finance_browser(tmp_path,record_property):
    node=Path(os.environ.get('ORBIT_NODE_BIN',str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')))
    assert node.is_file(),'Real Node/Chrome runtime required; no skip'
    out=tmp_path/'preview'
    before={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}
    with (tmp_path/'server.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,'-B',str(ROOT/'tests/u05_finance_preview.py'),'--out',str(out)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+15
            while not (out/'preview.json').exists():
                assert process.poll() is None,(tmp_path/'server.log').read_text(encoding='utf-8')
                assert time.monotonic()<deadline,'Preview startup timeout'
                time.sleep(.05)
            completed=subprocess.run([str(node),str(ROOT/'tests/u05_finance_browser.cjs'),str(out)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=120)
            assert completed.returncode==0,completed.stdout+completed.stderr
            result=json.loads((out/'browser-result.json').read_text(encoding='utf-8'))
            assert all(r['ok'] for r in result['results'])
            assert not result['blocked'] and not result['errors'] and not (out/'blocked-io.jsonl').exists()
            assert result['fixture']['handler_sha256']==before['modules/products/server.py']
            after={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}
            assert before==after,'Source changed during browser run'
            (out/'source-binding.json').write_text(json.dumps(before,indent=2))
            record_property('browser_evidence',str(out));record_property('browser_checks',len(result['results']))
        finally:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
