"""Read existing COMMON standing intent through service-owned startup inputs.

This local policy is neither an authenticated provider account nor a complete
write-history/installation receipt. Its cap counts confirmed writes, not tries.
"""
from dataclasses import dataclass
import hashlib
import json
import re

from shared_platform import local_operator_session, publication_autopilot
from shared_platform.publication_runtime_config import StartupConfig, _read


@dataclass(frozen=True)
class CommonStandingPolicyFacts:
    status: str
    reason: str | None
    raw_digest: str | None = None
    policy_id: str | None = None
    maximum_confirmed_writes: int | None = None

    def diagnostic(self):
        return {'schema_version': 'common-native-standing-policy-facts/v1',
                'status': self.status, 'reason': self.reason,
                'raw_digest': self.raw_digest, 'policy_id': self.policy_id,
                'maximum_confirmed_writes': self.maximum_confirmed_writes
                    if self.maximum_confirmed_writes is not None else 'UNKNOWN',
                'confirmed_write_count': 'UNKNOWN', 'account_authority': 'UNKNOWN',
                'coverage_authority': 'UNKNOWN', 'schema_installation': 'UNKNOWN',
                'trust': 'SAME_WINDOWS_USER', 'execution_authority': False}


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('COMMON_POLICY_DUPLICATE_KEY')
        result[name] = value
    return result


class NativeCommonStandingPolicyReader:
    """Installed only by the service, never supplied by an HTTP request."""
    def __init__(self, config):
        if type(config) is not StartupConfig:
            raise TypeError('COMMON_POLICY_STARTUP_CONFIG_REQUIRED')
        self._config = config
        self._owner = local_operator_session.current_windows_owner_sid()
        if (type(self._owner) is not str
                or not re.fullmatch(r'S-1-[0-9]+(?:-[0-9]+)+', self._owner)):
            raise ValueError('COMMON_POLICY_WINDOWS_OWNER_UNAVAILABLE')

    def read(self):
        raw_digest = None
        try:
            if local_operator_session.current_windows_owner_sid() != self._owner:
                raise ValueError('COMMON_POLICY_WINDOWS_OWNER_CHANGED')
            raw = _read(self._config.root, self._config.policy)
            raw_digest = hashlib.sha256(raw).hexdigest()
            policy = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object)
            if (type(policy) is not dict or type(policy.get('policy_id')) is not str
                    or not policy['policy_id'].strip()):
                raise ValueError('COMMON_POLICY_DOCUMENT_INVALID')
            policy = publication_autopilot.validate_autopilot_policy(policy)
            common = policy.get('miaoshou')
            review = policy.get('review_contract')
            if (type(common) is not dict
                    or type(review) is not dict
                    or review.get('intermediate_human_approval_required') is not False
                    or review.get('intermediate_artifacts_are_execution_authority') is not False
                    or review.get('sole_human_gate') != 'FINAL_MARKETPLACE_PUBLISH'
                    or policy['authority']['kind'] != 'standing_user_instruction'
                    or common.get('common_baseline_sync_allowed') is not True
                    or type(common.get('maximum_confirmed_writes_per_product')) is not int
                    or common['maximum_confirmed_writes_per_product'] != 1
                    or common.get('requires_exact_readback') is not True
                    or common.get('marketplace_draft_or_publish_not_authorized') is not True
                    or 'miaoshou_common_baseline_sync' not in policy['automatic_steps']):
                raise ValueError('COMMON_POLICY_CONTRACT_INVALID')
            return CommonStandingPolicyFacts('KNOWN_LOCAL_USER_INTENT', None, raw_digest,
                policy['policy_id'], common['maximum_confirmed_writes_per_product'])
        except FileNotFoundError:
            return CommonStandingPolicyFacts('UNKNOWN', 'COMMON_POLICY_MISSING')
        except (ValueError, TypeError, KeyError, OSError, UnicodeError, RecursionError) as error:
            return CommonStandingPolicyFacts('UNKNOWN', str(error), raw_digest)


def inspect_service_policy(reader):
    if type(reader) is not NativeCommonStandingPolicyReader:
        return CommonStandingPolicyFacts('UNKNOWN', 'COMMON_POLICY_SERVICE_READER_REQUIRED')
    return reader.read()
