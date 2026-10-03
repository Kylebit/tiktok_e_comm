"""One-hop provider transport: never forward credentials or replay work on redirect."""
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class NoProviderRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, 'Provider redirects are disabled', headers, fp)


def open_request(req, *, timeout):
    # An explicit empty proxy map also avoids environment/registry proxy discovery.
    return build_opener(ProxyHandler({}), NoProviderRedirect()).open(req, timeout=timeout)
