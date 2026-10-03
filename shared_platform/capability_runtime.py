"""Explicit, non-secret portable tool context. Importing this module performs no I/O."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
from stat import S_ISLNK
import sys
from urllib.parse import urlparse


class ToolContextError(ValueError):
    pass


class ToolPlanError(ToolContextError):
    """Structured recovery facts shared by existing tool entry points."""
    def __init__(self, code: str, **facts):
        self.diagnostic = {'ok': False, 'code': code, **facts}
        super().__init__(json.dumps(self.diagnostic, ensure_ascii=False))


def preflight_write_paths(root: Path, paths, *, operation: str) -> dict:
    """Read-only conservative tool support budget, not universal Windows limits."""
    root = checked_path(root, '.')
    units = lambda value: len(str(value).encode('utf-16-le', errors='surrogatepass')) // 2
    planned = {('directory', root)}
    for kind, value in paths:
        path = checked_path(root, value)
        planned.add((kind, path))
        parent = path if kind == 'directory' else path.parent
        while parent.is_relative_to(root):
            planned.add(('directory', parent))
            if parent == root: break
            parent = parent.parent
    blocked = []
    max_root = None
    for kind, path in sorted(planned, key=lambda item: (str(item[1]), item[0])):
        limit = 247 if kind == 'directory' else 259
        allowance = limit - (units(path) - units(root))
        max_root = allowance if max_root is None else min(max_root, allowance)
        long_components = [part for part in path.parts[1:] if units(part) > 255]
        if os.name == 'nt' and (units(path) > limit or long_components):
            blocked.append({'path': str(path), 'kind': kind, 'path_is_template': kind == 'temporary_file_template',
                            'utf16_units': units(path), 'supported_max_utf16_units': limit,
                            'oversize_components': long_components})
    summary = {'operation': operation, 'destination_root': str(root), 'planned_path_count': len(planned),
               'policy': 'conservative ordinary-local-Windows tool budget; measured on Windows Python 3.12.8 with LongPathsEnabled=0; not universal OS capability',
               'windows_policy_applied': os.name == 'nt', 'writes_performed': []}
    if blocked:
        raise ToolPlanError('LONG_PATH_UNSUPPORTED', **summary, blocked_paths=blocked,
                            supported_component_utf16_units=255,
                            suggested_max_destination_root_utf16_units=max_root,
                            recovery='Choose a shorter explicit destination root and rerun the same check/preview; existing installation and history are retained. No automatic relocation or hash truncation.')
    return {'ok': True, 'status': 'WRITE_PATHS_PREFLIGHTED', **summary}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def file_content(path: Path) -> bytes:
    """Match the existing Skill contract: UTF-8 text hashes use LF, binary hashes are exact."""
    raw = path.read_bytes()
    if path.suffix.lower() in {'.md', '.py', '.json', '.yaml', '.yml'}:
        return raw.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')
    return raw


def tree_files(root: Path) -> list[Path]:
    root = checked_path(root, '.')
    if not root.exists(): return []
    files = []; pending = [root]
    while pending:
        directory = pending.pop()
        for item in sorted(directory.iterdir()):
            checked_path(root, item)
            if item.name in {'__pycache__', '.pytest_cache'} or item.suffix in {'.pyc', '.pyo'}: continue
            if item.is_dir(): pending.append(item)
            elif item.is_file(): files.append(item)
    return sorted(files)


def _resolved_comparison_path(path: Path) -> Path:
    """Normalize only OS-resolved local drive spelling, never caller input."""
    resolved = path.resolve()
    if os.name == 'nt':
        text = str(resolved)
        if text.startswith('\\\\?\\') and re.fullmatch('[A-Za-z]:', text[4:6]) and text[6:7] == '\\':
            text = text[4:]
        resolved = Path(text)
        if not re.fullmatch('[A-Za-z]:', resolved.drive) or not resolved.is_absolute():
            raise ToolContextError('resolved path is not an absolute local drive path')
    return resolved


def checked_path(root: Path, relative: str | Path) -> Path:
    """Reject redirects before reading their targets, including Windows junctions."""
    root = Path(os.path.abspath(root))
    value = Path(relative)
    candidate = Path(os.path.abspath(value if value.is_absolute() else root / value))
    if str(candidate).startswith(('\\\\', '//')) or not candidate.is_relative_to(root):
        raise ToolContextError('path must remain inside its explicit root')
    relative_parts = candidate.relative_to(root).parts
    for item in (*reversed(root.parents), root, *[root.joinpath(*relative_parts[:n])
                         for n in range(1, len(relative_parts) + 1)]):
        try:
            stat = item.lstat()
        except FileNotFoundError:
            continue
        if S_ISLNK(stat.st_mode) or getattr(stat, 'st_file_attributes', 0) & 0x400:
            raise ToolContextError('path contains a symlink or reparse point: ' + str(item))
    if not _resolved_comparison_path(candidate).is_relative_to(_resolved_comparison_path(root)):
        raise ToolContextError('resolved path leaves its explicit root')
    return candidate


def read_object(path: Path) -> dict:
    result = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(result, dict): raise ToolContextError('JSON object required: ' + path.name)
    return result


def validate_runtime(root: Path) -> dict:
    root = checked_path(root, '.')
    manifest = read_object(checked_path(root, 'config/tool_runtime_manifest.json'))
    if manifest.get('schema') != 'orbit-tool-runtime/v1' or manifest.get('digest') != digest({k:v for k,v in manifest.items() if k != 'digest'}):
        raise ToolContextError('runtime manifest is invalid')
    minimum = manifest.get('runtime_requirements', {}).get('minimum_python')
    if minimum and sys.version_info[:2] < tuple(int(part) for part in minimum.split('.')):
        raise ToolContextError('runtime requires Python '+minimum+' or later')
    missing = []; changed = []
    for relative, expected in manifest['files'].items():
        path = checked_path(root, relative)
        if not path.is_file(): missing.append(relative)
        elif hashlib.sha256(file_content(path)).hexdigest() != expected: changed.append(relative)
    if missing or changed: raise ToolContextError(json.dumps({'missing_runtime_files': missing, 'changed_runtime_files': changed}))
    return manifest


def load_profile(project_root: Path, path: Path) -> dict:
    project_root = checked_path(project_root, '.')
    profile_path = checked_path(project_root, path)
    profile = read_object(profile_path)
    if (set(profile) != {'schema', 'tenant_id', 'artifact_root', 'providers'}
            or profile['schema'] != 'orbit-tool-profile/v1'
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', str(profile['tenant_id']))):
        raise ToolContextError('profile accepts schema, tenant_id, artifact_root, providers only; no secrets or implicit policy')
    if Path(profile['artifact_root']).is_absolute(): raise ToolContextError('artifact_root must be relative to the explicit project root')
    artifact_root = checked_path(project_root, profile['artifact_root'])
    allowed = {'lingshi': {'https://api.lk888.ai'}, 'duoplus': {'https://openapi.duoplus.cn', 'https://openapi.duoplus.net'},
               'tikhub': {'https://api.tikhub.io'}}
    if not isinstance(profile['providers'], dict) or set(profile['providers']) - set(allowed):
        raise ToolContextError('profile provider is unsupported; ToAPI is retired')
    for provider, row in profile['providers'].items():
        if (set(row) != {'origin', 'credential_env'} or row['origin'] not in allowed[provider]
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', str(row['credential_env']))
                or (provider == 'tikhub' and row['credential_env'] != 'TikHub')):
            raise ToolContextError('provider requires an exact official origin and an explicit environment key name')
    return {'profile': profile, 'profile_digest': digest(profile), 'project_root': project_root,
            'profile_path': profile_path, 'artifact_root': artifact_root, 'tenant_id': profile['tenant_id']}


def catalog(root: Path) -> dict:
    return read_object(checked_path(root, 'config/capability_catalog.json'))


def doctor(root: Path, context: dict, capability: str | None = None) -> dict:
    manifest = validate_runtime(root); registry = catalog(root)
    entries = registry['skills'] + registry['tools']
    if capability:
        entries = [r for r in entries if r['id'] == capability]
        if not entries: raise ToolContextError('unknown capability ID')
    results = []
    for entry in entries:
        missing = [r for r in entry.get('required_files', []) if not checked_path(root, r).is_file()]
        changed = []
        for relative, expected in entry.get('file_digests', {}).items():
            path = checked_path(root, relative)
            if path.is_file() and hashlib.sha256(file_content(path)).hexdigest() != expected: changed.append(relative)
        absent_modules = [m for m in entry.get('python_dependencies', []) if importlib.util.find_spec(m) is None]
        status = 'DEPENDENCIES_MISSING' if missing or changed or absent_modules else entry['stage']
        results.append({'id': entry['id'], 'status': status, 'missing_files': missing,
                        'changed_files': changed, 'missing_python_modules': absent_modules,
                        'next_action': entry['recovery']})
    return {'schema': 'orbit-tool-doctor/v1', 'ok': not any(r['missing_files'] or r['changed_files'] or r['missing_python_modules'] for r in results),
            'runtime_digest': manifest['digest'], 'tenant_id': context['tenant_id'], 'profile_digest': context['profile_digest'],
            'credentials': {p: {'environment_key': row['credential_env'], 'key_present': row['credential_env'] in os.environ,
                                'value_read': False} for p, row in context['profile']['providers'].items()},
            'capabilities': results, 'live_health_verified': False, 'network_call_performed': False}


class NoNetworkSession:
    trust_env = False
    def request(self, *args, **kwargs): raise ToolContextError('offline preview attempted network access')


def preview(root: Path, context: dict, provider: str, operation: str, payload: dict) -> dict:
    validate_runtime(root)
    selected = context['profile']['providers'].get(provider)
    if selected is None: raise ToolContextError('provider missing from the explicit profile')
    if provider == 'lingshi':
        from modules.sourcing.lingshi_client import LingshiClient
        # Non-empty synthetic value prevents the legacy constructor's env fallback.
        client = LingshiClient(api_key='offline-preview-no-credential', base_url=selected['origin'], session=NoNetworkSession())
        methods = {'media': client.preview_media_generation, 'image': client.preview_image_generation,
                   'chat': client.preview_chat, 'agent-chat': client.preview_agent_chat}
        if operation not in methods: raise ToolContextError('unsupported Lingshi preview operation')
        result = methods[operation](**payload)
        result['paid_execution_entry'] = 'skills/prepare-product-images/scripts/prepare_product_images.py (complete OrbitHive runtime required)'
    elif provider == 'tikhub':
        from modules.tools.tikhub import preview as tikhub_preview
        if operation != 'query': raise ToolContextError('TikHub preview supports query')
        result = tikhub_preview(**payload)
    elif provider == 'duoplus':
        from modules.tools.duoplus import DuoPlusClient, install_scope
        client = DuoPlusClient(api_key='offline-preview-no-credential', base_url=selected['origin'])
        if operation == 'install-app':
            scope = install_scope(tenant_id=context['tenant_id'], profile_digest=context['profile_digest'], origin=selected['origin'], **payload)
            result = client.install_app(scope['image_ids'], scope['app_id'], scope['app_version_id'])
            result['authorization_scope'] = scope
            result['readback_limit'] = 'installedList confirms package presence, not version or a particular installation attempt'
        else:
            if operation not in {'devices', 'info', 'status', 'apps', 'installed-apps'}: raise ToolContextError('unsupported DuoPlus operation')
            def fake_post(path, body): return {'method': 'POST', 'url': selected['origin']+path, 'payload': body, 'network_call_performed': False}
            client.post = fake_post
            result = getattr(client, operation.replace('-', '_'))(**payload)
    else: raise ToolContextError('unsupported provider')
    if provider == 'tikhub': result['authorization_scope'] = {'tenant_id': context['tenant_id'], 'profile_digest': context['profile_digest'],
        'plan_digest': digest(result), 'maximum_paid_requests': 2}
    return {'schema': 'orbit-tool-preview/v1', 'status': 'PREVIEW', 'tenant_id': context['tenant_id'],
            'profile_digest': context['profile_digest'], 'provider': provider, 'operation': operation,
            'request_digest': digest({'profile_digest': context['profile_digest'], 'operation': operation, 'request': result}),
            'request': result, 'request_attempted': False, 'paid_request_count': 0, 'platform_write_count': 0,
            'credential_value_read': False, 'local_write_count': 0}


def read_provider(context: dict, provider: str, operation: str, payload: dict) -> dict:
    """Explicit provider reads; offline previews are not device discovery."""
    if provider != 'duoplus' or operation not in {'devices', 'info', 'status', 'apps', 'installed-apps'}:
        raise ToolContextError('unsupported read-only provider operation')
    selected = context['profile']['providers'].get(provider)
    if selected is None: raise ToolContextError('provider missing from profile')
    value = os.environ.get(selected['credential_env'], '')
    if not value: raise ToolContextError('selected environment key is missing')
    from modules.tools.duoplus import DuoPlusClient
    client = DuoPlusClient(api_key=value, base_url=selected['origin'])
    result = getattr(client, operation.replace('-', '_'))(**payload)
    return {'schema': 'orbit-tool-read/v1', 'provider': provider, 'operation': operation,
            'tenant_id': context['tenant_id'], 'profile_digest': context['profile_digest'],
            'response': result, 'network_call_performed': True, 'cloud_phone_mutation_performed': False}


def execute(context: dict, provider: str, operation: str, payload: dict, authorization: dict) -> dict:
    selected = context['profile']['providers'].get(provider)
    if selected is None: raise ToolContextError('provider missing from profile')
    # Only execution reads the selected process variable; never the registry or local config.
    value = os.environ.get(selected['credential_env'], '')
    if not value: raise ToolContextError('selected environment key is missing')
    checked_path(context['project_root'], context['artifact_root'])
    if provider == 'duoplus' and operation in {'install-app', 'reconcile-install'}:
        from modules.tools.duoplus import DuoPlusClient, install_scope, execute_install
        scope = install_scope(tenant_id=context['tenant_id'], profile_digest=context['profile_digest'], origin=selected['origin'], **payload)
        return execute_install(DuoPlusClient(api_key=value, base_url=selected['origin']), artifact_root=context['artifact_root'],
            scope=scope, authorization=authorization, reconcile_only=operation == 'reconcile-install')
    if provider == 'tikhub' and operation == 'query':
        from modules.tools.tikhub import preview as tikhub_preview, collect
        return collect(plan=tikhub_preview(**payload), tenant_id=context['tenant_id'], profile_digest=context['profile_digest'],
                       artifact_root=context['artifact_root'], authorization=authorization, api_key=value)
    raise ToolContextError('paid Lingshi execution uses the existing R2 budget/checkpoint entry; unsupported generic execution')
