"""Source titles stay intact; only covered advice stops being a hard gate."""
from copy import deepcopy

import pytest

from shared_platform.round1_workspace import _auto_unresolved_preview_warnings

ADVICE = '当前商品标题仍含中文或缺少英文字母；可以先锁定事实，但发布前建议采用平台标题候选'


def review():
    labels = ['shopee:MY', 'tiktok:HB_TH', 'ozon:RU']
    copies = [('en', 'Wooden tissue holder'), ('th', 'ที่ใส่ทิชชู่ไม้'), ('ru', 'Деревянная подставка для салфеток')]
    return {'product_facts': {'title': '实木立式纸巾架'},
            'target_selection': {'requested': labels},
            'targets': [{'target': label, 'copy': {'status': 'PROPOSED', 'language': lang, 'title': title}}
                        for label, (lang, title) in zip(labels, copies)]}


def test_complete_distinct_localized_titles_resolve_only_source_advice():
    packet = review(); before = deepcopy(packet)
    assert _auto_unresolved_preview_warnings(packet, [ADVICE]) == []
    assert _auto_unresolved_preview_warnings(packet, [ADVICE, 'Unresolved source price']) == ['Unresolved source price']
    assert packet == before


@pytest.mark.parametrize('change', [
    lambda p: p['targets'].pop(),
    lambda p: p['targets'].append(deepcopy(p['targets'][0])),
    lambda p: p['targets'][0]['copy'].update(title=''),
    lambda p: p['targets'][0]['copy'].update(title='实木立式纸巾架'),
    lambda p: p['targets'][0]['copy'].update(title='Wooden 纸巾架'),
    lambda p: p['targets'][0]['copy'].update(title='123456'),
    lambda p: p['targets'][0]['copy'].update(status='AGENT_GENERATION_REQUIRED'),
    lambda p: p['targets'][1]['copy'].update(language='en'),
    lambda p: p['product_facts'].update(title='Original source already in English'),
])
def test_unresolved_or_incomplete_candidates_keep_advice_blocking(change):
    packet=review();change(packet)
    assert _auto_unresolved_preview_warnings(packet, [ADVICE]) == [ADVICE]


@pytest.mark.parametrize('warnings', [None, [], ['other warning'], [ADVICE+' changed'], [ADVICE, ADVICE]])
def test_unrelated_and_legacy_warning_shapes_are_unchanged(warnings):
    assert _auto_unresolved_preview_warnings(review(), warnings) == warnings
