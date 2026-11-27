import pytest
from conftest import HITS

from citecheck import Citation, Fetcher, LinkStatus, Status, check_link, check_quote

QUOTE = "voted seven to two to approve"


def fetcher(cache):
    return Fetcher("citecheck-tests/0", min_interval=0, cache=cache)


@pytest.fixture(autouse=True)
def reset_hits():
    HITS.clear()


def test_off_by_default_refetches(server):
    f = Fetcher("citecheck-tests/0", min_interval=0)
    for _ in range(2):
        assert check_quote(Citation(f"{server}/page", QUOTE), network=True, fetcher=f).status \
            is Status.SUPPORTED
    assert HITS["/page"] == 2


def test_hit_and_miss(server):
    f = fetcher(True)
    for quote in (QUOTE, "the budget", "hidden words here"):
        check_quote(Citation(f"{server}/page", quote), network=True, fetcher=f)
    check_quote(Citation(f"{server}/doc.pdf", "revenue rose sharply"), network=True, fetcher=f)
    assert (HITS["/page"], HITS["/doc.pdf"]) == (1, 1)


def test_cache_is_per_fetcher(server):
    for _ in range(2):
        check_quote(Citation(f"{server}/page", QUOTE), network=True, fetcher=fetcher(True))
    assert HITS["/page"] == 2


def test_link_check_answer_does_not_serve_a_quote_check(server):
    f = fetcher(True)
    assert check_link(f"{server}/page", network=True, fetcher=f).status is LinkStatus.LIVE
    assert check_quote(Citation(f"{server}/page", QUOTE), network=True, fetcher=f).status \
        is Status.SUPPORTED
    assert check_link(f"{server}/page", network=True, fetcher=f).status is LinkStatus.LIVE
    assert HITS["/page"] == 2


def test_cached_http_error_stays_an_error(server):
    f = fetcher(True)
    for _ in range(2):
        assert check_quote(Citation(f"{server}/forbidden", QUOTE), network=True, fetcher=f) \
            .status is Status.BLOCKED
        assert check_link(f"{server}/gone", network=True, fetcher=f).status \
            is LinkStatus.NOT_FOUND
    assert (HITS["/forbidden"], HITS["/gone"]) == (1, 1)


def test_cached_fetch_failure_stays_a_failure(server):
    f = fetcher(True)
    first = check_quote(Citation(f"{server}/short", QUOTE), network=True, fetcher=f)
    again = check_quote(Citation(f"{server}/short", QUOTE), network=True, fetcher=f)
    assert first.status is again.status is Status.NOT_CHECKED
    assert first.reason == again.reason and "ended at" in first.reason
    assert HITS["/short"] == 1
    dead = [check_link("http://127.0.0.1:9/", network=True, fetcher=f) for _ in range(2)]
    assert {r.status for r in dead} == {LinkStatus.ERROR}
