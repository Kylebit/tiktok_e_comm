(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  let registry = { navigation: [], workspaces: [], internal_tools: [] };
  let statusPayload = null;
  let inboxPayload = { ok: false, items: [] };

  function currentView() {
    return new URLSearchParams(window.location.search).get("view") || "overview";
  }

  function navLink(item, extraClass = "") {
    const view = currentView();
    const active = item.key === view || (item.key === "overview" && view === "overview");
    return `<a class="nav-link${extraClass ? ` ${extraClass}` : ""}${active ? " active" : ""}"
      href="${esc(item.href)}"${active ? ' aria-current="page"' : ""}>${esc(item.label)}</a>`;
  }

  function renderNavigation() {
    const focus = registry.navigation.filter((item) => item.level === "focus");
    const primary = registry.navigation.filter((item) => item.level === "primary");
    const secondary = registry.navigation.filter((item) => item.level === "secondary");
    const html = [
      '<span class="nav-section-label">快捷工作</span>',
      ...focus.map((item) => navLink(item, "focus")),
      '<span class="nav-section-label">运营领域</span>',
      ...primary.map((item) => navLink(item)),
      '<span class="nav-section-label">管理与系统</span>',
      ...secondary.map((item) => navLink(item)),
    ].join("");
    $("#orbitNav").innerHTML = html;
  }

  function pendingCount(keys) {
    if (!statusPayload || !statusPayload.ok) return null;
    return keys.reduce((total, key) => {
      const value = statusPayload[key];
      return total + (typeof value === "number" ? value : 0);
    }, 0);
  }

  function domainState(key) {
    if (key === "supply-chain") {
      return { label: "待建设", tone: "planned", detail: "供应链工作台尚未接入统一状态。" };
    }
    if (!statusPayload || !statusPayload.ok) {
      return { label: "未知", tone: "unknown", detail: "本地状态接口当前不可用。" };
    }
    const mappings = {
      product: [],
      content: ["pending_titles", "pending_images"],
      channel: ["pending_mx", "pending_uk", "pending_promos", "pending_deactivate"],
      data: [],
    };
    const keys = mappings[key] || [];
    if (!keys.length) {
      return { label: "未知", tone: "unknown", detail: "该域尚无统一状态信号。" };
    }
    const count = pendingCount(keys);
    return count > 0
      ? { label: `待处理 ${count}`, tone: "attention", detail: "来自已接入的本地队列。" }
      : { label: "无已知待办", tone: "ok", detail: "仅代表已接入的本地队列。" };
  }

  function renderDomains() {
    const html = registry.workspaces.map((workspace) => {
      const state = domainState(workspace.key);
      return `<a class="domain-card" href="/?view=${esc(workspace.key)}">
        <div><strong>${esc(workspace.label)}</strong>
          <span class="status-chip ${esc(state.tone)}">${esc(state.label)}</span></div>
        <p>${esc(workspace.description)}</p>
        <small>${esc(state.detail)}</small>
      </a>`;
    }).join("");
    $("#domainCards").innerHTML = html || '<p class="empty-copy">业务域注册表不可用</p>';
  }

  function latestProfitItem() {
    const items = Array.isArray(inboxPayload.items) ? inboxPayload.items : [];
    return items.find((item) => {
      const category = String(item.category || "").toLowerCase();
      const title = String(item.title || "");
      return category.includes("profit") || title.includes("利润");
    }) || null;
  }

  function renderProfitPulse() {
    const item = latestProfitItem();
    if (!inboxPayload.ok) {
      $("#profitPulse").textContent = "状态未知";
      $("#profitPulse").className = "focus-tag neutral";
      $("#profitReportState").textContent = "本地收件箱不可用";
      $("#profitReviewCount").textContent = "未知";
      $("#profitPeriod").textContent = "—";
      return;
    }
    if (!item) {
      $("#profitPulse").textContent = "尚无周报";
      $("#profitPulse").className = "focus-tag neutral";
      $("#profitReportState").textContent = "等待首份报告";
      $("#profitReviewCount").textContent = "—";
      $("#profitPeriod").textContent = "—";
      return;
    }
    const payload = item.payload || {};
    const period = payload.period || {};
    const status = payload.status || item.status || "unknown";
    const reviewCount = payload.quality_issue_count;
    $("#profitPulse").textContent = status === "ready" ? "周报可复核" : "周报需复核";
    $("#profitPulse").className = `focus-tag ${status === "ready" ? "" : "warning"}`.trim();
    $("#profitReportState").textContent = status === "ready" ? "已生成" : "需要补齐数据";
    $("#profitReviewCount").textContent = typeof reviewCount === "number" ? String(reviewCount) : "未知";
    const start = String(period.start || "").slice(0, 10);
    const end = String(period.end || "").slice(0, 10);
    $("#profitPeriod").textContent = [start, end].filter(Boolean).join(" — ") || "—";
  }

  function approvalRows() {
    if (!statusPayload || !statusPayload.ok) return [];
    return [
      ["内容标题", statusPayload.pending_titles, "/titles"],
      ["内容图片", statusPayload.pending_images, "/images"],
      ["MX 发布", statusPayload.pending_mx, "/mx"],
      ["UK 发布", statusPayload.pending_uk, "/uk"],
    ].filter((row) => typeof row[1] === "number");
  }

  function renderOverviewStatus() {
    const service = $("#serviceState");
    const sideService = $("#sidebarServiceState");
    const liveDot = $(".live-dot");
    const approvals = pendingCount(["pending_titles", "pending_images", "pending_mx", "pending_uk"]);
    const warnings = statusPayload && statusPayload.ok && Array.isArray(statusPayload.warnings)
      ? statusPayload.warnings : [];

    if (!statusPayload || !statusPayload.ok) {
      service.textContent = "状态不可用";
      service.className = "status-chip unknown";
      sideService.textContent = "本地状态不可用";
      liveDot.classList.add("offline");
      $("#approvalMetric").textContent = "未知";
      $("#approvalDetail").textContent = "状态接口未提供可用数据";
      $("#exceptionMetric").textContent = "未知";
      $("#exceptionDetail").textContent = "不把未接入状态显示为零";
      $("#approvalList").innerHTML =
        '<div class="unknown-block"><strong>审批汇总不可用</strong><p>请从对应业务域进入现有审批入口。</p></div>';
    } else {
      service.textContent = "本地服务在线";
      service.className = "status-chip ok";
      sideService.textContent = "本地服务在线";
      liveDot.classList.remove("offline");
      $("#approvalMetric").textContent = String(approvals);
      $("#approvalDetail").textContent = "标题、图片、MX 与 UK 已接入";
      $("#exceptionMetric").textContent = String(warnings.length);
      $("#exceptionDetail").textContent = "现有接口报告的本地警告";
      const rows = approvalRows();
      $("#approvalList").innerHTML = rows.map((row) =>
        `<a class="list-row" href="${esc(row[2])}"><span>${esc(row[0])}</span><strong>${esc(row[1])}</strong></a>`
      ).join("") || '<p class="empty-copy">没有已接入的审批队列</p>';
    }

    if (inboxPayload.ok) {
      const items = Array.isArray(inboxPayload.items) ? inboxPayload.items : [];
      const unread = items.filter((item) => item.status === "unread");
      $("#runMetric").textContent = String(unread.length);
      $("#runDetail").textContent = items.length ? "本地收件箱未读" : "尚无本地运行记录";
      $("#recentRunList").innerHTML = items.slice(0, 4).map((item) =>
        `<a class="list-row" href="/?view=tasks"><span>${esc(item.title)}</span>
          <strong>${esc(item.severity === "warning" ? "需复核" : "已生成")}</strong></a>`
      ).join("") || '<p class="empty-copy">尚无本地运行记录</p>';
    } else {
      $("#runMetric").textContent = "未知";
      $("#runDetail").textContent = "本地收件箱接口不可用";
      $("#recentRunList").innerHTML =
        '<div class="unknown-block"><strong>运行记录不可用</strong><p>Orbit 不推测任务是否成功。</p></div>';
    }
    renderProfitPulse();
    renderDomains();
  }

  function workspaceCard(link, internal = false) {
    return `<a class="workspace-card${internal ? " internal" : ""}" href="${esc(link.href)}">
      <strong>${esc(link.label)}</strong>
      <p>${esc(link.description)}</p>
      <span>${internal ? "打开内部工具" : "进入业务入口"} →</span>
    </a>`;
  }

  function renderPlatformCollection(view) {
    const target = $("#workspaceLinks");
    const endpoint = view === "tasks"
      ? "/api/orbit/inbox?limit=50"
      : "/api/orbit/report-runs?limit=50";
    target.innerHTML = '<p class="empty-copy">正在读取本地记录…</p>';
    fetch(endpoint, { headers: { Accept: "application/json" } })
      .then((response) => {
        if (!response.ok) throw new Error("collection unavailable");
        return response.json();
      })
      .then((payload) => {
        const items = Array.isArray(payload.items) ? payload.items : [];
        if (!items.length) {
          target.innerHTML = '<div class="unknown-block wide"><strong>暂无记录</strong>' +
            '<p>本地记录库尚未收到报告；不会把“没有记录”显示成任务成功。</p></div>';
          return;
        }
        target.innerHTML = items.map((item) => {
          const report = view === "tasks" ? item.payload || {} : item;
          const period = report.period || {};
          const status = view === "tasks" ? (report.status || item.status) : item.status;
          const title = view === "tasks" ? item.title : `${item.calculation_kind} · ${item.run_id}`;
          const detail = [
            String(period.start || "").slice(0, 10),
            String(period.end || "").slice(0, 10),
            status || "unknown",
          ].filter(Boolean).join(" · ");
          return `<article class="workspace-card"><strong>${esc(title)}</strong>
            <p>${esc(detail)}</p><span>${esc(
              view === "tasks" && item.severity === "warning" ? "需要复核" : "本地审计记录"
            )}</span></article>`;
        }).join("");
      })
      .catch(() => {
        target.innerHTML = '<div class="unknown-block wide"><strong>记录暂不可用</strong>' +
          '<p>本地运行记录接口当前不可用。</p></div>';
      });
  }

  function renderInternalTools(show) {
    const section = $("#internalToolsSection");
    section.classList.toggle("hidden", !show);
    if (!show) {
      $("#internalToolLinks").innerHTML = "";
      return;
    }
    const tools = Array.isArray(registry.internal_tools) ? registry.internal_tools : [];
    $("#internalToolLinks").innerHTML = tools.map((tool) => workspaceCard(tool, true)).join("")
      || '<div class="unknown-block wide"><strong>暂无内部工具</strong></div>';
  }

  function renderWorkspace() {
    const view = currentView();
    const navItem = registry.navigation.find((item) => item.key === view);
    const workspace = registry.workspaces.find((item) => item.key === view);
    const overview = view === "overview";
    $("#overviewView").classList.toggle("hidden", !overview);
    $("#workspaceView").classList.toggle("hidden", overview);
    $("#headerTitle").textContent = navItem ? navItem.label : "今天的运营";
    renderInternalTools(false);
    if (overview) return;

    const title = workspace ? workspace.label : (navItem ? navItem.label : "未知入口");
    const description = workspace ? workspace.description : (navItem ? navItem.description : "入口尚未注册。");
    $("#workspaceTitle").textContent = title;
    $("#workspaceDescription").textContent = description;
    $("#workspaceEyebrow").textContent = workspace ? "BUSINESS DOMAIN" : "SHARED PLATFORM";

    let links = workspace ? workspace.links.slice() : [];
    if (view === "tasks" || view === "audit") {
      renderPlatformCollection(view);
      return;
    }
    if (view === "approvals") {
      links = [
        { label: "标题审批", href: "/titles", description: "审核内容标题候选" },
        { label: "图片审批", href: "/images", description: "审核内容图片候选" },
        { label: "MX 发布审批", href: "/mx", description: "复核墨西哥渠道发布" },
        { label: "UK 发布审批", href: "/uk", description: "复核英国渠道发布" },
      ];
    } else if (view === "system") {
      links = [
        { label: "Shared Platform 健康", href: "/api/health", description: "8765 · 本地健康接口" },
        { label: "Orbit Treasury", href: "http://127.0.0.1:8766/", description: "8766 · 自动上品技术服务" },
        { label: "Orbit Rus", href: "http://127.0.0.1:8767/", description: "8767 · 俄罗斯与 Ozon 技术服务" },
      ];
      renderInternalTools(true);
    }

    $("#workspaceLinks").innerHTML = links.map((link) => workspaceCard(link)).join("") || (
      workspace && workspace.availability === "planned"
        ? '<div class="unknown-block wide"><strong>该业务域待建设</strong><p>不会伪造库存、仓库或补货状态。</p></div>'
        : '<div class="unknown-block wide"><strong>能力尚未接入</strong><p>当前状态为 unavailable。</p></div>'
    );
  }

  function setDateLabel() {
    const text = new Intl.DateTimeFormat("zh-CN", {
      month: "long",
      day: "numeric",
      weekday: "long",
    }).format(new Date());
    $("#todayLabel").textContent = text;
  }

  function setNavigationOpen(open) {
    const toggle = $("#navToggle");
    const nav = $("#orbitNav");
    nav.classList.toggle("open", open);
    $("#navBackdrop").hidden = !open;
    document.body.classList.toggle("nav-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    toggle.querySelector(".sr-only").textContent = open ? "关闭导航" : "打开导航";
  }

  $("#navToggle").addEventListener("click", () => {
    setNavigationOpen(!$("#orbitNav").classList.contains("open"));
  });
  $("#navBackdrop").addEventListener("click", () => setNavigationOpen(false));
  $("#orbitNav").addEventListener("click", (event) => {
    if (event.target.closest("a")) setNavigationOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("#orbitNav").classList.contains("open")) {
      setNavigationOpen(false);
      $("#navToggle").focus();
    }
  });
  window.addEventListener("resize", () => {
    if (window.innerWidth > 820) setNavigationOpen(false);
  });

  setDateLabel();
  Promise.all([
    fetch("/api/orbit/navigation", { headers: { Accept: "application/json" } })
      .then((response) => {
        if (!response.ok) throw new Error("navigation unavailable");
        return response.json();
      }),
    fetch("/api/status", { headers: { Accept: "application/json" } })
      .then((response) => {
        if (!response.ok) throw new Error("status unavailable");
        return response.json();
      })
      .catch((error) => ({ ok: false, error: String(error) })),
    fetch("/api/orbit/inbox?limit=20", { headers: { Accept: "application/json" } })
      .then((response) => {
        if (!response.ok) throw new Error("inbox unavailable");
        return response.json();
      })
      .catch((error) => ({ ok: false, error: String(error), items: [] })),
  ]).then((results) => {
    registry = {
      navigation: Array.isArray(results[0].navigation) ? results[0].navigation : [],
      workspaces: Array.isArray(results[0].workspaces) ? results[0].workspaces : [],
      internal_tools: Array.isArray(results[0].internal_tools) ? results[0].internal_tools : [],
    };
    statusPayload = results[1];
    inboxPayload = results[2];
    $("#updatedAt").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    })}`;
    renderNavigation();
    renderWorkspace();
    renderOverviewStatus();
  }).catch(() => {
    registry = {
      navigation: [
        { key: "new-product", label: "自动上品", href: "/new-product", level: "focus" },
        { key: "profit", label: "利润中心", href: "/profit", level: "focus" },
        { key: "overview", label: "总览", href: "/", level: "primary" },
        { key: "system", label: "系统与服务", href: "/?view=system", level: "secondary" },
      ],
      workspaces: [],
      internal_tools: [],
    };
    statusPayload = { ok: false };
    inboxPayload = { ok: false, items: [] };
    renderNavigation();
    renderWorkspace();
    renderOverviewStatus();
    $("#pageAlert").textContent = "业务注册表不可用，已显示安全的基础入口。";
  });
})();
