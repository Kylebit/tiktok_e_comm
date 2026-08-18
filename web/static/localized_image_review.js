(() => {
  "use strict";
  const params = new URLSearchParams(location.search);
  const offerId = (params.get("offer_id") || "").trim();
  const apiRoot = "/api/product-flow/content-package/localized-image-review";
  const localeNames = {
    "ms-MY": "马来语 · MY",
    "th-TH": "泰语 · TH",
    "vi-VN": "越南语 · VN",
    "es-MX": "西班牙语 · MX",
    "ru-RU": "俄语 · RU",
  };
  let state = null;
  let activeLocale = "th-TH";
  let busy = false;
  let generationPoll = null;

  const $ = (id) => document.getElementById(id);
  const status = (message, error = false) => {
    const node = $("reviewStatus");
    node.textContent = message;
    node.classList.toggle("error", error);
  };
  const request = async (action = "", payload = null) => {
    const url = `${apiRoot}${action}${payload === null ? `?offer_id=${encodeURIComponent(offerId)}` : ""}`;
    const response = await fetch(url, payload === null ? {} : {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({offer_id: offerId, ...payload}),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || body.ok === false) throw new Error(body.error || `HTTP ${response.status}`);
    return body.localized_image_review;
  };
  const withBusy = async (label, work) => {
    if (busy) return;
    busy = true;
    let failureMessage = "";
    document.querySelectorAll("button").forEach((button) => { button.disabled = true; });
    status(label);
    try {
      state = await work();
      render();
    } catch (error) {
      failureMessage = `失败：${error.message || error}`;
    } finally {
      busy = false;
      if (state) render();
      else updateActions();
      if (failureMessage) status(failureMessage, true);
    }
  };
  const project = () => state?.review || {};
  const generationJob = () => state?.generation_job || {};
  const generationIsActive = () => ["QUEUED", "RUNNING"].includes(generationJob().status);
  const visibleTasks = () => (project().tasks || []).filter((task) => task.locale === activeLocale);

  function clearGenerationPoll() {
    if (generationPoll !== null) window.clearTimeout(generationPoll);
    generationPoll = null;
  }

  function scheduleGenerationPoll() {
    clearGenerationPoll();
    if (!generationIsActive()) return;
    generationPoll = window.setTimeout(async () => {
      try {
        state = await request();
        render();
        if (generationIsActive()) scheduleGenerationPoll();
      } catch (error) {
        status(`状态读取失败：${error.message || error}`, true);
        scheduleGenerationPoll();
      }
    }, 2000);
  }

  function updateActions() {
    const review = project();
    $("initializeReview").hidden = Boolean(state?.initialized);
    $("initializeReview").disabled = busy || Boolean(state?.initialized);
    $("closeLightbox").disabled = false;
    $("generateLocalizedImages").disabled = busy || generationIsActive() || !state?.initialized || review.status === "APPROVED";
    $("passVisibleImages").disabled = busy || !visibleTasks().some((task) => task.status === "READY_FOR_REVIEW");
    $("approveLocalizedImages").disabled = busy || !(review.tasks || []).length || !(review.tasks || []).every((task) => task.status === "PASSED");
  }
  function renderTabs() {
    const locales = [...new Set((project().tasks || []).map((task) => task.locale))];
    if (locales.length && !locales.includes(activeLocale)) activeLocale = locales[0];
    $("localeTabs").innerHTML = locales.map((locale) => (
      `<button type="button" data-locale="${locale}" class="${locale === activeLocale ? "active" : ""}">${localeNames[locale] || locale}</button>`
    )).join("");
    $("localeTabs").querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => { activeLocale = button.dataset.locale; render(); });
    });
  }
  function imageMarkup(task) {
    const output = task.local_url
      ? `<img src="${task.local_url}" alt="${task.locale} 第 ${task.position} 张" data-zoom>`
      : `<div class="empty-image">尚未生成<br>需要付费确认</div>`;
    return `<div class="image-pair">
      <div class="image-box"><p>英文原图</p><img src="${task.source_local_url}" alt="英文原图第 ${task.position} 张" data-zoom></div>
      <div class="image-box"><p>${localeNames[task.locale] || task.locale}</p>${output}</div>
    </div>`;
  }
  function renderCards() {
    const rows = visibleTasks();
    $("reviewGrid").innerHTML = rows.length ? rows.map((task) => `
      <article class="review-card ${task.status === "PASSED" ? "passed" : ""}">
        <div class="card-head">
          <div><strong>第 ${task.position} 张</strong><div class="muted">${task.locale}</div></div>
          <span class="badge">${task.status}</span>
        </div>
        ${imageMarkup(task)}
        <div class="card-actions">
          <button type="button" class="retry" data-decision="RETRY" data-task="${task.task_id}" ${!["READY_FOR_REVIEW", "PASSED"].includes(task.status) ? "disabled" : ""}>重新生成</button>
          <button type="button" data-decision="PASS" data-task="${task.task_id}" ${task.status !== "READY_FOR_REVIEW" ? "disabled" : ""}>通过</button>
        </div>
      </article>`).join("") : `<div class="control-panel">尚未建立审核项目。</div>`;
    $("reviewGrid").querySelectorAll("[data-decision]").forEach((button) => {
      button.addEventListener("click", () => decide(button.dataset.task, button.dataset.decision));
    });
    $("reviewGrid").querySelectorAll("[data-zoom]").forEach((image) => {
      image.addEventListener("click", () => {
        $("lightboxImage").src = image.src;
        $("imageLightbox").showModal();
      });
    });
  }
  function render() {
    const review = project();
    const passed = (review.tasks || []).filter((task) => task.status === "PASSED").length;
    $("projectIdentity").textContent = state?.initialized
      ? `Offer ${offerId} · revision ${review.revision} · ${passed}/${(review.tasks || []).length} 已通过 · 当前 ReleasePlan 未修改`
      : `Offer ${offerId || "未提供"} · 尚未建立独立审核项目`;
    $("approvalDigest").textContent = review.approval?.approval_digest || "尚未批准";
    renderTabs();
    renderCards();
    updateActions();
    if (generationIsActive()) {
      status(`生成任务已进入后台：${generationJob().status}。页面会自动刷新，请勿重复点击。`, false);
      return;
    }
    if (["FAILED", "INTERRUPTED"].includes(generationJob().status)) {
      status(generationJob().error || "生成任务中断；再次确认后会从持久化检查点继续。", true);
      return;
    }
    status(review.status === "APPROVED" ? "整套图片已批准。本地发布补充凭据已生成，尚未写入发布计划。" : "项目已加载，可生成或审核。", false);
  }
  async function load() {
    if (!offerId) { status("缺少 offer_id。", true); return; }
    $("backToProduct").href = `/new-product?offer_id=${encodeURIComponent(offerId)}`;
    await withBusy("正在读取独立审核项目…", () => request());
    scheduleGenerationPoll();
  }
  async function decide(taskId, decision) {
    await withBusy(decision === "PASS" ? "正在记录通过决定…" : "正在标记重新生成…", () => request("/decision", {
      expected_revision: project().revision,
      task_id: taskId,
      decision,
    }));
  }

  $("initializeReview").addEventListener("click", () => withBusy("正在锁定第 1、5、6、7 张图片…", () => request("/initialize", {selected_positions: [1, 5, 6, 7]})));
  $("generateLocalizedImages").addEventListener("click", () => {
    if (!$("paidGenerationConfirm").checked) { status("请先勾选 ToAPI 付费确认。", true); return; }
    withBusy("正在调用 ToAPI 生成待处理图片，请勿重复点击…", () => request("/generate", {
      expected_revision: project().revision,
      confirm_paid_generation: true,
    })).then(scheduleGenerationPoll);
  });
  $("passVisibleImages").addEventListener("click", async () => {
    const pending = visibleTasks().filter((task) => task.status === "READY_FOR_REVIEW");
    for (const task of pending) await decide(task.task_id, "PASS");
  });
  $("approveLocalizedImages").addEventListener("click", () => withBusy("正在冻结审核凭据…", () => request("/approve", {
    expected_revision: project().revision,
    approved_by: "Kyle",
  })));
  $("closeLightbox").addEventListener("click", () => $("imageLightbox").close());
  $("imageLightbox").addEventListener("click", (event) => { if (event.target === $("imageLightbox")) $("imageLightbox").close(); });
  load();
})();
