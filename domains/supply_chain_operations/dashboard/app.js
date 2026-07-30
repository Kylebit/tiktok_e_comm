const facts = [
  { rank: 1, sku: "0003", label: "3片装拱门植物墙贴", sold: 187, days: 30, lead: 25, safety: 5, dimsMm: [300, 40, 40], cost: 7, priceMyr: 31.1 },
  { rank: 2, sku: "0015", label: "4片装冰箱防水垫", sold: 80, days: 30, lead: 25, safety: 5, dimsMm: [400, 100, 105], cost: 5, priceMyr: 30 },
  { rank: 3, sku: "0008", label: "绿植藤蔓墙贴", sold: 79, days: 30, lead: 25, safety: 5, dimsMm: [320, 50, 50], cost: 5, priceMyr: 19.8 },
  { rank: 4, sku: "0007", label: "乡村玫瑰墙贴", sold: 77, days: 30, lead: 25, safety: 5, dimsMm: [315, 57.5, 95], cost: 4, priceMyr: 17.99 }
];

const ceil = Math.ceil;
const velocity = item => item.sold / item.days;
const arrivalTarget = item => ceil(velocity(item) * (30 + item.safety));
const leadDemand = item => ceil(velocity(item) * item.lead);

function numericOrUnknown(input) {
  if (input.value.trim() === "") return null;
  const value = Number(input.value);
  return Number.isInteger(value) && value >= 0 ? value : null;
}

function calculate(item, available, inbound) {
  const complete = available !== null && inbound !== null;
  const trusted = (available || 0) + (inbound || 0);
  const projected = Math.max(0, trusted - leadDemand(item));
  return {
    complete,
    qty: Math.max(0, arrivalTarget(item) - projected),
    projected
  };
}

function renderRows() {
  const target = document.querySelector("#skuRows");
  target.innerHTML = facts.map(item => `
    <tr data-sku="${item.sku}">
      <td><div class="sku-cell"><span class="rank">${String(item.rank).padStart(2, "0")}</span><div><strong>${item.sku}</strong><small>${item.label}</small></div></div></td>
      <td><strong>${item.sold}</strong> 件</td>
      <td>${velocity(item).toFixed(2)} 件/天</td>
      <td>${arrivalTarget(item)} 件</td>
      <td><input class="qty-input available" inputmode="numeric" min="0" placeholder="未知" aria-label="${item.sku} 可用库存"></td>
      <td><input class="qty-input inbound" inputmode="numeric" min="0" placeholder="未知" aria-label="${item.sku} 可信在途"></td>
      <td><strong class="recommended">${arrivalTarget(item)}</strong> 件</td>
      <td><span class="decision-pill">库存未扣减上限</span></td>
    </tr>
  `).join("");

  target.querySelectorAll("input").forEach(input => input.addEventListener("input", recalculate));
}

function recalculate() {
  let total = 0;
  let volume = 0;
  let capital = 0;
  let taxBenefit = 0;
  facts.forEach(item => {
    const row = document.querySelector(`[data-sku="${item.sku}"]`);
    const available = numericOrUnknown(row.querySelector(".available"));
    const inbound = numericOrUnknown(row.querySelector(".inbound"));
    const result = calculate(item, available, inbound);
    row.querySelector(".recommended").textContent = result.qty;
    const pill = row.querySelector(".decision-pill");
    pill.textContent = result.complete ? (result.qty > 0 ? "建议补货" : "库存足够") : "库存未扣减上限";
    total += result.qty;
    volume += item.dimsMm.reduce((a, b) => a * b, 1) / 1e9 * result.qty;
    capital += item.cost * result.qty;
    taxBenefit += item.priceMyr * 1.659101 * 0.10 * result.qty;
  });
  const headFreight = total > 0 ? Math.max(0.3, volume) * 580 : 0;
  const localHandling = total * (0.15 + 2.2 + 0.3) + volume * 10;
  const knownBenefit = taxBenefit - headFreight - localHandling;
  document.querySelector("#batchQty").textContent = total.toLocaleString("zh-CN");
  document.querySelector("#batchVolume").textContent = `${volume.toFixed(3)} m³`;
  document.querySelector("#workingCapital").textContent = `¥${Math.round(capital).toLocaleString("zh-CN")}`;
  document.querySelector("#knownBenefit").textContent = `${knownBenefit >= 0 ? "+" : "−"}¥${Math.abs(Math.round(knownBenefit)).toLocaleString("zh-CN")}`;
}

document.querySelectorAll("[data-scroll]").forEach(button => {
  button.addEventListener("click", () => document.querySelector(button.dataset.scroll).scrollIntoView({ behavior: "smooth" }));
});

renderRows();
recalculate();
