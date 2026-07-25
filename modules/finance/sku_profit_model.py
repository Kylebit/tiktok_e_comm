# -*- coding: utf-8 -*-
"""SKU 即时利润：先验（Treasury 定价公式正向）+ 后验（同款已结单校准）。

广告默认统一 22%；Ads API 实耗分摊列为后续待办。
规则写回 SEA_REGION_RULES 必须人工确认，本模块只出建议。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

# 拍板：统一 22%；后续再做 Ads 实耗分摊（很难，单独待办）
DEFAULT_AD_RATE = 0.22
MIN_POSTERIOR_SAMPLES = 5
DEFAULT_LOOKBACK_DAYS = 14


@dataclass
class PriorBreakdown:
    sale_local: float
    goods_local: float
    logistics_local: float
    commission_local: float
    transaction_local: float
    extra_local: float
    creator_local: float
    affiliate_local: float
    ad_local: float
    seller_tax_local: float
    fixed_fee_local: float
    extra_cap_hit: bool

    def deductions(self, *, include_creator: bool, include_tax: bool) -> float:
        total = (
            self.goods_local
            + self.logistics_local
            + self.commission_local
            + self.transaction_local
            + self.extra_local
            + self.affiliate_local
            + self.ad_local
            + self.fixed_fee_local
        )
        if include_creator:
            total += self.creator_local
        if include_tax:
            total += self.seller_tax_local
        return total

    def as_dict(self) -> dict[str, Any]:
        return {
            "sale_local": round(self.sale_local, 2),
            "goods_local": round(self.goods_local, 2),
            "logistics_local": round(self.logistics_local, 2),
            "commission_local": round(self.commission_local, 2),
            "transaction_local": round(self.transaction_local, 2),
            "extra_local": round(self.extra_local, 2),
            "creator_local": round(self.creator_local, 2),
            "affiliate_local": round(self.affiliate_local, 2),
            "ad_local": round(self.ad_local, 2),
            "seller_tax_local": round(self.seller_tax_local, 2),
            "fixed_fee_local": round(self.fixed_fee_local, 2),
            "extra_cap_hit": self.extra_cap_hit,
        }


def profit_from_settlement(
    *,
    settlement_local: float,
    sale_local: float,
    cost_cny: float,
    fx_cny_per_local: float,
    ad_rate: float = DEFAULT_AD_RATE,
) -> dict[str, float]:
    """统一后验口径：π = settle*fx − (sale*ad%)*fx − cost。"""
    ad_local = float(sale_local) * float(ad_rate)
    profit_cny = (
        float(settlement_local) * fx_cny_per_local
        - ad_local * fx_cny_per_local
        - float(cost_cny)
    )
    profit_local = float(settlement_local) - ad_local - (
        float(cost_cny) / fx_cny_per_local if fx_cny_per_local else 0.0
    )
    margin = (profit_local / sale_local * 100.0) if sale_local else None
    return {
        "settlement_local": round(float(settlement_local), 2),
        "ad_local": round(ad_local, 2),
        "ad_rate": float(ad_rate),
        "profit_local": round(profit_local, 2),
        "profit_cny": round(profit_cny, 2),
        "margin_pct": round(margin, 2) if margin is not None else None,
    }


def summarize_nums(values: list[float]) -> dict[str, Any] | None:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    s = sorted(nums)
    n = len(s)
    return {
        "n": n,
        "mean": round(statistics.mean(s), 2),
        "median": round(statistics.median(s), 2),
        "p25": round(s[max(0, n // 4)], 2),
        "p75": round(s[min(n - 1, (3 * n) // 4)], 2),
        "min": round(s[0], 2),
        "max": round(s[-1], 2),
    }


def is_outlier_comp(comp: dict[str, Any]) -> bool:
    """异常结算：比为 0 或极低/负，不进中位。"""
    ratio = comp.get("settle_ratio")
    settle = float(comp.get("settlement_local") or 0)
    if settle <= 0:
        return True
    if ratio is not None and float(ratio) < 0.1:
        return True
    return False


def enrich_comp(
    *,
    order_id: str,
    statement_date: str,
    sale_local: float,
    settlement_local: float,
    cost_cny: float,
    fx: float,
    ad_rate: float = DEFAULT_AD_RATE,
    commission_local: float = 0.0,
    affiliate_local: float = 0.0,
    ship_net_local: float = 0.0,
    source: str = "",
) -> dict[str, Any]:
    sale = float(sale_local or 0)
    settle = float(settlement_local or 0)
    ratio = (settle / sale) if sale else None
    aff = abs(float(affiliate_local or 0))
    profit = profit_from_settlement(
        settlement_local=settle,
        sale_local=sale,
        cost_cny=cost_cny,
        fx_cny_per_local=fx,
        ad_rate=ad_rate,
    )
    return {
        "order_id": order_id,
        "statement_date": statement_date,
        "source": source,
        "sale_local": round(sale, 2),
        "settlement_local": round(settle, 2),
        "settle_ratio": round(ratio, 4) if ratio is not None else None,
        "commission_local": round(float(commission_local or 0), 2),
        "affiliate_local": round(aff, 2),
        "has_affiliate": aff >= 0.01,
        "ship_net_local": round(float(ship_net_local or 0), 2),
        "outlier": False,
        "profit_cny": profit["profit_cny"],
        "profit_local": profit["profit_local"],
        "margin_pct": profit["margin_pct"],
    }


def mark_outliers(comps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in comps:
        row = dict(c)
        row["outlier"] = is_outlier_comp(row)
        out.append(row)
    return out


def apply_affiliate_quantile_split(comps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """无真实达人字段时：按结算比中位拆高费/低费两档，保证两情景都有样本。"""
    usable = [c for c in comps if not c.get("outlier") and c.get("settle_ratio") is not None]
    if len(usable) < 2:
        return comps
    ratios = sorted(float(c["settle_ratio"]) for c in usable)
    med = statistics.median(ratios)
    out = []
    for c in comps:
        row = dict(c)
        if row.get("settle_ratio") is not None and not row.get("outlier"):
            # 低于中位 → 近似有达人/高费
            row["has_affiliate"] = float(row["settle_ratio"]) < med
            row["affiliate_approx"] = True
            row["affiliate_split_median_ratio"] = round(med, 4)
        out.append(row)
    return out


def split_comps(comps: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    usable = [c for c in comps if not c.get("outlier")]
    return {
        "all": usable,
        "with_affiliate": [c for c in usable if c.get("has_affiliate")],
        "no_affiliate": [c for c in usable if not c.get("has_affiliate")],
    }


def estimate_from_ratio(
    *,
    sale_local: float,
    comps: list[dict[str, Any]],
    cost_cny: float,
    fx: float,
    ad_rate: float = DEFAULT_AD_RATE,
) -> dict[str, Any] | None:
    ratios = [float(c["settle_ratio"]) for c in comps if c.get("settle_ratio") is not None]
    if not ratios:
        return None
    med = statistics.median(ratios)
    settle = float(sale_local) * med
    profit = profit_from_settlement(
        settlement_local=settle,
        sale_local=sale_local,
        cost_cny=cost_cny,
        fx_cny_per_local=fx,
        ad_rate=ad_rate,
    )
    profits = [float(c["profit_cny"]) for c in comps]
    ships = [float(c.get("ship_net_local") or 0) for c in comps]
    inferred_n = sum(1 for c in comps if c.get("affiliate_approx"))
    if inferred_n == len(comps):
        evidence_basis = "inferred_settlement_ratio"
    elif inferred_n:
        evidence_basis = "mixed_observed_and_inferred"
    else:
        evidence_basis = "observed_settlement"
    return {
        "n": len(comps),
        "evidence_basis": evidence_basis,
        "median_settle_ratio": round(med, 4),
        "est_settlement_local": round(settle, 2),
        **profit,
        "comp_profit_stats": summarize_nums(profits),
        "comp_ship_stats": summarize_nums(ships),
    }


def weighted_settle_ratio(comps: list[dict[str, Any]]) -> float | None:
    """按近单加权：越新权重越高（线性 1..n）。"""
    usable = [c for c in comps if c.get("settle_ratio") is not None and not c.get("outlier")]
    if not usable:
        return None
    n = len(usable)
    num = 0.0
    den = 0.0
    for i, c in enumerate(usable):
        w = float(n - i)
        num += float(c["settle_ratio"]) * w
        den += w
    return (num / den) if den else None


def build_three_scenarios(
    *,
    sale_local: float,
    cost_cny: float,
    fx: float,
    comps: list[dict[str, Any]],
    prior_with_creator: dict[str, Any] | None,
    prior_no_creator: dict[str, Any] | None,
    ad_rate: float = DEFAULT_AD_RATE,
) -> dict[str, Any]:
    """拍板：三个都展示 — 有达人 / 无达人 / 最近加权。"""
    groups = split_comps(comps)
    with_aff = estimate_from_ratio(
        sale_local=sale_local,
        comps=groups["with_affiliate"],
        cost_cny=cost_cny,
        fx=fx,
        ad_rate=ad_rate,
    )
    no_aff = estimate_from_ratio(
        sale_local=sale_local,
        comps=groups["no_affiliate"],
        cost_cny=cost_cny,
        fx=fx,
        ad_rate=ad_rate,
    )

    w_ratio = weighted_settle_ratio(groups["all"])
    if w_ratio is not None:
        settle = sale_local * w_ratio
        weighted = {
            "n": len(groups["all"]),
            "weighted_settle_ratio": round(w_ratio, 4),
            "est_settlement_local": round(settle, 2),
            **profit_from_settlement(
                settlement_local=settle,
                sale_local=sale_local,
                cost_cny=cost_cny,
                fx_cny_per_local=fx,
                ad_rate=ad_rate,
            ),
            "comp_profit_stats": summarize_nums([float(c["profit_cny"]) for c in groups["all"]]),
        }
    else:
        weighted = None

    if with_aff is None and prior_with_creator:
        with_aff = {"n": 0, "source": "prior_fallback", **prior_with_creator}
    if no_aff is None and prior_no_creator:
        no_aff = {"n": 0, "source": "prior_fallback", **prior_no_creator}
    if weighted is None:
        fallback = prior_with_creator or prior_no_creator
        if fallback:
            weighted = {"n": 0, "source": "prior_fallback", **fallback}

    return {
        "with_affiliate": with_aff,
        "no_affiliate": no_aff,
        "recent_weighted": weighted,
        "sample_counts": {
            "all_usable": len(groups["all"]),
            "with_affiliate": len(groups["with_affiliate"]),
            "no_affiliate": len(groups["no_affiliate"]),
            "outliers": sum(1 for c in comps if c.get("outlier")),
        },
    }


def pick_main_conclusion(
    *,
    scenarios: dict[str, Any],
    usable_n: int,
    min_samples: int = MIN_POSTERIOR_SAMPLES,
) -> dict[str, Any]:
    """有足够近单 → 最近加权后验；否则 → 有达人情景（更保守）。"""
    weighted = scenarios.get("recent_weighted")
    with_aff = scenarios.get("with_affiliate")
    if usable_n >= min_samples and weighted and weighted.get("n", 0) > 0:
        return {
            "key": "recent_weighted",
            "label": "最近加权（后验）",
            "confidence": "posterior",
            **weighted,
        }
    if with_aff:
        inferred = with_aff.get("evidence_basis") == "inferred_settlement_ratio"
        return {
            "key": "with_affiliate",
            "label": (
                "高费情景（结算比分位推断）"
                if inferred
                else "有达人（样本不足时保守）"
            ),
            "confidence": "prior_or_sparse",
            **with_aff,
        }
    no_aff = scenarios.get("no_affiliate") or {}
    return {
        "key": "no_affiliate",
        "label": "无达人",
        "confidence": "sparse",
        **no_aff,
    }


def tk_prior_breakdown(
    *,
    sale_local: float,
    cost_cny: float,
    weight_kg: float,
    fx_cny_per_local: float,
    ad_rate: float = DEFAULT_AD_RATE,
) -> PriorBreakdown:
    """Treasury TH 定价公式正向展开（广告率可覆盖为 22%）。"""
    from modules.sourcing.new_product_workbench import (
        SEA_REGION_RULES,
        _capped_fee,
        _sea_logistics_local,
    )

    rule = SEA_REGION_RULES["TH"]
    goods = float(cost_cny) / float(fx_cny_per_local) if fx_cny_per_local else 0.0
    logistics = _sea_logistics_local("TH", float(weight_kg) * 1000.0)
    sale = float(sale_local)
    commission = sale * rule.commission_rate
    transaction = sale * rule.transaction_rate
    extra, cap_hit = _capped_fee(sale, rule.extra_rate, rule.extra_cap_local)
    creator = sale * rule.creator_rate
    affiliate = sale * rule.affiliate_rate
    ad = sale * float(ad_rate)
    tax = sale * rule.seller_tax_rate
    return PriorBreakdown(
        sale_local=sale,
        goods_local=goods,
        logistics_local=logistics,
        commission_local=commission,
        transaction_local=transaction,
        extra_local=extra,
        creator_local=creator,
        affiliate_local=affiliate,
        ad_local=ad,
        seller_tax_local=tax,
        fixed_fee_local=rule.fixed_fee_local,
        extra_cap_hit=cap_hit,
    )


def prior_profit_from_breakdown(
    bd: PriorBreakdown,
    *,
    include_creator: bool,
    include_tax: bool,
    fx: float,
) -> dict[str, Any]:
    ded = bd.deductions(include_creator=include_creator, include_tax=include_tax)
    profit_local = bd.sale_local - ded
    profit_cny = profit_local * fx
    margin = (profit_local / bd.sale_local * 100.0) if bd.sale_local else None
    settle_est = bd.sale_local - (
        bd.commission_local
        + bd.transaction_local
        + bd.extra_local
        + bd.logistics_local
        + bd.fixed_fee_local
        + (bd.creator_local if include_creator else 0.0)
        + (bd.affiliate_local if include_creator else 0.0)
        + (bd.seller_tax_local if include_tax else 0.0)
    )
    return {
        "est_settlement_local": round(settle_est, 2),
        "profit_local": round(profit_local, 2),
        "profit_cny": round(profit_cny, 2),
        "margin_pct": round(margin, 2) if margin is not None else None,
        "include_creator": include_creator,
        "include_tax": include_tax,
        "breakdown": bd.as_dict(),
    }


def reprice_scenarios(
    *,
    sale_local: float,
    cost_cny: float,
    fx: float,
    comps: list[dict[str, Any]],
    prior_with: dict[str, Any] | None,
    prior_no: dict[str, Any] | None,
    ad_rate: float,
    label: str,
) -> dict[str, Any]:
    """同一套近单/先验，换一个售价重算三情景（用于挂牌价 vs 近单均价）。"""
    scenarios = build_three_scenarios(
        sale_local=sale_local,
        cost_cny=cost_cny,
        fx=fx,
        comps=comps,
        prior_with_creator=prior_with,
        prior_no_creator=prior_no,
        ad_rate=ad_rate,
    )
    usable_n = scenarios["sample_counts"]["all_usable"]
    main = pick_main_conclusion(scenarios=scenarios, usable_n=usable_n)
    return {
        "label": label,
        "sale_local": round(sale_local, 2),
        "scenarios": scenarios,
        "main": main,
    }


def suggest_rule_tweaks(
    *,
    model_logistics_local: float,
    comps: list[dict[str, Any]],
    fx_settings: float | None,
    fx_live: float,
    main_margin_pct: float | None = None,
    target_margin_pct: float = 15.0,
) -> list[dict[str, str]]:
    """只出建议，不改 SEA_REGION_RULES。"""
    tips: list[dict[str, str]] = []
    usable = [c for c in comps if not c.get("outlier")]
    if usable:
        ships = [float(c.get("ship_net_local") or 0) for c in usable]
        med_ship = statistics.median(ships) if ships else 0.0
        if model_logistics_local > 0 and med_ship > model_logistics_local * 1.5:
            tips.append(
                {
                    "code": "th_logistics_underestimated",
                    "title": "TH 物流模型偏低",
                    "detail": (
                        f"定价物流约 {model_logistics_local:.1f} THB，"
                        f"近单净运费中位 {med_ship:.1f} THB。建议上调 weight×0.10 或加 floor（需你确认后改规则）。"
                    ),
                }
            )
        aff_n = sum(1 for c in usable if c.get("has_affiliate") and not c.get("affiliate_approx"))
        aff_approx = sum(1 for c in usable if c.get("has_affiliate") and c.get("affiliate_approx"))
        if aff_n and aff_n / len(usable) >= 0.4:
            tips.append(
                {
                    "code": "creator_frequent",
                    "title": "达人佣金常见",
                    "detail": (
                        f"近单 {aff_n}/{len(usable)} 含真实达人扣费。"
                        f"定价可保留 creator_rate（需你确认后改规则）。"
                    ),
                }
            )
        elif aff_approx and aff_approx / len(usable) >= 0.4:
            tips.append(
                {
                    "code": "high_fee_orders_frequent",
                    "title": "高费订单常见",
                    "detail": (
                        f"近单约 {aff_approx}/{len(usable)} 结算比偏低（近似高费档）。"
                        f"建议关注运费/活动扣费（需你确认后改规则）。"
                    ),
                }
            )
    if fx_settings and fx_live and abs(fx_settings - fx_live) / fx_live > 0.05:
        tips.append(
            {
                "code": "fx_stale",
                "title": "配置汇率偏离实时",
                "detail": (
                    f"settings THB={fx_settings:.4f}，live={fx_live:.4f}。"
                    f"上架定价应强制绑实时汇率（需你确认后改）。"
                ),
            }
        )
    if main_margin_pct is not None and main_margin_pct < target_margin_pct:
        tips.append(
            {
                "code": "below_target_margin",
                "title": "低于上架目标利润率",
                "detail": (
                    f"主结论利率约 {main_margin_pct:.1f}% ，LivelyHive 目标 {target_margin_pct:.0f}%。"
                    f"可考虑提价或降费（需你确认后改规则/改价）。"
                ),
            }
        )
    tips.append(
        {
            "code": "ad_allocation_todo",
            "title": "广告分摊待办",
            "detail": "当前统一按售价×22%估广告；Ads API 实耗分摊较难，单列后续待办，不自动改规则。",
        }
    )
    return tips
