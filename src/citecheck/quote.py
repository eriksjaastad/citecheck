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
import csv
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
    marks (दिन is not दीन); digits with every "." or "," between them (50 is not 50.5,
    50,000, 3,5 or 1,00,000) and a leading decimal point (.5 is not 5); symbols (+, $,
    °) and percent, a currency symbol joined to the number after it ("$ 5" is "$5").

    Where a mark might belong to a number, it stays on it, so a quote that drops it
    is NOT_FOUND rather than falsely found:
    - a dash touching a digit before it stays ("10-20", "24.99-", "2025-01-15");
    - a sign touching a number or amount after it stays ("-5", "-$24.99", "COVID-19",
      and "100.00 -24.99", two values, not a range), every dash or minus written "-";
      "-", "+", "±" and "−" stay with spaces before the number too ("- 10%" is "-10%"),
      even right after another number: "$100.00 - $24.99" may be two cells, so
      "10 - 20" is "10 -20" and a quote must keep the dash. A spaced en or em dash is
      a sign only before money ("– $24.99", "— 5.00"), else a separator ("10 – 20"
      is "10 20");
    - parentheses around an amount (a currency symbol, a leading point, two decimals
      or a thousands comma) stay on it, spaces inside dropped: "( $ 1 234.56 )" is
      "($1234.56)". Parentheses around other numbers, "(2024)", are spacing.
    A CSV source is read cell by cell (see ``_decode``), so commas between fields are
    never mistaken for commas inside a number.
    """
    text = unicodedata.normalize("NFKC", text).casefold()
    out: list[str] = []
    gap = False        # punctuation or space skipped since the last kept character
    close_at = -1      # index of the ")" closing an amount in parentheses

    def emit(ch: str) -> None:
        nonlocal gap
        if gap and out:
            out.append(" ")
        out.append(ch)
        gap = False

    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        cat = unicodedata.category(ch)
        touching_number = bool(out) and out[-1].isdigit() and not gap
        dash = cat == "Pd" or ch in _SIGNS
        if i == close_at:
            out.append(ch)
            gap = True
        elif i < close_at and ch.isspace():
            pass
        elif ch == "(" and (close := _amount_close(text, i)) is not None:
            gap = True
            emit(ch)
            close_at = close
        elif dash and touching_number:
            out.append(ch if ch in "+±" else "-")
        elif dash and (j := _signed(text, i)) is not None:
            emit(ch if ch in "+±" else "-")
            i = j
            continue
        elif ch in ".," and touching_number and _digit_at(text, i + 1):
            out.append(ch)
        elif cat == "Sc":
            emit(ch)
            j = _skip_spaces(text, i + 1)
            if _number_at(text, j):
                i = j
                continue
        elif cat[0] in "LMNS" or ch == "%" or _leading_point(text, i):
            emit(ch)
        else:
            gap = True
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


def _signed(text: str, i: int) -> int | None:
    """Where the amount after the sign at ``i`` starts, or None if no amount follows.

    "-", "+", "±" and "−" may have spaces before the number. A spaced en or em dash is
    a sign only before money (a currency symbol, or a number with a decimal point):
    "– $24.99" is a typeset minus, "10 – 20" a range.
    """
    j = _skip_spaces(text, i + 1)
    if j > i + 1 and text[i] not in _SPACED_SIGNS and not _money_at(text, j):
        return None
    return j if _amount_at(text, j) else None


def _amount_at(text: str, j: int) -> bool:
    """A number starts at ``j``, or a currency symbol and then one."""
    if j < len(text) and unicodedata.category(text[j]) == "Sc":
        j = _skip_spaces(text, j + 1)
    return _number_at(text, j)


def _money_at(text: str, j: int) -> bool:
    """An amount at ``j`` that reads as money: a currency symbol, or a decimal point."""
    if j < len(text) and unicodedata.category(text[j]) == "Sc":
        return _amount_at(text, j)
    while _digit_at(text, j) or (text[j:j + 1] == "," and _digit_at(text, j + 1)):
        j += 1
    return text[j:j + 1] == "." and _digit_at(text, j + 1)


def _amount_close(text: str, i: int) -> int | None:
    """Index of the ")" closing an amount opened at ``i``, or None.

    An amount has a currency symbol (before or after), a leading point, two decimals or
    a thousands comma: "($1,234)", "( -100.00 )", "(€1 234.56)", "(100.00 €)", "($.50)".
    """
    j = _skip_spaces(text, i + 1)
    if j < len(text) and (text[j] in _SIGNS or unicodedata.category(text[j]) == "Pd"):
        j = _skip_spaces(text, j + 1)
    money = j < len(text) and unicodedata.category(text[j]) == "Sc"
    if money:
        j = _skip_spaces(text, j + 1)
    start = j
    while j < len(text):
        if text[j].isdigit() or (text[j] in ".," and _digit_at(text, j + 1)):
            j += 1
        elif text[j] == " " and j > start and re.match(r" \d{3}(?!\d)", text[j:]):
            j += 1  # "1 234": a space between thousands
        else:
            break
    number = text[start:j]
    if not any(c.isdigit() for c in number):
        return None
    k = _skip_spaces(text, j)
    if k < len(text) and unicodedata.category(text[k]) == "Sc":
        money = True
        k = _skip_spaces(text, k + 1)
    money = money or number[0] == "." or "," in number or re.search(r"\.\d\d$", number)
    return k if money and k < len(text) and text[k] == ")" else None


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


def csv_text(text: str) -> str:
    """A CSV file's text, one cell per tab-separated field, so field commas stay apart."""
    try:
        return "\n".join("\t".join(row) for row in csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise ValueError(f"source is not valid CSV: {exc}") from exc


def _decode(data: bytes, page: int | None, *, is_html: bool, charset: str | None = None,
            is_csv: bool = False) -> str:
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
    if is_csv:
        return csv_text(text)
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
                       is_csv=resp.content_type == "text/csv",
                       charset=resp.charset)
    else:
        path = _inside(Path(root), citation.source)
        if not path.is_file():
            return QuoteResult(Status.NOT_CHECKED, f"file missing: {citation.source}")
        text = _decode(path.read_bytes(), citation.page,
                       is_html=path.suffix.lower() in (".html", ".htm"),
                       is_csv=path.suffix.lower() == ".csv")
    if not normalize(text):
        raise EmptySourceError(f"no text extracted from {citation.source}")
    if quote_in(citation.quote, text):
        return QuoteResult(Status.SUPPORTED)
    return QuoteResult(Status.NOT_FOUND, "source read; quote not in it")
