"""Bounded reads of existing catalog image URLs, with pinned public DNS targets."""
from pathlib import Path
import http.client
import ipaddress
import io
import json
import socket
import ssl
import threading
import time
import warnings
from urllib.parse import urlsplit,parse_qsl
from shared_platform.catalog_cost_projection import digest

_network=threading.local()
MAX_BYTES=8*1024*1024
MAX_CACHE_BYTES=256*1024*1024
READ_DEADLINE_SECONDS=8.0
_dns_slots=threading.BoundedSemaphore(4)


def _remaining(deadline):
    remaining=deadline-time.monotonic()
    if remaining<=0:raise TimeoutError('image_read_deadline')
    return remaining


def _resolve(host,deadline):
    # getaddrinfo cannot be cancelled portably. At most four daemon resolvers
    # may remain in the OS; waiting callers still obey their single deadline.
    if not _dns_slots.acquire(timeout=_remaining(deadline)):raise TimeoutError('image_read_deadline')
    done=threading.Event();result={}
    def resolve():
        _network.host=host
        try:result['addresses']=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        except BaseException as error:result['error']=error
        finally:_network.host=None;_dns_slots.release();done.set()
    try:threading.Thread(target=resolve,name='catalog-image-dns',daemon=True).start()
    except BaseException:_dns_slots.release();raise
    if not done.wait(_remaining(deadline)):raise TimeoutError('image_read_deadline')
    _remaining(deadline)
    if 'error' in result:raise result['error']
    return result['addresses']


class _DeadlineReader(io.RawIOBase):
    def __init__(self,stream,sock,deadline):
        self.stream=stream;self.sock=sock;self.deadline=deadline
    def readable(self):return True
    def readinto(self,buffer):
        self.sock.settimeout(min(4.0,_remaining(self.deadline)))
        count=self.stream.readinto(buffer)
        _remaining(self.deadline)
        return count
    def close(self):
        try:self.stream.close()
        finally:super().close()


class _DeadlineSocket:
    def __init__(self,sock,deadline):self.sock=sock;self.deadline=deadline
    def sendall(self,data):
        self.sock.settimeout(min(4.0,_remaining(self.deadline)))
        self.sock.sendall(data)
        _remaining(self.deadline)
    def makefile(self,mode):
        if mode!='rb':raise ValueError('image_response_mode')
        # Keep the real socket file reference: HTTPConnection may close its
        # socket after Connection: close headers, before the body is consumed.
        raw=self.sock.makefile('rb',buffering=0)
        return io.BufferedReader(_DeadlineReader(raw,self.sock,self.deadline))
    def close(self):self.sock.close()


def permits_network(event,values):
    if event=='socket.getaddrinfo':return getattr(_network,'host',None)==values[0] and values[1]==443
    if event=='socket.connect':return values[1][:2]==(getattr(_network,'ip',None),443)
    return False


class ImageCache:
    def __init__(self,conn,directory):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        urls=[r[0] for r in conn.execute("SELECT image_url FROM products UNION SELECT image_url FROM shopee_products") if r[0]]
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_ozon_products'").fetchone():
            for r in conn.execute('SELECT document_json FROM catalog_ozon_products'):urls.extend(u for u in json.loads(r[0]).get('images',[]) if isinstance(u,str))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_archive_members'").fetchone():
            for r in conn.execute('SELECT document_json FROM catalog_archive_members'):
                url=json.loads(r[0]).get('image_url')
                if isinstance(url,str) and url:urls.append(url)
        self.urls={digest(url):url for url in urls};self.lock=threading.Lock();self.limit=threading.BoundedSemaphore(4);self.failures={}
        self.locks={}
        self.cache_bytes=sum(p.stat().st_size for p in self.directory.glob('*.bin'))

    def get(self,key):
        if key not in self.urls:raise ValueError('image_not_registered')
        deadline=time.monotonic()+READ_DEADLINE_SECONDS
        with self.lock:lock=self.locks.setdefault(key,threading.Lock())
        if not lock.acquire(timeout=_remaining(deadline)):raise TimeoutError('image_read_deadline')
        try:
            path=self.directory/(key+'.bin');meta=self.directory/(key+'.json')
            if path.is_file() and meta.is_file():return path.read_bytes(),json.loads(meta.read_text())['mime']
            if time.monotonic()-self.failures.get(key,-1000)<60:raise ValueError('image_unavailable_recently')
            try:
                if not self.limit.acquire(timeout=_remaining(deadline)):raise TimeoutError('image_read_deadline')
                try:body,mime=self._read(self.urls[key],deadline=deadline)
                finally:self.limit.release()
                with self.lock:
                    if self.cache_bytes+len(body)<=MAX_CACHE_BYTES:
                        path.write_bytes(body);meta.write_text(json.dumps({'mime':mime,'registered_image_key':key}));self.cache_bytes+=len(body)
                return body,mime
            except Exception:
                self.failures[key]=time.monotonic();raise
        finally:lock.release()

    def _public_source(self,url,deadline):
        if not isinstance(url,str) or url!=url.strip() or len(url)>4096 or any(ord(c)<32 or ord(c)==127 for c in url):raise ValueError('image_url_invalid')
        parsed=urlsplit(url);host=parsed.hostname
        if parsed.scheme!='https' or not host or parsed.port not in (None,443) or parsed.username or parsed.password or parsed.fragment:raise ValueError('image_url_not_public_https')
        # Original CDN image signatures remain at their original host. Never
        # accept platform/account credentials or a caller-supplied target URL.
        if any(any(word in k.casefold() for word in ('token','secret','password','api_key','authorization')) for k,v in parse_qsl(parsed.query)):raise ValueError('image_credential_query_denied')
        addresses=_resolve(host,deadline)
        ips=list(dict.fromkeys(r[4][0] for r in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):raise ValueError('image_non_public_address')
        return parsed,ips

    def _read(self,url,deadline=None):
        if deadline is None:deadline=time.monotonic()+READ_DEADLINE_SECONDS
        parsed,ips=self._public_source(url,deadline)
        # Try at most two distinct, already validated public IPs. Certificate
        # failures, redirects, denied types and content errors never trigger a
        # different transport policy. All attempts share one absolute deadline.
        for index,target in enumerate(ips[:2]):
            _remaining(deadline)
            try:return self._read_at(parsed,target,deadline)
            except ssl.SSLCertVerificationError:raise
            except (TimeoutError,ConnectionError,ssl.SSLEOFError):
                if index+1>=min(2,len(ips)):raise

    def _read_at(self,parsed,target,deadline):
        host=parsed.hostname
        class Pinned(http.client.HTTPSConnection):
            def connect(connection):
                family=socket.AF_INET6 if ':' in target else socket.AF_INET
                raw=socket.socket(family,socket.SOCK_STREAM);secured=None
                try:
                    raw.settimeout(min(4.0,_remaining(deadline)))
                    _network.ip=target;raw.connect((target,443))
                    raw.settimeout(min(4.0,_remaining(deadline)))
                    secured=connection._context.wrap_socket(raw,server_hostname=host)
                    _remaining(deadline)
                    connection.sock=_DeadlineSocket(secured,deadline)
                except BaseException:(secured or raw).close();raise
                finally:_network.ip=None
        connection=Pinned(host,443,timeout=min(4.0,_remaining(deadline)),context=ssl.create_default_context())
        response=None
        try:
            connection.request('GET',(parsed.path or '/')+('?' + parsed.query if parsed.query else ''),headers={'Accept':'image/jpeg,image/png,image/webp,image/avif','User-Agent':'OrbitCatalogImage/1.0'})
            response=connection.getresponse()
            if response.status!=200:raise ValueError('image_http_'+str(response.status))
            mime=response.getheader('Content-Type','').split(';')[0].lower()
            if mime not in {'image/jpeg','image/png','image/webp','image/avif'}:raise ValueError('image_type_denied')
            if int(response.getheader('Content-Length','0'))>MAX_BYTES:raise ValueError('image_too_large')
            body=response.read(MAX_BYTES+1)
            _remaining(deadline)
            if len(body)>MAX_BYTES:raise ValueError('image_too_large')
        finally:
            if response is not None:getattr(response,'close',lambda:None)()
            connection.close()
        from PIL import Image
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(body)) as image:
                if image.width*image.height>40000000:raise ValueError('image_dimensions_denied')
                formats={'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp','AVIF':'image/avif'}
                if image.format not in formats:raise ValueError('image_format_denied')
                mime=formats[image.format]
                image.verify()
        _remaining(deadline)
        return body,mime
