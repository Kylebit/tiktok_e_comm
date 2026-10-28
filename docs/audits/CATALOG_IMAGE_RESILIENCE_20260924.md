# Catalog proxy deadline correction

Exact baseline: `0ee81f8dd6fe118338c42cdcc8af75508b748953`. Independent review rejected `87ab9414`. Its browser direct-fetch fallback, URL-disclosure endpoint and fallback tests are removed. Final server handler and catalog JavaScript are identical to baseline. Do not integrate `87ab9414` independently.

Only `shared_platform/catalog_images.py` has a production delta against baseline:

- Registered-key cache and all-public-DNS checks remain. At most two distinct public IPs may be tried for timeout, connection or SSL EOF errors. TLS verification, no redirects, allowed MIME, 8 MiB body limit and real PIL format/dimension validation remain. Certificate and content failures do not retry another address.
- One absolute eight-second deadline is shared by lock/semaphore waiting, DNS waiting, connect, TLS, request writes, response headers, content-length/chunked body reads and both IP attempts. Every socket wait uses the smaller of four seconds or remaining time. A raw reader below HTTP buffering recalculates remaining time for each read; byte trickles cannot renew the allowance.
- OS DNS calls cannot be cancelled portably. At most four daemon resolvers may remain in the OS; callers and requests waiting for resolver capacity still time out at the same deadline. Late DNS results never continue to connect or cache an image.
- Response files and connections close on success/error. A real socketpair case verifies Python socket-file lifetime when Connection: close closes the connection before the body is consumed.

The deadline bounds network waits; Python scheduling and synchronous local cache/PIL work are not preemptive wall-clock guarantees. Late responses are never accepted after the deadline. If all addresses fail, honest image failure and existing registered-candidate fallback remain. No browser direct network fallback is restored.

Validation uses synthetic data and lowest socket/DNS replacements. Three actual-parser tests against `87ab9414` incorrectly accepted slow headers, slow bodies and cumulative two-IP overrun. After correction, **67 related cases passed, zero skipped**, covering bounded DNS capacity, TLS/send/header/content-length/chunked timing, second-IP verified caching, public/private DNS and 302/MIME/PIL rejection, source endpoint removal, real socket-file lifetime, and the existing real Chromium proxy-only image journey.

Evidence: `D:/OrbitEvidence/convergence-20260919/endgame-20260924/catalog-image-resilience/`: `deadline-red.xml` (3 fail / 10 pass), `deadline-final2.xml` (67 pass), `DEADLINE_RECEIPT.json`. Set: `test_catalog_image_deadline.py`, `test_catalog_sku_directory.py`, `test_catalog_archive.py`, `test_operations_web_only.py`. Earlier 66-case and 67-case runs overlap; do not sum. Test network/SQLite guards and Chromium interception prevent real provider/production access. No real product image request, production DB/cache change or service restart occurred in implementation/testing.

Release limits: the coordinator separately observed old `0ee` fresh-preview default rows initially 48/50 with 9 proxy 502s; two failed keys later returned 200 and a warm run reached 50/50. Those are old-baseline observations, not candidate acceptance. The final candidate still requires independent review, full integrated gates and an actually new isolated cold-cache default-50-row browser acceptance. Synthetic passing tests do not prove CDN reachability or all live images. Production switching remains blocked until the exact gates pass.
