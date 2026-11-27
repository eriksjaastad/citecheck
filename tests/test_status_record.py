from datetime import date

import pytest

from citecheck import Citation, Claim, Status, UnknownLabelError, map_label, tally

LABELS = {"✅": Status.SUPPORTED, "❌": Status.CONTRADICTED, "⚪": Status.NOT_CHECKED}


def test_status_set_matches_the_design():
    assert {s.value for s in Status} == {
        "supported", "contradicted", "partly", "not_found", "not_checked", "blocked", "disputed"}


def test_map_label_maps_and_refuses_unknown():
    assert map_label("✅", LABELS) is Status.SUPPORTED
    with pytest.raises(UnknownLabelError):
        map_label("❓", LABELS)
    with pytest.raises(TypeError):
        map_label("x", {"x": "supported"})


def test_tally_counts_every_status_and_refuses_empty():
    counts = tally([Status.SUPPORTED, Status.NOT_CHECKED, Status.NOT_CHECKED])
    assert counts[Status.NOT_CHECKED] == 2 and counts[Status.BLOCKED] == 0
    assert len(counts) == len(Status)
    with pytest.raises(ValueError):
        tally([])


def test_new_claim_is_not_checked():
    assert Claim("The sky is green").status is Status.NOT_CHECKED


@pytest.mark.parametrize("kwargs", [
    {"source": " "}, {"source": "a.pdf", "page": 0}, {"source": "a.pdf", "line": -1},
    {"source": "a.pdf", "page": True}, {"source": "a.pdf", "sha256": "ABC"},
])
def test_citation_rejects_bad_fields(kwargs):
    with pytest.raises(ValueError):
        Citation(**kwargs)


def test_claim_round_trips_and_rejects_unknown_keys():
    claim = Claim("Rent rose 4%", (Citation("https://example.org/r", "rent rose by four percent",
                                            fetched=date(2026, 11, 1), tier="primary"),),
                  Status.PARTLY, confidence="medium", notes="regional figure only")
    assert Claim.from_dict(claim.to_dict()) == claim
    with pytest.raises(ValueError):
        Claim.from_dict({**claim.to_dict(), "score": 0.8})
    bad_cite = {**claim.to_dict()["citations"][0], "url": "x"}
    with pytest.raises(ValueError):
        Claim.from_dict({**claim.to_dict(), "citations": [bad_cite]})
