"""The only code that touches the network, and the link check built on it.

Callers opt in per call with ``network=True`` and a ``Fetcher`` carrying their own
user agent (with their contact address, from their own config). Only http and https
are fetched, redirects included, and every request to a host, redirect hops too,
waits ``min_interval`` seconds after the last one to that host.
"""
from __future__ import annotations

import http.client
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote, urlsplit, urlunsplit

MAX_BYTES = 8 * 1024 * 1024
BLOCKED_CODES = frozenset({401, 402, 403, 429, 451})
GONE_CODES = frozenset({404, 410})
_PATH_SAFE = "/%:@!$&'()*+,;=-._~"
_QUERY_SAFE = _PATH_SAFE + "?"


class ConfigError(Exception):
    """Something the caller must supply is missing: a fetcher, a user agent."""


class FetchError(Exception):
    """No complete HTTP answer: DNS, refused, timeout, TLS, truncated or oversized body."""


@dataclass(frozen=True)
class Response:
    url: str            # final URL, after redirects
    code: int
    content_type: str
    charset: str | None
    body: bytes


def _require_http(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"only http(s) URLs are fetched, got {url!r}")


def to_uri(url: str) -> str:
    """An http(s) URL as ASCII: IDNA host, percent-encoded path and query.

    Existing %-escapes are kept, so an already-encoded URL comes back unchanged.
    """
    _require_http(url)
    p = urlsplit(url)
    netloc = p.netloc
    if not netloc.isascii():
        if "@" in netloc:
            raise ValueError(f"non-ASCII URL with credentials is not fetched: {url!r}")
        try:
            netloc = (p.hostname or "").encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError(f"host is not a valid domain name: {url!r}") from exc
        netloc += f":{p.port}" if p.port else ""
    return urlunsplit((p.scheme, netloc, quote(p.path, safe=_PATH_SAFE),
                       quote(p.query, safe=_QUERY_SAFE), ""))


class _HttpOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """urllib also follows redirects to ftp://; a caller's URL must stay on http(s)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _require_http(newurl)
        return super().redirect_request(req, fp, code, msg, headers, to_uri(newurl))


class _Spacing(urllib.request.BaseHandler):
    """Runs before every request this opener sends, redirect hops included."""

    def __init__(self, wait):
        self.wait = wait

    def http_request(self, req):
        self.wait(urlsplit(req.full_url).hostname or "")
        return req

    https_request = http_request


class Fetcher:
    """Fetches politely for the checks. One per run; it remembers when it last hit each host.

    It has no public fetch method: requests go out only through a check called with
    ``network=True``.
    """

    def __init__(self, user_agent: str, *, timeout: float = 20, min_interval: float = 1.0):
        if not user_agent or not user_agent.strip():
            raise ConfigError("a user agent is required for network checks")
        self.user_agent = user_agent
        self.timeout = timeout
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._opener = urllib.request.build_opener(_HttpOnlyRedirects, _Spacing(self._wait))

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self.min_interval - (time.monotonic() - last)
            if gap > 0:
                time.sleep(gap)
        self._last[host] = time.monotonic()

    def _get(self, url: str, *, read_body: bool = True) -> Response:
        """The HTTP answer, error codes included. Raises FetchError without a whole one."""
        req = urllib.request.Request(to_uri(url), headers={"User-Agent": self.user_agent})
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                body = resp.read(MAX_BYTES + 1) if read_body else b""
                if len(body) > MAX_BYTES:
                    raise FetchError(f"{url}: body is over the {MAX_BYTES}-byte limit")
                declared = resp.headers.get("Content-Length", "")
                if read_body and declared.isdigit() and len(body) < int(declared):
                    raise FetchError(f"{url}: body ended at {len(body)} of {declared} bytes")
                return Response(resp.geturl(), resp.status, resp.headers.get_content_type(),
                                resp.headers.get_content_charset(), body)
        except urllib.error.HTTPError as exc:
            with exc:
                return Response(exc.geturl() or url, exc.code, exc.headers.get_content_type(),
                                exc.headers.get_content_charset(), b"")
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            raise FetchError(f"{url}: {type(exc).__name__}: {exc}") from exc


class LinkStatus(str, Enum):
    LIVE = "live"                # 2xx after redirects
    BLOCKED = "blocked"          # 401, 402, 403, 429, 451: it exists, we were refused
    NOT_FOUND = "not_found"      # 404, 410
    ERROR = "error"              # any other code, or no HTTP answer at all
    NOT_CHECKED = "not_checked"  # offline: network=False


@dataclass(frozen=True)
class LinkResult:
    url: str
    status: LinkStatus
    code: int | None = None
    reason: str = ""


def check_link(url: str, *, network: bool = False, fetcher: Fetcher | None = None) -> LinkResult:
    """Does ``url`` load? Offline (the default) answers NOT_CHECKED without a request."""
    to_uri(url)
    if not network:
        return LinkResult(url, LinkStatus.NOT_CHECKED, reason="offline; pass network=True")
    if fetcher is None:
        raise ConfigError("network=True needs a Fetcher")
    try:
        resp = fetcher._get(url, read_body=False)
    except FetchError as exc:
        return LinkResult(url, LinkStatus.ERROR, reason=str(exc))
    if 200 <= resp.code < 300:
        return LinkResult(url, LinkStatus.LIVE, resp.code)
    if resp.code in BLOCKED_CODES:
        return LinkResult(url, LinkStatus.BLOCKED, resp.code, f"HTTP {resp.code}")
    if resp.code in GONE_CODES:
        return LinkResult(url, LinkStatus.NOT_FOUND, resp.code, f"HTTP {resp.code}")
    return LinkResult(url, LinkStatus.ERROR, resp.code, f"HTTP {resp.code}")
