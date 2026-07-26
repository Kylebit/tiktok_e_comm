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
    fact_evidence: { ready: true, blockers: [] },
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
  approval: { ready: false, blockers: ["Kyle approval required"] },
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
      policy_version: "listing-title-candidates-v1",
      model: "offline-model",
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
