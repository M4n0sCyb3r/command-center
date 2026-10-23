"""Build step 3 part 2a check: a suite file declares what it is.

WHAT THIS CHECK IS FOR
----------------------
Three things became true of a suite file in this step: it carries a HEADER
naming its scorer, every case carries an `id` and a `tier`, and one parser
(src/suite.py) owns those rules for both loaders. This file proves all three,
and proves the rejections are real.

THE PART THAT MATTERS IS CASE 4, AND CASE 5 IS WHY IT MATTERS
-------------------------------------------------------------
Case 1 asserts the three real files parse correctly. On its own that is nearly
worthless: they were written to parse. It would pass equally against a
parse_suite() that validated NOTHING and just returned whatever it read.

So case 4 hands the parser ten deliberately broken files and requires each one
to raise - and case 5 is the control that stops case 4 being vacuous in the
other direction, by requiring a WELL-FORMED file to parse. Without case 5, a
parse_suite() that raised on absolutely everything would pass case 4 with a
clean sheet, which is the same sabotage build step 2 caught with a hand count
and part 1 caught with a flipped row. Third time this device has earned its
keep.

Case 3's cross-file check is the one the SPLIT made necessary. While the
calibration fixtures shared a file with the cases they are labelled against,
moving a case and orphaning its pairs was impossible. Now it is possible, so it
is measured.

NEEDS NO MODEL AND NO NETWORK. Every case here is a file-reading check, which
is the point: it can run in CI, unlike the retrieval suite (which needs the
embedding model) and the live tier (which needs the lab VMs).

Run:  python tests/test_suite_header.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import suite as s                     # noqa: E402
from run_record import TIERS          # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def write_suite(lines: list, name: str = "tmp.jsonl") -> Path:
    """Write a throwaway suite file. Each line is dumped as JSON, except a
    string, which is written raw so malformed JSON can be tested too."""
    path = Path(tempfile.mkdtemp()) / name
    body = "\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines)
    path.write_text(body + ("\n" if body else ""), encoding="utf-8")
    return path


def rejects(label: str, lines: list, expect_in: str) -> None:
    """A malformed file must raise ValueError, and the message must name the
    problem - a rejection nobody can read costs an evening."""
    try:
        s.parse_suite(write_suite(lines))
    except ValueError as exc:
        hit = expect_in.lower() in str(exc).lower()
        check(f"rejects {label}", hit, f"message: {exc}" if not hit else "")
    except Exception as exc:                          # noqa: BLE001
        check(f"rejects {label}", False, f"raised {type(exc).__name__}: {exc}")
    else:
        check(f"rejects {label}", False, "parsed without complaint")


# Hand-counted from the files, 2026-09-21. Literals, not computed here.
SUITES = REPO_ROOT / "evals" / "suites"
FIXTURES = REPO_ROOT / "evals" / "fixtures" / "judge-calibration.jsonl"
EXPECTED = {
    SUITES / "retrieval.jsonl": ("retrieval", "retrieval", 20),   # 16 -> 20 2026-10-17 P02 step 8: one question per SOP failure-mode table; 16 answerable + 4 refuse.   # 14 -> 16 2026-10-05: near-misses moved to fixtures/answer-refusal.jsonl, 3 out-of-domain added (ADR 0023); 12 answerable + 4 refuse
    SUITES / "agent.jsonl": ("agent", "agent", 13),
    FIXTURES: ("judge-calibration", None, 9),   # 8 -> 9 2026-09-24: the agent's verbatim refusal
    # 2026-10-09 P02 step 6c: fixtures/answer-refusal.jsonl (7, scorer null, ADR 0023) RETIRED -
    # its cases moved word for word into the answer suite, with 12 answerable + 4 out-of-domain.
    SUITES / "answer.jsonl": ("answer", "answer", 27),   # 23 -> 27 2026-10-17 P02 step 8: the same 4 SOP questions, word for word
}

print("suite header - build step 3 part 2a: a file declares what it is")
print()

# --- Case 1: the three real files parse and declare what they should. -------
print("case 1  every real file parses and declares its scorer")
parsed: dict[Path, s.Suite] = {}
for path, (name, scorer, count) in EXPECTED.items():
    try:
        parsed[path] = s.parse_suite(path)
    except Exception as exc:                          # noqa: BLE001
        check(f"{path.name} parses", False, f"{type(exc).__name__}: {exc}")
        continue
    got = parsed[path]
    check(f"{path.name}: suite == {name!r}", got.header.suite == name, f"got {got.header.suite!r}")
    check(f"{path.name}: scorer == {scorer!r}", got.header.scorer == scorer,
          f"got {got.header.scorer!r}")
    check(f"{path.name}: {count} entries", len(got.cases) == count, f"got {len(got.cases)}")
    check(f"{path.name}: runnable is {scorer is not None}",
          got.header.runnable == (scorer is not None))
print()

# --- Case 2: the spine, and what --tier selects. ---------------------------
print("case 2  every case has a unique id and a known tier, and select() partitions")
for path in (SUITES / "retrieval.jsonl", SUITES / "agent.jsonl"):
    got = parsed[path]
    ids = got.ids
    check(f"{path.name}: {len(ids)} ids, all unique", len(set(ids)) == len(ids),
          f"{len(ids) - len(set(ids))} duplicates")
    check(f"{path.name}: every tier in {TIERS}",
          all(c["tier"] in TIERS for c in got.cases))
    fixed, live, every = got.select("fixed"), got.select("live"), got.select("all")
    check(f"{path.name}: fixed + live == all",
          len(fixed) + len(live) == len(every) == len(got.cases),
          f"fixed={len(fixed)} live={len(live)} all={len(every)}")
    check(f"{path.name}: tier_counts agrees with select()",
          got.tier_counts() == {"fixed": len(fixed), "live": len(live)},
          str(got.tier_counts()))
    # STATED OUT LOUD because a zero here is the thing ADR 0006 call 2 wants
    # visible: the live tier currently holds NO cases in either suite, so
    # --tier live would select nothing at all. Whether that is exit 2 is build
    # step 4's call and is deliberately not decided by this check.
    print(f"       {path.name}: tier_counts = {got.tier_counts()}")
print()

# --- Case 3: the fixtures file, and the integrity the split now needs. -----
print("case 3  the fixtures file is not a suite, and no pair is orphaned")
fix = parsed[FIXTURES]
check("declares scorer=null", fix.header.scorer is None)
check("no entry carries a tier", all("tier" not in c for c in fix.cases),
      str([c["id"] for c in fix.cases if "tier" in c]))
agent_ids = set(parsed[SUITES / "agent.jsonl"].ids)
orphans = sorted(c["id"] for c in fix.cases if c.get("calibration_of") not in agent_ids)
check("every calibration_of names a live agent case id", orphans == [], f"orphans: {orphans}")
check("every entry carries candidate_facts and a label",
      all({"candidate_facts", "label"} <= set(c) for c in fix.cases))
print("       While the pairs shared a file with their targets, orphaning one was")
print("       impossible. The split makes it possible, so it is now measured.")
print()

# --- Case 4: POSITIVE CONTROLS. every rejection is real. -------------------
print("case 4  positive controls - ten malformed files, each must be refused")
HEADER = {"suite": "t", "scorer": "agent"}
CASE = {"id": "a", "tier": "fixed"}

rejects("a first line that is a case, not a header",
        [CASE, {"id": "b", "tier": "fixed"}], "must be a header")
rejects("a header with no 'scorer' key", [{"suite": "t"}, CASE], "must declare 'scorer'")
rejects("a header whose scorer is an empty string",
        [{"suite": "t", "scorer": ""}, CASE], "non-empty string or null")
rejects("a case with no id", [HEADER, {"tier": "fixed"}], "'id'")
rejects("two cases sharing an id", [HEADER, CASE, dict(CASE)], "duplicate id")
rejects("a case with no tier", [HEADER, {"id": "a"}], "needs 'tier'")
rejects("a case with an unknown tier", [HEADER, {"id": "a", "tier": "staging"}], "needs 'tier'")
rejects("a second header further down the file",
        [HEADER, CASE, {"suite": "t2", "scorer": "agent", "id": "c", "tier": "fixed"}],
        "exactly one header")
rejects("a header and no cases at all", [HEADER], "no cases")
rejects("an empty file", [], "empty")
rejects("a tier on an entry in a scorer=null file",
        [{"suite": "t", "scorer": None}, CASE], "must not carry 'tier'")
rejects("malformed JSON on a case line", [HEADER, '{"id": "a", "tier": '], "line 2")
print()

# --- Case 5: THE CONTROL ON THE CONTROLS. ---------------------------------
print("case 5  control - a well-formed file still parses, so case 4 means something")
ok_path = write_suite([
    {"suite": "control", "scorer": "agent", "description": "hand-written"},
    {"id": "one", "tier": "fixed", "question": "q1"},
    {"id": "two", "tier": "live", "question": "q2"},
])
try:
    control = s.parse_suite(ok_path)
    check("parses", True)
    check("scorer == 'agent'", control.header.scorer == "agent")
    check("2 cases, one per tier", control.tier_counts() == {"fixed": 1, "live": 1},
          str(control.tier_counts()))
    check("select('live') returns exactly the live case",
          [c["id"] for c in control.select("live")] == ["two"])
except Exception as exc:                              # noqa: BLE001
    check("parses", False, f"{type(exc).__name__}: {exc}")
print("       Without this, a parse_suite() that raised on EVERYTHING would pass")
print("       case 4 with a clean sheet. Step 2's sabotage, two steps later.")
print()

# --- Case 6: a non-runnable file has no tiers, and says so. ---------------
print("case 6  a scorer=null file refuses tier questions instead of answering 0")
for label, call in (("tier_counts()", lambda: fix.tier_counts()),
                    ("select('all')", lambda: fix.select("all"))):
    try:
        call()
    except ValueError as exc:
        check(f"{label} raises", "not a runnable suite" in str(exc), str(exc))
    else:
        check(f"{label} raises", False, "returned a value")
try:
    parsed[SUITES / "agent.jsonl"].select("staging")
except ValueError as exc:
    check("select() rejects an unknown selection", "tier selection must be" in str(exc))
else:
    check("select() rejects an unknown selection", False, "returned a value")
print()

# --- Case 7: both loaders read their own file through the shared parser. --
print("case 7  both module loaders agree with the parser")
import retrieval as r                                 # noqa: E402
import score as sc                                    # noqa: E402

cases = r.load_suite()
check("retrieval.load_suite() returns 20 Cases", len(cases) == 20, f"got {len(cases)}")
check("ids are in file order",
      [c.id for c in cases] == parsed[SUITES / "retrieval.jsonl"].ids)
check("every Case carries id, tier, question, expect",
      all(c.id and c.tier and c.question is not None for c in cases))
check("the four unanswerable cases survived as expect=None",   # 2 -> 4 2026-10-05 (ADR 0023)
      sum(1 for c in cases if c.expect is None) == 4,
      f"got {sum(1 for c in cases if c.expect is None)}")
agent_cases = sc.load_suite()
check("score.load_suite() returns 13 dicts", len(agent_cases) == 13, f"got {len(agent_cases)}")
check("no calibration fixture is left in the agent suite",
      all(c.get("category") != "judge-calibration" for c in agent_cases))
check("score.load_calibration() returns 9 fixtures", len(sc.load_calibration()) == 9,
      f"got {len(sc.load_calibration())}")
print()

if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
