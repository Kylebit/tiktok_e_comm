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

  function sourceRowsFromDom() {
    return (preview?.review?.image_actions || []).map((row, index) => ({
      ...row,
      action: $(`.source-action[data-index="${index}"]`)?.value || row.action || "review",
      note: $(`.source-note[data-index="${index}"]`)?.value || "",
    }));
  }

  function selectedIdentityReferences() {
    return $$(".identity-reference:checked").map((node) => node.dataset.url);
  }

  function selectedPrimaryReference() {
    return $(".identity-primary:checked")?.dataset.url || "";
  }

  function reviewPayload(overrides = {}) {
    const videoUrl = String(preview?.source?.video?.url || "").trim();
    const review = {
      ...(preview?.review || {}),
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
    return {
      content_strategy: currentContentStrategy(),
      fact_card_approved: $("#factApproved").checked,
      planning_scope_approved: $("#scopeApproved").checked,
      identity_reference_urls: refs,
      primary_identity_url: refs.includes(selectedPrimaryReference())
        ? selectedPrimaryReference()
        : (refs[0] || ""),
      suite_customization: collectRecipeFromDom(),
      asset_decisions: assetDecisionsFromDom(),
    };
  }

  function generatedCurrentRows() {
    return preview?.review?.generated_image_actions || [];
  }

  function buildFinalItems() {
    const sourceItems = (preview?.review?.image_actions || [])
      .filter((row) => row.action === "keep")
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
    const requested = preview?.review?.image_order || [];
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
    const review = preview?.review || {};
    const content = preview?.content_package || {};
    const source = preview?.source || {};
    const generated = content.generated_review_images || [];
    const sourceReviewed = (review.image_actions || []).filter((row) => ["keep", "remove"].includes(row.action)).length;
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

    const baseSteps = [
      ["来源审核", sourceTotal > 0 && sourceReviewed === sourceTotal ? "done" : "current"],
      ["经验配方", content.fact_card_approved && content.planning_scope_approved && content.suite_approved ? "done" : "pending"],
      ["生成版本", workflow.generation_ready ? "done" : (content.remaining_images_generation?.status || "pending")],
      ["最终排序", workflow.image_review_ready && finalOrder.length ? "done" : "pending"],
    ];
    const firstOpen = baseSteps.findIndex((row) => row[1] !== "done");
    $("#flowRail").innerHTML = baseSteps.map(([label, rawStatus], index) => {
      const tone = rawStatus === "done" ? "done" : (index === firstOpen ? "current" : statusTone(rawStatus));
      const note = tone === "done" ? "已完成" : (tone === "current" ? "当前处理" : "等待前序");
      return `<div class="flow-step ${tone}"><span>${tone === "done" ? "✓" : `0${index + 1}`}</span><strong>${esc(label)}</strong><small>${note}</small></div>`;
    }).join("");
  }

  function renderSources() {
    const rows = preview?.review?.image_actions || [];
    const sourceSnapshot = preview?.content_package?.source_snapshot || {};
    const refs = new Set(sourceSnapshot.identity_reference_urls || []);
    const primary = sourceSnapshot.primary_identity_image || "";
    const kept = rows.filter((row) => row.action === "keep").length;
    const removed = rows.filter((row) => row.action === "remove").length;
    const pending = rows.length - kept - removed;
    $("#sourceStats").innerHTML = [
      `全部 ${rows.length}`,
      `保留 ${kept}`,
      `移除 ${removed}`,
      `待决定 ${pending}`,
      `身份参考 ${refs.size}`,
    ].map((text) => `<span>${text}</span>`).join("");
    const grid = $("#sourceGrid");
    grid.classList.remove("skeleton");
    grid.innerHTML = rows.map((row, index) => {
      const url = row.output_url || row.url || "";
      const action = row.action || "review";
      return `
        <article class="asset-card ${action === "keep" ? "kept" : (action === "remove" ? "removed" : "")}">
          <div class="asset-image" data-preview-url="${esc(proxyImage(url))}" data-preview-label="来源图 ${index + 1}">
            <img src="${esc(proxyImage(url))}" alt="来源图 ${index + 1}" loading="lazy">
            <span class="asset-index">${String(index + 1).padStart(2, "0")}</span>
          </div>
          <div class="asset-body">
            <header><strong>${row.kind === "detail" ? "详情图" : "主图"}</strong><small>${esc(action)}</small></header>
            <div class="decision-row">
              <label>图片决定
                <select class="source-action" data-index="${index}">
                  <option value="review" ${action === "review" ? "selected" : ""}>待决定</option>
                  <option value="keep" ${action === "keep" ? "selected" : ""}>保留</option>
                  <option value="remove" ${action === "remove" ? "selected" : ""}>移除</option>
                </select>
              </label>
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

    $$(".source-action").forEach((node) => node.addEventListener("change", () => {
      const card = node.closest(".asset-card");
      card.classList.toggle("kept", node.value === "keep");
      card.classList.toggle("removed", node.value === "remove");
    }));
    $$(".identity-reference").forEach((node) => node.addEventListener("change", () => {
      if (node.checked) {
        const index = node.closest(".asset-card").querySelector(".source-action").dataset.index;
        $(`.source-action[data-index="${index}"]`).value = "keep";
        node.closest(".asset-card").classList.add("kept");
        node.closest(".asset-card").classList.remove("removed");
      } else if ($(".identity-primary:checked")?.dataset.url === node.dataset.url) {
        node.checked = true;
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
        updateStrategyUi();
        renderVersions();
        renderFinal();
      };
    });
    $("#factApproved").checked = Boolean(content.fact_card_approved);
    $("#scopeApproved").checked = Boolean(content.planning_scope_approved);
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
    $("#storyboardGrid").innerHTML = items.length ? items.map((item) => `
      <article class="story-card">
        <header><h3>${esc(item.title_zh || item.title || item.id)}</h3><span class="experience-badge">经验配方 · 自动采用</span></header>
        <p>${esc(item.focus_zh || item.focus || "")}</p>
      </article>
    `).join("") : '<article class="story-card"><p>尚未建立经验配方。先确认商品事实与图片数量，再由 AI 生成本次分镜；分镜无需逐卡审批。</p></article>';
  }

  function updateStrategyUi() {
    const sourceOnly = sourceOnlyActive();
    $("#generationRecipe").classList.toggle("is-disabled", sourceOnly);
    $("#storyboardGrid").classList.toggle("is-disabled", sourceOnly);
    $("#sourceOnlyGenerationNotice").hidden = !sourceOnly;
    $("#scopeApprovalTitle").textContent = sourceOnly
      ? "来源素材范围已确认"
      : "本地生图约束已确认";
    $("#scopeApprovalHint").textContent = sourceOnly
      ? "所有来源图已逐张决定，最终顺序只包含保留的 HTTPS 来源图"
      : "图片数量、类型与类目规则符合本次需求";
    $("#strategyStatus").textContent = sourceOnly
      ? "AI 相关入口已关闭；历史 AI 图片不会进入本次最终图片。"
      : "AI 入口可用；商品事实与配方需确认，经验分镜自动采用，付费生成和成图审核仍由人工决定。";
    $$(".recipe-count, #sizeDimensions, #sizeConfirmed")
      .forEach((node) => { node.disabled = sourceOnly; });
    ["aiPlanButton", "preflightButton", "paidGenerateButton", "saveVersionsButton"]
      .forEach((id) => { $(`#${id}`).disabled = sourceOnly; });
  }

  function versionImageUrl(artifact) {
    return localImage(preview.offer_id, artifact.id);
  }

  function renderVersions() {
    const content = preview?.content_package || {};
    const artifacts = content.artifacts || [];
    const currentByArtifact = new Map(generatedCurrentRows().map((row) => [row.artifact_id, row]));
    const grid = $("#versionGrid");
    grid.classList.remove("skeleton");
    if (sourceOnlyActive()) {
      grid.innerHTML = '<article class="story-card"><p>当前策略仅使用来源图。历史 AI 版本保留但不会显示、审核或混入最终图片。</p></article>';
      $("#generationSummary").innerHTML = "<span>AI 生图已跳过</span><span>付费调用已禁用</span>";
      $("#preflightButton").disabled = true;
      $("#paidGenerateButton").disabled = true;
      $("#saveVersionsButton").disabled = true;
      return;
    }
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
            <label>版本审核
              <select class="asset-decision" data-artifact-id="${esc(artifact.id)}">
                ${["pending", "approved", "rework", "rejected"].map((value) => `<option value="${value}" ${decision === value ? "selected" : ""}>${({pending:"待审核",approved:"通过",rework:"重做",rejected:"拒绝"})[value]}</option>`).join("")}
              </select>
            </label>
            ${current ? `<label>进入最终图片
              <select class="final-action" data-artifact-id="${esc(artifact.id)}">
                <option value="keep" ${current.miaoshou_action === "keep" ? "selected" : ""}>保留</option>
                <option value="remove" ${current.miaoshou_action === "remove" ? "selected" : ""}>移除</option>
              </select>
            </label>` : ""}
            <label>版本备注<textarea class="asset-note" data-artifact-id="${esc(artifact.id)}">${esc(artifact.note || "")}</textarea></label>
            <p>${esc(artifact.task_id || "无任务 ID")}</p>
          </div>
        </article>
      `;
    }).join("") : '<article class="story-card"><p>当前没有生成版本。先保存经验配方并完成生成前检查。</p></article>';

    const generation = content.remaining_images_generation || {};
    const preflight = content.remaining_images_preflight || {};
    const total = Number(preflight.total || preflight.shots?.length || 0);
    const running = ["queued", "running"].includes(generation.status);
    const completed = ["completed_waiting_human_review", "completed_with_errors"].includes(generation.status);
    $("#generationSummary").innerHTML = [
      `已生成 ${content.generated_review_images?.length || 0}`,
      `历史版本 ${artifacts.length}`,
      `任务状态 ${generation.status || "未运行"}`,
      `本次预检 ${total} 张`,
    ].map((text) => `<span>${esc(text)}</span>`).join("");
    $("#paidGenerateButton").disabled = !total || running || completed;
    $("#paidGenerateButton").textContent = running
      ? "生成任务进行中"
      : completed
        ? "本次付费生成已完成"
        : `确认付费并生成${total ? ` ${total} 张` : ""}`;
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
      preview.review.image_order = order;
      renderFinal();
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

  function render() {
    finalOrder = buildFinalItems();
    renderProject();
    renderSources();
    renderStoryboard();
    renderVersions();
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
      preview = loadedPreview;
      if (!syncInFlight) syncFeedbackOverride = null;
      if (!quiet) contentStrategyDraft = null;
      if (recipeDraftOfferId && recipeDraftOfferId !== offerId) clearRecipeDraft();
      finalOrder = buildFinalItems();
      render();
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

  async function saveSourceReview() {
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
      preview = await post("review", {
        offer_id: currentOfferId(),
        review: reviewPayload({
          image_actions: sourceRows,
          ...(sourceOnlyActive() ? { image_order: sourceOrder } : {}),
        }),
      });
      render();
      toast("来源图决定、备注和当前排序已保存到本地。");
    } catch (error) {
      showAlert(error.message);
    } finally {
      setLoading($("#saveSourceButton"), false);
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
    try {
      const result = await post("content-package/prepare", {
        offer_id: currentOfferId(),
        collect_box_id: preview?.content_package?.collect_box_id || currentOfferId(),
      });
      preview.content_package = result.content_package;
      render();
      toast("本地内容审核包已创建；未调用模型或生成图片。");
    } catch (error) {
      showAlert(error.message);
    } finally {
      setLoading($("#preparePackageButton"), false);
    }
  }

  async function requestAiPlan() {
    if (sourceOnlyActive()) {
      showAlert("当前为仅来源图策略，AI 分镜已禁用。");
      return;
    }
    const refs = selectedIdentityReferences();
    if (!refs.length) {
      showAlert("请先选择至少一张身份参考图。");
      return;
    }
    if (!$("#factApproved").checked || !$("#scopeApproved").checked) {
      showAlert("请先确认商品事实和本地生图约束。");
      return;
    }
    const recipeCheck = validateRecipe();
    if (!recipeCheck.valid) {
      showAlert(recipeCheck.message);
      return;
    }
    const recipe = collectRecipeFromDom();
    if (!confirm(`将调用 AI 读取 ${refs.length} 张身份参考图，并严格按场景 ${recipe.type_counts.scene}、卖点 ${recipe.type_counts.selling_point}、尺寸 ${recipe.type_counts.size_card}，共 ${recipeCheck.total} 张规划分镜。会产生模型费用，但不会生成商品图片。确认继续吗？`)) return;
    setLoading($("#aiPlanButton"), true);
    try {
      await saveContentReview({ quiet: true });
      const result = await post("content-package/vision-proposal", {
        offer_id: currentOfferId(),
        reference_urls: refs,
        storyboard_feedback: {},
        confirm_ai_planning: true,
      });
      preview.content_package = result.content_package;
      render();
      toast("AI 已按经验配方完成分镜规划；无需逐卡审核，可继续生成前检查。");
    } catch (error) {
      showAlert(error.message);
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
    try {
      await saveContentReview({ quiet: true });
      const pending = preview?.content_package?.pending_regeneration_shot_ids || [];
      const result = await post("content-package/suite-images-preflight", {
        offer_id: currentOfferId(),
        force_shot_ids: pending,
      });
      preview.content_package = result.content_package;
      render();
      toast("生成前检查已完成；尚未创建付费任务。");
    } catch (error) {
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
    try {
      const result = await post("content-package/remaining-images-generate", {
        offer_id: currentOfferId(),
        confirm_paid_generation: true,
      });
      preview.content_package = result.content_package;
      render();
      toast("付费生成任务已开始，页面会自动刷新进度。");
    } catch (error) {
      showAlert(error.message);
    } finally {
      setLoading($("#paidGenerateButton"), false);
    }
  }

  async function saveVersionReview() {
    if (sourceOnlyActive()) {
      showAlert("当前为仅来源图策略，不审核或混入 AI 版本。");
      return;
    }
    const finalActions = $$(".final-action").map((node) => ({
      artifact_id: node.dataset.artifactId,
      action: node.value,
    }));
    setLoading($("#saveVersionsButton"), true);
    try {
      await saveContentReview({ quiet: true });
      for (const action of finalActions) {
        await post("content-package/generated-image/decision", {
          offer_id: currentOfferId(),
          ...action,
        });
      }
      await load({ quiet: true });
      toast("版本审核与最终图片决定已保存到本地。");
    } catch (error) {
      showAlert(error.message);
    } finally {
      setLoading($("#saveVersionsButton"), false);
    }
  }

  async function saveOrder({ quiet = false } = {}) {
    setLoading($("#saveOrderButton"), true);
    try {
      preview = await post("review", {
        offer_id: currentOfferId(),
        review: reviewPayload({ image_order: finalOrder.map((row) => row.url) }),
      });
      render();
      if (!quiet) toast("最终图片顺序已保存到本地，尚未写入妙手。");
      return preview;
    } catch (error) {
      showAlert(error.message);
      throw error;
    } finally {
      setLoading($("#saveOrderButton"), false);
    }
  }

  async function syncMiaoshou() {
    if (!$("#miaoshouConfirm").checked) return;
    if (!finalOrder.length) {
      showAlert("最终图片为空，不能同步妙手。");
      return;
    }
    if (!confirm(`将按当前顺序把 ${finalOrder.length} 张图片写入妙手公共采集箱并回读验证。此操作不会发布商品。再次确认继续吗？`)) return;
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
      syncFeedbackOverride = null;
      renderSyncFeedback();
      $("#miaoshouConfirm").checked = false;
      $("#syncMiaoshouButton").disabled = true;
      toast(`已同步 ${result.written_image_count || finalOrder.length} 张图片并通过回读验证。`);
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
  $("#savePlanButton").addEventListener("click", () => saveContentReview());
  $("#preparePackageButton").addEventListener("click", preparePackage);
  $("#aiPlanButton").addEventListener("click", requestAiPlan);
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
