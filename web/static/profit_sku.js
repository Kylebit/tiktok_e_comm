"use strict";
// Explicit local capture only. Each request clears the previous estimate first.
globalThis.ProfitSku = (() => {
 const $=id=>document.getElementById(id),money=v=>ProfitReviewModel.money(v);
 const node=(tag,value)=>{const e=document.createElement(tag);if(value!==undefined)e.textContent=value;return e;};
 let readProfile,run=0,abort=null,choices=[];
 const labels={listing_missing:'挂牌价未知',listing_invalid:'挂牌价来源无效',cost_unresolved:'成本缺失或冲突，未采用临时成本',fx_missing_or_changed:'FX 缺失或来源已变化',sample_evidence_invalid:'近单证据身份、期间或口径无效',samples_missing_or_empty:'近单样本缺失或为空',selected_price_unavailable:'所选估算价格未知',explicit_ad_rate_missing:'广告假设尚未明确'};
 function clear(message='输入已变更，请重新读取证据'){
  run++;abort?.abort();abort=null;choices=[];$('skuChoice').replaceChildren(new Option('先读取完整身份',''));$('skuBody').replaceChildren();$('skuStatus').textContent=message;$('skuRefresh').disabled=false;
 }
 function details(host,title,value){const d=node('details');d.append(node('summary',title),node('pre',JSON.stringify(value,null,2)));host.append(d);}
 function card(host,title,value,source){const e=node('section');e.append(node('h3',title),node('strong',value));if(source)e.append(node('small',source));host.append(e);return e;}
 const feeNames={goods_local:'商品成本',logistics_local:'物流',commission_local:'平台佣金',transaction_local:'交易费',extra_local:'额外平台费',creator_local:'达人佣金',affiliate_local:'联盟佣金',ad_local:'广告假设',seller_tax_local:'卖家税费',fixed_fee_local:'固定费用'};
 function waterfallRows(host,rows,currency){
  const wrap=node('div');wrap.className='fee-waterfall';let balance=null;const max=Math.max(1,...rows.map(r=>Math.abs(Number(r[1])||0)));
  for(const [label,value,kind='deduction']of rows){const row=node('div');row.className='fee-row';const known=ProfitReviewModel.numeric(value),v=known?Number(value):null;let from=balance,to=null;
   if(kind==='subtotal'||kind==='income'||kind==='result'){from=0;to=v;}else if(v!==null&&balance!==null)to=balance-v;
   balance=to;const meter=node('div');meter.className='fee-meter';if(from!==null&&to!==null){const bar=node('span');bar.className='fee-bar '+kind;const left=Math.max(0,Math.min(from,to))/max*100;bar.style.marginLeft=Math.min(left,100)+'%';bar.style.width=Math.min(Math.abs(to-from)/max*100,100-left)+'%';meter.append(bar);}
   row.append(node('span',label),node('strong',known?`${money(value)} ${currency}`:'未知'),meter);wrap.append(row);
  }host.append(wrap);
 }
 function renderWaterfall(host,b){
  const w=b.waterfall,area=node('div');area.id='skuWaterfall';host.append(area);area.append(node('h3','费用瀑布与样本对账'));
  if(!w||w.status==='missing'||w.status==='invalid'){area.append(node('p',w?.status==='invalid'?'费用来源无效或已过期，瀑布结果未知。':'未提供费用捕获证据；先验分项、联盟状态和样本费用未知。'));return;}
  area.append(node('p',`${w.source.source} · ${w.source.version} · 截至 ${w.source.as_of}`),node('p','达人佣金与联盟佣金为来源声明的独立、不重叠费用。先验和后验均为估算；样本观察不代表完整结算事实。'));
  for(const [key,title]of [['with_affiliate','有联盟情景'],['no_affiliate','无联盟情景']]){
   const p=w.prior[key],cardHost=card(area,'先验估算 · '+title,p?.estimate?`${money(p.estimate.profit_cny)} CNY / 件`:'未知 · 分项或必要输入不完整');cardHost.classList.add('prior-fee');cardHost.dataset.scenario=key;
   if(!p||p.status==='missing'){cardHost.append(node('p','来源没有提供此情景，不从另一组费用推导。'));continue;}
   if(p.status==='invalid'){cardHost.append(node('p','费用与价格、成本、广告、联盟或封顶证据矛盾，未调用模型。'));details(cardHost,'无效来源分项',p);continue;}
   cardHost.append(node('p',`额外费封顶：${p.extra_cap?.extra_cap_hit===true?'是':p.extra_cap?.extra_cap_hit===false?'否':'未知'}；仅校验捕获金额，不推测实时规则。`));
   const rows=[['选定销售额',p.sale_local,'income']];for(const key of ['logistics_local','commission_local','transaction_local','extra_local','creator_local','affiliate_local','seller_tax_local','fixed_fee_local']){const excluded=key==='seller_tax_local'&&p.include_tax===false;rows.push([feeNames[key]+(excluded?'（情景明确未计入）':''),excluded?0:p.components[key]]);}
   rows.push(['预估净结算',p.estimate?.est_settlement_local,'subtotal'],['商品成本',p.components.goods_local],['广告假设',p.components.ad_local],['先验利润',p.estimate?.profit_local,'result']);waterfallRows(cardHost,rows,w.currency);
   if(p.missing?.length)cardHost.append(node('p','缺失：'+p.missing.map(k=>feeNames[k]||k).join('、')));details(cardHost,'先验分项、封顶与模型回值',p);
  }
  const post=w.posterior;
  if(post?.status==='available'){
   area.append(node('p',`后验仅使用提供的已结样本；不主张完整覆盖，不作异常过滤。联盟状态未知 ${post.unknown_affiliate_count} 条，不并入无联盟组。`));
   for(const [key,title]of [['all','全部提供样本'],['with_affiliate','明确有联盟'],['no_affiliate','明确无联盟']]){const p=post[key],e=p.estimate,section=card(area,'后验估算 · '+title,e?`${money(e.profit_cny)} CNY / 件`:'未知 · 没有有效样本或模型输入',`样本 n=${p.n}；平台费已在净结算内，不再扣除。`);section.classList.add('posterior-fee');section.dataset.scenario=key;
    waterfallRows(section,[['选定销售额',p.sale_local,'income'],['销售与净结算差额（合并）',p.platform_net_difference_local],['预估净结算',e?.est_settlement_local,'subtotal'],['明确商品成本',p.goods_local],['广告假设',e?.ad_local],['后验利润',e?.profit_local,'result']],w.currency);
    section.append(node('p','合并差额不是逐项费用，不反推物流、佣金或税费。'));
   }
  }else area.append(node('p','后验估算未知：没有有效已结样本。'));
  const o=w.observed;
  if(o){const section=card(area,'已提供结算样本观察 · 非批准事实',`n=${o.sample_count} · 数量 ${o.quantity}`,o.note);section.id='skuFeeObserved';const rows=[['样本销售总额',o.gross_local,'income']];
   for(const [key,item]of Object.entries(o.components))rows.push([feeNames[key]+`（已知 ${item.known_rows}/${o.sample_count} 条）`+(item.amount===null&&item.known_subtotal!==null?` · 已知小计 ${money(item.known_subtotal)} ${w.currency}`:''),item.amount]);rows.push(['样本净结算总额',o.net_local,'subtotal']);waterfallRows(section,rows,w.currency);
   section.append(node('p',`对账：${o.reconciliation==='complete'?'完整分项吻合':o.reconciliation==='mismatch'?'完整分项存在差异':'部分费用未知'}；未解释差额 ${money(o.unexplained_delta_local)} ${w.currency}（不归类为费用）。`));
   details(section,'已知部分、小计和对账原文',o);
  }
  details(area,'费用来源、有效期和摘要',{source:w.source,sha256:w.sha256,identity:w.identity,scope:w.scope,model:b.model});
 }
 function render(b){
  const body=$('skuBody');body.replaceChildren();if(!b.identity)return;
  const heading=node('div');heading.className='sku-heading';const identity=node('div');identity.append(node('h3',b.name||'商品名称未知'),node('p',b.specification||'规格未知'),node('p',`${b.scope.platform} / ${b.scope.site} / ${b.identity.shop_key}`),node('p',`Product ${b.identity.product_id} · Variant ${b.identity.variant_id}`),node('strong',`Seller SKU ${b.identity.seller_sku}`));
  const picture=node('div');picture.className='sku-picture';if(b.image?.state==='available'){const img=node('img');img.alt=b.name||'本地商品图';img.src=b.image.data_url;img.onerror=()=>picture.replaceChildren(node('span','商品图未知：PNG 无法显示'));picture.append(img);}else picture.append(node('span','商品图未知：'+(b.image?.reason||'未提供')));heading.append(picture,identity);body.append(heading);
  const prices=node('div');prices.className='sku-grid';body.append(prices);
  card(prices,'挂牌价',b.listing?`${money(b.listing.amount)} ${b.listing.currency}`:'未知',b.listing?`${b.listing.source} · ${b.listing.version} · 截至 ${b.listing.as_of}`:'没有可核验的挂牌价');
  card(prices,'近单实付中位 / 每件',b.recent?.median!=null?`${money(b.recent.median)} ${b.recent.currency}`:'未知',b.recent?`${b.recent.source} · ${b.recent.version} · n=${b.recent.n||0} · ${b.scope.start} — ${b.scope.end} UTC${b.scope.timezone} · 截至 ${b.recent.as_of}`:'样本未知');
  body.append(node('p','近单价格只描述提供的已结样本，不代表全部市场或后验收益。'));
  const cost=card(body,'成本命中',b.cost?.selected?`${money(b.cost.selected.unit_cost_cny)} CNY / 件`:'未知 · 成本缺失或冲突',`命中路径 ${b.cost?.matching?.basis||'未知'} · 本地观察，未经事实批准；未记载有效日期的成本仅作估算输入。`);
  details(cost,'成本候选、冲突与版本',b.cost);
  const estimate=card(body,'纯估算 · 非结算事实',b.estimate?`${money(b.estimate.profit_cny)} CNY / 件`:'未知 · 必需输入不完整',b.model?.note);estimate.id='skuEstimate';
  if(b.model)estimate.append(node('p',`价格口径：${b.model.price_basis==='listing'?'挂牌价':b.model.price_basis==='recent_median'?'近单中位':'未选择'}；广告假设 ${b.model.ad_rate==null?'未知':money(b.model.ad_rate*100)+'%'}；FX ${b.fx?.selected_rate||'未知'} CNY/${b.fx?.selected_currency||'—'}`));
  if(b.gaps?.length)body.append(node('p','待核：'+b.gaps.map(k=>labels[k]||k).join('；')));
  renderWaterfall(body,b);
  ProfitSamples.render(body,b.sample_audit);
  details(body,'输入范围、价格 / 样本摘要与 FX', {scope:b.scope,sources:b.sources,listing:b.listing,recent:b.recent,fx:b.fx,image:{state:b.image?.state,sha256:b.image?.sha256}});
  details(body,'模型版本与完整估算结果',{evidence_id:b.evidence_id,approval_status:b.approval_status,model:b.model,estimate:b.estimate});
 }
 async function request(identity=null){
  const previous=choices;run++;const thisRun=run;abort?.abort();abort=new AbortController();$('skuBody').replaceChildren();$('skuStatus').textContent='正在核验所选本地证据…';$('skuRefresh').disabled=true;
  try{
   const profile=readProfile();const response=await fetch('/api/profit-center/captured-review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile,mode:'sku',sku_identity:identity}),signal:abort.signal,cache:'no-store'});const b=await response.json();if(thisRun!==run)return;
   if(!response.ok||b.status==='check_failed')throw Error(b.error?.code||`HTTP ${response.status}`);
   if(b.schema_version!=='profit-captured-sku/v1'||['platform','site','shop_id','start','end','timezone'].some(k=>b.scope?.[k]!==profile[k])||identity&&JSON.stringify(b.identity)!==JSON.stringify(identity))throw Error('返回身份或范围不一致');
   choices=b.choices||previous;$('skuChoice').replaceChildren(new Option('选择完整商品 / 变体身份',''));choices.forEach((c,i)=>$('skuChoice').add(new Option(`${c.name||'名称未知'} · ${c.identity.platform} / ${c.identity.shop_key} / ${c.identity.product_id} / ${c.identity.variant_id} / ${c.identity.seller_sku}`,String(i))));
   if(identity)$('skuChoice').value=String(choices.findIndex(c=>JSON.stringify(c.identity)===JSON.stringify(identity)));
   $('skuStatus').textContent=b.status==='estimate_available'?'证据已读取 · 仅供估算':b.identity?'证据已读取 · 存在待核输入':choices.length?'请选择完整身份；选择后核验价格、样本、成本和 FX':'当前范围没有完整身份';render(b);
  }catch(e){if(thisRun!==run)return;if(e.name!=='AbortError'){choices=[];$('skuChoice').replaceChildren(new Option('重新读取身份',''));$('skuStatus').textContent='读取失败，旧估算已失效：'+e.message;}}
  finally{if(thisRun===run){abort=null;$('skuRefresh').disabled=false;}}
 }
 function init(reader){readProfile=reader;$('skuEvidence').onclick=()=>{clear();$('skuDialog').showModal();request();};$('skuChoice').onchange=()=>{const c=choices[$('skuChoice').value];if(c)request(c.identity);else clear();};$('skuRefresh').onclick=()=>{const c=choices[$('skuChoice').value];request(c?.identity||null);};$('skuDialog').addEventListener('close',()=>clear());}
 return {init,invalidate:clear};
})();
