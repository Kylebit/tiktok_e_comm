"""Process-level tests for the bounded R1 facts stdout transport."""

import sys
import time
import os
import subprocess

import pytest

from shared_platform.operations_runtime import _session_id
from shared_platform.worker_category_readonly_cli import (
    MAX_JSONL_BYTES, run_readonly_jsonl)


def test_readonly_child_jsonl_stdout_is_captured_without_output_file(tmp_path):
    code = ("import json,sys; assert sys.stdin.read() == 'frozen prompt'; "
            "print(json.dumps({'type':'thread.started','thread_id':'fixture'})); "
            "print(json.dumps({'type':'turn.completed'}))")
    result = run_readonly_jsonl([sys.executable, '-c', code], 'frozen prompt',
                                cwd=tmp_path, timeout=10)
    assert result.returncode == 0
    assert not result.overflow and not result.timed_out
    assert result.stdout.count('\n') == 2
    assert list(tmp_path.iterdir()) == []


def test_readonly_child_stdout_limit_kills_noisy_process(tmp_path):
    code = "import sys,time; sys.stdout.write('x'*200000); sys.stdout.flush(); time.sleep(20)"
    result = run_readonly_jsonl([sys.executable, '-c', code], 'prompt',
                                cwd=tmp_path, timeout=10, max_stdout_bytes=4096)
    assert result.overflow and not result.timed_out
    assert len(result.stdout.encode('utf-8')) <= 4096
    assert result.returncode != 0
    assert list(tmp_path.iterdir()) == []


def test_readonly_child_just_over_stdout_limit_stops_before_timeout(tmp_path):
    code = ("import sys,time; sys.stdout.buffer.write(b'x'*129); "
            "sys.stdout.flush(); time.sleep(10)")
    started = time.monotonic()
    result = run_readonly_jsonl([sys.executable, '-c', code], 'prompt',
                                cwd=tmp_path, timeout=2, max_stdout_bytes=128)
    elapsed = time.monotonic() - started
    assert result.overflow and not result.timed_out
    assert result.stdout == 'x' * 128
    assert elapsed < 1.5


def test_readonly_child_timeout_returns_bounded_prefix(tmp_path):
    code = "import sys,time; print('started',flush=True); time.sleep(20)"
    result = run_readonly_jsonl([sys.executable, '-c', code], 'prompt',
                                cwd=tmp_path, timeout=0.2)
    assert result.timed_out and not result.overflow
    assert len(result.stdout.encode('utf-8')) <= MAX_JSONL_BYTES
    assert result.returncode != 0


@pytest.mark.parametrize('trigger', ['timeout', 'overflow'])
def test_readonly_child_stops_grandchild_before_return(tmp_path, trigger):
    ready = tmp_path / 'grandchild-ready.txt'
    marker = tmp_path / 'escaped.txt'
    grandchild = ("import pathlib,time; "
                  f"pathlib.Path({str(ready)!r}).write_text('ready'); "
                  "time.sleep(1.2); "
                  f"pathlib.Path({str(marker)!r}).write_text('escaped')")
    child = ("import subprocess,sys,time; "
             f"subprocess.Popen([sys.executable,'-c',{grandchild!r}], "
             "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
             f"from pathlib import Path; ready=Path({str(ready)!r}); "
             "\nwhile not ready.exists(): time.sleep(0.01)\n"
             "print('ready',flush=True); "
             + ("sys.stdout.write('x'*100000); sys.stdout.flush(); "
                if trigger == 'overflow' else '')
             + "time.sleep(20)")
    result = run_readonly_jsonl([sys.executable, '-c', child], 'prompt',
                                cwd=tmp_path, timeout=0.3 if trigger == 'timeout' else 10,
                                max_stdout_bytes=128)
    assert (result.timed_out if trigger == 'timeout' else result.overflow)
    assert ready.exists() and result.stdout.startswith('ready')
    time.sleep(1.4)
    assert not marker.exists()


def test_readonly_child_normal_exit_stops_remaining_grandchild(tmp_path):
    ready = tmp_path / 'ready.txt'
    marker = tmp_path / 'escaped.txt'
    grandchild = ("import pathlib,time; "
                  f"pathlib.Path({str(ready)!r}).write_text('ready'); "
                  "time.sleep(1.2); "
                  f"pathlib.Path({str(marker)!r}).write_text('escaped')")
    child = ("import subprocess,sys,time; from pathlib import Path; "
             f"subprocess.Popen([sys.executable,'-c',{grandchild!r}], "
             "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
             f"ready=Path({str(ready)!r}); "
             "\nwhile not ready.exists(): time.sleep(0.01)\n"
             "print('parent-complete',flush=True)")
    result = run_readonly_jsonl([sys.executable, '-c', child], 'prompt',
                                cwd=tmp_path, timeout=10)
    assert ready.exists() and result.stdout.startswith('parent-complete')
    assert result.returncode == 0 and not result.overflow and not result.timed_out
    time.sleep(1.4)
    assert not marker.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object failure path')
def test_readonly_child_job_assignment_failure_is_closed_before_resume(tmp_path, monkeypatch):
    from shared_platform import worker_category_readonly_cli as cli

    marker = tmp_path / 'escaped.txt'
    child = f"from pathlib import Path; Path({str(marker)!r}).write_text('escaped')"
    launched = []
    real_popen = subprocess.Popen

    def track_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        launched.append(process)
        return process

    monkeypatch.setattr(cli.subprocess, 'Popen', track_popen)
    monkeypatch.setattr(cli._kernel32, 'AssignProcessToJobObject', lambda *_: 0)
    with pytest.raises(OSError):
        run_readonly_jsonl([sys.executable, '-c', child], 'prompt',
                           cwd=tmp_path, timeout=10)
    assert len(launched) == 1 and launched[0].poll() is not None
    assert not marker.exists()


def test_readonly_child_overflow_at_utf8_boundary_keeps_complete_prefix(tmp_path):
    code = "import sys; sys.stdout.buffer.write(b'AAAA\\xe4\\xb8\\xad'); sys.stdout.flush()"
    result = run_readonly_jsonl([sys.executable, '-c', code], 'prompt',
                                cwd=tmp_path, timeout=10, max_stdout_bytes=5)
    assert result.overflow and not result.timed_out
    assert result.stdout == 'AAAA'


def test_readonly_child_overflow_keeps_session_before_utf8_boundary(tmp_path):
    prefix = b'{"type":"thread.started","thread_id":"fixture"}\n'
    payload = prefix + b'AAAA\xe4\xb8\xad'
    code = f'import sys; sys.stdout.buffer.write({payload!r}); sys.stdout.flush()'
    result = run_readonly_jsonl([sys.executable, '-c', code], 'prompt',
                                cwd=tmp_path, timeout=10,
                                max_stdout_bytes=len(prefix) + 5)
    assert result.overflow and not result.timed_out
    assert _session_id(result.stdout) == 'fixture'


def test_readonly_child_overflow_does_not_repair_invalid_session_utf8(tmp_path):
    prefix = b'{"type":"thread.started","thread_id":"fi\xffxture"}\n'
    payload = prefix + b'AAAA\xe4\xb8\xad'
    code = f'import sys; sys.stdout.buffer.write({payload!r}); sys.stdout.flush()'
    with pytest.raises(ValueError, match='not UTF-8'):
        run_readonly_jsonl([sys.executable, '-c', code], 'prompt',
                           cwd=tmp_path, timeout=10,
                           max_stdout_bytes=len(prefix) + 5)
