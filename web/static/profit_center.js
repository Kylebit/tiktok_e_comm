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
  const sampleViews = new Map();

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

  function priceBasisLabel(value) {
    const labels = {
      recent_comp_median_paid: "近单实付中位价",
      product_api: "商品接口目录价",
      shopee_products_db: "Shopee 商品目录价",
    };
    return labels[value] || value || "当前目录价";
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

  function costLineage(platform, product) {
    const source = String(product.cost_source || "");
    if (source === "sku_costs_via_tk_seller_sku_tail4") {
      return {
        label: "跨平台尾四位回退",
        path: "Shopee Seller SKU 尾四位 → TikTok Seller SKU 尾四位 → TikTok sku_costs",
        risk: "不是 Shopee 直接成本。同尾四位可能跨区域对应多个商品或不同成本；用于定价前必须核对商品身份与成本版本。",
        tone: "risk",
      };
    }
    if (source === "shopee_weekly_product_cost") {
      return {
        label: "Shopee 周快照成本",
        path: "Shopee 周度订单快照 → 商品成本字段",
        risk: "历史快照可能缺少数量或把单件成本与订单行总成本混用，必须结合 quantity 与快照版本复核。",
        tone: "risk",
      };
    }
    if (source === "manual_override") {
      return {
        label: "人工覆盖成本",
        path: "本次请求人工成本 → 利润估算",
        risk: "API 未提供审批人、审批时间或成本版本；该值不能直接成为财务事实。",
        tone: "risk",
      };
    }
    if (source === "sku_costs" && platform.platform === "tiktok") {
      return {
        label: "TikTok 成本库",
        path: "优先：平台 SKU ID 精确匹配 → sku_costs；缺失时后端可能按 Seller SKU 尾四位回退",
        risk: "响应只标注 sku_costs，没有暴露本次究竟命中精确 SKU 还是尾四位回退。若进入定价决策，需要后端补充 resolved_by 与 cost_version。",
        tone: "caution",
      };
    }
    return {
      label: source || "未提供成本来源",
      path: "当前 API 未提供可复核的成本匹配路径",
      risk: "缺少稳定成本来源时，利润估算只能作为线索，不能用于发布定价或财务结账。",
      tone: "risk",
    };
  }

  function imageMarkup(platform, product, label) {
    const imageUrl = String(product.image_url || "").trim();
    if (!/^https:\/\//i.test(imageUrl)) {
      return `<div class="product-image product-image-missing" role="img" aria-label="${esc(label)} 商品主图缺失">
        <span>暂无主图</span>
      </div>`;
    }
    const proxyUrl = `/api/proxy-image?url=${encodeURIComponent(imageUrl)}`;
    return `<div class="product-image">
      <img src="${esc(proxyUrl)}" alt="${esc(label)} 商品主图" loading="lazy" data-product-image>
      <span class="image-fallback" hidden>主图读取失败</span>
    </div>`;
  }

  function priceEvidenceMarkup(product, currency) {
    const sale = product.sale_local;
    const recent = product.recent_avg_sale_local;
    const list = product.list_price_local;
    const basis = priceBasisLabel(product.sale_basis || product.price_source);
    return `<section class="price-evidence" aria-label="售价证据">
      <div class="audit-heading">
        <div><span>PRICE BASIS</span><h5>售价口径</h5></div>
        <span class="badge neutral">${esc(basis)}</span>
      </div>
      <div class="price-evidence-grid">
        <div class="primary"><span>本次估算采用</span><strong>${money(sale)} ${esc(currency)}</strong></div>
        <div><span>近单实付中位价</span><strong>${money(recent)} ${esc(currency)}</strong></div>
        <div><span>当前商品标价</span><strong>${money(list)} ${esc(currency)}</strong></div>
      </div>
      <p>“近单实付中位价”来自回看窗口内可用订单的实付价格中位数；“当前商品标价”是目录或商品库中的挂牌价。促销、优惠券和价格变更会让两者不同，本次利润采用前者，不把挂牌价当作真实成交价。</p>
    </section>`;
  }

  function waterfallRow(label, value, maxValue, currency, kind = "deduction", note = "") {
    const numeric = Number(value);
    const width = Number.isFinite(numeric) && maxValue > 0
      ? Math.max(3, Math.min(100, Math.abs(numeric) / maxValue * 100))
      : 0;
    const prefix = kind === "deduction" && numeric > 0 ? "− " : "";
    return `<div class="waterfall-row ${esc(kind)}">
      <div class="waterfall-label"><span>${esc(label)}</span>${note ? `<small>${esc(note)}</small>` : ""}</div>
      <div class="waterfall-track"><i style="width:${width.toFixed(1)}%"></i></div>
      <strong>${prefix}${money(numeric)} ${esc(currency)}</strong>
    </div>`;
  }

  function mainWaterfallMarkup(platform, product, main) {
    const currency = platform.currency || "";
    const sale = Number(product.sale_local);
    const settlement = Number(main.est_settlement_local);
    const ad = Number(main.ad_local);
    const profit = Number(main.profit_local);
    const platformDeductions = sale - settlement;
    const goodsLocal = settlement - ad - profit;
    const values = [sale, settlement, platformDeductions, ad, goodsLocal, profit]
      .filter(Number.isFinite).map(Math.abs);
    const maxValue = Math.max(...values, 1);
    return `<section class="waterfall-card">
      <div class="audit-heading">
        <div><span>MAIN · POSTERIOR</span><h5>主估算费用瀑布</h5></div>
        <span class="badge warn">${esc(confidenceLabel(main))}</span>
      </div>
      <p class="audit-note">主估算使用样本后验结算额。平台佣金、交易费、联盟费与税费目前只以“平台结算扣减”合并体现；商品成本为结算额减广告与利润的反推值。</p>
      <div class="waterfall">
        ${waterfallRow("实付销售额", sale, maxValue, currency, "income")}
        ${waterfallRow("平台结算扣减（合并）", platformDeductions, maxValue, currency, "deduction", "销售额 − 预估净结算")}
        ${waterfallRow("预估净结算", settlement, maxValue, currency, "subtotal")}
        ${waterfallRow("广告成本", ad, maxValue, currency, "deduction")}
        ${waterfallRow("商品成本（反推）", goodsLocal, maxValue, currency, "deduction", "净结算 − 广告 − 利润")}
        ${waterfallRow("单件利润", profit, maxValue, currency, profit < 0 ? "result negative" : "result")}
      </div>
    </section>`;
  }

  function priorWaterfallMarkup(platform) {
    const prior = platform.prior || {};
    const currency = platform.currency || "";
    const variants = [
      ["with_affiliate", "有联盟佣金先验"],
      ["no_affiliate", "无联盟佣金先验"],
    ];
    const parts = variants.map(([key, title]) => {
      const result = prior[key] || {};
      const breakdown = result.breakdown;
      if (!breakdown || typeof breakdown !== "object") return "";
      const rows = [
        ["销售额", breakdown.sale_local, "income"],
        ["商品成本", breakdown.goods_local, "deduction"],
        ["物流", breakdown.logistics_local, "deduction"],
        ["平台佣金", breakdown.commission_local, "deduction"],
        ["交易费", breakdown.transaction_local, "deduction"],
        ["额外平台费", breakdown.extra_local, "deduction"],
        ["达人佣金", breakdown.creator_local, "deduction"],
        ["联盟佣金", breakdown.affiliate_local, "deduction"],
        ["广告", breakdown.ad_local, "deduction"],
        ["卖家税费", breakdown.seller_tax_local, "deduction"],
        ["固定费用", breakdown.fixed_fee_local, "deduction"],
        ["预估净结算", result.est_settlement_local, "subtotal"],
        ["先验利润", result.profit_local, Number(result.profit_local) < 0 ? "result negative" : "result"],
      ];
      const maxValue = Math.max(...rows.map(([, value]) => Math.abs(Number(value)))
        .filter(Number.isFinite), 1);
      return `<div class="prior-waterfall">
        <div class="prior-waterfall-title">
          <strong>${esc(title)}</strong>
          <span>额外费封顶：${breakdown.extra_cap_hit === true ? "是" : breakdown.extra_cap_hit === false ? "否" : "未提供"}</span>
        </div>
        <div class="waterfall">
          ${rows.map(([label, value, kind]) =>
            waterfallRow(label, value, maxValue, currency, kind)).join("")}
        </div>
      </div>`;
    }).filter(Boolean);

    if (!parts.length) {
      return `<section class="missing-evidence">
        <strong>先验费用拆分未由 API 返回</strong>
        <p>${platform.platform === "shopee"
          ? "Shopee 当前只返回推断的结算额、广告额与利润，没有逐项佣金、交易费、联盟费和税费。页面不会自行伪造拆分。"
          : "当前平台没有返回可审计的 prior.breakdown，无法展示逐项费用。"}
        </p>
      </section>`;
    }
    return `<section class="prior-section">
      <div class="audit-heading">
        <div><span>PRIOR · FULL BREAKDOWN</span><h5>先验费用全拆分</h5></div>
      </div>
      <div class="prior-grid">${parts.join("")}</div>
    </section>`;
  }

  function totalSampleCount(platform) {
    const posterior = platform.posterior || {};
    return Number(platform.platform === "tiktok"
      ? posterior.comps_in_window
      : posterior.comps_same_sku) || 0;
  }

  function sampleAuditMarkup(platform) {
    const posterior = platform.posterior || {};
    const samples = Array.isArray(posterior.recent_comps) ? posterior.recent_comps : [];
    const key = String(platform.platform || "unknown");
    const total = totalSampleCount(platform);
    sampleViews.set(key, { samples, total, filtered: samples, page: 1, pageSize: 10 });
    const omitted = Math.max(0, total - samples.length);
    return `<section class="sample-audit" data-sample-audit="${esc(key)}">
      <div class="audit-heading">
        <div><span>POSTERIOR EVIDENCE</span><h5>后验样本审计</h5></div>
        <span class="badge ${omitted ? "warn" : "safe"}">API ${samples.length} / 窗口 ${total}</span>
      </div>
      <p class="audit-note">${omitted
        ? `窗口共有 ${numberText(total)} 条证据，但接口只返回最近 ${numberText(samples.length)} 条；其余 ${numberText(omitted)} 条当前无法在页面逐行复核。下表完整展示接口实际返回的每一条。`
        : `接口返回的 ${numberText(samples.length)} 条证据会全部保留，可筛选并分页逐行复核。`}
      </p>
      <div class="distribution-panel">
        <div>
          <strong>返回样本利润分布</strong>
          <span>横轴为单件利润 CNY；红色为负利润，绿色为非负利润。</span>
        </div>
        <canvas class="distribution-canvas" width="760" height="210" data-sample-canvas="${esc(key)}"
          aria-label="${esc(key)} 返回样本利润分布"></canvas>
      </div>
      <div class="sample-toolbar">
        <label>筛选
          <select data-sample-filter="${esc(key)}">
            <option value="all">全部返回样本</option>
            <option value="affiliate">有联盟佣金</option>
            <option value="no_affiliate">无联盟佣金</option>
            <option value="profit">利润 ≥ 0</option>
            <option value="loss">利润 &lt; 0</option>
            <option value="outlier">异常值</option>
          </select>
        </label>
        <label>订单 / 来源
          <input type="search" data-sample-search="${esc(key)}" placeholder="筛选订单号或来源">
        </label>
        <span data-sample-summary="${esc(key)}">返回 ${samples.length} 条</span>
      </div>
      <div class="sample-table-wrap">
        <table class="sample-table">
          <thead><tr>
            <th>日期</th><th>订单</th><th>实付</th><th>净结算</th><th>结算比</th>
            <th>联盟</th><th>利润 CNY</th><th>利润率</th><th>来源</th><th>状态</th>
          </tr></thead>
          <tbody data-sample-rows="${esc(key)}"></tbody>
        </table>
      </div>
      <div class="pagination" data-sample-pagination="${esc(key)}"></div>
    </section>`;
  }

  function filteredSamples(key) {
    const view = sampleViews.get(key);
    if (!view) return [];
    const root = document.querySelector(`[data-sample-audit="${CSS.escape(key)}"]`);
    if (!root) return view.samples;
    const filter = root.querySelector("[data-sample-filter]")?.value || "all";
    const search = (root.querySelector("[data-sample-search]")?.value || "").trim().toLowerCase();
    return view.samples.filter((sample) => {
      const affiliate = sample.has_affiliate === true || Number(sample.affiliate_local) > 0;
      const profit = Number(sample.profit_cny);
      const matchesFilter = filter === "all" ||
        (filter === "affiliate" && affiliate) ||
        (filter === "no_affiliate" && !affiliate) ||
        (filter === "profit" && profit >= 0) ||
        (filter === "loss" && profit < 0) ||
        (filter === "outlier" && sample.outlier === true);
      const haystack = `${sample.order_id || ""} ${sample.source || ""}`.toLowerCase();
      return matchesFilter && (!search || haystack.includes(search));
    });
  }

  function renderSampleRows(key) {
    const view = sampleViews.get(key);
    const root = document.querySelector(`[data-sample-audit="${CSS.escape(key)}"]`);
    if (!view || !root) return;
    view.filtered = filteredSamples(key);
    const pageCount = Math.max(1, Math.ceil(view.filtered.length / view.pageSize));
    view.page = Math.min(Math.max(1, view.page), pageCount);
    const start = (view.page - 1) * view.pageSize;
    const pageRows = view.filtered.slice(start, start + view.pageSize);
    const tbody = root.querySelector("[data-sample-rows]");
    tbody.innerHTML = pageRows.length ? pageRows.map((sample) => {
      const affiliate = sample.has_affiliate === true || Number(sample.affiliate_local) > 0;
      const state = sample.outlier === true ? "异常值" : "已采用";
      return `<tr>
        <td>${esc(dateText(sample.statement_date))}</td>
        <td title="${esc(sample.order_id || "")}">${esc(sample.order_id || "—")}</td>
        <td>${money(sample.sale_local)}</td>
        <td>${money(sample.settlement_local)}</td>
        <td>${money(Number(sample.settle_ratio) * 100)}%</td>
        <td>${affiliate ? "有" : "无"}${sample.affiliate_approx ? "（估）" : ""}</td>
        <td class="${Number(sample.profit_cny) < 0 ? "negative-number" : ""}">${money(sample.profit_cny)}</td>
        <td>${money(sample.margin_pct)}%</td>
        <td title="${esc(sample.source || "")}">${esc(sample.source || "—")}</td>
        <td><span class="table-state ${sample.outlier ? "warn" : ""}">${state}</span></td>
      </tr>`;
    }).join("") : `<tr><td colspan="10" class="empty-table">没有符合筛选条件的返回样本</td></tr>`;
    root.querySelector("[data-sample-summary]").textContent =
      `筛选 ${view.filtered.length} / API 返回 ${view.samples.length} · 窗口 ${view.total}`;
    const pagination = root.querySelector("[data-sample-pagination]");
    pagination.innerHTML = `<button type="button" data-page-action="prev" ${view.page <= 1 ? "disabled" : ""}>上一页</button>
      <span>第 ${view.page} / ${pageCount} 页</span>
      <button type="button" data-page-action="next" ${view.page >= pageCount ? "disabled" : ""}>下一页</button>`;
    pagination.querySelector('[data-page-action="prev"]').addEventListener("click", () => {
      view.page -= 1;
      renderSampleRows(key);
    });
    pagination.querySelector('[data-page-action="next"]').addEventListener("click", () => {
      view.page += 1;
      renderSampleRows(key);
    });
    drawDistribution(key);
  }

  function drawDistribution(key) {
    const view = sampleViews.get(key);
    const canvas = document.querySelector(`[data-sample-canvas="${CSS.escape(key)}"]`);
    if (!view || !canvas) return;
    const values = (view.filtered || view.samples)
      .map((sample) => Number(sample.profit_cny))
      .filter(Number.isFinite);
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(320, Math.floor(rect.width || 760));
    const height = 210;
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);
    const context = canvas.getContext("2d");
    context.scale(ratio, ratio);
    context.clearRect(0, 0, width, height);
    context.font = '12px "Segoe UI", sans-serif';
    context.fillStyle = "#496068";
    if (!values.length) {
      context.fillText("当前筛选没有可绘制的利润样本", 18, 30);
      return;
    }
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (min === max) {
      min -= 1;
      max += 1;
    }
    const binCount = Math.min(12, Math.max(5, Math.ceil(Math.sqrt(values.length))));
    const bins = Array.from({ length: binCount }, () => 0);
    values.forEach((value) => {
      const index = Math.min(binCount - 1, Math.floor((value - min) / (max - min) * binCount));
      bins[index] += 1;
    });
    const pad = { left: 44, right: 18, top: 18, bottom: 34 };
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;
    const maxCount = Math.max(...bins, 1);
    const barWidth = plotWidth / binCount;
    bins.forEach((count, index) => {
      const lower = min + (max - min) * index / binCount;
      const upper = min + (max - min) * (index + 1) / binCount;
      const x = pad.left + index * barWidth + 2;
      const barHeight = count / maxCount * plotHeight;
      context.fillStyle = upper <= 0 ? "#a93f39" : lower >= 0 ? "#247154" : "#ce6c3d";
      context.fillRect(x, pad.top + plotHeight - barHeight, Math.max(2, barWidth - 4), barHeight);
    });
    context.strokeStyle = "#b9c0bc";
    context.beginPath();
    context.moveTo(pad.left, pad.top + plotHeight + 0.5);
    context.lineTo(width - pad.right, pad.top + plotHeight + 0.5);
    context.stroke();
    context.fillStyle = "#496068";
    context.textAlign = "left";
    context.fillText(`¥ ${money(min)}`, pad.left, height - 10);
    context.textAlign = "right";
    context.fillText(`¥ ${money(max)}`, width - pad.right, height - 10);
    context.textAlign = "left";
    context.fillText(`最高频 ${maxCount} 条`, pad.left, 12);
  }

  function bindSampleAudits() {
    sampleViews.forEach((view, key) => {
      const root = document.querySelector(`[data-sample-audit="${CSS.escape(key)}"]`);
      if (!root) return;
      root.querySelector("[data-sample-filter]").addEventListener("change", () => {
        view.page = 1;
        renderSampleRows(key);
      });
      root.querySelector("[data-sample-search]").addEventListener("input", () => {
        view.page = 1;
        renderSampleRows(key);
      });
      renderSampleRows(key);
    });
  }

  function missingEvidenceMarkup(platform, product) {
    const posterior = platform.posterior || {};
    const returned = Array.isArray(posterior.recent_comps) ? posterior.recent_comps.length : 0;
    const total = totalSampleCount(platform);
    const missing = [];
    if (!/^https:\/\//i.test(String(product.image_url || ""))) {
      missing.push("商品主图 URL 缺失或不是 HTTPS。");
    }
    if (total > returned) {
      missing.push(`后验窗口有 ${total} 条样本，但 API 只返回 ${returned} 条逐行证据。`);
    }
    if (!platform.fx?.as_of) {
      missing.push("汇率没有 as_of 时间，无法证明估算使用的是哪个时点的汇率。");
    }
    if (platform.platform === "tiktok" && product.cost_source === "sku_costs") {
      missing.push("TikTok 成本响应没有 resolved_by，无法区分精确平台 SKU 命中与尾四位回退。");
    }
    if (platform.platform === "shopee" && !(platform.prior?.with_affiliate?.breakdown)) {
      missing.push("Shopee API 未返回佣金、交易费、联盟费与税费的逐项先验拆分。");
    }
    if (platform.platform === "shopee" && product.cost_source === "sku_costs_via_tk_seller_sku_tail4") {
      missing.push("Shopee 没有直接成本事实，当前成本来自 TikTok 尾四位关联。");
    }
    if (!missing.length) {
      return `<section class="missing-evidence complete"><strong>本页要求的核心证据均已返回</strong></section>`;
    }
    return `<section class="missing-evidence">
      <strong>当前缺失 / 未暴露证据</strong>
      <ul>${missing.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>
    </section>`;
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
    const saleBasis = priceBasisLabel(product.sale_basis || product.price_source);
    const lineage = costLineage(platform, product);
    const assumption = platform.ad_note ||
      "结果使用当前广告费率假设；实际广告支出尚未按 SKU 分摊。";

    return `<article class="platform-card">
      <div class="platform-title">
        <div class="product-identity">
          ${imageMarkup(platform, product, label)}
          <div>
            <h4>${esc(label)}</h4>
            <p>${esc((product.product_name || product.model_name || requestedSku).slice(0, 120))}</p>
            <span>Seller SKU ${esc(product.seller_sku || requestedSku)} · 平台 SKU ${esc(platformSku)}</span>
          </div>
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
      ${priceEvidenceMarkup(product, platform.currency || "")}
      <section class="lineage-card ${esc(lineage.tone)}">
        <div class="audit-heading">
          <div><span>COST LINEAGE</span><h5>${esc(lineage.label)}</h5></div>
          <span class="badge ${lineage.tone === "risk" ? "danger" : "warn"}">${esc(costSource)}</span>
        </div>
        <p><strong>匹配路径：</strong>${esc(lineage.path)}</p>
        <p><strong>审计风险：</strong>${esc(lineage.risk)}</p>
      </section>
      <div class="assumption-box">广告假设 ${esc(adPercent)}。${esc(assumption)}</div>
      ${warnings.length
        ? `<ul class="warning-list">${warnings.map((warning) => `<li>${esc(warning)}</li>`).join("")}</ul>`
        : ""}
      <div class="waterfall-stack">
        ${mainWaterfallMarkup(platform, product, main)}
        ${priorWaterfallMarkup(platform)}
      </div>
      ${sampleAuditMarkup(platform)}
      ${missingEvidenceMarkup(platform, product)}
    </article>`;
  }

  function renderSku(payload) {
    const platforms = Object.values(payload.platforms || {});
    sampleViews.clear();
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
    document.querySelectorAll("[data-product-image]").forEach((image) => {
      image.addEventListener("error", () => {
        image.hidden = true;
        const fallback = image.parentElement?.querySelector(".image-fallback");
        if (fallback) fallback.hidden = false;
      }, { once: true });
    });
    requestAnimationFrame(bindSampleAudits);
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
