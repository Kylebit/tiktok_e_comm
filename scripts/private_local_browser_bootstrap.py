"""Same Windows user local launcher; no provider, HTTP server or human gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import webbrowser

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared_platform.common_offer_authority_store import PrivateCommonAuthorityStore
from shared_platform.local_operator_session import initialize_private_browser_handoff, issue_private_browser_handoff
from shared_platform.private_final_decision_store import PrivateFinalDecisionStore


def main(argv=None):
    parser = argparse.ArgumentParser(description='Bootstrap the explicitly installed private browser channel.')
    parser.add_argument('--private-root', required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--reservation-id', required=True)
    parser.add_argument('--marketplace-plan-id')
    args = parser.parse_args(argv)
    # open() requires a real explicit SYNTHETIC_TEST_ONLY private marker. This
    # CLI does not migrate a normal release.db or install a production service.
    authority = PrivateCommonAuthorityStore.open(args.private_root)
    store = PrivateFinalDecisionStore(authority)
    initialize_private_browser_handoff(store.sessions)
    nonce = issue_private_browser_handoff(store.sessions, port=args.port, reservation_id=args.reservation_id,
                                          marketplace_plan_id=args.marketplace_plan_id)
    # Only an independent short-lived one-use carrier appears in the fragment.
    # Reusable capability/CSRF never appear in a URL, stdout, stderr or log.
    opened = webbrowser.open(f'http://127.0.0.1:{args.port}/local-operator-init#{nonce}', new=0)
    print(json.dumps({'browser_open_requested': bool(opened), 'execution_authority': False}))
    return 0 if opened else 1


if __name__ == '__main__':
    raise SystemExit(main())
