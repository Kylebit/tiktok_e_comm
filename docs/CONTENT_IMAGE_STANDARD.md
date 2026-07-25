# AI product image operating standard

Status: v1, established from the 2026-07-25 size-card workflow.

## Responsibility boundary

Content operations owns image briefs, storyboard review, generation, local
post-processing, asset lineage, and final image review. Channel operations owns
all writes to Miaoshou or a marketplace. Approving a content image never implies
approval to publish or synchronize it.

## Required workflow

1. Review the storyboard as a text/composition proposal before paid generation.
2. Generate a clean product base from approved identity references.
3. Keep model generation free of text, numbers, arrows, guide lines, badges,
   prices, and unsupported claims.
4. Add verified dimensions and other exact copy with deterministic local code.
5. Review the final composed image, not only the model base.
6. Ask for a separate explicit approval before any external synchronization.

For the current two-axis size-card workflow, the first operator-approved
horizontal value is rendered as `WIDTH`; the second vertical value is rendered
as `HEIGHT`. Operator-confirmed orientation is authoritative when supplier
field names are ambiguous.

## Size-card implementation

- `modules/sourcing/image_shot_prompts.py` keeps the AI base text-free and
  reserves right/bottom whitespace.
- `modules/sourcing/dimension_overlay.py` locates the product, lays it out on a
  square white canvas, and draws dimension guides, double arrows, and English
  labels with Pillow.
- The original generated base is retained as `_model.png`.
- The overlay audit records labels, product bounds, arrow coordinates, overlay
  version, and upload result.
- Changing dimensions, colors, fonts, or arrow placement must reuse the model
  base instead of invoking paid generation again.

## Acceptance criteria

- Product shape, color, count, and identity remain faithful to approved inputs.
- Final text exactly matches operator-approved facts and uses the correct
  orientation.
- The image remains square, legible at marketplace thumbnail size, and has no
  clipped labels or measurement graphics.
- Every final asset can be traced to its model base, prompt/shot id, overlay
  version, and review decision.
- Technical tests and the complete repository regression suite pass.
- No real marketplace or Miaoshou write occurs without explicit approval.

## Evidence package

Every completed image task returns:

- final image and preserved model base;
- prompt/shot id and human-approved facts;
- local post-processing version and audit record;
- focused test results and full regression result;
- external writes attempted or completed;
- known limitations and the next reusable rule.

Approved and rejected examples should be accumulated into category-specific
golden and negative sets. Future image changes should be evaluated in batches
against those sets rather than tuned one step at a time with Kyle.
