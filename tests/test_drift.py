"""Build step 5 check: drift is matched by id, and a vanished case is exit 2.

THE STEP'S STATED CHECK is two things (P04 README, build order 5):
  case 1  a REORDERED suite produces ZERO drift
  case 7  a case MISSING from the run exits 2 - through run(), not a helper

CASE 2 IS THE POSITIVE CONTROL for case 1. A compare() that returned "nothing
moved" for every input would pass case 1 with a clean sheet - step 2's
constant-returning sabotage, three steps later. So flipping one row must be
seen, in both directions. Case 1 also proves its own fixture: a POSITIONAL
comparison of the same two records must see drift, or the reorder moved
nothing and the check tested nothing.

CASE 3 IS WHY OPTION B WAS CHOSEN: a retrieval case that still passes but
slipped from rank 1 to rank 2 must appear, with both values printed.

CASE 8 IS STRUCTURAL: drift.py's CODE (not its docstrings) must contain no
scorer vocabulary. The same leak has happened three times already. The check
is sabotage-verified in place - a copy with one scorer word injected must trip
it - because a grep that matches nothing proves nothing.

NO MODEL, NO NETWORK. Case 7 runs the real run() with the agent scorer's
scripted runner and judge (Lab 8's runner= seam, Lab 10's judge= seam).

Run:  python tests/test_drift.py
"""

from __future__ import annotations

import ast
import contextlib
import copy
import io
import json
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
import scorers                                     # noqa: E402
from agent import AgentResult, Step                # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def row(case_id: str, tier: str, passed: bool, detail: dict | None = None) -> dict:
    return {"id": case_id, "tier": tier, "case_sha256": "c" * 64, "result": "pass" if passed else "fail",
            "detail": detail or {}, "note": ""}


def record(rows: list[dict], tier_selection: str = "fixed") -> dict:
    return {"record_version": run_record.RECORD_VERSION,
            "header": {"suite_path": "evals/suites/x.jsonl", "suite_sha256": "a" * 64, "suite_header_sha256": "a" * 64,
                       "tier_selection": tier_selection, "scorer": "retrieval",
                       "model_name": "m", "model_digest": "d", "host_target": "h", "sampling": None, "rebaseline": None,
                       "started_at": "2026-09-23T00:00:00Z",
                       "record_version": run_record.RECORD_VERSION},
            "cases": rows}


ROWS = [row("q-a", "fixed", True, {"rank": 1, "top_score": 0.71}),
        row("q-b", "fixed", False, {"rank": None, "top_score": 0.40}),
        row("q-c", "fixed", True, {"rank": 2, "top_score": 0.66}),
        row("q-d", "fixed", True, {"rank": 1, "top_score": 0.80})]
BASE = record(ROWS)

print("drift - build step 5: matched by id; a vanished case is exit 2")
print()

# --- Case 1: STATED CHECK - a reordered suite is zero drift. ----------------
print("case 1  a reordered run against the same baseline: zero drift")
shuffled = record([ROWS[2], ROWS[0], ROWS[3], ROWS[1]])
positional = sum(1 for a, b in zip(BASE["cases"], shuffled["cases"]) if a != b)
check("fixture is real: a POSITIONAL compare would see drift", positional == 4, f"{positional} of 4")
d = drift.compare(shuffled, BASE)
check("compared 4, unchanged 4", (d.compared, d.unchanged) == (4, 4), f"{d.compared}/{d.unchanged}")
check("nothing moved (d.any is False)", d.any is False)
check("no diffs recorded", d.diffs == {})
print()

# --- Case 2: POSITIVE CONTROL - a flip is seen, both directions. -------------
print("case 2  positive control: one row flipped each way is seen")
flipped = copy.deepcopy(BASE)
flipped["cases"][0]["result"] = "fail"             # q-a True -> False
flipped["cases"][1]["result"] = "pass"              # q-b False -> True
d = drift.compare(flipped, BASE)
check("q-a regressed", d.regressed == ("q-a",), str(d.regressed))
check("q-b recovered", d.recovered == ("q-b",), str(d.recovered))
check("the other two unchanged", d.unchanged == 2, str(d.unchanged))
check("d.any is True", d.any is True)
print()

# --- Case 3: WHY B - passes both times, but got worse. -----------------------
print("case 3  a case that still passes but slipped rank 1 -> 2 is seen")
slipped = copy.deepcopy(BASE)
slipped["cases"][3]["detail"] = {"rank": 2, "top_score": 0.61}
d = drift.compare(slipped, BASE)
check("q-d is detail-moved, not regressed", d.detail_moved == ("q-d",) and not d.regressed,
      f"{d.detail_moved} {d.regressed}")
check("the diff carries both sides verbatim",
      d.diffs.get("q-d") == {"rank": (1, 2), "top_score": (0.80, 0.61)}, str(d.diffs.get("q-d")))
text = "\n".join(report.render(slipped, record_path="r.json", baseline=BASE, baseline_file="b.json"))
check("the report prints the key and both values",
      "DETAIL    q-d" in text and "rank" in text and "baseline 1" in text and "now      2" in text)
check("the report says drift is not gated", "never gated" in text)
print()

# --- Case 4: vanished is scoped to the tiers this run asked for. -------------
print("case 4  a baseline live row absent from a --tier fixed run is NOT vanished")
with_live = record(ROWS + [row("svc-health", "live", True)], tier_selection="all")
d = drift.compare(record(ROWS, "fixed"), with_live)
check("--tier fixed: vanished is empty", d.vanished == (), str(d.vanished))
check("--tier fixed: counted out_of_scope 1", d.out_of_scope == 1, str(d.out_of_scope))
text = "\n".join(report.render(record(ROWS, "fixed"), record_path="r", baseline=with_live,
                               baseline_file="b"))
check("the report prints the out-of-scope count", "1 baseline row(s) in tiers outside" in text)
d = drift.compare(record(ROWS, "all"), with_live)
check("--tier all: svc-health IS vanished", d.vanished == ("svc-health",), str(d.vanished))
print()

# --- Case 5: a new case is new, never vanished. -------------------------------
print("case 5  a case only in this run is NEW and does not vanish anything")
d = drift.compare(record(ROWS + [row("q-e", "fixed", True)]), BASE)
check("q-e is new", d.new == ("q-e",), str(d.new))
check("nothing vanished", d.vanished == ())
print()

# --- Case 6: duplicate ids refuse, on either side. ----------------------------
print("case 6  duplicate ids in either record raise rather than collapse")
dup = record(ROWS + [row("q-a", "fixed", False)])
for label, args in (("this run", (dup, BASE)), ("baseline", (BASE, dup))):
    try:
        drift.compare(*args)
        check(f"duplicate in {label} raises", False, "no exception")
    except ValueError as exc:
        check(f"duplicate in {label} raises ValueError", "q-a" in str(exc), str(exc)[:60])
print()

# --- Case 7: STATED CHECK - through run(), exit codes. -----------------------
print("case 7  run(): a vanished case exits 2; drift alone never moves the exit")


def fake(case: dict) -> AgentResult:
    calls = [(t["tool"], t["arguments"]) for t in case.get("expected_tools", [])]
    steps = [Step(n=i + 1, action="call", tool=t, arguments=a, ok=True)
             for i, (t, a) in enumerate(calls)]
    steps.append(Step(n=len(calls) + 1, action="final"))
    return AgentResult(ok=True, answer=f"scripted answer for {case['id']}",
                       reason="final", steps=steps)


def judge_ok(question, answer, expected, **kw):
    return expected


def judge_failing(case_id):
    def judge(question, answer, expected, **kw):
        return {} if f"for {case_id}" in (answer or "") else expected
    return judge


def harness(judge, out: Path, baseline: Path | None) -> tuple[int, str, str]:
    # The factory takes the suite it was resolved from (P04 scorer defect 2),
    # and passes it on, so this run also exercises the vocabulary check.
    eval_harness.resolve = lambda loaded: (
        lambda **kw: scorers.make_agent_scorer(runner=fake, judge=judge, **kw))
    argv = ["--suite", "evals/suites/agent.jsonl", "--tier", "fixed", "--out", str(out)]
    argv += ["--baseline", str(baseline if baseline is not None else out / "no-baseline.json")]  # ISOLATION: never the repo's committed baseline (see tests/test_isolation.py)
    o, e = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
        code = eval_harness.run(eval_harness.build_parser().parse_args(argv))
    return code, o.getvalue(), e.getvalue()


tmp = Path(tempfile.mkdtemp())
code, out, _ = harness(judge_ok, tmp / "r0", None)
check("no baseline: exit 0, drift says none", code == 0 and "DRIFT      none" in out, str(code))
first = run_record.read(next((tmp / "r0").glob("*.json")))

same = tmp / "same.json"
same.write_text(json.dumps(first))
code, out, _ = harness(judge_ok, tmp / "r1", same)
check("baseline == this run: exit 0, vanished 0", code == 0 and "vanished 0" in out, str(code))

gone = copy.deepcopy(first)
gone["cases"].append(row("retired-case", "fixed", True))
gone_p = tmp / "gone.json"
gone_p.write_text(json.dumps(gone))
code, out, err = harness(judge_ok, tmp / "r2", gone_p)
check("case missing from the run: EXIT 2", code == 2, str(code))
check("stderr names the vanished id and --rebaseline",
      "retired-case" in err and "--rebaseline" in err, err.strip()[-90:])
check("the report printed first, with a VANISHED line",
      "VANISHED  retired-case" in out)

code, out, _ = harness(judge_failing("aggregate-down"), tmp / "r3", same)
check("a regression vs baseline: exit 1 from the RUN's verdict, not 2",
      code == 1 and "REGRESSED aggregate-down" in out, str(code))
bad = copy.deepcopy(first)
for r in bad["cases"]:
    if r["id"] == "aggregate-down":
        r["result"] = "fail"
bad_p = tmp / "bad.json"
bad_p.write_text(json.dumps(bad))
code, out, _ = harness(judge_ok, tmp / "r4", bad_p)
check("a recovery vs baseline: exit 0 - drift is reported, never gated",
      code == 0 and "RECOVERED aggregate-down" in out, str(code))
print()

# --- Case 8: no scorer vocabulary in drift.py's code. ------------------------
print("case 8  drift.py's CODE names the spine and nothing else")
BANNED = {"rank", "top_score", "hit_ids", "trajectory", "outcome", "judge",
          "answer", "expected", "extracted", "missing", "extra", "question"}


def leaks(source: str) -> set[str]:
    tree = ast.parse(source)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and \
                    isinstance(node.body[0].value, ast.Constant):
                docs.add(id(node.body[0].value))
    words = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            words |= {w.strip("'\"{}()<>.,:;") for w in node.value.split()}
        elif isinstance(node, ast.Name):
            words.add(node.id)
        elif isinstance(node, ast.Attribute):
            words.add(node.attr)
        elif isinstance(node, ast.arg):
            words.add(node.arg)
    return words & BANNED


src = (SRC / "drift.py").read_text()
check("clean: no scorer word in drift.py's code", leaks(src) == set(), str(leaks(src)))
sabotaged = src.replace("        if d:\n            diffs[case_id] = d\n",
                        "        if d and 'rank' in d:\n            diffs[case_id] = d\n", 1)
check("sabotage-verified: an injected 'rank' IS caught",
      sabotaged != src and leaks(sabotaged) == {"rank"}, str(leaks(sabotaged)))
print()

total = sum(1 for _ in failures)
print("FAILED: " + ", ".join(failures) if failures else "all checks ok")
sys.exit(1 if failures else 0)
