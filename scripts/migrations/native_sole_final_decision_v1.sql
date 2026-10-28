-- Explicit maintenance installation only. Never called by constructor/startup/GET.
-- Existing ReleaseStore schema and native technical migration must already exist.
PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;
CREATE TABLE native_sole_final_nonces (
        nonce_digest TEXT PRIMARY KEY, review_digest TEXT NOT NULL,
        review_json TEXT NOT NULL, review_sha256 TEXT NOT NULL,
        plan_id TEXT NOT NULL, owner_sid TEXT NOT NULL, session_id TEXT NOT NULL,
        instance_id TEXT NOT NULL, expires_at_epoch INTEGER NOT NULL,
        current_read_json TEXT NOT NULL, current_read_sha256 TEXT NOT NULL,
        decision_id TEXT, FOREIGN KEY(plan_id) REFERENCES release_plans(plan_id),
        FOREIGN KEY(decision_id) REFERENCES native_sole_final_decisions(decision_id));

CREATE TABLE native_sole_final_decisions (
        decision_id TEXT PRIMARY KEY, review_digest TEXT NOT NULL UNIQUE,
        review_json TEXT NOT NULL, owner_sid TEXT NOT NULL,
        plan_id TEXT NOT NULL UNIQUE, approval_id TEXT NOT NULL UNIQUE,
        snapshot_digest TEXT NOT NULL, created_at_epoch INTEGER NOT NULL,
        FOREIGN KEY(plan_id) REFERENCES release_plans(plan_id),
        FOREIGN KEY(approval_id) REFERENCES release_approvals(approval_id));

CREATE TRIGGER native_sole_final_decisions_no_update BEFORE UPDATE ON native_sole_final_decisions BEGIN SELECT RAISE(ABORT,'immutable native final decision'); END;

CREATE TRIGGER native_sole_final_decisions_no_delete BEFORE DELETE ON native_sole_final_decisions BEGIN SELECT RAISE(ABORT,'immutable native final decision'); END;

CREATE TRIGGER native_sole_final_nonces_no_delete BEFORE DELETE ON native_sole_final_nonces BEGIN SELECT RAISE(ABORT,'immutable native final nonce'); END;

CREATE TRIGGER native_sole_final_nonces_consume_only BEFORE UPDATE ON native_sole_final_nonces
        WHEN NEW.nonce_digest IS NOT OLD.nonce_digest OR NEW.review_digest IS NOT OLD.review_digest
        OR NEW.review_json IS NOT OLD.review_json OR NEW.review_sha256 IS NOT OLD.review_sha256
        OR NEW.plan_id IS NOT OLD.plan_id OR NEW.owner_sid IS NOT OLD.owner_sid
        OR NEW.session_id IS NOT OLD.session_id OR NEW.instance_id IS NOT OLD.instance_id
        OR NEW.expires_at_epoch IS NOT OLD.expires_at_epoch OR OLD.decision_id IS NOT NULL
        OR NEW.current_read_json IS NOT OLD.current_read_json
        OR NEW.current_read_sha256 IS NOT OLD.current_read_sha256
        OR NEW.decision_id IS NULL
        BEGIN SELECT RAISE(ABORT,'native final nonce consume only'); END;
COMMIT;
