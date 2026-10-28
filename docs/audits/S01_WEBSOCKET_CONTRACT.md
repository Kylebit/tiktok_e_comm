# S01-B optional Feishu WebSocket TLS

Work order `WO-20260905-ORBIT-S01-B`; exact base
`3537468bfb9c3f3b963b22c849314ee4ddc60cdd`.

The bot no longer replaces the process-wide `websockets.connect` function or
injects a context with disabled certificate/hostname verification. The private
`_patch_websocket_ssl` helper and its only caller are removed. A read-only search
found no other users of that helper. Normal SDK/underlying library TLS behavior
is retained, with no compatibility fallback that disables verification.

The optional bot's existing contract remains: missing configuration reports
setup guidance and raises; missing/incompatible SDK reports the optional
dependency and exits. Configured construction registers the same message
handler and starts the client. Text command/reply handling, ignored non-text or
empty events, and existing error reply handling remain unchanged.

## Offline evidence

Two new tests failed on the exact base before the production edit: the global
connect function identity changed and the captured context had CERT_NONE.
After removing the helper and call, all 12 paired tests passed without skips.
The tests execute the actual candidate bot and event parser with synthetic
app/config/command/reply modules and a fake SDK. Socket, DNS, and SQLite access
are prohibited. No real messages, SDK connections, business configuration
initialization, or business database accesses occur. The fake client's start
is an observation point, not a running bot or a real TLS handshake.

## Actual dependency inspection

`requirements-hub.txt:1` declares `lark-oapi>=1.4.0`. The existing interpreter
environment has lark-oapi 1.7.1 and websockets 15.0.1. Their source was read
without importing/starting the SDK; no installation was performed.

The installed `lark_oapi/ws/client.py:204` calls `websockets.connect` through
`_ws_connect_kwargs`. Its lines 73–80 currently return `proxy=None` when that
parameter exists, with no SSL override. This records the installed artifact,
not a claim that its content is identical to a published upstream release.
That existing routing behavior was not changed. The installed websockets
asyncio implementation defaults SSL on at line 392 and documents the secure
default context at line 263; its legacy implementation has the equivalent
default at line 474. File hashes and dependency metadata are retained in the
audit evidence. This is not certification of every SDK dependency.

## Static bypass difference and limits

Scan expressions include `CERT_NONE`, `check_hostname=False`, `verify=False`,
and `_create_unverified_context`, allowing whitespace. The scan covers
`core/modules/domains/skills/agent_comms/desktop`; a supplemental tracked text
scan identifies out-of-scope matches without changing them.

| Classification | Before / after | Disposition |
| --- | --- | --- |
| In-scope production | `modules/hub/feishu_bot.py:20–21` / none | Removed in this package |
| Root CLI outside the six roots | `tiktok_auth.py:28–29` remains | Separate unresolved TLS bypass; reported for later ownership |
| Deliberate negative fixture | `tests/test_http_retry_contract.py:191,193` remains | Verifies rejection of insecure contexts; not a production caller |
| Documentation | S01 HTTP audit and this audit | Describes findings; not executable bypass |
| Archive/vendor | No matching tracked files in the supplemental scan | Untracked/ignored archives and installed dependency trees are not exhaustively audited |

No push, old-source edit, running-service replacement, global package change,
provider traffic, real message or paid operation occurred. S01-A's HTTP helper
was not edited or retested unnecessarily. The A/B candidates can enter the next
isolated integration; this package does not activate or certify a live bot.

Commands, raw red/green logs, JUnit, static/dependency inventories and receipt
are under `outputs/orbithive-audit-20260905/security/websocket-validation` and
`security/S01_B_RECEIPT.md/json`. Rollback is omitting/reverting this focused
commit; that would restore the known global TLS bypass.
