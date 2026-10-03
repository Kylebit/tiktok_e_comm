"""Explicit private-only same-user bootstrap and one final decision CLI."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared_platform.common_offer_authority_store import CommonAuthorityBlocked, PrivateCommonAuthorityStore
from shared_platform.private_final_decision_store import PrivateFinalDecisionStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", required=True)
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("init-owner")
    bootstrap = subs.add_parser("bootstrap")
    bootstrap.add_argument("--handoff-name", required=True)
    prepare = subs.add_parser("prepare")
    prepare.add_argument("--handoff-name", required=True)
    prepare.add_argument("--reservation-id", required=True)
    decide = subs.add_parser("decide")
    decide.add_argument("--handoff-name", required=True)
    decide.add_argument("--nonce", required=True)
    decide.add_argument("--review-digest", required=True)
    args = parser.parse_args(argv)
    try:
        authority = PrivateCommonAuthorityStore.open(args.private_root)
        store = PrivateFinalDecisionStore(authority)
        if args.command == "init-owner":
            store.migrate()
            result = store.sessions.initialize_owner()
        elif args.command == "bootstrap":
            result = store.sessions.bootstrap_to_private_file(filename=args.handoff_name)
        else:
            grant = store.sessions.grant_from_private_file(filename=args.handoff_name)
            if args.command == "prepare":
                result = store.prepare(grant, reservation_id=args.reservation_id)
            else:
                result = store.decide(grant, nonce=args.nonce, review_digest=args.review_digest)
        print(json.dumps({**result, "execution_authority": False, "external_writes_performed": []}, ensure_ascii=False))
        return 0
    except (CommonAuthorityBlocked, OSError, ValueError, TypeError) as error:
        code = str(error) if isinstance(error, CommonAuthorityBlocked) else "PRIVATE_LOCAL_FINAL_FAILED"
        print(json.dumps({"error": code, "execution_authority": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
