"""Drift: what changed between this run and the pinned baseline, case by case.

BUILD STEP 5 OF 8 (2026-09-23). Specified by ADR 0007 call 3b and the P04
README's build order: match by `id`, never position; a case present in the
baseline and absent from this run is a TOOLING ERROR (exit 2), not a
regression.

WHAT IS COMPARED - design call confirmed 2026-09-23 (option B)
-------------------------------------------------------------
The spine (`id`, `tier`, `result` - `passed` until record v4) PLUS a single uninterpreted question about
`detail`: is it identical to the baseline's? Jose's reason: "a spine-only
comparison can't see a case that still passes but got worse - a retrieval
question slipping from rank 1 to rank 2 - and comparing the detail as a whole
dict means the drift code never has to know what any key means (`rank`,
`top_score`, `trajectory`), so adding P02's scorer will not require changing
it."

Rejected: A, spine only (blind to the rank slip, which is exactly the movement
MRR exists to see); C, a comparator per scorer (a new seam method for one
real need). This module may name the spine and NOTHING ELSE - the check greps
its source for scorer vocabulary, because the same leak has already happened
three times (summarize() at step 3, calibrate() at 2b, report at step 4).

A detail diff is still generic: it walks the union of keys and prints the
ones whose values differ, both sides verbatim. It never asks what a key means.

RULINGS THIS MODULE IMPLEMENTS
------------------------------
* By `id`, never by position. A reordered suite is zero drift.
* Duplicate ids in EITHER record are a ValueError. Building a dict from them
  would silently keep the last one, and a baseline can be hand-edited.
* VANISHED is scoped to the tiers THIS run selected. A `live` case in the
  baseline is not "vanished" from a `--tier fixed` run - it was not asked
  for. Those rows are counted as `out_of_scope` and PRINTED, so the scoping is
  visible rather than silent.
* `recovered`, not `fixed`, for fail -> pass. "fixed" is already a TIER name in
  this repo, and one word meaning two things is the defect this build has
  caught three times.
* Drift is REPORTED, never gated (ADR 0007, 3b). The one exception, vanished,
  is decided by the caller (eval_harness), not here: this module describes.

CARRIED TO STEP 7 - HONOURED THERE (header_mismatches() is a separate function,
and tests/test_comparability.py case 7 checks it). An ordering constraint: removing
a case from a suite ALWAYS changes the suite's sha256, and step 7 suppresses
drift on an identity mismatch. So the vanished check must run BEFORE and
INDEPENDENTLY of that suppression, or 3b's exit 2 could never fire. compare()
deliberately reads nothing from either header except tier_selection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from run_record import TIERS

SELECTED_TIERS = {"fixed": frozenset({"fixed"}),
                  "live": frozenset({"live"}),
                  "all": frozenset(TIERS)}


@dataclass(frozen=True)
class Drift:
    compared: int                                  # ids present in both
    unchanged: int
    regressed: tuple[str, ...] = ()                # result pass -> fail
    recovered: tuple[str, ...] = ()                # result fail -> pass
    detail_moved: tuple[str, ...] = ()             # result same, detail differs
    # JUDGE-ERROR GATING (2026-09-27). The result changed and one side is
    # "unscored": (id, was, now). NEITHER a regression nor a recovery - a
    # pass -> unscored is the judge failing, not the agent getting worse, and
    # filing it under `regressed` would be the v3 bug reappearing in drift.
    unmeasured: tuple[tuple[str, str, str], ...] = ()
    tier_moved: tuple[tuple[str, str, str], ...] = ()   # (id, was, now)
    new: tuple[str, ...] = ()                      # in this run only
    vanished: tuple[str, ...] = ()                 # in baseline, selected tier, absent now
    # BUILD STEP 7 (call C): in both, but the case's DEFINITION changed - its
    # question, expectation or tools. NOT compared: a result change here is a
    # different question's answer, not the agent moving. Only this case is
    # withheld; the others still compare.
    redefined: tuple[str, ...] = ()
    out_of_scope: int = 0                          # baseline rows in tiers not selected
    diffs: dict[str, dict[str, tuple[Any, Any]]] = field(default_factory=dict)

    @property
    def any(self) -> bool:
        return bool(self.regressed or self.recovered or self.detail_moved
                    or self.tier_moved or self.new or self.vanished
                    or self.unmeasured or self.redefined)


def _by_id(record: dict[str, Any], which: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in record["cases"]:
        if r["id"] in out:
            raise ValueError(f"{which} record has duplicate case id {r['id']!r}; "
                             f"cases are matched by id, so drift cannot be computed")
        out[r["id"]] = r
    return out


_MISSING = "<absent>"


def detail_diff(was: dict[str, Any], now: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """Keys whose values differ, both sides verbatim. Generic by construction."""
    keys = sorted(set(was) | set(now))
    return {k: (was.get(k, _MISSING), now.get(k, _MISSING))
            for k in keys if was.get(k, _MISSING) != now.get(k, _MISSING)}


def compare(record: dict[str, Any], baseline: dict[str, Any]) -> Drift:
    """Compare this run's record to the baseline, matching cases by id."""
    now = _by_id(record, "this run's")
    was = _by_id(baseline, "the baseline")
    selection = record["header"]["tier_selection"]
    if selection not in SELECTED_TIERS:
        raise ValueError(f"unknown tier_selection {selection!r} in this run's record")
    in_scope = SELECTED_TIERS[selection]

    regressed, recovered, detail_moved, tier_moved, unmeasured, redefined = [], [], [], [], [], []
    diffs: dict[str, dict[str, tuple[Any, Any]]] = {}
    unchanged = 0
    both = [i for i in now if i in was]            # this run's order, for stable output
    for case_id in both:
        a, b = was[case_id], now[case_id]
        if a.get("case_sha256") != b.get("case_sha256"):
            redefined.append(case_id)              # withheld - see Drift.redefined
            continue
        d = detail_diff(a.get("detail", {}), b.get("detail", {}))
        if d:
            diffs[case_id] = d
        if a.get("tier") != b.get("tier"):
            tier_moved.append((case_id, a.get("tier"), b.get("tier")))
        # By EQUALITY with named values, never truthiness - see run_record.
        ra, rb = a.get("result"), b.get("result")
        if ra == "pass" and rb == "fail":
            regressed.append(case_id)
        elif ra == "fail" and rb == "pass":
            recovered.append(case_id)
        elif ra != rb:                              # one side is unscored
            unmeasured.append((case_id, ra, rb))
        elif d:
            detail_moved.append(case_id)
        elif a.get("tier") == b.get("tier"):
            unchanged += 1

    new = [i for i in now if i not in was]
    absent = [i for i in was if i not in now]
    vanished = [i for i in absent if was[i].get("tier") in in_scope]
    out_of_scope = len(absent) - len(vanished)

    return Drift(compared=len(both), unchanged=unchanged,
                 regressed=tuple(regressed), recovered=tuple(recovered),
                 detail_moved=tuple(detail_moved), tier_moved=tuple(tier_moved),
                 unmeasured=tuple(unmeasured), redefined=tuple(redefined),
                 new=tuple(new), vanished=tuple(vanished),
                 out_of_scope=out_of_scope, diffs=diffs)


# ---------------------------------------------------------------------------
# BUILD STEP 7 - the HEADER half of ADR 0008 call 4b(ii). Separate from
# compare() on purpose: compare() reads no header field but tier_selection, so
# the VANISHED check (exit 2) can never be switched off by an identity mismatch
# - removing a case always moves the suite, and 3b's exit 2 must still fire.
#
#   IDENTITY   the drift section is SUPPRESSED; the field and both values print
#   CONDITION  the drift section is shown and ANNOTATED with both values
#
# Neither touches the exit code: the gating cases ran, so a verdict WAS
# established. `sampling` is a condition since record v5 (6a). Not here:
# started_at (always differs; the report prints the baseline's date anyway),
# suite_sha256 (bytes - a reorder moves it; provenance only), tier_selection
# (drift already scopes by it and prints out_of_scope).
# ---------------------------------------------------------------------------
IDENTITY_FIELDS = ("suite_header_sha256", "scorer")
CONDITION_FIELDS = ("model_name", "model_digest", "host_target", "sampling")


def header_mismatches(record: dict[str, Any], baseline: dict[str, Any]
                      ) -> tuple[list[tuple[str, Any, Any]], list[tuple[str, Any, Any]]]:
    """(identity, condition) mismatches as (field, baseline value, this run's value)."""
    now, was = record["header"], baseline["header"]

    def diff(fields: tuple[str, ...]) -> list[tuple[str, Any, Any]]:
        return [(f, was.get(f), now.get(f)) for f in fields if was.get(f) != now.get(f)]
    return diff(IDENTITY_FIELDS), diff(CONDITION_FIELDS)
