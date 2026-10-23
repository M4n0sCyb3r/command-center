"""P04 scorer defect 2 check: the judge's form is a CLOSED VOCABULARY.

THE DEFECT: every slot accepted any string, so the judge wrote a service's
NAME ('print spooler'), its start type ('stopped and set to start
automatically') or a whole sentence into a status field, and decorated list
items ('Spooler (automatic startup)').

THE FIX (option A, Jose's call - "it makes the wrong answers impossible to
write"): the suite header declares the host's answer space; a status slot is an
enum of states, a list slot an enum of service names, each plus UNLISTED.

The schema does double duty (structured.ask_json): it constrains the decoder
AND validates the reply. So case 2 proves the observed wrong values cannot pass
the form by running them through the SAME validator - no model needed.

CASE 4 IS THE POSITIVE CONTROL for the whole design: a vocabulary that could
FORCE a pass (a one-value list) must be refused. Case 5 proves the vocabulary
actually REACHES the judge through the harness - a check that only ever saw
vocabulary=None would pass on the old code.

Run:  python tests/test_vocabulary.py
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import eval_harness                                # noqa: E402
import score                                       # noqa: E402
import scorers                                     # noqa: E402
from agent import AgentResult, Step                # noqa: E402
from structured import parse_json                  # noqa: E402
from suite import parse_suite                      # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def accepts(schema: dict, facts: dict) -> bool:
    try:
        parse_json(json.dumps({"stance": "states", "facts": facts}), schema)
        return True
    except Exception:                                # noqa: BLE001
        return False


SUITE = parse_suite(score.SUITE)
by_id = {c["id"]: c for c in SUITE.cases}
V = score.load_vocabulary()

print("vocabulary - P04 scorer defect 2")
print()

print("case 1  the agent suite declares the host's answer space")
check("7 service states", len(V["status"]) == 7, str(V["status"]))
check("4 service names, the host's", V["service"] == ["Spooler", "W32Time", "WinRM", "wuauserv"])
check("the header carries it", SUITE.header.vocabulary == V)
print()

print("case 2  the observed wrong values CANNOT pass the form; right ones can")
S1 = score.facts_schema(by_id["status-spooler"]["expected_facts"], V)
L1 = score.facts_schema(by_id["aggregate-down"]["expected_facts"], V)
check("status slot is enum(status + unlisted)",
      S1["properties"]["facts"]["properties"]["Spooler"]["enum"] == [*V["status"], "unlisted"])
check("list item is enum(service + unlisted)",
      L1["properties"]["facts"]["properties"]["down"]["items"]["enum"] == [*V["service"], "unlisted"])
for bad in ["print spooler", "stopped and set to start automatically",
            "Print Spooler service is currently stopped. It is set to start automatically.",
            "currently running and set to start automatically"]:
    check(f"refused: {bad[:44]!r}", not accepts(S1, {"Spooler": bad}))
check("refused: ['Spooler (automatic startup)', 'wuauserv (manual startup)']",
      not accepts(L1, {"down": ["Spooler (automatic startup)", "wuauserv (manual startup)"]}))
check("accepted: {'Spooler': 'stopped'}  (the form can say the right answer)",
      accepts(S1, {"Spooler": "stopped"}))
check("accepted: {'Spooler': 'running'}  (and a WRONG one - nothing is forced)",
      accepts(S1, {"Spooler": "running"}))
check("accepted: {'down': ['Spooler', 'wuauserv']}", accepts(L1, {"down": ["Spooler", "wuauserv"]}))
N1 = score.facts_schema(None, V)
check("negative case keeps the OPEN form (a fabrication still has somewhere to land)",
      N1["properties"]["facts"] == {"type": "object", "additionalProperties": {"type": "string"}})
F1 = score.facts_schema(by_id["status-spooler"]["expected_facts"], None)
check("no vocabulary (fixtures only) = the old free-string form",
      F1["properties"]["facts"]["properties"]["Spooler"] == {"type": "string"})
print()

print("case 3  UNLISTED is recordable and never passes")
check("{'Spooler': 'unlisted'} fails", not score.compare_facts({"Spooler": "stopped"}, {"Spooler": "unlisted"}))
check("{'down': ['Spooler', 'wuauserv', 'unlisted']} fails ('Fax is down' too)",
      not score.compare_facts({"down": ["Spooler", "wuauserv"]}, {"down": ["Spooler", "wuauserv", "unlisted"]}))
check("the form accepts it (so the judge is never forced to drop a claim)",
      accepts(L1, {"down": ["Spooler", "wuauserv", "unlisted"]}))
print()

print("case 4  POSITIVE CONTROL: a vocabulary that could force a pass is refused")
cases = SUITE.cases


def refuses(vocab, label, why):
    try:
        score.check_vocabulary(vocab, cases)
        check(f"refused: {label}", False, "no exception")
    except ValueError as exc:
        check(f"refused: {label}", why in str(exc), str(exc)[:70])


def with_(**changes):
    v = copy.deepcopy(V); v.update(changes); return v


refuses(None, "no vocabulary at all", "must declare")
refuses({"status": V["status"]}, "a missing key", "exactly the keys")
refuses(with_(extra=["x", "y"]), "an extra key", "exactly the keys")
refuses(with_(status=["stopped"]), "status == ['stopped'] - forces every answer", "at least two")
refuses(with_(service=["Spooler"]), "service == ['Spooler']", "at least two")
refuses(with_(status=[*V["status"], "unlisted"]), "'unlisted' inside the vocabulary", "escape hatch")
refuses(with_(service=[*V["service"], "spooler"]), "a duplicate, case-insensitive", "repeats")
refuses(with_(status=["running", "paused"]), "an expected value outside it (stopped)", "could never pass")
print()

print("case 5  the vocabulary REACHES the judge - through calibrate() and through the harness")
seen: list = []


def recording(q, a, expected, vocabulary=None, **k):
    seen.append(vocabulary)
    return expected


score.calibrate(cases, score.load_calibration(), judge=recording, vocabulary=V)
check("calibrate(): all 9 judge calls got the vocabulary",
      len(seen) == 9 and all(v == V for v in seen), f"{len(seen)} calls")


def fake(case):
    calls = [(t["tool"], t["arguments"]) for t in case.get("expected_tools", [])]
    steps = [Step(n=i + 1, action="call", tool=t, arguments=a, ok=True) for i, (t, a) in enumerate(calls)]
    steps.append(Step(n=len(calls) + 1, action="final"))
    return AgentResult(ok=True, answer="scripted", reason="final", steps=steps)


factory_kwargs: list = []
seen.clear()


def resolve(loaded):
    def factory(**kw):
        factory_kwargs.append(kw)
        return scorers.make_agent_scorer(runner=fake, judge=recording, **kw)
    return factory


orig = eval_harness.resolve
eval_harness.resolve = resolve
try:
    out = Path(tempfile.mkdtemp())
    argv = ["--suite", "evals/suites/agent.jsonl", "--tier", "fixed", "--out", str(out),
            "--baseline", str(out / "no-baseline.json")]  # ISOLATION: never the repo's committed baseline (see tests/test_isolation.py)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = eval_harness.run(eval_harness.build_parser().parse_args(argv))
finally:
    eval_harness.resolve = orig
check("the harness hands the factory its suite", factory_kwargs
      and factory_kwargs[0].get("suite") is not None, str(factory_kwargs)[:60])
check("all 13 judge calls in a harness run got the vocabulary",
      len(seen) == 13 and all(v == V for v in seen), f"{len(seen)} calls, exit {code}")

try:
    scorers.make_agent_scorer(runner=None, suite=None)
    check("a REAL measurement without its suite is refused", False, "no exception")
except scorers.ScorerError as exc:
    check("a REAL measurement without its suite is refused (before any network call)",
          "vocabulary" in str(exc), str(exc)[:60])
bare = copy.copy(SUITE)
object.__setattr__(bare, "header", type(SUITE.header)(suite="agent", scorer="agent"))
try:
    scorers.make_agent_scorer(runner=None, suite=bare)
    check("a REAL measurement of a suite with no vocabulary is refused", False, "no exception")
except ValueError as exc:
    check("a REAL measurement of a suite with no vocabulary is refused (exit 2 path)",
          "must declare" in str(exc), str(exc)[:60])
print()

print("case 6  extract_facts() SENDS the closed form to the model (ask_json stubbed)")
# Added after sabotage: with extract_facts() ignoring the vocabulary, cases 1-5
# all stayed green - case 2 calls facts_schema() directly, and case 5 uses
# scripted judges. This is the path to the REAL model, so it is checked on the
# schema that would actually be sent.
sent: list = []
orig_ask = score.ask_json
score.ask_json = lambda prompt, schema, **k: (sent.append(schema) or {"stance": "states", "facts": {}})
try:
    score.extract_facts("Is the print spooler running?", "It is stopped.",
                        by_id["status-spooler"]["expected_facts"], vocabulary=V)
finally:
    score.ask_json = orig_ask
slot = sent[0]["properties"]["facts"]["properties"]["Spooler"] if sent else {}
check("the schema sent carries enum(status + unlisted)",
      slot.get("enum") == [*V["status"], "unlisted"], str(slot)[:60])
sent.clear(); captured: dict = {}
score.ask_json = lambda prompt, schema, **k: (captured.update(k) or {"stance": "declines"})
try:
    score.extract_facts("q", "a", {"Spooler": "stopped"}, vocabulary=V, timeout=5)
finally:
    score.ask_json = orig_ask
check("only transport kwargs reach ask_json (vocabulary is consumed)",
      "vocabulary" not in captured and captured.get("timeout") == 5, str(sorted(captured)))
print()

print(f"{'PASS' if not failures else 'FAIL'}  {len(failures)} failure(s)")
sys.exit(1 if failures else 0)
