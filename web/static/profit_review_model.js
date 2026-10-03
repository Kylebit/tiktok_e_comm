"use strict";
(() => {
 const fields=['schema_version','platform','site','shop_id','start','end','timezone','catalog_path','evidence_path','fx_path','coverage_path','knowledge_root','allow_temporary_cost_policy','ad_rate','fx_overrides','seller_sku_mapping','sku_evidence_path','sku_evidence_sha256','sku_price_basis'];
 const numeric=v=>(typeof v==='number'||typeof v==='string'&&v.trim()!=='')&&Number.isFinite(Number(v));
 const money=v=>numeric(v)?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'—';
 function validate(p){
  if(!p||typeof p!=='object'||Array.isArray(p))throw Error('配置必须是 JSON 对象');
  const extra=Object.keys(p).filter(k=>!fields.includes(k));if(extra.length)throw Error('不支持的配置字段：'+extra.join('、'));
  if(p.schema_version!=='profit-captured-profile/v1')throw Error('需要 profit-captured-profile/v1 配置');
  if(!['tiktok','shopee','ozon'].includes(p.platform)||typeof p.site!=='string'||!p.site.trim()||typeof p.shop_id!=='string'||!p.shop_id.trim())throw Error('平台、站点或店铺缺失');
  for(const k of ['start','end'])if(!/^\d{4}-\d{2}-\d{2}$/.test(p[k]||'')||!Number.isFinite(Date.parse(p[k]))||new Date(p[k]+'T00:00:00Z').toISOString().slice(0,10)!==p[k])throw Error('日期无效');
  if(p.end<p.start||!/^[-+]\d{2}:\d{2}$/.test(p.timezone||''))throw Error('期间或 UTC offset 无效');
  const [h,m]=p.timezone.slice(1).split(':').map(Number);if(h>23||m>59)throw Error('UTC offset 无效');
  for(const k of ['catalog_path','evidence_path','fx_path'])if(typeof p[k]!=='string'||!(/^[A-Za-z]:[\\/]/.test(p[k])||/^\/(?!\/)/.test(p[k])))throw Error('配置缺少本地绝对输入位置：'+k);
  for(const k of ['coverage_path','knowledge_root','sku_evidence_path'])if(k in p&&(typeof p[k]!=='string'||!(/^[A-Za-z]:[\\/]/.test(p[k])||/^\/(?!\/)/.test(p[k]))))throw Error('需要本地绝对位置：'+k);
  if('sku_evidence_sha256'in p&&!/^[a-f0-9]{64}$/.test(p.sku_evidence_sha256))throw Error('SKU 来源需要 SHA256 摘要');
  if('sku_price_basis'in p&&!['listing','recent_median'].includes(p.sku_price_basis))throw Error('SKU 估算价格口径无效');
  if('allow_temporary_cost_policy'in p&&typeof p.allow_temporary_cost_policy!=='boolean')throw Error('临时成本选择必须为布尔值');
  if('ad_rate'in p&&(!numeric(p.ad_rate)||Number(p.ad_rate)<0||Number(p.ad_rate)>1))throw Error('广告比例须在 0–100% 内');
  for(const k of ['fx_overrides','seller_sku_mapping'])if(k in p){const map=p[k];if(!map||typeof map!=='object'||Array.isArray(map))throw Error(k+' 必须为对象');for(const [key,v]of Object.entries(map)){if(['__proto__','constructor','prototype'].includes(key)||typeof v!=='string'&&typeof v!=='number')throw Error('映射字段无效');if(k==='fx_overrides'&&(!/^[A-Z]{3}$/.test(key)||!numeric(v)||Number(v)<=0))throw Error('FX 覆盖须为币种和正数');}}
  if(new TextEncoder().encode(JSON.stringify(p)).length>60000)throw Error('配置过大，请缩小映射范围');return JSON.parse(JSON.stringify(p));
 }
 function project(bundle,profile){
  if(bundle?.status==='check_failed')throw Error(bundle.error?.code||'captured_input_check_failed');
  for(const k of ['platform','site','shop_id','start','end','timezone'])if(bundle?.scope?.[k]!==profile[k])throw Error('返回范围与当前配置不符：'+k);
  const item=bundle.reports?.[profile.platform],report=item?.report;if(!report||!Array.isArray(report.order_lines))throw Error('报告结构不完整');
  const found=new Map();for(const [source,rows]of [['报告',report.quality_issues],['证据',bundle.quality_issues],['目录',bundle.catalog_quality_issues],['成本政策',bundle.cost_policy?.issues],['适配',item.adapter?.issues]])for(const r of rows||[]){const id=JSON.stringify([r.code,r.record_id||r.canonical_sku||'',r.field||'',r.message||'']);if(!found.has(id))found.set(id,{...r,sources:[]});found.get(id).sources.push(source);}
  const rows=report.order_lines,label=rows.length?(report.result_scope==='partial_diagnostic'?'部分数据诊断':report.status==='ready'&&bundle.status==='ready'?'本次复核可读':'待核 · 已计算行'):report.status==='no_data'?'期间无可计算记录':'待核 · 暂无可计算金额';
  const mode=report.calculation_kind==='realized_settlement_with_estimated_ads'?'已结算证据 + 估算广告':report.calculation_kind==='realized_settlement_with_actual_ads'?'已结算证据 + 实际广告':report.calculation_kind||'计算口径未标注';
  return {report,item,rows,problems:[...found.values()],label,mode,totals:rows.length?report.totals:{}};
 }
 function csv(rows,report,s,bundle){
  const columns=['report_id','status','calculation_kind','result_scope','platform','site','shop_id','start','end','timezone','order_id','order_line_id','seller_sku','platform_sku','currency','net_local','settlement_cny','cost_cny','advertising_cny','profit_cny','evidence_sha256','fx_sha256','cost_policy_snapshot','fx_rate_cny'];
  const cell=v=>{let text=v==null?'':String(v);if(/^\s*[=+@-]/.test(text))text="'"+text;return '"'+text.replaceAll('"','""')+'"';};
  return '\ufeff'+[columns,...rows.map(r=>[report.report_id,report.status,report.calculation_kind,report.result_scope,s.platform,s.site,s.shop_id,s.start,s.end,s.timezone,r.identity?.order_id,r.identity?.order_line_id,r.product?.seller_sku,r.product?.platform_sku,r.settlement?.currency,r.settlement?.net_amount_local,r.settlement?.net_amount_cny,r.cost?.total_cny,r.advertising?.amount_cny,r.profit_cny,bundle?.captured_inputs?.evidence_sha256,bundle?.captured_inputs?.fx_sha256,bundle?.cost_policy?.snapshot_id,r.fx?.rate_cny_per_local])].map(row=>row.map(cell).join(',')).join('\r\n');
 }
 globalThis.ProfitReviewModel={validate,money,numeric,project,csv};
})();
