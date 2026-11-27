"""Print rules files: ``python -m citecheck.rules protocol tiers``."""
from __future__ import annotations

import sys

from . import NAMES, read


def main(argv: list[str] | None = None) -> int:
    names = sys.argv[1:] if argv is None else argv
    if not names or any(n not in NAMES for n in names):
        print(f"usage: citecheck-rules {{{','.join(NAMES)}}} ...", file=sys.stderr)
        return 2
    sys.stdout.write("\n".join(read(n) for n in names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
