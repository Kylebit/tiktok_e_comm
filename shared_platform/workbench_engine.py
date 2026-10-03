"""Durable orchestration on the existing WorkbenchStore, not business authority.

Workers are trusted server-side adapters. HTTP clients cannot claim work or
manufacture domain approval receipts. All business writes remain domain-owned.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from urllib.parse import quote

from shared_platform.workbench_store import WorkbenchStore, _json, _now

TEMPLATES = {
    "publication": {"label": "新品上架", "steps": [("facts", "准备商品事实"), ("images", "准备与审核图片"), ("release", "审核并发布"), ("readback", "核验发布结果")]},
    "delisting": {"label": "商品下架", "steps": [("identify", "核对商品与店铺范围"), ("delist", "执行下架"), ("readback", "核验下架结果")]},
    "profit": {"label": "月度利润", "steps": [("coverage", "核实完整结算截止日"), ("inputs", "核对成本与广告资料"), ("calculate", "计算并复核利润"), ("report", "生成利润报告")]},
}


def _product_resource_identity(resource):
    """Describe a locked product target without inferring its external result."""
    if not isinstance(resource, str) or not resource.startswith('product:'):
        return None
    try:
        target, sku = json.loads(resource[len('product:'):])
    except (TypeError, ValueError):
        return None
    if not all(isinstance(value, str) and 0 < len(value) <= 128 for value in (target, sku)):
        return None
    return {'target': target, 'sku': sku}
SCHEMA = """
CREATE TABLE IF NOT EXISTS workbench_execution (
 task_id TEXT PRIMARY KEY REFERENCES workbench_tasks(task_id), template TEXT NOT NULL,
 scope_json TEXT NOT NULL, version_json TEXT NOT NULL, request_digest TEXT NOT NULL,
 state TEXT NOT NULL, step_index INTEGER NOT NULL DEFAULT 0, steps_json TEXT NOT NULL,
 action_json TEXT, checkpoint_json TEXT NOT NULL DEFAULT '{}', result_url TEXT NOT NULL DEFAULT '',
 worker TEXT, lease_token TEXT, lease_until REAL, external_started INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS workbench_profit_requests (
 task_id TEXT PRIMARY KEY REFERENCES workbench_execution(task_id),
 request_scope_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS workbench_profit_requests_no_update
BEFORE UPDATE ON workbench_profit_requests
BEGIN SELECT RAISE(ABORT, 'profit request scope is immutable'); END;
CREATE TRIGGER IF NOT EXISTS workbench_profit_requests_no_delete
BEFORE DELETE ON workbench_profit_requests
BEGIN SELECT RAISE(ABORT, 'profit request scope is immutable'); END;
CREATE TABLE IF NOT EXISTS workbench_resource_locks (
 resource TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES workbench_tasks(task_id)
);
CREATE TABLE IF NOT EXISTS workbench_executors (
 worker TEXT PRIMARY KEY, templates_json TEXT NOT NULL, version_json TEXT NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workbench_receipts (
 receipt_key TEXT PRIMARY KEY, task_id TEXT NOT NULL, digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workbench_review_identity (
 task_id TEXT PRIMARY KEY REFERENCES workbench_execution(task_id),
 review_mode TEXT NOT NULL CHECK(review_mode='single-final-review/v1'),
 generation INTEGER NOT NULL CHECK(generation > 0)
);
CREATE TABLE IF NOT EXISTS workbench_external_tasks (
 task_id TEXT PRIMARY KEY REFERENCES workbench_tasks(task_id), external_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workbench_domain_operations (
 operation_id TEXT PRIMARY KEY, resources_json TEXT NOT NULL, owner_task_id TEXT,
 state TEXT NOT NULL, readback_ref TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workbench_domain_locks (
 resource TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES workbench_domain_operations(operation_id)
);
"""


def safe_url(value):
    value = str(value or "")
    if value and (not value.startswith("/") or value.startswith("//") or "\\" in value or any(ord(c) < 32 for c in value)):
        raise ValueError("result/review URL must be a local absolute route")
    return value


class ReceiptSnapshotUnavailable(ValueError):
    """A non-mutating, consistent source image cannot be established."""


def _receipt_database_image(path):
    """Capture a bounded rollback-mode image without SQLite opening the source.

    mode=ro can create/update WAL sidecars. WAL, rollback journals and concurrent
    file changes therefore fail closed; this reader never checkpoints a database
    or labels a mutable source immutable. SQLite only sees the resulting bytes.
    """
    import os
    from shared_platform.immutable_approval_files import require_local_path

    maximum = 64 * 1024 * 1024
    def no_journals():
        for suffix in ('-wal', '-shm', '-journal'):
            if require_local_path(path.with_name(path.name + suffix), root=path.parent) is not None:
                raise ReceiptSnapshotUnavailable('journal-backed source needs a stable snapshot')

    def signature(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)

    before = require_local_path(path, root=path.parent)
    if before is None:
        raise KeyError('receipt database missing')
    if before.st_size > maximum:
        raise ReceiptSnapshotUnavailable('receipt database exceeds snapshot limit')
    no_journals()
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(maximum + 1)
        stream.seek(0)
        second_digest = hashlib.sha256(stream.read(maximum + 1)).digest()
        after = os.fstat(stream.fileno())
    final = require_local_path(path, root=path.parent)
    no_journals()
    # Windows fstat/lstat may expose different ctime semantics; compare that
    # field within each API, and device/inode/size/mtime across all observations.
    if (final is None or any(signature(info) != signature(before) for info in (opened, after, final))
            or final.st_ctime_ns != before.st_ctime_ns or after.st_ctime_ns != opened.st_ctime_ns
            or len(raw) > maximum or hashlib.sha256(raw).digest() != second_digest):
        raise ReceiptSnapshotUnavailable('receipt database changed during snapshot')
    if len(raw) < 100 or raw[:16] != b'SQLite format 3\x00':
        raise ValueError('invalid receipt database format')
    # Never omit committed WAL pages or rewrite the SQLite header to make an
    # incomplete image deserialize. The normal WorkbenchStore uses rollback mode.
    if raw[18:20] != b'\x01\x01':
        raise ReceiptSnapshotUnavailable('journal mode requires a stable snapshot')
    return raw


class WorkbenchEngine:
    def __init__(self, path, release_identity, *, clock=time.time):
        self.store = WorkbenchStore(path)
        self.release = dict(release_identity)
        if not self.release.get("code_version"):
            raise ValueError("release identity requires code_version")
        self.clock = clock

    @contextmanager
    def transaction(self):
        conn = self.store._connect()
        try:
            conn.executescript(SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _event(self, conn, task_id, event, detail):
        self.store._event(conn, task_id, event, detail)

    def _row(self, conn, task_id):
        row = conn.execute("SELECT * FROM workbench_execution WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return dict(row)

    def _require_version(self, row):
        if json.loads(row["version_json"]) != self.release:
            raise ValueError("task belongs to another pinned runtime version")

    @staticmethod
    def _review_identity(conn, task_id):
        identity = conn.execute("SELECT review_mode,generation FROM workbench_review_identity WHERE task_id=?",
                                (task_id,)).fetchone()
        return dict(identity) if identity else {"review_mode": "legacy", "generation": 0}

    def worker_admission_cutoff(self):
        """Pin the first worker activation time for this exact release identity.

        A restart must not strand tasks created after the first activation, and
        an older queue must not become eligible merely because a worker starts.
        The immediate transaction also serializes concurrent first activations.
        """
        version = _json({key: self.release[key] for key in sorted(self.release)})
        with self.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS workbench_worker_admissions ("
                         "version_json TEXT PRIMARY KEY, cutoff_at TEXT NOT NULL)")
            conn.execute("INSERT OR IGNORE INTO workbench_worker_admissions VALUES(?,?)",
                         (version, _now()))
            return conn.execute("SELECT cutoff_at FROM workbench_worker_admissions WHERE version_json=?",
                                (version,)).fetchone()["cutoff_at"]

    def _state(self, conn, task_id, state, reason=""):
        status = {"queued": "todo", "running": "in_progress", "waiting_domain": "in_progress", "waiting_user": "waiting_approval", "failed": "blocked", "reconciliation_required": "blocked", "completed": "done", "cancelled": "cancelled"}[state]
        conn.execute("UPDATE workbench_execution SET state=? WHERE task_id=?", (state, task_id))
        conn.execute("UPDATE workbench_tasks SET status=?,blocked_reason=?,updated_at=?,completed_at=? WHERE task_id=?", (status, reason, _now(), _now() if state == "completed" else None, task_id))

    def _expire(self, conn):
        for row in conn.execute("SELECT * FROM workbench_execution WHERE state='running' AND lease_until<=?", (self.clock(),)).fetchall():
            state = "reconciliation_required" if row["external_started"] else "queued"
            self._state(conn, row["task_id"], state, "执行器失联，先核对外部结果" if row["external_started"] else "执行器租约过期，等待安全恢复")
            conn.execute("UPDATE workbench_execution SET worker=NULL,lease_token=NULL,lease_until=NULL WHERE task_id=?", (row["task_id"],))
            if not row["external_started"]:
                conn.execute("DELETE FROM workbench_resource_locks WHERE task_id=?", (row["task_id"],))
            self._event(conn, row["task_id"], "lease_expired", {"state": state})

    @staticmethod
    def _scope(template, scope):
        if not isinstance(scope, dict):
            raise ValueError("scope must be an object")
        scope = dict(scope)
        allowed = {"skus", "shops", "offer_id", "month", "platforms", "sites"}
        if set(scope) - allowed:
            raise ValueError("unknown task scope fields")
        for key in ("offer_id", "month"):
            if key in scope and not isinstance(scope[key], str):
                raise ValueError(key + " must be a string")
        for key in ("platforms", "sites"):
            if key in scope:
                if not isinstance(scope[key], list) or any(not isinstance(v, str) or not v.strip() for v in scope[key]):
                    raise ValueError(key + " must be a list of identities")
                scope[key] = sorted(set(v.strip() for v in scope[key]))
        for key in ("skus", "shops"):
            values = scope.get(key, [])
            if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError(key + " must contain exact string identities")
            scope[key] = sorted(set(v.strip() for v in values))
        if template == "delisting" and (not scope["skus"] or not scope["shops"]):
            raise ValueError("下架需要准确 SKU 与平台/店铺身份")
        if template == "publication" and not (scope.get("offer_id") or scope["skus"]):
            raise ValueError("上架需要采集箱商品 ID 或内部 SKU")
        if template == "profit":
            import re
            if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(scope.get("month", ""))):
                raise ValueError("利润需要 YYYY-MM 月份")
        return scope

    def create(self, payload):
        return self._create(payload)[0]

    def create_for_explicit_post(self, payload, *, allow_new=True):
        """Trusted HTTP creation outcome, taken in the insertion transaction."""
        return self._create(payload, explicit_post=True, allow_new=allow_new)

    def _create(self, payload, *, explicit_post=False, allow_new=True):
        template = payload.get("template")
        if template not in TEMPLATES:
            raise ValueError("unknown template")
        key = str(payload.get("source_key", "")).strip()
        if not key:
            raise ValueError("source_key is required for idempotency")
        scope = self._scope(template, payload.get("scope", {}))
        title = str(payload.get("title") or TEMPLATES[template]["label"]).strip()
        digest = hashlib.sha256(_json([template, scope, title]).encode()).hexdigest()
        reuse_scope = payload.get("reuse_existing_scope") is True and template == "profit"
        created = False
        with self.transaction() as conn:
            old = conn.execute("SELECT task_id FROM workbench_tasks WHERE source_key=?", (key,)).fetchone()
            if old:
                row = self._row(conn, old["task_id"])
                if row["request_digest"] != digest:
                    raise ValueError("idempotency key already binds a different request")
                task_id = old["task_id"]
            else:
                matches = []
                ambiguous_legacy = False
                if reuse_scope:
                    # scope_json is the worker's resolved identity and may gain
                    # platforms/sites. Compare only the immutable user request.
                    for candidate in conn.execute(
                        "SELECT e.task_id,e.scope_json,r.request_scope_json "
                        "FROM workbench_execution AS e LEFT JOIN workbench_profit_requests AS r "
                        "USING(task_id) WHERE e.template='profit' AND e.state!='cancelled'"):
                        requested = candidate["request_scope_json"]
                        if requested is None:
                            old = json.loads(candidate["scope_json"])
                            # Historical resolvers may have replaced aliases;
                            # disjoint current shop strings do not prove intent.
                            if old.get("month") == scope.get("month"):
                                ambiguous_legacy = True
                        elif json.loads(requested) == scope:
                            matches.append(candidate["task_id"])
                    if ambiguous_legacy:
                        raise ValueError("legacy profit task original request scope unavailable; reconcile first")
                    if len(matches) > 1:
                        raise ValueError("multiple existing profit tasks match this month and scope; reconcile first")
                if matches:
                    task_id = matches[0]
                else:
                    if not allow_new:
                        raise ValueError('新商品准备任务已满，请稍后提交；未创建新任务')
                    task_id = self.store._next_id(conn)
                    url = "/product-workspace?offer_id=" + quote(str(scope.get("offer_id", ""))) if template == "publication" else "/profit" if template == "profit" else "/catalog"
                    conn.execute("INSERT INTO workbench_tasks(task_id,title,project,business_line,priority,status,source_key,created_at,updated_at,related_url) VALUES(?,?,?,?,?,?,?,?,?,?)", (task_id, title, "OrbitHive", template, "P2", "todo", key, _now(), _now(), url))
                    steps = [{"key": k, "label": label, "state": "pending"} for k, label in TEMPLATES[template]["steps"]]
                    conn.execute("INSERT INTO workbench_execution(task_id,template,scope_json,version_json,request_digest,state,steps_json) VALUES(?,?,?,?,?,'queued',?)", (task_id, template, _json(scope), _json(self.release), digest, _json(steps)))
                    if template == "profit":
                        conn.execute("INSERT INTO workbench_profit_requests(task_id,request_scope_json) VALUES(?,?)",
                                     (task_id, _json(scope)))
                    self._event(conn, task_id, "workflow_created", {"template": template, "version": self.release})
                    created = True
                    if explicit_post and template == 'publication':
                        self._event(conn, task_id, 'explicit_new_post_preparation', {
                            'request_digest':digest, 'source_key':key,
                            'scope':scope, 'release':self.release})
                    if explicit_post and template == 'profit':
                        self._event(conn, task_id, 'explicit_new_post_profit_readonly', {
                            'request_digest':digest, 'source_key':key,
                            'scope':scope, 'release':self.release})
                    if explicit_post and template == 'delisting':
                        self._event(conn, task_id, 'explicit_new_post_delisting', {
                            'request_digest':digest, 'source_key':key,
                            'scope':scope, 'release':self.release})
        return self.get(task_id), created

    def explicit_new_post_task_ids(self):
        """Recover only trusted, exact-version new POST grants; never dashboards."""
        with self.transaction() as conn:
            rows=conn.execute('SELECT g.task_id,g.detail_json,e.request_digest AS current_digest,'
                'e.scope_json AS current_scope,t.source_key AS current_key FROM workbench_events g '
                'JOIN workbench_execution e USING(task_id) JOIN workbench_tasks t USING(task_id) '
                "WHERE g.event_type='explicit_new_post_preparation' AND e.version_json=? "
                "AND e.template='publication' AND e.state NOT IN ('completed','cancelled','failed') "
                'ORDER BY g.id LIMIT 33',(_json(self.release),)).fetchall()
            if len(rows)>32:raise ValueError('NATIVE_NEW_POST_RECOVERY_CAPACITY_EXCEEDED')
            result=[]
            for row in rows:
                detail=json.loads(row['detail_json']);current=json.loads(row['current_scope'])
                if (not isinstance(detail,dict) or not isinstance(detail.get('scope'),dict)
                        or not isinstance(current,dict)):
                    raise ValueError('NATIVE_NEW_POST_RECOVERY_GRANT_CHANGED')
                original=detail.get('scope') or {}
                if (set(detail)!={'request_digest','source_key','scope','release'}
                        or detail['release']!=self.release or row['task_id'] in result
                        or detail['request_digest']!=row['current_digest']
                        or detail['source_key']!=row['current_key']
                        or original.get('offer_id')!=current.get('offer_id')
                        or original.get('shops')!=current.get('shops')):
                    raise ValueError('NATIVE_NEW_POST_RECOVERY_GRANT_CHANGED')
                result.append(row['task_id'])
            return tuple(result)

    def explicit_new_post_profit_task_ids(self):
        """Recover only exact new profit grants, against the immutable request."""
        with self.transaction() as conn:
            rows = conn.execute(
                'SELECT g.task_id,g.detail_json,e.request_digest,t.source_key,r.request_scope_json '
                'FROM workbench_events g JOIN workbench_execution e USING(task_id) '
                'JOIN workbench_tasks t USING(task_id) '
                'LEFT JOIN workbench_profit_requests r USING(task_id) '
                "WHERE g.event_type='explicit_new_post_profit_readonly' AND e.version_json=? "
                "AND e.template='profit' AND e.state NOT IN ('completed','cancelled','failed') "
                'ORDER BY g.id LIMIT 33', (_json(self.release),)).fetchall()
            if len(rows) > 32:
                raise ValueError('NATIVE_NEW_PROFIT_RECOVERY_CAPACITY_EXCEEDED')
            result = []
            for row in rows:
                detail = json.loads(row['detail_json'])
                if (not isinstance(detail, dict)
                        or set(detail) != {'request_digest','source_key','scope','release'}
                        or row['request_scope_json'] is None
                        or detail['scope'] != json.loads(row['request_scope_json'])
                        or detail['release'] != self.release
                        or detail['request_digest'] != row['request_digest']
                        or detail['source_key'] != row['source_key']
                        or row['task_id'] in result):
                    raise ValueError('NATIVE_NEW_PROFIT_RECOVERY_GRANT_CHANGED')
                result.append(row['task_id'])
            return tuple(result)

    def explicit_new_post_delisting_task_ids(self):
        """Exact current-version delisting grants; no historic queue admission."""
        with self.transaction() as conn:
            rows=conn.execute('SELECT g.task_id,g.detail_json,e.request_digest,e.scope_json,t.source_key '
                'FROM workbench_events g JOIN workbench_execution e USING(task_id) '
                'JOIN workbench_tasks t USING(task_id) '
                "WHERE g.event_type='explicit_new_post_delisting' AND e.version_json=? "
                "AND e.template='delisting' AND e.state NOT IN ('completed','cancelled','failed') "
                'ORDER BY g.id LIMIT 33',(_json(self.release),)).fetchall()
            if len(rows)>32: raise ValueError('NATIVE_NEW_DELIST_RECOVERY_CAPACITY_EXCEEDED')
            result=[]
            for row in rows:
                detail=json.loads(row['detail_json'])
                if (not isinstance(detail,dict) or set(detail)!={'request_digest','source_key','scope','release'}
                        or detail['scope']!=json.loads(row['scope_json']) or detail['release']!=self.release
                        or detail['request_digest']!=row['request_digest'] or detail['source_key']!=row['source_key']
                        or row['task_id'] in result):
                    raise ValueError('NATIVE_NEW_DELIST_RECOVERY_GRANT_CHANGED')
                result.append(row['task_id'])
            return tuple(result)

    def _retry_eligible(self, conn, row):
        """Conservative ledger-only retry admission for a local pre-approval step.

        Later steps need a frozen approval and per-target readback contract that
        this generic task ledger cannot yet prove. Never infer those from a
        checkpoint string or a task-level receipt digest.
        """
        try:
            return self._retry_eligible_checked(conn, row)
        except (ValueError, TypeError, KeyError, IndexError):
            return False

    def _retry_eligible_checked(self, conn, row):
        if (row["state"] != "failed" or row["template"] != "delisting"
                or row["external_started"] or row["action_json"]
                or row["worker"] or row["lease_token"] or row["lease_until"]
                or row["step_index"] != 0 or json.loads(row["checkpoint_json"]) != {}
                or json.loads(row["version_json"]) != self.release
                or self._review_identity(conn, row["task_id"])["review_mode"] != "legacy"):
            return False
        scope = json.loads(row["scope_json"])
        steps = json.loads(row["steps_json"])
        if (not isinstance(scope, dict) or not isinstance(steps, list)
                or [step.get("key") for step in steps if isinstance(step, dict)]
                != [key for key, _ in TEMPLATES["delisting"]["steps"]]
                or len(steps) != len(TEMPLATES["delisting"]["steps"])
                or any(step.get("state") == "completed" for step in steps)
                or not all(isinstance(scope.get(key), list) and scope[key]
                           and all(isinstance(value, str) and value for value in scope[key])
                           for key in ("shops", "skus"))):
            return False
        task_id = row["task_id"]
        if conn.execute("SELECT 1 FROM workbench_external_tasks WHERE task_id=?", (task_id,)).fetchone():
            return False
        if conn.execute("SELECT 1 FROM workbench_receipts WHERE task_id=?", (task_id,)).fetchone():
            return False
        if conn.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=?", (task_id,)).fetchone():
            return False
        events = conn.execute("SELECT event_type,detail_json FROM workbench_events WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
        types = [event["event_type"] for event in events]
        if (types.count("workflow_created") != 1 or "claimed" not in types
                or not types or types[-1] != "execution_failed"
                or any(name in types for name in (
                    "external_action_started", "external_reconciled", "step_completed",
                    "checkpoint_saved", "scope_bound",
                    "user_action_required", "domain_receipt_accepted",
                    "final_review_receipt_accepted", "domain_unknown_observed"))
                or json.loads(events[-1]["detail_json"]).get("state") != "failed"):
            return False
        live = conn.execute("SELECT templates_json,version_json FROM workbench_executors WHERE expires>?", (self.clock(),)).fetchall()
        if not any(row["template"] in json.loads(worker["templates_json"])
                   and json.loads(worker["version_json"]) == self.release for worker in live):
            return False
        resources = {"product:" + _json([shop, sku]) for shop in scope["shops"] for sku in scope["skus"]}
        if any(conn.execute("SELECT 1 FROM workbench_domain_locks WHERE resource=?", (resource,)).fetchone()
               or conn.execute("SELECT 1 FROM workbench_resource_locks WHERE resource=? AND task_id<>?", (resource, task_id)).fetchone()
               for resource in resources):
            return False
        # Also catch a corrupt or legacy unresolved operation with missing locks.
        for operation in conn.execute("SELECT resources_json FROM workbench_domain_operations WHERE state<>'completed'"):
            operation_resources = json.loads(operation["resources_json"])
            if not isinstance(operation_resources, list) or resources.intersection(operation_resources):
                return False
        return True

    def _project(self, conn, row):
        task = self.store._row(conn.execute("SELECT * FROM workbench_tasks WHERE task_id=?", (row["task_id"],)).fetchone())
        for name in ("scope", "version", "steps", "checkpoint"):
            task[name] = json.loads(row[name + "_json"])
        if row["template"] == "profit":
            requested = conn.execute("SELECT request_scope_json FROM workbench_profit_requests WHERE task_id=?",
                                     (row["task_id"],)).fetchone()
            task["request_scope"] = json.loads(requested["request_scope_json"]) if requested else None
        task.update(template=row["template"], execution_state=row["state"], current_step=task["steps"][min(row["step_index"], len(task["steps"]) - 1)]["key"], required_action=json.loads(row["action_json"]) if row["action_json"] else None, result_url=row["result_url"], worker=row["worker"], **self._review_identity(conn, row["task_id"]))
        claimed = conn.execute("SELECT detail_json,created_at FROM workbench_events WHERE task_id=? AND event_type='claimed' ORDER BY id DESC LIMIT 1", (row["task_id"],)).fetchone()
        task["last_executor"] = json.loads(claimed["detail_json"]).get("worker") if claimed else None
        task["last_claimed_at"] = claimed["created_at"] if claimed else None
        if row["state"] == "waiting_domain":
            task["pending_observation"] = task["required_action"]
            task["required_action"] = None
        if row["step_index"] < len(task["steps"]) and row["state"] in {"running", "waiting_user", "waiting_domain", "failed", "reconciliation_required"}:
            task["steps"][row["step_index"]]["state"] = row["state"]
        live = conn.execute("SELECT * FROM workbench_executors WHERE expires>?", (self.clock(),)).fetchall()
        connected = any(row["template"] in json.loads(w["templates_json"]) and json.loads(w["version_json"]) == task["version"] for w in live)
        task["executor_connected"] = connected
        task["allowed_actions"] = {"retry": self._retry_eligible(conn, row)}
        if task["review_mode"] != "legacy" and row["state"] == "queued":
            task["execution_state"] = "awaiting_execution_authority"
            task["executor_connected"] = False
            task["blocked_reason"] = "终审决议已记录；等待独立执行授权"
        external = conn.execute("SELECT external_json FROM workbench_external_tasks WHERE task_id=?", (row["task_id"],)).fetchone()
        if external:
            task["external_task"] = json.loads(external["external_json"])
            task["execution_state"] = "external_task"
            task["executor_connected"] = False
            task["blocked_reason"] = "由已关联任务执行；状态以所示观测时间为准"
            return task
        if row["state"] == "queued" and not connected and task["review_mode"] == "legacy":
            task["execution_state"] = "executor_offline"
            task["blocked_reason"] = "尚无匹配此版本的在线执行器；任务已保存"
        return task

    def get(self, task_id):
        with self.transaction() as conn:
            self._expire(conn)
            return self._project(conn, self._row(conn, task_id))

    def read_delisting_receipt_inputs(self, task_id):
        """Query a checked in-memory image; no SQLite connection to the source file."""
        import sqlite3
        from pathlib import Path
        path = Path(self.store.path).absolute()
        image = _receipt_database_image(path)
        if not hasattr(sqlite3.Connection, 'deserialize'):
            raise ReceiptSnapshotUnavailable('runtime cannot load an in-memory database image')
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA temp_store=MEMORY')
            conn.deserialize(image)
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            row = conn.execute('SELECT e.*, t.title, t.updated_at FROM workbench_execution e '
                               'JOIN workbench_tasks t USING(task_id) WHERE e.task_id=?', (task_id,)).fetchone()
            if row is None or row['template'] != 'delisting' or conn.execute(
                    'SELECT 1 FROM workbench_external_tasks WHERE task_id=?', (task_id,)).fetchone():
                raise KeyError(task_id)
            task = dict(row)
            for key in ('scope', 'version', 'steps', 'checkpoint'):
                task[key] = json.loads(task.pop(key + '_json'))
            if (any(not isinstance(task[key], dict) for key in ('scope', 'version', 'checkpoint'))
                    or not isinstance(task['steps'], list)
                    or any(not isinstance(step, dict) or
                           (step.get('checkpoint') is not None and not isinstance(step['checkpoint'], dict))
                           for step in task['steps'])):
                raise ValueError('invalid saved delisting task evidence')
            operations = [dict(op) for op in conn.execute(
                'SELECT o.*, (SELECT count(*) FROM workbench_domain_locks l WHERE l.operation_id=o.operation_id) '
                'AS locked_resource_count FROM workbench_domain_operations o WHERE owner_task_id=?', (task_id,))]
            return {'task': task, 'operations': operations}
        finally:
            conn.close()

    def dashboard(self):
        with self.transaction() as conn:
            self._expire(conn)
            tasks = [self._project(conn, dict(r)) for r in conn.execute("SELECT * FROM workbench_execution ORDER BY rowid DESC").fetchall()]
            live = [dict(r) for r in conn.execute("SELECT * FROM workbench_executors WHERE expires>?", (self.clock(),)).fetchall() if json.loads(r["version_json"]) == self.release]
            connected_templates = sorted({template for row in live for template in json.loads(row["templates_json"])})
            guard = conn.execute("SELECT COUNT(*) AS unresolved, "
                                 "COALESCE(SUM(CASE WHEN owner_task_id IS NULL THEN 1 ELSE 0 END),0) AS unattached "
                                 "FROM workbench_domain_operations WHERE state<>'completed'").fetchone()
            locked = conn.execute("SELECT COUNT(*) FROM workbench_domain_locks AS l "
                                  "JOIN workbench_domain_operations AS o ON o.operation_id=l.operation_id "
                                  "WHERE o.state<>'completed'").fetchone()[0]
            unresolved_rows = conn.execute(
                "SELECT o.operation_id,o.owner_task_id,o.state,o.created_at,"
                "o.resources_json,COUNT(l.resource) AS locked_resources "
                "FROM workbench_domain_operations AS o "
                "LEFT JOIN workbench_domain_locks AS l ON l.operation_id=o.operation_id "
                "WHERE o.state<>'completed' "
                "GROUP BY o.operation_id ORDER BY o.created_at,o.operation_id LIMIT 20"
            ).fetchall()
            unresolved = []
            for row in unresolved_rows:
                resources = json.loads(row['resources_json'])
                identities = [_product_resource_identity(resource) for resource in resources]
                targets = [identity for identity in identities if identity is not None]
                unresolved.append({"operation_id": row["operation_id"],
                                   "owner_task_id": row["owner_task_id"],
                                   "state": row["state"], "created_at": row["created_at"],
                                   "resource_count": len(resources),
                                   "locked_resource_count": row["locked_resources"],
                                   "affected_targets": targets[:30],
                                   "affected_targets_truncated": len(targets) > 30,
                                   "unparsed_resource_count": len(resources) - len(targets)})
        return {"tasks": tasks, "templates": [{"key": k, **v, "steps": [{"key": a, "label": b} for a, b in v["steps"]]} for k, v in TEMPLATES.items()], "executor": {"connected": bool(live), "templates": connected_templates}, "release": self.release,
                "domain_guard": {"unresolved_operation_count": guard["unresolved"],
                                 "unattached_operation_count": guard["unattached"],
                                 "locked_resource_count": locked},
                "domain_operations": unresolved,
                "domain_operations_truncated": guard["unresolved"] > len(unresolved)}

    def register_executor(self, worker, templates, version, ttl=60):
        if not worker or not templates or any(t not in TEMPLATES for t in templates) or ttl <= 0:
            raise ValueError("invalid executor registration")
        with self.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO workbench_executors VALUES(?,?,?,?)", (worker, _json(templates), _json(version), self.clock() + min(ttl, 300)))

    def claim(self, task_id, worker, ttl=60):
        with self.transaction() as conn:
            self._expire(conn)
            row = self._row(conn, task_id)
            if conn.execute("SELECT 1 FROM workbench_external_tasks WHERE task_id=?", (task_id,)).fetchone():
                return None
            if json.loads(row["version_json"]) != self.release:
                return None
            if row["state"] == "running" and row["worker"] == worker:
                return {"task_id": task_id, "lease_token": row["lease_token"]}
            executor = conn.execute("SELECT * FROM workbench_executors WHERE worker=? AND expires>?", (worker, self.clock())).fetchone()
            if (row["state"] != "queued" or self._review_identity(conn, task_id)["review_mode"] != "legacy"
                    or not executor or row["template"] not in json.loads(executor["templates_json"])
                    or json.loads(executor["version_json"]) != json.loads(row["version_json"])):
                return None
            scope = json.loads(row["scope_json"])
            # Profit consumes immutable snapshots and does not lock product mutations.
            resources = [] if row["template"] == "profit" else ["product:" + _json([shop, sku]) for shop in scope["shops"] for sku in scope["skus"]]
            if row["template"] == "publication" and scope.get("offer_id"):
                resources.append("product-offer:" + scope["offer_id"])
            for resource in resources:
                if conn.execute("SELECT 1 FROM workbench_domain_locks WHERE resource=?", (resource,)).fetchone():
                    conn.execute("UPDATE workbench_tasks SET blocked_reason=? WHERE task_id=?", ("等待同商品的现有业务操作完成回读", task_id))
                    return None
                lock = conn.execute("SELECT task_id FROM workbench_resource_locks WHERE resource=?", (resource,)).fetchone()
                if lock and lock["task_id"] != task_id:
                    conn.execute("UPDATE workbench_tasks SET blocked_reason=? WHERE task_id=?", ("等待同商品任务 " + lock["task_id"], task_id))
                    return None
            for resource in resources:
                conn.execute("INSERT OR IGNORE INTO workbench_resource_locks VALUES(?,?)", (resource, task_id))
            token = uuid.uuid4().hex
            conn.execute("UPDATE workbench_execution SET worker=?,lease_token=?,lease_until=? WHERE task_id=?", (worker, token, self.clock() + max(1, min(ttl, 300)), task_id))
            self._state(conn, task_id, "running")
            self._event(conn, task_id, "claimed", {"worker": worker})
            return {"task_id": task_id, "lease_token": token}

    def _lease(self, conn, task_id, token):
        row = self._row(conn, task_id)
        self._require_version(row)
        if row["state"] != "running" or not token or row["lease_token"] != token or row["lease_until"] <= self.clock():
            raise ValueError("stale or invalid lease")
        return row

    def heartbeat(self, task_id, token, ttl=60):
        with self.transaction() as conn:
            self._lease(conn, task_id, token)
            conn.execute("UPDATE workbench_execution SET lease_until=? WHERE task_id=?", (self.clock() + max(1, min(ttl, 300)), task_id))

    def record_checkpoint(self, task_id, token, checkpoint):
        if not isinstance(checkpoint, dict):
            raise ValueError("checkpoint must be an object")
        with self.transaction() as conn:
            self._lease(conn, task_id, token)
            conn.execute("UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?", (_json(checkpoint), task_id))
            self._event(conn, task_id, "checkpoint_saved", {"checkpoint": checkpoint})

    def _r1_technical_recovery_row(self, conn, task_id):
        """Read the original durable intent; copied task JSON is never authority."""
        from shared_platform.publication_rounds import canonical_digest

        row = self._row(conn, task_id)
        self._require_version(row)
        steps = json.loads(row['steps_json'])
        scope = json.loads(row['scope_json'])
        checkpoint = json.loads(row['checkpoint_json'])
        intent = checkpoint.get('r1_auto_intent') or {}
        unsigned = {key: value for key, value in intent.items() if key != 'intent_digest'}
        reference = intent.get('prepared_reference')
        if (row['template'] != 'publication' or row['state'] != 'reconciliation_required'
                or row['external_started'] or row['action_json'] or row['step_index'] != 0
                or steps[0]['key'] != 'facts' or intent.get('schema_version') != 'r1-task-technical-intent/v1'
                or intent.get('task_id') != task_id or intent.get('offer_id') != scope.get('offer_id')
                or intent.get('targets') != sorted(scope.get('shops') or [])
                or intent.get('release') != self.release
                or not isinstance(reference, str) or not reference.startswith('r1-prepared:')
                or (checkpoint.get('native_preparation') or {}).get('prepared_reference') != reference
                or not isinstance(intent.get('policy_digest'), str)
                or not isinstance(intent.get('review_digest'), str)
                or intent.get('intent_digest') != canonical_digest(unsigned)):
            raise ValueError('R1 original technical intent is incomplete or changed')
        owner = None
        own_event = False
        for event in conn.execute('SELECT e.task_id,e.detail_json,x.scope_json FROM workbench_events e '
                                  'JOIN workbench_execution x ON x.task_id=e.task_id '
                                  "WHERE e.event_type='checkpoint_saved' AND x.template='publication' ORDER BY e.id"):
            detail = json.loads(event['detail_json']).get('checkpoint') or {}
            event_scope = json.loads(event['scope_json'])
            if (event_scope.get('offer_id') == scope['offer_id']
                    and (detail.get('native_preparation') or {}).get('prepared_reference') == reference):
                if owner is None:
                    owner = event['task_id']
                if event['task_id'] == task_id and detail == checkpoint:
                    own_event = True
        if owner != task_id or not own_event:
            raise ValueError('R1 technical intent belongs to another task or has no original event')
        if conn.execute('SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state<>?',
                        (task_id, 'completed')).fetchone():
            raise ValueError('R1 task has an unresolved external domain operation')
        return row, scope, intent

    def read_r1_technical_recovery(self, task_id):
        with self.transaction() as conn:
            row, _, intent = self._r1_technical_recovery_row(conn, task_id)
            return {'task': self._project(conn, row), 'intent': intent}

    def resume_r1_technical_recovery(self, task_id, *, intent_digest, frozen):
        """Queue only this exact task after a trusted local domain readback."""
        with self.transaction() as conn:
            row, scope, intent = self._r1_technical_recovery_row(conn, task_id)
            if (intent_digest != intent['intent_digest'] or not isinstance(frozen, dict)
                    or frozen.get('offer_id') != scope['offer_id']
                    or frozen.get('targets') != intent['targets']
                    or frozen.get('prepared_reference') != intent['prepared_reference']
                    or not frozen.get('snapshot_digest')):
                raise ValueError('R1 technical recovery readback conflicts with original intent')
            self._state(conn, task_id, 'queued')
            self._event(conn, task_id, 'r1_technical_recovered', {
                'intent_digest': intent_digest, 'prepared_reference': intent['prepared_reference'],
                'snapshot_digest': frozen['snapshot_digest']})
        return self.get(task_id)

    def record_r1_technical_recovery_blocked(self, task_id, *, intent_digest, code):
        with self.transaction() as conn:
            _, _, intent = self._r1_technical_recovery_row(conn, task_id)
            if intent.get('intent_digest') != intent_digest:
                raise ValueError('R1 recovery intent changed before blocked receipt')
            self._event(conn, task_id, 'r1_technical_recovery_blocked', {
                'intent_digest': intent_digest, 'code': code})

    def reconcile_external(self, task_id, receipt, verifier):
        """Trusted domain readback clears an unknown outcome; never exposed in UI.

        A verified applied outcome is retained in the checkpoint. The domain
        adapter consumes it on resume, using its own idempotency/readback rules.
        """
        with self.transaction() as conn:
            row = self._row(conn, task_id)
            self._require_version(row)
            if row["state"] != "reconciliation_required" or receipt.get("outcome") not in {"applied", "not_applied"} or not receipt.get("provider_readback_ref") or not verifier(receipt, json.loads(row["scope_json"])):
                raise ValueError("verified domain reconciliation required")
            checkpoint = json.loads(row["checkpoint_json"])
            checkpoint["reconciliation"] = receipt
            conn.execute("UPDATE workbench_execution SET checkpoint_json=?,external_started=0 WHERE task_id=?", (_json(checkpoint), task_id))
            self._state(conn, task_id, "queued")
            self._event(conn, task_id, "external_reconciled", {"receipt": receipt})

    def mark_external_started(self, task_id, token):
        with self.transaction() as conn:
            row = self._lease(conn, task_id, token)
            scope = json.loads(row["scope_json"])
            if row["template"] == "profit" or not scope["skus"] or not scope["shops"]:
                raise ValueError("external writes require exact SKU/shop scope and a commerce workflow")
            conn.execute("UPDATE workbench_execution SET external_started=1 WHERE task_id=?", (task_id,))
            self._event(conn, task_id, "external_action_started", {"step": row["step_index"]})

    def complete_step(self, task_id, token, *, expected_step, checkpoint, result_url=""):
        result_url = safe_url(result_url)
        completion_digest = hashlib.sha256(_json([checkpoint, result_url]).encode()).hexdigest()
        with self.transaction() as conn:
            if conn.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state<>'completed'", (task_id,)).fetchone():
                raise ValueError("existing domain operation requires readback before task completion")
            row = self._row(conn, task_id)
            steps = json.loads(row["steps_json"])
            previous = next((step for step in steps if step["key"] == expected_step), None)
            if previous and previous["state"] == "completed":
                if previous.get("completion_digest") != completion_digest:
                    raise ValueError("completed step receipt conflicts with previous result")
                return self._project(conn, row)
            row = self._lease(conn, task_id, token)
            if steps[row["step_index"]]["key"] != expected_step:
                raise ValueError("step changed; reload current checkpoint")
            if row["external_started"] and not checkpoint.get("provider_readback_ref"):
                raise ValueError("external result needs provider readback before completion")
            steps[row["step_index"]]["state"] = "completed"
            steps[row["step_index"]]["checkpoint"] = checkpoint
            steps[row["step_index"]]["completion_digest"] = completion_digest
            index = row["step_index"] + 1
            done = index == len(steps)
            conn.execute("UPDATE workbench_execution SET steps_json=?,step_index=?,checkpoint_json=?,external_started=0,result_url=CASE WHEN ?<>'' THEN ? ELSE result_url END WHERE task_id=?", (_json(steps), index, _json(checkpoint), result_url, result_url, task_id))
            if done:
                self._state(conn, task_id, "completed")
                conn.execute("DELETE FROM workbench_resource_locks WHERE task_id=?", (task_id,))
            self._event(conn, task_id, "step_completed", {"step": steps[index - 1]["key"], "checkpoint": checkpoint})
        return self.get(task_id)

    def wait_for_user(self, task_id, token, *, kind, label, reason, url="", receipt_binding=None):
        if kind not in {"input", "review", "observe"} or not label:
            raise ValueError("specific user action required")
        if kind in {"review", "observe"} and not receipt_binding:
            raise ValueError("review needs domain snapshot/target binding")
        action = {"kind": kind, "label": label, "reason": reason, "url": safe_url(url), "receipt_binding": receipt_binding, "action_id": uuid.uuid4().hex}
        with self.transaction() as conn:
            row = self._lease(conn, task_id, token)
            if self._review_identity(conn, task_id)["review_mode"] != "legacy":
                raise ValueError("final review requires its original action path")
            if row["external_started"]:
                raise ValueError("reconcile external result before asking for input")
            conn.execute("UPDATE workbench_execution SET action_json=?,worker=NULL,lease_token=NULL,lease_until=NULL WHERE task_id=?", (_json(action), task_id))
            self._state(conn, task_id, "waiting_domain" if kind == "observe" else "waiting_user")
            self._event(conn, task_id, "domain_observation_pending" if kind == "observe" else "user_action_required", action)

    def wait_for_final_review(self, task_id, token, *, label, reason, receipt_binding, url=""):
        """Trusted adapter seam: freeze one release action and its server-owned identity.

        No HTTP handler or production adapter calls this in this package.
        """
        required = {"offer_id", "revision", "sku", "round1_digest", "round2_digest",
                    "targets", "common_plan_id", "common_payload_digest", "common_token_digest",
                    "preview_digest"}
        if (not label or not isinstance(receipt_binding, dict)
                or set(receipt_binding) != required
                or any(not isinstance(receipt_binding[key], str) or not receipt_binding[key]
                       for key in required - {"targets"})
                or not isinstance(receipt_binding["targets"], list)
                or not receipt_binding["targets"]
                or any(not isinstance(target, str) or not target for target in receipt_binding["targets"])
                or len(set(receipt_binding["targets"])) != len(receipt_binding["targets"])):
            raise ValueError("complete frozen final review reference required")
        with self.transaction() as conn:
            row = self._lease(conn, task_id, token)
            steps = json.loads(row["steps_json"])
            scope = json.loads(row["scope_json"])
            if (row["template"] != "publication" or row["step_index"] != 2
                    or len(steps) <= 2 or steps[2].get("key") != "release"
                    or row["external_started"] or row["action_json"]
                    or scope.get("offer_id") != receipt_binding["offer_id"]
                    or scope.get("skus") != [receipt_binding["sku"]]
                    or scope.get("shops") != sorted(receipt_binding["targets"])
                    or conn.execute("SELECT 1 FROM workbench_external_tasks WHERE task_id=?", (task_id,)).fetchone()
                    or conn.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state<>'completed'", (task_id,)).fetchone()):
                raise ValueError("final review requires the original idle publication release step")
            identity = self._review_identity(conn, task_id)
            if identity["review_mode"] != "legacy":
                raise ValueError("final review mode is already frozen")
            generation = 1
            action_id = uuid.uuid4().hex
            binding = dict(receipt_binding, adapter="single-final-review/v1", review_mode="single-final-review/v1",
                           task_id=task_id, action_id=action_id, generation=generation,
                           step_index=2, scope_digest=hashlib.sha256(row["scope_json"].encode()).hexdigest())
            action = {"kind": "review", "label": label, "reason": reason, "url": safe_url(url),
                      "receipt_binding": binding, "action_id": action_id}
            conn.execute("INSERT INTO workbench_review_identity VALUES(?,?,?)",
                         (task_id, "single-final-review/v1", generation))
            conn.execute("UPDATE workbench_execution SET action_json=?,worker=NULL,lease_token=NULL,lease_until=NULL WHERE task_id=?",
                         (_json(action), task_id))
            self._state(conn, task_id, "waiting_user")
            self._event(conn, task_id, "user_action_required", action)
            return dict(action)

    def accept_final_review_receipt(self, task_id, receipt, verifier):
        """Atomically accept a domain-verified decision for the original action only.

        The verifier must read the immutable domain decision; this method never
        interprets an HTTP payload as approval or completes the release step.
        """
        if not isinstance(receipt, dict) or not callable(verifier):
            raise ValueError("trusted final review receipt verifier required")
        required = {"receipt_id", "task_id", "action_id", "generation", "step_index",
                    "binding_digest", "decision_id", "contract_digest", "common_plan_id", "preview_digest"}
        if (not required.issubset(receipt) or any(not receipt.get(key) for key in
                required - {"generation", "step_index"}) or receipt["task_id"] != task_id):
            raise ValueError("incomplete final review decision receipt")
        with self.transaction() as conn:
            row = self._row(conn, task_id)
            self._require_version(row)
            identity = self._review_identity(conn, task_id)
            if identity["review_mode"] != "single-final-review/v1":
                raise ValueError("task has no final review identity")
            digest = hashlib.sha256(_json(receipt).encode()).hexdigest()
            old = conn.execute("SELECT * FROM workbench_receipts WHERE receipt_key=?", (receipt["receipt_id"],)).fetchone()
            if old:
                if old["task_id"] != task_id or old["digest"] != digest:
                    raise ValueError("receipt identity conflict")
                return False
            action = json.loads(row["action_json"] or "{}")
            binding = action.get("receipt_binding") or {}
            events = conn.execute("SELECT task_id,detail_json FROM workbench_events "
                                  "WHERE event_type='user_action_required' ORDER BY id").fetchall()
            original = [event for event in events if json.loads(event["detail_json"]).get("action_id") == receipt["action_id"]]
            if (row["template"] != "publication" or row["state"] != "waiting_user"
                    or row["external_started"] or row["step_index"] != 2
                    or json.loads(row["steps_json"])[2].get("key") != "release"
                    or action.get("kind") != "review" or action.get("action_id") != receipt["action_id"]
                    or binding.get("adapter") != "single-final-review/v1"
                    or binding.get("review_mode") != identity["review_mode"]
                    or binding.get("task_id") != task_id or binding.get("action_id") != receipt["action_id"]
                    or binding.get("generation") != identity["generation"]
                    or receipt["generation"] != identity["generation"]
                    or binding.get("step_index") != 2 or receipt["step_index"] != 2
                    or binding.get("scope_digest") != hashlib.sha256(row["scope_json"].encode()).hexdigest()
                    or receipt["binding_digest"] != hashlib.sha256(_json(binding).encode()).hexdigest()
                    or receipt["common_plan_id"] != binding.get("common_plan_id")
                    or receipt["preview_digest"] != binding.get("preview_digest")
                    or receipt["receipt_id"] != "final-review:%s:%s:%s" % (
                        receipt["decision_id"], task_id, receipt["action_id"])
                    or len(original) != 1 or original[0]["task_id"] != task_id
                    or json.loads(original[0]["detail_json"]) != action
                    or conn.execute("SELECT 1 FROM workbench_external_tasks WHERE task_id=?", (task_id,)).fetchone()
                    or conn.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state<>'completed'", (task_id,)).fetchone()
                    or not verifier(receipt, binding)):
                raise ValueError("final review decision does not belong to current original action")
            conn.execute("INSERT INTO workbench_receipts VALUES(?,?,?)", (receipt["receipt_id"], task_id, digest))
            conn.execute("UPDATE workbench_review_identity SET generation=generation+1 WHERE task_id=?", (task_id,))
            conn.execute("UPDATE workbench_execution SET action_json=NULL WHERE task_id=?", (task_id,))
            self._state(conn, task_id, "queued")
            self._event(conn, task_id, "final_review_receipt_accepted", {
                "receipt_id": receipt["receipt_id"], "action_id": receipt["action_id"],
                "generation": identity["generation"], "step_index": 2})
            return True

    def await_domain(self, task_id, token, *, label, reason, url="", receipt_binding):
        self.wait_for_user(task_id, token, kind="observe", label=label, reason=reason, url=url, receipt_binding=receipt_binding)

    def accept_domain_receipt(self, task_id, receipt, verifier):
        # verifier must re-read domain-owned evidence; never supplied by HTTP.
        with self.transaction() as conn:
            row = self._row(conn, task_id)
            self._require_version(row)
            if self._review_identity(conn, task_id)["review_mode"] != "legacy":
                raise ValueError("final review requires its original decision receipt")
            digest = hashlib.sha256(_json(receipt).encode()).hexdigest()
            key = str(receipt.get("receipt_id", ""))
            if not key:
                raise ValueError("receipt_id required")
            old = conn.execute("SELECT * FROM workbench_receipts WHERE receipt_key=?", (key,)).fetchone()
            if old:
                if old["task_id"] != task_id or old["digest"] != digest:
                    raise ValueError("receipt identity conflict")
                return False
            action = json.loads(row["action_json"] or "{}")
            if row["state"] not in {"waiting_user", "waiting_domain"} or action.get("kind") not in {"review", "observe", "input"} or not action.get("receipt_binding") or not verifier(receipt, action["receipt_binding"]):
                raise ValueError("domain receipt is not verified for current frozen scope")
            conn.execute("INSERT INTO workbench_receipts VALUES(?,?,?)", (key, task_id, digest))
            conn.execute("UPDATE workbench_execution SET action_json=NULL WHERE task_id=?", (task_id,))
            self._state(conn, task_id, "queued")
            self._event(conn, task_id, "domain_receipt_accepted", {"receipt_id": key})
            return True

    def fail(self, task_id, token, reason):
        with self.transaction() as conn:
            row = self._lease(conn, task_id, token)
            state = "reconciliation_required" if row["external_started"] else "failed"
            self._state(conn, task_id, state, str(reason))
            conn.execute("UPDATE workbench_execution SET worker=NULL,lease_token=NULL,lease_until=NULL WHERE task_id=?", (task_id,))
            self._event(conn, task_id, "execution_failed", {"reason": str(reason), "state": state})

    def observe_reconciliation(self, task_id, token, reason, *, action_id=None):
        """Record a domain-observed unknown result without claiming we sent it."""
        with self.transaction() as conn:
            if token is not None:
                self._lease(conn, task_id, token)
            else:
                row = self._row(conn, task_id)
                self._require_version(row)
                action = json.loads(row["action_json"] or "{}")
                if row["state"] not in {"waiting_user", "waiting_domain"} or not action_id or action.get("action_id") != action_id:
                    raise ValueError("stale domain observation")
            self._state(conn, task_id, "reconciliation_required", str(reason))
            conn.execute("UPDATE workbench_execution SET worker=NULL,lease_token=NULL,lease_until=NULL,action_json=NULL WHERE task_id=?", (task_id,))
            self._event(conn, task_id, "domain_unknown_observed", {"reason": str(reason), "request_sent_by_this_observer": False})

    def user_action(self, task_id, action, payload=None):
        payload = payload or {}
        with self.transaction() as conn:
            self._expire(conn)
            row = self._row(conn, task_id)
            if conn.execute("SELECT 1 FROM workbench_external_tasks WHERE task_id=?", (task_id,)).fetchone():
                raise ValueError("关联任务须由其现有执行者处理，不能在此重复启动或取消")
            if conn.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state<>'completed'", (task_id,)).fetchone():
                raise ValueError("关联业务操作结果尚未回读，请先核对外部结果")
            if action == "retry":
                self._require_version(row)
                if not self._retry_eligible(conn, row):
                    raise ValueError("only known safe failures may be retried")
                self._state(conn, task_id, "queued")
            elif action == "provide-input":
                self._require_version(row)
                required = json.loads(row["action_json"] or "{}")
                note = str(payload.get("note", "")).strip()
                if row["state"] != "waiting_user" or required.get("kind") != "input" or not note:
                    raise ValueError("input cannot substitute for domain approval")
                if payload.get("action_id") != required.get("action_id"):
                    raise ValueError("待办已变化，请刷新后提交")
                conn.execute("UPDATE workbench_execution SET action_json=NULL WHERE task_id=?", (task_id,))
                self._state(conn, task_id, "queued")
                self._event(conn, task_id, "input_provided", {"note": note})
            elif action == "cancel":
                if row["state"] in {"running", "waiting_domain", "reconciliation_required", "completed", "cancelled"} or row["external_started"]:
                    raise ValueError("stop or reconcile active execution before cancellation")
                self._state(conn, task_id, "cancelled")
                if self._review_identity(conn, task_id)["review_mode"] != "legacy":
                    conn.execute("UPDATE workbench_review_identity SET generation=generation+1 WHERE task_id=?", (task_id,))
                    conn.execute("UPDATE workbench_execution SET action_json=NULL WHERE task_id=?", (task_id,))
                conn.execute("DELETE FROM workbench_resource_locks WHERE task_id=?", (task_id,))
            else:
                raise ValueError("unsupported action")
            self._event(conn, task_id, "user_" + action, {})
        return self.get(task_id)

    def attach_external(self, task_id, *, external_id, owner, observed_at, observed_status, evidence_ref=""):
        """Trusted coordinator records a dated observation, never live execution."""
        from datetime import datetime
        datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if not external_id or not owner:
            raise ValueError("external identity and owner required")
        with self.transaction() as conn:
            row = self._row(conn, task_id)
            if self._review_identity(conn, task_id)["review_mode"] != "legacy":
                raise ValueError("final review task cannot attach an external executor")
            if row["state"] != "queued" or row["external_started"]:
                raise ValueError("only unclaimed queued tasks may attach to an existing owner")
            external = dict(external_id=external_id, owner=owner, observed_at=observed_at, observed_status=observed_status, evidence_ref=evidence_ref, live_status="unknown")
            conn.execute("INSERT OR REPLACE INTO workbench_external_tasks VALUES(?,?)", (task_id, _json(external)))
            self._event(conn, task_id, "external_task_observed", external)
        return self.get(task_id)

    def bind_scope(self, task_id, token, scope):
        """Resolve exact identities before a business write; acquire locks atomically."""
        with self.transaction() as conn:
            row = self._lease(conn, task_id, token)
            if self._review_identity(conn, task_id)["review_mode"] != "legacy":
                raise ValueError("final review scope is frozen")
            if row["external_started"]:
                raise ValueError("cannot change scope after external action starts")
            scope = self._scope(row["template"], scope)
            old = json.loads(row["scope_json"])
            # Once exact identities are bound they cannot silently change.
            if any(value and scope.get(key) != value for key, value in old.items()):
                raise ValueError("bound scope cannot be replaced")
            if row["template"] == "publication" and scope.get("offer_id"):
                resource = "product-offer:" + scope["offer_id"]
                existing = conn.execute("SELECT task_id FROM workbench_resource_locks WHERE resource=?", (resource,)).fetchone()
                if existing and existing["task_id"] != task_id:
                    raise ValueError("offer preparation is locked by " + existing["task_id"])
                conn.execute("INSERT OR IGNORE INTO workbench_resource_locks VALUES(?,?)", (resource, task_id))
            for shop in ([] if row["template"] == "profit" else scope["shops"]):
                for sku in scope["skus"]:
                    resource = "product:" + _json([shop, sku])
                    if conn.execute("SELECT 1 FROM workbench_domain_locks WHERE resource=?", (resource,)).fetchone():
                        raise ValueError("product scope is locked by an existing domain operation")
                    existing = conn.execute("SELECT task_id FROM workbench_resource_locks WHERE resource=?", (resource,)).fetchone()
                    if existing and existing["task_id"] != task_id:
                        raise ValueError("product scope is locked by " + existing["task_id"])
                    conn.execute("INSERT OR IGNORE INTO workbench_resource_locks VALUES(?,?)", (resource, task_id))
            conn.execute("UPDATE workbench_execution SET scope_json=? WHERE task_id=?", (_json(scope), task_id))
            self._event(conn, task_id, "scope_bound", {"scope": scope})

    def begin_domain_operation(self, operation_id, *, skus, shops, owner_task_id=None):
        """Reserve an existing domain action before its external call.

        Returning acquired=False is a hard no-execute result, including same-ID
        retries. An interrupted operation stays reserved until provider readback.
        The server derives owner_task_id and identities from trusted domain data.
        """
        scope = self._scope("delisting", {"skus": skus, "shops": shops})
        resources = sorted("product:" + _json([shop, sku]) for shop in scope["shops"] for sku in scope["skus"])
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("immutable domain operation identity required")
        with self.transaction() as conn:
            old = conn.execute("SELECT * FROM workbench_domain_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if old:
                if json.loads(old["resources_json"]) != resources or old["owner_task_id"] != owner_task_id:
                    raise ValueError("domain operation identity already binds another scope")
                return {"acquired": False, "state": old["state"], "provider_readback_ref": old["readback_ref"]}
            if owner_task_id:
                owner = self._row(conn, owner_task_id)
                if self._review_identity(conn, owner_task_id)["review_mode"] != "legacy":
                    raise ValueError("final review execution authorization is not installed")
                if owner["template"] == "profit" or owner["state"] in {"cancelled", "completed", "reconciliation_required"}:
                    raise ValueError("invalid domain operation owner")
                owner_scope = json.loads(owner["scope_json"])
                if not set(scope["skus"]).issubset(owner_scope["skus"]) or not set(scope["shops"]).issubset(owner_scope["shops"]):
                    raise ValueError("domain action exceeds owner scope")
            for resource in resources:
                lock = conn.execute("SELECT task_id FROM workbench_resource_locks WHERE resource=?", (resource,)).fetchone()
                if lock and lock["task_id"] != owner_task_id:
                    raise ValueError("product scope is locked by task " + lock["task_id"])
                if conn.execute("SELECT 1 FROM workbench_domain_locks WHERE resource=?", (resource,)).fetchone():
                    raise ValueError("product scope has another unresolved domain operation")
            conn.execute("INSERT INTO workbench_domain_operations VALUES(?,?,?,'inflight','',?)", (operation_id, _json(resources), owner_task_id, _now()))
            for resource in resources:
                conn.execute("INSERT INTO workbench_domain_locks VALUES(?,?)", (resource, operation_id))
            return {"acquired": True, "state": "inflight"}

    def supersede_domain_operation(self, previous_operation_id, operation_id, *,
                                   previous_skus, previous_shops, skus, shops,
                                   owner_task_id=None, reconciliation_ref):
        """Atomically reconcile one exact operation and reserve its scoped successor."""
        previous_scope = self._scope("delisting", {
            "skus": previous_skus, "shops": previous_shops})
        scope = self._scope("delisting", {"skus": skus, "shops": shops})
        previous_resources = sorted(
            "product:" + _json([shop, sku])
            for shop in previous_scope["shops"] for sku in previous_scope["skus"])
        resources = sorted(
            "product:" + _json([shop, sku])
            for shop in scope["shops"] for sku in scope["skus"])
        if (not isinstance(previous_operation_id, str) or not previous_operation_id.strip()
                or not isinstance(operation_id, str) or not operation_id.strip()
                or previous_operation_id == operation_id
                or not isinstance(reconciliation_ref, str) or not reconciliation_ref.strip()):
            raise ValueError("exact domain handover identity is required")
        if not set(resources).issubset(previous_resources):
            raise ValueError("successor domain scope exceeds reconciled operation")
        with self.transaction() as conn:
            previous = conn.execute(
                "SELECT * FROM workbench_domain_operations WHERE operation_id=?",
                (previous_operation_id,)).fetchone()
            successor = conn.execute(
                "SELECT * FROM workbench_domain_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            if successor:
                if (json.loads(successor["resources_json"]) != resources
                        or successor["owner_task_id"] != owner_task_id
                        or not previous or previous["state"] != "completed"
                        or json.loads(previous["resources_json"]) != previous_resources
                        or previous["owner_task_id"] != owner_task_id
                        or previous["readback_ref"] != reconciliation_ref):
                    raise ValueError("domain handover identity already binds another scope")
                previous_locks = {row["resource"] for row in conn.execute(
                    "SELECT resource FROM workbench_domain_locks WHERE operation_id=?",
                    (previous_operation_id,)).fetchall()}
                successor_locks = {row["resource"] for row in conn.execute(
                    "SELECT resource FROM workbench_domain_locks WHERE operation_id=?",
                    (operation_id,)).fetchall()}
                if previous_locks or successor["state"] != "inflight" \
                        or successor_locks != set(resources):
                    raise ValueError("domain handover lock state drifted")
                return {"acquired": False, "state": successor["state"],
                        "provider_readback_ref": successor["readback_ref"]}
            if (not previous or previous["state"] != "inflight"
                    or json.loads(previous["resources_json"]) != previous_resources
                    or previous["owner_task_id"] != owner_task_id):
                raise ValueError("previous domain operation does not match recovery authority")
            previous_locks = {row["resource"] for row in conn.execute(
                "SELECT resource FROM workbench_domain_locks WHERE operation_id=?",
                (previous_operation_id,)).fetchall()}
            locked = {row["resource"]: row["operation_id"] for row in conn.execute(
                "SELECT resource,operation_id FROM workbench_domain_locks WHERE resource IN (%s)"
                % ",".join("?" for _ in previous_resources), previous_resources).fetchall()}
            if (previous_locks != set(previous_resources)
                    or locked != {resource: previous_operation_id for resource in previous_resources}):
                raise ValueError("previous domain operation lock set drifted")
            if owner_task_id:
                owner = self._row(conn, owner_task_id)
                if self._review_identity(conn, owner_task_id)["review_mode"] != "legacy":
                    raise ValueError("final review execution authorization is not installed")
                if owner["template"] == "profit" or owner["state"] in {
                        "cancelled", "completed", "reconciliation_required"}:
                    raise ValueError("invalid domain operation owner")
                owner_scope = json.loads(owner["scope_json"])
                if (not set(scope["skus"]).issubset(owner_scope["skus"])
                        or not set(scope["shops"]).issubset(owner_scope["shops"])):
                    raise ValueError("domain action exceeds owner scope")
            for resource in resources:
                task_lock = conn.execute(
                    "SELECT task_id FROM workbench_resource_locks WHERE resource=?",
                    (resource,)).fetchone()
                if task_lock and task_lock["task_id"] != owner_task_id:
                    raise ValueError("product scope is locked by task " + task_lock["task_id"])
            conn.execute(
                "UPDATE workbench_domain_operations SET state='completed',readback_ref=? "
                "WHERE operation_id=?", (reconciliation_ref, previous_operation_id))
            conn.execute("DELETE FROM workbench_domain_locks WHERE operation_id=?",
                         (previous_operation_id,))
            conn.execute(
                "INSERT INTO workbench_domain_operations VALUES(?,?,?,'inflight','',?)",
                (operation_id, _json(resources), owner_task_id, _now()))
            for resource in resources:
                conn.execute("INSERT INTO workbench_domain_locks VALUES(?,?)",
                             (resource, operation_id))
            return {"acquired": True, "state": "inflight",
                    "superseded_operation_id": previous_operation_id,
                    "reconciliation_ref": reconciliation_ref}

    def complete_domain_operation(self, operation_id, *, provider_readback_ref):
        if not isinstance(provider_readback_ref, str) or not provider_readback_ref.strip():
            raise ValueError("provider readback required to release domain scope")
        with self.transaction() as conn:
            old = conn.execute("SELECT * FROM workbench_domain_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if not old:
                raise KeyError(operation_id)
            if old["state"] == "completed" and old["readback_ref"] != provider_readback_ref:
                raise ValueError("domain readback identity changed")
            conn.execute("UPDATE workbench_domain_operations SET state='completed',readback_ref=? WHERE operation_id=?", (provider_readback_ref, operation_id))
            conn.execute("DELETE FROM workbench_domain_locks WHERE operation_id=?", (operation_id,))

    def complete_domain_operation_exact(self, operation_id, *, skus, shops,
                                        provider_readback_ref):
        """Atomically close only an exact inflight operation and its full lock set."""
        if not isinstance(provider_readback_ref, str) or not provider_readback_ref.strip():
            raise ValueError("provider readback required to release domain scope")
        scope = self._scope("delisting", {"skus": skus, "shops": shops})
        resources = sorted("product:" + _json([shop, sku])
                           for shop in scope["shops"] for sku in scope["skus"])
        with self.transaction() as conn:
            old = conn.execute("SELECT * FROM workbench_domain_operations WHERE operation_id=?",
                               (operation_id,)).fetchone()
            actual_locks = {row["resource"] for row in conn.execute(
                "SELECT resource FROM workbench_domain_locks WHERE operation_id=?",
                (operation_id,)).fetchall()}
            if (not old or old["state"] != "inflight" or old["readback_ref"] != ""
                    or json.loads(old["resources_json"]) != resources
                    or actual_locks != set(resources)):
                raise ValueError("exact inflight domain operation lock set drifted")
            conn.execute("UPDATE workbench_domain_operations SET state='completed',readback_ref=? WHERE operation_id=?",
                         (provider_readback_ref, operation_id))
            conn.execute("DELETE FROM workbench_domain_locks WHERE operation_id=?", (operation_id,))
