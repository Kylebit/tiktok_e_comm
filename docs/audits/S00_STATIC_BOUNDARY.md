# S00-A static resource boundary

Work order: `WO-20260905-ORBIT-S00-A`; exact base:
`b72a8d1d048862da08a9a9bca333425abbed857c`.

The three real HTTP handlers previously joined `/static/` user input directly
to their static directory. An isolated GET of `/static/../sentinel.txt` returned
200 and a generated parent-directory sentinel through all three handlers.

`core/static_files.py` now resolves only the untrusted static URL suffix. The
handlers pass `urlparse(request_target).path` without further decoding or slash
stripping. The helper decodes once, rejects malformed encoding, Windows
separators/drives/streams, absolute paths, empty/dot segments, control characters
and trailing-dot/space aliases. It resolves the root and candidate, uses
`Path.relative_to` to prove containment, and requires an existing regular file.
All rejected, missing and directory requests return 404 through each handler.
Residual percent escapes name literal files; they are never decoded twice.

Changes to each handler are limited to one import and its `/static/` branch.
The existing `_file`, fixed page/report routes, response bytes and normal asset
MIME behavior remain unchanged. Internal links work; resolved links to a sibling
directory with a matching string prefix are rejected.

Validation on Windows / Python 3.12.8 / pytest 8.4.2:

- Baseline: 3 expected failures (each handler exposed the generated sentinel),
  12 passing nested JS/CSS/PNG and fixed-page checks.
- The same regression after repair: 15 passed.
- Expanded resolver/real-handler matrix: 151 passed, 12 skipped.
- Related product-flow proxy, fixed-route and release-safety tests: 41 passed.
- File/directory symbolic links require unavailable Windows privilege, so 12
  cases explicitly skip. Six real junction cases pass (internal and escape
  cases through every handler); no administrator privilege was requested.

Tests import the actual candidate modules and instantiate their real handler
classes in temporary loopback HTTP servers. Only `WEB_DIR`/`STATIC_DIR` are
redirected to generated files; dispatch, resolver and file sending run normally.
The `serve()` entry points are intentionally not invoked because product
startup may initialize platform executors or token refresh. Tests reject
unrelated settings/database/provider calls and close their temporary servers.
This verifies the changed GET path, not full production startup or a deployment.

Static roots must remain controlled by trusted local deployment code: this
minimal path check does not provide descriptor-based atomic protection against
a local actor concurrently replacing directories between resolution and read.
Hard links are not distinguishable by pathname containment. Both are outside
this HTTP traversal work order; no production credentials/files were probed.

Evidence, rerun wrapper and delivery commit are recorded under
`C:/Users/Windows11/Desktop/Agent_PR/outputs/orbithive-audit-20260905/security/`.
Use a fresh phase name when running `run_s00_tests.py`; fixture directories are
retained inside this worktree and are never recursively cleaned for delivery.
Rollback is omission/reversion of this focused commit. Nothing was pushed and
no running service was replaced. Integration and final acceptance remain open.
