"""What a suite file DECLARES, and who is allowed to score it.

Project 4, build step 3 part 2. Until this module existed, a suite file was a
bare list of cases and everything ABOUT it lived in a human's head: which
scorer to run it through, whether a case replays a dated capture or reads a
live host, and whether two cases were the same case. All three are now written
in the file.

THE DECISION THIS FILE IMPLEMENTS (2026-09-21, confirmed explicitly)
--------------------------------------------------------------------
A suite declares its scorer on a HEADER LINE - the first JSON object in the
file, carrying `suite` and `scorer`. Every case line carries its own `id` and
`tier`.

Rejected, and what each would have cost:

  a `scorer` field on every case line - a file could then declare two
      different scorers across its lines, a state with no meaning that the
      loader would have to detect and reject anyway. The header makes that
      state unrepresentable instead of illegal.

  a sidecar evals/suites/<stem>.meta.json - splits one fact across two files,
      and ADR 0008 call 4b(ii) hashes THE SUITE FILE to decide whether two runs
      are comparable. A scorer change in the sidecar would leave the hash
      unmoved, so the harness would compare two runs scored by different code
      and print the difference as drift.

  --scorer on the command line - supersedes ADR 0006 call 1 and makes every
      run's correctness depend on a human pairing suite and scorer by hand,
      which is the defect ADR 0008 call 4c refused when it denied --tier a
      default.

WHAT THE HEADER BOUGHT ON ITS FIRST DAY
---------------------------------------
Writing it exposed a defect that had been sitting in evals/suites/agent.jsonl
since Lab 10: the file held 13 runnable agent cases AND 8 judge-calibration
fixtures, which are not cases at all - they carry `candidate_facts` and a human
`label`, have no `expected_tools`, and exist to measure the comparator. Handing
them to the agent scorer would have produced 8 rows whose `passed` meant
nothing, inflating the pass rate while looking like a measurement - the same
vacuous-field defect part 1 refused when it declined to write passed=True on
unanswerable retrieval cases.

They now live in evals/fixtures/judge-calibration.jsonl, which declares
`"scorer": null` and is therefore NOT RUNNABLE: the harness must refuse it.
A file is not a suite, and the file could not say so until it had a header.

THE `scorer` NAME IS NOT AN IMPORT PATH. It is a key into a fixed table
defined in code (ADR 0006 call 1, taken on security grounds: a data file must
not be able to choose which code executes, and Project 2 will ingest documents
we did not author). This module reads the name and validates nothing about it -
resolving it is build step 3 part 2b's job, and an unknown name is a TOOLING
ERROR there, never a failed case.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# One vocabulary, one definition. run_record already owns ("fixed", "live") and
# validates it structurally when a record is read, so it is the home; importing
# it here is cheaper than moving it and leaves nowhere for the two to disagree.
from run_record import TIERS

# The key that makes the first line a header rather than a case. A case line
# carrying it is rejected, so "two headers" is also unrepresentable.
HEADER_KEY = "suite"

# What --tier accepts. A superset of TIERS by exactly one member: `all` is a
# SELECTION and never a case's tier, because ADR 0006 call 2 requires every
# case to say which of the two kinds of ground truth it has.
TIER_SELECTIONS = ("fixed", "live", "all")


# ---------------------------------------------------------------------------
# BUILD STEP 7, call C (Jose, 2026-09-28: "because it hides anything that could
# lie" - sharpened: it hides exactly what could lie, and nothing else).
#
# Identity at TWO levels, both CANONICAL (sorted keys, fixed separators), so a
# reordered file or reformatted line hashes the same - step 5 proved a reorder
# is zero drift, and a byte hash would have called it "not comparable".
#
#   header_sha256  what applies to EVERY case: scorer, vocabulary. A mismatch
#                  suppresses the whole drift section.
#   case_sha256    one case's definition. A mismatch marks THAT case REDEFINED
#                  and leaves the other twelve compared.
#
# EXCLUDED, and each exclusion is a ruling:
#   header `description` - prose; it changes neither what is asked nor how it
#                          is scored.
#   case `id`, `tier`    - the SPINE keys. `id` is the match key itself; a tier
#                          change is already reported per case as tier-moved.
# Everything else in a case is IN - deliberately a deny-list of spine keys, not
# an allow-list of "meaningful" ones, because an allow-list would be the agent
# scorer's vocabulary in shared code (the leak this build has caught 3 times).
# ---------------------------------------------------------------------------
CASE_IDENTITY_EXCLUDES = ("id", "tier")
HEADER_IDENTITY_EXCLUDES = ("description",)


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def case_sha256(case: dict[str, Any]) -> str:
    """The identity of one case's DEFINITION: everything but its id and tier."""
    return hashlib.sha256(_canonical(
        {k: v for k, v in case.items() if k not in CASE_IDENTITY_EXCLUDES})).hexdigest()


def header_sha256(header: "SuiteHeader") -> str:
    """The identity of what a suite applies to every case."""
    body = {"suite": header.suite, "scorer": header.scorer,
            "vocabulary": header.vocabulary}
    assert not set(body) & set(HEADER_IDENTITY_EXCLUDES)
    return hashlib.sha256(_canonical(body)).hexdigest()


@dataclass(frozen=True)
class SuiteHeader:
    """What the file says about itself.

    `scorer=None` means the file is not a runnable suite. That is a deliberate
    third state rather than a missing key: a fixtures file has to be loadable
    by the module that uses it (score.py reads the calibration pairs) while
    being refused by the harness, and the only honest way to express that is to
    declare it.
    """

    suite: str
    scorer: str | None
    description: str = ""
    # The scorer's declared ANSWER SPACE (P04 scorer defect 2). This module
    # checks only that it is a JSON object: what the keys MEAN belongs to the
    # scorer that reads them (score.check_vocabulary), for the same reason
    # summarize() may touch only the spine. Excluded from hash/compare because
    # a dict cannot be hashed and a header is compared by the fields above.
    vocabulary: dict[str, Any] | None = field(default=None, compare=False, hash=False)

    @property
    def runnable(self) -> bool:
        return self.scorer is not None


@dataclass(frozen=True)
class Suite:
    """A parsed suite file: what it declares, and its cases as raw dicts.

    Cases stay dicts here on purpose. retrieval.py turns them into its frozen
    Case; score.py works in dicts. This module owns the rules both must agree
    on - a header, a unique id, a known tier - and nothing about what a case
    MEANS, which belongs to whichever scorer the header names.
    """

    path: Path
    header: SuiteHeader
    cases: list[dict[str, Any]]

    @property
    def ids(self) -> list[str]:
        return [case["id"] for case in self.cases]

    def _require_runnable(self, what: str) -> None:
        """A file declaring no scorer has no scored cases, so it has no tiers.
        Asking it for one is a mistake in the CALLER, not bad data - so it
        raises here rather than returning a zero that reads like a measurement."""
        if not self.header.runnable:
            raise ValueError(
                f"{self.path.name} declares scorer=null and is not a runnable "
                f"suite - {what} is meaningless for it"
            )

    def tier_counts(self) -> dict[str, int]:
        """Cases per tier. ADR 0006 call 2 requires this on the face of every
        report, so that a tier which has quietly drained is visible rather than
        being reported as a tier that simply passed."""
        self._require_runnable("a tier count")
        return {tier: sum(1 for c in self.cases if c["tier"] == tier) for tier in TIERS}

    def select(self, tier_selection: str) -> list[dict[str, Any]]:
        """The cases a --tier selection runs. `all` means both tiers.

        Returns them in FILE ORDER, which is not the same claim as comparing
        them in file order - ADR 0007 matches cases to a baseline by `id` and
        never by position, because a reordered file would otherwise report as a
        wave of regressions.
        """
        self._require_runnable("a tier selection")
        if tier_selection not in TIER_SELECTIONS:
            raise ValueError(
                f"tier selection must be one of {TIER_SELECTIONS}, got {tier_selection!r}"
            )
        if tier_selection == "all":
            return list(self.cases)
        return [c for c in self.cases if c["tier"] == tier_selection]


def _fail(path: Path, lineno: int, message: str) -> None:
    """Every rejection names the file and the line. A suite is DATA, and bad
    data must say where it is - inherited from the two loaders this replaces."""
    raise ValueError(f"{path.name} line {lineno}: {message}")


def parse_suite(path: str | Path) -> Suite:
    """Read a suite file: one header object, then one case object per line.

    Raises ValueError on anything malformed. Every raise here is a TOOLING
    ERROR at the harness's level (exit 2) and never a failed case - ADR 0006
    call 1's ruling on unknown scorers, applied to the file that names them.
    """
    path = Path(path)
    lines = [
        (lineno, stripped)
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if (stripped := raw.strip())
    ]
    if not lines:
        raise ValueError(f"{path.name} is empty - it declares no scorer and holds no cases")

    # ---- the header -------------------------------------------------------
    header_lineno, header_line = lines[0]
    try:
        raw_header = json.loads(header_line)
    except json.JSONDecodeError as exc:
        _fail(path, header_lineno, str(exc))
    if not isinstance(raw_header, dict) or HEADER_KEY not in raw_header:
        # The load-bearing rejection. A pre-header suite file's first line is a
        # CASE, and parsing it as a header would produce a suite declaring no
        # scorer and silently holding 13 cases instead of 14.
        _fail(path, header_lineno,
              f"the first line must be a header object declaring {HEADER_KEY!r} and 'scorer'")
    if "scorer" not in raw_header:
        _fail(path, header_lineno,
              "header must declare 'scorer' - use null for a file that is not a runnable suite")

    suite_name = raw_header[HEADER_KEY]
    scorer = raw_header["scorer"]
    if not isinstance(suite_name, str) or not suite_name:
        _fail(path, header_lineno, f"{HEADER_KEY!r} must be a non-empty string")
    if scorer is not None and (not isinstance(scorer, str) or not scorer):
        _fail(path, header_lineno, "'scorer' must be a non-empty string or null")

    vocabulary = raw_header.get("vocabulary")
    if vocabulary is not None and not isinstance(vocabulary, dict):
        _fail(path, header_lineno, "'vocabulary' must be a JSON object if present")

    header = SuiteHeader(suite=suite_name, scorer=scorer,
                         description=raw_header.get("description", ""),
                         vocabulary=vocabulary)

    # ---- the cases --------------------------------------------------------
    cases: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for lineno, line in lines[1:]:
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            _fail(path, lineno, str(exc))
        if not isinstance(case, dict):
            _fail(path, lineno, "a case must be a JSON object")
        if HEADER_KEY in case:
            _fail(path, lineno,
                  f"a case may not carry {HEADER_KEY!r} - a file declares exactly one header")

        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            _fail(path, lineno, "every case needs a non-empty string 'id'")
        if case_id in seen:
            # Not pedantry. ADR 0007 matches a run against its baseline BY ID,
            # so two cases sharing one id make the comparison ambiguous: one of
            # them silently decides the verdict and the other is invisible.
            _fail(path, lineno, f"duplicate id {case_id!r} - first seen on line {seen[case_id]}")
        seen[case_id] = lineno

        # THE TIER RULE IS CONDITIONAL ON WHAT THE FILE DECLARED, and the
        # condition is the point rather than a convenience. A tier says whether
        # a case's ground truth is a dated capture or a live reading, which
        # exists so the harness knows whether the case GATES. A file declaring
        # scorer=null is never scored by the harness, so its lines have no tier
        # to give - and writing "tier": "fixed" on them to satisfy a parser
        # would be a field that means nothing, which is the vacuous-field
        # defect that moved them out of the suite in the first place.
        if header.runnable:
            tier = case.get("tier")
            if tier not in TIERS:
                # No header-level default, and that is the decision. A default
                # means a case which forgot its tier inherits `fixed` and
                # therefore GATES - so a moving fact would be reported as a
                # stale expectation, which is the single confusion ADR 0006
                # call 2 exists to prevent. The cost is one field per line,
                # paid in full.
                _fail(path, lineno,
                      f"every case needs 'tier' - one of {TIERS}, got {tier!r}")
        elif "tier" in case:
            # And the reverse, so the rule cannot be honoured by accident: a
            # file that is not scored must not carry a field that only means
            # something when it is.
            _fail(path, lineno,
                  "this file declares scorer=null, so its entries are not scored "
                  "and must not carry 'tier'")

        cases.append(case)

    if not cases:
        raise ValueError(f"{path.name} holds no cases - an empty suite passes everything")
    return Suite(path=path, header=header, cases=cases)
