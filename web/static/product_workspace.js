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
  const ONECLICK_PREVIEW_SCHEMA = "release-batch-preparation/v1";
  const ONECLICK_STATUS_SCHEMA = "oneclick-release-status/v1";
  const ONECLICK_TERMINAL_PHASES = new Set([
    "SUCCEEDED",
    "WAITING_MANUAL_ACCEPTANCE",
    "BLOCKED",
    "SYSTEMIC_STOPPED",
  ]);
  const ONECLICK_POLL_INTERVAL_MS = 750;
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
    statusBusy: false,
    error: "",
    statusWarning: "",
    failureAction: null,
    timer: null,
    controller: null,
    finalDashboardRefreshed: false,
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
      Object.hasOwn(release, "canonical_next_action")
      || Object.hasOwn(release, "oneclick_controlplane")
      || Array.isArray(release.target_recovery_actions),
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
    oneClickExecution.statusBusy = false;
    oneClickExecution.error = "";
    oneClickExecution.statusWarning = "";
    oneClickExecution.failureAction = null;
    oneClickExecution.controller = null;
    oneClickExecution.finalDashboardRefreshed = false;
    if (wasPosting) releaseSubmitting = false;
  }

  function oneClickContractError(message) {
    const error = new Error(message);
    error.oneClickContractError = true;
    return error;
  }

  function oneClickDigest(value) {
    return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
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

  function validateOneClickProjection(projection, identity, schema, reference = null) {
    if (!projection || typeof projection !== "object" || Array.isArray(projection)) {
      throw oneClickContractError("统一发布控制面返回了无效状态。");
    }
    if (projection.schema_version !== schema) {
      throw oneClickContractError("统一发布控制面版本不匹配，请刷新后重试。");
    }
    const isStatus = schema === ONECLICK_STATUS_SCHEMA;
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
        Object.hasOwn(digests, key) && oneClickDigest(digests[key])
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
    for (const target of projection.targets) {
      const targetDigests = target?.digests;
      const dependency = target?.dependency;
      const reason = target?.reason;
      if (
        !target
        || typeof target !== "object"
        || Array.isArray(target)
        || typeof target.target_label !== "string"
        || !target.target_label
        || labels.has(target.target_label)
        || !ONECLICK_TARGET_STATUSES.has(target.status)
        || (
          isUnpreparedStatus
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
          target.target_label === "miaoshou:COMMON"
            ? target.storefront !== false
            : target.storefront !== true
        )
        || !dependency
        || typeof dependency !== "object"
        || Array.isArray(dependency)
        || dependency.policy_version !== "oneclick-target-dependency/v1"
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
      labels.add(target.target_label);
    }
    if (reference) {
      const previousTargets = new Map(
        (reference.targets || []).map((target) => [
          target.target_label,
          target,
        ]),
      );
      if (previousTargets.size !== projection.targets.length) {
        throw oneClickContractError(
          "统一发布控制面的目标集合已漂移，已停止提交。",
        );
      }
      for (const target of projection.targets) {
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
    const common = projection.targets.find(
      (target) => target.target_label === "miaoshou:COMMON",
    );
    for (const target of projection.targets) {
      const dependency = target.dependency;
      const isTikTok = target.target_label.startsWith("tiktok:");
      const dependencyExact = isTikTok
        ? (
          dependency.prerequisite_target === "miaoshou:COMMON"
          && (
            common
              ? dependency.prerequisite_status === common.status
                && (
                  common.status === "SUCCEEDED"
                    ? (
                      dependency.state === "SATISFIED"
                      && dependency.satisfied === true
                    )
                    : ["PENDING", "PREPARING", "READY", "DISPATCHING"]
                      .includes(common.status)
                      ? (
                        dependency.state === "WAITING"
                        && dependency.satisfied === false
                      )
                      : (
                        dependency.state === "BLOCKED"
                        && dependency.satisfied === false
                      )
                )
              : (
                dependency.prerequisite_status === "MISSING"
                && dependency.state === "BLOCKED"
                && dependency.satisfied === false
              )
          )
        )
        : (
          dependency.state === "SATISFIED"
          && dependency.satisfied === true
          && dependency.prerequisite_target === null
          && dependency.prerequisite_status === null
        );
      if (!dependencyExact) {
        throw oneClickContractError(
          "统一发布控制面的店铺依赖证据不一致，已停止提交。",
        );
      }
      if (
        target.next_action_target
        && !labels.has(target.next_action_target)
        && !(
          isTikTok
          && !common
          && target.next_action_target === "miaoshou:COMMON"
        )
      ) {
        throw oneClickContractError(
          "统一发布控制面的下一步目标无效，已停止提交。",
        );
      }
    }
    const storefronts = projection.targets.filter((target) => target.storefront);
    const runnable = storefronts.filter((target) => target.runnable_now);
    const summary = projection.summary;
    const capability = projection.dispatch_capability;
    if (
      !Number.isInteger(projection.storefront_count)
      || projection.storefront_count !== storefronts.length
      || !Number.isInteger(projection.control_row_count)
      || projection.control_row_count
        !== projection.targets.length - storefronts.length
      || !Number.isInteger(projection.runnable_target_count)
      || projection.runnable_target_count !== runnable.length
      || !summary
      || typeof summary !== "object"
      || Array.isArray(summary)
      || !["will_dispatch", "manual_after_submit", "blocked", "already_terminal"]
        .every((key) => (
          Array.isArray(summary[key])
          && summary[key].every((label) => labels.has(label))
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
      DISPATCHING: "提交中",
      SUCCEEDED: "已完成官方回读",
      SUCCEEDED_MANUAL_REVIEW: "官方硬事实已验证 · 等待观察警告验收",
      SUBMITTED_UNVERIFIED: "已提交，等待人工验收",
      FAILED_PRE_SUBMIT: "提交前安全失败",
      RECONCILIATION_REQUIRED: "需要对账，禁止重发",
      BLOCKED_AUTH: "渠道授权阻断",
      BLOCKED_INVENTORY: "库存决策阻断",
      BLOCKED_CAPABILITY: "渠道能力阻断",
      BLOCKED_SOURCE_IDENTITY: "来源身份阻断",
      BLOCKED_SKU_LINEAGE: "Seller SKU 血缘阻断",
    };
    return labels[String(status || "")] || "状态由服务端核定";
  }

  function oneClickReasonText(target) {
    const reason = target?.reason || {};
    const categoryLabels = {
      AUTH: "渠道授权尚未就绪",
      INVENTORY: "缺少已批准的库存决策",
      CAPABILITY: "该渠道的受治理能力尚未开放",
      CONTENT: "已批准的内容、类目或属性事实不完整",
      LOGISTICS: "已批准的物流策略或包裹事实不完整",
      SOURCE_IDENTITY: "来源商品身份尚未核准",
      SKU_LINEAGE: "Seller SKU 血缘尚未核准",
      DEPENDENCY: "前置目标尚未完成",
      SYSTEMIC_CONTRACT: "批次身份合同不完整",
    };
    return categoryLabels[String(reason.category || "")]
      || oneClickActionText(target?.next_action);
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
        <button class="button button-secondary" type="submit">
          记录 Kyle 观察警告验收
        </button>
        <span class="manual-verification-message" role="status" aria-live="polite"></span>
      </form>
    `;
  }

  function renderOneClickExecution(data) {
    const container = $("#oneClickExecutionGroups");
    const message = $("#oneClickExecutionMessage");
    const nextButton = $("#oneClickNextActionButton");
    if (!container || !message || !nextButton) return;
    const identity = oneClickIdentity(data);
    const projection = oneClickProjection();
    const headings = {
      automatic: "本轮自动执行",
      manual: "提交后人工验收",
      dependency: "依赖等待",
      preSubmit: "提交前安全失败",
      reconciliation: "需要对账",
      blocked: "阻断不执行",
      terminal: "已终态",
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
    for (const target of (projection?.targets || [])) {
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
                 <button type="button" class="oneclick-target-card"
                   data-oneclick-target="${esc(target.target_label)}">
                   <strong>${esc(targetDisplayName(target.target_label))}</strong>
                   <span>${esc(oneClickStatusText(target.status))}</span>
                   <small>${esc(oneClickReasonText(target))}</small>
                 </button>
                 ${oneClickObservationWarningForm(target)}
               </div>
             `).join("")}
           </div>
        </section>
      `).join("") || "<p>尚无服务端店铺状态。</p>";

    const nextAction = currentOneClickNextAction(data);
    if (nextAction?.action) {
      nextButton.hidden = false;
      nextButton.disabled = false;
      nextButton.dataset.oneclickAction = String(nextAction.action);
      nextButton.dataset.oneclickTargetFocus = String(nextAction.target_focus || "");
      nextButton.textContent = oneClickActionText(nextAction.action);
    } else {
      nextButton.hidden = true;
      nextButton.disabled = true;
      delete nextButton.dataset.oneclickAction;
      delete nextButton.dataset.oneclickTargetFocus;
    }

    if (!identity) {
      message.textContent = "批准不可变发布计划后，系统会读取服务端批次预览。";
    } else if (oneClickExecution.previewBusy) {
      message.textContent = "正在只读核对本批次店铺、依赖和执行能力…";
    } else if (oneClickExecution.posting) {
      message.textContent = "正在创建唯一持久任务；请勿重复点击。";
    } else if (oneClickExecution.error) {
      message.textContent = oneClickExecution.postAttempted
        ? `${oneClickExecution.error} 请求结果未确认，系统不会自动重发。`
        : `${oneClickExecution.error} 未发送任何发布请求。`;
    } else if (oneClickExecution.statusWarning) {
      message.textContent = oneClickExecution.statusWarning;
    } else if (oneClickExecution.job) {
      const phaseLabels = {
        PENDING: "持久任务已建立，等待准备。",
        PREPARING: "服务端正在准备本批次。",
        READY: "本批次已就绪，等待后台执行。",
        RUNNING: "后台正在逐店执行并记录回执。",
        SUCCEEDED: "所有可执行店铺已完成。",
        WAITING_MANUAL_ACCEPTANCE: "自动执行已终止，存在等待人工验收的店铺。",
        BLOCKED: "自动执行已终止，请按唯一下一步解除阻断。",
        SYSTEMIC_STOPPED: "批次身份异常，系统已安全停止且不会重试。",
      };
      message.textContent = phaseLabels[oneClickExecution.job.phase]
        || "任务状态已由服务端更新。";
    } else if (oneClickExecution.preview) {
      const count = Number(oneClickExecution.preview.runnable_target_count || 0);
      const manual = (oneClickExecution.preview.summary?.manual_after_submit || []).length;
      message.textContent =
        `服务端预览完成：${count} 个店铺可执行，${manual} 个提交后等待人工验收。`;
    } else {
      message.textContent = "等待服务端只读批次预览。";
    }
    if (identity) {
      if (oneClickExecution.job) {
        $("#publishAllNote").textContent = ONECLICK_TERMINAL_PHASES.has(
          oneClickExecution.job.phase,
        )
          ? "本计划已有终态持久任务；请按下方唯一下一步处理，不能再次发布。"
          : "持久任务正在运行；页面只读轮询状态，不会再次提交。";
      } else if (oneClickExecution.postAttempted) {
        $("#publishAllNote").textContent =
          "发布请求已尝试；确认服务端持久任务前禁止再次提交。";
      } else if (oneClickExecution.error) {
        $("#publishAllNote").textContent =
          "服务端批次预览未通过；没有发送发布请求，请按唯一下一步处理。";
      } else if (oneClickExecution.previewBusy || !oneClickExecution.preview) {
        $("#publishAllNote").textContent =
          "正在只读核对本批次；预览完成前不会发送发布请求。";
      } else if (oneClickExecution.preview.dispatch_capability?.enabled === false) {
        $("#publishAllNote").textContent =
          "统一发布执行能力当前关闭；所有店铺保持原状态。";
      } else {
        const count = Number(oneClickExecution.preview.runnable_target_count || 0);
        $("#publishAllNote").textContent = count > 0
          ? `本次只提交服务端标记为可执行的 ${count} 个店铺；其他店铺保持原状态。`
          : "当前没有可执行店铺；请按服务端唯一下一步处理。";
      }
    }
    $("#oneClickExecutionPreview").setAttribute(
      "aria-busy",
      String(Boolean(
        oneClickExecution.previewBusy
        || oneClickExecution.posting
        || oneClickExecution.statusBusy
      )),
    );
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
        || document.querySelector(`.run-target[data-target-label="${escaped}"]`);
    }
    target ||= $("#oneClickExecutionPreview");
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.focus({ preventScroll: true });
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
      const response = await fetch(
        `/api/product-workspace/publish-status?${params}`,
        { headers: { Accept: "application/json" }, signal: controller.signal },
      );
      const payload = await response.json().catch(() => ({
        ok: false,
        error: `服务返回 HTTP ${response.status}`,
      }));
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
      const response = await fetch(
        `/api/product-workspace/publish-preview?${params}`,
        { headers: { Accept: "application/json" }, signal: controller.signal },
      );
      const payload = await response.json().catch(() => ({
        ok: false,
        error: `服务返回 HTTP ${response.status}`,
      }));
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
    const container = document.getElementById(action.control_id || "");
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
    if (!panel || !container) return;
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
  }

  async function runReleaseRecovery(actionCode) {
    if (!currentData || pageLoading || releaseSubmitting || approvalSubmitting) return;
    const code = String(actionCode || "");
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

  function updateReleaseControls(data) {
    const release = data?.release_v1 || {};
    const plan = release.plan || {};
    const approved = Boolean(release.plan_approved);
    const eligible = Boolean(release.eligible_for_plan_approval && plan.plan_id);
    const busy = Boolean(
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
    releasePlanCheckbox.disabled = approved || !eligible || busy;
    if (!releasePlanCheckbox.disabled) {
      delete releasePlanCheckbox.dataset.disabledReason;
    } else if (approved) {
      releasePlanCheckbox.dataset.disabledReason =
        "当前 ReleasePlan 已批准，无需重复勾选。";
    } else if (busy) {
      releasePlanCheckbox.dataset.disabledReason =
        "正在完成当前读取或本地状态更新；结束后会自动重新计算审批入口。";
    } else {
      releasePlanCheckbox.dataset.disabledReason = translateBlocker(
        (release.blockers || [])[0]
        || "当前发布计划尚未满足批准条件。",
      );
    }
    $("#approveReleasePlanButton").disabled = Boolean(
      approved || !eligible || !releasePlanCheckbox.checked || busy,
    );

    const prepared = Boolean(release.miaoshou_prepared);
    const commonReadbackOnly = commonNeedsReadbackReconciliation(release);
    $("#prepareMiaoshouCheckbox").disabled = !approved || prepared || busy;
    $("#prepareMiaoshouButton").disabled = Boolean(
      !approved || prepared || !$("#prepareMiaoshouCheckbox").checked || busy,
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
    $("#commonOverwriteCheckbox").disabled = !overwriteReady || busy;
    $("#commonOverwriteButton").disabled = Boolean(
      !overwriteReady || !$("#commonOverwriteCheckbox").checked || busy,
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
      ? Number(oneClickExecution.preview?.runnable_target_count || 0)
      : (
        Number.isInteger(release.runnable_target_count)
          ? release.runnable_target_count
          : runGroups.pending.length
      );
    const ledgerBlocksPublish = Boolean(
      runGroups.running.length || oneClickJobExists,
    );
    const publishAllCheckbox = $("#publishAllCheckbox");
    if (busy) {
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
      || runnableTargetCount < 1
      || ledgerBlocksPublish
      || busy
    );
    $("#publishAllButton").disabled = Boolean(
      !publishReady
      || runnableTargetCount < 1
      || ledgerBlocksPublish
      || !$("#publishAllCheckbox").checked
      || busy,
    );
    document.querySelectorAll("[data-price-repair-action]").forEach((button) => {
      const panel = button.closest("[data-price-repair-target]");
      const targetLabel = panel?.dataset.priceRepairTarget || "";
      const repairState = shopeePriceRepairState(targetLabel);
      const confirmed = Boolean(
        panel?.querySelector("[data-price-repair-confirm]")?.checked,
      );
      button.disabled = Boolean(
        busy
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
    ensureOneClickExecution(data);
    renderOneClickExecution(data);
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
    if (!currentData || releaseSubmitting || !$("#releasePlanCheckbox").checked) return;
    releaseSubmitting = true;
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
      releaseSubmitting = false;
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

  async function publishSelectedTargets() {
    const identity = oneClickExecution.identity;
    const preview = oneClickExecution.preview;
    if (
      !currentData
      || releaseSubmitting
      || oneClickExecution.posting
      || oneClickExecution.postAttempted
      || oneClickExecution.job
      || !identity
      || !preview
      || preview.runnable_target_count < 1
      || preview.dispatch_capability?.enabled === false
      || !$("#publishAllCheckbox").checked
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
      const payload = await postReleaseAction(
        "/api/product-workspace/publish",
        body,
        { expectedStatus: 202 },
      );
      if (
        payload.accepted !== true
        || !Array.isArray(payload.external_writes_performed)
        || payload.external_writes_performed.length
      ) {
        throw new Error("统一发布任务未返回安全的接受回执。");
      }
      const job = validateOneClickProjection(
        payload.job,
        identity,
        ONECLICK_STATUS_SCHEMA,
        preview,
      );
      if (generation !== oneClickExecution.generation) return;
      oneClickExecution.job = job;
      oneClickExecution.error = "";
      $("#publishRunMessage").textContent =
        "持久任务已接受；正在只读轮询服务端状态，不会再次提交。";
      showError("");
      scheduleOneClickStatusPoll(generation, 0);
    } catch (error) {
      if (generation !== oneClickExecution.generation) return;
      const message = friendlyError(error.message);
      oneClickExecution.error = message;
      showError(message);
      $("#publishRunMessage").textContent =
        `${message} 请求结果未确认，系统不会自动重发。`;
    } finally {
      if (generation === oneClickExecution.generation) {
        releaseSubmitting = false;
        oneClickExecution.posting = false;
        renderOneClickExecution(currentData);
        updateReleaseControls(currentData || {});
      }
    }
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

    const offer = data.product?.offer_id || $("#offerId").value.trim();
    const studioUrl = `/ai-image-studio?offer_id=${encodeURIComponent(offer)}`;
    $("#workbenchLink").href = studioUrl;
    $("#studioNavLink").href = studioUrl;
    $("#workbenchLink").removeAttribute("aria-disabled");
  }

  function clearCurrentApprovalContext() {
    resetOneClickExecution();
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
    if (approvalSubmitting || releaseSubmitting) return;
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
  $("#releasePlanCheckbox").addEventListener("change", () => {
    updateReleaseControls(currentData || {});
  });
  $("#releasePlanApprovalForm").addEventListener("submit", (event) => {
    event.preventDefault();
    approveReleasePlan();
  });
  $("#releasePlanRecoveryActions").addEventListener("click", (event) => {
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
  $("#publishAllCheckbox").addEventListener("change", () => {
    updateReleaseControls(currentData || {});
  });
  $("#publishAllButton").addEventListener("click", publishSelectedTargets);
  $("#oneClickNextActionButton").addEventListener("click", (event) => {
    focusOneClickTarget(event.currentTarget.dataset.oneclickTargetFocus || "");
  });
  $("#oneClickExecutionGroups").addEventListener("submit", (event) => {
    const form = event.target.closest(".oneclick-observation-review-form");
    if (!form) return;
    event.preventDefault();
    submitOneClickObservationAcceptance(form);
  });
  $("#oneClickExecutionGroups").addEventListener("click", (event) => {
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
