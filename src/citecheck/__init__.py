"""Mechanical checks that a claim matches its cited source. Offline unless asked."""
from .fetch import ConfigError, Fetcher, FetchError, LinkResult, LinkStatus, check_link
from .quote import EmptySourceError, QuoteResult, check_quote, normalize, quote_in
from .record import Citation, Claim
from .status import Status, UnknownLabelError, map_label, tally

__all__ = [
    "Citation", "Claim", "ConfigError", "EmptySourceError", "FetchError", "Fetcher",
    "LinkResult", "LinkStatus", "QuoteResult", "Status", "UnknownLabelError",
    "check_link", "check_quote", "map_label", "normalize", "quote_in", "tally",
]
