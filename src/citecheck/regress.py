"""Re-run a labelled answer key and report, row by row, what got better or worse.

A key is JSON Lines: one row per case, each with an ``id`` and the label it should
get (``final_grade`` by default). A checker is any command: it reads the key's rows
on stdin, one JSON object per line, and prints one ``{"id": ..., "grade": ...}`` per
line for the rows it graded. Each row is then compared with the grade it had before
(a field of the row, ``first_grade`` by default, or an earlier run's results):

    better    wrong before, right now
    worse     right before, wrong now
    same      right both times, or wrong both times
    new       no grade before to compare with; right or wrong is still reported
    missing   the checker gave no grade: never counted as right

"No grade" is an empty string, "unknown", "omitted" or "⚪" (unchecked), before and
now alike. Counts are reported per outcome and never folded into one score. The key
is checked before the checker runs. An empty key, a checker that grades nothing, an
id the key does not have, one graded twice, or a baseline that is malformed or
shares no id with the key raises.

    python -m citecheck.regress key.jsonl -- uv run grade.py
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

OUTCOMES = ("better", "worse", "same", "new", "missing")
NO_GRADE = frozenset({"", "unknown", "omitted", "⚪"})
TIMEOUT = 600.0  # seconds a checker may run unless the caller says otherwise


def _graded(value) -> str | None:
    """The grade, or None when ``value`` is no grade at all."""
    if not isinstance(value, str) or value.strip().casefold() in NO_GRADE:
        return None
    return value


class CheckerError(Exception):
    """The checker failed, or printed something that is not a grade per line."""


@dataclass(frozen=True)
class RowResult:
    id: str
    expected: str
    before: str | None   # None: no earlier grade to compare with
    after: str | None    # None: the checker did not grade this row
    outcome: str

    @property
    def right(self) -> bool | None:
        return None if self.after is None else self.after == self.expected


def _records(text: str):
    """Non-blank JSON Lines records with their line numbers. Only "\n" ends a record:
    U+2028 and the like are valid inside a JSON string."""
    for number, line in enumerate(text.split("\n"), 1):
        if line.strip():
            yield number, line.rstrip("\r")


def read_jsonl(path: str | Path) -> list[dict]:
    """Rows of a JSON Lines file; a bad line raises naming its number."""
    rows = []
    for number, line in _records(Path(path).read_text(encoding="utf-8")):
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{path} line {number}: not JSON: {exc}") from None
        if not isinstance(row, dict):
            raise ValueError(f"{path} line {number}: not a JSON object")  # noqa: TRY004 - a bad record, like bad JSON
        rows.append(row)
    return rows


def check_key(key: list[dict], expect: str = "final_grade") -> None:
    """Raise unless every row has a unique id and an ``expect`` label: before any checker runs."""
    if not key:
        raise ValueError("the answer key is empty")
    ids = [row.get("id") for row in key]
    if not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("every key row needs a unique, non-empty string id")
    for row in key:
        if _graded(row.get(expect)) is None:
            raise ValueError(f"key row {row['id']} has no {expect!r} label")


def read_baseline(path: str | Path, key: list[dict]) -> dict[str, str | None]:
    """An earlier run's --out file as {id: grade or None}; malformed or unrelated raises."""
    rows = read_jsonl(path)
    if not rows:
        raise ValueError(f"baseline {path} is empty")
    grades: dict[str, str | None] = {}
    for number, row in enumerate(rows, 1):
        if not isinstance(row.get("id"), str) or "after" not in row \
                or not (row["after"] is None or isinstance(row["after"], str)):
            raise ValueError(f"baseline {path} row {number} is not a result row (id, after)")
        if row["id"] in grades:
            raise ValueError(f"baseline {path} has {row['id']!r} twice")
        grades[row["id"]] = row["after"]
    if not set(grades) & {row["id"] for row in key}:
        raise ValueError(f"baseline {path} shares no id with the key")
    return grades


def _grades(lines: list[dict], source: str) -> dict[str, str]:
    grades: dict[str, str] = {}
    for number, row in enumerate(lines, 1):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) \
                or not isinstance(row.get("grade"), str):
            raise CheckerError(f"{source} line {number}: needs a string id and grade: {row!r}")
        if row["id"] in grades:
            raise CheckerError(f"{source} graded {row['id']!r} twice")
        grades[row["id"]] = row["grade"]
    return grades


def compare(key: list[dict], grades: dict[str, str], *, expect: str = "final_grade",
            before: str | dict[str, str | None] = "first_grade") -> list[RowResult]:
    """One result per key row. ``before`` is a field name in each row, or earlier grades."""
    check_key(key, expect)
    if not any(_graded(g) is not None for g in grades.values()):
        raise CheckerError("the checker graded no rows")
    if unknown := sorted(set(grades) - {row["id"] for row in key}):
        raise CheckerError(f"the checker graded ids the key does not have: {unknown[:5]}")
    results = []
    for row in key:
        expected = row[expect]
        prior = _graded(before.get(row["id"]) if isinstance(before, dict) else row.get(before))
        after = _graded(grades.get(row["id"]))
        if after is None:
            outcome = "missing"
        elif prior is None:
            outcome = "new"
        else:
            was, now = prior == expected, after == expected
            outcome = "better" if now and not was else "worse" if was and not now else "same"
        results.append(RowResult(row["id"], expected, prior, after, outcome))
    return results


def run_checker(command: list[str], key: list[dict], *, cwd: str | Path | None = None,
                timeout: float = TIMEOUT) -> dict[str, str]:
    """Run ``command`` with the key rows on stdin; its grades by id.

    The checker runs in its own process group, and on timeout the whole group is
    killed, so a wrapper (``uv run ...``) cannot leave its child working.
    """
    if not command:
        raise ValueError("no checker command given")
    stdin = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in key)
    try:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", cwd=cwd,
                                start_new_session=True)
    except OSError as exc:
        raise CheckerError(f"checker did not run: {exc}") from exc
    try:
        stdout, stderr = proc.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise CheckerError(f"checker did not finish within {timeout} seconds; stopped it") \
            from None
    if proc.returncode != 0:
        raise CheckerError(f"checker exited {proc.returncode}: {stderr.strip()[-500:]}")
    lines = []
    for number, line in _records(stdout):
        try:
            lines.append(json.loads(line))
        except ValueError:
            raise CheckerError(f"checker output line {number} is not JSON: {line[:200]!r}") \
                from None
    return _grades(lines, "checker output")


def write_results(path: str | Path, results: list[RowResult]) -> None:
    """Write results as JSON Lines, replacing ``path`` only once the whole file is written."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.writelines(json.dumps(asdict(r), ensure_ascii=False) + "\n" for r in results)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)  # governance: allow-delete DS001: our own unfinished temp file
        raise


def counts(results: list[RowResult]) -> dict[str, int]:
    """Rows per outcome, every outcome listed; plus right and wrong among the graded rows."""
    tally = Counter(r.outcome for r in results)
    out = {name: tally.get(name, 0) for name in OUTCOMES}
    out["right"] = sum(r.right is True for r in results)
    out["wrong"] = sum(r.right is False for r in results)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m citecheck.regress", description=(
        "Re-run an answer key through a checker and report better/worse/same per row."))
    parser.add_argument("key", help="the answer key, JSON Lines with an id per row")
    parser.add_argument("command", nargs="+", help="the checker command (after --)")
    parser.add_argument("--expect", default="final_grade", help="field with the right label")
    parser.add_argument("--before", default="first_grade",
                        help="field with the earlier grade (ignored with --baseline)")
    parser.add_argument("--baseline", help="an earlier run's --out file to compare against")
    parser.add_argument("--cwd", help="directory to run the checker in")
    parser.add_argument("--timeout", type=float, default=TIMEOUT,
                        help=f"seconds to allow the checker (default {TIMEOUT:g})")
    parser.add_argument("--out", help="write this run's per-row results here (JSON Lines)")
    args = parser.parse_args(argv)

    key = read_jsonl(args.key)
    check_key(key, args.expect)
    before: str | dict[str, str | None] = args.before
    if args.baseline:
        before = read_baseline(args.baseline, key)
    grades = run_checker(args.command, key, cwd=args.cwd, timeout=args.timeout)
    results = compare(key, grades, expect=args.expect, before=before)
    if args.out:
        write_results(args.out, results)
    for r in results:
        print(f"{r.outcome:7}  {r.id}  expected {r.expected}, was {r.before}, now {r.after}")
    print("  ".join(f"{name} {n}" for name, n in counts(results).items()))
    return 1 if any(r.outcome in ("worse", "missing") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
