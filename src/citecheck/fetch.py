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
from dataclasses import dataclass, replace
from enum import Enum
from urllib.parse import quote, urlsplit, urlunsplit

MAX_BYTES = 8 * 1024 * 1024
BLOCKED_CODES = frozenset({401, 402, 403, 429, 451})
GONE_CODES = frozenset({404, 410})
_PATH_SAFE = "/%:@!$&'()*+,;=-._~"
_QUERY_SAFE = _PATH_SAFE + "?"


class ConfigError(Exception):
    """Something the caller must supply is missing: a fetcher, a user agent."""


class RequestCapError(Exception):
    """The Fetcher has sent its ``max_requests``; the run stops instead of going on unchecked."""


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

    ``cache=True`` fetches each URL once for this Fetcher's life, so many quotes checked
    against one page cost one request. It stores the answer as it came: an HTTP error
    code stays that code, and a failed fetch raises the same FetchError again. Off by
    default, so nothing is reused unless the caller asks; make a new Fetcher per run.

    ``max_requests`` caps every request it sends, redirect hops included; the one
    past the cap raises RequestCapError rather than being skipped.
    """

    def __init__(self, user_agent: str, *, timeout: float = 20, min_interval: float = 1.0,
                 cache: bool = False, max_requests: int | None = None):
        if not user_agent or not user_agent.strip():
            raise ConfigError("a user agent is required for network checks")
        if max_requests is not None and (isinstance(max_requests, bool)
                                         or not isinstance(max_requests, int) or max_requests < 1):
            raise ConfigError(f"max_requests must be a whole number of 1 or more, got {max_requests!r}")
        self.max_requests = max_requests
        self.sent = 0
        self.user_agent = user_agent
        self.timeout = timeout
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._opener = urllib.request.build_opener(_HttpOnlyRedirects, _Spacing(self._wait))
        self._cache: dict[str, tuple[Response, FetchError | None] | FetchError] | None = \
            {} if cache else None

    def _wait(self, host: str) -> None:
        if self.max_requests is not None and self.sent >= self.max_requests:
            raise RequestCapError(f"request cap of {self.max_requests} reached before {host}")
        self.sent += 1
        last = self._last.get(host)
        if last is not None:
            gap = self.min_interval - (time.monotonic() - last)
            if gap > 0:
                time.sleep(gap)
        self._last[host] = time.monotonic()

    def _get(self, url: str, *, read_body: bool = True) -> Response:
        """The HTTP answer, error codes included. Raises FetchError without a whole one."""
        if self._cache is None:
            resp, body_error = self._request(url, read_body)
        else:
            # One request per URL serves every later check, so the first always reads the
            # body. Its HTTP answer and any body failure are kept apart: a body that broke
            # off fails quote checks but leaves a link check's 200 a 200.
            key = to_uri(url)
            if key not in self._cache:
                try:
                    self._cache[key] = self._request(url, read_body=True)
                except FetchError as exc:
                    self._cache[key] = exc
            hit = self._cache[key]
            if isinstance(hit, FetchError):
                raise hit
            resp, body_error = hit
        if read_body and body_error is not None:
            raise body_error
        return resp

    def _request(self, url: str, read_body: bool) -> tuple[Response, FetchError | None]:
        """The HTTP answer and, separately, why its body could not be read whole.

        Raises FetchError when there is no HTTP answer at all.
        """
        req = urllib.request.Request(to_uri(url), headers={"User-Agent": self.user_agent})
        try:
            resp = self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            with exc:
                return Response(exc.geturl() or url, exc.code, exc.headers.get_content_type(),
                                exc.headers.get_content_charset(), b""), None
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            raise FetchError(f"{url}: {type(exc).__name__}: {exc}") from exc
        with resp:
            head = Response(resp.geturl(), resp.status, resp.headers.get_content_type(),
                            resp.headers.get_content_charset(), b"")
            if not read_body:
                return head, None
            try:
                body = resp.read(MAX_BYTES + 1)
            except (http.client.HTTPException, OSError) as exc:
                return head, FetchError(f"{url}: body read failed: {type(exc).__name__}: {exc}")
            if len(body) > MAX_BYTES:
                return head, FetchError(f"{url}: body is over the {MAX_BYTES}-byte limit")
            declared = resp.headers.get("Content-Length", "")
            if declared.isdigit() and len(body) < int(declared):
                return head, FetchError(f"{url}: body ended at {len(body)} of {declared} bytes")
            return replace(head, body=body), None


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
