/**
 * CURSOR Outprofit 订单利润表（与 build_order_profit_page.py 一致）
 */
(function (global) {
  'use strict';

  function escapeHtml(s) {
    if (s == null) return '';
    var div = document.createElement('div');
    div.textContent = String(s);
    return div.innerHTML;
  }

  function safeRate(rate) {
    var r = parseFloat(rate);
    return r > 0 ? r : 1;
  }

  function applyAdCost(rows, adRateFraction) {
    var frac = parseFloat(adRateFraction);
    if (isNaN(frac)) frac = 0.2;
    return (rows || []).map(function (r) {
      var copy = Object.assign({}, r);
      copy.fees = (r.fees || []).slice();
      copy.ad_cost = Math.round((Number(copy.subtotal) || 0) * frac * 100) / 100;
      return copy;
    });
  }

  function renderRegion(container, opts) {
    opts = opts || {};
    var region = opts.region || '';
    var rows = opts.rows || [];
    var feeColumns = opts.feeColumns || [];
    var rate = safeRate(opts.rate);
    var localShippingFee = parseFloat(opts.localShippingFee) || 0;
    var meta = opts.meta || {};
    var onLocalShippingChange = opts.onLocalShippingChange;

    var metaLine = '';
    if (meta.country || meta.period) {
      metaLine = '国家：' + escapeHtml(meta.country || region) + '　结算期：' + escapeHtml(meta.period || '');
    }

    container.innerHTML =
      (metaLine ? '<p class="hint op-meta-line">' + metaLine + '</p>' : '') +
      '<p class="hint">收入表为当地货币；商品成本为人民币。广告成本 = 卖家折扣后小计 × 广告比例。利润率 = 单笔利润 / 卖家折扣后小计。红底缺成本，绿底本土发货。表格可横向滚动。</p>' +
      '<div class="op-toolbar">' +
      '<span class="meta">汇率 1 当地 = <strong>' + rate + '</strong> 人民币</span>' +
      '<label style="margin-left:16px">本土发货费用（元/单）：</label>' +
      '<input type="number" class="op-local-fee" step="0.01" min="0" value="' + localShippingFee + '">' +
      '</div>' +
      '<h3 class="op-subtitle">统计总览</h3>' +
      '<div class="op-summary-box"></div>' +
      '<h3 class="op-subtitle">订单明细 <span class="meta">（' + rows.length + ' 笔，左右滑动查看费用列）</span></h3>' +
      '<div class="op-table-wrap">' +
      '<table class="op-order-table"><colgroup class="op-colgroup"></colgroup>' +
      '<thead><tr class="op-thead-row"></tr></thead>' +
      '<tbody class="op-tbody"></tbody>' +
      '<tfoot><tr class="op-tfoot-row"></tr></tfoot>' +
      '</table></div>';

    var localInput = container.querySelector('.op-local-fee');
    if (localInput && onLocalShippingChange) {
      localInput.onchange = function () {
        onLocalShippingChange(parseFloat(localInput.value) || 0);
      };
    }

    function getLocalFee() {
      if (!localInput) return localShippingFee;
      return parseFloat(localInput.value) || 0;
    }

    renderSummary(container, rows, feeColumns, rate, getLocalFee);
    renderTable(container, rows, feeColumns, rate, getLocalFee);
  }

  function renderSummary(container, rows, feeColumns, rate, getLocalFee) {
    var box = container.querySelector('.op-summary-box');
    if (!box) return;
    rate = safeRate(rate);
    var totalSubtotal = 0, totalSettlement = 0, totalProductCost = 0, totalAdCost = 0;
    var totalProfitLocal = 0, totalProfitCny = 0;
    var totalFeesByIndex = {};
    var bySku = {};
    var idxAffiliate = feeColumns.findIndex(function (fc) { return fc.en === 'Affiliate Commission'; });
    var idxShopAds = feeColumns.findIndex(function (fc) { return fc.en === 'Affiliate Shop Ads commission'; });
    var totalQty = 0, affiliateQtyTotal = 0, affiliateSubtotalTotal = 0, orderCount = 0, affiliateOrderCount = 0;
    var localShippingOrderCount = 0;
    var localFeePerRow = getLocalFee();

    rows.forEach(function (r) {
      var st = r.subtotal || 0;
      totalSubtotal += st;
      totalSettlement += r.settlement || 0;
      totalProductCost += r.product_cost || 0;
      totalAdCost += r.ad_cost || 0;
      var pl = (r.settlement || 0) - (r.product_cost || 0) / rate - (r.ad_cost || 0);
      var pc = (r.settlement || 0) * rate - (r.product_cost || 0) - (r.ad_cost || 0) * rate;
      if (r.local_shipping) {
        localShippingOrderCount += 1;
        if (localFeePerRow > 0) {
          pl -= localFeePerRow / rate;
          pc -= localFeePerRow;
        }
      }
      totalProfitLocal += pl;
      totalProfitCny += pc;
      (r.fees || []).forEach(function (v, i) {
        totalFeesByIndex[i] = (totalFeesByIndex[i] || 0) + v;
      });
      var isAffiliate = (idxAffiliate >= 0 && r.fees && Number(r.fees[idxAffiliate]) !== 0) ||
        (idxShopAds >= 0 && r.fees && Number(r.fees[idxShopAds]) !== 0);
      var q = r.qty || 0;
      totalQty += q;
      if (isAffiliate) { affiliateQtyTotal += q; affiliateSubtotalTotal += st; affiliateOrderCount += 1; }
      orderCount += 1;
      var key = r.sku_id || r.product_name || '-';
      if (!bySku[key]) bySku[key] = { qty: 0, profitLocal: 0, profitCny: 0, name: r.product_name || '-', image_url: r.image_url || '', affiliateQty: 0 };
      bySku[key].qty += q;
      bySku[key].profitLocal += pl;
      bySku[key].profitCny += pc;
      if (isAffiliate) bySku[key].affiliateQty += q;
    });

    var totalFeesIndex = feeColumns.findIndex(function (fc) { return fc.en === 'Total Fees'; });
    var totalFees = totalFeesIndex >= 0 ? (totalFeesByIndex[totalFeesIndex] || 0) : 0;
    var productCostLocal = totalProductCost / rate;
    var productPct = totalSubtotal > 0 ? (productCostLocal / totalSubtotal * 100) : 0;
    var adPct = totalSubtotal > 0 ? (totalAdCost / totalSubtotal * 100) : 0;
    var feesPct = totalSubtotal > 0 ? (totalFees / totalSubtotal * 100) : 0;
    var profitPct = totalSubtotal > 0 ? (totalProfitLocal / totalSubtotal * 100) : 0;
    var profitClass = totalProfitLocal >= 0 ? 'profit-positive' : 'profit-negative';

    box.innerHTML =
      '<div class="op-summary-grid">' +
      '<div class="op-summary-item"><span class="label">总利润</span><div class="value ' + profitClass + '">当地 ' + totalProfitLocal.toFixed(2) + '<br>¥' + totalProfitCny.toFixed(2) + '</div></div>' +
      '<div class="op-summary-item"><span class="label">卖家折扣后小计合计</span><div class="value">当地 ' + totalSubtotal.toFixed(2) + '<br>¥' + (totalSubtotal * rate).toFixed(2) + '</div></div>' +
      '<div class="op-summary-item"><span class="label">商品成本占小计</span><div class="value">' + productPct.toFixed(1) + '%</div></div>' +
      '<div class="op-summary-item"><span class="label">广告成本占小计</span><div class="value">' + adPct.toFixed(1) + '%</div></div>' +
      '<div class="op-summary-item"><span class="label">平台/费用占小计</span><div class="value">' + feesPct.toFixed(1) + '%</div></div>' +
      '<div class="op-summary-item"><span class="label">利润率</span><div class="value">' + profitPct.toFixed(1) + '%</div></div>' +
      '<div class="op-summary-item"><span class="label">广告耗费(¥)</span><div class="value">¥' + (totalAdCost * rate).toFixed(2) + '</div></div>' +
      '<div class="op-summary-item"><span class="label">本土发货订单</span><div class="value">' + localShippingOrderCount + ' 单</div></div>' +
      '</div>';
  }

  function renderTable(container, rows, feeColumns, rate, getLocalFee) {
    var colgroup = container.querySelector('.op-colgroup');
    var thead = container.querySelector('.op-thead-row');
    var tbody = container.querySelector('.op-tbody');
    var totalsRow = container.querySelector('.op-tfoot-row');
    if (!colgroup || !thead || !tbody) return;

    rate = safeRate(rate);
    var baseHeaders = ['日期', '订单ID', '商品图', 'SKU ID', '商品名称', '规格', '数量'];
    var sumHeaders = ['小计(当地/¥)', '结算(当地/¥)', '收入(当地/¥)', '成本(¥)', '广告(当地/¥)', '利润(当地/¥)', '利润率', '本土'];
    var numCols = baseHeaders.length + sumHeaders.length + feeColumns.length;

    colgroup.innerHTML = '';
    for (var c = 0; c < numCols; c++) {
      colgroup.innerHTML += '<col style="min-width:' + (c < 7 ? 88 : 76) + 'px">';
    }

    var headHtml = baseHeaders.map(function (h) { return '<th>' + h + '</th>'; }).join('') +
      sumHeaders.map(function (h) { return '<th class="num">' + h + '</th>'; }).join('') +
      feeColumns.map(function (fc) {
        return '<th class="num th-fee" title="' + escapeHtml(fc.en) + '">' + escapeHtml(fc.cn) + '</th>';
      }).join('');
    thead.innerHTML = headHtml;

    var fmt = function (localVal, cnyVal) {
      return Number(localVal || 0).toFixed(2) + ' / ¥' + Number(cnyVal || 0).toFixed(2);
    };

    var totalQty = 0, totalSubtotal = 0, totalSettlement = 0, totalRevenue = 0;
    var totalProductCost = 0, totalAdCost = 0, totalProfitLocal = 0, totalProfitCny = 0;
    var totalFeesByIndex = {};
    var localShippingCount = 0;
    var localFee = getLocalFee();
    var rowHtml = [];

    rows.forEach(function (r) {
      var cls = [];
      if (r.cost_matched === false) cls.push('row-no-cost-match');
      if (r.local_shipping) { cls.push('row-local-shipping'); localShippingCount += 1; }
      var imgCell = r.image_url
        ? '<td><img class="op-product-img" src="' + escapeHtml(r.image_url) + '" alt="" loading="lazy"></td>'
        : '<td><span class="op-product-img-none">无</span></td>';
      var st = r.subtotal || 0;
      var profitLocal = r.settlement - r.product_cost / rate - r.ad_cost;
      var profitCny = r.settlement * rate - r.product_cost - r.ad_cost * rate;
      if (r.local_shipping && localFee > 0) {
        profitLocal -= localFee / rate;
        profitCny -= localFee;
      }
      var marginPct = st > 0 ? (profitLocal / st * 100) : null;
      var profitClass = profitLocal >= 0 ? 'profit-positive' : 'profit-negative';

      totalQty += r.qty || 0;
      totalSubtotal += st;
      totalSettlement += r.settlement || 0;
      totalRevenue += r.revenue || 0;
      totalProductCost += r.product_cost || 0;
      totalAdCost += r.ad_cost || 0;
      totalProfitLocal += profitLocal;
      totalProfitCny += profitCny;
      (r.fees || []).forEach(function (v, i) { totalFeesByIndex[i] = (totalFeesByIndex[i] || 0) + v; });

      var feeCells = (r.fees || []).map(function (v) {
        return '<td class="num">' + fmt(v, v * rate) + '</td>';
      }).join('');

      rowHtml.push(
        '<tr class="' + cls.join(' ') + '">' +
        '<td>' + escapeHtml(r.date) + '</td><td>' + escapeHtml(r.order_id) + '</td>' + imgCell +
        '<td>' + escapeHtml(r.sku_id) + '</td>' +
        '<td class="product-name" title="' + escapeHtml(r.product_name) + '">' + escapeHtml(r.product_name) + '</td>' +
        '<td>' + escapeHtml(r.sku_name) + '</td><td class="num">' + escapeHtml(r.qty) + '</td>' +
        '<td class="num">' + fmt(st, st * rate) + '</td>' +
        '<td class="num">' + fmt(r.settlement, r.settlement * rate) + '</td>' +
        '<td class="num">' + fmt(r.revenue, r.revenue * rate) + '</td>' +
        '<td class="num">— / ¥' + Number(r.product_cost).toFixed(2) + '</td>' +
        '<td class="num">' + fmt(r.ad_cost, r.ad_cost * rate) + '</td>' +
        '<td class="num ' + profitClass + '">' + fmt(profitLocal, profitCny) + '</td>' +
        '<td class="num">' + (marginPct != null ? marginPct.toFixed(1) + '%' : '-') + '</td>' +
        '<td>' + (r.local_shipping ? '是' : '') + '</td>' +
        feeCells + '</tr>'
      );
    });

    tbody.innerHTML = rowHtml.join('');

    if (totalsRow) {
      var totalMarginPct = totalSubtotal > 0 ? (totalProfitLocal / totalSubtotal * 100) : null;
      var totalProfitClass = totalProfitLocal >= 0 ? 'profit-positive' : 'profit-negative';
      var totalsHtml = '<td colspan="7">合计 · ' + rows.length + ' 笔</td>' +
        '<td class="num">' + fmt(totalSubtotal, totalSubtotal * rate) + '</td>' +
        '<td class="num">' + fmt(totalSettlement, totalSettlement * rate) + '</td>' +
        '<td class="num">' + fmt(totalRevenue, totalRevenue * rate) + '</td>' +
        '<td class="num">— / ¥' + totalProductCost.toFixed(2) + '</td>' +
        '<td class="num">' + fmt(totalAdCost, totalAdCost * rate) + '</td>' +
        '<td class="num ' + totalProfitClass + '">' + fmt(totalProfitLocal, totalProfitCny) + '</td>' +
        '<td class="num">' + (totalMarginPct != null ? totalMarginPct.toFixed(1) + '%' : '-') + '</td>' +
        '<td>' + localShippingCount + '</td>';
      for (var fi = 0; fi < feeColumns.length; fi++) {
        var fv = totalFeesByIndex[fi] || 0;
        totalsHtml += '<td class="num">' + fmt(fv, fv * rate) + '</td>';
      }
      totalsRow.innerHTML = totalsHtml;
    }
  }

  global.Outprofit = {
    applyAdCost: applyAdCost,
    renderRegion: renderRegion
  };
})(window);
