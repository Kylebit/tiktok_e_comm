(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
  const flowApi = (suffix) => `/api/product-flow/${String(suffix).replace(/^\/+/, "")}`;
  const proxyImage = (url) => `/api/proxy-image?url=${encodeURIComponent(url || "")}`;
  const localImage = (offerId, artifactId) => (
    `${flowApi("content-image")}?offer_id=${encodeURIComponent(offerId)}&artifact_id=${encodeURIComponent(artifactId)}`
  );

  let preview = null;
  let finalOrder = [];
  let pollTimer = null;
  let toastTimer = null;
  let contentStrategyDraft = null;
  let recipeDraft = null;
  let recipeDraftOfferId = "";
  let recipeDraftDirty = false;
  let recipeDraftBaseline = "";
  let syncPollTimer = null;
  let syncInFlight = false;
  let syncFeedbackOverride = null;
  let planningProgressOverride = null;
  let generationProgressOverride = null;
  let sourceOnlyDraft = null;
  let sourceOnlyDraftOfferId = "";
  let sourceOnlyDraftDirty = false;
  let sourceOnlySaveFeedback = "";
  let sourceReviewSubmitting = false;
  let storyboardDraft = {};
  let storyboardDraftOfferId = "";
  let storyboardDraftDirty = false;
  let imageLocalizationDraft = {};
  let imageLocalizationDraftOfferId = "";
  let localizedImageProject = null;
  const RECIPE_LIMITS = Object.freeze({
    scene: 6,
    selling_point: 6,
    size_card: 1,
  });

  function showAlert(message) {
    $("#alert").textContent = message || "";
    $("#alert").hidden = !message;
  }

  function toast(message) {
    clearTimeout(toastTimer);
    $("#toast").textContent = message || "";
    $("#toast").hidden = !message;
    if (message) toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 6000);
  }

  function setLoading(element, loading) {
    if (!element) return;
    element.classList.toggle("is-loading", loading);
    if ("disabled" in element) element.disabled = loading;
    if (loading) element.setAttribute("aria-busy", "true");
    else element.removeAttribute("aria-busy");
  }

  function setButtonLabel(element, text) {
    if (!element) return;
    const label = element.querySelector(".button-label");
    if (label) label.textContent = text;
    else element.textContent = text;
  }

  function nextPaint() {
    return new Promise((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(resolve));
    });
  }

  const PLANNING_STEPS = Object.freeze([
    "保存本次配方",
    "AI 规划分镜",
    "自动采用经验配方",
    "生成前检查",
  ]);

  function renderPlanningProgress(override) {
    if (override !== undefined) {
      planningProgressOverride = override
        ? { ...override, offer_id: currentOfferId() }
        : null;
    }
    const host = $("#planningProgress");
    if (!host) return;
    const content = preview?.content_package || {};
    const preflightReady = (
      content.remaining_images_preflight?.status
      === "ready_for_explicit_paid_confirmation"
    );
    const proposalValid = Boolean(content.model_proposal?.valid);
    const proposalAutoAdopted = Boolean(
      proposalValid
      && content.suite_approved
      && content.planning_review_mode === "experience_recipe_auto_v1"
    );
    const feedback = (
      planningProgressOverride?.offer_id === currentOfferId()
        ? planningProgressOverride
        : null
    ) || (
      preflightReady
        ? {
          status: "complete",
          completedThrough: 3,
          title: "AI 分镜与生成前检查已完成",
          message: "尚未创建任何付费图片任务；等待 Kyle 确认付费。",
          badge: "等待付费确认",
        }
        : proposalAutoAdopted
          ? {
            status: "waiting",
            completedThrough: 2,
            step: 3,
            title: "AI 分镜已规划并自动采用",
            message: "经验配方已采用；下一步是无付费的生成前检查。",
            badge: "待检查",
          }
          : proposalValid
            ? {
              status: "waiting",
              completedThrough: 1,
              step: 2,
              title: "已有有效 AI 分镜",
              message: "这是升级前的旧状态；保存当前配方时会无费用迁移并自动采用，不会再次调用 AI。",
              badge: "待迁移",
            }
          : {
            status: "idle",
            completedThrough: -1,
            step: 0,
            title: "准备 AI 分镜",
            message: "确认商品事实、身份参考与图片配方后开始。",
            badge: "未开始",
          }
    );
    if (sourceOnlyActive() && !planningProgressOverride) {
      host.hidden = true;
      return;
    }
    const status = feedback.status || "idle";
    const completedThrough = Number(feedback.completedThrough ?? -1);
    const currentStep = Number(feedback.step ?? Math.min(completedThrough + 1, 3));
    const complete = status === "complete";
    const failed = status === "failed";
    const running = status === "running";
    const finishedCount = complete ? PLANNING_STEPS.length : Math.max(0, completedThrough + 1);
    const partial = running ? 0.45 : 0;
    const progress = complete
      ? 100
      : Math.min(100, Math.round(((finishedCount + partial) / PLANNING_STEPS.length) * 100));
    const tone = failed ? "danger" : (complete ? "safe" : (running || status === "waiting" ? "warn" : "neutral"));

    host.hidden = false;
    host.classList.toggle("running", running);
    host.classList.toggle("failed", failed);
    $("#planningProgressTitle").textContent = feedback.title || "AI 分镜进度";
    $("#planningProgressMessage").textContent = failed
      ? `失败原因：${feedback.error || feedback.message || "未知错误"}`
      : (feedback.message || "正在处理。");
    $("#planningProgressBadge").textContent = feedback.badge || (
      failed ? "失败" : (complete ? "已完成" : (running ? "进行中" : "等待"))
    );
    $("#planningProgressBadge").className = `badge ${tone}`;
    const action = $("#planningProgressAction");
    const actionType = String(feedback.action || "");
    const actionLabels = {
      "identity-reference": "前往选择身份参考图",
      "prepare-package": "先创建本地内容审核包",
    };
    action.hidden = !actionLabels[actionType];
    action.textContent = actionLabels[actionType] || "";
    action.dataset.action = actionLabels[actionType] ? actionType : "";
    $("#planningProgressBar").value = progress;
    $("#planningProgressSteps").innerHTML = PLANNING_STEPS.map((label, index) => {
      const state = complete || index <= completedThrough
        ? "done"
        : (index === currentStep && (running || failed || status === "waiting") ? "current" : "");
      return `<li class="${state}">${esc(label)}</li>`;
    }).join("");
  }

  function renderGenerationProgress(override) {
    if (override !== undefined) {
      generationProgressOverride = override
        ? { ...override, offer_id: currentOfferId() }
        : null;
    }
    const host = $("#generationProgress");
    if (!host) return;
    const content = preview?.content_package || {};
    const generation = content.remaining_images_generation || {};
    const preflight = content.remaining_images_preflight || {};
    const persistedStatus = String(generation.status || "");
    const persistedDisplayStatus = (
      preflight.status === "ready_for_explicit_paid_confirmation"
      && (!persistedStatus || persistedStatus === "not_started")
    )
      ? "preflight-ready"
      : (persistedStatus || "not_started");
    const feedback = (
      generationProgressOverride?.offer_id === currentOfferId()
        ? generationProgressOverride
        : null
    );
    const status = String(feedback?.status || (
      sourceOnlyActive()
        ? "skipped"
        : persistedDisplayStatus
    ));
    const items = Array.isArray(generation.items) ? generation.items : [];
    const total = Number(preflight.total || preflight.shots?.length || items.length || 0);
    const completedCount = items.filter(
      (item) => ["completed", "completed_waiting_human_review"].includes(item.status),
    ).length;
    const failedCount = items.filter((item) => item.status === "failed").length;
    const settledCount = completedCount + failedCount;
    const progress = feedback?.progress ?? (
      ["completed_waiting_human_review", "completed_with_errors", "completed"].includes(status)
        ? 100
        : (status === "running" && total
          ? Math.max(8, Math.round((settledCount / total) * 100))
          : (["queued", "submitting"].includes(status) ? 6 : (status === "preflighting" ? 55 : 0)))
    );
    const currentShot = String(generation.current_shot_id || "").trim();
    const states = {
      skipped: ["本次只使用来源图", "AI 生图已禁用。", "已跳过", "neutral", 0],
      not_started: ["尚未开始生成", "先完成 AI 分镜与生成前检查。", "未开始", "neutral", 0],
      preflighting: ["正在执行生成前检查", "只验证配方、参考图与任务参数，不会创建付费任务。", "检查中", "warn", 0],
      "preflight-ready": ["生成前检查已完成", `本次已准备 ${total} 张；等待 Kyle 确认付费。`, "等待付费确认", "warn", 1],
      submitting: ["正在提交付费生成确认", "正在建立任务队列；请勿重复点击。", "提交中", "warn", 1],
      queued: ["图片生成任务已排队", `${total || items.length} 张等待生成，页面会自动刷新。`, "排队中", "warn", 2],
      running: [
        "图片生成进行中",
        `${settledCount}/${total || items.length} 张已返回${currentShot ? `；当前 ${currentShot}` : ""}。`,
        "生成中",
        "warn",
        2,
      ],
      completed: ["图片生成完成", `${completedCount || total} 张已返回，等待逐图审核。`, "待审核", "safe", 3],
      completed_waiting_human_review: [
        "图片生成完成",
        `${completedCount || total} 张通过本地技术核验；等待逐图人工审核。`,
        "待审核",
        "safe",
        3,
      ],
      completed_with_errors: [
        "图片生成结束但存在失败项",
        `${completedCount} 张可审核，${failedCount || "部分"} 张失败；请查看每个版本的原因。`,
        "需处理",
        "danger",
        3,
      ],
      failed: [
        "图片生成失败",
        `失败原因：${feedback?.error || generation.error || "任务未产生可验证图片。"}`,
        "失败",
        "danger",
        2,
      ],
      error: [
        "生成操作失败",
        `失败原因：${feedback?.error || generation.error || "未知错误"}`,
        "失败",
        "danger",
        Number(feedback?.step ?? 0),
      ],
    };
    const [title, message, badge, tone, activeStep] = states[status] || [
      "图片生成状态",
      `当前状态：${status || "未知"}`,
      status || "未知",
      "neutral",
      0,
    ];
    const stepLabels = ["生成前检查", "付费确认", "任务队列", "成图审核"];
    const running = ["preflighting", "submitting", "queued", "running"].includes(status);
    const failed = ["failed", "error", "completed_with_errors"].includes(status);
    host.classList.toggle("running", running);
    host.classList.toggle("failed", failed);
    host.innerHTML = `
      <header>
        <div><strong>${esc(title)}</strong><span>${esc(feedback?.message || message)}</span></div>
        <span class="badge ${esc(tone)}">${esc(feedback?.badge || badge)}</span>
      </header>
      <progress class="progress-track" max="100" value="${Number(progress) || 0}" aria-label="图片生成进度"></progress>
      <ol>${stepLabels.map((label, index) => {
        const done = (
          ["completed", "completed_waiting_human_review"].includes(status)
          || (status === "completed_with_errors" && index < 3)
          || index < activeStep
        );
        const current = index === activeStep && status !== "not_started" && status !== "skipped";
        return `<li class="${done ? "done" : (current ? "current" : "")}">${esc(label)}</li>`;
      }).join("")}</ol>
    `;
  }

  async function requestJson(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    const data = await response.json().catch(() => ({
      ok: false,
      error: `服务返回 HTTP ${response.status}`,
    }));
    if (!response.ok || data.ok === false) {
      const error = new Error(data.error || `服务返回 HTTP ${response.status}`);
      error.payload = data;
      throw error;
    }
    return data;
  }

  function post(suffix, payload) {
    return requestJson(flowApi(suffix), {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  function currentOfferId() {
    return String(preview?.offer_id || $("#offerId").value || "").trim();
  }

  function currentContentStrategy() {
    return contentStrategyDraft
      || preview?.content_package?.content_strategy
      || "ai_assisted";
  }

  function sourceOnlyActive() {
    return currentContentStrategy() === "source_only";
  }

  function sourceOnlyFinalApproved() {
    return Boolean(
      sourceOnlyActive()
      && !sourceOnlyDraftDirty
      && preview?.content_package?.source_only_final_approved,
    );
  }

  function activeReview() {
    if (
      sourceOnlyActive()
      && sourceOnlyDraft
      && sourceOnlyDraftOfferId === currentOfferId()
    ) {
      return sourceOnlyDraft;
    }
    return preview?.review || {};
  }

  function clearSourceOnlyDraft() {
    sourceOnlyDraft = null;
    sourceOnlyDraftOfferId = "";
    sourceOnlyDraftDirty = false;
    sourceOnlySaveFeedback = "";
  }

  function sourceAction(row) {
    return row?.action === "remove" ? "remove" : "keep";
  }

  function sourceRowsFromDom() {
    return (activeReview().image_actions || []).map((row, index) => ({
      ...row,
      action: $(`.source-action[data-index="${index}"]`)?.value || sourceAction(row),
      note: $(`.source-note[data-index="${index}"]`)?.value || "",
    }));
  }

  function selectedIdentityReferences() {
    return $$(".identity-reference:checked").map((node) => node.dataset.url);
  }

  function focusIdentityReferenceArea() {
    const sourceArea = $("#sources");
    const firstReference = $(".identity-reference");
    if (sourceArea) sourceArea.scrollIntoView({ behavior: "smooth", block: "center" });
    if (firstReference) {
      firstReference.focus({ preventScroll: true });
    } else if (sourceArea) {
      sourceArea.setAttribute("tabindex", "-1");
      sourceArea.focus({ preventScroll: true });
    }
  }

  async function runPlanningProgressAction() {
    const action = $("#planningProgressAction")?.dataset.action || "";
    if (action === "prepare-package") {
      await preparePackage();
      return;
    }
    if (action === "identity-reference") focusIdentityReferenceArea();
  }

  function reportPlanningBlocker({ title, message, action = "" }) {
    renderPlanningProgress({
      status: "failed",
      step: 0,
      completedThrough: -1,
      title,
      message,
      badge: "需要处理",
      action,
      blocker: action || message,
    });
    if (action === "identity-reference") focusIdentityReferenceArea();
  }

  function planningErrorMessage(error) {
    const raw = String(error?.message || "");
    if (
      raw.includes("ToAPIs chat network error")
      && (
        raw.includes("UNEXPECTED_EOF_WHILE_READING")
        || raw.includes("EOF occurred in violation of protocol")
      )
    ) {
      return (
        "AI 分镜请求经过本机网络代理时连接被提前断开。"
        + "本次配方和身份参考已经保存在本地，但没有收到分镜结果、没有自动采用，也没有创建图片生成任务。"
        + "为避免重复产生模型费用，系统不会自动重试；确认网络恢复后可由你手动重新规划一次。"
      );
    }
    if (raw.includes("create a local content review package")) {
      return "还没有创建本地内容审核包。请先点击“读取妙手并创建本地包”，再重新确认商品事实和本次配方；本次没有调用 AI，也没有产生生图费用。";
    }
    if (raw.includes("save at least one approved identity reference")) {
      return "请先在来源图区保留并保存至少一张身份参考图，再请求 AI 分镜。";
    }
    return raw;
  }

  function clearPlanningBlocker(blocker) {
    if (planningProgressOverride?.blocker !== blocker) return;
    renderPlanningProgress(null);
  }

  function selectedPrimaryReference() {
    return $(".identity-primary:checked")?.dataset.url || "";
  }

  function reviewPayload(overrides = {}) {
    const videoUrl = String(preview?.source?.video?.url || "").trim();
    const review = {
      ...activeReview(),
      image_actions: sourceRowsFromDom(),
      image_order: finalOrder.map((row) => row.url),
      video_action: videoUrl ? ($("#videoAction")?.value || "remove") : "none",
      ...overrides,
    };
    delete review.generated_image_actions;
    delete review.overseas_image_candidates;
    delete review.image_generation_requests;
    return review;
  }

  function captureSourceOnlyDraft() {
    if (!sourceOnlyActive()) return;
    const rows = sourceRowsFromDom();
    const keptUrls = rows
      .filter((row) => row.action === "keep")
      .map((row) => row.output_url || row.url)
      .filter(Boolean);
    const currentOrder = finalOrder.map((row) => row.url);
    const imageOrder = [
      ...currentOrder.filter((url) => keptUrls.includes(url)),
      ...keptUrls.filter((url) => !currentOrder.includes(url)),
    ];
    sourceOnlyDraft = {
      ...activeReview(),
      image_actions: rows,
      image_order: imageOrder,
      video_action: String(preview?.source?.video?.url || "").trim()
        ? ($("#videoAction")?.value || "remove")
        : "none",
    };
    sourceOnlyDraftOfferId = currentOfferId();
    sourceOnlyDraftDirty = true;
    sourceOnlySaveFeedback = "";
  }

  function assetDecisionsFromDom() {
    const result = {};
    $$(".asset-decision").forEach((node) => {
      const artifactId = node.dataset.artifactId;
      result[artifactId] = {
        decision: node.value,
        note: $(`.asset-note[data-artifact-id="${artifactId}"]`)?.value || "",
      };
    });
    return result;
  }

  function deriveRecipeCounts(content = preview?.content_package || {}) {
    const savedCounts = content.suite_customization?.type_counts;
    const hasSavedCounts = savedCounts && typeof savedCounts === "object"
      && Object.keys(RECIPE_LIMITS).some((type) => Object.hasOwn(savedCounts, type));
    const derivedCounts = Object.fromEntries(Object.keys(RECIPE_LIMITS).map((type) => [type, 0]));
    (content.suite?.items || [])
      .filter((item) => item && item.selected !== false && Object.hasOwn(RECIPE_LIMITS, item.type))
      .forEach((item) => { derivedCounts[item.type] += 1; });
    const source = hasSavedCounts ? savedCounts : derivedCounts;
    return Object.fromEntries(Object.entries(RECIPE_LIMITS).map(([type, max]) => {
      const parsed = Number.parseInt(source[type] ?? 0, 10);
      return [type, Math.max(0, Math.min(Number.isFinite(parsed) ? parsed : 0, max))];
    }));
  }

  function collectRecipeFromDom() {
    const typeCounts = Object.fromEntries(Object.entries(RECIPE_LIMITS).map(([type]) => {
      const node = $(`.recipe-count[data-recipe-type="${type}"]`);
      return [type, Number.parseInt(node?.value ?? "0", 10)];
    }));
    return {
      type_counts: typeCounts,
      size_card: {
        enabled: typeCounts.size_card > 0,
        dimensions: $("#sizeDimensions")?.value.trim() || "",
        confirmed: Boolean($("#sizeConfirmed")?.checked),
      },
    };
  }

  function recipeFromContent(content = preview?.content_package || {}) {
    const sizeCard = content.suite_customization?.size_card || {};
    const typeCounts = deriveRecipeCounts(content);
    return {
      type_counts: typeCounts,
      size_card: {
        enabled: typeCounts.size_card > 0,
        dimensions: String(sizeCard.dimensions || ""),
        confirmed: Boolean(sizeCard.confirmed),
      },
    };
  }

  function visibleRecipe(content = preview?.content_package || {}) {
    if (
      recipeDraftDirty
      && recipeDraft
      && recipeDraftOfferId === currentOfferId()
    ) {
      return recipeDraft;
    }
    return recipeFromContent(content);
  }

  function captureRecipeDraft() {
    if (!recipeDraftDirty) {
      recipeDraftBaseline = JSON.stringify(recipeFromContent());
    }
    recipeDraft = collectRecipeFromDom();
    recipeDraftOfferId = currentOfferId();
    recipeDraftDirty = true;
  }

  function clearRecipeDraft() {
    recipeDraft = null;
    recipeDraftOfferId = "";
    recipeDraftDirty = false;
    recipeDraftBaseline = "";
  }

  function validateRecipe(recipe = collectRecipeFromDom(), { display = true } = {}) {
    if (sourceOnlyActive()) {
      if (display) {
        $("#recipeValidation").textContent = "仅来源图策略不需要生成配方。";
        $("#recipeValidation").classList.remove("error");
      }
      return { valid: true, message: "", total: 0 };
    }
    let message = "";
    for (const [type, max] of Object.entries(RECIPE_LIMITS)) {
      const value = recipe.type_counts[type];
      if (!Number.isInteger(value) || value < 0 || value > max) {
        message = `${type} 数量必须是 0–${max} 的整数。`;
        break;
      }
    }
    const total = Object.values(recipe.type_counts).reduce((sum, value) => (
      sum + (Number.isInteger(value) ? value : 0)
    ), 0);
    if (!message && total < 1) message = "请至少选择一张场景图、卖点图或尺寸图。";
    if (!message && recipe.type_counts.size_card > 0
      && (!recipe.size_card.dimensions || !recipe.size_card.confirmed)) {
      message = "尺寸图需要填写来源已确认的尺寸，并勾选人工确认。";
    }
    if (display) {
      $("#recipeValidation").textContent = message || `配方有效：本次共规划 ${total} 张图片。`;
      $("#recipeValidation").classList.toggle("error", Boolean(message));
    }
    return { valid: !message, message, total };
  }

  function updateRecipeUi({ validate = false } = {}) {
    const recipe = collectRecipeFromDom();
    const result = validate
      ? validateRecipe(recipe)
      : {
          total: Object.values(recipe.type_counts).reduce((sum, value) => (
            sum + (Number.isInteger(value) ? value : 0)
          ), 0),
        };
    $("#sizeCardFields").hidden = recipe.type_counts.size_card < 1;
    $("#recipeTotal").textContent = `共 ${result.total} 张`;
    if (!validate) {
      $("#recipeValidation").textContent = result.total
        ? `当前配方：场景 ${recipe.type_counts.scene || 0} · 卖点 ${recipe.type_counts.selling_point || 0} · 尺寸 ${recipe.type_counts.size_card || 0}`
        : "尚未选择图片类型。";
      $("#recipeValidation").classList.toggle("error", result.total < 1);
    }
    return result;
  }

  function contentReviewPayload() {
    const refs = selectedIdentityReferences();
    const videoUrl = String(preview?.source?.video?.url || "").trim();
    const generationBasisConfirmed = $("#generationBasisConfirmed").checked;
    return {
      content_strategy: currentContentStrategy(),
      fact_card_approved: generationBasisConfirmed,
      planning_scope_approved: generationBasisConfirmed,
      identity_reference_urls: refs,
      primary_identity_url: refs.includes(selectedPrimaryReference())
        ? selectedPrimaryReference()
        : (refs[0] || ""),
      video_action: videoUrl ? ($("#videoAction")?.value || "remove") : "none",
      video_url: videoUrl,
      suite_customization: collectRecipeFromDom(),
      asset_decisions: assetDecisionsFromDom(),
    };
  }

  function generatedCurrentRows() {
    return preview?.review?.generated_image_actions || [];
  }

  function buildFinalItems() {
    const review = activeReview();
    const sourceItems = (review.image_actions || [])
      .filter((row) => sourceAction(row) === "keep")
      .map((row, index) => ({
        url: row.output_url || row.url,
        label: row.kind === "detail" ? "来源详情图" : "来源主图",
        id: `source:${index + 1}`,
        type: "source",
      }))
      .filter((row) => row.url);
    const generatedItems = (sourceOnlyActive() ? [] : generatedCurrentRows())
      .filter((row) => row.miaoshou_action === "keep")
      .map((row) => ({
        url: row.url,
        label: `AI 图片 · ${row.shot_id || "生成"}`,
        id: row.artifact_id,
        type: "generated",
      }))
      .filter((row) => row.url);
    const byUrl = new Map([...sourceItems, ...generatedItems].map((row) => [row.url, row]));
    const requested = review.image_order || [];
    return [
      ...requested.map((url) => byUrl.get(url)).filter(Boolean),
      ...[...byUrl.values()].filter((row) => !requested.includes(row.url)),
    ].filter((row, index, rows) => rows.findIndex((candidate) => candidate.url === row.url) === index);
  }

  function statusTone(status) {
    if (["done", "approved", "completed_waiting_human_review"].includes(status)) return "done";
    if (["current", "attention", "running", "queued"].includes(status)) return "current";
    return "pending";
  }

  function renderProject() {
    const workflow = preview?.workflow || {};
    const review = activeReview();
    const content = preview?.content_package || {};
    const source = preview?.source || {};
    const generated = content.generated_review_images || [];
    const sourceReviewed = (review.image_actions || []).filter(
      (row) => ["keep", "remove"].includes(sourceAction(row)),
    ).length;
    const sourceTotal = (review.image_actions || []).length;
    const completedParts = [
      Boolean(content.fact_card_approved),
      Boolean(content.planning_scope_approved),
      sourceOnlyActive() || Boolean(content.suite_approved),
      sourceOnlyActive() || (generated.length > 0 && generated.every((row) => ["keep", "remove"].includes(row.miaoshou_action))),
      sourceTotal > 0 && sourceReviewed === sourceTotal,
    ].filter(Boolean).length;
    $("#projectTitle").textContent = review.title || source.title_source || `Offer ${preview.offer_id}`;
    $("#projectMeta").textContent = `Offer ${preview.offer_id} · ${source.skus?.length || 0} 个规格 · ${sourceTotal} 张来源图`;
    const precollectRecord = (source.precollect?.records || []).find(
      (row) => row && (row.common_collect_id || row.url),
    ) || {};
    const collectBoxId = String(
      content.collect_box_id || precollectRecord.common_collect_id || "",
    ).trim();
    let sourceUrl = String(precollectRecord.url || source.source_url || "").trim();
    try {
      const parsed = new URL(sourceUrl);
      if (parsed.protocol === "http:" && /(^|\.)1688\.com$/i.test(parsed.hostname)) {
        parsed.protocol = "https:";
        sourceUrl = parsed.href;
      }
    } catch (_error) {
      sourceUrl = "";
    }
    const sourceLinkAllowed = /^https:\/\/[^/]*1688\.com(?:\/|$)/i.test(sourceUrl);
    const sourceOfferId = sourceUrl.match(/\/offer\/(\d+)/i)?.[1] || "";
    $("#projectSourceLinks").innerHTML = [
      collectBoxId
        ? `<a id="miaoshouCollectLink" href="https://erp.91miaoshou.com/" target="_blank" rel="noopener" title="打开妙手后按采集箱 ID 定位">妙手采集箱 · ${esc(collectBoxId)} ↗</a>`
        : '<span class="source-link-missing">妙手采集箱 · 未关联</span>',
      sourceLinkAllowed
        ? `<a id="source1688Link" href="${esc(sourceUrl)}" target="_blank" rel="noopener">1688 商品 · ${esc(sourceOfferId || source.source_item_code || "打开来源")} ↗</a>`
        : '<span class="source-link-missing">1688 来源 · 未关联</span>',
    ].join("");
    $("#completionScore").textContent = `${completedParts}/5`;
    $("#currentStage").textContent = workflow.current_label || content.stage || "等待审核";
    $("#productCenterLink").href = `/product-workspace?offer_id=${encodeURIComponent(preview.offer_id)}`;

    const sourceOnlySaved = (
      !sourceOnlyDraftDirty
      && finalOrder.length > 0
      && (review.image_order || []).length === finalOrder.length
    );
    const baseSteps = sourceOnlyActive()
      ? [
        ["选择来源图", sourceTotal > 0 && sourceReviewed === sourceTotal ? "done" : "current"],
        ["排序并保存", sourceOnlySaved ? "done" : "current"],
        ["最终内容批准", sourceOnlyFinalApproved() ? "done" : "pending"],
      ]
      : [
        ["来源审核", sourceTotal > 0 && sourceReviewed === sourceTotal ? "done" : "current"],
        ["经验配方", content.fact_card_approved && content.planning_scope_approved && content.suite_approved ? "done" : "pending"],
        ["生成版本", workflow.generation_ready ? "done" : (content.remaining_images_generation?.status || "pending")],
        ["最终排序", workflow.image_review_ready && finalOrder.length ? "done" : "pending"],
      ];
    const firstOpen = baseSteps.findIndex((row) => row[1] !== "done");
    $("#flowRail").classList.toggle("source-only-flow", sourceOnlyActive());
    $("#flowRail").innerHTML = baseSteps.map(([label, rawStatus], index) => {
      const tone = rawStatus === "done" ? "done" : (index === firstOpen ? "current" : statusTone(rawStatus));
      const note = tone === "done" ? "已完成" : (tone === "current" ? "当前处理" : "等待前序");
      return `<div class="flow-step ${tone}"><span>${tone === "done" ? "✓" : `0${index + 1}`}</span><strong>${esc(label)}</strong><small>${note}</small></div>`;
    }).join("");
  }

  function renderSourceStats() {
    const rows = activeReview().image_actions || [];
    const sourceSnapshot = preview?.content_package?.source_snapshot || {};
    const removedUrls = new Set(
      rows
        .filter((row) => sourceAction(row) === "remove")
        .map((row) => row.output_url || row.url)
        .filter(Boolean),
    );
    const refs = new Set(
      (sourceSnapshot.identity_reference_urls || []).filter(
        (url) => !removedUrls.has(url),
      ),
    );
    const kept = rows.filter((row) => sourceAction(row) === "keep").length;
    const removed = rows.filter((row) => sourceAction(row) === "remove").length;
    const pending = rows.length - kept - removed;
    $("#sourceStats").innerHTML = [
      `全部 ${rows.length}`,
      `保留 ${kept}`,
      `移除 ${removed}`,
      `待决定 ${pending}`,
      `身份参考 ${refs.size}`,
    ].map((text) => `<span>${text}</span>`).join("");
  }

  function renderSources() {
    const rows = activeReview().image_actions || [];
    const sourceSnapshot = preview?.content_package?.source_snapshot || {};
    const refs = new Set(sourceSnapshot.identity_reference_urls || []);
    const primary = sourceSnapshot.primary_identity_image || "";
    renderSourceStats();
    const grid = $("#sourceGrid");
    grid.classList.remove("skeleton");
    grid.innerHTML = rows.map((row, index) => {
      const url = row.output_url || row.url || "";
      const action = sourceAction(row);
      if (action === "remove") return "";
      return `
        <article class="asset-card ${action === "keep" ? "kept" : (action === "remove" ? "removed" : "")}">
          <div class="asset-image" data-preview-url="${esc(proxyImage(url))}" data-preview-label="来源图 ${index + 1}">
            <img src="${esc(proxyImage(url))}" alt="来源图 ${index + 1}" loading="lazy">
            <span class="asset-index">${String(index + 1).padStart(2, "0")}</span>
            <button type="button" class="source-remove" data-index="${index}"
              aria-label="删除来源图 ${index + 1}" title="删除这张来源图">×</button>
          </div>
          <div class="asset-body">
            <header><strong>${row.kind === "detail" ? "详情图" : "主图"}</strong><small>${esc(action)}</small></header>
            <div class="decision-row">
              <div class="source-decision-summary">
                <span>图片决定</span>
                <strong>默认保留</strong>
                <input class="source-action" data-index="${index}" type="hidden" value="keep">
              </div>
              <div>
                <label class="identity-check"><input class="identity-reference" type="checkbox" data-url="${esc(url)}" ${refs.has(url) ? "checked" : ""}>身份参考</label>
                <label class="identity-check"><input class="identity-primary" name="primaryIdentity" type="radio" data-url="${esc(url)}" ${primary === url ? "checked" : ""}>主参考图</label>
              </div>
            </div>
            <label>审核备注<textarea class="source-note" data-index="${index}" placeholder="说明文字、水印、重复或商品一致性">${esc(row.note || "")}</textarea></label>
          </div>
        </article>
      `;
    }).join("");

    $$(".source-remove").forEach((button) => button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (sourceReviewSubmitting) return;
      const index = Number(button.dataset.index);
      const row = (activeReview().image_actions || [])[index];
      if (!row) return;
      const previousAction = row.action;
      const card = button.closest(".asset-card");
      const actionNode = $(`.source-action[data-index="${index}"]`);
      if (actionNode) actionNode.value = "remove";
      const identityReference = card?.querySelector(".identity-reference");
      const identityPrimary = card?.querySelector(".identity-primary");
      if (identityReference) identityReference.checked = false;
      if (identityPrimary) identityPrimary.checked = false;
      row.action = "remove";
      if (sourceOnlyActive()) {
        captureSourceOnlyDraft();
      }
      finalOrder = buildFinalItems();
      renderSources();
      renderFinal();
      updateStrategyUi();
      renderProject();
      const saved = await saveSourceReview({
        successMessage: `来源图 ${index + 1} 已删除并保存到本地。`,
      });
      if (!saved) {
        row.action = previousAction;
        finalOrder = buildFinalItems();
        renderSources();
        renderFinal();
        updateStrategyUi();
        renderProject();
      }
    }));
    $$(".source-note").forEach((node) => {
      node.addEventListener("input", () => {
        if (!sourceOnlyActive()) return;
        captureSourceOnlyDraft();
        updateStrategyUi();
        renderProject();
      });
    });
    $$(".identity-reference").forEach((node) => node.addEventListener("change", () => {
      if (node.checked) {
        const index = node.closest(".asset-card").querySelector(".source-action").dataset.index;
        $(`.source-action[data-index="${index}"]`).value = "keep";
        node.closest(".asset-card").classList.add("kept");
        node.closest(".asset-card").classList.remove("removed");
        if (sourceOnlyActive()) {
          captureSourceOnlyDraft();
          finalOrder = buildFinalItems();
          renderSourceStats();
          renderFinal();
          updateStrategyUi();
          renderProject();
        }
      } else if ($(".identity-primary:checked")?.dataset.url === node.dataset.url) {
        node.checked = true;
      }
      if (selectedIdentityReferences().length) {
        clearPlanningBlocker("identity-reference");
      }
    }));
    $$(".identity-primary").forEach((node) => node.addEventListener("change", () => {
      if (!node.checked) return;
      const checkbox = $$(".identity-reference").find(
        (candidate) => candidate.dataset.url === node.dataset.url,
      );
      if (checkbox) checkbox.checked = true;
    }));
    const sourceVideo = preview?.source?.video || {};
    const videoUrl = String(sourceVideo.url || "").trim();
    $("#videoReviewPanel").hidden = !videoUrl;
    $("#noSourceVideo").hidden = Boolean(videoUrl);
    if (videoUrl) {
      const savedAction = preview?.review?.video_action || sourceVideo.action || "keep";
      $("#videoAction").value = ["keep", "remove"].includes(savedAction)
        ? savedAction
        : "remove";
      $("#videoAction").onchange = () => {
        if (!sourceOnlyActive()) return;
        captureSourceOnlyDraft();
        renderFinal();
        updateStrategyUi();
        renderProject();
      };
      $("#videoPreviewHost").innerHTML = `
        <video controls preload="none">
          <source src="${esc(videoUrl)}">
        </video>
        <a href="${esc(videoUrl)}" target="_blank" rel="noopener">在新标签页打开来源视频 ↗</a>
      `;
    } else {
      $("#videoPreviewHost").innerHTML = "";
    }
    attachImagePreview();
  }

  function renderStoryboard() {
    const content = preview?.content_package || {};
    const strategy = currentContentStrategy();
    $$('input[name="contentStrategy"]').forEach((node) => {
      node.checked = node.value === strategy;
      node.onchange = () => {
        if (!node.checked) return;
        contentStrategyDraft = node.value;
        if (sourceOnlyActive()) captureSourceOnlyDraft();
        updateStrategyUi();
        renderVersions();
        renderFinal();
        renderProject();
      };
    });
    $("#generationBasisConfirmed").checked = Boolean(
      content.fact_card_approved && content.planning_scope_approved,
    );
    $("#preparePackageButton").hidden = Boolean(content.package_found);
    const items = content.suite?.items || [];
    const visible = visibleRecipe(content);
    const recipeCounts = visible.type_counts;
    const sizeCard = visible.size_card;
    Object.entries(recipeCounts).forEach(([type, value]) => {
      const node = $(`.recipe-count[data-recipe-type="${type}"]`);
      if (node) node.value = String(value);
    });
    $("#sizeDimensions").value = sizeCard.dimensions || "";
    $("#sizeConfirmed").checked = Boolean(sizeCard.confirmed);
    $$(".recipe-count").forEach((node) => {
      node.oninput = () => { captureRecipeDraft(); updateRecipeUi(); };
      node.onchange = () => { captureRecipeDraft(); updateRecipeUi({ validate: true }); };
    });
    $("#sizeDimensions").oninput = () => { captureRecipeDraft(); updateRecipeUi(); };
    $("#sizeDimensions").onchange = () => { captureRecipeDraft(); updateRecipeUi({ validate: true }); };
    $("#sizeConfirmed").onchange = () => { captureRecipeDraft(); updateRecipeUi({ validate: true }); };
    updateRecipeUi();
    updateStrategyUi();
    const activeStoryboardDraft = storyboardDraftOfferId === currentOfferId()
      ? storyboardDraft
      : {};
    $("#storyboardGrid").innerHTML = items.length ? items.map((item) => `
      <article class="story-card">
        <header><h3>${esc(item.title_zh || item.title || item.id)}</h3><span class="experience-badge">经验配方 · 自动采用</span></header>
        <label>标题<input data-story-title="${esc(item.id)}" value="${esc(activeStoryboardDraft[item.id]?.title ?? item.title_zh ?? item.title ?? "")}" maxlength="240"></label>
        <label>构图描述<textarea data-story-focus="${esc(item.id)}" maxlength="1200">${esc(activeStoryboardDraft[item.id]?.focus ?? item.focus_zh ?? item.focus ?? "")}</textarea></label>
      </article>
    `).join("") + '<button id="saveStoryboardEdits" class="button secondary" type="button">保存人工修改</button>' : '<article class="story-card"><p>尚未建立经验配方。先确认商品事实与图片数量，再由 AI 生成本次分镜；分镜无需逐卡审批。</p></article>';
    $$('[data-story-title], [data-story-focus]').forEach((node) => {
      node.addEventListener("input", () => {
        const id = node.dataset.storyTitle || node.dataset.storyFocus || "";
        if (!id) return;
        storyboardDraftOfferId = currentOfferId();
        storyboardDraft[id] = {
          title: document.querySelector(`[data-story-title="${id}"]`)?.value || "",
          focus: document.querySelector(`[data-story-focus="${id}"]`)?.value || "",
        };
        storyboardDraftDirty = true;
      });
    });
  }

  async function saveStoryboardEdits({ quiet = false } = {}) {
    const edits = {};
    (preview?.content_package?.suite?.items || []).forEach((item) => {
      const id = String(item.id || "");
      edits[id] = {
        title: document.querySelector(`[data-story-title="${id}"]`)?.value || "",
        focus: document.querySelector(`[data-story-focus="${id}"]`)?.value || "",
      };
    });
    try {
      const result = await post("content-package/storyboard-edits", {
        offer_id: currentOfferId(), expected_revision: Number(preview?.content_package?.revision || 0), edits,
      });
      preview.content_package = result.content_package;
      storyboardDraft = {};
      storyboardDraftOfferId = "";
      storyboardDraftDirty = false;
      if (!quiet) {
        render();
        toast("人工修改已保存；需要重新预检，尚未创建付费任务。");
      }
      return result;
    } catch (error) {
      showAlert(error.message);
      throw error;
    }
  }

  document.addEventListener("click", async (event) => {
    if (event.target?.id !== "saveStoryboardEdits") return;
    try { await saveStoryboardEdits(); } catch (_) { /* already shown */ }
  });

  function updateStrategyUi() {
    const sourceOnly = sourceOnlyActive();
    $("#generationRecipe").hidden = sourceOnly;
    $("#storyboardGrid").hidden = sourceOnly;
    $("#generated").hidden = sourceOnly;
    $(".approval-strip").hidden = sourceOnly;
    $("#sourceOnlyGenerationNotice").hidden = !sourceOnly;
    $("#saveSourceButton").hidden = sourceOnly;
    $("#sourceTitle").textContent = sourceOnly ? "选择要使用的来源图" : "来源图审核";
    $("#storyboardTitle").textContent = sourceOnly ? "图片使用方式" : "经验配方与内容范围";
    $("#storyboardDescription").textContent = sourceOnly
      ? "当前只使用妙手已采集的来源图片，不会规划或生成更多图片。"
      : "确认商品事实和本次图片数量后，AI 分镜作为持续优化的经验配方自动采用；最终成图仍需逐张人工审核。";
    $("#storyboardNavLink").textContent = sourceOnly ? "图片策略" : "分镜";
    $("#generatedNavLink").hidden = sourceOnly;
    $("#finalTitle").textContent = sourceOnly ? "来源图片顺序" : "最终图片与排序";
    $("#finalDescription").textContent = sourceOnly
      ? "保留的来源图会立即出现在这里；第 1 张作为主图。调整顺序后一次保存选择与排序。"
      : "第 1 张将作为主图；排序先保存到本地，再单独决定是否同步妙手。";
    setButtonLabel(
      $("#saveOrderButton"),
      sourceOnly ? "保存并批准最终内容" : "保存最终顺序",
    );
    $("#sourceOnlySaveStatus").hidden = !sourceOnly;
    $("#sourceOnlySaveStatus").textContent = sourceOnlySaveFeedback || (
      sourceOnlyDraftDirty
        ? "选择或顺序尚未保存；最终内容批准已失效。"
        : sourceOnlyFinalApproved()
          ? `最终内容已批准：${finalOrder.length} 张来源图及当前视频决定已锁定；尚未写入妙手。`
          : `当前已保存 ${finalOrder.length} 张来源图；请点击“保存并批准最终内容”。`
    );
    $("#generationBasisTitle").textContent = sourceOnly
      ? "来源素材与最终顺序已确认"
      : "生成依据已确认";
    $("#generationBasisHint").textContent = sourceOnly
      ? "所有来源图已逐张决定，最终顺序只包含保留的 HTTPS 来源图"
      : "商品事实、身份参考、图片数量与类目约束均符合本次需求";
    $("#strategyStatus").textContent = sourceOnly
      ? "AI 相关入口已关闭；历史 AI 图片不会进入本次最终图片。"
      : "AI 入口可用；商品事实与配方需确认，经验分镜自动采用，付费生成和成图审核仍由人工决定。";
    $$(".recipe-count, #sizeDimensions, #sizeConfirmed")
      .forEach((node) => { node.disabled = sourceOnly; });
    ["aiPlanButton", "preflightButton", "paidGenerateButton", "saveVersionsButton"]
      .forEach((id) => { $(`#${id}`).disabled = sourceOnly; });
    if (sourceOnly) {
      reportPlanningBlocker({
        title: "AI 分镜当前不可用",
        message: "当前为仅来源图策略，AI 分镜已禁用；切换到 AI 辅助套图后才能规划分镜，且不会创建任何 AI 或付费任务。",
        action: "source-only",
      });
    } else {
      clearPlanningBlocker("source-only");
    }
  }

  function versionImageUrl(artifact) {
    return localImage(preview.offer_id, artifact.id);
  }

  function renderVersions() {
    const content = preview?.content_package || {};
    const artifacts = content.artifacts || [];
    const generation = content.remaining_images_generation || {};
    const preflight = content.remaining_images_preflight || {};
    const total = Number(preflight.total || preflight.shots?.length || 0);
    const preflightReady = (
      preflight.status === "ready_for_explicit_paid_confirmation"
      && total > 0
    );
    const currentByArtifact = new Map(generatedCurrentRows().map((row) => [row.artifact_id, row]));
    const grid = $("#versionGrid");
    grid.classList.remove("skeleton");
    if (sourceOnlyActive()) {
      grid.innerHTML = '<article class="story-card"><p>当前策略仅使用来源图。历史 AI 版本保留但不会显示、审核或混入最终图片。</p></article>';
      $("#generationSummary").innerHTML = "<span>AI 生图已跳过</span><span>付费调用已禁用</span>";
      $("#preflightButton").disabled = true;
      $("#preflightButton").hidden = true;
      $("#paidGenerateButton").disabled = true;
      $("#saveVersionsButton").disabled = true;
      return;
    }
    const proposalReady = Boolean(
      content.model_proposal?.valid
      && content.suite_approved
      && content.planning_review_mode === "experience_recipe_auto_v1"
    );
    $("#preflightButton").hidden = preflightReady || !proposalReady;
    setButtonLabel(
      $("#preflightButton"),
      preflight.status && preflight.status !== "not_started"
        ? "重新运行生成检查"
        : "运行生成检查",
    );
    grid.innerHTML = artifacts.length ? artifacts.map((artifact) => {
      const current = currentByArtifact.get(artifact.id);
      const decision = artifact.decision || "pending";
      return `
        <article class="version-card ${["rework", "rejected"].includes(decision) ? "rework" : ""}">
          <div class="asset-image" data-preview-url="${esc(versionImageUrl(artifact))}" data-preview-label="${esc(artifact.id)}">
            <img src="${esc(versionImageUrl(artifact))}" alt="${esc(artifact.id)}" loading="lazy">
            <span class="asset-index">${esc(artifact.shot_id || "AI")}</span>
          </div>
          <div class="version-meta">
            <header><h3>${esc(artifact.id)}</h3><small>${artifact.technical_complete ? "技术核验通过" : "技术未完成"}</small></header>
            <label>图片决定
              <select class="asset-decision" data-artifact-id="${esc(artifact.id)}">
                ${["pending", "approved", "rework", "rejected"].map((value) => `<option value="${value}" ${decision === value ? "selected" : ""}>${({
                  pending:"待决定",
                  approved: current ? "通过并保留" : "历史通过（已被新版本替代）",
                  rework:"返工且不保留",
                  rejected:"拒绝且不保留",
                })[value]}</option>`).join("")}
              </select>
            </label>
            <label>版本备注<textarea class="asset-note" data-artifact-id="${esc(artifact.id)}">${esc(artifact.note || "")}</textarea></label>
            <p>${esc(artifact.task_id || "无任务 ID")}</p>
          </div>
        </article>
      `;
    }).join("") : (
      preflightReady
        ? '<article class="story-card"><p>生成前检查已经完成，但尚未创建付费任务，因此当前没有生成版本。确认付费后会在这里逐张显示进度和结果。</p></article>'
        : '<article class="story-card"><p>当前没有生成版本。先保存经验配方并完成生成前检查。</p></article>'
    );

    const running = ["queued", "running"].includes(generation.status);
    const completed = ["completed_waiting_human_review", "completed_with_errors"].includes(generation.status);
    const generationStatusLabel = (
      preflightReady && (!generation.status || generation.status === "not_started")
    )
      ? "等待付费确认"
      : (generation.status || "未运行");
    $("#generationSummary").innerHTML = [
      `已生成 ${content.generated_review_images?.length || 0}`,
      `历史版本 ${artifacts.length}`,
      `任务状态 ${generationStatusLabel}`,
      `本次预检 ${total} 张`,
    ].map((text) => `<span>${esc(text)}</span>`).join("");
    $("#paidGenerateButton").disabled = !total || running || completed;
    setButtonLabel($("#paidGenerateButton"), running
      ? "生成任务进行中"
      : completed
        ? "本次付费生成已完成"
        : `确认付费并生成${total ? ` ${total} 张` : ""}`);
    attachImagePreview();
  }

  function renderFinal() {
    finalOrder = buildFinalItems();
    const grid = $("#finalGrid");
    grid.innerHTML = finalOrder.length ? finalOrder.map((row, index) => `
      <article class="final-row">
        <span class="final-position">${String(index + 1).padStart(2, "0")}</span>
        <img class="final-thumb" src="${esc(row.type === "source" ? proxyImage(row.url) : localImage(preview.offer_id, row.id))}" alt="${esc(row.label)}">
        <div class="final-info"><strong>${esc(row.label)}</strong><small>${esc(row.id)}</small></div>
        <div class="move-actions">
          <button type="button" data-move="-1" data-index="${index}" aria-label="向前移动" ${index === 0 ? "disabled" : ""}>↑</button>
          <button type="button" data-move="1" data-index="${index}" aria-label="向后移动" ${index === finalOrder.length - 1 ? "disabled" : ""}>↓</button>
        </div>
      </article>
    `).join("") : '<article class="story-card"><p>最终图片为空。先在来源图和生成版本中选择“保留”。</p></article>';
    $$(".move-actions button").forEach((button) => button.addEventListener("click", () => {
      const index = Number(button.dataset.index);
      const target = index + Number(button.dataset.move);
      if (target < 0 || target >= finalOrder.length) return;
      [finalOrder[index], finalOrder[target]] = [finalOrder[target], finalOrder[index]];
      const order = finalOrder.map((row) => row.url);
      if (sourceOnlyActive()) {
        if (!sourceOnlyDraft) captureSourceOnlyDraft();
        sourceOnlyDraft.image_order = order;
        sourceOnlyDraftDirty = true;
        sourceOnlySaveFeedback = "";
      } else {
        preview.review.image_order = order;
      }
      renderFinal();
      if (sourceOnlyActive()) {
        updateStrategyUi();
        renderProject();
      }
    }));
  }

  function defaultSyncSteps(activeId = "") {
    const rows = [
      ["review_gate", "审核门禁与最终顺序"],
      ["read_current", "读取妙手当前版本"],
      ["write_images", "写入主图与详情图"],
      ["readback_verify", "回读并逐项验证"],
    ];
    const activeIndex = rows.findIndex(([id]) => id === activeId);
    return rows.map(([id, label], index) => ({
      id,
      label,
      status: activeIndex < 0
        ? "pending"
        : (index < activeIndex ? "completed" : (index === activeIndex ? "running" : "pending")),
      detail: "",
    }));
  }

  function renderSyncFeedback(override = null) {
    if (override) syncFeedbackOverride = override;
    const sync = syncFeedbackOverride
      || preview?.content_package?.miaoshou_generated_images_write
      || {};
    const status = String(sync.status || "not_started");
    let steps = Array.isArray(sync.steps) && sync.steps.length
      ? sync.steps
      : defaultSyncSteps(status === "not_started" ? "" : String(sync.phase || ""));
    if (status === "verified" && !(sync.steps || []).length) {
      steps = defaultSyncSteps().map((step) => ({ ...step, status: "completed" }));
    }
    const statusMeta = {
      not_started: ["等待同步确认", "未开始", "neutral"],
      preparing: ["正在读取妙手当前版本", "进行中", "warn"],
      writing: ["正在写入主图与详情图", "写入中", "warn"],
      verifying: ["写入已完成，正在回读验证", "回读中", "warn"],
      verified: ["妙手图片已同步并通过回读验证", "已验证", "safe"],
      failed: ["妙手同步失败", "失败", "danger"],
      verification_failed: ["写入后回读验证未通过", "需处理", "danger"],
    };
    const [title, badge, tone] = statusMeta[status] || ["妙手同步状态", status, "neutral"];
    $("#syncProgressTitle").textContent = title;
    $("#syncProgressBadge").textContent = badge;
    $("#syncProgressBadge").className = `badge ${tone}`;
    $("#syncStepList").innerHTML = steps.map((step) => `
      <li class="${esc(step.status || "pending")}">
        <strong>${esc(step.label || step.id || "同步步骤")}</strong>
        <span>${esc(step.detail || (
          step.status === "completed" ? "已完成" :
          step.status === "running" ? "正在执行" :
          step.status === "failed" ? "执行失败" : "等待执行"
        ))}</span>
      </li>
    `).join("");
    const checks = sync.checks || {};
    const passedChecks = Object.values(checks).filter(Boolean).length;
    const totalChecks = Object.keys(checks).length;
    $("#syncProgressMessage").textContent = sync.error
      ? `失败原因：${sync.error}`
      : (status === "verified"
        ? `已写入 ${sync.written_image_count || sync.ordered_image_count || finalOrder.length} 张图片；${passedChecks}/${totalChecks || 4} 项回读检查通过。未认领或发布商品。`
        : (syncInFlight
          ? "页面会持续读取任务状态；请勿关闭或重复点击。"
          : "勾选确认并点击同步后，这里会展示每个真实步骤和回读结果。"));
  }

  const IMAGE_REGION_CLASSIFICATIONS = Object.freeze([
    ["watermark", "水印"],
    ["supplier_metadata", "供应商信息"],
    ["translatable", "需要翻译"],
    ["product_fact", "商品事实"],
    ["protected_natural_text", "产品原生文字（保护）"],
    ["dimension", "尺寸文字"],
    ["rebuild_required", "复杂详情图（需重建）"],
    ["ignore", "忽略"],
  ]);

  function imageLocalizationState() {
    return preview?.content_package?.image_localization || {};
  }

  function setImageLocalizationStatus(message, tone = "") {
    const status = $("#imageLocalizationStatus");
    status.textContent = message || "";
    status.className = `localization-status ${tone}`.trim();
    status.hidden = !message;
  }

  function localizationRegionsFor(asset) {
    if (
      imageLocalizationDraftOfferId === currentOfferId()
      && Array.isArray(imageLocalizationDraft[asset.asset_id])
    ) {
      return imageLocalizationDraft[asset.asset_id];
    }
    return Array.isArray(asset.regions) ? asset.regions : [];
  }

  function regionOptions(selected) {
    return IMAGE_REGION_CLASSIFICATIONS.map(([value, label]) => (
      `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`
    )).join("");
  }

  function regionEditorRow(assetId, region, index) {
    const box = Array.isArray(region.bbox) && region.bbox.length === 4
      ? region.bbox
      : [0.05, 0.80, 0.95, 0.96];
    return `
      <div class="localization-region" data-asset-id="${esc(assetId)}" data-region-index="${index}">
        <input class="region-id" type="text" value="${esc(region.region_id || `manual-${Date.now()}-${index}`)}" aria-label="区域 ID">
        <select class="region-classification" aria-label="区域分类">${regionOptions(region.classification || "translatable")}</select>
        <input class="region-text" type="text" maxlength="2000" value="${esc(region.text || "")}" placeholder="识别或人工输入的文字" aria-label="区域文字">
        ${box.map((value, position) => `<input class="region-box" data-position="${position}" type="number" min="0" max="1" step="0.01" value="${esc(value)}" aria-label="边界 ${position + 1}">`).join("")}
        <button class="region-remove" type="button" aria-label="删除区域">×</button>
      </div>
    `;
  }

  function renderImageLocalization() {
    const localization = imageLocalizationState();
    const features = localization.features || {};
    const manifest = localization.manifest || {};
    const assets = Array.isArray(manifest.assets) ? manifest.assets : [];
    const section = $("#imageLocalization");
    section.hidden = !localization.enabled;
    if (!localization.enabled) return;
    $("#ocrProviderStatus").textContent = features.ocr_provider_enabled
      ? "OCR provider 已启用"
      : "OCR provider 未启用 · 可人工标注";
    $("#ocrProviderStatus").className = `badge ${features.ocr_provider_enabled ? "safe" : "neutral"}`;
    $("#initializeLocalizationButton").hidden = Boolean(localization.initialized);
    const grid = $("#imageLocalizationGrid");
    if (!assets.length) {
      grid.innerHTML = '<p class="localization-empty">尚未建立处理清单。点击“建立图片处理清单”只会保存本地图片身份，不调用 OCR 或图片服务。</p>';
      return;
    }
    grid.innerHTML = assets.map((asset) => {
      const regions = localizationRegionsFor(asset);
      const clean = asset.clean_master || {};
      const overlays = regions.map((region) => {
        const box = region.bbox || [0, 0, 0, 0];
        const tone = region.classification === "protected_natural_text" ? "protected" : (
          ["watermark", "supplier_metadata"].includes(region.classification) ? "remove" : "review"
        );
        return `<span class="localization-overlay ${tone}" style="left:${box[0] * 100}%;top:${box[1] * 100}%;width:${(box[2] - box[0]) * 100}%;height:${(box[3] - box[1]) * 100}%" title="${esc(region.classification)}"></span>`;
      }).join("");
      return `
        <article class="localization-card" data-asset-id="${esc(asset.asset_id)}">
          <header><div><strong>${esc(asset.source_kind || "source")} · ${esc(asset.asset_id)}</strong><small>清单 revision ${esc(manifest.revision)}</small></div><span class="badge ${clean.status === "created" ? "safe" : "neutral"}">${esc(clean.status || "not_created")}</span></header>
          <div class="localization-preview">
            <div class="localization-image"><img src="${esc(proxyImage(asset.source_url))}" alt="原始来源图">${overlays}</div>
            ${clean.local_url ? `<div class="localization-image clean"><img src="${esc(clean.local_url)}" alt="干净母图"><span>不可变干净母图</span></div>` : ""}
          </div>
          <div class="localization-region-head"><strong>文字与保护区域</strong><small>坐标为 0–1：x0, y0, x1, y1</small></div>
          <div class="localization-regions">${regions.map((region, index) => regionEditorRow(asset.asset_id, region, index)).join("")}</div>
          <div class="localization-card-actions">
            <button class="button secondary localization-add-region" type="button">添加人工区域</button>
            <button class="button dark localization-save-regions" type="button">保存区域</button>
            <button class="button secondary localization-clean-master" type="button">生成本地干净母图</button>
          </div>
        </article>
      `;
    }).join("");

    $$(".localization-add-region").forEach((button) => button.addEventListener("click", () => {
      const card = button.closest(".localization-card");
      const host = card.querySelector(".localization-regions");
      const index = host.querySelectorAll(".localization-region").length;
      host.insertAdjacentHTML("beforeend", regionEditorRow(card.dataset.assetId, {}, index));
      bindLocalizationRegionRemoveButtons(card);
      bindLocalizationDraftInputs(card);
      captureLocalizationDraft(card.dataset.assetId);
    }));
    $$(".localization-save-regions").forEach((button) => button.addEventListener("click", () => (
      saveImageLocalizationRegions(button.closest(".localization-card").dataset.assetId)
    )));
    $$(".localization-clean-master").forEach((button) => button.addEventListener("click", () => (
      createCleanMaster(button.closest(".localization-card").dataset.assetId)
    )));
    bindLocalizationRegionRemoveButtons(grid);
    bindLocalizationDraftInputs(grid);
  }

  function bindLocalizationDraftInputs(root) {
    root.querySelectorAll(".localization-region input,.localization-region select").forEach((node) => {
      node.addEventListener("input", () => {
        captureLocalizationDraft(node.closest(".localization-region").dataset.assetId);
      });
      node.addEventListener("change", () => {
        captureLocalizationDraft(node.closest(".localization-region").dataset.assetId);
      });
    });
  }

  function bindLocalizationRegionRemoveButtons(root) {
    root.querySelectorAll(".region-remove").forEach((button) => {
      button.onclick = () => {
        const row = button.closest(".localization-region");
        const assetId = row.dataset.assetId;
        row.remove();
        captureLocalizationDraft(assetId);
      };
    });
  }

  function collectLocalizationRegions(assetId) {
    return $$(".localization-region")
      .filter((row) => row.dataset.assetId === assetId)
      .map((row) => ({
        region_id: row.querySelector(".region-id").value.trim(),
        classification: row.querySelector(".region-classification").value,
        text: row.querySelector(".region-text").value,
        bbox: Array.from(row.querySelectorAll(".region-box")).map((input) => Number(input.value)),
        origin: "manual",
      }));
  }

  function captureLocalizationDraft(assetId) {
    imageLocalizationDraftOfferId = currentOfferId();
    imageLocalizationDraft[assetId] = collectLocalizationRegions(assetId);
  }

  async function initializeImageLocalization() {
    setLoading($("#initializeLocalizationButton"), true);
    setImageLocalizationStatus("正在建立本地图片处理清单…", "pending");
    try {
      const result = await post("content-package/image-localization/initialize", {
        offer_id: currentOfferId(),
      });
      preview.content_package.image_localization = result.image_localization;
      imageLocalizationDraft = {};
      imageLocalizationDraftOfferId = currentOfferId();
      renderImageLocalization();
      setImageLocalizationStatus(`图片处理清单已建立，共 ${result.image_localization.manifest.assets.length} 张。`);
      toast("已建立本地图片处理清单；未调用 OCR 或外部图片服务。");
    } catch (error) {
      setImageLocalizationStatus(`建立失败：${error.message}`, "error");
      showAlert(error.message);
    } finally {
      setLoading($("#initializeLocalizationButton"), false);
    }
  }

  function setLocalizedImagePackStatus(message, tone = "") {
    const node = $("#localizedImagePackStatus");
    node.textContent = message || "";
    node.hidden = !message;
    node.className = `localization-status ${tone}`.trim();
  }

  function renderLocalizedImageProject() {
    const packGrid = $("#localizedImagePackGrid");
    const routeGrid = $("#localizedImageRouteGrid");
    const summary = localizedImageProject || {};
    const project = summary.project || {};
    const packs = project.packs || {};
    const routes = project.route_draft?.routes || {};
    const initialized = Boolean(summary.initialized && Object.keys(packs).length);
    $("#initializeLocalizedImagePacksButton").hidden = initialized;
    if (!initialized) {
      packGrid.innerHTML = '<div class="localized-empty">尚未导入。完成商品发布中心审核后，可在这里建立独立语言图片项目。</div>';
      routeGrid.innerHTML = "";
      return;
    }
    packGrid.innerHTML = Object.values(packs).map((pack) => {
      const ready = pack.status === "READY_BASE";
      return `
        <article class="localized-pack-card ${ready ? "ready" : ""}">
          <header><strong>${esc(pack.locale)}</strong><span class="badge">${esc(pack.status)}</span></header>
          <small>${esc((pack.images || []).length)} 张 · revision ${esc(pack.revision)}</small>
          <small>${ready ? "复用已批准英文母版" : "待文字识别、翻译、排版与人工批准"}</small>
        </article>
      `;
    }).join("");
    routeGrid.innerHTML = Object.entries(routes).map(([target, route]) => (
      `<span class="localized-route">${esc(target)} → ${esc(route.locale)}</span>`
    )).join("");
    setLocalizedImagePackStatus(
      `已绑定批准计划 ${project.release_plan_id}；语言图片项目独立保存，外部写入 ${summary.external_writes || 0}。`,
    );
  }

  async function loadLocalizedImageProject({ quiet = false } = {}) {
    try {
      const result = await requestJson(
        `${flowApi("content-package/localized-images")}?offer_id=${encodeURIComponent(currentOfferId())}`,
      );
      localizedImageProject = result.localized_images || null;
      renderLocalizedImageProject();
    } catch (error) {
      localizedImageProject = null;
      renderLocalizedImageProject();
      setLocalizedImagePackStatus(`读取失败：${error.message}`, "error");
      if (!quiet) showAlert(error.message);
    }
  }

  async function initializeLocalizedImageProject() {
    const button = $("#initializeLocalizedImagePacksButton");
    setLoading(button, true);
    setLocalizedImagePackStatus("正在从已批准 ReleasePlan 导入最终英文图片…", "pending");
    try {
      const result = await post("content-package/localized-images/initialize", {
        offer_id: currentOfferId(),
      });
      localizedImageProject = result.localized_images || null;
      renderLocalizedImageProject();
      toast("独立语言图片项目已建立；商品发布中心和平台均未被修改。");
    } catch (error) {
      setLocalizedImagePackStatus(`导入失败：${error.message}`, "error");
      showAlert(error.message);
    } finally {
      setLoading(button, false);
    }
  }

  async function saveImageLocalizationRegions(assetId, { quiet = false } = {}) {
    const manifest = imageLocalizationState().manifest || {};
    setImageLocalizationStatus("正在保存区域…", "pending");
    try {
      const result = await post("content-package/image-localization/regions", {
        offer_id: currentOfferId(),
        expected_revision: manifest.revision,
        asset_id: assetId,
        regions: collectLocalizationRegions(assetId),
      });
      preview.content_package.image_localization = result.image_localization;
      delete imageLocalizationDraft[assetId];
      imageLocalizationDraftOfferId = currentOfferId();
      renderImageLocalization();
      setImageLocalizationStatus("区域已保存；原始图片没有被修改。");
      if (!quiet) toast("区域已保存；原始图片没有被修改。");
      return result.image_localization;
    } catch (error) {
      setImageLocalizationStatus(`保存失败：${error.message}`, "error");
      showAlert(error.message);
      if (quiet) throw error;
      return null;
    }
  }

  async function createCleanMaster(assetId) {
    const button = $$(".localization-card").find((card) => card.dataset.assetId === assetId)
      ?.querySelector(".localization-clean-master");
    setLoading(button, true);
    setImageLocalizationStatus("正在保存区域并生成本地干净母图…", "pending");
    try {
      const saved = await saveImageLocalizationRegions(assetId, { quiet: true });
      const result = await post("content-package/image-localization/clean-master", {
        offer_id: currentOfferId(),
        expected_revision: saved.manifest.revision,
        asset_id: assetId,
        method: "local_region_fill/v1",
        confirm_local_clean_master: true,
      });
      preview.content_package.image_localization = result.image_localization;
      renderImageLocalization();
      setImageLocalizationStatus("本地干净母图已生成；来源原图保持不变。");
      toast("本地干净母图已生成；来源原图保持不变。");
    } catch (error) {
      setImageLocalizationStatus(`生成失败：${error.message}`, "error");
      showAlert(error.message);
    } finally {
      setLoading(button, false);
    }
  }

  function render() {
    finalOrder = buildFinalItems();
    renderProject();
    renderSources();
    renderImageLocalization();
    renderLocalizedImageProject();
    renderStoryboard();
    renderPlanningProgress();
    renderVersions();
    renderGenerationProgress();
    renderFinal();
    renderSyncFeedback();
    schedulePoll();
  }

  function attachImagePreview() {
    $$(".asset-image").forEach((node) => {
      node.addEventListener("click", () => {
        $("#dialogImage").src = node.dataset.previewUrl;
        $("#dialogImage").alt = node.dataset.previewLabel;
        $("#dialogCaption").textContent = node.dataset.previewLabel;
        $("#imageDialog").showModal();
      });
    });
    $$(".asset-image img").forEach((image) => image.addEventListener("error", () => {
      image.alt = "图片暂时无法加载";
      image.closest(".asset-image").classList.add("image-error");
    }, { once: true }));
  }

  async function load({ quiet = false } = {}) {
    const offerId = $("#offerId").value.trim();
    if (!/^\d{1,32}$/.test(offerId)) {
      showAlert("Offer ID 必须是 1–32 位数字。");
      return;
    }
    setLoading($("#offerForm"), true);
    if (!quiet) showAlert("");
    try {
      const loadedPreview = await requestJson(`${flowApi("preview")}?offer_id=${encodeURIComponent(offerId)}`);
      if (
        recipeDraftDirty
        && recipeDraftOfferId === offerId
        && recipeDraftBaseline
        && JSON.stringify(recipeFromContent(loadedPreview.content_package || {})) !== recipeDraftBaseline
      ) {
        showAlert("服务端配方已发生变化；页面已保留你尚未保存的数字。请核对后再保存，不会静默覆盖。");
      }
      if (!quiet || String(preview?.offer_id || "") !== offerId) {
        planningProgressOverride = null;
        generationProgressOverride = null;
      }
      const preserveSourceDraft = (
        sourceOnlyDraftDirty
        && sourceOnlyDraftOfferId === offerId
      );
      preview = loadedPreview;
      if (!localizedImageProject || localizedImageProject.offer_id !== offerId) {
        localizedImageProject = null;
      }
      if (imageLocalizationDraftOfferId && imageLocalizationDraftOfferId !== offerId) {
        imageLocalizationDraft = {};
        imageLocalizationDraftOfferId = "";
      }
      if (!syncInFlight) syncFeedbackOverride = null;
      if (!quiet) contentStrategyDraft = null;
      if (preserveSourceDraft) contentStrategyDraft = "source_only";
      if (recipeDraftOfferId && recipeDraftOfferId !== offerId) clearRecipeDraft();
      if (sourceOnlyDraftOfferId && sourceOnlyDraftOfferId !== offerId) {
        clearSourceOnlyDraft();
      } else if (preserveSourceDraft) {
        showAlert("页面已保留尚未保存的来源图选择与顺序；服务端刷新不会静默覆盖本地草稿。");
      }
      finalOrder = buildFinalItems();
      render();
      await loadLocalizedImageProject({ quiet: true });
      const url = new URL(window.location.href);
      url.searchParams.set("offer_id", offerId);
      history.replaceState(null, "", url);
      if (!quiet) toast("图片项目已加载。");
    } catch (error) {
      showAlert(error.message || "图片项目读取失败。");
    } finally {
      setLoading($("#offerForm"), false);
    }
  }

  async function saveSourceReview({
    successMessage = "来源图决定、备注和当前排序已保存到本地。",
    quiet = false,
  } = {}) {
    if (sourceReviewSubmitting) return null;
    sourceReviewSubmitting = true;
    $$(".source-remove").forEach((button) => { button.disabled = true; });
    if (sourceOnlyActive()) {
      try {
        return await saveSourceOnlyReview({ approveFinal: false });
      } finally {
        sourceReviewSubmitting = false;
        $$(".source-remove").forEach((button) => { button.disabled = false; });
      }
    }
    setLoading($("#saveSourceButton"), true);
    try {
      const sourceRows = sourceRowsFromDom();
      const keptUrls = sourceRows
        .filter((row) => row.action === "keep")
        .map((row) => row.output_url || row.url)
        .filter(Boolean);
      const savedOrder = preview?.review?.image_order || [];
      const sourceOrder = [
        ...savedOrder.filter((url) => keptUrls.includes(url)),
        ...keptUrls.filter((url) => !savedOrder.includes(url)),
      ];
      const result = await post("content-package/review", {
        offer_id: currentOfferId(),
        review: {
          ...contentReviewPayload(),
          expected_revision: preview?.revision,
          image_actions: sourceRows,
          image_order: sourceOrder,
        },
      });
      preview.content_package = result.content_package;
      await load({ quiet: true });
      if (!quiet) toast(successMessage);
      return result;
    } catch (error) {
      showAlert(error.message);
      if (quiet) throw error;
      return null;
    } finally {
      sourceReviewSubmitting = false;
      setLoading($("#saveSourceButton"), false);
      $$(".source-remove").forEach((button) => { button.disabled = false; });
    }
  }

  async function saveSourceOnlyReview({ approveFinal = false } = {}) {
    captureSourceOnlyDraft();
    const review = activeReview();
    const keptCount = (review.image_actions || []).filter(
      (row) => row.action === "keep",
    ).length;
    if (!keptCount) {
      showAlert("至少保留 1 张来源图后才能保存最终顺序。");
      return null;
    }
    setLoading($("#saveOrderButton"), true);
    showAlert("");
    try {
      const result = await post("content-package/source-only/review", {
        offer_id: currentOfferId(),
        review: {
          expected_revision: preview?.revision,
          image_actions: review.image_actions || [],
          image_order: review.image_order || [],
          video_action: review.video_action || "none",
          confirm_final_content_approval: approveFinal,
          ...(approveFinal ? { approved_by: "Kyle" } : {}),
        },
      });
      preview = result;
      sourceOnlyDraft = null;
      sourceOnlyDraftOfferId = "";
      sourceOnlyDraftDirty = false;
      sourceOnlySaveFeedback = approveFinal
        ? `已保存并批准最终内容：${keptCount} 张来源图、顺序和视频决定已锁定；尚未写入妙手。`
        : `已保存 ${keptCount} 张来源图草稿；最终内容尚未批准，也尚未写入妙手。`;
      finalOrder = buildFinalItems();
      render();
      toast(sourceOnlySaveFeedback);
      return result;
    } catch (error) {
      showAlert(error.message);
      return null;
    } finally {
      setLoading($("#saveOrderButton"), false);
    }
  }

  async function saveContentReview({ quiet = false } = {}) {
    const recipeCheck = validateRecipe();
    if (!recipeCheck.valid) {
      showAlert(recipeCheck.message);
      if (quiet) throw new Error(recipeCheck.message);
      return null;
    }
    setLoading($("#savePlanButton"), true);
    try {
      const result = await post("content-package/review", {
        offer_id: currentOfferId(),
        review: contentReviewPayload(),
      });
      preview.content_package = result.content_package;
      contentStrategyDraft = null;
      clearRecipeDraft();
      if (!quiet) {
        render();
        toast("本次配方、身份参考与内容范围已保存到本地。");
      }
      return result;
    } catch (error) {
      showAlert(error.message);
      if (quiet) throw error;
      return null;
    } finally {
      setLoading($("#savePlanButton"), false);
    }
  }

  async function preparePackage() {
    if (!confirm("将从现有妙手采集箱读取素材并创建本地审核包。若已有内容决定，重新创建会重置审核状态。确认继续吗？")) return;
    setLoading($("#preparePackageButton"), true);
    renderPlanningProgress({
      status: "running",
      step: 0,
      completedThrough: -1,
      title: "正在创建本地内容审核包",
      message: "正在读取现有妙手采集箱素材；不会调用模型或生成图片。",
      badge: "创建中",
    });
    try {
      const result = await post("content-package/prepare", {
        offer_id: currentOfferId(),
        collect_box_id: preview?.content_package?.collect_box_id || currentOfferId(),
      });
      preview.content_package = result.content_package;
      planningProgressOverride = null;
      render();
      toast("本地内容审核包已创建；未调用模型或生成图片。");
    } catch (error) {
      renderPlanningProgress({
        status: "failed",
        step: 0,
        completedThrough: -1,
        title: "本地内容审核包创建失败",
        error: planningErrorMessage(error),
        badge: "失败",
      });
      showAlert(planningErrorMessage(error));
    } finally {
      setLoading($("#preparePackageButton"), false);
    }
  }

  async function requestAiPlan() {
    if (sourceOnlyActive()) {
      reportPlanningBlocker({
        title: "AI 分镜当前不可用",
        message: "当前为仅来源图策略，AI 分镜已禁用；切换到 AI 辅助套图后才能规划分镜。",
        action: "source-only",
      });
      return;
    }
    if (!preview?.content_package?.package_found) {
      reportPlanningBlocker({
        title: "AI 分镜尚未开始",
        message: "还没有创建本地内容审核包。先读取妙手素材并建立本地审核包，再重新确认商品事实和本次配方；本次没有调用 AI，也没有产生生图费用。",
        action: "prepare-package",
      });
      return;
    }
    const refs = selectedIdentityReferences();
    if (!refs.length) {
      reportPlanningBlocker({
        title: "AI 分镜尚未开始",
        message: "请先选择至少一张身份参考图；本次点击未发送 AI 规划请求。",
        action: "identity-reference",
      });
      return;
    }
    if (!$("#generationBasisConfirmed").checked) {
      reportPlanningBlocker({
        title: "AI 分镜尚未开始",
        message: "请先确认商品事实和本地生图约束；本次点击未发送 AI 规划请求。",
        action: "approval",
      });
      return;
    }
    const recipeCheck = validateRecipe();
    if (!recipeCheck.valid) {
      reportPlanningBlocker({
        title: "AI 分镜尚未开始",
        message: `${recipeCheck.message} 本次点击未发送 AI 规划请求。`,
        action: "recipe",
      });
      return;
    }
    const recipe = collectRecipeFromDom();
    if (!confirm(`将调用 AI 读取 ${refs.length} 张身份参考图，并严格按场景 ${recipe.type_counts.scene}、卖点 ${recipe.type_counts.selling_point}、尺寸 ${recipe.type_counts.size_card}，共 ${recipeCheck.total} 张规划分镜。会产生模型费用，但不会生成商品图片。确认继续吗？`)) return;
    setLoading($("#aiPlanButton"), true);
    showAlert("");
    renderPlanningProgress({
      status: "running",
      step: 0,
      completedThrough: -1,
      title: "正在保存本次图片配方",
      message: "先固定商品事实、身份参考与图片数量。",
      badge: "1 / 4",
    });
    try {
      await nextPaint();
      await saveStoryboardEdits();
      await saveContentReview({ quiet: true });
      renderPlanningProgress({
        status: "running",
        step: 1,
        completedThrough: 0,
        title: "AI 正在规划本次分镜",
        message: `正在读取 ${refs.length} 张身份参考图并规划 ${recipeCheck.total} 张构图；此步骤不生成商品图片。`,
        badge: "2 / 4",
      });
      await nextPaint();
      const result = await post("content-package/vision-proposal", {
        offer_id: currentOfferId(),
        reference_urls: refs,
        storyboard_feedback: {},
        confirm_ai_planning: true,
      });
      preview.content_package = result.content_package;
      renderPlanningProgress({
        status: "running",
        step: 2,
        completedThrough: 1,
        title: "AI 分镜已返回，正在自动采用",
        message: "系统只采用符合类目政策与当前配方的分镜，不跳过付费确认。",
        badge: "3 / 4",
      });
      render();
      await nextPaint();
      renderPlanningProgress({
        status: "running",
        step: 3,
        completedThrough: 2,
        title: "正在执行生成前检查",
        message: "只验证最终提示词、参考图和任务参数；不会创建付费任务。",
        badge: "4 / 4",
      });
      renderGenerationProgress({
        status: "preflighting",
        message: "AI 分镜已自动采用，正在执行无付费生成前检查。",
      });
      await nextPaint();
      const pending = preview?.content_package?.pending_regeneration_shot_ids || [];
      const preflightResult = await post("content-package/suite-images-preflight", {
        offer_id: currentOfferId(),
        force_shot_ids: pending,
      });
      preview.content_package = preflightResult.content_package;
      planningProgressOverride = null;
      generationProgressOverride = null;
      render();
      toast("AI 分镜与生成前检查已完成；尚未创建付费任务，等待 Kyle 确认。");
    } catch (error) {
      const currentStep = Number(planningProgressOverride?.step ?? 0);
      const rawError = String(error?.message || "");
      renderPlanningProgress({
        status: "failed",
        step: currentStep,
        completedThrough: currentStep - 1,
        title: `${PLANNING_STEPS[currentStep] || "AI 分镜"}失败`,
        error: planningErrorMessage(error),
        badge: "失败",
        action: rawError.includes("create a local content review package")
          ? "prepare-package"
          : "",
      });
      if (currentStep === 3) {
        renderGenerationProgress({
          status: "error",
          step: 1,
          error: planningErrorMessage(error),
        });
      }
      showAlert(planningErrorMessage(error));
    } finally {
      setLoading($("#aiPlanButton"), false);
    }
  }

  async function prepareGeneration() {
    if (sourceOnlyActive()) {
      showAlert("当前为仅来源图策略，生图预检已禁用。");
      return;
    }
    setLoading($("#preflightButton"), true);
    showAlert("");
    renderGenerationProgress({
      status: "preflighting",
      message: "正在保存最新配方并执行无付费生成前检查。",
    });
    try {
      await nextPaint();
      await saveContentReview({ quiet: true });
      const pending = preview?.content_package?.pending_regeneration_shot_ids || [];
      const result = await post("content-package/suite-images-preflight", {
        offer_id: currentOfferId(),
        force_shot_ids: pending,
      });
      preview.content_package = result.content_package;
      generationProgressOverride = null;
      render();
      toast("生成前检查已完成；尚未创建付费任务，等待 Kyle 确认。");
    } catch (error) {
      renderGenerationProgress({
        status: "error",
        step: 1,
        error: error.message,
      });
      showAlert(error.message);
    } finally {
      setLoading($("#preflightButton"), false);
    }
  }

  async function startPaidGeneration() {
    if (sourceOnlyActive()) {
      showAlert("当前为仅来源图策略，付费生图已禁用。");
      return;
    }
    const preflight = preview?.content_package?.remaining_images_preflight || {};
    const total = Number(preflight.total || preflight.shots?.length || 0);
    if (!total) {
      showAlert("请先完成生成前检查。");
      return;
    }
    if (!confirm(`将创建 ${total} 个真实图片生成任务并产生费用。生成完成后仍需逐图人工审核。确认付费并继续吗？`)) return;
    setLoading($("#paidGenerateButton"), true);
    showAlert("");
    renderGenerationProgress({
      status: "submitting",
      message: `已收到 Kyle 对 ${total} 张图片的付费确认，正在建立任务队列。`,
    });
    try {
      await nextPaint();
      const result = await post("content-package/remaining-images-generate", {
        offer_id: currentOfferId(),
        confirm_paid_generation: true,
      });
      preview.content_package = result.content_package;
      generationProgressOverride = null;
      render();
      toast("付费生成任务已开始，页面会自动刷新进度。");
    } catch (error) {
      renderGenerationProgress({
        status: "error",
        step: 2,
        error: error.message,
      });
      showAlert(error.message);
    } finally {
      setLoading($("#paidGenerateButton"), false);
      renderVersions();
      renderGenerationProgress();
    }
  }

  function generatedDecisionRowsFromDom() {
    const currentArtifactIds = new Set(
      generatedCurrentRows().map((row) => row.artifact_id),
    );
    return $$(".asset-decision")
      .filter((node) => currentArtifactIds.has(node.dataset.artifactId))
      .map((node) => ({
        artifact_id: node.dataset.artifactId,
        action: node.value === "approved" ? "keep" : "remove",
      }));
  }

  async function saveAllContentDrafts({ includeFinalOrder = false } = {}) {
    if (sourceOnlyActive()) return null;
    const desiredOrder = finalOrder.map((row) => row.url);
    const finalActions = generatedDecisionRowsFromDom();
    if (storyboardDraftDirty) {
      await saveStoryboardEdits({ quiet: true });
      await load({ quiet: true });
    }
    await saveSourceReview({ quiet: true });
    for (const action of finalActions) {
      await post("content-package/generated-image/decision", {
        offer_id: currentOfferId(),
        ...action,
      });
    }
    await load({ quiet: true });
    if (!includeFinalOrder) return preview;
    preview = await post("review", {
      offer_id: currentOfferId(),
      review: reviewPayload({ image_order: desiredOrder }),
    });
    render();
    return preview;
  }

  async function saveVersionReview() {
    if (sourceOnlyActive()) {
      showAlert("当前为仅来源图策略，不审核或混入 AI 版本。");
      return;
    }
    setLoading($("#saveVersionsButton"), true);
    try {
      await saveAllContentDrafts();
      toast("图片决定已保存：通过即保留，返工、拒绝或待定均不进入最终图片。");
    } catch (error) {
      showAlert(error.message);
    } finally {
      setLoading($("#saveVersionsButton"), false);
    }
  }

  async function saveOrder({ quiet = false } = {}) {
    if (sourceOnlyActive()) {
      return saveSourceOnlyReview({ approveFinal: true });
    }
    setLoading($("#saveOrderButton"), true);
    try {
      await saveAllContentDrafts({ includeFinalOrder: true });
      if (!quiet) toast("最终图片顺序已保存到本地，尚未写入妙手。");
      return preview;
    } catch (error) {
      showAlert(error.message);
      throw error;
    } finally {
      setLoading($("#saveOrderButton"), false);
    }
  }

  function miaoshouImagesAlreadyVerified() {
    const content = preview?.content_package || {};
    const write = content.miaoshou_ordered_images_write
      || content.miaoshou_generated_images_write
      || {};
    return write.status === "verified" && content.final_content_approval_ready === true;
  }

  async function finalizeAiContentAfterVerifiedSync() {
    if (sourceOnlyActive() || preview?.content_package?.final_content_approval_valid) return;
    const result = await post("content-package/finalize", {
      offer_id: currentOfferId(),
      approval: {
        expected_revision: Number(preview?.content_package?.revision || 0),
        approved_by: "Kyle",
      },
    });
    preview.content_package = result.content_package;
  }

  async function syncMiaoshou() {
    if (!$("#miaoshouConfirm").checked) return;
    if (syncInFlight) return;
    if (sourceOnlyActive() && !sourceOnlyFinalApproved()) {
      showAlert("请先点击“保存并批准最终内容”，再同步到妙手。");
      return;
    }
    if (!finalOrder.length) {
      showAlert("最终图片为空，不能同步妙手。");
      return;
    }
    syncInFlight = true;
    $("#miaoshouConfirm").disabled = true;
    setLoading($("#syncMiaoshouButton"), true);
    renderSyncFeedback({
      status: "preparing",
      phase: "review_gate",
      steps: defaultSyncSteps("review_gate"),
      ordered_image_count: finalOrder.length,
    });
    try {
      await saveOrder({ quiet: true });
      if (miaoshouImagesAlreadyVerified()) {
        await finalizeAiContentAfterVerifiedSync();
        await load({ quiet: true });
        renderSyncFeedback({
          status: "verified",
          written_image_count: finalOrder.length,
          checks: { images: true, description_images: true },
        });
        $("#miaoshouConfirm").checked = false;
        $("#syncMiaoshouButton").disabled = true;
        toast("妙手图片已验证；最终内容已批准，无需重复写入妙手。");
        return;
      }
      renderSyncFeedback({
        status: "preparing",
        phase: "read_current",
        steps: defaultSyncSteps("read_current"),
        ordered_image_count: finalOrder.length,
      });
      const commit = post("content-package/miaoshou-images/commit", {
        offer_id: currentOfferId(),
        confirm_miaoshou_write: true,
      });
      clearInterval(syncPollTimer);
      syncPollTimer = setInterval(async () => {
        try {
          const latest = await requestJson(
            `${flowApi("preview")}?offer_id=${encodeURIComponent(currentOfferId())}`,
          );
          preview.content_package = latest.content_package;
          renderSyncFeedback(
            latest.content_package?.miaoshou_generated_images_write || null,
          );
        } catch (_error) {
          // The commit request remains authoritative; transient poll failures
          // are surfaced only if the commit itself fails.
        }
      }, 800);
      const result = await commit;
      if (!result.verified) throw new Error("妙手已响应，但图片顺序回读验证未全部通过。");
      renderSyncFeedback(result.sync || {
        status: "verified",
        written_image_count: result.written_image_count,
        checks: result.checks,
      });
      await load({ quiet: true });
      await finalizeAiContentAfterVerifiedSync();
      await load({ quiet: true });
      syncFeedbackOverride = null;
      renderSyncFeedback();
      $("#miaoshouConfirm").checked = false;
      $("#syncMiaoshouButton").disabled = true;
      toast(`已同步 ${result.written_image_count || finalOrder.length} 张图片、通过回读并批准最终内容。`);
    } catch (error) {
      renderSyncFeedback(error.payload?.sync || {
        status: "failed",
        phase: "write_images",
        steps: defaultSyncSteps("write_images"),
        error: error.message,
      });
      showAlert(error.message);
    } finally {
      clearInterval(syncPollTimer);
      syncPollTimer = null;
      syncInFlight = false;
      $("#miaoshouConfirm").disabled = false;
      setLoading($("#syncMiaoshouButton"), false);
      $("#syncMiaoshouButton").disabled = !$("#miaoshouConfirm").checked;
      renderSyncFeedback();
    }
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (sourceOnlyActive()) return;
    const status = preview?.content_package?.remaining_images_generation?.status;
    if (!["queued", "running"].includes(status)) return;
    pollTimer = setTimeout(() => load({ quiet: true }), 3000);
  }

  $("#offerForm").addEventListener("submit", (event) => {
    event.preventDefault();
    load();
  });
  $("#refreshButton").addEventListener("click", () => load());
  $("#saveSourceButton").addEventListener("click", saveSourceReview);
  $("#initializeLocalizationButton").addEventListener("click", initializeImageLocalization);
  $("#initializeLocalizedImagePacksButton").addEventListener("click", initializeLocalizedImageProject);
  $("#savePlanButton").addEventListener("click", () => saveContentReview());
  $("#preparePackageButton").addEventListener("click", preparePackage);
  $("#aiPlanButton").addEventListener("click", requestAiPlan);
  $("#planningProgressAction").addEventListener("click", runPlanningProgressAction);
  $("#preflightButton").addEventListener("click", prepareGeneration);
  $("#paidGenerateButton").addEventListener("click", startPaidGeneration);
  $("#saveVersionsButton").addEventListener("click", saveVersionReview);
  $("#saveOrderButton").addEventListener("click", () => saveOrder());
  $("#miaoshouConfirm").addEventListener("change", (event) => {
    $("#syncMiaoshouButton").disabled = syncInFlight || !event.currentTarget.checked;
  });
  $("#syncMiaoshouButton").addEventListener("click", syncMiaoshou);
  $("#closeDialog").addEventListener("click", () => $("#imageDialog").close());
  $("#imageDialog").addEventListener("click", (event) => {
    if (event.target === $("#imageDialog")) $("#imageDialog").close();
  });

  const params = new URLSearchParams(window.location.search);
  if (params.get("offer_id")) $("#offerId").value = params.get("offer_id");
  load();
})();
