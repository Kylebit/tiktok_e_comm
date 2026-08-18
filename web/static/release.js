(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const money = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed)
      ? new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(parsed)
      : "—";
  };
  const shortDate = (value) => String(value || "").slice(0, 10) || "—";
  const setLoading = (form, loading) => {
    form.classList.toggle("is-loading", loading);
    form.setAttribute("aria-busy", loading ? "true" : "false");
    const button = form.querySelector('button[type="submit"]');
    if (button) button.disabled = loading;
  };
  const showAlert = (element, message, success = false) => {
    element.textContent = message;
    element.classList.toggle("success", success);
    element.hidden = !message;
  };

  async function fetchJson(url) {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    const payload = await response.json().catch(() => ({ ok: false, error: `HTTP ${response.status}` }));
    if (!response.ok || payload.ok === false) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }

  function badge(element, label, tone) {
    element.textContent = label;
    element.className = `badge ${tone}`;
  }

  function renderStages(stages) {
    const labels = { ready: "通过", blocked: "阻断", pending: "等待" };
    $("#stageRail").innerHTML = stages.map((stage, index) => `
      <div class="stage ${esc(stage.status)}">
        <span>${stage.status === "ready" ? "✓" : index + 1}</span>
        <div><small>${esc(labels[stage.status] || stage.status)}</small><strong>${esc(stage.label)}</strong></div>
      </div>`).join("");
  }

  function renderProduct(data) {
    const product = data.product;
    const facts = [
      ["Offer ID", product.offer_id],
      ["1688 source", product.source_offer_id || "未记录"],
      ["标题", product.title, true],
      ["成本", `¥ ${money(product.cost_cny)} CNY`],
      ["重量", `${product.weight_kg ?? "—"} kg`],
      ["包装", (product.package_cm || []).join(" × ") || "—"],
      ["站点", (product.selected_sites || []).join(" · ") || "—"],
      ["状态版本", `revision ${product.revision}`],
    ];
    $("#productFacts").classList.remove("skeleton-lines");
    $("#productFacts").innerHTML = facts.map(([label, value, wide]) => `
      <div class="fact${wide ? " wide" : ""}"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
    $("#candidateSku").textContent = product.seller_sku_candidate;
    const ready = data.approval_rehearsal.ready;
    $("#skuGateText").textContent = ready
      ? "目录唯一性与审批合同模拟均通过"
      : (data.approval_rehearsal.blockers[0] || "审批模拟未通过");
    badge(
      $("#productApprovalBadge"),
      product.actual_product_approved ? "已正式审批" : (ready ? "仅模拟通过" : "模拟阻断"),
      product.actual_product_approved ? "safe" : (ready ? "warn" : "danger"),
    );
  }

  function renderGate(data) {
    const gate = data.actual_release_gate;
    badge($("#realGateBadge"), gate.ready ? "READY" : "NEEDS ACTION", gate.ready ? "safe" : "warn");
    const rows = gate.blockers.length ? gate.blockers : ["正式发布前置条件已经满足"];
    $("#realGateList").innerHTML = rows.map((item) =>
      `<li class="${gate.ready ? "ok" : ""}">${esc(item)}</li>`).join("");
  }

  function renderImages(content) {
    badge(
      $("#imageCountBadge"),
      `${content.image_count} IMAGES · ${content.approved ? "APPROVED" : "REVIEW"}`,
      content.approved ? "safe" : "warn",
    );
    const grid = $("#imageGrid");
    grid.classList.remove("skeleton-cards");
    grid.innerHTML = content.images.map((image) => {
      const proxyUrl = `/api/proxy-image?url=${encodeURIComponent(image.image_url)}`;
      const type = image.asset_type === "source" ? "来源图" : "AI 生成图";
      return `<figure class="image-card">
        <div class="image-wrap">
          <img src="${esc(proxyUrl)}" alt="最终图片 ${image.position} · ${esc(type)}" loading="lazy">
          <span>${image.position}</span>
        </div>
        <figcaption><strong>${esc(type)} · ${esc(image.shot_id || image.artifact_id)}</strong>
        <small title="${esc(image.decision_source)}">${esc(image.decision_source)}</small></figcaption>
      </figure>`;
    }).join("");
    const notes = [];
    if (content.stale_external_write) notes.push("旧 11 图写入已过期");
    if (content.superseded_artifact_ids.length) notes.push(`${content.superseded_artifact_ids.length} 个历史版本已排除`);
    content.blockers.forEach((item) => notes.push(item));
    $("#contentNotes").innerHTML = [...new Set(notes)].map((item) => `<span>${esc(item)}</span>`).join("");
  }

  function renderChannels(publication) {
    const grid = $("#channelGrid");
    if (!publication.drafts.length) {
      grid.innerHTML = '<div class="empty-state"><span>渠道计划尚未生成</span></div>';
      return;
    }
    grid.innerHTML = publication.drafts.map((draft) => {
      const ready = !draft.missing_conditions.length;
      return `<div class="channel-card">
        <header><h4>${esc(draft.channel)}</h4><span class="badge ${ready ? "safe" : "warn"}">${ready ? "DRAFT READY" : "BLOCKED"}</span></header>
        <p>${ready ? "合同完整，仅生成内存草稿；仍需真实渠道审批。" : esc(draft.missing_conditions.join("；"))}</p>
      </div>`;
    }).join("");
  }

  function renderReleaseFailure(message) {
    renderStages([
      { label: "Source facts", status: "blocked" },
      { label: "Content & images", status: "blocked" },
      { label: "SKU approval rehearsal", status: "blocked" },
      { label: "Channel draft rehearsal", status: "blocked" },
    ]);
    badge($("#productApprovalBadge"), "PRECHECK FAILED", "danger");
    badge($("#realGateBadge"), "UNKNOWN", "danger");
    badge($("#imageCountBadge"), "NOT VERIFIED", "danger");
    $("#productFacts").classList.remove("skeleton-lines");
    $("#productFacts").innerHTML = `<div class="empty-state fact wide"><span>${esc(message)}</span></div>`;
    $("#candidateSku").textContent = "—";
    $("#skuGateText").textContent = "本次输入未通过预检";
    $("#realGateList").innerHTML = `<li>${esc(message)}</li>`;
    $("#imageGrid").classList.remove("skeleton-cards");
    $("#imageGrid").innerHTML = '<div class="empty-state"><span>没有经过验证的图片结果</span></div>';
    $("#contentNotes").innerHTML = "";
    $("#channelGrid").innerHTML = '<div class="empty-state"><span>没有生成渠道计划</span></div>';
    $("#safetyMessage").textContent = "预检失败；未沿用上一笔商品的结果，也没有执行写入。";
  }

  function renderWeeklyFailure(message) {
    badge($("#weeklyBadge"), "PREVIEW FAILED", "danger");
    $("#weeklyMetrics").classList.remove("skeleton-lines");
    $("#weeklyMetrics").innerHTML = '<div class="empty-state"><span>本次复算未完成</span><small>没有沿用上一次利润结果</small></div>';
    $("#weeklyQuality").innerHTML = `<strong>无法形成利润结论</strong>${esc(message)}`;
  }

  function renderWeekly(summary) {
    if (!summary || !summary.available) {
      badge($("#weeklyBadge"), "NO REPORT", "neutral");
      $("#weeklyMetrics").classList.remove("skeleton-lines");
      $("#weeklyMetrics").innerHTML = '<div class="empty-state"><span>尚无本地周报</span></div>';
      $("#weeklyQuality").innerHTML = "运行只读复算后可在这里预览，但不会自动保存。";
      return;
    }
    badge($("#weeklyBadge"), summary.status, summary.status === "ready" ? "safe" : "warn");
    const period = `${shortDate(summary.period.start)} — ${shortDate(summary.period.end)}`;
    const metrics = [
      summary.decision_usable
        ? ["可决策利润", `¥ ${money(summary.totals.profit_cny)}`, "当前数据质量门已通过"]
        : ["利润结论", "暂不可用", "成本、数量或广告支出口径仍需补齐"],
      ["平台净结算", `¥ ${money(summary.totals.settlement_cny)}`, "CNY 折算"],
      ["已识别成本", `¥ ${money(summary.totals.cost_cny)}`, `${summary.realized_bucket_count} 个 SKU 桶；不完整时不可推导利润`],
      ["负利润 SKU", summary.negative_profit_skus.length, period],
    ];
    $("#weeklyMetrics").classList.remove("skeleton-lines");
    $("#weeklyMetrics").innerHTML = metrics.map(([label, value, note]) => `
      <div class="metric"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`).join("");
    const tags = Object.entries(summary.quality_affected_row_counts || {})
      .map(([code, count]) => `<span>${esc(code)} · ${esc(count)} affected</span>`).join("");
    $("#weeklyQuality").innerHTML = `<strong>${esc(summary.quality_issue_group_count)} 组数据质量提醒</strong>
      源文件 ${esc(summary.source_file_count)} 个；规范化 ${esc(summary.source_row_counts.normalized ?? "—")} 行。
      ${summary.decision_usable ? "当前结果可进入决策复核。" : "当前结果不可形成利润结论，只展示结算与证据。"}
      <div class="quality-tags">${tags || "<span>no issues</span>"}</div>`;
  }

  function renderSku(payload) {
    const compare = payload.compare || [];
    $("#skuResults").className = "probe-results result-summary";
    $("#skuResults").innerHTML = `
      <div class="result-head">
        <div><strong>${esc(payload.sku)}</strong><small>广告费率 ${money(Number(payload.ad_rate) * 100)}% · 回看 ${esc(payload.lookback_days ?? "平台默认")} 天</small></div>
        <span class="badge ${payload.partial ? "warn" : "safe"}">${payload.partial ? "PARTIAL" : "CALCULATED"}</span>
      </div>
      <div class="platform-results">${compare.map((row) => {
        if (!row.ok) return `<div class="platform-result"><header><h4>${esc(row.platform)}</h4><span class="badge danger">ERROR</span></header><p>${esc(row.error)}</p></div>`;
        const negative = Number(row.profit_cny) < 0;
        return `<div class="platform-result">
          <header><h4>${esc(row.platform)}</h4><span class="badge ${negative ? "danger" : "safe"}">${esc(row.label || "ESTIMATE")}</span></header>
          <div class="profit-number ${negative ? "negative" : ""}">¥ ${money(row.profit_cny)}</div>
          <small>估算利润 / SKU</small>
          <dl><dt>利润率</dt><dd>${money(row.margin_pct)}%</dd><dt>售价</dt><dd>${money(row.sale_local)}</dd><dt>成本</dt><dd>¥ ${money(row.cost_cny)}</dd><dt>置信度</dt><dd>${esc(row.confidence || "—")}</dd></dl>
        </div>`;
      }).join("")}</div>`;
  }

  async function loadDashboard() {
    const form = $("#releaseForm");
    setLoading(form, true);
    showAlert($("#releaseAlert"), "");
    const params = new URLSearchParams({
      offer_id: $("#offerId").value.trim(),
      seller_sku: $("#sellerSku").value.trim(),
    });
    try {
      const data = await fetchJson(`/api/release/dashboard?${params}`);
      $("#safetyMessage").textContent = data.safety.message;
      renderStages(data.stages);
      renderProduct(data);
      renderGate(data);
      renderImages(data.content);
      renderChannels(data.publication_rehearsal);
      renderWeekly(data.weekly_profit);
      showAlert($("#releaseAlert"), "完整预检已完成：所有结果均来自只读证据或内存模拟。", true);
    } catch (error) {
      const message = error.message || "预检失败";
      renderReleaseFailure(message);
      showAlert($("#releaseAlert"), message);
    } finally {
      setLoading(form, false);
    }
  }

  function setPreviousWeek() {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const day = (today.getDay() + 6) % 7;
    const thisMonday = new Date(today);
    thisMonday.setDate(today.getDate() - day);
    const start = new Date(thisMonday);
    start.setDate(start.getDate() - 7);
    const end = new Date(thisMonday);
    end.setDate(end.getDate() - 1);
    const localIso = (date) => {
      const y = date.getFullYear();
      const m = String(date.getMonth() + 1).padStart(2, "0");
      const d = String(date.getDate()).padStart(2, "0");
      return `${y}-${m}-${d}`;
    };
    $("#weekStart").value = localIso(start);
    $("#weekEnd").value = localIso(end);
  }

  $("#releaseForm").addEventListener("submit", (event) => {
    event.preventDefault();
    loadDashboard();
  });

  $("#weeklyForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    setLoading(form, true);
    showAlert($("#weeklyAlert"), "");
    const params = new URLSearchParams({ start: $("#weekStart").value, end: $("#weekEnd").value });
    try {
      const data = await fetchJson(`/api/release/weekly-preview?${params}`);
      renderWeekly(data.summary);
      showAlert($("#weeklyAlert"), "只读复算完成；本次结果未保存、未推送。", true);
    } catch (error) {
      const message = error.message || "周报复算失败";
      renderWeeklyFailure(message);
      showAlert($("#weeklyAlert"), message);
    } finally {
      setLoading(form, false);
    }
  });

  $("#skuForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    setLoading(form, true);
    showAlert($("#skuAlert"), "");
    const params = new URLSearchParams({
      sku: $("#profitSku").value.trim(),
      platform: $("#profitPlatform").value,
      ad_rate_percent: $("#adRate").value,
      lookback_days: $("#lookbackDays").value,
    });
    try {
      const data = await fetchJson(`/api/sku-profit?${params}`);
      renderSku(data);
    } catch (error) {
      $("#skuResults").className = "probe-results empty-state";
      $("#skuResults").innerHTML = "<span>没有可展示的利润结果</span><small>请检查 SKU 与参数后重试</small>";
      showAlert($("#skuAlert"), error.message || "SKU 利润计算失败");
    } finally {
      setLoading(form, false);
    }
  });

  setPreviousWeek();
  loadDashboard();
})();
