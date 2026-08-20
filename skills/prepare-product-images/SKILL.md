---
name: prepare-product-images
description: Lock a conversation-approved first-round Product Center scope, optionally generate only the user-selected localized product images through ToAPIs, synchronize and verify the common Miaoshou baseline exactly once, record conversation approval, and atomically freeze the approved ReleasePlan handoff without publishing. Use after the user finishes the first-round review and asks to start, resume, approve, or finish the second round.
---

# Prepare Product Images

Use the deterministic script in `scripts/prepare_product_images.py`. Do not reconstruct this workflow with ad hoc API calls.

## Workflow

1. Require the exact Offer ID and current Kyle-approved first-round Product Center revision.
2. Require `reports/product-preparation/<offer_id>/first-review.json` to be `FIRST_REVIEW_READY` and to match the current revision.
3. Treat the first-review image plan as authoritative. Do not use OCR or model judgment to add translation positions.
4. Exclude source images marked `REMOVE`. Remap retained source positions deterministically.
5. Run the script without paid flags first. Report the frozen input digest, selected positions, locale routes, and paid task count.
6. If no positions were selected for translation, continue with zero paid tasks. Do not invent image work. Otherwise start paid generation only after explicit user authorization by supplying both `--execute-paid` and `--confirm-paid-generation`.
7. Require one completed ToAPIs receipt per task and an exact output count. Do not retry blindly after an unknown outcome; inspect the durable generation checkpoints first.
8. Write the English master image set to the common Miaoshou collect box exactly once and require official readback before publication execution. Use both `--execute-miaoshou` and `--confirm-miaoshou-write`. This technical condition must not block or erase conversation approval.
9. Keep localized artifacts frozen by target route. Do not place several country image sets in the common collect box; the publication workflow projects them into their matching site drafts later. Persist the public HTTPS result URL returned by ToAPIs. For a legacy artifact without that fact, require an explicit uploaded-assets manifest; never fabricate or silently re-upload it.
10. Product Center `/new-product?offer_id=<offer_id>#localizedImageResults` is the only human review surface. The page `/localized-image-review?offer_id=<offer_id>` is a technical result view with one action: refresh. It must not contain generation, per-image PASS, retry, paid-confirmation, or final-approval buttons.
11. Treat Kyle's explicit approval in the conversation as the only human approval entry. Record it immediately even when generation, Miaoshou sync, or another technical check is incomplete.
12. The approval intent automatically accepts ready artifacts and reconciles later artifacts under the same frozen input. Technical blockers prevent execution only; they never invalidate the approval intent or require Kyle to approve again.
13. After one verified Miaoshou sync and conversation approval, run the final handoff. Reuse an exact active approved base plan or locally freeze the current exact plan. If localized tasks exist, atomically create and approve an image-routing-only successor. If no localized tasks exist, retain the exact base plan. Persist `workflow-handoff.json` only after the frozen v4 snapshot and route coverage read back exactly.

## Safety boundaries

- ReleasePlan changes are local only: freeze the exact base plan and, when needed, atomically create one image-routing-only approved successor. Never leave the predecessor superseded without a usable approved successor.
- Do not write Miaoshou during paid generation. Run the separate common-baseline sync only after generation receipts are complete and the conversation has authorized that write.
- Do not publish, claim, create drafts, or update listing images.
- Do not generate for an unapproved or stale revision.
- Do not generate a locale that is absent from the approved first-review plan and selected targets.
- Treat paid image calls as external writes and report their exact confirmed count.
- OCR is allowed only to extract text inside a user-selected image; it must never choose which images are translated.
- Do not publish from this Skill. A `READY_TO_PUBLISH` handoff is consumed only by `publish-approved-product`.

## Commands

Preflight:

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id>
```

Paid generation after explicit approval:

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --execute-paid --confirm-paid-generation
```

Synchronize and verify the common Miaoshou baseline before review:

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --execute-miaoshou --confirm-miaoshou-write
```

Persist Kyle's explicit conversation approval at any point in the frozen round:

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --approve-all --approved-by Kyle
```

Freeze the exact approved handoff after Miaoshou verification and conversation approval:

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --finalize-release-handoff
```

For legacy generated artifacts whose ToAPIs receipt predates persisted public
result URLs, add `--uploaded-assets <PATH>`. The JSON file must contain an
exact `uploaded_assets` map of approved artifact ID to its matching digest and
public HTTPS URL. Extra, missing, or drifted entries fail closed.

The command emits one JSON summary. Approval and execution readiness are separate fields: approval may be recorded while the result still reports `MIAOSHOU_SYNC_REQUIRED` or another technical blocker. A verified baseline sync reports `miaoshou_external_write_count: 1`, `platform_writes: 0`. Finalization reports `READY_TO_PUBLISH`, the exact plan and snapshot identities, and never asks for another page approval.
