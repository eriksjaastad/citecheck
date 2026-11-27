"""Quote check: is the quoted passage actually in the cited source?

Merged from three working checks: a report gate that fetches each source and looks
for the evidence quote (any script, ellipsis fragments, PDFs), a verbatim
quote-in-archived-source match, and a citation record that reads one PDF page.
Matching ignores case, Unicode form, punctuation and spacing, nothing more.
"""
from __future__ import annotations

import html
import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from .fetch import BLOCKED_CODES, ConfigError, Fetcher, FetchError
from .record import Citation
from .status import Status

MIN_FRAGMENT = 12

_NON_WORD = re.compile(r"[\W_]+")
_ELLIPSIS = re.compile(r"\s*(?:…|\.\.\.|\[\s*(?:…|\.\.\.)\s*\])\s*")
_DROP_BLOCKS = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


class EmptySourceError(Exception):
    """The source was read but yielded no text (script-built page, image-only PDF).

    Raised rather than reported as NOT_FOUND: saying the quote is missing from a
    page we could not read would accuse the claim of something we never checked.
    """


@dataclass(frozen=True)
class QuoteResult:
    status: Status
    reason: str = ""


def normalize(text: str) -> str:
    """NFKC-fold and casefold; runs of punctuation and space become one space.

    Letters in every script survive, so a Cyrillic or CJK quote is compared, not erased.
    """
    text = unicodedata.normalize("NFKC", text).casefold()
    return _NON_WORD.sub(" ", text).strip()


def quote_in(quote: str, text: str, *, min_fragment: int = MIN_FRAGMENT) -> bool:
    """True when the quote's ellipsis-separated fragments appear in ``text``, in order.

    Fragments shorter than ``min_fragment`` characters are skipped, unless that
    leaves none, in which case the whole quote must appear. An empty quote or an
    empty text raises: an empty string is "in" every page.
    """
    page = normalize(text)
    if not page:
        raise EmptySourceError("source text is empty")
    parts = [normalize(p) for p in _ELLIPSIS.split(quote)]
    parts = [p for p in parts if len(p) >= min_fragment] or [normalize(quote)]
    if not all(parts):
        raise ValueError("quote is empty after normalizing")
    at = 0
    for part in parts:
        found = page.find(part, at)
        if found < 0:
            return False
        at = found + len(part)
    return True


def html_text(body: str) -> str:
    """Visible text of an HTML page: scripts and styles dropped, tags removed."""
    return html.unescape(_TAG.sub(" ", _DROP_BLOCKS.sub(" ", body)))


def pdf_text(data: bytes, page: int | None = None) -> str:
    """Text of a PDF, or of one 1-based page. A page past the end raises."""
    reader = PdfReader(io.BytesIO(data))
    if page is None:
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    if page > len(reader.pages):
        raise ValueError(f"page {page} is past the end of a {len(reader.pages)}-page PDF")
    return reader.pages[page - 1].extract_text() or ""


def _decode(data: bytes, page: int | None, *, is_html: bool, charset: str | None = None) -> str:
    if data[:5] == b"%PDF-":
        return pdf_text(data, page)
    if page is not None:
        raise ValueError("citation has a page, but the source is not a PDF")
    try:
        text = data.decode(charset or "utf-8", "replace")
    except LookupError:  # a server-declared charset Python doesn't know
        text = data.decode("utf-8", "replace")
    return html_text(text) if is_html else text


def _inside(root: Path, name: str) -> Path:
    base = root.resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"citation path escapes root: {name!r}")
    return path


def check_quote(citation: Citation, *, root: str | Path = ".", network: bool = False,
                fetcher: Fetcher | None = None) -> QuoteResult:
    """Grade one citation's quote against its source.

    SUPPORTED: found. NOT_FOUND: the source was read and the quote is not in it.
    BLOCKED: the server refused us. NOT_CHECKED: no quote, offline, or the source is
    gone or unreachable (the reason says which). Local paths resolve under ``root``.
    """
    if not citation.quote.strip():
        return QuoteResult(Status.NOT_CHECKED, "citation has no quote")
    if citation.is_url:
        if not network:
            return QuoteResult(Status.NOT_CHECKED, "offline; pass network=True")
        if fetcher is None:
            raise ConfigError("network=True needs a Fetcher")
        try:
            resp = fetcher.get(citation.source)
        except FetchError as exc:
            return QuoteResult(Status.NOT_CHECKED, f"unreachable: {exc}")
        if resp.code in BLOCKED_CODES:
            return QuoteResult(Status.BLOCKED, f"HTTP {resp.code}")
        if not 200 <= resp.code < 300:
            return QuoteResult(Status.NOT_CHECKED, f"source not loaded: HTTP {resp.code}")
        text = _decode(resp.body, citation.page, is_html="html" in resp.content_type,
                       charset=resp.charset)
    else:
        path = _inside(Path(root), citation.source)
        if not path.is_file():
            return QuoteResult(Status.NOT_CHECKED, f"file missing: {citation.source}")
        text = _decode(path.read_bytes(), citation.page, is_html=path.suffix in (".html", ".htm"))
    if not normalize(text):
        raise EmptySourceError(f"no text extracted from {citation.source}")
    if quote_in(citation.quote, text):
        return QuoteResult(Status.SUPPORTED)
    return QuoteResult(Status.NOT_FOUND, "source read; quote not in it")
