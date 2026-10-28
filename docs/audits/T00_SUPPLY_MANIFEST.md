# T00-A portable supply dashboard manifest

Work order: `WO-20260905-ORBIT-T00-A-SUPPLY-MANIFEST`.
Exact base: `b1f7ab60a5f55dfcdd3c2163358885e73b22414f`.

The B5A unversioned manifest hashes physical bytes of 13 managed text files.
Both real fresh Git checkouts (`core.autocrlf=false` for LF and `true` for CRLF)
failed the original `--check`. The original B5A source files match all 13 legacy
raw hashes; their CRLF-to-LF digests match all 13 Git blobs. This establishes an
EOL portability defect without accepting other source differences.

The verifier now emits `schema_version: dashboard-sync/v2` and
`digest_policy: sha256-utf8-crlf-to-lf/v1` only through explicit `--update`.
For the existing fixed allowlist, it validates UTF-8, rejects NUL-bearing binary
content, and hashes bytes after replacing CRLF with LF. It does not remove lone
CR, trailing spaces, final newlines, BOM, change case or normalize Unicode.
Images and other unmanaged files are never included or rewritten.

Unversioned `{ "sha256": ... }` manifests retain raw-byte SHA-256 semantics and
report that policy explicitly, including a migration hint. `--check` never
updates a manifest or file. Unknown schemas/policies, malformed manifests or
digest values, missing/extra manifest entries, missing files, invalid UTF-8 and
content drift return a nonzero result with an error. New versioned manifests
do not accept the raw policy as a fallback. `--update` validates every managed
file before writing only the new manifest.

Validation:

- Before production edits: real LF/CRLF fresh-checkout checks failed, and the
  added CRLF/LF digest-equivalence regression failed.
- The unchanged regression then passed.
- 25 focused tests pass, including shared-manifest LF/CRLF checks, raw legacy
  compatibility, Chinese content, seven non-EOL drift types, invalid schema,
  missing/extra entries, missing files, binary handling and read-only `--check`
  assertions over bytes, mtimes and file sets.
- 23 existing sync/skill/dashboard tests pass. No unrelated 91-test rerun.

Delivery evidence and fresh-checkout rerun tooling are retained in
`C:/Users/Windows11/Desktop/Agent_PR/outputs/orbithive-audit-20260905/domain-prep/04/portable-validation/`.
The final receipt records source commit and post-commit fresh-checkout checks,
including one-character tamper, missing-file and bad-schema failures using the
same versioned manifest. No per-checkout manifest regeneration is needed.

Only the verifier, its manifest, focused tests and this note change. The 13
managed source texts, 224 images, installed skill/junction, original B5A/S00
trees, production data/services and automations remain unchanged. No network,
platform/paid-service actions or Git push are part of this component. This does
not complete installed-toolkit migration or unified tool registration.

Rollback is omission/reversion of this focused commit. Restoring the B5A raw
manifest also restores its machine-dependent EOL limitation; keep the raw
legacy manifest and source hashes in the audit evidence for comparison.
