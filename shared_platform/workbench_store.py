"""Durable local task-management store for the Orbit workbench.

The workbench deliberately owns a separate SQLite file: task planning must not
be coupled to commerce synchronisation data and no credentials are stored here.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core.config import ROOT


DEFAULT_WORKBENCH_STORE_PATH = ROOT / "data" / "orbit_workbench.db"
STATUSES = frozenset({"inbox", "triage", "todo", "in_progress", "waiting_approval", "blocked", "parked", "done", "cancelled"})
PRIORITIES = frozenset({"P0", "P1", "P2", "P3"})
APPROVAL_STATUSES = frozenset({"not_required", "pending", "approved", "rejected"})
_TRANSITIONS = {
    "inbox": {"triage", "cancelled"},
    "triage": {"todo", "cancelled"},
    "todo": {"in_progress", "cancelled"},
    "in_progress": {"waiting_approval", "blocked", "parked", "done", "cancelled"},
    "waiting_approval": {"in_progress", "blocked", "done", "cancelled"},
    "blocked": {"todo", "in_progress", "cancelled", "parked"},
    "parked": {"triage", "todo", "cancelled"},
    "done": set(),
    "cancelled": set(),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workbench_tasks (
    task_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT '', business_line TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL, status TEXT NOT NULL,
    due_date TEXT, related_url TEXT NOT NULL DEFAULT '', definition_of_done_json TEXT NOT NULL DEFAULT '[]',
    blocked_reason TEXT NOT NULL DEFAULT '', approval_status TEXT NOT NULL DEFAULT 'not_required',
    execution_notes TEXT NOT NULL DEFAULT '', is_top3 INTEGER NOT NULL DEFAULT 0,
    source_key TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_workbench_tasks_status ON workbench_tasks(status, due_date);
CREATE INDEX IF NOT EXISTS idx_workbench_tasks_owner ON workbench_tasks(owner, priority);
CREATE TABLE IF NOT EXISTS workbench_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, event_type TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES workbench_tasks(task_id)
);
CREATE INDEX IF NOT EXISTS idx_workbench_events_task ON workbench_events(task_id, id DESC);
CREATE TABLE IF NOT EXISTS workbench_weekly_reviews (
    review_id TEXT PRIMARY KEY, week_start TEXT NOT NULL UNIQUE, content_json TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workbench_deep_ops_schedule (
    weekday INTEGER PRIMARY KEY CHECK(weekday BETWEEN 0 AND 4),
    group_key TEXT NOT NULL UNIQUE, group_name TEXT NOT NULL, focus_text TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workbench_deep_ops_sessions (
    session_date TEXT PRIMARY KEY, weekday INTEGER NOT NULL, group_key TEXT NOT NULL,
    group_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'planned', notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""

_DEFAULT_DEEP_OPS = (
    (0, "mx_uk", "墨西哥 + 英国", "商品、价格、库存与上架审批"),
    (1, "shopee", "虾皮", "经营数据、商品表现、促销与待处理事项"),
    (2, "ozon", "Ozon", "商品迁移、价格预警、俄语文案与利润"),
    (3, "tiktok_sea_lively_hive", "TikTok SEA · Lively Hive", "内容、商品、流量与店铺运营"),
    (4, "tiktok_sea_homebloom", "TikTok SEA · HomeBloom", "内容、商品、流量与店铺运营"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object) -> str:
    return str(value or "").strip()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class WorkbenchStore:
    def __init__(self, path: str | Path = DEFAULT_WORKBENCH_STORE_PATH) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript(_SCHEMA)
        return conn

    def _next_id(self, conn: sqlite3.Connection) -> str:
        prefix = f"TASK-{date.today():%Y%m%d}-"
        row = conn.execute("SELECT task_id FROM workbench_tasks WHERE task_id LIKE ? ORDER BY task_id DESC LIMIT 1", (prefix + "%",)).fetchone()
        serial = int(row["task_id"].rsplit("-", 1)[-1]) + 1 if row else 1
        return f"{prefix}{serial:03d}"

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["is_top3"] = bool(result["is_top3"])
        result["definition_of_done"] = json.loads(result.pop("definition_of_done_json") or "[]")
        return result

    def _event(self, conn: sqlite3.Connection, task_id: str, event_type: str, detail: Mapping[str, Any]) -> None:
        conn.execute("INSERT INTO workbench_events(task_id,event_type,detail_json,created_at) VALUES(?,?,?,?)", (task_id, event_type, _json(detail), _now()))

    def create_task(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        title = _text(payload.get("title"))
        if not title:
            raise ValueError("title is required")
        priority = _text(payload.get("priority") or "P2")
        status = _text(payload.get("status") or "todo")
        approval = _text(payload.get("approval_status") or "not_required")
        if priority not in PRIORITIES or status not in STATUSES or approval not in APPROVAL_STATUSES:
            raise ValueError("invalid priority, status, or approval_status")
        due_date = _text(payload.get("due_date")) or None
        if due_date:
            date.fromisoformat(due_date)
        dod = payload.get("definition_of_done") or []
        if not isinstance(dod, list) or not all(isinstance(item, str) for item in dod):
            raise ValueError("definition_of_done must be a list of strings")
        source_key = _text(payload.get("source_key")) or None
        now = _now()
        with self._connect() as conn:
            if source_key:
                existing = conn.execute("SELECT * FROM workbench_tasks WHERE source_key=?", (source_key,)).fetchone()
                if existing:
                    return self._row(existing)
            task_id = self._next_id(conn)
            conn.execute("""INSERT INTO workbench_tasks(task_id,title,project,business_line,owner,priority,status,due_date,related_url,definition_of_done_json,blocked_reason,approval_status,execution_notes,is_top3,source_key,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (task_id, title, _text(payload.get("project")), _text(payload.get("business_line")), _text(payload.get("owner")), priority, status, due_date, _text(payload.get("related_url")), _json(dod), _text(payload.get("blocked_reason")), approval, _text(payload.get("execution_notes")), int(bool(payload.get("is_top3"))), source_key, now, now))
            self._event(conn, task_id, "created", {"status": status, "source_key": source_key})
            row = conn.execute("SELECT * FROM workbench_tasks WHERE task_id=?", (task_id,)).fetchone()
            return self._row(row)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM workbench_tasks WHERE task_id=?", (_text(task_id),)).fetchone()
            return self._row(row) if row else None

    def list_tasks(self, **filters: str | None) -> list[dict[str, Any]]:
        clauses, values = [], []
        for name in ("project", "business_line", "owner", "priority", "status"):
            value = _text(filters.get(name))
            if value:
                clauses.append(f"{name}=?")
                values.append(value)
        query = "SELECT * FROM workbench_tasks" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END, COALESCE(due_date,'9999-12-31'), updated_at DESC"
        with self._connect() as conn:
            return [self._row(row) for row in conn.execute(query, values).fetchall()]

    def update_task(self, task_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"title", "project", "business_line", "owner", "priority", "due_date", "related_url", "definition_of_done", "blocked_reason", "approval_status", "execution_notes", "is_top3"}
        fields = {key: payload[key] for key in allowed if key in payload}
        if not fields:
            task = self.get_task(task_id)
            if not task:
                raise KeyError(task_id)
            return task
        if "priority" in fields and _text(fields["priority"]) not in PRIORITIES:
            raise ValueError("invalid priority")
        if "approval_status" in fields and _text(fields["approval_status"]) not in APPROVAL_STATUSES:
            raise ValueError("invalid approval_status")
        if "due_date" in fields and _text(fields["due_date"]):
            date.fromisoformat(_text(fields["due_date"]))
        if "definition_of_done" in fields:
            if not isinstance(fields["definition_of_done"], list) or not all(isinstance(item, str) for item in fields["definition_of_done"]):
                raise ValueError("definition_of_done must be a list of strings")
            fields["definition_of_done_json"] = _json(fields.pop("definition_of_done"))
        if "is_top3" in fields:
            fields["is_top3"] = int(bool(fields["is_top3"]))
        fields = {key: (_text(value) if key not in {"is_top3", "definition_of_done_json"} else value) for key, value in fields.items()}
        fields["updated_at"] = _now()
        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM workbench_tasks WHERE task_id=?", (_text(task_id),)).fetchone():
                raise KeyError(task_id)
            conn.execute("UPDATE workbench_tasks SET " + ", ".join(f"{key}=?" for key in fields) + " WHERE task_id=?", (*fields.values(), _text(task_id)))
            self._event(conn, _text(task_id), "updated", {"fields": sorted(fields)})
            return self._row(conn.execute("SELECT * FROM workbench_tasks WHERE task_id=?", (_text(task_id),)).fetchone())

    def transition(self, task_id: str, status: str, note: str = "") -> dict[str, Any]:
        status = _text(status)
        if status not in STATUSES:
            raise ValueError("invalid status")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM workbench_tasks WHERE task_id=?", (_text(task_id),)).fetchone()
            if not row:
                raise KeyError(task_id)
            old = row["status"]
            if status not in _TRANSITIONS[old]:
                raise ValueError(f"invalid transition: {old} -> {status}")
            now = _now()
            completed = now if status == "done" else None
            conn.execute("UPDATE workbench_tasks SET status=?, updated_at=?, completed_at=? WHERE task_id=?", (status, now, completed, _text(task_id)))
            self._event(conn, _text(task_id), "status_changed", {"from": old, "to": status, "note": _text(note)})
            return self._row(conn.execute("SELECT * FROM workbench_tasks WHERE task_id=?", (_text(task_id),)).fetchone())

    def dashboard(self, today: str | None = None) -> dict[str, Any]:
        today = today or date.today().isoformat()
        tasks = self.list_tasks()
        active = [item for item in tasks if item["status"] not in {"done", "cancelled", "parked"}]
        top3 = [item for item in active if item["is_top3"]][:3]
        if len(top3) < 3:
            top3 += [item for item in active if item not in top3][: 3 - len(top3)]
        counts = {status: sum(item["status"] == status for item in tasks) for status in STATUSES}
        projects: dict[str, dict[str, int]] = {}
        for item in active:
            key = item["project"] or "未归类"
            bucket = projects.setdefault(key, {"total": 0, "done": 0})
            bucket["total"] += 1
        for item in tasks:
            key = item["project"] or "未归类"
            if key in projects and item["status"] == "done":
                projects[key]["done"] += 1
        return {"today": today, "top3": top3, "tasks": tasks, "counts": counts, "overdue": [item for item in active if item["due_date"] and item["due_date"] < today], "projects": projects}

    def _ensure_deep_ops_schedule(self, conn: sqlite3.Connection) -> None:
        now = _now()
        for weekday, key, name, focus in _DEFAULT_DEEP_OPS:
            conn.execute("INSERT OR IGNORE INTO workbench_deep_ops_schedule(weekday,group_key,group_name,focus_text,updated_at) VALUES(?,?,?,?,?)", (weekday, key, name, focus, now))

    def deep_ops(self, for_date: str | None = None) -> dict[str, Any]:
        day = date.fromisoformat(for_date) if for_date else date.today()
        with self._connect() as conn:
            self._ensure_deep_ops_schedule(conn)
            schedule = [dict(row) for row in conn.execute("SELECT weekday,group_key,group_name,focus_text FROM workbench_deep_ops_schedule ORDER BY weekday").fetchall()]
            session = conn.execute("SELECT session_date,weekday,group_key,group_name,status,notes,updated_at FROM workbench_deep_ops_sessions WHERE session_date=?", (day.isoformat(),)).fetchone()
            today_item = next((item for item in schedule if item["weekday"] == day.weekday()), None) if day.weekday() < 5 else None
            current = dict(session) if session else today_item
            if current and today_item:
                current.setdefault("focus_text", today_item["focus_text"])
            return {"date": day.isoformat(), "is_operating_day": today_item is not None, "today": current, "schedule": schedule}

    def update_deep_ops_session(self, for_date: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        day = date.fromisoformat(for_date)
        if day.weekday() > 4:
            raise ValueError("deep operation days are Monday through Friday")
        with self._connect() as conn:
            self._ensure_deep_ops_schedule(conn)
            base = conn.execute("SELECT weekday,group_key,group_name,focus_text FROM workbench_deep_ops_schedule WHERE weekday=?", (day.weekday(),)).fetchone()
            status = _text(payload.get("status") or "planned")
            if status not in {"planned", "in_progress", "done"}:
                raise ValueError("invalid deep operation status")
            now = _now()
            conn.execute("""INSERT INTO workbench_deep_ops_sessions(session_date,weekday,group_key,group_name,status,notes,updated_at) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(session_date) DO UPDATE SET status=excluded.status,notes=excluded.notes,updated_at=excluded.updated_at""", (day.isoformat(), base["weekday"], base["group_key"], base["group_name"], status, _text(payload.get("notes")), now))
        return self.deep_ops(for_date)["today"]

    def weekly_review(self, week_start: str, content: Mapping[str, Any] | None = None) -> dict[str, Any]:
        monday = date.fromisoformat(week_start)
        if monday.weekday() != 0:
            raise ValueError("week_start must be a Monday")
        now = _now()
        if content is None:
            tasks = self.list_tasks()
            end = monday.fromordinal(monday.toordinal() + 6).isoformat()
            content = {"week_start": week_start, "week_end": end, "completed": [task for task in tasks if task["completed_at"] and week_start <= task["completed_at"][:10] <= end], "blocked": [task for task in tasks if task["status"] == "blocked"], "waiting_approval": [task for task in tasks if task["status"] == "waiting_approval"], "next_top3": self.dashboard()["top3"], "operating_results": "", "decisions_needed": ""}
        review_id = f"WEEK-{week_start}"
        with self._connect() as conn:
            conn.execute("""INSERT INTO workbench_weekly_reviews(review_id,week_start,content_json,created_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(week_start) DO UPDATE SET content_json=excluded.content_json,updated_at=excluded.updated_at""", (review_id, week_start, _json(content), now, now))
        return {"review_id": review_id, "week_start": week_start, "content": dict(content), "updated_at": now}

    def events(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT event_type,detail_json,created_at FROM workbench_events WHERE task_id=? ORDER BY id DESC", (_text(task_id),)).fetchall()
            return [{"event_type": row["event_type"], "detail": json.loads(row["detail_json"]), "created_at": row["created_at"]} for row in rows]

    def record_notification(self, task_id: str, *, delivered: bool, reason: str) -> None:
        with self._connect() as conn:
            self._event(conn, _text(task_id), "notification_sent" if delivered else "notification_skipped", {"reason": _text(reason)})


def default_workbench_store() -> WorkbenchStore:
    return WorkbenchStore()
