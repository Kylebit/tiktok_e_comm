"""Prepare or install exactly one local candidate review; never publish."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from shared_platform.publication_takeover import inspect_publication, source_binding
from shared_platform.publication_r2_candidate import inspect_candidate
from shared_platform.publication_r2_review import register_candidate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('runtime-root','source-root','offer-id','binding-path','expected-sha256','expected-commit'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--apply',action='store_true',help='Install one product projection and pending review locally')
    args=parser.parse_args()
    code=source_binding(ROOT,args.expected_commit)
    takeover=inspect_publication(offer_id=args.offer_id,data_root=args.source_root)
    candidate=inspect_candidate(binding_path=args.binding_path,expected_sha256=args.expected_sha256,takeover=takeover)
    result={'status':'DRY_RUN','offer_id':args.offer_id,'seller_sku':takeover['seller_sku'],
        'targets':takeover['targets'],'image_count':candidate['candidate_asset_count'],
        'binding_sha256':args.expected_sha256,'runtime_root':args.runtime_root,
        'publication_authorized':False,'source_binding':code}
    if args.apply:
        result['review']=register_candidate(runtime_root=args.runtime_root,source_root=args.source_root,
            offer_id=args.offer_id,binding_path=args.binding_path,expected_sha256=args.expected_sha256)
        result['status']='REGISTERED_LOCAL_PENDING_REVIEW'
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()
