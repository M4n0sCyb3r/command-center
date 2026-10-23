"""Build step 3 check: the retrieval refactor changed nothing it measures.

WHAT THIS PROVES, AND WHY IT IS A MEASUREMENT AND NOT AN ASSERTION
------------------------------------------------------------------
Build step 3 pulled the per-case body out of retrieval.evaluate() into
score_one(), and the aggregates into derive(). The claim that needs proving is
"this was a refactor" - that the numbers evaluate() reports are the same
numbers it reported before a line moved.

The figures in BEFORE were captured by RUNNING the pre-refactor evaluate() on
2026-09-21 and pasting its output. They are literals. Nothing in this file
computes them, so they are an independent claim the refactored code has to
meet - the same device as build step 2's hand count.

THE RANK DECOMPOSITION IS A STRONGER CLAIM THAN THE AGGREGATES
--------------------------------------------------------------
evaluate() never reported per-case ranks, so case 3's expected ranks were not
captured from anything - they were DERIVED BY HAND from the pinned MRR:

    mrr 0.9583333333333334 x 12 answerable cases = 11.5 reciprocal ranks
    recall_at_k 1.0 means all 12 were found, so every rank is 1, 2 or 3
    solving  a + b + c = 12  and  a + b/2 + c/3 = 11.5  gives exactly
    a = 11, b = 1, c = 0 - and that solution is UNIQUE

So: eleven cases at rank 1, exactly one at rank 2, none at rank 3, no misses.
This matters because recall_at_k = 1.0 would still be reported if the rank-2
case had silently moved to rank 1, or if score_one lost rank information
entirely and reported every hit as rank 1. The aggregate cannot see that.
The decomposition can.

REQUIRES OLLAMA. search() embeds each question, so this check cannot run
without the embedding model up - which is also true of the retrieval suite
itself, and is worth remembering at build step 8: CI cannot run this suite for
the same class of reason it cannot reach the lab VMs.

Run:  python tests/test_retrieval_seam.py
"""

from __future__ import annotations

BENCH_ONLY = "embeds each question through Ollama and reads data/index.json (gitignored)"

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import retrieval as r  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# RE-CAPTURED 2026-10-05 (P02 build step 2b, ADR 0023). The 2026-09-21 figures
# described a corpus (15 chunks) and a suite (14 cases) that no longer exist.
# These came from running BOTH the pre-step-2b evaluate() (git show 1268b54)
# and the current one on the same index and suite: the two dicts were
# IDENTICAL, which is what proves score_suite()'s extraction a refactor.
# Corpus 44 chunks / 9 files, nomic-embed-text, suite 16 cases. Literals - do
# not recompute; if the corpus, suite or model changes, re-capture deliberately.
# RE-CAPTURED 2026-10-17 (P02 step 8): suite 16 -> 20 (one question per SOP
# failure-mode table), index as rebuilt by upgrade_check on Ollama 0.40.4
# (data/index.json sha256 e6930b75...). Source: Jose's harness run record
# evals/runs/20261018T015139Z-retrieval.json, per-case ranks read from it and
# the aggregates computed by hand - NOT from evaluate(), which this file checks.
# RE-CAPTURED 2026-10-21 (P06, ADR 0046): Ollama 0.40.8 accepted. Index rebuilt
# by upgrade_check on 0.40.8 (data/index.json sha256 5d5ef035...). Source:
# evals/runs/20261022T044638Z-retrieval.json. One case moved: sop-jea-failure
# rank 3 -> MISS (rank 4, a near-tie flipped by 0.40.7's llama.cpp update).
# Separation top scores unchanged to the last digit.
BEFORE = {
    "k": 3,
    "answerable_cases": 16,
    "recall_at_k": 13 / 16,             # 0.8125: restart-procedure, sop-jea-failure, sop-closeout-failure missed
    "mrr": (12 + 1 / 2) / 16,           # 25/32 = 0.78125
    "worst_answerable_top_score": 0.5511134711295764,
    "best_unanswerable_top_score": 0.5824582646482497,
    "separated": False,
}

# Derived by hand from BEFORE (2026-10-21): mrr x 16 = 12 1/2 reciprocal ranks;
# recall 13/16 means 13 found, 3 missed; a+b+c = 13 and a+b/2+c/3 = 12 1/2 give
# 3b + 4c = 3, whose only non-negative integer solution is b = 1, c = 0, a = 12.
# Rank 2: sop-bench-failure. Misses: restart-procedure (ADR 0022c),
# sop-closeout-failure (P02 step 8) and sop-jea-failure (ADR 0046) - known losses.
HAND_RANKS = {1: 12, 2: 1, 3: 0, 0: 3}      # rank -> count, 0 means MISS
HAND_UNANSWERABLE = 4                        # 20 cases total, 16 answerable

print("retrieval seam - build step 3: the refactor changed nothing")
print()

index = r.load_index()
cases = r.load_suite()

# --- Case 1: evaluate() still returns the pinned numbers. ------------------
print("case 1  evaluate() reproduces the pre-refactor figures exactly")
after = r.evaluate(index=index, verbose=False)
for key, expected in BEFORE.items():
    got = after[key]
    if isinstance(expected, float):
        delta = abs(got - expected)
        check(f"{key} == {expected!r}", delta <= 1e-12, f"got {got!r}  delta {delta:.3e}")
    else:
        check(f"{key} == {expected!r}", got == expected, f"got {got!r}")
check("no key appeared or vanished", set(after) == set(BEFORE), str(sorted(set(after) ^ set(BEFORE))))
print()

# --- Case 2: the composition identity. -------------------------------------
print("case 2  derive(score_one per case) == evaluate() - the seam composes")
rows = [r.score_one(c, index=index) for c in cases]
composed = r.derive(rows, k=3)
check("score_suite() returns the same rows as score_one per case",
      r.score_suite(index=index) == rows)
check("the two dicts are identical", composed == after,
      "" if composed == after else f"{composed} != {after}")
print()

# --- Case 3: the hand-derived rank decomposition. --------------------------
print("case 3  per-case ranks match the decomposition derived by hand from MRR")
answerable = [row for row in rows if row["answerable"]]
counts = {rank: sum(1 for row in answerable if row["rank"] == rank) for rank in (1, 2, 3, 0)}
for rank, expected in HAND_RANKS.items():
    label = "MISS" if rank == 0 else f"rank {rank}"
    check(f"{label}: {expected} case(s)", counts[rank] == expected, f"got {counts[rank]}")
check(f"{HAND_UNANSWERABLE} unanswerable cases",
      len(rows) - len(answerable) == HAND_UNANSWERABLE,
      f"got {len(rows) - len(answerable)}")
print("  recall_at_k alone cannot say WHERE the hits landed, or which case")
print("  missed. The hand-derived decomposition can.")
print()

# --- Case 4: what `passed` means for an unanswerable case. -----------------
print("case 4  an unanswerable case passes by being REFUSED, not by default")
negatives = [row for row in rows if not row["answerable"]]
# ADR 0023: the threshold is a pre-filter; 3 of 4 refused, sourdough a known miss.
check("passed == (top_score < REFUSE_BELOW) on every unanswerable row",
      all(row["passed"] == (row["top_score"] < r.REFUSE_BELOW) for row in negatives),
      f"REFUSE_BELOW={r.REFUSE_BELOW}, tops={[round(n['top_score'], 4) for n in negatives]}")
missed = [row["question"] for row in negatives if not row["passed"]]
check("exactly one negative is answered, and it is the sourdough known miss",
      len(missed) == 1 and "sourdough" in missed[0], str(missed))
check("every answerable case passes by rank > 0",
      all(row["passed"] == (row["rank"] > 0) for row in answerable))
print("  passed=True by default would have been VACUOUS - it would inflate every")
print("  pass rate by the number of negatives while looking like a measurement.")
print()

# --- Case 5: the detail payload has the keys the record will read. ---------
print("case 5  every row carries the detail keys a run record will store")
required = {"question", "answerable", "expect", "rank", "top_score", "hit_ids", "passed"}
missing = [i for i, row in enumerate(rows) if not required <= set(row)]
check("all rows carry every required key", missing == [], f"rows missing keys: {missing}")
print("  This is the price of option A's untyped detail bag, paid here: a")
print("  mistyped key fails a check instead of producing an unreadable row.")
print()

# --- Case 6: POSITIVE CONTROL - derive() actually reads the rows. ----------
print("case 6  positive control - a changed row changes the derived numbers")
mutated = [dict(row) for row in rows]
# Re-aimed 2026-10-05: flips a rank-1 case to a miss. Expected values counted
# by hand (re-counted 2026-10-21 for 16 answerable: 12 found; 11 + 1/2).
target = next(row for row in mutated if row["answerable"] and row["rank"] == 1)
target["rank"] = 0          # a rank-1 case now MISSES
moved = r.derive(mutated, k=3)
check("recall_at_k moved 13/16 -> 12/16", abs(moved["recall_at_k"] - 12 / 16) <= 1e-12,
      f"got {moved['recall_at_k']!r}")
check("mrr moved 25/32 -> 23/32", abs(moved["mrr"] - 23 / 32) <= 1e-12,
      f"got {moved['mrr']!r}")
check("answerable_cases did NOT move", moved["answerable_cases"] == 16)
print("  Without this, a derive() returning BEFORE's numbers as constants would")
print("  pass cases 1 and 2 with a clean sheet - step 2's sabotage, one layer up.")
print()

if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
