"""Real kernel-lock scheduling, with file barriers and synthetic local data only."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.sourcing import image_generation_checkpoint as cp

DIGEST = "d" * 64


def wait_for(path: Path, timeout: float = 30, processes=()):
    deadline = time.monotonic() + timeout
    while not path.exists():
        for process in processes:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(f"fixture worker exited before {path.name}: pid={process.pid} "
                                     f"code={process.returncode} stdout={stdout!r} stderr={stderr!r}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"fixture barrier not reached: {path.name}")
        time.sleep(0.005)


def worker_env():
    # Test subprocess imports may transitively initialize numerical libraries.
    # Keep each worker at one native thread during a full-suite process run.
    env = os.environ.copy()
    env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1",
               NUMEXPR_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")
    return env


def worker(role: str, directory: Path):
    """Delay real stream operations; never inject a lock or I/O exception."""
    if role == "hold":
        with cp.business_lock(directory, DIGEST):
            (directory / "held").touch()
            wait_for(directory / "release-holder")
        return
    original_open = Path.open
    lock_path = directory / f".lingshi-{DIGEST[:24]}.lock"
    split_append = (directory / "native-split").exists()

    def mark(name):
        (directory / name).touch()

    class ScheduledStream:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def tell(self):
            value = self.stream.tell()
            if value == 0:
                mark(f"{role}-observed-empty")
                wait_for(directory / "A-observed-empty")
                wait_for(directory / "B-observed-empty")
                if role == "A":
                    wait_for(directory / ("B-append-positioned" if split_append else "B-buffered"))
            return value

        def write(self, value):
            result = self.stream.write(value)
            if role == "B":
                mark("B-buffered")
            return result

        def flush(self):
            if role == "B":
                if split_append:
                    # Test-only decomposition of append positioning and WriteFile.
                    # The same real handle still belongs to the real Python stream.
                    # No exception is injected: Windows must reject the actual write.
                    import ctypes
                    import msvcrt
                    from ctypes import wintypes
                    position = os.lseek(self.stream.fileno(), 0, os.SEEK_END)
                    assert position == 0
                    mark("B-append-positioned")
                    wait_for(directory / "A-entered")
                    mark("B-attempting")
                    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                    kernel.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                                 ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
                    kernel.WriteFile.restype = wintypes.BOOL
                    written = wintypes.DWORD()
                    value = ctypes.create_string_buffer(b"0")
                    result = kernel.WriteFile(msvcrt.get_osfhandle(self.stream.fileno()), value, 1,
                                              ctypes.byref(written), None)
                    error = ctypes.get_last_error() if not result else 0
                    (directory / "native-write.json").write_text(json.dumps({
                        "position_before_owner_acquired": position, "write_result": bool(result),
                        "bytes_written": written.value, "winerror": error}), encoding="utf-8")
                    if not result:
                        raise ctypes.WinError(error)
                wait_for(directory / "A-entered")
                mark("B-attempting")
            return self.stream.flush()

        def seek(self, offset, whence=os.SEEK_SET):
            if role == "B" and offset == 0 and whence == os.SEEK_SET:
                wait_for(directory / "A-entered")
                mark("B-attempting")
            return self.stream.seek(offset, whence)

    def scheduled_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path == lock_path:
            mark(f"{role}-opened")
            wait_for(directory / "A-opened")
            wait_for(directory / "B-opened")
            return ScheduledStream(stream)
        return stream

    Path.open = scheduled_open
    record = {"role": role, "pid": os.getpid(), "instrumentation":
              "test-only native append decomposition" if split_append else "original Python stream with file barriers"}
    try:
        with cp.business_lock(directory, DIGEST, timeout=3):
            record["entered_ns"] = time.monotonic_ns()
            mark(f"{role}-entered")
            if role == "A":
                wait_for(directory / "release-A")
            record["leaving_ns"] = time.monotonic_ns()
    except BaseException as error:
        record.update(error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
        record["thread_lock_left_locked"] = cp._THREAD_LOCKS[str(lock_path.resolve())].locked()
        raise
    finally:
        Path.open = original_open
        (directory / f"{role}-result.json").write_text(json.dumps(record, indent=2), encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="Windows byte-range I/O regression")
@pytest.mark.parametrize("split_append", [False, True], ids=["python-stream", "native-append-gap"])
def test_two_processes_opening_empty_file_serialize_without_initialization_write(tmp_path, split_append):
    if split_append:
        (tmp_path / "native-split").touch()
    options = dict(cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                   creationflags=subprocess.CREATE_NO_WINDOW, env=worker_env())
    processes = [subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), role, str(tmp_path)],
                                  **options) for role in ("A", "B")]
    outputs = []
    try:
        wait_for(tmp_path / "A-entered", timeout=45, processes=processes)
        wait_for(tmp_path / "B-attempting", timeout=45, processes=processes)
        # A continues to own the real kernel lock while B attempts initialization/acquisition.
        time.sleep(0.2)
        before_release = [process.poll() for process in processes]
        (tmp_path / "release-A").touch()
        outputs = [process.communicate(timeout=10) for process in processes]
    finally:
        (tmp_path / "release-A").touch()
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
    for role, (stdout, stderr) in zip(("A", "B"), outputs):
        (tmp_path / f"{role}-stdout.txt").write_text(stdout, encoding="utf-8")
        (tmp_path / f"{role}-stderr.txt").write_text(stderr, encoding="utf-8")
    lock = next(tmp_path.glob(".lingshi-*.lock"))
    evidence = {"pids": [p.pid for p in processes], "exit_codes": [p.returncode for p in processes],
                "before_release_exit_codes": before_release, "lock_bytes_hex_after_exit": lock.read_bytes().hex(),
                "lock_size_after_exit": lock.stat().st_size,
                "events": sorted(p.name for p in tmp_path.iterdir() if p.suffix == ""),
                "workers": [json.loads((tmp_path / f"{role}-result.json").read_text()) for role in ("A", "B")]}
    (tmp_path / "process-evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    assert evidence["exit_codes"] == [0, 0], evidence
    assert before_release == [None, None], evidence
    assert evidence["workers"][1]["entered_ns"] >= evidence["workers"][0]["leaving_ns"]
    assert lock.read_bytes() == b""


class CloseFailure(OSError):
    pass


@contextmanager
def closing_failure(monkeypatch, tmp_path):
    original_open = Path.open
    streams = []

    class Stream:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def close(self):
            self.stream.close()
            raise CloseFailure("fixture close failed after handle release")

    def open_stream(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path.parent == tmp_path and path.suffix == ".lock":
            streams.append(stream)
            return Stream(stream)
        return stream

    monkeypatch.setattr(Path, "open", open_stream)
    yield streams


def test_body_error_survives_close_error_and_releases_thread_lock(tmp_path, monkeypatch):
    original = ValueError("fixture original body error")
    with closing_failure(monkeypatch, tmp_path) as streams:
        with pytest.raises(ValueError) as error:
            with cp.business_lock(tmp_path, DIGEST):
                raise original
    assert error.value is original
    assert any("CloseFailure" in note for note in original.__notes__)
    assert all(stream.closed for stream in streams)
    assert not cp._THREAD_LOCKS[str((tmp_path / f".lingshi-{DIGEST[:24]}.lock").resolve())].locked()


def test_close_error_alone_is_reported_and_releases_thread_lock(tmp_path, monkeypatch):
    with closing_failure(monkeypatch, tmp_path) as streams:
        with pytest.raises(CloseFailure):
            with cp.business_lock(tmp_path, DIGEST):
                pass
    assert all(stream.closed for stream in streams)
    assert not cp._THREAD_LOCKS[str((tmp_path / f".lingshi-{DIGEST[:24]}.lock").resolve())].locked()


@pytest.mark.parametrize("initial", [b"", b"0", b"00"], ids=["empty", "one-byte", "two-bytes"])
@pytest.mark.parametrize("close_fails", [False, True], ids=["normal-close", "close-fails"])
def test_real_process_timeout_recovers_and_preserves_existing_file(tmp_path, monkeypatch, initial, close_fails):
    lock = tmp_path / f".lingshi-{DIGEST[:24]}.lock"
    lock.write_bytes(initial)
    process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "hold", str(tmp_path)],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, env=worker_env())
    try:
        wait_for(tmp_path / "held", timeout=45, processes=(process,))
        started = time.monotonic()
        with monkeypatch.context() as patch:
            if close_fails:
                with closing_failure(patch, tmp_path) as streams:
                    with pytest.raises(cp.CheckpointRecoveryRequired, match="another process") as error:
                        with cp.business_lock(tmp_path, DIGEST, timeout=0.1):
                            pytest.fail("peer owns this business identity")
                assert all(stream.closed for stream in streams)
                assert any("CloseFailure" in note for note in error.value.__notes__)
            else:
                with pytest.raises(cp.CheckpointRecoveryRequired, match="another process"):
                    with cp.business_lock(tmp_path, DIGEST, timeout=0.1):
                        pytest.fail("peer owns this business identity")
        assert 0.09 <= time.monotonic() - started < 2
        assert not cp._THREAD_LOCKS[str(lock.resolve())].locked()
    finally:
        (tmp_path / "release-holder").touch()
        stdout, stderr = process.communicate(timeout=10)
    (tmp_path / "holder-evidence.json").write_text(json.dumps({"pid": process.pid, "exit_code": process.returncode,
        "stdout": stdout, "stderr": stderr, "initial_hex": initial.hex(), "final_hex": lock.read_bytes().hex()}), encoding="utf-8")
    assert process.returncode == 0, stderr
    with cp.business_lock(tmp_path, DIGEST, timeout=0):
        # Windows locks also deny a second handle in the owning process.
        assert lock.exists()
    assert lock.exists() and lock.read_bytes() == initial


def test_open_failure_releases_local_lock_and_allows_recovery(tmp_path, monkeypatch):
    original_open = Path.open
    original = OSError("fixture lock file open failure")

    def fail_lock_open(path, *args, **kwargs):
        if path.suffix == ".lock":
            raise original
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", fail_lock_open)
        with pytest.raises(OSError) as error:
            with cp.business_lock(tmp_path, DIGEST):
                pytest.fail("open failed")
        assert error.value is original
    with cp.business_lock(tmp_path, DIGEST, timeout=0):
        pass


def test_local_thread_timeout_does_not_release_the_owner(tmp_path):
    held, release = threading.Event(), threading.Event()

    def owner():
        with cp.business_lock(tmp_path, DIGEST):
            held.set()
            assert release.wait(5)

    thread = threading.Thread(target=owner)
    thread.start()
    try:
        assert held.wait(5)
        with pytest.raises(cp.CheckpointRecoveryRequired, match="another local writer"):
            with cp.business_lock(tmp_path, DIGEST, timeout=0):
                pytest.fail("thread owner still holds the lock")
        assert cp._THREAD_LOCKS[str((tmp_path / f".lingshi-{DIGEST[:24]}.lock").resolve())].locked()
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    with cp.business_lock(tmp_path, DIGEST, timeout=0):
        pass


@pytest.mark.skipif(os.name != "nt", reason="Windows kernel unlock cleanup")
def test_unlock_failure_remains_primary_if_close_also_fails(tmp_path, monkeypatch):
    import msvcrt
    original_locking = msvcrt.locking
    original = OSError("fixture unlock failure")

    def fail_unlock(fd, mode, size):
        if mode == msvcrt.LK_UNLCK:
            raise original
        return original_locking(fd, mode, size)

    with monkeypatch.context() as patch:
        patch.setattr(msvcrt, "locking", fail_unlock)
        with closing_failure(patch, tmp_path) as streams:
            with pytest.raises(OSError) as error:
                with cp.business_lock(tmp_path, DIGEST):
                    pass
        assert error.value is original
        assert all(stream.closed for stream in streams)
        assert any("CloseFailure" in note for note in original.__notes__)
    with cp.business_lock(tmp_path, DIGEST, timeout=0):
        pass


if __name__ == "__main__":
    worker(sys.argv[1], Path(sys.argv[2]))
