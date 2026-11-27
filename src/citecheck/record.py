"""The claim-and-citation record: one shape for a claim and the evidence behind it.

Fields come from the projects that already keep one: a file hash, quote and page
(a ledger-backed citation record), sources with a checked date, and a source tier.
Confidence is a label, not a number, so nobody averages it.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields
from datetime import date
from typing import Any

from .status import Status

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Citation:
    """Where a claim's evidence is: a URL or a path, the exact words, and where in it.

    ``page`` is 1-based and limits the quote check to that PDF page. ``line`` is a
    locator for readers only. ``sha256`` binds a local file's bytes at citing time;
    ``fetched`` is the date a URL was read.
    """

    source: str
    quote: str = ""
    page: int | None = None
    line: int | None = None
    sha256: str | None = None
    fetched: date | None = None
    tier: str | None = None

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("citation source is empty")
        for name in ("page", "line"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or value < 1):
                raise ValueError(f"citation {name} must be 1 or more, got {value!r}")
        if self.sha256 is not None and not _SHA256.match(self.sha256):
            raise ValueError("citation sha256 must be 64 lowercase hex characters")

    @property
    def is_url(self) -> bool:
        return self.source.startswith(("http://", "https://"))


@dataclass(frozen=True)
class Claim:
    """A claim, its citations and its grade. A new claim starts NOT_CHECKED."""

    text: str
    citations: tuple[Citation, ...] = ()
    status: Status = Status.NOT_CHECKED
    confidence: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("claim text is empty")
        if not isinstance(self.status, Status):
            raise TypeError(f"claim status must be a Status, got {self.status!r}")

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict; ``from_dict`` reads it back unchanged."""
        data = asdict(self)
        data["status"] = self.status.value
        for cite in data["citations"]:
            if cite["fetched"] is not None:
                cite["fetched"] = cite["fetched"].isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Claim:
        """Inverse of ``to_dict``. Unknown keys raise, so a typo never drops a field."""
        _no_extra(data, cls)
        cites = []
        for raw in data.get("citations", ()):
            _no_extra(raw, Citation)
            raw = dict(raw)
            if raw.get("fetched") is not None:
                raw["fetched"] = date.fromisoformat(raw["fetched"])
            cites.append(Citation(**raw))
        return cls(**{**data, "citations": tuple(cites),
                      "status": Status(data.get("status", Status.NOT_CHECKED.value))})


def _no_extra(data: dict[str, Any], kind: type) -> None:
    extra = set(data) - {f.name for f in fields(kind)}
    if extra:
        raise ValueError(f"unknown {kind.__name__} fields: {sorted(extra)}")
