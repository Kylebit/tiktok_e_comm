"""Real getter, real native Handler/actor and the user's sole existing button."""
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

import pytest

from modules.products import server
from shared_platform import release_control,publication_r3_image_bridge as bridge
from test_round1_workspace_freeze import live
from test_native_sole_final_service import _installed,_registry,_http
from test_release_ux_contract import _browser_runtime


@pytest.mark.parametrize('changed_display',[False,True],ids=['same-displayed-review','changed-prepare-review'])
def test_real_sole_review_button_compares_shown_full_review_before_deciding_and_displays_original_targets(live,monkeypatch,tmp_path,changed_display):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    calls=_registry(monkeypatch)
    http,thread=_http(installed)
    try:
        code,getter=server._publication_stages_for_request({'offer_id':market['product_id']})
        assert code==200 and getter['marketplace']['final_review_available'] is True,getter
        def urls(value):
            if isinstance(value,dict):return set().union(*(urls(part) for part in value.values()))
            if isinstance(value,(list,tuple)):return set().union(*(urls(part) for part in value))
            return {value} if isinstance(value,str) and value.startswith('https://') else set()
        media=urls(release_control.build_release_dashboard(offer_id=market['product_id']))|urls(bridge.load_r2_documents(market['product_id']))|urls(getter)
        media={url for url in media if urlsplit(url).hostname in {'example.com','fixture.example'}
            and Path(urlsplit(url).path).suffix.lower() in {'.png','.jpg','.jpeg','.webp','.svg'}}
        expected=tmp_path/'native-shown-review.json'
        expected.write_text(json.dumps({'getter':getter,'media':sorted(media)},ensure_ascii=False),encoding='utf-8')
        available=_browser_runtime();assert available,'Real Chromium is required; no skip'
        node,modules=available
        result=subprocess.run([str(node),str(Path(__file__).parent/'browser/native_sole_final_flow.cjs'),
            f'http://127.0.0.1:{http.server_port}',str(tmp_path),market['product_id'],
            'changed' if changed_display else 'same',str(expected)],
            env=dict(os.environ,NODE_PATH=str(modules)),capture_output=True,text=True,encoding='utf-8',timeout=100)
        assert result.returncode==0,result.stdout+result.stderr
        proof=json.loads(result.stdout)
        assert proof['widths']==[1440,390] and proof['errors']==[]
        with installed.store._connect_readonly() as db:
            approvals=db.execute('SELECT COUNT(*) FROM native_sole_final_decisions').fetchone()[0]
        if changed_display:
            assert approvals==0 and calls==[] and proof['decision_posts']==0
        else:
            installed._execution_thread.join(timeout=40)
            assert not installed._execution_thread.is_alive()
            assert approvals==1 and proof['decision_posts']==1
            assert len(calls)==len(market['targets']) and {request.target_label for request in calls}==set(market['targets'])
            with installed._operations.engine.transaction() as db:
                task=db.execute("SELECT state,action_json FROM workbench_execution WHERE template='publication'").fetchone()
                assert task['state']=='waiting_domain' and json.loads(task['action_json'])['kind']=='observe'
    finally:
        http.shutdown();http.server_close();thread.join(timeout=5);installed.close()
        assert not thread.is_alive()
