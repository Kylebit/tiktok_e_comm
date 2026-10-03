"""Standalone preview guard; only explicit synthetic files and local serving."""
import hashlib,json,os,sys,subprocess
from pathlib import Path
from urllib.parse import urlsplit,unquote

def install(root,out):
    root,out=Path(root).resolve(),Path(out).resolve()
    if out.is_relative_to(root):raise ValueError('Preview output must be outside the source tree')
    fixture=out/'inputs'
    def blocked(event):
        with (out/'blocked-io.jsonl').open('a') as stream:stream.write(json.dumps({'event':event})+'\n')
        raise RuntimeError('U05_FIXTURE_GUARD '+event)
    def guard(event,args):
        if event.startswith('socket.') and event not in {'socket.__new__','socket.gethostname'}:
            if not (event=='socket.bind' and args[1]==('127.0.0.1',0)):blocked(event)
        if event=='subprocess.Popen':
            expected=['git','-c','safe.directory='+root.as_posix(),'-C',str(root),'rev-parse','HEAD']
            if not (os.environ.get('ORBIT_FULL_HANDLER')=='1' and args[0] in {None,'git'} and (args[1]==expected or args[1]==subprocess.list2cmdline(expected))):blocked(event)
        if event in {'os.system','winreg.OpenKey','winreg.QueryValue'}:blocked(event)
        if event=='sqlite3.connect':
            value=os.fsdecode(args[0]);value=unquote(urlsplit(value).path) if value.startswith('file:') else value
            if value.startswith('/') and len(value)>2 and value[2]==':':value=value[1:]
            if not Path(value).resolve().is_relative_to(fixture):blocked('database_outside_fixture')
        if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
            name=os.fsdecode(args[0]);path=Path(name).resolve()
            if path.name.lower() in {'settings.json','.env','tiktok_tokens.json'} or path.name.lower().endswith('.local.json'):blocked('private_configuration')
            if path.suffix.lower() in {'.db','.sqlite','.sqlite3'} and not path.is_relative_to(fixture):blocked('database_file_outside_fixture')
            mode,flags=args[1:3]
            writing=isinstance(mode,str) and any(c in mode for c in 'wax+') or isinstance(flags,int) and flags&(os.O_WRONLY|os.O_RDWR)
            if writing and not path.is_relative_to(out):blocked('write_outside_preview')
    sys.addaudithook(guard)
    (out/'guard-installed.json').write_text(json.dumps({'root':str(root),'out':str(out),'fixture':str(fixture),'guard_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}))
