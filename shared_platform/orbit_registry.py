"""Single source of truth for Orbit V1 navigation and local services."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class NavigationItem:
    key: str
    label: str
    href: str
    level: str
    description: str


@dataclass(frozen=True)
class WorkspaceLink:
    label: str
    href: str
    description: str


@dataclass(frozen=True)
class InternalTool:
    label: str
    href: str
    description: str


@dataclass(frozen=True)
class WorkspaceSpec:
    key: str
    label: str
    description: str
    links: tuple[WorkspaceLink, ...]
    availability: str = "available"


@dataclass
class ModuleSpec:
    """Mutable runtime state around a registered local technical service."""

    key: str
    title: str
    subtitle: str
    port: int
    url: str
    health_url: str
    command: list[str]
    quick_links: list[tuple[str, str]] = field(default_factory=list)
    process: Any = None
    logs: list[str] = field(default_factory=list)


NAVIGATION: tuple[NavigationItem, ...] = (
    NavigationItem("new-product", "自动上品", "/new-product", "focus", "从采集箱到渠道草稿的新品主流程"),
    NavigationItem("profit", "利润中心", "/profit", "focus", "周度利润、数据质量与单 SKU 查询"),
    NavigationItem("overview", "总览", "/", "primary", "五域状态、审批、异常与最近任务"),
    NavigationItem("product", "商品运营", "/?view=product", "primary", "商品主数据、SKU 与上品审批"),
    NavigationItem("content", "内容运营", "/?view=content", "primary", "文案、图片与内容包"),
    NavigationItem("channel", "渠道运营", "/?view=channel", "primary", "渠道发布、促销、改价与下架"),
    NavigationItem("supply-chain", "供应链运营", "/?view=supply-chain", "primary", "供应商、仓库、库存与补货"),
    NavigationItem("data", "数据运营", "/?view=data", "primary", "结算、利润、账单与分析"),
    NavigationItem("approvals", "待我审批", "/?view=approvals", "secondary", "跨域待审批事项"),
    NavigationItem("tasks", "任务与通知", "/?view=tasks", "secondary", "后台任务、通知与回执"),
    NavigationItem("audit", "审计记录", "/?view=audit", "secondary", "跨域操作与运行审计"),
    NavigationItem("system", "系统与服务", "/?view=system", "secondary", "本地服务、端口、健康与日志"),
)


WORKSPACES: tuple[WorkspaceSpec, ...] = (
    WorkspaceSpec(
        "product",
        "商品运营",
        "商品主数据、SKU、上品流程与数据库维护。",
        (
            WorkspaceLink("自动上品", "/new-product", "从采集箱开始完成商品资料、AI 图片与发布前确认"),
            WorkspaceLink("商品目录", "/catalog", "浏览与同步共享商品目录"),
            WorkspaceLink("成本维护", "/costs", "维护现有 SKU 成本入口"),
            WorkspaceLink("1688 选品工具（旧）", "/sourcing", "保留现有选品与素材处理工具"),
        ),
    ),
    WorkspaceSpec(
        "content",
        "内容运营",
        "文案、图片与未来视频内容包。",
        (
            WorkspaceLink("标题优化", "/titles", "查看标题候选与审批队列"),
            WorkspaceLink("主图优化", "/images", "查看图片候选与处理队列"),
        ),
    ),
    WorkspaceSpec(
        "channel",
        "渠道运营",
        "TikTok、Shopee、Ozon 与妙手渠道生命周期。",
        (
            WorkspaceLink("MX 审批", "/mx", "墨西哥渠道发布审批"),
            WorkspaceLink("UK 审批", "/uk", "英国渠道发布审批"),
            WorkspaceLink("促销活动", "/promotions", "折扣与促销流程"),
            WorkspaceLink("下架巡检", "/deactivate", "低表现商品下架复核"),
            WorkspaceLink("Orbit Rus", "http://127.0.0.1:8767/", "俄罗斯与 Ozon 独立运营台"),
        ),
    ),
    WorkspaceSpec(
        "supply-chain",
        "供应链运营",
        "供应商、雅仓/Seaya、库存、入库与补货。",
        (),
        availability="planned",
    ),
    WorkspaceSpec(
        "data",
        "数据运营",
        "财务事实、运营快照、估算工具与分析入口。",
        (
            WorkspaceLink("利润中心", "/profit", "查看周度利润、数据质量与单 SKU 利润"),
            WorkspaceLink("结算中心", "/settlement", "TikTok 结算导入与汇总"),
            WorkspaceLink("SKU 利润估算探针", "/sku-profit", "单 SKU 估算与证据检查"),
            WorkspaceLink("结算账单", "/billing", "Shopee 周报与账单入口"),
            WorkspaceLink("数据分析", "/analytics", "商品分层与表现分析"),
        ),
    ),
)


INTERNAL_TOOLS: tuple[InternalTool, ...] = (
    InternalTool(
        "Release Lab",
        "/internal/release",
        "内部发布候选验收工具；不是日常业务入口",
    ),
)


def navigation_payload() -> dict[str, list[dict[str, object]]]:
    return {
        "navigation": [
            {
                "key": item.key,
                "label": item.label,
                "href": item.href,
                "level": item.level,
                "description": item.description,
            }
            for item in NAVIGATION
        ],
        "workspaces": [
            {
                "key": workspace.key,
                "label": workspace.label,
                "description": workspace.description,
                "availability": workspace.availability,
                "links": [
                    {
                        "label": link.label,
                        "href": link.href,
                        "description": link.description,
                    }
                    for link in workspace.links
                ],
            }
            for workspace in WORKSPACES
        ],
        "internal_tools": [
            {
                "label": tool.label,
                "href": tool.href,
                "description": tool.description,
            }
            for tool in INTERNAL_TOOLS
        ],
    }


def build_module_specs(root: Path, python_executable: str) -> list[ModuleSpec]:
    """Build fresh mutable service state from immutable registration data."""
    base = "http://127.0.0.1"
    return [
        ModuleSpec(
            key="os",
            title="Orbit Shared Platform",
            subtitle="CEO 总览、五域入口与共享平台 API",
            port=8765,
            url=f"{base}:8765/",
            health_url=f"{base}:8765/api/health",
            command=[python_executable, str(root / "main.py"), "serve", "--port", "8765", "--no-browser"],
            quick_links=[
                ("总览", f"{base}:8765/"),
                ("利润中心", f"{base}:8765/profit"),
                ("系统与服务", f"{base}:8765/?view=system"),
            ],
        ),
        ModuleSpec(
            key="treasury",
            title="Orbit Treasury",
            subtitle="现有新品上架与审核技术服务",
            port=8766,
            url=f"{base}:8766/",
            health_url=f"{base}:8766/health",
            command=[python_executable, str(root / "scripts" / "start_new_product_server.py"), "8766"],
            quick_links=[("新品工作台", f"{base}:8766/")],
        ),
        ModuleSpec(
            key="rus",
            title="Orbit Rus",
            subtitle="现有俄罗斯与 Ozon 技术服务",
            port=8767,
            url=f"{base}:8767/",
            health_url=f"{base}:8767/health",
            command=[python_executable, str(root / "scripts" / "start_rus_server.py"), "8767"],
            quick_links=[("俄罗斯台", f"{base}:8767/")],
        ),
    ]
