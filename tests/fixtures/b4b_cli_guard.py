"""Run the B4B CLI contract against an ephemeral loopback test server only."""

import ipaddress
import runpy
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse


root = Path(__file__).resolve().parents[2]
allowed_scripts = {
    (root / 'scripts/product_publication_workflow.py').resolve(),
    (root / 'skills/publish-approved-product/scripts/prepare_publication_execution.py').resolve(),
}
if len(sys.argv) < 4:
    raise SystemExit('B4B CLI guard requires script and --base-url')
script = Path(sys.argv[1]).resolve()
if script not in allowed_scripts or sys.argv[2] != '--base-url':
    raise SystemExit('B4B CLI guard rejected script or arguments')
url = urlparse(sys.argv[3])
if url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port or url.path not in ('', '/'):
    raise SystemExit('B4B CLI guard requires an ephemeral loopback server')

original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex


def loopback_only(address):
    if not isinstance(address, tuple) or not ipaddress.ip_address(address[0]).is_loopback:
        raise OSError('B4B CLI guard denied non-loopback socket')


def guarded_connect(self, address):
    loopback_only(address)
    return original_connect(self, address)


def guarded_connect_ex(self, address):
    loopback_only(address)
    return original_connect_ex(self, address)


socket.socket.connect = guarded_connect
socket.socket.connect_ex = guarded_connect_ex
sys.argv = [str(script), *sys.argv[2:]]
sys.path.insert(0, str(root))
runpy.run_path(str(script), run_name='__main__')
