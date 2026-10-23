"""P02 build step 2(b): the refusal threshold keeps the promise it CAN keep.

HISTORY. ADR 0022a (step 2a) checked REFUSE_BELOW against a BAND - between the
best unanswerable and the worst answerable top score. On 2026-10-05 the band
stopped existing: near-miss questions scored up to 0.7121, and even a bread
question ("restart my sourdough starter", 0.5825) outscored the worst real
question (0.5511). A similarity score measures TOPIC, not answerability.

THE PROMISE NOW (ADR 0023, call 2b-iii - B): the threshold is a pre-filter.
  1. TOP edge, strict - no answerable question is refused.
  2. RATE - at least REFUSAL_FLOOR of the unanswerable questions are refused,
     and every one that is not is NAMED, so a known miss stays visible.

Cases 2-4 are one per way to fail. Case 6 is the positive control - a
threshold_verdict() that always returned False would pass cases 2-4 clean.
Case 5 is the vacuous-pass guard: zero negatives is not 100% refused.

NO MODEL, NO INDEX: rows are literals, so this runs in the CI home. The real
reading on the real corpus is `python src/retrieval.py` - Jose's, on the bench.

Run:  python tests/test_threshold_band.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import retrieval                                   # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def row(score: float, answerable: bool, question: str = "q") -> dict:
    return {"question": question, "answerable": answerable, "top_score": score}


# The 2026-10-05 bench reading, verbatim: the worst answerable plus one more,
# and all four out-of-domain negatives.
TODAY = [row(0.5511, True, "worst real question"), row(0.7000, True),
         row(0.3563, False, "what is the capital of France"),
         row(0.3763, False, "who won the world series last year"),
         row(0.4651, False, "what temperature do I bake chicken thighs at"),
         row(0.5825, False, "how do I restart my sourdough starter")]

print("case 1  today's reading at 0.51: the promise holds, the known miss is named")
ok, why = retrieval.threshold_verdict(TODAY, 0.51, 0.75)
check("True", ok is True, why)
check("says 3 of 4", "refused 3 of 4" in why)
check("names the sourdough miss even though it passes", "sourdough" in why and "known miss" in why)
check("the band is inverted - and that is no longer the verdict",
      max(r["top_score"] for r in TODAY if not r["answerable"]) > 0.5511)

print("case 2  TOP edge - a real question refused (the edge the harness cannot see)")
ok, why = retrieval.threshold_verdict(TODAY, 0.56, 0.75)
check("False, TOP", ok is False and "TOP edge" in why, why)

print("case 3  a tie on the top edge fails - kept, but only by a tie")
ok, why = retrieval.threshold_verdict(TODAY, 0.5511, 0.0)
check("False, TOP", ok is False and "TOP edge" in why, why)

print("case 4  RATE below the floor - and the misses are named")
ok, why = retrieval.threshold_verdict(TODAY, 0.51, 1.0)
check("floor 1.0 -> False, names sourdough", ok is False and "sourdough" in why and "below the floor" in why, why)
ok, why = retrieval.threshold_verdict(TODAY, 0.40, 0.75)
check("threshold 0.40 refuses 2 of 4 -> False", ok is False and "refused 2 of 4" in why, why)

print("case 5  no negatives is a FAIL, never a vacuous 100%")
ok, why = retrieval.threshold_verdict([row(0.60, True)], 0.51, 0.75)
check("False", ok is False and "cannot be measured" in why, why)

print("case 6  POSITIVE CONTROL - every negative refused, every real question kept")
ok, why = retrieval.threshold_verdict([row(0.60, True), row(0.30, False), row(0.40, False)], 0.51, 1.0)
check("True, no known miss", ok is True and "known miss" not in why, why)

print("case 7  the defaults are the module's committed constants")
check("REFUSE_BELOW is 0.51", retrieval.REFUSE_BELOW == 0.51, str(retrieval.REFUSE_BELOW))
check("REFUSAL_FLOOR is 0.75", retrieval.REFUSAL_FLOOR == 0.75, str(retrieval.REFUSAL_FLOOR))
check("default arguments read them", retrieval.threshold_verdict(TODAY)[0] is True)

print("case 8  the gate actually calls it, on rows (structural)")
src = (REPO_ROOT / "src" / "retrieval.py").read_text(encoding="utf-8")
gate = src[src.index("THE QUALITY GATE"):]
check("gate calls threshold_verdict(rows) and appends 'threshold'",
      "threshold_verdict(rows)" in gate and 'failures.append("threshold")' in gate)
check("gate no longer fails on separation", 'failures.append("separation")' not in gate)

if failures:
    print(f"\nFAIL: {len(failures)} check(s): {', '.join(failures)}")
    sys.exit(1)
print("\nPASS: the threshold keeps the promise it can keep - real questions kept, misses named")
