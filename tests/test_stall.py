"""Agent-loop stall, call B, build step 2: the harness survives a stall and says where.

THE PROPERTY
------------
When one case's model call times out, the harness must: stop (sub-call (i)),
write the rows it has plus an `incomplete` marker naming the case, print where
it stalled ABOVE the line that looks like a result, and exit 2. Before this
step, a ModelError on case 8 escaped a list comprehension and took cases 1-7
with it - exit 2, no record, no location. That is what 2026-09-24 looked like.

  case 1  a stall on the 3rd case: exit 2, record written with 2 rows + marker
  case 2  POSITIVE CONTROL: the same runner without the stall exits 0 and
          writes a complete record. A harness that marked every run incomplete
          would pass case 1 clean.
  case 3  it STOPS: the runner is never called for cases after the stall
  case 4  a stall on the FIRST case still writes a record (zero rows, a marker)
  case 5  a turn the loop reports (exc.turn) reaches the marker - ready for step 3
  case 6  with a baseline present, drift is NOT compared (the unscored cases
          would read as "vanished", i.e. removed, when they merely did not run)
  case 7  a NON-model exception keeps the old path: exit 2 and NO record. A
          crashing scorer is a bug in our code, and a partial record would be
          evidence about the scorer rather than the agent.

No model: scripted runner + scripted judge (Lab 8's runner=, Lab 10's judge=).
Run:  python tests/test_stall.py
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


SUITE = "evals/suites/agent.jsonl"
IDS = [c["id"] for c in parse_suite(REPO_ROOT / SUITE).select("fixed")]


def make_runner(stall_on: str | None = None, *, turn: int | None = None,
                boom: Exception | None = None):
    """A scripted agent. Raises on `stall_on`; records every case it was asked for."""
    asked: list[str] = []

    def run(case: dict) -> AgentResult:
        asked.append(case["id"])
        if case["id"] == stall_on:
            if boom is not None:
                raise boom
            exc = ModelError("the model did not answer within 120s")
            if turn is not None:
                exc.turn = turn
            raise exc
        calls = [(t["tool"], t["arguments"]) for t in case.get("expected_tools", [])]
        steps = [Step(n=i + 1, action="call", tool=t, arguments=a, ok=True)
                 for i, (t, a) in enumerate(calls)]
        steps.append(Step(n=len(calls) + 1, action="final"))
        return AgentResult(ok=True, answer=f"scripted answer for {case['id']}",
                           reason="final", steps=steps)
    return run, asked


def judge_ok(question, answer, expected, **kw):
    return expected


def harness(runner, out: Path, baseline: Path | None = None, *, via_main=False):
    eval_harness.resolve = lambda loaded: (
        lambda **kw: scorers.make_agent_scorer(runner=runner, judge=judge_ok, **kw))
    argv = ["--suite", SUITE, "--tier", "fixed", "--out", str(out)]
    argv += ["--baseline", str(baseline if baseline is not None else out / "no-baseline.json")]  # ISOLATION: never the repo's committed baseline (see tests/test_isolation.py)
    o, e = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
        if via_main:
            code = eval_harness.main(argv)
        else:
            code = eval_harness.run(eval_harness.build_parser().parse_args(argv))
    return code, o.getvalue(), e.getvalue()


def records(out: Path) -> list[Path]:
    return sorted(out.glob("*.json")) if out.exists() else []


tmp = Path(tempfile.mkdtemp())
third = IDS[2]
print("stall - agent-loop stall, call B, build step 2\n")

print(f"case 1  a stall on the 3rd case ({third})")
runner, asked = make_runner(third)
code, out, err = harness(runner, tmp / "c1")
check("exit 2", code == 2, f"got {code}")
found = records(tmp / "c1")
check("a record WAS written (before: none)", len(found) == 1, str(found))
rec = run_record.read(found[0]) if found else {"header": {"incomplete": None}, "cases": []}
marker = rec["header"]["incomplete"]
check("it holds exactly the 2 rows scored before the stall",
      [r["id"] for r in rec["cases"]] == IDS[:2], str([r["id"] for r in rec["cases"]]))
check("the marker names the stalled case", marker is not None and marker["case_id"] == third,
      repr(marker))
check("the marker carries the error text", marker is not None and "120s" in marker["error"])
check("turn is null - a scripted runner raises it unstamped (agent.run() stamps it)",
      marker is not None and marker["turn"] is None)
check("stderr names the case", f"STALLED at {third}" in err, err.strip()[:120])
lines = out.splitlines()
inc = next((i for i, l in enumerate(lines) if l.startswith("  INCOMPLETE")), None)
gat = next((i for i, l in enumerate(lines) if l.startswith("  GATING")), None)
check("the report has an INCOMPLETE line naming the case",
      inc is not None and third in lines[inc], lines[inc] if inc is not None else "absent")
check("...printed ABOVE the gating line, which on a partial run looks like a verdict",
      inc is not None and gat is not None and inc < gat)

print("\ncase 2  positive control - no stall, a complete run")
runner, _ = make_runner(None)
code, out, _ = harness(runner, tmp / "c2")
check("exit 0", code == 0, f"got {code}")
rec2 = run_record.read(records(tmp / "c2")[0])
check("incomplete is null and all 13 rows are present",
      rec2["header"]["incomplete"] is None and len(rec2["cases"]) == len(IDS))
check("no INCOMPLETE line in the report", "INCOMPLETE" not in out)

print("\ncase 3  it STOPS at the first stall - sub-call (i)")
runner, asked = make_runner(third)
harness(runner, tmp / "c3")
check("the runner was asked for exactly 3 cases, never the 10 after",
      asked == IDS[:3], str(asked))

print("\ncase 4  a stall on the FIRST case still leaves evidence")
runner, _ = make_runner(IDS[0])
code, out, _ = harness(runner, tmp / "c4")
found = records(tmp / "c4")
rec4 = run_record.read(found[0]) if found else None
check("exit 2, one record, zero rows, marker names the first case",
      code == 2 and rec4 is not None and rec4["cases"] == []
      and rec4["header"]["incomplete"]["case_id"] == IDS[0])

print("\ncase 5  a turn the loop reports reaches the marker")
runner, _ = make_runner(third, turn=4)
code, out, _ = harness(runner, tmp / "c5")
rec5 = run_record.read(records(tmp / "c5")[0])
check("marker turn == 4", rec5["header"]["incomplete"]["turn"] == 4,
      repr(rec5["header"]["incomplete"]))
check("the report says turn 4", f"stalled at {third} (turn 4)" in out)

print("\ncase 6  with a baseline, drift is NOT compared on an incomplete run")
base = tmp / "base.json"
base.write_text(json.dumps(rec2))                   # the complete run from case 2
runner, _ = make_runner(third)
code, out, err = harness(runner, tmp / "c6", base)
check("exit 2", code == 2, f"got {code}")
# Added after sabotage 3 (drift computed anyway) passed this case clean: the
# report hides drift on an incomplete run and the exit code is 2 regardless, so
# the ONLY visible trace of the wrong comparison was stderr telling the operator
# that 10 cases were REMOVED and removal takes --rebaseline.
check("stderr does not claim the unscored cases were removed",
      "absent from this run" not in err, err.strip().splitlines()[-1][:100])
check("drift says not compared", "DRIFT      not compared - the run is incomplete" in out)
# A case LINE, not the word: the not-compared message itself says "VANISHED",
# and the first version of this check matched it - the check was wrong, not
# the code. What must not appear is a case reported as removed.
check("no case is reported VANISHED",
      not any(l.startswith("    VANISHED  ") for l in out.splitlines()))
runner, _ = make_runner(None)
code, out, _ = harness(runner, tmp / "c6b", base)
check("control: the same baseline IS compared on a complete run",
      code == 0 and "matched by id: compared 13" in out, f"exit {code}")

print("\ncase 7  a non-model exception keeps the old path - exit 2, NO record")
runner, _ = make_runner(third, boom=AttributeError("scorer bug"))
code, out, err = harness(runner, tmp / "c7", via_main=True)
check("exit 2 (main's last-resort catch)", code == 2, f"got {code}")
check("no record written", records(tmp / "c7") == [], str(records(tmp / "c7")))
check("stderr calls it a TOOLING ERROR, not a stall",
      "TOOLING ERROR" in err and "STALLED" not in err)

print("\ncase 8  started_at is stamped BEFORE the first case, not after the last")
print("  Found 2026-09-27: the header was built after scoring, so 'started' was the")
print("  finish time. This runner takes >1 s per case (the stamp has 1 s resolution).")
import time as _time
from datetime import datetime as _dt, timezone as _tz
seen: list[str] = []
slow_inner, _ = make_runner(IDS[1])                 # stall on case 2: two calls total
def slow(case):
    seen.append(_dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _time.sleep(1.1)
    return slow_inner(case)
code, out, _ = harness(slow, tmp / "c8")
rec8 = run_record.read(records(tmp / "c8")[0])
check("started_at <= the moment the first case began",
      rec8["header"]["started_at"] <= seen[0], f"started {rec8['header']['started_at']} first {seen[0]}")
# No "control" line here: the first draft had one that could not fail (an `or`
# made it always true). The control is the sabotage - restore the old default
# and this case goes red (verified 2026-09-27).

print()
if failures:
    print(f"FAIL: {len(failures)} check(s) failed")
    sys.exit(1)
print("PASS: a stall stops the run, leaves the evidence, says where, and never claims a verdict")
