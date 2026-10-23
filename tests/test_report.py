"""Build step 4 check: the report says what ran, what it was measured against, what failed.

THE STEP'S STATED CHECK is case 1: all three required sections present on a run
with NO BASELINE AT ALL - which is the only state that exists until build step
6 can write one. The per-tier counts, the baseline block and the drift block
must all appear and all be honest about having nothing to compare.

CASE 6 IS THE ONE THAT WOULD HAVE CAUGHT THE DEFECT THIS FILE WAS REWRITTEN TO
AVOID. The report was very nearly built to count failures as "trajectory" vs
"outcome-only" - the AGENT scorer's words. A retrieval row has neither; it has
a rank and a top score. So case 6 renders a RETRIEVAL record with a failing row
and requires retrieval's own keys to appear, which a report carrying agent
vocabulary could not do.

CASE 5 IS THE POSITIVE CONTROL: flipping one row's `result` must move the
GATING line. A render() that printed plausible constants would pass cases 1-4
with a clean sheet - step 2's sabotage, three steps later.

NO MODEL, NO NETWORK. git is shelled out to for the baseline's commit, and case
3 covers it being unable to answer.

Run:  python tests/test_report.py
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

import report                                      # noqa: E402
import run_record                                  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def row(case_id: str, tier: str, passed: bool, detail: dict, note: str = "") -> dict:
    return {"id": case_id, "tier": tier, "case_sha256": "c" * 64, "result": "pass" if passed else "fail",
            "detail": detail, "note": note}


def record(rows: list[dict], *, scorer: str = "agent", tier_selection: str = "fixed") -> dict:
    return {
        "record_version": run_record.RECORD_VERSION,
        "header": {
            "suite_path": f"evals/suites/{scorer}.jsonl",
            "suite_sha256": "b" * 64, "suite_header_sha256": "a" * 64, "tier_selection": tier_selection, "scorer": scorer,
            "model_name": "qwen3:8b", "model_digest": "sha256:deadbeef",
            "host_target": "fixture", "sampling": None, "rebaseline": None, "started_at": "2026-09-21T00:00:00Z",
            "incomplete": None,     # record v3: a record must SAY it finished
            "record_version": run_record.RECORD_VERSION,
        },
        "cases": rows,
    }


AGENT = record([
    row("status-spooler", "fixed", True, {"outcome": True, "trajectory": True}),
    row("aggregate-down", "fixed", False,
        {"outcome": False, "trajectory": False, "answer": "only Spooler",
         "expected": {"down": ["Spooler", "wuauserv"]}, "extracted": {"down": ["Spooler"]},
         "missing": ["get_service_status({\"name\": \"wuauserv\"})"], "extra": 0}),
    row("live-ish", "live", False, {"outcome": False, "trajectory": True}, note="judge error: X"),
])

print("report - build step 4: what ran, what against, what failed")
print()

# --- Case 1: THE STATED CHECK - no baseline, all three sections present. ---
print("case 1  all three sections appear on a run with no baseline at all")
lines = report.render(AGENT, record_path="evals/runs/x.json")
text = "\n".join(lines)
check("the per-tier counts print", "fixed=2" in text and "live=1" in text,
      next((l for l in lines if l.strip().startswith("tiers")), "<no tiers line>"))
check("the tier SELECTION prints beside them", "--tier fixed" in text)
check("the baseline section says NONE and names the path",
      "BASELINE   NONE" in text and "evals/baselines/agent.json" in text)
check("it says how a baseline is created", "--rebaseline" in text)
check("the drift section appears", "DRIFT" in text)
check("...and says why it is empty", "no baseline to compare against" in text)
check("the record path prints", "evals/runs/x.json" in text)
check("gating counts only the fixed tier", "GATING     1/2 passed" in text,
      next((l for l in lines if "GATING" in l), ""))
check("the live row is reported, not gated", "reported   0/1 passed" in text,
      next((l for l in lines if "reported" in l), ""))
print()

# --- Case 2: a baseline that exists prints its commit and date. -----------
print("case 2  a baseline prints its commit and the date it was recorded")
tmp = Path(tempfile.mkdtemp()) / "agent.json"
tmp.write_text(json.dumps(record([row("status-spooler", "fixed", True, {})])), encoding="utf-8")
based = "\n".join(report.render(AGENT, record_path="r.json",
                                baseline=run_record.read(tmp), baseline_file=tmp))
check("the baseline's path prints", str(tmp) in based)
check("a commit line prints", "commit " in based,
      next((l for l in based.splitlines() if "commit" in l), ""))
check("the date it was RECORDED prints", "recorded 2026-09-21T00:00:00Z" in based)
check("...and it no longer says NONE", "BASELINE   NONE" not in based)
print()

# --- Case 3: git provenance, both ways. -----------------------------------
print("case 3  git provenance answers for a committed file and says so for others")
committed = report.git_provenance(REPO_ROOT / "README.md")   # a file every clone has - the public showcase too
check("a committed file yields '<sha> <iso date>'",
      len(committed.split()) == 2 and committed.split()[1].startswith("20"), committed)
uncommitted = sorted((REPO_ROOT / "evals" / "runs").glob("*.json"))
if uncommitted:
    # evals/runs/* is gitignored (ADR 0008 4a), so git has never seen these.
    answer = report.git_provenance(uncommitted[-1])
    check("a file git has never seen is named as NOT COMMITTED",
          "NOT COMMITTED" in answer, answer)
else:
    check("a file git has never seen is named as NOT COMMITTED", True, "skipped: no run records")
check("a missing file does not raise", isinstance(report.git_provenance("no/such/file"), str))
print()

# --- Case 4: every failing row, and only failing rows. --------------------
print("case 4  failing rows are itemised in their own words; passing rows are not")
check("the failing row appears", "aggregate-down" in text)
check("the passing row does NOT", "status-spooler" not in text)
for key in ("expected", "extracted", "missing", "answer"):
    check(f"detail key {key!r} is printed", f"{key:<12}" in text or f"{key} " in text)
check("a row's note prints", "judge error: X" in text)
check("the report states it does NOT attribute the failure",
      "does NOT attribute" in text)
check("the gating/reported status of each failing row is shown",
      "[fixed, gating]" in text and "[live, reported]" in text)
print("       Everything that needed a throwaway script at part 2c is now on the")
print("       face of the report.")
print()

# --- Case 5: THE POSITIVE CONTROL. ---------------------------------------
print("case 5  positive control - flipping one row moves the numbers")
flipped = copy.deepcopy(AGENT)
flipped["cases"][0]["result"] = "fail"               # the one passing fixed row
moved = "\n".join(report.render(flipped, record_path="r.json"))
check("GATING moved 1/2 -> 0/2", "GATING     0/2 passed" in moved,
      next((l for l in moved.splitlines() if "GATING" in l), ""))
check("the newly failing row is now itemised", "status-spooler" in moved)
check("FAILURES count moved 2 -> 3", "FAILURES   3 row(s)" in moved,
      next((l for l in moved.splitlines() if "FAILURES" in l), ""))
print("       Without this, a render() printing plausible constants would pass")
print("       cases 1-4 with a clean sheet.")
print()

# --- Case 6: the report is SCORER-AGNOSTIC. ------------------------------
print("case 6  a retrieval record renders in retrieval's vocabulary, not the agent's")
RETRIEVAL = record([
    row("restart-symptoms", "fixed", True,
        {"answerable": True, "rank": 1, "top_score": 0.61, "hit_ids": ["a#b"]}),
    row("patching-procedure", "fixed", False,
        {"answerable": True, "rank": 0, "top_score": 0.44, "hit_ids": ["x#y"],
         "question": "how do I prove the reboot applied", "expect": "patching-window.md#procedure"}),
], scorer="retrieval")
ret = "\n".join(report.render(RETRIEVAL, record_path="r.json"))
check("it renders at all", "GATING     1/2 passed" in ret)
for key in ("rank", "top_score", "hit_ids", "expect"):
    check(f"retrieval's own key {key!r} is printed", key in ret)
check("no agent vocabulary is invented for it",
      "trajectory" not in ret and "outcome" not in ret)
check("the baseline path follows the SUITE, not the scorer name",
      "evals/baselines/retrieval.json" in ret)
print("       The report was nearly built to count 'trajectory' vs 'outcome-only'")
print("       failures - the AGENT scorer's words. This case is why it itemises")
print("       instead: summarize() and anything like it may touch the spine only.")
print()

# --- Case 7: baseline_path resolution. -----------------------------------
print("case 7  baseline_path defaults from the suite stem and yields to --baseline")
check("default is evals/baselines/<stem>.json",
      report.baseline_path("evals/suites/agent.jsonl") == Path("evals/baselines/agent.json"),
      str(report.baseline_path("evals/suites/agent.jsonl")))
check("an explicit --baseline wins",
      report.baseline_path("evals/suites/agent.jsonl", "/tmp/other.json") == Path("/tmp/other.json"))
print()

# --- Case 8: the empty-tier ruling is settled, not provisional. ----------
print("case 8  an empty tier selection is exit 2, and no longer calls itself provisional")
done = subprocess.run(
    [sys.executable, str(SRC / "eval_harness.py"), "--suite",
     str(REPO_ROOT / "evals" / "suites" / "agent.jsonl"), "--tier", "live",
     "--out", str(Path(tempfile.mkdtemp())),
     "--baseline", str(Path(tempfile.mkdtemp()) / "none.json")],  # ISOLATION (test_isolation.py)
    capture_output=True, text=True, cwd=REPO_ROOT)
check("exit 2", done.returncode == 2, f"exit={done.returncode}")
check("the word 'provisional' is GONE", "provisional" not in done.stderr,
      done.stderr.strip()[-120:])
check("and the reason is stated: drained is indistinguishable",
      "drained" in done.stderr, done.stderr.strip()[-120:])
print()

if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
