"""Shopee OAuth：授权链接、换 token、刷新。"""

from __future__ import annotations

import json
import hashlib
import os
import ctypes
import copy
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from core.config import ROOT
from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import urlopen as urlopen_retry
from modules.shopee.config import ready, shopee_config
from modules.shopee.sign import sign_partner


_TOKEN_STORE_THREAD_LOCK = threading.RLock()
_LOCK_TIMEOUT_SECONDS = 30.0


class RefreshReconciliationRequired(RuntimeError):
    """A refresh may have rotated credentials and must not be replayed."""


def token_path() -> Path:
    rel = shopee_config()["token_file"]
    pinned = os.environ.get("ORBIT_SHOPEE_TOKEN_PATH")
    if pinned:
        path = Path(pinned).expanduser()
        if not path.is_absolute():
            raise RuntimeError("ORBIT_SHOPEE_TOKEN_PATH must be absolute")
        return path
    p = Path(rel)
    return p if p.is_absolute() else ROOT / rel


def load_tokens() -> dict:
    return _load_tokens_from(token_path())


def _load_tokens_from(path: Path) -> dict:
    if not path.is_file():
        return {"shops": {}}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_tokens(data: dict) -> None:
    """Merge metadata while refusing to overwrite existing credentials.

    Existing access/refresh tokens can only change through a guarded exchange
    or refresh path.  This makes legacy stale whole-store writers harmless.
    """
    path = token_path()
    with _token_store_lock(path):
        current = _load_tokens_from(path)
        merged = _merge_without_credential_overwrite(current, data)
        _save_tokens_unlocked(path, merged)


def _merge_without_credential_overwrite(current: dict, incoming: dict) -> dict:
    merged = copy.deepcopy(current)
    for key, value in incoming.items():
        if key not in {"shops", "merchants"}:
            merged[key] = copy.deepcopy(value)
    credential_fields = {"access_token", "refresh_token", "expire_at"}
    for collection in ("shops", "merchants"):
        current_entries = current.get(collection) or {}
        merged_entries = merged.setdefault(collection, {})
        for entity_id, proposed in (incoming.get(collection) or {}).items():
            if not isinstance(proposed, dict):
                continue
            existing = current_entries.get(str(entity_id)) or current_entries.get(entity_id)
            if not isinstance(existing, dict):
                merged_entries[str(entity_id)] = copy.deepcopy(proposed)
                continue
            entry = {**copy.deepcopy(existing), **copy.deepcopy(proposed)}
            for field in credential_fields:
                if field in existing:
                    entry[field] = existing[field]
            merged_entries[str(entity_id)] = entry
    return merged


def update_tokens(mutator):
    """Apply a non-network mutation to the latest store under the store lock."""
    path = token_path()
    with _token_store_lock(path):
        store = _load_tokens_from(path)
        result = mutator(store)
        _save_tokens_unlocked(path, store)
        return result


def _save_tokens_unlocked(path: Path, data: dict) -> None:
    encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.is_file():
        _atomic_write_bytes(
            path.with_name(path.name + ".bak"),
            path.read_bytes(),
            security_source=path,
        )
    _atomic_write_bytes(path, encoded, security_source=path if path.is_file() else None)


def _atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    security_source: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if os.name == "nt":
            _apply_windows_dacl(temporary, security_source)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _windows_dacl_bytes(path: Path) -> bytes:
    """Return the self-relative Windows DACL descriptor without secret data."""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    get_security = advapi32.GetFileSecurityW
    get_security.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    get_security.restype = ctypes.c_int
    needed = ctypes.c_uint32()
    dacl_information = 0x00000004
    get_security(str(path), dacl_information, None, 0, ctypes.byref(needed))
    if not needed.value:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(needed.value)
    if not get_security(
        str(path), dacl_information, buffer, needed.value, ctypes.byref(needed)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return bytes(buffer.raw[: needed.value])


def _apply_windows_dacl(path: Path, source: Path | None) -> None:
    """Preserve an existing DACL, or protect a new secret file owner-only."""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    set_security = advapi32.SetFileSecurityW
    set_security.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
    set_security.restype = ctypes.c_int
    dacl_information = 0x00000004
    protected_dacl = 0x80000000
    if source is not None and source.is_file():
        payload = _windows_dacl_bytes(source)
        buffer = ctypes.create_string_buffer(payload)
        if not set_security(str(path), dacl_information, buffer):
            raise ctypes.WinError(ctypes.get_last_error())
        return

    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    convert.restype = ctypes.c_int
    security_descriptor = ctypes.c_void_p()
    if not convert("D:P(A;;FA;;;OW)", 1, ctypes.byref(security_descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not set_security(
            str(path),
            dacl_information | protected_dacl,
            security_descriptor,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(security_descriptor)


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _token_store_lock(path: Path, timeout: float = _LOCK_TIMEOUT_SECONDS):
    """Serialize every token-store refresh across threads and processes."""
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _TOKEN_STORE_THREAD_LOCK:
        with lock_path.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    _lock_stream(stream)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Shopee token store lock timed out") from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                _unlock_stream(stream)


def _lock_stream(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_stream(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _credential_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _refresh_state_path(path: Path) -> Path:
    return path.with_name(path.name + ".refresh-state.json")


def _load_refresh_state_unlocked(path: Path) -> dict:
    state_path = _refresh_state_path(path)
    if not state_path.is_file():
        return {
            "schema_version": "shopee-refresh-state/v1",
            "shops": {},
            "merchants": {},
        }
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RefreshReconciliationRequired(
            f"Shopee refresh state is unreadable ({type(exc).__name__})"
        ) from None
    if state.get("schema_version") != "shopee-refresh-state/v1" or not isinstance(state.get("shops"), dict):
        raise RefreshReconciliationRequired("Shopee refresh state schema is invalid")
    if not isinstance(state.setdefault("merchants", {}), dict):
        raise RefreshReconciliationRequired("Shopee refresh state merchant schema is invalid")
    return state


def _write_refresh_state_unlocked(token_file: Path, state: dict) -> None:
    payload = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write_bytes(_refresh_state_path(token_file), payload)


def _set_refresh_state_unlocked(
    token_file: Path,
    state: dict,
    shop_id: int,
    status: str,
    *,
    old_refresh_digest: str,
    new_refresh_digest: str | None = None,
    error_type: str | None = None,
) -> None:
    _set_entity_refresh_state_unlocked(
        token_file,
        state,
        "shops",
        shop_id,
        status,
        old_refresh_digest=old_refresh_digest,
        new_refresh_digest=new_refresh_digest,
        error_type=error_type,
    )


def _set_entity_refresh_state_unlocked(
    token_file: Path,
    state: dict,
    collection: str,
    entity_id: int,
    status: str,
    *,
    old_refresh_digest: str,
    new_refresh_digest: str | None = None,
    error_type: str | None = None,
) -> None:
    entry = {
        "entity_id": int(entity_id),
        "status": status,
        "old_refresh_digest": old_refresh_digest,
        "updated_at": int(time.time()),
    }
    entry["shop_id" if collection == "shops" else "merchant_id"] = int(entity_id)
    if new_refresh_digest:
        entry["new_refresh_digest"] = new_refresh_digest
    if error_type:
        entry["error_type"] = error_type
    state.setdefault(collection, {})[str(entity_id)] = entry
    _write_refresh_state_unlocked(token_file, state)


def auth_partner_url() -> str:
    """生成店铺授权链接（浏览器打开，回调 URL 带 code + shop_id）。"""
    if not ready():
        raise RuntimeError("未配置 shopee.partner_id / partner_key")
    c = shopee_config()
    path = "/api/v2/shop/auth_partner"
    ts, sig = sign_partner(path, c["partner_id"], c["partner_key"])
    q = {
        "partner_id": c["partner_id"],
        "timestamp": ts,
        "sign": sig,
        "redirect": c["redirect_url"],
    }
    return f"{c['auth_host']}{path}?{urllib.parse.urlencode(q)}"


def _token_get_body(code: str, *, shop_id: int | None = None, main_account_id: int | None = None) -> bytes:
    c = shopee_config()
    payload: dict = {"code": code, "partner_id": c["partner_id"]}
    if main_account_id is not None:
        payload["main_account_id"] = int(main_account_id)
    elif shop_id is not None:
        payload["shop_id"] = int(shop_id)
    else:
        raise ValueError("需要 shop_id 或 main_account_id")
    return json.dumps(payload).encode("utf-8")


def _call_token_get(code: str, *, shop_id: int | None = None, main_account_id: int | None = None) -> dict:
    if not ready():
        raise RuntimeError("未配置 shopee")
    c = shopee_config()
    path = "/api/v2/auth/token/get"
    ts, sig = sign_partner(path, c["partner_id"], c["partner_key"])
    url = f"{c['host']}{path}?partner_id={c['partner_id']}&timestamp={ts}&sign={sig}"
    body = _token_get_body(code, shop_id=shop_id, main_account_id=main_account_id)
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    with urlopen_retry(req, timeout=30, context=SSL_CTX) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(data.get("message") or data)
    if not data.get("access_token"):
        raise RuntimeError(f"换 token 失败: {data}")
    return data


def exchange_code(code: str, shop_id: int) -> dict:
    """用授权 code + shop_id 换取 access_token。"""
    data = _call_token_get(code, shop_id=shop_id)
    entry = {
        "shop_id": int(shop_id),
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expire_at": int(time.time()) + int(data.get("expire_in") or 14400),
        "region": data.get("region") or "",
        "updated_at": int(time.time()),
    }
    def apply(store: dict) -> None:
        store.setdefault("shops", {})[str(shop_id)] = entry

    update_tokens(apply)
    return entry


def exchange_code_main(code: str, main_account_id: int) -> dict:
    """主账号授权（回调带 main_account_id）→ token + shop_id_list。"""
    data = _call_token_get(code, main_account_id=main_account_id)
    now = int(time.time())
    expire_at = now + int(data.get("expire_in") or 14400)
    # 主账号 token 通常共用；为每个 shop_id 建条目便于后续 API
    shop_ids = data.get("shop_id_list") or []
    if not shop_ids and data.get("shop_id"):
        shop_ids = [data["shop_id"]]

    entry_base = {
        "main_account_id": int(main_account_id),
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expire_at": expire_at,
        "updated_at": now,
    }
    merchant_ids = data.get("merchant_id_list") or []

    def apply(store: dict) -> None:
        store["main_account_id"] = int(main_account_id)
        store["merchant_id_list"] = merchant_ids
        shops = store.setdefault("shops", {})
        merchants = store.setdefault("merchants", {})
        for mid in merchant_ids:
            merchants[str(mid)] = {
                **entry_base,
                "merchant_id": int(mid),
            }
        if not shop_ids:
            key = f"main_{main_account_id}"
            shops[key] = {**entry_base, "shop_id": None, "region": ""}
        else:
            for sid in shop_ids:
                shops[str(sid)] = {**entry_base, "shop_id": int(sid), "region": ""}

    update_tokens(apply)
    return {"main_account_id": main_account_id, "shop_id_list": shop_ids, **entry_base}


def refresh_merchant_token(merchant_id: int) -> dict:
    """Refresh a merchant token once, with durable unknown-outcome blocking."""
    token_file = token_path()
    with _token_store_lock(token_file):
        store = _load_tokens_from(token_file)
        merchant = (store.get("merchants") or {}).get(str(merchant_id)) or {}
        previous_refresh = merchant.get("refresh_token")
        if not isinstance(previous_refresh, str) or not previous_refresh.strip():
            raise RuntimeError(
                f"merchant {merchant_id} has no explicitly bound refresh_token; reauthorize"
            )

        previous_digest = _credential_digest(previous_refresh)
        state = _load_refresh_state_unlocked(token_file)
        prior = state.get("merchants", {}).get(str(merchant_id)) or {}
        if prior.get("status") in {"IN_FLIGHT", "UNKNOWN"}:
            current = (store.get("merchants") or {}).get(str(merchant_id)) or {}
            current_refresh = current.get("refresh_token")
            if isinstance(current_refresh, str) and (
                _credential_digest(current_refresh) != prior.get("old_refresh_digest")
            ):
                _set_entity_refresh_state_unlocked(
                    token_file,
                    state,
                    "merchants",
                    merchant_id,
                    "COMMITTED_RECOVERED",
                    old_refresh_digest=str(prior.get("old_refresh_digest") or "unknown"),
                    new_refresh_digest=_credential_digest(current_refresh),
                )
                return current
            raise RefreshReconciliationRequired(
                f"merchant {merchant_id} refresh outcome requires reconciliation; automatic retry blocked"
            )
        _raise_if_digest_has_uncertain_alias(
            state,
            previous_digest,
            exclude=("merchants", str(merchant_id)),
        )

        _set_entity_refresh_state_unlocked(
            token_file,
            state,
            "merchants",
            merchant_id,
            "IN_FLIGHT",
            old_refresh_digest=previous_digest,
        )
        c = shopee_config()
        endpoint = "/api/v2/auth/access_token/get"
        ts, signature = sign_partner(endpoint, c["partner_id"], c["partner_key"])
        url = f"{c['host']}{endpoint}?partner_id={c['partner_id']}&timestamp={ts}&sign={signature}"
        request = urllib.request.Request(
            url,
            data=json.dumps(
                {
                    "merchant_id": int(merchant_id),
                    "refresh_token": previous_refresh,
                    "partner_id": c["partner_id"],
                }
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen_retry(
                request,
                timeout=30,
                context=SSL_CTX,
                attempts=1,
                allow_curl_fallback=False,
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if _http_failure_is_definitive(exc.code):
                _set_entity_refresh_state_unlocked(
                    token_file,
                    state,
                    "merchants",
                    merchant_id,
                    "FAILED",
                    old_refresh_digest=previous_digest,
                    error_type=f"HTTP_{exc.code}",
                )
                raise RuntimeError(
                    f"merchant {merchant_id} token refresh rejected (HTTP {exc.code})"
                ) from None
            _mark_entity_unknown_unlocked(
                token_file, state, "merchants", merchant_id, previous_digest, exc
            )
            raise RefreshReconciliationRequired(
                f"merchant {merchant_id} refresh HTTP outcome unknown; automatic retry blocked"
            ) from None
        except Exception as exc:
            _mark_entity_unknown_unlocked(
                token_file, state, "merchants", merchant_id, previous_digest, exc
            )
            raise RefreshReconciliationRequired(
                f"merchant {merchant_id} refresh transport outcome unknown; automatic retry blocked"
            ) from None

        if not isinstance(data, dict):
            _mark_entity_unknown_unlocked(
                token_file,
                state,
                "merchants",
                merchant_id,
                previous_digest,
                ValueError("invalid response shape"),
            )
            raise RefreshReconciliationRequired(
                f"merchant {merchant_id} refresh response unknown; automatic retry blocked"
            )
        if data.get("error"):
            _mark_entity_unknown_unlocked(
                token_file,
                state,
                "merchants",
                merchant_id,
                previous_digest,
                ValueError("unclassified provider error"),
            )
            raise RefreshReconciliationRequired(
                f"merchant {merchant_id} refresh provider outcome unknown; automatic retry blocked"
            )
        try:
            access_token, refresh_value, expire_in = _validated_refresh_payload(
                data, previous_refresh
            )
        except ValueError as exc:
            _mark_entity_unknown_unlocked(
                token_file, state, "merchants", merchant_id, previous_digest, exc
            )
            raise RefreshReconciliationRequired(
                f"merchant {merchant_id} refresh response incomplete; automatic retry blocked"
            ) from None

        entry = {
            **merchant,
            "merchant_id": int(merchant_id),
            "access_token": access_token,
            "refresh_token": refresh_value,
            "expire_at": int(time.time()) + expire_in,
            "updated_at": int(time.time()),
        }
        store.setdefault("merchants", {})[str(merchant_id)] = entry
        try:
            _save_tokens_unlocked(token_file, store)
            persisted = (_load_tokens_from(token_file).get("merchants") or {}).get(
                str(merchant_id), {}
            )
            persisted_digest = _credential_digest(
                str(persisted.get("refresh_token") or "")
            )
            if persisted_digest != _credential_digest(refresh_value):
                raise OSError("persisted merchant credential verification failed")
        except Exception as exc:
            _mark_entity_unknown_unlocked(
                token_file, state, "merchants", merchant_id, previous_digest, exc
            )
            raise RefreshReconciliationRequired(
                f"merchant {merchant_id} rotated credential was not durably verified; automatic retry blocked"
            ) from None
        try:
            _set_entity_refresh_state_unlocked(
                token_file,
                state,
                "merchants",
                merchant_id,
                "COMMITTED",
                old_refresh_digest=previous_digest,
                new_refresh_digest=_credential_digest(refresh_value),
            )
        except OSError:
            pass
        return entry


def ensure_merchant_token(merchant_id: int, *, shop_id: int | None = None) -> str:
    """全球商品 API 用 merchant token；过期则按 merchant_id 刷新。"""
    store = load_tokens()
    merchants = store.get("merchants") or {}
    entry = merchants.get(str(merchant_id)) or {}
    token = entry.get("access_token") or ""
    if token and int(entry.get("expire_at") or 0) >= int(time.time()) + 120:
        return token
    if entry.get("refresh_token"):
        return refresh_merchant_token(merchant_id)["access_token"]
    if shop_id:
        return ensure_shop_token(shop_id)
    raise RuntimeError(
        f"merchant {merchant_id} 无 token，请重新 Shopee 授权（勾选 Auth Merchant）"
    )


def ensure_shop_token(shop_id: int) -> str:
    store = load_tokens()
    entry = store.get("shops", {}).get(str(shop_id), {})
    token = entry.get("access_token") or ""
    if not token:
        raise RuntimeError(f"shop_id={shop_id} 无 token，请 shopee auth")
    if int(entry.get("expire_at") or 0) < int(time.time()) + 120:
        entry = refresh_token(shop_id)
        token = entry["access_token"]
    return token


def refresh_token(shop_id: int) -> dict:
    token_file = token_path()
    with _token_store_lock(token_file):
        c = shopee_config()
        store = _load_tokens_from(token_file)
        shop = store.get("shops", {}).get(str(shop_id))
        if not shop or not isinstance(shop.get("refresh_token"), str) or not shop["refresh_token"].strip():
            raise RuntimeError(f"shop {shop_id} 无 refresh_token，请重新授权")
        previous_refresh = shop["refresh_token"]
        previous_digest = _credential_digest(previous_refresh)
        state = _load_refresh_state_unlocked(token_file)
        prior = state.get("shops", {}).get(str(shop_id)) or {}
        if prior.get("status") in {"IN_FLIGHT", "UNKNOWN"}:
            if prior.get("old_refresh_digest") != previous_digest:
                _set_refresh_state_unlocked(
                    token_file,
                    state,
                    shop_id,
                    "COMMITTED_RECOVERED",
                    old_refresh_digest=str(prior.get("old_refresh_digest") or "unknown"),
                    new_refresh_digest=previous_digest,
                )
                return shop
            raise RefreshReconciliationRequired(
                f"shop {shop_id} refresh outcome requires reconciliation; automatic retry blocked"
            )
        _raise_if_digest_has_uncertain_alias(
            state,
            previous_digest,
            exclude=("shops", str(shop_id)),
        )

        _set_refresh_state_unlocked(
            token_file,
            state,
            shop_id,
            "IN_FLIGHT",
            old_refresh_digest=previous_digest,
        )
        endpoint = "/api/v2/auth/access_token/get"
        ts, sig = sign_partner(endpoint, c["partner_id"], c["partner_key"])
        url = f"{c['host']}{endpoint}?partner_id={c['partner_id']}&timestamp={ts}&sign={sig}"
        body = json.dumps(
            {
                "shop_id": int(shop_id),
                "refresh_token": previous_refresh,
                "partner_id": c["partner_id"],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, method="POST", headers={"Content-Type": "application/json"}
        )
        try:
            with urlopen_retry(
                request,
                timeout=30,
                context=SSL_CTX,
                attempts=1,
                allow_curl_fallback=False,
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if _http_failure_is_definitive(exc.code):
                _set_refresh_state_unlocked(
                    token_file,
                    state,
                    shop_id,
                    "FAILED",
                    old_refresh_digest=previous_digest,
                    error_type=f"HTTP_{exc.code}",
                )
                raise RuntimeError(
                    f"shop {shop_id} token refresh rejected (HTTP {exc.code})"
                ) from None
            _mark_unknown_unlocked(token_file, state, shop_id, previous_digest, exc)
            raise RefreshReconciliationRequired(
                f"shop {shop_id} refresh HTTP outcome unknown; automatic retry blocked"
            ) from None
        except Exception as exc:
            _mark_unknown_unlocked(token_file, state, shop_id, previous_digest, exc)
            raise RefreshReconciliationRequired(
                f"shop {shop_id} refresh transport outcome unknown; automatic retry blocked"
            ) from None

        if not isinstance(data, dict):
            _mark_unknown_unlocked(
                token_file,
                state,
                shop_id,
                previous_digest,
                ValueError("invalid response shape"),
            )
            raise RefreshReconciliationRequired(
                f"shop {shop_id} refresh response unknown; automatic retry blocked"
            )
        if data.get("error"):
            _mark_unknown_unlocked(
                token_file,
                state,
                shop_id,
                previous_digest,
                ValueError("unclassified provider error"),
            )
            raise RefreshReconciliationRequired(
                f"shop {shop_id} refresh provider outcome unknown; automatic retry blocked"
            )
        try:
            access_token, refresh_token_value, expire_in = _validated_refresh_payload(
                data, previous_refresh
            )
        except ValueError as exc:
            _mark_unknown_unlocked(token_file, state, shop_id, previous_digest, exc)
            raise RefreshReconciliationRequired(
                f"shop {shop_id} refresh response incomplete; automatic retry blocked"
            ) from None

        updated_shop = {
            **shop,
            "access_token": access_token,
            "refresh_token": refresh_token_value,
            "expire_at": int(time.time()) + expire_in,
            "updated_at": int(time.time()),
        }
        store.setdefault("shops", {})[str(shop_id)] = updated_shop
        try:
            _save_tokens_unlocked(token_file, store)
            verified = _load_tokens_from(token_file).get("shops", {}).get(str(shop_id)) or {}
            if _credential_digest(str(verified.get("refresh_token") or "")) != _credential_digest(refresh_token_value):
                raise OSError("persisted credential verification failed")
        except Exception as exc:
            _mark_unknown_unlocked(token_file, state, shop_id, previous_digest, exc)
            raise RefreshReconciliationRequired(
                f"shop {shop_id} rotated credential was not durably verified; automatic retry blocked"
            ) from None

        try:
            _set_refresh_state_unlocked(
                token_file,
                state,
                shop_id,
                "COMMITTED",
                old_refresh_digest=previous_digest,
                new_refresh_digest=_credential_digest(refresh_token_value),
            )
        except OSError:
            # The canonical credential has already been atomically replaced and verified.
            # Leave IN_FLIGHT for digest-based recovery on the next call; never replay here.
            pass
        return updated_shop


def _validated_refresh_payload(data: dict, previous_refresh: str) -> tuple[str, str, int]:
    access = data.get("access_token")
    refresh = data.get("refresh_token", previous_refresh)
    expire_in = data.get("expire_in", 14400)
    if not isinstance(access, str) or not access.strip():
        raise ValueError("missing access token")
    if not isinstance(refresh, str) or not refresh.strip():
        raise ValueError("missing refresh token")
    if isinstance(expire_in, bool):
        raise ValueError("invalid expiry")
    try:
        expire_seconds = int(expire_in)
    except (TypeError, ValueError):
        raise ValueError("invalid expiry") from None
    if expire_seconds <= 0:
        raise ValueError("invalid expiry")
    return access, refresh, expire_seconds


def _mark_unknown_unlocked(
    token_file: Path,
    state: dict,
    shop_id: int,
    previous_digest: str,
    error: BaseException,
) -> None:
    try:
        _set_refresh_state_unlocked(
            token_file,
            state,
            shop_id,
            "UNKNOWN",
            old_refresh_digest=previous_digest,
            error_type=type(error).__name__,
        )
    except OSError:
        # The pre-request IN_FLIGHT marker remains the fail-closed recovery signal.
        pass


def _mark_entity_unknown_unlocked(
    token_file: Path,
    state: dict,
    collection: str,
    entity_id: int,
    previous_digest: str,
    error: BaseException,
) -> None:
    try:
        _set_entity_refresh_state_unlocked(
            token_file,
            state,
            collection,
            entity_id,
            "UNKNOWN",
            old_refresh_digest=previous_digest,
            error_type=type(error).__name__,
        )
    except OSError:
        pass


def _http_failure_is_definitive(status: int) -> bool:
    """Only a non-transient client rejection is safe to retry manually."""
    return 400 <= int(status) < 500 and int(status) not in {408, 409, 425, 429}


def _raise_if_digest_has_uncertain_alias(
    state: dict,
    refresh_digest: str,
    *,
    exclude: tuple[str, str],
) -> None:
    """Block reuse of one uncertain credential through another entity alias."""
    for collection in ("shops", "merchants"):
        for entity_id, entry in (state.get(collection) or {}).items():
            if (collection, str(entity_id)) == exclude:
                continue
            if entry.get("status") not in {"IN_FLIGHT", "UNKNOWN"}:
                continue
            if entry.get("old_refresh_digest") == refresh_digest:
                raise RefreshReconciliationRequired(
                    "Shopee refresh credential has an uncertain alias outcome; "
                    "automatic retry blocked"
                )


def status_text() -> str:
    from modules.shopee.shops import status_lines

    if not ready():
        return "Shopee：未配置 partner_id / partner_key"
    shops = load_tokens().get("shops") or {}
    if not shops:
        lines = status_lines()[:1]
        lines.append("已授权店铺: 0")
        lines.append("  运行 python3 main.py shopee auth-url 获取授权链接")
        return "\n".join(lines)
    if not load_tokens().get("sync_shop_ids"):
        try:
            from modules.shopee.shops import refresh_shop_regions
            refresh_shop_regions(quiet=True)
        except Exception:
            pass
    return "\n".join(status_lines())
