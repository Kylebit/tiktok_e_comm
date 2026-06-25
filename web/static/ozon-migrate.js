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
    var html = '<select id="f-cat-select" class="cat-select" size="6">';
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

  function bindCategoryEditor(draft) {
    var area = document.getElementById('draft-area');
    var select = document.getElementById('f-cat-select');
    var filter = document.getElementById('f-cat-filter');
    var profileSel = document.getElementById('f-profile');
    var catInput = document.getElementById('f-cat');
    var typeInput = document.getElementById('f-type');

    function updateNameLabels(catId, typeId) {
      var opt = findCategoryOption(typeId);
      var catNameEl = document.getElementById('f-cat-name');
      var typeNameEl = document.getElementById('f-type-name');
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
        area.dataset.migrateProfile = prof;
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
        area.dataset.migrateProfile = profileSel.value;
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

  function openDraft(idx) {
    var it = unmigratedCache[idx];
    if (!it) return;
    var sellerSku = it.seller_sku || it.offer_id;
    var area = document.getElementById('draft-area');
    setRowBusy(idx, true);

    showTaskBanner('running', '正在生成草稿 ' + sellerSku, [
      { cls: 'active', text: '① 读取商品目录 + TikTok 类目/价格' },
      { cls: 'pending', text: '② DeepSeek 生成俄语文案 + 匹配 Ozon 类目（约 30–90 秒）' },
      { cls: 'pending', text: '③ 渲染草稿表单' }
    ], '这是同步任务，不是后台运行；完成前请保持本页打开，不要重复点击。');

    startTaskTimer('正在生成草稿 ' + sellerSku);

    area.innerHTML =
      '<div class="card draft-loading">' +
      '<p><span class="oz-spinner"></span><strong>生成草稿中</strong> · <code>' + esc(sellerSku) + '</code></p>' +
      '<p class="meta">正在拉 TikTok 价格/类目并调用 AI 写俄语标题，通常 30–90 秒…</p>' +
      '<ol class="oz-task-steps">' +
      '<li class="active">读取商品目录</li><li class="pending">AI 生成文案</li><li class="pending">完成</li>' +
      '</ol></div>';
    area.scrollIntoView({ behavior: 'smooth', block: 'start' });

    return Promise.all([
      api('draft/' + encodeURIComponent(sellerSku)),
      loadCategoryOptions()
    ]).then(function (results) {
      var d = results[0];
      if (d.error && !d.draft_title) throw new Error(d.error);
      var offerId = d.offer_id || it.offer_id;
      var matchHint = d.tk_category_path
        ? ('TK: ' + esc(d.tk_category_path) + ' · 匹配: ' + esc(matchMethodLabel(d.category_match_method)) +
          (d.category_match_score != null ? '（得分 ' + esc(String(d.category_match_score)) + '）' : '') +
          ' · 建议 <strong>' + esc(d.type_name_zh || d.type_id) + '</strong>')
        : '未识别 TikTok 类目，请手动选择 Ozon 类目';
      var profile = d.migrate_profile || 'generic';

      showTaskBanner('ok', '✅ 草稿已生成 · ' + sellerSku + '（耗时 ' + elapsedSec() + ' 秒）', [
        { cls: 'done', text: '① 目录 + TK 数据' },
        { cls: 'done', text: '② AI 文案 · ' + (d.deepseek_used ? 'DeepSeek ✓' : '规则兜底') +
          ' · 标题 ' + (d.title_source || '—') + ' · 描述 ' + (d.desc_source || '—') },
        { cls: 'done', text: '③ 请核对类目/文案后：转换图片 → 提交 Ozon' }
      ], '类目、标题、价格等可在下方修改后再提交');
      hideTaskBannerLater(12000);

      area.innerHTML =
        '<div class="card" id="draft-card">' +
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
        '<div class="imgs" id="orig-imgs">' + (d.images || []).map(function (u) {
          return '<img src="' + esc(u) + '" alt="">';
        }).join('') + '</div>' +
        '<div class="grid draft-grid">' +
        '<label>俄语标题</label><textarea id="f-title" rows="2">' + esc(d.draft_title) + '</textarea>' +
        '<label>俄语描述</label><textarea id="f-desc">' + esc(d.draft_description) + '</textarea>' +
        '<label>价格 CNY</label><input id="f-price" value="' + esc(d.price) + '">' +
        '<label>划线价 CNY</label><input id="f-old-price" value="' + esc(d.old_price) + '">' +
        '<label>Ozon 类目</label>' +
        '<div class="cat-editor">' +
        '<div class="cat-match-box">' + matchHint +
        '<br><span class="warn">提交前请核对类目；匹配不准时可搜索或改选下方列表</span></div>' +
        '<input type="search" id="f-cat-filter" class="cat-filter" placeholder="搜索类目中文名…">' +
        buildCategorySelectHtml(d.type_id) +
        '<label style="font-weight:600;font-size:12px;color:#555;padding-top:0">属性模板 profile</label>' +
        '<select id="f-profile" class="cat-select" style="height:auto">' +
        ['sticker', 'tablecloth', 'frame', 'generic'].map(function (p) {
          return '<option value="' + p + '"' + (p === profile ? ' selected' : '') + '>' + p + '</option>';
        }).join('') +
        '</select>' +
        '<div class="cat-ids-row">' +
        '<span>category_id</span><input id="f-cat" value="' + esc(d.category_id) + '">' +
        '<span class="id-name" id="f-cat-name">' + esc(d.category_name_zh || '—') + '</span>' +
        '<span>type_id</span><input id="f-type" value="' + esc(d.type_id) + '">' +
        '<span class="id-name" id="f-type-name">' + esc(d.type_name_zh || '—') + '</span>' +
        '</div></div>' +
        '<label>颜色 / 字典ID</label><div class="row2"><input id="f-color-name" value="' + esc(d.color_name) + '"><input id="f-color-dict" value="' + esc(d.color_dict_id) + '"></div>' +
        '<label>材质 / 字典ID</label><div class="row2"><input id="f-material" value="' + esc(d.material) + '"><input id="f-material-dict" value="' + esc(d.material_dict_id) + '"></div>' +
        '<label>Hashtags</label><input id="f-hashtags" value="' + esc(d.hashtags || '') + '">' +
        '<label>套装 kit</label><input id="f-kit" value="' + esc(d.kit) + '">' +
        '<label>重量(g)</label><input id="f-weight" value="' + esc(d.weight) + '">' +
        '<label>长×宽×高 mm</label><div class="row3"><input id="f-depth" value="' + esc(d.depth) + '"><input id="f-width" value="' + esc(d.width) + '"><input id="f-height" value="' + esc(d.height) + '"></div>' +
        '<label>长×宽 cm</label><div class="row2"><input id="f-len-cm" value="' + esc(d.len_cm) + '"><input id="f-wid-cm" value="' + esc(d.wid_cm) + '"></div>' +
        '</div>' +
        '<div class="toolbar" style="margin-top:12px">' +
        '<button type="button" class="btn secondary" id="btn-process-images">↻ 重新转换图片 3:4（约30s/张）</button>' +
        '<button type="button" class="btn" id="btn-submit" disabled>② 提交 Ozon（请先核对后再点）</button>' +
        '<span class="status" id="draft-status"></span></div>' +
        '<div class="imgs" id="processed-imgs"></div></div>';

      area.dataset.sellerSku = sellerSku;
      area.dataset.offerId = offerId;
      area.dataset.images = JSON.stringify(d.images || []);
      area.dataset.processed = '';
      area.dataset.migrateProfile = profile;
      area.dataset.tkCategoryId = d.tk_category_id || '';
      area.dataset.tkCategoryLeaf = d.tk_category_leaf || '';

      bindCategoryEditor(d);

      document.getElementById('btn-process-images').onclick = function () {
        processImages(sellerSku);
      };
      document.getElementById('btn-submit').onclick = function () {
        submitMigrate(offerId);
      };

      // 草稿生成后直接自动转换图片，无需手动点击；提交 Ozon 仍需人工核对后点击。
      if (d.images && d.images.length) {
        processImages(sellerSku);
      }
      area.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }).catch(function (e) {
      stopTaskTimer();
      showTaskBanner('err', '❌ 草稿失败 · ' + sellerSku, [
        { cls: 'fail', text: e.message || '未知错误' }
      ], '可检查网络、DeepSeek 配置，或稍后重试');
      area.innerHTML = '<div class="card err"><strong>草稿失败</strong><p>' + esc(e.message) + '</p></div>';
    }).finally(function () {
      setRowBusy(idx, false);
    });
  }

  function processImages(sellerSku) {
    var area = document.getElementById('draft-area');
    var btn = document.getElementById('btn-process-images');
    var status = document.getElementById('draft-status');
    var images = JSON.parse(area.dataset.images || '[]');
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
      area.dataset.processed = JSON.stringify(d.images || []);
      document.getElementById('processed-imgs').innerHTML = (d.images || []).map(function (u) {
        return '<img src="' + esc(u) + '" alt="">';
      }).join('');
      status.textContent = '✅ 图片完成，共 ' + (d.images || []).length + ' 张（' + elapsedSec() + ' 秒）';
      showTaskBanner('ok', '✅ 图片处理完成 · ' + sellerSku, [
        { cls: 'done', text: '① 草稿' },
        { cls: 'done', text: '② 图片 ' + (d.images || []).length + ' 张' },
        { cls: 'active', text: '③ 可点「提交 Ozon」' }
      ], '');
      hideTaskBannerLater(8000);
      document.getElementById('btn-submit').disabled = !(d.images && d.images.length);
    }).catch(function (e) {
      stopTaskTimer();
      showTaskBanner('err', '❌ 图片失败', [{ cls: 'fail', text: e.message }], '');
      status.textContent = '图片失败: ' + e.message;
    }).finally(function () {
      btn.disabled = false;
    });
  }

  function submitMigrate(offerId) {
    var area = document.getElementById('draft-area');
    var status = document.getElementById('draft-status');
    var images = JSON.parse(area.dataset.processed || '[]');
    if (!images.length) {
      alert('请先完成图片转换');
      return Promise.resolve();
    }
    var payload = {
      offer_id: offerId,
      images: images,
      title: document.getElementById('f-title').value,
      description: document.getElementById('f-desc').value,
      price: document.getElementById('f-price').value,
      old_price: document.getElementById('f-old-price').value,
      color_name: document.getElementById('f-color-name').value,
      color_dict_id: document.getElementById('f-color-dict').value,
      material: document.getElementById('f-material').value,
      material_dict_id: document.getElementById('f-material-dict').value,
      hashtags: document.getElementById('f-hashtags').value,
      kit: document.getElementById('f-kit').value,
      weight: document.getElementById('f-weight').value,
      depth: document.getElementById('f-depth').value,
      width: document.getElementById('f-width').value,
      height: document.getElementById('f-height').value,
      len_cm: document.getElementById('f-len-cm').value,
      wid_cm: document.getElementById('f-wid-cm').value,
      category_id: document.getElementById('f-cat').value,
      type_id: document.getElementById('f-type').value,
      migrate_profile: (document.getElementById('f-profile') || {}).value || area.dataset.migrateProfile || 'generic',
      tk_category_id: area.dataset.tkCategoryId || '',
      tk_category_leaf: area.dataset.tkCategoryLeaf || ''
    };
    status.textContent = '提交中…';
    showTaskBanner('running', '提交 Ozon · offer ' + offerId, [
      { cls: 'done', text: '① 草稿' },
      { cls: 'done', text: '② 图片' },
      { cls: 'active', text: '③ Ozon import + 富内容（约 1–3 分钟）' }
    ], '请勿关闭页面');
    startTaskTimer('提交 Ozon · ' + offerId);
    document.getElementById('btn-submit').disabled = true;
    return api('migrate', { method: 'POST', body: payload }).then(function (d) {
      var ok = d.import_ok || (d.status === 'imported' && (!d.errors || !d.errors.length));
      status.textContent = (ok ? '✅ ' : '⚠ ') + 'status=' + d.status +
        (d.rich_status ? ' rich=' + d.rich_status : '') +
        (d.errors && d.errors.length ? ' ' + JSON.stringify(d.errors) : '') +
        ' · ' + elapsedSec() + ' 秒';
      showTaskBanner(ok ? 'ok' : 'err', (ok ? '✅ Ozon 上品成功 · ' : '⚠ 提交异常 · ') + offerId, [
        { cls: 'done', text: 'status=' + d.status + (d.rich_status ? ' rich=' + d.rich_status : '') }
      ], ok ? '已从待搬运列表移除' : '见下方详情或历史 Tab');
      if (ok) hideTaskBannerLater(10000);
      if (ok) loadUnmigrated();
    }).catch(function (e) {
      stopTaskTimer();
      showTaskBanner('err', '❌ 提交失败 · ' + offerId, [{ cls: 'fail', text: e.message }], '');
      status.textContent = '提交失败: ' + e.message;
    }).finally(function () {
      document.getElementById('btn-submit').disabled = false;
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

  function bindUploadTab() {
    loadCategoryOptions().catch(function () { /* 草稿页会再试 */ });
    document.getElementById('btn-refresh-unmig').onclick = function () { loadUnmigrated(); };
    document.getElementById('check-all-unmig').onchange = function (e) {
      document.querySelectorAll('.unmig-check').forEach(function (c) { c.checked = e.target.checked; });
    };
    document.getElementById('btn-batch-migrate').onclick = function () {
      var n = parseInt(document.getElementById('batch-count').value, 10) || 5;
      batchMigrate(n);
    };
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
    batchMigrate: batchMigrate,
    bindUploadTab: bindUploadTab,
    api: api
  };
})(window);
