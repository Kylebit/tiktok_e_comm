"""Shopee API v2 签名。"""

from __future__ import annotations

import hashlib
import hmac
import time


def sign_partner(path: str, partner_id: int, partner_key: str, timestamp: int | None = None) -> tuple[int, str]:
    """Public API / 换 token：base = partner_id + path + timestamp。"""
    ts = int(timestamp if timestamp is not None else time.time())
    base = f"{partner_id}{path}{ts}"
    sig = hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()
    return ts, sig


def sign_shop(
    path: str,
    partner_id: int,
    partner_key: str,
    access_token: str,
    shop_id: int,
    timestamp: int | None = None,
) -> tuple[int, str]:
    """Shop API：base = partner_id + path + timestamp + access_token + shop_id。"""
    ts = int(timestamp if timestamp is not None else time.time())
    base = f"{partner_id}{path}{ts}{access_token}{shop_id}"
    sig = hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()
    return ts, sig


def sign_merchant(
    path: str,
    partner_id: int,
    partner_key: str,
    access_token: str,
    merchant_id: int,
    timestamp: int | None = None,
) -> tuple[int, str]:
    """Merchant API (CNSC global product)：base = partner_id + path + timestamp + access_token + merchant_id。"""
    ts = int(timestamp if timestamp is not None else time.time())
    base = f"{partner_id}{path}{ts}{access_token}{merchant_id}"
    sig = hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()
    return ts, sig
