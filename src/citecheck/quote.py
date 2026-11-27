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


@dataclass(frozen=True)
class QuoteResult:
    status: Status
    reason: str = ""


def normalize(text: str) -> str:
    """NFKC-fold and casefold; punctuation and spacing collapse to single spaces.

    Kept, because each can change a claim's meaning: letters with their combining
    marks (दिन is not दीन); numbers with their decimal point, a leading one too (.5 is
    not 5), and their thousands commas (50,000 is not 50); symbols (+, $, °) and
    percent; and signs, with every dash or minus written "-":
    - a sign before a number or a currency amount joins it ("- 10%" and "−10%" are
      "-10%"; "-$24.99" keeps its minus); an en or em dash counts only when it touches;
    - a minus right after a number, before a space or punctuation, stays on it
      ("24.99-", a trailing negative);
    - parentheses around an amount (a currency symbol, or exactly two decimals) stay
      on it, spaces inside dropped: "( $ 100.00 )" is "($100.00)".
    A dash right after a number, with only spaces between, is a range and becomes a
    space ("10–20" and "10 - 20" are "10 20"); after other punctuation it is a sign
    ("10, -5" keeps -5). A comma between digits is a thousands separator only before
    exactly three digits, after at most three that are not a decimal fraction, so the
    fields of "7084,2025" or "24.99,100" stay apart.
    Known costs, on the safe side (a quote reads NOT_FOUND): a spaced hyphen before a
    number in prose ("Acme - 2024") reads as a sign; "100,200" read as one number; a
    hyphen ending a line after a number ("20-" then "year") stays on it. Not covered:
    a decimal comma ("3,5") reads as two numbers, so "3" is found in it.
    """
    text = unicodedata.normalize("NFKC", text).casefold()
    out: list[str] = []
    gap = False        # punctuation or space skipped since the last kept character
    punct_gap = False  # ...and some of it was not space
    close_at = -1      # index of the ")" closing an amount in parentheses

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
        touching_number = bool(out) and out[-1].isdigit() and not gap
        after_number = bool(out) and out[-1].isdigit() and not punct_gap
        if i == close_at:
            out.append(ch)
            gap = punct_gap = False
        elif i < close_at and ch.isspace():
            pass
        elif ch == "(" and (close := _amount_close(text, i)) is not None:
            emit(ch)
            close_at = close
        # A spaced en or em dash before a number is a separator ("the score — 10").
        elif (cat == "Pd" or ch in _SIGNS) and not after_number \
                and (j := _signed(text, i)) is not None:
            emit(ch if ch in "+±" else "-")
            i = j
            continue
        elif (cat == "Pd" or ch == "−") and touching_number and _trailing(text, i):
            out.append("-")
        elif touching_number and (ch == "." and _digit_at(text, i + 1)
                                  or ch == "," and _thousands(text, i)):
            out.append(ch)
        elif cat[0] in "LMNS" or ch == "%" or _leading_point(text, i):
            emit(ch)
        else:
            gap = True
            punct_gap = punct_gap or not ch.isspace()
        i += 1
    return "".join(out)


def _digit_at(text: str, j: int) -> bool:
    return j < len(text) and text[j].isdigit()


def _number_at(text: str, j: int) -> bool:
    """A number starts at ``j``: a digit, or a decimal point and a digit."""
    return _digit_at(text, j) or (j < len(text) and text[j] == "." and _digit_at(text, j + 1))


def _leading_point(text: str, i: int) -> bool:
    """A decimal point that starts a number (".5"), not one ending a word or an ellipsis."""
    return text[i] == "." and _digit_at(text, i + 1) \
        and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "."))


def _skip_spaces(text: str, j: int) -> int:
    while j < len(text) and text[j].isspace():
        j += 1
    return j


def _amount_at(text: str, j: int) -> bool:
    """An amount starts at ``j``: a number, or a currency symbol and then one."""
    if j < len(text) and unicodedata.category(text[j]) == "Sc":
        j = _skip_spaces(text, j + 1)
    return _number_at(text, j)


def _signed(text: str, i: int) -> int | None:
    """Where the signed amount after the sign at ``i`` resumes, or None if it is no sign."""
    j = _skip_spaces(text, i + 1) if text[i] in _SPACED_SIGNS else i + 1
    return j if _amount_at(text, j) else None


def _trailing(text: str, i: int) -> bool:
    """A minus touching the number before it, not a range ("$10-$20") or a hyphenated word."""
    nxt = i + 1
    if nxt < len(text) and text[nxt].isalnum():
        return False
    return not _amount_at(text, _skip_spaces(text, nxt))


def _thousands(text: str, i: int) -> bool:
    """Is the comma at ``i`` a thousands separator: three digits after, at most three before?"""
    j = i + 1
    while _digit_at(text, j):
        j += 1
    if j - i - 1 != 3:
        return False
    k = i - 1
    while k >= 0 and text[k].isdigit():
        k -= 1
    return i - 1 - k <= 3 and not (k >= 0 and text[k] == ".")


def _amount_close(text: str, i: int) -> int | None:
    """Index of the ")" closing an amount opened at ``i``: "($1,234)", "( -100.00 )"."""
    j = _skip_spaces(text, i + 1)
    if j < len(text) and (text[j] in _SIGNS or unicodedata.category(text[j]) == "Pd"):
        j = _skip_spaces(text, j + 1)
    money = j < len(text) and unicodedata.category(text[j]) == "Sc"
    if money:
        j = _skip_spaces(text, j + 1)
    start = j
    while _digit_at(text, j) or (j > start and text[j:j + 1] in (".", ",")
                                 and _digit_at(text, j + 1)):
        j += 1
    number = text[start:j]
    if not number or not number[0].isdigit():
        return None
    money = money or (len(number) > 3 and number[-3] == "." and number[-2:].isdigit())
    j = _skip_spaces(text, j)
    return j if money and j < len(text) and text[j] == ")" else None


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
        raise ValueError(f"page {page} is past the end of a {len(reader.pages)}-page PDF")
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
