# B00 B2A Product Contract Integration

Work order: `WO-20260905-ORBIT-B00-B2A`

Base: `b72a8d1d048862da08a9a9bca333425abbed857c`

Parent integration base: `eb60a9052988654100405d5cb8c679723303734e`

## Outcome

This candidate integrates only the four approved Product Operations pure-contract
modules. It performs no file, database, HTTP, provider, Skill-install, or other
commerce write at runtime.

- Governed AB/B-link Seller and Model SKUs matching `99xxxx` retain their full
  six-digit reservation key. Ordinary six-digit SKUs continue to reserve by the
  canonical zero-filled tail four digits. The two namespaces do not collide.
- `approved-publication-snapshot/v4` remains a detached canonical JSON document
  with a SHA-256 digest. Shopee global master v1 and v2 both validate and
  round-trip; v2 freezes exact per-model image URL, digest, source offer and
  source position, with multi-SKU uniqueness enforced.
- Exact TikTok warehouse allocations can be frozen per publication target and
  shop, including warehouse IDs/names/stocks, exact total, approval source,
  actor, and timezone-aware approval time. Coverage, duplicate shop identity,
  malformed identity, total drift, actor/time drift, and digest tampering fail
  closed.
- Shopper-facing SKU specification names are validated independently from raw
  supplier variant keys and Seller/Model SKU identities. Derivation never
  mutates or replaces the governed identity fields.

## Public product-domain interfaces

Consumers should use these module-level interfaces directly until the integration
owner deliberately updates package exports:

- `resolve_sku_lineage_reservation(...)`
- `finalize_new_source_sku_reservation(...)`
- `new_source_sku_reservation_digest(...)`
- `build_approved_publication_snapshot(...)`
- `approved_publication_snapshot_from_payload(...)`
- `validate_approved_publication_snapshot(...)`
- `publication_images_for_target(...)`
- `publication_content_for_target(...)`
- `build_approved_publication_snapshot_inputs(...)`
- `validate_sku_display_name(...)`
- `derive_sku_display_name(...)`
- `validate_specification_mapping(...)`

## Frozen shapes added or preserved

`shopee-global-master/v2` uses:

```text
variant_image_bindings[] = {
  model_sku,
  image_url,
  image_digest,
  source: {kind, source_offer_id, source_position}
}
```

`product.warehouse_inventory_by_target[target_label]` uses:

```text
{
  schema_version: miaoshou-tiktok-warehouse-allocation/v1,
  shop_id,
  warehouses: [{warehouse_id, warehouse_name, stock}],
  total_stock,
  source: CONVERSATION_APPROVAL,
  approved_by,
  approved_at
}
```

The approval-time bridge returns the same detached allocation under
`approved_inputs.warehouse_inventory_by_target` only when dashboard and plan
facts are both present and exactly equal. The value is also included in the
policy evidence digest.

## 00 wiring notes

1. Treat `approved_publication_snapshot.py` and
   `approved_publication_snapshot_inputs.py` as one atomic contract change.
2. At ReleasePlan approval, call
   `build_approved_publication_snapshot_inputs(dashboard=..., release_plan_payload=...)`.
   When the returned mapping contains `warehouse_inventory_by_target`, copy it
   into `payload.product_facts.warehouse_inventory_by_target` before computing
   the plan approval digest. The current shared projection at base `b72a8d1d`
   does not yet copy this new key; that is the precise 00-owned wiring gap.
3. Build the v4 snapshot once from the approved payload, persist its canonical
   payload and `snapshot_digest` atomically with the approved ReleasePlan, and
   validate again on load. Never rebuild it from a current dashboard or provider
   readback.
4. Store reservation keys exactly as returned by the 01 contract. Do not apply
   tail-four normalization a second time: `990003` is governed key `990003`,
   while ordinary `770003` is key `0003`. Existing reservation lookup and unique
   indexes must compare the returned keys verbatim.
5. Keep raw variant key, Seller SKU, Model SKU, and shopper display name in
   separate fields. Import display-name helpers from
   `domains.product_operations.sku_display_name`; do not use the display value
   as a persistence or lookup identity.
6. 02/03/04/05 consumers receive only the frozen v4 payload. Mutable source,
   content, category, pricing, warehouse, or page state must not overwrite it.

## Verification evidence

Before production-code changes, tests from the audited D source were executed
against this exact base via `PYTHONPATH`:

- SKU lineage: `1 failed, 32 passed`; the failing assertion proved `990003` was
  incorrectly collapsed to `0003`.
- Snapshot and Shopee v2 suites failed during collection because the base lacked
  the newer snapshot interface.
- SKU display-name suite failed during collection because the module did not
  exist.

After integration:

- Four primary contract suites: `95 passed`.
- Primary suites plus existing snapshot input/integration tests: `120 passed`.
- Final changed-input and added acceptance cases: `26 passed`.
- Four production modules compile through `py_compile` with bytecode directed to
  the private validation evidence directory.
- The original receipt recorded a passing working diff check. Independent review
  of exact commit `26290f87` found two Markdown hard-break whitespace warnings;
  the correction below removes them.

Raw command output and machine evidence live outside the repository under
`outputs/orbithive-audit-20260905/domain-prep/01/b2a-validation`.

## Remaining integration risks

- This candidate intentionally does not change the 00-owned shared projection,
  Store schema/CAS, indexes, or package export surface. Enabling warehouse
  allocations before the projection copies the bridge key would silently omit
  approved warehouse facts, so 00 must complete item 2 above before activation.
- Store and downstream consumers must be tested against full-six-digit governed
  reservation keys before enabling affected products.
- `derive_sku_display_name` has a deterministic `1pcs` fallback. Callers should
  present that value for review; they must not infer supplier facts from it.

## B2A independent-review correction

Correction base: `26290f878559621c4ae78d1112ab05eee8b8dd92`.

The snapshot loader previously accepted shopper specifications that the builder
rejected when the input document carried a recomputed checksum. Construction,
deserialization, and validation now share the same specification validator. Raw
variant keys, Seller SKUs, and Model SKUs remain distinct from display values.
This is semantic validation; a matching checksum never grants marketplace write
authority or replaces independently stored approval evidence.

The warehouse approval bridge previously used Python mapping equality, which
treated `true`, `1`, and `1.0` as the same value. Both dashboard and plan allocations
now satisfy the existing exact-target warehouse contract before canonical JSON
digest comparison. Warehouse row stock and total stock must be built-in integers;
coverage, schema, shop identity, warehouse identity, approval source, actor, and
time checks are reused from the frozen snapshot validator. Valid allocations stay
detached and remain included in the policy digest.

The isolated correction adds 25 regression/control cases: before production
edits, `22 failed, 3 passed`; after correction, `25 passed`. The four affected
snapshot, input, Shopee global-master, and display-name suites report `103 passed`.
Valid v1/v2 contracts retain their schema and successful construction/load paths.
Raw red/green evidence is under
`outputs/orbithive-audit-20260905/architecture/b2a-fix-validation`.

This correction does not activate the 00 projection or any provider consumer.
The integration requirements above remain until separately verified.
