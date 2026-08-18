"""Optional, non-blocking Feishu notifications for workbench task changes."""

from __future__ import annotations

from typing import Any

from shared_platform.workbench_store import WorkbenchStore


def notify_task_change(store: WorkbenchStore, task: dict[str, Any], event: str) -> None:
    """Notify only when the existing Feishu webhook is explicitly configured."""
    try:
        from modules.hub.feishu import feishu_config, send_text

        config = feishu_config()
        if not config["enabled"] or not config["webhook_url"]:
            store.record_notification(task["task_id"], delivered=False, reason="Feishu webhook is not configured")
            return
        labels = {"assigned": "任务已分派", "waiting_approval": "任务等待审批", "blocked": "任务已阻塞", "done": "任务已完成"}
        label = labels.get(event, "任务已更新")
        send_text(f"{label}：{task['title']}\nID: {task['task_id']} · {task['priority']} · {task.get('owner') or '未分派'}\n本地工作台：{config['console_base_url']}/workbench")
        store.record_notification(task["task_id"], delivered=True, reason=event)
    except Exception as error:
        # Task persistence must not depend on external webhook availability.
        store.record_notification(task["task_id"], delivered=False, reason=str(error)[:300])
