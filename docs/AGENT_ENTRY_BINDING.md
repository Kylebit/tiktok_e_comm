# Explicit Agent CLI binding

Agents may invoke `scripts/repo_bound_agent_entry.py` from any working directory
using an absolute `--profile` path and one of `preparation`, `images`, `qa`, or
`delist`. The script uses the selected complete Git root's original CLI. A
physical copy under a personal Skill directory is not a repository source.
The selected source must declare `SETTINGS_BINDING_CONTRACT =
"orbit-settings-binding/v1"` in `core/config.py`; the checker verifies this
top-level constant using AST without importing it. An exact older source HEAD
does not prove explicit settings support and is rejected without that capability.

Start from `config/agent_entry.example.json` and replace every placeholder with
current, independently verified paths and the exact 40-character source HEAD.
Keep the private profile outside the source tree. The fields are:

| Field | Version 1 contract |
| --- | --- |
| `schema` | `orbit-agent-entry/v1` |
| `source_root` | Absolute complete Git top-level, exact HEAD, clean status including untracked files; no link/reparse roots |
| `expected_source_head` | Exact source commit; a role or old checkout name cannot substitute |
| `settings_path` | Existing absolute settings file; explicit selection overrides source settings and ambient `ORBIT_HIVE_SETTINGS` |
| `config_root` | Existing `source_root/config`, used by current source-relative image/provider configuration consumers |
| `data_root` | Existing `source_root/data`; independent data roots are unsupported in v1 |
| `output_root` | Existing `source_root/reports`; independent report roots are unsupported in v1 |
| `catalog_database` | Existing absolute catalog file, required for `delist`, optional for other entries; passed as `ORBIT_CATALOG_DATABASE` |

`settings_path` can live outside `config_root`: token/settings consumers use its
directory while existing image configuration consumers still use
`config_root`. Binding does not assert either configuration is business-ready.
Missing paths and unsupported layouts fail before importing domains. The checker
reads the profile, source `core/config.py` for AST capability checking, and Git
metadata, checks file/directory metadata, and does
not read settings contents, open a business database, call providers, create
directories, or start a service. It validates layout/identity only; no successful
business dispatch or provider result is implied.

```powershell
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry preparation --check-binding
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry preparation -- --offer-id <OFFER_ID> --targets <EXACT_TARGETS>
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry images -- --offer-id <OFFER_ID>
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry qa -- --offer-id <OFFER_ID> --assessment <EXISTING_ASSESSMENT>
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry delist -- plan --sku <EXACT_SKU> --no-live
```

Always check binding first and pass original CLI arguments after `--`. Dispatch
rechecks the same source/profile and starts a fresh isolated Python process in
`source_root`; it passes arguments unchanged and adds no targets, prices, paid
flags, or approval. The original Skill's authority and validation still apply.
Relative original arguments resolve from the selected source root; use absolute
input paths for plans/policies. R1's existing `--output` may select a single report
file within the bound `output_root` but does not redirect workbench state,
snapshots, image reports, or paid ledgers. The original standalone R1 CLI can
select an independent single-file output; v1 rejects that path. R2/QA/delist have
no general data/output-root or workdir argument. Root/workdir arguments passed
after `--` are rejected by the binding entry, including `--repo-root`.

The child fixes `ORBIT_HIVE_SETTINGS` and `ORBIT_CATALOG_DATABASE` from the profile
and discards ambient `GIT_*` checkout overrides. Existing path overrides must
match their source defaults:

| Environment variable | Required v1 value when present |
| --- | --- |
| `ORBIT_WORKBENCH_STORE_PATH` | `source_root/data/orbit_workbench.db` |
| `ORBIT_RELEASE_STORE_PATH`, `ORBIT_REPORT_STORE_PATH` | `source_root/data/orbit_platform.db` |
| `ORBIT_R3_CONFIG_ROOT`, `ORBIT_R2_REVIEW_RUNTIME_ROOT`, `ORBIT_RELEASE_EVIDENCE_ROOT`, `TIKTOK_E_COMM_ROOT` | `source_root` |
| `ORBIT_R3_POLICY_PATH` | `config/product_publication_autopilot_policy.json` |
| `ORBIT_R3_INCIDENT_REGISTRY_PATH` | `skills/publish-approved-product/references/incident-registry.json` |

Other nonempty `ORBIT_*` root/path/database/profile overrides (including operations
data/profile, history, source identity and pinned credentials/evidence paths)
are unsupported and rejected. This preserves a conflicting authority/evidence
selection instead of silently clearing it. Their absence does not prove domain
facts or guard/provider readiness; those remain the original entry's checks.

`core.config` evaluates `ORBIT_HIVE_SETTINGS` when selecting settings, before
source defaults. An invalid explicit path fails without fallback. A loaded cache
cannot be silently relabelled after an explicit source change: use a fresh
process. With no explicit selection it uses this source's settings; no historical
C-workspace fallback is shipped. Existing profit pins and Supply routing are
separate contracts and remain unchanged.

Current formal mixed-root deployment has independent configuration, catalog,
release/report/workbench and evidence roots. This version does not redirect those
consumers and must not be described as making the whole Agent workflow usable.
The metadata-only entry matrix in the work package receipt identifies remaining
consumer changes for a bounded follow-up.
