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
    return jsonResponse(state.productDashboard || productDashboard);
  }
  if (path === "/api/product-flow/preview") return jsonResponse(state.aiPreview || aiPreview);
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

async function openScenario(browser, path, viewport, options = {}) {
  const context = await browser.newContext({ viewport });
  if (Array.isArray(options.queueOffers)) {
    await context.addInitScript(({ key, offers }) => {
      localStorage.setItem(
        key,
        JSON.stringify(offers.map((offer_id) => ({ offer_id }))),
      );
    }, {
      key: "orbit.productWorkspace.releaseQueue.v1",
      offers: options.queueOffers,
    });
  }
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  const state = {
    delayWeekly: false,
    delaySku: false,
    pending: {},
    aiPreview: options.aiPreview || null,
    productDashboard: options.productDashboard || null,
  };
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  await installApiRoutes(page, state, requests);
  await page.goto(`${baseUrl}${path}`, { waitUntil: "networkidle" });
  return { context, page, errors, requests, state };
}

async function productQueueLongTitleMobileContract(browser) {
  const dashboard = JSON.parse(JSON.stringify(productDashboard));
  dashboard.product.title = (
    "Self-Adhesive Watercolor Floral Butterfly Wall Sticker, PVC Flat "
    + "Wall Decal for Living Room and Bedroom, 30 x 90 cm, 2 Pieces"
  );
  const scenario = await openScenario(
    browser,
    "/product-workspace?offer_id=3828540231",
    { width: 390, height: 844 },
    {
      productDashboard: dashboard,
      queueOffers: [
        "3828540231",
        "3828811808",
        "3838616043",
        "3838614276",
        "3838600989",
        "3845133620",
      ],
    },
  );
  const { page, context, errors, requests } = scenario;
  try {
    await page.locator(".queue-card").first().waitFor({
      state: "visible",
      timeout: 5000,
    });
    const cardCount = await page.locator(".queue-card").count();
    check(
      cardCount === 6,
      "mobile queue: all long-title products remain visible",
      cardCount,
    );
    const overflow = await overflowAudit(page);
    check(
      overflow.pageOverflow <= 2,
      "mobile queue: long product titles do not cause page overflow",
      overflow,
    );
    check(
      requests.filter((row) => row.method === "POST").length === 0,
      "mobile queue: rendering and refresh perform zero writes",
      requests,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      "mobile queue: no console/page errors",
      errors,
    );
  } finally {
    await context.close();
  }
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
  let adoptRequest = null;
  let refreshedDashboard = null;
  let confirmation = "";
  let markAdoptionStarted;
  let finishAdoption;
  const adoptionStarted = new Promise((resolve) => {
    markAdoptionStarted = resolve;
  });
  const adoptionGate = new Promise((resolve) => {
    finishAdoption = resolve;
  });
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
  staleDashboard.release_v1 = {
    eligible_for_plan_approval: false,
    plan_persisted: false,
    plan_approved: false,
    miaoshou_prepared: false,
    publish_ready: false,
    blockers: [
      "listing copy must be adopted in approved product facts before release",
      "listing copy input signature is stale",
    ],
    recovery_actions: [{
      code: "refresh_listing_copy",
      label: "重新生成平台文案",
      detail: "商品事实或所选规格在上次采用文案后发生了变化。",
      next_codes: ["refresh_listing_copy", "adopt_listing_copy"],
      marketplace_writes_performed: [],
    }],
    adapter_blockers: [],
    run: null,
    plan: {
      plan_id: "omnichannel:stale-preview",
      payload: {
        product_revision: 20,
        content_package_id: "content:fixture:r20",
      },
    },
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
        semantic_master_en: staleDashboard.product.title,
        candidates: [],
        input_signature: "sha256:stable-approved-facts",
        current_input_signature: "sha256:stable-approved-facts",
        fact_snapshot: { offer_id: "3828540231" },
        model_input_signature: "sha256:audited-model-input",
        policy_version: "listing-copy-candidates-v4",
        model: "gpt-5.4-mini-official",
      };
      updated.release_v1 = {
        ...staleDashboard.release_v1,
        blockers: [
          "listing copy must be adopted in approved product facts before release",
        ],
        recovery_actions: [{
          code: "adopt_listing_copy",
          label: "去采用当前 EN MASTER",
          detail: "平台文案候选已经生成，但尚未绑定到当前商品事实。",
          next_codes: ["adopt_listing_copy"],
          marketplace_writes_performed: [],
        }],
      };
      refreshedDashboard = updated;
      return route.fulfill(jsonResponse({
        ok: true,
        revision: 21,
        locked_stale_refresh: true,
        superseded_release_plan_id: "omnichannel:old-plan",
        marketplace_writes_performed: [],
        dashboard: updated,
      }));
    }
    if (
      url.pathname === "/api/product-workspace/title-adopt"
      && request.method() === "POST"
    ) {
      adoptRequest = request.postDataJSON();
      const adopted = JSON.parse(JSON.stringify(refreshedDashboard));
      adopted.listing_copy.status = "adopted_in_product_facts";
      adopted.release_v1 = {
        ...adopted.release_v1,
        eligible_for_plan_approval: true,
        blockers: [],
        recovery_actions: [],
        plan: {
          plan_id: "omnichannel:refreshed-preview",
          confirmation_token: "PUBLISH-REFRESHED-PREVIEW",
          payload: {
            product_revision: 21,
            content_package_id: "content:fixture:r21",
          },
        },
      };
      markAdoptionStarted();
      await adoptionGate;
      return route.fulfill(jsonResponse({
        ok: true,
        revision: 21,
        product_approval_preserved: true,
        marketplace_writes_performed: [],
        dashboard: adopted,
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
    const approvalCheckbox = page.locator("#releasePlanCheckbox");
    const recovery = page.locator(
      '[data-release-recovery="refresh_listing_copy"]',
    );
    check(
      await approvalCheckbox.isDisabled()
      && await recovery.isEnabled()
      && await recovery.isVisible(),
      "product: blocked approval exposes an enabled recovery action beside the disabled gate",
    );
    const recoveryDetail = (
      await page.locator("#releasePlanRecovery").innerText()
    ).trim();
    check(
      recoveryDetail.includes("商品事实或所选规格")
      && recoveryDetail.includes("重新生成平台文案"),
      "product: blocked approval explains the stale-copy cause and next action in Chinese",
      recoveryDetail,
    );
    await recovery.click();
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
      await page.locator(
        '[data-release-recovery="adopt_listing_copy"]',
      ).isEnabled(),
      "product: refresh advances the release recovery action to explicit EN MASTER adoption",
    );
    await page.locator(
      '[data-release-recovery="adopt_listing_copy"]',
    ).click();
    await page.locator(".adopt-title-candidate").click();
    await adoptionStarted;
    await page.waitForFunction(() => (
      document.querySelector("#releasePlanCheckboxDisabledReason")
        ?.textContent.includes("正在完成当前读取或本地状态更新")
    ));
    check(
      await approvalCheckbox.isDisabled(),
      "product: release approval remains disabled while EN MASTER adoption is in flight",
    );
    finishAdoption();
    await page.waitForFunction(
      () => document.querySelector("#releasePlanCheckbox")?.disabled === false,
    );
    check(
      adoptRequest
      && adoptRequest.expected_revision === 21
      && adoptRequest.input_signature === "sha256:stable-approved-facts"
      && adoptRequest.user_approved === true
      && adoptRequest.approved_by === "Kyle",
      "product: EN MASTER adoption binds the refreshed revision and signature",
      adoptRequest,
    );
    check(
      await approvalCheckbox.isEnabled()
      && await page.locator("#releasePlanRecovery").isHidden(),
      "product: successful adoption releases the approval checkbox without a reload",
    );
    await approvalCheckbox.check();
    check(
      await page.locator("#approveReleasePlanButton").isEnabled(),
      "product: Kyle can proceed to approve the refreshed ReleasePlan in the same page session",
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
      ledger.includes("1 已回读 · 1 待人工验收")
      && ledger.includes("公共草稿已核验 · 不计入店铺发布"),
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
      && stages.includes("部分完成 · 需对账")
      && stages.includes("回读对账")
      && stages.includes("1 个待人工验收"),
      "product release: journey advances to reconciliation after submissions finish",
      stages,
    );
    const nextStep = (await page.locator("#nextStepDescription").innerText()).trim();
    check(
      nextStep.includes("1/2 个店铺已完成官方回读")
      && nextStep.includes("需在平台后台逐字段人工验收")
      && nextStep.includes("禁止重发"),
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

async function productReleasePartialFailedLedger(browser) {
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ]) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    const errors = [];
    const requests = [];
    const dashboard = JSON.parse(JSON.stringify(productDashboard));
    const targetLabels = [
      "miaoshou:COMMON",
      "shopee:PH",
      "shopee:TH",
      "shopee:MY",
      "shopee:VN",
      "ozon:RU",
      "tiktok:MX",
      "tiktok:GB",
    ];
    dashboard.product.seller_sku_candidate = "0953";
    dashboard.product.actual_product_approved = true;
    dashboard.content = {
      approved: true,
      image_count: 6,
      images: [],
      blockers: [],
    };
    dashboard.actual_release_gate = { ready: true, blockers: [] };
    dashboard.publication_scope = {
      selected_labels: targetLabels,
      default_labels: targetLabels,
      available_targets: [
        { label: "miaoshou:COMMON", channel: "miaoshou", country: "COMMON" },
        { label: "shopee:PH", channel: "shopee", shop: "LivelyHive", country: "PH" },
        { label: "shopee:TH", channel: "shopee", shop: "LivelyHive", country: "TH" },
        { label: "shopee:MY", channel: "shopee", shop: "LivelyHive", country: "MY" },
        { label: "shopee:VN", channel: "shopee", shop: "LivelyHive", country: "VN" },
        { label: "ozon:RU", channel: "ozon", country: "RU" },
        { label: "tiktok:MX", channel: "tiktok", shop: "LivelyHive", country: "MX" },
        { label: "tiktok:GB", channel: "tiktok", shop: "LivelyHive", country: "GB" },
      ],
    };
    const reconcileTarget = (targetLabel, externalId) => ({
      target_label: targetLabel,
      status: "FAILED",
      attempts: 1,
      external_id: externalId,
      error: "official readback verified identity; price basis has not converged",
      readback: {
        evidence: {
          source: "official-marketplace-readback",
          verified: false,
          checks: { identity: true, title: true, price: false },
        },
      },
      latest_failure_evidence: {
        attempt: 1,
        evidence: {
          external_writes_performed: [`${targetLabel}:create`],
          readback_verified: false,
        },
      },
    });
    const safeRetryTarget = (targetLabel, reason) => ({
      target_label: targetLabel,
      status: "FAILED",
      attempts: 1,
      external_id: null,
      error: reason,
      readback: null,
      submission: null,
      failure_events: [{
        evidence: {
          pre_submit_failure: true,
          submission_accepted: false,
          reason_code: "fixture_zero_write_pre_submit",
          external_writes_performed: [],
        },
      }],
      latest_failure_evidence: {
        evidence: {
          pre_submit_failure: true,
          submission_accepted: false,
          reason_code: "fixture_zero_write_pre_submit",
          external_writes_performed: [],
        },
      },
    });
    const manualTarget = (targetLabel, externalId) => ({
      target_label: targetLabel,
      status: "SUBMITTED_UNVERIFIED",
      attempts: 1,
      external_id: externalId,
      error: "accepted; no authorised official readback",
      submission: {
        status: "SUBMITTED_UNVERIFIED",
        external_id: externalId,
        evidence: {
          accepted: true,
          pre_submit_audit: { submission_fingerprint: `audit-${targetLabel}` },
        },
      },
    });
    dashboard.release_v1 = {
      eligible_for_plan_approval: false,
      plan_persisted: true,
      plan_approved: true,
      miaoshou_prepared: true,
      publish_ready: true,
      blockers: [],
      adapter_blockers: [],
      plan: {
        plan_id: "omnichannel:partial-failed-ledger",
        confirmation_token: "PUBLISH-PARTIAL-FAILED",
        targets: targetLabels,
        payload: {
          product_revision: 31,
          content_package_id: "content:partial-failed",
          targets: targetLabels,
        },
      },
      run: {
        run_id: "release-run:partial-failed-ledger",
        status: "PARTIAL_FAILED",
        targets: [
          {
            target_label: "miaoshou:COMMON",
            status: "SUCCEEDED",
            attempts: 1,
            external_id: "3838616043",
            error: null,
          },
          reconcileTarget("shopee:PH", "56164935203"),
          reconcileTarget("shopee:TH", "51564925929"),
          safeRetryTarget("shopee:MY", "missing required category mapping"),
          safeRetryTarget("shopee:VN", "listing price preflight is blocked"),
          safeRetryTarget("ozon:RU", "adapter input is incomplete"),
          manualTarget("tiktok:MX", "3227308139:16265910"),
          manualTarget("tiktok:GB", "3227304421:10204699"),
        ],
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
      const fixture = apiFixture(
        url,
        request.method(),
        { delayWeekly: false, delaySku: false, pending: {} },
      );
      return route.fulfill(fixture || jsonResponse({ ok: false }, 404));
    });
    try {
      await page.goto(`${baseUrl}/product-workspace?offer_id=3838616043`, {
        waitUntil: "networkidle",
      });
      const stages = (await page.locator("#stageRail").innerText()).trim();
      check(
        stages.includes("渠道执行")
        && stages.includes("部分完成 · 需对账")
        && stages.includes("回读对账")
        && stages.includes("2 个结果待对账")
        && !stages.includes("渠道执行\n待执行"),
        `release ledger ${viewport.width}: started partial run never falls back to pending execution`,
        stages,
      );
      const ledger = (await page.locator("#releaseRunLedger").innerText()).trim();
      check(
        await page.locator(".run-target.reconciliation-required").count() === 2
        && ledger.includes("已创建 · 结果待对账，禁止重发")
        && ledger.includes("56164935203")
        && ledger.includes("51564925929")
        && !ledger.includes("失败待重试"),
        `release ledger ${viewport.width}: PH/TH external outcomes are reconcile-only`,
        ledger,
      );
      check(
        await page.locator(".run-target.safe-retry").count() === 3
        && ledger.includes("失败 · 可安全重试"),
        `release ledger ${viewport.width}: MY/VN/Ozon pre-submit failures are safe-retry only`,
        ledger,
      );
      check(
        await page.locator(".run-target.awaiting-readback").count() === 2,
        `release ledger ${viewport.width}: MX/GB remain manual verification`,
      );
      const nextAction = (await page.locator("#nextStepDescription").innerText()).trim();
      check(
        nextAction.includes("Shopee · LivelyHive · 菲律宾")
        && nextAction.includes("Shopee · LivelyHive · 泰国")
        && nextAction.includes("仅回读/对账，禁止重发")
        && nextAction.includes("Shopee · LivelyHive · 马来西亚")
        && nextAction.includes("Shopee · LivelyHive · 越南")
        && nextAction.includes("Ozon · 俄罗斯")
        && nextAction.includes("修复阻塞后再安全重试")
        && nextAction.includes("TikTok Shop · LivelyHive · 墨西哥")
        && nextAction.includes("TikTok Shop · LivelyHive · 英国")
        && nextAction.includes("人工验收"),
        `release ledger ${viewport.width}: next action separates reconcile, retry and manual lanes`,
        nextAction,
      );
      const publishNote = (await page.locator("#publishAllNote").innerText()).trim();
      check(
        await page.locator("#publishAllButton").isDisabled()
        && await page.locator("#publishAllCheckbox").isDisabled()
        && publishNote.includes("只能回读/对账，禁止重发")
        && publishNote.includes("一键发布保持关闭"),
        `release ledger ${viewport.width}: unsafe outcomes disable one-click publish with reason`,
        publishNote,
      );
      const overflow = await overflowAudit(page);
      check(
        overflow.pageOverflow <= 2,
        `release ledger ${viewport.width}: no horizontal overflow`,
        overflow,
      );
      check(
        unexpectedInteractionErrors(errors).length === 0,
        `release ledger ${viewport.width}: no console/page errors`,
        errors,
      );
      check(
        requests.filter((row) => row.method === "POST").length === 0,
        `release ledger ${viewport.width}: fixture performs zero writes`,
        requests,
      );
    } finally {
      await context.close();
    }
  }
}

function shopeePriceRepairDashboard(targetLabel = "shopee:PH") {
  const dashboard = JSON.parse(JSON.stringify(productDashboard));
  const labels = ["shopee:PH", "shopee:TH"];
  dashboard.product.offer_id = "3838616043";
  dashboard.product.seller_sku_candidate = "0954";
  dashboard.product.revision = 31;
  dashboard.product.fields_locked = true;
  dashboard.product.actual_product_approved = true;
  dashboard.content = {
    approved: true,
    image_count: 6,
    images: [],
    blockers: [],
  };
  dashboard.actual_release_gate = { ready: true, blockers: [] };
  dashboard.publication_scope = {
    selected_labels: labels,
    default_labels: labels,
    available_targets: [
      {
        label: "shopee:PH",
        channel: "shopee",
        shop: "LivelyHive",
        country: "PH",
      },
      {
        label: "shopee:TH",
        channel: "shopee",
        shop: "LivelyHive",
        country: "TH",
      },
    ],
  };
  dashboard.release_v1 = {
    eligible_for_plan_approval: false,
    plan_persisted: true,
    plan_approved: true,
    miaoshou_prepared: true,
    publish_ready: false,
    blockers: [],
    adapter_blockers: [],
    plan: {
      plan_id: "omnichannel:SECRET-PLAN-ID",
      confirmation_token: "PUBLISH-SECRET-TOKEN",
      payload_digest: "SECRET-PAYLOAD-DIGEST",
      targets: labels,
      payload: {
        product_revision: 31,
        content_package_id: "content:price-repair-fixture",
        targets: labels,
      },
    },
    run: {
      run_id: "release-run:price-repair-fixture",
      status: "PARTIAL_FAILED",
      targets: [{
        target_label: targetLabel,
        status: "FAILED",
        attempts: 1,
        external_id: targetLabel === "shopee:PH"
          ? "56164935203"
          : "51564925929",
        error: "official price basis has not converged",
        latest_failure_evidence: {
          evidence: {
            external_writes_performed: [`${targetLabel}:create`],
          },
        },
        repair: null,
      }],
    },
  };
  return dashboard;
}

async function productShopeePriceRepairContract(browser) {
  const outcomes = ["success", "reconciliation", "durable"];
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ]) {
    for (const outcome of outcomes) {
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      const errors = [];
      const requests = [];
      const externalRequests = [];
      const targetLabel = outcome === "durable" ? "shopee:TH" : "shopee:PH";
      let dashboard = shopeePriceRepairDashboard(targetLabel);
      let pendingPreview;
      let resolvePreviewRequested;
      const previewRequested = new Promise((resolve) => {
        resolvePreviewRequested = resolve;
      });
      let pendingRepair;
      let resolveRepairRequested;
      const repairRequested = new Promise((resolve) => {
        resolveRepairRequested = resolve;
      });
      page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
      page.on("console", (message) => {
        if (message.type() === "error") errors.push(`console: ${message.text()}`);
      });
      await page.route("**/*", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin !== baseUrl) {
          externalRequests.push(request.url());
          return route.abort("blockedbyclient");
        }
        if (!url.pathname.startsWith("/api/")) return route.continue();
        requests.push({
          method: request.method(),
          url: request.url(),
          body: request.postDataJSON?.() || null,
        });
        if (url.pathname === "/api/product-workspace/dashboard") {
          return route.fulfill(jsonResponse(dashboard));
        }
        if (
          url.pathname
            === "/api/product-workspace/release-target/shopee-price-repair-preview"
          && request.method() === "GET"
        ) {
          pendingPreview = route;
          resolvePreviewRequested();
          return;
        }
        if (
          url.pathname
            === "/api/product-workspace/release-target/shopee-price-repair"
          && request.method() === "POST"
        ) {
          pendingRepair = route;
          resolveRepairRequested();
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
        await page.goto(
          `${baseUrl}/product-workspace?offer_id=3838616043`,
          { waitUntil: "networkidle" },
        );
        const targetSelector = `[data-price-repair-target="${targetLabel}"]`;
        const centerHit = async (selector) => {
          const locator = page.locator(selector);
          await locator.scrollIntoViewIfNeeded();
          return locator.evaluate((element) => {
            const box = element.getBoundingClientRect();
            const hit = document.elementFromPoint(
              box.left + (box.width / 2),
              box.top + (box.height / 2),
            );
            return {
              targetId: element.id || "",
              hitId: hit?.closest("button")?.id || "",
              hitAction: hit?.closest("button")?.dataset.priceRepairAction || "",
              box: {
                left: Math.round(box.left),
                top: Math.round(box.top),
                width: Math.round(box.width),
                height: Math.round(box.height),
              },
            };
          });
        };
        const refreshHit = await centerHit("#refreshChannelsButton");
        await page.locator("#refreshChannelsButton").click({ trial: true });
        check(
          refreshHit.hitId === "refreshChannelsButton",
          `Shopee price repair ${viewport.width}/${outcome}: refresh control center is not covered by decorative flow`,
          refreshHit,
        );
        const previewHit = await centerHit(
          `${targetSelector} [data-price-repair-action="preview"]`,
        );
        check(
          await page.locator(`${targetSelector} [data-price-repair-action="preview"]`).isVisible(),
          `Shopee price repair ${viewport.width}/${outcome}: eligible target exposes read-only check`,
          previewHit,
        );
        check(
          previewHit.hitAction === "preview",
          `Shopee price repair ${viewport.width}/${outcome}: target preview control center is clickable`,
          previewHit,
        );
        check(
          await page.locator(`${targetSelector} [data-price-repair-confirm]`).count() === 0
          && await page.locator(`${targetSelector} [data-price-repair-action="submit"]`).count() === 0,
          `Shopee price repair ${viewport.width}/${outcome}: confirm and repair stay hidden before allowed preview`,
        );
        check(
          requests.filter((row) => row.method === "POST").length === 0,
          `Shopee price repair ${viewport.width}/${outcome}: initial page performs zero POST`,
          requests,
        );

        const previewStarted = Date.now();
        await page.locator(
          `${targetSelector} [data-price-repair-action="preview"]`,
        ).click();
        await previewRequested;
        await page.waitForFunction(
          (selector) => document.querySelector(selector)?.textContent.includes(
            "正在只读核对",
          ),
          `${targetSelector} .shopee-price-repair-message`,
          { timeout: 500 },
        );
        check(
          Date.now() - previewStarted < 500,
          `Shopee price repair ${viewport.width}/${outcome}: preview pending feedback appears within 500ms`,
        );
        check(
          await page.locator(
            `${targetSelector} [data-price-repair-action="preview"]`,
          ).isDisabled(),
          `Shopee price repair ${viewport.width}/${outcome}: preview control disables while GET is pending`,
        );
        const previewUrl = new URL(pendingPreview.request().url());
        check(
          pendingPreview.request().method() === "GET"
          && previewUrl.searchParams.get("offer_id") === "3838616043"
          && previewUrl.searchParams.get("target_label") === targetLabel,
          `Shopee price repair ${viewport.width}/${outcome}: preview GET binds offer and one target`,
          pendingPreview.request().url(),
        );
        check(
          requests.filter((row) => row.method === "POST").length === 0,
          `Shopee price repair ${viewport.width}/${outcome}: preview is GET-only`,
          requests,
        );
        await pendingPreview.fulfill(jsonResponse({
          ok: true,
          repair_allowed: true,
          plan_id: "omnichannel:SECRET-PLAN-ID",
          target_label: targetLabel,
          expected_revision: 31,
          payload_digest: "SECRET-PAYLOAD-DIGEST",
          preflight_digest: "SECRET-PREFLIGHT-DIGEST",
          external_writes_performed: [],
          state_mutations_performed: [],
        }));
        await page.waitForFunction(
          (selector) => document.querySelector(selector),
          `${targetSelector} [data-price-repair-confirm]`,
        );
        const previewText = await page.locator(targetSelector).innerText();
        check(
          previewText.includes("当前不可变 ReleasePlan")
          && previewText.includes("Kyle 已批准")
          && previewText.includes("revision 31")
          && previewText.includes("仅原地修正该站点价格，不重发商品")
          && ![
            "SECRET",
            "0954",
            "56164935203",
            "51564925929",
            "414",
            "model",
            "digest",
          ].some((secret) => previewText.includes(secret)),
          `Shopee price repair ${viewport.width}/${outcome}: allowed preview is redacted`,
          previewText,
        );

        await page.locator(
          `${targetSelector} [data-price-repair-confirm]`,
        ).check();
        const submitButton = page.locator(
          `${targetSelector} [data-price-repair-action="submit"]`,
        );
        check(
          await submitButton.isEnabled(),
          `Shopee price repair ${viewport.width}/${outcome}: dedicated checkbox enables only this target`,
        );
        const repairStarted = Date.now();
        await submitButton.click();
        await repairRequested;
        await page.waitForFunction(
          (selector) => document.querySelector(selector)?.textContent.includes(
            "只发送一次原地修价",
          ),
          `${targetSelector} .shopee-price-repair-message`,
          { timeout: 500 },
        );
        check(
          Date.now() - repairStarted < 500,
          `Shopee price repair ${viewport.width}/${outcome}: repair pending feedback appears within 500ms`,
        );
        check(
          await page.locator(
            `${targetSelector} [data-price-repair-action="submit"]`,
          ).isDisabled(),
          `Shopee price repair ${viewport.width}/${outcome}: repair button disables while POST is pending`,
        );
        const repairPosts = requests.filter((row) => (
          row.method === "POST"
          && new URL(row.url).pathname
            === "/api/product-workspace/release-target/shopee-price-repair"
        ));
        const body = pendingRepair.request().postDataJSON();
        check(
          repairPosts.length === 1
          && body.offer_id === "3838616043"
          && body.seller_sku === "0954"
          && body.publication_targets.join("|") === "shopee:PH|shopee:TH"
          && body.plan_id === "omnichannel:SECRET-PLAN-ID"
          && body.confirmation_token === "PUBLISH-SECRET-TOKEN"
          && body.expected_revision === 31
          && body.payload_digest === "SECRET-PAYLOAD-DIGEST"
          && body.preflight_digest === "SECRET-PREFLIGHT-DIGEST"
          && body.target_label === targetLabel
          && body.confirm_shopee_price_repair === true
          && body.approved_by === "Kyle"
          && !Object.hasOwn(body, "confirm"),
          `Shopee price repair ${viewport.width}/${outcome}: POST is exact, dedicated and single-target`,
          body,
        );

        if (outcome === "success") {
          dashboard = JSON.parse(JSON.stringify(dashboard));
          dashboard.release_v1.run.status = "SUCCEEDED";
          dashboard.release_v1.run.targets[0].status = "SUCCEEDED";
          dashboard.release_v1.run.targets[0].repair = {
            status: "SUCCEEDED",
            result: {
              write_status: "verified",
              listing_price_verified: true,
              derived_price_status: "warning",
              profit_status: "unverified",
            },
          };
          await pendingRepair.fulfill(jsonResponse({
            ok: true,
            idempotent: false,
            target: targetLabel,
            external_writes_performed: ["shopee:update_price"],
          }));
          await page.waitForFunction(() => (
            document.querySelector("#releaseRunLedger")?.textContent.includes(
              "挂牌价已验证 · SIP差异待财务审查",
            )
          ));
        } else if (outcome === "reconciliation") {
          await pendingRepair.fulfill(jsonResponse({
            ok: false,
            error: "accepted but official readback did not converge",
            reconciliation_required: true,
            durable_state_uncertain: false,
            external_writes_performed: ["shopee:update_price"],
          }, 409));
          await page.waitForFunction(
            (selector) => document.querySelector(selector)?.textContent.includes(
              "结果待对账",
            ),
            `${targetSelector} .shopee-price-repair-message`,
          );
        } else {
          await pendingRepair.fulfill(jsonResponse({
            ok: false,
            error: "durable reconciliation receipt unavailable",
            reconciliation_required: true,
            durable_state_uncertain: true,
            external_writes_performed: ["shopee:update_price"],
          }, 502));
          await page.waitForFunction(
            (selector) => document.querySelector(selector)?.textContent.includes(
              "本地回执仍不确定",
            ),
            `${targetSelector} .shopee-price-repair-message`,
          );
        }
        check(
          await page.locator(
            `${targetSelector} [data-price-repair-action="submit"]`,
          ).count() === 0,
          `Shopee price repair ${viewport.width}/${outcome}: terminal result hides repeat repair`,
        );
        await page.waitForTimeout(100);
        check(
          requests.filter((row) => (
            row.method === "POST"
            && new URL(row.url).pathname
              === "/api/product-workspace/release-target/shopee-price-repair"
          )).length === 1,
          `Shopee price repair ${viewport.width}/${outcome}: terminal wait/repeat path adds zero POST`,
          requests,
        );
        const overflow = await overflowAudit(page);
        check(
          overflow.pageOverflow <= 2,
          `Shopee price repair ${viewport.width}/${outcome}: no horizontal overflow`,
          overflow,
        );
        check(
          unexpectedInteractionErrors(errors).length === 0,
          `Shopee price repair ${viewport.width}/${outcome}: no console/page errors`,
          errors,
        );
        check(
          externalRequests.length === 0,
          `Shopee price repair ${viewport.width}/${outcome}: no external network`,
          externalRequests,
        );
      } finally {
        await context.close();
      }
    }

    const stateContext = await browser.newContext({ viewport });
    const statePage = await stateContext.newPage();
    const stateErrors = [];
    const stateRequests = [];
    const stateDashboard = shopeePriceRepairDashboard();
    stateDashboard.release_v1.run.targets = [
      {
        target_label: "shopee:PH",
        status: "RUNNING",
        attempts: 2,
        external_id: "56164935203",
        repair: { status: "RUNNING" },
      },
      {
        target_label: "shopee:TH",
        status: "RECONCILIATION_REQUIRED",
        attempts: 2,
        external_id: "51564925929",
        repair: { status: "RECONCILIATION_REQUIRED" },
      },
    ];
    statePage.on("pageerror", (error) => stateErrors.push(error.message));
    statePage.on("console", (message) => {
      if (message.type() === "error") stateErrors.push(message.text());
    });
    await statePage.route("**/*", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== baseUrl) return route.abort("blockedbyclient");
      if (!url.pathname.startsWith("/api/")) return route.continue();
      stateRequests.push({ method: request.method(), url: request.url() });
      if (url.pathname === "/api/product-workspace/dashboard") {
        return route.fulfill(jsonResponse(stateDashboard));
      }
      return route.fulfill(jsonResponse({ ok: false }, 404));
    });
    try {
      await statePage.goto(
        `${baseUrl}/product-workspace?offer_id=3838616043`,
        { waitUntil: "networkidle" },
      );
      const ledger = await statePage.locator("#releaseRunLedger").innerText();
      check(
        ledger.includes("价格修复执行中 · 禁止重复操作")
        && ledger.includes("挂牌价已写入，等待只读对账")
        && ledger.includes("零平台写入")
        && await statePage.locator(
          '[data-price-repair-action="reconcile"]',
        ).count() === 1,
        `Shopee price repair ${viewport.width}: RUNNING/reconciliation ledger states expose only GET-close action`,
        ledger,
      );
      check(
        await statePage.locator("#publishAllButton").isDisabled()
        && await statePage.locator("#publishAllCheckbox").isDisabled(),
        `Shopee price repair ${viewport.width}: unresolved repair keeps one-click publish disabled`,
      );
      check(
        stateRequests.filter((row) => row.method === "POST").length === 0
        && unexpectedInteractionErrors(stateErrors).length === 0,
        `Shopee price repair ${viewport.width}: lifecycle fixture has zero POST and zero browser errors`,
        { stateRequests, stateErrors },
      );
    } finally {
      await stateContext.close();
    }
  }
}

async function productShopeePriceReconciliationContract(browser) {
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ]) {
    for (const outcome of ["success", "error"]) {
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      const errors = [];
      const requests = [];
      const externalRequests = [];
      let dashboard = shopeePriceRepairDashboard("shopee:PH");
      dashboard.release_v1.run.targets[0].status =
        "RECONCILIATION_REQUIRED";
      dashboard.release_v1.run.targets[0].repair = {
        status: "RECONCILIATION_REQUIRED",
        result: {
          reconciliation_required: true,
          external_writes_performed: ["shopee:update_price"],
        },
      };
      let pendingPreview;
      let resolvePreview;
      const previewRequested = new Promise((resolve) => {
        resolvePreview = resolve;
      });
      let pendingClose;
      let resolveClose;
      const closeRequested = new Promise((resolve) => {
        resolveClose = resolve;
      });
      page.on("pageerror", (error) => {
        errors.push(`pageerror: ${error.message}`);
      });
      page.on("console", (message) => {
        if (message.type() === "error") {
          errors.push(`console: ${message.text()}`);
        }
      });
      await page.route("**/*", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin !== baseUrl) {
          externalRequests.push(request.url());
          return route.abort("blockedbyclient");
        }
        if (!url.pathname.startsWith("/api/")) return route.continue();
        requests.push({
          method: request.method(),
          url: request.url(),
          body: request.postDataJSON?.() || null,
        });
        if (url.pathname === "/api/product-workspace/dashboard") {
          return route.fulfill(jsonResponse(dashboard));
        }
        if (
          url.pathname === (
            "/api/product-workspace/release-target/"
            + "shopee-price-reconciliation-preview"
          )
          && request.method() === "GET"
        ) {
          pendingPreview = route;
          resolvePreview();
          return;
        }
        if (
          url.pathname === (
            "/api/product-workspace/release-target/"
            + "shopee-price-reconciliation"
          )
          && request.method() === "POST"
        ) {
          pendingClose = route;
          resolveClose();
          return;
        }
        return route.fulfill(jsonResponse({ ok: false }, 404));
      });
      try {
        await page.goto(
          `${baseUrl}/product-workspace?offer_id=3838616043`,
          { waitUntil: "networkidle" },
        );
        const targetSelector =
          '[data-price-repair-target="shopee:PH"]';
        const buttonSelector =
          `${targetSelector} [data-price-repair-action="reconcile"]`;
        const initialText = await page.locator(targetSelector).innerText();
        check(
          initialText.includes("挂牌价已写入，等待只读对账")
          && initialText.includes("只读回读并结案")
          && initialText.includes("零平台写入")
          && ![
            "SECRET",
            "0954",
            "56164935203",
            "model",
            "digest",
          ].some((secret) => initialText.includes(secret)),
          `Shopee price reconciliation ${viewport.width}/${outcome}: control is scoped and redacted`,
          initialText,
        );
        check(
          requests.filter((row) => row.method === "POST").length === 0,
          `Shopee price reconciliation ${viewport.width}/${outcome}: initial page performs zero POST`,
          requests,
        );
        const button = page.locator(buttonSelector);
        await button.scrollIntoViewIfNeeded();
        await button.click({ trial: true });
        const started = Date.now();
        await button.click();
        await previewRequested;
        await page.waitForFunction(
          (selector) => document.querySelector(selector)?.textContent.includes(
            "零平台写入",
          ),
          `${targetSelector} .shopee-price-repair-message`,
          { timeout: 500 },
        );
        check(
          Date.now() - started < 500,
          `Shopee price reconciliation ${viewport.width}/${outcome}: pending feedback appears within 500ms`,
        );
        check(
          await page.locator(buttonSelector).isDisabled(),
          `Shopee price reconciliation ${viewport.width}/${outcome}: action disables while GET is pending`,
        );
        const previewUrl = new URL(pendingPreview.request().url());
        check(
          pendingPreview.request().method() === "GET"
          && previewUrl.searchParams.get("offer_id") === "3838616043"
          && previewUrl.searchParams.get("target_label") === "shopee:PH",
          `Shopee price reconciliation ${viewport.width}/${outcome}: preview is exact GET`,
          pendingPreview.request().url(),
        );
        await pendingPreview.fulfill(jsonResponse({
          ok: true,
          reconciliation_allowed: true,
          mode: "official_get_only_durable_close",
          plan_id: "omnichannel:SECRET-PLAN-ID",
          target_label: "shopee:PH",
          expected_revision: 31,
          payload_digest: "SECRET-PAYLOAD-DIGEST",
          preflight_digest: "SECRET-PREFLIGHT-DIGEST",
          operation_digest: "SECRET-OPERATION-DIGEST",
          external_writes_performed: [],
          state_mutations_performed: [],
        }));
        await closeRequested;
        const body = pendingClose.request().postDataJSON();
        const closePosts = requests.filter((row) => (
          row.method === "POST"
          && new URL(row.url).pathname === (
            "/api/product-workspace/release-target/"
            + "shopee-price-reconciliation"
          )
        ));
        check(
          closePosts.length === 1
          && body.offer_id === "3838616043"
          && body.publication_targets.join("|") === "shopee:PH|shopee:TH"
          && body.plan_id === "omnichannel:SECRET-PLAN-ID"
          && body.confirmation_token === "PUBLISH-SECRET-TOKEN"
          && body.expected_revision === 31
          && body.payload_digest === "SECRET-PAYLOAD-DIGEST"
          && body.preflight_digest === "SECRET-PREFLIGHT-DIGEST"
          && body.operation_digest === "SECRET-OPERATION-DIGEST"
          && body.target_label === "shopee:PH"
          && body.confirm_shopee_price_reconciliation === true
          && body.approved_by === "Kyle"
          && !Object.hasOwn(body, "confirm")
          && !Object.hasOwn(body, "confirm_shopee_price_repair"),
          `Shopee price reconciliation ${viewport.width}/${outcome}: POST has dedicated exact identity`,
          body,
        );
        if (outcome === "success") {
          dashboard = JSON.parse(JSON.stringify(dashboard));
          dashboard.release_v1.run.status = "SUCCEEDED";
          dashboard.release_v1.publish_ready = true;
          dashboard.release_v1.run.targets[0].status = "SUCCEEDED";
          dashboard.release_v1.run.targets[0].repair = {
            status: "SUCCEEDED",
            result: {
              write_status: "verified",
              listing_price_verified: true,
              derived_price_status: "warning",
              profit_status: "unverified",
              external_writes_performed: ["shopee:update_price"],
              reconciliation_external_writes_performed: [],
            },
          };
          await pendingClose.fulfill(jsonResponse({
            ok: true,
            idempotent: false,
            target: "shopee:PH",
            write_status: "verified",
            listing_price_verified: true,
            derived_price_status: "warning",
            profit_status: "unverified",
            external_writes_performed: [],
            state_mutations_performed: [
              "release_target_repair:SUCCEEDED",
            ],
          }));
          await page.waitForFunction(() => (
            document.querySelector("#releaseRunLedger")?.textContent.includes(
              "挂牌价已验证 · SIP差异待财务审查",
            )
          ));
          check(
            await page.locator(buttonSelector).count() === 0,
            `Shopee price reconciliation ${viewport.width}: success removes repeat control`,
          );
          check(
            await page.locator("#publishAllCheckbox").isDisabled(),
            `Shopee price reconciliation ${viewport.width}: durable close with no remaining target keeps one-click closed`,
          );
        } else {
          await pendingClose.fulfill(jsonResponse({
            ok: false,
            error: "official local price is not exact",
            external_writes_performed: [],
            state_mutations_performed: [],
          }, 409));
          await page.waitForFunction(
            (selector) => document.querySelector(selector)?.textContent.includes(
              "未再次修价或重发",
            ),
            `${targetSelector} .shopee-price-repair-message`,
          );
          check(
            await page.locator(buttonSelector).isEnabled(),
            `Shopee price reconciliation ${viewport.width}: GET mismatch remains safely retryable`,
          );
        }
        await page.waitForTimeout(100);
        check(
          requests.filter((row) => (
            row.method === "POST"
            && new URL(row.url).pathname === (
              "/api/product-workspace/release-target/"
              + "shopee-price-reconciliation"
            )
          )).length === 1,
          `Shopee price reconciliation ${viewport.width}/${outcome}: terminal wait adds zero repeat POST`,
          requests,
        );
        check(
          requests.every((row) => (
            !row.url.includes("update_price")
            && !row.url.includes("refresh")
          )),
          `Shopee price reconciliation ${viewport.width}/${outcome}: browser journey invokes no update/refresh API`,
          requests,
        );
        const overflow = await overflowAudit(page);
        check(
          overflow.pageOverflow <= 2,
          `Shopee price reconciliation ${viewport.width}/${outcome}: no horizontal overflow`,
          overflow,
        );
        check(
          unexpectedInteractionErrors(errors).length === 0,
          `Shopee price reconciliation ${viewport.width}/${outcome}: no console/page errors`,
          errors,
        );
        check(
          externalRequests.length === 0,
          `Shopee price reconciliation ${viewport.width}/${outcome}: no external network`,
          externalRequests,
        );
      } finally {
        await context.close();
      }
    }
  }
}

function targetScopedReleaseDashboard() {
  const dashboard = shopeePriceRepairDashboard("shopee:MY");
  const targetLabel = "shopee:MY";
  dashboard.publication_scope.selected_labels = [targetLabel];
  dashboard.publication_scope.default_labels = [targetLabel];
  dashboard.publication_scope.available_targets = [{
    label: targetLabel,
    channel: "shopee",
    shop: "LivelyHive",
    country: "MY",
  }];
  dashboard.release_v1.plan.targets = [targetLabel];
  dashboard.release_v1.plan.payload.targets = [targetLabel];
  dashboard.release_v1.run.run_id = "release-run:target-scoped-fixture";
  dashboard.release_v1.run.targets = [{
    target_label: targetLabel,
    status: "FAILED",
    attempts: 1,
    external_id: "",
    error: "official pre-submit validation failed; no external write",
    latest_failure_evidence: {
      evidence: {
        phase: "pre_submit",
        pre_submit_failure: true,
        external_writes_performed: [],
      },
    },
  }];
  return dashboard;
}

async function productTargetScopedReleaseContract(browser) {
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ]) {
    for (const outcome of ["success", "reconciliation"]) {
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      const errors = [];
      const requests = [];
      const externalRequests = [];
      let dashboard = targetScopedReleaseDashboard();
      let pendingPreview;
      let resolvePreview;
      const previewRequested = new Promise((resolve) => {
        resolvePreview = resolve;
      });
      let pendingSubmit;
      let resolveSubmit;
      const submitRequested = new Promise((resolve) => {
        resolveSubmit = resolve;
      });
      page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
      page.on("console", (message) => {
        if (message.type() === "error") errors.push(`console: ${message.text()}`);
      });
      await page.route("**/*", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin !== baseUrl) {
          externalRequests.push(request.url());
          return route.abort("blockedbyclient");
        }
        if (!url.pathname.startsWith("/api/")) return route.continue();
        requests.push({
          method: request.method(),
          url: request.url(),
          body: request.postDataJSON?.() || null,
        });
        if (url.pathname === "/api/product-workspace/dashboard") {
          return route.fulfill(jsonResponse(dashboard));
        }
        if (
          url.pathname.endsWith("/target-scoped-action-preview")
          && request.method() === "GET"
        ) {
          pendingPreview = route;
          resolvePreview();
          return;
        }
        if (
          url.pathname.endsWith("/target-scoped-action")
          && request.method() === "POST"
        ) {
          pendingSubmit = route;
          resolveSubmit();
          return;
        }
        return route.fulfill(jsonResponse({ ok: false }, 404));
      });
      try {
        await page.goto(
          `${baseUrl}/product-workspace?offer_id=3838616043`,
          { waitUntil: "networkidle" },
        );
        const panel = '[data-target-scoped-target="shopee:MY"]';
        const previewButton =
          `${panel} [data-target-scoped-action="preview"]`;
        check(
          await page.locator(panel).count() === 1
          && await page.locator(
            `${panel} [data-target-scoped-action="submit"]`,
          ).count() === 0,
          `target scoped ${viewport.width}/${outcome}: execute is hidden before official preview`,
        );
        check(
          requests.filter((row) => row.method === "POST").length === 0,
          `target scoped ${viewport.width}/${outcome}: initial page has zero POST`,
          requests,
        );

        const previewStarted = Date.now();
        await page.locator(previewButton).click();
        await previewRequested;
        check(
          Date.now() - previewStarted < 500,
          `target scoped ${viewport.width}/${outcome}: preview feedback starts within 500ms`,
        );
        check(
          await page.locator(previewButton).isDisabled(),
          `target scoped ${viewport.width}/${outcome}: preview control disables while GET is pending`,
        );
        const previewUrl = new URL(pendingPreview.request().url());
        check(
          previewUrl.searchParams.get("offer_id") === "3838616043"
          && previewUrl.searchParams.get("target_label") === "shopee:MY",
          `target scoped ${viewport.width}/${outcome}: preview GET is exact and single-target`,
          pendingPreview.request().url(),
        );
        await pendingPreview.fulfill(jsonResponse({
          ok: true,
          preview: true,
          available: true,
          target_label: "shopee:MY",
          operation_kind: "shopee_safe_pre_submit_retry_v1",
          plan_id: "omnichannel:SECRET-PLAN-ID",
          expected_revision: 31,
          payload_digest: "SECRET-PAYLOAD-DIGEST",
          planned_command_digest: "SECRET-COMMAND-DIGEST",
          preflight_digest: "SECRET-PREFLIGHT-DIGEST",
          proof_digest: "SECRET-PROOF-DIGEST",
          failure_attempt: 1,
          summary: { target: "shopee:MY", state: "safe" },
          external_writes_performed: [],
        }));
        const confirm = page.locator(`${panel} [data-target-scoped-confirm]`);
        await confirm.check();
        const submit =
          page.locator(`${panel} [data-target-scoped-action="submit"]`);
        check(
          await submit.isEnabled(),
          `target scoped ${viewport.width}/${outcome}: dedicated consent enables one target only`,
        );
        await submit.click();
        await submitRequested;
        check(
          await page.locator(
            `${panel} [data-target-scoped-action="submit"]`,
          ).isDisabled(),
          `target scoped ${viewport.width}/${outcome}: execute disables while POST is pending`,
        );
        const actionPosts = requests.filter((row) => (
          row.method === "POST"
          && new URL(row.url).pathname.endsWith("/target-scoped-action")
        ));
        const body = pendingSubmit.request().postDataJSON();
        check(
          actionPosts.length === 1
          && body.target_label === "shopee:MY"
          && body.publication_targets.join("|") === "shopee:MY"
          && body.confirm_target_scoped_action === true
          && body.approved_by === "Kyle"
          && body.expected_revision === 31
          && body.planned_command_digest === "SECRET-COMMAND-DIGEST"
          && body.preflight_digest === "SECRET-PREFLIGHT-DIGEST"
          && body.proof_digest === "SECRET-PROOF-DIGEST"
          && !Object.hasOwn(body, "planned_command")
          && !Object.hasOwn(body, "confirm"),
          `target scoped ${viewport.width}/${outcome}: POST is dedicated, exact and single`,
          body,
        );
        if (outcome === "success") {
          dashboard = JSON.parse(JSON.stringify(dashboard));
          dashboard.release_v1.run.status = "SUCCEEDED";
          dashboard.release_v1.run.targets[0].status = "SUCCEEDED";
          await pendingSubmit.fulfill(jsonResponse({
            ok: true,
            code: "target_scoped_action_succeeded",
            operation_status: "SUCCEEDED",
            external_writes_performed: ["shopee:regional_publish"],
          }));
          await page.waitForFunction(() => (
            document.querySelector(
              '[data-target-scoped-target="shopee:MY"]',
            ) === null
          ));
        } else {
          await pendingSubmit.fulfill(jsonResponse({
            ok: false,
            code: "target_scoped_reconciliation_required",
            operation_status: "RECONCILIATION_REQUIRED",
            durable_state_uncertain: true,
            reconciliation_required: true,
            external_writes_performed: ["shopee:regional_publish"],
          }, 409));
          await page.waitForTimeout(100);
        }
        check(
          requests.filter((row) => (
            row.method === "POST"
            && new URL(row.url).pathname.endsWith("/target-scoped-action")
          )).length === 1,
          `target scoped ${viewport.width}/${outcome}: terminal path adds zero repeat POST`,
          requests,
        );
        check(
          await page.locator("#publishAllButton").isDisabled(),
          `target scoped ${viewport.width}/${outcome}: generic publish remains isolated`,
        );
        const overflow = await overflowAudit(page);
        check(
          overflow.pageOverflow <= 2,
          `target scoped ${viewport.width}/${outcome}: no horizontal overflow`,
          overflow,
        );
        check(
          unexpectedInteractionErrors(errors).length === 0,
          `target scoped ${viewport.width}/${outcome}: no console/page errors`,
          errors,
        );
        check(
          externalRequests.length === 0,
          `target scoped ${viewport.width}/${outcome}: no external network`,
          externalRequests,
        );
      } finally {
        await context.close();
      }
    }
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
    await page.route("**/api/product-flow/content-package/review", (route) => {
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

async function aiPlanningBlockerFeedback(browser, viewport) {
  const blockedPreview = JSON.parse(JSON.stringify(aiPreview));
  const sourceUrl = "https://fixture.invalid/source-identity.jpg";
  blockedPreview.offer_id = "3838614276";
  blockedPreview.revision = 8;
  blockedPreview.source.offer_id = "3838614276";
  blockedPreview.source.images = [sourceUrl];
  blockedPreview.review.image_actions = [{ url: sourceUrl, action: "review" }];
  blockedPreview.content_package.collect_box_id = "3838614276";
  blockedPreview.content_package.source_snapshot = {
    image_urls: [sourceUrl],
    identity_reference_urls: [],
    primary_identity_image: "",
  };
  blockedPreview.content_package.remaining_images_preflight = { status: "not_started", total: 0 };
  const scenario = await openScenario(
    browser,
    "/ai-image-studio?offer_id=3838614276",
    viewport,
    { aiPreview: blockedPreview },
  );
  const { page, context, errors, requests } = scenario;
  try {
    const started = Date.now();
    await page.locator("#aiPlanButton").click();
    await page.waitForFunction(() => (
      !document.querySelector("#planningProgress")?.hidden
      && document.querySelector("#planningProgress")?.classList.contains("failed")
    ));
    const feedbackMs = Date.now() - started;
    const planningText = (await page.locator("#planningProgress").innerText()).trim();
    check(
      feedbackMs < 500,
      `AI planner blocker ${viewport.width}: local feedback is visible within 500ms`,
      feedbackMs,
    );
    check(
      planningText.includes("身份参考图") && planningText.includes("未发送 AI 规划请求"),
      `AI planner blocker ${viewport.width}: missing identity reason is beside the action`,
      planningText,
    );
    check(
      await computedVisibility(page, "#planningProgressAction"),
      `AI planner blocker ${viewport.width}: identity guidance action is visible`,
    );
    check(
      await page.locator(".identity-reference").evaluate((node) => document.activeElement === node),
      `AI planner blocker ${viewport.width}: focus moves to identity-reference control`,
    );
    check(
      requests.filter((row) => row.method === "POST").length === 0,
      `AI planner blocker ${viewport.width}: blocker sends zero POST requests`,
      requests,
    );
    await page.locator(".identity-reference").check();
    await page.waitForFunction(() => (
      !document.querySelector("#planningProgress")?.classList.contains("failed")
    ));
    check(
      !(await page.locator("#planningProgress").innerText()).includes("身份参考图"),
      `AI planner blocker ${viewport.width}: selecting a reference clears the stale blocker`,
    );
    let savedReview = null;
    await page.route("**/api/product-flow/content-package/review", async (route) => {
      savedReview = route.request().postDataJSON().review;
      blockedPreview.revision = 9;
      blockedPreview.review.image_actions = savedReview.image_actions;
      blockedPreview.review.image_order = savedReview.image_order;
      blockedPreview.content_package.source_snapshot.identity_reference_urls = savedReview.identity_reference_urls;
      blockedPreview.content_package.source_snapshot.primary_identity_image = savedReview.primary_identity_url;
      await route.fulfill(jsonResponse({ ok: true, content_package: blockedPreview.content_package }));
    });
    await page.locator("#saveSourceButton").click();
    await page.waitForFunction(() => !document.querySelector("#saveSourceButton")?.classList.contains("is-loading"));
    check(
      savedReview?.expected_revision === 8
      && savedReview.identity_reference_urls?.length === 1
      && savedReview.primary_identity_url === sourceUrl
      && savedReview.image_actions?.[0]?.action === "keep",
      `AI identity save ${viewport.width}: one CAS payload atomically carries source and identity decisions`,
      savedReview,
    );
    check(
      await page.locator(".identity-reference").isChecked()
      && await page.locator(".identity-primary").isChecked(),
      `AI identity save ${viewport.width}: saved identity and primary choices survive reload`,
    );
    const overflow = await overflowAudit(page);
    check(
      overflow.pageOverflow <= 2,
      `AI planner blocker ${viewport.width}: no horizontal overflow`,
      overflow,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      `AI planner blocker ${viewport.width}: no console/page errors`,
      errors,
    );
  } finally {
    await context.close();
  }
}

async function aiMissingPackageFeedback(browser, viewport) {
  const blockedPreview = JSON.parse(JSON.stringify(aiPreview));
  const sourceUrl = "https://fixture.invalid/source-identity.jpg";
  blockedPreview.offer_id = "3838614276";
  blockedPreview.revision = 8;
  blockedPreview.source.offer_id = "3838614276";
  blockedPreview.source.images = [sourceUrl];
  blockedPreview.review.image_actions = [{ url: sourceUrl, action: "keep" }];
  blockedPreview.content_package.package_found = false;
  blockedPreview.content_package.collect_box_id = "3838614276";
  blockedPreview.content_package.source_snapshot = {
    image_urls: [sourceUrl],
    identity_reference_urls: [sourceUrl],
    primary_identity_image: sourceUrl,
  };
  blockedPreview.content_package.remaining_images_preflight = {
    status: "not_started",
    total: 0,
  };
  const scenario = await openScenario(
    browser,
    "/ai-image-studio?offer_id=3838614276",
    viewport,
    { aiPreview: blockedPreview },
  );
  const { page, context, errors, requests } = scenario;
  try {
    await page.locator("#aiPlanButton").click();
    await page.waitForFunction(() => (
      !document.querySelector("#planningProgress")?.hidden
      && document.querySelector("#planningProgress")?.classList.contains("failed")
    ));
    const planningText = (await page.locator("#planningProgress").innerText()).trim();
    check(
      planningText.includes("还没有创建本地内容审核包")
      && planningText.includes("没有调用 AI")
      && planningText.includes("没有产生生图费用"),
      `AI package blocker ${viewport.width}: explains the missing local step and zero-cost outcome`,
      planningText,
    );
    check(
      (await page.locator("#planningProgressAction").innerText()).includes("先创建本地内容审核包"),
      `AI package blocker ${viewport.width}: provides the exact next action`,
    );
    check(
      requests.filter((row) => row.method === "POST").length === 0,
      `AI package blocker ${viewport.width}: sends zero POST requests`,
      requests,
    );
    const overflow = await overflowAudit(page);
    check(
      overflow.pageOverflow <= 2,
      `AI package blocker ${viewport.width}: no horizontal overflow`,
      overflow,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      `AI package blocker ${viewport.width}: no console/page errors`,
      errors,
    );
  } finally {
    await context.close();
  }
}

async function productWorkflowNextActionContract(browser, viewport) {
  const dashboard = JSON.parse(JSON.stringify(productDashboard));
  dashboard.product.actual_product_approved = true;
  dashboard.product.fields_locked = true;
  dashboard.content = {
    approved: true,
    approval_status: "approved",
    image_count: 1,
    images: [{ position: 1, image_url: "https://fixture.invalid/main.jpg" }],
    blockers: [],
  };
  dashboard.actual_release_gate = {
    ready: false,
    blockers: ["The previous 11-image Miaoshou write is stale."],
  };
  dashboard.release_v1 = {
    eligible_for_plan_approval: true,
    plan_approved: true,
    plan_persisted: true,
    miaoshou_prepared: true,
    canonical_common_ready: true,
    common_evidence_blockers: [],
    release_preflight_authority: "canonical_common_readback",
    publish_ready: true,
    adapter_blockers: [],
    blockers: [],
    plan: {
      plan_id: "plan:workflow-fixture",
      confirmation_token: "PUBLISH-TEST-0001",
      payload_digest: "a".repeat(64),
      payload: {
        product_revision: 1,
        content_package_id: "content:workflow",
        targets: ["miaoshou:COMMON", "tiktok:MX"],
      },
      targets: ["miaoshou:COMMON", "tiktok:MX"],
    },
    run: {
      run_id: "release-run:workflow",
      status: "RUNNING",
      targets: [
        {
          target_label: "miaoshou:COMMON",
          status: "SUCCEEDED",
          attempts: 1,
          external_id: "3828540231",
        },
        {
          target_label: "tiktok:MX",
          status: "PENDING",
          attempts: 0,
        },
      ],
    },
  };
  dashboard.workflow_next_action = {
    schema_version: "product-workflow-next-action/v1",
    code: "publish_selected_targets",
    phase: "channels",
    label: "确认并发布剩余店铺",
    detail: "当前计划与妙手回读一致；成功目标会自动跳过，只执行仍为 PENDING 的店铺。",
    kind: "control",
    actionable: true,
    terminal: false,
    control_id: "publishAllCheckbox",
    reason_codes: ["pending_targets_ready"],
  };
  const scenario = await openScenario(
    browser,
    "/product-workspace?offer_id=3828540231",
    viewport,
    { productDashboard: dashboard },
  );
  const { page, context, errors, requests } = scenario;
  try {
    const button = page.locator("#nextStepActionButton");
    check(
      await button.isVisible() && await button.isEnabled(),
      `workflow ${viewport.width}: server-owned next action is visible and enabled`,
    );
    check(
      (await button.innerText()).includes("发布剩余店铺"),
      `workflow ${viewport.width}: next action copy comes from server contract`,
      await button.innerText(),
    );
    check(
      await page.locator("#publishAllCheckbox").isEnabled(),
      `workflow ${viewport.width}: canonical COMMON readback opens publish consent despite stale legacy gate`,
    );
    const blockers = (await page.locator("#blockerList").innerText()).trim();
    check(
      !blockers.includes("11-image") && !blockers.includes("11 图"),
      `workflow ${viewport.width}: superseded legacy blocker is not presented as active truth`,
      blockers,
    );
    await button.click();
    check(
      await page.locator("#publishAllCheckbox").evaluate(
        (element) => document.activeElement === element,
      ),
      `workflow ${viewport.width}: primary next action leads to the enabled consent control`,
    );
    check(
      requests.filter((row) => row.method === "POST").length === 0,
      `workflow ${viewport.width}: navigation to next action performs zero writes`,
      requests,
    );
    const overflow = await overflowAudit(page);
    check(
      overflow.pageOverflow <= 2,
      `workflow ${viewport.width}: no horizontal overflow`,
      overflow,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      `workflow ${viewport.width}: no console/page errors`,
      errors,
    );
  } finally {
    await context.close();
  }
}

async function mixedReleaseDispositionContract(browser, viewport) {
  const dashboard = JSON.parse(JSON.stringify(productDashboard));
  dashboard.product.actual_product_approved = true;
  dashboard.product.fields_locked = true;
  dashboard.content = {
    approved: true,
    approval_status: "approved",
    image_count: 5,
    images: Array.from({ length: 5 }, (_, index) => ({
      position: index + 1,
      image_url: `https://fixture.invalid/${index + 1}.jpg`,
    })),
    blockers: [],
  };
  dashboard.actual_release_gate = { ready: true, blockers: [] };
  dashboard.release_v1 = {
    eligible_for_plan_approval: true,
    plan_approved: true,
    plan_persisted: true,
    miaoshou_prepared: true,
    canonical_common_ready: true,
    publish_ready: true,
    runnable_target_count: 1,
    adapter_blockers: [],
    blockers: [],
    plan: {
      plan_id: "plan:mixed-release",
      confirmation_token: "PUBLISH-MIXED-0001",
      payload_digest: "b".repeat(64),
      payload: {
        product_revision: 1,
        content_package_id: "content:mixed",
        targets: [
          "miaoshou:COMMON",
          "tiktok:LH_MY",
          "tiktok:MX",
          "tiktok:GB",
          "shopee:MY",
          "shopee:VN",
        ],
      },
      targets: [
        "miaoshou:COMMON",
        "tiktok:LH_MY",
        "tiktok:MX",
        "tiktok:GB",
        "shopee:MY",
        "shopee:VN",
      ],
    },
    run: {
      run_id: "release-run:mixed",
      status: "PARTIAL_FAILED",
      targets: [
        { target_label: "miaoshou:COMMON", status: "SUCCEEDED", attempts: 1 },
        { target_label: "tiktok:LH_MY", status: "FAILED", attempts: 1 },
        {
          target_label: "tiktok:MX",
          status: "SUBMITTED_UNVERIFIED",
          attempts: 1,
        },
        {
          target_label: "tiktok:GB",
          status: "SUBMITTED_UNVERIFIED",
          attempts: 1,
        },
        { target_label: "shopee:VN", status: "FAILED", attempts: 1 },
        { target_label: "shopee:MY", status: "PENDING", attempts: 0 },
      ],
    },
  };
  dashboard.workflow_next_action = {
    schema_version: "product-workflow-next-action/v1",
    code: "publish_selected_targets",
    phase: "channels",
    label: "继续发布 1 个安全目标",
    detail:
      "本次只执行 1 个从未提交目标；2 个待对账、2 个待人工验收目标保持原状态且不会重发。",
    kind: "control",
    actionable: true,
    terminal: false,
    control_id: "publishAllCheckbox",
    reason_codes: ["runnable_release_targets_available"],
    target_counts: {
      running: 0,
      reconciliation: 2,
      manual_acceptance: 2,
      pending: 1,
    },
  };
  const scenario = await openScenario(
    browser,
    "/product-workspace?offer_id=3845133620",
    viewport,
    { productDashboard: dashboard },
  );
  const { page, context, errors, requests } = scenario;
  try {
    const button = page.locator("#nextStepActionButton");
    check(
      await button.isVisible() && await button.isEnabled(),
      `mixed release ${viewport.width}: remaining first attempt is visible`,
    );
    check(
      (await button.innerText()).includes("继续发布 1 个安全目标"),
      `mixed release ${viewport.width}: first attempt is not hidden by other outcomes`,
      await button.innerText(),
    );
    check(
      (await page.locator(".next-panel").innerText()).includes("2 个待人工验收")
        && (await page.locator(".next-panel").innerText()).includes("1 个从未提交"),
      `mixed release ${viewport.width}: mixed target counts remain visible`,
      await page.locator(".next-panel").innerText(),
    );
    await button.click();
    check(
      await page.locator("#publishAllCheckbox").isEnabled()
        && await page.locator("#publishAllCheckbox").evaluate(
          (element) => document.activeElement === element,
        ),
      `mixed release ${viewport.width}: action focuses the enabled one-click consent`,
    );
    check(
      requests.filter((row) => row.method === "POST").length === 0,
      `mixed release ${viewport.width}: disposition navigation performs zero writes`,
      requests,
    );
    const overflow = await overflowAudit(page);
    check(
      overflow.pageOverflow <= 2,
      `mixed release ${viewport.width}: no horizontal overflow`,
      overflow,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      `mixed release ${viewport.width}: no console/page errors`,
      errors,
    );
  } finally {
    await context.close();
  }
}

async function blockedCapabilityNextActionContract(browser, viewport) {
  const dashboard = JSON.parse(JSON.stringify(productDashboard));
  dashboard.product.actual_product_approved = true;
  dashboard.product.fields_locked = true;
  dashboard.content = {
    approved: true,
    approval_status: "approved",
    image_count: 1,
    images: [{ position: 1, image_url: "https://fixture.invalid/main.jpg" }],
    blockers: [],
  };
  dashboard.actual_release_gate = { ready: true, blockers: [] };
  dashboard.release_v1 = {
    eligible_for_plan_approval: true,
    plan_approved: true,
    plan_persisted: true,
    miaoshou_prepared: true,
    canonical_common_ready: true,
    publish_ready: true,
    runnable_target_count: 0,
    adapter_blockers: [],
    blockers: [],
    target_recovery_actions: [
      {
        schema_version: "release-target-recovery-action/v1",
        target_label: "ozon:RU",
        status: "PENDING",
        attempts: 0,
        action_kind: "BLOCKED_CAPABILITY",
        runnable: false,
        reason_code: "automatic_first_attempt_capability_unavailable",
      },
    ],
    plan: {
      plan_id: "plan:blocked-capability",
      confirmation_token: "PUBLISH-BLOCKED-0001",
      payload_digest: "c".repeat(64),
      payload: {
        product_revision: 1,
        content_package_id: "content:blocked",
        targets: ["miaoshou:COMMON", "ozon:RU"],
      },
      targets: ["miaoshou:COMMON", "ozon:RU"],
    },
    run: {
      run_id: "release-run:blocked",
      status: "RUNNING",
      targets: [
        { target_label: "miaoshou:COMMON", status: "SUCCEEDED", attempts: 1 },
        { target_label: "ozon:RU", status: "PENDING", attempts: 0 },
      ],
    },
  };
  dashboard.workflow_next_action = {
    schema_version: "product-workflow-next-action/v1",
    code: "resolve_release_capability",
    phase: "channels",
    label: "查看 Ozon 安全阻断",
    detail: "Ozon 当前缺少受治理的自动首发能力，请查看店铺卡片中的唯一解决方案。",
    kind: "manual",
    actionable: true,
    terminal: false,
    control_id: "releaseRunLedger",
    focus_target_label: "ozon:RU",
    reason_codes: ["adapter_capability_blocked"],
    target_counts: {
      running: 0,
      reconciliation: 0,
      manual_acceptance: 0,
      pending: 0,
      blocked_capability: 1,
    },
  };
  const scenario = await openScenario(
    browser,
    "/product-workspace?offer_id=3845133620",
    viewport,
    { productDashboard: dashboard },
  );
  const { page, context, errors, requests } = scenario;
  try {
    const nextButton = page.locator("#nextStepActionButton");
    const publishCheckbox = page.locator("#publishAllCheckbox");
    const ozonCard = page.locator(
      '.run-target[data-target-label="ozon:RU"]',
    );
    check(
      await nextButton.isVisible() && await nextButton.isEnabled(),
      `blocked capability ${viewport.width}: safe next action is visible`,
    );
    check(
      !(await publishCheckbox.isEnabled()),
      `blocked capability ${viewport.width}: one-click publish is not a dead-end control`,
    );
    check(
      (await ozonCard.innerText()).includes("库存决策")
        && (await ozonCard.innerText()).includes("默认库存"),
      `blocked capability ${viewport.width}: card explains the unique safe resolution`,
      await ozonCard.innerText(),
    );
    check(
      (await page.locator(".run-ledger-head").innerText()).includes(
        "1 个能力阻断",
      ),
      `blocked capability ${viewport.width}: ledger header does not claim active execution`,
      await page.locator(".run-ledger-head").innerText(),
    );
    await nextButton.click();
    check(
      await ozonCard.evaluate((element) => document.activeElement === element),
      `blocked capability ${viewport.width}: next action focuses the exact target card`,
    );
    check(
      requests.filter((row) => row.method === "POST").length === 0,
      `blocked capability ${viewport.width}: navigation performs zero writes`,
      requests,
    );
    const overflow = await overflowAudit(page);
    check(
      overflow.pageOverflow <= 2,
      `blocked capability ${viewport.width}: no horizontal overflow`,
      overflow,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      `blocked capability ${viewport.width}: no console/page errors`,
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
    await productQueueLongTitleMobileContract(browser);
    await productLockedTitleAdoption(browser);
    await productPreservedTitleApprovalReload(browser);
    await productLockedStaleTitleRefresh(browser);
    await productMultiTabTitleRefreshConflict(browser);
    await productReleaseTerminalState(browser);
    await productReleasePartialFailedLedger(browser);
    await productShopeePriceRepairContract(browser);
    await productShopeePriceReconciliationContract(browser);
    await productTargetScopedReleaseContract(browser);
    await productCommonOverwriteContract(browser);
    await aiAsyncFeedback(browser);
    await aiPlanningBlockerFeedback(browser, { width: 1440, height: 900 });
    await aiPlanningBlockerFeedback(browser, { width: 390, height: 844 });
    await aiMissingPackageFeedback(browser, { width: 1440, height: 900 });
    await aiMissingPackageFeedback(browser, { width: 390, height: 844 });
    await productWorkflowNextActionContract(browser, { width: 1440, height: 900 });
    await productWorkflowNextActionContract(browser, { width: 390, height: 844 });
    await mixedReleaseDispositionContract(browser, { width: 1440, height: 900 });
    await mixedReleaseDispositionContract(browser, { width: 390, height: 844 });
    await blockedCapabilityNextActionContract(browser, { width: 1440, height: 900 });
    await blockedCapabilityNextActionContract(browser, { width: 390, height: 844 });
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
