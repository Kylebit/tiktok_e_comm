"""Validate a scoped producer receipt; --apply atomically updates one manifest.

Use the adopted repository Python runtime. No polling, session messages, database,
business action or timestamp-only refresh is performed by this command.
"""
from pathlib import Path
import argparse, json, sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from shared_platform.workbench_observation_updates import update_observation

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',required=True)
    p.add_argument('--update',required=True,help='absolute dated update envelope including receipt_ref')
    p.add_argument('--expected-sha256',required=True)
    p.add_argument('--apply',action='store_true',help='otherwise validate only')
    args=p.parse_args()
    update_path=Path(args.update)
    if not update_path.is_absolute(): p.error('update path must be absolute')
    update=json.loads(update_path.read_text(encoding='utf-8'))
    print(json.dumps(update_observation(args.manifest,update,expected_sha256=args.expected_sha256,apply=args.apply),ensure_ascii=False))

if __name__=='__main__':main()
