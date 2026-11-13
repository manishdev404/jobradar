"""HTTP for every reader: the job lists (sources.py, boards_more.py) and the
single postings (details/).

One pooled session per thread: a scan reads hundreds of boards on the same
few hosts (boards-api.greenhouse.io, api.lever.co, *.myworkdayjobs.com), and a
kept-alive connection skips a TLS handshake per request. Plus what every
reader needs: a retry when a host drops a connection or is briefly busy, and
page text decoded in the page's own charset.
"""
from __future__ import annotations

import re
import threading
import time

import requests
from requests.adapters import HTTPAdapter

TIMEOUT = 25
UA = {"User-Agent": "jobradar/0.1 (personal job tracker)"}
# Indian HR suites and many careers pages serve a stub to non-browser clients.
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
JSON = {**BROWSER, "Accept": "application/json"}

# A busy or briefly unavailable host is worth one more try; a 404 or 403 is an answer.
_RETRY_STATUS = {429, 502, 503, 504}
_local = threading.local()


def session() -> requests.Session:
    """This thread's session: requests.Session is not documented as thread
    safe, and one per thread still reuses connections within a scan."""
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        adapter = HTTPAdapter(pool_connections=32, pool_maxsize=8)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _local.session = s
    return s


def set_timeout(seconds: float) -> None:
    """This thread's request timeout. The scan's fast queue gives a board a few
    seconds and no second try - a board that needs more goes to the slow
    queue, whose threads keep the full TIMEOUT and its retry."""
    _local.timeout = float(seconds)


def _timeout() -> float:
    return getattr(_local, "timeout", TIMEOUT)


def _retry_after(r: requests.Response) -> float:
    try:
        return min(max(float(r.headers.get("retry-after", 3)), 1.0), 10.0)
    except ValueError:
        return 3.0


def request(method: str, url: str, **kw) -> requests.Response:
    """One retry on a dropped connection, a timeout or a busy host (429/5xx).
    Hosts like Workday drop the odd connection under a burst of parallel
    requests; a single retry after a pause turns most of those into a normal
    fetch instead of a missing company."""
    kw.setdefault("timeout", _timeout())
    for attempt in (0, 1):
        try:
            r = session().request(method, url, **kw)
        except requests.Timeout:
            if attempt or _timeout() < TIMEOUT:     # the fast queue hands it on instead
                raise
            time.sleep(3)
            continue
        except requests.ConnectionError:
            if attempt:
                raise
            time.sleep(3)
            continue
        if r.status_code in _RETRY_STATUS and not attempt:
            wait = _retry_after(r)
            r.close()
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise AssertionError("unreachable")


def json(url: str, headers: dict | None = None, **kw):
    return request("GET", url, headers=headers or JSON, **kw).json()


def post_json(url: str, body: dict, headers: dict | None = None):
    return request("POST", url, json=body,
                   headers={**(headers or JSON), "Content-Type": "application/json"}).json()


_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)


def decode(body: bytes, content_type: str = "") -> str:
    """Page bytes as text, in the charset the server or the page names. A
    text/html response without one would otherwise decode as Latin-1 (the
    HTTP default) and turn a UTF-8 page's quotes into mojibake."""
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    charset = m.group(1) if m else None
    if not charset:
        m = _META_CHARSET.search(body[:4096])
        charset = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        return body.decode(charset, "replace")
    except LookupError:              # a charset name Python does not know
        return body.decode("utf-8", "replace")


def page(url: str, limit: int = 1_500_000, headers: dict | None = None) -> tuple[str, str]:
    """(final url, html) - capped, since some careers pages are huge."""
    r = request("GET", url, headers=headers or BROWSER, stream=True)
    body = bytearray()
    for chunk in r.iter_content(65536):
        body += chunk
        if len(body) >= limit:
            break
    r.close()
    return r.url, decode(bytes(body), r.headers.get("content-type", ""))
