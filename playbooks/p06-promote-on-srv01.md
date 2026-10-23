# Playbook — P06: promote on SRV01 (a clean script promoted, a bad one rejected and restored)

**What this proves** (ADR 0042, ADR 0044's bench half): the promotion flow on the real SRV01 —
review, QE before, a parser-gated dry run, a checkpoint a person takes, a live run, QE after —
ends **0** for a clean change and **1** for a bad one, with SRV01 restored and the restore
**proved by reading the machine** (the marker, then QE matching the before report).
**Decided by:** ADRs 0042, 0044; build step 4's picks (person-run checkpoint, vault by pipe,
parser-gated dry run); build step 5's picks (the `Limit-EventLog` pair; this playbook restores a
session checkpoint as its last step — `promote` itself leaves a promoted change in place).

**Fixed 2026-10-23 (QE lab PR #33, ADR 0042 am. 2):** the bad script below is now caught (exit 1,
restored). **Session 1 (2026-10-22) found** the bad script below was PROMOTED — the QE check reads the System
log's size through `Get-WinEvent`, `Limit-EventLog` writes the classic registry value. See
`evidence/build-step-05-promote-on-srv01.md`. Session 2 (2026-10-23) re-ran the same script after
the fix: caught, restored and confirmed (exit 1). That run is the restore proof, so the planned
Run C was not needed.

**The pair** (`projects/06-command-center/samples/`) — the same cmdlet; only SRV01's QE baseline
tells them apart:

| Script | Change | QE sees it? | Expected |
|---|---|---|---|
| `promote-clean-applog.ps1` | Application log → 32 MB | no check reads the Application log | **0** promoted |
| `promote-bad-syslog.ps1` | System log → 64 MB | `eventlog` pins `maximum_size_bytes: 20971520` | **1** not promoted, SRV01 restored |

The bad script *reads* as routine. (In the real runs the reviewer raised blocking findings on
*both* scripts that a person judged false, and the override screen carried them on — ADR 0042
am. 1.) That is the point: review is advice; the canary sees the baseline (ADR 0042).

**Exit codes** — 0 promoted · 1 not promoted, SRV01 in a described state · 2 nothing on SRV01
changed · **3 SRV01 undescribed: stop; restore the run's checkpoint by hand; do not re-run.**

---

## Where each command runs

| Tag | Window |
|---|---|
| **[WSL]** | WSL on <host>, in `<repo>`, venv active |
| **[HOST]** | Windows PowerShell **as Administrator** on <host> (Hyper-V cmdlets) |
| **[OLLAMA]** | its own PowerShell window running `ollama serve` (`sops/bench-startup.md`) |

## 0. Preconditions

1. **[OLLAMA]** one `ollama serve`, left open; `qwen3:14b` present.
2. **[WSL]** `source .venv/bin/activate` and `python tests/run_checks.py --home ci` → all pass.
3. DC01 and SRV01 **Running**; SRV01 not booted in the last hour (`eventhealth` looks back 60 min).
4. **[WSL]** `python tools/p06_bench_check.py` → **ALL HOLD**. This is the canary's health check:
   the marker, the analyzer's verdicts (both scripts of the pair pass), the dry run, QE green.
   If QE is not green here, stop — `promote` would refuse with 2 anyway.
5. The Ansible vault password to hand (asked once per run).

## 1. Session checkpoint

**[HOST]**
```powershell
Checkpoint-VM -Name SRV01 -SnapshotName 'pre-P06-step5'
Get-VMSnapshot -VMName SRV01 | Select-Object Name, CreationTime
```
*Why:* step 4 restores SRV01 at the end of the session, so the promoted Application-log change
does not stay on the canary. Never older than `post-activation-2026-10-21` (expired licence).

## 2. Run A — the clean script → expect 0

**[WSL]**
```
python src/command_center.py promote projects/06-command-center/samples/promote-clean-applog.ps1
```
What you will see, in order: the review (qwen3:14b, ~1 min); the vault prompt; QE before (a few
minutes); the dry run; then **=== CHECKPOINT - your turn ===** with three `[HOST]` lines and a
checkpoint name `p06-promote-<sha8>-<id>`. Run them in **[HOST]**, paste the output of the last
two (it must show the name and `Standard`), then a line `END`. Then the live run and QE after.

**Expect:** exit **0**, `PROMOTED -> .../promoted/promote-clean-applog-<sha8>`.

## 3. Run B — the bad script → expect 1

**[WSL]**
```
python src/command_center.py promote projects/06-command-center/samples/promote-bad-syslog.ps1
```
Same as run A up to QE after. QE after should fail the `eventlog` scope's check (its
exact test name is in the QE lab's `validate-event-log.yml`; the suite is fail-fast, so later
scopes show as missing). Then **=== RESTORE - your turn ===**:
run the three `[HOST]` lines (Restore-VMSnapshot, Start-VM, Get-VM), paste the output (State
must be `Running`), then `END`. The flow waits for SRV01 to answer, reads the marker (must be
`<name>:pre`), re-runs QE, and compares it to the before report check-for-check.

**Expect:** exit **1**, `restored to <name> and confirmed against its before report`.

**If it ends 3:** do not re-run. Note what the message names (the marker? the QE checks that
differ?). Restore the run's checkpoint by hand in **[HOST]**, then go to step 4. A 3 here whose
only difference is `Windows Time Synchronization` is a finding about the resumed clock, not a
failed restore — record it as such.

## 4. Restore the session checkpoint (the operator's rule, step 5 pick)

**[HOST]** — first confirm the name is UNIQUE (found 2026-10-22: a second `Checkpoint-VM` with
the same name made two; `-Name` was then ambiguous). Exactly one line, or rename the extra:
```powershell
Get-VMSnapshot -VMName SRV01 -Name 'pre-P06-step5' | Select-Object Name, CreationTime
```
Then:
```powershell
Restore-VMSnapshot -VMName SRV01 -Name 'pre-P06-step5' -Confirm:$false
Start-VM -Name SRV01            # a Standard restore of a running VM comes back Running: a warning here is fine
Get-VM -Name SRV01 | Select-Object Name, State
```
**[WSL]** `python tools/p06_bench_check.py` → **ALL HOLD** (SRV01 back at baseline; the
Application log back to its old size; the marker says `bench-check:<time>`).

## 5. Evidence

Paste the WSL output of runs A and B (and the [HOST] pastes) into the chat. Claude copies the
run records (`evals/runs/*-promote-*.json`, `*-cc-promote-*.json`) into
`projects/06-command-center/evidence/step-05-runs/`, writes
`evidence/build-step-05-promote-on-srv01.md`, and commits `promoted/` with them.

## What this does not prove

- That a promoted script is *safe* — only that it passed **these** checks (ADR 0042).
- DC01 anything: the system never writes it; a person runs a promoted script in production.
- The recording (step 7) shows these runs; it is not the evidence.
