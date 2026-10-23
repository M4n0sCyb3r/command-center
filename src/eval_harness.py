"""Agent evaluation harness - the entry point.

Project 4. One command runs golden suites against the agents in this repo,
scores them, and reports pass rate, per-case detail and drift against a pinned
baseline.

BUILD STEP 1 OF 8: the argument surface only. Nothing is executed yet. The
surface is fixed by ADR 0008 (design call 4c) and is deliberately built before
any loader, scorer or report, so that every later step runs through the real
entry point instead of a test shim.

BUILD STEP 4 OF 8 (2026-09-21) moved the reporting into src/report.py, which
renders from the RECORD on disk and prints the baseline's commit and date on
every run (owed by ADR 0008 call 4b). It also closed the empty-tier question:
an empty selection is exit 2, and the reasoning sits on that branch below.

BUILD STEP 3 PART 2c OF 8 (2026-09-21) is where the harness first MEASURES.
run() scores the selected cases through the resolved scorer, writes a run
record, reads it BACK OFF DISK, and derives the verdict from the file - so the
number reported is provably the number a later reader of that record will
compute. Exit 0 and exit 1 are now reachable; they were not before. The report
proper (baseline commit and date, the drift section) is still build step 4.

BUILD STEP 3 PART 2b OF 8 (2026-09-21) gave run() its first real work: it
loads each named suite through suite.parse_suite(), resolves the suite's
declared scorer name through the fixed table in src/scorers.py, and reports
what it found. It still executes NO cases and still returns exit 2, because no
verdict has been established - the case runner and the record write land in
part 2c. Every failure on that path (a malformed suite, an unknown scorer, a
fixtures file handed in by mistake) is a ValueError caught in one place and
turned into exit 2, because ADR 0006, 0007 and 0008 all put those in the same
category: the answer could not be established.

BUILD STEP 2 OF 8 (2026-09-21) touched this file twice: --suite became
required=True (the reasoning sits on the argument itself, below), and that is
all. The record writer landed as src/run_record.py and is NOT wired into run()
yet, on purpose. Wiring it now would mean writing a header whose model name,
model digest, host target and scorer are placeholder strings, because none of
those are resolved until the scorer seam exists at step 3 - and a file in
evals/runs/ that looks like evidence and is not is the exact failure this
project exists to prevent. The writer gets its first caller when step 3
produces rows worth writing.

THE EXIT-CODE CONTRACT (inherited unchanged from the Windows QE lab's
qe_report.py, restated in projects/04-agent-evaluation-harness/README.md):

    0  all gating cases passed        -> promote
    1  a gating case failed           -> validation failure, the agent got worse
    2  the answer could not be established -> tooling error, NOT a verdict

NOTE ON ARGPARSE AND EXIT 2 - this is a coincidence, written down on purpose.
argparse exits 2 on a command-line usage error because that is an old Unix
convention for "you invoked me wrong". Our 2 means "the verdict could not be
established". Two unrelated traditions landed on the same number, and here they
happen to agree: a harness that could not parse its own invocation established
nothing, so 2 is correct either way.

It is written down because the alternative is a future reader seeing exit 2 and
being unable to tell whether it was chosen or inherited. This repo has already
paid for trusting an exit code it had not checked: `auditpol` exits 0 while
printing "a required privilege is not held", which made a privilege problem
arrive disguised as a parser bug in Project 1. An exit code is a convention,
not a guarantee. tests/test_eval_harness.py asserts the agreement rather than
assuming it.
"""

from __future__ import annotations

from dataclasses import asdict, replace

import argparse
import sys

import drift as drift_mod
import report
import run_record
from model_client import ModelError
from run_record import Header, suite_sha256
from scorers import resolve
from suite import case_sha256, header_sha256, parse_suite

# The three outcomes. Named, so that no later step writes a bare integer.
EXIT_PASS = 0
EXIT_REGRESSION = 1
EXIT_TOOLING_ERROR = 2

# Emitted by run() as its first act. The step 1 check asserts this string is
# ABSENT when --tier is omitted, which is how "no suite was executed" is
# observed rather than assumed. Later steps replace the body of run(), but the
# marker stays: it is the boundary between "did not get past the gate" and
# "got past the gate and then failed", and those are different defects.
_REACHED_RUN = "eval_harness: past the argument gate"


def build_parser() -> argparse.ArgumentParser:
    """The command-line surface fixed by ADR 0008, design call 4c.

    Kept separate from main() so it can be inspected without running anything.
    """
    parser = argparse.ArgumentParser(
        prog="eval_harness.py",
        description=(
            "Run golden suites against this repo's agents and report pass rate, "
            "per-case detail and drift against a pinned baseline."
        ),
        epilog=(
            "Exit codes: 0 promote, 1 a gating case failed, "
            "2 the verdict could not be established (tooling error)."
        ),
    )

    # BUILD STEP 2 DECISION: required=True, settled 2026-09-21.
    #
    # Step 1 implemented ADR 0008's asymmetric wording as written (--tier
    # "required with no default", --suite "repeatable and defaultless") rather
    # than tidying it, and left the call to the step that would have a loader -
    # the first code with somewhere to put the error. This is that step.
    #
    # The rejected option was to leave it defaultless and let the loader raise
    # the harness's own tooling error, so that "you named no suite" would share
    # a vocabulary with "that suite file is missing" and "that scorer is
    # unknown". Both options exit 2, so the exit code could not be the reason.
    #
    # The reason it lost: the tooling-error vocabulary is for things discovered
    # WHILE MEASURING - a fixture that is gone, a scorer we do not have, a lab
    # host with no route to it. Every one of those is a fact about the world
    # the harness had to go and look at. "No suite was named" is knowable from
    # the command line before the process touches anything, and routing it down
    # the same channel makes exit 2 mean two things. This repo has ruled three
    # times that the categories stay apart: an unknown scorer is not a failed
    # case (ADR 0006), a vanished case is not a regression (ADR 0007), an
    # incomparable baseline is not a verdict (ADR 0008).
    #
    # The concrete cost of the alternative: it fails AFTER --out may already
    # have been created, leaving a directory and no record behind - the shape
    # that teaches people to ignore stray artifacts.
    parser.add_argument(
        "--suite",
        action="append",
        metavar="PATH",
        required=True,
        default=None,
        help=(
            "Suite file to run. Repeatable. REQUIRED, no default: the harness "
            "never guesses what it is measuring."
        ),
    )

    # ADR 0008, call 4c: REQUIRED, NO DEFAULT. The invocation records the
    # intent. A default is how a workflow edit silently narrows CI, and how a
    # bench habit silently stops exercising the live tier - and once the tier
    # is absent from the invocation, a report showing zero live cases is
    # indistinguishable from a live tier that has quietly drained.
    parser.add_argument(
        "--tier",
        choices=["fixed", "live", "all"],
        required=True,
        help=(
            "Which ground-truth tier to run. REQUIRED, no default. "
            "CI invokes --tier fixed, because CI has no network route to the "
            "lab VMs. A selected-but-unreachable live case is exit 2, never a skip."
        ),
    )

    # Default is derived from --suite, so it cannot be an argparse default.
    # Resolution lands in build step 6, with --rebaseline.
    parser.add_argument(
        "--baseline",
        metavar="PATH",
        default=None,
        help=(
            "Baseline to compare against. Defaults to "
            "evals/baselines/<suite-stem>.json, resolved after parsing."
        ),
    )

    parser.add_argument(
        "--rebaseline",
        action="store_true",
        help=(
            "Promote this run to the committed baseline, recording any removed "
            "cases. The only way a baseline moves (ADR 0008, call 4b)."
        ),
    )

    parser.add_argument(
        "--out",
        metavar="DIR",
        default="evals/runs",
        help="Directory for run records. Default: evals/runs (gitignored).",
    )

    return parser


def verdict(summary: dict) -> int:
    """The exit code a summary implies.

    Split out of run() so it can be checked against hand-built summaries
    without running a suite - the same reason build step 2's totals were
    hand-counted literals rather than computed by the code under test.

    ONLY GATING CASES DECIDE IT (ADR 0006, decision 2): the `live` tier is
    reported and never gates, because its ground truth moves and a moving fact
    reported as a stale expectation is the single confusion that ruling exists
    to prevent. So a run whose live cases all failed still exits 0, and the
    report is what carries them.

    AMENDED 2026-09-26 (agent-loop stall, call B). This docstring used to say
    exit 2 is never produced here, because "a summary exists only because rows
    exist, which means a verdict WAS established". A partial run breaks that
    argument: it has rows AND no verdict. So an incomplete summary is exit 2
    **whatever its rows say** - checked first, before gating_failed, because
    seven passing rows from a run that stalled on case 8 are not a pass. They
    are seven measurements and one unknown, and the unknown could be the
    regression.

    AMENDED 2026-09-27 (judge-error gating, call A, sub-call x). A gating case
    whose judge crashed is UNSCORED - not a failure - and the order below is
    the whole ruling:

      1. incomplete        -> 2  (the server is sick; no row is trusted)
      2. any gating fail   -> 1  (a regression PROVEN without the missing
                                  measurement - each row was judged on its
                                  own, so one judge error does not make the
                                  other rows suspect. Jose: "it proves the
                                  verdict without the missing measurement")
      3. any gating unscored -> 2  (only now could the missing case have
                                  changed the answer)
      4. otherwise         -> 0

    Rejected (y): unscored beats fail. It would turn a proven regression into
    a "flaky, re-run it" signal, which is how regressions get waved through.
    """
    if summary.get("incomplete") is not None:
        return EXIT_TOOLING_ERROR
    if summary["gating_failed"]:
        return EXIT_REGRESSION
    if summary["gating_unscored"]:
        return EXIT_TOOLING_ERROR
    return EXIT_PASS


def run(args: argparse.Namespace) -> int:
    """Run each suite through its scorer, write a record, and report.

    Returns one of EXIT_PASS / EXIT_REGRESSION / EXIT_TOOLING_ERROR.

    BUILD STEP 3 PART 2c: this is where the harness first MEASURES something.
    Each suite is loaded, its scorer resolved and built (which is also where
    that scorer's conditions come from - see scorers.Conditions), the selected
    cases are scored into rows, and a run record is written.

    **The verdict is then derived from the FILE, not from the rows in memory.**
    The record is read back off disk and summarized, so the number reported is
    provably the number a later reader of that file will compute. A verdict
    taken from memory would be a claim ABOUT the evidence rather than a claim
    FROM it, and this project exists because those come apart.

    The report itself - the baseline's commit and date, the drift section - is
    build step 4. What prints here is what this step can honestly produce.
    """
    print(_REACHED_RUN, file=sys.stderr)
    worst = EXIT_PASS

    try:
        for path in args.suite:
            loaded = parse_suite(path)
            factory = resolve(loaded)
            counts = loaded.tier_counts()
            selected = loaded.select(args.tier)

            # ORDER MATTERS HERE, and it was wrong on the first run.
            #
            # resolve() is a dict lookup, so an unknown scorer is still caught
            # before anything expensive. But CALLING the factory is what loads
            # the 160 KB index and reads the model digest off Ollama - so
            # building it before checking whether there is anything to score
            # meant `--tier live` demanded a live model for a run that measures
            # nothing, and failed with a gateway error instead of the honest
            # "you selected a tier with no cases".
            #
            # Found by running it. The last-resort catch added in part 2b
            # turned it into exit 2 with a readable message rather than a
            # traceback and exit 1, which is the whole reason that catch is
            # there. Decide whether there is work, THEN build what the work
            # needs.
            if not selected:
                # SETTLED AT BUILD STEP 4 (2026-09-21), no longer provisional.
                #
                # An empty tier selection is EXIT 2. The reason: "I asked for a
                # tier and it holds no cases" is indistinguishable from "that
                # tier has drained", and ADR 0006 decision 2 exists to make a
                # drained tier LOUD. Exit 0 would make it silent, which is the
                # exact failure that ruling was written to prevent.
                #
                # It also has teeth in a case that will arrive: an all-`live`
                # suite handed to CI selects nothing under --tier fixed, and
                # exit 2 forces someone to either add fixed cases or stop
                # handing that suite to CI. Exit 0 would let CI report a green
                # build over zero gating cases indefinitely.
                #
                # Rejected: exit 0 with a loud report line (a line is only read
                # by whoever is already looking); a fourth status, which the
                # three-outcome contract does not have (ADR 0008, call 4c).
                print(
                    f"eval_harness: --tier {args.tier} selected 0 of "
                    f"{len(loaded.cases)} cases in {loaded.path.name} "
                    f"(fixed={counts['fixed']} live={counts['live']}). Nothing "
                    f"was measured, so no verdict was established - and a "
                    f"selected tier with no cases is indistinguishable from a "
                    f"tier that has drained.",
                    file=sys.stderr,
                )
                return EXIT_TOOLING_ERROR

            # Only now: this is what needs the index and the digest.
            # The factory gets the suite it was resolved from - its own
            # declaration, not a shared context (see build step 3 part 2b).
            # P05 step 7: the tier goes in too, so a factory can tell what the run will
            # TOUCH - a read-only `--tier live` must not demand Ollama, and its record must
            # not name a model it never called.
            scorer, conditions = factory(suite=loaded, tier=args.tier)

            # STAMPED BEFORE THE FIRST CASE (fixed 2026-09-27). Header() used to
            # default started_at to "now" at CONSTRUCTION, and the header is
            # built after every case is scored - so every record until this fix
            # stamped its FINISH time as "started". Found by lining the first
            # captured stall's record up against Ollama's server log: the record
            # said started 20:03:16Z, the server said the stalled request ENDED
            # at 13:03:16 local. A condition field (ADR 0008) wrong on every run.
            started_at = run_record.utc_now_iso()

            # AGENT-LOOP STALL, CALL B (2026-09-26). Until now this was one
            # list comprehension, so a ModelError on case 8 escaped to the catch
            # below and took cases 1-7 with it: exit 2, no record, and no word
            # about WHERE. On 2026-09-24 that cost three runs and every piece of
            # evidence that would have explained them.
            #
            # Now a stall on one case STOPS the run (sub-call (i), Jose: "because
            # it's more efficient that it stops at the first stall" - a wedged
            # server costs 120 s per remaining case, and rows measured after a
            # stall are measured on a server already known to be sick). The rows
            # it has are written, with a marker naming the case, and the verdict
            # below comes out as exit 2 because the RECORD says incomplete.
            #
            # ONLY ModelError. A stall is the model boundary failing; any other
            # exception is a bug in our code, and it keeps the old path - no
            # record, straight to exit 2 - because a partial record from a
            # crashing scorer would be evidence about the scorer, not the agent.
            rows = []
            stalled = None
            for case in selected:
                try:
                    # BUILD STEP 7: the harness stamps each row with its case's
                    # DEFINITION hash - the scorer never sees it.
                    rows.append(replace(scorer(case), case_sha256=case_sha256(case)))
                except ModelError as exc:
                    stalled = {"case_id": case["id"], "error": str(exc),
                               "turn": exc.turn}
                    print(f"eval_harness: STALLED at {case['id']}: {exc}. "
                          f"{len(rows)} of {len(selected)} case(s) scored; the run "
                          f"stops here and no verdict is established.", file=sys.stderr)
                    break

            # BUILD STEP 6b: the baseline is read BEFORE the header is built,
            # because a --rebaseline run writes what it removes INTO its own
            # header (call A) - the copy then stays a copy.
            # read() validates it is a record of a version we understand;
            # a baseline we cannot read is a tooling error, not a verdict.
            where = report.baseline_path(loaded.path, args.baseline)
            pinned = run_record.read(where) if where.exists() else None

            rebaseline = None
            if args.rebaseline:
                removed = None                     # incomplete: not established
                if stalled is None and pinned is None:
                    removed = []                   # the first baseline retires nothing
                elif stalled is None:
                    # THE SAME FUNCTION drift uses for VANISHED, on the rows in
                    # memory - one definition of "gone", scoped to the tiers
                    # this run selected. A second definition here would be a
                    # removal list that disagrees with the drift section.
                    provisional = {"header": {"tier_selection": args.tier},
                                   "cases": [asdict(r) for r in rows]}
                    removed = list(drift_mod.compare(provisional, pinned).vanished)
                rebaseline = {"replaced": pinned["header"]["started_at"] if pinned else None,
                              "removed": removed}

            header = Header(
                suite_path=str(loaded.path),
                suite_sha256=suite_sha256(loaded.path),
                suite_header_sha256=header_sha256(loaded.header),
                tier_selection=args.tier,
                scorer=loaded.header.scorer,
                model_name=conditions.model_name,
                model_digest=conditions.model_digest,
                host_target=conditions.host_target,
                sampling=conditions.sampling,
                rebaseline=rebaseline,
                incomplete=stalled,
                started_at=started_at,
            )
            written = run_record.write(args.out, header, rows)
            stored = run_record.read(written)
            summary = run_record.summarize(stored)
            worst = max(worst, verdict(summary))
            # THE PER-TIER COUNT PRINTS EVERY RUN, from the first version that
            # could print anything (ADR 0006 call 2, and the P04 README's
            # standing requirement that it must not be added later). It is the
            # only thing that makes a tier which has quietly DRAINED visible:
            # without it, zero live cases looks exactly like a live tier that
            # passed.
            # BUILD STEP 4: the report is rendered by src/report.py, which
            # reads the RECORD rather than the rows in memory - the same rule
            # the verdict follows, for the same reason.
            # BUILD STEP 5. Computed ONCE and handed to the report, so the
            # drift section and the exit code are one answer, not two.
            # Not compared when the run is incomplete: every case after the
            # stall would read as VANISHED, and "vanished" means somebody
            # removed a case - the wrong story about a run that just stopped.
            # The exit code is 2 either way; this keeps the report honest.
            complete = stored["header"]["incomplete"] is None
            moved = (drift_mod.compare(stored, pinned)
                     if pinned is not None and complete else None)
            for line in report.render(stored, record_path=written, baseline=pinned,
                                      baseline_file=where, selected=len(selected),
                                      suite_cases=len(loaded.cases), drift=moved):
                print(line)
            # Drift is REPORTED, never gated (ADR 0007, 3b) - with one
            # exception. A case in the baseline and absent from this run is a
            # CONFIGURATION mistake until proven otherwise, so it is exit 2,
            # never exit 1: the pass rate must not be improvable by deleting
            # evidence. The report is printed FIRST so the operator sees which
            # ids vanished; the loop then carries on so every suite is reported.
            # BUILD STEP 6b. The report above has already said PROMOTING or
            # REFUSED (it calls the same refusal()), and printed the OLD
            # baseline's provenance - which is why the copy happens after it.
            # A refused promotion leaves the verdict alone: incomplete and
            # unscored are exit 2 already, and fail+unscored is exit 1 by call
            # (x) - non-zero either way, so no script reads a refusal as success.
            promoted = False
            if args.rebaseline:
                why_not = run_record.refusal(stored)
                if why_not is None:
                    run_record.promote(written, where)
                    promoted = True
                    print(f"eval_harness: baseline written to {where}. It is not a "
                          f"baseline until it is COMMITTED (ADR 0008, 4a).", file=sys.stderr)
                else:
                    print(f"eval_harness: --rebaseline REFUSED for {loaded.path.name}: "
                          f"{why_not}. The baseline at {where} is unchanged.", file=sys.stderr)
            # A promoted run's vanished cases are the removals it recorded in
            # its own header - the explicit act 3b asks for, so not exit 2.
            if moved is not None and moved.vanished and not promoted:
                print(f"eval_harness: {len(moved.vanished)} case(s) in the baseline are "
                      f"absent from this run of {loaded.path.name}: "
                      f"{', '.join(moved.vanished)}. Removing a case takes --rebaseline "
                      f"(ADR 0007, 3b); no verdict is established.", file=sys.stderr)
                worst = max(worst, EXIT_TOOLING_ERROR)
    except (ValueError, OSError, ModelError) as exc:
        # ONE catch for every tooling error, and the reason it is one catch is
        # that this repo has ruled three times that these are the same
        # category: an unknown scorer is not a failed case (ADR 0006), a
        # vanished case is not a regression (ADR 0007), an incomparable
        # baseline is not a verdict (ADR 0008). Exit 1 would claim a case
        # failed, which is a lie about the agent.
        print(f"eval_harness: {exc}", file=sys.stderr)
        return EXIT_TOOLING_ERROR

    # The worst outcome across every suite. A later suite passing does not
    # excuse an earlier one failing, and one exit code has to speak for the
    # whole invocation.
    return worst


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # argparse exits 2 here on a missing or invalid --tier. Nothing below this
    # line runs, which is the property build step 1 exists to establish.
    args = parser.parse_args(argv)

    # THE LAST-RESORT CATCH, added at build step 3 part 2b, and the reason is
    # the exit-code contract rather than tidiness.
    #
    # Python exits 1 on an uncaught exception. In this harness exit 1 already
    # means "a gating case failed - the agent got worse". So without this, any
    # unexpected crash - a scorer returning a dict with no `passed`, an
    # AttributeError in an adapter, a bug we have not thought of - would reach
    # CI as a QUALITY REGRESSION THAT NEVER HAPPENED, and somebody would spend
    # an afternoon looking for it in the agent.
    #
    # This repo has paid for this exact shape before: `auditpol` exits 0 while
    # printing "a required privilege is not held", which made a privilege
    # problem arrive disguised as a parser bug in Project 1. An exit code is a
    # convention, not a guarantee, and two different events must not share one.
    #
    # A crash means the verdict could not be established, which is what 2 is
    # for. SystemExit is a BaseException and so is NOT caught here - argparse's
    # own exit still works exactly as it did.
    try:
        return run(args)
    except Exception as exc:                        # noqa: BLE001 - see above
        print(f"eval_harness: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "eval_harness: this is a TOOLING ERROR, not a failed case - the "
            "verdict could not be established.",
            file=sys.stderr,
        )
        return EXIT_TOOLING_ERROR


if __name__ == "__main__":
    sys.exit(main())
