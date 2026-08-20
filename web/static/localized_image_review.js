(() => {
  "use strict";
  const params = new URLSearchParams(location.search);
  const offerId = (params.get("offer_id") || "").trim();
  const apiRoot = "/api/product-flow/content-package/localized-image-review";
  const localeNames = {
    "ms-MY": "马来语 · MY", "th-TH": "泰语 · TH", "vi-VN": "越南语 · VN",
    "es-MX": "西班牙语 · MX", "ru-RU": "俄语 · RU",
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
  const request = async () => {
    const response = await fetch(`${apiRoot}?offer_id=${encodeURIComponent(offerId)}`);
    const body = await response.json().catch(() => ({}));
    if (!response.ok || body.ok === false) throw new Error(body.error || `HTTP ${response.status}`);
    return body.localized_image_review;
  };
  const project = () => state?.review || {};
  const generationJob = () => state?.generation_job || {};
  const generationIsActive = () => ["QUEUED", "RUNNING"].includes(generationJob().status);
  const visibleTasks = () => (project().tasks || []).filter((task) => task.locale === activeLocale);

  function updateActions() {
    $("refreshReview").disabled = busy;
    $("closeLightbox").disabled = false;
  }
  function clearGenerationPoll() {
    if (generationPoll !== null) window.clearTimeout(generationPoll);
    generationPoll = null;
  }
  function scheduleGenerationPoll() {
    clearGenerationPoll();
    if (!generationIsActive()) return;
    generationPoll = window.setTimeout(async () => {
      try { state = await request(); render(); }
      catch (error) { status(`状态读取失败：${error.message || error}`, true); }
      if (generationIsActive()) scheduleGenerationPoll();
    }, 2000);
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
      : "<div class=\"empty-image\">尚未生成<br>由第二轮图片 Skill 继续处理</div>";
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
      </article>`).join("") : "<div class=\"control-panel\">尚未建立审核项目。请在会话中让 Agent 开始第二轮图片 Skill。</div>";
    $("reviewGrid").querySelectorAll("[data-zoom]").forEach((image) => {
      image.addEventListener("click", () => { $("lightboxImage").src = image.src; $("imageLightbox").showModal(); });
    });
  }
  function render() {
    const review = project();
    const passed = (review.tasks || []).filter((task) => task.status === "PASSED").length;
    $("projectIdentity").textContent = state?.initialized
      ? `Offer ${offerId} · revision ${review.revision} · ${passed}/${(review.tasks || []).length} 已生成`
      : `Offer ${offerId || "未提供"} · 尚未建立多语言图片任务`;
    $("approvalDigest").textContent = review.approval?.approval_digest
      || review.approval_intent?.intent_digest || "内部授权尚未记录";
    renderTabs();
    renderCards();
    updateActions();
    if (generationIsActive()) status(`生成任务正在后台执行：${generationJob().status}。页面会自动刷新。`);
    else if (["FAILED", "INTERRUPTED"].includes(generationJob().status)) status(generationJob().error || "生成任务中断；Agent 会根据检查点处理。", true);
    else if (review.status === "APPROVAL_RECORDED") status("会话授权已记录。Agent 会继续完成尚未就绪的图片和技术检查。");
    else if (review.status === "APPROVED") status("会话授权已生效；技术执行状态由 Skill 独立检查。");
    else status("页面仅显示技术结果；所有人工审核统一在商品发布中心完成。");
  }
  async function load() {
    if (!offerId) { status("缺少 offer_id。", true); return; }
    if (busy) return;
    busy = true;
    updateActions();
    status("正在读取执行状态…");
    try { state = await request(); render(); scheduleGenerationPoll(); }
    catch (error) { status(`失败：${error.message || error}`, true); }
    finally { busy = false; updateActions(); }
  }

  $("refreshReview").addEventListener("click", load);
  $("closeLightbox").addEventListener("click", () => $("imageLightbox").close());
  $("imageLightbox").addEventListener("click", (event) => {
    if (event.target === $("imageLightbox")) $("imageLightbox").close();
  });
  $("backToProduct").href = `/new-product?offer_id=${encodeURIComponent(offerId)}`;
  load();
})();
