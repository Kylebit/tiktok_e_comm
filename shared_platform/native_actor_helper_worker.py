"""Fixed trusted helper entry. No HTTP, provider, release DB or publication."""
import os
from pathlib import Path
import sys

from shared_platform import native_actor_launcher as launcher
from shared_platform.common_offer_authority_store import canonical_bytes
from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform.native_windows_actor import NativeActorServiceConfig, bootstrap_service
from shared_platform import local_operator_session as owner


def main(argv):
    if len(argv)!=4 or len(argv[3])!=32 or any(c not in '0123456789abcdef' for c in argv[3]):
        return 74
    root=Path(argv[0]); sid=owner.current_windows_owner_sid()
    owner.verify_owner_only(owner._safe_path(root),sid)
    config=NativeActorServiceConfig(root/'actor',argv[1],int(argv[2]))
    service=bootstrap_service(config)
    ready_written=False
    while True:
        with launcher.NativeOwnerPipeLauncher(service) as pipe:
            if not ready_written:
                row={'schema_version':'native-owner-helper-ready/v1','nonce':argv[3],
                    'pid':os.getpid(),'owner_sid':sid,'instance_id':pipe.endpoint.instance_id,
                    'pipe_name':pipe.endpoint.pipe_name}
                path=root/'ready.json'
                with path.open('xb') as stream: stream.write(canonical_bytes(row))
                owner.verify_owner_only(path,sid,protected=False)
                ready_written=True
            try:
                pipe.serve_once(timeout_seconds=2)
            except ApprovalBlocked as error:
                # Only ordinary idle connect timeout is a reusable pipe cycle.
                if launcher._IO_POISONED or pipe._poisoned: return 73
                if str(error)!='NATIVE_LAUNCHER_IO_TIMEOUT': return 74
        if launcher._IO_POISONED: return 73


if __name__=='__main__':
    try: result=main(sys.argv[1:])
    except BaseException: result=74
    raise SystemExit(result)
