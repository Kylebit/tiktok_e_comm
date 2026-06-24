"""
Miaoshou ERP (JCOP Open Platform) - TikTok采集箱 CRUD Skill

Usage:
    # 列表查询
    python tiktok_collectbox.py list

    # 商品详情（店铺模式）
    python tiktok_collectbox.py detail 12345 --shop-id 1001

    # 商品详情（站点模式）
    python tiktok_collectbox.py detail 12345 --site MY

    # 诊断四个维度
    python tiktok_collectbox.py diagnose 12345 --shop-id 1001

    # 保存（店铺模式）
    python tiktok_collectbox.py save 12345 --shop-id 1001 --oss-md5 abc123 --file edit.json

    # 保存（站点模式）
    python tiktok_collectbox.py save 12345 --site MY --oss-md5 abc123 --file edit.json

    # 认领到预发布店铺
    python tiktok_collectbox.py claim --detail-ids 12345 --shop-ids 1001

    # 获取仓库列表
    python tiktok_collectbox.py warehouses --shop-ids 1001

    # 获取制造商列表
    python tiktok_collectbox.py manufacturers --shop-id 1001

    # 获取责任人列表
    python tiktok_collectbox.py responsible-persons --shop-id 1001
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

class TikTokCollectBoxClient:
    """
    TikTok采集箱 API 客户端。

    Base path:
        /open/v1/product/collect_box/tiktok/collect_box/

    支持店铺模式和站点模式：
    - 店铺模式：get_shop_collect_item_info / save_shop_collect_item_info
    - 站点模式：get_site_collect_item_info / save_site_collect_item_info
    """

    BASE_PATH = "/open/v1/product/collect_box/tiktok/collect_box/"

    SITE_MAP = {
        "my": "MY", "MY": "MY",
        "sg": "SG", "SG": "SG",
        "ph": "PH", "PH": "PH",
        "th": "TH", "TH": "TH",
        "vn": "VN", "VN": "VN",
        "id": "ID", "ID": "ID",
    }

    def __init__(self, config: dict):
        self.base_url = config["base_url"].rstrip("/")
        self.app_key = config["app_key"]
        self.app_secret = config["app_secret"]
        self.timeout = config.get("timeout", 30)

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

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

        resp = requests.post(url, headers=headers, data=body_json.encode("utf-8"), timeout=self.timeout)
        resp.raise_for_status()

        # Handle empty response (upstream server unavailable)
        if not resp.text or not resp.text.strip():
            print(f"\nAPI Error: 服务器返回空响应（HTTP {resp.status_code}）")
            print(f"可能原因：VPN已断开 / JCOP平台服务不可用 / IP不在白名单")
            sys.exit(1)

        result = resp.json()

        if result.get("result") != "success" and result.get("code") not in ("0", 0):
            print(f"\nAPI Error: [{result.get('code')}] {result.get('message', '')}")
            sys.exit(1)

        return result

    def _full_path(self, endpoint: str) -> str:
        """Build full API path."""
        return self.BASE_PATH + endpoint

    # ------------------------------------------------------------------
    # API 1: 列表查询
    # ------------------------------------------------------------------

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
        path = self._full_path("search_collect_box_detail_list")
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

    # ------------------------------------------------------------------
    # API 2: 店铺模式详情
    # ------------------------------------------------------------------

    def get_shop_detail(self, detail_id: int, shop_id: int) -> dict:
        """
        获取商品店铺模式详情。

        Args:
            detail_id: 采集箱详情ID
            shop_id: 店铺ID
        Returns:
            包含 ossMd5 和 shopCollectItemInfo 的响应
        """
        path = self._full_path("get_shop_collect_item_info")
        body = {"detailId": detail_id, "shopId": shop_id}
        return self._post(path, body)

    # ------------------------------------------------------------------
    # API 3: 保存店铺模式
    # ------------------------------------------------------------------

    def save_shop(
        self,
        detail_id: int,
        shop_id: int,
        oss_md5: str,
        shop_item_info: dict,
    ) -> dict:
        """
        保存商品店铺模式。

        Args:
            detail_id: 采集箱详情ID
            shop_id: 店铺ID
            oss_md5: 详情接口返回的 ossMd5（防并发冲突）
            shop_item_info: shopCollectItemInfo 完整数据
        """
        path = self._full_path("save_shop_collect_item_info")
        body = {
            "ossMd5": oss_md5,
            "detailId": detail_id,
            "shopId": shop_id,
            "shopCollectItemInfo": shop_item_info,
        }
        return self._post(path, body)

    # ------------------------------------------------------------------
    # API 4: 站点模式详情
    # ------------------------------------------------------------------

    def get_site_detail(self, detail_id: int, site: str) -> dict:
        """
        获取商品站点模式详情。

        Args:
            detail_id: 采集箱详情ID
            site: 站点代码（MY/SG/PH/TH/VN/ID）
        Returns:
            包含 ossMd5 和 siteCollectItemInfo 的响应
        """
        path = self._full_path("get_site_collect_item_info")
        body = {"detailId": detail_id, "site": site}
        return self._post(path, body)

    # ------------------------------------------------------------------
    # API 5: 保存站点模式
    # ------------------------------------------------------------------

    def save_site(
        self,
        detail_id: int,
        site: str,
        oss_md5: str,
        site_item_info: dict,
    ) -> dict:
        """
        保存商品站点模式。

        Args:
            detail_id: 采集箱详情ID
            site: 站点代码（MY/SG/PH/TH/VN/ID）
            oss_md5: 详情接口返回的 ossMd5（防并发冲突）
            site_item_info: siteCollectItemInfo 完整数据
        """
        path = self._full_path("save_site_collect_item_info")
        body = {
            "ossMd5": oss_md5,
            "site": site,
            "detailId": detail_id,
            "siteCollectItemInfo": site_item_info,
        }
        return self._post(path, body)

    # ------------------------------------------------------------------
    # API 6: 认领到预发布店铺
    # ------------------------------------------------------------------

    def claim_to_shop(self, detail_ids: List[int], shop_ids: List[int]) -> dict:
        """
        将商品认领到指定店铺。

        Args:
            detail_ids: 采集箱详情ID列表
            shop_ids: 目标店铺ID列表
        """
        path = self._full_path("claim_to_shop")
        body = {"shopIds": shop_ids, "detailIds": detail_ids}
        return self._post(path, body)

    # ------------------------------------------------------------------
    # API 7: 店铺仓库列表
    # ------------------------------------------------------------------

    def get_shop_warehouse_list(self, shop_ids: List[int]) -> dict:
        """
        获取店铺仓库列表（用于填写物流库存信息）。

        Args:
            shop_ids: 店铺ID列表
        """
        path = self._full_path("get_shop_warehouse_list")
        body = {"shopIds": shop_ids}
        return self._post(path, body)

    # ------------------------------------------------------------------
    # API 8: 制造商列表
    # ------------------------------------------------------------------

    def get_manufacturer_list(self, shop_id: int, refresh: int = 0) -> dict:
        """
        获取制造商列表（欧盟合规用）。

        Args:
            shop_id: 店铺ID
            refresh: 0=缓存数据，1=重新刷新
        """
        path = self._full_path("get_manufacturer_list")
        body = {"shopId": shop_id, "refresh": refresh}
        return self._post(path, body)

    # ------------------------------------------------------------------
    # API 9: 责任人列表
    # ------------------------------------------------------------------

    def get_responsible_person_list(self, shop_id: int, refresh: int = 0) -> dict:
        """
        获取欧盟责任人列表。

        Args:
            shop_id: 店铺ID
            refresh: 0=缓存数据，1=重新刷新
        """
        path = self._full_path("get_responsible_person_list")
        body = {"shopId": shop_id, "refresh": refresh}
        return self._post(path, body)


# ---------------------------------------------------------------------------
# Output Helpers
# ---------------------------------------------------------------------------

def print_list(data: dict, page_no: int, page_size: int):
    """Pretty-print product list."""
    items = data.get("data", {}).get("list", [])
    total = data.get("data", {}).get("total", 0)
    total_pages = (total + page_size - 1) // page_size if page_size else 0

    print(f"\n{'='*80}")
    print(f"TikTok采集箱 - 商品列表  |  第 {page_no}/{total_pages} 页  |  共 {total} 个商品")
    print(f"{'='*80}")

    if not items:
        print("（无数据）")
        return

    print(f"{'#':<4} {'ID':<12} {'标题':<35} {'价格':<8} {'库存':<8} {'状态':<12} {'编辑模式'}")
    print("-" * 100)

    for i, item in enumerate(items, start=(page_no - 1) * page_size + 1):
        title = (item.get("title") or "")[:33]
        price = item.get("price", "-")
        stock = item.get("stock", "-")
        status = item.get("status", "-")
        edit_model = item.get("editModel", "-")
        detail_id = item.get("collectBoxDetailId", "-")
        print(f"{i:<4} {str(detail_id):<12} {title:<35} {str(price):<8} {str(stock):<8} {status:<12} {edit_model}")

    print(f"{'='*80}")


def print_shop_detail(data: dict, detail_id: int, shop_id: int):
    """Pretty-print shop-mode detail."""
    resp_data = data.get("data", {})
    oss_md5 = resp_data.get("ossMd5", "-")
    edit_model = resp_data.get("editModel", "-")
    claim_to_shop_ids = resp_data.get("claimToShopIds", [])
    is_support_multi_warehouse = resp_data.get("isSupportMultiWarehouse", 0)

    info = resp_data.get("shopCollectItemInfo", {})
    sku_map = info.get("skuMap", {})
    sku_props = info.get("skuPropertyList", [])
    attrs = info.get("productAttributes", [])

    sku_count = len(sku_map)
    sku_prices = [float(s.get("price", 0)) for s in sku_map.values() if s.get("price")]
    min_price = min(sku_prices) if sku_prices else 0
    max_price = max(sku_prices) if sku_prices else 0

    print(f"\n{'='*80}")
    print(f"TikTok采集箱 - 商品详情  |  店铺模式  |  ID: {detail_id}  |  Shop: {shop_id}")
    print(f"{'='*80}")
    print(f"  ossMd5:       {oss_md5}")
    print(f"  编辑模式:     {edit_model}")
    print(f"  已认领店铺:   {claim_to_shop_ids or '（无）'}")
    print(f"  多仓库模式:   {'是' if is_support_multi_warehouse else '否'}")
    print(f"  标题:         {(info.get('title') or '')[:50]}")
    print(f"  描述:         {(info.get('notes') or '')[:50]}{'...' if len(info.get('notes','')) > 50 else ''}")
    print(f"  类目ID:       {info.get('cid', '-')}")
    print(f"  品牌:         {info.get('brandName', '-')} (ID: {info.get('brandId', '-')})")
    print(f"  图片数:       {len(info.get('imgUrls', []))} 张")
    print(f"  规格维度:     {', '.join(p.get('attrName','') for p in sku_props) or '（无）'}")
    print(f"  SKU数量:      {sku_count} 个")
    if sku_count > 0:
        print(f"  价格范围:     ¥{min_price:.2f} ~ ¥{max_price:.2f}")
    print(f"  重量(kg):     {info.get('weight', '-')}")
    print(f"  尺寸(cm):     {info.get('packageLength','-')} x {info.get('packageWidth','-')} x {info.get('packageHeight','-')}")
    print(f"  COD:          {'开启' if info.get('isCodOpen') == '1' else '关闭'}")
    print(f"  发货方式:     {info.get('deliveryOptionSetType', '-')}")
    print(f"  制造商IDs:    {info.get('manufacturerIds', []) or '（未填写）'}")
    print(f"  责任人IDs:    {info.get('responsiblePersonIds', []) or '（未填写）'}")
    print(f"  属性数:       {len(attrs)} 个")
    print(f"{'='*80}")
    print(f"⚠️  保存时需要此 ossMd5: {oss_md5}")
    print(f"{'='*80}")


def print_site_detail(data: dict, detail_id: int, site: str):
    """Pretty-print site-mode detail."""
    resp_data = data.get("data", {})
    oss_md5 = resp_data.get("ossMd5", "-")
    edit_model = resp_data.get("editModel", "-")

    info = resp_data.get("siteCollectItemInfo", {})
    shop_list = info.get("collectBoxDetailShopList", [])
    sku_map = info.get("skuMap", {})
    sku_props = info.get("skuPropertyList", [])

    sku_count = len(sku_map)
    sku_prices = [float(s.get("price", 0)) for s in sku_map.values() if s.get("price")]
    min_price = min(sku_prices) if sku_prices else 0
    max_price = max(sku_prices) if sku_prices else 0

    print(f"\n{'='*80}")
    print(f"TikTok采集箱 - 商品详情  |  站点模式  |  ID: {detail_id}  |  Site: {site}")
    print(f"{'='*80}")
    print(f"  ossMd5:       {oss_md5}")
    print(f"  编辑模式:     {edit_model}")
    print(f"  店铺配置数:   {len(shop_list)} 个")
    for shop in shop_list:
        print(f"    店铺 {shop.get('shopId')}: 品牌={shop.get('brandName','-')} 制造商={shop.get('manufacturerIds',[])}")
    print(f"  标题:         {(info.get('title') or '')[:50]}")
    print(f"  描述:         {(info.get('notes') or '')[:50]}{'...' if len(info.get('notes','')) > 50 else ''}")
    print(f"  类目ID:       {info.get('cid', '-')}")
    print(f"  图片数:       {len(info.get('imgUrls', []))} 张")
    print(f"  规格维度:     {', '.join(p.get('attrName','') for p in sku_props) or '（无）'}")
    print(f"  SKU数量:      {sku_count} 个")
    if sku_count > 0:
        print(f"  价格范围:     ¥{min_price:.2f} ~ ¥{max_price:.2f}")
    print(f"  重量(kg):     {info.get('weight', '-')}")
    print(f"  尺寸(cm):     {info.get('packageLength','-')} x {info.get('packageWidth','-')} x {info.get('packageHeight','-')}")
    print(f"  制造商IDs:    {info.get('manufacturerIds', []) or '（未填写）'}")
    print(f"  责任人IDs:    {info.get('responsiblePersonIds', []) or '（未填写）'}")
    print(f"{'='*80}")
    print(f"⚠️  保存时需要此 ossMd5: {oss_md5}")
    print(f"{'='*80}")


def diagnose_product(data: dict, mode: str, detail_id: int, shop_id: int = None, site: str = None):
    """
    诊断商品四个维度缺漏。

    四个维度：
    1. 基础信息：标题、描述、图片、类目、品牌、视频、尺码表
    2. SKU配置：规格完整性、价格、库存、预售、平台SKU
    3. 产品属性：类目属性
    4. 物流信息：重量、尺寸、COD、仓库、制造商、责任人
    """
    if mode == "shop":
        info = data.get("data", {}).get("shopCollectItemInfo", {})
        prefix = f"店铺模式 | Shop {shop_id}"
    else:
        info = data.get("data", {}).get("siteCollectItemInfo", {})
        prefix = f"站点模式 | Site {site}"

    sku_map = info.get("skuMap", {})
    sku_prices = [float(s.get("price", 0)) for s in sku_map.values() if s.get("price")]
    sku_stocks = [s.get("stock", 0) for s in sku_map.values()]

    # 判断
    missing_title = not info.get("title")
    missing_notes = not info.get("notes")
    missing_img = len(info.get("imgUrls", [])) == 0
    low_img = 0 < len(info.get("imgUrls", [])) < 5
    missing_cid = not info.get("cid")
    missing_brand = not info.get("brandId")
    missing_video = not info.get("mainImgVideoUrl")
    missing_sizechart = not info.get("sizeChart")
    missing_weight = not info.get("weight") or float(info.get("weight", 0)) <= 0
    missing_dims = (
        not info.get("packageLength")
        or not info.get("packageWidth")
        or not info.get("packageHeight")
        or float(info.get("packageLength", 0)) <= 0
    )
    sku_empty = not sku_map or (len(sku_map) == 1 and "0" in sku_map)
    sku_bad_price = all(p <= 0 for p in sku_prices)
    sku_bad_stock = any(s < 0 for s in sku_stocks)
    sku_missing_weight = any(
        float(s.get("weight", 0)) <= 0
        for s in sku_map.values()
        if s.get("isDelete") != "1"
    )
    missing_mfr = not info.get("manufacturerIds")
    missing_rp = not info.get("responsiblePersonIds")
    attrs = info.get("productAttributes", [])
    missing_attrs = len(attrs) == 0

    print(f"\n{'='*60}")
    print(f"📋 商品 {detail_id} 诊断报告（{prefix}）")
    print(f"{'='*60}")

    # 维度一
    print(f"\n[维度一：基础信息]")
    print(f"  {'✅' if not missing_title else '❌'} 标题: {'已填写' if not missing_title else '❌ 空'}")
    print(f"  {'✅' if not missing_notes else '❌'} 描述: {'已填写' if not missing_notes else '❌ 空'}")
    img_status = f"{len(info.get('imgUrls',[]))}张 {'⚠️ 建议≥5张' if low_img else ''}"
    print(f"  {'✅' if not missing_img else '❌'} 图片: {img_status}")
    print(f"  {'✅' if not missing_cid else '❌'} 类目: {'已选择' if not missing_cid else '❌ 未选择'}")
    print(f"  {'✅' if not missing_brand else '⚠️'} 品牌: {'已填写' if not missing_brand else '⚠️ 未填写'}")
    print(f"  {'✅' if not missing_video else '⚠️'} 视频: {'已上传' if not missing_video else '⚠️ 未上传'}")
    print(f"  {'✅' if not missing_sizechart else '⚠️'} 尺码表: {'已上传' if not missing_sizechart else '⚠️ 未上传'}")

    # 维度二
    print(f"\n[维度二：SKU配置]")
    if sku_empty:
        print(f"  ❌ 规格定义: ❌ 无SKU（请先配置规格）")
    else:
        prop_names = ", ".join(p.get("attrName","?") for p in info.get("skuPropertyList", []))
        print(f"  ✅ 规格定义: 完整（{prop_names}）")
        print(f"  ✅ SKU数量: {len(sku_map)} 个")
    price_status = "✅ 全部填写" if not sku_bad_price else f"❌ {sum(1 for p in sku_prices if p <= 0)} 个SKU价格≤0"
    print(f"  {price_status} 销售价格")
    stock_status = "✅ 全部填写" if not sku_bad_stock else "❌ 存在负库存"
    print(f"  {stock_status} 库存")
    if not sku_empty:
        weight_status = "✅ 全部填写" if not sku_missing_weight else "❌ 存在SKU重量≤0（必填）"
        print(f"  {weight_status} SKU重量")

    # 维度三
    print(f"\n[维度三：产品属性]")
    print(f"  {'✅' if not missing_attrs else '⚠️'} 类目属性: {'已填写' if not missing_attrs else '⚠️ 未填写（建议使用「类目属性优化」工具）'}")

    # 维度四
    print(f"\n[维度四：物流信息]")
    print(f"  {'✅' if not missing_weight else '❌'} 重量: {'已填写' if not missing_weight else '❌ 未填写/≤0'}")
    print(f"  {'✅' if not missing_dims else '❌'} 尺寸: {'已填写' if not missing_dims else '❌ 未填写'}")
    cod_status = "✅ 已配置" if info.get("isCodOpen") in ("0", "1") else "⚠️ 未配置"
    print(f"  {cod_status} COD")
    print(f"  {'✅' if not missing_mfr else '⚠️'} 制造商: {'已选择' if not missing_mfr else '⚠️ 未选择（欧盟站点必须）'}")
    print(f"  {'✅' if not missing_rp else '⚠️'} 责任人: {'已选择' if not missing_rp else '⚠️ 未选择（欧盟站点必须）'}")

    # 汇总
    critical = sum([
        missing_title, missing_notes, missing_img, missing_cid,
        sku_bad_price, sku_bad_stock, sku_missing_weight,
        missing_weight, missing_dims
    ])
    warnings = sum([low_img, missing_brand, missing_video, missing_sizechart,
                    missing_mfr, missing_rp, missing_attrs])

    print(f"\n{'━'*60}")
    print(f"结论: {'可发布' if critical == 0 else f'不可发布（缺 {critical} 项必填 + {warnings} 项建议）'}")
    if critical > 0:
        steps = []
        if missing_notes:
            steps.append("补充描述")
        if missing_mfr or missing_rp:
            steps.append("欧盟合规信息（制造商+责任人）")
        if sku_bad_price:
            steps.append("SKU价格")
        if missing_weight or missing_dims:
            steps.append("物流信息")
        if missing_img:
            steps.append("商品图片")
        print(f"建议: {' → '.join(steps)}")
    print(f"{'━'*60}")


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------

def cmd_list(client: TikTokCollectBoxClient, args):
    """List products in TikTok collect box."""
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
    print_list(result, args.page, args.page_size)
    return result


def cmd_detail(client: TikTokCollectBoxClient, args):
    """Get product detail (shop mode or site mode)."""
    detail_id = int(args.detail_id)

    # 如果用户没指定模式，默认尝试店铺模式（需要 --shop-id）
    if args.mode == "shop":
        if not args.shop_id:
            print("ERROR: --shop-id is required for shop mode")
            sys.exit(1)
        result = client.get_shop_detail(detail_id, int(args.shop_id))
        print_shop_detail(result, detail_id, int(args.shop_id))
    elif args.mode == "site":
        if not args.site:
            print("ERROR: --site is required for site mode (MY/SG/PH/TH/VN/ID)")
            sys.exit(1)
        result = client.get_site_detail(detail_id, args.site.upper())
        print_site_detail(result, detail_id, args.site.upper())
    else:
        # auto 模式：用户必须二选一
        print("ERROR: 请指定 --mode shop 或 --mode site，并提供对应参数")
        print("  店铺模式: --mode shop --shop-id 1001")
        print("  站点模式: --mode site --site MY")
        sys.exit(1)
    return result


def cmd_diagnose(client: TikTokCollectBoxClient, args):
    """Diagnose product four dimensions."""
    detail_id = int(args.detail_id)

    if args.mode == "shop":
        if not args.shop_id:
            print("ERROR: --shop-id is required for shop mode")
            sys.exit(1)
        result = client.get_shop_detail(detail_id, int(args.shop_id))
        diagnose_product(result, "shop", detail_id, shop_id=int(args.shop_id))
    else:
        if not args.site:
            print("ERROR: --site is required for site mode")
            sys.exit(1)
        result = client.get_site_detail(detail_id, args.site.upper())
        diagnose_product(result, "site", detail_id, site=args.site.upper())
    return result


def cmd_save(client: TikTokCollectBoxClient, args):
    """Save product edit."""
    detail_id = int(args.detail_id)

    if not args.oss_md5:
        print("ERROR: --oss-md5 is required (从详情接口获取)")
        sys.exit(1)
    if not args.file:
        print("ERROR: --file is required (编辑数据JSON文件)")
        sys.exit(1)

    edit_file = Path(args.file)
    if not edit_file.exists():
        print(f"ERROR: File not found: {edit_file}")
        sys.exit(1)

    with open(edit_file, "r", encoding="utf-8") as f:
        edit_data = json.load(f)

    if args.mode == "shop":
        if not args.shop_id:
            print("ERROR: --shop-id is required for shop mode")
            sys.exit(1)
        result = client.save_shop(detail_id, int(args.shop_id), args.oss_md5, edit_data)
        new_md5 = result.get("data", {}).get("ossMd5", args.oss_md5)
        print(f"\n✅ 保存成功（店铺模式 | Shop {args.shop_id}）")
        print(f"   新 ossMd5: {new_md5}")
        print(f"   请保存此值，下次编辑需传入")
    else:
        if not args.site:
            print("ERROR: --site is required for site mode")
            sys.exit(1)
        result = client.save_site(detail_id, args.site.upper(), args.oss_md5, edit_data)
        new_md5 = result.get("data", {}).get("ossMd5", args.oss_md5)
        print(f"\n✅ 保存成功（站点模式 | Site {args.site.upper()}）")
        print(f"   新 ossMd5: {new_md5}")
        print(f"   请保存此值，下次编辑需传入")
    return result


def cmd_claim(client: TikTokCollectBoxClient, args):
    """Claim products to pre-publish shops."""
    detail_ids = []
    for d in (args.detail_ids or "").split(","):
        d = d.strip()
        if d:
            detail_ids.append(int(d))

    shop_ids = []
    for s in (args.shop_ids or "").split(","):
        s = s.strip()
        if s:
            shop_ids.append(int(s))

    if not detail_ids:
        print("ERROR: --detail-ids is required")
        sys.exit(1)
    if not shop_ids:
        print("ERROR: --shop-ids is required")
        sys.exit(1)

    print(f"认领 {len(detail_ids)} 个商品到 {len(shop_ids)} 个店铺...")
    result = client.claim_to_shop(detail_ids, shop_ids)
    print(f"\n✅ 认领完成: {result.get('message', '')}")
    return result


def cmd_warehouses(client: TikTokCollectBoxClient, args):
    """List shop warehouses."""
    shop_ids = []
    for s in (args.shop_ids or "").split(","):
        s = s.strip()
        if s:
            shop_ids.append(int(s))

    if not shop_ids:
        print("ERROR: --shop-ids is required")
        sys.exit(1)

    result = client.get_shop_warehouse_list(shop_ids)
    data = result.get("data", {}).get("shopWarehouseList", [])

    print(f"\n{'='*70}")
    print(f"店铺仓库列表")
    print(f"{'='*70}")
    for shop in data:
        print(f"\n  店铺 {shop.get('shopId')} ({shop.get('shopName','')}) - {shop.get('site','')}")
        for wh in shop.get("warehouseList", []):
            default = " [默认]" if wh.get("isDefault") == "1" else ""
            print(f"    仓库 {wh.get('warehouseId')}: {wh.get('warehouseName','')}{default}")
    print(f"{'='*70}")


def cmd_manufacturers(client: TikTokCollectBoxClient, args):
    """List manufacturers."""
    if not args.shop_id:
        print("ERROR: --shop-id is required")
        sys.exit(1)

    result = client.get_manufacturer_list(int(args.shop_id), refresh=1 if args.refresh else 0)
    items = result.get("data", {}).get("manufacturerList", [])

    print(f"\n{'='*60}")
    print(f"制造商列表 (Shop {args.shop_id})")
    print(f"{'='*60}")
    for item in items:
        print(f"  ID: {item.get('id')}  |  名称: {item.get('name','')}")
    print(f"{'='*60}  共 {len(items)} 个")


def cmd_responsible_persons(client: TikTokCollectBoxClient, args):
    """List responsible persons."""
    if not args.shop_id:
        print("ERROR: --shop-id is required")
        sys.exit(1)

    result = client.get_responsible_person_list(int(args.shop_id), refresh=1 if args.refresh else 0)
    items = result.get("data", {}).get("responsiblePersonList", [])

    print(f"\n{'='*60}")
    print(f"欧盟责任人列表 (Shop {args.shop_id})")
    print(f"{'='*60}")
    for item in items:
        print(f"  ID: {item.get('id')}  |  名称: {item.get('name','')}")
    print(f"{'='*60}  共 {len(items)} 个")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Miaoshou ERP - TikTok采集箱 CRUD Skill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 查询未发布商品列表
  python tiktok_collectbox.py list --page 1 --page-size 20

  # 查看商品详情（店铺模式）
  python tiktok_collectbox.py detail 12345 --mode shop --shop-id 1001

  # 查看商品详情（站点模式）
  python tiktok_collectbox.py detail 12345 --mode site --site MY

  # 诊断商品缺漏
  python tiktok_collectbox.py diagnose 12345 --mode shop --shop-id 1001

  # 保存编辑（店铺模式）
  python tiktok_collectbox.py save 12345 --mode shop --shop-id 1001 --oss-md5 abc123 --file edit.json

  # 保存编辑（站点模式）
  python tiktok_collectbox.py save 12345 --mode site --site MY --oss-md5 abc123 --file edit.json

  # 认领商品到店铺
  python tiktok_collectbox.py claim --detail-ids 12345,12346 --shop-ids 1001

  # 获取店铺仓库
  python tiktok_collectbox.py warehouses --shop-ids 1001,1002

  # 获取制造商列表
  python tiktok_collectbox.py manufacturers --shop-id 1001 --refresh

  # 获取责任人列表
  python tiktok_collectbox.py responsible-persons --shop-id 1001
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ---- list ----
    list_parser = subparsers.add_parser("list", help="查询采集箱商品列表")
    list_parser.add_argument("--page", type=int, default=1, help="页码（默认1）")
    list_parser.add_argument("--page-size", type=int, default=20, help="每页条数（默认20）")
    list_parser.add_argument("--status", type=str, choices=["not_published", "timing_publish", "published"],
                             help="状态过滤")
    list_parser.add_argument("--keyword", type=str, help="标题关键词搜索")

    # ---- detail ----
    detail_parser = subparsers.add_parser("detail", help="获取商品详情（店铺/站点模式）")
    detail_parser.add_argument("detail_id", type=str, help="采集箱详情ID")
    detail_parser.add_argument("--mode", type=str, required=True, choices=["shop", "site"], help="编辑模式")
    detail_parser.add_argument("--shop-id", type=str, help="店铺ID（店铺模式必填）")
    detail_parser.add_argument("--site", type=str, help="站点代码（站点模式必填，如MY/SG/PH/TH/VN/ID）")

    # ---- diagnose ----
    diag_parser = subparsers.add_parser("diagnose", help="诊断商品四个维度缺漏")
    diag_parser.add_argument("detail_id", type=str, help="采集箱详情ID")
    diag_parser.add_argument("--mode", type=str, required=True, choices=["shop", "site"], help="编辑模式")
    diag_parser.add_argument("--shop-id", type=str, help="店铺ID（店铺模式必填）")
    diag_parser.add_argument("--site", type=str, help="站点代码（站点模式必填）")

    # ---- save ----
    save_parser = subparsers.add_parser("save", help="保存商品编辑")
    save_parser.add_argument("detail_id", type=str, help="采集箱详情ID")
    save_parser.add_argument("--mode", type=str, required=True, choices=["shop", "site"], help="编辑模式")
    save_parser.add_argument("--oss-md5", type=str, required=True, help="ossMd5（从详情接口获取）")
    save_parser.add_argument("--shop-id", type=str, help="店铺ID（店铺模式必填）")
    save_parser.add_argument("--site", type=str, help="站点代码（站点模式必填）")
    save_parser.add_argument("--file", type=str, required=True, help="编辑数据JSON文件路径")

    # ---- claim ----
    claim_parser = subparsers.add_parser("claim", help="认领商品到预发布店铺")
    claim_parser.add_argument("--detail-ids", type=str, required=True, help="详情ID（逗号分隔）")
    claim_parser.add_argument("--shop-ids", type=str, required=True, help="店铺ID（逗号分隔）")

    # ---- warehouses ----
    wh_parser = subparsers.add_parser("warehouses", help="获取店铺仓库列表")
    wh_parser.add_argument("--shop-ids", type=str, required=True, help="店铺ID（逗号分隔）")

    # ---- manufacturers ----
    mfr_parser = subparsers.add_parser("manufacturers", help="获取制造商列表")
    mfr_parser.add_argument("--shop-id", type=str, required=True, help="店铺ID")
    mfr_parser.add_argument("--refresh", action="store_true", help="重新刷新数据（默认用缓存）")

    # ---- responsible-persons ----
    rp_parser = subparsers.add_parser("responsible-persons", help="获取责任人列表")
    rp_parser.add_argument("--shop-id", type=str, required=True, help="店铺ID")
    rp_parser.add_argument("--refresh", action="store_true", help="重新刷新数据（默认用缓存）")

    parser.add_argument("--raw", action="store_true", help="输出原始JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    config = load_config()
    client = TikTokCollectBoxClient(config)

    data = None
    if args.command == "list":
        data = cmd_list(client, args)
    elif args.command == "detail":
        data = cmd_detail(client, args)
    elif args.command == "diagnose":
        data = cmd_diagnose(client, args)
    elif args.command == "save":
        data = cmd_save(client, args)
    elif args.command == "claim":
        data = cmd_claim(client, args)
    elif args.command == "warehouses":
        data = cmd_warehouses(client, args)
    elif args.command == "manufacturers":
        data = cmd_manufacturers(client, args)
    elif args.command == "responsible-persons":
        data = cmd_responsible_persons(client, args)

    if args.raw and data:
        print("\n--- RAW JSON ---")
        print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

