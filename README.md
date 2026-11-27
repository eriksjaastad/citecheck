# citecheck

Mechanical checks that a claim matches the source it cites. No model, no paid API,
and nothing leaves the machine unless you ask for it on that call.

- **Record:** `Claim` and `Citation`, one shape for a claim, its source, the exact
  quote, where in the source it sits, and how sure you are.
- **Status set:** seven statuses every project maps its own labels onto. A check that
  did not run is `NOT_CHECKED`; nothing is averaged into a score.
- **Quote check:** is the quoted passage in the source? Local text files, PDFs by page,
  any script, and quotes cut with an ellipsis.
- **Link check:** does the link load? `BLOCKED` (401, 403, 429, 451) is kept separate
  from `NOT_FOUND` (404, 410).
- **DOI check:** does the DOI exist, and what title and journal does its record give?
  Crossref, then DataCite (arXiv, Zenodo), then the DOI registry itself.
- **Drift check:** has a cited file changed since it was cited? A moved hash (`HASH`)
  is kept apart from a lost quote or page (`QUOTE`) and a missing file (`MISSING`).

## Install

Pin a tag:

```
uv add "citecheck @ git+https://github.com/eriksjaastad/citecheck@v0.2.0"
```

## Use

```python
from citecheck import (Citation, Fetcher, Status, check_doi, check_drift, check_link,
                       check_quote, map_label)

cite = Citation(source="docs/report.pdf", quote="revenue rose … in the third quarter", page=4)
check_quote(cite, root=".").status          # Status.SUPPORTED, or NOT_FOUND

web = Citation(source="https://example.org/page", quote="the exact words")
check_quote(web).status                     # Status.NOT_CHECKED: offline by default

fetcher = Fetcher(USER_AGENT)               # from your config, with your contact address
check_quote(web, network=True, fetcher=fetcher).status
check_link("https://example.org/page", network=True, fetcher=fetcher).status  # LinkStatus

LABELS = {"✅": Status.SUPPORTED, "❌": Status.CONTRADICTED, "⚪": Status.NOT_CHECKED}
map_label("✅", LABELS)                      # unknown labels raise, never default

check_doi("10.1038/nature12373", network=True, fetcher=fetcher)  # DoiResult: status, title, journal
check_drift(Citation("data/rules.yaml", "the quoted rule", sha256=SHA_WHEN_CITED)).status  # Drift
```

Network checks need a `Fetcher`, and a `Fetcher` needs a user agent; either missing
raises `ConfigError`. One `Fetcher` per run spaces requests to the same host, redirect
hops included; `Fetcher(USER_AGENT, cache=True)` also fetches each URL only once, keeping
error answers and failures as they came, and `max_requests=N` stops the run with
`RequestCapError` at the cap. A DOI is `NOT_FOUND` only when the DOI registry itself
has no record of it, and `REGISTERED`, not `FOUND`, when it exists but no record gives
a title to compare. A drift check raises on a source with no text, like a quote check. Checking a citation with no quote raises `ValueError`. A source
that loads but yields no text raises `EmptySourceError` rather than calling the quote
missing. A quote against a source that refused us is `BLOCKED`, against one that is
gone or unreachable `NOT_CHECKED`, never `NOT_FOUND`.

## Develop

```
uv run pytest
uv run ruff check
```

MIT licensed.
