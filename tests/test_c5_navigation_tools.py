from pathlib import Path
import pytest
from shared_platform.orbit_registry import navigation_payload

ROOT=Path(__file__).resolve().parents[1]

def test_actual_navigation_consumer_uses_combined_tools_and_knowledge():
    payload=navigation_payload(root=ROOT)
    cards={r['id']:r for r in payload['tools']}
    assert cards['tikhub']['stage']=='PORTABLE_OFFLINE_ENTRY'
    assert 'orbit_tools.py' in cards['tikhub']['method']
    assert cards['duoplus']['status']=='离线入口可用 · 账号未核验'
    assert cards['publication-knowledge']['stage']=='PORTABLE_REVIEWED_SNAPSHOT_ENTRY'
    assert 'expected-version' in cards['publication-knowledge']['method']
    assert cards['publish-approved-product']['stage']=='WORKFLOW_RUNTIME_REQUIRED'
    assert cards['apply-product-discounts']['stage']=='WORKFLOW_RUNTIME_REQUIRED'
    assert cards['minimax-h3']['status']=='需要宿主插件与配置'

def test_navigation_never_promotes_tools_when_runtime_validation_fails(monkeypatch):
    from shared_platform import capability_runtime as rt
    def fail(*args):raise rt.ToolContextError('synthetic manifest drift')
    monkeypatch.setattr(rt,'validate_runtime',fail)
    payload=navigation_payload(root=ROOT)
    assert all(row['status']=='工具包未核验' for row in payload['tools'])
    assert payload['tool_catalog']['state']=='UNVERIFIED'
