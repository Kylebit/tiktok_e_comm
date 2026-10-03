"use strict";
(()=>{
 const $=s=>document.querySelector(s);let mode='UNKNOWN',copy=null,offset=0,total=0,items=[],generation=0;
 let limit=50;
 function fail(message){mode='UNKNOWN';$('#catalogModeError').hidden=false;$('#catalogModeError span').textContent=message+'；操作已暂停。';document.querySelectorAll('main input,main select,main button').forEach(n=>{if(n.id!=='catalogModeRetry')n.disabled=true;});}
 function accept(data){
  if(!data||data.ok!==true||!Object.hasOwn(data,'review_copy'))throw Error('目录数据模式无效');
  const candidate=data.review_copy,valid=candidate?.mode==='LOCAL_REVIEW_COPY'&&candidate.cost_changes==='COPY_ONLY'&&candidate.images==='REGISTERED_CACHE'&&!isNaN(Date.parse(candidate.captured_at));
  if(candidate!==null&&!valid)throw Error('目录数据模式无效');
  if(copy&&(!valid||candidate.captured_at!==copy.captured_at||candidate.source_database!==copy.source_database))throw Error('目录数据模式冲突');
  copy=candidate;mode=valid?'REVIEW_COPY':'NORMAL';$('#catalogModeError').hidden=true;
  document.querySelectorAll('#catalogFilters input,#catalogFilters select,#catalogFilters button').forEach(n=>n.disabled=false);
  $('#pageSize').disabled=false;
  $('#catalogCopyNote').hidden=!copy;
  if(copy)$('#catalogCopyNote').textContent='本地审核副本 · 成本改动仅保存在副本，不会写回原库或平台。商品图按已有记录读取；失效原图会明确标注。快照采集：'+new Date(copy.captured_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+'（北京时间，非平台同步时间）。';
 }
 async function json(url,options){const r=await fetch(url,{cache:'no-store',...options});const d=await r.json();if(!r.ok||d.ok!==true)throw Error(d.error||'目录请求失败');return d;}
 function cell(tr,text,cls){const td=document.createElement('td');if(cls)td.className=cls;td.textContent=text||'';tr.append(td);return td;}
 function render(){
  const body=$('#tbody');body.replaceChildren();
  for(const item of items){
   const tr=document.createElement('tr');tr.dataset.entity=item.entity_key;
   const picture=cell(tr,'');
   if(item.image_key){const candidates=item.image_candidates||[{key:item.image_key,url:item.image_url}];let imageIndex=0;const img=document.createElement('img');img.className='product-image';img.alt=item.name;img.loading='lazy';const show=()=>{img.src='/api/catalog/image?key='+encodeURIComponent(candidates[imageIndex].key);};img.onerror=()=>{if(++imageIndex<candidates.length){show();return;}img.remove();picture.textContent='原图暂不可用';picture.className='image-failure';};show();picture.append(img);}else picture.textContent='无原图';
   const sku=cell(tr,item.sku||'未填 SKU');
   if(item.aliases.length>1){const aliases=document.createElement('div');aliases.className='alias';aliases.textContent=item.aliases.filter(s=>s!==item.sku).join(' / ');sku.append(aliases);}
   if(item.binding==='MISSING_SKU'){const note=document.createElement('small');note.className='binding';note.textContent='缺 SKU，单独保留';sku.append(note);}
   const name=cell(tr,'','name');
   const disclosure=document.createElement('details');disclosure.className='product-name';
   const summary=document.createElement('summary'),shortName=document.createElement('span');shortName.textContent=item.name||'未命名商品';summary.append(shortName);summary.title='展开或收起完整商品名称';
   const fullName=document.createElement('div');fullName.className='product-name-full';fullName.textContent=item.name||'未命名商品';disclosure.append(summary,fullName);name.append(disclosure);
   if(item.channel_evidence?.length){const note=document.createElement('small');note.className='binding';const statuses=[...new Set(item.channel_evidence.map(r=>r.product_status||'当前状态未核验'))];note.textContent='商品档案 · '+statuses.join(' / ');note.title=item.channel_evidence.map(r=>[r.platform,r.product_status||'当前状态未核验',r.observed_at||'来源时间未提供'].join(' · ')).join('\n');name.append(document.createElement('br'),note);}
   cell(tr,item.spec||'—');
   const cost=cell(tr,''),editor=document.createElement('div');editor.className='cost-editor';
   const input=document.createElement('input');input.type='number';input.step='0.01';input.min='0.01';input.className='cost-input';input.setAttribute('aria-label',(item.sku||'未填 SKU')+' 成本价');input.value=item.cost.amount??'';input.placeholder=item.cost.status==='CONFLICT'?'待选择':'未填写';
   const save=document.createElement('button');save.type='button';save.className='cost-save';save.textContent='保存';editor.append(input,save);cost.append(editor);
   const hint=document.createElement('div');hint.className='cost-hint';if(item.cost.status==='CONFLICT'){hint.classList.add('cost-conflict');hint.textContent='原记录：¥'+item.cost.choices.join(' / ¥')+'，请填写确认值。';}cost.append(hint);
   async function saveCost(){if(mode==='UNKNOWN')return;const value=input.value;if(!value||!Number.isFinite(Number(value))||Number(value)<=0){hint.textContent='请输入大于 0 的成本价';return;}save.disabled=true;input.disabled=true;try{const data=await json('/api/catalog/cost',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entity_key:item.entity_key,cost_cny:value,revision:item.cost.revision})});item.cost=data;input.value=data.amount;hint.classList.remove('cost-conflict');hint.textContent=copy?'已保存到副本':'已保存';}catch(error){hint.textContent=error.message+'，请重新搜索后再试。';}finally{save.disabled=mode==='UNKNOWN';input.disabled=mode==='UNKNOWN';}}
   save.onclick=saveCost;input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();saveCost();}};
   cell(tr,item.weight_g==null?(item.weight_status==='CONFLICT'?'记录不一致':'—'):String(item.weight_g));body.append(tr);
  }
  if(!items.length){const tr=document.createElement('tr'),td=cell(tr,'没有符合条件的商品');td.colSpan=6;body.append(tr);}
  $('#listMeta').textContent='共 '+total+' 个 SKU 实体 · 当前 '+(total?offset+1:0)+'–'+Math.min(offset+items.length,total);
  $('#pageNumber').textContent=(Math.floor(offset/limit)+1)+' / '+Math.max(1,Math.ceil(total/limit));$('#previousPage').disabled=offset===0;$('#nextPage').disabled=offset+limit>=total;
 }
 async function load(){if(mode==='UNKNOWN')return;const ticket=++generation;$('#previousPage').disabled=true;$('#nextPage').disabled=true;$('#listMeta').textContent='正在加载…';try{const d=await json('/api/catalog/skus?'+new URLSearchParams({q:$('#skuQ').value,region:$('#region').value,cost_status:$('#costStatus').value,limit,offset}));if(ticket!==generation)return;accept(d);items=d.items;total=d.total;if(offset>0&&offset>=total){offset=total?Math.floor((total-1)/limit)*limit:0;return load();}$('#coverage').textContent=d.coverage.internal_skus+' 个内部 SKU · '+d.coverage.missing_sku_records+' 条缺 SKU 记录 · '+d.coverage.conflicts+' 组成本待选择';render();}catch(error){if(ticket!==generation)return;fail('目录加载失败：'+error.message);$('#tbody').replaceChildren();$('#listMeta').textContent='目录加载失败，未显示不完整数据。';}}
 async function bootstrap(){++generation;fail('正在确认目录连接');try{accept(await json('/api/catalog/data-mode'));await load();}catch(error){fail('目录连接失败：'+error.message);$('#listMeta').textContent='目录连接失败';}}
 $('#pageSize').onchange=()=>{const selected=Number($('#pageSize').value);if(![25,50,100].includes(selected))return;limit=selected;offset=0;load();};
 $('#catalogModeRetry').onclick=bootstrap;$('#catalogFilters').onsubmit=e=>{e.preventDefault();offset=0;load();};$('#resetFilters').onclick=()=>{$('#catalogFilters').reset();offset=0;load();};$('#previousPage').onclick=()=>{offset=Math.max(0,offset-limit);load();};$('#nextPage').onclick=()=>{offset+=limit;load();};bootstrap();
})();
