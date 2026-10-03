/* Current stage authority and retained local intake/evidence within the dense workspace. */
(() => {
  'use strict';
  const $=s=>document.querySelector(s);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let host={}, data=null, stages=null, loading=false, busy=false, sequence=0, requestId=crypto.randomUUID();
  let savedImages=[], pendingRequest=null;
  let localReview=null, localCsrfCookie='';
  let localSessionIssue=null, localRecoveryLauncher='';
  let nativeProgressTimer=null;
  const localOperatorPrefix='/api/product-workspace/local-operator/';
  const nativeFinalPrefix='/api/product-workspace/native-final/';
  const localDraftKey='orbit-manual-intake-draft-v1';
  async function api(path,body) {
    const headers=body===undefined?{}:{'Content-Type':'application/json'};
    if(path.startsWith(localOperatorPrefix)&&!path.endsWith('/context')){
      const entry=document.cookie.split('; ').find(value=>value.startsWith(localCsrfCookie+'='));
      if(!localCsrfCookie||!entry){const error=new Error('本机会话未连接或已到期。');error.payload={error:'LOCAL_OPERATOR_SESSION_REQUIRED'};throw error;}
      headers['X-Orbit-CSRF-Token']=entry.slice(localCsrfCookie.length+1);
    }
    const response=await fetch(path,{credentials:'same-origin',headers,...(body===undefined?{}:{method:'POST',body:JSON.stringify(body)})});
    const value=await response.json();
    if (!response.ok || value.ok===false) {const error=new Error(value.error||'读取失败');error.payload=value;throw error;}
    return value;
  }
  async function prepareLocalReview(descriptor){
    if(descriptor?.schema_version==='native-sole-final-review/v1'){
      if(descriptor.offer_id!==stages?.offer_id || descriptor.plan_id!==stages?.marketplace?.plan?.plan_id
        ||descriptor.candidate_digest!==stages?.marketplace?.preview?.candidate_digest
        ||descriptor.execution_authority!==false || !descriptor.review_digest || !descriptor.review)
        throw new Error('当前冻结候选尚未连接原生终审来源。');
      const displayed=structuredClone(descriptor.review),displayedDigest=descriptor.review_digest;
      const prepared=await api(nativeFinalPrefix+'prepare',{plan_id:descriptor.plan_id});
      const actual=prepared.review;
      if(prepared.review_digest!==displayedDigest || !actual
        ||Object.keys(actual).length!==Object.keys(displayed).length
        ||Object.keys(displayed).some(key=>JSON.stringify(actual[key])!==JSON.stringify(displayed[key])))
        throw new Error('商品内容或目标已变化；当前点击没有批准新候选，请刷新并核对新内容。');
      if(prepared.display?.candidate_digest!==descriptor.candidate_digest
        ||JSON.stringify(prepared.manifest?.targets?.map(row=>row.target_label))!==JSON.stringify(descriptor.targets))
        throw new Error('完整冻结候选已变化，未记录批准。');
      return {...prepared,native_channel:true};
    }
    if(!descriptor||descriptor.schema_version!=='local-operator-review/v1'
      ||descriptor.execution_authority!==false||descriptor.offer_id!==stages?.offer_id
      ||!descriptor.reservation_id||!descriptor.review_digest
      ||descriptor.marketplace_plan_id!==stages?.marketplace?.plan?.plan_id
      ||descriptor.candidate_digest!==stages?.marketplace?.preview?.candidate_digest)
      throw new Error('最终审核尚未连接可信的本机候选。');
    const context=await api(localOperatorPrefix+'context');
    if(context.channel_installed!==true||typeof context.csrf_cookie_name!=='string')throw new Error('本机会话尚未连接。');
    localCsrfCookie=context.csrf_cookie_name;
    localRecoveryLauncher=context.recovery_requires_same_windows_user===true
      && typeof context.recovery_launcher_prefix==='string'?context.recovery_launcher_prefix:'';
    const prepared=await api(localOperatorPrefix+'prepare',{reservation_id:descriptor.reservation_id,
      marketplace_plan_id:descriptor.marketplace_plan_id});
    if(prepared.review_digest!==descriptor.review_digest||prepared.review?.offer_id!==descriptor.offer_id)
      throw new Error('当前冻结候选已变化，请刷新并核对新内容。');
    localSessionIssue=null;
    return prepared;
  }
  function sessionRecovery(error){
    const code=error.payload?.error;
    if(!['LOCAL_OPERATOR_SESSION_EXPIRED','LOCAL_OPERATOR_SESSION_REQUIRED'].includes(code))return false;
    localSessionIssue={code,offer_id:stages?.offer_id,review_digest:stages?.marketplace?.local_operator_review?.review_digest};
    return true;
  }
  function renderSessionRecovery(){
    const descriptor=stages?.marketplace?.local_operator_review;
    if(!localSessionIssue||localSessionIssue.offer_id!==stages?.offer_id||localSessionIssue.review_digest!==descriptor?.review_digest)return;
    const note=document.createElement('aside');note.dataset.localSessionRecovery='required';note.setAttribute('role','status');
    const saved=descriptor?.approval_saved===true;
    note.innerHTML=`<strong>${localSessionIssue.code==='LOCAL_OPERATOR_SESSION_EXPIRED'?'本机会话已到期':'本机会话未连接或已到期'}</strong><p>${saved?'这份冻结候选的批准已保存，重新连接后沿用原决定。':'完整冻结候选已保留，重新连接后继续当前审核。'}本机重新连接不增加一次审核，也不会发布商品。</p>`;
    // The server provides a nonsecret launcher prefix. Only safe identifiers
    // from the same displayed domain descriptor may be appended; no URL secret.
    if(localRecoveryLauncher && /^[A-Za-z0-9_:-]+$/.test(descriptor?.reservation_id||'')
      && /^[A-Za-z0-9_:-]+$/.test(descriptor?.marketplace_plan_id||'')){
      const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='本机重新连接方法';
      const guide=document.createElement('p');guide.textContent='在同一 Windows 用户下的命令提示符运行下面已有的本机启动程序。它将打开原审核页；完成连接后只读刷新当前页。';
      const command=document.createElement('pre');command.textContent=localRecoveryLauncher+' --reservation-id '+descriptor.reservation_id+' --marketplace-plan-id '+descriptor.marketplace_plan_id;
      details.append(summary,guide,command);note.append(details);
    }
    const refreshButton=document.createElement('button');refreshButton.type='button';refreshButton.textContent='已重新连接，读取原审核状态';refreshButton.disabled=busy||loading;refreshButton.onclick=refresh;
    note.append(refreshButton);$('#publicationFlowReview').append(note);
  }
  async function checkRecoveredSession(){
    const descriptor=stages?.marketplace?.local_operator_review;
    if(!localSessionIssue)return;
    if(localSessionIssue.offer_id!==stages?.offer_id||localSessionIssue.review_digest!==descriptor?.review_digest){localSessionIssue=null;return;}
    try{
      const context=await api(localOperatorPrefix+'context');
      if(context.channel_installed!==true||typeof context.csrf_cookie_name!=='string')return;
      localCsrfCookie=context.csrf_cookie_name;
      const status=await api(localOperatorPrefix+'status');
      if(status.session_ready===true)localSessionIssue=null;
    }catch(_error){/* Keep the same candidate and technical recovery guidance. */}
  }
  function variant(row={}) {
    const tr=document.createElement('tr');
    tr.innerHTML=['label','cost','weight','length','width','height'].map(name=>`<td><input data-manual-field="${name}" value="${esc(row[name]||'')}" ${name==='label'?'maxlength="120"':'inputmode="decimal"'} required></td>`).join('')+'<td><button type="button" data-remove-variant>移除</button></td>';
    tr.querySelector('button').onclick=()=>{if($('#manualVariants').children.length>1)tr.remove();};
    $('#manualVariants').append(tr);
  }
  function draft() {
    return {request_id:requestId,title:$('#manualTitle').value,description:$('#manualDescription').value,
      rows:[...$('#manualVariants').children].map(tr=>Object.fromEntries([...tr.querySelectorAll('input')].map(i=>[i.dataset.manualField,i.value]))),images:savedImages,pending:pendingRequest};
  }
  async function readImages() {
    if (!$('#manualImages').files.length) return savedImages;
    const files=[...$('#manualImages').files];
    if(files.length>12 || files.some(f=>f.size>24*1024*1024) || files.reduce((n,f)=>n+f.size,0)>64*1024*1024)throw new Error('图片数量或大小超限。');
    return Promise.all(files.map(file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({name:file.name,mime_type:file.type,data_base64:String(reader.result).split(',')[1]});reader.onerror=()=>reject(new Error('无法读取图片'));reader.readAsDataURL(file);})));
  }
  function manualStatus(message,error=false){$('#manualStatus').textContent=message;$('#manualStatus').dataset.state=error?'error':'ok';}
  async function submitManual(event) {
    event.preventDefault();if(busy)return;
    busy=true;$('#submitManualIntake').disabled=true;manualStatus('正在保存本地图片与资料…');
    try {
      savedImages=await readImages();
      const value=draft();
      const body={request_id:requestId,title:value.title,description:value.description,
        variants:value.rows.map(r=>({label:r.label,purchase_cost_cny:r.cost,weight_kg:r.weight,package_cm:[r.length,r.width,r.height]})),images:savedImages};
      if(pendingRequest && JSON.stringify(body)!==JSON.stringify(pendingRequest))throw new Error('上次保存结果尚未确认，请恢复原内容重试；原请求仍保留。');
      pendingRequest=body;
      const result=await api('/api/product-workspace/manual-intake',body);
      manualStatus(`已保存本地商品 ${result.offer_id}。从商品历史打开并核对事实。`);
      pendingRequest=null;localStorage.removeItem(localDraftKey);
      requestId=crypto.randomUUID();
      $('#manualIntakeDialog').close();
      await loadHistory();
    } catch(error){manualStatus(error.message,true);} finally{busy=false;$('#submitManualIntake').disabled=false;}
  }
  async function loadHistory(showDetails=false) {
    if(showDetails===true)$('#productHistory').hidden=false;
    $('#productHistoryStatus').textContent='正在读取本地历史…';
    $('#connectedProductStatus').textContent='正在读取已连接商品…';
    $('#openConnectedProduct').disabled=true;
    try {
      const value=await api('/api/product-workspace/history');
      const select=$('#connectedProductSelect'),selected=select.value||new URL(location.href).searchParams.get('offer_id')||'';
      select.innerHTML='<option value="">请选择商品，不会自动切换</option>'+value.items.map(row=>`<option value="${esc(row.offer_id)}">SKU ${esc(row.primary_sku||'待分配')} · Offer ${esc(row.offer_id)} · ${esc(row.title)}</option>`).join('');
      select.value=value.items.some(row=>String(row.offer_id)===selected)?selected:'';
      $('#openConnectedProduct').disabled=!select.value;
      $('#connectedProductStatus').textContent=value.items.length?`${value.count} 件商品可在本页审核。当前地址中的商品不在列表时，表示其资料尚未连接，请明确选择。`:'当前没有可读取的商品。可在下方录入，或恢复原商品资料后刷新。';
      $('#productHistoryRows').innerHTML=value.items.map(row=>`<tr><td>${esc(row.title)}<br><small>Offer ${esc(row.offer_id)} · SKU ${esc(row.primary_sku||'待分配')}</small></td><td>${row.source_mode==='manual_intake'?'手工录入':esc(row.source_authority)}<br><small>${row.record_source==='registered_review'?'已连接冻结审核资料':'本地商品档案'}</small></td><td>${esc(row.revision??'未知')}</td><td>${esc(row.updated_at||'未知')}</td><td><button data-history-offer="${esc(row.offer_id)}" type="button">打开当前商品</button></td></tr>`).join('')+(value.errors||[]).map(row=>`<tr><td>Offer ${esc(row.offer_id)}</td><td colspan="4">资料未连接：${esc(row.error)}。需恢复对应资料后读取。</td></tr>`).join('');
      $('#productHistoryStatus').textContent=`${value.count} 条可读取记录。请明确选择商品；历史批准不授予当前执行权限。${value.truncated?' 列表已达显示上限，可输入准确 Offer ID 读取。':''}`;
      $('#productHistoryRows').querySelectorAll('button').forEach(button=>button.onclick=()=>host.openProduct?.(button.dataset.historyOffer));
    }catch(error){$('#productHistoryStatus').textContent=error.message;$('#connectedProductStatus').textContent='商品列表读取失败：'+error.message;$('#connectedProductSelect').replaceChildren();}
  }
  // Any stages response owns this workspace; a partial response cannot reopen legacy controls.
  function managed(){return loading || stages !== null;}
  function previewReady(candidate){return ['PREVIEW_READY','READY_FOR_FINAL_REVIEW'].includes(candidate?.status);}
  function privateReviewAvailable(market){
    const descriptor=market?.local_operator_review,plan=market?.plan,candidate=market?.preview||market?.candidate;
    return market?.private_final_review_available===true
      && descriptor?.schema_version==='local-operator-review/v1'
      && descriptor.evidence_kind==='SYNTHETIC_TEST_ONLY' && descriptor.execution_authority===false
      && descriptor.marketplace_plan_id===plan?.plan_id
      && descriptor.offer_id===String(data?.product?.offer_id)
      && descriptor.candidate_digest===candidate?.candidate_digest
      && typeof descriptor.reservation_id==='string' && typeof descriptor.review_digest==='string';
  }
  function nativeReviewAvailable(market,candidate){
    const descriptor=market?.native_final_review,review=descriptor?.review,plan=market?.plan;
    const sameTargets=(left,right)=>Array.isArray(left) && left.length>0
      && new Set(left).size===left.length && JSON.stringify(left)===JSON.stringify(right);
    return market?.status==='APPROVAL_REQUIRED' && market.final_review_available===true
      && market.final_review_admission?.final_review_available===true
      && descriptor?.schema_version==='native-sole-final-review/v1'
      && descriptor.approval_saved===false && descriptor.decision_id===null
      && descriptor.execution_authority===false
      && candidate?.schema_version==='publication-release-candidate/v1'
      && candidate.status==='READY_FOR_FINAL_REVIEW'
      && typeof descriptor.plan_id==='string' && descriptor.plan_id===plan?.plan_id
      && descriptor.offer_id===String(data?.product?.offer_id)
      && typeof descriptor.candidate_digest==='string' && descriptor.candidate_digest.length>0
      && descriptor.candidate_digest===candidate.candidate_digest
      && typeof descriptor.review_digest==='string' && descriptor.review_digest.startsWith('sha256:')
      && review?.plan_id===descriptor.plan_id && review.offer_id===descriptor.offer_id
      && review.candidate_digest===descriptor.candidate_digest
      && sameTargets(descriptor.targets,market.targets) && sameTargets(descriptor.targets,plan.targets)
      && sameTargets(descriptor.targets,review.targets);
  }
  function candidateAwaitingReview(market,candidate){
    if(market?.native_final_review)return nativeReviewAvailable(market,candidate);
    return candidate?.approval_status==='NOT_APPROVED'
      || (privateReviewAvailable(market) && market.local_operator_review.private_domain_plan_status==='PENDING_APPROVAL'
        && candidate?.schema_version==='publication-release-candidate/v1'
        && candidate?.status==='READY_FOR_FINAL_REVIEW');
  }
  function actionable(value){
    const market=value?.marketplace,common=value?.common;
    return value?.ok===true && common?.status==='VERIFIED'
      && market && typeof market==='object'
      && (market.status==='APPROVAL_REQUIRED'
        ? privateReviewAvailable(market) || (market.final_review_available===true
          && market.final_review_admission?.final_review_available===true)
        : market.status==='NATIVE_DECISION_SAVED' ? !!market.native_final_review?.decision_id
        : ['APPROVAL_BINDING_REQUIRED','READY_TO_PUBLISH'].includes(market.status)
          && market.final_review_admission?.execution_authority===true);
  }
  function reason(value) {
    const raw=Array.isArray(value)?value.join('；'):String(value||'');
    if(!raw)return '先核对当前商品事实与图片，再准备本次店铺候选。';
    if(/snapshot|round1|ROUND1|R1_|identity|IDENTITY|changed|drift/.test(raw))return '当前来源或审核版本与原冻结资料不一致。请重新核对第一轮资料，原 COMMON 结果仍可查看。';
    if(/image|IMAGE|R2_|round2|qa|QA/.test(raw))return '当前图片或第二轮审核证据尚未齐备。请在图片页完成明确选择并核对当前版本。';
    if(/^[\x00-\x7f]+$/.test(raw))return '当前资料尚未通过服务端检查。请核对事实、图片和店铺条件；完整原因见详情。';
    return raw;
  }
  function commonConditions(technical) {
    const policy=technical?.standing_policy_facts||{},authority=technical?.authority_facts||{};
    const census=technical?.source_facts?.native_local_census||{};
    const known=policy.status==='KNOWN_LOCAL_USER_INTENT' && policy.maximum_confirmed_writes===1;
    const history=Number.isInteger(policy.confirmed_write_count) && policy.confirmed_write_count>=0
      ? `实际历史确认写入 ${policy.confirmed_write_count} 次`
      : '实际历史写入数待核实';
    const lines=[known?'本机持续意愿已读取；确认写入上限 1 次':'本机持续意愿尚未核实',history];
    if(census.status==='LOCAL_PERSISTED_CENSUS') {
      lines.push('已核对同库准备轮血缘');
      if(Number.isInteger(census.local_observed_confirmed_writes)&&census.local_observed_confirmed_writes>=0)
        lines.push(`本地账本记录确认写入 ${census.local_observed_confirmed_writes} 次`);
      if(Number.isInteger(census.local_observed_readonly_reuses)&&census.local_observed_readonly_reuses>=0)
        lines.push(`只读复用 ${census.local_observed_readonly_reuses} 次`);
      if(Array.isArray(census.unresolved_attempts)&&census.unresolved_attempts.length)
        lines.push(`原请求结果待核对 ${census.unresolved_attempts.length} 项`);
      if(Array.isArray(census.unclassified_history)&&census.unclassified_history.length)
        lines.push(`历史记录尚未分类 ${census.unclassified_history.length} 项`);
      lines.push('这些数量仅覆盖本地保留记录，不代表剩余可写次数');
    } else lines.push('本地准备轮血缘尚未核实');
    if(authority.account_authority==='UNKNOWN'||!authority.account_authority)lines.push('账户执行权限待核实');
    if(authority.coverage_authority==='UNKNOWN'||!authority.coverage_authority)lines.push('写入覆盖范围待核实');
    if(authority.schema_installation==='UNKNOWN'||!authority.schema_installation)lines.push('运行安装状态待核实');
    return lines.join('；')+'。读取这些事实不代表可以写入或发布，也不增加人工批准。';
  }
  function actionState() {
    if(loading)return {label:'正在核对发布阶段',detail:'读取当前商品的持久计划与目标账本。'};
    if(!stages)return null;
    const common=stages.common||{},market=stages.marketplace||{};
    if(stages.ok===false)return {label:'发布阶段待核对',detail:'当前发布阶段不可核实，旧发布入口已关闭；请只读刷新核对。'};
    if(market.successor_preview?.status==='NOT_APPROVED')return market.successor_preview.review_blocked
      ? {label:'补齐 successor 质量证据',detail:'durable_quality_evidence 尚未通过；当前不可审核或执行，所有平台入口保持关闭。'}
      : {label:'审核本地化 successor',detail:'新候选尚未批准；请在最终审核页核对 Shopee MY / TH / VN 描述与完整目标，所有平台执行入口保持关闭。',action:'review'};
    if(stages.error && !common.status)return {label:'发布阶段需核对',detail:reason(stages.error)};
    if(['RETAINED_TECHNICAL_BASELINE','TECHNICAL_CONDITIONS_UNKNOWN'].includes(common.status) && common.user_status){
      const state=common.user_status,pending=Array.isArray(state.pending_items)?state.pending_items:[];
      return {label:state.summary||'COMMON 准备条件待核对',
        detail:[state.candidate_summary,pending.length?'待核对：'+pending.join('、'):null].filter(Boolean).join('；')};
    }
    if(common.status==='RECONCILIATION_REQUIRED')return {label:'核对原 COMMON 结果',detail:'原提交结果或证据待核对；保留原计划，停止重复写入。'};
    if(market.native_execution_in_progress)return {label:'正在执行原已批准任务',detail:'唯一终审决定已保存；逐目标记录自动更新，不重复提交。'};
    if(market.native_final_review?.approval_saved && market.status==='PUBLISHED')return {label:'所选平台已完成回读',detail:'原任务已完成，可查看逐目标结果与来源报告。'};
    if(market.native_final_review?.approval_saved && market.status==='PROCESSING')return {label:'等待平台结果',detail:'原任务已提交，保留已批准范围；可仅核对回读，不重新批准或重复发布。'};
    if(market.status==='RECONCILIATION_REQUIRED')return {label:'处理发布结果与对账',detail:'COMMON 已回读；部分平台结果未知，按原运行记录核对。'};
    if(market.status==='NATIVE_DECISION_SAVED')return {label:'原最终批准已保存',detail:'沿用原冻结范围自动核对并继续；不会再次审核或重发未知结果。',action:'resume-native'};
    if(market.status==='APPROVAL_BINDING_REQUIRED')return {label:'恢复已保存的批准绑定',detail:'使用原批准与原冻结资料继续，不新增批准。',action:'resume'};
    if(market.final_review_available===false && market.final_review_admission)return {label:'最终审核暂不可用',detail:`终审前证据缺口：${(market.final_review_admission.blockers||[]).join('、')||'来源未核实'}。保留冻结候选和原回读，不可批准。`};
    if(market.status==='APPROVAL_REQUIRED')return {label:'核对最终发布候选',detail:'核对本次目标、价格、图片版本与预算后批准。',action:'review'};
    if(common.status==='APPROVAL_REQUIRED')return {label:'COMMON 技术接线待完成',detail:commonConditions(common.technical_admission)};
    if(common.status==='READY_TO_SYNC')return {label:'COMMON 技术接线待完成',detail:commonConditions(common.technical_admission)};
    if(market.status==='PUBLISHED')return {label:'所选平台已完成回读',detail:'查看逐目标结果与来源报告。'};
    if(market.status==='PROCESSING')return {label:'等待原发布任务回读',detail:'仅刷新原运行结果，不重新提交。'};
    if(market.status==='FAILED')return {label:'核对平台失败记录',detail:'保留 COMMON 与其他平台结果，按原报告处理。'};
    if(market.status==='READY_TO_PUBLISH')return {label:'执行已批准的平台',detail:'按下方独立平台按钮执行；已有结果的平台只读核对。',action:'review'};
    if(common.status==='VERIFIED')return {label:'核对平台准备条件',detail:reason(market.blockers)};
    return null;
  }
  function drawNext() {
    if(!managed())return false;
    const state=actionState()||{label:'发布阶段待核对',detail:'刷新并检查持久证据。'};
    $('#nextStepNumber').textContent='03';$('#nextStepTitle').textContent=state.label;$('#nextStepDescription').textContent=state.detail;
    const button=$('#nextStepActionButton');button.hidden=false;button.disabled=busy||loading;button.textContent=state.action==='resume'?'恢复原批准绑定':'查看阶段与账本';
    button.dataset.actionCode='server-stage';
    return true;
  }
  function renderReview() {
    const common=stages?.common||{},market=stages?.marketplace||{};
    const successor=market.successor_preview;
    const candidate=successor||market.preview||market.candidate;
    const plan=market.plan||common.plan;
    $('#publicationFlowStatus').textContent=actionState()?.detail||stages?.error||'第一、二轮证据尚未齐备，先在商品事实与图片页核对。';
    const review=$('#publicationFlowReview');
    const summary=candidate?.review_manifest;
    const commonOnly=!market.plan && !!common.plan;
    const nativeCommon=['RETAINED_TECHNICAL_BASELINE','TECHNICAL_CONDITIONS_UNKNOWN'].includes(common.status) && common.user_status;
    const factsLabel=nativeCommon?(common.status==='RETAINED_TECHNICAL_BASELINE'?'原准备事实已冻结':'准备来源待核对'):commonOnly?(data?.product?.actual_product_approved?'当前事实已冻结':'当前事实待准备'):(data?.product?.actual_product_approved?'当前事实已批准':'当前事实待批准');
    const selected=data?.publication_scope?.selected_labels||[];
    const preparationRange=`${selected.includes('miaoshou:COMMON')?'COMMON＋':''}${selected.filter(label=>label!=='miaoshou:COMMON').length} 个平台目标`;
    const frozenTargets=candidate?.target_labels||plan?.targets||[];
    const frozenRange=market.plan?`冻结候选范围：${frozenTargets.filter(label=>label!=='miaoshou:COMMON').length} 个平台目标`:'COMMON 准备基线';
    review.innerHTML=`<div class="review-version-row"><strong>当前审核</strong><div>${esc(data?.product?.title||'当前商品')}<small>版本 ${esc(data?.product?.revision??'未知')} · 当前准备范围：${esc(preparationRange)} · ${factsLabel}</small><small>原计划的成功或批准，不代表当前审核版本可发布。</small></div></div>`;
    if(plan)review.innerHTML+=`<div class="review-version-row"><strong>${successor?'本地化 successor':market.plan?'平台冻结候选':'COMMON 冻结资料'}</strong><div>${esc(plan.payload?.product_facts?.title||plan.payload?.copy_package?.title||'已保存的原计划资料')}<small>冻结版本 ${esc(plan.payload?.product_revision??candidate?.product_revision??'未知')} · ${esc(frozenRange)} · ${esc(frozenTargets.join('、'))}</small><small>${successor?'NOT_APPROVED · 旧候选与历史保留，平台执行已关闭':market.approval?'该冻结候选已有最终批准':nativeCommon?'平台候选资料保留 · 当前不可终审或执行':commonOnly?'COMMON 技术准备资料 · 不增加人工批准':plan.status==='APPROVED'?'该原计划已批准':'该计划尚未批准'}</small></div></div>`;
    const technical=common.technical_admission,source=technical?.source_facts;
    const sourceLabel=nativeCommon?(common.status==='RETAINED_TECHNICAL_BASELINE'?'COMMON 完成记录已核对':'COMMON 准备来源待核对'):source?.status==='RETAINED_IDENTITY_VERIFIED'?'已核对本地持久计划与历史':source?.status==='INVALID'?'本地来源有变化 · 需核对':'本地来源尚未核实';
    const authority=technical?.authority_facts||{};
    review.innerHTML+=`<div class="review-version-row"><strong>COMMON 来源与执行条件</strong><div>${esc(sourceLabel)}<small>${esc(nativeCommon?common.user_status.summary:commonConditions(technical))}</small></div></div>`;
    review.innerHTML+=`<details><summary>当前检查原因与阶段身份 · 完整详情</summary><pre>${esc(JSON.stringify({error:stages?.error,common_status:common.status,common_blockers:common.blockers,common_technical_admission:technical,common_retained_baseline:common.retained_baseline_facts,common_technical_details:common.technical_details,marketplace_status:market.status,marketplace_blockers:market.blockers,final_review_available:market.final_review_available,final_review_admission:market.final_review_admission,plan_id:plan?.plan_id},null,2))}</pre></details>`;
    if(candidate){
      const targets=summary?.targets||[];
      review.innerHTML+=`<div class="table-scroll"><table><thead><tr><th>目标 / 语言</th><th>SKU / 冻结售价</th><th>库存依据</th><th>图片版本</th></tr></thead><tbody>${targets.map(t=>`<tr><td>${esc(t.target_label)}<br>${esc(t.locale)}</td><td>${(t.prices||[]).map(p=>`${esc(p.model_sku)} · ${esc(p.amount)} ${esc(p.currency)}`).join('<br>')||'未知'}</td><td>${esc(t.inventory?.note_zh||'按该平台冻结规则')}</td><td>${esc(t.image_set_id||'未知')}</td></tr>`).join('')}</tbody></table></div>`;
      for(const set of summary?.image_sets||[])review.innerHTML+=`<details><summary>${esc(set.target_labels.join('、'))} · ${set.images.length} 张冻结图片 · 查看顺序</summary><div class="flow-images">${set.images.map(i=>`<figure><img src="${esc(i.url)}" alt="${esc(i.brand_id||'商品')} ${esc(i.role||i.position)}" data-flow-image="${esc(set.image_set_id)}"><figcaption>${esc(i.position)} · ${esc(i.role)}</figcaption></figure>`).join('')}</div></details>`;
      review.innerHTML+=`<details><summary>完整冻结文案、规格、预算与证据</summary><pre>${esc(JSON.stringify({review:summary,write_budget:candidate.write_budget,inventory:candidate.warehouse_inventory_policy},null,2))}</pre></details>`;
    }else if(plan){review.innerHTML+=`<details><summary>冻结公共资料与图片身份</summary><pre>${esc(JSON.stringify(plan.payload,null,2))}</pre></details>`;}
    const rows=market.platforms||[];
    if(market.native_final_review?.approval_saved && Array.isArray(market.target_results)){
      const resultLabels={SUCCEEDED:'已完成官方回读',SUBMITTED_UNVERIFIED:'已提交 · 等待官方结果',RUNNING:'正在执行 · 结果待核对',RECONCILIATION_REQUIRED:'结果未知 · 不可重发',FAILED:'结果待核对 · 不可重发',PENDING:'尚未执行'};
      review.innerHTML+=`<table data-native-target-results><thead><tr><th>冻结目标</th><th>原任务结果</th></tr></thead><tbody>${market.target_results.map(row=>`<tr><td>${esc(row.target_label)}</td><td>${esc(resultLabels[row.status]||'结果待核对')}</td></tr>`).join('')}</tbody></table>`;
    }
    const statusLabel=value=>({RETAINED_TECHNICAL_BASELINE:'COMMON 已完成',TECHNICAL_CONDITIONS_UNKNOWN:'COMMON 准备条件待核对',VERIFIED:'原 COMMON 已回读',RECONCILIATION_REQUIRED:'原结果待核对 · 不可重发',APPROVAL_REQUIRED:'待批准当前范围',READY_TO_SYNC:'原计划已批准 · 技术预算待核对',READY_TO_PUBLISH:'已批准 · 待执行',PUBLISHED:'平台已回读',PROCESSING:'等待回读',FAILED:'失败待处理',BLOCKED:'条件待补全'}[value]||'尚未完成');
    $('#publicationFlowLedger').innerHTML=`<table><thead><tr><th>阶段 / 平台</th><th>结果</th><th>来源</th></tr></thead><tbody><tr><td>COMMON 冻结计划</td><td>${esc(common.status==='APPROVAL_REQUIRED'?'技术接线待完成 · 不新增人工批准':common.status==='READY_TO_SYNC'?'COMMON 技术条件待核对':statusLabel(common.status))}</td><td>${esc(sourceLabel)}<details><summary>来源、执行条件与原运行详情</summary><pre>${esc(JSON.stringify({status:common.status,source_facts:source,authority_facts:authority,retained_baseline:common.retained_baseline_facts,technical_details:common.technical_details,run:common.run},null,2))}</pre></details></td></tr>${rows.map(row=>`<tr><td>${esc(row.platform)}</td><td>${esc(statusLabel(row.status))}</td><td><details><summary>原目标记录</summary><pre>${esc(JSON.stringify(row,null,2))}</pre></details></td></tr>`).join('')}</tbody></table><button type="button" id="inspectStageDetails">查看与复制完整阶段证据</button>`;
    $('#inspectStageDetails').onclick=()=>window.OrbitDenseReview?.showDetails('当前审核与原冻结阶段证据',stages);
    const actions=$('#publicationFlowActions');actions.replaceChildren();
    function button(label,action){const b=document.createElement('button');b.type='button';b.className='button button-primary';b.textContent=label;b.disabled=busy||loading;b.onclick=action;actions.append(b);}
    const legacyManaged=managed();
    if(privateReviewAvailable(market))review.innerHTML+='<aside data-private-review="synthetic" role="status"><strong>私有演练 · SYNTHETIC_TEST_ONLY</strong><p>本页仅验证完整候选与一次批准流程，使用隔离数据和模拟执行；没有正式发布权限。</p></aside>';
    renderSessionRecovery();
    $('#releasePlanApprovalForm').hidden=legacyManaged;
    $('#legacyReleaseActionPanels').hidden=legacyManaged;
    $('#legacyReleaseActionPanels').setAttribute('aria-hidden',String(legacyManaged));
    $('#releasePrimaryActionPanel').hidden=legacyManaged;
    if(legacyManaged)$('#releasePlan').querySelectorAll('#releasePlanApprovalForm button,#releasePlanApprovalForm input[type="checkbox"],#legacyReleaseActionPanels button,#legacyReleaseActionPanels input[type="checkbox"]').forEach(control=>{control.disabled=true;control.title='当前阶段请使用上方冻结候选与目标账本。';});
    if(loading||!actionable(stages)||!plan||successor?.status==='NOT_APPROVED')return;
    if(localSessionIssue?.offer_id===stages.offer_id && localSessionIssue.review_digest===market.local_operator_review?.review_digest)return;
    if(market.status==='APPROVAL_REQUIRED' && (market.final_review_available===true||privateReviewAvailable(market)) && candidateAwaitingReview(market,candidate) && previewReady(candidate)){
      if(market.local_operator_review?.approval_saved===true)actions.textContent=market.local_operator_review.private_successor_state==='COMPLETE'
        ? '本次批准已保存，隔离测试后续步骤已完成。' : '本次冻结候选的批准已保存，技术恢复沿用该决定。';
      else if(market.native_final_review?.schema_version==='native-sole-final-review/v1')button('批准当前最终候选',()=>write('market-approve'));
      else if(market.local_operator_review?.schema_version==='local-operator-review/v1')button(privateReviewAvailable(market)?'批准当前最终候选（私有演练）':'批准当前最终候选',()=>write('market-approve'));
      else actions.textContent='本机唯一终审通道尚未连接，冻结候选保留。';
    }
    else if(market.native_readback_resume_available===true&&!market.native_execution_in_progress)button('仅核对原平台回读',()=>write('readback-native'));
    else if(market.status==='NATIVE_DECISION_SAVED'&&!market.native_execution_in_progress)button('继续原已批准任务',()=>write('resume-native'));
    else if(market.status==='APPROVAL_BINDING_REQUIRED')button('恢复原批准绑定',()=>write('resume'));
    if(Array.isArray(market.native_readback_capabilities)&&market.native_readback_capabilities.length){
      const pending=document.createElement('p');
      pending.textContent=market.native_readback_capabilities.map(item=>`${item.target_label||'原目标'}：具体店铺身份尚未核验，保留提交结果。`).join(' ');
      const detail=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');
      summary.textContent='回读技术详情';pre.textContent=JSON.stringify(market.native_readback_capabilities,null,2);
      detail.append(summary,pre);actions.append(pending,detail);
    }
    if(market.candidate && market.approval && common.status==='VERIFIED')for(const row of rows){
      if(row.status==='READY_TO_PUBLISH'&&row.next_action==='EXECUTE_APPROVED_PLATFORM')button(`执行 ${row.platform}`,()=>write(row.platform));
    }
  }
  async function refresh() {
    if(nativeProgressTimer!==null){clearTimeout(nativeProgressTimer);nativeProgressTimer=null;}
    if(!data?.product?.offer_id)return;
    const offer=String(data.product.offer_id),ticket=++sequence;loading=true;stages=null;drawNext();renderReview();
    try{
      let value;try{value=await api(`/api/product-workspace/publication-stages?offer_id=${encodeURIComponent(offer)}`);}catch(error){
        // An error body can describe a missing stage but cannot authorize a plan.
        value={ok:false,offer_id:offer,schema_version:'publication-stages/v1',stage:'RECONCILIATION_REQUIRED',error:'当前发布阶段不可核实，旧发布入口已关闭；请只读刷新核对。'};
      }
      if(ticket!==sequence||String(data?.product?.offer_id)!==offer)return;
      const identified=value?.offer_id===offer&&value?.schema_version==='publication-stages/v1';
      const valid=identified && value?.ok===true && value.marketplace
        && typeof value.marketplace==='object' && typeof value.marketplace.status==='string'
        && (value.marketplace.final_review_available!==true
          || value.marketplace.final_review_admission?.final_review_available===true)
        && (value.common===null
          ? value.marketplace.status==='BLOCKED'
          : value.common && typeof value.common==='object'
            && typeof value.common.status==='string'
            && (!['APPROVAL_REQUIRED','READY_TO_PUBLISH'].includes(value.marketplace.status)
              || value.common.status==='VERIFIED'));
      stages=valid?value:{ok:false,offer_id:offer,schema_version:'publication-stages/v1',
        stage:'RECONCILIATION_REQUIRED',error:'Stage response is incomplete; legacy release is closed. Refresh and reconcile.'};
      await checkRecoveredSession();
      if(ticket!==sequence||String(data?.product?.offer_id)!==offer)return;
      window.OrbitOriginalReview?.stages(stages);
      if(stages?.marketplace?.native_execution_in_progress){
        nativeProgressTimer=setTimeout(()=>{
          nativeProgressTimer=null;
          if(String(data?.product?.offer_id)===offer&&!busy)refresh();
        },2000);
      }
    }finally{if(ticket===sequence){loading=false;renderReview();host.redrawRelease?.();if(!drawNext())host.redrawNext?.();}}
  }
  async function write(kind) {
    if(busy||loading||!actionable(stages)||String(data?.product?.offer_id)!==stages.offer_id)return;
    const frozen=stages,market=frozen.marketplace||{},common=frozen.common||{},plan=market.plan||common.plan;
    if(!plan)return;
    if(kind==='market-approve' && !(market.status==='APPROVAL_REQUIRED'
      && (market.final_review_available===true||privateReviewAvailable(market)) && previewReady(market.preview)
      && candidateAwaitingReview(market,market.preview)))return;
    if(kind==='resume' && market.status!=='APPROVAL_BINDING_REQUIRED')return;
    if(kind==='resume-native' && (market.status!=='NATIVE_DECISION_SAVED'||!market.native_final_review?.decision_id))return;
    if(kind==='readback-native' && (market.native_readback_resume_available!==true||!market.native_final_review?.decision_id))return;
    busy=true;drawNext();renderReview();
    const base={offer_id:frozen.offer_id,plan_id:plan.plan_id,confirmation_token:plan.confirmation_token};
    try{
      if(kind==='market-approve'){
        // Reissue the short nonce on this same displayed frozen digest. A slow
        // review is one human click, not another approval because TTL elapsed.
        localReview=await prepareLocalReview(market.native_final_review||market.local_operator_review);
        await api((localReview.native_channel?nativeFinalPrefix:localOperatorPrefix)+'decision',{nonce:localReview.nonce,review_digest:localReview.review_digest});
      }
      else if(kind==='resume-native')await api(nativeFinalPrefix+'resume',{decision_id:market.native_final_review.decision_id});
      else if(kind==='readback-native')await api(nativeFinalPrefix+'readback',{decision_id:market.native_final_review.decision_id});
      else if(kind==='resume')await api('/api/product-workspace/r3-marketplace/resume-binding',base);
      else {
        const row=market.platforms?.find(r=>r.platform===kind);
        if(!row||row.next_action!=='EXECUTE_APPROVED_PLATFORM'||row.status!=='READY_TO_PUBLISH')throw new Error('原平台任务需要核对，不能重复执行。');
        const paths={TIKTOK:'publish-tiktok',SHOPEE:'publish-shopee-global',OZON:'publish-ozon'};
        await api('/api/product-workspace/'+paths[kind],{...base,candidate_digest:market.candidate.candidate_digest,snapshot_digest:market.candidate.snapshot_digest,publication_targets:plan.targets});
      }
      await refresh();
    }catch(error){
      if(sessionRecovery(error)){
        // Do not retry the decision, renew an expired identity from HTTP, or
        // mistake reconnection for a new human approval.
        renderReview();$('#publicationFlowStatus').textContent='本机会话需要重新连接；当前冻结候选和已保存的批准均保留。';
      }else $('#publicationFlowStatus').textContent=`${error.message} 当前审核版本与草稿保留，请只读刷新核对。`;
      $('#publicationFlowActions').replaceChildren();
    }
    finally{busy=false;if(localSessionIssue)renderReview();drawNext();}
  }
  async function evidence(offer) {
    try{const value=await api(`/api/product-workspace/evidence?offer_id=${encodeURIComponent(offer)}`);if(String(data?.product?.offer_id)!==offer)return;
      $('#roundEvidenceContent').innerHTML=(value.local_images?.length?`<p>手工录入来源图 · 尚未批准用于发布</p><div class="flow-images">${value.local_images.map(r=>r.url?`<img width="88" height="88" src="${esc(r.url)}" alt="本地商品来源图 ${esc(r.position)}" data-local-source-image="${esc(offer)}">`:`<span>来源图 ${esc(r.position)} 需恢复</span>`).join('')}</div>`:'')+value.rounds.map(r=>`<details><summary>${esc(r.filename)} · ${esc(r.status)}</summary><p>${esc(r.error||r.source_digest||'尚无记录')}</p><pre>${esc(JSON.stringify(r.facts||{},null,2))}</pre></details>`).join('');
      $('#publicationClosureContent').innerHTML=value.closures.length?value.closures.map(r=>`<p>${esc(r.status)} · ${esc(r.closure?.closure_id||r.error)}</p><p>官方回读 ${r.status==='AVAILABLE'?esc(r.closure.summary.verified_count):'未知'} · 人工接管 ${esc(r.closure?.summary.manual_handoff_count??'未知')} · 待处理 ${esc(r.closure?.summary.processing_count??'未知')}</p><details><summary>来源报告与目标</summary><pre>${esc(JSON.stringify(r,null,2))}</pre></details>`).join(''):'尚无关闭记录。未执行与未知结果不计为发布成功。';
    }catch(error){$('#roundEvidenceContent').textContent=error.message;}
  }
  window.OrbitPublicationWorkspace={
    setHost(value){host=value;},
    render(value){data=value;const select=$('#connectedProductSelect');select.value=String(value.product?.offer_id||'');$('#openConnectedProduct').disabled=!select.value;refresh();evidence(String(value.product?.offer_id||''));},
    drawNext,
    managed,
    loadHistory,
    next(){if(!managed())return false;host.releaseTab?.();$('#publicationFlow').scrollIntoView({block:'start'});return true;},
    clear(){if(nativeProgressTimer!==null){clearTimeout(nativeProgressTimer);nativeProgressTimer=null;}sequence++;data=null;stages=null;loading=false;localSessionIssue=null;localRecoveryLauncher='';localCsrfCookie='';$('#roundEvidenceContent').textContent='正在核对当前商品来源。';$('#publicationFlowReview').replaceChildren();$('#publicationFlowLedger').replaceChildren();$('#publicationFlowActions').replaceChildren();$('#publicationClosureContent').textContent='当前商品记录尚未读取。';},
  };
  document.addEventListener('DOMContentLoaded',()=>{
    const legacyControls='#releasePlanApprovalForm button,#releasePlanApprovalForm input[type="checkbox"],'
      +'#legacyReleaseActionPanels button,#legacyReleaseActionPanels input[type="checkbox"],'
      +'#collectboxActionButton,#commonOverwriteButton,#prepareMiaoshouButton,'
      +'#releasePrimaryActionButton,#shopeeGlobalReleaseButton,#ozonReleaseButton,#publishAllButton,'
      +'#releaseRunLedger button,#oneClickExecutionGroups button,#releasePlanRecovery button';
    // Keep only proven GET previews and local guidance/focus reachable. Unknown
    // old buttons, including price-repair reconciliation, remain closed.
    const legacyReadOnlyControls='#releaseRunLedger button[data-target-scoped-action="preview"],'
      +'#releaseRunLedger button[data-price-repair-action="preview"],'
      +'#oneClickExecutionGroups button.channel-category-preview-retry,'
      +'#oneClickExecutionGroups button.channel-category-attributes-next,'
      +'#oneClickExecutionGroups button.shopee-global-plan-preview-retry,'
      +'#oneClickExecutionGroups button.shopee-global-auth-restore,'
      +'#oneClickExecutionGroups button[data-oneclick-target],'
      +'#releasePlanRecovery button.channel-category-preview-retry,'
      +'#releasePlanRecovery button.channel-category-attributes-next,'
      +'#releasePlanRecovery button.shopee-global-plan-preview-retry,'
      +'#releasePlanRecovery button.shopee-global-auth-restore';
    document.addEventListener('click',event=>{
      const control=event.target.closest(legacyControls);
      if(managed()&&control&&!control.matches(legacyReadOnlyControls)){
        event.preventDefault();event.stopImmediatePropagation();$('#publicationFlowStatus').textContent='请使用当前冻结阶段与目标账本中的操作。';
      }
    },true);
    document.addEventListener('submit',event=>{if(managed()&&event.target.closest('#releasePlanApprovalForm,#legacyReleaseActionPanels form,#releaseRunLedger form,#oneClickExecutionGroups form,#releasePlanRecovery form')){event.preventDefault();event.stopImmediatePropagation();}},true);
    variant();
    $('#openManualIntake').onclick=()=>{$('#manualIntakeDialog').showModal();};
    $('#openProductHistory').onclick=()=>loadHistory(true);
    $('#connectedProductSelect').onchange=()=>{$('#openConnectedProduct').disabled=!$('#connectedProductSelect').value;};
    $('#openConnectedProduct').onclick=()=>{const offer=$('#connectedProductSelect').value;if(offer)host.openProduct?.(offer);};
    $('#refreshConnectedProducts').onclick=loadHistory;
    $('#closeProductHistory').onclick=()=>{$('#productHistory').hidden=true;};
    // Discovery is read-only and independent of this browser's retained queue.
    loadHistory();
    $('#manualIntakeForm').onsubmit=submitManual;
    $('#addManualVariant').onclick=()=>{if($('#manualVariants').children.length<50)variant();};
    $('#refreshPublicationFlow').onclick=refresh;
    $('#manualIntakeDialog').addEventListener('cancel',event=>{event.preventDefault();manualStatus('请选择保存草稿或放弃录入。');});
    $('#saveManualDraft').onclick=async()=>{try{savedImages=await readImages();localStorage.setItem(localDraftKey,JSON.stringify(draft()));$('#manualIntakeDialog').close();}catch(error){manualStatus('草稿未保存：'+error.message,true);}};
    $('#discardManualDraft').onclick=()=>{if(busy)return;localStorage.removeItem(localDraftKey);pendingRequest=null;savedImages=[];requestId=crypto.randomUUID();$('#manualIntakeForm').reset();$('#manualVariants').replaceChildren();variant();$('#manualIntakeDialog').close();};
    try{const value=JSON.parse(localStorage.getItem(localDraftKey)||'null');if(value){requestId=value.request_id;$('#manualTitle').value=value.title;$('#manualDescription').value=value.description;savedImages=value.images||[];pendingRequest=value.pending||null;$('#manualVariants').replaceChildren();value.rows.forEach(variant);$('#manualImages').required=!savedImages.length;manualStatus('已恢复本地录入草稿与图片。');}}catch(_error){manualStatus('旧录入草稿无法读取，原记录未删除。',true);}
  });
})();
