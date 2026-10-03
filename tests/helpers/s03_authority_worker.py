"""Real isolated persistence worker; faults are injected only at file I/O."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared_platform import publication_autopilot as authority


def main():
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if config.get("action") == "recovery_cli":
        from scripts.recover_publication_approval import main as recovery_main
        raise SystemExit(recovery_main(config["argv"]))
    root = Path(config["root"])
    marker = Path(config["marker"])
    if config.get("interrupt_after_link"):
        original_link = os.link
        def linked_then_interrupted(source, destination, *args, **kwargs):
            result = original_link(source, destination, *args, **kwargs)
            marker.write_text(json.dumps({"published_path": str(destination), "pid": os.getpid()}), encoding="utf-8")
            while True:
                time.sleep(0.05)
            return result
        os.link = linked_then_interrupted
    original_open = Path.open
    if config.get("interrupt"):
        class InterruptedWriter:
            def __init__(self, handle, path):
                self.handle, self.path = handle, path
            def __enter__(self):
                self.handle.__enter__()
                return self
            def __exit__(self, *args):
                return self.handle.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.handle, name)
            def write(self, value):
                prefix = value[:31]
                self.handle.write(prefix)
                self.handle.flush()
                os.fsync(self.handle.fileno())
                raw = value.encode("utf-8") if isinstance(value, str) else value
                marker.write_text(json.dumps({
                    "partial_path": str(self.path),
                    "expected_sha256": hashlib.sha256(raw).hexdigest(),
                    "pid": os.getpid(),
                }), encoding="utf-8")
                while True:
                    time.sleep(0.05)

        def interrupted_open(path, mode="r", *args, **kwargs):
            handle = original_open(path, mode, *args, **kwargs)
            if path.is_relative_to(root) and any(flag in mode for flag in "wx"):
                return InterruptedWriter(handle, path)
            return handle
        Path.open = interrupted_open
    elif not config.get("interrupt_after_link"):
        marker.write_text(str(os.getpid()), encoding="utf-8")
        barrier = config.get("barrier")
        if barrier:
            deadline = time.monotonic() + 20
            while not Path(barrier).exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("worker barrier timed out")
                time.sleep(0.01)
    try:
        if config["kind"] == "candidate":
            path = authority.persist_release_candidate(config["candidate"], reports_root=root)
        else:
            path = authority.persist_final_approval_receipt(
                config["approval"], config["candidate"], reports_root=root
            )
        print(json.dumps({"status": "PERSISTED", "path": str(path)}), flush=True)
    except authority.PublicationAutopilotContractError as error:
        print(json.dumps({"status": "CONFLICT", "error": str(error)}), flush=True)
        raise SystemExit(3)


if __name__ == "__main__":
    main()
