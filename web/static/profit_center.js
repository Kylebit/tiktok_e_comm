(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
  const money = (value) => {
    const number = Number(value);
    return Number.isFinite(number)
      ? new Intl.NumberFormat("zh-CN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(number)
      : "—";
  };
  const numberText = (value) => {
    const number = Number(value);
    return Number.isFinite(number)
      ? new Intl.NumberFormat("zh-CN").format(number)
      : "—";
  };
  const dateText = (value) => {
    const raw = String(value || "");
    if (!raw) return "—";
    const parsed = new Date(raw);
    if (!Number.isNaN(parsed.getTime())) {
      return new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).format(parsed);
    }
    return raw.slice(0, 10);
  };
  const dateTimeText = (value) => {
    const raw = String(value || "");
    if (!raw) return "—";
    const parsed = new Date(raw);
    return Number.isNaN(parsed.getTime())
      ? raw
      : new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(parsed);
  };
  const setBadge = (element, text, tone = "neutral") => {
    element.textContent = text;
    element.className = `badge ${tone}`;
  };
  const setLoading = (form, loading) => {
    form.classList.toggle("is-loading", loading);
    const button = form.querySelector('button[type="submit"]');
    if (button) button.disabled = loading;
  };
  const showAlert = (element, message, success = false) => {
    element.textContent = message;
    element.classList.toggle("success", success);
    element.hidden = !message;
  };
  const isMissing = (summary, fragment) =>
    (summary.decision_blockers || []).some((code) => String(code).includes(fragment));

  const qualityLabels = {
    "upstream:missing_quantity": ["订单数量缺失", "无法把单位成本可靠换算为订单总成本"],
    "upstream:missing_cost": ["商品成本缺失", "相关订单不能形成完整利润"],
    "upstream:missing_fx": ["汇率证据缺失", "当地币种不能可靠折算为人民币"],
    "upstream:missing_ad_spend": ["广告支出未接入", "当前广告成本不是受治理的实际支出"],
    "upstream:missing_settlement": ["结算金额缺失", "平台净结算事实不完整"],
    "upstream:missing_occurred_at": ["发生时间缺失", "相关记录无法归入本周"],
    "upstream:conflicting_cost": ["成本版本冲突", "同一 Seller SKU 存在多个正成本，需要确认主数据"],
    "report_status:needs_review": ["周报需要复核", "至少一个经营数据质量门尚未关闭"],
    "report_status:failed": ["周报生成失败", "本周不能形成经营结论"],
  };

  async function fetchJson(url) {
    const response = await fetch(url, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({
      ok: false,
      error: `HTTP ${response.status}`,
    }));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  }

  function previousCompleteWeek() {
    const today = new Date();
    today.setHours(12, 0, 0, 0);
    const weekdayFromMonday = (today.getDay() + 6) % 7;
    const thisMonday = new Date(today);
    thisMonday.setDate(today.getDate() - weekdayFromMonday);
    const start = new Date(thisMonday);
    start.setDate(start.getDate() - 7);
    const end = new Date(thisMonday);
    end.setDate(end.getDate() - 1);
    const localIso = (date) => {
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, "0");
      const day = String(date.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    };
    return [localIso(start), localIso(end)];
  }

  function unpackWeekly(payload) {
    if (payload && payload.summary) return payload.summary;
    if (payload && payload.weekly_profit) return payload.weekly_profit;
    return payload || {};
  }

  function renderUnavailableWeekly(summary) {
    const verdict = $("#weeklyVerdict");
    verdict.className = "verdict-card unavailable";
    $("#weeklyStatusLabel").textContent = "NO WEEKLY REPORT";
    $("#weeklyVerdictTitle").textContent = "暂时没有可读取的周报";
    $("#weeklyVerdictText").textContent = "请确认本地结算快照已准备完成，再生成上一完整自然周。";
    setBadge($("#weeklyStatusBadge"), "NOT GENERATED", "neutral");
    $("#profitMetric").textContent = "暂不可确认";
    $("#profitMetricNote").textContent = "没有周报证据";
    $("#settlementMetric").textContent = "—";
    $("#costMetric").textContent = "—";
    $("#adMetric").textContent = "—";
    $("#qualityCount").textContent = "0";
    $("#qualitySummary").classList.remove("skeleton-lines");
    $("#qualitySummary").textContent = "尚无数据质量记录。";
    $("#qualityList").innerHTML = "";
    $("#evidenceGrid").classList.remove("skeleton-lines");
    $("#evidenceGrid").innerHTML = "";
    setBadge($("#freshnessBadge"), "UNKNOWN", "neutral");
    $("#negativeQueue").hidden = true;
  }

  function renderQuality(summary) {
    const affected = summary.quality_affected_row_counts || {};
    const issues = Array.isArray(summary.quality_issues) ? summary.quality_issues : [];
    const blockers = Array.isArray(summary.decision_blockers) ? summary.decision_blockers : [];
    const codes = [...new Set([
      ...Object.keys(affected),
      ...issues.map((issue) => String(issue.code || "unknown")),
      ...blockers,
    ])];

    setBadge(
      $("#qualityCount"),
      summary.decision_usable ? "GATE PASSED" : `${summary.quality_issue_group_count || codes.length} GROUPS`,
      summary.decision_usable ? "safe" : "warn",
    );
    const normalized = summary.source_row_counts?.normalized;
    $("#qualitySummary").classList.remove("skeleton-lines");
    $("#qualitySummary").textContent = summary.decision_usable
      ? `本周 ${numberText(normalized)} 条规范化记录已通过利润数据质量门。`
      : `当前只允许查看结算和证据；在下列问题关闭前，利润结论不会开放。`;

    $("#qualityList").innerHTML = codes.length
      ? codes.map((code) => {
        const [title, explanation] = qualityLabels[code] || [
          code.replace(/^upstream:/, ""),
          issues.find((issue) => issue.code === code)?.message || "需要数据运营复核。",
        ];
        const count = affected[code];
        const countText = Number.isFinite(Number(count))
          ? `${numberText(count)} 条/项受影响`
          : "需要复核";
        return `<div class="quality-item">
          <div><strong>${esc(title)}</strong><small>${esc(explanation)}</small></div>
          <span>${esc(countText)}</span>
        </div>`;
      }).join("")
      : `<div class="quality-item">
        <div><strong>没有阻断项</strong><small>当前周报可以进入经营决策复核。</small></div>
        <span>通过</span>
      </div>`;
  }

  function renderEvidence(summary) {
    const period = summary.period || {};
    const freshness = summary.freshness || {};
    const counts = summary.source_row_counts || {};
    const snapshot = String(summary.snapshot_id || "");
    const evidence = [
      ["报告周期", `${dateText(period.start)} — ${dateText(period.end)}`],
      ["生成时间", dateTimeText(summary.generated_at)],
      ["数据新鲜度", freshness.state || "unknown"],
      ["最新发生时间", dateText(freshness.newest_occurred_at)],
      ["源文件", `${numberText(summary.source_file_count)} 个`],
      ["规范化记录", `${numberText(counts.normalized)} / 原始 ${numberText(counts.raw)}`],
      ["拒绝记录", numberText(counts.rejected)],
      ["快照指纹", snapshot ? `${snapshot.slice(0, 22)}…` : "—"],
    ];
    $("#evidenceGrid").classList.remove("skeleton-lines");
    $("#evidenceGrid").innerHTML = evidence.map(([label, value]) =>
      `<div><dt>${esc(label)}</dt><dd title="${esc(value)}">${esc(value)}</dd></div>`).join("");
    const freshnessTone = freshness.state === "fresh" ? "safe" : "warn";
    setBadge($("#freshnessBadge"), freshness.state || "UNKNOWN", freshnessTone);
  }

  function renderNegativeQueue(summary) {
    const rows = Array.isArray(summary.negative_profit_skus)
      ? summary.negative_profit_skus
      : [];
    $("#negativeQueue").hidden = rows.length === 0;
    setBadge($("#negativeCount"), `${rows.length} CANDIDATES`, "warn");
    $("#negativeList").innerHTML = rows.map((row) => {
      const identity = row.sku_id || "unknown";
      const origin = [row.channel, row.region].filter(Boolean).join(" · ");
      const amount = summary.decision_usable
        ? ` · ¥ ${money(row.profit_cny)}`
        : "";
      return `<span class="candidate"><strong>${esc(identity)}</strong>${esc(origin)}${esc(amount)}</span>`;
    }).join("");
  }

  function renderWeekly(summary) {
    if (!summary || summary.available === false || summary.status === "not_generated") {
      renderUnavailableWeekly(summary || {});
      return;
    }

    const usable = summary.decision_usable === true;
    const verdict = $("#weeklyVerdict");
    verdict.className = `verdict-card ${usable ? "ready" : "needs-review"}`;
    $("#weeklyStatusLabel").textContent = usable ? "DECISION GATE PASSED" : "DATA REVIEW REQUIRED";
    $("#weeklyVerdictTitle").textContent = usable
      ? "本周数据质量门已通过"
      : "本周利润暂不可确认";
    $("#weeklyVerdictText").textContent = usable
      ? "结算、成本、汇率和广告支出证据完整，可进入经营决策复核。"
      : "结算事实仍可查看，但成本、数量或广告支出口径尚未闭合。";
    setBadge($("#weeklyStatusBadge"), summary.status || "UNKNOWN", usable ? "safe" : "warn");

    const totals = summary.totals || {};
    $("#profitMetric").textContent = usable ? `¥ ${money(totals.profit_cny)}` : "暂不可确认";
    $("#profitMetricNote").textContent = usable
      ? "可进入经营决策复核"
      : "不展示不完整利润";
    $("#settlementMetric").textContent = `¥ ${money(totals.settlement_cny)}`;
    $("#costMetric").textContent = `¥ ${money(totals.cost_cny)}`;
    $("#costMetricNote").textContent = isMissing(summary, "missing_quantity") ||
      isMissing(summary, "missing_cost")
      ? "存在缺口 · 不是总成本"
      : `${numberText(summary.realized_bucket_count)} 个渠道 SKU 桶`;
    if (isMissing(summary, "missing_ad_spend")) {
      $("#adMetric").textContent = "未接入";
      $("#adMetricNote").textContent = "不能用 0 代替实际支出";
    } else {
      $("#adMetric").textContent = `¥ ${money(totals.ad_cost_cny)}`;
      $("#adMetricNote").textContent = "已纳入周度利润口径";
    }

    renderQuality(summary);
    renderEvidence(summary);
    renderNegativeQueue(summary);
  }

  async function loadWeekly() {
    const form = $("#weeklyForm");
    setLoading(form, true);
    showAlert($("#weeklyAlert"), "");
    const params = new URLSearchParams({
      start: $("#weekStart").value,
      end: $("#weekEnd").value,
    });
    try {
      const payload = await fetchJson(`/api/profit-center/weekly?${params}`);
      renderWeekly(unpackWeekly(payload));
    } catch (error) {
      showAlert($("#weeklyAlert"), error.message || "周报读取失败");
      renderUnavailableWeekly({});
    } finally {
      setLoading(form, false);
    }
  }

  function confidenceLabel(main) {
    const confidence = String(main?.confidence || "");
    if (confidence === "posterior") return "样本后验";
    if (confidence === "prior_or_sparse") return "先验 / 样本稀疏";
    if (confidence === "sparse") return "样本不足";
    return confidence || "未标注";
  }

  function sampleText(platform) {
    const posterior = platform.posterior || {};
    if (platform.platform === "tiktok") {
      return `${numberText(posterior.comps_in_window)} 条窗口样本`;
    }
    if (platform.platform === "shopee") {
      return `${numberText(posterior.comps_same_sku)} 条同 SKU 样本`;
    }
    return "—";
  }

  function renderPlatform(platform, requestedSku) {
    const label = platform.platform === "tiktok" ? "TikTok TH" :
      platform.platform === "shopee" ? "Shopee TH" :
        String(platform.platform || "平台");
    if (!platform.ok) {
      return `<article class="platform-card failed">
        <div class="platform-title">
          <div><h4>${esc(label)}</h4><p>没有形成估算</p></div>
          <span class="badge danger">UNAVAILABLE</span>
        </div>
        <div class="assumption-box">${esc(platform.error || "未找到匹配商品或数据不足。")}</div>
      </article>`;
    }

    const product = platform.product || {};
    const main = platform.main || {};
    const fx = platform.fx || {};
    const warnings = Array.isArray(platform.warnings) ? platform.warnings : [];
    const platformSku = product.sku_id || product.model_id || "—";
    const negative = Number(main.profit_cny) < 0;
    const rate = Number(platform.ad_rate);
    const adPercent = Number.isFinite(rate) ? `${money(rate * 100)}%` : "—";
    const fxValue = fx.THB_CNY;
    const costSource = product.cost_source || "未标注";
    const saleBasis = product.sale_basis || product.price_source || "当前目录价";
    const assumption = platform.ad_note ||
      "结果使用当前广告费率假设；实际广告支出尚未按 SKU 分摊。";

    return `<article class="platform-card">
      <div class="platform-title">
        <div>
          <h4>${esc(label)}</h4>
          <p>${esc((product.product_name || product.model_name || requestedSku).slice(0, 90))}</p>
        </div>
        <span class="badge warn">ESTIMATE</span>
      </div>
      <div class="estimate-value${negative ? " negative" : ""}">¥ ${money(main.profit_cny)}</div>
      <div class="estimate-caption">单件估算利润 · 利润率 ${money(main.margin_pct)}%</div>
      <dl class="estimate-facts">
        <div><dt>Seller SKU</dt><dd>${esc(product.seller_sku || requestedSku)}</dd></div>
        <div><dt>平台 SKU ID</dt><dd>${esc(platformSku)}</dd></div>
        <div><dt>估算售价</dt><dd>${money(product.sale_local)} ${esc(platform.currency || "")}</dd></div>
        <div><dt>售价依据</dt><dd>${esc(saleBasis)}</dd></div>
        <div><dt>商品成本</dt><dd>¥ ${money(product.cost_cny)}</dd></div>
        <div><dt>成本来源</dt><dd>${esc(costSource)}</dd></div>
        <div><dt>汇率</dt><dd>${money(fxValue)} · ${esc(fx.as_of ? dateText(fx.as_of) : "未标时间")}</dd></div>
        <div><dt>样本置信度</dt><dd>${esc(confidenceLabel(main))} · ${esc(sampleText(platform))}</dd></div>
      </dl>
      <div class="assumption-box">广告假设 ${esc(adPercent)}。${esc(assumption)}</div>
      ${warnings.length
        ? `<ul class="warning-list">${warnings.map((warning) => `<li>${esc(warning)}</li>`).join("")}</ul>`
        : ""}
    </article>`;
  }

  function renderSku(payload) {
    const platforms = Object.values(payload.platforms || {});
    $("#skuResult").className = "sku-result";
    const requestedSku = payload.sku || $("#skuInput").value.trim();
    const partial = payload.partial === true;
    $("#skuResult").innerHTML = `
      <div class="sku-result-head">
        <div>
          <h3>${esc(requestedSku)}</h3>
          <p>广告假设 ${money(Number(payload.ad_rate) * 100)}% · 回看 ${esc(payload.lookback_days ?? "平台默认")} 天 · 单件估算</p>
        </div>
        <span class="badge ${partial ? "warn" : "safe"}">${partial ? "PARTIAL" : "CALCULATED"}</span>
      </div>
      <div class="platform-grid">
        ${platforms.map((platform) => renderPlatform(platform, requestedSku)).join("")}
      </div>`;
  }

  async function loadSku() {
    const form = $("#skuForm");
    setLoading(form, true);
    showAlert($("#skuAlert"), "");
    const sku = $("#skuInput").value.trim();
    const params = new URLSearchParams({
      sku,
      platform: $("#skuPlatform").value,
      ad_rate_percent: $("#skuAdRate").value,
      lookback_days: $("#skuLookback").value,
    });
    try {
      const payload = await fetchJson(`/api/sku-profit?${params}`);
      renderSku(payload);
      const url = new URL(window.location.href);
      url.searchParams.set("sku", sku);
      history.replaceState({}, "", url);
    } catch (error) {
      $("#skuResult").className = "sku-result empty-state";
      $("#skuResult").innerHTML =
        "<strong>没有可展示的估算</strong><span>请检查 SKU、平台和输入参数。</span>";
      showAlert($("#skuAlert"), error.message || "SKU 查询失败");
    } finally {
      setLoading(form, false);
    }
  }

  $("#weeklyForm").addEventListener("submit", (event) => {
    event.preventDefault();
    loadWeekly();
  });
  $("#skuForm").addEventListener("submit", (event) => {
    event.preventDefault();
    loadSku();
  });

  const [weekStart, weekEnd] = previousCompleteWeek();
  $("#weekStart").value = weekStart;
  $("#weekEnd").value = weekEnd;
  const initialSku = new URLSearchParams(window.location.search).get("sku");
  if (initialSku) {
    $("#skuInput").value = initialSku;
  }
  loadWeekly();
  if (initialSku) loadSku();
})();
