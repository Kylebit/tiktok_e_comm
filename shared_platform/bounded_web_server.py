"""Bound local web connections without queueing business handlers.

Read-only pages can create many short asset connections. Saturation or a
request-thread allocation failure must not turn the listener into a reset or
an unbounded thread factory. Rejected sockets never reach the handler.
"""
import socket
import threading
from http.server import ThreadingHTTPServer


_BUSY_BODY = (
    '{"ok":false,"code":"SERVICE_BUSY",'
    '"error":"工作台暂时繁忙，请稍后重试。"}'
).encode("utf-8")
_BUSY_RESPONSE = (
    b"HTTP/1.0 503 Service Unavailable\r\n"
    b"Content-Type: application/json; charset=utf-8\r\n"
    b"Retry-After: 1\r\nConnection: close\r\n"
    + b"Content-Length: " + str(len(_BUSY_BODY)).encode("ascii")
    + b"\r\n\r\n" + _BUSY_BODY
)


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """At most eight active connections; excess clients receive terminal503."""

    def __init__(self, address, handler, *, max_active_connections=8,
                 connection_timeout=5.0):
        if type(max_active_connections) is not int or not 1 <= max_active_connections <= 32:
            raise ValueError("max_active_connections must be an integer from1 to32")
        if (type(connection_timeout) not in (int, float)
                or not 0 < connection_timeout <= 30):
            raise ValueError("connection_timeout must be positive and at most30 seconds")
        self._active_connections = threading.BoundedSemaphore(max_active_connections)
        self.connection_timeout = float(connection_timeout)
        super().__init__(address, handler)

    def _reject_busy(self, request):
        # Small declared-free drain avoids closing a Windows socket with a
        # complete short GET still unread. It never parses/delegates a body.
        # Both drain and send are bounded; a broken peer cannot stop the listener.
        try:
            request.settimeout(0.1)
            try:
                request.recv(4096)
            except OSError:
                pass
            request.sendall(_BUSY_RESPONSE)
            try:
                request.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        except OSError:
            pass
        finally:
            self.shutdown_request(request)

    def process_request(self, request, client_address):
        if not self._active_connections.acquire(blocking=False):
            self._reject_busy(request)
            return
        try:
            request.settimeout(self.connection_timeout)
            super().process_request(request, client_address)
        except (RuntimeError, OSError, MemoryError):
            # Thread.start resource failure occurs before its target runs, so
            # this path owns the one release. A started target releases below.
            self._active_connections.release()
            self._reject_busy(request)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._active_connections.release()
