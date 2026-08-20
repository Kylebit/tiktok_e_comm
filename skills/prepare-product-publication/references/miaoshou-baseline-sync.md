# Miaoshou round ownership

This reference prevents a historical boundary regression.

## First round

`prepare-product-publication` performs zero Miaoshou writes and reports:

```json
{
  "status": "DEFERRED_TO_SECOND_ROUND",
  "written_to_miaoshou": false,
  "verified": false
}
```

Do not import or call `prepare_miaoshou_draft` or `write_miaoshou_draft` from
the first-round client. Legacy execution flags fail explicitly.

## Second round

`prepare-product-images` owns the single common collect-box synchronization
after frozen image work is complete and the conversation authorizes execution.
It uses the existing Product Center writer, sends at most one request, and
requires official readback. Claim, shop-draft creation, and publication remain
forbidden until the separate publication Skill.
