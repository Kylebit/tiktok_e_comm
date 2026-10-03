"""Render a selected frozen report in an isolated frame; no reads or writes."""
import json
import re
from urllib.parse import parse_qs
from .render import render_profit_report_html

VIEW_BODY_LIMIT = 16 * 1024 * 1024

def handle_report_view(handler):
    port=handler.server.server_address[1];hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
    if handler.headers.get('Host') not in hosts or handler.headers.get('Origin') not in {'http://'+h for h in hosts}:
        return handler._json(403,{'error':{'code':'invalid_local_origin'}})
    try:
        sizes=handler.headers.get_all('Content-Length') or []
        size=int(sizes[0]) if len(sizes)==1 else -1
        if size<0 or size>VIEW_BODY_LIMIT or handler.headers.get('Transfer-Encoding'):raise ValueError()
        if handler.headers.get('Content-Type','').split(';')[0]!='application/x-www-form-urlencoded':raise ValueError()
        form=parse_qs(handler.rfile.read(size).decode('utf-8'),strict_parsing=True,max_num_fields=1)
        if set(form)!={'report'} or len(form['report'])!=1:raise ValueError()
        report=json.loads(form['report'][0])
        if not isinstance(report,dict) or not report.get('report_id') or not isinstance(report.get('order_lines'),list):raise ValueError()
        body=render_profit_report_html(_isolated_report(report)).encode('utf-8')
    except (ValueError,KeyError,TypeError,AttributeError,ArithmeticError):return handler._json(400,{'error':{'code':'invalid_report_view'}})
    handler.send_response(200);handler.send_header('Content-Type','text/html; charset=utf-8')
    handler.send_header('Content-Length',str(len(body)));handler.send_header('Cache-Control','no-store')
    handler.send_header('X-Content-Type-Options','nosniff')
    handler.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'; sandbox allow-scripts")
    handler.end_headers();handler.wfile.write(body)


def _isolated_report(report):
    """Retain report facts while preventing remote image requests in the iframe."""
    copied=json.loads(json.dumps(report,ensure_ascii=False))
    for line in copied.get('order_lines',[]):
        if not isinstance(line,dict) or not isinstance(line.get('product'),dict):continue
        raw=str(line['product'].get('image_url') or '').strip()
        if not re.fullmatch(r'data:image/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+',raw,re.IGNORECASE):
            line['product']['image_url']=''
    return copied
