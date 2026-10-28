"""COMMON projection keeps source/user video, independent of image replacement."""
import ast
from copy import deepcopy
import os
import subprocess

import pytest
from shared_platform import publication_r3_image_bridge as bridge
from test_b4b_common_stage import context


@pytest.mark.parametrize('videos', [[], ['https://source.invalid/original.mp4'],
                                  ['https://user-edit.invalid/selected.mp4']])
def test_common_projection_retains_video_and_other_frozen_fields(tmp_path, monkeypatch, videos):
    documents,dashboard,store,_=context(tmp_path,monkeypatch)
    dashboard.setdefault('content',{})['video_urls']=deepcopy(videos)
    before=deepcopy(dashboard);documents_before=deepcopy(documents)
    baseline=subprocess.check_output(['git','show','70e2bacf2044a5e44bd81e48af42d4da3e2a43d4:shared_platform/publication_r3_image_bridge.py'],text=True,encoding='utf-8')
    node=next(n for n in ast.parse(baseline).body if isinstance(n,ast.FunctionDef) and n.name=='prepare_common_stage_dashboard')
    namespace=vars(bridge).copy()
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<accepted-70e2bac-projection>','exec'),namespace)
    original=namespace['prepare_common_stage_dashboard'](dashboard,documents)
    project=namespace['prepare_common_stage_dashboard'] if os.environ.get('RESTORE_VERIFY_BASELINE')=='1' else bridge.prepare_common_stage_dashboard
    current=project(dashboard,documents)
    assert current['content']['video_urls']==videos
    assert current['content']['video_urls'] is not dashboard['content']['video_urls']
    assert dashboard==before and documents==documents_before
    assert not store.path.exists()
    expected=deepcopy(original);expected['content']['video_urls']=deepcopy(videos)
    assert current==expected
    current['content']['video_urls'].append('https://test.invalid/independent-copy.mp4')
    assert dashboard==before
