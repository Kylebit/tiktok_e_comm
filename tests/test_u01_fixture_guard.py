"""Explicit guard negative controls, run separately from the zero-denial gate."""
import os
import socket
import pytest


@pytest.mark.parametrize('port',[8766,49289])
def test_nonfixture_local_services_are_denied_before_connect(port):
    if not os.environ.get('B4B_BOUND_RUN'):
        pytest.skip('Requires the isolated source-bound audit runner')
    with socket.socket() as connection:
        with pytest.raises(RuntimeError,match='bound validation denied non_fixture_port'):
            connection.connect(('127.0.0.1',port))
