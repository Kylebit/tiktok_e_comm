# Five retained-source registry corrections

This maintenance correction seals existing tracked files; it does not install a Skill, enable a provider, or authorize publication. The existing `WORKFLOW_RUNTIME_REQUIRED` stages remain unchanged.

The three obsolete hashes exactly match the file contents at the catalog generation checkpoint `325186fcb85f7ff70788a9d4f4fd5cd0a8643c88`. The actual files are later committed descendants, with no difference between the online `7d2eb1ff` baseline and this integration tree:

| File | Retained source | Reason to retain |
|---|---|---|
| `modules/shopee/auth.py` | `e487558fb9d7443de301c3b29f4212c7fb20826c` | Explicit absolute token-file pin; rejects relative pin. |
| `modules/ozon/config.py` | `2cbc414d9d223742d5315dd19c4537eec4f9d444` | Settings-relative paths and account/digest-bound credentials. |
| `modules/miaoshou/client.py` | `2e578b63`, `9da2c14000dd70b5fc63ba28cacd18c8ad0c01db` | Structured business rejection with bounded, redacted public reason/code/field. |
| `skills/publish-approved-product/scripts/build_shopee_existing_media_binding.py` | `69caed47` → `0f8515c6` → `ba37142eaca62c57727269c16bda3d2ebcc8b9c8` | Existing v2 media-binding evidence producer; its file was absent from the full Skill tree hash map. |
| `skills/publish-approved-product/scripts/register_zero_write_reconciliation.py` | `26a53d7a623381249f308e9e4e78ae6ca62ebbe9` | Existing explicit-execute reconciliation CLI; default path calls read-only `prepare`. Its file was absent from the full Skill tree hash map. |

Only these five file hashes, four containing-row composed hashes, and the portable runtime catalog/digest fields change. Historical source identities, generation checkpoint, capability stages, required-file contracts and all other source digests remain intact. The two scripts remain workflow helpers rather than newly advertised standalone executable capabilities.

Validation: **68 isolated contract tests passed** across Shopee pin/media recovery, Ozon credentials, Miaoshou rejection handling and zero-write reconciliation. Four tests requiring a subprocess, historical Git lookup, unavailable real report, or full server import were explicitly deselected. The initial run had six missing synthetic-settings failures and one unavailable historical-report failure; a sandbox settings file resolved the configuration failures without reading real credentials. No real report was substituted. All declared hashes, complete Skill tree maps and portable runtime manifest now independently match.

The existing full refresh command still reports `catalog_changed=true` because it compares serialized key order. An independent reproduction of its computed catalog finds **zero semantic differences**. No unrelated row was reformatted to suppress that byte-order signal. Evidence and reproducible isolated runners: `D:/OrbitEvidence/convergence-20260919/round3-publication/REGISTRATION_PROVENANCE.json`, `registration-after.json`, `CATALOG_CHECK_DIFFERENCES.json`, `regsrc3.xml`.
