"""Build a code-only portable toolkit from a fixed allowlist; never copy personal runtime data."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from shared_platform.capability_runtime import checked_path, digest, file_content, tree_files, preflight_write_paths, ToolPlanError

CORE_FILES = (
    'scripts/orbit_tools.py', 'scripts/package_agent_tools.py',
    'scripts/sync_product_publication_skills.py', 'scripts/sync_publish_approved_product_skill.py',
    'scripts/analyze_tikhub_market_snapshot.py', 'shared_platform/capability_runtime.py',
    'modules/tools/duoplus.py', 'modules/tools/tikhub.py',
    'modules/tools/provider_http.py',
    'modules/product_agent/__init__.py', 'modules/product_agent/knowledge.py',
    'scripts/sync_product_publication_knowledge.py', 'docs/knowledge/README.md',
    'modules/sourcing/lingshi_client.py', 'modules/sourcing/image_generation_checkpoint.py',
    'config/capability_catalog.json', 'config/tool_profile.example.json', 'docs/tools/README.md',
    'skills/publish-approved-product/scripts/close_product_publication.py',
)
PORTABLE_SKILLS = ('use-lingshi-ai', 'control-duoplus-cloud-phone', 'research-tikhub-reference')


def manifest(root: Path) -> dict:
    names = list(CORE_FILES)
    for skill in PORTABLE_SKILLS:
        path = checked_path(root, 'skills/'+skill)
        if not (path/'SKILL.md').is_file(): raise ValueError('missing portable Skill: '+skill)
        names.extend(p.relative_to(root).as_posix() for p in tree_files(path))
    files = {}
    for name in sorted(names):
        path = checked_path(root, name)
        if not path.is_file(): raise ValueError('missing package source: '+name)
        files[name] = hashlib.sha256(file_content(path)).hexdigest()
    value = {'schema': 'orbit-tool-runtime/v1', 'hash_format': 'utf8-lf-text/raw-binary-sha256', 'files': files,
             'package_version': 'orbit-portable-tools/6',
             'composition_basis_commit': json.loads(file_content(root/'config/capability_catalog.json'))['source_basis_commit'],
             'runtime_requirements': {'minimum_python': '3.11', 'required_capabilities': ['BaseException.add_note', 'atomic same-directory hard-link for knowledge export'],
                 'validated_python': '3.12.8', 'validated_platform': 'Windows',
                 'other_python_versions_validated': False,
                 'modules_by_provider': {'lingshi': ['requests'], 'duoplus': [], 'tikhub': [], 'publication-knowledge': []}}}
    value['digest'] = digest(value)
    return value


def build(root: Path, destination: Path, *, write: bool = False) -> dict:
    value = manifest(root)
    target = checked_path(destination.parent, destination)
    if target.exists(): raise ValueError('package destination must be new; retain earlier version')
    path_preflight = preflight_write_paths(target, [('file', target/name) for name in
        [*value['files'], 'config/tool_runtime_manifest.json']], operation='package-build')
    if write:
        completed = []
        try:
            target.mkdir(parents=True)
            for relative in value['files']:
                path = checked_path(target, relative); path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(file_content(checked_path(root, relative)))
                completed.append(relative)
            path = target/'config/tool_runtime_manifest.json'
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            completed.append('config/tool_runtime_manifest.json')
            if manifest(target) != value: raise ValueError('package parity failed')
        except (OSError, ValueError) as error:
            raise ToolPlanError('PACKAGE_WRITE_INTERRUPTED', destination_root=str(target),
                                completed_files=completed, cause=str(error),
                                recovery='Retain this partial package for inspection; compare its files with the planned manifest and choose a new explicit destination. Do not treat it as a valid runtime or delete it automatically.') from error
    return {'ok': True, 'mode': 'build' if write else 'preview', 'destination': str(target),
            'file_count': len(value['files'])+1, 'manifest': value, 'network_call_performed': False,
            'path_preflight': path_preflight}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--build', action='store_true', help='write a new immutable package directory; default preview')
    args = parser.parse_args(argv)
    try:
        if checked_path(args.runtime_root, '.').resolve() != ROOT: raise ValueError('runtime-root must match this launcher')
        print(json.dumps(build(ROOT, args.destination, write=args.build), ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps(error.diagnostic if isinstance(error, ToolPlanError) else {'ok': False, 'error': str(error)}, ensure_ascii=False)); return 2


if __name__ == '__main__': raise SystemExit(main())
