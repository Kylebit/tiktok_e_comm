"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const labels = {
    READY:"身份匹配", STOPPED:"服务未启动", PORT_IN_USE:"端口被占用", WRONG_SERVICE:"服务不符", SOURCE_MISMATCH:"代码不符", CONFIG_MISMATCH:"配置不符", DATA_PROFILE_MISMATCH:"数据位置不符", ASSET_MISSING:"缺少页面资源", ASSET_MISMATCH:"页面资源不符", DEPENDENCY_UNAVAILABLE:"缺少依赖", UNKNOWN:"身份未核验"
  };
  const navigationLabels = {
    focus:"常用入口", primary:"业务区域", secondary:"记录与运行"
  };
  const serviceLabels = {
    "product-center":"主工作台", "new-product-workbench":"新品服务", "orbit-rus":"Ozon 服务"
  };
  let catalog = null;
  const params = new URLSearchParams(location.search);
  const view = params.get("view") || "overview";
  if (view === "product") {
    const destination = new URL("/product-workspace", location.origin);
    if (params.has("offer_id")) destination.searchParams.set("offer_id", params.get("offer_id"));
    location.replace(destination.href);
    return;
  }
  const aliases = {
    overview:"workbench", approvals:"channel", tasks:"workbench", audit:"data", system:"workbench"
  };
  const regionKey = aliases[view] || view;
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
  const anchor = (href, label, cls="quiet-link") => `<a class="${cls}" href="${escape(href)}">${escape(label)}</a>`;
  const regionHref = key => key === "product" ? "/product-workspace" : key === "supply-chain" ? "/supply-chain/" : `/?view=${key}`;
  const getJSON = async (url) => {
    const response = await fetch(url, {
      cache:"no-store"
    });
    if (!response.ok) throw new Error("本地信息暂时无法读取");
    return response.json();
  };
  function closeNav() {
    document.body.classList.remove("nav-open");
    $("orbitNav").classList.remove("open");
    $("navToggle").setAttribute("aria-expanded", "false");
    $("navBackdrop").hidden = true;
  }
  $("navToggle").addEventListener("click", () => {
    const open = document.body.classList.toggle("nav-open");
    $("orbitNav").classList.toggle("open", open);
    $("navToggle").setAttribute("aria-expanded", String(open));
    $("navBackdrop").hidden = !open;
  });
  $("navBackdrop").addEventListener("click", closeNav);
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") closeNav();
  });
  function renderNavigation() {
    $("orbitNav").innerHTML = ["focus", "primary", "secondary"].map(level => `<div class="nav-group"><span class="nav-group-label">${navigationLabels[level]}</span>${catalog.navigation.filter(x=>x.level===level).map(x=>`<a class="nav-link ${x.key===view?"active":""}" href="${escape(x.href)}" ${x.key===view?'aria-current="page"':""}>${escape(x.label)}</a>`).join("")}</div>`).join("");
    $("relationshipFlow").innerHTML = catalog.flow.map((x,i)=>`<a href="${escape(x.href)}"><span>${String(i+1).padStart(2,"0")}</span><strong>${escape(x.label)}</strong><small>${escape(x.description)}</small></a>`).join("");
    $("domainCards").innerHTML = catalog.regions.map(region => {
      const links = region.key === "knowledge"
        ? ["TikHub 参考研究", "DuoPlus 云手机", "MiniMax H3 / 商品流程 Skill"]
          .map(title => anchor("/?view=knowledge", title)).join("")
        : catalog.capabilities.filter(item => item.region === region.key).slice(0, 3)
          .map(item => item.entry_available ? anchor(item.href, item.title)
            : `<span>${escape(item.title)} · ${escape(item.status)}</span>`).join("");
      return `<article class="region-card">
        <h3>${anchor(regionHref(region.key), region.label)}</h3>
        <p>${escape(region.description)}</p><div class="region-links">${links}</div>
        ${anchor(regionHref(region.key), region.key === "product" ? "打开商品队列 →" : "查看区域全部能力 →")}
      </article>`;
    }).join("");
  }
  function methodMarkup(method) {
    return method ? `<details class="method"><summary>查看使用方法</summary><code>${escape(method)}</code><button type="button" class="copy-method" data-copy="${escape(method)}">复制方法</button><small>复制不会执行；占位参数需按当前任务填写。</small></details>` : "";
  }
  const related = {
    product:["content","channel"],content:["product","knowledge"],channel:["product","supply-chain"],"supply-chain":["channel","data"],data:["supply-chain","knowledge"],knowledge:["product","content"],workbench:["product","data"]
  };
  function capabilityCard(item) {
    const links = (related[item.region]||[]).map(key=>anchor(regionHref(key),catalog.regions.find(r=>r.key===key).label));
    return `<article class="capability-card" data-capability="${escape(item.id)}"><div class="card-heading"><h2>${escape(item.title)}</h2><span class="status-chip ${item.entry_available?'neutral':'warning'}">${escape(item.status)}</span></div><p>${escape(item.description)}</p>${item.entry_available?anchor(item.href,"打开页面 →","entry-action"):""}${item.kind==='service'?'<span class="service-link" data-service="orbit-rus">核验后显示独立服务链接</span>':""}${methodMarkup(item.method)}<div class="related-links"><span>关联</span>${links.join("")}</div></article>`;
  }
  let knowledgeSelection = null;
  let knowledgeGroup = "";
  const knowledgeGroupOf = item => item.stage === "PORTABLE_REVIEWED_SNAPSHOT_ENTRY" ? "知识快照"
    : item.stage === "PORTABLE_OFFLINE_ENTRY" ? "独立工具"
    : item.stage === "WORKFLOW_RUNTIME_REQUIRED" ? "流程 Skill" : "其他集成";
  const knowledgeEntries = () => [
    ...catalog.tools.map(item => ({...item, directoryKey:`tool:${item.id}`, directoryType:"tool"})),
    ...catalog.capabilities.filter(item => item.region === "knowledge").map(item => ({...item, directoryKey:`capability:${item.id}`, directoryType:"capability"}))
  ];
  function renderKnowledgeDetail(item, focus = false) {
    const detail = $("knowledgeDetail");
    detail.removeAttribute("data-tool");
    detail.removeAttribute("data-capability");
    if (!item) {
      detail.innerHTML = '<p class="empty-copy">选择一个条目查看完整用途和方法。</p>';
      return;
    }
    detail.dataset[item.directoryType] = item.id;
    const missing = item.missing_files || [];
    const changed = item.changed_files || [];
    detail.innerHTML = `<button type="button" class="knowledge-back secondary-button">返回工具列表</button>
      <div class="knowledge-detail-heading"><span>${escape(knowledgeGroupOf(item))}</span><h2 id="knowledgeDetailTitle" tabindex="-1">${escape(item.title)}</h2><p class="knowledge-condition">${escape(item.status || "状态未提供")}</p></div>
      <p class="knowledge-description">${escape(item.description)}</p>
      ${item.entry_available ? anchor(item.href,"打开页面 →","entry-action") : ""}
      ${missing.length || changed.length ? `<div class="knowledge-blocker"><strong>先核对使用条件</strong><p>${missing.length ? "补齐下面列出的依赖后再使用。" : "来源文件发生变化，请先核对本次工具包。"}可保留当前方法用于检查，不代表可执行业务。</p><ul>${missing.map(x=>`<li>缺少：${escape(x)}</li>`).join("")}${changed.map(x=>`<li>变化：${escape(x)}</li>`).join("")}</ul></div>` : ""}
      ${item.method ? `<section class="knowledge-method" aria-label="使用方法"><h3>使用方法</h3><p>填写本次任务参数后使用；复制不会执行。</p><textarea id="knowledgeMethod" readonly spellcheck="false" aria-label="可手动复制的完整使用方法">${escape(item.method)}</textarea><button type="button" class="copy-method" data-copy="${escape(item.method)}">复制方法</button><p id="knowledgeCopyState" role="status" aria-live="polite"></p></section>` : '<p class="knowledge-blocker">暂无已核验方法。请先核对工程版本与依赖，再重新载入目录。</p>'}
      <details class="knowledge-source"><summary>来源与技术信息</summary><pre>${escape(JSON.stringify({id:item.id,stage:item.stage,source:item.source,asset:item.asset,configuration:item.configuration,account_verified:item.account_verified,tool_catalog:catalog.tool_catalog},null,2))}</pre></details>
      <div class="related-links">${(related.knowledge||[]).map(key=>anchor(regionHref(key),catalog.regions.find(r=>r.key===key)?.label||key)).join("")}</div>`;
    if (focus) $("knowledgeDetailTitle").focus();
  }
  function renderKnowledge() {
    const entries = knowledgeEntries();
    const term = $("entrySearch").value.trim().toLocaleLowerCase();
    const status = $("knowledgeStatus").value;
    const groups = [...new Set(entries.map(knowledgeGroupOf))];
    $("knowledgeGroups").innerHTML = ["",...groups].map(group=>`<button type="button" data-knowledge-group="${escape(group)}" aria-pressed="${knowledgeGroup===group}">${escape(group||"全部")} <span>${group?entries.filter(x=>knowledgeGroupOf(x)===group).length:entries.length}</span></button>`).join("");
    const rows = entries.filter(item => (!knowledgeGroup || knowledgeGroupOf(item)===knowledgeGroup) && (!status || item.status===status) && `${item.title} ${item.description} ${item.method||""} ${item.status||""} ${knowledgeGroupOf(item)}`.toLocaleLowerCase().includes(term));
    if (!rows.some(x=>x.directoryKey===knowledgeSelection)) knowledgeSelection=rows[0]?.directoryKey||null;
    $("knowledgeCount").textContent=`${rows.length} / ${entries.length} 项`;
    $("knowledgeNotice").textContent=catalog.tool_catalog?.state==='VERIFIED_LOCAL_CODE_ONLY' ? "选择条目查看方法；账号、配置与业务结果以各项条件为准。" : "工具目录来源尚未通过校验，请先核对工程版本与文件；当前不能确认工具可用。";
    $("knowledgeNotice").classList.toggle("warning",catalog.tool_catalog?.state!=='VERIFIED_LOCAL_CODE_ONLY');
    $("knowledgeList").innerHTML = rows.length ? rows.map(item=>`<button type="button" class="knowledge-row" data-knowledge-key="${escape(item.directoryKey)}" aria-pressed="${knowledgeSelection===item.directoryKey}" aria-controls="knowledgeDetail"><span class="knowledge-row-copy"><strong>${escape(item.title)}</strong><small>${escape(item.description)}</small></span><span class="knowledge-row-status">${escape(item.status||"状态未提供")}</span><span class="knowledge-row-action">${item.method?"查看方法":"查看条件"} ›</span></button>`).join("") : '<div class="knowledge-empty"><h3>没有匹配的条目</h3><p>试试其他名称或用途，或清除搜索与筛选。</p><button type="button" class="secondary-button" id="knowledgeReset">清除搜索与筛选</button></div>';
    renderKnowledgeDetail(rows.find(x=>x.directoryKey===knowledgeSelection));
    $("knowledgeDirectory").classList.remove("detail-open");
    $("workspaceView").classList.remove("knowledge-detail-active");
  }
  $("knowledgeStatus").addEventListener("change",renderKnowledge);
  $("knowledgeDirectory").addEventListener("click",event=>{
    const group=event.target.closest("[data-knowledge-group]");
    if (group) {knowledgeGroup=group.dataset.knowledgeGroup;renderKnowledge();[...$("knowledgeGroups").children].find(x=>x.dataset.knowledgeGroup===knowledgeGroup)?.focus();return;}
    const row=event.target.closest("[data-knowledge-key]");
    if (row) {
      knowledgeSelection=row.dataset.knowledgeKey;
      $("knowledgeList").querySelectorAll("[data-knowledge-key]").forEach(x=>x.setAttribute("aria-pressed",String(x===row)));
      $("knowledgeDirectory").classList.add("detail-open");
      $("workspaceView").classList.add("knowledge-detail-active");
      renderKnowledgeDetail(knowledgeEntries().find(x=>x.directoryKey===knowledgeSelection),true);
    }
    if (event.target.closest(".knowledge-back")) {$("knowledgeDirectory").classList.remove("detail-open");$("workspaceView").classList.remove("knowledge-detail-active");[...$("knowledgeList").children].find(x=>x.dataset.knowledgeKey===knowledgeSelection)?.focus();}
    if (event.target.closest("#knowledgeReset")) {knowledgeGroup="";$("entrySearch").value="";$("knowledgeStatus").value="";renderKnowledge();$("entrySearch").focus();}
  });
  function renderWorkspace() {
    $("workspaceView").classList.toggle("knowledge-workspace",regionKey==='knowledge');
    const region = catalog.regions.find(x=>x.key===regionKey);
    if (!region) {
      $("workspaceTitle").textContent="未找到这个区域";
      $("workspaceDescription").textContent="请从左侧选择已有入口。";
      return;
    }
    const special = {
      approvals:["审批与发布","从商品发布中心查看当前冻结范围与授权状态；首页未读取审批队列。"],tasks:["本地任务","任务看板使用本地任务库。"],audit:["审计与报告","从利润、结算或商品发布中心查看对应报告，缺报告不表示已完成。"],system:["系统与服务","展开运行信息核对身份；桌面容器迁移将单独交付。"]
    };
    $("headerTitle").textContent=special[view]?.[0]||region.label;
    $("workspaceTitle").textContent=special[view]?.[0]||region.label;
    $("workspaceDescription").textContent=special[view]?.[1]||region.description;
    const term=$("entrySearch").value.trim().toLowerCase();
    const matches=x=>`${x.title} ${x.description} ${x.method||""}`.toLowerCase().includes(term);
    const rows=catalog.capabilities.filter(x=>x.region===regionKey && matches(x));
    $("workspaceLinks").classList.toggle("hidden",regionKey==='knowledge');
    $("knowledgeDirectory").classList.toggle("hidden",regionKey!=='knowledge');
    $("workspaceLinks").innerHTML=regionKey==='knowledge' ? '' : rows.map(capabilityCard).join("")||'<p class="empty-copy">没有匹配的能力，请换一个关键词。</p>';
    if(regionKey==='knowledge') renderKnowledge();
    if(regionKey==='data') $("workspaceLinks").insertAdjacentHTML("beforeend",'<article class="capability-card"><h2>结算账单</h2><p>保留 Shopee 周报与账单入口。</p><a class="entry-action" href="/billing">打开账单 →</a></article>');
    if (view === "approvals") {
    }
    $("internalToolsSection").classList.toggle("hidden",view!=="system");
    $("internalToolLinks").innerHTML=catalog.internal_tools.map(x=>`<article class="capability-card"><h3>${escape(x.label)}</h3><p>${escape(x.description)}</p>${anchor(x.href,"打开内部工具 →")}</article>`).join("");
    if(view==='system') $("runtimeDetails").open=true;
  }
  document.addEventListener("click", async event => {
    const button=event.target.closest(".copy-method");
    if(!button) return;
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent="已复制";
      $("pageAlert").textContent="使用方法已复制，尚未执行。";
      if(button.closest('#knowledgeDetail')) $("knowledgeCopyState").textContent="已复制，尚未执行。";
    } catch {
      button.textContent="请选中上方方法复制";
      $("pageAlert").textContent="无法访问剪贴板，请选中方法手动复制。";
      if(button.closest('#knowledgeDetail')) {$("knowledgeCopyState").textContent="无法访问剪贴板。方法已选中，请按 Ctrl+C 或长按复制。";$("knowledgeMethod").focus();$("knowledgeMethod").select();}
    }
  });
  $("entrySearch").addEventListener("input",renderWorkspace);
  let recordsKind = view;
  let recordsGeneration = 0;
  async function loadRecords(kind) {
    if (typeof kind === "string") recordsKind = kind;
    if (recordsKind !== 'audit') return;
    const generation = ++recordsGeneration;
    const requestedKind = recordsKind;
    const button = $("recordsReload");
    $("recordsSection").classList.remove("hidden");
    $("recordsTitle").textContent = "报告与审计记录";
    $("recordsState").textContent = "正在读取已有本地记录…";
    $("recordList").replaceChildren();
    button.disabled = true;
    try {
      const payload = await getJSON("/api/orbit/report-runs?limit=50");
      if (generation !== recordsGeneration) return;
      if (payload.ok !== true || !Array.isArray(payload.items)) throw new Error("记录格式不完整");
      $("recordsState").textContent = payload.items.length ? "已读取本地记录，状态以各条原始记录为准。" : "尚无本地记录；不代表任务或报告已完成。";
      $("recordList").innerHTML = payload.items.map(row => {
        const period = row.period || row.payload?.period || "期间未记录";
        const quality = row.quality || row.payload?.quality || row.payload?.quality_summary || "质量未记录";
        const show = value => typeof value === "object" ? JSON.stringify(value) : String(value);
        return `<article class="capability-card"><h3>${escape(row.title || row.run_id || "未命名记录")}</h3><p>状态：${escape(row.status || "未记录")}</p><p>期间：${escape(show(period))}</p><p>质量：${escape(show(quality))}</p><small>${escape(row.created_at || "时间未记录")}</small></article>`;
      }).join("");
    } catch {
      if (generation === recordsGeneration) $("recordsState").textContent = "记录读取失败。原记录未改动，请重试或检查数据位置。";
    } finally {
      if (generation === recordsGeneration) button.disabled = false;
    }
  }
  $("recordsReload").addEventListener("click", loadRecords);
  $("loadRecentReports").addEventListener("click", () => loadRecords("audit"));
  async function loadHealth() {
    try {
      const health=await getJSON("/api/health");
      const state=health.contract_version==='orbit-runtime/v1' ? (labels[health.state]||labels.UNKNOWN) : labels.UNKNOWN;
      $("serviceState").textContent=state;
      $("sidebarServiceState").textContent=state;
      $("runtimeSummary").textContent=state;
      $("runtimeIdentity").textContent=JSON.stringify(health,null,2);
      $("runtimeNotice").textContent=health.state==='READY'?"当前页面身份与本候选一致。业务执行和真实结果未在首页验证。":"页面可以浏览；配置、数据或依赖身份尚未完整匹配，查看技术身份定位缺项。";
    } catch {
      $("serviceState").textContent="运行信息不可用";
      $("sidebarServiceState").textContent="运行信息不可用";
      $("runtimeSummary").textContent="状态未知";
      $("runtimeIdentity").textContent="无法读取运行身份。";
    }
  }
  $("runtimeCheck").addEventListener("click",async()=>{
    document.querySelectorAll('[data-service="orbit-rus"]').forEach(node => {
      node.textContent = "核验后显示独立服务链接";
    });
    const button=$("runtimeCheck");
    button.disabled=true;
    button.textContent="正在核验…";
    try {
      const result=await getJSON("/api/orbit/runtime");
      $("runtimeServices").innerHTML=Object.entries(result.services||{
      }).map(([name,row])=>`<article class="runtime-service"><strong>${escape(serviceLabels[name]||name)}</strong><span>${escape(labels[row.state]||labels.UNKNOWN)}</span><details><summary>核验依据</summary><pre>${escape(JSON.stringify(row,null,2))}</pre></details></article>`).join("");
      const rus=result.services?.["orbit-rus"];
      if(rus?.state==='READY' && /^http:\/\/127\.0\.0\.1:\d+\/health$/.test(rus.health_url)) document.querySelectorAll('[data-service="orbit-rus"]').forEach(node=>{
        node.innerHTML=anchor(new URL(rus.health_url).origin + "/","打开已核验 Ozon 服务 →");
      });
      $("runtimeNotice").textContent=result.ok?"三个本地服务身份均匹配。业务操作仍按各自范围执行。":"部分服务尚未匹配；已保留原进程，未启动或替换任何服务。";
    } catch {
      $("runtimeNotice").textContent="运行检查失败，请查看本地 launcher 的只读 --status。";
    } finally {
      button.disabled=false;
      button.textContent="重新检查三个本地服务";
    }
  });
  async function init() {
    loadHealth();
    if(regionKey==='knowledge') {
      $("overviewView").classList.add("hidden");
      $("headerTitle").textContent="知识工具";
      $("main").insertBefore($("entryError"),$("workspaceView"));
      $("entryError").hidden=false;
      $("entryError").setAttribute("role","status");
      $("entryError").textContent="正在读取知识与工具目录…";
    }
    try {
      catalog=await getJSON("/api/orbit/navigation");
      if(!['capabilities','regions','tools','navigation','flow','internal_tools'].every(key=>Array.isArray(catalog[key]))) throw new Error();
      if(regionKey==='knowledge') {
        const statuses=[...new Set(knowledgeEntries().map(x=>x.status).filter(Boolean))];
        $("knowledgeStatus").innerHTML='<option value="">全部状态</option>'+statuses.map(status=>`<option value="${escape(status)}">${escape(status)}</option>`).join('');
      }
      renderNavigation();
      $("entryError").hidden=true;
      if (view === "overview") $("overviewView").append($("recordsSection"));
      if(view!=="overview"){
        $("overviewView").classList.add("hidden");
        $("workspaceView").classList.remove("hidden");
        renderWorkspace();
        loadRecords();
      }
    } catch {
      $("entryError").hidden=false;
      $("entryError").setAttribute("role","alert");
      $("entryError").textContent="能力目录暂时不可用。可重载本页，或打开商品发布中心、利润中心；运行状态仍需核验。";
      $("orbitNav").innerHTML=anchor("/","工作台")+anchor("/product-workspace","商品发布中心")+anchor("/profit","利润中心");
    }
  }
  init();
})();
