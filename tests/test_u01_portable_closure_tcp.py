"""Unmodified canonical/relocated stdlib client against an owned actual Handler."""
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

from modules.products import server
from scripts.package_agent_tools import build
from test_r3_closure_service import synthetic_closure_context

ROOT = Path(__file__).resolve().parents[1]
CLIENT = 'skills/publish-approved-product/scripts/close_product_publication.py'


@pytest.mark.parametrize('relocated', [False, True])
@pytest.mark.parametrize('lost', [False, True])
def test_real_stdlib_client_owned_tcp_identity_and_record(tmp_path, monkeypatch, capsys, relocated, lost):
    _, data, provider, _, _, service, inputs = synthetic_closure_context(tmp_path, monkeypatch, 'PROCESSING')
    script = ROOT / CLIENT
    package = None
    if relocated:
        package = build(ROOT, tmp_path / 'bundle', write=True)
        script = tmp_path / 'bundle' / CLIENT
    spec = importlib.util.spec_from_file_location('owned_tcp_closure_client', script)
    client = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = client
    spec.loader.exec_module(client)
    events = []

    class Handler(server.Handler):
        def _json(self, code, payload):
            events.append({'method': self.command, 'path': self.path, 'status': code, 'payload': payload})
            if lost and self.path.endswith('/publication-closure/record') and code == 200:
                # The real service has persisted; only the response transport is lost.
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            return super()._json(code, payload)

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{httpd.server_port}'
    outputs = []

    def invoke(*args, root=ROOT):
        code = client.main(['--base-url', base, '--expected-root', str(root), *args])
        result = json.loads(capsys.readouterr().out)
        outputs.append({'exit_code': code, 'result': result})
        return code, result

    try:
        code, doctor = invoke('doctor')
        assert code == 0 and doctor['status'] == 'RUNTIME_IDENTIFIED'
        assert doctor['runtime']['business_authority'] == 'NOT_CHECKED'
        before = len(events)
        code, wrong = invoke('doctor', root=tmp_path)
        assert code == 2 and wrong['code'] == 'RUNTIME_ROOT_MISMATCH'
        assert [e['path'] for e in events[before:]] == ['/api/health']
        source = tmp_path / 'input.json'; source.write_text(json.dumps(inputs), encoding='utf-8')
        code, prepared = invoke('prepare', '--input', str(source))
        assert code == 0 and prepared['status'] == 'READY_TO_RECORD'
        path = tmp_path / 'prepared.json'; path.write_text(json.dumps(prepared), encoding='utf-8')
        code, recorded = invoke('record', '--prepared', str(path))
        assert code == (2 if lost else 0), recorded
        if lost:
            assert recorded['status'] == 'RECORD_OUTCOME_UNKNOWN'
            assert recorded['automatic_retry'] is False
            assert recorded['expected_closure_id'] == prepared['result']['closure']['closure_id']
            assert recorded['expected_closure_digest'] == prepared['result']['closure']['closure_digest']
        else:
            assert recorded['result']['closure'] == prepared['result']['closure']
        assert sum(e['path'].endswith('/publication-closure/record') for e in events) == 1
        code, latest = invoke('latest', '--offer-id', data['offer_id'], '--plan-id', data['plan_id'])
        assert code == 0 and latest['result']['closure'] == prepared['result']['closure']
        files = list(service.report_store.reports_root.rglob('closure-report.json'))
        assert len(files) == 1 and json.loads(files[0].read_text()) == latest['result']['closure']
        assert provider.mutations == 1
        assert all(e['path'] == '/api/health' or e['path'].startswith('/api/product-workspace/publication-closure/') for e in events)
    finally:
        httpd.shutdown(); worker.join(timeout=3); httpd.server_close()
    code, unavailable = invoke('doctor')
    assert code == 2 and unavailable['status'] == 'BLOCKED'
    (tmp_path / 'portable-tcp.json').write_text(json.dumps({'port': httpd.server_port,
        'relocated': relocated, 'lost_response': lost, 'client_sha256': hashlib.sha256(script.read_bytes()).hexdigest(),
        'package': package, 'requests': events, 'outputs': outputs}, indent=2), encoding='utf-8')
