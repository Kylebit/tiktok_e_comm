"""Explicit local schema maintenance and current-time native task adoption.

No listener, provider transport, paid request, approval or business write exists
in this entrypoint. Deployment pins select existing source/store paths; source
verification must pass before the local maintenance/adoption mutation begins.
"""
from __future__ import annotations
import argparse,json,os,sqlite3,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def initialize_local_schemas(engine,profile,store):
    """Explicit maintenance preserves existing native preparation and history."""
    from shared_platform import native_common_technical_execution as technical
    from shared_platform import native_sole_final_decision as final
    from shared_platform import manual_common_creation as manual
    from shared_platform import agent_prepared_publication_adoption as adoption
    with store._connect() as db:
        store._ensure_schema(db)
    with store._connect_readonly() as db:
        installed=technical._schema_installed(db)
    if not installed:
        with sqlite3.connect(store.path) as db:
            db.execute('PRAGMA foreign_keys=OFF')
            db.executescript((ROOT/'shared_platform/migrations/native_common_technical_runs_v1.sql').read_text(encoding='utf-8'))
            if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('ADOPTION_MIGRATION_FOREIGN_KEY_FAILED')
    with sqlite3.connect(store.path) as db:
        db.row_factory=sqlite3.Row
        present=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_sole_final_nonces'").fetchone()
        if not present:
            for statement in final.TABLES+final.TRIGGERS:db.execute(statement)
        final.check_schema(db)
    manual.initialize_schema(store.path)
    from shared_platform.native_final_mutation_budget import SCHEMA_SQL
    with sqlite3.connect(store.path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_final_budget_scopes'").fetchone():
            db.executescript(SCHEMA_SQL)
    profile.data_root.mkdir(parents=True,exist_ok=True)
    adoption.initialize_schema(engine,profile)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--deployment',type=Path,required=True)
    p.add_argument('--offer-id',required=True)
    p.add_argument('--source-key',required=True)
    p.add_argument('--initialize-local-schema',action='store_true',required=True)
    p.add_argument('--receipt',type=Path,required=True)
    args=p.parse_args(argv);config=json.loads(args.deployment.read_text(encoding='utf-8-sig'))
    from scripts.stable_runtime_bootstrap import prebind_release_store,prebind_workbench_store,configure_stable_runtime
    from shared_platform.agent_prepared_common_service import SCOPE
    if config.get('native_service_scope')!=SCOPE or Path(config['code_root']).resolve()!=ROOT:
        raise ValueError('ADOPTION_EXACT_DEPLOYMENT_REQUIRED')
    if subprocess.check_output(['git','status','--porcelain=v1','-uall'],cwd=ROOT,text=True).strip():
        raise ValueError('ADOPTION_CLEAN_SOURCE_REQUIRED')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if config.get('code_version')!=head:raise ValueError('ADOPTION_DEPLOYMENT_HEAD_CHANGED')
    # These imports occur only after the real existing ledger path is pinned.
    release=prebind_release_store(config);workbench=prebind_workbench_store(config)
    os.environ['ORBIT_OPERATIONS_ENV']='stable'
    os.environ['ORBIT_OPERATIONS_DATA_ROOT']=str(Path(config['operations_data_root']).resolve())
    os.environ['ORBIT_HIVE_SETTINGS']=str(Path(config['settings']).resolve())
    os.environ['ORBIT_R3_CONFIG_ROOT']=str(config.get('publication_runtime_config_root') or config['r3_config_root'])
    os.environ['TIKTOK_ECOMM_HOME']=str(config['r3_config_root'])
    from shared_platform.operations_launch import bind_publication_policy_override,bind_review_paths
    bind_publication_policy_override(config);bind_review_paths(config)
    configure_stable_runtime(config,expected_workbench_store_path=workbench,expected_release_store_path=release)
    from shared_platform.operations_runtime import RuntimeProfile
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform.release_store import default_release_store
    from shared_platform import agent_prepared_publication_adoption as adoption
    from shared_platform.native_windows_actor import NativeActorServiceConfig,bootstrap_service,NativeWindowsActorProfileReader
    profile=RuntimeProfile.capture(ROOT)
    if profile.manifest_digest!=config['manifest_digest']:raise ValueError('ADOPTION_MANIFEST_CHANGED')
    engine=WorkbenchEngine(profile.data_root/'tasks.db',dict(code_version=profile.version,environment='stable',manifest_digest=profile.manifest_digest))
    snap=json.loads((ROOT/'reports/product-preparation'/args.offer_id/'round1-approved-snapshot.json').read_text(encoding='utf-8'))
    targets=snap['canonical_targets']
    # Incomplete R2, changed source, prior business write or paid UNKNOWN blocks
    # before schemas or a replacement task are created.
    # A genuine R1-only ledger has no business tables yet. This pre-maintenance
    # check may report that exact absence, never a verified empty history.
    # adopt() below uses the strict default and re-reads the installed ledger.
    adoption._verify_sources(profile,args.offer_id,targets,allow_uninitialized_release_schema=True)
    initialize_local_schemas(engine,profile,default_release_store())
    actor=bootstrap_service(NativeActorServiceConfig(profile.data_root/'native-final-owner','Kyle'))
    result=adoption.adopt(engine,profile,offer_id=args.offer_id,targets=targets,source_key=args.source_key,
       actor_reader=NativeWindowsActorProfileReader(actor,actor.grant_same_user()))
    if not args.receipt.is_absolute():raise ValueError('ADOPTION_ABSOLUTE_RECEIPT_REQUIRED')
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    encoded=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.receipt.exists() and args.receipt.read_text(encoding='utf-8')!=encoded:raise ValueError('ADOPTION_RECEIPT_EXISTS')
    args.receipt.write_text(encoded,encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    return 0
if __name__=='__main__':raise SystemExit(main())
