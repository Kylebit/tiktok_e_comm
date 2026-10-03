const DATA = window.SUPPLY_CHAIN_DATA;
const INBOUND_PLAN = window.SUPPLY_CHAIN_INBOUND_PLAN;
const TIMELINE = window.SUPPLY_CHAIN_TIMELINE;
const OVERRIDE_STORE = window.SUPPLY_CHAIN_OVERRIDES;
const INBOUND_ETA_KEY = OVERRIDE_STORE.KEY;
const REGION_NAMES = {MY: "马来西亚", TH: "泰国", VN: "越南", PH: "菲律宾"};
let activeRegion = new URLSearchParams(location.hash.slice(1)).get("region") || "ALL";
let overrideStorage = null;
let storageError = "";
let overrides = loadOverrides();
const failedDrafts = {};

const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[char]);
const overrideId = (region, batchId) => `${region}:${batchId}`;

function loadOverrides() {
  try {
    overrideStorage = OVERRIDE_STORE.read(localStorage);
    return overrideStorage.values;
  } catch (error) {
    storageError = String(error.message);
    return {};
  }
}

function saveOverrides(nextOverrides) {
  if (!overrideStorage) throw Error(storageError || "本地确认尚未读取");
  overrideStorage = OVERRIDE_STORE.write(localStorage, overrideStorage, nextOverrides);
  overrides = overrideStorage.values;
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

function currentOverride(batch) {
  const saved = overrides[overrideId(batch.region, batch.batchId)];
  return OVERRIDE_STORE.current(saved, batch, DATA, INBOUND_PLAN);
}

function rowHtml(batch) {
  const saved = currentOverride(batch);
  const draft = failedDrafts[overrideId(batch.region, batch.batchId)];
  const actualAnchorAt = saved?.anchorAt || (batch.anchorAt ? batch.anchorAt.slice(0, 16) : "");
  const calculationAnchorAt = actualAnchorAt || (batch.estimatedAnchorAt ? batch.estimatedAnchorAt.slice(0, 16) : "");
  const effectiveAnchorDate = calculationAnchorAt ? calculationAnchorAt.slice(0, 10) : "";
  const effectiveDate = saved?.estimatedSellableDate
    || batch.estimatedSellableDate
    || (effectiveAnchorDate ? TIMELINE.addDays(effectiveAnchorDate, batch.transportDays) : "");
  const allocation = allocationState(batch);
  return `<tr data-region="${escapeHtml(batch.region)}" data-batch-id="${escapeHtml(batch.batchId)}">
    <td><div class="batch-identity"><small>${escapeHtml(batch.region)} · ${escapeHtml(REGION_NAMES[batch.region])}</small><strong>${escapeHtml(batch.batchId)}</strong><span>${escapeHtml(DATA.config[batch.region].warehouse)}</span></div></td>
    <td><div class="batch-facts"><span>批次总量 <b>${batch.totalUnits.toLocaleString("zh-CN")} 件</b></span><span>海外运输周期 <b>${batch.transportDays} 天</b></span><span>签收上架缓冲 <b>0 天（已取消）</b></span></div></td>
    <td><div class="batch-facts"><span>建单时间 <b>${escapeHtml(batch.createdAt.replace("T", " ").slice(0, 19))}</b></span><span>实际已入库 <b>${escapeHtml(actualAnchorAt ? actualAnchorAt.replace("T", " ").slice(0, 19) : "尚未入库")}</b></span><span>计算起算 <b>${escapeHtml(calculationAnchorAt.replace("T", " "))}</b></span><span>口径 <b>${anchorLabel(batch, saved, actualAnchorAt)}</b></span><span>批次预计可售 <b>${escapeHtml(effectiveDate)}</b></span><span>日期来源 <b>${escapeHtml(batch.estimatedSellableSource === "user_confirmed_arrival_and_shelving_date" ? "用户确认到达并上架" : "系统运输周期估算")}</b></span></div></td>
    <td><label class="batch-date-field">实际已入库时间（入库后填写）<input name="anchorAt" type="datetime-local" min="${escapeHtml(batch.createdAt.slice(0, 16))}" value="${escapeHtml(draft?.anchorAt ?? actualAnchorAt)}"></label><label class="batch-date-field">预计可售日期<input name="estimatedSellableDate" type="date" value="${escapeHtml(draft?.estimatedSellableDate ?? effectiveDate)}"></label><label class="batch-note-field">确认依据<input name="sourceNote" type="text" maxlength="120" value="${escapeHtml(draft?.sourceNote ?? saved?.sourceNote ?? "")}" placeholder="例如：雅仓日志显示已入库 2026-08-04 15:39:15"></label><small class="batch-save-state">${draft ? "输入尚未保存，可重试；计算仍使用此前确认。" : saved ? `已人工确认实际入库 · ${escapeHtml(saved.updatedAt?.slice(0, 10) || "本地")}` : batch.anchorAt ? "已读取雅仓实际入库日志" : "尚未入库；当前按建单时间 + 4 天估算"}</small></td>
    <td><span class="pill ${allocation.pending || !actualAnchorAt ? "blocked" : "hold"}">${!actualAnchorAt ? "未入库" : allocation.pending ? "待核对" : "可归属"}</span><small class="reason"><b>${escapeHtml(!actualAnchorAt ? "尚未实际入库" : allocation.label)}</b><br>${escapeHtml(!actualAnchorAt ? `预计入库起算：${calculationAnchorAt.replace("T", " ")}` : allocation.detail)}</small></td>
    <td><div class="batch-actions"><button class="primary" type="button" data-action="save">确认批次时间</button><button class="secondary" type="button" data-action="clear" ${saved ? "" : "disabled"}>恢复系统估算</button></div></td>
  </tr>`;
}

function render() {
  const batches = allBatches();
  const visible = activeRegion === "ALL" ? batches : batches.filter(batch => batch.region === activeRegion);
  document.querySelector("#snapshotDate").textContent = `库存 ${DATA.snapshotDate} · 订单 ${DATA.orderDemandCapturedAt || "待刷新"} · 批次 ${INBOUND_PLAN.capturedAt || "待刷新"}`;
  document.querySelector("#batchCount").textContent = `${batches.length} 批`;
  document.querySelector("#batchUnits").textContent = `${batches.reduce((sum, batch) => sum + batch.totalUnits, 0).toLocaleString("zh-CN")} 件`;
  document.querySelector("#confirmedCount").textContent = `${batches.filter(batch => currentOverride(batch)?.anchorAt || batch.anchorAt).length} 批`;
  document.querySelector("#pendingCount").textContent = `${batches.filter(batch => !(currentOverride(batch)?.anchorAt || batch.anchorAt)).length} 批`;
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
  const batch = (INBOUND_PLAN.regions[region].batches || []).find(item => item.batchId === batchId);
  const message = document.querySelector("#pageMessage");
  const nextOverrides = {...overrides};
  if (button.dataset.action === "clear") {
    delete nextOverrides[key];
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
    nextOverrides[key] = {
      anchorAt,
      anchorDate,
      estimatedSellableDate,
      sourceNote: row.querySelector("[name='sourceNote']").value.trim(),
      basePlanConfirmedAt: batch?.estimatedSellableConfirmedAt || null,
      updatedAt: new Date().toISOString(),
      binding: OVERRIDE_STORE.basis({...batch, region}, DATA, INBOUND_PLAN)
    };
  }
  try {
    saveOverrides(nextOverrides);
  } catch (error) {
    if (button.dataset.action !== "clear") failedDrafts[key] = nextOverrides[key];
    message.textContent = `浏览器本地存储不可用或已变化，本次确认尚未保存。${error.message}`;
    message.classList.add("error-text");
    return;
  }
  delete failedDrafts[key];
  refreshUndo();
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
      ? TIMELINE.addDays(fallbackDate, batch.transportDays)
      : "";
    return;
  }
  const anchorDate = event.target.value.slice(0, 10);
  etaInput.min = anchorDate;
  etaInput.value = TIMELINE.addDays(anchorDate, batch.transportDays);
});

render();
if (storageError) document.querySelector("#pageMessage").textContent = `本地确认读取失败：${storageError}。暂用已提交来源；请保留存储后恢复。`;

let importPreview = null;
const transferMessage = document.querySelector("#transferMessage");
const importText = document.querySelector("#importText");
const applyImport = document.querySelector("#applyImport");
const undoImport = document.querySelector("#undoImport");
const transferStatus = (message, error = false) => {
  transferMessage.textContent = message; transferMessage.classList.toggle("error-text", error);
};
function refreshUndo() { undoImport.disabled = !overrideStorage?.state?.undo; }
function invalidatePreview() { importPreview = null; applyImport.disabled = true; }
importText.addEventListener("input", invalidatePreview);
document.querySelector("#exportOverrides").addEventListener("click", async event => {
  const button = event.currentTarget; button.disabled = true; transferStatus("正在核对当前有效确认并生成导出…");
  try {
    const stored = OVERRIDE_STORE.read(localStorage);
    const exported = await OVERRIDE_STORE.exportBundle(stored.values, DATA, INBOUND_PLAN, location.origin);
    document.querySelector("#exportText").value = JSON.stringify(exported.bundle, null, 2);
    transferStatus(`已导出 ${exported.bundle.entries.length} 项有效确认；${exported.excluded.length} 项过期或缺少身份/依据，原存储均保留。来源 ${location.origin}`);
  } catch (error) { transferStatus(`导出失败：${error.message}`, true); }
  finally { button.disabled = false; }
});
document.querySelector("#importFile").addEventListener("change", async event => {
  invalidatePreview();
  const file = event.target.files[0]; if (!file) return;
  try { if (file.size > 1000000) throw Error("文件超过1MB"); importText.value = await file.text(); transferStatus("已读取文件，尚未修改确认。请预览。"); }
  catch (error) { transferStatus(error.message, true); }
});
document.querySelector("#previewImport").addEventListener("click", async event => {
  const button = event.currentTarget; button.disabled = true; invalidatePreview(); transferStatus("正在核对身份、日期、摘要和本地冲突…");
  try {
    // Preview owns its snapshot; it must never advance the manual-edit token
    // while manual values and unsaved row inputs still belong to an older read.
    const previewStorage = OVERRIDE_STORE.read(localStorage);
    const input = importText.value;
    const prepared = await OVERRIDE_STORE.preview(input, previewStorage, DATA, INBOUND_PLAN);
    if (input !== importText.value) throw Error("输入已变化，请重新预览");
    importPreview = prepared;
    document.querySelector("#importRows").innerHTML = prepared.rows.map((row, index) => `<tr>
      <td><input type="checkbox" aria-label="选择 ${escapeHtml(row.key)}" data-import-index="${index}" ${row.status === "APPLICABLE" ? "checked" : ""} ${["APPLICABLE", "CONFLICT"].includes(row.status) ? "" : "disabled"}></td>
      <td>${escapeHtml(row.key)}<br>${escapeHtml(row.value.binding.warehouse)}<br>SKU ${escapeHtml(Object.keys(row.value.binding.skuQuantities || {}).join("、"))}</td>
      <td>${escapeHtml(row.status)} · ${escapeHtml(row.detail)}</td><td>本地 ${escapeHtml(row.existing?.estimatedSellableDate || "无")}<br>导入 ${escapeHtml(row.value.estimatedSellableDate)}<br>${escapeHtml(row.value.sourceNote)}</td></tr>`).join("");
    applyImport.disabled = !prepared.rows.some(r => ["APPLICABLE", "CONFLICT"].includes(r.status));
    transferStatus(`预览 ${prepared.rows.length} 项；来源 ${prepared.bundle.source.origin}，导出于 ${prepared.bundle.source.exportedAt}。尚未写入；冲突默认保留本地。`);
  } catch (error) { transferStatus(`无法导入：${error.message}`, true); }
  finally { button.disabled = false; }
});
applyImport.addEventListener("click", () => {
  try {
    if (!importPreview || importPreview.input !== importText.value) throw Error("请重新预览");
    const selected = [...document.querySelectorAll("[data-import-index]:checked")].map(input => importPreview.rows[Number(input.dataset.importIndex)].key);
    overrideStorage = OVERRIDE_STORE.apply(localStorage, importPreview, selected); overrides = overrideStorage.values;
    invalidatePreview(); refreshUndo(); render(); transferStatus(`已导入 ${selected.length} 项；旧origin和旧版存储均保留，可撤销本次导入。`);
  } catch (error) { transferStatus(`导入尚未保存：${error.message}`, true); }
});
undoImport.addEventListener("click", () => {
  try { overrideStorage = OVERRIDE_STORE.undo(localStorage, overrideStorage); overrides = overrideStorage.values;
    invalidatePreview(); refreshUndo(); render(); transferStatus("已撤销最近导入，恢复导入前本地确认；旧版存储仍保留。");
  } catch (error) { transferStatus(`未撤销：${error.message}`, true); }
});
window.addEventListener("storage", event => {
  if (![OVERRIDE_STORE.KEY, OVERRIDE_STORE.LEGACY_KEY].includes(event.key)) return;
  invalidatePreview(); transferStatus("其他页面已修改确认，请重新预览或刷新页面，当前输入保留。", true);
});
refreshUndo();
