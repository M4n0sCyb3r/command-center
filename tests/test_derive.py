"""P04 scorer defect 3, build step 1: the judge extracts, CODE interprets.

THE DEFECT: the judge was asked "Any outages?" and declined a correct answer
that said "stopped" - it was deciding whether "stopped" counts as an "outage".
Attempt A (hide the question) made it worse: it filed RUNNING services under
"down" and returned ['Spooler', 'Spooler'].

THE FIX (option A, Jose's call - "the 4 single-service cases already use this
shape"): the judge will report one reading per service; derive_facts() works
out `down` and `automatic` by plain rules, tested here with no model at all.
Sub-call (i): DOWN MEANS ANYTHING BUT `running`.

CASE 7 IS THE POSITIVE CONTROL: the rules, applied to the host's TRUE state
(src/tools.py SERVICES), must reproduce every case's answer key exactly. A
rule that disagreed with the answer key would fail a perfect judge.

Run:  python tests/test_derive.py
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import score                                       # noqa: E402
import tools                                       # noqa: E402
from suite import parse_suite                      # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


V = score.load_vocabulary()
SVC = V["service"]
SUITE = parse_suite(score.SUITE)
by_id = {c["id"]: c for c in SUITE.cases}
DOWN = by_id["aggregate-down"]["expected_facts"]            # {'down': [Spooler, wuauserv]}


def all_running() -> dict:
    return {s: {"status": "running", "start_type": "manual"} for s in SVC}


def down_of(readings: dict) -> list:
    return score.derive_facts(["down"], readings, SVC)["down"]


print("derive - P04 scorer defect 3, build step 1")
print()

print("case 1  sub-call (i): down means anything but `running`")
for state in V["status"]:
    r = all_running(); r["Spooler"]["status"] = state
    want = [] if state == "running" else ["Spooler"]
    check(f"Spooler {state!r:20} -> down {want}", down_of(r) == want, str(down_of(r)))
print()

print("case 2  NOT_STATED contributes nothing - a concise correct answer passes")
concise = {"Spooler": {"status": "stopped"}, "wuauserv": {"status": "stopped"}}   # others unmentioned
check("'Spooler and wuauserv are stopped' -> down passes",
      score.compare_facts(DOWN, score.derive_facts(["down"], concise, SVC)))
missed = {"Spooler": {"status": "stopped"}}                                       # wuauserv never mentioned
check("an answer that never mentions wuauserv -> down FAILS",
      not score.compare_facts(DOWN, score.derive_facts(["down"], missed, SVC)))
check("a reading with no status field counts as not stated",
      down_of({"Spooler": {}}) == [])
print()

print("case 3  UNLISTED poisons a list - an unreadable claim never passes")
r = all_running(); r["Spooler"]["status"] = "stopped"; r["wuauserv"]["status"] = "stopped"
check("baseline: the right readings pass", score.compare_facts(DOWN, score.derive_facts(["down"], r, SVC)))
r["W32Time"]["status"] = score.UNLISTED
got = score.derive_facts(["down"], r, SVC)
check("one UNLISTED status anywhere -> the list cannot match",
      not score.compare_facts(DOWN, got), str(got))
check("even a list that is otherwise empty is poisoned",
      down_of({"WinRM": {"status": score.UNLISTED}}) == [score.UNLISTED])
print()

print("case 4  a service-name key reads that service's status (single-service cases)")
spool = by_id["status-spooler"]["expected_facts"]                                 # {'Spooler': 'stopped'}
check("Spooler stopped -> passes",
      score.compare_facts(spool, score.derive_facts(list(spool), {"Spooler": {"status": "stopped"}}, SVC)))
check("Spooler not mentioned -> NOT_STATED -> fails",
      not score.compare_facts(spool, score.derive_facts(list(spool), {}, SVC)))
check("Spooler UNLISTED -> fails",
      not score.compare_facts(spool, score.derive_facts(list(spool), {"Spooler": {"status": score.UNLISTED}}, SVC)))
check("key matching is case-insensitive, like compare_facts",
      score.derive_facts(["spooler"], {"Spooler": {"status": "stopped"}}, SVC) == {"spooler": "stopped"})
print()

print("case 5  attempt A's two wrong extractions cannot be produced")
r = all_running(); r["Spooler"]["status"] = "stopped"
check("running services never land in down (any-down-2's [Spooler, W32Time, WinRM])",
      down_of(r) == ["Spooler"], str(down_of(r)))
check("a service appears at most once (aggregate-down's [Spooler, Spooler])",
      len(down_of({s: {"status": "stopped"} for s in SVC})) == len(SVC))
print()

print("case 6  `automatic` reads start_type, not status; unknown keys are a tooling error")
r = all_running(); r["Spooler"]["start_type"] = "automatic"; r["Spooler"]["status"] = "stopped"
check("a STOPPED automatic service is still automatic",
      score.derive_facts(["automatic"], r, SVC)["automatic"] == ["Spooler"])
try:
    score.derive_facts(["outages"], all_running(), SVC)
    check("a key no rule derives raises ValueError", False)
except ValueError as exc:
    check("a key no rule derives raises ValueError", "outages" in str(exc), str(exc)[:60])
for reserved in (score.UNLISTED, score.NOT_STATED):
    bad = copy.deepcopy(V); bad["status"] = [*bad["status"], reserved]
    try:
        score.check_vocabulary(bad, SUITE.cases)
        check(f"a vocabulary containing {reserved!r} is refused", False)
    except ValueError:
        check(f"a vocabulary containing {reserved!r} is refused", True)
print()

print("case 7  POSITIVE CONTROL: the host's true state reproduces every answer key")
truth = {name: dict(rec) for name, rec in tools.SERVICES.items()}
checked = 0
for case in SUITE.cases:
    exp = case.get("expected_facts")
    if exp is None:
        continue                                   # the negative case - step 2's business
    got = score.derive_facts(list(exp), truth, SVC)
    check(f"{case['id']:22} {exp}", score.compare_facts(exp, got), "" if score.compare_facts(exp, got) else str(got))
    checked += 1
check("every positive case was checked", checked == sum(1 for c in SUITE.cases if c.get("expected_facts") is not None))
print()

print(f"{'PASS' if not failures else 'FAIL'}  {len(failures)} failure(s)")
sys.exit(1 if failures else 0)
