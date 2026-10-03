"""Bounded immutable COMPLETE generations for the existing supply dashboard."""
from __future__ import annotations
import base64
import hashlib
import html
import json
from pathlib import Path
import re

from core.static_files import resolve_static_path
from shared_platform.capability_runtime import checked_path,digest
from modules.sourcing.image_generation_checkpoint import atomic_json,business_lock,CheckpointRecoveryRequired

FILES=('data.js','inbound-plan.js')
LIMIT=32
HEX=re.compile(r'[0-9a-f]{64}')
# The dashboard's resource-failure marker does not alter the captured sequence.
SCRIPT=re.compile(r'<script(?: data-supply-asset)? src="([^"<>]+)"></script>')

def require(value):
    if not value:raise ValueError('captured snapshot unavailable')

def sha(raw):return hashlib.sha256(raw).hexdigest()

def read(path,limit=128*1024):
    raw=path.read_bytes();require(len(raw)<=limit)
    def pairs(items):
        result={}
        for k,v in items:require(k not in result);result[k]=v
        return result
    result=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:require(False));require(type(result) is dict)
    return result

def hashes(value):return type(value) is dict and set(value)==set(FILES) and all(type(v) is str and HEX.fullmatch(v) for v in value.values())

def capture_artifact_root(root,config):
    """Resolve the same explicitly selected capture root for producer and consumer.

    runtime_root is local data, never a substitute source checkout or an inferred
    installation. Existing two-field callers retain their project-relative scope.
    """
    require(type(config) is dict and set(config) in (
        {'artifact_root','output_root'}, {'runtime_root','artifact_root','output_root'}))
    code=checked_path(Path(root),'.')
    base=code
    if 'runtime_root' in config:
        value=config['runtime_root']
        require(type(value) is str and bool(value) and Path(value).is_absolute())
        base=checked_path(Path(value),'.')
        require(base.is_dir() and not base.is_relative_to(code) and not code.is_relative_to(base))
    require(type(config['artifact_root']) is str and type(config['output_root']) is str)
    relative=Path(config['artifact_root'])
    require(not relative.is_absolute()
            and len(relative.parts)>=2 and relative.parts[0]=='outputs')
    return base,checked_path(base,relative)

def deployment_capture(config):
    """Explicit native launcher binding. Never infer a root or ignore bad config.

    Missing/unreadable runtime data remains configured: the existing request
    boundary returns unavailable instead of startup silently choosing old data.
    """
    if 'supply_chain_capture' not in config:return None
    value=config['supply_chain_capture']
    require(type(value) is dict and set(value) in ({'runtime_root','artifact_root','output_root'},
            {'runtime_root','artifact_root','output_root','mode'}))
    if 'mode' in value:require(value['mode']=='AUDITED_MANUAL_SNAPSHOT')
    require(all(type(v) is str and bool(v) for v in value.values()))
    require(Path(value['runtime_root']).is_absolute())
    artifact=Path(value['artifact_root']);output=Path(value['output_root'])
    require(not artifact.is_absolute() and len(artifact.parts)>=2 and artifact.parts[0]=='outputs'
            and '..' not in artifact.parts and not output.is_absolute() and '..' not in output.parts
            and output.parts and output.parts[0] not in {'captured-stages','captured-apply.json'})
    return dict(value)

def index_read(artifact):
    value=read(checked_path(artifact,'captured-serving.json'))
    require(set(value)=={'schema','current','versions'} and value['schema']=='supply-chain-complete-index/v1')
    rows=value['versions'];require(type(rows) is list and 0<len(rows)<=LIMIT)
    require(all(type(r) is dict and set(r)=={'version','sha256'} and all(type(v) is str and HEX.fullmatch(v) for v in r.values()) for r in rows))
    require(len({r['version'] for r in rows})==len(rows) and value['current'] in {r['version'] for r in rows})
    return value

def publish_complete(artifact,output,version,expected):
    """Called under the existing writer lease, after output COMPLETE readback."""
    require(HEX.fullmatch(version) and hashes(expected))
    journal=read(checked_path(artifact,'captured-apply.json'))
    require(journal=={'state':'COMPLETE','stage_digest':version,'output_root':str(output),'output_sha256':expected})
    directory=checked_path(artifact,'captured-stages/'+version)
    stage=read(checked_path(directory,'stage.json'))
    require(stage['stage_digest']==version and stage['output_root']==str(output) and stage['output_sha256']==expected)
    for name in FILES:
        require(sha(checked_path(output,name).read_bytes())==expected[name] and sha(checked_path(directory,name).read_bytes())==expected[name])
    record={'schema':'supply-chain-complete-generation/v1','version':version,'output_root':str(output),'output_sha256':expected,'stage_sha256':sha(checked_path(directory,'stage.json').read_bytes())}
    marker=checked_path(directory,'complete.json')
    if marker.exists():require(read(marker)==record)
    else:atomic_json(marker,record)
    entry={'version':version,'sha256':sha(marker.read_bytes())}
    index_path=checked_path(artifact,'captured-serving.json')
    previous=index_read(artifact) if index_path.exists() else None
    if previous and previous['current']==version:
        require(next(r for r in previous['versions'] if r['version']==version)==entry);return
    rows=[r for r in previous['versions'] if r['version']!=version] if previous else []
    atomic_json(index_path,{'schema':'supply-chain-complete-index/v1','current':version,'versions':(rows+[entry])[-LIMIT:]})

class CompleteStore:
    def __init__(self,root,config):
        self.root,self.artifact=capture_artifact_root(root,config)
        self.output=checked_path(self.artifact,config['output_root'])
        require(self.output!=self.artifact and self.output.is_relative_to(self.artifact))

    def context(self):
        journal=read(checked_path(self.artifact,'captured-apply.json'))
        require(set(journal)=={'state','stage_digest','output_root','output_sha256'} and journal['state'] in ('APPLYING','COMPLETE'))
        version=journal['stage_digest'];require(type(version) is str and HEX.fullmatch(version) and journal['output_root']==str(self.output) and hashes(journal['output_sha256']))
        stage=read(checked_path(self.artifact,'captured-stages/'+version+'/stage.json'))
        require(stage['stage_digest']==version and stage['output_root']==str(self.output) and stage['output_sha256']==journal['output_sha256'])
        index=index_read(self.artifact)
        if journal['state']=='COMPLETE':require(index['current']==version)
        return journal,index

    def pair(self,version,index):
        require(type(version) is str and HEX.fullmatch(version))
        entry=next((r for r in index['versions'] if r['version']==version),None);require(entry is not None)
        directory=checked_path(self.artifact,'captured-stages/'+version)
        marker=checked_path(directory,'complete.json');require(sha(marker.read_bytes())==entry['sha256'])
        record=read(marker);require(set(record)=={'schema','version','output_root','output_sha256','stage_sha256'} and record['schema']=='supply-chain-complete-generation/v1' and record['version']==version and record['output_root']==str(self.output) and hashes(record['output_sha256']))
        stage_path=checked_path(directory,'stage.json');require(sha(stage_path.read_bytes())==record['stage_sha256'])
        stage=read(stage_path);require(stage['stage_digest']==version and stage['output_root']==str(self.output) and stage['output_sha256']==record['output_sha256'])
        bodies={}
        for name in FILES:
            body=checked_path(directory,name).read_bytes();require(len(body)<=32*1024*1024 and sha(body)==record['output_sha256'][name]);bodies[name]=body
        return bodies,record['output_sha256']

    def page(self,supply,name):
        scope=digest({'root':str(self.root).casefold(),'artifact':str(self.artifact).casefold()})
        with business_lock(self.artifact,scope,timeout=0):
            journal,index=self.context();require(journal['state']=='COMPLETE')
            version=index['current'];bodies,expected=self.pair(version,index)
            require(all(sha(checked_path(self.output,f).read_bytes())==expected[f] for f in FILES))
            text=(supply/name).read_text(encoding='utf-8');scripts=[]
            for match in SCRIPT.finditer(text):
                src=match.group(1);filename=src.removeprefix('./').split('?')[0]
                require(filename in {*FILES,'transport-history.js','inbound-timeline.js','app.js','inbound-batches.js'})
                if filename in FILES:
                    src='/supply-chain/captured/'+version+'/'+filename
                    integrity='sha256-'+base64.b64encode(bytes.fromhex(expected[filename])).decode()
                else:integrity=''
                scripts.append({'src':src,'integrity':integrity})
            require(sum('/'+f in item['src'] for item in scripts for f in FILES)==2)
            payload=html.escape(json.dumps(scripts,separators=(',',':')),quote=True)
            bootstrap=f'<script src="./captured-bootstrap.js" data-captured-version="{version}" data-captured-scripts="{payload}"></script>'
            text,count=SCRIPT.subn('',text);require(count==len(scripts) and count>=4)
            return text.replace('</body>',bootstrap+'\n</body>').encode('utf-8')

    def response(self,suffix,supply):
        if suffix in ('index.html','inbound-batches.html'):return 200,self.page(supply,suffix),'text/html; charset=utf-8'
        if suffix in FILES:
            self.context()
            return 409,b'An entire page must select one complete generation.','text/plain; charset=utf-8'
        if suffix.startswith('captured/'):
            match=re.fullmatch(r'captured/([0-9a-f]{64})/(data\.js|inbound-plan\.js)',suffix)
            if not match:return 404,b'Unknown snapshot resource.','text/plain; charset=utf-8'
            journal,index=self.context();version,name=match.groups()
            if version not in {r['version'] for r in index['versions']}:return 404,b'Unknown snapshot version.','text/plain; charset=utf-8'
            bodies,expected=self.pair(version,index);return 200,bodies[name],'application/javascript; charset=utf-8'
        if suffix.startswith('assets/'):
            asset=resolve_static_path(self.output/'assets',suffix[len('assets/'):])
            if asset is None or not asset.is_file() or asset.suffix.lower() not in {'.png','.jpg','.jpeg','.webp','.gif','.svg'}:return 404,b'Unknown asset.','text/plain; charset=utf-8'
            import mimetypes
            return 200,asset.read_bytes(),mimetypes.guess_type(str(asset))[0] or 'application/octet-stream'
        return None

def serve_request(handler,config,root,supply,suffix):
    manual=type(config) is dict and config.get('mode')=='AUDITED_MANUAL_SNAPSHOT'
    try:
        if manual:
            from .audited_snapshot import AuditedSnapshotStore
            response=AuditedSnapshotStore(root,config).response(suffix,supply)
        else:response=CompleteStore(root,config).response(suffix,supply)
    except (ValueError,KeyError,TypeError,OSError,CheckpointRecoveryRequired) as error:
        if manual and str(error).startswith('AUDITED_CONSUMER_CHANGED:'):
            message='历史快照的显示代码已改变，请重新导入并核验页面兼容；未用旧库存替代。'
        else:message='请稍后重新加载；未显示不完整数据。'
        response=(503,('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>供应链数据暂不可用</title><body><h1>供应链数据正在刷新或暂不可用</h1><p>'+message+'</p></body></html>').encode(),'text/html; charset=utf-8')
    if response is None:return False
    code,body,ctype=response;handler.send_response(code);handler.send_header('Content-Type',ctype);handler.send_header('Content-Length',str(len(body)));handler.send_header('Cache-Control','no-store');handler.send_header('X-Content-Type-Options','nosniff')
    if ctype.startswith('text/html'):handler.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
    handler.end_headers();handler.wfile.write(body);return True
