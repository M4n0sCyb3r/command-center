"""No test may read the repo's committed baselines (found 2026-09-28, build step 7).

THE DEFECT. Every test that drove the harness without `--baseline` fell back to
the REAL `evals/baselines/<suite>.json`. Until step 6 no such file existed, so
"no baseline" was true by accident. The day the first baselines were committed
(57c8d20), `test_drift`'s "no baseline" case went red - and the step was closed
on a suite run taken BEFORE the baselines landed, so it was pushed red
(6c26832: 617/618). `test_stall` passed only because it was silently comparing
its scripted run against the real baseline.

THE RULE: a test names its own baseline, in a temp directory. A test's result
must not depend on what a human last promoted.

  case 1  every test file that invokes the harness passes `--baseline`
  case 2  POSITIVE CONTROL: the scan finds the invoking files (a scan matching
          nothing proves nothing), and flags a planted file without the flag

This is a FILE-level scan - weaker than per-call. The DYNAMIC proof was run
once, on 2026-09-28: the whole suite went green while the committed baselines
were v6 files v7 code cannot read. Re-run it that way after any harness change:
it is the check that cannot be fooled by a comment.

Run:  python tests/test_isolation.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


INVOKES = re.compile(r"eval_harness\.(run|main)\(|eval_harness\.py")


def offenders(sources: dict[str, str]) -> tuple[list[str], list[str]]:
    invoking = sorted(n for n, s in sources.items() if INVOKES.search(s))
    return invoking, [n for n in invoking if '"--baseline"' not in sources[n]]


sources = {p.name: p.read_text(encoding="utf-8") for p in TESTS.glob("test_*.py")
           if p.name != Path(__file__).name}
invoking, bad = offenders(sources)

print("case 1  every harness-invoking test names its own baseline")
check("no offenders", not bad, str(bad))

print("\ncase 2  positive control")
check("the scan finds the invoking files (at least the 6 fixed today)", len(invoking) >= 6,
      str(invoking))
_, planted = offenders({"test_planted.py": 'eval_harness.run(parse(["--suite", "x"]))'})
check("a planted invocation without --baseline is flagged", planted == ["test_planted.py"])

print()
if failures:
    print(f"{len(failures)} check(s) FAILED")
    sys.exit(1)
print("all checks passed")
