"""P03 build step 7: the `review` suite enters the harness - its scorer, proven with a SCRIPTED pipeline.

  case 1  the suite: 32 development cases, every held-out id absent, each text_sha256 matching its file
  case 2  the pass rule: bugged passes only when STOPPED (needs-human); approved bugged is a fail
          (shipped broken); clean passes only approved AND byte-unchanged; an error is unscored
  case 3  refusals: a held-out id, and a script whose text no longer matches the suite, raise
          ScorerError - never a score

Run:  python tests/test_review_scorer.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import hashlib                                        # noqa: E402
import review as R                                    # noqa: E402
import scorers                                        # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


lines = (REPO_ROOT / "evals" / "suites" / "review.jsonl").read_text(encoding="utf-8").splitlines()
header, cases = json.loads(lines[0]), [json.loads(x) for x in lines[1:]]
items = {i.id: i for i in R.load_set()}

print("case 1  the suite")
check("header names the review scorer", header["suite"] == "review" and header["scorer"] == "review")
check("32 cases, all development, none held-out",
      len(cases) == 32 and all(items[c["id"]].split == "development" for c in cases)
      and not {i for i, it in items.items() if it.split == "held_out"} & {c["id"] for c in cases})
check("every text_sha256 matches the committed script",
      all(hashlib.sha256(items[c["id"]].text().encode()).hexdigest() == c["text_sha256"] for c in cases))

print("case 2  the pass rule")
case_of = {c["id"]: c for c in cases}
def run(terminal, changed=False, error=""):
    def run_item(it):
        text = it.text() + ("# edited\n" if changed else "")
        return ({"terminal": terminal, "stop": "x", "rounds": 0, "calls": 1, "trace": [], "error": error}, text)
    return run_item
expect = [("01-G", "needs-human", False, "pass"), ("01-G", "approved", False, "fail"),
          ("01-G", "approved-after-escalation", True, "fail"), ("01-C", "approved", False, "pass"),
          ("01-C", "approved", True, "fail"), ("01-C", "needs-human", False, "fail"),
          ("01-G", "error", False, "unscored"), ("01-C", "error", False, "unscored")]
for cid, terminal, changed, want in expect:
    score, cond = scorers.make_review_scorer(run_item=run(terminal, changed, "boom"))
    row = score(case_of[cid])
    check(f"{cid} {terminal}{' (rewritten)' if changed else ''} -> {want}", row.result == want, row.result)
check("an unscored row carries the error as its note", "boom" in row.note)
check("scripted conditions never pose as a measurement", cond.model_name == "scripted" and cond.sampling is None)

print("case 3  refusals")
score, _ = scorers.make_review_scorer(run_item=run("needs-human"))
held = next(i for i, it in items.items() if it.split == "held_out")
for label, case in [("a held-out id", {"id": held, "tier": "fixed", "kind": "defect", "text_sha256": "x"}),
                    ("a stale text_sha256", {**case_of["01-G"], "text_sha256": "0" * 64})]:
    try:
        score(case); check(f"refused: {label}", False)
    except scorers.ScorerError:
        check(f"refused: {label}", True)

if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
