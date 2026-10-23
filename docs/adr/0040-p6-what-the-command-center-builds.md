# ADR 0040 — Project 6: what the Command Center builds

**Status:** accepted · 2026-10-21 · decided by Jose (Project 6, design call 1)
**Builds on:** ADR 0001 (Project 6 is the integration of 1–5, built last), ADR 0028–0032 and
Project 3's measurements (the drafter/critic/escalation hierarchy), ADR 0031 (no frontier API in
use; the boundary pre-committed), ADR 0033 (DC01 out of scope for anything that writes), ADR 0005
(a checkpoint is not a valid undo on a domain controller), `docs/ai-command-center-plan.md`
(the 2026-09-05 design).

**Who decides in Project 6:** Jose picks each call from options Claude lays out with a
recommendation; Claude writes the code (the Project 3 and 5 arrangement, carried forward).

---

## Context

The syllabus's *Done means*: **a stranger reads the README and understands the system without
asking a question** — plus an architecture diagram, a record of why each model holds the role it
holds, and a ninety-second screen recording. Résumé line: *a hierarchical multi-model AI system
routing work across local and frontier models by task complexity and cost, with staged validation
environments and versioned decision records.*

The design doc was written on 2026-09-05, before any project ran. Its centre is an org chart:
Qwen drafts, Hermes peer-reviews, GPT Codex reviews, Claude signs off "cleared to test", then
<host> runs `--check`, checkpoints SRV01, runs the QE suite and promotes to DC01 on exit 0.

Three things have happened since:

1. **Project 3 measured the local half of that chain** on 36 planted defects with clean controls.
   The critic (hermes3:8b) caught *fewer* defects than the drafter alone (dev 5/24 vs 11/24;
   held-out 5/12 vs 9/12); the revision loop fixed 0; qwen3:14b alone caught 17/24 and 10/12 at
   ~3,400 output tokens per catch against the pipeline's ~17,300. The first gate decided
   everything.
2. **No frontier API is in use** (ADR 0031): Jose has subscriptions, not API accounts or keys.
   Codex and Claude as automated pipeline stages do not exist to call.
3. **ADR 0033 put DC01 out of scope for writes**, and ADR 0005 already said a checkpoint is not an
   undo on a domain controller. "Promote to DC01 on exit 0" is a write to a DC with no undo.

## Options

- **A — Evidence-driven.** One entry point that routes each job to the measured-best path among
  what Projects 1–5 built: validate (P1), ask (P2), review a script (P3 — with the reviewer the
  data supports), remediate (P5), and a staged promotion flow on SRV01 gated by the QE suite and
  the harness (P4). The original org chart is kept, in an ADR, as the design that Project 3
  measured and disproved.
- **B — Build the plan as drawn.** The four-stage chain and DC01 promotion. Needs paid API
  accounts; contradicts Project 3's numbers and ADR 0033.
- **C — Documentation only.** README, diagram, decision records and recording describing P1–P5
  as one system; no new code runs.

## Decision — A

**Jose picked A** from the options as laid out (multiple choice, Claude's recommendation
marked). The reasons it was offered with:

1. **It keeps the measurement.** A capstone that silently rebuilds a design its own Project 3
   disproved would contradict the repo's evidence; one that cites the evidence and changes the
   design is the stronger portfolio story — "designed, measured, redesigned".
2. **It runs.** Unlike C, something new executes end to end, so the README describes behaviour
   that can be demonstrated in the recording, not a diagram of intentions.
3. **It stays inside the boundaries already set** — local models, no API keys (ADR 0031), no
   writes to DC01 (ADR 0033).

## What this commits

- **The Command Center is an orchestrator over existing parts**, not a new agent: it routes a job
  to a project's entry point and records what happened. Each project keeps its own exit-code
  contract; the orchestrator's own contract is a later call.
- **Every role is assigned by measurement where one exists**, and the ADR that assigns it cites
  the number. Where nothing was measured, the role says so.
- **The original org chart is preserved**, verbatim, in the project's decision record as
  *the 2026-09-05 design*, with the Project 3 numbers beside it. It is not deleted from
  `docs/ai-command-center-plan.md`; that doc gains a pointer to this ADR.
- **No frontier model is called** unless a later call crosses ADR 0031's pre-committed boundary
  (allow-list, key from the environment, spend cap first, an ADR amendment).
- **SRV01 is the only write target.** DC01 stays read-only (Project 1's endpoint).

## Known weaknesses, accepted

- **"Routing by complexity and cost" is only partly measured.** Project 3 measured cost per
  catch for script review; other jobs have one path each, so there is nothing to route between
  yet. The README must say which routes are measured and which are single paths.
- **Hermes loses its reviewer role** on Project 3's evidence. Its second role (the vault query
  layer) was not built as such — Project 2 answers with qwen3:8b. Whether Hermes holds any role is
  a later call, decided on evidence.
- **The résumé line overstates frontier routing.** If no frontier tier is wired, the line must be
  reworded at the project close, not kept because it was written first.

## Rejected

- **B.** Builds what the repo's own measurement disproved; needs accounts that do not exist;
  writes to a domain controller with no undo.
- **C.** Meets the letter of the done-means with nothing new that runs; the recording would show
  documents.

## Left for later calls

What a "job" is and the entry point's interface; the promotion flow on SRV01 (what is promoted,
what gates it, what "promote" means when DC01 is read-only); which model holds which role and
how routing is decided; the orchestrator's exit codes and harness entry; the deliverables (README
shape, diagram, recording, and whether a public showcase repo is split out — left open by
ADR 0001).
