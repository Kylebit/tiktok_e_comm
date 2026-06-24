"""
Miaoshou ERP (JCOP Open Platform) - Common Collect Box CRUD Tool

Full Create / Read / Update / Delete operations for Miaoshou ERP common collect box.
With SPU/SKU layer awareness and interactive confirmation for ambiguous instructions.

Usage:
    python collectbox_crud.py list [--status STATUS] [--keyword KEYWORD] [--page PAGE] [--size SIZE]
    python collectbox_crud.py detail --id ID
    python collectbox_crud.py add --data '{"title":"...", ...}'
    python collectbox_crud.py add --file product.json
    python collectbox_crud.py edit --id ID --data '{"title":"...", ...}'
    python collectbox_crud.py edit --id ID --file edit.json
    python collectbox_crud.py delete --ids ID1,ID2,ID3
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
# Field Layer Definitions
# ---------------------------------------------------------------------------

# SPU layer fields
SPU_FIELDS = {
    'title', 'itemNum', 'notesText', 'notes', 'price', 'stock', 'weight',
    'packageLength', 'packageWidth', 'packageHeight',
    'imgUrls', 'sizeChart', 'mainImgVideoUrl',
    'colorPropName', 'sizePropName', 'saleProp3Name',
    'sourceAttrs', 'sourceList', 'cateList', 'colorMap', 'sizeMap',
    'productCertifications', 'guideInfo', 'mainImgAppVideoId'
}

# SKU layer fields (inside skuMap)
SKU_FIELDS = {
    'price', 'stock', 'itemNum', 'weight',
    'packageLength', 'packageWidth', 'packageHeight'
}


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
    content = f"{app_secret}{path}{timestamp}{app_key}{body_json}{app_secret}"
    return hmac.new(
        app_secret.encode("utf-8"),
        content.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------------------
# Layer Detection
# ---------------------------------------------------------------------------

def detect_field_layer(field_name: str) -> str:
    """Detect if a field belongs to SPU or SKU layer."""
    if field_name in SPU_FIELDS:
        return 'SPU'
    elif field_name in SKU_FIELDS:
        return 'SKU'
    elif field_name == 'skuMap':
        return 'SKU'
    else:
        return 'UNKNOWN'


def analyze_edit_fields(edit_data: dict) -> Dict[str, Any]:
    """Analyze edit data to determine layers involved."""
    spu_fields = []
    sku_fields = []
    has_sku_map = 'skuMap' in edit_data

    for key in edit_data.keys():
        layer = detect_field_layer(key)
        if layer == 'SPU':
            spu_fields.append(key)
        elif layer == 'SKU':
            sku_fields.append(key)

    return {
        'has_spu_changes': len(spu_fields) > 0,
        'has_sku_changes': len(sku_fields) > 0 or has_sku_map,
        'spu_fields': spu_fields,
        'sku_fields': sku_fields,
        'has_sku_map': has_sku_map
    }


# ---------------------------------------------------------------------------
# SKU Operations
# ---------------------------------------------------------------------------

def parse_sku_key(key: str) -> dict:
    """Parse SKU key like ';红色;S;' into components."""
    parts = [p.strip() for p in key.split(';') if p.strip()]
    return {'specs': parts, 'raw': key}


def format_sku_display(sku_map: dict) -> List[dict]:
    """Format SKU map for display."""
    result = []
    for key, value in sku_map.items():
        specs = parse_sku_key(key)
        result.append({
            'key': key,
            'specs': ' / '.join(specs['specs']),
            'price': value.get('price', '-'),
            'stock': value.get('stock', '-'),
            'itemNum': value.get('itemNum', '-')
        })
    return result


def generate_sku_key(specs: dict) -> str:
    """Generate SKU key from specs dict like {'颜色': '红色', '尺码': 'S'}."""
    parts = []
    for key in sorted(specs.keys()):
        parts.append(specs[key])
    return ';' + ';'.join(parts) + ';'


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------

class CollectBoxClient:
    def __init__(self, config: dict):
        self.base_url = config["base_url"].rstrip("/")
        self.app_key = config["app_key"]
        self.app_secret = config["app_secret"]
        self.timeout = config.get("timeout", 30)

    def _post(self, path: str, body: dict) -> dict:
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
            print(f"\nAPI Error: [{result.get('code')}] {result.get('message', '')}")

        return result

    # ==================== CREATE ====================

    def add_product(self, product_data: dict) -> dict:
        """Create a new product in common collect box."""
        path = "/open/v1/product/common_collect_box/common_collect_box/add_common_collect_box_detail"
        body = product_data
        if "title" not in body:
            print("ERROR: 'title' is required for creating a product")
            sys.exit(1)
        return self._post(path, body)

    # ==================== READ ====================

    def get_list(self, page: int = 1, size: int = 20,
                 status: str = "all", keyword: str = "") -> dict:
        path = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_list"
        body = {
            "pageNo": page,
            "pageSize": size,
            "filter": {"tabPaneName": status},
        }
        if keyword:
            body["filter"]["sourceItemIdKeyword"] = keyword
        return self._post(path, body)

    def get_detail(self, detail_id: int) -> dict:
        path = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail"
        body = {"commonCollectBoxDetailId": detail_id}
        return self._post(path, body)

    # ==================== UPDATE ====================

    def edit_product(self, detail_id: int, edit_data: dict, oss_md5: str = "") -> dict:
        """Edit an existing product in common collect box."""
        path = "/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail"
        body = {
            "commonCollectBoxDetailId": detail_id,
            "editCommonCollectBoxDetail": edit_data,
            "ossMd5": oss_md5,
        }
        return self._post(path, body)

    def edit_product_auto_md5(self, detail_id: int, edit_data: dict) -> dict:
        """Edit product, auto-fetching ossMd5 from current detail."""
        detail_result = self.get_detail(detail_id)
        if detail_result.get("result") != "success":
            print(f"ERROR: Cannot fetch product detail to get ossMd5")
            return detail_result

        data = detail_result.get("data", {})
        oss_md5 = data.get("ossMd5", "")
        edit_common = data.get("editCommonCollectBoxDetail", {})

        merged = dict(edit_common)
        merged.update(edit_data)

        return self.edit_product(detail_id, merged, oss_md5)

    # ==================== DELETE ====================

    def batch_delete(self, detail_ids: List[int]) -> dict:
        path = "/open/v1/product/common_collect_box/common_collect_box/batch_delete_common_collect_box_detail"
        body = {"commonCollectBoxDetailIds": detail_ids}
        return self._post(path, body)


# ---------------------------------------------------------------------------
# Output Helpers
# ---------------------------------------------------------------------------

def print_list_result(data: dict):
    items = data.get("detailList", [])
    total = data.get("total", 0)

    print(f"\n{'='*80}")
    print(f"公共采集箱商品列表  |  总数: {total}  |  本页: {len(items)}")
    print(f"{'='*80}")

    if not items:
        print("(空)")
        return

    for i, item in enumerate(items, 1):
        print(f"\n--- [{i}] ID: {item.get('commonCollectBoxDetailId')} ---")
        print(f"  货号:       {item.get('itemNum', '-')}")
        print(f"  标题:       {item.get('title', '-')}")
        print(f"  价格:       {item.get('price', '-')}  (SKU: {item.get('minSkuPrice', '-')}~{item.get('maxSkuPrice', '-')})")
        print(f"  库存:       {item.get('stock', '-')}")
        print(f"  重量(kg):   {item.get('weight', '-')}")
        print(f"  状态:       {item.get('status', '-')}")
        if item.get("reason"):
            print(f"  失败原因:   {item.get('reason')}")
        if item.get("remark"):
            print(f"  备注:       {item.get('remark')}")
        if item.get("commonCollectBoxGroupName"):
            print(f"  分组:       {item.get('commonCollectBoxGroupName')}")
        sources = item.get("sourceList", [])
        if sources:
            for s in sources:
                print(f"  来源:       {s.get('source', '-')} | {s.get('sourceSite', '-')} | ID: {s.get('sourceItemId', '-')}")
        print(f"  创建:       {item.get('gmtCreate', '-')}")
        print(f"  修改:       {item.get('gmtModified', '-')}")


def print_detail_result(data: dict):
    detail = data.get("editCommonCollectBoxDetail", {})
    if not detail:
        print("No detail data returned.")
        return

    print(f"\n{'='*80}")
    print(f"公共采集箱商品详情  |  ID: {detail.get('commonCollectBoxDetailId')}")
    print(f"{'='*80}")

    # Basic Info
    print(f"\n[基本信息]")
    print(f"标题:         {detail.get('title', '-')}")
    print(f"货号:         {detail.get('itemNum', '-')}")
    print(f"SPU价格:      {detail.get('price', '-')}")
    print(f"SPU库存:      {detail.get('stock', '-')}")
    print(f"重量(kg):     {detail.get('weight', '-')}")
    print(f"包装(cm):     {detail.get('packageLength', '-')} x {detail.get('packageWidth', '-')} x {detail.get('packageHeight', '-')}")

    # Category
    cate_list = detail.get("cateList", [])
    if cate_list:
        print(f"类目:         {' > '.join(cate_list)}")

    # Sales Attributes
    print(f"\n[销售属性]")
    if detail.get("colorPropName"):
        print(f"规格一:       {detail.get('colorPropName')}")
    if detail.get("sizePropName"):
        print(f"规格二:       {detail.get('sizePropName')}")
    if detail.get("saleProp3Name"):
        print(f"规格三:       {detail.get('saleProp3Name')}")

    # SKU Map
    sku_map = detail.get("skuMap", {})
    if sku_map:
        print(f"\nSKU列表 ({len(sku_map)} 个):")
        print(f"  {'规格组合':<30} {'SKU编号':<15} {'价格':<10} {'库存':<8}")
        print(f"  {'-'*30} {'-'*15} {'-'*10} {'-'*8}")
        for spec_key, sku in sku_map.items():
            specs = parse_sku_key(spec_key)
            spec_str = ' / '.join(specs['specs'])
            item_num = sku.get('itemNum') or '-'
            price = sku.get('price') or '-'
            stock = sku.get('stock') or '-'
            print(f"  {spec_str:<30} {str(item_num):<15} {str(price):<10} {str(stock):<8}")
    else:
        print("(单品模式，无SKU)")

    # Product Attributes
    attrs = detail.get("sourceAttrs", [])
    if attrs:
        print(f"\n[产品属性]")
        for a in attrs:
            print(f"  {a.get('name', '-')}: {a.get('value', '-')}")

    # Images
    img_urls = detail.get("imgUrls", [])
    if img_urls:
        print(f"\n[产品图片] ({len(img_urls)} 张)")
        for url in img_urls[:5]:
            print(f"  {url}")
        if len(img_urls) > 5:
            print(f"  ... 省略 {len(img_urls) - 5} 张")

    if detail.get("sizeChart"):
        print(f"\n[尺寸图表] {detail.get('sizeChart')}")

    if detail.get("mainImgVideoUrl"):
        print(f"\n[主图视频] {detail.get('mainImgVideoUrl')}")

    # Certifications
    certs = detail.get("productCertifications", [])
    if certs:
        print(f"\n[产品认证] ({len(certs)} 个)")
        for c in certs:
            print(f"  {c.get('fileName', '-')}: {c.get('fileUrl', '-')}")

    # Source Links
    sources = detail.get("sourceList", [])
    if sources:
        print(f"\n[货源链接] ({len(sources)} 个)")
        for s in sources:
            print(f"  {s.get('source', '-')}: {s.get('sourceItemId', '-')}")
            if s.get("sourceItemUrl"):
                print(f"    {s.get('sourceItemUrl')}")

    # Notes
    notes = detail.get("notesText", "") or detail.get("notes", "")
    if notes:
        print(f"\n[描述]")
        print(f"{notes[:300]}{'...' if len(notes) > 300 else ''}")


def print_add_result(data: dict):
    new_id = data.get("commonCollectBoxDetailId")
    print(f"\n[OK] 创建成功！")
    print(f"   新商品ID: {new_id}")


def print_edit_result():
    print(f"\n[OK] 编辑成功！")


def print_delete_result(data: dict, ids: List[int]):
    print(f"\n[OK] 删除成功！")
    print(f"   已删除商品ID: {ids}")


def print_field_analysis(analysis: dict):
    """Print field layer analysis for edit operation."""
    print("\n[字段层级分析]")
    if analysis['has_spu_changes']:
        print(f"  SPU层级字段: {', '.join(analysis['spu_fields'])}")
    if analysis['has_sku_changes']:
        print(f"  SKU层级字段: {', '.join(analysis['sku_fields']) or 'skuMap'}")


def print_sku_list(sku_map: dict):
    """Print SKU list for selection."""
    formatted = format_sku_display(sku_map)
    print("\n[SKU列表]")
    print(f"  {'#':<3} {'规格组合':<30} {'SKU编号':<15} {'价格':<10} {'库存':<8}")
    print(f"  {'-'*3} {'-'*30} {'-'*15} {'-'*10} {'-'*8}")
    for i, sku in enumerate(formatted, 1):
        print(f"  {i:<3} {sku['specs']:<30} {str(sku['itemNum']):<15} {str(sku['price']):<10} {str(sku['stock']):<8}")


def print_edit_preview(edit_data: dict, sku_map: dict = None):
    """Print preview of changes to be made."""
    print("\n[修改预览]")
    for key, value in edit_data.items():
        if key == 'skuMap':
            print("  skuMap:")
            for sku_key, sku_val in value.items():
                if sku_map and sku_key in sku_map:
                    old_price = sku_map[sku_key].get('price', '-')
                    old_stock = sku_map[sku_key].get('stock', '-')
                    new_price = sku_val.get('price', old_price)
                    new_stock = sku_val.get('stock', old_stock)
                    specs = parse_sku_key(sku_key)
                    spec_str = ' / '.join(specs['specs'])
                    changes = []
                    if 'price' in sku_val:
                        changes.append(f"价格: {old_price} -> {new_price}")
                    if 'stock' in sku_val:
                        changes.append(f"库存: {old_stock} -> {new_stock}")
                    if changes:
                        print(f"    {spec_str}: {', '.join(changes)}")
                else:
                    specs = parse_sku_key(sku_key)
                    spec_str = ' / '.join(specs['specs'])
                    print(f"    {spec_str}: {sku_val}")
        else:
            print(f"  {key}: {value}")


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------

def cmd_list(client: CollectBoxClient, args):
    status = args.status or "all"
    keyword = args.keyword or ""
    page = args.page or 1
    size = args.size or 20

    valid_statuses = {"all", "noClaimed", "claimed", "collectFail", "collectSuccess"}
    if status not in valid_statuses:
        print(f"ERROR: Invalid status '{status}'. Valid: {', '.join(sorted(valid_statuses))}")
        sys.exit(1)

    print(f"Querying: page={page}, size={size}, status={status}, keyword={keyword or '(none)'}")
    result = client.get_list(page=page, size=size, status=status, keyword=keyword)
    if result.get("result") == "success":
        print_list_result(result.get("data", {}))
    return result.get("data", {})


def cmd_detail(client: CollectBoxClient, args):
    detail_id = args.id
    print(f"Querying detail: ID={detail_id}")
    result = client.get_detail(detail_id)
    if result.get("result") == "success":
        print_detail_result(result.get("data", {}))
    return result.get("data", {})


def cmd_add(client: CollectBoxClient, args):
    """Create a new product."""
    if args.data:
        product_data = json.loads(args.data)
    elif args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"ERROR: File not found: {file_path}")
            sys.exit(1)
        with open(file_path, "r", encoding="utf-8") as f:
            product_data = json.load(f)
    else:
        print("ERROR: Must provide --data or --file for add command")
        sys.exit(1)

    print(f"Creating new product...")
    print(f"Title: {product_data.get('title', '(no title)')}")

    result = client.add_product(product_data)

    if result.get("result") == "success":
        print_add_result(result.get("data", {}))
    else:
        print(f"ERROR: Create failed - {result.get('message', '')}")
    return result.get("data", {})


def cmd_edit(client: CollectBoxClient, args):
    """Edit an existing product."""
    detail_id = args.id

    if args.data:
        edit_data = json.loads(args.data)
    elif args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"ERROR: File not found: {file_path}")
            sys.exit(1)
        with open(file_path, "r", encoding="utf-8") as f:
            edit_data = json.load(f)
    else:
        print("ERROR: Must provide --data or --file for edit command")
        sys.exit(1)

    print(f"Editing product ID: {detail_id}")

    # Analyze fields
    analysis = analyze_edit_fields(edit_data)
    print_field_analysis(analysis)

    # If SKU changes, get current skuMap for preview
    current_sku_map = None
    if analysis['has_sku_changes']:
        detail_result = client.get_detail(detail_id)
        if detail_result.get("result") == "success":
            current_sku_map = detail_result.get("data", {}).get("editCommonCollectBoxDetail", {}).get("skuMap", {})

    # Print preview
    if analysis['has_sku_changes'] and current_sku_map:
        print_sku_list(current_sku_map)
    print_edit_preview(edit_data, current_sku_map)

    # Execute
    result = client.edit_product_auto_md5(detail_id, edit_data)

    if result.get("result") == "success":
        print_edit_result()
    else:
        print(f"ERROR: Edit failed - {result.get('message', '')}")
    return result.get("data", {})


def cmd_delete(client: CollectBoxClient, args):
    """Batch delete products."""
    ids_str = args.ids or ""
    ids = [int(x.strip()) for x in ids_str.split(",") if x.strip()]

    if not ids:
        print("ERROR: No valid IDs provided")
        sys.exit(1)

    print(f"Deleting {len(ids)} product(s): {ids}")
    confirm = input("Confirm delete? (y/N): ")
    if confirm.lower() != "y":
        print("Aborted.")
        return {}

    result = client.batch_delete(ids)

    if result.get("result") == "success":
        print_delete_result(result.get("data", {}), ids)
    else:
        print(f"ERROR: Delete failed - {result.get('message', '')}")
    return result.get("data", {})


def cmd_interactive_add(client: CollectBoxClient, args):
    """Interactively create a product with minimal required fields."""
    print("\n" + "="*80)
    print("交互式创建商品")
    print("="*80)

    title = input("\n商品标题（必填）: ").strip()
    if not title:
        print("ERROR: title is required")
        sys.exit(1)

    product_data = {"title": title}

    item_num = input("货号（可选）: ").strip()
    if item_num:
        product_data["itemNum"] = item_num

    price_str = input("价格（可选）: ").strip()
    if price_str:
        try:
            product_data["price"] = float(price_str)
        except ValueError:
            print("Invalid price, skipping")

    stock_str = input("库存（可选）: ").strip()
    if stock_str:
        try:
            product_data["stock"] = int(stock_str)
        except ValueError:
            print("Invalid stock, skipping")

    weight_str = input("重量 kg（可选）: ").strip()
    if weight_str:
        try:
            product_data["weight"] = float(weight_str)
        except ValueError:
            print("Invalid weight, skipping")

    print(f"\n即将创建商品:")
    print(json.dumps(product_data, ensure_ascii=False, indent=2))
    confirm = input("\n确认创建? (y/N): ")
    if confirm.lower() != "y":
        print("Aborted.")
        return {}

    result = client.add_product(product_data)
    if result.get("result") == "success":
        print_add_result(result.get("data", {}))
        new_id = result.get("data", {}).get("commonCollectBoxDetailId")
        print(f"\nHint: Use following command to continue editing:")
        print(f"   python collectbox_crud.py edit --id {new_id} --data '{{...}}'")
    return result.get("data", {})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Miaoshou ERP Common Collect Box CRUD Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Query (List)
  %(prog)s list
  %(prog)s list --status noClaimed --page 1 --size 20
  %(prog)s list --keyword "ABC123"

  # Query Detail (Read)
  %(prog)s detail --id 12345

  # Create (Create) via --data
  %(prog)s add --data '{"title": "Test Product", "price": 19.99, "stock": 100}'

  # Create (Create) via JSON file
  %(prog)s add --file product.json

  # Interactive Create
  %(prog)s add-interactive

  # Edit (Update) via --data
  %(prog)s edit --id 12345 --data '{"title": "New Title", "price": 29.99}'

  # Edit (Update) via JSON file
  %(prog)s edit --id 12345 --file edit.json

  # Delete (Delete)
  %(prog)s delete --ids 12345,12346,12347
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # list
    list_p = subparsers.add_parser("list", help="List products (Read)")
    list_p.add_argument("--status", type=str, default="all",
                        help="Filter: all|noClaimed|claimed|collectFail|collectSuccess")
    list_p.add_argument("--keyword", type=str, default="",
                        help="Filter by source item ID keyword")
    list_p.add_argument("--page", type=int, default=1, help="Page number")
    list_p.add_argument("--size", type=int, default=20, help="Page size")

    # detail
    detail_p = subparsers.add_parser("detail", help="Get product detail (Read)")
    detail_p.add_argument("--id", type=int, required=True, help="Product ID")

    # add
    add_p = subparsers.add_parser("add", help="Create new product (Create)")
    add_p.add_argument("--data", type=str, default="", help="Product data as JSON string")
    add_p.add_argument("--file", type=str, default="", help="Path to JSON file with product data")

    # add-interactive
    subparsers.add_parser("add-interactive", help="Interactively create product")

    # edit
    edit_p = subparsers.add_parser("edit", help="Edit existing product (Update)")
    edit_p.add_argument("--id", type=int, required=True, help="Product ID to edit")
    edit_p.add_argument("--data", type=str, default="", help="Edit data as JSON string")
    edit_p.add_argument("--file", type=str, default="", help="Path to JSON file with edit data")

    # delete
    del_p = subparsers.add_parser("delete", help="Batch delete products (Delete)")
    del_p.add_argument("--ids", type=str, required=True,
                        help="Comma-separated product IDs to delete")

    # --raw flag
    parser.add_argument("--raw", action="store_true", help="Output raw JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    config = load_config()
    client = CollectBoxClient(config)

    command_map = {
        "list": cmd_list,
        "detail": cmd_detail,
        "add": cmd_add,
        "add-interactive": cmd_interactive_add,
        "edit": cmd_edit,
        "delete": cmd_delete,
    }

    if args.command in command_map:
        data = command_map[args.command](client, args)
        if args.raw and data:
            print("\n--- RAW JSON ---")
            print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()

