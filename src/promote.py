#!/usr/bin/env python3
"""Promote ONE script through SRV01 - the Command Center's `promote` job. P06 build step 3.

    python src/promote.py path/to/script.ps1

Implements ADR 0042 (the promotion flow; it stops at SRV01) and ADR 0044's promote contract
(0/1/2/3, decided by the run record). Step 3 builds the flow against FAKES (src/fake_bench.py):
CI proves every fork on every push. Step 4 put the real SRV01 behind the same seam:
src/srv01_bench.py (and playbooks/ansible/p06-srv01.yml).

THE FLOW, IN ORDER - each step's failure ends the run
-----------------------------------------------------
  0  read      the file exists, is text, is not empty                     else 2
     place     promoted/<stem>-<sha8>/ does not already exist             else 2
  1  classify  CODE deny-list of domain-controller-only surfaces          hit -> 2
  2  review    the measured reviewer (qwen3:14b, P3's contract)           revise -> 1, no review -> 2
               (a person may OVERRIDE a revise at a screen, with a reason - ADR 0042 am. 1)
  3  QE before the QE suite on SRV01 must be GREEN                        red -> 2
  4  dry run   analyze on SRV01 (refused -> 2), then -WhatIf              fails -> 1
  5  checkpoint created, then CONFIRMED to exist                          else 2
  -- "live-started" is written to the record HERE, before the live run --
  6  live run  the script on SRV01
  7  QE after  0 -> 8; anything else -> restore
  8  promote   the script + evidence written to promoted/<stem>-<sha8>/   -> 0

  restore      restore the checkpoint, re-run the QE suite, and require its per-check results
               to EQUAL the before report. Confirmed -> 1. Anything else -> 3.

WHY EACH BUILD CALL (Jose's picks, 2026-10-21, all recommended)
---------------------------------------------------------------
* Own entry + one seam. promote is a job like the others - a process in JOBS, its own exit
  contract - and every action on the machine goes through ONE object, `Bench`. CI hands it a
  FakeBench; step 4 a real one. The logic CI proves is the logic SRV01 runs (P5's Target).
* QE before, red = 2. A canary that is already failing cannot judge a script: any failure
  after the run could be the script's or the server's. The before report is also what
  "restored" is measured against.
* Restored = restore + QE matches before. Confirmed by READING the machine, not by the
  restore call saying OK - the actor never grades itself (P5's verifier rule). Same checks,
  same results, or it is not confirmed and the code is 3.
* DC-only = a code deny-list, before review. SRV01 is a member server; a script that touches
  AD DS, NTDS or DNS-server settings cannot be rehearsed on it (ADR 0042). Code decides,
  so CI can test it. KNOWN WEAKNESS: a deny-list refuses only what it lists. It is a
  floor, not a classifier - a script that passes it is "not known to be DC-only", never
  "member-server-safe". Pinned in tests/test_promote.py: a script that reaches ANOTHER host
  (Invoke-Command -ComputerName dc01 ...) is not refused - carried to step 4.

EXIT CODES (ADR 0044) - decided by the record, not by reflex
------------------------------------------------------------
  0  promoted: script + evidence in promoted/<stem>-<sha8>/
  1  not promoted, and SRV01 is in a DESCRIBED state: either nothing was changed (review
     blocked, dry run failed) or the checkpoint was restored AND confirmed
  2  tooling or configuration error, or a refusal, BEFORE the live run: nothing on SRV01
     changed (a checkpoint may exist; a checkpoint is not a change to the server)
  3  SRV01 is in an UNDESCRIBED state: restore failed or unconfirmed, or a crash after the
     live run began. Stop. Restore the checkpoint by hand.

Once `live_started` is in the record, 2 is no longer a possible answer - only 0, 1 or 3. A
BenchError after that point (the tool failed, the machine is still reachable) goes through
the restore path; an UNEXPECTED exception (a bug - this code no longer knows where it is) is
3 with no attempt to restore, because a restore driven by code that just crashed is not a
described action.

On 0, SRV01 is left as the script left it: ADR 0042 says nothing about rolling a passing
canary back; the step 5 playbook restores a session checkpoint as its own last step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))          # srv01_bench, review_script
RUNS = REPO / "evals" / "runs"
PROMOTED = REPO / "promoted"

EXIT_PROMOTED, EXIT_NOT_PROMOTED, EXIT_NOTHING_CHANGED, EXIT_UNDESCRIBED = 0, 1, 2, 3
REVIEWER = "qwen3:14b"          # ADR 0043; the JOBS row names it, CI checks they agree
MAX_BYTES = 200_000

# DC-only surfaces (ADR 0042; Jose's pick: a code deny-list). Matched case-insensitively as
# whole words against the script text. A floor, not a classifier - see the docstring.
DC_ONLY = (
    r"ActiveDirectory",            # Import-Module ActiveDirectory, #Requires -Modules ...
    r"ADDSDeployment",
    r"Install-ADDS\w*", r"Uninstall-ADDS\w*", r"Test-ADDS\w*",
    r"dcpromo", r"ntdsutil", r"repadmin", r"dcdiag",
    r"NTDS",                       # HKLM:\...\Services\NTDS, the NTDS.dit database
    r"DnsServer",                  # Import-Module DnsServer
    r"\w+-DnsServer\w*",           # its cmdlets: Set-DnsServer*, Add-DnsServerPrimaryZone ...
    r"\w+-ADObject", r"\w+-ADUser", r"\w+-ADGroup\w*", r"\w+-ADComputer",
    r"\w+-ADOrganizationalUnit", r"\w+-ADDomain\w*", r"\w+-ADForest", r"\w+-GPO\b",
)
_DC_ONLY = re.compile(r"(?<![\w-])(" + "|".join(DC_ONLY) + r")(?![\w])", re.IGNORECASE)


def dc_only_hits(text: str) -> list[str]:
    """Every DC-only token in the script, in order, deduplicated case-insensitively."""
    seen: dict[str, str] = {}
    for m in _DC_ONLY.finditer(text):
        seen.setdefault(m.group(1).lower(), m.group(1))
    return list(seen.values())


# --------------------------------------------------------------------------
# THE SEAM - every action on the machine goes through one of these
# --------------------------------------------------------------------------
class BenchError(Exception):
    """The tool failed (unreachable, cmdlet error, unreadable output). Not a verdict."""


class Refused(BenchError):
    """The dry run REFUSED the script before running it (P06 step 4, Jose's pick 3): it calls
    something -WhatIf cannot stop (a native executable, a .NET static call, a redirection), it
    reaches another machine, or it cannot take -WhatIf at all. A refusal, not a tool failure -
    but like one it ends the run with 2 before anything on SRV01 changed."""


@dataclass(frozen=True)
class Review:
    verdict: str                                     # "approve" | "revise"
    findings: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepResult:
    ok: bool
    output: str = ""


@dataclass(frozen=True)
class QEReport:
    exit_code: int                                   # the QE suite's own 0 / 1 / 2
    checks: dict[str, str]                           # check name -> "pass" | "fail"


class Bench(Protocol):
    target: str                                      # what this bench touches, for the record

    def review(self, script: Path) -> Review: ...           # BenchError = no review
    def qe(self) -> QEReport: ...                            # exit_code 2 = tooling
    def dry_run(self, script: Path) -> StepResult: ...
    def checkpoint(self, name: str) -> None: ...
    def checkpoint_exists(self, name: str) -> bool: ...
    def live_run(self, script: Path) -> StepResult: ...
    def restore(self, name: str) -> None: ...


# --------------------------------------------------------------------------
# THE RUN
# --------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Record:
    """The promote run record: written at every transition, atomically. The exit code is
    decided from what it says (`live_started`), and the file on disk says the same thing."""

    def __init__(self, runs: Path, script: Path, target: str) -> None:
        runs.mkdir(parents=True, exist_ok=True)
        stamp = _now().replace("-", "").replace(":", "")
        self.path = runs / f"{stamp}-promote-{script.stem}-{secrets.token_hex(3)}.json"
        self.data: dict[str, Any] = {
            "kind": "promote-run", "schema": 1, "script": str(script), "script_sha256": None,
            "bench": target, "reviewer": REVIEWER, "started": _now(), "ended": None,
            "state": "started", "live_started": False, "checkpoint": None,
            "steps": [], "qe_before": None, "qe_after": None, "qe_restored": None,
            "promoted_to": None, "exit_code": None, "reason": None,
        }
        self.write()

    def write(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def step(self, step: str, result: str, /, **detail: Any) -> None:
        self.data["steps"].append({"step": step, "result": result, "at": _now(), **detail})
        self.write()

    def end(self, code: int, state: str, reason: str) -> int:
        self.data.update(exit_code=code, state=state, reason=reason, ended=_now())
        self.write()
        return code


def _qe_dict(r: QEReport) -> dict[str, Any]:
    return {"exit_code": r.exit_code, "checks": dict(r.checks)}


Override = Callable[["Review"], "str | None"]
OVERRIDE_WORD = "OVERRIDE"
MIN_REASON = 8


def parse_override(text: str) -> str | None:
    """The reason, iff the person typed OVERRIDE and a reason of at least MIN_REASON characters.
    Anything else - a bare OVERRIDE, 'yes', an empty line - is no override."""
    t = (text or "").strip()
    if not t.startswith(OVERRIDE_WORD + " "):
        return None
    reason = t[len(OVERRIDE_WORD):].strip()
    return reason if len(reason) >= MIN_REASON else None


def flush_typeahead() -> None:
    """Discard keystrokes typed BEFORE a prompt appeared. Found on the first real override
    screen (2026-10-22): a newline left in the terminal buffer - a pasted command's trailing
    newline, or Enter pressed while the review ran - was read as the decision, and the run
    stopped before the operator saw the screen. Only what is typed after the prompt counts.
    No-op when stdin is not a terminal (tests, pipes)."""
    try:
        if sys.stdin.isatty():
            import termios
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except (ImportError, OSError, ValueError, AttributeError):
        pass


def screen_override(review: "Review") -> str | None:
    """THE OVERRIDE SCREEN (P06 step 5, ADR 0042 amendment 1; Jose's pick). Shown after a
    REVISE only. The findings are printed in full; the person decides."""
    print("\n=== THE REVIEWER BLOCKED THIS SCRIPT - your decision ===", file=sys.stderr)
    for f in review.findings:
        print(f"  [{f.get('severity')}] line {f.get('line_start')}-{f.get('line_end')} "
              f"{f.get('category')}: {f.get('why')}", file=sys.stderr)
    print("If a finding is FALSE, type   OVERRIDE <your reason>   and press Enter.\n"
          "The override and your reason go into the run record and the promoted evidence.\n"
          "Anything else stops here (exit 1). qwen3:14b false-alarmed on 6/8 dev controls "
          "(ADR 0043).", file=sys.stderr, flush=True)
    flush_typeahead()
    for _ in range(3):                          # an empty line is not a decision: ask again
        try:
            line = sys.stdin.readline()
        except (OSError, ValueError):
            return None
        if line == "":                          # end of input
            return None
        if line.strip():
            reason = parse_override(line)
            if reason is None:
                print(f"promote: read {line.strip()[:80]!r} - not `OVERRIDE <reason>` "
                      f"(a reason of {MIN_REASON}+ characters). The block stands.",
                      file=sys.stderr)
            return reason
        print("(empty line - type OVERRIDE <reason>, or anything else to stop)",
              file=sys.stderr, flush=True)
    return None


def run(script: Path, bench: Bench, *, runs: Path = RUNS, promoted: Path = PROMOTED,
        override: Override | None = None) -> int:
    """The whole flow. Returns the exit code; never raises past the record.
    `override(review)` is asked only after a REVISE; it returns the person's reason, or None.
    None (the default, and every fake) means a block stands - the design as first decided."""
    try:
        rec = _Record(runs, script, bench.target)
    except OSError as exc:
        print(f"promote: cannot write the run record ({exc}). Nothing was run.", file=sys.stderr)
        return EXIT_NOTHING_CHANGED

    try:
        return _flow(script, bench, rec, promoted, override=override)
    except Exception as exc:                       # the last resort - decided by the record
        live = rec.data["live_started"]
        why = f"crash: {type(exc).__name__}: {exc}"
        try:
            rec.step("crash", "error", error=why[:500])
        except OSError:
            pass
        code = EXIT_UNDESCRIBED if live else EXIT_NOTHING_CHANGED
        try:
            rec.end(code, "undescribed" if live else "not-started", why[:500])
        except OSError:
            pass
        print(f"promote: {why}. " + ("The live run had begun - SRV01 is UNDESCRIBED. Restore "
              f"checkpoint {rec.data['checkpoint']!r} by hand." if live else
              "The live run never started - nothing on SRV01 changed."), file=sys.stderr)
        return code


def _flow(script: Path, bench: Bench, rec: _Record, promoted: Path,
          override: Override | None = None) -> int:
    say = lambda msg: print(f"promote: {msg}", file=sys.stderr)        # noqa: E731

    # 0 read, place
    try:
        raw = script.read_bytes()
        if not raw.strip() or len(raw) > MAX_BYTES:
            raise ValueError("empty" if not raw.strip() else f"over {MAX_BYTES} bytes")
        text = raw.decode("utf-8-sig")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        rec.step("read", "refused", error=str(exc))
        say(f"cannot read {script} as a script ({exc}). Nothing was run.")
        return rec.end(EXIT_NOTHING_CHANGED, "not-started", f"unreadable script: {exc}")
    sha = hashlib.sha256(raw).hexdigest()
    rec.data["script_sha256"] = sha
    dest = promoted / f"{script.stem}-{sha[:8]}"
    if dest.exists():
        rec.step("read", "refused", error=f"{dest.name} already promoted")
        say(f"this exact script is already promoted ({dest}). Nothing was run.")
        return rec.end(EXIT_NOTHING_CHANGED, "not-started", "already promoted")
    rec.step("read", "ok", bytes=len(raw))

    # 1 classify
    hits = dc_only_hits(text)
    if hits:
        rec.step("classify", "refused", dc_only=hits)
        say(f"refused: the script touches domain-controller-only surfaces {hits}. SRV01 is a "
            f"member server and cannot rehearse it (ADR 0042). Nothing was run.")
        return rec.end(EXIT_NOTHING_CHANGED, "refused", f"DC-only: {', '.join(hits)}")
    rec.step("classify", "ok", note="no DC-only token found (a floor, not a classifier)")

    # 2 review
    try:
        review = bench.review(script)
    except BenchError as exc:
        rec.step("review", "error", error=str(exc))
        say(f"no review ({exc}). A review we cannot read approves nothing. Nothing was run.")
        return rec.end(EXIT_NOTHING_CHANGED, "not-started", f"no review: {exc}")
    rec.step("review", review.verdict, findings=review.findings, meta=review.meta)
    overridden: str | None = None
    if review.verdict != "approve":
        # ADR 0042 amendment 1 (P06 step 5, Jose's pick): the reviewer false-alarms (ADR 0043:
        # 6/8 dev controls; on 2026-10-22 it blocked a clean Limit-EventLog line for a slash
        # that was not there). A PERSON may override a REVISE - never a missing review (2) -
        # by typing OVERRIDE and a reason. The block, the findings and the reason all stay in
        # the record; the rest of the flow (QE, dry run, checkpoint, QE after) is unchanged.
        overridden = override(review) if override is not None else None
        if overridden is None:
            rec.step("review-override", "declined")
            say(f"the reviewer blocked it ({len(review.findings)} finding(s)). Not promoted; "
                f"SRV01 was not touched.")
            return rec.end(EXIT_NOT_PROMOTED, "blocked", "reviewer: revise")
        rec.data["review_overridden"] = {"by": "operator", "reason": overridden}
        rec.step("review-override", "overridden", by="operator", reason=overridden)
        say(f"review OVERRIDDEN by the operator: {overridden!r}. Continuing - QE decides.")

    # 3 QE before
    try:
        before = bench.qe()
    except BenchError as exc:
        before = QEReport(2, {})
        rec.step("qe-before", "error", error=str(exc))
    else:
        rec.data["qe_before"] = _qe_dict(before)
        rec.step("qe-before", "green" if before.exit_code == 0 else "red",
                 exit_code=before.exit_code)
    if before.exit_code != 0:
        failing = sorted(k for k, v in before.checks.items() if v != "pass")
        say(f"the QE suite is not green on SRV01 BEFORE the run (exit {before.exit_code}, "
            f"failing {failing}). A red canary cannot judge a script. Nothing was run.")
        return rec.end(EXIT_NOTHING_CHANGED, "not-started",
                       f"QE before exit {before.exit_code}: {failing}")

    # 4 dry run
    try:
        dry = bench.dry_run(script)
    except Refused as exc:
        rec.step("dry-run", "refused", error=str(exc)[:1500])
        say(f"refused by the dry run: {exc}. Nothing was run.")
        return rec.end(EXIT_NOTHING_CHANGED, "refused", f"dry run refused: {str(exc)[:300]}")
    except BenchError as exc:
        rec.step("dry-run", "error", error=str(exc))
        say(f"the dry run could not run ({exc}). Nothing was changed.")
        return rec.end(EXIT_NOTHING_CHANGED, "not-started", f"dry run tooling: {exc}")
    rec.step("dry-run", "ok" if dry.ok else "failed", output=dry.output[:2000])
    if not dry.ok:
        say("the dry run failed. Not promoted; SRV01 was not changed.")
        return rec.end(EXIT_NOT_PROMOTED, "blocked", "dry run failed")

    # 5 checkpoint - created, then confirmed by asking again
    name = f"p06-promote-{sha[:8]}-{rec.path.stem[-6:]}"
    try:
        bench.checkpoint(name)
        exists = bench.checkpoint_exists(name)
    except BenchError as exc:
        rec.step("checkpoint", "error", name=name, error=str(exc))
        say(f"the checkpoint could not be made ({exc}). The live run is refused.")
        return rec.end(EXIT_NOTHING_CHANGED, "not-started", f"checkpoint: {exc}")
    if not exists:
        rec.step("checkpoint", "unconfirmed", name=name)
        say(f"checkpoint {name} was not found after creating it. The live run is refused.")
        return rec.end(EXIT_NOTHING_CHANGED, "not-started", "checkpoint not confirmed")
    rec.data["checkpoint"] = name
    rec.step("checkpoint", "confirmed", name=name)

    # -- the line: from here on, only 0, 1 or 3 ------------------------------------------
    rec.data["live_started"] = True
    rec.data["state"] = "live-started"
    rec.write()                                    # BEFORE the call: write-ahead (P5's rule)

    # 6 live run
    try:
        live = bench.live_run(script)
    except BenchError as exc:
        rec.step("live-run", "error", error=str(exc))
        return _restore(bench, rec, before, f"live run tooling: {exc}")
    rec.step("live-run", "ok" if live.ok else "failed", output=live.output[:2000])
    if not live.ok:
        return _restore(bench, rec, before, "the script failed on SRV01")

    # 7 QE after
    try:
        after = bench.qe()
    except BenchError as exc:
        rec.step("qe-after", "error", error=str(exc))
        return _restore(bench, rec, before, f"QE after tooling: {exc}")
    rec.data["qe_after"] = _qe_dict(after)
    failing = sorted(k for k, v in after.checks.items() if v != "pass")
    rec.step("qe-after", {0: "green", 1: "red"}.get(after.exit_code, "error"),
             exit_code=after.exit_code, failing=failing)
    if after.exit_code != 0:
        return _restore(bench, rec, before,
                        f"QE after exit {after.exit_code}: failing {failing}")

    # 8 promote
    try:
        dest.mkdir(parents=True)
        (dest / script.name).write_bytes(raw)
        evidence = {"script": script.name, "script_sha256": sha, "checkpoint": name,
                    "bench": bench.target, "reviewer": REVIEWER, "review": asdict(review),
                    "review_overridden": ({"by": "operator", "reason": overridden}
                                          if overridden else None),
                    "dry_run": dry.output, "qe_before": _qe_dict(before),
                    "qe_after": _qe_dict(after), "run_record": rec.path.name,
                    "meaning": "cleared, with evidence, for a person to run in production - "
                               "passed THESE checks, not 'safe' (ADR 0042)"}
        (dest / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n",
                                            encoding="utf-8")
    except OSError as exc:
        rec.step("promote", "error", error=str(exc))
        return _restore(bench, rec, before, f"could not write {dest}: {exc}")
    rec.data["promoted_to"] = str(dest)
    rec.step("promote", "ok", to=str(dest))
    say(f"PROMOTED -> {dest}. Cleared, with evidence, for a person to run in production. "
        f"DC01 was not touched.")
    return rec.end(EXIT_PROMOTED, "promoted", "QE green after the live run")


def _restore(bench: Bench, rec: _Record, before: QEReport, why: str) -> int:
    """Restore the checkpoint and CONFIRM it by reading the machine: 1 if confirmed, else 3."""
    name = rec.data["checkpoint"]
    say = lambda msg: print(f"promote: {msg}", file=sys.stderr)        # noqa: E731
    try:
        bench.restore(name)
    except BenchError as exc:
        rec.step("restore", "error", name=name, error=str(exc))
        say(f"{why}; RESTORE FAILED ({exc}). SRV01 is UNDESCRIBED - restore {name} by hand.")
        return rec.end(EXIT_UNDESCRIBED, "undescribed", f"{why}; restore failed: {exc}")
    rec.step("restore", "done", name=name)
    try:
        again = bench.qe()
    except BenchError as exc:
        rec.step("restore-confirm", "error", error=str(exc))
        say(f"{why}; restored, but the QE suite could not confirm it ({exc}). SRV01 is "
            f"UNDESCRIBED - check it, restore {name} by hand.")
        return rec.end(EXIT_UNDESCRIBED, "undescribed", f"{why}; restore unconfirmed: {exc}")
    rec.data["qe_restored"] = _qe_dict(again)
    differs = sorted(k for k in set(before.checks) | set(again.checks)
                     if before.checks.get(k) != again.checks.get(k))
    if again.exit_code != before.exit_code or differs:
        rec.step("restore-confirm", "mismatch", differs=differs, exit_code=again.exit_code)
        say(f"{why}; restored, but SRV01 does NOT match its before report ({differs}). "
            f"UNDESCRIBED - restore {name} by hand.")
        return rec.end(EXIT_UNDESCRIBED, "undescribed", f"{why}; restore unconfirmed: {differs}")
    rec.step("restore-confirm", "matches-before")
    say(f"{why}. Not promoted; SRV01 restored to {name} and confirmed against its before report.")
    return rec.end(EXIT_NOT_PROMOTED, "restored", why)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="promote", description=(
        "Promote one script through SRV01: classify, review, QE before, dry run, checkpoint, "
        "live run, QE after; promoted/ on green, restore on anything else. DC01 is never "
        "written. Exits 0 promoted / 1 not promoted (described) / 2 nothing changed / 3 "
        "UNDESCRIBED - restore by hand."))
    ap.add_argument("script", type=Path)
    ap.add_argument("--runs", type=Path, default=RUNS, help=argparse.SUPPRESS)       # tests only
    ap.add_argument("--promoted", type=Path, default=PROMOTED, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)                                   # bad arguments -> 2
    # There is no --fake flag: a fake reachable from the operator's command line is a
    # promotion nobody ran. Fakes are injected by the CI suite, in-process.
    import srv01_bench                       # lazy: CI's fakes never need it
    return run(args.script, srv01_bench.RealBench(), runs=args.runs, promoted=args.promoted,
               override=screen_override)


if __name__ == "__main__":
    # Run as the module `promote`, not as `__main__`: srv01_bench imports `promote`, and the
    # exceptions it raises (promote.Refused, promote.BenchError) must be the SAME classes the
    # flow catches. Run as __main__, they would be two copies, and a refusal would fall through
    # to the last-resort catch.
    import promote
    sys.exit(promote.main())
