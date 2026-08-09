const DATA = window.SUPPLY_CHAIN_DATA;
const INBOUND_PLAN = window.SUPPLY_CHAIN_INBOUND_PLAN;
const TIMELINE = window.SUPPLY_CHAIN_TIMELINE;
const INBOUND_ETA_KEY = "supply-chain-inbound-batch-eta-v2";
const REGION_NAMES = {MY: "马来西亚", TH: "泰国", VN: "越南", PH: "菲律宾"};
let activeRegion = new URLSearchParams(location.hash.slice(1)).get("region") || "ALL";
let overrides = loadOverrides();

const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[char]);
const overrideId = (region, batchId) => `${region}:${batchId}`;

function loadOverrides() {
  try {
    const value = JSON.parse(localStorage.getItem(INBOUND_ETA_KEY) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

function saveOverrides() {
  localStorage.setItem(INBOUND_ETA_KEY, JSON.stringify(overrides));
}

function allBatches() {
  return Object.entries(INBOUND_PLAN.regions).flatMap(([region, plan]) =>
    (plan.batches || []).map(batch => ({region, plan, ...batch}))
  );
}

function allocationState(batch) {
  if (batch.plan.allocationPolicy === "SINGLE_ACTIVE_BATCH") {
    return {pending: false, label: "单一运输中批次", detail: "该国 SKU 聚合在途可归属此唯一批次"};
  }
  const entries = Object.entries(batch.skuQuantities || {});
  const pendingSkus = entries.filter(([, quantity]) => !Number.isInteger(quantity) || quantity < 0).map(([sku]) => sku);
  if (!entries.length || pendingSkus.length) {
    return {
      pending: true,
      label: "批次 SKU 数量待核对",
      detail: pendingSkus.length ? `待核 SKU：${pendingSkus.join("、")}` : "尚无完整 SKU 分摊"
    };
  }
  const total = entries.reduce((sum, [, quantity]) => sum + quantity, 0);
  return {pending: total !== batch.totalUnits, label: total === batch.totalUnits ? "分摊已对平" : "分摊总数不一致", detail: `${entries.length} 个 SKU，共 ${total} 件`};
}

function anchorLabel(type) {
  return type === "MARKED_SHIPPED" ? "雅仓标记发货" : "创建日期回退估算";
}

function rowHtml(batch) {
  const saved = overrides[overrideId(batch.region, batch.batchId)];
  const effectiveDate = saved?.estimatedSellableDate || batch.estimatedSellableDate;
  const allocation = allocationState(batch);
  return `<tr data-region="${escapeHtml(batch.region)}" data-batch-id="${escapeHtml(batch.batchId)}">
    <td><div class="batch-identity"><small>${escapeHtml(batch.region)} · ${escapeHtml(REGION_NAMES[batch.region])}</small><strong>${escapeHtml(batch.batchId)}</strong><span>${escapeHtml(DATA.config[batch.region].warehouse)}</span></div></td>
    <td><div class="batch-facts"><span>批次总量 <b>${batch.totalUnits.toLocaleString("zh-CN")} 件</b></span><span>运输周期 <b>${batch.transportDays} 天</b></span><span>签收上架 <b>${batch.shelvingDays} 天</b></span></div></td>
    <td><div class="batch-facts"><span>起算日 <b>${escapeHtml(batch.anchorDate)}</b></span><span>锚点 <b>${anchorLabel(batch.anchorType)}</b></span><span>系统预计可售 <b>${escapeHtml(batch.estimatedSellableDate)}</b></span></div></td>
    <td><label class="batch-date-field">预计可售日期<input name="estimatedSellableDate" type="date" min="${escapeHtml(DATA.snapshotDate)}" value="${escapeHtml(effectiveDate)}"></label><label class="batch-note-field">确认依据<input name="sourceNote" type="text" maxlength="120" value="${escapeHtml(saved?.sourceNote || "")}" placeholder="例如：物流商确认到仓时间"></label><small class="batch-save-state">${saved ? `已确认 · ${escapeHtml(saved.updatedAt?.slice(0, 10) || "本地")}` : "尚未人工确认，当前使用系统估算"}</small></td>
    <td><span class="pill ${allocation.pending ? "blocked" : "hold"}">${allocation.pending ? "待核对" : "可归属"}</span><small class="reason"><b>${escapeHtml(allocation.label)}</b><br>${escapeHtml(allocation.detail)}</small></td>
    <td><div class="batch-actions"><button class="primary" type="button" data-action="save">确认批次时间</button><button class="secondary" type="button" data-action="clear" ${saved ? "" : "disabled"}>恢复系统估算</button></div></td>
  </tr>`;
}

function render() {
  const batches = allBatches();
  const visible = activeRegion === "ALL" ? batches : batches.filter(batch => batch.region === activeRegion);
  document.querySelector("#snapshotDate").textContent = DATA.snapshotDate;
  document.querySelector("#batchCount").textContent = `${batches.length} 批`;
  document.querySelector("#batchUnits").textContent = `${batches.reduce((sum, batch) => sum + batch.totalUnits, 0).toLocaleString("zh-CN")} 件`;
  document.querySelector("#confirmedCount").textContent = `${batches.filter(batch => overrides[overrideId(batch.region, batch.batchId)]).length} 批`;
  document.querySelector("#pendingCount").textContent = `${batches.filter(batch => allocationState(batch).pending).length} 批`;
  document.querySelector("#batchRows").innerHTML = visible.map(rowHtml).join("");
  document.querySelector("#batchEmpty").hidden = visible.length > 0;
  document.querySelectorAll("[data-region]").forEach(button => {
    if (button.tagName === "BUTTON") button.classList.toggle("active", button.dataset.region === activeRegion);
  });
}

document.querySelectorAll("nav [data-region]").forEach(button => button.addEventListener("click", () => {
  activeRegion = button.dataset.region;
  location.hash = activeRegion === "ALL" ? "" : `region=${activeRegion}`;
  render();
}));

document.querySelector("#batchRows").addEventListener("click", event => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const row = button.closest("tr");
  const region = row.dataset.region;
  const batchId = row.dataset.batchId;
  const key = overrideId(region, batchId);
  const message = document.querySelector("#pageMessage");
  if (button.dataset.action === "clear") {
    delete overrides[key];
  } else {
    const estimatedSellableDate = row.querySelector("[name='estimatedSellableDate']").value;
    try {
      TIMELINE.daysBetween(DATA.snapshotDate, estimatedSellableDate);
      if (estimatedSellableDate < DATA.snapshotDate) throw new TypeError("past date");
    } catch {
      message.textContent = `预计可售日期必须是 ${DATA.snapshotDate} 或之后的有效日期。`;
      message.classList.add("error-text");
      return;
    }
    overrides[key] = {
      estimatedSellableDate,
      sourceNote: row.querySelector("[name='sourceNote']").value.trim(),
      updatedAt: new Date().toISOString()
    };
  }
  try {
    saveOverrides();
  } catch {
    message.textContent = "浏览器本地存储不可用，本次确认尚未保存。";
    message.classList.add("error-text");
    return;
  }
  message.textContent = button.dataset.action === "clear"
    ? `${batchId} 已恢复系统估算。`
    : `${batchId} 已按完整批次保存；补货看板将使用新日期。`;
  message.classList.remove("error-text");
  render();
});

render();
