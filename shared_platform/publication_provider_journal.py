"""Request-bound private provider responses, retained before JSON decoding.

Only a real publication executor installs this context. It neither approves a
write nor replaces a write-budget ledger. An existing uncertain intent can
never open another mutation connection. A retained response may be decoded
again locally, preserving its original task/item identities without a resend.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Mapping
import urllib.error
import urllib.parse


_ACTIVE = ContextVar("publication_provider_response_journal", default=None)
_SHOPEE_MUTATIONS = frozenset({
    "/api/v2/global_product/add_global_item",
    "/api/v2/global_product/update_global_item",
    "/api/v2/global_product/init_tier_variation",
    "/api/v2/global_product/update_global_tier_variation",
    "/api/v2/global_product/update_global_model",
    "/api/v2/global_product/create_publish_task",
    "/api/v2/product/add_item", "/api/v2/product/update_item",
    "/api/v2/product/update_model", "/api/v2/product/unlist_item",
    "/api/v2/product/update_stock", "/api/v2/discount/add_discount_item",
    "/api/v2/media_space/upload_image",
})
_OZON_MUTATIONS = frozenset({
    "/v3/product/import", "/v2/products/stocks",
    "/v1/product/pictures/import", "/v1/product/import/pictures",
})


class ProviderOutcomeUnknown(RuntimeError):
    """The original mutation requires read-only reconciliation, never resend."""


def response_is_retained(response):
    return getattr(response, "_journal", None) is not None or getattr(response, "_publication_response_retained", False)


def _retained_http_error(url, status, headers, raw):
    error = urllib.error.HTTPError(url, status, "provider rejection retained privately", headers, io.BytesIO(raw))
    error._publication_response_retained = True
    return error


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _identities(payload):
    found = {}
    def walk(value, prefix="", depth=0):
        if depth > 8:
            return
        if isinstance(value, Mapping):
            for name, item in value.items():
                if name in {"publish_task_id", "task_id", "item_id", "global_item_id", "product_id", "request_id"}:
                    text = str(item)
                    if text and len(text) <= 160 and all(c.isalnum() or c in "_:-." for c in text):
                        found[prefix + str(name)] = text
                if isinstance(item, (Mapping, list)):
                    walk(item, prefix + str(name) + ".", depth + 1)
        elif isinstance(value, list):
            for index, item in enumerate(value[:1000]):
                walk(item, prefix + str(index) + ".", depth + 1)
    walk(payload)
    return found


def _checked_runtime_path(root: Path):
    """Check lexical ancestors before resolve; junctions must not hide identity."""
    root = Path(root).expanduser()
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("private journal needs a normalized absolute data path")
    for part in (root, *root.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("private provider journal cannot traverse a reparse point")
        if (part / ".git").exists() or ((part / "shared_platform").is_dir() and (part / "modules").is_dir()):
            raise ValueError("private provider journal must be outside the source checkout")
    return root.resolve()


def _user_identity():
    if os.name != "nt":
        return str(os.getuid())
    from shared_platform.local_operator_session import current_windows_owner_sid
    return current_windows_owner_sid()


def ensure_private_runtime_directory(root: Path, scope_digest: str):
    """Only initialize our new scope directory; never change an existing ACL.

    Every call rechecks lexical path identity, effective owner, actual ACL and
    scope owner marker. Existing directories never have their ACL rewritten.
    """
    root = _checked_runtime_path(root)
    marker = root / ".publication-provider-owner.json"
    expected = {"schema_version": "publication-provider-owner/v1",
                "scope_digest": scope_digest, "user_identity": _user_identity()}
    if root.exists():
        _checked_runtime_path(marker)
        if os.name == "nt":
            from shared_platform.local_operator_session import verify_owner_only
            verify_owner_only(root, expected["user_identity"])
        elif root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
            raise ValueError("existing private journal permissions changed")
        if not marker.is_file() or marker.read_bytes() != _canonical(expected):
            raise ValueError("existing private journal directory has no matching owner")
        return root
    root.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        from shared_platform.local_operator_session import create_owner_only_directory
        create_owner_only_directory(root)
    else:
        root.mkdir(mode=0o700)
    with marker.open("xb") as stream:
        stream.write(_canonical(expected))
        stream.flush()
        os.fsync(stream.fileno())
    return root


def _scope(request):
    snapshot = getattr(request, "snapshot", None)
    root = getattr(request, "checkpoint_root", None)
    if not isinstance(snapshot, Mapping) or not root:
        raise ValueError("provider response journal requires an explicit request data root")
    root = Path(root).expanduser()
    if not root.is_absolute():
        raise ValueError("provider response journal data root must be absolute")
    identity = {
        "run_id": getattr(request, "run_id", None),
        "report_id": getattr(request, "report_id", None),
        "platform": getattr(request, "platform", None),
        "target_labels": list(getattr(request, "target_labels", ())),
        "snapshot_digest": snapshot.get("snapshot_digest"),
        "offer_id": snapshot.get("offer_id"),
        "plan_id": snapshot.get("plan_id"),
    }
    if not all(identity.get(k) for k in ("run_id", "report_id", "platform", "target_labels", "snapshot_digest", "offer_id", "plan_id")):
        raise ValueError("provider response journal request identity is incomplete")
    return _checked_runtime_path(root), identity


def retained_provider_executor(executor):
    @wraps(executor)
    def execute(request):
        # Delay directory/DB creation until a real SDK mutation is attempted.
        # Pure status/planning and injected offline reads do not create state.
        token = _ACTIVE.set(request)
        try:
            return executor(request)
        finally:
            _ACTIVE.reset(token)
    return execute


class ProviderResponseJournal:
    def __init__(self, root, identity):
        # A new process/run must not hide an original UNKNOWN. Original run and
        # report references live in each row, outside the stable resource key.
        self.scope = {k: v for k, v in identity.items() if k not in {"run_id", "report_id"}}
        self.scope_digest = _digest(self.scope)
        self.root = Path(root) / "private-provider-responses" / self.scope_digest[:24]
        self.path = self.root / "responses.private.db"
        self.identity = identity

    def initialize(self):
        ensure_private_runtime_directory(self.root, self.scope_digest)
        _checked_runtime_path(self.path)
        with sqlite3.connect(self.path) as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS provider_response_intents (
              key TEXT PRIMARY KEY, identity_json TEXT NOT NULL,
              request_identity_json TEXT NOT NULL, phase TEXT NOT NULL,
              http_status INTEGER, response_body BLOB, response_sha256 TEXT,
              response_headers_json TEXT, parsed_identities_json TEXT,
              started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              error_type TEXT);
            """)

    def claim(self, request_identity):
        key = _digest(request_identity)
        with sqlite3.connect(self.path, timeout=10) as c:
            c.row_factory = sqlite3.Row
            c.execute("BEGIN IMMEDIATE")
            old = c.execute("SELECT * FROM provider_response_intents WHERE key=?", (key,)).fetchone()
            if old:
                original = json.loads(old["identity_json"])
                original_scope = {k: v for k, v in original.items() if k not in {"run_id", "report_id"}}
                if original_scope != self.scope:
                    raise ValueError("provider response journal scope conflicts")
                return key, dict(old)
            if c.execute("SELECT 1 FROM provider_response_intents WHERE response_body IS NULL LIMIT 1").fetchone():
                raise ProviderOutcomeUnknown("original provider intent requires read-only reconciliation before changed payload")
            at = _now()
            c.execute("""INSERT INTO provider_response_intents
              (key,identity_json,request_identity_json,phase,started_at,updated_at)
              VALUES (?,?,?,'INTENT',?,?)""",
              (key, _canonical(self.identity).decode(), _canonical(request_identity).decode(), at, at))
        return key, None

    def retain(self, key, status, raw, headers):
        # Deliberately no JSON decoding before this transaction commits.
        retained_headers = {k: str(headers.get(k)) for k in ("Content-Encoding", "Content-Type")
                            if headers is not None and headers.get(k) is not None}
        with sqlite3.connect(self.path, timeout=10) as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("""UPDATE provider_response_intents SET phase='RESPONSE_RETAINED',
                http_status=?,response_body=?,response_sha256=?,response_headers_json=?,
                updated_at=? WHERE key=? AND response_body IS NULL""",
                (status, raw, hashlib.sha256(raw).hexdigest(), _canonical(retained_headers).decode(), _now(), key))

    def unknown(self, key, error):
        with sqlite3.connect(self.path, timeout=10) as c:
            c.execute("""UPDATE provider_response_intents SET phase='UNKNOWN',
                updated_at=?,error_type=? WHERE key=? AND response_body IS NULL""",
                (_now(), type(error).__name__, key))

    def identities(self, key, payload):
        with sqlite3.connect(self.path, timeout=10) as c:
            c.execute("""UPDATE provider_response_intents SET parsed_identities_json=?,
                updated_at=? WHERE key=? AND response_body IS NOT NULL""",
                (_canonical(_identities(payload)).decode(), _now(), key))


class _Response:
    def __init__(self, raw, status, headers, url, *, journal=None, key=None):
        self._stream = io.BytesIO(raw)
        self.status = status
        self.code = status
        self.headers = headers or {}
        self.url = url
        self._journal = journal
        self._key = key

    def read(self, *args):
        return self._stream.read(*args)

    def geturl(self):
        return self.url

    def info(self):
        return self.headers

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self._stream.close()


def _is_mutation(provider, path):
    if provider == "shopee":
        return path in _SHOPEE_MUTATIONS
    if provider == "ozon":
        return path in _OZON_MUTATIONS
    return provider == "miaoshou" and any(word in path.lower() for word in
        ("/create", "/add", "/save", "/edit", "/publish", "/claim", "/copy"))


@contextmanager
def retained_urlopen(provider, request, opener, **kwargs):
    """Wrap the actual SDK wire edge; parsed fixture success is insufficient."""
    current = _ACTIVE.get()
    parsed = urllib.parse.urlsplit(request.full_url)
    if current is None or not _is_mutation(provider, parsed.path):
        with opener(request, **kwargs) as response:
            yield response
        return
    root, identity = _scope(current)
    journal = ProviderResponseJournal(root, identity)
    journal.initialize()  # Fails before any network if storage/privacy is invalid.
    query = urllib.parse.parse_qs(parsed.query)
    accounts = {k: values[0] for k, values in query.items() if k in {"shop_id", "merchant_id", "partner_id"}}
    if provider == "ozon":
        accounts["client_id"] = request.get_header("Client-id")
    if provider == "miaoshou":
        accounts["app_id"] = request.get_header("X-app-key")
    request_identity = {"provider": provider, "method": request.get_method(),
        "path": parsed.path, "account": accounts,
        "request_body_sha256": hashlib.sha256(request.data or b"").hexdigest()}
    if provider == "shopee" and parsed.path == "/api/v2/global_product/create_publish_task":
        body = json.loads((request.data or b"{}").decode("utf-8"))
        resource = {k: body.get(k) for k in ("shop_id", "global_item_id", "shop_region")}
        if (any(type(resource[k]) is not int or resource[k] <= 0 for k in ("shop_id", "global_item_id"))
                or type(resource["shop_region"]) is not str or len(resource["shop_region"]) != 2):
            raise ValueError("provider create-task resource identity is incomplete")
        request_identity["resource"] = resource
    key, prior = journal.claim(request_identity)
    if prior:
        raw = prior.get("response_body")
        if raw is None:
            raise ProviderOutcomeUnknown("original provider intent requires read-only reconciliation")
        raw = bytes(raw)
        if hashlib.sha256(raw).hexdigest() != prior["response_sha256"]:
            raise ProviderOutcomeUnknown("retained provider response integrity mismatch")
        status = int(prior["http_status"])
        headers = json.loads(prior["response_headers_json"] or "{}")
        if status >= 400:
            raise _retained_http_error(request.full_url, status, headers, raw)
        yield _Response(raw, status, headers, request.full_url, journal=journal, key=key)
        return
    try:
        if provider in {"shopee", "ozon"}:
            kwargs.update(attempts=1, allow_curl_fallback=False)
        with opener(request, **kwargs) as response:
            raw = response.read()
            status = int(response.status)
            headers = response.headers
            url = response.geturl()
            journal.retain(key, status, raw, headers)
            yield _Response(raw, status, headers, url, journal=journal, key=key)
    except urllib.error.HTTPError as error:
        raw = error.read()
        journal.retain(key, error.code, raw, error.headers)
        raise _retained_http_error(request.full_url, error.code, error.headers, raw) from None
    except BaseException as error:
        # This includes response-save or caller parse/consumer exceptions.
        # Never turn a possibly submitted original into zero-write retryable.
        journal.unknown(key, error)
        raise


def retain_decoded_provider_identity(response, parsed):
    journal = getattr(response, "_journal", None)
    if journal is not None:
        journal.identities(response._key, parsed)


def provider_recovery_state(checkpoint_root, identity):
    """Read-only local evidence; never implies canonical marketplace success."""
    journal = ProviderResponseJournal(_checked_runtime_path(Path(checkpoint_root)), identity)
    if not journal.path.is_file():
        return {"schema_version": "publication-provider-recovery/v1", "intents": []}
    _checked_runtime_path(journal.path)
    if os.name == "nt":
        from shared_platform.local_operator_session import verify_owner_only
        verify_owner_only(journal.root, _user_identity())
    with sqlite3.connect(journal.path.as_uri() + "?mode=ro", uri=True) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT * FROM provider_response_intents ORDER BY started_at").fetchall()
    results = []
    for row in rows:
        identities = json.loads(row["parsed_identities_json"] or "{}")
        decode_status = "NOT_AVAILABLE"
        raw = row["response_body"]
        if raw is not None:
            raw = bytes(raw)
            decode_status = "INTEGRITY_MISMATCH"
            if hashlib.sha256(raw).hexdigest() == row["response_sha256"]:
                try:
                    headers = json.loads(row["response_headers_json"] or "{}")
                    if headers.get("Content-Encoding", "").lower() == "gzip" or raw[:2] == b"\x1f\x8b":
                        raw = gzip.decompress(raw)
                    identities = _identities(json.loads(raw.decode("utf-8")))
                    decode_status = "DECODED_LOCALLY"
                except (ValueError, OSError, UnicodeError):
                    decode_status = "UNPARSEABLE_ORIGINAL"
        results.append({k: row[k] for k in ("key", "phase", "http_status", "response_sha256", "started_at", "updated_at", "error_type")})
        results[-1].update({"next_action": "RECONCILE_ORIGINAL_READONLY",
            "blocked_by": [] if identities else [decode_status], "next_check_at": None,
            "original_identities": identities, "decode_status": decode_status,
            "original_execution": json.loads(row["identity_json"]),
            "original_request": json.loads(row["request_identity_json"])})
    return {"schema_version": "publication-provider-recovery/v1", "intents": results}
