import os
from pathlib import Path
import subprocess
import sqlite3
import json
import threading
from http.server import ThreadingHTTPServer

from PIL import Image
import pytest


def isolated_handler(server):
    class Handler(server.Handler):
        def _handle_product_flow_proxy(self, method):
            if self.path.startswith('/api/product-flow/'):
                self.send_error(503, 'No auxiliary service in this retained-state fixture')
                return True
            return False
    return Handler


def test_real_handler_local_intake_in_browser(tmp_path, monkeypatch):
    from modules.products import server
    monkeypatch.setattr(server,'ROOT',tmp_path)
    from shared_platform.publication_runtime_config import capture_startup_config
    monkeypatch.setattr(server, 'R3_STARTUP_CONFIG', capture_startup_config(root=tmp_path, environ={}))
    (tmp_path/'shared_platform').mkdir()
    source=Path(__file__).resolve().parents[1]
    (tmp_path/'shared_platform/entry_catalog.json').write_bytes((source/'shared_platform/entry_catalog.json').read_bytes())
    from shared_platform import release_control, release_store, report_store, publication_r3_image_bridge
    from modules.sourcing import new_product_workbench
    monkeypatch.setattr(release_control.build_release_dashboard,'__kwdefaults__',{**release_control.build_release_dashboard.__kwdefaults__,'root':tmp_path})
    monkeypatch.setattr(new_product_workbench,'ROOT',tmp_path)
    monkeypatch.setattr(new_product_workbench,'STATE_DIR',tmp_path/'data/new_product_workbench')
    monkeypatch.setattr(release_store,'DEFAULT_RELEASE_STORE_PATH',tmp_path/'data/orbit_platform.db')
    monkeypatch.setattr(report_store,'DEFAULT_REPORT_STORE_PATH',tmp_path/'data/orbit_platform.db')
    monkeypatch.setattr(publication_r3_image_bridge,'REPORTS_ROOT',tmp_path/'reports/product-preparation')
    (tmp_path/'data').mkdir()
    with sqlite3.connect(tmp_path/'data/shop.db') as db:
        db.execute('CREATE TABLE products (seller_sku TEXT)')
        db.execute('CREATE TABLE shopee_products (seller_sku TEXT)')
    httpd=ThreadingHTTPServer(('127.0.0.1',0),isolated_handler(server))
    thread=threading.Thread(target=httpd.serve_forever,daemon=True)
    thread.start()
    Image.new('RGB',(12,10),(15,80,130)).save(tmp_path/'tile.png')
    node=Path('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
    script=Path(__file__).parent/'browser/u01_workspace.js'
    try:
        result=subprocess.run([str(node),str(script),f'http://127.0.0.1:{httpd.server_port}',str(tmp_path)],
            capture_output=True,text=True,encoding='utf-8',timeout=90)
        assert result.returncode==0,result.stdout+result.stderr
    finally:
        httpd.shutdown();thread.join(timeout=3);httpd.server_close()


@pytest.mark.parametrize('unknown',[True,False])
def test_retained_common_uses_real_stage_store_and_current_dashboard(tmp_path,monkeypatch,unknown):
    from modules.products import server
    from shared_platform import release_control,release_store,publication_r3_image_bridge as bridge
    from test_b4b_publication_preview import multivariant_context
    from test_b4b_common_stage import approve
    seed=tmp_path/'accepted-frozen-producer'
    # Seed only from the accepted COMMON fixture producer. Its dashboard adapter
    # is removed before the browser; current dashboard, Handler and stage Store
    # all execute actual source. This is retained-run recovery, not a claim that
    # the historical producer fixture proves today's new-product preparation.
    with monkeypatch.context() as preparer:
        _,_,store,data,transport=multivariant_context(seed,preparer)
        exact=approve(data)
        transport.timeout=unknown
        status,_=server._prepare_miaoshou_release({**exact,'confirm_miaoshou_write':True})
        assert (status!=200)==unknown and transport.mutations==1
    monkeypatch.setattr(server,'ROOT',tmp_path)
    from shared_platform.publication_runtime_config import capture_startup_config
    monkeypatch.setattr(server, 'R3_STARTUP_CONFIG', capture_startup_config(root=tmp_path, environ={}))
    monkeypatch.setattr(release_store,'default_release_store',lambda:store)
    monkeypatch.setattr(bridge,'REPORTS_ROOT',seed/'reports')
    monkeypatch.setattr(release_control.build_release_dashboard,'__kwdefaults__',{**release_control.build_release_dashboard.__kwdefaults__,'root':tmp_path})
    monkeypatch.setattr(release_store,'DEFAULT_RELEASE_STORE_PATH',store.path)
    from shared_platform import report_store
    monkeypatch.setattr(report_store,'DEFAULT_REPORT_STORE_PATH',tmp_path/'data/reports.db')
    (tmp_path/'data/new_product_workbench').mkdir(parents=True)
    state={'offer_id':data['offer_id'],'_revision':44,'source':{'title_source':'Synthetic retained product',
        'source_authority':'1688','source_record':{'source_id':'986159122616'},'skus':[{'key':'blue','name':'Blue','price':2}]},
        'review':{'title':'Synthetic retained product','cost_cny':2,'weight_kg':0.1,'package_cm':[20,10,1],
            'selected_sku_keys':['blue'],'image_actions':[],'image_order':[],'fields_locked':False}}
    (tmp_path/'data/new_product_workbench'/f"{data['offer_id']}.json").write_text(json.dumps(state),encoding='utf-8')
    with sqlite3.connect(tmp_path/'data/shop.db') as db:
        db.execute('CREATE TABLE products (seller_sku TEXT)');db.execute('CREATE TABLE shopee_products (seller_sku TEXT)')
    source=Path(__file__).resolve().parents[1]
    (tmp_path/'shared_platform').mkdir()
    (tmp_path/'shared_platform/entry_catalog.json').write_bytes((source/'shared_platform/entry_catalog.json').read_bytes())
    if os.environ.get('U01_BASELINE_WEB'):
        monkeypatch.setattr(server,'WEB_DIR',Path(os.environ['U01_BASELINE_WEB']))
    httpd=ThreadingHTTPServer(('127.0.0.1',0),isolated_handler(server))
    thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('U01_BROWSER_SCENARIO','common-unknown' if unknown else 'common-verified')
    try:
        result=subprocess.run([str(Path('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')),
            str(source/'tests/browser/u01_workspace.js'),f'http://127.0.0.1:{httpd.server_port}',str(tmp_path)],capture_output=True,text=True,encoding='utf-8',timeout=90)
        assert result.returncode==0,result.stdout+result.stderr
        assert transport.mutations==1
    finally:
        httpd.shutdown();thread.join(timeout=3);httpd.server_close()
