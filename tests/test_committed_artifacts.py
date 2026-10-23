"""Build step 8 check: the files the bench will load are loadable. ADR 0010.

WHY THIS EXISTS
---------------
tests/test_isolation.py forbids tests that DRIVE the harness from reading the
committed baselines - correctly: a test's verdict must not depend on what a
human last promoted. The side effect: nothing checked the committed baselines
at all, so a push that left them unreadable was found only on the bench, days
later. Step 7 is the precedent - v7 code could not read the v6 baselines.

This file's SUBJECT is the committed files. It reads them as data under test,
never as a fallback, and it never runs the harness, so the isolation rule is
untouched. (test_suite_header.py already parses the suites one by one; this
file checks suites and baselines as a SET - every baseline pairs with a suite.)

WHAT IT DELIBERATELY DOES NOT CHECK
-----------------------------------
Whether a baseline's identity still matches its suite. Editing a suite and then
rebaselining on the bench is the normal order of work; failing here would make
CI red during correct work. ADR 0007 keeps identity out of every exit code.

  case 1  every committed suite parses; every committed baseline is readable by
          THIS code (run_record.read), and names a suite that exists
  case 2  POSITIVE CONTROLS: the scans find the files, and a planted old-version
          baseline and a planted broken suite are both refused

Run:  python tests/test_committed_artifacts.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import run_record  # noqa: E402
import suite  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def refused(fn, path) -> str | None:
    """Return the refusal message, or None if the file was accepted."""
    try:
        fn(path)
    except ValueError as exc:
        return str(exc)
    return None


suites = sorted((REPO / "evals" / "suites").glob("*.jsonl"))
baselines = sorted((REPO / "evals" / "baselines").glob("*.json"))

print("case 1  the committed suites and baselines load")
for s in suites:
    msg = refused(suite.parse_suite, s)
    check(f"suite parses: {s.name}", msg is None, msg or "")
for b in baselines:
    msg = refused(run_record.read, b)
    check(f"baseline readable by record v{run_record.RECORD_VERSION}: {b.name}", msg is None,
          msg or "")
    if msg is None:
        named = json.loads(b.read_text(encoding="utf-8"))["header"]["suite_path"]
        check(f"baseline names a suite that exists: {b.name} -> {named}",
              (REPO / named).is_file())

print("\ncase 2  positive controls")
check("the scan finds the suites (2 today)", len(suites) >= 2, str([s.name for s in suites]))
check("the scan finds the baselines (2 today)", len(baselines) >= 2,
      str([b.name for b in baselines]))

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    if baselines:
        old = json.loads(baselines[0].read_text(encoding="utf-8"))
        old["record_version"] = run_record.RECORD_VERSION - 1
        planted = tmp / "old.json"
        planted.write_text(json.dumps(old), encoding="utf-8")
        msg = refused(run_record.read, planted)
        check("a baseline one record version behind is refused", msg is not None
              and "record_version" in msg, msg or "ACCEPTED")
    broken = tmp / "broken.jsonl"
    broken.write_text('{"suite": "x", "scorer": "agent"}\n{not json\n', encoding="utf-8")
    msg = refused(suite.parse_suite, broken)
    check("a broken suite is refused", msg is not None, msg or "ACCEPTED")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED")
    sys.exit(1)
print("all checks passed")
