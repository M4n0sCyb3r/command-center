"""The bench pin: an Ollama update is announced, and the pin moves only on proof.

Ollama on Windows updates itself (0.33.3 -> 0.34.4 -> 0.40.1 in five weeks), and
each update was found by someone happening to read a version line. bench_check
check C fails on a mismatch; tools/upgrade_check.py re-measures and moves the pin
only when nothing moved. This file proves the decisions, with no server:

  case 1  pin_verdict: match passes; mismatch and missing pin both fail, by name
  case 2  load_pin: missing, malformed, empty and wrong-typed files all read as None
  case 3  compare: identical cases -> [] (POSITIVE CONTROL - a compare() that
          always reported a move would pass cases 4-5 clean)
  case 4  compare: each way a case can move is reported
  case 5  compare: a case missing or new is a move, never silently skipped
  case 6  the probe's count and the harness's record line are read correctly
  case 7  write_pin round-trips through load_pin
  case 8  the committed pin is readable (bench_check would fail on a bad one)

Run:  python tests/test_bench_pin.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import bench_check                                 # noqa: E402
import upgrade_check                               # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def case(cid: str, result: str = "pass", rank: int = 1, score: float = 0.6) -> dict:
    return {"id": cid, "tier": "fixed", "result": result,
            "detail": {"rank": rank, "top_score": score, "hit_ids": ["a", "b", "c"]}}


print("case 1  pin_verdict")
check("match -> None", bench_check.pin_verdict("0.40.1", "0.40.1") is None)
why = bench_check.pin_verdict("0.40.1", "0.41.0") or ""
check("mismatch names both versions", "0.40.1" in why and "0.41.0" in why, why)
why = bench_check.pin_verdict(None, "0.40.1") or ""
check("missing pin fails, names the file", "bench-pin.json" in why, why)

print("case 2  load_pin reads anything wrong as None")
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    check("missing file", bench_check.load_pin(d / "nope.json") is None)
    for name, text in [("malformed", "{not json"), ("no key", '{"x": 1}'),
                       ("empty", '{"ollama_version": ""}'), ("number", '{"ollama_version": 40}'),
                       ("list", "[1]")]:
        (d / "p.json").write_text(text, encoding="utf-8")
        check(name, bench_check.load_pin(d / "p.json") is None)

print("case 3  POSITIVE CONTROL - identical runs compare clean")
base = [case("a"), case("b", "fail", 0, 0.58), case("c", rank=2, score=0.7)]
same = [dict(c) for c in base]
same[0] = {**base[0], "detail": {**base[0]["detail"], "hit_ids": ["a", "z", "y"]}}
check("[] (rank 2-3 reshuffle ignored)", upgrade_check.compare(base, same) == [],
      str(upgrade_check.compare(base, same)))
tiny = [case("a", score=0.6 + 1e-9), case("b", "fail", 0, 0.58), case("c", rank=2, score=0.7)]
check("score inside tolerance -> []", upgrade_check.compare(base, tiny) == [])

print("case 4  each kind of move is reported")
for label, run in [("result", [case("a", "fail"), base[1], base[2]]),
                   ("rank", [case("a", rank=2), base[1], base[2]]),
                   ("top_score", [case("a", score=0.61), base[1], base[2]])]:
    got = upgrade_check.compare(base, run)
    check(label, len(got) == 1 and label in got[0], str(got))
broken = [{**base[0], "detail": {"rank": 1, "top_score": None}}, base[1], base[2]]
check("unreadable score is a move", any("not comparable" in m for m in upgrade_check.compare(base, broken)))

print("case 5  missing and new cases are moves")
got = upgrade_check.compare(base, base[:2])
check("missing", any("c: missing" in m for m in got), str(got))
got = upgrade_check.compare(base, base + [case("d")])
check("new", any("d: new" in m for m in got), str(got))

print("case 6  reading the other tools' output")
check("probe count", upgrade_check.truncated_count("108 chunks probed; 0 would be truncated; x") == 0)
check("probe count 4", upgrade_check.truncated_count("91 chunks probed; 4 would be truncated") == 4)
check("probe garbage -> None", upgrade_check.truncated_count("Traceback ...") is None)
rec = upgrade_check.record_path("  DRIFT ...\n  record     evals/runs/20261009T034347Z-retrieval.json\n")
check("record line", rec == REPO_ROOT / "evals/runs/20261009T034347Z-retrieval.json", str(rec))
check("no record line -> None", upgrade_check.record_path("BASELINE x\n") is None)

print("case 7  write_pin round-trips")
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "bench-pin.json"
    upgrade_check.write_pin(p, "0.41.0", "test")
    check("load_pin reads what write_pin wrote", bench_check.load_pin(p) == "0.41.0")

print("case 8  the committed pin is readable")
check("bench-pin.json", bench_check.load_pin() is not None, str(bench_check.load_pin()))
_pin = json.loads((REPO_ROOT / "bench-pin.json").read_text(encoding="utf-8"))
check("the pin says what it covers - retrieval only, generation not re-verified (ADR 0026 amendment 12)",
      "retrieval" in _pin.get("covers", "") and "NOT" in _pin.get("covers", ""), _pin.get("covers", "MISSING"))

if failures:
    print(f"\nFAIL: {len(failures)} check(s): {', '.join(failures)}")
    sys.exit(1)
print("\nPASS: an Ollama update is announced, and the pin moves only on proof")
