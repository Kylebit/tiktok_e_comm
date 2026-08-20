# Product-family knowledge schema

Knowledge accelerates preparation; it never overrides the current Offer ID,
target selection, official provider state or a user decision.

## Rule shape

Each confirmed product-family rule should contain:

```yaml
product_family: "stable semantic name"
match:
  required_semantics: []
  excluded_semantics: []
targets:
  "exact target label":
    category:
      provider_category_id: ""
      path: ""
      required_attributes: {}
      evidence: "official readback reference"
    copy:
      language: ""
      title_constraints: []
      description_constraints: []
    image_guidance:
      recommended_count: null
      recommended_roles: []
commercial:
  pricing_rule_version: ""
  parcel_policy_version: ""
confirmed_at: ""
regression_test: ""
```

## Admission rule

Add or change a rule only when all are true:

1. product semantics are explicit;
2. provider target and exact category are known;
3. required attributes were read from the official provider;
4. a regression protects the mapping or invariant;
5. official post-write readback confirmed the result when a write was needed.

Do not store raw provider responses or secrets. Do not generalize one target's
category ID to another target. Do not turn a user-approved narrow fallback into
a generic fallback.

## Image guidance

Knowledge may recommend image count and roles such as cover, scale, use case,
installation or care. It may not select which source positions require
translation and may not decide that two store groups need different content.
Those remain user decisions in the first review.
