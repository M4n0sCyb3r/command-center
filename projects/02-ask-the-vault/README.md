# Project 2 — Ask the Vault (runbook RAG)

A retrieval-augmented assistant over Windows Server documentation, personal Ansible
playbooks and the course's own notes. Answers cite the source file and location, and the
retrieval quality is measured by a suite the Project 4 harness runs.

**Status:** **complete** (2026-10-18) — build steps 1–8 shipped (see *Build order* below) ·
build order 3rd (ADR 0001) · Phase II · design calls 1–4 settled 2026-09-29 (ADRs 0011–0021);
build-time calls settled in ADRs 0022–0023 and in steps 3–4's evidence. Final retrieval
baseline: **17/20** (recall@3 14/16, MRR 0.802), answer suite **24/27** — named known failures
(`restart-procedure`, ADR 0022c; `sourdough`, ADR 0023; `sop-closeout-failure`, step 8). Updated 2026-10-18 (step 8).

**Done means:** twenty questions with known answers, a measured hit rate, every answer
traceable to a file — and the prompt-injection test Lab 11 promised, run against the index.

**Résumé line:** Built a retrieval-augmented assistant over lab runbooks and
infrastructure documentation, with source-cited answers and a measured retrieval-quality
baseline.

## Why it is built third

ADR 0001: it extends Lab 6 onto a real corpus and pays the injection debt, and its suite is
the harness's first new customer — the test of Project 4's claim to be agent-agnostic.
ADR 0006 and the scorer table (P04 build step 3, part 2b) were shaped for it: adding its
scorer is one factory and one table row.

## Build order

Proposed order, re-confirmed at the start of each step. A step is done when its check passes on
the bench, it is committed and pushed, and its evidence file exists in `evidence/`.

| Step | What | Decided in | State |
|---|---|---|---|
| 1 | Manifest loader — `data/corpus.jsonl` replaces the directory glob | ADRs 0011, 0015, 0016 | **shipped** 2026-09-30 · `build-step-01-manifest.md` |
| 2 | Refusal threshold: gate checks it; near-misses tested; threshold becomes a pre-filter; rebaseline | ADRs 0022, 0023 | **shipped** 2026-10-05 · `build-step-02-threshold-and-rebaseline.md` |
| 3 | Chunk identity: ids are manifest paths; chunks carry authorship; a proven pure rename | ADRs 0019, 0020 | **shipped** 2026-10-06 · `build-step-03-chunk-identity.md` |
| 4 | Splitter fixes, all of ADR 0017's code: **4a** front matter, fence-aware, Microsoft link lists (no-ops today, proved byte-identical); **4b** the lead becomes an `Overview` chunk (+4 SOP chunks). Every drift explained | ADR 0017 (findings 1, 3, 4) | **shipped** 2026-10-07 · `build-step-04-splitter-fixes.md` |
| 5 | Vendor the Microsoft slice (12 pages, 8 competitors + 4 decoys); `_title()` reads past front matter; the embedder's context limit found and enforced (no silent truncation; long sections cut into parts); threshold unchanged; Overview chunks kept | ADRs 0012–0014, 0016, **0024** | **shipped** 2026-10-07 · `build-step-05-microsoft-slice.md` |
| 6 | The answer step: structured `{answer, cited}`, citation check + one retry, authorship field + warning, CC BY line, a "sources do not answer this" outcome; `evals/fixtures/answer-refusal.jsonl` becomes its first golden set | ADRs 0018–0021, 0023, **0025** | **6a–6c shipped** 2026-10-10 · `build-step-06-answer-step.md` — 20/23 (10/12 answerable, 6/7 near-miss, 4/4 out-of-domain), fresh-load rule; **6d** faithfulness judge built (ADR 0026, `f051f33`), bar met on the 12 constructed pairs (0.917, false pass 0) but **not trusted** — real answers show paraphrase false fails (ADR 0026 amendment 3); 9 real-answer pairs added (`8936bc4`) → honest baseline **0.714**, false pass 0, every miss an empty quote on a paraphrase (amendment 4); fresh load per judge reading (`0e1542f`) → reproducible baseline **0.667** (amendment 5); quote-mark fold + two prompt changes → **0.857, false pass 0** (amendment 6, `d3df30c`), still under the bar; held-out check (12 new pairs, labels committed first) → **0.750, false pass 0** (amendment 7); list markers stripped (`a126c94`) → 21 pairs 0.857 unchanged, held-out **0.833, FP 0** (amendment 8); an ellipsis in a quote joins real passages (`24e5c5e`) → held-out **0.917, FP 0** (first over the bar, but v1 is now development data), 21 pairs 0.857 unchanged (amendment 9); `--calibrate` saves every reading with its condition (`09cc250`), cited readings committed under `evidence/judge-readings/` — held-out reproduced exactly, `cleanmgr-sageset` re-explained (amendment 10); **held-out v2** (questions `35a085e`, labels committed first `74a611c`) → **0.417**, false pass 0 only by luck (`bench-diagnosis` inverted sentence passed) — dev-set readings were overfit, judge **not trustworthy**, reported never gated (amendment 11); scored at the defect sentence (`8489c09`) — v2 re-scored **0.333, FP 1** (wrong reason), 21 pairs 0.857/FP 0 real; bench pin covers retrieval only, not generation (amendment 12); inline numbered runs, closing-quote splits, ordinal words fixed (`f8c30c9`) — v2 (dev) 0.250, FP 1 plain, one pair moved by Ollama 0.40.4 (amendment 13); the deterministic 500 diagnosed — a JSON-escape loop on backslashes (`'\\' x 203`) — and removed (`5f85f8d`): v2 (dev) 0.333, FP 1, errors 0 (amendment 14); **held-out v3** (questions `0194e8c`, labels first `e78873c`) → **0.636, FP 0, FF 4, errors 0**; **6d CLOSED: the judge ships UNCALIBRATED — reported, never gated** — never met the bar on held-out data (v1 0.750, v2 0.333, v3 0.636); known failure modes recorded (amendment 15). **Step 6 complete** |
| 7 | The prompt-injection test Lab 11 promised, run against the index | **0027** | **7a measured** 2026-10-16 (`185ab0b`): six INSTRUCTION injections planted in real Microsoft chunks, code oracle with a clean twin — **0 of 6 succeeded**, twice (oracle checked for paraphrase blindness; wording varies run to run, verdicts do not); instruction injection ≠ content poisoning (amendment 1); **7a-ii** (`217e216`): content poisoning in the document's own voice — **the tampered number (1800→86400) got through**, cited and confident; the harmful-step case was never invited (inconclusive) (amendment 3); **7b** (`77442d3`): **corpus integrity** — every vendored page pins a content sha256, `load_manifest()` refuses a changed page before a chunk is cut; attack (h) on disk is refused (amendment 4); **7c** (`cedb8b5`): cases (a)–(f) gate — exit 1 if one succeeds, 2 if one was not reached (amendment 5); **second labeller** (ADR 0026 am. 17): blind agent agrees on 32/35 (kappa 0.77), the three misses are Claude's lenient borderline calls, ruling unchanged; next: bench check, then step 8 |
| 8 | Close: suite to the 20 questions *Done means* names (16 today), notes card, glossary, Field Notes PDF + artifact | 0022c, 0023 (applied) | **shipped** 2026-10-18 · `build-step-08-close.md` — 4 questions, one per SOP failure-mode table, committed before measuring (`e43e43e`); retrieval **17/20** (recall@3 14/16, MRR 0.802; new known failure `sop-closeout-failure` — patching vocabulary wins, kept as written), answer **24/27**, both read twice identical and rebaselined; a stale index-digest condition since the 0.40.4 upgrade cleared; card `notes/project-02-ask-the-vault.md`. **Project 2 complete** |

## Decisions

| ADR | Decision |
|---|---|
| `docs/adr/0011-p2-corpus-scope.md` | **Call 1** — the corpus is **ours plus a pinned slice of public Microsoft Windows Server documentation** (CC BY 4.0), chosen to overlap the runbooks' topics. Membership is an explicit list, never a directory walk. Every chunk carries its authorship. Rejected: ours only (no competing documents, a staged injection target); indexing folders (a moving corpus, and a boundary crossed by default). |
| `docs/adr/0012-p2-vendor-the-corpus.md` | **Call 2.1** — the Microsoft slice is **committed to the repo**, not fetched at build: a commit is the review step for text. Index builds carry no network dependency; each vendored file records its upstream path and commit. Rejected: fetch at build (untrusted text reaches the index without ever appearing in a diff). |
| `docs/adr/0013-p2-page-selection.md` | **Call 2.2** — about **18 pages / ~150 KB**: competitors that contend with each runbook for rank 1, plus **decoys** that share a runbook's words but answer a different question; WinRM (no upstream page) gets the Server Manager remote-management page as a near-miss. Every page carries a role, `competitor` or `decoy`, chosen by a stated rule. Rejected: competitors only (never tests a look-alike); whole folders (an unreadable diff). |
| `docs/adr/0014-p2-our-documents.md` | **Call 2.3** — ours = the **five runbooks plus the four SOPs**: the documents written to answer operational questions. Rejected: field-notes cards (study material; a corpus that moves every session); copies of the QE playbooks (a drifting snapshot, and code rather than prose — its own ADR later). **Call 2 closed.** |
| `docs/adr/0015-p2-manifest-format.md` | **Call 3.1** — the manifest is **JSONL with a header row**, `data/corpus.jsonl`, validated against a JSON Schema on load: data, not code. The header carries the upstream commit, attribution and the decoy rule; one document per line, so a membership change is one diff line. Rejected: YAML (new dependency, implicit typing); JSON (multi-line diffs); a Python module (the membership list would execute). |
| `docs/adr/0016-p2-manifest-fields.md` | **Call 3.2** — each line carries `path`, `authorship`, and for Microsoft pages `role`, `upstream_path` and **`targets`** (the `ours` document it contends with); decoys add **`shared_keyword`**. Answers two questions: measurement (per-topic results; ADR 0013's prediction becomes testable) and audit (the decoy rule checked row by row). Fields forbidden on `ours` rows. Rejected: the required four only (prediction unmeasurable); per-file `sha256` (duplicates git, churns on every SOP edit). |
| `docs/adr/0017-p2-chunking-second-shape.md` | **Call 3.3** — fix what is wrong **now**: text above the first `##` becomes an `Overview` chunk (it was being discarded — including our SOPs' leads), front matter stripped, Microsoft "Related links / See also" sections dropped (keyed on manifest authorship), a fence-aware splitter. **Chunk size is left alone and measured** once the corpus lands. Rejected: fix the lead only (link-list chunks crowd the refusal band); a size cap now (a number tuned before it is measured). **Call 3 closed.** |
| `docs/adr/0018-p2-who-writes-the-citation.md` | **Call 4.1** — **the model picks, the code checks and renders**: structured `{answer, cited}`; every cited id must be in the retrieved set (and at least one); citation text built by code from chunk + manifest. **4.1b** — a failed check gets **one retry** naming the bad id, then refusal; every retry counted and reported. Rejected: model-written inline citations (a fabricated source looks real); listing every hit (cites what was retrieved, not used — hides the decoys); dropping bad ids (the prose was written from the invented source). |
| `docs/adr/0019-p2-where-authorship-shows.md` | **Call 4.2** — authorship is a **field on every citation**, set by code from the manifest; the text label is rendered from it. A **warning** is added when the answer cites a Microsoft page but not the `ours` document it `targets` — reported, not gated. Rejected: a text-only label (forgeable by the untrusted text it describes); field only (silent about the failure the decoys exist to provoke). |
| `docs/adr/0020-p2-citation-identity.md` | **Call 4.3a** — a citation's identity is its **manifest path** (`data/runbooks/service-restart.md#procedure`): unique by construction, and the manifest key and citation key are one string. `INDEX_VERSION` → 2; the 14 suite ids rewritten in one diff and promoted by `--rebaseline`. Rejected: bare file name + duplicate check (uniqueness held by a check; a repeated upstream name forces a rename away from upstream). |
| `docs/adr/0021-p2-microsoft-citations-and-credit.md` | **Call 4.3b** — a Microsoft citation names our vendored copy **and** its upstream reference (repo @ pinned commit : path), from the manifest; any answer citing Microsoft carries **one CC BY attribution line**, rendered from the manifest header and recorded as a field. Answers leave the program; the credit goes with them. Rejected: vendored path only; upstream reference without the credit line. **Call 4 closed.** |
| `docs/adr/0022-p2-refusal-threshold-and-known-loss.md` | **Build step 2 call** — (a) `REFUSE_BELOW` stays a **committed constant** and Lab 6's gate fails unless it sits strictly **inside the band, both edges** (the harness checks only the top one); (b) the threshold is re-tuned only after **~6 near-miss unanswerable cases** measure the band on ~8 negatives instead of 2, and re-measured when the Microsoft slice lands; (c) `restart-procedure` stays as written and is baselined as a **known failure**. Rejected: deriving the threshold every run; re-tuning on one question; rewording the question until it passes. |
| `docs/adr/0023-p2-near-misses-and-the-pre-filter.md` | **Build step 2(b) calls** — six near-miss negatives all outscored a real question (best 0.7121 vs 0.5511): a similarity score measures topic, not answerability. (1) refusing a near-miss is the **answer step's** job; (2) the near-misses move to `evals/fixtures/answer-refusal.jsonl` (`scorer: null`) as that scorer's first golden set; (3) `REFUSE_BELOW` (0.53 → **0.51**) is a **pre-filter**: no real question refused, ≥ 75% of out-of-domain refused, every miss named — `sourdough` a known miss. Supersedes ADR 0022(b) and its band check. Rejected: a retrieval-time judge (deferred until a number earns it), deleting the near-misses, a vocabulary rule written after seeing the score. |

## Design-call ledger

| # | Call | Status |
|---|---|---|
| 1 | What goes into the corpus, and who wrote it | **settled** — ADR 0011 (B) |
| 2.1 | Committed or fetched at build | **settled** — ADR 0012 (A, vendor) |
| 2.2 | Which Microsoft pages, and how many | **settled** — ADR 0013 (C + ii: competitors plus decoys; WinRM near-miss) |
| 2.3 | Which of ours | **settled** — ADR 0014 (B: runbooks + SOPs) |
| 3.1 | Manifest format and location | **settled** — ADR 0015 (C: JSONL + header row, schema-checked) |
| 3.2 | Manifest fields | **settled** — ADR 0016 (B: + `targets`, `shared_keyword`) |
| 3.3 | Chunking the Microsoft page shape (front matter, `:::` blocks, `[!NOTE]`) | **settled** — ADR 0017 (B: fix defects now, measure size later) |
| 4.1 | Who writes the citation; what happens when it fails the check | **settled** — ADR 0018 (C: model picks, code checks + renders; B: retry once, then refuse) |
| 4.2 | Where authorship shows in an answer | **settled** — ADR 0019 (C: field + warning when ours was passed over) |
| 4.3a | Citation identity | **settled** — ADR 0020 (B: manifest path) |
| 4.3b | What a Microsoft citation points to; where CC BY attribution appears | **settled** — ADR 0021 (C: upstream ref + one attribution line) |
| S2 | Build step 2: the threshold gate, its evidence, `restart-procedure` | **settled** — ADR 0022 (a: A, b: C, c: A) |
| S2b | Build step 2(b): who refuses a near-miss; where they live; what the threshold promises | **settled** — ADR 0023 (1: A answer step, 2: B fixture, 3: B pre-filter) |
| — | The injection test; the scorer (incl. faithfulness) and its CI home | later calls, order set as they surface |

## What it builds on

| Piece | From | Role here |
|---|---|---|
| `src/retrieval.py` | Lab 6 | Chunking, embeddings, `search()`, the refusal band |
| `src/memory.py` | Lab 5 | Provenance discipline, meeting a corpus nobody here observed |
| `src/guard.py` | Lab 11 | Blast radius — the injection test lands here |
| `src/eval_harness.py`, `src/scorers.py` | Project 4 | The gate; P02's suite enters through the scorer table |
| `evals/suites/retrieval.jsonl` + baseline | Lab 6 / P04 | 14 cases, 14/14 at the first measurement of the corpus change; 20 questions, 17/20 at close (step 8) |
