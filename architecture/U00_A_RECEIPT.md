# U00-A — Unified entry and runtime identity

Base: 429768b52699cdbe5578d00b06bb0e6b09a35d86. Delivery branch: codex/orbithive-u00-entry-20260905. This is an isolated candidate; production services have not been replaced. The external audit receipt records the final commit and machine evidence.

The existing registry and homepage now expose seven regions, the 29 stable initial capability IDs, and 12 tool/Skill entries. These 29 are an initial audit subset, not the system's capability limit. A six-step relationship strip explains how facts, content, publication, operations, review and knowledge connect without implying execution progress. Existing aliases, business pages, task notifications and report collections remain reachable. Only tasks/audit views read their existing respective collections; homepage load reads navigation and health only. CLI/Skill methods are copied, never executed from the directory. Incomplete advertising, affiliate invitation, Shadow, desktop and R2/content integrations stay explicit.

The main handler serves the unchanged supply dashboard through /supply-chain/ and /supply-chain/inbound-batches.html. The S00 resolver and public asset allowlist reject traversal. Existing CSP remains in force. The 13-text portable manifest and 224 image files are unchanged. Inventory is a 2026-09-02 historical snapshot. Existing browser overrides at the old origin remain there; this delivery does not migrate or clear them.

Each of the three service modules captures code root, Git commit, explicit entry-file build digest, assets and non-secret runtime profile when it loads, before its first health request. The launcher independently derives the expected identity from its selected candidate. A changed source tree or commit cannot become the identity of an already loaded service. Hashing is restricted to listed entry/assets files and a 224-image inventory; unchanged files use a bounded cache. No recursive repository scan runs per health request.

core/config.py adds only cache-source path and size/mtime metadata when existing load_settings succeeds. Health never calls load_settings, opens a database or reads credentials. It uses the known loaded source and cached non-secret database/Ozon path fields. Unattributed caches are UNKNOWN; source selection or metadata changes are CONFIG_MISMATCH. Default report/release paths and the actual WorkbenchStore constructor default are distinguished from the catalog and Ozon stores.

Supply and homepage assets can be browsed while business dependencies remain unverified. READY means the local service identity matches an explicit expectation, not successful business execution or live provider readiness. R2 and content tool entries explicitly await integration. Feishu remains optional. The desktop webview/container is outside this package.

## Explicit runtime profile

Use an operator-created non-secret JSON file with profile_id, settings_path and stores keys catalog, reports_release, workbench and ozon. Paths must describe the actual configuration/store selection; the profile does not redirect business stores or supply credentials. Select it with ORBIT_RUNTIME_PROFILE or launcher --profile. No profile or no attributed initialized configuration stays UNKNOWN.

Existing launcher parameters remain supported. --status remains read-only; --include-rus additionally checks the Ozon service. --start refuses every state except stopped or matching, and never stops/replaces another process. A failed recheck removes the prior Ozon navigation link.

## Verification and limits

The audit includes the original three missing-directory/supply/weak-health red tests, the mobile computed-style red/green journey, three stale-code/config red/green cases, five real subprocess HTTP scenarios with generated code/config/Git fixtures, and real handlers for all three services. The process harness loads only generated configuration; the handler/browser fixture uses explicitly attributed synthetic caches and forbids initialization/provider/DB calls. They are distinct evidence layers, not production validation.

The browser suite uses actual Chrome through the existing local Playwright runtime at 1440x1000 and 390x844. It tests real visible navigation, the mobile menu-to-supply journey, tool method copying, identity positive/negative states, notification/report loading/existing/empty/error states, literal XSS content, and stale Ozon links. The supply browser check verifies 92 currently rendered images load with no console errors or external requests. No dependencies were installed.

Run the focused Python tests with bytecode/cache disabled and an explicit temporary fixture directory: test_unified_entry.py, test_runtime_identity_process.py, test_product_publication_runtime.py, test_orbit_v1_navigation.py, the S00 static tests, product publication legacy-retirement test, and test_unified_entry_browser.py. The browser runner accepts ORBIT_NODE_BIN and ORBIT_NODE_MODULES; this Windows environment uses its installed Chrome channel. Machine JUnit/browser records and screenshots are under the external audit architecture/u00-evidence directory.

No production configuration, business DB, credentials, paid provider, platform write, notification, deployment, installed Skill change, old-source edit or Git push was performed. Only generated fixture processes may be restarted. Future combination must rebuild the startup identity and independently recheck the resulting candidate.
