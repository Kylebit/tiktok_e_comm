"""Real Runner request factory -> regional dispatch -> SDK -> raw wire fixture.

Only credential/configuration and provider network edges are synthetic. No SDK
parser, provider journal, Windows ACL check, or request constructor is mocked.
"""
from copy import deepcopy
from dataclasses import replace
import io
import json
from pathlib import Path
import sqlite3
import urllib.error
import urllib.request

import pytest

from domains.product_operations import build_approved_publication_snapshot
from modules.shopee import client
from modules.shopee.skill_regions import OfficialShopeeRegionRuntime, RegionContext, dispatch_selected_regions
from shared_platform.product_publication_runner import ProductPublicationRunner
from shared_platform.publication_provider_journal import (
    ProviderOutcomeUnknown, ProviderResponseJournal, _scope,
    retained_provider_executor, retained_urlopen, provider_recovery_state,
)
from test_approved_publication_snapshot import _approved_plan, _rebind
from test_product_publication_runner import _SnapshotStore, _report_store, _platform_result


class WireResponse(io.BytesIO):
    status = 200
    headers = {"Content-Type": "application/json"}
    def geturl(self):
        return "https://example.invalid/provider"


def _request(tmp_path):
    # Genuine factory input: fresh product plus three models, validated by the
    # existing snapshot builder and Runner. Do not handcraft the request shape.
    plan = json.loads(json.dumps(_approved_plan()).replace("3838616043", "9753102468")
                      .replace("0958", "7301").replace("0959", "7302"))
    p = plan["payload"]
    p["sku_lineage"]["assignment"]["model_skus"].append(
        {"variant_key": "green-38x45", "model_sku": "7303"})
    facts = p["product_facts"]
    facts["selected_sku_keys"].append("green-38x45")
    facts["sku_commercial_facts"]["green-38x45"] = deepcopy(facts["sku_commercial_facts"]["pink-38x45"])
    facts["sku_commercial_facts"]["green-38x45"]["specification"]["color"] = "Green"
    for prices in p["pricing"]["selected_targets"].values():
        prices["sku_prices"].append({**prices["sku_prices"][-1], "model_sku": "7303"})
    p["shopee_global_master"]["sku_original_prices_cny"].append(
        {**p["shopee_global_master"]["sku_original_prices_cny"][-1], "model_sku": "7303"})
    p["shopee_global_master"]["variant_image_positions"].append(
        {**p["shopee_global_master"]["variant_image_positions"][-1], "model_sku": "7303"})
    snapshot = build_approved_publication_snapshot(_rebind(plan)).payload()
    requests = []
    def capture(request):
        requests.append(request)
        return _platform_result("SHOPEE", [("shopee:PH", "FAILED")], writes=0,
                                dispatch_attempted=False, readback_completed=False)
    ProductPublicationRunner(release_store=_SnapshotStore(snapshot), report_store=_report_store(tmp_path)).run(
        run_id="fresh-provider-capture", offer_id=snapshot["offer_id"], plan_id=snapshot["plan_id"],
        platform_scope=("SHOPEE",), platform_executors={"SHOPEE": capture})
    assert len(requests) == 1 and len(requests[0].snapshot["skus"]) == 3
    return requests[0]


class ReadFixtureRuntime(OfficialShopeeRegionRuntime):
    def context(self, region):
        return RegionContext(region, 101, 500, "synthetic-shop-token", "synthetic-merchant-token")
    def global_models(self, _context, _global_id):
        return [{"global_model_sku": sku, "tier_index": [i]} for i, sku in enumerate(("7301", "7302", "7303"))]
    def compatible_logistics(self, *_args, **_kwargs):
        return [2001]
    def existing_regional_item(self, *_args):
        return None
    def discover_existing_regional_item(self, *_args):
        return None


def _sdk(monkeypatch, wire):
    monkeypatch.setattr(client, "shopee_config", lambda: {
        "host": "https://example.invalid", "partner_id": 123,
        "partner_key": "synthetic-partner-key"})
    monkeypatch.setattr(client, "urlopen_retry", wire)


def _dispatch(request):
    return retained_provider_executor(lambda r: dispatch_selected_regions(
        r.snapshot, global_item_id="87654321", runtime=ReadFixtureRuntime(),
        target_scope=r.target_labels))(request)


def _intents(request):
    root, identity = _scope(request)
    journal = ProviderResponseJournal(root, identity)
    with sqlite3.connect(journal.path) as c:
        c.row_factory = sqlite3.Row
        return journal, [dict(row) for row in c.execute("SELECT * FROM provider_response_intents")]


def test_original_task_survives_real_sdk_decoder_exception_without_resend(tmp_path, monkeypatch):
    request = _request(tmp_path)
    body = b'{"error":"","response":{"publish_task_id":81234567},"request_id":"wire-original-1"}'
    calls = []
    def wire(req, **options):
        assert options["attempts"] == 1 and options["allow_curl_fallback"] is False
        calls.append(json.loads(req.data))
        return WireResponse(body)
    _sdk(monkeypatch, wire)
    original = client._parse_json
    monkeypatch.setattr(client, "_parse_json", lambda *_args: (_ for _ in ()).throw(RuntimeError("decoder interruption")))
    first = _dispatch(request)
    assert first["targets"][0]["outcome"] == "UNKNOWN"
    journal, rows = _intents(request)
    assert rows[0]["response_body"] == body and rows[0]["phase"] == "RESPONSE_RETAINED"
    assert "synthetic-merchant-token" not in rows[0]["request_identity_json"]
    assert "sign" not in rows[0]["request_identity_json"]
    # The read-only recovery consumer can extract the original task before
    # re-entering any executor or its mutation budget.
    readonly = provider_recovery_state(*_scope(request))
    assert readonly["intents"][0]["original_identities"]["response.publish_task_id"] == "81234567"
    assert len(calls) == 1
    monkeypatch.setattr(client, "_parse_json", original)
    # A new process/run cannot hide the old retained/unknown identity.
    resumed = replace(request, run_id="fresh-provider-resume", report_id="publication-report:fresh-provider-resume")
    second = _dispatch(resumed)
    assert second["targets"][0]["provider_task_id"] == "81234567"
    assert second["targets"][0]["expected_model_count"] == 3
    assert len(calls) == 1
    state = provider_recovery_state(*_scope(request))
    assert state["intents"][0]["original_identities"]["response.publish_task_id"] == "81234567"
    assert "response_body" not in json.dumps(state)


def test_transport_unknown_blocks_same_or_changed_payload_and_new_run(tmp_path, monkeypatch):
    request = _request(tmp_path)
    calls = []
    def wire(req, **options):
        calls.append(req.data)
        raise TimeoutError("could have reached provider")
    _sdk(monkeypatch, wire)
    assert _dispatch(request)["targets"][0]["outcome"] == "UNKNOWN"
    assert _dispatch(replace(request, run_id="another-run"))["targets"][0]["outcome"] == "UNKNOWN"
    root, identity = _scope(request)
    with pytest.raises(ProviderOutcomeUnknown):
        ProviderResponseJournal(root, identity).claim({"changed": "body"})
    assert len(calls) == 1
    assert _intents(request)[1][0]["phase"] == "UNKNOWN"


def test_response_save_failure_is_not_safe_to_resend(tmp_path, monkeypatch):
    request = _request(tmp_path)
    calls = []
    _sdk(monkeypatch, lambda req, **_: (calls.append(req.data) or WireResponse(b'{"response":{"publish_task_id":12345}}')))
    original = ProviderResponseJournal.retain
    monkeypatch.setattr(ProviderResponseJournal, "retain", lambda *_: (_ for _ in ()).throw(OSError("disk write failed")))
    assert _dispatch(request)["targets"][0]["outcome"] == "UNKNOWN"
    monkeypatch.setattr(ProviderResponseJournal, "retain", original)
    assert _dispatch(request)["targets"][0]["outcome"] == "UNKNOWN"
    assert len(calls) == 1
    assert _intents(request)[1][0]["response_body"] is None


def test_source_root_or_unknown_existing_directory_refused_before_wire(tmp_path, monkeypatch):
    request = _request(tmp_path)
    calls = []
    _sdk(monkeypatch, lambda *a, **k: calls.append(a))
    source = replace(request, checkpoint_root=Path(__file__).resolve().parents[1])
    assert _dispatch(source)["targets"][0]["outcome"] == "UNKNOWN"
    root, identity = _scope(request)
    journal = ProviderResponseJournal(root, identity)
    journal.root.mkdir(parents=True)
    assert _dispatch(request)["targets"][0]["outcome"] == "UNKNOWN"
    assert calls == []


def test_ozon_sdk_retains_raw_before_decode_and_reuses_original(tmp_path, monkeypatch):
    from modules.ozon import client as ozon
    request = replace(_request(tmp_path), platform="OZON", target_labels=("ozon:RU",))
    calls = []
    body = b'{"result":{"task_id":91234567}}'
    monkeypatch.setattr(ozon, "urlopen", lambda req, **opts: (calls.append(req.data) or WireResponse(body)))
    execute = retained_provider_executor(lambda _: ozon.ozon_post_bound("/v3/product/import",
        {"items": [{"offer_id": "7301-B"}]}, client_id="70101", api_key="synthetic-key"))
    assert execute(request)["result"]["task_id"] == 91234567
    assert execute(request)["result"]["task_id"] == 91234567
    assert len(calls) == 1 and _intents(request)[1][0]["response_body"] == body


def test_readonly_passthrough_creates_no_journal(tmp_path):
    request = _request(tmp_path)
    root = Path(request.checkpoint_root) / "private-provider-responses"
    read = urllib.request.Request("https://example.invalid/api/v2/global_product/get_publishable_shop")
    def execute(_):
        with retained_urlopen("shopee", read, lambda *a, **k: WireResponse(b'{}')) as response:
            return response.read()
    assert retained_provider_executor(execute)(request) == b'{}'
    assert not root.exists()


def test_lexical_junction_is_refused_before_resolve_or_wire(tmp_path, monkeypatch):
    import os
    request = _request(tmp_path)
    direct = tmp_path / "direct"
    direct.mkdir()
    junction = tmp_path / "junction"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(direct), str(junction))
    else:
        junction.symlink_to(direct, target_is_directory=True)
    calls = []
    _sdk(monkeypatch, lambda *a, **k: calls.append(a))
    assert _dispatch(replace(request, checkpoint_root=junction))["targets"][0]["outcome"] == "UNKNOWN"
    assert calls == [] and not (direct / "private-provider-responses").exists()


def test_malformed_retained_body_stays_private_and_never_resends(tmp_path, monkeypatch):
    request = _request(tmp_path)
    calls = []
    secret = b'{"access_token":"synthetic-private-token", malformed'
    _sdk(monkeypatch, lambda req, **_: (calls.append(req.data) or WireResponse(secret)))
    execute = retained_provider_executor(lambda _: client.merchant_post(
        "/api/v2/global_product/create_publish_task", 500, "synthetic-token",
        {"global_item_id": 87654321, "shop_id": 101, "shop_region": "PH"}))
    for _ in range(2):
        with pytest.raises(RuntimeError) as error:
            execute(request)
        assert "synthetic-private-token" not in str(error.value)
    assert len(calls) == 1
    assert _intents(request)[1][0]["response_body"] == secret
    state = provider_recovery_state(*_scope(request))
    assert state["intents"][0]["decode_status"] == "UNPARSEABLE_ORIGINAL"
    assert "synthetic-private-token" not in json.dumps(state)


def test_http_error_raw_body_is_private_and_replay_does_not_reopen(tmp_path, monkeypatch):
    from modules.ozon import client as ozon
    request = replace(_request(tmp_path), platform="OZON", target_labels=("ozon:RU",))
    calls = []
    raw = b'{"access_token":"synthetic-private-http-token","code":"BAD_INPUT"}'
    def wire(req, **_):
        calls.append(req.data)
        raise urllib.error.HTTPError(req.full_url, 400, "bad fixture input", {}, io.BytesIO(raw))
    monkeypatch.setattr(ozon, "urlopen", wire)
    execute = retained_provider_executor(lambda _: ozon.ozon_post_bound("/v3/product/import",
        {"items": [{"offer_id": "7301-B"}]}, client_id="70101", api_key="synthetic-key"))
    for _ in range(2):
        with pytest.raises(RuntimeError) as error:
            execute(request)
        assert "synthetic-private-http-token" not in str(error.value)
    assert len(calls) == 1 and _intents(request)[1][0]["response_body"] == raw


def test_miaoshou_sdk_retains_compressed_original_then_decodes_once(tmp_path, monkeypatch):
    import gzip
    from modules.miaoshou import client as miaoshou
    request = replace(_request(tmp_path), platform="TIKTOK", target_labels=("tiktok:LH_PH",))
    calls = []
    raw = gzip.compress(b'{"result":"success","data":{"task_id":"provider-123456"}}')
    def wire(req, **_):
        calls.append(req.data)
        response = WireResponse(raw)
        response.headers = {"Content-Encoding": "gzip"}
        return response
    monkeypatch.setattr(urllib.request, "urlopen", wire)
    execute = retained_provider_executor(lambda _: miaoshou.post_open("/open/product/publish",
        {"detailId": "123456"}, cfg={"app_id": "synthetic-app", "app_secret": "synthetic-secret",
        "base_url": "https://example.invalid"}))
    assert execute(request)["data"]["task_id"] == "provider-123456"
    assert execute(request)["data"]["task_id"] == "provider-123456"
    assert len(calls) == 1 and _intents(request)[1][0]["response_body"] == raw
    assert provider_recovery_state(*_scope(request))["intents"][0]["original_identities"]["data.task_id"] == "provider-123456"
