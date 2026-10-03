# Existing workspace candidate review

The review stays on `/product-workspace?offer_id=<offer>`. It uses the existing
image cards, 保留 / 不使用 choices and 保存图片选择 interaction in the multi-language
results section. Opening the page only reads. Selecting a radio changes the
browser draft; saving persists it and verifies a separate GET readback.

## Exact local installation

From the audited Git checkout containing this implementation, run:

```text
python -B scripts/register_publication_review.py --expected-commit <checkout-full-HEAD> --runtime-root <deployed-code-root> --source-root <frozen-product-evidence-root> --offer-id <exact-offer> --binding-path <absolute-binding-json> --expected-sha256 <binding-sha256>
```

The default is a read-only dry run. Add `--apply` to install the one-product
projection and pending review locally. The integrator supplies these values;
the user never types hashes or paths in the review page. Deploy the matching
code before exposing this registration. This command does not start, stop or
replace any service and does not import databases, credentials or policies.

Installation uses an explicit product JSON allowlist. It copies approved facts
and report evidence for this offer, then atomically installs the completed
registration. The source bytes remain unchanged. Repeating an identical install
only reads; a conflicting or incomplete existing registration fails closed.
An interrupted staging directory is not an installed registration and must not
be renamed manually into place.

## Authoritative records and downstream use

The registry is `data/r2_candidate_reviews/<offer>/registration.json` under the
runtime root. Its `project` directory contains the projected workbench state and
`reports/product-preparation/<offer>/r2-candidate-adoption.json`. That adoption
file is the sole current decision store for this candidate. It records the
binding, frozen R1 revision/digest, complete image identities, output digests,
target lists, decisions and review revision. It never grants publication
authority. Source paths and hashes are retained in registration, not accepted
from browser requests.

Saves hold an OS lock on a stable `decision.lock` file until the transaction
finishes: Windows locks byte 0 for length 1; POSIX uses `flock`. Initialization
occurs only under the acquired lock, without truncation or replacement. The
file remains after a save; its existence is not ownership. A crashed process
releases the OS lock, so a retry can reconcile the same request. Stop older
handler versions before starting this version; do not remove active lock files.

`publication_r3_image_bridge.load_r2_documents` resolves a registered offer to
the same project and checks that adoption file before its existing QA identity
validation. Pending/removed images produce `R2_CANDIDATE_REVIEW_REQUIRED`.
After all images are kept, an old translation receipt still produces
`R2_ADOPTED_ASSET_RECEIPT_REQUIRED`. Keeping images does not create replacement
translation, QA, upload, COMMON or marketplace receipts. Subsequent R2 evidence
must describe these adopted bytes and pass the existing R3 validator in this
same project. A caller using an explicit reports root must use the registered
project's reports root as well.

The local registration exposes image review only. Frozen fact/source edits and
other legacy workspace POST actions are blocked for this registered offer;
unregistered products retain their previous routing. Later publication work
must use the governed R2/R3 workflow and its current authority checks. This
installation does not activate historical autopilot configuration.

## Validation

Run the candidate service, HTTP and actual browser tests together with the R2
identity and release-control suites. Use non-secret test configuration and
isolated databases. `test_publication_r2_review_browser.py` exercises a generated
product fixture with the real local decision handler and store. Its image,
radio-only, save, duplicate click, stale-tab, reload and viewport checks do not
constitute user approval. Real-product browser inspection must be read-only;
use a separately named synthetic runtime for any click/save rehearsal.
