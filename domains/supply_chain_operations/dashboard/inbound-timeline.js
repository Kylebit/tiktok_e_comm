(function (root) {
  const DAY_MS = 24 * 60 * 60 * 1000;

  function parseDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      throw new TypeError("date must be YYYY-MM-DD");
    }
    const timestamp = Date.parse(`${value}T00:00:00Z`);
    if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString().slice(0, 10) !== value) {
      throw new TypeError("date must be a real calendar date");
    }
    return timestamp;
  }

  function addDays(value, days) {
    if (!Number.isInteger(days) || days < 0) throw new TypeError("days must be a nonnegative integer");
    return new Date(parseDate(value) + days * DAY_MS).toISOString().slice(0, 10);
  }

  function daysBetween(start, end) {
    return Math.max(0, Math.ceil((parseDate(end) - parseDate(start)) / DAY_MS));
  }

  function consume(stock, dailyVelocity, days) {
    if (days <= 0) return stock;
    return Math.max(0, stock - Math.ceil(dailyVelocity * days));
  }

  function consumptionStep({snapshotDate, startDay, endDay, stock, dailyVelocity}) {
    const days = endDay - startDay;
    const demand = Math.ceil(dailyVelocity * days);
    const stockAfter = consume(stock, dailyVelocity, days);
    const localFulfilledDemand = Math.min(stock, demand);
    const crossBorderFallbackDemand = Math.max(0, demand - localFulfilledDemand);
    const coveredDays = crossBorderFallbackDemand > 0 && dailyVelocity > 0
      ? Math.min(days, Math.floor(stock / dailyVelocity))
      : days;
    return {
      kind: "CONSUMPTION",
      fromDate: addDays(snapshotDate, startDay),
      toDate: addDays(snapshotDate, endDay),
      days,
      stockBefore: stock,
      demand,
      stockAfter,
      unmetDemand: crossBorderFallbackDemand,
      localFulfilledDemand,
      crossBorderFallbackDemand,
      localStockoutStartDate: crossBorderFallbackDemand > 0
        ? addDays(snapshotDate, startDay + coveredDays)
        : null,
      crossBorderFallbackDays: crossBorderFallbackDemand > 0 ? Math.max(0, days - coveredDays) : 0
    };
  }

  function projectSupply({snapshotDate, nextArrivalDate, available, dailyVelocity, inboundEvents}) {
    if (!Number.isInteger(available) || available < 0) throw new TypeError("available must be a nonnegative integer");
    if (typeof dailyVelocity !== "number" || !Number.isFinite(dailyVelocity) || dailyVelocity < 0) {
      throw new TypeError("dailyVelocity must be a nonnegative finite number");
    }
    parseDate(snapshotDate);
    parseDate(nextArrivalDate);
    const horizonDays = daysBetween(snapshotDate, nextArrivalDate);
    const events = (inboundEvents || []).map(event => {
      if (typeof event.batchId !== "string" || !event.batchId.trim()) {
        throw new TypeError("inbound batchId must be a nonempty string");
      }
      if (!Number.isInteger(event.quantity) || event.quantity < 0) {
        throw new TypeError("inbound quantity must be a nonnegative integer");
      }
      parseDate(event.estimatedSellableDate);
      return {...event, day: daysBetween(snapshotDate, event.estimatedSellableDate)};
    }).sort((left, right) => left.day - right.day);

    let stock = available;
    let lastDay = 0;
    let countedInbound = 0;
    let pendingInbound = 0;
    let localFulfilledUnits = 0;
    let crossBorderFallbackUnits = 0;
    let crossBorderFallbackDays = 0;
    let firstLocalStockoutDate = null;
    const steps = [];
    events.forEach(event => {
      if (event.day > horizonDays) {
        pendingInbound += event.quantity;
        return;
      }
      if (event.day > lastDay) {
        const step = consumptionStep({
          snapshotDate, startDay: lastDay, endDay: event.day, stock, dailyVelocity
        });
        steps.push(step);
        localFulfilledUnits += step.localFulfilledDemand;
        crossBorderFallbackUnits += step.crossBorderFallbackDemand;
        crossBorderFallbackDays += step.crossBorderFallbackDays;
        firstLocalStockoutDate ||= step.localStockoutStartDate;
        stock = step.stockAfter;
      }
      const stockBefore = stock;
      stock += event.quantity;
      countedInbound += event.quantity;
      steps.push({
        kind: "INBOUND",
        date: event.estimatedSellableDate,
        batchId: event.batchId,
        quantity: event.quantity,
        stockBefore,
        stockAfter: stock
      });
      lastDay = event.day;
    });
    if (horizonDays > lastDay) {
      const step = consumptionStep({
        snapshotDate, startDay: lastDay, endDay: horizonDays, stock, dailyVelocity
      });
      steps.push(step);
      localFulfilledUnits += step.localFulfilledDemand;
      crossBorderFallbackUnits += step.crossBorderFallbackDemand;
      crossBorderFallbackDays += step.crossBorderFallbackDays;
      firstLocalStockoutDate ||= step.localStockoutStartDate;
      stock = step.stockAfter;
    }
    const projectedDemand = localFulfilledUnits + crossBorderFallbackUnits;
    return {
      projectedStock: Math.max(0, Math.floor(stock)),
      countedInbound,
      pendingInbound,
      horizonDays,
      localFulfilledUnits,
      crossBorderFallbackUnits,
      crossBorderFallbackDays,
      firstLocalStockoutDate,
      localFulfillmentRate: projectedDemand > 0 ? localFulfilledUnits / projectedDemand : null,
      events,
      steps,
      projectionMethod: "TIME_PHASED_BATCH_EVENTS_V1"
    };
  }

  root.SUPPLY_CHAIN_TIMELINE = {addDays, daysBetween, projectSupply};
})(typeof window === "undefined" ? globalThis : window);

// One batch override contract shared by the ETA editor and SKU projection.
// The legacy key is read-only. One atomic v4 value holds changes and import undo.
(function (root) {
  const LEGACY_KEY = "supply-chain-inbound-batch-timing-v3";
  const KEY = "supply-chain-inbound-batch-timing-v4";
  const SCHEMA = "supply-chain-batch-transfer/v1";
  const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const canonical = value => JSON.stringify(Array.isArray(value) ? value.map(v => JSON.parse(canonical(v)))
    : object(value) ? Object.fromEntries(Object.keys(value).sort().map(k => [k, JSON.parse(canonical(value[k]))])) : value);
  const clone = value => JSON.parse(JSON.stringify(value));
  const id = (region, batchId) => `${region}:${batchId}`;
  const exact = value => typeof value === "string" && !!value.trim() && !/[\u0000-\u001f*…]/.test(value) && !value.includes("...");
  async function digest(value) {
    const bytes = new TextEncoder().encode(canonical(value));
    return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map(x => x.toString(16).padStart(2, "0")).join("");
  }
  function basis(batch, data, plan) {
    return {region: batch.region, batchId: batch.batchId, warehouse: data.config[batch.region]?.warehouse ?? null,
      skuQuantities: batch.skuQuantities ?? {}, totalUnits: batch.totalUnits ?? null,
      createdAt: batch.createdAt ?? null, anchorAt: batch.anchorAt ?? null, estimatedAnchorAt: batch.estimatedAnchorAt ?? null,
      estimatedSellableDate: batch.estimatedSellableDate ?? null,
      estimatedSellableConfirmedAt: batch.estimatedSellableConfirmedAt ?? null, transportDays: batch.transportDays ?? null,
      inboundCapturedAt: plan.capturedAt ?? null, source: plan.source ?? batch.source ?? null};
  }
  function validTiming(saved, batch) {
    if (!object(saved) || typeof saved.anchorAt !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(saved.anchorAt)) return false;
    try {
      root.SUPPLY_CHAIN_TIMELINE.daysBetween(saved.anchorAt.slice(0, 10), saved.estimatedSellableDate);
      if (!Number.isFinite(Date.parse(saved.anchorAt)) || saved.anchorAt.slice(11, 13) > "23" || saved.anchorAt.slice(14) > "59"
          || saved.anchorAt < batch.createdAt.slice(0, 16) || saved.estimatedSellableDate < saved.anchorAt.slice(0, 10)) return false;
    } catch { return false; }
    const planConfirmationTime = Date.parse(batch.estimatedSellableConfirmedAt || "");
    const overrideUpdateTime = Date.parse(saved.updatedAt || "");
    return Number.isFinite(overrideUpdateTime) && (!Number.isFinite(planConfirmationTime)
      || (saved.basePlanConfirmedAt === batch.estimatedSellableConfirmedAt && overrideUpdateTime > planConfirmationTime));
  }
  function current(saved, batch, data, plan) {
    if (!validTiming(saved, batch)) return null;
    if (saved.binding && canonical(saved.binding) !== canonical(basis(batch, data, plan))) return null;
    return saved;
  }
  function read(storage) {
    const raw = storage.getItem(KEY), legacy = storage.getItem(LEGACY_KEY);
    const state = raw === null ? null : JSON.parse(raw);
    if (raw !== null && (!object(state) || state.schema !== "supply-chain-batch-local/v4" || !object(state.values))) throw Error("本地确认版本损坏，请保留原数据后恢复");
    const values = state ? state.values : JSON.parse(legacy || "{}");
    if (!object(values)) throw Error("本地确认必须为对象");
    return {token: JSON.stringify([raw, legacy]), values, state};
  }
  function write(storage, previous, values, undo = null) {
    if (read(storage).token !== previous.token) throw Error("其他页面已修改本地确认，请重新预览或刷新后重试");
    const state = {schema: "supply-chain-batch-local/v4", revision: crypto.randomUUID(), values, undo};
    storage.setItem(KEY, JSON.stringify(state)); // Commit before exposing changed memory.
    return read(storage);
  }
  const batches = plan => Object.entries(plan.regions).flatMap(([region, p]) => (p.batches || []).map(b => ({...b, region})));
  function validIdentity(value) {
    return ["MY", "TH", "VN", "PH"].includes(value.region) && exact(value.batchId) && exact(value.warehouse)
      && object(value.skuQuantities) && Object.keys(value.skuQuantities).length > 0
      && Object.entries(value.skuQuantities).every(([sku, n]) => exact(sku) && Number.isInteger(n) && n >= 0)
      && Number.isInteger(value.totalUnits) && value.totalUnits >= 0
      && Object.values(value.skuQuantities).reduce((sum, n) => sum + n, 0) === value.totalUnits;
  }
  async function exportBundle(values, data, plan, origin) {
    const entries = [], excluded = [];
    for (const [key, saved] of Object.entries(values)) {
      const batch = batches(plan).find(b => id(b.region, b.batchId) === key);
      if (!batch || !current(saved, batch, data, plan) || !validIdentity(basis(batch, data, plan)) || !saved.sourceNote?.trim()) {
        excluded.push({key, reason: "身份、当前版本、完整SKU分摊或确认依据不满足；原本地数据保留"}); continue;
      }
      const binding = basis(batch, data, plan);
      const confirmation = {anchorAt: saved.anchorAt, anchorDate: saved.anchorAt.slice(0, 10),
        estimatedSellableDate: saved.estimatedSellableDate, sourceNote: saved.sourceNote,
        basePlanConfirmedAt: saved.basePlanConfirmedAt ?? null, updatedAt: saved.updatedAt};
      entries.push({binding, sourceBatchDigest: await digest(binding), confirmation});
    }
    const payload = {schema: SCHEMA, source: {origin, exportedAt: new Date().toISOString(),
      inventoryCapturedAt: data.snapshotDate ?? null, orderCapturedAt: data.orderDemandCapturedAt ?? null,
      inboundCapturedAt: plan.capturedAt ?? null}, entries};
    return {bundle: {...payload, digest: await digest(payload)}, excluded};
  }
  async function preview(text, previous, data, plan) {
    if (typeof text !== "string" || text.length > 1000000) throw Error("导入文件为空或超过1MB");
    const bundle = JSON.parse(text), {digest: expected, ...payload} = bundle;
    if (bundle.schema !== SCHEMA || !Array.isArray(bundle.entries) || bundle.entries.length > 1000
        || !object(bundle.source) || !Number.isFinite(Date.parse(bundle.source.exportedAt))) throw Error("不支持的迁移包或导出时间");
    const url = new URL(bundle.source.origin);
    if (!["http:", "https:"].includes(url.protocol) || url.origin !== bundle.source.origin) throw Error("来源origin无效");
    if (expected !== await digest(payload)) throw Error("迁移包摘要不一致，请保留原文件重新导出");
    const rows = [], seen = new Set();
    for (const entry of bundle.entries) {
      if (!object(entry) || !object(entry.binding)) throw Error("缺少完整批次身份");
      const binding = entry.binding, key = id(binding.region, binding.batchId);
      if (seen.has(key)) throw Error("迁移包内重复批次，不能决定覆盖顺序");
      seen.add(key);
      const batch = batches(plan).find(b => id(b.region, b.batchId) === key);
      let status = "APPLICABLE", detail = "可导入";
      if (!validIdentity(binding) || entry.sourceBatchDigest !== await digest(binding)) { status = "INVALID"; detail = "完整国家/批次/SKU分摊或源摘要无效"; }
      else if (!batch) { status = "IDENTITY_MISMATCH"; detail = "当前来源不存在这个完整批次"; }
      else if (canonical(binding) !== canonical(basis(batch, data, plan))) { status = "STALE_PLAN"; detail = "批次源事实或确认版本已变化，须重新核对"; }
      else if (!validTiming(entry.confirmation, batch) || typeof entry.confirmation.sourceNote !== "string" || !entry.confirmation.sourceNote.trim()) { status = "INVALID"; detail = "时间已过期或缺确认依据"; }
      const value = {...entry.confirmation, binding};
      if (status === "APPLICABLE" && Object.prototype.hasOwnProperty.call(previous.values, key)) {
        const existing = previous.values[key];
        const same = current(existing, batch, data, plan) && ["anchorAt", "estimatedSellableDate", "sourceNote", "basePlanConfirmedAt", "updatedAt"]
          .every(field => (existing[field] ?? null) === (value[field] ?? null));
        status = same ? "DUPLICATE" : "CONFLICT"; detail = same ? "已存在相同确认，不写入" : "本地已有不同确认；默认保留本地，须逐条选择替换";
      }
      rows.push({key, status, detail, value, existing: previous.values[key] ?? null});
    }
    return {bundle, rows, token: previous.token, input: text};
  }
  function apply(storage, prepared, selected) {
    const previous = read(storage);
    if (previous.token !== prepared.token) throw Error("预览后本地数据已变化，请重新预览");
    const allowed = prepared.rows.filter(row => selected.includes(row.key));
    if (!allowed.length || allowed.some(r => !["APPLICABLE", "CONFLICT"].includes(r.status))) throw Error("没有可导入且已选择的确认");
    const values = clone(previous.values);
    for (const row of allowed) values[row.key] = row.value;
    return write(storage, previous, values, {values: previous.values, importDigest: prepared.bundle.digest, source: prepared.bundle.source});
  }
  function undo(storage, previous) {
    if (!previous.state?.undo) throw Error("没有可撤销的最近导入；后续修改不会被覆盖");
    return write(storage, previous, previous.state.undo.values);
  }
  root.SUPPLY_CHAIN_OVERRIDES = {KEY, LEGACY_KEY, current, basis, read, write, exportBundle, preview, apply, undo};
})(typeof window === "undefined" ? globalThis : window);
