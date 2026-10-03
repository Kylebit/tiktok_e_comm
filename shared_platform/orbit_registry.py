"""Single source of truth for Orbit V1 navigation and local services."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
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
    NavigationItem("catalog", "商品目录", "/catalog", "primary", "已有商品与成本"),
    NavigationItem("product", "商品上架", "/product-workspace", "primary", "商品队列与上品审批"),
    NavigationItem("supply-chain", "供应链", "/supply-chain/", "primary", "仓库、库存与补货"),
    NavigationItem("data", "利润", "/profit", "primary", "结算、成本与数据质量"),
    NavigationItem("knowledge", "知识工具", "/knowledge", "primary", "工具、Skill 与使用方法"),
)


WORKSPACES: tuple[WorkspaceSpec, ...] = (
    WorkspaceSpec(
        "product",
        "商品运营",
        "商品主数据、SKU、上品流程与数据库维护。",
        (
            WorkspaceLink("自动上品", "/new-product", "从采集箱开始完成商品资料、AI 图片与发布前确认"),
            WorkspaceLink("商品目录", "/catalog", "浏览与同步共享商品目录"),

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
        (
            WorkspaceLink("库存与补货", "/supply-chain/", "按快照条件判断；批次确认支持显式导出与预览导入"),
            WorkspaceLink("入库批次", "/supply-chain/inbound-batches.html", "保留现有入库视图和本地覆盖"),
        ),
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


def _composed_tools(repository: Path, cards: list[dict]) -> tuple[list[dict], dict]:
    """Project the verified machine catalog without provider/profile discovery."""
    import hashlib
    from shared_platform import capability_runtime as runtime
    cards=[dict(row) for row in cards]
    try:
        manifest=runtime.validate_runtime(repository)
        source=runtime.catalog(repository)
        entries={row['id']:row for row in source['skills']+source['tools']}
        for key,title in [('lingshi','灵识 AI 参数预览'),('publication-knowledge','商品发布知识快照')]:
            if not any(card['id']==key for card in cards):cards.append({'id':key,'title':title})
        statuses={'PORTABLE_OFFLINE_ENTRY':'离线入口可用 · 账号未核验',
            'PORTABLE_REVIEWED_SNAPSHOT_ENTRY':'需核准来源与固定版本',
            'PENDING_B4B_COMPOSITION':'流程待组合', 'WORKFLOW_RUNTIME_REQUIRED':'需要完整业务工程',
            'HOST_DISCOVERY_REQUIRED':'需要宿主插件与配置','EXTERNAL_PLUGIN':'需要宿主插件与配置',
            'STUB_NOT_IMPLEMENTED':'尚未实现','OPTIONAL_NOT_STARTED':'尚未接入','RETIRED':'已退役'}
        for card in cards:
            row=entries.get(card['id'])
            if row is None:
                card.update(status='尚未接入统一目录',stage='UNREGISTERED',method='')
                continue
            missing=[name for name in row.get('required_files',[]) if not runtime.checked_path(repository,name).is_file()]
            changed=[name for name,sha in row.get('file_digests',{}).items()
                     if runtime.checked_path(repository,name).is_file() and hashlib.sha256(runtime.file_content(runtime.checked_path(repository,name))).hexdigest()!=sha]
            status=statuses.get(row['stage'],'使用前核对依赖与权限')
            if changed:status='来源文件不符'
            elif missing and row['stage'] not in {'PENDING_B4B_COMPOSITION','HOST_DISCOVERY_REQUIRED','EXTERNAL_PLUGIN'}:status='缺少运行依赖'
            method=row.get('method','')
            if card['id'] in {'duoplus','tikhub','lingshi'}:
                method=f"python R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json doctor --capability {card['id']}\npython R/scripts/orbit_tools.py --runtime-root R help {card['id']}"
            elif card['id']=='publication-knowledge':
                method='python R/scripts/sync_product_publication_knowledge.py export-preview --runtime-root R --project-root P --profile knowledge.json\npython R/scripts/sync_product_publication_knowledge.py inspect --runtime-root R --project-root P --snapshot version.json --expected-version sha256:PIN --query QUERY'
            descriptions={'duoplus':'预览设备与应用参数；真实查询需要设备权限和独立凭据。安装复用精确范围的有效授权；未知结果恢复原记录并只读核对，不自动重装，包名存在不证明版本。',
                'tikhub':'查询商品与视频参考样本。离线预览不计费；真实查询需要凭据与明确预算，结果只作参考。',
                'lingshi':'离线核对模型请求参数；模型可用性、价格和账号权限需要单独核验。',
                'publication-knowledge':'从明确来源导出核准的知识快照，按任务固定版本加载与检索；新版本不会替换旧任务。'}
            description=descriptions.get(card['id']) or row.get('purpose') or card.get('description','按本次任务核对使用条件。')
            if row['stage']=='PENDING_B4B_COMPOSITION':description+=' 当前发布或折扣流程尚待接入。'
            elif missing:description+=' 缺少运行所需文件，请先补齐已核准的依赖。'
            brief = {field: row.get(field, '') for field in
                     ('purpose', 'inputs', 'outputs', 'side_effects', 'recovery')}
            brief['source_path'] = row.get('source_path', card.get('asset') or '')
            card.update(stage=row['stage'],status=status,method=method,description=description,
                missing_files=missing,changed_files=changed,account_verified=False,
                source=row['source'],brief=brief)
        return cards,{'state':'VERIFIED_LOCAL_CODE_ONLY','runtime_digest':manifest['digest'],'network_call_performed':False}
    except (OSError,ValueError,KeyError,TypeError):
        for card in cards:card.update(status='工具包未核验',stage='UNVERIFIED',method='',description='请核对明确工程的工具包版本与文件；当前不能确认入口可用。',account_verified=False)
        return cards,{'state':'UNVERIFIED','network_call_performed':False}


def navigation_payload(*, root: Path | None = None) -> dict:
    from shared_platform.capability_runtime import checked_path
    repository = checked_path(Path(root) if root is not None else Path(__file__).absolute().parents[1], '.').resolve()
    catalog = json.loads((repository / "shared_platform/entry_catalog.json").read_text(encoding="utf-8"))
    for item in catalog["capabilities"]:
        asset = item.get("asset")
        item["asset_available"] = bool(asset and (repository / asset).is_file())
        item["entry_available"] = bool(item.get("href") and item["asset_available"])
        item["status"] = ("历史快照" if item["kind"] == "snapshot" else "历史工具" if item["kind"] == "legacy" else "页面可打开") if item["entry_available"] else {
            "pending": "待建设", "partial": "部分实现", "method": "按需配置", "service": "待核验服务"
        }.get(item["kind"], "缺少页面资源")
    tools,tool_catalog = _composed_tools(repository,catalog['tools'])
    return {
        "regions": catalog["regions"], "capabilities": catalog["capabilities"],
        "tools": tools, "tool_catalog": tool_catalog, "flow": catalog["flow"],
        "catalog_scope": (
            f"{len(catalog['capabilities'])} 项历史能力清单，仅供检索；"
            "一级入口以 navigation 的五项为准，页面资源存在不等于业务已验证。"
        ),
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
