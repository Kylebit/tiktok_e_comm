"""Explicit local credential relocation; no live CLI, discovery or network.

prepare reads file metadata only. execute needs a separately approved plan and
a caller's fresh stopped-writer evidence. This helper cannot stop old writers.
"""
from __future__ import annotations
import importlib.util,json,os,re,uuid
from pathlib import Path

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('u04_storage_primitives',HERE/'session_recovery.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
require=g.require

def sources():
    import hashlib
    files=[Path(__file__).resolve(),HERE/'session_recovery.py',g.ROOT/'core/auth.py',g.ROOT/'core/config.py',g.ROOT/'core/db.py',g.ROOT/'modules/shopee/auth.py',g.ROOT/'modules/shopee/config.py']
    return {str(p.relative_to(g.ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}

def plain(path):
    import stat
    path=Path(path).absolute()
    require(path.is_absolute() and '..' not in path.parts,'PATH_INVALID')
    for p in (path,*path.parents):
        if p.exists():
            s=p.lstat();require(not stat.S_ISLNK(s.st_mode) and not getattr(s,'st_file_attributes',0)&0x400,'REPARSE_FORBIDDEN')
    return path

def prepare(*,settings,tiktok,shopee,private,run_id,writers,database_target=None):
    require(re.fullmatch('[A-Za-z0-9_-]{1,48}',run_id or '') and type(writers) is list and writers and all(type(x) is str and x for x in writers) and len(set(writers))==len(writers),'PLAN_INVALID')
    paths={k:plain(v) for k,v in {'settings':settings,'tiktok':tiktok,'shopee':shopee}.items()};private=plain(private)
    require(not any((p/'.git').exists() for p in (private,*private.parents)),'PRIVATE_INSIDE_GIT')
    require(len(set(paths.values()))==3 and all(not p.is_relative_to(private) for p in paths.values()),'PATH_OVERLAP')
    return {'schema':'u04-private-storage/v1','sources':sources(),'paths':{k:str(p) for k,p in paths.items()},'versions':{k:g.version(p) for k,p in paths.items()},'private':str(private),'run_id':run_id,'writers':writers,'mode':'prepare','config_keys':['token_file','shopee.token_file','database'],'database_target':str(plain(database_target)) if database_target is not None else None,'activation':'NOT_ACTIVATED'}

def _atomic_private(destination,raw,private):
    """All secret candidates originate in the already protected directory."""
    candidate=private/('candidate-'+uuid.uuid4().hex)
    with g._reserve(candidate) as handle:
        if os.name=='nt':handle.update(destination=destination,rename_info=g._rename_info(destination))
        g._write_reserved(handle,raw);g._replace_reserved(handle,destination)
        g.protected(destination);handle['stream'].seek(0);require(handle['stream'].read()==raw,'READBACK_FAILED')

def execute(plan,authorization,stopped_writers):
    """Future caller must independently verify all enumerated writers stopped.

    No in-package production attestation or execution command is supplied.
    """
    require(type(authorization) is dict and authorization.get('storage_write') is True and authorization.get('plan_digest')==g.plan_digest(plan) and type(authorization.get('authority')) is str and bool(authorization['authority']),'AUTHORIZATION_REQUIRED')
    require(plan.get('schema')=='u04-private-storage/v1' and plan.get('sources')==sources() and plan.get('mode')=='prepare','SOURCE_OR_PLAN_CHANGED')
    require(type(plan.get('database_target')) is str and Path(plan['database_target']).is_absolute(),'DATABASE_TARGET_UNVERIFIED')
    private=plain(plan['private']);paths={k:plain(v) for k,v in plan['paths'].items()}
    require(set(paths)=={'settings','tiktok','shopee'} and all(not p.is_relative_to(private) for p in paths.values()),'PATH_INVALID')
    require(re.fullmatch('[A-Za-z0-9_-]{1,48}',plan.get('run_id','')),'PLAN_INVALID')
    g.protected(private)
    require(private.is_dir() and not any((p/'.git').exists() for p in (private,*private.parents)),'PRIVATE_INSIDE_GIT')
    def quiet():
        receipt=stopped_writers(plan)
        require(type(receipt) is dict and receipt.get('plan_digest')==g.plan_digest(plan) and receipt.get('writers')==plan['writers'] and receipt.get('all_stopped') is True and bool(receipt.get('evidence')),'WRITERS_NOT_QUIESCENT')
    quiet()
    journal=private/('migration-'+plan['run_id']+'.json')
    # Every plan writes the same three final filenames. Exclude the entire
    # destination, including after a crash; changing run_id cannot bypass it.
    with g.locked(private/'migration-destination'):
        if journal.exists():
            g.protected(journal);state=g._read_json(journal.read_bytes())
            require(state.get('plan_digest')==g.plan_digest(plan) and state.get('state') in ('READY','COMPLETE'),'RECONCILIATION_REQUIRED')
            originals={}
            for k in paths:
                backup=private/(plan['run_id']+'-'+k+'.backup');g.protected(backup);originals[k]=backup.read_bytes()
        else:
            require(all(g.version(p)==plan['versions'][k] for k,p in paths.items()),'VERSION_CHANGED')
            require(not any((private/(k+'.json')).exists() for k in paths),'DESTINATION_EXISTS')
            originals={k:p.read_bytes() for k,p in paths.items()}
            for raw in originals.values():g._read_json(raw)
            _atomic_private(journal,json.dumps({'plan_digest':g.plan_digest(plan),'state':'BUILDING'}).encode(),private)
            for k,raw in originals.items():
                quiet();backup=private/(plan['run_id']+'-'+k+'.backup');g._write_new(backup,raw);require(backup.read_bytes()==raw,'BACKUP_UNVERIFIED')
            state={'plan_digest':g.plan_digest(plan),'state':'READY'}
            _atomic_private(journal,json.dumps(state).encode(),private)
        config=g._read_json(originals['settings']);require(type(config.get('shopee')) is dict,'CONFIG_INVALID')
        config['token_file']=str(private/'tiktok.json');config['shopee']['token_file']=str(private/'shopee.json')
        database=Path(config.get('database','data/shop.db'))
        base=paths['settings'].parent.parent if paths['settings'].parent.name=='config' else paths['settings'].parent
        config['database']=str(database if database.is_absolute() else base/database)
        require(Path(config['database'])==Path(plan['database_target']),'DATABASE_TARGET_CHANGED')
        updated=json.dumps(config,ensure_ascii=False,indent=2).encode()
        def unchanged():
            quiet()
            for k in paths:
                require(g.version(paths[k])==plan['versions'][k] and paths[k].read_bytes()==originals[k],'SOURCE_CHANGED')
        unchanged()
        for k in ('tiktok','shopee'):
            destination=private/(k+'.json')
            if destination.exists():g.protected(destination);require(destination.read_bytes()==originals[k],'DESTINATION_CHANGED')
            else:
                require(state['state']!='COMPLETE','COMPLETE_CHANGED');unchanged();_atomic_private(destination,originals[k],private)
        unchanged()
        target_config=private/'settings.json'
        if target_config.exists():g.protected(target_config);require(target_config.read_bytes()==updated,'CONFIG_CHANGED')
        else:
            require(state['state']!='COMPLETE','COMPLETE_CHANGED');_atomic_private(target_config,updated,private)
        g.protected(target_config);require(target_config.read_bytes()==updated,'READBACK_FAILED')
        reused=state['state']=='COMPLETE';state['state']='COMPLETE';_atomic_private(journal,json.dumps(state).encode(),private)
        return {'state':'REUSED' if reused else 'COMPLETE','plan_digest':g.plan_digest(plan),'network_requests':0,'original_files_preserved':True,'activation':'NOT_ACTIVATED','settings_target':str(target_config)}

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Inspect a public migration plan only; no execution CLI.')
    parser.add_argument('--dry-run',type=Path,required=True);args=parser.parse_args()
    try:
        plan=g._read_json(args.dry_run.read_bytes());require(plan.get('schema')=='u04-private-storage/v1','PLAN_INVALID')
        print(json.dumps({'state':'PREPARATION_ONLY','plan_digest':g.plan_digest(plan),'source_match':plan.get('sources')==sources(),'database_target_verified':plan.get('database_target') is not None,'credential_reads':0,'settings_reads':0,'writes':0,'network_requests':0,'activation':'NOT_ACTIVATED'}))
    except Exception:print('{"state":"DRY_RUN_REJECTED"}');raise SystemExit(1)
