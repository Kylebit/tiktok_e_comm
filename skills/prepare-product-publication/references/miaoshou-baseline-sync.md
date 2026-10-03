# Miaoshou round ownership

## First round

`prepare-product-publication` freezes the product facts and selected scope with
zero Miaoshou writes. Its existing client still emits
`miaoshou_sync.status=DEFERRED_TO_SECOND_ROUND`; legacy execution flags also
retain historical error wording. These are compatibility outputs, not current
stage authority. Do not rewrite persisted values to resume a workflow.

Do not import or call `prepare_miaoshou_draft` or `write_miaoshou_draft` from
the first-round client. Legacy execution flags fail explicitly.

## Second round

`prepare-product-images` consumes the frozen R1 scope, generates/localizes images
and performs QA. It does not synchronize Miaoshou, create shop drafts or publish.

## Third round

Use the current Product Center `publication-stages/v1` response through
`scripts/product_publication_workflow.py`. For COMMON work it routes to
`skills/publish-approved-product/scripts/prepare_publication_execution.py`.
R3 owns COMMON synchronization and official readback, then the frozen release
handoff and independently authorized platform publication. Reuse the existing
exact approval; this routing correction does not add an approval step.

If COMMON is running, blocked or requires reconciliation, the workflow returns
no execution command. Preserve the durable outcome and reconcile it rather
than interpreting the old R1 label as permission to repeat a write.
