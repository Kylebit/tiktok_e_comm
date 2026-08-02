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
  const publicationSiteNames = {
    COMMON: "公共草稿",
    PH: "菲律宾",
    MY: "马来西亚",
    TH: "泰国",
    VN: "越南",
    MX: "墨西哥",
    GB: "英国",
    RU: "俄罗斯",
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
  const LISTING_COPY_POLICY_VERSION = "listing-copy-candidates-v6";
  const MAX_QUEUE_ITEMS = 50;
  const QUEUE_REFRESH_CONCURRENCY = 4;
  let currentData = null;
  let approvalSubmitting = false;
  let factsSubmitting = false;
  let titleDraftSubmitting = false;
  let titleAdoptSubmitting = false;
  let releaseSubmitting = false;
  let releasePlanApprovalSubmitting = false;
  let pageLoading = false;
  let queueRefreshing = false;
  let queueItems = [];
  let currentQueueKey = "";
  let loadedQueueKey = "";
  let pendingPublicationTargets = new Set();
  let appliedPublicationTargets = new Set();
  const SHOPEE_PRICE_REPAIR_TARGETS = new Set(["shopee:PH", "shopee:TH"]);
  const shopeePriceRepairStates = new Map();
  const TARGET_SCOPED_ACTION_TARGETS = new Set(["shopee:MY", "shopee:VN", "ozon:RU"]);
  const targetScopedActionStates = new Map();
  const targetRecoveryActions = new Map();
  const ONECLICK_PREVIEW_SCHEMA = "release-batch-preparation/v2";
  const ONECLICK_STATUS_SCHEMA = "oneclick-release-status/v2";
  const SHOPEE_GLOBAL_CONTROL_TARGET = "shopee:GLOBAL";
  const SHOPEE_GLOBAL_PLAN_PREVIEW_SCHEMA = "shopee-global-plan-preview/v1";
  const SHOPEE_GLOBAL_PLAN_CANDIDATE_SCHEMA =
    "shopee-global-plan-candidate/v1";
  const SHOPEE_GLOBAL_PLAN_APPROVAL_SCHEMA =
    "shopee-global-plan-approval-response/v1";
  const SHOPEE_CATEGORY_DECISION_PREVIEW_SCHEMA =
    "channel-category-decision-preview/v2";
  const SHOPEE_CATEGORY_DECISION_STATUSES = new Set([
    "SELECTED",
    "READY_FOR_SELECTION",
    "BLOCKED_CAPABILITY",
    "RECHECK_REQUIRED",
  ]);
  const APPROVED_SHOPEE_GLOBAL_PLAN_SCHEMA_MODES = new Map([
    ["approved-shopee-global-plan/v1", "NEW_GLOBAL"],
    ["approved-shopee-global-plan/v2", "EXISTING_GLOBAL"],
  ]);
  const ONECLICK_TERMINAL_PHASES = new Set([
    "SUCCEEDED",
    "WAITING_MANUAL_ACCEPTANCE",
    "BLOCKED",
    "SYSTEMIC_STOPPED",
  ]);
  const ONECLICK_POLL_INTERVAL_MS = 750;
  const ONECLICK_LOCAL_READ_TIMEOUT_MS = 15000;
  const ONECLICK_LOCAL_POST_TIMEOUT_MS = 15000;
  const COLLECTBOX_ACTION_SCHEMA = "collectbox-action-status/v1";
  const COLLECTBOX_ACTION_STATUSES = new Set([
    "READY",
    "RUNNING",
    "PARTIAL_FAILED",
    "SUCCEEDED",
    "BLOCKED_IDENTITY",
  ]);
  const COLLECTBOX_PLATFORM_STATUSES = new Set([
    "PENDING",
    "RUNNING",
    "SUCCEEDED",
    "FAILED_RETRYABLE",
    "RECONCILIATION_REQUIRED",
  ]);
  const COLLECTBOX_TARGET_OUTCOME_STATUSES = new Set([
    "SUCCEEDED",
    "REPAIRED_SUCCEEDED",
    "FAILED",
  ]);
  const COLLECTBOX_ACTION_POLL_INTERVAL_MS = 400;
  const SHOPEE_GLOBAL_READ_TIMEOUT_MS = 180000;
  const ONECLICK_DEPENDENCY_POLICY_VERSION =
    "oneclick-target-dependency/mvp-unblocked-v1";
  const ONECLICK_JOB_PHASES = new Set([
    "PENDING",
    "PREPARING",
    "READY",
    "RUNNING",
    "SUCCEEDED",
    "WAITING_MANUAL_ACCEPTANCE",
    "BLOCKED",
    "SYSTEMIC_STOPPED",
  ]);
  const ONECLICK_TARGET_STATUSES = new Set([
    "PENDING",
    "PREPARING",
    "READY",
    "DISPATCHING",
    "SUCCEEDED",
    "SUCCEEDED_MANUAL_REVIEW",
    "SUBMITTED_UNVERIFIED",
    "FAILED_PRE_SUBMIT",
    "RECONCILIATION_REQUIRED",
    "BLOCKED_AUTH",
    "BLOCKED_INVENTORY",
    "BLOCKED_CAPABILITY",
    "BLOCKED_SOURCE_IDENTITY",
    "BLOCKED_SKU_LINEAGE",
  ]);
  const ONECLICK_CLASSIFICATIONS = new Set([
    "PREPARE_PENDING",
    "EXACT_READY_AUTOMATIC",
    "READY_SUBMIT_MANUAL",
    "BLOCKED_AUTH",
    "BLOCKED_INVENTORY",
    "BLOCKED_CAPABILITY",
    "BLOCKED_SOURCE_IDENTITY",
    "BLOCKED_SKU_LINEAGE",
    "SAFE_ACTION_REQUIRED",
  ]);
  const ONECLICK_ACTIONS = new Set([
    "prepare_batch",
    "wait_for_preparation",
    "wait_for_worker",
    "wait_for_dispatch_receipt",
    "wait_for_dependency",
    "resolve_prerequisite_target",
    "verify_submission_in_marketplace",
    "review_verified_observation_warning",
    "retry_exact_zero_write_action",
    "reconcile_before_any_retry",
    "restore_channel_authorization",
    "approve_sellable_inventory",
    "review_approved_content_facts",
    "review_logistics_policy",
    "review_shopee_global_plan",
    "wait_for_channel_capability",
    "resolve_source_product_identity",
    "resolve_predecessor_sku_lineage",
    "perform_governed_safe_action",
    "enable_oneclick_dispatch",
    "refresh_release_state",
    "resolve_plan_or_source_identity",
  ]);
  const ONECLICK_REASON_CATEGORIES = new Set([
    "AUTH",
    "INVENTORY",
    "CAPABILITY",
    "CONTENT",
    "LOGISTICS",
    "SAFE_ACTION",
    "PRE_SUBMIT",
    "POST_WRITE",
    "DEPENDENCY",
    "SYSTEMIC_IDENTITY",
    "SYSTEMIC_CONTRACT",
  ]);
  const ONECLICK_DIGEST_KEYS = Object.freeze([
    "payload",
    "targets",
    "source_identity",
    "source_identity_payload",
    "sku_lineage",
    "sku_lineage_payload",
    "adapter_policy",
  ]);
  const ONECLICK_TARGET_DIGEST_KEYS = Object.freeze([
    "prepared_command",
    "proof",
    "adapter_policy",
    "shared_resource",
    "shared_resource_context",
  ]);
  const ONECLICK_POSTPUBLISH_PROMOTION_PREREQUISITES = new Set([
    "tiktok:LH_PH",
    "tiktok:LH_MY",
    "tiktok:LH_TH",
    "tiktok:LH_VN",
    "shopee:PH",
    "shopee:MY",
    "shopee:TH",
    "shopee:VN",
  ]);
  const COLLECTBOX_TARGETS = Object.freeze({
    TIKTOK: Object.freeze([
      "tiktok:LH_PH",
      "tiktok:LH_MY",
      "tiktok:LH_TH",
      "tiktok:LH_VN",
      "tiktok:HB_PH",
      "tiktok:HB_MY",
      "tiktok:HB_TH",
      "tiktok:HB_VN",
      "tiktok:MX",
      "tiktok:GB",
    ]),
    SHOPEE: Object.freeze([
      "shopee:PH",
      "shopee:MY",
      "shopee:TH",
      "shopee:VN",
    ]),
  });
  const COLLECTBOX_TARGET_OPERATIONS = Object.freeze({
    TIKTOK: Object.freeze([
      "detail:create",
      "shop:claim",
      "detail:update",
    ]),
    SHOPEE: Object.freeze(["detail:update"]),
  });
  const COLLECTBOX_ALLOWED_WRITE_CLASSES = Object.freeze(
    Object.fromEntries(["TIKTOK", "SHOPEE"].map((platform) => {
      const platformName = platform.toLowerCase();
      const classes = new Set([`miaoshou:collectbox:claim:${platformName}`]);
      COLLECTBOX_TARGET_OPERATIONS[platform].forEach((operation) => {
        COLLECTBOX_TARGETS[platform].forEach((target) => {
          classes.add(
            `miaoshou:collectbox:${platformName}:${operation}:${target}`,
          );
        });
      });
      return [platform, classes];
    })),
  );
  const SHOPEE_GLOBAL_PLAN_COUNT_KEYS = Object.freeze([
    "category_path_depth",
    "attribute_count",
    "approved_image_count",
    "selected_image_count",
    "variation_tier_count",
    "model_count",
  ]);
  const SHOPEE_GLOBAL_PLAN_DIGEST_KEYS = Object.freeze([
    "observation_evidence_digest",
    "source_identity_digest",
    "sku_lineage_digest",
    "content_package_digest",
    "approved_copy_digest",
    "approved_source_image_manifest_digest",
    "selected_source_image_manifest_digest",
    "parcel_contract_digest",
    "target_pricing_digest",
    "policy_digest",
    "category_evidence_digest",
    "attribute_tree_digest",
    "brand_evidence_digest",
    "seller_stock_source_digest",
    "location_evidence_digest",
    "existing_global_identity_digest",
    "candidate_digest",
  ]);
  const oneClickExecution = {
    generation: 0,
    contextKey: "",
    identity: null,
    preview: null,
    job: null,
    previewAttempted: false,
    previewBusy: false,
    posting: false,
    postAttempted: false,
    resumePostAttempted: false,
    statusBusy: false,
    acceptanceCheckBusy: false,
    error: "",
    statusWarning: "",
    failureAction: null,
    timer: null,
    controller: null,
    finalDashboardRefreshed: false,
  };
  const collectboxAction = {
    generation: 0,
    contextKey: "",
    identity: null,
    projection: null,
    previewAttempted: false,
    previewBusy: false,
    posting: false,
    statusBusy: false,
    error: "",
    timer: null,
    controller: null,
  };
  const shopeeGlobalPlanReview = {
    generation: 0,
    contextKey: "",
    candidate: null,
    approval: null,
    approvalCurrent: false,
    previewAttempted: false,
    previewBusy: false,
    submitting: false,
    approvalPostAttempted: false,
    reconciliationBusy: false,
    error: "",
    controller: null,
  };
  const shopeeCategoryDecisionReview = {
    generation: 0,
    contextKey: "",
    projection: null,
    draftIdentityDigest: "",
    draftBrandIdentityDigest: "",
    draftLocationIdentityDigest: "",
    requiredAttributeSelections: {},
    confirmSelection: false,
    confirmSellerStock: false,
    confirmConditionAndPreorder: false,
    confirmRequiredAttributes: false,
    previewAttempted: false,
    previewBusy: false,
    submitting: false,
    postAttempted: false,
    reconciliationBusy: false,
    error: "",
    message: "",
    controller: null,
  };

  function productKey(offerId) {
    return String(offerId || "").trim();
  }

  function validOfferId(offerId) {
    return /^\d{1,32}$/.test(String(offerId || "").trim());
  }

  function readQueue() {
    try {
      const parsed = JSON.parse(localStorage.getItem(QUEUE_STORAGE_KEY) || "[]");
      if (!Array.isArray(parsed)) return [];
      const seen = new Set();
      return parsed
        .filter((item) => validOfferId(item?.offer_id))
        .map((item) => ({
          offer_id: String(item.offer_id).trim(),
          seller_sku: "",
          data: null,
          error: "",
          loading: false,
        }))
        .filter((item) => {
          const key = productKey(item.offer_id);
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
        JSON.stringify(queueItems.map(({ offer_id }) => ({
          offer_id,
        }))),
      );
    } catch (_error) {
      $("#queueMessage").textContent = "浏览器无法保存队列；本次页面内仍可继续使用。";
    }
  }

  function queueItem(key) {
    return queueItems.find((item) => (
      productKey(item.offer_id) === key
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
    const customSku = text.match(/^selected SKU (.+) is a customer-service\/custom placeholder/);
    if (customSku) {
      return `已选规格 ${customSku[1]} 是“咨询客服/定制”占位项，不能作为正式采购成本。`;
    }
    const priceConflict = text.match(/^selected SKU prices conflict: (.+)$/);
    if (priceConflict) {
      return `已选规格存在多个采购价：${priceConflict[1]}；请只保留真实可采购规格或明确成本规则。`;
    }
    const costMismatch = text.match(/^cost_cny does not match the selected SKU price: (.+)$/);
    if (costMismatch) {
      return `当前采购成本与已选规格价格不一致：${costMismatch[1]}。`;
    }
    const missingSku = text.match(/^selected SKU (.+) is not present in source\.skus$/);
    if (missingSku) {
      return `已选规格 ${missingSku[1]} 已不在当前来源商品中，请重新同步并选择。`;
    }
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
      return "这件商品还没有本地发布档案。请在上方重新加入队列，系统会立即从妙手采集箱读取并建档。";
    }
    return text || "商品状态读取失败，请确认本地服务已启动。";
  }

  function isStateRevisionConflict(error) {
    const payload = error?.payload || {};
    return error?.status === 409 && (
      payload.error_code === "state_revision_conflict"
      || String(payload.error || error?.message || "").trim() === "state revision is stale"
    );
  }

  function setLoading(loading) {
    pageLoading = loading;
    $("#lookupForm").classList.toggle("is-loading", loading);
    $("#refreshButton").disabled = loading;
    if ($("#factsEditSaveButton")) updateFactsEditControls();
    if ($("#generateTitleDraftButton") && currentData) renderTitleDraft(currentData);
    if ($("#approvalButton")) updateApprovalButton(currentData || {});
    if ($("#applyPublicationScopeButton")) updatePublicationScopeControls();
    if ($("#approveReleasePlanButton")) updateReleaseControls(currentData || {});
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
    $("#readinessNote").textContent = "请检查 Offer ID，或先在内容与图片工作室读取来源";
    $("#stageRail").innerHTML = [
      "商品事实",
      "内容审批",
      "商品审批",
      "发布计划",
      "妙手待发布",
      "渠道执行",
      "回读对账",
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
    pendingPublicationTargets = new Set();
    appliedPublicationTargets = new Set();
    $("#publicationTargetGrid").innerHTML =
      '<div class="image-fallback">读取商品后选择发布平台与国家。</div>';
    $("#publicationScopeNote").textContent = "尚未读取可选发布范围。";
    $("#applyPublicationScopeButton").disabled = true;
    $("#channelGrid").innerHTML =
      '<div class="channel-card"><p>读取商品后展示渠道准备状态。</p></div>';
    $("#pricingSummary").textContent = "尚未读取售价计算。";
    $("#storePriceGrid").innerHTML =
      '<div class="image-fallback">读取商品后展示全部国家与店铺售价。</div>';
    $("#selectedChannelPriceGrid").innerHTML =
      '<div class="image-fallback">读取商品后展示已选渠道售价。</div>';
    $("#pricingAuditTables").innerHTML = "";
    $("#channelPlanSummary").textContent = "尚未形成全渠道发布计划。";
    $("#channelBlockers").innerHTML = "";
    $("#publishAllButton").disabled = true;
    $("#publishAllCheckbox").checked = false;
    $("#publishAllCheckbox").disabled = true;
    $("#publishAllNote").textContent = "请先成功读取商品与内容审批事实。";
    $("#releasePlanSummary").innerHTML =
      '<div><span>ReleasePlan</span><strong>尚未生成</strong></div>';
    $("#releasePlanCheckbox").checked = false;
    $("#releasePlanCheckbox").disabled = true;
    $("#approveReleasePlanButton").disabled = true;
    $("#releasePlanMessage").textContent = "";
    $("#prepareMiaoshouCheckbox").checked = false;
    $("#prepareMiaoshouCheckbox").disabled = true;
    $("#prepareMiaoshouButton").disabled = true;
    $("#prepareMiaoshouMessage").textContent = "";
    $("#commonOverwritePanel").hidden = true;
    $("#commonOverwriteConfirmLabel").hidden = true;
    $("#commonOverwriteButton").hidden = true;
    $("#commonOverwriteCheckbox").checked = false;
    $("#commonOverwriteCheckbox").disabled = true;
    $("#commonOverwriteButton").disabled = true;
    $("#commonOverwriteMessage").textContent = "";
    $("#publishRunMessage").textContent = "";
    $("#releaseRunLedger").textContent = "当前没有发布运行。";
    $("#workbenchLink").removeAttribute("href");
    $("#workbenchLink").setAttribute("aria-disabled", "true");
    $("#approvalSku").textContent = "—";
    $("#approvalRevision").textContent = "—";
    $("#approvalContent").textContent = "未加载";
    $("#approvalStatus").textContent = "未加载";
    $("#approvalFacts").innerHTML = "";
    $("#approvalButton").disabled = true;
    $("#approvalMessage").textContent = "";
    $("#productFactsForm").reset();
    $("#productFactsForm").dataset.revision = "";
    $("#productFactsForm").dataset.locked = "true";
    $("#factsEditRevision").textContent = "revision —";
    $("#titleDraftStatus").textContent = "读取商品后才能生成平台标题候选。";
    $("#titleCandidateGrid").innerHTML = "";
    $("#generateTitleDraftButton").disabled = true;
    $("#productSpecGrid").innerHTML =
      '<span class="source-spec-empty">采集完成后显示来源规格。</span>';
    $("#factsEditMessage").textContent = message;
    updateFactsEditControls();
  }

  function setBadge(element, text, tone) {
    element.textContent = text;
    element.className = `badge ${tone}`;
  }

  async function fetchDashboard(offerId, publicationTargets = null) {
    const params = new URLSearchParams({
      offer_id: offerId,
    });
    if (Array.isArray(publicationTargets)) {
      publicationTargets.forEach((target) => params.append("target", target));
    }
    const response = await fetch(`/api/product-workspace/dashboard?${params}`, {
      headers: { Accept: "application/json" },
    });
    const payload = await response.json().catch(() => ({
      ok: false,
      error: `服务返回 HTTP ${response.status}`,
    }));
    if (!response.ok || payload.ok === false) {
      const error = new Error(payload.error || `服务返回 HTTP ${response.status}`);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function oneClickAuthorityAvailable(data) {
    const release = data?.release_v1 || {};
    return Boolean(
      release.plan_approved === true
      && String(release.plan?.plan_id || "").trim(),
    );
  }

  function oneClickIdentity(data) {
    const release = data?.release_v1 || {};
    const plan = release.plan || {};
    const offerId = String(data?.product?.offer_id || "").trim();
    const planId = String(plan.plan_id || "").trim();
    const revision = data?.product?.revision;
    if (
      !oneClickAuthorityAvailable(data)
      || release.plan_approved !== true
      || !offerId
      || !planId
      || !Number.isInteger(revision)
    ) return null;
    return {
      key: `${offerId}\u0000${planId}\u0000${revision}`,
      offerId,
      planId,
      revision,
      payloadDigest: String(plan.payload_digest || ""),
      targetsDigest: String(plan.targets_digest || ""),
      publicationTargets: [...(data?.publication_scope?.selected_labels || [])],
    };
  }

  function shopeeGlobalPlanIdentity(data) {
    const offerId = String(data?.product?.offer_id || "").trim();
    const revision = data?.product?.revision;
    if (!validOfferId(offerId) || !Number.isInteger(revision)) return null;
    return {
      key: `${offerId}\u0000${revision}`,
      offerId,
      revision,
    };
  }

  function cancelOneClickTimer() {
    if (oneClickExecution.timer !== null) {
      window.clearTimeout(oneClickExecution.timer);
      oneClickExecution.timer = null;
    }
  }

  function resetOneClickExecution() {
    const wasPosting = oneClickExecution.posting;
    oneClickExecution.generation += 1;
    cancelOneClickTimer();
    if (oneClickExecution.controller) {
      oneClickExecution.controller.abort();
    }
    oneClickExecution.contextKey = "";
    oneClickExecution.identity = null;
    oneClickExecution.preview = null;
    oneClickExecution.job = null;
    oneClickExecution.previewAttempted = false;
    oneClickExecution.previewBusy = false;
    oneClickExecution.posting = false;
    oneClickExecution.postAttempted = false;
    oneClickExecution.resumePostAttempted = false;
    oneClickExecution.statusBusy = false;
    oneClickExecution.acceptanceCheckBusy = false;
    oneClickExecution.error = "";
    oneClickExecution.statusWarning = "";
    oneClickExecution.failureAction = null;
    oneClickExecution.controller = null;
    oneClickExecution.finalDashboardRefreshed = false;
    if (wasPosting) releaseSubmitting = false;
    resetShopeeGlobalPlanReview();
  }

  function collectboxActionIdentity(data) {
    const identity = oneClickIdentity(data);
    const token = String(data?.release_v1?.plan?.confirmation_token || "");
    if (
      !identity
      || !oneClickDigest(identity.payloadDigest)
      || !oneClickDigest(identity.targetsDigest)
      || !token
    ) return null;
    return {
      ...identity,
      confirmationToken: token,
      key: [
        identity.key,
        identity.payloadDigest,
        identity.targetsDigest,
        token,
      ].join("\u0000"),
    };
  }

  function cancelCollectboxActionTimer() {
    if (collectboxAction.timer !== null) {
      window.clearTimeout(collectboxAction.timer);
      collectboxAction.timer = null;
    }
  }

  function resetCollectboxAction() {
    collectboxAction.generation += 1;
    cancelCollectboxActionTimer();
    if (collectboxAction.controller) collectboxAction.controller.abort();
    collectboxAction.contextKey = "";
    collectboxAction.identity = null;
    collectboxAction.projection = null;
    collectboxAction.previewAttempted = false;
    collectboxAction.previewBusy = false;
    collectboxAction.posting = false;
    collectboxAction.statusBusy = false;
    collectboxAction.error = "";
    collectboxAction.controller = null;
  }

  function collectboxErrorShape(error) {
    return (
      error
      && exactObjectKeys(error, ["category", "code", "detail_digest"])
      && typeof error.category === "string"
      && error.category
      && typeof error.code === "string"
      && error.code
      && oneClickDigest(error.detail_digest)
    );
  }

  function collectboxWriteClass(platform) {
    return platform === "TIKTOK"
      ? "miaoshou:collectbox:claim:tiktok"
      : "miaoshou:collectbox:claim:shopee";
  }

  function collectboxWriteClassAllowed(platform, value) {
    return COLLECTBOX_ALLOWED_WRITE_CLASSES[platform]?.has(value) === true;
  }

  function validateCollectboxPlatform(row, expectedPlatform) {
    if (
      !exactObjectKeys(row, [
        "platform",
        "targets",
        "target_outcomes",
        "status",
        "outcome",
        "attempt_count",
        "retry_allowed",
        "receipt_digest",
        "platform_detail_id_digest",
        "external_writes",
        "error",
      ])
      || row.platform !== expectedPlatform
      || !Array.isArray(row.targets)
      || row.targets.some((target) => (
        !exactObjectKeys(target, ["target_label", "status"])
        || typeof target.target_label !== "string"
        || !target.target_label.startsWith(
          expectedPlatform === "TIKTOK" ? "tiktok:" : "shopee:",
        )
        || !COLLECTBOX_PLATFORM_STATUSES.has(target.status)
      ))
      || new Set(row.targets.map((target) => target.target_label)).size
        !== row.targets.length
      || !Array.isArray(row.target_outcomes)
      || row.target_outcomes.some((target) => (
        !exactObjectKeys(target, [
          "target_label",
          "status",
          "error_code",
          "detail_digest",
        ])
        || typeof target.target_label !== "string"
        || !target.target_label.startsWith(
          expectedPlatform === "TIKTOK" ? "tiktok:" : "shopee:",
        )
        || !COLLECTBOX_TARGET_OUTCOME_STATUSES.has(target.status)
        || (
          target.status === "FAILED"
            ? (
              typeof target.error_code !== "string"
              || !target.error_code
              || !oneClickDigest(target.detail_digest)
            )
            : target.error_code !== null || target.detail_digest !== null
        )
      ))
      || new Set(
        row.target_outcomes.map((target) => target.target_label),
      ).size !== row.target_outcomes.length
      || !COLLECTBOX_PLATFORM_STATUSES.has(row.status)
      || !Number.isInteger(row.attempt_count)
      || row.attempt_count < 0
      || typeof row.retry_allowed !== "boolean"
      || ![null, "IMPORTED", "ALREADY_PRESENT"].includes(row.outcome)
      || !(row.receipt_digest === null || oneClickDigest(row.receipt_digest))
      || !(
        row.platform_detail_id_digest === null
        || oneClickDigest(row.platform_detail_id_digest)
      )
      || !exactObjectKeys(row.external_writes, ["count", "classes"])
      || !(
        row.external_writes.count === null
        || (
          Number.isInteger(row.external_writes.count)
          && row.external_writes.count >= 0
        )
      )
      || !Array.isArray(row.external_writes.classes)
      || row.external_writes.classes.some((value) => (
        typeof value !== "string" || !value
      ))
      || new Set(row.external_writes.classes).size
        !== row.external_writes.classes.length
      || !(row.error === null || collectboxErrorShape(row.error))
    ) {
      throw oneClickContractError(
        "妙手采集箱平台状态不完整，请刷新后重试。",
      );
    }
    const selectedTargetOrder = row.targets.map(
      (target) => target.target_label,
    );
    let previousTargetIndex = -1;
    for (const outcome of row.target_outcomes) {
      const currentTargetIndex = selectedTargetOrder.indexOf(
        outcome.target_label,
      );
      if (currentTargetIndex <= previousTargetIndex) {
        throw oneClickContractError(
          "妙手采集箱逐站结果顺序不完整，请刷新后重试。",
        );
      }
      previousTargetIndex = currentTargetIndex;
    }
    const writeClass = collectboxWriteClass(expectedPlatform);
    const successExact = row.status === "SUCCEEDED" && (
      row.outcome === "IMPORTED"
        ? (
          Number.isInteger(row.external_writes.count)
          && row.external_writes.count > 0
          && row.external_writes.count
            >= row.external_writes.classes.length
          && row.external_writes.classes.every(
            (value) => collectboxWriteClassAllowed(
              expectedPlatform,
              value,
            ),
          )
        )
        : (
          row.outcome === "ALREADY_PRESENT"
          && row.external_writes.count === 0
          && row.external_writes.classes.length === 0
        )
    );
    const failedExact = row.status === "FAILED_RETRYABLE" && (
      row.outcome === null
      && row.retry_allowed === true
      && row.error !== null
      && oneClickDigest(row.receipt_digest)
      && row.platform_detail_id_digest === null
      && row.external_writes.count === 0
      && row.external_writes.classes.length === 0
    );
    const reconciliationExact = row.status === "RECONCILIATION_REQUIRED" && (
      row.outcome === null
      && row.retry_allowed === false
      && row.error !== null
      && oneClickDigest(row.receipt_digest)
      && row.platform_detail_id_digest === null
      && row.external_writes.classes.every(
        (value) => collectboxWriteClassAllowed(expectedPlatform, value),
      )
    );
    const pendingExact = ["PENDING", "RUNNING"].includes(row.status) && (
      row.outcome === null
      && row.retry_allowed === false
      && row.error === null
      && row.receipt_digest === null
      && row.platform_detail_id_digest === null
    );
    if (
      !(successExact || failedExact || reconciliationExact || pendingExact)
      || (row.status === "SUCCEEDED" && row.error !== null)
    ) {
      throw oneClickContractError(
        "妙手采集箱平台结果互相矛盾，请刷新后重试。",
      );
    }
    return row;
  }

  function validateCollectboxProjection(payload, identity) {
    if (
      !exactObjectKeys(payload, [
        "schema_version",
        "ok",
        "persisted",
        "approved_plan",
        "action",
        "external_writes_performed",
        "external_write_count",
        "canonical_next_action",
      ])
      || payload.schema_version !== COLLECTBOX_ACTION_SCHEMA
      || payload.ok !== true
      || typeof payload.persisted !== "boolean"
      || !exactObjectKeys(payload.approved_plan, [
        "plan_id",
        "product_revision",
        "payload_digest",
        "targets_digest",
      ])
      || payload.approved_plan.plan_id !== identity.planId
      || payload.approved_plan.product_revision !== identity.revision
      || payload.approved_plan.payload_digest !== identity.payloadDigest
      || payload.approved_plan.targets_digest !== identity.targetsDigest
      || !Array.isArray(payload.external_writes_performed)
      || payload.external_writes_performed.some((value) => (
        typeof value !== "string" || !value
      ))
      || new Set(payload.external_writes_performed).size
        !== payload.external_writes_performed.length
      || !(
        payload.external_write_count === null
        || (
          Number.isInteger(payload.external_write_count)
          && payload.external_write_count >= 0
        )
      )
      || !exactObjectKeys(payload.action, [
        "action_id",
        "status",
        "start_allowed",
        "retry_allowed",
        "terminal",
        "platforms",
        "error",
      ])
      || !COLLECTBOX_ACTION_STATUSES.has(payload.action.status)
      || !(
        payload.action.action_id === null
        || (
          typeof payload.action.action_id === "string"
          && payload.action.action_id
        )
      )
      || typeof payload.action.start_allowed !== "boolean"
      || typeof payload.action.retry_allowed !== "boolean"
      || typeof payload.action.terminal !== "boolean"
      || !(payload.action.error === null
        || collectboxErrorShape(payload.action.error))
      || !Array.isArray(payload.action.platforms)
      || payload.action.platforms.length !== 2
    ) {
      throw oneClickContractError(
        "妙手采集箱状态合同不完整，请刷新后重试。",
      );
    }
    const platforms = [
      validateCollectboxPlatform(payload.action.platforms[0], "TIKTOK"),
      validateCollectboxPlatform(payload.action.platforms[1], "SHOPEE"),
    ];
    const status = payload.action.status;
    const terminalPartial = status === "PARTIAL_FAILED" && (
      payload.action.start_allowed === true
      && payload.action.retry_allowed === false
      && payload.action.terminal === true
      && platforms.some((row) => [
        "FAILED_RETRYABLE",
        "RECONCILIATION_REQUIRED",
      ].includes(row.status))
      && platforms.every((row) => (
        row.status === "SUCCEEDED"
        || row.status === "FAILED_RETRYABLE"
        || row.status === "RECONCILIATION_REQUIRED"
        || row.status === "PENDING"
      ))
    );
    const actionExact = (
      status === "READY"
        ? (
          payload.persisted === false
          && payload.action.action_id === null
          && payload.action.start_allowed === true
          && payload.action.retry_allowed === false
          && payload.action.terminal === false
          && payload.action.error === null
        )
        : status === "RUNNING"
          ? (
            payload.persisted === true
            && typeof payload.action.action_id === "string"
            && payload.action.start_allowed === false
            && payload.action.retry_allowed === false
            && payload.action.terminal === false
            && payload.action.error === null
          )
          : status === "PARTIAL_FAILED"
            ? (
              payload.persisted === true
              && typeof payload.action.action_id === "string"
              && payload.action.error === null
              && terminalPartial
            )
            : status === "SUCCEEDED"
              ? (
                payload.persisted === true
                && typeof payload.action.action_id === "string"
                && payload.action.start_allowed === true
                && payload.action.retry_allowed === false
                && payload.action.terminal === true
                && payload.action.error === null
                && platforms.every((row) => row.status === "SUCCEEDED")
              )
              : (
                payload.persisted === true
                && typeof payload.action.action_id === "string"
                && payload.action.start_allowed === false
                && payload.action.retry_allowed === false
                && payload.action.terminal === true
                && collectboxErrorShape(payload.action.error)
                && platforms.every((row) => row.status === "PENDING")
              )
    );
    const expectedAction = status === "READY"
      ? "start_collectbox_action"
      : status === "RUNNING"
        ? "read_collectbox_status"
        : ["SUCCEEDED", "PARTIAL_FAILED"].includes(status)
          ? "restart_collectbox_action"
          : null;
    if (
      !actionExact
      || (
        expectedAction === null
          ? payload.canonical_next_action !== null
          : !exactObjectKeys(
            payload.canonical_next_action,
            ["action", "target_focus"],
          )
            || payload.canonical_next_action.action !== expectedAction
            || payload.canonical_next_action.target_focus !== null
      )
    ) {
      throw oneClickContractError(
        "妙手采集箱下一步状态不一致，请刷新后重试。",
      );
    }
    const platformClasses = platforms.flatMap(
      (row) => row.external_writes.classes,
    );
    const platformCountUnknown = platforms.some(
      (row) => row.external_writes.count === null,
    );
    const platformCount = platformCountUnknown
      ? null
      : platforms.reduce(
        (total, row) => total + row.external_writes.count,
        0,
      );
    if (
      JSON.stringify(payload.external_writes_performed)
        !== JSON.stringify(platformClasses)
      || payload.external_write_count !== platformCount
    ) {
      throw oneClickContractError(
        "妙手采集箱写入证据不一致，请刷新后重试。",
      );
    }
    return payload;
  }

  function collectboxActionErrorText(error) {
    const code = String(error?.code || "");
    if (code === "approved_plan_identity_mismatch") {
      return "批准计划身份不一致，请刷新页面后重新核对计划。";
    }
    if (code.includes("shopee")) return "Shopee 导入失败，可重试。";
    if (code.includes("tiktok")) return "TikTok 导入失败，可重试。";
    return "妙手采集箱状态暂不可用，请刷新后重试。";
  }

  function collectboxPlatformState(row) {
    if (row.status === "SUCCEEDED") {
      return row.outcome === "ALREADY_PRESENT" ? "已存在" : "已导入";
    }
    if (row.status === "RUNNING") return "正在导入";
    if (row.status === "FAILED_RETRYABLE") return "失败，可重试";
    if (row.status === "RECONCILIATION_REQUIRED") {
      return "本批次结果待确认；可重新导入并创建新批次";
    }
    return "等待导入";
  }

  function collectboxTargetOutcomeState(row) {
    if (row.status === "SUCCEEDED") return "成功";
    if (row.status === "REPAIRED_SUCCEEDED") return "修正后成功";
    return "失败";
  }

  function collectboxTargetFailureText(row) {
    const messages = {
      collectbox_target_preparation_failed:
        "失败原因：站点草稿未完成，请检查类目、售价和必填项后重新导入。",
      collectbox_target_write_unknown:
        "失败原因：写入结果待确认，请检查妙手中的站点草稿后重新导入。",
    };
    return messages[row.error_code]
      || "失败原因：该站点未完成，请检查妙手中的站点草稿后重新导入。";
  }

  function renderCollectboxTargetOutcomes(row) {
    if (row.platform !== "TIKTOK" || row.target_outcomes.length === 0) {
      return "";
    }
    return `
      <div class="collectbox-target-outcomes" role="list"
        aria-label="TikTok 逐站导入结果">
        ${row.target_outcomes.map((target) => `
          <div class="collectbox-target-outcome ${esc(target.status.toLowerCase())}"
            role="listitem"
            data-collectbox-target-outcome="${esc(target.target_label)}">
            <span>${esc(targetDisplayName(target.target_label))}</span>
            <strong>${esc(collectboxTargetOutcomeState(target))}</strong>
            ${target.status === "FAILED" ? `
              <small>${esc(collectboxTargetFailureText(target))}</small>
            ` : ""}
          </div>
        `).join("")}
      </div>
    `;
  }

  function renderCollectboxAction(data) {
    const panel = $("#collectboxActionPanel");
    const status = $("#collectboxActionStatus");
    const message = $("#collectboxActionMessage");
    const button = $("#collectboxActionButton");
    const approved = Boolean(data?.release_v1?.plan_approved);
    if (!panel || !status || !message || !button) return;
    panel.hidden = !approved;
    if (!approved) {
      status.innerHTML = "";
      return;
    }
    const projection = collectboxAction.projection;
    const busy = collectboxAction.previewBusy
      || collectboxAction.posting
      || collectboxAction.statusBusy;
    if (!projection) {
      status.innerHTML = "";
      button.disabled = true;
      button.textContent = collectboxAction.previewBusy
        ? "正在读取妙手采集箱状态"
        : "导入 TikTok / Shopee 妙手采集箱";
      message.textContent = collectboxAction.error
        || "正在读取 TikTok 与 Shopee 妙手采集箱状态。";
      button.dataset.disabledReason = message.textContent;
      return;
    }
    status.innerHTML = projection.action.platforms.map((row) => {
      const failed = [
        "FAILED_RETRYABLE",
        "RECONCILIATION_REQUIRED",
      ].includes(row.status);
      return `
        <article class="collectbox-platform-state${failed ? " failed" : ""}"
          data-collectbox-platform="${esc(row.platform)}">
          <strong>${row.platform === "TIKTOK" ? "TikTok" : "Shopee"}</strong>
          <span>${esc(collectboxPlatformState(row))}</span>
          ${renderCollectboxTargetOutcomes(row)}
        </article>
      `;
    }).join("");
    if (collectboxAction.posting || projection.action.status === "RUNNING") {
      button.disabled = true;
      button.textContent = "正在导入妙手采集箱";
      message.textContent =
        "正在分别处理 TikTok 与 Shopee；页面只读取同一持久任务状态。";
    } else if (
      projection.action.status === "READY"
      && projection.action.start_allowed
    ) {
      button.disabled = false;
      button.textContent = "导入 TikTok / Shopee 妙手采集箱";
      message.textContent = "点击一次，分别导入 TikTok 与 Shopee 妙手采集箱。";
    } else if (
      ["PARTIAL_FAILED", "SUCCEEDED"].includes(projection.action.status)
      && projection.action.start_allowed
    ) {
      button.disabled = false;
      button.textContent = "重新导入 TikTok / Shopee 妙手采集箱";
      message.textContent =
        "点击后从头创建一个新导入批次；旧草稿保留，妙手中使用最新草稿。";
    } else {
      button.disabled = true;
      button.textContent = "暂不可导入妙手采集箱";
      message.textContent = collectboxActionErrorText(
        projection.action.error,
      );
    }
    if (busy || button.disabled) {
      button.dataset.disabledReason = message.textContent;
    } else {
      delete button.dataset.disabledReason;
    }
  }

  function scheduleCollectboxActionStatus(
    generation,
    delay = COLLECTBOX_ACTION_POLL_INTERVAL_MS,
  ) {
    cancelCollectboxActionTimer();
    if (
      generation !== collectboxAction.generation
      || collectboxAction.projection?.action?.status !== "RUNNING"
    ) return;
    collectboxAction.timer = window.setTimeout(
      () => pollCollectboxActionStatus(generation),
      delay,
    );
  }

  async function requestCollectboxActionPreview(generation) {
    if (
      generation !== collectboxAction.generation
      || collectboxAction.previewBusy
      || collectboxAction.previewAttempted
      || !collectboxAction.identity
    ) return;
    collectboxAction.previewAttempted = true;
    collectboxAction.previewBusy = true;
    collectboxAction.error = "";
    const identity = collectboxAction.identity;
    const controller = new AbortController();
    collectboxAction.controller = controller;
    renderCollectboxAction(currentData);
    updateReleasePrimaryAction(currentData || {});
    try {
      const params = new URLSearchParams({
        offer_id: identity.offerId,
        plan_id: identity.planId,
      });
      const { response, payload } = await boundedJsonFetch(
        `/api/product-workspace/collectbox-action/preview?${params}`,
        {
          headers: { Accept: "application/json" },
          controller,
        },
        ONECLICK_LOCAL_READ_TIMEOUT_MS,
        "妙手采集箱导入预览",
      );
      if (!response.ok || payload.ok === false) {
        throw new Error(
          payload.error?.code || `服务返回 HTTP ${response.status}`,
        );
      }
      const projection = validateCollectboxProjection(payload, identity);
      if (generation !== collectboxAction.generation) return;
      collectboxAction.projection = projection;
      if (projection.action.status === "RUNNING") {
        scheduleCollectboxActionStatus(generation, 0);
      }
    } catch (error) {
      if (
        error.name === "AbortError"
        || generation !== collectboxAction.generation
      ) return;
      collectboxAction.error = friendlyError(error.message);
    } finally {
      if (generation === collectboxAction.generation) {
        collectboxAction.previewBusy = false;
        if (collectboxAction.controller === controller) {
          collectboxAction.controller = null;
        }
        renderCollectboxAction(currentData);
        updateReleasePrimaryAction(currentData || {});
      }
    }
  }

  async function pollCollectboxActionStatus(generation) {
    if (
      generation !== collectboxAction.generation
      || collectboxAction.statusBusy
      || !collectboxAction.identity
      || !collectboxAction.projection?.action?.action_id
    ) return;
    collectboxAction.statusBusy = true;
    const identity = collectboxAction.identity;
    const controller = new AbortController();
    collectboxAction.controller = controller;
    try {
      const params = new URLSearchParams({
        offer_id: identity.offerId,
        plan_id: identity.planId,
      });
      const { response, payload } = await boundedJsonFetch(
        `/api/product-workspace/collectbox-action/status?${params}`,
        {
          headers: { Accept: "application/json" },
          controller,
        },
        ONECLICK_LOCAL_READ_TIMEOUT_MS,
        "妙手采集箱导入状态",
      );
      if (!response.ok || payload.ok === false) {
        throw new Error(
          payload.error?.code || `服务返回 HTTP ${response.status}`,
        );
      }
      const projection = validateCollectboxProjection(payload, identity);
      if (
        generation !== collectboxAction.generation
        || projection.action.action_id
          !== collectboxAction.projection.action.action_id
      ) {
        if (generation === collectboxAction.generation) {
          throw oneClickContractError(
            "妙手采集箱任务身份已变化，请刷新后重试。",
          );
        }
        return;
      }
      collectboxAction.projection = projection;
      collectboxAction.error = "";
      if (projection.action.status === "RUNNING") {
        scheduleCollectboxActionStatus(generation);
      }
    } catch (error) {
      if (
        error.name === "AbortError"
        || generation !== collectboxAction.generation
      ) return;
      collectboxAction.error =
        `导入状态读取失败：${friendlyError(error.message)}`;
      scheduleCollectboxActionStatus(generation);
    } finally {
      if (generation === collectboxAction.generation) {
        collectboxAction.statusBusy = false;
        if (collectboxAction.controller === controller) {
          collectboxAction.controller = null;
        }
        renderCollectboxAction(currentData);
        updateReleasePrimaryAction(currentData || {});
      }
    }
  }

  function ensureCollectboxAction(data) {
    const identity = collectboxActionIdentity(data);
    if (!identity) {
      if (collectboxAction.contextKey) resetCollectboxAction();
      return;
    }
    if (collectboxAction.contextKey !== identity.key) {
      resetCollectboxAction();
      collectboxAction.contextKey = identity.key;
      collectboxAction.identity = identity;
    } else {
      collectboxAction.identity = identity;
    }
    requestCollectboxActionPreview(collectboxAction.generation);
  }

  async function runCollectboxPrimaryAction() {
    const projection = collectboxAction.projection;
    const identity = collectboxAction.identity;
    const actionName = projection?.canonical_next_action?.action;
    const restarting = actionName === "restart_collectbox_action";
    if (
      !identity
      || !projection
      || collectboxAction.posting
      || projection.action.start_allowed !== true
      || ![
        "start_collectbox_action",
        "restart_collectbox_action",
      ].includes(actionName)
    ) return;
    collectboxAction.posting = true;
    collectboxAction.error = "";
    const generation = collectboxAction.generation;
    const controller = new AbortController();
    collectboxAction.controller = controller;
    renderCollectboxAction(currentData);
    updateReleasePrimaryAction(currentData || {});
    try {
      const { response, payload } = await boundedJsonFetch(
        "/api/product-workspace/collectbox-action/start",
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            confirm_collectbox_action: true,
            approved_by: "Kyle",
            offer_id: identity.offerId,
            plan_id: identity.planId,
            product_revision: identity.revision,
            payload_digest: identity.payloadDigest,
            confirmation_token: identity.confirmationToken,
            targets_digest: identity.targetsDigest,
            ...(restarting ? {
              restart_collectbox_action: true,
              reimport_request_id: window.crypto.randomUUID(),
            } : {}),
          }),
          controller,
        },
        ONECLICK_LOCAL_POST_TIMEOUT_MS,
        "妙手采集箱导入",
      );
      if (!response.ok || payload.ok === false) {
        throw new Error(
          payload.error?.code || `服务返回 HTTP ${response.status}`,
        );
      }
      const next = validateCollectboxProjection(payload, identity);
      if (generation !== collectboxAction.generation) return;
      collectboxAction.projection = next;
      if (next.action.status === "RUNNING") {
        scheduleCollectboxActionStatus(generation, 0);
      }
    } catch (error) {
      if (
        error.name === "AbortError"
        || generation !== collectboxAction.generation
      ) return;
      collectboxAction.error =
        `导入请求失败：${friendlyError(error.message)}`;
    } finally {
      if (generation === collectboxAction.generation) {
        collectboxAction.posting = false;
        if (collectboxAction.controller === controller) {
          collectboxAction.controller = null;
        }
        renderCollectboxAction(currentData);
        updateReleasePrimaryAction(currentData || {});
      }
    }
  }

  function resetShopeeGlobalPlanReview() {
    shopeeGlobalPlanReview.generation += 1;
    if (shopeeGlobalPlanReview.controller) {
      shopeeGlobalPlanReview.controller.abort();
    }
    shopeeGlobalPlanReview.contextKey = "";
    shopeeGlobalPlanReview.candidate = null;
    shopeeGlobalPlanReview.approval = null;
    shopeeGlobalPlanReview.approvalCurrent = false;
    shopeeGlobalPlanReview.previewAttempted = false;
    shopeeGlobalPlanReview.previewBusy = false;
    shopeeGlobalPlanReview.submitting = false;
    shopeeGlobalPlanReview.approvalPostAttempted = false;
    shopeeGlobalPlanReview.reconciliationBusy = false;
    shopeeGlobalPlanReview.error = "";
    shopeeGlobalPlanReview.controller = null;
    resetShopeeCategoryDecisionReview();
  }

  function resetShopeeCategoryDecisionReview() {
    shopeeCategoryDecisionReview.generation += 1;
    if (shopeeCategoryDecisionReview.controller) {
      shopeeCategoryDecisionReview.controller.abort();
    }
    shopeeCategoryDecisionReview.contextKey = "";
    shopeeCategoryDecisionReview.projection = null;
    shopeeCategoryDecisionReview.draftIdentityDigest = "";
    shopeeCategoryDecisionReview.draftBrandIdentityDigest = "";
    shopeeCategoryDecisionReview.draftLocationIdentityDigest = "";
    shopeeCategoryDecisionReview.requiredAttributeSelections = {};
    shopeeCategoryDecisionReview.confirmSelection = false;
    shopeeCategoryDecisionReview.confirmSellerStock = false;
    shopeeCategoryDecisionReview.confirmConditionAndPreorder = false;
    shopeeCategoryDecisionReview.confirmRequiredAttributes = false;
    shopeeCategoryDecisionReview.previewAttempted = false;
    shopeeCategoryDecisionReview.previewBusy = false;
    shopeeCategoryDecisionReview.submitting = false;
    shopeeCategoryDecisionReview.postAttempted = false;
    shopeeCategoryDecisionReview.reconciliationBusy = false;
    shopeeCategoryDecisionReview.error = "";
    shopeeCategoryDecisionReview.message = "";
    shopeeCategoryDecisionReview.controller = null;
  }

  function oneClickContractError(message) {
    const error = new Error(message);
    error.oneClickContractError = true;
    return error;
  }

  function oneClickDigest(value) {
    return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  }

  function oneClickSourceIdentityDigest(value) {
    return oneClickDigest(value)
      || (
        typeof value === "string"
        && /^sha256:[a-f0-9]{64}$/.test(value)
      );
  }

  function oneClickProjectionDigest(key, value) {
    return key === "source_identity"
      ? oneClickSourceIdentityDigest(value)
      : oneClickDigest(value);
  }

  function oneClickPromotionPrerequisite(targetLabel) {
    if (
      typeof targetLabel !== "string"
      || !targetLabel.startsWith("promotion:")
    ) return null;
    const prerequisite = targetLabel.slice("promotion:".length);
    return ONECLICK_POSTPUBLISH_PROMOTION_PREREQUISITES.has(prerequisite)
      ? prerequisite
      : null;
  }

  function exactObjectKeys(value, keys) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const actual = Object.keys(value).sort();
    const expected = [...keys].sort();
    return actual.length === expected.length
      && actual.every((key, index) => key === expected[index]);
  }

  function nullableDigest(value) {
    return value === null || oneClickDigest(value);
  }

  async function boundedJsonFetch(
    path,
    options,
    timeoutMs,
    operationLabel,
  ) {
    const {
      controller = new AbortController(),
      ...fetchOptions
    } = options || {};
    let timedOut = false;
    const timer = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    try {
      const response = await fetch(path, {
        ...fetchOptions,
        signal: controller.signal,
      });
      let payload;
      try {
        payload = await response.json();
      } catch (_error) {
        const error = new Error(
          `${operationLabel}返回内容无法解析；${fetchOptions.method === "POST"
            ? "请求可能已被服务端受理"
            : "未取得可用只读结果"}`,
        );
        error.responseOutcomeUnknown = fetchOptions.method === "POST";
        error.status = response.status;
        throw error;
      }
      return { response, payload, controller };
    } catch (error) {
      if (timedOut) {
        const timeoutError = new Error(
          `${operationLabel}超过 ${Math.round(timeoutMs / 1000)} 秒；${
            fetchOptions.method === "POST"
              ? "请求结果未知，将只读核对，绝不自动重发"
              : "可明确重试只读请求"
          }`,
        );
        timeoutError.requestTimedOut = true;
        timeoutError.responseOutcomeUnknown = fetchOptions.method === "POST";
        throw timeoutError;
      }
      if (
        error.name !== "AbortError"
        && fetchOptions.method === "POST"
        && !Object.hasOwn(error, "responseOutcomeUnknown")
      ) {
        error.responseOutcomeUnknown = true;
      }
      throw error;
    } finally {
      window.clearTimeout(timer);
    }
  }

  function oneClickRuleId(value) {
    return (
      typeof value === "string"
      && value.length > 0
      && value.length <= 120
      && value === value.trim()
      && /^[A-Za-z0-9_.:-]+$/.test(value)
    );
  }

  function validateOneClickObservationWarning(target) {
    const result = target?.result;
    if (
      !target?.target_label?.startsWith("shopee:")
      || target.status !== "SUCCEEDED_MANUAL_REVIEW"
      || target.next_action !== "review_verified_observation_warning"
      || target.next_action_target !== target.target_label
      || target.requires_human !== true
      || target.manual_after_submit !== true
      || !result
      || typeof result !== "object"
      || Array.isArray(result)
      || result.canonical_status !== "SUCCEEDED_MANUAL_REVIEW"
      || result.manual_review !== true
      || result.readback_verified !== true
      || result.submission_accepted !== true
      || result.dispatch_outcome_unknown !== false
      || !Number.isInteger(result.external_write_count)
      || result.external_write_count < 1
      || !Array.isArray(result.external_write_classes)
      || result.external_write_classes.length !== result.external_write_count
      || result.external_write_classes.some((value) => (
        typeof value !== "string" || !value || value !== value.trim()
      ))
      || !oneClickDigest(result.evidence_digest)
      || !Array.isArray(result.rule_ids)
      || result.rule_ids.length < 1
      || result.rule_ids.some((value) => !oneClickRuleId(value))
      || new Set(result.rule_ids).size !== result.rule_ids.length
      || !Array.isArray(result.observation_digests)
      || result.observation_digests.length < 1
      || result.observation_digests.some((value) => !oneClickDigest(value))
      || new Set(result.observation_digests).size
        !== result.observation_digests.length
    ) {
      throw oneClickContractError(
        "Shopee 观察警告验收证据不完整，已停止人工结案。",
      );
    }
  }

  function sameSortedValues(left, right) {
    const leftValues = [...left].sort();
    const rightValues = [...right].sort();
    return (
      leftValues.length === rightValues.length
      && leftValues.every((value, index) => value === rightValues[index])
    );
  }

  function validateOneClickDispatchLedger(target, isStatus) {
    if (!isStatus) {
      if (
        Object.hasOwn(target, "dispatch_ledger")
        || Object.hasOwn(target, "dispatch_count")
      ) {
        throw oneClickContractError(
          "只读预览不应携带持久执行账本，已停止提交。",
        );
      }
      return;
    }
    const ledger = target.dispatch_ledger;
    const classes = ledger?.cumulative_external_write_classes;
    const exact = ledger?.cumulative_external_write_count;
    const lower = ledger?.confirmed_external_write_count_lower_bound;
    const upper = ledger?.possible_external_write_count_upper_bound;
    if (
      !Number.isInteger(target.dispatch_count)
      || target.dispatch_count < 0
      || !exactObjectKeys(ledger, [
        "stage",
        "cumulative_external_write_count",
        "cumulative_external_write_classes",
        "confirmed_external_write_count_lower_bound",
        "possible_external_write_count_upper_bound",
        "digest",
        "stage_evidence_digest",
        "pending_write_intent_digest",
      ])
      || (
        ledger.stage !== null
        && (
          typeof ledger.stage !== "string"
          || !ledger.stage
          || ledger.stage !== ledger.stage.trim()
        )
      )
      || !Array.isArray(classes)
      || classes.some((value) => (
        typeof value !== "string" || !value || value !== value.trim()
      ))
      || new Set(classes).size !== classes.length
      || !(exact === null || (Number.isInteger(exact) && exact >= 0))
      || !Number.isInteger(lower)
      || lower < 0
      || !(upper === null || (Number.isInteger(upper) && upper >= lower))
      || (exact !== null && (exact !== lower || exact !== upper))
      || !nullableDigest(ledger.digest)
      || !nullableDigest(ledger.stage_evidence_digest)
      || !nullableDigest(ledger.pending_write_intent_digest)
      || (
        classes.includes("UNKNOWN")
          ? exact !== null
          : (
            exact === null
            && target.status !== "DISPATCHING"
            && target.status !== "RECONCILIATION_REQUIRED"
          )
      )
    ) {
      throw oneClickContractError(
        "统一发布写入次数账本不完整，已停止提交。",
      );
    }
  }

  function validateOneClickProjection(projection, identity, schema, reference = null) {
    if (!projection || typeof projection !== "object" || Array.isArray(projection)) {
      throw oneClickContractError("统一发布控制面返回了无效状态。");
    }
    if (projection.schema_version !== schema) {
      throw oneClickContractError("统一发布控制面版本不匹配，请刷新后重试。");
    }
    const isStatus = schema === ONECLICK_STATUS_SCHEMA;
    const isPreview = schema === ONECLICK_PREVIEW_SCHEMA;
    const isUnpreparedStatus = (
      isStatus
      && ["PENDING", "PREPARING"].includes(projection.phase)
    );
    if (
      projection.plan_id !== identity.planId
      || projection.product_revision !== identity.revision
      || typeof projection.run_id !== "string"
      || !projection.run_id
      || !Array.isArray(projection.targets)
      || !Array.isArray(projection.shared_controls)
      || (
        isStatus
        && (
          typeof projection.job_id !== "string"
          || !projection.job_id
          || !ONECLICK_JOB_PHASES.has(projection.phase)
        )
      )
    ) {
      throw oneClickContractError("统一发布控制面身份已漂移，已停止提交。");
    }
    const digests = projection.digests;
    if (
      !digests
      || typeof digests !== "object"
      || Array.isArray(digests)
      || !ONECLICK_DIGEST_KEYS.every((key) => (
        Object.hasOwn(digests, key)
          && oneClickProjectionDigest(key, digests[key])
      ))
    ) {
      throw oneClickContractError("统一发布控制面缺少不可变摘要，已停止提交。");
    }
    if (
      (identity.payloadDigest && digests.payload !== identity.payloadDigest)
      || (identity.targetsDigest && digests.targets !== identity.targetsDigest)
      || (
        reference
        && (
          projection.run_id !== reference.run_id
          || !ONECLICK_DIGEST_KEYS.every((key) => (
            digests[key] === reference.digests?.[key]
          ))
        )
      )
    ) {
      throw oneClickContractError("统一发布控制面摘要已漂移，已停止提交。");
    }
    const labels = new Set();
    const targetLabels = new Set();
    const sharedControlLabels = new Set();
    const projectedRows = [
      ...projection.targets.map((target) => ({
        target,
        sharedControl: false,
      })),
      ...projection.shared_controls.map((target) => ({
        target,
        sharedControl: true,
      })),
    ];
    for (const { target, sharedControl } of projectedRows) {
      const targetDigests = target?.digests;
      const dependency = target?.dependency;
      const reason = target?.reason;
      const promotionPrerequisite = oneClickPromotionPrerequisite(
        target?.target_label,
      );
      const deferredPostpublishAction = Boolean(
        isStatus
        && promotionPrerequisite !== null
        && target?.status === "PENDING"
        && target?.classification === null
        && target?.control_target === false
        && target?.storefront === false
        && target?.runnable_now === false
      );
      const completedCommonControl = Boolean(
        isStatus
        && target?.target_label === "miaoshou:COMMON"
        && target?.status === "SUCCEEDED"
        && target?.classification === null
        && target?.runnable_now === false
        && target?.manual_after_submit === false
        && target?.requires_human === false
        && targetDigests?.prepared_command === null
        && targetDigests?.proof === null
      );
      if (
        !target
        || typeof target !== "object"
        || Array.isArray(target)
        || typeof target.target_label !== "string"
        || !target.target_label
        || labels.has(target.target_label)
        || typeof target.control_target !== "boolean"
        || target.control_target !== sharedControl
        || !ONECLICK_TARGET_STATUSES.has(target.status)
        || (
          isUnpreparedStatus
            || deferredPostpublishAction
            || completedCommonControl
            ? target.classification !== null
            : !ONECLICK_CLASSIFICATIONS.has(target.classification)
        )
        || typeof target.runnable_now !== "boolean"
        || typeof target.manual_after_submit !== "boolean"
        || typeof target.requires_human !== "boolean"
        || target.requires_human !== [
          "SUCCEEDED_MANUAL_REVIEW",
          "SUBMITTED_UNVERIFIED",
        ].includes(target.status)
        || (
          ["SUCCEEDED_MANUAL_REVIEW", "SUBMITTED_UNVERIFIED"]
            .includes(target.status)
          && target.manual_after_submit !== true
        )
        || typeof target.storefront !== "boolean"
        || (
          sharedControl
            ? (
              target.target_label !== SHOPEE_GLOBAL_CONTROL_TARGET
              || target.storefront !== false
            )
            : (
              target.target_label === "miaoshou:COMMON"
                || promotionPrerequisite !== null
                ? target.storefront !== false
                : target.storefront !== true
            )
        )
        || !dependency
        || typeof dependency !== "object"
        || Array.isArray(dependency)
        || dependency.policy_version !== ONECLICK_DEPENDENCY_POLICY_VERSION
        || !["SATISFIED", "WAITING", "BLOCKED"].includes(dependency.state)
        || typeof dependency.satisfied !== "boolean"
        || (
          dependency.prerequisite_target !== null
          && (
            typeof dependency.prerequisite_target !== "string"
            || !dependency.prerequisite_target
          )
        )
        || (
          dependency.prerequisite_status !== null
          && (
            typeof dependency.prerequisite_status !== "string"
            || !dependency.prerequisite_status
          )
        )
        || (
          dependency.state === "SATISFIED"
            ? dependency.satisfied !== true
            : dependency.satisfied !== false
        )
        || (
          target.next_action !== null
          && !ONECLICK_ACTIONS.has(target.next_action)
        )
        || (
          target.next_action_target !== null
          && typeof target.next_action_target !== "string"
        )
        || !targetDigests
        || typeof targetDigests !== "object"
        || Array.isArray(targetDigests)
        || !ONECLICK_TARGET_DIGEST_KEYS.every((key) => (
          Object.hasOwn(targetDigests, key)
        ))
        || !oneClickDigest(targetDigests.adapter_policy)
        || !nullableDigest(targetDigests.shared_resource)
        || !nullableDigest(targetDigests.shared_resource_context)
        || !(
          (
            targetDigests.prepared_command === null
            && targetDigests.proof === null
          )
          || (
            oneClickDigest(targetDigests.prepared_command)
            && oneClickDigest(targetDigests.proof)
          )
        )
        || (
          isUnpreparedStatus
          && (
            targetDigests.prepared_command !== null
            || targetDigests.proof !== null
          )
        )
        || (
          reason !== null
          && (
            !reason
            || typeof reason !== "object"
            || Array.isArray(reason)
            || !ONECLICK_REASON_CATEGORIES.has(reason.category)
            || !["TARGET", "SYSTEMIC_IDENTITY"].includes(reason.scope)
            || typeof reason.code !== "string"
            || !reason.code
            || typeof reason.summary_code !== "string"
            || !reason.summary_code
            || !oneClickDigest(reason.detail_digest)
          )
        )
      ) {
        throw oneClickContractError(
          "统一发布控制面店铺状态不完整，已停止提交。",
        );
      }
      if (target.status === "SUCCEEDED_MANUAL_REVIEW") {
        validateOneClickObservationWarning(target);
      } else if (
        target.next_action === "review_verified_observation_warning"
        || target.result?.canonical_status === "SUCCEEDED_MANUAL_REVIEW"
      ) {
        throw oneClickContractError(
          "Shopee 观察警告状态与验收动作不一致，已停止人工结案。",
        );
      }
      validateOneClickDispatchLedger(target, isStatus);
      labels.add(target.target_label);
      (sharedControl ? sharedControlLabels : targetLabels).add(
        target.target_label,
      );
    }
    if (
      sharedControlLabels.size !== projection.shared_controls.length
      || targetLabels.size !== projection.targets.length
      || (
        projection.shared_controls.length > 0
        && (
          projection.shared_controls.length !== 1
          || !sharedControlLabels.has(SHOPEE_GLOBAL_CONTROL_TARGET)
        )
      )
    ) {
      throw oneClickContractError(
        "Shopee Global 共享控制身份不完整，已停止提交。",
      );
    }
    const promotionRows = projection.targets.filter((target) => (
      oneClickPromotionPrerequisite(target.target_label) !== null
    ));
    if (
      !Array.isArray(projection.postpublish_actions)
      || projection.postpublish_actions.length !== promotionRows.length
      || projection.postpublish_actions.some((action, index) => (
        JSON.stringify(action) !== JSON.stringify(promotionRows[index])
      ))
    ) {
      throw oneClickContractError(
        "统一发布控制面的发布后动作身份不完整，已停止提交。",
      );
    }
    if (reference) {
      const previousTargets = new Map(
        [
          ...(reference.targets || []),
          ...(reference.shared_controls || []),
        ].map((target) => [
          target.target_label,
          target,
        ]),
      );
      if (previousTargets.size !== projectedRows.length) {
        throw oneClickContractError(
          "统一发布控制面的目标集合已漂移，已停止提交。",
        );
      }
      for (const { target } of projectedRows) {
        const previous = previousTargets.get(target.target_label);
        if (
          !previous
          || previous.digests?.adapter_policy
            !== target.digests.adapter_policy
          || (
            reference.schema_version === ONECLICK_STATUS_SCHEMA
            && previous.digests?.prepared_command !== null
            && (
              previous.digests.prepared_command
                !== target.digests.prepared_command
              || previous.digests.proof !== target.digests.proof
            )
          )
        ) {
          throw oneClickContractError(
            "统一发布控制面的目标证明已漂移，已停止提交。",
          );
        }
      }
    }
    for (const { target } of projectedRows) {
      const dependency = target.dependency;
      const dependencyExact = (
        dependency.state === "SATISFIED"
        && dependency.satisfied === true
        && dependency.prerequisite_target === null
        && dependency.prerequisite_status === null
      );
      const prerequisiteSummaryExact = dependency.prerequisite === undefined;
      if (!dependencyExact) {
        throw oneClickContractError(
          "统一发布控制面的店铺依赖证据不一致，已停止提交。",
        );
      }
      if (!prerequisiteSummaryExact) {
        throw oneClickContractError(
          "统一发布控制面的前置目标摘要不一致，已停止提交。",
        );
      }
      if (
        target.next_action_target
        && !labels.has(target.next_action_target)
      ) {
        throw oneClickContractError(
          "统一发布控制面的下一步目标无效，已停止提交。",
        );
      }
    }
    const storefronts = projection.targets.filter((target) => target.storefront);
    const runnable = storefronts.filter((target) => target.runnable_now);
    const preparePending = storefronts
      .filter((target) => target.classification === "PREPARE_PENDING");
    const controlRows = [
      ...projection.targets.filter((target) => !target.storefront),
      ...projection.shared_controls,
    ];
    const storefrontLabels = new Set(
      storefronts.map((target) => target.target_label),
    );
    const summary = projection.summary;
    const capability = projection.dispatch_capability;
    if (
      !Number.isInteger(projection.storefront_count)
      || projection.storefront_count !== storefronts.length
      || !Number.isInteger(projection.control_row_count)
      || projection.control_row_count
        !== controlRows.length
      || !Number.isInteger(projection.runnable_target_count)
      || projection.runnable_target_count !== runnable.length
      || (
        isPreview
        && (
          !Number.isInteger(projection.preparation_pending_count)
          || projection.preparation_pending_count !== preparePending.length
          || !Array.isArray(projection.prepare_pending)
          || !sameSortedValues(
            projection.prepare_pending,
            preparePending.map((target) => target.target_label),
          )
          || typeof projection.start_allowed !== "boolean"
          || projection.start_allowed !== (
            preparePending.length > 0
            && projection.dispatch_capability?.enabled === true
          )
          || runnable.length !== 0
          || preparePending.some((target) => (
            target.status !== "PENDING"
            || target.runnable_now !== false
            || target.next_action !== "prepare_batch"
          ))
        )
      )
      || !summary
      || typeof summary !== "object"
      || Array.isArray(summary)
      || !["will_dispatch", "manual_after_submit", "blocked", "already_terminal"]
        .every((key) => (
          Array.isArray(summary[key])
          && summary[key].every((label) => storefrontLabels.has(label))
          && new Set(summary[key]).size === summary[key].length
        ))
      || !capability
      || typeof capability !== "object"
      || Array.isArray(capability)
      || capability.schema_version !== "oneclick-dispatch-capability/v1"
      || typeof capability.enabled !== "boolean"
      || typeof capability.source !== "string"
      || !capability.source
      || typeof capability.reason_code !== "string"
      || !capability.reason_code
      || (
        capability.next_action !== null
        && capability.next_action !== "enable_oneclick_dispatch"
      )
    ) {
      throw oneClickContractError(
        "统一发布控制面摘要或计数不一致，已安全停止。",
      );
    }
    const expectedAutomatic = runnable
      .filter((target) => target.classification === "EXACT_READY_AUTOMATIC")
      .map((target) => target.target_label);
    const expectedManual = storefronts
      .filter((target) => (
        (
          target.runnable_now === true
          && target.classification === "READY_SUBMIT_MANUAL"
        )
        || ["SUCCEEDED_MANUAL_REVIEW", "SUBMITTED_UNVERIFIED"]
          .includes(target.status)
      ))
      .map((target) => target.target_label);
    const blockedStatuses = new Set([
      "FAILED_PRE_SUBMIT",
      "RECONCILIATION_REQUIRED",
      "BLOCKED_AUTH",
      "BLOCKED_INVENTORY",
      "BLOCKED_CAPABILITY",
      "BLOCKED_SOURCE_IDENTITY",
      "BLOCKED_SKU_LINEAGE",
    ]);
    const expectedBlocked = storefronts
      .filter((target) => (
        blockedStatuses.has(target.status)
        || target.dependency.state === "BLOCKED"
      ))
      .map((target) => target.target_label);
    const expectedTerminal = storefronts
      .filter((target) => (
        [
          "SUCCEEDED",
          "SUCCEEDED_MANUAL_REVIEW",
          "SUBMITTED_UNVERIFIED",
        ].includes(target.status)
      ))
      .map((target) => target.target_label);
    if (
      !sameSortedValues(summary.will_dispatch, expectedAutomatic)
      || !sameSortedValues(summary.manual_after_submit, expectedManual)
      || !sameSortedValues(summary.blocked, expectedBlocked)
      || !sameSortedValues(summary.already_terminal, expectedTerminal)
    ) {
      throw oneClickContractError(
        "统一发布控制面的分类摘要与店铺状态不一致，已停止提交。",
      );
    }
    const canonical = projection.canonical_next_action;
    if (
      !Object.hasOwn(projection, "canonical_next_action")
      || (
        canonical !== null
        && (
          !canonical
          || typeof canonical !== "object"
          || Array.isArray(canonical)
          || (
            canonical.target_label !== null
            && !labels.has(canonical.target_label)
          )
          || (
            canonical.target_focus !== null
            && !labels.has(canonical.target_focus)
          )
          || !ONECLICK_TARGET_STATUSES.has(canonical.canonical_status)
          || !ONECLICK_ACTIONS.has(canonical.action)
          || typeof canonical.runnable !== "boolean"
        )
      )
    ) {
      throw oneClickContractError(
        "统一发布控制面缺少服务端唯一下一步，已停止提交。",
      );
    }
    if (
      isPreview
      && projection.start_allowed
      && canonical?.action === "prepare_batch"
      && (
        canonical.canonical_status !== "PENDING"
        || canonical.runnable !== false
      )
    ) {
      throw oneClickContractError(
        "统一发布准备入口与服务端唯一下一步不一致，已停止提交。",
      );
    }
    return projection;
  }

  function oneClickActionText(action) {
    const labels = {
      prepare_batch: "等待服务端准备本批次",
      wait_for_preparation: "等待批次准备完成",
      wait_for_worker: "等待后台执行",
      wait_for_dispatch_receipt: "等待渠道回执",
      wait_for_dependency: "等待前置目标完成",
      resolve_prerequisite_target: "处理前置目标",
      verify_submission_in_marketplace: "前往人工验收",
      review_verified_observation_warning: "验收 Shopee 观察警告",
      retry_exact_zero_write_action: "修复后安全重试",
      reconcile_before_any_retry: "先完成只读对账",
      restore_channel_authorization: "恢复渠道授权",
      approve_sellable_inventory: "批准可售库存",
      review_approved_content_facts: "复核已批准的内容与类目事实",
      review_logistics_policy: "复核物流策略",
      review_shopee_global_plan: "审核 Shopee Global 计划",
      wait_for_channel_capability: "等待渠道能力开放",
      resolve_source_product_identity: "修复来源商品身份",
      resolve_predecessor_sku_lineage: "修复 Seller SKU 血缘",
      perform_governed_safe_action: "执行受治理安全动作",
      enable_oneclick_dispatch: "启用统一发布执行能力",
      refresh_release_state: "重新读取发布状态",
      resolve_plan_or_source_identity: "修复计划或来源身份",
    };
    return labels[String(action || "")] || "查看服务端唯一下一步";
  }

  function oneClickStatusText(status) {
    const labels = {
      PENDING: "等待准备",
      PREPARING: "准备中",
      READY: "可执行",
      DISPATCHING: "正在提交到妙手",
      SUCCEEDED: "妙手已接受提交",
      SUCCEEDED_MANUAL_REVIEW: "妙手已接受提交",
      SUBMITTED_UNVERIFIED: "妙手已接受提交",
      FAILED_PRE_SUBMIT: "上次发布失败，可再次发布",
      RECONCILIATION_REQUIRED: "上次结果未确认，可再次发布",
      BLOCKED_AUTH: "上次发布失败，可再次发布",
      BLOCKED_INVENTORY: "上次发布失败，可再次发布",
      BLOCKED_CAPABILITY: "上次发布失败，可再次发布",
      BLOCKED_SOURCE_IDENTITY: "上次发布失败，可再次发布",
      BLOCKED_SKU_LINEAGE: "上次发布失败，可再次发布",
    };
    return labels[String(status || "")] || "状态由服务端核定";
  }

  function shopeeGlobalWriteCountText(control) {
    const ledger = control?.dispatch_ledger;
    if (!ledger) return "尚未进入持久执行";
    const exact = ledger.cumulative_external_write_count;
    const lower = ledger.confirmed_external_write_count_lower_bound;
    const upper = ledger.possible_external_write_count_upper_bound;
    if (Number.isInteger(exact)) {
      return `已确认 ${exact} 次外部写入`;
    }
    const upperText = Number.isInteger(upper) ? String(upper) : "未知";
    return `结果未知 · 已确认至少 ${lower} 次，最多 ${upperText} 次`;
  }

  function compactDigest(value) {
    return oneClickDigest(value)
      ? `${value.slice(0, 10)}…${value.slice(-8)}`
      : "不可用";
  }

  function oneClickReasonText(target) {
    if (
      ["SUCCEEDED", "SUCCEEDED_MANUAL_REVIEW", "SUBMITTED_UNVERIFIED"]
        .includes(String(target?.status || ""))
    ) {
      return "妙手已接受该店铺的提交；不等待平台官方回读。";
    }
    if (String(target?.status || "") === "DISPATCHING") {
      return "妙手 API 正在处理该店铺。";
    }
    return "该店铺上次未完成；点击上方按钮可重新发起妙手发布。";
  }

  function oneClickTargetBucket(target) {
    const status = String(target?.status || "");
    const classification = String(target?.classification || "");
    const dependency = String(target?.dependency?.state || "");
    if (status === "SUCCEEDED") return "terminal";
    if (status === "SUCCEEDED_MANUAL_REVIEW") return "manual";
    if (status === "SUBMITTED_UNVERIFIED") return "manual";
    if (status === "RECONCILIATION_REQUIRED") return "reconciliation";
    if (status === "FAILED_PRE_SUBMIT" || classification === "SAFE_ACTION_REQUIRED") {
      return "preSubmit";
    }
    if (dependency === "WAITING" || dependency === "BLOCKED") return "dependency";
    if (
      target?.runnable_now === true
      || ["PENDING", "PREPARING", "READY", "DISPATCHING"].includes(status)
    ) {
      return classification === "READY_SUBMIT_MANUAL" ? "manual" : "automatic";
    }
    return "blocked";
  }

  function oneClickProjection() {
    return oneClickExecution.job || oneClickExecution.preview;
  }

  function currentOneClickNextAction(data) {
    const release = data?.release_v1 || {};
    const projection = oneClickProjection();
    if (
      !oneClickExecution.job
      && (
        release.canonical_next_action?.action
          === "review_shopee_global_plan"
        || shopeeGlobalPlanReviewRequired(data, projection)
      )
    ) {
      return {
        target_label: SHOPEE_GLOBAL_CONTROL_TARGET,
        target_focus: SHOPEE_GLOBAL_CONTROL_TARGET,
        canonical_status: "BLOCKED_CAPABILITY",
        action: "review_shopee_global_plan",
        runnable: false,
      };
    }
    const action = oneClickExecution.failureAction
      || projection?.canonical_next_action
      || release.canonical_next_action;
    return action && typeof action === "object" && !Array.isArray(action)
      ? action
      : null;
  }

  function oneClickObservationWarningForm(target) {
    if (target?.status !== "SUCCEEDED_MANUAL_REVIEW") return "";
    const result = target.result;
    const observationDigest = [...result.observation_digests].sort()[0];
    const ruleIds = [...result.rule_ids].sort();
    return `
      <form class="manual-verification-form oneclick-observation-review-form"
        data-oneclick-observation-review="${esc(target.target_label)}"
        data-observation-evidence-digest="${esc(observationDigest)}">
        <p><strong>官方硬事实已验证</strong></p>
        <p>存在平台派生翻译/图片观察警告，等待Kyle人工验收。</p>
        <p>脱敏警告规则：${ruleIds.map((ruleId) => esc(ruleId)).join("、")}</p>
        <label class="manual-verification-confirm">
          <input name="manual_review_accepted" type="checkbox" required>
          <span>我已查看上述平台派生观察警告，并确认接受本次官方回读结果；此操作只结案，不会重新发布或重试。</span>
        </label>
        <button class="button button-secondary" type="submit" disabled>
          记录 Kyle 观察警告验收
        </button>
        <span class="manual-verification-message" role="status" aria-live="polite"></span>
      </form>
    `;
  }

  function validateShopeeGlobalPlanCandidate(candidate) {
    const observerFailureKeys = [
      "schema_version",
      "status",
      "planning_allowed",
      "reason_category",
      "reason_code",
      "blocker_codes",
    ];
    if (
      exactObjectKeys(candidate, observerFailureKeys)
      && candidate.schema_version === SHOPEE_GLOBAL_PLAN_CANDIDATE_SCHEMA
      && ["BLOCKED_AUTH", "BLOCKED_CAPABILITY"].includes(candidate.status)
      && candidate.planning_allowed === false
      && (
        (
          candidate.status === "BLOCKED_AUTH"
          && candidate.reason_category === "AUTH"
        )
        || (
          candidate.status === "BLOCKED_CAPABILITY"
          && candidate.reason_category === "CAPABILITY"
        )
      )
      && oneClickRuleId(candidate.reason_code)
      && Array.isArray(candidate.blocker_codes)
      && candidate.blocker_codes.length === 1
      && candidate.blocker_codes[0] === candidate.reason_code
    ) {
      return candidate;
    }
    const candidateKeys = [
      "schema_version",
      "status",
      "planning_allowed",
      "mode",
      "observation_authority",
      "observation_schema_version",
      "checks",
      "counts",
      "digests",
      "blocker_codes",
    ];
    const checkKeys = [
      "official_authority_exact",
      "audited_schema_exact",
      "attributes_complete",
      "variations_complete",
      "no_default_execution_fact",
    ];
    if (
      !exactObjectKeys(candidate, candidateKeys)
      || candidate.schema_version !== SHOPEE_GLOBAL_PLAN_CANDIDATE_SCHEMA
      || !["READY", "BLOCKED_CAPABILITY"].includes(candidate.status)
      || typeof candidate.planning_allowed !== "boolean"
      || !(candidate.mode === null || ["NEW_GLOBAL", "EXISTING_GLOBAL"]
        .includes(candidate.mode))
      || typeof candidate.observation_authority !== "string"
      || !candidate.observation_authority
      || typeof candidate.observation_schema_version !== "string"
      || !candidate.observation_schema_version
      || !exactObjectKeys(candidate.checks, checkKeys)
      || checkKeys.some((key) => typeof candidate.checks[key] !== "boolean")
      || !Array.isArray(candidate.blocker_codes)
      || candidate.blocker_codes.some((code) => !oneClickRuleId(code))
      || new Set(candidate.blocker_codes).size !== candidate.blocker_codes.length
      || candidate.blocker_codes.some((code, index) => (
        index > 0 && candidate.blocker_codes[index - 1] >= code
      ))
    ) {
      throw oneClickContractError(
        "Shopee Global 候选合同不完整，已停止审批。",
      );
    }
    const ready = candidate.status === "READY";
    const expectedDigestKeys = ready
      ? SHOPEE_GLOBAL_PLAN_DIGEST_KEYS
      : ["observation_evidence_digest", "candidate_digest"];
    if (
      !exactObjectKeys(
        candidate.counts,
        ready ? SHOPEE_GLOBAL_PLAN_COUNT_KEYS : [],
      )
      || Object.values(candidate.counts).some((count) => (
        !Number.isInteger(count) || count < 0
      ))
      || !exactObjectKeys(candidate.digests, expectedDigestKeys)
      || !oneClickDigest(candidate.digests.candidate_digest)
      || !nullableDigest(candidate.digests.observation_evidence_digest)
      || Object.entries(candidate.digests).some(([key, value]) => (
        !["observation_evidence_digest", "existing_global_identity_digest"]
          .includes(key)
        && !oneClickDigest(value)
      ))
      || (
        ready
        && !nullableDigest(candidate.digests.existing_global_identity_digest)
      )
      || (
        ready
          ? (
            candidate.planning_allowed !== true
            || !["NEW_GLOBAL", "EXISTING_GLOBAL"].includes(candidate.mode)
            || candidate.observation_authority !== "shopee_official_open_api"
            || candidate.observation_schema_version
              !== "shopee-official-global-plan-observation/v1"
            || candidate.blocker_codes.length
            || Object.values(candidate.checks).some((value) => value !== true)
          )
          : (
            candidate.planning_allowed !== false
            || candidate.blocker_codes.length < 1
          )
      )
    ) {
      throw oneClickContractError(
        "Shopee Global 候选证据不一致，已停止审批。",
      );
    }
    return candidate;
  }

  function validateApprovedShopeeGlobalPlan(approval) {
    if (approval === null) return null;
    const keys = [
      "schema_version",
      "approved_by",
      "literal_consent_recorded",
      "mode",
      "status",
      "counts",
      "digests",
    ];
    const digestKeys = [
      ...SHOPEE_GLOBAL_PLAN_DIGEST_KEYS.filter(
        (key) => key !== "candidate_digest",
      ),
      "candidate_digest",
      "approved_plan_digest",
    ];
    if (
      !exactObjectKeys(approval, keys)
      || APPROVED_SHOPEE_GLOBAL_PLAN_SCHEMA_MODES.get(
        approval.schema_version
      ) !== approval.mode
      || approval.approved_by !== "Kyle"
      || approval.literal_consent_recorded !== true
      || approval.status !== "APPROVED"
      || !exactObjectKeys(approval.counts, SHOPEE_GLOBAL_PLAN_COUNT_KEYS)
      || Object.values(approval.counts).some((count) => (
        !Number.isInteger(count) || count < 0
      ))
      || !exactObjectKeys(approval.digests, digestKeys)
      || Object.entries(approval.digests).some(([key, value]) => (
        key === "existing_global_identity_digest"
          ? !nullableDigest(value)
          : !oneClickDigest(value)
      ))
    ) {
      throw oneClickContractError(
        "Shopee Global 已批准计划合同不完整，已停止使用。",
      );
    }
    return approval;
  }

  function validateShopeeGlobalPlanPreview(payload, identity) {
    if (
      !exactObjectKeys(payload, [
        "ok",
        "schema_version",
        "offer_id",
        "product_revision",
        "candidate",
        "approval",
        "approval_current",
        "external_writes_performed",
      ])
      || payload.ok !== true
      || payload.schema_version !== SHOPEE_GLOBAL_PLAN_PREVIEW_SCHEMA
      || String(payload.offer_id) !== identity.offerId
      || payload.product_revision !== identity.revision
      || typeof payload.approval_current !== "boolean"
      || !Array.isArray(payload.external_writes_performed)
      || payload.external_writes_performed.length
    ) {
      throw oneClickContractError(
        "Shopee Global 只读预览身份不一致，已停止审批。",
      );
    }
    const candidate = validateShopeeGlobalPlanCandidate(payload.candidate);
    const approval = validateApprovedShopeeGlobalPlan(payload.approval);
    const observerFailure = ["BLOCKED_AUTH", "BLOCKED_CAPABILITY"]
      .includes(candidate.status) && !Object.hasOwn(candidate, "digests");
    if (
      observerFailure
      && (approval !== null || payload.approval_current !== false)
    ) {
      throw oneClickContractError(
        "Shopee Global 官方观察阻断时不得投影现行批准，已停止使用。",
      );
    }
    if (
      payload.approval_current === true
      && (
        approval === null
        || observerFailure
        || approval.mode !== candidate.mode
        || approval.digests.candidate_digest
          !== candidate.digests.candidate_digest
      )
    ) {
      throw oneClickContractError(
        "Shopee Global 已批准计划与当前候选不一致，已停止使用。",
      );
    }
    return {
      candidate,
      approval,
      approvalCurrent: payload.approval_current,
    };
  }

  function validateShopeeGlobalPlanApprovalResponse(
    payload,
    identity,
    candidate,
  ) {
    if (
      !exactObjectKeys(payload, [
        "ok",
        "persisted",
        "schema_version",
        "offer_id",
        "product_revision",
        "approval",
        "record_digest",
        "external_writes_performed",
      ])
      || payload.ok !== true
      || payload.persisted !== true
      || payload.schema_version !== SHOPEE_GLOBAL_PLAN_APPROVAL_SCHEMA
      || String(payload.offer_id) !== identity.offerId
      || payload.product_revision !== identity.revision
      || !oneClickDigest(payload.record_digest)
      || !Array.isArray(payload.external_writes_performed)
      || payload.external_writes_performed.length
    ) {
      throw oneClickContractError(
        "Shopee Global 审批回执身份不一致，已停止刷新。",
      );
    }
    const approval = validateApprovedShopeeGlobalPlan(payload.approval);
    if (
      approval === null
      || approval.mode !== candidate.mode
      || approval.digests.candidate_digest
        !== candidate.digests.candidate_digest
    ) {
      throw oneClickContractError(
        "Shopee Global 审批回执与当前候选不一致，已停止刷新。",
      );
    }
    return approval;
  }

  function validateShopeeCategoryDecisionProjection(
    payload,
    identity,
    { persistedResponse = false } = {},
  ) {
    const baseKeys = [
      "ok",
      "schema_version",
      "offer_id",
      "product_revision",
      "target_label",
      "mode",
      "status",
      "options_digest",
      "recommendation",
      "options",
      "brand_options",
      "location_options",
      "creation_fact_option",
      "selection",
      "attribute_selection",
      "blocker",
      "next_action",
      "external_writes_performed",
    ];
    const expectedKeys = persistedResponse
      ? [...baseKeys, "persisted", "created"]
      : baseKeys;
    if (
      !exactObjectKeys(payload, expectedKeys)
      || payload.ok !== true
      || payload.schema_version !== SHOPEE_CATEGORY_DECISION_PREVIEW_SCHEMA
      || String(payload.offer_id) !== identity.offerId
      || payload.product_revision !== identity.revision
      || payload.target_label !== SHOPEE_GLOBAL_CONTROL_TARGET
      || payload.mode !== "NEW_GLOBAL"
      || !SHOPEE_CATEGORY_DECISION_STATUSES.has(payload.status)
      || !Array.isArray(payload.options)
      || !Array.isArray(payload.brand_options)
      || !Array.isArray(payload.location_options)
      || !Array.isArray(payload.external_writes_performed)
      || payload.external_writes_performed.length
      || (
        persistedResponse
        && (
          payload.persisted !== true
          || typeof payload.created !== "boolean"
        )
      )
    ) {
      throw oneClickContractError(
        "Shopee Global 类目选择回执身份不一致，已停止使用。",
      );
    }
    const blockedLike = ["BLOCKED_CAPABILITY", "RECHECK_REQUIRED"]
      .includes(payload.status);
    if (blockedLike && !payload.options.length) {
      const recheck = payload.status === "RECHECK_REQUIRED";
      if (
        (recheck ? !oneClickDigest(payload.options_digest) : payload.options_digest !== null)
        || payload.recommendation !== null
        || payload.brand_options.length
        || payload.location_options.length
        || payload.creation_fact_option !== null
        || payload.selection !== null
        || (
          recheck
            ? (
              !exactObjectKeys(payload.attribute_selection, [
                "selection_digest",
                "category_identity_digest",
                "attribute_tree_digest",
                "selection_count",
                "approved_by",
              ])
              || !oneClickDigest(payload.attribute_selection.selection_digest)
              || !oneClickDigest(
                payload.attribute_selection.category_identity_digest,
              )
              || !oneClickDigest(
                payload.attribute_selection.attribute_tree_digest,
              )
              || !Number.isInteger(
                payload.attribute_selection.selection_count,
              )
              || payload.attribute_selection.selection_count < 0
              || payload.attribute_selection.approved_by !== "Kyle"
            )
            : payload.attribute_selection !== null
        )
        || !exactObjectKeys(payload.blocker, ["category", "code"])
        || payload.blocker.category !== "CAPABILITY"
        || !oneClickRuleId(payload.blocker.code)
        || !exactObjectKeys(payload.next_action, ["action", "target_focus"])
        || payload.next_action.action !== (
          recheck
            ? "recheck_channel_category_attributes"
            : "wait_for_channel_capability"
        )
        || payload.next_action.target_focus !== SHOPEE_GLOBAL_CONTROL_TARGET
      ) {
        throw oneClickContractError(
          "Shopee Global 类目能力阻断合同不完整，已安全停止。",
        );
      }
      return payload;
    }
    if (
      !oneClickDigest(payload.options_digest)
      || !exactObjectKeys(
        payload.recommendation,
        ["source", "category_identity_digest"],
      )
      || !exactObjectKeys(
        payload.recommendation.source,
        ["authority", "evidence_digest"],
      )
      || typeof payload.recommendation.source.authority !== "string"
      || !payload.recommendation.source.authority.trim()
      || !oneClickDigest(payload.recommendation.source.evidence_digest)
      || !oneClickDigest(
        payload.recommendation.category_identity_digest,
      )
      || !payload.options.length
      || !payload.brand_options.length
      || !payload.location_options.length
      || !payload.creation_fact_option
      || payload.blocker !== null
    ) {
      throw oneClickContractError(
        "Shopee Global 类目候选缺少官方推荐证据，已停止选择。",
      );
    }
    const optionKeys = [
      "category_identity_digest",
      "display_name",
      "path_labels",
      "recommended",
      "approval_ready",
      "attribute_status",
      "required_attribute_count",
      "selected_attribute_count",
      "missing_required_attributes",
      "attribute_tree_digest",
      "option_evidence_digest",
    ];
    const missingAttributeKeys = [
      "attribute_identity_digest",
      "label",
      "selection_kind",
      "option_values",
    ];
    let recommendedCount = 0;
    const seen = new Set();
    let previousDigest = "";
    for (const option of payload.options) {
      if (
        !exactObjectKeys(option, optionKeys)
        || !oneClickDigest(option.category_identity_digest)
        || seen.has(option.category_identity_digest)
        || (
          previousDigest
          && previousDigest >= option.category_identity_digest
        )
        || typeof option.display_name !== "string"
        || !option.display_name.trim()
        || !Array.isArray(option.path_labels)
        || !option.path_labels.length
        || option.path_labels.some((label) => (
          typeof label !== "string" || !label.trim()
        ))
        || typeof option.recommended !== "boolean"
        || typeof option.approval_ready !== "boolean"
        || !["READY", "BLOCKED_REQUIRED_VALUES"]
          .includes(option.attribute_status)
        || !Number.isInteger(option.required_attribute_count)
        || option.required_attribute_count < 0
        || !Number.isInteger(option.selected_attribute_count)
        || option.selected_attribute_count < 0
        || !Array.isArray(option.missing_required_attributes)
        || !oneClickDigest(option.attribute_tree_digest)
        || !oneClickDigest(option.option_evidence_digest)
      ) {
        throw oneClickContractError(
          "Shopee Global 类目候选结构不完整，已停止选择。",
        );
      }
      for (const missing of option.missing_required_attributes) {
        if (
          !exactObjectKeys(missing, missingAttributeKeys)
          || !oneClickDigest(missing.attribute_identity_digest)
          || typeof missing.label !== "string"
          || !missing.label.trim()
          || !["SINGLE", "MULTI", "TEXT"].includes(missing.selection_kind)
          || !Array.isArray(missing.option_values)
          || missing.option_values.some((value) => (
            !exactObjectKeys(value, [
              "option_identity_digest",
              "display_label",
              "recommended",
            ])
            || !oneClickDigest(value.option_identity_digest)
            || typeof value.display_label !== "string"
            || !value.display_label.trim()
            || typeof value.recommended !== "boolean"
          ))
          || (
            missing.selection_kind === "TEXT"
              ? missing.option_values.length !== 0
              : missing.option_values.length === 0
          )
        ) {
          throw oneClickContractError(
            "Shopee Global 必填属性提示结构不完整，已停止选择。",
          );
        }
      }
      if (
        (
          option.approval_ready
          && (
            option.attribute_status !== "READY"
            || option.missing_required_attributes.length
          )
        )
        || (
          !option.approval_ready
          && (
            option.attribute_status !== "BLOCKED_REQUIRED_VALUES"
            || !option.missing_required_attributes.length
          )
        )
      ) {
        throw oneClickContractError(
          "Shopee Global 类目属性完成状态互相矛盾，已停止选择。",
        );
      }
      if (option.recommended) {
        recommendedCount += 1;
        if (
          option.category_identity_digest
          !== payload.recommendation.category_identity_digest
        ) {
          throw oneClickContractError(
            "Shopee Global 推荐类目身份不一致，已停止选择。",
          );
        }
      }
      seen.add(option.category_identity_digest);
      previousDigest = option.category_identity_digest;
    }
    const validateNamedOptions = (rows, identityKey) => {
      const seenOptions = new Set();
      let recommendationCount = 0;
      for (const row of rows) {
        if (
          !exactObjectKeys(row, [
            identityKey,
            "display_name",
            "recommended",
            "option_evidence_digest",
          ])
          || !oneClickDigest(row[identityKey])
          || seenOptions.has(row[identityKey])
          || typeof row.display_name !== "string"
          || !row.display_name.trim()
          || typeof row.recommended !== "boolean"
          || !oneClickDigest(row.option_evidence_digest)
        ) {
          throw oneClickContractError(
            "Shopee Global 品牌或卖家位置候选不完整，已停止选择。",
          );
        }
        if (row.recommended) recommendationCount += 1;
        seenOptions.add(row[identityKey]);
      }
      if (recommendationCount !== 1) {
        throw oneClickContractError(
          "Shopee Global 品牌或卖家位置推荐不唯一，已停止选择。",
        );
      }
    };
    validateNamedOptions(payload.brand_options, "brand_identity_digest");
    validateNamedOptions(
      payload.location_options,
      "location_identity_digest",
    );
    const creation = payload.creation_fact_option;
    if (
      !exactObjectKeys(creation, [
        "creation_fact_identity_digest",
        "seller_stock_quantity",
        "condition",
        "preorder",
        "variation_summary",
        "recommended",
        "option_evidence_digest",
      ])
      || !oneClickDigest(creation.creation_fact_identity_digest)
      || !Number.isInteger(creation.seller_stock_quantity)
      || creation.seller_stock_quantity <= 0
      || typeof creation.condition !== "string"
      || !creation.condition.trim()
      || !exactObjectKeys(creation.preorder, [
        "is_pre_order",
        "days_to_ship",
      ])
      || typeof creation.preorder.is_pre_order !== "boolean"
      || !Number.isInteger(creation.preorder.days_to_ship)
      || creation.preorder.days_to_ship < 0
      || !exactObjectKeys(creation.variation_summary, [
        "tier_count",
        "model_count",
        "model_sku_count",
        "approved_image_position",
      ])
      || Object.values(creation.variation_summary).some((value) => (
        !Number.isInteger(value) || value <= 0
      ))
      || creation.recommended !== true
      || !oneClickDigest(creation.option_evidence_digest)
      || !(
        payload.attribute_selection === null
        || (
          exactObjectKeys(payload.attribute_selection, [
            "selection_digest",
            "category_identity_digest",
            "attribute_tree_digest",
            "selection_count",
            "approved_by",
          ])
          && oneClickDigest(payload.attribute_selection.selection_digest)
          && oneClickDigest(
            payload.attribute_selection.category_identity_digest,
          )
          && oneClickDigest(
            payload.attribute_selection.attribute_tree_digest,
          )
          && Number.isInteger(payload.attribute_selection.selection_count)
          && payload.attribute_selection.selection_count >= 0
          && payload.attribute_selection.approved_by === "Kyle"
        )
      )
    ) {
      throw oneClickContractError(
        "Shopee Global 库存、状态或单 SKU 规格事实不完整，已停止选择。",
      );
    }
    if (
      recommendedCount !== 1
      || !seen.has(payload.recommendation.category_identity_digest)
    ) {
      throw oneClickContractError(
        "Shopee Global 推荐类目不是唯一官方候选，已停止选择。",
      );
    }
    const readyOptions = payload.options.filter(
      (option) => option.approval_ready,
    );
    if (payload.status === "BLOCKED_CAPABILITY") {
      if (
        payload.blocker !== null
        || payload.selection !== null
        || payload.attribute_selection !== null
        || readyOptions.length
        || !exactObjectKeys(payload.next_action, ["action", "target_focus"])
        || payload.next_action.action
          !== "complete_official_category_attributes"
        || payload.next_action.target_focus !== SHOPEE_GLOBAL_CONTROL_TARGET
      ) {
        throw oneClickContractError(
          "Shopee Global 必填属性待补全状态不一致，已停止选择。",
        );
      }
      return payload;
    }
    if (payload.status === "READY_FOR_SELECTION") {
      if (
        payload.selection !== null
        || !readyOptions.length
        || !exactObjectKeys(payload.next_action, ["action", "target_focus"])
        || payload.next_action.action !== "select_channel_category"
        || payload.next_action.target_focus !== SHOPEE_GLOBAL_CONTROL_TARGET
      ) {
        throw oneClickContractError(
          "Shopee Global 类目待选状态不一致，已停止选择。",
        );
      }
      return payload;
    }
    if (
      payload.status !== "SELECTED"
      || !exactObjectKeys(payload.selection, [
        "decision_digest",
        "selected_category_identity_digest",
        "selected_is_recommended",
        "attribute_tree_digest",
        "approved_by",
        "selected_brand",
        "selected_location",
        "creation_fact_identity_digest",
        "attribute_selection_digest",
        "seller_stock_quantity",
        "condition",
        "preorder",
        "variation_summary",
      ])
      || !oneClickDigest(payload.selection.decision_digest)
      || !oneClickDigest(
        payload.selection.selected_category_identity_digest,
      )
      || typeof payload.selection.selected_is_recommended !== "boolean"
      || !oneClickDigest(payload.selection.attribute_tree_digest)
      || payload.selection.approved_by !== "Kyle"
      || !exactObjectKeys(payload.selection.selected_brand, [
        "brand_identity_digest",
        "display_name",
        "selected_is_recommended",
      ])
      || !oneClickDigest(
        payload.selection.selected_brand.brand_identity_digest,
      )
      || !exactObjectKeys(payload.selection.selected_location, [
        "location_identity_digest",
        "display_name",
        "selected_is_recommended",
      ])
      || !oneClickDigest(
        payload.selection.selected_location.location_identity_digest,
      )
      || !oneClickDigest(
        payload.selection.creation_fact_identity_digest,
      )
      || !oneClickDigest(payload.selection.attribute_selection_digest)
      || !Number.isInteger(payload.selection.seller_stock_quantity)
      || payload.selection.seller_stock_quantity <= 0
      || typeof payload.selection.condition !== "string"
      || !exactObjectKeys(payload.selection.preorder, [
        "is_pre_order",
        "days_to_ship",
      ])
      || !exactObjectKeys(payload.selection.variation_summary, [
        "tier_count",
        "model_count",
        "model_sku_count",
        "approved_image_position",
      ])
      || !exactObjectKeys(payload.next_action, ["action", "target_focus"])
      || payload.next_action.action !== "review_shopee_global_plan"
      || payload.next_action.target_focus !== SHOPEE_GLOBAL_CONTROL_TARGET
    ) {
      throw oneClickContractError(
        "Shopee Global 已选类目合同不完整，已停止使用。",
      );
    }
    const selected = payload.options.find(
      (option) => (
        option.category_identity_digest
        === payload.selection.selected_category_identity_digest
      ),
    );
    if (
      !selected
      || !selected.approval_ready
      || selected.attribute_tree_digest
        !== payload.selection.attribute_tree_digest
      || selected.recommended
        !== payload.selection.selected_is_recommended
      || payload.selection.creation_fact_identity_digest
        !== creation.creation_fact_identity_digest
      || !payload.brand_options.some((row) => (
        row.brand_identity_digest
        === payload.selection.selected_brand.brand_identity_digest
      ))
      || !payload.location_options.some((row) => (
        row.location_identity_digest
        === payload.selection.selected_location.location_identity_digest
      ))
    ) {
      throw oneClickContractError(
        "Shopee Global 已选类目与当前官方候选不一致，已停止使用。",
      );
    }
    return payload;
  }

  function shopeeCategoryDecisionRequired(candidate) {
    return Boolean(
      candidate
      && Object.hasOwn(candidate, "digests")
      && candidate.mode === "NEW_GLOBAL",
    );
  }

  function shopeeCategoryDecisionCanCollectSelection(projection) {
    return Boolean(
      projection
      && (
        projection.status === "READY_FOR_SELECTION"
        || (
          projection.status === "BLOCKED_CAPABILITY"
          && projection.blocker === null
          && Array.isArray(projection.options)
          && projection.options.length > 0
        )
      ),
    );
  }

  function selectedShopeeCategoryOption() {
    const projection = shopeeCategoryDecisionReview.projection;
    if (
      !projection
      || (
        projection.status !== "SELECTED"
        && !shopeeCategoryDecisionCanCollectSelection(projection)
      )
    ) return null;
    const digest = projection.status === "SELECTED"
      ? projection.selection.selected_category_identity_digest
      : shopeeCategoryDecisionReview.draftIdentityDigest;
    return projection.options.find(
      (option) => option.category_identity_digest === digest,
    ) || null;
  }

  function adoptShopeeCategoryProjection(projection) {
    shopeeCategoryDecisionReview.projection = projection;
    shopeeCategoryDecisionReview.draftIdentityDigest = (
      projection.status === "SELECTED"
        ? projection.selection.selected_category_identity_digest
        : projection.recommendation?.category_identity_digest || ""
    );
    shopeeCategoryDecisionReview.draftBrandIdentityDigest = (
      projection.status === "SELECTED"
        ? projection.selection.selected_brand.brand_identity_digest
        : projection.brand_options.find((row) => row.recommended)
          ?.brand_identity_digest || ""
    );
    shopeeCategoryDecisionReview.draftLocationIdentityDigest = (
      projection.status === "SELECTED"
        ? projection.selection.selected_location.location_identity_digest
        : projection.location_options.find((row) => row.recommended)
          ?.location_identity_digest || ""
    );
    shopeeCategoryDecisionReview.requiredAttributeSelections = {};
    shopeeCategoryDecisionReview.confirmSelection = false;
    shopeeCategoryDecisionReview.confirmSellerStock = false;
    shopeeCategoryDecisionReview.confirmConditionAndPreorder = false;
    shopeeCategoryDecisionReview.confirmRequiredAttributes = false;
    shopeeCategoryDecisionReview.error = "";
  }

  async function requestShopeeCategoryDecisionPreview(identity) {
    const generation = shopeeCategoryDecisionReview.generation;
    if (
      !identity
      || shopeeCategoryDecisionReview.previewAttempted
      || shopeeCategoryDecisionReview.previewBusy
    ) return;
    shopeeCategoryDecisionReview.previewAttempted = true;
    shopeeCategoryDecisionReview.previewBusy = true;
    shopeeCategoryDecisionReview.error = "";
    const controller = new AbortController();
    shopeeCategoryDecisionReview.controller = controller;
    renderOneClickExecution(currentData);
    try {
      const params = new URLSearchParams({
        offer_id: identity.offerId,
        target_label: SHOPEE_GLOBAL_CONTROL_TARGET,
      });
      const { response, payload } = await boundedJsonFetch(
        `/api/product-workspace/channel-category-decision-preview?${params}`,
        {
          headers: { Accept: "application/json" },
          controller,
        },
        SHOPEE_GLOBAL_READ_TIMEOUT_MS,
        "Shopee Global 类目候选只读预览",
      );
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || `服务返回 HTTP ${response.status}`);
      }
      const projection = validateShopeeCategoryDecisionProjection(
        payload,
        identity,
      );
      if (generation !== shopeeCategoryDecisionReview.generation) return;
      adoptShopeeCategoryProjection(projection);
      shopeeCategoryDecisionReview.message = (
        projection.status === "SELECTED"
          ? "已恢复 Kyle 对当前类目与属性树的固化选择。"
          : ""
      );
    } catch (error) {
      if (
        error.name === "AbortError"
        || generation !== shopeeCategoryDecisionReview.generation
      ) return;
      shopeeCategoryDecisionReview.error = friendlyError(error.message);
    } finally {
      if (generation === shopeeCategoryDecisionReview.generation) {
        shopeeCategoryDecisionReview.previewBusy = false;
        if (shopeeCategoryDecisionReview.controller === controller) {
          shopeeCategoryDecisionReview.controller = null;
        }
        renderOneClickExecution(currentData);
        renderReleaseRecovery(currentData?.release_v1 || {});
      }
    }
  }

  function ensureShopeeCategoryDecisionReview(identity, required) {
    if (!identity || required !== true) {
      if (shopeeCategoryDecisionReview.contextKey) {
        resetShopeeCategoryDecisionReview();
      }
      return Promise.resolve();
    }
    if (shopeeCategoryDecisionReview.contextKey !== identity.key) {
      resetShopeeCategoryDecisionReview();
      shopeeCategoryDecisionReview.contextKey = identity.key;
    }
    return requestShopeeCategoryDecisionPreview(identity);
  }

  function shopeeGlobalPlanRequired(projection) {
    const control = projection?.shared_controls?.find(
      (row) => row.target_label === SHOPEE_GLOBAL_CONTROL_TARGET,
    );
    return control?.next_action === "review_shopee_global_plan"
      || projection?.canonical_next_action?.action
        === "review_shopee_global_plan";
  }

  function shopeeGlobalPlanRecoveryRequired(data) {
    return (data?.release_v1?.recovery_actions || []).some(
      (action) => action?.code === "review_shopee_global_plan",
    );
  }

  function shopeeGlobalPlanReviewRequired(data, projection) {
    return shopeeGlobalPlanRequired(projection)
      || shopeeGlobalPlanRecoveryRequired(data);
  }

  async function requestShopeeGlobalPlanPreview(identity) {
    const generation = shopeeGlobalPlanReview.generation;
    if (
      !identity
      || shopeeGlobalPlanReview.previewAttempted
      || shopeeGlobalPlanReview.previewBusy
    ) return;
    shopeeGlobalPlanReview.previewAttempted = true;
    shopeeGlobalPlanReview.previewBusy = true;
    shopeeGlobalPlanReview.error = "";
    const controller = new AbortController();
    shopeeGlobalPlanReview.controller = controller;
    renderOneClickExecution(currentData);
    try {
      const params = new URLSearchParams({ offer_id: identity.offerId });
      const { response, payload } = await boundedJsonFetch(
        `/api/product-workspace/shopee-global-plan-preview?${params}`,
        {
          headers: { Accept: "application/json" },
          controller,
        },
        SHOPEE_GLOBAL_READ_TIMEOUT_MS,
        "Shopee Global 官方计划只读预览",
      );
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || `服务返回 HTTP ${response.status}`);
      }
      const validated = validateShopeeGlobalPlanPreview(payload, identity);
      if (generation !== shopeeGlobalPlanReview.generation) return;
      shopeeGlobalPlanReview.candidate = validated.candidate;
      shopeeGlobalPlanReview.approval = validated.approval;
      shopeeGlobalPlanReview.approvalCurrent = validated.approvalCurrent;
      shopeeGlobalPlanReview.error = "";
    } catch (error) {
      if (
        error.name === "AbortError"
        || generation !== shopeeGlobalPlanReview.generation
      ) return;
      shopeeGlobalPlanReview.error = friendlyError(error.message);
    } finally {
      if (generation === shopeeGlobalPlanReview.generation) {
        shopeeGlobalPlanReview.previewBusy = false;
        if (shopeeGlobalPlanReview.controller === controller) {
          shopeeGlobalPlanReview.controller = null;
        }
        renderOneClickExecution(currentData);
        renderReleaseRecovery(currentData?.release_v1 || {});
      }
    }
  }

  function ensureShopeeGlobalPlanReview(identity, required) {
    if (!identity || required !== true) {
      if (shopeeGlobalPlanReview.contextKey) resetShopeeGlobalPlanReview();
      return Promise.resolve();
    }
    if (shopeeGlobalPlanReview.contextKey !== identity.key) {
      resetShopeeGlobalPlanReview();
      shopeeGlobalPlanReview.contextKey = identity.key;
    }
    return requestShopeeGlobalPlanPreview(identity);
  }

  function shopeeGlobalPlanBlockerText(code) {
    const labels = {
      official_authority_unavailable: "缺少官方 Open API 权威观察",
      shopee_global_observer_contract_invalid:
        "Shopee Global 观察合同不一致；系统已安全阻断",
      shopee_official_global_list_unavailable:
        "Shopee 官方 Global 商品清单无法读取；请检查 Open API Global Product 权限",
      audited_schema_unavailable: "官方响应结构尚未完成审计",
      category_evidence_unavailable: "缺少官方类目证据",
      attributes_incomplete: "类目属性尚未完整核准",
      variations_incomplete: "规格与模型组合尚未完整核准",
      seller_stock_unavailable: "卖家库存事实尚未完成批准",
      location_unavailable: "发货位置事实尚未核准",
    };
    return labels[code] || `等待服务端解除阻断：${code}`;
  }

  function shopeeCategoryOptionLabel(option) {
    return option.path_labels.join(" › ") || option.display_name;
  }

  function shopeeRequiredAttributesComplete(option) {
    return (option?.missing_required_attributes || []).every((attribute) => {
      const value = shopeeCategoryDecisionReview
        .requiredAttributeSelections[attribute.attribute_identity_digest];
      if (attribute.selection_kind === "TEXT") {
        return typeof value?.textValue === "string"
          && value.textValue.trim().length >= 1
          && value.textValue.trim().length <= 120;
      }
      if (attribute.selection_kind === "SINGLE") {
        return Array.isArray(value?.optionDigests)
          && value.optionDigests.length === 1;
      }
      return Array.isArray(value?.optionDigests)
        && value.optionDigests.length >= 1;
    });
  }

  function shopeeRequiredAttributeMarkup(attribute, locked) {
    const current = shopeeCategoryDecisionReview
      .requiredAttributeSelections[attribute.attribute_identity_digest] || {};
    const name = `attribute_${attribute.attribute_identity_digest}`;
    if (attribute.selection_kind === "TEXT") {
      return `
        <label class="channel-attribute-field">
          <span>${esc(attribute.label)}（文本，1–120 字）</span>
          <input type="text" maxlength="120"
            name="${esc(name)}"
            data-category-attribute="${esc(attribute.attribute_identity_digest)}"
            data-selection-kind="TEXT"
            value="${esc(current.textValue || "")}"
            ${locked ? "disabled" : ""}>
        </label>
      `;
    }
    if (attribute.selection_kind === "SINGLE") {
      const selected = current.optionDigests?.[0] || "";
      return `
        <label class="channel-attribute-field">
          <span>${esc(attribute.label)}（单选）</span>
          <select name="${esc(name)}"
            data-category-attribute="${esc(attribute.attribute_identity_digest)}"
            data-selection-kind="SINGLE"
            ${locked ? "disabled" : ""}>
            <option value="">请选择，不会自动采用推荐值</option>
            ${attribute.option_values.map((value) => `
              <option value="${esc(value.option_identity_digest)}"
                ${selected === value.option_identity_digest ? "selected" : ""}>
                ${esc(value.display_label)}
                ${value.recommended ? "（系统推荐）" : ""}
              </option>
            `).join("")}
          </select>
        </label>
      `;
    }
    const selected = new Set(current.optionDigests || []);
    return `
      <fieldset class="channel-attribute-field">
        <legend>${esc(attribute.label)}（多选）</legend>
        ${attribute.option_values.map((value) => `
          <label>
            <input type="checkbox"
              data-category-attribute="${esc(attribute.attribute_identity_digest)}"
              data-selection-kind="MULTI"
              value="${esc(value.option_identity_digest)}"
              ${selected.has(value.option_identity_digest) ? "checked" : ""}
              ${locked ? "disabled" : ""}>
            <span>${esc(value.display_label)}
              ${value.recommended ? "（系统推荐）" : ""}</span>
          </label>
        `).join("")}
      </fieldset>
    `;
  }

  function shopeeCategoryDecisionPanel() {
    if (shopeeCategoryDecisionReview.previewBusy) {
      return `
        <section class="channel-category-decision" aria-busy="true">
          <strong>Shopee Global 类目决定</strong>
          <p>正在只读读取官方候选、推荐依据和必填属性状态；不会保存或发布。</p>
        </section>
      `;
    }
    if (shopeeCategoryDecisionReview.error) {
      return `
        <section class="channel-category-decision is-blocked" role="alert">
          <strong>Shopee Global 类目候选暂不可用</strong>
          <p>${esc(shopeeCategoryDecisionReview.error)}</p>
          <p>未保存类目决定，也未触发任何渠道发布。只能重新只读读取。</p>
          <button class="button button-secondary channel-category-preview-retry"
            type="button">重新读取官方类目候选</button>
        </section>
      `;
    }
    const projection = shopeeCategoryDecisionReview.projection;
    if (!projection) {
      return `
        <section class="channel-category-decision">
          <strong>Shopee Global 类目决定</strong>
          <p>等待服务端提供当前 revision 的官方类目候选。</p>
        </section>
      `;
    }
    if (
      (
        projection.status === "BLOCKED_CAPABILITY"
        && !shopeeCategoryDecisionCanCollectSelection(projection)
      )
      || projection.status === "RECHECK_REQUIRED"
    ) {
      const rechecking = projection.status === "RECHECK_REQUIRED";
      return `
        <section class="channel-category-decision is-blocked">
          <strong>${rechecking
            ? "已保存完整决定，等待官方属性复核"
            : "Shopee Global 官方类目能力不可用"}</strong>
          <p>服务端阻断：${esc(projection.blocker.code)}。${rechecking
            ? "系统只会继续 GET 复核，绝不会重复 POST。"
            : "系统不会猜测类目，也不会开放最终计划批准。"}</p>
          <button class="button button-secondary channel-category-preview-retry"
            type="button">${rechecking
              ? "只读复核官方属性"
              : "重新读取官方类目能力"}</button>
        </section>
      `;
    }
    const recommendation = projection.options.find(
      (option) => option.recommended,
    );
    const selected = selectedShopeeCategoryOption();
    const locked = projection.status === "SELECTED";
    const attributesComplete = shopeeRequiredAttributesComplete(selected);
    const brandSelected = projection.brand_options.some((row) => (
      row.brand_identity_digest
      === shopeeCategoryDecisionReview.draftBrandIdentityDigest
    ));
    const locationSelected = projection.location_options.some((row) => (
      row.location_identity_digest
      === shopeeCategoryDecisionReview.draftLocationIdentityDigest
    ));
    const selectedReady = Boolean(
      selected
      && attributesComplete
      && brandSelected
      && locationSelected
    );
    const disabled = Boolean(
      locked
      || !selectedReady
      || !shopeeCategoryDecisionReview.confirmSelection
      || !shopeeCategoryDecisionReview.confirmSellerStock
      || !shopeeCategoryDecisionReview.confirmConditionAndPreorder
      || !shopeeCategoryDecisionReview.confirmRequiredAttributes
      || shopeeCategoryDecisionReview.submitting
      || shopeeCategoryDecisionReview.postAttempted,
    );
    const missing = selected?.missing_required_attributes || [];
    const disabledReason = locked
      ? "当前 revision 的类目决定已固化；这里只读显示，不允许无新证据覆盖。"
      : shopeeCategoryDecisionReview.postAttempted
        ? "保存请求已尝试；先只读核对结果，禁止重复提交。"
        : !selectedReady
          ? "请完成当前类目全部官方必填属性；品牌和卖家位置已由固定政策锁定。"
          : !shopeeCategoryDecisionReview.confirmSelection
            ? "请完成四项 Kyle 明确确认，才可保存完整创建决定。"
            : !shopeeCategoryDecisionReview.confirmSellerStock
              ? "请明确确认可见的正数卖家库存数量。"
              : !shopeeCategoryDecisionReview.confirmConditionAndPreorder
                ? "请明确确认商品状态与预购设置。"
                : !shopeeCategoryDecisionReview.confirmRequiredAttributes
                  ? "请明确确认全部必填属性选择。"
            : "";
    const wrapperTag = locked ? "section" : "form";
    return `
      <${wrapperTag} class="channel-category-decision ${locked
        ? "is-selected"
        : "channel-category-decision-form"}"
        data-options-digest="${esc(projection.options_digest)}">
        <div class="channel-category-decision-heading">
          <div>
            <strong>Shopee Global 类目决定</strong>
            <p>类目推荐仅用于辅助判断；只有 Kyle 明确选择并保存后，才会进入最终计划批准。</p>
          </div>
          <span class="badge">${locked ? "已固化" : "待选择"}</span>
        </div>
        <dl class="channel-category-recommendation">
          <div>
            <dt>系统推荐</dt>
            <dd>${esc(shopeeCategoryOptionLabel(recommendation))}</dd>
          </div>
          <div>
            <dt>推荐依据</dt>
            <dd>${esc(projection.recommendation.source.authority)}
              · ${esc(compactDigest(
                projection.recommendation.source.evidence_digest,
              ))}</dd>
          </div>
        </dl>
        <p class="channel-category-disclaimer">
          系统推荐不等于 Kyle 批准。选择其他官方候选不会被自动改回推荐项。
        </p>
        <label class="channel-category-choice">
          <span>选择 Shopee Global 类目</span>
          <select name="selected_category_identity_digest"
            ${locked ? "disabled" : ""}
            aria-label="选择 Shopee Global 类目">
            ${projection.options.map((option) => `
              <option value="${esc(option.category_identity_digest)}"
                ${option.category_identity_digest
                  === selected?.category_identity_digest ? "selected" : ""}
                >
                ${esc(shopeeCategoryOptionLabel(option))}
                ${option.recommended ? "（系统推荐）" : ""}
                ${option.approval_ready ? "" : "（需在下方补全属性）"}
              </option>
            `).join("")}
          </select>
        </label>
        <p class="channel-category-option-note">
          ${selected
            ? `${selected.recommended ? "系统推荐候选" : "备选候选"} · `
              + `已选属性 ${selected.selected_attribute_count}/`
              + `${selected.required_attribute_count} · `
              + `证据 ${compactDigest(selected.option_evidence_digest)}`
            : "未找到与当前选择一致的官方候选。"}
        </p>
        <section class="channel-category-attributes ${missing.length ? "" : "is-complete"}">
          <strong>${missing.length ? "完成官方必填属性" : "官方必填属性已完整"}</strong>
          ${missing.length ? `
            <p>只使用服务端提供的官方选项或受校验文本。推荐值可见，但不会自动批准。</p>
            <div class="channel-category-attribute-grid">
              ${missing.map((row) => (
                shopeeRequiredAttributeMarkup(row, locked)
              )).join("")}
            </div>
          ` : `
            <p>当前候选的属性树已完整，可由 Kyle 明确保存类目决定。</p>
          `}
        </section>
        <div class="channel-category-fact-grid">
          <div class="channel-category-choice channel-category-fixed-fact">
            <span>官方品牌（固定政策）</span>
            <strong>${esc(
              projection.brand_options.find((row) => row.recommended)
                ?.display_name || "无品牌政策不可用",
            )}</strong>
          </div>
          <div class="channel-category-choice channel-category-fixed-fact">
            <span>卖家位置（固定政策）</span>
            <strong>${esc(
              projection.location_options.find((row) => row.recommended)
                ?.display_name || "中国仓库政策不可用",
            )}</strong>
          </div>
        </div>
        <section class="channel-category-creation-facts">
          <strong>NEW_GLOBAL 创建事实（必须明确确认）</strong>
          <dl class="channel-category-recommendation">
            <div><dt>卖家库存</dt><dd>${esc(
              projection.creation_fact_option.seller_stock_quantity,
            )}</dd></div>
            <div><dt>状态 / 预购</dt><dd>${esc(
              projection.creation_fact_option.condition,
            )} · ${projection.creation_fact_option.preorder.is_pre_order
              ? `预购 ${esc(
                projection.creation_fact_option.preorder.days_to_ship,
              )} 天`
              : "非预购"}</dd></div>
            <div><dt>单 SKU 规格</dt><dd>${esc(
              projection.creation_fact_option.variation_summary.tier_count,
            )} 层 / ${esc(
              projection.creation_fact_option.variation_summary.model_count,
            )} 模型 / ${esc(
              projection.creation_fact_option.variation_summary.model_sku_count,
            )} SKU</dd></div>
            <div><dt>批准图片位置</dt><dd>#${esc(
              projection.creation_fact_option.variation_summary
                .approved_image_position,
            )}</dd></div>
          </dl>
        </section>
        ${locked ? "" : `
          <label class="manual-verification-confirm">
            <input name="confirm_channel_category_selection"
              type="checkbox"
              ${shopeeCategoryDecisionReview.confirmSelection ? "checked" : ""}>
            <span>我 Kyle 已核对推荐依据、备选类目与必填属性，并明确选择当前类目。</span>
          </label>
          <label class="manual-verification-confirm">
            <input name="confirm_seller_stock_quantity" type="checkbox"
              ${shopeeCategoryDecisionReview.confirmSellerStock ? "checked" : ""}>
            <span>我 Kyle 明确确认上述正数卖家库存数量；它不会被静默采用。</span>
          </label>
          <label class="manual-verification-confirm">
            <input name="confirm_condition_and_preorder" type="checkbox"
              ${shopeeCategoryDecisionReview.confirmConditionAndPreorder ? "checked" : ""}>
            <span>我 Kyle 明确确认商品状态与预购设置。</span>
          </label>
          <label class="manual-verification-confirm">
            <input name="confirm_required_attribute_selections" type="checkbox"
              ${shopeeCategoryDecisionReview.confirmRequiredAttributes ? "checked" : ""}>
            <span>我 Kyle 明确确认当前全部官方必填属性选择。</span>
          </label>
        `}
        <div class="channel-category-decision-actions">
          ${locked ? `
            <button class="button button-secondary channel-category-preview-retry"
              type="button">重新读取已固化决定</button>
          ` : `
            <button class="button button-secondary" type="submit"
              ${disabled ? "disabled" : ""}>保存当前类目决定</button>
            <button class="button button-secondary channel-category-preview-retry"
              type="button">刷新官方候选</button>
          `}
        </div>
        <p class="channel-category-disabled-reason">${esc(disabledReason)}</p>
        <p class="channel-category-save-message" role="status"
          aria-live="polite">${esc(shopeeCategoryDecisionReview.message)}</p>
      </${wrapperTag}>
    `;
  }

  function shopeeCategoryDecisionAllowsFinalApproval(candidate) {
    if (candidate?.mode !== "NEW_GLOBAL") return true;
    const projection = shopeeCategoryDecisionReview.projection;
    return Boolean(
      projection?.status === "SELECTED"
      && selectedShopeeCategoryOption()?.approval_ready === true,
    );
  }

  function shopeeGlobalPlanPanel() {
    if (shopeeGlobalPlanReview.previewBusy) {
      return `
        <div class="shopee-global-plan-review" aria-busy="true">
          <strong>Shopee Global 计划审核</strong>
          <p>正在读取官方只读候选；不会创建、修改或发布商品。</p>
        </div>
      `;
    }
    if (shopeeGlobalPlanReview.error) {
      return `
        <div class="shopee-global-plan-review is-blocked" role="alert">
          <strong>Shopee Global 计划暂不可审核</strong>
          <p>${esc(shopeeGlobalPlanReview.error)}</p>
          <p>未批准、未提交、未执行任何渠道写入。可只读重试，不会触发发布。</p>
          <button class="button button-secondary shopee-global-plan-preview-retry"
            type="button">重新读取 Shopee Global 计划</button>
        </div>
      `;
    }
    const candidate = shopeeGlobalPlanReview.candidate;
    if (!candidate) {
      return `
        <div class="shopee-global-plan-review">
          <strong>Shopee Global 计划审核</strong>
          <p>等待官方只读候选。</p>
        </div>
      `;
    }
    const observerFailure = !Object.hasOwn(candidate, "digests");
    if (observerFailure) {
      const authBlocked = candidate.status === "BLOCKED_AUTH";
      return `
        <div class="shopee-global-plan-review is-blocked">
          <strong>${authBlocked
            ? "Shopee Global 官方授权不可用"
            : "Shopee Global 官方计划能力不可用"}</strong>
          <p>${esc(shopeeGlobalPlanBlockerText(candidate.reason_code))}</p>
          <p>未取得可批准候选，不会显示批准按钮，也不会以默认类目、属性或身份继续。</p>
          ${authBlocked
            ? `<button class="button button-secondary shopee-global-auth-restore"
                type="button">前往恢复 Shopee 授权</button>`
            : `<button class="button button-secondary shopee-global-plan-preview-retry"
                type="button">重新读取官方能力</button>`}
        </div>
      `;
    }
    const modeText = candidate.mode === "NEW_GLOBAL"
      ? "新建 Global 商品"
      : candidate.mode === "EXISTING_GLOBAL"
        ? "复用既有 Global 商品"
        : "模式尚未核准";
    const checks = Object.entries(candidate.checks)
      .map(([key, value]) => `
        <li><span>${esc(key)}</span><strong>${value ? "通过" : "未通过"}</strong></li>
      `).join("");
    const counts = Object.entries(candidate.counts)
      .map(([key, value]) => `
        <li><span>${esc(key)}</span><strong>${esc(value)}</strong></li>
      `).join("");
    const digest = candidate.digests.candidate_digest;
    if (candidate.status === "BLOCKED_CAPABILITY") {
      return `
        <div class="shopee-global-plan-review is-blocked">
          <strong>Shopee Global 计划尚不可批准</strong>
          <p>${esc(modeText)}；系统不会以默认类目、属性、库存或位置继续。</p>
          <ul class="shopee-global-plan-blockers">
            ${candidate.blocker_codes.map((code) => `
              <li>${esc(shopeeGlobalPlanBlockerText(code))}</li>
            `).join("")}
          </ul>
          <p>候选摘要 ${esc(compactDigest(digest))}</p>
          ${candidate.mode === "NEW_GLOBAL"
            ? shopeeCategoryDecisionPanel()
            : ""}
        </div>
      `;
    }
    if (shopeeGlobalPlanReview.approvalCurrent) {
      return `
        <div class="shopee-global-plan-review is-approved">
          <strong>Shopee Global 计划已由 Kyle 批准</strong>
          <p>${esc(modeText)} · 当前候选摘要 ${esc(compactDigest(digest))}</p>
          <ul class="shopee-global-plan-checks">${checks}${counts}</ul>
        </div>
      `;
    }
    if (!shopeeCategoryDecisionAllowsFinalApproval(candidate)) {
      return `
        <div class="shopee-global-plan-review">
          <strong>Shopee Global 最终计划尚未开放批准</strong>
          <p>${esc(modeText)}。必须先保存当前 revision 的明确类目选择，并且所选类目的官方必填属性完整。</p>
          ${shopeeCategoryDecisionPanel()}
          <p class="channel-category-disabled-reason">
            类目决定未固化前，不显示最终计划批准表单，也不会执行任何渠道写入。
          </p>
        </div>
      `;
    }
    return `
      <form class="shopee-global-plan-review shopee-global-plan-approval-form"
        data-candidate-digest="${esc(digest)}">
        <strong>Shopee Global 计划等待 Kyle 批准</strong>
        <p>${esc(modeText)}。这里只显示官方检查、计数和摘要；不提供默认值或字段编辑。</p>
        ${candidate.mode === "NEW_GLOBAL"
          ? shopeeCategoryDecisionPanel()
          : ""}
        <ul class="shopee-global-plan-checks">${checks}${counts}</ul>
        <p>候选摘要 ${esc(compactDigest(digest))}</p>
        <label class="manual-verification-confirm">
          <input name="confirm_approved_shopee_global_plan"
            type="checkbox" required>
          <span>我 Kyle 已核对上述官方候选，并明确批准当前 Shopee Global 计划。</span>
        </label>
        <button class="button button-secondary" type="submit">
          批准 Shopee Global 计划
        </button>
        <span class="manual-verification-message" role="status"
          aria-live="polite"></span>
      </form>
    `;
  }

  function shopeeGlobalControlCard(control) {
    const ledger = control.dispatch_ledger;
    return `
      <section class="oneclick-shared-control"
        data-oneclick-shared-control="${esc(control.target_label)}">
        <div class="oneclick-shared-control-heading">
          <div>
            <p class="kicker">SHARED CONTROL · NOT A STOREFRONT</p>
            <h5>Shopee Global 准备</h5>
          </div>
          <span class="badge">${esc(oneClickStatusText(control.status))}</span>
        </div>
        <p>${esc(oneClickReasonText(control))}</p>
        <dl class="oneclick-shared-control-facts">
          <div><dt>唯一下一步</dt><dd>${esc(oneClickActionText(control.next_action))}</dd></div>
          <div><dt>阻断/状态原因</dt><dd>${esc(
            control.reason
              ? `${control.reason.category} · ${control.reason.summary_code}`
              : "无阻断",
          )}</dd></div>
          <div><dt>准备命令</dt><dd>${esc(compactDigest(control.digests.prepared_command))}</dd></div>
          <div><dt>官方证明</dt><dd>${esc(compactDigest(control.digests.proof))}</dd></div>
          <div><dt>共享资源</dt><dd>${esc(compactDigest(control.digests.shared_resource))}</dd></div>
          <div><dt>写入次数</dt><dd>${esc(shopeeGlobalWriteCountText(control))}</dd></div>
          ${ledger ? `
            <div><dt>当前阶段</dt><dd>${esc(ledger.stage || "尚未调用")}</dd></div>
          ` : ""}
        </dl>
        ${shopeeGlobalPlanRequired(oneClickProjection())
          ? shopeeGlobalPlanPanel()
          : ""}
      </section>
    `;
  }

  async function refreshShopeeGlobalPlanAfterCategory(identity) {
    shopeeGlobalPlanReview.candidate = null;
    shopeeGlobalPlanReview.approval = null;
    shopeeGlobalPlanReview.approvalCurrent = false;
    shopeeGlobalPlanReview.previewAttempted = false;
    shopeeGlobalPlanReview.error = "";
    await requestShopeeGlobalPlanPreview(identity);
  }

  async function reconcileShopeeCategoryDecision(identity) {
    shopeeCategoryDecisionReview.reconciliationBusy = true;
    shopeeCategoryDecisionReview.previewAttempted = false;
    shopeeCategoryDecisionReview.error = "";
    await requestShopeeCategoryDecisionPreview(identity);
    shopeeCategoryDecisionReview.reconciliationBusy = false;
    const selected = shopeeCategoryDecisionReview.projection?.status
      === "SELECTED";
    if (selected) {
      shopeeCategoryDecisionReview.message =
        "只读核对确认类目决定已经保存；不会再次提交。";
      await refreshShopeeGlobalPlanAfterCategory(identity);
      return true;
    }
    shopeeCategoryDecisionReview.error =
      "类目保存结果仍未确认。禁止再次提交；请继续只读核对服务端决定。";
    return false;
  }

  async function submitShopeeCategoryDecision(form) {
    if (
      shopeeCategoryDecisionReview.submitting
      || shopeeCategoryDecisionReview.postAttempted
      || releaseSubmitting
    ) return;
    const identity = shopeeGlobalPlanIdentity(currentData);
    const projection = shopeeCategoryDecisionReview.projection;
    const option = selectedShopeeCategoryOption();
    const optionsDigest = String(form.dataset.optionsDigest || "");
    const confirmed = (
      form.elements.confirm_channel_category_selection?.checked === true
    );
    const stockConfirmed = (
      form.elements.confirm_seller_stock_quantity?.checked === true
    );
    const conditionConfirmed = (
      form.elements.confirm_condition_and_preorder?.checked === true
    );
    const attributesConfirmed = (
      form.elements.confirm_required_attribute_selections?.checked === true
    );
    const message = form.querySelector(".channel-category-save-message");
    const button = form.querySelector("button[type='submit']");
    if (
      !identity
      || !shopeeCategoryDecisionCanCollectSelection(projection)
      || optionsDigest !== projection.options_digest
      || !oneClickDigest(optionsDigest)
      || !shopeeRequiredAttributesComplete(option)
      || option.category_identity_digest
        !== shopeeCategoryDecisionReview.draftIdentityDigest
      || !projection.brand_options.some((row) => (
        row.brand_identity_digest
        === shopeeCategoryDecisionReview.draftBrandIdentityDigest
      ))
      || !projection.location_options.some((row) => (
        row.location_identity_digest
        === shopeeCategoryDecisionReview.draftLocationIdentityDigest
      ))
      || !confirmed
      || !stockConfirmed
      || !conditionConfirmed
      || !attributesConfirmed
    ) {
      if (message) {
        message.textContent =
          "类目候选、属性状态或确认勾选已变化；请重新读取并核对。";
      }
      return;
    }
    const generation = shopeeCategoryDecisionReview.generation;
    shopeeCategoryDecisionReview.submitting = true;
    shopeeCategoryDecisionReview.postAttempted = true;
    shopeeCategoryDecisionReview.error = "";
    shopeeCategoryDecisionReview.message =
      "正在保存 Kyle 对当前类目与属性树的明确决定…";
    releaseSubmitting = true;
    if (button) button.disabled = true;
    let responseReceived = false;
    try {
      const { response, payload } = await boundedJsonFetch(
        "/api/product-workspace/channel-category-decision",
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            offer_id: identity.offerId,
            target_label: SHOPEE_GLOBAL_CONTROL_TARGET,
            expected_product_revision: identity.revision,
            expected_options_digest: optionsDigest,
            selected_category_identity_digest:
              option.category_identity_digest,
            selected_brand_identity_digest:
              shopeeCategoryDecisionReview.draftBrandIdentityDigest,
            selected_location_identity_digest:
              shopeeCategoryDecisionReview.draftLocationIdentityDigest,
            selected_creation_fact_identity_digest:
              projection.creation_fact_option
                .creation_fact_identity_digest,
            approved_by: "Kyle",
            confirm_channel_category_selection: true,
            confirm_seller_stock_quantity: true,
            confirm_condition_and_preorder: true,
            required_attribute_selections:
              option.missing_required_attributes.map((attribute) => {
                const chosen = shopeeCategoryDecisionReview
                  .requiredAttributeSelections[
                    attribute.attribute_identity_digest
                  ] || {};
                return {
                  attribute_identity_digest:
                    attribute.attribute_identity_digest,
                  selection_kind: attribute.selection_kind,
                  selected_option_identity_digests:
                    attribute.selection_kind === "TEXT"
                      ? []
                      : [...(chosen.optionDigests || [])].sort(),
                  text_value: attribute.selection_kind === "TEXT"
                    ? String(chosen.textValue || "").trim()
                    : null,
                  confirm_attribute_selection: true,
                };
              }),
            confirm_required_attribute_selections: true,
          }),
        },
        ONECLICK_LOCAL_POST_TIMEOUT_MS,
        "Shopee Global 类目决定保存",
      );
      responseReceived = true;
      if (!response.ok || payload.ok === false) {
        const error = new Error(
          payload.error || `服务返回 HTTP ${response.status}`,
        );
        error.status = response.status;
        error.safeNoWrite = Boolean(
          [400, 409].includes(response.status)
          && Array.isArray(payload.external_writes_performed)
          && payload.external_writes_performed.length === 0
        );
        throw error;
      }
      const saved = validateShopeeCategoryDecisionProjection(
        payload,
        identity,
        { persistedResponse: true },
      );
      if (
        !["SELECTED", "RECHECK_REQUIRED"].includes(saved.status)
        || (
          saved.status === "SELECTED"
          && saved.selection.selected_category_identity_digest
            !== option.category_identity_digest
        )
      ) {
        throw oneClickContractError(
          "服务端保存的类目与 Kyle 当前选择不一致，已停止继续。",
        );
      }
      if (generation !== shopeeCategoryDecisionReview.generation) return;
      adoptShopeeCategoryProjection(saved);
      if (saved.status === "RECHECK_REQUIRED") {
        shopeeCategoryDecisionReview.postAttempted = true;
        shopeeCategoryDecisionReview.message =
          "完整决定已保存，正在仅 GET 复核官方属性；不会重复 POST。";
        shopeeCategoryDecisionReview.previewAttempted = false;
        await requestShopeeCategoryDecisionPreview(identity);
        if (
          shopeeCategoryDecisionReview.projection?.status === "SELECTED"
        ) {
          await refreshShopeeGlobalPlanAfterCategory(identity);
        }
      } else {
        shopeeCategoryDecisionReview.message = saved.created
          ? "完整创建决定已固化；正在重新读取最终 Shopee Global 计划。"
          : "已恢复相同的固化创建决定；未创建重复记录。";
        await refreshShopeeGlobalPlanAfterCategory(identity);
      }
    } catch (error) {
      if (generation !== shopeeCategoryDecisionReview.generation) return;
      if (!responseReceived) {
        shopeeCategoryDecisionReview.message =
          "保存响应未收到，正在只读核对；绝不会重复提交。";
        await reconcileShopeeCategoryDecision(identity);
      } else if (error.safeNoWrite === true) {
        shopeeCategoryDecisionReview.message =
          `${friendlyError(error.message)}；服务端确认零渠道写入，正在刷新候选。`;
        shopeeCategoryDecisionReview.postAttempted = false;
        shopeeCategoryDecisionReview.previewAttempted = false;
        await requestShopeeCategoryDecisionPreview(identity);
      } else {
        shopeeCategoryDecisionReview.error =
          `${friendlyError(error.message)} 禁止再次提交；请只读核对类目决定。`;
      }
    } finally {
      if (generation === shopeeCategoryDecisionReview.generation) {
        shopeeCategoryDecisionReview.submitting = false;
        releaseSubmitting = false;
        renderOneClickExecution(currentData);
        renderReleaseRecovery(currentData?.release_v1 || {});
        updateReleaseControls(currentData || {});
      }
    }
  }

  async function submitShopeeGlobalPlanApproval(form) {
    if (
      shopeeGlobalPlanReview.submitting
      || shopeeGlobalPlanReview.approvalPostAttempted
      || releaseSubmitting
    ) return;
    const identity = shopeeGlobalPlanIdentity(currentData);
    const candidate = shopeeGlobalPlanReview.candidate;
    const digest = String(form.dataset.candidateDigest || "");
    const confirmed = (
      form.elements.confirm_approved_shopee_global_plan?.checked === true
    );
    const message = form.querySelector(".manual-verification-message");
    const button = form.querySelector("button[type='submit']");
    if (
      !identity
      || candidate?.status !== "READY"
      || shopeeGlobalPlanReview.approvalCurrent
      || !shopeeCategoryDecisionAllowsFinalApproval(candidate)
      || digest !== candidate.digests.candidate_digest
      || !oneClickDigest(digest)
      || !confirmed
    ) {
      if (message) {
        message.textContent =
          "候选状态已变化或尚未明确勾选；请刷新并重新核对官方候选。";
      }
      return;
    }
    shopeeGlobalPlanReview.submitting = true;
    shopeeGlobalPlanReview.approvalPostAttempted = true;
    releaseSubmitting = true;
    if (button) button.disabled = true;
    if (message) message.textContent = "正在保存 Kyle 对当前候选的不可变批准…";
    updateReleaseControls(currentData || {});
    try {
      const { response, payload } = await boundedJsonFetch(
        "/api/product-workspace/shopee-global-plan-approval",
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            offer_id: identity.offerId,
            expected_product_revision: identity.revision,
            expected_candidate_digest: digest,
            approved_by: "Kyle",
            confirm_approved_shopee_global_plan: true,
          }),
        },
        ONECLICK_LOCAL_POST_TIMEOUT_MS,
        "Shopee Global 计划批准",
      );
      if (!response.ok || payload.ok === false) {
        const error = new Error(
          payload.error || `服务返回 HTTP ${response.status}`,
        );
        error.status = response.status;
        error.payload = payload;
        error.responseOutcomeUnknown = response.status >= 500;
        throw error;
      }
      shopeeGlobalPlanReview.approval =
        validateShopeeGlobalPlanApprovalResponse(
          payload,
          identity,
          candidate,
        );
      shopeeGlobalPlanReview.approvalCurrent = true;
      if (message) message.textContent = "批准已保存，正在重新读取发布状态…";
      window.location.reload();
    } catch (error) {
      if (error.responseOutcomeUnknown === true) {
        shopeeGlobalPlanReview.reconciliationBusy = true;
        shopeeGlobalPlanReview.previewAttempted = false;
        shopeeGlobalPlanReview.error =
          "批准响应未收到，正在只读核对批准是否已经保存；不会再次提交批准。";
        if (message) message.textContent = shopeeGlobalPlanReview.error;
        await requestShopeeGlobalPlanPreview(identity);
        shopeeGlobalPlanReview.reconciliationBusy = false;
        if (shopeeGlobalPlanReview.approvalCurrent) {
          shopeeGlobalPlanReview.error = "";
          oneClickExecution.statusWarning =
            "Shopee Global 计划已通过 GET 对账确认保存；当前页面已稳定结案，不会再次提交。";
          renderOneClickExecution(currentData);
          renderReleaseRecovery(currentData?.release_v1 || {});
          return;
        }
        shopeeGlobalPlanReview.error =
          "批准结果仍未确认。禁止再次提交；请仅重新读取 Shopee Global 计划。";
      } else {
        shopeeGlobalPlanReview.error = friendlyError(error.message);
        if (message) {
          message.textContent =
            `${shopeeGlobalPlanReview.error} 未执行任何渠道写入。`;
        }
      }
    } finally {
      shopeeGlobalPlanReview.submitting = false;
      releaseSubmitting = false;
      if (button?.isConnected) button.disabled = false;
      updateReleaseControls(currentData || {});
    }
  }

  function syncShopeeGlobalPlanApprovalConsent(root = document) {
    const forms = root.matches?.(".shopee-global-plan-approval-form")
      ? [root]
      : root.querySelectorAll(".shopee-global-plan-approval-form");
    forms.forEach(
      (form) => {
        const checkbox = form.querySelector(
          "input[name='confirm_approved_shopee_global_plan']",
        );
        const button = form.querySelector("button[type='submit']");
        if (button) {
          button.disabled = (
            checkbox?.checked !== true
            || shopeeGlobalPlanReview.submitting
            || shopeeGlobalPlanReview.approvalPostAttempted
            || releaseSubmitting
          );
        }
      },
    );
  }

  function updateShopeeGlobalPlanApprovalConsent(event) {
    const checkbox = event.target.closest(
      ".shopee-global-plan-approval-form "
        + "input[name='confirm_approved_shopee_global_plan']",
    );
    if (!checkbox) return false;
    const form = checkbox.closest(".shopee-global-plan-approval-form");
    if (form) syncShopeeGlobalPlanApprovalConsent(form);
    return true;
  }

  function renderOneClickExecution(data) {
    const container = $("#oneClickExecutionGroups");
    const message = $("#oneClickExecutionMessage");
    if (!container || !message) return;
    const identity = oneClickIdentity(data);
    const projection = oneClickProjection();
    const headings = {
      automatic: "等待妙手提交",
      manual: "妙手已接受",
      dependency: "上次未完成",
      preSubmit: "上次发布失败",
      reconciliation: "上次结果未确认",
      blocked: "上次未发布",
      terminal: "妙手已接受",
    };
    const groups = {
      automatic: [],
      manual: [],
      dependency: [],
      preSubmit: [],
      reconciliation: [],
      blocked: [],
      terminal: [],
    };
    for (const target of (projection?.targets || []).filter(
      (candidate) => candidate.storefront === true,
    )) {
      groups[oneClickTargetBucket(target)].push(target);
    }
    container.innerHTML = Object.entries(groups)
      .filter(([, targets]) => targets.length)
      .map(([bucket, targets]) => `
        <section class="oneclick-execution-group oneclick-${esc(bucket)}">
           <h5>${esc(headings[bucket])}</h5>
           <div class="oneclick-target-list">
             ${targets.map((target) => `
               <div class="oneclick-target-control">
                 <article class="oneclick-target-card"
                   data-oneclick-target="${esc(target.target_label)}" tabindex="-1">
                   <strong>${esc(targetDisplayName(target.target_label))}</strong>
                   <span>${esc(oneClickStatusText(target.status))}</span>
                   <small>${esc(oneClickReasonText(target))}</small>
                 </article>
               </div>
             `).join("")}
           </div>
        </section>
      `).join("") || "<p>尚无服务端店铺状态。</p>";

    if (!identity) {
      message.textContent = "批准不可变发布计划后，系统会读取服务端批次预览。";
    } else if (oneClickExecution.previewBusy) {
      message.textContent = "正在读取上次妙手提交结果…";
    } else if (oneClickExecution.posting) {
      message.textContent = "正在向妙手 API 提交所选 TikTok、Shopee 和 Ozon 店铺…";
    } else if (oneClickExecution.error) {
      message.textContent =
        `${oneClickExecution.error} 本次已结束；可以再次点击一键发布。`;
    } else if (oneClickExecution.statusWarning) {
      message.textContent = oneClickExecution.statusWarning;
    } else if (oneClickExecution.job) {
      message.textContent =
        "已显示上一轮妙手提交结果；需要时可再次点击一键发布。";
    } else if (oneClickExecution.preview) {
      const count = Number(
        oneClickExecution.preview.preparation_pending_count || 0,
      );
      const manual = (oneClickExecution.preview.summary?.manual_after_submit || []).length;
      message.textContent =
        `服务端预览完成：${count} 个目标等待后台正式准备，${manual} 个提交后等待人工验收。`;
    } else {
      message.textContent = "等待服务端只读批次预览。";
    }
    if (identity) {
      $("#publishAllNote").textContent =
        "所有所选 TikTok、Shopee 和 Ozon 店铺统一通过妙手 API 提交；"
        + "上一轮失败不会阻止再次显式发布。";
    }
    $("#oneClickExecutionPreview").setAttribute(
      "aria-busy",
      String(Boolean(
        oneClickExecution.previewBusy
        || oneClickExecution.posting
        || oneClickExecution.statusBusy
      )),
    );
    updateReleasePrimaryAction(data);
  }

  function focusOneClickTarget(targetLabel) {
    const label = String(targetLabel || "");
    let target = null;
    if (label) {
      const escaped = CSS.escape(label);
      target = document.querySelector(
        `[data-oneclick-observation-review="${escaped}"] `
        + "[name='manual_review_accepted']",
      )
        || document.querySelector(`[data-oneclick-target="${escaped}"]`)
        || document.querySelector(
          `[data-oneclick-shared-control="${escaped}"]`,
        )
        || document.querySelector(`.run-target[data-target-label="${escaped}"]`);
    }
    target ||= $("#oneClickExecutionPreview");
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.focus({ preventScroll: true });
  }

  async function refreshOneClickDashboard() {
    const identity = oneClickExecution.identity;
    const generation = oneClickExecution.generation;
    if (!identity || oneClickExecution.statusBusy) return;
    oneClickExecution.statusBusy = true;
    renderOneClickExecution(currentData);
    try {
      const latest = await fetchDashboard(
        identity.offerId,
        identity.publicationTargets,
      );
      if (
        generation === oneClickExecution.generation
        && productKey(latest?.product?.offer_id) === identity.offerId
      ) {
        oneClickExecution.resumePostAttempted = false;
        oneClickExecution.failureAction = null;
        adoptWorkflowDashboard(latest);
      }
    } catch (error) {
      if (generation === oneClickExecution.generation) {
        oneClickExecution.statusWarning =
          `重新读取发布状态失败（${friendlyError(error.message)}）；未提交任何发布请求。`;
      }
    } finally {
      if (generation === oneClickExecution.generation) {
        oneClickExecution.statusBusy = false;
        renderOneClickExecution(currentData);
      }
    }
  }

  function focusFirstControl(selectors) {
    for (const selector of selectors) {
      const target = document.querySelector(selector);
      if (!target) continue;
      if (
        !target.matches(
          "button,input,select,textarea,a[href],[tabindex]",
        )
      ) {
        target.setAttribute("tabindex", "-1");
      }
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.focus({ preventScroll: true });
      return true;
    }
    return false;
  }

  async function routeOneClickNextAction(button) {
    const action = String(button.dataset.oneclickAction || "");
    const targetLabel = String(button.dataset.oneclickTargetFocus || "");
    if (!ONECLICK_ACTIONS.has(action)) {
      oneClickExecution.error =
        "服务端返回了未知下一步，系统已停止操作；请重新读取发布状态。";
      renderOneClickExecution(currentData);
      return;
    }
    if (action === "refresh_release_state") {
      await refreshOneClickDashboard();
      return;
    }
    if (action === "review_shopee_global_plan") {
      await ensureShopeeGlobalPlanReview(
        shopeeGlobalPlanIdentity(currentData),
        true,
      );
      renderOneClickExecution(currentData);
      if (!focusFirstControl([
        ".channel-category-decision-form select[name='selected_category_identity_digest']",
        ".channel-category-decision-form input[name='confirm_channel_category_selection']",
        ".channel-category-attributes-next",
        ".shopee-global-plan-approval-form input[name='confirm_approved_shopee_global_plan']",
        ".shopee-global-plan-preview-retry",
        ".shopee-global-auth-restore",
      ])) {
        focusOneClickTarget(SHOPEE_GLOBAL_CONTROL_TARGET);
      }
      return;
    }
    if (
      [
        "retry_exact_zero_write_action",
        "perform_governed_safe_action",
      ].includes(action)
    ) {
      await resumeExactZeroWriteFailures();
      return;
    }
    if (action === "verify_submission_in_marketplace") {
      if (!focusFirstControl([
        `.run-target[data-target-label="${CSS.escape(targetLabel)}"] `
          + ".manual-verification-form input[name='marketplace_product_id']",
      ])) {
        oneClickExecution.statusWarning =
          "该目标等待人工验收，但当前页面没有可填写的验收表单；请重新读取发布状态。";
        focusOneClickTarget(targetLabel);
        renderOneClickExecution(currentData);
      }
      return;
    }
    if (action === "review_verified_observation_warning") {
      if (!focusFirstControl([
        `[data-oneclick-observation-review="${CSS.escape(targetLabel)}"] `
          + "input[name='manual_review_accepted']",
      ])) {
        oneClickExecution.statusWarning =
          "Shopee 观察警告验收控件尚未就绪；请重新读取任务状态。";
        focusOneClickTarget(targetLabel);
        renderOneClickExecution(currentData);
      }
      return;
    }
    if (action === "reconcile_before_any_retry") {
      const escaped = CSS.escape(targetLabel);
      if (!focusFirstControl([
        `[data-target-scoped-target="${escaped}"] `
          + "[data-target-scoped-action='preview']",
      ])) {
        oneClickExecution.statusWarning =
          "该目标当前没有可用的只读对账入口；系统不会重发，请先刷新状态或等待受治理对账能力。";
        focusOneClickTarget(targetLabel);
        renderOneClickExecution(currentData);
      }
      return;
    }
    if (action === "restore_channel_authorization") {
      if (targetLabel.startsWith("shopee:")) {
        const focused = focusFirstControl([
          ".shopee-global-auth-restore",
          ".shopee-global-plan-preview-retry",
        ]);
        if (!focused) focusOneClickTarget(SHOPEE_GLOBAL_CONTROL_TARGET);
        oneClickExecution.statusWarning =
          "请在 Shopee 授权管理入口恢复当前店铺与 Global 官方读取授权，完成后只点“重新读取发布条件”；这里不会刷新凭据或提交发布。";
      } else {
        focusOneClickTarget(targetLabel);
        oneClickExecution.statusWarning =
          `${targetDisplayName(targetLabel)} 需要在对应平台的授权管理入口恢复授权；完成后只点“重新读取发布条件”，这里不会刷新凭据或提交发布。`;
      }
      renderOneClickExecution(currentData);
      return;
    }
    const contentSelectors = {
      review_approved_content_facts: [
        "#listingCopyAssistant",
        "#content",
        "#productFactsPanel",
      ],
      review_logistics_policy: ["#productFactsPanel"],
      resolve_source_product_identity: ["#productFactsPanel"],
      resolve_predecessor_sku_lineage: ["#productFactsPanel"],
      resolve_plan_or_source_identity: ["#releasePlan", "#productFactsPanel"],
      approve_sellable_inventory: ["#releasePlan", "#productFactsPanel"],
    };
    if (contentSelectors[action]) {
      if (!focusFirstControl(contentSelectors[action])) {
        focusOneClickTarget(targetLabel);
      }
      return;
    }
    focusOneClickTarget(targetLabel);
  }

  async function resumeExactZeroWriteFailures() {
    const identity = oneClickExecution.identity;
    const reference = oneClickExecution.job;
    if (
      !identity
      || !reference
      || releaseSubmitting
      || oneClickExecution.resumePostAttempted
    ) return;
    const generation = oneClickExecution.generation;
    oneClickExecution.resumePostAttempted = true;
    releaseSubmitting = true;
    oneClickExecution.posting = true;
    oneClickExecution.error = "";
    renderOneClickExecution(currentData);
    updateReleaseControls(currentData || {});
    try {
      const { response, payload } = await boundedJsonFetch(
        "/api/product-workspace/publish",
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
          },
          body: JSON.stringify(currentReleaseBody({
            confirm_publish: true,
            resume_exact_zero_write_failures: true,
          })),
        },
        ONECLICK_LOCAL_POST_TIMEOUT_MS,
        "零写入失败安全恢复",
      );
      if (response.status !== 202 || !response.ok || payload.ok === false) {
        const error = new Error(
          payload.error || `服务返回 HTTP ${response.status}`,
        );
        error.responseOutcomeUnknown = response.status >= 500;
        throw error;
      }
      const job = validateOneClickProjection(
        payload.job,
        identity,
        ONECLICK_STATUS_SCHEMA,
        reference,
      );
      if (generation !== oneClickExecution.generation) return;
      oneClickExecution.job = job;
      oneClickExecution.statusWarning =
        "受治理的零写入恢复已受理；正在只读轮询同一持久任务。";
      scheduleOneClickStatusPoll(generation, 0);
    } catch (error) {
      if (generation !== oneClickExecution.generation) return;
      if (error.responseOutcomeUnknown === true) {
        oneClickExecution.statusWarning =
          "安全恢复响应未收到；正在只读核对同一任务，绝不再次提交。";
        await pollOneClickStatus(generation);
      } else {
        oneClickExecution.error = friendlyError(error.message);
        oneClickExecution.failureAction = {
          action: "refresh_release_state",
          target_focus: null,
          runnable: false,
        };
      }
    } finally {
      if (generation === oneClickExecution.generation) {
        releaseSubmitting = false;
        oneClickExecution.posting = false;
        renderOneClickExecution(currentData);
        updateReleaseControls(currentData || {});
      }
    }
  }

  async function retryOneClickReadOnly() {
    if (
      oneClickExecution.previewBusy
      || oneClickExecution.statusBusy
      || oneClickExecution.acceptanceCheckBusy
    ) return;
    const generation = oneClickExecution.generation;
    if (oneClickExecution.postAttempted && !oneClickExecution.job) {
      await reconcileOneClickAcceptance(generation);
      return;
    }
    if (oneClickExecution.job) {
      await pollOneClickStatus(generation);
      return;
    }
    oneClickExecution.previewAttempted = false;
    oneClickExecution.error = "";
    oneClickExecution.failureAction = null;
    await requestOneClickPreview(generation);
  }

  function scheduleOneClickStatusPoll(generation, delay = ONECLICK_POLL_INTERVAL_MS) {
    cancelOneClickTimer();
    if (
      generation !== oneClickExecution.generation
      || !oneClickExecution.job
      || ONECLICK_TERMINAL_PHASES.has(oneClickExecution.job.phase)
    ) return;
    oneClickExecution.timer = window.setTimeout(
      () => pollOneClickStatus(generation),
      delay,
    );
  }

  async function refreshDashboardAfterOneClickTerminal(generation) {
    if (
      generation !== oneClickExecution.generation
      || oneClickExecution.finalDashboardRefreshed
      || !oneClickExecution.identity
    ) return;
    oneClickExecution.finalDashboardRefreshed = true;
    const identity = oneClickExecution.identity;
    try {
      const latest = await fetchDashboard(
        identity.offerId,
        identity.publicationTargets,
      );
      if (
        generation === oneClickExecution.generation
        && productKey(latest?.product?.offer_id) === productKey(identity.offerId)
      ) {
        adoptWorkflowDashboard(latest);
      }
    } catch (_error) {
      if (generation === oneClickExecution.generation) {
        oneClickExecution.statusWarning =
          "任务已终止；最终本地账本刷新暂时失败，请稍后只读刷新页面。";
        renderOneClickExecution(currentData);
      }
    }
  }

  async function reconcileOneClickAcceptance(generation) {
    if (
      generation !== oneClickExecution.generation
      || oneClickExecution.acceptanceCheckBusy
      || !oneClickExecution.identity
      || oneClickExecution.job
    ) return;
    oneClickExecution.acceptanceCheckBusy = true;
    oneClickExecution.statusWarning = "";
    oneClickExecution.error =
      "发布响应未收到，正在只读核对服务端是否已经受理；绝不自动重发。";
    const identity = oneClickExecution.identity;
    const controller = new AbortController();
    oneClickExecution.controller = controller;
    renderOneClickExecution(currentData);
    try {
      const params = new URLSearchParams({ plan_id: identity.planId });
      const { response, payload } = await boundedJsonFetch(
        `/api/product-workspace/publish-status?${params}`,
        {
          headers: { Accept: "application/json" },
          controller,
        },
        ONECLICK_LOCAL_READ_TIMEOUT_MS,
        "统一发布受理状态只读核对",
      );
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || `服务返回 HTTP ${response.status}`);
      }
      const job = validateOneClickProjection(
        payload.job,
        identity,
        ONECLICK_STATUS_SCHEMA,
        oneClickExecution.preview,
      );
      if (generation !== oneClickExecution.generation) return;
      oneClickExecution.job = job;
      oneClickExecution.error = "";
      oneClickExecution.statusWarning = "";
      renderOneClickExecution(currentData);
      updateReleaseControls(currentData || {});
      if (ONECLICK_TERMINAL_PHASES.has(job.phase)) {
        await refreshDashboardAfterOneClickTerminal(generation);
      } else {
        scheduleOneClickStatusPoll(generation, 0);
      }
    } catch (error) {
      if (
        error.name === "AbortError"
        || generation !== oneClickExecution.generation
      ) return;
      oneClickExecution.error =
        `仍无法确认发布请求是否已受理（${friendlyError(error.message)}）。禁止再次发布，只能继续只读核对。`;
    } finally {
      if (generation === oneClickExecution.generation) {
        oneClickExecution.acceptanceCheckBusy = false;
        if (oneClickExecution.controller === controller) {
          oneClickExecution.controller = null;
        }
        renderOneClickExecution(currentData);
        updateReleaseControls(currentData || {});
      }
    }
  }

  async function pollOneClickStatus(generation) {
    if (
      generation !== oneClickExecution.generation
      || oneClickExecution.statusBusy
      || !oneClickExecution.identity
      || !oneClickExecution.job
    ) return;
    oneClickExecution.statusBusy = true;
    oneClickExecution.statusWarning = "";
    const identity = oneClickExecution.identity;
    const reference = oneClickExecution.job;
    const controller = new AbortController();
    oneClickExecution.controller = controller;
    renderOneClickExecution(currentData);
    try {
      const params = new URLSearchParams({
        job_id: reference.job_id,
        plan_id: identity.planId,
      });
      const { response, payload } = await boundedJsonFetch(
        `/api/product-workspace/publish-status?${params}`,
        {
          headers: { Accept: "application/json" },
          controller,
        },
        ONECLICK_LOCAL_READ_TIMEOUT_MS,
        "统一发布任务状态读取",
      );
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || `服务返回 HTTP ${response.status}`);
      }
      const job = validateOneClickProjection(
        payload.job,
        identity,
        ONECLICK_STATUS_SCHEMA,
        reference,
      );
      if (job.job_id !== reference.job_id) {
        throw oneClickContractError(
          "统一发布任务身份已漂移，已停止轮询。",
        );
      }
      if (generation !== oneClickExecution.generation) return;
      oneClickExecution.job = job;
      oneClickExecution.error = "";
      renderOneClickExecution(currentData);
      updateReleaseControls(currentData || {});
      if (ONECLICK_TERMINAL_PHASES.has(job.phase)) {
        await refreshDashboardAfterOneClickTerminal(generation);
      } else {
        scheduleOneClickStatusPoll(generation);
      }
    } catch (error) {
      if (error.name === "AbortError" || generation !== oneClickExecution.generation) {
        return;
      }
      if (error.oneClickContractError === true) {
        cancelOneClickTimer();
        oneClickExecution.error = friendlyError(error.message);
        oneClickExecution.statusWarning = "";
        oneClickExecution.failureAction = {
          action: "refresh_release_state",
          target_focus: null,
          runnable: false,
        };
        renderOneClickExecution(currentData);
        updateReleaseControls(currentData || {});
        return;
      }
      oneClickExecution.statusWarning =
        `任务状态读取暂时失败（${friendlyError(error.message)}）；系统不会再次提交，正在只读重试。`;
      renderOneClickExecution(currentData);
      scheduleOneClickStatusPoll(generation);
    } finally {
      if (generation === oneClickExecution.generation) {
        oneClickExecution.statusBusy = false;
        if (oneClickExecution.controller === controller) {
          oneClickExecution.controller = null;
        }
        renderOneClickExecution(currentData);
      }
    }
  }

  async function requestOneClickPreview(generation) {
    if (
      generation !== oneClickExecution.generation
      || oneClickExecution.previewBusy
      || oneClickExecution.previewAttempted
      || !oneClickExecution.identity
      || oneClickExecution.job
    ) return;
    oneClickExecution.previewAttempted = true;
    oneClickExecution.previewBusy = true;
    oneClickExecution.error = "";
    oneClickExecution.failureAction = null;
    const identity = oneClickExecution.identity;
    const controller = new AbortController();
    oneClickExecution.controller = controller;
    renderOneClickExecution(currentData);
    updateReleaseControls(currentData || {});
    try {
      const params = new URLSearchParams({
        offer_id: identity.offerId,
        plan_id: identity.planId,
      });
      const { response, payload } = await boundedJsonFetch(
        `/api/product-workspace/publish-preview?${params}`,
        {
          headers: { Accept: "application/json" },
          controller,
        },
        ONECLICK_LOCAL_READ_TIMEOUT_MS,
        "统一发布条件只读预览",
      );
      if (!response.ok || payload.ok === false) {
        const error = new Error(payload.error || `服务返回 HTTP ${response.status}`);
        error.payload = payload;
        throw error;
      }
      if (
        payload.persisted !== false
        || !Array.isArray(payload.external_writes_performed)
        || payload.external_writes_performed.length
      ) {
        throw new Error("只读发布预览返回了不安全的写入证据。");
      }
      const preview = validateOneClickProjection(
        payload.preview,
        identity,
        ONECLICK_PREVIEW_SCHEMA,
      );
      if (generation !== oneClickExecution.generation) return;
      oneClickExecution.preview = preview;
    } catch (error) {
      if (error.name === "AbortError" || generation !== oneClickExecution.generation) {
        return;
      }
      oneClickExecution.error = friendlyError(error.message);
      oneClickExecution.failureAction = error.payload?.canonical_next_action || null;
    } finally {
      if (generation === oneClickExecution.generation) {
        oneClickExecution.previewBusy = false;
        if (oneClickExecution.controller === controller) {
          oneClickExecution.controller = null;
        }
        renderOneClickExecution(currentData);
        updateReleaseControls(currentData || {});
      }
    }
  }

  function ensureOneClickExecution(data) {
    const identity = oneClickIdentity(data);
    if (!identity) {
      if (oneClickExecution.contextKey) resetOneClickExecution();
      return;
    }
    if (oneClickExecution.contextKey !== identity.key) {
      resetOneClickExecution();
      oneClickExecution.contextKey = identity.key;
      oneClickExecution.identity = identity;
    } else {
      oneClickExecution.identity = identity;
    }
    const generation = oneClickExecution.generation;
    const serverJob = data?.release_v1?.oneclick_controlplane;
    if (serverJob && !oneClickExecution.job) {
      try {
        oneClickExecution.job = validateOneClickProjection(
          serverJob,
          identity,
          ONECLICK_STATUS_SCHEMA,
        );
      } catch (error) {
        oneClickExecution.error = friendlyError(error.message);
        oneClickExecution.failureAction =
          data?.release_v1?.canonical_next_action || null;
        return;
      }
    }
    if (oneClickExecution.job) {
      if (!ONECLICK_TERMINAL_PHASES.has(oneClickExecution.job.phase)) {
        scheduleOneClickStatusPoll(generation, 0);
      }
      return;
    }
    requestOneClickPreview(generation);
  }

  async function postProductWorkspace(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({
      ok: false,
      error: `服务返回 HTTP ${response.status}`,
    }));
    if (!response.ok || payload.ok === false) {
      const error = new Error(payload.error || `服务返回 HTTP ${response.status}`);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function dashboardFromPayload(payload) {
    if (payload?.product) return payload;
    if (payload?.dashboard?.product) return payload.dashboard;
    if (payload?.data?.product) return payload.data;
    return null;
  }

  async function collectProduct(offerId) {
    const payload = await postProductWorkspace("/api/product-workspace/collect", {
      offer_id: offerId,
    });
    return dashboardFromPayload(payload) || fetchDashboard(offerId);
  }

  function missingProductError(error) {
    return error?.status === 404
      || String(error?.message || "").includes("required release evidence not found");
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
      && product.fact_evidence?.ready !== false
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
    const release = data.release_v1 || {};
    const planReady = Boolean(release.plan_approved);
    const imageSyncReady = Boolean(release.miaoshou_prepared);
    const runTargets = release.run?.targets || [];
    const channelTargets = runTargets.filter(
      (target) => target.target_label !== "miaoshou:COMMON",
    );
    const runCounts = releaseRunCounts(release.run);
    const runGroups = releaseTargetGroups(release.run);
    const runStarted = Boolean(release.run && runTargets.length);
    const channelExecutionReady = Boolean(
      runStarted
      && channelTargets.length
      && channelTargets.every((target) => (
        !["RUNNING", "PENDING"].includes(target.status)
      )),
    );
    let channelWaitText = "待执行";
    let channelReadyText = "执行已结束";
    if (runStarted && runGroups.running.length) {
      channelWaitText =
        `执行中 · ${runCounts.succeeded}/${runCounts.total} 已完成`;
    } else if (
      runStarted
      && (
        runGroups.draftVerify.length
        || runGroups.draftConflict.length
        || runGroups.reconcileOnly.length
        || runGroups.unsafeFailure.length
        || runGroups.safeRetry.length
        || runGroups.manualVerify.length
      )
    ) {
      channelReadyText = "部分完成 · 需对账";
      channelWaitText = `${runCounts.succeeded}/${runCounts.total} 个店铺发布完成 · 需处置`;
    } else if (runStarted && runGroups.pending.length) {
      channelWaitText = "运行已创建 · 等待继续";
    } else if (runStarted) {
      channelWaitText = "执行已开始";
    }
    const reconciliationReady = Boolean(
      ["SUCCEEDED", "COMPLETED_WITH_MANUAL_VERIFICATION"].includes(
        release.run?.status,
      )
      && runTargets.length
      && runTargets.every(
        (target) => ["SUCCEEDED", "MANUALLY_VERIFIED"].includes(target.status),
      ),
    );

    const raw = [
      { key: "product", label: "商品事实", ready: productReady, readyText: "证据完整", waitText: "待核对" },
      { key: "content", label: "内容审批", ready: contentReady, readyText: "已批准", waitText: "待审核" },
      { key: "approval", label: "商品审批", ready: approvalReady, readyText: "已锁定", waitText: "待批准" },
      { key: "plan", label: "发布计划", ready: planReady, readyText: "已批准", waitText: "待批准" },
      { key: "sync", label: "妙手待发布", ready: imageSyncReady, readyText: "回读一致", waitText: "待同步" },
      {
        key: "channels",
        label: "渠道执行",
        ready: channelExecutionReady,
        readyText: channelReadyText,
        waitText: channelWaitText,
      },
      {
        key: "reconcile",
        label: "回读对账",
        ready: reconciliationReady,
        readyText: "全部一致",
        waitText: runCounts.draftVerify
          ? `${runCounts.draftVerify} 个草稿待核验后提交`
          : (
            runCounts.draftConflict
              ? `${runCounts.draftConflict} 个草稿版本冲突`
              : (
                runCounts.reconcileOnly
                  ? `${runCounts.reconcileOnly} 个结果待对账`
                  : (
                    runCounts.awaitingReadback
                      ? `${runCounts.awaitingReadback} 个待人工验收`
                      : (
                        runCounts.safeRetry
                          ? `${runCounts.safeRetry} 个修复后可重试`
                          : "待对账"
                      )
                  )
              )
          ),
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
        stage: item.activity || "正在读取最新本地证据",
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
      const key = productKey(item.offer_id);
      const summary = queueSummary(item);
      const isCurrent = key === currentQueueKey;
      const title = item.data?.product?.title || `Offer ${item.offer_id}`;
      const sellerSku = (
        item.data?.product?.seller_sku_candidate
        || item.seller_sku
        || "待系统分配"
      );
      const thumbnail = item.data?.product?.thumbnail || {};
      const thumbnailUrl = String(
        thumbnail.url
        || item.data?.content?.images?.[0]?.image_url
        || "",
      ).trim();
      const thumbnailProxy = thumbnailUrl
        ? `/api/proxy-image?url=${encodeURIComponent(thumbnailUrl)}`
        : "";
      const thumbnailLabel = thumbnail.approved ? "已批准主图" : "来源预览";
      return `
        <article class="queue-card${isCurrent ? " current" : ""}${item.loading ? " is-loading" : ""}"
                 data-key="${esc(key)}">
          <div class="queue-main">
            <div class="queue-thumbnail">
              ${thumbnailProxy ? `
                <img src="${esc(thumbnailProxy)}" alt="${esc(title)} 主图" loading="lazy" data-queue-image>
                <span>${esc(thumbnailLabel)}</span>
              ` : `
                <div class="queue-thumbnail-placeholder">
                  <strong>${item.loading ? "读取中" : "暂无主图"}</strong>
                  <small>${item.error ? "来源读取失败" : "刷新后自动补图"}</small>
                </div>
              `}
            </div>
            <div class="queue-copy">
              <header>
                <h3 title="${esc(title)}">${esc(title)}</h3>
                <span class="badge ${summary.tone}">${isCurrent ? "当前商品" : "队列中"}</span>
              </header>
              <div class="queue-identity">
                <span>Offer ${esc(item.offer_id)}</span>
                <span>Seller SKU ${esc(sellerSku)}</span>
              </div>
              <p class="queue-stage">${esc(summary.stage)}</p>
            </div>
          </div>
          <div class="queue-metrics">
            <div><span>阻塞</span><strong>${esc(summary.blockers)}</strong></div>
            <div><span>内容图</span><strong>${esc(summary.images)}</strong></div>
            <div><span>审批</span><strong>${esc(summary.approval)}</strong></div>
          </div>
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
    grid.querySelectorAll("img[data-queue-image]").forEach((image) => {
      image.addEventListener("error", () => {
        const frame = image.closest(".queue-thumbnail");
        frame.innerHTML = `
          <div class="queue-thumbnail-placeholder">
            <strong>主图不可用</strong>
            <small>商品证据仍保留</small>
          </div>
        `;
      }, { once: true });
    });
  }

  function syncCurrentUrl(item) {
    const url = new URL(window.location.href);
    url.searchParams.set("offer_id", item.offer_id);
    url.searchParams.delete("seller_sku");
    history.replaceState(null, "", url);
  }

  function addToQueue(offerId, { select = true } = {}) {
    const cleanOffer = String(offerId || "").trim();
    if (!validOfferId(cleanOffer)) return null;
    const key = productKey(cleanOffer);
    let item = queueItem(key);
    if (!item) {
      if (queueItems.length >= MAX_QUEUE_ITEMS) {
        $("#queueMessage").textContent = `队列最多保存 ${MAX_QUEUE_ITEMS} 件商品。`;
        return null;
      }
      item = {
        offer_id: cleanOffer,
        seller_sku: "",
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
    const skuGovernance = product.seller_sku_governance || {};
    const evidence = product.fact_evidence || {};
    $("#productTitle").textContent = product.title || "未命名商品";
    $("#productIdentity").innerHTML = [
      `Offer ${esc(product.offer_id || "—")}`,
      `1688 来源 ${esc(product.source_offer_id || "—")}`,
      `revision ${esc(product.revision ?? "—")}`,
    ].map((item) => `<span>${item}</span>`).join("");

    const factsReady = productFactsReady(product);
    const factsApproved = Boolean(
      product.actual_product_approved && product.fields_locked,
    );
    const hasFactWarnings = Boolean((evidence.warnings || []).length);
    setBadge(
      $("#factsBadge"),
      factsApproved
        ? "Kyle 已批准并锁定"
        : (
          factsReady
            ? (hasFactWarnings ? "有提醒 · 可审批" : "证据完整 · 待 Kyle 核对")
            : "事实存在冲突"
        ),
      factsApproved ? "safe" : "warn",
    );
    const evidenceBlockers = (evidence.blockers || []).map(translateBlocker);
    const evidenceWarnings = (evidence.warnings || []).map(translateBlocker);
    const factAttention = [...evidenceBlockers, ...evidenceWarnings];
    $("#factsNotice").textContent = factsApproved
      ? "这些值已由 Kyle 批准并锁定；商业字段若要修改，必须显式废止旧审批和旧发布计划。"
      : (
        factAttention.length
          ? `当前是前序采集值，尚未批准。发现 ${factAttention.length} 项需要留意：${factAttention.join("；")} 这些提醒不会阻止 Kyle 锁定当前 revision。`
          : "当前是带来源的前序采集值，尚未成为不可更改的正式事实；请核对后再由 Kyle 批准锁定。"
      );
    const category = typeof product.category === "object"
      ? (product.category?.name || Object.values(product.category || {}).filter(Boolean).join(" / "))
      : product.category;
    const facts = [
      [
        "SKU 占用审查",
        skuGovernance.available
          ? "目录与预留均未占用"
          : (
            `已被 ${skuGovernance.reservation_conflicts?.length || 0} 条旧流程占用`
            + (skuGovernance.suggested_base_sku
              ? ` · 建议改用 ${skuGovernance.suggested_base_sku}`
              : "")
          ),
      ],
      [
        "建议连续 SKU",
        (skuGovernance.suggested_sku_range || []).join(" → ") || "—",
      ],
      ["商品类目", category || "—"],
      ["目标站点", (product.selected_sites || []).map((site) => siteNames[site] || site).join(" · ") || "—"],
      [
        "当前规格价格证据",
        (evidence.selected_sku_prices || []).map(
          (row) => `${row.label || row.selected_key}: ¥${row.price_cny ?? "—"}`,
        ).join(" · ") || "—",
        true,
      ],
    ];
    const factsElement = $("#productFacts");
    factsElement.classList.remove("skeleton-lines");
    factsElement.innerHTML = facts.map(([label, value, wide]) => `
      <div class="fact${wide ? " wide" : ""}">
        <span>${esc(label)}</span>
        <strong>${esc(value)}</strong>
      </div>
    `).join("");
    renderFactsEditor(data);

    const ready = Boolean(data.actual_release_gate?.ready);
    $("#readinessLabel").textContent = ready ? "发布前条件" : "当前状态";
    $("#readinessValue").textContent = ready ? "已就绪" : "待完成";
    $("#readinessNote").textContent = ready
      ? "可以进入受控渠道流程"
      : `${(data.actual_release_gate?.blockers || []).length} 项关键条件待处理`;
  }

  function sourceSkuOptions(product) {
    const evidence = product.fact_evidence || {};
    const raw = (
      product.source_skus
      || product.source_sku_options
      || product.available_skus
      || evidence.source_skus
      || []
    );
    const selected = new Set((product.selected_sku_keys || []).map(String));
    const options = [];
    const seen = new Set();
    (Array.isArray(raw) ? raw : []).forEach((row) => {
      const source = typeof row === "object" && row !== null ? row : { key: row };
      const key = String(
        source.key
        ?? source.sku_key
        ?? source.selected_key
        ?? source.id
        ?? "",
      ).trim();
      if (!key || seen.has(key)) return;
      seen.add(key);
      options.push({
        key,
        label: String(
          source.label
          ?? source.name
          ?? source.spec
          ?? source.title
          ?? key,
        ).trim() || key,
        source_label: String(
          source.source_label
          ?? source.name
          ?? source.spec
          ?? source.title
          ?? source.label
          ?? key,
        ).trim() || key,
        price_cny: source.price_cny ?? source.price ?? source.cost_cny ?? null,
      });
    });
    (evidence.selected_sku_prices || []).forEach((row) => {
      const key = String(row.selected_key ?? row.key ?? "").trim();
      if (!key || seen.has(key)) return;
      seen.add(key);
      options.push({
        key,
        label: String(row.label || key),
        source_label: String(row.source_label || row.label || key),
        price_cny: row.price_cny ?? null,
      });
    });
    selected.forEach((key) => {
      if (seen.has(key)) return;
      seen.add(key);
      options.push({
        key,
        label: key,
        source_label: key,
        price_cny: null,
      });
    });
    return options;
  }

  function renderFactsEditor(data) {
    const product = data.product || {};
    const evidenceFields = product.fact_evidence?.fields || {};
    const sourceFor = (field) => evidenceFields[field]?.selected_source || "未记录";
    const dimensions = Array.isArray(product.package_cm) ? product.package_cm : [];
    const selected = new Set((product.selected_sku_keys || []).map(String));
    const locked = Boolean(product.actual_product_approved || product.fields_locked);
    const form = $("#productFactsForm");
    form.dataset.revision = String(product.revision ?? "");
    form.dataset.locked = locked ? "true" : "false";
    $("#factsEditRevision").textContent = `revision ${product.revision ?? "—"}`;
    $("#factsEditTitle").value = product.title || "";
    $("#factsEditSellerSku").value = product.seller_sku_candidate || "";
    $("#factsEditCost").value = product.cost_cny ?? "";
    $("#factsEditWeight").value = product.weight_kg ?? "";
    $("#factsEditLength").value = dimensions[0] ?? "";
    $("#factsEditWidth").value = dimensions[1] ?? "";
    $("#factsEditHeight").value = dimensions[2] ?? "";
    $("#factsEditTitleSource").textContent = product.source_title_zh
      ? `中文来源标题：${product.source_title_zh}`
      : `来源：${sourceFor("title")}`;
    $("#factsEditCostSource").textContent = `来源：${sourceFor("cost_cny")}`;
    $("#factsEditWeightSource").textContent = `来源：${sourceFor("weight_kg")}`;
    $("#factsEditPackageSource").textContent = `来源：${sourceFor("package_cm")}`;

    const options = sourceSkuOptions(product);
    $("#productSpecGrid").innerHTML = options.length
      ? options.map((option, index) => {
        const price = Number(option.price_cny);
        const priceLabel = Number.isFinite(price)
          ? `采购价 ¥${money(price)}`
          : "来源价待核对";
        return `
          <div class="source-spec-option">
            <label class="source-spec-selector">
              <input type="checkbox" name="selected_sku_key"
                     value="${esc(option.key)}" ${selected.has(option.key) ? "checked" : ""}>
              <span>
                <strong>${esc(option.source_label)}</strong>
                <small>${esc(option.key)} · ${esc(priceLabel)}</small>
              </span>
            </label>
            <label class="source-spec-name">
              <span>发布规格名称</span>
              <input class="sku-label-input" type="text" maxlength="50"
                     data-sku-key="${esc(option.key)}"
                     data-source-label="${esc(option.source_label)}"
                     value="${esc(option.label)}"
                     aria-label="${esc(`编辑规格名称：${option.source_label}`)}"
                     ${selected.has(option.key) ? "" : "disabled"}>
              <small>保留来源规格键和采购价，只修改各平台显示名称。</small>
            </label>
          </div>
        `;
      }).join("")
      : '<span class="source-spec-empty">当前来源没有可选规格，请重新采集后再保存。</span>';
    $("#factsEditMessage").textContent = locked
      ? "当前 revision 已审批锁定。如需修改，必须先显式废止旧审批与发布计划。"
      : `正在编辑 revision ${product.revision ?? "—"}；保存后会生成下一版并重新运行售价与发布预检。`;
    updateFactsEditControls();
  }

  function renderTitleDraft(data) {
    const draft = data.listing_copy || {};
    const candidates = Array.isArray(draft.candidates) ? draft.candidates : [];
    const product = data.product || {};
    const locked = Boolean(product.actual_product_approved || product.fields_locked);
    const approvedAndLocked = Boolean(
      product.actual_product_approved && product.fields_locked
    );
    const stale = draft.status === "superseded_product_facts_changed";
    const adopted = draft.status === "adopted_in_product_facts";
    const release = data.release_v1 || {};
    const releasePlan = release.plan || {};
    const releasePlanRevision = Number(releasePlan.payload?.product_revision);
    const currentPlanApproved = Boolean(
      release.plan_approved
      && !release.historical
      && Number.isInteger(releasePlanRevision)
      && releasePlanRevision === Number(product.revision)
    );
    const currentSignature = String(draft.current_input_signature || "").trim();
    const candidateSignature = String(draft.input_signature || "").trim();
    const candidateCurrent = Boolean(
      candidateSignature
      && currentSignature
      && candidateSignature === currentSignature
    );
    const button = $("#generateTitleDraftButton");
    const missingCandidate = !String(draft.semantic_master_en || "").trim();
    const canRecoverLockedCandidate = locked && (stale || missingCandidate);
    button.disabled =
      (locked && !canRecoverLockedCandidate) || titleDraftSubmitting || pageLoading;
    button.classList.toggle("is-loading", titleDraftSubmitting);
    if (!draft.semantic_master_en) {
      $("#titleDraftStatus").textContent =
        locked
          ? (
            "当前商品事实已锁定，但缺少平台英文文案候选。"
            + "可在 Kyle 确认后生成本地候选；这不会写妙手或任何渠道，"
            + "采用候选时才会安全废止旧审批与旧发布计划。"
          )
          : "尚未生成。点击后由 ToAPI 文本模型按平台特点优化本地候选，不会写妙手或任何平台。";
      $("#titleCandidateGrid").innerHTML = "";
      return;
    }
    const adoptedApprovedStatus = currentPlanApproved
      ? (
        `EN MASTER 已采用且当前商品审批 / 事实锁有效；当前 ReleasePlan `
        + `${releasePlan.plan_id || "（未记录 ID）"} 已批准并绑定 revision ${product.revision ?? "—"}`
      )
      : (
        "EN MASTER 已采用且当前商品审批 / 事实锁有效；"
        + (
          candidateCurrent
            ? "候选签名与当前商品事实一致，等待建立或批准当前 ReleasePlan"
            : "候选签名状态待复核，请在发布前重新检查当前 ReleasePlan"
        )
      );
    const status = adopted && approvedAndLocked
      ? adoptedApprovedStatus
      : (adopted
        ? "已采用到当前商品事实；旧审批与旧发布计划已废止，等待重新核对并批准"
      : (stale
        ? "候选与当前商品事实不匹配，不能采用"
        : (locked
          ? "候选待 Kyle 显式采用；采用会废止旧审批、旧发布计划和未完成运行"
          : "候选待 Kyle 采用")));
    $("#titleDraftStatus").textContent =
      `${status} · 模型 ${draft.model || "未记录"} · 规则 ${draft.policy_version || "未记录"} · 输入签名 ${(draft.input_signature || "").slice(0, 16)}`;
    const shopeeDescription = String(draft.shopee_description_en || "").trim();
    const rows = [
      {
        channel: "商品主数据",
        site: "EN MASTER",
        language: "English",
        title: draft.semantic_master_en,
        master: true,
      },
      ...candidates,
    ];
    $("#titleCandidateGrid").innerHTML = rows.map((row) => `
      <article class="title-candidate ${row.master ? "master" : ""}">
        <div>
          <span>${esc(row.channel || "渠道")} · ${esc(row.site || "")}</span>
          <small>${esc(row.language || "")}${row.limit ? ` · ≤${esc(row.limit)}` : ""}</small>
        </div>
        <strong>${esc(row.title || "")}</strong>
        ${row.master ? `<button class="button button-secondary adopt-title-candidate"
          type="button" data-title="${esc(row.title || "")}"
          ${titleAdoptSubmitting || stale || adopted ? "disabled" : ""}>${
            titleAdoptSubmitting
              ? "正在采用并废止旧版本…"
              : (locked
                ? "采用并废止旧审批 / 发布计划"
                : "采用为正式英文标题")
          }</button>` : ""}
      </article>
    `).join("") + (shopeeDescription ? `
      <article class="title-candidate title-description-candidate">
        <div>
          <span>Shopee · CNSC 英语母版描述</span>
          <small>${esc(shopeeDescription.length)} / 3000</small>
        </div>
        <p>${esc(shopeeDescription)}</p>
        <small>各国家店由 Shopee 从此英语母版导入并本地化；发布前会校验长度、事实和禁用承诺。</small>
      </article>
    ` : "");
  }

  async function generateTitleDraft() {
    if (!currentData || titleDraftSubmitting || pageLoading) return;
    const product = currentData.product || {};
    const draft = currentData.listing_copy || {};
    const locked = Boolean(product.actual_product_approved || product.fields_locked);
    const lockedStaleRefresh =
      locked && (
        String(draft.status || "").startsWith("superseded")
        || String(draft.policy_version || "") !== LISTING_COPY_POLICY_VERSION
      );
    const lockedMissingRecovery =
      locked && !String(draft.semantic_master_en || "").trim();
    const lockedUnadoptedRefresh =
      locked && draft.status === "draft_pending_kyle_review";
    if ((
      lockedStaleRefresh
      || lockedMissingRecovery
      || lockedUnadoptedRefresh
    ) && !window.confirm(
      lockedMissingRecovery
        ? (
          "当前商品事实已锁定，但缺少平台英文文案候选。\n\n"
          + "本次只会调用 ToAPI 生成本地候选，并废止不完整的旧 ReleasePlan；"
          + "不会修改已批准商品事实，也不会写妙手或任何渠道。"
          + "生成后仍需由 Kyle 明确采用候选并重新审批。"
        )
        : lockedUnadoptedRefresh
        ? (
          "当前商品事实已锁定，但这份候选尚未采用。\n\n"
          + "本次只会重新生成本地候选，不会修改商品事实、妙手或任何渠道；"
          + "生成后仍需由 Kyle 明确采用。"
        )
        : (
          "当前标题候选已过期。重新生成只会调用 ToAPI 并更新本地候选，"
          + "同时废止旧 ReleasePlan；不会修改已批准商品事实，也不会写妙手或渠道。"
        )
    )) return;
    titleDraftSubmitting = true;
    let failureMessage = "";
    renderTitleDraft(currentData);
    updateReleaseControls(currentData);
    $("#titleDraftStatus").textContent =
      "ToAPI 正在依据中文来源、类目、尺寸和保留规格，按各平台搜索习惯优化标题…";
    try {
      const payload = await postProductWorkspace("/api/product-workspace/title-draft", {
        offer_id: product.offer_id,
        expected_revision: product.revision,
        refresh_stale_locked_candidate: lockedStaleRefresh,
        recover_missing_locked_candidate: lockedMissingRecovery,
        replace_unadopted_locked_candidate: lockedUnadoptedRefresh,
        user_approved: (
          lockedStaleRefresh
          || lockedMissingRecovery
          || lockedUnadoptedRefresh
        ),
        approved_by: (
          lockedStaleRefresh
          || lockedMissingRecovery
          || lockedUnadoptedRefresh
        ) ? "Kyle" : "",
      });
      const data = dashboardFromPayload(payload) || payload.dashboard || currentData;
      currentData = data;
      const item = queueItem(currentQueueKey);
      if (item) item.data = data;
      render(data);
      const master = String(data.listing_copy?.semantic_master_en || "").trim();
      if (master) {
        $("#factsEditTitle").value = master;
        $("#factsEditMessage").textContent =
          "已把英文语义母版放入正式标题输入框；请核对后点击“保存并确认商品事实 · 刷新全部售价”。";
      }
    } catch (error) {
      if (isStateRevisionConflict(error)) {
        const reportedRevision = Number(error.payload?.current_revision);
        const revisionHint = Number.isInteger(reportedRevision)
          ? ` revision ${reportedRevision}`
          : "";
        $("#titleDraftStatus").textContent =
          `另一窗口已更新商品${revisionHint}；旧 revision 请求已由 CAS 安全拒绝，正在自动刷新最新状态…`;
        try {
          const latest = await fetchDashboard(
            product.offer_id,
            currentData?.publication_scope?.selected_labels || null,
          );
          currentData = latest;
          const item = queueItem(currentQueueKey);
          if (item) {
            item.data = latest;
            item.seller_sku = latest.product?.seller_sku_candidate || "";
          }
          render(latest);
          const latestRevision = latest.product?.revision ?? reportedRevision;
          failureMessage =
            `另一窗口已将商品更新到 revision ${latestRevision}；本窗口的旧 revision 请求已由 CAS 安全拒绝，已自动刷新最新标题状态。`;
        } catch (_refreshError) {
          failureMessage =
            `另一窗口已更新商品${revisionHint}；本窗口的旧 revision 请求已由 CAS 安全拒绝。自动刷新失败，请点击页面顶部“重新检查”。`;
        }
      } else {
        failureMessage =
          `标题生成失败：${friendlyError(error.message)}；商品事实没有被修改。`;
      }
      $("#titleDraftStatus").textContent = failureMessage;
    } finally {
      titleDraftSubmitting = false;
      renderTitleDraft(currentData || {});
      updateReleaseControls(currentData || {});
      if (failureMessage) $("#titleDraftStatus").textContent = failureMessage;
    }
  }

  async function adoptTitleCandidate(button) {
    if (!currentData || titleAdoptSubmitting || pageLoading) return;
    const product = currentData.product || {};
    const draft = currentData.listing_copy || {};
    const locked = Boolean(product.actual_product_approved || product.fields_locked);
    const candidateTitle = String(button.dataset.title || "").trim();
    const sameApprovedTitle =
      candidateTitle === String(product.title || "").trim();
    if (!locked) {
      $("#factsEditTitle").value = candidateTitle;
      $("#factsEditMessage").textContent =
        "已采用 ToAPI 优化的英文语义母版；核对后保存，才会建立新 revision 并刷新全部售价。";
      $("#factsEditTitle").focus();
      return;
    }
    if (
      !window.confirm(
        sameApprovedTitle
          ? (
            `当前 EN MASTER 已与批准标题一致。\n\n`
            + `本次只会把刷新后的候选标记为已采用，并废止旧 ReleasePlan；`
            + `商品审批与事实锁定保持不变，不会写妙手或任何渠道。`
            + `确认由 Kyle 对 revision ${product.revision ?? "—"} 执行吗？`
          )
          : (
            `采用当前 EN MASTER 将执行以下本地变更：\n\n`
            + `• 把正式商品标题改为该英文候选\n`
            + `• 废止当前商品审批与旧 ReleasePlan\n`
            + `• 废止旧计划尚未完成的运行并解锁商品事实\n\n`
            + `不会写妙手或任何渠道。确认由 Kyle 对 revision ${product.revision ?? "—"} 执行吗？`
          ),
      )
    ) {
      $("#titleDraftStatus").textContent =
        "已取消采用；当前商品审批、ReleasePlan 和运行均未改变。";
      return;
    }
    if (
      loadedQueueKey !== currentQueueKey
      || productKey(product.offer_id) !== currentQueueKey
    ) {
      $("#titleDraftStatus").textContent =
        "当前商品仍在切换，不能使用上一件商品的标题候选。";
      return;
    }
    titleAdoptSubmitting = true;
    renderTitleDraft(currentData);
    updateReleaseControls(currentData);
    $("#titleDraftStatus").textContent =
      "正在复核候选、事实签名和 revision，并安全废止旧审批与发布计划…";
    let finalMessage = "";
    try {
      const payload = await postProductWorkspace(
        "/api/product-workspace/title-adopt",
        {
          offer_id: product.offer_id,
          expected_revision: product.revision,
          candidate_title: candidateTitle,
          input_signature: String(draft.input_signature || ""),
          approved_by: "Kyle",
          user_approved: true,
        },
      );
      const data = dashboardFromPayload(payload)
        || payload.dashboard
        || await fetchDashboard(product.offer_id);
      currentData = data;
      const item = queueItem(currentQueueKey);
      if (item) item.data = data;
      render(data);
      finalMessage = payload.product_approval_preserved
        ? (
          `已确认 EN MASTER 与批准标题一致并建立 revision `
          + `${data.product?.revision ?? payload.revision ?? "新"}；`
          + "商品审批与事实锁定保持有效，可创建新的 ReleasePlan。"
        )
        : (
          `已采用 EN MASTER 并建立 revision ${data.product?.revision ?? payload.revision ?? "新"}；`
          + "旧商品审批、旧发布计划及未完成运行已废止。请复核商品事实后重新批准锁定。"
        );
      $("#factsEditMessage").textContent = finalMessage;
      showError("");
    } catch (error) {
      finalMessage =
        `采用失败：${friendlyError(error.message)}；未建立新的商品事实审批。`;
      showError(finalMessage);
    } finally {
      titleAdoptSubmitting = false;
      renderTitleDraft(currentData || {});
      updateReleaseControls(currentData || {});
      if (finalMessage) $("#titleDraftStatus").textContent = finalMessage;
    }
  }

  function updateFactsEditControls() {
    const form = $("#productFactsForm");
    if (!form) return;
    const locked = form.dataset.locked !== "false";
    const disabled = locked || factsSubmitting || pageLoading || !currentData;
    form.querySelectorAll("textarea, input").forEach((field) => {
      if (field.id === "factsEditSellerSku") {
        field.readOnly = true;
        field.disabled = !currentData;
      } else if (field.classList.contains("sku-label-input")) {
        const selectedSku = [...form.querySelectorAll(
          'input[name="selected_sku_key"]',
        )].find((input) => input.value === (field.dataset.skuKey || ""));
        field.disabled = disabled || !selectedSku?.checked;
      } else {
        field.disabled = disabled;
      }
    });
    $("#factsEditSaveButton").disabled = disabled;
    if ($("#generateTitleDraftButton")) {
      $("#generateTitleDraftButton").disabled = disabled || titleDraftSubmitting;
    }
  }

  async function submitFactsEdit() {
    if (!currentData || factsSubmitting || pageLoading) return;
    const form = $("#productFactsForm");
    if (form.dataset.locked !== "false") {
      $("#factsEditMessage").textContent =
        "当前 revision 已锁定，不能直接覆盖；请先废止旧审批与发布计划。";
      return;
    }
    if (!form.reportValidity()) return;
    const selectedSkuKeys = [...form.querySelectorAll(
      'input[name="selected_sku_key"]:checked',
    )].map((input) => input.value);
    if (!selectedSkuKeys.length) {
      $("#factsEditMessage").textContent = "请至少保留一个真实可采购的来源规格。";
      $("#productSpecGrid").focus?.();
      return;
    }
    const skuLabelOverrides = {};
    for (const key of selectedSkuKeys) {
      const input = [...form.querySelectorAll(".sku-label-input")].find(
        (field) => field.dataset.skuKey === key,
      );
      const label = String(input?.value || "").trim().replace(/\s+/g, " ");
      if (!label) {
        $("#factsEditMessage").textContent = "发布规格名称不能为空。";
        input?.focus();
        return;
      }
      skuLabelOverrides[key] = label;
    }
    const product = currentData.product || {};
    const key = productKey(product.offer_id);
    if (!key || key !== currentQueueKey || loadedQueueKey !== currentQueueKey) {
      showError("当前商品仍在切换中，不能用上一件商品的 revision 保存。");
      return;
    }
    factsSubmitting = true;
    form.classList.add("is-submitting");
    $("#factsEditMessage").textContent =
      "正在核对来源规格、Seller SKU 占用和当前 revision…";
    updateFactsEditControls();
    try {
      const payload = await postProductWorkspace("/api/product-workspace/facts", {
        offer_id: product.offer_id,
        expected_revision: Number(form.dataset.revision),
        title: $("#factsEditTitle").value.trim(),
        cost_cny: Number($("#factsEditCost").value),
        weight_kg: Number($("#factsEditWeight").value),
        package_cm: [
          Number($("#factsEditLength").value),
          Number($("#factsEditWidth").value),
          Number($("#factsEditHeight").value),
        ],
        selected_sku_keys: selectedSkuKeys,
        sku_label_overrides: skuLabelOverrides,
      });
      const data = dashboardFromPayload(payload)
        || await fetchDashboard(product.offer_id);
      const item = queueItem(currentQueueKey);
      if (item) {
        item.data = data;
        item.seller_sku = data.product?.seller_sku_candidate || "";
        item.error = "";
      }
      currentData = data;
      loadedQueueKey = currentQueueKey;
      render(data);
      renderQueue();
      showError("");
      const revision = data.product?.revision ?? payload.revision ?? "新";
      $("#factsEditMessage").textContent =
        `已核对并保存 revision ${revision}；全部国家与店铺售价、费用审计和渠道预检已按新值刷新。当前尚未锁定或发布。`;
    } catch (error) {
      const message = error.status === 409
        ? `保存被拒绝：${friendlyError(error.message)} 请刷新当前商品后再核对。`
        : `保存失败：${friendlyError(error.message)}`;
      $("#factsEditMessage").textContent = message;
      showError(message);
    } finally {
      factsSubmitting = false;
      form.classList.remove("is-submitting");
      updateFactsEditControls();
    }
  }

  function approvalEligible(data) {
    return Boolean(
      data.product?.fact_evidence?.ready !== false
      && data.approval?.ready
      && data.approval?.state_patch_preview?.product_approval?.input_fingerprint
    );
  }

  function updateApprovalButton(data) {
    const approved = Boolean(data?.product?.actual_product_approved);
    const eligible = approvalEligible(data || {});
    $("#approvalButton").disabled = (
      approved
      || !eligible
      || approvalSubmitting
      || pageLoading
    );
  }

  function renderApproval(data, message = "") {
    const product = data.product || {};
    const skuGovernance = product.seller_sku_governance || {};
    const approved = Boolean(product.actual_product_approved);
    const eligible = approvalEligible(data);
    const packageCm = Array.isArray(product.package_cm) ? product.package_cm : [];
    $("#approvalSku").textContent = skuGovernance.available
      ? (product.seller_sku_candidate || "—")
      : `${product.seller_sku_candidate || "—"}（已占用）`;
    $("#approvalRevision").textContent = String(product.revision ?? "—");
    $("#approvalContent").textContent = data.content?.approved
      ? `${data.content.image_count || 0} 图已批准（独立版本）`
      : "内容包独立审批，不阻塞商品事实批准";
    $("#approvalStatus").textContent = approved
      ? "已批准并锁定"
      : (
        eligible
          ? ((data.approval?.warnings || []).length ? "有提醒 · 可以审批" : "可以审批")
          : "等待前置条件"
      );
    $("#approvalFacts").innerHTML = [
      ["采购成本", product.cost_cny ? `¥ ${money(product.cost_cny)} CNY` : "—"],
      ["商品重量", product.weight_kg ? `${product.weight_kg} kg` : "—"],
      ["包装尺寸", packageCm.length ? `${packageCm.join(" × ")} cm` : "—"],
      ["目标站点", (product.selected_sites || []).map((site) => siteNames[site] || site).join(" · ") || "—"],
      ["保留规格", (product.selected_sku_keys || []).join(" · ") || "—"],
      [
        "事实证据",
        product.fact_evidence?.ready === false
          ? "存在必须修复的证据错误"
          : ((product.fact_evidence?.warnings || []).length ? "有提醒 · 可由 Kyle 批准" : "证据一致"),
      ],
    ].map(([label, value]) => `
      <div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>
    `).join("");
    $(".approval-button-label").textContent = approved
      ? "商品字段已锁定"
      : `批准并锁定 revision ${product.revision ?? "—"}`;
    const approvalBlockers = data.approval?.blockers || [];
    const approvalWarnings = (data.approval?.warnings || []).map(translateBlocker);
    $("#approvalMessage").classList.toggle(
      "has-warning",
      !approved && eligible && approvalWarnings.length > 0,
    );
    $("#approvalMessage").textContent = message || (
      approved
        ? "商品事实审批已保存；内容包与发布计划仍按各自版本独立审批。"
        : (!skuGovernance.available && skuGovernance.suggested_base_sku
          ? `当前候选已被旧工作台/已验证声明占用；请改用 ${skuGovernance.suggested_base_sku} 后重新审查。`
        : (eligible
          ? (
            approvalWarnings.length
              ? `可以批准并锁定。审批提醒：${approvalWarnings.join("；")}`
              : "点击即代表 Kyle 最终批准并锁定当前 revision；不会上传或发布。"
          )
          : (approvalBlockers.length
            ? `当前不能锁定：${approvalBlockers.map(translateBlocker).join("；")}`
            : "先解决商品事实、成本证据或 Seller SKU 冲突，再保存商品审批。")))
    );
    updateApprovalButton(data);
  }

  async function submitApproval() {
    if (!currentData || approvalSubmitting || !approvalEligible(currentData)) return;
    const approvalWarnings = (currentData.approval?.warnings || [])
      .map(translateBlocker)
      .filter(Boolean);
    if (
      approvalWarnings.length
      && !window.confirm(
        `当前 revision 有以下审批提醒，但不会阻止锁定：\n\n`
        + approvalWarnings.map((warning) => `• ${warning}`).join("\n")
        + "\n\n确认仍按当前值批准并锁定吗？",
      )
    ) {
      $("#approvalMessage").textContent = "已取消锁定；商品事实保持可编辑状态。";
      return;
    }
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
    $("#approvalMessage").textContent = "正在复核 SKU 唯一性、事实证据和 revision…";
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
    const workflow = data.workflow_next_action || {};
    const currentIndex = Math.max(0, stages.findIndex((stage) => !stage.ready));
    const stage = stages[currentIndex] || stages[stages.length - 1];
    const blockers = (data.actual_release_gate?.blockers || []).map(translateBlocker);
    const contentBlockers = (data.content?.blockers || [])
      .map(translateBlocker)
      .filter((item) => !blockers.includes(item));
    const factBlockers = (data.product?.fact_evidence?.blockers || [])
      .map(translateBlocker);
    const planBlockers = (data.release_v1?.blockers || []).map(translateBlocker);
    const run = data.release_v1?.run;
    const runCounts = releaseRunCounts(run);
    const runGroups = releaseTargetGroups(run);
    const targetNames = (targets) => targets.map(
      (target) => targetDisplayName(target.target_label),
    );
    const reconcileOnlyLabels = targetNames(runGroups.reconcileOnly);
    const safeRetryLabels = targetNames(runGroups.safeRetry);
    const unsafeFailureLabels = targetNames(runGroups.unsafeFailure);
    const blockedCapabilityLabels = targetNames(
      runGroups.blockedCapability,
    );
    const awaitingReadbackLabels = targetNames(runGroups.manualVerify);
    const draftVerifyLabels = targetNames(runGroups.draftVerify);
    const draftConflictLabels = targetNames(runGroups.draftConflict);
    let allBlockers = [
      ...blockers,
      ...contentBlockers,
      ...factBlockers,
      ...planBlockers,
    ];
    if (
      data.content?.stale_external_write
      && blockers.some((item) => item.includes("旧的 11 图版本"))
    ) {
      allBlockers = allBlockers.filter((item) => !item.startsWith("妙手图片记录与"));
    }
    if (data.release_v1?.release_preflight_authority === "canonical_common_readback") {
      const superseded = new Set(
        (data.actual_release_gate?.blockers || []).map(translateBlocker),
      );
      allBlockers = allBlockers.filter((item) => !superseded.has(item));
    }
    allBlockers = [...new Set(allBlockers)];
    const descriptions = {
      product: "补齐商品标题、规格、成本、重量、包装尺寸与目标站点。",
      content: "完成内容审核，并确认最终图片的选择、版本与顺序。",
      approval: "确认候选 Seller SKU，保存商品审批并锁定当前商业字段。",
      plan: "核对精确目标、来源店铺、售价和费用，批准不可变 ReleasePlan。",
      sync: "将当前 ReleasePlan 同步到妙手待发布，并以回读结果确认完全一致。",
      channels: "按已批准计划执行所选店铺；已有外部结果的目标只能对账，只有明确提交前失败才可重试。",
      reconcile: "核对每个目标的回读结果和外部商品身份，完成发布对账。",
    };
    const dispositionActions = [];
    if (draftVerifyLabels.length) {
      dispositionActions.push(
        `${draftVerifyLabels.join("、")}：妙手草稿已保存，但尚未提交店铺；先重新核验草稿，再执行一次店铺提交。`,
      );
    }
    if (draftConflictLabels.length) {
      dispositionActions.push(
        `${draftConflictLabels.join("、")}：妙手草稿存在版本冲突，尚未提交店铺；先刷新最新草稿，禁止直接覆盖。`,
      );
    }
    if (reconcileOnlyLabels.length) {
      dispositionActions.push(
        `${reconcileOnlyLabels.join("、")}：已有外部结果，仅回读/对账，禁止重发。`,
      );
    }
    if (safeRetryLabels.length) {
      dispositionActions.push(
        `${safeRetryLabels.join("、")}：明确在提交前失败，修复阻塞后再安全重试。`,
      );
    }
    if (unsafeFailureLabels.length) {
      dispositionActions.push(
        `${unsafeFailureLabels.join("、")}：失败边界尚未证实，先查明外部结果，禁止重发。`,
      );
    }
    if (blockedCapabilityLabels.length) {
      dispositionActions.push(
        `${blockedCapabilityLabels.join("、")}：当前缺少受治理的自动执行能力；系统不会猜测、导入或使用默认值，请按店铺卡片中的唯一解决方案处理。`,
      );
    }
    if (awaitingReadbackLabels.length) {
      dispositionActions.push(
        `${awaitingReadbackLabels.join("、")}：已提交，需在平台后台逐字段人工验收，禁止重发。`,
      );
    }
    if (dispositionActions.length) {
      descriptions.reconcile =
        `${runCounts.succeeded}/${runCounts.total} 个店铺已完成官方回读；`
        + dispositionActions.join(" ");
    }
    if (awaitingReadbackLabels.length) {
      allBlockers.push(
        `${awaitingReadbackLabels.join("、")} 没有可用的官方店铺 API；`
        + "账本已停止自动重试，等待 Kyle 人工核对 SKU、标题、售价、图片和物流字段。",
      );
    }
    if (draftVerifyLabels.length) {
      allBlockers.push(
        `${draftVerifyLabels.join("、")} 当前只有妙手草稿，没有店铺提交凭证或店铺商品 ID。`,
      );
    }
    if (draftConflictLabels.length) {
      allBlockers.push(
        `${draftConflictLabels.join("、")} 在妙手草稿更新阶段发生版本冲突；不能视为发布成功。`,
      );
    }
    if (reconcileOnlyLabels.length) {
      allBlockers.push(
        `${reconcileOnlyLabels.join("、")} 已有外部 ID、提交或失败证据；`
        + "只能继续回读与对账，不得再次发布。",
      );
    }
    if (unsafeFailureLabels.length) {
      allBlockers.push(
        `${unsafeFailureLabels.join("、")} 的失败边界不明确；`
        + "在确认没有外部结果前不得重试。",
      );
    }
    if (blockedCapabilityLabels.length) {
      allBlockers.push(
        `${blockedCapabilityLabels.join("、")} 当前没有可证明安全的自动执行合同；它不会阻塞其他可执行店铺，也不会被一键发布误触发。`,
      );
    }
    allBlockers = [...new Set(allBlockers)];
    const phaseNumbers = {
      product: 1,
      content: 2,
      approval: 3,
      plan: 4,
      sync: 5,
      channels: 6,
      reconcile: 7,
      complete: 7,
    };
    const workflowValid = (
      workflow.schema_version === "product-workflow-next-action/v1"
      && workflow.code
      && workflow.label
    );
    const workflowIndex = phaseNumbers[workflow.phase] || (currentIndex + 1);
    $("#nextStepNumber").textContent = String(workflowIndex).padStart(2, "0");
    const releaseCompleted = [
      "SUCCEEDED",
      "COMPLETED_WITH_MANUAL_VERIFICATION",
    ].includes(data.release_v1?.run?.status);
    const releaseNeedsDisposition = Boolean(
      run
      && !releaseCompleted
      && dispositionActions.length,
    );
    $("#nextStepTitle").textContent = workflowValid
      ? workflow.label
      : (
        releaseCompleted
          ? "本次正式发布已完成"
          : (releaseNeedsDisposition ? "处理发布结果与对账" : stage.label)
      );
    $("#nextStepDescription").textContent = workflowValid
      ? workflow.detail
      : (
        releaseCompleted
          ? "全部已选店铺均已完成 API 回读或 Kyle 人工验收；账本保留每个目标的幂等提交证据。"
          : descriptions[stage.key]
      );
    $("#blockerList").innerHTML = allBlockers.length
      ? allBlockers.map((item) => `<li>${esc(item)}</li>`).join("")
      : '<li class="ok">当前发布前条件均已满足。</li>';
    const actionButton = $("#nextStepActionButton");
    const actionable = Boolean(
      workflowValid && workflow.actionable === true && workflow.terminal !== true,
    );
    actionButton.hidden = !actionable;
    actionButton.disabled = !actionable;
    actionButton.textContent = workflow.label || "前往下一步";
    actionButton.dataset.actionCode = workflow.code || "";
    document.querySelector(".next-panel").dataset.workflowTerminal = String(
      workflowValid && workflow.terminal === true,
    );
  }

  function runWorkflowNextAction() {
    const action = currentData?.workflow_next_action || {};
    if (
      action.schema_version !== "product-workflow-next-action/v1"
      || action.actionable !== true
      || action.terminal === true
    ) return;
    if (action.kind === "link" && action.href) {
      window.open(action.href, "_blank", "noopener");
      return;
    }
    if (action.kind === "refresh") {
      const item = queueItem(currentQueueKey);
      if (item) refreshQueueProduct(item, { collectIfMissing: true }).catch(() => {});
      return;
    }
    const requestedControlId = action.control_id === "publishAllCheckbox"
      ? "releasePrimaryActionButton"
      : (action.control_id || "");
    const container = document.getElementById(requestedControlId);
    if (!container) return;
    container.scrollIntoView({ behavior: "smooth", block: "center" });
    let target = container;
    if (action.control_id === "releaseRecoveryActions") {
      target = container.querySelector("button:not([disabled])") || container;
    } else if (action.control_id === "releaseRunLedger") {
      const focusLabel = String(action.focus_target_label || "");
      const focusedCard = focusLabel
        ? container.querySelector(
          `.run-target[data-target-label="${CSS.escape(focusLabel)}"]`,
        )
        : null;
      target = focusedCard?.querySelector(
        "input:not([disabled]), button:not([disabled]), a[href]",
      ) || focusedCard || container;
    } else if (
      container.matches("section, article, form, div")
    ) {
      target = container.querySelector(
        "input:not([disabled]), button:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]",
      ) || container;
    }
    if (typeof target.focus === "function") {
      target.focus({ preventScroll: true });
    }
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
    const videos = Array.isArray(content?.video_urls) ? content.video_urls : [];
    if (videos.length) {
      notices.push({
        text: `视频已审核保留 · ${videos.length} 条 HTTPS 素材`,
        safe: true,
        url: videos[0],
      });
    } else {
      notices.push({
        text: "本内容包不包含保留视频；不会把未审核视频带入发布计划",
        safe: true,
      });
    }
    $("#contentNotice").innerHTML = notices
      .map((item) => `
        <span class="${item.safe ? "safe" : ""}">
          ${esc(item.text)}
          ${item.url ? ` · <a href="${esc(item.url)}" target="_blank" rel="noopener">打开审核视频 ↗</a>` : ""}
        </span>
      `)
      .join("");
  }

  function sameTargetSet(left, right) {
    if (left.size !== right.size) return false;
    return [...left].every((label) => right.has(label));
  }

  function updatePublicationScopeControls() {
    const dirty = !sameTargetSet(pendingPublicationTargets, appliedPublicationTargets);
    const count = pendingPublicationTargets.size;
    const note = $("#publicationScopeNote");
    note.classList.toggle("is-dirty", dirty);
    if (!count) {
      note.textContent = "请至少选择一个平台与国家目标。";
    } else if (dirty) {
      note.textContent = `已选择 ${count} 个目标；应用后会生成新的预检计划和确认令牌。`;
    } else {
      note.textContent = `当前计划已包含 ${count} 个目标；选择结果已由服务端校验。`;
    }
    const applyButton = $("#applyPublicationScopeButton");
    applyButton.textContent = !dirty && count
      ? "当前选择已应用"
      : "应用选择并审查售价";
    applyButton.disabled = pageLoading || !dirty || !count;
  }

  function renderPublicationScope(scope) {
    const available = Array.isArray(scope?.available_targets)
      ? scope.available_targets
      : [];
    appliedPublicationTargets = new Set(scope?.selected_labels || []);
    pendingPublicationTargets = new Set(appliedPublicationTargets);
    const grid = $("#publicationTargetGrid");
    if (!available.length) {
      grid.innerHTML = '<div class="image-fallback">当前没有服务端允许的发布目标。</div>';
      updatePublicationScopeControls();
      return;
    }
    grid.innerHTML = available.map((target, index) => {
      const channel = channelNames[target.channel] || target.channel;
      const site = target.channel === "tiktok" && target.shop && target.country
        ? `${target.shop} · ${publicationSiteNames[target.country] || target.country}`
        : (publicationSiteNames[target.site] || target.site);
      const checked = pendingPublicationTargets.has(target.label);
      return `
        <div class="publication-target">
          <input
            id="publicationTarget${index}"
            type="checkbox"
            name="publication_target"
            value="${esc(target.label)}"
            ${checked ? "checked" : ""}
          >
          <label for="publicationTarget${index}">
            <span>
              <strong>${esc(channel)} · ${esc(site)}</strong>
              <small>${esc(target.label)}</small>
            </span>
          </label>
        </div>
      `;
    }).join("");
    updatePublicationScopeControls();
  }

  function setPendingPublicationTargets(labels) {
    const desired = new Set(labels || []);
    $("#publicationTargetGrid").querySelectorAll('input[name="publication_target"]')
      .forEach((input) => {
        input.checked = desired.has(input.value);
      });
    pendingPublicationTargets = desired;
    updatePublicationScopeControls();
  }

  function renderPricingReview(pricing, publicationScope) {
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
    const minimumProfit = validProfits.length ? money(Math.min(...validProfits)) : "—";
    $("#pricingSummary").textContent = allRows.length
      ? `${allRows.length} 个国家/店铺价格 · 当前范围 ${selectedKeys.size} 个店铺价 · 最低预计利润 ¥${minimumProfit} · ${adjusted} 个触发利润底线`
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

    const selectedLabels = publicationScope?.selected_labels || [];
    const availableByLabel = new Map(
      (publicationScope?.available_targets || []).map((row) => [row.label, row]),
    );
    const targetPricing = pricing?.target_pricing || {};
    $("#selectedChannelPriceGrid").innerHTML = selectedLabels.length
      ? selectedLabels.map((label) => {
          const [channel, site] = String(label).split(":");
          const targetMeta = availableByLabel.get(label) || {};
          const siteLabel = channel === "tiktok" && targetMeta.shop
            ? `${targetMeta.shop} · ${publicationSiteNames[targetMeta.country] || targetMeta.country}`
            : (publicationSiteNames[site] || site);
          const detail = channelPriceLine(targetPricing[label] || {});
          return `
            <article class="selected-channel-price-card ${esc(detail.status)}">
              <header>
                <h5>${esc(channelNames[channel] || channel)} · ${esc(siteLabel)}</h5>
                <span>${esc(detail.statusText)}</span>
              </header>
              <strong>${esc(detail.value)}</strong>
              <small>${esc(detail.label)}<br>${esc(detail.source)}</small>
            </article>
          `;
        }).join("")
      : '<div class="image-fallback">请至少选择一个发布目标。</div>';

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
    const status = String(pricing?.status || "blocked");
    const statusText = {
      ready: "售价可审查",
      awaiting_tiktok_readback: "等待 TikTok 回读",
      blocked: "售价被阻塞",
    }[status] || status;
    if ((pricing?.store_prices || []).length > 1) {
      const stores = pricing.store_prices;
      return {
        label: pricing?.role === "common_draft"
          ? "妙手公共草稿包含所选逐店价格"
          : "所选 TikTok 店铺建议挂牌价",
        value: stores.map((store) => (
          `${store.shop || store.target_key} ${localMoney(store.list_price, store.currency)}`
        )).join(" · "),
        status,
        statusText,
        source: `来源：${stores.map((store) => store.target_key).join("、")} 的旧版逐店反向定价审计`,
      };
    }
    const store = (pricing?.store_prices || [])[0];
    if (store?.list_price != null) {
      return {
        label: `${store.shop || "TikTok"} ${store.region || ""} 建议挂牌价`,
        value: localMoney(store.list_price, store.currency),
        status,
        statusText,
        source: `来源：${store.target_key || "legacy price_review"} · 折后 ${localMoney(store.sale_after_discount, store.currency)}`,
      };
    }
    const derived = pricing?.derived_preview || {};
    const source = pricing?.source || {};
    if (derived.global_original_price_cny != null) {
      return {
        label: `由 TikTok ${derived.source_currency || ""} 价格派生`,
        value: `¥${money(derived.global_original_price_cny)} CNY`,
        status,
        statusText,
        source: `来源：${source.shop || "TikTok"} ${source.region || ""} ${source.target_key || ""}；${pricing?.source_selection_note || "真实写入前必须重新回读"}`,
      };
    }
    if (derived.price_cny != null) {
      return {
        label: "由 TikTok 主商品派生（划线价同时保留）",
        value: `¥${money(derived.price_cny)} / 划线 ¥${money(derived.old_price_cny)}`,
        status,
        statusText,
        source: `来源：${source.shop || "TikTok"} ${source.region || ""} ${source.target_key || ""}；${pricing?.source_selection_note || "真实写入前必须重新回读"}`,
      };
    }
    return {
      label: "当前范围没有可用售价",
      value: "—",
      status: "blocked",
      statusText: "售价被阻塞",
      source: pricing?.blocker || "缺少对应 TikTok 主商品价格",
    };
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
      const targetMeta = (currentData?.publication_scope?.available_targets || [])
        .find((row) => row.label === `${target.channel}:${target.site}`) || {};
      const targetSiteLabel = target.channel === "tiktok" && targetMeta.shop
        ? `${targetMeta.shop} · ${publicationSiteNames[targetMeta.country] || targetMeta.country}`
        : (publicationSiteNames[target.site] || target.site || "COMMON");
      return `
        <article class="channel-card ${preflightReady ? "ready" : "blocked"}">
          <header>
            <h3>
              ${esc(channelNames[target.channel] || target.channel)}
              <small>${esc(targetSiteLabel)}</small>
            </h3>
            <span class="badge ${preflightReady ? "safe" : "warn"}">
              ${preflightReady ? "预检通过" : "已阻塞"}
            </span>
          </header>
          <p>${esc(message)}</p>
          ${priceLine ? `
            <div class="channel-price ${esc(priceLine.status)}">
              <span>${esc(priceLine.label)} · ${esc(priceLine.statusText)}</span>
              <strong>${esc(priceLine.value)}</strong>
              <small>${esc(priceLine.source)}</small>
            </div>
          ` : ""}
          <dl class="channel-meta">
            <div><dt>适配器</dt><dd>${esc(adapterStatus)}</dd></div>
            <div><dt>步骤 / 外部动作</dt><dd>${target.steps?.length || 0} / ${externalStepCount}</dd></div>
            <div><dt>依赖</dt><dd>${esc(dependencies)}</dd></div>
          </dl>
        </article>
      `;
    }).join("");

    void publication;
    void releaseReady;
    void targetLabels;
  }

  function maskedToken(token) {
    const value = String(token || "");
    return value.length > 18
      ? `${value.slice(0, 12)}…${value.slice(-4)}`
      : (value || "—");
  }

  function targetDisplayName(label) {
    const meta = (currentData?.publication_scope?.available_targets || [])
      .find((row) => row.label === label);
    if (meta?.shop) {
      return `${channelNames[meta.channel] || meta.channel} · ${meta.shop} · ${publicationSiteNames[meta.country] || meta.country}`;
    }
    const [channel, site] = String(label || "").split(":");
    return `${channelNames[channel] || channel} · ${publicationSiteNames[site] || site}`;
  }

  function targetNamesForLedger(targets) {
    return targets
      .map((target) => targetDisplayName(target.target_label))
      .join("、");
  }

  function shopeePriceRepairKey(targetLabel) {
    return `${currentData?.product?.offer_id || ""}:${targetLabel}`;
  }

  function shopeePriceRepairState(targetLabel) {
    return shopeePriceRepairStates.get(shopeePriceRepairKey(targetLabel))
      || { phase: "idle", message: "" };
  }

  function setShopeePriceRepairState(targetLabel, state) {
    shopeePriceRepairStates.set(shopeePriceRepairKey(targetLabel), state);
  }

  function shopeePriceRepairLifecycle(target) {
    return String(target?.repair?.status || "").toUpperCase();
  }

  function shopeePriceRepairEligible(target) {
    return Boolean(
      SHOPEE_PRICE_REPAIR_TARGETS.has(String(target?.target_label || ""))
      && target?.status === "FAILED"
      && target?.external_id
      && !target?.repair,
    );
  }

  function shopeePriceRepairPanel(target) {
    const targetLabel = String(target.target_label);
    const state = shopeePriceRepairState(targetLabel);
    const site = publicationSiteNames[targetLabel.split(":")[1]] || targetLabel;
    const lifecycle = shopeePriceRepairLifecycle(target);
    if (
      SHOPEE_PRICE_REPAIR_TARGETS.has(targetLabel)
      && lifecycle === "RECONCILIATION_REQUIRED"
    ) {
      const reconciling = state.phase === "reconciling";
      const message = state.message || (
        "本操作只调用 Shopee 官方 GET 回读并更新本地账本；"
        + "零平台写入，不会再次修价或重发商品。"
      );
      return `
        <section class="shopee-price-repair-panel is-reconciliation"
          data-price-repair-target="${esc(targetLabel)}" aria-live="polite">
          <strong>挂牌价已写入，等待只读对账</strong>
          <button class="button button-secondary" type="button"
            data-price-repair-action="reconcile"
            data-target-label="${esc(targetLabel)}"
            ${reconciling ? "disabled" : ""}>
            ${reconciling ? `正在只读回读 ${esc(site)}…` : "只读回读并结案"}
          </button>
          <p class="shopee-price-repair-message" role="status">${esc(message)}</p>
        </section>
      `;
    }
    if (!shopeePriceRepairEligible(target)) return "";
    const plan = currentData?.release_v1?.plan || {};
    const previewCurrent = Boolean(
      state.preview?.repair_allowed === true
      && state.preview?.target_label === targetLabel
      && state.preview?.plan_id === plan.plan_id
      && state.preview?.payload_digest === plan.payload_digest
      && Number(state.preview?.expected_revision) === Number(
        currentData?.product?.revision,
      )
      && currentData?.release_v1?.plan_approved
    );
    const message = (
      state.phase === "preview" && !previewCurrent
        ? "当前计划或 revision 已变化，请重新执行只读检查。"
        : state.message
    ) || (
      state.phase === "idle"
        ? "先执行只读检查；未通过安全门前不会提供修复确认。"
        : ""
    );
    if (state.phase === "terminal" || state.phase === "succeeded") {
      return `
        <section class="shopee-price-repair-panel is-terminal"
          data-price-repair-target="${esc(targetLabel)}" aria-live="polite">
          <strong>${state.phase === "succeeded" ? "价格修复已完成" : "价格修复已停止"}</strong>
          <p class="shopee-price-repair-message" role="status">${esc(message)}</p>
        </section>
      `;
    }
    if (state.phase === "repairing") {
      return `
        <section class="shopee-price-repair-panel is-busy"
          data-price-repair-target="${esc(targetLabel)}" aria-live="polite">
          <strong>正在执行一次性原地修价</strong>
          <button class="button button-secondary" type="button"
            data-price-repair-action="submit"
            data-target-label="${esc(targetLabel)}" disabled>
            修复请求处理中…
          </button>
          <p class="shopee-price-repair-message" role="status">${esc(message)}</p>
        </section>
      `;
    }
    if (state.phase === "preview" && previewCurrent) {
      return `
        <section class="shopee-price-repair-panel is-ready"
          data-price-repair-target="${esc(targetLabel)}" aria-live="polite">
          <div class="shopee-price-repair-summary">
            <strong>${esc(site)} · 当前不可变 ReleasePlan</strong>
            <span>Kyle 已批准 · revision ${esc(state.preview.expected_revision)}</span>
          </div>
          <label class="shopee-price-repair-confirm">
            <input type="checkbox" data-price-repair-confirm="${esc(targetLabel)}">
            <span>我确认仅原地修正该站点价格，不重发商品。</span>
          </label>
          <button class="button button-secondary" type="button"
            data-price-repair-action="submit"
            data-target-label="${esc(targetLabel)}" disabled>
            原地修正 ${esc(site)} 价格并回读
          </button>
          <p class="shopee-price-repair-message" role="status">${esc(message)}</p>
        </section>
      `;
    }
    const checking = state.phase === "checking";
    return `
      <section class="shopee-price-repair-panel"
        data-price-repair-target="${esc(targetLabel)}" aria-live="polite">
        <button class="button button-secondary" type="button"
          data-price-repair-action="preview"
          data-target-label="${esc(targetLabel)}"
          ${checking ? "disabled" : ""}>
          ${checking ? `正在只读检查 ${esc(site)}…` : "检查价格修复"}
        </button>
        <p class="shopee-price-repair-message" role="status">${esc(message)}</p>
      </section>
    `;
  }

  function targetScopedActionPanel(target) {
    const label = String(target?.target_label || "");
    if (!TARGET_SCOPED_ACTION_TARGETS.has(label) || target?.status !== "FAILED") return "";
    const state = targetScopedActionStates.get(label) || {};
    const preview = state.preview || {};
    const eligible = preview.available === true
      && preview.target_label === label
      && preview.plan_id === currentData?.release_v1?.plan?.plan_id
      && Number(preview.expected_revision) === Number(currentData?.product?.revision);
    return `<section class="target-scoped-action-panel" data-target-scoped-target="${esc(label)}" aria-live="polite">
      <strong>${esc(label)}：仅限当前失败目标的受控恢复</strong>
      <p>${esc(state.message || "Shopee 自动翻译 · 发布后官方回读；先执行只读预检，不会调用通用一键发布或其他目标。")}</p>
      <button type="button" class="button button-secondary" data-target-scoped-action="preview" data-target-label="${esc(label)}" aria-busy="${state.checking === true}" ${releaseSubmitting || state.checking === true ? "disabled" : ""}>只读预检</button>
      ${eligible ? `<label><input type="checkbox" data-target-scoped-confirm> 我确认仅执行该目标的既有对象恢复并立即回读</label>
      <button type="button" class="button" data-target-scoped-action="submit" data-target-label="${esc(label)}" disabled>确认执行单目标恢复</button>` : ""}
    </section>`;
  }

  async function previewTargetScopedAction(targetLabel) {
    if (!currentData || releaseSubmitting) return;
    if (targetScopedActionStates.get(targetLabel)?.checking) return;
    targetScopedActionStates.set(targetLabel, { checking: true, message: "正在执行官方只读预检…" });
    renderReleaseV1(currentData);
    try {
      const query = new URLSearchParams({ offer_id: currentData.product?.offer_id || "", target_label: targetLabel });
      const response = await fetch(`/api/product-workspace/release-target/target-scoped-action-preview?${query}`, { headers: { Accept: "application/json" } });
      const payload = await response.json();
      if (!response.ok || payload.ok === false || payload.available !== true) throw new Error(payload.error || "目标预检未通过");
      targetScopedActionStates.set(targetLabel, { checking: false, preview: payload, message: "预检通过；确认后只会执行该站点的一次受控操作。" });
    } catch (error) {
      targetScopedActionStates.set(targetLabel, { checking: false, message: `${friendlyError(error.message)}；不会显示执行按钮。` });
    }
    if (currentData) renderReleaseV1(currentData);
  }

  async function submitTargetScopedAction(targetLabel) {
    const state = targetScopedActionStates.get(targetLabel) || {};
    const preview = state.preview || {};
    const panel = document.querySelector(`[data-target-scoped-target="${CSS.escape(targetLabel)}"]`);
    if (!panel?.querySelector("[data-target-scoped-confirm]")?.checked || preview.available !== true) return;
    releaseSubmitting = true;
    targetScopedActionStates.set(targetLabel, { ...state, message: "正在重新核对 proof 并执行一次受控操作…" });
    renderReleaseV1(currentData);
    try {
      await postReleaseAction("/api/product-workspace/release-target/target-scoped-action", currentReleaseBody({
        target_label: targetLabel,
        expected_revision: preview.expected_revision,
        payload_digest: preview.payload_digest,
        planned_command_digest: preview.planned_command_digest,
        preflight_digest: preview.preflight_digest,
        proof_digest: preview.proof_digest,
        failure_attempt: preview.failure_attempt,
        confirm_target_scoped_action: true,
        approved_by: "Kyle",
      }));
      targetScopedActionStates.set(targetLabel, { message: "区域商品身份已验证 · 平台翻译/图片待人工复核。" });
    } catch (error) {
      targetScopedActionStates.set(targetLabel, { message: `${friendlyError(error.message)}；需要对账，已停止且不会自动重试。` });
    } finally {
      releaseSubmitting = false;
      const item = queueItem(currentQueueKey);
      if (item) refreshQueueProduct(item).catch(() => {});
    }
  }

  function awaitsOfficialReadback(target) {
    if (target?.status === "SUBMITTED_UNVERIFIED") return true;
    const error = String(target?.error || "").toLowerCase();
    return Boolean(
      target?.status === "FAILED"
      && target?.external_id
      && error.includes("official")
      && error.includes("readback")
      && (
        error.includes("unavailable")
        || error.includes("no authorised")
        || error.includes("no authorized")
      ),
    );
  }

  function targetFailureEvidence(target) {
    return target?.latest_failure_evidence?.evidence
      || (target?.failure_events || []).at(-1)?.evidence
      || null;
  }

  function isZeroWritePreSubmitEvidence(evidence) {
    return Boolean(
      evidence
      && evidence.pre_submit_failure === true
      && evidence.submission_accepted === false
      && Array.isArray(evidence.external_writes_performed)
      && evidence.external_writes_performed.length === 0
    );
  }

  function targetHasExternalOutcome(target) {
    const evidence = targetFailureEvidence(target);
    return Boolean(
      target?.external_id
      || target?.submission
      || target?.readback
      || target?.external_writes_performed?.length
      || (evidence && !isZeroWritePreSubmitEvidence(evidence)),
    );
  }

  function isExplicitPreSubmitFailure(target) {
    if (target?.status !== "FAILED" || targetHasExternalOutcome(target)) {
      return false;
    }
    if (isZeroWritePreSubmitEvidence(targetFailureEvidence(target))) {
      return true;
    }
    const detail = String(target?.error || "").toLowerCase();
    return [
      "pre-submit",
      "pre submit",
      "preflight",
      "before submission",
      "before external",
      "not submitted",
      "no external write",
      "no edit was sent",
      "persisted miaoshou claim lacks",
      "提交前",
      "未提交",
      "未发生外部写入",
    ].some((marker) => detail.includes(marker));
  }

  function miaoshouDraftOnlyState(target) {
    if (
      target?.status !== "FAILED"
      || !String(target?.target_label || "").startsWith("tiktok:")
    ) {
      return "";
    }
    const evidence = targetFailureEvidence(target) || {};
    const writes = new Set(
      Array.isArray(evidence.external_writes_performed)
        ? evidence.external_writes_performed.map(String)
        : [],
    );
    const hasDraftWrite = [
      "miaoshou:tiktok_detail:create",
      "miaoshou:tiktok_shop:claim",
      "miaoshou:tiktok_detail:update",
    ].some((value) => writes.has(value));
    const publishDispatched = Boolean(
      writes.has("miaoshou:tiktok_publish:submission")
      || evidence.publish_dispatched === true
      || evidence.submission_accepted === true,
    );
    if (!hasDraftWrite || publishDispatched) return "";
    const detail = String(target?.error || "").toLowerCase();
    if (
      detail.includes("产品数据发生变动")
      || detail.includes("version")
      || detail.includes("conflict")
    ) {
      return "version_conflict";
    }
    return "waiting_verification";
  }

  function commonSpecLabelApplication(release) {
    const common = (release?.run?.targets || []).find(
      (target) => target?.target_label === "miaoshou:COMMON",
    );
    return common?.readback?.evidence?.spec_label_application || {};
  }

  function preparedMiaoshouMessage(release) {
    const application = commonSpecLabelApplication(release);
    if (application?.status === "deferred_to_site_draft") {
      return "妙手公共草稿已写入并回读一致；规格显示名将在各站点草稿中按已批准计划写入并校验。";
    }
    return "妙手公共草稿已写入并回读一致；可以继续检查渠道执行条件。";
  }

  function commonNeedsReadbackReconciliation(release) {
    const common = (release?.run?.targets || []).find(
      (target) => target?.target_label === "miaoshou:COMMON",
    );
    return Boolean(
      common?.status === "FAILED" && targetHasExternalOutcome(common),
    );
  }

  function releaseTargetDisposition(target) {
    const recovery = targetRecoveryActions.get(
      String(target?.target_label || ""),
    );
    const canonicalStatus = String(recovery?.canonical_status || "");
    const canonicalAction = String(recovery?.action || "");
    if (canonicalStatus === "SUBMITTED_UNVERIFIED") return "manual_verify";
    if (canonicalStatus === "RECONCILIATION_REQUIRED") return "reconcile_only";
    if (canonicalStatus === "FAILED_PRE_SUBMIT") return "safe_retry";
    if (canonicalStatus === "DISPATCHING") return "running";
    if (canonicalStatus === "SUCCEEDED") return "succeeded";
    if (canonicalStatus.startsWith("BLOCKED_")) return "blocked_capability";
    if (
      recovery?.runnable === true
      || ["wait_for_worker", "prepare_batch", "wait_for_preparation"].includes(
        canonicalAction,
      )
    ) return "pending";
    if (recovery?.action_kind === "MANUAL_ACCEPT") return "manual_verify";
    if (recovery?.action_kind === "FIRST_ATTEMPT") return "pending";
    if (recovery?.action_kind === "GOVERNED_RECOVERY") return "pending";
    if (recovery?.action_kind === "BLOCKED_CAPABILITY") {
      return "blocked_capability";
    }
    if (recovery?.action_kind === "SAFE_RETRY") return "safe_retry";
    if (recovery?.action_kind === "READONLY_RECONCILE") {
      return target?.status === "RUNNING" ? "running" : "reconcile_only";
    }
    if (recovery?.action_kind === "SAFE_REPAIR") return "unsafe_failure";
    if (recovery?.action_kind === "BLOCKED") return "unsafe_failure";
    if (recovery?.action_kind === "TERMINAL") {
      return target?.status === "MANUALLY_VERIFIED" ? "verified" : "succeeded";
    }
    const repairStatus = shopeePriceRepairLifecycle(target);
    if (repairStatus === "RUNNING") return "running";
    if (repairStatus === "RECONCILIATION_REQUIRED") return "reconcile_only";
    if (repairStatus === "SUCCEEDED") return "succeeded";
    if (target?.status === "MANUALLY_VERIFIED") return "verified";
    if (target?.status === "SUCCEEDED") return "succeeded";
    if (target?.status === "RUNNING") return "running";
    if (target?.status === "PENDING") return "pending";
    if (target?.status === "FAILED") {
      const draftState = miaoshouDraftOnlyState(target);
      if (draftState === "waiting_verification") return "draft_verify";
      if (draftState === "version_conflict") return "draft_conflict";
      if (targetHasExternalOutcome(target)) return "reconcile_only";
      if (isExplicitPreSubmitFailure(target)) return "safe_retry";
      return "unsafe_failure";
    }
    if (awaitsOfficialReadback(target)) return "manual_verify";
    return "unknown";
  }

  function releaseTargetGroups(run) {
    const groups = {
      reconcileOnly: [],
      safeRetry: [],
      unsafeFailure: [],
      blockedCapability: [],
      manualVerify: [],
      running: [],
      pending: [],
      draftVerify: [],
      draftConflict: [],
    };
    for (const target of (run?.targets || [])) {
      const disposition = releaseTargetDisposition(target);
      if (disposition === "reconcile_only") groups.reconcileOnly.push(target);
      if (disposition === "safe_retry") groups.safeRetry.push(target);
      if (disposition === "unsafe_failure") groups.unsafeFailure.push(target);
      if (disposition === "blocked_capability") {
        groups.blockedCapability.push(target);
      }
      if (disposition === "manual_verify") groups.manualVerify.push(target);
      if (disposition === "running") groups.running.push(target);
      if (disposition === "pending") groups.pending.push(target);
      if (disposition === "draft_verify") groups.draftVerify.push(target);
      if (disposition === "draft_conflict") groups.draftConflict.push(target);
    }
    return groups;
  }

  function releaseRunCounts(run) {
    const targets = (run?.targets || []).filter(
      (target) => target?.target_label !== "miaoshou:COMMON",
    );
    const groups = releaseTargetGroups(run);
    return {
      total: targets.length,
      succeeded: targets.filter((target) => target.status === "SUCCEEDED").length,
      running: targets.filter((target) => target.status === "RUNNING").length,
      awaitingReadback: targets.filter(awaitsOfficialReadback).length,
      manuallyVerified: targets.filter(
        (target) => target.status === "MANUALLY_VERIFIED",
      ).length,
      failed: targets.filter(
        (target) => target.status === "FAILED" && !awaitsOfficialReadback(target),
      ).length,
      reconcileOnly: groups.reconcileOnly.length,
      safeRetry: groups.safeRetry.length,
      unsafeFailure: groups.unsafeFailure.length,
      blockedCapability: groups.blockedCapability.length,
      pending: groups.pending.length,
      draftVerify: groups.draftVerify.length,
      draftConflict: groups.draftConflict.length,
    };
  }

  function releaseRunLabel(run) {
    const counts = releaseRunCounts(run);
    if (run?.status === "SUCCEEDED") return `${counts.succeeded}/${counts.total} 全部回读成功`;
    if (run?.status === "COMPLETED_WITH_MANUAL_VERIFICATION") {
      return `${counts.succeeded} API 回读 · ${counts.manuallyVerified} 人工验收`;
    }
    if (run?.status === "RUNNING" && !counts.running) {
      const governedOutcomes = [
        counts.reconcileOnly ? `${counts.reconcileOnly} 待对账` : "",
        counts.blockedCapability ? `${counts.blockedCapability} 个能力阻断` : "",
        counts.awaitingReadback ? `${counts.awaitingReadback} 待人工验收` : "",
        counts.safeRetry ? `${counts.safeRetry} 修复后可重试` : "",
        counts.unsafeFailure ? `${counts.unsafeFailure} 禁止重发` : "",
        counts.draftVerify ? `${counts.draftVerify} 个草稿待核验` : "",
        counts.draftConflict ? `${counts.draftConflict} 个草稿版本冲突` : "",
      ].filter(Boolean);
      if (governedOutcomes.length) {
        return (
          `${counts.succeeded}/${counts.total} 个店铺发布完成 · `
          + governedOutcomes.join(" · ")
        );
      }
    }
    if (run?.status === "RUNNING") return `${counts.succeeded}/${counts.total} 正在执行`;
    if (counts.awaitingReadback && !counts.failed && !counts.running) {
      return `${counts.succeeded} 已回读 · ${counts.awaitingReadback} 待人工验收`;
    }
    if (run?.status === "PARTIAL_FAILED") {
      const outcomes = [
        counts.draftVerify ? `${counts.draftVerify} 个草稿待核验` : "",
        counts.draftConflict ? `${counts.draftConflict} 个草稿版本冲突` : "",
        counts.reconcileOnly ? `${counts.reconcileOnly} 待对账` : "",
        counts.safeRetry ? `${counts.safeRetry} 修复后可重试` : "",
        counts.unsafeFailure ? `${counts.unsafeFailure} 禁止重发` : "",
        counts.awaitingReadback ? `${counts.awaitingReadback} 待人工验收` : "",
      ].filter(Boolean);
      if (counts.pending) outcomes.push(`${counts.pending} 个尚未执行`);
      return `${counts.succeeded}/${counts.total} 个店铺发布完成 · ${outcomes.join(" · ") || `${counts.failed} 待处置`}`;
    }
    return run?.status || "未知";
  }

  function releaseTargetLabel(target, statusNames) {
    if (
      target?.target_label === "miaoshou:COMMON"
      && target?.status === "SUCCEEDED"
    ) {
      return "公共草稿已核验 · 不计入店铺发布";
    }
    const repairStatus = shopeePriceRepairLifecycle(target);
    if (repairStatus === "RUNNING") {
      return "价格修复执行中 · 禁止重复操作";
    }
    if (repairStatus === "RECONCILIATION_REQUIRED") {
      return "挂牌价已写入，等待只读对账";
    }
    if (repairStatus === "SUCCEEDED") {
      const derivedStatus = target?.repair?.result?.derived_price_status;
      return derivedStatus === "warning"
        ? "挂牌价已验证 · SIP差异待财务审查"
        : "挂牌价已验证 · 利润仍待财务审查";
    }
    const disposition = releaseTargetDisposition(target);
    if (disposition === "blocked_capability") {
      return "等待已批准的渠道执行条件";
    }
    if (disposition === "verified") return "Kyle 已人工验收";
    if (disposition === "manual_verify") return "已提交 · 待人工验收";
    if (disposition === "draft_verify") {
      return "妙手草稿已保存 · 尚未提交店铺";
    }
    if (disposition === "draft_conflict") {
      return "妙手草稿版本冲突 · 尚未提交店铺";
    }
    if (disposition === "reconcile_only") {
      return "已创建 · 结果待对账，禁止重发";
    }
    if (disposition === "safe_retry") return "失败 · 可安全重试";
    if (disposition === "unsafe_failure") {
      return "失败 · 边界未证实，禁止重发";
    }
    return statusNames[target?.status] || target?.status || "未知";
  }

  function releaseTargetDetail(target) {
    if (
      target?.target_label === "miaoshou:COMMON"
      && target?.status === "SUCCEEDED"
    ) {
      return "这里只证明妙手公共采集箱与批准计划一致；它不是任何店铺的商品，也不代表店铺发布成功。";
    }
    const repairStatus = shopeePriceRepairLifecycle(target);
    if (repairStatus === "RUNNING") {
      return "该站点的一次性原地修价已领取；正在等待官方回读和本地账本落账，系统不会再次提交。";
    }
    if (repairStatus === "RECONCILIATION_REQUIRED") {
      return "原地修价已有一次平台写入证据；仅允许官方 GET 回读并结案，禁止再次修价或重发商品。";
    }
    if (repairStatus === "SUCCEEDED") {
      return "该站点挂牌价已完成官方精确回读；SIP 是 Shopee 派生观察，不代表已实现利润，仍待财务审查。";
    }
    const disposition = releaseTargetDisposition(target);
    if (disposition === "blocked_capability") {
      const recovery = targetRecoveryActions.get(
        String(target?.target_label || ""),
      );
      if (target?.target_label === "ozon:RU") {
        return "Ozon 缺少不可变计划内的库存决策；本轮不会使用默认库存，也不会导入或修改库存。";
      }
      return `当前渠道尚无受治理的自动首发能力（${recovery?.reason_code || "capability unavailable"}）。`;
    }
    if (disposition === "manual_verify") {
      return "妙手已接收且提交凭证已锁定；当前店铺没有官方 API，系统不会自动重试。请在平台后台核对后记录人工验收。";
    }
    if (disposition === "verified") {
      return `由 Kyle 在平台后台完成逐字段验收 · 商品 ID ${
        target?.submission?.verification_evidence?.marketplace_product_id || "—"
      }`;
    }
    if (disposition === "draft_verify") {
      return "只完成了妙手草稿创建、店铺认领和详情更新；没有调用店铺发布提交。需要先重新只读核验草稿，再执行一次受控店铺提交。";
    }
    if (disposition === "draft_conflict") {
      return "妙手在详情更新阶段报告版本冲突；没有证据表明已提交到店铺。需要重新读取最新草稿并核验，禁止直接覆盖或重复创建。";
    }
    if (disposition === "reconcile_only") {
      return "已存在外部 ID、提交、回读或失败证据。仅允许继续回读/人工对账，系统禁止再次发布。"
        + (target?.error ? ` 原因：${target.error}` : "");
    }
    if (disposition === "safe_retry") {
      return "失败明确发生在提交前，且没有外部结果证据；修复阻塞后可安全重试。"
        + (target?.error ? ` 原因：${target.error}` : "");
    }
    if (disposition === "unsafe_failure") {
      return "没有足够证据证明失败发生在提交前；查明外部结果前禁止重发。"
        + (target?.error ? ` 原因：${target.error}` : "");
    }
    return target?.error || "";
  }

  function releaseTargetCssClass(target) {
    const repairStatus = shopeePriceRepairLifecycle(target);
    if (repairStatus === "RUNNING") return "repair-running";
    if (repairStatus === "RECONCILIATION_REQUIRED") {
      return "repair-reconciliation reconciliation-required";
    }
    if (repairStatus === "SUCCEEDED") return "repair-succeeded succeeded";
    const disposition = releaseTargetDisposition(target);
    if (disposition === "blocked_capability") return "unsafe-failure";
    if (disposition === "manual_verify") return "awaiting-readback";
    if (disposition === "draft_verify") return "draft-waiting-verification";
    if (disposition === "draft_conflict") return "draft-version-conflict";
    if (disposition === "reconcile_only") return "reconciliation-required";
    if (disposition === "safe_retry") return "safe-retry";
    if (disposition === "unsafe_failure") return "unsafe-failure";
    return String(target?.status || "").toLowerCase();
  }

  function renderReleaseRecovery(release) {
    const panel = $("#releasePlanRecovery");
    const container = $("#releasePlanRecoveryActions");
    const reviewContainer = $("#releasePlanRecoveryReview");
    if (!panel || !container || !reviewContainer) return;
    const supplied = Array.isArray(release?.recovery_actions)
      ? release.recovery_actions.filter((row) => row && row.code)
      : [];
    const actions = supplied.length
      ? supplied
      : (
        !release?.plan_approved && !release?.eligible_for_plan_approval
          ? [{
            code: "refresh_release_state",
            label: "重新检查并定位未完成步骤",
            detail: "重新读取当前商品状态，不会批准、同步或发布。",
          }]
          : []
      );
    panel.hidden = actions.length === 0;
    if (!actions.length) {
      $("#releasePlanRecoveryDetail").textContent = "";
      container.innerHTML = "";
      reviewContainer.innerHTML = "";
      reviewContainer.hidden = true;
      return;
    }
    const primary = actions[0];
    $("#releasePlanRecoveryTitle").textContent = "当前计划还不能批准";
    $("#releasePlanRecoveryDetail").textContent = String(
      primary.detail || "请先完成当前阻断项，再返回批准发布计划。",
    );
    container.innerHTML = actions.map((action) => `
      <button class="button button-secondary" type="button"
        data-release-recovery="${esc(action.code)}">
        ${esc(action.label || "继续处理")}
      </button>
    `).join("");
    const globalPlanReviewRequired = actions.some(
      (action) => action.code === "review_shopee_global_plan",
    );
    reviewContainer.hidden = !globalPlanReviewRequired;
    if (globalPlanReviewRequired) {
      ensureShopeeGlobalPlanReview(
        shopeeGlobalPlanIdentity(currentData),
        true,
      );
      ensureShopeeCategoryDecisionReview(
        shopeeGlobalPlanIdentity(currentData),
        shopeeCategoryDecisionRequired(shopeeGlobalPlanReview.candidate)
          || Boolean(
            shopeeGlobalPlanReview.previewBusy
            && shopeeCategoryDecisionReview.contextKey
              === shopeeGlobalPlanIdentity(currentData)?.key,
          ),
      );
      reviewContainer.innerHTML = shopeeGlobalPlanPanel();
      syncShopeeGlobalPlanApprovalConsent(reviewContainer);
    } else {
      reviewContainer.innerHTML = "";
    }
  }

  async function runReleaseRecovery(actionCode) {
    if (!currentData || pageLoading || releaseSubmitting || approvalSubmitting) return;
    const code = String(actionCode || "");
    if (code === "review_shopee_global_plan") {
      const identity = shopeeGlobalPlanIdentity(currentData);
      if (!identity) {
        $("#releasePlanRecoveryDetail").textContent =
          "当前商品身份或 revision 不完整，无法读取 Shopee Global 候选；未执行任何审批或渠道写入。";
        return;
      }
      await ensureShopeeGlobalPlanReview(identity, true);
      renderReleaseRecovery(currentData.release_v1 || {});
      if (!focusFirstControl([
        "#releasePlanRecoveryReview .channel-category-decision-form select[name='selected_category_identity_digest']",
        "#releasePlanRecoveryReview .channel-category-decision-form input[name='confirm_channel_category_selection']",
        "#releasePlanRecoveryReview .channel-category-attributes-next",
        "#releasePlanRecoveryReview .shopee-global-plan-approval-form input[name='confirm_approved_shopee_global_plan']",
        "#releasePlanRecoveryReview .shopee-global-plan-preview-retry",
        "#releasePlanRecoveryReview .shopee-global-auth-restore",
      ])) {
        focusFirstControl(["#releasePlanRecoveryReview"]);
      }
      return;
    }
    if (code === "refresh_listing_copy") {
      await generateTitleDraft();
      const assistant = $("#listingCopyAssistant");
      assistant?.scrollIntoView({ behavior: "smooth", block: "start" });
      const adopt = assistant?.querySelector(".adopt-title-candidate:not([disabled])");
      (adopt || $("#generateTitleDraftButton"))?.focus();
      return;
    }
    if (code === "adopt_listing_copy") {
      const assistant = $("#listingCopyAssistant");
      assistant?.scrollIntoView({ behavior: "smooth", block: "start" });
      const adopt = assistant?.querySelector(".adopt-title-candidate:not([disabled])");
      (adopt || $("#generateTitleDraftButton"))?.focus();
      $("#titleDraftStatus").textContent =
        "请核对 EN MASTER 后点击“采用”，完成后即可返回批准发布计划。";
      return;
    }
    const item = queueItem(currentQueueKey);
    if (item) {
      await refreshQueueProduct(item, { collectIfMissing: true }).catch(() => {});
    }
  }

  function updateReleasePrimaryAction(data) {
    const release = data?.release_v1 || {};
    const approved = Boolean(release.plan_approved);
    const panel = $("#releasePrimaryActionPanel");
    const button = $("#releasePrimaryActionButton");
    const shopeeButton = $("#shopeeGlobalReleaseButton");
    const ozonButton = $("#ozonReleaseButton");
    const message = $("#releasePrimaryActionMessage");
    const approvalButton = $("#approveReleasePlanButton");
    const legacyPanels = $("#legacyReleaseActionPanels");
    const legacyRunLedger = $("#legacyReleaseRunLedger");
    const oneClickPreview = $("#oneClickExecutionPreview");
    const collectboxPanel = $("#collectboxActionPanel");
    const releaseSection = $("#releasePlan");
    if (
      !panel
      || !button
      || !shopeeButton
      || !ozonButton
      || !message
      || !approvalButton
      || !legacyPanels
      || !legacyRunLedger
      || !oneClickPreview
      || !collectboxPanel
      || !releaseSection
    ) return;

    const unifiedAuthority = approved && oneClickAuthorityAvailable(data);
    releaseSection.classList.toggle(
      "oneclick-unified-action",
      unifiedAuthority,
    );
    approvalButton.hidden = approved;
    panel.hidden = !unifiedAuthority;
    legacyPanels.hidden = unifiedAuthority;
    legacyPanels.setAttribute("aria-hidden", String(unifiedAuthority));
    legacyRunLedger.hidden = unifiedAuthority;
    legacyRunLedger.setAttribute("aria-hidden", String(unifiedAuthority));
    oneClickPreview.hidden = !unifiedAuthority;
    collectboxPanel.hidden = !unifiedAuthority;
    if (!unifiedAuthority) {
      button.disabled = true;
      shopeeButton.disabled = true;
      ozonButton.disabled = true;
      button.textContent = "发布 TikTok";
      message.textContent = approved
        ? "当前旧版计划不具备统一控制面，继续使用原有受治理入口。"
        : "批准当前发布计划后，系统会显示唯一可执行的下一步。";
      return;
    }
    const posting = releaseSubmitting || oneClickExecution.posting;
    const tiktokCollected = Boolean(
      collectboxAction.projection?.action?.platforms?.some(
        (row) => row.platform === "TIKTOK" && row.status === "SUCCEEDED",
      ),
    );
    button.disabled = posting || !tiktokCollected;
    shopeeButton.disabled = posting;
    ozonButton.disabled = posting;
    button.textContent = "发布 TikTok";
    shopeeButton.textContent = "发布 Shopee 全球商品";
    ozonButton.textContent = "发布 Ozon";
    button.dataset.disabledReason = tiktokCollected
      ? ""
      : "请先完成 TikTok 妙手采集箱导入。";
    message.textContent =
      "TikTok、Shopee 全球商品和 Ozon 为三个独立任务；一个平台的状态不会阻挡另外两个。";
    renderCollectboxAction(data);
  }

  function updateReleaseControls(data) {
    const release = data?.release_v1 || {};
    const plan = release.plan || {};
    const approved = Boolean(release.plan_approved);
    const eligible = Boolean(release.eligible_for_plan_approval && plan.plan_id);
    const executionBusy = Boolean(
      releaseSubmitting
      || approvalSubmitting
      || titleDraftSubmitting
      || titleAdoptSubmitting
      || factsSubmitting
      || pageLoading
      || oneClickExecution.previewBusy
      || oneClickExecution.posting
    );

    const releasePlanCheckbox = $("#releasePlanCheckbox");
    releasePlanCheckbox.checked = Boolean(eligible && !approved);
    releasePlanCheckbox.disabled = true;
    if (approved) {
      releasePlanCheckbox.dataset.disabledReason =
        "当前 ReleasePlan 已批准，无需重复操作。";
    } else if (releasePlanApprovalSubmitting) {
      releasePlanCheckbox.dataset.disabledReason =
        "正在批准当前 ReleasePlan。";
    } else {
      releasePlanCheckbox.dataset.disabledReason = translateBlocker(
        (release.blockers || [])[0]
        || "当前发布计划尚未满足批准条件。",
      );
    }
    $("#approveReleasePlanButton").disabled = Boolean(
      approved || !eligible || releasePlanApprovalSubmitting,
    );
    updateReleasePrimaryAction(data);

    const prepared = Boolean(release.miaoshou_prepared);
    const commonReadbackOnly = commonNeedsReadbackReconciliation(release);
    $("#prepareMiaoshouCheckbox").disabled =
      !approved || prepared || executionBusy;
    $("#prepareMiaoshouButton").disabled = Boolean(
      !approved
      || prepared
      || !$("#prepareMiaoshouCheckbox").checked
      || executionBusy,
    );
    $("#prepareMiaoshouButton").textContent = commonReadbackOnly
      ? "只读回读并结案"
      : "同步到妙手待发布";
    $("#prepareMiaoshouCheckbox").parentElement.querySelector(
      "span",
    ).textContent = commonReadbackOnly
      ? "我确认本次只执行妙手 COMMON 官方回读与本地结案，不再次编辑妙手。"
      : "我确认将当前 ReleasePlan 写入妙手公共采集箱并执行回读验证。";
    const overwrite = release.common_overwrite_review || {};
    const overwriteVisible = overwrite.status === "MISMATCH";
    const overwriteExact = Boolean(
      overwrite.plan_id
      && overwrite.plan_id === plan.plan_id
      && overwrite.payload_digest === plan.payload_digest,
    );
    const overwriteReady = Boolean(
      overwriteVisible
      && overwrite.overwrite_allowed
      && overwrite.identity_exact
      && overwrite.readback_non_ambiguous
      && approved
      && overwriteExact
      && !release.run,
    );
    $("#commonOverwriteCheckbox").disabled =
      !overwriteReady || executionBusy;
    $("#commonOverwriteButton").disabled = Boolean(
      !overwriteReady
      || !$("#commonOverwriteCheckbox").checked
      || executionBusy,
    );

    const hasOneClickAuthority = oneClickAuthorityAvailable(data);
    const oneClickView = oneClickProjection();
    const oneClickJobExists = Boolean(oneClickExecution.job);
    const oneClickDispatchEnabled = (
      oneClickView?.dispatch_capability?.enabled !== false
    );
    const publishReady = hasOneClickAuthority
      ? Boolean(
        oneClickExecution.preview
        && !oneClickJobExists
        && !oneClickExecution.postAttempted
        && oneClickDispatchEnabled
        && !oneClickExecution.error
      )
      : Boolean(release.publish_ready);
    const runCounts = releaseRunCounts(release.run);
    const runGroups = releaseTargetGroups(release.run);
    const onlyWaitingForManual = Boolean(
      release.run
      && runCounts.awaitingReadback
      && !runCounts.failed
      && !runCounts.running,
    );
    const runnableTargetCount = hasOneClickAuthority
      ? Number(oneClickExecution.preview?.preparation_pending_count || 0)
      : (
        Number.isInteger(release.runnable_target_count)
          ? release.runnable_target_count
          : runGroups.pending.length
      );
    const ledgerBlocksPublish = Boolean(
      runGroups.running.length || oneClickJobExists,
    );
    const publishAllCheckbox = $("#publishAllCheckbox");
    if (executionBusy) {
      publishAllCheckbox.dataset.disabledReason =
        "当前操作尚未完成；完成后系统会重新计算唯一下一步。";
    } else if (hasOneClickAuthority && oneClickExecution.postAttempted && !oneClickJobExists) {
      publishAllCheckbox.dataset.disabledReason =
        "发布请求结果尚未确认；必须先刷新服务端持久任务，禁止再次提交。";
    } else if (hasOneClickAuthority && oneClickExecution.error) {
      publishAllCheckbox.dataset.disabledReason =
        `${oneClickExecution.error} 未发送任何发布请求。`;
    } else if (hasOneClickAuthority && !oneClickExecution.preview && !oneClickJobExists) {
      publishAllCheckbox.dataset.disabledReason =
        "正在读取服务端批次预览；完成前不会发送发布请求。";
    } else if (hasOneClickAuthority && !oneClickDispatchEnabled) {
      publishAllCheckbox.dataset.disabledReason =
        "统一发布执行能力当前关闭；请按服务端唯一下一步处理。";
    } else if (hasOneClickAuthority && oneClickJobExists) {
      publishAllCheckbox.dataset.disabledReason =
        ONECLICK_TERMINAL_PHASES.has(oneClickExecution.job.phase)
          ? "本计划已有终态持久任务，不能再次提交。"
          : "本计划已有持久任务正在执行；系统只读轮询，不能再次提交。";
    } else if (!publishReady) {
      publishAllCheckbox.dataset.disabledReason =
        $("#publishAllNote").textContent || "发布前条件尚未全部通过。";
    } else if (runnableTargetCount < 1) {
      publishAllCheckbox.dataset.disabledReason =
        "当前没有可安全首发或已证明零写入的目标；请按店铺卡片完成对账、修复或人工验收。";
    } else if (ledgerBlocksPublish) {
      publishAllCheckbox.dataset.disabledReason =
        "仍有目标正在执行或回执尚未落账；为避免重复提交，暂不允许再次发布。";
    } else {
      delete publishAllCheckbox.dataset.disabledReason;
    }
    publishAllCheckbox.disabled = Boolean(
      !publishReady
      || (
        hasOneClickAuthority
          ? oneClickExecution.preview?.start_allowed !== true
          : runnableTargetCount < 1
      )
      || ledgerBlocksPublish
      || executionBusy
    );
    $("#publishAllButton").disabled = Boolean(
      !publishReady
      || (
        hasOneClickAuthority
          ? oneClickExecution.preview?.start_allowed !== true
          : runnableTargetCount < 1
      )
      || ledgerBlocksPublish
      || !$("#publishAllCheckbox").checked
      || executionBusy,
    );
    document.querySelectorAll("[data-price-repair-action]").forEach((button) => {
      const panel = button.closest("[data-price-repair-target]");
      const targetLabel = panel?.dataset.priceRepairTarget || "";
      const repairState = shopeePriceRepairState(targetLabel);
      const confirmed = Boolean(
        panel?.querySelector("[data-price-repair-confirm]")?.checked,
      );
      button.disabled = Boolean(
        executionBusy
        || repairState.phase === "checking"
        || repairState.phase === "repairing"
        || repairState.phase === "reconciling"
        || (
          button.dataset.priceRepairAction === "submit"
          && !confirmed
        ),
      );
    });
  }

  function renderCommonOverwrite(release) {
    const review = release.common_overwrite_review || {};
    const panel = $("#commonOverwritePanel");
    const visible = review.status === "MISMATCH";
    panel.hidden = !visible;
    if (!visible) {
      $("#commonOverwriteDiff").innerHTML = "";
      $("#commonOverwriteConfirmLabel").hidden = true;
      $("#commonOverwriteButton").hidden = true;
      $("#commonOverwriteCheckbox").checked = false;
      $("#commonOverwriteMessage").textContent = "";
      return;
    }
    $("#commonOverwriteIdentity").innerHTML = `
      <strong>现有妙手草稿 ↔ 不可变 ReleasePlan</strong>
      <span>Plan ${esc(review.plan_id || "—")}</span>
      <span>令牌 ${esc(maskedToken(review.confirmation_token || ""))} · payload ${esc(String(review.payload_digest || "").slice(0, 16))}… · revision ${esc(review.expected_revision ?? "—")}</span>
    `;
    $("#commonOverwriteDiff").innerHTML = (review.fields || []).map((row) => `
      <div class="common-overwrite-row ${row.changed ? "changed" : ""}">
        <strong>${esc(row.label || row.field)}${row.changed ? " · 不一致" : " · 一致"}</strong>
        <span>现有：${esc(row.existing_summary || "unavailable")}</span>
        <span>计划：${esc(row.immutable_plan_summary || "unavailable")}</span>
      </div>
    `).join("");
    const canOverwrite = Boolean(
      review.overwrite_allowed
      && review.identity_exact
      && review.readback_non_ambiguous
      && release.plan_approved
      && review.plan_id === release.plan?.plan_id
      && review.payload_digest === release.plan?.payload_digest
      && !release.run,
    );
    $("#commonOverwriteConfirmLabel").hidden = !canOverwrite;
    $("#commonOverwriteButton").hidden = !canOverwrite;
    if (!canOverwrite) {
      $("#commonOverwriteCheckbox").checked = false;
      const blockers = (review.blocking_fields || []).join("、");
      $("#commonOverwriteRisk").textContent = blockers
        ? `身份、绑定或不可识别字段不满足安全门（${blockers}），系统不会提供覆盖操作。`
        : "当前回读不明确、计划身份已变化或已有运行账本，系统不会提供覆盖操作。";
    } else {
      $("#commonOverwriteRisk").textContent =
        "普通“同步到妙手待发布”不会覆盖。只有下方独立确认会按当前已批准计划覆盖允许字段，且只发送一次编辑后立即官方回读。";
    }
  }

  function renderReleaseV1(data) {
    const release = data.release_v1 || {};
    targetRecoveryActions.clear();
    for (const action of (release.target_recovery_actions || [])) {
      if (action?.target_label) {
        targetRecoveryActions.set(String(action.target_label), action);
      }
    }
    const plan = release.plan || {};
    const payload = plan.payload || {};
    const targets = plan.targets || payload.targets || [];
    const planStatus = release.plan_approved
      ? "Kyle 已批准"
      : (release.plan_persisted ? "等待批准" : "尚未持久化");
    $("#releasePlanSummary").innerHTML = `
      <div><span>ReleasePlan</span><strong>${esc(plan.plan_id || "尚未生成")}</strong></div>
      <div><span>商品 / 内容版本</span><strong>revision ${esc(payload.product_revision ?? "—")} · ${esc(payload.content_package_id || plan.content_package_id || "—")}</strong></div>
      <div><span>精确目标</span><strong>${esc(String(targets.length))} 个店铺 · ${esc(maskedToken(plan.confirmation_token))}</strong></div>
      <div><span>批准状态</span><strong>${esc(planStatus)}</strong></div>
    `;
    $("#releasePlanCheckbox").checked = Boolean(release.plan_approved);
    $("#releasePlanMessage").textContent = release.plan_approved
      ? "当前计划已绑定商品 revision、内容包、16 目标范围中的本次选择、来源映射、售价与费用。"
      : (
        release.eligible_for_plan_approval
          ? "计划预览已形成。批准只保存本地不可变计划，不会同步或发布。"
          : translateBlocker((release.blockers || [])[0] || "完成商品事实和内容审批后生成正式计划。")
      );
    renderReleaseRecovery(release);

    $("#prepareMiaoshouCheckbox").checked = Boolean(release.miaoshou_prepared);
    $("#prepareMiaoshouMessage").textContent = release.miaoshou_prepared
      ? preparedMiaoshouMessage(release)
      : (
        commonNeedsReadbackReconciliation(release)
          ? "COMMON 已有一次写入记录；本操作只执行官方回读和本地结案，不会再次编辑妙手。"
          : release.plan_approved
          ? "等待你的独立确认；此动作只准备妙手待发布商品，不会提交站点发布。"
          : "先批准当前 ReleasePlan。"
      );
    renderCommonOverwrite(release);

    const adapterBlockers = release.adapter_blockers || [];
    const ledgerGroups = releaseTargetGroups(release.run);
    const runnableTargetCount = Number.isInteger(release.runnable_target_count)
      ? release.runnable_target_count
      : ledgerGroups.pending.length;
    if (release.publish_ready && runnableTargetCount > 0) {
      const preservedCount = (
        ledgerGroups.draftVerify.length
        + ledgerGroups.draftConflict.length
        + ledgerGroups.reconcileOnly.length
        + ledgerGroups.unsafeFailure.length
        + ledgerGroups.blockedCapability.length
        + ledgerGroups.manualVerify.length
      );
      $("#publishAllNote").textContent =
        `本次只继续发布 ${runnableTargetCount} 个从未提交或已证明零写入的目标；`
        + `${preservedCount} 个待核验、对账或人工验收目标保持原状态且不会重发。`;
    } else if (ledgerGroups.draftVerify.length) {
      $("#publishAllNote").textContent =
        `${targetNamesForLedger(ledgerGroups.draftVerify)} 只有妙手草稿，尚未提交店铺；`
        + "先完成草稿核验和一次性店铺提交，一键重发保持关闭。";
    } else if (ledgerGroups.draftConflict.length) {
      $("#publishAllNote").textContent =
        `${targetNamesForLedger(ledgerGroups.draftConflict)} 的妙手草稿存在版本冲突，尚未提交店铺；`
        + "必须先读取最新草稿，禁止直接覆盖或重建。";
    } else if (ledgerGroups.reconcileOnly.length) {
      $("#publishAllNote").textContent =
        `${targetNamesForLedger(ledgerGroups.reconcileOnly)} 已有外部结果，`
        + "只能回读/对账，禁止重发；一键发布保持关闭。";
    } else if (ledgerGroups.blockedCapability.length) {
      $("#publishAllNote").textContent =
        `${targetNamesForLedger(ledgerGroups.blockedCapability)} 当前缺少受治理的自动执行能力；`
        + "请查看对应店铺卡片的唯一解决方案。一键发布保持关闭，其他独立店铺不受影响。";
    } else if (ledgerGroups.unsafeFailure.length) {
      $("#publishAllNote").textContent =
        `${targetNamesForLedger(ledgerGroups.unsafeFailure)} 的失败边界尚未证实，`
        + "查明外部结果前禁止重发；一键发布保持关闭。";
    } else if (ledgerGroups.manualVerify.length) {
      $("#publishAllNote").textContent =
        `${targetNamesForLedger(ledgerGroups.manualVerify)} 已提交并等待人工验收，`
        + "不会自动重发；一键发布保持关闭。";
    } else if (ledgerGroups.running.length) {
      $("#publishAllNote").textContent =
        `${targetNamesForLedger(ledgerGroups.running)} 正在执行或结果尚未落账；`
        + "先完成回读/恢复处置，一键发布保持关闭。";
    } else if (ledgerGroups.safeRetry.length) {
      $("#publishAllNote").textContent =
        `${targetNamesForLedger(ledgerGroups.safeRetry)} 明确在提交前失败；`
        + "先修复阻塞，再使用原计划安全重试。";
    } else {
      $("#publishAllNote").textContent = release.publish_ready
        ? `将按当前令牌执行 ${targets.length} 个已选目标；成功目标不会重复发布。`
        : (
          !release.miaoshou_prepared
            ? "先完成妙手待发布写入和回读。"
            : (adapterBlockers.length
              ? `仍有 ${adapterBlockers.length} 项统一适配器审计未完成；正式按钮保持关闭，不会调用旧发布函数。`
              : "等待当前计划的所有发布前条件通过。")
        );
    }
    $("#publishAllCheckbox").checked = false;

    const run = release.run;
    if (!run) {
      $("#releaseRunLedger").textContent =
        "当前没有发布运行。批准计划不会自动触发外部写入。";
    } else {
      const statusNames = {
        PENDING: "等待执行",
        RUNNING: "执行中",
        FAILED: "失败 · 状态待核验",
        PARTIAL_FAILED: "部分失败",
        SUCCEEDED: "回读成功",
        SUBMITTED_UNVERIFIED: "已提交 · 待人工验收",
        MANUALLY_VERIFIED: "Kyle 已人工验收",
        SUPERSEDED: "已废止",
      };
      $("#releaseRunLedger").innerHTML = `
        <div class="run-ledger-head">
          <strong>${esc(run.run_id || "ReleaseRun")}</strong>
          <span>${esc(releaseRunLabel(run))}</span>
        </div>
        <div class="run-target-grid">
          ${(run.targets || []).map((target) => {
            const targetDetail = releaseTargetDetail(target);
            return `
            <article class="run-target ${esc(releaseTargetCssClass(target))}"
              data-target-label="${esc(target.target_label)}" tabindex="-1">
              <span>${esc(targetDisplayName(target.target_label))}</span>
              <strong>${esc(releaseTargetLabel(target, statusNames))}</strong>
              <small>尝试 ${esc(String(target.attempts || 0))} 次${target.external_id ? ` · 外部 ID ${esc(target.external_id)}` : ""}</small>
              ${targetDetail ? `<p>${esc(targetDetail)}</p>` : ""}
              ${shopeePriceRepairPanel(target)}
              ${targetScopedActionPanel(target)}
              ${target.status === "SUBMITTED_UNVERIFIED" ? `
                <form class="manual-verification-form" data-target-label="${esc(target.target_label)}">
                  <label>
                    <span>平台商品 ID</span>
                    <input name="marketplace_product_id" autocomplete="off" maxlength="128" required
                      placeholder="从店铺后台复制商品 ID">
                  </label>
                  <label class="manual-verification-confirm">
                    <input name="all_checks_confirmed" type="checkbox" required>
                    <span>我已确认该店同一 Seller SKU 只保留 1 个在售商品，并核对商品身份、标题、售价、图片、重量和尺寸均与本次计划一致。</span>
                  </label>
                  <button class="button button-secondary" type="submit">记录 Kyle 人工验收</button>
                  <span class="manual-verification-message" role="status"></span>
                </form>
              ` : ""}
            </article>`;
          }).join("")}
        </div>
      `;
    }
    ensureCollectboxAction(data);
    renderCollectboxAction(data);
    updateReleaseControls(data);
  }

  function currentReleaseBody(extra = {}) {
    const release = currentData?.release_v1 || {};
    const plan = release.plan || {};
    return {
      offer_id: currentData?.product?.offer_id,
      seller_sku: currentData?.product?.seller_sku_candidate,
      publication_targets: [...(currentData?.publication_scope?.selected_labels || [])],
      plan_id: plan.plan_id,
      confirmation_token: plan.confirmation_token,
      ...extra,
    };
  }

  function adoptWorkflowDashboard(dashboard) {
    if (!dashboard) return;
    currentData = dashboard;
    const key = productKey(
      dashboard.product?.offer_id,
      dashboard.product?.seller_sku_candidate,
    );
    loadedQueueKey = key;
    const item = queueItem(key);
    if (item) item.data = dashboard;
    render(dashboard);
    renderQueue();
  }

  async function postReleaseAction(path, body, { expectedStatus = null } = {}) {
    const response = await fetch(path, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({
      ok: false,
      error: `服务返回 HTTP ${response.status}`,
    }));
    if (expectedStatus !== null && response.status !== expectedStatus) {
      const error = new Error(
        `服务返回 HTTP ${response.status}，但本操作要求 HTTP ${expectedStatus}`,
      );
      error.payload = payload;
      throw error;
    }
    if (!response.ok || payload.ok === false) {
      const targetDetails = (payload.adapter_blockers || [])
        .slice(0, 3)
        .map((row) => `${row.target}: ${row.detail}`)
        .join("；");
      const error = new Error(
        `${payload.error || `服务返回 HTTP ${response.status}`}${targetDetails ? `（${targetDetails}）` : ""}`,
      );
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function currentReleaseTarget(targetLabel) {
    return (currentData?.release_v1?.run?.targets || [])
      .find((target) => target.target_label === targetLabel);
  }

  async function previewShopeePriceRepair(targetLabel) {
    const target = currentReleaseTarget(targetLabel);
    if (
      releaseSubmitting
      || !currentData
      || !shopeePriceRepairEligible(target)
    ) return;
    setShopeePriceRepairState(targetLabel, {
      phase: "checking",
      message: "正在只读核对当前已批准计划、原商品身份和价格差异…",
    });
    renderReleaseV1(currentData);
    const params = new URLSearchParams({
      offer_id: currentData.product?.offer_id || "",
      target_label: targetLabel,
    });
    try {
      const response = await fetch(
        `/api/product-workspace/release-target/shopee-price-repair-preview?${params}`,
        { headers: { Accept: "application/json" } },
      );
      const payload = await response.json().catch(() => ({
        ok: false,
        error: `服务返回 HTTP ${response.status}`,
      }));
      if (!response.ok || payload.ok === false) {
        const error = new Error(
          payload.error || `服务返回 HTTP ${response.status}`,
        );
        error.payload = payload;
        throw error;
      }
      const release = currentData?.release_v1 || {};
      const plan = release.plan || {};
      const currentTarget = currentReleaseTarget(targetLabel);
      const previewExact = Boolean(
        payload.repair_allowed === true
        && payload.target_label === targetLabel
        && payload.plan_id
        && payload.plan_id === plan.plan_id
        && payload.payload_digest === plan.payload_digest
        && Number(payload.expected_revision) === Number(
          currentData?.product?.revision,
        )
        && payload.payload_digest
        && payload.preflight_digest
        && release.plan_approved
        && shopeePriceRepairEligible(currentTarget)
      );
      if (!previewExact) {
        throw new Error(
          "只读检查未返回当前已批准计划的精确修复许可。",
        );
      }
      setShopeePriceRepairState(targetLabel, {
        phase: "preview",
        preview: payload,
        message: "只读检查通过。确认后只会原地修正该站点价格并立即官方回读。",
      });
      showError("");
    } catch (error) {
      setShopeePriceRepairState(targetLabel, {
        phase: "error",
        message: `${friendlyError(error.message)} 未获得修复许可，确认与修复按钮保持隐藏。`,
      });
    }
    if (currentData) renderReleaseV1(currentData);
  }

  async function submitShopeePriceRepair(targetLabel) {
    const state = shopeePriceRepairState(targetLabel);
    const preview = state.preview || {};
    const panel = document.querySelector(
      `[data-price-repair-target="${CSS.escape(targetLabel)}"]`,
    );
    const confirmed = Boolean(
      panel?.querySelector("[data-price-repair-confirm]")?.checked,
    );
    const release = currentData?.release_v1 || {};
    const plan = release.plan || {};
    const target = currentReleaseTarget(targetLabel);
    const exact = Boolean(
      currentData
      && !releaseSubmitting
      && confirmed
      && state.phase === "preview"
      && preview.repair_allowed === true
      && preview.target_label === targetLabel
      && preview.plan_id === plan.plan_id
      && preview.payload_digest === plan.payload_digest
      && Number(preview.expected_revision) === Number(
        currentData?.product?.revision,
      )
      && preview.payload_digest
      && preview.preflight_digest
      && release.plan_approved
      && shopeePriceRepairEligible(target)
    );
    if (!exact) return;

    releaseSubmitting = true;
    setShopeePriceRepairState(targetLabel, {
      ...state,
      phase: "repairing",
      message: "正在再次核对身份；通过后只发送一次原地修价，并等待官方回读…",
    });
    renderReleaseV1(currentData);

    try {
      await postReleaseAction(
        "/api/product-workspace/release-target/shopee-price-repair",
        currentReleaseBody({
          target_label: targetLabel,
          expected_revision: preview.expected_revision,
          payload_digest: preview.payload_digest,
          preflight_digest: preview.preflight_digest,
          confirm_shopee_price_repair: true,
          approved_by: "Kyle",
        }),
      );
    } catch (error) {
      const payload = error.payload || {};
      const message = payload.durable_state_uncertain
        ? "外部修价结果或本地回执仍不确定；禁止再次修复，请只做人工对账。"
        : (
          payload.reconciliation_required
            ? "原地修价结果待对账；系统已停止，禁止再次修复或重发商品。"
            : `${friendlyError(error.message)} 为避免重复写入，本目标的修复按钮保持关闭。`
        );
      setShopeePriceRepairState(targetLabel, {
        phase: "terminal",
        message,
      });
      releaseSubmitting = false;
      if (currentData) renderReleaseV1(currentData);
      showError(message);
      return;
    }

    setShopeePriceRepairState(targetLabel, {
      phase: "succeeded",
      message: "该站点价格已原地修正并完成官方精确回读；正在刷新发布账本。",
    });
    if (currentData) renderReleaseV1(currentData);
    try {
      const latest = await fetchDashboard(
        currentData.product?.offer_id,
        currentData.publication_scope?.selected_labels || [],
      );
      adoptWorkflowDashboard(latest);
      showError("");
    } catch (error) {
      const message = (
        "原地修价已成功，但最新账本暂时无法刷新；禁止再次修复，请执行只读刷新。"
      );
      setShopeePriceRepairState(targetLabel, {
        phase: "succeeded",
        message,
      });
      if (currentData) renderReleaseV1(currentData);
      showError(`${message} ${friendlyError(error.message)}`);
    } finally {
      releaseSubmitting = false;
      updateReleaseControls(currentData || {});
    }
  }

  async function reconcileShopeePriceRepair(targetLabel) {
    const target = currentReleaseTarget(targetLabel);
    if (
      !currentData
      || releaseSubmitting
      || shopeePriceRepairLifecycle(target) !== "RECONCILIATION_REQUIRED"
    ) return;

    releaseSubmitting = true;
    setShopeePriceRepairState(targetLabel, {
      phase: "reconciling",
      message: (
        "正在核对不可变计划与既有一次写入证据；"
        + "随后仅调用官方 GET 回读，零平台写入…"
      ),
    });
    renderReleaseV1(currentData);
    const params = new URLSearchParams({
      offer_id: currentData.product?.offer_id || "",
      target_label: targetLabel,
    });
    try {
      const previewResponse = await fetch(
        (
          "/api/product-workspace/release-target/"
          + `shopee-price-reconciliation-preview?${params}`
        ),
        { headers: { Accept: "application/json" } },
      );
      const preview = await previewResponse.json().catch(() => ({
        ok: false,
        error: `服务返回 HTTP ${previewResponse.status}`,
      }));
      if (!previewResponse.ok || preview.ok === false) {
        const error = new Error(
          preview.error || `服务返回 HTTP ${previewResponse.status}`,
        );
        error.payload = preview;
        throw error;
      }
      const plan = currentData?.release_v1?.plan || {};
      const exact = Boolean(
        preview.reconciliation_allowed === true
        && preview.mode === "official_get_only_durable_close"
        && preview.target_label === targetLabel
        && preview.plan_id === plan.plan_id
        && preview.payload_digest === plan.payload_digest
        && Number(preview.expected_revision) === Number(
          currentData?.product?.revision,
        )
        && preview.preflight_digest
        && preview.operation_digest
        && currentData?.release_v1?.plan_approved
        && shopeePriceRepairLifecycle(
          currentReleaseTarget(targetLabel),
        ) === "RECONCILIATION_REQUIRED"
      );
      if (!exact) {
        throw new Error("只读对账身份已变化，请刷新后重试。");
      }
      await postReleaseAction(
        (
          "/api/product-workspace/release-target/"
          + "shopee-price-reconciliation"
        ),
        currentReleaseBody({
          target_label: targetLabel,
          expected_revision: preview.expected_revision,
          payload_digest: preview.payload_digest,
          preflight_digest: preview.preflight_digest,
          operation_digest: preview.operation_digest,
          confirm_shopee_price_reconciliation: true,
          approved_by: "Kyle",
        }),
      );
      setShopeePriceRepairState(targetLabel, {
        phase: "reconciled",
        message: (
          "挂牌价已通过官方 GET 验证并完成本地结案；"
          + "SIP 差异保留给财务审查，未声明利润已验证。"
        ),
      });
      renderReleaseV1(currentData);
      const latest = await fetchDashboard(
        currentData.product?.offer_id,
        currentData.publication_scope?.selected_labels || [],
      );
      adoptWorkflowDashboard(latest);
      showError("");
    } catch (error) {
      const message = (
        `${friendlyError(error.message)} `
        + "未再次修价或重发；本地账本保持待对账，可稍后重新执行只读回读。"
      );
      setShopeePriceRepairState(targetLabel, {
        phase: "reconciliation-error",
        message,
      });
      if (currentData) renderReleaseV1(currentData);
      showError(message);
    } finally {
      releaseSubmitting = false;
      updateReleaseControls(currentData || {});
    }
  }

  async function submitManualTargetVerification(form) {
    if (!currentData || releaseSubmitting) return;
    const targetLabel = form.dataset.targetLabel || "";
    const productId = form.elements.marketplace_product_id?.value?.trim() || "";
    const confirmed = Boolean(form.elements.all_checks_confirmed?.checked);
    const message = form.querySelector(".manual-verification-message");
    const button = form.querySelector("button[type='submit']");
    if (!productId || !confirmed) {
      if (message) {
        message.textContent = "请填写平台商品 ID，并完成全部字段核对。";
      }
      return;
    }
    releaseSubmitting = true;
    if (button) button.disabled = true;
    if (message) message.textContent = "正在把人工验收证据写入本地发布账本…";
    updateReleaseControls(currentData);
    try {
      const payload = await postReleaseAction(
        "/api/product-workspace/release-target/manual-verify",
        currentReleaseBody({
          target_label: targetLabel,
          marketplace_product_id: productId,
          verified_by: "Kyle",
          user_verified: true,
          checks: {
            identity_matches: true,
            seller_sku_matches: true,
            single_listing_for_sku: true,
            title_matches: true,
            price_matches: true,
            images_match: true,
            logistics_match: true,
          },
        }),
      );
      adoptWorkflowDashboard(payload.dashboard);
      $("#publishRunMessage").textContent =
        `${targetDisplayName(targetLabel)} 已记录 Kyle 人工验收；没有再次提交商品。`;
      showError("");
    } catch (error) {
      const errorMessage = friendlyError(error.message);
      showError(errorMessage);
      if (message) message.textContent = errorMessage;
    } finally {
      releaseSubmitting = false;
      if (button?.isConnected) button.disabled = false;
      updateReleaseControls(currentData || {});
    }
  }

  async function submitOneClickObservationAcceptance(form) {
    if (!currentData || releaseSubmitting) return;
    const targetLabel = String(
      form.dataset.oneclickObservationReview || "",
    );
    const observationDigest = String(
      form.dataset.observationEvidenceDigest || "",
    );
    const confirmed = (
      form.elements.manual_review_accepted?.checked === true
    );
    const message = form.querySelector(".manual-verification-message");
    const button = form.querySelector("button[type='submit']");
    const identity = oneClickExecution.identity;
    const reference = oneClickExecution.job;
    const target = reference?.targets?.find(
      (row) => row.target_label === targetLabel,
    );
    if (
      !identity
      || !reference
      || target?.status !== "SUCCEEDED_MANUAL_REVIEW"
      || !targetLabel.startsWith("shopee:")
      || !confirmed
      || !oneClickDigest(observationDigest)
      || !target.result?.observation_digests?.includes(observationDigest)
    ) {
      if (message) {
        message.textContent =
          "请确认已查看平台派生观察警告；证据状态变化时请先刷新页面。";
      }
      return;
    }
    releaseSubmitting = true;
    if (button) button.disabled = true;
    if (message) {
      message.textContent =
        "正在记录 Kyle 对已验证官方回读中观察警告的验收；不会重新发布。";
    }
    updateReleaseControls(currentData);
    try {
      const payload = await postReleaseAction(
        "/api/product-workspace/release-target/manual-verify",
        currentReleaseBody({
          target_label: targetLabel,
          verified_by: "Kyle",
          user_verified: true,
          manual_review_accepted: true,
          observation_evidence_digest: observationDigest,
        }),
        { expectedStatus: 200 },
      );
      if (
        !Array.isArray(payload.external_writes_performed)
        || payload.external_writes_performed.length
      ) {
        throw oneClickContractError(
          "观察警告验收返回了外部写入证据，已停止本地结案。",
        );
      }
      const returnedJob = payload.job
        || payload.dashboard?.release_v1?.oneclick_controlplane;
      const acceptedJob = validateOneClickProjection(
        returnedJob,
        identity,
        ONECLICK_STATUS_SCHEMA,
        reference,
      );
      const acceptedTarget = acceptedJob.targets.find(
        (row) => row.target_label === targetLabel,
      );
      if (
        acceptedTarget?.status !== "SUCCEEDED"
        || acceptedTarget.requires_human !== false
        || acceptedTarget.next_action === "review_verified_observation_warning"
      ) {
        throw oneClickContractError(
          "观察警告验收后目标未进入 SUCCEEDED，已停止刷新结案状态。",
        );
      }
      oneClickExecution.job = acceptedJob;
      oneClickExecution.statusWarning = "";
      if (payload.dashboard) {
        adoptWorkflowDashboard(payload.dashboard);
      } else {
        renderOneClickExecution(currentData);
        updateReleaseControls(currentData);
      }
      $("#publishRunMessage").textContent =
        `${targetDisplayName(targetLabel)} 已记录 Kyle 观察警告验收并进入 SUCCEEDED；没有重新发布商品。`;
      showError("");
    } catch (error) {
      const errorMessage = friendlyError(error.message);
      showError(errorMessage);
      if (message) {
        message.textContent =
          `${errorMessage} 未自动重发，也未再次提交验收。`;
      }
    } finally {
      releaseSubmitting = false;
      if (button?.isConnected) button.disabled = false;
      updateReleaseControls(currentData || {});
    }
  }

  async function approveReleasePlan() {
    if (!currentData || releasePlanApprovalSubmitting) return;
    releasePlanApprovalSubmitting = true;
    updateReleaseControls(currentData);
    $("#releasePlanMessage").textContent = "正在重新计算精确计划并校验确认令牌…";
    try {
      const payload = await postReleaseAction(
        "/api/product-workspace/release-plan/approve",
        currentReleaseBody({ approved_by: "Kyle", user_approved: true }),
      );
      adoptWorkflowDashboard(payload.dashboard);
      $("#releasePlanMessage").textContent =
        "当前 ReleasePlan 已由 Kyle 批准并持久化；没有发生外部写入。";
      showError("");
    } catch (error) {
      if (error.payload?.dashboard) {
        adoptWorkflowDashboard(error.payload.dashboard);
      }
      const message = friendlyError(error.message);
      showError(message);
      $("#releasePlanMessage").textContent = `${message} 请刷新后重新核对计划。`;
    } finally {
      releasePlanApprovalSubmitting = false;
      updateReleaseControls(currentData || {});
    }
  }

  async function prepareMiaoshou() {
    if (!currentData || releaseSubmitting || !$("#prepareMiaoshouCheckbox").checked) return;
    const readbackOnly = commonNeedsReadbackReconciliation(
      currentData?.release_v1 || {},
    );
    releaseSubmitting = true;
    updateReleaseControls(currentData);
    $("#prepareMiaoshouMessage").textContent = readbackOnly
      ? "正在执行妙手 COMMON 官方只读回读；不会再次编辑妙手…"
      : "正在写入妙手待发布草稿并逐字段回读…";
    try {
      const payload = await postReleaseAction(
        "/api/product-workspace/miaoshou-draft/commit",
        currentReleaseBody({ confirm_miaoshou_write: true }),
      );
      adoptWorkflowDashboard(payload.dashboard);
      const application = payload.result?.spec_label_application || {};
      $("#prepareMiaoshouMessage").textContent =
        payload.mode === "readback_reconciliation_no_write"
          ? "COMMON 官方回读已与计划一致并完成本地结案；本次没有再次编辑妙手。"
          : application.status === "deferred_to_site_draft"
          ? "妙手公共草稿已写入并回读一致；规格显示名将在各站点草稿中按已批准计划写入并校验。"
          : (
            payload.idempotent
              ? "该计划的妙手草稿已回读成功，本次没有重复写入。"
              : "妙手待发布草稿已写入并回读一致；尚未发布到任何站点。"
          );
      showError("");
    } catch (error) {
      if (error.payload?.dashboard) {
        adoptWorkflowDashboard(error.payload.dashboard);
      }
      const message = friendlyError(error.message);
      showError(message);
      $("#prepareMiaoshouMessage").textContent =
        error.payload?.mode === "readback_reconciliation_no_write"
          ? `${message} 本次只进行了官方回读，没有再次编辑妙手。`
          : error.payload?.common_overwrite_review
          ? `${message} 已显示脱敏差异；普通同步不会自动覆盖。`
          : `${message} 失败状态已写入运行账本，可在修复后重试。`;
    } finally {
      releaseSubmitting = false;
      updateReleaseControls(currentData || {});
    }
  }

  async function overwriteMiaoshou() {
    const review = currentData?.release_v1?.common_overwrite_review || {};
    if (
      !currentData
      || releaseSubmitting
      || !$("#commonOverwriteCheckbox").checked
      || review.overwrite_allowed !== true
    ) return;
    releaseSubmitting = true;
    updateReleaseControls(currentData);
    $("#commonOverwriteMessage").textContent =
      "正在重新只读核对身份与差异；通过后只发送一次 COMMON 编辑并执行官方回读…";
    try {
      const payload = await postReleaseAction(
        "/api/product-workspace/miaoshou-draft/commit",
        currentReleaseBody({
          confirm_miaoshou_write: true,
          confirm_miaoshou_overwrite: true,
          approved_by: "Kyle",
          expected_revision: review.expected_revision,
          payload_digest: review.payload_digest,
          overwrite_review_digest: review.review_digest,
        }),
      );
      adoptWorkflowDashboard(payload.dashboard);
      const successMessage =
        "妙手公共草稿已按当前不可变 ReleasePlan 覆盖，并完成逐字段官方回读；未触发认领或发布。";
      $("#commonOverwriteMessage").textContent = successMessage;
      $("#prepareMiaoshouMessage").textContent = successMessage;
      showError("");
    } catch (error) {
      if (error.payload?.dashboard) {
        adoptWorkflowDashboard(error.payload.dashboard);
      }
      const message = error.payload?.reconciliation_required
        ? "编辑结果存在网络歧义，系统已保留 reconciliation evidence 且不会自动重试。"
        : friendlyError(error.message);
      showError(message);
      $("#commonOverwriteMessage").textContent =
        `${message} 确认控件已恢复；请先依据最新回读状态处理。`;
    } finally {
      releaseSubmitting = false;
      updateReleaseControls(currentData || {});
    }
  }

  async function runLegacyReleasePrimaryAction() {
    if (!currentData?.release_v1?.plan_approved) return;
    const canonicalNextAction = currentOneClickNextAction(currentData);
    if (canonicalNextAction?.action === "review_shopee_global_plan") {
      const approvalForm = [...document.querySelectorAll(
        ".shopee-global-plan-approval-form",
      )].find((form) => (
        form.elements.confirm_approved_shopee_global_plan?.checked === true
        && form.querySelector("button[type='submit']")?.disabled === false
      )) || null;
      const approvalConsent = approvalForm?.elements
        ?.confirm_approved_shopee_global_plan;
      const approvalSubmit = approvalForm?.querySelector(
        "button[type='submit']",
      );
      if (
        approvalForm
        && approvalConsent?.checked === true
        && approvalSubmit
        && approvalSubmit.disabled === false
      ) {
        await submitShopeeGlobalPlanApproval(approvalForm);
        return;
      }
      const categoryForm = document.querySelector(
        ".channel-category-decision-form",
      );
      const categorySubmit = categoryForm?.querySelector(
        "button[type='submit']",
      );
      if (
        categoryForm
        && categorySubmit
        && categorySubmit.disabled === false
      ) {
        await submitShopeeCategoryDecision(categoryForm);
        return;
      }
    }
    if (
      canonicalNextAction?.action
        === "review_verified_observation_warning"
    ) {
      const targetLabel = String(
        canonicalNextAction.target_focus
        || canonicalNextAction.target_label
        || "",
      );
      const warningForm = document.querySelector(
        `[data-oneclick-observation-review="${CSS.escape(targetLabel)}"]`,
      );
      if (
        warningForm
        && warningForm.elements.manual_review_accepted?.checked === true
      ) {
        await submitOneClickObservationAcceptance(warningForm);
        return;
      }
    }
    if (
      canonicalNextAction?.action === "verify_submission_in_marketplace"
    ) {
      const targetLabel = String(
        canonicalNextAction.target_focus
        || canonicalNextAction.target_label
        || "",
      );
      const verificationForm = document.querySelector(
        `.run-target[data-target-label="${CSS.escape(targetLabel)}"] `
          + ".manual-verification-form",
      );
      const verificationSubmit = verificationForm?.querySelector(
        "button[type='submit']",
      );
      if (
        verificationForm
        && String(
          verificationForm.elements.marketplace_product_id?.value || "",
        ).trim()
        && verificationForm.elements.all_checks_confirmed?.checked === true
        && verificationSubmit
        && verificationSubmit.disabled === false
      ) {
        await submitManualTargetVerification(verificationForm);
        return;
      }
    }
    if (oneClickExecution.job) {
      if (canonicalNextAction?.action) {
        const button = $("#releasePrimaryActionButton");
        button.dataset.oneclickAction = String(canonicalNextAction.action);
        button.dataset.oneclickTargetFocus = String(
          canonicalNextAction.target_focus || "",
        );
        await routeOneClickNextAction(button);
      } else {
        await retryOneClickReadOnly();
      }
      return;
    }
    if (
      oneClickExecution.error
      || oneClickExecution.statusWarning
      || oneClickExecution.postAttempted
    ) {
      await retryOneClickReadOnly();
      return;
    }
    if (
      !oneClickExecution.preview
      || oneClickExecution.preview.start_allowed !== true
    ) {
      if (canonicalNextAction?.action) {
        const button = $("#releasePrimaryActionButton");
        button.dataset.oneclickAction = String(canonicalNextAction.action);
        button.dataset.oneclickTargetFocus = String(
          canonicalNextAction.target_focus || "",
        );
        await routeOneClickNextAction(button);
      } else {
        await retryOneClickReadOnly();
      }
      return;
    }

    // The single visible click supplies the former redundant confirmation.
    $("#publishAllCheckbox").checked = true;
    await publishSelectedTargets();
  }

  async function runReleasePrimaryAction() {
    if (!currentData?.release_v1?.plan_approved) return;
    // The approved-plan button is the only confirmation interaction in MVP.
    $("#publishAllCheckbox").checked = true;
    await publishSelectedTargets();
  }

  async function publishPlatformBatch(endpoint, platformName) {
    const identity = oneClickExecution.identity;
    if (
      !currentData
      || releaseSubmitting
      || oneClickExecution.posting
      || !identity
    ) return;
    const generation = oneClickExecution.generation;
    const body = currentReleaseBody({ confirm_publish: true });
    releaseSubmitting = true;
    oneClickExecution.posting = true;
    oneClickExecution.postAttempted = true;
    oneClickExecution.error = "";
    updateReleaseControls(currentData);
    renderOneClickExecution(currentData);
    $("#publishRunMessage").textContent = "正在创建唯一持久任务；不会在浏览器中循环调用发布。";
    try {
      const { response, payload } = await boundedJsonFetch(
        endpoint,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
          },
          body: JSON.stringify(body),
        },
        ONECLICK_LOCAL_POST_TIMEOUT_MS,
        `${platformName} 发布任务创建`,
      );
      if (response.status !== 202 || !response.ok || payload.ok === false) {
        const error = new Error(
          response.status !== 202
            ? `服务返回 HTTP ${response.status}，但本操作要求 HTTP 202`
            : payload.error || `服务返回 HTTP ${response.status}`,
        );
        error.status = response.status;
        error.payload = payload;
        error.responseOutcomeUnknown = response.status >= 500;
        throw error;
      }
      if (
        payload.accepted !== true
        || !Array.isArray(payload.external_writes_performed)
        || payload.external_writes_performed.length
      ) {
        throw new Error(`${platformName} 发布任务未返回安全的接受回执。`);
      }
      const job = validateOneClickProjection(
        payload.job,
        identity,
        ONECLICK_STATUS_SCHEMA,
        null,
      );
      if (generation !== oneClickExecution.generation) return;
      oneClickExecution.job = job;
      oneClickExecution.error = "";
      oneClickExecution.statusWarning =
        `${platformName} 独立发布批次已提交；其他平台不会被联动执行。`;
      $("#publishRunMessage").textContent =
        `${platformName} 发布批次已接受；正在读取该平台结果。`;
      showError("");
      scheduleOneClickStatusPoll(generation, 0);
    } catch (error) {
      if (generation !== oneClickExecution.generation) return;
      const message = friendlyError(error.message);
      oneClickExecution.error = message;
      showError(message);
      $("#publishRunMessage").textContent =
        `${message} 本次已结束；可以再次点击一键发布。`;
    } finally {
      if (generation === oneClickExecution.generation) {
        releaseSubmitting = false;
        oneClickExecution.posting = false;
        oneClickExecution.postAttempted = false;
        renderOneClickExecution(currentData);
        updateReleaseControls(currentData || {});
      }
    }
  }

  async function publishSelectedTargets() {
    await publishPlatformBatch(
      "/api/product-workspace/publish-tiktok",
      "TikTok",
    );
  }

  async function runTiktokReleaseAction() {
    if (!currentData?.release_v1?.plan_approved) return;
    await publishSelectedTargets();
  }

  async function runShopeeGlobalReleaseAction() {
    if (!currentData?.release_v1?.plan_approved) return;
    await publishPlatformBatch(
      "/api/product-workspace/publish-shopee-global",
      "Shopee 全球商品",
    );
  }

  async function runOzonReleaseAction() {
    if (!currentData?.release_v1?.plan_approved) return;
    await publishPlatformBatch(
      "/api/product-workspace/publish-ozon",
      "Ozon",
    );
  }

  function render(data) {
    currentData = data;
    const stages = stageModel(data);
    renderProduct(data);
    renderTitleDraft(data);
    renderApproval(data);
    renderStages(stages);
    renderNextStep(data, stages);
    renderImages(data.content || {});
    renderPublicationScope(data.publication_scope || {});
    renderPricingReview(
      data.pricing_review || {},
      data.publication_scope || {},
    );
    renderChannels(
      data.omnichannel_preview || {},
      data.publication_rehearsal || {},
      Boolean(data.actual_release_gate?.ready),
    );
    renderReleaseV1(data);
    // ReleasePlan rendering and one-click execution are separate projections.
    // Initialise the immutable execution identity on every authoritative
    // dashboard render so the three platform buttons cannot be visible while
    // their click handlers still have a null identity and silently do nothing.
    ensureOneClickExecution(data);

    const offer = data.product?.offer_id || $("#offerId").value.trim();
    const studioUrl = `/ai-image-studio?offer_id=${encodeURIComponent(offer)}`;
    $("#workbenchLink").href = studioUrl;
    $("#studioNavLink").href = studioUrl;
    $("#workbenchLink").removeAttribute("aria-disabled");
  }

  function clearCurrentApprovalContext() {
    resetOneClickExecution();
    resetCollectboxAction();
    currentData = null;
    loadedQueueKey = "";
    $("#releasePlanCheckbox").checked = false;
    $("#prepareMiaoshouCheckbox").checked = false;
    $("#publishAllCheckbox").checked = false;
    updateApprovalButton({});
    updateReleaseControls({});
  }

  async function refreshQueueProduct(item, options = {}) {
    if (item.loading && item.promise) {
      if (!options.collectIfMissing) return item.promise;
      return item.promise.catch((error) => {
        if (!missingProductError(error) || item.data) throw error;
        return refreshQueueProduct(item, options);
      });
    }
    const key = productKey(item.offer_id);
    item.loading = true;
    item.activity = options.collectIfMissing
      ? "正在检查本地档案；缺失时将立即从妙手采集"
      : "正在读取最新本地证据";
    item.error = "";
    if (key === currentQueueKey) {
      clearCurrentApprovalContext();
      setLoading(true);
    }
    renderQueue();
    item.promise = (async () => {
      try {
        const requestedTargets = Object.hasOwn(options, "publicationTargets")
          ? options.publicationTargets
          : (item.data?.publication_scope?.selected_labels || null);
        let data;
        try {
          data = await fetchDashboard(
            item.offer_id,
            requestedTargets,
          );
        } catch (error) {
          if (!options.collectIfMissing || !missingProductError(error)) throw error;
          item.activity = "正在从妙手采集箱读取并建立本地商品档案";
          $("#queueMessage").textContent =
            `Offer ${item.offer_id} 正在从妙手采集箱读取；完成后会自动分配 Seller SKU。`;
          renderQueue();
          data = await collectProduct(item.offer_id);
        }
        item.data = data;
        item.seller_sku = data.product?.seller_sku_candidate || "";
        item.error = "";
        if (key === currentQueueKey) {
          loadedQueueKey = key;
          $("#offerId").value = item.offer_id;
          $("#sellerSku").textContent = item.seller_sku
            ? `自动候选 ${item.seller_sku}`
            : "系统读取后自动分配";
          syncCurrentUrl(item);
          // The fetched dashboard is authoritative. Clear the page-level
          // loading gate before rendering it so an eligible plan cannot remain
          // disabled until an unrelated later render happens to run.
          pageLoading = false;
          render(data);
          showError("");
          if (options.collectIfMissing) {
            $("#queueMessage").textContent =
              `Offer ${item.offer_id} 已读取完成；商品事实可在当前页面直接修改。`;
          }
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
        item.activity = "";
        item.promise = null;
        if (key === currentQueueKey) setLoading(false);
        renderQueue();
      }
    })();
    return item.promise;
  }

  async function selectQueueProduct(key, { collectIfMissing = true } = {}) {
    // A short one-click POST has an unknown outcome until the server returns
    // the durable job identity.  Do not let offer switching discard that
    // context.  Once the 202 receipt is stored locally, releaseSubmitting is
    // cleared and switching only cancels this page's read-only polling.
    if (
      approvalSubmitting
      || releaseSubmitting
      || releasePlanApprovalSubmitting
    ) return;
    const item = queueItem(key);
    if (!item) return;
    currentQueueKey = key;
    $("#offerId").value = item.offer_id;
    $("#sellerSku").textContent = item.seller_sku
      ? `自动候选 ${item.seller_sku}`
      : "正在重新核验";
    syncCurrentUrl(item);
    clearCurrentApprovalContext();
    renderQueue();
    await refreshQueueProduct(item, { collectIfMissing }).catch(() => {});
  }

  async function addAndOpenCurrentInput() {
    const offerId = $("#offerId").value.trim();
    if (!validOfferId(offerId)) {
      const message = "Offer ID 必须是 1–32 位数字。";
      showError(message);
      return;
    }
    const item = addToQueue(offerId, { select: true });
    if (!item) return;
    $("#queueMessage").textContent =
      "商品已加入并行队列；正在从妙手采集箱读取并建立本地档案。";
    await selectQueueProduct(productKey(item.offer_id), { collectIfMissing: true });
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

  async function hydrateUnloadedQueueProducts() {
    const pending = queueItems.filter((item) => (
      productKey(item.offer_id) !== currentQueueKey
      && !item.data
      && !item.loading
    ));
    let cursor = 0;
    const worker = async () => {
      while (cursor < pending.length) {
        const item = pending[cursor];
        cursor += 1;
        await refreshQueueProduct(item).catch(() => {});
      }
    };
    await Promise.allSettled(
      Array.from(
        { length: Math.min(QUEUE_REFRESH_CONCURRENCY, pending.length) },
        () => worker(),
      ),
    );
  }

  $("#lookupForm").addEventListener("submit", (event) => {
    event.preventDefault();
    addAndOpenCurrentInput();
  });
  $("#refreshButton").addEventListener("click", () => {
    const item = queueItem(currentQueueKey);
    if (item) refreshQueueProduct(item, { collectIfMissing: true }).catch(() => {});
  });
  $("#nextStepActionButton").addEventListener("click", runWorkflowNextAction);
  $("#refreshAllButton").addEventListener("click", refreshAllQueueProducts);
  $("#refreshChannelsButton").addEventListener("click", () => {
    const item = queueItem(currentQueueKey);
    if (item) refreshQueueProduct(item).catch(() => {});
  });
  $("#publicationTargetGrid").addEventListener("change", (event) => {
    if (!event.target.matches('input[name="publication_target"]')) return;
    pendingPublicationTargets = new Set(
      [...$("#publicationTargetGrid").querySelectorAll(
        'input[name="publication_target"]:checked',
      )].map((input) => input.value),
    );
    updatePublicationScopeControls();
  });
  $("#selectAllTargetsButton").addEventListener("click", () => {
    setPendingPublicationTargets(
      [...$("#publicationTargetGrid").querySelectorAll(
        'input[name="publication_target"]',
      )].map((input) => input.value),
    );
  });
  $("#restoreTargetDefaultsButton").addEventListener("click", () => {
    setPendingPublicationTargets(currentData?.publication_scope?.default_labels || []);
  });
  $("#publicationScopeForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const item = queueItem(currentQueueKey);
    if (!item || !pendingPublicationTargets.size || pageLoading) return;
    refreshQueueProduct(item, {
      publicationTargets: [...pendingPublicationTargets],
    }).catch(() => {});
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
        productKey(item.offer_id) !== key
      ));
      saveQueue();
      renderQueue();
      $("#queueMessage").textContent = "商品已移出队列。";
    }
  });
  $("#approvalForm").addEventListener("submit", (event) => {
    event.preventDefault();
    submitApproval();
  });
  $("#productFactsForm").addEventListener("input", () => {
    if (!currentData || factsSubmitting) return;
    $("#factsEditMessage").textContent =
      "有尚未保存的修改；当前售价仍是上一 revision。保存后会建立新 revision，并重新计算全部国家与店铺售价。";
  });
  $("#productFactsForm").addEventListener("change", (event) => {
    if (!event.target.matches('input[name="selected_sku_key"]')) return;
    const labelInput = [...document.querySelectorAll(".sku-label-input")].find(
      (field) => field.dataset.skuKey === event.target.value,
    );
    if (labelInput) labelInput.disabled = !event.target.checked;
  });
  $("#productFactsForm").addEventListener("submit", (event) => {
    event.preventDefault();
    submitFactsEdit();
  });
  $("#generateTitleDraftButton").addEventListener("click", generateTitleDraft);
  $("#titleCandidateGrid").addEventListener("click", (event) => {
    const button = event.target.closest(".adopt-title-candidate");
    if (!button || button.disabled) return;
    adoptTitleCandidate(button);
  });
  $("#releasePlanApprovalForm").addEventListener("submit", (event) => {
    event.preventDefault();
    approveReleasePlan();
  });
  function updateShopeeCategoryDraft(event) {
    const form = event.target.closest(".channel-category-decision-form");
    if (!form) return false;
    const target = event.target;
    if (target.matches("select[name='selected_category_identity_digest']")) {
      shopeeCategoryDecisionReview.draftIdentityDigest = String(
        target.value || "",
      );
      shopeeCategoryDecisionReview.requiredAttributeSelections = {};
      shopeeCategoryDecisionReview.confirmSelection = false;
      shopeeCategoryDecisionReview.confirmRequiredAttributes = false;
      shopeeCategoryDecisionReview.message = "";
    } else if (target.matches("select[name='selected_brand_identity_digest']")) {
      shopeeCategoryDecisionReview.draftBrandIdentityDigest =
        String(target.value || "");
    } else if (target.matches("select[name='selected_location_identity_digest']")) {
      shopeeCategoryDecisionReview.draftLocationIdentityDigest =
        String(target.value || "");
    } else if (target.matches("[data-category-attribute]")) {
      const digest = String(target.dataset.categoryAttribute || "");
      const kind = String(target.dataset.selectionKind || "");
      if (kind === "TEXT") {
        shopeeCategoryDecisionReview.requiredAttributeSelections[digest] = {
          textValue: String(target.value || ""),
        };
      } else if (kind === "SINGLE") {
        shopeeCategoryDecisionReview.requiredAttributeSelections[digest] = {
          optionDigests: target.value ? [String(target.value)] : [],
        };
      } else if (kind === "MULTI") {
        shopeeCategoryDecisionReview.requiredAttributeSelections[digest] = {
          optionDigests: [...form.querySelectorAll(
            `[data-category-attribute="${CSS.escape(digest)}"]:checked`,
          )].map((input) => String(input.value)),
        };
      }
      shopeeCategoryDecisionReview.confirmRequiredAttributes = false;
    } else if (target.matches(
      "input[name='confirm_channel_category_selection']",
    )) {
      shopeeCategoryDecisionReview.confirmSelection =
        target.checked === true;
    } else if (target.matches(
      "input[name='confirm_seller_stock_quantity']",
    )) {
      shopeeCategoryDecisionReview.confirmSellerStock =
        target.checked === true;
    } else if (target.matches(
      "input[name='confirm_condition_and_preorder']",
    )) {
      shopeeCategoryDecisionReview.confirmConditionAndPreorder =
        target.checked === true;
    } else if (target.matches(
      "input[name='confirm_required_attribute_selections']",
    )) {
      shopeeCategoryDecisionReview.confirmRequiredAttributes =
        target.checked === true;
    } else {
      return false;
    }
    renderOneClickExecution(currentData);
    renderReleaseRecovery(currentData?.release_v1 || {});
    return true;
  }

  function handleShopeeCategoryDecisionClick(event) {
    const retry = event.target.closest(".channel-category-preview-retry");
    if (retry) {
      const identity = shopeeGlobalPlanIdentity(currentData);
      if (
        identity
        && !shopeeCategoryDecisionReview.previewBusy
        && !shopeeCategoryDecisionReview.reconciliationBusy
      ) {
        shopeeCategoryDecisionReview.previewAttempted = false;
        shopeeCategoryDecisionReview.error = "";
        shopeeCategoryDecisionReview.message =
          shopeeCategoryDecisionReview.postAttempted
            ? "正在只读核对已尝试的保存结果；不会重复提交。"
            : "正在刷新官方类目候选…";
        requestShopeeCategoryDecisionPreview(identity);
      }
      return true;
    }
    const attributes = event.target.closest(
      ".channel-category-attributes-next",
    );
    if (attributes) {
      if (!focusFirstControl(["#productFactsPanel", "#content"])) {
        shopeeCategoryDecisionReview.message =
          "当前页面没有可用的商品事实/属性映射控件；请重新读取商品状态。";
        renderOneClickExecution(currentData);
        renderReleaseRecovery(currentData?.release_v1 || {});
      }
      return true;
    }
    return false;
  }

  $("#releasePlanRecovery").addEventListener("change", (event) => {
    if (updateShopeeGlobalPlanApprovalConsent(event)) return;
    updateShopeeCategoryDraft(event);
  });
  $("#releasePlanRecovery").addEventListener("input", (event) => {
    if (event.target.matches("[data-selection-kind='TEXT']")) {
      const digest = String(event.target.dataset.categoryAttribute || "");
      shopeeCategoryDecisionReview.requiredAttributeSelections[digest] = {
        textValue: String(event.target.value || ""),
      };
      shopeeCategoryDecisionReview.confirmRequiredAttributes = false;
    }
  });
  $("#releasePlanRecovery").addEventListener("submit", (event) => {
    const categoryForm = event.target.closest(
      ".channel-category-decision-form",
    );
    if (categoryForm) {
      event.preventDefault();
      submitShopeeCategoryDecision(categoryForm);
      return;
    }
    const globalPlanForm = event.target.closest(
      ".shopee-global-plan-approval-form",
    );
    if (!globalPlanForm) return;
    event.preventDefault();
    submitShopeeGlobalPlanApproval(globalPlanForm);
  });
  $("#releasePlanRecovery").addEventListener("click", (event) => {
    if (handleShopeeCategoryDecisionClick(event)) return;
    const globalRetry = event.target.closest(
      ".shopee-global-plan-preview-retry",
    );
    if (globalRetry) {
      const identity = shopeeGlobalPlanIdentity(currentData);
      if (
        identity
        && !shopeeGlobalPlanReview.previewBusy
      ) {
        shopeeGlobalPlanReview.previewAttempted = false;
        shopeeGlobalPlanReview.error = "";
        requestShopeeGlobalPlanPreview(identity);
      }
      return;
    }
    const authRestore = event.target.closest(".shopee-global-auth-restore");
    if (authRestore) {
      $("#releasePlanRecoveryDetail").textContent =
        "请在 Shopee 授权管理中恢复当前 Global 官方读取授权，然后回到这里重新读取；系统不会猜测或刷新凭据。";
      return;
    }
    const button = event.target.closest("[data-release-recovery]");
    if (!button || button.disabled) return;
    runReleaseRecovery(button.dataset.releaseRecovery);
  });
  $("#prepareMiaoshouCheckbox").addEventListener("change", () => {
    updateReleaseControls(currentData || {});
  });
  $("#prepareMiaoshouButton").addEventListener("click", prepareMiaoshou);
  $("#commonOverwriteCheckbox").addEventListener("change", () => {
    updateReleaseControls(currentData || {});
  });
  $("#commonOverwriteButton").addEventListener("click", overwriteMiaoshou);
  $("#collectboxActionButton").addEventListener(
    "click",
    runCollectboxPrimaryAction,
  );
  $("#releasePrimaryActionButton").addEventListener("click", runTiktokReleaseAction);
  $("#shopeeGlobalReleaseButton").addEventListener("click", runShopeeGlobalReleaseAction);
  $("#ozonReleaseButton").addEventListener("click", runOzonReleaseAction);
  $("#publishAllCheckbox").addEventListener("change", () => {
    updateReleaseControls(currentData || {});
  });
  $("#publishAllButton").addEventListener("click", publishSelectedTargets);
  $("#oneClickExecutionGroups").addEventListener("change", (event) => {
    if (updateShopeeGlobalPlanApprovalConsent(event)) return;
    updateShopeeCategoryDraft(event);
  });
  $("#oneClickExecutionGroups").addEventListener("input", (event) => {
    if (event.target.matches("[data-selection-kind='TEXT']")) {
      const digest = String(event.target.dataset.categoryAttribute || "");
      shopeeCategoryDecisionReview.requiredAttributeSelections[digest] = {
        textValue: String(event.target.value || ""),
      };
      shopeeCategoryDecisionReview.confirmRequiredAttributes = false;
    }
  });
  $("#oneClickExecutionGroups").addEventListener("submit", (event) => {
    const categoryForm = event.target.closest(
      ".channel-category-decision-form",
    );
    if (categoryForm) {
      event.preventDefault();
      submitShopeeCategoryDecision(categoryForm);
      return;
    }
    const globalPlanForm = event.target.closest(
      ".shopee-global-plan-approval-form",
    );
    if (globalPlanForm) {
      event.preventDefault();
      submitShopeeGlobalPlanApproval(globalPlanForm);
      return;
    }
    const form = event.target.closest(".oneclick-observation-review-form");
    if (!form) return;
    event.preventDefault();
    submitOneClickObservationAcceptance(form);
  });
  $("#oneClickExecutionGroups").addEventListener("click", (event) => {
    if (handleShopeeCategoryDecisionClick(event)) return;
    const globalRetry = event.target.closest(
      ".shopee-global-plan-preview-retry",
    );
    if (globalRetry) {
      const identity = shopeeGlobalPlanIdentity(currentData);
      if (
        !shopeeGlobalPlanReview.previewBusy
        && identity
      ) {
        shopeeGlobalPlanReview.previewAttempted = false;
        shopeeGlobalPlanReview.error = "";
        requestShopeeGlobalPlanPreview(identity);
      }
      return;
    }
    const authRestore = event.target.closest(".shopee-global-auth-restore");
    if (authRestore) {
      focusOneClickTarget(SHOPEE_GLOBAL_CONTROL_TARGET);
      oneClickExecution.statusWarning =
        "请在 Shopee 授权管理中恢复当前 Global 官方读取授权，然后回到这里重新读取；系统不会猜测或刷新凭据。";
      renderOneClickExecution(currentData);
      return;
    }
    const target = event.target.closest("[data-oneclick-target]");
    if (!target) return;
    focusOneClickTarget(target.dataset.oneclickTarget || "");
  });
  $("#releaseRunLedger").addEventListener("submit", (event) => {
    const form = event.target.closest(".manual-verification-form");
    if (!form) return;
    event.preventDefault();
    submitManualTargetVerification(form);
  });
  $("#releaseRunLedger").addEventListener("change", (event) => {
    const scoped = event.target.closest("[data-target-scoped-confirm]");
    if (scoped) {
      const button = scoped.closest("[data-target-scoped-target]")?.querySelector('[data-target-scoped-action="submit"]');
      if (button) button.disabled = releaseSubmitting || !scoped.checked;
      return;
    }
    const checkbox = event.target.closest("[data-price-repair-confirm]");
    if (!checkbox) return;
    const panel = checkbox.closest("[data-price-repair-target]");
    const button = panel?.querySelector(
      '[data-price-repair-action="submit"]',
    );
    if (button) button.disabled = releaseSubmitting || !checkbox.checked;
  });
  $("#releaseRunLedger").addEventListener("click", (event) => {
    const scoped = event.target.closest("[data-target-scoped-action]");
    if (scoped && !scoped.disabled) {
      if (scoped.dataset.targetScopedAction === "preview") previewTargetScopedAction(scoped.dataset.targetLabel || "");
      if (scoped.dataset.targetScopedAction === "submit") submitTargetScopedAction(scoped.dataset.targetLabel || "");
      return;
    }
    const button = event.target.closest("[data-price-repair-action]");
    if (!button || button.disabled) return;
    const targetLabel = button.dataset.targetLabel || "";
    if (button.dataset.priceRepairAction === "preview") {
      previewShopeePriceRepair(targetLabel);
      return;
    }
    if (button.dataset.priceRepairAction === "submit") {
      submitShopeePriceRepair(targetLabel);
      return;
    }
    if (button.dataset.priceRepairAction === "reconcile") {
      reconcileShopeePriceRepair(targetLabel);
    }
  });

  const initial = new URLSearchParams(window.location.search);
  queueItems = readQueue();
  const initialOffer = initial.get("offer_id");
  let initialItem = null;
  if (validOfferId(initialOffer)) {
    initialItem = addToQueue(initialOffer, { select: true });
  } else if (queueItems.length) {
    initialItem = queueItems[0];
    currentQueueKey = productKey(initialItem.offer_id);
  } else {
    initialItem = addToQueue($("#offerId").value, { select: true });
  }
  renderQueue();
  if (initialItem) {
    $("#offerId").value = initialItem.offer_id;
    $("#sellerSku").textContent = initialItem.seller_sku
      ? `自动候选 ${initialItem.seller_sku}`
      : "正在读取并自动分配";
    syncCurrentUrl(initialItem);
    refreshQueueProduct(initialItem, { collectIfMissing: true })
      .catch(() => {})
      .finally(() => hydrateUnloadedQueueProducts());
  }
})();
