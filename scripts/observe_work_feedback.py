"""Single coordinator writer for explicit readonly engineering/supply receipts."""
from pathlib import Path
import argparse
import json
import os
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from shared_platform.workbench_source_feedback import load_profile, local_file, tick


def stop_requested(path, manifest):
    if path is None or not path.exists():
        return False
    raw = local_file(path).read_bytes()
    if len(raw) > 1024:
        raise ValueError('feedback stop marker exceeds bound')
    return json.loads(raw) == {'schema': 'orbit-feedback-stop/v1',
                               'manifest': str(manifest.resolve())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--health-file', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--stop-file', type=Path)
    args = parser.parse_args()
    sources = load_profile(args.profile)
    for output in (args.manifest, args.state_dir, args.health_file):
        local_file(output)
        if output.resolve().is_relative_to(ROOT):
            parser.error('observer output must be outside the source repository')
        for source in sources.values():
            if output.resolve() == source.resolve() or source.resolve().is_relative_to(output.resolve()):
                parser.error('observer output overlaps a producer receipt')
    if args.stop_file is not None:
        local_file(args.stop_file)
        if args.stop_file.resolve() != (args.state_dir / 'STOP.json').resolve():
            parser.error('feedback stop must be the selected state directory STOP.json')
    args.state_dir.mkdir(parents=True, exist_ok=True)
    lock = args.state_dir / 'observer.lock'
    marker = json.dumps({'pid': os.getpid(), 'manifest': str(args.manifest.resolve())})
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(marker)
    try:
        while True:
            if stop_requested(args.stop_file, args.manifest):
                break
            result = tick(args.manifest, args.health_file, args.state_dir, sources)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if args.once:
                break
            for _ in range(20):
                if stop_requested(args.stop_file, args.manifest):
                    break
                time.sleep(.25)
    finally:
        if lock.read_text(encoding='utf-8') == marker:
            lock.unlink()


if __name__ == '__main__':
    main()
