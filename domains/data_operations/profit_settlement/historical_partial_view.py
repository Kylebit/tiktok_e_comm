"""Fail-closed HTML view for the frozen Shopee TH historical partial candidate."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from typing import Any, Mapping


CANDIDATE_RELATIVE_PATH = Path(
    "artifacts/historical_settlement_candidates/2026-07/"
    "shopee_TH_20260718_20260731.partial.json"
)
EXPECTED_CANDIDATE_SHA256 = "38aad361b14bd226347038b6dabc430de01461fe23a0c343ff27efbd15269bc8"


def load_historical_partial_candidate(root: Path) -> dict[str, Any]:
    path = root / CANDIDATE_RELATIVE_PATH
    raw = path.read_bytes()
    digest = sha256(raw).hexdigest()
    if digest != EXPECTED_CANDIDATE_SHA256:
        raise ValueError("historical candidate checksum mismatch")
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("schema_version") != "shopee-historical-settlement-candidate/v1":
        raise ValueError("unsupported historical candidate schema")
    if payload.get("classification") != "HISTORICAL_ACTUAL_PARTIAL_UNVERIFIED":
        raise ValueError("historical candidate classification is not fail-closed")
    if payload.get("authoritative_for_profit") is not False:
        raise ValueError("historical candidate must not be authoritative for profit")
    rows = payload.get("rows")
    coverage = payload.get("coverage") or {}
    if not isinstance(rows, list) or len(rows) != 428:
        raise ValueError("historical candidate row count mismatch")
    if coverage.get("unique_order_count") != 409:
        raise ValueError("historical candidate order count mismatch")
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("invalid historical candidate row")
        if row.get("historical_cost_cny") is not None or row.get("fx_cny_per_local") is not None:
            raise ValueError("historical cost or FX must remain unknown")
        if row.get("profit_cny") is not None:
            raise ValueError("historical profit must remain unknown")
        if not str(row.get("order_id_digest") or "").startswith("sha256:"):
            raise ValueError("historical order identity is not redacted")
    return payload


def render_historical_partial_from_root(root: Path) -> str:
    return render_historical_partial_html(load_historical_partial_candidate(root))


def render_historical_partial_html(candidate: Mapping[str, Any]) -> str:
    rows = [row for row in candidate.get("rows", []) if isinstance(row, Mapping)]
    coverage = candidate.get("coverage") or {}
    source = candidate.get("source") or {}
    observed = candidate.get("observed_release_line_counts_site_local") or {}
    release_days = Counter(str(row.get("settled_at") or "")[:10] for row in rows)
    unique_orders = len({str(row.get("order_id_digest")) for row in rows})
    settlement_total = sum((_decimal(row.get("settlement_local")) for row in rows), Decimal("0"))
    sale_total = sum((_decimal(row.get("sale_local")) for row in rows), Decimal("0"))
    commission_total = sum((_decimal(row.get("commission_fee_local")) for row in rows), Decimal("0"))
    service_total = sum((_decimal(row.get("service_fee_local")) for row in rows), Decimal("0"))
    row_html = "".join(_render_row(row) for row in rows)
    observed_text = "、".join(
        f"{escape(str(day))}：{escape(str(count))} 行"
        for day, count in sorted(observed.items())
    )
    issues = [
        "仅覆盖留存快照声明的 2026-07-18 至 2026-07-31 UTC 窗口，不代表 7 月全月完整结算。",
        "分页完成回执缺失，平台端总量与遗漏情况未知。",
        "候选只保留 TH 地区，未独立保留或认证具体 shop_id/店铺绑定。",
        "缺少下单时间；本页筛选、每日数量和初始排序均按泰国当地结算时间。",
        "缺少平台订单行 ID、完整费用/税费/退款分解、买家现金实付、重量和主图身份。",
        "缺少历史成本快照与历史汇率快照，因此商品成本、广告费、履约费、CNY 金额、利润和利润率均为未具备。",
        "旧字段 commission_fee/service_fee 只按原快照名称展示，不能冒充完整平台费用。",
    ]
    issue_html = "".join(f"<li>{escape(issue)}</li>" for issue in issues)
    snapshot_id = escape(str(candidate.get("snapshot_id") or ""))
    source_sha = escape(str(source.get("sha256") or ""))
    pulled_at = escape(str(source.get("pulled_at") or ""))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shopee TH 历史部分结算</title><style>
body{{font:13px/1.45 system-ui,sans-serif;margin:0;background:#f5f7f8;color:#172126}}main{{padding:20px}}h1{{margin:4px 0}}.meta{{color:#64748b}}.alert{{background:#fff3cd;border:1px solid #e6b800;border-radius:10px;padding:12px;margin:12px 0}}.cards{{display:grid;grid-template-columns:repeat(5,minmax(150px,1fr));gap:10px;margin:16px 0}}.card,section{{background:#fff;border:1px solid #dfe6e9;border-radius:10px;padding:12px}}.card strong{{display:block;font-size:19px;margin-top:4px}}.unknown{{color:#a33}}.filter{{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin:10px 0;padding:10px 12px;background:#fff;border:1px solid #dfe6e9;border-radius:10px}}.filter label{{display:flex;align-items:center;gap:6px}}.filter input,.filter button{{font:inherit;padding:5px 8px;border:1px solid #b8c4ca;border-radius:6px;background:#fff}}.summary{{font-weight:700}}.daily{{display:flex;flex-wrap:wrap;gap:6px;width:100%}}.chip{{padding:3px 7px;border-radius:999px;background:#e8f4ff;color:#164e78}}.top-scroll{{overflow-x:auto;overflow-y:hidden;height:16px;margin-bottom:4px;background:#eef3f4;border:1px solid #dfe6e9;border-radius:7px}}.top-scroll>div{{height:1px}}.table{{overflow:auto;max-height:72vh;background:#fff;border:1px solid #dfe6e9;border-radius:10px}}table{{border-collapse:collapse;min-width:max-content;width:100%}}th,td{{padding:7px 9px;border-bottom:1px solid #edf1f2;text-align:left;vertical-align:top;white-space:nowrap}}th{{position:sticky;top:0;z-index:3;background:#eef3f4}}td.num{{text-align:right;font-variant-numeric:tabular-nums}}td.product{{white-space:normal;min-width:220px;max-width:300px}}tfoot td{{position:sticky;bottom:0;background:#e8f4ff;font-weight:700;border-top:2px solid #4b9bd8}}.sort{{border:0;background:transparent;font:inherit;font-weight:700;cursor:pointer}}code{{font-size:11px}}@media(max-width:800px){{.cards{{grid-template-columns:1fr 1fr}}main{{padding:10px}}}}
</style></head><body><main>
<header><span class="unknown">HISTORICAL_ACTUAL_PARTIAL_UNVERIFIED</span><h1>SHOPEE TH 历史部分结算明细</h1><p class="meta">源声明窗口 2026-07-18 至 2026-07-31（旧边界为 UTC） · {len(rows)} 个商品行 · {unique_orders} 个脱敏父订单 · 非完整 7 月利润报表</p></header>
<div class="alert"><strong>这是已冻结的真实历史部分结算，不是合成预览。</strong><br>已知结算批次：{observed_text}。页面只展示源快照实际保留的字段；未知金额不会填 0，也不会套用当前成本、当前汇率或 22% 广告估算。</div>
<div class="cards"><div class="card"><span>净结算（当地 THB）</span><strong>{_money(settlement_total)}</strong></div><div class="card"><span>商品折后成交额（当地 THB）</span><strong>{_money(sale_total)}</strong></div><div class="card"><span>商品成本 CNY</span><strong class="unknown">未具备</strong></div><div class="card"><span>汇率 / CNY 净结算</span><strong class="unknown">未具备</strong></div><div class="card"><span>利润 / 利润率</span><strong class="unknown">未具备</strong></div></div>
<section><h2>覆盖与审计限制</h2><ul>{issue_html}</ul><p class="meta">snapshot_id={snapshot_id}<br>source_sha256={source_sha}<br>pulled_at={pulled_at}<br>候选文件 SHA-256={EXPECTED_CANDIDATE_SHA256}</p></section>
<h2>订单明细（旧版列结构，已知字段按原事实展示）</h2>
<div class="filter"><label>结算日期从 <input type="date" data-role="date-start"></label><label>至 <input type="date" data-role="date-end"></label><button type="button" data-role="date-reset">清除筛选</button><span class="summary" data-role="summary"></span><span class="meta">筛选按泰国当地结算日，只改变页面显示和底部筛选合计。</span><div class="daily" data-role="daily"></div></div>
<div class="top-scroll" data-role="top-scroll"><div></div></div><div class="table" data-role="table-scroll"><table><thead><tr>
<th><button class="sort" data-sort="settled-at" aria-sort="descending">结算时间 ↓</button></th><th>下单时间</th><th>订单 ID（脱敏）</th><th>国家</th><th>发货方式</th><th>主图</th><th>Seller SKU</th><th>净结算(CNY)</th><th>商品总成本(CNY)</th><th>广告费(CNY)</th><th>本土履约费(CNY)</th><th>利润(CNY)</th><th>利润率</th><th>规格</th><th>数量</th><th>单件重量(g)</th><th>计费重量(g)</th><th>联盟营销佣金(AMS)</th><th>商品折后成交额</th><th>买家现金实付商品金额</th><th>净结算(当地)</th><th>最新汇率(CNY/当地)</th><th>汇率更新时间</th><th>汇率来源</th><th>单件成本(CNY)</th><th>广告基数(当地)</th><th>广告比例</th><th>广告比例来源</th><th>广告费(当地)</th><th>外部成本合计(CNY)</th><th>平台佣金</th><th>服务费</th><th>成本/FX/结算证据</th><th>商品名称</th>
</tr></thead><tbody>{row_html}</tbody><tfoot><tr><td data-role="total-label">合计</td>{''.join(_footer_cells(settlement_total, sale_total, commission_total, service_total))}</tr></tfoot></table></div>
<script>{_SCRIPT}</script></main></body></html>"""


def _render_row(row: Mapping[str, Any]) -> str:
    settled_at = str(row.get("settled_at") or "")
    day = settled_at[:10]
    order_id = str(row.get("order_id_digest") or "")
    short_order = order_id.replace("sha256:", "sha256:")[:19] + "…"
    candidate_row_id = str(row.get("candidate_row_id") or "")
    settlement = _decimal(row.get("settlement_local"))
    sale = _decimal(row.get("sale_local"))
    commission = _decimal(row.get("commission_fee_local"))
    service = _decimal(row.get("service_fee_local"))
    sums = escape(json.dumps({
        "settlement_local": str(settlement), "sale_local": str(sale),
        "commission_local": str(commission), "service_local": str(service),
    }, separators=(",", ":")), quote=True)
    values = [
        _display_time(settled_at), "未具备", escape(short_order), escape(str(row.get("region") or "—")),
        "未具备", "无可离线显示主图", escape(str(row.get("seller_sku") or "—")),
        "未具备", "未具备", "未具备", "未具备", "未具备", "未具备", "未具备",
        escape(str(row.get("quantity") or "—")), "未具备", "未具备", "未具备",
        f"{_money(sale)} {escape(str(row.get('currency') or ''))}", "未具备",
        f"{_money(settlement)} {escape(str(row.get('currency') or ''))}",
        "未具备", "未具备", "未具备", "未具备", "未具备", "未具备", "未具备", "未具备", "未具备",
        f"{_money(commission)} {escape(str(row.get('currency') or ''))}",
        f"{_money(service)} {escape(str(row.get('currency') or ''))}",
        "历史结算候选: 已冻结<br>成本: 未具备<br>FX: 未具备<br>费用分解: 不完整",
        escape(str(row.get("product_name") or "—")),
    ]
    cells = "".join(f'<td class="{"num" if i in {8,9,10,11,12,13,15,16,17,19,20,21,22,25,26,27,29,30,31,32} else "product" if i in {33,34} else ""}">{value}</td>' for i, value in enumerate(values))
    return f'<tr data-day="{escape(day, quote=True)}" data-settled-at="{escape(settled_at, quote=True)}" data-order-id="{escape(order_id, quote=True)}" data-tie="{escape(candidate_row_id, quote=True)}" data-sums="{sums}">{cells}</tr>'


def _footer_cells(settlement: Decimal, sale: Decimal, commission: Decimal, service: Decimal) -> list[str]:
    cells = ["<td></td>" for _ in range(33)]
    for index in (6, 7, 8, 9, 10, 11, 12, 16, 18, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29):
        cells[index] = '<td class="num unknown">未具备</td>'
    cells[17] = f'<td class="num" data-total="sale_local">{_money(sale)} THB</td>'
    cells[19] = f'<td class="num" data-total="settlement_local">{_money(settlement)} THB</td>'
    cells[29] = f'<td class="num" data-total="commission_local">{_money(commission)} THB</td>'
    cells[30] = f'<td class="num" data-total="service_local">{_money(service)} THB</td>'
    return cells


def _decimal(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")
    return number if number.is_finite() else Decimal("0")


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _display_time(value: str) -> str:
    return escape(value.replace("T", " ").replace("+07:00", "（UTC+07:00）"))


_SCRIPT = r"""
(() => {
  const top=document.querySelector('[data-role="top-scroll"]'), body=document.querySelector('[data-role="table-scroll"]'), spacer=top.firstElementChild;
  let syncing=false; const resize=()=>spacer.style.width=`${body.scrollWidth}px`;
  const mirror=(a,b)=>{if(syncing)return;syncing=true;b.scrollLeft=a.scrollLeft;requestAnimationFrame(()=>syncing=false)};
  top.addEventListener('scroll',()=>mirror(top,body),{passive:true}); body.addEventListener('scroll',()=>mirror(body,top),{passive:true}); window.addEventListener('resize',resize); resize();
  const tbody=body.querySelector('tbody'), start=document.querySelector('[data-role="date-start"]'), end=document.querySelector('[data-role="date-end"]'), summary=document.querySelector('[data-role="summary"]'), daily=document.querySelector('[data-role="daily"]'), label=body.querySelector('[data-role="total-label"]');
  const totalCells=Array.from(body.querySelectorAll('[data-total]')); const originals=new Map(totalCells.map(c=>[c,c.textContent]));
  const apply=()=>{const active=Boolean(start.value||end.value), visible=[], byDay=new Map(); Array.from(tbody.rows).forEach(row=>{const day=row.dataset.day||'', show=!active||(day&&(!start.value||day>=start.value)&&(!end.value||day<=end.value));row.hidden=!show;if(show){visible.push(row);if(!byDay.has(day))byDay.set(day,new Set());byDay.get(day).add(row.dataset.orderId)}}); const orders=new Set(visible.map(r=>r.dataset.orderId)); summary.textContent=`显示 ${orders.size} 个订单，${visible.length} 个商品行`; daily.replaceChildren(...Array.from(byDay).sort().map(([day,ids])=>{const c=document.createElement('span');c.className='chip';c.textContent=`${day}：${ids.size} 单`;return c})); if(!active){originals.forEach((v,c)=>c.textContent=v);label.textContent='合计';return} const sums={settlement_local:0,sale_local:0,commission_local:0,service_local:0}; visible.forEach(r=>{const v=JSON.parse(r.dataset.sums||'{}');Object.keys(sums).forEach(k=>sums[k]+=Number(v[k]||0))}); totalCells.forEach(c=>c.textContent=`${sums[c.dataset.total].toFixed(2)} THB`);label.textContent=visible.length?'筛选合计':'筛选无可用行'};
  start.addEventListener('input',apply);end.addEventListener('input',apply);document.querySelector('[data-role="date-reset"]').addEventListener('click',()=>{start.value='';end.value='';apply()});apply();
  const button=body.querySelector('[data-sort="settled-at"]'); button.addEventListener('click',()=>{const asc=button.getAttribute('aria-sort')!=='ascending'; const rows=Array.from(tbody.rows);rows.sort((a,b)=>{const t=(a.dataset.settledAt||'').localeCompare(b.dataset.settledAt||'');return t?(asc?t:-t):(a.dataset.tie||'').localeCompare(b.dataset.tie||'')});rows.forEach(r=>tbody.appendChild(r));button.setAttribute('aria-sort',asc?'ascending':'descending');button.textContent=`结算时间 ${asc?'↑':'↓'}`});
})();
"""
