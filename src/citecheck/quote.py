"""Quote check: is the quoted passage actually in the cited source?

Merged from three working checks: a report gate that fetches each source and looks
for the evidence quote (any script, ellipsis fragments, PDFs), a verbatim
quote-in-archived-source match, and a citation record that reads one PDF page.

Matching ignores case, Unicode form, punctuation and spacing, and nothing that can
change meaning: letters keep their marks, numbers keep their signs and percent, and a
match must start and end on a word boundary ("4%" is not in "40%").
"""
from __future__ import annotations

import codecs
import io
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from pypdf import PdfReader

from .fetch import BLOCKED_CODES, ConfigError, Fetcher, FetchError
from .record import Citation
from .status import Status

_ELLIPSIS = re.compile(r"\s*(?:\[\s*(?:…|\.\.\.)\s*\]|…|\.\.\.)\s*")
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z0-9_.:-]+)""", re.IGNORECASE)
# Scripts written without spaces between words: any character there is a boundary.
_UNSEGMENTED = ("CJK", "HIRAGANA", "KATAKANA", "HALFWIDTH KATAKANA", "IDEOGRAPHIC",
                "THAI", "LAO", "KHMER", "MYANMAR")


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
    """NFKC-fold and casefold; punctuation and spacing collapse to single spaces.

    Kept: letters with their combining marks (दिन is not दीन), digits, symbols
    (+, $, °), percent, and a dash before a digit (as "-", so -10 is not 10).
    """
    text = unicodedata.normalize("NFKC", text).casefold()
    out: list[str] = []
    gap = False
    for i, ch in enumerate(text):
        cat = unicodedata.category(ch)
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if cat == "Pd" and nxt.isdigit():
            ch, cat = "-", "Sm"
        if cat[0] in "LMNS" or ch == "%":
            if gap and out:
                out.append(" ")
            out.append(ch)
            gap = False
        else:
            gap = True
    return "".join(out)


def _unsegmented(ch: str) -> bool:
    return unicodedata.name(ch, "").startswith(_UNSEGMENTED)


def _bounded(page: str, start: int, end: int) -> bool:
    left = start == 0 or page[start - 1] == " " or _unsegmented(page[start - 1]) \
        or _unsegmented(page[start])
    right = end == len(page) or page[end] == " " or _unsegmented(page[end]) \
        or _unsegmented(page[end - 1])
    return left and right


def _find(page: str, part: str, at: int) -> int:
    """End of the first word-bounded match of ``part`` at or after ``at``, or -1."""
    found = page.find(part, at)
    while found >= 0:
        if _bounded(page, found, found + len(part)):
            return found + len(part)
        found = page.find(part, found + 1)
    return -1


def quote_in(quote: str, text: str) -> bool:
    """True when every ellipsis-separated fragment of the quote is in ``text``, in order.

    An empty quote raises ValueError and an empty text raises EmptySourceError: an
    empty string is "in" every page.
    """
    page = normalize(text)
    if not page:
        raise EmptySourceError("source text is empty")
    parts = [p for p in (normalize(f) for f in _ELLIPSIS.split(quote)) if p]
    if not parts:
        raise ValueError("quote is empty after normalizing")
    at = 0
    for part in parts:
        at = _find(page, part, at)
        if at < 0:
            return False
    return True


class _Visible(HTMLParser):
    """Visible text: no comments, attributes, scripts or styles; inline tags join words."""

    SKIP = frozenset({"script", "style", "noscript", "template"})
    BLOCK = frozenset({"address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
                       "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5",
                       "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
                       "table", "td", "th", "tr", "ul"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BLOCK:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag in self.BLOCK:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


def html_text(body: str) -> str:
    """Visible text of an HTML page."""
    parser = _Visible()
    parser.feed(body)
    parser.close()
    return "".join(parser.parts)


def pdf_text(data: bytes, page: int | None = None) -> str:
    """Text of a PDF, or of one 1-based page. A page past the end raises."""
    reader = PdfReader(io.BytesIO(data))
    if page is None:
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    if page > len(reader.pages):
        raise ValueError(f"page {page} is past the end of a {len(reader.pages)}-page PDF")
    return reader.pages[page - 1].extract_text() or ""


def _encoding(data: bytes, charset: str | None, is_html: bool) -> str:
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    if charset:
        return charset
    match = _META_CHARSET.search(data[:4096]) if is_html else None
    return match.group(1).decode("ascii") if match else "utf-8"


def _decode(data: bytes, page: int | None, *, is_html: bool, charset: str | None = None) -> str:
    """Source bytes as text. Undecodable bytes raise; they are never replaced and graded."""
    if data[:5] == b"%PDF-":
        return pdf_text(data, page)
    if page is not None:
        raise ValueError("citation has a page, but the source is not a PDF")
    encoding = _encoding(data, charset, is_html)
    try:
        text = data.decode(encoding)
    except LookupError:
        raise ValueError(f"source declares an unknown encoding {encoding!r}") from None
    except UnicodeDecodeError as exc:
        raise ValueError(f"source is not valid {encoding}: {exc}") from exc
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

    SUPPORTED: found. NOT_FOUND: the whole source was read and the quote is not in it.
    BLOCKED: the server refused us. NOT_CHECKED: offline, or the source is gone,
    unreachable or incomplete (the reason says which). A citation with no quote
    raises. Local paths resolve under ``root``.
    """
    if not normalize(citation.quote):
        raise ValueError(f"citation of {citation.source} has no quote to check")
    if citation.is_url:
        if not network:
            return QuoteResult(Status.NOT_CHECKED, "offline; pass network=True")
        if fetcher is None:
            raise ConfigError("network=True needs a Fetcher")
        try:
            resp = fetcher._get(citation.source)
        except FetchError as exc:
            return QuoteResult(Status.NOT_CHECKED, f"not loaded: {exc}")
        if resp.code in BLOCKED_CODES:
            return QuoteResult(Status.BLOCKED, f"HTTP {resp.code}")
        if resp.code != 200:
            return QuoteResult(Status.NOT_CHECKED, f"source not loaded whole: HTTP {resp.code}")
        text = _decode(resp.body, citation.page, is_html="html" in resp.content_type,
                       charset=resp.charset)
    else:
        path = _inside(Path(root), citation.source)
        if not path.is_file():
            return QuoteResult(Status.NOT_CHECKED, f"file missing: {citation.source}")
        text = _decode(path.read_bytes(), citation.page,
                       is_html=path.suffix.lower() in (".html", ".htm"))
    if not normalize(text):
        raise EmptySourceError(f"no text extracted from {citation.source}")
    if quote_in(citation.quote, text):
        return QuoteResult(Status.SUPPORTED)
    return QuoteResult(Status.NOT_FOUND, "source read; quote not in it")
