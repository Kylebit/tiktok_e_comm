(() => {
  'use strict';
  const base = '/api/product-workspace/round1-category/';
  const storageKey = 'orbit-r1-category-operations-v1';
  let data = null, region = '', context = null, options = null, selected = null;
  let receipt = null, prepared = null, generation = 0, busy = false, dirty = false;
  let serverPreparedReference = null;
  let message = '请选择用于读取官方类目的账号地区。', host = {}, activeOperation = null;
  const selections = new Map();
  const stockPolicyReady = packet => {
    const policy = packet?.publication_stock_policy;
    return policy && Object.getPrototypeOf(policy) === Object.prototype
      && Object.keys(policy).sort().join('|') === 'quantity_per_sku|review_round|schema_version|scope|source'
      && policy.schema_version === 'publication-default-stock/v1'
      && policy.quantity_per_sku === 200
      && policy.scope === 'EACH_SELECTED_SKU'
      && policy.source === 'SYSTEM_GOVERNED_DEFAULT'
      && policy.review_round === 'ROUND1';
  };
  const reviewReady = () => prepared?.packet?.status === 'FIRST_REVIEW_READY'
    && !(prepared.packet.blockers || []).length && stockPolicyReady(prepared.packet);
  const el = (tag, text, attrs = {}) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  };
  const identity = () => JSON.stringify([data?.product?.offer_id, data?.product?.revision,
    data?.publication_scope?.selected_labels || [], region]);
  const enabled = () => !!data && (!!data.round1_prepared_review || (data.publication_scope?.selected_labels || []).some(t => t.startsWith('shopee:')));
  const records = () => {
    try { const rows = JSON.parse(localStorage.getItem(storageKey) || '[]'); return Array.isArray(rows) ? rows : []; }
    catch { return []; }
  };
  const uncertain = () => records().some(op => op.identity === identity()
    && op.context_digest === context?.context_digest && op.account_identity_digest === context?.source_account?.account_identity_digest
    && (!op.result || ['UNKNOWN','IN_PROGRESS','NOT_STARTED'].includes(op.result.status)));
  function remember(op) {
    const rows = records().filter(row => row.id !== op.id);
    // Retain previous scopes for explicit reconciliation; never silently evict UNKNOWN.
    const stored = {...op};
    if (op.result) stored.result = Object.fromEntries(['status','code','progress','options_reference','observer_reference','prepared_reference']
      .filter(key=>Object.hasOwn(op.result,key)).map(key=>[key,op.result[key]]));
    rows.push(stored); localStorage.setItem(storageKey, JSON.stringify(rows));
  }
  function invalidate(text, resetRegion = false) {
    generation += 1; context = options = selected = receipt = prepared = null;
    activeOperation = null; busy = false; selections.clear();
    if (resetRegion) region = '';
    message = text; draw();
  }
  function scope() {
    return {offer_id: String(data.product.offer_id), product_center_revision: data.product.revision,
      requested_targets: [...data.publication_scope.selected_labels], source_region: region};
  }
  const token = () => ({generation, identity: identity()});
  const current = t => t.generation === generation && t.identity === identity();
  async function json(url, body) {
    const response = await fetch(url, body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {});
    const value = await response.json();
    return {response, value};
  }
  async function loadContext() {
    if (!enabled() || !region || dirty) return;
    const t = token(); busy = true; message = '正在读取本地账号与类目记录…'; draw();
    try {
      const query = new URLSearchParams({...scope(), requested_targets:JSON.stringify(scope().requested_targets)});
      const {response, value} = await json(base + 'context?' + query);
      if (!current(t)) return;
      if (!response.ok || !value.context_digest) throw Error(value.code || '本地上下文不可用');
      context = value;
      message = value.source_account?.readiness === 'READY' ? '账号已就绪；请点击读取候选，再自行选择类目。' : '账号尚未就绪；新读取不可用。';
    } catch (error) { if (current(t)) message = String(error.message); }
    finally { if (current(t)) { busy = false; draw(); } }
    if (current(t) && context) {
      const previous = records().filter(op => op.identity === identity() && op.purpose !== 'RESOLVE').at(-1)
        || records().filter(op => ['PREPARE','APPROVE','FREEZE'].includes(op.purpose)
          && op.scope.offer_id === data.product.offer_id && op.scope.source_region === region).at(-1);
      if (previous) await reconcile(previous);
    }
  }
  function common(schema) {
    return {...scope(), schema_version:schema, request_id:crypto.randomUUID(),
      context_digest:context.context_digest, account_identity_digest:context.source_account.account_identity_digest};
  }
  function selectedAttributes() {
    if (!selected) return null;
    const rows = [];
    for (const attr of selected.attributes) {
      const value = selections.get(attr.attribute_identity_digest);
      if (attr.kind === 'TEXT') {
        if (!String(value || '').trim()) { if (attr.required) return null; continue; }
        rows.push({attribute_identity_digest:attr.attribute_identity_digest, text_value:String(value)});
      } else {
        const values = Array.isArray(value) ? value : [];
        if (!values.length) { if (attr.required) return null; continue; }
        if (attr.kind === 'SINGLE_SELECT' && values.length !== 1) return null;
        rows.push({attribute_identity_digest:attr.attribute_identity_digest, option_identity_digests:values});
      }
    }
    return rows;
  }
  function consume(op, value) {
    activeOperation = {...op, result:value};
    if (value.status === 'SUCCEEDED' && op.purpose === 'OPTIONS' && value.options) {
      options = value.options; selected = receipt = prepared = null; selections.clear();
      message = '候选已读取；尚未选择类目。';
    } else if (value.status === 'SUCCEEDED' && op.purpose === 'CAPTURE' && value.observer_reference) {
      receipt = value.receipt; prepared = null; message = '分类凭据已保存，尚未准备完整首轮审核。';
    } else if (op.purpose === 'RESOLVE' && value.status === 'RESOLVED' && value.receipt) {
      receipt = value.receipt; prepared = null; message = '历史分类凭据已在本地核对；账号就绪状态保持不变。';
    } else if (op.purpose === 'PREPARE' && value.status === 'PREPARED') {
      prepared = value; message = '完整首轮内容已准备。请查看审核内容，再使用现有批准按钮。';
    } else if (['PREPARE','APPROVE','FREEZE'].includes(op.purpose) && ['APPROVED','APPROVED_NOT_FROZEN','FROZEN'].includes(value.status)) {
      prepared = value; message = value.status === 'FROZEN' && value.persisted_readback ? '首轮快照已落盘并读回。' : '批准已保存，快照尚未确认落盘；请先只读核对。';
    } else {
      message = `${value.status || 'UNKNOWN'}：${value.code || '结果需只读核对'}`;
      if (value.code === 'RECHECK_REQUIRED') { options = selected = receipt = prepared = null; selections.clear(); }
    }
  }
  async function operation(purpose, path, body) {
    if (busy || dirty || uncertain()) return;
    const t = token();
    const op = {id:body.request_id || crypto.randomUUID(), purpose, path, body, scope:scope(), identity:identity(),
      prepare_request_id:prepared?.request_id,
      context_digest:context?.context_digest, account_identity_digest:context?.source_account?.account_identity_digest};
    try { remember(op); } catch { message = '无法保存操作身份，未发送请求。'; draw(); return; }
    if (purpose === 'OPTIONS') { options = selected = receipt = prepared = null; selections.clear(); }
    if (purpose === 'CAPTURE') { receipt = prepared = null; }
    if (purpose === 'PREPARE') prepared = null;
    activeOperation = op; busy = true; message = '正在处理；请勿重复提交。'; draw();
    try {
      const {response, value} = await json(path, body);
      if (!value.status) value.status = value.ok === false && response.status < 500 ? 'FAILED' : 'UNKNOWN';
      remember({...op, result:value});
      if (!current(t) || activeOperation?.id !== op.id) return;
      consume(op, value);
      if (value.dashboard && ['APPROVED_NOT_FROZEN','FROZEN'].includes(value.status)) {
        host.applyDashboard?.(value.dashboard);
        region = op.scope.source_region; prepared = value; busy = false;
        message = value.status === 'FROZEN' ? '首轮快照已落盘并读回。' : '批准已保存，快照尚未确认；请只读核对。';
        draw();
      }
      if (!response.ok && !value.status) message = value.code || '请求失败，请核对';
    } catch (error) {
      const result = {status:'UNKNOWN', code:'RESPONSE_OR_RENDER_UNCONFIRMED'};
      try { remember({...op, result}); } catch { /* The pre-send identity is durable. */ }
      if (current(t)) { activeOperation = {...op, result}; prepared = null; message = '响应或页面更新未确认；请只读核对，勿重新提交。'; }
    } finally { if (current(t)) { busy = false; draw(); } }
  }
  async function reconcile(op) {
    if (busy) return;
    const t = token(); busy = true; message = '正在只读核对原操作…'; draw();
    try {
      const preparation = ['PREPARE','APPROVE','FREEZE'].includes(op.purpose);
      const requestId = op.purpose === 'PREPARE' ? op.body.request_id : op.prepare_request_id;
      if (preparation && !requestId) throw Error('缺少原准备操作身份，无法核对');
      const query = new URLSearchParams({offer_id:op.scope.offer_id, request_id:preparation ? requestId : op.id});
      const {value} = op.purpose === 'RESOLVE' ? await json(base+'resolve',op.body)
        : await json(base + (preparation ? 'prepare-status?' : 'capture-status?') + query);
      remember({...op,result:value});
      if (!current(t)) return;
      // An old operation remains inspectable but cannot bind another current scope.
      const recoveredApproval = preparation && ['APPROVED','FROZEN'].includes(value.status)
        && value.current_revision === data.product.revision && op.scope.offer_id === data.product.offer_id
        && op.scope.source_region === region && JSON.stringify(op.scope.requested_targets) === JSON.stringify(scope().requested_targets)
        && (!context?.source_account?.account_identity_digest || op.account_identity_digest === context.source_account.account_identity_digest);
      if (!recoveredApproval && (op.identity !== identity() || (context && (op.context_digest !== context.context_digest || op.account_identity_digest !== context.source_account?.account_identity_digest)))) {
        message = `旧范围核对结果：${value.status || 'UNKNOWN'}；未绑定当前商品。`; return;
      }
      consume(op, value);
    } catch (error) { if (current(t)) message = '核对失败：' + error.message; }
    finally { if (current(t)) { busy = false; draw(); } }
  }
  function action(text, callback, disabled = false) {
    const button = el('button', text, {type:'button',class:'button button-secondary'}); button.disabled = disabled;
    button.addEventListener('click', callback); return button;
  }
  function draw() {
    let panel = document.querySelector('#round1Category');
    if (!panel) {
      const facts = document.querySelector('#pane-facts'); if (!facts) return;
      panel = el('section', undefined, {id:'round1Category', class:'r1-category'}); facts.append(panel);
    }
    panel.hidden = !enabled(); if (!enabled()) return;
    panel.replaceChildren(el('h3','首轮类目与审核快照'));
    if(data.frozen_review_projection && data.frozen_first_review){
      const frozen=data.frozen_first_review;
      panel.append(el('p',`首轮事实已批准并冻结 · revision ${frozen.approved_revision}。当前沿用已冻结类目；目标店铺实时条件仍需发布前核对。`,{role:'status','data-r1-status':'FROZEN'}));
      const table=el('table');table.className='review-table';
      const head=el('tr');['平台','冻结类目','来源依据'].forEach(text=>head.append(el('th',text)));table.append(head);
      for(const category of frozen.platform_categories||[]){
        const row=el('tr');[category.platform,`${category.category_id||'ID 待补'} · ${category.category_zh||category.category_en||'类目资料缺失'}`,category.authority||'来源待核'].forEach(text=>row.append(el('td',text)));table.append(row);
      }
      const wrap=el('div');wrap.className='table-scroll';wrap.append(table);panel.append(wrap);
      const details=el('details');details.append(el('summary','冻结快照与类目完整依据'),el('pre',JSON.stringify({snapshot_digest:frozen.snapshot_digest,categories:frozen.platform_categories},null,2)));panel.append(details);
      return;
    }
    const label = el('label','官方类目账号地区 '), select = el('select', undefined, {'aria-label':'官方类目账号地区'});
    for (const value of ['', 'MY','PH','TH','VN']) { const option = el('option', value || '请选择地区', {value}); option.selected = region === value; select.append(option); }
    select.disabled = dirty;
    select.addEventListener('change', () => { region = select.value; invalidate('地区已变更，旧选择已失效。'); loadContext(); });
    label.append(select); panel.append(label);
    panel.append(el('p',message,{role:'status','aria-live':'polite','data-r1-status':prepared?.status || activeOperation?.result?.status || 'IDLE'}));
    if (activeOperation?.result?.progress) {
      const p = activeOperation.result.progress;
      panel.append(el('p',`官方读取：尝试 ${p.attempted ?? '未知'} / 返回 ${p.completed ?? '未知'} · ${p.stage || ''}`));
    }
    const unresolved = uncertain();
    panel.append(action('刷新本地上下文', () => { invalidate('正在刷新本地上下文…'); loadContext(); }, busy || dirty || !region));
    panel.append(action('读取类目候选', () => operation('OPTIONS', base+'options', common('round1-category-options-request/v1')),
      busy || dirty || unresolved || context?.source_account?.readiness !== 'READY'));
    if (options) {
      const fieldset = el('fieldset'); fieldset.append(el('legend','选择类目（推荐不代表已选择）'));
      for (const item of options.options) {
        const row = el('label'), radio = el('input', undefined, {type:'radio',name:'r1-category-choice',value:item.category_identity_digest});
        radio.checked = selected?.category_identity_digest === item.category_identity_digest;
        radio.disabled = busy || dirty;
        radio.addEventListener('change', () => {
          selected = item; receipt = prepared = null; selections.clear();
          message = '已选择类目；请补齐必填属性后保存分类凭据。'; draw();
        });
        row.append(radio, document.createTextNode(item.path.map(p=>p.name).join(' / ') + (item.recommended ? '（推荐）' : ''))); fieldset.append(row);
      }
      panel.append(fieldset);
    }
    if (selected) {
      for (const attr of selected.attributes) {
        const field = el('fieldset'); field.append(el('legend',attr.label + (attr.required ? ' *' : '（可选）')));
        if (attr.kind === 'TEXT') {
          const input = el('input',undefined,{type:'text','aria-label':attr.label}); input.value = selections.get(attr.attribute_identity_digest) || '';
          input.disabled = busy || dirty;
          input.addEventListener('input', () => { selections.set(attr.attribute_identity_digest,input.value); receipt = prepared = null; updateButtons(); }); field.append(input);
        } else {
          for (const value of attr.values) {
            const label = el('label'), input = el('input',undefined,{type:attr.kind==='MULTI_SELECT'?'checkbox':'radio',name:attr.attribute_identity_digest,value:value.option_identity_digest});
            input.checked = (selections.get(attr.attribute_identity_digest) || []).includes(value.option_identity_digest); input.disabled = busy || dirty;
            input.addEventListener('change', () => {
              const values = attr.kind==='MULTI_SELECT' ? new Set(selections.get(attr.attribute_identity_digest)||[]) : new Set();
              if(input.checked) values.add(value.option_identity_digest); else values.delete(value.option_identity_digest);
              selections.set(attr.attribute_identity_digest,[...values]); receipt = prepared = null; updateButtons();
            });
            label.append(input,document.createTextNode(value.label + (value.unit ? ' '+value.unit : ''))); field.append(label);
          }
        }
        panel.append(field);
      }
      const capture = action('保存分类凭据', () => operation('CAPTURE',base+'capture', {...common('round1-category-capture-request/v3'),
        options_reference:options.options_reference,options_digest:options.options_digest,
        selected_category_identity:selected.category_identity_digest,attribute_selections:selectedAttributes()}));
      capture.id = 'r1Capture'; panel.append(capture);
    }
    if (receipt) panel.append(action('准备完整首轮审核', () => {
      const body = {...scope(),account_identity_digest:receipt.account_identity_digest,
        context_digest:context.context_digest,observer_reference:receipt.observer_reference,request_id:crypto.randomUUID()};
      return operation('PREPARE',base+'prepare',body);
    },busy||dirty||unresolved));
    for (const row of context?.observations || []) {
      panel.append(action(`复用历史凭据 · ${row.category?.name || row.status}`,()=>operation('RESOLVE',base+'resolve',{
        ...scope(),account_identity_digest:row.account_identity_digest,observer_reference:row.observer_reference}),
        busy || dirty || row.status !== 'REUSABLE'));
    }
    if (prepared?.packet) {
      const details = el('details'), summary = el('summary','查看本次完整审核内容'); details.append(summary,el('pre',JSON.stringify(prepared.packet,null,2))); panel.append(details);
      if (prepared.status === 'PREPARED') {
        panel.append(el('p',`本次确认：商品 ${data.product.offer_id} · revision ${data.product.revision} · ${(data.publication_scope?.selected_labels || []).join('、')}。确认后冻结当前完整首轮内容。`));
        if(!reviewReady())panel.append(el('p','首轮资料尚未完整，需执行者补齐类目、文案或图片方案后再审核。'));
        panel.append(action('批准当前首轮事实并冻结快照',()=>window.OrbitRound1Category.approve(),busy||dirty||uncertain()||!reviewReady()));
      }
      if (prepared.status === 'APPROVED') panel.append(action('完成已批准快照的技术落盘',()=>operation('FREEZE',base+'freeze',{
        offer_id:scope().offer_id,prepared_reference:prepared.prepared_reference}),busy||dirty));
    }
    const history = records().filter(op=>op.scope?.offer_id === data.product.offer_id);
    if (history.length) {
      const details = el('details'); details.append(el('summary','原操作只读核对'));
      for (const op of history) details.append(action(`${op.purpose} · ${op.result?.status || '响应待核对'} · ${op.id.slice(0,8)}`,()=>reconcile(op),busy));
      panel.append(details);
    }
    updateButtons();
  }
  function updateButtons() {
    const capture = document.querySelector('#r1Capture'); if (capture) capture.disabled = busy || dirty || uncertain() || selectedAttributes() === null;
    const approval = document.querySelector('#approvalButton');
    if (enabled() && approval) {
      approval.disabled = busy || dirty || uncertain() || prepared?.status !== 'PREPARED' || !reviewReady();
      if (prepared?.status === 'PREPARED') {
        const label = approval.querySelector('.button-label');
        if (label) label.textContent = '批准当前首轮事实并冻结快照';
      }
    }
  }
  window.OrbitRound1Category = {
    active:enabled,
    setHost(value) { host = value; },
    render(value) {
      const old = identity(); data = value;
      if (identity() !== old) { dirty=false; invalidate('商品或已保存范围已变更，请选择地区重新核对。',true); }
      const restored=value.round1_prepared_review;
      if (serverPreparedReference && !restored) { prepared=null; serverPreparedReference=null; }
      if (!busy && !dirty && restored?.ok === true && restored.prepared_reference && restored.packet
          && ['PREPARED','APPROVED','FROZEN'].includes(restored.status)
          && restored.current_revision === value.product?.revision) {
        prepared=restored;
        serverPreparedReference=restored.prepared_reference;
        message=restored.status==='PREPARED'?(reviewReady()?'完整首轮内容已从当前商品的审核记录恢复。请检查后确认。':'已恢复当前首轮准备资料；内容尚未齐备，暂不能批准。'):'当前首轮批准记录已核对。';
      }
      draw();
    },
    dirty(value) { dirty=!!value; if(dirty) invalidate('存在未保存事实，类目选择与审核引用已失效。'); else draw(); },
    reset() {data=null;invalidate('请选择商品。',true);},
    updateButtons,
    async approve() {
      if (!prepared || prepared.status !== 'PREPARED' || !reviewReady() || dirty || busy) return;
      const body={offer_id:scope().offer_id,prepared_reference:prepared.prepared_reference,
        approved_by:prepared.approval_actor,user_approved:true};
      await operation('APPROVE','/api/product-workspace/approve',body);
    },
  };
})();
