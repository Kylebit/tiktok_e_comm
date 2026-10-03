"""Actual existing consumers; injected synthetic settings, no token/DB access."""
import sys,os,json,sqlite3,socket,importlib,hashlib
from pathlib import Path
root,old,new=map(Path,sys.argv[1:]);sys.path.insert(0,str(root))
def forbidden(*a,**k):raise AssertionError('unrelated I/O forbidden')
sqlite3.connect=forbidden;socket.socket.connect=forbidden
import core.config as c
c.CONFIG_PATH=old;c.FALLBACK_CONFIG_PATHS=[new];c._cache=None
import core.auth as tk
import modules.shopee.auth as sp
import core.db as db
assert c.settings_path()==old # CONFIG_PATH wins even with a selected fallback.
old_tk=tk.token_path();old_db=db.db_path()
c.CONFIG_PATH=old.with_name('missing-config.json')
assert c.settings_path()==new
assert c.get('token_file')=='tiktok.json' and db.db_path()!=old_db # Cached relative values now resolve against the new base: restart required.
c._cache=None
assert tk.token_path()==new.parent/'tiktok.json' and sp.token_path()==new.parent/'shopee.json'
assert db.db_path()==old_db and not old_db.exists()
# A fresh actual module import honors the process environment before loading.
os.environ['ORBIT_HIVE_SETTINGS']=str(new);importlib.reload(c);c.CONFIG_PATH=old.with_name('missing-config.json')
assert c.settings_path()==new and tk.token_path()==new.parent/'tiktok.json' and sp.token_path()==new.parent/'shopee.json' and db.db_path()==old_db
print(json.dumps({'source_root':str(root),'selected':str(c.settings_path()),'database_path_unchanged':str(old_db),'db_open_calls':0,'tokens_loaded':0,'source_sha256':{str(Path(m.__file__).relative_to(root)):hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (c,tk,sp,db)}}))
