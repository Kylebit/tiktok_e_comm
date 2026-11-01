"""A known ignored non-authority key is a diagnostic, never a failed turn."""
import json
import pytest
from shared_platform.operations_runtime import _parse_codex_final_jsonl

MESSAGE=('Codex is ignoring 1 unrecognized configuration setting. Check for typos or deprecated settings.\n'
         '  user (C:\\Users\\OwnedFixture\\.codex\\config.toml): `computer_use.windows.always_allowed_app_ids` is ignored.')


def _events(message=MESSAGE, *, count=2, placement='prefix', item_extra=None):
    item={'id':'item_0','type':'error','message':message,**(item_extra or {})}
    warning={'type':'item.completed','item':item}
    rows=[{'type':'thread.started','thread_id':'owned-retained-choice'}]
    if placement=='prefix':rows.extend([warning]*count)
    rows.append({'type':'turn.started'})
    if placement=='in-turn':rows.append(warning)
    rows.extend([{'type':'item.completed','item':{'id':'final','type':'agent_message',
        'text':json.dumps({'selection':'owned-selection','missing_inputs':[]})}}, {'type':'turn.completed','usage':{}}])
    if placement=='after':rows.append(warning)
    return '\n'.join(json.dumps(r) for r in rows)+'\n'


def test_known_two_prefix_diagnostics_preserve_original_completed_choice():
    assert json.loads(_parse_codex_final_jsonl(_events()))=={'selection':'owned-selection','missing_inputs':[]}


@pytest.mark.parametrize('kwargs',[
    {'message':MESSAGE.replace('computer_use.windows.always_allowed_app_ids','mcp_servers.tool.enabled')},
    {'message':MESSAGE+'\nAdditional failure'}, {'message':'Fatal child error'},
    {'count':3}, {'placement':'in-turn'}, {'placement':'after'}, {'item_extra':{'fatal':True}},
])
def test_arbitrary_or_misplaced_error_is_still_rejected(kwargs):
    with pytest.raises(ValueError):_parse_codex_final_jsonl(_events(**kwargs))


@pytest.mark.parametrize('kind',['turn.failed','error'])
def test_known_prefix_does_not_hide_real_failure(kind):
    raw=_events().replace('{"type": "turn.completed", "usage": {}}',json.dumps({'type':kind,'message':'failed'}))
    with pytest.raises(ValueError):_parse_codex_final_jsonl(raw)
