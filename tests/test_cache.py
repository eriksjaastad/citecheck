import pytest
from conftest import HITS

from citecheck import Citation, Fetcher, LinkStatus, Status, check_link, check_quote

QUOTE = "voted seven to two to approve"


def fetcher(cache):
    return Fetcher("citecheck-tests/0", min_interval=0, cache=cache)


def quote(server, path, words, f):
    return check_quote(Citation(f"{server}{path}", words), network=True, fetcher=f)


def link(server, path, f):
    return check_link(f"{server}{path}", network=True, fetcher=f)


@pytest.fixture(autouse=True)
def reset_hits():
    HITS.clear()


def test_off_by_default_refetches(server):
    f = Fetcher("citecheck-tests/0", min_interval=0)
    for _ in range(2):
        assert quote(server, "/page", QUOTE, f).status is Status.SUPPORTED
    assert HITS["/page"] == 2


def test_warm_hits_give_the_same_verdicts(server):
    f = fetcher(True)
    for _ in range(2):
        assert quote(server, "/page", QUOTE, f).status is Status.SUPPORTED
        assert quote(server, "/page", "hidden words here", f).status is Status.NOT_FOUND
        assert quote(server, "/doc.pdf", "revenue rose sharply", f).status is Status.SUPPORTED
        assert quote(server, "/doc.pdf", "revenue fell", f).status is Status.NOT_FOUND
    assert (HITS["/page"], HITS["/doc.pdf"]) == (1, 1)


def test_cache_is_per_fetcher(server):
    for _ in range(2):
        assert quote(server, "/page", QUOTE, fetcher(True)).status is Status.SUPPORTED
    assert HITS["/page"] == 2


@pytest.mark.parametrize("link_first", [True, False])
def test_link_and_quote_share_one_request(server, link_first):
    f = fetcher(True)
    checks = [lambda: link(server, "/page", f), lambda: quote(server, "/page", QUOTE, f)]
    for check in checks if link_first else checks[::-1]:
        check()
    assert link(server, "/page", f).status is LinkStatus.LIVE
    assert quote(server, "/page", QUOTE, f).status is Status.SUPPORTED
    assert HITS["/page"] == 1


@pytest.mark.parametrize("path", ["/short", "/huge"])
@pytest.mark.parametrize("link_first", [True, False])
def test_body_failure_fails_quotes_but_keeps_the_link_live(server, path, link_first):
    f = fetcher(True)
    if link_first:
        assert link(server, path, f).status is LinkStatus.LIVE
    for _ in range(2):
        assert quote(server, path, "words that would be checked", f).status \
            is Status.NOT_CHECKED
    assert link(server, path, f).status is LinkStatus.LIVE
    assert HITS[path] == 1


def test_cached_http_error_stays_an_error(server):
    f = fetcher(True)
    for _ in range(2):
        assert quote(server, "/forbidden", QUOTE, f).status is Status.BLOCKED
        assert link(server, "/forbidden", f).status is LinkStatus.BLOCKED
        assert link(server, "/gone", f).status is LinkStatus.NOT_FOUND
    assert (HITS["/forbidden"], HITS["/gone"]) == (1, 1)


def test_cached_connection_failure_stays_a_failure():
    f = fetcher(True)
    for _ in range(2):
        assert check_link("http://127.0.0.1:9/", network=True, fetcher=f).status \
            is LinkStatus.ERROR
        assert check_quote(Citation("http://127.0.0.1:9/", QUOTE), network=True, fetcher=f) \
            .status is Status.NOT_CHECKED
