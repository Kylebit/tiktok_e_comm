"""Plan and normalize external 1688/EchoTik intelligence requests."""

from __future__ import annotations

from datetime import datetime, timezone

from modules.sourcing.linkfox_client import LIVELYHIVE_REGIONS, LinkfoxClient

REGION_ORDER = ("PH", "MY", "TH", "VN")
MAX_CALLS_PER_RUN = 10


def build_intel_plan(
    *,
    keyword_cn: str,
    region_keywords: dict[str, str] | None = None,
    page_size: int = 20,
    image_url: str | None = None,
    new_rank_date: str | None = None,
) -> dict:
    keyword_cn = str(keyword_cn or "").strip()
    if not keyword_cn:
        raise ValueError("keyword_cn is required")
    if not 10 <= int(page_size) <= 20:
        raise ValueError("page_size must be between 10 and 20")
    if new_rank_date:
        try:
            datetime.strptime(new_rank_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("new_rank_date must use YYYY-MM-DD") from exc
    region_keywords = {str(k).upper(): str(v).strip() for k, v in (region_keywords or {}).items()}
    unknown = set(region_keywords) - set(LIVELYHIVE_REGIONS)
    if unknown:
        raise ValueError(f"unsupported regions: {', '.join(sorted(unknown))}")

    calls = []
    for region in REGION_ORDER:
        payload = {
            "region": region,
            "categoryKeywordCN": keyword_cn,
            "productSortField": 5,
            "sortType": 1,
            "pageNum": 1,
            "pageSize": page_size,
        }
        if region_keywords.get(region):
            payload["keyword"] = region_keywords[region]
        calls.append({"id": f"echotik_products_{region}", "tool": "echotik_product_search", "payload": payload})

    calls.append(
        {
            "id": "alibaba1688_suppliers",
            "tool": "dld_product_search",
            "payload": {
                "keyWord": keyword_cn,
                "cycle": "30",
                "sortField": "saleCount30d",
                "sortType": "desc",
                "pageIndex": 1,
                "pageSize": page_size,
            },
        }
    )
    if image_url:
        calls.append(
            {
                "id": "alibaba1688_image_matches",
                "tool": "alibaba1688_image_search",
                "payload": {
                    "imageUrl": image_url,
                    "page": 1,
                    "pageSize": page_size,
                    "filter": "certifiedFactory,qrr5,isOnePsale",
                    "sort": '{"monthSold":"desc"}',
                },
            }
        )
    if new_rank_date:
        for region in REGION_ORDER:
            calls.append(
                {
                    "id": f"echotik_new_rank_{region}",
                    "tool": "echotik_new_product_rank",
                    "payload": {
                        "date": new_rank_date,
                        "region": region,
                        "pageNum": 1,
                        "pageSize": page_size,
                    },
                }
            )
    if len(calls) > MAX_CALLS_PER_RUN:
        raise ValueError(f"plan exceeds maximum of {MAX_CALLS_PER_RUN} paid calls")
    return {
        "schema": "orbit_hive.external_intel_plan.v1",
        "mode": "preview_only_no_network",
        "provider": "linkfox_controlled_adapter",
        "keyword_cn": keyword_cn,
        "regions": list(REGION_ORDER),
        "call_count": len(calls),
        "calls": calls,
        "safety": {
            "feedback_api": "disabled_not_implemented",
            "local_image_upload": "disabled",
            "paid_execution": "requires_explicit_flag_and_environment_key",
        },
    }


def run_intel_plan(plan: dict, *, client: LinkfoxClient | None = None, allow_paid: bool = False) -> dict:
    calls = list(plan.get("calls") or [])
    if len(calls) > MAX_CALLS_PER_RUN:
        raise ValueError(f"plan exceeds maximum of {MAX_CALLS_PER_RUN} paid calls")
    client = client or LinkfoxClient()
    if not allow_paid:
        return {
            **plan,
            "requests": [client.preview(call["tool"], call["payload"]) for call in calls],
        }

    responses = {}
    cost_tokens = 0
    for call in calls:
        response = client.execute(call["tool"], call["payload"], allow_paid=True)
        responses[call["id"]] = response
        try:
            cost_tokens += int(response.get("costToken") or 0)
        except (TypeError, ValueError):
            pass
    return {
        **plan,
        "mode": "executed_paid_read_only",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "cost_tokens_total": cost_tokens,
        "responses": responses,
        "normalized": normalize_intel(responses),
    }


def normalize_intel(responses: dict) -> dict:
    markets = {}
    for region in REGION_ORDER:
        response = responses.get(f"echotik_products_{region}") or {}
        products = list(response.get("products") or [])
        sales_30d = [int(x.get("totalSale30dCnt") or 0) for x in products]
        markets[region] = {
            "product_count": len(products),
            "top_30d_sales": max(sales_30d, default=0),
            "total_30d_sales": sum(sales_30d),
            "products": products,
        }

    supply_response = responses.get("alibaba1688_suppliers") or {}
    image_response = responses.get("alibaba1688_image_matches") or {}
    suppliers = list(supply_response.get("products") or [])
    image_matches = list(image_response.get("products") or [])
    prices = []
    for item in suppliers + image_matches:
        try:
            prices.append(float(item.get("price")))
        except (TypeError, ValueError):
            continue
    return {
        "markets": markets,
        "market_count_with_results": sum(1 for item in markets.values() if item["product_count"]),
        "supply": {
            "supplier_count": len(suppliers),
            "image_match_count": len(image_matches),
            "min_price_cny": min(prices, default=None),
            "products": suppliers,
            "image_matches": image_matches,
        },
    }
