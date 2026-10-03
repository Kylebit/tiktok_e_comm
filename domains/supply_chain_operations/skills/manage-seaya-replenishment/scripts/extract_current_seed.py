"""Copy an explicit existing dashboard into a NEW local evidence directory.

Preserves raw bytes, every JSON field and all local assets. Does not run JS,
refresh source timestamps, download images, or apply any captured data.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from shared_platform.capability_runtime import checked_path

PREFIX = 'window.SUPPLY_CHAIN_DATA = '
WAREHOUSES = {'MY': 'MY8803', 'TH': 'TH8806', 'VN': 'VN8805', 'PH': 'PH8807'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'duplicate JSON field')
        result[key] = value
    return result


def parse(raw):
    text = raw.decode('utf-8')
    require(text.startswith(PREFIX), 'exact JSON dashboard assignment required')
    return json.loads(text[len(PREFIX):].strip().removesuffix(';'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def inventory(source):
    files = ['data.js']
    if checked_path(source, 'inbound-plan.js').exists():
        files.append('inbound-plan.js')
    assets = checked_path(source, 'assets')
    require(assets.is_dir(), 'local assets directory missing')
    for path in sorted(assets.rglob('*')):
        relative = path.relative_to(source).as_posix()
        checked = checked_path(source, relative)
        if checked.is_file():
            files.append(relative)
    return {name: sha(checked_path(source, name).read_bytes()) for name in files}


def extract(source, destination):
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    checked_path(source.parent, source.name)
    checked_path(destination.parent, destination.name)
    require(not destination.is_relative_to(source) and not source.is_relative_to(destination), 'source and output must be separate')
    require(not destination.exists(), 'output already exists; never overwrite evidence')
    before = inventory(source)
    raw = checked_path(source, 'data.js').read_bytes()
    require(sha(raw) == before['data.js'], 'source changed during extraction')
    seed = parse(raw)
    require(set(seed.get('countries', {})) == set(WAREHOUSES), 'four countries required')
    images = {}
    for region, warehouse in WAREHOUSES.items():
        require(seed['config'][region]['warehouse'] == warehouse, 'seed warehouse differs')
        rows = seed['countries'][region]
        identities = [r['sku'] for r in rows]
        require(len(set(identities)) == len(identities), 'duplicate country SKU')
        for row in rows:
            name = row.get('image')
            require(type(name) is str and name.startswith('assets/') and name in before, 'referenced local image missing')
            images[region + '/' + row['sku']] = {'path': name, 'sha256': before[name]}
    destination.mkdir(parents=False, exist_ok=False)
    for name, expected in before.items():
        body = checked_path(source, name).read_bytes()
        require(sha(body) == expected, 'source changed during copy')
        target = checked_path(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(body)
    with (destination/'seed.json').open('x', encoding='utf-8') as stream:
        json.dump(seed, stream, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    require(inventory(source) == before, 'source changed; partial copy has no COMPLETE manifest')
    require(inventory(destination) == before and json.loads((destination/'seed.json').read_text(encoding='utf-8')) == seed, 'copy readback differs')
    manifest = {'state': 'COPIED_NOT_REFRESHED', 'source': str(source), 'sourceFiles': before,
                'seedSha256': sha((destination/'seed.json').read_bytes()), 'images': images,
                'countries': {r: len(v) for r, v in seed['countries'].items()},
                'snapshotDate': seed.get('snapshotDate'), 'orderDemandCapturedAt': seed.get('orderDemandCapturedAt')}
    with (destination/'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = extract(args.source, args.destination)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'state': 'NOT_READY', 'detail': str(error)}))
        return 1
    print(json.dumps({k: v for k, v in result.items() if k not in ('images', 'sourceFiles')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
