"""Has a cited source changed since it was cited?

A citation records the source's sha256 when it was cited. Two different things can
happen to that source later, and only one of them loses the evidence: its bytes can
move on while the quoted words stay (a maintained file was edited), or the quoted
words can go. They come back as different answers so a caller can re-bind the first
and must re-review the second.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .fetch import Fetcher
from .quote import EmptySourceError, PageMissingError, _decode, _load, normalize, quote_in
from .record import Citation


class Drift(str, Enum):
    UNCHANGED = "unchanged"      # same bytes as when cited
    HASH = "hash"                # bytes changed; the quote is still there
    QUOTE = "quote"              # the quote, or its page, is no longer in the source
    MISSING = "missing"          # the file is gone, or the server says 404/410
    NOT_CHECKED = "not_checked"  # offline, refused, or not loaded whole


@dataclass(frozen=True)
class DriftResult:
    status: Drift
    reason: str = ""


def check_drift(citation: Citation, *, root: str | Path = ".", network: bool = False,
                fetcher: Fetcher | None = None) -> DriftResult:
    """Compare a source with the sha256 and quote recorded when it was cited.

    The quote is checked first: a lost quote is QUOTE even when the bytes also moved.
    A citation whose quote is "" is checked by hash alone. These raise rather than
    guess: no sha256 (nothing to compare against), a quote with no words in it, and a
    source with no text to look in (EmptySourceError: a scanned PDF or a script-built
    page is unreadable, not proof the quote went). Local paths resolve under ``root``.
    """
    if citation.sha256 is None:
        raise ValueError(f"citation of {citation.source} has no sha256 to compare")
    if citation.quote and not normalize(citation.quote):
        raise ValueError(f"citation of {citation.source} has a quote with no words in it")
    src = _load(citation, root, network, fetcher)
    if src.data is None:
        return DriftResult(Drift.MISSING if src.gone else Drift.NOT_CHECKED, src.reason)
    if normalize(citation.quote):
        try:
            text = _decode(src.data, citation.page, is_html=src.is_html, charset=src.charset)
        except PageMissingError as exc:
            return DriftResult(Drift.QUOTE, str(exc))
        if not normalize(text):
            raise EmptySourceError(f"no text extracted from {citation.source}")
        if not quote_in(citation.quote, text):
            return DriftResult(Drift.QUOTE, "quote not in the source now")
    if hashlib.sha256(src.data).hexdigest() != citation.sha256:
        kept = "; the quote is still there" if normalize(citation.quote) else ""
        return DriftResult(Drift.HASH, f"bytes changed{kept}")
    return DriftResult(Drift.UNCHANGED)
