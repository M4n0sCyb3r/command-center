# ADR 0044 — Project 6: the Command Center's exit codes, and how it is tested

**Status:** accepted · 2026-10-21 · decided by Jose (Project 6, design call 5)
**Builds on:** ADR 0041 (child exit codes passed through and recorded), ADR 0042 (the promotion
flow), ADR 0039 (Project 5's exits 0/1/2/3 and its three-way split), ADR 0008/0010 (the harness's
own 0/1/2; three homes), the QE lab's exit-code contract.

---

## Context

Each project already speaks its own contract: the harness and the QE suite 0/1/2; Project 5's agent
adds **3 — undescribed state, restore the checkpoint**. The Command Center sits on all of them, and
`promote` is a new flow that can leave SRV01 changed. Its exit code must not blur those meanings,
and its routing and gate must be proved on every push, not only watched in a recording.

## Options

- **A — Pass through, plus a `promote` contract; tested like Project 5.** Single-project jobs exit
  with the child's code unchanged. `promote`: 0 / 1 / 2 / 3 in Project 5's shape. A gated CI suite
  on fakes; the real SRV01 promotion is a playbook with committed evidence.
- **B — Squash every job to 0/1/2.** Exit 3 folds into 2.
- **C — No harness entry; the recording is the evidence.**

## Decision — A

**Jose picked A** (multiple choice, Claude's recommendation marked), for Project 5's reasons:
"maybe half-changed" must never read as "nothing changed", and a property worth having is proved on
every push.

## What this commits

**Exit codes**

| Job | Code |
|---|---|
| `validate`, `ask`, `review`, `remediate` | the child's code, unchanged (so `remediate` can still return 3) |
| unknown job, bad arguments | **2** before anything runs (argparse/gate 0) |
| `promote` **0** | promoted — script + evidence committed to `promoted/` |
| `promote` **1** | not promoted; SRV01 restored **and confirmed** at its baseline (a described state) |
| `promote` **2** | tooling or configuration error, or a refusal, **before the live run**: the script never ran on SRV01 (a checkpoint and the flow's working folder may exist) |
| `promote` **3** | **SRV01 in an undescribed state** — restore failed or unconfirmed, or a crash after the live run began. Stop; restore the checkpoint by hand |

As in Project 5, the last-resort catch is decided by the record, not by reflex: a crash is 2 only
if the run record shows the live run never started; once it has, it is 3.

**Testing — three homes (ADR 0010)**

- **CI, gated:** a `command-center` suite in `evals/suites/` with a scorer row. Fixed cases on fakes:
  an unknown job refused with nothing run; each child code passed through unchanged (including 3);
  `promote` against a fake SRV01, a fake checkpoint and a fake QE runner — a clean script promoted,
  a script that breaks the fake server not promoted and restored, a reviewer block stopping before
  the dry run, a failing restore ending 3, and a tooling failure before the live run ending 2. A
  case passes iff the exit code and the run record both match.
- **Bench + lab, playbook:** `playbooks/p06-promote-on-srv01.md` — checkpoint, a clean script
  promoted, a deliberately bad script rejected and SRV01 restored, evidence committed.
- **The recording** shows the playbook's runs; it is not the evidence.

## Known weaknesses, accepted

- **Fakes prove the flow's logic, not Hyper-V or the QE suite.** The playbook proves those, once.
- **Pass-through means the caller must know each job's contract.** The README carries one table
  of every code for every job.

## Rejected

- **B.** Erases the one code that tells an operator to restore a checkpoint.
- **C.** Proves the system worked once, while watched.
