"""
Miaoshou ERP (JCOP Open Platform) - Claim Products to Platform Collect Box

Usage:
    python claim_to_platform.py claim --detail-ids ID1,ID2,ID3 --platform tiktok
    python claim_to_platform.py claim --detail-ids 12345 --platform tiktok
    python claim_to_platform.py batch-claim --file claim_list.json
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Any

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
    """
    Generate HmacSHA256 signature for JCOP Open Platform.
    
    sign = HmacSHA256(appSecret, appSecret + path + timestamp + appKey + bodyJson + appSecret)
    """
    content = f"{app_secret}{path}{timestamp}{app_key}{body_json}{app_secret}"
    sign = hmac.new(
        app_secret.encode("utf-8"),
        content.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return sign


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------

class JCopClient:
    # Supported platforms mapping
    PLATFORM_MAP = {
        "tiktok": "tiktok",
        "tt": "tiktok",
        "shopee": "shopee",
        "sp": "shopee",
        "lazada": "lazada",
        "lz": "lazada",
        "amazon": "amazon",
        "amz": "amazon",
        "ozon": "ozon",
        "temu": "temu",
        "shein": "shein",
    }

    def __init__(self, config: dict):
        self.base_url = config["base_url"].rstrip("/")
        self.app_key = config["app_key"]
        self.app_secret = config["app_secret"]
        self.timeout = config.get("timeout", 30)

    def _post(self, path: str, body: dict) -> dict:
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
        print(f"POST {url}")
        print(f"Body: {body_json[:300]}{'...' if len(body_json) > 300 else ''}")

        resp = requests.post(url, headers=headers, data=body_json.encode("utf-8"), timeout=self.timeout)
        resp.raise_for_status()

        # Handle empty response (upstream server unavailable or VPN disconnected)
        if not resp.text or not resp.text.strip():
            print(f"\nAPI Error: 服务器返回空响应（HTTP {resp.status_code}）")
            print(f"可能原因：VPN已断开 / JCOP平台服务不可用 / IP不在白名单")
            sys.exit(1)

        result = resp.json()

        if result.get("result") != "success":
            print(f"\nAPI Error: [{result.get('code')}] {result.get('message')}")
            sys.exit(1)

        return result

    def normalize_platform(self, platform: str) -> str:
        """Normalize platform name to standard format."""
        platform_lower = platform.lower().strip()
        if platform_lower in self.PLATFORM_MAP:
            return self.PLATFORM_MAP[platform_lower]
        return platform_lower

    def get_common_collect_detail(self, detail_id: int) -> dict:
        """
        获取公共采集箱商品详情。

        Args:
            detail_id: 公共采集箱商品ID（注意：参数名为 commonCollectBoxDetailId）
        Returns:
            包含商品详情的响应
        """
        path = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail"
        body = {"commonCollectBoxDetailId": detail_id}
        return self._post(path, body)

    def claim_to_platform(
        self,
        detail_ids: List[int],
        platform: str,
        serial_number: int = 1
    ) -> dict:
        """
        Claim products from common collect box to platform collect box.
        
        Args:
            detail_ids: List of common collect box detail IDs
            platform: Target platform (tiktok, shopee, lazada, amazon, etc.)
            serial_number: Internal API compatibility field (default: 1)
        """
        path = "/open/v1/product/common_collect_box/common_collect_box/claimed"
        
        platform_normalized = self.normalize_platform(platform)
        
        detail_list = [
            {
                "detailId": detail_id,
                "platform": platform_normalized,
                "serialNumber": serial_number
            }
            for detail_id in detail_ids
        ]
        
        body = {
            "detailSerialNumberPlatformList": detail_list
        }

        return self._post(path, body)


# ---------------------------------------------------------------------------
# Output Helpers
# ---------------------------------------------------------------------------

def print_claim_result(data: dict, detail_ids: List[int], platform: str):
    """Pretty-print claim result."""
    platform_map = data.get("platformCollectBoxDetailIdMap", {})
    
    print(f"\n{'='*80}")
    print(f"认领结果  |  平台: {platform}  |  认领商品数: {len(detail_ids)}")
    print(f"{'='*80}")

    if not platform_map:
        print("⚠️  未返回平台映射数据")
        return

    platform_data = platform_map.get(platform, {})
    
    if not platform_data:
        print(f"⚠️  未找到平台 '{platform}' 的映射数据")
        print(f"   可用平台: {list(platform_map.keys())}")
        return

    print(f"\n✅ 认领成功！平台采集箱详情ID映射:")
    print(f"{'公共采集箱ID':<20} -> {'平台采集箱ID':<20}")
    print(f"{'-'*20}    {'-'*20}")
    
    success_count = 0
    for detail_id in detail_ids:
        detail_id_str = str(detail_id)
        if detail_id_str in platform_data:
            platform_detail_id = platform_data[detail_id_str]
            print(f"{detail_id:<20} -> {platform_detail_id:<20}")
            success_count += 1
        else:
            print(f"{detail_id:<20} -> (未找到映射)")
    
    print(f"\n成功: {success_count}/{len(detail_ids)}")


def print_batch_claim_result(data: dict, claim_items: List[Dict]):
    """Pretty-print batch claim result."""
    platform_map = data.get("platformCollectBoxDetailIdMap", {})
    
    # Group by platform
    platform_groups: Dict[str, List[Dict]] = {}
    for item in claim_items:
        platform = item["platform"]
        if platform not in platform_groups:
            platform_groups[platform] = []
        platform_groups[platform].append(item)
    
    print(f"\n{'='*80}")
    print(f"批量认领结果  |  总商品数: {len(claim_items)}  |  平台数: {len(platform_groups)}")
    print(f"{'='*80}")

    for platform, items in platform_groups.items():
        print(f"\n--- 平台: {platform} ({len(items)} 个商品) ---")
        
        platform_data = platform_map.get(platform, {})
        if not platform_data:
            print(f"   ⚠️  未返回该平台映射数据")
            continue
        
        print(f"   {'公共采集箱ID':<20} -> {'平台采集箱ID':<20}")
        print(f"   {'-'*20}    {'-'*20}")
        
        for item in items:
            detail_id_str = str(item["detailId"])
            if detail_id_str in platform_data:
                platform_detail_id = platform_data[detail_id_str]
                print(f"   {item['detailId']:<20} -> {platform_detail_id:<20}")
            else:
                print(f"   {item['detailId']:<20} -> (未找到映射)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_claim(client: JCopClient, args):
    """Claim single or multiple products to a platform."""
    # Parse detail IDs
    detail_ids = []
    for id_str in args.detail_ids.split(","):
        id_str = id_str.strip()
        if id_str:
            detail_ids.append(int(id_str))
    
    if not detail_ids:
        print("ERROR: No valid detail IDs provided")
        sys.exit(1)
    
    platform = args.platform
    serial_number = args.serial_number or 1
    
    print(f"Claiming {len(detail_ids)} product(s) to platform '{platform}':")
    print(f"  Detail IDs: {detail_ids}")
    
    result = client.claim_to_platform(
        detail_ids=detail_ids,
        platform=platform,
        serial_number=serial_number
    )
    
    print_claim_result(result["data"], detail_ids, client.normalize_platform(platform))
    return result["data"]


def cmd_batch_claim(client: JCopClient, args):
    """Claim products from a JSON file."""
    if not args.file:
        print("ERROR: --file is required for batch-claim command")
        sys.exit(1)
    
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"ERROR: File not found: {file_path}")
        sys.exit(1)
    
    with open(file_path, "r", encoding="utf-8") as f:
        claim_data = json.load(f)
    
    # Support two formats:
    # 1. { "detailSerialNumberPlatformList": [...] }
    # 2. [ { "detailId": 1, "platform": "tiktok", "serialNumber": 1 }, ... ]
    
    if isinstance(claim_data, dict) and "detailSerialNumberPlatformList" in claim_data:
        claim_items = claim_data["detailSerialNumberPlatformList"]
    elif isinstance(claim_data, list):
        claim_items = claim_data
    else:
        print("ERROR: Invalid JSON format. Expected list or object with 'detailSerialNumberPlatformList'")
        sys.exit(1)
    
    if not claim_items:
        print("ERROR: No claim items found in file")
        sys.exit(1)
    
    print(f"Batch claiming {len(claim_items)} product(s) from file: {file_path}")
    
    # Group by platform for efficient API calls
    platform_groups: Dict[str, List[Dict]] = {}
    for item in claim_items:
        platform = client.normalize_platform(item["platform"])
        if platform not in platform_groups:
            platform_groups[platform] = []
        platform_groups[platform].append(item)
    
    all_results = {"platformCollectBoxDetailIdMap": {}}
    
    for platform, items in platform_groups.items():
        detail_ids = [item["detailId"] for item in items]
        serial_number = items[0].get("serialNumber", 1)
        
        print(f"\nProcessing platform '{platform}' with {len(items)} item(s)...")
        result = client.claim_to_platform(
            detail_ids=detail_ids,
            platform=platform,
            serial_number=serial_number
        )
        
        # Merge results
        if "platformCollectBoxDetailIdMap" in result.get("data", {}):
            all_results["platformCollectBoxDetailIdMap"].update(
                result["data"]["platformCollectBoxDetailIdMap"]
            )
    
    print_batch_claim_result(all_results, claim_items)
    return all_results


def print_detail(data: dict, detail_id: int):
    """Pretty-print common collect box product detail."""
    edit_data = data.get("data", {}).get("editCommonCollectBoxDetail", {})
    sku_map = edit_data.get("skuMap", {})

    title = edit_data.get("title", "-")
    spu_price = edit_data.get("price", "-")
    stock = edit_data.get("stock", "-")
    source = edit_data.get("source", "-")
    item_num = edit_data.get("itemNum", "-")
    sku_count = len(sku_map)
    sku_prices = [float(s.get("price", 0)) for s in sku_map.values() if s.get("price")]
    min_price = min(sku_prices) if sku_prices else 0
    max_price = max(sku_prices) if sku_prices else 0

    print(f"\n{'='*60}")
    print(f"公共采集箱 - 商品详情  |  ID: {detail_id}")
    print(f"{'='*60}")
    print(f"  标题: {title[:50]}{'...' if len(title) > 50 else ''}")
    print(f"  货号: {item_num}")
    print(f"  SPU价格: {spu_price}")
    print(f"  SKU价格范围: {'¥{:.2f} ~ ¥{:.2f}'.format(min_price, max_price) if sku_count > 1 else '¥{:.2f}'.format(min_price)}")
    print(f"  SKU数量: {sku_count}")
    print(f"  SPU库存: {stock}")
    print(f"  来源: {source}")
    print(f"{'='*60}")


def cmd_detail(client: JCopClient, args):
    """Get common collect box product detail."""
    detail_id = int(args.detail_id)
    result = client.get_common_collect_detail(detail_id)

    if result.get("result") == "success":
        print_detail(result, detail_id)
    return result


def cmd_list_platforms(client: JCopClient, args):
    """List supported platforms."""
    print("\n支持的平台列表:")
    print(f"{'平台代码':<15} {'标准名称':<15}")
    print("-" * 30)
    
    seen = set()
    for code, standard in client.PLATFORM_MAP.items():
        if standard not in seen:
            print(f"{code:<15} {standard:<15}")
            seen.add(standard)
    
    print("\n使用说明:")
    print("  --platform 参数接受平台代码或标准名称")
    print("  例如: --platform tiktok 或 --platform tt")


def main():
    parser = argparse.ArgumentParser(
        description="Miaoshou ERP - Claim Products to Platform Collect Box",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 认领单个商品到 TikTok 采集箱
  %(prog)s claim --detail-ids 12345 --platform tiktok

  # 批量认领多个商品
  %(prog)s claim --detail-ids 12345,12346,12347 --platform tiktok

  # 使用简写平台代码
  %(prog)s claim --detail-ids 12345 --platform tt

  # 从 JSON 文件批量认领（支持多平台）
  %(prog)s batch-claim --file claim_list.json

  # 查看支持的平台列表
  %(prog)s platforms

JSON 文件格式 (claim_list.json):
  [
    {"detailId": 12345, "platform": "tiktok", "serialNumber": 1},
    {"detailId": 12346, "platform": "tiktok", "serialNumber": 1},
    {"detailId": 12347, "platform": "shopee", "serialNumber": 1}
  ]
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # claim sub-command
    claim_parser = subparsers.add_parser("claim", help="Claim products to platform collect box")
    claim_parser.add_argument("--detail-ids", type=str, required=True,
                              help="Comma-separated list of common collect box detail IDs")
    claim_parser.add_argument("--platform", type=str, required=True,
                              help="Target platform (tiktok, shopee, lazada, amazon, ozon, temu, shein)")
    claim_parser.add_argument("--serial-number", type=int, default=1,
                              help="Internal API compatibility field (default: 1). Usually omit this option.")

    # batch-claim sub-command
    batch_parser = subparsers.add_parser("batch-claim", help="Batch claim from JSON file")
    batch_parser.add_argument("--file", type=str, required=True,
                              help="Path to JSON file with claim list")

    # platforms sub-command
    subparsers.add_parser("platforms", help="List supported platforms")

    # detail sub-command
    detail_parser = subparsers.add_parser("detail", help="Get product detail from common collect box")
    detail_parser.add_argument("--detail-id", type=int, required=True,
                              help="Common collect box detail ID")

    # --raw flag (global)
    parser.add_argument("--raw", action="store_true",
                        help="Output raw JSON instead of formatted text")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    config = load_config()
    client = JCopClient(config)

    if args.command == "claim":
        data = cmd_claim(client, args)
    elif args.command == "batch-claim":
        data = cmd_batch_claim(client, args)
    elif args.command == "detail":
        data = cmd_detail(client, args)
    elif args.command == "platforms":
        cmd_list_platforms(client, args)
        sys.exit(0)
    else:
        parser.print_help()
        sys.exit(0)

    if args.raw and args.command != "platforms":
        print("\n--- RAW JSON ---")
        print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

