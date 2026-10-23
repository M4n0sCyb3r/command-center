# ADR 0042 — Project 6: the promotion flow, and what "promoted" means

**Status:** accepted · 2026-10-21 · decided by Jose (Project 6, design call 3)
**Builds on:** ADR 0040 (SRV01 the only write target), ADR 0041 (`promote` is a job in the
table), ADR 0005 (a checkpoint is not a valid undo on a domain controller), ADR 0033 (DC01 out of
scope for writes), the QE lab's exit-code contract (0 pass · 1 validation fail · 2 tooling),
Project 3 (script review), Project 5 (checkpoint first; restore after an undescribed state).

---

## Context

`promote` is the only job that runs a script nobody wrote by hand against a real server. The
2026-09-05 design ended with *"exit 0 on SRV01 → promote to DC01"*. ADRs 0005 and 0033 rule that
out: DC01 is a domain controller, and a checkpoint cannot undo a change there. "Promoted" needs a
meaning that does not include writing to it.

## Options

- **A — Stops at SRV01.** Review → `--check` dry run → checkpoint SRV01 → live run → QE suite on
  SRV01. Exit 0: the script and its evidence are committed to `promoted/` — cleared, with
  evidence, for a person to run in production. Exit 1: restore SRV01, return the script with the
  reason. DC01 is never written by the system.
- **B — Runs on DC01 too.** The original plan.
- **C — No live run.** Review and the dry run only; SRV01 never changes.

## Decision — A

**Jose picked A** (multiple choice, Claude's recommendation marked). Reasons it was offered with:

1. **A real canary run.** The QE suite checks a server the script actually changed — what makes
   "staged validation environments" true.
2. **Production stays a human's decision.** The old plan's own rule was "cleared to TEST, not
   cleared to ship"; A carries it one step further: cleared, with evidence, for a person to ship.
3. **Every write has an undo.** SRV01 is a member server; its checkpoint is a valid undo.

## What this commits

The flow, in order, each step's failure ending the run:

1. **Review** — the reviewer call 4 assigns reads the script; any blocking finding stops the run
   (returned with the findings). No revision loop: Project 3's loop fixed 0 of 36.
2. **Dry run** — `--check` (or `-WhatIf`) against SRV01; a failure stops the run.
3. **Checkpoint** — a named Hyper-V checkpoint of SRV01, confirmed to exist before step 4.
4. **Live run** on SRV01.
5. **QE suite** on SRV01 — the existing framework, unchanged, with its own exit code.
6. **Gate on that exit code:**
   - **0** → commit the script plus evidence to `promoted/<name>/`: review findings, dry-run
     output, QE report before and after, checkpoint name, run record. *Promoted* means exactly
     this and nothing more.
   - **1** → restore the checkpoint, confirm SRV01 back at its baseline, return the script with
     the failing checks. Not promoted.
   - **2** → tooling error or refusal before the live run, not a verdict: nothing promoted; the
     script never ran on SRV01 (a checkpoint may exist). *(Wording corrected at build to match the
     flow as built; once the live run starts, only 0, 1 or 3 are possible.)*
- **DC01 is never written by the system.** A person may run a promoted script in production; that
  act is outside the Command Center and is not automated.
- **The QE suite's baseline is SRV01's** (30 checks, the step-6 subset where `eventhealth` is
  known to fail after a boot — handled in the build, not by loosening the suite).

## Known weaknesses, accepted

- **Checkpoint and restore need an elevated Windows PowerShell** (Hyper-V cmdlets); WSL cannot
  run them. The build decides how — a human-run step the flow waits for and verifies (Project 5's
  playbook pattern), or a narrowly scoped elevated helper. Either way the flow refuses step 4
  until the checkpoint is confirmed.
- **The QE suite only sees what it checks.** A script can pass 30/30 and still do harm the suite
  has no check for — Project 3's "a mechanical check only sees what you planted", at server scale.
  The evidence says *passed these checks*, never *safe*.
- **SRV01 is not a DC.** A script touching AD DS, NTDS or DNS-server settings cannot be rehearsed
  here; the design doc's DC02 escape hatch stays unbuilt until such a script exists, and the flow
  refuses scripts it cannot classify as member-server-safe (how is a build item).

## Rejected

- **B.** A write path to a domain controller with no undo; contradicts ADRs 0005 and 0033.
- **C.** The suite would validate a server the script never touched.

---

## Amendment 1 — a person may override a reviewer block (2026-10-22, P06 build step 5)

**Decided by Jose** (multiple choice, Claude's recommendation marked).

**What happened.** The first real `promote` (step 5, run A) sent a clean one-line script —
`Limit-EventLog -LogName Application -MaximumSize 32MB` — to the reviewer, qwen3:14b. It
answered REVISE with one blocking finding: *"Uses forward slash for parameter instead of
hyphen"*, on a line that has no slash. A hallucinated defect. The flow did what this ADR says:
exit 1, SRV01 untouched. ADR 0043 had measured the cost in advance — false alarms on **6/8**
development controls, 1/4 held-out — and Claude's prediction of exit 0 ignored that number.
Re-running is pointless (the reviewer is byte-repeatable on 0.40.8), and rewording a clean
script until the reviewer passes it is shopping for a verdict.

**Options.** A — a human override at a screen after a REVISE; B — the same as a command-line
flag; C — keep as designed and record it (step 5's restore path then unprovable with this
reviewer); D — re-open ADR 0043 for the reviewer role.

**Decision — A.** After a REVISE, `promote` prints every finding and waits. The person types
`OVERRIDE <reason>` (a reason of at least 8 characters) to continue; anything else — `yes`,
a bare `OVERRIDE`, an empty line — stops the run (exit 1, as before).

- **What it overrides:** a REVISE only. A missing or unreadable review is still exit 2 — a
  review nobody could read approves nothing, and nothing can override it.
- **What it does not change:** QE before, the dry run's refusals, the checkpoint, QE after,
  restore. An overridden script that breaks the server is caught and restored exactly as
  any other (CI case `review-overridden-qe-catches`).
- **What it records:** the review, its findings and the block stay in the run record; the
  record gains `review_overridden: {by: operator, reason}` and a `review-override` step; the
  evidence in `promoted/` carries the same field. A promotion after an override says so.
- **Why a screen, not a flag (B rejected):** an override that can live in a shell history or
  a script is a standing policy, not a decision made at the moment of the finding. Same reason
  Project 5's approval is typed at a screen.
- **C rejected:** with a 6/8 false-alarm reviewer, `promote` would rarely promote; the
  done-means could not be met. **D rejected for now:** a measurement project of its own; it
  stays the honest next move if the overrides pile up (the run records count them).

**Known weakness, accepted.** An operator can override a *true* finding. The defence is the
rest of the flow — QE on a checkpointed canary — and the record, which shows who overrode
what and why. "Passed these checks" never meant "safe" (Known weaknesses above).

---

## Amendment 2 — the QE suite took a fix, through its own repo (2026-10-23, P06 build step 5)

**Decided by Jose** (multiple choice, Claude's recommendation marked).

**What happened.** On 2026-10-22, run B of the step-5 playbook promoted a script that broke
SRV01's baseline: `Limit-EventLog -LogName System -MaximumSize 64MB`. The QE lab's System-log
check read the size through `Get-WinEvent -ListLog` (20 MB); the classic API the script used
writes the registry `MaxSize` (64 MB) — the check could not see the change. Reading the file also
turned up a second defect: it asserted **before** recording its result, so in the fail-fast
suite a real FAIL went to `rescue` unrecorded and surfaced as an incomplete run (exit 2), not a
FAIL (exit 1). This ADR said the QE suite is used "unchanged".

**Options.** A — fix the check in the QE lab, through its own branch and PR; B — refuse the
classic EventLog cmdlets in promote's analyzer; C — document only.

**Decision — A.** `windows-server-qe-validation-lab` PR **#33** (merged 2026-10-23, main
`5279ec1`): `validate-event-log.yml` reads the maximum size three ways — `Get-WinEvent`, the
registry `MaxSize`, the classic API — and all three must match the baseline; the result is
recorded before the assert, like its sibling task files. Verified before merge by that repo's
own `run-ci-checks.sh` (ALL PASS, harness 24/24), DC01 34/34 and SRV01 30/30 (all three readings
20,971,520 on both), and by promote: the same 64 MB script on the same SRV01 → QE after **FAIL**
(exit 1, recorded) → restore → confirmed → exit 1.

**What changes in this ADR:** "the existing framework, unchanged" now reads **unchanged by this
project's code**. promote still calls the QE lab's suite as it ships; a defect found through
promote is fixed *in the QE lab, through its own review*, never patched around in promote.
- **B rejected:** it closes one door and leaves the general weakness — a check reading a
  different source than a script writes — exactly where it was.
- **C rejected:** a canary known to be blind to a class of change it pins is not one to keep.

**Still accepted:** the QE suite only sees what it checks (Known weaknesses above). This fix
closed one blind spot that was found; it does not prove there are no others.
