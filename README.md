# citecheck

Mechanical checks that a claim matches the source it cites. No model, no paid API,
and nothing leaves the machine unless you ask for it on that call.

- **Record:** `Claim` and `Citation`, one shape for a claim, its source, the exact
  quote, where in the source it sits, and how sure you are.
- **Status set:** seven statuses every project maps its own labels onto. A check that
  did not run is `NOT_CHECKED`; nothing is averaged into a score.
- **Quote check:** is the quoted passage in the source? Local text files, CSV by cell, PDFs by page,
  any script, and quotes cut with an ellipsis.
- **Link check:** does the link load? `BLOCKED` (401, 403, 429, 451) is kept separate
  from `NOT_FOUND` (404, 410).
- **DOI check:** does the DOI exist, and what title and journal does its record give?
  Crossref, then DataCite (arXiv, Zenodo), then the DOI registry itself.
- **Regression runner:** re-run a labelled answer key through any checker command and
  report each row as better, worse, same, new or missing. No single score.
- **Drift check:** has a cited file changed since it was cited? A moved hash (`HASH`)
  is kept apart from a lost quote or page (`QUOTE`) and a missing file (`MISSING`).

## Install

Pin a tag:

```
uv add "citecheck @ git+https://github.com/eriksjaastad/citecheck@v0.5.0"
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

## Regression runner

An answer key is JSON Lines with an `id` and the right label per row (`final_grade` by
default). The checker is any command: it reads the rows on stdin and prints one
`{"id": ..., "grade": ...}` per line. Each row is compared with its earlier grade
(`first_grade`, or an earlier run's `--out` file given as `--baseline`):

```
python -m citecheck.regress key.jsonl --out run.jsonl --cwd ../grader -- uv run grade.py
```

It exits 1 when any row got worse or was not graded. A row the checker skips, or grades
`""`, `unknown`, `omitted` or `⚪`, is `missing`, never right; the same markers as an
earlier grade make the row `new`. The key is checked before the checker runs; the
checker gets 600 seconds (`--timeout`) and is stopped with everything it started. An
empty key, an empty checker output, an id the key does not have, or a malformed
baseline raises.

## Rules for agents

The fact-check protocol (seven failure modes, a source in every row, the grades) and the
source tiers (S1 primary, S2 professional, S3 secondary, forums for experience only) ship as
files an agent reads before checking. A project with no Python can load them pinned:

```
uvx --from git+https://github.com/eriksjaastad/citecheck@v0.5.0 citecheck-rules protocol tiers
```

In Python: `citecheck.rules.read("tiers")`. An unknown name raises.

## Judgment panel (optional)

Is the claim true, given evidence the mechanical checks already passed? Run those checks
first: the panel judges whatever evidence it is handed. Researchers from
different model families judge each claim against the same evidence without seeing each
other; a critic from a further family then tries to falsify a SUPPORTED or CONTRADICTED
result, and a falsified result is `DISPUTED`. Nothing else in citecheck calls it.

```
python -m citecheck.panel claims.jsonl --network    # rows: {"id", "claim", "evidence"}
python -m citecheck.panel claims.jsonl --mock       # offline dry run
```

- `--network` uses subscription CLIs: `claude`, `grok` and `agy` research, `codex` criticises.
  In Python, build any panel from `cli_judge`, `mock_judge` or `openrouter_judge`; the last is
  the only paid judge and needs `max_usd`, a cap per judge checked before each call (the last
  call can pass it by one reply of `max_tokens`); a reply without a usable cost raises.
- Grok and Antigravity cannot run with no tools: grok keeps only `read_file`, agy runs with
  `--sandbox --mode plan`, and every judge starts in an empty temporary folder.
- Every row is checked before the first call, and results print as each claim finishes.
  Any judge failing makes that claim `NOT_CHECKED`; researchers splitting `SUPPORTED` and
  `CONTRADICTED`, or tying, is `DISPUTED`. A run stops at 20 calls (`--max-calls`), and every
  claim after the cap comes back `NOT_CHECKED`, never dropped.
- Output rows carry `grade`, so the panel is a checker for the regression runner:
  `python -m citecheck.regress key.jsonl -- python -m citecheck.panel - --network`.

## Develop

```
uv run pytest
uv run ruff check
```

MIT licensed.
