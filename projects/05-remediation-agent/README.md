# Project 5 — Remediation Agent with Approval Gates

An agent that detects configuration drift on a Windows Server target, proposes a remediation
plan, shows the operator the diff and what it will touch, waits for a human, applies the change,
verifies it independently, and rolls back automatically when verification fails. Every decision
is written to an audit log.

**Status:** **complete** (2026-10-19 → 2026-10-21) — design calls 1–7 (ADRs 0033–0039); build steps 1–8 shipped; done-means met on SRV01 — `evidence/build-step-08-done-means-run.md` · build order 5th (ADR 0001) · Phase IV.

**Result:** SRV01 broken three ways (registry, service, audit) — each detected, proposed by `qwen3:8b` (right fix 11/11), approved, applied and **verified** on real Windows; the audit fix **sabotaged** at the console with a third value → verifier failed it, rollback wrote the before-state and verified it (`rolled-back`, exit 1), confirmed by SRV01's own JEA transcript. 11/11 audit logs complete; 3/3 approvals refused when the machine changed before the yes. Card: `notes/project-05-remediation-agent.md`.

**Done means:** break the target three ways; the agent detects, proposes, fixes and verifies
each — and correctly rolls back a fix you sabotaged.

**Résumé line:** Built a closed-loop remediation agent with human approval gates, automated
post-change verification, and rollback-on-failure, operating against Windows Server targets
under a complete audit trail.

**Who decides:** Jose picks each design call from options Claude lays out; Claude writes the code.

## Why it is built fifth

ADR 0001: it needs everything underneath it — a real target and a read-only verifier
(Project 1), the human gate (Lab 13), and the harness (Project 4), so that "rolls back a
sabotaged fix" is proved on every run instead of watched once. Lab 13 left it two items:
checking a preview against what actually happened, and reporting refusals from a template
instead of the model's prose.

## Decisions

| ADR | Decision |
|---|---|
| `docs/adr/0033-p5-the-target.md` | **Call 1** — **both targets behind one `Target` seam**: a fake target in CI (rollback proved on every push) and SRV01 on the bench + lab home (broken three ways for real); one scenario set runs in both. DC01 out of scope; SRV01 checkpointed before every bench session. Owed: a fake-vs-real parity check. Rejected: fake only; SRV01 only; a third disposable VM. |
| `docs/adr/0034-p5-write-access-and-an-independent-verifier.md` | **Call 2** — writes go through a **second JEA endpoint, `AgentRemediator`**, mapped to a new account `CORP\svc-agent-rw`: reviewed functions only, `NoLanguage`, each with a preview and a before-state capture; rollback through the same door. **Verification stays on `AgentReader` as `svc-agent-ro`** — different code, an identity that cannot write, reading the machine rather than the actor's report. Rejected: writes added to `AgentReader`; an admin account over WinRM/Ansible. |
| `docs/adr/0035-p5-drift-classes-and-the-sabotage.md` | **Call 3** — **three drift classes in three subsystems**: a service's start type/state, an audit-policy subcategory, one `HKLM` registry value; specifics chosen in the build and committed with baselines first. **Sabotage:** in CI a broken fake remediator on every push; on SRV01 a bench-only pause between apply and verify while the operator reverts the value at the console — no broken code ever on the real endpoint. Rollback restores the before-state (the drifted one). Rejected: admin-group membership; three service drifts; a sabotaged function on the endpoint; CI-only sabotage. |
| `docs/adr/0036-p5-verified-and-the-rollback-trigger.md` | **Call 4** — **verified** = from verifier reads only: the drifted value equals baseline, the observed change equals the approved preview, every other watched value unchanged; bounded settling, timeout or unreachable = not verified. **On failure:** automatic rollback with no second approval, the rollback itself verified, outcome needs-human; a failed rollback stops loudly (checkpoint is the backstop). Never retry an apply. Outcomes `verified` / `rolled-back` / `rollback-failed` / `declined`. Rejected: target value only; full QE re-run as gate; ask before rollback; retry once. |
| `docs/adr/0037-p5-what-the-model-does.md` | **Call 5** — **the model proposes, code decides.** Code detects drift; the model (`qwen3:8b`, ADR 0009 sampling) proposes the fix as a tool call from `AgentRemediator`'s menu plus a short rationale; guard, preview, verification and rollback are code. Screen facts and refusals are templated; the rationale is labelled as the model's. An off-menu or refused proposal is outcome `refused`, no retry. Right-fix-chosen is measured per scenario; CI uses scripted replies. Rejected: no model (a lookup table); the model doing everything. |
| `docs/adr/0038-p5-approval-screen-and-audit-log.md` | **Call 6** — **one approval per fix**, Lab 13's screen extended: host read live, drift evidence, preview diff, the checks that will run, *"undone automatically, without asking"*, the model's rationale in a labelled block; Lab 13's answer rules. **Audit log** separate from the trace: append-only JSONL with the trace's run id, **write-ahead** (intent durable before apply) and **fail-closed** (no record, no action); who decided each line; the exact screen text and its hash; never a credential. Rejected: one approval for the plan; the screen unchanged; audit as a trace view; JEA transcripts only. **Amendment 1** (Jose, build step 3): the model's rationale leaves the screen — audit log only — restoring Lab 13's *show the ask, never the argument*. |
| `docs/adr/0039-p5-harness-entry-and-exit-codes.md` | **Call 7** — **three-way split**: a `remediation` suite whose fake-target scenarios are `fixed` and gated (scripted approver; scripted model in CI, live on the bench); SRV01 read-only detection and **fake-vs-real parity** `live` and reported; the human-gated SRV01 run is `playbooks/p05-break-three-ways.md` with committed evidence. A case passes iff **outcome matches and the audit log is complete**; right-fix-chosen reported. Agent exits **0** verified · **1** nothing stands · **2** tooling · **3** rollback-failed (restore from checkpoint). Rejected: all in the harness; SRV01 by hand; three exit codes; gating fix choice. |

## Design-call ledger

| # | Call | Status |
|---|---|---|
| 1 | What the agent changes, and where each check runs | **settled** — ADR 0033 (C: both, behind one seam) |
| 2 | Write access on SRV01; keeping the verifier independent of the actor | **settled** — ADR 0034 (B: second endpoint, read-only verifier) |
| 3 | The three drift classes and the sabotage | **settled** — ADR 0035 (service / audit / registry; broken fake in CI, out-of-band undo on SRV01) |
| 4 | What "verified" means and what triggers rollback | **settled** — ADR 0036 (target + preview + nothing else moved; auto-rollback, verified, stop) |
| 5 | What the model does | **settled** — ADR 0037 (A: the model proposes, code decides) |
| 6 | What the approval screen shows and what the audit log records | **settled** — ADR 0038 (one fix per approval; separate write-ahead, fail-closed audit log) |
| 7 | How the scenarios enter the harness (suite, scorer row, exit codes) | **settled** — ADR 0039 (three-way split; outcome + audit gate; exits 0/1/2/3) |

Design complete 2026-10-19.

## Build order

Proposed, re-confirmed at the start of each step. A step is done when its check passes in its
home (ADR 0010), it is committed and pushed, and its evidence file exists in `evidence/`.
Steps 1–5 need no VM; 6–8 need SRV01 (checkpoint first, every session).

| Step | What | Decided in | Home | State |
|---|---|---|---|---|
| 1 | `Target` seam: a reader and a remediator as separate objects; the fake target and `BrokenRemediator`; the scenario baseline file (three classes, values committed) | 0033, 0034, 0035 | CI | **done** 2026-10-19 — `evidence/build-step-01-target-seam.md` |
| 2 | Detection (code), the verifier with bounded settling, rollback, the four outcomes — on the fake | 0036 | CI | **done** 2026-10-19 — `evidence/build-step-02-verify-and-rollback.md` |
| 3 | The extended approval screen (`approval.py`) and the write-ahead, fail-closed audit log | 0038 | CI | **done** 2026-10-19 — `evidence/build-step-03-screen-and-audit.md` |
| 4 | The model's proposal as a tool call through the guard; `refused`; scripted replies in CI, `qwen3:8b` on the bench | 0037 | CI + bench | **done** 2026-10-20 — right fix 3/3, verified 3/3 on `qwen3:8b` — `evidence/build-step-04-the-proposal.md` |
| 5 | The `remediation` suite, scorer row and committed baseline; the agent CLI's exit codes 0/1/2/3 | 0039 | CI + bench | **done** 2026-10-20 — 11/11 on `qwen3:8b`, read twice identical, baseline pinned; one fix per run (Jose's pick) — `evidence/build-step-05-suite-baseline-exit-codes.md` |
| 6 | SRV01: `CORP\svc-agent-rw` on DC01; the `AgentRemediator` endpoint (three functions + rollbacks, each with a preview); the specific service, subcategory and registry value, baselines committed first | 0034, 0035 | bench + lab | **done** 2026-10-20 — SRV01 brought to baseline by hand (Spooler; Jose's pick); endpoint proved as the agent; write + undo of all three confirmed by console reads — `evidence/build-step-06-agentremediator-on-srv01.md` |
| 7 | SRV01 `live` tier: read-only detection and fake-vs-real parity | 0033, 0039 | bench + lab | **done** 2026-10-21 — verifier reads on SRV01 (SRV01-only role, Jose's pick); read-only detection with a positive control; `live` tier 6/6 incl. parity (password asked once, Jose's pick); negative control: no elevation = no session (fail-closed); SRV01 licence activated to ~2027-04-19 — `evidence/build-step-07-srv01-live-tier.md` |
| 8 | The done-means run — `playbooks/p05-break-three-ways.md`: break three ways, approve, sabotage one fix, evidence; project close (card, glossary, PDF, Field Notes) | 0035, 0039 | bench + lab | **done** 2026-10-21 — the agent's SRV01 write path built first (8a); three classes verified, audit sabotage (third value, Jose's pick) rolled back; pause hardened after two empty-Enter resumes — `evidence/build-step-08-done-means-run.md` |
