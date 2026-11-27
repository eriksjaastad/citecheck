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

from .fetch import BLOCKED_CODES, GONE_CODES, ConfigError, Fetcher, FetchError
from .record import Citation
from .status import Status

_ELLIPSIS = re.compile(r"\s*(?:\[\s*(?:…|\.\.\.)\s*\]|…|\.\.\.)\s*")
_CONTENT_CHARSET = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", re.IGNORECASE)
_SIGNS = frozenset("+±−")  # plus every Pd dash
_SPACED_SIGNS = frozenset("-+±−")  # still a sign with spaces before the number
# Scripts written without spaces between words: any character there is a boundary.
_UNSEGMENTED = ("CJK", "HIRAGANA", "KATAKANA", "HALFWIDTH KATAKANA", "IDEOGRAPHIC",
                "THAI", "LAO", "KHMER", "MYANMAR")


class EmptySourceError(Exception):
    """The source was read but yielded no text (script-built page, image-only PDF).

    Raised rather than reported as NOT_FOUND: saying the quote is missing from a
    page we could not read would accuse the claim of something we never checked.
    """


class PageMissingError(ValueError):
    """The cited page is not in the source: past the end, or the source is not a PDF."""


@dataclass(frozen=True)
class QuoteResult:
    status: Status
    reason: str = ""


def normalize(text: str) -> str:
    """NFKC-fold and casefold; punctuation and spacing collapse to single spaces.

    Kept, because each can change a claim's meaning: letters with their combining
    marks (दिन is not दीन); digits with the "." or "," inside a number (50 is not
    50.5 or 50,000); symbols (+, $, °) and percent; and a sign before a number, joined
    to it, with every dash or minus written "-" (so "- 10%" and "−10%" are "-10%";
    an en or em dash counts as a sign only when it touches the digit).
    A dash right after a number, with only spaces between, is a range and becomes a
    space ("10–20" and "10 - 20" are "10 20"); after other punctuation it is a sign
    ("10, -5" keeps -5). Known cost, on the safe side: a spaced hyphen before a number
    in prose ("Acme - 2024") reads as a sign, so a quote dropping it is NOT_FOUND.
    """
    text = unicodedata.normalize("NFKC", text).casefold()
    out: list[str] = []
    gap = False        # punctuation or space skipped since the last kept character
    punct_gap = False  # ...and some of it was not space

    def emit(ch: str) -> None:
        nonlocal gap, punct_gap
        if gap and out:
            out.append(" ")
        out.append(ch)
        gap = punct_gap = False

    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        cat = unicodedata.category(ch)
        after_number = bool(out) and out[-1].isdigit() and not punct_gap
        # A spaced en or em dash before a number is a separator ("the score — 10").
        if (cat == "Pd" or ch in _SIGNS) and not after_number:
            j = i + 1
            while ch in _SPACED_SIGNS and j < n and text[j].isspace():
                j += 1
            if j < n and text[j].isdigit():
                emit(ch if ch in "+±" else "-")
                i = j
                continue
        if ch in ".," and out and out[-1].isdigit() and not gap and i + 1 < n \
                and text[i + 1].isdigit():
            out.append(ch)
        elif cat[0] in "LMNS" or ch == "%":
            emit(ch)
        else:
            gap = True
            punct_gap = punct_gap or not ch.isspace()
        i += 1
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
    """Visible text: no comments, attributes, scripts or styles; inline tags join words.

    <sup> and <sub> join too, so CO<sub>2</sub> is CO2 and m<sup>2</sup> is m2, the same
    as NFKC makes of CO₂ and m². A reference marker is dropped and leaves a word
    boundary instead: a <sup>/<sub> that holds an in-page link (<a href="#...">) or is
    marked as a note (role doc-noteref; class reference, footnote, noteref or cite).
    An unmarked <sup>1</sup> after a word can't be told from m<sup>2</sup>, so it
    joins; a quote that omits it is NOT_FOUND, never falsely SUPPORTED.
    """

    SKIP = frozenset({"script", "style", "noscript", "template"})
    BLOCK = frozenset({"address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
                       "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5",
                       "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
                       "table", "td", "th", "tr", "ul"})
    SCRIPT_POS = frozenset({"sup", "sub"})
    NOTE_CLASSES = ("reference", "footnote", "noteref", "cite")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0
        self.marks: list[tuple[list[bool], list[str]]] = []  # open sup/sub: (is_note, text)

    @classmethod
    def _is_note(cls, attrs) -> bool:
        values = {k: (v or "").lower() for k, v in attrs}
        return values.get("role") == "doc-noteref" \
            or any(c in values.get("class", "") for c in cls.NOTE_CLASSES)

    def _out(self) -> list[str]:
        return self.marks[-1][1] if self.marks else self.parts

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in self.SCRIPT_POS:
            self.marks.append(([self._is_note(attrs)], []))
        elif tag == "a" and self.marks and (
                (dict(attrs).get("href") or "").startswith("#") or self._is_note(attrs)):
            self.marks[-1][0][0] = True
        elif tag in self.BLOCK:
            self._out().append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag in self.SCRIPT_POS and self.marks:
            (is_note,), text = self.marks.pop()
            self._out().extend([" "] if is_note else text)
        elif tag in self.BLOCK:
            self._out().append(" ")

    def handle_data(self, data):
        if not self.skipping:
            self._out().append(data)

    def text(self) -> str:
        while self.marks:  # an unclosed <sup>/<sub> keeps its text
            _, text = self.marks.pop()
            self._out().extend(text)
        return "".join(self.parts)


def html_text(body: str) -> str:
    """Visible text of an HTML page."""
    parser = _Visible()
    parser.feed(body)
    parser.close()
    return parser.text()


def pdf_text(data: bytes, page: int | None = None) -> str:
    """Text of a PDF, or of one 1-based page. A page past the end raises."""
    reader = PdfReader(io.BytesIO(data))
    if page is None:
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    if page > len(reader.pages):
        raise PageMissingError(f"page {page} is past the end of a {len(reader.pages)}-page PDF")
    return reader.pages[page - 1].extract_text() or ""


def _encoding(data: bytes, charset: str | None, is_html: bool) -> str:
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    if charset:
        return charset
    return (_meta_charset(data[:4096]) if is_html else None) or "utf-8"


class _MetaCharset(HTMLParser):
    """The first real <meta> encoding declaration; comments and other attributes ignored."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.charset: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag != "meta" or self.charset:
            return
        values = {k: (v or "") for k, v in attrs}
        if values.get("charset", "").strip():
            self.charset = values["charset"].strip()
        elif values.get("http-equiv", "").lower() == "content-type":
            match = _CONTENT_CHARSET.search(values.get("content", ""))
            self.charset = match.group(1) if match else None


def _meta_charset(head: bytes) -> str | None:
    """Declared encoding in an HTML page's first bytes (read as ASCII, as browsers do)."""
    parser = _MetaCharset()
    parser.feed(head.decode("ascii", "replace"))
    return parser.charset


def _decode(data: bytes, page: int | None, *, is_html: bool, charset: str | None = None) -> str:
    """Source bytes as text. Undecodable bytes raise; they are never replaced and graded."""
    if data[:5] == b"%PDF-":
        return pdf_text(data, page)
    if page is not None:
        raise PageMissingError("citation has a page, but the source is not a PDF")
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


@dataclass(frozen=True)
class _Source:
    """A citation's source bytes, or why they could not be read whole."""

    data: bytes | None
    is_html: bool = False
    charset: str | None = None
    reason: str = ""
    blocked: bool = False   # the server refused us
    gone: bool = False      # the file is missing, or the server says 404/410


def _load(citation: Citation, root: str | Path, network: bool,
          fetcher: Fetcher | None) -> _Source:
    if citation.is_url:
        if not network:
            return _Source(None, reason="offline; pass network=True")
        if fetcher is None:
            raise ConfigError("network=True needs a Fetcher")
        try:
            resp = fetcher._get(citation.source)
        except FetchError as exc:
            return _Source(None, reason=f"not loaded: {exc}")
        if resp.code in BLOCKED_CODES:
            return _Source(None, reason=f"HTTP {resp.code}", blocked=True)
        if resp.code != 200:
            return _Source(None, reason=f"source not loaded whole: HTTP {resp.code}",
                           gone=resp.code in GONE_CODES)
        return _Source(resp.body, "html" in resp.content_type, resp.charset)
    path = _inside(Path(root), citation.source)
    if not path.is_file():
        return _Source(None, reason=f"file missing: {citation.source}", gone=True)
    return _Source(path.read_bytes(), path.suffix.lower() in (".html", ".htm"))


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
    src = _load(citation, root, network, fetcher)
    if src.data is None:
        return QuoteResult(Status.BLOCKED if src.blocked else Status.NOT_CHECKED, src.reason)
    text = _decode(src.data, citation.page, is_html=src.is_html, charset=src.charset)
    if not normalize(text):
        raise EmptySourceError(f"no text extracted from {citation.source}")
    if quote_in(citation.quote, text):
        return QuoteResult(Status.SUPPORTED)
    return QuoteResult(Status.NOT_FOUND, "source read; quote not in it")
