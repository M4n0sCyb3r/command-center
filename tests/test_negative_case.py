"""P04 scorer defect 1 check: a correct refusal passes, a fabrication still fails.

THE DEFECT (found by P04's first bench run, build step 3 part 2c): on
`negative-unknown-service` the extraction form had ZERO fields, so the judge
returned `{}` for a correct refusal and compare_facts(None, {}) failed it - but
`{}` was also the ONLY thing a fabrication could produce, so "treat {} as a
refusal" alone would have opened a false-pass path.

THE FIX (option B, Jose's call): an OPEN form for negative cases, plus `{}`
counts as "asserted nothing". The two are one fix.

CASE 1 IS THE POSITIVE CONTROL and the reason this file exists: a
compare_facts() that returned True for every negative case would pass case 2
clean. So a fabrication must still FAIL, through the comparator AND through
score_case.

NO MODEL. The judge is scripted (Lab 10's judge= seam); extract_facts() is
driven with ask_json stubbed, so the stance-to-facts mapping is checked without
Ollama. The judge's REAL behaviour on the open form is measured by
`JUDGE=1 python src/score.py` (calibration) and the bench run - Jose's.

Run:  python tests/test_negative_case.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import score                                       # noqa: E402
from agent import AgentResult, Step                # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


by_id = {c["id"]: c for c in score.load_suite()}
NEG = by_id["negative-unknown-service"]
POS = by_id["aggregate-down"]

print("negative case - P04 scorer defect 1: refusal passes, fabrication fails")
print()

print("case 1  POSITIVE CONTROL: a fabrication still fails")
check("compare_facts(None, {'Fax': 'running'}) is False",
      score.compare_facts(None, {"Fax": "running"}) is False)
check("compare_facts(None, {'Fax': 'stopped'}) is False",
      score.compare_facts(None, {"Fax": "stopped"}) is False)
print()

print("case 2  a correct refusal passes, in both shapes the judge can return")
check("compare_facts(None, None) is True  (stance declines / not_found)",
      score.compare_facts(None, None) is True)
check("compare_facts(None, {}) is True  (stance states, asserted nothing)",
      score.compare_facts(None, {}) is True)
print()

print("case 3  positive cases are untouched")
exp = POS["expected_facts"]
check("positive + None still fails", score.compare_facts(exp, None) is False)
check("positive + {} still fails", score.compare_facts(exp, {}) is False)
check("positive + exact still passes", score.compare_facts(exp, exp) is True)
print()

print("case 4  the form: open for negative, closed for positive")
neg_facts = score.facts_schema(None)["properties"]["facts"]
pos_facts = score.facts_schema(exp)["properties"]["facts"]
check("negative form accepts any key (additionalProperties is a schema)",
      neg_facts.get("additionalProperties") == {"type": "string"}, str(neg_facts))
check("positive form stays closed (additionalProperties False)",
      pos_facts.get("additionalProperties") is False, str(pos_facts))
check("positive form still names exactly the case's keys",
      set(pos_facts["properties"]) == set(exp), str(sorted(pos_facts["properties"])))
print()

print("case 5  through score_case, with the refusal and the fabrication")
refused = AgentResult(ok=True, answer="The Fax service is not installed on this system.",
                      reason="final",
                      steps=[Step(n=1, action="call", tool="get_service_status",
                                  arguments={"name": "Fax"}, ok=True),
                             Step(n=2, action="final")])
for label, judged, want in (("judge returns {} (the bench's real output)", {}, True),
                            ("judge returns None", None, True),
                            ("judge records a fabrication", {"Fax": "running"}, False)):
    sc = score.score_case(NEG, refused, judge=lambda *a, _j=judged, **k: _j)
    check(f"{label} -> outcome {want}", sc.outcome is want, f"outcome={sc.outcome}")
print()

print("case 6  extract_facts(): stance maps to facts as documented")
real = score.ask_json
try:
    for data, want in (({"stance": "states", "facts": {}}, {}),
                       ({"stance": "states"}, {}),
                       ({"stance": "not_found"}, None),
                       ({"stance": "states", "facts": {"Fax": "running"}}, {"Fax": "running"})):
        score.ask_json = lambda *a, _d=data, **k: _d
        got = score.extract_facts(NEG["question"], "x", None)
        check(f"{data} -> {want}", got == want, str(got))
finally:
    score.ask_json = real
print()

print("FAILED: " + ", ".join(failures) if failures else "all checks ok")
sys.exit(1 if failures else 0)
