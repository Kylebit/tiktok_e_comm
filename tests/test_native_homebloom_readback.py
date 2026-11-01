"""HomeBloom is Miaoshou-only; official token and transport are out of scope."""
import pytest

from shared_platform.native_tiktok_exact_readback import NativeTikTokExactStorefrontReadback
from domains.channel_operations.tiktok_v4_execution import _readback_fact, _classify_target


@pytest.mark.parametrize('label', ['tiktok:HB_PH', 'tiktok:HB_MY', 'tiktok:HB_TH', 'tiktok:HB_VN'])
def test_homebloom_never_loads_official_token_or_substitutes_official_shop(label, monkeypatch):
    from core import auth
    from modules.miaoshou.tiktok_publisher import EXPECTED_SHOP_ID_BY_TARGET
    calls = []
    def forbidden(name):
        def call(*args, **kwargs):
            calls.append(name)
            raise AssertionError('HomeBloom must not enter ' + name)
        return call
    monkeypatch.setattr(auth, 'token_path', forbidden('default_official_token_path'))
    evidence = {}
    reader = NativeTikTokExactStorefrontReadback(
        token_loader=forbidden('official_token_loader'),
        official_call=forbidden('official_api'),
        shop_post=forbidden('alternate_shop_join'), evidence=evidence)
    command = {'target_label': label, 'control': {'shop_id': EXPECTED_SHOP_ID_BY_TARGET[label]},
               'target': {'site': label.split(':')[1]}, 'skus': []}
    dispatch = {'target_label': label, 'outcome': 'ACCEPTED', 'external_write_count': 1,
                'write_request_count': 1}
    result = reader.readback(command=command, dispatch=dispatch)
    assert result == {'target_label': label, 'authority': 'UNAVAILABLE', 'status': 'UNAVAILABLE', 'exact': False}
    assert calls == [] and evidence == {}
    # Consume the actual adapter value through the actual v4 classifier. An
    # accepted Miaoshou publish remains processing, never official success.
    classified = _classify_target(dispatch, _readback_fact(result, label, command))
    assert classified['status'] == 'PROCESSING'
    assert classified['reason_code'] == 'STOREFRONT_READBACK_UNAVAILABLE'
    assert classified['dispatch_outcome'] == 'ACCEPTED'
    assert 'official_catalog_rows' not in classified and not classified['retry_safe']


def test_homebloom_unavailable_readback_does_not_hide_rejected_dispatch():
    label = 'tiktok:HB_PH'
    reader = NativeTikTokExactStorefrontReadback()
    dispatch = {'target_label': label, 'outcome': 'REJECTED', 'external_write_count': 0}
    result = reader.readback(command={'target_label': label}, dispatch=dispatch)
    classified = _classify_target(dispatch, _readback_fact(result, label, {}))
    assert classified['status'] == 'FAILED'
    assert classified['reason_code'] == 'DISPATCH_REJECTED'
