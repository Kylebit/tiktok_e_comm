"""Check an explicit Agent CLI profile before dispatching an original entry.

Only the standard library and Git metadata are used during check-binding.
Version 1 preserves source-relative layouts; version 2 scopes captured inputs.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
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
R1_CONTRACT = 'orbit-r1-paths/v2'
AGENT_ENTRY_BINDING_CONTRACT = 'orbit-agent-entry/v2'
QA_ASSESSMENT_BINDING_CONTRACT = 'orbit-qa-assessment-paths/v2'
IMAGES_STATUS_BINDING_CONTRACT = 'orbit-images-status-paths/v2'
DELIST_OFFLINE_BINDING_CONTRACT = 'orbit-delist-offline-paths/v2'
R1_CONTRACT_FILES = ('modules/sourcing/new_product_workbench.py',
    'modules/sourcing/pipeline.py', 'modules/sourcing/manual_product_intake.py',
    'shared_platform/release_control.py', ENTRIES['preparation'])
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


def source_constant_contract(path, name, expected, error):
    try:
        module = ast.parse(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, SyntaxError):
        raise ValueError(error) from None
    values = [node.value.value for node in module.body
              if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
              and any(isinstance(target, ast.Name) and target.id == name
                      for target in node.targets)]
    if values != [expected]:
        raise ValueError(error)


def source_settings_contract(root):
    source_constant_contract(root/'core/config.py', 'SETTINGS_BINDING_CONTRACT',
                            SETTINGS_CONTRACT, 'SOURCE_SETTINGS_BINDING_CAPABILITY_MISSING')


def check_environment(root, scoped=None):
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
    if scoped:
        defaults.update({'ORBIT_RELEASE_STORE_PATH':Path(scoped['release_store_path']),
                         'ORBIT_REPORT_STORE_PATH':Path(scoped['report_store_path'])})
        if scoped.get('workbench_store_path'):
            defaults['ORBIT_WORKBENCH_STORE_PATH'] = Path(scoped['workbench_store_path'])
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
            raise ValueError(('ENV_BINDING_UNSUPPORTED_V2: ' if scoped else 'ENV_BINDING_UNSUPPORTED_V1: ') + key)
    profile_overrides = {'ORBIT_RELEASE_STORE_PATH','ORBIT_REPORT_STORE_PATH'} if scoped else set()
    if scoped and scoped.get('workbench_store_path'):
        profile_overrides.add('ORBIT_WORKBENCH_STORE_PATH')
    return {'profile_fixed': sorted(fixed | profile_overrides),
            'must_match_source_defaults': sorted(set(defaults)-profile_overrides),
            'must_match_profile': sorted(profile_overrides),
            'must_match_relative_defaults': sorted(relative),
            'other_orbit_path_overrides': 'rejected'}


def check_arguments(bound, entry, arguments):
    if bound.get('entry_mode') == 'delist-offline-diagnostic':
        if not arguments:
            return
        if arguments[0] != 'plan':
            raise ValueError('DELIST_OFFLINE_PLAN_ONLY')
        counts = {'--sku': 0, '--scope': 0, '--no-live': 0}
        index = 1
        while index < len(arguments):
            option, separator, value = arguments[index].partition('=')
            if option not in counts:
                raise ValueError('DELIST_OFFLINE_ARGUMENT_UNSUPPORTED: ' + option)
            counts[option] += 1
            if option == '--no-live':
                if separator or counts[option] != 1:
                    raise ValueError('DELIST_OFFLINE_NO_LIVE_REQUIRED')
            else:
                if not separator:
                    index += 1
                    if index >= len(arguments):
                        raise ValueError('DELIST_OFFLINE_ARGUMENT_VALUE_REQUIRED')
                    value = arguments[index]
                if not value or value.startswith('--'):
                    raise ValueError('DELIST_OFFLINE_ARGUMENT_VALUE_REQUIRED')
                if option == '--scope' and (value != 'all' or counts[option] != 1):
                    raise ValueError('DELIST_OFFLINE_SCOPE_ALL_REQUIRED')
            index += 1
        if not counts['--sku'] or counts['--no-live'] != 1:
            raise ValueError('DELIST_OFFLINE_SKU_AND_NO_LIVE_REQUIRED')
        return
    for index, value in enumerate(arguments):
        option, separator, supplied = value.partition('=')
        output_option = entry == 'preparation' and option == '--output'
        forbidden = ROOT_ARGUMENTS | {'--binding-profile', '--binding-profile-sha256'}
        if option in forbidden or (option.startswith('--') and len(option)>2 and
                any(flag.startswith(option) for flag in forbidden) and
                not output_option):
            raise ValueError('ENTRY_ROOT_ARGUMENT_UNSUPPORTED: ' + option)
        # R1 disables argparse abbreviation; metadata accepts the same full flag.
        if output_option:
            if not separator:
                if index + 1 >= len(arguments):
                    raise ValueError('ENTRY_OUTPUT_PATH_REQUIRED')
                supplied = arguments[index + 1]
            path = Path(supplied).expanduser()
            if not path.is_absolute():
                path = Path(bound['source_root']) / path
            if not path.resolve().is_relative_to(Path(bound['output_root'])):
                raise ValueError('ENTRY_OUTPUT_OUTSIDE_BOUND_ROOT')
    if bound.get('entry_mode') == 'images-captured-status' and arguments:
        if len(arguments) == 2 and arguments[0] == '--offer-id':
            offer = arguments[1]
        elif len(arguments) == 1 and arguments[0].startswith('--offer-id='):
            offer = arguments[0].partition('=')[2]
        else:
            raise ValueError('IMAGES_STATUS_CANONICAL_OFFER_ARGUMENT_REQUIRED')
        if re.fullmatch(r'[0-9]+', offer) is None:
            raise ValueError('IMAGES_STATUS_OFFER_ID_INVALID')
    if bound.get('entry_mode') == 'qa-existing-assessment' and arguments:
        values = {}
        index = 0
        while index < len(arguments):
            option, separator, value = arguments[index].partition('=')
            if option not in ('--offer-id', '--assessment'):
                raise ValueError('QA_ASSESSMENT_ARGUMENT_UNSUPPORTED: ' + option)
            if option in values:
                raise ValueError('QA_ARGUMENT_DUPLICATE: ' + option)
            if not separator:
                index += 1
                if index >= len(arguments):
                    raise ValueError('QA_ARGUMENT_VALUE_REQUIRED: ' + option)
                value = arguments[index]
            values[option] = value
            index += 1
        if set(values) != {'--offer-id', '--assessment'}:
            raise ValueError('QA_OFFER_AND_EXISTING_ASSESSMENT_REQUIRED')
        if re.fullmatch(r'[0-9]+', values['--offer-id']) is None:
            raise ValueError('QA_OFFER_ID_INVALID')
        assessment = checked_path(values['--assessment'], 'QA_CAPTURED_ASSESSMENT')
        if str(assessment) != bound['assessment_path']:
            raise ValueError('QA_ASSESSMENT_PATH_CHANGED')


def direct_producer_lock_contract(root):
    """Prove the original direct CLI's source-relative phase-lock expression.

    This does not establish a native/custom runtime mapping or report provenance.
    """
    try:
        module = ast.parse((root/ENTRIES['images']).read_text(encoding='utf-8-sig'))
        functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
        roots = [node.value for node in module.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'REPO_ROOT' for target in node.targets)]
        directory = [node.value for node in ast.walk(functions['run']) if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == 'directory' for target in node.targets)]
        locks = [item.context_expr for node in ast.walk(functions['run']) if isinstance(node, ast.With)
                 for item in node.items if isinstance(item.context_expr, ast.Call)
                 and isinstance(item.context_expr.func, ast.Name) and item.context_expr.func.id == 'business_lock']
        def same(node, expression):
            return ast.dump(node) == ast.dump(ast.parse(expression).body[0].value)
        valid = (len(roots) == len(directory) == len(locks) == 1
            and same(roots[0], 'Path(__file__).resolve().parents[3]')
            and ast.dump(functions['_runtime_root'].body[0]) == ast.dump(ast.parse('if runtime is None:\n    return REPO_ROOT').body[0])
            and same(directory[0], "_runtime_root(runtime)/'reports/product-preparation'/str(args.offer_id)")
            and same(locks[0], "business_lock(directory,digest({'scope':'round2-phase','offer_id':str(args.offer_id)}))"))
    except (OSError, ValueError, SyntaxError, KeyError, IndexError):
        valid = False
    if not valid:
        raise ValueError('QA_DIRECT_PRODUCER_LOCK_CONTRACT_UNPROVEN')


def check_qa_assessment_binding(path, raw, profile):
    allowed = {'schema','entry_mode','source_root','expected_source_head','state_dir',
        'round1_reports_root','r2_reports_root','assessment_path','qa_output_root','phase_lock_root',
        'r2_producer_mode','r2_producer_source_root','expected_r2_producer_head'}
    unknown = set(profile)-allowed
    if unknown:
        raise ValueError('QA_PROFILE_FIELD_UNSUPPORTED: ' + ','.join(sorted(unknown)))
    if profile.get('r2_producer_mode') != 'direct-cli-source-reports':
        raise ValueError('QA_R2_PRODUCER_MODE_UNSUPPORTED')
    root = checked_path(profile.get('source_root'), 'SOURCE_ROOT', directory=True)
    producer = checked_path(profile.get('r2_producer_source_root'), 'QA_R2_PRODUCER_SOURCE_ROOT', directory=True)
    for selected, expected, role, files in (
        (root, profile.get('expected_source_head'), 'SOURCE', (*SOURCE_FILES, ENTRIES['qa'], 'scripts/repo_bound_agent_entry.py')),
        (producer, profile.get('expected_r2_producer_head'), 'QA_PRODUCER_SOURCE', (*SOURCE_FILES, ENTRIES['images']))):
        for relative in files:
            checked_path(str(selected/relative), role+'_FILE')
        if Path(git(selected,'rev-parse','--show-toplevel')).resolve() != selected:
            raise ValueError(role+'_MUST_BE_GIT_TOP_LEVEL')
        git(selected,'ls-files','--error-unmatch','--',*files)
        if not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{40}',expected):
            raise ValueError(role+'_EXPECTED_HEAD_REQUIRED')
        if git(selected,'rev-parse','HEAD') != expected:
            raise ValueError(role+'_HEAD_MISMATCH')
        if git(selected,'status','--porcelain=v1','-uall'):
            raise ValueError(role+'_DIRTY')
    for relative in (ENTRIES['qa'], 'scripts/repo_bound_agent_entry.py'):
        source_constant_contract(root/relative,'QA_ASSESSMENT_BINDING_CONTRACT',
            QA_ASSESSMENT_BINDING_CONTRACT,'SOURCE_QA_ASSESSMENT_CAPABILITY_MISSING: '+relative)
    direct_producer_lock_contract(producer)
    fields = {name:str(checked_path(profile.get(name),'QA_CAPTURED_'+name.upper(),
        directory=name != 'assessment_path')) for name in (
        'state_dir','round1_reports_root','r2_reports_root','assessment_path','qa_output_root','phase_lock_root')}
    original = producer/'reports/product-preparation'
    if Path(fields['r2_reports_root']) != original or Path(fields['phase_lock_root']) != original:
        raise ValueError('QA_ORIGINAL_PRODUCER_PHASE_LOCK_UNPROVEN')
    output = Path(fields['qa_output_root'])
    for name in ('state_dir','round1_reports_root','r2_reports_root'):
        input_path = Path(fields[name])
        if output.is_relative_to(input_path) or input_path.is_relative_to(output):
            raise ValueError('QA_INPUT_OUTPUT_ROOT_OVERLAP: '+name)
    if Path(fields['assessment_path']).is_relative_to(output):
        raise ValueError('QA_INPUT_OUTPUT_ROOT_OVERLAP: assessment_path')
    for key,value in os.environ.items():
        if value and (key == 'ORBIT_HIVE_SETTINGS' or key == 'LINGSHI_IMAGE_QA_MODEL'
                or (key.startswith('ORBIT_') and key.endswith(('_ROOT','_PATH','_DATABASE','_PROFILE')))):
            raise ValueError('ENV_BINDING_UNSUPPORTED_QA_ASSESSMENT: '+key)
    scopes = {'source_root':'exact clean QA source', 'expected_source_head':'QA source identity',
        'state_dir':'existing captured JSON state', 'round1_reports_root':'retained R1 snapshot identity',
        'r2_reports_root':'generation and optional translation input reports', 'assessment_path':'existing captured assessment',
        'qa_output_root':'normalized assessment, signed QA receipt and signed attempt archives',
        'phase_lock_root':'original direct producer round2-phase lock',
        'r2_producer_source_root':'statically verified original direct CLI source path',
        'expected_r2_producer_head':'original direct producer source identity', 'r2_producer_mode':'direct CLI only'}
    propagation = {name:{'supported':True,'scope':scope} for name,scope in scopes.items()}
    for name in ('master_qa_reports_root','settings_path','config_root','data_root','output_root',
                 'catalog_database','release_store_path','report_store_path','workbench_store_path','lingshi_config_path'):
        propagation[name] = {'supported':False,'scope':'not consumed or accepted by QA existing-assessment subset'}
    return {'status':'AGENT_ENTRY_LAYOUT_BINDING_VALIDATED','schema':profile['schema'],
        'entry_mode':'qa-existing-assessment','profile_path':str(path),'profile_sha256':hashlib.sha256(raw).hexdigest(),
        'source_root':str(root),'source_head':profile['expected_source_head'],'entry':str(root/ENTRIES['qa']),
        'r2_producer_source_root':str(producer),'expected_r2_producer_head':profile['expected_r2_producer_head'],
        'r2_producer_mode':profile['r2_producer_mode'], **fields,
        'qa_assessment_binding_contract':QA_ASSESSMENT_BINDING_CONTRACT,
        'supported_layout':'QA existing captured assessment only; explicit independent inputs/output and original direct producer lock',
        'field_propagation':propagation,'settings_override_supported':False,'separate_data_output_supported':True,
        'producer_proof':'direct CLI code path plus declared roots/HEAD only; native/custom runtime and actual report provenance not established',
        'dispatch_preflight':'layout/source only; per-offer captured inputs and existing domain validity checked on dispatch',
        'local_writes':['original producer phase lock','QA normalized assessment','QA signed receipt','QA signed superseded attempt archive'],
        'domain_imported':False,'sql_connections':0,'provider_calls':0,'business_calls':0,'auth_writes':0,'paid_calls':0}


def check_images_status_binding(path, raw, profile):
    allowed = {'schema','entry_mode','source_root','expected_source_head','state_dir',
        'round1_reports_root','r2_reports_root','phase_lock_root','r2_producer_mode',
        'r2_producer_source_root','expected_r2_producer_head'}
    if set(profile)-allowed:
        raise ValueError('IMAGES_STATUS_PROFILE_FIELD_UNSUPPORTED: '+','.join(sorted(set(profile)-allowed)))
    if profile.get('r2_producer_mode') != 'direct-cli-source-reports':
        raise ValueError('IMAGES_STATUS_R2_PRODUCER_MODE_UNSUPPORTED')
    root = checked_path(profile.get('source_root'),'SOURCE_ROOT',directory=True)
    producer = checked_path(profile.get('r2_producer_source_root'),'IMAGES_PRODUCER_SOURCE_ROOT',directory=True)
    for selected,expected,role,files in (
        (root,profile.get('expected_source_head'),'SOURCE',(*SOURCE_FILES,ENTRIES['images'],'scripts/repo_bound_agent_entry.py')),
        (producer,profile.get('expected_r2_producer_head'),'IMAGES_PRODUCER_SOURCE',(*SOURCE_FILES,ENTRIES['images']))):
        for relative in files:
            checked_path(str(selected/relative),role+'_FILE')
        if Path(git(selected,'rev-parse','--show-toplevel')).resolve() != selected:
            raise ValueError(role+'_MUST_BE_GIT_TOP_LEVEL')
        git(selected,'ls-files','--error-unmatch','--',*files)
        if not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{40}',expected):
            raise ValueError(role+'_EXPECTED_HEAD_REQUIRED')
        if git(selected,'rev-parse','HEAD') != expected:
            raise ValueError(role+'_HEAD_MISMATCH')
        if git(selected,'status','--porcelain=v1','-uall'):
            raise ValueError(role+'_DIRTY')
    for relative in (ENTRIES['images'],'scripts/repo_bound_agent_entry.py'):
        source_constant_contract(root/relative,'IMAGES_STATUS_BINDING_CONTRACT',IMAGES_STATUS_BINDING_CONTRACT,
            'SOURCE_IMAGES_STATUS_CAPABILITY_MISSING: '+relative)
    # Keep the same original direct producer proof used by QA. No native/custom mapping.
    direct_producer_lock_contract(producer)
    fields = {name:str(checked_path(profile.get(name),'IMAGES_CAPTURED_'+name.upper(),directory=True))
        for name in ('state_dir','round1_reports_root','r2_reports_root','phase_lock_root')}
    original = producer/'reports/product-preparation'
    if Path(fields['r2_reports_root']) != original or Path(fields['phase_lock_root']) != original:
        raise ValueError('IMAGES_ORIGINAL_PRODUCER_PHASE_LOCK_UNPROVEN')
    for key,value in os.environ.items():
        if value and (key in ('ORBIT_HIVE_SETTINGS','TIKTOK_E_COMM_ROOT') or
            (key.startswith('ORBIT_') and key.endswith(('_ROOT','_PATH','_DIR','_DATABASE','_PROFILE')))):
            raise ValueError('ENV_BINDING_UNSUPPORTED_IMAGES_STATUS: '+key)
    scopes = {'source_root':'exact clean images source','expected_source_head':'images source identity',
        'state_dir':'existing captured JSON state','round1_reports_root':'retained R1 snapshot input',
        'r2_reports_root':'existing generation and optional translation reports',
        'phase_lock_root':'original direct producer round2-phase lock',
        'r2_producer_source_root':'statically verified original direct CLI source path',
        'expected_r2_producer_head':'original direct producer source identity','r2_producer_mode':'direct CLI only'}
    propagation = {name:{'supported':True,'scope':scope} for name,scope in scopes.items()}
    for name in ('settings_path','config_root','data_root','output_root','qa_output_root','assessment_path',
        'catalog_database','release_store_path','report_store_path','workbench_store_path','lingshi_config_path',
        'checkpoint_root','history_root','master_qa_reports_root'):
        propagation[name] = {'supported':False,'scope':'not consumed or accepted by captured status subset'}
    return {'status':'AGENT_ENTRY_LAYOUT_BINDING_VALIDATED','schema':profile['schema'],
        'entry_mode':'images-captured-status','profile_path':str(path),'profile_sha256':hashlib.sha256(raw).hexdigest(),
        'source_root':str(root),'source_head':profile['expected_source_head'],'entry':str(root/ENTRIES['images']),
        'r2_producer_source_root':str(producer),'expected_r2_producer_head':profile['expected_r2_producer_head'],
        'r2_producer_mode':profile['r2_producer_mode'],**fields,
        'images_status_binding_contract':IMAGES_STATUS_BINDING_CONTRACT,
        'supported_layout':'existing captured images status only; explicit state/R1/R2 and original direct producer lock',
        'field_propagation':propagation,'settings_override_supported':False,'separate_data_output_supported':False,
        'producer_proof':'direct CLI code path plus declared roots/HEAD only; native/custom runtime and actual capture lineage not established',
        'dispatch_preflight':'layout/source only; per-offer existing captures and R1 validity checked on dispatch',
        'local_writes':['original direct producer phase lock'],'result_output':'stdout only',
        'domain_imported':False,'sql_connections':0,'provider_calls':0,'business_calls':0,'auth_writes':0,'paid_calls':0}


def checked_offline_child(root, path, name, *, directory=None):
    """Check one known child before metadata/read/write; missing caches are valid."""
    root, path = Path(root), Path(path)
    if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(root):
        raise ValueError(name + '_OUTSIDE_BOUND_ROOT')
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError(name + '_REPARSE_REJECTED')
    if path.exists() and directory is not None:
        if not (path.is_dir() if directory else path.is_file()):
            raise ValueError(name + '_WRONG_TYPE')
    return path


def check_delist_offline_binding(path, raw, profile):
    allowed = {'schema', 'entry_mode', 'source_root', 'expected_source_head',
               'catalog_database', 'captured_reports_root', 'data_root', 'ozon_data_root', 'output_root'}
    if set(profile) - allowed:
        raise ValueError('DELIST_OFFLINE_PROFILE_FIELD_UNSUPPORTED: ' + ','.join(sorted(set(profile)-allowed)))
    root = checked_path(profile.get('source_root'), 'SOURCE_ROOT', directory=True)
    files = (*SOURCE_FILES, ENTRIES['delist'], 'scripts/repo_bound_agent_entry.py',
             'modules/catalog/ozon_offline_data.py', 'modules/catalog/sku_key.py')
    for relative in files:
        checked_path(str(root/relative), 'DELIST_OFFLINE_SOURCE_FILE')
    if Path(git(root, 'rev-parse', '--show-toplevel')).resolve() != root:
        raise ValueError('SOURCE_MUST_BE_GIT_TOP_LEVEL')
    git(root, 'ls-files', '--error-unmatch', '--', *files)
    expected = profile.get('expected_source_head')
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{40}', expected):
        raise ValueError('EXPECTED_SOURCE_HEAD_REQUIRED')
    if git(root, 'rev-parse', 'HEAD') != expected:
        raise ValueError('SOURCE_HEAD_MISMATCH')
    if git(root, 'status', '--porcelain=v1', '-uall'):
        raise ValueError('SOURCE_DIRTY')
    for relative in (ENTRIES['delist'], 'scripts/repo_bound_agent_entry.py'):
        source_constant_contract(root/relative, 'DELIST_OFFLINE_BINDING_CONTRACT',
                                DELIST_OFFLINE_BINDING_CONTRACT, 'SOURCE_DELIST_OFFLINE_CAPABILITY_MISSING')
    source_constant_contract(root/'modules/catalog/ozon_offline_data.py', 'OZON_OFFLINE_READER_CONTRACT',
                            'orbit-ozon-captured-data/v1', 'SOURCE_OZON_OFFLINE_CAPABILITY_MISSING')
    fields = {name: str(checked_path(profile.get(name), 'DELIST_OFFLINE_'+name.upper(),
                                     directory=name != 'catalog_database')) for name in (
        'catalog_database', 'captured_reports_root', 'data_root', 'ozon_data_root', 'output_root')}
    output = Path(fields['output_root'])
    for name in ('captured_reports_root', 'data_root', 'ozon_data_root'):
        selected = Path(fields[name])
        if output.is_relative_to(selected) or selected.is_relative_to(output):
            raise ValueError('DELIST_OFFLINE_INPUT_OUTPUT_OVERLAP: '+name)
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('DELIST_OFFLINE_SOURCE_OUTPUT_OVERLAP')
    for selected, name in ((Path(fields['catalog_database']), 'catalog_database'), (path, 'profile')):
        if selected.is_relative_to(output):
            raise ValueError('DELIST_OFFLINE_INPUT_OUTPUT_OVERLAP: '+name)
    for key, value in os.environ.items():
        if not value:
            continue
        if key == 'ORBIT_CATALOG_DATABASE':
            if str(checked_path(value, 'ENV_CATALOG_DATABASE')) != fields['catalog_database']:
                raise ValueError('ENV_BINDING_CONFLICT: '+key)
        elif (key in ('ORBIT_HIVE_SETTINGS', 'TIKTOK_E_COMM_ROOT') or
              (key.startswith('ORBIT_') and key.endswith(('_ROOT', '_PATH', '_DIR', '_DATABASE', '_PROFILE', '_SETTINGS')))):
            raise ValueError('ENV_BINDING_UNSUPPORTED_DELIST_OFFLINE: '+key)
    scopes = {'source_root': 'exact clean source', 'expected_source_head': 'source identity',
              'catalog_database': 'existing readonly catalog', 'captured_reports_root': 'existing preparation/publication/discount JSON',
              'data_root': 'existing Shopee SKU map only', 'ozon_data_root': 'three existing Ozon cached JSON files',
              'output_root': 'local offline diagnostic plan'}
    propagation = {name: {'supported': True, 'scope': scope} for name, scope in scopes.items()}
    for name in ('settings_path', 'config_root', 'state_dir', 'release_store_path', 'report_store_path',
                 'workbench_store_path', 'lingshi_config_path'):
        propagation[name] = {'supported': False, 'scope': 'not consumed or accepted by offline diagnostic subset'}
    return {'status': 'AGENT_ENTRY_LAYOUT_BINDING_VALIDATED', 'schema': profile['schema'],
            'entry_mode': 'delist-offline-diagnostic', 'profile_path': str(path),
            'profile_sha256': hashlib.sha256(raw).hexdigest(), 'source_root': str(root), 'source_head': expected,
            'entry': str(root/ENTRIES['delist']), **fields, 'field_propagation': propagation,
            'delist_offline_binding_contract': DELIST_OFFLINE_BINDING_CONTRACT,
            'supported_layout': 'existing independent catalog/cache roots and separate diagnostic output',
            'settings_override_supported': False, 'separate_data_output_supported': True,
            'dispatch_preflight': 'source/path metadata only; cached candidates are not executable or official readback',
            'local_writes': ['product-delisting/<normalized-skus>/delist-plan.json and its parent directories'],
            'domain_imported': False, 'sql_connections': 0, 'provider_calls': 0, 'business_calls': 0, 'auth_writes': 0, 'paid_calls': 0}


def check_binding(profile_path, entry):
    path = checked_path(str(profile_path), 'PROFILE')
    try:
        raw = path.read_bytes()
        profile = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError):
        raise ValueError('PROFILE_INVALID_JSON') from None
    if not isinstance(profile, dict) or profile.get('schema') not in ('orbit-agent-entry/v1','orbit-agent-entry/v2'):
        raise ValueError('PROFILE_SCHEMA_INVALID')
    v2 = profile['schema'] == 'orbit-agent-entry/v2'
    if v2 and entry == 'delist' and profile.get('entry_mode') == 'delist-offline-diagnostic':
        return check_delist_offline_binding(path, raw, profile)
    if v2 and entry == 'qa' and profile.get('entry_mode') == 'qa-existing-assessment':
        return check_qa_assessment_binding(path, raw, profile)
    if v2 and entry == 'images' and profile.get('entry_mode') == 'images-captured-status':
        return check_images_status_binding(path, raw, profile)
    if v2 and entry != 'preparation':
        raise ValueError('ENTRY_PATH_BINDING_UNSUPPORTED_V2: ' + entry)
    if v2:
        allowed = {'schema','source_root','expected_source_head','settings_path','config_root',
            'data_root','output_root','catalog_database','state_dir','source_outputs_root',
            'content_outputs_root','release_store_path','report_store_path',
            'workbench_store_path','lingshi_config_path'}
        unknown = set(profile)-allowed
        if unknown:
            raise ValueError('PROFILE_FIELD_UNSUPPORTED_V2: ' + ','.join(sorted(unknown)))
    root = checked_path(profile.get('source_root'), 'SOURCE_ROOT', directory=True)
    settings = checked_path(profile.get('settings_path'), 'SETTINGS_PATH')
    config = checked_path(profile.get('config_root'), 'CONFIG_ROOT', directory=True)
    data = checked_path(profile.get('data_root'), 'DATA_ROOT', directory=True)
    output = checked_path(profile.get('output_root'), 'OUTPUT_ROOT', directory=True)
    database = None
    if v2 or entry == 'delist' or profile.get('catalog_database') is not None:
        database = checked_path(profile.get('catalog_database'), 'CATALOG_DATABASE')
    # These layouts are used by existing producer/consumer defaults. Reject
    # unsupported separation rather than pretending to redirect every consumer.
    for actual, expected, name in ((config, root/'config', 'CONFIG'),
                                   (data, root/'data', 'DATA'),
                                   (output, root/'reports', 'OUTPUT')):
        if not v2 and actual != expected.resolve():
            raise ValueError('SEPARATE_' + name + '_ROOT_UNSUPPORTED_V1')
    scoped = {}
    if v2:
        if not settings.is_relative_to(config):
            raise ValueError('SETTINGS_OUTSIDE_CONFIG_ROOT_V2')
        for name in ('state_dir','source_outputs_root','content_outputs_root',
                     'release_store_path','report_store_path','workbench_store_path','lingshi_config_path'):
            if name in ('workbench_store_path','lingshi_config_path') and profile.get(name) is None:
                continue
            scoped[name] = str(checked_path(profile.get(name), name.upper(),
                directory=name in ('state_dir','source_outputs_root','content_outputs_root')))
    if not all((root/name).is_file() for name in SOURCE_FILES):
        raise ValueError('COMPLETE_AGENT_SOURCE_REQUIRED')
    for relative in SOURCE_FILES:
        checked_path(str(root/relative), 'SOURCE_FILE')
    source_settings_contract(root)
    if v2:
        for relative in R1_CONTRACT_FILES:
            checked_path(str(root/relative), 'R1_SOURCE_FILE')
            source_constant_contract(root/relative, 'R1_PATH_BINDING_CONTRACT', R1_CONTRACT,
                                     'SOURCE_R1_PATH_BINDING_CAPABILITY_MISSING: '+relative)
        checker = checked_path(str(root/'scripts/repo_bound_agent_entry.py'), 'SOURCE_ENTRY_CHECKER')
        source_constant_contract(checker, 'AGENT_ENTRY_BINDING_CONTRACT', 'orbit-agent-entry/v2',
                                 'SOURCE_ENTRY_CHECKER_CAPABILITY_MISSING_V2')
        git(root, 'ls-files', '--error-unmatch', '--', *R1_CONTRACT_FILES, 'scripts/repo_bound_agent_entry.py')
    environment = check_environment(root, scoped if v2 else None)
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
    result = {'status': 'AGENT_ENTRY_LAYOUT_BINDING_VALIDATED', 'schema': profile['schema'],
            'profile_path':str(path), 'profile_sha256':hashlib.sha256(raw).hexdigest(),
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
    if v2:
        result.update(scoped)
        result.update({'supported_layout':'R1 existing captured inputs only; independent explicit roots',
            'separate_data_output_supported':True, 'r1_path_binding_contract':R1_CONTRACT,
            'dispatch_preflight':'layout/source only; per-offer captured state and domain validity checked on dispatch',
            'field_propagation':{name:{'supported':True,'scope':scope} for name,scope in {
                'source_root':'exact source script', 'expected_source_head':'exact clean Git identity',
                'settings_path':'core.config explicit selection',
                'config_root':'settings containment and optional configuration discovery only',
                'data_root':'sourcing and manual intake reads', 'state_dir':'workbench and SKU reservation reads',
                'source_outputs_root':'source capture reads', 'content_outputs_root':'content metadata reads',
                'output_root':'R1 packet and sidecar defaults', 'catalog_database':'catalog reads',
                'release_store_path':'release lineage and category observation reads',
                'report_store_path':'weekly report history reads'}.items()}})
        result['field_propagation']['workbench_store_path'] = {'supported':False,
            'scope':'optional existing path fixed in child environment; not consumed by R1 dashboard'}
        result['field_propagation']['lingshi_config_path'] = {'supported':False,'scope':'metadata discovery only'}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
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
    if bound.get('settings_path'):
        env['ORBIT_HIVE_SETTINGS'] = bound['settings_path']
    if bound.get('catalog_database'):
        env['ORBIT_CATALOG_DATABASE'] = bound['catalog_database']
    else:
        env.pop('ORBIT_CATALOG_DATABASE', None)
    if bound['schema'] == 'orbit-agent-entry/v2':
        for key,field in (('ORBIT_RELEASE_STORE_PATH','release_store_path'),
                          ('ORBIT_REPORT_STORE_PATH','report_store_path'),
                          ('ORBIT_WORKBENCH_STORE_PATH','workbench_store_path')):
            if bound.get(field):
                env[key] = bound[field]
        arguments = [*arguments, '--binding-profile', bound['profile_path'],
                     '--binding-profile-sha256', bound['profile_sha256']]
    # subprocess list quoting preserves spaces/backslashes in argv on Windows;
    # os.execve's CRT path can split an argument containing spaces.
    return subprocess.call([sys.executable, '-I', '-B', bound['entry'], *arguments],
                           cwd=bound['source_root'], env=env)


if __name__ == '__main__':
    raise SystemExit(main())
