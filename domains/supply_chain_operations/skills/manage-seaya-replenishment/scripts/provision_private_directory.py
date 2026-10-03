"""Provision one NEW private directory only after exact separate authority.

Never modify an existing directory, create parents, delete data or migrate tokens.
On Windows, apply the protected DACL at CreateDirectory time, not afterward.
"""
from pathlib import Path
import ctypes,hashlib,importlib.util,os
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('provision_storage',HERE/'private_storage.py');storage=importlib.util.module_from_spec(spec);spec.loader.exec_module(storage)
g=storage.g

def binding():
    return {str(p.relative_to(g.ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__).resolve(),HERE/'private_storage.py',HERE/'session_recovery.py']}

def identity(path):
    s=path.stat();return {'device':s.st_dev,'inode':s.st_ino}

def prepare(path):
    path=storage.plain(path)
    g.require(not any((p/'.git').exists() for p in (path,*path.parents)),'PRIVATE_INSIDE_GIT')
    g.require(path.parent.is_dir(),'PARENT_MISSING')
    g.require(not path.exists(),'TARGET_EXISTS')
    return {'schema':'u04-private-directory/v1','target':str(path),'parent_identity':identity(path.parent),'owner_sid':g.current_sid() if os.name=='nt' else str(os.getuid()),'sources':binding(),'policy':'new directory, protected owner-only inheritable ACL','mode':'prepare'}

def _create(path,sid):
    if os.name!='nt':path.mkdir(mode=0o700);return
    from ctypes import wintypes as w
    adv=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    class SecurityAttributes(ctypes.Structure):
        _fields_=[('nLength',w.DWORD),('lpSecurityDescriptor',ctypes.c_void_p),('bInheritHandle',w.BOOL)]
    descriptor=ctypes.c_void_p()
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[w.LPCWSTR,w.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    kernel.CreateDirectoryW.argtypes=[w.LPCWSTR,ctypes.POINTER(SecurityAttributes)]
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    g.require(adv.ConvertStringSecurityDescriptorToSecurityDescriptorW('O:'+sid+'D:P(A;OICI;FA;;;'+sid+')',1,ctypes.byref(descriptor),None),'ACL_DESCRIPTOR_FAILED')
    try:
        attributes=SecurityAttributes(ctypes.sizeof(SecurityAttributes),descriptor,False)
        g.require(kernel.CreateDirectoryW(str(path),ctypes.byref(attributes)),'DIRECTORY_CREATE_FAILED')
    finally:kernel.LocalFree(descriptor)

def verify(path):
    path=storage.plain(path);g.protected(path);g.require(path.is_dir() and not any(path.iterdir()),'DIRECTORY_NOT_EMPTY')
    return {'state':'PROTECTED_EMPTY_DIRECTORY','path':str(path),'identity':identity(path),'credential_reads':0,'credential_writes':0}

def execute(plan,authorization):
    g.require(type(authorization) is dict and authorization.get('directory_create') is True and authorization.get('plan_digest')==g.plan_digest(plan) and type(authorization.get('authority')) is str and bool(authorization['authority']),'AUTHORIZATION_REQUIRED')
    g.require(plan.get('schema')=='u04-private-directory/v1' and plan.get('sources')==binding() and plan.get('mode')=='prepare','SOURCE_OR_PLAN_CHANGED')
    # Metadata is checked again. Existing or partially created directories are
    # never adopted, repaired or deleted by execute, even under the same plan.
    current=prepare(plan['target']);g.require(current==plan,'DIRECTORY_PLAN_CHANGED')
    path=Path(plan['target']);_create(path,plan['owner_sid'])
    try:return verify(path)
    except Exception:raise g.RecoveryError('PROVISION_UNVERIFIED_RECONCILE') from None
