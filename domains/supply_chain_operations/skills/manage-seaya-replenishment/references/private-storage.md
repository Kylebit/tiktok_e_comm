# U04-I: isolated daily credentials, preparation only

Use `scripts/private_storage.py` to prepare a metadata-only public plan. Its CLI
only accepts `--dry-run PUBLIC_PLAN`; there is no migration or activation CLI.
`execute(plan, authorization, stopped_writers)` is a future integration API,
requiring `storage_write=True`, exact `plan_digest`, a named authority, and fresh
independently established stopped-writer evidence before each publication. There
is no production evidence collector supplied here. Synthetic receipts authorize
only synthetic tests. A lock file does not establish quiescence of legacy writers.

## Concrete proposed targets

Private directory (not created):
`C:/Users/Windows11/AppData/Local/OrbitHive/credentials/supply-chain-daily`.

| Original, preserved | Private copy |
| --- | --- |
| `D:/Users/Windows11/Desktop/Agent_PR/tiktok_e_comm/tiktok_tokens.json` | private directory `/tiktok.json` |
| `D:/Users/Windows11/Desktop/Agent_PR/_codex_worktrees/04-supply-chain-ops/shopee_tokens.json` | private directory `/shopee.json` |
| `D:/Users/Windows11/Desktop/Agent_PR/tiktok_e_comm/config/settings.json` | private directory `/settings.json` |

The new settings changes only `token_file`, `shopee.token_file`, and `database`.
Token values are absolute private paths. `database` is the EXACT old resolved
absolute target, including the original default semantics if the key is absent.
The current real database setting was not read in this package: its target is
UNVERIFIED, never assumed to be the default. The public draft leaves it null;
execution rejects it before credential/settings payload reads. A later permitted
path-only inspection must freeze this non-secret target and reissue the plan
before migration approval. No database is opened or created by this helper.

Static source inspection of both D trees found settings-base-relative paths only
in `core/auth.token_path` and `core/db.db_path`; Shopee uses project-root-relative
paths. Other settings keys are copied unchanged; no new resource base is applied
to ROOT-relative consumers. This inventory binds the observed code, not arbitrary
untracked scripts or future modules. Recheck source bindings before execution.

Do not default to changing the shared original settings in place: an absolute
Shopee daily path there would also redirect unrelated consumers of shared
settings. The D main and daily Shopee token sets were not compared and must not
be assumed equivalent. The recommended independent profile keeps both originals
and other consumers' path selection unchanged.

## Protection and publication

Later authorized provisioning must create only the named private directory with
owner=current Windows SID, inheritance disabled, and inheritable full access for
that SID (optionally SYSTEM/Administrators). Verify it using G `protected` before
reading/copying secrets. Do not change any Git parent ACL. The helper does not
create directories or change ACLs and rejects Git-contained or reparse paths.
Create no credential junction/symlink. Existing ACL failure is a blocker.

All originals are metadata-bound by the public plan without credential hashes.
Source code hashes contain no secret material. After explicit authority, full
original bytes are read only in memory and backed up privately. Token copies are
byte-for-byte, preserving every Shopee shop, merchant, sync map and unknown key.
Private settings preserve all unrelated JSON values. G retained native reserved
handles protect candidate creation, flush, atomic replacement and byte readback;
non-BMP Windows paths are exercised. No secret-bearing candidate is created in
an unprotected repository parent. The new files all publish inside one private
directory, so original D to private C copies need no cross-volume atomic rename.

Journal states: BUILDING while private backups are prepared, then READY before
any final copy, then COMPLETE after both tokens and settings read back. READY
resumes only the same plan with unchanged originals and exact already-published
bytes. Settings publishes last. COMPLETE repeats return REUSED after verification.
Backup-stage failures, changed sources/destinations or conflicting plans require
reconciliation; no silent overwrite or rollback. A crash leaves a durable orphan
lock at `migration-destination.recovery-lock`, shared by every run ID targeting
the directory. The lock covers source reads, destination checks, backups and
all publications through final readback; another run cannot bypass it by changing
its journal/run ID. Verify the exact process has exited, retain journal/candidate evidence and
obtain explicit local reconciliation authority before removing only that lock.
Do not remove backups or rotate anything to make a retry pass. Synthetic tests
exercise a real child exit between file publications and explicit reconciliation.

## Stop writers and activate only in a later approved package

1. Freeze metadata/code/config selection again. Obtain real path-only database
   evidence. The three old file sizes/mtimes matched the prior preflight on this
   run, but this does not prove their secret contents or platform validity.
2. Enumerate and pause the exact daily automation `automation`, task
   `019f9849-1a6d-7671-a517-1b75700cbafe`; it was ACTIVE when inspected. Wait for
   active daily work/child runners to stop. Do not treat scheduler pause alone
   as termination of an already running writer.
3. Inventory runtime processes and ad-hoc commands by approved metadata, verify
   owner/PID/start time and stop in-flight auth calls in both trees. Writers include
   TikTok `save_token`/refresh/ensure, Shopee OAuth/merchant/shop refresh,
   `modules/shopee/shops.py` region/sync saves, API service requests and manual
   `tiktok_auth.py`. The latter honors configured `token_file`, but its relative
   fallback is CWD-based. No fixed-path bypass was found in the bounded tracked
   source inventory; this does not prove the absence of untracked/background tools.
4. Confirm ALL old remote refresh writers remain stopped during migration AND
   later credential recovery: copies of one refresh token can invalidate each
   other even when local files differ. Preserving historical files does not make
   old token rotation safe. No automatic restart is authorized by copy completion.
5. Provision private ACLs; approve the exact complete plan; run local copying with
   genuine fresh stopped-writer evidence. The helper does not manufacture this
   evidence and cannot protect against an unenumerated legacy writer merely using
   a sidecar lock. Metadata/byte rechecks detect observed drift, not arbitrary
   hostile races. Original files remain historical copies.
6. In a separate approved activation, update the daily runner's currently explicit
   `ORBIT_HIVE_SETTINGS` assignment to the exact private settings path. Keep the
   same D daily source root. Verify D daily `config/settings.json` is absent or
   resolve its precedence explicitly; do not delete it. Start a NEW process after
   setting the environment. `CONFIG_PATH` wins before environment fallbacks, and
   cached relative values can resolve against a newly selected base. Actual R,
   D main and D daily consumers were tested with injected synthetic configurations
   to prove these conditions and the selected private token paths/unchanged DB path.
7. Read back only actual selection/provenance, ACLs and non-secret path bindings;
   no DB connection or token refresh is needed to verify routing. Automation,
   live auth recovery, account/target validation and APP/daily serving activation
   are separate authorities and remain unperformed.

This package supplies a reviewable approach and synthetic local migration, not a
live cutover. Real settings/token payload reads, copies, ACL/config changes,
network/auth calls, automation changes, DB opens and U01 access were zero.
