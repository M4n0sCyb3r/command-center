"""P03 build step 3: the review contract and the catch scorer, proven without a model.

  case 1  the schema: a valid review passes; 6 findings, an unknown category, a line
          outside the file, an extra key are each refused
  case 2  rules a schema cannot say: line_start > line_end refused; verdict must be
          revise IFF a finding blocks (both directions refused)
  case 3  the catch rule: blocking overlap within +-1 catches; 2 lines away misses;
          an ADVISORY finding on the exact line does not catch; a second range counts;
          right_reason is reported, never required
  case 4  controls: a blocking finding is a false alarm; advisory-only is not
  case 5  the set: 48 items (36 defects + 12 controls), 32 development / 16 held out;
          every defect's lines are inside its file; every oracle matches its defect and
          NOT its control (re-checked here, not trusted from step 2)
  case 6  the zero-model arm: PSScriptAnalyzer's saved readings - which must LIST every
          file they scanned - score development at 6/24 caught (lint-visible 6/6,
          logic 0/12, blast 0/6), 6 right reason, 0/8 false alarms; the cap applies
  case 7  a reading that cannot prove what it read is refused: a bare [] (the
          wrong-folder scan of 2026-10-18), and an item no reading lists

Run:  python tests/test_review.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import review as R                                   # noqa: E402
from structured import SchemaError                   # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def f(a: int, b: int | None = None, cat: str = "correctness", sev: str = "blocking") -> dict:
    return {"line_start": a, "line_end": a if b is None else b, "category": cat,
            "severity": sev, "why": "x", "fix": ""}


def refused(data: dict, n: int = 40) -> bool:
    try:
        R.validate_review(data, n)
    except SchemaError:
        return True
    return False


print("case 1  the schema")
check("a valid review passes", not refused({"verdict": "revise", "findings": [f(3)]}))
check("an empty approve passes", not refused({"verdict": "approve", "findings": []}))
check("6 findings refused (the cap)", refused({"verdict": "revise", "findings": [f(i) for i in range(1, 7)]}))
check("unknown category refused", refused({"verdict": "revise", "findings": [f(3, cat="bug")]}))
check("line 0 refused", refused({"verdict": "revise", "findings": [f(0)]}))
check("line past the end refused", refused({"verdict": "revise", "findings": [f(41)]}, 40))
check("extra key refused", refused({"verdict": "revise", "findings": [{**f(3), "confidence": 0.9}]}))
check("an over-long why refused (the 24,000-char runaway)", refused({"verdict": "revise", "findings": [{**f(3), "why": "x" * (R.MAX_WHY + 1)}]}))

print("case 2  what a schema cannot say")
check("line_start > line_end refused", refused({"verdict": "revise", "findings": [f(9, 4)]}))
check("revise with no blocking finding refused", refused({"verdict": "revise", "findings": [f(3, sev="advisory")]}))
check("approve with a blocking finding refused", refused({"verdict": "approve", "findings": [f(3)]}))

print("case 3  the catch rule")
d = R.Item("t-L", "t.ps1", "development", Path("t.ps1"),
           {"lines": [[22, 22]], "family": "logic", "expected_category": "correctness"})
two = R.Item("t2-L", "t.ps1", "development", Path("t.ps1"),
             {"lines": [[14, 17], [38, 39]], "family": "lint-visible", "expected_category": "security"})
rv = lambda *fs: R.validate_review({"verdict": "revise" if any(x["severity"] == "blocking" for x in fs) else "approve",
                                    "findings": list(fs)}, 60)
check("exact line catches", R.score_item(d, rv(f(22)))["caught"])
check("one line off catches (+-1)", R.score_item(d, rv(f(23)))["caught"] and R.score_item(d, rv(f(20, 21)))["caught"])
check("two lines off misses", not R.score_item(d, rv(f(24)))["caught"] and not R.score_item(d, rv(f(20)))["caught"])
check("advisory on the exact line does not catch", not R.score_item(d, rv(f(22, sev="advisory")))["caught"])
check("a hit on the second range catches", R.score_item(two, rv(f(39, cat="security")))["caught"])
row = R.score_item(d, rv(f(22, cat="style")))
check("caught for the wrong reason: caught, right_reason False", row["caught"] and not row["right_reason"])
check("caught for the right reason", R.score_item(d, rv(f(22)))["right_reason"])

print("case 4  controls")
c = R.Item("t-C", "t.ps1", "development", Path("t.ps1"))
check("blocking on a control is a false alarm", R.score_item(c, rv(f(5)))["false_alarm"])
check("advisory only is not", not R.score_item(c, rv(f(5, sev="advisory")))["false_alarm"])

print("case 5  the set")
items = R.load_set()
defects = [i for i in items if not i.is_control]
controls = [i for i in items if i.is_control]
check("48 items: 36 defects + 12 controls", (len(items), len(defects), len(controls)) == (48, 36, 12),
      f"{len(items)}/{len(defects)}/{len(controls)}")
dev = [i for i in items if i.split == "development"]
check("32 development (24 + 8), 16 held out (12 + 4)",
      len(dev) == 32 and sum(i.is_control for i in dev) == 8 and len(items) - len(dev) == 16)
check("every defect's lines lie inside its file",
      all(1 <= a <= b <= i.n_lines() for i in defects for a, b in i.defect["lines"]))
ctrl = {i.script: i for i in controls}
bad = [i.id for i in defects
       if not re.search(i.defect["oracle"], i.text(), re.M)
       or re.search(i.defect["oracle"], ctrl[i.script].text(), re.M)]
check("every oracle matches its defect and not its control", not bad, str(bad))

print("case 6  the zero-model arm (PSScriptAnalyzer 1.25.0, saved readings)")
ev = REPO_ROOT / "projects" / "03-script-review-pipeline" / "evidence"
ref_reading, def_reading = ev / "pssa-reference-1.25.0.json", ev / "pssa-defects-1.25.0.json"
try:
    ref_files, _ = R.read_pssa(ref_reading)
    def_files, def_hits = R.read_pssa(def_reading)
    readable = True
except (OSError, R.ReadingError, ValueError) as exc:
    readable, ref_files, def_files, def_hits = False, [], [], []
    print(f"       {exc}")
check("both readings load and list their files", readable)
check("the reference reading lists all 12 originals", sorted(ref_files) == sorted(c.path.name for c in controls),
      f"{len(ref_files)} listed")
check("the defects reading lists all 36 copies", sorted(def_files) == sorted(d.path.name for d in defects),
      f"{len(def_files)} listed")
check("9 findings, one per lint-visible copy", len(def_hits) == 9 and
      {h["ScriptName"] for h in def_hits} == {d.path.name for d in defects if d.defect["family"] == "lint-visible"})
s = R.summarize(R.pssa_arm(dev, def_reading, ref_reading)) if readable else {}
check("development: 6/24 caught", (s.get("caught"), s.get("defects")) == (6, 24), str(s.get("caught")))
check("by family: lint 6/6, logic 0/12, blast 0/6",
      s.get("by_family") == {"lint-visible": {"caught": 6, "of": 6}, "logic": {"caught": 0, "of": 12},
                             "blast-radius": {"caught": 0, "of": 6}}, str(s.get("by_family")))
check("6 caught for the right reason", s.get("right_reason") == 6, str(s.get("right_reason")))
check("0/8 false alarms on development controls", (s.get("false_alarms"), s.get("controls")) == (0, 8))
many = [{"ScriptName": "x", "RuleName": "PSAvoidUsingCmdletAliases", "Line": n} for n in range(1, 9)]
check("the cap applies to the linter too (8 hits -> 5 findings)", len(R.pssa_review(many).findings) == 5)

print("case 7  a reading that cannot prove what it read")
import tempfile                                       # noqa: E402
with tempfile.TemporaryDirectory() as tmp:
    bare = Path(tmp) / "bare.json"
    bare.write_text("[\n\n]\n", encoding="utf-8")
    try:
        R.read_pssa(bare)
        check("a bare [] is refused", False)
    except R.ReadingError:
        check("a bare [] is refused", True)
    partial = Path(tmp) / "partial.json"
    partial.write_text(json.dumps({"files": [controls[0].path.name], "findings": []}), encoding="utf-8")
    try:
        R.pssa_arm(controls, partial)
        check("an item no reading lists is refused", False)
    except R.ReadingError:
        check("an item no reading lists is refused", True)

if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
