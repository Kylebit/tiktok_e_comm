"""Actual frozen B facts, explicit query-only inputs, and exact-scope negative cases.

The diagnostic is not a final candidate/approval or an installed source reader.
Actual combined source append and provider current-read remain owner checks.
"""
from pathlib import Path
import os,subprocess,sys


def test_actual_B_api9_ro_compilation_and_scope_rejections():
    script=Path(os.environ['ORBIT_API9_READONLY_DIAGNOSTIC'])
    assert script.is_absolute() and script.is_file()
    result=subprocess.run([sys.executable,'-I','-B','-X','utf8',str(script),'--negative-validation'],
        text=True,capture_output=True,timeout=60)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    assert '"status": "READY_FOR_FINAL_REVIEW"' in result.stdout
    assert '"all_rejected": true' in result.stdout
