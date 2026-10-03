import json
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def test_dense_review_retains_state_until_dom_enhancer_is_initialized():
    source = Path(__file__).resolve().parents[1]
    script = source / 'web/static/product_workspace_dense.js'
    probe = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let readyCallback = null;
global.document = {
  querySelector: () => null,
  addEventListener: (name, callback) => {
    if (name === 'DOMContentLoaded') readyCallback = callback;
  },
};
global.window = {};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
assert.equal(typeof readyCallback, 'function');
assert.doesNotThrow(() => window.OrbitDenseReview.render({
  product: {offer_id: 'early-offer'},
  publication_scope: {selected_labels: []},
}));
"""
    run = subprocess.run(
        [os.environ['ORBIT_NODE_BIN'], '-e', probe, str(script)],
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=20,
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_actual_dense_review(tmp_path, monkeypatch):
    from modules.products import server
    from modules.sourcing import new_product_workbench as wb, manual_product_intake
    from shared_platform import release_control, release_store, report_store, publication_r3_image_bridge as bridge
    from test_u01_manual_intake import packet
    source = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(server, 'ROOT', tmp_path)
    from shared_platform.publication_runtime_config import capture_startup_config
    monkeypatch.setattr(server, 'R3_STARTUP_CONFIG', capture_startup_config(root=tmp_path, environ={}))
    monkeypatch.setattr(wb, 'ROOT', tmp_path)
    monkeypatch.setattr(wb, 'WORKSPACE_ROOT', tmp_path)
    monkeypatch.setattr(wb, 'OUTPUTS_DIR', tmp_path/'outputs')
    monkeypatch.setattr(wb, 'STATE_DIR', tmp_path/'data/new_product_workbench')
    monkeypatch.setattr(wb, 'IMAGE_SUITE_OUTPUTS_DIR', tmp_path/'outputs/image_suite_from_miaoshou')
    monkeypatch.setattr(release_control.build_release_dashboard, '__kwdefaults__', {**release_control.build_release_dashboard.__kwdefaults__, 'root':tmp_path})
    monkeypatch.setattr(release_store, 'DEFAULT_RELEASE_STORE_PATH', tmp_path/'data/orbit_platform.db')
    monkeypatch.setattr(report_store, 'DEFAULT_REPORT_STORE_PATH', tmp_path/'data/orbit_platform.db')
    monkeypatch.setattr(bridge, 'REPORTS_ROOT', tmp_path/'reports/product-preparation')
    (tmp_path/'shared_platform').mkdir()
    (tmp_path/'shared_platform/entry_catalog.json').write_bytes((source/'shared_platform/entry_catalog.json').read_bytes())
    data=packet()
    data['title']='Synthetic multi variant tile for dense review'
    data['variants']=[{**data['variants'][0], 'label':f'Blue tile {i+1} pack', 'purchase_cost_cny':str(i+3)} for i in range(6)]
    data['images']*=3
    created=manual_product_intake.create_manual_intake(data,root=tmp_path)
    offer=created['offer_id']
    state_path=wb.STATE_DIR/f'{offer}.json'
    state=json.loads(state_path.read_text(encoding='utf-8'))
    state['review']['fx_rates']={'MYR':0.62,'THB':4.8,'VND':3500,'PHP':8,'MXN':2.5,'GBP':0.11}
    assert all(row['action']=='review' for row in state['review']['image_actions'])
    assert state['review']['image_order']==[]
    state_path.write_text(json.dumps(state),encoding='utf-8')
    with sqlite3.connect(tmp_path/'data/shop.db') as db:
        db.execute('CREATE TABLE products (seller_sku TEXT)')
        db.execute('CREATE TABLE shopee_products (seller_sku TEXT)')

    class LocalConsumerHandler(server.Handler):
        delay_next_dashboard = False

        # Replace only the fixed-port proxy transport with the original local
        # consumer; no dashboard, preview, approval or store response is mocked.
        def _handle_product_flow_proxy(self, method):
            parsed=urlparse(self.path)
            if parsed.path=='/api/product-flow/preview' and method=='GET':
                self._json(200,wb.build_preview(parse_qs(parsed.query)['offer_id'][0]));return True
            if parsed.path=='/api/product-flow/review' and method=='POST':
                body=self._read_json()
                self._json(200,wb.save_review(body['offer_id'],body['review']));return True
            if parsed.path=='/api/product-flow/content-package/review' and method=='POST':
                body=self._read_json()
                try:
                    self._json(200,wb.save_content_package_review(body['offer_id'],body['review']))
                    type(self).delay_next_dashboard = True
                except ValueError as error:self._json(409,{'ok':False,'error':str(error)})
                return True
            if parsed.path.startswith('/api/product-flow/'):
                self.send_error(503,'Outside this local review fixture');return True
            return False

        def do_GET(self):
            if (
                urlparse(self.path).path == '/api/product-workspace/dashboard'
                and type(self).delay_next_dashboard
            ):
                type(self).delay_next_dashboard = False
                # Make the post-save refresh window deterministic.  The UI's
                # saved receipt must mean the refreshed review is usable, even
                # when the authoritative readback is slower than usual.
                time.sleep(0.35)
            return super().do_GET()

    httpd=ThreadingHTTPServer(('127.0.0.1',0),LocalConsumerHandler)
    worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
    (tmp_path/'scenario.json').write_text(json.dumps({'offer_id':offer}),encoding='utf-8')
    try:
        run=subprocess.run([os.environ['ORBIT_NODE_BIN'],str(source/'tests/browser/u01_dense_review.cjs'),
            f'http://127.0.0.1:{httpd.server_port}',str(tmp_path)],capture_output=True,text=True,encoding='utf-8',timeout=100)
        assert run.returncode==0,run.stdout+run.stderr
        saved=json.loads(state_path.read_text(encoding='utf-8'))
        assert [row['action'] for row in saved['review']['image_actions']]==['keep','review','review']
        assert len(saved['review']['image_order'])==1
        proof=json.loads((tmp_path/'FACTS_READBACK.json').read_text(encoding='utf-8'))
        edited,removed=proof['edited'],proof['removed']
        expected_keys=[key for key in proof['keys'] if key!=removed]
        expected=dict(state['review']['sku_commercial_facts'])
        expected.pop(removed)
        expected[edited]={'cost_cny':12.34,'weight_kg':0.789,'package_cm':[31,22,9]}
        assert saved['review']['selected_sku_keys']==expected_keys
        from decimal import Decimal
        def amounts(rows):
            return {key:{'cost_cny':Decimal(str(row['cost_cny'])),
                         'weight_kg':Decimal(str(row['weight_kg'])),
                         'package_cm':[Decimal(str(n)) for n in row['package_cm']]}
                    for key,row in rows.items()}
        assert amounts(saved['review']['sku_commercial_facts'])==amounts(expected)
        assert amounts(proof['after']['product']['sku_commercial_facts'])==amounts(expected)
        assert proof['after']['product']['selected_sku_keys']==expected_keys
        assert saved['review']['image_order']==[saved['review']['image_actions'][0]['url']]
    finally:
        httpd.shutdown();worker.join(timeout=3);httpd.server_close()
