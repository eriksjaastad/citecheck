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
    normalize,
    quote_in,
)

UA = "citecheck-tests/0"


def fetcher():
    return Fetcher(UA, min_interval=0)


@given(st.text(min_size=1), st.integers(0, 50), st.integers(1, 80))
def test_any_slice_of_a_text_is_found_in_it(text, start, length):
    quote = text[start:start + length]
    if not normalize(quote) or not normalize(text):
        return
    assert quote_in(quote, text, min_fragment=1)


@pytest.mark.parametrize("quote, text", [
    ("Москва не сразу строилась", "Говорят, Москва не сразу строилась."),
    ("東京は日本の首都です", "東京は日本の首都です。人口は多い。"),
    ("“Employees: approx. 450”", "Employees:   Approx. 450 (2024)"),
])
def test_any_script_and_spacing(quote, text):
    assert quote_in(quote.strip("“”"), text)


def test_ellipsis_fragments_must_all_appear_in_order():
    text = "We will cut taxes for families. Later, we will balance the budget."
    assert quote_in("cut taxes for families … balance the budget", text)
    assert quote_in("cut taxes for families [...] balance the budget", text)
    assert not quote_in("balance the budget ... cut taxes for families", text)
    assert not quote_in("cut taxes for families ... raise the minimum wage", text)


def test_empty_quote_or_text_raises():
    with pytest.raises(ValueError):
        quote_in(" ... ", "some page text")
    with pytest.raises(EmptySourceError):
        quote_in("anything at all here", "  \n ")


def test_local_text_file(tmp_path):
    (tmp_path / "notes.txt").write_text("The bridge opened in 1932 to traffic.")
    assert check_quote(Citation("notes.txt", "bridge opened in 1932"), root=tmp_path).status \
        is Status.SUPPORTED
    assert check_quote(Citation("notes.txt", "bridge closed in 1932"), root=tmp_path).status \
        is Status.NOT_FOUND
    assert check_quote(Citation("gone.txt", "bridge opened in 1932"), root=tmp_path).status \
        is Status.NOT_CHECKED


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


def test_no_quote_is_not_checked(tmp_path):
    assert check_quote(Citation("a.txt"), root=tmp_path).status is Status.NOT_CHECKED


def test_offline_by_default(no_network):
    result = check_quote(Citation("https://example.org/x", "words that would be checked"))
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


@pytest.mark.parametrize("path, status", [
    ("/forbidden", Status.BLOCKED), ("/limited", Status.BLOCKED),
    ("/gone", Status.NOT_CHECKED), ("/broken", Status.NOT_CHECKED),
])
def test_web_refusals_are_never_not_found(server, path, status):
    cite = Citation(f"{server}{path}", "words that would be checked")
    assert check_quote(cite, network=True, fetcher=fetcher()).status is status


def test_page_with_no_text_raises(server):
    with pytest.raises(EmptySourceError):
        check_quote(Citation(f"{server}/blank", "words that would be checked"),
                    network=True, fetcher=fetcher())
