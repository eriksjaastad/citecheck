import re
import subprocess
import zipfile

import pytest

from citecheck import Status, rules
from citecheck.rules.__main__ import main


@pytest.mark.parametrize("name", rules.NAMES)
def test_each_file_loads(name):
    assert rules.read(name).startswith("# ")


def test_unknown_name_raises():
    with pytest.raises(KeyError):
        rules.read("nope")


def test_protocol_names_all_seven_failure_modes():
    text = rules.read("protocol")
    assert re.findall(r"^### (F\d):", text, re.MULTILINE) == [f"F{i}" for i in range(1, 8)]


def test_protocol_grades_are_the_status_set():
    text = rules.read("protocol")
    rows = re.findall(r"^\| ([A-Z_]+) \|", text, re.MULTILINE)
    assert sorted(rows) == sorted(s.name for s in Status)


def test_tiers_lists_each_tier():
    text = rules.read("tiers")
    assert [t for t in ("S1", "S2", "S3", "Forum") if f"| {t} |" in text] == [
        "S1", "S2", "S3", "Forum"]


def test_cli_prints_files(capsys):
    assert main(["tiers", "protocol"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Source tiers") and "# Fact-check protocol" in out


@pytest.mark.parametrize("argv", [[], ["nope"], ["tiers", "nope"]])
def test_cli_rejects_bad_names(argv, capsys):
    assert main(argv) == 2
    assert capsys.readouterr().out == ""


def test_installed_wheel_ships_the_files(tmp_path):
    subprocess.run(["uv", "build", "--wheel", "-o", str(tmp_path)], check=True,
                   capture_output=True)
    wheel = next(tmp_path.glob("*.whl"))
    names = zipfile.ZipFile(wheel).namelist()
    assert {"citecheck/rules/protocol.md", "citecheck/rules/tiers.md"} <= set(names)
