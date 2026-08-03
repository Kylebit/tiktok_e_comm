"use strict";

const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

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
  if (path === "/api/product-workspace/publish-preview") {
    return jsonResponse({
      ok: true,
      available: false,
      start_allowed: false,
      external_writes_performed: [],
    });
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

async function productReleasePlanSingleApprovalAction(browser) {
  const dashboard = JSON.parse(JSON.stringify(productDashboard));
  dashboard.product.fields_locked = true;
  dashboard.product.actual_product_approved = true;
  dashboard.content = {
    approved: true,
    image_count: 5,
    images: [],
    blockers: [],
  };
  dashboard.actual_release_gate = { ready: true, blockers: [] };
  dashboard.release_v1 = {
    eligible_for_plan_approval: true,
    plan_persisted: false,
    plan_approved: false,
    miaoshou_prepared: false,
    publish_ready: false,
    blockers: [],
    plan: {
      plan_id: "omnichannel:single-approval-action",
      confirmation_token: "PUBLISH-SINGLE-APPROVAL",
      payload_digest: "a".repeat(64),
      targets: ["miaoshou:COMMON"],
      payload: {
        product_revision: 1,
        content_package_id: "content:single-approval-action",
        targets: ["miaoshou:COMMON"],
      },
    },
    run: null,
  };
  const scenario = await openScenario(
    browser,
    "/product-workspace?offer_id=3828540231",
    { width: 1440, height: 900 },
    { productDashboard: dashboard },
  );
  const { page, context, errors, requests } = scenario;
  let approvalRequest = null;
  await page.route(
    "**/api/product-workspace/release-plan/approve",
    async (route) => {
      approvalRequest = route.request().postDataJSON();
      const approved = JSON.parse(JSON.stringify(dashboard));
      approved.release_v1.plan_persisted = true;
      approved.release_v1.plan_approved = true;
      approved.release_v1.eligible_for_plan_approval = false;
      approved.release_v1.plan.approval = {
        status: "APPROVED",
        approved_by: "Kyle",
      };
      await route.fulfill(jsonResponse({
        ok: true,
        dashboard: approved,
        external_writes_performed: [],
      }));
    },
  );
  try {
    const button = page.locator("#approveReleasePlanButton");
    check(
      await button.isEnabled()
      && await page.locator("#releasePlanCheckbox").isHidden(),
      "product: an eligible ReleasePlan exposes one direct approval action without a prerequisite checkbox",
    );
    await button.click();
    await page.waitForFunction(() => (
      document.querySelector("#releasePlanMessage")
        ?.textContent.includes("已由 Kyle 批准并持久化")
    ));
    check(
      approvalRequest?.approved_by === "Kyle"
      && approvalRequest?.user_approved === true,
      "product: direct approval sends the exact Kyle consent once",
      approvalRequest,
    );
    check(
      requests.filter((row) => (
        row.method === "POST"
        && !row.url.includes("/release-plan/approve")
      )).length === 0,
      "product: direct plan approval sends no publish or channel POST",
      requests,
    );
    check(
      errors.length === 0,
      "product single approval action: no console/page errors",
      errors,
    );
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
    if (url.pathname === "/api/product-workspace/publish-preview") {
      return route.fulfill(jsonResponse({
        ok: true,
        available: false,
        start_allowed: false,
        external_writes_performed: [],
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
    const approvalButton = page.locator("#approveReleasePlanButton");
    const recovery = page.locator(
      '[data-release-recovery="refresh_listing_copy"]',
    );
    check(
      await approvalButton.isDisabled()
      && await recovery.isEnabled()
      && await recovery.isVisible(),
      "product: blocked approval exposes an enabled recovery action beside the disabled approval button",
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
    check(
      await approvalButton.isDisabled(),
      "product: release approval remains disabled while EN MASTER adoption is in flight",
    );
    finishAdoption();
    await page.waitForFunction(
      () => document.querySelector("#approveReleasePlanButton")?.disabled === false,
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
      await approvalButton.isEnabled()
      && await page.locator("#releasePlanRecovery").isHidden(),
      "product: successful adoption directly releases the single approval action without a reload",
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

async function sourceOnlyFinalApprovalContract(browser, viewport) {
  const sourcePreview = JSON.parse(JSON.stringify(aiPreview));
  const sourceA = "https://fixture.invalid/source-a.jpg";
  const sourceB = "https://fixture.invalid/source-b.jpg";
  sourcePreview.offer_id = "3845131687";
  sourcePreview.revision = 31;
  sourcePreview.source.offer_id = "3845131687";
  sourcePreview.source.images = [
    { url: sourceA, kind: "main" },
    { url: sourceB, kind: "detail" },
  ];
  sourcePreview.source.video = { url: "", action: "none" };
  sourcePreview.review.image_actions = [
    { url: sourceA, action: "keep", note: "" },
    { url: sourceB, action: "keep", note: "" },
  ];
  sourcePreview.review.image_order = [sourceA, sourceB];
  sourcePreview.review.video_action = "none";
  sourcePreview.content_package.content_strategy = "source_only";
  sourcePreview.content_package.fact_card_approved = false;
  sourcePreview.content_package.planning_scope_approved = false;
  sourcePreview.content_package.source_only_ready = true;
  sourcePreview.content_package.source_only_final_approved = false;
  sourcePreview.content_package.content_approved = false;
  sourcePreview.workflow.current_label = "最终内容批准";
  sourcePreview.workflow.content_ready = false;
  sourcePreview.workflow.image_review_ready = true;

  const scenario = await openScenario(
    browser,
    "/ai-image-studio?offer_id=3845131687",
    viewport,
    { aiPreview: sourcePreview },
  );
  const { page, context, errors, requests, state } = scenario;
  const savedPayloads = [];
  try {
    await page.route(
      "**/api/product-flow/content-package/source-only/review",
      async (route) => {
        const payload = route.request().postDataJSON();
        savedPayloads.push(payload);
        const approved = payload.review.confirm_final_content_approval === true;
        state.aiPreview = {
          ...state.aiPreview,
          revision: Number(state.aiPreview.revision || 0) + 1,
          review: {
            ...state.aiPreview.review,
            ...payload.review,
            video_url: "",
          },
          content_package: {
            ...state.aiPreview.content_package,
            fact_card_approved: approved,
            planning_scope_approved: approved,
            source_only_final_approved: approved,
            content_approved: approved,
          },
          workflow: {
            ...state.aiPreview.workflow,
            current_label: approved ? "价格与发布信息" : "最终内容批准",
            content_ready: approved,
          },
        };
        await route.fulfill(jsonResponse(state.aiPreview));
      },
    );

    check(
      (await page.locator("#saveOrderButton").innerText()).includes("保存并批准最终内容"),
      `source-only approval ${viewport.width}: exact final action is visible`,
    );
    check(
      (await page.locator("#flowRail").innerText()).includes("最终内容批准")
      && (await page.locator("#sourceOnlySaveStatus").innerText()).includes("请点击"),
      `source-only approval ${viewport.width}: pending gate explains the next action`,
    );

    await page.locator("#saveOrderButton").click();
    await page.waitForFunction(() => (
      !document.querySelector("#saveOrderButton")?.classList.contains("is-loading")
    ));
    check(
      savedPayloads.length === 1
      && savedPayloads[0].review.confirm_final_content_approval === true
      && savedPayloads[0].review.approved_by === "Kyle",
      `source-only approval ${viewport.width}: explicit action sends one governed approval`,
      savedPayloads,
    );
    check(
      (await page.locator("#sourceOnlySaveStatus").innerText()).includes("已保存并批准最终内容")
      && (await page.locator("#flowRail").innerText()).includes("最终内容批准"),
      `source-only approval ${viewport.width}: approved state is rendered`,
    );

    await page.locator(".source-remove").nth(1).click();
    await page.waitForFunction(() => (
      document.querySelectorAll("#sourceGrid .asset-card").length === 1
      && !document.querySelector("#saveOrderButton")?.classList.contains("is-loading")
    ));
    check(
      savedPayloads.length === 2
      && savedPayloads[1].review.confirm_final_content_approval === false
      && !("approved_by" in savedPayloads[1].review),
      `source-only approval ${viewport.width}: automatic draft save never silently approves`,
      savedPayloads,
    );
    check(
      (await page.locator("#sourceOnlySaveStatus").innerText()).includes("最终内容尚未批准"),
      `source-only approval ${viewport.width}: source drift visibly invalidates approval`,
    );
    check(
      savedPayloads.length === 2,
      `source-only approval ${viewport.width}: one approval and one draft save only`,
      savedPayloads,
    );
    const overflow = await overflowAudit(page);
    check(
      overflow.pageOverflow <= 2,
      `source-only approval ${viewport.width}: no horizontal overflow`,
      overflow,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      `source-only approval ${viewport.width}: no console/page errors`,
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
  return collectboxStepOnePrimaryActionContract(browser, viewport);
  /* Historical direct-store fixture retained below for migration reference. */
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
      await page.locator("#releasePrimaryActionButton").isEnabled(),
      `workflow ${viewport.width}: approved plan exposes the unified Miaoshou action`,
    );
    const blockers = (await page.locator("#blockerList").innerText()).trim();
    check(
      !blockers.includes("11-image") && !blockers.includes("11 图"),
      `workflow ${viewport.width}: superseded legacy blocker is not presented as active truth`,
      blockers,
    );
    await button.click();
    check(
      await page.locator("#releasePrimaryActionButton").evaluate(
        (element) => document.activeElement === element,
      ),
      `workflow ${viewport.width}: primary next action leads to the unified action`,
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
  return collectboxStepOnePrimaryActionContract(browser, viewport);
  /* Historical direct-store fixture retained below for migration reference. */
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
      await page.locator("#releasePrimaryActionButton").isEnabled()
        && await page.locator("#releasePrimaryActionButton").evaluate(
          (element) => document.activeElement === element,
        ),
      `mixed release ${viewport.width}: action focuses the unified Miaoshou publish button`,
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
  return collectboxStepOnePrimaryActionContract(browser, viewport);
  /* Historical direct-store fixture retained below for migration reference. */
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
    check(
      await page.locator("#legacyReleaseRunLedger").isHidden()
        && await page.locator("#releasePrimaryActionButton").isEnabled(),
      `blocked capability ${viewport.width}: approved MVP uses the primary action instead of the legacy ledger`,
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

function oneClickDashboard({
  offerId = "3828540231",
  terminal = false,
  warningAccepted = false,
} = {}) {
  const dashboard = JSON.parse(JSON.stringify(productDashboard));
  const payloadDigest = "a".repeat(64);
  const targetsDigest = "b".repeat(64);
  dashboard.product.offer_id = offerId;
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
    selected_labels: [
      "shopee:MY",
      "tiktok:GB",
      "shopee:VN",
      "ozon:RU",
    ],
    default_labels: [],
    available_targets: [],
  };
  dashboard.release_v1 = {
    eligible_for_plan_approval: false,
    plan_approved: true,
    miaoshou_prepared: true,
    publish_ready: !terminal,
    blockers: [],
    plan: {
      plan_id: "omnichannel:oneclick-ui",
      confirmation_token: "browser-echo-only",
      payload_digest: payloadDigest,
      targets_digest: targetsDigest,
    },
    run: null,
    target_recovery_actions: [],
    oneclick_controlplane: terminal
      ? oneClickProjection(
        "oneclick-release-status/v2",
        warningAccepted ? "accepted" : "terminal",
        "WAITING_MANUAL_ACCEPTANCE",
      )
      : null,
    canonical_next_action: terminal
      ? warningAccepted
        ? {
          target_label: "tiktok:GB",
          target_focus: "tiktok:GB",
          canonical_status: "SUBMITTED_UNVERIFIED",
          action: "verify_submission_in_marketplace",
          runnable: false,
        }
        : {
          target_label: "shopee:MY",
          target_focus: "shopee:MY",
          canonical_status: "SUCCEEDED_MANUAL_REVIEW",
          action: "review_verified_observation_warning",
          runnable: false,
        }
      : {
        target_label: "shopee:MY",
        target_focus: "shopee:MY",
        canonical_status: "READY",
        action: "wait_for_worker",
        runnable: true,
      },
  };
  return dashboard;
}

function oneClickTargets(stage) {
  const independentDependency = {
    policy_version: "oneclick-target-dependency/mvp-unblocked-v1",
    state: "SATISFIED",
    satisfied: true,
    prerequisite_target: null,
    prerequisite_status: null,
  };
  return [
    {
      target_label: "miaoshou:COMMON",
      storefront: false,
      control_target: false,
      status: "SUCCEEDED",
      classification: "EXACT_READY_AUTOMATIC",
      runnable_now: false,
      manual_after_submit: false,
      requires_human: false,
      dependency: independentDependency,
      next_action: null,
      next_action_target: "miaoshou:COMMON",
      reason: null,
      digests: {
        prepared_command: "9".repeat(64),
        proof: "0".repeat(64),
        adapter_policy: "a".repeat(64),
        shared_resource: null,
        shared_resource_context: null,
      },
      dispatch_ledger: {},
    },
    {
      target_label: "shopee:MY",
      storefront: true,
      control_target: false,
      status: stage === "terminal" ? "SUCCEEDED_MANUAL_REVIEW"
        : stage === "accepted" ? "SUCCEEDED"
        : stage === "running" ? "DISPATCHING" : "READY",
      classification: "EXACT_READY_AUTOMATIC",
      runnable_now: stage === "preview",
      manual_after_submit: stage === "terminal",
      requires_human: stage === "terminal",
      dependency: independentDependency,
      next_action: stage === "terminal"
        ? "review_verified_observation_warning"
        : stage === "accepted" ? null
        : stage === "running"
          ? "wait_for_dispatch_receipt"
          : "wait_for_worker",
      next_action_target: "shopee:MY",
      reason: null,
      digests: {
        prepared_command: "1".repeat(64),
        proof: "2".repeat(64),
        adapter_policy: "3".repeat(64),
        shared_resource: "1".repeat(64),
        shared_resource_context: "2".repeat(64),
      },
      dispatch_ledger: {},
      result: stage === "terminal" ? {
        canonical_status: "SUCCEEDED_MANUAL_REVIEW",
        reason_category: "CAPABILITY",
        reason_scope: "TARGET",
        reason_code: "shopee_observation_warning",
        external_write_count: 2,
        external_write_classes: [
          "shopee:global_publish",
          "shopee:regional_publish",
        ],
        cumulative_external_write_count: 2,
        cumulative_external_write_classes: [
          "shopee:global_publish",
          "shopee:regional_publish",
        ],
        submission_accepted: true,
        readback_verified: true,
        dispatch_outcome_unknown: false,
        evidence_digest: "a".repeat(64),
        manual_review: true,
        rule_ids: [
          "copy:language_signal_weak",
          "global_image:rehosted_order_unverifiable",
        ],
        observation_digests: [
          "b".repeat(64),
          "c".repeat(64),
        ],
      } : stage === "accepted" ? {
        canonical_status: "SUCCEEDED",
        manual_review: true,
        manual_review_status: "ACCEPTED",
      } : null,
    },
    {
      target_label: "tiktok:GB",
      storefront: true,
      control_target: false,
      status: ["terminal", "accepted"].includes(stage)
        ? "SUBMITTED_UNVERIFIED" : "READY",
      classification: "READY_SUBMIT_MANUAL",
      runnable_now: stage === "preview" || stage === "running",
      manual_after_submit: true,
      requires_human: ["terminal", "accepted"].includes(stage),
      dependency: independentDependency,
      next_action: ["terminal", "accepted"].includes(stage)
        ? "verify_submission_in_marketplace" : "wait_for_worker",
      next_action_target: "tiktok:GB",
      reason: null,
      digests: {
        prepared_command: "4".repeat(64),
        proof: "5".repeat(64),
        adapter_policy: "6".repeat(64),
        shared_resource: null,
        shared_resource_context: null,
      },
      dispatch_ledger: {},
    },
    {
      target_label: "shopee:VN",
      storefront: true,
      control_target: false,
      status: "BLOCKED_CAPABILITY",
      classification: "BLOCKED_CAPABILITY",
      runnable_now: false,
      manual_after_submit: false,
      requires_human: false,
      dependency: independentDependency,
      next_action: "review_approved_content_facts",
      next_action_target: "shopee:VN",
      reason: {
        category: "CONTENT",
        scope: "TARGET",
        code: "approved_shopee_category_missing",
        summary_code: "approved_shopee_category_missing",
        detail_digest: "c".repeat(64),
      },
      digests: {
        prepared_command: null,
        proof: null,
        adapter_policy: "7".repeat(64),
        shared_resource: "1".repeat(64),
        shared_resource_context: "2".repeat(64),
      },
      dispatch_ledger: {},
    },
    {
      target_label: "ozon:RU",
      storefront: true,
      control_target: false,
      status: "BLOCKED_INVENTORY",
      classification: "BLOCKED_INVENTORY",
      runnable_now: false,
      manual_after_submit: false,
      requires_human: false,
      dependency: independentDependency,
      next_action: "approve_sellable_inventory",
      next_action_target: "ozon:RU",
      reason: {
        category: "INVENTORY",
        scope: "TARGET",
        code: "approved_inventory_missing",
        summary_code: "approved_inventory_missing",
        detail_digest: "d".repeat(64),
      },
      digests: {
        prepared_command: null,
        proof: null,
        adapter_policy: "8".repeat(64),
        shared_resource: null,
        shared_resource_context: null,
      },
      dispatch_ledger: {},
    },
  ];
}

function oneClickGlobalControl(stage) {
  return {
    target_label: "shopee:GLOBAL",
    storefront: false,
    control_target: true,
    status: "SUCCEEDED",
    classification: "EXACT_READY_AUTOMATIC",
    runnable_now: false,
    manual_after_submit: false,
    requires_human: false,
    dependency: {
      policy_version: "oneclick-target-dependency/mvp-unblocked-v1",
      state: "SATISFIED",
      satisfied: true,
      prerequisite_target: null,
      prerequisite_status: null,
    },
    next_action: null,
    next_action_target: "shopee:GLOBAL",
    reason: null,
    digests: {
      prepared_command: "1".repeat(64),
      proof: "2".repeat(64),
      adapter_policy: "3".repeat(64),
      shared_resource: "1".repeat(64),
      shared_resource_context: "2".repeat(64),
    },
    dispatch_ledger: {},
  };
}

function oneClickStatusLedger(target) {
  const count = target.status === "DISPATCHING" ? null : 0;
  return {
    stage: target.status === "DISPATCHING" ? "dispatch_invoked" : null,
    cumulative_external_write_count: count,
    cumulative_external_write_classes: count === null ? ["UNKNOWN"] : [],
    confirmed_external_write_count_lower_bound: 0,
    possible_external_write_count_upper_bound: count,
    digest: null,
    stage_evidence_digest: null,
    pending_write_intent_digest: null,
  };
}

function oneClickManualReconciliationStatusProjection() {
  const projection = oneClickProjection(
    "oneclick-release-status/v2",
    "accepted",
    "RUNNING",
  );
  const mvpDependency = {
    policy_version: "oneclick-target-dependency/mvp-unblocked-v1",
    state: "SATISFIED",
    satisfied: true,
    prerequisite_target: null,
    prerequisite_status: null,
  };
  const template = projection.targets.find(
    (target) => target.target_label === "tiktok:GB",
  );
  const reconciliationTarget = (label) => ({
    ...template,
    target_label: label,
    status: "RECONCILIATION_REQUIRED",
    classification: "READY_SUBMIT_MANUAL",
    runnable_now: false,
    manual_after_submit: true,
    requires_human: false,
    next_action: "reconcile_before_any_retry",
    next_action_target: label,
    result: null,
    dispatch_ledger: {
      stage: "dispatch_invoked",
      cumulative_external_write_count: 2,
      cumulative_external_write_classes: [
        "miaoshou:tiktok_detail:update",
        "miaoshou:tiktok_publish:submit",
      ],
      confirmed_external_write_count_lower_bound: 2,
      possible_external_write_count_upper_bound: 2,
      digest: null,
      stage_evidence_digest: null,
      pending_write_intent_digest: null,
    },
  });
  projection.targets = projection.targets.filter(
    (target) => target.target_label !== "tiktok:GB",
  );
  projection.targets.push(
    reconciliationTarget("tiktok:LH_TH"),
    reconciliationTarget("tiktok:LH_VN"),
  );
  projection.targets = projection.targets.map((target) => ({
    ...target,
    dependency: { ...mvpDependency },
  }));
  projection.shared_controls = [];
  projection.postpublish_actions = [];
  projection.control_row_count = projection.targets.filter(
    (target) => target.storefront === false,
  ).length;
  projection.storefront_count += 1;
  projection.summary.manual_after_submit = [];
  projection.summary.blocked = [
    ...projection.summary.blocked,
    "tiktok:LH_TH",
    "tiktok:LH_VN",
  ];
  projection.summary.already_terminal = projection.summary.already_terminal
    .filter((label) => label !== "tiktok:GB");
  projection.canonical_next_action = {
    target_label: "tiktok:LH_TH",
    target_focus: "tiktok:LH_TH",
    canonical_status: "RECONCILIATION_REQUIRED",
    action: "reconcile_before_any_retry",
    runnable: false,
  };
  return projection;
}

function oneClickProjection(schemaVersion, stage, phase = null) {
  const targets = oneClickTargets(stage);
  const projection = {
    schema_version: schemaVersion,
    plan_id: "omnichannel:oneclick-ui",
    run_id: "release-run:oneclick-ui",
    product_revision: 31,
    digests: {
      payload: "a".repeat(64),
      targets: "b".repeat(64),
      source_identity: `sha256:${"c".repeat(64)}`,
      source_identity_payload: "d".repeat(64),
      sku_lineage: "e".repeat(64),
      sku_lineage_payload: "f".repeat(64),
      adapter_policy: "0".repeat(64),
    },
    targets,
    shared_controls: [],
    postpublish_actions: [],
    storefront_count: 4,
    control_row_count: 1,
    runnable_target_count: stage === "preview" ? 2
      : stage === "running" ? 1 : 0,
    summary: {
      will_dispatch: stage === "preview" ? ["shopee:MY"] : [],
      manual_after_submit: ["preview", "running"].includes(stage)
        ? ["tiktok:GB"]
        : stage === "terminal"
          ? ["shopee:MY", "tiktok:GB"]
          : stage === "accepted" ? ["tiktok:GB"] : [],
      blocked: ["shopee:VN", "ozon:RU"],
      already_terminal: ["terminal", "accepted"].includes(stage)
        ? ["shopee:MY", "tiktok:GB"] : [],
    },
    dispatch_capability: {
      schema_version: "oneclick-dispatch-capability/v1",
      enabled: true,
      source: "server_default",
      reason_code: "oneclick_dispatch_enabled_by_default",
      next_action: null,
    },
    canonical_next_action: stage === "terminal"
      ? {
        target_label: "shopee:MY",
        target_focus: "shopee:MY",
        canonical_status: "SUCCEEDED_MANUAL_REVIEW",
        action: "review_verified_observation_warning",
        runnable: false,
      }
      : stage === "accepted"
        ? {
          target_label: "tiktok:GB",
          target_focus: "tiktok:GB",
          canonical_status: "SUBMITTED_UNVERIFIED",
          action: "verify_submission_in_marketplace",
          runnable: false,
        }
      : stage === "running"
        ? {
          target_label: "tiktok:GB",
          target_focus: "tiktok:GB",
          canonical_status: "READY",
          action: "wait_for_worker",
          runnable: true,
        }
        : {
          target_label: "shopee:MY",
          target_focus: "shopee:MY",
          canonical_status: "READY",
          action: "wait_for_worker",
          runnable: true,
        },
  };
  if (!phase) {
    projection.targets = projection.targets.map(({
      dispatch_ledger: _dispatchLedger,
      ...target
    }) => target);
    projection.shared_controls = projection.shared_controls.map(({
      dispatch_ledger: _dispatchLedger,
      ...target
    }) => target);
    projection.preparation_pending_count = 0;
    projection.prepare_pending = [];
    projection.start_allowed = false;
    if (stage === "preview") {
      projection.targets = projection.targets.map((target) => (
        target.runnable_now !== true
          ? target
          : {
            ...target,
            status: "PENDING",
            classification: "PREPARE_PENDING",
            runnable_now: false,
            manual_after_submit: false,
            requires_human: false,
            next_action: "prepare_batch",
            digests: {
              ...target.digests,
              prepared_command: null,
              proof: null,
            },
          }
      ));
      projection.shared_controls = projection.shared_controls.map((target) => ({
        ...target,
        status: "PENDING",
        classification: "PREPARE_PENDING",
        next_action: "prepare_batch",
        digests: {
          ...target.digests,
          prepared_command: null,
          proof: null,
        },
      }));
      projection.prepare_pending = [
        ...projection.targets,
      ]
        .filter((target) => target.classification === "PREPARE_PENDING")
        .map((target) => target.target_label);
      projection.preparation_pending_count = projection.prepare_pending.length;
      projection.start_allowed = projection.preparation_pending_count > 0;
      projection.runnable_target_count = 0;
      projection.summary.will_dispatch = [];
      projection.summary.manual_after_submit = [];
      const firstPending = [
        ...projection.shared_controls,
        ...projection.targets,
      ].find((target) => target.classification === "PREPARE_PENDING");
      projection.canonical_next_action = {
        target_label: firstPending.target_label,
        target_focus: firstPending.target_label,
        canonical_status: "PENDING",
        action: "prepare_batch",
        runnable: false,
      };
    }
  }
  if (phase) {
    projection.job_id = "oneclick-job:ui";
    projection.phase = phase;
    projection.terminal = [
      "SUCCEEDED",
      "WAITING_MANUAL_ACCEPTANCE",
      "BLOCKED",
      "SYSTEMIC_STOPPED",
    ].includes(phase);
    projection.targets = projection.targets.map((target) => ({
      ...target,
      dispatch_count: target.status === "PENDING" ? 0 : 1,
      dispatch_ledger: oneClickStatusLedger(target),
    }));
    projection.shared_controls = projection.shared_controls.map((target) => ({
      ...target,
      dispatch_count: target.status === "PENDING" ? 0 : 1,
      dispatch_ledger: oneClickStatusLedger(target),
    }));
  }
  return projection;
}

function withPostpublishPromotionAction(projection) {
  const prerequisite = projection.targets.find(
    (target) => target.target_label === "shopee:MY",
  );
  const promotion = {
    target_label: "promotion:shopee:MY",
    storefront: false,
    control_target: false,
    status: "PENDING",
    classification: "PREPARE_PENDING",
    runnable_now: false,
    manual_after_submit: false,
    requires_human: false,
    dependency: {
      policy_version: "oneclick-target-dependency/mvp-unblocked-v1",
      state: "WAITING",
      satisfied: false,
      prerequisite_target: prerequisite.target_label,
      prerequisite_status: prerequisite.status,
      prerequisite: {
        target_label: prerequisite.target_label,
        status: prerequisite.status,
        reason: prerequisite.reason,
        next_action: prerequisite.next_action,
        digests: {
          prepared_command: prerequisite.digests.prepared_command,
          proof: prerequisite.digests.proof,
          shared_resource: prerequisite.digests.shared_resource,
          shared_resource_context:
            prerequisite.digests.shared_resource_context,
        },
      },
    },
    next_action: "prepare_batch",
    next_action_target: prerequisite.target_label,
    reason: null,
    digests: {
      prepared_command: null,
      proof: null,
      adapter_policy: "c".repeat(64),
      shared_resource: null,
      shared_resource_context: null,
    },
  };
  projection.targets.push(promotion);
  projection.postpublish_actions = [promotion];
  projection.control_row_count += 1;
  projection.canonical_next_action = {
    target_label: "ozon:RU",
    target_focus: "ozon:RU",
    canonical_status: "BLOCKED_INVENTORY",
    action: "approve_sellable_inventory",
    runnable: false,
  };
  return projection;
}

async function oneClickLivePostpublishProjectionContract(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
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
      return route.fulfill(jsonResponse(oneClickDashboard()));
    }
    if (url.pathname === "/api/product-workspace/publish-preview") {
      return route.fulfill(jsonResponse({
        ok: true,
        persisted: false,
        external_writes_performed: [],
        preview: withPostpublishPromotionAction(oneClickProjection(
          "release-batch-preparation/v2",
          "preview",
        )),
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
    try {
      await page.waitForFunction(() => (
        document.querySelector("#publishAllCheckbox")?.disabled === false
      ), null, { timeout: 5000 });
    } catch (error) {
      throw new Error(
        `${error.message}\nmessage=${
          await page.locator("#oneClickExecutionMessage").innerText()
        }\nnote=${await page.locator("#publishAllNote").innerText()
        }\nerrors=${JSON.stringify(errors)}`,
      );
    }
    check(
      requests.filter((row) => row.method === "POST").length === 0,
      "one-click live postpublish projection: preview performs zero writes",
      requests,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      "one-click live postpublish projection: no console/page errors",
      errors,
    );
  } finally {
    await context.close();
  }
}

function oneClickBlockedPromotionStatusProjection() {
  const projection = withPostpublishPromotionAction(oneClickProjection(
    "oneclick-release-status/v2",
    "terminal",
    "BLOCKED",
  ));
  projection.targets = projection.targets.map((target) => (
    target.target_label === "miaoshou:COMMON"
      ? {
        ...target,
        classification: null,
        digests: {
          ...target.digests,
          prepared_command: null,
          proof: null,
        },
        dispatch_count: 0,
        dispatch_ledger: {
          stage: null,
          cumulative_external_write_count: 0,
          cumulative_external_write_classes: [],
          confirmed_external_write_count_lower_bound: 0,
          possible_external_write_count_upper_bound: 0,
          digest: null,
          stage_evidence_digest: null,
          pending_write_intent_digest: null,
        },
      }
      : target
  ));
  const completedCommon = projection.targets.find(
    (target) => target.target_label === "miaoshou:COMMON",
  );
  projection.targets = projection.targets.map((target) => (
    target.target_label.startsWith("tiktok:")
      ? {
        ...target,
        dependency: {
          ...target.dependency,
          prerequisite: {
            ...target.dependency.prerequisite,
            digests: {
              prepared_command: completedCommon.digests.prepared_command,
              proof: completedCommon.digests.proof,
              shared_resource: completedCommon.digests.shared_resource,
              shared_resource_context:
                completedCommon.digests.shared_resource_context,
            },
          },
        },
      }
      : target
  ));
  projection.targets = projection.targets.map((target) => {
    if (target.target_label !== "tiktok:GB") return target;
    return {
      ...target,
      status: "RECONCILIATION_REQUIRED",
      runnable_now: false,
      requires_human: false,
      next_action: "reconcile_before_any_retry",
      reason: {
        category: "POST_WRITE",
        scope: "TARGET",
        code: "dispatch_invocation_requires_reconciliation",
        summary_code: "dispatch_invocation_requires_reconciliation",
        detail_digest: "e".repeat(64),
      },
      dispatch_ledger: {
        stage: "dispatch_invoked",
        cumulative_external_write_count: 1,
        cumulative_external_write_classes: [
          "miaoshou:tiktok_detail:update",
        ],
        confirmed_external_write_count_lower_bound: 1,
        possible_external_write_count_upper_bound: 1,
        digest: "f".repeat(64),
        stage_evidence_digest: "0".repeat(64),
        pending_write_intent_digest: null,
      },
    };
  });
  const promotionIndex = projection.targets.findIndex(
    (target) => target.target_label === "promotion:shopee:MY",
  );
  const prerequisite = projection.targets.find(
    (target) => target.target_label === "shopee:MY",
  );
  const promotion = {
    ...projection.targets[promotionIndex],
    classification: null,
    dispatch_count: 0,
    dispatch_ledger: {
      stage: null,
      cumulative_external_write_count: 0,
      cumulative_external_write_classes: [],
      confirmed_external_write_count_lower_bound: 0,
      possible_external_write_count_upper_bound: 0,
      digest: null,
      stage_evidence_digest: null,
      pending_write_intent_digest: null,
    },
    dependency: {
      ...projection.targets[promotionIndex].dependency,
      state: "BLOCKED",
      satisfied: false,
      prerequisite_status: prerequisite.status,
      prerequisite: {
        target_label: prerequisite.target_label,
        status: prerequisite.status,
        reason: prerequisite.reason,
        next_action: prerequisite.next_action,
        digests: {
          prepared_command: prerequisite.digests.prepared_command,
          proof: prerequisite.digests.proof,
          shared_resource: prerequisite.digests.shared_resource,
          shared_resource_context:
            prerequisite.digests.shared_resource_context,
        },
      },
    },
  };
  projection.targets[promotionIndex] = promotion;
  projection.postpublish_actions = [promotion];
  projection.summary = {
    will_dispatch: [],
    manual_after_submit: ["shopee:MY"],
    blocked: ["tiktok:GB", "shopee:VN", "ozon:RU"],
    already_terminal: ["shopee:MY"],
    postpublish_pending: ["promotion:shopee:MY"],
  };
  projection.canonical_next_action = {
    target_label: "tiktok:GB",
    target_focus: "tiktok:GB",
    canonical_status: "RECONCILIATION_REQUIRED",
    action: "reconcile_before_any_retry",
    runnable: false,
  };
  projection.targets = projection.targets
    .filter((target) => target.target_label !== "promotion:shopee:MY")
    .map((target) => ({
      ...target,
      dependency: {
        policy_version: "oneclick-target-dependency/mvp-unblocked-v1",
        state: "SATISFIED",
        satisfied: true,
        prerequisite_target: null,
        prerequisite_status: null,
      },
    }));
  projection.postpublish_actions = [];
  projection.control_row_count = projection.targets.filter(
    (target) => target.storefront === false,
  ).length;
  delete projection.summary.postpublish_pending;
  return projection;
}

async function oneClickBlockedPromotionStatusAndSingleActionContract(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  const status = oneClickBlockedPromotionStatusProjection();
  const dashboard = oneClickDashboard();
  dashboard.release_v1.oneclick_controlplane = status;
  dashboard.release_v1.canonical_next_action =
    status.canonical_next_action;
  let statusReads = 0;
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
    if (url.pathname === "/api/product-workspace/publish-status") {
      statusReads += 1;
      return route.fulfill(jsonResponse({ ok: true, job: status }));
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
    await page.waitForFunction(() => (
      document.querySelector("#releasePrimaryActionButton")
      && !document.querySelector("#releasePrimaryActionButton").hidden
    ));
    const message = await page.locator("#oneClickExecutionMessage").innerText();
    check(
      !message.includes("店铺状态不完整"),
      "one-click blocked promotion: durable status replaces stale cached job",
      message,
    );
    const reconciliation = page.locator(
      '[data-oneclick-target="tiktok:GB"]',
    );
    await page.waitForTimeout(500);
    if (await reconciliation.count() === 0) {
      throw new Error(
        "blocked promotion status did not render: "
        + JSON.stringify({
          message: await page.locator("#oneClickExecutionMessage").innerText(),
          errors,
          body: (await page.locator("#releasePlan").innerText()).slice(0, 2000),
        }),
      );
    }
    check(
      await reconciliation.isVisible()
        && (await reconciliation.innerText()).includes("需要对账"),
      "one-click blocked promotion: reconciliable target is visible",
      await reconciliation.innerText(),
    );
    const visibleReleaseButtons = await page.locator(
      "#releasePlan button:visible",
    ).count();
    check(
      visibleReleaseButtons === 1,
      "one-click approved flow: exactly one visible action remains",
      visibleReleaseButtons,
    );
    check(
      statusReads === 0
        && requests.filter((row) => row.method === "POST").length === 0,
      "one-click blocked promotion: persisted terminal dashboard performs no retry",
      { statusReads, requests },
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      "one-click blocked promotion: no console/page errors",
      errors,
    );
  } finally {
    await context.close();
  }
}

async function oneClickMiaoshouMvpAlwaysRetryContract(browser, viewport) {
  return collectboxStepOnePrimaryActionContract(browser, viewport);
  /* Retained fixture history below documents the removed direct-store flow. */
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  const blockedStatus = oneClickBlockedPromotionStatusProjection();
  const acceptedStatus = oneClickProjection(
    "oneclick-release-status/v2",
    "accepted",
    "WAITING_MANUAL_ACCEPTANCE",
  );
    const dashboard = oneClickDashboard();
    dashboard.release_v1.oneclick_controlplane = blockedStatus;
    dashboard.release_v1.canonical_next_action =
      blockedStatus.canonical_next_action;
    dashboard.release_v1.run = {
      run_id: "release-run:legacy-mvp-history",
      status: "PARTIAL_FAILED",
      targets: [
        {
          target_label: "tiktok:LH_TH",
          status: "FAILED",
          attempts: 1,
          external_id: "legacy-external-id",
          error: "accepted; waiting for official readback; retry forbidden",
        },
      ],
    };
  let publishAttempts = 0;
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
    if (url.pathname === "/api/product-workspace/publish-status") {
      return route.fulfill(jsonResponse({ ok: true, job: acceptedStatus }));
    }
    if (
      url.pathname === "/api/product-workspace/publish"
      && request.method() === "POST"
    ) {
      publishAttempts += 1;
      await new Promise((resolve) => setTimeout(resolve, 120));
      if (publishAttempts === 1) {
        return route.fulfill(jsonResponse({
          ok: false,
          error: "fixture Miaoshou submit failed",
          external_writes_performed: [],
        }, 409));
      }
      return route.fulfill(jsonResponse({
        ok: true,
        accepted: true,
        external_writes_performed: [],
        job: acceptedStatus,
      }, 202));
    }
    const fixture = apiFixture(
      url,
      request.method(),
      { delayWeekly: false, delaySku: false, pending: {} },
    );
    return route.fulfill(fixture || jsonResponse({ ok: false }, 404));
  });
  try {
    await page.goto(`${baseUrl}/product-workspace?offer_id=3846511157`, {
      waitUntil: "networkidle",
    });
    const publishButton = page.locator("#releasePrimaryActionButton");
    await publishButton.waitFor({ state: "visible" });
    check(
      await page.locator("#releasePlan button:visible").count() === 1,
      `Miaoshou MVP ${viewport.width}: exactly one release action is visible`,
      await page.locator("#releasePlan button:visible").allTextContents(),
    );
    check(
      await publishButton.isEnabled()
        && (await publishButton.innerText()).trim() === "一键发布已选店铺",
      `Miaoshou MVP ${viewport.width}: blocked history does not disable publish`,
      {
        enabled: await publishButton.isEnabled(),
        text: await publishButton.innerText(),
      },
    );
    check(
      requests.filter((row) => row.method === "POST").length === 0,
      `Miaoshou MVP ${viewport.width}: first load is GET-only`,
      requests,
    );
    const forbiddenControls = page.getByRole("button", {
      name: /对账|回读|依赖|库存|解除阻断|人工验收/,
    });
    check(
      await forbiddenControls.count() === 0,
      `Miaoshou MVP ${viewport.width}: no reconcile/readback dependency controls`,
      await forbiddenControls.allTextContents(),
    );
    check(
      !await page.locator(".run-ledger").isVisible()
        && !((await page.locator("#releasePlan").innerText()).includes("禁止重发"))
        && !((await page.locator("#releasePlan").innerText()).includes("等待官方回读")),
      `Miaoshou MVP ${viewport.width}: approved flow hides the legacy reconciliation ledger`,
      await page.locator("#releasePlan").innerText(),
    );

    await publishButton.click();
    await page.waitForTimeout(40);
    check(
      await publishButton.isDisabled()
        && (await publishButton.innerText()).includes("正在"),
      `Miaoshou MVP ${viewport.width}: one click has visible loading`,
      {
        disabled: await publishButton.isDisabled(),
        text: await publishButton.innerText(),
      },
    );
    await page.waitForTimeout(500);
    check(
      publishAttempts === 1
        && await publishButton.isEnabled()
        && (await publishButton.innerText()).trim() === "一键发布已选店铺"
        && (await page.locator("#oneClickExecutionMessage").innerText())
          .includes("HTTP 409"),
      `Miaoshou MVP ${viewport.width}: explicit failure keeps retry available`,
      {
        publishAttempts,
        text: await publishButton.innerText(),
        message: await page.locator("#oneClickExecutionMessage").innerText(),
      },
    );

    await publishButton.click();
    await page.waitForTimeout(500);
    check(
      publishAttempts === 2 && await publishButton.isEnabled(),
      `Miaoshou MVP ${viewport.width}: retry is one POST and remains available`,
      {
        publishAttempts,
        enabled: await publishButton.isEnabled(),
        message: await page.locator("#oneClickExecutionMessage").innerText(),
      },
    );
    const resultText = await page.locator("#oneClickExecutionGroups").innerText();
    check(
      resultText.includes("妙手已接受") && resultText.includes("失败"),
      `Miaoshou MVP ${viewport.width}: accepted and failed targets stay visible`,
      resultText,
    );
    check(
      await page.evaluate(() => (
        document.documentElement.scrollWidth <= window.innerWidth
      )),
      `Miaoshou MVP ${viewport.width}: no horizontal overflow`,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      `Miaoshou MVP ${viewport.width}: no console/page errors`,
      errors,
    );
  } finally {
    await context.close();
  }
}

async function oneClickManualReconciliationStatusContract(browser, viewport) {
  return collectboxStepOnePrimaryActionContract(browser, viewport);
  /* Historical direct-store fixture retained below for migration reference. */
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  const status = oneClickManualReconciliationStatusProjection();
  const dashboard = oneClickDashboard();
  dashboard.release_v1.oneclick_controlplane = status;
  dashboard.release_v1.canonical_next_action = status.canonical_next_action;
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
    if (url.pathname === "/api/product-workspace/publish-status") {
      return route.fulfill(jsonResponse({ ok: true, job: status }));
    }
    const fixture = apiFixture(
      url,
      request.method(),
      { delayWeekly: false, delaySku: false, pending: {} },
    );
    return route.fulfill(fixture || jsonResponse({ ok: false }, 404));
  });
  try {
    await page.goto(`${baseUrl}/product-workspace?offer_id=3846511157`, {
      waitUntil: "networkidle",
    });
    await page.waitForTimeout(1200);
    const message = await page.locator("#oneClickExecutionMessage").innerText();
    check(
      !message.includes("店铺状态不完整")
        && requests.filter(
          (row) => row.url.includes("/publish-status"),
        ).length >= 2,
      `manual reconciliation ${viewport.width}: valid server status remains pollable`,
      { message, requests },
    );
    check(
      await page.locator("#releasePrimaryActionButton").isEnabled(),
      `manual reconciliation ${viewport.width}: status does not disable explicit republish`,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      `manual reconciliation ${viewport.width}: no console/page errors`,
      errors,
    );
  } finally {
    await context.close();
  }
}

function collectboxActionProjection(state) {
  const platform = ({
    name,
    status,
    outcome = null,
    attempts = 0,
    retryAllowed = false,
    receiptDigest = null,
    detailDigest = null,
    writeCount = 0,
    writeClasses = [],
    error = null,
    targets = null,
    targetOutcomes = null,
    publishable = status === "SUCCEEDED",
  }) => ({
    platform: name,
    targets: targets || [{
      target_label: name === "TIKTOK" ? "tiktok:LH_PH" : "shopee:MY",
      status,
    }],
    target_outcomes: targetOutcomes || [],
    status,
    outcome,
    attempt_count: attempts,
    retry_allowed: retryAllowed,
    receipt_digest: receiptDigest,
    platform_detail_id_digest: detailDigest,
    external_writes: {
      count: writeCount,
      classes: writeClasses,
    },
    error,
    publishable,
  });
  const base = {
    schema_version: "collectbox-action-status/v1",
    ok: true,
    persisted: state !== "READY",
    approved_plan: {
      plan_id: "omnichannel:oneclick-ui",
      product_revision: 31,
      payload_digest: "a".repeat(64),
      targets_digest: "b".repeat(64),
    },
    external_writes_performed: [],
    external_write_count: 0,
    canonical_next_action: null,
  };
  if (state === "READY") {
    return {
      ...base,
      action: {
        action_id: null,
        status: "READY",
        start_allowed: true,
        retry_allowed: false,
        terminal: false,
        error: null,
        platforms: [
          platform({ name: "TIKTOK", status: "PENDING" }),
          platform({ name: "SHOPEE", status: "PENDING" }),
        ],
      },
      canonical_next_action: {
        action: "start_collectbox_action",
        target_focus: null,
      },
    };
  }
  if (state === "RUNNING") {
    return {
      ...base,
      persisted: true,
      action: {
        action_id: "collectbox-action:fixture",
        status: "RUNNING",
        start_allowed: false,
        retry_allowed: false,
        terminal: false,
        error: null,
        platforms: [
          platform({
            name: "TIKTOK",
            status: "SUCCEEDED",
            outcome: "IMPORTED",
            attempts: 1,
            receiptDigest: "1".repeat(64),
            detailDigest: "2".repeat(64),
            writeCount: 1,
            writeClasses: ["miaoshou:collectbox:claim:tiktok"],
          }),
          platform({ name: "SHOPEE", status: "RUNNING", attempts: 1 }),
        ],
      },
      external_writes_performed: ["miaoshou:collectbox:claim:tiktok"],
      external_write_count: 1,
      canonical_next_action: {
        action: "read_collectbox_status",
        target_focus: null,
      },
    };
  }
  if (state === "PARTIAL_FAILED") {
    return {
      ...base,
      persisted: true,
      action: {
        action_id: "collectbox-action:fixture",
        status: "PARTIAL_FAILED",
        start_allowed: true,
        retry_allowed: false,
        terminal: true,
        error: null,
        platforms: [
          platform({
            name: "TIKTOK",
            status: "SUCCEEDED",
            outcome: "IMPORTED",
            attempts: 1,
            receiptDigest: "1".repeat(64),
            detailDigest: "2".repeat(64),
            writeCount: 1,
            writeClasses: ["miaoshou:collectbox:claim:tiktok"],
          }),
          platform({
            name: "SHOPEE",
            status: "FAILED_RETRYABLE",
            attempts: 1,
            retryAllowed: true,
            receiptDigest: "9".repeat(64),
            error: {
              category: "ADAPTER",
              code: "shopee_collectbox_import_failed",
              detail_digest: "3".repeat(64),
            },
          }),
        ],
      },
      external_writes_performed: ["miaoshou:collectbox:claim:tiktok"],
      external_write_count: 1,
      canonical_next_action: {
        action: "restart_collectbox_action",
        target_focus: null,
      },
    };
  }
  if (state === "SUCCEEDED") {
    return {
      ...base,
      persisted: true,
      action: {
        action_id: "collectbox-action:complete",
        status: "SUCCEEDED",
        start_allowed: true,
        retry_allowed: false,
        terminal: true,
        error: null,
        platforms: [
          platform({
            name: "TIKTOK",
            status: "SUCCEEDED",
            outcome: "IMPORTED",
            attempts: 1,
            receiptDigest: "4".repeat(64),
            detailDigest: "5".repeat(64),
            writeCount: 3,
            writeClasses: [
              "miaoshou:collectbox:claim:tiktok",
              "miaoshou:collectbox:tiktok:shop:claim:tiktok:LH_PH",
              "miaoshou:collectbox:tiktok:detail:update:tiktok:LH_PH",
            ],
          }),
          platform({
            name: "SHOPEE",
            status: "SUCCEEDED",
            outcome: "IMPORTED",
            attempts: 1,
            receiptDigest: "6".repeat(64),
            detailDigest: "7".repeat(64),
            writeCount: 2,
            writeClasses: [
              "miaoshou:collectbox:claim:shopee",
              "miaoshou:collectbox:shopee:detail:update:shopee:MY",
            ],
          }),
        ],
      },
      external_writes_performed: [
        "miaoshou:collectbox:claim:tiktok",
        "miaoshou:collectbox:tiktok:shop:claim:tiktok:LH_PH",
        "miaoshou:collectbox:tiktok:detail:update:tiktok:LH_PH",
        "miaoshou:collectbox:claim:shopee",
        "miaoshou:collectbox:shopee:detail:update:shopee:MY",
      ],
      external_write_count: 5,
      canonical_next_action: {
        action: "restart_collectbox_action",
        target_focus: null,
      },
    };
  }
  if (
    state === "REPAIRED_SUCCESS_COUNT"
    || state === "INVALID_WRITE_COUNT_LOW"
    || state === "INVALID_WRITE_CLASS_DUPLICATE"
  ) {
    const writeClasses = [
      "miaoshou:collectbox:claim:tiktok",
      "miaoshou:collectbox:tiktok:shop:claim:tiktok:LH_PH",
      "miaoshou:collectbox:tiktok:detail:update:tiktok:LH_PH",
    ];
    if (state === "INVALID_WRITE_CLASS_DUPLICATE") {
      writeClasses[2] = writeClasses[1];
    }
    const writeCount = state === "INVALID_WRITE_COUNT_LOW" ? 2 : 4;
    const externalClasses = [...new Set(writeClasses)];
    return {
      ...base,
      persisted: true,
      action: {
        action_id: `collectbox-action:${state.toLowerCase()}`,
        status: "SUCCEEDED",
        start_allowed: true,
        retry_allowed: false,
        terminal: true,
        error: null,
        platforms: [
          platform({
            name: "TIKTOK",
            status: "SUCCEEDED",
            outcome: "IMPORTED",
            attempts: 1,
            receiptDigest: "8".repeat(64),
            detailDigest: "9".repeat(64),
            writeCount,
            writeClasses,
            targets: [
              { target_label: "tiktok:LH_PH", status: "SUCCEEDED" },
              { target_label: "tiktok:LH_MY", status: "SUCCEEDED" },
              { target_label: "tiktok:LH_TH", status: "SUCCEEDED" },
              { target_label: "tiktok:LH_VN", status: "SUCCEEDED" },
              { target_label: "tiktok:MX", status: "SUCCEEDED" },
              { target_label: "tiktok:GB", status: "SUCCEEDED" },
            ],
            targetOutcomes: [
              {
                target_label: "tiktok:LH_PH",
                status: "SUCCEEDED",
                error_code: null,
                detail_digest: null,
              },
              {
                target_label: "tiktok:LH_MY",
                status: "REPAIRED_SUCCEEDED",
                error_code: null,
                detail_digest: null,
              },
              {
                target_label: "tiktok:LH_TH",
                status: "SUCCEEDED",
                error_code: null,
                detail_digest: null,
              },
              {
                target_label: "tiktok:LH_VN",
                status: "SUCCEEDED",
                error_code: null,
                detail_digest: null,
              },
              {
                target_label: "tiktok:MX",
                status: "SUCCEEDED",
                error_code: null,
                detail_digest: null,
              },
              {
                target_label: "tiktok:GB",
                status: "SUCCEEDED",
                error_code: null,
                detail_digest: null,
              },
            ],
          }),
          platform({
            name: "SHOPEE",
            status: "SUCCEEDED",
            outcome: "ALREADY_PRESENT",
            attempts: 1,
            receiptDigest: "6".repeat(64),
            detailDigest: "7".repeat(64),
          }),
        ],
      },
      external_writes_performed: externalClasses,
      external_write_count: writeCount,
      canonical_next_action: {
        action: "restart_collectbox_action",
        target_focus: null,
      },
    };
  }
  if (state === "RECONCILIATION_PENDING") {
    return {
      ...base,
      persisted: true,
      action: {
        action_id: "collectbox-action:reconciliation-pending",
        status: "PARTIAL_FAILED",
        start_allowed: true,
        retry_allowed: false,
        terminal: true,
        error: null,
        platforms: [
          platform({
            name: "TIKTOK",
            status: "RECONCILIATION_REQUIRED",
            attempts: 1,
            receiptDigest: "e".repeat(64),
            writeCount: 2,
            writeClasses: [
              "miaoshou:collectbox:claim:tiktok",
              "miaoshou:collectbox:tiktok:shop:claim:tiktok:LH_PH",
            ],
            error: {
              category: "UNKNOWN",
              code: "collectbox_platform_preparation_failed",
              detail_digest: "f".repeat(64),
            },
          }),
          platform({ name: "SHOPEE", status: "PENDING" }),
        ],
      },
      external_writes_performed: [
        "miaoshou:collectbox:claim:tiktok",
        "miaoshou:collectbox:tiktok:shop:claim:tiktok:LH_PH",
      ],
      external_write_count: 2,
      canonical_next_action: {
        action: "restart_collectbox_action",
        target_focus: null,
      },
    };
  }
  if (state === "TARGET_MIXED") {
    return {
      ...base,
      persisted: true,
      action: {
        action_id: "collectbox-action:target-mixed",
        status: "PARTIAL_FAILED",
        start_allowed: true,
        retry_allowed: false,
        terminal: true,
        error: null,
        platforms: [
          platform({
            name: "TIKTOK",
            status: "RECONCILIATION_REQUIRED",
            attempts: 1,
            receiptDigest: "e".repeat(64),
            writeCount: null,
            writeClasses: [
              "miaoshou:collectbox:claim:tiktok",
              "miaoshou:collectbox:tiktok:shop:claim:tiktok:LH_PH",
              "miaoshou:collectbox:tiktok:detail:update:tiktok:LH_PH",
            ],
            error: {
              category: "UNKNOWN",
              code: "collectbox_platform_preparation_failed",
              detail_digest: "f".repeat(64),
            },
            targets: [
              { target_label: "tiktok:LH_PH", status: "SUCCEEDED" },
              { target_label: "tiktok:LH_MY", status: "SUCCEEDED" },
              { target_label: "tiktok:LH_TH", status: "FAILED_RETRYABLE" },
              { target_label: "tiktok:LH_VN", status: "SUCCEEDED" },
              { target_label: "tiktok:MX", status: "RECONCILIATION_REQUIRED" },
              { target_label: "tiktok:GB", status: "SUCCEEDED" },
            ],
            targetOutcomes: [
              {
                target_label: "tiktok:LH_PH",
                status: "SUCCEEDED",
                error_code: null,
                detail_digest: null,
              },
              {
                target_label: "tiktok:LH_MY",
                status: "REPAIRED_SUCCEEDED",
                error_code: null,
                detail_digest: null,
              },
              {
                target_label: "tiktok:LH_TH",
                status: "FAILED",
                error_code: "collectbox_target_preparation_failed",
                detail_digest: "1".repeat(64),
              },
              {
                target_label: "tiktok:LH_VN",
                status: "SUCCEEDED",
                error_code: null,
                detail_digest: null,
              },
              {
                target_label: "tiktok:MX",
                status: "FAILED",
                error_code: "collectbox_target_write_unknown",
                detail_digest: "2".repeat(64),
              },
              {
                target_label: "tiktok:GB",
                status: "SUCCEEDED",
                error_code: null,
                detail_digest: null,
              },
            ],
          }),
          platform({
            name: "SHOPEE",
            status: "SUCCEEDED",
            outcome: "ALREADY_PRESENT",
            attempts: 1,
            receiptDigest: "6".repeat(64),
            detailDigest: "7".repeat(64),
          }),
        ],
      },
      external_writes_performed: [
        "miaoshou:collectbox:claim:tiktok",
        "miaoshou:collectbox:tiktok:shop:claim:tiktok:LH_PH",
        "miaoshou:collectbox:tiktok:detail:update:tiktok:LH_PH",
      ],
      external_write_count: null,
      canonical_next_action: {
        action: "restart_collectbox_action",
        target_focus: null,
      },
    };
  }
  if (state === "GB_WAIVED") {
    const projection = collectboxActionProjection("TARGET_MIXED");
    const row = projection.action.platforms[0];
    const successfulTargets = [
      "tiktok:LH_PH",
      "tiktok:LH_MY",
      "tiktok:LH_TH",
      "tiktok:LH_VN",
      "tiktok:MX",
    ];
    row.publishable = true;
    row.targets = [
      ...successfulTargets.map((target_label) => ({
        target_label,
        status: "SUCCEEDED",
      })),
      { target_label: "tiktok:GB", status: "FAILED_RETRYABLE" },
    ];
    row.target_outcomes = [
      ...successfulTargets.map((target_label) => ({
        target_label,
        status: "SUCCEEDED",
        error_code: null,
        detail_digest: null,
      })),
      {
        target_label: "tiktok:GB",
        status: "FAILED",
        error_code: "approved_detail_readback_mismatch",
        detail_digest: "8".repeat(64),
      },
    ];
    return projection;
  }
  if (state === "INVALID_WRITE_CLASS") {
    return {
      ...base,
      persisted: true,
      action: {
        action_id: "collectbox-action:invalid-write-class",
        status: "SUCCEEDED",
        start_allowed: true,
        retry_allowed: false,
        terminal: true,
        error: null,
        platforms: [
          platform({
            name: "TIKTOK",
            status: "SUCCEEDED",
            outcome: "IMPORTED",
            attempts: 1,
            receiptDigest: "1".repeat(64),
            detailDigest: "2".repeat(64),
            writeCount: 2,
            writeClasses: [
              "miaoshou:collectbox:claim:tiktok",
              "miaoshou:collectbox:tiktok:detail:update:shopee:MY",
            ],
          }),
          platform({
            name: "SHOPEE",
            status: "SUCCEEDED",
            outcome: "ALREADY_PRESENT",
            attempts: 1,
            receiptDigest: "3".repeat(64),
            detailDigest: "4".repeat(64),
          }),
        ],
      },
      external_writes_performed: [
        "miaoshou:collectbox:claim:tiktok",
        "miaoshou:collectbox:tiktok:detail:update:shopee:MY",
      ],
      external_write_count: 2,
      canonical_next_action: {
        action: "restart_collectbox_action",
        target_focus: null,
      },
    };
  }
  if (state === "RECONCILIATION") {
    return {
      ...base,
      persisted: true,
      action: {
        action_id: "collectbox-action:reconciliation",
        status: "PARTIAL_FAILED",
        start_allowed: true,
        retry_allowed: false,
        terminal: true,
        error: null,
        platforms: [
          platform({
            name: "TIKTOK",
            status: "SUCCEEDED",
            outcome: "IMPORTED",
            attempts: 1,
            receiptDigest: "a".repeat(64),
            detailDigest: "b".repeat(64),
            writeCount: 1,
            writeClasses: ["miaoshou:collectbox:claim:tiktok"],
          }),
          platform({
            name: "SHOPEE",
            status: "RECONCILIATION_REQUIRED",
            attempts: 1,
            receiptDigest: "c".repeat(64),
            writeCount: null,
            writeClasses: ["miaoshou:collectbox:claim:shopee"],
            error: {
              category: "ADAPTER",
              code: "shopee_collectbox_result_unknown",
              detail_digest: "d".repeat(64),
            },
          }),
        ],
      },
      external_writes_performed: [
        "miaoshou:collectbox:claim:tiktok",
        "miaoshou:collectbox:claim:shopee",
      ],
      external_write_count: null,
      canonical_next_action: {
        action: "restart_collectbox_action",
        target_focus: null,
      },
    };
  }
  return {
    ...base,
    persisted: true,
    action: {
      action_id: "collectbox-action:blocked",
      status: "BLOCKED_IDENTITY",
      start_allowed: false,
      retry_allowed: false,
      terminal: true,
      error: {
        category: "IDENTITY",
        code: "approved_plan_identity_mismatch",
        detail_digest: "8".repeat(64),
      },
      platforms: [
        platform({ name: "TIKTOK", status: "PENDING" }),
        platform({ name: "SHOPEE", status: "PENDING" }),
      ],
    },
    canonical_next_action: null,
  };
}

async function collectboxStepOnePrimaryActionContract(browser, viewport) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const requests = [];
  const errors = [];
  let previewState = "READY";
  let statusReads = 0;
  let releaseInitialStartResponse;
  let initialStartIsLatched = true;
  const initialStartResponseGate = new Promise((resolve) => {
    releaseInitialStartResponse = resolve;
  });
  const optionalText = async (selector) => {
    const locator = page.locator(selector);
    return await locator.count() ? locator.innerText() : "";
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
    let requestBody = null;
    if (request.method() === "POST") {
      try {
        requestBody = request.postDataJSON();
      } catch (_error) {
        requestBody = null;
      }
    }
    requests.push({
      method: request.method(),
      path: url.pathname,
      body: requestBody,
    });
    if (url.pathname === "/api/product-workspace/dashboard") {
      return route.fulfill(jsonResponse(oneClickDashboard()));
    }
    if (url.pathname === "/api/product-workspace/publish-preview") {
      return route.fulfill(jsonResponse({
        ok: true,
        persisted: false,
        preview: oneClickProjection("release-batch-preparation/v2", "preview"),
        external_writes_performed: [],
      }));
    }
    if (url.pathname === "/api/product-workspace/collectbox-action/preview") {
      return route.fulfill(jsonResponse(collectboxActionProjection(previewState)));
    }
    if (url.pathname === "/api/product-workspace/collectbox-action/start") {
      if (initialStartIsLatched) {
        initialStartIsLatched = false;
        await initialStartResponseGate;
      }
      previewState = "PARTIAL_FAILED";
      return route.fulfill(jsonResponse(collectboxActionProjection("RUNNING")));
    }
    if (url.pathname === "/api/product-workspace/collectbox-action/status") {
      statusReads += 1;
      return route.fulfill(jsonResponse(
        collectboxActionProjection(previewState),
      ));
    }
    if ([
      "/api/product-workspace/publish-tiktok",
      "/api/product-workspace/publish-shopee-global",
      "/api/product-workspace/publish-ozon",
    ].includes(url.pathname)) {
      if (url.pathname === "/api/product-workspace/publish-tiktok") {
        return route.fulfill(jsonResponse({
          ok: false,
          error: {
            category: "CAPABILITY",
            code: "step1_collectbox_required",
            detail_digest: "9".repeat(64),
          },
          canonical_next_action: {
            action: "start_collectbox_action",
            target_focus: null,
          },
          external_writes_performed: [],
        }, 409));
      }
      if (url.pathname === "/api/product-workspace/publish-ozon") {
        return route.fulfill(jsonResponse({
          ok: false,
          error: {
            category: "INVENTORY",
            code: "approved_inventory_required",
            detail_digest: "7".repeat(64),
          },
          external_writes_performed: [],
        }, 409));
      }
      return route.fulfill(jsonResponse({
        ok: true,
        accepted: true,
        external_writes_performed: [],
        job: oneClickPendingJobProjection(),
      }, 202));
    }
    if (url.pathname === "/api/product-workspace/publish-status") {
      return route.fulfill(jsonResponse({
        ok: true,
        job: oneClickProjection(
          "oneclick-release-status/v2",
          "terminal",
          "SUCCEEDED",
        ),
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
    const pageUrl = `${baseUrl}/product-workspace?offer_id=3828540231`;
    await page.goto(pageUrl, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => {
      const button = document.querySelector("#collectboxActionButton");
      const status = document.querySelector("#collectboxActionStatus");
      return button
        && !button.textContent.includes("正在读取")
        && status?.textContent?.includes("TikTok")
        && status?.textContent?.includes("Shopee");
    });
    const primary = page.locator("#collectboxActionButton");
    const initialPosts = requests.filter((row) => row.method === "POST");
    check(
      requests.some((row) => (
        row.method === "GET"
        && row.path === "/api/product-workspace/collectbox-action/preview"
      ))
        && initialPosts.length === 0,
      `collectbox ${viewport.width}: first load is GET-only and reads preview`,
      requests,
    );
    check(
      await primary.isEnabled()
        && (await primary.innerText()).includes(
          "导入 TikTok / Shopee 妙手采集箱",
        )
        && await page.locator("#releasePrimaryActionButton").isVisible()
        && !(await page.locator("#releasePrimaryActionButton").isEnabled())
        && await page.locator("#shopeeGlobalReleaseButton").isVisible()
        && await page.locator("#shopeeGlobalReleaseButton").isEnabled()
        && await page.locator("#ozonReleaseButton").isVisible()
        && await page.locator("#ozonReleaseButton").isEnabled(),
      `collectbox ${viewport.width}: TikTok waits only for TikTok collectbox while Shopee and Ozon remain actionable`,
      {
        label: await primary.innerText(),
        enabled: await primary.isEnabled(),
      },
    );
    const initialState = await optionalText("#collectboxActionStatus");
    check(
      initialState.includes("TikTok")
        && initialState.includes("等待导入")
        && initialState.includes("Shopee"),
      `collectbox ${viewport.width}: waiting states are explicit`,
      initialState,
    );
    const initialStartRequest = page.waitForRequest((request) => (
      request.method() === "POST"
      && new URL(request.url()).pathname
        === "/api/product-workspace/collectbox-action/start"
    ));
    const click = primary.click();
    await initialStartRequest;
    try {
      await page.waitForFunction(() => {
        const button = document.querySelector("#collectboxActionButton");
        const message = document.querySelector("#collectboxActionMessage");
        return button?.textContent?.includes("正在导入")
          || message?.textContent?.includes("正在");
      });
      check(
        (await primary.innerText()).includes("正在导入")
          || (await optionalText("#collectboxActionMessage")).includes("正在"),
        `collectbox ${viewport.width}: click shows loading progress`,
      );
    } finally {
      releaseInitialStartResponse();
    }
    await click;
    await page.waitForTimeout(1200);
    const partialState = await optionalText("#collectboxActionStatus");
    check(
      requests.filter((row) => (
        row.method === "POST"
        && row.path === "/api/product-workspace/collectbox-action/start"
      )).length === 1
        && requests.filter((row) => (
          row.method === "POST"
          && row.path.startsWith("/api/product-workspace/publish")
          && !row.path.endsWith("-preview")
          && !row.path.endsWith("-status")
        )).length === 0,
      `collectbox ${viewport.width}: click sends exactly one collectbox POST`,
      requests,
    );
    check(
      statusReads >= 1
        && partialState.includes("TikTok")
        && partialState.includes("已导入")
        && partialState.includes("Shopee")
        && partialState.includes("失败，可重试")
        && await primary.isEnabled()
        && (await primary.innerText()).includes("重新导入"),
      `collectbox ${viewport.width}: partial result offers one fresh full batch`,
      { partialState, statusReads, label: await primary.innerText() },
    );

    requests.length = 0;
    previewState = "SUCCEEDED";
    await page.goto(pageUrl, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(300);
    const successState = await optionalText("#collectboxActionStatus");
    const restartEnabled = await primary.isEnabled();
    check(
      successState.includes("TikTok")
        && successState.includes("Shopee")
        && restartEnabled
        && (await primary.innerText()).includes("重新导入"),
      `collectbox ${viewport.width}: terminal success offers one explicit full restart`,
      { successState, label: await primary.innerText() },
    );
    const platformButtons = [
      ["#releasePrimaryActionButton", "/api/product-workspace/publish-tiktok"],
      ["#shopeeGlobalReleaseButton", "/api/product-workspace/publish-shopee-global"],
      ["#ozonReleaseButton", "/api/product-workspace/publish-ozon"],
    ];
    for (const [selector, expectedPath] of platformButtons) {
      const platformButton = page.locator(selector);
      check(
        await platformButton.isVisible() && await platformButton.isEnabled(),
        `platform isolation ${viewport.width}: ${selector} is independently actionable`,
      );
      await platformButton.click();
      await page.waitForTimeout(250);
      check(
        requests.some((row) => (
          row.method === "POST" && row.path === expectedPath
        )),
        `platform isolation ${viewport.width}: ${selector} posts only ${expectedPath}`,
        requests,
      );
      if (expectedPath === "/api/product-workspace/publish-tiktok") {
        const failureText = await optionalText("#oneClickExecutionPreview");
        check(
          failureText.includes("TikTok")
            && failureText.includes("发布失败")
            && failureText.includes("step1_collectbox_required")
            && await platformButton.isEnabled(),
          `platform structured error ${viewport.width}: actionable reason and code stay visible with retry`,
          { failureText, enabled: await platformButton.isEnabled() },
        );
      }
      if (expectedPath === "/api/product-workspace/publish-ozon") {
        const failureText = await optionalText("#oneClickExecutionPreview");
        check(
          failureText.includes("Ozon")
            && failureText.includes("发布失败")
            && failureText.includes("approved_inventory_required")
            && await platformButton.isEnabled(),
          `platform structured error ${viewport.width}: unknown structured code is preserved with retry`,
          { failureText, enabled: await platformButton.isEnabled() },
        );
      }
    }
    const platformPosts = requests.filter((row) => (
      row.method === "POST"
      && row.path.startsWith("/api/product-workspace/publish-")
    ));
    check(
      platformPosts.length === 3
        && new Set(platformPosts.map((row) => row.path)).size === 3,
      `platform isolation ${viewport.width}: each button calls only its endpoint`,
      platformPosts,
    );
    if (restartEnabled) {
      await primary.click();
      await page.waitForTimeout(250);
      const restartPosts = requests.filter((row) => (
        row.method === "POST"
        && row.path === "/api/product-workspace/collectbox-action/start"
      ));
      check(
        restartPosts.length === 1
          && restartPosts[0].body?.restart_collectbox_action === true
          && /^[0-9a-f-]{36}$/.test(
            restartPosts[0].body?.reimport_request_id || "",
          ),
        `collectbox ${viewport.width}: one explicit restart sends one full-batch POST`,
        restartPosts,
      );
    }

    requests.length = 0;
    previewState = "REPAIRED_SUCCESS_COUNT";
    await page.goto(pageUrl, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(300);
    const repairedWriteState = await optionalText(
      '[data-collectbox-platform="TIKTOK"]',
    );
    check(
      repairedWriteState.includes("修正后成功")
        && await primary.isEnabled()
        && (await primary.innerText()).includes("重新导入"),
      `collectbox ${viewport.width}: repaired target permits repeated write occurrences`,
      {
        repairedWriteState,
        label: await primary.innerText(),
        message: await optionalText("#collectboxActionMessage"),
      },
    );

    for (const invalidState of [
      "INVALID_WRITE_COUNT_LOW",
      "INVALID_WRITE_CLASS_DUPLICATE",
    ]) {
      requests.length = 0;
      previewState = invalidState;
      await page.goto(pageUrl, { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(300);
      const malformedState = await optionalText("#collectboxActionStatus");
      const malformedMessage = await optionalText("#collectboxActionMessage");
      check(
        !(await primary.isEnabled())
          && malformedState === ""
          && malformedMessage.length > 0,
        `collectbox ${viewport.width}: ${invalidState} fails closed`,
        {
          malformedState,
          malformedMessage,
          label: await primary.innerText(),
        },
      );
    }

    requests.length = 0;
    previewState = "RECONCILIATION";
    await page.goto(pageUrl, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(300);
    const reconciliationMessage = await optionalText(
      "#collectboxActionMessage",
    );
    const reconciliationState = await optionalText(
      "#collectboxActionStatus",
    );
    check(
      await primary.isEnabled()
        && reconciliationState.includes(
          "本批次结果待确认；可重新导入并创建新批次",
        )
        && (await primary.innerText()).includes("重新导入"),
      `collectbox ${viewport.width}: unknown result can start a fresh explicit batch`,
      {
        reconciliationMessage,
        reconciliationState,
        label: await primary.innerText(),
      },
    );

    requests.length = 0;
    previewState = "RECONCILIATION_PENDING";
    await page.goto(pageUrl, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(300);
    const reconciliationPendingState = await optionalText(
      "#collectboxActionStatus",
    );
    check(
      await primary.isEnabled()
        && reconciliationPendingState.includes("TikTok")
        && reconciliationPendingState.includes("Shopee")
        && reconciliationPendingState.includes(
          "本批次结果待确认；可重新导入并创建新批次",
        )
        && (await primary.innerText()).includes("重新导入"),
      `collectbox ${viewport.width}: reconciliation plus pending offers a fresh batch`,
      {
        reconciliationPendingState,
        label: await primary.innerText(),
        message: await optionalText("#collectboxActionMessage"),
      },
    );

    requests.length = 0;
    previewState = "TARGET_MIXED";
    await page.goto(pageUrl, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(300);
    const targetRows = page.locator(
      '[data-collectbox-platform="TIKTOK"] [data-collectbox-target-outcome]',
    );
    const targetLabels = await targetRows.evaluateAll((rows) => (
      rows.map((row) => row.getAttribute("data-collectbox-target-outcome"))
    ));
    const targetStateText = await optionalText(
      '[data-collectbox-platform="TIKTOK"]',
    );
    const finalTarget = targetRows.last();
    await finalTarget.scrollIntoViewIfNeeded();
    check(
      JSON.stringify(targetLabels) === JSON.stringify([
        "tiktok:LH_PH",
        "tiktok:LH_MY",
        "tiktok:LH_TH",
        "tiktok:LH_VN",
        "tiktok:MX",
        "tiktok:GB",
      ]),
      `collectbox ${viewport.width}: all six TikTok targets render through the final target`,
      { targetLabels, targetStateText },
    );
    check(
      targetStateText.includes("成功")
        && targetStateText.includes("修正后成功")
        && targetStateText.includes("失败原因")
        && !targetStateText.includes("collectbox_target_preparation_failed")
        && !targetStateText.includes("collectbox_target_write_unknown")
        && !targetStateText.includes("1111111111111111")
        && !targetStateText.includes("2222222222222222")
        && await finalTarget.isVisible()
        && await primary.isEnabled()
        && (await primary.innerText()).includes("重新导入")
        && requests.every((row) => (
          row.method !== "POST"
          || row.path !== "/api/product-workspace/publish"
        )),
      `collectbox ${viewport.width}: per-target result copy preserves one reimport action and never publishes`,
      {
        targetStateText,
        label: await primary.innerText(),
        requests,
      },
    );

    requests.length = 0;
    previewState = "GB_WAIVED";
    await page.goto(pageUrl, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(300);
    const tiktokPublish = page.locator("#releasePrimaryActionButton");
    check(
      await tiktokPublish.isVisible()
        && await tiktokPublish.isEnabled()
        && requests.every((row) => row.method === "GET"),
      `collectbox ${viewport.width}: exact GB-only waiver keeps TikTok publishing actionable`,
      {
        label: await tiktokPublish.innerText(),
        enabled: await tiktokPublish.isEnabled(),
        requests,
      },
    );

    requests.length = 0;
    previewState = "INVALID_WRITE_CLASS";
    await page.goto(pageUrl, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(300);
    const invalidWriteState = await optionalText("#collectboxActionStatus");
    const invalidWriteMessage = await optionalText("#collectboxActionMessage");
    check(
      !(await primary.isEnabled())
        && invalidWriteState === ""
        && invalidWriteMessage.length > 0,
      `collectbox ${viewport.width}: illegal cross-platform write class fails closed`,
      {
        invalidWriteState,
        invalidWriteMessage,
        label: await primary.innerText(),
      },
    );

    requests.length = 0;
    previewState = "BLOCKED_IDENTITY";
    await page.goto(pageUrl, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(300);
    const blockedMessage = await optionalText("#collectboxActionMessage");
    check(
      !(await primary.isEnabled())
        && blockedMessage.includes("批准计划身份不一致"),
      `collectbox ${viewport.width}: disabled action explains the blocker`,
      { blockedMessage, label: await primary.innerText() },
    );
    check(
      await page.evaluate(() => (
        document.documentElement.scrollWidth <= window.innerWidth
      )),
      `collectbox ${viewport.width}: no horizontal overflow`,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      `collectbox ${viewport.width}: no console/page errors`,
      errors,
    );
  } finally {
    releaseInitialStartResponse();
    await context.close();
  }
}

function oneClickPendingJobProjection() {
  const projection = oneClickProjection(
    "oneclick-release-status/v2",
    "preview",
    "PENDING",
  );
  projection.targets = projection.targets.map((target) => ({
    ...target,
    dispatch_count: 0,
    status: "PENDING",
    classification: null,
    runnable_now: false,
    manual_after_submit: false,
    requires_human: false,
    dependency: target.target_label.startsWith("tiktok:")
      ? {
        policy_version: "oneclick-target-dependency/mvp-unblocked-v1",
        state: "SATISFIED",
        satisfied: true,
        prerequisite_target: null,
        prerequisite_status: null,
      }
      : target.dependency,
    next_action: "prepare_batch",
    reason: null,
    digests: {
      prepared_command: null,
      proof: null,
      adapter_policy: target.digests.adapter_policy,
      shared_resource: target.digests.shared_resource,
      shared_resource_context: target.digests.shared_resource_context,
    },
  }));
  projection.shared_controls = projection.shared_controls.map((target) => ({
    ...target,
    dispatch_count: 0,
    status: "PENDING",
    classification: null,
    next_action: "prepare_batch",
    digests: {
      ...target.digests,
      prepared_command: null,
      proof: null,
    },
    dispatch_ledger: oneClickStatusLedger({ status: "PENDING" }),
  }));
  projection.runnable_target_count = 0;
  projection.summary = {
    will_dispatch: [],
    manual_after_submit: [],
    blocked: [],
    already_terminal: [],
  };
  projection.canonical_next_action = {
    target_label: "miaoshou:COMMON",
    target_focus: "miaoshou:COMMON",
    canonical_status: "PENDING",
    action: "prepare_batch",
    runnable: false,
  };
  return projection;
}

function shopeeGlobalCandidate() {
  const counts = {
    category_path_depth: 2,
    attribute_count: 3,
    approved_image_count: 6,
    selected_image_count: 6,
    variation_tier_count: 1,
    model_count: 1,
  };
  const digests = {
    observation_evidence_digest: "1".repeat(64),
    source_identity_digest: "2".repeat(64),
    sku_lineage_digest: "3".repeat(64),
    content_package_digest: "4".repeat(64),
    approved_copy_digest: "5".repeat(64),
    approved_source_image_manifest_digest: "6".repeat(64),
    selected_source_image_manifest_digest: "7".repeat(64),
    parcel_contract_digest: "8".repeat(64),
    target_pricing_digest: "9".repeat(64),
    policy_digest: "a".repeat(64),
    category_evidence_digest: "b".repeat(64),
    attribute_tree_digest: "c".repeat(64),
    brand_evidence_digest: "d".repeat(64),
    seller_stock_source_digest: "e".repeat(64),
    location_evidence_digest: "f".repeat(64),
    existing_global_identity_digest: null,
    candidate_digest: "0".repeat(64),
  };
  return {
    schema_version: "shopee-global-plan-candidate/v1",
    status: "READY",
    planning_allowed: true,
    mode: "NEW_GLOBAL",
    observation_authority: "shopee_official_open_api",
    observation_schema_version: "shopee-official-global-plan-observation/v1",
    checks: {
      official_authority_exact: true,
      audited_schema_exact: true,
      attributes_complete: true,
      variations_complete: true,
      no_default_execution_fact: true,
    },
    counts,
    digests,
    blocker_codes: [],
  };
}

function shopeeCategoryPreview({
  status = "READY_FOR_SELECTION",
} = {}) {
  const digest = (character) => character.repeat(64);
  if (status === "RECHECK_REQUIRED") {
    return {
      ok: true,
      schema_version: "channel-category-decision-preview/v2",
      offer_id: "3828540231",
      product_revision: 31,
      target_label: "shopee:GLOBAL",
      mode: "NEW_GLOBAL",
      status,
      options_digest: digest("e"),
      recommendation: null,
      options: [],
      brand_options: [],
      location_options: [],
      creation_fact_option: null,
      selection: null,
      attribute_selection: {
        selection_digest: digest("f"),
        category_identity_digest: digest("1"),
        attribute_tree_digest: digest("b"),
        selection_count: 3,
        approved_by: "Kyle",
      },
      blocker: {
        category: "CAPABILITY",
        code: "official_category_attribute_recheck_required",
      },
      next_action: {
        action: "recheck_channel_category_attributes",
        target_focus: "shopee:GLOBAL",
      },
      external_writes_performed: [],
      persisted: true,
      created: true,
    };
  }
  const selected = status === "SELECTED";
  const awaitingRequired = status === "BLOCKED_CAPABILITY";
  const attributes = selected ? [] : [
    {
      attribute_identity_digest: digest("6"),
      label: "Material",
      selection_kind: "SINGLE",
      option_values: [{
        option_identity_digest: digest("9"),
        display_label: "PVC",
        recommended: true,
      }],
    },
    {
      attribute_identity_digest: digest("7"),
      label: "Room",
      selection_kind: "MULTI",
      option_values: [{
        option_identity_digest: digest("a"),
        display_label: "Bedroom",
        recommended: true,
      }],
    },
    {
      attribute_identity_digest: digest("8"),
      label: "Style note",
      selection_kind: "TEXT",
      option_values: [],
    },
  ];
  const option = {
    category_identity_digest: digest("1"),
    display_name: "Wall Stickers",
    path_labels: ["Home & Living", "Wall Stickers"],
    recommended: true,
    approval_ready: selected,
    attribute_status: selected ? "READY" : "BLOCKED_REQUIRED_VALUES",
    required_attribute_count: 3,
    selected_attribute_count: selected ? 3 : 0,
    missing_required_attributes: attributes,
    attribute_tree_digest: digest("b"),
    option_evidence_digest: digest("c"),
  };
  const creation = {
    creation_fact_identity_digest: digest("5"),
    seller_stock_quantity: 200,
    condition: "NEW",
    preorder: { is_pre_order: false, days_to_ship: 0 },
    variation_summary: {
      tier_count: 1,
      model_count: 1,
      model_sku_count: 1,
      approved_image_position: 1,
    },
    recommended: true,
    option_evidence_digest: digest("0"),
  };
  return {
    ok: true,
    schema_version: "channel-category-decision-preview/v2",
    offer_id: "3828540231",
    product_revision: 31,
    target_label: "shopee:GLOBAL",
    mode: "NEW_GLOBAL",
    status,
    options_digest: digest("e"),
    recommendation: {
      source: {
        authority: "approved_copy_category_recommendation/v1",
        evidence_digest: digest("d"),
      },
      category_identity_digest: digest("1"),
    },
    options: [option, {
      ...option,
      category_identity_digest: digest("2"),
      display_name: "Decorative Stickers",
      path_labels: ["Home & Living", "Decorative Stickers"],
      recommended: false,
      approval_ready: !awaitingRequired,
      attribute_status: awaitingRequired
        ? "BLOCKED_REQUIRED_VALUES"
        : "READY",
      selected_attribute_count: awaitingRequired ? 0 : 3,
      missing_required_attributes: awaitingRequired ? attributes : [],
      attribute_tree_digest: digest("3"),
      option_evidence_digest: digest("4"),
    }],
    brand_options: [{
      brand_identity_digest: digest("3"),
      display_name: "NoBrand",
      recommended: true,
      option_evidence_digest: digest("4"),
    }],
    location_options: [{
      location_identity_digest: digest("4"),
      display_name: "中国仓库",
      recommended: true,
      option_evidence_digest: digest("5"),
    }],
    creation_fact_option: creation,
    selection: selected ? {
      decision_digest: digest("6"),
      selected_category_identity_digest: digest("1"),
      selected_is_recommended: true,
      attribute_tree_digest: digest("b"),
      approved_by: "Kyle",
      selected_brand: {
        brand_identity_digest: digest("3"),
        display_name: "NoBrand",
        selected_is_recommended: true,
      },
      selected_location: {
        location_identity_digest: digest("4"),
        display_name: "中国仓库",
        selected_is_recommended: true,
      },
      creation_fact_identity_digest: digest("5"),
      attribute_selection_digest: digest("f"),
      seller_stock_quantity: 200,
      condition: "NEW",
      preorder: { is_pre_order: false, days_to_ship: 0 },
      variation_summary: creation.variation_summary,
    } : null,
    attribute_selection: selected ? {
      selection_digest: digest("f"),
      category_identity_digest: digest("1"),
      attribute_tree_digest: digest("b"),
      selection_count: 3,
      approved_by: "Kyle",
    } : null,
    blocker: null,
    next_action: {
      action: selected
        ? "review_shopee_global_plan"
        : awaitingRequired
          ? "complete_official_category_attributes"
          : "select_channel_category",
      target_focus: "shopee:GLOBAL",
    },
    external_writes_performed: [],
  };
}

function approvedShopeeGlobalPlan(candidate) {
  return {
    schema_version: "approved-shopee-global-plan/v1",
    approved_by: "Kyle",
    literal_consent_recorded: true,
    mode: candidate.mode,
    status: "APPROVED",
    counts: candidate.counts,
    digests: {
      ...candidate.digests,
      approved_plan_digest: "f".repeat(64),
    },
  };
}

async function oneClickAsyncControlPlaneContract(browser, viewport) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  let dashboardReads = 0;
  let previewReads = 0;
  let publishPosts = 0;
  let statusReads = 0;
  let manualAcceptancePosts = 0;
  let manualAcceptanceBody = null;
  let terminal = false;
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== baseUrl) {
      requests.push({ method: request.method(), url: request.url(), external: true });
      return route.abort("blockedbyclient");
    }
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ method: request.method(), url: request.url(), external: false });
    if (url.pathname === "/api/product-workspace/dashboard") {
      dashboardReads += 1;
      if (terminal && dashboardReads > 1 && viewport.width === 390) {
        return route.fulfill(jsonResponse({
          ok: false,
          error: "temporary final dashboard failure",
        }, 503));
      }
      return route.fulfill(jsonResponse(oneClickDashboard({ terminal })));
    }
    if (url.pathname === "/api/product-workspace/publish-preview") {
      previewReads += 1;
      return route.fulfill(jsonResponse({
        ok: true,
        persisted: false,
        external_writes_performed: [],
        preview: oneClickProjection(
          "release-batch-preparation/v2",
          "preview",
        ),
      }));
    }
    if (
      url.pathname === "/api/product-workspace/publish"
      && request.method() === "POST"
    ) {
      publishPosts += 1;
      await page.waitForTimeout(300);
      if (viewport.width === 390) {
        return route.fulfill(jsonResponse({
          ok: false,
          error: "committed but response lost",
        }, 503));
      }
      return route.fulfill(jsonResponse({
        ok: true,
        accepted: true,
        external_writes_performed: [],
        job: oneClickPendingJobProjection(),
      }, 202));
    }
    if (url.pathname === "/api/product-workspace/publish-status") {
      statusReads += 1;
      if (statusReads === 1 && viewport.width !== 390) {
        return route.fulfill(jsonResponse({
          ok: false,
          error: "temporary status transport failure",
        }, 503));
      }
      if (
        statusReads === 2
        || (viewport.width === 390 && statusReads === 1)
      ) {
        return route.fulfill(jsonResponse({
          ok: true,
          persisted: true,
          job: oneClickProjection(
            "oneclick-release-status/v2",
            "running",
            "RUNNING",
          ),
        }));
      }
      terminal = true;
      return route.fulfill(jsonResponse({
        ok: true,
        persisted: true,
        job: oneClickProjection(
          "oneclick-release-status/v2",
          "terminal",
          "WAITING_MANUAL_ACCEPTANCE",
        ),
      }));
    }
    if (
      url.pathname === "/api/product-workspace/release-target/manual-verify"
      && request.method() === "POST"
    ) {
      manualAcceptancePosts += 1;
      manualAcceptanceBody = request.postDataJSON();
      const dashboard = oneClickDashboard({
        terminal: true,
        warningAccepted: true,
      });
      return route.fulfill(jsonResponse({
        ok: true,
        external_writes_performed: [],
        job: dashboard.release_v1.oneclick_controlplane,
        dashboard,
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
    try {
      await page.waitForFunction(() => {
        const button = document.querySelector("#releasePrimaryActionButton");
        return button && !button.hidden && !button.disabled;
      });
    } catch (error) {
      throw new Error(
        `${error.message}\nmessage=${
          await page.locator("#oneClickExecutionMessage").innerText()
        }\nnote=${await page.locator("#publishAllNote").innerText()
        }\nerrors=${JSON.stringify(errors)}`,
      );
    }
    check(
      previewReads === 1,
      `one-click ${viewport.width}: preview is read exactly once`,
      { previewReads, requests },
    );
    const groupText = await page.locator("#oneClickExecutionGroups").innerText();
    check(
      groupText.includes("Shopee")
        && groupText.includes("TikTok")
        && groupText.includes("Ozon"),
      `one-click ${viewport.width}: mixed server-owned groups are visible`,
      groupText,
    );
    const primaryActionButton = page.locator("#releasePrimaryActionButton");
    await primaryActionButton.focus();
    await page.keyboard.press("Enter");
    await primaryActionButton.evaluate((button) => {
      button.click();
      button.click();
    });
    await page.keyboard.press("Space");
    await page.locator("#offerId").fill("3828540232");
    await page.locator("#lookupForm").evaluate((form) => form.requestSubmit());
    await page.waitForTimeout(100);
    check(
      page.url().includes("offer_id=3828540231"),
      `one-click ${viewport.width}: offer switch is blocked until the 202 job identity returns`,
      page.url(),
    );
    await page.locator("#offerId").fill("3828540231");
    try {
      await page.waitForFunction(() => {
        if (
          document.querySelector("#releasePrimaryActionButton")
            ?.dataset.oneclickAction === "review_verified_observation_warning"
        ) return true;
        return false;
      }, null, { timeout: 8000 });
    } catch (error) {
      throw new Error(
        `${error.message}\nmessage=${
          await page.locator("#oneClickExecutionMessage").innerText()
        }\nnext=${await page.locator("#oneClickNextActionButton")
          .getAttribute("data-oneclick-action")
        }\nerrors=${JSON.stringify(errors)}`,
      );
    }
    check(
      publishPosts === 1,
      `one-click ${viewport.width}: double click and keyboard do not duplicate POST`,
      { publishPosts, requests },
    );
    check(
      statusReads >= (viewport.width === 390 ? 2 : 3),
      `one-click ${viewport.width}: 5xx/GET failure recovers by status-only and reaches terminal`,
      { statusReads, requests },
    );
    const manualCard = page.locator(
      '[data-oneclick-target="tiktok:GB"]',
    );
    check(
      await manualCard.isVisible()
        && (await manualCard.innerText()).includes("TikTok"),
      `one-click ${viewport.width}: submitted-unverified target is retained`,
      await manualCard.innerText(),
    );
    check(
      !(await page.locator("#publishAllCheckbox").isEnabled())
        && !(await page.locator("#publishAllButton").isEnabled()),
      `one-click ${viewport.width}: terminal job cannot be submitted again`,
    );
    const nextActionButton = page.locator("#releasePrimaryActionButton");
    check(
      await nextActionButton.getAttribute("data-oneclick-action")
        === "review_verified_observation_warning",
      `one-click ${viewport.width}: verified warning is the canonical controlled action`,
      await nextActionButton.getAttribute("data-oneclick-action"),
    );
    const warningForm = page.locator(
      '[data-oneclick-observation-review="shopee:MY"]',
    );
    check(
      await warningForm.isVisible(),
      `one-click ${viewport.width}: Shopee warning has a dedicated acceptance form`,
    );
    const warningText = await warningForm.innerText();
    check(
      warningText.includes("官方硬事实已验证")
        && warningText.includes("存在平台派生翻译/图片观察警告，等待Kyle人工验收")
        && warningText.includes("copy:language_signal_weak")
        && warningText.includes("global_image:rehosted_order_unverifiable"),
      `one-click ${viewport.width}: warning form exposes only controlled redacted evidence`,
      warningText,
    );
    check(
      await warningForm.locator('[name="marketplace_product_id"]').count() === 0
        && await warningForm.locator("[name^='check_']").count() === 0,
      `one-click ${viewport.width}: warning form has no raw item identity or API-less checklist`,
    );
    await nextActionButton.click();
    const warningCheckbox = warningForm.locator(
      '[name="manual_review_accepted"]',
    );
    check(
      await warningCheckbox.evaluate(
        (element) => document.activeElement === element,
      ),
      `one-click ${viewport.width}: canonical warning action focuses its controlled acceptance`,
    );
    await warningCheckbox.check();
    await nextActionButton.click();
    await nextActionButton.click();
    await page.waitForFunction(() => (
      !document.querySelector("[data-oneclick-observation-review='shopee:MY']")
      && document.querySelector("#releasePrimaryActionButton")
        ?.dataset.oneclickAction === "verify_submission_in_marketplace"
    ));
    check(
      manualAcceptancePosts === 1,
      `one-click ${viewport.width}: warning acceptance is posted exactly once`,
      { manualAcceptancePosts, manualAcceptanceBody, requests },
    );
    check(
      manualAcceptanceBody?.target_label === "shopee:MY"
        && manualAcceptanceBody?.verified_by === "Kyle"
        && manualAcceptanceBody?.user_verified === true
        && manualAcceptanceBody?.manual_review_accepted === true
        && /^[a-f0-9]{64}$/.test(
          manualAcceptanceBody?.observation_evidence_digest || "",
        )
        && !Object.hasOwn(
          manualAcceptanceBody || {},
          "marketplace_product_id",
        )
        && !Object.hasOwn(manualAcceptanceBody || {}, "checks"),
      `one-click ${viewport.width}: warning acceptance body is minimal and digest-bound`,
      manualAcceptanceBody,
    );
    check(
      await nextActionButton.getAttribute("data-oneclick-action")
        === "verify_submission_in_marketplace",
      `one-click ${viewport.width}: accepted warning advances to the independent API-less form`,
      await nextActionButton.getAttribute("data-oneclick-action"),
    );
    const shopeeCard = page.locator(
      '[data-oneclick-target="shopee:MY"]',
    );
    check(
      (await shopeeCard.innerText()).includes("已完成官方回读"),
      `one-click ${viewport.width}: accepted warning becomes canonical SUCCEEDED`,
      await shopeeCard.innerText(),
    );
    check(
      await manualCard.isVisible()
        && (await manualCard.innerText()).includes("已提交，等待人工验收"),
      `one-click ${viewport.width}: independent API-less acceptance remains pending`,
    );
    const statusReadsAfterAcceptance = statusReads;
    await page.waitForTimeout(1200);
    check(
      publishPosts === 1
        && manualAcceptancePosts === 1
        && statusReads === statusReadsAfterAcceptance,
      `one-click ${viewport.width}: acceptance is terminal and never republishes, retries, or resumes polling`,
      {
        publishPosts,
        manualAcceptancePosts,
        statusReads,
        statusReadsAfterAcceptance,
        dashboardReads,
      },
    );
    check(
      dashboardReads === 2,
      `one-click ${viewport.width}: terminal job triggers one final dashboard read`,
      { dashboardReads, requests },
    );
    const overflow = await overflowAudit(page);
    check(
      overflow.pageOverflow <= 2,
      `one-click ${viewport.width}: no horizontal overflow`,
      overflow,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      `one-click ${viewport.width}: no console/page errors`,
      errors,
    );
    check(
      requests.filter((row) => row.external).length === 0,
      `one-click ${viewport.width}: external network budget is zero`,
      requests,
    );
  } finally {
    await context.close();
  }
}

async function releaseV2TerminalHistoryIsolationContract(browser, viewport) {
  const context = await browser.newContext({ viewport });
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
    requests.push({ method: request.method(), path: url.pathname });
    if (url.pathname === "/api/product-workspace/dashboard") {
      return route.fulfill(jsonResponse(oneClickDashboard({ terminal: true })));
    }
    if (url.pathname === "/api/product-workspace/publish-preview") {
      return route.fulfill(jsonResponse({
        ok: true,
        persisted: false,
        external_writes_performed: [],
        preview: oneClickProjection(
          "release-batch-preparation/v2",
          "preview",
        ),
      }));
    }
    if (url.pathname === "/api/product-workspace/collectbox-action/preview") {
      return route.fulfill(jsonResponse(
        collectboxActionProjection("SUCCEEDED"),
      ));
    }
    if (
      request.method() === "POST"
      && url.pathname === "/api/product-workspace/publish-shopee-global"
    ) {
      return route.fulfill(jsonResponse({
        ok: true,
        accepted: true,
        external_writes_performed: [],
        job: oneClickPendingJobProjection(),
      }, 202));
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
      `${baseUrl}/product-workspace?offer_id=3828540231`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForFunction(() => {
      const shopee = document.querySelector("#shopeeGlobalReleaseButton");
      const ozon = document.querySelector("#ozonReleaseButton");
      return shopee && ozon && !shopee.disabled && !ozon.disabled;
    });
    const primaryGroups = await page.locator(
      "#oneClickExecutionGroups",
    ).innerText();
    const primaryMessage = await page.locator(
      "#oneClickExecutionMessage",
    ).innerText();
    check(
      !primaryGroups.includes("涓婃")
        && !primaryMessage.includes("涓婃")
        && !primaryMessage.includes("鍘嗗彶"),
      `release-v2 ${viewport.width}: terminal history never re-enters the primary operation`,
      { primaryGroups, primaryMessage },
    );
    check(
      await page.locator("#releasePrimaryActionButton").isEnabled()
        && await page.locator("#shopeeGlobalReleaseButton").isEnabled()
        && await page.locator("#ozonReleaseButton").isEnabled(),
      `release-v2 ${viewport.width}: a terminal attempt does not disable a fresh platform attempt`,
    );
    await page.locator("#shopeeGlobalReleaseButton").click();
    await page.waitForTimeout(250);
    check(
      requests.filter((row) => (
        row.method === "POST"
        && row.path === "/api/product-workspace/publish-shopee-global"
      )).length === 1,
      `release-v2 ${viewport.width}: fresh Shopee attempt posts exactly once after terminal history`,
      requests,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      `release-v2 ${viewport.width}: terminal-history isolation has no console/page errors`,
      errors,
    );
  } finally {
    await context.close();
  }
}

async function oneClickContentRecoveryContract(browser, viewport) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  const blockedTargets = oneClickProjection(
    "release-batch-preparation/v2",
    "terminal",
  ).targets.map((target) => ({
    ...target,
    status: target.target_label === "shopee:MY"
      ? "BLOCKED_CAPABILITY" : target.status,
    classification: target.target_label === "shopee:MY"
      ? "BLOCKED_CAPABILITY" : target.classification,
    runnable_now: false,
    manual_after_submit: target.target_label === "shopee:MY"
      ? false : target.manual_after_submit,
    requires_human: target.target_label === "shopee:MY"
      ? false : target.requires_human,
    result: target.target_label === "shopee:MY" ? null : target.result,
    next_action: target.target_label === "shopee:MY"
      ? "review_approved_content_facts" : target.next_action,
    next_action_target: target.target_label === "shopee:MY"
      ? "shopee:MY" : target.next_action_target,
    reason: target.target_label === "shopee:MY"
      ? {
        category: "CONTENT",
        scope: "TARGET",
        code: "approved_shopee_category_missing",
        summary_code: "approved_shopee_category_missing",
        detail_digest: "e".repeat(64),
      } : target.reason,
  }));
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== baseUrl) {
      requests.push({ method: request.method(), url: request.url(), external: true });
      return route.abort("blockedbyclient");
    }
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ method: request.method(), url: request.url(), external: false });
    if (url.pathname === "/api/product-workspace/dashboard") {
      const dashboard = oneClickDashboard();
      dashboard.release_v1.canonical_next_action = {
        target_label: "shopee:MY",
        target_focus: "shopee:MY",
        canonical_status: "BLOCKED_CAPABILITY",
        action: "review_approved_content_facts",
        runnable: false,
      };
      return route.fulfill(jsonResponse(dashboard));
    }
    if (url.pathname === "/api/product-workspace/publish-preview") {
      const preview = oneClickProjection(
        "release-batch-preparation/v2",
        "terminal",
      );
      preview.targets = blockedTargets;
      preview.storefront_count = blockedTargets.filter(
        (target) => target.storefront,
      ).length;
      preview.control_row_count = blockedTargets.length
        - preview.storefront_count
        + preview.shared_controls.length;
      preview.runnable_target_count = 0;
      preview.summary.will_dispatch = [];
      preview.summary.manual_after_submit = ["tiktok:GB"];
      preview.summary.blocked = [
        "shopee:MY",
        "shopee:VN",
        "ozon:RU",
      ];
      preview.summary.already_terminal = ["tiktok:GB"];
      preview.canonical_next_action = {
        target_label: "shopee:MY",
        target_focus: "shopee:MY",
        canonical_status: "BLOCKED_CAPABILITY",
        action: "review_approved_content_facts",
        runnable: false,
      };
      return route.fulfill(jsonResponse({
        ok: true,
        persisted: false,
        external_writes_performed: [],
        preview,
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
    const nextButton = page.locator("#releasePrimaryActionButton");
    await nextButton.waitFor({ state: "visible" });
    check(
      !(await page.locator("#publishAllCheckbox").isEnabled()),
      `one-click blocker ${viewport.width}: zero runnable target cannot publish`,
    );
    check(
      (await nextButton.innerText()).trim().length > 0,
      `one-click blocker ${viewport.width}: content blocker has actionable label`,
      await nextButton.innerText(),
    );
    await nextButton.click();
    const target = page.locator("#listingCopyAssistant");
    check(
      await target.evaluate((element) => document.activeElement === element),
      `one-click blocker ${viewport.width}: content action focuses the owning content section`,
    );
    check(
      requests.filter((row) => row.method === "POST").length === 0,
      `one-click blocker ${viewport.width}: recovery navigation performs zero writes`,
      requests,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      `one-click blocker ${viewport.width}: no console/page errors`,
      errors,
    );
  } finally {
    await context.close();
  }
}

async function oneClickOfferSwitchCancelsStalePreviewContract(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  const firstOffer = "3828540231";
  const secondOffer = "3828540232";
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== baseUrl) {
      requests.push({ method: request.method(), url: request.url(), external: true });
      return route.abort("blockedbyclient");
    }
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ method: request.method(), url: request.url(), external: false });
    if (url.pathname === "/api/product-workspace/dashboard") {
      const offerId = url.searchParams.get("offer_id") || firstOffer;
      return route.fulfill(jsonResponse(oneClickDashboard({ offerId })));
    }
    if (url.pathname === "/api/product-workspace/collectbox-action/preview") {
      const offerId = url.searchParams.get("offer_id") || firstOffer;
      if (offerId === firstOffer) await page.waitForTimeout(600);
      const state = offerId === firstOffer ? "BLOCKED_IDENTITY" : "READY";
      return route.fulfill(
        jsonResponse(collectboxActionProjection(state)),
      ).catch(() => {});
    }
    if (url.pathname === "/api/product-workspace/publish-preview") {
      const offerId = url.searchParams.get("offer_id") || firstOffer;
      if (offerId === firstOffer) await page.waitForTimeout(600);
      const preview = oneClickProjection(
        "release-batch-preparation/v2",
        "preview",
      );
      preview.targets = preview.targets.filter((target) => (
        offerId === firstOffer
          ? target.target_label === "shopee:MY"
          : ["miaoshou:COMMON", "tiktok:GB"].includes(
            target.target_label,
          )
      ));
      preview.storefront_count = preview.targets.filter(
        (target) => target.storefront,
      ).length;
      preview.control_row_count = preview.targets.length
        - preview.storefront_count
        + preview.shared_controls.length;
      preview.runnable_target_count = 1;
      preview.summary.will_dispatch = preview.targets
        .filter((target) => (
          target.storefront
          && target.runnable_now
          && target.classification === "EXACT_READY_AUTOMATIC"
        ))
        .map((target) => target.target_label);
      preview.summary.manual_after_submit = preview.targets
        .filter((target) => (
          target.storefront
          && target.runnable_now
          && target.classification === "READY_SUBMIT_MANUAL"
        ))
        .map((target) => target.target_label);
      preview.summary.blocked = [];
      preview.prepare_pending = preview.targets
        .filter((target) => (
          target.storefront
          && target.classification === "PREPARE_PENDING"
        ))
        .map((target) => target.target_label);
      preview.preparation_pending_count = preview.prepare_pending.length;
      preview.start_allowed = preview.preparation_pending_count > 0;
      preview.runnable_target_count = 0;
      const storefront = preview.targets.find((target) => target.storefront);
      preview.canonical_next_action = {
        target_label: storefront.target_label,
        target_focus: storefront.target_label,
        canonical_status: "PENDING",
        action: "prepare_batch",
        runnable: false,
      };
      return route.fulfill(jsonResponse({
        ok: true,
        persisted: false,
        external_writes_performed: [],
        preview,
      })).catch(() => {});
    }
    const fixture = apiFixture(
      url,
      request.method(),
      { delayWeekly: false, delaySku: false, pending: {} },
    );
    return route.fulfill(fixture || jsonResponse({ ok: false }, 404));
  });
  try {
    await page.goto(`${baseUrl}/product-workspace?offer_id=${firstOffer}`, {
      waitUntil: "domcontentloaded",
    });
    await page.locator("#offerId").fill(secondOffer);
    await page.locator("#lookupForm").evaluate((form) => form.requestSubmit());
    const primary = page.locator("#collectboxActionButton");
    const collectboxStatus = page.locator("#collectboxActionStatus");
    try {
      await collectboxStatus.getByText("Shopee", { exact: true }).waitFor({
        state: "visible",
        timeout: 5000,
      });
    } catch (error) {
      throw new Error(
        `${error.message}\nmessage=${
          await page.locator("#collectboxActionMessage").innerText()
        }\nstatus=${
          await collectboxStatus.innerText()
        }\nerrors=${JSON.stringify(errors)}`,
      );
    }
    await page.waitForTimeout(900);
    check(
      await primary.isEnabled()
        && (await primary.innerText()).includes("TikTok / Shopee")
        && !(await page.locator("#collectboxActionMessage").innerText())
          .includes("批准计划身份不一致"),
      "collectbox offer switch: late preview from old offer is ignored",
      {
        label: await primary.innerText(),
        message: await page.locator("#collectboxActionMessage").innerText(),
        status: await collectboxStatus.innerText(),
      },
    );
    check(
      page.url().includes(`offer_id=${secondOffer}`),
      "collectbox offer switch: current URL retains the new offer",
      page.url(),
    );
    check(
      requests.filter((row) => row.method === "POST").length === 0,
      "collectbox offer switch: stale preview cancellation performs zero writes",
      requests,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      "collectbox offer switch: no console/page errors",
      errors,
    );
  } finally {
    await context.close();
  }
}

async function oneClickStrictFailureContract(browser, mode) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  let publishPosts = 0;
  let statusReads = 0;
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== baseUrl) {
      requests.push({ method: request.method(), url: request.url(), external: true });
      return route.abort("blockedbyclient");
    }
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ method: request.method(), url: request.url(), external: false });
    if (url.pathname === "/api/product-workspace/dashboard") {
      return route.fulfill(jsonResponse(oneClickDashboard()));
    }
    if (url.pathname === "/api/product-workspace/publish-preview") {
      return route.fulfill(jsonResponse({
        ok: true,
        persisted: false,
        external_writes_performed: [],
        preview: oneClickProjection(
          "release-batch-preparation/v2",
          "preview",
        ),
      }));
    }
    if (url.pathname === "/api/product-workspace/publish") {
      publishPosts += 1;
      const payload = {
        ok: true,
        accepted: true,
        external_writes_performed: [],
        job: oneClickPendingJobProjection(),
      };
      return route.fulfill(jsonResponse(
        payload,
        mode === "wrong-post-status" ? 200 : 202,
      ));
    }
    if (url.pathname === "/api/product-workspace/publish-status") {
      statusReads += 1;
      const job = oneClickProjection(
        "oneclick-release-status/v2",
        "running",
        mode === "malformed-status" ? "UNKNOWN_PHASE" : "RUNNING",
      );
      if (mode === "digest-drift") {
        job.digests.source_identity = "9".repeat(64);
      }
      if (mode === "target-proof-missing") {
        delete job.targets[0].digests.proof;
      }
      if (mode === "missing-target") {
        job.targets = job.targets.filter(
          (target) => target.target_label !== "shopee:VN",
        );
        job.storefront_count -= 1;
        job.summary.blocked = job.summary.blocked.filter(
          (label) => label !== "shopee:VN",
        );
      }
      if (mode === "dependency-drift") {
        const target = job.targets.find(
          (row) => row.target_label === "tiktok:GB",
        );
        target.dependency.prerequisite_status = "READY";
      }
      if (mode === "unknown-target-status") {
        const target = job.targets.find(
          (row) => row.target_label === "shopee:MY",
        );
        target.status = "UNKNOWN_TARGET_STATUS";
      }
      if (mode === "unknown-canonical-action") {
        job.canonical_next_action.action = "unknown_action";
      }
      return route.fulfill(jsonResponse({
        ok: true,
        persisted: true,
        job,
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
    await page.waitForFunction(() => {
      const button = document.querySelector("#releasePrimaryActionButton");
      return button && !button.hidden && !button.disabled;
    });
    await page.locator("#releasePrimaryActionButton").focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => (
      document.querySelector("#releasePrimaryActionButton")?.disabled === true
    ));
    if (mode === "wrong-post-status") {
      await page.waitForFunction(() => (
        document.querySelector("#oneClickExecutionMessage")?.textContent
          ?.includes("HTTP 200")
      ));
      await page.waitForTimeout(1300);
      check(
        publishPosts === 1 && statusReads === 0,
        "one-click strict HTTP: HTTP 200 is rejected and never starts polling",
        { publishPosts, statusReads, requests },
      );
    } else {
      await page.waitForFunction(() => (
        document.querySelector("#oneClickNextActionButton")
          ?.dataset.oneclickAction === "refresh_release_state"
      ));
      await page.waitForTimeout(1300);
      check(
        publishPosts === 1 && statusReads === 1,
        "one-click strict schema: malformed status stops polling and never reposts",
        { publishPosts, statusReads, requests },
      );
    }
    check(
      unexpectedInteractionErrors(errors).length === 0,
      `one-click strict ${mode}: no console/page errors`,
      errors,
    );
    check(
      requests.filter((row) => row.external).length === 0,
      `one-click strict ${mode}: external network budget is zero`,
      requests,
    );
  } finally {
    await context.close();
  }
}

async function oneClickFeatureDisabledContract(browser) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
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
    if (url.origin !== baseUrl) {
      requests.push({ method: request.method(), url: request.url(), external: true });
      return route.abort("blockedbyclient");
    }
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ method: request.method(), url: request.url(), external: false });
    if (url.pathname === "/api/product-workspace/dashboard") {
      return route.fulfill(jsonResponse(oneClickDashboard()));
    }
    if (url.pathname === "/api/product-workspace/publish-preview") {
      const preview = oneClickProjection(
        "release-batch-preparation/v2",
        "preview",
      );
      preview.targets = preview.targets.map((target) => (
        target.classification !== "PREPARE_PENDING"
          ? target
          : {
            ...target,
            classification: "BLOCKED_CAPABILITY",
            status: "BLOCKED_CAPABILITY",
            runnable_now: false,
            next_action: "enable_oneclick_dispatch",
            next_action_target: null,
            reason: {
              category: "CAPABILITY",
              scope: "TARGET",
              code: "oneclick_dispatch_disabled",
              summary_code: "channel_capability_status",
              detail_digest: "9".repeat(64),
            },
          }
      ));
      preview.runnable_target_count = 0;
      preview.prepare_pending = [];
      preview.preparation_pending_count = 0;
      preview.start_allowed = false;
      preview.summary.will_dispatch = [];
      preview.summary.manual_after_submit = [];
      preview.summary.blocked = [
        "shopee:MY",
        "tiktok:GB",
        "shopee:VN",
        "ozon:RU",
      ];
      preview.dispatch_capability = {
        schema_version: "oneclick-dispatch-capability/v1",
        enabled: false,
        source: "environment",
        reason_code: "oneclick_dispatch_disabled",
        next_action: "enable_oneclick_dispatch",
      };
      preview.canonical_next_action = {
        target_label: null,
        target_focus: null,
        canonical_status: "BLOCKED_CAPABILITY",
        action: "enable_oneclick_dispatch",
        runnable: false,
      };
      return route.fulfill(jsonResponse({
        ok: true,
        persisted: false,
        external_writes_performed: [],
        preview,
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
    const nextButton = page.locator("#oneClickNextActionButton");
    try {
      await page.waitForFunction(() => (
        document.querySelector("#publishAllNote")?.textContent
          ?.includes("执行能力当前关闭")
      ));
    } catch (error) {
      throw new Error(
        `${error.message}\nmessage=${
          await page.locator("#oneClickExecutionMessage").innerText()
        }\nnote=${await page.locator("#publishAllNote").innerText()
        }\nerrors=${JSON.stringify(errors)}`,
      );
    }
    check(
      !(await nextButton.isVisible())
        && !(await page.locator("#publishAllCheckbox").isEnabled()),
      "one-click feature disabled: passive state is explanatory and publish stays disabled",
    );
    check(
      requests.filter((row) => row.method === "POST").length === 0,
      "one-click feature disabled: recovery navigation performs zero writes",
      requests,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      "one-click feature disabled: no console/page errors",
      errors,
    );
  } finally {
    await context.close();
  }
}

async function shopeeGlobalPreApprovalEntryContract(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  const candidate = shopeeGlobalCandidate();
  let globalReads = 0;
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  const dashboard = oneClickDashboard();
  dashboard.release_v1.plan_approved = false;
  dashboard.release_v1.eligible_for_plan_approval = false;
  dashboard.release_v1.miaoshou_prepared = false;
  dashboard.release_v1.publish_ready = false;
  dashboard.release_v1.oneclick_controlplane = null;
  dashboard.release_v1.canonical_next_action = null;
  dashboard.release_v1.recovery_actions = [{
    code: "review_shopee_global_plan",
    label: "核对并批准 Shopee 全球商品方案",
    detail: "只有 Kyle 对当前精确候选完成批准后，ReleasePlan 才会开放。",
  }];
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== baseUrl) {
      requests.push({ method: request.method(), url: request.url(), external: true });
      return route.abort("blockedbyclient");
    }
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ method: request.method(), url: request.url(), external: false });
    if (url.pathname === "/api/product-workspace/dashboard") {
      return route.fulfill(jsonResponse(dashboard));
    }
    if (
      url.pathname
        === "/api/product-workspace/shopee-global-plan-preview"
    ) {
      globalReads += 1;
      return route.fulfill(jsonResponse({
        ok: true,
        schema_version: "shopee-global-plan-preview/v1",
        offer_id: "3828540231",
        product_revision: 31,
        candidate,
        approval: null,
        approval_current: false,
        external_writes_performed: [],
      }));
    }
    if (
      url.pathname
        === "/api/product-workspace/channel-category-decision-preview"
    ) {
      return route.fulfill(jsonResponse(
        shopeeCategoryPreview({ status: "SELECTED" }),
      ));
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
    const recovery = page.locator(
      '[data-release-recovery="review_shopee_global_plan"]',
    );
    const form = page.locator(
      "#releasePlanRecoveryReview .shopee-global-plan-approval-form",
    );
    await form.waitFor({ state: "visible" });
    check(
      await page.locator("#releasePlanCheckbox").isDisabled()
      && await recovery.isEnabled()
      && await form.isVisible(),
      "Shopee Global pre-approval: disabled ReleasePlan gate exposes the exact approval panel",
    );
    await recovery.click();
    await page.waitForFunction(() => (
      document.activeElement?.getAttribute("name")
        === "confirm_approved_shopee_global_plan"
    ));
    check(
      globalReads === 1,
      "Shopee Global pre-approval: one official read supplies the candidate",
      { globalReads, requests },
    );
    check(
      requests.filter((row) => row.method === "POST").length === 0,
      "Shopee Global pre-approval: opening and focusing the panel performs zero POSTs",
      requests,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      "Shopee Global pre-approval: no console/page errors",
      errors,
    );
  } finally {
    await context.close();
  }
}

async function shopeeGlobalCapabilityBlockerContract(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  let globalReads = 0;
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  const dashboard = oneClickDashboard();
  dashboard.release_v1.plan_approved = false;
  dashboard.release_v1.eligible_for_plan_approval = false;
  dashboard.release_v1.miaoshou_prepared = false;
  dashboard.release_v1.publish_ready = false;
  dashboard.release_v1.oneclick_controlplane = null;
  dashboard.release_v1.canonical_next_action = null;
  dashboard.release_v1.recovery_actions = [{
    code: "review_shopee_global_plan",
    label: "核对并批准 Shopee 全球商品方案",
    detail: "必须先取得官方只读候选。",
  }];
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== baseUrl) {
      requests.push({ method: request.method(), url: request.url(), external: true });
      return route.abort("blockedbyclient");
    }
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ method: request.method(), url: request.url(), external: false });
    if (url.pathname === "/api/product-workspace/dashboard") {
      return route.fulfill(jsonResponse(dashboard));
    }
    if (
      url.pathname
        === "/api/product-workspace/shopee-global-plan-preview"
    ) {
      globalReads += 1;
      return route.fulfill(jsonResponse({
        ok: true,
        schema_version: "shopee-global-plan-preview/v1",
        offer_id: "3828540231",
        product_revision: 31,
        candidate: {
          schema_version: "shopee-global-plan-candidate/v1",
          status: "BLOCKED_CAPABILITY",
          planning_allowed: false,
          reason_category: "CAPABILITY",
          reason_code: "shopee_official_global_list_unavailable",
          blocker_codes: ["shopee_official_global_list_unavailable"],
        },
        approval: null,
        approval_current: false,
        external_writes_performed: [],
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
    const recovery = page.locator(
      '[data-release-recovery="review_shopee_global_plan"]',
    );
    const blocker = page.locator(
      "#releasePlanRecoveryReview .shopee-global-plan-review.is-blocked",
    );
    await blocker.waitFor({ state: "visible" });
    await recovery.click();
    await page.waitForFunction(() => (
      document.activeElement?.classList
        .contains("shopee-global-plan-preview-retry")
    ));
    check(
      (await blocker.innerText()).includes("Global Product")
      && (await blocker.innerText()).includes("权限"),
      "Shopee Global blocker: official-list capability failure is actionable and visible after the real recovery click",
      await blocker.innerText(),
    );
    check(
      globalReads === 1
      && requests.filter((row) => row.method === "POST").length === 0,
      "Shopee Global blocker: the visible diagnosis performs one GET and zero writes",
      { globalReads, requests },
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      "Shopee Global blocker: no console/page errors",
      errors,
    );
  } finally {
    await context.close();
  }
}

async function shopeeGlobalApprovalResponseLossContract(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  const candidate = shopeeGlobalCandidate();
  let globalReads = 0;
  let approvalPosts = 0;
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== baseUrl) {
      requests.push({ method: request.method(), url: request.url(), external: true });
      return route.abort("blockedbyclient");
    }
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ method: request.method(), url: request.url(), external: false });
    if (url.pathname === "/api/product-workspace/dashboard") {
      return route.fulfill(jsonResponse(oneClickDashboard()));
    }
    if (url.pathname === "/api/product-workspace/publish-preview") {
      const preview = oneClickProjection(
        "release-batch-preparation/v2",
        "preview",
      );
      const global = preview.shared_controls[0];
      global.status = "BLOCKED_CAPABILITY";
      global.classification = "BLOCKED_CAPABILITY";
      global.next_action = "review_shopee_global_plan";
      global.reason = {
        category: "CONTENT",
        scope: "TARGET",
        code: "approved_shopee_global_plan_required",
        summary_code: "approved_shopee_global_plan_required",
        detail_digest: "f".repeat(64),
      };
      for (const target of preview.targets.filter(
        (row) => row.target_label.startsWith("shopee:"),
      )) {
        target.status = "BLOCKED_CAPABILITY";
        target.classification = "BLOCKED_CAPABILITY";
        target.next_action = "resolve_prerequisite_target";
        target.reason = null;
        target.dependency = {
          policy_version: "oneclick-target-dependency/mvp-unblocked-v1",
          state: "BLOCKED",
          satisfied: false,
          prerequisite_target: "shopee:GLOBAL",
          prerequisite_status: global.status,
          prerequisite: {
            target_label: global.target_label,
            status: global.status,
            reason: global.reason,
            next_action: global.next_action,
            digests: {
              prepared_command: global.digests.prepared_command,
              proof: global.digests.proof,
              shared_resource: global.digests.shared_resource,
              shared_resource_context: global.digests.shared_resource_context,
            },
          },
        };
      }
      preview.prepare_pending = preview.prepare_pending.filter(
        (label) => !label.startsWith("shopee:"),
      );
      preview.preparation_pending_count = preview.prepare_pending.length;
      preview.start_allowed = preview.preparation_pending_count > 0;
      preview.summary.blocked = ["shopee:MY", "shopee:VN", "ozon:RU"];
      return route.fulfill(jsonResponse({
        ok: true,
        persisted: false,
        external_writes_performed: [],
        preview,
      }));
    }
    if (
      url.pathname
        === "/api/product-workspace/shopee-global-plan-preview"
    ) {
      globalReads += 1;
      const approved = approvalPosts > 0;
      return route.fulfill(jsonResponse({
        ok: true,
        schema_version: "shopee-global-plan-preview/v1",
        offer_id: "3828540231",
        product_revision: 31,
        candidate,
        approval: approved ? approvedShopeeGlobalPlan(candidate) : null,
        approval_current: approved,
        external_writes_performed: [],
      }));
    }
    if (
      url.pathname
        === "/api/product-workspace/channel-category-decision-preview"
    ) {
      return route.fulfill(jsonResponse(
        shopeeCategoryPreview({ status: "SELECTED" }),
      ));
    }
    if (
      url.pathname
        === "/api/product-workspace/shopee-global-plan-approval"
      && request.method() === "POST"
    ) {
      approvalPosts += 1;
      return route.fulfill(jsonResponse({
        ok: false,
        error: "commit succeeded but response failed",
      }, 503));
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
    const form = page.locator(".shopee-global-plan-approval-form");
    await form.waitFor({ state: "visible" });
    const approvalButton = form.locator("button[type='submit']");
    check(
      await approvalButton.isDisabled(),
      "Shopee Global approval: submit stays disabled before explicit consent",
    );
    await form.locator(
      "input[name='confirm_approved_shopee_global_plan']",
    ).check();
    check(
      await approvalButton.isEnabled(),
      "Shopee Global approval: explicit consent enables exactly one submit",
    );
    const primaryAction = page.locator("#releasePrimaryActionButton");
    check(
      await primaryAction.getAttribute("data-oneclick-action")
        === "review_shopee_global_plan",
      "Shopee Global approval: the one visible action is bound to the server review action",
      await primaryAction.getAttribute("data-oneclick-action"),
    );
    await primaryAction.click();
    await page.waitForTimeout(250);
    check(
      approvalPosts === 1,
      "Shopee Global approval: the one visible action dispatches the approved form once",
      {
        approvalPosts,
        globalReads,
        primaryAction: await primaryAction.getAttribute("data-oneclick-action"),
        forms: await page.locator(".shopee-global-plan-approval-form").count(),
      },
    );
    check(
      globalReads >= 2,
      "Shopee Global approval: response loss is reconciled by a GET-only reread",
      { approvalPosts, globalReads, requests },
    );
    check(
      approvalPosts === 1 && globalReads >= 2,
      "Shopee Global approval loss: exactly one POST and GET-only reconciliation",
      { approvalPosts, globalReads, requests },
    );
    check(
      requests.filter((row) => (
        row.method === "POST"
        && row.url.includes("/api/product-workspace/publish")
      )).length === 0,
      "Shopee Global approval loss: no channel publish POST",
      requests,
    );
    check(
      unexpectedInteractionErrors(errors).length === 0,
      "Shopee Global approval loss: no console/page errors",
      errors,
    );
  } finally {
    await context.close();
  }
}

async function shopeeCategoryDecisionContract(browser, viewport) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  const candidate = shopeeGlobalCandidate();
  let categoryPosts = 0;
  let categoryReads = 0;
  let selected = false;
  let submittedBody = null;
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  const dashboard = oneClickDashboard();
  dashboard.release_v1.plan_approved = false;
  dashboard.release_v1.eligible_for_plan_approval = false;
  dashboard.release_v1.miaoshou_prepared = false;
  dashboard.release_v1.publish_ready = false;
  dashboard.release_v1.oneclick_controlplane = null;
  dashboard.release_v1.canonical_next_action = null;
  dashboard.release_v1.recovery_actions = [{
    code: "review_shopee_global_plan",
    label: "核对并批准 Shopee 全球商品方案",
    detail: "先固化完整 NEW_GLOBAL 创建决定。",
  }];
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== baseUrl) {
      requests.push({ method: request.method(), url: request.url(), external: true });
      return route.abort("blockedbyclient");
    }
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ method: request.method(), url: request.url(), external: false });
    if (url.pathname === "/api/product-workspace/dashboard") {
      return route.fulfill(jsonResponse(dashboard));
    }
    if (url.pathname === "/api/product-workspace/shopee-global-plan-preview") {
      return route.fulfill(jsonResponse({
        ok: true,
        schema_version: "shopee-global-plan-preview/v1",
        offer_id: "3828540231",
        product_revision: 31,
        candidate,
        approval: null,
        approval_current: false,
        external_writes_performed: [],
      }));
    }
    if (
      url.pathname
        === "/api/product-workspace/channel-category-decision-preview"
    ) {
      categoryReads += 1;
      return route.fulfill(jsonResponse(
        shopeeCategoryPreview({
          // The live server keeps the conservative platform status while
          // required official values are incomplete.  A null blocker plus
          // usable options is an actionable decision form, not an observer
          // failure.
          status: selected ? "SELECTED" : "BLOCKED_CAPABILITY",
        }),
      ));
    }
    if (
      url.pathname === "/api/product-workspace/channel-category-decision"
      && request.method() === "POST"
    ) {
      categoryPosts += 1;
      submittedBody = request.postDataJSON();
      selected = true;
      return route.fulfill(jsonResponse(
        shopeeCategoryPreview({ status: "RECHECK_REQUIRED" }),
      ));
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
    const scope = "#releasePlanRecoveryReview";
    await page.locator(`${scope} .channel-category-decision-form`)
      .waitFor({ state: "visible" });
    check(
      await page.locator(`${scope} .shopee-global-plan-approval-form`).count()
        === 0,
      "Shopee category: final plan approval is absent before full decision",
      viewport,
    );
    check(
      await page.locator(
        `${scope} select[name="selected_brand_identity_digest"]`,
      ).count() === 0
      && await page.locator(
        `${scope} select[name="selected_location_identity_digest"]`,
      ).count() === 0,
      "Shopee category: fixed brand/location are not editable controls",
      viewport,
    );
    const fixedFacts = await page.locator(
      `${scope} .channel-category-fixed-fact`,
    ).allTextContents();
    check(
      fixedFacts.some((text) => text.includes("NoBrand"))
      && fixedFacts.some((text) => text.includes("中国仓库")),
      "Shopee category: fixed no-brand and China warehouse are visible",
      viewport,
    );
    await page.locator(
      `${scope} [data-selection-kind="SINGLE"]`,
    ).selectOption("9".repeat(64));
    await page.locator(
      `${scope} [data-selection-kind="MULTI"]`,
    ).check();
    await page.locator(
      `${scope} [data-selection-kind="TEXT"]`,
    ).fill("PVC wall decal");
    for (const name of [
      "confirm_channel_category_selection",
      "confirm_seller_stock_quantity",
      "confirm_condition_and_preorder",
      "confirm_required_attribute_selections",
    ]) {
      await page.locator(`${scope} input[name="${name}"]`).check();
    }
    const submit = page.locator(
      `${scope} .channel-category-decision-form button[type="submit"]`,
    );
    check(
      await submit.isEnabled(),
      "Shopee category: full explicit decision enables one save",
      viewport,
    );
    await submit.click();
    await page.locator(`${scope} .shopee-global-plan-approval-form`)
      .waitFor({ state: "visible" });
    check(
      categoryPosts === 1
      && categoryReads >= 2
      && submittedBody?.required_attribute_selections?.length === 3
      && submittedBody?.confirm_seller_stock_quantity === true
      && submittedBody?.confirm_condition_and_preorder === true,
      "Shopee category: one POST binds attributes, brand/location and creation facts, then GET-only rechecks",
      { categoryPosts, categoryReads, submittedBody },
    );
    const postsBeforeReload = categoryPosts;
    await page.reload({ waitUntil: "networkidle" });
    await page.locator(`${scope} .shopee-global-plan-approval-form`)
      .waitFor({ state: "visible" });
    check(
      categoryPosts === postsBeforeReload
      && await page.locator(`${scope} .channel-category-decision.is-selected`)
        .isVisible(),
      "Shopee category: refresh restores persisted decision without another POST",
      { categoryPosts, categoryReads },
    );
    check(
      requests.filter((row) => (
        row.method === "POST"
        && row.url.includes("/api/product-workspace/publish")
      )).length === 0
      && requests.filter((row) => row.external).length === 0,
      "Shopee category: decision journey performs zero channel publish/external requests",
      requests,
    );
    const overflow = await page.evaluate(() => (
      document.documentElement.scrollWidth > window.innerWidth + 1
    ));
    check(!overflow, "Shopee category: no horizontal overflow", viewport);
    check(
      unexpectedInteractionErrors(errors).length === 0,
      "Shopee category: no console/page errors",
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

async function simplifiedPlatformPublishContract(browser, viewport) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const requests = [];
  const errors = [];
  const artifactRoot = process.env.ORBIT_BROWSER_ARTIFACT_DIR || "";
  const suffix = `${viewport.width}x${viewport.height}`;
  let tiktokAttempt = 0;
  let releaseFirstTiktok;
  const firstTiktokGate = new Promise((resolve) => {
    releaseFirstTiktok = resolve;
  });
  const screenshot = async (name) => {
    if (!artifactRoot) return;
    fs.mkdirSync(artifactRoot, { recursive: true });
    await page.locator("#releasePrimaryActionPanel").screenshot({
      path: path.join(artifactRoot, `${suffix}-${name}.png`),
    });
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
    requests.push({ method: request.method(), path: url.pathname });
    if (url.pathname === "/api/product-workspace/dashboard") {
      return route.fulfill(jsonResponse(oneClickDashboard()));
    }
    if (url.pathname === "/api/product-workspace/collectbox-action/preview") {
      return route.fulfill(jsonResponse(collectboxActionProjection("SUCCEEDED")));
    }
    if (url.pathname === "/api/product-workspace/collectbox-action/start") {
      return route.fulfill(jsonResponse(collectboxActionProjection("SUCCEEDED")));
    }
    if (url.pathname === "/api/product-workspace/publish-tiktok") {
      tiktokAttempt += 1;
      if (tiktokAttempt === 1) {
        await firstTiktokGate;
        return route.fulfill(jsonResponse({
          schema_version: "miaoshou-platform-publish-result/v1",
          ok: false,
          platform: "TIKTOK",
          success: false,
          message: "TikTok 发布失败：妙手拒绝了本次请求",
          target_count: 6,
          successful_target_count: 0,
          failed_targets: ["tiktok:LH_PH"],
          retryable: true,
        }));
      }
      return route.fulfill(jsonResponse({
        schema_version: "miaoshou-platform-publish-result/v1",
        ok: true,
        platform: "TIKTOK",
        success: true,
        message: "TikTok 发布成功",
        target_count: 6,
        successful_target_count: 6,
        failed_targets: [],
        retryable: true,
      }));
    }
    if (url.pathname === "/api/product-workspace/publish-shopee-global") {
      // Production serializes writes for one approved product. The second
      // platform may be clicked and remain publishing, but its response is
      // released only after the first request leaves the shared ledger.
      await firstTiktokGate;
      return route.fulfill(jsonResponse({
        schema_version: "miaoshou-platform-publish-result/v1",
        ok: true,
        platform: "SHOPEE_GLOBAL",
        success: true,
        message: "Shopee 全球商品 发布成功",
        target_count: 1,
        successful_target_count: 1,
        failed_targets: [],
        retryable: true,
      }));
    }
    if (url.pathname === "/api/product-workspace/publish-ozon") {
      return route.fulfill(jsonResponse({
        schema_version: "miaoshou-platform-publish-result/v1",
        ok: true,
        platform: "OZON",
        success: true,
        message: "Ozon 发布成功",
        target_count: 1,
        successful_target_count: 1,
        failed_targets: [],
        retryable: true,
      }));
    }
    return route.fulfill(jsonResponse({ ok: false }, 404));
  });
  try {
    await page.goto(`${baseUrl}/product-workspace?offer_id=3828540231`, {
      waitUntil: "domcontentloaded",
    });
    const tiktok = page.locator("#releasePrimaryActionButton");
    const shopee = page.locator("#shopeeGlobalReleaseButton");
    const ozon = page.locator("#ozonReleaseButton");
    await page.waitForFunction(() => (
      document.querySelector("#releasePrimaryActionButton")?.disabled === false
      && document.querySelector("#shopeeGlobalReleaseButton")?.disabled === false
      && document.querySelector("#ozonReleaseButton")?.disabled === false
    ));
    await screenshot("initial");

    const firstClick = tiktok.click();
    await page.waitForFunction(() => (
      document.querySelector('[data-platform-publish-result="TIKTOK"]')
        ?.textContent?.includes("发布中")
    ));
    check(
      await shopee.isEnabled() && await ozon.isEnabled(),
      `simple publish ${suffix}: TikTok loading does not disable other platforms`,
    );
    await screenshot("publishing");

    const shopeeClick = shopee.click();
    releaseFirstTiktok();
    await firstClick;
    await shopeeClick;
    await page.waitForFunction(() => (
      document.querySelector('[data-platform-publish-result="SHOPEE_GLOBAL"]')
        ?.textContent?.includes("发布成功")
    ));
    await page.waitForFunction(() => (
      document.querySelector('[data-platform-publish-result="TIKTOK"]')
        ?.textContent?.includes("发布失败")
    ));
    const combined = await page.locator("#oneClickExecutionGroups").innerText();
    check(
      combined.includes("TikTok")
        && combined.includes("发布失败")
        && combined.includes("Shopee 全球商品")
        && combined.includes("发布成功"),
      `simple publish ${suffix}: independent success and failure remain visible`,
      combined,
    );
    check(await tiktok.isEnabled(), `simple publish ${suffix}: failed TikTok can retry`);
    await screenshot("failure-and-independent-success");

    const siblingsBeforeTiktokRetry = await page.evaluate(() => ({
      shopee: document.querySelector(
        '[data-platform-publish-result="SHOPEE_GLOBAL"]',
      )?.outerHTML,
      ozon: document.querySelector(
        '[data-platform-publish-result="OZON"]',
      )?.outerHTML,
    }));
    await tiktok.click();
    await page.waitForFunction(() => (
      document.querySelector('[data-platform-publish-result="TIKTOK"]')
        ?.textContent?.includes("发布成功")
    ));
    const siblingsAfterTiktokRetry = await page.evaluate(() => ({
      shopee: document.querySelector(
        '[data-platform-publish-result="SHOPEE_GLOBAL"]',
      )?.outerHTML,
      ozon: document.querySelector(
        '[data-platform-publish-result="OZON"]',
      )?.outerHTML,
    }));
    check(
      JSON.stringify(siblingsAfterTiktokRetry)
        === JSON.stringify(siblingsBeforeTiktokRetry),
      `simple publish ${suffix}: TikTok retry leaves sibling cards byte-for-byte unchanged`,
      { siblingsBeforeTiktokRetry, siblingsAfterTiktokRetry },
    );
    await ozon.click();
    await page.waitForFunction(() => (
      document.querySelector('[data-platform-publish-result="OZON"]')
        ?.textContent?.includes("发布成功")
    ));
    await screenshot("all-success-after-retry");

    const siblingsBeforeTiktokReimport = await page.evaluate(() => ({
      shopee: document.querySelector(
        '[data-platform-publish-result="SHOPEE_GLOBAL"]',
      )?.outerHTML,
      ozon: document.querySelector(
        '[data-platform-publish-result="OZON"]',
      )?.outerHTML,
    }));
    const collectboxReimport = page.locator("#collectboxActionButton");
    await collectboxReimport.click();
    await page.waitForFunction(() => (
      document.querySelector("#collectboxActionButton")?.disabled === false
    ));
    const siblingsAfterTiktokReimport = await page.evaluate(() => ({
      shopee: document.querySelector(
        '[data-platform-publish-result="SHOPEE_GLOBAL"]',
      )?.outerHTML,
      ozon: document.querySelector(
        '[data-platform-publish-result="OZON"]',
      )?.outerHTML,
    }));
    check(
      JSON.stringify(siblingsAfterTiktokReimport)
        === JSON.stringify(siblingsBeforeTiktokReimport),
      `simple publish ${suffix}: collectbox reimport leaves sibling cards byte-for-byte unchanged`,
      { siblingsBeforeTiktokReimport, siblingsAfterTiktokReimport },
    );
    await screenshot("sibling-cards-stable-after-reimport");

    const statusReads = requests.filter((row) => (
      row.path === "/api/product-workspace/publish-status"
      || row.path === "/api/product-workspace/publish-preview"
    ));
    const finalText = await page.locator("#oneClickExecutionPreview").innerText();
    check(statusReads.length === 0, `simple publish ${suffix}: zero post/publish polling`, statusReads);
    check(
      !/(人工验收|对账|结果未确认|SUBMITTED_UNVERIFIED|RECONCILIATION_REQUIRED)/.test(finalText),
      `simple publish ${suffix}: obsolete workflow text is absent`,
      finalText,
    );
    check(errors.length === 0, `simple publish ${suffix}: no browser errors`, errors);
  } finally {
    await context.close();
  }
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.ORBIT_CHROMIUM_BIN
      ? { executablePath: process.env.ORBIT_CHROMIUM_BIN }
      : {}),
  });
  try {
    if (
      process.env.ORBIT_BROWSER_CONTRACT_ONLY
        === "simplified-platform-publish"
    ) {
      await simplifiedPlatformPublishContract(
        browser,
        { width: 1440, height: 900 },
      );
      await simplifiedPlatformPublishContract(
        browser,
        { width: 390, height: 844 },
      );
      process.stdout.write(`${JSON.stringify({
        ok: failures.length === 0,
        failures,
        results,
      }, null, 2)}\n`);
      if (failures.length) process.exitCode = 1;
      return;
    }
    if (
      process.env.ORBIT_BROWSER_CONTRACT_ONLY
        === "shopee-global-approval"
    ) {
      await shopeeGlobalApprovalResponseLossContract(browser);
      return;
    }
    if (
      process.env.ORBIT_BROWSER_CONTRACT_ONLY
        === "collectbox-step-one"
    ) {
      await collectboxStepOnePrimaryActionContract(
        browser,
        { width: 1440, height: 900 },
      );
      await collectboxStepOnePrimaryActionContract(
        browser,
        { width: 390, height: 844 },
      );
      process.stdout.write(`${JSON.stringify({
        ok: failures.length === 0,
        failures,
        results,
      }, null, 2)}\n`);
      if (failures.length) process.exitCode = 1;
      return;
    }
    if (
      process.env.ORBIT_BROWSER_CONTRACT_ONLY
        === "collectbox-offer-switch"
    ) {
      await oneClickOfferSwitchCancelsStalePreviewContract(browser);
      process.stdout.write(`${JSON.stringify({
        ok: failures.length === 0,
        failures,
        results,
      }, null, 2)}\n`);
      if (failures.length) process.exitCode = 1;
      return;
    }
    if (
      process.env.ORBIT_BROWSER_CONTRACT_ONLY
        === "release-v2-terminal-history"
    ) {
      await releaseV2TerminalHistoryIsolationContract(
        browser,
        { width: 1440, height: 900 },
      );
      await releaseV2TerminalHistoryIsolationContract(
        browser,
        { width: 390, height: 844 },
      );
      process.stdout.write(`${JSON.stringify({
        ok: failures.length === 0,
        failures,
        results,
      }, null, 2)}\n`);
      if (failures.length) process.exitCode = 1;
      return;
    }
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
    await productReleasePlanSingleApprovalAction(browser);
    await productQueueLongTitleMobileContract(browser);
    await productLockedTitleAdoption(browser);
    await productPreservedTitleApprovalReload(browser);
    await productLockedStaleTitleRefresh(browser);
    await productMultiTabTitleRefreshConflict(browser);
    await aiAsyncFeedback(browser);
    await sourceOnlyFinalApprovalContract(browser, { width: 1440, height: 900 });
    await sourceOnlyFinalApprovalContract(browser, { width: 390, height: 844 });
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
    await oneClickMiaoshouMvpAlwaysRetryContract(
      browser,
      { width: 1440, height: 900 },
    );
    await oneClickMiaoshouMvpAlwaysRetryContract(
      browser,
      { width: 390, height: 844 },
    );
    await oneClickManualReconciliationStatusContract(
      browser,
      { width: 1440, height: 900 },
    );
    await oneClickManualReconciliationStatusContract(
      browser,
      { width: 390, height: 844 },
    );
    await collectboxStepOnePrimaryActionContract(
      browser,
      { width: 1440, height: 900 },
    );
    await collectboxStepOnePrimaryActionContract(
      browser,
      { width: 390, height: 844 },
    );
    await oneClickOfferSwitchCancelsStalePreviewContract(browser);
    await releaseV2TerminalHistoryIsolationContract(
      browser,
      { width: 1440, height: 900 },
    );
    await releaseV2TerminalHistoryIsolationContract(
      browser,
      { width: 390, height: 844 },
    );
    await shopeeCategoryDecisionContract(browser, { width: 1440, height: 900 });
    await shopeeCategoryDecisionContract(browser, { width: 390, height: 844 });
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
