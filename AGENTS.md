# AGENTS.md - citecheck

This is a public repo. Never add personal data, contact addresses or the names of
private projects: not in code, tests, fixtures, commits or docs.

citecheck is a shared library. Projects install it pinned to a version tag
(`citecheck @ git+https://github.com/eriksjaastad/citecheck@v0.3.0`), so every
change a caller can notice ships as a new tag, and a tag is never moved.

## Rules the code keeps

- Offline by default: anything that touches the network takes `network=True` per call.
- No paid API is required for any check.
- Mechanical checks only; a model-based layer, if one is ever added, runs after them.
- Results are never averaged into one score, and a check that did not run is
  `Status.NOT_CHECKED`, never a pass.
- Fail loudly: a missing user agent, an empty quote or a source with no text raises.

## Work

- `uv run pytest` and `uv run ruff check` before every PR.
- Run `pt memory search "citecheck"` before starting a card.
