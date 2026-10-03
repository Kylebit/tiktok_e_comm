import pytest

from modules.products import server
from test_b4b_common_stage import context


def test_common_preview_accepts_r1_dictionary_category_semantic(tmp_path, monkeypatch):
    documents, dashboard, store, request = context(tmp_path, monkeypatch)
    semantic = documents['first_review']['product_facts']['category_semantic']
    semantic = semantic.get('name') if isinstance(semantic, dict) else semantic
    assert isinstance(semantic, str) and semantic.strip()
    dashboard['product']['category'] = {'name': '  ' + semantic + '  '}
    status, result = server._preview_r3_common_stage(request)
    assert status == 200, result
    assert result['common']['plan']['payload']['r3_stage_binding']['round1_snapshot_digest'] == documents['round1_snapshot']['snapshot_digest']
    assert result['external_writes_performed'] == []
    assert not store.path.exists()


def test_common_preview_preserves_r1_string_category_semantic(tmp_path, monkeypatch):
    import json
    from shared_platform import publication_r3_image_bridge as bridge
    documents, dashboard, store, request = context(tmp_path, monkeypatch)
    semantic = documents['first_review']['product_facts']['category_semantic']
    semantic = semantic.get('name') if isinstance(semantic, dict) else semantic
    assert isinstance(semantic, str) and semantic.strip()
    dashboard['product']['category'] = '  ' + semantic + '  '
    frozen_bytes = json.dumps(documents, sort_keys=True)
    result = bridge.prepare_common_stage_dashboard(dashboard, documents)
    assert result['product']['category'] == dashboard['product']['category']
    assert result['content']['strategy'] == 'R3_COMMON_FROZEN_R2_BASELINE'
    assert result['publication_scope']['selected_labels'] == ['miaoshou:COMMON']
    assert json.dumps(documents, sort_keys=True) == frozen_bytes
    assert not store.path.exists()


@pytest.mark.parametrize('category', [
    {'name': 'Different category'}, {'name': ''}, {'name': '  '},
    {'name': 123}, {'id': 'only-id'}, ['Home decor'], None,
], ids=['drift', 'empty', 'blank', 'numeric-name', 'missing-name', 'list', 'none'])
def test_common_preview_rejects_category_drift_or_unrecognized_semantic(tmp_path, monkeypatch, category):
    _, dashboard, store, request = context(tmp_path, monkeypatch)
    dashboard['product']['category'] = category
    status, result = server._preview_r3_common_stage(request)
    assert status == 409, result
    assert result['error'] == 'COMMON_R1_CURRENT_PRODUCT_IDENTITY_CONFLICT'
    assert result['external_writes_performed'] == []
    assert not store.path.exists()
