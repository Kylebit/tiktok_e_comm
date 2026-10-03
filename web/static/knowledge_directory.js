"use strict";(()=>{const $=id=>document.getElementById(id);let catalog=null;let guides=null;let identities=null;
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const anchor=(href,label,cls='quiet-link')=>`<a class="${cls}" href="${escape(href)}">${escape(label)}</a>`;
function renderIteration(data) {
  const box=$('knowledgeIteration');
  if(!box)return;
  if(!data||data.schema!=='orbit-knowledge-iteration/v1'||data.execution_authority!==false){
    box.innerHTML='<h2>知识迭代</h2><p role="status">知识索引尚未接入。工具分类和使用说明仍可使用。</p>';return;
  }
  const quote=x=>"'"+String(x).replace(/'/g,"''")+"'";
  const command=(id,title,text)=>`<details><summary>${escape(title)}</summary><textarea id="${id}" readonly aria-label="${escape(title)}">${escape(text)}</textarea><button type="button" class="copy-method" data-copy-target="${id}" data-copy-state="${id}State" data-copy="${escape(text)}">复制给 Agent</button><p id="${id}State" role="status"></p></details>`;
  const known=data.available===true&&Number.isInteger(data.notes)&&data.by_kind&&data.by_freshness&&data.source_check;
  const kinds={business_fact_or_analysis:'经营事实与分析',historical_reference:'历史资料',operating_rule:'运行规则',incident_or_learning:'事故与经验',decision_record:'决策记录',supporting_note:'辅助笔记'};
  const candidate=data.candidate;
  const candidateOK=candidate&&candidate.reviewed===false&&Array.isArray(candidate.sources);
  const historical=known&&Date.parse(data.audit_date+'T00:00:00+08:00')<Date.now()-86400000;
  const affected=known?((data.source_check.changed||0)+(data.source_check.missing||0)):0;
  const prefix='在已核验的 OrbitHive 工程根，使用该工程 Python 执行以下命令。保持原 Vault 不变；复制本段不会执行。\n';
  const commands=known?command('knowledgeSearchCommand','检索已核验来源',prefix+`python -X utf8 tools/knowledge_iteration/readonly_index_v2.py search --index ${quote(data.index_path)} --query '【替换为要查找的主题】' --limit 6`)+command('knowledgeRefreshCommand','刷新索引摘要',prefix+`python -X utf8 tools/knowledge_iteration/readonly_index_v2.py index --vault ${quote(data.vault_path)} --output '【本次独立输出目录】/vault-index.json'\npython -X utf8 tools/knowledge_iteration/build_projection.py --index '【本次独立输出目录】/vault-index.json' --candidate ${quote(data.candidate_manifest_path)} --guides web/static/knowledge_guides.json\n输出须在 Vault 外；先审阅变化，再由集成任务更新页面。`):'';
  box.innerHTML=`<h2>知识迭代</h2><p>把经营记录与完成案例整理成可复用经验。候选需审阅后才能进入正式知识快照。</p>
    ${known?`<p class="knowledge-iteration-date" role="status">索引审计日：${escape(data.audit_date)} · 摘要更新：${escape(data.projected_at)}${historical?' · 历史截点，当前动作前请刷新':''}</p>
    <div class="knowledge-iteration-stats"><span><strong>${data.notes}</strong>篇笔记</span><span><strong>${Number(data.by_freshness.STALE_FOR_CURRENT_DECISION)||0}</strong>篇经营资料需刷新</span><span><strong>${Number(data.by_freshness.UNKNOWN_OBSERVED_AT)||0}</strong>篇经营资料缺观察日期</span><span><strong>${Number(data.source_check.unchanged)||0}</strong>篇源字节核对一致</span></div>
    <p>日期只提示时效，不证明结论正确；源字节核对发生在摘要更新时间，不是实时监测。${affected?`有 ${affected} 篇来源变化或缺失，检索前须重新核对。`:data.notes===0?'索引为空，尚无可检索笔记。':''}</p>
    <details><summary>查看索引分类与来源</summary><ul>${Object.entries(data.by_kind).map(([kind,count])=>`<li>${escape(kinds[kind]||'其他资料')}：${escape(count)} 篇</li>`).join('')}</ul><p>Vault：<code>${escape(data.vault_path)}</code></p><p>索引：<code>${escape(data.index_path)}</code></p><p>索引生成：${escape(data.indexed_at)}</p><p>索引摘要：<code>${escape(data.index_digest)}</code></p></details>`:`<p role="status">${escape(data.problem||'索引暂不可用，请先生成只读索引。')}</p>`}
    <details class="knowledge-candidate"><summary>经验候选${candidateOK?' · 1 项待审阅':''}</summary>${candidateOK?`<h3>${escape(candidate.title)}</h3><p><strong>尚未批准为运行知识。</strong>仅供回看该次有界验收，不能据此修改当前成本或执行上架。</p><ul>${(candidate.matches&&Array.isArray(candidate.learnings)?candidate.learnings:[]).map(line=>`<li>${escape(line)}</li>`).join('')}</ul><p>观察时间：${escape(candidate.observed_at)}</p><p>验收提交：<code>${escape(candidate.verified_commit)}</code></p><p>候选文件：<code>${escape(candidate.path)}</code></p><p>${candidate.matches&&candidate.sources.every(x=>x.matches)?'摘要生成时，候选与全部来源字节一致。':'候选或来源已变化/缺失，需重新核验后使用。'}</p><ul>${candidate.sources.map(source=>`<li><code>${escape(source.path)}</code> · ${source.matches?'摘要生成时一致':'变化或缺失'}</li>`).join('')}</ul>`:`<p>${escape(data.candidate_problem||'暂无经验候选。')}</p>`}</details>
    <details class="knowledge-maintenance"><summary>交给 Agent 维护 · 只读检索与候选整理</summary>${commands}${command('knowledgeCandidateCommand','整理下一条经验候选','请先读取当前工程 docs/knowledge/README.md、docs/knowledge/USAGE.md 和 tools/knowledge_iteration/README.md。根据【明确完成案例和验收回执】在 Vault 外的独立输出目录整理候选，列出精确来源、摘要、观察时间、适用范围、未知及替代关系。候选保持未评审，不修改真实 Vault、不生成虚假评审清单、不激活运行规则；交付可审阅候选后再按现有知识快照合同处理。')}</details>`;
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
  const guideOf = item => item.method && item.stage !== 'UNVERIFIED' ? guides?.entries[item.id] : null;
  const coreSkillIds=new Set(['prepare-product-publication','prepare-product-images','publish-approved-product','delist-products-by-sku','apply-product-discounts','manage-seaya-replenishment','manage-profit-settlement']);
  const identityOf=item=>identities?.skills?.[item.id]||null;
  const identityLabel=row=>({MATCH:'个人安装版与本服务工程版匹配',RAW_DRIFT:'原始字节有差异；规范化内容一致',CONFLICT:'版本冲突，须核对后使用',NOT_INSTALLED:'个人 Skill 未安装',INVALID:'个人安装目录无法核验',SOURCE_UNVERIFIED:'工程来源清单无法核验',UNKNOWN:'版本扫描超限或正在进行，状态未核验'})[row?.comparison?.status]||'安装版状态未核验';
  function identityMarkup(item){
    if(!coreSkillIds.has(item.id))return '';
    const row=identityOf(item);
    if(!row)return '<section class="knowledge-skill-identity knowledge-skill-identity-hold" aria-label="Skill 版本身份"><h3>Skill 版本身份</h3><p>本次安装版状态未核验；请按工程原文和来源清单检查，不能据此执行业务。</p></section>';
    const project=row.project||{},installed=row.installed||{},comparison=row.comparison||{};
    const hold=!project.registry_verified||!['MATCH','RAW_DRIFT'].includes(comparison.status);
    return `<section class="knowledge-skill-identity${hold?' knowledge-skill-identity-hold':''}" aria-label="Skill 版本身份"><h3>Skill 版本身份 · 只读核验</h3><p>${escape(identityLabel(row))}。版本核查不代表账号、数据或业务执行就绪。</p>
      <dl><div><dt>本服务工程版</dt><dd>${project.registry_verified?'来源清单一致':'来源清单待核'}</dd></div>
      <div><dt>个人安装版</dt><dd>${installed.state==='PRESENT'?escape(installed.source_type)+' · 已发现':escape(identityLabel(row))}</dd></div>
      <div><dt>差异</dt><dd>${escape(comparison.status||'未核验')}${comparison.normalized_changed_count?' · 内容变化 '+comparison.normalized_changed_count+' 个文件':''}${comparison.missing_count?' · 缺少 '+comparison.missing_count+' 个文件':''}${comparison.extra_count?' · 多出 '+comparison.extra_count+' 个文件':''}</dd></div></dl>
      ${item.id==='manage-seaya-replenishment'&&comparison.status==='CONFLICT'?'<p class="knowledge-skill-supply-hold">供应链重复库存身份规则仍按工程严格 BLOCKED_INVENTORY；不自动选择旧个人版，也不合并历史库存快照。</p>':''}
      <p>只读快照：${escape(identities.observed_at)}。完整逐文件摘要仅供本机 CLI 审计；页面不展示个人路径或链接目标。</p></section>`;
  }
  const guideSection = (title, items) => `<section><h3>${title}</h3><ul>${items.map(x=>`<li>${escape(x)}</li>`).join('')}</ul></section>`;
  const workflowMarkup = guide => guide?.workflow?.length
    ? `<section class="knowledge-workflow" aria-label="Skill 执行顺序"><h3>执行顺序</h3><ol>${guide.workflow.map(step=>`<li>${escape(step)}</li>`).join('')}</ol><p>这是流程说明；每一步仍须核对本次来源、权限与 Skill 原文。</p></section>`
    : '';
  function briefMarkup(brief, guide) {
    if (!brief) return '';
    const fields = [
      ['用途', brief.purpose], ['输入', brief.inputs],
      ['产出与证据', brief.outputs], ['操作边界', brief.side_effects],
      ['失败恢复', brief.recovery]
    ].filter(([,value]) => value);
    return `<section class="knowledge-brief" aria-label="能力速览"><h3>能力速览</h3><dl>${fields.map(([label,value])=>`<div><dt>${label}</dt><dd>${escape(value)}</dd></div>`).join('')}
      ${guide?.requirements?.length ? `<div class="knowledge-brief-authority"><dt>审核与授权</dt><dd>${guide.requirements.map(escape).join('<br>')}</dd></div>` : ''}
      ${brief.source_path ? `<div><dt>原文入口</dt><dd><code>${escape(brief.source_path)}</code></dd></div>` : ''}</dl></section>`;
  }
  function guideMarkup(guide) {
    if (!guide) return '';
    return `<details class="knowledge-guide"><summary>详细说明：场景、输入、产出、条件与边界</summary>
      ${guideSection('适用场景',guide.scenarios)}${guideSection('需要提供',guide.inputs)}${guideSection('主要产出',guide.outputs)}${guideSection('依赖与授权',guide.requirements)}${guideSection('使用边界',guide.limits)}</details>
      <details class="knowledge-example" open><summary>中文调用示例 · 可复制</summary><p>把【】中的内容替换后交给 Agent。示例默认先检查，不会因复制而执行。</p>
      <textarea id="knowledgeExample" readonly spellcheck="false" aria-label="可复制的中文调用示例">${escape(guide.example)}</textarea>
      <button type="button" class="copy-method" data-copy-target="knowledgeExample" data-copy-state="knowledgeExampleCopyState" data-copy="${escape(guide.example)}">复制中文示例</button><p id="knowledgeExampleCopyState" role="status" aria-live="polite"></p></details>`;
  }
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
    const guide = guideOf(item);
    const identity=identityOf(item);
    const identityReady=!coreSkillIds.has(item.id)||(identity?.project?.registry_verified===true&&['MATCH','RAW_DRIFT'].includes(identity?.comparison?.status));
    const localCodeReady = catalog.tool_catalog?.state === 'VERIFIED_LOCAL_CODE_ONLY' && !missing.length && !changed.length && item.stage !== 'UNVERIFIED' && identityReady;
    const executionState = identity?.comparison?.status==='CONFLICT' ? 'Skill 来源冲突，执行规则待核；业务执行保持阻断' : !identityReady ? 'Skill 版本身份待核；业务执行保持阻断' : item.stage === 'WORKFLOW_RUNTIME_REQUIRED' ? '需要完整业务工程和本次数据核验' : '须按该条目的条件逐项核验';
    detail.innerHTML = `<button type="button" class="knowledge-back secondary-button">返回工具列表</button>
      <div class="knowledge-detail-heading"><span>${escape(knowledgeGroupOf(item))}</span><h2 id="knowledgeDetailTitle" tabindex="-1">${escape(item.title)}</h2><p class="knowledge-condition">${escape(item.status || "状态未提供")}</p></div>
      <p class="knowledge-description">${escape(guide?.summary || item.description)}</p>
      ${guide?.source_notice ? `<div class="knowledge-source-notice" role="status"><strong>历史核查：${escape(guide.source_notice.status)}</strong><p>核查日：${escape(guide.source_notice.observed_at)} · ${escape(guide.source_notice.message)}；当前版本以本次只读身份核验为准。</p></div>` : ''}
      ${identityMarkup(item)}
      ${item.directoryType === 'tool' ? `<section class="knowledge-readiness" aria-label="可用性与权限"><h3>可用性与权限</h3><dl><div><dt>工程文件</dt><dd>${localCodeReady ? '本工程目录文件已核对' : '工程文件未通过核对'}</dd></div><div><dt>账号与 API</dt><dd>${item.account_verified === true ? '本次已核验' : '本页未验证；不能据此执行外部操作'}</dd></div><div><dt>业务执行</dt><dd>${escape(executionState)}</dd></div></dl></section>` : ''}
      ${guide?.source_notice?.checksums ? `<details class="knowledge-source-checksums"><summary>查看核查日的 Skill 来源摘要</summary><p>各项摘要按各自核对日期展示，不自动选择执行权威；执行前重新核对。${guide.source_notice.project_snapshot_observed_at?`本工程摘要核对日：${escape(guide.source_notice.project_snapshot_observed_at)}；个人安装版未重新核验。`:''}${escape(guide.source_notice.location_note || '')}</p><ul>${guide.source_notice.checksums.map(row=>`<li>${escape(row.label)} · 核对日 ${escape(row.observed_at||guide.source_notice.observed_at)}：<code>sha256:${escape(row.sha256)}</code></li>`).join('')}</ul>${Array.isArray(guide.source_notice.historical_checksums)?`<details class="knowledge-source-history"><summary>${escape(guide.source_notice.observed_at)} 原核查快照</summary><p>保留旧摘要用于追溯；不代表当前工程原文、个人安装版本或库存。</p><ul>${guide.source_notice.historical_checksums.map(row=>`<li>${escape(row.label)} · 核对日 ${escape(row.observed_at||guide.source_notice.observed_at)}：<code>sha256:${escape(row.sha256)}</code></li>`).join('')}</ul></details>`:''}</details>` : ''}
      ${item.entry_available ? anchor(item.href,"打开页面 →","entry-action") : ""}
      ${missing.length || changed.length ? `<div class="knowledge-blocker"><strong>先核对使用条件</strong><p>${missing.length ? "补齐下面列出的依赖后再使用。" : "来源文件发生变化，请先核对本次工具包。"}可保留当前方法用于检查，不代表可执行业务。</p><ul>${missing.map(x=>`<li>缺少：${escape(x)}</li>`).join("")}${changed.map(x=>`<li>变化：${escape(x)}</li>`).join("")}</ul></div>` : ""}
      ${briefMarkup(item.brief, guide)}
      ${workflowMarkup(guide)}
      ${guideMarkup(guide)}
      ${item.method ? `<details class="knowledge-technical"><summary>Agent 技术调用方法</summary><section class="knowledge-method" aria-label="使用方法"><p>R 为已核验工程根，P 为本次项目根；使用支持的 Python，补全本次参数。复制不会执行。</p><textarea id="knowledgeMethod" readonly spellcheck="false" aria-label="可手动复制的完整使用方法">${escape(item.method)}</textarea><button type="button" class="copy-method" data-copy-target="knowledgeMethod" data-copy-state="knowledgeCopyState" data-copy="${escape(item.method)}">复制技术方法</button><p id="knowledgeCopyState" role="status" aria-live="polite"></p></section></details>` : '<p class="knowledge-blocker">暂无已核验方法。请先核对工程版本与依赖，再重新载入目录。</p>'}
      <details class="knowledge-source"><summary>技能原文、可复用脚本/API 与来源</summary>
      ${guide ? `<p>${escape(guides.path_note)}</p><ul>${guide.references.map(x=>`<li class="knowledge-reference"><code>${escape(x)}</code></li>`).join('')}${(guide.host_references||[]).map(x=>`<li class="knowledge-reference">${escape(x)}</li>`).join('')}</ul>` : ''}
      <p>目录条件：${escape(item.description)}</p><p>账号与业务结果尚未由本目录验证。</p>
      <details><summary>完整来源元数据</summary><pre>${escape(JSON.stringify({id:item.id,stage:item.stage,source:item.source,asset:item.asset,configuration:item.configuration,account_verified:item.account_verified,tool_catalog:catalog.tool_catalog,guide_reviewed_base:guide?guides.reviewed_base:undefined},null,2))}</pre></details></details>
      `;
    if (focus) $("knowledgeDetailTitle").focus();
  }
  function renderKnowledge() {
    const entries = knowledgeEntries();
    const term = $("entrySearch").value.trim().toLocaleLowerCase();
    const status = $("knowledgeStatus").value;
    const groups = [...new Set(entries.map(knowledgeGroupOf))];
    $("knowledgeGroups").innerHTML = ["",...groups].map(group=>`<button type="button" data-knowledge-group="${escape(group)}" aria-pressed="${knowledgeGroup===group}">${escape(group||"全部")} <span>${group?entries.filter(x=>knowledgeGroupOf(x)===group).length:entries.length}</span></button>`).join("");
    const rows = entries.filter(item => (!knowledgeGroup || knowledgeGroupOf(item)===knowledgeGroup) && (!status || item.status===status) && `${item.title} ${item.description} ${item.method||""} ${item.status||""} ${knowledgeGroupOf(item)} ${JSON.stringify(guideOf(item)||{})}`.toLocaleLowerCase().includes(term));
    if (!rows.some(x=>x.directoryKey===knowledgeSelection)) knowledgeSelection=rows[0]?.directoryKey||null;
    $("knowledgeCount").textContent=`${rows.length} / ${entries.length} 项`;
    $("knowledgeNotice").textContent=catalog.tool_catalog?.state==='VERIFIED_LOCAL_CODE_ONLY' ? "选择条目查看方法；账号、配置与业务结果以各项条件为准。" : "工具目录来源尚未通过校验，请先核对工程版本与文件；当前不能确认工具可用。";
    $("knowledgeNotice").classList.toggle("warning",catalog.tool_catalog?.state!=='VERIFIED_LOCAL_CODE_ONLY');
    $("knowledgeList").innerHTML = rows.length ? rows.map(item=>`<button type="button" class="knowledge-row" data-knowledge-key="${escape(item.directoryKey)}" aria-pressed="${knowledgeSelection===item.directoryKey}" aria-controls="knowledgeDetail"><span class="knowledge-row-copy"><strong>${escape(item.title)}</strong><small>${escape(guideOf(item)?.summary || item.description)}</small></span><span class="knowledge-row-status">${escape(item.status||"状态未提供")}${coreSkillIds.has(item.id)?' · '+escape(identityLabel(identityOf(item))):''}${guideOf(item)?.source_notice && (!identityOf(item)||identityOf(item).comparison?.status==='CONFLICT') ? ' · 来源待核' : ''}</span><span class="knowledge-row-action">${item.method?"查看说明":"查看条件"} ›</span></button>`).join("") : '<div class="knowledge-empty"><h3>没有匹配的条目</h3><p>试试其他名称或用途，或清除搜索与筛选。</p><button type="button" class="secondary-button" id="knowledgeReset">清除搜索与筛选</button></div>';
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
  document.addEventListener("click", async event => {
    const button=event.target.closest(".copy-method");
    if(!button) return;
    const copyState=$(button.dataset.copyState);
    const copyTarget=$(button.dataset.copyTarget);
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent="已复制";
      $("pageAlert").textContent="使用方法已复制，尚未执行。";
      if(copyState) copyState.textContent="已复制，尚未执行。";
    } catch {
      button.textContent="请选中上方方法复制";
      $("pageAlert").textContent="无法访问剪贴板，请选中方法手动复制。";
      if(copyState) copyState.textContent="无法访问剪贴板。内容已选中，请按 Ctrl+C 或长按复制。";
      if(copyTarget) {copyTarget.focus();copyTarget.select();}
    }
  });
$('entrySearch').addEventListener('input',()=>{if(catalog)renderKnowledge();});
const guideRequest=fetch('/static/knowledge_guides.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json();}).then(data=>{
  if(data.schema!=='orbit-knowledge-guides/v1'||!data.entries||typeof data.entries!=='object')throw Error();
  for(const guide of Object.values(data.entries)){
    if(!['summary','example'].every(k=>typeof guide[k]==='string'&&guide[k])||!['scenarios','inputs','outputs','requirements','limits','references'].every(k=>Array.isArray(guide[k])&&guide[k].length&&guide[k].every(x=>typeof x==='string')))throw Error();
    if(guide.host_references&&!Array.isArray(guide.host_references))throw Error();
    if(guide.workflow&&(!Array.isArray(guide.workflow)||guide.workflow.length<3||guide.workflow.some(step=>typeof step!=='string'||!step.trim())))throw Error();
    if(guide.source_notice&&(!/^\d{4}-\d{2}-\d{2}$/.test(guide.source_notice.observed_at)||!['status','message'].every(k=>typeof guide.source_notice[k]==='string'&&guide.source_notice[k])||(guide.source_notice.checksums&&!Array.isArray(guide.source_notice.checksums))))throw Error();
    if(guide.source_notice?.checksums?.some(row=>!row||typeof row.label!=='string'||!/^[0-9a-f]{64}$/.test(row.sha256)))throw Error();
    if(guide.source_notice?.location_note && (typeof guide.source_notice.location_note!=='string'||/\b[A-Z]:\\/i.test(guide.source_notice.location_note)))throw Error();
  }
  guides=data;
  renderIteration(data.iteration);
}).catch(()=>{$('knowledgeGuideNotice').hidden=false;$('knowledgeGuideNotice').textContent='详细介绍暂时不可用，仍可查看目录原有用途和技术方法。';renderIteration(null);});
 const identityRequest=fetch('/api/orbit/skill-identity',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json();}).then(data=>{if(data.schema!=='orbit-skill-identity/v1'||data.execution_authority!==false||!data.skills)throw Error();return data;}).catch(()=>null);
 Promise.all([fetch('/api/orbit/navigation',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('工具目录读取失败');return r.json();}),guideRequest,identityRequest]).then(([data,,identityData])=>{if(!Array.isArray(data.tools)||!Array.isArray(data.capabilities))throw Error('工具目录格式不完整');catalog=data;identities=identityData;const statuses=[...new Set(knowledgeEntries().map(x=>x.status).filter(Boolean))];$('knowledgeStatus').innerHTML='<option value="">全部状态</option>'+statuses.map(s=>`<option value="${escape(s)}">${escape(s)}</option>`).join('');const tool=new URLSearchParams(location.search).get('tool');const deepLink=tool&&knowledgeEntries().find(item=>item.directoryType==='tool'&&item.id===tool);if(deepLink)knowledgeSelection=deepLink.directoryKey;renderKnowledge();if(deepLink){$('knowledgeDirectory').classList.add('detail-open');$('workspaceView').classList.add('knowledge-detail-active');renderKnowledgeDetail(deepLink);}}).catch(e=>{const error=$('entryError');error.hidden=false;error.textContent=e.message;$('workspaceView').insertBefore(error,$('knowledgeDirectory'));window.scrollTo(0,0);});
})();
