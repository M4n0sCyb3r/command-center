# Project 4 — Agent Evaluation Harness

One command that runs golden suites against the agents in this repo, scores them with
deterministic assertions plus a calibrated judge, and reports pass rate, per-case diffs,
and drift against the previous run — so a prompt edit that quietly degrades quality fails
the build instead of going unnoticed.

**Status:** **BUILD COMPLETE — all 8 steps (2026-09-28).** CI green on `c1235af` (`evidence/build-step-08-ci.md`); ADR 0010 settled what CI is for. · build order 2nd (ADR 0001) · Phase III · design calls 1–4 settled 2026-09-20 (ADRs 0006–0008), CI scope 2026-09-28 (ADR 0010)

Step 1 shipped `src/eval_harness.py` (the argument surface only) and `tests/test_eval_harness.py` (12 checks, all observed `ok`). Notes, the build-time decision to put checks in `tests/` rather than `__main__`, and the one question left open for step 2 are in `evidence/build-step-01-argument-surface.md`.

Step 2 shipped `src/run_record.py` and `tests/test_run_record.py` (9 cases / 33 checks, all observed `ok`), settled `--suite required=True` and whole-file JSON over JSONL, corrected two things the step's own wording got wrong, and **discharged step 1's vacuous case 4** with a positive control. The check was proved capable of failing by three deliberate sabotages — one of which passed the round-trip check with a clean sheet and was caught only by the positive control. All of it is in `evidence/build-step-02-run-record.md`.

**Done means:** change a prompt, run one command, and state *with evidence* whether the
change helped.

## Why it is built second

ADR 0001: a harness with one consumer is a wrapper around that consumer. After Project 1
there are three, with three different shapes — the lab agent over the fake host (ground
truth fixed in code), the P01 agent over a real host (**ground truth that moves**), and
the Lab 6 retrieval suite (a different metric altogether). Three shapes is the minimum
needed to design a suite interface honestly.

## Decisions

| ADR | Decision |
|---|---|
| `docs/adr/0006-p4-suite-interface-and-ground-truth.md` | **Call 1** — the harness owns the run, the report, the drift comparison and the exit-code contract; case-level scoring is a seam with implementations named in code. No dynamic import; an unknown scorer is exit 2. |
| `docs/adr/0006-p4-suite-interface-and-ground-truth.md` | **Call 2** — every case declares its ground truth `fixed` or `live`. `fixed` replays a dated real capture, asserts values exactly, gates. `live` runs against the host, asserts invariants only, reports without gating. |
| `docs/adr/0007-p4-run-record-and-drift.md` | **Call 3a** — the run record is self-contained evidence: a header saying what the run was measured with, per-case rows carrying the scored detail, the answer text and the judge's verdict. Totals derived on read, never stored. |
| `docs/adr/0007-p4-run-record-and-drift.md` | **Call 3b** — drift is reported, not gated, with one exception: a case in the baseline and absent from this run is **exit 2**, and removing a case requires an explicit `--rebaseline`. Score-delta gating deferred until there is data to set a threshold from. |
| `docs/adr/0008-p4-records-baselines-and-cli.md` | **Call 4a** — run records stay gitignored; a designated baseline is committed at `evals/baselines/<suite-stem>.json`, rewritten only by `--rebaseline`. A reading is promoted to source by an explicit act. |
| `docs/adr/0008-p4-records-baselines-and-cli.md` | **Call 4b** — the baseline is **pinned** and moves only by hand. The report prints the baseline's commit and date every run, so a stale pin is loud. |
| `docs/adr/0008-p4-records-baselines-and-cli.md` | **Call 4b (ii)** — an incomparable baseline is handled **per field**: identity fields (suite hash, scorer, tier composition) suppress the drift section; condition fields (model digest, host, date) annotate and still compare. The exit code never moves. |
| `docs/adr/0009-sampling-policy.md` | **ADR 0009** — the agent and judge run under qwen3's own thinking-mode sampling, pinned in `model_client.SAMPLING`, replacing Lab 1's temperature 0. **Sampling is a CONDITION the header carries since record v5 (build step 6a); a mismatch is annotated since step 7.** |
| `docs/adr/0008-p4-records-baselines-and-cli.md` | **Call 4c** — `--tier fixed\|live\|all` is required with **no default**. A selected-but-unreachable `live` case is exit 2, never a skip. (*"CI invokes `--tier fixed`"* — superseded by ADR 0010.) |
| `docs/adr/0010-p4-ci-scope-three-homes.md` | **CI scope** — three homes: CI (the repo), the bench (+ the model), bench + lab (+ the VMs). Each check runs in the lowest home that can run it; every higher home runs everything below it first. CI runs the no-model checks and a committed-artifacts check; the measured `--tier fixed` run is a bench activity. Tests declare `BENCH_ONLY` with a reason; there is no allow-list. |
| build step 2 (not an ADR — cheap to reverse) | **`--suite` is `required=True` too.** Both options exit 2, so the exit code could not decide it; the tooling-error vocabulary is for things discovered *while measuring*, and a missing `--suite` is knowable before the process touches anything. |
| build step 2 (not an ADR — cheap to reverse) | **A record is whole-file JSON, not JSONL.** A trace must survive a crash; a record must be complete to be comparable. `write()` is atomic, and `--rebaseline` is therefore a file copy. |

## The contract these calls produce

| Outcome | Exit | Meaning |
|---|---|---|
| All gating cases pass | **0** | Promote. |
| A gating case failed | **1** | Validation failure — the agent got worse. |
| The answer could not be established | **2** | Tooling error: lab unreachable, fixture missing, unknown scorer. **Not a verdict.** |

Inherited unchanged from the Windows QE lab's `qe_report.py`. A configuration mistake is
never reported as a quality regression.

## What it builds on

| Piece | From | Role here |
|---|---|---|
| `src/score.py` | Lab 10 | The agent-case scorer behind the seam — facts, trajectory, judge, `calibrate()` |
| `src/retrieval.py` | Lab 6 | The second scorer — recall@k, MRR, refusal band |
| `evals/suites/agent.jsonl` | Lab 9 | 21 cases, 7 paraphrase groups, 8 judge-calibration pairs |
| `evals/suites/retrieval.jsonl` | Lab 6 | 14 cases (12 answerable, 2 unanswerable) |
| `src/tracing.py` | Lab 12 | Run records; `summarize()` already derives run-level numbers |
| `src/agent.py` | Lab 4 | The thing under test, over the fake host |
| `src/host_client.py`, `src/jea/` | Project 1 | The thing under test, over a **real** host whose answers move |

## Design calls

| # | Question | Status |
|---|---|---|
| 1 | What does the harness grade? | **settled** 2026-09-20 — C (ADR 0006) |
| 2 | What is `expected` when the answer key lives on a machine that changes? | **settled** 2026-09-20 — D (ADR 0006) |
| 3 | What is a run record, and how is drift compared against the previous run? | **settled** 2026-09-20 — 3a = D, 3b = B (ADR 0007) |
| 4 | What does the CLI look like, and what does CI invoke? — plus where records live, what a baseline is, and what happens when it is not comparable | **settled** 2026-09-20 — 4a = B, 4b = B, 4b(ii) = D, 4c = B (ADR 0008) |

## Build steps

Design is closed. Build order, each step ending in a check that can fail:

1. ~~**`src/eval_harness.py` — argument surface only.**~~ **DONE 2026-09-21.** `--suite` (repeatable), `--tier`
   (required, no default), `--baseline`, `--rebaseline`, `--out`. Check: `--tier` omitted
   exits non-zero with a usage message; no suite is executed.
2. ~~**The run-record writer.**~~ **DONE 2026-09-21.** Header (suite path + content hash,
   scorer, model name + digest, host target, **tier selection**, timestamp, record version)
   plus one row per case. Totals derived on read. Check: a written record reloads and
   `summarize()`-style totals match a hand count. **Two corrections to this step's own
   wording, made before any code:** a record is per **suite**, not per invocation (4a pins
   one baseline per suite, so the unit of comparison forces the unit of record); and the
   header carries the tier **selection**, not the tier **counts** — a count over rows is a
   total, and the selection is the only thing that separates "nobody asked for live cases"
   from "the live tier has drained".
3. **The scorer seam.** Fixed table in code mapping a suite's scorer name to
   `score.score_case` or the retrieval metric. Check: an unknown scorer name exits 2.
   **PART 1 DONE 2026-09-21** — the seam is PER CASE; `retrieval.evaluate()` refactored into
   `score_one()` + `derive()` with its numbers pinned first and reproduced bit-identically;
   the record row reshaped to a spine plus a scorer-owned `detail`, `RECORD_VERSION` 2.
   **PART 2a DONE 2026-09-21 — the suite declaration.** The design call is **settled: option
   A, a header line**, confirmed explicitly. A suite file's first line declares `suite` and
   `scorer`; every case carries its own `id` and `tier`; one parser (`src/suite.py`) owns
   those rules for both loaders. Writing it exposed that `agent.jsonl` was **two files in
   one** — 13 runnable cases plus 8 judge-calibration fixtures with no `expected_tools`,
   which would have scored as 8 rows whose `passed` means nothing. The fixtures moved to
   `evals/fixtures/judge-calibration.jsonl`, declaring `"scorer": null`, which the harness
   must refuse: **a file is not a suite, and it could not say so until it had a header.**
   155 checks, all observed `ok`; the suite's sha256 moved and **not one measurement did**
   (every pinned retrieval figure at delta exactly `0.000e+00`). Evidence:
   `evidence/build-step-03b-suite-declaration.md`.
   **PART 2b DONE 2026-09-21 — the scorer table.** `src/scorers.py`: a plain dict of
   literals mapping a declared name to a **factory**, which is called once per run with that
   scorer's own dependencies and returns a `scorer(case) -> CaseRow` closure. Rejected: a
   shared context object every scorer receives — it grows a field per scorer and none of them
   can tell "this run has no index" from "this scorer never needs one", the part-1 row defect
   one level up. `run()` now loads each suite, resolves its scorer and prints per-tier counts;
   it still exits 2, because nothing has been scored. **ADR 0006 call 1 is now checked
   structurally** — the check greps `scorers.py` for `importlib`/`__import__`/`eval(`/`exec(`/
   `entry_points`, and that grep was sabotage-verified before being trusted.
   **THE FINDING: exit 1 already meant "a gating case failed", and Python exits 1 on an
   uncaught exception** — so every crash in the harness was about to arrive as a quality
   regression that never happened. `main()` now turns any `Exception` into exit 2.
   197 checks, all observed `ok`. Evidence: `evidence/build-step-03c-scorer-table.md`.
   **PART 2c DONE 2026-09-21 — THE HARNESS MEASURED, AND EXIT 0/1 BECAME REACHABLE.** `run()`
   scores the selected cases, writes a record, reads it **back off disk** and derives the
   verdict from the file. A factory now returns `(scorer, Conditions)` because the two scorers
   use different models — retrieval's digest is `index.json`'s hash (no network; moves when the
   corpus is re-indexed), the agent's is Ollama's model digest, and a scripted run writes
   `model_digest: "scripted-runner-no-model"` so a fixture cannot pass itself off as a
   measurement. **Bench result: retrieval 14/14 exit 0; agent 6/13 exit 1**, identical over two
   runs. **Exit 1 is correct, and reading the record attributed every failure: 2 genuine agent
   shortcuts, 4 judge false-fails (decorated extractions that `normalize()` cannot match), 1
   underspecified expectation (`negative-unknown-service` has `expected_facts: null`, so a
   correct refusal cannot pass).** So `pass_rate = 0.4615` is NOT a measurement of the agent —
   it averages a false fail into an agent failure, the error Lab 10's own teaching line
   forbids. **ADR 0001 paid off: the harness found three defects in `score.py`, code that had
   passed every one of its own labs.** Nothing was fixed here on purpose — each fix moves the
   number and needs its own before/after. 214 no-model checks plus 3 bench runs. Evidence:
   `evidence/build-step-03d-measured-run.md`. **BUILD STEP 3 IS COMPLETE.**
4. **The report.** Per-tier case counts, baseline commit and date, drift section.
   Check: all three present on a run with no baseline at all. **DONE 2026-09-21.**
   `src/report.py` renders from the RECORD on disk. **The empty-tier question is SETTLED:
   exit 2, no longer provisional** — "I asked for a tier and it holds no cases" is
   indistinguishable from "that tier drained", and ADR 0006 decision 2 exists to make a drained
   tier loud. **And call 2 was CORRECTED before a line was written:** the plan to count
   "trajectory" vs "outcome-only" failures would have put the AGENT scorer's words inside a
   scorer-agnostic renderer — the third instance of that leak (`judge_errors` at part 1,
   `category` at 2b) — so the report **itemises rather than classifies**: spine-level counts,
   then every failing row's own detail verbatim. It refuses to ATTRIBUTE a failure; the
   available heuristic would have been right on 3 of 4 cases and is refused anyway, because
   attribution is what Lab 10's calibration measures against human labels. 254 checks. Evidence:
   `evidence/build-step-04-the-report.md`.
5. ~~**Drift comparison.**~~ **DONE 2026-09-23.** Match by `id`, never position. Check: a reordered suite file
   produces zero drift; a case missing from the run exits 2. `src/drift.py`: the spine plus one
   uninterpreted "is `detail` identical?" (option B — a case that still passes but slipped rank 1→2
   must show). Vanished is scoped to the tiers the run selected; drift never gates except a
   vanished case (exit 2). Evidence: `evidence/build-step-05-drift.md`.
   **Scorer defect 1 (negative case), part 1 DONE 2026-09-23:** an open extraction form for
   negative cases closes the `{}` ambiguity; pass rate unchanged at 6/13 — the judge's STANCE is
   the remaining defect, and calibration cannot see it yet. Evidence:
   `evidence/scorer-defect-1-negative-case.md`. **Defects must be fixed before step 6.**
   **Scorer defect 1, part 2 DONE 2026-09-24 — DEFECT 1 FIXED:** calibration gained the agent's
   real refusal (`candidate_text`, FF 0 → 1 as predicted), then the judge's overlapping stances
   became a partition decided in order (FF 1 → 0, FP 0, and the Fax refusal passes on a real
   answer). Evidence: `evidence/scorer-defect-1b-calibration-sees-it.md`,
   `evidence/scorer-defect-1c-stance-precedence.md`. **Defects 2 and 3 remain before step 6.**
   **Scorer defect 2 DONE 2026-09-24:** a closed vocabulary in the suite header, enforced by the
   judge's form (states and service names, plus `unlisted`). Judge replay 7 → 10 of 13 as
   predicted; calibration 1.00 / FP 0 twice. Evidence: `evidence/scorer-defect-2-closed-vocabulary.md`.
   **Defect 3 remains before step 6** (later closed as a known limit) — diagnosed 2026-09-24 (the question's wording, not the
   slot); attempt A (hide the question) measured and REJECTED, see
   `evidence/scorer-defect-3-attempt-a-rejected.md`. Next: per-service status extraction, with
   the aggregate derived in code.
6. ~~**`--rebaseline`.**~~ **DONE 2026-09-28.** Writes `evals/baselines/<suite-stem>.json`, records removals.
   Check: a removal appears in the new baseline's header and in the report. **6a** = sampling in the
   header (record v5, `evidence/build-step-06a-sampling-condition.md`); **6b** = `--rebaseline`,
   refuses incomplete / unscored-gating, promotes failing runs (call B), removals in the run's own
   header (call A, record v6), `evidence/build-step-06b-rebaseline.md`. **First baselines committed**:
   retrieval 14/14, agent 11/13.
7. ~~**Header comparability.**~~ **DONE 2026-09-28.** Identity mismatch suppresses drift and names the field;
   condition mismatch annotates. Check: both, by editing a baseline header by hand. **Call C:** identity at
   two levels — canonical suite-header hash (suppresses all) + canonical per-case definition hash on every
   row (REDEFINED, that case only); record v7; baselines migrated. Also fixed: tests reading the repo's
   baselines (pushed red at `6c26832`), and a BASELINE commit line that named a deletion commit.
   `evidence/build-step-07-comparability.md`.
8. ~~**CI workflow.**~~ **DONE 2026-09-28.** Not the one line ADR 0008 specified: `fixed` froze the ground
   truth, not the model, so that line exits 2 on every push. **ADR 0010 = A:** three homes (CI / bench /
   bench + lab), each check in the lowest home that can run it, every higher home runs everything below it
   first. `tests/run_checks.py --home ci|bench`, exclude-by-declaration, a committed-artifacts check,
   `.github/workflows/checks.yml`. **Two findings:** two checks passed only because no model was reachable
   (caught by the first `--home bench` run), and two read the gitignored index (caught by the first CI run,
   red on `6847e21`, green on `c1235af`). `evidence/build-step-08-ci.md`.

## Open

- **`Recent System Event Health` is settled as a `live` case** by ADR 0006 — it carries
  `lookback_minutes: 60`, so it describes recent history, not configuration, and freezing
  an expectation onto it is a category error. This closes the question left open in
  `LOG.md` on 2026-09-19.
- The per-tier case count must be printed on every run from the **first** version, not
  added later — it is the only thing that makes a silently draining tier visible.
- Where run records live is settled (ADR 0008). Whether the `fixed` captures sit beside
  their suite or in their own directory is now a **build-time** decision, not a design call:
  it is cheap to reverse and touches one loader.
- ~~Whether `--suite` should be `required=True`~~ — **closed at build step 2**: yes, with
  the reasoning on the argument itself in `src/eval_harness.py`.
- ~~**`RECORD_VERSION` is 1.**~~ — **bumped to 2 at build step 3 part 1**, three days after
  the field was added: a v1 row has no `passed`, so reading one would count every case as
  failed and report a catastrophic regression that never happened. `read()` now refuses v1.
- ~~**`--tier live` would currently select ZERO cases.**~~ — **SETTLED AT STEP 4: exit 2**, and
  the reasoning is on that branch in `src/eval_harness.py`.
- ~~**OPEN AND SCHEDULED: the scorer's three defects must be fixed BEFORE step 6 pins a
  baseline.**~~ **CLOSED before step 6:** defects 1 and 2 fixed, defect 3 closed as a known limit
  (`evidence/scorer-defect-3-closed-known-limit.md`, private). A baseline recorded while the judge has a ~31% false-fail rate on the agent suite
  **enshrines that rate as the expectation**, and then fixing the judge reads as drift against a
  baseline that was wrong. Priority order: the negative-case rule (`expected_facts: null` makes
  a correct refusal unpassable), `normalize()` vs decorated extractions, the empty extraction on
  `any-down-4`.
- ~~**OPEN FOR STEP 6 (2026-09-27): `--rebaseline` must refuse a record that is
  `incomplete` OR has any `unscored` gating case.**~~ **DONE at 6b** (`run_record.refusal()`). A baseline with a hole in it makes every
  later comparison of that case UNMEASURED. Record v4, `evidence/judge-error-gating.md`.
- (superseded) **`--tier live` would currently select ZERO cases.** Both
  suites are entirely `fixed`; the `live` tier belongs to Project 1's real endpoint and holds
  nothing yet. Whether "a tier was selected and it has no cases" is exit 2 or a vacuous pass
  is **build step 4's call**, with the exit code — deliberately not decided at step 3. The
  per-tier counts print so the zero is visible rather than inferred.
- **Settled by ADR 0008:** "the previous run" means a **pinned** baseline, not last-run-wins.
  It moves only by an explicit `--rebaseline`, and its commit and date print on every report
  so a stale pin is visible rather than discovered.
- **The score-delta threshold is deferred, not dropped** (ADR 0007, call 3b = C). It is
  added once the run records have collected enough run-to-run data to set it from
  measurement instead of guesswork.

- ~~**Every retrieval record said `host_target: "none - committed index"`**~~ — **CLOSED 2026-09-29.**
  The index is gitignored. Now `"none - local index (data/index.json, gitignored)"`, named once as
  `scorers.HOST_TARGET_RETRIEVAL`, and `test_scorer_table.py` checks it against **git** (sabotage: the old
  text goes red). Bench: the change was annotated as a CONDITION with 14/14 still compared and exit 0;
  retrieval rebaselined. Evidence: `evidence/build-step-08-ci.md`, post-close section.
- **CI cannot reach the lab** (ADR 0008, stated in writing for the first time). DC01 and
  SRV01 are on `LAB-INTERNAL` with no gateway, so the `live` tier runs on the bench only.
  This is a property of the topology, not a gap to close.
