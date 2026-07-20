# -*- coding: utf-8 -*-
"""停止全部 Stage3 进程（精确回收 .stage3_pids.json 中记录的 pid）。"""
import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from start_stage3 import _stop_all, status  # noqa


if __name__ == "__main__":
    print("[stop] 回收 Stage3 进程 ...")
    _stop_all()
    print("[done] 回收完成。当前状态：")
    try:
        status()
    except Exception:
        pass
