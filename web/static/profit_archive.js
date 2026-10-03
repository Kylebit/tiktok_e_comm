(()=>{
  'use strict';
  const $=id=>document.getElementById(id), reports=new Map(), MAX_BYTES=16*1024*1024;
  const text=value=>String(value??'').trim();
  async function digest(value){const bytes=new TextEncoder().encode(value),raw=await crypto.subtle.digest('SHA-256',bytes);return 'sha256:'+Array.from(new Uint8Array(raw),byte=>byte.toString(16).padStart(2,'0')).join('');}
  function validate(value){
    if(!value||typeof value!=='object'||Array.isArray(value))throw Error('顶层必须是 JSON 对象');
    const platform=text(value.platform).toLowerCase();
    if(!['tiktok','shopee','ozon'].includes(platform))throw Error('平台身份无效');
    if(!text(value.report_id))throw Error('缺少 report_id');
    if(!['monthly','weekly'].includes(text(value.period_kind)))throw Error('period_kind 无效');
    if(!value.period||!/^\d{4}-\d{2}-\d{2}$/.test(text(value.period.start))||!/^\d{4}-\d{2}-\d{2}$/.test(text(value.period.end)))throw Error('报告期间无效');
    if(value.period.end<value.period.start)throw Error('报告结束日期早于开始日期');
    if(!Array.isArray(value.order_lines)||!value.totals||typeof value.totals!=='object')throw Error('缺少订单行或合计');
    if(!text(value.calculation_kind)||!text(value.status))throw Error('缺少计算口径或状态');
    return structuredClone(value);
  }
  function scope(report){
    const sites=new Set(),shops=new Set();
    for(const line of report.order_lines){const identity=line&&typeof line==='object'?(line.identity||{}):{};if(text(identity.region))sites.add(text(identity.region));if(text(identity.shop_id))shops.add(text(identity.shop_id));}
    return {sites:[...sites].sort(),shops:[...shops].sort()};
  }
  function sourceIdentity(report){
    const source=report.source||{};
    return text(source.settlement_evidence_snapshot_id||source.snapshot_id||source.input_checksum||source.checksum);
  }
  function openReport(report){
    const identity={report_id:report.report_id,platform:report.platform,period_kind:report.period_kind,period:report.period,status:report.status,calculation_kind:report.calculation_kind,source_snapshot_or_checksum:sourceIdentity(report)};
    $('reportTitle').textContent=`${text(report.platform).toUpperCase()} · ${report.period.start} 至 ${report.period.end}`;
    $('reportEvidence').textContent=JSON.stringify(identity,null,2);
    const old=$('reportFrame'),frame=document.createElement('iframe');frame.id='reportFrame';frame.name='profit-report-'+crypto.randomUUID();frame.title='历史利润报表明细';frame.setAttribute('sandbox','allow-scripts');old.replaceWith(frame);
    $('reportDialog').showModal();
    const form=document.createElement('form');form.method='POST';form.action='/api/profit-center/report-view';form.target=frame.name;form.hidden=true;
    const input=document.createElement('input');input.name='report';input.value=JSON.stringify(report);form.append(input);document.body.append(form);form.submit();form.remove();
  }
  function render(){
    const host=$('reportRows');host.replaceChildren();
    const rows=[...reports.values()].sort((a,b)=>`${a.report.period.start}|${a.report.platform}|${a.report.report_id}|${a.digest}`.localeCompare(`${b.report.period.start}|${b.report.platform}|${b.report.report_id}|${b.digest}`));
    for(const entry of rows){const {report,digest}=entry;
      const card=document.createElement('article');card.className='report-card';const s=scope(report),source=sourceIdentity(report);
      const title=document.createElement('h3');title.textContent=`${text(report.platform).toUpperCase()} ${s.sites.join('/')||'站点待核'} · ${report.period.start} 至 ${report.period.end}`;
      const kind=document.createElement('p');kind.textContent=`${report.period_kind} · ${report.calculation_kind} · ${report.status} · ${report.order_lines.length} 个订单行`;
      const origin=document.createElement('p');origin.textContent=source?`来源快照/校验和：${source}`:'来源快照身份缺失，不能证明历史版本';if(!source)origin.className='source-warning';
      const id=document.createElement('p');id.textContent=`report_id：${report.report_id}`;
      const content=document.createElement('p');content.textContent=`文件内容摘要：${digest}`;
      const button=document.createElement('button');button.type='button';button.textContent='打开原订单级报表';button.onclick=()=>openReport(report);
      card.append(title,kind,origin,id,content,button);host.append(card);
    }
    $('reportCount').textContent=`${rows.length} 份`;$('emptyReports').hidden=rows.length>0;$('clearReports').disabled=rows.length===0;
  }
  $('reportFiles').addEventListener('change',async event=>{
    const errors=[],notices=[];let loaded=0,duplicates=0;
    for(const file of event.target.files){
      try{if(file.size>MAX_BYTES)throw Error('文件超过 16 MiB');const raw=await file.text(),report=validate(JSON.parse(raw)),contentDigest=await digest(raw);if(reports.has(contentDigest)){duplicates++;continue;}const versions=[...reports.values()].filter(entry=>text(entry.report.report_id)===text(report.report_id));if(versions.length)notices.push(`${file.name}：report_id ${report.report_id} 内容不同，已作为新版本保留`);reports.set(contentDigest,{report,digest:contentDigest,file_name:file.name});loaded++;}
      catch(error){errors.push(`${file.name}：${error.message}`);}
    }
    $('reportIssues').hidden=errors.length+notices.length===0;$('reportIssues').textContent=[...notices,...errors].join('\n');$('loadStatus').textContent=`本次导入 ${loaded} 份；相同内容跳过 ${duplicates} 份；拒绝 ${errors.length} 份。`;
    event.target.value='';render();
  });
  $('clearReports').onclick=()=>{reports.clear();$('loadStatus').textContent='已清空；文件内容未保存。';$('reportIssues').hidden=true;render();};
  $('closeReport').onclick=()=>$('reportDialog').close();
  render();
})();
