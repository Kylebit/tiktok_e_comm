# Fixed legacy Shopee producer excerpts

Source: `D:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm\modules\shopee\orders_pull.py`

SHA-256: `401e9ac9b3a4753f82b06cda3fb6a974ed9924ed98d18f74a7448d4dbb808a12`

These excerpts are copied read-only from the producer that created the fixed historical snapshot. They contain no credential or order data.

## utc_display

Lines 44-50

```python
44: def _fmt_ts(ts: int | float | None) -> str:
45:     if not ts:
46:         return ""
47:     try:
48:         return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
49:     except (OSError, OverflowError, ValueError):
50:         return ""
```

## list_pagination

Lines 83-116

```python
83: def iter_escrow_list(
84:     shop_id: int,
85:     token: str,
86:     *,
87:     time_from: int,
88:     time_to: int,
89: ) -> list[dict[str, Any]]:
90:     out: list[dict[str, Any]] = []
91:     page = 1
92:     while True:
93:         resp = shop_get(
94:             "/api/v2/payment/get_escrow_list",
95:             shop_id,
96:             token,
97:             {
98:                 "release_time_from": int(time_from),
99:                 "release_time_to": int(time_to),
100:                 "page_size": 100,
101:                 "page_no": page,
102:             },
103:         )
104:         if resp.get("error"):
105:             raise RuntimeError(resp.get("message") or resp.get("error") or str(resp))
106:         body = resp.get("response") or {}
107:         batch = body.get("escrow_list") or []
108:         out.extend(batch)
109:         if not body.get("more"):
110:             break
111:         page += 1
112:         if page > 200:
113:             break
114:     return out
115: 
116: 
```

## normalized_fields

Lines 129-202

```python
129: def _normalize_detail(
130:     *,
131:     region: str,
132:     currency: str,
133:     list_row: dict[str, Any],
134:     detail: dict[str, Any],
135: ) -> list[dict[str, Any]]:
136:     """一单可能多 SKU → 多行；结算金额按 discounted_price 比例分摊。"""
137:     oi = detail.get("order_income") or {}
138:     settlement = _f(oi.get("escrow_amount_after_adjustment") or oi.get("escrow_amount"))
139:     if settlement == 0:
140:         settlement = _f(list_row.get("payout_amount"))
141:     release_ts = list_row.get("escrow_release_time")
142:     release_str = _fmt_ts(release_ts)
143:     order_sn = str(detail.get("order_sn") or list_row.get("order_sn") or "")
144:     items = oi.get("items") or []
145:     if not items:
146:         items = [
147:             {
148:                 "model_sku": "",
149:                 "item_name": "",
150:                 "discounted_price": _f(oi.get("buyer_total_amount")),
151:                 "selling_price": _f(oi.get("buyer_total_amount")),
152:                 "quantity_purchased": 1,
153:             }
154:         ]
155: 
156:     weights = []
157:     for it in items:
158:         w = _f(it.get("discounted_price") or it.get("selling_price")) * max(
159:             int(it.get("quantity_purchased") or 1), 1
160:         )
161:         weights.append(max(w, 0.01))
162:     total_w = sum(weights) or 1.0
163: 
164:     rows = []
165:     for it, w in zip(items, weights):
166:         sku = str(it.get("model_sku") or it.get("item_sku") or "").strip()
167:         sale = _f(it.get("discounted_price") or it.get("selling_price"))
168:         qty = max(int(it.get("quantity_purchased") or 1), 1)
169:         subtotal = sale * qty if sale else w
170:         share = settlement * (w / total_w)
171:         unit_cost = _cost_by_seller_sku(sku)
172:         product_cost = unit_cost * qty if unit_cost is not None else None
173:         rows.append(
174:             {
175:                 "region": region,
176:                 "currency": currency,
177:                 "order_sn": order_sn,
178:                 "seller_sku": sku,
179:                 "product_name": str(it.get("item_name") or it.get("model_name") or "")[:200],
180:                 "status": "COMPLETED",
181:                 "sale_local": round(subtotal, 2),
182:                 "settlement_local": round(share, 2),
183:                 "quantity": qty,
184:                 "release_time": release_str,
185:                 "release_ts": int(release_ts or 0),
186:                 # ``product_cost`` remains the backwards-compatible total
187:                 # line cost.  New consumers should use the explicit fields.
188:                 "unit_cost_cny": unit_cost,
189:                 "product_cost_cny": product_cost,
190:                 "product_cost": product_cost,
191:                 "commission_fee": _f(oi.get("commission_fee")) * (w / total_w),
192:                 "service_fee": _f(oi.get("service_fee")) * (w / total_w),
193:                 "source": "shopee_api_escrow",
194:             }
195:         )
196:     return rows
197: 
198: 
199: def _headers() -> list[dict[str, str]]:
200:     return [
201:         {"name": "Purchase Date"},
202:         {"name": "Order SN"},
```

## json_envelope

Lines 288-307

```python
288: 
289: def write_json_snapshot(
290:     *,
291:     region: str,
292:     start: date,
293:     end: date,
294:     norms: list[dict[str, Any]],
295: ) -> Path:
296:     OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
297:     path = OUTPUT_DIR / f"shopee_escrow_{region}_{_ymd(start)}_{_ymd(end)}.json"
298:     payload = {
299:         "region": region,
300:         "start": start.isoformat(),
301:         "end": end.isoformat(),
302:         "pulled_at": datetime.now(timezone.utc).isoformat(),
303:         "count": len(norms),
304:         "rows": norms,
305:     }
306:     path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
307:     return path
```

## utc_request_bounds_and_dedupe

Lines 323-356

```python
323: 
324:     shop_id, token = _shop_ctx(region)
325:     currency = {"TH": "THB", "MY": "MYR", "PH": "PHP", "VN": "VND"}.get(region.upper(), "THB")
326: 
327:     # 切窗拉取 escrow_list
328:     list_rows: list[dict[str, Any]] = []
329:     cursor = start
330:     while cursor <= end:
331:         chunk_end = min(cursor + timedelta(days=CHUNK_DAYS - 1), end)
332:         t0 = int(datetime(cursor.year, cursor.month, cursor.day, tzinfo=timezone.utc).timestamp())
333:         t1 = int(
334:             (
335:                 datetime(chunk_end.year, chunk_end.month, chunk_end.day, tzinfo=timezone.utc)
336:                 + timedelta(days=1)
337:             ).timestamp()
338:         )
339:         batch = iter_escrow_list(shop_id, token, time_from=t0, time_to=t1)
340:         list_rows.extend(batch)
341:         cursor = chunk_end + timedelta(days=1)
342: 
343:     # 去重 order_sn（保留最新 release）
344:     by_sn: dict[str, dict[str, Any]] = {}
345:     for row in list_rows:
346:         sn = str(row.get("order_sn") or "")
347:         if not sn:
348:             continue
349:         prev = by_sn.get(sn)
350:         if not prev or int(row.get("escrow_release_time") or 0) >= int(
351:             prev.get("escrow_release_time") or 0
352:         ):
353:             by_sn[sn] = row
354: 
355:     print(f"  [shopee {region}] escrow_list={len(by_sn)} orders · fetching details…", flush=True)
356: 
```
