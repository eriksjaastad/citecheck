"""Does a DOI exist, and what does its record say it is?

Crossref holds most journal DOIs. DataCite holds most of the rest: preprints
(arXiv), datasets and repository deposits (Zenodo), which answer 404 at Crossref.
A DOI neither knows is looked up at the DOI registry itself, doi.org, before it is
called NOT_FOUND. All three are free and need no key.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from enum import Enum
from urllib.parse import quote

from .fetch import BLOCKED_CODES, ConfigError, Fetcher, FetchError
from .quote import html_text

CROSSREF = "https://api.crossref.org/works/"
DATACITE = "https://api.datacite.org/dois/"
REGISTRY = "https://doi.org/api/handles/"
_DOI = re.compile(r"10\.\d{4,9}/\S+")


class DoiStatus(str, Enum):
    FOUND = "found"              # registered, with a title to compare
    REGISTERED = "registered"    # registered, but no record gave a title to compare
    NOT_FOUND = "not_found"      # the DOI registry has no such DOI
    BLOCKED = "blocked"          # 401, 402, 403, 429, 451 from a lookup service
    ERROR = "error"              # no answer, any other code, or an answer we cannot read
    NOT_CHECKED = "not_checked"  # offline: network=False


@dataclass(frozen=True)
class DoiResult:
    doi: str
    status: DoiStatus
    title: str = ""
    journal: str = ""            # Crossref's container title; DataCite's, else its publisher
    year: int | None = None
    authors: tuple[str, ...] = ()
    kind: str = ""               # "journal-article" (Crossref), "Preprint" (DataCite)
    abstract: str = ""
    reason: str = ""


class _Malformed(Exception):
    pass


def check_doi(doi: str, *, network: bool = False, fetcher: Fetcher | None = None) -> DoiResult:
    """Look ``doi`` up. A bare DOI only (``10.1038/x``, not a doi.org URL); others raise.

    The title and journal come back for the caller to compare with what the claim
    says; this does not decide whether they match.
    """
    if not _DOI.fullmatch(doi):
        raise ValueError(f"not a bare DOI (10.<prefix>/<suffix>): {doi!r}")
    if not network:
        return DoiResult(doi, DoiStatus.NOT_CHECKED, reason="offline; pass network=True")
    if fetcher is None:
        raise ConfigError("network=True needs a Fetcher")
    path = quote(doi, safe="/")
    for service, base, read in (("Crossref", CROSSREF, _crossref),
                                ("DataCite", DATACITE, _datacite)):
        try:
            resp = fetcher._get(base + path)
        except FetchError as exc:
            return DoiResult(doi, DoiStatus.ERROR, reason=str(exc))
        if resp.code == 200:
            try:
                return _graded(DoiResult(doi, DoiStatus.FOUND, **read(json.loads(resp.body))))
            except (ValueError, TypeError, AttributeError, IndexError, _Malformed):
                return DoiResult(doi, DoiStatus.ERROR, reason=f"{service} answer is not a record")
        if resp.code != 404:
            return _refused(doi, service, resp.code)
    return _registry(doi, fetcher, path)


def _registry(doi: str, fetcher: Fetcher, path: str) -> DoiResult:
    try:
        resp = fetcher._get(REGISTRY + path)
    except FetchError as exc:
        return DoiResult(doi, DoiStatus.ERROR, reason=str(exc))
    if resp.code == 404:
        return DoiResult(doi, DoiStatus.NOT_FOUND, reason="the DOI registry has no such DOI")
    if resp.code != 200:
        return _refused(doi, "doi.org", resp.code)
    try:
        registered = json.loads(resp.body)["responseCode"] == 1
    except (ValueError, KeyError, TypeError):
        registered = False
    if not registered:
        return DoiResult(doi, DoiStatus.ERROR, reason="doi.org answer is not a DOI record")
    return DoiResult(doi, DoiStatus.REGISTERED,
                     reason="registered with neither Crossref nor DataCite: no title to compare")


def _graded(result: DoiResult) -> DoiResult:
    if result.title:
        return result
    return replace(result, status=DoiStatus.REGISTERED, reason="the record has no title to compare")


def _refused(doi: str, service: str, code: int) -> DoiResult:
    status = DoiStatus.BLOCKED if code in BLOCKED_CODES else DoiStatus.ERROR
    return DoiResult(doi, status, reason=f"{service} HTTP {code}")


def _crossref(data) -> dict:
    work = _field(data, "message", dict)
    year = (_field(work, "issued", dict, {}).get("date-parts") or [[None]])
    if not isinstance(year, list) or not year or not isinstance(year[0], list):
        raise _Malformed
    authors = []
    for a in _field(work, "author", list, []):
        if not isinstance(a, dict):
            raise _Malformed
        authors.append(" ".join(_str(a.get(k)) for k in ("given", "family") if a.get(k))
                       or _str(a.get("name")))
    return {"title": _first(work, "title"), "journal": _first(work, "container-title"),
            "year": _year(year[0][0] if year[0] else None), "authors": _names(authors),
            "kind": _str(work.get("type")), "abstract": _text(_str(work.get("abstract")))}


def _datacite(data) -> dict:
    attrs = _field(_field(data, "data", dict), "attributes", dict)
    titles = _field(attrs, "titles", list, [])
    if not all(isinstance(t, dict) for t in titles):
        raise _Malformed
    main = [t for t in titles if not t.get("titleType")] or titles  # untyped = the main title
    publisher = attrs.get("publisher")
    if isinstance(publisher, dict):  # newer API versions nest it
        publisher = publisher.get("name")
    container = _field(attrs, "container", dict, {}).get("title")
    authors = []
    for c in _field(attrs, "creators", list, []):
        if not isinstance(c, dict):
            raise _Malformed
        given, family = c.get("givenName"), c.get("familyName")
        authors.append(f"{_str(given)} {_str(family)}" if given and family else _str(c.get("name")))
    abstract = next((_str(d.get("description")) for d in _field(attrs, "descriptions", list, [])
                     if isinstance(d, dict) and d.get("descriptionType") == "Abstract"), "")
    return {"title": _text(_str(main[0].get("title"))) if main else "",
            "journal": _text(_str(container or publisher)),
            "year": _year(attrs.get("publicationYear")), "authors": _names(authors),
            "kind": _str(_field(attrs, "types", dict, {}).get("resourceTypeGeneral")),
            "abstract": _text(abstract)}


def _field(obj, key, kind, default=None):
    """``obj[key]`` checked to be ``kind``; ``default`` when absent (else it must be there)."""
    if not isinstance(obj, dict):
        raise _Malformed
    value = obj.get(key)
    if value is None and default is not None:
        return default
    if not isinstance(value, kind):
        raise _Malformed
    return value


def _first(work: dict, key: str) -> str:
    """Crossref's list-valued title fields: first entry, or ""."""
    values = _field(work, key, list, [])
    return _text(_str(values[0])) if values else ""


def _str(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _Malformed
    return value


def _year(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise _Malformed
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int):
        raise _Malformed
    return value


def _names(names: list[str]) -> tuple[str, ...]:
    return tuple(n for n in (" ".join(x.split()) for x in names) if n)


def _text(value: str) -> str:
    """Titles and abstracts carry JATS or HTML markup (<i>, <jats:p>)."""
    return " ".join(html_text(value).split())
