import base64
from copy import deepcopy
import importlib
import io
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
import pytest


def service():
    return importlib.import_module('modules.sourcing.manual_product_intake')


def packet():
    stream = io.BytesIO()
    Image.new('RGB', (12, 10), (15, 80, 130)).save(stream, format='PNG')
    return {'request_id': '412a3859-8393-480b-bc45-1d6b11874513', 'title': 'Local blue tile',
        'description': 'User supplied local description',
        'variants': [{'label': 'Blue 10 cm', 'purchase_cost_cny': '3.25', 'weight_kg': '0.12', 'package_cm': [10, 10, 2]}],
        'images': [{'name': 'tile.png', 'mime_type': 'image/png', 'data_base64': base64.b64encode(stream.getvalue()).decode()}]}


def test_local_create_replay_and_changed_request_preserve_identity_and_edit(tmp_path):
    mod = service()
    data = packet()
    first = mod.create_manual_intake(data, root=tmp_path)
    state_path = tmp_path/'data/new_product_workbench'/f"{first['offer_id']}.json"
    state = json.loads(state_path.read_text(encoding='utf-8'))
    assert state['source']['source_mode'] == 'manual_intake'
    assert state['source']['source_authority'] == 'manual-intake'
    assert state['source']['source_url'] == '' and not state['review']['fields_locked']
    assert all(row['action']=='review' for row in state['review']['image_actions'])
    assert state['review']['image_order']==[]
    state['_revision'] = 2
    state['review']['title'] = 'User edited title'
    state_path.write_text(json.dumps(state), encoding='utf-8')
    again = mod.create_manual_intake(data, root=tmp_path)
    assert again['offer_id'] == first['offer_id'] and again['idempotent']
    assert json.loads(state_path.read_text())['review']['title'] == 'User edited title'
    assert first['external_writes_performed'] == []
    data['title'] = 'Changed after unknown response'
    with pytest.raises(ValueError): mod.create_manual_intake(data, root=tmp_path)
    assert len(list((tmp_path/'data/product_intake').glob('[0-9]*'))) == 1


@pytest.mark.parametrize('change', ['bad_request','zero','nonfinite','huge_number','duplicate_variants','too_many_images','truncated','wrong_mime','bad_base64'])
def test_rejected_input_creates_no_product(tmp_path, change):
    data = packet()
    if change == 'bad_request': data['request_id'] = '../../outside'
    elif change == 'zero': data['variants'][0]['weight_kg'] = 0
    elif change == 'nonfinite': data['variants'][0]['purchase_cost_cny'] = 'NaN'
    elif change == 'huge_number': data['variants'][0]['purchase_cost_cny'] = '1e1000'
    elif change == 'duplicate_variants': data['variants'] *= 2
    elif change == 'too_many_images': data['images'] *= 13
    elif change == 'truncated': data['images'][0]['data_base64'] = base64.b64encode(b'\x89PNG\r\n\x1a\ninvalid').decode()
    elif change == 'wrong_mime': data['images'][0]['mime_type'] = 'image/jpeg'
    else: data['images'][0]['data_base64'] = '@@bad'
    with pytest.raises(ValueError): service().create_manual_intake(data, root=tmp_path)
    assert not list(tmp_path.rglob('record.json'))


def test_interrupted_state_install_resumes_original_product(tmp_path, monkeypatch):
    mod = service()
    original = mod._install_state
    count = 0
    def interrupt(*a, **k):
        nonlocal count
        count += 1
        if count == 2: raise OSError('synthetic interrupted workbench install')
        return original(*a, **k)
    with monkeypatch.context() as m:
        m.setattr(mod, '_install_state', interrupt)
        with pytest.raises(OSError): mod.create_manual_intake(packet(), root=tmp_path)
    records = list(tmp_path.rglob('record.json'))
    assert len(records) == 1
    original_id = json.loads(records[0].read_text())['offer_id']
    result = mod.create_manual_intake(packet(), root=tmp_path)
    assert result['offer_id'] == original_id and result['idempotent']
    assert (tmp_path/'data/new_product_workbench'/f'{original_id}.json').is_file()


def test_concurrent_duplicate_requests_create_one_product(tmp_path):
    mod = service()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: mod.create_manual_intake(packet(), root=tmp_path), range(4)))
    assert len({r['offer_id'] for r in results}) == 1
    assert len(list(tmp_path.rglob('record.json'))) == 1


def test_image_binding_rejects_traversal_tamper_and_wrong_record(tmp_path):
    mod = service()
    result = mod.create_manual_intake(packet(), root=tmp_path)
    offer = result['offer_id']
    image = mod.resolve_manual_intake_image(offer, 'image-01.png', root=tmp_path)
    assert image and image.is_file()
    assert mod.resolve_manual_intake_image('../'+offer, 'image-01.png', root=tmp_path) is None
    assert mod.resolve_manual_intake_image(offer, '../record.json', root=tmp_path) is None
    record_path = image.parent.parent/'record.json'
    record = json.loads(record_path.read_text())
    record['offer_id'] = '999'
    record_path.write_text(json.dumps(record), encoding='utf-8')
    assert mod.resolve_manual_intake_image(offer, 'image-01.png', root=tmp_path) is None
    record['offer_id'] = offer
    record_path.write_text(json.dumps(record), encoding='utf-8')
    image.write_bytes(b'changed')
    assert mod.resolve_manual_intake_image(offer, 'image-01.png', root=tmp_path) is None


def test_symlink_root_is_rejected_before_writes(tmp_path):
    mod = service()
    candidate = tmp_path/'candidate'
    (candidate/'data').mkdir(parents=True)
    outside = tmp_path/'unrelated'
    outside.mkdir()
    (candidate/'data/product_intake').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError): mod.create_manual_intake(packet(), root=candidate)
    assert list(outside.iterdir()) == []


def test_source_summary_preserves_manual_identity(tmp_path, monkeypatch):
    mod = service()
    result = mod.create_manual_intake(packet(), root=tmp_path)
    from modules.sourcing import new_product_workbench as workbench
    monkeypatch.setattr(workbench, 'ROOT', tmp_path)
    monkeypatch.setattr(workbench, 'STATE_DIR', tmp_path/'data/new_product_workbench')
    source = workbench._source_summary(result['offer_id'])
    assert source['source_mode'] == 'manual_intake'
    assert source['source_authority'] == 'manual-intake' and source['source_url'] == ''
    assert source['skus'][0]['weight_kg'] == '0.12'


@pytest.mark.parametrize('action', ['review','pending','remove',None,'keep'])
def test_only_explicit_keep_enters_workflow_image_count(tmp_path,action):
    result=service().create_manual_intake(packet(),root=tmp_path)
    state=json.loads((tmp_path/'data/new_product_workbench'/f"{result['offer_id']}.json").read_text(encoding='utf-8'))
    state['review']['image_actions'][0]['action']=action
    from modules.sourcing import new_product_workbench as wb
    workflow=wb._product_workflow_summary(source=state['source'],review=state['review'],content={},miaoshou_draft={},tiktok_claim={},site_drafts={})
    assert workflow['kept_source_image_count']==int(action=='keep')
    assert not workflow['content_ready']
    if action!='keep':assert not workflow['image_review_ready']
    assert state['review']['image_order']==[]


def test_actual_http_local_intake_image_and_history(tmp_path, monkeypatch):
    from modules.products import server
    from http.server import ThreadingHTTPServer
    import threading
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    monkeypatch.setattr(server, 'ROOT', tmp_path)
    httpd = ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    thread = threading.Thread(target=httpd.serve_forever,daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{httpd.server_port}'
    def request(path, data=None, origin=None):
        req = Request(base+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json',**({'Origin':origin} if origin else {})})
        try: response = urlopen(req,timeout=5)
        except HTTPError as e: response=e
        with response: return response.status,response.read()
    try:
        code, raw = request('/api/product-workspace/manual-intake',packet())
        assert code == 200,raw
        result=json.loads(raw)
        offer=result['offer_id']
        code,image=request(f'/api/product-workspace/manual-intake-image?offer_id={offer}&image=image-01.png')
        assert code==200 and image==base64.b64decode(packet()['images'][0]['data_base64'])
        code,raw=request('/api/product-workspace/history')
        history=json.loads(raw)
        assert code==200 and history['items'][0]['offer_id']==offer
        assert history['items'][0]['source_mode']=='manual_intake'
        assert history['items'][0]['execution_authority'] is False
        assert request('/api/product-workspace/manual-intake',packet(),'https://foreign.invalid')[0]==403
        assert request(f'/api/product-workspace/manual-intake-image?offer_id={offer}&image=../record.json')[0]==404
    finally:
        httpd.shutdown();thread.join(timeout=3);httpd.server_close()
