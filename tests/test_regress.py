import json
import sys

import pytest

from citecheck.regress import CheckerError, compare, counts, main, run_checker

KEY = [
    {"id": "A1", "first_grade": "❌", "final_grade": "✅"},   # wrong before
    {"id": "A2", "first_grade": "✅", "final_grade": "✅"},   # right before
    {"id": "A3", "first_grade": "❓", "final_grade": "❌"},   # wrong before
    {"id": "A4", "first_grade": "unknown", "final_grade": "❌"},
    {"id": "A5", "first_grade": "✅", "final_grade": "✅"},
]


def test_each_outcome():
    got = compare(KEY, {"A1": "✅", "A2": "❌", "A3": "✅", "A4": "❌"})
    assert [r.outcome for r in got] == ["better", "worse", "same", "new", "missing"]
    assert counts(got) == {"better": 1, "worse": 1, "same": 1, "new": 1, "missing": 1,
                           "right": 2, "wrong": 2}
    assert got[4].right is None  # a row the checker skipped is neither right nor wrong


def test_baseline_grades_replace_the_before_field():
    got = compare(KEY[:2], {"A1": "✅", "A2": "✅"}, before={"A1": "✅"})
    assert [r.outcome for r in got] == ["same", "new"]


@pytest.mark.parametrize(("key", "grades", "error"), [
    ([], {"A1": "✅"}, ValueError),
    (KEY, {}, CheckerError),
    (KEY, {"Z9": "✅"}, CheckerError),
    ([{"id": "A1", "final_grade": "✅"}, {"id": "A1", "final_grade": "❌"}], {"A1": "✅"}, ValueError),
    ([{"first_grade": "❌", "final_grade": "✅"}], {"A1": "✅"}, ValueError),
    ([{"id": "A1", "final_grade": ""}], {"A1": "✅"}, ValueError),
])
def test_fails_loudly(key, grades, error):
    with pytest.raises(error):
        compare(key, grades)


def checker(tmp_path, body):
    script = tmp_path / "grade.py"
    script.write_text("import json, sys\n" + body)
    return [sys.executable, str(script)]


def test_checker_reads_rows_on_stdin_and_runs_in_cwd(tmp_path):
    cmd = checker(tmp_path, "import os\nfor line in sys.stdin:\n"
                            "    r = json.loads(line)\n"
                            "    print(json.dumps({'id': r['id'], 'grade': os.path.basename(os.getcwd())}))\n")
    (tmp_path / "work").mkdir()
    assert run_checker(cmd, KEY[:2], cwd=tmp_path / "work") == {"A1": "work", "A2": "work"}


@pytest.mark.parametrize(("body", "match"), [
    ("sys.exit(3)", "exited 3"),
    ("print('not json')", "not JSON"),
    ("print(json.dumps({'id': 'A1'}))", "string id and grade"),
    ("print(json.dumps({'id': 'A1', 'grade': 'x'}))\nprint(json.dumps({'id': 'A1', 'grade': 'y'}))", "twice"),
    ("import time\ntime.sleep(5)", "did not run"),
])
def test_checker_failures_raise(tmp_path, body, match):
    with pytest.raises(CheckerError, match=match):
        run_checker(checker(tmp_path, body), KEY, timeout=1)


def test_cli_writes_results_and_exits_on_worse_or_missing(tmp_path, capsys):
    key = tmp_path / "key.jsonl"
    key.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in KEY))
    cmd = checker(tmp_path, "for line in sys.stdin:\n    r = json.loads(line)\n"
                            "    print(json.dumps({'id': r['id'], 'grade': r['final_grade']}))\n")
    out = tmp_path / "run1.jsonl"
    assert main([str(key), "--out", str(out), "--", *cmd]) == 0
    assert "better 2  worse 0  same 2  new 1  missing 0  right 5  wrong 0" in capsys.readouterr().out
    flip = checker(tmp_path, "for line in sys.stdin:\n    r = json.loads(line)\n"
                             "    if r['id'] != 'A5': print(json.dumps({'id': r['id'], 'grade': '❓'}))\n")
    assert main([str(key), "--baseline", str(out), "--", *flip]) == 1
    printed = capsys.readouterr().out
    assert "worse    A1" in printed and "missing  A5" in printed
