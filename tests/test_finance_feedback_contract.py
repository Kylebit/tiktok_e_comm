"""Current finance feedback, from actual captured inputs and the full Handler."""
import json,os,subprocess,sys,time
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def finance_feedback_contract(tmp_path_factory):
    root=Path(__file__).resolve().parents[1]
    out=tmp_path_factory.mktemp('finance-feedback')/'preview'
    with (out.parent/'server.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,'-B',str(root/'tests/u05_finance_preview.py'),'--out',str(out)],
            cwd=root,stdout=log,stderr=subprocess.STDOUT,env={**os.environ,'ORBIT_FULL_HANDLER':'1'},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+20
            while not (out/'preview.json').exists():
                assert process.poll() is None,(out.parent/'server.log').read_text(errors='replace')
                assert time.monotonic()<deadline
                time.sleep(.05)
            run=subprocess.run([os.environ['ORBIT_NODE_BIN'],str(root/'tests/browser/finance_feedback.cjs'),str(out)],capture_output=True,text=True,encoding='utf-8',timeout=60)
            assert run.returncode==0,run.stdout+run.stderr
            value=json.loads((out/'feedback.json').read_text(encoding='utf-8'))
            assert not value['errors'] and not value['blocked'],value
            assert not (out/'blocked-io.jsonl').exists()
            return value
        finally:
            process.terminate();process.wait(timeout=5)
