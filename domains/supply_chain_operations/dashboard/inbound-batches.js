const DATA = window.SUPPLY_CHAIN_DATA;
const INBOUND_PLAN = window.SUPPLY_CHAIN_INBOUND_PLAN;
const TIMELINE = window.SUPPLY_CHAIN_TIMELINE;
const INBOUND_ETA_KEY = "supply-chain-inbound-batch-timing-v3";
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

function anchorLabel(batch, saved, actualAnchorAt) {
  if (actualAnchorAt) return saved?.anchorAt ? "人工确认实际入库" : "雅仓已入库日志";
  return "未入库 · 建单时间 + 4 天估算";
}

function rowHtml(batch) {
  const saved = overrides[overrideId(batch.region, batch.batchId)];
  const actualAnchorAt = saved?.anchorAt || (batch.anchorAt ? batch.anchorAt.slice(0, 16) : "");
  const calculationAnchorAt = actualAnchorAt || (batch.estimatedAnchorAt ? batch.estimatedAnchorAt.slice(0, 16) : "");
  const effectiveAnchorDate = calculationAnchorAt ? calculationAnchorAt.slice(0, 10) : "";
  const effectiveDate = saved?.estimatedSellableDate
    || (effectiveAnchorDate ? TIMELINE.addDays(effectiveAnchorDate, batch.transportDays + batch.shelvingDays) : "");
  const allocation = allocationState(batch);
  return `<tr data-region="${escapeHtml(batch.region)}" data-batch-id="${escapeHtml(batch.batchId)}">
    <td><div class="batch-identity"><small>${escapeHtml(batch.region)} · ${escapeHtml(REGION_NAMES[batch.region])}</small><strong>${escapeHtml(batch.batchId)}</strong><span>${escapeHtml(DATA.config[batch.region].warehouse)}</span></div></td>
    <td><div class="batch-facts"><span>批次总量 <b>${batch.totalUnits.toLocaleString("zh-CN")} 件</b></span><span>运输周期 <b>${batch.transportDays} 天</b></span><span>签收上架 <b>${batch.shelvingDays} 天</b></span></div></td>
    <td><div class="batch-facts"><span>建单时间 <b>${escapeHtml(batch.createdAt.replace("T", " ").slice(0, 19))}</b></span><span>实际已入库 <b>${escapeHtml(actualAnchorAt ? actualAnchorAt.replace("T", " ").slice(0, 19) : "尚未入库")}</b></span><span>计算起算 <b>${escapeHtml(calculationAnchorAt.replace("T", " "))}</b></span><span>口径 <b>${anchorLabel(batch, saved, actualAnchorAt)}</b></span><span>系统预计可售 <b>${escapeHtml(effectiveDate)}</b></span></div></td>
    <td><label class="batch-date-field">实际已入库时间（入库后填写）<input name="anchorAt" type="datetime-local" min="${escapeHtml(batch.createdAt.slice(0, 16))}" value="${escapeHtml(actualAnchorAt)}"></label><label class="batch-date-field">预计可售日期<input name="estimatedSellableDate" type="date" value="${escapeHtml(effectiveDate)}"></label><label class="batch-note-field">确认依据<input name="sourceNote" type="text" maxlength="120" value="${escapeHtml(saved?.sourceNote || "")}" placeholder="例如：雅仓日志显示已入库 2026-08-04 15:39:15"></label><small class="batch-save-state">${saved ? `已人工确认实际入库 · ${escapeHtml(saved.updatedAt?.slice(0, 10) || "本地")}` : batch.anchorAt ? "已读取雅仓实际入库日志" : "尚未入库；当前按建单时间 + 4 天估算"}</small></td>
    <td><span class="pill ${allocation.pending || !actualAnchorAt ? "blocked" : "hold"}">${!actualAnchorAt ? "未入库" : allocation.pending ? "待核对" : "可归属"}</span><small class="reason"><b>${escapeHtml(!actualAnchorAt ? "尚未实际入库" : allocation.label)}</b><br>${escapeHtml(!actualAnchorAt ? `预计入库起算：${calculationAnchorAt.replace("T", " ")}` : allocation.detail)}</small></td>
    <td><div class="batch-actions"><button class="primary" type="button" data-action="save">确认批次时间</button><button class="secondary" type="button" data-action="clear" ${saved ? "" : "disabled"}>恢复系统估算</button></div></td>
  </tr>`;
}

function render() {
  const batches = allBatches();
  const visible = activeRegion === "ALL" ? batches : batches.filter(batch => batch.region === activeRegion);
  document.querySelector("#snapshotDate").textContent = DATA.snapshotDate;
  document.querySelector("#batchCount").textContent = `${batches.length} 批`;
  document.querySelector("#batchUnits").textContent = `${batches.reduce((sum, batch) => sum + batch.totalUnits, 0).toLocaleString("zh-CN")} 件`;
  document.querySelector("#confirmedCount").textContent = `${batches.filter(batch => overrides[overrideId(batch.region, batch.batchId)]?.anchorAt || batch.anchorAt).length} 批`;
  document.querySelector("#pendingCount").textContent = `${batches.filter(batch => !(overrides[overrideId(batch.region, batch.batchId)]?.anchorAt || batch.anchorAt)).length} 批`;
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
    const anchorAt = row.querySelector("[name='anchorAt']").value;
    const anchorDate = anchorAt.slice(0, 10);
    const estimatedSellableDate = row.querySelector("[name='estimatedSellableDate']").value;
    try {
      const minimumAnchorAt = row.querySelector("[name='anchorAt']").min;
      TIMELINE.daysBetween(minimumAnchorAt.slice(0, 10), anchorDate);
      TIMELINE.daysBetween(DATA.snapshotDate, estimatedSellableDate);
      if (!anchorAt || anchorAt < minimumAnchorAt || estimatedSellableDate < anchorDate) {
        throw new TypeError("invalid batch timing");
      }
    } catch {
      message.textContent = "必须填写有效的已入库起算日；预计可售日期不得早于起算日。";
      message.classList.add("error-text");
      return;
    }
    overrides[key] = {
      anchorAt,
      anchorDate,
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

document.querySelector("#batchRows").addEventListener("change", event => {
  if (event.target.name !== "anchorAt") return;
  const row = event.target.closest("tr");
  const region = row.dataset.region;
  const batchId = row.dataset.batchId;
  const batch = (INBOUND_PLAN.regions[region].batches || []).find(item => item.batchId === batchId);
  const etaInput = row.querySelector("[name='estimatedSellableDate']");
  if (!event.target.value || !batch) {
    const fallbackDate = batch?.estimatedAnchorAt?.slice(0, 10) || "";
    etaInput.value = fallbackDate
      ? TIMELINE.addDays(fallbackDate, batch.transportDays + batch.shelvingDays)
      : "";
    return;
  }
  const anchorDate = event.target.value.slice(0, 10);
  etaInput.min = anchorDate;
  etaInput.value = TIMELINE.addDays(anchorDate, batch.transportDays + batch.shelvingDays);
});

render();
