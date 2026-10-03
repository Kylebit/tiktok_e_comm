from copy import deepcopy
import pytest
from shared_platform.publication_r3_image_bridge import _frozen_shopee_master_source


def prices():
    return {'shopee:MY':{'source':{'region':'MY','target_key':'shopee_my'},
        'sku_prices':[{'model_sku':'0988','list_price':37,'currency':'MYR','global_original_price_cny':64.75}]},
        'shopee:PH':{'source':{'region':'PH','target_key':'shopee_ph'},
        'sku_prices':[{'model_sku':'0988','list_price':478,'currency':'PHP','global_original_price_cny':56.4}]}}


def test_existing_master_order_is_explicit_metadata_without_repricing():
    value=prices();before=deepcopy(value)
    source=_frozen_shopee_master_source(value,{'snapshot_digest':'sha256:frozen'})
    assert source['region']=='PH' and source['target_key']=='shopee_ph'
    assert source['provenance']['round1_snapshot_digest']=='sha256:frozen'
    assert source['provenance']['price_recalculated'] is False
    assert value==before
    source['region']='changed'
    assert value==before


def test_selection_subset_never_adds_unselected_region():
    value=prices();value.pop('shopee:PH')
    assert _frozen_shopee_master_source(value,{'snapshot_digest':'sha256:frozen'})['region']=='MY'
    assert _frozen_shopee_master_source({'tiktok:LH_PH':{}},{}) is None


@pytest.mark.parametrize('source',[{}, {'region':'VN','target_key':'shopee_ph'}])
def test_missing_or_conflicting_source_is_not_filled_from_other_target(source):
    value=prices();value['shopee:PH']['source']=source
    with pytest.raises(ValueError,match='FROZEN_SHOPEE_MASTER_SOURCE_REQUIRED'):
        _frozen_shopee_master_source(value,{'snapshot_digest':'sha256:frozen'})
