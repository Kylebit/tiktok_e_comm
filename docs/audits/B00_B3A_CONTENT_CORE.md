# B00-B3A content core integration

Work order: `WO-20260905-ORBIT-B00-B3A`; implementation owner: 02 content core.
Base: `46583c401eccd7a639ebd8f1401e133d1ef56433`.
Branch: `codex/orbithive-b3-content-core-20260905`.
This delivers a locally importable, fixture-tested core. It does not activate
paid execution, change approval policy, install a Skill, or wire a server route.

## Source selection and actual baseline gap

All four production additions and the four paired test files were absent from
the exact base. Before adding production files, the paired suite produced four
real collection errors for the missing Lingshi modules. The already present
planning, review, and handoff files were not removed to create artificial red.

Production source selection (Git blob IDs use LF-normalized Git content):

| Path under `modules/sourcing/` | Retained source blob | Decision |
| --- | --- | --- |
| `brand_image_lingshi_generation.py` | `0369388502d5e787e629af090699652aa450127d` | Exact reviewed working source from r4 restored main |
| `lingshi_client.py` | `de5bac19ca9f0469dc963bb0d64c8bb1b04343cb` | Reviewed working source plus the bounded pure payload correction below |
| `localized_image_lingshi_generation.py` | `e5632b2fcab9e80f3f35437adaffbc2add175c75` | Exact reviewed working source from r4 restored main |
| `brand_image_prompt.py` | `5406e37784df5c7cc3ab6d397e7414314dc8cbcd` | Required pure dependency, r4 preserved untracked source; imports only `__future__` |

The recovery root is
`outputs/orbithive-baseline-recovery-20260905-fixed-00-r4`; the three modules and
paired tests come from `restore-check/main/`. The prompt comes from
`private/formal-untracked/modules/sourcing/brand_image_prompt.py`, indexed in
`public/formal-untracked-manifest.json`. Live D source comparisons matched the
three registered working blobs, but r4 preservation was the implementation
input. No credential, business database, or unrelated backup payload was used.

Direct paired test source blobs:

| Test file | Source blob |
| --- | --- |
| `test_brand_image_lingshi_generation.py` | `3a7dd13b8b5b3615736cd08c248ab13d38b3d592` |
| `test_lingshi_client.py` | `4b4e6392b96724e67c64ccbd481cc9dfa047e462` |
| `test_lingshi_brand_generation.py` | `f280cd94925df5a98098e3d9c6d08dc037dcd3a6` |
| `test_lingshi_localized_generation.py` | `e05f02a0360d01de5030885c054ec2e69ae18dd0` |

Only `brand_image_prompt` is a new internal dependency of the three modules.
The base already contains `image_workbench`, `image_localization`,
`image_generation_knowledge`, `image_review_package`, `image_suite_plan`,
`image_shot_prompts`, `localized_image_packs`, `localized_image_review`, and
`domains/content_operations/content_package_adapter.py`. They are kept at base;
no sourcing directory wholesale copy or speculative dependency replacement was
performed. The source inspection manifest records their base/local-head blob
differences without claiming that every variant is semantically equivalent.

## Retained behavior and narrow correction

- The brand prompt imports the standalone pure prompt module; the retired
  ToAPI generator is not imported as a fallback. Brand model alias resolution
  prefers `gpt-image-2`, then `tt-image-2`, using injected catalog fixtures.
- `LK888_API_KEY` remains an environment alias and injectable configuration
  contract. Preview does not make a request; client paid/upload calls retain
  their existing explicit runtime flags. No approval dialog or extra review
  layer was added. The supervised versus applicable ACTIVE-policy decision
  remains owned by the existing governing caller.
- Brand/localized generation retains checkpoint reuse, explicit model receipts,
  output digest checks, task polling, source identity inputs, and differentiation
  between no-task rejection and failure after a task was created.
- A repeat with the same `SUBMISSION_UNKNOWN` identity is blocked. A known
  submitted task is polled without another creation. These guarantees do not
  imply the broader S02 recovery gaps below have been resolved.
- The pure preflight payload builder now requires integer `n == 1`, a nonempty
  model/prompt, and 1–14 ordered HTTPS references with a hostname and no URL
  credentials. Invalid supplied references are rejected instead of silently
  removed. URL validation is syntactic; no DNS/public-host reachability claim
  is made. The fixed 2048-square, medium-quality payload remains unchanged.

The last change is a verified local correction, not a newer-source selection.
On the retained client, new fixtures first produced 8 failures and 21 passes:
`int(n)` silently accepted booleans/fractions/strings; prefix-only filtering
accepted incomplete/credential-bearing URLs and silently dropped identity
references; the model could be empty. The same tests pass after correction.

## Entries and consumers

Pure entry points are `build_brand_image_prompt`,
`build_localized_reference_prompt`, `build_generation_payload`, and client
`preview_*` methods. Execution entries are `generate_brand_image` and
`generate_localized_reference_image`; these require an already governed caller
and are tested here only with fixture clients/loaders. They are not attached to
`new_product_server.py` or `new_product_workbench.py` in this candidate.

The existing `ContentPackage` consumer remains
`domains/content_operations/content_package_adapter.py`, backed by
`shared_platform.contracts`. Its existing fixtures plus the new negative cases
verify stable source/generated ordering, current-versus-superseded asset
selection, and exclusion of pending/rework/rejected/superseded decisions. The
existing localized pack/review tests exercise approved-source linkage and
reviewed derivative state. Generation receipts alone are not an approved
ContentPackage and do not confer upload or publication authority.

## Offline evidence

Evidence lives only in audit `domain-prep/02/b3a-validation/`:

| Run | Result |
| --- | --- |
| `base-paired` | 4 collection errors: target production modules absent |
| `selected-paired` | 18 passed on the selected source set |
| `contract-before-fix` | 8 failed, 21 passed; concrete payload-boundary defects |
| `green-focused` | 97 passed, no skips; paired + new core + existing handoff/review/pack/planning tests |
| `s02-observed-gaps` | 3 deliberately red safety probes, deferred recovery requirements below |

The runner uses existing Python 3.12.8 / pytest 8.4.2, sets candidate cwd/imports
explicitly, disables bytecode, pytest cache and plugin autoload, scrubs the two
environment aliases, and only permits fixture configuration reads. Python audit
hooks reject socket/DNS connections and SQLite connections. Import paths and
zero external attempts are recorded. All model catalogs, API responses, prices,
tasks, images and keys in tests are synthetic. There were zero actual external
requests, paid generations, business database operations, marketplace writes,
notifications, service starts, installations, automation changes, or pushes.
No browser acceptance was claimed; this package changes no UI.

## Remaining activation seams and next owners

S02 must resolve these **reproduced** gaps before any production execution:

1. `localized_image_lingshi_generation.generate_localized_reference_image` puts
   `retry_attempt` in the job digest. Incrementing it after attempt 0 became
   `SUBMISSION_UNKNOWN` created a second fixture task without reconciliation.
2. A recovered `SUBMITTING` checkpoint with no task ID can create a second task.
   The fixture simulates a process interruption after the durable submit marker.
3. `brand_image_lingshi_generation.generate_brand_image` does not bind the full
   prompt basis into its checkpoint identity. Changing `product_identity`
   reused a completed fixture output. Frozen facts/full prompt identity need
   explicit binding in the recovery contract.

These probes remain in the audit area as honest red evidence, not skipped tests
or changed assertions in the delivered suite. This preservation package must
not be presented as approval to enable the paid runtime.

The base's `image_suite_plan.py` and `localized_image_auto_translation.py`
still contain pre-existing retired ToAPI execution paths. This package neither
restores nor calls them. The coordinator must assign the existing planning/
translation consumers to the subsequent provider migration package; their
source decisions must be reviewed independently of this minimal import closure.
U02 owns the coherent UI trio and browser acceptance. The round-2 Skill owner
retains immutable upstream facts, cost/authority checks and governed QA;
03 alone owns external synchronization/publication. H3, TikHub, LinkFox and
Photoroom retain their independent source and are not enabled by this package.
