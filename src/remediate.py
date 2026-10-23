"""Detect, apply one fix, verify, roll back (Project 5, build step 2).

Every decision in this module is CODE (ADR 0037). The model proposes a Call in
step 4; the operator approves in step 3; until then both arrive as arguments,
so CI can drive every path with no model and no screen.

One fix, one approval, one attempt (ADRs 0036, 0038):

    before  the VERIFIER reads every watched value
    preview the remediator says what it would change   - refused?  -> `refused`
    approve the operator answers; only a literal True  - anything else -> `declined`
    apply   the remediator changes the machine and REPORTS (recorded, not trusted)
    verify  the verifier re-reads until the three rules hold or the ceiling is hit
    rollback (only if not verified) - the before-state, no second approval
    verify the rollback: every watched value back to its before-read
                                                       -> `rolled-back` or `rollback-failed`

"Verified" (ADR 0036), from verifier reads ONLY:
  1. the drifted item now equals its baseline;
  2. what changed on that item is exactly what the approved preview said would change;
  3. every other watched item equals its before-read.
A verifier that cannot answer has verified nothing: an error or the ceiling is a failure.
The fix is never retried.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import remediation_gate as gate
from audit import AuditError, AuditLog, MemoryAudit, sha256
from target import ApplyReport, Call, RemediationRefused, Target, Watched

OUTCOMES = ("verified", "rolled-back", "rollback-failed", "declined", "refused")

# Settling (ADR 0036): re-read every INTERVAL seconds, give up after CEILING.
# Guesses until SRV01 measures them (step 7/8); committed constants, not knobs.
# Too short false-fails a good fix (it rolls back - safe, noisy); too long slows every run.
SETTLE: dict[str, tuple[float, float]] = {
    "service":  (1.0, 30.0),   # a service start/stop is the slow one
    "audit":    (0.5, 5.0),
    "registry": (0.5, 5.0),
}


class TargetUnreachable(RuntimeError):
    """The verifier could not read the machine BEFORE anything was done. Tooling error."""


@dataclass(frozen=True)
class Drift:
    """One watched item that differs from the baseline, as the verifier read it."""

    item: str
    cls: str
    name: str
    baseline: dict[str, Any]
    observed: dict[str, Any]
    read_at: float

    @property
    def fields(self) -> list[str]:
        return sorted(f for f in self.baseline if self.observed.get(f) != self.baseline[f])


def detect(target: Target, baseline: list[Watched], *, clock: Callable[[], float] = time.time
           ) -> list[Drift]:
    """Every watched item that differs from the baseline. Code, not the model (ADR 0037)."""
    found = []
    for w in baseline:
        try:
            seen = target.reader.read(w.id)
        except Exception as exc:
            raise TargetUnreachable(f"cannot read {w.id}: {exc}") from exc
        if seen != w.fields:
            found.append(Drift(w.id, w.cls, w.name, dict(w.fields), seen, clock()))
    return found


@dataclass
class Result:
    """What happened to one fix. Everything the audit log (step 3) will need."""

    outcome: str
    drift: Drift
    call: Call
    preview: list = field(default_factory=list)
    before: dict[str, dict[str, Any]] = field(default_factory=dict)
    after: dict[str, dict[str, Any]] = field(default_factory=dict)
    report: ApplyReport | None = None
    rollback_report: ApplyReport | None = None
    reasons: list[str] = field(default_factory=list)    # why NOT verified, in rule order
    reads: int = 0                                      # verifier reads spent settling
    screen: str = ""                                    # exactly what the approver was shown
    answer: Any = None                                  # what the approver returned
    audit_incomplete: bool = False                      # an audit write failed AFTER apply

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {self.outcome!r}")


def check_rules(drift: Drift, preview: list, before: dict, after: dict) -> list[str]:
    """ADR 0036's three rules. Returns the failures; empty means verified."""
    fails = []
    item = drift.item
    if after[item] != drift.baseline:
        fails.append(f"rule 1: {item} is {after[item]} not baseline {drift.baseline}")
    promised = {c.field: c.after for c in preview}
    expected = {**before[item], **promised}
    changed = {f for f in after[item] if after[item][f] != before[item].get(f)}
    promised_changes = {c.field for c in preview if c.before != c.after}
    if after[item] != expected or changed != promised_changes:
        fails.append(f"rule 2: {item} changed {sorted(changed)} to {after[item]}; the preview "
                     f"promised {sorted(promised_changes)} to {expected}")
    moved = sorted(k for k in before if k != item and after.get(k) != before[k])
    if moved:
        fails.append(f"rule 3: also moved {moved}")
    return fails


def _read_all(target: Target, ids: list[str]) -> dict[str, dict[str, Any]]:
    return {i: target.reader.read(i) for i in ids}


def _settle(target: Target, ids: list[str], test: Callable[[dict], list[str]], cls: str,
            clock: Callable[[], float], sleep: Callable[[float], None]
            ) -> tuple[dict, list[str], int]:
    """Re-read until `test` passes or the ceiling for `cls` is reached.

    Returns (last reads, failures, reads spent). A read that raises is a failure for this
    attempt and stays one if the ceiling arrives: fail closed (ADR 0036).
    """
    interval, ceiling = SETTLE[cls]
    deadline = clock() + ceiling
    # A second, clock-free bound: a clock that stalls (or a test clock that never moves)
    # must not hang the agent. Found in build step 3 - a constant clock looped forever.
    max_reads = int(ceiling / interval) + 2
    reads, last, fails = 0, {}, ["no read completed"]
    while True:
        reads += 1
        try:
            last = _read_all(target, ids)
            fails = test(last)
        except Exception as exc:
            fails = [f"verifier error: {type(exc).__name__}: {exc}"]
        if not fails or clock() >= deadline or reads >= max_reads:
            return last, fails, reads
        sleep(interval)


def _fields(obj: Any) -> dict[str, Any]:
    """An approver's answer as audit fields. A bool (CI's scripted approver) is recorded as-is."""
    if isinstance(obj, bool) or obj is None:
        return {"returned": obj}
    keep = ("approved", "answer", "seconds", "hasty", "drained", "reason")
    if any(hasattr(obj, k) for k in keep):
        return {k: getattr(obj, k) for k in keep if hasattr(obj, k)}
    return {"returned": repr(obj)}


def remediate(target: Target, baseline: list[Watched], drift: Drift, call: Call,
              approve: Callable[..., Any], *, audit: AuditLog | None = None,
              rationale: str | None = None, proposer: str = "model",
              clock: Callable[[], float] = time.monotonic,
              sleep: Callable[[float], None] = time.sleep,
              pause: Callable[[], Any] | None = None) -> Result:
    """One drift, one proposed call, one approval, one attempt - every decision on the record.

    Audit (ADR 0038): a failed write BEFORE apply raises AuditError and nothing is applied
    (the caller exits 2). A failed write AFTER apply does not stop verification or rollback -
    stopping there would leave an unverified change - but the result is marked incomplete.
    """
    log = audit if audit is not None else MemoryAudit()
    ids = [w.id for w in baseline]
    try:
        before = _read_all(target, ids)
    except Exception as exc:
        raise TargetUnreachable(f"cannot read before-state: {exc}") from exc
    res = Result("refused", drift, call, before=before)

    # --- before apply: every write must land, or nothing happens ------------------------
    def finish(outcome: str) -> Result:
        res.outcome = outcome
        log.write("outcome", "code", outcome=outcome, reasons=res.reasons)
        return res

    log.write("drift", "code", item=drift.item, baseline=drift.baseline,
              observed=drift.observed, read_at=drift.read_at)
    log.write("proposal", proposer, function=call.function, item=call.item,
              values=call.values, rationale=rationale)
    if call.item != drift.item:
        res.reasons.append(f"the call targets {call.item}, the drift is {drift.item}")
        return finish("refused")
    try:
        res.preview = target.remediator.preview(call)
    except RemediationRefused as exc:
        res.reasons.append(f"remediator refused: {exc}")
        return finish("refused")
    log.write("preview", "code", changes=[[c.field, c.before, c.after, c.effect]
                                          for c in res.preview])

    host = target.reader.host()
    fp = gate.fingerprint(host, drift.item, before[drift.item], call, res.preview)
    res.screen = gate.render(host=host, drift=drift, call=call, preview=res.preview,
                             before=before[drift.item], n_watched=len(ids),
                             settle=SETTLE[drift.cls], fp=fp)
    screen_hash = sha256(res.screen)
    log.write("screen", "code", text=res.screen, sha256=screen_hash, fingerprint=fp, host=host)

    res.answer = approve(screen=res.screen, drift=drift, call=call, preview=res.preview)
    approved = res.answer is True or getattr(res.answer, "approved", None) is True   # Lab 13
    log.write("answer", "human", screen_sha256=screen_hash, **_fields(res.answer))
    if not approved:
        return finish("declined")

    # Lab 13's state-bound approval: the yes was to THIS state. Re-read before acting.
    try:
        now = _read_all(target, ids)
    except Exception as exc:
        now = {"error": f"{type(exc).__name__}: {exc}"}
    same = now == before
    log.write("recheck", "code", unchanged=same)
    if not same:
        res.reasons.append("the machine changed after the screen was drawn; the answer was "
                           "to a state that no longer exists")
        return finish("declined")

    log.write("intent", "code", function=call.function, item=call.item, values=call.values,
              approved_screen=screen_hash)          # WRITE-AHEAD: durable before the change

    # --- after apply: keep going, but say so if the record breaks -----------------------
    def note(event: str, who: str, **f: Any) -> None:
        if res.audit_incomplete:
            return
        try:
            log.write(event, who, **f)
        except AuditError as exc:
            res.audit_incomplete = True
            res.reasons.append(f"audit incomplete after apply: {exc}")
            try:
                log.write("audit_incomplete", "code", error=str(exc))
            except AuditError:
                pass

    try:
        res.report = target.remediator.apply(call)
        note("applied", "code", before=res.report.before,
             reported_after=res.report.reported_after, trusted=False)
    except Exception as exc:
        # It may have half-applied. Treat as not verified: undo toward the VERIFIER'S
        # before-reads (the only before-state we hold), then check that.
        res.reasons.append(f"apply raised {type(exc).__name__}: {exc}")
        res.report = ApplyReport(call=call, before=dict(before[call.item]), reported_after={})
        note("applied", "code", error=f"{type(exc).__name__}: {exc}")
        fails = ["apply raised"]
    else:
        if pause is not None:
            # ADR 0035, SRV01 half of the sabotage: BENCH-ONLY. The change has been applied
            # and not yet verified; a person may now change the machine from outside the
            # agent. What they say they did is recorded as theirs (who: human) - the
            # verifier's reads that follow are what decide, not this note.
            t0 = clock()
            said = pause()
            note("pause", "human", operator_note=str(said or "")[:500],
                 paused_seconds=round(clock() - t0, 1))
        res.after, fails, res.reads = _settle(
            target, ids, lambda a: check_rules(drift, res.preview, before, a), drift.cls,
            clock, sleep)
        res.reasons.extend(fails)
    note("verify", "code", ok=not fails, reasons=fails, reads=res.reads)
    if not fails:
        res.outcome = "verified"
        note("outcome", "code", outcome="verified", reasons=[])
        return res

    # Not verified: roll back at once, no second approval (ADR 0036), then verify THAT.
    try:
        res.rollback_report = target.remediator.rollback(res.report)
        note("rollback", "code", restore={f: res.report.before[f] for f in call.values})
    except Exception as exc:
        res.reasons.append(f"rollback raised {type(exc).__name__}: {exc}")
        note("rollback", "code", error=f"{type(exc).__name__}: {exc}")
    restored, rfails, n = _settle(
        target, ids, lambda a: [f"rollback: {k} is {a.get(k)} not {before[k]}"
                                for k in ids if a.get(k) != before[k]], drift.cls, clock, sleep)
    res.reads += n
    res.after = restored or res.after
    res.reasons.extend(rfails)
    note("rollback_verify", "code", ok=not rfails, reasons=rfails, reads=n)
    res.outcome = "rollback-failed" if rfails else "rolled-back"
    note("outcome", "code", outcome=res.outcome, reasons=res.reasons)
    return res
