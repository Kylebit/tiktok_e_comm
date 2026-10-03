"""Real bounded Python CLI checks; all business access is denied in the child."""
from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
ENTRIES = (
    'mx_confirm_cli', 'migrate_mx_one', 'migrate_mx_group', 'migrate_mx_batch',
    'migrate_mx_batch_queue', 'migrate_mx_batch_08xx', 'migrate_mx_batch_0809_0829',
    'fix_mx_price_and_publish', 'update_mx_pop_listings',
    'publish_mx_sku_0002', 'publish_mx_sku_0003',
)
MESSAGE = 'LEGACY_MX_ENTRY_RETIRED: 该历史工具已停用，请使用 Orbit 商品上架页面或当前上架 Skill\n'
CHILD = r'''
import os, runpy, sys
source = sys.argv[1]
arguments = sys.argv[2:]
attempts = []
def audit(event, args):
    denied = event in {
        'sqlite3.connect', 'sqlite3.connect/handle', 'subprocess.Popen',
        'os.system', 'os.exec', 'os.posix_spawn', 'os.spawn', 'os.fork',
        'os.remove', 'os.rename', 'os.mkdir', 'os.rmdir', 'os.link',
        'os.symlink', 'os.truncate', 'os.chmod', 'os.chown', 'os.utime',
        'shutil.copyfile', 'shutil.copymode', 'shutil.copystat',
    } or event.startswith('socket.')
    if event == 'open':
        mode = args[1]
        flags = args[2]
        denied = denied or (isinstance(mode, str) and any(c in mode for c in 'wax+'))
        denied = denied or bool(isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
    if event == 'import':
        name = str(args[0]).split('.')[0]
        denied = denied or name in {'core', 'modules', 'domains', 'shared_platform', 'scripts', 'settings'}
    if denied:
        attempts.append(event)
        raise RuntimeError('RETIRED_CLI_BUSINESS_SIDE_EFFECT_BLOCKED')
assert sys.flags.isolated and sys.dont_write_bytecode and sys.flags.no_site
sys.addaudithook(audit)
sys.argv = [source, *arguments]
try:
    runpy.run_path(source, run_name='__main__')
except SystemExit as stopped:
    assert attempts == [], attempts
    raise
assert attempts == [], attempts
raise AssertionError('Retired entry did not exit explicitly')
'''


def _inventory(root):
    values = {}
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        assert not path.is_symlink()
        assert not getattr(info, 'st_file_attributes', 0) & 0x400
        values[str(path.relative_to(root))] = {
            'mode': stat.S_IMODE(info.st_mode), 'mtime_ns': info.st_mtime_ns,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        }
    return values


@pytest.mark.parametrize('entry', ENTRIES, ids=ENTRIES)
def test_retired_legacy_mx_cli_has_no_business_side_effects(tmp_path, entry):
    source = ROOT / 'scripts' / (entry + '.py')
    raw = source.read_bytes()
    before_source_stat = source.stat()
    tree = ast.parse(raw.decode('utf-8'))
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert all((isinstance(node, ast.Import) and [alias.name for alias in node.names] == ['sys'])
               or (isinstance(node, ast.ImportFrom) and node.module == '__future__'
                   and [alias.name for alias in node.names] == ['annotations']) for node in imports)
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            names.append(ast.unparse(node.func))
    assert set(names) == {'sys.stderr.write', 'SystemExit', 'main'}
    assert len(names) == 3
    assert not any(isinstance(node, ast.Global) for node in ast.walk(tree))

    owned = tmp_path / 'owned-empty-runtime'
    owned.mkdir()
    for relative, value in {
        'reports/old-card.json': b'{"reviewed":true,"sku":"0002"}\n',
        'reports/profit.feishu.json': b'{"historical_profit_cny":"12.34"}\n',
        'config/settings.json': b'{"owned_synthetic":true}\n',
        'data/shop.db': b'OWNED_SENTINEL_NOT_A_DATABASE\n',
        'reports/current.summary.json': b'{"profit_cny":"56.78"}\n',
    }.items():
        path = owned / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    bootstrap = owned / 'closed_retired_cli_probe.py'
    bootstrap.write_text(CHILD, encoding='utf-8')
    expected = _inventory(owned)
    environment = {name: os.environ[name] for name in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP') if name in os.environ}
    unsafe = ['--skip-confirm', '--user-approved', '--publish', '--save-only',
              '--keys', '0002', '--token', 'owned-sentinel-not-a-credential']
    for arguments in ([], unsafe):
        result = subprocess.run(
            [sys.executable, '-I', '-B', '-S', '-X', 'utf8', str(bootstrap), str(source), *arguments],
            cwd=owned, env=environment, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding='utf-8', timeout=5,
        )
        assert result.returncode == 2, result.stdout + result.stderr
        assert result.stdout == ''
        assert result.stderr == MESSAGE
        assert 'owned-sentinel-not-a-credential' not in result.stdout + result.stderr
        assert _inventory(owned) == expected
        assert source.read_bytes() == raw
        assert source.stat().st_mtime_ns == before_source_stat.st_mtime_ns
        assert stat.S_IMODE(source.stat().st_mode) == stat.S_IMODE(before_source_stat.st_mode)
