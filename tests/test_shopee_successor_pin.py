import hashlib
import json

import pytest

from shared_platform import shopee_category_attribute_successor as successor
from test_shopee_category_attribute_successor import approval


def _evidence(product_id: str, source_digest: str):
    return {
        "schema_version": "shopee-current-official-category-observation/v1",
        "offer_id": product_id,
        "targets": ["shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN"],
        "authority": "SHOPEE_OFFICIAL",
        "source_region": "PH",
        "account_binding": {"shop_region_exact": True, "merchant_identity_exact": True},
        "selected_category_id": 101157,
        "selected_category_path": [{"category_id": 100636}, {"category_id": 100711}, {"category_id": 101157}],
        "attribute_tree": [{"attribute_id": 100818, "is_mandatory": True, "attribute_value_list": [{"value_id": 4228, "original_value_name": "No"}, {"value_id": 4235, "original_value_name": "Yes"}]}],
        "public_observation": {"schema_version": "shopee-official-new-global-candidate-observation/v1", "authority": "shopee_official_open_api", "checks": {"recommendation_observed": True, "selected_category_observed": True, "selected_category_was_recommended": True}},
        "official_reads": [{"endpoint": "/api/v2/global_product/category_recommend"}],
        "official_read_count": 2,
        "platform_product_write_count": 0,
        "external_writes_performed": [],
        "evidence_digest": source_digest,
    }


def _scan(product_id: str, sku: str = "0988"):
    return {
        "schema_version": "shopee-current-global-sku-scan/v1",
        "offer_id": product_id,
        "seller_sku": sku,
        "model_sku": sku,
        "authority": "SHOPEE_OFFICIAL",
        "source_region": "PH",
        "scan_mode": "NORMAL_UNLIST_BANNED_COMPLETE",
        "matches": [],
        "classification": "NEW_GLOBAL",
        "official_reads": [{"endpoint": "/api/v2/global_product/get_global_item_list"}],
        "official_read_count": 2,
        "platform_product_write_count": 0,
        "external_writes_performed": [],
    }


def _write(path, value):
    raw = (json.dumps(value) + "\n").encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_successor_pin_requires_exact_current_official_observation(tmp_path, monkeypatch):
    source_digest = "sha256:" + "7" * 64
    approved = approval("395")
    approved["decision_basis"]["source_evidence_digest"] = source_digest
    approval_path = tmp_path / "approval.json"
    evidence_path = tmp_path / "evidence.json"
    scan_path = tmp_path / "scan.json"
    monkeypatch.setenv(successor.APPROVAL_PATH_ENV, str(approval_path.resolve()))
    monkeypatch.setenv(successor.APPROVAL_OFFER_ENV, "395")
    monkeypatch.setenv(successor.APPROVAL_SHA256_ENV, _write(approval_path, approved))
    monkeypatch.setenv(successor.EVIDENCE_PATH_ENV, str(evidence_path.resolve()))
    monkeypatch.setenv(successor.EVIDENCE_SHA256_ENV, _write(evidence_path, _evidence("395", source_digest)))
    monkeypatch.setenv(successor.GLOBAL_SCAN_PATH_ENV, str(scan_path.resolve()))
    monkeypatch.setenv(successor.GLOBAL_SCAN_SHA256_ENV, _write(scan_path, _scan("395")))
    monkeypatch.setenv(successor.GLOBAL_SCAN_SKU_ENV, "0988")
    assert successor.configured_successor_approval("396") is None
    assert successor.configured_successor_approval("395")["offer_id"] == "395"


def test_successor_pin_rejects_evidence_drift(tmp_path, monkeypatch):
    approval_path = tmp_path / "approval.json"
    evidence_path = tmp_path / "evidence.json"
    scan_path = tmp_path / "scan.json"
    monkeypatch.setenv(successor.APPROVAL_PATH_ENV, str(approval_path.resolve()))
    monkeypatch.setenv(successor.APPROVAL_OFFER_ENV, "395")
    monkeypatch.setenv(successor.APPROVAL_SHA256_ENV, _write(approval_path, approval("395")))
    monkeypatch.setenv(successor.EVIDENCE_PATH_ENV, str(evidence_path.resolve()))
    monkeypatch.setenv(successor.EVIDENCE_SHA256_ENV, "0" * 64)
    monkeypatch.setenv(successor.GLOBAL_SCAN_PATH_ENV, str(scan_path.resolve()))
    monkeypatch.setenv(successor.GLOBAL_SCAN_SHA256_ENV, _write(scan_path, _scan("395")))
    monkeypatch.setenv(successor.GLOBAL_SCAN_SKU_ENV, "0988")
    _write(evidence_path, _evidence("395", "sha256:" + "9" * 64))
    with pytest.raises(ValueError, match="digest drifted"):
        successor.configured_successor_approval("395")


def test_successor_pin_rejects_global_scan_drift(tmp_path, monkeypatch):
    source_digest = "sha256:" + "7" * 64
    approved = approval("395")
    approved["decision_basis"]["source_evidence_digest"] = source_digest
    approval_path = tmp_path / "approval.json"
    evidence_path = tmp_path / "evidence.json"
    scan_path = tmp_path / "scan.json"
    monkeypatch.setenv(successor.APPROVAL_PATH_ENV, str(approval_path.resolve()))
    monkeypatch.setenv(successor.APPROVAL_OFFER_ENV, "395")
    monkeypatch.setenv(successor.APPROVAL_SHA256_ENV, _write(approval_path, approved))
    monkeypatch.setenv(successor.EVIDENCE_PATH_ENV, str(evidence_path.resolve()))
    monkeypatch.setenv(successor.EVIDENCE_SHA256_ENV, _write(evidence_path, _evidence("395", source_digest)))
    monkeypatch.setenv(successor.GLOBAL_SCAN_PATH_ENV, str(scan_path.resolve()))
    monkeypatch.setenv(successor.GLOBAL_SCAN_SHA256_ENV, _write(scan_path, _scan("395")))
    monkeypatch.setenv(successor.GLOBAL_SCAN_SKU_ENV, "0988")
    scan_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="digest drifted"):
        successor.configured_successor_approval("395")


def test_absolute_shopee_token_path_pin(monkeypatch, tmp_path):
    from modules.shopee import auth
    path = (tmp_path / "tokens.json").resolve()
    monkeypatch.setenv("ORBIT_SHOPEE_TOKEN_PATH", str(path))
    assert auth.token_path() == path
