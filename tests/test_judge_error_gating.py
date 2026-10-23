"""Judge-error gating (P04, 2026-09-27): a broken instrument is not a failed agent.

THE DEFECT. `score.score_case()` caught every judge exception and recorded
`outcome=False`, so a judge that ran away (ModelError on the 2,048-token cap)
produced a failed gating row and EXIT 1 - the harness blaming the agent for a
failure of the thing measuring it.

THE CALLS (all Jose's, 2026-09-27):
  A    - keep the row, mark it UNSCORED, keep running; "it keeps the rules and
         changes what the data says".
  (c)  - the spine's `passed: bool` becomes `result: pass|fail|unscored`, a
         closed enum; "it makes the wrong answer impossible to write".
  (i)  - trajectory failed + judge error = FAIL, not unscored; "it still counts
         and keeps track" (the judge could not have changed that verdict).
  (x)  - a run with a real failure AND an unscored case exits 1; "it proves the
         verdict without the missing measurement".

WHAT THIS PROVES
  case 1  agent_row applies (i) - all four combinations, hand-listed
  case 2  a bug in OUR comparison code is not filed as a judge error
  case 3  verdict() order: incomplete > fail > unscored > pass
  case 4  end to end, off disk: one judge error -> exit 2, not 1
  case 5  POSITIVE CONTROL: same run plus one real failure -> exit 1 (x);
          and with no judge error at all -> exit 0
  case 6  the report says UNSCORED above GATING and lists the row apart
  case 7  drift files pass -> unscored as UNMEASURED, never REGRESSED

NO MODEL, NO NETWORK. Scripted runner (Lab 8's seam), scripted judge (Lab 10's).

Run:  python tests/test_judge_error_gating.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

import drift                                       # noqa: E402
import eval_harness                                # noqa: E402
import report                                      # noqa: E402
import run_record                                  # noqa: E402
import score                                       # noqa: E402
import scorers                                     # noqa: E402
from agent import AgentResult, Step                # noqa: E402
from model_client import ModelError                # noqa: E402
from suite import parse_suite                      # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


AGENT_SUITE = REPO_ROOT / "evals" / "suites" / "agent.jsonl"
SUITE = parse_suite(AGENT_SUITE)
CASES = [c for c in SUITE.cases if c["tier"] == "fixed"]
BY_ID = {c["id"]: c for c in CASES}


def fake(calls, answer: str) -> AgentResult:
    steps = [Step(n=i + 1, action="call", tool=t, arguments=a, ok=True)
             for i, (t, a) in enumerate(calls)]
    steps.append(Step(n=len(calls) + 1, action="final"))
    return AgentResult(ok=True, answer=answer, reason="final", steps=steps)


def honest_runner(case):
    """Makes exactly the required calls -> trajectory passes."""
    return fake([(t["tool"], t["arguments"]) for t in case.get("expected_tools", [])],
                f"scripted answer for {case['id']}")


def shortcut_runner(case):
    """Answers without calling anything -> trajectory fails."""
    return fake([], f"scripted answer for {case['id']}")


def judge_ok(question, answer, expected, **kw):
    return expected


def judge_boom_on(*ids):
    """Raises the error the real judge raised on 09-27: the generation cap."""
    def judge(question, answer, expected, **kw):
        if any(f"for {i}" in (answer or "") for i in ids):
            raise ModelError("the model hit the 2048-token generation cap")
        return expected
    return judge


def judge_wrong_on(*ids):
    def judge(question, answer, expected, **kw):
        return {} if any(f"for {i}" in (answer or "") for i in ids) else expected
    return judge


def row_for(case_id, runner, judge):
    scorer, _ = scorers.make_agent_scorer(runner=runner, judge=judge)
    return scorer(BY_ID[case_id])


def measure(runner, judge, out: Path):
    scorer, cond = scorers.make_agent_scorer(runner=runner, judge=judge)
    rows = [scorer(c) for c in CASES]
    header = run_record.Header(
        suite_path=str(AGENT_SUITE), suite_sha256=run_record.suite_sha256(AGENT_SUITE), suite_header_sha256="a" * 64,
        tier_selection="fixed", scorer="agent", model_name=cond.model_name,
        model_digest=cond.model_digest, host_target=cond.host_target,
        sampling=cond.sampling, rebaseline=None)
    path = run_record.write(out, header, [__import__("dataclasses").replace(r, case_sha256="c" * 64) for r in rows])  # the harness stamps this
    record = run_record.read(path)                 # verdict from the FILE
    return record, run_record.summarize(record), path


A, B = CASES[0]["id"], CASES[1]["id"]
check("the suite has at least two fixed cases with required tools",
      len(CASES) >= 2 and all(c.get("expected_tools") for c in (CASES[0], CASES[1])),
      f"{A}, {B}")

# --- Case 1 ---------------------------------------------------------------
print("\ncase 1  agent_row applies sub-call (i) - every combination, listed by hand")
#   trajectory  judge      -> result
#   pass        ok         -> pass
#   pass        raises     -> unscored   (the verdict depended on the judge)
#   fail        raises     -> fail       (False AND anything; judge not needed)
#   fail        ok         -> fail
for runner, judge, want, label in (
        (honest_runner, judge_ok, "pass", "trajectory ok, judge ok"),
        (honest_runner, judge_boom_on(A), "unscored", "trajectory ok, judge RAISES"),
        (shortcut_runner, judge_boom_on(A), "fail", "trajectory FAILED, judge raises"),
        (shortcut_runner, judge_ok, "fail", "trajectory failed, judge ok")):
    r = row_for(A, runner, judge)
    check(f"{label:<34} -> {want}", r.result == want, f"got {r.result}")
r = row_for(A, shortcut_runner, judge_boom_on(A))
check("(i) keeps the judge error in the note - still counted",
      r.note.startswith("judge error: ModelError"), r.note[:60])
check("(i) and records judged=False in the detail", r.detail.get("judged") is False)
r = row_for(A, honest_runner, judge_boom_on(A))
check("an unscored row keeps the answer, for grading by hand",
      r.detail.get("answer") == f"scripted answer for {A}")

# --- Case 2 ---------------------------------------------------------------
print("\ncase 2  a bug in OUR comparison is raised, not filed as a judge error")
real_compare = score.compare_facts
try:
    def broken_compare(expected, actual):
        raise KeyError("a bug in compare_facts")
    score.compare_facts = broken_compare
    try:
        score.score_case(BY_ID[A], honest_runner(BY_ID[A]), judge=judge_ok)
        check("compare_facts raising escapes score_case", False, "it was swallowed")
    except KeyError:
        check("compare_facts raising escapes score_case", True)
finally:
    score.compare_facts = real_compare
print("       Swallowed, it would read as a flaky judge (exit 2, 're-run it')")
print("       instead of a bug in our code, which takes the harness's crash path.")

# --- Case 3 ---------------------------------------------------------------
print("\ncase 3  verdict() order: incomplete > fail > unscored > pass")


def s(failed=0, unscored=0, incomplete=None):
    return {"gating": 13, "gating_failed": failed, "gating_unscored": unscored,
            "incomplete": incomplete}


stall = {"case_id": A, "error": "timeout", "turn": 2}
for summ, want, label in (
        (s(), 0, "nothing failed, nothing unscored"),
        (s(unscored=1), 2, "one unscored, no failure"),
        (s(failed=1), 1, "one failure"),
        (s(failed=1, unscored=1), 1, "a failure AND an unscored  (x)"),
        (s(failed=0, unscored=13), 2, "every gating case unscored"),
        (s(failed=1, unscored=1, incomplete=stall), 2, "all of it, but incomplete")):
    got = eval_harness.verdict(summ)
    check(f"{label:<36} -> {want}", got == want, f"got {got}")

# --- Case 4 ---------------------------------------------------------------
print("\ncase 4  end to end, off disk: ONE judge error -> exit 2, not 1")
rec, got, path = measure(honest_runner, judge_boom_on(A), Path(tempfile.mkdtemp()))
check("record_version is 7", rec["record_version"] == 7)
check("1 unscored, 0 failed", got["gating_unscored"] == 1 and got["gating_failed"] == 0,
      f"unscored {got['gating_unscored']} failed {got['gating_failed']}")
check(f"the other {len(CASES) - 1} still scored and passed",
      got["gating_passed"] == len(CASES) - 1, str(got["gating_passed"]))
check("no pass rate is claimed", got["pass_rate"] is None, repr(got["pass_rate"]))
check("verdict from the FILE is 2 (was 1 before this fix)", eval_harness.verdict(got) == 2)

# --- Case 5 ---------------------------------------------------------------
print("\ncase 5  POSITIVE CONTROL - the verdict moves when it should")
_, got_x, _ = measure(honest_runner,
                      lambda q, a, e, **k: judge_boom_on(A)(q, a, e) if f"for {A}" in a
                      else judge_wrong_on(B)(q, a, e),
                      Path(tempfile.mkdtemp()))
check("one unscored + one real failure", got_x["gating_unscored"] == 1
      and got_x["gating_failed"] == 1, f"{got_x['gating_unscored']}/{got_x['gating_failed']}")
check("-> exit 1 (sub-call x)", eval_harness.verdict(got_x) == 1)
_, got_ok, _ = measure(honest_runner, judge_ok, Path(tempfile.mkdtemp()))
check("no judge error at all -> 0 unscored, exit 0",
      got_ok["gating_unscored"] == 0 and eval_harness.verdict(got_ok) == 0)
print("       Without these, a verdict() returning a constant 2 passes case 4.")

# --- Case 6 ---------------------------------------------------------------
print("\ncase 6  the report says it, above the gating line")
text = report.render(rec, record_path=path)
joined = "\n".join(text)
iu = next((i for i, l in enumerate(text) if l.strip().startswith("UNSCORED")), None)
ig = next((i for i, l in enumerate(text) if l.strip().startswith("GATING")), None)
check("an UNSCORED line exists", iu is not None)
check("it sits ABOVE the GATING line", iu is not None and ig is not None and iu < ig)
check("it names exit 2", "Exit 2." in joined)
check("the GATING line counts it", f"unscored 1" in (text[ig] if ig is not None else ""))
check("FAILURES is 'none' - an unscored row is not a failure", "FAILURES   none" in joined)
check("the row is listed apart, marked UNSCORED", f"? {A}  [fixed, gating, UNSCORED]" in joined)
rec_x, _, path_x = measure(honest_runner,
                           lambda q, a, e, **k: judge_boom_on(A)(q, a, e) if f"for {A}" in a
                           else judge_wrong_on(B)(q, a, e), Path(tempfile.mkdtemp()))
text_x = "\n".join(report.render(rec_x, record_path=path_x))
check("with a real failure too, the line names exit 1",
      "UNSCORED" in text_x and "Exit 1." in text_x)
check("and the real failure IS under FAILURES", f"FAILURES   1 row(s)" in text_x
      and f"- {B}  [fixed, gating]" in text_x)

# --- Case 7 ---------------------------------------------------------------
print("\ncase 7  drift: pass -> unscored is UNMEASURED, never REGRESSED")
base, _, _ = measure(honest_runner, judge_ok, Path(tempfile.mkdtemp()))
d = drift.compare(rec, base)
check("not in regressed", A not in d.regressed, str(d.regressed))
check("in unmeasured as (id, pass, unscored)",
      (A, "pass", "unscored") in d.unmeasured, str(d.unmeasured))
back = drift.compare(base, rec)
check("and unscored -> pass is not a recovery either",
      A not in back.recovered and (A, "unscored", "pass") in back.unmeasured)
bad, _, _ = measure(honest_runner, judge_wrong_on(A), Path(tempfile.mkdtemp()))
check("control: pass -> fail IS still a regression", A in drift.compare(bad, base).regressed)

print()
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("PASS: a judge that breaks is recorded as unmeasured - never as a failed agent")
