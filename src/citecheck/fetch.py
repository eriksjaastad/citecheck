"""The only code that touches the network, and the link check built on it.

Callers opt in per call with ``network=True`` and a ``Fetcher`` carrying their own
user agent (with their contact address, from their own config). Only http and https
are fetched, and requests to one host are spaced ``min_interval`` seconds apart.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

MAX_BYTES = 8 * 1024 * 1024
BLOCKED_CODES = frozenset({401, 402, 403, 429, 451})
GONE_CODES = frozenset({404, 410})


class ConfigError(Exception):
    """Something the caller must supply is missing: a fetcher, a user agent."""


class FetchError(Exception):
    """The request never got an HTTP answer: DNS, refused, timeout, TLS."""


@dataclass(frozen=True)
class Response:
    url: str            # final URL, after redirects
    code: int
    content_type: str
    charset: str | None
    body: bytes


def _require_http(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"only http(s) URLs are fetched, got {url!r}")


class _HttpOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """urllib also follows redirects to ftp://; a caller's URL must stay on http(s)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _require_http(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_HttpOnlyRedirects)


class Fetcher:
    """GETs URLs politely. One per run; it remembers when it last hit each host."""

    def __init__(self, user_agent: str, *, timeout: float = 20, min_interval: float = 1.0):
        if not user_agent or not user_agent.strip():
            raise ConfigError("a user agent is required for network checks")
        self.user_agent = user_agent
        self.timeout = timeout
        self.min_interval = min_interval
        self._last: dict[str, float] = {}

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self.min_interval - (time.monotonic() - last)
            if gap > 0:
                time.sleep(gap)
        self._last[host] = time.monotonic()

    def get(self, url: str, *, max_bytes: int = MAX_BYTES) -> Response:
        """The HTTP answer, error codes included. Raises FetchError with no answer."""
        _require_http(url)
        self._wait(urlparse(url).hostname or "")
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with _OPENER.open(req, timeout=self.timeout) as resp:
                return Response(resp.geturl(), resp.status, resp.headers.get_content_type(),
                                resp.headers.get_content_charset(), resp.read(max_bytes))
        except urllib.error.HTTPError as exc:
            with exc:
                return Response(exc.geturl() or url, exc.code, exc.headers.get_content_type(),
                                exc.headers.get_content_charset(), b"")
        except (urllib.error.URLError, OSError) as exc:
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
    _require_http(url)
    if not network:
        return LinkResult(url, LinkStatus.NOT_CHECKED, reason="offline; pass network=True")
    if fetcher is None:
        raise ConfigError("network=True needs a Fetcher")
    try:
        resp = fetcher.get(url, max_bytes=0)
    except FetchError as exc:
        return LinkResult(url, LinkStatus.ERROR, reason=str(exc))
    if 200 <= resp.code < 300:
        return LinkResult(url, LinkStatus.LIVE, resp.code)
    if resp.code in BLOCKED_CODES:
        return LinkResult(url, LinkStatus.BLOCKED, resp.code, f"HTTP {resp.code}")
    if resp.code in GONE_CODES:
        return LinkResult(url, LinkStatus.NOT_FOUND, resp.code, f"HTTP {resp.code}")
    return LinkResult(url, LinkStatus.ERROR, resp.code, f"HTTP {resp.code}")
