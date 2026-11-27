import hashlib

import pytest
from conftest import ROUTES, make_pdf

from citecheck import Citation, Drift, EmptySourceError, Fetcher, check_drift

PDF = make_pdf(["Cover page", "Revenue rose sharply in Q3"])


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cite(tmp_path, data: bytes, quote="", name="note.txt", **kw):
    (tmp_path / name).write_bytes(data)
    return Citation(name, quote, sha256=sha(data), **kw)


def test_unchanged(tmp_path):
    c = cite(tmp_path, b"annual entry for 2025", "annual entry")
    assert check_drift(c, root=tmp_path).status is Drift.UNCHANGED


def test_edited_file_keeping_the_quote_is_hash(tmp_path):
    c = cite(tmp_path, b"annual entry for 2025", "annual entry")
    (tmp_path / "note.txt").write_bytes(b"annual entry for 2025\nappended later")
    r = check_drift(c, root=tmp_path)
    assert (r.status, r.reason) == (Drift.HASH, "bytes changed; the quote is still there")


def test_lost_quote_is_quote_even_though_bytes_moved(tmp_path):
    c = cite(tmp_path, b"annual entry for 2025", "annual entry")
    (tmp_path / "note.txt").write_bytes(b"annual entries for 2025")  # whole words only
    assert check_drift(c, root=tmp_path).status is Drift.QUOTE


@pytest.mark.parametrize(("name", "data"), [
    ("note.txt", b"  \n"), ("page.html", b"<html><script>render()</script></html>"),
    ("scan.pdf", make_pdf([""])),
])
@pytest.mark.parametrize("edited", [False, True])
def test_a_source_with_no_text_raises(tmp_path, name, data, edited):
    """Unreadable is not proof the quote went, with the hash unchanged or not."""
    c = cite(tmp_path, data if not edited else b"annual entry", "annual entry", name)
    (tmp_path / name).write_bytes(data)
    with pytest.raises(EmptySourceError):
        check_drift(c, root=tmp_path)


@pytest.mark.parametrize("quote", [" ", "...", " … "])
def test_a_quote_with_no_words_raises(tmp_path, quote):
    with pytest.raises(ValueError, match="no words"):
        check_drift(cite(tmp_path, b"annual entry", quote), root=tmp_path)


def test_missing_file(tmp_path):
    c = cite(tmp_path, b"annual entry", "annual entry")
    (tmp_path / "note.txt").rename(tmp_path / "moved.txt")
    assert check_drift(c, root=tmp_path).status is Drift.MISSING


def test_no_quote_is_checked_by_hash_alone(tmp_path):
    c = cite(tmp_path, b"v1")
    assert check_drift(c, root=tmp_path).status is Drift.UNCHANGED
    (tmp_path / "note.txt").write_bytes(b"v2")
    assert check_drift(c, root=tmp_path).reason == "bytes changed"


def test_no_sha256_raises(tmp_path):
    (tmp_path / "note.txt").write_bytes(b"x")
    with pytest.raises(ValueError, match="no sha256"):
        check_drift(Citation("note.txt", "x"), root=tmp_path)


@pytest.mark.parametrize(("pages", "drift"), [
    (["Cover page", "Revenue rose sharply in Q3", "Appendix"], Drift.HASH),
    (["Revenue rose sharply in Q3", "Cover page"], Drift.QUOTE),  # moved off page 2
    (["Revenue rose sharply in Q3"], Drift.QUOTE),                 # page 2 is gone
])
def test_pdf_page(tmp_path, pages, drift):
    c = cite(tmp_path, PDF, "revenue rose sharply", "doc.pdf", page=2)
    assert check_drift(c, root=tmp_path).status is Drift.UNCHANGED
    (tmp_path / "doc.pdf").write_bytes(make_pdf(pages))
    assert check_drift(c, root=tmp_path).status is drift


def test_paged_citation_of_a_file_no_longer_pdf_is_quote(tmp_path):
    c = cite(tmp_path, PDF, "revenue rose sharply", "doc.pdf", page=2)
    (tmp_path / "doc.pdf").write_bytes(b"Revenue rose sharply in Q3")
    assert check_drift(c, root=tmp_path).status is Drift.QUOTE


def test_url_offline_is_not_checked(no_network):
    c = Citation("https://example.org/a", "x", sha256=sha(b"x"))
    assert check_drift(c).status is Drift.NOT_CHECKED


@pytest.mark.parametrize(("path", "drift"), [
    ("/page", Drift.UNCHANGED), ("/gone", Drift.MISSING), ("/forbidden", Drift.NOT_CHECKED),
    ("/short", Drift.NOT_CHECKED),
])
def test_url(server, path, drift):
    body = ROUTES.get(path, (0, "", b""))[2]
    c = Citation(f"{server}{path}", "voted seven to two", sha256=sha(body))
    f = Fetcher("citecheck-tests/0", min_interval=0)
    assert check_drift(c, network=True, fetcher=f).status is drift


def test_csv_is_read_by_cell(tmp_path):
    c = cite(tmp_path, b"REI 7084,2025-01-15,-24.99\n", "REI 7084", "rows.csv")
    assert check_drift(c, root=tmp_path).status is Drift.UNCHANGED
    (tmp_path / "rows.csv").write_bytes(b"REI 7084,2025-01-15,24.99\n")
    assert check_drift(c, root=tmp_path).status is Drift.HASH
    assert check_drift(Citation("rows.csv", "-24.99", sha256=c.sha256), root=tmp_path).status \
        is Drift.QUOTE
