# ADR 0043 — Project 6: which model holds which role, and why

**Status:** accepted · 2026-10-21 · decided by Jose (Project 6, design call 4)
**Builds on:** ADR 0040 (every role assigned by a cited measurement), ADR 0031 (models per role in
Project 3; the frontier boundary pre-committed), ADR 0009 (qwen3 sampling pinned in code),
Project 2 (answer step), Project 3 (held-out read once), Project 5 (the proposer).

---

## Context

The syllabus asks for *a decision record explaining why each model holds the role it holds*, and
the résumé line says *routing by task complexity and cost*. Five projects produced numbers for
some roles and none for others:

| Role | Measured | Evidence |
|---|---|---|
| Script reviewer | qwen3:14b alone | P3: dev 17/24, held-out 10/12 (read once); ~3,400 output tokens per catch vs the pipeline's ~17,300; false alarms dev 6/8, held-out 1/4 |
| Fix proposer | qwen3:8b | P5: right fix 3/3 (step 4), 8/8 live (step 5), 11/11 on SRV01 (step 8) |
| Answerer | qwen3:8b | P2: answer suite 24/27, every answer traceable to a manifest chunk |
| Hermes (hermes3:8b) | — | P3: as critic dev 5/24, held-out 5/12 — the weakest model arm; vault-query role never built |
| Frontier | — | ADR 0031: no API accounts or keys |

## Options

- **A — One measured model per role.** The table becomes the design; Hermes is recorded as tested
  and dropped; the frontier tier is a documented, closed seam; no cost routing is claimed beyond
  what was measured, and the résumé line is reworded at close.
- **B — A cheap-first cascade.** qwen3:8b reviews first, escalating to qwen3:14b on risk or
  uncertainty — cost routing in shape. Project 3 measured this shape: the first gate decided
  everything.
- **C — Open the frontier tier.** API account, key, spend cap; a frontier reviewer. Crosses
  ADR 0031, costs money, needs a fresh planted-bug set (P3's held-out is spent).

## Decision — A

**Jose picked A** (multiple choice, Claude's recommendation marked). Reasons it was offered with:
every role is backed by a number the README can cite; B rebuilds a shape the repo measured and
found worse; C spends money to answer a question nothing in the course yet requires.

## What this commits

- **Roles:** reviewer `qwen3:14b`; fix proposer `qwen3:8b`; answerer `qwen3:8b`. Each pinned by
  digest in the run record (Project 3's lesson: a family-name lookup once reported the wrong
  model). Sampling per ADR 0009.
- **Hermes holds no role.** The README's model table lists it as *tested in Project 3 as critic
  and dropped*, with the numbers. It is not deleted from Ollama; it is not called.
- **The frontier tier is a seam, closed.** A named place where a frontier reviewer would sit, with
  ADR 0031's conditions to open it (allow-list of what may be sent, key from the environment,
  spend cap configured first, an ADR amendment, and a fresh measurement set).
- **"Routing by cost" is stated as what it is:** roles chosen by measured catch rate and cost per
  catch, once, from evidence — not a per-request router. The résumé line is rewritten at close to
  that, and the old wording recorded as superseded.
- **The reviewer's false-alarm rate is a known cost.** In `promote`, a false alarm stops a clean
  script (safe direction) and sends it back with findings; the README says so.

## Known weaknesses, accepted

- **One measurement per role, from earlier projects.** Models update; the Ollama auto-update has
  moved results before. The run record's digest makes a changed model visible, not re-measured.
- **qwen3:14b's review numbers come from 36 planted defects in 12 scripts.** Real scripts differ;
  the README quotes the set size beside every number.
- **No frontier comparison exists.** The claim is "best of what was measured locally", never
  "best available".

## Rejected

- **B.** Project 3's central finding: in a tiered chain the first gate's misses are final.
- **C.** Money and an ADR 0031 crossing for an unasked question; the held-out set is spent.

---

## Amendment 1 — the reviewer re-measured on Ollama 0.40.8 (2026-10-23, P06 build step 5)

**Decided by Jose** (multiple choice, Claude's recommendation marked).

**Why re-measure.** In step 5, qwen3:14b blocked every script it reviewed — two clean-looking
one-line `Limit-EventLog` scripts, four reviews, each blocked by a hallucinated defect (a slash
that is not on the line), word for word on repeat. The 6/8 false-alarm figure above was measured
on Ollama 0.40.4; the bench has since moved to 0.40.8 (ADR 0046).

**The re-measure** — the same arm, prompt, rubric, schema and budget as Project 3, on the
**development split only** (the held-out set stays read once, ADR 0028); record
`projects/06-command-center/evidence/step-05-runs/20261023T190855Z-p3-escalator-development.json`:

| qwen3:14b (digest `bdbd181c33f2`), P3 development | 0.40.4 (above) | 0.40.8 |
|---|---|---|
| Caught | 17/24 | **17/24** (lint-visible 3/6, logic 9/12, blast-radius 5/6; right reason 14) |
| False alarms on clean controls | 6/8 | **5/8** |
| Errors | — | 0 |

Predicted 15–19 caught, 5–7 false alarms: both inside. **The reviewer did not drift;** its false
alarms are what it is.

**Options.** A — keep qwen3:14b, the override absorbs its false alarms (ADR 0042 am. 1), QE is the
gate; B — re-open this ADR for the reviewer role now; C — make the review advisory (never blocks).

**Decision — A.** qwen3:14b stays the reviewer: still the most catches of any arm measured.
A block now costs a person's judgement at the override screen, which is recorded with its
reason; QE on a checkpointed canary decides (and, since ADR 0042 am. 2, sees the System-log
class it used to miss).
- **B deferred, named:** re-selecting the reviewer (other local models; a prompt change measured
  on development and read once on held-out; PSScriptAnalyzer as the gate — 0/8 false alarms, 6/24
  caught) is a follow-on beyond Project 6, recorded in the project README.
- **C rejected:** a review that can never stop anything puts unread scripts on the live run and
  makes ADR 0042's "review first" decoration.

**Known cost, accepted:** at ~5/8, most clean scripts will meet the override screen. The run
records count overrides; a reader of `promoted/` sees each one with its reason.
