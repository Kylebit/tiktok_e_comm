# Reviewed local repair adoption

`shared_platform.publication_r2_adoption` consumes an existing registered candidate and its saved, exact `keep` decisions. It never creates an approval, a paid request, an upload, a provider task, a budget exception, or a publication plan. Use only after the operator has verified the upload/readback and QA evidence. The current source Skill remains `skills/prepare-product-images/SKILL.md`.

## Prepare an isolated QA bundle

Call `prepare_adoption(runtime_root=..., offer_id=..., expected_revision=..., uploaded_assets_path=..., output_dir=...)`. The output must be outside the registered project and immutable original source. Preparation requires all current candidate rows to be kept and preserves their exact binding/source hashes.

The upload manifest is `{"uploaded_assets": {"local-footer:<reviewed PNG SHA256>": entry}}`, with exactly one entry per candidate and no extras. Each entry contains:

- `artifact_digest`: `sha256:<reviewed PNG SHA256>`.
- `public_url`: the actual public HTTPS upload result.
- `upload_receipt_ref`: the retained actual media-upload receipt reference.
- `readback_path`, `readback_sha256`: the bytes independently fetched from that result URL and their SHA256.
- For differing returned bytes: `transformation: "official-media-transcode"` and `dimensions: {"source": [width,height], "delivered": [width,height], "format": "JPEG"}` (PNG/WEBP also supported).

The verifier checks these files and dimensions and rejects aspect-ratio changes. It does not make network requests or independently establish that the operator fetched a particular URL. The producer of this manifest must retain the original upload response and actual GET readback evidence. This contract does not lower any frozen image or platform size requirement; those requirements must also hold for the delivery artifact.

Outputs:

- `brand-image-translation.json`: proposed formal assets use the **actual delivered image** SHA/path and public URL.
- `reviewed-original-translation.json`: the kept original PNG asset list for original-image QA.
- Other original R2 documents and `adoption-preparation.json`, which binds originals, decisions, and upload manifest hashes.

Every repaired asset has a distinct `local-footer:` artifact ID. Its `local_provenance` retains the kept image SHA/path, master SHA, frozen candidate commit/binding, protected-artwork verification and the complete superseded asset record. The old provider task is not presented as the new artifact's generation. Original counters and policy-budget blocker evidence remain unchanged. Official media transcoding is not reported as new image generation.

## QA and activation

Use the existing `build_automated_image_qa` / original QA Skill contract. Perform original-image and delivered-image QA against their respective full master-plus-localized digest lists. A successful original PNG assessment cannot substitute for assessment of a downsampled JPEG. When upload bytes are identical, the same evidence can serve both lists.

Call `adopt_prepared(bundle_path=..., qa_path=..., qa_sha256=..., assessment_path=..., original_qa_path=..., original_assessment_path=...)`. The primary QA arguments refer to **delivered images**. Original QA arguments are mandatory when any delivery digest differs. The module recomputes both QA receipts through the existing producer and validates the delivered R2 documents through the actual consumer. Merely returning `prepared` or saving `keep` cannot make this call succeed.

Activation is local to the registered project, under its existing decision lock. It archives original R2 documents at the registration's `adoption-history/<digest-prefix>`, preserves the original failed QA and budget evidence, and records active translation/QA hashes in `r2-adopted-assets-receipt.json`. The receipt is installed last: an interruption leaves the consumer blocked, and the same exact bundle can recover without a new provider/upload call. Changing the user decision revision, source binding, bytes, upload manifest or QA prevents reuse.

The terminal receipt confers no Miaoshou or marketplace publication authority. R3 still owns its exact COMMON and final marketplace approval/readback contracts.
