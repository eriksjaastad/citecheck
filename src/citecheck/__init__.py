"""Mechanical checks that a claim matches its cited source. Offline unless asked."""
from .doi import DoiResult, DoiStatus, check_doi
from .drift import Drift, DriftResult, check_drift
from .fetch import (
    ConfigError,
    Fetcher,
    FetchError,
    LinkResult,
    LinkStatus,
    RequestCapError,
    check_link,
)
from .quote import (
    EmptySourceError,
    PageMissingError,
    QuoteResult,
    check_quote,
    normalize,
    quote_in,
)
from .record import Citation, Claim
from .status import Status, UnknownLabelError, map_label, tally

__all__ = [
    "Citation", "Claim", "ConfigError", "DoiResult", "DoiStatus", "Drift", "DriftResult",
    "EmptySourceError", "FetchError", "Fetcher", "LinkResult", "LinkStatus",
    "PageMissingError", "QuoteResult", "RequestCapError", "Status", "UnknownLabelError",
    "check_doi", "check_drift", "check_link", "check_quote", "map_label", "normalize",
    "quote_in", "tally",
]
