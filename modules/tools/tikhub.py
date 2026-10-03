from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from modules.tools.provider_http import open_request


API_BASE = "https://api.tikhub.io"
PRODUCT_SEARCH_PATH = "/api/v1/tiktok/shop/web/fetch_search_products_list"
VIDEO_SEARCH_PATH = "/api/v1/tiktok/app/v3/fetch_video_search_result"


def load_api_key() -> tuple[str, str]:
    value = os.environ.get("TikHub")
    if value:
        return value, "process_environment"

    raise RuntimeError("TikHub API key was not found in the TikHub environment variable")


def request_json(path: str, params: dict[str, object], api_key: str) -> tuple[dict, dict]:
    query = urllib.parse.urlencode(params)
    url = f"{API_BASE}{path}?{query}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "Agent-PR-TikHub-Reference-Probe/1.0",
    }
    attempts: list[dict[str, object]] = []

    for attempt in range(1, 2):
        started = time.monotonic()
        try:
            with open_request(
                urllib.request.Request(url, headers=headers), timeout=30
            ) as response:
                body = response.read().decode("utf-8")
                attempts.append(
                    {
                        "attempt": attempt,
                        "http_status": response.status,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                    }
                )
                return json.loads(body), {
                    "endpoint": path,
                    "params": params,
                    "attempts": attempts,
                }
        except urllib.error.HTTPError as exc:
            attempts.append(
                {
                    "attempt": attempt,
                    "http_status": exc.code,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }
            )
            raise RuntimeError(f"TikHub request failed with HTTP {exc.code}") from exc

    raise AssertionError("unreachable")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def preview(*, keyword: str, region: str, video_count: int = 20) -> dict:
    import re
    keyword = urllib.parse.unquote_plus(keyword).strip()
    region = region.upper()
    if not keyword or len(keyword) > 300 or not re.fullmatch(r'[A-Z]{2}', region):
        raise ValueError('keyword and two-letter region required')
    if type(video_count) is not int or not 1 <= video_count <= 20:
        raise ValueError('video_count must be between 1 and 20')
    return {'provider': 'tikhub-reference/v1', 'origin': API_BASE, 'keyword': keyword,
            'region': region, 'video_count': video_count, 'maximum_paid_requests': 2,
            'rights': 'reference_only_no_publication_authority', 'network_call_performed': False,
            'requests': [
                {'endpoint': PRODUCT_SEARCH_PATH, 'params': {'search_word': keyword, 'offset': 0, 'page_token': '', 'region': region}},
                {'endpoint': VIDEO_SEARCH_PATH, 'params': {'keyword': keyword, 'offset': 0, 'count': video_count,
                    'sort_type': 0, 'publish_time': 0, 'region': region}}]}


def collect(*, plan: dict, tenant_id: str, profile_digest: str, artifact_root: Path,
            authorization: dict, api_key: str, request_fn=None) -> dict:
    """Run the two retained requests once. Resume completed raw files; unknown stays occupied."""
    import hashlib
    import re
    from modules.sourcing.image_generation_checkpoint import atomic_json, business_lock, digest
    from shared_platform.capability_runtime import checked_path
    if (not re.fullmatch(r'[a-f0-9]{64}', profile_digest)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', tenant_id)):
        raise ValueError('tenant/profile identity is invalid')
    if plan != preview(**{k: plan[k] for k in ('keyword', 'region', 'video_count')}):
        raise ValueError('TikHub plan differs from the bounded retained query contract')
    scope = {'tenant_id': tenant_id, 'profile_digest': profile_digest, 'plan_digest': digest(plan), 'maximum_paid_requests': 2}
    if (authorization.get('scope') != scope or not authorization.get('authorization_id')
            or not re.fullmatch(r'(?:audit|fixture)://[A-Za-z0-9_./:-]{1,220}', str(authorization.get('instruction_ref', '')))):
        raise ValueError('existing authorization must bind the complete TikHub query and two-request budget')
    root = checked_path(Path(artifact_root), 'tikhub/' + profile_digest + '/' + digest(plan)[:24])
    def check_files():
        for name in ('manifest.json', 'products.raw.json', 'videos.raw.json'):
            checked_path(root, name); checked_path(root, name+'.tmp')
        checked_path(root, f'.lingshi-{digest(scope)[:24]}.lock')
    check_files()
    state_path = checked_path(root, 'manifest.json'); request_fn = request_fn or request_json
    with business_lock(root, digest(scope)):
        check_files()
        state = {'schema': 'tikhub-reference-run/v2', 'scope': scope, 'plan': plan,
                 'purpose': 'reference_only_no_publication_authority', 'source': 'TikHub unofficial third-party API',
                 'collected_at_utc': datetime.now(timezone.utc).isoformat(), 'credential_persisted': False,
                 'authorization_digest': digest(authorization), 'requests': []}
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding='utf-8'))
            if (state.get('scope') != scope or state.get('plan') != plan
                    or state.get('digest') != digest({k: v for k, v in state.items() if k != 'digest'})):
                raise ValueError('TikHub run manifest damaged or belongs to another request; preserve it')
        def save():
            check_files()
            state['digest'] = digest({k: v for k, v in state.items() if k != 'digest'})
            atomic_json(state_path, state)
        new_count = 0
        for index, request_plan in enumerate(plan['requests']):
            check_files()
            raw_path = checked_path(root, 'products.raw.json' if index == 0 else 'videos.raw.json')
            if len(state['requests']) > index:
                row = state['requests'][index]
                if row['request'] != request_plan: raise ValueError('TikHub retained request differs')
                if row['status'] != 'COMPLETED':
                    raise RuntimeError('TikHub request outcome unknown; preserve budget and reconcile provider usage before retry')
                if not raw_path.exists() or hashlib.sha256(raw_path.read_bytes()).hexdigest() != row['raw_sha256']:
                    raise ValueError('completed TikHub raw evidence missing or changed; no new query')
                continue
            row = {'request': request_plan, 'status': 'SUBMITTING', 'paid_request_count': 1}
            state['requests'].append(row); save()
            new_count += 1
            try:
                raw, audit = request_fn(request_plan['endpoint'], request_plan['params'], api_key)
                if not isinstance(raw, dict): raise ValueError('TikHub raw response must be an object')
                check_files()
                atomic_json(raw_path, raw)
                row.update(status='COMPLETED', raw_sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(), audit=audit)
                save()
            except Exception:
                row['status'] = 'UNKNOWN'; save()
                raise RuntimeError('TikHub query outcome unknown; no automatic paid retry') from None
        return {'status': 'COMPLETED', 'manifest_path': str(state_path), 'scope': scope,
                'paid_request_count': len(state['requests']), 'new_paid_request_count': new_count,
                'external_business_writes': 0, 'rights': plan['rights']}


def percentile(values: list[int | float], fraction: float) -> float | None:
    if not values: return None
    ordered = sorted(values); position = (len(ordered) - 1) * fraction
    lower = int(position); upper = min(lower + 1, len(ordered) - 1); weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def analyze(snapshot: dict) -> dict:
    """Retain the source's sample statistics, including explicit unknown for empty samples."""
    products = snapshot['products']; videos = snapshot['videos']
    def numbers(rows, key):
        values = [row[key] for row in rows if row.get(key) is not None]
        if any(type(v) not in (int, float) or v < 0 for v in values):
            raise ValueError('sample counts must be non-negative numeric values or null')
        return values
    sold = numbers(products, 'sold_count'); views = numbers(videos, 'play_count'); total = sum(sold)
    return {'products': {'returned': len(products), 'with_sold_count': len(sold),
            'distinct_shops': len({r['seller_id'] for r in products if r.get('seller_id')}),
            'min': min(sold) if sold else None, 'p25': percentile(sold, .25),
            'median': statistics.median(sold) if sold else None, 'p75': percentile(sold, .75),
            'max': max(sold) if sold else None, 'sum_not_market_total': total if sold else None,
            'top3_share_of_sample_sum': sum(sorted(sold, reverse=True)[:3])/total if total else None,
            **{f'count_ge_{n}': sum(v >= n for v in sold) for n in (100, 500, 1000)}},
            'videos': {'returned': len(videos), 'median_views': statistics.median(views) if views else None,
            'max_views': max(views) if views else None, 'count_ge_100k': sum(v >= 100000 for v in views)}}
