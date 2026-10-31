"""Real owned Windows helper, request-local reuse and tamper invalidation."""
import json
import threading

import pytest

from shared_platform import native_sole_final_service as channel
from shared_platform import native_actor_helper as helper
from shared_platform import native_actor_authentication as authentication
from shared_platform.native_windows_actor import NativeActorServiceConfig
from shared_platform.release_store import ReleaseStore
from shared_platform.final_review_server_admission import ApprovalBlocked


def _runtime(tmp_path,supervisor):
    reader=authentication.NativeHelperActorProfileReader(supervisor,supervisor.request_carrier())
    runtime=object.__new__(channel.NativeSoleFinalService)
    runtime._lock=threading.RLock();runtime._closed=False
    runtime._reader=reader;runtime.supervisor=supervisor
    runtime.store=ReleaseStore(tmp_path/'unused.db')
    return runtime


def test_one_operation_reuses_real_owner_authentication_rpc(tmp_path):
    with helper.NativeActorHelperSupervisor(NativeActorServiceConfig(tmp_path/'owner','Kyle',120)) as supervisor:
        runtime=_runtime(tmp_path,supervisor)
        before=len(supervisor.client_history)
        with runtime.operation() as service:
            actors=[service._actor() for unused in range(3)]
        assert actors[0] == actors[1] == actors[2]
        assert len(supervisor.client_history)-before == 1
        # No reuse crosses the operation/write boundary.
        runtime._reader.read_verified()
        assert len(supervisor.client_history)-before == 2


@pytest.mark.parametrize('damage',['expiry','carrier','profile','generation'])
def test_reused_identity_is_invalidated_before_its_next_use(tmp_path,monkeypatch,damage):
    with helper.NativeActorHelperSupervisor(NativeActorServiceConfig(tmp_path/'owner','Kyle',120)) as supervisor:
        runtime=_runtime(tmp_path,supervisor)
        with runtime.operation() as service:
            first=service._actor();before=len(supervisor.client_history)
            if damage=='expiry':
                now=authentication.time.time()
                monkeypatch.setattr(authentication.time,'time',lambda:now+121)
            elif damage=='carrier':
                raw=json.loads(runtime._reader.carrier.read_bytes())
                raw['capability']='0'*len(raw['capability'])
                runtime._reader.carrier.write_bytes(helper.canonical_bytes(raw))
            elif damage=='profile':
                path=runtime._reader.endpoint.owner_root/'native-owner.json'
                raw=json.loads(path.read_bytes());raw['logical_profile']='other-profile'
                path.write_bytes(helper.canonical_bytes(raw))
            else:supervisor.replace_owned()
            with pytest.raises(ApprovalBlocked):service._actor()
            assert len(supervisor.client_history)==before
            assert first['identity_verified'] is True


def test_operation_error_does_not_reenter_or_retry_the_body(tmp_path):
    with helper.NativeActorHelperSupervisor(NativeActorServiceConfig(tmp_path/'owner','Kyle',120)) as supervisor:
        runtime=_runtime(tmp_path,supervisor);entered=[]
        with pytest.raises(ApprovalBlocked,match='NATIVE_ACTOR_SESSION_EXPIRED'):
            with runtime.operation():
                entered.append(1)
                raise ApprovalBlocked('NATIVE_ACTOR_SESSION_EXPIRED')
        assert entered==[1]
        assert len(supervisor.client_history)==2  # carrier issuance + one authentication
