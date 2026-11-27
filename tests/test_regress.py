import json
import sys

import pytest

from citecheck.regress import (
    CheckerError,
    check_key,
    compare,
    counts,
    main,
    read_baseline,
    run_checker,
    write_results,
)

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
    ("import time\ntime.sleep(5)", "did not finish"),
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


@pytest.mark.parametrize("absent", ["", "unknown", "Unknown", " omitted ", "⚪"])
def test_no_grade_markers_are_absent_before_and_now(absent):
    key = [{"id": "A1", "first_grade": absent, "final_grade": "✅"},
           {"id": "A2", "first_grade": "❌", "final_grade": "✅"}]
    got = compare(key, {"A1": "✅", "A2": absent})
    assert [r.outcome for r in got] == ["new", "missing"]
    with pytest.raises(CheckerError, match="graded no rows"):
        compare(key, {"A1": absent, "A2": absent})


def test_key_is_checked_before_the_checker_runs(tmp_path):
    marker = tmp_path / "ran"
    cmd = checker(tmp_path, f"open({str(marker)!r}, 'w').write('x')\n")
    key = tmp_path / "key.jsonl"
    key.write_text(json.dumps({"id": "A1", "final_grade": "✅"}) + "\n")
    with pytest.raises(ValueError, match="no 'verdict' label"):
        main([str(key), "--expect", "verdict", "--", *cmd])
    assert not marker.exists()
    with pytest.raises(ValueError):
        check_key([])


def test_timeout_stops_a_wrapped_checker_and_its_child(tmp_path):
    marker = tmp_path / "late"
    child = tmp_path / "child.py"
    child.write_text(f"import time\ntime.sleep(1.5)\nopen({str(marker)!r}, 'w').write('x')\n")
    wrapper = [sys.executable, "-c",
               f"import subprocess, sys; subprocess.run([sys.executable, {str(child)!r}])"]
    with pytest.raises(CheckerError, match="did not finish"):
        run_checker(wrapper, KEY, timeout=0.5)
    import time
    time.sleep(2)
    assert not marker.exists()


def test_unicode_line_separators_inside_strings_are_kept(tmp_path):
    claim = "a\u2028b\u2029c\u0085d"
    key = tmp_path / "key.jsonl"
    key.write_text(json.dumps({"id": "A1", "final_grade": "✅", "claim": claim},
                              ensure_ascii=False) + "\n", encoding="utf-8")
    cmd = checker(tmp_path, "for line in sys.stdin.read().split('\\n'):\n"
                            "    if line.strip():\n        r = json.loads(line)\n"
                            "        print(json.dumps({'id': r['id'], 'grade': r['final_grade'], "
                            "'echo': r['claim']}, ensure_ascii=False))\n")
    out = tmp_path / "run.jsonl"
    assert main([str(key), "--out", str(out), "--", *cmd]) == 0
    assert read_baseline(out, [{"id": "A1"}]) == {"A1": "✅"}


@pytest.mark.parametrize("content", ["", '{"id": "A1", "grade": "✅"}\n',
                                     '{"id": "A1", "after": "✅"}\n{"id": "A1", "after": "❌"}\n',
                                     '{"id": "Z9", "after": "✅"}\n'])
def test_bad_baselines_raise(tmp_path, content):
    path = tmp_path / "base.jsonl"
    path.write_text(content)
    with pytest.raises(ValueError):
        read_baseline(path, KEY)


def test_partial_baseline_with_a_missing_row_is_fine(tmp_path):
    path = tmp_path / "base.jsonl"
    path.write_text('{"id": "A1", "after": "❌"}\n{"id": "A2", "after": null}\n')
    base = read_baseline(path, KEY)
    got = compare(KEY, {"A1": "✅", "A2": "✅", "A3": "❌"}, before=base)
    assert [r.outcome for r in got[:3]] == ["better", "new", "new"]


def test_failed_write_keeps_the_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "run.jsonl"
    path.write_text("previous\n")
    results = compare(KEY, {"A1": "✅"})

    class Boom(Exception):
        pass

    def fail(*args, **kwargs):
        raise Boom
    monkeypatch.setattr("citecheck.regress.os.replace", fail)
    with pytest.raises(Boom):
        write_results(path, results)
    assert path.read_text() == "previous\n"
    assert [p.name for p in tmp_path.iterdir()] == ["run.jsonl"]


@pytest.mark.parametrize(("grades", "code"), [
    ({"A1": "✅", "A2": "✅", "A3": "❌", "A4": "❌", "A5": "✅"}, 0),   # control
    ({"A1": "✅", "A2": "❌", "A3": "❌", "A4": "❌", "A5": "✅"}, 1),   # worse only
    ({"A1": "✅", "A2": "✅", "A3": "❌", "A4": "❌"}, 1),               # missing only
])
def test_exit_code_per_condition_and_every_row_reported(tmp_path, capsys, grades, code):
    key = tmp_path / "key.jsonl"
    key.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in KEY))
    cmd = checker(tmp_path, f"G = {grades!r}\nfor line in sys.stdin:\n    r = json.loads(line)\n"
                            "    if r['id'] in G: print(json.dumps({'id': r['id'], 'grade': G[r['id']]}))\n")
    assert main([str(key), "--", *cmd]) == code
    printed = capsys.readouterr().out
    assert all(f"  {row['id']}  " in printed for row in KEY)
