/* Presentation only: existing forms and consumers retain mutation authority. */
(() => {
 'use strict';
 const $=s=>document.querySelector(s),esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let data=null,offer=null,initialized=false;
 const preparationRangeLabel=labels=>`${labels.includes('miaoshou:COMMON')?'COMMON＋':''}${labels.filter(label=>label!=='miaoshou:COMMON').length} 个平台目标`;
 function fold(node,label,id){
  if(!node||node.parentElement.id===id)return;
  const details=document.createElement('details');details.id=id;details.className='review-fold';
  const summary=document.createElement('summary');summary.textContent=label;details.append(summary);
  node.before(details);details.append(node);return details;
 }
 function compactSpecs(){
  const grid=$('#productSpecGrid');if(!grid||grid.querySelector('table'))return;
  const options=[...grid.querySelectorAll('.source-spec-option')];if(!options.length)return;
  const table=document.createElement('table');table.className='review-table sku-review-table';
  table.innerHTML='<thead><tr><th>保留 / 来源规格</th><th>发布名称</th><th>成本 CNY</th><th>重量 kg</th><th>长 cm</th><th>宽 cm</th><th>高 cm</th></tr></thead><tbody></tbody>';
  options.forEach(option=>{
   const row=document.createElement('tr');row.className='source-spec-option';
   [option.querySelector('.source-spec-selector'),option.querySelector('.source-spec-name'),...option.querySelectorAll('.sku-commercial-editor label')].forEach(node=>{const cell=document.createElement('td');cell.append(node);row.append(cell);});
   table.tBodies[0].append(row);
  });
  grid.replaceChildren(table);grid.classList.add('table-scroll');
  $('#specSelectionCount').textContent=`已选 ${grid.querySelectorAll('input:checked').length} 个规格 · 保存仅更新本商品事实`;
 }
 function targetTable(){
  const grid=$('#selectedChannelPriceGrid');if(!grid||!data)return;
  const labels=data.publication_scope?.selected_labels||[],prices=data.pricing_review?.target_pricing||{};
  const metadata=new Map((data.publication_scope?.available_targets||[]).map(r=>[r.label,r]));
  if(!labels.length)return;
  const table=document.createElement('div');table.className='table-scroll';
  table.innerHTML=`<table class="review-table target-review-table"><thead><tr><th>目标 / 店铺</th><th>SKU / 售价与币种</th><th>当前条件</th><th>完整详情</th></tr></thead><tbody>${labels.map(label=>{
   const p=prices[label]||{},meta=metadata.get(label)||{},rows=p.sku_prices||p.store_prices||[],derived=p.derived_preview||{};
   const amount=rows.map(r=>`${r.model_sku||r.shop||r.target_key||'目标价'} · ${r.list_price??r.amount??r.global_original_price_cny??'待补'} ${r.currency||'币种待核'}`).join('；')||
    (derived.global_original_price_cny!=null?`${derived.global_original_price_cny} CNY`:derived.price_cny!=null?`${derived.price_cny} CNY / 划线 ${derived.old_price_cny??'待补'} CNY`:'尚无可用价格');
   return `<tr><td><strong>${esc(label)}</strong><small>${esc(meta.shop||meta.country||meta.site||'身份待核')}</small></td><td>${esc(amount)}</td><td>${p.status==='ready'?'可审查':p.status==='awaiting_tiktok_readback'?'等待主商品回读':'待补全价格依据'}</td><td><button type="button" data-target-detail="${esc(label)}">查看</button></td></tr>`;
  }).join('')}</tbody></table>`;
  grid.replaceChildren(table);
  grid.querySelectorAll('button').forEach(button=>button.onclick=()=>showDetails('店铺、仓库、SKU 与价格依据',{
   target:metadata.get(button.dataset.targetDetail),pricing:prices[button.dataset.targetDetail],
   warehouse_inventory:data.warehouse_inventory_by_target?.[button.dataset.targetDetail]??'当前接口未提供独立仓库库存事实',
  }));
 }
 function showDetails(title,value){$('#reviewDetailTitle').textContent=title;$('#reviewDetailText').textContent=JSON.stringify(value,null,2);$('#reviewCopyStatus').textContent='';$('#reviewDetailDialog').showModal();}
 function render(value){
   data=value;
   // Dashboard data can arrive from the base workspace before this enhancer's
   // DOMContentLoaded callback has created its disclosure and summary nodes.
   // Retain that latest state and render it once initialization is complete.
   if(!initialized)return;
   const p=value.product||{},selected=value.publication_scope?.selected_labels||[];
  if(offer!==String(p.offer_id)){offer=String(p.offer_id);$('#queueDisclosure').open=false;}
  $('#currentReviewSummary').innerHTML=`<div><span>当前审核版本</span><strong>${esc(p.revision??'未知')} · ${p.actual_product_approved?'事实已批准':'事实待批准'}</strong></div><div><span>当前准备范围</span><strong>${esc(preparationRangeLabel(selected))}</strong></div><div><span>所选规格</span><strong>${(p.selected_sku_keys||[]).length} 个 SKU</strong></div><div><span>图片决定</span><strong id="reviewImageCount">读取中</strong></div><button type="button" id="inspectCurrentReview">版本与来源详情</button>`;
  $('#inspectCurrentReview').onclick=()=>showDetails('当前审核版本与来源', {product:p,scope:value.publication_scope,blockers:value.release_v1?.blockers});
  compactSpecs();targetTable();imageCount();frozenReview();
 }
 function imageCount(){
  const source=$('#embeddedImageReviewBadge')?.textContent;
  const candidate=data?.r2_candidate_review;
  if($('#reviewImageCount'))$('#reviewImageCount').textContent=candidate
   ? `本轮候选 ${$('#localizedImageResultsBadge')?.textContent||candidate.keep_count+'/'+candidate.images.length+' 张保留'}；来源 ${source||'读取中'}`
   : source||'尚未读取';
 }
 function frozenReview(){
  const frozen=data?.frozen_first_review,active=Boolean(data?.frozen_review_projection&&frozen);
  const labels={
   '#productFactsPanel h2':'已冻结商品事实',
   '#productFactsPanel .panel-heading h2 + p':'核对已批准的商品身份、规格、成本与包装事实。当前版本已锁定。',
   '.facts-edit-title > span':'已冻结来源商品标题',
   '#factsEditSellerSkuNote':'冻结快照中的 Seller SKU；当前库存与目录占用需另行核对。',
   '#embeddedImageReviewTitle':'来源图片 · 冻结参考',
   '#publicationScopeTitle + p':'核对首轮已冻结的平台与国家；本轮图片审核沿用该范围。'};
  Object.entries(labels).forEach(([selector,text])=>{const node=$(selector);if(!node)return;node.dataset.originalReviewLabel??=node.textContent;node.textContent=active?text:node.dataset.originalReviewLabel;});
  let copy=$('#frozenCopyReview'),masters=$('#frozenMasterReview');
  if(!copy){copy=document.createElement('section');copy.id='frozenCopyReview';copy.className='panel';$('#pane-images').prepend(copy);}
  if(!masters){masters=document.createElement('section');masters.id='frozenMasterReview';masters.className='panel';$('#localizedImageResults').before(masters);}
  copy.hidden=masters.hidden=!active;
  $('#titleTools').hidden=active;
  if(!active)return;
  copy.innerHTML=`<h2>已冻结商品文案</h2><p>Offer ${esc(frozen.offer_id)} · 审核版本 ${esc(frozen.approved_revision)}。以下文案来自已批准首轮，当前只读。</p>`+
   (frozen.copy_review_sets||[]).map(g=>`<article><h3>${esc(g.label)}</h3><strong>${esc(g.title_en)}</strong><p>${esc(g.title_zh)}</p><p>${esc(g.description_en)}</p><p>${esc(g.description_zh)}</p><p>${esc(g.specification_name_en)} · ${esc((g.specification_values||[]).join(' / '))}</p></article>`).join('')+
   `<h3>各目标标题与描述 · ${esc(preparationRangeLabel((frozen.targets||[]).map(t=>t.target)))}</h3>`+(frozen.targets||[]).map(t=>`<details data-frozen-copy-target="${esc(t.target)}"><summary>${esc(t.target)} · ${esc(t.copy?.language||'语言待核')} · ${esc(t.copy?.title||'标题资料缺失')}</summary><p>${esc(t.copy?.description||'描述资料缺失')}</p></details>`).join('');
  const roles={cover_scene:'主图场景',installed_detail:'安装细节',scene_1:'场景 1',scene_2:'场景 2',scene_3:'场景 3',assembled_size:'组合尺寸',piece_layout_or_instructions:'片数与说明'};
  masters.innerHTML=`<h2>已生成品牌母图 · ${(data.frozen_master_images||[]).length} 张</h2><p>仅展示已记录的生成结果，不计为本轮已保留图片；本地化候选的明确决定见下方。</p><div class="frozen-master-grid">`+(data.frozen_master_images||[]).map(r=>`<figure><a href="${esc(r.local_url)}" target="_blank" rel="noopener"><img src="${esc(r.local_url)}" alt="母图 ${esc(r.review_number)} · ${esc(r.brand_label)}" loading="lazy"></a><figcaption>${esc(r.review_number)} · ${esc(r.brand_label)} · ${esc(roles[r.role]||r.role)}<span class="master-load-status"> · 图片读取中</span></figcaption></figure>`).join('')+'</div>';
  masters.querySelectorAll('img').forEach(img=>{const status=img.closest('figure').querySelector('.master-load-status');img.onload=()=>status.textContent=' · 已加载，供核对';img.onerror=()=>status.textContent=' · 图片未连接，需恢复原文件';});
  $('#publicationTargetGrid').querySelectorAll('input').forEach(input=>input.disabled=true);
  ['selectAllTargetsButton','restoreTargetDefaultsButton','applyPublicationScopeButton'].forEach(id=>{if($('#'+id))$('#'+id).disabled=true;});
  $('#publicationScopeNote').textContent=`已冻结 ${preparationRangeLabel(frozen.targets.map(t=>t.target))}；当前审核沿用该范围。`;
 }
 function clear(){data=null;offer=null;$('#currentReviewSummary')?.replaceChildren();['frozenCopyReview','frozenMasterReview'].forEach(id=>{if($('#'+id)){$('#'+id).hidden=true;$('#'+id).replaceChildren();}});}
 document.addEventListener('DOMContentLoaded',()=>{
  const disclosure=document.createElement('details');disclosure.id='queueDisclosure';disclosure.open=true;disclosure.innerHTML='<summary>商品队列与录入 · 展开查找、筛选和批量只读操作</summary>';
  const hero=$('.hero'),queue=$('.queue-section');hero.before(disclosure);disclosure.append(hero,queue);
  $('.queue-jump').onclick=event=>{event.preventDefault();disclosure.open=true;disclosure.scrollIntoView({block:'start'});};
  $('.topbar nav').querySelectorAll('a[href^="#"]').forEach(link=>link.remove());
  const summary=document.createElement('section');summary.id='currentReviewSummary';summary.className='current-review-summary';$('.status-hero').after(summary);
  const next=$('.next-panel');next.classList.add('compact-next');$('.product-tabs').before(next);
  fold($('#blockerList'),'全部阻塞原因与原始诊断','reviewBlockers');
  fold($('#productFacts'),'来源、身份与采集依据','factsSourceDetails');
  fold($('#listingCopyAssistant'),'标题候选与生成工具','titleTools');
  $('#factsSourceDetails').append($('#roundEvidence'),$('#factsNotice'));
  $('.facts-edit-actions').before($('#titleTools'));
  fold($('#approvalFacts'),'核对全部批准字段','approvalFactsDetails');
  fold($('#storePriceGrid'),'全部历史店铺价与费用公式','allPriceDetails');
  fold($('#channelBlockers'),'店铺预检阻塞 · 查看完整原因','channelBlockerDetails');
  // The approved release controls are the current execution surface. Keep the
  // whole release plan visible on its dedicated tab; only its page-level
  // approval form is hidden by the base review markup.
  const approval=fold($('#approval'),'当前第一轮审批 · 核对与锁定版本','currentApprovalDetails');
  $('#pane-release').prepend(approval);
  const dialog=document.createElement('dialog');dialog.id='reviewDetailDialog';dialog.innerHTML='<div class="review-detail-heading"><h2 id="reviewDetailTitle">审核详情</h2><button type="button" id="closeReviewDetail">返回审核</button></div><button type="button" id="copyReviewDetail">复制完整详情</button><p id="reviewCopyStatus" role="status"></p><pre id="reviewDetailText"></pre>';
  document.body.append(dialog);$('#closeReviewDetail').onclick=()=>dialog.close();
  $('#copyReviewDetail').onclick=async()=>{try{await navigator.clipboard.writeText($('#reviewDetailText').textContent);$('#reviewCopyStatus').textContent='已复制';}catch(e){$('#reviewCopyStatus').textContent='复制未完成，可选择下方全文复制。';}};
  const count=document.createElement('p');count.id='specSelectionCount';count.setAttribute('role','status');$('#productSpecGrid').before(count);
  $('#productSpecGrid').addEventListener('change',()=>{count.textContent=`已选 ${$('#productSpecGrid').querySelectorAll('input:checked').length} 个规格 · 保存仅更新本商品事实`;});
  new MutationObserver(imageCount).observe($('#embeddedImageReviewBadge'),{childList:true,subtree:true,characterData:true});
  new MutationObserver(imageCount).observe($('#localizedImageResultsBadge'),{childList:true,subtree:true,characterData:true});
  new MutationObserver(compactSpecs).observe($('#productSpecGrid'),{childList:true});
  initialized=true;
  if(data)render(data);
 });
 window.OrbitDenseReview={render,compactSpecs,showDetails,clear};
})();
