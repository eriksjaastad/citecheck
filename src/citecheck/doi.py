"""Does a DOI exist, and what does its record say it is?

Crossref holds most journal DOIs and gives title, journal, year and authors. It does
not hold them all: datasets and preprints registered with DataCite (Zenodo, for one)
answer 404 there. So a DOI Crossref does not know is looked up at the DOI registry
itself, doi.org, before it is called NOT_FOUND. Both are free and need no key.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote

from .fetch import BLOCKED_CODES, ConfigError, Fetcher, FetchError
from .quote import html_text

CROSSREF = "https://api.crossref.org/works/"
REGISTRY = "https://doi.org/api/handles/"
_DOI = re.compile(r"10\.\d{4,9}/\S+")


class DoiStatus(str, Enum):
    FOUND = "found"              # registered; Crossref records come with title and journal
    NOT_FOUND = "not_found"      # the DOI registry has no such DOI
    BLOCKED = "blocked"          # 401, 402, 403, 429, 451 from Crossref or the registry
    ERROR = "error"              # no answer, any other code, or an answer we cannot read
    NOT_CHECKED = "not_checked"  # offline: network=False


@dataclass(frozen=True)
class DoiResult:
    doi: str
    status: DoiStatus
    title: str = ""
    journal: str = ""
    year: int | None = None
    authors: tuple[str, ...] = ()
    kind: str = ""               # Crossref's type, e.g. "journal-article"
    abstract: str = ""
    reason: str = ""


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
    try:
        resp = fetcher._get(CROSSREF + path)
        if resp.code == 200:
            return _crossref(doi, resp.body)
        if resp.code != 404:
            return _refused(doi, "Crossref", resp.code)
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
    return DoiResult(doi, DoiStatus.FOUND,
                     reason="registered outside Crossref: no title or journal to compare")


def _refused(doi: str, service: str, code: int) -> DoiResult:
    status = DoiStatus.BLOCKED if code in BLOCKED_CODES else DoiStatus.ERROR
    return DoiResult(doi, status, reason=f"{service} HTTP {code}")


def _crossref(doi: str, body: bytes) -> DoiResult:
    try:
        work = json.loads(body)["message"]
        title = _text((work.get("title") or [""])[0])
        journal = _text((work.get("container-title") or [""])[0])
        year = ((work.get("issued") or {}).get("date-parts") or [[None]])[0][0]
        authors = tuple(
            " ".join(p for p in (a.get("given"), a.get("family")) if p) or a.get("name", "")
            for a in work.get("author") or [])
        kind = work.get("type") or ""
        abstract = _text(work.get("abstract") or "")
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        return DoiResult(doi, DoiStatus.ERROR, reason="Crossref answer is not a work record")
    if year is not None and not isinstance(year, int):
        return DoiResult(doi, DoiStatus.ERROR, reason="Crossref answer is not a work record")
    return DoiResult(doi, DoiStatus.FOUND, title, journal, year,
                     tuple(a for a in authors if a), kind, abstract,
                     "" if title else "Crossref record has no title")


def _text(value: str) -> str:
    """Crossref titles and abstracts carry JATS markup (<i>, <jats:p>)."""
    if not isinstance(value, str):
        raise TypeError("expected a string")
    return " ".join(html_text(value).split())
