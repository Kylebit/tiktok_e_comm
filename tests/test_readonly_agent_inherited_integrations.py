"""Synthetic inherited configuration only; the CLI spawn is always replaced."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared_platform import operations_runtime as runtime


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path/'source'
    root.mkdir()
    home = tmp_path/'codex-home'
    home.mkdir()
    user = tmp_path/'user'
    user.mkdir()
    system = tmp_path/'program-data'
    system.mkdir()
    config = home/'config.toml'
    config.write_text('model = "gpt-6.1-sol"\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setenv('HOME', str(user))
    monkeypatch.setenv('USERPROFILE', str(user))
    monkeypatch.setenv('ProgramData', str(system))
    for name in ('CODEX_PROFILE','CODEX_CONFIG','CODEX_CONFIG_FILE','CODEX_MANAGED_CONFIG'):
        monkeypatch.delenv(name, raising=False)
    output = tmp_path/'data'/'attempt'
    bridge = runtime.ControlledAgentBridge('synthetic-codex', runtime.RuntimeProfile(
        root, tmp_path/'data', 'preview', 'a'*40))
    calls = []
    def spawn(argv, **kwargs):
        calls.append({'argv':argv,'kwargs':{key:value for key,value in kwargs.items() if key!='env'},
                      'environment_matches':kwargs.get('env')==dict(os.environ)})
        result = Path(argv[argv.index('--output-last-message')+1])
        result.write_text(json.dumps({'summary':'synthetic preparation','missing_inputs':[],
                                      'evidence_paths':[]}), encoding='utf-8')
        return SimpleNamespace(returncode=0,stdout='{"type":"thread.started","thread_id":"synthetic-session"}\n',stderr='')
    monkeypatch.setattr(runtime.subprocess,'run',spawn)
    return SimpleNamespace(root=root,home=home,user=user,system=system,config=config,output=output,
                           bridge=bridge,calls=calls,monkeypatch=monkeypatch)


@pytest.mark.parametrize(('body','reason'), [
    ('[mcp_servers.synthetic]\ncommand = "synthetic-only"\nenabled = true\n', 'readonly_agent_enabled_mcp'),
    ('[[hooks.SessionStart]]\nmatcher = "startup"\n[[hooks.SessionStart.hooks]]\ntype = "command"\ncommand = "synthetic-only"\n', 'readonly_agent_hooks_configured'),
    ('notify = ["synthetic-only"]\n', 'readonly_agent_notify_configured'),
], ids=['enabled-mcp','inline-hooks','notify'])
def test_risky_config_blocks_before_artifacts_and_spawn(case, body, reason):
    case.config.write_text(body, encoding='utf-8')
    result = case.bridge.prepare({'template':'synthetic','scope':{}}, case.output)
    # The unmodified consumer actually reaches this fake spawn, writes schema
    # and result, and returns prepared; this is the original behavioral RED.
    assert not case.calls, 'original consumer spawned despite inherited integration risk'
    assert not case.output.exists(), 'guard must precede directory/schema/result writes'
    assert result == {'status':'blocked','reason':reason}


@pytest.mark.parametrize('body', [
    '',
    'model = "gpt-6.1-sol"\nmodel_provider = "openai"\nnotify = []\n'
    '[hooks]\n[mcp_servers.synthetic]\nenabled = false\ncommand = "synthetic-only"\n',
], ids=['empty-minimal','disabled-mcp-and-empty-integrations'])
def test_locally_clean_config_preserves_prepare_and_route(case, body):
    case.config.write_text(body, encoding='utf-8')
    before = dict(os.environ)
    raw = case.config.read_bytes()
    result = case.bridge.prepare({'template':'synthetic','scope':{}}, case.output)
    assert result['status']=='prepared' and result['result']['summary']=='synthetic preparation'
    assert len(case.calls)==1 and case.calls[0]['environment_matches'] is True
    environment_unchanged=dict(os.environ)==before
    assert environment_unchanged and case.config.read_bytes()==raw
    argv=case.calls[0]['argv']
    assert argv[:4]==['synthetic-codex','exec','--sandbox','read-only']
    assert not any(flag in argv for flag in ('--ignore-user-config','--ignore-rules','--model','--profile'))
    prompt=case.calls[0]['kwargs']['input']
    assert 'No credential refresh, paid calls, commerce writes, file modifications, or child agents.' in prompt
    assert set(result)=={'status','result','session_id','sha256','path'}


@pytest.mark.parametrize(('body','reason'), [
    (None,'readonly_agent_user_config_missing'),
    ('model = "SYNTHETIC_SECRET_NOT_A_REAL_KEY\n','readonly_agent_config_parse_failed'),
], ids=['missing-user-config','malformed-toml-redacted'])
def test_missing_or_invalid_user_config_has_no_artifacts(case, body, reason):
    if body is None:
        # Simulate an absent leaf without deleting any fixture.
        lstat=Path.lstat
        def absent(path):
            if path==case.config: raise FileNotFoundError()
            return lstat(path)
        case.monkeypatch.setattr(Path,'lstat',absent)
    else:
        case.config.write_text(body,encoding='utf-8')
    result=case.bridge.prepare({},case.output)
    assert result=={'status':'blocked','reason':reason}
    assert not case.calls and not case.output.exists()
    assert 'SYNTHETIC_SECRET' not in str(result)


def test_unsafe_leaf_metadata_blocks_before_config_read(case):
    from shared_platform.readonly_agent_config_guard import ReadonlyAgentConfigBlocked, _path_metadata
    lstat=Path.lstat
    original=case.config.lstat()
    attrs={name:getattr(original,name) for name in ('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_nlink')}
    remote_attempts=[]
    def no_remote_metadata(path):
        if str(path).startswith('\\\\'):
            remote_attempts.append('synthetic UNC metadata boundary')
            raise OSError('synthetic network access would have been attempted')
        return lstat(path)
    with case.monkeypatch.context() as patch:
        patch.setattr(Path,'lstat',no_remote_metadata)
        with pytest.raises(ReadonlyAgentConfigBlocked,match='readonly_agent_config_path_unsafe'):
            _path_metadata(Path('//synthetic-host/share/config.toml'))
        assert not remote_attempts
    # Simulated reparse attributes; no link, junction, ACL or filesystem change.
    def unsafe(path):
        return SimpleNamespace(**attrs,st_file_attributes=0x400) if path==case.config else lstat(path)
    case.monkeypatch.setattr(Path,'lstat',unsafe)
    result=case.bridge.prepare({},case.output)
    assert result=={'status':'blocked','reason':'readonly_agent_config_path_unsafe'}
    assert not case.calls and not case.output.exists()


def test_project_layer_mcp_is_checked_even_when_user_is_clean(case):
    folder=case.root/'.codex'
    folder.mkdir()
    (folder/'config.toml').write_text('[mcp_servers.synthetic]\ncommand="synthetic-only"\n',encoding='utf-8')
    assert case.bridge.prepare({},case.output)=={'status':'blocked','reason':'readonly_agent_enabled_mcp'}
    assert not case.calls and not case.output.exists()


def test_profile_selector_is_not_silently_skipped(case):
    case.config.write_text('profile="synthetic-profile"\n',encoding='utf-8')
    assert case.bridge.prepare({},case.output)=={'status':'blocked','reason':'readonly_agent_profile_layer_unverified'}
    assert not case.calls and not case.output.exists()


def test_concrete_managed_and_plugin_inputs_are_not_claimed_clean(case):
    folder=case.system/'OpenAI/Codex'
    folder.mkdir(parents=True)
    (folder/'requirements.toml').write_text('[hooks]\n',encoding='utf-8')
    assert case.bridge.prepare({},case.output)=={'status':'blocked','reason':'readonly_agent_managed_layer_unverified'}
    case.config.write_text('[plugins.synthetic]\nenabled=true\n',encoding='utf-8')
    assert case.bridge.prepare({},case.output)=={'status':'blocked','reason':'readonly_agent_plugin_or_app_layer_unverified'}
    assert not case.calls and not case.output.exists()


def test_standalone_hook_file_does_not_hide_behind_empty_toml(case):
    (case.home/'hooks.json').write_text('{"hooks":{"SessionStart":[]}}',encoding='utf-8')
    assert case.bridge.prepare({},case.output)=={'status':'blocked','reason':'readonly_agent_hooks_file_unverified'}
    assert not case.calls and not case.output.exists()


def test_config_drift_during_output_resolution_blocks_before_mkdir(case):
    resolve=Path.resolve
    def mutate(path,*args,**kwargs):
        if path==case.output:
            case.config.write_text('notify=["synthetic-drift"]\n',encoding='utf-8')
        return resolve(path,*args,**kwargs)
    case.monkeypatch.setattr(Path,'resolve',mutate)
    assert case.bridge.prepare({},case.output)=={'status':'blocked','reason':'readonly_agent_config_changed'}
    assert not case.calls and not case.output.exists()


def test_environment_binding_drift_blocks_without_reading_new_home(case):
    resolve=Path.resolve
    def mutate(path,*args,**kwargs):
        if path==case.output:
            case.monkeypatch.setenv('CODEX_HOME',str(case.user/'not-opened'))
        return resolve(path,*args,**kwargs)
    case.monkeypatch.setattr(Path,'resolve',mutate)
    assert case.bridge.prepare({},case.output)=={'status':'blocked','reason':'readonly_agent_environment_changed'}
    assert not case.calls and not case.output.exists()


def test_clean_timeout_keeps_original_unknown_and_no_retry(case):
    def timeout(argv,**kwargs):
        case.calls.append({'argv':argv,'kwargs':{key:value for key,value in kwargs.items() if key!='env'}})
        raise runtime.subprocess.TimeoutExpired(argv,kwargs['timeout'],
            output=b'{"type":"thread.started","thread_id":"synthetic-timeout"}\n')
    case.monkeypatch.setattr(runtime.subprocess,'run',timeout)
    result=case.bridge.prepare({},case.output,timeout=7)
    assert result=={'status':'unknown','reason':'agent_timeout_reconcile_session_before_retry',
                    'session_id':'synthetic-timeout'}
    assert len(case.calls)==1 and case.calls[0]['kwargs']['timeout']==7
