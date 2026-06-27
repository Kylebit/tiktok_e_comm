/**
 * TikTok MY → Ozon 链接同步（与 ozon/CLAUDE.md 一致）
 * draft / process_images 用 seller_sku（6位）；migrate 用 offer_id（4位）
 */
(function (global) {
  'use strict';

  var unmigratedCache = [];
  var taskTimer = null;
  var taskStartedAt = 0;
  var categoryOptionsCache = null;
  var typeProfilesCache = {};

  var MATCH_METHOD_LABELS = {
    title_tablecloth: '标题识别桌布',
    tk_category_map: 'TK→Ozon 映射表',
    rule_auto: '规则自动匹配',
    rule_narrow_ai: 'AI 从候选中选择'
  };

  var BUILTIN_TYPE_PROFILES = {
    '91971': 'sticker',
    '115946973': 'frame',
    '92692': 'tablecloth'
  };

  function esc(s) {
    if (s == null) return '';
    var d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  function api(path, opts) {
    opts = opts || {};
    return fetch('/api/ozon/' + path.replace(/^\//, ''), {
      method: opts.method || 'GET',
      headers: opts.body ? { 'Content-Type': 'application/json' } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (r) {
      return r.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = { error: text.slice(0, 200) }; }
        if (!r.ok) throw new Error((data && data.error) || text.slice(0, 120) || ('HTTP ' + r.status));
        return data;
      });
    });
  }

  function elapsedSec() {
    return Math.floor((Date.now() - taskStartedAt) / 1000);
  }

  function stopTaskTimer() {
    if (taskTimer) { clearInterval(taskTimer); taskTimer = null; }
  }

  function startTaskTimer(titlePrefix) {
    stopTaskTimer();
    taskStartedAt = Date.now();
    taskTimer = setInterval(function () {
      var el = document.getElementById('oz-task-title');
      if (el && el.dataset.running === '1') {
        el.innerHTML = '<span class="oz-spinner"></span>' + titlePrefix + '（已等待 ' + elapsedSec() + ' 秒，请勿关闭页面）';
      }
    }, 1000);
  }

  /** @param {'running'|'ok'|'err'} state */
  function showTaskBanner(state, title, steps, hint) {
    var banner = document.getElementById('oz-task-banner');
    var titleEl = document.getElementById('oz-task-title');
    var stepsEl = document.getElementById('oz-task-steps');
    var hintEl = document.getElementById('oz-task-hint');
    if (!banner) return;
    banner.className = state;
    titleEl.dataset.running = state === 'running' ? '1' : '0';
    if (state === 'running') {
      titleEl.innerHTML = '<span class="oz-spinner"></span>' + title;
    } else {
      stopTaskTimer();
      titleEl.textContent = title;
    }
    stepsEl.innerHTML = (steps || []).map(function (s) {
      return '<li class="' + esc(s.cls) + '">' + esc(s.text) + '</li>';
    }).join('');
    hintEl.textContent = hint || '';
    banner.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function hideTaskBannerLater(ms) {
    setTimeout(function () {
      var banner = document.getElementById('oz-task-banner');
      if (banner && banner.className === 'ok') banner.style.display = 'none';
    }, ms || 8000);
  }

  function setRowBusy(idx, busy) {
    var tr = document.querySelector('#unmig-table tbody tr[data-idx="' + idx + '"]');
    if (!tr) return;
    tr.classList.toggle('row-busy', !!busy);
    var btn = tr.querySelector('[data-action="draft"]');
    if (btn) btn.textContent = busy ? '生成中…' : '生成草稿';
  }

  function log(el, msg, cls) {
    if (!el) return;
    var line = document.createElement('div');
    line.className = 'oz-log-line' + (cls ? ' ' + cls : '');
    line.textContent = msg;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
  }

  function setGroupBusy(groupId, busy) {
    document.querySelectorAll('#unmig-table tbody tr.group-hdr[data-group="' + groupId + '"] button').forEach(function (b) {
      b.disabled = !!busy;
      if (busy) b.textContent = '搬运中…';
      else b.textContent = '整组搬运';
    });
  }

  function countGroups(items) {
    var s = {};
    items.forEach(function (it) {
      if (it.tk_group_id) s[it.tk_group_id] = true;
    });
    return Object.keys(s).length;
  }

  function itemsInGroup(groupId) {
    return unmigratedCache.filter(function (it) {
      return it.tk_group_id === groupId && !it.tk_dup;
    });
  }

  function loadCategoryOptions() {
    if (categoryOptionsCache) {
      return Promise.resolve(categoryOptionsCache);
    }
    return api('category_options').then(function (data) {
      categoryOptionsCache = (data && data.options) ? data.options : (data || []);
      typeProfilesCache = (data && data.type_profiles) ? data.type_profiles : {};
      return categoryOptionsCache;
    });
  }

  function resolveProfileForType(typeId, keepCurrent) {
    var key = String(typeId);
    var mapped = typeProfilesCache[key] || BUILTIN_TYPE_PROFILES[key];
    if (mapped && mapped !== 'generic') return mapped;
    return keepCurrent || 'generic';
  }

  function findCategoryOption(typeId) {
    var tid = parseInt(typeId, 10);
    if (!tid || !categoryOptionsCache) return null;
    for (var i = 0; i < categoryOptionsCache.length; i++) {
      if (categoryOptionsCache[i].type_id === tid) return categoryOptionsCache[i];
    }
    return null;
  }

  function buildCategorySelectHtml(selectedTypeId) {
    var groups = {};
    (categoryOptionsCache || []).forEach(function (o) {
      var g = o.cat_name_zh || o.cat_name || '其他';
      if (!groups[g]) groups[g] = [];
      groups[g].push(o);
    });
    var html = '<select class="f-cat-select cat-select" size="6">';
    html += '<option value="">— 选择 Ozon 类目 —</option>';
    Object.keys(groups).sort().forEach(function (grp) {
      html += '<optgroup label="' + esc(grp) + '">';
      groups[grp].forEach(function (o) {
        var val = o.cat_id + '|' + o.type_id;
        var label = (o.type_name_zh || o.type_name || ('type ' + o.type_id));
        var sel = o.type_id === parseInt(selectedTypeId, 10) ? ' selected' : '';
        html += '<option value="' + esc(val) + '"' + sel + '>' + esc(label) + '</option>';
      });
      html += '</optgroup>';
    });
    html += '</select>';
    return html;
  }

  function matchMethodLabel(method) {
    return MATCH_METHOD_LABELS[method] || method || '—';
  }

  function bindCategoryEditor(card, draft) {
    var select = card.querySelector('.f-cat-select');
    var filter = card.querySelector('.f-cat-filter');
    var profileSel = card.querySelector('.f-profile');
    var catInput = card.querySelector('.f-cat');
    var typeInput = card.querySelector('.f-type');

    function updateNameLabels(catId, typeId) {
      var opt = findCategoryOption(typeId);
      var catNameEl = card.querySelector('.f-cat-name');
      var typeNameEl = card.querySelector('.f-type-name');
      if (opt) {
        catNameEl.textContent = opt.cat_name_zh || opt.cat_name || '—';
        typeNameEl.textContent = opt.type_name_zh || opt.type_name || '—';
        catNameEl.title = catNameEl.textContent;
        typeNameEl.title = typeNameEl.textContent;
      } else {
        catNameEl.textContent = catId ? ('cat ' + catId) : '—';
        typeNameEl.textContent = typeId ? ('type ' + typeId) : '—';
      }
    }

    function applyTypeSelection(catId, typeId, syncProfile) {
      catInput.value = catId;
      typeInput.value = typeId;
      updateNameLabels(catId, typeId);
      if (select) {
        var want = String(catId) + '|' + String(typeId);
        if (select.value !== want) {
          var found = false;
          for (var i = 0; i < select.options.length; i++) {
            if (select.options[i].value === want) {
              select.selectedIndex = i;
              found = true;
              break;
            }
          }
          if (!found) select.value = '';
        }
      }
      if (syncProfile && profileSel) {
        var prof = resolveProfileForType(typeId, profileSel.value);
        profileSel.value = prof;
        card.dataset.migrateProfile = prof;
      }
    }

    if (select) {
      select.onchange = function () {
        if (!select.value) return;
        var parts = select.value.split('|');
        applyTypeSelection(parts[0], parts[1], true);
      };
    }

    if (filter && select) {
      filter.oninput = function () {
        var q = filter.value.trim().toLowerCase();
        Array.prototype.forEach.call(select.options, function (opt) {
          if (!opt.value) {
            opt.hidden = false;
            return;
          }
          var text = (opt.textContent || '').toLowerCase();
          var grp = opt.parentElement && opt.parentElement.label ? opt.parentElement.label.toLowerCase() : '';
          opt.hidden = q ? (text.indexOf(q) < 0 && grp.indexOf(q) < 0) : false;
        });
        Array.prototype.forEach.call(select.querySelectorAll('optgroup'), function (og) {
          var anyVisible = false;
          Array.prototype.forEach.call(og.children, function (opt) {
            if (!opt.hidden) anyVisible = true;
          });
          og.hidden = !anyVisible;
        });
      };
    }

    if (profileSel) {
      profileSel.onchange = function () {
        card.dataset.migrateProfile = profileSel.value;
      };
    }

    function syncFromManualIds() {
      applyTypeSelection(catInput.value, typeInput.value, true);
    }
    catInput.onchange = syncFromManualIds;
    typeInput.onchange = syncFromManualIds;

    applyTypeSelection(draft.category_id, draft.type_id, false);
  }

  function loadUnmigrated() {
    var tbody = document.querySelector('#unmig-table tbody');
    var countEl = document.getElementById('unmig-count');
    tbody.innerHTML = '<tr><td colspan="9">加载中…</td></tr>';
    return api('unmigrated').then(function (items) {
      unmigratedCache = items || [];
      var gc = countGroups(unmigratedCache);
      countEl.textContent = '共 ' + unmigratedCache.length + ' 个待搬运' +
        (gc ? '（' + gc + ' 组多规格）' : '');
      tbody.innerHTML = '';
      if (!unmigratedCache.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="meta">暂无待搬运商品</td></tr>';
        return unmigratedCache;
      }
      var lastGroup = '';
      unmigratedCache.forEach(function (it, idx) {
        if (it.tk_group_id && it.tk_group_id !== lastGroup) {
          var gkeys = (it.tk_group_keys || []).join('–');
          var gcount = it.tk_group_size || (it.tk_group_keys || []).length;
          var hdr = document.createElement('tr');
          hdr.className = 'group-hdr';
          hdr.setAttribute('data-group', it.tk_group_id);
          hdr.innerHTML =
            '<td colspan="9">' +
            '多规格组 · ' + esc(gkeys) + ' · ' + esc(gcount) + ' 个 SKU ' +
            '<button type="button" class="btn btn-sm secondary" data-action="batch-group" data-group="' +
            esc(it.tk_group_id) + '">整组搬运</button></td>';
          tbody.appendChild(hdr);
          lastGroup = it.tk_group_id;
        } else if (!it.tk_group_id) {
          lastGroup = '';
        }
        var tr = document.createElement('tr');
        if (it.tk_dup) tr.className = 'row-warn';
        else if (it.tk_group_id) tr.className = 'group-member';
        var specCell = it.variant_label
          ? '<span class="variant-tag">' + esc(it.variant_label) + '</span>'
          : '<span class="meta">—</span>';
        var priceHint = it.price_preview_cny ? ' · ¥' + esc(it.price_preview_cny) : '';
        tr.innerHTML =
          '<td><input type="checkbox" class="unmig-check" data-idx="' + idx + '"></td>' +
          '<td><img class="thumb" src="' + esc(it.image) + '" alt=""></td>' +
          '<td><code>' + esc(it.offer_id) + '</code></td>' +
          '<td><code>' + esc(it.seller_sku || '') + '</code></td>' +
          '<td>' + specCell + priceHint + '</td>' +
          '<td class="tk-id" title="' + esc(it.tk_id) + '">' + esc((it.tk_id || '').slice(0, 12)) +
          (it.tk_dup ? ' ⚠重复' : '') + '</td>' +
          '<td>' + esc((it.title || '').slice(0, 44)) + '</td>' +
          '<td class="num">' + esc(it.image_count) + '</td>' +
          '<td><button type="button" class="btn btn-sm" data-action="draft" data-idx="' + idx + '">生成草稿</button></td>';
        tr.setAttribute('data-idx', idx);
        tbody.appendChild(tr);
      });
      return unmigratedCache;
    }).catch(function (e) {
      tbody.innerHTML = '<tr><td colspan="9" class="err">' + esc(e.message) + '</td></tr>';
      throw e;
    });
  }

  /** 把一个草稿数据 d 渲染进 card 并绑定按钮。
   *  opts.preProcessed: 已裁好的图片数组（来自待审队列）；有则直接展示+启用提交，不自动裁图。
   *  无 preProcessed 时（新生成草稿）自动触发裁图。 */
  function buildDraftCard(card, d, sellerSku, offerId, opts) {
    opts = opts || {};
    var matchHint = d.tk_category_path
      ? ('TK: ' + esc(d.tk_category_path) + ' · 匹配: ' + esc(matchMethodLabel(d.category_match_method)) +
        (d.category_match_score != null ? '（得分 ' + esc(String(d.category_match_score)) + '）' : '') +
        ' · 建议 <strong>' + esc(d.type_name_zh || d.type_id) + '</strong>')
      : '未识别 TikTok 类目，请手动选择 Ozon 类目';
    var profile = d.migrate_profile || 'generic';

    card.innerHTML =
      '<div class="card draft-card-body">' +
      '<h3>草稿 · offer_id <code>' + esc(offerId) + '</code> · seller_sku <code>' + esc(sellerSku) + '</code></h3>' +
      '<p class="meta">售价 ¥' + esc(d.price) + ' / 划线 ¥' + esc(d.old_price) +
      ' · 草稿来源: ' + esc(d.source || '') + '</p>' +
      '<p class="meta">AI: ' + (d.deepseek_used ? '✅ DeepSeek 已调用' : '⚠️ 未调用 API（规则兜底）') +
      ' · 标题 ' + esc(d.title_source || '—') + ' · 描述 ' + esc(d.desc_source || '—') +
      (d.weight_source === 'logistics'
        ? ' · 重量 物流实测 ' + esc(d.weight) + 'g（' + esc(String(d.logistics_package_count || 0)) + ' 单）'
        : ' · 重量 ' + esc(d.weight_source || '模板')) + '</p>' +
      (d.price_label ? '<p class="meta">售价来源: ' + esc(d.price_label) + ' → ¥' + esc(d.price) + '</p>' : '') +
      (d.variant_label ? '<p class="meta">规格: <span class="variant-tag">' + esc(d.variant_label) + '</span>' +
        (d.tk_group_keys && d.tk_group_keys.length ? ' · 组 ' + esc(d.tk_group_keys.join('–')) : '') + '</p>' : '') +
      '<p class="meta">原标题(MS): ' + esc(d.title_ms) + '</p>' +
      '<div class="imgs orig-imgs">' + (d.images || []).map(function (u) {
        return '<img src="' + esc(u) + '" alt="">';
      }).join('') + '</div>' +
      '<div class="grid draft-grid">' +
      '<label>俄语标题</label><textarea class="f-title" rows="2">' + esc(d.draft_title) + '</textarea>' +
      '<label>俄语描述</label><textarea class="f-desc">' + esc(d.draft_description) + '</textarea>' +
      '<label>价格 CNY</label><input class="f-price" value="' + esc(d.price) + '">' +
      '<label>划线价 CNY</label><input class="f-old-price" value="' + esc(d.old_price) + '">' +
      '<label>Ozon 类目</label>' +
      '<div class="cat-editor">' +
      '<div class="cat-match-box">' + matchHint +
      '<br><span class="warn">提交前请核对类目；匹配不准时可搜索或改选下方列表</span></div>' +
      '<input type="search" class="f-cat-filter cat-filter" placeholder="搜索类目中文名…">' +
      buildCategorySelectHtml(d.type_id) +
      '<label style="font-weight:600;font-size:12px;color:#555;padding-top:0">属性模板 profile</label>' +
      '<select class="f-profile cat-select" style="height:auto">' +
      ['sticker', 'tablecloth', 'frame', 'generic'].map(function (p) {
        return '<option value="' + p + '"' + (p === profile ? ' selected' : '') + '>' + p + '</option>';
      }).join('') +
      '</select>' +
      '<div class="cat-ids-row">' +
      '<span>category_id</span><input class="f-cat" value="' + esc(d.category_id) + '">' +
      '<span class="id-name f-cat-name">' + esc(d.category_name_zh || '—') + '</span>' +
      '<span>type_id</span><input class="f-type" value="' + esc(d.type_id) + '">' +
      '<span class="id-name f-type-name">' + esc(d.type_name_zh || '—') + '</span>' +
      '</div></div>' +
      '<label>颜色 / 字典ID</label><div class="row2"><input class="f-color-name" value="' + esc(d.color_name) + '"><input class="f-color-dict" value="' + esc(d.color_dict_id) + '"></div>' +
      '<label>材质 / 字典ID</label><div class="row2"><input class="f-material" value="' + esc(d.material) + '"><input class="f-material-dict" value="' + esc(d.material_dict_id) + '"></div>' +
      '<label>Hashtags</label><input class="f-hashtags" value="' + esc(d.hashtags || '') + '">' +
      '<label>套装 kit</label><input class="f-kit" value="' + esc(d.kit) + '">' +
      '<label>重量(g)</label><input class="f-weight" value="' + esc(d.weight) + '">' +
      '<label>长×宽×高 mm</label><div class="row3"><input class="f-depth" value="' + esc(d.depth) + '"><input class="f-width" value="' + esc(d.width) + '"><input class="f-height" value="' + esc(d.height) + '"></div>' +
      '<label>长×宽 cm</label><div class="row2"><input class="f-len-cm" value="' + esc(d.len_cm) + '"><input class="f-wid-cm" value="' + esc(d.wid_cm) + '"></div>' +
      '</div>' +
      '<div class="toolbar" style="margin-top:12px">' +
      '<button type="button" class="btn secondary btn-process-images">↻ 重新转换图片 3:4（约30s/张）</button>' +
      '<button type="button" class="btn btn-submit" disabled>② 提交 Ozon（请先核对后再点）</button>' +
      '<button type="button" class="btn secondary btn-dismiss">忽略</button>' +
      '<span class="status draft-status"></span></div>' +
      '<div class="imgs processed-imgs"></div></div>';

    card.dataset.sellerSku = sellerSku;
    card.dataset.offerId = offerId;
    card.dataset.tkId = d.tk_id || '';
    card.dataset.images = JSON.stringify(d.images || []);
    card.dataset.processed = '';
    card.dataset.migrateProfile = profile;
    card.dataset.tkCategoryId = d.tk_category_id || '';
    card.dataset.tkCategoryLeaf = d.tk_category_leaf || '';

    bindCategoryEditor(card, d);

    card.querySelector('.btn-process-images').onclick = function () { processImages(card); };
    card.querySelector('.btn-submit').onclick = function () { submitMigrate(card); };
    card.querySelector('.btn-dismiss').onclick = function () { dismissDraft(card); };

    var pre = opts.preProcessed;
    if (pre && pre.length) {
      // 来自待审队列：图片已裁好，直接展示并启用提交，不重复裁图
      card.dataset.processed = JSON.stringify(pre);
      card.querySelector('.processed-imgs').innerHTML = pre.map(function (u) {
        return '<img src="' + esc(u) + '" alt="">';
      }).join('');
      card.querySelector('.draft-status').textContent = '✅ 已就绪，共 ' + pre.length + ' 张图（待审）';
      card.querySelector('.btn-submit').disabled = false;
    } else if (d.images && d.images.length) {
      // 新生成草稿：自动裁图
      processImages(card);
    }
  }

  function dismissDraft(card) {
    if (!confirm('忽略该产品？将从待搬运列表永久排除，之后不会再生成草稿/上品（可在后端 dismissed_offers.json 撤销）。')) return;
    var sellerSku = card.dataset.sellerSku;
    var tkId = card.dataset.tkId || '';
    api('dismiss', { method: 'POST', body: { seller_sku: sellerSku, tk_id: tkId, reason: 'manual dismiss' } })
      .then(function () { loadUnmigrated(); })
      .catch(function () {});
    card.parentNode.removeChild(card);
  }

  /** 打开 /ozon 时加载 agent 预生成的待审草稿，渲染成卡片供逐个审核。 */
  function loadPendingDrafts() {
    var area = document.getElementById('draft-area');
    if (!area) return Promise.resolve();
    return Promise.all([api('pending_drafts'), loadCategoryOptions()]).then(function (res) {
      var drafts = (res[0] && res[0].drafts) || [];
      drafts.forEach(function (d) {
        var sellerSku = d.seller_sku || '';
        if (!sellerSku) return;
        if (area.querySelector('.draft-card[data-seller-sku="' + sellerSku + '"]')) return;
        var card = document.createElement('div');
        card.className = 'draft-card';
        card.dataset.sellerSku = sellerSku;
        area.appendChild(card);
        buildDraftCard(card, d, sellerSku, d.offer_id || '', { preProcessed: d.processed_images || [] });
      });
      return drafts;
    }).catch(function () { /* 无待审或接口缺失时忽略 */ });
  }

  function openDraft(idx) {
    var it = unmigratedCache[idx];
    if (!it) return;
    var sellerSku = it.seller_sku || it.offer_id;
    var area = document.getElementById('draft-area');

    // 多 SKU 审核：同一 seller_sku 已有卡片则聚焦，不重复生成
    var existing = area.querySelector('.draft-card[data-seller-sku="' + sellerSku + '"]');
    if (existing) {
      existing.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return Promise.resolve();
    }

    var card = document.createElement('div');
    card.className = 'draft-card';
    card.dataset.sellerSku = sellerSku;
    area.appendChild(card);
    setRowBusy(idx, true);

    showTaskBanner('running', '正在生成草稿 ' + sellerSku, [
      { cls: 'active', text: '① 读取商品目录 + TikTok 类目/价格' },
      { cls: 'pending', text: '② DeepSeek 生成俄语文案 + 匹配 Ozon 类目（约 30–90 秒）' },
      { cls: 'pending', text: '③ 渲染草稿表单' }
    ], '这是同步任务，不是后台运行；完成前请保持本页打开，不要重复点击。');

    startTaskTimer('正在生成草稿 ' + sellerSku);

    card.innerHTML =
      '<div class="card draft-loading">' +
      '<p><span class="oz-spinner"></span><strong>生成草稿中</strong> · <code>' + esc(sellerSku) + '</code></p>' +
      '<p class="meta">正在拉 TikTok 价格/类目并调用 AI 写俄语标题，通常 30–90 秒…</p>' +
      '<ol class="oz-task-steps">' +
      '<li class="active">读取商品目录</li><li class="pending">AI 生成文案</li><li class="pending">完成</li>' +
      '</ol></div>';
    card.scrollIntoView({ behavior: 'smooth', block: 'start' });

    return Promise.all([
      api('draft/' + encodeURIComponent(sellerSku)),
      loadCategoryOptions()
    ]).then(function (results) {
      var d = results[0];
      if (d.error && !d.draft_title) throw new Error(d.error);
      var offerId = d.offer_id || it.offer_id;

      showTaskBanner('ok', '✅ 草稿已生成 · ' + sellerSku + '（耗时 ' + elapsedSec() + ' 秒）', [
        { cls: 'done', text: '① 目录 + TK 数据' },
        { cls: 'done', text: '② AI 文案 · ' + (d.deepseek_used ? 'DeepSeek ✓' : '规则兜底') +
          ' · 标题 ' + (d.title_source || '—') + ' · 描述 ' + (d.desc_source || '—') },
        { cls: 'done', text: '③ 请核对类目/文案后：转换图片 → 提交 Ozon' }
      ], '类目、标题、价格等可在下方修改后再提交');
      hideTaskBannerLater(12000);

      buildDraftCard(card, d, sellerSku, offerId, {});
      card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }).catch(function (e) {
      stopTaskTimer();
      showTaskBanner('err', '❌ 草稿失败 · ' + sellerSku, [
        { cls: 'fail', text: e.message || '未知错误' }
      ], '可检查网络、DeepSeek 配置，或稍后重试');
      card.innerHTML = '<div class="card err"><strong>草稿失败 · ' + esc(sellerSku) + '</strong><p>' + esc(e.message) + '</p></div>';
    }).finally(function () {
      setRowBusy(idx, false);
    });
  }

  function processImages(card) {
    var sellerSku = card.dataset.sellerSku;
    var btn = card.querySelector('.btn-process-images');
    var status = card.querySelector('.draft-status');
    var images = JSON.parse(card.dataset.images || '[]');
    btn.disabled = true;
    showTaskBanner('running', '图片处理中 · ' + sellerSku, [
      { cls: 'done', text: '① 草稿已完成' },
      { cls: 'active', text: '② 下载并裁剪上传 ' + images.length + ' 张（约 30 秒/张）' },
      { cls: 'pending', text: '③ 提交 Ozon' }
    ], '预计 ' + Math.ceil(images.length * 0.5) + '–' + (images.length * 2) + ' 分钟，请勿关闭页面');
    startTaskTimer('图片处理中 · ' + sellerSku);
    status.textContent = '处理中…';
    return api('process_images/' + encodeURIComponent(sellerSku), {
      method: 'POST',
      body: { images: images }
    }).then(function (d) {
      card.dataset.processed = JSON.stringify(d.images || []);
      card.querySelector('.processed-imgs').innerHTML = (d.images || []).map(function (u) {
        return '<img src="' + esc(u) + '" alt="">';
      }).join('');
      status.textContent = '✅ 图片完成，共 ' + (d.images || []).length + ' 张（' + elapsedSec() + ' 秒）';
      showTaskBanner('ok', '✅ 图片处理完成 · ' + sellerSku, [
        { cls: 'done', text: '① 草稿' },
        { cls: 'done', text: '② 图片 ' + (d.images || []).length + ' 张' },
        { cls: 'active', text: '③ 可点「提交 Ozon」' }
      ], '');
      hideTaskBannerLater(8000);
      if (!card.classList.contains('done')) {
        card.querySelector('.btn-submit').disabled = !(d.images && d.images.length);
      }
    }).catch(function (e) {
      stopTaskTimer();
      showTaskBanner('err', '❌ 图片失败 · ' + sellerSku, [{ cls: 'fail', text: e.message }], '');
      status.textContent = '图片失败: ' + e.message;
    }).finally(function () {
      if (!card.classList.contains('done')) btn.disabled = false;
    });
  }

  function submitMigrate(card) {
    var offerId = card.dataset.offerId;
    var status = card.querySelector('.draft-status');
    var images = JSON.parse(card.dataset.processed || '[]');
    if (!images.length) {
      alert('请先完成图片转换');
      return Promise.resolve();
    }
    var val = function (sel) { var el = card.querySelector(sel); return el ? el.value : ''; };
    var payload = {
      offer_id: offerId,
      images: images,
      title: val('.f-title'),
      description: val('.f-desc'),
      price: val('.f-price'),
      old_price: val('.f-old-price'),
      color_name: val('.f-color-name'),
      color_dict_id: val('.f-color-dict'),
      material: val('.f-material'),
      material_dict_id: val('.f-material-dict'),
      hashtags: val('.f-hashtags'),
      kit: val('.f-kit'),
      weight: val('.f-weight'),
      depth: val('.f-depth'),
      width: val('.f-width'),
      height: val('.f-height'),
      len_cm: val('.f-len-cm'),
      wid_cm: val('.f-wid-cm'),
      category_id: val('.f-cat'),
      type_id: val('.f-type'),
      migrate_profile: val('.f-profile') || card.dataset.migrateProfile || 'generic',
      tk_category_id: card.dataset.tkCategoryId || '',
      tk_category_leaf: card.dataset.tkCategoryLeaf || ''
    };
    var submitBtn = card.querySelector('.btn-submit');
    status.textContent = '提交中…';
    showTaskBanner('running', '提交 Ozon · offer ' + offerId, [
      { cls: 'done', text: '① 草稿' },
      { cls: 'done', text: '② 图片' },
      { cls: 'active', text: '③ Ozon import + 富内容（约 1–3 分钟）' }
    ], '请勿关闭页面');
    startTaskTimer('提交 Ozon · ' + offerId);
    submitBtn.disabled = true;
    return api('migrate', { method: 'POST', body: payload }).then(function (d) {
      var ok = d.import_ok || (d.status === 'imported' && (!d.errors || !d.errors.length));
      status.textContent = (ok ? '✅ ' : '⚠ ') + 'status=' + d.status +
        (d.rich_status ? ' rich=' + d.rich_status : '') +
        (d.errors && d.errors.length ? ' ' + JSON.stringify(d.errors) : '') +
        ' · ' + elapsedSec() + ' 秒';
      showTaskBanner(ok ? 'ok' : 'err', (ok ? '✅ Ozon 上品成功 · ' : '⚠ 提交异常 · ') + offerId, [
        { cls: 'done', text: 'status=' + d.status + (d.rich_status ? ' rich=' + d.rich_status : '') }
      ], ok ? '已从待搬运列表移除' : '见下方详情或历史 Tab');
      if (ok) {
        hideTaskBannerLater(10000);
        card.classList.add('done');
        var pbtn = card.querySelector('.btn-process-images');
        if (pbtn) pbtn.disabled = true;
        submitBtn.disabled = true;
        submitBtn.textContent = '✅ 已上品 offer_id=' + offerId;
        // 上品成功 → 从待审队列删除该草稿（若存在）
        api('pending_drafts/delete', { method: 'POST', body: { seller_sku: card.dataset.sellerSku } }).catch(function () {});
        loadUnmigrated();  // 刷新左侧待搬运表移除该行；不影响其它卡片
      } else {
        submitBtn.disabled = false;
      }
    }).catch(function (e) {
      stopTaskTimer();
      showTaskBanner('err', '❌ 提交失败 · ' + offerId, [{ cls: 'fail', text: e.message }], '');
      status.textContent = '提交失败: ' + e.message;
      submitBtn.disabled = false;
    });
  }

  /** 单条全自动：draft → images → migrate */
  function migrateOne(it, logEl) {
    var sellerSku = it.seller_sku || it.offer_id;
    if (it.tk_dup) {
      log(logEl, '跳过 ' + sellerSku + '（offer_id/tk_id 真重复）', 'warn');
      return Promise.resolve({ ok: false, skipped: true });
    }
    var label = it.variant_label ? ' [' + it.variant_label + ']' : '';
    log(logEl, '▶ ' + sellerSku + label + ' 生成草稿…');
    return api('draft/' + encodeURIComponent(sellerSku)).then(function (draft) {
      if (draft.error) throw new Error(draft.error);
      var offerId = draft.offer_id || it.offer_id;
      log(logEl, '  图片处理 ' + (draft.images || []).length + ' 张…');
      return api('process_images/' + encodeURIComponent(sellerSku), {
        method: 'POST',
        body: { images: draft.images || [] }
      }).then(function (proc) {
        if (!proc.images || !proc.images.length) throw new Error('图片处理失败');
        log(logEl, '  提交 Ozon offer_id=' + offerId + '…');
        return api('migrate', {
          method: 'POST',
          body: {
            offer_id: offerId,
            images: proc.images,
            title: draft.draft_title || '',
            description: draft.draft_description || '',
            price: String(draft.price || '45'),
            old_price: String(draft.old_price || '62'),
            color_name: draft.color_name || '',
            color_dict_id: draft.color_dict_id || '',
            material: draft.material || '',
            material_dict_id: draft.material_dict_id || '',
            hashtags: draft.hashtags || '',
            kit: draft.kit || '',
            weight: draft.weight || '',
            depth: draft.depth || '',
            width: draft.width || '',
            height: draft.height || '',
            len_cm: draft.len_cm || '',
            wid_cm: draft.wid_cm || '',
            category_id: draft.category_id || '',
            type_id: draft.type_id || '',
            migrate_profile: draft.migrate_profile || 'generic',
            tk_category_id: draft.tk_category_id || '',
            tk_category_leaf: draft.tk_category_leaf || '',
          }
        }).then(function (res) {
          if (res.status === 'skipped_duplicate') {
            log(logEl, '⚠ ' + offerId + ' ' + (res.error || 'skipped_duplicate'), 'warn');
            return { ok: false, offer_id: offerId, result: res, skipped: true };
          }
          var ok = res.import_ok || (res.status === 'imported' && (!res.errors || !res.errors.length));
          var msg = offerId + ' ' + res.status;
          if (res.reset && res.reset.action === 'deleted') {
            msg += ' (已删旧卡:' + (res.reset.detail || '') + ')';
          }
          if (res.errors && res.errors.length) msg += ' ' + JSON.stringify(res.errors);
          if (res.error) msg += ' ' + res.error;
          log(logEl, (ok ? '✅ ' : '❌ ') + msg, ok ? 'ok' : 'err');
          return { ok: ok, offer_id: offerId, result: res };
        });
      });
    }).catch(function (e) {
      log(logEl, '❌ ' + sellerSku + ' ' + e.message, 'err');
      return { ok: false, error: e.message };
    });
  }

  function batchMigrateGroup(groupId) {
    var queue = itemsInGroup(groupId);
    if (!queue.length) {
      alert('该组没有可搬运的规格');
      return Promise.resolve();
    }
    var logEl = document.getElementById('batch-log');
    logEl.style.display = 'block';
    var plan = queue.map(function (it) {
      return (it.offer_id || '') + (it.variant_label ? ' ' + it.variant_label : '');
    }).join('\n');
    if (!confirm('确认整组搬运 ' + queue.length + ' 个规格？\n\n' + plan)) return Promise.resolve();

    setGroupBusy(groupId, true);
    log(logEl, '—— 整组 ' + groupId + '（' + queue.length + ' 个）——', 'ok');
    var chain = Promise.resolve();
    queue.forEach(function (it, i) {
      chain = chain.then(function () {
        log(logEl, '[' + (i + 1) + '/' + queue.length + ']');
        return migrateOne(it, logEl).then(function (r) {
          return new Promise(function (resolve) {
            setTimeout(function () { resolve(r); }, 2000);
          });
        });
      });
    });
    return chain.then(function () {
      log(logEl, '整组完成', 'ok');
      setGroupBusy(groupId, false);
      return loadUnmigrated();
    }).catch(function () {
      setGroupBusy(groupId, false);
    });
  }

  function batchMigrate(count) {
    var logEl = document.getElementById('batch-log');
    logEl.style.display = 'block';
    logEl.innerHTML = '';
    var checked = [];
    document.querySelectorAll('.unmig-check:checked').forEach(function (c) {
      checked.push(unmigratedCache[parseInt(c.dataset.idx, 10)]);
    });
    var queue = checked.length ? checked : unmigratedCache.slice(0, count);
    queue = queue.filter(function (it) { return !it.tk_dup; }).slice(0, count);
    if (!queue.length) {
      alert('没有可搬运的商品（请勾选或确保列表非空）');
      return Promise.resolve();
    }
    var plan = queue.map(function (it) {
      return (it.seller_sku || it.offer_id) + ' → offer ' + it.offer_id;
    }).join('\n');
    if (!confirm('确认批量搬运 ' + queue.length + ' 个？\n\n' + plan)) return Promise.resolve();

    log(logEl, '开始批量 ' + queue.length + ' 个（串行，预计 ' + Math.ceil(queue.length * 4) + ' 分钟）…');
    var chain = Promise.resolve();
    queue.forEach(function (it, i) {
      chain = chain.then(function () {
        log(logEl, '—— [' + (i + 1) + '/' + queue.length + '] ——');
        return migrateOne(it, logEl).then(function (r) {
          return new Promise(function (resolve) {
            setTimeout(function () { resolve(r); }, 2000);
          });
        });
      });
    });
    return chain.then(function () {
      log(logEl, '批量完成', 'ok');
      return loadUnmigrated();
    });
  }

  /** 勾选多个 → 串行生成多张草稿卡片（各自自动裁图），全部停在等待人工「提交 Ozon」，不自动提交。*/
  function draftSelected() {
    var idxs = [];
    document.querySelectorAll('.unmig-check:checked').forEach(function (c) {
      var it = unmigratedCache[parseInt(c.dataset.idx, 10)];
      if (it && !it.tk_dup) idxs.push(parseInt(c.dataset.idx, 10));
    });
    if (!idxs.length) {
      alert('请先勾选要生成草稿的 SKU');
      return Promise.resolve();
    }
    // 串行：避免 N×6 张图并发裁剪把机器打满
    var chain = Promise.resolve();
    idxs.forEach(function (idx) {
      chain = chain.then(function () { return openDraft(idx); });
    });
    return chain;
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function loadSettlementSummary() {
    var area = document.getElementById('settlement-area');
    var months = parseInt(document.getElementById('settle-months').value, 10) || 3;
    if (!area) return;
    area.innerHTML = '<p class="meta">查询中…</p>';
    return api('settlement_summary?months=' + months).then(function (d) {
      var html = '';
      html += '<p class="meta">数据区间：' + escapeHtml(d.date_from) + ' ~ ' + escapeHtml(d.date_to) +
        '　已完整结算 ' + d.settled_count + ' 单　未完结(仅扣收单费) ' + d.pending_count + ' 单' +
        '　已结算订单净额合计 <strong>' + d.settled_net_total + ' RUB</strong>' +
        '　全部流水净额合计 <strong>' + d.grand_total + ' RUB</strong></p>';

      html += '<table class="oz"><thead><tr><th>费用类型</th><th>笔数</th><th>合计(RUB)</th></tr></thead><tbody>';
      (d.fee_breakdown || []).forEach(function (f) {
        html += '<tr><td>' + escapeHtml(f.type_name) + '</td><td>' + f.count + '</td><td>' + f.total + '</td></tr>';
      });
      html += '</tbody></table>';

      html += '<h4 style="margin:16px 0 4px">按订单明细</h4>';
      html += '<table class="oz"><thead><tr><th>posting</th><th>商品</th><th>状态</th><th>净额(RUB)</th></tr></thead><tbody>';
      (d.orders || []).forEach(function (o) {
        html += '<tr><td>' + escapeHtml(o.posting_number) + '</td><td>' + escapeHtml((o.products || []).join('; ')) +
          '</td><td>' + (o.settled ? '已结算' : '未完结') + '</td><td>' + o.net_amount + '</td></tr>';
      });
      html += '</tbody></table>';
      area.innerHTML = html;
    }).catch(function (e) {
      area.innerHTML = '<p class="meta" style="color:#c33">查询失败：' + escapeHtml(e.message || e) + '</p>';
    });
  }

  function loadProfitTable() {
    var statusEl = document.getElementById('profit-status');
    var summaryEl = document.getElementById('profit-summary');
    var tbody = document.querySelector('#profit-table tbody');
    var marginPct = parseFloat(document.getElementById('profit-target-margin').value) || 5;
    if (!tbody) return;
    statusEl.textContent = '查询中(几秒到十几秒)…';
    tbody.innerHTML = '';
    return api('profit_table?target_margin=' + (marginPct / 100)).then(function (d) {
      var s = d.summary || {};
      var rates = d.rates || {};
      statusEl.textContent = '';
      summaryEl.innerHTML =
        '<p class="meta">费率：佣金' + rates.commission_pct + '% · 收单' + rates.acquiring_pct +
        '% · 广告(CPO)' + rates.ad_pct + '% · 汇率1CNY=' + rates.rub_per_cny + 'RUB　|　' +
        '共' + s.total + '个商品 · <strong style="color:#c33">亏损' + s.losing_count + '个</strong> · 平均利润率' +
        s.avg_margin_pct + '% · 参与弹性提升' + s.in_boost_count + '个 · 需要提价才能设min_price的' +
        s.need_price_raise_count + '个 · 缺成本数据' + s.missing_cost_count + '个</p>';

      (d.rows || []).forEach(function (r) {
        var tr = document.createElement('tr');
        if (r.profit_cny != null && r.profit_cny <= 0) tr.style.background = '#fdecea';
        tr.innerHTML =
          '<td><img class="thumb" src="' + escapeHtml(r.image || '') + '" alt=""></td>' +
          '<td>' + escapeHtml(r.offer_id) + '</td>' +
          '<td title="' + escapeHtml(r.name) + '">' + escapeHtml((r.name || '').slice(0, 36)) + '</td>' +
          '<td>' + r.list_price_cny + '</td>' +
          '<td>' + r.real_price_cny + '</td>' +
          '<td>' + (r.in_elastic_boost ? ('是(boost' + r.boost_pct + ')') : '否') + '</td>' +
          '<td>' + (r.cost_cny != null ? r.cost_cny : '—') + '</td>' +
          '<td>' + (r.weight_g != null ? r.weight_g : '—') + '</td>' +
          '<td>' + r.commission_cny + '</td>' +
          '<td>' + r.logistics_cny + '</td>' +
          '<td>' + r.acquiring_cny + '</td>' +
          '<td>' + r.ad_cny + '</td>' +
          '<td>' + (r.profit_cny != null ? r.profit_cny : '—') + '</td>' +
          '<td>' + (r.margin_pct != null ? r.margin_pct : '—') + '</td>' +
          '<td>' + (r.min_price_draft != null ? r.min_price_draft : '—') + '</td>' +
          '<td>' + (r.needs_increase ? ('是(+' + r.price_gap + ')') : (r.needs_increase === false ? '否' : '—')) + '</td>';
        tbody.appendChild(tr);
      });
    }).catch(function (e) {
      statusEl.textContent = '查询失败：' + (e.message || e);
    });
  }

  function bindUploadTab() {
    loadCategoryOptions().catch(function () { /* 草稿页会再试 */ });
    loadPendingDrafts();  // 加载 agent 预生成的待审草稿
    document.getElementById('btn-refresh-unmig').onclick = function () { loadUnmigrated(); };
    document.getElementById('check-all-unmig').onchange = function (e) {
      document.querySelectorAll('.unmig-check').forEach(function (c) { c.checked = e.target.checked; });
    };
    document.getElementById('btn-batch-migrate').onclick = function () {
      var n = parseInt(document.getElementById('batch-count').value, 10) || 5;
      batchMigrate(n);
    };
    var draftSelBtn = document.getElementById('btn-draft-selected');
    if (draftSelBtn) draftSelBtn.onclick = function () { draftSelected(); };
    document.querySelector('#unmig-table tbody').addEventListener('click', function (e) {
      var gbtn = e.target.closest('[data-action="batch-group"]');
      if (gbtn) {
        batchMigrateGroup(gbtn.dataset.group);
        return;
      }
      var btn = e.target.closest('[data-action="draft"]');
      if (btn) openDraft(parseInt(btn.dataset.idx, 10));
    });
  }

  global.OzonMigrate = {
    loadUnmigrated: loadUnmigrated,
    openDraft: openDraft,
    draftSelected: draftSelected,
    loadPendingDrafts: loadPendingDrafts,
    batchMigrate: batchMigrate,
    bindUploadTab: bindUploadTab,
    loadSettlementSummary: loadSettlementSummary,
    loadProfitTable: loadProfitTable,
    api: api
  };
})(window);
