"""Request-scoped resources versus full admission; private real-file fixtures only."""
import os
import shutil
from pathlib import Path
import pytest
from test_audited_supply_snapshot import snapshot,admit,module,git,write,snapshot_http

def two_assets(snapshot):
    m=module();code,source,_,_,config,raws,png=snapshot
    data=m.js_object(raws['data'],'SUPPLY_CHAIN_DATA')
    data['countries']['MY'][0]['image']='assets/sku-0002.png'
    raws={**raws,'data':b'window.SUPPLY_CHAIN_DATA = '+m.encoded(data)+b';\n'}
    write(source/m.SOURCE_PATHS['data'],raws['data'])
    write(source/'domains/supply_chain_operations/dashboard/assets/sku-0002.png',png)
    git(source,'add','.')
    git(source,'-c','user.name=Synthetic test','-c','user.email=fixture@example.invalid','commit','-m','Synthetic manual source')
    return code,source,git(source,'rev-parse','HEAD'),{key:m.sha(raw) for key,raw in raws.items()},config,raws,png

def response(store,version,name):
    return store.response('audited/'+version+'/'+name,store.supply)

def same_time_damage(path):
    observed=path.stat();raw=path.read_bytes()
    path.write_bytes(bytes([raw[0]^1])+raw[1:])
    os.utime(path,ns=(observed.st_atime_ns,observed.st_mtime_ns))
    assert path.stat().st_size==observed.st_size and path.stat().st_mtime_ns==observed.st_mtime_ns

def test_unrequested_image_damage_is_local_to_resource_but_full_admission_rejects(snapshot):
    m=module();result,store=admit(two_assets(snapshot));version=result['version']
    _,manifest,directory=store.current()
    assert set(manifest['asset_sha256'])=={'assets/sku-0001.png','assets/sku-0002.png'}
    assert response(store,version,'assets/sku-0002.png')[1]==snapshot[-1]
    same_time_damage(directory/'assets/sku-0001.png')
    healthy=response(store,version,'assets/sku-0002.png')
    assert healthy[0]==200 and healthy[1]==snapshot[-1]
    with pytest.raises(ValueError):response(store,version,'assets/sku-0001.png')
    with pytest.raises(ValueError):store.current()
    with pytest.raises(ValueError):store.read_version(version,store.index()['versions'][0]['sha256'])
    with pytest.raises(ValueError):store.response('index.html',store.supply)

@pytest.mark.parametrize('name',['assets/sku-0001.png','data.js','inbound-plan.js'])
def test_resource_freshly_reads_complete_binding_and_only_selected_image(snapshot,monkeypatch,name):
    m=module();result,store=admit(two_assets(snapshot));version=result['version'];directory=store.output/version
    original=m._bounded_file_bytes;seen=[]
    def tracked(path,*args):seen.append(Path(path).resolve());return original(path,*args)
    monkeypatch.setattr(m,'_bounded_file_bytes',tracked)
    assert response(store,version,name)[0]==200
    mandatory={store.artifact/'audited-serving.json',directory/'audited.json'}
    mandatory|={store.supply/consumer for consumer in m.CONSUMERS}
    mandatory|={directory/'inputs'/(key+'.source') for key in m.SOURCE_PATHS}
    mandatory|={directory/file for file in m.FILES}
    if name.startswith('assets/'):mandatory.add(directory/name)
    assert {path.resolve() for path in mandatory}==set(seen)
    assert directory/'assets/sku-0002.png' not in seen

@pytest.mark.parametrize('changed',['index','manifest','pair_data','pair_inbound',*["input_"+key for key in module().SOURCE_PATHS],*["consumer_"+name for name in module().CONSUMERS]])
def test_healthy_image_still_rejects_every_fresh_binding_damage(snapshot,changed):
    m=module();result,store=admit(two_assets(snapshot));version=result['version'];directory=store.output/version
    assert response(store,version,'assets/sku-0002.png')[0]==200
    if changed=='index':path=store.artifact/'audited-serving.json'
    elif changed=='manifest':path=directory/'audited.json'
    elif changed=='pair_data':path=directory/'data.js'
    elif changed=='pair_inbound':path=directory/'inbound-plan.js'
    elif changed.startswith('input_'):path=directory/'inputs'/(changed.removeprefix('input_')+'.source')
    else:path=store.supply/changed.removeprefix('consumer_')
    same_time_damage(path)
    with pytest.raises((ValueError,KeyError,UnicodeError)):response(store,version,'assets/sku-0002.png')

@pytest.mark.parametrize('kind',['file_symlink','directory_symlink','directory_junction'])
def test_selected_image_real_redirect_is_rejected(snapshot,tmp_path,kind):
    from shared_platform.capability_runtime import ToolContextError
    m=module();result,store=admit(snapshot);directory=store.output/result['version']
    outside=tmp_path/'redirect-target';outside.mkdir();write(outside/'sku-0001.png',snapshot[-1])
    selected=directory/'assets/sku-0001.png'
    if kind=='file_symlink':selected.unlink();selected.symlink_to(outside/'sku-0001.png')
    else:
        selected.unlink();selected.parent.rmdir()
        if kind=='directory_symlink':selected.parent.symlink_to(outside,target_is_directory=True)
        else:
            import _winapi
            _winapi.CreateJunction(str(outside),str(selected.parent))
            assert selected.parent.lstat().st_file_attributes & 0x400
    assert selected.read_bytes()==snapshot[-1]
    with pytest.raises(ToolContextError,match='symlink or reparse point'):response(store,result['version'],'assets/sku-0001.png')

@pytest.mark.parametrize('name',['assets/sku-unknown.png','assets/../sku-0001.png','assets/sku-0001.png/extra','../data.js'])
def test_unknown_or_traversal_resources_never_escape_bound_membership(snapshot,name):
    _,store=admit(snapshot)
    assert response(store,store.index()['current'],name)[0]==404

def test_unknown_version_never_selects_current_or_stale_fallback(snapshot):
    _,store=admit(snapshot)
    with pytest.raises(ValueError):response(store,'0'*64,'assets/sku-0001.png')

@pytest.mark.parametrize('changed',['inventory_semantics','provenance_authority','pair_sri','unrequested_digest_type'])
def test_hash_consistent_forged_binding_still_rejects_healthy_resource(snapshot,changed):
    m=module();_,store=admit(two_assets(snapshot));_,manifest,directory=store.current()
    if changed=='inventory_semantics':
        inventory=m.loads((directory/'inputs/inventory.source').read_bytes())
        inventory['records'][0]['available']=999
        changed_raw=m.encoded(inventory)
        manifest['source_files']['inventory']['sha256']=m.sha(changed_raw)
    elif changed=='provenance_authority':manifest['provenance']['user_approval']=True
    elif changed=='pair_sri':manifest['pair_sha256']['data.js']='0'*64
    else:manifest['asset_sha256']['assets/sku-0001.png']=None
    version=m.sha(m.encoded({k:v for k,v in manifest.items() if k!='version'}));manifest['version']=version
    copied=store.output/version;shutil.copytree(directory,copied)
    if changed=='inventory_semantics':(copied/'inputs/inventory.source').write_bytes(changed_raw)
    marker=m.encoded(manifest);(copied/'audited.json').write_bytes(marker)
    index=store.index();index['versions'].append({'version':version,'sha256':m.sha(marker)})
    (store.artifact/'audited-serving.json').write_bytes(m.encoded(index))
    assert store.index()['versions'][-1]=={'version':version,'sha256':m.sha(marker)}
    with pytest.raises(ValueError):response(store,version,'assets/sku-0002.png')

def test_actual_private_handler_two_images_overlap_and_each_reads_complete_binding(snapshot,snapshot_http,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    m=module();result,store=admit(two_assets(snapshot));server,get=snapshot_http
    version=result['version'];_,manifest,directory=store.current()
    assert set(manifest['asset_sha256'])=={'assets/sku-0001.png','assets/sku-0002.png'}
    barrier=threading.Barrier(2);guard=threading.Lock();seen={};memberships={}
    original_read=m._bounded_file_bytes;original_pair=m.AuditedSnapshotStore._read_bound_pair
    def tracked(path,*args,**kwargs):
        raw=original_read(path,*args,**kwargs)
        with guard:seen.setdefault(threading.get_ident(),{})[Path(path).resolve()]=m.sha(raw)
        return raw
    def simultaneous(self,*args,**kwargs):
        barrier.wait(1)
        answer=original_pair(self,*args,**kwargs)
        with guard:memberships[threading.get_ident()]=dict(answer[1]['asset_sha256'])
        return answer
    monkeypatch.setattr(m,'_bounded_file_bytes',tracked)
    monkeypatch.setattr(m.AuditedSnapshotStore,'_read_bound_pair',simultaneous)
    names=('assets/sku-0001.png','assets/sku-0002.png')
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(get,'/supply-chain/audited/'+version+'/'+name) for name in names]
        responses=[future.result(timeout=4) for future in futures]
    assert [response[0] for response in responses]==[200,200]
    assert [response[2] for response in responses]==[snapshot[-1],snapshot[-1]]
    assert len(seen)==len(memberships)==2
    expected={store.artifact/'audited-serving.json',directory/'audited.json'}
    expected|={store.supply/name for name in m.CONSUMERS}
    expected|={directory/'inputs'/(name+'.source') for name in m.SOURCE_PATHS}
    expected|={directory/name for name in m.FILES}
    expected={path.resolve():m.sha(path.read_bytes()) for path in expected}
    assert len(m.CONSUMERS)==9 and len(m.SOURCE_PATHS)==5 and len(m.FILES)==2
    selected={ (directory/name).resolve() for name in names }
    for thread,reads in seen.items():
        assert memberships[thread]==manifest['asset_sha256']
        assert expected.items()<=reads.items()
        assert set(reads)-set(expected)<=selected
        assert len(set(reads)&selected)==1
        assert all(digest==m.sha(snapshot[-1]) for path,digest in reads.items() if path in selected)
    assert {path for reads in seen.values() for path in reads if path in selected}==selected
