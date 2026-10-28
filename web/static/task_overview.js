"use strict";
(() => {
 const $=id=>document.getElementById(id), escape=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const kinds={publication:'商品上架',delisting:'商品下架',profit:'利润',replenishment:'补货',engineering:'工程',cleanup:'清理',creator:'达人',video:'视频',other:'其他工作'};
 let snapshot=null,records=[],selected=null,readFailed=false,previewObserver=null,feedback=null;
 const PRESENTATION={
  'product-4039931504':{title:'商品上架准备',next:'采用接收库修复，继续核定入档与类目。'},
  'product-engineering-continuation':{title:'商品准备工程',next:'将接收库修复交给原商品任务接续。'},
  'task-workbench-simplification':{title:'工作台首页',next:'修正首页展示，核查回归结果并准备正式采用。'},
  'supply-completion':{title:'四国补货计划',next:'接入已补齐的库存与订单，核对缺失商品资料。'},
  'exact-six-recycle':{title:'六个临时文件回收',next:'核查原任务的安全回收入口与结果。'},
  'creator-independent':{title:'达人邀约',next:'确认原系统的任务范围、账号与执行者。'},
  'video-independent':{title:'视频制作',next:'核对未确认结果，以及账号、预算与执行者。'},
  'platform-convergence':{title:'平台收敛',next:'保留交付与未确认结果记录。'}
 };
 const MAIN='product-4039931504',CHILD='product-engineering-continuation';
 function presentation(row){return PRESENTATION[row.work_id]||{title:row.title,next:row.blocker||'核对原任务证据，明确下一步。'};}
 function stageText(row){
  if(row.last_reported_state==='retained_checkpoint')return ({'exact-six-recycle':'回收未完成','creator-independent':'待交接','video-independent':'自动化已暂停','platform-convergence':'平台交付已完成'})[row.work_id]||row.stage;
  if(row.work_id==='task-workbench-simplification'&&row.last_reported_state==='independent_integration_delivered')return '候选可预览；正式未采用';
  return row.stage||'阶段待核实';
 }
 function businessRecords(){
  const main=records.find(r=>r.work_id===MAIN),child=records.find(r=>r.work_id===CHILD);
  const exact=r=>r?.products?.some(p=>productKey(p)==='common_collect_box_id:4039931504');
  const grouped=main&&child&&main.kind==='publication'&&child.kind==='engineering'&&main.handover==='accepted'&&child.handover==='accepted'&&main.source_status==='verified'&&child.source_status==='verified'&&exact(main)&&exact(child)
    &&main.source_session_id==='01a1066a-bdd4-77ad-b3d7-8750d0d08021'
    &&child.source_session_id==='01a101b6-0ab6-72cb-9a61-d557641ba023';
  return records.filter(r=>!grouped||r.work_id!==CHILD).map(r=>r.work_id===MAIN&&grouped?{...r,children:[child]}:r);
 }
 function actionText(row){
  if(row.user_decision&&row.source_status==='verified'&&!row.stale&&!readFailed)return '需要你'+(row.user_decision.kind==='final_review'?'审核':'决定')+'：'+row.user_decision.label;
  const checkpoint=['retained_checkpoint','independent_integration_delivered'].includes(row.last_reported_state);
  return '由我处理：'+(checkpoint?presentation(row).next:(row.blocker||presentation(row).next));
 }
 function feedbackText(){
  const age=feedback?.checked_at?(Date.now()-new Date(feedback.checked_at).getTime())/1000:Infinity;
  if(!feedback||age>20||feedback.state!=='observing')return '反馈观察已停止或未接入 · 当前执行状态未知';
  if(feedback.outcomes?.some(s=>s.status==='needs_verification'))return '部分反馈来源待核 · 每15秒刷新';
  if(feedback.missing_source_lanes?.length){
   const labels={product_business:'商品业务',engineering:'工程',supply:'补货'};
   const missing=feedback.missing_source_lanes.map(s=>labels[s]||'其他').join('、');
   return `${feedback.source_count||0}条反馈来源已登记 · ${missing}新回执待接入 · 每15秒刷新`;
  }
  return '商品、补货反馈已接入 · 每15秒刷新';
 }
 async function refreshFeedback(){
  try{const r=await fetch('/api/orbit/home/feedback',{cache:'no-store'});feedback=r.ok?await r.json():null;}catch{feedback=null;}
  if(snapshot){$('overviewConnection').textContent=(snapshot?.release?.environment==='preview'?'预览 · ':'')+feedbackText()+' · 页面读取 '+clock(snapshot.snapshot_read_at);}
 }
 refreshFeedback();setInterval(refreshFeedback,15000);
 const clock=v=>{if(!v)return '尚无观察时间';const d=new Date(v);return Number.isNaN(d.getTime())?'观察时间待核':d.toISOString().replace('T',' ').slice(0,19)+' UTC';};
 const localURL=v=>{if(typeof v!=='string'||!v.startsWith('/')||v.startsWith('//')||/\\|%2f|%5c|[\x00-\x20]/i.test(v))return null;return v;};
 const productKey=p=>`${p?.identity?.kind||''}:${p?.identity?.id||''}`;
 function productsForTask(task,registry){
  const desired=new Set(task.scope?.offer_id?[`${task.template==='publication'?'common_collect_box_id':'offer_id'}:${task.scope.offer_id}`]:(task.scope?.skus||[]).map(x=>`internal_sku:${x}`));
  const bound=[];
  for(const key of desired){
   const matches=registry.flatMap(row=>row.products||[]).filter(p=>productKey(p)===key);
   const signatures=new Set(matches.map(p=>JSON.stringify([p.name,p.name_verified,p.image_status,p.image_url])));
   if(matches.length&&signatures.size===1)bound.push(matches[0]);
   else {const cut=key.indexOf(':');bound.push({identity:{kind:key.slice(0,cut),id:key.slice(cut+1)},name:'商品名称待核实',name_verified:false,image_status:matches.length?'identity_conflict':'missing',image_url:null});}
  }
  return bound;
 }
 function normalized(data){
  const observations=Array.isArray(data.observations?.items)?data.observations.items:[];
  const claimed=new Set();
  const native=(data.tasks||[]).map(t=>{
   const state=t.execution_state||t.status,id=t.task_id||t.id;
   const candidates=observations.filter(row=>row.operations_task_id===id&&row.source_status==='verified');
   const linked=candidates.length===1?candidates[0]:null;if(linked)claimed.add(linked.work_id);
   const review=window.OrbitTaskWorkspace?.reviewLink?.({...t,id});
   const currentReview=state==='waiting_user'&&t.required_action?.receipt_binding?.adapter==='single-final-review/v1'&&review;
   const updated=t.external_task?.observed_at||t.updated_at;
   const old=!currentReview&&(t.external_task||state==='cancelled'||(Date.now()-new Date(updated).getTime()>7*86400000));
   return {work_id:linked?.work_id||'operations-'+id,kind:t.template||'other',title:linked?.title||t.title||'原任务记录',goal:linked?.goal||t.title||'目标未记录',stage:t.steps?.find(s=>s.key===t.current_step)?.label||'阶段待核实',
    handover:'accepted',last_reported_state:state,observed_at:updated,stale:!Number.isFinite(new Date(updated).getTime())||Date.now()-new Date(updated).getTime()>1800000,live_status:'unknown',source_status:'verified',owner_label:t.worker||t.owner||'原任务执行者',
    blocker:t.blocked_reason||t.pending_observation?.reason||'',closed:['completed','done','cancelled'].includes(state),historical:linked?false:!!old,
    result_url:localURL(t.result_url),user_decision:currentReview?{kind:'final_review',label:'查看最终审核候选',reason:t.required_action.reason||'完整冻结候选已绑定原审核入口',url:review.url}:null,
    products:productsForTask(t,observations),sources:linked?.sources||[],source_session_id:linked?.source_session_id||'',evidence_notes:linked?.evidence_notes||[],native_task_id:id,native:t};
  });
  return [...observations.filter(row=>!claimed.has(row.work_id)),...native];
 }
 function status(row){
  if(row.handover==='pending')return '待交接';
  if(row.handover==='user_owned')return '按既有决定由本人处理';
  if(row.source_status!=='verified')return '来源待核 · 当前状态未知';
  if(row.closed)return '已有交付记录 · 非实时';
  return row.stale?'证据已过期 · 执行未核实':'执行状态未核实';
 }
 function productMarkup(row){
  if(!row.products?.length)return ['publication','delisting'].includes(row.kind)?'<div class="work-products"><div class="work-product"><span class="work-image-missing" role="img" aria-label="主图缺失">主图缺失</span><div><strong>商品范围与名称待核实</strong><small>由 Agent 绑定原任务的准确商品资料</small></div></div></div>':'';
  return `<div class="work-products">${row.products.map(p=>{
   const image=p.image_status==='verified'&&/^\/api\/orbit\/home\/images\/[0-9a-f]{64}$/.test(p.image_url||'')?p.image_url:null;
   return `<div class="work-product" data-product-identity="${escape(productKey(p))}">${image?`<img src="${escape(image)}" alt="${escape(p.name)}主图" width="84" height="84">`:`<span class="work-image-missing" role="img" aria-label="主图缺失">主图缺失</span>`}<div><strong>${escape(p.name_verified?p.name:'商品名称待核实')}</strong>${!image?'<small>由 Agent 核对准确来源</small>':''}</div></div>`;
  }).join('')}</div>`;
 }
 function card(row,decision=false){
  const action=decision?row.user_decision:null,p=presentation(row);
  return `<article class="work-card${decision?' decision-card':''}" data-work-id="${escape(row.work_id)}"><div class="work-card-top"><span class="work-kind">${escape(kinds[row.kind]||'其他工作')}</span><span class="work-status">${escape(status(row))}</span></div>${productMarkup(row)}<h3>${escape(p.title||'已交接工作')}</h3><p class="work-stage"><span>状态</span>${escape(stageText(row))}</p><p class="work-owner"><span>负责人</span>我（协调与跟进）</p><p class="work-next${row.user_decision&&!row.stale&&!readFailed?' needs-user':''}">${escape(actionText(row))}</p><div class="work-card-foot"><small>证据更新 ${escape(clock(row.observed_at))}</small><div class="buttons">${action?.url&&localURL(action.url)?`<a class="action-link primary" href="${escape(action.url)}">${escape(action.label)}</a>`:''}<button type="button" data-work-detail="${escape(row.work_id)}">详情${row.children?.length?' · 工程进展':''}</button></div></div></article>`;
 }
 function decisionRows(){return businessRecords().filter(r=>!r.historical&&r.handover==='accepted'&&r.source_status==='verified'&&!r.stale&&r.user_decision&&!readFailed);}
 function render(){
  const decisions=decisionRows();$('decisionCount').textContent=decisions.length?`${decisions.length} 项`:'暂无';
  $('decisionCards').innerHTML=decisions.length?decisions.map(r=>`<div class="decision-line"><strong>${escape(r.products?.find(p=>p.name_verified)?.name||presentation(r).title)}</strong><span>${escape(actionText(r))}</span>${localURL(r.user_decision.url)?`<a class="action-link primary" href="${escape(r.user_decision.url)}">${r.user_decision.kind==='final_review'?'查看审核候选':'查看决定'}</a>`:`<button type="button" data-work-detail="${escape(r.work_id)}">查看依据</button>`}</div>`).join(''):readFailed?'<p class="empty">决定所需证据暂不可读；不能判断是否需要你的决定。</p>':'<p class="empty">暂无需要你决定的事项。</p>';
  const q=$('workSearch').value.trim().toLowerCase(),filter=$('workFilter').value;
  const visible=businessRecords().filter(r=>filter==='all'||(filter==='current'?!r.closed&&!r.historical:filter==='results'?r.closed:filter==='handover'?r.handover==='pending':true)).filter(r=>[r.title,r.goal,r.stage,...(r.products||[]).map(p=>p.name),...(r.products||[]).map(p=>p.identity?.id)].join(' ').toLowerCase().includes(q));
  const cardsHTML=visible.map(r=>card(r)).join('');if($('workCards')._paintedHTML!==cardsHTML){$('workCards').innerHTML=cardsHTML;$('workCards')._paintedHTML=cardsHTML;}$('workEmpty').hidden=visible.length>0;
  $('workEmpty').textContent=!snapshot||readFailed?'工作记录尚未完整读到，不能判断任务为空。':q?'没有符合搜索条件的工作。':filter==='results'?'当前没有已绑定的交付结果。':filter==='handover'?'当前没有待交接记录。':'当前筛选下没有工作记录；可查看“全部交接”。';
  const coverage=snapshot?.observations;
  const incomplete=coverage?.complete!==true;
  $('coverageNote').textContent=incomplete?'记录覆盖不完整，由我核查。':'技术进展与历史记录在详情。';
  $('coverageNote').classList.toggle('read-failed',incomplete);
  $('coverageNote').setAttribute('role','status');
  $('showFinalReviews').textContent=`最终审核${decisions.filter(x=>x.user_decision.kind==='final_review').length?' · '+decisions.filter(x=>x.user_decision.kind==='final_review').length:''}`;
  if(selected)renderDetail(selected);
 }
 function repairImages(){document.querySelectorAll('.work-product img').forEach(img=>img.addEventListener('error',()=>{const replacement=document.createElement('span');replacement.className='work-image-missing';replacement.setAttribute('role','img');replacement.setAttribute('aria-label','主图读取失败');replacement.textContent='主图读取失败';img.replaceWith(replacement);},{once:true}));}
 document.addEventListener('error',event=>{const img=event.target;if(!(img instanceof HTMLImageElement)||!img.closest('.work-product'))return;const replacement=document.createElement('span');replacement.className='work-image-missing';replacement.setAttribute('role','img');replacement.setAttribute('aria-label','主图读取失败');replacement.textContent='主图读取失败';img.replaceWith(replacement);},true);
 function renderDetail(id){
  const row=businessRecords().find(r=>r.work_id===id)||records.find(r=>r.work_id===id);
  if(!row){$('observationTitle').textContent='记录暂不可用';$('observationBody').textContent='所选记录已变化或暂未读到；不显示上一个商品的图片。';return;}
  $('observationTitle').textContent=presentation(row).title||'工作记录';
  const decisions=row.user_decision;
  const openSections=new Set([...$('observationBody').querySelectorAll('details[open]')].map(d=>d.querySelector('summary')?.textContent));
  $('observationBody').innerHTML=`${productMarkup(row)}<p class="work-status">${escape(status(row))}</p><dl class="observation-fields"><dt>目标</dt><dd>${escape(row.goal)}</dd><dt>实际阶段</dt><dd>${escape(row.stage)}</dd><dt>最近证据／观察</dt><dd>${escape(clock(row.observed_at))}</dd><dt>当前实时状态</dt><dd>未核实。文件存在、刷新成功及记录状态不能证明正在运行。</dd><dt>负责人／边界</dt><dd>${escape('我（协调与跟进）；原执行者：'+(row.owner_label||'待确认'))} · ${escape(row.handover==='pending'?'尚未接管':row.handover==='user_owned'?'沿用本人处理的决定':'Agent 负责既有授权内跟进')}</dd><dt>下一步</dt><dd>${escape(actionText(row))}</dd></dl>${decisions&&!readFailed?`<section class="decision-detail"><h3>${escape(decisions.label)}</h3><p>${escape(decisions.reason)}</p>${localURL(decisions.url)?`<a class="action-link primary" href="${escape(decisions.url)}">前往原决定入口</a>`:''}</section>`:''}<div class="buttons">${localURL(row.result_url)?`<a class="action-link" href="${escape(row.result_url)}">查看已有结果</a>`:''}${row.native_task_id?`<button type="button" data-native-detail="${escape(row.native_task_id)}">查看原任务详细记录</button>`:''}</div><details class="observation-evidence" data-child-progress><summary>工程子任务与历史</summary>${(row.children||[]).map(c=>`<section><h3>${escape(presentation(c).title)}</h3><p>状态：${escape(c.stage)}<br>下一步：${escape(actionText(c))}<br>证据更新：${escape(clock(c.observed_at))}</p><p>原任务 ${escape(c.source_session_id)} · ${escape(c.work_id)}</p>${(c.evidence_notes||[]).map(n=>`<p>${escape(n)}</p>`).join('')}</section>`).join('')||'<p>无已绑定工程子任务。</p>'}${records.filter(h=>h.historical||h.closed).map(h=>`<p>${escape(h.title)} · ${escape(h.stage)} · ${escape(clock(h.observed_at))}</p>`).join('')}<p>换品历史沿用原任务身份；本次没有新建或取消任务。</p></details><details class="observation-evidence"><summary>反馈更新方式</summary><p>${escape(feedbackText())}。原任务回执变化后，观察器每5秒核验，再经单一写入者的带时间更新命令接入。页面每15秒读一次；无变化不延后业务证据时间。</p><p>自动源：商品接收库回执、供应链交接回执。工程回执同时保留在子任务。其他任务须由协调者核验反馈后接入；没有全会话自动事件。</p><p>最后观察器检查：${escape(clock(feedback?.checked_at))}。这是观察器心跳；各任务执行状态仍以原系统证据为准。</p></details><details class="observation-evidence"><summary>身份与来源证据</summary><p>工作记录：${escape(row.work_id)}</p>${row.source_session_id?`<p>原会话：${escape(row.source_session_id)}；此记录不提供向会话追加消息的能力。</p>`:''}${(row.products||[]).map(p=>`<p>${escape(p.name)} · ${escape(p.identity.kind)} ${escape(p.identity.id)} · 图片 ${escape(p.image_status)}</p>`).join('')}${(row.evidence_notes||[]).map(note=>`<p>${escape(note)}</p>`).join('')}${(row.sources||[]).map(s=>`<p>${escape(s.label)}<br><code>${escape(s.sha256)}</code></p>`).join('')||'<p>详细证据尚未绑定。</p>'}</details>`;
  $('observationBody').querySelectorAll('details').forEach(d=>{d.open=openSections.has(d.querySelector('summary')?.textContent);});repairImages();
 }
 function select(id,push=true){selected=id;renderDetail(id);if(!$('observationDialog').open)$('observationDialog').showModal();if(push){const url=new URL(location.href);url.searchParams.set('work',id);history.pushState({work:id},'',url);}}
 function close(push=true){selected=null;if($('observationDialog').open)$('observationDialog').close();if(push){const u=new URL(location.href);u.searchParams.delete('work');history.pushState({},'',u);}}
 function restore(){const id=new URL(location.href).searchParams.get('work');if(id&&snapshot)select(id,false);else if(!id)close(false);}
 function lockPreview(){if(snapshot?.release?.environment!=='preview')return;const lock=()=>document.querySelectorAll('#submitTask,#detailBody button,#detailBody input,#detailBody textarea').forEach(el=>{if(!el.disabled){el.disabled=true;el.title='候选预览不能保存或执行';}});lock();if(!previewObserver){previewObserver=new MutationObserver(lock);previewObserver.observe($('detailBody'),{subtree:true,childList:true});}}
 window.addEventListener('orbit:task-snapshot',event=>{
  const next=event.detail,previous=snapshot;
  readFailed=next.observations?.status!=='AVAILABLE'||next.observations?.complete!==true||next.task_snapshot_status==='UNAVAILABLE';
  if(next.task_snapshot_status==='UNAVAILABLE')next.tasks=(previous?.tasks||[]);
  if(next.observations?.status!=='AVAILABLE')next.observations={...next.observations,items:(previous?.observations?.items||[]).map(row=>({...row,historical:row.historical||row.closed,source_status:'unavailable',user_decision:null,result_url:null,closed:false,products:(row.products||[]).map(p=>({...p,name_verified:false,image_url:null,image_status:'unavailable'}))}))};
  else if(next.observations.missing_session_ids?.length){
   const ids=new Set(next.observations.items.map(row=>row.work_id));
   const missing=new Set(next.observations.missing_session_ids);
   const retained=(previous?.observations?.items||[]).filter(row=>missing.has(row.source_session_id)&&!ids.has(row.work_id)).map(row=>({...row,source_status:'not_bound',stale:true,user_decision:null,result_url:null,closed:false,products:(row.products||[]).map(p=>({...p,name:'商品名称待核实',name_verified:false,image_url:null,image_status:'unavailable'}))}));
   next.observations={...next.observations,items:[...next.observations.items,...retained]};
  }
  snapshot=next;records=normalized(snapshot);
  $('overviewConnection').textContent=(snapshot?.release?.environment==='preview'?'预览 · ':'')+feedbackText()+' · 页面读取 '+clock(snapshot.snapshot_read_at);
  if(readFailed){$('overviewConnection').textContent+=' 部分来源暂不可读；保留上次记录，不能判断为空。';$('overviewConnection').classList.add('read-failed');}else $('overviewConnection').classList.remove('read-failed');render();repairImages();restore();lockPreview();
 });
 window.addEventListener('orbit:snapshot-error',event=>{readFailed=true;$('overviewConnection').textContent=`记录读取失败：${event.detail.message}。保留上次记录，当前状态未核实。`;$('overviewConnection').classList.add('read-failed');render();repairImages();});
 document.addEventListener('click',e=>{const row=e.target.closest('[data-work-detail]'),native=e.target.closest('[data-native-detail]');if(row)select(row.dataset.workDetail);if(native){close();window.OrbitTaskWorkspace?.openDetail(native.dataset.nativeDetail);}});
 window.OrbitTaskOverview={productMarkupForTask:t=>productMarkup({kind:t.template,products:productsForTask(t,snapshot?.observations?.items||[])})};
 $('closeObservation').onclick=()=>close();$('observationDialog').addEventListener('cancel',e=>{e.preventDefault();close();});
 window.addEventListener('popstate',restore);window.addEventListener('pageshow',restore);
 $('workSearch').addEventListener('input',()=>{render();repairImages();});$('workFilter').addEventListener('change',()=>{sessionStorage.setItem('orbit-work-filter',$('workFilter').value);render();repairImages();});
 const saved=sessionStorage.getItem('orbit-work-filter');if(['current','all','results','handover'].includes(saved))$('workFilter').value=saved;
 $('continueWork').onclick=()=>{$('workFilter').value='current';render();repairImages();$('workHeading').scrollIntoView({behavior:'smooth',block:'start'});};
 $('showFinalReviews').onclick=()=>{$('decisionHeading').scrollIntoView({behavior:'smooth',block:'start'});};
})();
