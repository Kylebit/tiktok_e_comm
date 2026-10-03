"use strict";
(() => {
 const aside=document.createElement('aside');aside.id='orbitSidebar';aside.setAttribute('aria-label','Orbit 主导航');
 aside.innerHTML='<a class="orbit-brand" href="/">Orbit<span>运营工作台</span></a><button id="orbitMenu" type="button" aria-expanded="false" aria-controls="orbitCoreNav">菜单</button><nav id="orbitCoreNav" aria-label="五核心导航"></nav>';
 document.body.classList.add('orbit-shell-page');document.body.prepend(aside);
 const nav=aside.querySelector('nav'),toggle=aside.querySelector('button');
 const rows=/* ORBIT_REGISTERED_NAVIGATION */[];
 for(const row of rows){const a=document.createElement('a');a.href=row.href;a.textContent=row.label;a.dataset.core=row.key;if(row.key===(document.body.dataset.core||'catalog'))a.setAttribute('aria-current','page');nav.append(a);}
 const status=document.createElement('div');status.id='orbitNavStatus';status.setAttribute('role','status');aside.append(status);
 toggle.addEventListener('click',()=>{const open=aside.classList.toggle('open');toggle.setAttribute('aria-expanded',String(open));});
 document.addEventListener('keydown',event=>{if(event.key==='Escape'){aside.classList.remove('open');toggle.setAttribute('aria-expanded','false');}});
 function verifyNavigation(){status.textContent='';fetch('/api/orbit/navigation',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('导航读取失败');return r.json();}).then(data=>{
  if(data.ok!==true||!Array.isArray(data.navigation))throw Error('导航配置无效');
  const remote=data.navigation.filter(r=>r.level==='primary').map(r=>({key:r.key,label:r.label,href:r.href}));
  if(JSON.stringify(remote)!==JSON.stringify(rows))throw Error('导航配置不一致');
 }).catch(()=>{status.textContent='导航状态读取失败，仍可使用入口。';const retry=document.createElement('button');retry.type='button';retry.textContent='重试';retry.onclick=verifyNavigation;status.append(retry);});}
 verifyNavigation();
 async function loadPublicationHistory(){
  if(location.pathname!=='/product-workspace')return;
  const offer=new URLSearchParams(location.search).get('offer_id');
  if(!/^[0-9]{1,32}$/.test(offer||''))return;
  const anchor=document.querySelector('#publicationFlowLedger');
  if(!anchor)return;
  const panel=document.createElement('details');panel.id='orbitPublicationHistory';panel.open=true;
  const heading=document.createElement('summary');heading.textContent='历史发布结果（只读）';panel.append(heading);
  const note=document.createElement('p');note.textContent='上方状态描述当前候选；不代表此前批准或发布记录消失。这里保留历史结果，不作为当前重新批准、发布或重试的授权。';panel.append(note);
  const output=document.createElement('div');output.textContent='正在读取历史记录…';panel.append(output);anchor.after(panel);
  try{
   const response=await fetch('/api/product-workspace/publication-history?offer_id='+encodeURIComponent(offer),{cache:'no-store'});
   if(!response.ok)throw Error('history unavailable');
   const data=await response.json();
   if(data.execution_authority!==false||data.display_mode!=='HISTORICAL_READ_ONLY')throw Error('invalid history');
   output.replaceChildren();
   if(!['AVAILABLE','PARTIALLY_BLOCKED'].includes(data.history_status)){
    output.textContent=data.history_status==='INTEGRITY_BLOCKED'?'历史证据完整性校验未通过，记录保持封存；不能据此执行。':'历史数据源暂不可用；未创建或替换原记录。';return;
   }
   if(data.blocked_count){const warning=document.createElement('p');warning.textContent=`另有 ${data.blocked_count} 份历史报告完整性校验未通过，保持封存；以下仅显示校验通过的历史记录。`;output.append(warning);}
   if(!data.items.length){output.textContent='指定历史库中没有此商品的报告。';return;}
   for(const item of data.items){
    const row=document.createElement('details'),title=document.createElement('summary');
    const platforms=(item.summary?.platforms||[]).map(x=>`${x.platform} ${x.status}`).join(' · ');
    title.textContent=`版本 ${item.revision} · ${platforms||item.status} · ${item.created_at||''}`;
    const identity=document.createElement('p');identity.textContent=`报告 ${item.report_id} · Plan ${item.plan_id}`;
    row.append(title,identity);
    for(const target of item.targets||[]){const line=document.createElement('p');line.textContent=`${target.target_label} · ${target.status}`;row.append(line);}
    output.append(row);
   }
  }catch(error){output.textContent='历史记录读取失败，当前执行继续保持暂停。';}
 }
 if(location.pathname==='/knowledge')return;
 fetch('/api/orbit/operations-runtime',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(profile=>{
  if(profile?.execution_mode==='web-only'){
   const notice=document.createElement('div');notice.id='orbitMaintenanceNotice';notice.setAttribute('role','status');
   notice.textContent=profile.environment==='preview'
    ? '只读预览 · 与正式任务账本隔离。可查看页面与历史记录；成本、任务及商品审核不能在此保存。'
    : (typeof profile.maintenance_message==='string' && profile.maintenance_message.trim()
       ? profile.maintenance_message
       : '后台状态待核，请以各任务能力提示为准');
   notice.style.cssText='position:sticky;top:0;z-index:90;padding:10px 20px;background:#fff4d6;color:#754c0b;border-bottom:1px solid #e4c777;font-size:14px';
   document.body.insertBefore(notice,aside.nextSibling);
   loadPublicationHistory();
  }
  if(profile?.environment!=='preview')return;
   const lockWrites=()=>document.querySelectorAll('#createTask,#submitTask,#submitProfit,.cost-editor input,.cost-editor button,#originalPublicationReview button:not([data-original-round]),#originalPublicationReview input,#originalPublicationReview select,#originalPublicationReview textarea,#approvalButton').forEach(control=>{
   if(!control.disabled)control.disabled=true;
   if(control.title!=='只读预览不可保存')control.title='只读预览不可保存';
  });
  lockWrites();new MutationObserver(lockWrites).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled']});
 }).catch(()=>{});
})();
