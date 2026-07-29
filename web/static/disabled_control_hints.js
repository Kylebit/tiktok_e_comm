(() => {
  "use strict";

  const STATUS_BY_CONTROL = Object.freeze({
    releasePlanCheckbox: ["releasePlanMessage"],
    prepareMiaoshouCheckbox: ["prepareMiaoshouMessage"],
    commonOverwriteCheckbox: ["commonOverwriteRisk", "commonOverwriteMessage"],
    publishAllCheckbox: ["publishAllNote", "publishRunMessage"],
  });
  let hintSequence = 0;

  function statusTextFor(control) {
    const preferred = STATUS_BY_CONTROL[control.id] || [];
    for (const id of preferred) {
      const text = String(document.getElementById(id)?.textContent || "").trim();
      if (text) return text;
    }
    return "";
  }

  function disabledReason(control) {
    const explicit = String(control.dataset.disabledReason || "").trim();
    if (explicit) return explicit;
    const known = statusTextFor(control);
    if (known) return known;
    return "当前前置条件尚未满足；完成本区域上方步骤后会自动开放。";
  }

  function syncControl(control) {
    const label = control.closest("label");
    if (!label) return;
    let hint = label.querySelector(".disabled-control-reason");
    if (!control.disabled) {
      hint?.remove();
      control.removeAttribute("aria-describedby");
      control.removeAttribute("title");
      return;
    }
    if (!hint) {
      hint = document.createElement("small");
      hint.className = "disabled-control-reason";
      hint.id = control.id
        ? `${control.id}DisabledReason`
        : `disabledCheckboxReason${++hintSequence}`;
      hint.setAttribute("role", "status");
      label.appendChild(hint);
    }
    const message = `暂不可选：${disabledReason(control)}`;
    if (hint.textContent !== message) hint.textContent = message;
    control.setAttribute("aria-describedby", hint.id);
    control.setAttribute("title", message);
  }

  function syncAll() {
    document.querySelectorAll('input[type="checkbox"]').forEach(syncControl);
  }

  let scheduled = false;
  function scheduleSync() {
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(() => {
      scheduled = false;
      syncAll();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    syncAll();
    new MutationObserver(scheduleSync).observe(document.body, {
      attributes: true,
      attributeFilter: ["disabled", "hidden", "data-disabled-reason"],
      characterData: true,
      childList: true,
      subtree: true,
    });
  });
})();
