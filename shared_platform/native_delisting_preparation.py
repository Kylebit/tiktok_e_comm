"""Only exact, newly authorized delist POSTs may use the retained executor."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from shared_platform.operations_runtime import runtime_matches

DELIST_SCOPE = 'EXPLICIT_NEW_POST_DELIST_EXACT_SCOPE'


def _regular(path):
    path = Path(path)
    leaf = path.lstat()
    if (not stat.S_ISREG(leaf.st_mode) or leaf.st_nlink != 1
            or getattr(leaf, 'st_file_attributes', 0) & 0x400):
        raise ValueError('NATIVE_DELIST_FIXED_FILE_INVALID')
    if any(getattr(p.lstat(), 'st_file_attributes', 0) & 0x400 for p in path.parents):
        raise ValueError('NATIVE_DELIST_FIXED_ROOT_INVALID')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _directory(path):
    path=Path(path)
    node=path.lstat()
    if (not stat.S_ISDIR(node.st_mode) or getattr(node,'st_file_attributes',0)&0x400
            or any(getattr(parent.lstat(),'st_file_attributes',0)&0x400 for parent in path.parents)):
        raise ValueError('NATIVE_DELIST_FIXED_ROOT_INVALID')
    return path.resolve()


@dataclass(frozen=True)
class NativeDelistingServiceConfig:
    profile: object
    settings_path: Path
    settings_sha256: str
    script_sha256: str

    @classmethod
    def capture(cls, profile, configuration_root):
        if configuration_root is None or not Path(configuration_root).is_absolute():
            raise ValueError('NATIVE_DELIST_SERVICE_BINDING_REQUIRED')
        path = Path(configuration_root) / 'config/settings.json'
        if not path.is_file():
            raise ValueError('NATIVE_DELIST_SETTINGS_REQUIRED')
        script = Path(profile.root) / 'skills/delist-products-by-sku/scripts/delist_products_by_sku.py'
        return cls(profile, path, _regular(path), _regular(script))

    def check(self, engine, task, token, profile):
        if (profile is not self.profile or profile.environment != 'stable'
                or task['template'] != 'delisting' or task['version'] != engine.release
                or engine.release != {'code_version':profile.version, 'environment':profile.environment,
                                      'manifest_digest':profile.manifest_digest}
                or not profile.manifest_digest or not runtime_matches(profile)):
            raise ValueError('NATIVE_DELIST_RUNTIME_CHANGED')
        if (Path(os.environ.get('ORBIT_OPERATIONS_DATA_ROOT','')).resolve() != profile.data_root.resolve()
                or os.environ.get('ORBIT_OPERATIONS_ENV') != 'stable'):
            raise ValueError('NATIVE_DELIST_ORIGINAL_LEDGER_BINDING_REQUIRED')
        if (_regular(self.settings_path) != self.settings_sha256
                or _regular(Path(profile.root) / 'skills/delist-products-by-sku/scripts/delist_products_by_sku.py') != self.script_sha256):
            raise ValueError('NATIVE_DELIST_FIXED_BINDING_CHANGED')
        from core import config
        if (Path(config.settings_path()) != self.settings_path
                or config._cache_source != self.settings_path.resolve()
                or config.load_settings() != json.loads(self.settings_path.read_bytes())):
            raise ValueError('NATIVE_DELIST_SETTINGS_SOURCE_CHANGED')
        with engine.transaction() as db:
            row = engine._lease(db, task['task_id'], token)
            if json.loads(row['scope_json']) != task['scope']:
                raise ValueError('NATIVE_DELIST_TASK_SCOPE_CHANGED')
        if task['task_id'] not in engine.explicit_new_post_delisting_task_ids():
            raise ValueError('NATIVE_DELIST_NEW_POST_GRANT_REQUIRED')


class _Calls:
    def __init__(self, config, engine, task, token, profile):
        self.config, self.engine, self.task, self.token, self.profile = config, engine, task, token, profile
        config.check(engine, task, token, profile)
        settings = json.loads(config.settings_path.read_bytes())
        root = config.settings_path.parent.parent
        self.settings, self.files = settings, {}
        shops = task['scope']['shops']
        if any(s.startswith('tiktok:') for s in shops):
            if any(s not in {'tiktok:LH_PH','tiktok:LH_MY','tiktok:LH_TH','tiktok:LH_VN'} for s in shops if s.startswith('tiktok:')):
                raise ValueError('NATIVE_DELIST_OFFICIAL_TARGET_UNAVAILABLE')
            path = Path(settings.get('token_file','tiktok_tokens.json'))
            path = path if path.is_absolute() else root/path
            if not path.is_file(): raise ValueError('NATIVE_DELIST_TOKEN_REQUIRED')
            from core.auth import token_path
            if token_path() != path:
                raise ValueError('NATIVE_DELIST_TOKEN_SOURCE_CHANGED')
            self.files['tiktok'] = (path, _regular(path))
        if any(s.startswith('shopee:') for s in shops):
            cfg = settings.get('shopee') or {}
            if not cfg.get('enabled') or cfg.get('environment') != 'live' or not cfg.get('partner_id') or not cfg.get('partner_key'):
                raise ValueError('NATIVE_DELIST_SHOPEE_CONFIG_REQUIRED')
            path = Path(cfg.get('token_file',''))
            if not path.is_absolute():
                raise ValueError('NATIVE_DELIST_FIXED_TOKEN_PATH_REQUIRED')
            if not path.is_file(): raise ValueError('NATIVE_DELIST_TOKEN_REQUIRED')
            from modules.shopee.auth import token_path
            if token_path() != path:
                raise ValueError('NATIVE_DELIST_TOKEN_SOURCE_CHANGED')
            self.files['shopee'] = (path, _regular(path))
        if 'ozon:RU' in shops:
            cfg = settings.get('ozon') or {}
            if not cfg.get('client_id') or not cfg.get('api_key'):
                raise ValueError('NATIVE_DELIST_OZON_CONFIG_REQUIRED')
            from modules.ozon.config import ozon_credentials
            if ozon_credentials() != (str(cfg['client_id']).strip(),str(cfg['api_key']).strip()):
                raise ValueError('NATIVE_DELIST_OZON_ACCOUNT_CHANGED')
        self.data_sources={}
        if 'ozon:RU' in shops:
            from modules.ozon.config import ozon_data_dir
            raw=(settings.get('ozon') or {}).get('data_dir')
            if not isinstance(raw,str) or not raw.strip():
                raise ValueError('NATIVE_DELIST_OZON_CATALOG_REQUIRED')
            directory=Path(raw).expanduser()
            directory=directory if directory.is_absolute() else root/directory
            configured_directory=directory
            directory=_directory(configured_directory)
            if not directory.is_dir() or ozon_data_dir()!=directory:
                raise ValueError('NATIVE_DELIST_OZON_CATALOG_SOURCE_CHANGED')
            # Preserve the original resolver input; no legacy sibling fallback.
            files={}
            for name in ('all_products_attrs.json','tk_sku_map.json','migrated_offers.json'):
                path=directory/name
                files[name]=_regular(path) if path.exists() else None
            if not files['all_products_attrs.json']:
                raise ValueError('NATIVE_DELIST_OZON_CATALOG_REQUIRED')
            self.data_sources['ozon']={'directory':str(directory),'configured_directory':str(configured_directory),'files':files}
        if any(s.startswith('tiktok:') for s in shops):
            from core.db import db_path
            configured=Path(settings.get('database','data/shop.db'))
            configured=configured if configured.is_absolute() else root/configured
            if db_path().resolve()!=configured.resolve():
                raise ValueError('NATIVE_DELIST_CATALOG_SOURCE_CHANGED')
            self.data_sources['catalog']=str(configured.resolve())
        # Persist original non-secret file identities, not new business authority.
        evidence = {'settings_sha256':config.settings_sha256, 'script_sha256':config.script_sha256,
                    'tokens':{kind:{'path':str(p),'sha256':digest} for kind,(p,digest) in self.files.items()},
                    'data_sources':self.data_sources}
        with engine.transaction() as db:
            engine._lease(db,task['task_id'],token)
            rows = db.execute("SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type='native_delisting_source_bound' ORDER BY id",(task['task_id'],)).fetchall()
            if rows:
                if len(rows)!=1 or json.loads(rows[0]['detail_json'])!=evidence:
                    raise ValueError('NATIVE_DELIST_ORIGINAL_SOURCE_CHANGED')
            else:
                if task['current_step'] != 'identify' or task.get('checkpoint'):
                    raise ValueError('NATIVE_DELIST_ORIGINAL_SOURCE_REQUIRED')
                engine._event(db,task['task_id'],'native_delisting_source_bound',evidence)
        self.check()

    def check(self):
        self.config.check(self.engine,self.task,self.token,self.profile)
        if 'catalog' in self.data_sources:
            from core.db import db_path
            if str(db_path().resolve())!=self.data_sources['catalog']:
                raise ValueError('NATIVE_DELIST_CATALOG_SOURCE_CHANGED')
        if 'ozon:RU' in self.task['scope']['shops']:
            from modules.ozon.config import ozon_credentials, ozon_data_dir
            cfg=self.settings['ozon']
            if ozon_credentials()!=(str(cfg['client_id']).strip(),str(cfg['api_key']).strip()):
                raise ValueError('NATIVE_DELIST_OZON_ACCOUNT_CHANGED')
            bound=self.data_sources['ozon'];directory=Path(bound['directory'])
            if _directory(bound['configured_directory'])!=directory or ozon_data_dir()!=directory:
                raise ValueError('NATIVE_DELIST_OZON_CATALOG_SOURCE_CHANGED')
            for name,digest in bound['files'].items():
                path=directory/name
                if (_regular(path) if path.exists() else None)!=digest:
                    raise ValueError('NATIVE_DELIST_OZON_CATALOG_SOURCE_CHANGED')
        for kind,(path,digest) in self.files.items():
            if _regular(path)!=digest:
                raise ValueError('NATIVE_DELIST_TOKEN_SOURCE_CHANGED')
            doc=json.loads(path.read_bytes())
            if kind=='tiktok':
                expiry=doc.get('access_token_expire_in')
                if (not doc.get('access_token') or type(expiry) not in {int,float}
                        or expiry<=time.time()+300 or not doc.get('authorized_shops')):
                    raise ValueError('NATIVE_DELIST_CURRENT_TOKEN_REQUIRED')
            else:
                mapping=doc.get('sync_shop_ids') or {}
                for target in self.task['scope']['shops']:
                    if not target.startswith('shopee:'): continue
                    shop_id=mapping.get(target.split(':')[1]);entry=(doc.get('shops') or {}).get(str(shop_id)) or {}
                    expiry=entry.get('expire_at')
                    if (not shop_id or not entry.get('access_token') or type(expiry) not in {int,float}
                            or expiry<=time.time()+300 or entry.get('region')!=target.split(':')[1]):
                        raise ValueError('NATIVE_DELIST_CURRENT_TOKEN_REQUIRED')

    def tiktok_token(self):
        self.check()
        return json.loads(self.files['tiktok'][0].read_bytes())['access_token']

    def tiktok_request(self, method, path, token, query=None, body=None):
        self.check()
        from core.api_client import request
        return request(method,path,token,query=query,body=body,_retry_on_401=False,retry_read=False,
                       call_guard=self.check)

    def tiktok_detail(self, token, cipher, product_id):
        result=self.tiktok_request('GET','/product/202309/products/'+str(product_id),token,{'shop_cipher':cipher})
        if result.get('code')!=0: raise RuntimeError('official TikTok identity unavailable')
        return result.get('data') or {}

    def tiktok_shops(self, token):
        result=self.tiktok_request('GET','/authorization/202309/shops',token)
        if result.get('code')!=0:raise RuntimeError('official TikTok shops unavailable')
        data=result.get('data') or {}
        return data.get('shops',data.get('list',[]))


class _Skill:
    def __init__(self, original, calls): self.original,self.calls=original,calls
    def __getattr__(self,name):
        value=getattr(self.original,name)
        if name in {'_live_tiktok_rows','_live_shopee_rows','_live_verify','execute','readback'}:
            def call(*args,**kwargs):
                self.calls.check()
                return value(*args,**kwargs,call_guard=self.calls.check,tiktok_runtime=self.calls)
            return call
        if name in {'_local_tiktok_rows','_ozon_rows'}:
            def read(*args,**kwargs):
                self.calls.check()
                return value(*args,**kwargs)
            return read
        return value


def bindings(engine,profile,config):
    def run(current_engine,task,token,current_profile):
        if current_engine is not engine or current_profile is not profile:
            raise ValueError('NATIVE_DELIST_SERVICE_RUNTIME_CONFLICT')
        if type(config) is not NativeDelistingServiceConfig:
            engine.fail(task['task_id'],token,'NATIVE_DELIST_SERVICE_BINDING_REQUIRED')
            return
        from shared_platform.workbench_delisting_adapter import run, _module
        calls=_Calls(config,engine,task,token,profile)
        return run(engine,task,token,profile,skill=_Skill(_module(profile.root),calls))
    return run
