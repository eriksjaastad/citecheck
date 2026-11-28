import io
import json
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st

from citecheck import ConfigError, Status
from citecheck import panel as P
from citecheck.panel import (
    JudgeError,
    SpendCapError,
    cli_judge,
    judge_claim,
    mock_judge,
    openrouter_judge,
    run_panel,
)


def say(verdict, reason="r"):
    return json.dumps({"verdict": verdict, "reason": reason})


def crit(falsified, reason="c"):
    return json.dumps({"falsified": falsified, "reason": reason})


STANDS = crit(False)


def panel(*verdicts, critic=STANDS):
    researchers = [mock_judge(f"fam{i}", say(v) if v in P.VERDICTS else v)
                   for i, v in enumerate(verdicts)]
    return researchers, mock_judge("critic-fam", critic)


def run(*verdicts, critic=STANDS):
    researchers, c = panel(*verdicts, critic=critic)
    return judge_claim("c1", "the claim", "the evidence", researchers, c)


def test_unanimous_and_critic_cannot_falsify():
    r = run("supported", "supported", "supported")
    assert r.status is Status.SUPPORTED and r.calls == 4
    assert r.critic.falsified is False and [v.status for v in r.votes] == [Status.SUPPORTED] * 3


def test_critic_falsifies_makes_it_disputed():
    r = run("contradicted", "contradicted", "contradicted", critic=crit(True))
    assert r.status is Status.DISPUTED and r.critic.falsified


def test_majority_still_goes_to_the_critic():
    r = run("supported", "supported", "not_found")
    assert r.status is Status.SUPPORTED and r.critic is not None


@pytest.mark.parametrize("verdicts", [("supported", "contradicted", "supported"),
                                      ("supported", "not_found")])
def test_split_or_tie_is_disputed_without_a_critic(verdicts):
    r = run(*verdicts)
    assert r.status is Status.DISPUTED and r.critic is None and r.calls == len(verdicts)


def test_nothing_found_skips_the_critic():
    r = run("not_found", "not_found", "not_found")
    assert r.status is Status.NOT_FOUND and r.critic is None and r.calls == 3


@pytest.mark.parametrize("bad", ["no json here", '{"verdict": "probably"}', "[1, 2]",
                                 '{"verdict": "supported"'])
def test_one_bad_researcher_makes_it_not_checked(bad):
    r = run("supported", bad, "supported")
    assert r.status is Status.NOT_CHECKED and r.critic is None
    assert r.votes[1].error and r.votes[1].status is None


def test_a_researcher_that_raises_makes_it_not_checked():
    def boom(_prompt):
        raise JudgeError("down")
    researchers, c = panel("supported", "supported")
    researchers.append(P.Judge("other", boom, "boom", network=False))
    r = judge_claim("c1", "x", "y", researchers, c)
    assert r.status is Status.NOT_CHECKED and r.votes[2].error == "down"


@pytest.mark.parametrize("bad", ['{"falsified": "yes"}', "nope", '{"reason": "x"}'])
def test_a_bad_critic_makes_it_not_checked(bad):
    r = run("supported", "supported", critic=bad)
    assert r.status is Status.NOT_CHECKED and r.critic.error


def test_researchers_see_the_same_prompt_and_not_each_other():
    seen = []
    def record(reply):
        def ask(prompt):
            seen.append(prompt)
            return reply
        return ask
    researchers = [mock_judge("a", record(say("supported", "ALPHA"))),
                   mock_judge("b", record(say("supported", "BETA")))]
    critic_seen = []
    critic = mock_judge("c", lambda p: critic_seen.append(p) or crit(False))
    judge_claim("c1", "claim", "evidence", researchers, critic)
    assert seen[0] == seen[1] and "ALPHA" not in seen[0] and "BETA" not in seen[0]
    assert "ALPHA" in critic_seen[0] and "BETA" in critic_seen[0]


def test_networked_judges_need_network_true():
    researchers, c = panel("supported", "supported")
    researchers[0] = P.Judge("fam0", researchers[0].ask, "net")
    with pytest.raises(ConfigError):
        judge_claim("c1", "x", "y", researchers, c)
    assert judge_claim("c1", "x", "y", researchers, c, network=True).status is Status.SUPPORTED


def test_panel_shape_is_checked():
    one, c = panel("supported")
    with pytest.raises(ValueError, match="two researchers"):
        judge_claim("c1", "x", "y", one, c)
    same = [mock_judge("a", say("supported")), mock_judge("a", say("supported"))]
    with pytest.raises(ValueError, match="different families"):
        judge_claim("c1", "x", "y", same, c)
    researchers, _ = panel("supported", "supported")
    with pytest.raises(ValueError, match="critic"):
        judge_claim("c1", "x", "y", researchers, mock_judge("fam0", crit(False)))


@pytest.mark.parametrize("claim,evidence", [("", "e"), ("c", " "), ("c", "")])
def test_empty_claim_or_evidence_raises(claim, evidence):
    researchers, c = panel("supported", "supported")
    with pytest.raises(ValueError):
        judge_claim("c1", claim, evidence, researchers, c)


def rows(n):
    return [{"id": f"c{i}", "claim": "x", "evidence": "y"} for i in range(n)]


def test_call_cap_marks_the_rest_not_checked_in_order():
    researchers, c = panel("supported", "supported", "supported")
    out = run_panel(rows(3), researchers, c, max_calls=8)
    assert [r.id for r in out] == ["c0", "c1", "c2"]
    assert [r.status for r in out] == [Status.SUPPORTED, Status.SUPPORTED, Status.NOT_CHECKED]
    assert "call cap 8" in out[2].note and out[2].calls == 0


def test_cap_counts_calls_actually_made():
    researchers, c = panel("not_found", "not_found", "not_found")  # 3 calls each, no critic
    out = run_panel(rows(3), researchers, c, max_calls=10)
    assert [r.status for r in out] == [Status.NOT_FOUND] * 3


@pytest.mark.parametrize("bad,kw", [([], {}), ([{"claim": "x", "evidence": "y"}], {}),
                                    (rows(1), {"max_calls": 3})])
def test_run_panel_rejects_bad_input(bad, kw):
    researchers, c = panel("supported", "supported", "supported")
    with pytest.raises(ValueError):
        run_panel(bad, researchers, c, **kw)


ECHO = "import sys; print(sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read())"


def test_cli_judge_prompt_on_stdin_or_in_argv():
    assert cli_judge("f", [sys.executable, "-c", ECHO]).ask("hello").strip() == "hello"
    assert cli_judge("f", [sys.executable, "-c", ECHO, "{prompt}"]).ask("hi").strip() == "hi"


@pytest.mark.parametrize("code,match", [("import sys; sys.exit(3)", "exited 3"),
                                        ("pass", "printed nothing"),
                                        ("import time; time.sleep(5)", "timed out")])
def test_cli_judge_failures_raise(code, match):
    with pytest.raises(JudgeError, match=match):
        cli_judge("f", [sys.executable, "-c", code], timeout=0.5).ask("x")


def test_cli_judge_missing_program_raises():
    with pytest.raises(JudgeError):
        cli_judge("f", ["no-such-program-here"]).ask("x")


def test_subscription_panel_is_four_families_and_networked():
    researchers, critic = P.subscription_panel()
    families = [j.family for j in researchers] + [critic.family]
    assert len(set(families)) == 4 and all(j.network for j in [*researchers, critic])
    with pytest.raises(ConfigError):
        run_panel(rows(1), researchers, critic)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_openrouter(monkeypatch, replies):
    sent = []
    def urlopen(req, timeout):
        sent.append(req)
        return FakeResponse(json.dumps(replies.pop(0)).encode())
    monkeypatch.setattr(P.urllib.request, "urlopen", urlopen)
    return sent


def reply(content, cost):
    usage = {} if cost is None else {"cost": cost}
    return {"choices": [{"message": {"content": content}}], "usage": usage}


def test_openrouter_needs_a_key_a_cap_and_a_user_agent(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        openrouter_judge("f", "m", max_usd=1, ua="t/1")
    for cap in (0, -1):
        with pytest.raises(ValueError):
            openrouter_judge("f", "m", max_usd=cap, ua="t/1", key="k")
    with pytest.raises(ConfigError):
        openrouter_judge("f", "m", max_usd=1, ua=" ", key="k")


def test_openrouter_stops_at_its_cap(monkeypatch):
    sent = fake_openrouter(monkeypatch, [reply("a", 0.6), reply("b", 0.6)])
    j = openrouter_judge("f", "m", max_usd=1, ua="t/1", key="k")
    assert j.network and j.ask("p") == "a" and j.ask("p") == "b"
    with pytest.raises(SpendCapError):
        j.ask("p")
    assert len(sent) == 2 and sent[0].get_header("User-agent") == "t/1"


def test_openrouter_reply_without_cost_raises(monkeypatch):
    fake_openrouter(monkeypatch, [reply("a", None)])
    with pytest.raises(SpendCapError, match="cost"):
        openrouter_judge("f", "m", max_usd=1, ua="t/1", key="k").ask("p")


def test_to_json_carries_grade_votes_and_critic():
    out = P.to_json(run("supported", "supported"))
    assert out["grade"] == "supported" and out["calls"] == 3
    assert out["votes"][0]["status"] == "supported" and out["critic"]["falsified"] is False


def test_cli_mock_run(tmp_path, capsys):
    f = tmp_path / "claims.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows(2)) + "\n")
    assert P.main([str(f), "--mock"]) == 0
    out = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(o["id"], o["grade"]) for o in out] == [("c0", "not_found"), ("c1", "not_found")]


def test_cli_needs_a_mode(tmp_path):
    with pytest.raises(SystemExit):
        P.main([str(tmp_path / "x.jsonl")])


def test_panel_works_as_a_regression_checker(tmp_path):
    from citecheck.regress import main as regress
    key = tmp_path / "key.jsonl"
    key.write_text("\n".join(json.dumps({**r, "final_grade": "not_found"}) for r in rows(2)))
    code = regress([str(key), "--", sys.executable, "-m", "citecheck.panel", "-", "--mock"])
    assert code == 0


# --- review round 1 -----------------------------------------------------------------------

def counting_panel(verdict="supported"):
    calls = []
    def judge(family, reply):
        return mock_judge(family, lambda p: calls.append(family) or reply)
    researchers = [judge(f"fam{i}", say(verdict)) for i in range(3)]
    return researchers, judge("critic-fam", STANDS), calls


@pytest.mark.parametrize("bad,error", [
    ({"id": "z", "claim": "x", "evidence": ""}, ValueError),
    ({"claim": "x", "evidence": "y"}, ValueError),
    ({"id": "c0", "claim": "x", "evidence": "y"}, ValueError),   # duplicate id
    ("not a row", TypeError),
])
def test_a_bad_row_anywhere_fails_before_any_call(bad, error):
    researchers, c, calls = counting_panel()
    with pytest.raises(error):
        run_panel([*rows(2), bad], researchers, c)
    assert calls == []


def test_results_stream_one_claim_at_a_time():
    researchers, c, calls = counting_panel()
    it = P.iter_panel(rows(3), researchers, c)
    first = next(it)
    assert first.id == "c0" and len(calls) == 4


def test_no_call_goes_past_the_cap():
    researchers, c, calls = counting_panel()
    out = run_panel(rows(3), researchers, c, max_calls=8)
    assert len(calls) == 8 and out[2].status is Status.NOT_CHECKED


def test_cap_counts_actual_calls_not_the_worst_case():
    researchers, c, calls = counting_panel("not_found")  # 3 calls each; worst case is 4
    out = run_panel(rows(3), researchers, c, max_calls=10)
    assert [r.status for r in out] == [Status.NOT_FOUND] * 3 and len(calls) == 9


prose = st.text(alphabet=st.characters(blacklist_categories=["Cs"]), max_size=40)


@given(prose, prose, st.sampled_from(sorted(P.VERDICTS)), st.booleans())
def test_reply_found_inside_any_prose(before, after, verdict, fence):
    body = json.dumps({"verdict": verdict, "reason": "r"})
    if fence:
        body = f"```json\n{body}\n```"
    text = f"{before} {{noise}} {body} {after} {{\"other\": 1}}"
    assert P._reply(text, "verdict")["verdict"] == verdict


@given(prose)
def test_reply_without_the_key_raises(text):
    with pytest.raises(JudgeError):
        P._reply(text.replace("verdict", "") + ' {"reason": "x"}', "verdict")


@given(st.lists(st.sampled_from(list(P.VERDICTS.values())), min_size=1, max_size=7))
def test_tally_never_hides_a_split_or_a_tie(statuses):
    result = P._tally(statuses)
    if Status.SUPPORTED in statuses and Status.CONTRADICTED in statuses:
        assert result is Status.DISPUTED
    counts = sorted((statuses.count(s) for s in set(statuses)), reverse=True)
    if len(counts) > 1 and counts[0] == counts[1]:
        assert result is Status.DISPUTED
    if len(set(statuses)) == 1:
        assert result is statuses[0]


STDIN_AND_ARG = "import sys; print(repr(sys.stdin.read()), sys.argv[1])"


def test_prompt_in_argv_leaves_stdin_closed():
    out = cli_judge("f", [sys.executable, "-c", STDIN_AND_ARG, "{prompt}"], timeout=10).ask("hi")
    assert out.strip() == "'' hi"


def test_prompt_file_placeholder():
    code = "import sys; print(open(sys.argv[1]).read())"
    assert cli_judge("f", [sys.executable, "-c", code, "{prompt_file}"]).ask("long").strip() == "long"


def test_non_utf8_output_is_kept_not_raised():
    code = "import sys; sys.stdout.buffer.write(b'ok \\xff')"
    assert cli_judge("f", [sys.executable, "-c", code]).ask("x").startswith("ok")


def test_timeout_kills_the_children_too(tmp_path):
    import subprocess
    import time
    pidfile = tmp_path / "pid"
    code = ("import subprocess, sys, time; "
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "open(sys.argv[1], 'w').write(str(p.pid)); time.sleep(30)")
    with pytest.raises(JudgeError, match="timed out"):
        cli_judge("f", [sys.executable, "-c", code, str(pidfile)], timeout=1).ask("x")
    pid = pidfile.read_text()
    def alive():
        return subprocess.run(["ps", "-p", pid], capture_output=True, check=False).returncode == 0
    for _ in range(50):
        if not alive():
            break
        time.sleep(0.1)
    assert not alive(), "the child outlived the timeout"


@pytest.mark.parametrize("cost", [float("nan"), float("inf"), -0.1, True, "0.1"])
def test_openrouter_rejects_unusable_costs(monkeypatch, cost):
    fake_openrouter(monkeypatch, [reply("a", cost)])
    with pytest.raises(SpendCapError):
        openrouter_judge("f", "m", max_usd=1, ua="t/1", key="k").ask("p")


@pytest.mark.parametrize("body", [[1, 2], {"usage": {"cost": 0.1}}, {"usage": [], "choices": []}])
def test_openrouter_odd_bodies_raise_judge_errors(monkeypatch, body):
    fake_openrouter(monkeypatch, [body])
    with pytest.raises(JudgeError):
        openrouter_judge("f", "m", max_usd=1, ua="t/1", key="k").ask("p")


def test_openrouter_sends_max_tokens(monkeypatch):
    sent = fake_openrouter(monkeypatch, [reply("a", 0.01)])
    openrouter_judge("f", "m", max_usd=1, ua="t/1", key="k", max_tokens=50).ask("p")
    assert json.loads(sent[0].data)["max_tokens"] == 50


def test_cli_network_mode_uses_the_subscription_panel(tmp_path, capsys, monkeypatch):
    researchers, critic = panel("supported", "supported")
    researchers = [P.Judge(j.family, j.ask, j.name) for j in researchers]  # networked
    critic = P.Judge(critic.family, critic.ask, critic.name)
    monkeypatch.setattr(P, "subscription_panel", lambda timeout: (researchers, critic))
    f = tmp_path / "claims.jsonl"
    f.write_text(json.dumps(rows(1)[0]))
    assert P.main([str(f), "--network"]) == 0
    assert json.loads(capsys.readouterr().out)["grade"] == "supported"
