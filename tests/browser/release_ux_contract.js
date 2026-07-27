"use strict";

const { chromium } = require("playwright");

const baseUrl = process.argv[2];
if (!baseUrl) throw new Error("base URL argument is required");

const results = [];
const failures = [];

function check(condition, message, detail = null) {
  results.push({ ok: Boolean(condition), message, detail });
  if (!condition) failures.push({ message, detail });
}

const productDashboard = {
  ok: true,
  schema_version: "product-workspace-v1",
  mode: "formal_v1",
  safety: {
    simulation_only: true,
    publish_enabled: false,
    external_writes_performed: [],
  },
  product: {
    offer_id: "3828540231",
    seller_sku_candidate: "0952",
    revision: 1,
    title: "离线发布门禁样例商品",
    category: { name: "贴饰 > 墙贴" },
    cost_cny: 9,
    weight_kg: 0.14,
    package_cm: [30, 3, 3],
    selected_sites: ["lh_th"],
    selected_sku_keys: ["30x90"],
    source_skus: [{ key: "30x90", label: "30 × 90 cm", price_cny: 9 }],
    fields_locked: false,
    actual_product_approved: false,
    fact_evidence: {
      ready: true,
      blockers: [],
      warnings: [
        "cost_cny does not match the selected SKU price: 9 CNY vs 8.1 CNY",
      ],
    },
    seller_sku_governance: {
      available: true,
      candidate: "0952",
      source: "catalog_gap",
    },
    thumbnail: { url: "", approved: false },
  },
  content: {
    approved: false,
    image_count: 0,
    images: [],
    blockers: ["content approval required"],
  },
  approval: {
    ready: true,
    blockers: [],
    warnings: [
      "当前商品标题仍含中文或缺少英文字母；可以先锁定事实，但发布前建议采用平台标题候选",
      "cost_cny does not match the selected SKU price: 9 CNY vs 8.1 CNY",
    ],
    state_patch_preview: {
      product_approval: { input_fingerprint: "sha256:offline-product" },
    },
  },
  actual_release_gate: {
    ready: false,
    blockers: ["content approval required", "product approval required"],
  },
  publication_scope: {
    selected_labels: ["miaoshou:COMMON"],
    default_labels: ["miaoshou:COMMON"],
    available_targets: [],
  },
  pricing_review: {
    status: "blocked",
    selected_store_prices: [],
    all_legacy_store_prices: [],
    blockers: ["product approval required"],
  },
  omnichannel_preview: {
    available: false,
    targets: [],
    blockers: ["product approval required"],
  },
  publication_rehearsal: { ready: false, listings: [], blockers: [] },
  release_v1: {
    eligible_for_plan_approval: false,
    plan_approved: false,
    miaoshou_prepared: false,
    blockers: ["content approval required"],
    plan: null,
    run: null,
  },
};

const aiPreview = {
  ok: true,
  mode: "first_review_no_model_call",
  offer_id: "3828540231",
  source: {
    offer_id: "3828540231",
    source_url: "https://detail.1688.com/offer/123456.html",
    source_item_code: "123456",
    title_source: "离线图片审核样例商品",
    skus: [{ key: "30x90", name: "30 × 90 cm" }],
    images: [],
    video: { url: "", action: "remove" },
  },
  review: {
    title: "离线图片审核样例商品",
    image_actions: [],
    image_order: [],
    video_action: "remove",
  },
  content_package: {
    package_found: true,
    collect_box_id: "3828540231",
    content_strategy: "ai_assisted",
    fact_card_approved: true,
    planning_scope_approved: true,
    suite_approved: true,
    planning_review_mode: "experience_recipe_auto_v1",
    source_snapshot: {
      image_urls: [],
      identity_reference_urls: [],
      primary_identity_image: "",
    },
    suite_customization: {
      type_counts: { scene: 1, selling_point: 1, size_card: 0 },
      size_card: { dimensions: "", confirmed: false },
    },
    suite: { items: [] },
    generated_review_images: [],
    artifacts: [],
    model_proposal: {
      available: true,
      valid: true,
      status: "auto_adopted_experience_recipe",
    },
    remaining_images_preflight: {
      status: "ready_for_explicit_paid_confirmation",
      total: 2,
      shots: [{ id: "sc1" }, { id: "sp1" }],
    },
    remaining_images_generation: { status: "not_started" },
    miaoshou_generated_images_write: { status: "not_started" },
  },
  workflow: {
    current_label: "来源图审核",
    generation_ready: false,
    image_review_ready: false,
  },
};

const orbitNavigation = {
  ok: true,
  navigation: [
    { key: "new-product", label: "自动上品", href: "/product-workspace", level: "focus" },
    { key: "profit", label: "利润中心", href: "/profit", level: "focus" },
    { key: "overview", label: "总览", href: "/", level: "primary" },
    { key: "product", label: "商品运营", href: "/?view=product", level: "primary" },
    { key: "content", label: "内容运营", href: "/?view=content", level: "primary" },
    { key: "channel", label: "渠道运营", href: "/?view=channel", level: "primary" },
    { key: "supply-chain", label: "供应链运营", href: "/?view=supply-chain", level: "primary" },
    { key: "data", label: "数据运营", href: "/?view=data", level: "primary" },
    { key: "tasks", label: "任务", href: "/?view=tasks", level: "secondary" },
  ],
  workspaces: [
    {
      key: "product",
      label: "商品运营",
      description: "商品主数据、SKU 与上品审批",
      availability: "active",
      links: [
        { label: "商品发布中心", href: "/product-workspace", description: "正式发布流程" },
      ],
    },
    {
      key: "content",
      label: "内容运营",
      description: "图片与内容包",
      availability: "active",
      links: [
        { label: "AI 图片工作室", href: "/ai-image-studio", description: "图片审核与生成" },
      ],
    },
    {
      key: "channel",
      label: "渠道运营",
      description: "多渠道发布",
      availability: "active",
      links: [],
    },
    {
      key: "supply-chain",
      label: "供应链运营",
      description: "库存与采购",
      availability: "planned",
      links: [],
    },
    {
      key: "data",
      label: "数据运营",
      description: "周报与 SKU 利润",
      availability: "active",
      links: [
        { label: "利润中心", href: "/profit", description: "周报与 SKU 查询" },
      ],
    },
  ],
  internal_tools: [],
};

function jsonResponse(payload, status = 200) {
  return {
    status,
    contentType: "application/json; charset=utf-8",
    body: JSON.stringify(payload),
  };
}

function apiFixture(url, method, state) {
  const path = url.pathname;
  if (path === "/api/orbit/navigation") return jsonResponse(orbitNavigation);
  if (path === "/api/status") {
    return jsonResponse({
      ok: true,
      pending_titles: 0,
      pending_images: 0,
      pending_mx: 0,
      pending_uk: 0,
      warnings: [],
    });
  }
  if (path === "/api/orbit/inbox") return jsonResponse({ ok: true, items: [] });
  if (path === "/api/product-workspace/dashboard") {
    return jsonResponse(productDashboard);
  }
  if (path === "/api/product-flow/preview") return jsonResponse(aiPreview);
  if (path === "/api/profit-center/weekly") {
    if (state.delayWeekly) return null;
    return jsonResponse({
      ok: true,
      summary: { available: false, status: "not_generated" },
      external_writes_performed: [],
    });
  }
  if (path === "/api/sku-profit") {
    if (state.delaySku) return null;
    return jsonResponse({ ok: false, error: "离线样例：没有可审计样本" }, 422);
  }
  return jsonResponse({ ok: false, error: `unhandled offline API ${method} ${path}` }, 404);
}

async function computedVisibility(page, selector) {
  return page.locator(selector).evaluate((element) => {
    let node = element;
    while (node && node instanceof Element) {
      const style = getComputedStyle(node);
      if (
        style.display === "none"
        || style.visibility === "hidden"
        || style.visibility === "collapse"
        || Number(style.opacity) === 0
      ) return false;
      node = node.parentElement;
    }
    const rect = element.getBoundingClientRect();
    return (
      element.getClientRects().length > 0
      && rect.width > 0
      && rect.height > 0
    );
  });
}

function unexpectedInteractionErrors(errors) {
  return errors.filter((message) => (
    !message.includes("Failed to load resource: the server responded with a status of")
  ));
}

async function overflowAudit(page) {
  return page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const pageOverflow = document.documentElement.scrollWidth - width;
    const offenders = [...document.querySelectorAll("body *")].flatMap((element) => {
      const style = getComputedStyle(element);
      if (
        style.display === "none"
        || style.visibility === "hidden"
        || element.getClientRects().length === 0
        || style.position === "fixed"
      ) return [];
      const rect = element.getBoundingClientRect();
      if (rect.right <= width + 2 && rect.left >= -2) return [];
      if (rect.width > width * 4 && ["pre", "code"].includes(element.localName)) return [];
      return [{
        tag: element.localName,
        id: element.id,
        className: String(element.className || "").slice(0, 120),
        left: Math.round(rect.left),
        right: Math.round(rect.right),
        width: Math.round(rect.width),
      }];
    }).slice(0, 8);
    return { pageOverflow, offenders };
  });
}

async function installApiRoutes(page, state, requests) {
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== baseUrl) {
      requests.push({ method: request.method(), url: request.url(), external: true });
      return route.abort("blockedbyclient");
    }
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ method: request.method(), url: request.url(), external: false });
    const fixture = apiFixture(url, request.method(), state);
    if (fixture === null) {
      const kind = url.pathname.includes("weekly") ? "weekly" : "sku";
      state.pending[kind] = route;
      return;
    }
    return route.fulfill(fixture);
  });
}

async function openScenario(browser, path, viewport) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  const state = { delayWeekly: false, delaySku: false, pending: {} };
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  await installApiRoutes(page, state, requests);
  await page.goto(`${baseUrl}${path}`, { waitUntil: "networkidle" });
  return { context, page, errors, requests, state };
}

async function auditPage(browser, definition, viewport) {
  const scenario = await openScenario(browser, definition.path, viewport);
  const { page, context, errors, requests } = scenario;
  try {
    for (const selector of definition.nextActions) {
      const count = await page.locator(selector).count();
      check(count === 1, `${definition.name}: next-action selector exists: ${selector}`, count);
      if (count === 1) {
        const visible = await computedVisibility(page, selector);
        const text = (await page.locator(selector).innerText()).trim();
        check(visible, `${definition.name}: computed next action is visible: ${selector}`);
        check(Boolean(text), `${definition.name}: next action is non-empty: ${selector}`, text);
      }
    }
    const overflow = await overflowAudit(page);
    check(
      overflow.pageOverflow <= 2,
      `${definition.name}: no ${viewport.width}px page overflow`,
      overflow,
    );
    check(errors.length === 0, `${definition.name}: no console/page errors`, errors);
    const initialPosts = requests.filter((row) => row.method === "POST");
    const external = requests.filter((row) => row.external);
    check(initialPosts.length === 0, `${definition.name}: initial POST budget is zero`, initialPosts);
    check(external.length === 0, `${definition.name}: external network budget is zero`, external);
  } finally {
    await context.close();
  }
}

async function productAsyncFeedback(browser) {
  const scenario = await openScenario(
    browser,
    "/product-workspace?offer_id=3828540231",
    { width: 1440, height: 900 },
  );
  const { page, context, errors, state } = scenario;
  try {
    await page.waitForSelector("#productFactsForm[data-locked='false']");
    check(
      await page.locator("#approvalButton").isEnabled(),
      "product: Chinese title and reviewed cost mismatch remain warnings and do not disable approval",
    );
    const approvalMessage = (await page.locator("#approvalMessage").innerText()).trim();
    check(
      approvalMessage.includes("可以批准并锁定")
      && approvalMessage.includes("采购成本与已选规格价格不一致"),
      "product: approval warnings are visible beside the enabled lock action",
      approvalMessage,
    );
    await page.route("**/api/product-workspace/title-draft", (route) => {
      state.pending.titleDraft = route;
    });
    await page.locator("#generateTitleDraftButton").click();
    await page.waitForFunction(() => document.querySelector("#generateTitleDraftButton")?.classList.contains("is-loading"));
    check(
      await computedVisibility(page, "#titleDraftStatus"),
      "product: title model action exposes visible progress feedback",
    );
    const listingCopy = {
      status: "draft_pending_kyle_review",
      semantic_master_en: "Watercolour Floral Butterfly Wall Decal",
      candidates: [
        {
          channel: "tiktok",
          site: "PH",
          language: "English",
          limit: 255,
          title: "Watercolour Floral Butterfly Wall Decal",
        },
        {
          channel: "ozon",
          site: "RU",
          language: "Russian",
          limit: 200,
          title: "Наклейка на стену с цветами и бабочками",
        },
      ],
      input_signature: "sha256:offline-title-fixture",
      policy_version: "listing-title-candidates-v2",
      provider: "toapi",
      model: "gpt-5.4-mini-official",
    };
    await state.pending.titleDraft.fulfill(
      jsonResponse({
        ok: true,
        marketplace_writes_performed: [],
        dashboard: { ...productDashboard, listing_copy: listingCopy },
      }),
    );
    await page.waitForFunction(() => !document.querySelector("#generateTitleDraftButton")?.classList.contains("is-loading"));
    check(
      (await page.locator("#titleCandidateGrid").innerText()).includes("Watercolour Floral"),
      "product: generated platform title candidates are visible",
    );
    check(
      (await page.locator("#factsEditTitle").inputValue()).includes("Watercolour Floral"),
      "product: semantic English master is adopted into the editable fact field",
    );
    await page.route("**/api/product-workspace/facts", (route) => {
      state.pending.productFacts = route;
    });
    await page.locator("#factsEditTitle").fill("Kyle 修改后的离线商品标题");
    await page.locator("#productFactsForm").evaluate((form) => form.requestSubmit());
    await page.waitForFunction(() => document.querySelector("#productFactsForm")?.classList.contains("is-submitting"));
    check(
      await computedVisibility(page, "#factsEditMessage"),
      "product: facts save exposes computed loading/progress feedback",
    );
    await state.pending.productFacts.fulfill(
      jsonResponse({ ok: false, error: "离线冲突：revision 已变化" }, 409),
    );
    await page.waitForFunction(() => !document.querySelector("#productFactsForm")?.classList.contains("is-submitting"));
    const message = (await page.locator("#factsEditMessage").innerText()).trim();
    check(Boolean(message), "product: facts save failure is visible", message);
    check(
      !(await page.locator("#factsEditMessage").evaluate((node) => node.textContent.includes("已保存 revision"))),
      "product: failed facts save does not show success",
      message,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      "product interaction: no unexpected console/page errors",
      errors,
    );
  } finally {
    await context.close();
  }
}

async function productLockedTitleAdoption(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  let adoptionRequest = null;
  let confirmation = "";
  const englishMaster = "Cute Bear PVC Wall Sticker, 3-Piece 30 x 40 cm";
  const lockedDashboard = JSON.parse(JSON.stringify(productDashboard));
  lockedDashboard.product.revision = 15;
  lockedDashboard.product.title = "小熊躲猫猫墙贴";
  lockedDashboard.product.fields_locked = true;
  lockedDashboard.product.actual_product_approved = true;
  lockedDashboard.content = {
    approved: true,
    image_count: 6,
    images: [],
    blockers: [],
  };
  lockedDashboard.listing_copy = {
    status: "draft_pending_kyle_review",
    semantic_master_en: englishMaster,
    candidates: [],
    input_signature: "sha256:locked-current-facts",
    policy_version: "listing-copy-candidates-v4",
    model: "gpt-5.4-mini-official",
  };
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  page.on("dialog", async (dialog) => {
    confirmation = dialog.message();
    await dialog.accept();
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== baseUrl) return route.abort("blockedbyclient");
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ method: request.method(), url: request.url() });
    if (url.pathname === "/api/product-workspace/dashboard") {
      return route.fulfill(jsonResponse(lockedDashboard));
    }
    if (
      url.pathname === "/api/product-workspace/title-adopt"
      && request.method() === "POST"
    ) {
      adoptionRequest = request.postDataJSON();
      const updated = JSON.parse(JSON.stringify(lockedDashboard));
      updated.product.revision = 16;
      updated.product.title = englishMaster;
      updated.product.fields_locked = false;
      updated.product.actual_product_approved = false;
      updated.listing_copy.status = "adopted_in_product_facts";
      return route.fulfill(jsonResponse({
        ok: true,
        revision: 16,
        external_writes_performed: [],
        dashboard: updated,
      }));
    }
    const fixture = apiFixture(
      url,
      request.method(),
      { delayWeekly: false, delaySku: false, pending: {} },
    );
    return route.fulfill(fixture || jsonResponse({ ok: false }, 404));
  });
  try {
    await page.goto(`${baseUrl}/product-workspace?offer_id=3828540231`, {
      waitUntil: "networkidle",
    });
    const adopt = page.locator(".adopt-title-candidate");
    check(
      await adopt.count() === 1
      && (await adopt.innerText()).includes("废止旧审批"),
      "product: locked EN MASTER exposes one explicit supersession action",
    );
    await adopt.click();
    await page.waitForFunction(
      () => document.querySelector("#productFactsForm")?.dataset.locked === "false",
    );
    check(
      confirmation.includes("废止当前商品审批与旧 ReleasePlan")
      && confirmation.includes("不会写妙手或任何渠道"),
      "product: title adoption confirmation states destructive local scope and external-write boundary",
      confirmation,
    );
    check(
      adoptionRequest
      && adoptionRequest.user_approved === true
      && adoptionRequest.approved_by === "Kyle"
      && adoptionRequest.expected_revision === 15
      && adoptionRequest.candidate_title === englishMaster
      && adoptionRequest.input_signature === "sha256:locked-current-facts",
      "product: locked title adoption submits exact Kyle approval, revision, candidate and signature",
      adoptionRequest,
    );
    check(
      await page.locator("#factsEditTitle").inputValue() === englishMaster
      && await page.locator("#factsEditTitle").isEnabled(),
      "product: successful adoption shows the English title in unlocked product facts",
    );
    const status = (await page.locator("#titleDraftStatus").innerText()).trim();
    check(
      status.includes("旧商品审批、旧发布计划及未完成运行已废止")
      && status.includes("重新批准锁定"),
      "product: adoption success explains supersession and required re-approval",
      status,
    );
    check(
      errors.length === 0,
      "product title adoption: no console/page errors",
      errors,
    );
    check(
      requests.filter((row) => row.method === "POST").length === 1,
      "product title adoption: exactly one local transition POST",
      requests,
    );
  } finally {
    await context.close();
  }
}

async function productPreservedTitleApprovalReload(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  const englishMaster = "Cute Bear PVC Wall Sticker, 3-Piece 30 x 40 cm";
  const preservedDashboard = JSON.parse(JSON.stringify(productDashboard));
  preservedDashboard.product.revision = 31;
  preservedDashboard.product.title = englishMaster;
  preservedDashboard.product.fields_locked = true;
  preservedDashboard.product.actual_product_approved = true;
  preservedDashboard.content = {
    approved: true,
    image_count: 6,
    images: [],
    blockers: [],
  };
  preservedDashboard.listing_copy = {
    status: "adopted_in_product_facts",
    semantic_master_en: englishMaster,
    candidates: [],
    input_signature: "sha256:same-title-reaffirmed",
    current_input_signature: "sha256:same-title-reaffirmed",
    policy_version: "listing-copy-candidates-v4",
    model: "gpt-5.4-mini-official",
    superseded_release_plan_id: "omnichannel:prior-plan",
  };
  preservedDashboard.release_v1 = {
    eligible_for_plan_approval: false,
    plan_persisted: true,
    plan_approved: true,
    historical: false,
    miaoshou_prepared: false,
    publish_ready: false,
    blockers: [],
    adapter_blockers: [],
    run: null,
    plan: {
      plan_id: "omnichannel:revision-31-current",
      status: "APPROVED",
      payload: {
        product_revision: 31,
        content_package_id: "content:fixture:r31",
      },
    },
  };
  const titleChangedDashboard = JSON.parse(JSON.stringify(preservedDashboard));
  titleChangedDashboard.product.revision = 32;
  titleChangedDashboard.product.fields_locked = false;
  titleChangedDashboard.product.actual_product_approved = false;
  titleChangedDashboard.listing_copy.product_approval_preserved = false;
  titleChangedDashboard.release_v1 = {
    eligible_for_plan_approval: false,
    plan_persisted: false,
    plan_approved: false,
    miaoshou_prepared: false,
    publish_ready: false,
    blockers: ["product approval required"],
    adapter_blockers: [],
    run: null,
    plan: null,
  };
  let activeDashboard = preservedDashboard;
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== baseUrl) return route.abort("blockedbyclient");
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ method: request.method(), url: request.url() });
    if (url.pathname === "/api/product-workspace/dashboard") {
      return route.fulfill(jsonResponse(activeDashboard));
    }
    const fixture = apiFixture(
      url,
      request.method(),
      { delayWeekly: false, delaySku: false, pending: {} },
    );
    return route.fulfill(fixture || jsonResponse({ ok: false }, 404));
  });
  try {
    await page.goto(`${baseUrl}/product-workspace?offer_id=3828540231`, {
      waitUntil: "networkidle",
    });
    const status = (await page.locator("#titleDraftStatus").innerText()).trim();
    check(
      status.includes("EN MASTER 已采用且当前商品审批 / 事实锁有效")
      && status.includes("omnichannel:revision-31-current 已批准")
      && status.includes("绑定 revision 31"),
      "product: approved adopted-title reload shows the current approval, lock and bound plan",
      status,
    );
    check(
      !status.includes("旧 ReleasePlan 已废止")
      && !status.includes("旧审批与旧发布计划已废止"),
      "product: current approved ReleasePlan is not described as superseded",
      status,
    );
    check(
      await page.locator("#productFactsForm").getAttribute("data-locked") === "true",
      "product: same-title reaffirm reload keeps the approved facts locked",
    );
    check(
      preservedDashboard.listing_copy.superseded_release_plan_id
        === "omnichannel:prior-plan",
      "product: same-title reaffirm keeps the superseded ReleasePlan audit link",
    );
    activeDashboard = titleChangedDashboard;
    await page.reload({ waitUntil: "networkidle" });
    const changedStatus = (
      await page.locator("#titleDraftStatus").innerText()
    ).trim();
    check(
      changedStatus.includes("旧审批与旧发布计划已废止")
      && changedStatus.includes("等待重新核对并批准")
      && !changedStatus.includes("当前商品审批 / 事实锁有效"),
      "product: title-changed reload keeps the superseded approval and plan semantics",
      changedStatus,
    );
    check(
      errors.length === 0,
      "product preserved title reload: no console/page errors",
      errors,
    );
    check(
      requests.filter((row) => row.method === "POST").length === 0,
      "product preserved title reload: DOM fixture performs zero writes",
      requests,
    );
  } finally {
    await context.close();
  }
}

async function productLockedStaleTitleRefresh(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  let refreshRequest = null;
  let confirmation = "";
  const staleDashboard = JSON.parse(JSON.stringify(productDashboard));
  staleDashboard.product.revision = 20;
  staleDashboard.product.fields_locked = true;
  staleDashboard.product.actual_product_approved = true;
  staleDashboard.listing_copy = {
    status: "superseded_product_facts_changed",
    semantic_master_en: "Stale English Master",
    candidates: [],
    input_signature: "sha256:legacy-mutable-source-summary",
    policy_version: "listing-copy-candidates-v4",
    model: "gpt-5.4-mini-official",
  };
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  page.on("dialog", async (dialog) => {
    confirmation = dialog.message();
    await dialog.accept();
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== baseUrl) return route.abort("blockedbyclient");
    if (!url.pathname.startsWith("/api/")) return route.continue();
    if (url.pathname === "/api/product-workspace/dashboard") {
      return route.fulfill(jsonResponse(staleDashboard));
    }
    if (
      url.pathname === "/api/product-workspace/title-draft"
      && request.method() === "POST"
    ) {
      refreshRequest = request.postDataJSON();
      const updated = JSON.parse(JSON.stringify(staleDashboard));
      updated.product.revision = 21;
      updated.listing_copy = {
        status: "draft_pending_kyle_review",
        semantic_master_en: "Fresh English Master for Approved Product Facts",
        candidates: [],
        input_signature: "sha256:stable-approved-facts",
        fact_snapshot: { offer_id: "3828540231" },
        model_input_signature: "sha256:audited-model-input",
        policy_version: "listing-copy-candidates-v4",
        model: "gpt-5.4-mini-official",
      };
      return route.fulfill(jsonResponse({
        ok: true,
        revision: 21,
        locked_stale_refresh: true,
        superseded_release_plan_id: "omnichannel:old-plan",
        marketplace_writes_performed: [],
        dashboard: updated,
      }));
    }
    const fixture = apiFixture(
      url,
      request.method(),
      { delayWeekly: false, delaySku: false, pending: {} },
    );
    return route.fulfill(fixture || jsonResponse({ ok: false }, 404));
  });
  try {
    await page.goto(`${baseUrl}/product-workspace?offer_id=3828540231`, {
      waitUntil: "networkidle",
    });
    const refresh = page.locator("#generateTitleDraftButton");
    check(
      await refresh.isEnabled(),
      "product: locked stale title exposes an explicit refresh action",
    );
    await refresh.click();
    await page.waitForFunction(
      () => document.querySelector(".adopt-title-candidate")?.disabled === false,
    );
    check(
      confirmation.includes("废止旧 ReleasePlan")
      && confirmation.includes("不会修改已批准商品事实")
      && confirmation.includes("不会写妙手或渠道"),
      "product: locked stale refresh explains local supersession and no external write",
      confirmation,
    );
    check(
      refreshRequest
      && refreshRequest.expected_revision === 20
      && refreshRequest.refresh_stale_locked_candidate === true
      && refreshRequest.user_approved === true
      && refreshRequest.approved_by === "Kyle",
      "product: locked stale refresh submits exact revision and Kyle approval",
      refreshRequest,
    );
    check(
      errors.length === 0,
      "product locked stale title refresh: no console/page errors",
      errors,
    );
  } finally {
    await context.close();
  }
}

async function productMultiTabTitleRefreshConflict(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const firstTab = await context.newPage();
  const staleTab = await context.newPage();
  const errors = [];
  const requests = [];
  let pendingFirst = null;
  let pendingStale = null;
  let modelCalls = 0;
  const initialDashboard = JSON.parse(JSON.stringify(productDashboard));
  initialDashboard.product.revision = 27;
  initialDashboard.product.fields_locked = true;
  initialDashboard.product.actual_product_approved = true;
  initialDashboard.listing_copy = {
    status: "superseded_product_facts_changed",
    semantic_master_en: "Stale English Master",
    candidates: [],
    input_signature: "sha256:68e84-previous",
    policy_version: "listing-copy-candidates-v4",
    model: "gpt-5.4-mini-official",
  };
  let liveDashboard = initialDashboard;
  const latestDashboard = JSON.parse(JSON.stringify(initialDashboard));
  latestDashboard.product.revision = 28;
  latestDashboard.listing_copy = {
    status: "draft_pending_kyle_review",
    semantic_master_en: "Bear Peekaboo PVC Wall Sticker for Kids Room Decor",
    candidates: [],
    input_signature: "sha256:999a44-current",
    fact_snapshot: { offer_id: "3828540231" },
    model_input_signature: "sha256:current-model-input",
    policy_version: "listing-copy-candidates-v4",
    model: "gpt-5.4-mini-official",
    refreshed_while_product_locked: true,
    superseded_release_plan_id: "omnichannel:revision-27-plan",
  };

  const attach = async (page, tabName) => {
    page.on("pageerror", (error) => errors.push(`${tabName} pageerror: ${error.message}`));
    page.on("console", (message) => {
      if (message.type() === "error") {
        errors.push(`${tabName} console: ${message.text()}`);
      }
    });
    page.on("dialog", async (dialog) => dialog.accept());
    await page.route("**/*", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== baseUrl) return route.abort("blockedbyclient");
      if (!url.pathname.startsWith("/api/")) return route.continue();
      requests.push({ tab: tabName, method: request.method(), url: request.url() });
      if (url.pathname === "/api/product-workspace/dashboard") {
        return route.fulfill(jsonResponse(liveDashboard));
      }
      if (
        url.pathname === "/api/product-workspace/title-draft"
        && request.method() === "POST"
      ) {
        const body = request.postDataJSON();
        if (body.expected_revision === liveDashboard.product.revision) {
          pendingFirst = { route, body };
        } else {
          pendingStale = { route, body };
        }
        return;
      }
      const fixture = apiFixture(
        url,
        request.method(),
        { delayWeekly: false, delaySku: false, pending: {} },
      );
      return route.fulfill(fixture || jsonResponse({ ok: false }, 404));
    });
  };

  await attach(firstTab, "first");
  await attach(staleTab, "stale");
  try {
    await Promise.all([
      firstTab.goto(`${baseUrl}/product-workspace?offer_id=3828540231`, {
        waitUntil: "networkidle",
      }),
      staleTab.goto(`${baseUrl}/product-workspace?offer_id=3828540231`, {
        waitUntil: "networkidle",
      }),
    ]);

    await firstTab.locator("#generateTitleDraftButton").click();
    await firstTab.waitForFunction(
      () => document.querySelector("#generateTitleDraftButton")?.classList.contains("is-loading"),
    );
    check(
      (await firstTab.locator("#titleDraftStatus").innerText()).includes("ToAPI 正在"),
      "product multi-tab: winning tab exposes pending ToAPI feedback",
    );
    check(
      pendingFirst?.body.expected_revision === 27,
      "product multi-tab: winning tab submits its current dashboard revision",
      pendingFirst?.body,
    );
    modelCalls += 1;
    liveDashboard = latestDashboard;
    await pendingFirst.route.fulfill(jsonResponse({
      ok: true,
      revision: 28,
      locked_stale_refresh: true,
      superseded_release_plan_id: "omnichannel:revision-27-plan",
      marketplace_writes_performed: [],
      dashboard: latestDashboard,
    }));
    await firstTab.waitForFunction(
      () => document.querySelector("#factsEditRevision")?.textContent.includes("28"),
    );
    check(
      (await firstTab.locator("#titleCandidateGrid").innerText()).includes(
        "Bear Peekaboo PVC Wall Sticker",
      ),
      "product multi-tab: winning tab renders the generated title",
    );

    await staleTab.locator("#generateTitleDraftButton").click();
    await staleTab.waitForFunction(
      () => document.querySelector("#generateTitleDraftButton")?.classList.contains("is-loading"),
    );
    check(
      (await staleTab.locator("#titleDraftStatus").innerText()).includes("ToAPI 正在"),
      "product multi-tab: stale tab exposes pending feedback before CAS response",
    );
    check(
      pendingStale?.body.expected_revision === 27,
      "product multi-tab: stale tab keeps its original revision for CAS",
      pendingStale?.body,
    );
    await pendingStale.route.fulfill(jsonResponse({
      ok: false,
      error: "state revision is stale",
      current_revision: 28,
      marketplace_writes_performed: [],
    }, 409));
    await staleTab.waitForFunction(
      () => document.querySelector("#titleDraftStatus")?.textContent.includes("已自动刷新最新标题状态"),
    );
    const staleStatus = (await staleTab.locator("#titleDraftStatus").innerText()).trim();
    check(
      staleStatus.includes("另一窗口已将商品更新到 revision 28")
      && staleStatus.includes("CAS 安全拒绝")
      && !staleStatus.includes("标题生成失败"),
      "product multi-tab: stale CAS is explained as another-window update, not ToAPI failure",
      staleStatus,
    );
    check(
      (await staleTab.locator("#factsEditRevision").innerText()).includes("28")
      && (await staleTab.locator("#titleCandidateGrid").innerText()).includes(
        "Bear Peekaboo PVC Wall Sticker",
      ),
      "product multi-tab: stale tab automatically reloads revision 28 and its current title",
    );
    check(
      await staleTab.locator("#productFactsForm").getAttribute("data-locked") === "true",
      "product multi-tab: automatic reload preserves product approval lock",
    );
    check(
      liveDashboard.listing_copy.superseded_release_plan_id
        === "omnichannel:revision-27-plan",
      "product multi-tab: automatic reload keeps the superseded ReleasePlan link",
    );
    check(
      modelCalls === 1,
      "product multi-tab: stale tab is rejected before a second model call",
      modelCalls,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      "product multi-tab title refresh: no console/page errors",
      errors,
    );
  } finally {
    await context.close();
  }
}

async function productReleaseTerminalState(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== baseUrl) return route.abort("blockedbyclient");
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ method: request.method(), url: request.url() });
    if (url.pathname === "/api/product-workspace/dashboard") {
      const dashboard = JSON.parse(JSON.stringify(productDashboard));
      const targetLabels = ["miaoshou:COMMON", "tiktok:MX", "ozon:RU"];
      dashboard.product.seller_sku_candidate = "0953";
      dashboard.product.actual_product_approved = true;
      dashboard.content = {
        approved: true,
        image_count: 5,
        images: [],
        blockers: [],
      };
      dashboard.approval = {
        ready: true,
        blockers: [],
        warnings: [],
        state_patch_preview: {
          product_approval: { input_fingerprint: "sha256:offline-terminal" },
        },
      };
      dashboard.actual_release_gate = { ready: true, blockers: [] };
      dashboard.publication_scope = {
        selected_labels: targetLabels,
        default_labels: targetLabels,
        available_targets: [
          { label: "miaoshou:COMMON", channel: "miaoshou", country: "COMMON" },
          { label: "tiktok:MX", channel: "tiktok", shop: "LivelyHive", country: "MX" },
          { label: "ozon:RU", channel: "ozon", country: "RU" },
        ],
      };
      dashboard.release_v1 = {
        eligible_for_plan_approval: false,
        plan_persisted: true,
        plan_approved: true,
        miaoshou_prepared: true,
        publish_ready: true,
        blockers: [],
        adapter_blockers: [],
        plan: {
          plan_id: "omnichannel:offline-terminal",
          confirmation_token: "PUBLISH-OFFLINE-TERMINAL",
          targets: targetLabels,
          payload: {
            product_revision: 1,
            content_package_id: "content:offline",
            targets: targetLabels,
          },
        },
        run: {
          run_id: "release-run:offline-terminal",
          status: "AWAITING_MANUAL_VERIFICATION",
          targets: [
            {
              target_label: "miaoshou:COMMON",
              status: "SUCCEEDED",
              attempts: 1,
              external_id: "3828811808",
              error: null,
            },
            {
              target_label: "tiktok:MX",
              status: "SUBMITTED_UNVERIFIED",
              attempts: 2,
              external_id: "3224868435:16265910",
              error: "Miaoshou already accepted MX; retry did not resubmit. An authorised official TikTok readback is still unavailable.",
              submission: {
                status: "SUBMITTED_UNVERIFIED",
                evidence: {
                  accepted: true,
                  pre_submit_audit: {
                    submission_fingerprint: "audit-mx",
                  },
                },
              },
            },
            {
              target_label: "ozon:RU",
              status: "SUCCEEDED",
              attempts: 1,
              external_id: "5673889199",
              error: null,
            },
          ],
        },
      };
      return route.fulfill(jsonResponse(dashboard));
    }
    const fixture = apiFixture(
      url,
      request.method(),
      { delayWeekly: false, delaySku: false, pending: {} },
    );
    return route.fulfill(fixture || jsonResponse({ ok: false }, 404));
  });
  try {
    await page.goto(`${baseUrl}/product-workspace?offer_id=3828811808`, {
      waitUntil: "networkidle",
    });
    const ledger = (await page.locator("#releaseRunLedger").innerText()).trim();
    check(
      ledger.includes("2 已回读 · 1 待人工验收"),
      "product release: terminal run separates API readback from manual verification",
      ledger,
    );
    check(
      ledger.includes("已提交 · 待人工验收")
      && ledger.includes("系统不会自动重试"),
      "product release: accepted-but-unverified target is not presented as a publish failure",
      ledger,
    );
    check(
      !ledger.includes("正式发布处理中"),
      "product release: terminal partial run is not presented as still processing",
      ledger,
    );
    check(
      await computedVisibility(page, ".run-target.awaiting-readback"),
      "product release: awaiting-readback state is visually distinct",
    );
    const stages = (await page.locator("#stageRail").innerText()).trim();
    check(
      stages.includes("渠道执行")
      && stages.includes("执行已结束")
      && stages.includes("回读对账")
      && stages.includes("1 个待人工验收"),
      "product release: journey advances to reconciliation after submissions finish",
      stages,
    );
    const nextStep = (await page.locator("#nextStepDescription").innerText()).trim();
    check(
      nextStep.includes("2/3 个目标已完成官方回读")
      && nextStep.includes("逐字段核对并记录人工验收"),
      "product release: next action explains the remaining manual verification",
      nextStep,
    );
    check(
      await computedVisibility(page, ".manual-verification-form"),
      "product release: API-less target exposes an in-product manual verification form",
    );
    check(
      await page.locator("#publishAllButton").isDisabled(),
      "product release: awaiting-manual-only run cannot be submitted again",
    );
    check(
      errors.length === 0,
      "product release terminal state: no console/page errors",
      errors,
    );
    check(
      requests.filter((row) => row.method === "POST").length === 0,
      "product release terminal state: initial render performs zero writes",
      requests,
    );
  } finally {
    await context.close();
  }
}

async function productCommonOverwriteContract(browser) {
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ]) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    const errors = [];
    const requests = [];
    let pendingOverwrite = null;
    let resolvePendingOverwrite;
    const pendingOverwriteReady = new Promise((resolve) => {
      resolvePendingOverwrite = resolve;
    });
    const dashboard = JSON.parse(JSON.stringify(productDashboard));
    dashboard.product.revision = 31;
    dashboard.product.fields_locked = true;
    dashboard.product.actual_product_approved = true;
    dashboard.content.approved = true;
    dashboard.content.blockers = [];
    dashboard.release_v1 = {
      eligible_for_plan_approval: true,
      plan_persisted: true,
      plan_approved: true,
      miaoshou_prepared: false,
      publish_ready: false,
      blockers: [],
      adapter_blockers: [],
      run: null,
      plan: {
        plan_id: "omnichannel:fixture-successor",
        status: "APPROVED",
        confirmation_token: "PUBLISH-FIXTURETOKEN",
        payload_digest: "fixture-payload-digest",
        targets: ["miaoshou:COMMON"],
        payload: {
          product_revision: 31,
          content_package_id: "content:fixture:r31",
          targets: ["miaoshou:COMMON"],
        },
      },
      common_overwrite_review: {
        schema_version: "miaoshou-common-overwrite-review-v1",
        status: "MISMATCH",
        plan_id: "omnichannel:fixture-successor",
        confirmation_token: "PUBLISH-FIXTURETOKEN",
        payload_digest: "fixture-payload-digest",
        expected_revision: 31,
        review_digest: "fixture-review-digest",
        identity_exact: true,
        readback_non_ambiguous: true,
        overwrite_allowed: true,
        changed_fields: ["title", "images"],
        blocking_fields: [],
        unknown_fields: [],
        fields: [
          {
            field: "title",
            label: "标题",
            changed: true,
            existing_summary: "18 chars · sha256:existing",
            immutable_plan_summary: "21 chars · sha256:approved",
          },
          {
            field: "seller_sku",
            label: "Seller SKU",
            changed: false,
            existing_summary: "••52 · sha256:existing",
            immutable_plan_summary: "••52 · sha256:approved",
          },
          {
            field: "spec_key",
            label: "规格 key",
            changed: false,
            existing_summary: "1 values · sha256:existing",
            immutable_plan_summary: "1 values · sha256:approved",
          },
          {
            field: "spec_label",
            label: "规格标签",
            changed: false,
            existing_summary: "1 values · sha256:existing",
            immutable_plan_summary: "1 values · sha256:approved",
          },
          {
            field: "weight",
            label: "重量",
            changed: false,
            existing_summary: "0.14 kg",
            immutable_plan_summary: "0.14 kg",
          },
          {
            field: "package",
            label: "包装尺寸",
            changed: false,
            existing_summary: "30 × 3 × 3 cm",
            immutable_plan_summary: "30 × 3 × 3 cm",
          },
          {
            field: "images",
            label: "图片数量与顺序",
            changed: true,
            existing_summary: "5 images · order sha256:existing",
            immutable_plan_summary: "6 images · order sha256:approved",
          },
          {
            field: "description",
            label: "描述",
            changed: false,
            existing_summary: "500 chars · sha256:existing",
            immutable_plan_summary: "500 chars · sha256:approved",
          },
          {
            field: "video_action",
            label: "视频动作",
            changed: false,
            existing_summary: "remove",
            immutable_plan_summary: "remove",
          },
        ],
        external_writes_performed: [],
      },
    };
    page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(`console: ${message.text()}`);
    });
    await page.route("**/*", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== baseUrl) return route.abort("blockedbyclient");
      if (!url.pathname.startsWith("/api/")) return route.continue();
      requests.push({ method: request.method(), url: request.url() });
      if (url.pathname === "/api/product-workspace/dashboard") {
        return route.fulfill(jsonResponse(dashboard));
      }
      if (
        url.pathname === "/api/product-workspace/miaoshou-draft/commit"
        && request.method() === "POST"
      ) {
        pendingOverwrite = route;
        resolvePendingOverwrite();
        return;
      }
      const fixture = apiFixture(
        url,
        request.method(),
        { delayWeekly: false, delaySku: false, pending: {} },
      );
      return route.fulfill(fixture || jsonResponse({ ok: false }, 404));
    });
    try {
      await page.goto(`${baseUrl}/product-workspace?offer_id=3828540231`, {
        waitUntil: "networkidle",
      });
      check(
        await computedVisibility(page, "#commonOverwritePanel"),
        `COMMON overwrite ${viewport.width}: redacted mismatch panel is visible`,
      );
      check(
        await computedVisibility(page, "#commonOverwriteButton"),
        `COMMON overwrite ${viewport.width}: explicit action is visible`,
      );
      const diffText = await page.locator("#commonOverwriteDiff").innerText();
      check(
        diffText.includes("标题")
        && diffText.includes("Seller SKU")
        && diffText.includes("规格 key")
        && diffText.includes("规格标签")
        && diffText.includes("重量")
        && diffText.includes("包装尺寸")
        && diffText.includes("图片数量与顺序")
        && diffText.includes("描述")
        && diffText.includes("视频动作"),
        `COMMON overwrite ${viewport.width}: required field rows are rendered`,
        diffText,
      );
      const overflow = await overflowAudit(page);
      check(
        overflow.pageOverflow <= 2,
        `COMMON overwrite ${viewport.width}: no horizontal overflow`,
        overflow,
      );
      check(
        requests.filter((row) => row.method === "POST").length === 0,
        `COMMON overwrite ${viewport.width}: initial render performs zero writes`,
        requests,
      );
      if (viewport.width === 1440) {
        await page.locator("#commonOverwriteCheckbox").check();
        await page.locator("#commonOverwriteButton").click();
        await pendingOverwriteReady;
        await page.waitForFunction(() => (
          document.querySelector("#commonOverwriteMessage")?.textContent.includes("重新只读核对")
        ));
        const body = pendingOverwrite.request().postDataJSON();
        check(
          body.confirm_miaoshou_write === true
          && body.confirm_miaoshou_overwrite === true
          && body.approved_by === "Kyle"
          && body.plan_id === "omnichannel:fixture-successor"
          && body.confirmation_token === "PUBLISH-FIXTURETOKEN"
          && body.expected_revision === 31
          && body.payload_digest === "fixture-payload-digest"
          && body.overwrite_review_digest === "fixture-review-digest",
          "COMMON overwrite desktop: POST binds every explicit immutable-plan field",
          body,
        );
        check(
          await page.locator("#commonOverwriteButton").isDisabled(),
          "COMMON overwrite desktop: button is disabled while request is pending",
        );
        const ambiguousDashboard = JSON.parse(JSON.stringify(dashboard));
        ambiguousDashboard.release_v1.run = {
          status: "FAILED",
          targets: [{
            target_label: "miaoshou:COMMON",
            status: "FAILED",
            attempts: 1,
            error: "unknown after dispatch",
          }],
        };
        await pendingOverwrite.fulfill(jsonResponse({
          ok: false,
          error: "socket closed after COMMON edit dispatch",
          reconciliation_required: true,
          durable_state_uncertain: true,
          external_writes_performed: ["miaoshou:COMMON:immutable_plan_write"],
          dashboard: ambiguousDashboard,
        }, 502));
        await page.waitForFunction(() => (
          document.querySelector("#commonOverwriteMessage")?.textContent.includes("reconciliation evidence")
        ));
        check(
          !(await computedVisibility(page, "#commonOverwriteButton")),
          "COMMON overwrite desktop: ambiguous run hides repeat overwrite action",
        );
      }
      check(
        unexpectedInteractionErrors(errors).length === 0,
        `COMMON overwrite ${viewport.width}: no console/page errors`,
        errors,
      );
    } finally {
      await context.close();
    }
  }
}

async function aiAsyncFeedback(browser) {
  const scenario = await openScenario(
    browser,
    "/ai-image-studio?offer_id=3828540231",
    { width: 1440, height: 900 },
  );
  const { page, context, errors, state } = scenario;
  try {
    check(
      !(await computedVisibility(page, "#preparePackageButton")),
      "AI studio: existing content package hides the create-package action",
    );
    const generationState = (await page.locator("#generationProgress").innerText()).trim();
    check(
      generationState.includes("等待 Kyle 确认付费")
      && !generationState.includes("尚未开始生成"),
      "AI studio: ready preflight is not presented as not-started",
      generationState,
    );
    check(
      generationState.includes("生成前检查")
      && generationState.includes("付费确认")
      && !generationState.includes("尚未开始"),
      "AI studio: progress steps describe actual actions instead of a fake phase",
      generationState,
    );
    const generationSummary = (await page.locator("#generationSummary").innerText()).trim();
    const emptyVersionMessage = (await page.locator("#versionGrid").innerText()).trim();
    check(
      generationSummary.includes("等待付费确认")
      && emptyVersionMessage.includes("尚未创建付费任务")
      && !emptyVersionMessage.includes("先保存经验配方"),
      "AI studio: summary and empty-state agree with the ready preflight",
      { generationSummary, emptyVersionMessage },
    );
    check(
      !(await computedVisibility(page, "#preflightButton")),
      "AI studio: completed automatic preflight hides the redundant manual button",
    );
    // Do not confuse the successful initial project-load toast with the result
    // of the subsequent save operation under test.
    await page.waitForFunction(() => document.querySelector("#toast")?.hidden, null, {
      timeout: 8000,
    });
    await page.route("**/api/product-flow/review", (route) => {
      state.pending.sourceReview = route;
    });
    await page.locator("#saveSourceButton").click();
    await page.waitForFunction(() => document.querySelector("#saveSourceButton")?.classList.contains("is-loading"));
    check(
      await computedVisibility(page, "#saveSourceButton"),
      "AI studio: async source save keeps a visible loading control",
    );
    await state.pending.sourceReview.fulfill(
      jsonResponse({ ok: false, error: "离线保存失败" }, 500),
    );
    await page.waitForFunction(() => !document.querySelector("#saveSourceButton")?.classList.contains("is-loading"));
    check(
      await computedVisibility(page, "#alert"),
      "AI studio: save failure alert is computed-visible",
    );
    check(
      !(await computedVisibility(page, "#toast")),
      "AI studio: failed save does not display success toast",
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      "AI interaction: no unexpected console/page errors",
      errors,
    );
  } finally {
    await context.close();
  }
}

async function profitAsyncAndNoFalseSuccess(browser) {
  const scenario = await openScenario(browser, "/profit", { width: 1440, height: 900 });
  const { page, context, errors, state } = scenario;
  try {
    const badge = (await page.locator("#weeklyStatusBadge").innerText()).trim().toUpperCase();
    check(!badge.includes("READY"), "profit: no-data weekly state is not READY", badge);
    const verdictClass = await page.locator("#weeklyVerdict").getAttribute("class");
    check(!String(verdictClass).includes(" ready"), "profit: no-data weekly state has no ready style", verdictClass);

    state.delayWeekly = true;
    await page.locator("#weeklyForm").evaluate((form) => form.requestSubmit());
    await page.waitForFunction(() => document.querySelector("#weeklyForm")?.classList.contains("is-loading"));
    const weeklyLoading = await page.locator("#weeklyForm .button-loading").evaluate((node) => {
      const style = getComputedStyle(node);
      return style.display !== "none" && style.visibility !== "hidden";
    });
    check(weeklyLoading, "profit: weekly refresh exposes loading state");
    await state.pending.weekly.fulfill(
      jsonResponse({ ok: false, error: "离线周报失败" }, 500),
    );
    await page.waitForFunction(() => !document.querySelector("#weeklyForm")?.classList.contains("is-loading"));
    check(await computedVisibility(page, "#weeklyAlert"), "profit: weekly failure alert is visible");
    const failedBadge = (await page.locator("#weeklyStatusBadge").innerText()).trim().toUpperCase();
    check(!failedBadge.includes("READY"), "profit: failed weekly request is not READY", failedBadge);

    state.delaySku = true;
    await page.locator("#skuInput").fill("0021");
    await page.locator("#skuForm").evaluate((form) => form.requestSubmit());
    await page.waitForFunction(() => document.querySelector("#skuForm")?.classList.contains("is-loading"));
    const skuLoading = await page.locator("#skuForm .button-loading").evaluate((node) => {
      const style = getComputedStyle(node);
      return style.display !== "none" && style.visibility !== "hidden";
    });
    check(skuLoading, "profit: SKU lookup exposes loading state");
    await state.pending.sku.fulfill(
      jsonResponse({ ok: false, error: "离线样例：没有可审计样本" }, 422),
    );
    await page.waitForFunction(() => !document.querySelector("#skuForm")?.classList.contains("is-loading"));
    check(await computedVisibility(page, "#skuAlert"), "profit: SKU failure alert is visible");
    const skuText = (await page.locator("#skuResult").innerText()).toUpperCase();
    check(!skuText.includes("CALCULATED"), "profit: failed SKU lookup is not CALCULATED", skuText);
    check(
      unexpectedInteractionErrors(errors).length === 0,
      "profit interaction: no unexpected console/page errors",
      errors,
    );
  } finally {
    await context.close();
  }
}

async function legacyStateSafety(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  const state = { delayWeekly: false, delaySku: false, pending: {} };
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== baseUrl) return route.abort("blockedbyclient");
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ method: request.method(), url: request.url() });
    if (url.pathname === "/api/product-flow/preview") {
      const legacy = JSON.parse(JSON.stringify(aiPreview));
      delete legacy.content_package.content_strategy;
      delete legacy.content_package.suite_customization;
      delete legacy.content_package.remaining_images_generation;
      return route.fulfill(jsonResponse(legacy));
    }
    const fixture = apiFixture(url, request.method(), state);
    return route.fulfill(fixture || jsonResponse({ ok: false }, 500));
  });
  try {
    await page.goto(`${baseUrl}/ai-image-studio?offer_id=3828540231`, { waitUntil: "networkidle" });
    check(errors.length === 0, "legacy AI state: missing newer fields does not crash", errors);
    check(
      await computedVisibility(page, "#flowRail"),
      "legacy AI state: workflow remains visible",
    );
    const progressText = (await page.locator("#generationProgress").innerText()).toUpperCase();
    check(
      !progressText.includes("VERIFIED"),
      "legacy AI state: missing write state does not fabricate verified",
      progressText,
    );
    check(
      requests.filter((row) => row.method === "POST").length === 0,
      "legacy AI state: migration/read performs zero writes",
      requests,
    );
  } finally {
    await context.close();
  }
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const pages = [
      {
        name: "Orbit 首页",
        path: "/",
        nextActions: [".focus-product", ".focus-profit"],
      },
      {
        name: "商品发布中心",
        path: "/product-workspace?offer_id=3828540231",
        nextActions: ["#nextStepTitle", "#nextStepDescription"],
      },
      {
        name: "AI 图片工作室",
        path: "/ai-image-studio?offer_id=3828540231",
        nextActions: ["#flowRail", "#generationProgress"],
      },
      {
        name: "利润中心",
        path: "/profit",
        nextActions: ["#weeklyVerdict", "#skuResult"],
      },
    ];
    for (const definition of pages) {
      await auditPage(browser, definition, { width: 1440, height: 900 });
      await auditPage(browser, definition, { width: 390, height: 844 });
    }
    await productAsyncFeedback(browser);
    await productLockedTitleAdoption(browser);
    await productPreservedTitleApprovalReload(browser);
    await productLockedStaleTitleRefresh(browser);
    await productMultiTabTitleRefreshConflict(browser);
    await productReleaseTerminalState(browser);
    await productCommonOverwriteContract(browser);
    await aiAsyncFeedback(browser);
    await profitAsyncAndNoFalseSuccess(browser);
    await legacyStateSafety(browser);
  } finally {
    await browser.close();
  }
  process.stdout.write(`${JSON.stringify({ ok: failures.length === 0, failures, results }, null, 2)}\n`);
  if (failures.length) process.exitCode = 1;
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
