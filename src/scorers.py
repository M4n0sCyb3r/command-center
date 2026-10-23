"""The scorer table: a suite's declared name, and the code that scores it.

Project 4, build step 3 part 2b. Part 2a made a suite file declare a scorer
NAME. This is where a name becomes a function, and where a name we do not have
becomes a tooling error instead of a crash or a failed case.

THE SECURITY CONSTRAINT THIS FILE EXISTS TO HONOUR (ADR 0006, call 1)
---------------------------------------------------------------------
`SCORERS` below is a plain dict of literals written in source. There is no
importlib, no entry points, no registry, no module path read from a file. A
suite file chooses exactly one thing: WHICH KEY. The key either matches a name
in that dict or the run exits 2.

Jose's reason, which is the whole point: selecting a scorer by a string in a
data file lets the data choose which code executes, and Project 2 will ingest
documents we did not author. A suite naming an unknown scorer is a TOOLING
ERROR, never a failed case - the same category ruling as ADR 0007's vanished
case (exit 2, not exit 1) and part 2a's malformed header.

`tests/test_scorer_table.py` asserts this STRUCTURALLY by reading this source
for the forbidden constructs, rather than trusting the paragraph above. A
comment saying "no dynamic import" does not stop anyone adding one.

WHAT THE TABLE MAPS TO: A FACTORY, NOT A SCORER (decided 2026-09-21, confirmed)
-------------------------------------------------------------------------------
Each value is a FACTORY. Call it once per run with that scorer's own
dependencies and it returns a `scorer(case) -> CaseRow` closure. So the
per-case signature stays uniform and minimal, and the dependency wiring lives
inside the factory that needs it.

Rejected: a shared context object passed to every scorer. Count what the three
scorers this repo will have actually need - retrieval wants the loaded index,
the agent scorer wants a runner and a judge, Project 2's will want its own
retriever over untrusted documents - and that object carries four or five
fields of which each scorer touches one or two. Every run would then build
dependencies for scorers it is not using (loading the index on an agent-suite
run, standing up a model client on a retrieval run), and no scorer could tell
"this run has no index" from "this scorer never needs one". **That is the
union-of-nullables shape rejected for the record row in part 1, one level up.**
A keyword bag is the same thing with less type safety.

The payoff, concretely: adding Project 2's scorer is one factory and one table
row, and touches nothing the agent or retrieval scorer can see.

WHY THE MODULE IMPORTS ARE INSIDE THE FACTORIES
-----------------------------------------------
`retrieval` pulls in `requests` and the embedding client; `score` pulls in the
model client. Importing both at module scope would mean an agent-suite run
fails when retrieval's dependencies are missing, and a retrieval run drags in a
model client it never calls. So each factory imports its own module when it is
built - the module name is a LITERAL in code, so nothing here is dynamic and
the constraint above is untouched.

THE COST, named and paid: a typo in one of those imports would surface only
when that scorer is selected, rather than at startup. It is paid in the check -
`test_scorer_table.py` builds EVERY entry in the table, so a broken import
fails a check instead of waiting for the one run that selects it.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from dataclasses import dataclass

from run_record import CaseRow, suite_sha256
from suite import Suite

# One case in, one row out. The only signature the harness ever calls.
ScorerFn = Callable[[dict[str, Any]], CaseRow]


@dataclass(frozen=True)
class Conditions:
    """What a scorer is about to measure WITH. Built by the factory, not the
    harness, and that split is build step 3 part 2c's decision.

    The run record's header needs `model_name`, `model_digest` and
    `host_target`. The harness cannot fill them, because **the two scorers do
    not use the same model**: retrieval reads `nomic-embed-text` through a
    local index, the agent scorer runs `qwen3:8b` for both the loop and the
    judge. Writing either one from the harness would be a placeholder that
    looks like provenance, which is what ADR 0007's self-contained-evidence
    ruling exists to prevent.

    So the factory returns the scorer AND its conditions - one object, built at
    the same moment, by the only code that knows. **Third time this project has
    found an interface incomplete only once a second consumer was wired to it**
    (the row's shape in part 1, the suite's header in part 2a, this).

    Rejected: attributes hung off the closure (a callable carrying metadata
    nothing can type-check and everyone forgets to set); a second CONDITIONS
    table parallel to SCORERS (two tables that will disagree about one scorer);
    the harness asking each scorer's module directly (reinstates exactly the
    coupling this file was created to remove).

    THE DEFINITION THAT MATTERS: the digest records **the artifact the scorer
    actually reads.** For retrieval that is `data/index.json`, not the
    embedding model - because re-indexing an edited corpus changes the
    measurement while the model's own digest sits still. For the agent it is
    the model file, because that is what produces the answer.
    """

    model_name: str
    model_digest: str
    host_target: str
    # P04 build step 6a: the options every generation was sent with, or None
    # for a scorer that generates nothing (retrieval embeds; a scripted runner
    # calls no model). No default - a factory that forgot it must fail loudly,
    # not record "this scorer does not sample".
    sampling: dict[str, Any] | None


# A factory: called once per run, returns the per-case scorer and what it is
# measuring with.
ScorerFactory = Callable[..., tuple[ScorerFn, Conditions]]


class ScorerError(ValueError):
    """A scorer could not be resolved. Always exit 2, never exit 1.

    A subclass of ValueError so the harness can catch every tooling error -
    malformed suite, unknown scorer, unreadable file - in one place, since ADR
    0006/0007/0008 put all three in the same category: the verdict could not be
    established.
    """


def _spine_and_detail(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Split a scorer's per-case dict into the spine's `result` and the detail.

    retrieval.score_one() still says `passed: bool` - that is the retrieval
    scorer's own vocabulary, pinned by its seam test, and it has no judge so it
    can never be unscored. The mapping onto the v4 spine happens HERE, once,
    and it is strict: only a real bool is accepted. `bool(row["passed"])` used
    to coerce, which would turn a stray None into a quiet "fail".

    `passed` is REMOVED from the detail rather than copied into both. A field
    written twice is a field that can disagree with itself, and `summarize()`
    reads the spine - so a detail carrying a stale `passed` would be a second
    answer to a question that already has one. Same rule that keeps totals out
    of a record entirely (ADR 0007, call 3a).
    """
    passed = row["passed"]
    if not isinstance(passed, bool):
        raise ValueError(f"scorer row 'passed' must be a bool, got {passed!r}")
    detail = {key: value for key, value in row.items() if key != "passed"}
    return ("pass" if passed else "fail"), detail


# --------------------------------------------------------------------------
# retrieval
# --------------------------------------------------------------------------

def retrieval_row(case: dict[str, Any], scored: dict[str, Any]) -> CaseRow:
    """Turn retrieval.score_one()'s dict into a CaseRow. Pure, so it is testable
    without the embedding model - which matters, because everything else on the
    retrieval path needs Ollama up and therefore cannot run in CI."""
    result, detail = _spine_and_detail(scored)
    return CaseRow(id=case["id"], tier=case["tier"], result=result, detail=detail)


# What a retrieval record says it ran against. Named once so the check that it
# tells the truth (tests/test_scorer_table.py) reads the same string.
HOST_TARGET_RETRIEVAL = "none - local index (data/index.json, gitignored)"


def make_retrieval_scorer(*, k: int = 3, index: Any = None,
                          suite: Suite | None = None, tier: str | None = None) -> tuple[ScorerFn, Conditions]:
    """Build the retrieval scorer. Loads the index ONCE, here, not per case.

    That is the reason this is a factory: `search()` needs the index for every
    case, the index is 160 KB of vectors read off disk, and re-reading it 14
    times would make the harness's own overhead part of what it measures.

    The conditions are read OFF THE INDEX FILE and need no network: the file's
    own header names the embedding model that built it (and `load_index()`
    already refuses an index built by a different one), and hashing its bytes
    captures the corpus as well as the model. `suite_sha256` is reused because
    it is simply "hash this file's bytes" - one implementation, so the record's
    two hashes cannot be computed two different ways.
    """
    import retrieval                      # literal module name - see the docstring

    loaded = retrieval.load_index() if index is None else index

    def score(case: dict[str, Any]) -> CaseRow:
        one = retrieval.Case(id=case["id"], tier=case["tier"],
                             question=case["question"], expect=case["expect"])
        return retrieval_row(case, retrieval.score_one(one, k=k, index=loaded))

    payload = json.loads(retrieval.INDEX_PATH.read_text(encoding="utf-8"))
    conditions = Conditions(
        model_name=payload.get("model", "unknown"),
        model_digest=suite_sha256(retrieval.INDEX_PATH),
        # Not a host. Saying "none" here is a fact about retrieval, not a
        # missing value - it reads a local index and talks to nothing. The index
        # is GITIGNORED (derived data, rebuilt from the committed runbooks); this
        # said "committed index" until 2026-09-29 - found by the first CI run,
        # which had no index at all. tests/test_scorer_table.py checks it.
        host_target=HOST_TARGET_RETRIEVAL,
        # null is a FACT about retrieval: it embeds text and generates none.
        sampling=None,
    )
    return score, conditions


# --------------------------------------------------------------------------
# agent
# --------------------------------------------------------------------------

def agent_row(case: dict[str, Any], scored: Any, answer: str) -> CaseRow:
    """Turn score.Score into a CaseRow.

    **`passed` is `earned`, and that is a ruling, not a convenience.** `Score`
    carries three verdicts: `outcome` (the answer matched the expected facts),
    `trajectory` (the required calls were actually made) and `earned` (both).
    Lab 9 exists because a right answer can be reached by guessing, and Lab 8
    watched one happen - so the spine's single verdict has to be the one that
    requires the work. `outcome` and `trajectory` stay in the detail, where a
    failure can be told apart: right answer / no work reads differently from
    wrong answer / correct work, and the pass rate must not blur them.

    `answer` lives in the detail too. It is not a spine field, but it is the
    payload that makes a row investigable - especially on the `live` tier,
    where "case_11: fail" says nothing without the text that failed.
    """
    # JUDGE-ERROR GATING, sub-call (i), Jose: "because it still counts and
    # keeps track". The order is the ruling:
    #   trajectory failed        -> fail      (False AND anything is False, so
    #                                          the verdict never needed the
    #                                          judge; a judge error stays in
    #                                          `note` and is still counted)
    #   judge raised             -> unscored  (the verdict DEPENDED on it)
    #   otherwise                -> earned ? pass : fail
    if not scored.trajectory:
        result = "fail"
    elif not scored.judged:
        result = "unscored"
    else:
        result = "pass" if scored.earned else "fail"
    return CaseRow(
        id=case["id"], tier=case["tier"], result=result,
        detail={"outcome": bool(scored.outcome),
                "trajectory": bool(scored.trajectory),
                "judged": bool(scored.judged),
                "missing": list(scored.missing),
                "extra": scored.extra,
                "answer": answer,
                # THE PAIR THAT MAKES AN OUTCOME FAILURE ATTRIBUTABLE, added
                # after the first real run of the agent suite. Five cases
                # failed on `outcome` with answer text that read correct, and
                # from the row alone there was no way to tell whether the AGENT
                # had the facts wrong or the JUDGE had failed to read a correct
                # answer. Those are different defects - the second is the
                # false-fail rate Lab 10 exists to measure - and `outcome:
                # False` cannot separate them.
                #
                # Stored side by side and NOT compared here: the comparison is
                # score.compare_facts()'s job and its verdict is `outcome`
                # above. Writing a second opinion beside it would be two
                # answers to one question.
                "expected": case.get("expected_facts"),
                "extracted": scored.actual},
        note=scored.note,
    )


def default_agent_runner(case: dict[str, Any]) -> Any:
    """Run the agent against the in-process fake host. Build step 3 part 2c.

    TWO THINGS THIS DELIBERATELY DOES NOT DO, and both are the point.

    **It passes no `guard`.** `agent.run()` defaults to `Guard(READ_ONLY)`,
    whose tool list is - per agent.py's own comment - byte-identical to the
    prompt Labs 8, 9 and 10 measured, "which is what lets those numbers stand".
    Passing a custom guard here would change the prompt and silently break
    comparability with every number those labs published. The default approver
    is `deny_all`, so no write can execute and **no approval prompt can hang an
    unattended run** - which is what a harness in CI requires.

    **It resets the fake host before every case.** Under READ_ONLY nothing can
    mutate `tools.SERVICES` today, so this changes no result now. It is here
    because ADR 0006 defines the `fixed` tier as a capture "replayed
    hermetically", and hermetic has to be STRUCTURAL rather than a side effect
    of a default that might later be widened. The first write-tier case to
    arrive would otherwise leak case 5's state into case 12, and the symptom
    would be a suite whose verdict depends on case order.
    """
    import agent
    import tools

    tools.reset_services()
    return agent.run(case["question"])


def make_agent_scorer(*, runner: Callable[[dict[str, Any]], Any] | None = None,
                      judge: Any = None,
                      suite: Suite | None = None, tier: str | None = None) -> tuple[ScorerFn, Conditions]:
    """Build the agent scorer. `runner` is INJECTABLE and defaults to the real one.

    `runner(case) -> AgentResult` is Lab 8's `runner=` seam arriving at the
    harness: the scorer does not know how the agent is invoked, so the check
    drives this whole path with a scripted run and no model at all. A scorer
    that needs a live model to be tested is a scorer nobody runs.

    `judge=None` means score_case's own default (the live fact extractor), kept
    as None here rather than imported at module scope so that building this
    scorer does not stand up a model client before anything has been measured.

    The digest is read from Ollama and **raises** if it cannot be - see
    model_client.model_digest(). An injected `runner` means the caller is
    driving this with a fixture, so the conditions say so rather than reaching
    for a live digest a scripted run never used.
    """
    from score import check_vocabulary, score_case   # literal module name - see the docstring

    # P04 scorer defect 2: the judge's form is closed over the SUITE's declared
    # vocabulary. A real measurement REFUSES to run without it (ValueError ->
    # exit 2), and the check runs BEFORE the model digest is read, so a suite
    # missing its vocabulary is refused without touching the network. Scripted
    # fixtures may omit the suite and keep the old free-string form.
    vocabulary = None
    if suite is not None or runner is None:
        if suite is None:
            raise ScorerError("the agent scorer needs its suite (for the vocabulary) "
                              "to run a real measurement")
        vocabulary = check_vocabulary(suite.header.vocabulary, suite.cases)

    invoke = default_agent_runner if runner is None else runner

    def score(case: dict[str, Any]) -> CaseRow:
        result = invoke(case)
        scored = (score_case(case, result, vocabulary=vocabulary) if judge is None
                  else score_case(case, result, judge=judge, vocabulary=vocabulary))
        return agent_row(case, scored, getattr(result, "answer", "") or "")

    if runner is None:
        import model_client
        name = os.environ.get("LOCAL_MODEL", model_client.DEFAULT_MODEL)
        conditions = Conditions(model_name=name,
                                model_digest=model_client.model_digest(name),
                                host_target="fake host (src/tools.py, in-process)",
                                sampling=model_client.effective_sampling())
    else:
        # A fixture is not a measurement, and the record must not be able to
        # pass itself off as one. A record written from a scripted run says so
        # in the field that decides comparability.
        conditions = Conditions(model_name="scripted",
                                model_digest="scripted-runner-no-model",
                                host_target="fixture",
                                sampling=None)
    return score, conditions


# --------------------------------------------------------------------------
# answer  (Project 2, build step 6c - ADR 0025)
# --------------------------------------------------------------------------

# The two honest refusals. Which LAYER refused is detail, not verdict: the
# threshold is a pre-filter (ADR 0023), so an unanswerable question refused by
# retrieval and one refused by the model are the same correct behaviour.
HONEST_REFUSALS = ("refused-no-retrieval", "refused-not-in-sources")
HOST_TARGET_ANSWER = "none - local index (data/index.json, gitignored) + Ollama generation"


def answer_row(case: dict[str, Any], a: Any, warnings: list[str]) -> CaseRow:
    """Turn an answer.Answer into a CaseRow. Pure - testable with no model.

    The gate is MECHANICAL (ADR 0025 part 2): set membership and string
    equality, so the same reply always gets the same verdict.
      answerable    pass = outcome answered AND the expected chunk is cited
      unanswerable  pass = outcome is an honest refusal (either layer)
    refused-citation is never a pass: "could not trace an answer" is not
    "correctly declined to answer". Faithfulness is not judged here (part 3).
    """
    expect = case.get("expect")
    if expect:
        passed = a.outcome == "answered" and expect in a.cited
    else:
        passed = a.outcome in HONEST_REFUSALS
    return CaseRow(
        id=case["id"], tier=case["tier"], result="pass" if passed else "fail",
        detail={"category": case.get("category"),
                "expect": expect,
                "outcome": a.outcome,          # a STRING - report.py tallies it
                "attempts": a.attempts,
                "cited": list(a.cited),
                "retrieved": [cid for cid, _ in a.retrieved],
                "top_score": round(a.retrieved[0][1], 4) if a.retrieved else None,
                "errors": list(a.errors),
                "warnings": list(warnings),
                "answer": a.text},
    )


def make_answer_scorer(*, generate: Callable[..., str] | None = None,
                       retriever: Callable[[str], list] | None = None,
                       manifest: Any = None,
                       suite: Suite | None = None, tier: str | None = None) -> tuple[ScorerFn, Conditions]:
    """Build the answer scorer. `generate` and `retriever` are INJECTABLE.

    Real run: the index is loaded once, retrieval embeds through Ollama and
    qwen3 answers at ANSWER_NUM_CTX. Injected: a scripted run, and the
    conditions say so (the agent scorer's rule - a fixture never poses as a
    measurement).

    The digest names BOTH artifacts this scorer reads: the generating model,
    and the index (re-indexing an edited corpus moves this measurement while
    the model's digest sits still - the retrieval scorer's lesson).

    A SchemaError on a case is a FAILED row, not a stall: the model was
    reached and answered, and the answer was unusable - a quality failure of
    that case. A ModelError propagates (the harness's stall path). A
    ContextBudgetError propagates too: an over-long prompt is OUR
    configuration, so the run is refused (exit 2), never scored.
    """
    import answer as answer_mod            # literal module names - see the docstring
    from structured import SchemaError

    if manifest is None:
        from manifest import load_manifest
        manifest = load_manifest()

    if retriever is None:
        import retrieval
        loaded = retrieval.load_index()

        def retriever(question: str) -> list:
            return retrieval.retrieve(question, k=3, index=loaded)

    def score(case: dict[str, Any]) -> CaseRow:
        hits = retriever(case["question"])
        try:
            a = answer_mod.answer(case["question"], hits=hits, generate=generate)
        except SchemaError as exc:
            a = answer_mod.Answer(case["question"], "schema-error", "", [], 1,
                                  [(h.chunk.chunk_id, h.score) for h in hits], [str(exc)])
        warnings = (list(answer_mod.render(a, manifest).warnings)
                    if a.outcome == answer_mod.ANSWERED else [])
        return answer_row(case, a, warnings)

    if generate is None:
        import model_client
        import retrieval
        name = os.environ.get("LOCAL_MODEL", model_client.DEFAULT_MODEL)
        # A MEASURED RUN STARTS ON A FRESH LOAD (step 6c finding; see
        # model_client.unload). Here, in the factory, so it happens once,
        # before the first case - and only on the real path: a scripted run
        # has no model to unload.
        model_client.unload(name)
        index_hash = suite_sha256(retrieval.INDEX_PATH)
        conditions = Conditions(
            model_name=f"{name} + index",
            model_digest=f"{model_client.model_digest(name)} index:{index_hash}",
            host_target=HOST_TARGET_ANSWER,
            sampling={**model_client.effective_sampling(),
                      "num_ctx": answer_mod.ANSWER_NUM_CTX})
    else:
        conditions = Conditions(model_name="scripted",
                                model_digest="scripted-runner-no-model",
                                host_target="fixture", sampling=None)
    return score, conditions


# --------------------------------------------------------------------------
# review (Project 3, build step 7)
# --------------------------------------------------------------------------
HOST_TARGET_REVIEW = "none - local Ollama; scripts are read as text, never executed"


def review_row(case: dict[str, Any], row: dict[str, Any], final_text: str, original: str) -> CaseRow:
    """Turn one pipeline run (review_loop.loop_item's row) into a CaseRow. Pure.

      bugged   pass = terminal needs-human: the pipeline STOPPED it. "Fixed" is not credited here -
               it needs a Windows linter reading (the step-5 two-pass score), which a harness
               run does not have; an approved bugged script is a fail, which is what shipping
               it broken is.
      clean    pass = approved (either way) AND byte-unchanged. A rewritten control cannot
               pass mechanically: on development 2/2 rewrites were broken by hand audit while
               lint and the planted oracles saw nothing (ADR 0032 am. 1).
      error    unscored, with the error as the note - never a pass, never a fail.
    """
    terminal = row["terminal"]
    detail = {"kind": case["kind"], "terminal": terminal, "stop": row["stop"],
              "rounds": row["rounds"], "changed": final_text != original, "calls": row["calls"],
              "round0": row.get("round0"), "escalation": row.get("escalation"),
              "completion_tokens": sum(c.get("completion_tokens") or 0 for c in row["trace"]),
              "model_seconds": round(sum(c.get("duration_s") or 0 for c in row["trace"]), 1)}
    if terminal == "error":
        return CaseRow(id=case["id"], tier=case["tier"], result="unscored", detail=detail,
                       note=f"pipeline error: {row.get('error', '')}"[:300])
    if case["kind"] == "defect":
        passed = terminal == "needs-human"
    else:
        passed = terminal.startswith("approved") and final_text == original
    return CaseRow(id=case["id"], tier=case["tier"], result="pass" if passed else "fail", detail=detail)


def make_review_scorer(*, run_item: Callable[..., Any] | None = None,
                       suite: Suite | None = None, tier: str | None = None) -> tuple[ScorerFn, Conditions]:
    """Build the pipeline scorer. `run_item(item) -> (row, final_text)` is INJECTABLE.

    A case is found by id in the committed set; its text must still hash to the suite's
    `text_sha256`, or the case is refused - a script edited after the suite was written is a
    different case. A held-out id is refused outright: held-out is read once, by
    review_runner --final (ADR 0028), never through the harness.

    Real run: the three models' digests are read in the factory, so a server that is down
    refuses the run before the first case (exit 2), instead of scoring 32 unscored rows.
    """
    import hashlib
    import review as review_mod            # literal module names - see the docstring
    import review_loop

    items = {it.id: it for it in review_mod.load_set()}

    def score(case: dict[str, Any]) -> CaseRow:
        it = items.get(case["id"])
        if it is None or it.split != "development":
            raise ScorerError(f"review case {case['id']!r} is not a development item - refused (ADR 0028)")
        original = it.text()
        if hashlib.sha256(original.encode()).hexdigest() != case["text_sha256"]:
            raise ScorerError(f"review case {case['id']!r}: the script no longer matches the suite's text_sha256")
        row, final_text = (run_item or review_loop.loop_item_pipeline)(it)
        return review_row(case, row, final_text, original)

    if run_item is None:
        import model_client
        import review_runner as RR
        models = (review_loop.CRITIC, review_loop.REVISER, review_loop.ESCALATOR)
        conditions = Conditions(
            model_name=" + ".join(models),
            model_digest=" ".join(f"{m}:{model_client.model_digest(m)[:12]}" for m in models),
            host_target=HOST_TARGET_REVIEW,
            sampling={**model_client.effective_sampling(), "num_ctx": RR.NUM_CTX,
                      "max_tokens": RR.REVIEW_MAX_TOKENS, "timeout_s": RR.REVIEW_TIMEOUT,
                      "max_rounds": review_loop.MAX_ROUNDS})
    else:
        conditions = Conditions(model_name="scripted", model_digest="scripted-runner-no-model",
                                host_target="fixture", sampling=None)
    return score, conditions


# --------------------------------------------------------------------------
# remediation (Project 5, build step 5)
# --------------------------------------------------------------------------
HOST_TARGET_REMEDIATION = "fake target (FakeHost, in memory) - no machine is read or written"
HOST_TARGET_SRV01 = ("SRV01 10.10.10.20 READ-ONLY - AgentReader as CORP\\svc-agent-ro "
                     "(SrvReader); nothing approved or written")
LIVE_CHECKS = ("detect", "parity")
STOPPED = "stopped-before-apply"
SABOTAGE = {"none": False, "broken": "broken", "broken-rollback": "broken-rollback"}

# What a suite case may expect: the five outcomes, plus the one result that is not an outcome -
# the agent STOPPED because its record could not be written (ADR 0039: exit 2, nothing applied).
LiveText = Callable[[dict[str, Any], Any], str]


def _virtual_clock() -> tuple[Callable[[], float], Callable[[float], None]]:
    """A clock that only moves when the agent sleeps. The fake target settles instantly, so a
    real clock would only make a failing case wait out its whole ceiling (30 s for a service)
    in CI and on the bench. Same reads, same verdict, no waiting."""
    now = [0.0]
    return (lambda: now[0]), (lambda s: now.__setitem__(0, now[0] + s))


def stopped_problems(lines: list[dict[str, Any]]) -> list[str]:
    """The audit half of the gate for a STOPPED run: there is no outcome line to grade, so the
    record must prove the stop - nothing past the write-ahead line, every line attributed."""
    import audit as A
    problems = []
    if [ln.get("seq") for ln in lines] != list(range(1, len(lines) + 1)):
        problems.append("seq is not 1..n")
    problems += [f"seq {ln.get('seq')}: who={ln.get('who')!r}" for ln in lines
                 if ln.get("who") not in A.WHO]
    problems += [f"a stopped run recorded {ln['event']!r}" for ln in lines
                 if ln.get("event") in ("intent", "applied", "outcome")]
    return problems


def _srv01_session_factory() -> Callable[[], Any]:
    """The real way into SRV01 for the `live` tier (P05 build step 7).

    The password is asked for ONCE, the first time an SRV01 case runs, and held in this
    closure only - never a file, an environment variable, chat or git (ADR 0002; Jose's pick
    2026-10-20). `--tier fixed` never reaches here, so CI never asks. Each case opens its own
    session (a context manager) and the endpoint drift check runs on every one of them.
    """
    import getpass
    import host_client as H
    import target as T
    held: dict[str, str] = {}

    class _Checked:
        def __init__(self) -> None:
            if "pw" not in held:
                held["pw"] = getpass.getpass(f"Password for {T.VERIFIER_ACCOUNT} "
                                             f"(SRV01 live tier, asked once): ")
            self._host = H.Host(T.SRV01_SERVER, T.VERIFIER_ACCOUNT, held["pw"],
                                allowed=H.ALLOWED_SRV01)

        def __enter__(self) -> Any:
            session = self._host.__enter__()
            session.check_drift()
            return session

        def __exit__(self, *exc: object) -> None:
            self._host.__exit__(*exc)

    return _Checked


def make_remediation_scorer(*, live_text: LiveText | None = None,
                            suite: Suite | None = None, tier: str | None = None,
                            srv01_session: Callable[[], Any] | None = None
                            ) -> tuple[ScorerFn, Conditions]:
    """Build the remediation scorer (ADR 0039).

    A case plants ONE drift on a fresh fake target, the proposer is asked, a scripted approver
    answers, and remediate() runs - the same run_one() the agent CLI calls. PASS iff:

      1. the outcome equals `expected_outcome`; and
      2. the audit log is complete (audit.check_complete) - or, for a STOPPED case, proves the
         stop (stopped_problems) and the machine is byte-for-byte as it was before the run.

    right_fix is REPORTED in detail, never gated (ADR 0039, E-A).

    Where the proposal comes from:
      model = {"reply": "..."}  that exact text, in every home - an off-menu or wrong-argument
                                 proposal is a scenario, not something to hope the model does.
      model = "live"            the real qwen3:8b on the bench; in CI, `live_text(case, drift)`
                                 stands in (INJECTABLE - the scripted model of ADR 0039).

    Refused, never scored (ScorerError -> exit 2): an expected value we do not have, a planted
    drift that detect() does not see as exactly that one item, an expected_call that disagrees
    with the committed baseline.

    THE `live` TIER (build step 7, ADR 0039): `target: "srv01"` cases, READ-ONLY. Nothing is
    proposed, approved or written - the remediator is never built. Two checks per class:
      detect  SRV01's reading of the item equals the baseline (pass), or names the drifted
              fields (fail - reported, never gated: ADR 0006)
      parity  the SAME read through the fake and through SRV01 has the same fields, the same
              types, and an SRV01 value the fake can represent (inside target.ALLOWED) - so CI's
              every-push proof on the fake is a proof about a machine shaped like SRV01
    `fake` <=> `fixed` and `srv01` <=> `live`: a case on the wrong tier is refused. SRV01
    unreachable, endpoint drift, or a malformed reading -> ScorerError -> exit 2 (ADR 0008:
    a selected-but-unreachable live case is never a skip). `srv01_session` is INJECTABLE
    (a zero-argument callable returning a context manager that yields the session).
    """
    import audit as A                          # literal module names - see the docstring
    import model_client
    import proposer as P
    import remediate as R
    import target as T

    baseline = T.load_baseline()
    expected_values = (*R.OUTCOMES, STOPPED)

    def generator(case: dict[str, Any], drift: Any) -> Any:
        model = case["model"]
        if isinstance(model, dict):
            text = model["reply"]
        elif model == "live" and live_text is not None:
            text = live_text(case, drift)
        elif model == "live":
            return P.default_generate
        else:
            raise ScorerError(f"remediation case {case['id']!r}: model must be 'live' or "
                              f"{{'reply': ...}}, got {model!r}")
        return lambda messages, schema: model_client.Reply(
            text=text, thinking="", model="scripted", prompt_tokens=None,
            completion_tokens=None, duration_s=0.0, raw={})

    open_srv01 = srv01_session

    def score_live(case: dict[str, Any]) -> CaseRow:
        nonlocal open_srv01
        cid, item, check = case["id"], case.get("item"), case.get("check")
        if check not in LIVE_CHECKS:
            raise ScorerError(f"remediation case {cid!r}: check {check!r} not in {LIVE_CHECKS}")
        watched = {w.id: w for w in baseline}.get(item)
        if watched is None or watched.cls != case.get("class"):
            raise ScorerError(f"remediation case {cid!r}: {item!r} is not a watched "
                              f"{case.get('class')!r} item")
        if open_srv01 is None:
            open_srv01 = _srv01_session_factory()
        try:
            with open_srv01() as session:
                observed = T.SrvReader(session, baseline).read(item)
        except Exception as exc:                 # host, drift, identity, malformed reading
            raise ScorerError(f"remediation case {cid!r}: SRV01 could not be read "
                              f"({type(exc).__name__}: {exc})") from exc
        if check == "detect":
            drifted = sorted(f for f in watched.fields if observed.get(f) != watched.fields[f])
            passed = not drifted
            detail = {"class": watched.cls, "check": check, "observed": observed,
                      "baseline": dict(watched.fields), "drifted_fields": drifted}
            note = "" if passed else f"SRV01 drifted on {drifted}: {observed}"
        else:
            fake_read = T.fake_target(baseline)[0].reader.read(item)
            problems = []
            if sorted(observed) != sorted(fake_read):
                problems.append(f"fields {sorted(observed)} != fake {sorted(fake_read)}")
            for f in sorted(set(observed) & set(fake_read)):
                if type(observed[f]) is not type(fake_read[f]):
                    problems.append(f"{f}: {type(observed[f]).__name__} != fake "
                                    f"{type(fake_read[f]).__name__}")
                if observed[f] not in T.ALLOWED[watched.cls].get(f, ()):
                    problems.append(f"{f}={observed[f]!r} cannot be represented by the fake")
            passed = not problems
            detail = {"class": watched.cls, "check": check, "srv01": observed,
                      "fake": fake_read, "problems": problems}
            note = "" if passed else problems[0]
        return CaseRow(id=cid, tier=case["tier"], result="pass" if passed else "fail",
                       detail=detail, note=note[:300])

    def score(case: dict[str, Any]) -> CaseRow:
        cid = case["id"]
        pairing = {"fake": "fixed", "srv01": "live"}
        if pairing.get(case.get("target")) != case.get("tier"):
            raise ScorerError(f"remediation case {cid!r}: target {case.get('target')!r} on tier "
                              f"{case.get('tier')!r} - fake runs `fixed`, srv01 runs `live`")
        if case["target"] == "srv01":
            return score_live(case)
        expected = case["expected_outcome"]
        if expected not in expected_values:
            raise ScorerError(f"remediation case {cid!r}: expected_outcome {expected!r} not in "
                              f"{expected_values}")
        if case["sabotage"] not in SABOTAGE:
            raise ScorerError(f"remediation case {cid!r}: sabotage {case['sabotage']!r} unknown")

        tgt, host = T.fake_target(baseline, broken=SABOTAGE[case["sabotage"]])
        host.set(case["item"], **case["drift"])                  # the console: the break
        drifts = R.detect(tgt, baseline)
        if [d.item for d in drifts] != [case["item"]]:
            raise ScorerError(f"remediation case {cid!r}: planted {case['item']} but detect() "
                              f"found {[d.item for d in drifts]}")
        drift = drifts[0]
        want = T.Call(**case["expected_call"])
        if P.expected_call(drift) != want:
            raise ScorerError(f"remediation case {cid!r}: expected_call disagrees with the "
                              f"committed baseline ({P.expected_call(drift)})")

        machine_before = host.snapshot()
        clock, sleep = _virtual_clock()
        log = A.MemoryAudit(run_id=cid, clock=clock, fail_after=case.get("audit_fail_after"))
        approve = (lambda **k: True) if case["approver"] is True else (lambda **k: False)
        proposal = None
        try:
            res, proposal = P.run_one(tgt, baseline, drift, approve, audit=log,
                                      generate=generator(case, drift), clock=clock, sleep=sleep)
            outcome, reasons = res.outcome, list(res.reasons)
        except A.AuditError as exc:
            outcome, reasons = STOPPED, [str(exc)]
        except P.ModelUnavailable as exc:
            # The model boundary failed: the harness's STALL path (partial record, exit 2) -
            # not a failed case, and not a crash.
            raise model_client.ModelError(f"remediation case {cid!r}: {exc}") from exc

        if outcome == STOPPED:
            problems = stopped_problems(log.lines)
            if host.snapshot() != machine_before:
                problems.append("the machine changed although the agent stopped")
        else:
            problems = A.check_complete(log.lines)
        passed = outcome == expected and not problems
        call = proposal.call if proposal else None
        meta = proposal.meta if proposal else {}
        detail = {
            "class": case["class"], "sabotage": case["sabotage"],
            "model": "scripted" if isinstance(case["model"], dict) else
                     ("stand-in" if live_text is not None else "live"),
            "expected": expected, "outcome": outcome,
            "right_fix": P.right_fix(call, drift) if proposal else None,
            "call": {"function": call.function, "item": call.item, "values": call.values}
                    if call else None,
            "audit_problems": problems, "audit_lines": len(log.lines),
            "reasons": [r[:200] for r in reasons[:4]],
            "completion_tokens": meta.get("completion_tokens"),
            "model_seconds": meta.get("seconds"),
        }
        note = "" if passed else (f"expected {expected}, got {outcome}" if outcome != expected
                                  else f"audit incomplete: {problems[0]}")
        return CaseRow(id=cid, tier=case["tier"], result="pass" if passed else "fail",
                       detail=detail, note=note[:300])

    # WHAT THIS RUN TOUCHES decides the conditions (build step 7). `tier` is the harness's
    # selection; None (a direct call) means "the whole suite", as before step 7.
    chosen = [c for c in (suite.cases if suite is not None else [])
              if tier in (None, "all") or c.get("tier") == tier]
    reads_srv01 = any(c.get("target") == "srv01" for c in chosen)
    uses_model = suite is None or any(c.get("target") == "fake" and c.get("model") == "live"
                                      for c in chosen)
    fake_part = any(c.get("target") == "fake" for c in chosen) or suite is None
    host_target = " + ".join(t for t, on in ((HOST_TARGET_REMEDIATION, fake_part),
                                             (HOST_TARGET_SRV01, reads_srv01)) if on)
    if live_text is not None:
        conditions = Conditions(model_name="scripted", model_digest="scripted-runner-no-model",
                                host_target="fixture", sampling=None)
    elif not uses_model:
        # A read-only live run asks no model anything: it must not need Ollama up, and must
        # not record a digest it never used.
        conditions = Conditions(model_name="none (read-only live tier)",
                                model_digest="no-model-called", host_target=host_target,
                                sampling=None)
    else:
        conditions = Conditions(
            model_name=P.MODEL,
            model_digest=model_client.model_digest(P.MODEL),    # server down -> exit 2, now
            host_target=host_target,
            sampling={**model_client.effective_sampling(), "num_ctx": P.NUM_CTX,
                      "max_tokens": P.MAX_TOKENS, "timeout_s": P.TIMEOUT})
    return score, conditions


# --------------------------------------------------------------------------
# command-center (Project 6, build step 3)
# --------------------------------------------------------------------------
HOST_TARGET_COMMAND_CENTER = ("FakeBench (in memory) + a fake job table - no machine, no "
                              "Hyper-V, no model is touched")
CC_KINDS = ("promote", "route")


def make_command_center_scorer(*, suite: Suite | None = None, tier: str | None = None
                               ) -> tuple[ScorerFn, Conditions]:
    """Build the command-center scorer (ADR 0044's CI suite on fakes).

    `promote` cases run promote.run() - the SAME function the job's CLI calls - against a fresh
    fake_bench.FakeBench in a scratch directory. PASS iff ALL of:

      1. the exit code equals `expected_exit`;
      2. the run record, RE-READ FROM DISK, says `expected_state` and `expected_live_started`
         (ADR 0044: the code is decided by the record, so the record is graded too);
      3. the actions on the machine, in order, equal `expected_calls` - what was NOT done is
         asserted as hard as what was (a blocked script is never dry-run);
      4. the invariant for that code holds on the fake machine:
           0  promoted/<stem>-<sha8>/ holds the script and evidence.json; record says where
           1  the machine is exactly as it was before the run (untouched, or restored)
           2  the machine is unchanged AND the live run never happened
           3  the record says the live run began (the only way 3 can be honest)

    `route` cases run command_center.main() with a fake job table whose child exits with the
    case's code (pass-through, including 3), an unknown job (argparse 2, nothing run, no
    record), and the REAL table's `promote` row with the reviewer unreachable (2; its own record
    says not-started). That case pins OLLAMA_BASE_URL to a dead port for its child, so on the
    bench - where Ollama IS up - it still stops at the review, before any vault prompt. Model digests are injected - no Ollama.

    Nothing here samples a model, so the conditions say so; there is no `live` tier.
    """
    import shutil
    import sys
    import tempfile
    from pathlib import Path
    import command_center as cc
    import fake_bench as FB
    import promote as PR

    def fresh_records(runs: Path, kind: str) -> list[dict[str, Any]]:
        # By the record's own `kind`, never by file name: "-promote-" also matches the
        # orchestrator's "...-cc-promote-..." record (found on the first run - the same glob
        # trap as build step 2's).
        out = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(runs.glob("*.json"))]
        return [r for r in out if r.get("kind") == kind]

    def score_promote(case: dict[str, Any], tmp: Path) -> CaseRow:
        cid = case["id"]
        script = tmp / "change.ps1"
        script.write_text(case["script"], encoding="utf-8", newline="\n")
        bench = FB.FakeBench(start=case.get("start"), effect=case.get("effect"),
                             faults=case.get("faults"))
        machine_before = dict(bench.machine)
        runs, promoted = tmp / "runs", tmp / "promoted"
        typed = case.get("typed")                    # the override screen's input (step 5)
        override = (lambda review: PR.parse_override(typed)) if typed is not None else None
        code = PR.run(script, bench, runs=runs, promoted=promoted, override=override)
        recs = fresh_records(runs, "promote-run")
        problems: list[str] = []
        if len(recs) != 1:
            problems.append(f"{len(recs)} promote records, expected 1")
        rec = recs[0] if recs else {}
        if code != case["expected_exit"]:
            problems.append(f"exit {code}, expected {case['expected_exit']}")
        if rec.get("exit_code") != code:
            problems.append(f"record exit_code {rec.get('exit_code')} != returned {code}")
        if rec.get("state") != case["expected_state"]:
            problems.append(f"record state {rec.get('state')!r}, expected "
                            f"{case['expected_state']!r}")
        if rec.get("live_started") is not case["expected_live_started"]:
            problems.append(f"record live_started {rec.get('live_started')}, expected "
                            f"{case['expected_live_started']}")
        if bench.calls != case["expected_calls"]:
            problems.append(f"calls {bench.calls} != expected {case['expected_calls']}")
        # A crash is the right code for the WRONG reason unless the case planted one. Found
        # on the first run: a TypeError in the record writer crashed the checkpoint step,
        # the last-resort catch answered 2, and two cases expecting 2 passed on the bug.
        crashed = [s for s in rec.get("steps", []) if s.get("step") == "crash"]
        planted_crash = "crash" in (case.get("faults") or {}).values()
        if crashed and not planted_crash:
            problems.append(f"the flow crashed: {crashed[0].get('error', '')[:200]}")
        if planted_crash and not crashed:
            problems.append("a crash was planted but the record shows none")
        accepted = typed is not None and PR.parse_override(typed) is not None
        if bool(rec.get("review_overridden")) is not accepted:
            problems.append(f"record review_overridden={rec.get('review_overridden')!r}, but the "
                            f"override was {'accepted' if accepted else 'not accepted'}")
        if accepted and code == PR.EXIT_PROMOTED:
            ev = Path(rec.get("promoted_to") or tmp / "missing") / "evidence.json"
            if ev.is_file() and not json.loads(ev.read_text(encoding="utf-8")).get("review_overridden"):
                problems.append("promoted after an override, but the evidence does not say so")
        changed = bench.changed(machine_before)
        if code == PR.EXIT_PROMOTED:
            dest = Path(rec.get("promoted_to") or tmp / "missing")
            if not ((dest / script.name).is_file() and (dest / "evidence.json").is_file()):
                problems.append("exit 0 but promoted/ lacks the script or evidence.json")
        elif code == PR.EXIT_NOT_PROMOTED and changed:
            problems.append(f"exit 1 but the machine differs from before the run: {changed}")
        elif code == PR.EXIT_NOTHING_CHANGED and (changed or "live_run" in bench.calls):
            problems.append(f"exit 2 but the machine changed {changed} or the live run ran")
        elif code == PR.EXIT_UNDESCRIBED and not rec.get("live_started"):
            problems.append("exit 3 but the record says the live run never began")
        if code != PR.EXIT_PROMOTED and promoted.exists() and any(promoted.iterdir()):
            problems.append("not promoted, yet promoted/ was written")
        detail = {"kind": "promote", "exit": code, "expected_exit": case["expected_exit"],
                  "state": rec.get("state"), "live_started": rec.get("live_started"),
                  "calls": bench.calls, "machine_changed": changed,
                  "reason": (rec.get("reason") or "")[:200], "problems": problems}
        return CaseRow(id=cid, tier=case["tier"], result="fail" if problems else "pass",
                       detail=detail, note=problems[0][:300] if problems else "")

    def score_route(case: dict[str, Any], tmp: Path) -> CaseRow:
        cid, problems = case["id"], []
        runs = tmp / "runs"
        digest, runtime = (lambda m: f"fake-digest-of-{m}"), (lambda: "fake-ollama")
        if case["job"] == "promote":
            script = tmp / "change.ps1"
            script.write_text("Get-Service -Name Spooler\n", encoding="utf-8")
            argv = ["--runs", str(runs), "promote", str(script),
                    "--runs", str(runs), "--promoted", str(tmp / "promoted")]
            jobs, repo = None, None
        else:
            (tmp / "child.py").write_text("import sys\nsys.exit(int(sys.argv[1]))\n",
                                          encoding="utf-8")
            jobs = {"fake-writes": cc.Job("PX", "child.py", True, "exits with argv[1]")}
            repo = tmp
            argv = ["--runs", str(runs), case["job"], str(case.get("child_exit", 0))]
        devnull = open(os.devnull, "w")
        saved, saved_url = sys.stderr, os.environ.get("OLLAMA_BASE_URL")
        if case["job"] == "promote":                 # the child inherits it: no live reviewer
            os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:9"
        try:
            sys.stderr = devnull                     # argparse's usage text is not evidence
            code = cc.main(argv, jobs=jobs, repo=repo, digest=digest, runtime=runtime)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
        finally:
            sys.stderr = saved
            devnull.close()
            if case["job"] == "promote":
                if saved_url is None:
                    os.environ.pop("OLLAMA_BASE_URL", None)
                else:
                    os.environ["OLLAMA_BASE_URL"] = saved_url
        if code != case["expected_exit"]:
            problems.append(f"exit {code}, expected {case['expected_exit']}")
        cc_recs = fresh_records(runs, "command-center-run")
        if case["expected_state"] is None:
            if runs.exists() and any(runs.iterdir()):
                problems.append("refused, yet something was written to runs/")
        else:
            if len(cc_recs) != 1:
                problems.append(f"{len(cc_recs)} command-center records, expected 1")
            elif cc_recs[0].get("state") != case["expected_state"] or \
                    cc_recs[0].get("child_returncode") != code:
                problems.append(f"record state {cc_recs[0].get('state')!r} / child "
                                f"{cc_recs[0].get('child_returncode')} vs exit {code}")
        if case.get("expected_child_state"):
            child = fresh_records(runs, "promote-run")
            if len(child) != 1 or child[0].get("state") != case["expected_child_state"] \
                    or child[0].get("live_started") is not False:
                problems.append(f"promote's own record: {[c.get('state') for c in child]}, "
                                f"expected {case['expected_child_state']!r}, live not started")
        detail = {"kind": "route", "job": case["job"], "exit": code,
                  "expected_exit": case["expected_exit"], "problems": problems}
        return CaseRow(id=cid, tier=case["tier"], result="fail" if problems else "pass",
                       detail=detail, note=problems[0][:300] if problems else "")

    def score(case: dict[str, Any]) -> CaseRow:
        if case.get("tier") != "fixed":
            raise ScorerError(f"command-center case {case['id']!r}: fakes run `fixed` only")
        kind = case.get("kind")
        if kind not in CC_KINDS:
            raise ScorerError(f"command-center case {case['id']!r}: kind {kind!r} not in "
                              f"{CC_KINDS}")
        if kind == "promote" and case["expected_exit"] not in (0, 1, 2, 3):
            raise ScorerError(f"command-center case {case['id']!r}: expected_exit "
                              f"{case['expected_exit']!r} is not a promote code")
        tmp = Path(tempfile.mkdtemp(prefix="cc-case-"))
        try:
            return (score_promote if kind == "promote" else score_route)(case, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    conditions = Conditions(model_name="none (fakes: scripted reviewer)",
                            model_digest="no-model-called",
                            host_target=HOST_TARGET_COMMAND_CENTER, sampling=None)
    return score, conditions


# --------------------------------------------------------------------------
# THE TABLE
# --------------------------------------------------------------------------
# Keys are the names a suite header may declare. Values are literals. Adding a
# scorer means adding a line here and a factory above - there is deliberately
# no other way in.
SCORERS: dict[str, ScorerFactory] = {
    "agent": make_agent_scorer,
    "answer": make_answer_scorer,
    "retrieval": make_retrieval_scorer,
    "review": make_review_scorer,
    "remediation": make_remediation_scorer,
    "command-center": make_command_center_scorer,
}


def resolve(suite: Suite) -> ScorerFactory:
    """The factory for this suite's declared scorer, or ScorerError.

    Two distinct refusals, and the message says which:

      the file declares `scorer: null`  -> it is not a suite at all (a fixtures
          file was handed to the harness), so there is nothing to resolve.
      the name is not in SCORERS        -> a suite naming a scorer we do not
          have. The message LISTS the names we do have, because the likeliest
          cause is a typo and a rejection nobody can read costs an evening.
    """
    header = suite.header
    if not header.runnable:
        raise ScorerError(
            f"{suite.path.name} declares scorer=null - it is not a runnable "
            "suite, so the harness has nothing to score it with"
        )
    if header.scorer not in SCORERS:
        raise ScorerError(
            f"{suite.path.name} declares scorer={header.scorer!r}, which is not "
            f"in the scorer table. Known scorers: {sorted(SCORERS)}"
        )
    return SCORERS[header.scorer]
