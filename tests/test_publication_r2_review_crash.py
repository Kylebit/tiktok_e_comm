import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest

from test_publication_r2_review import registered,view,request
from test_publication_r2_candidate import candidate
from test_publication_takeover import packet


def command(root,mode,ready,body):
    return [sys.executable,'-B',str(Path(__file__).with_name('r2_lock_worker.py')),
        str(root),mode,str(ready),json.dumps(body)]


def child_env():
    return dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONIOENCODING='utf-8')


@pytest.mark.parametrize('mode,code,revision',[('before_replace',77,0),('after_replace',78,1)])
def test_process_exit_releases_save_lock_and_same_request_recovers(registered,tmp_path,mode,code,revision):
    from shared_platform.publication_r2_review import decide,registered_project
    root,_=registered;body=request(view(root))
    child=subprocess.run(command(root,mode,tmp_path/'ready',body),env=child_env(),capture_output=True,timeout=15)
    assert child.returncode==code,child.stderr.decode('utf-8',errors='replace')
    lock=registered_project('123',runtime_root=root).parent/'decision.lock'
    assert lock.exists() and view(root)['revision']==revision
    saved=decide(body,runtime_root=root)
    assert saved['revision']==1 and saved['keep_count']==1 and not saved['publication_authorized']
    assert decide(body,runtime_root=root)==saved


def test_live_process_lock_cannot_be_stolen_by_another_save(registered,tmp_path):
    from shared_platform.publication_r2_review import decide,registered_project
    root,_=registered;body=request(view(root));ready=tmp_path/'writer-ready'
    child=subprocess.Popen(command(root,'hold',ready,body),env=child_env(),stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        deadline=time.monotonic()+10
        while not ready.exists():
            assert child.poll() is None
            assert time.monotonic()<deadline,'writer did not acquire its lock'
            time.sleep(.02)
        assert view(root)['revision']==0 and child.poll() is None
        lock=registered_project('123',runtime_root=root).parent/'decision.lock'
        inode=lock.stat().st_ino
        assert lock.stat().st_size==1
        with pytest.raises(ValueError,match='R2_REVIEW_SAVE_IN_PROGRESS'):
            decide(body,runtime_root=root)
        with pytest.raises(ValueError,match='R2_REVIEW_SAVE_IN_PROGRESS'):
            decide(request(view(root),'remove'),runtime_root=root)
        assert view(root)['revision']==0 and child.poll() is None
        assert lock.stat().st_ino==inode and lock.stat().st_size==1
        stdout,stderr=child.communicate(b'x',timeout=10)
        assert child.returncode==0,stderr.decode('utf-8',errors='replace')
        saved=json.loads(stdout)
        assert saved['revision']==1 and saved['keep_count']==1
        assert lock.read_bytes()==b'\0' and lock.stat().st_ino==inode
        assert decide(body,runtime_root=root)==saved
        with pytest.raises(ValueError,match='R2_REVIEW_REVISION_CONFLICT'):
            decide(request(dict(view(root),revision=0),'remove'),runtime_root=root)
    finally:
        if child.poll() is None:
            child.kill();child.communicate(timeout=5)


@pytest.mark.parametrize('existing',[b'',b'legacy sentinel bytes'])
def test_unowned_residual_file_does_not_block_or_get_truncated(registered,existing):
    from shared_platform.publication_r2_review import decide,registered_project
    root,_=registered
    lock=registered_project('123',runtime_root=root).parent/'decision.lock'
    lock.write_bytes(existing);inode=lock.stat().st_ino
    saved=decide(request(view(root)),runtime_root=root)
    assert saved['keep_count']==1 and saved['revision']==1
    assert lock.stat().st_ino==inode
    assert lock.read_bytes()==(existing or b'\0')


def test_future_revision_does_not_create_a_lock_file(registered):
    from shared_platform.publication_r2_review import decide
    root,_=registered;body=request(view(root));body['expected_revision']=99
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='R2_REVIEW_REVISION_CONFLICT'):
        decide(body,runtime_root=root)
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
