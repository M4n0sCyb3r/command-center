"""P04 scorer defect 1 part 2b check: the stances are a PARTITION, decided in order.

THE DEFECT: the judge's three stances were listed with no precedence and they
overlapped. The agent's real refusal fit `states` (it names a value) AND
`not_found` (the value says the thing does not exist); the judge took `states`
and a correct refusal failed.

THE FIX (option A, Jose's call): decide not_found, then declines, then states,
first match wins - existence before value.

WHAT THIS FILE CAN AND CANNOT PROVE. Whether the judge OBEYS the rule is a
model measurement (`JUDGE=1 python src/score.py` + the agent bench, Jose's).
Without a model, two properties must hold and can fail:
  1. the prompt states the order, and defines every stance in STANCES
  2. NO CALIBRATION PROSE LEAKS INTO THE PROMPT. Quoting the test sentence as
     an example would teach the judge its own test, and the 1 -> 0 would prove
     nothing. Checked on 4-word runs, for every pair, verbatim AND rendered.

CASE 3 IS THE POSITIVE CONTROL for case 2: a leak detector that finds nothing
proves nothing, so it must find a planted sentence and a planted 4-word run.

Run:  python tests/test_stance_order.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import score                                       # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def shingles(text: str, n: int = 4) -> set[str]:
    words = re.findall(r"[a-z0-9']+", text.casefold())
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def leaks(prompt: str, proses: list[str]) -> set[str]:
    have = shingles(prompt)
    return {s for p in proses for s in shingles(p) if s in have}


SYSTEM = score.JUDGE_SYSTEM
PROMPT = score.JUDGE_SYSTEM + "\n" + score.JUDGE_PROMPT
pairs = score.load_calibration()
PROSE = [p["candidate_text"] if "candidate_text" in p
         else score.render_candidate(p["candidate_facts"]) for p in pairs]

print("stance order - P04 scorer defect 1 part 2b")
print()

print("case 1  the prompt states the order, and defines every stance")
pos = {s: SYSTEM.find(f"  {s} ") for s in score.STANCES}
check("every stance in STANCES has a definition line", all(v >= 0 for v in pos.values()),
      str(pos))
check("defined in the order not_found < declines < states",
      0 <= pos["not_found"] < pos["declines"] < pos["states"], str(pos))
check("the prompt says the order decides", "IN THIS ORDER" in SYSTEM and "FIRST" in SYSTEM)
check("states is limited to things treated as existing",
      "treats as existing" in SYSTEM)
print()

print("case 2  no calibration prose leaks into the prompt (4-word runs)")
check(f"{len(PROSE)} calibration proses checked", len(PROSE) == 9, f"got {len(PROSE)}")
found = leaks(PROMPT, PROSE)
check("no 4-word run of any calibration prose appears in the prompt", not found,
      str(sorted(found)))
print()

print("case 3  POSITIVE CONTROL: the detector finds a planted leak")
real = next(p["candidate_text"] for p in pairs if "candidate_text" in p)
check("planted whole sentence is found", bool(leaks(PROMPT + "\n" + real, PROSE)))
four = " ".join(real.split()[3:7])
check(f"planted 4-word run {four!r} is found", bool(leaks(PROMPT + " " + four, PROSE)))
three = " ".join(real.split()[3:6])
check(f"a 3-word run {three!r} alone is NOT flagged (the threshold is 4)",
      not leaks(PROMPT + " " + three, PROSE))
print()

print(f"{'PASS' if not failures else 'FAIL'}  {len(failures)} failure(s)")
sys.exit(1 if failures else 0)
