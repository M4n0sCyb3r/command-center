"""Build step 3 part 2b check: a name resolves to a scorer, or the run exits 2.

WHAT THIS PROVES
----------------
1. Both real suites resolve to the factory they should.
2. EVERY entry in the table can actually be built - which is where the cost of
   importing each scorer's module inside its factory is paid. A typo in one of
   those imports would otherwise surface only on the one run that selects it.
3. The row adapters produce CaseRows the run record accepts, with `passed`
   appearing in the spine and NOT duplicated in the detail.
4. The agent scorer's spine verdict is `earned`, not `outcome` - so a right
   answer reached without doing the work scores FAILED. Driven by a scripted
   run and a scripted judge, with no model anywhere.
5. An unknown scorer name, and a file declaring `scorer: null`, both exit 2
   through the real entry point as a process.
6. The security property from ADR 0006 call 1 is checked STRUCTURALLY, by
   reading src/scorers.py for the forbidden constructs. A docstring promising
   "no dynamic import" does not stop anyone adding one; a check does.

CASE 5's THIRD SUBCASE IS THE POSITIVE CONTROL. The two refusal subcases assert
a message appears on stderr and the exit code is 2 - and an eval_harness that
crashed on startup, or refused every suite it was ever handed, would satisfy
both. So the third invokes it with the two REAL suites and requires the
resolved-scorers marker to appear. Same device as build step 1's case 2 and
part 2a's case 5; fourth time it has been needed.

NEEDS NO MODEL AND NO NETWORK - deliberately, so it runs in CI. The retrieval
factory is built with an injected index, which is enough to prove it builds;
actually SCORING a retrieval case needs Ollama and belongs to the checks Jose
runs (tests/test_retrieval_seam.py).

Run:  python tests/test_scorer_table.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

import run_record                                  # noqa: E402
import scorers                                     # noqa: E402
from agent import AgentResult, Step                # noqa: E402
from suite import parse_suite                      # noqa: E402


# HERMETIC INDEX (build step 8, found by the FIRST CI run, 2026-09-28).
# data/index.json is gitignored, so a fresh clone - which is what a CI runner
# is - does not have one. The retrieval factory reads that file for its
# CONDITIONS even when an index is injected, so this check passed only in homes
# that happened to hold the file. A test names its own index, as it names its
# own baseline (tests/test_isolation.py). The factory reads
# `retrieval.INDEX_PATH` at call time, so pointing it here is enough.
def _own_index() -> Path:
    import json as _json
    import tempfile as _tempfile
    import retrieval as _retrieval
    p = Path(_tempfile.mkdtemp()) / "index.json"
    p.write_text(_json.dumps({"model": "fixture-embedder", "chunks": []}), encoding="utf-8")
    _retrieval.INDEX_PATH = p
    return p

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


SUITES = REPO_ROOT / "evals" / "suites"
FIXTURES = REPO_ROOT / "evals" / "fixtures" / "judge-calibration.jsonl"

print("scorer table - build step 3 part 2b: a name resolves, or the run exits 2")
print()

# --- Case 1: the real suites resolve to the right factory. -----------------
print("case 1  each real suite resolves to its own factory")
for path, expected in ((SUITES / "retrieval.jsonl", scorers.make_retrieval_scorer),
                       (SUITES / "agent.jsonl", scorers.make_agent_scorer),
                       (SUITES / "review.jsonl", scorers.make_review_scorer),    # P03 step 7
                       (SUITES / "remediation.jsonl", scorers.make_remediation_scorer),  # P05 step 5
                       (SUITES / "command-center.jsonl", scorers.make_command_center_scorer)):  # P06 step 3
    got = scorers.resolve(parse_suite(path))
    check(f"{path.name} -> {expected.__name__}", got is expected,
          f"got {getattr(got, '__name__', got)!r}")
check("the table has exactly the six scorers built so far",   # + answer P02 6c, review P03 7, remediation P05 5, command-center P06 3
      sorted(scorers.SCORERS) == ["agent", "answer", "command-center", "remediation", "retrieval",
                                  "review"],
      str(sorted(scorers.SCORERS)))
print()

# --- Case 2: every entry builds. the lazy-import cost, paid. ---------------
print("case 2  every factory in the table can be built")
OWN_INDEX = _own_index()
built = {
    # An injected index: enough to prove the factory builds and imports cleanly,
    # without the embedding model that scoring a case would need.
    "retrieval": scorers.SCORERS["retrieval"](index=[]),
    "agent": scorers.SCORERS["agent"](runner=lambda case: None),
    # P02 step 6c: scripted generate + empty retriever - builds with no model.
    "answer": scorers.SCORERS["answer"](generate=lambda m, s: "{}", retriever=lambda q: []),
    # P03 step 7: a scripted pipeline - builds with no model.
    "review": scorers.SCORERS["review"](run_item=lambda item: None),
    # P05 step 5: a stand-in model - builds with no Ollama.
    "remediation": scorers.SCORERS["remediation"](live_text=lambda case, drift: "{}"),
    # P06 step 3: fakes only - builds with no model and no machine.
    "command-center": scorers.SCORERS["command-center"](),
}
for name, (scorer, conditions) in built.items():
    check(f"{name} builds and returns a callable", callable(scorer),
          f"got {type(scorer).__name__}")
    # BUILD STEP 3 PART 2c: a factory returns the scorer AND what it is about
    # to measure with, because the harness cannot know - the two scorers do
    # not use the same model.
    check(f"{name} reports its conditions", isinstance(conditions, scorers.Conditions),
          f"got {type(conditions).__name__}")
    check(f"{name} names a model and a digest",
          bool(conditions.model_name) and bool(conditions.model_digest),
          str(conditions))
# Stronger since step 8: the name below exists ONLY in this test's own index
# file, so matching it proves the value was READ, where matching the real
# "nomic-embed-text" could not tell reading from hardcoding.
check("retrieval's conditions come off the index file, with no network",
      built["retrieval"][1].model_name == "fixture-embedder"
      and built["retrieval"][1].model_digest == run_record.suite_sha256(OWN_INDEX),
      str(built["retrieval"][1]))
# 2026-09-29: host_target said "none - committed index" while data/index.json
# is gitignored - a false statement in every retrieval record. The check asks
# GIT, not a constant: if the index is ignored, the text may not claim it is
# committed, and it must name where the index actually lives.
_ignored = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "data/index.json"],
                          capture_output=True).returncode == 0
_ht = built["retrieval"][1].host_target


def _host_target_honest(text: str, ignored: bool) -> bool:
    return "data/index.json" in text and (not ignored or "committed" not in text)


check("git says data/index.json is ignored (the premise of the next check)", _ignored)
check("retrieval's host_target tells the truth about the index", _host_target_honest(_ht, _ignored),
      repr(_ht))
check("POSITIVE CONTROL: the old text would be caught",
      not _host_target_honest("none - committed index", True))
check("a scripted runner CANNOT pass itself off as a measurement",
      built["agent"][1].model_digest == "scripted-runner-no-model",
      str(built["agent"][1]))
print("       This is where the cost of importing each scorer's module inside its")
print("       factory is paid: a broken import fails HERE, not on the one run")
print("       that happens to select that scorer.")
print()

# --- Case 3: the row adapters, and the spine/detail split. -----------------
print("case 3  the adapters produce CaseRows the record accepts")
retrieval_case = {"id": "restart-symptoms", "tier": "fixed",
                  "question": "print jobs are piling up", "expect": "service-restart.md#symptoms"}
scored = {"question": "print jobs are piling up", "answerable": True,
          "expect": "service-restart.md#symptoms", "rank": 1, "top_score": 0.61,
          "hit_ids": ["service-restart.md#symptoms"], "passed": True}
row = scorers.retrieval_row(retrieval_case, scored)
check("id and tier come off the case", (row.id, row.tier) == ("restart-symptoms", "fixed"))
check("passed=True lands on the spine as result 'pass'", row.result == "pass", row.result)
check("passed is NOT duplicated in the detail", "passed" not in row.detail,
      str(sorted(row.detail)))
check("every other key survived into the detail",
      set(row.detail) == set(scored) - {"passed"}, str(sorted(row.detail)))
print("       A field written in two places is a field that can disagree with")
print("       itself, and summarize() reads the spine.")
print()

# --- Case 4: the agent spine verdict is `earned`. --------------------------
print("case 4  a right answer with the work not done scores FAILED")
cases = {c["id"]: c for c in parse_suite(SUITES / "agent.jsonl").cases}


def fake(calls: list[tuple[str, dict]], answer: str) -> AgentResult:
    steps = [Step(n=i + 1, action="call", tool=t, arguments=a, ok=True)
             for i, (t, a) in enumerate(calls)]
    steps.append(Step(n=len(calls) + 1, action="final"))
    return AgentResult(ok=True, answer=answer, reason="final", steps=steps)


four = [("get_service_status", {"name": n}) for n in ("Spooler", "W32Time", "WinRM", "wuauserv")]
target = cases["aggregate-down"]

# A scripted judge: hands back exactly the expected facts, so `outcome` is True
# in both runs below and the ONLY thing that differs is whether the work was
# done. No model is involved.
scripted = lambda question, answer, expected, **kw: expected          # noqa: E731

honest_scorer, _ = scorers.make_agent_scorer(
    runner=lambda c: fake(four, "Spooler and wuauserv are down."), judge=scripted)
shortcut_scorer, _ = scorers.make_agent_scorer(
    runner=lambda c: fake([("list_services", {})], "Spooler and wuauserv are down."),
    judge=scripted)
honest = honest_scorer(target)
shortcut = shortcut_scorer(target)

check("honest run: pass", honest.result == "pass", f"detail={honest.detail}")
check("shortcut run: outcome True in the detail", shortcut.detail["outcome"] is True)
check("shortcut run: trajectory False in the detail", shortcut.detail["trajectory"] is False)
check("shortcut run: result is 'fail' on the spine", shortcut.result == "fail")
check("the four required calls are named as missing", len(shortcut.detail["missing"]) == 4,
      f"missing={shortcut.detail['missing']}")
check("the answer text is in the detail on both",
      bool(honest.detail["answer"]) and bool(shortcut.detail["answer"]))
# ADDED AFTER THE FIRST REAL RUN: an outcome failure must be attributable.
# `expected` beside `extracted` is what separates "the agent had the facts
# wrong" from "the judge failed to read a correct answer" - two different
# defects that `outcome: False` alone cannot tell apart.
# NOT `row` as the loop variable: `row` is case 3's retrieval CaseRow, which
# case 4b still needs. Naming it `row` here shadowed it and case 4b silently
# counted the shortcut row twice - caught instantly by its hand-counted
# totals, which is the third time that habit has paid (step 2's `cases`,
# part 1's `judge_errors`, this).
for label, scored_row in (("honest", honest), ("shortcut", shortcut)):
    check(f"{label}: detail carries expected AND extracted",
          "expected" in scored_row.detail and "extracted" in scored_row.detail,
          str(sorted(scored_row.detail)))
check("expected is the case's own expected_facts",
      honest.detail["expected"] == target["expected_facts"], str(honest.detail["expected"]))
check("extracted is what the judge actually returned",
      honest.detail["extracted"] == target["expected_facts"], str(honest.detail["extracted"]))
# The POSITIVE CONTROL on that pair: a judge that reads something different
# must make the two fields DIFFER, or they are decoration.
wrong_scorer, _ = scorers.make_agent_scorer(
    runner=lambda c: fake(four, "Spooler and wuauserv are down."),
    judge=lambda q, a, exp, **kw: {"Spooler": "running"})
wrong = wrong_scorer(target)
check("a disagreeing judge makes expected != extracted",
      wrong.detail["expected"] != wrong.detail["extracted"],
      f"{wrong.detail['expected']} vs {wrong.detail['extracted']}")
check("...and that case fails on outcome, not trajectory",
      wrong.detail["outcome"] is False and wrong.detail["trajectory"] is True)
print("       Both runs produced the RIGHT ANSWER. If the spine carried `outcome`")
print("       the two would be indistinguishable, and Lab 9 exists because a")
print("       right answer can be reached by guessing.")
print()

# --- Case 4b: the record accepts these rows and counts them. ---------------
print("case 4b the run record reads these rows back and derives its own totals")
header = run_record.Header(
    suite_path=str(SUITES / "agent.jsonl"),
    suite_sha256=run_record.suite_sha256(SUITES / "agent.jsonl"), suite_header_sha256="a" * 64,
    tier_selection="all", scorer="agent",
    model_name="qwen3:8b", model_digest="test-fixture", host_target="none", sampling=None, rebaseline=None,
)
out = Path(tempfile.mkdtemp())
written = run_record.write(out, header, [__import__("dataclasses").replace(r, case_sha256="c" * 64) for r in [honest, shortcut, row]])  # the harness stamps this
back = run_record.read(written)
summary = run_record.summarize(back)
# Hand-counted from the three rows above. Literals, not computed here.
check("case_count == 3", summary["case_count"] == 3, str(summary["case_count"]))
check("gating == 3 (all three are fixed)", summary["gating"] == 3, str(summary["gating"]))
check("gating_passed == 2", summary["gating_passed"] == 2, str(summary["gating_passed"]))
check("gating_failed == 1", summary["gating_failed"] == 1, str(summary["gating_failed"]))
check("no total was stored in the file",
      "case_count" not in back and "gating_passed" not in back, str(sorted(back)))
print()

# --- Case 5: the real entry point, as a process. ---------------------------
print("case 5  the harness exits 2 on an unresolvable scorer, and 2 with a marker on a good one")


def harness(*suite_paths: str, tier: str = "fixed") -> subprocess.CompletedProcess:
    argv = [sys.executable, str(SRC / "eval_harness.py"), "--tier", tier,
            "--baseline", str(Path(tempfile.mkdtemp()) / "no-baseline.json")]  # ISOLATION: never the repo's committed baseline (see tests/test_isolation.py)
    for s in suite_paths:
        argv += ["--suite", s]
    return subprocess.run(argv, capture_output=True, text=True, cwd=REPO_ROOT)


bad = Path(tempfile.mkdtemp()) / "bad.jsonl"
bad.write_text("\n".join([
    json.dumps({"suite": "bad", "scorer": "definitely-not-a-scorer"}),
    json.dumps({"id": "a", "tier": "fixed", "question": "q"}),
]) + "\n", encoding="utf-8")

got = harness(str(bad))
check("unknown scorer: exit 2", got.returncode == 2, f"exit={got.returncode}")
check("unknown scorer: the name is in the message",
      "definitely-not-a-scorer" in got.stderr, got.stderr.strip()[:120])
check("unknown scorer: the known names are listed",
      "'agent'" in got.stderr and "'retrieval'" in got.stderr, got.stderr.strip()[:160])

got = harness(str(FIXTURES))
check("scorer=null: exit 2", got.returncode == 2, f"exit={got.returncode}")
check("scorer=null: the message says it is not a runnable suite",
      "not a runnable suite" in got.stderr, got.stderr.strip()[:120])

# THE POSITIVE CONTROL. Both assertions above pass equally if the harness
# refuses everything or dies on import, so something has to prove the good
# path is reachable.
#
# REWRITTEN AT PART 2c, and the reason is worth keeping. At 2b this invoked
# the harness on both real suites and asserted the "scorers resolved" marker,
# because 2b could not measure and always stopped there. 2c MEASURES - so that
# same invocation now needs Ollama, and this check would have quietly become a
# test of whether the bench was up.
#
# So the control now uses a REAL suite with `--tier live`, which selects zero
# cases. That path runs parse_suite() and resolve() and stops before the
# factory, so reaching it proves the suite loaded and its scorer RESOLVED -
# and the message is different from both refusals above, which is what makes
# it a control rather than a third way of failing.
#
# A full measuring run is proved elsewhere: in-process with a scripted runner
# in tests/test_measured_run.py, and on the bench by Jose.
got = harness(str(SUITES / "agent.jsonl"), tier="live")
check("a real suite gets PAST resolution (the good path is reachable)",
      "selected 0 of 13 cases" in got.stderr, got.stderr.strip()[-200:])
check("...and was NOT refused by the scorer table",
      "not in the scorer table" not in got.stderr and "not a runnable" not in got.stderr)
# Build step 4 settled this and dropped the provisional flag - see
# tests/test_report.py case 8 for the ruling and its reasoning.
check("an empty tier selection is exit 2, for a stated reason",
      got.returncode == 2 and "drained" in got.stderr, f"exit={got.returncode}")
check("the per-tier counts are on the face of the message",
      "fixed=13 live=0" in got.stderr, got.stderr.strip()[-200:])
print("       Without this, a harness that refused every suite it was ever handed")
print("       would pass the two refusal checks with a clean sheet.")
print()

# --- Case 6: ADR 0006 call 1, checked structurally. ------------------------
print("case 6  the scorer table cannot be reached by a data file (checked, not promised)")
source = (SRC / "scorers.py").read_text(encoding="utf-8")
code_lines = [ln for ln in source.splitlines()
              if not ln.lstrip().startswith("#")]
code = "\n".join(code_lines)
# `getattr` itself is NOT on this list, and that is not an oversight: it is
# used legitimately in agent_row to read an answer off a result. A ban with a
# false positive gets deleted by the next person; a ban that names only the
# constructs that could turn a STRING into CODE survives.
for forbidden in ("importlib", "__import__", "eval(", "exec(", "entry_points"):
    # The docstring names these to explain the constraint, so only the code
    # below the docstring is searched - split on the closing triple quote.
    body = code.split('"""', 2)[-1]
    check(f"no {forbidden!r} anywhere in the code", forbidden not in body)
check("every table value is a callable, not a string or a path",
      all(callable(v) for v in scorers.SCORERS.values()),
      str({k: type(v).__name__ for k, v in scorers.SCORERS.items()}))
check("every table key is a plain string",
      all(isinstance(k, str) for k in scorers.SCORERS))
print("       Jose's reason, ADR 0006 call 1: a data file must not be able to")
print("       choose which code executes, and Project 2 ingests documents we")
print("       did not author.")
print()

# --- Case 7: an unexpected crash is exit 2, NOT exit 1. --------------------
print("case 7  an unexpected crash exits 2, because exit 1 already means something")
import argparse                                    # noqa: E402
import eval_harness                                # noqa: E402

original = eval_harness.run
try:
    for exc_type in (TypeError, KeyError, AttributeError, RuntimeError):
        def boom(args, exc_type=exc_type):
            raise exc_type("scorer handed back something unexpected")
        eval_harness.run = boom
        code = eval_harness.main(["--suite", str(SUITES / "agent.jsonl"), "--tier", "fixed",
                                  "--baseline", str(Path(tempfile.mkdtemp()) / "none.json")])
        check(f"{exc_type.__name__} -> exit 2", code == 2, f"got {code}")
finally:
    eval_harness.run = original

check("and run() is back in place afterwards", eval_harness.run is original)
# The control: a NORMAL invocation through the same main() still reaches run().
# PINNED to no model (build step 8, ADR 0010): this used to pass only because
# the shell running it could not reach Ollama; on the bench it ran the real
# suite and returned 1. Same defect as test_eval_harness.py case 2.
import os                                          # noqa: E402
_saved = os.environ.get("OLLAMA_BASE_URL")
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:9"
try:
    code = eval_harness.main(["--suite", str(SUITES / "agent.jsonl"), "--tier", "fixed",
                              "--out", tempfile.mkdtemp(),
                              "--baseline", str(Path(tempfile.mkdtemp()) / "none.json")])  # ISOLATION: never the repo's committed baseline (see tests/test_isolation.py)
finally:
    if _saved is None:
        os.environ.pop("OLLAMA_BASE_URL", None)
    else:
        os.environ["OLLAMA_BASE_URL"] = _saved
check("a normal invocation with no model reachable returns run()'s own 2", code == 2,
      f"got {code}")
print("       Python exits 1 on an uncaught exception, and exit 1 in this harness")
print("       means A GATING CASE FAILED. Without the catch in main(), every")
print("       crash would reach CI as a quality regression that never happened -")
print("       Project 1's auditpol lesson in a new place.")
print()

if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
