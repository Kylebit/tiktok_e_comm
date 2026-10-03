/* Presentation from 99401680; current frozen readers retain all write authority. */
(() => {
"use strict";
const $=s=>document.querySelector(s);
const esc=v=>String(v??"—").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
const setBadge=(node,text,kind)=>{node.textContent=text;node.className="badge "+kind;};
const humanizeFirstReviewBlocker=v=>String(v??"");
  function renderFirstReviewCandidates(view) {
    const badge = $("#firstReviewCandidatesBadge");
    const notice = $("#firstReviewCandidatesNotice");
    const grid = $("#firstReviewCandidateGrid");
    const contentOptions = $("#firstReviewContentGroupOptions");
    const blockers = $("#firstReviewCandidateBlockers");
    if (!badge || !notice || !grid || !contentOptions || !blockers) return;

    const status = String(view?.status || "NOT_PREPARED").toUpperCase();
    const categories = Array.isArray(view?.platform_categories) ? view.platform_categories : [];
    const reviewSets = Array.isArray(view?.copy_review_sets) ? view.copy_review_sets : [];
    const targets = Array.isArray(view?.targets) ? view.targets : [];
    const sharedFacts = view?.shared_review_facts && typeof view.shared_review_facts === "object" ? view.shared_review_facts : {};
    const sharedVariants = Array.isArray(sharedFacts.variants) ? sharedFacts.variants : [];
    const productFacts = view?.product_facts || {};
    const productSummary = Object.keys(productFacts).length ? `<article><strong>${esc(productFacts.title || '当前商品')}</strong><span>商品 SKU ${esc(productFacts.seller_sku)}</span><span>成本 ¥ ${esc(productFacts.cost_cny)}</span><span>重量 ${esc(productFacts.weight_kg)} kg</span><small>包装 ${esc((productFacts.package_cm || []).join(' × '))} cm</small><small>来源图片 ${esc(productFacts.source_image_count)} 张</small></article>` : '';
    const knowledge = view?.knowledge_basis && typeof view.knowledge_basis === "object" ? view.knowledge_basis : {};
    const platformLabels = { tiktok: "TikTok Shop", shopee: "Shopee", ozon: "Ozon" };
    const needsDecision = (value) => /REQUIRED|PENDING|WAIT|BLOCK|FAIL/i.test(String(value || ""));
    const writeCount = Math.max(0, Number(view?.external_write_count || 0));
    setBadge(
      badge,
      status === "READY" ? "审核材料已整理" : (status === "STALE" ? "报告已过期" : status),
      status === "READY" ? "safe" : "warn",
    );
    notice.textContent = `依据中文知识库 ${knowledge.version || "未标记版本"}；先审 ${sharedVariants.length} 个通用变体，再审 ${categories.length} 个平台类目、${reviewSets.length} 套英中稿和 ${targets.length} 个目标的逐变体售价。确认外部写入 ${writeCount} 次。`;

    const sharedSection = `
      <section class="first-review-shared-facts" aria-label="通用商品事实">
        <header><div><span>01</span><strong>通用商品事实</strong></div><small>所有平台与店铺共用，只审核一次</small></header>
        <div class="first-review-shared-fact-grid">
          ${productSummary}
          ${sharedVariants.length ? sharedVariants.map((variant) => `<article><strong>SKU ${esc(variant.sku_id)}</strong><span>${esc(variant.specification_value || "1pcs")}</span><span>${variant.weight_kg ?? "—"} kg</span><small>包裹 ${esc((variant.package_cm || []).join(" × ") || "待确认")} cm</small></article>`).join("") : '<p class="first-review-audit-empty">尚无通用变体事实。</p>'}
        </div>
      </section>`;

    const categorySection = `
      <section class="first-review-category-summary" aria-label="平台类目汇总">
        <header><div><span>02</span><strong>平台类目</strong></div><small>同一平台只审核一次</small></header>
        <div class="first-review-platform-category-grid">
          ${categories.length ? categories.map((category) => `
            <article class="first-review-platform-category-card ${needsDecision(category.status) ? "is-pending" : ""}">
              <div><strong>${esc(platformLabels[category.platform] || category.platform)}</strong><span>${needsDecision(category.status) ? "待确认" : "候选已整理"}</span></div>
              <h3>类目 ${esc(category.category_id)}</h3>
              <p class="category-zh">${esc(category.category_zh)}</p>
              <p class="category-en">${esc(category.category_en)}</p>
              ${category.note ? `<small>${esc(category.note)}</small>` : ""}
            </article>
          `).join("") : '<p class="first-review-audit-empty">尚无平台级类目摘要。</p>'}
        </div>
      </section>
    `;

    const copySection = `
      <section class="first-review-copy-review" aria-label="英中审核稿">
        <header><div><span>03</span><strong>最终英文 + 中文对照</strong></div><small>冻结英中审核稿；各目标语言沿用当前首轮快照</small></header>
        <div class="first-review-copy-review-grid">
          ${reviewSets.length ? reviewSets.map((copy) => `
            <article class="first-review-copy-bilingual">
              <header><strong>${esc(copy.label || copy.id)}</strong><span>独立内容组</span></header>
              <div class="first-review-bilingual-grid">
                <div><span>最终英文标题</span><h3>${esc(copy.title_en)}</h3><p>${esc(copy.description_en)}</p></div>
                <div><span>中文对照</span><h3>${esc(copy.title_zh)}</h3><p>${esc(copy.description_zh)}</p></div>
              </div>
              <div class="first-review-specification-row">
                <strong>${esc(copy.specification_name_zh || "规格")} / ${esc(copy.specification_name_en || "Specification")}</strong>
                <div>${(copy.specification_values || []).map((value) => `<span>${esc(value || "1pcs")}</span>`).join("") || "<span>1pcs</span>"}</div>
              </div>
            </article>
          `).join("") : '<p class="first-review-audit-empty">尚无英中审核稿。</p>'}
        </div>
      </section>
    `;

    const renderKeyValues = (values) => Object.entries(values || {}).map(([key, value]) => (
      `<div><span>${esc(key)}</span><strong>${esc(value === null || value === undefined ? "—" : value)}</strong></div>`
    )).join("");
    const priceSection = `
      <section class="first-review-price-review" aria-label="逐目标价格审核">
        <header><div><span>04</span><strong>逐目标、逐变体售价</strong></div><small>每个变体独立售价；公式默认折叠</small></header>
        <div class="first-review-price-grid">
          ${targets.length ? targets.map((row) => {
            const price = row?.price || {};
            const calculation = price.calculation || {};
            const skuPrices = Array.isArray(price.sku_prices) ? price.sku_prices : [];
            const amount = price.amount === null || price.amount === undefined || price.amount === ""
              ? "待确认"
              : `${price.amount} ${price.currency || ""}`.trim();
            return `
              <article class="first-review-price-card">
                <header><strong>${esc(row.target)}</strong><span>${esc(amount)}</span></header>
                <small>${esc(price.status || "UNKNOWN")}</small>
                <div class="first-review-variant-price-list">
                  ${skuPrices.length ? skuPrices.map((variant) => {
                    const variantCalculation = variant.calculation || calculation;
                    const variantAmount = variant.amount === null || variant.amount === undefined || variant.amount === "" ? "待确认" : `${variant.amount} ${variant.currency || price.currency || ""}`.trim();
                    return `<div><header><strong>${esc(variant.display_name || "1pcs")}</strong><span>${esc(variantAmount)}</span></header><details class="first-review-price-formula"><summary>完整价格公式</summary><p>${esc(variantCalculation.formula_zh || "当前变体尚无可审计公式。")}</p>${variantCalculation.formula ? `<code>${esc(variantCalculation.formula)}</code>` : ""}<div class="first-review-formula-values">${renderKeyValues(variantCalculation.inputs)}</div><div class="first-review-formula-values result">${renderKeyValues(variantCalculation.result || variantCalculation.derived_preview)}</div></details></div>`;
                  }).join("") : '<p class="first-review-audit-empty">此目标尚无逐变体价格。</p>'}
                </div>
                <details class="first-review-price-formula">
                  <summary>完整价格公式</summary>
                  <p>${esc(calculation.formula_zh || "当前目标尚无可审计公式。")}</p>
                  ${calculation.formula ? `<code>${esc(calculation.formula)}</code>` : ""}
                  <div class="first-review-formula-values">${renderKeyValues(calculation.inputs)}</div>
                  <div class="first-review-formula-values result">${renderKeyValues(calculation.result || calculation.derived_preview)}</div>
                </details>
              </article>
            `;
          }).join("") : '<p class="first-review-audit-empty">尚无逐目标售价。</p>'}
        </div>
      </section>
    `;
    grid.innerHTML = sharedSection + categorySection + copySection + priceSection;

    const contentGroups = view?.content_groups && typeof view.content_groups === "object"
      ? view.content_groups
      : {};
    const options = Array.isArray(contentGroups.options) ? contentGroups.options : [];
    contentOptions.innerHTML = options.length
      ? options.map((option) => `
          <article class="first-review-content-option ${option.recommended ? "recommended" : ""}">
            <header><strong>${esc(option.label || option.id)}</strong><span>${contentGroups.status === "REQUIRED_BY_TARGET_SCOPE" ? "强制规则" : "Skill 建议"}</span></header>
            <p>${esc(option.description)}</p>
          </article>
        `).join("")
      : '<p class="first-review-audit-empty">尚无内容分组选项。</p>';
    blockers.innerHTML = Array.isArray(view?.blockers)
      ? view.blockers.map((value) => `<p>${esc(humanizeFirstReviewBlocker(value))}</p>`).join("")
      : "";
    if (contentGroups.note) blockers.insertAdjacentHTML("beforeend", `<p>${esc(contentGroups.note)}</p>`);
  }

  function renderFirstReviewAudit(audit) {
    const badge = $("#firstReviewAuditBadge");
    const disclosure = $("#firstReviewAuditDisclosure");
    const workflowSummary = $("#firstReviewWorkflowSummary");
    const workflowStages = $("#firstReviewWorkflowStages");
    const finalFacts = $("#firstReviewFinalFacts");
    const finalQuestions = $("#firstReviewFinalQuestions");
    const evidence = $("#firstReviewAuditEvidence");
    const decisions = $("#firstReviewAuditDecisions");
    const operations = $("#firstReviewAuditOperations");
    if (
      !badge
      || !disclosure
      || !workflowSummary
      || !workflowStages
      || !finalFacts
      || !finalQuestions
      || !evidence
      || !decisions
      || !operations
    ) return;

    const status = String(audit?.status || "NOT_PREPARED").toUpperCase();
    const statusLabels = {
      READY: `revision ${Number(audit?.revision || 0)} · 可审核`,
      STALE: "审计记录已过期",
      INVALID: "审计记录不可用",
      NOT_PREPARED: "尚未准备",
    };
    setBadge(
      badge,
      statusLabels[status] || status,
      status === "READY" ? "safe" : (status === "STALE" ? "warn" : "neutral"),
    );
    disclosure.textContent = status === "STALE"
      ? `该记录对应 revision ${Number(audit?.revision || 0)}，当前为 revision ${Number(audit?.current_revision || 0)}；请重新执行第一轮 Skill。`
      : "这里展示可核验的证据来源、决策摘要与操作记录，不展示不可验证的内部思维链。";

    const sourceRows = Array.isArray(audit?.evidence_sources) ? audit.evidence_sources : [];
    evidence.innerHTML = sourceRows.length
      ? sourceRows.map((row) => `
          <article class="first-review-audit-row">
            <header><strong>${esc(row.source || "证据")}</strong></header>
            <p>${esc(row.summary || "已记录")}</p>
            <small>${esc(JSON.stringify(row.evidence || {}))}</small>
          </article>
        `).join("")
      : '<p class="first-review-audit-empty">第一轮 Skill 尚未记录证据来源。</p>';

    const topicLabels = {
      sku_identity: "SKU 身份",
      parcel: "重量与包裹",
      target_category: "目标类目",
      target_price: "目标价格",
      target_copy: "标题与发布规格",
      images: "图片计划",
      content_groups: "内容分组",
    };
    const decisionRows = Array.isArray(audit?.decisions) ? audit.decisions : [];
    decisions.innerHTML = decisionRows.length
      ? decisionRows.map((row) => `
          <article class="first-review-audit-row">
            <header>
              <strong>${esc(topicLabels[row.topic] || row.topic || "决策")}${row.target ? ` · ${esc(row.target)}` : ""}</strong>
              <span>${esc(row.status || "待决定")}</span>
            </header>
            <p>${esc(row.summary || "尚无摘要")}</p>
            <small>${esc(row.reason || "")} ${row.evidence_path ? `· 证据：${esc(row.evidence_path)}` : ""}</small>
          </article>
        `).join("")
      : '<p class="first-review-audit-empty">第一轮 Skill 尚未记录决策摘要。</p>';

    const operationRows = Array.isArray(audit?.operations) ? audit.operations : [];
    operations.innerHTML = operationRows.length
      ? operationRows.map((row) => `
          <article class="first-review-audit-row">
            <header>
              <strong>${esc(row.sequence || "-")} · ${esc(row.event || "操作")}</strong>
              <span>${esc(row.status || "UNKNOWN")}</span>
            </header>
            <p>${esc(row.summary || "已记录")}</p>
            <small>外部写入 ${Number(row.external_write_count || 0)} 次${row.recorded_at ? ` · ${esc(row.recorded_at)}` : ""}</small>
          </article>
        `).join("")
      : '<p class="first-review-audit-empty">第一轮 Skill 尚未记录操作。</p>';

    const stageTitleLabels = {
      SCOPE_LOCKED: "锁定商品与目标店铺",
      PRODUCT_CENTER_READ: "读取商品中心事实",
      FACTS_PROJECTED: "整理 SKU 与物流事实",
      TARGETS_PROJECTED: "逐目标整理类目、价格与文案",
      IMAGE_PLAN_PROJECTED: "整理图片翻译与生成方案",
      ZERO_WRITE_BOUNDARY_VERIFIED: "核验第一轮零外部写入",
    };
    const stageRows = (Array.isArray(audit?.workflow_stages) && audit.workflow_stages.length)
      ? [...audit.workflow_stages].sort(
        (left, right) => Number(left.sequence || 0) - Number(right.sequence || 0),
      )
      : operationRows.map((row) => ({
        sequence: row.sequence,
        stage_id: row.event,
        title: stageTitleLabels[row.event] || row.event || "第一轮任务",
        status: row.status,
        summary: row.summary,
        conclusions: [],
        evidence_refs: [],
        warnings: [],
        completed_at: row.recorded_at,
      }));
    const stageStatusClass = (value) => {
      const normalized = String(value || "").toUpperCase();
      if (
        normalized.includes("WARN")
        || normalized.includes("REQUIRED")
        || normalized.includes("BLOCK")
        || normalized.includes("FAIL")
      ) return "warn";
      if (
        normalized.includes("PENDING")
        || normalized.includes("WAIT")
        || normalized.includes("NOT_")
      ) return "pending";
      return "";
    };
    const durationLabel = (durationMs) => {
      const value = Number(durationMs);
      if (!Number.isFinite(value) || value < 0) return "";
      if (value < 1000) return `${Math.round(value)} ms`;
      if (value < 60000) return `${Math.round(value / 1000)} 秒`;
      return `${Math.round(value / 60000)} 分钟`;
    };
    workflowStages.innerHTML = stageRows.length
      ? stageRows.map((row, index) => {
          const conclusions = Array.isArray(row.conclusions) ? row.conclusions : [];
          const evidenceRefs = Array.isArray(row.evidence_refs) ? row.evidence_refs : [];
          const warnings = Array.isArray(row.warnings) ? row.warnings : [];
          const meta = [
            durationLabel(row.duration_ms),
            ...evidenceRefs.map((value) => `证据 ${value}`),
            row.completed_at || row.started_at || "",
          ].filter(Boolean);
          return `
            <article class="first-review-workflow-stage ${stageStatusClass(row.status)}">
              <span class="first-review-stage-number">${String(Number(row.sequence || index + 1)).padStart(2, "0")}</span>
              <div class="first-review-stage-body">
                <header>
                  <strong>${esc(row.title || stageTitleLabels[row.stage_id] || row.stage_id || "第一轮任务")}</strong>
                  <span>${esc(row.status || "UNKNOWN")}</span>
                </header>
                <p>${esc(row.summary || "该步骤已记录，但尚无公开摘要。")}</p>
                ${conclusions.length ? `
                  <ul class="first-review-stage-conclusions">
                    ${conclusions.map((value) => `<li>${esc(value)}</li>`).join("")}
                  </ul>
                ` : ""}
                ${warnings.length ? `
                  <ul class="first-review-stage-conclusions">
                    ${warnings.map((value) => `<li>${esc(`待处理：${value}`)}</li>`).join("")}
                  </ul>
                ` : ""}
                ${meta.length ? `<div class="first-review-stage-meta">${meta.map((value) => `<span>${esc(value)}</span>`).join("")}</div>` : ""}
              </div>
            </article>
          `;
        }).join("")
      : '<p class="first-review-audit-empty">第一轮 Skill 尚未记录工作过程。</p>';

    const reviewSummary = (
      audit?.review_summary && typeof audit.review_summary === "object"
        ? audit.review_summary
        : {}
    );
    const needsDecision = (value) => {
      const normalized = String(value || "").toUpperCase();
      return (
        normalized.includes("REQUIRED")
        || normalized.includes("PENDING")
        || normalized.includes("WAIT")
        || normalized.includes("BLOCK")
      );
    };
    const summaryFacts = Array.isArray(reviewSummary.key_facts) && reviewSummary.key_facts.length
      ? reviewSummary.key_facts
      : decisionRows
        .filter((row) => !needsDecision(row.status))
        .slice(0, 8)
        .map((row) => `${topicLabels[row.topic] || row.topic || "结论"}${row.target ? ` · ${row.target}` : ""}：${row.summary || "已记录"}`);
    const summaryQuestions = Array.isArray(reviewSummary.review_questions) && reviewSummary.review_questions.length
      ? reviewSummary.review_questions.map(humanizeFirstReviewBlocker)
      : decisionRows
        .filter((row) => needsDecision(row.status))
        .map((row) => `${topicLabels[row.topic] || row.topic || "待决定"}${row.target ? ` · ${row.target}` : ""}：${row.summary || row.reason || "需要确认"}`);
    const recommendations = Array.isArray(reviewSummary.final_recommendations)
      ? reviewSummary.final_recommendations
      : [];
    finalFacts.innerHTML = [...summaryFacts, ...recommendations].length
      ? [...summaryFacts, ...recommendations].map((value, index) => `
          <div class="first-review-final-item">
            <strong>${index < summaryFacts.length ? "已形成结论" : "建议"}</strong>
            <span>${esc(value)}</span>
          </div>
        `).join("")
      : '<div class="first-review-final-item"><strong>尚无最终结论</strong><span>请先运行第一轮商品准备 Skill。</span></div>';
    finalQuestions.innerHTML = summaryQuestions.length
      ? summaryQuestions.map((value) => `
          <div class="first-review-final-item question">
            <strong>需要 Kyle 决定</strong>
            <span>${esc(value)}</span>
          </div>
        `).join("")
      : '<div class="first-review-final-item"><strong>当前没有待决项</strong><span>此处保留首轮形成时记录；当前已批准快照继续有效。</span></div>';

    const completedStages = stageRows.filter((row) => (
      ["COMPLETED", "READY", "PASSED", "SUCCESS"].includes(String(row.status || "").toUpperCase())
    )).length;
    const confirmedWrites = operationRows.reduce(
      (total, row) => total + Math.max(0, Number(row.external_write_count || 0)),
      0,
    );
    const packetStatus = String(
      reviewSummary.status || audit?.packet_status || status,
    ).toUpperCase();
    workflowSummary.innerHTML = [
      ["第一轮状态", reviewSummary.headline || packetStatus],
      ["商品 revision", Number(audit?.revision || 0) || "—"],
      ["工作过程", `${completedStages}/${stageRows.length} 已完成`],
      ["确认外部写入", `${confirmedWrites} 次`],
    ].map(([label, value]) => `
      <div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>
    `).join("");
  }


window.OrbitOriginalRender={candidates:renderFirstReviewCandidates,audit:renderFirstReviewAudit};
document.addEventListener("DOMContentLoaded",()=>{document.querySelector("#firstReviewCandidatesSlot").innerHTML="<section id=\"firstReviewCandidates\" class=\"first-review-candidates\" aria-labelledby=\"firstReviewCandidatesTitle\">\n      <div class=\"section-heading\">\n        <div>\n          <p class=\"kicker\">FIRST REVIEW CANDIDATES</p>\n          <h2 id=\"firstReviewCandidatesTitle\">平台类目、英中审核稿与价格</h2>\n        </div>\n        <span id=\"firstReviewCandidatesBadge\" class=\"badge neutral\">尚未准备</span>\n      </div>\n      <p id=\"firstReviewCandidatesNotice\" class=\"first-review-candidates-notice\">\n        类目按平台汇总；文案展示冻结英文与中文对照，各目标语言读取同一首轮快照。\n      </p>\n      <div id=\"firstReviewTitleAssistantSlot\" class=\"first-review-title-assistant-slot\"></div>\n      <div id=\"firstReviewCandidateGrid\" class=\"first-review-candidate-grid\" aria-live=\"polite\"></div>\n      <div class=\"first-review-content-groups\">\n        <div>\n          <p class=\"kicker\">CONTENT GROUP OPTIONS</p>\n          <h3>内容分组选项</h3>\n        </div>\n        <div id=\"firstReviewContentGroupOptions\" class=\"first-review-content-group-options\" aria-live=\"polite\"></div>\n      </div>\n      <div id=\"firstReviewCandidateBlockers\" class=\"first-review-candidate-blockers\" aria-live=\"polite\"></div>\n    </section>";document.querySelector("#firstReviewAuditSlot").innerHTML="<section id=\"firstReviewAudit\" class=\"first-review-audit\" aria-labelledby=\"firstReviewAuditTitle\">\n      <div class=\"section-heading\">\n        <div>\n          <p class=\"kicker\">FIRST REVIEW AUDIT</p>\n          <h2 id=\"firstReviewAuditTitle\">决策与操作审计</h2>\n        </div>\n        <span id=\"firstReviewAuditBadge\" class=\"badge neutral\">尚未准备</span>\n      </div>\n      <p id=\"firstReviewAuditDisclosure\" class=\"first-review-audit-disclosure\">\n        这里展示可核验的证据来源、决策摘要与操作记录，不展示不可验证的内部思维链。\n      </p>\n      <div id=\"firstReviewWorkflowSummary\" class=\"first-review-workflow-summary\" aria-live=\"polite\"></div>\n      <div class=\"first-review-workflow-layout\">\n        <div>\n          <h3>工作过程</h3>\n          <p>按实际执行顺序展示任务、证据、结论、质检和修正结果。</p>\n          <div id=\"firstReviewWorkflowStages\" class=\"first-review-workflow-stages\" aria-live=\"polite\"></div>\n        </div>\n        <aside class=\"first-review-final-board\" aria-labelledby=\"firstReviewFinalBoardTitle\">\n          <p class=\"kicker\">FINAL REVIEW BOARD</p>\n          <h3 id=\"firstReviewFinalBoardTitle\">最终待你审核</h3>\n          <div id=\"firstReviewFinalFacts\" class=\"first-review-final-list\"></div>\n          <div id=\"firstReviewFinalQuestions\" class=\"first-review-final-list\"></div>\n          <p class=\"first-review-final-hint\">此处展示历史首轮记录，不产生新的批准或执行授权。</p>\n        </aside>\n      </div>\n      <details class=\"first-review-raw-audit\">\n        <summary>展开完整证据、逐目标决策与操作账本</summary>\n        <div class=\"first-review-audit-columns\">\n        <details open>\n          <summary>证据来源</summary>\n          <div id=\"firstReviewAuditEvidence\" class=\"first-review-audit-list\" aria-live=\"polite\"></div>\n        </details>\n        <details open>\n          <summary>决策摘要</summary>\n          <div id=\"firstReviewAuditDecisions\" class=\"first-review-audit-list\" aria-live=\"polite\"></div>\n        </details>\n        <details open>\n          <summary>操作记录</summary>\n          <div id=\"firstReviewAuditOperations\" class=\"first-review-audit-list\" aria-live=\"polite\"></div>\n        </details>\n        </div>\n      </details>\n    </section>";});
})();
