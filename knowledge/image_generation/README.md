# Image Generation Knowledge

`v1.json` is the versioned local policy used by image-suite planning and per-shot prompt generation.

It has three layers:

1. Global guardrails: approved visual-text language, fact provenance, identity preservation, and prohibited content.
2. Category profiles: category match terms, category-specific planning constraints, and recommended shot suites.
3. Traceability: the selected category profile and knowledge version are included in the suite plan and prompt bundle metadata.

## Editing Policy

- Add a category profile only when its rules are evidence-based and materially change the image plan.
- Use JSON Unicode escapes for Chinese match terms so the file remains reliable across Windows terminal encodings.
- Keep customer-facing visual text in English unless a separate storefront locale explicitly approves another language.
- Do not place claims, measurements, logos, or labels into generation prompts unless they are source-verified. Add approved English overlays deterministically after generation.
- Review the white-background hero before paying for the remaining suite.
