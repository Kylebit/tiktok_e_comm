import json,subprocess,os
from test_u04_refresh import project,ROOT
from test_u04_complete_serving import serving,first_version

def test_captured_supply_composed_navigation(serving):
    root,a,p,s,server,get,dashboard=serving
    first_version(serving)
    (root/'shared_platform/entry_catalog.json').write_bytes((ROOT/'shared_platform/entry_catalog.json').read_bytes())
    args=[os.environ['ORBIT_NODE_BIN'],str(ROOT/'tests/u01_supply_navigation.cjs'),f'http://127.0.0.1:{server.server_port}',str(a)]
    result=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=60)
    (a/'navigation-command.json').write_text(json.dumps({'args':args,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}),encoding='utf-8')
    assert result.returncode==0,result.stdout+result.stderr
