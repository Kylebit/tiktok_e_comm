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

  let currentData = null;

  function money(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed)
      ? new Intl.NumberFormat("zh-CN", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        }).format(parsed)
      : "—";
  }

  function translateBlocker(value) {
    const text = String(value || "").trim();
    return blockerTranslations.get(text) || text;
  }

  function setLoading(loading) {
    $("#lookupForm").classList.toggle("is-loading", loading);
    $("#refreshButton").disabled = loading;
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
      "内容与五图",
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
    $("#workbenchLink").removeAttribute("href");
    $("#workbenchLink").setAttribute("aria-disabled", "true");
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
      { key: "content", label: "内容与五图", ready: contentReady, readyText: "已批准", waitText: "待审核" },
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
      approved ? `${content.image_count} 图已批准` : "待内容审核",
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

  function renderChannels(publication, releaseReady) {
    const drafts = Array.isArray(publication?.drafts) ? publication.drafts : [];
    const grid = $("#channelGrid");
    if (!drafts.length) {
      grid.innerHTML = '<div class="channel-card"><p>商品审批完成后将生成渠道草稿预览。</p></div>';
      return;
    }
    grid.innerHTML = drafts.map((draft) => {
      const contractReady = !(draft.missing_conditions || []).length;
      const ready = releaseReady && contractReady;
      const message = ready
        ? "发布前条件已满足；进入渠道流程后仍需单独确认。"
        : (contractReady
          ? "草稿合同已生成，等待商品审批与妙手同步完成。"
          : (draft.missing_conditions || []).map(translateBlocker).join("；"));
      return `
        <article class="channel-card">
          <header>
            <h3>${esc(channelNames[draft.channel] || draft.channel)}</h3>
            <span class="badge ${ready ? "safe" : "neutral"}">${ready ? "可进入" : "未提交"}</span>
          </header>
          <p>${esc(message)}</p>
        </article>
      `;
    }).join("");
  }

  function render(data) {
    currentData = data;
    const stages = stageModel(data);
    renderProduct(data);
    renderStages(stages);
    renderNextStep(data, stages);
    renderImages(data.content || {});
    renderChannels(data.publication_rehearsal || {}, Boolean(data.actual_release_gate?.ready));

    const offer = data.product?.offer_id || $("#offerId").value.trim();
    $("#workbenchLink").href = `http://127.0.0.1:8766/?offer_id=${encodeURIComponent(offer)}`;
    $("#workbenchLink").removeAttribute("aria-disabled");
  }

  async function load() {
    const offerId = $("#offerId").value.trim();
    const sellerSku = $("#sellerSku").value.trim();
    if (!/^\d{1,32}$/.test(offerId) || !/^\d{1,32}$/.test(sellerSku)) {
      const message = "Offer ID 和 Seller SKU 必须是 1–32 位数字。";
      showError(message);
      renderFailure(message);
      return;
    }
    setLoading(true);
    showError("");
    try {
      const data = await fetchDashboard(offerId, sellerSku);
      render(data);
      const url = new URL(window.location.href);
      url.searchParams.set("offer_id", offerId);
      url.searchParams.set("seller_sku", sellerSku);
      history.replaceState(null, "", url);
    } catch (error) {
      const message = error.message || "商品状态读取失败，请确认本地服务已启动。";
      showError(message);
      renderFailure(message);
    } finally {
      setLoading(false);
    }
  }

  $("#lookupForm").addEventListener("submit", (event) => {
    event.preventDefault();
    load();
  });
  $("#refreshButton").addEventListener("click", load);

  const initial = new URLSearchParams(window.location.search);
  if (initial.get("offer_id")) $("#offerId").value = initial.get("offer_id");
  if (initial.get("seller_sku")) $("#sellerSku").value = initial.get("seller_sku");
  load();
})();
