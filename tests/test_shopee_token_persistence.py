from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import threading
import time
import urllib.error

import pytest

from modules.shopee import auth
from modules.shopee import shops as shop_registry


def _lock_worker(token_file, guard_file, ready, start, results):
    from modules.shopee.auth import _token_store_lock

    ready.put(True)
    start.wait(5)
    with _token_store_lock(Path(token_file), timeout=5):
        try:
            descriptor = os.open(guard_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            results.put("overlap")
            return
        os.close(descriptor)
        time.sleep(0.1)
        Path(guard_file).unlink()
        results.put("ok")


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _store(path: Path):
    path.write_text(json.dumps({
        "shops": {
            "101": {"shop_id": 101, "access_token": "old-a", "refresh_token": "old-r-a", "expire_at": 1},
            "202": {"shop_id": 202, "access_token": "old-b", "refresh_token": "old-r-b", "expire_at": 1},
        }
    }), encoding="utf-8")


def _bind(monkeypatch, path: Path):
    monkeypatch.setattr(auth, "token_path", lambda: path)
    monkeypatch.setattr(auth, "shopee_config", lambda: {
        "host": "https://provider.invalid", "partner_id": 7, "partner_key": "not-a-real-secret",
    })
    monkeypatch.setattr(auth, "sign_partner", lambda *_args: (123, "redacted-signature"))


def test_concurrent_different_shop_refreshes_are_serialized_and_do_not_lose_updates(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    _store(path)
    _bind(monkeypatch, path)
    active = 0
    maximum_active = 0
    active_lock = threading.Lock()
    start = threading.Barrier(3)

    def provider(request, **_kwargs):
        nonlocal active, maximum_active
        body = json.loads(request.data)
        with active_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.05)
        with active_lock:
            active -= 1
        shop_id = body["shop_id"]
        return _Response({
            "access_token": f"new-access-{shop_id}",
            "refresh_token": f"new-refresh-{shop_id}",
            "expire_in": 3600,
        })

    monkeypatch.setattr(auth, "urlopen_retry", provider)
    errors = []

    def run(shop_id):
        start.wait()
        try:
            auth.refresh_token(shop_id)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(shop_id,)) for shop_id in (101, 202)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=5)
    assert errors == []
    assert maximum_active == 1
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["shops"]["101"]["refresh_token"] == "new-refresh-101"
    assert saved["shops"]["202"]["refresh_token"] == "new-refresh-202"
    assert path.with_name("tokens.json.bak").exists()


def test_token_store_lock_serializes_independent_processes(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    results = context.Queue()
    start = context.Event()
    token_file = tmp_path / "tokens.json"
    guard_file = tmp_path / "critical-section.guard"
    processes = [
        context.Process(
            target=_lock_worker,
            args=(str(token_file), str(guard_file), ready, start, results),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    assert ready.get(timeout=5) is True
    assert ready.get(timeout=5) is True
    start.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert sorted([results.get(timeout=2), results.get(timeout=2)]) == ["ok", "ok"]


def test_unknown_transport_outcome_is_redacted_and_blocks_retry(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    _store(path)
    _bind(monkeypatch, path)
    calls = 0

    def provider(*_args, **kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["attempts"] == 1
        assert kwargs["allow_curl_fallback"] is False
        raise TimeoutError("unknown provider outcome with secret-that-must-not-be-logged")

    monkeypatch.setattr(auth, "urlopen_retry", provider)
    with pytest.raises(auth.RefreshReconciliationRequired):
        auth.refresh_token(101)
    with pytest.raises(auth.RefreshReconciliationRequired):
        auth.refresh_token(101)
    assert calls == 1
    state = json.loads(path.with_name("tokens.json.refresh-state.json").read_text(encoding="utf-8"))
    entry = state["shops"]["101"]
    assert entry["status"] == "UNKNOWN"
    assert entry["error_type"] == "TimeoutError"
    assert "secret" not in json.dumps(state)
    assert "old-r-a" not in json.dumps(state)
    assert json.loads(path.read_text(encoding="utf-8"))["shops"]["101"]["refresh_token"] == "old-r-a"


def test_preflight_state_failure_prevents_provider_call(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    _store(path)
    _bind(monkeypatch, path)
    calls = 0

    def provider(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Response({})

    monkeypatch.setattr(auth, "urlopen_retry", provider)
    monkeypatch.setattr(
        auth,
        "_write_refresh_state_unlocked",
        lambda *_args: (_ for _ in ()).throw(OSError("synthetic preflight persistence failure")),
    )
    with pytest.raises(OSError, match="preflight persistence failure"):
        auth.refresh_token(101)
    assert calls == 0
    assert json.loads(path.read_text(encoding="utf-8"))["shops"]["101"]["refresh_token"] == "old-r-a"


def test_incomplete_success_response_is_unknown_and_never_replayed(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    _store(path)
    _bind(monkeypatch, path)
    calls = 0

    def provider(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Response({"refresh_token": "possibly-rotated", "expire_in": 3600})

    monkeypatch.setattr(auth, "urlopen_retry", provider)
    with pytest.raises(auth.RefreshReconciliationRequired):
        auth.refresh_token(101)
    with pytest.raises(auth.RefreshReconciliationRequired):
        auth.refresh_token(101)
    assert calls == 1
    state = json.loads(path.with_name("tokens.json.refresh-state.json").read_text(encoding="utf-8"))
    assert state["shops"]["101"]["status"] == "UNKNOWN"
    assert "possibly-rotated" not in json.dumps(state)


def test_atomic_replace_failure_after_rotation_blocks_retry_and_keeps_old_file(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    _store(path)
    _bind(monkeypatch, path)
    monkeypatch.setattr(auth, "urlopen_retry", lambda *_args, **_kwargs: _Response({
        "access_token": "rotated-access", "refresh_token": "rotated-refresh", "expire_in": 3600,
    }))
    real_replace = auth.os.replace

    def fail_token_replace(source, target):
        if Path(target) == path:
            raise OSError("synthetic replace failure")
        return real_replace(source, target)

    monkeypatch.setattr(auth.os, "replace", fail_token_replace)
    with pytest.raises(auth.RefreshReconciliationRequired):
        auth.refresh_token(101)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["shops"]["101"]["refresh_token"] == "old-r-a"
    state = json.loads(path.with_name("tokens.json.refresh-state.json").read_text(encoding="utf-8"))
    assert state["shops"]["101"]["status"] == "UNKNOWN"


def test_committed_token_survives_status_log_failure_and_next_call_does_not_refresh(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    _store(path)
    _bind(monkeypatch, path)
    calls = 0

    def provider(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Response({"access_token": "new-access", "refresh_token": "new-refresh", "expire_in": 3600})

    monkeypatch.setattr(auth, "urlopen_retry", provider)
    real_state_write = auth._write_refresh_state_unlocked
    fail_once = True

    def state_write(token_file, state):
        nonlocal fail_once
        entry = state.get("shops", {}).get("101", {})
        if fail_once and entry.get("status") == "COMMITTED":
            fail_once = False
            raise OSError("synthetic audit write failure")
        return real_state_write(token_file, state)

    monkeypatch.setattr(auth, "_write_refresh_state_unlocked", state_write)
    result = auth.refresh_token(101)
    assert result["refresh_token"] == "new-refresh"
    recovered = auth.refresh_token(101)
    assert recovered["refresh_token"] == "new-refresh"
    assert calls == 1
    state = json.loads(path.with_name("tokens.json.refresh-state.json").read_text(encoding="utf-8"))
    assert state["shops"]["101"]["status"] == "COMMITTED_RECOVERED"


def test_shop_metadata_update_cannot_restore_a_rotated_credential(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    _store(path)
    _bind(monkeypatch, path)
    monkeypatch.setattr(shop_registry, "shopee_config", lambda: {"regions": ["TH", "MY"]})
    rotated = False

    def get_shop_info(shop_id, _token):
        nonlocal rotated
        if not rotated:
            latest = json.loads(path.read_text(encoding="utf-8"))
            latest["shops"]["101"]["access_token"] = "rotated-access"
            latest["shops"]["101"]["refresh_token"] = "rotated-refresh"
            auth._save_tokens_unlocked(path, latest)
            rotated = True
        return {"response": {"region": "TH" if shop_id == 101 else "MY", "shop_name": str(shop_id)}}

    monkeypatch.setattr(shop_registry, "get_shop_info", get_shop_info)
    shop_registry.refresh_shop_regions(quiet=True)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["shops"]["101"]["access_token"] == "rotated-access"
    assert saved["shops"]["101"]["refresh_token"] == "rotated-refresh"
    assert saved["shops"]["101"]["region"] == "TH"


def test_legacy_stale_whole_store_save_cannot_restore_rotated_credentials(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    _store(path)
    _bind(monkeypatch, path)
    stale = auth.load_tokens()
    current = auth.load_tokens()
    current["shops"]["101"]["access_token"] = "rotated-access"
    current["shops"]["101"]["refresh_token"] = "rotated-refresh"
    current["shops"]["101"]["expire_at"] = 999
    auth._save_tokens_unlocked(path, current)

    stale["shops"]["202"]["region"] = "MY"
    stale["shops"]["303"] = {
        "shop_id": 303,
        "access_token": "new-shop-access",
        "refresh_token": "new-shop-refresh",
        "expire_at": 888,
    }
    auth.save_tokens(stale)

    saved = auth.load_tokens()
    assert saved["shops"]["101"]["access_token"] == "rotated-access"
    assert saved["shops"]["101"]["refresh_token"] == "rotated-refresh"
    assert saved["shops"]["101"]["expire_at"] == 999
    assert saved["shops"]["202"]["region"] == "MY"
    assert saved["shops"]["303"]["refresh_token"] == "new-shop-refresh"


@pytest.mark.parametrize(
    "outcome",
    ["http500", "non_dict", "provider_error"],
)
def test_ambiguous_provider_outcomes_are_redacted_and_never_replayed(
    tmp_path, monkeypatch, outcome
):
    path = tmp_path / "tokens.json"
    _store(path)
    _bind(monkeypatch, path)
    calls = 0

    def provider(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if outcome == "http500":
            raise urllib.error.HTTPError(
                "https://provider.invalid", 500, "server error", None, None
            )
        if outcome == "non_dict":
            return _Response(["unexpected"])
        return _Response({"error": "SECRET_MARKER_must_not_persist"})

    monkeypatch.setattr(auth, "urlopen_retry", provider)
    with pytest.raises(auth.RefreshReconciliationRequired):
        auth.refresh_token(101)
    with pytest.raises(auth.RefreshReconciliationRequired):
        auth.refresh_token(101)
    assert calls == 1
    state_text = path.with_name("tokens.json.refresh-state.json").read_text(encoding="utf-8")
    assert "SECRET_MARKER" not in state_text
    assert json.loads(state_text)["shops"]["101"]["status"] == "UNKNOWN"


def test_merchant_refresh_unknown_outcome_is_single_attempt_and_redacted(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    _store(path)
    store = json.loads(path.read_text(encoding="utf-8"))
    store["merchants"] = {
        "303": {
            "merchant_id": 303,
            "access_token": "merchant-old-a",
            "refresh_token": "merchant-old-r",
            "expire_at": 1,
        }
    }
    path.write_text(json.dumps(store), encoding="utf-8")
    _bind(monkeypatch, path)
    calls = 0

    def provider(*_args, **kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["attempts"] == 1
        assert kwargs["allow_curl_fallback"] is False
        raise TimeoutError("SECRET_MARKER")

    monkeypatch.setattr(auth, "urlopen_retry", provider)
    with pytest.raises(auth.RefreshReconciliationRequired):
        auth.refresh_merchant_token(303)
    with pytest.raises(auth.RefreshReconciliationRequired):
        auth.refresh_merchant_token(303)
    assert calls == 1
    state_text = path.with_name("tokens.json.refresh-state.json").read_text(encoding="utf-8")
    assert "SECRET_MARKER" not in state_text
    assert json.loads(state_text)["merchants"]["303"]["status"] == "UNKNOWN"


def test_uncertain_shop_refresh_blocks_same_credential_through_merchant_alias(
    tmp_path, monkeypatch
):
    path = tmp_path / "tokens.json"
    shared = "shared-synthetic-refresh"
    path.write_text(
        json.dumps(
            {
                "shops": {
                    "101": {
                        "shop_id": 101,
                        "access_token": "shop-old",
                        "refresh_token": shared,
                        "expire_at": 1,
                    }
                },
                "merchants": {
                    "303": {
                        "merchant_id": 303,
                        "access_token": "merchant-old",
                        "refresh_token": shared,
                        "expire_at": 1,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    _bind(monkeypatch, path)
    calls = 0

    def provider(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("unknown")

    monkeypatch.setattr(auth, "urlopen_retry", provider)
    with pytest.raises(auth.RefreshReconciliationRequired):
        auth.refresh_token(101)
    with pytest.raises(auth.RefreshReconciliationRequired, match="alias"):
        auth.refresh_merchant_token(303)
    assert calls == 1


def test_uncertain_shop_refresh_does_not_block_independent_merchant_credential(
    tmp_path, monkeypatch
):
    path = tmp_path / "tokens.json"
    path.write_text(
        json.dumps(
            {
                "shops": {
                    "101": {
                        "shop_id": 101,
                        "access_token": "shop-old",
                        "refresh_token": "shop-refresh",
                        "expire_at": 1,
                    }
                },
                "merchants": {
                    "303": {
                        "merchant_id": 303,
                        "access_token": "merchant-old",
                        "refresh_token": "independent-merchant-refresh",
                        "expire_at": 1,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    _bind(monkeypatch, path)
    calls = []

    def provider(request, **_kwargs):
        body = json.loads(request.data)
        calls.append(body)
        if "shop_id" in body:
            raise TimeoutError("unknown shop outcome")
        return _Response(
            {
                "access_token": "merchant-new",
                "refresh_token": "independent-merchant-new",
                "expire_in": 3600,
            }
        )

    monkeypatch.setattr(auth, "urlopen_retry", provider)
    with pytest.raises(auth.RefreshReconciliationRequired):
        auth.refresh_token(101)
    refreshed = auth.refresh_merchant_token(303)
    assert refreshed["refresh_token"] == "independent-merchant-new"
    assert len(calls) == 2


def test_merchant_refresh_never_falls_back_to_an_arbitrary_shop_credential(
    tmp_path, monkeypatch
):
    path = tmp_path / "tokens.json"
    _store(path)
    _bind(monkeypatch, path)
    calls = 0

    def provider(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Response({})

    monkeypatch.setattr(auth, "urlopen_retry", provider)
    with pytest.raises(RuntimeError, match="explicitly bound"):
        auth.refresh_merchant_token(303)
    assert calls == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL regression")
def test_atomic_replacement_and_backup_preserve_restricted_token_dacl(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_bytes(b'{"shops": {}}')
    auth._apply_windows_dacl(path, None)
    expected = auth._windows_dacl_bytes(path)

    auth._save_tokens_unlocked(path, {"shops": {"101": {"refresh_token": "synthetic"}}})

    assert auth._windows_dacl_bytes(path) == expected
    assert auth._windows_dacl_bytes(path.with_name("tokens.json.bak")) == expected
