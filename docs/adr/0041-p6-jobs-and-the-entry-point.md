# ADR 0041 — Project 6: what a job is, and how one reaches the right part of the system

**Status:** accepted · 2026-10-21 · decided by Jose (Project 6, design call 2)
**Builds on:** ADR 0040 (an orchestrator over P1–P5), ADR 0006 (the scorer table: code named in a
fixed dict, never chosen by a data file or a model), Lab 3 (the model proposes, the code
disposes), Lab 11 (blast radius; gate 0), each project's own exit-code contract.

---

## Context

ADR 0040 made the Command Center an orchestrator that sends each job to its measured-best path.
Before roles, gates or exit codes can be decided, something has to decide **which part of the
system handles a request** — and one of those parts (remediate, and the promotion flow) writes to
SRV01.

## Options

- **A — A fixed job table.** The operator names the job. A table in code maps each job name to
  one project's entry point; the Command Center runs it, and records the job, the route and the
  child's exit code. No model routes.
- **B — A model as front door.** qwen3 reads a plain-English request and picks the job, limited
  to the same table by an enum. Needs its own routing measurement; a wrong pick can reach the
  write path.
- **C — Promotion only.** No jobs; the Command Center is one flow (draft → review → canary →
  gate). P1, P2 and P5 stay outside the system the README describes.

## Decision — A

**Jose picked A** (multiple choice, Claude's recommendation marked). Reasons it was offered with:

1. **The operator already knows the job.** There is nothing for a model to propose, so a model
   there adds a failure mode and no capability — Lab 3's rule, applied to routing.
2. **A wrong route cannot happen silently.** A job name not in the table is refused before
   anything runs (gate 0, as Lab 11 refused an unknown tool).
3. **It keeps every project's contract intact.** The child's exit code is passed through and
   recorded, not reinterpreted.

## What this commits

- **`JOBS`** — one fixed dict in code, job name → entry point, read by both the CLI's argument
  check and the dispatcher (one source, Lab 3's registry rule). Proposed starting set, confirmed
  in the build: `validate` (P1), `ask` (P2), `review` (P3), `remediate` (P5), `promote` (the
  flow decided in call 3).
- **Each job runs its project's own entry point as a process**, so the child's exit code is
  observed exactly as the project defined it (the reason Project 4 put process checks in
  `tests/`).
- **Every run writes one record**: job, route, the command it ran, the child's exit code, start
  and end times, and the child's own evidence paths. The Command Center adds no verdict of its
  own on top of a child's.
- **A job whose entry point cannot yet take an operator's input** gets a thin adapter in the
  build, not a change of meaning: `review` today runs only Project 3's planted set, and Project 1
  has no question-answering CLI. Those adapters are build items, and each says what it adds.
- **A plain-English front door (B) is not ruled out** — it may be added on top later, only with
  its routing accuracy measured, and never as the only way to reach `remediate` or `promote`.

## Known weaknesses, accepted

- **"Routing by complexity and cost" is not in this call.** Job selection is by name; any routing
  by cost happens *inside* a job (call 4).
- **Process boundaries cost start-up time** per job (model loads, PSRP connects). Accepted: the
  record and the exit-code contract matter more than seconds here.

## Rejected

- **B.** A routing model with no measurement in front of the only path that writes.
- **C.** Leaves three of five projects outside the system the done-means asks a stranger to
  understand.
