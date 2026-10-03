"""Local runtime identity without configuration initialization or database access.

The operator's non-secret runtime profile is an independent expectation, not
an instruction to redirect stores. Unknown configuration stays unverified.
Only the listed entry files are hashed; unchanged files use a bounded cache.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from core.static_files import resolve_static_path

CONTRACT = 'orbit-runtime/v1'
ROOT = Path(__file__).resolve().parents[1]
ENTRY_FILES = (
    'shared_platform/original_profit_reports.py', 'config/original_profit_reports.json',
    'shared_platform/internal_catalog_sku.py',
    'shared_platform/catalog_sku_costs.py','shared_platform/catalog_images.py','modules/catalog/sku_directory.py',
    'scripts/catalog_review_preview.py',
    'main.py', 'shared_platform/runtime_identity.py', 'shared_platform/skill_identity.py',
    'shared_platform/orbit_registry.py', 'shared_platform/entry_catalog.json',
    'scripts/product_publication_runtime.py', 'scripts/stable_runtime_bootstrap.py',
    'core/config.py', 'core/db.py',
    'core/static_files.py', 'shared_platform/report_store.py',
    'shared_platform/workbench_store.py', 'modules/ozon/config.py',
    'modules/products/server.py', 'modules/sourcing/new_product_server.py',
    'modules/ozon/rus_server.py',
    'shared_platform/round1_workspace.py',
    'shared_platform/catalog_cost_projection.py', 'shared_platform/catalog_publication_sync.py',
    'shared_platform/catalog_shopee_readback.py',
    'shared_platform/catalog_ozon.py', 'shared_platform/catalog_ozon_readback.py',
    'modules/ozon/approved_publication_v4.py', 'modules/ozon/client.py',
    'shared_platform/product_publication_live_dependencies.py',
    'domains/data_operations/profit_settlement/local_catalog.py',
    'shared_platform/product_publication_runner.py', 'shared_platform/product_publication_executors.py',
    'modules/shopee/skill_regions.py', 'modules/catalog/listings.py', 'modules/products/costs.py',
    'domains/product_operations/catalog_database_audit.py',
)
ASSET_FILES = (
    'web/task_workspace.html', 'web/static/task_workspace.css', 'web/static/task_workspace.js',
    'web/static/catalog_directory.js','web/static/catalog_directory.css',
    'web/knowledge.html','web/static/knowledge_directory.js','web/static/operations_shell.css',
    'web/static/knowledge_guides.json',
    'web/static/knowledge_guides.css',
    'web/index.html', 'web/static/orbit_product.js', 'web/static/orbit_product.css',
    'web/static/operations.css', 'web/static/operations_shell.js',
    'web/static/product_workspace.js', 'web/static/product_workspace.css',
    'web/static/product_workspace_round1_category.js',
    'web/static/product_workspace_round1_category.css',
    'web/product_workspace.html', 'web/ai_image_studio.html',
    'web/profit_center.html', 'web/profit_archive.html',
    'web/static/profit_archive.js', 'web/static/profit_archive.css',
    'web/profit_legacy.html', 'web/profit_legacy_layout.html',
    'web/static/profit_legacy.css',
    'web/new_product.html',
    'web/catalog.html',
    'domains/supply_chain_operations/dashboard/index.html',
    'domains/supply_chain_operations/dashboard/app.js',
    'domains/supply_chain_operations/dashboard/load-errors.js',
    'domains/supply_chain_operations/dashboard/data.js',
    'domains/supply_chain_operations/dashboard/styles.css',
    'domains/supply_chain_operations/dashboard/inbound-batches.html',
    'domains/supply_chain_operations/dashboard/inbound-batches.js',
    'domains/supply_chain_operations/dashboard/inbound-plan.js',
    'domains/supply_chain_operations/dashboard/inbound-timeline.js',
    'domains/supply_chain_operations/dashboard/transport-history.js',
    'domains/supply_chain_operations/skills/manage-seaya-replenishment/references/dashboard-sync.json',
)
LEGACY_STORE_KEYS = {'catalog', 'reports_release', 'workbench', 'ozon'}
PINNED_STORE_KEYS = {'catalog', 'report', 'release', 'workbench', 'ozon'}


def _path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve())


@lru_cache(maxsize=512)
def _file_hash(path: str, size: int, modified: int) -> str:
    raw = Path(path).read_bytes()
    if Path(path).suffix in {'.py', '.json', '.html', '.js', '.css'}:
        raw = raw.replace(b'\r\n', b'\n')
    return hashlib.sha256(raw).hexdigest()


def file_set(root: Path, names: tuple[str, ...]) -> dict:
    hashes, missing = {}, []
    for name in names:
        path = root / name
        try:
            # An existing symlink target is not necessarily a servable asset.
            resolved = path.resolve(strict=True)
            resolved.relative_to(root.resolve(strict=True))
            public_prefix = next((prefix for prefix in ('web/static/', 'domains/supply_chain_operations/dashboard/', 'web/') if name.startswith(prefix)), None)
            if public_prefix and resolve_static_path(root / public_prefix, name[len(public_prefix):]) is None:
                raise ValueError('asset is outside its HTTP public root')
            stat = resolved.stat()
            hashes[name] = _file_hash(str(resolved), stat.st_size, stat.st_mtime_ns)
        except (OSError, ValueError, RuntimeError):
            missing.append(name)
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return {'digest': digest, 'missing': missing, 'file_count': len(hashes)}


@lru_cache(maxsize=16)
def _commit(root: str, head: str) -> str | None:
    try:
        result = subprocess.run(['git', '-c', 'safe.directory=' + Path(root).as_posix(),
                                 '-C', root, 'rev-parse', 'HEAD'], capture_output=True,
                                text=True, timeout=3, check=True,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def code_identity(root: Path) -> dict:
    # HEAD/ref metadata invalidates the commit cache after a local commit.
    try:
        git = root / '.git'
        if git.is_file():
            git = Path(git.read_text(encoding='utf-8').strip().removeprefix('gitdir: '))
        head = (git / 'HEAD').read_text(encoding='utf-8').strip()
        if head.startswith('ref: '):
            common = git / 'commondir'
            base = (git / common.read_text().strip()).resolve() if common.is_file() else git
            ref = base / head[5:]
            head += ref.read_text() if ref.is_file() else str((base / 'packed-refs').stat().st_mtime_ns)
    except OSError:
        head = 'unavailable'
    return {'code_root': _path(root), 'commit': _commit(str(root), head),
            'build': file_set(root, ENTRY_FILES)}


def read_profile(root: Path, profile_path: str | Path | None = None) -> dict | None:
    path = Path(profile_path or os.environ.get('ORBIT_RUNTIME_PROFILE') or root / 'config/runtime-profile.json')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data.get('profile_id'), str) or not data['profile_id'].strip():
            return None
        store_keys = set(data.get('stores', {}))
        if (
            not isinstance(data.get('settings_path'), str)
            or store_keys not in {frozenset(LEGACY_STORE_KEYS), frozenset(PINNED_STORE_KEYS)}
        ):
            return None
        # Whitelist only non-secret identity fields; never echo extra profile data.
        return {'profile_id': data['profile_id'], 'settings_path': _path(data['settings_path']),
                'stores': {key: _path(value) for key, value in data['stores'].items()}}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def expected_identity(root: str | Path, service: str, *, profile_path=None) -> dict:
    repository = Path(root).resolve()
    profile = read_profile(repository, profile_path)
    try:
        catalog_path = resolve_static_path(repository, 'shared_platform/entry_catalog.json')
        if catalog_path is None:
            raise ValueError('catalog is outside the selected candidate')
        catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
        images = tuple(catalog['supply_images'])
        if any(not name.startswith('domains/supply_chain_operations/dashboard/assets/') or '..' in Path(name).parts for name in images):
            images = ('invalid-image-manifest',)
    except (OSError, ValueError, KeyError, TypeError):
        images = ('missing-image-manifest',)
    return {'contract_version': CONTRACT, 'service': service, **code_identity(repository),
            'profile': profile, 'assets': file_set(repository, ASSET_FILES + images)}


def _absolute_override(name: str) -> str | None:
    raw = os.environ.get(name)
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw).expanduser()
    return _path(path) if path.is_absolute() else None


def actual_stores(
    root: Path,
    settings_path: Path,
    cache: dict | None,
    *,
    expected_stores: Mapping[str, object] | None = None,
) -> dict | None:
    if not isinstance(cache, dict):
        return None
    from shared_platform.report_store import DEFAULT_REPORT_STORE_PATH
    from shared_platform.release_store import DEFAULT_RELEASE_STORE_PATH
    from shared_platform.workbench_store import default_workbench_store
    base = settings_path.parent.parent if settings_path.parent.name == 'config' else settings_path.parent
    ozon = cache.get('ozon') or {}
    feishu = cache.get('feishu') or {}
    raw = ozon.get('data_dir') or feishu.get('ozon_data_dir')
    data = Path(raw).expanduser() if isinstance(raw, str) and raw.strip() else None
    if data is None or not data.is_dir():
        embedded = root / 'modules/ozon/legacy_webapp/data'
        sibling = root.parent / 'ozon/webapp/data'
        data = embedded if embedded.is_dir() else sibling if sibling.is_dir() else None
    database = Path(cache.get('database') or 'data/shop.db')
    catalog = _absolute_override('ORBIT_CATALOG_DATABASE') or _path(
        database if database.is_absolute() else base / database
    )
    report = _path(DEFAULT_REPORT_STORE_PATH)
    release = _path(DEFAULT_RELEASE_STORE_PATH)
    workbench = _path(default_workbench_store().path)
    ozon = _absolute_override('ORBIT_OZON_DATA_ROOT') or (_path(data) if data else None)
    if set(expected_stores or {}) == PINNED_STORE_KEYS:
        return {
            'catalog': catalog,
            'report': report,
            'release': release,
            'workbench': workbench,
            'ozon': ozon,
        }
    return {
        'catalog': catalog,
        'reports_release': report if report == release else None,
        'workbench': workbench,
        'ozon': ozon,
    }


def compare_identity(actual: dict, expected: dict) -> str:
    if actual.get('service') != expected.get('service'):
        return 'WRONG_SERVICE'
    if actual.get('contract_version') != CONTRACT:
        return 'UNKNOWN'
    if actual.get('code_root') and actual['code_root'] != expected.get('code_root'):
        return 'SOURCE_MISMATCH'
    for key in ('code_root', 'commit', 'build', 'assets'):
        if not actual.get(key) or not expected.get(key):
            return 'UNKNOWN'
    for key in ('build', 'assets'):
        if not isinstance(actual[key], dict) or not actual[key].get('digest') or 'missing' not in actual[key]:
            return 'UNKNOWN'
    if (actual['code_root'], actual['commit'], actual['build']['digest']) != (expected['code_root'], expected['commit'], expected['build']['digest']):
        return 'SOURCE_MISMATCH'
    if actual['build'].get('missing') or expected['build'].get('missing'):
        return 'SOURCE_MISMATCH'
    if actual['assets'].get('missing') or expected['assets'].get('missing'):
        return 'ASSET_MISSING'
    if actual['assets'].get('digest') != expected['assets'].get('digest'):
        return 'ASSET_MISMATCH'
    profile = expected.get('profile')
    if not profile or not isinstance(actual.get('profile'), dict):
        return 'UNKNOWN'
    if not actual.get('configuration_source_known'):
        return 'UNKNOWN'
    if actual['profile'].get('profile_id') != profile['profile_id'] or actual['profile'].get('settings_path') != profile['settings_path']:
        return 'CONFIG_MISMATCH'
    if not actual.get('configuration_source_matches'):
        return 'CONFIG_MISMATCH'
    if not actual.get('configuration_initialized'):
        return 'UNKNOWN'
    if actual.get('stores') != profile['stores']:
        return 'DATA_PROFILE_MISMATCH'
    if not isinstance(actual.get('missing_dependencies'), list):
        return 'UNKNOWN'
    if actual['missing_dependencies']:
        return 'DEPENDENCY_UNAVAILABLE'
    return 'READY'


def capture_runtime_identity(service: str, *, root: str | Path = ROOT, web_root=None) -> dict:
    """Freeze identity at service module load, before any first health request."""
    repository = Path(root).resolve()
    snapshot = expected_identity(repository, service)
    if web_root is not None and Path(web_root).resolve() != repository / 'web':
        snapshot['assets'] = {**snapshot['assets'], 'digest': None, 'missing': ['web root mismatch']}
    return deepcopy(snapshot)


def health_payload(service: str, *, root: str | Path = ROOT, web_root=None, startup=None) -> dict:
    from core import config
    repository = Path(root).resolve()
    expected = expected_identity(repository, service)
    selected = config.settings_path().resolve()  # Metadata only; never relabel cache.
    loaded = config._cache_source
    source_known = loaded is not None and config._cache_source_stat is not None
    source_matches = False
    if source_known:
        try:
            stat = Path(loaded).stat()
            source_matches = selected == Path(loaded).resolve() and config._cache_source_stat == (stat.st_size, stat.st_mtime_ns)
        except OSError:
            pass
    actual = deepcopy(startup) if isinstance(startup, dict) else {'service': service}
    actual.update({'profile': {'profile_id': (actual.get('profile') or {}).get('profile_id'),
                              'settings_path': _path(loaded) if loaded else None},
              'stores': actual_stores(
                  repository,
                  Path(loaded),
                  config._cache,
                  expected_stores=(expected.get('profile') or {}).get('stores'),
              ) if source_known else None,
              'configuration_initialized': isinstance(config._cache, dict),
              'configuration_source_known': source_known,
              'configuration_source_matches': source_matches,
              'missing_dependencies': [name for name in ('requests', 'PIL') if importlib.util.find_spec(name) is None]})
    if web_root is not None and Path(web_root).resolve() != repository / 'web':
        actual['assets'] = {**actual['assets'], 'digest': None, 'missing': ['web root mismatch']}
    state = compare_identity(actual, expected)
    return {'ok': True, **actual, 'state': state,
            'business_execution_verified': False, 'identity_only': True}
