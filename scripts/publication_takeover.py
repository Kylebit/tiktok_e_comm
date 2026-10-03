"""Inspect an existing exact product using an explicit, commit-bound source."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))


def main(argv=None):
    # The portable JSON command must stay UTF-8 when redirected on Windows.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-commit', required=True)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--offer-id', required=True)
    parser.add_argument('--r2-candidate-binding', type=Path)
    parser.add_argument('--expected-candidate-sha256')
    args = parser.parse_args(argv)
    if bool(args.r2_candidate_binding) != bool(args.expected_candidate_sha256):
        parser.error('candidate binding and expected SHA256 must be supplied together')
    from shared_platform.publication_takeover import inspect_publication, source_binding, TakeoverError
    try:
        binding = source_binding(ROOT, args.expected_commit)
        result = inspect_publication(offer_id=args.offer_id, data_root=args.data_root)
        result['source_binding'] = binding
        if args.r2_candidate_binding:
            from shared_platform.publication_r2_candidate import inspect_candidate
            result['pending_r2_candidate'] = inspect_candidate(
                binding_path=args.r2_candidate_binding,
                expected_sha256=args.expected_candidate_sha256, takeover=result)
    except (TakeoverError, OSError, ValueError) as error:
        print(json.dumps({'status':'TAKEOVER_REJECTED','reason':str(error) if isinstance(error,TakeoverError) else type(error).__name__,'paid_requests':0,'business_writes':0}))
        return 2
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
