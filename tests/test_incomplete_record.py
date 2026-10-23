"""Agent-loop stall, call B, build step 1: a record that can say "I stopped early".

THE PROPERTY
------------
A run whose model call stalled on case N has rows for cases 1..N-1 and none for
N onwards. Before record v3 nothing in the file said so: the rows it had would
summarize into a verdict, and seven passing rows from a run that stalled on
case 8 would exit 0. The one unmeasured case could be the regression.

So v3 adds a header field, `incomplete`, and this file checks four things:

  case 1  a marker survives write -> read -> summarize
  case 2  THE POINT: an incomplete record is exit 2 even when every row passed
  case 3  POSITIVE CONTROL for case 2: the same rows with no marker are exit 0,
          and with a failing row exit 1. A verdict() that returned 2 for
          everything would pass case 2 clean - this is what catches it.
  case 4  a COMPLETE record says so: `incomplete` is present and null in the
          file, and a header with the key absent is refused. Optional would
          have meant "absent reads as finished", which is exactly what a writer
          that never heard of stalls produces.
  case 5  malformed markers are refused - at construction and on read
  case 6  a marker naming a case that also has a row is refused (contradiction)
  case 7  a v2 record is refused, not misread as complete

No model. Run:  python tests/test_incomplete_record.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import run_record as rr  # noqa: E402
from eval_harness import (EXIT_PASS, EXIT_REGRESSION,  # noqa: E402
                          EXIT_TOOLING_ERROR, verdict)

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


STALL = {"case_id": "c3", "error": "the model did not answer within 120s", "turn": 4}


def header(incomplete=None) -> rr.Header:
    return rr.Header(
        suite_path="evals/suites/agent.jsonl", suite_sha256="0" * 64, suite_header_sha256="a" * 64,
        tier_selection="fixed", scorer="agent", model_name="qwen3:8b",
        model_digest="sha256:deadbeef", host_target="fixture", sampling=None, rebaseline=None,
        started_at="2026-09-26T18:00:00Z", incomplete=incomplete,
    )


def rows(*passed: bool) -> list[rr.CaseRow]:
    return [rr.CaseRow(case_sha256="c" * 64, id=f"c{i}", tier="fixed", result="pass" if p else "fail", detail={})
            for i, p in enumerate(passed, start=1)]


def roundtrip(h: rr.Header, rs: list[rr.CaseRow]) -> dict:
    with tempfile.TemporaryDirectory() as d:
        return rr.read(rr.write(d, h, rs))


def raw(doc: dict) -> Exception | None:
    """read() a hand-built document; return the exception, or None if accepted."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "r.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        try:
            rr.read(p)
        except ValueError as exc:
            return exc
    return None


def doc(incomplete="__absent__", version=None, ids=("c1", "c2")) -> dict:
    h = {"suite_path": "evals/suites/agent.jsonl", "suite_sha256": "0" * 64, "suite_header_sha256": "a" * 64,
         "tier_selection": "fixed", "scorer": "agent", "model_name": "qwen3:8b",
         "model_digest": "sha256:deadbeef", "host_target": "fixture", "sampling": None, "rebaseline": None,
         "started_at": "2026-09-26T18:00:00Z"}
    if incomplete != "__absent__":
        h["incomplete"] = incomplete
    return {"record_version": rr.RECORD_VERSION if version is None else version,
            "header": h,
            "cases": [{"id": i, "tier": "fixed", "case_sha256": "c" * 64, "result": "pass", "detail": {}} for i in ids]}


print("incomplete record - agent-loop stall, call B, build step 1\n")

print("case 1  a stall marker survives write -> read -> summarize")
stored = roundtrip(header(STALL), rows(True, True))
check("record_version is 7", stored["record_version"] == 7)
check("the marker is in the file, unchanged", stored["header"]["incomplete"] == STALL,
      repr(stored["header"]["incomplete"]))
summary = rr.summarize(stored)
check("summarize() carries it", summary["incomplete"] == STALL)
check("the rows it has are still counted (2 of 2 present rows passed)",
      summary["gating"] == 2 and summary["gating_passed"] == 2)

print("\ncase 2  THE POINT - incomplete is exit 2 even when every row passed")
check("2 passing rows + a stall -> exit 2", verdict(summary) == EXIT_TOOLING_ERROR,
      f"got {verdict(summary)}")
mixed = rr.summarize(roundtrip(header(STALL), rows(True, False)))
check("a failing row + a stall -> still exit 2, not 1 (no verdict, not a bad one)",
      verdict(mixed) == EXIT_TOOLING_ERROR, f"got {verdict(mixed)}")

print("\ncase 3  positive control - the same rows with no marker DO yield a verdict")
print("  A verdict() returning 2 for everything passes case 2. This catches it.")
clean = rr.summarize(roundtrip(header(None), rows(True, True)))
check("2 passing rows, finished -> exit 0", verdict(clean) == EXIT_PASS, f"got {verdict(clean)}")
bad = rr.summarize(roundtrip(header(None), rows(True, False)))
check("a failing row, finished -> exit 1", verdict(bad) == EXIT_REGRESSION, f"got {verdict(bad)}")

print("\ncase 4  a complete record SAYS it finished")
with tempfile.TemporaryDirectory() as d:
    text = rr.write(d, header(None), rows(True)).read_text(encoding="utf-8")
on_disk = json.loads(text)["header"]
check("the key is written, and it is null", "incomplete" in on_disk and on_disk["incomplete"] is None)
exc = raw(doc())                                    # key absent
check("a v3 header WITHOUT the key is refused", exc is not None and "incomplete" in str(exc), str(exc))
check("the same header with the key null is accepted", raw(doc(incomplete=None)) is None)

print("\ncase 5  malformed markers are refused - at construction and on read")
BAD = [
    ("a string", "stalled"),
    ("an empty object", {}),
    ("missing turn", {"case_id": "c3", "error": "x"}),
    ("an extra key", {**STALL, "retried": True}),
    ("empty case_id", {**STALL, "case_id": ""}),
    ("empty error", {**STALL, "error": ""}),
    ("turn 0", {**STALL, "turn": 0}),
    ("turn as a string", {**STALL, "turn": "4"}),
    ("turn True (bool is an int in Python)", {**STALL, "turn": True}),
]
for label, marker in BAD:
    try:
        header(marker)
        built = False
    except ValueError:
        built = True
    exc = raw(doc(incomplete=marker))
    check(f"{label}: Header() refuses AND read() refuses", built and exc is not None)
check("control: turn null is accepted (a raiser that is not the agent loop)",
      raw(doc(incomplete={**STALL, "turn": None})) is None)
check("control: a well-formed marker is accepted", raw(doc(incomplete=STALL)) is None)

print("\ncase 6  a marker naming a case that also has a row is a contradiction")
exc = raw(doc(incomplete={**STALL, "case_id": "c2"}, ids=("c1", "c2")))
check("refused, and the message names the case", exc is not None and "c2" in str(exc), str(exc))

print("\ncase 7  a v2 record is refused, not read as complete")
exc = raw(doc(incomplete=None, version=2))
check("record_version 2 is refused", exc is not None and "record_version" in str(exc), str(exc))

print()
if failures:
    print(f"FAIL: {len(failures)} check(s) failed")
    sys.exit(1)
print("PASS: a record can say it stopped early, and one that does never yields a verdict")
