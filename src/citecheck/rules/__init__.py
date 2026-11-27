"""Rules an agent loads before fact-checking: the protocol and the source tiers.

    python -m citecheck.rules protocol      # or: citecheck-rules tiers
"""
from __future__ import annotations

from importlib.resources import files

NAMES = ("protocol", "tiers")


def read(name: str) -> str:
    """The text of one rules file. An unknown name raises, never an empty string."""
    if name not in NAMES:
        raise KeyError(f"no rules file {name!r}; choose one of {', '.join(NAMES)}")
    return files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8")
