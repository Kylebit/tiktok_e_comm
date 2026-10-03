"use strict";
globalThis.ProfitSamples=(()=>{
 const M=ProfitReviewModel,node=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
 function histogram(input){
  const values=input.filter(M.numeric).map(Number),unknown=input.length-values.length;
  if(!values.length)return {bins:[],count:0,unknown,min:null,max:null};
  let min=Infinity,max=-Infinity;for(const v of values){min=Math.min(min,v);max=Math.max(max,v);}
  if(min===max)return {bins:[{lower:min,upper:max,upper_inclusive:true,count:values.length}],count:values.length,unknown,min,max};
  const n=Math.min(10,Math.max(2,Math.ceil(Math.sqrt(values.length)))),scale=Math.max(Math.abs(min),Math.abs(max),1),lo=min/scale,span=max/scale-lo;
  const edge=i=>i===0?min:i===n?max:(1-i/n)*min+(i/n)*max;
  const bins=Array.from({length:n},(_,i)=>({lower:edge(i),upper:edge(i+1),upper_inclusive:i===n-1,count:0}));
  for(const value of values){const index=Math.max(0,Math.min(n-1,Math.floor(((value/scale-lo)/span)*n)));bins[index].count++;}
  return {bins,count:values.length,unknown,min,max};
 }
 function render(host,a){
  const root=node('section');root.id='skuSampleAudit';root.append(node('h3','完整返回样本审计 · 仅估算'));host.append(root);
  if(!a||a.status!=='available'){root.append(node('p','没有可审阅的捕获样本数组；数量未知。'));return;}
  root.append(node('p',`${a.scope.platform} / ${a.scope.site} / ${a.scope.shop_id} · ${a.scope.start} — ${a.scope.end} UTC${a.scope.timezone} · 截至 ${a.source.as_of||'未知'}`),node('p','完整返回仅指当前捕获文件。筛选和分页只改变展示；上方估算保留原集合，异常标记不会自动剔除样本。派生利润使用当前成本、FX 和广告假设。'));
  const toolbar=node('div');toolbar.className='sample-controls';root.append(toolbar);
  function select(id,label,options){const l=node('label',label),s=node('select');s.id=id;for(const [v,t]of options)s.add(new Option(t,v));l.append(s);toolbar.append(l);return s;}
  const affiliate=select('sampleAffiliate','联盟',[['all','全部联盟状态'],['with','明确有联盟'],['without','明确无联盟'],['unknown','联盟未知']]);
  const profit=select('sampleProfit','单件利润',[['all','全部利润状态'],['negative','负利润'],['zero','零利润'],['positive','正利润'],['unknown','利润未知']]);
  const outlier=select('sampleOutlier','异常规则',[['all','全部异常状态'],['true','规则异常'],['false','规则非异常'],['unknown','异常未知']]);
  const eligible=select('sampleEligible','证据',[['all','全部证据'],['true','合格记录'],['false','未采用 / 待核']]);
  const label=node('label','订单 / 来源搜索'),search=node('input');search.type='search';search.id='sampleSearch';label.append(search);toolbar.append(label);
  const counts=node('p');counts.id='sampleCounts';counts.setAttribute('role','status');counts.setAttribute('aria-live','polite');root.append(counts);
  const chart=node('div');chart.id='sampleHistogram';root.append(chart);
  root.append(node('p','表格按捕获顺序显示；窄屏可在表格容器内横向滚动查看完整列。'));
  const scroll=node('div');scroll.className='sample-scroll';scroll.tabIndex=0;scroll.setAttribute('aria-label','样本表格，可在容器内横向滚动');const table=node('table'),head=node('thead'),hr=node('tr');
  for(const text of ['捕获序号 / 日期','完整订单号','数量','实付总额 / 单件','净结算总额 / 单件','联盟','单件利润 CNY','异常 / 参与估算','来源 / 原文'])hr.append(node('th',text));head.append(hr);table.append(head);const body=node('tbody');body.id='sampleRows';table.append(body);scroll.append(table);root.append(scroll);
  const nav=node('div');nav.className='sample-pages';const prev=node('button','上一页'),next=node('button','下一页'),position=node('span');prev.id='samplePrev';next.id='sampleNext';position.id='samplePage';nav.append(prev,position,next);root.append(nav);
  const raw=node('details'),summary=node('summary','选中样本的完整身份、原始记录与缺失原因'),pre=node('pre');raw.id='sampleDetail';raw.append(summary,pre);root.append(raw);
  const source=node('details');source.append(node('summary','样本来源、异常规则和模型版本'),node('pre',JSON.stringify({source:a.source,scope:a.scope,identity:a.identity,capture_sha256:a.capture_sha256,sample_sha256:a.sample_sha256,outlier_rule:a.outlier_rule,model_sha256:a.model_sha256,assumptions:a.assumptions},null,2)));root.append(source);
  let page=1;const size=10;
  function draw(){
   const query=search.value.trim().toLowerCase(),filtered=a.rows.filter(r=>(affiliate.value==='all'||r.affiliate_state===affiliate.value)&&(profit.value==='all'||r.profit_class===profit.value)&&(outlier.value==='all'||(r.outlier===null?'unknown':String(r.outlier))===outlier.value)&&(eligible.value==='all'||String(r.eligible)===eligible.value)&&(!query||[r.order_id,r.source,r.source_version].join(' ').toLowerCase().includes(query)));
   const pages=Math.max(1,Math.ceil(filtered.length/size));page=Math.min(Math.max(page,1),pages);const shown=filtered.slice((page-1)*size,page*size);
   counts.textContent=`原始捕获 ${a.counts.captured} · 结算窗内 ${a.counts.settled_in_window} · 证据合格 ${a.counts.eligible} · 完整返回 ${a.counts.returned} · 筛选后 ${filtered.length} · 当前页 ${shown.length}`;
   counts.dataset.counts=JSON.stringify({...a.counts,filtered:filtered.length,current_page:shown.length,page,pages});body.replaceChildren();pre.textContent='';raw.open=false;
   for(const r of shown){const tr=node('tr');tr.dataset.rowKey=r.row_key;tr.dataset.orderId=typeof r.order_id==='string'?r.order_id:'';
    for(const value of [`#${r.capture_index+1} / ${r.settled_at||'日期未知'}`,typeof r.order_id==='string'?r.order_id:'订单身份未知',r.quantity??'未知',`${M.money(r.paid_total_local)} / ${M.money(r.paid_per_item_local)} ${r.currency||''}`,`${M.money(r.net_total_local)} / ${M.money(r.net_per_item_local)} ${r.currency||''}`,{with:'有',without:'无',unknown:'未知'}[r.affiliate_state],r.profit_cny===null?'未知':M.money(r.profit_cny),`${r.outlier===null?'异常未知':r.outlier?'规则异常':'规则非异常'} / ${r.used_in_base_estimate?'参与上方估算':'未参与上方估算'}`])tr.append(node('td',value));
    const td=node('td',r.source||'来源未知'),button=node('button','查看原文');button.onclick=()=>{pre.textContent=JSON.stringify(r,null,2);raw.open=true;raw.scrollIntoView({block:'nearest'});};td.append(button);tr.append(td);body.append(tr);
   }
   if(!shown.length){const tr=node('tr'),td=node('td','没有符合条件的返回样本');td.colSpan=9;tr.append(td);body.append(tr);}
   prev.disabled=page===1;next.disabled=page===pages;position.textContent=`第 ${page} / ${pages} 页 · 每页 ${size} 条`;
   const h=histogram(filtered.map(r=>r.profit_cny));chart.dataset.histogram=JSON.stringify(h);chart.replaceChildren(node('h4','当前筛选的单件利润分布 · CNY'),node('p',`使用筛选后的全部 ${filtered.length} 条，不仅当前页；有限利润 ${h.count} 条，未知排除 ${h.unknown} 条。区间左闭右开，最后一桶包含右端点。`));
   if(!h.count)chart.append(node('p','没有可绘制的有限利润；不绘制默认零。'));
   const largest=Math.max(1,...h.bins.map(b=>b.count)),bound=v=>Number(v.toPrecision(8)).toString();for(const b of h.bins){const row=node('div');row.className='sample-bin';row.append(node('span',`${b.lower===b.upper?'值':'区间'} [${bound(b.lower)}, ${bound(b.upper)}${b.upper_inclusive?']':')'}`),node('strong',`${b.count} 条`));const bar=node('div');bar.className='sample-bin-track';const fill=node('span');fill.style.width=(b.count/largest*100)+'%';fill.className=b.upper<0?'loss':b.lower>=0?'gain':'mixed';bar.append(fill);row.append(bar);chart.append(row);}
  }
  for(const control of [affiliate,profit,outlier,eligible])control.onchange=()=>{page=1;draw();};search.oninput=()=>{page=1;draw();};prev.onclick=()=>{page--;draw();};next.onclick=()=>{page++;draw();};draw();
 }
 return {render,histogram};
})();
