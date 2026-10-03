"""Offline R3 configuration diagnostics. No activation or document copying."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from shared_platform.publication_runtime_config import capture_startup_config,diagnose


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-root',type=Path,required=True)
    parser.add_argument('--policy-path',default='config/product_publication_autopilot_policy.json')
    parser.add_argument('--incident-registry-path',default='skills/publish-approved-product/references/incident-registry.json')
    args=parser.parse_args(argv)
    # An explicit offline invocation cannot accidentally inherit a runtime's
    # private ORBIT_R3_* paths or activate any configured service.
    config=capture_startup_config(root=args.config_root,environ={
        'ORBIT_R3_POLICY_PATH':args.policy_path,
        'ORBIT_R3_INCIDENT_REGISTRY_PATH':args.incident_registry_path})
    public,_=diagnose(config)
    result={'schema_version':'publication-config-audit/v1','mode':'OFFLINE_READ_ONLY',
        'execution_authority':False,'current_request_authority':'NOT_INFERRED',
        'incident_fix_provenance':'NOT_VERIFIED_BY_SCHEMA_VALIDATOR',
        'configuration':public,'source_files':{relative:hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()
            for relative in ('scripts/publication_config_audit.py','shared_platform/publication_runtime_config.py',
                'shared_platform/publication_autopilot.py')}}
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if public['status']=='CONFIGURED' else 2


if __name__=='__main__':
    raise SystemExit(main())
