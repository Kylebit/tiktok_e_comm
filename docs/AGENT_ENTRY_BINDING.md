# Explicit Agent CLI binding

Agents may invoke `<SOURCE>/scripts/repo_bound_agent_entry.py` from any working directory
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
no general data/output-root or workdir argument in this source revision. Root/workdir arguments passed
after `--` are rejected by the binding entry, including `--repo-root`.
Abbreviations that could select root/workdir/technical binding flags are rejected;
the wrapper and R1 parsers disable argparse abbreviation.

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

## Version 2: R1 existing captured inputs

`config/agent_entry.r1-v2.example.json` describes `orbit-agent-entry/v2`.
Only `--entry preparation` accepts v2. Images, QA and delist return
`ENTRY_PATH_BINDING_UNSUPPORTED_V2`; use v1 only when its original layout and
authority contract actually hold. A whole mixed-root Agent workflow is still
unsupported. The selected R1 script and its four source consumers must declare
`R1_PATH_BINDING_CONTRACT = "orbit-r1-paths/v2"`; AST checks these constants and
Git verifies the files are tracked under the exact clean HEAD. Older c4/16d
sources cannot claim this propagation merely by selecting an exact HEAD.

| Field | R1 v2 propagation |
| --- | --- |
| `source_root`, `expected_source_head` | Exact complete clean source, original R1 CLI; same identity contract as v1 |
| `config_root`, `settings_path` | Independent existing config directory containing selected settings; `core.config` selection/cache guards retained |
| `data_root` | Independent captured sourcing JSON and manual-intake records |
| `state_dir` | Independent existing workbench JSON and local SKU reservations |
| `source_outputs_root` | Explicit existing source capture directory for sea previews/common captures; no cwd or second data-root inference |
| `content_outputs_root` | Explicit existing `image_suite_from_miaoshou` directory for content metadata |
| `output_root` | Existing report directory; packet and default category/image/candidate sidecars use `product-preparation/<offer>` through `publication_rounds.report_dir` |
| `catalog_database` | Required existing catalog path used by dashboard |
| `release_store_path` | Required existing release lineage/reservation and captured category observation database |
| `report_store_path` | Required existing report history database, independently passed to weekly summary |
| `workbench_store_path` | Optional existing path fixed through its established fresh-process environment contract; not consumed by this R1 dashboard and not a readiness requirement |
| `lingshi_config_path` | Optional existing file, metadata discovery only; no configuration contents loaded or provider call, and no image/QA propagation |

`config_root` is not a claim that every provider/auth configuration consumer has
been redirected. Check-binding reads no settings contents or business DB. Its
`field_propagation` reports the supported scope; optional discovery fields are
explicitly marked unconsumed. No directories or databases are created to satisfy
the check. Supplying a nonexistent optional workbench path fails; omitting it
does not create one or assert that the formal workbench exists.

Dispatch passes a SHA-256 frozen profile to the original R1 CLI, which rechecks
the digest, exact source and environment before using scoped paths. These hidden
technical flags cannot be supplied after the wrapper's `--`. The child fixes
existing release/report/workbench overrides to profile paths; inherited values
must already match them, while other unsupported path/profile overrides fail.
Settings/catalog use the explicit profile selection as in v1. `--output` stays
inside the bound report root. Business plan/target arguments retain their original
meaning and authorization.

R1 v2 requires an existing per-offer captured JSON state. If it is missing, the
CLI returns `R1_CAPTURED_STATE_MISSING` and never imports server/bootstrap code.
It reads only existing captures through scoped consumers; it does not collect
upstream data, mutate global ROOT, generate images, write marketplace state or
initialize workbench/release/report stores. Missing commercial facts keep the
existing candidate blockers. Later technical freeze, R2/R3 snapshots, paid
ledgers and provider readiness are outside this propagation package.

Current formal 16d is unchanged and lacks these source capabilities. Its missing
workbench remains missing. The metadata-only support matrix in the work package
receipt records formal layout gaps and follow-up consumers; source tests do not
make formal dispatch or a provider result complete.
