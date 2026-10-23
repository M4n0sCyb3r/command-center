"""P04 build step 6: --rebaseline, the only way a baseline moves (ADR 0008, 4b).

THE CALLS (Jose, 2026-09-28)
  6a = B  the header records the effective sampling (test_sampling_condition.py)
  6b-1 = B  a run with FAILED gating cases may become the baseline; "because
            [A] gives one verdict two sources" - the exit code already says
            whether a run is good, the baseline answers "compared to what?"
  6b-2 = A  a removal is written by the run into its OWN header, so the promote
            stays a byte copy; "the harness compares against the old baseline
            before writing"
  settled   refuse an INCOMPLETE record, refuse any UNSCORED gating case

WHAT THIS PROVES (end to end through eval_harness.run, off disk)
  case 1  the first baseline: written, BYTE-IDENTICAL to the run's record,
          replaced=null removed=[], and the report says so
  case 2  an ordinary run never moves it (bytes unchanged, rebaseline=null)
  case 3  a removal: WITHOUT --rebaseline exit 2 VANISHED (the control); WITH
          it the new baseline's header names the id and the old baseline's
          date, the report says REMOVED, and the exit is the run's own verdict
  case 4  REFUSED: incomplete - baseline bytes unchanged, removed=null
  case 5  REFUSED: an unscored gating case - unchanged, the id named
  case 6  call B: a FAILED gating case IS promoted, and still exits 1
  case 7  fail + unscored: exit 1 by call (x), and still REFUSED
  case 8  read() refuses a malformed marker and a removed id that has a row

No model: scripted runner + scripted judge.
Run:  python tests/test_rebaseline.py
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import eval_harness                                # noqa: E402
import run_record                                  # noqa: E402
import scorers                                     # noqa: E402
from agent import AgentResult, Step                # noqa: E402
from model_client import ModelError                # noqa: E402
from suite import parse_suite                      # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


SUITE = REPO_ROOT / "evals" / "suites" / "agent.jsonl"
IDS = [c["id"] for c in parse_suite(SUITE).select("fixed")]
tmp = Path(tempfile.mkdtemp())

# A copy of the suite with one case gone - what a real removal looks like.
DROPPED = IDS[-1]
SMALLER = tmp / "agent.jsonl"
SMALLER.write_text("".join(l for l in SUITE.read_text(encoding="utf-8").splitlines(keepends=True)
                           if f'"{DROPPED}"' not in l), encoding="utf-8")
assert len(parse_suite(SMALLER).cases) == len(IDS) - 1


def runner(stall_on=None, skip_tools_on=None):
    def run(case):
        if case["id"] == stall_on:
            raise ModelError("the model did not answer within 120s")
        calls = ([] if case["id"] == skip_tools_on else
                 [(t["tool"], t["arguments"]) for t in case.get("expected_tools", [])])
        steps = [Step(n=i + 1, action="call", tool=t, arguments=a, ok=True)
                 for i, (t, a) in enumerate(calls)]
        steps.append(Step(n=len(calls) + 1, action="final"))
        return AgentResult(ok=True, answer=f"scripted answer for {case['id']}",
                           reason="final", steps=steps)
    return run


def judge(boom_on=None):
    def j(question, answer, expected, **kw):
        if boom_on and f"for {boom_on}" in answer:
            raise ModelError("the model hit the 2048-token generation cap")
        return expected
    return j


BASE = tmp / "baselines" / "agent.json"


def harness(out, *, suite=SUITE, rebaseline=False, run=None, jdg=None):
    run = run or runner()
    jdg = jdg or judge()
    eval_harness.resolve = lambda loaded: (
        lambda **kw: scorers.make_agent_scorer(runner=run, judge=jdg, **kw))
    argv = ["--suite", str(suite), "--tier", "fixed", "--out", str(tmp / out),
            "--baseline", str(BASE)] + (["--rebaseline"] if rebaseline else [])
    o, e = io.StringIO(), io.StringIO()
    # A crash is turned into a code that fails every exit-code check BY NAME.
    # Found by sabotage: with refusal() gutted, the report crashed on the
    # incomplete run's removed=null, and the script died before a single
    # case-4 check could say what was wrong - red, but for no stated reason.
    with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
        try:
            code = eval_harness.run(eval_harness.build_parser().parse_args(argv))
        except Exception as exc:                     # noqa: BLE001
            code = f"CRASH {type(exc).__name__}: {exc}"
    written = sorted((tmp / out).glob("*.json"))
    return code, o.getvalue(), e.getvalue(), (written[-1] if written else None)


def base_bytes():
    return BASE.read_bytes() if BASE.exists() else None


print("case 1  the first baseline")
code, out, err, rec = harness("c1", rebaseline=True)
check("exit 0 (every scripted case passes)", code == 0, f"got {code}")
check("the baseline exists", BASE.exists())
check("it is BYTE-IDENTICAL to the run's record - a copy, not a rewrite",
      rec is not None and BASE.exists() and BASE.read_bytes() == rec.read_bytes())
first = run_record.read(BASE)
check("header: replaced=null, removed=[]",
      first["header"]["rebaseline"] == {"replaced": None, "removed": []}, str(first["header"]["rebaseline"]))
check("report: PROMOTING ... no baseline file was present", "REBASELINE PROMOTING" in out and "no baseline file was present" in out)
check("stderr: written, and not a baseline until committed", "not a baseline until it is COMMITTED" in err)

print("\ncase 2  an ordinary run never moves it")
before = base_bytes()
code, out, err, rec = harness("c2")
check("exit 0", code == 0, f"got {code}")
check("baseline bytes unchanged", base_bytes() == before)
check("the ordinary record says rebaseline=null", run_record.read(rec)["header"]["rebaseline"] is None)
check("no REBASELINE section", "REBASELINE" not in out)

print(f"\ncase 3  a removal ({DROPPED})")
before = base_bytes()
code, out, err, rec = harness("c3a", suite=SMALLER)
check("CONTROL - without --rebaseline: exit 2, VANISHED", code == 2 and f"VANISHED  {DROPPED}" in out,
      f"got {code}")
check("CONTROL - baseline unchanged", base_bytes() == before)
code, out, err, rec = harness("c3b", suite=SMALLER, rebaseline=True)
check("with --rebaseline: exit 0 - the run's own verdict, the removal was explicit", code == 0,
      f"got {code}")
new = run_record.read(BASE)
check("the NEW baseline's header names the removed id",
      new["header"]["rebaseline"]["removed"] == [DROPPED], str(new["header"]["rebaseline"]))
check("... and the date of the baseline it replaced",
      new["header"]["rebaseline"]["replaced"] == first["header"]["started_at"])
check("the report says REMOVED, not VANISHED",
      f"REMOVED   {DROPPED}" in out and "VANISHED" not in out)
check("the report's REBASELINE line lists it", f"removed 1: {DROPPED}" in out)
check("byte copy again", BASE.read_bytes() == rec.read_bytes())

print("\ncase 4  REFUSED: incomplete")
before = base_bytes()
code, out, err, rec = harness("c4", suite=SMALLER, rebaseline=True, run=runner(stall_on=IDS[2]))
check("exit 2", code == 2, f"got {code}")
check("baseline bytes unchanged", base_bytes() == before)
check("stderr: REFUSED ... INCOMPLETE", "REFUSED" in err and "INCOMPLETE" in err, err.strip()[-160:])
check("report: REBASELINE REFUSED", "REBASELINE REFUSED" in out)
check("the record says removed=null - not established, not []",
      run_record.read(rec)["header"]["rebaseline"]["removed"] is None)

print("\ncase 5  REFUSED: an unscored gating case")
before = base_bytes()
code, out, err, rec = harness("c5", suite=SMALLER, rebaseline=True, jdg=judge(boom_on=IDS[1]))
check("exit 2", code == 2, f"got {code}")
check("baseline bytes unchanged", base_bytes() == before)
check("the refusal names the case", "UNSCORED" in err and IDS[1] in err, err.strip()[-160:])

print("\ncase 6  call B: a FAILED gating case is promoted")
code, out, err, rec = harness("c6", suite=SMALLER, rebaseline=True, run=runner(skip_tools_on=IDS[0]))
check("exit 1 - the baseline does not excuse it", code == 1, f"got {code}")
got = run_record.read(BASE)
check("the baseline IS the failing run", BASE.read_bytes() == rec.read_bytes())
check("... and records the failure honestly",
      next(r["result"] for r in got["cases"] if r["id"] == IDS[0]) == "fail")

print("\ncase 7  fail + unscored: exit 1 by (x), and still refused")
before = base_bytes()
code, out, err, rec = harness("c7", suite=SMALLER, rebaseline=True,
                              run=runner(skip_tools_on=IDS[0]), jdg=judge(boom_on=IDS[1]))
check("exit 1", code == 1, f"got {code}")
check("baseline bytes unchanged", base_bytes() == before)
check("REFUSED", "REFUSED" in err)

print("\ncase 8  read() refuses malformed markers and contradictions")
doc = json.loads(BASE.read_text(encoding="utf-8"))
for label, marker in (("missing key", {"replaced": None}),
                      ("removed not a list", {"replaced": None, "removed": "x"}),
                      ("duplicate ids", {"replaced": None, "removed": ["a", "a"]}),
                      ("empty replaced", {"replaced": "", "removed": []}),
                      ("a removed id that has a row", {"replaced": None, "removed": [IDS[0]]})):
    bad = json.loads(json.dumps(doc))
    bad["header"]["rebaseline"] = marker
    p = tmp / "bad.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    try:
        run_record.read(p)
        check(f"{label} -> refused", False, "read")
    except ValueError as exc:
        check(f"{label} -> refused", "rebaseline" in str(exc), str(exc)[-90:])

print()
if failures:
    print(f"{len(failures)} check(s) FAILED")
    sys.exit(1)
print("all checks passed")
