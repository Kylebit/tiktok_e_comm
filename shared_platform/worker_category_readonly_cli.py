"""Bounded stdout transport for the read-only R1 facts child.

The child owns no result file. The parent retains at most MAX_JSONL_BYTES in
memory and kills a child that exceeds the limit or times out.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


MAX_JSONL_BYTES = 1024 * 1024


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

    class _BasicJobLimit(ctypes.Structure):
        _fields_ = [('per_process_time', ctypes.c_int64), ('per_job_time', ctypes.c_int64),
                    ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                    ('max_working_set', ctypes.c_size_t), ('active_process_limit', wintypes.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority_class', wintypes.DWORD),
                    ('scheduling_class', wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in
                    ('read_operations', 'write_operations', 'other_operations',
                     'read_bytes', 'write_bytes', 'other_bytes')]

    class _ExtendedJobLimit(ctypes.Structure):
        _fields_ = [('basic', _BasicJobLimit), ('io', _IoCounters),
                    ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                    ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]

    class _ThreadEntry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                    ('thread_id', wintypes.DWORD), ('owner_pid', wintypes.DWORD),
                    ('base_priority', wintypes.LONG), ('delta_priority', wintypes.LONG),
                    ('flags', wintypes.DWORD)]

    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    _kernel32.SetInformationJobObject.argtypes = (ctypes.c_void_p, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD)
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    _kernel32.Thread32First.argtypes = (ctypes.c_void_p, ctypes.POINTER(_ThreadEntry))
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = (ctypes.c_void_p, ctypes.POINTER(_ThreadEntry))
    _kernel32.Thread32Next.restype = wintypes.BOOL
    _kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenThread.restype = ctypes.c_void_p
    _kernel32.ResumeThread.argtypes = (ctypes.c_void_p,)
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _start_child(argv: list[str], cwd: Path, *, invocation=None) -> tuple[subprocess.Popen, Callable[[], None]]:
    options = dict(cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                   stderr=subprocess.DEVNULL,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if os.name != 'nt':
        if invocation is not None:
            options['env'] = invocation.verify_launch(argv, cwd)
        process = subprocess.Popen(argv, start_new_session=True, **options)
        return process, lambda: os.killpg(process.pid, signal.SIGKILL)

    # Suspend before any child code can spawn descendants. CPython closes the
    # initial thread handle, so reopen it by PID after the Job assignment.
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    process = None
    try:
        limits = _ExtendedJobLimit()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        options['creationflags'] |= getattr(subprocess, 'CREATE_SUSPENDED', 0x00000004)
        if invocation is not None:
            options['env'] = invocation.verify_launch(argv, cwd)
        process = subprocess.Popen(argv, **options)
        if not _kernel32.AssignProcessToJobObject(job, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        snapshot = _kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = _ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            if not _kernel32.Thread32First(snapshot, ctypes.byref(entry)):
                raise ctypes.WinError(ctypes.get_last_error())
            while entry.owner_pid != process.pid:
                if not _kernel32.Thread32Next(snapshot, ctypes.byref(entry)):
                    raise OSError('R1 facts suspended child thread was not found')
            thread = _kernel32.OpenThread(0x0002, False, entry.thread_id)
            if not thread:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if _kernel32.ResumeThread(thread) == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                _kernel32.CloseHandle(thread)
        finally:
            _kernel32.CloseHandle(snapshot)
    except BaseException:
        _kernel32.CloseHandle(job)
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    if process.poll() is None:
                        raise
            process.wait()
        raise
    def close_job():
        if not _kernel32.CloseHandle(job):
            raise ctypes.WinError(ctypes.get_last_error())
    return process, close_job


@dataclass(frozen=True)
class ReadonlyCodexResult:
    returncode: int | None
    stdout: str
    overflow: bool = False
    timed_out: bool = False


def run_readonly_jsonl(argv: list[str], prompt: str, *, cwd: Path,
                       timeout: int, max_stdout_bytes: int = MAX_JSONL_BYTES, invocation=None
                       ) -> ReadonlyCodexResult:
    """Stream JSONL into a fixed size buffer; never grant a child file output."""
    if max_stdout_bytes < 1:
        raise ValueError('stdout limit must be positive')
    if not isinstance(prompt, str) or len(prompt.encode('utf-8')) > MAX_JSONL_BYTES:
        raise ValueError('R1 facts prompt exceeds input bound')
    if invocation is None:
        process, stop_tree = _start_child(argv, cwd)
    else:
        from shared_platform.native_readonly_invocation import NativeReadonlyInvocation
        if type(invocation) is not NativeReadonlyInvocation:
            raise ValueError('readonly_native_verified_invocation_required')
        process, stop_tree = _start_child(argv, cwd, invocation=invocation)
    captured = bytearray()
    overflow = threading.Event()
    read_error: list[BaseException] = []

    stop_lock = threading.Lock()
    stopped = False

    def stop_child():
        nonlocal stopped
        with stop_lock:
            if not stopped:
                try:
                    stop_tree()
                except ProcessLookupError:
                    pass
                stopped = True

    def read_stdout():
        try:
            while True:
                # read() waits to fill its buffer or see EOF on a live pipe.
                # read1() returns the bytes available from one raw read, so
                # cap+1 bytes stop a sleeping child without waiting for timeout.
                chunk = process.stdout.read1(8192)
                if not chunk:
                    break
                remaining = max_stdout_bytes - len(captured)
                captured.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()
                    stop_child()
                    break
        except BaseException as error:
            read_error.append(error)
            stop_child()

    def write_prompt():
        try:
            process.stdin.write(prompt.encode('utf-8'))
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    reader = threading.Thread(target=read_stdout, daemon=True)
    writer = threading.Thread(target=write_prompt, daemon=True)
    timed_out = False
    try:
        reader.start()
        writer.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_child()
            process.wait()
    finally:
        stop_child()
    reader.join(timeout=5)
    writer.join(timeout=5)
    if reader.is_alive() or writer.is_alive() or read_error:
        raise OSError('R1 facts child pipe did not close cleanly')
    try:
        stdout = captured.decode('utf-8')
    except UnicodeDecodeError as error:
        if (not overflow.is_set() or error.reason != 'unexpected end of data'
                or error.end != len(captured)):
            raise ValueError('R1 facts child stdout is not UTF-8') from error
        # Only discard a partial final character cut by the byte limit.
        # Invalid bytes in earlier events must never change a session ID.
        stdout = captured[:error.start].decode('utf-8')
    return ReadonlyCodexResult(None if overflow.is_set() or timed_out else process.returncode, stdout,
                              overflow.is_set(), timed_out)
