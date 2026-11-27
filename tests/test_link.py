import time

import pytest

from citecheck import ConfigError, Fetcher, LinkStatus, check_link


def fetcher():
    return Fetcher("citecheck-tests/0", min_interval=0)


def test_offline_by_default(no_network):
    assert check_link("https://example.org/").status is LinkStatus.NOT_CHECKED


def test_user_agent_and_fetcher_required():
    with pytest.raises(ConfigError):
        Fetcher("  ")
    with pytest.raises(ConfigError):
        check_link("https://example.org/", network=True)


@pytest.mark.parametrize("url", ["file:///etc/hosts", "ftp://example.org/x", "example.org"])
def test_only_http_urls(url):
    with pytest.raises(ValueError):
        check_link(url, network=True, fetcher=fetcher())


@pytest.mark.parametrize("path, status, code", [
    ("/page", LinkStatus.LIVE, 200),
    ("/forbidden", LinkStatus.BLOCKED, 403),
    ("/limited", LinkStatus.BLOCKED, 429),
    ("/gone", LinkStatus.NOT_FOUND, 404),
    ("/broken", LinkStatus.ERROR, 500),
])
def test_blocked_is_not_not_found(server, path, status, code):
    result = check_link(f"{server}{path}", network=True, fetcher=fetcher())
    assert (result.status, result.code) == (status, code)


def test_redirect_off_http_is_refused(server):
    with pytest.raises(ValueError):
        check_link(f"{server}/to-ftp", network=True, fetcher=fetcher())


def test_no_answer_is_error():
    result = check_link("http://127.0.0.1:9/", network=True, fetcher=fetcher())
    assert result.status is LinkStatus.ERROR and result.reason


def test_same_host_is_spaced(server):
    polite = Fetcher("citecheck-tests/0", min_interval=0.3)
    start = time.monotonic()
    check_link(f"{server}/page", network=True, fetcher=polite)
    check_link(f"{server}/page", network=True, fetcher=polite)
    assert time.monotonic() - start >= 0.3
