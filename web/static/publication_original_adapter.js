/* Original review layout, current immutable evidence and existing event handlers. */
(() => {
  'use strict';
  const $=s=>document.querySelector(s);
  const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const homes=new Map();
  let data=null,stage=null,round='first',routedOffer=null;
  const json=v=>`<pre>${esc(JSON.stringify(v??{},null,2))}</pre>`;
  const stockPolicyReady=packet=>{
    const policy=packet?.publication_stock_policy;
    return policy&&Object.getPrototypeOf(policy)===Object.prototype
      &&Object.keys(policy).sort().join('|')==='quantity_per_sku|review_round|schema_version|scope|source'
      &&policy.schema_version==='publication-default-stock/v1'
      &&policy.quantity_per_sku===200&&policy.scope==='EACH_SELECTED_SKU'
      &&policy.source==='SYSTEM_GOVERNED_DEFAULT'&&policy.review_round==='ROUND1';
  };
  function move(id,target){const n=$('#'+id),to=$(target);if(!n||!to)return;if(!homes.has(id)){const mark=document.createComment('review home '+id);n.before(mark);homes.set(id,mark);}to.append(n);}
  function choose(value){round=value;$('#firstReviewWorkspace').hidden=value!=='first';$('#originalImagesRound').hidden=value!=='images';$('#originalFinalRound').hidden=value!=='final';document.querySelectorAll('[data-original-round]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.originalRound===value)));}
  function list(selector,values,empty){$(selector).innerHTML=values.length?values.map(v=>`<p>${esc(v)}</p>`).join(''):`<p>${esc(empty)}</p>`;}
  function imagePlan(){
    const plan=data.first_review_image_plan||data.round1_prepared_review?.packet?.image_execution_plan||{},root=$('#originalBrandPlans');
    const planLabel=data.frozen_review_projection?'冻结图片方案':'本次待确认图片方案';
    const roles={cover_scene:'场景主图',installed_detail:'上墙细节',scene_1:'场景 1',scene_2:'场景 2',scene_3:'场景 3',assembled_size:'组合尺寸',piece_layout_or_instructions:'分片布局与安装说明'};
    root.innerHTML=(plan.brand_plans||[]).map(p=>`<article class="first-review-content-option"><header><strong>${esc(p.label||p.id)}</strong><span>${planLabel}</span></header><p>${esc(p.positioning)}</p><small>来源图 ${esc((p.reference_positions||[]).join('、'))}</small><div class="original-image-plan-roles">${(p.generated_assets||[]).map(a=>`<div><strong>${esc(roles[a.role]||a.role)} · ${esc(a.quantity)} 张</strong><p>${esc(a.brief)}</p></div>`).join('')}</div></article>`).join('')||'<p>当前资料没有品牌图片方案。</p>';
    $('#originalTranslationPlan').textContent=plan.translation_plan?.note||'当前资料未提供翻译范围说明。';
    // Existing source-image cards keep their own loading/error and identity handlers.
    move('embeddedImageReview','#originalSourceImages');
  }
  function imageStatus(){if(!data)return;const r=data.r2_candidate_review||{},state=r.r2_consumer||{};$('#originalImagesStatus').textContent=`Offer ${data.product.offer_id} · ${$('#localizedImageResultsBadge')?.textContent||'图片决定尚未读取'}。图片采用只保存当前候选决定；完整第二轮状态：${state.code||state.status||'证据尚未齐备'}。`;}
  function render(value){
    const frozen=!!(value.frozen_review_projection&&value.frozen_first_review);
    const preparation=value.round1_prepared_review;
    const ready=preparation?.ok===true&&preparation.status==='PREPARED'&&preparation.prepared_reference&&preparation.packet&&preparation.current_revision===value.product?.revision;
    if(!frozen&&!ready){clear();return;}
    data=value;stage=null;
    if(routedOffer!==String(value.product?.offer_id)){
      const params=new URLSearchParams(location.search),requested=params.get('round');
      round=params.get('offer_id')===String(value.product?.offer_id)&&['first','images','final'].includes(requested)?requested:'first';
      routedOffer=String(value.product?.offer_id);
    }
    const first=frozen?value.frozen_first_review:preparation.packet,product=value.product||{},audit=first.audit_log||{};
    const complete=frozen||(first.status==='FIRST_REVIEW_READY'&&!(first.blockers||[]).length&&stockPolicyReady(first));
    const stock=first.publication_stock_policy;
    const stockLine=stockPolicyReady(first)
      ?`库存策略 · 每个所选 SKU ${stock.quantity_per_sku} 件 · ${stock.scope} · ${stock.source} · ${stock.review_round}`
      :frozen?'库存策略 · 历史首轮记录未提供有效策略，当前页面不补写':'库存策略 · 缺失或变更，当前不可审核';
    $('#originalPublicationReview').hidden=false;document.body.classList.add('original-review-active');
    $('#firstReviewWorkspaceOffer').textContent=product.offer_id;
    $('#firstReviewWorkspaceTargets').textContent=(first.targets||[]).length;
    $('#firstReviewWorkspaceRevision').textContent=frozen?first.approved_revision:product.revision;
    $('#firstReviewWorkspaceWrites').textContent=frozen?'本轮页面只读':'尚未批准 · 平台写入 0';
    $('#firstReviewWorkspaceSubtitle').textContent=`${product.title} · SKU ${product.seller_sku_candidate} · ${frozen?'已批准事实保留原快照':'请审核本次完整首轮内容'}`;
    $('#firstReviewWorkspaceStatus').textContent=frozen?'第一轮已冻结':complete?'第一轮待确认':'首轮资料待补充';
    const note=$('#firstReviewWorkspace .first-review-zero-write-note span');
    if(note)note.textContent=frozen?'本轮已冻结，保留原批准与事实；当前页面只读核对。':'本轮确认仅冻结商品事实；不会同步妙手、创建商城草稿或发布商品。';
    $('#firstReviewDecisionHeadline').textContent=frozen?'核对已冻结的一轮事实和依据；图片与最终发布各自使用当前阶段证据。':'核对事实、目标店铺、文案、售价及图片方案，再明确批准当前首轮。';
    const summary=audit.review_summary||{};
    list('#firstReviewDecisionFacts',[`Offer ${product.offer_id} · SKU ${product.seller_sku_candidate}`,`已批准 revision ${first.approved_revision} · ${(first.targets||[]).length} 个目标`,stockLine,...(first.shared_review_facts?.variants||[]).map(v=>`${v.specification_value||'规格未提供'} · ${v.weight_kg??'重量未提供'} kg · ${(v.package_cm||[]).join(' × ')} cm`)],'冻结事实未提供。');
    list('#firstReviewDecisionRecommendations',summary.final_recommendations||[],'冻结记录未单列建议；查看下方图片方案和决策理由。');
    list('#firstReviewDecisionQuestions',[`${value.r2_candidate_review?.images?.length||0} 张本地化候选，当前逐图决定与保存状态见第二轮。最终发布包是否形成以最终审核页为准。`],'当前没有新增决定。');
    window.OrbitOriginalRender.candidates({...first,status:complete?'READY':'资料待补充',external_write_count:0});
    $('#firstReviewCandidatesNotice').textContent=`${frozen?'首轮冻结':'首轮待确认'} revision ${frozen?first.approved_revision:product.revision} · ${first.knowledge_basis?.version||'知识依据版本未记录'}。平台中文类目、双品牌英中稿和逐规格售价均来自本次首轮审核内容。`;
    window.OrbitOriginalRender.audit({...audit,status:'READY'});
    $('#firstReviewAuditDisclosure').textContent='以下为首轮形成时的来源、决策与操作记录，保留当时状态；当前图片与发布状态见对应审核页。';
    $('#firstReviewFinalBoardTitle').textContent='首轮形成时的结论与待决项';
    $('#firstReviewAudit .first-review-final-hint').textContent='这里保留历史首轮记录，已批准的当前快照继续有效；不能从历史问题推断需要再次批准。';
    if(!frozen){
      list('#firstReviewDecisionFacts',[`Offer ${product.offer_id} · SKU ${product.seller_sku_candidate}`,`待确认 revision ${product.revision} · ${(first.targets||[]).length} 个目标`,stockLine,...(first.shared_review_facts?.variants||[]).map(v=>`${v.specification_value||'规格未提供'} · ${v.weight_kg??'重量未提供'} kg`)],'本次事实未提供。');
      $('#firstReviewAuditDisclosure').textContent='以下是本次准备内容的来源与决策依据，尚未批准或冻结。';
      $('#firstReviewFinalBoardTitle').textContent='本次首轮结论与待决项';
      $('#firstReviewAudit .first-review-final-hint').textContent='请在同屏确认区域审核并明确批准；打开页面不会产生批准。';
      if(!complete){
        $('#firstReviewWorkspaceSubtitle').textContent=`${product.title} · SKU ${product.seller_sku_candidate} · 当前准备资料未齐备`;
        $('#firstReviewCandidatesNotice').textContent='当前仅展示已取得的真实资料。类目、文案或图片方案仍需补齐，尚不构成完整首轮审核包。';
        list('#firstReviewDecisionQuestions',first.blockers||[],'执行者仍需补齐首轮准备资料。');
      }
      let confirmation=$('#originalRound1Confirmation');
      if(!confirmation){confirmation=document.createElement('div');confirmation.id='originalRound1Confirmation';$('#firstReviewWorkspace').append(confirmation);}
      move('round1Category','#originalRound1Confirmation');
      round='first';
    }
    move('firstReviewImagePlan','#firstReviewImagePlanSlot');
    move('frozenMasterReview','#originalImagesSlot');
    move('localizedImageResults','#originalImagesSlot');
    move('publicationFlow','#originalStageLedger');
    move('publicationClosure','#originalStageLedger');
    imagePlan();imageStatus();finalReview();choose(round);
  }
  function finalReview(){
    if(!data)return;
    const market=stage?.marketplace||{},successor=market.successor_preview;
    const candidate=successor||market.preview||market.candidate,manifest=candidate?.review_manifest;
    const predecessor=successor?(market.candidate||market.preview):null;
    const matching=String(stage?.offer_id||'')===String(data.product.offer_id);
    const usable=matching&&manifest;
    const successorPending=usable&&successor?.status==='NOT_APPROVED';
    const successorBlocked=successorPending&&successor?.review_blocked===true;
    $('#releaseCandidateBadge').textContent=successorBlocked?'新候选证据不足 · 不可审核':successorPending?'新候选待审核 · 未批准':usable?'当前最终候选':'最终候选尚未形成';
    $('#releaseCandidateMessage').textContent=usable?'以下内容直接读取当前发布阶段的冻结候选；批准和执行由下方现有阶段操作处理。':'当前尚无可审核的最终发布候选。首轮与图片资料不能代替最终冻结包；以下各区保留原审核位置并明确缺项。';
    $('#releaseCandidateContent').hidden=false;
    const targetCount=manifest?.targets?.length||candidate?.target_labels?.length||0;
    const qualityStatus=candidate?.durable_quality_evidence?.status||'NOT_AVAILABLE';
    const fields=[['商品身份',`Offer ${data.product.offer_id} · SKU ${data.product.seller_sku_candidate}`],['第一轮',data.frozen_first_review?`已冻结 revision ${data.frozen_first_review.approved_revision}`:'待确认，尚未冻结'],['图片采用',$('#localizedImageResultsBadge')?.textContent||'尚未读取'],['COMMON',stage?.common?.status||'尚无当前回执'],['平台阶段',successorPending?'NOT_APPROVED · 禁止执行':market.status||'尚无当前回执'],['质量证据',qualityStatus==='PASSED'?'PASSED':`${qualityStatus} · 阻断审核与执行`],['完整目标',targetCount?`${targetCount} 个目标`:'尚未形成'],['最终候选',usable?candidate.candidate_digest||'摘要未提供':'尚未形成']];
    if(predecessor?.candidate_digest)fields.push(['上一候选',`${predecessor.candidate_digest} · 历史保留`]);
    $('#releaseCandidateChecks').innerHTML=fields.map(([k,v])=>`<article><strong>${esc(k)}</strong><span>${esc(v)}</span></article>`).join('');
    const missing='<p class="first-review-audit-empty">当前最终冻结包尚未提供此项，未用历史候选或其他商品补齐。</p>';
    const groups=manifest?.copy_sets||[];
    const changedTargets=successor?.changed_targets||successor?.changed_target_labels||[];
    const changeNotice=successorPending?`<aside class="release-successor-notice" role="status"><strong>${successorBlocked?'durable_quality_evidence 未通过，当前不可审核或执行':'本次 successor 尚未批准，当前不能发布'}</strong><p>仅 Shopee MY / TH / VN 的本地化描述发生变化；标题、SKU、价格、类目、图片、目标范围及其他业务事实沿用上一冻结候选。完整 ${esc(targetCount)} 个目标均在下方展示。</p></aside>`:'';
    $('#releaseCandidateCopy').innerHTML=changeNotice+(usable&&groups.length?groups.map((g,i)=>{const targets=g.target_labels||[],changed=targets.some(t=>changedTargets.includes(t));return `<article class="release-review-copy-card${changed?' release-review-copy-changed':''}"><div class="release-review-card-head"><strong>文案组 ${i+1} · ${esc(g.language)}</strong><span>${esc(targets.join('、'))}${changed?' · 本次描述变更':''}</span></div><h6>${esc(g.title)}</h6><p>${esc(g.description)}</p></article>`;}).join(''):missing);
    $('#releaseCandidateVariants').innerHTML=usable&&manifest.variants?.length?manifest.variants.map(v=>`<article class="release-review-variant-card"><h6>${esc(v.model_sku||v.sku_id)} · ${esc(Object.values(v.specification||{}).join(' / ')||v.display_name||v.specification_value)}</h6><p>重量 ${esc(v.parcel?.weight_kg)} kg · 包裹 ${esc(v.parcel?.package_cm?.join(' × '))} cm</p><details><summary>完整规格与物流来源</summary>${json(v)}</details></article>`).join(''):missing;
    $('#releaseCandidateTargets').innerHTML=usable&&manifest.targets?.length?`<div class="table-scroll"><table><thead><tr><th>目标 / 语言</th><th>类目</th><th>逐规格售价</th><th>仓库库存</th></tr></thead><tbody>${manifest.targets.map(t=>`<tr><td>${esc(t.target_label)}<br>${esc(t.locale)}</td><td>${esc(t.category?.category_zh||t.category?.path||t.category?.name||t.category?.id||t.category?.category_id||'当前候选未提供')}<details><summary>类目依据</summary>${json(t.category)}</details></td><td>${(t.prices||[]).map(p=>`${esc(p.model_sku)} · ${esc(p.amount)} ${esc(p.currency)}<details><summary>完整价格与公式依据</summary>${json(p)}</details>`).join('<br>')}</td><td><details><summary>库存与仓库依据</summary>${t.inventory?json(t.inventory):'当前最终候选未提供独立库存依据'}</details><small>文案组 ${esc(t.copy_set_id)} · 图片组 ${esc(t.image_set_id)}</small></td></tr>`).join('')}</tbody></table></div>`:missing;
    $('#releaseCandidateImages').innerHTML=usable&&manifest.image_sets?.length?manifest.image_sets.map(s=>`<article class="release-review-image-set"><header><strong>${esc((s.target_labels||[]).join('、'))}</strong><span>${s.images?.length||0} 张 · 按冻结顺序</span></header><div class="frozen-master-grid">${(s.images||[]).map(i=>`<figure><img src="${esc(safeImage(i.url))}" alt="${esc(i.role||i.position)}"><figcaption>${esc(i.position)} · ${esc(i.role)}</figcaption></figure>`).join('')}</div></article>`).join(''):missing;
    $('#releaseCandidateWriteBudget').innerHTML=usable&&candidate.write_budget?Object.entries(candidate.write_budget).map(([platform,b])=>`<article><strong>${esc(platform)}</strong><span>${esc((b.target_labels||[]).length)} 个目标 · 最多 ${esc(b.maximum_confirmed_writes??'未提供')} 次确认写入</span><details><summary>完整冻结预算</summary>${json(b)}</details></article>`).join(''):missing;
    const safeguards=candidate?.incident_safeguards||[],recovery=candidate?.error_recovery_policy?.classes||{};
    $('#releaseCandidateIncidents').innerHTML=usable&&(safeguards.length||Object.keys(recovery).length)?safeguards.map(r=>`<article><strong>${esc(r.incident_id)}</strong><span>${esc(r.invariant)}</span></article>`).join('')+Object.entries(recovery).map(([name,r])=>`<article><strong>${esc(name)}</strong><span>自动修复 ${esc(r.automatic_repair_attempts)} 次 · 自动重试 ${esc(r.automatic_retry_attempts)} 次 · 只读对账 ${esc(r.readback_reconciliation_attempts)} 次</span></article>`).join(''):'<p>当前阶段未提供已启用事故防护清单，不能据历史规则推断。</p>';
  }
  function safeImage(value){try{const u=new URL(value,location.origin);return u.origin===location.origin||u.protocol==='https:'?u.href:'';}catch{return '';}}
  function clear(){
    data=null;stage=null;round='first';routedOffer=null;
    document.body.classList.remove('original-review-active');$('#originalPublicationReview').hidden=true;
    for(const [id,mark] of homes){const node=$('#'+id);if(node&&mark.parentNode)mark.after(node);}homes.clear();
    ['firstReviewCandidateGrid','firstReviewAuditEvidence','firstReviewAuditDecisions','firstReviewAuditOperations','firstReviewWorkflowStages','firstReviewDecisionFacts','firstReviewDecisionRecommendations','firstReviewDecisionQuestions','releaseCandidateCopy','releaseCandidateVariants','releaseCandidateTargets','releaseCandidateImages','releaseCandidateWriteBudget','releaseCandidateIncidents','releaseCandidateChecks','originalBrandPlans'].forEach(id=>$('#'+id)?.replaceChildren());
  }
  document.addEventListener('DOMContentLoaded',()=>{
    const slot=$('#firstReviewImagePlanSlot');slot.innerHTML='<div id="originalBrandPlans"></div><p id="originalTranslationPlan"></p><div id="originalSourceImages"></div>';
    document.querySelectorAll('[data-original-round]').forEach(b=>b.onclick=()=>choose(b.dataset.originalRound));
    new MutationObserver(()=>{imageStatus();finalReview();}).observe($('#localizedImageResultsBadge'),{childList:true,subtree:true,characterData:true});
  });
  window.OrbitOriginalReview={render,clear,stages(value){if(!data||String(value.offer_id)!==String(data.product.offer_id))return;stage=value;finalReview();}};
})();
