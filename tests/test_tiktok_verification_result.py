from copy import deepcopy
import pytest
from test_tiktok_continuation_report_compatibility import _zero_report
from test_tiktok_lineage_protocol import _completion_manifest
import shared_platform.tiktok_lineage_recovery as protocol


def test_failed_scope_verification_result_uses_safe_codes():
    manifest=_completion_manifest()
    result=deepcopy(_zero_report(manifest)['continuation_evidence'])
    verification={'schema_version':'tiktok-scope-verification/v1','status':'FAILED',
        'target_count':len(manifest['recovery_target_labels']), 'unique_asset_count':1,
        'failures':[{'target_label':manifest['recovery_target_labels'][-1],
                     'stage':'VERIFY','code':'ASSET_READ_UNAVAILABLE'}]}
    verification['verification_digest']=protocol._canonical_digest(verification)
    result['verification_summary']=verification
    result['result_digest']=protocol._canonical_digest({k:v for k,v in result.items() if k!='result_digest'})
    assert protocol.validate_tiktok_lineage_recovery_result(result,manifest=manifest)==result
    verification['failures'][0]['code']='arbitrary-provider-secret'
    verification['verification_digest']=protocol._canonical_digest({k:v for k,v in verification.items() if k!='verification_digest'})
    result['result_digest']=protocol._canonical_digest({k:v for k,v in result.items() if k!='result_digest'})
    with pytest.raises(ValueError):protocol.validate_tiktok_lineage_recovery_result(result,manifest=manifest)
