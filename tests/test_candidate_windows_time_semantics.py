"""Actual Windows creation/change-time difference, without mock stat results."""
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from shared_platform import publication_rounds as rounds
from shared_platform import round1_workspace as workspace


def test_actual_windows_creation_change_difference_keeps_original_source_bytes(tmp_path, monkeypatch):
    assert os.name == 'nt', 'This fixed regression binds the actual Windows runtime'
    root=tmp_path/'reports'/'product-preparation'
    monkeypatch.setattr(rounds,'REPORTS_ROOT',root)
    offer='3828811808'
    directory=rounds.report_dir(offer)
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/'first-review-candidate-plan.json'
    raw=b'{"owned_creation_time_semantics": true}\n'
    path.write_bytes(raw)
    # Deliberately separate two real Windows timestamp semantics, without a
    # sleep, retry, source/read interception or modification of result objects.
    original=path.stat()
    creation_ns=original.st_birthtime_ns-1_000_000_000
    ticks=creation_ns//100+116444736000000000
    creation=wintypes.FILETIME(ticks&0xffffffff,ticks>>32)
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    create=api.CreateFileW
    create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    create.restype=wintypes.HANDLE
    settime=api.SetFileTime
    settime.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.FILETIME),ctypes.POINTER(wintypes.FILETIME),ctypes.POINTER(wintypes.FILETIME)]
    settime.restype=wintypes.BOOL
    close=api.CloseHandle;close.argtypes=[wintypes.HANDLE];close.restype=wintypes.BOOL
    handle=create(str(path),0x100,7,None,3,0,None)
    assert handle!=wintypes.HANDLE(-1).value,ctypes.get_last_error()
    try:assert settime(handle,ctypes.byref(creation),None,None),ctypes.get_last_error()
    finally:assert close(handle),ctypes.get_last_error()
    before=path.lstat()
    with path.open('rb') as stream:
        opened=os.fstat(stream.fileno())
        assert stream.read()==raw
    fields=('st_dev','st_ino','st_mode','st_size','st_mtime_ns')
    assert all(getattr(before,key)==getattr(opened,key) for key in fields)
    assert before.st_birthtime_ns==opened.st_birthtime_ns
    assert before.st_ctime_ns!=opened.st_ctime_ns
    proof={'runtime_semantics':'real-Windows-path-creation-versus-handle-change',
           'path_ctime_ns':before.st_ctime_ns,'handle_ctime_ns':opened.st_ctime_ns,
           'path_birthtime_ns':before.st_birthtime_ns,'handle_birthtime_ns':opened.st_birthtime_ns,
           'five_identity_fields_equal':True,'original_bytes_sha256':hashlib.sha256(raw).hexdigest()}
    (tmp_path/'actual-creation-change-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
    source,candidate=workspace._candidate_input(offer)
    assert source['present'] is True
    assert source['raw_sha256']==hashlib.sha256(raw).hexdigest()
    assert base64.b64decode(source['raw_base64'],validate=True)==raw
    assert candidate==json.loads(raw) and path.read_bytes()==raw


def _stat_fields():
    return dict(st_dev=1, st_ino=2, st_mode=33206, st_size=8,
                st_mtime_ns=100, st_ctime_ns=200, st_birthtime_ns=300)


def test_missing_windows_birthtime_fails_closed(monkeypatch):
    monkeypatch.setattr(workspace, 'os', SimpleNamespace(name='nt'))
    fields = _stat_fields()
    fields.pop('st_birthtime_ns')
    with pytest.raises(workspace.Round1WorkspaceError, match='R1_CANDIDATE_INPUT_INVALID'):
        workspace._candidate_file_identity(SimpleNamespace(**fields))


def test_non_windows_retains_ctime_without_requiring_birthtime(monkeypatch):
    monkeypatch.setattr(workspace, 'os', SimpleNamespace(name='posix'))
    fields = _stat_fields()
    fields.pop('st_birthtime_ns')
    before = workspace._candidate_file_identity(SimpleNamespace(**fields))
    assert before == (1, 2, 33206, 8, 100, 200)
    fields['st_ctime_ns'] += 1
    assert workspace._candidate_file_identity(SimpleNamespace(**fields)) != before


@pytest.mark.parametrize('field', ['st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_birthtime_ns'])
def test_windows_identity_still_rejects_each_bound_field_change(monkeypatch, field):
    monkeypatch.setattr(workspace, 'os', SimpleNamespace(name='nt'))
    fields = _stat_fields()
    before = workspace._candidate_file_identity(SimpleNamespace(**fields))
    assert before == (1, 2, 33206, 8, 100, 300)
    fields[field] += 1
    assert workspace._candidate_file_identity(SimpleNamespace(**fields)) != before
