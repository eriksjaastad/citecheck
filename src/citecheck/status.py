"""The shared status set, and the one way a project's own labels map onto it.

Every project grades claims with its own labels (emoji, PASS/NOT_ASSESSED, verdict
words). They share these seven outcomes. Keeping them distinct is the point:
"not checked" is never a pass, and "blocked" is never "not found".
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from enum import Enum


class Status(str, Enum):
    SUPPORTED = "supported"        # checked, and the source supports the claim
    CONTRADICTED = "contradicted"  # checked, and the source says otherwise
    PARTLY = "partly"              # checked; supports some of the claim, not all
    NOT_FOUND = "not_found"        # searched or read the source, nothing supports it
    NOT_CHECKED = "not_checked"    # no check ran (offline, no quote, no source)
    BLOCKED = "blocked"            # the source refused us: paywall, 401, 403, 429, 451
    DISPUTED = "disputed"          # sources or checkers disagree


class UnknownLabelError(KeyError):
    """A project label with no entry in its mapping. Raised, never defaulted."""


def map_label(label: str, mapping: Mapping[str, Status]) -> Status:
    """The shared status for one of a project's labels.

    A label missing from ``mapping`` raises: silently treating it as anything,
    least of all a pass, is the failure this package exists to prevent.
    """
    try:
        status = mapping[label]
    except KeyError:
        raise UnknownLabelError(f"label {label!r} is not mapped to a Status") from None
    if not isinstance(status, Status):
        raise TypeError(f"label {label!r} maps to {status!r}, not a Status")
    return status


def tally(statuses: Iterable[Status]) -> dict[Status, int]:
    """Count of each status, every status present (zero included). Never a score.

    An empty input raises: a run that checked nothing has no tally to report. So does
    anything that is not a Status (a LinkStatus, a raw label), rather than vanish.
    """
    items = list(statuses)
    for item in items:
        if not isinstance(item, Status):
            raise TypeError(f"tally counts Status values only, got {item!r}")
    counts = Counter(items)
    if not counts:
        raise ValueError("no results to tally")
    return {s: counts.get(s, 0) for s in Status}
