const FX_MYR_CNY = 1.659101;
const LEAD_DAYS = 25;
const TARGET_DAYS = 30;
const SAFETY_DAYS = 5;
const HEAD_FREIGHT_PER_M3 = 580;
const MIN_BILLABLE_M3 = 0.3;

const money = (value, digits = 0) => {
  const normalized = Math.abs(value) < 0.005 ? 0 : value;
  return `¥${normalized.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  })}`;
};
const number = value => Number(value || 0);
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[char]);

function outboundFee(weightG) {
  if (weightG <= 50) return 1.8;
  if (weightG <= 500) return 2.2;
  if (weightG <= 2000) return 2.5;
  if (weightG <= 5000) return 3.5;
  if (weightG <= 10000) return 5;
  return 12;
}

function shelvingFee(weightG) {
  if (weightG <= 500) return 0.15;
  if (weightG <= 5000) return 0.2;
  if (weightG <= 10000) return 0.5;
  return 1;
}

function calculateBase(item) {
  const annualVelocity = item.settlement.units > 0
    ? item.settlement.units / item.settlement.days
    : 0;
  let dailyVelocity = annualVelocity;
  let demandMethod = "全年 SKU 结算日均";
  if (item.recent30 !== null && item.recent30 !== undefined) {
    const recentVelocity = item.recent30 / 30;
    dailyVelocity = annualVelocity > 0
      ? recentVelocity * 0.7 + annualVelocity * 0.3
      : recentVelocity;
    demandMethod = annualVelocity > 0 ? "70%近30天 + 30%全年" : "近30天 SKU 日均";
  }
  const leadDemand = Math.ceil(dailyVelocity * LEAD_DAYS);
  const arrivalTarget = Math.ceil(dailyVelocity * (TARGET_DAYS + SAFETY_DAYS));
  const trusted = item.inventory.available + item.inventory.inbound;
  const projectedAtArrival = Math.max(0, trusted - leadDemand);
  const recommended = Math.max(0, arrivalTarget - projectedAtArrival);
  const volumeM3 = item.dimensionsCm.reduce((a, b) => a * b, 1) / 1e6;
  return {...item, annualVelocity, dailyVelocity, demandMethod, leadDemand, arrivalTarget,
    projectedAtArrival, recommended, volumeM3};
}

function calculateEconomics(items) {
  const batchVolume = items.reduce((sum, item) => sum + item.volumeM3 * item.recommended, 0);
  const billableVolume = items.some(item => item.recommended > 0)
    ? Math.max(MIN_BILLABLE_M3, batchVolume)
    : 0;
  const batchFreight = billableVolume * HEAD_FREIGHT_PER_M3;
  return items.map(item => {
    const units = item.settlement.units;
    const customerPaymentMyr = units ? item.settlement.customerPaymentMyr / units : 0;
    const observedShippingMyr = units ? Math.abs(item.settlement.actualShippingFeeMyr) / units : 0;
    const taxSavingUnit = customerPaymentMyr * 0.10 * FX_MYR_CNY;
    const shippingSavingUnit = observedShippingMyr * 0.20 * FX_MYR_CNY;
    const handlingUnit = outboundFee(item.weightG) + shelvingFee(item.weightG) + 0.3
      + item.volumeM3 * 10;
    const skuVolume = item.volumeM3 * item.recommended;
    const headFreightTotal = batchVolume > 0 ? batchFreight * skuVolume / batchVolume : 0;
    const headFreightUnit = item.recommended ? headFreightTotal / item.recommended : 0;
    const netUnit = taxSavingUnit + shippingSavingUnit - handlingUnit - headFreightUnit;
    const netTotal = netUnit * item.recommended;
    const status = item.dailyVelocity === 0
      ? "NO_DEMAND"
      : item.recommended === 0
        ? "HOLD"
        : units === 0 || netUnit <= 0
          ? "REVIEW"
          : "REPLENISH";
    return {...item, customerPaymentMyr, observedShippingMyr, taxSavingUnit,
      shippingSavingUnit, handlingUnit, headFreightUnit, netUnit, netTotal, status};
  });
}

let calculated = calculateEconomics(window.SKU_FACTS.map(calculateBase))
  .sort((a, b) => b.recommended - a.recommended || b.netTotal - a.netTotal || a.sku.localeCompare(b.sku));

function statusLabel(status) {
  return {REPLENISH: "建议补货", REVIEW: "待核经济性", HOLD: "库存覆盖", NO_DEMAND: "销量不足"}[status];
}

function renderRows() {
  const query = document.querySelector("#searchInput").value.trim().toLowerCase();
  const filter = document.querySelector("#statusFilter").value;
  const visible = calculated.filter(item => {
    const matchesText = !query || `${item.sku} ${item.name}`.toLowerCase().includes(query);
    return matchesText && (filter === "all" || item.status === filter);
  });
  document.querySelector("#visibleCount").textContent = `显示 ${visible.length} / ${calculated.length} 个 SKU`;
  document.querySelector("#skuRows").innerHTML = visible.map(item => `
    <tr>
      <td>
        <div class="product-cell">
          <img src="./assets/sku-${escapeHtml(item.sku)}.jpg" alt="SKU ${escapeHtml(item.sku)} 主图">
          <div><strong>${escapeHtml(item.sku)}</strong><span>${escapeHtml(item.name)}</span><small>${item.dimensionsCm.join("×")} cm · ${item.weightG} g</small></div>
        </div>
      </td>
      <td>
        <div class="stack"><b>${item.settlement.units.toLocaleString("zh-CN")} 件 / ${item.settlement.orders.toLocaleString("zh-CN")} 单</b>
          <span>全年 SKU 结算</span>
          <span>${item.recent30 === null ? `近30天为商品族 ${item.family30} 件，未强拆` : `近30天 ${item.recent30} 件`}</span>
          <em>${item.dailyVelocity.toFixed(2)} 件/天 · ${item.demandMethod}</em>
        </div>
      </td>
      <td>
        <div class="inventory-grid">
          <span>库存<b>${item.inventory.stock}</b></span><span>可用<b>${item.inventory.available}</b></span>
          <span>占用<b>${item.inventory.allocated}</b></span><span>冻结<b>${item.inventory.frozen}</b></span>
          <span>在途<b>${item.inventory.inbound}</b></span><span>仓库<b>MY8803</b></span>
        </div>
      </td>
      <td>
        <div class="calc-lines">
          <span>25天需求 <b>${item.leadDemand}</b></span>
          <span>到仓剩余 <b>${item.projectedAtArrival}</b></span>
          <span>35天目标 <b>${item.arrivalTarget}</b></span>
          <code>max(0, ${item.arrivalTarget} − ${item.projectedAtArrival})</code>
        </div>
      </td>
      <td class="recommend"><strong>${item.recommended}</strong><span>件</span><small>${item.recommended ? `${(item.volumeM3 * item.recommended).toFixed(3)} m³` : "无需新增"}</small></td>
      <td>
        <div class="economics-mini">
          <span>用户结算价 <b>RM ${item.customerPaymentMyr.toFixed(2)}</b></span>
          <span>税费节省 10% <b class="gain">${money(item.taxSavingUnit, 2)}</b></span>
          <span>运费节省 20% <b class="gain">${money(item.shippingSavingUnit, 2)}</b></span>
          <span>本土处理+头程 <b>−${money(item.handlingUnit + item.headFreightUnit, 2)}</b></span>
          <em>单件净优势 ${money(item.netUnit, 2)} · 本批 ${money(item.netTotal)}</em>
        </div>
      </td>
      <td><span class="pill ${item.status.toLowerCase()}">${statusLabel(item.status)}</span><small class="reason">${item.status === "REPLENISH" ? "需求缺口且单件净优势为正" : item.status === "REVIEW" ? "有需求缺口，但经济性证据未通过" : item.status === "HOLD" ? "现有库存可覆盖目标" : "无可审计销量，不新增"}</small></td>
    </tr>
  `).join("");
}

function renderSummary() {
  const replenish = calculated.filter(item => item.status === "REPLENISH");
  const qty = replenish.reduce((sum, item) => sum + item.recommended, 0);
  const volume = replenish.reduce((sum, item) => sum + item.volumeM3 * item.recommended, 0);
  const capital = replenish.reduce((sum, item) => sum + item.costCny * item.recommended, 0);
  const net = replenish.reduce((sum, item) => sum + item.netTotal, 0);
  const inventory = calculated.reduce((sum, item) => sum + item.inventory.available, 0);
  document.querySelector("#batchQty").textContent = qty.toLocaleString("zh-CN");
  document.querySelector("#batchSkuCount").textContent = `${replenish.length} 款`;
  document.querySelector("#batchVolume").textContent = `${volume.toFixed(3)} m³`;
  document.querySelector("#workingCapital").textContent = money(capital);
  document.querySelector("#knownBenefit").textContent = money(net);
  document.querySelector("#inventoryUnits").textContent = `${inventory.toLocaleString("zh-CN")} 件`;
  document.querySelector("#decisionHeadline").textContent = replenish.length ? `补 ${replenish.length} 款` : "暂缓补货";
  document.querySelector("#batchSentence").textContent = replenish.length
    ? `优先补 ${replenish.slice(0, 3).map(item => item.sku).join("、")}；其余 SKU 逐行说明。`
    : "当前可用库存已覆盖目标，暂不新增。";
}

document.querySelector("#searchInput").addEventListener("input", renderRows);
document.querySelector("#statusFilter").addEventListener("change", renderRows);
renderSummary();
renderRows();
