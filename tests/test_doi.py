import json

import pytest
from conftest import HITS, ROUTES

from citecheck import ConfigError, DoiStatus, Fetcher, RequestCapError, check_doi
from citecheck import doi as doi_module

WORK = {"status": "ok", "message": {
    "title": ["Nanometre-scale <i>thermometry</i> in a living cell"],
    "container-title": ["Nature"], "issued": {"date-parts": [[2013, 7, 31]]},
    "author": [{"given": "G.", "family": "Kucsko"}, {"name": "A Consortium"}, {}],
    "type": "journal-article", "abstract": "<jats:p>Sensitive probing.</jats:p>"}}
JSON = "application/json"


@pytest.fixture
def services(server, monkeypatch):
    monkeypatch.setattr(doi_module, "CROSSREF", f"{server}/crossref/")
    monkeypatch.setattr(doi_module, "REGISTRY", f"{server}/registry/")
    routes = {
        "/crossref/10.1038/nature12373": (200, JSON, json.dumps(WORK).encode()),
        "/crossref/10.1000/notjson": (200, JSON, b"<html>"),
        "/crossref/10.1000/oddyear": (200, JSON, b'{"message": {"issued": {"date-parts": [["x"]]}}}'),
        "/crossref/10.1000/untitled": (200, JSON, b'{"message": {}}'),
        "/crossref/10.1000/limited": (429, "text/plain", b""),
        "/crossref/10.1000/broken": (500, "text/plain", b""),
        "/registry/10.5281/zenodo.12345": (200, JSON, b'{"responseCode": 1}'),
        "/registry/10.1000/odd": (200, JSON, b'{"responseCode": 200}'),
        "/registry/10.1000/refused": (403, JSON, b""),
    }
    for path, route in routes.items():
        monkeypatch.setitem(ROUTES, path, route)
    HITS.clear()
    return Fetcher("citecheck-tests/0", min_interval=0)


def test_crossref_record(services):
    r = check_doi("10.1038/nature12373", network=True, fetcher=services)
    assert r.status is DoiStatus.FOUND
    assert (r.title, r.journal, r.year) == ("Nanometre-scale thermometry in a living cell",
                                           "Nature", 2013)
    assert r.authors == ("G. Kucsko", "A Consortium")
    assert (r.kind, r.abstract, r.reason) == ("journal-article", "Sensitive probing.", "")
    assert HITS["/registry/10.1038/nature12373"] == 0


def test_doi_outside_crossref_is_found_without_metadata(services):
    r = check_doi("10.5281/zenodo.12345", network=True, fetcher=services)
    assert (r.status, r.title) == (DoiStatus.FOUND, "")
    assert "outside Crossref" in r.reason


def test_unregistered_doi_is_not_found(services):
    r = check_doi("10.9999/made-up", network=True, fetcher=services)
    assert r.status is DoiStatus.NOT_FOUND
    assert (HITS["/crossref/10.9999/made-up"], HITS["/registry/10.9999/made-up"]) == (1, 1)


@pytest.mark.parametrize(("doi", "status"), [
    ("10.1000/notjson", DoiStatus.ERROR), ("10.1000/oddyear", DoiStatus.ERROR),
    ("10.1000/limited", DoiStatus.BLOCKED), ("10.1000/broken", DoiStatus.ERROR),
    ("10.1000/odd", DoiStatus.ERROR), ("10.1000/refused", DoiStatus.BLOCKED),
])
def test_unusable_answers_are_never_found(services, doi, status):
    assert check_doi(doi, network=True, fetcher=services).status is status


def test_untitled_record_says_so(services):
    r = check_doi("10.1000/untitled", network=True, fetcher=services)
    assert (r.status, r.reason) == (DoiStatus.FOUND, "Crossref record has no title")


def test_doi_with_reserved_characters_stays_one_path(services):
    check_doi("10.1000/a?b#c", network=True, fetcher=services)
    assert HITS["/crossref/10.1000/a%3Fb%23c"] == 1


def test_connection_failure_is_error(monkeypatch):
    monkeypatch.setattr(doi_module, "CROSSREF", "http://127.0.0.1:9/")
    r = check_doi("10.1000/x", network=True, fetcher=Fetcher("citecheck-tests/0"))
    assert r.status is DoiStatus.ERROR


def test_offline_and_config(no_network):
    assert check_doi("10.1000/x").status is DoiStatus.NOT_CHECKED
    with pytest.raises(ConfigError):
        check_doi("10.1000/x", network=True)


@pytest.mark.parametrize("bad", ["", "doi:10.1000/x", "https://doi.org/10.1000/x", "10.1000/", "10.1000/a b", "10.1/x"])
def test_only_bare_dois(bad):
    with pytest.raises(ValueError):
        check_doi(bad)


def test_request_cap_stops_the_run(services):
    f = Fetcher("citecheck-tests/0", min_interval=0, max_requests=1)
    with pytest.raises(RequestCapError):
        check_doi("10.9999/made-up", network=True, fetcher=f)  # needs two requests
    assert f.sent == 1


@pytest.mark.parametrize("bad", [0, -1, 1.5, True])
def test_request_cap_must_be_a_count(bad):
    with pytest.raises(ConfigError):
        Fetcher("citecheck-tests/0", max_requests=bad)
