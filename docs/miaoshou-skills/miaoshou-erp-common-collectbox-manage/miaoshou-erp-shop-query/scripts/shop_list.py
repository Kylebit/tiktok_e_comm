"""
Miaoshou ERP (JCOP Open Platform) - Query Authorized Shop List

Usage:
    python shop_list.py list --platform tiktok
    python shop_list.py list --platform tiktok --site US
    python shop_list.py list --platform shopee --page 1 --size 50
    python shop_list.py list-all
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package is required. Install with: pip install requests")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load Miaoshou Open Platform credentials from local config or environment."""
    base_dir = Path(__file__).resolve().parent.parent
    config_path = base_dir / "resources" / "config.json"
    config = {}

    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

    env_app_key = os.getenv("MIAOSHOU_APP_KEY")
    env_app_secret = os.getenv("MIAOSHOU_APP_SECRET")
    env_base_url = os.getenv("MIAOSHOU_BASE_URL")

    if env_app_key:
        config["app_key"] = env_app_key
    if env_app_secret:
        config["app_secret"] = env_app_secret
    if env_base_url:
        config["base_url"] = env_base_url

    config.setdefault("base_url", "https://openapi-erp.91miaoshou.com")
    config.setdefault("timeout", 30)

    app_key = str(config.get("app_key", "")).strip()
    app_secret = str(config.get("app_secret", "")).strip()
    placeholder_values = {"your_app_key_here", "your_app_secret_here", ""}
    if app_key in placeholder_values or app_secret in placeholder_values:
        print("ERROR: Miaoshou Open Platform credentials are not configured.")
        print(f"Create {config_path} from resources/config.json.example and fill app_key/app_secret,")
        print("or set MIAOSHOU_APP_KEY and MIAOSHOU_APP_SECRET in the environment.")
        print("Use base_url: https://openapi-erp.91miaoshou.com")
        sys.exit(1)

    return config

# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------

def generate_sign(app_secret: str, path: str, timestamp: int, app_key: str, body_json: str = "") -> str:
    """Generate HmacSHA256 signature for JCOP Open Platform."""
    content = f"{app_secret}{path}{timestamp}{app_key}{body_json}{app_secret}"
    return hmac.new(
        app_secret.encode("utf-8"),
        content.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Common platforms to scan when using list-all
# Platform -> common sites mapping for list-all scanning
PLATFORM_SITES = {
    "tiktok": ["US", "UK", "TH", "VN", "MY", "PH", "SG", "ID"],
    "shopee": ["MY", "TH", "VN", "PH", "SG", "ID", "TW", "BR"],
    "lazada": ["MY", "TH", "VN", "PH", "SG", "ID"],
    "amazon": ["US", "UK", "DE", "FR", "JP", "CA", "AU", "IT", "ES"],
    "ozon": ["RU"],
    "pddkj": ["US", "UK", "DE"],
    "shein": [""],
    "aliexpress": [""],
    "wish": [""],
    "shopify": [""],
    "shopline": [""],
    "coupang": ["KR"],
    "mercadolibre": ["MX", "BR", "CO"],
    "walmart": ["US"],
    "allegro": ["PL"],
}

COMMON_PLATFORMS = list(PLATFORM_SITES.keys())

PLATFORM_DISPLAY_NAMES = {
    "tiktok": "TikTok Shop",
    "shopee": "Shopee",
    "lazada": "Lazada",
    "amazon": "Amazon",
    "ozon": "Ozon",
    "pddkj": "Temu",
    "shein": "Shein",
    "aliexpress": "AliExpress",
    "wish": "Wish",
    "shopify": "Shopify",
    "shopline": "Shopline",
    "coupang": "Coupang",
    "mercadolibre": "MercadoLibre",
    "walmart": "Walmart",
    "allegro": "Allegro",
}


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------

class ShopListClient:
    def __init__(self, config: dict):
        self.base_url = config["base_url"].rstrip("/")
        self.app_key = config["app_key"]
        self.app_secret = config["app_secret"]
        self.timeout = config.get("timeout", 30)

    def _post(self, path: str, body: dict, silent: bool = False) -> dict:
        """Send a signed POST request to the API."""
        timestamp = int(time.time())
        body_json = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        sign = generate_sign(self.app_secret, path, timestamp, self.app_key, body_json)

        headers = {
            "Content-Type": "application/json",
            "x-app-key": self.app_key,
            "x-timestamp": str(timestamp),
            "x-sign": sign,
        }

        url = f"{self.base_url}{path}"

        resp = requests.post(url, headers=headers, data=body_json.encode("utf-8"), timeout=self.timeout)
        resp.raise_for_status()

        if not resp.text or not resp.text.strip():
            if not silent:
                print(f"\nAPI Error: empty response (HTTP {resp.status_code})")
                print("Possible causes: VPN disconnected / JCOP service unavailable / IP not whitelisted")
            return {"result": "fail", "code": "emptyResponse", "message": "Empty response"}

        result = resp.json()

        if result.get("result") != "success" and not silent:
            print(f"\nAPI Error: [{result.get('code')}] {result.get('message', '')}")

        return result

    def get_shop_list(
        self,
        platform: str,
        site: str = "",
        page: int = 1,
        size: int = 100,
        silent: bool = False,
    ) -> dict:
        """Query authorized shop list by platform and site."""
        path = "/open/v1/product/shop/shop/get_shop_list"
        body: Dict[str, Any] = {
            "platform": platform,
            "site": site,
        }
        if page:
            body["pageNo"] = page
        if size:
            body["pageSize"] = size
        return self._post(path, body, silent=silent)


# ---------------------------------------------------------------------------
# Output Helpers
# ---------------------------------------------------------------------------

def print_shop_list(data: dict, platform: str):
    """Pretty-print shop list for a single platform."""
    shop_list = data.get("data", {}).get("shopList", [])
    display_name = PLATFORM_DISPLAY_NAMES.get(platform, platform)

    if not shop_list:
        print(f"  {display_name}: (no shops)")
        return []

    print(f"\n{'='*90}")
    print(f"  {display_name} - {len(shop_list)} shop(s)")
    print(f"{'='*90}")
    print(f"  {'#':<4} {'Shop ID':<12} {'Shop Name':<25} {'Site':<8} {'Status':<10} {'CB':<4} {'CNSC':<5} {'Expire':<20}")
    print(f"  {'-'*4} {'-'*12} {'-'*25} {'-'*8} {'-'*10} {'-'*4} {'-'*5} {'-'*20}")

    for i, shop in enumerate(shop_list, 1):
        shop_id = shop.get("shopId", "-")
        shop_nick = shop.get("shopNick", "-") or "-"
        site = shop.get("site", "-") or "-"
        status = shop.get("status", "-")
        is_cb = "Y" if shop.get("isCb") == 1 else "N"
        is_cnsc = "Y" if shop.get("isCnsc") == 1 else "N"
        expire = shop.get("gmtExpire", "-") or "-"

        # Truncate long shop names
        if len(shop_nick) > 24:
            shop_nick = shop_nick[:21] + "..."

        print(f"  {i:<4} {shop_id:<12} {shop_nick:<25} {site:<8} {status:<10} {is_cb:<4} {is_cnsc:<5} {expire:<20}")

    return shop_list


def print_summary(all_shops: Dict[str, list]):
    """Print summary across all platforms."""
    total = sum(len(shops) for shops in all_shops.values())
    active_platforms = {p: s for p, s in all_shops.items() if s}

    print(f"\n{'='*90}")
    print(f"  Summary: {total} shop(s) across {len(active_platforms)} platform(s)")
    print(f"{'='*90}")

    if not active_platforms:
        print("  No authorized shops found on any platform.")
        return

    for platform, shops in active_platforms.items():
        display_name = PLATFORM_DISPLAY_NAMES.get(platform, platform)
        normal_count = sum(1 for s in shops if s.get("status") == "normal")
        print(f"  {display_name:<20} {len(shops)} shop(s) ({normal_count} normal)")


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------

def cmd_list(client: ShopListClient, args):
    """Query shop list for a specific platform."""
    platform = args.platform.lower().strip()
    site = args.site or ""
    page = args.page or 1
    size = args.size or 100

    result = client.get_shop_list(platform=platform, site=site, page=page, size=size)

    if result.get("result") == "success":
        shops = print_shop_list(result, platform)
        print(f"\n  Total: {len(shops)} shop(s)")
    else:
        print(f"\n  Failed to query shops for platform '{platform}'")

    return result


def cmd_list_all(client: ShopListClient, args):
    """Query shop list across all common platforms by iterating platform+site combos."""
    all_shops: Dict[str, list] = {}
    seen_shop_ids = set()

    for platform in COMMON_PLATFORMS:
        sites = PLATFORM_SITES.get(platform, [""])
        platform_shops = []

        for site in sites:
            try:
                result = client.get_shop_list(platform=platform, site=site, page=1, size=100, silent=True)
                if result.get("result") == "success":
                    shop_list = result.get("data", {}).get("shopList", [])
                    for shop in shop_list:
                        sid = shop.get("shopId")
                        if sid and sid not in seen_shop_ids:
                            seen_shop_ids.add(sid)
                            platform_shops.append(shop)
            except Exception:
                pass

        if platform_shops:
            all_shops[platform] = platform_shops
            # Build a fake result for printing
            print_shop_list({"data": {"shopList": platform_shops}}, platform)

    print_summary(all_shops)

    return all_shops


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Miaoshou ERP - Query Authorized Shop List",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Query TikTok shops
  %(prog)s list --platform tiktok

  # Query TikTok shops in US site
  %(prog)s list --platform tiktok --site US

  # Query Shopee shops with pagination
  %(prog)s list --platform shopee --page 1 --size 50

  # Scan all common platforms
  %(prog)s list-all
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # list sub-command
    list_parser = subparsers.add_parser("list", help="Query shops for a specific platform")
    list_parser.add_argument("--platform", type=str, required=True,
                             help="Platform code (tiktok, shopee, lazada, amazon, ozon, pddkj, shein, etc.)")
    list_parser.add_argument("--site", type=str, default="",
                             help="Site code (US, UK, TH, etc.). Empty for all sites.")
    list_parser.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    list_parser.add_argument("--size", type=int, default=100, help="Page size (default: 100)")

    # list-all sub-command
    subparsers.add_parser("list-all", help="Scan all common platforms for authorized shops")

    # --raw flag
    parser.add_argument("--raw", action="store_true", help="Output raw JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    config = load_config()
    client = ShopListClient(config)

    command_map = {
        "list": cmd_list,
        "list-all": cmd_list_all,
    }

    data = command_map[args.command](client, args)

    if args.raw:
        print("\n--- RAW JSON ---")
        print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

