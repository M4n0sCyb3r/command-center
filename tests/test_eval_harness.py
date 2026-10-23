"""Build step 1 check: the argument gate holds before anything is executed.

WHY THIS IS A SUBPROCESS AND NOT AN IMPORT
------------------------------------------
Calling build_parser().parse_args([]) in-process raises SystemExit, so the test
would wrap it in try/except and inspect .code - which tests argparse's
internals, not this program's contract. CI does not catch an exception. CI
reads an exit code from a process. So the check launches the real CLI exactly
the way CI will, and reads exactly what CI reads.

WHY THERE IS A POSITIVE CONTROL
-------------------------------
The load-bearing assertion in case 1 is that run() was NEVER REACHED, observed
by the absence of a marker string in stderr. An absence assertion passes for
two different reasons: the gate held, or the marker never gets emitted at all
(misspelled, removed, silenced). Those are indistinguishable without a case
that makes the marker APPEAR. Case 2 is that case.

This is Project 1's lesson applied to a test instead of a design: a test that
passes under both your design and the design you rejected has not tested
anything. DC01 worked first try and proved nothing until the GPO link was
disabled on purpose.

Run:  python tests/test_eval_harness.py     (from the repo root or anywhere)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "src" / "eval_harness.py"

# Build step 2: case 4's positive control writes a real record, so it needs the
# real writer. Importing it here rather than shelling out keeps the control
# honest - it proves THE THING THAT WRITES RECORDS puts a file in that
# directory, not that some test helper can create a file.
sys.path.insert(0, str(REPO_ROOT / "src"))
import run_record as rr  # noqa: E402

# Kept in sync with eval_harness._REACHED_RUN by the last check in this file,
# so a rename over there fails here instead of silently disarming case 1.
REACHED_RUN = "eval_harness: past the argument gate"

EXIT_TOOLING_ERROR = 2

# PINNED: no model is reachable from any invocation in this file (build step 8,
# ADR 0010). Case 2 used to assert "still exits 2 - no runner yet". The runner
# arrived at step 3 part 2c, and from then on the check passed ONLY because the
# shell running it could not reach Ollama. On the bench, with Ollama up, it ran
# the real agent suite and got exit 1 - found by the first `--home bench` run.
# A check whose verdict depends on the home it runs in is not a check. Port 9
# (discard) on loopback refuses at once, in every home.
NO_MODEL_ENV = {**os.environ, "OLLAMA_BASE_URL": "http://127.0.0.1:9"}

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def invoke(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the harness the way CI will: as a process, from the repo root."""
    return subprocess.run(
        [sys.executable, str(HARNESS), *args,
         "--baseline", str(Path(tempfile.mkdtemp()) / "none.json")],  # ISOLATION: never the repo's committed baseline (see tests/test_isolation.py)
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=NO_MODEL_ENV,
    )


print("eval_harness - build step 1: the argument surface")
print("                 (amended at step 2: --suite required, case 4 discharged)")
print()

# --- Case 1: the gate. --tier omitted. -------------------------------------
print("case 1  --tier omitted (the gate)")
r = invoke("--suite", "evals/suites/agent.jsonl")
check("exits 2, not 0 and not 1", r.returncode == EXIT_TOOLING_ERROR, f"got {r.returncode}")
check("stderr names --tier", "--tier" in r.stderr)
check("stderr says it is required", "required" in r.stderr.lower())
check("run() was NEVER reached", REACHED_RUN not in r.stderr)
check("nothing written to stdout", r.stdout == "", repr(r.stdout[:60]))
print()

# --- Case 2: positive control. The marker must be able to appear. ----------
print("case 2  --tier supplied (positive control - proves case 1 can fail)")
r2 = invoke("--suite", "evals/suites/agent.jsonl", "--tier", "fixed",
            "--out", tempfile.mkdtemp())
check("run() WAS reached", REACHED_RUN in r2.stderr)
check(
    "with no model reachable, exits 2 - a missing dependency is never a verdict",
    r2.returncode == EXIT_TOOLING_ERROR,
    f"got {r2.returncode}",
)
print()

# --- Case 3: --tier is constrained, not just present. ----------------------
print("case 3  --tier with an invalid value")
r3 = invoke("--suite", "evals/suites/agent.jsonl", "--tier", "smoke")
check("exits 2", r3.returncode == EXIT_TOOLING_ERROR, f"got {r3.returncode}")
check("stderr names the allowed values", "fixed" in r3.stderr and "live" in r3.stderr)
check("run() was NEVER reached", REACHED_RUN not in r3.stderr)
print()

# --- Case 4: the gate writes nothing. --------------------------------------
#
# STEP 1 DECLARED THIS CASE VACUOUS IN ITS OWN OUTPUT, and it was: nothing in
# the repo wrote run records, so "no run record was written" could not fail
# however broken the gate became. The note said it becomes load-bearing at step
# 2. Discharging that is not a matter of deleting the note - it means adding
# the control that makes the assertion capable of failing, and OBSERVING it.
#
# The control writes a real record into the same directory with the same writer
# the harness will use. If the directory comparison cannot see that, it could
# never have seen a record the harness wrote either, and the whole case was
# decoration.
print("case 4  the rejected invocation leaves --out untouched")
with tempfile.TemporaryDirectory() as tmp:
    before = set(Path(tmp).iterdir())
    invoke("--suite", "evals/suites/agent.jsonl", "--out", tmp)
    after = set(Path(tmp).iterdir())
    check("no run record written", before == after)

    # POSITIVE CONTROL - proves the assertion above is capable of failing.
    rr.write(
        tmp,
        rr.Header(
            suite_path="evals/suites/agent.jsonl",
            suite_sha256="0" * 64, suite_header_sha256="a" * 64,
            tier_selection="fixed",
            scorer="score.score_case",
            model_name="qwen3:8b",
            model_digest="sha256:0000",
            host_target="fake-host",
            sampling=None, rebaseline=None,
            started_at="2026-09-21T18:00:00Z",
        ),
        [],
    )
    check(
        "a record written HERE is detected (so case 4 can fail)",
        set(Path(tmp).iterdir()) != after,
    )
print()

# --- Case 4b: --suite is now required too (build step 2). ------------------
print("case 4b  --suite omitted (the second half of the gate, step 2)")
r4 = invoke("--tier", "fixed")
check("exits 2", r4.returncode == EXIT_TOOLING_ERROR, f"got {r4.returncode}")
check("stderr names --suite", "--suite" in r4.stderr)
check("stderr says it is required", "required" in r4.stderr.lower())
check("run() was NEVER reached", REACHED_RUN not in r4.stderr)
print()

# --- Case 5: the marker this file asserts on still exists over there. ------
print("case 5  the marker string is the one the harness actually emits")
src = (REPO_ROOT / "src" / "eval_harness.py").read_text(encoding="utf-8")
check(f"{REACHED_RUN!r} found in src/eval_harness.py", REACHED_RUN in src)
print()

if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
