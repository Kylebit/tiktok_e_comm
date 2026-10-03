"use strict";
(() => {
  const $ = id => document.getElementById(id), esc = x => String(x ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const eventLabels={workflow_created:'任务已创建',claimed:'执行者已接手',checkpoint_saved:'进度已保存',lease_expired:'执行连接中断，已安排恢复',user_action_required:'需要你处理',domain_receipt_accepted:'业务审核已通过',input_provided:'资料已补充',step_completed:'步骤已完成',execution_failed:'执行遇到异常',external_action_started:'开始执行平台操作',external_reconciled:'外部结果已核实',external_task_observed:'已记录原任务历史观测',scope_bound:'任务范围已核实','user_retry':'任务已重新排队','user_cancel':'任务已取消','user_provide-input':'任务已恢复排队'};
  const types = {publication:'商品上架',delisting:'商品下架',profit:'利润统计'};
  const labels = {queued:'待执行',running:'执行中',waiting_user:'待我处理',failed:'执行失败',reconciliation_required:'核对执行结果',waiting_domain:'等待平台结果',completed:'已完成',cancelled:'已取消',executor_offline:'等待执行器接入',external_task:'历史观测'};
  const normalize = t => ({...t,id:t.task_id||t.id});
  let tasks = [], current = null, createKey = null, refreshBusy = false, paintedSignature = null, activeRelease = null, runtimeStatus = null;
  let detailId = null, detailGeneration = 0, detailSignature = null, detailLoading = false, detailRefreshBusy = false, retiredDrafts = [];
  const pendingMutations = new Map();
  const lines = s => [...new Set(s.split(/[\n,，]+/).map(v=>v.trim()).filter(Boolean))];
  const previousMonth = () => {const d=new Date(),m=d.getMonth();return `${m===0?d.getFullYear()-1:d.getFullYear()}-${String(m===0?12:m).padStart(2,'0')}`;};
  const state = t => t.execution_state || ({todo:'queued',in_progress:'running',waiting_approval:'waiting_user',blocked:'failed',done:'completed',cancelled:'cancelled'}[t.status] || 'queued');
  const profitUnknown = t => t.template==='profit' && t.current_step==='coverage' && !!t.profit_unknown_readback;
  function renderDomainOperations(data){
    const guard=data.domain_guard||{}, rows=Array.isArray(data.domain_operations)?data.domain_operations:[];
    const count=Number(guard.unresolved_operation_count)||0, section=$('domainOperations');
    section.hidden=count===0;
    if(!count)return;
    $('domainOperationsTitle').textContent=`历史业务操作待回读 · ${count} 条`;
    $('domainOperationsList').innerHTML=rows.length
      ?rows.map(row=>{
        const targets=Array.isArray(row.affected_targets)?row.affected_targets:[];
        const affected=targets.length?targets.map(item=>`SKU ${esc(item.sku)} · ${esc(item.target)}`).join('；'):'具体 SKU / 店铺仍待核实';
        return `<p><strong>${esc(row.operation_id||'未知操作')}</strong> · ${esc(row.state||'状态未知')} · ${esc(row.owner_task_id?'关联任务 '+row.owner_task_id:'未关联当前任务')} · ${esc(row.locked_resource_count??0)}/${esc(row.resource_count??'?')} 个资源仍锁定 · ${esc(stamp(row.created_at))}<br><small>受影响：${affected}${row.affected_targets_truncated?'；其他目标未在首页展开':''}${row.unparsed_resource_count?'；另有 '+esc(row.unparsed_resource_count)+' 项资源身份待核实':''}。来源与远端结果均待核实，不能据此重试或解锁。</small></p>`;
      }).join('')
      :'<p>操作清单暂不可读；保留锁，等待只读核查。</p>';
    if(data.domain_operations_truncated)$('domainOperationsList').insertAdjacentHTML('beforeend','<p>仅显示前 20 条，其他操作仍保持锁定。</p>');
  }
  const externalAttention = t => !!t.external_task && (t.checkpoint?.business_status === 'needs_review' || /\bneeds_review\b/.test(t.external_task.observed_status||''));
  const observed = t => t.external_task ? `${t.external_task.observed_status||'尚无业务状态记录'} · 观测于 ${stamp(t.external_task.observed_at)}（历史观测，非实时）` : '';
  function renderPendingChecks(data){
    const operations=Array.isArray(data.domain_operations)?data.domain_operations:[];
    const failed=tasks.filter(t=>isCurrent(t)&&['failed','reconciliation_required'].includes(state(t)));
    const gaps=tasks.filter(t=>isCurrent(t)&&(externalAttention(t)||profitUnknown(t)
      ||(t.template==='publication'&&t.current_step==='release'&&!finalReviewReady(t))));
    const count=operations.length+failed.length+gaps.length,section=$('pendingChecks');
    section.hidden=count===0;$('pendingChecksCount').textContent=String(count);
    const card=(title,body,actions)=>`<div class="pending-item"><div><strong>${title}</strong>${body}</div><div class="buttons">${actions}</div></div>`;
    const detailButton=id=>`<button type="button" data-detail="${esc(id)}">查看任务详情</button>`;
    const rawLink='<a class="action-link" href="/api/orbit/tasks" target="_blank" rel="noopener">读取本地任务账本</a>';
    const operationRows=operations.map(row=>{
      const linked=tasks.find(t=>t.id===row.owner_task_id);
      const active=linked&&isCurrent(linked)?linked:null;
      const association=active?`${active.title||'未命名'}（${active.id}）`:
        linked?`${linked.id}（已取消历史，仅作关联）`:
        row.owner_task_id?`${row.owner_task_id}（当前列表未找到）`:'未关联当前任务';
      const responsible=active?.owner||active?.worker||'未登记';
      const previous=active?.last_executor?`；上次执行者：${esc(active.last_executor)}（历史）`:'';
      const targets=Array.isArray(row.affected_targets)?row.affected_targets:[];
      const target=targets.length?targets.map(item=>`SKU ${esc(item.sku)} · ${esc(item.target)}`).join('；'):'具体 SKU / 店铺未在首页登记';
      return card(`操作 ${esc(row.operation_id||'身份未登记')}`,
        `<p>目标：${target}${row.affected_targets_truncated?'；另有目标未展开':''}。关联任务：${esc(association)}；当前负责人：${esc(responsible)}${previous}。</p><p class="sub">本地状态 ${esc(row.state||'未知')} · ${esc(stamp(row.created_at))} · ${esc(row.locked_resource_count??0)} 个锁；首页账本未提供官方回读凭据。下一步只读核查原操作与目标的官方状态，不能据此重试或解锁。</p>`,
        `${active?detailButton(active.id):''}${rawLink}`);
    });
    if(data.domain_operations_truncated)operationRows.push('<p class="sub">仅展示前 20 条操作；其余仍保持待核与锁定。</p>');
    const failedRows=failed.map(t=>card(`任务 ${esc(t.id)} · ${esc(t.title||'未命名')}`,
      `<p>范围：${esc(compactScope(t))}；当前 ${esc(labels[state(t)]||state(t))}。当前负责人：${esc(t.owner||t.worker||'未登记')}${t.last_executor?`；上次执行者：${esc(t.last_executor)}（历史）`:''}。</p><p class="sub">已有证据：任务记录 ${esc(stamp(t.updated_at))}；失败线索 ${esc(t.blocked_reason||t.pending_observation?.reason||'未登记具体原因')}。下一步只读查看任务事件与现有证据。</p>`,detailButton(t.id)));
    const gapRows=gaps.map(t=>{
      const publication=t.template==='publication',reason=publication?'冻结终审候选、妙手 COMMON 官方回读和完整目标证明尚未绑定到本任务':profitUnknown(t)?'原利润尝试结果尚未独立核实':'历史观测标记 needs_review，当前来源资料待核';
      const observedAt=t.external_task?.observed_at||t.updated_at;
      return card(`资料缺口 · ${esc(t.id)} · ${esc(t.title||'未命名')}`,
        `<p>目标：${esc(compactScope(t))}。当前负责人：${esc(t.owner||t.worker||'未登记')}${t.external_task?.owner?`；历史观测来源：${esc(t.external_task.owner)}（旧记录）`:t.last_executor?`；上次执行者：${esc(t.last_executor)}（历史）`:''}。</p><p class="sub">缺少：${esc(reason)}。已有证据：${esc(stamp(observedAt))} 的任务/历史记录；下一步只读核查详情，不自动接续。</p>`,detailButton(t.id));
    });
    const group=(title,rows)=>rows.length?`<div class="pending-group"><h3>${title} · ${rows.length}</h3>${rows.join('')}</div>`:'';
    $('pendingChecksList').innerHTML=group('远端结果待回读',operationRows)+group('执行异常待核',failedRows)+group('资料缺口待补证',gapRows);
  }
  const unknownHint = t => profitUnknown(t)?'原尝试未经核实，不可自动重跑；打开详情查看只读线索。':'';
  const group = t => t.template==='publication'&&state(t)!=='cancelled'&&t.current_step==='release'&&!finalReviewReady(t)?'problem':t.external_task?'external':profitUnknown(t)?'problem':({running:'running',waiting_domain:'waiting',waiting_user:'attention',failed:'problem',reconciliation_required:'problem',completed:'completed',cancelled:'cancelled',queued:'queued',executor_offline:'queued'}[state(t)] || 'other');
  const isCurrent = t => state(t) !== 'cancelled';
  const canResume = t => t.allowed_actions?.retry===true;
  const stamp = v => {if(!v)return '尚无执行记录';const d=new Date(v);return isNaN(d)?String(v):d.toLocaleString('zh-CN',{hour12:false});};
  const badge = t => `<span class="badge ${group(t)}">${esc(profitUnknown(t)?'利润原会话待核对':state(t)==='queued'&&!runtimeStatus?'已登记，执行状态待核实':state(t)==='queued'&&runtimeStatus&&(t.executor_connected!==true||(t.template==='profit'&&!ownedProfitRunning(t)))?'已登记，未接续':(labels[state(t)] || state(t)))}</span>`;
  const scope = t => {const s=t.scope||{};return [s.offer_id&&`采集箱 ${s.offer_id}`,s.month,s.skus?.length&&`SKU ${s.skus.join('、')}`,s.shops?.length&&s.shops.join('、')].filter(Boolean).join(' · ')||'范围待核实';};
  const compactScope = t => {const s=t.scope||{},shops=s.shops||[];return [s.offer_id&&`采集箱 ${s.offer_id}`,s.month,s.skus?.length&&`SKU ${s.skus.join('、')}`,shops.length&&(shops.length<=2?shops.join('、'):`${shops.slice(0,2).join('、')} 等${shops.length}个目标`)].filter(Boolean).join(' · ')||'范围待核实';};
  // Review and result navigation only use local, same-origin application routes.
  function localURL(value) {if(!value||typeof value!=='string')return null;try{const u=new URL(value,location.origin);return u.origin===location.origin&&/^https?:$/.test(u.protocol)?u.pathname+u.search+u.hash:null;}catch{return null;}}
  async function request(path,body,base='/api/orbit/tasks'){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);try{const response=await fetch(base+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',signal:controller.signal});let data;try{data=await response.json();}catch{throw Error('任务服务返回了无法读取的内容');}if(!response.ok||data.ok===false)throw Error(data.message||data.error?.message||data.error||`请求失败（${response.status}）`);return data;}finally{clearTimeout(timer);}}
  const scopedPreparationRunning = () => runtimeStatus?.new_task_preparation?.scope==='EXPLICIT_NEW_POST_PUBLICATION_ONLY'
    && runtimeStatus.new_task_preparation.running===true && runtimeStatus.new_task_preparation.state==='polling';
  const profitReadonlyRunning = () => runtimeStatus?.new_task_preparation?.profit_scope==='EXPLICIT_NEW_POST_PROFIT_READONLY'
    && runtimeStatus.new_task_preparation.running===true && runtimeStatus.new_task_preparation.state==='polling'
    && runtimeStatus.new_task_preparation.profit_executor_connected===true
    && runtimeStatus.capabilities?.profit?.scope==='EXPLICIT_NEW_POST_PROFIT_READONLY'
    && runtimeStatus.capabilities.profit.connected===true;
  const ownedProfitRunning = t => !t.external_task&&t.template==='profit'
    &&t.preparation_scope==='EXPLICIT_NEW_POST_PROFIT_READONLY'&&t.executor_connected===true
    &&sameRuntimeRelease(runtimeStatus,t.version)&&profitReadonlyRunning();
  const sameRuntimeRelease = (runtime,release) => !!runtime&&!!release
    && typeof release.code_version==='string'&&!!release.code_version
    && typeof release.manifest_digest==='string'&&!!release.manifest_digest
    && runtime.version===release.code_version&&runtime.environment===release.environment
    && runtime.manifest_digest===release.manifest_digest;
  function preparationNotice(){
    const other=['delisting','profit'].filter(t=>t==='profit'?!profitReadonlyRunning():runtimeStatus?.capabilities?.[t]?.connected!==true).map(t=>types[t]);
    return `本次新建上架任务已接入准备执行器；技术缺项在任务中显示。${profitReadonlyRunning()?'本次新建利润任务已接入只读统计；资料缺项在任务中显示。':''}${other.length?other.join('、')+'任务当前仅登记，未接入后台接续。':''}历史任务不会自动接管。`;
  }
  function creationStatus(template){
    if(activeRelease?.environment==='preview')return '只读预览不能创建或修改任务。';
    if(template==='publication'&&scopedPreparationRunning())return '仅本次新建上架任务交由准备执行器接续；来源、图片预算或配置缺项会在任务中暂停。最终审核沿用商品页面。';
    if(template==='publication'&&runtimeStatus?.new_task_preparation?.scope==='EXPLICIT_NEW_POST_PUBLICATION_ONLY')return '上架任务已登记；准备执行器当前未正常接续，请查看任务中的暂停原因。';
    if(template==='profit'&&profitReadonlyRunning())return '仅本次新建利润任务自动接续只读统计和本地报告；缺少结算、成本或广告资料会在原任务中列出。同月旧任务复用不代替新任务授权，原 UNKNOWN 不自动重跑。';
    if(template==='profit')return runtimeStatus?'利润统计任务仅登记，当前未确认只读后台接续。':'任务会保存；后台接续能力尚未核实，不能确认会自动执行。';
    if(runtimeStatus?.capabilities?.[template]?.connected===true)return '该类型执行器已接入；任务保存后按原范围接续，商业审核沿用对应业务页面。';
    return runtimeStatus?`${types[template]||'运营'}任务仅登记，当前未接入该类型的后台接续。`:'任务会保存；后台接续能力尚未核实，不能确认会自动执行。';
  }
  function observationStatus(t){
    const reason=t.pending_observation?.reason||'已提交操作的结果尚未确定。';
    const scoped=t.template==='publication'&&t.preparation_scope==='EXPLICIT_NEW_POST_PUBLICATION_ONLY';
    const connected=!t.external_task&&scoped&&t.executor_connected===true&&scopedPreparationRunning();
    return `${reason} ${connected?'当前任务已接入只读观测；未确定的结果保留待核，不重复提交。':'当前未确认该任务的自动回读接续；保留已有结果，等待只读核查，不重复提交。'}`;
  }
  function profitCreationNotice(returned, requested){
    const task=normalize(returned||{}),id=task.id||'任务身份待核',month=task.scope?.month||'月份待核';
    const known=typeof task.source_key==='string'&&task.source_key.length>0;
    const reused=known&&task.source_key!==requested.source_key;
    const heading=reused?'本月已有任务，已打开原任务':known?'利润任务已登记':'利润任务创建或复用状态待核';
    const status=profitUnknown(task)?'原尝试待核，不自动重跑':state(task)==='queued'&&!ownedProfitRunning(task)?'已登记，未确认自动接续':labels[state(task)]||state(task);
    const result=localURL(task.result_url)?'可在此任务详情查看原报告。':'此任务尚未提供报告入口。';
    return `${heading} ${id} · ${month} · ${status}。${result}${reused?'本次未重新执行统计。':''}`;
  }
  function toast(message){$('toast').textContent=message;$('toast').classList.add('visible');setTimeout(()=>$('toast').classList.remove('visible'),3500);}
  function stepLabel(t){return t.external_task?'原任务进度未同步':t.template==='publication'&&t.current_step==='release'&&!finalReviewReady(t)?'终审候选待形成':t.steps?.find(s=>s.key===t.current_step)?.label||t.current_step||'等待执行';}
  function frozenReviewLink(t){
    const a=t.required_action,b=a?.receipt_binding,s=t.scope||{};
    if(t.template!=='publication'||t.review_mode!=='single-final-review/v1'
      ||state(t)!=='waiting_user'||t.current_step!=='release'
      ||a?.kind!=='review'||!b||typeof b!=='object'||Array.isArray(b)
      ||b.review_mode!==t.review_mode||b.task_id!==t.id
      ||b.action_id!==a.action_id||b.offer_id!==s.offer_id
      ||s.skus?.length!==1||s.skus[0]!==b.sku
      ||!Array.isArray(b.targets)||!b.targets.length
      ||b.targets.some(x=>typeof x!=='string'||!x)||new Set(b.targets).size!==b.targets.length
      ||JSON.stringify([...b.targets].sort())!==JSON.stringify(s.shops)
      ||!/^[1-9][0-9]{0,19}$/.test(b.offer_id)
      ||!/^TASK-[A-Za-z0-9-]{1,64}$/.test(t.id)
      ||!/^[0-9a-f]{32}$/.test(a.action_id)
      ||b.generation!==1||b.step_index!==2
      ||!/^[0-9a-f]{64}$/.test(b.scope_digest||''))return null;
    const common=['offer_id','revision','sku','round1_digest','round2_digest','targets',
      'common_plan_id','common_payload_digest','adapter','review_mode','task_id',
      'action_id','generation','step_index','scope_digest'];
    if(b.adapter==='post-common-diagnostic/v1'){
      const fields=[...common,'common_run_id','common_readback_evidence_digest','source_digest'];
      if(JSON.stringify(Object.keys(b).sort())!==JSON.stringify(fields.sort())
        ||['revision','sku','round1_digest','round2_digest','common_plan_id',
          'common_payload_digest','common_run_id','common_readback_evidence_digest',
          'source_digest'].some(key=>typeof b[key]!=='string'||!b[key]))return null;
      const url=`/product-workspace?offer_id=${b.offer_id}&round=final&task_id=${t.id}`
        +`&action_id=${a.action_id}&review_stage=post-common&generation=1&step_index=2`
        +'#originalPublicationReview';
      return localURL(a.url)===url?{url,label:'查看 COMMON 回读诊断（只读）'}:null;
    }
    if(b.adapter==='single-final-review/v1'){
      const fields=[...common,'common_token_digest','preview_digest'];
      if(JSON.stringify(Object.keys(b).sort())!==JSON.stringify(fields.sort())
        ||['revision','sku','round1_digest','round2_digest','common_plan_id',
          'common_payload_digest','common_token_digest','preview_digest']
          .some(key=>typeof b[key]!=='string'||!b[key]))return null;
      return {url:`/product-workspace?offer_id=${b.offer_id}&task_id=${t.id}`
        +`&action_id=${a.action_id}&round=final#originalPublicationReview`,
        label:'查看冻结候选（只读）'};
    }
    return null;
  }
  const finalReviewReady = t => t.required_action?.receipt_binding?.adapter==='single-final-review/v1' && !!frozenReviewLink(t);
  function actionLink(t){
    if(externalAttention(t)){const result=localURL(t.result_url);return result?`<a class="action-link primary" href="${esc(result)}">查看资料缺口与现有结果</a>`:'';}
    if(t.template==='publication'&&t.current_step==='release'){
      const link=frozenReviewLink(t);
      return link?`<a class="action-link primary" href="${esc(link.url)}">${esc(link.label)}</a>`:'';
    }
    if(t.review_mode==='single-final-review/v1'){
      const link=frozenReviewLink(t);
      return link?`<a class="action-link primary" href="${esc(link.url)}">${esc(link.label)}</a>`:'';
    }
    const a=t.required_action,u=localURL(a?.url);
    return a?.kind==='review'&&u?`<a class="action-link primary" href="${esc(u)}">${esc(a.label||'前往审核')}</a>`:'';
  }
  function signature(){return JSON.stringify({tasks:tasks.map(({updated_at,created_at,checkpoint,...t})=>t),runtimeVerified:!!runtimeStatus,profitRunning:profitReadonlyRunning()});}
  function render(){paintedSignature=signature();const counts={current:tasks.filter(isCurrent).length,all:tasks.length,running:0,attention:0,external:0,problem:0,completed:0,cancelled:0};tasks.forEach(t=>{const g=group(t);if(g in counts)counts[g]++;});Object.keys(counts).forEach(k=>$('count'+k[0].toUpperCase()+k.slice(1)).textContent=counts[k]);
    const attention=tasks.filter(t=>group(t)==='attention');$('attentionList').innerHTML=attention.length?attention.map(t=>`<div class="attention-row"><div><strong>${esc(t.required_action?.label||(externalAttention(t)?'原任务资料待复核':'需要补充信息'))}</strong><p class="sub">${esc(t.title)} · ${esc(observed(t)||t.required_action?.reason||t.blocked_reason||'打开详情查看具体要求')}</p></div><div class="buttons">${actionLink(t)}<button data-detail="${esc(t.id)}">${t.required_action?.kind==='input'?'补充资料':'查看详情'}</button></div></div>`).join(''):'<p class="empty">当前没有需要你处理的事项。</p>';
    const search=$('taskSearch').value.toLowerCase(),type=$('taskType').value,filter=$('taskState').value,matchesState=t=>filter==='all'||(filter==='current'?isCurrent(t):group(t)===filter),visible=tasks.filter(t=>(!type||t.template===type)&&matchesState(t)&&(`${t.title} ${scope(t)}`.toLowerCase().includes(search)));
    const filterTitles={current:'未取消记录',all:'全部记录',running:'执行中任务',waiting:'等待平台结果',attention:'待我处理',external:'历史观测（非实时）',problem:'执行异常',queued:'待执行任务',completed:'已完成任务',cancelled:'已取消历史'};$('taskListTitle').textContent=filterTitles[filter]||'任务';
    $('taskRows').innerHTML=visible.map(t=>`<tr><td><button class="task-title" data-detail="${esc(t.id)}">${esc(t.title)}</button><div class="sub">${esc(types[t.template]||'运营任务')} · ${esc(compactScope(t))}</div></td><td>${esc(stepLabel(t))}<div class="sub">${esc(unknownHint(t)||observed(t)||t.required_action?.label||t.blocked_reason||'')}</div></td><td>${badge(t)}</td><td>${esc(t.external_task?`原执行者：${t.external_task.owner||'未记录'}`:t.worker||t.last_executor||t.owner||'待分派')}<div class="sub">${esc(stamp(t.external_task?.observed_at||t.updated_at))}</div></td><td><button data-detail="${esc(t.id)}">详情</button></td></tr>`).join('');$('listEmpty').hidden=visible.length>0;$('listEmpty').textContent=tasks.length?(filter==='current'?'当前没有未取消的任务。可切换到“全部任务”或“已取消历史”查看记录。':'没有符合筛选条件的任务。'):'还没有任务。新建上架、下架或利润任务即可开始。';document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('selected',b.dataset.filter===filter));
  }
  async function refresh(){if(refreshBusy)return;refreshBusy=true;$('refreshTasks').disabled=true;try{const [d,runtime]=await Promise.all([request(''),request('',undefined,'/api/orbit/operations-runtime').catch(()=>null)]);runtimeStatus=sameRuntimeRelease(runtime,d.release)?runtime:null;if(!Array.isArray(d.tasks))throw Error('任务列表格式不正确');tasks=d.tasks.map(normalize);$('pageError').hidden=true;const connected=d.executor?.connected===true;activeRelease=d.release||null;$('connection').className='notice'+(connected?'':' warning');const dispatcher=d.dispatcher||{};$('connection').textContent=d.release?.environment==='preview'?'只读预览：任务来自独立快照，不能在此创建或修改。':connected?'任务服务已连接。进度来自执行器回报。':dispatcher.state==='error'?`执行器调度异常（${dispatcher.last_error_type||'未知错误'}，${stamp(dispatcher.last_error_at)}）；新任务已保存，但当前不会自动执行。`:dispatcher.state==='source_mismatch'?'运行代码或 Skill 版本与启动版本不一致；执行器已暂停，请核对部署。':'任务可保存；后台执行尚未启动，新任务仅登记，不会自动执行商业操作。';if(scopedPreparationRunning()&&d.release?.environment!=='preview'){ $('connection').className='notice';$('connection').textContent=preparationNotice();}updateCreationStatus();const guard=d.domain_guard;if(guard?.unresolved_operation_count>0){$('connection').classList.add('warning');$('connection').textContent+=` 另有 ${guard.unresolved_operation_count} 条历史业务操作尚未完成回读，锁定 ${guard.locked_resource_count} 个资源；不会自动重试。`;}const release=d.release||{};$('releaseInfo').textContent=Object.keys(release).length?JSON.stringify(release,null,2):'服务未提供版本信息；不能确认当前运行版本。';renderDomainOperations(d);renderPendingChecks(d);if(paintedSignature!==signature())render();await refreshOpenDetail();}catch(e){runtimeStatus=null;updateCreationStatus();$('pageError').hidden=false;$('pageError').textContent=`任务读取失败：${e.message}。${tasks.length?'下方保留上次读取结果，可能已过时。':'请重试。'}`;$('connection').textContent='任务服务连接失败，当前进度未验证。';$('connection').className='notice warning';}finally{refreshBusy=false;$('refreshTasks').disabled=false;}}
  function updateCreationStatus(){if($('creationStatus'))$('creationStatus').textContent=creationStatus($('newType').value);}
  function updateFields(){updateCreationStatus();const t=$('newType').value;$('offerField').hidden=t!=='publication';$('skuField').hidden=t!=='delisting';$('monthField').hidden=t!=='profit';$('newOffer').required=t==='publication';$('newSkus').required=t==='delisting';$('newMonth').required=t==='profit';$('newShops').required=t==='profit';$('scopeHint').textContent=t==='profit'?'填写精确店铺范围；统计截止到已完全结算的日期，实际广告等缺失资料将在任务中列出。':'范围须核实准确；创建任务不代替商品审核或最终商业执行授权。';}
  function detailFingerprint(task,events){return JSON.stringify({task:normalize(task),events,release:activeRelease,runtimeVerified:!!runtimeStatus,preparationRunning:scopedPreparationRunning(),profitRunning:profitReadonlyRunning()});}
  function replaceDetailBody(html,preserveInput){
    const body=$('detailBody'),form=preserveInput?$('inputForm'):null,action=form?.closest('.detail-action');
    if(!form||action?.parentElement!==body){body.innerHTML=html;return;}
    const next=document.createElement('div');next.innerHTML=html;
    const nextAction=next.querySelector('.detail-action'),nextForm=nextAction?.querySelector('#inputForm');
    if(!nextForm||nextAction.parentElement!==next){body.innerHTML=html;return;}
    for(const node of [...action.childNodes])if(node!==form)node.remove();
    for(const node of [...nextAction.childNodes])if(node!==nextForm)action.insertBefore(node,form);
    const before=[],after=[];let passedAction=false;
    for(const node of [...next.childNodes]){if(node===nextAction){passedAction=true;continue;}(passedAction?after:before).push(node);}
    for(const node of [...body.childNodes])if(node!==action)node.remove();
    for(const node of before)body.insertBefore(node,action);
    for(const node of after)body.appendChild(node);
  }
  async function refreshOpenDetail(){
    const id=detailId,generation=detailGeneration;
    if(!$('detailDialog').open||!id||detailLoading||detailRefreshBusy||pendingMutations.has(id))return;
    detailRefreshBusy=true;
    try{
      const data=await request('/'+encodeURIComponent(id));
      if(!$('detailDialog').open||detailId!==id||detailGeneration!==generation||pendingMutations.has(id))return;
      if(normalize(data.task).id!==id)throw Error('详情任务身份与当前选择不一致');
      const events=data.events||[],fingerprint=detailFingerprint(data.task,events);
      if(fingerprint!==detailSignature){renderDetail(data.task,events);detailSignature=fingerprint;}
      $('detailError').textContent='';
    }catch(error){
      if($('detailDialog').open&&detailId===id&&detailGeneration===generation)$('detailError').textContent=error.message;
    }finally{detailRefreshBusy=false;}
  }
  function renderDetail(t,events){const previous=current;t=normalize(t);const oldInput=previous?.id===t.id&&previous.required_action?.kind==='input'&&$('inputForm');const preserveInput=!!(oldInput&&t.required_action?.kind==='input'&&previous.required_action.action_id===t.required_action.action_id);if(oldInput&&!preserveInput){const draft=$('inputNote')?.value;if(draft)retiredDrafts.push({actionId:previous.required_action.action_id,text:draft});}$('detailTitle').textContent=t.title||'任务详情';const action=t.required_action,review=actionLink(t),result=localURL(t.result_url),needsInput=action?.kind==='input';
    const detailHTML=`${badge(t)}${t.pending_observation?`<p class="notice">${esc(observationStatus(t))}</p>`:''}${t.external_task?`<p class="notice">原任务观测状态：${esc(t.external_task.observed_status||'尚无业务状态记录')}<br>观测时间：${esc(stamp(t.external_task.observed_at))}。这是历史观测，当前实时状态未核实。${externalAttention(t)?'<br>历史记录标有资料缺口；需核实原任务当前状态，再决定如何接续。':''}</p>`:''}<div class="detail-grid"><div><small>任务范围</small>${esc(scope(t))}</div><div><small>执行者</small>${esc(t.external_task?.owner||t.worker||t.last_executor||t.owner||'待分派')}</div><div><small>当前步骤</small>${esc(stepLabel(t))}</div><div><small>观测或更新</small>${esc(stamp(t.external_task?.observed_at||t.updated_at))}</div></div>${action?`<section class="detail-action"><strong>${esc(action.label||'待处理')}</strong><p>${esc(action.reason||t.blocked_reason||'')}</p>${review}${action.kind==='review'&&!review?'<p>审核入口尚未提供，请由执行者绑定准确的业务审核页。</p>':''}${needsInput?'<form id="inputForm"><label for="inputNote">补充资料或文件位置</label><textarea id="inputNote" required rows="3" maxlength="5000" placeholder="填写资料位置或需要补充的说明，不要填写密码、令牌。"></textarea><button class="primary" type="submit">提交资料并继续</button></form>':''}</section>`:''}${t.blocked_reason&&!action?`<p class="error">${esc(t.blocked_reason)}</p>`:''}${t.external_task?'<p class="muted">原任务的逐步执行记录未完整同步；未同步步骤不代表尚未开展，请以已有报告及观测记录为准。</p>':''}<ol class="steps">${(t.steps||[]).filter(s=>!t.external_task||['completed','done'].includes(s.state)).map((s,i)=>`<li><span>${i+1}. ${esc(s.key==='release'&&t.template==='publication'&&!finalReviewReady(t)?'终审候选待形成':s.label)}</span><span class="badge">${esc(state(t)==='cancelled'&&s.state==='pending'?'已停止':({pending:'待执行',running:'执行中',completed:'已完成',done:'已完成',failed:'失败',waiting_user:'待你处理',waiting_domain:'等待平台结果',reconciliation_required:'核对执行结果',skipped:'已跳过'})[s.state]||s.state||'待执行')}</span></li>`).join('')}</ol><div class="buttons">${result?`<a class="action-link primary" href="${esc(result)}">${externalAttention(t)?'查看现有结果':'查看结果'}</a>`:''}${canResume(t)?'<button id="retryTask">恢复任务</button>':state(t)==='failed'?'<span class="muted">当前任务缺少可验证的安全恢复资格；请先核对任务历史与外部结果。</span>':''}${!['completed','cancelled','running','reconciliation_required','external_task','waiting_domain'].includes(state(t))?'<button id="cancelTask">取消任务</button>':''}</div>${state(t)==='reconciliation_required'?'<p class="muted">外部结果尚未确定，先核实已有操作结果，不重复提交。</p>':''}<details class="runtime"><summary>任务版本与恢复记录</summary><div>${esc(JSON.stringify({version:t.version,checkpoint:t.checkpoint},null,2))}</div></details><h3>执行记录</h3>${events.length?events.map(e=>`<div class="event"><div>${esc(eventLabels[e.event_type]||e.message||e.note||'任务记录已更新')}</div><div class="sub">${esc(stamp(e.created_at||e.at||e.timestamp))}</div></div>`).join(''):'<p class="muted">尚无执行记录。</p>'}`;
    replaceDetailBody(detailHTML,preserveInput);if(retiredDrafts.length)$('detailBody').insertAdjacentHTML('beforeend',`<section id="staleInputDraft" class="notice"><strong>旧动作已失效</strong><p>未提交的草稿仅供复制，不会自动提交到新动作。</p>${retiredDrafts.map(d=>`<div><small>原动作 ${esc(d.actionId||'未标识')}</small><pre>${esc(d.text)}</pre></div>`).join('')}</section>`);current=t;
    if(t.template==='publication'&&t.current_step==='release'&&!finalReviewReady(t)){
      const offer=t.scope?.offer_id;
      const link=typeof offer==='string'&&/^[1-9][0-9]{0,19}$/.test(offer)
        ?`<a class="action-link" href="/product-workspace?offer_id=${offer}&round=final">查看商品当前阻断（只读）</a>`:'';
      $('detailBody').insertAdjacentHTML('afterbegin',`<section class="detail-action"><strong>终审候选尚未就绪 · BLOCKED</strong><p>本任务未绑定可审核的完整冻结候选；妙手 COMMON 官方逐字段回读、完整目标矩阵及图片质量/审核展示证据仍须核实。打开商品工作台可查看实时阻断，此入口不是批准动作。</p>${link}</section>`);
    }
    if(profitUnknown(t)){
      const proof=t.profit_unknown_readback, file=proof.agent_result_file;
      $('detailBody').insertAdjacentHTML('afterbegin',`<section class="detail-action"><strong>利润原尝试待核对</strong><p>原会话与候选输出未经独立核实，不可自动重跑或扩大结算截止日期。</p><div><small>只读状态</small> ${esc(proof.status||'BLOCKED')}</div><div><small>原尝试路径</small> ${esc(proof.attempt_output||'未记录')}</div><div><small>会话 ID</small> ${esc(proof.session_id||'未记录')}（${esc(proof.session_readback||'未核实')}）</div><div><small>候选摘要</small> ${esc(file?.sha256||'无可核对文件')}（未经核实）</div></section>`);
      $('cancelTask')?.remove();
    }
    if(!preserveInput)$('inputForm')?.addEventListener('submit',e=>{e.preventDefault();mutate('provide-input',{note:$('inputNote').value.trim(),action_id:t.required_action.action_id});});$('retryTask')?.addEventListener('click',()=>mutate('retry',{}));$('cancelTask')?.addEventListener('click',()=>mutate('cancel',{}));
    if(pendingMutations.has(t.id))$('detailBody').querySelectorAll('button').forEach(button=>button.disabled=true);
  }
  async function openDetail(id){const generation=++detailGeneration;if(detailId!==id)retiredDrafts=[];detailId=id;detailSignature=null;detailLoading=true;current=null;$('detailError').textContent='';$('detailTitle').textContent='任务详情';$('detailBody').textContent='正在读取…';if(!$('detailDialog').open)$('detailDialog').showModal();try{const d=await request('/'+encodeURIComponent(id));if(!$('detailDialog').open||detailId!==id||detailGeneration!==generation)return;if(normalize(d.task).id!==id)throw Error('详情任务身份与当前选择不一致');renderDetail(d.task,d.events||[]);detailSignature=detailFingerprint(d.task,d.events||[]);}catch(e){if($('detailDialog').open&&detailId===id&&detailGeneration===generation)$('detailError').textContent=e.message;}finally{if(detailGeneration===generation)detailLoading=false;}}
  async function mutate(action,body){
    const id=current?.id,generation=detailGeneration;
    if(!id||!$('detailDialog').open||detailId!==id||pendingMutations.has(id))return;
    const mutation={generation},buttons=[...$('detailBody').querySelectorAll('button')];
    pendingMutations.set(id,mutation);
    $('detailError').textContent='';buttons.forEach(button=>button.disabled=true);
    const stillCurrent=()=>$('detailDialog').open&&detailId===id&&detailGeneration===generation;
    try{
      await request('/'+encodeURIComponent(id)+'/'+action,body);
      const ownsView=stillCurrent();
      if(ownsView)await openDetail(id);
      await refresh();
      if(ownsView&&$('detailDialog').open&&detailId===id)toast(action==='provide-input'?'资料已保存，任务状态已更新。':'任务状态已更新。');
    }catch(error){
      if(stillCurrent()){$('detailError').textContent=error.message;buttons.forEach(button=>{if(button.isConnected)button.disabled=false;});}
    }finally{
      if(pendingMutations.get(id)===mutation)pendingMutations.delete(id);
      if($('detailDialog').open&&detailId===id&&detailGeneration!==generation&&!detailLoading){
        detailSignature=null;
        await refreshOpenDetail();
      }
    }
  }
  $('createTask').onclick=()=>{$('createForm').reset();$('newMonth').value=previousMonth();createKey=crypto.randomUUID();$('createError').textContent='';updateFields();$('createDialog').showModal();};$('newType').onchange=updateFields;
  $('createForm').addEventListener('submit',async e=>{e.preventDefault();$('createError').textContent='';$('submitTask').disabled=true;const template=$('newType').value,scope={shops:lines($('newShops').value),skus:template==='delisting'?lines($('newSkus').value):[]};if(template==='publication')scope.offer_id=$('newOffer').value.trim();if(template==='profit')scope.month=$('newMonth').value;const payload={template,title:$('newTitle').value.trim(),source_key:createKey,scope};if(template==='profit')payload.reuse_existing_scope=true;try{const d=await request('',payload);$('createDialog').close();await refresh();toast(template==='profit'?profitCreationNotice(d.task,payload):'任务已保存。');if(d.task)await openDetail(d.task.task_id||d.task.id);}catch(e){$('createError').textContent=e.message;}finally{$('submitTask').disabled=false;}});
  $('detailDialog').addEventListener('close',()=>{if($('detailDialog').open)return;detailGeneration++;detailId=null;detailSignature=null;detailLoading=false;current=null;retiredDrafts=[];});
  document.addEventListener('click',e=>{const detail=e.target.closest('[data-detail]'),close=e.target.closest('[data-close]'),filter=e.target.closest('[data-filter]');if(detail)openDetail(detail.dataset.detail);if(close)$(close.dataset.close).close();if(filter){$('taskState').value=filter.dataset.filter;render();}});
  ['taskSearch','taskType','taskState'].forEach(id=>$(id).addEventListener(id==='taskSearch'?'input':'change',render));$('refreshTasks').onclick=refresh;document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});setInterval(()=>{if(!document.hidden)refresh();},15000);refresh();
})();
