"""Build step 3 part 2c check: the harness measures, records, and judges the file.

WHAT THIS PROVES
----------------
1. `verdict()` maps a summary to an exit code, checked against hand-built
   summaries rather than against a run - and NEVER returns 2.
2. A full measuring run works end to end with a scripted runner and a scripted
   judge: 13 cases scored, a record written, read BACK OFF DISK, summarized,
   and the exit code derived from the file.
3. POSITIVE CONTROL: failing one case moves the verdict from 0 to 1. Without
   this, a `verdict()` that returned a constant 0 would pass case 2 clean.
4. A `live` case that FAILS does not gate (ADR 0006 decision 2).
5. A record written from a fixture SAYS SO in the fields that decide
   comparability, so it cannot be mistaken for a measurement later.
6. Run records are gitignored (ADR 0008, call 4a) - checked by reading
   .gitignore, not assumed.

NO MODEL AND NO NETWORK. The runner and the judge are both scripted, which is
Lab 8's `runner=` seam and Lab 10's `judge=` seam doing the job they were built
for. The equivalent run against the real agent needs Ollama and is Jose's.

Run:  python tests/test_measured_run.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

import eval_harness                                # noqa: E402
import run_record                                  # noqa: E402
import scorers                                     # noqa: E402
from agent import AgentResult, Step                # noqa: E402
from suite import parse_suite                      # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


AGENT_SUITE = REPO_ROOT / "evals" / "suites" / "agent.jsonl"

print("measured run - build step 3 part 2c: measure, record, judge the file")
print()

# --- Case 1: verdict(), against summaries nothing here computed. -----------
print("case 1  verdict() maps a summary to an exit code, and never to 2")


def summary(gating: int, gating_failed: int, reported_failed: int = 0) -> dict:
    # gating_unscored is 0 here: these are COMPLETE, fully-judged summaries.
    # Unscored rows and the verdict order are tests/test_judge_error_gating.py.
    return {"gating": gating, "gating_failed": gating_failed, "gating_unscored": 0,
            "reported": reported_failed, "reported_failed": reported_failed}


check("all gating cases passed -> 0", eval_harness.verdict(summary(13, 0)) == 0)
check("one gating case failed -> 1", eval_harness.verdict(summary(13, 1)) == 1)
check("thirteen failed -> still 1 (not a count)", eval_harness.verdict(summary(13, 13)) == 1)
# ADR 0006 decision 2: the live tier is REPORTED and never gates, because its
# ground truth moves and a moving fact reported as a stale expectation is the
# one confusion that ruling exists to prevent.
check("five LIVE cases failed, none gating -> still 0",
      eval_harness.verdict(summary(13, 0, reported_failed=5)) == 0)
check("verdict never returns 2 for any of these",
      all(eval_harness.verdict(summary(g, f)) in (0, 1)
          for g in (0, 1, 13) for f in (0, 1)))
print("       Exit 2 is not reachable from a COMPLETE summary. AMENDED 2026-09-26:")
print("       a partial run has rows AND no verdict, so an incomplete summary IS")
print("       exit 2 - see tests/test_incomplete_record.py and test_stall.py.")
print()

# --- the fixtures: Lab 8's runner= and Lab 10's judge= seams. --------------
cases = parse_suite(AGENT_SUITE).cases


def fake(calls: list[tuple[str, dict]], answer: str) -> AgentResult:
    steps = [Step(n=i + 1, action="call", tool=t, arguments=a, ok=True)
             for i, (t, a) in enumerate(calls)]
    steps.append(Step(n=len(calls) + 1, action="final"))
    return AgentResult(ok=True, answer=answer, reason="final", steps=steps)


def scripted_runner(case: dict) -> AgentResult:
    """Make exactly the calls the case requires, so trajectory always passes.
    We are testing the HARNESS here, so the agent is a fixture."""
    calls = [(t["tool"], t["arguments"]) for t in case.get("expected_tools", [])]
    return fake(calls, f"scripted answer for {case['id']}")


def judge_all_correct(question, answer, expected, **kw):
    return expected                              # outcome True for every case


def judge_failing(case_id: str):
    """A judge that gets exactly one case wrong - the positive control."""
    def judge(question, answer, expected, **kw):
        return {} if f"for {case_id}" in (answer or "") else expected
    return judge


def measure(judge, out: Path, tier: str = "fixed") -> tuple[dict, Path]:
    scorer, conditions = scorers.make_agent_scorer(runner=scripted_runner, judge=judge)
    selected = [c for c in cases if c["tier"] == tier]
    rows = [scorer(c) for c in selected]
    header = run_record.Header(
        suite_path=str(AGENT_SUITE),
        suite_sha256=run_record.suite_sha256(AGENT_SUITE), suite_header_sha256="a" * 64,
        tier_selection=tier, scorer="agent",
        model_name=conditions.model_name, model_digest=conditions.model_digest,
        host_target=conditions.host_target,
        sampling=conditions.sampling,
        rebaseline=None,
    )
    written = run_record.write(out, header, [__import__("dataclasses").replace(r, case_sha256="c" * 64) for r in rows])  # the harness stamps this
    # THE POINT: summarized from the FILE, not from `rows` in memory.
    return run_record.summarize(run_record.read(written)), written


# --- Case 2: the full path, all passing. ----------------------------------
print("case 2  13 cases scored, recorded, read back off disk, and judged")
out = Path(tempfile.mkdtemp())
got, written = measure(judge_all_correct, out)
check("13 cases in the record", got["case_count"] == 13, str(got["case_count"]))
check("all 13 gate", got["gating"] == 13, str(got["gating"]))
check("all 13 passed", got["gating_passed"] == 13, str(got["gating_passed"]))
check("pass_rate == 1.0", got["pass_rate"] == 1.0, str(got["pass_rate"]))
check("verdict from the FILE is 0", eval_harness.verdict(got) == 0)
check("the record exists on disk", written.exists() and written.stat().st_size > 0,
      f"{written.name} {written.stat().st_size} b")
check("no total was stored in it",
      set(run_record.read(written)) == {"record_version", "header", "cases"},
      str(sorted(run_record.read(written))))
print()

# --- Case 3: POSITIVE CONTROL - one failure moves the verdict. ------------
print("case 3  positive control - failing ONE case moves the verdict 0 -> 1")
got_bad, _ = measure(judge_failing("status-spooler"), Path(tempfile.mkdtemp()))
check("still 13 cases", got_bad["case_count"] == 13, str(got_bad["case_count"]))
check("exactly one gating failure", got_bad["gating_failed"] == 1,
      str(got_bad["gating_failed"]))
check("pass_rate moved 1.0 -> 12/13", got_bad["pass_rate"] == round(12 / 13, 4),
      str(got_bad["pass_rate"]))
check("verdict from the FILE is 1", eval_harness.verdict(got_bad) == 1)
print("       Without this, a verdict() returning a constant 0 - or a summarize()")
print("       ignoring its rows - would pass case 2 with a clean sheet.")
print()

# --- Case 4: a fixture run cannot pass itself off as a measurement. -------
print("case 4  a record written from a fixture says so in its own header")
header = run_record.read(written)["header"]
check("model_name == 'scripted'", header["model_name"] == "scripted", header["model_name"])
check("model_digest names the fixture",
      header["model_digest"] == "scripted-runner-no-model", header["model_digest"])
check("host_target == 'fixture'", header["host_target"] == "fixture", header["host_target"])
check("the suite hash IS real, though",
      header["suite_sha256"] == run_record.suite_sha256(AGENT_SUITE))
print("       model_name/model_digest are the CONDITION fields ADR 0008 4b(ii)")
print("       compares on. A fixture that wrote a live model's digest there would")
print("       be a record claiming a measurement it never made.")
print()

# --- Case 5: an empty tier selection, through the real entry point. -------
print("case 5  an empty tier selection is refused before anything expensive is built")
got_proc = subprocess.run(
    [sys.executable, str(SRC / "eval_harness.py"), "--suite", str(AGENT_SUITE),
     "--tier", "live", "--out", str(Path(tempfile.mkdtemp())),
     "--baseline", str(Path(tempfile.mkdtemp()) / "none.json")],  # ISOLATION: never the repo's committed baseline (see tests/test_isolation.py)
    capture_output=True, text=True, cwd=REPO_ROOT)
check("exit 2", got_proc.returncode == 2, f"exit={got_proc.returncode}")
check("the message says how many of how many", "selected 0 of 13 cases" in got_proc.stderr,
      got_proc.stderr.strip()[-160:])
# WAS "flags itself provisional pending step 4". Build step 4 SETTLED it:
# an empty tier selection is exit 2 because "I asked for a tier and it holds
# no cases" is indistinguishable from "that tier drained", and ADR 0006
# decision 2 exists to make a drained tier loud. The check now asserts the
# settled reasoning instead of the temporary flag.
check("and states the settled reason - drained is indistinguishable",
      "drained" in got_proc.stderr and "provisional" not in got_proc.stderr,
      got_proc.stderr.strip()[-140:])
# THIS is the ordering defect the first run of part 2c exposed: the factory was
# being built before the selection was checked, so this path demanded a model
# digest for a run that measures nothing.
check("no model was needed to reach that refusal",
      "gateway" not in got_proc.stderr and "Ollama" not in got_proc.stderr,
      got_proc.stderr.strip()[-160:])
print()

# --- Case 6: records stay out of git (ADR 0008 call 4a). ------------------
print("case 6  run records are gitignored - read from .gitignore, not assumed")
ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
check("evals/runs/* is ignored", "evals/runs/*" in ignore)
check("...but its README is kept", "!evals/runs/README.md" in ignore)
check("evals/suites is NOT ignored (suites are source)",
      "evals/suites" not in ignore.replace("!evals/suites", ""))
print("       A baseline is a reading PROMOTED to source by --rebaseline and is")
print("       committed; an ad-hoc run is a reading and is not. Role decides.")
print()

if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
