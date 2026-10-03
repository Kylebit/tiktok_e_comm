"""Loopback HTTP surface for local candidate choices, without provider clients."""
import ipaddress
import json
from urllib.parse import parse_qs, urlparse
from shared_platform.publication_r2_review import decide, review_view, image_bytes, master_image_bytes, has_registration

PREFIX='/api/product-workspace/r2-candidate/'


def handle(handler, *, runtime_root, method):
    parsed=urlparse(handler.path)
    if not parsed.path.startswith(PREFIX):
        return False
    action=parsed.path[len(PREFIX):]
    if not ipaddress.ip_address(handler.client_address[0]).is_loopback:
        handler._json(403,{'ok':False,'error':'R2_REVIEW_LOOPBACK_REQUIRED'});return True
    if not handler._closure_local_host():
        return True
    try:
        if method=='GET' and action in {'review','image','master-image'}:
            query=parse_qs(parsed.query,keep_blank_values=True)
            required={'offer_id'} if action=='review' else {'offer_id','image_id','binding'}
            if set(query)!=required or any(len(v)!=1 for v in query.values()):
                raise ValueError('R2_REVIEW_QUERY_INVALID')
            offer=query['offer_id'][0]
            if action=='review':
                if not has_registration(offer,runtime_root=runtime_root):
                    handler._json(404,{'ok':False,'error':'R2_REVIEW_NOT_REGISTERED'});return True
                handler._json(200,{'ok':True,'review':review_view(offer,runtime_root=runtime_root)})
            else:
                reader=master_image_bytes if action=='master-image' else image_bytes
                raw=reader(offer,query['image_id'][0],query['binding'][0],runtime_root=runtime_root)
                handler.send_response(200);handler.send_header('Content-Type','image/png')
                handler.send_header('Cache-Control','no-store');handler.send_header('X-Content-Type-Options','nosniff')
                handler.send_header('Content-Length',str(len(raw)));handler.end_headers();handler.wfile.write(raw)
            return True
        if method!='POST' or action!='decision':
            handler._json(405,{'ok':False,'error':'R2_REVIEW_METHOD_INVALID'});return True
        headers=handler.headers
        port=int(handler.server.server_address[1])
        if (len(headers.get_all('Origin') or [])!=1 or headers.get('Origin') not in
            {f'http://127.0.0.1:{port}',f'http://localhost:{port}'}):
            handler._json(403,{'ok':False,'error':'R2_REVIEW_ORIGIN_REQUIRED'});return True
        if (parsed.query or headers.get('Transfer-Encoding') or
            len(headers.get_all('Content-Length') or [])!=1 or len(headers.get_all('Content-Type') or [])!=1 or
            headers.get_content_type()!='application/json'):
            raise ValueError('R2_REVIEW_FRAMING_INVALID')
        length=int(headers['Content-Length'])
        if not 0<length<=65536:
            raise ValueError('R2_REVIEW_BODY_LIMIT')
        raw=handler.rfile.read(length)
        if len(raw)!=length:
            raise ValueError('R2_REVIEW_BODY_INCOMPLETE')
        def unique(pairs):
            result={}
            for k,v in pairs:
                if k in result:raise ValueError('R2_REVIEW_DUPLICATE_FIELD')
                result[k]=v
            return result
        body=json.loads(raw.decode('utf-8'),object_pairs_hook=unique)
        handler._json(200,{'ok':True,'review':decide(body,runtime_root=runtime_root)})
    except (ValueError,KeyError,TypeError,UnicodeDecodeError):
        handler._json(409,{'ok':False,'error':'图片或审核版本已变化，请重新读取后保存。'})
    except OSError:
        handler._json(503,{'ok':False,'error':'本地审核记录暂时不可读，请保留选择并重试。'})
    return True
