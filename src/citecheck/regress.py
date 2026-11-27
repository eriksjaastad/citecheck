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

Counts are reported per outcome and never folded into one score. An empty key, a
checker that grades nothing, an id the key does not have, or one graded twice raises.

    python -m citecheck.regress key.jsonl -- uv run grade.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

OUTCOMES = ("better", "worse", "same", "new", "missing")
NO_GRADE = {"", "unknown"}


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


def read_jsonl(path: str | Path) -> list[dict]:
    """Rows of a JSON Lines file; a bad line raises naming its number."""
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path} line {number}: not JSON: {exc}") from None
            if not isinstance(row, dict):
                raise ValueError(f"{path} line {number}: not a JSON object")
            rows.append(row)
    return rows


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
            before: str | dict[str, str] = "first_grade") -> list[RowResult]:
    """One result per key row. ``before`` is a field name in each row, or earlier grades."""
    if not key:
        raise ValueError("the answer key is empty")
    ids = [row.get("id") for row in key]
    if not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("every key row needs a unique, non-empty string id")
    if not grades:
        raise CheckerError("the checker graded no rows")
    if unknown := sorted(set(grades) - set(ids)):
        raise CheckerError(f"the checker graded ids the key does not have: {unknown[:5]}")
    results = []
    for row in key:
        expected = row.get(expect)
        if not isinstance(expected, str) or expected in NO_GRADE:
            raise ValueError(f"key row {row['id']} has no {expect!r} label")
        prior = before.get(row["id"]) if isinstance(before, dict) else row.get(before)
        prior = None if not isinstance(prior, str) or prior in NO_GRADE else prior
        after = grades.get(row["id"])
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
                timeout: float | None = None) -> dict[str, str]:
    """Run ``command`` with the key rows on stdin; its grades by id."""
    if not command:
        raise ValueError("no checker command given")
    stdin = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in key)
    try:
        done = subprocess.run(command, input=stdin, capture_output=True, text=True, cwd=cwd,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckerError(f"checker did not run: {exc}") from exc
    if done.returncode != 0:
        raise CheckerError(f"checker exited {done.returncode}: {done.stderr.strip()[-500:]}")
    lines = []
    for number, line in enumerate(done.stdout.splitlines(), 1):
        if line.strip():
            try:
                lines.append(json.loads(line))
            except ValueError:
                raise CheckerError(f"checker output line {number} is not JSON: {line[:200]!r}") \
                    from None
    return _grades(lines, "checker output")


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
    parser.add_argument("--timeout", type=float, help="seconds to allow the checker")
    parser.add_argument("--out", help="write this run's per-row results here (JSON Lines)")
    args = parser.parse_args(argv)

    key = read_jsonl(args.key)
    before: str | dict[str, str] = args.before
    if args.baseline:
        before = {r["id"]: r["after"] for r in read_jsonl(args.baseline)
                  if isinstance(r.get("after"), str)}
    results = compare(key, run_checker(args.command, key, cwd=args.cwd, timeout=args.timeout),
                      expect=args.expect, before=before)
    if args.out:
        Path(args.out).write_text("".join(json.dumps(asdict(r), ensure_ascii=False) + "\n"
                                          for r in results), encoding="utf-8")
    for r in results:
        if r.outcome in ("better", "worse", "missing"):
            print(f"{r.outcome:7}  {r.id}  expected {r.expected}, was {r.before}, now {r.after}")
    print("  ".join(f"{name} {n}" for name, n in counts(results).items()))
    return 1 if any(r.outcome in ("worse", "missing") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
