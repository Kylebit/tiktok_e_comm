"""Retained supply dispatch through the newly composed Handler prefix guards."""
import http.client,threading
from http.server import ThreadingHTTPServer
from modules.products import server

def test_supply_legacy_and_missing_capture_dispatch_remain_distinct(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'ROOT',tmp_path)
    supply=tmp_path/'domains/supply_chain_operations/dashboard';supply.mkdir(parents=True)
    raw=b'<html><body>SYNTHETIC RETAINED SUPPLY</body></html>'
    (supply/'index.html').write_bytes(raw)
    httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    def get():
        c=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=5)
        try:c.request('GET','/supply-chain/');r=c.getresponse();return r.status,r.read()
        finally:c.close()
    try:
        assert get()==(200,raw)
        httpd.supply_chain_capture={'artifact_root':'outputs/absent','output_root':'applied'}
        status,body=get();assert status==503 and raw not in body
        httpd.supply_chain_capture=None;assert get()==(200,raw)
    finally:httpd.shutdown();httpd.server_close();thread.join(5)
