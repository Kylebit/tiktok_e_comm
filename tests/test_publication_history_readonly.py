import hashlib
import sqlite3
import pytest
from test_product_publication_report_store import _report_payload
from shared_platform.product_publication_reports import ProductPublicationReportStore


def test_history_uses_explicit_report_pin_not_common_root_and_never_writes(tmp_path, monkeypatch):
    from shared_platform.publication_history import history
    common=tmp_path/'common.db';sqlite3.connect(common).close()
    database=tmp_path/'platform.db';assets=tmp_path/'assets'
    store=ProductPublicationReportStore(database,reports_root=assets)
    for index in range(13):
        store.store_report(_report_payload(report_id=f'publication-report:run-{index}',run_id=f'run-{index}'))
    from shared_platform.product_publication_runs import ProductPublicationRunStore
    with sqlite3.connect(database) as connection:
        ProductPublicationRunStore._ensure_schema(connection)
    monkeypatch.setenv('ORBIT_REPORT_STORE_PATH',str(database))
    monkeypatch.setenv('ORBIT_PUBLICATION_HISTORY_ROOT',str(assets))
    monkeypatch.setenv('ORBIT_RELEASE_EVIDENCE_ROOT',str(tmp_path/'wrong-common-root'))
    before=hashlib.sha256(database.read_bytes()).hexdigest()
    result=history('3838616043')
    assert result['history_status']=='AVAILABLE' and result['count']==13
    assert result['execution_authority'] is False
    assert all(item['revision']==31 for item in result['items'])
    assert hashlib.sha256(database.read_bytes()).hexdigest()==before
    assert not (tmp_path/'wrong-common-root').exists()


def test_history_missing_pin_fails_without_creating_database(tmp_path,monkeypatch):
    from shared_platform.publication_history import history
    missing=tmp_path/'missing.db'
    monkeypatch.setenv('ORBIT_REPORT_STORE_PATH',str(missing))
    monkeypatch.setenv('ORBIT_PUBLICATION_HISTORY_ROOT',str(tmp_path))
    result=history('3838616043')
    assert result['history_status']=='UNAVAILABLE' and result['execution_authority'] is False
    assert not missing.exists()


def test_history_integrity_failure_stays_blocked(tmp_path,monkeypatch):
    from shared_platform.publication_history import history
    database=tmp_path/'platform.db';assets=tmp_path/'assets'
    store=ProductPublicationReportStore(database,reports_root=assets)
    item=store.store_report(_report_payload())
    (assets/item.report_path).write_text('{}')
    monkeypatch.setenv('ORBIT_REPORT_STORE_PATH',str(database))
    monkeypatch.setenv('ORBIT_PUBLICATION_HISTORY_ROOT',str(assets))
    result=history('3838616043')
    assert result['history_status']=='INTEGRITY_BLOCKED'
    assert result['items']==[] and result['execution_authority'] is False
    store.store_report(_report_payload(report_id='publication-report:valid',run_id='valid'))
    result=history('3838616043')
    assert result['history_status']=='PARTIALLY_BLOCKED'
    assert result['count']==1 and result['blocked_count']==1
    assert result['items'][0]['report_id']=='publication-report:valid'


def test_history_rejects_relative_paths(tmp_path,monkeypatch):
    from shared_platform.publication_history import history
    monkeypatch.setenv('ORBIT_REPORT_STORE_PATH','relative.db')
    monkeypatch.setenv('ORBIT_PUBLICATION_HISTORY_ROOT',str(tmp_path))
    assert history('3838616043')['history_status']=='UNAVAILABLE'


def test_history_rejects_offer_traversal():
    from shared_platform.publication_history import history
    with pytest.raises(ValueError):history('../3838616043')


def test_history_report_path_cannot_escape_asset_root(tmp_path,monkeypatch):
    from shared_platform.publication_history import history
    database=tmp_path/'platform.db';assets=tmp_path/'assets'
    store=ProductPublicationReportStore(database,reports_root=assets)
    store.store_report(_report_payload())
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE product_publication_reports SET report_path='../outside.json'")
    monkeypatch.setenv('ORBIT_REPORT_STORE_PATH',str(database))
    monkeypatch.setenv('ORBIT_PUBLICATION_HISTORY_ROOT',str(assets))
    assert history('3838616043')['history_status']=='INTEGRITY_BLOCKED'


def test_maintenance_history_http_does_not_delegate_to_business_handler(tmp_path,monkeypatch):
    from http.client import HTTPConnection
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import json,threading
    from shared_platform.operations_launch import maintenance_handler
    monkeypatch.setenv('ORBIT_REPORT_STORE_PATH',str(tmp_path/'missing.db'))
    monkeypatch.setenv('ORBIT_PUBLICATION_HISTORY_ROOT',str(tmp_path))
    root=tmp_path/'existing-evidence';folder=root/'data/new_product_workbench';folder.mkdir(parents=True)
    (folder/'3838616043.json').write_text(json.dumps({'offer_id':'3838616043','_revision':31,'review':{'title':'existing product','seller_sku':'0988'}}))
    monkeypatch.setenv('ORBIT_R2_REVIEW_RUNTIME_ROOT',str(root))
    class Base(BaseHTTPRequestHandler):
        def do_GET(self):raise AssertionError('business handler called')
        def _json(self,status,body):
            encoded=json.dumps(body).encode();self.send_response(status);self.end_headers();self.wfile.write(encoded)
    server=ThreadingHTTPServer(('127.0.0.1',0),maintenance_handler(Base))
    thread=threading.Thread(target=server.serve_forever);thread.start()
    try:
        client=HTTPConnection('127.0.0.1',server.server_port)
        client.request('GET','/api/product-workspace/publication-history?offer_id=3838616043')
        response=client.getresponse();data=json.loads(response.read());client.close()
        assert response.status==200 and data['history_status']=='UNAVAILABLE'
        assert data['execution_authority'] is False and not (tmp_path/'missing.db').exists()
        client=HTTPConnection('127.0.0.1',server.server_port)
        client.request('GET','/api/product-workspace/history')
        response=client.getresponse();data=json.loads(response.read());client.close()
        assert response.status==200 and data['count']==1
        assert data['items'][0]['offer_id']=='3838616043'
        assert data['execution_authority'] is False
    finally:server.shutdown();server.server_close();thread.join()
