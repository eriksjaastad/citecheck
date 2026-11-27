import codecs
import io

import pytest
from conftest import make_pdf
from hypothesis import given
from hypothesis import strategies as st

from citecheck import (
    Citation,
    ConfigError,
    EmptySourceError,
    Fetcher,
    Status,
    check_quote,
    quote_in,
)

UA = "citecheck-tests/0"
WORD = st.text(st.characters(categories=["Ll", "Lu", "Nd"]), min_size=1, max_size=8)
ASCII_WORD = st.text("abcdefghij0123456789", min_size=1, max_size=8)


def fetcher():
    return Fetcher(UA, min_interval=0)


@given(st.lists(WORD, min_size=1, max_size=12), st.data())
def test_any_run_of_whole_words_is_found(words, data):
    start = data.draw(st.integers(0, len(words) - 1))
    end = data.draw(st.integers(start + 1, len(words)))
    sep = data.draw(st.sampled_from([" ", ", ", "\n", " — ", "; "]))
    assert quote_in(" ".join(words[start:end]), sep.join(words) + ".")


@given(ASCII_WORD, st.text("abcdefghij0123456789", min_size=1, max_size=3), st.booleans())
def test_part_of_a_word_or_number_is_never_found(word, extra, before):
    longer = extra + word if before else word + extra
    assert not quote_in(f"said {word} today", f"He said {longer} today.")


@pytest.mark.parametrize("quote, text", [
    ("Москва не сразу строилась", "Говорят, Москва не сразу строилась."),
    ("東京は日本の首都です", "東京は日本の首都です。人口は多い。"),
    ("首都", "東京は日本の首都です。"),
    ("Employees: approx. 450", "Employees:   Approx. 450 (2024)"),
    ("café est ouvert", "Le café est ouvert"),  # decomposed é, NFKC-equal
    ("pages 10–20", "see pages 10-20"),
    ("Net change was -10%", "Net change was −10%."),
    ("Net change was −10%", "Net change was - 10%."),
    ("Revenue reached 50.5 million", "Revenue reached 50.5 million."),
    ("Revenue reached 50,000 dollars", "Revenue reached 50,000 dollars."),
    ("It cost 50", "It cost 50. Then it rose."),
    ("the score 10 points", "the score — 10 points"),
    ("Values were 10, -5, and 3", "Values were 10, -5, and 3"),
    ("-5", "Values were 10, -5, and 3"),
    ("pages 10 20", "pages 10 - 20"),
    ("pages 10 20", "pages 10—20"),
])
def test_matches_that_must_pass(quote, text):
    assert quote_in(quote, text)


@pytest.mark.parametrize("quote, text", [
    ("Revenue rose 4%", "Revenue rose 40%."),
    ("revenue reached 50", "The revenue reached 500 million."),
    ("legal activity", "illegal activity"),
    ("Net change was +10%", "Net change was -10%"),
    ("Net change was 10%", "Net change was -10%"),
    ("Net change was 10%", "Net change was - 10%."),
    ("Net change was 10%", "Net change was −10%."),
    ("Revenue reached 50", "Revenue reached 50.5 million."),
    ("Revenue reached 50", "Revenue reached 50,000 dollars."),
    ("Values were 10, 5, and 3", "Values were 10, -5, and 3"),
    ("rose 10 and 5%", "rose 10 and -5%"),
    ("temps 10, 5", "temps 10, −5"),
    ("दिन", "दीन"),
    ("দিন", "দান"),
])
def test_matches_that_must_fail(quote, text):
    assert not quote_in(quote, text)


@pytest.mark.parametrize("quote, found", [
    ("cut taxes for families … balance the budget", True),
    ("cut taxes for families [...] balance the budget", True),
    ("We … balance", True),                        # all-short fragments still match
    ("not … balance the budget", False),           # a short negation is never dropped
    ("balance the budget ... cut taxes for families", False),  # order matters
    ("cut taxes for families ... raise the minimum wage", False),
])
def test_ellipsis_fragments(quote, found):
    text = "We will cut taxes for families. Later, we will balance the budget."
    assert quote_in(quote, text) is found


def test_empty_quote_or_text_raises(tmp_path):
    with pytest.raises(ValueError):
        quote_in(" ... ", "some page text")
    with pytest.raises(EmptySourceError):
        quote_in("anything", "  \n ")
    for quote in ("", "   ", "“ … ”"):
        for source in ("missing.txt", "https://example.org/x"):
            with pytest.raises(ValueError):
                check_quote(Citation(source, quote), root=tmp_path)


def test_local_text_file(tmp_path):
    (tmp_path / "notes.txt").write_text("The bridge opened in 1932 to traffic.")
    assert check_quote(Citation("notes.txt", "bridge opened in 1932"), root=tmp_path).status \
        is Status.SUPPORTED
    assert check_quote(Citation("notes.txt", "bridge closed in 1932"), root=tmp_path).status \
        is Status.NOT_FOUND
    assert check_quote(Citation("gone.txt", "bridge opened in 1932"), root=tmp_path).status \
        is Status.NOT_CHECKED


@pytest.mark.parametrize("name, data", [
    ("utf16.txt", codecs.BOM_UTF16_LE + "Le café est ouvert".encode("utf-16-le")),
    ("bom.txt", codecs.BOM_UTF8 + "Le café est ouvert".encode()),
    ("page.HTML", b'<meta charset="windows-1252"><p>Le caf\xe9 est ouvert</p>'),
    ("equiv.html", (b'<meta http-equiv="Content-Type" content="text/html; charset=windows-1252">'
                    b"<p>Le caf\xe9 est ouvert</p>")),
    ("inert.html", '<!-- <meta charset="windows-1252"> --><meta name="x" content="charset=latin1">'
                   '<meta charset="utf-8"><p>Le café est ouvert</p>'.encode()),
])
def test_declared_encodings(tmp_path, name, data):
    (tmp_path / name).write_bytes(data)
    assert check_quote(Citation(name, "café est ouvert"), root=tmp_path).status \
        is Status.SUPPORTED


def test_undecodable_bytes_raise(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"Le caf\xe9 est ouvert")
    with pytest.raises(ValueError):
        check_quote(Citation("bad.txt", "est ouvert"), root=tmp_path)


def test_html_hides_comments_attributes_and_scripts(tmp_path):
    (tmp_path / "p.html").write_text(
        '<p title="a > b secret attribute words">Shown text.</p>'
        "<!-- <p>commented out words</p> --><script>var s = 'script words';</script>"
        "<p><b>Un</b>employment rose</p>")
    for hidden in ("secret attribute words", "commented out words", "script words"):
        assert check_quote(Citation("p.html", hidden), root=tmp_path).status is Status.NOT_FOUND
    for shown in ("shown text", "unemployment rose"):
        assert check_quote(Citation("p.html", shown), root=tmp_path).status is Status.SUPPORTED


@pytest.mark.parametrize("html", [
    '<p>The treatment reduced mortality<sup><a href="#r1" role="doc-noteref">1</a></sup>.</p>',
    '<p>The treatment reduced mortality<sup><a href="#b1">1</a>,<a href="#b2">2</a></sup>.</p>',
    '<p>The treatment reduced mortality<sup class="reference">[<a href="#c">3</a>]</sup>.</p>',
    '<p>The treatment reduced mortality<sup role="doc-noteref">4</sup> in adults.</p>',
    "<p>The treatment reduced mortality[1] in adults.</p>",
    "<p>The <i>treat</i>ment reduced <b>mor</b>tality.</p>",
])
def test_reference_markers_and_inline_tags(tmp_path, html):
    (tmp_path / "p.html").write_text(html)
    assert check_quote(Citation("p.html", "The treatment reduced mortality"), root=tmp_path) \
        .status is Status.SUPPORTED


@pytest.mark.parametrize("html, whole, cut", [
    ("<p>The gas is CO<sub>2</sub> here.</p>", "The gas is CO₂", "The gas is CO"),
    ("<p>The gas is CO<sub>2</sub> here.</p>", "The gas is CO2", "The gas is CO"),
    ("<p>An area of 25 m<sup>2</sup> was used.</p>", "An area of 25 m²", "An area of 25 m"),
    ("<p>The count was 10<sup>6</sup> cells.</p>", "The count was 10⁶ cells", "The count was 10"),
    ("<p>She came 1<sup>st</sup> overall.</p>", "She came 1st overall", "She came 1"),
    ("<p>Drink H<sub>2</sub>O daily.</p>", "Drink H₂O daily", "Drink H"),
])
def test_sub_and_sup_notation_stays_joined(tmp_path, html, whole, cut):
    (tmp_path / "n.html").write_text(html)
    assert check_quote(Citation("n.html", whole), root=tmp_path).status is Status.SUPPORTED
    assert check_quote(Citation("n.html", cut), root=tmp_path).status is Status.NOT_FOUND


def test_aes_pdf_with_empty_password(tmp_path):
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf(["Revenue rose sharply"]))))
    writer.encrypt("", algorithm="AES-128")
    with open(tmp_path / "aes.pdf", "wb") as out:
        writer.write(out)
    assert check_quote(Citation("aes.pdf", "revenue rose sharply", page=1), root=tmp_path) \
        .status is Status.SUPPORTED


def test_local_pdf_by_page(tmp_path):
    (tmp_path / "r.pdf").write_bytes(make_pdf(["Cover page only", "Revenue rose sharply in Q3"]))
    assert check_quote(Citation("r.pdf", "revenue rose sharply", page=2), root=tmp_path).status \
        is Status.SUPPORTED
    assert check_quote(Citation("r.pdf", "revenue rose sharply", page=1), root=tmp_path).status \
        is Status.NOT_FOUND
    assert check_quote(Citation("r.pdf", "revenue rose sharply"), root=tmp_path).status \
        is Status.SUPPORTED
    with pytest.raises(ValueError):
        check_quote(Citation("r.pdf", "revenue rose sharply", page=3), root=tmp_path)


def test_page_on_a_non_pdf_raises(tmp_path):
    (tmp_path / "a.txt").write_text("plain words in a plain file")
    with pytest.raises(ValueError):
        check_quote(Citation("a.txt", "plain words in a plain", page=1), root=tmp_path)


def test_path_outside_root_raises(tmp_path):
    with pytest.raises(ValueError):
        check_quote(Citation("../secret.txt", "anything here at all"), root=tmp_path)


def test_offline_by_default(no_network):
    for url in ("https://example.org/x", "HTTPS://example.org/x"):
        result = check_quote(Citation(url, "words that would be checked"))
        assert result.status is Status.NOT_CHECKED and "offline" in result.reason


def test_network_without_fetcher_raises():
    with pytest.raises(ConfigError):
        check_quote(Citation("https://example.org/x", "words that would be checked"),
                    network=True)


def test_web_page_and_pdf(server):
    page = Citation(f"{server}/page", "voted seven to two to approve")
    assert check_quote(page, network=True, fetcher=fetcher()).status is Status.SUPPORTED
    hidden = Citation(f"{server}/page", "hidden words here")
    assert check_quote(hidden, network=True, fetcher=fetcher()).status is Status.NOT_FOUND
    pdf = Citation(f"{server}/doc.pdf", "revenue rose sharply", page=2)
    assert check_quote(pdf, network=True, fetcher=fetcher()).status is Status.SUPPORTED
    latin1 = Citation(f"{server}/cp1252", "café est ouvert")
    assert check_quote(latin1, network=True, fetcher=fetcher()).status is Status.SUPPORTED


@pytest.mark.parametrize("path, status", [
    ("/forbidden", Status.BLOCKED), ("/limited", Status.BLOCKED),
    ("/gone", Status.NOT_CHECKED), ("/broken", Status.NOT_CHECKED),
    ("/partial", Status.NOT_CHECKED),   # 206: not the whole source
    ("/short", Status.NOT_CHECKED),     # body ends before Content-Length
    ("/huge", Status.NOT_CHECKED),      # over the size limit, quote at the end
])
def test_unread_sources_are_never_not_found(server, path, status):
    cite = Citation(f"{server}{path}", "words that would be checked")
    assert check_quote(cite, network=True, fetcher=fetcher()).status is status


def test_page_with_no_text_raises(server):
    with pytest.raises(EmptySourceError):
        check_quote(Citation(f"{server}/blank", "words that would be checked"),
                    network=True, fetcher=fetcher())
