"""Execute exact native caller bodies with closed synthetic parent transports."""
import ast
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import tomllib

import pytest

SOURCE = Path(__file__).resolve().parents[1]


def _body(relative, function, globals_):
    tree = ast.parse((SOURCE / relative).read_text(encoding='utf-8-sig'))
    if '.' in function:
        owner, name = function.split('.')
        tree = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner)
    else:
        name = function
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), relative, 'exec'), globals_)
    return globals_[name]


def _module(monkeypatch, name, **attributes):
    value = ModuleType(name)
    value.__dict__.update(attributes)
    monkeypatch.setitem(sys.modules, name, value)
    return value


@pytest.fixture
def native_case(tmp_path, monkeypatch):
    from shared_platform import worker_category_readonly_cli as transport
    import shared_platform
    root, home, system = (tmp_path / name for name in ('source', 'codex-home', 'program-data'))
    for path in (root, home, system):
        path.mkdir()
    config = home / 'config.toml'
    config.write_text('model="original-model"\nmodel_provider="original-provider"\n'
        'notify=["SYNTHETIC-NOTIFY"]\n[hooks]\nSessionStart="SYNTHETIC-HOOK"\n'
        '[mcp_servers.alpha]\nenabled=true\ncommand="SYNTHETIC-MCP"\n'
        '[mcp_servers.beta]\nenabled=true\n', encoding='utf-8')
    for name in ('CODEX_PROFILE', 'CODEX_CONFIG', 'CODEX_CONFIG_FILE', 'CODEX_MANAGED_CONFIG'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setenv('ProgramData', str(system))
    executable = tmp_path / 'synthetic-codex.exe'
    executable.write_bytes(b'SYNTHETIC-CAPABILITY-BINARY')
    monkeypatch.setenv('ORBIT_OPERATIONS_AGENT_EXECUTABLE', str(executable))
    from shared_platform import native_readonly_invocation as isolation
    monkeypatch.setattr(isolation, '_SUPPORTED_BINARY_SHA256', frozenset({
        hashlib.sha256(executable.read_bytes()).hexdigest()}))
    calls, attempts = [], []
    def run(argv, prompt, **kwargs):
        # A closed fake child interprets only integration overrides. No CLI,
        # auth, database or provider can be reached by this fixture.
        invocation = kwargs.get('invocation')
        if invocation is not None:
            assert type(invocation) is isolation.NativeReadonlyInvocation
            assert invocation.verify_launch(argv, kwargs['cwd']) == dict(os.environ)
        inherited = tomllib.loads(config.read_text(encoding='utf-8'))
        overrides = [argv[i+1] for i, item in enumerate(argv) if item == '--config']
        disabled = [argv[i+1] for i, item in enumerate(argv) if item == '--disable']
        active = [name for name, value in inherited['mcp_servers'].items()
                  if value.get('enabled', True) and f'mcp_servers.{name}.enabled=false' not in overrides]
        calls.append(dict(argv=argv, kwargs=kwargs, active_mcp=active,
            active_hooks='hooks' not in disabled,
            active_notify='notify=[]' not in overrides))
        return transport.ReadonlyCodexResult(None, '', timed_out=True)
    monkeypatch.setattr(transport, 'run_readonly_jsonl', run)
    def reserve(*args, **kwargs):
        attempts.append(kwargs)
        return {'started_this_call': True}
    ledger = lambda: SimpleNamespace(reserve=reserve)
    @contextmanager
    def pin(paths):
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
        yield
    db = SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchone=lambda: None))
    engine = SimpleNamespace(release={'code_version': 'synthetic'}, transaction=lambda: __import__('contextlib').nullcontext(db))
    adapter = SimpleNamespace(engine=engine, worker_id='worker', profile=SimpleNamespace(
        root=root, data_root=tmp_path/'state', environment='stable'),
        boundary=SimpleNamespace(_pin=pin), _context=lambda *args: None)
    task = {'task_id': 'synthetic-task', 'version': engine.release,
            'scope': {'offer_id': 'synthetic-offer', 'shops': ['synthetic-shop']}}
    packet = json.dumps({'offer_id': 'synthetic-offer'}).encode()
    encode = lambda value: json.dumps(value, sort_keys=True).encode()
    read = lambda path: path.read_bytes() if path.exists() else packet
    utility = _module(monkeypatch, 'shared_platform.native_parent_facts', _bytes=encode,
        _read=read, _unique=dict, _nonfinite=lambda v: None, MAX_PROPOSAL_BYTES=131072)
    receipts = _module(monkeypatch, 'shared_platform.native_parent_child_receipt',
        original_attempt=lambda *args: None, retain=lambda *args, **kwargs: {'synthetic': True},
        read_retained=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('not retained')))
    _module(monkeypatch, 'shared_platform.operations_runtime',
        _create_agent_artifact=lambda path, body: path.write_bytes(body),
        _parse_codex_final_jsonl=lambda raw: raw)
    release = _module(monkeypatch, 'shared_platform.release_store', default_release_store=lambda: object())
    workspace = _module(monkeypatch, 'shared_platform.round1_workspace',
        _module=lambda server: SimpleNamespace(__file__=str(root/'synthetic-contract.py')))
    monkeypatch.setattr(shared_platform, 'release_store', release, raising=False)
    monkeypatch.setattr(shared_platform, 'round1_workspace', workspace, raising=False)
    capture = {'capture_request_id': 'synthetic-capture', 'observer_reference': 'synthetic-reference'}
    flow = SimpleNamespace(options=lambda *a, **k: {'status': 'SUCCEEDED', 'request_id': 'synthetic-request',
        'projection': {'options_reference': 'synthetic-options', 'options_digest': 'synthetic-digest'}})
    category = _body('shared_platform/native_parent_category.py', 'ensure_capture',
        dict(Path=Path, hashlib=hashlib, json=json, _context=lambda *a: {},
            WorkerCategoryBridge=lambda *a: object(), ParentR1CategoryFlow=lambda *a, **k: flow,
            CategoryAgentAttemptLedger=ledger))
    _module(monkeypatch, 'shared_platform.native_parent_category',
        ensure_capture=lambda *a, **k: {'status': 'verified', 'capture': capture})
    facts = _body('shared_platform/native_parent_facts.py', 'NativeParentFactsAdapter.execute_facts',
        dict(Path=Path, hashlib=hashlib, json=json, os=os, subprocess=subprocess, SCHEMA='synthetic-schema',
            MAX_PROPOSAL_BYTES=131072, _bytes=encode, _read=read,
            CategoryAgentAttemptLedger=ledger, VerifiedFactsCategoryTransport=lambda *a, **k: object(),
            native=SimpleNamespace(_server=lambda *a: object(), _report_dir=lambda *a: root/'reports')))
    output = adapter.profile.data_root/'artifacts'/task['task_id']/'facts-1'
    return SimpleNamespace(category=category, facts=facts, adapter=adapter, task=task,
        output=output, source=packet, executable=executable, config=config, home=home,
        root=root, system=system, calls=calls, attempts=attempts, receipts=receipts,
        monkeypatch=monkeypatch)


def _invoke(case, caller):
    if caller == 'category':
        return case.category(case.adapter, case.task, lease_token='synthetic-lease', server=object(),
            store=object(), transport=object(), output=case.output, source=case.source,
            notes=[], executable=str(case.executable))
    return case.facts(case.adapter, case.task, case.output, [], lease_token='synthetic-lease')


@pytest.mark.parametrize('caller', ['category', 'facts'])
def test_actual_native_child_does_not_inherit_integrations(native_case, caller):
    result = _invoke(native_case, caller)
    assert len(native_case.calls) == 1, result
    call = native_case.calls[0]
    assert not call['active_mcp'], 'prior actual native argv leaves enabled MCP inherited'
    assert not call['active_hooks'] and not call['active_notify']
    assert '--ephemeral' in call['argv']
    assert result['status'] == 'unknown' and len(native_case.attempts) == 1


@pytest.mark.parametrize('caller', ['category', 'facts'])
@pytest.mark.parametrize('failure', ['unsupported-binary', 'profile', 'selector', 'managed', 'invalid-key'])
def test_native_preflight_blocks_before_original_attempt_or_child(native_case, caller, failure):
    from shared_platform import native_readonly_invocation as isolation
    if failure == 'unsupported-binary':
        native_case.monkeypatch.setattr(isolation, '_SUPPORTED_BINARY_SHA256', frozenset())
    elif failure == 'profile':
        native_case.config.write_text('profile="unknown"\n', encoding='utf-8')
    elif failure == 'selector':
        native_case.monkeypatch.setenv('CODEX_CONFIG_FILE', 'unverified-selector')
    elif failure == 'managed':
        (native_case.home/'requirements.toml').write_text('synthetic=true', encoding='utf-8')
    else:
        native_case.config.write_text('[mcp_servers."bad.name"]\nenabled=true\n', encoding='utf-8')
    result = _invoke(native_case, caller)
    assert result['status'] == 'blocked'
    assert not native_case.calls and not native_case.attempts
    assert not native_case.output.exists()


@pytest.mark.parametrize('caller', ['category', 'facts'])
def test_retained_unknown_ignores_new_config_and_never_relaunches(native_case, caller):
    first = _invoke(native_case, caller)
    assert first['status'] == 'unknown'
    native_case.config.write_text('profile="unsupported-now"\n', encoding='utf-8')
    output = native_case.output/('category-choice' if caller == 'category' else '')
    native_case.receipts.original_attempt = lambda *a: {'output_path': str(output)}
    from shared_platform.worker_category_readonly_cli import ReadonlyCodexResult
    native_case.receipts.read_retained = lambda *a, **k: (
        ReadonlyCodexResult(None, '', timed_out=True), {'original': True})
    second = _invoke(native_case, caller)
    assert second['status'] == 'unknown' and second['child_receipt'] == {'original': True}
    assert len(native_case.calls) == len(native_case.attempts) == 1


def _plan(case):
    from shared_platform.native_readonly_invocation import prepare_readonly_invocation
    schema = case.root/'schema.json'
    schema.write_text('{}', encoding='utf-8')
    plan = prepare_readonly_invocation(str(case.executable), case.root)
    return plan, schema


def test_split_project_mcp_preserves_environment_model_and_provider(native_case):
    local = native_case.root/'.codex'
    local.mkdir()
    (local/'config.toml').write_text('[mcp_servers.project]\nenabled=false\n', encoding='utf-8')
    (native_case.home/'hooks.json').write_bytes(b'SYNTHETIC-HOOK-BODY-NOT-READ')
    (native_case.home/'plugins').mkdir()
    before = native_case.config.read_bytes(), dict(os.environ)
    plan, schema = _plan(native_case)
    argv = plan.argv(schema)
    overrides = [argv[i+1] for i, arg in enumerate(argv) if arg == '--config']
    assert set(overrides) == {'mcp_servers.alpha.enabled=false', 'mcp_servers.beta.enabled=false',
        'mcp_servers.project.enabled=false', 'notify=[]', 'web_search="disabled"'}
    assert plan.verify_launch(argv, native_case.root) == before[1]
    assert native_case.config.read_bytes() == before[0] and dict(os.environ) == before[1]
    assert not any(v in argv for v in ('--model', '--profile', '--ignore-user-config'))
    assert all('model' not in item and 'provider' not in item for item in overrides)
    assert 'SYNTHETIC' not in repr(plan) and 'original-provider' not in repr(plan)


@pytest.mark.parametrize('change', ['config', 'new-project', 'environment', 'binary', 'argv', 'cwd'])
def test_launch_drift_stops_before_popen(native_case, change):
    from shared_platform import worker_category_readonly_cli as transport
    plan, schema = _plan(native_case)
    argv, cwd = plan.argv(schema), native_case.root
    if change == 'config':
        native_case.config.write_text('[mcp_servers.changed]\nenabled=true\n', encoding='utf-8')
    elif change == 'new-project':
        folder = cwd/'.codex'
        folder.mkdir()
        (folder/'config.toml').write_text('[mcp_servers.new]\nenabled=true\n', encoding='utf-8')
    elif change == 'environment':
        native_case.monkeypatch.setenv('CODEX_PROFILE', 'unknown')
    elif change == 'binary':
        native_case.executable.write_bytes(b'CHANGED-SYNTHETIC-BINARY')
    elif change == 'argv':
        argv += ['--model', 'changed-route']
    else:
        cwd = native_case.home
    popens = []
    native_case.monkeypatch.setattr(transport.subprocess, 'Popen', lambda *a, **k: popens.append((a, k)))
    with pytest.raises(ValueError, match='readonly_native_'):
        transport._start_child(argv, cwd, invocation=plan)
    assert not popens


@pytest.mark.parametrize('body', [
    '[mcp_servers."bad name"]\n', '[mcp_servers."bad\\"quote"]\n',
    '[mcp_servers."非ASCII"]\n', '[mcp_servers.ok]\nenabled="true"\n',
    'SYNTHETIC_SECRET_UNCLOSED="never', '[features]\nremote_control=true\n',
])
def test_unsafe_config_is_fixed_reason_only(native_case, body):
    native_case.config.write_text(body, encoding='utf-8')
    with pytest.raises(ValueError) as error:
        _plan(native_case)
    assert str(error.value).startswith('readonly_')
    assert 'SYNTHETIC' not in str(error.value) and 'bad' not in str(error.value)


def test_actual_popen_receives_frozen_original_environment(native_case):
    from shared_platform import worker_category_readonly_cli as transport
    plan, schema = _plan(native_case)
    expected = dict(os.environ)
    calls = []
    class StopSyntheticPopen(RuntimeError):
        pass
    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        raise StopSyntheticPopen('no process started')
    native_case.monkeypatch.setattr(transport.subprocess, 'Popen', popen)
    with pytest.raises(StopSyntheticPopen):
        transport._start_child(plan.argv(schema), native_case.root, invocation=plan)
    assert len(calls) == 1 and calls[0][1]['env'] == expected
    assert calls[0][1]['cwd'] == native_case.root


def test_same_size_and_mtime_content_change_still_blocks(native_case):
    plan, schema = _plan(native_case)
    original = native_case.config.stat()
    raw = native_case.config.read_bytes()
    native_case.config.write_bytes(raw.replace(b'alpha', b'gamma'))
    os.utime(native_case.config, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert native_case.config.stat().st_size == original.st_size
    with pytest.raises(ValueError, match='readonly_native_invocation_changed'):
        plan.verify_launch(plan.argv(schema), native_case.root)


@pytest.mark.parametrize('location', ['home', 'system'])
def test_absent_managed_layer_becomes_present_before_launch(native_case, location):
    plan, schema = _plan(native_case)
    folder = native_case.home if location == 'home' else native_case.system/'OpenAI/Codex'
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'managed_config.toml').write_text('synthetic=true', encoding='utf-8')
    with pytest.raises(ValueError, match='readonly_native_invocation_changed'):
        plan.verify_launch(plan.argv(schema), native_case.root)


def test_no_auth_hook_body_provider_database_or_real_configuration_open(native_case):
    from shared_platform import native_readonly_invocation as isolation
    (native_case.home/'hooks.json').write_bytes(b'SYNTHETIC-NEVER-READ')
    allowed = {native_case.config, native_case.executable, native_case.root/'schema.json'}
    opened = []
    original = Path.open
    def guarded(path, *args, **kwargs):
        assert path in allowed, f'nonfixture content access: {path.name}'
        opened.append(path)
        return original(path, *args, **kwargs)
    native_case.monkeypatch.setattr(Path, 'open', guarded)
    native_case.monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('no CLI allowed'))
    plan, schema = _plan(native_case)
    plan.verify_launch(plan.argv(schema), native_case.root)
    assert native_case.config in opened and native_case.executable in opened
    assert not (native_case.home/'hooks.json') in opened


def test_unresolved_system_config_root_is_not_a_verified_layer(native_case):
    native_case.monkeypatch.setenv('ProgramData', str(native_case.system/'..'/'unverified-system'))
    with pytest.raises(ValueError, match='readonly_agent_system_layer_path_unverified'):
        _plan(native_case)
