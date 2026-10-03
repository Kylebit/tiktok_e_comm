"""Check an explicit Agent CLI profile before dispatching an original entry.

Only the standard library and Git metadata are used during check-binding.
Version 1 deliberately preserves source-relative config/data/report layouts.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ENTRIES = {
    'preparation': 'skills/prepare-product-publication/scripts/prepare_product_publication.py',
    'images': 'skills/prepare-product-images/scripts/prepare_product_images.py',
    'qa': 'skills/prepare-product-images/scripts/run_automated_image_qa.py',
    'delist': 'skills/delist-products-by-sku/scripts/delist_products_by_sku.py',
}
SOURCE_FILES = ('core/config.py', 'core/db.py',
                'modules/sourcing/new_product_workbench.py',
                'shared_platform/publication_rounds.py')
SETTINGS_CONTRACT = 'orbit-settings-binding/v1'
ROOT_ARGUMENTS = frozenset({'--repo-root', '--root', '--source-root', '--config-root',
    '--data-root', '--output-root', '--reports-root', '--workdir', '--cwd', '--project-root'})


def checked_path(value, name, *, directory=False):
    if not isinstance(value, str) or not value:
        raise ValueError(name + '_REQUIRED')
    path = Path(value).expanduser()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError(name + '_MUST_BE_ABSOLUTE')
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError(name + '_REPARSE_REJECTED')
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError(name + '_MISSING')
    return path.resolve()


def git(root, *arguments):
    # Ambient Git overrides must not redefine the selected checkout identity.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env['GIT_OPTIONAL_LOCKS'] = '0'
    result = subprocess.run(['git', '-C', str(root), *arguments], env=env,
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ValueError('SOURCE_GIT_IDENTITY_UNAVAILABLE')
    return result.stdout.strip()


def source_settings_contract(root):
    try:
        module = ast.parse((root/'core/config.py').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, SyntaxError):
        raise ValueError('SOURCE_SETTINGS_BINDING_CAPABILITY_MISSING') from None
    values = [node.value.value for node in module.body
              if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
              and any(isinstance(target, ast.Name) and target.id == 'SETTINGS_BINDING_CONTRACT'
                      for target in node.targets)]
    if values != [SETTINGS_CONTRACT]:
        raise ValueError('SOURCE_SETTINGS_BINDING_CAPABILITY_MISSING')


def check_environment(root):
    # Existing consumer overrides, derived from their current source defaults.
    # Settings/catalog are fixed from the profile in the child, never inherited.
    defaults = {'ORBIT_WORKBENCH_STORE_PATH':root/'data/orbit_workbench.db',
                'ORBIT_RELEASE_STORE_PATH':root/'data/orbit_platform.db',
                'ORBIT_REPORT_STORE_PATH':root/'data/orbit_platform.db',
                'ORBIT_R3_CONFIG_ROOT':root, 'ORBIT_R2_REVIEW_RUNTIME_ROOT':root,
                'ORBIT_RELEASE_EVIDENCE_ROOT':root, 'TIKTOK_E_COMM_ROOT':root}
    relative = {'ORBIT_R3_POLICY_PATH':'config/product_publication_autopilot_policy.json',
                'ORBIT_R3_INCIDENT_REGISTRY_PATH':'skills/publish-approved-product/references/incident-registry.json'}
    fixed = {'ORBIT_HIVE_SETTINGS', 'ORBIT_CATALOG_DATABASE'}
    for key, value in os.environ.items():
        if not value or key in fixed:
            continue
        if key in defaults:
            path = Path(value).expanduser()
            if not path.is_absolute() or path.resolve() != defaults[key].resolve():
                raise ValueError('ENV_BINDING_CONFLICT: ' + key)
        elif key in relative:
            if value.replace('\\', '/') != relative[key]:
                raise ValueError('ENV_BINDING_CONFLICT: ' + key)
        elif key.startswith('ORBIT_') and key.endswith(('_ROOT', '_PATH', '_DATABASE', '_PROFILE')):
            # These runtime/provider/evidence/authority overrides are not covered
            # by v1. Preserve them by rejecting, never silently clear them.
            raise ValueError('ENV_BINDING_UNSUPPORTED_V1: ' + key)
    return {'profile_fixed': sorted(fixed), 'must_match_source_defaults': sorted(defaults),
            'must_match_relative_defaults': sorted(relative),
            'other_orbit_path_overrides': 'rejected; no independent-root support in v1'}


def check_arguments(bound, entry, arguments):
    for index, value in enumerate(arguments):
        option, separator, supplied = value.partition('=')
        if option in ROOT_ARGUMENTS:
            raise ValueError('ENTRY_ROOT_ARGUMENT_UNSUPPORTED_V1: ' + option)
        # argparse accepts abbreviations. For R1, guard every output spelling
        # that the original parser can resolve to --output.
        if entry == 'preparation' and option.startswith('--o') and '--output'.startswith(option):
            if not separator:
                if index + 1 >= len(arguments):
                    raise ValueError('ENTRY_OUTPUT_PATH_REQUIRED')
                supplied = arguments[index + 1]
            path = Path(supplied).expanduser()
            if not path.is_absolute():
                path = Path(bound['source_root']) / path
            if not path.resolve().is_relative_to(Path(bound['output_root'])):
                raise ValueError('ENTRY_OUTPUT_OUTSIDE_BOUND_ROOT')


def check_binding(profile_path, entry):
    path = checked_path(str(profile_path), 'PROFILE')
    try:
        profile = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError):
        raise ValueError('PROFILE_INVALID_JSON') from None
    if not isinstance(profile, dict) or profile.get('schema') != 'orbit-agent-entry/v1':
        raise ValueError('PROFILE_SCHEMA_INVALID')
    root = checked_path(profile.get('source_root'), 'SOURCE_ROOT', directory=True)
    settings = checked_path(profile.get('settings_path'), 'SETTINGS_PATH')
    config = checked_path(profile.get('config_root'), 'CONFIG_ROOT', directory=True)
    data = checked_path(profile.get('data_root'), 'DATA_ROOT', directory=True)
    output = checked_path(profile.get('output_root'), 'OUTPUT_ROOT', directory=True)
    database = None
    if entry == 'delist' or profile.get('catalog_database') is not None:
        database = checked_path(profile.get('catalog_database'), 'CATALOG_DATABASE')
    # These layouts are used by existing producer/consumer defaults. Reject
    # unsupported separation rather than pretending to redirect every consumer.
    for actual, expected, name in ((config, root/'config', 'CONFIG'),
                                   (data, root/'data', 'DATA'),
                                   (output, root/'reports', 'OUTPUT')):
        if actual != expected.resolve():
            raise ValueError('SEPARATE_' + name + '_ROOT_UNSUPPORTED_V1')
    if not all((root/name).is_file() for name in SOURCE_FILES):
        raise ValueError('COMPLETE_AGENT_SOURCE_REQUIRED')
    for relative in SOURCE_FILES:
        checked_path(str(root/relative), 'SOURCE_FILE')
    source_settings_contract(root)
    environment = check_environment(root)
    target = checked_path(str(root/ENTRIES[entry]), 'ENTRY')
    if not target.is_relative_to(root):
        raise ValueError('ENTRY_OUTSIDE_SOURCE')
    if Path(git(root, 'rev-parse', '--show-toplevel')).resolve() != root:
        raise ValueError('SOURCE_MUST_BE_GIT_TOP_LEVEL')
    git(root, 'ls-files', '--error-unmatch', '--', *SOURCE_FILES, ENTRIES[entry])
    expected = profile.get('expected_source_head')
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{40}', expected):
        raise ValueError('EXPECTED_SOURCE_HEAD_REQUIRED')
    head = git(root, 'rev-parse', 'HEAD')
    if head != expected:
        raise ValueError('SOURCE_HEAD_MISMATCH')
    if git(root, 'status', '--porcelain=v1', '-uall'):
        raise ValueError('SOURCE_DIRTY')
    return {'status': 'AGENT_ENTRY_LAYOUT_BINDING_VALIDATED', 'schema': profile['schema'],
            'source_root': str(root), 'source_head': head, 'entry': str(target),
            'settings_path': str(settings), 'config_root': str(config),
            'data_root': str(data), 'output_root': str(output),
            'catalog_database': str(database) if database else None,
            'supported_layout': 'config_root=source/config; data_root=source/data; output_root=source/reports',
            'settings_override_supported': True, 'separate_data_output_supported': False,
            'settings_binding_contract': SETTINGS_CONTRACT, 'environment_contract': environment,
            'dispatch_preflight': 'path metadata and clean Git identity only; domain inputs/authority not checked',
            'domain_imported': False, 'sql_connections': 0, 'provider_calls': 0,
            'business_calls': 0, 'auth_writes': 0, 'paid_calls': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--entry', choices=ENTRIES, required=True)
    parser.add_argument('--check-binding', action='store_true')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        bound = check_binding(args.profile, args.entry)
        arguments = args.arguments
        if arguments and arguments[0] == '--':
            arguments = arguments[1:]
        check_arguments(bound, args.entry, arguments)
        if args.check_binding:
            print(json.dumps(bound, ensure_ascii=True, indent=2))
            return 0
        if not arguments:
            raise ValueError('EXPLICIT_ENTRY_ARGUMENTS_REQUIRED')
        # Recheck before dispatch; the original CLI owns all business validation.
        bound = check_binding(args.profile, args.entry)
        check_arguments(bound, args.entry, arguments)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 2
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env['ORBIT_HIVE_SETTINGS'] = bound['settings_path']
    if bound['catalog_database']:
        env['ORBIT_CATALOG_DATABASE'] = bound['catalog_database']
    else:
        env.pop('ORBIT_CATALOG_DATABASE', None)
    # subprocess list quoting preserves spaces/backslashes in argv on Windows;
    # os.execve's CRT path can split an argument containing spaces.
    return subprocess.call([sys.executable, '-I', '-B', bound['entry'], *arguments],
                           cwd=bound['source_root'], env=env)


if __name__ == '__main__':
    raise SystemExit(main())
