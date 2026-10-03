# Publication closure client

`scripts/close_product_publication.py` is a Python standard-library client of the server-owned `/api/product-workspace/publication-closure/` API. It accepts only an explicit HTTP loopback origin (`127.0.0.1`, `localhost`, or `[::1]`) and an expected absolute runtime root. It rejects redirects and checks `/api/health` service identity, root, and missing dependencies before every action. The client can run from a portable copy, but the server must be a verified full OrbitHive runtime with the exact original approval and publication evidence. Neither `--help` nor `doctor` proves closure authority.

Use the same verified origin and root for every command:

```text
python scripts/close_product_publication.py --base-url http://127.0.0.1:PORT --expected-root R doctor
python scripts/close_product_publication.py --base-url http://127.0.0.1:PORT --expected-root R prepare --input INPUT.json > PREPARED.json
python scripts/close_product_publication.py --base-url http://127.0.0.1:PORT --expected-root R record --prepared PREPARED.json
python scripts/close_product_publication.py --base-url http://127.0.0.1:PORT --expected-root R latest --offer-id OFFER --plan-id PLAN
```

`INPUT.json` contains the exact `offer_id`, `plan_id`, `recorded_by`, and `recorded_at` (ISO timestamp). For a user-accepted manual target resolution, add `manual_handoffs` keyed by an approved target label, each with `accepted_by`, `accepted_at`, and `note`. Do not invent handoffs or expand the frozen target list. Prepare validates the current plan, final approval, target readbacks, source reports, and any handoff evidence; it performs no closure write. Save its complete JSON output as `PREPARED.json` only when `status` is `READY_TO_RECORD` and `ok` is true. Record submits the prepared request and input digest; the server rechecks that the evidence has not changed, then stores one immutable local closure. It makes no provider write and does not change the underlying target status.

If record returns `RECORD_OUTCOME_UNKNOWN`, retain `expected_closure_id` and `expected_closure_digest`. Do not resend record automatically. Read `latest` for the same offer and plan and compare both identities, then inspect the durable source and closure evidence before deciding on any further action. A processing or failed source remains an open item even when the business workflow is closed. A manual handoff is not official publication success. The existing single final approval and exact candidate, snapshot, plan, and ordered target identity remain unchanged.
