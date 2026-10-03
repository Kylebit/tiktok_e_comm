"""Inspect by default; restore only complete original, digest-bound local bytes."""
from pathlib import Path
import argparse
import json
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared_platform.publication_autopilot import CANDIDATE_SCHEMA, load_release_candidate
from shared_platform.immutable_approval_files import inspect_approval_recovery, recover_approval_file


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("candidate", "approval"), required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    parser.add_argument("--offer-id", required=True)
    parser.add_argument("--candidate-digest", required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--expected-evidence-sha256")
    parser.add_argument("--expected-document-digest")
    parser.add_argument("--expected-damaged-sha256")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    candidate = {"schema_version": CANDIDATE_SCHEMA, "offer_id": args.offer_id,
                 "candidate_digest": args.candidate_digest}
    kwargs = dict(kind=args.kind, candidate=candidate, reports_root=args.reports_root,
                  evidence_path=args.evidence, evidence_root=args.evidence_root,
                  expected_evidence_sha256=args.expected_evidence_sha256,
                  expected_document_digest=args.expected_document_digest)
    try:
        if args.kind == "approval":
            kwargs["candidate"] = load_release_candidate(args.offer_id, args.candidate_digest, reports_root=args.reports_root)
        result = recover_approval_file(expected_damaged_sha256=args.expected_damaged_sha256, **kwargs) if args.apply else inspect_approval_recovery(**kwargs)
    except ValueError as error:
        print(json.dumps({"status": "BLOCKED", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
