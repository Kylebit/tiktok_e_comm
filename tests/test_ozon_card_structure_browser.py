"""Real Chromium of the original HTML/CSS/renderer, inert display fixture only."""
import json
import os
from pathlib import Path
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from shared_platform.release_store import RELEASE_TARGET_LABELS
from shared_platform.publication_ozon_card_structure import independent_card
from test_release_ux_contract import _browser_runtime


def test_original_review_shows_ozon_three_cards_and_fourteen_variant_products(tmp_path):
    root=Path(__file__).resolve().parents[1]
    http=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(root/'web')))
    thread=threading.Thread(target=http.serve_forever);thread.start()
    try:
        cards=[{'canonical_sku':sku,'variant_key':'fixture-'+sku,**independent_card('200'+sku)}
            for sku in ('0431','0432','0433')]
        rows=[]
        for target in RELEASE_TARGET_LABELS[1:]:
            row={'target_label':target,'locale':'ru-RU' if target=='ozon:RU' else 'en',
                'category':{'id':'53968796','name':'Fixture category'},'prices':[],
                'listing_structure':{'note_zh':'Ozon 单独建立 3 条新 B 链接，每条一个花型、每件 1 片；原 A 保留。',
                    'cards':cards} if target=='ozon:RU' else {'note_zh':'本店一条新 B 链接，包含全部 3 个花型规格；原 A 保留。'}}
            rows.append(row)
        manifest={'schema_version':'publication-final-review-manifest/v1','manifest_digest':'presentation-fixture',
            'product':{'ozon_card_structure':{'fixture_kind':'PRESENTATION_ONLY','cards':cards}},
            'targets':rows,'variants':[],'copy_sets':[],'image_sets':[]}
        candidate={'schema_version':'publication-release-candidate/v1','offer_id':'80001','plan_id':'display-only',
            'candidate_digest':'display-only','review_manifest':manifest,'target_labels':list(RELEASE_TARGET_LABELS[1:])}
        value={'offer_id':'80001','marketplace':{'preview':candidate,
            'plan':{'plan_id':'display-only','payload':{'product_id':'80001','product_facts':{'title':'Presentation fixture'},
                'r3_marketplace_binding':{'reviewed_candidate_facts':{'review_manifest':manifest}}}},
            'native_final_review':{'schema_version':'native-sole-final-review/v1','candidate_digest':'display-only'}}}
        fixture=tmp_path/'presentation-only.json';fixture.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        available=_browser_runtime();assert available,'Real Chromium is required'
        node,modules=available
        run=subprocess.run([str(node),str(root/'tests/browser/ozon_card_structure_review.cjs'),
            f'http://127.0.0.1:{http.server_port}',str(tmp_path),'80001',str(fixture)],
            env=dict(os.environ,NODE_PATH=str(modules)),capture_output=True,text=True,encoding='utf-8',timeout=70)
        assert run.returncode==0,run.stdout+run.stderr
        proof=json.loads(run.stdout)
        assert proof['widths']==[1440,390] and proof['posts']==[] and proof['errors']==[]
    finally:
        http.shutdown();http.server_close();thread.join(timeout=5)
