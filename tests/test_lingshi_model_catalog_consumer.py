"""Offline catalog fixtures; no credentials, provider calls, or paid execution."""
from copy import deepcopy

import pytest

from modules.sourcing.brand_image_lingshi_generation import resolve_brand_image_model
from modules.sourcing.lingshi_client import LingshiClient


ROWS = [
    {'name': 'gpt-image-2', 'type': 'image', 'available_for_this_key': False},
    {'name': 'tt-image-2', 'type': 'image', 'available_for_this_key': True},
]
DETAIL = {'name': 'tt-image-2', 'params': [
    {'name': 'images'}, {'name': 'size', 'options': [{'value': '2048x2048'}]},
    {'name': 'quality', 'options': [{'value': 'medium'}]},
]}


class OfflineCatalog:
    def __init__(self, payload, detail=None):
        self.payload = payload
        self.detail = deepcopy(DETAIL if detail is None else detail)
        self.details = []

    def list_models(self, kind):
        assert kind == 'image'
        return self.payload

    def model_detail(self, name):
        self.details.append(name)
        return self.detail


@pytest.mark.parametrize('envelope', ['models', 'data-list', 'data-models', 'data-items'])
def test_actual_and_legacy_envelopes_preserve_candidate_and_edit_contract(envelope):
    rows = deepcopy(ROWS)
    payload = {'models': rows} if envelope == 'models' else {'data': (
        rows if envelope == 'data-list' else {envelope.removeprefix('data-'): rows})}
    before = deepcopy(payload)
    client = OfflineCatalog(payload)
    name, detail = resolve_brand_image_model(client)
    assert name == 'tt-image-2'
    assert detail == DETAIL
    assert client.details == ['tt-image-2']
    assert payload == before


@pytest.mark.parametrize('payload', [
    None,
    {'models': {'name': 'tt-image-2'}},
    {'models': [None, deepcopy(ROWS[1])]},
    {'models': [{'name': '', 'available_for_this_key': True}, deepcopy(ROWS[1])]},
    {'models': [{'name': 7, 'available_for_this_key': True}, deepcopy(ROWS[1])]},
    {'models': [deepcopy(ROWS[1]), deepcopy(ROWS[1])]},
    {'models': [{**ROWS[1], 'type': 'chat'}]},
    {'models': [{'name': 'unapproved-offline-alias', 'type': 'image', 'available_for_this_key': True}]},
])
def test_invalid_or_unapproved_identity_never_reaches_model_detail(payload):
    client = OfflineCatalog(payload)
    with pytest.raises(ValueError):
        resolve_brand_image_model(client)
    assert client.details == []


def test_top_level_models_remains_authoritative_over_legacy_data():
    client = OfflineCatalog({'models': [], 'data': deepcopy(ROWS)})
    with pytest.raises(ValueError):
        resolve_brand_image_model(client)
    assert client.details == []
    malformed = OfflineCatalog({'models': 'invalid', 'data': deepcopy(ROWS)})
    with pytest.raises(ValueError):
        resolve_brand_image_model(malformed)
    assert malformed.details == []


def test_detail_identity_conflict_cannot_select_another_model():
    client = OfflineCatalog({'models': deepcopy(ROWS)}, {**DETAIL, 'name': 'another-offline-model'})
    with pytest.raises(ValueError):
        resolve_brand_image_model(client)
    assert client.details == ['tt-image-2']


def test_typeless_unrelated_models_do_not_block_the_approved_alias():
    # An image-filtered directory can contain legacy rows without explicit type.
    client = OfflineCatalog({'models': [
        {'name': 'unrelated-offline-model', 'available_for_this_key': True},
        {'name': 'tt-image-2', 'available_for_this_key': True},
    ]})
    name, detail = resolve_brand_image_model(client)
    assert name == 'tt-image-2'
    assert detail == DETAIL
    assert client.details == ['tt-image-2']


def test_raw_sdk_catalog_contract_is_not_normalized_or_mutated():
    payload = {'models': deepcopy(ROWS), 'metadata': {'offline': True}}

    class OfflineSession:
        trust_env = True
        calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url))

            class Response:
                ok = True

                def json(self):
                    return payload

            return Response()

    session = OfflineSession()
    client = LingshiClient(api_key='synthetic-offline-key', session=session)
    assert client.list_models('image') is payload
    assert session.calls == [('GET', 'https://api.lk888.ai/api/v1/skills/models?type=image')]
    assert payload == {'models': ROWS, 'metadata': {'offline': True}}
