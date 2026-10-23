"""Build step 2 check, amended at build step 3: records, rows, and totals.

WHY THE EXPECTED NUMBERS ARE WRITTEN OUT BY HAND
------------------------------------------------
The build step words the check as "a written record reloads and the totals
match a hand count". The words "hand count" are doing real work. If this file
computed the expected numbers with the same summarize() it is checking, the
check would establish only that the code agrees with itself - which it would
also do if every number were wrong in the same direction.

So the fixture below is six rows chosen to make each total distinct, and the
expected values are LITERALS counted off the fixture by eye.

WHY THERE IS A POSITIVE CONTROL (case 3)
-----------------------------------------
A summarize() that ignored the rows and returned the fixture's numbers as
constants would pass case 1 perfectly. Case 3 flips one row and requires the
totals to MOVE. This was not hypothetical: at build step 2 that exact sabotage
was run against a scratch copy and case 1 passed it with a clean sheet.

AMENDED AT BUILD STEP 3
-----------------------
RECORD_VERSION is 2. The row is now a SPINE (id, tier, passed) plus a `detail`
payload owned by whichever scorer produced it, because the agent scorer's
vocabulary did not fit the retrieval scorer. Case 7 is new and is the point of
the change: one record holding rows from BOTH scorers, summarized correctly
without summarize() knowing either of them exists.

Run:  python tests/test_run_record.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import run_record as rr  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def header(tier_selection: str = "all", suite: str = "evals/suites/agent.jsonl") -> rr.Header:
    return rr.Header(
        suite_path=suite,
        suite_sha256="0" * 64, suite_header_sha256="a" * 64,
        tier_selection=tier_selection,
        scorer="score.score_case",
        model_name="qwen3:8b",
        model_digest="sha256:deadbeef",
        host_target="fake-host",
        sampling=None, rebaseline=None,
        started_at="2026-09-21T18:00:00Z",
    )


# --- The fixture. Count these by eye; the literals below come from here. ----
#
#   fixed  f-pass-1       passed        live   l-pass    passed
#   fixed  f-pass-2       passed        live   l-fail    failed
#   fixed  f-fail-traj    failed
#   fixed  f-unscored     UNSCORED - the judge crashed on a case whose
#                         trajectory passed, so the verdict needed it (v4).
#                         Carries the only note. Until v4 this row was
#                         `passed=False` and counted as a failure.
#
ROWS = [
    rr.CaseRow(case_sha256="c" * 64, id="f-pass-1", tier="fixed", result="pass",
               detail={"outcome": True, "trajectory": True,
                       "answer": "Spooler is stopped."}),
    rr.CaseRow(case_sha256="c" * 64, id="f-pass-2", tier="fixed", result="pass",
               detail={"outcome": True, "trajectory": True,
                       "answer": "W32Time is running."}),
    rr.CaseRow(case_sha256="c" * 64, id="f-fail-traj", tier="fixed", result="fail",
               detail={"outcome": True, "trajectory": False,
                       "answer": "Spooler is stopped.",
                       "missing": [{"tool": "get_service_status"}]}),
    rr.CaseRow(case_sha256="c" * 64, id="f-unscored", tier="fixed", result="unscored",
               detail={"outcome": False, "trajectory": True,
                       "answer": "I could not determine that."},
               note="judge error: TimeoutError: read timed out"),
    rr.CaseRow(case_sha256="c" * 64, id="l-pass", tier="live", result="pass",
               detail={"outcome": True, "trajectory": True,
                       "answer": "34 scopes reported, all columns present."}),
    rr.CaseRow(case_sha256="c" * 64, id="l-fail", tier="live", result="fail",
               detail={"outcome": False, "trajectory": True,
                       "answer": "System.Management.Automation.RemoteException"}),
]

# Counted off the fixture by hand. NOT computed. If you change ROWS, recount.
HAND = {
    "case_count": 6,
    "by_tier": {"fixed": 4, "live": 2},
    "gating": 4,
    "gating_passed": 2,
    "gating_failed": 1,
    "gating_unscored": 1,
    # None, not 2/4 or 2/3: see summarize(). An unscored gating case means
    # there is no honest rate to print.
    "pass_rate": None,
    "reported": 2,
    "reported_passed": 1,
    "reported_failed": 1,
    "reported_unscored": 0,
    "rows_with_notes": 1,
}

TOTAL_NAMES = ("case_count", "by_tier", "gating", "gating_passed", "gating_failed",
               "gating_unscored", "pass_rate", "reported", "reported_passed",
               "reported_failed", "reported_unscored", "rows_with_notes")

print("run_record - build step 2 (amended at step 3): the record writer")
print()

# --- Case 1: write, reload, derive, compare against the hand count. --------
print("case 1  a written record reloads and its totals match the hand count")
with tempfile.TemporaryDirectory() as tmp:
    path = rr.write(tmp, header(), ROWS)
    check("the file exists on disk", path.exists(), str(path.name))
    check("filename names the suite and sorts by time",
          path.name == "20260921T180000Z-agent.json", path.name)

    reloaded = rr.read(path)
    check("record_version is 7", reloaded["record_version"] == 7)
    totals = rr.summarize(reloaded)
    for key, expected in HAND.items():
        check(f"{key} == {expected!r}", totals[key] == expected, f"got {totals[key]!r}")
print()

# --- Case 2: the ruling. No total is anywhere in the file. -----------------
#
# Step 2 asserted this by grepping the raw file text. That worked while a row
# had a fixed vocabulary; with a free-form `detail` bag a scorer could legally
# write a key called "rank" or "pass_rate" and the grep would fail on a record
# that is perfectly correct. So the assertion is now STRUCTURAL: no total may
# appear as a top-level key of the record or of any row. What a scorer puts in
# its own detail is its business.
print("case 2  no total is STORED - every one of them is derived on read")
with tempfile.TemporaryDirectory() as tmp:
    doc = json.loads(rr.write(tmp, header(), ROWS).read_text(encoding="utf-8"))
    check("record's top-level keys are exactly record_version/header/cases",
          set(doc) == {"record_version", "header", "cases"}, str(sorted(doc)))
    for name in TOTAL_NAMES:
        check(f"{name!r} is not a record key", name not in doc)
        check(f"{name!r} is not a row key",
              all(name not in row for row in doc["cases"]))
print()

# --- Case 3: POSITIVE CONTROL. Move a row, the totals must move. -----------
print("case 3  positive control - a changed row changes the totals")
mutated = list(ROWS)
mutated[3] = rr.CaseRow(case_sha256="c" * 64, id="f-unscored", tier="fixed", result="pass", detail={})
after = rr.summarize(rr.build(header(), mutated))
check("gating_unscored moved 1 -> 0", after["gating_unscored"] == 0, f"got {after['gating_unscored']}")
check("gating_passed moved 2 -> 3", after["gating_passed"] == 3, f"got {after['gating_passed']}")
check("gating_failed did NOT move (still 1)", after["gating_failed"] == 1, f"got {after['gating_failed']}")
check("pass_rate moved None -> 0.75", after["pass_rate"] == 0.75, f"got {after['pass_rate']}")
mutated[0] = rr.CaseRow(case_sha256="c" * 64, id="f-pass-1", tier="fixed", result="fail", detail={})
after = rr.summarize(rr.build(header(), mutated))
check("then pass -> fail: pass_rate 0.75 -> 0.5", after["pass_rate"] == 0.5, f"got {after['pass_rate']}")
check("cases did NOT move (still 6)", after["case_count"] == 6, f"got {after['case_count']}")
print("  At step 2 this exact sabotage was run for real: a summarize() returning")
print("  the fixture's numbers as constants passed case 1 with a clean sheet.")
print()

# --- Case 4: the property the tier SELECTION buys. -------------------------
print("case 4  a drained tier is distinguishable from a tier nobody asked for")
fixed_only = [r for r in ROWS if r.tier == "fixed"]
asked = rr.summarize(rr.build(header("all"), fixed_only))
not_asked = rr.summarize(rr.build(header("fixed"), fixed_only))
check("both report zero live cases",
      asked["by_tier"]["live"] == 0 and not_asked["by_tier"]["live"] == 0)
check("but the selections differ - 'all' vs 'fixed'",
      asked["tier_selection"] == "all" and not_asked["tier_selection"] == "fixed")
print("  Same rows, same counts, different MEANING: --tier all with zero live")
print("  rows is a drained tier; --tier fixed with zero live rows is correct.")
print()

# --- Case 5: pass_rate over zero gating cases is None, not 0.0. ------------
print("case 5  no gating cases means no pass rate, not a zero pass rate")
live_only = rr.summarize(rr.build(header("live"), [r for r in ROWS if r.tier == "live"]))
check("pass_rate is None", live_only["pass_rate"] is None, repr(live_only["pass_rate"]))
check("gating is 0", live_only["gating"] == 0)
check("the live cases are still reported", live_only["reported"] == 2)
print()

# --- Case 6: the reader refuses what it cannot honestly compare. -----------
print("case 6  read() rejects a record it cannot trust (the exit-2 path)")
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    (d / "notjson.json").write_text("{ not json", encoding="utf-8")
    (d / "v1.json").write_text(json.dumps(
        {"record_version": 1, "header": {}, "cases": []}), encoding="utf-8")
    good = json.loads(rr.write(tmp, header(), ROWS).read_text(encoding="utf-8"))
    nofield = json.loads(json.dumps(good))
    del nofield["header"]["model_digest"]
    (d / "nofield.json").write_text(json.dumps(nofield), encoding="utf-8")
    nospine = json.loads(json.dumps(good))
    del nospine["cases"][2]["result"]
    (d / "nospine.json").write_text(json.dumps(nospine), encoding="utf-8")
    # v4: present is not enough. A v3-shaped boolean, and a string outside
    # the enum, must both be refused - summarize() counts by equality, so
    # either would silently vanish from all three counts.
    boolres = json.loads(json.dumps(good))
    boolres["cases"][0]["result"] = True
    (d / "boolres.json").write_text(json.dumps(boolres), encoding="utf-8")
    offenum = json.loads(json.dumps(good))
    offenum["cases"][1]["result"] = "error"
    (d / "offenum.json").write_text(json.dumps(offenum), encoding="utf-8")
    v3 = json.loads(json.dumps(good))
    v3["record_version"] = 3
    (d / "v3.json").write_text(json.dumps(v3), encoding="utf-8")

    for name, fragment in (("notjson.json", "not valid JSON"),
                           ("v1.json", "record_version"),
                           ("nofield.json", "model_digest"),
                           ("nospine.json", "case 2 missing result"),
                           ("boolres.json", "case 0 result must be one of"),
                           ("offenum.json", "case 1 result must be one of"),
                           ("v3.json", "record_version 3")):
        try:
            rr.read(d / name)
            check(f"{name} raises ValueError", False, "it did not raise")
        except ValueError as exc:
            check(f"{name} raises ValueError naming the problem", fragment in str(exc),
                  str(exc)[-60:])
print("  v3.json matters for the same reason v1 did: its `passed=False` rows")
print("  cannot say which were really a crashed judge.")
print("  v1.json matters: a version-1 row has no `passed`, so reading it would")
print("  count every case as failed and report a regression that never happened.")
print()

# --- Case 7: the point of the step 3 reshape. ------------------------------
print("case 7  one record holds BOTH scorers' rows, summarized by neither")
mixed = [
    rr.CaseRow(case_sha256="c" * 64, id="agent-01", tier="fixed", result="pass",
               detail={"outcome": True, "trajectory": True, "answer": "Spooler is stopped.",
                       "missing": [], "extra": []}),
    rr.CaseRow(case_sha256="c" * 64, id="retr-01", tier="fixed", result="pass",
               detail={"question": "print jobs are piling up", "answerable": True,
                       "expect": "service-restart.md#symptoms", "rank": 1,
                       "top_score": 0.7314, "hit_ids": ["service-restart.md#symptoms"]}),
    rr.CaseRow(case_sha256="c" * 64, id="retr-02", tier="fixed", result="fail",
               detail={"question": "how do I file expenses", "answerable": False,
                       "expect": None, "rank": 0, "top_score": 0.6002, "hit_ids": ["x#y"]},
               note="scored above REFUSE_BELOW - would not have been refused"),
]
with tempfile.TemporaryDirectory() as tmp:
    mixed_totals = rr.summarize(rr.read(rr.write(tmp, header(), mixed)))
check("3 cases, 3 gating", mixed_totals["case_count"] == 3 and mixed_totals["gating"] == 3)
check("2 passed, 1 failed, 0 unscored",
      mixed_totals["gating_passed"] == 2 and mixed_totals["gating_failed"] == 1
      and mixed_totals["gating_unscored"] == 0)
check("pass_rate 2/3", mixed_totals["pass_rate"] == round(2 / 3, 4),
      f"got {mixed_totals['pass_rate']}")
check("1 row carries a note", mixed_totals["rows_with_notes"] == 1)
print("  summarize() touched id, tier and result and nothing else. It has never")
print("  heard of rank, top_score, trajectory or answer - which is why a third")
print("  scorer for Project 2 will not require changing it.")
print()

# --- Case 8: the atomic write leaves nothing behind. -----------------------
print("case 8  the write is atomic - no .partial survives it")
with tempfile.TemporaryDirectory() as tmp:
    rr.write(tmp, header(), ROWS)
    leftovers = [p.name for p in Path(tmp).iterdir() if p.name.endswith(".partial")]
    check("no .partial file left in the directory", leftovers == [], str(leftovers))
    check("exactly one file written", len(list(Path(tmp).iterdir())) == 1)
print()

# --- Case 9: the dataclasses refuse nonsense at construction. --------------
print("case 9  a bad tier or selection is refused where it is made")
try:
    rr.CaseRow(case_sha256="c" * 64, id="x", tier="smoke", result="pass")
    check("CaseRow rejects an unknown tier", False, "it did not raise")
except ValueError as exc:
    check("CaseRow rejects an unknown tier", "tier must be" in str(exc))
for bad in (True, None, "passed", ""):
    try:
        rr.CaseRow(case_sha256="c" * 64, id="x", tier="fixed", result=bad)
        check(f"CaseRow rejects result={bad!r}", False, "it did not raise")
    except ValueError as exc:
        check(f"CaseRow rejects result={bad!r}", "result must be one of" in str(exc))
try:
    header("everything")
    check("Header rejects an unknown tier_selection", False, "it did not raise")
except ValueError as exc:
    check("Header rejects an unknown tier_selection", "fixed|live|all" in str(exc))
print()

# --- Case 10: the suite hash hashes CONTENT, not the path. -----------------
print("case 10  suite_sha256 tracks the file's bytes")
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "suite.jsonl"
    p.write_text('{"id": "a"}\n', encoding="utf-8")
    first = rr.suite_sha256(p)
    p.write_text('{"id": "a"}\n{"id": "b"}\n', encoding="utf-8")
    second = rr.suite_sha256(p)
    check("same path, edited content, different hash", first != second)
    check("hash is 64 hex chars",
          len(first) == 64 and all(c in "0123456789abcdef" for c in first))
print()

if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
