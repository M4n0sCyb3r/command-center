"""The remediation agent's command line (Project 5, build step 5; ADR 0039).

ONE FIX PER RUN (Jose's pick, build step 5)
-------------------------------------------
The agent detects every drift, fixes the FIRST one in baseline order, and names the rest as
"not touched this run". So the exit code always describes exactly one fix, and every fix has
its own audit log - the evidence unit step 8's playbook commits. Rejected: loop and stop at the
first failure (exit 1 no longer says which fix; one log mixes several); loop all, exit = worst
(keeps changing a machine right after the verifier was surprised by it - ADR 0036 says stop).

EXIT CODES (ADR 0039) - the harness's own 0/1/2 contract is unchanged
---------------------------------------------------------------------
  0  the fix held (`verified`), or there was nothing to fix
  1  nothing the agent did stands; the machine is in a DESCRIBED state
     (`rolled-back`, `refused`, `declined`)
  2  tooling or configuration error; NOTHING WAS APPLIED
  3  the machine is in an UNDESCRIBED state - restore from the checkpoint (`rollback-failed`)

THE LAST-RESORT CATCH IS NOT exit 2 BY REFLEX. Every earlier tool in this repo maps an
unexpected crash to 2, "the verdict could not be established". Here 2 also PROMISES that nothing
was applied - and after the write-ahead `intent` line that promise can be false. So a crash is
2 only if the audit log holds no `intent`; once it does, the machine may have changed and nobody
verified it, which is exactly what 3 means. The audit log decides, not a guess.

    python src/remediation_agent.py --target fake --drift service:Spooler start_type=automatic status=running
    python src/remediation_agent.py --target fake --drift audit:Logon "setting=No Auditing" --sabotage broken

`--target` has no default for the reason `--tier` has none: the invocation records the intent.
`--drift` and `--sabotage` are the fake's console - they act on the fake only.

SRV01 (build step 8 - the done-means run, playbooks/p05-break-three-ways.md)
---------------------------------------------------------------------------
    python src/remediation_agent.py --target srv01
    python src/remediation_agent.py --target srv01 --pause-before-verify

Two sessions, two accounts, two endpoints (ADR 0034): the VERIFIER reads as svc-agent-ro through
AgentReader, the FIXER writes as svc-agent-rw through AgentRemediator. Both passwords are asked
with getpass, held in memory only, and both endpoints' command lists are drift-checked before
anything is read. On SRV01 the drift is planted BY A PERSON at the console - the agent has no
--drift there, because breaking the machine is not the agent's job.

`--pause-before-verify` is ADR 0035's SRV01 sabotage and is BENCH-ONLY: after the fix is applied
and before the verifier reads, the agent stops and waits for Enter, so the operator can undo the
fix at the console. What the operator types is recorded as a `pause` line, who: human.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import audit as A                                       # noqa: E402
import proposer as P                                    # noqa: E402
import remediate as R                                   # noqa: E402
import remediation_gate as gate                         # noqa: E402
import target as T                                      # noqa: E402
import tracing                                          # noqa: E402

EXIT_VERIFIED, EXIT_NOTHING_STANDS, EXIT_TOOLING, EXIT_UNDESCRIBED = 0, 1, 2, 3
EXIT_FOR = {"verified": EXIT_VERIFIED, "rolled-back": EXIT_NOTHING_STANDS,
            "refused": EXIT_NOTHING_STANDS, "declined": EXIT_NOTHING_STANDS,
            "rollback-failed": EXIT_UNDESCRIBED}
SABOTAGE = {"none": False, "broken": "broken", "broken-rollback": "broken-rollback"}


class UsageError(ValueError):
    """The command line asked for something that cannot be set up. Exit 2, nothing touched."""


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="remediation_agent.py",
        description="Detect drift, propose one fix, ask, apply, verify, roll back if needed.",
        epilog="Exit codes: 0 the fix held (or no drift) - 1 nothing the agent did stands - "
               "2 tooling error, nothing applied - 3 UNDESCRIBED STATE: restore from checkpoint.")
    ap.add_argument("--target", required=True, choices=["fake", "srv01"],
                    help="REQUIRED, no default. fake = in-memory; srv01 = the lab VM, two "
                         "passwords asked.")
    ap.add_argument("--drift", action="append", nargs="+", default=[],
                    metavar="ARG",
                    help="Fake only: ITEM FIELD=VALUE [FIELD=VALUE ...] - break ITEM before "
                         "the agent looks (repeatable).")
    ap.add_argument("--sabotage", choices=sorted(SABOTAGE), default="none",
                    help="Fake only: broken = the fix lies; broken-rollback = the undo lies too.")
    ap.add_argument("--pause-before-verify", action="store_true",
                    help="SRV01 only, BENCH-ONLY: stop after the fix is applied and before the "
                         "verifier reads, so the operator can undo it at the console (ADR 0035).")
    ap.add_argument("--out", default=str(REPO_ROOT / "evals" / "runs"),
                    help="Where the audit log goes. Default evals/runs (gitignored).")
    return ap


def _value(text: str) -> Any:
    """`value=0` is the integer 0 - the registry baseline holds ints, and "0" != 0."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def plant(host: T.FakeHost, specs: list[list[str]]) -> None:
    for spec in specs:
        item, pairs = spec[0], spec[1:]
        if not pairs or any("=" not in p for p in pairs):
            raise UsageError(f"--drift {' '.join(spec)}: give ITEM FIELD=VALUE [FIELD=VALUE ...]")
        try:
            host.set(item, **{k: _value(v) for k, v in (p.split("=", 1) for p in pairs)})
        except KeyError:
            raise UsageError(f"--drift: {item!r} is not a watched item") from None


def open_srv01(stack: contextlib.ExitStack) -> T.Target:
    """Both SRV01 doors, each drift-checked against its own list. HostError -> unreachable."""
    import host_client as H
    baseline = T.load_baseline()
    ro = getpass.getpass(f"Password for {T.VERIFIER_ACCOUNT} (verifier, read-only): ")
    rw = getpass.getpass(f"Password for {T.REMEDIATOR_ACCOUNT} (fixer, writes): ")
    try:
        reader = stack.enter_context(H.Host(T.SRV01_SERVER, T.VERIFIER_ACCOUNT, ro,
                                            configuration_name=T.VERIFIER_ENDPOINT,
                                            allowed=H.ALLOWED_SRV01))
        reader.check_drift()
        fixer = stack.enter_context(H.Host(T.SRV01_SERVER, T.REMEDIATOR_ACCOUNT, rw,
                                           configuration_name=T.REMEDIATOR_ENDPOINT,
                                           allowed=H.ALLOWED_REMEDIATOR))
        fixer.check_drift()
    except H.HostError as exc:
        raise R.TargetUnreachable(str(exc)) from exc
    finally:
        del ro, rw
    print(f"SRV01 {T.SRV01_SERVER}: verifier {T.VERIFIER_ACCOUNT} @ {T.VERIFIER_ENDPOINT}, "
          f"fixer {T.REMEDIATOR_ACCOUNT} @ {T.REMEDIATOR_ENDPOINT} - both endpoint lists checked")
    return T.Target(reader=T.SrvReader(reader, baseline),
                    remediator=T.SrvRemediator(fixer, baseline), kind="srv01")


def console_pause(read: Callable[[str], str] = input) -> str:
    """Wait for a DELIBERATE resume. Found on the bench 2026-10-21: twice the pause resumed on
    an empty Enter before the operator had touched SRV01, so the verifier read an untouched fix
    and the sabotage landed after the run had ended. An empty line now re-prompts; the note
    (what you did, and when) is the evidence ADR 0035 asks for, so it is required."""
    print("\n*** PAUSED - the fix is APPLIED and NOT YET VERIFIED (bench-only sabotage) ***")
    print("    1. Make your change at the SRV01 console now, as an administrator.")
    print("    2. Then type what you did and the time, and press Enter. The verifier reads next.")
    print("    (To resume with NO change, type: none)")
    while True:
        try:
            said = read("operator> ").strip()
        except EOFError:
            return ""
        if said:
            return said
        print("    empty line ignored - the verifier has NOT read yet. Type what you did, or 'none'.")


def _console_approver() -> Callable[..., Any]:
    ask = gate.console_asker()
    return lambda **k: ask(k["screen"])


def run(args: argparse.Namespace, *, approve: Callable[..., Any] | None = None,
        generate: P.Generate | None = None, state: dict[str, Any] | None = None,
        **clocks: Any) -> int:
    state = state if state is not None else {}
    baseline = T.load_baseline()
    if args.target == "srv01":
        if args.drift or args.sabotage != "none":
            raise UsageError("--drift and --sabotage act on the fake only; on SRV01 a person "
                             "breaks the machine at the console (playbook)")
        tgt = state["srv"]
    else:
        if args.pause_before_verify:
            raise UsageError("--pause-before-verify is SRV01's sabotage; the fake's is "
                             "--sabotage broken")
        tgt, host = state.get("fake") or T.fake_target(baseline, broken=SABOTAGE[args.sabotage])
        plant(host, args.drift)
    if args.pause_before_verify:
        clocks = {**clocks, "pause": state.get("pause") or console_pause}

    drifts = R.detect(tgt, baseline)
    if not drifts:
        print(f"at baseline: {len(baseline)} watched item(s) on {tgt.reader.host()}, no drift. "
              "Nothing to fix.")
        return EXIT_VERIFIED
    drift, rest = drifts[0], drifts[1:]
    print(f"drift: {drift.item} {drift.observed} (baseline {drift.baseline})")
    for d in rest:
        print(f"  not touched this run: {d.item} - one fix per run; run again for it")

    run_id = tracing.new_run_id()
    log = A.AuditLog(run_id, path=Path(args.out) / f"{run_id}.audit.jsonl")
    state["audit_path"] = log.path
    res, prop = P.run_one(tgt, baseline, drift, approve or _console_approver(), audit=log,
                          generate=generate or P.default_generate, **clocks)

    call = f"{prop.call.function} {json.dumps(prop.call.values, sort_keys=True)}" \
        if prop.call else "-"
    print(f"proposed: {call}   right fix: {P.right_fix(prop.call, drift)}")
    print(f"OUTCOME: {res.outcome}")
    for r in res.reasons:
        print(f"  {r}")
    print(f"audit log: {log.path}")
    if res.audit_incomplete:
        print("WARNING: the audit log is INCOMPLETE after the change - see its last line")
    code = EXIT_FOR[res.outcome]
    if code == EXIT_UNDESCRIBED:
        print("ROLLBACK FAILED: the machine is in an undescribed state. "
              "RESTORE FROM THE CHECKPOINT. Do not re-run the agent against it.")
    return code


def _intent_written(path: Path | None) -> bool:
    if path is None or not Path(path).exists():
        return False
    try:
        return any(ln.get("event") == "intent" for ln in A.read_log(path))
    except Exception:
        return True          # a log we cannot read cannot prove nothing was applied


def main(argv: list[str] | None = None, *, approve: Callable[..., Any] | None = None,
         generate: P.Generate | None = None, fake: tuple[Any, Any] | None = None,
         clock: Callable[[], float] | None = None,
         sleep: Callable[[float], None] | None = None,
         srv_open: Callable[[contextlib.ExitStack], T.Target] | None = None,
         pause: Callable[[], Any] | None = None) -> int:
    """`clock`/`sleep` are injectable so CI need not wait out a 30 s settling ceiling;
    `srv_open`/`pause` so CI can drive the SRV01 path against scripted sessions."""
    args = build_parser().parse_args(argv)          # argparse exits 2 itself
    state: dict[str, Any] = {"fake": fake} if fake else {}
    if pause is not None:
        state["pause"] = pause
    clocks = {k: v for k, v in (("clock", clock), ("sleep", sleep)) if v is not None}
    try:
        with contextlib.ExitStack() as stack:
            if args.target == "srv01" and not (args.drift or args.sabotage != "none"):
                state["srv"] = (srv_open or open_srv01)(stack)
            return run(args, approve=approve, generate=generate, state=state, **clocks)
    except (UsageError, T.BaselineError, R.TargetUnreachable, P.ModelUnavailable) as exc:
        print(f"remediation_agent: {type(exc).__name__}: {exc}. Nothing was applied.",
              file=sys.stderr)
        return EXIT_TOOLING
    except A.AuditError as exc:
        # remediate() raises AuditError only BEFORE apply - after apply it notes and carries on.
        print(f"remediation_agent: the audit log could not be written ({exc}). Fail-closed: "
              "nothing was applied.", file=sys.stderr)
        return EXIT_TOOLING
    except Exception as exc:                        # noqa: BLE001 - see the module docstring
        if _intent_written(state.get("audit_path")):
            print(f"remediation_agent: unexpected {type(exc).__name__}: {exc} AFTER the intent "
                  "line - the change may have been applied and was not verified. "
                  "RESTORE FROM THE CHECKPOINT.", file=sys.stderr)
            return EXIT_UNDESCRIBED
        print(f"remediation_agent: unexpected {type(exc).__name__}: {exc}. No intent line was "
              "written, so nothing was applied.", file=sys.stderr)
        return EXIT_TOOLING


if __name__ == "__main__":
    sys.exit(main())
