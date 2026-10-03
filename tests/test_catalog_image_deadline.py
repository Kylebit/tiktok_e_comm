"""Offline slow HTTP streams exercise the real HTTP parser and image validator."""
import io
import socket
import sqlite3
import ssl

import pytest
from PIL import Image
from shared_platform.catalog_images import ImageCache
from shared_platform.catalog_cost_projection import digest
from test_catalog_cost_projection import catalog, catalog_http


URL='https://example.com/image.png'


def cache_for(tmp_path, url=URL):
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE products(image_url)')
    conn.execute('CREATE TABLE shopee_products(image_url)')
    conn.execute('INSERT INTO products VALUES(?)',(url,))
    result=ImageCache(conn,tmp_path/'images');conn.close();return result


class Clock:
    now=100.0
    def monotonic(self):return self.now
    def advance(self,delay,timeout):
        if delay>timeout:
            self.now+=timeout;raise TimeoutError('synthetic socket timeout')
        self.now+=delay


def wire(monkeypatch, *, stage='normal', addresses=('8.8.8.8','1.1.1.1'), status=200, mime='image/png', payload=None):
    output=io.BytesIO();Image.new('RGB',(2,3),'blue').save(output,format='PNG');png=output.getvalue()
    if payload is not None:png=payload
    encoded=png
    framing='Content-Length: %s'%len(png)
    if stage=='chunked':
        framing='Transfer-Encoding: chunked';encoded=b''.join(b'1\r\n'+bytes([value])+b'\r\n' for value in png)+b'0\r\n\r\n'
    header=('HTTP/1.1 %s Test\r\nContent-Type: %s\r\n%s\r\nConnection: close\r\n\r\n'%(status,mime,framing)).encode()
    clock=Clock();attempts=[];tls=[];sockets=[]
    monkeypatch.setattr('shared_platform.catalog_images.time.monotonic',clock.monotonic)
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443)) for ip in addresses])
    class Stream(io.RawIOBase):
        def __init__(self,owner):self.owner=owner;self.position=0
        def readable(self):return True
        def readinto(self,buffer):
            owner=self.owner
            if stage=='two_ip_budget' and owner.address=='8.8.8.8':
                clock.advance(3,owner.timeout);raise TimeoutError('first header timeout')
            body=self.position>=len(header)
            delay=.5 if stage=='header' and not body else (1 if stage in ('body','chunked') and body else 0)
            clock.advance(delay,owner.timeout)
            amount=1 if delay else (len(header)-self.position if not body else len(encoded))
            chunk=(header+encoded)[self.position:self.position+min(len(buffer),amount)]
            buffer[:len(chunk)]=chunk;self.position+=len(chunk);return len(chunk)
    class Raw:
        def __init__(self):self.timeout=4;self.address=None;self.closed=False;self.stream=None;sockets.append(self)
        def settimeout(self,timeout):self.timeout=timeout
        def connect(self,address):
            self.address=address[0];attempts.append(address)
            if stage=='first_timeout' and self.address=='8.8.8.8':
                clock.advance(4,self.timeout);raise TimeoutError('first IP timeout')
            if stage=='two_ip_budget':clock.advance(3,self.timeout)
        def sendall(self,body):
            assert body.startswith(b'GET /image.png ')
            if stage=='send':clock.advance(5,self.timeout)
        def makefile(self,mode,buffering=-1):
            raw=Stream(self)
            self.stream=raw
            return raw if buffering==0 else io.BufferedReader(raw)
        def close(self):self.closed=True
    class Context:
        def wrap_socket(self,raw,*,server_hostname):
            tls.append(server_hostname)
            if stage=='certificate':raise ssl.SSLCertVerificationError('synthetic cert failure')
            if stage=='tls':clock.advance(5,raw.timeout)
            return raw
    monkeypatch.setattr(socket,'socket',lambda *a,**k:Raw())
    monkeypatch.setattr(ssl,'create_default_context',lambda:Context())
    return clock,attempts,tls,sockets,png


@pytest.mark.parametrize('stage',['header','body','chunked','two_ip_budget','tls','send'])
def test_shared_deadline_stops_trickled_headers_body_and_second_ip(tmp_path,monkeypatch,stage):
    cache=cache_for(tmp_path);clock,attempts,_,sockets,_=wire(monkeypatch,stage=stage)
    with pytest.raises(TimeoutError):cache.get(digest(URL))
    assert clock.now-100<=8.000001
    assert len(attempts)<=2 and all(raw.closed for raw in sockets)
    assert all(raw.stream is None or raw.stream.closed for raw in sockets)
    assert list(cache.directory.iterdir())==[]


def test_dns_wait_is_bounded_and_inflight_resolvers_cannot_grow_unbounded(tmp_path,monkeypatch):
    import threading,time
    from concurrent.futures import ThreadPoolExecutor
    import shared_platform.catalog_images as images
    unblock=threading.Event();calls=[]
    def resolve(*a,**k):
        calls.append(a[0]);unblock.wait(2)
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))]
    monkeypatch.setattr(socket,'getaddrinfo',resolve)
    monkeypatch.setattr(images,'READ_DEADLINE_SECONDS',.05)
    caches=[cache_for(tmp_path/str(i)) for i in range(5)]
    started=time.monotonic()
    def read(cache):
        with pytest.raises(TimeoutError):cache.get(digest(URL))
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(read,caches))
        assert len(calls)<=4 and time.monotonic()-started<1
    finally:
        unblock.set()
        # Restore the module's bounded resolver capacity before monkeypatch undo.
        for _ in range(4):assert images._dns_slots.acquire(timeout=1)
        for _ in range(4):images._dns_slots.release()


def test_first_public_ip_timeout_second_ip_verifies_and_caches(tmp_path,monkeypatch):
    cache=cache_for(tmp_path);clock,attempts,tls,_,png=wire(monkeypatch,stage='first_timeout')
    assert cache.get(digest(URL))==(png,'image/png')
    assert attempts==[('8.8.8.8',443),('1.1.1.1',443)] and tls==['example.com']
    assert cache.get(digest(URL))==(png,'image/png') and len(attempts)==2
    assert clock.now-100<=8


@pytest.mark.parametrize('options,match',[
    ({'addresses':('8.8.8.8','127.0.0.1')},'non_public'),
    ({'addresses':('127.0.0.1',)},'non_public'),
    ({'status':302},'http_302'),
    ({'mime':'text/html'},'type_denied'),
])
def test_no_address_retry_weakens_public_address_redirect_or_mime_checks(tmp_path,monkeypatch,options,match):
    cache=cache_for(tmp_path);_,attempts,_,_,_=wire(monkeypatch,**options)
    with pytest.raises(ValueError,match=match):cache.get(digest(URL))
    assert len(attempts)==(0 if 'addresses' in options else 1)
    assert list(cache.directory.iterdir())==[]


def test_tls_certificate_failure_never_retries_other_ip(tmp_path,monkeypatch):
    cache=cache_for(tmp_path);_,attempts,_,sockets,_=wire(monkeypatch,stage='certificate')
    with pytest.raises(ssl.SSLCertVerificationError):cache.get(digest(URL))
    assert len(attempts)==1 and all(raw.closed for raw in sockets)


@pytest.mark.parametrize('payload',[b'not-an-image','large-dimensions'])
def test_real_pil_verification_and_dimension_limit_remain_terminal(tmp_path,monkeypatch,payload):
    if payload=='large-dimensions':
        import struct,zlib
        image=io.BytesIO();Image.new('RGB',(1,1)).save(image,format='PNG');original=image.getvalue()
        ihdr=struct.pack('>II',10000,5000)+original[24:29]
        payload=original[:16]+ihdr+struct.pack('>I',zlib.crc32(b'IHDR'+ihdr)&0xffffffff)+original[33:]
    cache=cache_for(tmp_path);_,attempts,_,_,_=wire(monkeypatch,payload=payload)
    from PIL import UnidentifiedImageError
    with pytest.raises((ValueError,UnidentifiedImageError)):cache.get(digest(URL))
    assert len(attempts)==1 and list(cache.directory.iterdir())==[]


def test_removed_browser_source_endpoint_is_404_and_does_not_read_image(catalog,catalog_http):
    request,http=catalog_http
    class NoRead:
        def get(self,*a):pytest.fail('unexpected image fetch')
    http.catalog_image_cache=NoRead()
    before=catalog.read_bytes()
    status,_,_=request('GET','/api/catalog/image-source?key='+digest(URL))
    assert status==404 and catalog.read_bytes()==before


def test_deadline_reader_retains_real_socket_file_after_connection_close():
    import http.client,time
    from shared_platform.catalog_images import _DeadlineSocket
    local,peer=socket.socketpair()
    try:
        peer.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\ndata')
        wrapped=_DeadlineSocket(local,time.monotonic()+8)
        response=http.client.HTTPResponse(wrapped);response.begin()
        wrapped.close()  # Mirrors HTTPConnection.getresponse() for will_close.
        assert response.read()==b'data'
        response.close()
    finally:local.close();peer.close()


@pytest.mark.parametrize('url',['http://example.com/a','https://u:p@example.com/a','https://example.com/a?access_token=CANARY'])
def test_credential_and_non_https_sources_fail_before_dns(tmp_path,monkeypatch,url):
    cache=cache_for(tmp_path,url)
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:pytest.fail('DNS for invalid source'))
    with pytest.raises(ValueError):cache.get(digest(url))


def test_real_chromium_stays_on_registered_proxy(tmp_path):
    import os,subprocess
    from pathlib import Path
    from test_release_ux_contract import _browser_runtime
    runtime=_browser_runtime()
    if runtime is None:
        if os.environ.get('ORBIT_REQUIRE_BROWSER_TESTS')=='1':pytest.fail('Browser runtime required')
        pytest.skip('Browser runtime unavailable')
    node,modules=runtime;root=Path(__file__).resolve().parents[1]
    result=subprocess.run([str(node),str(root/'tests/browser/catalog_image_routing.cjs')],cwd=root,env=dict(os.environ,NODE_PATH=str(modules)),capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
