"""Characterize why a dashboard target default cannot authorize worker scope."""

from shared_platform.release_control import _publication_scope


def test_dashboard_default_is_not_an_explicit_product_center_target_selection():
    _, projection = _publication_scope({'selected_sites': []}, None)
    assert projection['source'] == 'workbench_default'
    assert {'tiktok:MX', 'tiktok:GB', 'ozon:RU'} <= set(projection['selected_labels'])
    # These labels exist without any explicit selection in Product Center.
    assert not {'tiktok:MX', 'tiktok:GB', 'ozon:RU'} <= set(
        _publication_scope({'selected_sites': []}, ['miaoshou:COMMON'])[1]['selected_labels'])
