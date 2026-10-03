"""Local intake, adapted from the retained r4 service with recoverable installs.

A request UUID owns one immutable image/source record. Installing its workbench
projections can be resumed after interruption; subsequent user edits are retained.
No provider identity, approval, collection or paid operation is created here.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
from pathlib import Path
import re
import threading
from uuid import UUID, uuid4

from PIL import Image, UnidentifiedImageError
from core.config import ROOT

SCHEMA_VERSION = 'manual-product-intake/v1'
MAX_IMAGE_COUNT = 12
MAX_IMAGE_BYTES = 24 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 64 * 1024 * 1024
MAX_BODY_BYTES = 90 * 1024 * 1024
_FORMATS = {'image/png': ('PNG', 'png'), 'image/jpeg': ('JPEG', 'jpg'), 'image/webp': ('WEBP', 'webp')}
_lock = threading.RLock()


class ManualIntakeError(ValueError):
    pass


def _text(value, field, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ManualIntakeError(f'{field} is required and must contain at most {limit} characters')
    return value.strip()


def _number(value, field):
    try:
        number = Decimal(str(value))
    except (ValueError, InvalidOperation):
        raise ManualIntakeError(f'{field} must be a positive number') from None
    if not number.is_finite() or not 0 < number <= Decimal('1000000000'):
        raise ManualIntakeError(f'{field} must be finite, positive and at most 1000000000')
    return format(number, 'f')


def _safe(root, relative):
    root = Path(root).resolve()
    path = root / relative
    for part in [path, *path.parents]:
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ManualIntakeError('local intake path must not contain links')
    if not path.resolve().is_relative_to(root):
        raise ManualIntakeError('local intake path escapes its root')
    return path


def _read(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ManualIntakeError('local intake record must be an object')
    return value


def _write(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def _normalize(payload):
    if not isinstance(payload, dict) or set(payload) != {'request_id','title','description','variants','images'}:
        raise ManualIntakeError('request_id, title, description, variants and images are required')
    try:
        request_id = str(UUID(payload['request_id']))
    except (ValueError, TypeError, AttributeError):
        raise ManualIntakeError('request_id must be a UUID retained for retries') from None
    title = _text(payload['title'], 'title', 300)
    description = _text(payload['description'], 'description', 20000)
    variants = payload['variants']
    if not isinstance(variants, list) or not 1 <= len(variants) <= 50:
        raise ManualIntakeError('variants must contain 1-50 rows')
    normalized, labels = [], set()
    for index, row in enumerate(variants, 1):
        if not isinstance(row, dict):
            raise ManualIntakeError('variant must be an object')
        label = _text(row.get('label'), 'variant label', 120)
        key = ' '.join(label.split()).casefold()
        if key in labels:
            raise ManualIntakeError('variant labels must be unique')
        labels.add(key)
        dims = row.get('package_cm')
        if not isinstance(dims, list) or len(dims) != 3:
            raise ManualIntakeError('package_cm must contain three dimensions')
        weight = _number(row.get('weight_kg'), 'weight_kg')
        normalized.append(dict(key=f'manual-{index:02d}', name=label, item_num='',
            price=_number(row.get('purchase_cost_cny'), 'purchase_cost_cny'), stock=0,
            weight=weight, weight_kg=weight, package_cm=[_number(v, 'package_cm') for v in dims]))
    images = payload['images']
    if not isinstance(images, list) or not 1 <= len(images) <= MAX_IMAGE_COUNT:
        raise ManualIntakeError('images must contain 1-12 files')
    decoded, total = [], 0
    for index, row in enumerate(images, 1):
        if not isinstance(row, dict) or row.get('mime_type') not in _FORMATS:
            raise ManualIntakeError('images must be JPEG, PNG or WebP')
        encoded = _text(row.get('data_base64'), 'data_base64', (MAX_IMAGE_BYTES + 2)//3*4)
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ManualIntakeError('image base64 is invalid') from None
        total += len(raw)
        if not raw or len(raw) > MAX_IMAGE_BYTES or total > MAX_TOTAL_IMAGE_BYTES:
            raise ManualIntakeError('image exceeds 24 MB or total exceeds 64 MB')
        fmt, ext = _FORMATS[row['mime_type']]
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != fmt or image.width * image.height > 40_000_000:
                    raise ManualIntakeError('image format or pixel count is invalid')
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
        except (OSError, ValueError, Image.DecompressionBombError, UnidentifiedImageError):
            raise ManualIntakeError('image is incomplete or does not match its MIME type') from None
        decoded.append(dict(bytes=raw, filename=f'image-{index:02d}.{ext}', position=index,
            mime_type=row['mime_type'], original_name=_text(row.get('name'), 'image name', 180),
            byte_count=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    material = dict(request_id=request_id, title=title, description=description, variants=normalized,
        images=[{k:v for k,v in row.items() if k != 'bytes'} for row in decoded])
    digest = hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()).hexdigest()
    return material, decoded, digest


def _projections(record):
    offer = record['offer_id']
    first = record['variants'][0]
    images = [dict(url=r['url'], kind='main', action='review', note='本地来源图，尚待审核') for r in record['images']]
    source = dict(offer_id=offer, source_url='', source_authority='manual-intake', source_mode='manual_intake',
        source_record={'source_id':offer}, title_source=record['title'], title_source_kind='manual-intake',
        description=record['description'], cost_cny=first['price'], weight_kg=first['weight'], weight_is_estimate=False,
        package_cm=first['package_cm'], package_is_estimate=False, category={'id':'','name':'','confidence':'manual-unresolved'},
        images=images, attributes={'来源方式':'本地图片与文字录入'}, skus=record['variants'],
        precollect={'mode':'manual_intake','records':[{'source_id':offer,'source':'manual-intake','status':'success'}], 'claimed':False,'published':False},
        risks=['本地输入尚待审核，类目与卖点未确认'])
    review = dict(title=record['title'], category=source['category'], cost_cny=first['price'], weight_kg=first['weight'],
        package_cm=first['package_cm'], video_action='none', video_url='', image_actions=images,
        image_order=[], selected_sku_keys=[r['key'] for r in record['variants']],
        sku_label_overrides={}, fields_locked=False, sku_commercial_facts={r['key']:dict(cost_cny=r['price'],weight_kg=r['weight'],package_cm=r['package_cm']) for r in record['variants']})
    state = dict(_revision=1, offer_id=offer, updated_at=record['created_at'], source=source, review=review,
        collection={'source':'manual_intake','local_only':True,'intake_request_id':record['request_id'],
            'intake_digest':record['request_digest'],'intake_record':f'data/product_intake/{offer}/record.json'})
    precollect = dict(offer_id=offer, mode='manual_intake', source_authority='manual-intake', intake_request_id=record['request_id'],
        normalized={}, records=source['precollect']['records'], claimed=False, published=False)
    return {f'{offer}_miaoshou.json':precollect, f'{offer}.json':state}


def _install_state(root, name, value, request_id):
    path = _safe(root, 'data/new_product_workbench/'+name)
    if path.exists():
        old = _read(path)
        identity = old.get('intake_request_id') or (old.get('collection') or {}).get('intake_request_id')
        if identity != request_id or old.get('offer_id') != value['offer_id']:
            raise ManualIntakeError('workbench identity conflicts with local intake')
        return  # A retry must never overwrite subsequent user revisions.
    temp = _safe(root, f'data/new_product_workbench/.{uuid4().hex}.tmp')
    _write(temp, value)
    try:
        os.link(temp, path)  # Exclusive complete-file install, including across processes.
    except FileExistsError:
        _install_state(root, name, value, request_id)
    finally:
        temp.unlink(missing_ok=True)


def create_manual_intake(payload, *, root=ROOT):
    material, images, digest = _normalize(payload)
    offer = '8'+f"{int(hashlib.sha256(material['request_id'].encode()).hexdigest(),16) % 10**18:018d}"
    with _lock:
        intake = _safe(root, 'data/product_intake')
        workbench = _safe(root, 'data/new_product_workbench')
        final = _safe(root, f'data/product_intake/{offer}')
        intake.mkdir(parents=True, exist_ok=True)
        workbench.mkdir(parents=True, exist_ok=True)
        existed = final.exists()
        if not existed:
            if any((workbench/name).exists() for name in (f'{offer}.json',f'{offer}_miaoshou.json')):
                raise ManualIntakeError('local identity is already occupied')
            stage = _safe(root, f'data/product_intake/.{offer}-{uuid4().hex}.tmp')
            (stage/'images').mkdir(parents=True)
            record = {**material, 'offer_id':offer, 'schema_version':SCHEMA_VERSION, 'request_digest':digest,
                'source_authority':'manual-intake', 'source_mode':'manual_intake',
                'created_at':datetime.now(timezone.utc).isoformat(), 'external_writes_performed':[]}
            for image, public in zip(images, record['images']):
                (stage/'images'/image['filename']).write_bytes(image['bytes'])
                public['url'] = f"/api/product-workspace/manual-intake-image?offer_id={offer}&image={image['filename']}"
            _write(stage/'record.json', record)
            try:
                stage.rename(final)
            except FileExistsError:
                existed = True  # Another process committed this request; verify below.
        record = _read(_safe(root, f'data/product_intake/{offer}/record.json'))
        if record.get('request_digest') != digest or record.get('request_id') != material['request_id'] or record.get('offer_id') != offer:
            raise ManualIntakeError('request was already used with different facts')
        for image in record['images']:
            if resolve_manual_intake_image(offer, image['filename'], root=root) is None:
                raise ManualIntakeError('persisted image requires local recovery')
        for name, value in _projections(record).items():
            _install_state(root, name, value, material['request_id'])
        return dict(ok=True, schema_version=SCHEMA_VERSION, offer_id=offer, source_mode='manual_intake',
            source_authority='manual-intake', idempotent=existed, request_id=material['request_id'],
            image_count=len(images), variant_count=len(record['variants']), external_writes_performed=[])


def resolve_manual_intake_image(offer_id, image_name, *, root=ROOT):
    if not isinstance(offer_id,str) or not re.fullmatch(r'[0-9]{1,32}',offer_id):
        return None
    if not isinstance(image_name,str) or not re.fullmatch(r'image-[0-9]{2}\.(png|jpg|webp)',image_name):
        return None
    try:
        directory = _safe(root, f'data/product_intake/{offer_id}')
        record = _read(_safe(root, f'data/product_intake/{offer_id}/record.json'))
        if record.get('offer_id') != offer_id or record.get('source_authority') != 'manual-intake':
            return None
        row = next((r for r in record['images'] if r.get('filename') == image_name), None)
        candidate = _safe(root, f'data/product_intake/{offer_id}/images/{image_name}')
        if not row or not candidate.is_file() or candidate.stat().st_size != row['byte_count']:
            return None
        if hashlib.sha256(candidate.read_bytes()).hexdigest() != row['sha256']:
            return None
        return candidate
    except (OSError, ValueError, KeyError, TypeError):
        return None


R1_PATH_BINDING_CONTRACT = 'orbit-r1-paths/v2'


def load_manual_source(offer_id, *, root=ROOT, data_root=None):
    if not isinstance(offer_id,str) or not re.fullmatch(r'[0-9]{1,32}',offer_id):
        raise ManualIntakeError('invalid local identity')
    record = _read(_safe(data_root, f'product_intake/{offer_id}/record.json') if data_root is not None
                   else _safe(root, f'data/product_intake/{offer_id}/record.json'))
    if record.get('offer_id') != offer_id or record.get('source_authority') != 'manual-intake':
        raise ManualIntakeError('manual source record identity conflicts')
    return deepcopy(_projections(record)[f'{offer_id}.json']['source'])
