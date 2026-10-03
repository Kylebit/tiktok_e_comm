# U04-J: reviewable execution sequence, not execution authority

This package adds only `scripts/provision_private_directory.py` and a synthetic
native Windows rehearsal. It neither stops processes nor migrates credentials.
Read the frozen audit bundle `u04-execution-readiness-20260906/EXECUTION_BUNDLE.md`
for dated PID/automation observations; a PID snapshot is never permission to stop.

## Create one new private directory with its ACL already attached

`prepare(path)` reads metadata and binds target, parent device/inode, current
owner SID and implementation sources. Parent must exist; target must not exist;
Git-contained and reparse paths fail. It never creates parents or changes an ACL.
`execute(plan, authority)` requires exact plan digest, `directory_create=True`
and an actual named authority. On Windows it supplies the protected owner-only
inheritable DACL to `CreateDirectoryW` through SECURITY_ATTRIBUTES. There is no
interval between an unprotected mkdir and subsequent ACL tightening.

After creation, existing G `protected` verifies owner/DACL/no-reparse, and the
directory must be empty. ACL-conversion/create failures do not proceed. If
post-create verification fails, report `PROVISION_UNVERIFIED_RECONCILE`, preserve
the directory and stop. A new execute refuses any occupied target, even an empty
directory from the same interrupted plan. `verify(path)` can independently
inspect a retained empty directory; it does not assert who created it or authorize
reuse. Before adoption after an interruption, bind the original process exit and
the resulting directory identity to the approved plan. Never delete or modify
preexisting contents to make provisioning succeed.

A caller may provision missing named ancestors one at a time under separately
frozen plans, only if that exact creation scope is approved. Existing ancestors
are not modified. No recursive mkdir, ACL propagation across repository parents,
symlink shortcut or automatic cleanup is supplied.

## Separate three decisions

1. **Local provisioning and copying:** use the accepted I directory-scoped lock,
   approved private storage plan and independently verified stopped writers.
   Original settings and tokens remain unchanged. The existing database target
   remains absolute and no DB connection is needed. Copy completion means only
   that the private files match the approved inputs; it does not activate them.
2. **Controlled remote recovery:** G is the explicit bounded auth writer. All old
   refresh writers sharing the TikTok session must remain stopped throughout
   refresh, identity readback and any subsequent local reconciliation. A second
   copy of an old refresh token is not an independent session. Unknown network
   outcomes remain reconciliation-only, with no rollback to historical tokens.
3. **Resume workflows:** before resuming a retained TikTok consumer, route its
   fresh process to the SAME private token store and establish serialized refresh
   ownership. Path convergence alone does not serialize legacy `ensure_valid_token`
   or `save_token`; those old writers do not honor the migration lock. Do not
   restore them unchanged and assume that a still-present historical file works.

The daily runner can use its exact independent settings path only after a fresh
process confirms CONFIG_PATH precedence, loaded settings provenance, token_path
and db_path. Existing D main CONFIG_PATH takes precedence over environment; merely
setting ORBIT_HIVE_SETTINGS cannot reroute that process. Reuse the I actual
synthetic consumer proof to verify a future route, without opening DB or tokens.

For broader resumption, a separate scoped implementation must give each retained
entry an explicit settings route before config is cached, point TikTok to the
single private store, retain its own original Shopee path and database target,
and serialize its remote refresh with the authorized writer. This package does
not implement a general auth rewrite or pretend this compatibility work is done.
Until that scope is accepted and proven, the executable narrow option is local
private preparation followed by explicitly controlled G recovery/captured reads
while legacy TikTok refresh entries remain stopped. Whether those old workflows
may remain unavailable is an ownership decision for the coordinator/user.

Do not infer equality of D main and daily Shopee token collections. Never redirect
unrelated Shopee consumers to the copied daily store. Unclassified processes,
Codex/coordinator runtimes, other previews and U01 are outside any stop proposal.

## Verification and limits

The native provisioning tests create only new synthetic directories. They cover
private DACL readback, a non-BMP path, occupied/nonempty target preservation,
missing parent, authority/source/parent drift, ACL failure, post-create failure,
an actual child exit before verification and a Windows junction parent. RED is
a missing-helper failure, not a claim of a previous unsafe ACL implementation.
No real directory/ancestor ACL, credential, configuration, task or automation is
changed by this package. Runtime inventories are read-only and redacted.
