"""妙手公共采集箱匹配：按平台 SKU / itemNum / sourceItemId，支持 Temu。"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from core.config import ROOT
from modules.catalog.sku_key import tk_match_key
from modules.miaoshou.client import post_open

LIST_PATH = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_list"
DETAIL_PATH = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail"
INDEX_PATH = ROOT / "data" / "mx_confirm" / "collect_box_index.json"
DEFAULT_MAX_AGE_SEC = 6 * 3600


@dataclass
class CollectHit:
    mk: str
    common_id: int
    item_num: str
    source: str
    gmt_modified: str
    title: str


@dataclass
class CollectIndex:
    built_at: float
    by_mk: dict[str, CollectHit]
    by_pid: dict[str, int]

    def common_id_for_mk(self, mk: str) -> int | None:
        hit = self.by_mk.get(mk.zfill(4)[-4:])
        return hit.common_id if hit else None


def _fetch_collect_list(*, tab: str = "all") -> list[dict]:
    items: list[dict] = []
    for page in range(1, 100):
        resp = post_open(
            LIST_PATH,
            {
                "pageNo": page,
                "pageSize": 100,
                "filter": {"tabPaneName": tab, "sourceItemIdKeyword": ""},
            },
        )
        batch = (resp.get("data") or {}).get("detailList") or []
        if not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
    return items


def _fetch_detail(common_id: int) -> dict:
    resp = post_open(DETAIL_PATH, {"commonCollectBoxDetailId": int(common_id)})
    data = resp.get("data") or {}
    return data.get("editCommonCollectBoxDetail") or data


def _item_nums_from_detail(detail: dict) -> set[str]:
    out: set[str] = set()
    top = str(detail.get("itemNum") or "").strip()
    if top:
        out.add(top)
    for sku in (detail.get("skuMap") or {}).values():
        inum = str((sku or {}).get("itemNum") or "").strip()
        if inum:
            out.add(inum)
    return out


def _primary_source(it: dict) -> str:
    srcs = it.get("sourceList") or []
    if not srcs:
        return ""
    return str(srcs[0].get("source") or "")


def _collect_list_items() -> tuple[list[dict], set[int]]:
    """all 列表 + noClaimed id 集合（detail 只拉未认领，加速索引）。"""
    all_items = _fetch_collect_list(tab="all")
    no_claimed = _fetch_collect_list(tab="noClaimed")
    no_claimed_ids = {int(it["commonCollectBoxDetailId"]) for it in no_claimed}
    return all_items, no_claimed_ids


def build_collect_index(*, log: bool = True, workers: int = 12) -> CollectIndex:
    """扫描采集箱；Temu 等平台 SKU 在 detail.skuMap.itemNum（如 770810）。"""
    items, no_claimed_ids = _collect_list_items()
    by_mk: dict[str, CollectHit] = {}
    by_pid: dict[str, int] = {}
    need_detail: list[tuple[int, dict]] = []

    for it in items:
        cid = int(it["commonCollectBoxDetailId"])
        for s in it.get("sourceList") or []:
            pid = str(s.get("sourceItemId") or "").strip()
            if pid:
                by_pid[pid] = cid
            url = str(s.get("sourceItemUrl") or "")
            m = re.search(r"/(\d{15,20})", url)
            if m:
                by_pid.setdefault(m.group(1), cid)
        if it.get("itemNum"):
            _merge_item_nums(by_mk, {str(it["itemNum"])}, cid=cid, it=it)
        elif cid in no_claimed_ids:
            need_detail.append((cid, it))

    if log:
        print(
            f"  collect index: list={len(items)} noClaimed_detail={len(need_detail)}",
            flush=True,
        )

    def _detail_nums(cid: int) -> set[str]:
        try:
            return _item_nums_from_detail(_fetch_detail(cid))
        except Exception:
            return set()

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_detail_nums, cid): (cid, it) for cid, it in need_detail}
        for fut in as_completed(futures):
            cid, it = futures[fut]
            _merge_item_nums(by_mk, fut.result(), cid=cid, it=it)
            done += 1
            if log and done % 100 == 0:
                print(f"  collect detail… {done}/{len(need_detail)} mk={len(by_mk)}", flush=True)

    idx = CollectIndex(built_at=time.time(), by_mk=by_mk, by_pid=by_pid)
    _save_index(idx)
    if log:
        print(
            f"  collect index done: items={len(items)} mk={len(by_mk)} pid={len(by_pid)}",
            flush=True,
        )
    return idx


def _merge_item_nums(
    by_mk: dict[str, CollectHit],
    item_nums: set[str],
    *,
    cid: int,
    it: dict,
) -> None:
    title = str(it.get("title") or "")[:120]
    gmt = str(it.get("gmtModified") or "")
    source = _primary_source(it)
    for inum in item_nums:
        mk = tk_match_key(inum)
        if not mk or len(mk) != 4:
            continue
        prev = by_mk.get(mk)
        if prev and prev.gmt_modified > gmt:
            continue
        by_mk[mk] = CollectHit(
            mk=mk,
            common_id=cid,
            item_num=inum,
            source=source,
            gmt_modified=gmt,
            title=title,
        )


def _save_index(idx: CollectIndex) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "built_at": idx.built_at,
        "by_mk": {k: asdict(v) for k, v in idx.by_mk.items()},
        "by_pid": idx.by_pid,
    }
    tmp = INDEX_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(INDEX_PATH)


def _load_index_file() -> CollectIndex | None:
    if not INDEX_PATH.is_file():
        return None
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    by_mk = {k: CollectHit(**v) for k, v in (data.get("by_mk") or {}).items()}
    return CollectIndex(
        built_at=float(data.get("built_at") or 0),
        by_mk=by_mk,
        by_pid={str(k): int(v) for k, v in (data.get("by_pid") or {}).items()},
    )


def load_collect_index(*, max_age_sec: int = DEFAULT_MAX_AGE_SEC, rebuild: bool = False) -> CollectIndex:
    if not rebuild:
        cached = _load_index_file()
        if cached and (time.time() - cached.built_at) <= max_age_sec:
            return cached
    return build_collect_index()


def find_common_id(
    *,
    mk: str,
    seller_sku: str | None = None,
    product_id: str | None = None,
    index: CollectIndex | None = None,
) -> int | None:
    """按对齐码 / 平台 SKU / PH product_id 在采集箱中查找 commonCollectBoxDetailId。"""
    mk = str(mk).zfill(4)[-4:]
    idx = index or load_collect_index()

    hit = idx.common_id_for_mk(mk)
    if hit:
        return hit

    if seller_sku:
        sku_mk = tk_match_key(seller_sku)
        hit = idx.common_id_for_mk(sku_mk)
        if hit:
            return hit

    if product_id:
        pid = str(product_id).strip()
        if pid in idx.by_pid:
            return idx.by_pid[pid]
        resp = post_open(
            LIST_PATH,
            {
                "pageNo": 1,
                "pageSize": 5,
                "filter": {"tabPaneName": "all", "sourceItemIdKeyword": pid},
            },
        )
        items = (resp.get("data") or {}).get("detailList") or []
        if items:
            return int(items[0]["commonCollectBoxDetailId"])

    return None


def discover_collect_ready(
    *,
    prefix: str,
    limit: int,
    skip: set[str] | None = None,
    index: CollectIndex | None = None,
) -> list[str]:
    """从采集箱反查：前缀匹配、已采集、按 gmtModified 新→旧，不按编号顺序。"""
    from scripts.orbit_mx_migrate_prep import catalog_row

    idx = index or load_collect_index()
    skip = skip or set()
    pref = str(prefix).strip()
    rows: list[tuple[str, str, int]] = []
    for mk, hit in idx.by_mk.items():
        if pref and not mk.startswith(pref):
            continue
        if mk in skip:
            continue
        if not catalog_row(mk):
            continue
        rows.append((hit.gmt_modified, mk, hit.common_id))
    rows.sort(key=lambda x: x[0], reverse=True)
    out: list[str] = []
    seen: set[str] = set()
    for _, mk, _ in rows:
        if mk in seen:
            continue
        seen.add(mk)
        out.append(mk)
        if len(out) >= limit:
            break
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Rebuild Miaoshou collect box SKU index")
    ap.add_argument("--prefix", default="09")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()
    idx = load_collect_index(rebuild=True)
    keys = discover_collect_ready(prefix=args.prefix, limit=args.limit, index=idx)
    print(f"index mk={len(idx.by_mk)} discover {args.prefix}xx -> {keys}")
