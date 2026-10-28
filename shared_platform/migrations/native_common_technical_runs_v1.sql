-- Explicit installation only. No service path executes this migration.
-- Existing database, foreign_keys OFF before BEGIN; foreign_key_check after
-- commit and before re-enabling writers. Owned validation must preserve all old
-- rows and their referenced approval identities. No approval rows are created.
BEGIN IMMEDIATE;
CREATE TABLE release_runs_native_v1 (
    run_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE,
    approval_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('PENDING','RUNNING','PARTIAL_FAILED','FAILED','SUCCEEDED','SUPERSEDED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    technical_admission_json TEXT,
    technical_admission_digest TEXT,
    technical_execution_state TEXT NOT NULL DEFAULT 'LEGACY' CHECK (
        technical_execution_state IN ('LEGACY','RESERVED','READONLY_RESERVED','UNKNOWN','CONFIRMED_WRITE','READONLY_REUSE','PROVEN_NOT_DISPATCHED')),
    CHECK ((approval_id IS NOT NULL AND technical_admission_json IS NULL
            AND technical_admission_digest IS NULL AND technical_execution_state='LEGACY')
        OR (approval_id IS NULL AND technical_admission_json IS NOT NULL
            AND technical_admission_digest IS NOT NULL AND technical_execution_state<>'LEGACY')),
    FOREIGN KEY (plan_id) REFERENCES release_plans(plan_id),
    FOREIGN KEY (approval_id) REFERENCES release_approvals(approval_id)
);
INSERT INTO release_runs_native_v1(run_id,plan_id,approval_id,status,created_at,updated_at,completed_at)
SELECT run_id,plan_id,approval_id,status,created_at,updated_at,completed_at FROM release_runs;
DROP TABLE release_runs;
ALTER TABLE release_runs_native_v1 RENAME TO release_runs;
CREATE INDEX IF NOT EXISTS idx_release_runs_status
    ON release_runs(status, updated_at DESC);
CREATE TRIGGER IF NOT EXISTS trg_release_run_identity_immutable
BEFORE UPDATE OF run_id, plan_id, approval_id, created_at
ON release_runs
BEGIN
    SELECT RAISE(ABORT, 'release run identity is immutable');
END;
CREATE TRIGGER release_runs_native_binding_immutable BEFORE UPDATE OF
    plan_id,approval_id,technical_admission_json,technical_admission_digest
    ON release_runs WHEN OLD.approval_id IS NULL
    BEGIN SELECT RAISE(ABORT,'immutable native COMMON technical binding'); END;
CREATE TRIGGER release_runs_native_common_scope BEFORE INSERT ON release_runs
    WHEN NEW.approval_id IS NULL
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM release_plans WHERE plan_id=NEW.plan_id
                AND target_labels_json='["miaoshou:COMMON"]')
            THEN RAISE(ABORT,'native technical run requires exact COMMON scope') END;
    END;
COMMIT;
