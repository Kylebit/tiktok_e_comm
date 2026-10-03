"""Guide coverage and reference integrity; runtime conditions stay server-owned."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import pytest

from shared_platform.orbit_registry import navigation_payload

ROOT = Path(__file__).resolve().parents[1]


def test_every_visible_tool_has_a_complete_guide_with_real_source_paths():
    guides = json.loads((ROOT / 'web/static/knowledge_guides.json').read_text(encoding='utf-8'))
    payload = navigation_payload(root=ROOT)
    assert set(guides['entries']) == {x['id'] for x in payload['tools']}
    for key, guide in guides['entries'].items():
        assert 10 <= len(guide['summary']) <= 65, key
        assert len(guide['example']) >= 60, key
        for field in ('scenarios', 'inputs', 'outputs', 'requirements', 'limits', 'references'):
            assert isinstance(guide[field], list) and guide[field], (key, field)
            assert all(isinstance(x, str) and x.strip() for x in guide[field])
        # Editorial help cannot grant readiness, change grouping or replace executable methods.
        assert not set(guide).intersection({'stage', 'status', 'method', 'href', 'account_verified', 'group'})
        for relative in guide['references']:
            source = ROOT / relative
            assert source.resolve().is_relative_to(ROOT), (key, relative)
            assert source.is_file(), (key, relative)
        assert guide['references'], key


def test_visible_tool_briefs_follow_verified_manifest_contract():
    manifest = json.loads((ROOT / 'config/capability_catalog.json').read_text(encoding='utf-8'))
    source = {row['id']: row for row in manifest['skills'] + manifest['tools']}
    payload = navigation_payload(root=ROOT)
    assert payload['tool_catalog']['state'] == 'VERIFIED_LOCAL_CODE_ONLY'
    assert not ({'feishu', 'notifications', 'miaoshou-legacy', 'toapi'} & {card['id'] for card in payload['tools']})
    for card in payload['tools']:
        row = source[card['id']]
        brief = card['brief']
        for field in ('purpose', 'inputs', 'outputs', 'side_effects', 'recovery'):
            assert brief[field] == row.get(field, ''), (card['id'], field)
        assert brief['source_path'] == row.get('source_path', card.get('asset') or '')


def test_publication_guides_have_only_one_human_review_gate():
    guides = json.loads((ROOT / 'web/static/knowledge_guides.json').read_text(encoding='utf-8'))['entries']
    first = json.dumps(guides['prepare-product-publication'], ensure_ascii=False)
    second = json.dumps(guides['prepare-product-images'], ensure_ascii=False)
    final = json.dumps(guides['publish-approved-product'], ensure_ascii=False)
    assert '第一轮审核包' not in first
    assert '第一轮已批准' not in second
    assert '先经过第一轮人工审核' not in second
    assert 'FINAL_MARKETPLACE_PUBLISH' in final


def test_business_skills_explain_the_workflow_without_granting_execution():
    guides = json.loads((ROOT / 'web/static/knowledge_guides.json').read_text(encoding='utf-8'))['entries']
    business_ids = {
        'prepare-product-publication', 'prepare-product-images', 'publish-approved-product',
        'delist-products-by-sku', 'apply-product-discounts',
        'manage-seaya-replenishment', 'manage-profit-settlement',
    }
    for name in business_ids:
        workflow = guides[name]['workflow']
        assert 3 <= len(workflow) <= 4, name
        assert all(isinstance(step, str) and len(step) >= 12 for step in workflow), name
        assert not set(guides[name]).intersection({'execution_authority', 'approved', 'ready'}), name
    assert 'COMMON' in ' '.join(guides['publish-approved-product']['workflow'])
    assert '唯一终审' in ' '.join(guides['publish-approved-product']['workflow'])
    assert '有效订单' in ' '.join(guides['manage-seaya-replenishment']['workflow'])
    assert '结算' in ' '.join(guides['manage-profit-settlement']['workflow'])


def test_unresolved_skill_source_drift_is_disclosed_as_dated_non_authoritative_help():
    guides = json.loads((ROOT / 'web/static/knowledge_guides.json').read_text(encoding='utf-8'))['entries']
    for name in ('manage-seaya-replenishment',):
        notice = guides[name]['source_notice']
        assert notice['observed_at'] == '2026-09-28'
        assert '待核' in notice['status']
        assert notice['message']
        assert not set(notice).intersection({'stage', 'method', 'href', 'account_verified'})
        assert 'C:\\' not in notice['message'] and 'D:\\' not in notice['message']
        assert {row['label'] for row in notice['checksums']} == {'本工程英文 Skill 原始字节', '本机安装版原始字节', '配置清单记录的本工程 LF 规范化文本'}
        assert all(len(row['sha256']) == 64 for row in notice['checksums'])
        assert len({row['sha256'] for row in notice['checksums']}) == 3
        assert notice['location_note'].startswith('本机安装位置：~/.codex/skills/')
        assert 'LF 文本规范化' in notice['message']
        # The page displays a dated source comparison. Its project digests must
        # follow the actual Skill bytes after any repo-side Skill correction;
        # the personal installed copy remains an independent, unresolved source.
        skill_bytes = (ROOT / 'domains/supply_chain_operations/skills/manage-seaya-replenishment/SKILL.md').read_bytes()
        digests = {row['label']: row['sha256'] for row in notice['checksums']}
        assert digests['本工程英文 Skill 原始字节'] == hashlib.sha256(skill_bytes).hexdigest()
        normalized = hashlib.sha256(skill_bytes.replace(b'\r\n', b'\n')).hexdigest()
        sync = json.loads((ROOT / 'domains/supply_chain_operations/skills/manage-seaya-replenishment/references/dashboard-sync.json').read_text(encoding='utf-8'))
        assert sync['digest_policy'] == 'sha256-utf8-crlf-to-lf/v1'
        assert digests['配置清单记录的本工程 LF 规范化文本'] == normalized
        assert digests['配置清单记录的本工程 LF 规范化文本'] == sync['sha256']['skills/manage-seaya-replenishment/SKILL.md']
        assert digests['本机安装版原始字节'] != digests['本工程英文 Skill 原始字节']
    assert '库存' in guides['manage-seaya-replenishment']['source_notice']['message']
    assert '旧快照' in guides['manage-seaya-replenishment']['source_notice']['message']
    assert 'source_notice' not in guides['manage-profit-settlement']


def test_guides_are_served_as_static_documentation_not_execution(entry_http):
    get, _, _ = entry_http
    status, _, body = get('/static/knowledge_guides.json')
    assert status == 200
    assert json.loads(body)['schema'] == 'orbit-knowledge-guides/v1'
    assert get('/static/knowledge_guides.css')[0] == 200


def test_knowledge_skill_briefs_in_real_browser(entry_http, tmp_path):
    _, server, _ = entry_http
    node_root = Path('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node')
    node = node_root / 'bin/node.exe'
    if not node.is_file():
        pytest.skip('bundled Node unavailable')
    env = {**os.environ, 'NODE_PATH': str(node_root / 'node_modules')}
    script = ROOT / 'tests/browser/knowledge_cards.js'
    result = subprocess.run(
        [str(node), str(script), f'http://127.0.0.1:{server.server_port}', str(tmp_path)],
        env=env, capture_output=True, text=True, encoding='utf-8', timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((tmp_path / 'knowledge-browser-result.json').read_text(encoding='utf-8'))['passed']


@pytest.mark.parametrize('asset', ['web/static/knowledge_guides.json', 'web/static/knowledge_guides.css'])
def test_guide_asset_changes_invalidate_runtime_identity(asset, entry_http, tmp_path, monkeypatch):
    from shared_platform import runtime_identity as identity
    # This fixture checks asset identity, independent of optional host packages.
    original_find_spec = identity.importlib.util.find_spec
    monkeypatch.setattr(identity.importlib.util, 'find_spec',
                        lambda name: object() if name in {'requests', 'PIL'} else original_find_spec(name))
    assert asset in identity.ASSET_FILES
    tree = tmp_path / 'synthetic-source'
    tree.mkdir()
    for name in identity.ENTRY_FILES + identity.ASSET_FILES:
        target = tree / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('synthetic asset', encoding='utf-8')
    (tree / 'shared_platform/entry_catalog.json').write_text('{"supply_images":[]}', encoding='utf-8')
    monkeypatch.setattr(identity, '_commit', lambda *_: 'a' * 40)
    startup = identity.capture_runtime_identity('orbit-hive-local-console', root=tree)
    assert identity.health_payload('orbit-hive-local-console', root=tree, startup=startup)['state'] == 'READY'
    (tree / asset).write_text('changed synthetic asset content', encoding='utf-8')
    changed = identity.health_payload('orbit-hive-local-console', root=tree, startup=startup)
    assert changed['state'] == 'ASSET_MISMATCH'


from test_unified_entry import entry_http
