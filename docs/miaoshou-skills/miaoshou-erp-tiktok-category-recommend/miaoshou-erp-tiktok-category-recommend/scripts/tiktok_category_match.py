#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Miaoshou ERP - TikTok 类目属性 AI 匹配工具

通过 AI 智能匹配 TikTok 平台的最佳类目和属性值。
支持两种工作模式：
1. 从采集箱获取商品信息 + AI 匹配
2. 用户手动提供商品信息 + AI 匹配
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import requests
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "resources" / "config.json"

# 也支持复用 tiktok-publish 的配置
PUBLISH_CONFIG_PATH = Path(__file__).parent.parent.parent / "miaoshou-erp-tiktok-product-publish" / "resources" / "config.json"


def load_config() -> dict:
    """加载配置；支持本目录、tiktok-publish 配置和环境变量。"""
    config = {}
    for path in [DEFAULT_CONFIG_PATH, PUBLISH_CONFIG_PATH]:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                config = json.load(f)
            break

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

    app_key = str(config.get("app_key", "")).strip()
    app_secret = str(config.get("app_secret", "")).strip()
    placeholder_values = {"your_app_key_here", "your_app_secret_here", ""}
    if app_key in placeholder_values or app_secret in placeholder_values:
        raise FileNotFoundError(
            "未配置妙手开放平台 AppKey/AppSecret；请创建 resources/config.json，"
            "或设置 MIAOSHOU_APP_KEY 和 MIAOSHOU_APP_SECRET 环境变量"
        )

    return config

# ---------------------------------------------------------------------------
# LLM Client（可配置接入 WorkBuddy 运行时 LLM）
# ---------------------------------------------------------------------------

class LLMClient:
    """LLM 调用客户端。支持自定义 endpoint 和 model。"""

    def __init__(self, config: dict):
        self.endpoint = config.get("llm", {}).get("endpoint", "")
        self.model = config.get("llm", {}).get("model", "gpt-4o-mini")
        self.api_key = config.get("llm", {}).get("api_key", "")
        self.enabled = bool(self.endpoint)

    def call(self, prompt: str, system: str = "") -> str:
        """调用 LLM 并返回文本响应。"""
        if not self.enabled:
            return ""

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": [],
        }
        if system:
            payload["messages"].append({"role": "system", "content": system})
        payload["messages"].append({"role": "user", "content": prompt})

        resp = requests.post(
            self.endpoint,
            headers=headers,
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "")


# ---------------------------------------------------------------------------
# JCOP API Client
# ---------------------------------------------------------------------------

class TikTokCategoryMatchClient:
    """TikTok 类目匹配 API 客户端。"""

    BASE_URL = "https://openapi-erp.91miaoshou.com"
    SHOP_LIST_PATH = "/open/v1/product/shop/shop/get_shop_list"

    # TK 采集箱详情（复用 tiktok-collectbox API）
    COLLECT_BOX_DETAIL_SHOP = "/open/v1/product/collect_box/tiktok/collect_box/get_collect_box_detail"
    COLLECT_BOX_DETAIL_SITE = "/open/v1/product/collect_box/tiktok/collect_box/get_collect_box_detail"

    # 类目相关
    CATEGORY_TREE_PATH = "/open/v1/product/collect_box/tiktok/collect_box/get_category_tree_by_site"
    CATEGORY_METADATA_PATH = "/open/v1/product/collect_box/tiktok/collect_box/get_category_metadata"

    def __init__(self, config: dict):
        self.base_url = config.get("base_url", self.BASE_URL)
        self.app_key = config.get("app_key", "")
        self.app_secret = config.get("app_secret", "")

    # ---- 签名 ----

    def _sign(self, path: str, timestamp: int, body_json: str) -> str:
        """生成 HmacSHA256 签名。"""
        msg = f"{self.app_secret}{path}{timestamp}{self.app_key}{body_json}{self.app_secret}"
        return hmac.new(
            self.app_secret.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _headers(self, path: str, body: dict) -> dict:
        """生成请求头（含签名）。"""
        timestamp = int(time.time())
        body_json = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        sign = self._sign(path, timestamp, body_json)
        return {
            "x-app-key": self.app_key,
            "x-timestamp": str(timestamp),
            "x-sign": sign,
            "Content-Type": "application/json",
        }

    def _post(self, path: str, body: dict) -> dict:
        """POST 请求统一入口。"""
        url = f"{self.base_url}{path}"
        headers = self._headers(path, body)
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        if resp.status_code == 200 and not resp.content:
            return {"result": "error", "message": "上游服务不可用（JCOP），HTTP 200 但无内容"}
        resp.raise_for_status()
        return resp.json()

    # ---- 商品详情（复用 TK 采集箱 API） ----

    def get_product_detail_shop(self, detail_id: int, shop_id: int) -> dict:
        """店铺模式获取商品详情。"""
        body = {"detailId": detail_id, "shopId": shop_id}
        return self._post(self.COLLECT_BOX_DETAIL_SHOP, body)

    def get_product_detail_site(self, detail_id: int, site: str) -> dict:
        """站点模式获取商品详情。"""
        body = {"detailId": detail_id, "site": site}
        return self._post(self.COLLECT_BOX_DETAIL_SITE, body)

    def extract_product_info(self, detail_result: dict, mode: str) -> dict:
        """从采集箱详情中提取商品关键信息。"""
        if detail_result.get("result") != "success":
            raise ValueError(f"获取商品详情失败: {detail_result.get('message', '未知错误')}")

        data = detail_result.get("data", {})
        info = data.get("collectBoxDetailDTO", data.get("info"), {})

        # 尝试多个可能的数据结构
        title = ""
        description = ""
        images = []

        # 结构1：直接在 info 下
        if isinstance(info, dict):
            title = info.get("title", info.get("productName", ""))
            description = info.get("description", info.get("descriptionStr", ""))
            imgs = info.get("imageList", info.get("imageUrls", []))
            if isinstance(imgs, list):
                images = [img.get("url", img) if isinstance(img, dict) else str(img) for img in imgs]

        # 结构2：在 detailInfo 下
        detail_info = info.get("detailInfo", {}) if isinstance(info, dict) else {}
        if not title:
            title = detail_info.get("title", "")
        if not description:
            description = detail_info.get("description", "")
        if not images:
            images = detail_info.get("imageList", [])

        return {
            "title": title,
            "description": description,
            "images": images,
        }

    # ---- 类目树 ----

    def get_category_tree(self, site: str) -> dict:
        """获取站点类目树。"""
        body = {"site": site.upper()}
        return self._post(self.CATEGORY_TREE_PATH, body)

    def flatten_category_tree(self, tree_data: dict) -> list:
        """将嵌套类目树展平为列表，每个元素包含 cid 和面包屑路径。"""
        if tree_data.get("result") != "success":
            raise ValueError(f"获取类目树失败: {tree_data.get('message', '未知错误')}")

        cate_tree = tree_data.get("data", {}).get("cateTree", {})

        # 构建 cid -> name 映射
        cid_to_name = {}
        cid_to_fid = {}
        for cid_str, node in cate_tree.items():
            cid = int(cid_str)
            cid_to_name[cid] = {
                "name": node.get("name", ""),
                "nameChinese": node.get("nameChinese", ""),
                "fid": node.get("fid", 0),
                "isLastLevel": node.get("isLastLevel", "false"),
            }
            cid_to_fid[cid] = node.get("fid", 0)

        # 构建面包屑
        flat = []

        def get_breadcrumb(cid: int) -> str:
            path = []
            seen = set()
            current = cid
            while current and current not in seen:
                seen.add(current)
                node = cid_to_name.get(current, {})
                name = node.get("nameChinese") or node.get("name", "")
                if name:
                    path.append(name)
                current = node.get("fid", 0)
            return " > ".join(reversed(path))

        for cid, node in cid_to_name.items():
            flat.append({
                "cid": cid,
                "name": node.get("name", ""),
                "nameChinese": node.get("nameChinese", ""),
                "breadcrumb": get_breadcrumb(cid),
                "isLastLevel": node.get("isLastLevel", "false") == "true",
            })

        return flat

    # ---- 类目属性 ----

    def get_category_metadata(self, site: str, cid: int, shop_ids: list) -> dict:
        """获取类目属性元数据。"""
        body = {
            "site": site.upper(),
            "cid": cid,
            "shopIds": [int(s) for s in shop_ids],
        }
        return self._post(self.CATEGORY_METADATA_PATH, body)


# ---------------------------------------------------------------------------
# 匹配引擎
# ---------------------------------------------------------------------------

def build_stage2_prompt(title: str, description: str, flat_tree: list, top_n: int = 3) -> str:
    """构建阶段二 Prompt：LLM 预筛选 Top N 候选类目。"""
    # 只传末级类目，减少 Token
    leaf_categories = [c for c in flat_tree if c["isLastLevel"]]
    if not leaf_categories:
        leaf_categories = flat_tree[:100]  # fallback

    tree_lines = "\n".join(
        f"{c['cid']}: {c['breadcrumb']}" for c in leaf_categories
    )

    return f"""你是一个TikTok电商类目专家。请根据商品信息，从以下类目树中推荐最匹配的 Top {top_n} 个类目。

商品信息：
- 标题：{title}
- 描述：{description}

类目树：
{tree_lines}

请只输出JSON格式（不要任何其他文字）：
{{
  "recommendations": [
    {{
      "cid": 12345,
      "breadcrumb": "一级 > 二级 > 三级",
      "reason": "推荐理由，50字以内"
    }}
  ]
}}"""


def build_stage4_prompt(
    title: str,
    description: str,
    category_cid: int,
    category_breadcrumb: str,
    metadata: dict,
) -> str:
    """构建阶段四 Prompt：LLM 为类目匹配属性值。"""

    def fmt_attr(attr: dict) -> str:
        attr_id = attr.get("attrId", "")
        name = attr.get("attributeNameAlias") or attr.get("name", "")
        mandatory = "【必填】" if attr.get("isMandatory") else "【选填】"
        values = attr.get("values", [])
        if values:
            value_list = ", ".join(
                f"{v.get('valueNameAlias') or v.get('name', '')} (id:{v.get('id', '')})"
                for v in values[:30]  # 限制每个属性最多30个值
            )
            return f"- {name} {mandatory} (attrId:{attr_id}): {value_list}"
        return f"- {name} {mandatory} (attrId:{attr_id}): [无预定义值，可自定义]"

    sale_attrs = metadata.get("categorySaleAttrList", [])
    product_attrs = metadata.get("categoryProductAttrList", [])
    config = metadata.get("categoryConfig", {})

    sale_section = "\n".join(fmt_attr(a) for a in sale_attrs)
    product_section = "\n".join(fmt_attr(a) for a in product_attrs)

    prompt = f"""你是一个TikTok电商属性匹配专家。请根据商品信息，为目标类目推荐最合适的属性值。

商品信息：
- 标题：{title}
- 描述：{description}

目标类目：{category_breadcrumb} (cid: {category_cid})

销售属性（Sales Attributes）：
{sale_section}

商品属性（Product Attributes）：
{product_section}

请只输出JSON格式（不要任何其他文字）：
{{
  "recommended_attributes": [
    {{
      "attrId": "属性ID",
      "attrName": "属性英文名",
      "attrNameAlias": "属性中文名",
      "isMandatory": true,
      "recommended_values": [
        {{
          "id": "值ID",
          "name": "值英文名",
          "valueNameAlias": "值中文名",
          "confidence": 0.95
        }}
      ],
      "reason": "推荐理由，30字以内"
    }}
  ]
}}"""

    return prompt


def parse_llm_json(text: str) -> Any:
    """从 LLM 输出中提取 JSON。"""
    text = text.strip()
    # 尝试找 ```json ... ``` 包裹的内容
    if "```" in text:
        for block in text.split("```"):
            block = block.strip()
            if block.startswith("json") or block.startswith("{"):
                text = block.lstrip("json").strip()
                break
    # 找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        return json.loads(text[start:end])
    return None


# ---------------------------------------------------------------------------
# 输出函数
# ---------------------------------------------------------------------------

def print_category_tree(flat_tree: list, site: str):
    """打印类目树（树形结构）。"""
    print(f"\nTikTok 类目树  |  站点: {site}  |  共 {len(flat_tree)} 个类目\n")
    print(f"{'CID':<12} {'是否末级':<8} {'类目路径'}")
    print("-" * 80)

    for cat in flat_tree[:100]:  # 默认只显示前100
        flag = "✓" if cat["isLastLevel"] else "○"
        name = cat["nameChinese"] or cat["name"] or "(无名称)"
        print(f"{cat['cid']:<12} {flag:<8} {cat['breadcrumb']}")

    if len(flat_tree) > 100:
        print(f"\n... 共 {len(flat_tree)} 个类目，以上显示前 100 个")
        print("提示: 使用 --keyword 筛选，或查看完整树请配合 grep 使用 --output-json")


def print_match_result(
    title: str,
    description: str,
    top_categories: list,
    all_metadata: dict,
    llm_client: LLMClient,
    include_optional: bool = False,
    output_json: bool = False,
):
    """打印 AI 匹配结果。"""

    if output_json:
        print("\n--- MATCH RESULT JSON ---")
        print(json.dumps({
            "product": {"title": title, "description": description},
            "recommendations": top_categories,
        }, ensure_ascii=False, indent=2))
        return

    print(f"\n🍀 TikTok 类目 AI 匹配结果")
    print(f"   商品: {title[:50]}{'...' if len(title) > 50 else ''}")
    print("=" * 80)

    for i, cat in enumerate(top_categories, 1):
        cid = cat.get("cid", "")
        breadcrumb = cat.get("breadcrumb", "")
        reason = cat.get("reason", "")
        recommended_attrs = cat.get("recommended_attributes", [])

        print(f"\n{'═' * 80}")
        print(f"  推荐类目 #{i}")
        print(f"{'═' * 80}")
        print(f"  路径:   {breadcrumb}")
        print(f"  类目ID: {cid}")
        print(f"  理由:   {reason}")

        if recommended_attrs:
            mandatory = [a for a in recommended_attrs if a.get("isMandatory")]
            optional = [a for a in recommended_attrs if not a.get("isMandatory")]

            if mandatory:
                print(f"\n  📋 推荐属性值\n")
                print(f"  【必填属性】")
                for attr in mandatory:
                    attr_alias = attr.get("attrNameAlias") or attr.get("attrName", "")
                    values = attr.get("recommended_values", [])
                    if values:
                        value_strs = []
                        for v in values[:5]:
                            name = v.get("valueNameAlias") or v.get("name", "")
                            conf = v.get("confidence", 0)
                            value_strs.append(f"{name} (id:{v.get('id', '')}, 相关度:{conf:.0%})")
                        print(f"  ✓ {attr_alias}")
                        for vs in value_strs:
                            print(f"      {vs}")
                    else:
                        print(f"  ✓ {attr_alias} (无预定义值，可自定义)")

            if optional and include_optional:
                print(f"\n  【选填属性】")
                for attr in optional:
                    attr_alias = attr.get("attrNameAlias") or attr.get("attrName", "")
                    values = attr.get("recommended_values", [])
                    if values:
                        for v in values[:3]:
                            name = v.get("valueNameAlias") or v.get("name", "")
                            conf = v.get("confidence", 0)
                            print(f"  ○ {attr_alias} → {name} (相关度:{conf:.0%})")

    print(f"\n{'=' * 80}")
    print("💡 提示: 将以上结果用于 tiktok-collectbox edit 命令补全属性")


def print_category_metadata(data: dict, cid: int, site: str):
    """打印单个类目属性（调试用）。"""
    if data.get("result") != "success":
        print(f"Error: {data.get('message', 'Unknown error')}")
        return

    metadata = data.get("data", {}).get("categoryMetadata", {})
    config = metadata.get("categoryConfig", {})
    sale_attrs = metadata.get("categorySaleAttrList", [])
    product_attrs = metadata.get("categoryProductAttrList", [])

    print(f"\nTikTok 类目属性  |  CID: {cid}  |  站点: {site}")
    print("=" * 80)

    # 分类配置
    print("\n📋 分类配置")
    for key in ["sizeChartIsRequired", "codIsSupported", "packageDimensionIsRequired",
                "eprIsRequired", "responsiblePersonIsRequired", "manufacturerIsRequired"]:
        val = config.get(key, "N/A")
        label = {
            "sizeChartIsRequired": "尺码表必填",
            "codIsSupported": "支持COD",
            "packageDimensionIsRequired": "包装尺寸必填",
            "eprIsRequired": "EPR必填",
            "responsiblePersonIsRequired": "责任人必填",
            "manufacturerIsRequired": "制造商必填",
        }.get(key, key)
        print(f"  {label}: {val}")

    # 认证
    certs = config.get("productCertifications", [])
    if certs:
        print(f"\n📜 商品认证（共 {len(certs)} 项）")
        for cert in certs:
            mandatory = "【强制】" if cert.get("isMandatory") else "【可选】"
            print(f"  {mandatory} {cert.get('name', '')} (id:{cert.get('id', '')})")
            doc = cert.get("documentDetails", "")
            if doc:
                print(f"      说明: {doc[:80]}")

    # 销售属性
    print(f"\n🏷️  销售属性（共 {len(sale_attrs)} 项）")
    for attr in sale_attrs:
        mandatory = "【必填】" if attr.get("isMandatory") else "【选填】"
        multi = "（可多选）" if attr.get("isMultipleSelected") else ""
        custom = "（可自定义）" if attr.get("isCustomized") else ""
        alias = attr.get("attributeNameAlias") or attr.get("name", "")
        values = attr.get("values", [])
        print(f"\n  {mandatory} {alias} (attrId:{attr.get('attrId', '')}) {multi}{custom}")
        if values:
            for v in values[:20]:
                valias = v.get("valueNameAlias") or v.get("name", "")
                print(f"      - {valias} (id:{v.get('id', '')})")
            if len(values) > 20:
                print(f"      ... 共 {len(values)} 个值")

    # 商品属性
    print(f"\n📦 商品属性（共 {len(product_attrs)} 项）")
    for attr in product_attrs:
        mandatory = "【必填】" if attr.get("isMandatory") else "【选填】"
        multi = "（可多选）" if attr.get("isMultipleSelected") else ""
        custom = "（可自定义）" if attr.get("isCustomized") else ""
        alias = attr.get("attributeNameAlias") or attr.get("name", "")
        values = attr.get("values", [])
        print(f"\n  {mandatory} {alias} (attrId:{attr.get('attrId', '')}) {multi}{custom}")
        if values:
            for v in values[:20]:
                valias = v.get("valueNameAlias") or v.get("name", "")
                print(f"      - {valias} (id:{v.get('id', '')})")
            if len(values) > 20:
                print(f"      ... 共 {len(values)} 个值")


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------

def cmd_match(client: TikTokCategoryMatchClient, llm_client: LLMClient, args):
    """AI 匹配类目 + 属性值。"""

    # ---- 1. 获取商品信息 ----
    if args.detail_id:
        print(f"正在获取商品详情: detailId={args.detail_id}, mode={args.mode}, site={args.site}")
        if args.mode == "shop":
            if not args.shop_id:
                print("ERROR: --shop-id required in shop mode")
                return
            result = client.get_product_detail_shop(int(args.detail_id), int(args.shop_id))
        else:
            result = client.get_product_detail_site(int(args.detail_id), args.site.upper())
        product_info = client.extract_product_info(result, args.mode)
        title = product_info["title"]
        description = product_info["description"]
        images = product_info["images"]
        print(f"  标题: {title[:50]}{'...' if len(title) > 50 else ''}")
        print(f"  描述: {description[:80]}{'...' if len(description) > 80 else ''}")
    elif args.title:
        title = args.title
        description = args.description or ""
        images = []
        print(f"使用手动输入的商品信息:")
        print(f"  标题: {title[:50]}{'...' if len(title) > 50 else ''}")
    else:
        print("ERROR: 必须提供 --detail-id 或 --title")
        return

    shop_ids = [int(s.strip()) for s in args.shop_ids.split(",") if s.strip()]
    top_n = args.top_n or 3

    # ---- 2. 获取类目树 ----
    print(f"\n正在获取类目树: site={args.site}")
    tree_result = client.get_category_tree(args.site.upper())
    flat_tree = client.flatten_category_tree(tree_result)
    leaf_count = sum(1 for c in flat_tree if c["isLastLevel"])
    print(f"  共 {len(flat_tree)} 个类目（含 {leaf_count} 个末级类目）")

    # ---- 3. 阶段二：LLM 预筛选 Top N 类目 ----
    if llm_client.enabled:
        print(f"\n正在调用 LLM 预筛选 Top {top_n} 候选类目...")
        prompt = build_stage2_prompt(title, description, flat_tree, top_n)
        llm_output = llm_client.call(prompt)
        parsed = parse_llm_json(llm_output)
        if parsed and "recommendations" in parsed:
            top_categories = parsed["recommendations"]
        else:
            print(f"⚠️  LLM 返回格式异常，回退为前 {top_n} 个末级类目")
            top_categories = [
                {"cid": c["cid"], "breadcrumb": c["breadcrumb"], "reason": "（无 LLM 分析）"}
                for c in flat_tree if c["isLastLevel"]
            ][:top_n]
    else:
        print(f"\n⚠️  LLM 未启用（未配置 llm.endpoint），显示前 {top_n} 个末级类目")
        top_categories = [
            {"cid": c["cid"], "breadcrumb": c["breadcrumb"], "reason": "（LLM 未启用，仅显示前 N 个）"}
            for c in flat_tree if c["isLastLevel"]
        ][:top_n]

    # ---- 4. 阶段三：对每个候选类目获取属性 ----
    all_metadata = {}
    for cat in top_categories:
        cid = cat["cid"]
        print(f"\n正在获取类目属性: cid={cid}")
        meta_result = client.get_category_metadata(args.site.upper(), cid, shop_ids)
        if meta_result.get("result") == "success":
            metadata = meta_result.get("data", {}).get("categoryMetadata", {})
            all_metadata[cid] = metadata
            sale_count = len(metadata.get("categorySaleAttrList", []))
            prod_count = len(metadata.get("categoryProductAttrList", []))
            print(f"  销售属性 {sale_count} 项，商品属性 {prod_count} 项")
        else:
            print(f"  ⚠️  获取失败: {meta_result.get('message', '')}")
            all_metadata[cid] = {}

    # ---- 5. 阶段四：LLM 匹配属性值 ----
    if llm_client.enabled:
        print(f"\n正在调用 LLM 匹配属性值...")
        for cat in top_categories:
            cid = cat["cid"]
            metadata = all_metadata.get(cid, {})
            if not metadata:
                continue
            prompt = build_stage4_prompt(
                title, description,
                cid, cat["breadcrumb"],
                metadata,
            )
            llm_output = llm_client.call(prompt)
            parsed = parse_llm_json(llm_output)
            if parsed and "recommended_attributes" in parsed:
                cat["recommended_attributes"] = parsed["recommended_attributes"]
            else:
                print(f"  ⚠️  类目 {cid} LLM 属性匹配失败，使用空结果")
                cat["recommended_attributes"] = []
    else:
        print(f"\n⚠️  LLM 未启用，跳过属性匹配")
        for cat in top_categories:
            cat["recommended_attributes"] = []

    # ---- 6. 输出结果 ----
    print_match_result(
        title, description,
        top_categories, all_metadata,
        llm_client,
        include_optional=args.include_optional,
        output_json=args.output_json,
    )

    return {
        "product": {"title": title, "description": description, "images": images},
        "recommendations": top_categories,
        "all_metadata": all_metadata,
    }


def cmd_tree(client: TikTokCategoryMatchClient, args):
    """展示类目树。"""
    print(f"正在获取类目树: site={args.site}")
    result = client.get_category_tree(args.site.upper())
    flat = client.flatten_category_tree(result)
    print_category_tree(flat, args.site.upper())
    return flat


def cmd_attributes(client: TikTokCategoryMatchClient, args):
    """查询单个类目属性（调试用）。"""
    shop_ids = [int(s.strip()) for s in args.shop_ids.split(",") if s.strip()]
    print(f"获取类目属性: site={args.site}, cid={args.cid}, shops={shop_ids}")
    result = client.get_category_metadata(args.site.upper(), args.cid, shop_ids)
    print_category_metadata(result, args.cid, args.site.upper())
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Miaoshou ERP - TikTok 类目属性 AI 匹配工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # AI 匹配（从采集箱获取商品信息）
  python tiktok_category_match.py match --detail-id 12345 --site US --shop-ids 1001

  # AI 匹配（手动提供商品信息）
  python tiktok_category_match.py match --site US --shop-ids 1001 \\
      --title "Summer Floral Print Maxi Dress Women" \\
      --description "Elegant floral dress with v-neck..."

  # AI 匹配（店铺模式 + 包含选填属性）
  python tiktok_category_match.py match --detail-id 12345 --mode shop \\
      --shop-id 1001 --site US --shop-ids 1001 --include-optional

  # 展示类目树
  python tiktok_category_match.py tree --site US

  # 查询单个类目属性（调试用）
  python tiktok_category_match.py attributes --site US --cid 2000012345 --shop-ids 1001
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ---- match ----
    match_parser = subparsers.add_parser("match", help="AI 匹配类目 + 属性值")
    src = match_parser.add_argument_group("商品信息来源（必选其一）")
    src.add_argument("--detail-id", type=int, help="采集箱商品ID")
    src.add_argument("--mode", type=str, choices=["site", "shop"], default="site",
                     help="detail 模式: site 或 shop（默认 site）")
    src.add_argument("--shop-id", type=int, help="店铺ID（shop 模式下必填）")
    src.add_argument("--title", type=str, help="商品标题（二选一）")
    src.add_argument("--description", type=str, help="商品描述（配合 --title）")

    match_parser.add_argument("--site", type=str, required=True, help="站点代码（US/MY/SG/PH 等）")
    match_parser.add_argument("--shop-ids", type=str, required=True,
                              help="店铺ID列表（逗号分隔，用于获取类目属性）")
    match_parser.add_argument("--top-n", type=int, default=3, help="输出推荐类目数量（默认3）")
    match_parser.add_argument("--include-optional", action="store_true",
                              help="是否包含选填属性")
    match_parser.add_argument("--output-json", action="store_true",
                              help="输出原始 JSON")

    # ---- tree ----
    tree_parser = subparsers.add_parser("tree", help="展示类目树")
    tree_parser.add_argument("--site", type=str, required=True, help="站点代码")
    tree_parser.add_argument("--keyword", type=str, help="关键词过滤（显示匹配面包屑的类目）")

    # ---- attributes ----
    attr_parser = subparsers.add_parser("attributes", help="查询单个类目属性（调试用）")
    attr_parser.add_argument("--site", type=str, required=True, help="站点代码")
    attr_parser.add_argument("--cid", type=int, required=True, help="类目ID")
    attr_parser.add_argument("--shop-ids", type=str, required=True,
                             help="店铺ID列表（逗号分隔）")

    parser.add_argument("--raw", action="store_true", help="Output raw JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    config = load_config()
    client = TikTokCategoryMatchClient(config)
    llm_client = LLMClient(config)

    if args.command == "match":
        data = cmd_match(client, llm_client, args)
    elif args.command == "tree":
        data = cmd_tree(client, args)
    elif args.command == "attributes":
        data = cmd_attributes(client, args)
    else:
        parser.print_help()
        sys.exit(0)

    if args.raw and args.command != "tree":
        print("\n--- RAW JSON ---")
        print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

