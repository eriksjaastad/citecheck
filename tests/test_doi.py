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
DATACITE = {"data": {"attributes": {
    "titles": [{"title": "Attention Is All You Need"}, {"title": "Alt", "titleType": "Other"}],
    "publisher": "arXiv", "publicationYear": 2017, "container": {},
    "creators": [{"name": "Vaswani, Ashish", "givenName": "Ashish", "familyName": "Vaswani"},
                 {"name": "A Lab"}],
    "types": {"resourceTypeGeneral": "Preprint"},
    "descriptions": [{"description": "Other text", "descriptionType": "Other"},
                     {"description": "The dominant models.", "descriptionType": "Abstract"}]}}}
JATS = {"message": {"title": ["T"], "abstract":
    "<jats:title>Methods</jats:title><jats:p>Participants were recruited.</jats:p>"
    "<jats:p>The result was 10</jats:p><jats:p>20 people enrolled.</jats:p>"
    "<jats:p>CO<jats:sub>2</jats:sub> rose, <jats:italic>in vivo</jats:italic>.</jats:p>"}}
JSON = "application/json"


@pytest.fixture
def services(server, monkeypatch):
    monkeypatch.setattr(doi_module, "CROSSREF", f"{server}/crossref/")
    monkeypatch.setattr(doi_module, "DATACITE", f"{server}/datacite/")
    monkeypatch.setattr(doi_module, "REGISTRY", f"{server}/registry/")
    routes = {
        "/crossref/10.1038/nature12373": (200, JSON, json.dumps(WORK).encode()),
        "/crossref/10.1000/jats": (200, JSON, json.dumps(JATS).encode()),
        "/crossref/10.1000/notjson": (200, JSON, b"<html>"),
        "/crossref/10.1000/oddyear": (200, JSON, b'{"message": {"issued": {"date-parts": [["x"]]}}}'),
        "/crossref/10.1000/strtitle": (200, JSON, b'{"message": {"title": "A title"}}'),
        "/crossref/10.1000/errmsg": (200, JSON, b'{"status": "error", "message": "busy"}'),
        "/crossref/10.1000/untitled": (200, JSON, b'{"message": {}}'),
        "/datacite/10.48550/arxiv.1706.03762": (200, JSON, json.dumps(DATACITE).encode()),
        "/datacite/10.1000/dcbad": (200, JSON, b'{"data": {"attributes": {"titles": "x"}}}'),
        "/datacite/10.1000/dclimited": (429, JSON, b""),
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


def test_jats_blocks_stay_apart_and_inline_tags_join(services):
    r = check_doi("10.1000/jats", network=True, fetcher=services)
    assert r.abstract == ("Methods Participants were recruited. The result was 10 "
                          "20 people enrolled. CO2 rose, in vivo.")


def test_datacite_record(services):
    r = check_doi("10.48550/arxiv.1706.03762", network=True, fetcher=services)
    assert (r.status, r.title, r.journal, r.year) == (DoiStatus.FOUND,
                                                     "Attention Is All You Need", "arXiv", 2017)
    assert r.authors == ("Ashish Vaswani", "A Lab")
    assert (r.kind, r.abstract) == ("Preprint", "The dominant models.")


def test_doi_with_neither_record_is_registered_not_found(services):
    r = check_doi("10.5281/zenodo.12345", network=True, fetcher=services)
    assert (r.status, r.title) == (DoiStatus.REGISTERED, "")
    assert "no title to compare" in r.reason


def test_unregistered_doi_is_not_found(services):
    r = check_doi("10.9999/made-up", network=True, fetcher=services)
    assert r.status is DoiStatus.NOT_FOUND
    assert [HITS[f"/{s}/10.9999/made-up"] for s in ("crossref", "datacite", "registry")] \
        == [1, 1, 1]


@pytest.mark.parametrize(("doi", "status"), [
    ("10.1000/notjson", DoiStatus.ERROR), ("10.1000/oddyear", DoiStatus.ERROR),
    ("10.1000/strtitle", DoiStatus.ERROR), ("10.1000/errmsg", DoiStatus.ERROR),
    ("10.1000/dcbad", DoiStatus.ERROR), ("10.1000/dclimited", DoiStatus.BLOCKED),
    ("10.1000/limited", DoiStatus.BLOCKED), ("10.1000/broken", DoiStatus.ERROR),
    ("10.1000/odd", DoiStatus.ERROR), ("10.1000/refused", DoiStatus.BLOCKED),
])
def test_unusable_answers_are_never_found(services, doi, status):
    assert check_doi(doi, network=True, fetcher=services).status is status


def test_untitled_record_is_registered_not_found(services):
    r = check_doi("10.1000/untitled", network=True, fetcher=services)
    assert (r.status, r.reason) == (DoiStatus.REGISTERED, "the record has no title to compare")


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
        check_doi("10.9999/made-up", network=True, fetcher=f)  # needs three requests
    assert f.sent == 1


@pytest.mark.parametrize("bad", [0, -1, 1.5, True])
def test_request_cap_must_be_a_count(bad):
    with pytest.raises(ConfigError):
        Fetcher("citecheck-tests/0", max_requests=bad)
