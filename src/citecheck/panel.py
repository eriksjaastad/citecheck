"""Optional judgment panel: is the claim true, given evidence already checked mechanically?

Researchers from different model families judge each claim against the same evidence,
none seeing another's answer. When they reach SUPPORTED or CONTRADICTED, a critic from a
further family tries to falsify it; if it succeeds the claim is DISPUTED. Nothing else in
citecheck calls this, and its result is one more labelled input, never a score.

Judges run on subscription CLIs (``subscription_panel``) or a mock. A paid API is used only
through ``openrouter_judge``, which needs an explicit spend cap. Every judge that sends data
out needs ``network=True`` on the call, like the other checks.

    python -m citecheck.panel claims.jsonl --network   # rows: {"id", "claim", "evidence"}
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import signal
import subprocess
import tempfile
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .fetch import ConfigError
from .status import Status

VERDICTS = {"supported": Status.SUPPORTED, "contradicted": Status.CONTRADICTED,
            "not_found": Status.NOT_FOUND}
MAX_CALLS = 20  # per run; a claim costs one call per researcher plus at most one critic


class JudgeError(RuntimeError):
    """A judge failed, timed out, or replied outside the contract."""


class SpendCapError(JudgeError):
    """A paid judge reached its spend cap, or could not report what a call cost."""


@dataclass(frozen=True)
class Judge:
    family: str                  # model family: anthropic, openai, xai, google, ...
    ask: Callable[[str], str]    # prompt -> raw reply; raises JudgeError on failure
    name: str = ""
    network: bool = True         # sends the prompt off this machine


@dataclass(frozen=True)
class Vote:
    judge: str
    family: str
    status: Status | None        # researchers only; None for the critic or on error
    falsified: bool | None       # critic only
    reason: str
    error: str | None = None


@dataclass(frozen=True)
class PanelResult:
    id: str
    status: Status
    votes: tuple[Vote, ...]
    critic: Vote | None
    note: str
    calls: int


# --- judges -------------------------------------------------------------------------------

def cli_judge(family: str, argv: list[str], *, name: str = "", timeout: float = 300) -> Judge:
    """A judge that runs a subscription CLI in an empty temporary folder.

    ``"{prompt}"`` in argv is replaced by the prompt, ``"{prompt_file}"`` by a file in that
    folder holding it; with neither, the prompt goes on stdin. On a timeout the CLI is killed
    with every process it started."""
    if not argv:
        raise ValueError("argv is empty")
    on_stdin = "{prompt}" not in argv and "{prompt_file}" not in argv

    def ask(prompt: str) -> str:
        with tempfile.TemporaryDirectory() as cwd:
            prompt_file = os.path.join(cwd, "prompt.txt")
            with open(prompt_file, "w", encoding="utf-8") as f:
                f.write(prompt)
            cmd = [prompt if a == "{prompt}" else prompt_file if a == "{prompt_file}" else a
                   for a in argv]
            try:
                proc = subprocess.Popen(
                    cmd, cwd=cwd, stdin=subprocess.PIPE if on_stdin else subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            except OSError as e:
                raise JudgeError(f"{argv[0]}: {e}") from None
            try:
                out, err = proc.communicate(prompt.encode() if on_stdin else None,
                                            timeout=timeout)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
                raise JudgeError(f"{argv[0]} timed out after {timeout} s") from None
        stdout = out.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            tail = err.decode("utf-8", errors="replace").strip()[-300:]
            raise JudgeError(f"{argv[0]} exited {proc.returncode}: {tail}")
        if not stdout.strip():
            raise JudgeError(f"{argv[0]} printed nothing")
        return stdout

    return Judge(family, ask, name or argv[0])


def mock_judge(family: str, reply: str | Callable[[str], str], *, name: str = "") -> Judge:
    """An offline judge with a fixed reply (or a function of the prompt). For tests and
    dry runs."""
    return Judge(family, reply if callable(reply) else (lambda _p: reply),
                 name or f"mock-{family}", network=False)


def subscription_panel(timeout: float = 300) -> tuple[list[Judge], Judge]:
    """Researchers on Claude, Grok and Gemini (Antigravity) subscriptions; Codex critic."""
    researchers = [
        cli_judge("anthropic", ["claude", "-p", "--tools", "", "--no-session-persistence"],
                  timeout=timeout),
        # Grok and Antigravity cannot be given zero tools: grok keeps only read_file, agy runs
        # sandboxed in plan mode, and both start in an empty folder.
        cli_judge("xai", ["grok", "--prompt-file", "{prompt_file}", "--tools", "read_file",
                          "--disable-web-search"], timeout=timeout),
        cli_judge("google", ["agy", "--sandbox", "--mode", "plan", "--print-timeout",
                             f"{int(timeout)}s", "-p", "{prompt}"], timeout=timeout + 10),
    ]
    critic = cli_judge("openai", ["codex", "exec", "--sandbox", "read-only",
                                  "--skip-git-repo-check", "-"], timeout=timeout)
    return researchers, critic


def openrouter_judge(family: str, model: str, *, max_usd: float, ua: str,
                     key: str | None = None, timeout: float = 120,
                     max_tokens: int = 800) -> Judge:
    """A paid judge through OpenRouter, stopped once this judge has spent ``max_usd``.

    The cap is per judge, checked before each call, so the last call can pass it by up to
    one reply of ``max_tokens``. The key comes from ``OPENROUTER_API_KEY`` when not given; a
    missing key raises. A reply that does not report a cost raises, since the cap could not
    be kept."""
    if not max_usd > 0:
        raise ValueError("max_usd must be above 0")
    if not ua.strip():
        raise ConfigError("a user agent is required")
    key = key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ConfigError("OPENROUTER_API_KEY is not set")
    spent = [0.0]

    def ask(prompt: str) -> str:
        if spent[0] >= max_usd:
            raise SpendCapError(f"{model}: spent ${spent[0]:.4f} of ${max_usd}")
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                           "max_tokens": max_tokens, "usage": {"include": True}}).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions", data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "User-Agent": ua})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.load(resp)
        except (OSError, ValueError) as e:
            raise JudgeError(f"{model}: {e}") from None
        usage = data.get("usage") if isinstance(data, dict) else None
        cost = usage.get("cost") if isinstance(usage, dict) else None
        if (not isinstance(cost, (int, float)) or isinstance(cost, bool)
                or not math.isfinite(cost) or cost < 0):
            raise SpendCapError(f"{model}: reply did not report a usable cost: {cost!r}")
        spent[0] += cost
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise JudgeError(f"{model}: reply had no message") from None

    return Judge(family, ask, model)


# --- prompts and parsing ------------------------------------------------------------------

_RESEARCHER = (
    "Judge one factual claim against the evidence below, and ONLY that evidence: not memory, "
    "not a search. If the evidence does not settle the claim, answer not_found.\n"
    'Reply with JSON only: {"verdict": "supported" | "contradicted" | "not_found", '
    '"reason": "<one or two sentences>"}'
)
_CRITIC = (
    "You are an adversarial critic. Independent researchers judged the claim below against "
    "the evidence. Do not agree with them: try to FALSIFY their verdict. Find the overreach, "
    "the missing evidence, or the reason the evidence does not say what they think. Say it "
    "stands only if it is genuinely solid.\n"
    'Reply with JSON only: {"falsified": true | false, "reason": "<one or two sentences>"}'
)
_DECODER = json.JSONDecoder()


def _reply(text: str, key: str) -> dict:
    """The first JSON object in a model reply that has ``key``. Prose, code fences and other
    braces around it are ignored; no such object raises."""
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = _DECODER.raw_decode(text, i)
        except ValueError:
            continue
        if isinstance(obj, dict) and key in obj:
            return obj
    raise JudgeError(f"no JSON object with {key!r} in reply: {text.strip()[:200]!r}")


def _research(judge: Judge, prompt: str) -> Vote:
    try:
        obj = _reply(judge.ask(prompt), "verdict")
        status = VERDICTS.get(str(obj.get("verdict", "")).strip().lower())
        if status is None:
            raise JudgeError(f"verdict {obj.get('verdict')!r} is not one of {sorted(VERDICTS)}")
    except JudgeError as e:
        return Vote(judge.name, judge.family, None, None, "", str(e))
    return Vote(judge.name, judge.family, status, None, str(obj.get("reason", "")).strip())


def _criticise(judge: Judge, prompt: str) -> Vote:
    try:
        obj = _reply(judge.ask(prompt), "falsified")
        falsified = obj.get("falsified")
        if not isinstance(falsified, bool):
            raise JudgeError(f"falsified is {falsified!r}, not true or false")
    except JudgeError as e:
        return Vote(judge.name, judge.family, None, None, "", str(e))
    return Vote(judge.name, judge.family, None, falsified, str(obj.get("reason", "")).strip())


def _tally(statuses: list[Status]) -> Status:
    """Majority of the researchers. A tie, or SUPPORTED against CONTRADICTED, is DISPUTED."""
    counts = Counter(statuses)
    if Status.SUPPORTED in counts and Status.CONTRADICTED in counts:
        return Status.DISPUTED
    (top, n), *rest = counts.most_common()
    return Status.DISPUTED if rest and rest[0][1] == n else top


# --- running ------------------------------------------------------------------------------

def _check_panel(researchers: list[Judge], critic: Judge, network: bool) -> None:
    families = [j.family for j in researchers]
    if len(researchers) < 2:
        raise ValueError("a panel needs at least two researchers")
    if len(set(families)) != len(families):
        raise ValueError(f"researchers must be from different families, got {families}")
    if critic.family in families:
        raise ValueError(f"the critic's family {critic.family!r} is also a researcher's")
    if not network and any(j.network for j in [*researchers, critic]):
        raise ConfigError("these judges send the claim off this machine; pass network=True")


def judge_claim(id: str, claim: str, evidence: str, researchers: list[Judge], critic: Judge,
                *, network: bool = False) -> PanelResult:
    """One claim through the panel. Any judge failing makes the claim NOT_CHECKED."""
    _check_panel(researchers, critic, network)
    if not claim.strip() or not evidence.strip():
        raise ValueError(f"{id}: claim and evidence must both have text")
    body = f"CLAIM:\n{claim}\n\nEVIDENCE:\n{evidence}"
    with ThreadPoolExecutor(len(researchers)) as pool:
        votes = tuple(pool.map(lambda j: _research(j, f"{_RESEARCHER}\n\n{body}"), researchers))
    calls = len(votes)
    failed = [v for v in votes if v.error]
    if failed:
        return PanelResult(id, Status.NOT_CHECKED, votes, None,
                           f"{len(failed)} of {len(votes)} researchers failed", calls)
    status = _tally([v.status for v in votes if v.status is not None])
    if status not in (Status.SUPPORTED, Status.CONTRADICTED):
        return PanelResult(id, status, votes, None, "no verdict for the critic to attack", calls)
    lines = "\n".join(f"- {v.family}: {v.status.value}: {v.reason}" for v in votes if v.status)
    crit = _criticise(critic, f"{_CRITIC}\n\n{body}\n\nVERDICT: {status.value}\n"
                              f"RESEARCHERS:\n{lines}")
    calls += 1
    if crit.error:
        return PanelResult(id, Status.NOT_CHECKED, votes, crit, "the critic failed", calls)
    if crit.falsified:
        return PanelResult(id, Status.DISPUTED, votes, crit, f"critic falsified {status.value}",
                           calls)
    return PanelResult(id, status, votes, crit, "critic could not falsify it", calls)


def _claims(rows: Iterable) -> list[tuple[str, str, str]]:
    """Every row as (id, claim, evidence), checked before any judge is called."""
    out, seen = [], set()
    for n, row in enumerate(rows, 1):
        if not isinstance(row, Mapping):
            raise TypeError(f"row {n} is not an object: {row!r}")
        id, claim, evidence = (str(row.get(k) or "") for k in ("id", "claim", "evidence"))
        if not id:
            raise ValueError(f"row {n} has no id")
        if id in seen:
            raise ValueError(f"row {n}: duplicate id {id!r}")
        if not claim.strip() or not evidence.strip():
            raise ValueError(f"row {n} ({id}): claim and evidence must both have text")
        seen.add(id)
        out.append((id, claim, evidence))
    if not out:
        raise ValueError("no claims to judge")
    return out


def iter_panel(rows: Iterable[Mapping], researchers: list[Judge], critic: Judge, *,
               network: bool = False, max_calls: int = MAX_CALLS) -> Iterator[PanelResult]:
    """Every row ``{"id", "claim", "evidence"}`` through the panel, in order, each result as
    soon as it is ready.

    Every row is checked before the first call, so a bad row costs nothing. A claim that
    could take the run past ``max_calls`` is not judged: it and every later one come back
    NOT_CHECKED, never dropped."""
    _check_panel(researchers, critic, network)
    per_claim = len(researchers) + 1
    if max_calls < per_claim:
        raise ValueError(f"max_calls {max_calls} is below one claim's {per_claim} calls")
    claims = _claims(rows)
    calls = 0
    for id, claim, evidence in claims:
        if calls + per_claim > max_calls:
            yield PanelResult(id, Status.NOT_CHECKED, (), None, f"call cap {max_calls} reached", 0)
            continue
        result = judge_claim(id, claim, evidence, researchers, critic, network=network)
        calls += result.calls
        yield result


def run_panel(rows: Iterable[Mapping], researchers: list[Judge], critic: Judge, *,
              network: bool = False, max_calls: int = MAX_CALLS) -> list[PanelResult]:
    """``iter_panel`` collected into a list."""
    return list(iter_panel(rows, researchers, critic, network=network, max_calls=max_calls))


def to_json(result: PanelResult) -> dict:
    """One output row. ``grade`` makes the panel usable as a citecheck.regress checker."""
    def vote(v: Vote) -> dict:
        return {"judge": v.judge, "family": v.family,
                "status": v.status.value if v.status else None, "falsified": v.falsified,
                "reason": v.reason, "error": v.error}
    return {"id": result.id, "grade": result.status.value, "note": result.note,
            "votes": [vote(v) for v in result.votes],
            "critic": vote(result.critic) if result.critic else None, "calls": result.calls}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(prog="python -m citecheck.panel",
                                description="Optional judgment panel over claims and evidence.")
    p.add_argument("claims", help='JSON Lines of {"id", "claim", "evidence"}; "-" reads stdin')
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--network", action="store_true",
                      help="judge on the subscription CLIs (claude, grok, agy; codex critic)")
    mode.add_argument("--mock", action="store_true",
                      help="offline dry run: every researcher answers not_found")
    p.add_argument("--max-calls", type=int, default=MAX_CALLS)
    p.add_argument("--timeout", type=float, default=300, help="seconds per judge call")
    args = p.parse_args(argv)

    if args.claims == "-":
        text = sys.stdin.read()
    else:
        with open(args.claims, encoding="utf-8") as f:
            text = f.read()
    rows = [json.loads(line) for line in text.split("\n") if line.strip()]
    if args.mock:
        reply = '{"verdict": "not_found", "reason": "mock"}'
        researchers = [mock_judge(f, reply) for f in ("mock-a", "mock-b", "mock-c")]
        critic = mock_judge("mock-critic", '{"falsified": false, "reason": "mock"}')
    else:
        researchers, critic = subscription_panel(args.timeout)
    counts: Counter[str] = Counter()
    for r in iter_panel(rows, researchers, critic, network=args.network,
                        max_calls=args.max_calls):
        print(json.dumps(to_json(r), ensure_ascii=False), flush=True)
        counts[r.status.value] += 1
    print(" ".join(f"{k}={v}" for k, v in sorted(counts.items())), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
