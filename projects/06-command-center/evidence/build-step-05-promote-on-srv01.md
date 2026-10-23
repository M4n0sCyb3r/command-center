# P06 build step 5 — promote on SRV01: a true promotion, a false one, the fix, and a proved restore

**Date:** 2026-10-22 (evening) · **Home:** bench + lab · **Playbook:** `playbooks/p06-promote-on-srv01.md`
**Status:** **DONE 2026-10-23.** Session 1 (10-22): run A proved the promotion path on the real
SRV01; run B was a false promotion that exposed a QE blind spot. Session 2 (10-23): the QE check
was fixed in the QE lab (PR #33), and the same bad script was **caught and SRV01 restored and
confirmed by the flow — exit 1**. Done-means met: a clean script promoted, a bad one rejected and
SRV01 restored.

## Picks this session (Jose's, all recommended)

1. **The pair:** `Limit-EventLog` — clean: Application log → 32 MB (no QE check); bad: System
   log → 64 MB (baseline pins 20,971,520).
2. **After exit 0:** `promote` unchanged; the playbook restores a session checkpoint last.
3. **A reviewer block a person judges false:** an **override screen** (ADR 0042 amendment 1) —
   `OVERRIDE <reason>` continues, anything else stops (1); a missing review (2) cannot be
   overridden; the reason is in the record and the promoted evidence.
4. **The restore proof:** run C next session with `Set-Service W32Time -StartupType Manual` (the
   `time` check pins `auto`; `Set-Service` takes `-WhatIf`).
5. **The false promotion:** moved out of `promoted/` into `step-05-runs/false-promotion/` with a NOTE.

## Code that ran (all committed before the runs they served)

| Commit | What |
|---|---|
| `2418a8b` | `-WhatIf` host-stream lines captured (`host_out`); analyzer refuses state-changing cmdlets without `-WhatIf`; the pair; the playbook |
| `e7c6d0b` | the override screen, ADR 0042 am. 1; suite 28, re-baselined 28/28 |
| `8d309d3` | only input typed AFTER a prompt counts (flush type-ahead; re-ask on empty lines) — **run A** |
| `7a773e3` | the same flush before the vault password prompt — **run B** |

## The runs

| # | Script | What happened | Exit |
|---|---|---|---|
| A1 | clean | **no digest, no run** — `ollama serve` was not running (started: `OLLAMA_HOST=0.0.0.0`, 0.40.8, `bench_check` PASS) | 2 |
| A2 | clean | qwen3:14b **REVISE**: *"Uses forward slash for parameter instead of hyphen"* — the line has no slash. ADR 0042 as first decided: stopped, SRV01 untouched | 1 |
| A3 | clean | the same REVISE, word for word; the new override screen read a **leftover newline** (type-ahead) as the decision → stopped | 1 |
| A4 | clean | same REVISE; screen re-asked on empty lines; no text arrived | 1 |
| **A5** | clean | REVISE → Jose: `OVERRIDE false alarm: line 4 uses -MaximumSize, there is no slash` → QE before green → dry run (`What if: … "Application" log`) → checkpoint `p06-promote-af181269-4ef69b` (Standard) → live run → QE after green → **`promoted/promote-clean-applog-af181269/`** | **0** |
| **B** | bad | REVISE (*"Trailing slash causes syntax error"* — none) → Jose: `OVERRIDE false alarm: line 4 has no slash; QE will judge the change itself` → QE before green → checkpoint `p06-promote-2bb632b1-dcee57` → live run → **QE after green** → PROMOTED | **0 — false** |

Records: `step-05-runs/` (every `*-promote-*` and `*-cc-promote-*` record from tonight).

## Findings

1. **The reviewer's false alarms are not hypothetical.** Two clean one-line scripts, two
   hallucinated blocking defects, both repeated word for word (byte-repeatable on 0.40.8). ADR
   0043 measured this (6/8 dev controls); Claude's prediction of exit 0 ignored it. Hence the
   override (am. 1). Two overrides tonight; the records count them.
2. **The canary's sharpest blind spot (run B).** QE passed a script that breaks the baseline.
   SRV01 read three ways right after run B:

   | Read through | System log size |
   |---|---|
   | registry `MaxSize` | 67,108,864 |
   | `Get-EventLog -List` (classic) | 65,536 KB |
   | `Get-WinEvent -ListLog` (modern — **what the QE check reads**, `validate-event-log.yml` l.5) | 20,971,520 |

   `Limit-EventLog` writes the classic registry value; the check reads the modern API. The
   right setting, read through the wrong door. ADR 0042's accepted weakness ("the QE suite only
   sees what it checks") on a real machine. Claude predicted this three-way split correctly.
   Not fixed in this session: the QE lab is unchanged by design (ADR 0042). **Fixed in the QE
   lab the next day** (ADR 0042 am. 2) — session 2 below.
3. **Type-ahead decided twice.** A leftover newline answered the override screen; a password
   typed before its prompt was **echoed** to the terminal, exposing a credential. Fixed:
   every prompt discards input typed before it appears, and **the credential was rotated the
   same night**; its value is in no file or record.
4. **A real Hyper-V restore, proved by reading the machine** (playbook step 4): after
   `Restore-VMSnapshot pre-P06-step5`, all three reads said 20,971,520 / 20,971,520 / 20,480.
   Restoring a Standard checkpoint of a running VM returns it **already Running** (`Start-VM`:
   "already in the specified state").
5. **Two checkpoints had the same name** (`pre-P06-step5` at 20:11:41 and 20:13:12 — a second
   `Checkpoint-VM` the listing did not yet show). Renamed the later to `pre-P06-step5-dup`
   before restoring. The playbook now checks uniqueness first.

## Session 2 — 2026-10-23 (morning)

**Picks (Jose's, recommended):** fix the QE check in the QE lab through its own PR (ADR 0042
amendment 2) — over refusing classic EventLog cmdlets in promote, or documenting only; reviewer:
**re-measure first** on 0.40.8 (development split only — the held-out set stays read once, ADR 0028).

**The QE fix** (`windows-server-qe-validation-lab` PR #33, merged, main `5279ec1`):
`validate-event-log.yml` reads the System log's maximum size three ways (`Get-WinEvent`, registry
`MaxSize`, classic API) and records its result **before** asserting — the second defect: asserting
first sent a real FAIL to `rescue` unrecorded, reported as an incomplete run (exit 2) instead of a
FAIL (exit 1). Before merge: that repo's `run-ci-checks.sh` ALL PASS (harness 24/24); **DC01 34/34**
and **SRV01 30/30**, all three readings 20,971,520 on both (the file is imported by both suites).

**Run B2 — the proof** (session checkpoint `pre-P06-step5-s2`, unique; record
`step-05-runs/20261023T161225Z-promote-promote-bad-syslog-728c90.json`):

| Step | Result |
|---|---|
| review | REVISE — the same hallucinated "trailing slash", word for word; Jose: `OVERRIDE false alarm: line 4 has no slash; QE will judge the change itself` |
| QE before | green, 48/48 (30 checks + 18 scopes) — with the fixed check |
| checkpoint | `p06-promote-2bb632b1-728c90` (09:24:51, Standard), confirmed |
| live run | ok — System log → 64 MB |
| QE after | **red, exit 1** — `Windows Event Log - System` FAIL, *recorded*; later scopes missing (fail-fast) |
| restore | Jose: `Restore-VMSnapshot` (VM already Running) |
| restore-confirm | marker `:pre` read; QE **48/48, identical to the before report** |
| **exit** | **1 — not promoted; SRV01 restored and confirmed** |

The same script that was falsely promoted on 10-22 is caught on 10-23: the fix, the recorded FAIL,
and the flow's restore on real Hyper-V, proved by one run. **The resumed clock did not trip the
`time` check** (confirmation 47 s after the restore, all 48 matching) — the step-4 open question,
answered.

**The reviewer re-measured** (development split, 0.40.8 — held-out stays read once): **17/24 caught,
5/8 false alarms** (was 17/24, 6/8 on 0.40.4) — predicted 15–19 and 5–7. Unchanged. Jose's pick:
keep it + the override; re-selection a named follow-on (ADR 0043 am. 1).

**Also closed:** `svc-agent-ro` rotated on DC01 (PasswordLastSet 2026-10-23 08:44:11); `validate
--server srv01` with the new password → 60 subcategories, exit 0
(`step-05-runs/20261023T185427Z-validate-srv01.json`).

## What was next after session 1 (kept for the record)

Run C: `Set-Service W32Time -StartupType Manual` → QE after fails `time` → RESTORE → marker
`:pre` + QE matches before → **exit 1**. Then step 5 is done. Watch: the resumed clock and the
`time` scope itself (the check that fails is the same scope a resume can disturb).
