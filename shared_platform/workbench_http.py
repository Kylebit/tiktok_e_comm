"""Small HTTP adapter; executor/approval mutation is intentionally not public."""
from urllib.parse import urlsplit


def dispatch(engine, method, path, payload=None, *, create_handler=None):
    prefix = "/api/orbit/tasks"
    path = urlsplit(path).path.rstrip("/")
    if path != prefix and not path.startswith(prefix + "/"):
        return None
    if payload is not None and not isinstance(payload, dict):
        return 400, {"error": "请求内容必须为 JSON 对象"}
    try:
        parts = path[len(prefix):].strip("/").split("/") if path != prefix else []
        if not parts and method == "GET":
            return 200, engine.dashboard()
        if not parts and method == "POST":
            return 201, {"task": (create_handler or engine.create)(payload or {})}
        if len(parts) == 1 and method == "GET":
            return 200, {"task": engine.get(parts[0]), "events": engine.store.events(parts[0])}
        if len(parts) == 2 and method == "POST" and parts[1] in {"retry", "cancel", "provide-input"}:
            return 200, {"task": engine.user_action(parts[0], parts[1], payload)}
        return 404, {"error": "任务接口不存在"}
    except KeyError:
        return 404, {"error": "任务不存在"}
    except (ValueError, TypeError) as exc:
        return 409, {"error": str(exc)}
