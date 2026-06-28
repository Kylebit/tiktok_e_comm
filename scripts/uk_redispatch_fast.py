"""Fast UK re-dispatch: refresh POP on existing cards without collect index scan."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.uk_confirm import create_confirm_card
from modules.miaoshou.uk_web_approval import uk_approval_url
from scripts.uk_pop_pricing import fetch_cny_gbp, quote_match_key

CONFIRM_DIR = ROOT / "data" / "uk_confirm"
KEYS = ["0003", "0187", "0907", "0619", "0139", "0170"]


def _latest_card_meta(mk: str) -> dict | None:
    best: tuple[float, dict] | None = None
    for path in CONFIRM_DIR.glob("*.json"):
        if path.name in ("feishu_dispatch_last.json", "orbit_dry_run.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(data.get("match_key", "")).zfill(4)[-4:] != mk:
            continue
        ts = float(data.get("created_at") or 0)
        if best is None or ts > best[0]:
            best = (ts, data)
    return best[1] if best else None


def main() -> int:
    rate = fetch_cny_gbp()
    queued: list[dict] = []
    for mk in KEYS:
        meta = _latest_card_meta(mk)
        q = quote_match_key(mk, cny_gbp=rate)
        card = create_confirm_card(
            pop_quote=q,
            collect_box_detail_id=int(meta.get("collect_box_detail_id") or 0) if meta else 0,
            seller_sku=str(meta.get("seller_sku") or q.seller_sku) if meta else q.seller_sku,
            master_product_id=str(meta.get("master_product_id") or "") if meta else "",
            master_region=str(meta.get("master_region") or "PH") if meta else "PH",
            product_name=str(meta.get("product_name") or q.seller_sku) if meta else q.seller_sku,
            main_image_url=str(meta.get("main_image_url") or "") if meta else "",
            stock=int(meta.get("stock") or 200) if meta else 200,
        )
        row = {
            "mk": mk,
            "token": card.token,
            "list_ceil": card.list_price_ceil_gbp,
            "sale": round(card.sale_price_gbp, 2),
            "profit_pct": round(card.profit_margin_on_sale_pct, 2),
            "url": uk_approval_url(card.token),
        }
        queued.append(row)
        print(
            f"  queued {mk} ceil=GBP {row['list_ceil']} sale=GBP {row['sale']:.2f} "
            f"profit={row['profit_pct']}% -> {row['url']}"
        )
    out = CONFIRM_DIR / "feishu_dispatch_last.json"
    out.write_text(
        json.dumps({"keys": KEYS, "queued_web": queued, "summary": f"re-dispatch {len(queued)}"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f">>> Web inbox: {uk_approval_url()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
