"""P04 scorer defect 1 part 2 check: calibration can hold a REAL agent sentence.

THE GAP: every calibration pair stored `candidate_facts`, and calibrate() showed
the judge render_candidate()'s tidy prose, never an agent's words. The judge
passed "there is no such service" and failed the agent's own refusal - so
calibration read 1.00 while the bench failed the case.

THE FIX (option A, Jose's call): an optional `candidate_text`, handed to the
judge VERBATIM. `candidate_facts` stays on the pair as the human's reading of
that text, so the comparator section still checks every pair.

CASE 3 IS THE POSITIVE CONTROL: a scripted judge that repeats last night's
misreading must produce EXACTLY one false fail, on the new pair, marked
`verbatim`. Case 4 is its control: the same judge reading the sentence
correctly must produce zero - so the one in case 3 comes from the new pair,
and not from something else in the set.

Expected numbers are LITERALS counted by hand, never computed by the code
under test. NO MODEL.

Run:  python tests/test_calibration_text.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import score                                       # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


SENTENCE = ("The Fax service is not installed or running on this system, "
            "as it does not appear in the list of services.")
NEW_ID = "calib-unknown-real-refusal"

cases = score.load_suite()
pairs = score.load_calibration()
by_pid = {p["id"]: p for p in pairs}

print("calibration text - P04 scorer defect 1 part 2")
print()

print("case 1  the agent's real refusal is in the set, as the human labelled it")
new = by_pid.get(NEW_ID, {})
check("the pair exists", NEW_ID in by_pid)
check("candidate_text is the sentence, verbatim", new.get("candidate_text") == SENTENCE)
check("candidate_facts is null (a refusal)", "candidate_facts" in new and new["candidate_facts"] is None)
check("label is correct", new.get("label") == "correct")
check("exactly one pair carries candidate_text",
      sum("candidate_text" in p for p in pairs) == 1)
print()

print("case 2  the judge is handed the text VERBATIM; every other pair is rendered")
seen: dict[str, str] = {}


def recording_judge(q, prose, expected, **_k):
    seen[prose] = prose
    return None


score.calibrate(cases, pairs, judge=recording_judge)
check("the sentence reached the judge unchanged", SENTENCE in seen)
check("render_candidate(None) reached the judge too (the old pair is untouched)",
      score.render_candidate(None) in seen)
check("9 prompts, 9 distinct prose strings", len(seen) == 9, f"got {len(seen)}")
print()


# The scripted judge: reads rendered prose perfectly (maps it back to the
# pair's facts, since the renderer is deterministic), and on the real sentence
# either repeats last night's misreading or reads it correctly.
RENDERED = {score.render_candidate(p["candidate_facts"]): p["candidate_facts"]
            for p in pairs if "candidate_text" not in p}
LAST_NIGHT = {"Fax service status": "not installed or running",
              "reason": "does not appear in the list of services"}


def judge_for(real_sentence_reading):
    def judge(q, prose, expected, **_k):
        if prose == SENTENCE:
            return real_sentence_reading
        return RENDERED[prose]
    return judge


print("case 3  POSITIVE CONTROL: last night's misreading = exactly one false fail")
r = score.calibrate(cases, pairs, judge=judge_for(LAST_NIGHT))
check("false_fail == 1", r["false_fail"] == 1, f"got {r['false_fail']}")
check("false_pass == 0", r["false_pass"] == 0, f"got {r['false_pass']}")
check("false_fail_rate == 0.2  (1 of 5 correct-labelled)", r["false_fail_rate"] == 0.2,
      f"got {r['false_fail_rate']}")
check("agreement == 8/9", r["agreement"] == 8 / 9, f"got {r['agreement']}")
check("the miss is the new pair", [m["id"] for m in r["misses"]] == [NEW_ID],
      str([m["id"] for m in r["misses"]]))
check("the miss is marked source=verbatim",
      [m.get("source") for m in r["misses"]] == ["verbatim"])
print()

print("case 4  CONTROL: the same judge reading the sentence correctly = zero misses")
r = score.calibrate(cases, pairs, judge=judge_for(None))
check("false_fail == 0 and false_pass == 0", (r["false_fail"], r["false_pass"]) == (0, 0),
      f"got FF {r['false_fail']} FP {r['false_pass']}")
check("agreement == 1.0", r["agreement"] == 1.0, f"got {r['agreement']}")
print()

print("case 5  an empty candidate_text is REFUSED, not measured as \"\"")
lines = score.FIXTURES.read_text(encoding="utf-8").splitlines()
for bad in ["", "   ", None, 7]:
    out = []
    for ln in lines:
        obj = json.loads(ln)
        if obj.get("id") == NEW_ID:
            obj["candidate_text"] = bad
        out.append(json.dumps(obj))
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "judge-calibration.jsonl"
        f.write_text("\n".join(out) + "\n", encoding="utf-8")
        try:
            score.load_calibration(f)
            raised = False
        except ValueError as exc:
            raised = NEW_ID in str(exc)
    check(f"candidate_text={bad!r} raises, naming the pair", raised)
print()

print(f"{'PASS' if not failures else 'FAIL'}  {len(failures)} failure(s)")
sys.exit(1 if failures else 0)
