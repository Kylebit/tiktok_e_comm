# Image localization pipeline — batch 1

This batch establishes the local evidence layer used by later watermark removal
and translated-image generation. It does not call a paid OCR provider, an image
editing provider, Miaoshou, or a publication API.

## Data contract

Each offer owns `image-localization-manifest/v1`. A source image is identified
by the SHA-256 of its authoritative URL and is never overwritten. All edits use
a manifest revision compare-and-swap and create a new derived artifact.

Each text region has a stable `region_id`, normalized `[x0, y0, x1, y1]` box,
text, origin (`manual` or `ocr`) and one closed classification:

- `watermark` / `supplier_metadata`: eligible for reviewed removal;
- `protected_natural_text`: must not overlap a removal region;
- `translatable`, `product_fact`, `dimension`: retained for later translation;
- `rebuild_required`: dense detail art that must be rebuilt instead of erased;
- `ignore`: explicitly excluded from later processing.

OCR results are bound to the source identity digest. A rescan replaces only OCR
regions; manual regions remain authoritative. The production OCR provider flag
is off by default.

## Clean master

The first local method is `local_region_fill/v1`. It fills only reviewed
watermark/supplier boxes using neighboring pixels. Blanket `ai.all` removal is
forbidden. Any removal/protected overlap fails before an artifact is written.
The output is stored by content digest under the offer and records its source
digest, method, removal regions and protected regions.

This deterministic method is intended for simple edge watermarks and preview.
Complex embedded marks remain `rebuild_required` until a later provider-backed
method is added behind the same contract.

## Feature flags

- `ORBIT_IMAGE_LOCALIZATION_MANIFEST` (default on)
- `ORBIT_IMAGE_LOCALIZATION_LOCAL_CLEAN_MASTER` (default on)
- `ORBIT_IMAGE_LOCALIZATION_MANUAL_EDITOR` (default on)
- `ORBIT_IMAGE_LOCALIZATION_OCR_PROVIDER` (default off)

Batch 2 may add an OCR adapter and English-authority text review. Batch 3 may
add approved translations and deterministic localized renderers. Neither may
replace the source or bypass the batch-1 manifest.
