"""The report: what a run measured, what it was measured against, and what failed.

Project 4, build step 4. Everything here is derived from a run record on disk
plus, when one exists, the committed baseline it is compared against. Nothing
is stored and nothing is recomputed from the rows a second time - the numbers
come from run_record.summarize(), which is the one place they are derived.

WHAT THE REPORT IS ALLOWED TO CLAIM
-----------------------------------
Build step 3's first real run made this a live question. The agent suite
returned `pass_rate 0.4615`, and reading the record showed that number was 2
genuine agent failures, 4 judge false-fails and 1 case whose expectation made a
correct refusal impossible to pass. Publishing 0.4615 as agent quality would be
the error Lab 10's own teaching line names: never average a false pass and a
false fail into one accuracy number.

So the report **itemizes rather than classifies**. It prints the spine-level
counts - the three fields every scorer shares - and then every failing row's
OWN detail, verbatim, in that scorer's own vocabulary.

THE CORRECTION THAT SHAPED THIS FILE, worth keeping because it nearly went the
other way: the plan was to split failures into "trajectory" and "outcome-only"
counts. Those are the AGENT scorer's words. A retrieval row has neither - it
has a rank and a top score - so counting by them would put scorer-specific
vocabulary inside a scorer-agnostic renderer. That is `judge_errors` in
run_record at part 1, and `category == "judge-calibration"` in score.calibrate
at part 2b, arriving a third time in a new place. The rule that keeps winning:
`summarize()` and anything like it may touch the spine and nothing else.

What it therefore refuses to do is ATTRIBUTE a failure. A heuristic was
available - "extracted contains expected as a substring, so this is probably a
judge false-fail" - and it would have been right on 3 of the 4 cases tonight.
It is refused anyway: attributing a failure between the agent and the judge is
what Lab 10's CALIBRATION measures, against human labels, and a report that
guessed would be a second opinion competing with compare_facts()'s verdict -
two answers to one question, which this project has already ruled against.

THE BASELINE'S COMMIT AND DATE PRINT EVERY RUN
----------------------------------------------
Owed by ADR 0008 call 4b. The baseline is PINNED and moves only on an explicit
--rebaseline, so its cost is that it goes stale silently - and the only defence
is that its age is on the face of every report.

If git cannot tell us the commit, the report says so and carries on. That is
NOT the same decision as model_digest() raising when it cannot read a digest,
and the difference is worth naming: a model digest describes what PRODUCED the
measurement, so a record missing it is not comparable at all; the baseline's
commit is an annotation ON a comparison whose inputs we already hold (ADR 0008
4b(ii) classes baseline date/commit as a CONDITION field, which annotates and
never gates). One is the evidence; the other is a note about the evidence.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import drift as drift_mod
from run_record import GATING_TIERS, TIERS, refusal, summarize

# How much of one detail value to show. The record holds the whole thing and
# the report names the record's path, so truncating here loses nothing - but a
# 4 KB answer string in a terminal report loses everything else.
VALUE_WIDTH = 96


def baseline_path(suite_path: str | Path, explicit: str | None = None) -> Path:
    """Where this suite's baseline lives. ADR 0008 call 4a.

    Resolved HERE, at step 4, rather than at step 6 with --rebaseline, because
    the report has to say whether a baseline exists before anything can write
    one. Step 6 adds the writing; this is the reading.
    """
    if explicit:
        return Path(explicit)
    stem = Path(suite_path).stem
    return Path("evals") / "baselines" / f"{stem}.json"


def git_provenance(path: str | Path) -> str:
    """Whether the baseline ON DISK is the committed one, and if so which commit.

    Returns a readable reason instead of raising: see the module docstring on
    why this annotates rather than gates.

    FIXED 2026-09-28 (build step 7), found live. This used to ask only "which
    commit last touched this path?" - and a DELETION touches it. After the v6
    baselines were retired in cc4efec and new ones promoted, the report printed
    `commit cc4efec` for files git had never seen: an uncommitted baseline
    reported as committed. The same lie waited in a second place: a re-promoted
    (or hand-edited) baseline, before commit, printed the OLD commit for the
    NEW contents. So the question is now "does the file on disk match what is
    committed?", asked in that order:
      untracked                  -> NOT COMMITTED
      tracked, differs from HEAD -> MODIFIED since <commit>
      clean                      -> <commit> <date>
    """
    path = Path(path).resolve()
    where = path.parent if path.parent.exists() else Path.cwd()

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(where), *args],
                              capture_output=True, text=True, timeout=10)
    try:
        tracked = git("ls-files", "--error-unmatch", "--", path.name)
        if tracked.returncode != 0:
            # ADR 0008 call 4a makes a baseline a COMMITTED file; an
            # uncommitted one is a local reading nobody else can reproduce.
            return "NOT COMMITTED - a baseline must be committed (ADR 0008, 4a)"
        last = git("log", "-1", "--format=%h %cI", "--", path.name)
        dirty = git("diff", "--quiet", "HEAD", "--", path.name)
    except Exception as exc:                        # noqa: BLE001
        return f"unknown (git unavailable: {type(exc).__name__})"
    if last.returncode != 0:
        return f"unknown (git exited {last.returncode})"
    line = last.stdout.strip()
    if dirty.returncode == 1:
        return (f"MODIFIED since {line.split()[0] if line else '?'} - the file on disk is "
                f"not the committed baseline (ADR 0008, 4a)")
    if dirty.returncode != 0:
        return f"unknown (git diff exited {dirty.returncode})"
    return line


def _fmt(value: Any) -> str:
    """One detail value, rendered short. JSON so a dict stays readable."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text.replace("\n", "\\n")
    if len(text) <= VALUE_WIDTH:
        return text
    return text[:VALUE_WIDTH - 1] + "…"


def _identity_fmt(field_name: str, value: Any) -> str:
    """One header value for a mismatch line. Build step 7."""
    if field_name == "sampling":
        return fmt_sampling(value)
    if isinstance(value, str) and len(value) == 64:
        return value[:16] + "…"
    return str(value)


def fmt_sampling(sampling: dict[str, Any] | None) -> str:
    """One line, keys sorted so two reports diff cleanly. Build step 6a."""
    if sampling is None:
        return "none - this scorer generates nothing"
    return " ".join(f"{k}={sampling[k]}" for k in sorted(sampling))


def render(record: dict[str, Any], *, record_path: str | Path,
           baseline: dict[str, Any] | None = None,
           baseline_file: str | Path | None = None,
           selected: int | None = None,
           suite_cases: int | None = None,
           drift: "drift_mod.Drift | None" = None) -> list[str]:
    """The report for one suite's run, as lines. Returns rather than prints so
    the check can read it without capturing stdout."""
    header = record["header"]
    got = summarize(record)
    lines: list[str] = []

    lines.append(f"=== {Path(header['suite_path']).name} ===")
    lines.append(f"  scorer     {header['scorer']}")
    lines.append(f"  measured   {header['model_name']}  digest {_fmt(header['model_digest'])}")
    lines.append(f"  host       {header['host_target']}")
    lines.append(f"  sampling   {fmt_sampling(header['sampling'])}")
    lines.append(f"  suite      sha256 {header['suite_sha256'][:16]}…  "
                 f"identity {header['suite_header_sha256'][:16]}…")
    lines.append(f"  started    {header['started_at']}")

    # ADR 0006 decision 2: the per-tier count prints EVERY run, so a tier that
    # has quietly drained is visible on the face of the report rather than
    # looking like a tier that passed.
    tiers = "  ".join(f"{t}={got['by_tier'][t]}" for t in TIERS)
    scope = "" if suite_cases is None else f" of {suite_cases} in the suite"
    chose = "" if selected is None else f"  selected {selected}{scope}"
    lines.append(f"  tiers      {tiers}  (--tier {header['tier_selection']}){chose}")

    # AGENT-LOOP STALL, CALL B. Printed ABOVE the gating line, because the
    # gating line on a partial run reads like a verdict ("7/7 passed") and is
    # not one. Whoever reads only the first line that looks like a result must
    # hit this one first.
    stall = header.get("incomplete")
    if stall is not None:
        turn = "turn unknown" if stall["turn"] is None else f"turn {stall['turn']}"
        lines.append(f"  INCOMPLETE stalled at {stall['case_id']} ({turn}): {stall['error']}")
        lines.append(f"             {got['case_count']} case(s) scored before the stall. The run "
                     f"STOPPED - the counts")
        lines.append( "             below are what was measured, NOT a verdict. Exit 2.")

    # JUDGE-ERROR GATING (2026-09-27). Also ABOVE the gating line, for the
    # same reason as INCOMPLETE: "10/13 passed" beside 1 unscored reads like a
    # verdict about 13 cases and is a verdict about 12. The line says which
    # exit the ordering in eval_harness.verdict() produces, so nobody has to
    # re-derive sub-call (x) from the counts.
    unscored = got["gating_unscored"]
    if unscored and stall is None:
        lines.append(f"  UNSCORED   {unscored} gating case(s): the judge raised, so the outcome "
                     f"was never measured.")
        if got["gating_failed"]:
            lines.append( "             A gating case FAILED on its own, so the regression is proven")
            lines.append( "             without them. Exit 1.")
        else:
            lines.append( "             Nothing failed, but an unmeasured case could have. No verdict.")
            lines.append( "             Exit 2.")

    rate = got["pass_rate"]
    if rate is not None:
        rate_text = str(rate)
    elif unscored:
        rate_text = f"n/a ({unscored} unscored - see summarize())"
    else:
        rate_text = "n/a (no gating cases)"
    lines.append(f"  GATING     {got['gating_passed']}/{got['gating']} passed  "
                 f"failed {got['gating_failed']}  unscored {unscored}  "
                 f"pass_rate {rate_text}")
    lines.append(f"  reported   {got['reported_passed']}/{got['reported']} passed  "
                 + (f"unscored {got['reported_unscored']}  " if got["reported_unscored"] else "")
                 + f"(tiers outside {sorted(GATING_TIERS)} never gate - ADR 0006)")
    if got["rows_with_notes"]:
        lines.append(f"  notes      {got['rows_with_notes']} row(s) carry a note")

    # P02 step 6c (ADR 0025 part 2): the retry rate and the refusal rates are
    # reported EVERY run - derived here from the rows, never stored (ADR 0007).
    # Generic by shape, not by scorer name: any scorer whose detail carries a
    # STRING `outcome` gets a tally (the agent scorer's `outcome` is a bool and
    # is untouched), and `attempts` adds the retry line.
    named = [r["detail"] for r in record["cases"]
             if isinstance(r.get("detail", {}).get("outcome"), str)]
    if named:
        tally: dict[str, int] = {}
        for d in named:
            tally[d["outcome"]] = tally.get(d["outcome"], 0) + 1
        lines.append("  OUTCOMES   " + "  ".join(f"{k} {v}" for k, v in sorted(tally.items())))
        called = [d for d in named if isinstance(d.get("attempts"), int) and d["attempts"] > 0]
        if called:
            retried = sum(1 for d in called if d["attempts"] > 1)
            lines.append(f"  RETRIES    {retried} of {len(called)} model-called case(s) needed the "
                         f"one retry (ADR 0018)")

    # ---- every failing row, in its own scorer's words --------------------
    # By equality with the named value. `not r.get("result")` would be False
    # for every row now - "fail" is a non-empty string - and the section
    # would silently print "none". Exactly the silent misread (c) exists for.
    failed = [r for r in record["cases"] if r.get("result") == "fail"]
    if failed:
        lines.append(f"  FAILURES   {len(failed)} row(s). Each row's detail is printed in its")
        lines.append(f"             own scorer's vocabulary; this report does NOT attribute a")
        lines.append(f"             failure to the agent or to the judge - see src/report.py.")
        for row in failed:
            gates = "gating" if row.get("tier") in GATING_TIERS else "reported"
            lines.append(f"    - {row['id']}  [{row.get('tier')}, {gates}]"
                         + (f"  note: {row['note']}" if row.get("note") else ""))
            for key in sorted(row.get("detail", {})):
                lines.append(f"        {key:<12} {_fmt(row['detail'][key])}")
    else:
        lines.append("  FAILURES   none")

    # Unscored rows are NOT failures, so they get their own section - with
    # their detail, because `answer` beside `judged: false` is what lets a
    # human grade the case by hand when it matters.
    unscored_rows = [r for r in record["cases"] if r.get("result") == "unscored"]
    for row in unscored_rows:
        gates = "gating" if row.get("tier") in GATING_TIERS else "reported"
        lines.append(f"    ? {row['id']}  [{row.get('tier')}, {gates}, UNSCORED]"
                     + (f"  note: {row['note']}" if row.get("note") else ""))
        for key in sorted(row.get("detail", {})):
            lines.append(f"        {key:<12} {_fmt(row['detail'][key])}")

    # ---- the baseline, and its age ---------------------------------------
    where = baseline_path(header["suite_path"]) if baseline_file is None else Path(baseline_file)
    if baseline is None:
        lines.append(f"  BASELINE   NONE at {where}")
        lines.append( "             Nothing to compare against. A baseline is created by an")
        lines.append( "             explicit --rebaseline (ADR 0008, call 4b).")
    else:
        b_header = baseline["header"]
        lines.append(f"  BASELINE   {where}")
        lines.append(f"             commit {git_provenance(where)}")
        lines.append(f"             recorded {b_header['started_at']}  "
                     f"scorer {b_header['scorer']}  model {b_header['model_name']}")
        # BUILD STEP 7: a CONDITION mismatch annotates and still compares
        # (ADR 0008, 4b-ii) - these are real measurements under different
        # circumstances, which is what the harness is for.
        _, conditions = drift_mod.header_mismatches(record, baseline)
        if conditions:
            lines.append( "  CONDITIONS differ from the baseline - drift below is still compared,")
            lines.append( "             and may be caused by these rather than by the agent:")
            for f, was, now in conditions:
                lines.append(f"               {f:<13} baseline {_identity_fmt(f, was)}")
                lines.append(f"               {'':<13} now      {_identity_fmt(f, now)}")

    # ---- --rebaseline (build step 6b) --------------------------------------
    # Derived from the record plus run_record.refusal() - the same function
    # the harness decides with, so the report cannot claim a promotion that
    # did not happen.
    marker = header.get("rebaseline")
    if marker is not None:
        why_not = refusal(record)
        if why_not is not None:
            lines.append(f"  REBASELINE REFUSED - {why_not}.")
            lines.append(f"             {where} is unchanged.")
        else:
            was = marker["replaced"] or "none - no baseline file was present"
            lines.append(f"  REBASELINE PROMOTING this record to {where}")
            lines.append(f"             replaces the baseline recorded {was}")
            lines.append(f"             removed {len(marker['removed'])}"
                         + (f": {', '.join(marker['removed'])}" if marker["removed"] else ""))

    # ---- drift ------------------------------------------------------------
    # BUILD STEP 5. Matched by id (src/drift.py). Computed once: the harness
    # passes the Drift it also uses for the exit code, so the report and the
    # verdict cannot give two answers. Computed here only when a caller hands
    # a baseline and no Drift (the checks do).
    if stall is not None:
        lines.append("  DRIFT      not compared - the run is incomplete. Every case after the")
        lines.append("             stall would read as VANISHED, which means \"removed\", not \"stopped\".")
    elif baseline is None:
        lines.append("  DRIFT      none - no baseline to compare against. One is created")
        lines.append("             by an explicit --rebaseline (ADR 0008, 4b).")
    else:
        d = drift if drift is not None else drift_mod.compare(record, baseline)
        identity, _ = drift_mod.header_mismatches(record, baseline)
        if identity:
            # BUILD STEP 7: an IDENTITY mismatch suppresses the comparison -
            # ids still match, so every number would look like real drift.
            lines.append("  DRIFT      SUPPRESSED - an IDENTITY field differs from the baseline, so the")
            lines.append("             comparison could look like drift without being drift (ADR 0008, 4b-ii):")
            for f, was, now in identity:
                lines.append(f"               {f:<20} baseline {_identity_fmt(f, was)}")
                lines.append(f"               {'':<20} now      {_identity_fmt(f, now)}")
            lines.append("             The verdict above stands; only the comparison is withheld.")
            lines.append("             A new baseline is made by an explicit --rebaseline.")
        else:
            lines.append(f"  DRIFT      matched by id: compared {d.compared}  unchanged {d.unchanged}  "
                         f"regressed {len(d.regressed)}  recovered {len(d.recovered)}  "
                         f"detail-moved {len(d.detail_moved)}  tier-moved {len(d.tier_moved)}  "
                         f"new {len(d.new)}  vanished {len(d.vanished)}  "
                         f"unmeasured {len(d.unmeasured)}  redefined {len(d.redefined)}")
            lines.append( "             reported, never gated (ADR 0007, 3b) - except a vanished case")
            if d.out_of_scope:
                lines.append(f"             {d.out_of_scope} baseline row(s) in tiers outside "
                             f"--tier {header['tier_selection']}: not asked for, not compared")

            def diff_lines(case_id: str) -> None:
                for key, (was, now) in d.diffs.get(case_id, {}).items():
                    lines.append(f"        {key:<12} baseline {_fmt(was)}")
                    lines.append(f"        {'':<12} now      {_fmt(now)}")

            for label, ids in (("REGRESSED", d.regressed), ("RECOVERED", d.recovered),
                               ("DETAIL", d.detail_moved)):
                for case_id in ids:
                    lines.append(f"    {label:<9} {case_id}")
                    diff_lines(case_id)
            for case_id, was, now in d.unmeasured:
                lines.append(f"    UNMEASURED {case_id}  {was} -> {now}  (a judge error, not the agent)")
                diff_lines(case_id)
            for case_id, was, now in d.tier_moved:
                lines.append(f"    TIER      {case_id}  {was} -> {now}")
            for case_id in d.new:
                lines.append(f"    NEW       {case_id}  (not in the baseline)")
            for case_id in d.redefined:
                lines.append(f"    REDEFINED {case_id}  its definition changed since the baseline - "
                             f"not compared")
        # OUTSIDE the suppression, always: removing a case moves the suite, and
        # 3b's exit 2 must still be explained when the comparison is withheld.
        for case_id in d.vanished:
            if marker is not None and refusal(record) is None:
                lines.append(f"    REMOVED   {case_id}  retired by --rebaseline, recorded in this "
                             f"record's header")
            else:
                lines.append(f"    VANISHED  {case_id}  in the baseline, absent from this run -> EXIT 2.")
                lines.append( "              Not a regression: removal takes --rebaseline (ADR 0007, 3b).")

    lines.append(f"  record     {record_path}")
    return lines
