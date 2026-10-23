"""The run record - self-contained evidence of one suite execution.

Project 4, build step 2 of 8. Implements ADR 0007 decision 3a (the record is
header + per-case rows, totals DERIVED ON READ and never stored) and the
storage half of ADR 0008 decision 4a (a record is a reading; a baseline is the
same artifact promoted to source by an explicit --rebaseline).

WHAT THIS MODULE IS FOR
-----------------------
The project's definition of done is: change a prompt, run one command, and
state WITH EVIDENCE whether the change helped. "Whether it helped" is a
comparison between two executions, so something has to outlive the process
that produced it. This is that something.

A note on the word "run", because this repo now uses it for two things:
Lab 12's run is ONE agent answering ONE question (src/tracing.py). Project 4's
run is one execution of a WHOLE SUITE - many cases, one verdict, one exit code.
This module is about the second.

THREE RULINGS BUILT INTO THE SHAPE BELOW
----------------------------------------
1. ONE RECORD PER SUITE, NOT PER INVOCATION. --suite is repeatable, but ADR
   0008 call 4a pins the baseline at evals/baselines/<suite-stem>.json - one
   file per suite. A record spanning two suites has no baseline to be compared
   against, and comparison is the only reason a record exists. So the unit of
   comparison is a suite, which forces the unit of record to be a suite.

2. THE HEADER CARRIES THE TIER SELECTION, NOT THE TIER COUNTS. The build-step
   wording said "tier counts" in the header and "totals derived on read" in the
   same breath, and a count over rows IS a total. Storing it beside the rows it
   counts is the exact defect summarize() exists to forbid: a total written
   into the stream is a number that can disagree with the events beside it.
   What cannot be derived from the rows is the INTENT - what you asked for:

       header --tier fixed + 0 live rows  -> you did not ask for any. Fine.
       header --tier all   + 0 live rows  -> THE LIVE TIER HAS DRAINED.

   Those two look identical on a report without the selection in the header,
   and telling them apart is the whole reason ADR 0008 call 4c made --tier
   required. The per-tier COUNTS still print on every run - derived, by
   summarize() below.

3. WHOLE-FILE JSON, NOT JSONL. evals/runs/README.md argues JSONL for that
   directory, on the grounds that a process dying mid-write leaves a valid
   JSONL file with fewer lines. That argument is correct ABOUT TRACES and does
   not transfer here. A trace exists to explain a failure, so it must survive
   one. A record exists to be COMPARED, so it must be complete: a 30-of-35
   record read against a 35-case baseline reports five cases as vanished and
   exits 2, which is a crash being reported as a suite edit. A partial record
   is not a record. Promotion by --rebaseline is therefore a file COPY, and the
   committed baseline is bit-for-bit a reading somebody promoted - never a file
   no run ever produced.

WHAT IS TRANSCRIBED VS WHAT IS DERIVED - the line, stated once
--------------------------------------------------------------
A row carries what THE SCORER said about one case, verbatim, including its
`earned` field. The record does not recompute or second-guess it; transcription
is this module's whole job at the row level. "Derived, never stored" is a rule
about RUN-LEVEL TOTALS - pass rate, case counts, per-tier counts - and those
appear nowhere in the file. They are computed by summarize() on read.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Bumped whenever the on-disk shape changes. A baseline committed at step 2 and
# read by step 7's code is a real scenario, and a silent misread of an older
# shape is exactly the kind of thing that turns into a fake regression.
#
# VERSION 2 (build step 3): the row lost the agent scorer's vocabulary. See
# CaseRow. Version 1 records are NOT readable by this code, deliberately - a
# v1 row has no `passed` field, so summarize() would count every case as
# failed and report a catastrophic regression that never happened. Refusing to
# read it is the honest outcome; this is the field earning its place three
# days after it was added.
#
# VERSION 3 (agent-loop stall, call B, 2026-09-26): the header gained
# `incomplete`. A v2 record has no way to say "this run stopped early", so a v2
# reader handed a partial run would summarize the rows it has and derive a
# verdict from them - a clean 7/7 from a run that stalled on case 8. Refusing
# to read v2 is the honest outcome, and no baseline exists yet (step 6), so
# nothing committed is invalidated.
#
# VERSION 4 (judge-error gating, 2026-09-27): the spine's `passed: bool` became
# `result: "pass" | "fail" | "unscored"`. A judge that crashed used to write
# passed=False, so a broken INSTRUMENT gated as a failed AGENT (exit 1). The
# new value needs a third state, and it is a closed enum rather than
# `passed: bool | None` because `if r.get("passed")` reads None as a failure -
# every reader not yet updated would have reproduced the bug without a sound.
# Removing the key makes every such reader fail LOUDLY instead. A v3 record is
# refused for the usual reason: its `passed=False` rows cannot say which were
# really unscored.
#
# VERSION 5 (build step 6a, 2026-09-28): the header gained `sampling`, a
# CONDITION field. ADR 0009 showed the policy changes what the agent IS
# (aggregate-down failed under greedy, passed 5/5 under S1), so a baseline that
# does not say its policy reads a policy change as agent drift. Bumped BEFORE
# step 6 commits the first baseline - after it, this bump would make that
# baseline unreadable (exit 2 every run) until someone migrated it.
#
# VERSION 6 (build step 6b, 2026-09-28): the header gained `rebaseline`. A
# removal must be recorded IN the baseline (the build step's own check), and
# --rebaseline is a FILE COPY (build step 2), so the only place it can be
# written without editing a record after the fact is the header of the run
# that did the removing. Bumped now for 6a's reason: no baseline exists yet.
#
# VERSION 7 (build step 7, call C, 2026-09-28): identity at two levels. The
# header gained `suite_header_sha256`, every row `case_sha256` - both canonical
# (src/suite.py). A v6 record has no per-case identity, so a reworded case in
# it would compare "cleanly" - the one lie ADR 0008 4b(ii) exists to stop. This
# bump came AFTER the first baselines, so it costs a migration: the v6
# baselines are unreadable (exit 2) and are re-promoted by an explicit act.
RECORD_VERSION = 7

# The spine's verdict, closed. "unscored" = the measurement this case's verdict
# DEPENDED ON could not be taken (a judge error on a case whose trajectory
# passed). It is never written when the verdict is already decided without it -
# see scorers.agent_row, sub-call (i).
RESULTS = ("pass", "fail", "unscored")


def check_result(value: Any) -> None:
    """Raise ValueError unless `value` is one of RESULTS."""
    if value not in RESULTS:
        raise ValueError(f"result must be one of {RESULTS}, got {value!r}")

# ADR 0006 decision 2: `fixed` replays a dated capture and GATES; `live` runs
# against the host, asserts invariants only, and is reported but NEVER gates.
# Gating is therefore a property of the tier, which makes it derivable - so it
# is not a field on a row.
TIERS = ("fixed", "live")
GATING_TIERS = frozenset({"fixed"})

# Every field a header must carry to be readable. Checked on read, because a
# header missing a field is a comparison that cannot be made honestly, and that
# is a tooling error (exit 2) rather than a verdict.
REQUIRED_HEADER_FIELDS = (
    "suite_path", "suite_sha256", "suite_header_sha256", "tier_selection", "scorer",
    "model_name", "model_digest", "host_target", "started_at",
    # REQUIRED even though it is usually null. Optional would mean an absent key
    # reads as "complete" - and absence is exactly what a writer that never
    # heard of stalls produces. A record must SAY it finished, not merely fail
    # to say it did not.
    "incomplete",
    # REQUIRED, nullable - same reasoning as `incomplete`. null = this scorer
    # generates nothing. ABSENT = a writer that never heard of sampling, which
    # must not read as "did not sample".
    "sampling",
    # Build step 6b, call A (Jose, 2026-09-28: "the harness compares against
    # the old baseline before writing"). null = an ordinary reading. Otherwise
    # {replaced, removed}: this run was asked to BECOME the baseline, replacing
    # the one recorded at `replaced` (null: there was none) and retiring the
    # ids in `removed` (null: not established - the run was incomplete).
    "rebaseline",
)

REBASELINE_FIELDS = ("replaced", "removed")


def check_rebaseline(value: Any) -> None:
    """Raise ValueError unless `value` is None or a well-formed rebaseline marker."""
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != set(REBASELINE_FIELDS):
        raise ValueError(f"rebaseline must be null or an object with exactly "
                         f"{REBASELINE_FIELDS}, got {value!r}")
    replaced = value["replaced"]
    if replaced is not None and (not isinstance(replaced, str) or not replaced):
        raise ValueError(f"rebaseline.replaced must be null or a non-empty string, got {replaced!r}")
    removed = value["removed"]
    if removed is None:
        return
    if (not isinstance(removed, list)
            or not all(isinstance(i, str) and i for i in removed)
            or len(set(removed)) != len(removed)):
        raise ValueError(f"rebaseline.removed must be null or a list of distinct "
                         f"non-empty ids, got {removed!r}")


def check_sampling(value: Any) -> None:
    """Raise ValueError unless `value` is None or a non-empty {name: number}."""
    if value is None:
        return
    if not isinstance(value, dict) or not value:
        raise ValueError(f"sampling must be null or a non-empty object, got {value!r}")
    for k, v in value.items():
        if not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"sampling must map option names to numbers, got {k!r}: {v!r}")

# What an `incomplete` marker must carry. `turn` is stamped by agent.run() on
# the ModelError it re-raises (build step 3); null when whatever raised it was
# not the agent loop - a scorer with no turns, or a scripted fixture.
INCOMPLETE_FIELDS = ("case_id", "error", "turn")


def check_incomplete(marker: Any) -> None:
    """Raise ValueError unless `marker` is None or a well-formed stall marker."""
    if marker is None:
        return
    if not isinstance(marker, dict) or set(marker) != set(INCOMPLETE_FIELDS):
        raise ValueError(f"incomplete must be null or an object with exactly "
                         f"{INCOMPLETE_FIELDS}, got {marker!r}")
    if not isinstance(marker["case_id"], str) or not marker["case_id"]:
        raise ValueError(f"incomplete.case_id must be a non-empty string, got {marker['case_id']!r}")
    if not isinstance(marker["error"], str) or not marker["error"]:
        raise ValueError(f"incomplete.error must be a non-empty string, got {marker['error']!r}")
    turn = marker["turn"]
    if turn is not None and (isinstance(turn, bool) or not isinstance(turn, int) or turn < 1):
        raise ValueError(f"incomplete.turn must be null or a positive integer, got {turn!r}")

# THE SPINE. summarize() may depend on these and nothing else, because it must
# work on a file written last week by a process that no longer exists - and,
# since build step 3, by a SCORER THIS CODE HAS NEVER HEARD OF. Validated on
# read so the guarantee is structural rather than a convention each scorer is
# trusted to honour.
REQUIRED_ROW_FIELDS = ("id", "tier", "result")

# NOT a spine field - summarize() never reads it - but required on every row a
# record carries (record v7). Stamped by the HARNESS, never by a scorer, so P02's
# scorer will not need to know it exists.
ROW_IDENTITY_FIELD = "case_sha256"


def check_sha256(value: Any, what: str) -> None:
    if (not isinstance(value, str) or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)):
        raise ValueError(f"{what} must be a 64-character lowercase hex sha256, got {value!r}")


def suite_sha256(path: str | Path) -> str:
    """Hash the suite file's BYTES.

    ADR 0008 call 4b(ii) makes this an IDENTITY field: if it differs from the
    baseline's, the drift section is SUPPRESSED rather than printed, because
    case ids can still match while case 14's question has been reworded. That
    produces a number which looks like a real drift and is not one - silently
    wrong, which is the worst of the available failures.
    """
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc_now_iso() -> str:
    """ISO 8601 UTC with a Z, one second resolution.

    Stored in the header in the readable form because a baseline is COMMITTED
    and a human reads its diff. The filename gets the compact form (below),
    which matches tracing.py's run_id stamp and sorts correctly under `ls` -
    two renderings, one source.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(started_at: str) -> str:
    """Compact the header's timestamp for use in a filename."""
    return started_at.replace("-", "").replace(":", "")


@dataclass(frozen=True)
class Header:
    """What the run was measured WITH - never what it measured.

    Split by ADR 0008 call 4b(ii) into two classes that fail in opposite
    directions, and the split is why each field is here:

      IDENTITY  suite_path, suite_sha256, scorer  - a mismatch means the two
                runs are not measuring the same thing, so comparing them
                produces a plausible-looking lie. Drift is SUPPRESSED.
      CONDITION model_name, model_digest, host_target, sampling, started_at - a mismatch
                means real measurements under different conditions, which is
                the entire point of the harness. Drift is ANNOTATED and still
                compared.

    Neither touches the exit code. The gating cases ran, so a verdict WAS
    established; exit 2 stays reserved for "the verdict could not be
    established". A stale pin is a comparison the harness honestly declines.
    """

    suite_path: str
    suite_sha256: str        # the file's BYTES - provenance, printed, not compared
    suite_header_sha256: str # IDENTITY, canonical (build step 7) - see src/suite.py
    tier_selection: str      # fixed | live | all - the INTENT, see ruling 2
    scorer: str
    model_name: str
    model_digest: str
    host_target: str
    # Build step 6a. CONDITION: a mismatch annotates and still compares (step
    # 7). No default, deliberately - see REQUIRED_HEADER_FIELDS.
    sampling: dict[str, Any] | None
    # Build step 6b. No default, for sampling's reason: a writer that forgot
    # must fail loudly, not record "an ordinary reading".
    rebaseline: dict[str, Any] | None
    started_at: str = field(default_factory=utc_now_iso)
    # AGENT-LOOP STALL, CALL B. None = every selected case was scored. Otherwise
    # {case_id, error, turn}: the case whose model call timed out, after which
    # the run STOPPED. That case has no row. A record carrying this marker can
    # never yield a verdict (exit 2) and can never become a baseline (step 6).
    incomplete: dict[str, Any] | None = None
    record_version: int = RECORD_VERSION

    def __post_init__(self) -> None:
        check_incomplete(self.incomplete)
        check_sampling(self.sampling)
        check_rebaseline(self.rebaseline)
        if self.tier_selection not in ("fixed", "live", "all"):
            raise ValueError(
                f"tier_selection must be fixed|live|all, got {self.tier_selection!r}"
            )


@dataclass(frozen=True)
class CaseRow:
    """One case: a spine every scorer shares, plus that scorer's own detail.

    BUILD STEP 3 RESHAPED THIS, and the reason is worth keeping. Version 1's
    row was `outcome / trajectory / earned / answer / missing / extra` - the
    AGENT scorer's vocabulary, which was invisible as a problem while the agent
    scorer was the only one. The retrieval scorer has no tools and no answer
    text; it has a rank, a top score and the chunk ids that came back. Forcing
    it into v1's fields meant writing `trajectory=True` on every retrieval row,
    which makes `earned` meaningless for half the suites and has summarize()
    aggregating a field that means nothing.

    **That is ADR 0001 being right on schedule.** "A harness with one consumer
    is a wrapper around that consumer" - and the wrapper-shaped part only
    became visible when the second consumer arrived. It is why the project is
    built second.

    So: `id`, `tier` and `passed` are the spine - the only fields summarize()
    is allowed to touch, and the only ones that must never grow. `detail` is
    whatever the scorer thought worth recording, which is ADR 0007 call 3a's
    "the scored detail" made literal rather than enumerated.

      agent scorer     detail = outcome, trajectory, missing, extra, answer
      retrieval scorer detail = question, answerable, expect, rank,
                                top_score, hit_ids

    THE COST, named and paid: `detail` is an untyped bag, so a mistyped key is
    silent. It is paid in the checks - each scorer's detail keys are asserted
    by its own test, so a typo fails a check instead of producing a row nobody
    can read.

    `answer` is not a spine field but it is still the payload that makes a row
    investigable, especially on the `live` tier where a case asserts invariants
    only and "case_11: fail" says nothing without the text that failed. It
    lives in `detail` now; it has not stopped mattering.
    """

    id: str
    tier: str
    result: str                                    # THE scorer's verdict: RESULTS
    detail: dict[str, Any] = field(default_factory=dict)
    note: str = ""                                 # e.g. "judge error: ..."
    # Build step 7. None as the scorer returns it; the harness stamps it, and
    # build() refuses a row that reaches a record without it.
    case_sha256: str | None = None

    def __post_init__(self) -> None:
        check_result(self.result)
        if self.tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {self.tier!r}")


def build(header: Header, rows: Iterable[CaseRow]) -> dict[str, Any]:
    """Assemble the in-memory record. No totals. Deliberately no totals."""
    rows = list(rows)
    check_sha256(header.suite_header_sha256, "suite_header_sha256")
    for r in rows:
        check_sha256(r.case_sha256, f"case {r.id!r} case_sha256 (stamped by the harness)")
    return {
        "record_version": RECORD_VERSION,
        "header": asdict(header),
        "cases": [asdict(r) for r in rows],
    }


def record_filename(header: Header) -> str:
    """<stamp>-<suite-stem>.json - sorts by time, names what it measured."""
    stem = Path(header.suite_path).stem
    return f"{_stamp(header.started_at)}-{stem}.json"


def write(out_dir: str | Path, header: Header, rows: Iterable[CaseRow]) -> Path:
    """Write the record into out_dir. Returns the path written.

    THE WRITE IS ATOMIC, and that follows directly from ruling 3 rather than
    from general tidiness: we chose whole-file JSON on the grounds that a
    partial record must not exist, and a crash midway through a plain write()
    would create precisely that - a truncated file sitting in evals/runs/ that
    json.load refuses and a human has to reason about. Write to a temporary
    name in the same directory, then os.replace, which is atomic on the same
    filesystem. Either the whole record is there or nothing is.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / record_filename(header)
    tmp = final.with_suffix(".json.partial")
    payload = json.dumps(build(header, rows), indent=2, ensure_ascii=False)
    tmp.write_text(payload + "\n", encoding="utf-8")
    os.replace(tmp, final)
    return final


def read(path: str | Path) -> dict[str, Any]:
    """Load a record and check it is one. Raises ValueError if it is not.

    Validation lives here rather than at every call site because every caller -
    the drift comparison, --rebaseline, the report - needs the same guarantees,
    and a check written three times is a check that disagrees with itself twice.
    A caller maps these failures onto exit 2: a record that cannot be read is a
    verdict that could not be established, never a case that failed.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object, got {type(data).__name__}")

    version = data.get("record_version")
    if version != RECORD_VERSION:
        raise ValueError(
            f"{path}: record_version {version!r}, this code reads {RECORD_VERSION}"
        )

    header = data.get("header")
    if not isinstance(header, dict):
        raise ValueError(f"{path}: missing or malformed header")
    missing = [f for f in REQUIRED_HEADER_FIELDS if f not in header]
    if missing:
        raise ValueError(f"{path}: header missing {', '.join(missing)}")

    try:
        check_incomplete(header["incomplete"])
        check_sampling(header["sampling"])
        check_rebaseline(header["rebaseline"])
        check_sha256(header["suite_header_sha256"], "suite_header_sha256")
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc

    rows = data.get("cases")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: missing or malformed cases list")

    # The spine, checked here so summarize() never has to guard it. A row
    # missing `passed` would otherwise be counted as a failure, and a
    # catastrophic fake regression is far worse than a refusal to read.
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{path}: case {i} is not an object")
        absent = [f for f in REQUIRED_ROW_FIELDS if f not in row]
        if absent:
            raise ValueError(f"{path}: case {i} missing {', '.join(absent)}")
        # Present is not enough: `"result": true` would otherwise be read by
        # summarize() as neither pass nor fail nor unscored and vanish from all
        # three counts.
        try:
            check_result(row["result"])
            check_sha256(row.get(ROW_IDENTITY_FIELD), ROW_IDENTITY_FIELD)
        except ValueError as exc:
            raise ValueError(f"{path}: case {i} {exc}") from exc

    # The stalled case produced no row. A record claiming both is contradictory
    # - either the marker or the row is a lie - and a contradiction is a record
    # nobody can reason from.
    marker = header["incomplete"]
    if marker is not None and any(r["id"] == marker["case_id"] for r in rows):
        raise ValueError(f"{path}: incomplete names {marker['case_id']!r}, "
                         f"which also has a row")
    # Same contradiction, one field over: a case cannot be retired AND scored.
    retired = (header["rebaseline"] or {}).get("removed") or []
    both = sorted(set(retired) & {r["id"] for r in rows})
    if both:
        raise ValueError(f"{path}: rebaseline.removed names {both}, which also have rows")

    return data


def refusal(record: dict[str, Any]) -> str | None:
    """Why this record may NOT become a baseline, or None if it may.

    BUILD STEP 6b. One function, called by the harness (to decide) and by the
    report (to say), so the two cannot disagree about what happened.

    Both refusals are about a HOLE in the measurement - a baseline is an
    expectation every later run is compared against, so a hole in it becomes a
    hole in every comparison after it:
      incomplete    every case after the stall is missing; later runs would
                    report them all as NEW
      unscored      that case would read UNMEASURED against the baseline forever

    A FAILED gating case is NOT a refusal (call 6b-1 = B, Jose: "because it
    gives one verdict two sources" - sharpened: A would; the exit code already
    says whether a run is good). A failure is a measurement; the baseline
    records what IS, and the exit code keeps saying 1 on every run until it
    passes.
    """
    header = record["header"]
    if header["incomplete"] is not None:
        return (f"the run is INCOMPLETE (stalled at {header['incomplete']['case_id']}); "
                f"every case after the stall would be missing from the baseline")
    holes = [r["id"] for r in record["cases"]
             if r["result"] == "unscored" and r["tier"] in GATING_TIERS]
    if holes:
        return (f"{len(holes)} gating case(s) are UNSCORED ({', '.join(holes)}); each would "
                f"read UNMEASURED against the baseline on every later run")
    return None


def promote(record_path: str | Path, baseline_path: str | Path) -> Path:
    """Copy a record, byte for byte, into place as the baseline. Atomic.

    A COPY, never a re-serialisation (build step 2): the committed baseline is
    bit-for-bit a file some run wrote. Temp file + os.replace, like write(), so
    a crash mid-copy leaves the OLD baseline, never half of the new one.
    """
    src, dst = Path(record_path), Path(baseline_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_bytes(src.read_bytes())
    os.replace(tmp, dst)
    return dst


def summarize(record: dict[str, Any]) -> dict[str, Any]:
    """Derive every run-level number from the rows. Nothing here is stored.

    This is tracing.summarize()'s own ruling carried up a level, and the reason
    is the same: a total written into the file is a number that can disagree
    with the rows beside it, and a number computed from them cannot.

    The per-tier counts come out of here, which is how ADR 0006's requirement -
    "the report must print the case count per tier every run, so a tier quietly
    draining is visible on the face of the report" - is met without storing a
    count. Read alongside header.tier_selection, which says what was ASKED FOR.
    """
    rows = record["cases"]
    by_tier = {t: sum(1 for r in rows if r.get("tier") == t) for t in TIERS}

    gating = [r for r in rows if r.get("tier") in GATING_TIERS]
    reported = [r for r in rows if r.get("tier") not in GATING_TIERS]

    # Counted by EQUALITY with each named value, never by truthiness, and
    # failed is counted too rather than derived as `len - passed`. Derivation
    # is what made the v3 bug structural: anything not a pass became a fail.
    def count(rs: list[dict[str, Any]], value: str) -> int:
        return sum(1 for r in rs if r.get("result") == value)

    gating_passed = count(gating, "pass")
    reported_passed = count(reported, "pass")

    return {
        "suite_path": record["header"]["suite_path"],
        "suite_sha256": record["header"]["suite_sha256"],
        "tier_selection": record["header"]["tier_selection"],
        # Named `case_count`, not `cases`, on purpose. `cases` is already the
        # record's key for the ROWS THEMSELVES, and a word that means "the
        # rows" in the file and "how many rows" in the summary is the same
        # trap ADR 0007 had to disambiguate for the word "run". The check that
        # asserts no total is stored found this collision on its first run.
        "case_count": len(rows),
        "by_tier": by_tier,
        # Gating - these decide the exit code, in the caller.
        "gating": len(gating),
        "gating_passed": gating_passed,
        "gating_failed": count(gating, "fail"),
        # Judge-error gating (2026-09-27). Any of these makes the verdict exit
        # 2: the case a verdict needed was not measured.
        "gating_unscored": count(gating, "unscored"),
        # None, not 0.0, when nothing gated. A pass rate over zero cases is not
        # 0% - it is a question with no answer, and printing 0% would read as a
        # total failure on a report.
        #
        # Also None when any gating case is UNSCORED. Over all gating cases the
        # rate would count an unmeasured case as a failure (the v3 bug, as a
        # number); over the scored ones only, a judge dying on every hard case
        # would RAISE it (rejected option C). Neither is the agent's rate, so
        # there is no rate - the counts above say what is known.
        "pass_rate": (round(gating_passed / len(gating), 4)
                      if gating and not count(gating, "unscored") else None),
        # Reported but never gating (ADR 0006 decision 2).
        "reported": len(reported),
        "reported_passed": reported_passed,
        "reported_failed": count(reported, "fail"),
        "reported_unscored": count(reported, "unscored"),
        # Renamed from `judge_errors` at build step 3. A retrieval row with a
        # note has not had a judge error - it has no judge. Scorer-specific
        # vocabulary in a scorer-agnostic function is the same defect as
        # `trajectory` on a retrieval row, just smaller and easier to miss.
        "rows_with_notes": sum(1 for r in rows if r.get("note")),
        # Not a total - a header fact passed through, so verdict() can see it
        # without reaching back into the record. None = the run finished.
        "incomplete": record["header"].get("incomplete"),
    }
