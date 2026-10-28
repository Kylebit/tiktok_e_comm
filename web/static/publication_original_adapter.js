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
  let finalOnly=false,pendingStage=null;
  function frozenFinalContext(value){
    if(!actualFinalReady(value))return null;
    const market=value.marketplace,candidate=market.preview||market.candidate,manifest=candidate.review_manifest;
    const binding=market.plan?.payload?.r3_marketplace_binding;
    return candidate.offer_id===String(data.product.offer_id)
      &&market.native_final_review.offer_id===candidate.offer_id
      &&market.native_final_review.plan_id===candidate.plan_id
      &&manifest.manifest_digest===binding?.reviewed_candidate_facts?.review_manifest?.manifest_digest
      &&market.plan?.payload?.product_id===candidate.offer_id
      ?{market,candidate,manifest}:null;
  }
  function clearFrozenImages(){
    document.body.classList.remove('native-final-images-ready');
    $('#nativeFinalFrozenContent')?.remove();
  }
  function frozenImagesAndContent(value){
    const context=frozenFinalContext(value),pane=$('#pane-images');
    if(!context||!pane){clearFrozenImages();return;}
    const {market,candidate,manifest}=context;
    const identity=[candidate.candidate_digest,manifest.manifest_digest,market.native_final_review.review_digest].join(':');
    if($('#nativeFinalFrozenContent')?.dataset.frozenIdentity===identity)return;
    clearFrozenImages();
    const panel=document.createElement('section');panel.id='nativeFinalFrozenContent';panel.className='panel native-final-frozen-content';
    panel.dataset.frozenIdentity=identity;
    const figure=(image,label)=>{
      const url=safeImage(image.url);
      return `<figure><a href="${esc(url)}" target="_blank" rel="noopener"><img src="${esc(url)}" alt="${esc(label)}" loading="eager" data-native-frozen-image></a><figcaption>${esc(label)}<span class="native-image-load" role="status">正在载入图片…</span></figcaption></figure>`;
    };
    const sets=new Map();
    for(const set of manifest.image_sets||[]){
      const key=JSON.stringify(set.images||[]);
      if(!sets.has(key))sets.set(key,{images:set.images||[],targets:[]});
      sets.get(key).targets.push(...set.target_labels||[]);
    }
    const gallery=[...sets.values()].map(set=>`<article class="native-frozen-gallery"><h3>完整冻结图库 · ${esc(set.images.length)} 张</h3><p>${esc(set.targets.join('、'))} · 按发布顺序；点击图片查看大图。</p><div class="native-frozen-image-grid">${set.images.map(i=>figure(i,`${i.position} · ${i.role}`)).join('')}</div></article>`).join('');
    const heroes=(manifest.variants||[]).map(variant=>{
      const groups=new Map();
      for(const [target,url] of Object.entries(variant.hero_images_by_target||{})){
        if(!groups.has(url))groups.set(url,[]);groups.get(url).push(target);
      }
      const name=Object.values(variant.specification||{}).join(' / ')||variant.display_name||'';
      return `<article data-native-variant="${esc(variant.model_sku)}"><h3>SKU ID (Model SKU) ${esc(variant.model_sku)} · ${esc(name)}</h3><div class="native-frozen-image-grid">${[...groups].map(([url,targets])=>figure({url},`${variant.model_sku} · ${name} · ${targets.join('、')}`)).join('')}</div><p>成本 ${esc(variant.cost?.amount)} ${esc(variant.cost?.currency)} · 重量 ${esc(variant.parcel?.weight_kg)} kg · 包裹 ${esc(variant.parcel?.package_cm?.join(' × '))} cm</p></article>`;
    }).join('');
    const cards=(manifest.targets||[]).filter(t=>t.listing_structure?.cards).map(target=>`<section><h2>${esc(target.target_label)} · 独立卡片的首图与完整顺序</h2><p>${esc(target.listing_structure.note_zh)}</p>${target.listing_structure.cards.map(card=>`<article data-native-ozon-card="${esc(card.model_sku)}"><h3>内部 SKU ${esc(card.canonical_sku)} · Model SKU ${esc(card.model_sku)} · ${esc(card.image_count)} 张</h3><div class="native-frozen-image-grid">${(card.images||[]).map(i=>figure(i,`${i.position}${i.position===1?' · 本卡首图':''} · ${i.role}`)).join('')}</div></article>`).join('')}</section>`).join('');
    const copy=(manifest.copy_sets||[]).map((group,index)=>`<article data-native-copy-set="${esc(index)}"><h3>文案组 ${index+1} · ${esc(group.language)}</h3><p>${esc((group.target_labels||[]).join('、'))}</p><h4>${esc(group.title)}</h4><p class="native-frozen-description">${esc(group.description)}</p>${(group.variants||[]).map(v=>`<p>Model SKU ${esc(v.model_sku)} · ${esc(v.display_name)}</p>`).join('')}</article>`).join('');
    panel.innerHTML=`<header><p class="kicker">FROZEN FINAL REVIEW</p><h2>本轮已冻结的图片与内容</h2><p>Offer ${esc(candidate.offer_id)} · ${manifest.targets.length} 家店。本页只读核对；最终决定仍由原唯一终审控件保存。</p><details><summary>当前候选与冻结资料身份</summary><p>候选 ${esc(candidate.candidate_digest)}</p><p>计划 ${esc(candidate.plan_id)}</p><p>Manifest ${esc(manifest.manifest_digest)}</p><p>终审 ${esc(market.native_final_review.review_digest)}</p></details></header>${gallery}<section><h2>三款规格各自主图</h2>${heroes}</section>${cards}<section><h2>各店发布文案与规格名称</h2>${copy}</section>`;
    pane.prepend(panel);document.body.classList.add('native-final-images-ready');
    panel.querySelectorAll('[data-native-frozen-image]').forEach(img=>{
      const status=img.closest('figure').querySelector('.native-image-load');
      const report=()=>{status.textContent=img.naturalWidth>0?'图片已载入':'图片载入失败；可点击上图重试查看原图。';status.dataset.state=img.naturalWidth>0?'loaded':'error';};
      img.addEventListener('load',report,{once:true});img.addEventListener('error',report,{once:true});
      if(img.complete)report();
    });
  }
  function actualFinalReady(value){
    const market=value?.marketplace,candidate=market?.preview||market?.candidate,manifest=candidate?.review_manifest;
    return data&&String(value.offer_id)===String(data.product?.offer_id)
      &&candidate?.schema_version==='publication-release-candidate/v1'
      &&manifest?.schema_version==='publication-final-review-manifest/v1'
      &&candidate.offer_id===String(data.product.offer_id)
      &&candidate.plan_id===market.plan?.plan_id
      &&Array.isArray(manifest.targets)&&manifest.targets.length>0
      &&JSON.stringify(manifest.targets.map(row=>row.target_label))===JSON.stringify(candidate.target_labels)
      &&market.native_final_review?.schema_version==='native-sole-final-review/v1'
      &&market.native_final_review.candidate_digest===candidate.candidate_digest;
  }
  function syncActualStagePresentation(){
    if(!data||!stage||!actualFinalReady(stage))return;
    const common=stage.common||{},market=stage.marketplace,candidate=market.preview||market.candidate;
    const image=$('#reviewImageCount');
    if(image&&candidate.image_route_checks?.all_targets_complete===true&&candidate.durable_quality_evidence?.status==='PASSED'){
      const message='原 R2 已冻结 · 最终图片路由与 QA 已核';if(image.textContent!==message)image.textContent=message;
    }
    if(common.status==='VERIFIED'&&common.retained_baseline_facts?.status==='RETAINED_NATIVE_COMMON_BASELINE'){
      const row=[...document.querySelectorAll('#publicationFlowReview .review-version-row')].find(r=>r.querySelector('strong')?.textContent==='COMMON 来源与执行条件');
      const detail=row?.children[1],message=common.user_status?.summary||'原 COMMON 签名官方回读已验证；沿用原成功记录，不重复写入。';
      if(detail&&detail.textContent!==message)detail.textContent=message;
      const ledger=$('#publicationFlowLedger tbody tr'),cell=ledger?.children[2],oldDetail=cell?.querySelector('details');
      if(cell&&cell.firstChild?.textContent!==message){cell.replaceChildren(document.createTextNode(message));if(oldDetail)cell.append(oldDetail);}
    }
    const select=$('#connectedProductSelect'),offer=String(data.product.offer_id);
    if(select&&!Array.from(select.options).some(option=>option.value===offer)){
      const option=document.createElement('option');option.value=offer;option.dataset.currentLoadedProduct='true';
      option.textContent=`当前已读取 · SKU ${data.product.seller_sku_candidate||'—'} · Offer ${offer} · ${data.product.title||''}`;
      select.append(option);select.value=offer;$('#openConnectedProduct').disabled=false;
      const note=$('#connectedProductStatus');if(note)note.textContent='当前商品已从准确 Offer 读取；列表中的其他项目保留原历史来源，本页不创建新的商品注册。';
    }
    // Generic stage ledger retains its authoritative controls; only its frozen
    // manifest images use the same existing bounded image proxy as other views.
    document.querySelectorAll('#publicationFlowReview img[data-flow-image]').forEach(img=>{
      const url=img.getAttribute('src');if(url?.startsWith('https://'))img.setAttribute('src','/api/proxy-image?url='+encodeURIComponent(url));
    });
    compactStagePresentation();
    frozenImagesAndContent(stage);
  }
  function render(value){
    const frozen=!!(value.frozen_review_projection&&value.frozen_first_review);
    const preparation=value.round1_prepared_review;
    const ready=preparation?.ok===true&&preparation.status==='PREPARED'&&preparation.prepared_reference&&preparation.packet&&preparation.current_revision===value.product?.revision;
    if(!frozen&&!ready){
      const incoming=pendingStage;clear();data=value;finalOnly=true;pendingStage=null;
      // Native final stages arrive separately from the original dashboard.
      // Keep this actual product context; do not invent R1/R2 registrations.
      if(incoming&&String(incoming.offer_id)===String(value.product?.offer_id))queueMicrotask(()=>window.OrbitOriginalReview.stages(incoming));
      return;
    }
    finalOnly=false;
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
    const fields=[['商品身份',`Offer ${data.product.offer_id} · SKU ${data.product.seller_sku_candidate}`],['第一轮',data.frozen_first_review?`已冻结 revision ${data.frozen_first_review.approved_revision}`:finalOnly&&market.plan?.payload?.r3_marketplace_binding?.r2_identity?.round1_snapshot_digest?'原 R1 冻结事实已核':'待确认，尚未冻结'],['图片采用',finalOnly&&candidate?.image_route_checks?.all_targets_complete===true&&candidate?.durable_quality_evidence?.status==='PASSED'?'原 R2 已冻结 · 最终路由和 QA 已核':$('#localizedImageResultsBadge')?.textContent||'尚未读取'],['COMMON',stage?.common?.status||'尚无当前回执'],['平台阶段',successorPending?'NOT_APPROVED · 禁止执行':market.status||'尚无当前回执'],['质量证据',qualityStatus==='PASSED'?'PASSED':`${qualityStatus} · 阻断审核与执行`],['完整目标',targetCount?`${targetCount} 个目标`:'尚未形成'],['最终候选',usable?candidate.candidate_digest||'摘要未提供':'尚未形成']];
    if(predecessor?.candidate_digest)fields.push(['上一候选',`${predecessor.candidate_digest} · 历史保留`]);
    if(manifest?.product?.ozon_card_structure)fields.push(['刊登结构',`Ozon 3 条独立 B 链接，每条 1 个花型、每件 1 片；其余 ${(manifest.targets||[]).filter(t=>t.target_label!=='ozon:RU').length} 店各一条 B 链接、3 个规格。原 A 保留。`]);
    $('#releaseCandidateChecks').innerHTML=fields.map(([k,v])=>`<article><strong>${esc(k)}</strong><span>${esc(v)}</span></article>`).join('');
    const missing='<p class="first-review-audit-empty">当前最终冻结包尚未提供此项，未用历史候选或其他商品补齐。</p>';
    const retained=market.plan?.payload?.r3_marketplace_binding?.retained_category_metadata;
    const sameFrozenPlan=market.plan?.plan_id===candidate?.plan_id
      &&market.plan?.payload?.product_id===String(data.product.offer_id)
      &&market.plan?.payload?.r3_marketplace_binding?.reviewed_candidate_facts?.review_manifest?.manifest_digest===manifest?.manifest_digest;
    const sourceCode=usable&&sameFrozenPlan&&retained?.['ozon:RU']?.schema_version==='retained-official-category-projection/v1'
      &&retained['ozon:RU'].offer_id===String(data.product.offer_id)
      &&retained['ozon:RU'].target==='ozon:RU'
      ?retained['ozon:RU'].source_model_basis?.original_supplier_item_code:null;
    const sourceCodeLine=sourceCode?`<p>供应商货号 ${esc(sourceCode)} · 来源参考，不是商品 Seller SKU、变体 Model SKU 或制造型号。</p>`:'';
    const estimateLine=v=>{
      const r1=market.plan?.payload?.r3_marketplace_binding?.documents?.round1_snapshot;
      const planning=r1?.fact_snapshot?.product_facts?.parcel_planning?.planning_estimates;
      const estimate=planning?.sku_estimates?.[v.variant_key],w=estimate?.weight_kg,p=estimate?.package_cm;
      const identity=market.plan?.payload?.source_product_identity?.identity_digest;
      if(!sameFrozenPlan||planning?.offer_id!==String(data.product.offer_id)||planning.source_identity_digest!==identity
        ||!planning.selected_sku_keys?.includes(v.variant_key)||w?.status!=='estimated'||p?.status!=='estimated'
        ||Number(w.value)!==Number(v.parcel?.weight_kg)||JSON.stringify(p.value.map(Number))!==JSON.stringify(v.parcel?.package_cm?.map(Number)))return '';
      return `<p>包裹为原冻结估值，不是实测 · 置信度 ${esc(w.confidence)} / ${esc(p.confidence)}。重量区间 ${esc(w.lower)}–${esc(w.upper)} kg；包裹区间 ${esc(p.lower.join(' × '))}–${esc(p.upper.join(' × '))} cm。估值影响运费、体积重和报价。</p><details><summary>原估值依据与范围</summary>${json(estimate)}</details>`;
    };
    const attributes=category=>Array.isArray(category?.technical_attribute_candidates)
      ?category.technical_attribute_candidates.map(a=>`<p>${esc(a.name||'属性 '+a.attribute_id)}：${esc(a.value)}${a.value_kind==='OFFICIAL_SINGLE_OPTION_ENUM'?' · 官方单选枚举，制造批号未知':''}${a.value_kind==='EXISTING_MODEL_SKU_PROFILE_RULE'?' · 现有 Model SKU 与类目规则的技术型号，不是制造商型号':''}</p>`).join(''):'';
    const variantHeroes=v=>{
      if(!v.hero_images_by_target||typeof v.hero_images_by_target!=='object')return '';
      const grouped=new Map();
      for(const [target,url] of Object.entries(v.hero_images_by_target)){
        if(!grouped.has(url))grouped.set(url,[]);
        grouped.get(url).push(target);
      }
      return `<div class="frozen-master-grid">${[...grouped].map(([url,targets])=>`<figure><img src="${esc(safeImage(url))}" alt="${esc(v.model_sku)} 冻结规格首图"><figcaption>${esc(v.model_sku)} · ${esc(Object.values(v.specification||{}).join(' / '))}<br>${esc(targets.join('、'))}</figcaption></figure>`).join('')}</div>`;
    };
    const listingStructure=t=>t.listing_structure?`<section data-listing-structure="${esc(t.target_label)}"><p>${esc(t.listing_structure.note_zh)}</p>${(t.listing_structure.cards||[]).map(c=>`<p>独立 B 卡 · 内部 SKU ${esc(c.canonical_sku)} · SKU ID (Model SKU) ${esc(c.model_sku)} · 包装 ${esc(c.quantity_per_pack)} 片 · 技术分卡型号 ${esc(c.model_name)}</p>${Array.isArray(c.images)?`<details><summary>本卡 ${esc(c.image_count)} 张冻结图片 · 首图与顺序</summary><div class="frozen-master-grid">${c.images.map(i=>`<figure><img src="${esc(safeImage(i.url))}" alt="${esc(i.role||i.position)}"><figcaption>${esc(i.position)}${i.position===1?' · 本卡首图':''} · ${esc(i.role)}</figcaption></figure>`).join('')}</div></details>`:''}`).join('')}</section>`:'';
    let companions=$('#releaseCandidateCompanions');
    if(!companions){companions=document.createElement('section');companions.id='releaseCandidateCompanions';companions.className='release-review-section';$('#releaseCandidateContent').append(companions);}
    const companionReview=usable?candidate.companion_actions:null;
    const companionActions=Array.isArray(companionReview?.actions)?companionReview.actions:[];
    companions.innerHTML='<h5>本次终审包含的伴随动作</h5>'+(companionReview?.schema_version==='publication-companion-action-review/v1'
      ?(companionActions.length?companionActions.map(a=>`<article><strong>${esc(a.action_kind==='OZON_FBS_STOCK_CONVERGENCE'?'Ozon 仓库库存':a.action_kind)}</strong><p>目标 ${esc(a.prerequisite_target||a.target_label||a.target_labels?.join('、'))}${a.quantity_per_sku!==undefined?' · 每个 Model SKU '+esc(a.quantity_per_sku)+' 件':''}</p>${a.warehouse_selection_policy?`<p>仓库规则 ${esc(a.warehouse_selection_policy)} · ${a.warehouse_identity_bound?'已绑定官方仓库身份，执行时再次核验':'身份待核验'}</p>`:''}<details><summary>完整动作、来源与完成回读</summary>${json(a)}</details></article>`).join(''):'<p>本次冻结候选没有伴随动作。</p>')+`<details><summary>完整冻结伴随范围与接续边界</summary>${json(companionReview)}</details>`:missing);
    const groups=manifest?.copy_sets||[];
    const changedTargets=successor?.changed_targets||successor?.changed_target_labels||[];
    const changeNotice=successorPending?`<aside class="release-successor-notice" role="status"><strong>${successorBlocked?'durable_quality_evidence 未通过，当前不可审核或执行':'本次 successor 尚未批准，当前不能发布'}</strong><p>仅 Shopee MY / TH / VN 的本地化描述发生变化；标题、SKU、价格、类目、图片、目标范围及其他业务事实沿用上一冻结候选。完整 ${esc(targetCount)} 个目标均在下方展示。</p></aside>`:'';
    $('#releaseCandidateCopy').innerHTML=changeNotice+(usable&&groups.length?groups.map((g,i)=>{const targets=g.target_labels||[],changed=targets.some(t=>changedTargets.includes(t));return `<article class="release-review-copy-card${changed?' release-review-copy-changed':''}"><div class="release-review-card-head"><strong>文案组 ${i+1} · ${esc(g.language)}</strong><span>${esc(targets.join('、'))}${changed?' · 本次描述变更':''}</span></div><h6>${esc(g.title)}</h6><p>${esc(g.description)}</p>${Array.isArray(g.variants)?`<section class="target-variant-displays"><h6>逐店规格显示名</h6>${g.variants.map(v=>`<p>SKU ID (Model SKU) ${esc(v.model_sku)} · ${esc(v.display_name)}</p>`).join('')}</section>`:''}</article>`;}).join(''):missing);
    $('#releaseCandidateVariants').innerHTML=usable&&manifest.variants?.length?sourceCodeLine+manifest.variants.map(v=>`<article class="release-review-variant-card"><h6>SKU ID (Model SKU) ${esc(v.model_sku||v.sku_id)} · ${esc(Object.values(v.specification||{}).join(' / ')||v.display_name||v.specification_value)}</h6><p>成本 ${esc(v.cost?.amount)} ${esc(v.cost?.currency)} · 重量 ${esc(v.parcel?.weight_kg)} kg · 包裹 ${esc(v.parcel?.package_cm?.join(' × '))} cm</p>${variantHeroes(v)}${estimateLine(v)}<details><summary>完整规格与物流来源</summary>${json(v)}</details></article>`).join(''):missing;
    $('#releaseCandidateTargets').innerHTML=usable&&manifest.targets?.length?`<div class="table-scroll"><table><thead><tr><th>目标 / 语言</th><th>类目</th><th>逐规格售价</th><th>仓库库存</th></tr></thead><tbody>${manifest.targets.map(t=>`<tr><td>${esc(t.target_label)}<br>${esc(t.locale)}${listingStructure(t)}</td><td>${esc(t.category?.category_zh||t.category?.path||t.category?.name||t.category?.id||t.category?.category_id||'当前候选未提供')}${attributes(t.category)}<details><summary>类目依据与官方技术属性</summary>${json(t.category)}</details></td><td>${(t.prices||[]).map(p=>`${esc(p.model_sku)} · ${esc(p.amount)} ${esc(p.currency)}<details><summary>完整价格与公式依据</summary>${json(p)}</details>`).join('<br>')}</td><td><details><summary>库存与仓库依据</summary>${t.inventory?json(t.inventory):'当前最终候选未提供独立库存依据'}</details><small>文案组 ${esc(t.copy_set_id)} · 图片组 ${esc(t.image_set_id)}</small></td></tr>`).join('')}</tbody></table></div>`:missing;
    $('#releaseCandidateImages').innerHTML=usable&&manifest.image_sets?.length?manifest.image_sets.map(s=>`<article class="release-review-image-set"><header><strong>${esc((s.target_labels||[]).join('、'))}</strong><span>${s.images?.length||0} 张 · 按冻结顺序</span></header><div class="frozen-master-grid">${(s.images||[]).map(i=>`<figure><img src="${esc(safeImage(i.url))}" alt="${esc(i.role||i.position)}"><figcaption>${esc(i.position)} · ${esc(i.role)}</figcaption></figure>`).join('')}</div></article>`).join(''):missing;
    $('#releaseCandidateWriteBudget').innerHTML=usable&&candidate.write_budget?Object.entries(candidate.write_budget).map(([platform,b])=>`<article><strong>${esc(platform)}</strong><span>${esc((b.target_labels||[]).length)} 个目标 · 最多 ${esc(b.maximum_confirmed_writes??'未提供')} 次确认写入</span><details><summary>完整冻结预算</summary>${json(b)}</details></article>`).join(''):missing;
    const safeguards=candidate?.incident_safeguards||[],recovery=candidate?.error_recovery_policy?.classes||{};
    $('#releaseCandidateIncidents').innerHTML=usable&&(safeguards.length||Object.keys(recovery).length)?safeguards.map(r=>`<article><strong>${esc(r.incident_id)}</strong><span>${esc(r.invariant)}</span></article>`).join('')+Object.entries(recovery).map(([name,r])=>`<article><strong>${esc(name)}</strong><span>自动修复 ${esc(r.automatic_repair_attempts)} 次 · 自动重试 ${esc(r.automatic_retry_attempts)} 次 · 只读对账 ${esc(r.readback_reconciliation_attempts)} 次</span></article>`).join(''):'<p>当前阶段未提供已启用事故防护清单，不能据历史规则推断。</p>';
    if(finalOnly&&usable&&sameFrozenPlan&&String(data.product.offer_id)==='4039931504')operatorFinalReview(candidate,manifest,market,{sourceCodeLine,estimateLine,attributes});
  }
  const countries={PH:'菲律宾',MY:'马来西亚',TH:'泰国',VN:'越南',GB:'英国',MX:'墨西哥',RU:'俄罗斯'};
  const languages={en:'英语',ms:'马来语',th:'泰语',vi:'越南语',es:'西班牙语',ru:'俄语'};
  let selectedCopy='';
  function targetDisplay(label){
    const target=stage?.marketplace?.preview?.review_manifest?.targets?.find(t=>t.target_label===label);
    const [channel,site]=String(label).split(':'),country=site?.split('_').pop();
    const platform={tiktok:'TikTok',shopee:'Shopee',ozon:'Ozon'}[channel]||target?.platform||channel;
    const original=stage?.marketplace?.plan?.payload?.r3_marketplace_binding?.documents?.first_review?.targets?.find(t=>t.target===label);
    const frozenKey=original?.selection_key;
    const configured=(data.pricing_review?.selected_store_prices||[]).find(t=>t.target_key===frozenKey);
    const shop=site?.startsWith('HB_')?'HomeBloom':site?.startsWith('LH_')?'LivelyHive':configured?.shop;
    return {platform,country:countries[country]||country||'国家未提供',shop:shop||`${countries[country]||country||''}店（店名未提供）`};
  }
  const targetText=label=>{const t=targetDisplay(label);return `${t.platform} · ${t.shop} · ${t.country}`;};
  function evidenceSection(){
    let details=$('#operatorTechnicalEvidence');
    if(!details){details=document.createElement('details');details.id='operatorTechnicalEvidence';details.className='operator-technical';
      details.innerHTML='<summary>技术与来源详情 · 冻结身份、预算、完整记录</summary><div id="operatorTechnicalBody"></div>';
      $('#releaseCandidatePanel').append(details);
    }
    return details;
  }
  function compactStagePresentation(){
    if(!document.body.classList.contains('operator-final-active'))return;
    evidenceSection();
    move('publicationFlow','#originalFinalActions');
    move('publicationFlowReview','#operatorTechnicalBody');move('publicationFlowLedger','#operatorTechnicalBody');
    move('publicationClosure','#operatorTechnicalBody');
    const saved=stage?.marketplace?.native_final_review?.approval_saved===true;
    $('#publicationFlowTitle').textContent=saved?'批准已保存 · 原任务进度':'确认本次完整上架范围';
    const status=$('#publicationFlowStatus');
    if(status&&saved&&status.textContent===status.dataset.operatorMessage){
      status.textContent='批准已保存，沿用原最终决定；原任务进度与官方回读见阶段详情。本页不再请求同一决定。';
      status.dataset.operatorMessage=status.textContent;
    }else if(status&&!saved&&!status.dataset.originalStatus){
      status.dataset.originalStatus=status.textContent;
      status.textContent='核对上方商品、图片、15家店铺及伴随动作后，在这里作出一次最终决定。尚未批准或发布。';
      status.dataset.operatorMessage=status.textContent;
    }
  }
  function operatorFinalReview(candidate,manifest,market,view){
    document.body.classList.add('operator-final-active');
    const panel=$('#releaseCandidatePanel'),content=$('#releaseCandidateContent');evidenceSection();
    $('#releaseCandidateTitle').textContent='商品上架终审';
    $('#releaseCandidateMessage').textContent=`本次 ${manifest.targets.length} 家店铺。COMMON 官方回读已保留；以下仅调整阅读方式，冻结内容不变。`;
    const technical=$('#operatorTechnicalBody');
    if($('#releaseCandidateChecks').parentNode!==technical)move('releaseCandidateChecks','#operatorTechnicalBody');
    for(const id of ['releaseCandidateWriteBudget','releaseCandidateIncidents']){
      const detail=$('#'+id).closest('details');if(detail&&detail.parentNode!==technical){
        const mark=document.createComment('compact detail home');detail.before(mark);homes.set(id+'-detail',mark);detail.dataset.compactHome=id+'-detail';technical.append(detail);
      }
    }
    let summary=$('#operatorProductSummary');
    if(!summary){summary=document.createElement('header');summary.id='operatorProductSummary';summary.className='operator-product-summary';panel.insertBefore(summary,content);}
    const facts=market.plan.payload.r3_marketplace_binding.documents.round1_snapshot.fact_snapshot.product_facts;
    const variants=manifest.variants||[];
    const decisionState=market.native_final_review?.approval_saved===true?'批准已保存':'尚未批准';
    summary.innerHTML=`<p class="operator-eyebrow">${manifest.targets.length} 家店铺 · TikTok / Shopee / Ozon · ${decisionState}</p><h2>${esc(facts.title||data.product.title)}</h2><p>商品 Seller SKU <strong>${esc(market.plan.payload.seller_sku)}</strong> · ${variants.length} 个变体 · COMMON 官方回读已验证一次</p>`;
    const sections={variants:$('#releaseCandidateVariants').closest('section'),images:$('#releaseCandidateImages').closest('section'),copy:$('#releaseCandidateCopy').closest('section'),targets:$('#releaseCandidateTargets').closest('section'),companions:$('#releaseCandidateCompanions')};
    Object.values(sections).forEach(section=>{if(section)content.append(section);});
    $('#releaseCandidateVariantsTitle').textContent='商品规格、成本与包裹';
    $('#releaseCandidateVariants').innerHTML=view.sourceCodeLine+variants.map(v=>`<article class="release-review-variant-card"><h6>SKU ID (Model SKU) ${esc(v.model_sku||v.sku_id)} · ${esc(Object.values(v.specification||{}).join(' / ')||v.display_name||v.specification_value)}</h6><p><strong>成本 ${esc(v.cost?.amount)} ${esc(v.cost?.currency)}</strong> · 重量估值 ${esc(v.parcel?.weight_kg)} kg · 包裹估值 ${esc(v.parcel?.package_cm?.join(' × '))} cm</p>${view.estimateLine(v)}<details><summary>完整规格与物流来源</summary>${json(v)}</details></article>`).join('');
    // Identical bytes are one image even when two brand-role rows use them.
    const generation=market.plan.payload.r3_marketplace_binding.documents.generation_result;
    const entries=new Map();
    for(const set of manifest.image_sets||[])for(const image of set.images||[]){
      const asset=(generation?.assets||[]).find(a=>a.public_url===image.url&&(a.brand_id===image.brand_id||a.brand_label?.toLowerCase()===image.brand_id?.split('-')[0]));
      const exactAssets=(generation?.assets||[]).filter(a=>a.public_url===image.url);
      const digest=asset?.artifact_digest||(exactAssets.length&&new Set(exactAssets.map(a=>a.artifact_digest)).size===1?exactAssets[0].artifact_digest:null);
      const key=digest||image.url;
      if(!entries.has(key))entries.set(key,{image,assets:exactAssets,targets:[],routes:[]});
      const entry=entries.get(key);entry.targets.push(...set.target_labels);entry.routes.push(set);
    }
    const displayEntries=[...entries.values()].sort((a,b)=>Math.min(...a.assets.map(x=>Number(x.review_number)).filter(n=>Number.isInteger(n)&&n>0),Number.MAX_SAFE_INTEGER)-Math.min(...b.assets.map(x=>Number(x.review_number)).filter(n=>Number.isInteger(n)&&n>0),Number.MAX_SAFE_INTEGER));
    const allReused=entries.size>0&&[...entries.values()].every(e=>e.assets.length&&e.assets.every(a=>a.status==='REUSED_FROM_MIAOSHOU'&&a.external_generation_count===0));
    const newAsset=a=>a.status==='COMPLETED'&&a.provider!=='MIAOSHOU_EXISTING'&&(a.external_generation_count>0||String(a.provider||'').toLowerCase()==='lingshi');
    const newUnique=new Set((generation?.assets||[]).filter(newAsset).map(a=>a.artifact_digest||a.public_url)).size;
    const sourceLabel=e=>e.assets.length&&e.assets.every(newAsset)?'已生成的合格图片':e.assets.length&&e.assets.every(a=>a.status==='REUSED_FROM_MIAOSHOU')?'原来源图复用':'素材来源待核验';
    const imageRoles={master:'主图',cover:'主图',detail:'细节',product_detail:'细节',scene_1:'场景一',scene_2:'场景二',scene_3:'场景三',proportion_evidence:'原图比例证据（无实测尺寸）',construction_evidence:'可见结构证据（非安装说明）',scene_in_use_toilet_roll:'纸巾卷使用场景（纸巾为道具）'};
    const roleLabel=e=>[...new Set(e.assets.map(a=>`${a.brand_label||a.brand_id||'品牌待核验'} / ${imageRoles[a.role]||a.role||'图片角色'}`))].join('、');
    const brandCounts={};for(const a of generation?.assets||[]){const brand=['hb','homebloom-sea'].includes(a.brand_id)?'HomeBloom':['lh','livelyhive-sea'].includes(a.brand_id)?'LivelyHive':null;if(brand&&a.artifact_digest&&['COMPLETED','REUSED_FROM_MIAOSHOU'].includes(a.status)){(brandCounts[brand]??=new Set()).add(a.artifact_digest);}}
    const gallerySummary=Object.entries(brandCounts).map(([brand,artifacts])=>`${brand} ${artifacts.size} 张独立图`).join('；');
    const incompleteGallery=Object.values(brandCounts).length===0||Object.values(brandCounts).some(artifacts=>artifacts.size<7);
    const galleryNote=`<p class="operator-muted">${esc(gallerySummary)}。无专用品类规则时每品牌至少 7 张独立合格图；主图、细节、场景及证据角色需完整。${incompleteGallery?'当前图库不足，不能进入发布终审。':'完整性以当前冻结计划及自动 QA 为准。'}</p>`;
    const actualGallery=market.plan.payload.r3_marketplace_binding.image_successor?.relation?.gallery_completeness;
    const galleryLineage=actualGallery?.status==='PASSED'&&actualGallery.total_distinct_qualified===entries.size?`<p class="operator-muted">当前合格图库 ${actualGallery.total_distinct_qualified} 张独立图片 = 保留 ${actualGallery.retained_qualified_count} 张已合格图片 + 新增 ${actualGallery.new_qualified_count} 张合格图片；不含历史失败图。计划数与实际交付分别核验。</p>`:'';
    if(incompleteGallery)$('#releaseCandidateMessage').textContent='图库准备未完成：每品牌至少 7 张独立合格图。旧候选保持未批准，不可作为完整终审候选。';
    $('#releaseCandidateImagesTitle').textContent='发布图片';
    const commonImageNote=market.plan.payload.r3_marketplace_binding.image_successor?'<p class="operator-muted">COMMON 事实基线已验证（原图版）；本次两品牌新图由正式发布载荷逐店覆盖。</p>':'';
    $('#releaseCandidateImages').innerHTML=`${commonImageNote}${galleryNote}${galleryLineage}<p id="operatorImageCount"><strong>${entries.size} 张独立${allReused?'原图':'图片'}</strong> · ${(manifest.image_sets||[]).length} 条品牌路由${allReused?' · LivelyHive / HomeBloom 共用 · 未 AI 生成':''}</p>${allReused?`<p class="operator-muted">未生成品牌新背景；原计划 ${esc(generation?.provider_preview?.size||'品牌主图')} 新图未交付。原冻结翻译结果保留，无文字图片不需翻译。</p>`:''}<div class="operator-thumbnail-grid">${displayEntries.map((e,i)=>`<figure><a href="${esc(safeImage(e.image.url))}" target="_blank" rel="noopener" aria-label="查看本轮图片 ${i+1} 大图"><img src="${esc(safeImage(e.image.url))}" alt="本轮商品图片 ${i+1}" width="160" height="160"></a><figcaption>图片 ${i+1} · ${esc(sourceLabel(e))} · <a href="${esc(safeImage(e.image.url))}" target="_blank" rel="noopener">查看大图</a><small>${esc(roleLabel(e))} · <span data-image-dimensions>读取实际尺寸…</span></small></figcaption><details><summary>使用店铺与冻结顺序</summary>${e.routes.map(r=>`<p>${r.target_labels.map(targetText).map(esc).join('；')} · ${r.images.length} 张，按原顺序</p>`).join('')}</details></figure>`).join('')}</div><details><summary>图片来源与预算 fallback 记录</summary><p>原冻结 R1 的两品牌主图计划保留为历史；完整图库以本轮明确绑定的图片计划后继为准。当前 ${newUnique} 张独立生成图片，当前图库共 ${entries.size} 张独立图片。${allReused?'当前仍为原来源图复用；尚无本轮新图。':newUnique===entries.size?'新图已接入当前图库；旧复用素材仅为历史回退记录，不作为本轮生成成果。':'当前包含不同来源素材，以下逐张标明来源。'}独立图按实际 artifact 摘要去重，品牌路由条数不代表生成张数。本候选包含 ${newUnique} 张已完成的生成图片。末次任务记录新增生图调用 ${esc(generation?.external_generation_count??'未提供')} 次（零任务翻译可为 0）；累计付费请求尝试 ${esc(generation?.paid_requests?.attempted??'未提供')} 次，包含历史失败，图数与调用次数分别统计。</p><p>以下为当前 R2 的实际素材来源与付费记录，历史 fallback 不作为当前生图或最终发布批准。当前是否已经批准由原保护终审状态独立决定。</p>${json({assets:generation?.assets,provider_preview:generation?.provider_preview,paid_requests:generation?.paid_requests})}</details>`;
    document.querySelectorAll('#releaseCandidateImages img').forEach(img=>{const dimensions=()=>{const node=img.closest('figure').querySelector('[data-image-dimensions]');if(img.naturalWidth>0)node.textContent=`实际 ${img.naturalWidth} × ${img.naturalHeight}`;};img.onload=dimensions;if(img.complete)dimensions();});
    $('#releaseCandidateCopyTitle').textContent='各平台 / 品牌文案';
    const groups=manifest.copy_sets||[];
    if(!groups.some(g=>g.copy_set_id===selectedCopy))selectedCopy=groups[0]?.copy_set_id||'';
    const groupTitle=g=>`${[...new Set((g.target_labels||[]).map(label=>{const t=targetDisplay(label);return t.platform+' / '+t.shop;}))].join('、')} · ${languages[g.language]||g.language}`;
    $('#releaseCandidateCopy').innerHTML=`<label class="operator-copy-picker">查看冻结文案（共 ${groups.length} 组）<select id="operatorCopySelect">${groups.map(g=>`<option value="${esc(g.copy_set_id)}"${g.copy_set_id===selectedCopy?' selected':''}>${esc(groupTitle(g))}</option>`).join('')}</select></label>`+groups.map(g=>`<article class="release-review-copy-card" data-copy-set="${esc(g.copy_set_id)}"${g.copy_set_id===selectedCopy?'':' hidden'}><div class="release-review-card-head"><strong>${esc(groupTitle(g))}</strong></div><p class="operator-muted">使用店铺：${g.target_labels.map(targetText).map(esc).join('；')}</p><h6>${esc(g.title)}</h6><p>${esc(g.description)}</p>${Array.isArray(g.variants)?`<section class="target-variant-displays"><h6>逐店规格显示名</h6>${g.variants.map(v=>`<p>SKU ID (Model SKU) ${esc(v.model_sku)} · ${esc(v.display_name)}</p>`).join('')}</section>`:''}</article>`).join('');
    $('#operatorCopySelect').onchange=event=>{selectedCopy=event.target.value;document.querySelectorAll('#releaseCandidateCopy [data-copy-set]').forEach(card=>card.hidden=card.dataset.copySet!==selectedCopy);};
    $('#releaseCandidateTargetsTitle').textContent='15家店铺 · 类目与售价';
    $('#releaseCandidateTargets').innerHTML=`<div class="table-scroll"><table class="operator-target-table"><thead><tr><th>平台</th><th>店铺</th><th>国家</th><th>类目</th><th>SKU / 挂牌价</th><th>计算折后价</th><th>详情</th></tr></thead><tbody>${manifest.targets.map(t=>{const d=targetDisplay(t.target_label);return `<tr data-frozen-target="${esc(t.target_label)}"><td>${esc(d.platform)}</td><td>${esc(d.shop)}</td><td>${esc(d.country)}</td><td>${esc(t.category?.category_zh||t.category?.path||t.category?.name||t.category?.id||'未提供')}</td><td>${(t.prices||[]).map(p=>`${esc(p.model_sku)} · <strong>${esc(p.amount)} ${esc(p.currency)}</strong>`).join('<br>')}</td><td>${(t.prices||[]).map(p=>p.calculation?.result?.sale_after_discount!==undefined?`${esc(p.calculation.result.sale_after_discount)} ${esc(p.currency)}`:'未单列').join('<br>')}</td><td><details><summary>属性 / 依据</summary><p>语言 ${esc(t.locale)}</p>${view.attributes(t.category)}${(t.prices||[]).map(p=>`<p>${esc(p.calculation?.formula_zh||'原价格计算记录')}</p>${json(p)}`).join('')}${json(t)}</details></td></tr>`;}).join('')}</tbody></table></div>`;
    const actions=candidate.companion_actions?.actions||[],stock=actions.filter(a=>a.action_kind==='OZON_FBS_STOCK_CONVERGENCE'),discounts=actions.filter(a=>a.action_kind==='POSTPUBLISH_DIRECT_DISCOUNT'),other=actions.filter(a=>!['OZON_FBS_STOCK_CONVERGENCE','POSTPUBLISH_DIRECT_DISCOUNT'].includes(a.action_kind));
    sections.companions.innerHTML='<h5>批准范围内的伴随动作</h5>'+stock.map(a=>`<p><strong>Ozon 库存：</strong>发布后，每个 Model SKU ${esc(a.quantity_per_sku)} 件；按原规则核验唯一可用的非大件仓库，并以该仓库官方库存回读为完成依据。</p>`).join('')+(discounts.length?`<p><strong>发布后折扣：</strong>${discounts.length} 家店按原冻结价格参加唯一符合规则的进行中直接折扣活动；以活动商品官方回读为完成依据。</p><details><summary>查看全部折扣店铺与冻结折后价</summary><ul>${discounts.map(a=>{const target=manifest.targets.find(t=>t.target_label===a.prerequisite_target);return `<li>${esc(targetText(a.prerequisite_target))} · ${(target?.prices||[]).map(p=>`${esc(p.model_sku)}：${esc(p.calculation?.result?.sale_after_discount??'沿用原冻结定价政策')} ${esc(p.currency)}`).join('；')}</li>`;}).join('')}</ul></details>`:'')+other.map(a=>`<article><strong>${esc(a.action_kind)}</strong>${json(a)}</article>`).join('')+`<details><summary>完整伴随范围、执行边界与完成凭据</summary>${json(candidate.companion_actions)}</details>`;
    compactStagePresentation();
  }
  function safeImage(value){try{const u=new URL(value,location.origin);return u.origin===location.origin?u.href:u.protocol==='https:'?'/api/proxy-image?url='+encodeURIComponent(u.href):'';}catch{return '';}}
  function clear(){
    clearFrozenImages();
    data=null;stage=null;round='first';routedOffer=null;
    finalOnly=false;document.querySelectorAll('[data-original-round]').forEach(button=>button.hidden=false);
    document.body.classList.remove('operator-final-active');selectedCopy='';
    $('#publicationFlowTitle').textContent='当前发布阶段与审核';delete $('#publicationFlowStatus').dataset.originalStatus;delete $('#publicationFlowStatus').dataset.operatorMessage;
    document.body.classList.remove('original-review-active');$('#originalPublicationReview').hidden=true;
    for(const [id,mark] of homes){const node=$('#'+id)||document.querySelector('[data-compact-home="'+id+'"]');if(node&&mark.parentNode)mark.after(node);}homes.clear();
    $('#operatorProductSummary')?.remove();$('#operatorTechnicalEvidence')?.remove();
    ['firstReviewCandidateGrid','firstReviewAuditEvidence','firstReviewAuditDecisions','firstReviewAuditOperations','firstReviewWorkflowStages','firstReviewDecisionFacts','firstReviewDecisionRecommendations','firstReviewDecisionQuestions','releaseCandidateCopy','releaseCandidateVariants','releaseCandidateTargets','releaseCandidateImages','releaseCandidateWriteBudget','releaseCandidateIncidents','releaseCandidateCompanions','releaseCandidateChecks','originalBrandPlans'].forEach(id=>$('#'+id)?.replaceChildren());
  }
  document.addEventListener('DOMContentLoaded',()=>{
    const slot=$('#firstReviewImagePlanSlot');slot.innerHTML='<div id="originalBrandPlans"></div><p id="originalTranslationPlan"></p><div id="originalSourceImages"></div>';
    document.querySelectorAll('[data-original-round]').forEach(b=>b.onclick=()=>choose(b.dataset.originalRound));
    new MutationObserver(()=>{imageStatus();finalReview();}).observe($('#localizedImageResultsBadge'),{childList:true,subtree:true,characterData:true});
    new MutationObserver(syncActualStagePresentation).observe($('#publicationFlowReview'),{childList:true,subtree:true});
    new MutationObserver(syncActualStagePresentation).observe($('#connectedProductSelect'),{childList:true});
  });
  window.OrbitOriginalReview={render,clear,stages(value){
    if(!data){pendingStage=value;return;}
    if(String(value.offer_id)!==String(data.product.offer_id))return;stage=value;
    if(finalOnly){
      if(!actualFinalReady(value)){clearFrozenImages();return;}
      $('#originalPublicationReview').hidden=false;document.body.classList.add('original-review-active');
      document.querySelectorAll('[data-original-round]').forEach(button=>button.hidden=button.dataset.originalRound!=='final');
      const notice=$('#originalPublicationReview .central-review-notice');
      if(notice)notice.textContent='完整冻结候选在下方展示；唯一受保护终审保存本次最终决定。读取和核对不会发布商品。';
      move('publicationFlow','#originalStageLedger');move('publicationClosure','#originalStageLedger');
      finalReview();choose('final');
    }else finalReview();
    syncActualStagePresentation();
  }};
})();
