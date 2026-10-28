"""Reuse admission keeps identity and zero-generation facts mandatory."""
from copy import deepcopy

import pytest

from shared_platform.publication_image_qa import is_completed_image_asset


def source_asset():
    return {'status': 'REUSED_FROM_MIAOSHOU', 'provider': 'MIAOSHOU_EXISTING',
            'external_generation_count': 0, 'source_position': 6,
            'public_url': 'https://source.invalid/image.jpg',
            'artifact_digest': 'sha256:' + 'a' * 64}


def test_original_asset_reuse_is_accepted_without_inventing_generated_status():
    row = source_asset()
    before = deepcopy(row)
    assert is_completed_image_asset(row)
    assert row == before


@pytest.mark.parametrize('key,value', [
    ('provider', 'UNBOUND'), ('external_generation_count', 1),
    ('external_generation_count', False), ('source_position', 0),
    ('source_position', True), ('public_url', 'http://source.invalid/image.jpg'),
    ('artifact_digest', 'unknown'), ('status', 'READY'),
])
def test_unproven_or_nonzero_generation_is_not_admitted_as_original_reuse(key, value):
    row = source_asset()
    row[key] = value
    assert not is_completed_image_asset(row)
