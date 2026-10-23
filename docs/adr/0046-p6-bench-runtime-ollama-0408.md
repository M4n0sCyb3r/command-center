# ADR 0046 — Project 6: the bench moves to Ollama 0.40.8; the Lab 6 gate floors re-set

**Status:** accepted · 2026-10-21 · decided by Jose (Project 6, before build step 3)
**Builds on:** ADR 0008 (a baseline moves only by `--rebaseline`, a person's call), ADR 0009
(generation is not promised repeatable), ADR 0022 (known retrieval losses), ADR 0043 (models
pinned by digest), `tools/upgrade_check.py` (the only path that moves `bench-pin.json`).

---

## Context

Ollama updated itself 0.40.4 → 0.40.7 during build step 2 (the tray app installs updates when it
restarts). `upgrade_check` read **MOVED**: `sop-jea-failure`'s expected chunk fell from rank 3 to
rank 4, and the Lab 6 gate failed. 0.40.7's release notes say "Updated llama.cpp and MLX" — the
engine that computes embeddings. On 2026-10-21 Jose updated to 0.40.8 (latest stable; it adds
one model and does not touch the engine). `upgrade_check` on 0.40.8 read MOVED identically.

**Found while reading that report — a latent defect, not drift.** The Lab 6 gate in
`src/retrieval.py` held recall@3 ≥ 0.90 and MRR ≥ 0.85, set on the 12-case suite. P02 step 8
(`137e07f`, 2026-10-18) grew the suite to 16 answerable and committed a baseline of **0.875 /
0.802** — under both floors. From 10-18 the gate could not pass on *any* Ollama version, so
`upgrade_check` could never read CLEAN. On 10-21 Claude reported "Lab 6 gate fails" as 0.40.7's
doing; that attribution was wrong. Nothing ran `retrieval.py` between 10-18 and 10-21.

## Options

- **A — Accept 0.40.8.** Re-set the floors just under the measured numbers (the gate's own stated
  rule); rebaseline retrieval on 0.40.8, read twice; `sop-jea-failure` becomes a named known loss;
  `upgrade_check` then moves the pin. Updates are taken on purpose, followed by `upgrade_check`.
- **B — Fix the floors only.** Floors under the 0.40.4 baseline; 0.40.8 still MOVED; P06 runs on an
  unverified runtime until a rollback.
- **C — Roll back to 0.40.4.** Fix the floors; reinstall; CLEAN against the old baseline. Behind
  on versions again, and fighting an updater that shipped five releases in ten days.

## Decision

**A** (Jose's pick, Claude's recommendation). The rollback picked the night before (C) was
dropped: the problem was never the version, it was the unannounced change.

- `src/retrieval.py`: `RECALL_FLOOR = 0.80`, `MRR_FLOOR = 0.77` — just under the accepted
  baseline (recall 13/16 = 0.8125, MRR 25/32 = 0.78125). One more miss, or one rank-1 case
  slipping to rank 2, fails the gate.
- `evals/baselines/retrieval.json` rebaselined on 0.40.8 (index sha256 `5d5ef035…`).
  **Known retrieval losses are now three:** `restart-procedure` (ADR 0022c),
  `sop-closeout-failure` (P02 step 8), `sop-jea-failure` (this ADR — a near-tie at rank 3/4,
  flipped by the engine update; the top two hits are unchanged). Hybrid retrieval, carried from
  P02, remains the targeted fix for all three.
- `tests/test_retrieval_seam.py` re-captured from the run record by hand: ranks 12 / 1 / 0 / 3.
- `bench-pin.json` → **0.40.8**, moved by `upgrade_check` (CLEAN).

## Reading (Jose's bench, 2026-10-21)

| Check | Result |
|---|---|
| `bench_check` before | FAIL on version only (0.40.4 → 0.40.8), generation OK — the alarm, as predicted |
| `upgrade_check` (old floors, old baseline) | MOVED: Lab 6 gate FAIL; `sop-jea-failure` rank 3 → 0; two top scores moved ~1e-4 |
| `eval_harness --rebaseline`, then re-read | 16/20 (4 known), drift vs new baseline **20/20 unchanged** |
| `upgrade_check` | **CLEAN** — gate PASS, 0 truncated, 20/20 identical; pin → 0.40.8 |
| `run_checks --home bench` | **53/53** |
| `review` 01-B via the Command Center, twice | REVISE, exit 1, line 14, both — **byte-identical** (sha256 `01b2e01b…`, 1,271 tokens) |

**Generation, honestly stated.** Four reviews of the same 01-B copy exist (digest `bdbd181c33f2`):
three are byte-identical across 0.40.7 and 0.40.8; one (0.40.7, 10-22 04:23Z, 1,290 tokens) is
not, and is unexplained. Step 2's "not repeatable on 0.40.7" rested on two runs, one of them the
outlier. Verdict and catch were the same in all four. Claude predicted *not* byte-identical on
0.40.8 — wrong.

## Consequences

- P06 evidence rests on **saved records**, never on re-generating a reply. A promotion's proof is
  the review record (raw reply, digest, version), not a re-run.
- `bench-pin.json` still covers retrieval only; generation is reported, not certified.
- The tray app installs updates on restart. An update is taken on purpose and followed by
  `upgrade_check` in the same session — never mid-build.
- Lesson: a gate whose floors sit above the committed baseline is a gate that cannot pass. When a
  suite's composition changes, re-check every floor that reads it (extends the "grep every pinned
  count" rule).
