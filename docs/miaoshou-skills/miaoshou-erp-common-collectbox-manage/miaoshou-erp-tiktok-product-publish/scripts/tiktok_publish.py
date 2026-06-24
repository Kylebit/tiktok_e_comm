"""
Miaoshou ERP (JCOP Open Platform) - TikTok Product Publish Tool

This tool helps publish products from TikTok collect box to TikTok shops.
It handles:
- TikTok shop list query
- Collect box product listing (with pre-claimed shop info)
- Pre-publish shop claiming
- Category and attribute management
- Warehouse selection
- Responsible person (EU) selection
- Final product publishing

Usage:
    # 发布流程
    python tiktok_publish.py shops                           # 查看TikTok店铺列表
    python tiktok_publish.py list-products                    # 查看待发布商品
    python tiktok_publish.py claim --detail-ids ID --shop-ids SHOP    # 认领到店铺
    python tiktok_publish.py publish --detail-ids ID --shop-ids SHOP # 发布商品

    # 发布前准备
    python tiktok_publish.py categories --site US             # 查询类目树
    python tiktok_publish.py attributes --site US --cid ID --shop-ids SHOP  # 获取类目属性要求
    python tiktok_publish.py warehouses --shop-ids SHOP        # 查询店铺仓库
    python tiktok_publish.py responsible-persons --shop-id SHOP # 查询欧盟责任人
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

class TikTokPublishClient:
    """Client for TikTok product publishing APIs."""

    SHOP_LIST_PATH = "/open/v1/product/shop/shop/get_shop_list"
    COLLECT_BOX_BASE = "/open/v1/product/collect_box/tiktok/collect_box/"

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
        result = resp.json()

        if result.get("result") != "success":
            print(f"\nAPI Error: [{result.get('code')}] {result.get('message')}")
            return result  # Return error response instead of exit

        return result

    # -----------------------------------------------------------------------
    # Category APIs
    # -----------------------------------------------------------------------

    def get_category_tree(self, site: str) -> dict:
        """Get category tree for a site."""
        path = "/open/v1/product/collect_box/tiktok/collect_box/get_category_tree_by_site"
        body = {"site": site}
        return self._post(path, body)

    def get_category_metadata(self, site: str, cid: int, shop_ids: List[int]) -> dict:
        """Get category metadata (attributes, requirements)."""
        path = "/open/v1/product/collect_box/tiktok/collect_box/get_category_metadata"
        body = {
            "site": site,
            "cid": cid,
            "shopIds": shop_ids
        }
        return self._post(path, body)

    # -----------------------------------------------------------------------
    # Warehouse APIs
    # -----------------------------------------------------------------------

    def get_shop_warehouse_list(self, shop_ids: List[int]) -> dict:
        """Get warehouse list for shops."""
        path = "/open/v1/product/collect_box/tiktok/collect_box/get_shop_warehouse_list"
        body = {"shopIds": shop_ids}
        return self._post(path, body)

    # -----------------------------------------------------------------------
    # Responsible Person (EU) APIs
    # -----------------------------------------------------------------------

    def get_responsible_person_list(self, shop_id: int, refresh: int = 0) -> dict:
        """Get EU responsible person list for a shop."""
        path = "/open/v1/product/collect_box/tiktok/collect_box/get_responsible_person_list"
        body = {
            "shopId": shop_id,
            "refresh": refresh
        }
        return self._post(path, body)

    # -----------------------------------------------------------------------
    # Shop List API
    # -----------------------------------------------------------------------

    def get_shop_list(
        self,
        platform: str = "tiktok",
        site: Optional[str] = None,
        page_no: int = 1,
        page_size: int = 50,
    ) -> dict:
        """
        获取店铺数据列表。

        Args:
            platform: 平台（默认 tiktok）
            site: 站点过滤（可选，如 MY/SG/PH 等；None 表示全部站点）
            page_no: 页码（默认1）
            page_size: 每页条数（默认50）
        """
        path = self.SHOP_LIST_PATH
        body = {
            "platform": platform,
            "pageNo": page_no,
            "pageSize": page_size,
        }
        if site:
            body["site"] = site
        return self._post(path, body)

    # -----------------------------------------------------------------------
    # Collect Box List API
    # -----------------------------------------------------------------------

    def search_list(
        self,
        page_no: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
        keyword: str = "",
    ) -> dict:
        """
        分页获取采集箱商品列表。

        Args:
            page_no: 页码（默认1）
            page_size: 每页条数（默认20）
            status: 状态过滤（notPublished/timingPublish/published）
            keyword: 商品标题关键词搜索
        """
        path = self.COLLECT_BOX_BASE + "search_collect_box_detail_list"
        body = {
            "pageNo": page_no,
            "pageSize": page_size,
            "filter": {
                "sourceItemIdKeyword": keyword,
            },
        }
        if status:
            body["filter"]["status"] = status
        return self._post(path, body)

    # -----------------------------------------------------------------------
    # Publishing APIs
    # -----------------------------------------------------------------------

    def claim_to_shop(self, detail_ids: List[int], shop_ids: List[int]) -> dict:
        """认领商品到预发布店铺。"""
        path = self.COLLECT_BOX_BASE + "claim_to_shop"
        body = {
            "detailIds": detail_ids,
            "shopIds": shop_ids
        }
        return self._post(path, body)

    def publish_products(self, detail_ids: List[int], shop_ids: List[int]) -> dict:
        """发布商品到店铺。"""
        path = self.COLLECT_BOX_BASE + "save_move_collect_task"
        body = {
            "detailIds": detail_ids,
            "shopIds": shop_ids
        }
        return self._post(path, body)


# ---------------------------------------------------------------------------
# Output Helpers
# ---------------------------------------------------------------------------

def print_category_tree(data: dict, site: str):
    """Pretty-print category tree."""
    cate_tree = data.get("cateTree", {})
    
    print(f"\n{'='*80}")
    print(f"类目列表  |  站点: {site}")
    print(f"{'='*80}")

    if not cate_tree:
        print("⚠️  未获取到类目数据")
        return

    def print_category(cid: str, node: dict, level: int = 0):
        indent = "  " * level
        name = node.get("name", "-")
        name_cn = node.get("nameChinese", "-")
        is_last = node.get("isLastLevel", "false")
        disabled = node.get("disabled", False)
        
        status = ""
        if disabled:
            status = " [禁用]"
        elif is_last.lower() == "true":
            status = " [末级]"
        
        print(f"{indent}[{cid}] {name_cn} ({name}){status}")
        
        # Print children
        children = node.get("children", {})
        for child_cid, child_node in sorted(children.items(), key=lambda x: int(x[0])):
            print_category(child_cid, child_node, level + 1)

    # Print root categories
    for cid, node in sorted(cate_tree.items(), key=lambda x: int(x[0])):
        if node.get("fid") == 0 or node.get("aid") == int(cid):
            print_category(cid, node, 0)
            print()


def print_category_metadata(data: dict, cid: int):
    """Pretty-print category metadata."""
    metadata = data.get("categoryMetadata", {})
    config = metadata.get("categoryConfig", {})
    sale_attrs = metadata.get("categorySaleAttrList", [])
    product_attrs = metadata.get("categoryProductAttrList", [])

    print(f"\n{'='*80}")
    print(f"类目属性信息  |  类目ID: {cid}")
    print(f"{'='*80}")

    # Print config
    print("\n📋 类目配置:")
    print(f"  尺码表支持: {config.get('sizeChartIsSupported', '-')}")
    print(f"  尺码表必填: {config.get('sizeChartIsRequired', '-')}")
    print(f"  货到付款支持: {config.get('codIsSupported', '-')}")
    print(f"  包装尺寸必填: {config.get('packageDimensionIsRequired', '-')}")
    print(f"  EPR必填: {config.get('eprIsRequired', '-')}")
    print(f"  责任人必填: {config.get('responsiblePersonIsRequired', '-')}")
    print(f"  制造商必填: {config.get('manufacturerIsRequired', '-')}")

    # Print certifications
    certs = config.get("productCertifications", [])
    if certs:
        print(f"\n📜 商品认证要求 ({len(certs)} 项):")
        for cert in certs:
            required = "必填" if cert.get("isRequired") else "选填"
            mandatory = "强制" if cert.get("isMandatory") else "非强制"
            print(f"  [{cert.get('id', '-')}] {cert.get('name', '-')} ({required}, {mandatory})")

    # Print sale attributes
    if sale_attrs:
        print(f"\n🏷️  销售属性 ({len(sale_attrs)} 项):")
        for attr in sale_attrs:
            mandatory = "必填" if attr.get("isMandatory") else "选填"
            multi = "多选" if attr.get("isMultipleSelected") else "单选"
            custom = "可自定义" if attr.get("isCustomized") else "不可自定义"
            print(f"  [{attr.get('attrId', '-')}] {attr.get('attributeNameAlias', attr.get('name', '-'))} ({mandatory}, {multi}, {custom})")
            values = attr.get("values", [])
            if values:
                value_str = ", ".join([f"{v.get('valueNameAlias', v.get('name', '-'))}" for v in values[:5]])
                if len(values) > 5:
                    value_str += f" ... 等{len(values)}个选项"
                print(f"      选项: {value_str}")

    # Print product attributes
    if product_attrs:
        print(f"\n📦 商品属性 ({len(product_attrs)} 项):")
        for attr in product_attrs:
            mandatory = "必填" if attr.get("isMandatory") else "选填"
            multi = "多选" if attr.get("isMultipleSelected") else "单选"
            print(f"  [{attr.get('attrId', '-')}] {attr.get('attributeNameAlias', attr.get('name', '-'))} ({mandatory}, {multi})")


def print_warehouse_list(data: dict):
    """Pretty-print shop warehouse list."""
    shop_list = data.get("shopWarehouseList", [])

    print(f"\n{'='*80}")
    print(f"店铺仓库列表")
    print(f"{'='*80}")

    if not shop_list:
        print("⚠️  未获取到店铺仓库数据")
        return

    for shop in shop_list:
        print(f"\n🏪 [{shop.get('shopId', '-')}] {shop.get('shopName', '-')}")
        print(f"   平台: {shop.get('platform', '-')} | 站点: {shop.get('site', '-')}")
        
        warehouses = shop.get("warehouseList", [])
        if warehouses:
            print(f"   仓库 ({len(warehouses)} 个):")
            for wh in warehouses:
                default_mark = " [默认]" if wh.get("isDefault") == "1" else ""
                print(f"      [{wh.get('warehouseId', '-')}] {wh.get('warehouseName', '-')}{default_mark}")
                print(f"         类型: {wh.get('warehouseSubType', '-')} | 状态: {wh.get('warehouseEffectStatus', '-')}")


def print_responsible_person_list(data: dict, shop_id: int):
    """Pretty-print responsible person list."""
    person_list = data.get("responsiblePersonList", [])

    print(f"\n{'='*80}")
    print(f"欧盟责任人列表  |  店铺ID: {shop_id}")
    print(f"{'='*80}")

    if not person_list:
        print("⚠️  未获取到责任人数据")
        return

    print(f"共 {len(person_list)} 个责任人:\n")
    for person in person_list:
        print(f"  [{person.get('id', '-')}] {person.get('name', '-')}")


def print_claim_result(data: dict, detail_ids: List[int], shop_ids: List[int]):
    """Pretty-print claim result."""
    print(f"\n{'='*80}")
    print(f"认领预发布店铺结果")
    print(f"{'='*80}")
    print(f"商品ID: {detail_ids}")
    print(f"店铺ID: {shop_ids}")
    print(f"\n✅ 认领成功")


def print_publish_result(data: dict, detail_ids: List[int], shop_ids: List[int]):
    """Pretty-print publish result."""
    print(f"\n{'='*80}")
    print(f"发布商品结果")
    print(f"{'='*80}")
    print(f"商品ID: {detail_ids}")
    print(f"店铺ID: {shop_ids}")

    result = data.get("result", "fail")
    code = data.get("code", "-")
    message = data.get("message", "")

    if result == "success":
        print(f"\n✅ 发布成功")
    else:
        print(f"\n❌ 发布失败")
        print(f"   错误码: {code}")
        if message:
            print(f"   错误信息: {message}")


def print_shop_list(data: dict):
    """Pretty-print TikTok shop list."""
    shop_list = data.get("data", {}).get("shopList", [])

    print(f"\n{'='*80}")
    print(f"TikTok 店铺列表  |  共 {len(shop_list)} 个店铺")
    print(f"{'='*80}")

    if not shop_list:
        print("（无数据）")
        return

    print(f"{'#':<4} {'店铺ID':<10} {'站点':<6} {'站点名称':<12} {'店铺名称':<20} {'跨境':<6} {'状态'}")
    print("-" * 80)

    for i, shop in enumerate(shop_list, start=1):
        shop_id = shop.get("shopId", "-")
        site = shop.get("site", "-")
        site_name = shop.get("siteName", "-")
        shop_nick = (shop.get("shopNick") or "")[:19]
        is_cb = "是" if shop.get("isCb") == 1 else "否"
        status = shop.get("status", "-")
        print(f"{i:<4} {str(shop_id):<10} {site:<6} {site_name:<12} {shop_nick:<20} {is_cb:<6} {status}")

    print(f"{'='*80}")


def print_product_list(data: dict, page_no: int, page_size: int, status_filter: str = ""):
    """Pretty-print product list with shop info."""
    items = data.get("data", {}).get("list", [])
    total = data.get("data", {}).get("total", 0)
    total_pages = (total + page_size - 1) // page_size if page_size else 0

    status_label = {
        "notPublished": "未发布",
        "timingPublish": "定时发布",
        "published": "已发布",
    }.get(status_filter, status_filter or "全部")

    print(f"\n{'='*100}")
    print(f"TikTok采集箱 - 待发布商品  |  第 {page_no}/{total_pages} 页  |  共 {total} 个  |  状态: {status_label}")
    print(f"{'='*100}")

    if not items:
        print("（无数据）")
        return

    print(f"{'#':<4} {'ID':<12} {'标题':<30} {'价格':<8} {'库存':<8} {'预认领店铺'}")
    print("-" * 100)

    for i, item in enumerate(items, start=(page_no - 1) * page_size + 1):
        detail_id = item.get("collectBoxDetailId", "-")
        title = (item.get("title") or "")[:28]
        price = item.get("price", "-")
        stock = item.get("stock", "-")

        # 预认领店铺
        shop_list = item.get("collectBoxDetailShopList", [])
        if shop_list:
            shop_ids = [str(s.get("shopId", "")) for s in shop_list]
            shop_str = ", ".join(shop_ids)
        else:
            shop_str = "（未认领）"

        print(f"{i:<4} {str(detail_id):<12} {title:<30} {str(price):<8} {str(stock):<8} {shop_str}")

    print(f"{'='*100}")


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------

def cmd_shops(client: TikTokPublishClient, args):
    """List TikTok shops."""
    site = args.site.upper() if args.site else None
    result = client.get_shop_list(
        platform="tiktok",
        site=site,
        page_no=args.page,
        page_size=args.page_size,
    )

    if result.get("result") == "success":
        print_shop_list(result)
    else:
        print(f"Error: {result.get('message', 'Unknown error')}")

    return result.get("data", {})


def cmd_list_products(client: TikTokPublishClient, args):
    """List products in TikTok collect box for publish review."""
    status_map = {
        "not_published": "notPublished",
        "timing_publish": "timingPublish",
        "published": "published",
    }
    status = status_map.get(args.status) if args.status else None

    result = client.search_list(
        page_no=args.page,
        page_size=args.page_size,
        status=status,
        keyword=args.keyword or "",
    )

    if result.get("result") == "success":
        print_product_list(result, args.page, args.page_size, args.status or "")
    else:
        print(f"Error: {result.get('message', 'Unknown error')}")

    return result.get("data", {})


def cmd_categories(client: TikTokPublishClient, args):
    """Get category tree for a site."""
    site = args.site.upper()
    print(f"Getting category tree for site: {site}")
    result = client.get_category_tree(site)

    if result.get("result") == "success":
        print_category_tree(result.get("data", {}), site)
    else:
        print(f"Error: {result.get('message', 'Unknown error')}")

    return result.get("data", {})


def cmd_attributes(client: TikTokPublishClient, args):
    """Get category metadata."""
    site = args.site.upper()
    cid = args.cid
    shop_ids = [int(s.strip()) for s in args.shop_ids.split(",") if s.strip()]
    
    print(f"Getting category metadata: site={site}, cid={cid}, shops={shop_ids}")
    result = client.get_category_metadata(site, cid, shop_ids)
    
    if result.get("result") == "success":
        print_category_metadata(result.get("data", {}), cid)
    else:
        print(f"Error: {result.get('message', 'Unknown error')}")
    
    return result.get("data", {})


def cmd_warehouses(client: TikTokPublishClient, args):
    """Get shop warehouse list."""
    shop_ids = [int(s.strip()) for s in args.shop_ids.split(",") if s.strip()]
    
    print(f"Getting warehouse list for shops: {shop_ids}")
    result = client.get_shop_warehouse_list(shop_ids)
    
    if result.get("result") == "success":
        print_warehouse_list(result.get("data", {}))
    else:
        print(f"Error: {result.get('message', 'Unknown error')}")
    
    return result.get("data", {})


def cmd_responsible_persons(client: TikTokPublishClient, args):
    """Get responsible person list."""
    shop_id = args.shop_id
    refresh = 1 if args.refresh else 0
    
    print(f"Getting responsible person list for shop: {shop_id}")
    result = client.get_responsible_person_list(shop_id, refresh)
    
    if result.get("result") == "success":
        print_responsible_person_list(result.get("data", {}), shop_id)
    else:
        print(f"Error: {result.get('message', 'Unknown error')}")
    
    return result.get("data", {})


def cmd_claim(client: TikTokPublishClient, args):
    """Claim products to pre-publish shops."""
    detail_ids = [int(s.strip()) for s in args.detail_ids.split(",") if s.strip()]
    shop_ids = [int(s.strip()) for s in args.shop_ids.split(",") if s.strip()]
    
    print(f"Claiming products to shops: products={detail_ids}, shops={shop_ids}")
    result = client.claim_to_shop(detail_ids, shop_ids)
    
    if result.get("result") == "success":
        print_claim_result(result.get("data", {}), detail_ids, shop_ids)
    else:
        print(f"Error: {result.get('message', 'Unknown error')}")
    
    return result.get("data", {})


def cmd_publish(client: TikTokPublishClient, args):
    """Publish products to shops."""
    detail_ids = [int(s.strip()) for s in args.detail_ids.split(",") if s.strip()]
    shop_ids = [int(s.strip()) for s in args.shop_ids.split(",") if s.strip()]
    
    print(f"Publishing products to shops: products={detail_ids}, shops={shop_ids}")
    result = client.publish_products(detail_ids, shop_ids)
    
    print_publish_result(result, detail_ids, shop_ids)
    return result.get("data", {})


def cmd_check(client: TikTokPublishClient, args):
    """Check product publish requirements."""
    detail_id = args.detail_id
    
    print(f"Checking publish requirements for product: {detail_id}")
    print("\n⚠️  此功能需要结合商品详情API和类目属性API来实现完整检查")
    print("建议流程:")
    print("  1. 获取商品详情，确认当前类目和属性")
    print("  2. 获取该类目的属性要求")
    print("  3. 对比检查缺失信息")
    print("\n使用以下命令获取相关信息:")
    print(f"  python tiktok_publish.py categories --site <站点>")
    print(f"  python tiktok_publish.py attributes --site <站点> --cid <类目ID> --shop-ids <店铺ID>")


def cmd_workflow(client: TikTokPublishClient, args):
    """Interactive workflow for publishing."""
    print("\n" + "="*80)
    print("TikTok 商品发布工作流")
    print("="*80)
    
    print("\n📋 完整发布流程:")
    print("\n1️⃣  查询类目结构")
    print("   python tiktok_publish.py categories --site US")
    
    print("\n2️⃣  获取类目属性要求")
    print("   python tiktok_publish.py attributes --site US --cid <类目ID> --shop-ids <店铺ID>")
    
    print("\n3️⃣  查询店铺仓库")
    print("   python tiktok_publish.py warehouses --shop-ids <店铺ID>")
    
    print("\n4️⃣  查询欧盟责任人")
    print("   python tiktok_publish.py responsible-persons --shop-id <店铺ID>")
    
    print("\n5️⃣  认领预发布店铺")
    print("   python tiktok_publish.py claim --detail-ids <商品ID> --shop-ids <店铺ID>")
    
    print("\n6️⃣  发布商品")
    print("   python tiktok_publish.py publish --detail-ids <商品ID> --shop-ids <店铺ID>")
    
    print("\n" + "="*80)
    print("提示: 在妙手ERP网页端完成商品信息补充后，再执行第5-6步")
    print("="*80)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Miaoshou ERP - TikTok Product Publish Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 获取TikTok店铺列表
  python tiktok_publish.py shops
  python tiktok_publish.py shops --site MY

  # 列出待发布商品（未发布状态）
  python tiktok_publish.py list-products
  python tiktok_publish.py list-products --page 1 --page-size 20

  # 查询站点类目
  python tiktok_publish.py categories --site US

  # 获取类目属性
  python tiktok_publish.py attributes --site US --cid 12345 --shop-ids 10001

  # 查询店铺仓库
  python tiktok_publish.py warehouses --shop-ids 10001,10002

  # 查询欧盟责任人
  python tiktok_publish.py responsible-persons --shop-id 10001

  # 认领预发布店铺
  python tiktok_publish.py claim --detail-ids 12345 --shop-ids 10001

  # 发布商品
  python tiktok_publish.py publish --detail-ids 12345 --shop-ids 10001

  # 查看完整工作流
  python tiktok_publish.py workflow
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ---- shops ----
    shops_parser = subparsers.add_parser("shops", help="获取TikTok店铺列表")
    shops_parser.add_argument("--site", type=str, help="站点过滤（可选，如 MY/SG/PH 等）")
    shops_parser.add_argument("--page", type=int, default=1, help="页码（默认1）")
    shops_parser.add_argument("--page-size", type=int, default=50, help="每页条数（默认50）")

    # ---- list-products ----
    list_parser = subparsers.add_parser("list-products", help="列出待发布商品（含预认领店铺）")
    list_parser.add_argument("--page", type=int, default=1, help="页码（默认1）")
    list_parser.add_argument("--page-size", type=int, default=20, help="每页条数（默认20）")
    list_parser.add_argument(
        "--status", type=str,
        choices=["not_published", "timing_publish", "published"],
        help="状态过滤（默认显示未发布）"
    )
    list_parser.add_argument("--keyword", type=str, help="商品标题关键词搜索")

    # categories
    cat_parser = subparsers.add_parser("categories", help="Get category tree for a site")
    cat_parser.add_argument("--site", type=str, required=True,
                            help="Site code (e.g., US, UK, SG)")

    # attributes
    attr_parser = subparsers.add_parser("attributes", help="Get category metadata")
    attr_parser.add_argument("--site", type=str, required=True,
                             help="Site code (e.g., US, UK, SG)")
    attr_parser.add_argument("--cid", type=int, required=True,
                             help="Category ID")
    attr_parser.add_argument("--shop-ids", type=str, required=True,
                             help="Comma-separated shop IDs")

    # warehouses
    wh_parser = subparsers.add_parser("warehouses", help="Get shop warehouse list")
    wh_parser.add_argument("--shop-ids", type=str, required=True,
                           help="Comma-separated shop IDs")

    # responsible-persons
    rp_parser = subparsers.add_parser("responsible-persons", help="Get EU responsible person list")
    rp_parser.add_argument("--shop-id", type=int, required=True,
                           help="Shop ID")
    rp_parser.add_argument("--refresh", action="store_true",
                           help="Refresh data from platform")

    # claim
    claim_parser = subparsers.add_parser("claim", help="Claim products to pre-publish shops")
    claim_parser.add_argument("--detail-ids", type=str, required=True,
                              help="Comma-separated collect box detail IDs")
    claim_parser.add_argument("--shop-ids", type=str, required=True,
                              help="Comma-separated shop IDs")

    # publish
    pub_parser = subparsers.add_parser("publish", help="Publish products to shops")
    pub_parser.add_argument("--detail-ids", type=str, required=True,
                            help="Comma-separated collect box detail IDs")
    pub_parser.add_argument("--shop-ids", type=str, required=True,
                            help="Comma-separated shop IDs")

    # check
    check_parser = subparsers.add_parser("check", help="Check product publish requirements")
    check_parser.add_argument("--detail-id", type=int, required=True,
                              help="Collect box detail ID")

    # workflow
    subparsers.add_parser("workflow", help="Show complete publishing workflow")

    # --raw flag (global)
    parser.add_argument("--raw", action="store_true",
                        help="Output raw JSON instead of formatted text")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    config = load_config()
    client = TikTokPublishClient(config)

    command_map = {
        "shops": cmd_shops,
        "list-products": cmd_list_products,
        "categories": cmd_categories,
        "attributes": cmd_attributes,
        "warehouses": cmd_warehouses,
        "responsible-persons": cmd_responsible_persons,
        "claim": cmd_claim,
        "publish": cmd_publish,
        "check": cmd_check,
        "workflow": cmd_workflow,
    }

    if args.command in command_map:
        data = command_map[args.command](client, args)
        
        if args.raw and args.command != "workflow":
            print("\n--- RAW JSON ---")
            print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()

