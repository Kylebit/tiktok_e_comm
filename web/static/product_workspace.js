(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const channelNames = {
    tiktok: "TikTok Shop",
    shopee: "Shopee",
    ozon: "Ozon",
    miaoshou: "妙手",
  };
  const siteNames = {
    lh_ph: "菲律宾",
    lh_my: "马来西亚",
    lh_th: "泰国",
    lh_vn: "越南",
    lh_mx: "墨西哥",
    uk_import: "英国",
  };
  const feeNames = {
    goods_cost_local: "货值",
    logistics_local: "物流",
    hidden_shipping_local: "隐藏物流",
    shipping_local: "物流",
    commission_local: "佣金",
    transaction_local: "交易费",
    extra_fee_local: "平台附加费",
    import_tax_local: "进口税",
    vat_local: "VAT",
    sfp_local: "SFP",
    smart_promo_local: "Smart Promo",
    affiliate_local: "达人费",
    ad_local: "广告费",
    creator_local: "创作者费",
    seller_tax_local: "卖家税",
    fixed_fee_local: "固定费",
  };
  const blockerTranslations = new Map([
    ["Product approval has not been persisted.", "商品审批尚未正式保存。"],
    ["Workbench commercial fields are not locked.", "标题、SKU、成本、重量和站点等商品字段尚未锁定。"],
    ["The previous 11-image Miaoshou write is stale.", "妙手仍是旧的 11 图版本，需要用当前 5 图重新同步并回读验证。"],
    ["The current final image set has not been verified as written to Miaoshou.", "当前最终图片尚未完成妙手写入与回读验证。"],
    [
      "Persisted product approval does not match the current product, SKU, content package, and input fingerprint.",
      "已保存的审批与当前商品、Seller SKU 或内容版本不一致，需要重新审批。",
    ],
    [
      "Locked workbench Seller SKU does not match the approved candidate SKU.",
      "工作台锁定的 Seller SKU 与本次候选 SKU 不一致。",
    ],
    [
      "external Miaoshou image write is stale for the current artifact set",
      "妙手图片记录与当前最终五图不一致。",
    ],
  ]);

  const QUEUE_STORAGE_KEY = "orbit.productWorkspace.releaseQueue.v1";
  const MAX_QUEUE_ITEMS = 50;
  const QUEUE_REFRESH_CONCURRENCY = 4;
  let currentData = null;
  let approvalSubmitting = false;
  let pageLoading = false;
  let queueRefreshing = false;
  let queueItems = [];
  let currentQueueKey = "";
  let loadedQueueKey = "";

  function productKey(offerId, sellerSku) {
    return `${String(offerId || "").trim()}::${String(sellerSku || "").trim()}`;
  }

  function validIdentity(offerId, sellerSku) {
    return /^\d{1,32}$/.test(String(offerId || "").trim())
      && /^\d{1,32}$/.test(String(sellerSku || "").trim());
  }

  function readQueue() {
    try {
      const parsed = JSON.parse(localStorage.getItem(QUEUE_STORAGE_KEY) || "[]");
      if (!Array.isArray(parsed)) return [];
      const seen = new Set();
      return parsed
        .filter((item) => validIdentity(item?.offer_id, item?.seller_sku))
        .map((item) => ({
          offer_id: String(item.offer_id).trim(),
          seller_sku: String(item.seller_sku).trim(),
          data: null,
          error: "",
          loading: false,
        }))
        .filter((item) => {
          const key = productKey(item.offer_id, item.seller_sku);
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        })
        .slice(0, MAX_QUEUE_ITEMS);
    } catch (_error) {
      return [];
    }
  }

  function saveQueue() {
    try {
      localStorage.setItem(
        QUEUE_STORAGE_KEY,
        JSON.stringify(queueItems.map(({ offer_id, seller_sku }) => ({
          offer_id,
          seller_sku,
        }))),
      );
    } catch (_error) {
      $("#queueMessage").textContent = "浏览器无法保存队列；本次页面内仍可继续使用。";
    }
  }

  function queueItem(key) {
    return queueItems.find((item) => (
      productKey(item.offer_id, item.seller_sku) === key
    ));
  }

  function money(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed)
      ? new Intl.NumberFormat("zh-CN", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        }).format(parsed)
      : "—";
  }

  function localMoney(value, currency = "") {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return "—";
    const digits = currency === "VND" ? 0 : 2;
    return `${new Intl.NumberFormat("zh-CN", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(parsed)} ${currency}`.trim();
  }

  function translateBlocker(value) {
    const text = String(value || "").trim();
    const unaudited = text.match(/^([a-z]+):([A-Z0-9_]+) has no audited repository adapter path$/);
    if (unaudited) {
      return `${channelNames[unaudited[1]] || unaudited[1]} ${unaudited[2]} 的真实发布适配器尚未完成仓库审计。`;
    }
    const dependency = text.match(/^([a-z]+):([A-Z0-9_]+) requires (.+)$/);
    if (dependency) {
      return `${channelNames[dependency[1]] || dependency[1]} ${dependency[2]} 等待前置结果：${dependency[3]}。`;
    }
    return blockerTranslations.get(text) || text;
  }

  function friendlyError(value) {
    const text = String(value || "").trim();
    if (text.includes("required release evidence not found")) {
      return "这件商品还没有本地发布档案。请先进入 AI 图片工作室，从妙手采集箱重新读取并完成图片审核。";
    }
    return text || "商品状态读取失败，请确认本地服务已启动。";
  }

  function setLoading(loading) {
    pageLoading = loading;
    $("#lookupForm").classList.toggle("is-loading", loading);
    $("#refreshButton").disabled = loading;
    if ($("#approvalButton")) updateApprovalButton(currentData || {});
  }

  function showError(message) {
    const alert = $("#pageAlert");
    alert.textContent = message || "";
    alert.hidden = !message;
  }

  function renderFailure(message) {
    currentData = null;
    $("#productTitle").textContent = "未能读取该商品";
    $("#productIdentity").innerHTML = "";
    $("#readinessLabel").textContent = "当前状态";
    $("#readinessValue").textContent = "未加载";
    $("#readinessNote").textContent = "请检查 Offer ID、Seller SKU 或本地数据";
    $("#stageRail").innerHTML = [
      "商品信息",
      "内容与图片",
      "审批与锁定",
      "妙手同步",
      "渠道草稿",
    ].map((label, index) => `
      <div class="stage waiting">
        <span>${String(index + 1).padStart(2, "0")}</span>
        <strong>${esc(label)}</strong>
        <small>等待商品数据</small>
      </div>
    `).join("");
    setBadge($("#factsBadge"), "未加载", "neutral");
    $("#productFacts").classList.remove("skeleton-lines");
    $("#productFacts").innerHTML =
      `<div class="fact wide"><span>读取结果</span><strong>${esc(message)}</strong></div>`;
    $("#nextStepNumber").textContent = "—";
    $("#nextStepTitle").textContent = "重新检查商品身份";
    $("#nextStepDescription").textContent = "确认输入后再次读取；页面不会沿用上一次商品结果。";
    $("#blockerList").innerHTML = `<li>${esc(message)}</li>`;
    setBadge($("#contentBadge"), "未加载", "neutral");
    setBadge($("#syncBadge"), "状态未知", "neutral");
    $("#imageGrid").classList.remove("skeleton-cards");
    $("#imageGrid").innerHTML =
      '<div class="image-fallback">当前没有可展示的商品图片。</div>';
    $("#contentNotice").innerHTML = "";
    $("#channelGrid").innerHTML =
      '<div class="channel-card"><p>读取商品后展示渠道准备状态。</p></div>';
    $("#pricingSummary").textContent = "尚未读取售价计算。";
    $("#storePriceGrid").innerHTML =
      '<div class="image-fallback">读取商品后展示全部国家与店铺售价。</div>';
    $("#pricingAuditTables").innerHTML = "";
    $("#channelPlanSummary").textContent = "尚未形成全渠道发布计划。";
    $("#channelBlockers").innerHTML = "";
    $("#publishAllButton").disabled = true;
    $("#publishAllNote").textContent = "请先成功读取商品与内容审批事实。";
    $("#workbenchLink").removeAttribute("href");
    $("#workbenchLink").setAttribute("aria-disabled", "true");
    $("#approvalSku").textContent = "—";
    $("#approvalRevision").textContent = "—";
    $("#approvalContent").textContent = "未加载";
    $("#approvalStatus").textContent = "未加载";
    $("#approvalFacts").innerHTML = "";
    $("#approvalCheckbox").checked = false;
    $("#approvalCheckbox").disabled = true;
    $("#approvalButton").disabled = true;
    $("#approvalMessage").textContent = "";
  }

  function setBadge(element, text, tone) {
    element.textContent = text;
    element.className = `badge ${tone}`;
  }

  async function fetchDashboard(offerId, sellerSku) {
    const params = new URLSearchParams({
      offer_id: offerId,
      seller_sku: sellerSku,
    });
    const response = await fetch(`/api/product-workspace/dashboard?${params}`, {
      headers: { Accept: "application/json" },
    });
    const payload = await response.json().catch(() => ({
      ok: false,
      error: `服务返回 HTTP ${response.status}`,
    }));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `服务返回 HTTP ${response.status}`);
    }
    return payload;
  }

  function productFactsReady(product) {
    const dimensions = Array.isArray(product.package_cm) ? product.package_cm : [];
    return Boolean(
      product.offer_id
      && product.title
      && Number(product.cost_cny) > 0
      && Number(product.weight_kg) > 0
      && dimensions.length === 3
      && dimensions.every((value) => Number(value) > 0)
      && (product.selected_sites || []).length
      && (product.selected_sku_keys || []).length
    );
  }

  function stageModel(data) {
    const productReady = productFactsReady(data.product || {});
    const contentReady = Boolean(
      data.content?.approved
      && Number(data.content?.image_count) > 0
      && !(data.content?.blockers || []).some((row) => !String(row).startsWith("external "))
    );
    const approvalReady = Boolean(data.product?.actual_product_approved);
    const imageSyncReady = Boolean(data.content?.current_image_write_verified);
    const channelPreviewReady = Boolean(data.publication_rehearsal?.ready);
    const releaseReady = Boolean(data.actual_release_gate?.ready);

    const raw = [
      { key: "product", label: "商品信息", ready: productReady, readyText: "事实完整", waitText: "待补全" },
      { key: "content", label: "内容与图片", ready: contentReady, readyText: "已批准", waitText: "待审核" },
      { key: "approval", label: "审批与锁定", ready: approvalReady, readyText: "已批准", waitText: "待批准" },
      { key: "sync", label: "妙手同步", ready: imageSyncReady, readyText: "已验证", waitText: "待同步" },
      {
        key: "channels",
        label: "渠道草稿",
        ready: releaseReady,
        readyText: "可进入渠道",
        waitText: channelPreviewReady ? "草稿已预览" : "等待前置条件",
      },
    ];
    const firstIncomplete = raw.findIndex((stage) => !stage.ready);
    return raw.map((stage, index) => ({
      ...stage,
      status: stage.ready ? "ready" : (index === firstIncomplete ? "current" : "waiting"),
    }));
  }

  function renderStages(stages) {
    $("#stageRail").innerHTML = stages.map((stage, index) => `
      <div class="stage ${stage.status}">
        <span>${stage.ready ? "✓" : String(index + 1).padStart(2, "0")}</span>
        <strong>${esc(stage.label)}</strong>
        <small>${esc(stage.ready ? stage.readyText : stage.waitText)}</small>
      </div>
    `).join("");
  }

  function queueSummary(item) {
    if (item.loading) {
      return {
        stage: "正在读取最新本地证据",
        blockers: "—",
        images: "—",
        approval: "检查中",
        tone: "neutral",
      };
    }
    if (item.error || !item.data) {
      return {
        stage: item.error || "等待首次刷新",
        blockers: "—",
        images: "—",
        approval: "未读取",
        tone: item.error ? "danger" : "neutral",
      };
    }
    const stages = stageModel(item.data);
    const active = stages.find((stage) => !stage.ready);
    const blockers = new Set([
      ...(item.data.actual_release_gate?.blockers || []),
      ...(item.data.content?.blockers || []),
    ]);
    return {
      stage: active ? `当前阶段：${active.label}` : "发布前条件已满足",
      blockers: String(blockers.size),
      images: String(item.data.content?.image_count || 0),
      approval: item.data.product?.actual_product_approved ? "已锁定" : "待审批",
      tone: item.data.actual_release_gate?.ready ? "safe" : "warn",
    };
  }

  function renderQueue() {
    const grid = $("#queueGrid");
    if (!queueItems.length) {
      grid.innerHTML = '<div class="image-fallback">队列为空。请在上方输入商品并加入队列。</div>';
      return;
    }
    grid.innerHTML = queueItems.map((item) => {
      const key = productKey(item.offer_id, item.seller_sku);
      const summary = queueSummary(item);
      const isCurrent = key === currentQueueKey;
      const title = item.data?.product?.title || `Offer ${item.offer_id}`;
      return `
        <article class="queue-card${isCurrent ? " current" : ""}${item.loading ? " is-loading" : ""}"
                 data-key="${esc(key)}">
          <header>
            <h3 title="${esc(title)}">${esc(title)}</h3>
            <span class="badge ${summary.tone}">${isCurrent ? "当前商品" : "队列中"}</span>
          </header>
          <div class="queue-identity">
            <span>Offer ${esc(item.offer_id)}</span>
            <span>Seller SKU ${esc(item.seller_sku)}</span>
          </div>
          <div class="queue-metrics">
            <div><span>阻塞</span><strong>${esc(summary.blockers)}</strong></div>
            <div><span>内容图</span><strong>${esc(summary.images)}</strong></div>
            <div><span>审批</span><strong>${esc(summary.approval)}</strong></div>
          </div>
          <p class="queue-stage">${esc(summary.stage)}</p>
          <footer>
            <button type="button" data-action="switch" data-key="${esc(key)}"
                    ${isCurrent || item.loading || approvalSubmitting ? "disabled" : ""}>
              ${isCurrent ? "正在查看" : "打开商品"}
            </button>
            <button type="button" data-action="remove" data-key="${esc(key)}"
                    ${isCurrent || approvalSubmitting ? "disabled" : ""}>
              移出队列
            </button>
          </footer>
        </article>
      `;
    }).join("");
  }

  function syncCurrentUrl(item) {
    const url = new URL(window.location.href);
    url.searchParams.set("offer_id", item.offer_id);
    url.searchParams.set("seller_sku", item.seller_sku);
    history.replaceState(null, "", url);
  }

  function addToQueue(offerId, sellerSku, { select = true } = {}) {
    const cleanOffer = String(offerId || "").trim();
    const cleanSku = String(sellerSku || "").trim();
    if (!validIdentity(cleanOffer, cleanSku)) return null;
    const key = productKey(cleanOffer, cleanSku);
    let item = queueItem(key);
    if (!item) {
      if (queueItems.length >= MAX_QUEUE_ITEMS) {
        $("#queueMessage").textContent = `队列最多保存 ${MAX_QUEUE_ITEMS} 件商品。`;
        return null;
      }
      item = {
        offer_id: cleanOffer,
        seller_sku: cleanSku,
        data: null,
        error: "",
        loading: false,
      };
      queueItems.push(item);
      saveQueue();
    }
    if (select) currentQueueKey = key;
    renderQueue();
    return item;
  }

  function renderProduct(data) {
    const product = data.product || {};
    $("#productTitle").textContent = product.title || "未命名商品";
    $("#productIdentity").innerHTML = [
      `Offer ${esc(product.offer_id || "—")}`,
      `1688 来源 ${esc(product.source_offer_id || "—")}`,
      `revision ${esc(product.revision ?? "—")}`,
    ].map((item) => `<span>${item}</span>`).join("");

    const factsReady = productFactsReady(product);
    setBadge($("#factsBadge"), factsReady ? "信息完整" : "需要补充", factsReady ? "safe" : "warn");
    const category = typeof product.category === "object"
      ? (product.category?.name || Object.values(product.category || {}).filter(Boolean).join(" / "))
      : product.category;
    const facts = [
      ["候选 Seller SKU", product.seller_sku_candidate || "—"],
      ["商品类目", category || "—"],
      ["商品标题", product.title || "—", true],
      ["采购成本", `¥ ${money(product.cost_cny)} CNY`],
      ["商品重量", product.weight_kg ? `${product.weight_kg} kg` : "—"],
      ["包装尺寸", (product.package_cm || []).join(" × ") || "—"],
      ["目标站点", (product.selected_sites || []).map((site) => siteNames[site] || site).join(" · ") || "—"],
      ["保留规格", (product.selected_sku_keys || []).join(" · ") || "—"],
    ];
    const factsElement = $("#productFacts");
    factsElement.classList.remove("skeleton-lines");
    factsElement.innerHTML = facts.map(([label, value, wide]) => `
      <div class="fact${wide ? " wide" : ""}">
        <span>${esc(label)}</span>
        <strong>${esc(value)}</strong>
      </div>
    `).join("");

    const ready = Boolean(data.actual_release_gate?.ready);
    $("#readinessLabel").textContent = ready ? "发布前条件" : "当前状态";
    $("#readinessValue").textContent = ready ? "已就绪" : "待完成";
    $("#readinessNote").textContent = ready
      ? "可以进入受控渠道流程"
      : `${(data.actual_release_gate?.blockers || []).length} 项关键条件待处理`;
  }

  function approvalEligible(data) {
    return Boolean(
      data.content?.approved
      && data.approval?.ready
      && data.approval?.state_patch_preview?.product_approval?.input_fingerprint
    );
  }

  function updateApprovalButton(data) {
    const approved = Boolean(data?.product?.actual_product_approved);
    const eligible = approvalEligible(data || {});
    $("#approvalCheckbox").disabled = (
      approved || !eligible || approvalSubmitting || pageLoading
    );
    $("#approvalButton").disabled = (
      approved
      || !eligible
      || !$("#approvalCheckbox").checked
      || approvalSubmitting
      || pageLoading
    );
  }

  function renderApproval(data, message = "") {
    const product = data.product || {};
    const approved = Boolean(product.actual_product_approved);
    const eligible = approvalEligible(data);
    const packageCm = Array.isArray(product.package_cm) ? product.package_cm : [];
    $("#approvalSku").textContent = product.seller_sku_candidate || "—";
    $("#approvalRevision").textContent = String(product.revision ?? "—");
    $("#approvalContent").textContent = data.content?.approved
      ? `${data.content.image_count || 0} 图已批准`
      : "内容包未批准";
    $("#approvalStatus").textContent = approved
      ? "已批准并锁定"
      : (eligible ? "可以审批" : "等待前置条件");
    $("#approvalFacts").innerHTML = [
      ["采购成本", product.cost_cny ? `¥ ${money(product.cost_cny)} CNY` : "—"],
      ["商品重量", product.weight_kg ? `${product.weight_kg} kg` : "—"],
      ["包装尺寸", packageCm.length ? `${packageCm.join(" × ")} cm` : "—"],
      ["目标站点", (product.selected_sites || []).map((site) => siteNames[site] || site).join(" · ") || "—"],
      ["保留规格", (product.selected_sku_keys || []).join(" · ") || "—"],
      ["内容包 ID", data.content?.package_id || "—"],
    ].map(([label, value]) => `
      <div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>
    `).join("");
    $("#approvalCheckbox").checked = approved;
    $(".approval-button-label").textContent = approved
      ? "商品字段已锁定"
      : "批准并锁定商品字段";
    $("#approvalMessage").textContent = message || (
      approved
        ? "本地审批事实已保存；外部发布仍保持关闭。"
        : (eligible
          ? "请逐项核对上方事实，再勾选确认。"
          : "内容包批准且审批预览通过后，才可保存本地商品审批。")
    );
    updateApprovalButton(data);
  }

  async function submitApproval() {
    if (!currentData || !$("#approvalCheckbox").checked || approvalSubmitting) return;
    const approvalKey = productKey(
      currentData.product?.offer_id,
      currentData.product?.seller_sku_candidate,
    );
    if (loadedQueueKey !== currentQueueKey || approvalKey !== currentQueueKey) {
      showError("当前商品仍在加载，不能使用上一件商品的审批 revision。");
      updateApprovalButton({});
      return;
    }
    approvalSubmitting = true;
    renderQueue();
    $("#approvalForm").classList.add("is-submitting");
    $("#approvalMessage").textContent = "正在复核 SKU 唯一性、内容包和 revision…";
    updateApprovalButton(currentData);
    try {
      const response = await fetch("/api/product-workspace/approve", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          offer_id: currentData.product?.offer_id,
          seller_sku: currentData.product?.seller_sku_candidate,
          expected_revision: currentData.product?.revision,
          approved_by: "Kyle",
          user_approved: true,
        }),
      });
      const payload = await response.json().catch(() => ({
        ok: false,
        error: `服务返回 HTTP ${response.status}`,
      }));
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || `服务返回 HTTP ${response.status}`);
      }
      currentData = payload.dashboard;
      loadedQueueKey = approvalKey;
      const item = queueItem(approvalKey);
      if (item) item.data = payload.dashboard;
      render(payload.dashboard);
      renderApproval(
        payload.dashboard,
        payload.idempotent
          ? "该 revision 已审批，无需重复写入；外部发布仍保持关闭。"
          : "本地商品审批已保存，字段已锁定；没有发生妙手、渠道或数据库写入。",
      );
      showError("");
    } catch (error) {
      const message = friendlyError(error.message);
      showError(message);
      $("#approvalMessage").textContent = `${message} 请重新读取最新状态后再审批。`;
    } finally {
      approvalSubmitting = false;
      $("#approvalForm").classList.remove("is-submitting");
      updateApprovalButton(currentData || {});
      renderQueue();
    }
  }

  function renderNextStep(data, stages) {
    const currentIndex = Math.max(0, stages.findIndex((stage) => !stage.ready));
    const stage = stages[currentIndex] || stages[stages.length - 1];
    const blockers = (data.actual_release_gate?.blockers || []).map(translateBlocker);
    const contentBlockers = (data.content?.blockers || [])
      .map(translateBlocker)
      .filter((item) => !blockers.includes(item));
    let allBlockers = [...blockers, ...contentBlockers];
    if (
      data.content?.stale_external_write
      && blockers.some((item) => item.includes("旧的 11 图版本"))
    ) {
      allBlockers = allBlockers.filter((item) => !item.startsWith("妙手图片记录与"));
    }
    allBlockers = [...new Set(allBlockers)];
    const descriptions = {
      product: "补齐商品标题、规格、成本、重量、包装尺寸与目标站点。",
      content: "完成内容审核，并确认最终图片的选择、版本与顺序。",
      approval: "确认候选 Seller SKU，保存商品审批并锁定当前商业字段。",
      sync: "将当前最终图片同步到妙手，并以回读结果确认内容完全一致。",
      channels: "检查各渠道草稿条件，再进入需要二次确认的渠道流程。",
    };
    $("#nextStepNumber").textContent = String(currentIndex + 1).padStart(2, "0");
    $("#nextStepTitle").textContent = data.actual_release_gate?.ready
      ? "进入受控渠道流程"
      : stage.label;
    $("#nextStepDescription").textContent = data.actual_release_gate?.ready
      ? "发布前条件已满足。进入详细工作台后，外部动作仍需逐项确认。"
      : descriptions[stage.key];
    $("#blockerList").innerHTML = allBlockers.length
      ? allBlockers.map((item) => `<li>${esc(item)}</li>`).join("")
      : '<li class="ok">当前发布前条件均已满足。</li>';
  }

  function imageType(image) {
    if (image.asset_type === "source") return "来源实拍图";
    if (image.shot_id === "sz1") return "AI 尺寸图";
    return "AI 场景图";
  }

  function renderImages(content) {
    const approved = Boolean(content?.approved);
    const synced = Boolean(content?.current_image_write_verified);
    setBadge(
      $("#contentBadge"),
      approved
        ? `${content.image_count} 图已批准 · ${content.strategy === "source_only" ? "原素材直发" : "AI 辅助"}`
        : "待内容审核",
      approved ? "safe" : "warn",
    );
    setBadge(
      $("#syncBadge"),
      synced ? "妙手已同步" : "妙手待更新",
      synced ? "safe" : "warn",
    );

    const grid = $("#imageGrid");
    grid.classList.remove("skeleton-cards");
    const images = Array.isArray(content?.images) ? content.images : [];
    if (!images.length) {
      grid.innerHTML = '<div class="image-fallback">尚无已批准的商品图片。</div>';
    } else {
      grid.innerHTML = images.map((image, index) => {
        const proxy = `/api/proxy-image?url=${encodeURIComponent(image.image_url || "")}`;
        const type = imageType(image);
        return `
          <figure class="image-card">
            <div class="image-wrap">
              <img src="${esc(proxy)}" alt="商品图片 ${index + 1}，${esc(type)}" loading="${index < 2 ? "eager" : "lazy"}">
              <span class="image-index">${String(index + 1).padStart(2, "0")}</span>
            </div>
            <figcaption>
              <strong>${esc(type)}</strong>
              <small title="${esc(image.artifact_id || image.audit_id)}">${esc(image.artifact_id || image.audit_id || "已审核素材")}</small>
            </figcaption>
          </figure>
        `;
      }).join("");
      grid.querySelectorAll("img").forEach((image) => {
        image.addEventListener("error", () => {
          const wrap = image.closest(".image-wrap");
          image.remove();
          const fallback = document.createElement("div");
          fallback.className = "image-fallback";
          fallback.textContent = "图片暂时无法加载，审核证据仍保留";
          wrap.prepend(fallback);
        }, { once: true });
      });
    }

    const notices = [];
    if (synced) {
      notices.push({ text: "当前图片已通过妙手回读验证", safe: true });
    } else if (content?.stale_external_write) {
      notices.push({ text: `妙手当前记录为 ${content.written_image_count || "旧"} 图，与最终图片不一致`, safe: false });
    } else {
      notices.push({ text: "当前最终图片尚未同步到妙手", safe: false });
    }
    if ((content?.superseded_artifact_ids || []).length) {
      notices.push({
        text: `${content.superseded_artifact_ids.length} 个历史图片版本已排除`,
        safe: true,
      });
    }
    $("#contentNotice").innerHTML = notices
      .map((item) => `<span class="${item.safe ? "safe" : ""}">${esc(item.text)}</span>`)
      .join("");
  }

  function renderPricingReview(pricing) {
    const allRows = Array.isArray(pricing?.all_legacy_store_prices)
      ? pricing.all_legacy_store_prices
      : [];
    const selectedKeys = new Set(
      (pricing?.selected_store_prices || []).map((row) => row.target_key),
    );
    const validProfits = allRows
      .map((row) => Number(row.estimated_profit_cny))
      .filter(Number.isFinite);
    const adjusted = allRows.filter((row) => row.min_profit_adjusted).length;
    $("#pricingSummary").textContent = allRows.length
      ? `${allRows.length} 个国家/店铺价格 · 最低预计利润 ¥${money(Math.min(...validProfits))} · ${adjusted} 个触发利润底线`
      : "当前没有可展示的售价计算。";

    const grid = $("#storePriceGrid");
    if (!allRows.length) {
      grid.innerHTML = '<div class="image-fallback">补全成本、重量和包装后计算售价。</div>';
    } else {
      grid.innerHTML = allRows.map((row) => {
        const selected = selectedKeys.has(row.target_key);
        const fees = Object.entries(row.fees || {});
        const formula = Object.entries(row.formula_parameters || {});
        return `
          <article class="store-price-card${selected ? " selected" : ""}">
            <div class="store-price-main">
              <header>
                <h4>${esc(row.shop || "店铺")}<small>${esc(row.region)} · ${esc(row.shop_id || row.target_key || "—")}</small></h4>
                <span class="price-selection">${selected ? "本次选择" : "保留计算"}</span>
              </header>
              <div class="price-hero">
                <span>建议挂牌价</span>
                <strong>${esc(localMoney(row.list_price, row.currency))}</strong>
                <small>折扣后成交价 ${esc(localMoney(row.sale_after_discount, row.currency))}</small>
              </div>
              <div class="price-profit">
                <div><span>预计利润</span><strong>¥${esc(money(row.estimated_profit_cny))}</strong></div>
                <div><span>利润率</span><strong>${esc(money(row.profit_margin_on_sale_pct))}%</strong></div>
              </div>
            </div>
            <details>
              <summary>费用与公式参数</summary>
              <dl class="price-fees">
                ${fees.map(([key, value]) => `
                  <div><dt>${esc(feeNames[key] || key)}</dt><dd>${esc(localMoney(value, row.currency))}</dd></div>
                `).join("")}
                ${formula.map(([key, value]) => `
                  <div><dt>${esc(key)}</dt><dd>${esc(String(value))}</dd></div>
                `).join("")}
              </dl>
            </details>
          </article>
        `;
      }).join("");
    }

    const sections = pricing?.legacy_audit?.sections || [];
    $("#pricingAuditTables").innerHTML = sections.map((section) => `
      <section class="pricing-audit-section">
        <h4>${esc(section.title || section.section || "售价审计")}</h4>
        <p>${(section.notes || []).filter(Boolean).map(esc).join(" · ")}</p>
        <div class="pricing-table-wrap">
          <table class="pricing-table">
            <thead><tr>${(section.header_labels || []).map((label) => `<th>${esc(label)}</th>`).join("")}</tr></thead>
            <tbody>${(section.rows || []).map((row) => `
              <tr>${row.map((cell) => `<td>${esc(cell == null ? "—" : String(cell)).replaceAll("\n", "<br>")}</td>`).join("")}</tr>
            `).join("")}</tbody>
          </table>
        </div>
      </section>
    `).join("");
  }

  function channelPriceLine(pricing) {
    if ((pricing?.store_prices || []).length > 1) {
      return {
        label: "妙手公共草稿包含价格",
        value: `${pricing.store_prices.length} 个已选店铺`,
      };
    }
    const store = (pricing?.store_prices || [])[0];
    if (store?.list_price != null) {
      return {
        label: `${store.shop || "TikTok"} ${store.region || ""} 建议挂牌价`,
        value: localMoney(store.list_price, store.currency),
      };
    }
    const derived = pricing?.derived_preview || {};
    if (derived.global_original_price_cny != null) {
      return {
        label: `由 TikTok ${derived.source_currency || ""} 价格派生`,
        value: `¥${money(derived.global_original_price_cny)} CNY`,
      };
    }
    if (derived.price_cny != null) {
      return {
        label: "由 TikTok 主商品派生（划线价同时保留）",
        value: `¥${money(derived.price_cny)} / 划线 ¥${money(derived.old_price_cny)}`,
      };
    }
    return null;
  }

  function renderChannels(omnichannel, publication, releaseReady) {
    const targets = Array.isArray(omnichannel?.targets) ? omnichannel.targets : [];
    const grid = $("#channelGrid");
    const summary = $("#channelPlanSummary");
    const blockers = Array.from(new Set(
      (omnichannel?.blockers || []).map(translateBlocker).filter(Boolean),
    ));
    const token = omnichannel?.confirmation_token_summary?.masked || "未生成";
    const approval = omnichannel?.approval_summary || {};
    const targetLabels = Array.isArray(approval.target_labels)
      ? approval.target_labels
      : [];
    summary.textContent = targets.length
      ? `计划 ${omnichannel.plan_id || "—"} · ${targets.length} 个店铺目标 · ${approval.image_count || 0} 张图 · 确认令牌 ${token}`
      : "商品与内容审批完成后，系统会生成精确的店铺矩阵和一次性确认摘要。";
    $("#channelBlockers").innerHTML = blockers
      .map((item) => `<span>${esc(item)}</span>`)
      .join("");

    if (!targets.length) {
      grid.innerHTML = '<div class="channel-card"><p>商品审批完成后将生成渠道草稿预览。</p></div>';
      $("#publishAllButton").disabled = true;
      $("#publishAllNote").textContent = blockers[0] || "全渠道计划尚未就绪。";
      return;
    }

    grid.innerHTML = targets.map((target) => {
      const failedChecks = (target.preflights || []).filter((check) => !check.passed);
      const preflightReady = Boolean(target.executable) && !failedChecks.length;
      const adapterStatus = target.repository_adapter_audited ? "已审计" : "未审计";
      const dependencies = (target.depends_on || []).length
        ? target.depends_on.join(" → ")
        : "无";
      const message = preflightReady
        ? "该目标的本地发布预检通过；仍未调用真实渠道适配器。"
        : translateBlocker(failedChecks[0]?.detail || "等待前置依赖和适配器审计。");
      const externalStepCount = (target.steps || [])
        .filter((step) => step.mutates_external_state).length;
      const priceLine = channelPriceLine(target.pricing || {});
      return `
        <article class="channel-card ${preflightReady ? "ready" : "blocked"}">
          <header>
            <h3>
              ${esc(channelNames[target.channel] || target.channel)}
              <small>${esc(target.site || "COMMON")}</small>
            </h3>
            <span class="badge ${preflightReady ? "safe" : "warn"}">
              ${preflightReady ? "预检通过" : "已阻塞"}
            </span>
          </header>
          <p>${esc(message)}</p>
          ${priceLine ? `
            <p class="channel-price">
              <span>${esc(priceLine.label)}</span>
              <strong>${esc(priceLine.value)}</strong>
            </p>
          ` : ""}
          <dl class="channel-meta">
            <div><dt>适配器</dt><dd>${esc(adapterStatus)}</dd></div>
            <div><dt>步骤 / 外部动作</dt><dd>${target.steps?.length || 0} / ${externalStepCount}</dd></div>
            <div><dt>依赖</dt><dd>${esc(dependencies)}</dd></div>
          </dl>
        </article>
      `;
    }).join("");

    const allReady = Boolean(
      releaseReady
      && omnichannel?.ready
      && omnichannel?.all_preflights_passed
      && targetLabels.length === targets.length,
    );
    // The execution endpoint is deliberately absent in this release.  Even a
    // fully green preview must not turn a disabled control into a network write.
    $("#publishAllButton").disabled = true;
    $("#publishAllNote").textContent = allReady
      ? "全部预检已经通过；真实执行端点仍在发布审计中，本版本不会调用渠道接口。"
      : `${targets.length} 个目标中有前置条件未完成；当前按钮保持强制禁用。`;
  }

  function render(data) {
    currentData = data;
    const stages = stageModel(data);
    renderProduct(data);
    renderApproval(data);
    renderStages(stages);
    renderNextStep(data, stages);
    renderImages(data.content || {});
    renderPricingReview(data.pricing_review || {});
    renderChannels(
      data.omnichannel_preview || {},
      data.publication_rehearsal || {},
      Boolean(data.actual_release_gate?.ready),
    );

    const offer = data.product?.offer_id || $("#offerId").value.trim();
    const studioUrl = `/ai-image-studio?offer_id=${encodeURIComponent(offer)}`;
    $("#workbenchLink").href = studioUrl;
    $("#studioNavLink").href = studioUrl;
    $("#workbenchLink").removeAttribute("aria-disabled");
  }

  function clearCurrentApprovalContext() {
    currentData = null;
    loadedQueueKey = "";
    $("#approvalCheckbox").checked = false;
    updateApprovalButton({});
  }

  async function refreshQueueProduct(item) {
    if (item.loading && item.promise) return item.promise;
    const key = productKey(item.offer_id, item.seller_sku);
    item.loading = true;
    item.error = "";
    if (key === currentQueueKey) {
      clearCurrentApprovalContext();
      setLoading(true);
    }
    renderQueue();
    item.promise = (async () => {
      try {
        const data = await fetchDashboard(item.offer_id, item.seller_sku);
        item.data = data;
        item.error = "";
        if (key === currentQueueKey) {
          loadedQueueKey = key;
          $("#offerId").value = item.offer_id;
          $("#sellerSku").value = item.seller_sku;
          syncCurrentUrl(item);
          render(data);
          showError("");
        }
        return data;
      } catch (error) {
        const message = friendlyError(error.message);
        item.data = null;
        item.error = message;
        if (key === currentQueueKey) {
          showError(message);
          renderFailure(message);
        }
        throw error;
      } finally {
        item.loading = false;
        item.promise = null;
        if (key === currentQueueKey) setLoading(false);
        renderQueue();
      }
    })();
    return item.promise;
  }

  async function selectQueueProduct(key) {
    if (approvalSubmitting) return;
    const item = queueItem(key);
    if (!item) return;
    currentQueueKey = key;
    $("#offerId").value = item.offer_id;
    $("#sellerSku").value = item.seller_sku;
    syncCurrentUrl(item);
    clearCurrentApprovalContext();
    renderQueue();
    await refreshQueueProduct(item).catch(() => {});
  }

  async function addAndOpenCurrentInput() {
    const offerId = $("#offerId").value.trim();
    const sellerSku = $("#sellerSku").value.trim();
    if (!validIdentity(offerId, sellerSku)) {
      const message = "Offer ID 和 Seller SKU 必须是 1–32 位数字。";
      showError(message);
      return;
    }
    const item = addToQueue(offerId, sellerSku, { select: true });
    if (!item) return;
    $("#queueMessage").textContent = "商品已加入并行队列。";
    await selectQueueProduct(productKey(item.offer_id, item.seller_sku));
  }

  async function refreshAllQueueProducts() {
    if (queueRefreshing || !queueItems.length) return;
    queueRefreshing = true;
    $("#refreshAllButton").disabled = true;
    $(".queue-section").classList.add("is-refreshing");
    $("#queueMessage").textContent = `最多 ${QUEUE_REFRESH_CONCURRENCY} 件商品并行读取中。`;
    clearCurrentApprovalContext();
    let cursor = 0;
    const worker = async () => {
      while (cursor < queueItems.length) {
        const item = queueItems[cursor];
        cursor += 1;
        await refreshQueueProduct(item).catch(() => {});
      }
    };
    const workers = Array.from(
      { length: Math.min(QUEUE_REFRESH_CONCURRENCY, queueItems.length) },
      () => worker(),
    );
    await Promise.allSettled(workers);
    queueRefreshing = false;
    $("#refreshAllButton").disabled = false;
    $(".queue-section").classList.remove("is-refreshing");
    const failures = queueItems.filter((item) => item.error).length;
    $("#queueMessage").textContent = failures
      ? `刷新完成，${failures} 件商品读取失败；其余商品已更新。`
      : `刷新完成，共更新 ${queueItems.length} 件商品。`;
    renderQueue();
  }

  $("#lookupForm").addEventListener("submit", (event) => {
    event.preventDefault();
    addAndOpenCurrentInput();
  });
  $("#refreshButton").addEventListener("click", () => {
    const item = queueItem(currentQueueKey);
    if (item) refreshQueueProduct(item).catch(() => {});
  });
  $("#refreshAllButton").addEventListener("click", refreshAllQueueProducts);
  $("#refreshChannelsButton").addEventListener("click", () => {
    const item = queueItem(currentQueueKey);
    if (item) refreshQueueProduct(item).catch(() => {});
  });
  $("#queueGrid").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button || button.disabled) return;
    const key = button.dataset.key || "";
    if (button.dataset.action === "switch") {
      selectQueueProduct(key);
      return;
    }
    if (button.dataset.action === "remove" && key !== currentQueueKey) {
      queueItems = queueItems.filter((item) => (
        productKey(item.offer_id, item.seller_sku) !== key
      ));
      saveQueue();
      renderQueue();
      $("#queueMessage").textContent = "商品已移出队列。";
    }
  });
  $("#approvalCheckbox").addEventListener("change", () => {
    updateApprovalButton(currentData || {});
  });
  $("#approvalForm").addEventListener("submit", (event) => {
    event.preventDefault();
    submitApproval();
  });

  const initial = new URLSearchParams(window.location.search);
  queueItems = readQueue();
  const initialOffer = initial.get("offer_id");
  const initialSku = initial.get("seller_sku");
  let initialItem = null;
  if (validIdentity(initialOffer, initialSku)) {
    initialItem = addToQueue(initialOffer, initialSku, { select: true });
  } else if (queueItems.length) {
    initialItem = queueItems[0];
    currentQueueKey = productKey(initialItem.offer_id, initialItem.seller_sku);
  } else {
    initialItem = addToQueue($("#offerId").value, $("#sellerSku").value, { select: true });
  }
  renderQueue();
  if (initialItem) {
    $("#offerId").value = initialItem.offer_id;
    $("#sellerSku").value = initialItem.seller_sku;
    syncCurrentUrl(initialItem);
    refreshQueueProduct(initialItem).catch(() => {});
  }
})();
