const DATA = window.SUPPLY_CHAIN_DATA;
let activeRegion = "MY";
let calculated = [];
let batch = {};
const MANUAL_INPUT_KEY = "supply-chain-manual-logistics-v1";
let manualInputs = loadManualInputs();

const number = value => Number(value || 0);
const money = (value, digits = 0) => {
  const normalized = Math.abs(value) < 0.005 ? 0 : value;
  return `¥${normalized.toLocaleString("zh-CN", {minimumFractionDigits: digits, maximumFractionDigits: digits})}`;
};
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[char]);

function loadManualInputs() {
  try {
    const value = JSON.parse(localStorage.getItem(MANUAL_INPUT_KEY) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

function saveManualInputs() {
  localStorage.setItem(MANUAL_INPUT_KEY, JSON.stringify(manualInputs));
}

const manualInputId = (region, sku) => `${region}:${sku}`;

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

function validTrendDecision(trend, channel) {
  if (!trend || trend.method !== "segmented_7_8_15_v1") return false;
  if (!["RISING", "STABLE", "FALLING", "SPIKE"].includes(trend.trendClass)) return false;
  if (!["HIGH", "MEDIUM", "LOW"].includes(trend.confidence)) return false;
  if (typeof trend.dailyVelocity !== "number" || !Number.isFinite(trend.dailyVelocity) || trend.dailyVelocity < 0) return false;
  const units = trend.units;
  if (!units || ["last7", "days8To15", "days16To30"].some(
    key => typeof units[key] !== "number" || !Number.isFinite(units[key]) || units[key] < 0
  )) return false;
  const trendTotal = units.last7 + units.days8To15 + units.days16To30;
  return channel.recent30Units === null || channel.recent30Units === undefined
    || Math.abs(trendTotal - number(channel.recent30Units)) < 0.001;
}

function channelDemand(channel, region, platform) {
  const expectedSource = `${platform} ${region}`;
  if (typeof channel.source !== "string" || !channel.source.startsWith(expectedSource)) {
    return {daily: 0, method: "COUNTRY_MISMATCH", trendClass: null, confidence: null, trend: null};
  }
  if (channel.state && channel.state !== "READY") {
    return {daily: 0, method: "BLOCKED", trendClass: null, confidence: null, trend: null};
  }
  if (validTrendDecision(channel.trendDecision, channel)) {
    return {
      daily: channel.trendDecision.dailyVelocity,
      method: "SEGMENTED_TREND",
      trendClass: channel.trendDecision.trendClass,
      confidence: channel.trendDecision.confidence,
      trend: channel.trendDecision
    };
  }
  const longDaily = channel.days ? channel.units / channel.days : 0;
  if (channel.recent30Units === null || channel.recent30Units === undefined) {
    return {daily: longDaily, method: "LONG_WINDOW_FALLBACK", trendClass: null, confidence: null, trend: null};
  }
  return {
    daily: (channel.recent30Units / 30) * 0.7 + longDaily * 0.3,
    method: "RECENT30_FALLBACK",
    trendClass: null,
    confidence: null,
    trend: null
  };
}

function calculateCountry(region) {
  const config = DATA.config[region];
  const base = DATA.countries[region].map(item => {
    const manualInput = manualInputs[manualInputId(region, item.sku)];
    const effectiveItem = manualInput
      ? {
          ...item,
          dimensionsCm: manualInput.dimensionsCm,
          weightG: manualInput.weightG,
          costCny: manualInput.costCny,
          manualInput
        }
      : item;
    const tiktokDemand = channelDemand(effectiveItem.channels.tiktok, region, "TikTok");
    const shopeeDemand = channelDemand(effectiveItem.channels.shopee, region, "Shopee");
    const tiktokDaily = tiktokDemand.daily;
    const shopeeDaily = shopeeDemand.daily;
    const dailyVelocity = tiktokDaily + shopeeDaily;
    const dimensionsReady = Array.isArray(effectiveItem.dimensionsCm) && effectiveItem.dimensionsCm.length === 3
      && effectiveItem.dimensionsCm.every(value => typeof value === "number" && Number.isFinite(value) && value > 0)
    const weightReady = typeof effectiveItem.weightG === "number"
      && Number.isFinite(effectiveItem.weightG) && effectiveItem.weightG > 0;
    const costReady = typeof effectiveItem.costCny === "number"
      && Number.isFinite(effectiveItem.costCny) && effectiveItem.costCny > 0;
    const dataIncomplete = !dimensionsReady || !weightReady || !costReady;
    const spikeProtection = effectiveItem.kind === "first_stock" && [tiktokDemand, shopeeDemand].some(
      demand => demand.trendClass === "SPIKE"
    );
    const targetCoverageDays = spikeProtection ? 15 : config.targetDays + config.safetyDays;
    const leadDemand = Math.ceil(dailyVelocity * config.leadDays);
    const arrivalTarget = Math.ceil(dailyVelocity * targetCoverageDays);
    const trusted = number(effectiveItem.inventory.available) + number(effectiveItem.inventory.inbound);
    const projectedAtArrival = Math.max(0, trusted - leadDemand);
    const recommended = Math.max(0, arrivalTarget - projectedAtArrival);
    const volumeM3 = dimensionsReady
      ? effectiveItem.dimensionsCm.reduce((a, b) => a * number(b), 1) / 1e6
      : null;
    const weightEquivalentM3 = weightReady
      ? number(effectiveItem.weightG) / 1000 / config.weightRatioKgM3
      : null;
    const chargeableUnitM3 = dimensionsReady && weightReady
      ? Math.max(volumeM3, weightEquivalentM3)
      : null;
    return {...effectiveItem, dimensionsReady, weightReady, costReady, dataIncomplete,
      tiktokDemand, shopeeDemand, tiktokDaily, shopeeDaily, dailyVelocity,
      spikeProtection, targetCoverageDays, leadDemand, arrivalTarget,
      projectedAtArrival, recommended, volumeM3, chargeableUnitM3};
  });

  const batchMetrics = items => {
    const rawChargeableM3 = items.reduce(
      (sum, item) => sum + number(item.chargeableUnitM3) * item.recommended, 0
    );
    const physicalM3 = items.reduce(
      (sum, item) => sum + number(item.volumeM3) * item.recommended, 0
    );
    const recommendedUnits = items.reduce((sum, item) => sum + item.recommended, 0);
    return {
      rawChargeableM3,
      physicalM3,
      billableM3: rawChargeableM3,
      surcharge: 0,
      freightTotal: recommendedUnits * config.fixedHeadFreightUnitCny
    };
  };

  const economics = item => {
    const channels = Object.values(item.channels);
    const units = channels.reduce((sum, channel) => sum + number(channel.units), 0);
    const customerPayment = channels.reduce((sum, channel) => sum + number(channel.customerPayment), 0);
    const shipping = channels.reduce((sum, channel) => (
      sum + (typeof channel.actualShippingFee === "number" ? Math.abs(channel.actualShippingFee) : 0)
    ), 0);
    const customerPaymentLocal = units ? customerPayment / units : 0;
    const observedShippingLocal = units ? shipping / units : 0;
    const taxSavingUnit = customerPaymentLocal * config.taxSavingRate * config.fxToCny;
    const shippingSavingUnit = observedShippingLocal * config.shippingSavingRate * config.fxToCny;
    const handlingUnit = item.weightReady
      ? outboundFee(item.weightG) + shelvingFee(item.weightG) + 0.3
      : null;
    const headFreightUnit = config.fixedHeadFreightUnitCny;
    const netUnit = handlingUnit === null
      ? null
      : taxSavingUnit + shippingSavingUnit - handlingUnit - headFreightUnit;
    const netTotal = netUnit === null ? null : netUnit * item.recommended;
    return {units, customerPaymentLocal, observedShippingLocal, taxSavingUnit,
      shippingSavingUnit, handlingUnit, headFreightUnit, netUnit, netTotal};
  };

  const approvedItems = base.filter(
    item => item.dailyVelocity > 0 && item.recommended > 0
  );
  const approvedMetrics = batchMetrics(approvedItems);

  const rows = base.map(item => {
    const itemEconomics = economics(item);
    let status = "NO_DEMAND";
    if (item.dailyVelocity > 0 && item.recommended === 0) status = "HOLD";
    if (item.dailyVelocity > 0 && item.recommended > 0) {
      status = item.kind === "first_stock" ? "FIRST_STOCK" : "REPLENISH";
    }
    return {...item, ...itemEconomics, status};
  });
  return {
    rows: rows.sort((a, b) => b.recommended - a.recommended || number(b.netTotal) - number(a.netTotal) || a.sku.localeCompare(b.sku)),
    ...approvedMetrics
  };
}

function statusLabel(status) {
  return {REPLENISH: "建议补货", FIRST_STOCK: "建议首批", HOLD: "库存覆盖", NO_DEMAND: "销量不足"}[status];
}

function channelBlock(label, channel, demand) {
  if (demand.method === "COUNTRY_MISMATCH") {
    return `<div class="channel-line blocked"><b>${label}</b><span>BLOCKED_COUNTRY_SOURCE</span><small>来源国家与当前决策国家不一致 · 未计入需求</small></div>`;
  }
  if (channel.state && channel.state !== "READY") {
    const reason = channel.state === "PENDING_REFRESH"
      ? "访问令牌可刷新 · 结算待拉取"
      : channel.state === "BLOCKED_AUTH"
      ? "授权不可用"
      : "SKU 映射待确认";
    return `<div class="channel-line blocked-channel"><b>${label}</b><span>${channel.state}</span><small>${reason} · 未计入需求</small></div>`;
  }
  const recent = channel.recent30Units === null || channel.recent30Units === undefined
    ? "无可安全拆分的近30天值"
    : `近30天 ${channel.recent30Units} 件`;
  if (demand.method === "SEGMENTED_TREND") {
    const trend = demand.trend;
    const trendLabel = {RISING: "上涨", STABLE: "稳定", FALLING: "下降", SPIKE: "短期爆量"}[trend.trendClass];
    const confidenceLabel = {HIGH: "高", MEDIUM: "中", LOW: "低"}[trend.confidence];
    const denominatorNote = trend.denominatorBasis === "verified_sellable_days"
      ? "按实际可售天数"
      : "暂无缺货日历，按自然日";
    return `<div class="channel-line"><b>${label}</b><span>${channel.units.toLocaleString("zh-CN")} 件 / ${channel.orders.toLocaleString("zh-CN")} 单</span><small>近7天 ${trend.units.last7} · 8–15天 ${trend.units.days8To15} · 16–30天 ${trend.units.days16To30}</small><small>趋势${trendLabel} · 置信度${confidenceLabel} · ${denominatorNote} · 预测日均 ${demand.daily.toFixed(2)}</small></div>`;
  }
  const fallback = demand.method === "RECENT30_FALLBACK"
    ? "缺逐日分段，按30日+长窗降级"
    : "缺逐日分段，按长窗降级";
  return `<div class="channel-line"><b>${label}</b><span>${channel.units.toLocaleString("zh-CN")} 件 / ${channel.orders.toLocaleString("zh-CN")} 单</span><small>${recent} · ${fallback} · 日均 ${demand.daily.toFixed(2)}</small></div>`;
}

function rowHtml(item, config) {
  const local = config.currencySymbol;
  const inventoryLabel = item.kind === "first_stock" ? "海外仓尚无" : config.warehouse;
  const shippingEvidence = item.channels.shopee.actualShippingFee === null ? "（Shopee运费未计）" : "";
  const manualSource = item.manualInput?.sourceNote
    ? ` · 来源：${escapeHtml(item.manualInput.sourceNote)}`
    : "";
  const physicalLabel = [
    item.manualInput ? "手动补齐" : "",
    item.dimensionsReady ? `${item.dimensionsCm.join("×")} cm` : "尺寸待补充",
    item.weightReady ? `${item.weightG} g` : "重量待补充",
    item.costReady ? `成本 ${money(item.costCny, 2)}` : "成本待补充"
  ].filter(Boolean).join(" · ") + manualSource;
  const volumeLabel = item.dimensionsReady
    ? `${(item.volumeM3 * item.recommended).toFixed(3)} m³`
    : "体积待补充";
  const handlingLabel = item.handlingUnit === null
    ? "待补充（需重量）"
    : `−${money(item.handlingUnit + item.headFreightUnit, 2)}`;
  const benefitLabel = item.netUnit === null
    ? "单件收益待补充 · 本批收益待补充"
    : `单件净优势 ${money(item.netUnit, 2)} · 本批 ${money(item.netTotal)}`;
  const missingFields = [
    !item.dimensionsReady ? "尺寸" : "",
    !item.weightReady ? "重量" : "",
    !item.costReady ? "成本" : ""
  ].filter(Boolean).join("、");
  const affectedOutputs = [
    !item.dimensionsReady ? "体积" : "",
    !item.weightReady ? "本土处理和收益" : "",
    !item.costReady ? "占款" : ""
  ].filter(Boolean).join("、");
  return `<tr>
    <td><div class="product-cell"><img src="./${escapeHtml(item.image)}" alt="SKU ${escapeHtml(item.sku)} 主图"><div><strong>${escapeHtml(item.sku)}</strong><span>${escapeHtml(item.name)}</span><small>${physicalLabel}</small></div></div></td>
    <td><div class="channel-stack">${channelBlock("TikTok", item.channels.tiktok, item.tiktokDemand)}${channelBlock("Shopee", item.channels.shopee, item.shopeeDemand)}<em>合并需求 ${item.dailyVelocity.toFixed(2)} 件/天${item.spikeProtection ? " · 短期爆量首批仅覆盖15天" : ""}</em></div></td>
    <td><div class="inventory-grid"><span>库存<b>${item.inventory.stock}</b></span><span>可用<b>${item.inventory.available}</b></span><span>占用<b>${item.inventory.allocated}</b></span><span>冻结<b>${item.inventory.frozen}</b></span><span>在途<b>${item.inventory.inbound}</b></span><span>绑定<b>${inventoryLabel}</b></span></div></td>
    <td><div class="calc-lines"><span>${config.leadDays}天需求 <b>${item.leadDemand}</b></span><span>到仓剩余 <b>${item.projectedAtArrival}</b></span><span>${item.targetCoverageDays}天目标 <b>${item.arrivalTarget}</b></span><code>max(0, ${item.arrivalTarget} − ${item.projectedAtArrival})</code></div></td>
    <td class="recommend"><strong>${item.recommended}</strong><span>件</span><small>${volumeLabel}</small></td>
    <td><div class="economics-mini"><span>用户结算价 <b>${local}${item.customerPaymentLocal.toFixed(2)}</b></span><span>税费节省 ${Math.round(config.taxSavingRate * 100)}% <b class="gain">${money(item.taxSavingUnit, 2)}</b></span><span>跨境运费节省 20% <b class="gain">${money(item.shippingSavingUnit, 2)}</b></span><span>本土处理 + 头程 <b>${handlingLabel}</b></span><em>${benefitLabel} ${shippingEvidence}</em></div></td>
    <td><span class="pill ${item.status.toLowerCase()}">${statusLabel(item.status)}</span><small class="reason">${item.dataIncomplete ? `建议件数已生成；${missingFields}待补充，仅影响${affectedOutputs}展示。` : item.kind === "first_stock" ? "当前仓库为0；平台需求与商品资料齐全，收益单独展示。" : item.status === "HOLD" ? "现货与在途已覆盖到仓目标。" : item.status === "NO_DEMAND" ? "没有足够的SKU级需求事实。" : "需求缺口成立；收益仅展示，不拦截补货建议。"}</small>${item.dataIncomplete || item.manualInput ? `<button class="manual-entry-button" type="button" data-action="manual-entry" data-sku="${escapeHtml(item.sku)}">${item.manualInput ? "修改已补资料" : "手动补齐"}</button>` : ""}</td>
  </tr>`;
}

function renderRows() {
  const query = document.querySelector("#searchInput").value.trim().toLowerCase();
  const filter = document.querySelector("#statusFilter").value;
  const visible = calculated.filter(item => {
    const textMatch = !query || `${item.sku} ${item.name}`.toLowerCase().includes(query);
    const recent30Units = Object.values(item.channels).reduce(
      (sum, channel) => sum + number(channel.recent30Units), 0
    );
    const filterMatch = filter === "all"
      || (filter === "RECENT30" && recent30Units > 0)
      || (filter === "MISSING_DATA" && item.dataIncomplete)
      || item.status === filter;
    return textMatch && filterMatch;
  });
  const config = DATA.config[activeRegion];
  document.querySelector("#skuRows").innerHTML = visible.map(item => rowHtml(item, config)).join("");
  document.querySelector("#skuEmpty").hidden = visible.length > 0;
  document.querySelector("#visibleCount").textContent = `显示 ${visible.length} / ${calculated.length} 个 SKU`;
}

function renderCountry() {
  const result = calculateCountry(activeRegion);
  calculated = result.rows;
  batch = result;
  const config = DATA.config[activeRegion];
  const approved = calculated.filter(item => ["REPLENISH", "FIRST_STOCK"].includes(item.status));
  const qty = approved.reduce((sum, item) => sum + item.recommended, 0);
  const capital = approved.reduce(
    (sum, item) => sum + (item.costReady ? item.costCny * item.recommended : 0), 0
  );
  const benefit = approved.reduce((sum, item) => sum + number(item.netTotal), 0);
  const missingCostCount = approved.filter(item => !item.costReady).length;
  const missingBenefitCount = approved.filter(item => item.netTotal === null).length;
  const missingVolumeCount = approved.filter(item => !item.dimensionsReady).length;
  const missingChargeableCount = approved.filter(item => !item.dimensionsReady || !item.weightReady).length;
  const available = calculated.filter(item => item.kind === "existing").reduce((sum, item) => sum + item.inventory.available, 0);
  const inbound = calculated.filter(item => item.kind === "existing").reduce((sum, item) => sum + item.inventory.inbound, 0);
  const existingCount = calculated.filter(item => item.kind === "existing").length;
  const firstCount = calculated.filter(item => item.kind === "first_stock").length;
  const recent30Count = calculated.filter(item => Object.values(item.channels).some(
    channel => number(channel.recent30Units) > 0
  )).length;
  const readyChannelDemands = calculated.flatMap(item => [item.tiktokDemand, item.shopeeDemand])
    .filter(demand => demand.method !== "BLOCKED");
  const segmentedChannelCount = readyChannelDemands.filter(
    demand => demand.method === "SEGMENTED_TREND"
  ).length;
  const spikeProtectedCount = calculated.filter(item => item.spikeProtection).length;

  document.querySelector("#snapshotDate").textContent = DATA.snapshotDate;
  document.querySelector("#countryEyebrow").textContent = `${activeRegion} · ${config.freightMode.toUpperCase()} · ${config.warehouse}`;
  document.querySelector("#countryName").textContent = config.name;
  document.querySelector("#heroDescription").textContent = `把 TikTok ${activeRegion} 与 Shopee ${activeRegion} 的 SKU 需求相加，再扣除 ${config.warehouse} 的可用库存和在途；按 ${config.leadDays} 天补货周期算到仓缺口，并单独展示税费、跨境运费、本土处理和固定头程后的收益。`;
  const taxChip = config.taxSavingRate > 0
    ? `税费节省 = 用户结算价 × ${Math.round(config.taxSavingRate * 100)}%`
    : "税费优势尚未批准，按 0";
  document.querySelector("#sourceChips").innerHTML = `<span>雅仓 ${existingCount} SKU</span><span>${config.demandCoverage}</span><span>${config.freightMode}</span><span>${taxChip}</span>`;
  document.querySelector("#coverageLabel").textContent = `${config.leadDays}天交期 · 常规${config.targetDays + config.safetyDays}天 · 爆量首批15天`;
  document.querySelector("#batchQty").textContent = qty.toLocaleString("zh-CN");
  document.querySelector("#batchSkuCount").textContent = `${approved.length} 款`;
  document.querySelector("#batchVolume").textContent = missingVolumeCount
    ? `${result.physicalM3.toFixed(3)} m³ + ${missingVolumeCount}款待补`
    : `${result.physicalM3.toFixed(3)} m³`;
  document.querySelector("#billableVolume").textContent = missingChargeableCount
    ? `${result.billableM3.toFixed(3)} m³ + ${missingChargeableCount}款待补`
    : `${result.billableM3.toFixed(3)} m³`;
  document.querySelector("#workingCapital").textContent = missingCostCount
    ? `${money(capital)} + ${missingCostCount}款待补`
    : money(capital);
  document.querySelector("#knownBenefit").textContent = missingBenefitCount
    ? `${money(benefit)} + ${missingBenefitCount}款待补`
    : money(benefit);
  document.querySelector("#headFreight").textContent = money(result.freightTotal);
  document.querySelector("#batchSentence").textContent = approved.length
    ? `优先：${approved.slice(0, 6).map(item => item.sku).join("、")}；其中首批候选 ${approved.filter(item => item.kind === "first_stock").length} 款。`
    : "当前没有需要补货且商品物流资料完整的 SKU。";
  document.querySelector("#warehouseLabel").textContent = `雅仓 ${config.warehouse}`;
  document.querySelector("#inventoryUnits").textContent = `${available.toLocaleString("zh-CN")} 件`;
  document.querySelector("#inboundUnits").textContent = `${inbound.toLocaleString("zh-CN")} 件`;
  document.querySelector("#demandWindow").textContent = config.demandCoverage;
  document.querySelector("#decisionHeadline").textContent = approved.length ? `备 ${approved.length} 款` : "暂缓备货";
  document.querySelector("#formulaText").textContent = `Q = max[0, ceil(v × 目标覆盖天数) − max(0, 可用 + 在途 − ceil(v × ${config.leadDays}))]`;
  document.querySelector("#freightRule").textContent = `所有国家、站点和 SKU 的头程统一按 ${money(config.fixedHeadFreightUnitCny, 2)}/件计入；体积只用于装运规划，不参与本页头程金额。`;
  const taxRule = config.taxSavingRate > 0
    ? `税费节省按用户结算价的 ${Math.round(config.taxSavingRate * 100)}%`
    : "税费节省没有已批准比例，保守按 0";
  document.querySelector("#economicsRule").textContent = `${taxRule}；跨境运费只按有 SKU 级结算证据的金额取 20%。扣除上架、出库、包材和固定 ${money(config.fixedHeadFreightUnitCny, 2)}/件头程；0–30天仓储费按0。收益只展示，不作为隐藏 SKU 或拦截建议的条件。`;
  const shopeeState = calculated
    .map(item => item.channels.shopee.state)
    .find(state => state && state !== "READY");
  const demandEvidenceClass = shopeeState ? "warn" : "ok";
  const demandEvidenceText = shopeeState
    ? `TikTok 已按 SKU 计入；Shopee 当前状态为 ${shopeeState} 且未计入需求。当前建议是保守下限，不把待拉取渠道当作零销量。`
    : `TikTok 与 Shopee 独立显示订单、件数、窗口与日均，再在 SKU 层相加。近30天有动销 ${recent30Count} 款，全部保留在台账，可用筛选查看。`;
  const identityEvidence = config.inventoryIdentityBlocker
    ? `<article class="warn"><strong>完整 SKU 身份待恢复</strong><p>${config.inventoryIdentityBlocker}</p></article>`
    : "";
  const shopeeAudit = config.shopeeDemandEvidence;
  const unmappedEvidence = shopeeAudit?.unmappedItemLines
    ? `<article class="warn"><strong>Shopee 明细待映射</strong><p>完整结算已拉取，但 ${shopeeAudit.unmappedItemLines} 条商品明细无可审计 4 位 SKU 映射，已排除自动备货计算，未伪造销量。</p></article>`
    : "";
  document.querySelector("#evidenceGrid").innerHTML = `
    <article class="${demandEvidenceClass}"><strong>${shopeeState ? "Shopee 需求暂未纳入" : "双平台需求已合并"}</strong><p>${demandEvidenceText}</p></article>
    <article class="ok"><strong>库存按国家隔离</strong><p>${config.warehouse} 当前可用 ${available} 件、在途 ${inbound} 件；不使用其他国家仓库存抵扣本国需求。</p></article>
    ${identityEvidence}
    ${unmappedEvidence}
    <article class="${segmentedChannelCount ? "ok" : "warn"}"><strong>趋势算法数据覆盖</strong><p>${segmentedChannelCount} / ${readyChannelDemands.length} 个可用渠道具备精确7/8/15天分段；其余明确使用30日+长窗或长窗降级，不伪造趋势。当前短期爆量首批保护 ${spikeProtectedCount} 款。</p></article>
    <article class="ok"><strong>数量与物流资料已解耦</strong><p>${firstCount} 款海外仓尚无的候选进入台账；缺尺寸、重量或成本仍按需求、库存、在途生成建议件数，相关体积、占款或收益显示待补充。</p></article>
    <article class="${activeRegion === "TH" ? "warn" : "ok"}"><strong>运费证据范围</strong><p>${config.shippingCoverage}。</p></article>
    <article class="neutral"><strong>固定头程口径</strong><p>本页按用户批准口径对所有国家、站点和 SKU 统一使用 ${money(config.fixedHeadFreightUnitCny, 2)}/件；实际发货报价差异不在本轮建议中调整。</p></article>
    <article class="neutral"><strong>外部写入为 0</strong><p>该页面是本地只读决策制品，不会写雅仓、TikTok、Shopee 或业务数据库，也不会自动下采购单。</p></article>`;
  renderRows();
}

document.querySelectorAll(".country-tab").forEach(button => button.addEventListener("click", () => {
  activeRegion = button.dataset.region;
  document.querySelectorAll(".country-tab").forEach(item => item.classList.toggle("active", item === button));
  renderCountry();
}));
document.querySelector("#searchInput").addEventListener("input", renderRows);
document.querySelector("#statusFilter").addEventListener("change", renderRows);

const manualDialog = document.querySelector("#manualInputDialog");
const manualForm = document.querySelector("#manualInputForm");
const manualError = document.querySelector("#manualInputError");

document.addEventListener("click", event => {
  const button = event.target.closest("[data-action='manual-entry']");
  if (!button) return;
  const sku = button.dataset.sku;
  const sourceItem = DATA.countries[activeRegion].find(item => item.sku === sku);
  if (!sourceItem) return;
  const saved = manualInputs[manualInputId(activeRegion, sku)];
  const dimensions = saved?.dimensionsCm || sourceItem.dimensionsCm || [];
  manualForm.elements.region.value = activeRegion;
  manualForm.elements.sku.value = sku;
  manualForm.elements.lengthCm.value = dimensions[0] ?? "";
  manualForm.elements.widthCm.value = dimensions[1] ?? "";
  manualForm.elements.heightCm.value = dimensions[2] ?? "";
  manualForm.elements.weightG.value = saved?.weightG ?? sourceItem.weightG ?? "";
  manualForm.elements.costCny.value = saved?.costCny ?? sourceItem.costCny ?? "";
  manualForm.elements.sourceNote.value = saved?.sourceNote ?? "";
  document.querySelector("#manualDialogTitle").textContent = `${activeRegion} · SKU ${sku} 手动补齐`;
  document.querySelector("#clearManualInput").hidden = !saved;
  manualError.textContent = "";
  manualDialog.showModal();
});

document.querySelector("#cancelManualInput").addEventListener("click", () => manualDialog.close());
document.querySelector("#cancelManualInputBottom").addEventListener("click", () => manualDialog.close());

manualForm.addEventListener("submit", event => {
  event.preventDefault();
  const values = ["lengthCm", "widthCm", "heightCm", "weightG", "costCny"]
    .map(name => Number(manualForm.elements[name].value));
  if (values.some(value => !Number.isFinite(value) || value <= 0)) {
    manualError.textContent = "长、宽、高、重量和采购成本都必须填写大于 0 的数字。";
    return;
  }
  const region = manualForm.elements.region.value;
  const sku = manualForm.elements.sku.value;
  manualInputs[manualInputId(region, sku)] = {
    dimensionsCm: values.slice(0, 3),
    weightG: values[3],
    costCny: values[4],
    sourceNote: manualForm.elements.sourceNote.value.trim(),
    updatedAt: new Date().toISOString()
  };
  try {
    saveManualInputs();
  } catch {
    manualError.textContent = "浏览器本地存储不可用，资料尚未保存。";
    return;
  }
  manualDialog.close();
  renderCountry();
});

document.querySelector("#clearManualInput").addEventListener("click", () => {
  const region = manualForm.elements.region.value;
  const sku = manualForm.elements.sku.value;
  delete manualInputs[manualInputId(region, sku)];
  try {
    saveManualInputs();
  } catch {
    manualError.textContent = "浏览器本地存储不可用，无法清除。";
    return;
  }
  manualDialog.close();
  renderCountry();
});
renderCountry();
