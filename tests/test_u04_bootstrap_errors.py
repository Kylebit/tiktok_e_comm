import subprocess,json
from pathlib import Path
import pytest

@pytest.mark.parametrize('mode',['throw','reject','normal','abort'])
def test_actual_chrome_controlled_initialization(tmp_path,mode):
    root=Path(__file__).resolve().parents[1]
    args=['C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe',str(root/'tests/u04_bootstrap_errors.cjs'),str(root/'domains/supply_chain_operations/dashboard/captured-bootstrap.js'),str(tmp_path/'observed.json'),mode]
    run=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=30)
    (tmp_path/'command.json').write_text(json.dumps({'command':args,'exit':run.returncode,'stdout':run.stdout,'stderr':run.stderr}),encoding='utf-8')
    assert run.returncode==0,run.stderr
