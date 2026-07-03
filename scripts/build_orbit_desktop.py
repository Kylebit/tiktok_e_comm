from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    script = Path(__file__).with_suffix(".ps1")
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]
    if "--install-dependencies" in sys.argv:
        command.append("-InstallDependencies")
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
