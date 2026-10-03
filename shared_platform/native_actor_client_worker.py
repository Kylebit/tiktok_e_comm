"""Fixed same-owner client I/O boundary; never runs inside the web process."""
import os
from pathlib import Path
import sys

from shared_platform import native_actor_launcher as launcher
from shared_platform import native_actor_helper as helper
from shared_platform import local_operator_session as owner


def read_request(root, nonce):
    sid=owner.current_windows_owner_sid()
    root=owner._safe_path(root)
    owner.verify_owner_only(root,sid)
    row=helper._read_metadata(root/'request.json',sid)
    if (set(row)!={'schema_version','nonce','owner_sid','server_pid','instance_id',
            'pipe_name','owner_root','timeout_seconds'}
            or row['schema_version']!='native-owner-client-request/v1'
            or row['nonce']!=nonce or row['owner_sid']!=sid
            or type(row['server_pid']) is not int or row['server_pid']<=0
            or type(row['instance_id']) is not str or len(row['instance_id'])!=32
            or any(c not in '0123456789abcdef' for c in row['instance_id'])
            or row['pipe_name']!=r'\\.\pipe\OrbitNativeOwner-'+row['instance_id']
            or type(row['owner_root']) is not str):
        helper._deny('NATIVE_HELPER_CLIENT_REQUEST_INVALID')
    helper._bound(row['timeout_seconds'])
    actor_root=owner._safe_path(Path(row['owner_root']))
    owner.verify_owner_only(actor_root,sid)
    endpoint=launcher.NativeLauncherEndpoint(row['pipe_name'],row['server_pid'],sid,
        row['instance_id'],actor_root)
    return row,endpoint


def main(argv):
    if len(argv)!=2 or len(argv[1])!=32 or any(c not in '0123456789abcdef' for c in argv[1]):
        return 74
    root=Path(argv[0])
    from shared_platform import native_actor_authentication as auth
    sid=owner.current_windows_owner_sid()
    preliminary=helper._read_metadata(root/'request.json',sid)
    if preliminary.get('schema_version')==auth.CLIENT_SCHEMA:
        row,endpoint=auth.read_client_request(root,argv[1])
        reply=auth.request_authentication(endpoint,row,timeout_seconds=row['timeout_seconds'])
        result={'schema_version':auth.CLIENT_RESULT_SCHEMA,'nonce':argv[1],
            'client_pid':os.getpid(),'server_pid':endpoint.process_id,
            'server_birth_100ns':row['server_birth_100ns'],'instance_id':endpoint.instance_id,'reply':reply}
        with (root/'result.json').open('xb') as stream:
            stream.write(helper.canonical_bytes(result))
        owner.verify_owner_only(root/'result.json',endpoint.owner_sid,protected=False)
        return 0
    row,endpoint=read_request(root,argv[1])
    # All overlapped addresses/quarantine globals belong to this owned child.
    path=launcher.request_owner_carrier(endpoint,timeout_seconds=row['timeout_seconds'])
    result={'schema_version':'native-owner-client-result/v1','nonce':argv[1],
        'client_pid':os.getpid(),'server_pid':endpoint.process_id,
        'instance_id':endpoint.instance_id,'filename':path.name}
    with (root/'result.json').open('xb') as stream:
        stream.write(helper.canonical_bytes(result))
    owner.verify_owner_only(root/'result.json',endpoint.owner_sid,protected=False)
    return 0


if __name__=='__main__':
    try: result=main(sys.argv[1:])
    except BaseException: result=73 if launcher._IO_POISONED else 74
    raise SystemExit(result)
