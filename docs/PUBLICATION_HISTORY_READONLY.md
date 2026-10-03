# Maintenance publication history

The existing product workspace exposes a read-only history section below its publication result area in maintenance mode. It does not change the current candidate, approval, plan, retry controls, or provider executors.

`GET /api/product-workspace/publication-history?offer_id=<digits>` is served only by the maintenance handler. It uses the explicit `ORBIT_REPORT_STORE_PATH` from the deployment's `report_store_path`, and the explicit `publication_history_root` deployment field for immutable report files. The latter is exported as `ORBIT_PUBLICATION_HISTORY_ROOT`. Neither is inferred from the COMMON release root. Missing sources are unavailable and are never created.

Each report is independently verified using the existing immutable report reader. Valid reports remain visible if another report fails validation; failed reports are counted as blocked, never replayed or rewritten. Relative report file paths retain the existing traversal and file-identity checks. All results have `execution_authority=false`.

`AVAILABLE`, `PARTIALLY_BLOCKED`, `INTEGRITY_BLOCKED`, and `UNAVAILABLE` describe historical evidence only. A historical published result never makes the current candidate publishable. Existing business write-store resolution remains unchanged.
