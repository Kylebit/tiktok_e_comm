# Explicit Agent CLI binding

Agents may invoke `<SOURCE>/scripts/repo_bound_agent_entry.py` from any working directory
using an absolute `--profile` path and one of `preparation`, `images`, `qa`, or
`delist`. The script uses the selected complete Git root's original CLI. A
physical copy under a personal Skill directory is not a repository source.
For v1 and R1 v2, the selected source must declare `SETTINGS_BINDING_CONTRACT =
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
The R1 profile accepts only `--entry preparation`. QA additionally accepts the
separate existing-assessment profile described below. Captured images status
has its own narrow profile; delist and other
QA modes return `ENTRY_PATH_BINDING_UNSUPPORTED_V2`; use v1 only when its original layout and
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

## Version 2: QA with an existing captured assessment

Start from `config/agent_entry.qa-assessment-v2.example.json`. This mode consumes
existing JSON inputs and writes local QA evidence. It does not instantiate a
Lingshi client, initialize a paid context, upload assets or perform marketplace
actions. It is not a readonly operation: the original producer phase lock,
normalized assessment, signed receipt and any superseded signed receipt archive
are local writes. Neither an existing assessment nor its path grants new authority.

| Field | QA existing-assessment propagation |
| --- | --- |
| `schema`, `entry_mode` | `orbit-agent-entry/v2`, `qa-existing-assessment`; no other QA mode |
| `source_root`, `expected_source_head` | Exact complete clean QA Git source; the checker and QA consumer must declare `QA_ASSESSMENT_BINDING_CONTRACT = "orbit-qa-assessment-paths/v2"` |
| `state_dir` | Existing captured workbench JSON; `<offer>.json` must already exist |
| `round1_reports_root` | Retained R1 input root; existing snapshot and state identity checks remain |
| `r2_reports_root` | Existing generation and optional translation reports; never inferred from QA output or cwd |
| `assessment_path` | Existing absolute captured assessment; argv must select exactly this file |
| `qa_output_root` | Existing root; normalized assessment, signed receipt and signed attempt archives use `<offer>/` beneath it |
| `phase_lock_root` | Original producer round2-phase lock root, also using `<offer>/`; independent QA output does not move this lock |
| `r2_producer_mode` | Only `direct-cli-source-reports`; native/custom runtime producers are unsupported |
| `r2_producer_source_root`, `expected_r2_producer_head` | Exact clean original direct producer Git source; metadata and AST verify its source-relative report and lock expression |

Both `r2_reports_root` and `phase_lock_root` must equal
`r2_producer_source_root/reports/product-preparation`. The original producer's
`REPO_ROOT`, `_runtime_root(None)`, report directory and `round2-phase` lock
expressions are statically checked without importing it. An unsupported source,
HEAD, mapping or moved lock is BLOCKED. This proves the original direct CLI code
path and declared roots; it does **not** establish native/custom runtime mappings,
actual captured report provenance or current actor/producer state. Real inputs
and those facts require their own existing evidence; this package does not infer
them from the profile. No extra human business approval is introduced.

The checker reads only the technical profile, source AST/Git metadata and path
metadata. It requires no settings/config/catalog/release/report/workbench database
because this subset does not consume them; these fields and
`master_qa_reports_root` are rejected in this profile and reported unconsumed.
The existing-assessment branch does not call the master-QA reader. Provider/localized
QA still needs its separately bound original master evidence and paid history.

Run `--entry qa --check-binding` first, then
`--entry qa -- --offer-id <ID> --assessment <EXACT_ABSOLUTE_ASSESSMENT>`.
Only those canonical full flags are accepted; abbreviations, alternate root/profile,
model, paid-policy, upload and rework flags reject. Nonempty ambient ORBIT path,
profile, database or settings overrides and `LINGSHI_IMAGE_QA_MODEL` reject rather
than silently selecting another source. The child rechecks the frozen profile
digest, selected source and original producer mapping before inputs and again
under the original phase lock. Missing captured state/R1/generation/assessment
stops before a lock/output write. Link/reparse input and output locations reject.
The QA output root cannot overlap captured state/R1/R2 input roots or contain the
assessment input. Existing phase-lock files and output archive paths also reject
links/reparse aliases.
Successful dispatch returns an absolute QA report path; output archives stay in
the QA output tree. Existing QA business checks and native paid/service guards
are preserved. Tests use synthetic source provenance and poison side-effect
consumers; they do not prove formal inputs, deployment or a real QA result.

## Version 2: existing captured images status

Use `config/agent_entry.images-status-v2.example.json` with
`entry_mode=images-captured-status`. This mode projects existing local R2 status
to stdout and can create/open the original producer phase-lock file. It is a
local-write operation, not a read-only task or provider reconciliation.

| Field | Captured status consumption |
| --- | --- |
| `source_root`, `expected_source_head` | Exact complete clean images source; wrapper and consumer declare `IMAGES_STATUS_BINDING_CONTRACT = "orbit-images-status-paths/v2"` |
| `state_dir` | Existing `<offer>.json`; explicit `load_state(state_dir=...)`, no bootstrap |
| `round1_reports_root` | Retained `<offer>/round1-approved-snapshot.json`; existing R1 digest/freeze validation |
| `r2_reports_root` | Required existing `<offer>/brand-image-generation.json` and optional `brand-image-translation.json`; report-only status projection |
| `phase_lock_root` | Original direct CLI `round2-phase` lock; never relocated to source/cwd/output |
| `r2_producer_mode`, `r2_producer_source_root`, `expected_r2_producer_head` | Only exact clean original `direct-cli-source-reports` producer; the unchanged QA AST proof checks its original source-relative lock expression |

R2 input and lock roots must both be
`r2_producer_source_root/reports/product-preparation`, just as for QA. This proves
the code path and declared source/root relationship only; actual captured lineage,
current producer/actor state and native/custom runtime mappings are not established.
Native/custom producer profiles and unproven mappings reject. Existing paid/native
guards and original direct producer expressions are preserved.

Check binding first, then dispatch only
`--entry images -- --offer-id <ID>` (canonical `--offer-id=<ID>` also works).
Abbreviations, duplicate offer flags, root/profile/mode overrides, paid/model,
generation, translation scope, retry, rework, upload and round3 flags reject.
The internal frozen-profile route also checks its canonical argv. Nonempty
ambient ORBIT path/root/dir/database/profile/settings overrides and
`TIKTOK_E_COMM_ROOT` reject. Source and frozen profile digest are checked before
capture reads and again under the original phase lock.
The original unbound direct/paid/native parser retains its historical abbreviation
behavior; only this frozen captured-status route enforces canonical flags.

The checker is stdlib/Git/path metadata only, without domain import, business
DB, provider or paid calls. This subset does not require or accept settings,
config/data/output roots, catalog/release/report/workbench stores, Lingshi config,
assessment/QA output, master-QA, checkpoint or history roots. It does not read
checkpoint contents, paid ledger, localized review/pack history, asset bytes or
embedded report references. In particular, an UNKNOWN report stays UNKNOWN;
no historical count is reset and no provider result is inferred.

Per-offer state/R1/generation captures must exist and validate before a lock
write. An optional translation report, when present, must be a valid local
object with status. Links/reparse capture and lock aliases reject. Under-lock
drift can leave the already acquired persistent lock file; it stops the result
without writing reports or initializing history. Existing legacy R1 plans retain
the `LEGACY_R2_BRIDGE_REQUIRED` status; this mode does not perform that bridge.
Synthetic validation proves path consumption and boundaries only; real input
availability, capture provenance, deployed Agent use and provider state remain unverified.
