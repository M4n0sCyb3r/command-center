# Project 3 — Script Review Pipeline

A local model drafts a PowerShell change; a critic scores it against a written
rubric and returns structured findings, not prose; a revision loop runs to a hard cap; what the
critic cannot clear escalates to a stronger model. Every stage records its cost and latency.

**Status:** **complete** (2026-10-18 → 2026-10-19) — design calls 1–5 (ADRs 0028–0032, 3 amendments to 0031/0032); build steps 1–8 shipped; held-out read once · build order 4th
(ADR 0001) · Phase II.

**Done means:** a seeded-bug test set where the pipeline catches defects the drafter alone
misses, with the cost delta measured.

**Résumé line:** Designed a multi-stage, role-specialized agent pipeline (draft → automated
critique → revision → escalation) for infrastructure code review, with per-stage cost and
latency instrumentation.

**Who decides:** Jose picks each design call from options Claude lays out; Claude writes the code.

## Why it is built fourth

ADR 0001: its *Done means* is a harness run plus Lab 12 traces. Built before Project 4, it would
have shipped its own scorer — a second source of truth.

## Decisions

| ADR | Decision |
|---|---|
| `docs/adr/0028-p3-what-the-pipeline-is-measured-on.md` | **Call 1** — measured on **reference scripts with one planted defect each, plus clean controls**; every arm reads identical files; defects, classes, locations and code oracles committed before any reviewer reads them; a held-out split from day one. Rejected: defects planted into live drafts (the key moves with the model); natural defects only (no answer key without an uncalibrated judge). |
| `docs/adr/0029-p3-language-defects-and-split.md` | **Call 2** — **PowerShell only**, with PSScriptAnalyzer as a zero-model baseline arm; **three defect families** (~1/4 lint-visible, ~1/2 logic, ~1/4 blast radius), membership *measured* by whether the linter flags it; **12 scripts × 3 defects = 36 + 12 controls**, 8 scripts development / 4 held out whole. Rejected: Ansible / both; no lint-visible control; lint-only; 8 or 16 scripts. |
| `docs/adr/0030-p3-findings-schema-and-catch-rule.md` | **Call 3** — findings are a **closed schema** `{line_start, line_end, category, severity, why, fix}` (5 categories, blocking/advisory, ≤ 5 per review; verdict revise iff a finding is blocking); a **catch** is a blocking finding overlapping the defect's lines ±1, decided by code; right category reported as a second number; blocking findings on controls are false alarms. PSScriptAnalyzer mapped into the same schema. Rejected: checklist; free text; category required; a judge. |
| `docs/adr/0031-p3-models-per-role-and-the-frontier-boundary.md` | **Call 4** — drafter `qwen3:8b`; critic `hermes3:8b` (a different family, so not the drafter-alone arm); escalation is a **seam** with `qwen3:14b` as a local stand-in — frontier cost reported "stand-in, not final". The boundary for a future API is committed now: code allow-list (only the set + rubric), key in an env var, spend cap first, switching is an amendment. Rejected: a bigger Qwen as critic; qwen3:8b as critic; an API now. |
| `docs/adr/0032-p3-round-cap-and-escalation-rule.md` | **Call 5** — **at most 2 revise rounds**, code-counted, stopping early on approval or a no-change revision; **escalate immediately** on any blocking `blast-radius`/`security` finding, otherwise when blocking findings remain after the cap; the escalation verdict is final, what it rejects is `needs-human`. Escalate-everything kept as the cost-ceiling comparison arm. Rejected: 1 or 3 rounds; cap-only escalation. |

## Design-call ledger

| # | Call | Status |
|---|---|---|
| 1 | What the pipeline is measured on | **settled** — ADR 0028 (A) |
| 2 | Language, defect classes, set size and split | **settled** — ADR 0029 (PowerShell; 3 families; 12 scripts, split by script) |
| 3 | Rubric and findings schema; what counts as a catch | **settled** — ADR 0030 (closed schema; catch = line overlap ±1) |
| 4 | Models per role; the frontier boundary | **settled** — ADR 0031 (qwen3:8b drafts, hermes3:8b critiques, escalation seam with qwen3:14b stand-in) |
| 5 | Iteration cap and escalation rule | **settled** — ADR 0032 (≤ 2 rounds; escalate on blocking blast-radius/security, or at the cap) |

## Build order

Proposed, re-confirmed at the start of each step. A step is done when its check passes on the
bench, it is committed and pushed, and its evidence file exists in `evidence/`.

| Step | What | Decided in | State |
|---|---|---|---|
| 1 | The 12 reference scripts — generic placeholders, PSScriptAnalyzer-clean on the bench (Jose installs it), each with the checks its defect copies must fail | 0028, 0029 | **shipped** 2026-10-18 · `build-step-01-reference-scripts.md` — 12 scripts, PSScriptAnalyzer 1.25.0: 0 findings; positive control 5/5 rules fired |
| 2 | 36 planted defects + manifest (lines, family, expected category, code oracle); family **measured** by PSScriptAnalyzer; the 4 held-out scripts chosen and committed before any reviewer reads the set | 0028–0030 | **shipped** 2026-10-18 · `build-step-02-planted-defects.md` — held-out drawn by seed (02, 03, 05, 09); 36 defects, oracles verified; PSScriptAnalyzer flags exactly the 9 lint-visible; linter catch ceiling 8/9 under the line rule (02-L points at the leftover variable) |
| 3 | Findings schema, rubric file, the catch scorer (code only), PSScriptAnalyzer mapped into the schema — the zero-model arm | 0030 | **shipped** 2026-10-18 · `build-step-03-contract-and-scorer.md` — `src/review.py`, `rubric.md`, `tests/test_review.py`; linter arm on development **6/24** (lint 6/6, logic 0/12, blast 0/6), **0/8** false alarms; readings must list the files they scanned |
| 4 | Review-only arms on development data: drafter-alone vs. critic (`hermes3:8b`) — first catch / false-alarm numbers | 0031 | **shipped** 2026-10-18 · `build-step-04-review-arms.md` — development: linter 6/24 FA 0/8; critic (hermes3:8b) 5/24 FA 3/8; drafter-alone (qwen3:8b) 11/24 FA 6/8 at 8.7× the critic's output tokens. **Neither model discriminates** bugged from clean. Fixes on the way: `why` capped (runaway), review budget (ADR 0031 am. 1) |
| 5 | The revision loop (≤ 2 rounds) and the "fixed" oracle | 0032 | **shipped** 2026-10-18 · `build-step-05-revision-loop.md` — **fixed 0/24**; 2 controls rewritten, broke (detectable) 0, **broke by hand 2/2**; 2 broken scripts approved. Models now run alone (void run). ADR 0032 am. 1 |
| 6 | The escalation seam with the `qwen3:14b` stand-in, and the escalation rule | 0031, 0032 | **shipped** 2026-10-19 · `build-step-06-escalation.md` — escalator qwen3:14b alone 17/24 (logic 9/12) FA 6/8; **pipeline shipped 17/24 bugged scripts broken** (the first gate decides). ADR 0032 am. 2 |
| 7 | Per-stage cost and latency from traces; a scorer row in the Project 4 harness | 0028, Lab 12 | **shipped** 2026-10-19 · `build-step-07-cost-and-harness.md` — cost per arm from traces (pipeline 17,291 out tokens per catch vs 3,406 for qwen3:14b alone); `review` suite + scorer; baseline 12/32. ADR 0032 am. 3 (fail safe); digest-lookup bug fixed |
| 8 | Held-out measurement, the cost delta, card / glossary / PDF / artifact — close | 0028 | **shipped** 2026-10-19 · `build-step-08-predictions.md` (pushed before any run), `build-step-08-held-out.md`, `hand-audit-step-08.json` — held-out: qwen3:14b 10/12, qwen3:8b 9/12, hermes 5/12, linter 2/12; pipeline stopped 5/12, **shipped broken 7/12, fixed 0 by hand**; escalation not measured (bench slowdown, fail-safe held). Card `notes/project-03-script-review-pipeline.md`, PDF 72 pp, Field Notes v17 |

## What it builds on

| Piece | From | Role here |
|---|---|---|
| `src/team.py` | Lab 7 | worker / reviewer, round budget, cost ledger |
| `src/score.py`, judge calibration | Lab 10 | false pass vs. false alarm, never averaged |
| `src/tracing.py` | Lab 12 | per-stage cost and latency |
| `src/eval_harness.py`, `src/scorers.py` | Project 4 | the gate; one new scorer row |
| ADR 0026 lessons | Project 2 | labels before reading; held-out data; a different judge model |
