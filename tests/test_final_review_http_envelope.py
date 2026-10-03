"""Synthetic contract for the unconnected final-review decision HTTP gate."""

import json

import pytest

from shared_platform.final_review_http_envelope import (
    FinalReviewEnvelopeError,
    inspect_decision_headers,
    parse_decision_body,
)


TARGET = "/api/product-workspace/final-review/decision"
BODY = {
    "offer_id": "3956742887",
    "task_id": "task-17",
    "action_id": "action-3",
    "plan_id": "common:3956742887:abc",
    "contract_digest": "sha256:" + "a" * 64,
    "user_approved": True,
}


class SyntheticSession:
    """Only a test capability; no identity is derived from request fields."""

    def authenticate(self, *, headers, origin, csrf_token):
        cookies = [value for name, value in headers if name.lower() == "cookie"]
        if cookies == ["test_session=server-held"] and csrf_token == "test-csrf":
            return "synthetic-reviewer"
        return None


def request(*, target=TARGET, body=None, headers=None, capability=None,
            client="127.0.0.1", bound="127.0.0.1", port=8765):
    raw = json.dumps(BODY if body is None else body).encode("utf-8")
    fields = [
        ("Host", f"127.0.0.1:{port}"),
        ("Origin", f"http://127.0.0.1:{port}"),
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(raw))),
        ("X-Orbit-CSRF-Token", "test-csrf"),
        ("Cookie", "test_session=server-held"),
    ] if headers is None else headers
    framing = inspect_decision_headers(
        target=target, headers=fields, client_address=client,
        server_address=(bound, port),
    )
    return parse_decision_body(
        framing, raw, session_capability=SyntheticSession() if capability is None else capability,
    )


def reject(code, **kwargs):
    with pytest.raises(FinalReviewEnvelopeError) as caught:
        request(**kwargs)
    assert caught.value.code == code


def test_exact_loopback_request_normalizes_identity_and_uses_session_subject():
    result = request()
    assert result.subject == "synthetic-reviewer"
    assert result.offer_id == BODY["offer_id"]
    assert result.plan_id == BODY["plan_id"]
    assert result.user_approved is True
    assert result.contract_digest == BODY["contract_digest"]


def test_ipv6_loopback_with_matching_host_and_origin():
    raw = json.dumps(BODY).encode()
    fields = [("Host", "[::1]:8765"), ("Origin", "http://[::1]:8765"),
              ("Content-Type", "application/json"), ("Content-Length", str(len(raw))),
              ("X-Orbit-CSRF-Token", "test-csrf"), ("Cookie", "test_session=server-held")]
    result = request(headers=fields, client="::1", bound="::1")
    assert result.subject == "synthetic-reviewer"


@pytest.mark.parametrize("client,bound", [
    ("192.168.1.8", "127.0.0.1"),
    ("127.0.0.1", "0.0.0.0"),
    ("::ffff:127.0.0.1", "127.0.0.1"),
])
def test_non_loopback_or_nonlocal_binding_is_rejected(client, bound):
    reject("LOOPBACK_REQUIRED", client=client, bound=bound)


@pytest.mark.parametrize("change", [
    lambda h: [item for item in h if item[0] != "Host"],
    lambda h: h + [("hOSt", h[0][1])],
    lambda h: [("Host", "localhost:8765")] + h[1:],
    lambda h: [("Host", "127.0.0.1:8765,evil.test")] + h[1:],
])
def test_host_must_be_one_exact_bound_loopback_authority(change):
    raw = json.dumps(BODY).encode()
    fields = [("Host", "127.0.0.1:8765"), ("Origin", "http://127.0.0.1:8765"),
              ("Content-Type", "application/json"), ("Content-Length", str(len(raw))),
              ("X-Orbit-CSRF-Token", "test-csrf"), ("Cookie", "test_session=server-held")]
    reject("HOST_INVALID", headers=change(fields))


@pytest.mark.parametrize("origin", [None, "http://localhost:8765", "http://evil.test",
                                       "http://127.0.0.1:8765/", "null"])
def test_origin_must_be_present_and_exactly_match_host(origin):
    raw = json.dumps(BODY).encode()
    fields = [("Host", "127.0.0.1:8765"), ("Content-Type", "application/json"),
              ("Content-Length", str(len(raw))), ("X-Orbit-CSRF-Token", "test-csrf"),
              ("Cookie", "test_session=server-held")]
    if origin is not None:
        fields.append(("Origin", origin))
    reject("ORIGIN_INVALID", headers=fields)


def test_duplicate_origin_is_rejected():
    raw = json.dumps(BODY).encode()
    fields = [("Host", "127.0.0.1:8765"), ("Origin", "http://127.0.0.1:8765"),
              ("origin", "http://127.0.0.1:8765"), ("Content-Type", "application/json"),
              ("Content-Length", str(len(raw))), ("X-Orbit-CSRF-Token", "test-csrf")]
    reject("ORIGIN_INVALID", headers=fields)


@pytest.mark.parametrize("target", [TARGET + "?mode=legacy", TARGET + "#fragment",
                                     "http://127.0.0.1:8765" + TARGET, TARGET + "/"])
def test_target_has_no_query_fragment_or_alias(target):
    reject("TARGET_INVALID", target=target)


@pytest.mark.parametrize("name,value,code", [
    ("Transfer-Encoding", "chunked", "FRAMING_INVALID"),
    ("Content-Length", "4", "FRAMING_INVALID"),
    ("Content-Type", "text/plain", "MEDIA_TYPE_INVALID"),
    ("Content-Type", "application/json; charset=utf-8", "MEDIA_TYPE_INVALID"),
    ("X-Orbit-CSRF-Token", "wrong", "AUTH_REQUIRED"),
])
def test_framing_type_and_csrf_fail_closed(name, value, code):
    raw = json.dumps(BODY).encode()
    fields = [("Host", "127.0.0.1:8765"), ("Origin", "http://127.0.0.1:8765"),
              ("Content-Type", "application/json"), ("Content-Length", str(len(raw))),
              ("X-Orbit-CSRF-Token", "test-csrf"), ("Cookie", "test_session=server-held")]
    if name == "Transfer-Encoding":
        fields.append((name, value))
    elif name == "Content-Length":
        fields.append((name, value))
    else:
        fields = [(key, value if key == name else original) for key, original in fields]
    reject(code, headers=fields)


def test_missing_capability_returns_auth_unavailable():
    raw = json.dumps(BODY).encode()
    fields = [("Host", "127.0.0.1:8765"), ("Origin", "http://127.0.0.1:8765"),
              ("Content-Type", "application/json"), ("Content-Length", str(len(raw))),
              ("X-Orbit-CSRF-Token", "test-csrf")]
    framing = inspect_decision_headers(
        target=TARGET, headers=fields, client_address="127.0.0.1",
        server_address=("127.0.0.1", 8765),
    )
    with pytest.raises(FinalReviewEnvelopeError) as caught:
        parse_decision_body(framing, raw)
    assert caught.value.code == "AUTH_UNAVAILABLE"


def test_broken_session_capability_fails_closed():
    class BrokenSession:
        def authenticate(self, **_kwargs):
            raise RuntimeError("session store unavailable")

    raw = json.dumps(BODY).encode()
    fields = [("Host", "127.0.0.1:8765"), ("Origin", "http://127.0.0.1:8765"),
              ("Content-Type", "application/json"), ("Content-Length", str(len(raw))),
              ("X-Orbit-CSRF-Token", "test-csrf")]
    framing = inspect_decision_headers(target=TARGET, headers=fields,
                                       client_address="127.0.0.1",
                                       server_address=("127.0.0.1", 8765))
    with pytest.raises(FinalReviewEnvelopeError) as caught:
        parse_decision_body(framing, raw, session_capability=BrokenSession())
    assert caught.value.code == "AUTH_UNAVAILABLE"


def test_body_cannot_claim_identity_or_mode():
    for extra in ({"approved_by": "Kyle"}, {"mode": "legacy"}, {"targets": []}):
        reject("BODY_INVALID", body={**BODY, **extra})


@pytest.mark.parametrize("value", ["03956742887", " 3956742887", 3956742887, "0"])
def test_offer_id_must_be_canonical(value):
    reject("BODY_INVALID", body={**BODY, "offer_id": value})


@pytest.mark.parametrize("field,value", [
    ("task_id", " task-17"), ("action_id", "action/3"),
    ("plan_id", "common:3956742887:abc\n"),
    ("contract_digest", "sha256:" + "A" * 64),
    ("user_approved", 1), ("user_approved", False),
])
def test_identity_fields_and_positive_decision_are_exact(field, value):
    reject("BODY_INVALID", body={**BODY, field: value})


def test_truncated_body_rejected():
    raw = json.dumps(BODY).encode()
    fields = [("Host", "127.0.0.1:8765"), ("Origin", "http://127.0.0.1:8765"),
              ("Content-Type", "application/json"), ("Content-Length", str(len(raw))),
              ("X-Orbit-CSRF-Token", "test-csrf"), ("Cookie", "test_session=server-held")]
    framing = inspect_decision_headers(target=TARGET, headers=fields,
                                       client_address="127.0.0.1",
                                       server_address=("127.0.0.1", 8765))
    with pytest.raises(FinalReviewEnvelopeError) as caught:
        parse_decision_body(framing, raw[:-1], session_capability=SyntheticSession())
    assert caught.value.code == "BODY_INCOMPLETE"


@pytest.mark.parametrize("raw", [
    json.dumps(BODY).encode()[:-1] + b',"offer_id":"2"}',
    json.dumps(BODY).encode()[:-1] + b',"extra":{"x":1,"x":2}}',
])
def test_duplicate_json_key_even_nested_rejected(raw):
    fields = [("Host", "127.0.0.1:8765"), ("Origin", "http://127.0.0.1:8765"),
              ("Content-Type", "application/json"), ("Content-Length", str(len(raw))),
              ("X-Orbit-CSRF-Token", "test-csrf"), ("Cookie", "test_session=server-held")]
    framing = inspect_decision_headers(target=TARGET, headers=fields,
                                       client_address="127.0.0.1",
                                       server_address=("127.0.0.1", 8765))
    with pytest.raises(FinalReviewEnvelopeError) as caught:
        parse_decision_body(framing, raw, session_capability=SyntheticSession())
    assert caught.value.code == "BODY_INVALID"


@pytest.mark.parametrize("header", [
    ("Content-Length", "0"), ("Content-Length", "+12"),
    ("Content-Length", "12, 12"), ("Content-Length", "00012"),
])
def test_noncanonical_content_length_rejected(header):
    raw = json.dumps(BODY).encode()
    fields = [("Host", "127.0.0.1:8765"), ("Origin", "http://127.0.0.1:8765"),
              ("Content-Type", "application/json"), header,
              ("X-Orbit-CSRF-Token", "test-csrf"), ("Cookie", "test_session=server-held")]
    reject("FRAMING_INVALID", headers=fields)


@pytest.mark.parametrize("removed,code", [
    ("Content-Length", "FRAMING_INVALID"),
    ("Content-Type", "MEDIA_TYPE_INVALID"),
    ("X-Orbit-CSRF-Token", "AUTH_REQUIRED"),
])
def test_required_framing_and_csrf_headers(removed, code):
    raw = json.dumps(BODY).encode()
    fields = [("Host", "127.0.0.1:8765"), ("Origin", "http://127.0.0.1:8765"),
              ("Content-Type", "application/json"), ("Content-Length", str(len(raw))),
              ("X-Orbit-CSRF-Token", "test-csrf"), ("Cookie", "test_session=server-held")]
    reject(code, headers=[field for field in fields if field[0] != removed])


@pytest.mark.parametrize("duplicated,code", [
    ("Content-Type", "MEDIA_TYPE_INVALID"),
    ("X-Orbit-CSRF-Token", "AUTH_REQUIRED"),
    ("Cookie", "FRAMING_INVALID"),
])
def test_duplicate_security_headers_rejected(duplicated, code):
    raw = json.dumps(BODY).encode()
    fields = [("Host", "127.0.0.1:8765"), ("Origin", "http://127.0.0.1:8765"),
              ("Content-Type", "application/json"), ("Content-Length", str(len(raw))),
              ("X-Orbit-CSRF-Token", "test-csrf"), ("Cookie", "test_session=server-held")]
    fields.extend([(key.lower(), value) for key, value in fields if key == duplicated])
    reject(code, headers=fields)


def test_bad_session_is_not_replaced_by_body_claim():
    raw = json.dumps(BODY).encode()
    fields = [("Host", "127.0.0.1:8765"), ("Origin", "http://127.0.0.1:8765"),
              ("Content-Type", "application/json"), ("Content-Length", str(len(raw))),
              ("X-Orbit-CSRF-Token", "test-csrf")]
    reject("AUTH_REQUIRED", headers=fields)


def test_excessive_body_rejected_before_read():
    fields = [("Host", "127.0.0.1:8765"), ("Origin", "http://127.0.0.1:8765"),
              ("Content-Type", "application/json"), ("Content-Length", "99999999"),
              ("X-Orbit-CSRF-Token", "test-csrf")]
    with pytest.raises(FinalReviewEnvelopeError) as caught:
        inspect_decision_headers(target=TARGET, headers=fields,
                                 client_address="127.0.0.1",
                                 server_address=("127.0.0.1", 8765))
    assert caught.value.code == "BODY_TOO_LARGE"


def test_overlong_decimal_content_length_is_controlled_before_integer_conversion():
    raw = json.dumps(BODY).encode()
    fields = [("Host", "127.0.0.1:8765"), ("Origin", "http://127.0.0.1:8765"),
              ("Content-Type", "application/json"), ("Content-Length", "9" * 5000),
              ("X-Orbit-CSRF-Token", "test-csrf")]
    with pytest.raises(FinalReviewEnvelopeError) as caught:
        inspect_decision_headers(target=TARGET, headers=fields,
                                 client_address="127.0.0.1",
                                 server_address=("127.0.0.1", 8765))
    assert caught.value.code == "BODY_TOO_LARGE"
    assert caught.value.status == 413


def test_small_but_deep_json_returns_controlled_body_error():
    raw = b"[" * 1200 + b"0" + b"]" * 1200
    fields = [("Host", "127.0.0.1:8765"), ("Origin", "http://127.0.0.1:8765"),
              ("Content-Type", "application/json"), ("Content-Length", str(len(raw))),
              ("X-Orbit-CSRF-Token", "test-csrf"), ("Cookie", "test_session=server-held")]
    framing = inspect_decision_headers(target=TARGET, headers=fields,
                                       client_address="127.0.0.1",
                                       server_address=("127.0.0.1", 8765))
    with pytest.raises(FinalReviewEnvelopeError) as caught:
        parse_decision_body(framing, raw, session_capability=SyntheticSession())
    assert caught.value.code == "BODY_INVALID"
    assert caught.value.status == 400
