#!/usr/bin/env python3
"""Two agents, and the question of whether the second one earns its keep.

Lab 4 built one loop: think, act, observe, stop. It works. Everything in
this file is an ARGUMENT for adding a second model to it, and an argument is
what it has to be - a second agent is a second place to be wrong, and it is
not free. It doubles the model calls and adds a handoff that can lose
information.

So it only pays when it removes a constraint one agent cannot remove:

  - different CONTEXT   - one agent per domain, so no prompt carries 40 tools
  - different AUTHORITY - the second agent's job is to REJECT
  - different FAILURE   - a deterministic checker beside a stochastic writer

This module builds the second one, because it is the one that can be
measured: a worker answers, a reviewer reads that answer AGAINST THE
EVIDENCE and either approves it or sends it back with named issues, for a
bounded number of rounds. The smoke test prints what that cost.

What this is NOT is a crew of six agents with job titles. Same model, same
tools, same context, a new name on top buys variance, not verification.

    python src/team.py; echo "exit: $?"
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from agent import AgentResult, run
from memory import Memory
from structured import ask_json


@dataclass
class Draft:
    """What crosses between the two agents. An interface, not a shared mind.

    The reviewer gets three things:

      question  - what was actually asked, because an answer can only be
                  judged against its question
      answer    - what the worker concluded
      evidence  - what the run OBSERVED, with provenance

    And one thing it deliberately does NOT get: the worker's transcript. Not
    its reasoning, not its failed attempts, not the order it tried things in.
    Hand a reviewer the argument before the exhibits and you get agreement,
    not review.

    The separation that makes two agents worth having at this scale is
    CONTEXT, not weights. The same model on both sides is fine. The same
    context is not.
    """

    question: str
    answer: str
    evidence: str          # Memory.render() - facts with tool, arguments, time
    steps: int             # what this draft cost, carried for the ledger


def draft_of(question: str, result: AgentResult) -> Draft:
    """Package a finished run as the thing the reviewer will read.

    Note what is NOT written here: any new rendering of the evidence. Lab 5
    already built exactly that and called it Memory.render(). Reaching for it
    instead of writing a second renderer is the same move this course has
    made three times now - use the lower layer, do not reimplement it one
    floor up, where it will drift.
    """
    memory = result.memory if result.memory is not None else Memory()
    return Draft(
        question=question,
        answer=result.answer or "",
        evidence=memory.render(),
        steps=len(result.steps),
    )


# --- the reviewer ---------------------------------------------------------


def verdict_schema() -> dict[str, Any]:
    """The shape of one review. Both fields required, unlike step_schema().

    Lab 4's step schema required only the discriminator, because the field
    beside it depended on which branch you were in. Here both branches use
    both fields - an approval simply carries an empty issues list - so there
    is no reason to let one go missing.
    """
    return {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["approve", "revise"]},
            "issues": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["verdict", "issues"],
        "additionalProperties": False,
    }


REVIEWER = """You are a reviewer. You did not write the answer below, and you
are not answering the question yourself.

You are shown the question, the answer somebody else gave, and the EVIDENCE:
every fact that was actually observed by running a tool. Judge the answer
against that evidence and nothing else. Do not use your own knowledge of
Windows to fill a gap - a gap IS the finding.

Return "revise" if any of these is true:
  - the answer states something the evidence does not support
  - the answer contradicts the evidence
  - the answer leaves part of the question unanswered
  - the evidence is not sufficient to answer the question at all

Otherwise return "approve".

Every issue you name must be specific and fixable: name the claim and say
what is wrong with it. "Could be clearer" is not an issue. If you return
"revise" you must name at least one issue. If you approve, return an empty
issues list.

Approving a wrong answer is the expensive mistake here, because it ships.
Asking for a revision that turns out to be unnecessary costs one more round.
When you are genuinely unsure, revise."""


REVIEW_REQUEST = """QUESTION
{question}

ANSWER UNDER REVIEW
{answer}

EVIDENCE
{evidence}"""


def review(draft: Draft, **kwargs: Any) -> dict[str, Any]:
    """Judge one draft against its evidence. Returns {verdict, issues}.

    Note the empty-evidence fallback below. Memory.render() returns "" when
    nothing was observed, which was the right call at Lab 5 - an empty
    heading is an invitation to invent. But a blank EVIDENCE block reads to a
    reviewer as "nothing to check here" when what it actually means is "this
    answer is entirely unsupported". Those are opposite verdicts, so the
    absence is stated out loud rather than left as whitespace.
    """
    prompt = REVIEW_REQUEST.format(
        question=draft.question,
        answer=draft.answer,
        evidence=draft.evidence or "(no tool was run, so there is no evidence)",
    )
    return ask_json(prompt, verdict_schema(), system=REVIEWER, **kwargs)


def actionable(verdict: dict[str, Any]) -> bool:
    """Can the worker DO anything with this verdict?

    The schema guarantees that `issues` exists. It cannot guarantee the list
    has anything in it, so a model may return "revise" with nothing named.
    That is not a revision request - it is a rejection the worker cannot act
    on, and sending it round the loop again buys another model call and no
    new information. Our half of the bargain, again.
    """
    return verdict["verdict"] != "revise" or bool(verdict["issues"])


# --- the loop -------------------------------------------------------------


REVISION = """{question}

You already answered this question once, and a reviewer REJECTED your answer.

YOUR PREVIOUS ANSWER
{answer}

WHAT THE REVIEWER SAYS MUST BE FIXED
{issues}

Answer the original question again, correcting those issues. Everything you
observed last time is listed above under KNOWN FACTS - do not spend a step
re-running a tool unless the reviewer's objection is about that fact."""


@dataclass
class TeamResult:
    """How the collaboration ended.

    `ok` means APPROVED. Not "finished", not "an answer came back" - approved
    by the reviewer. Every other exit is ok=False even when an answer is
    attached, because a caller must never have to guess whether the thing it
    is holding passed review. Cleared to test is not cleared to ship.
    """

    ok: bool
    answer: str | None
    reason: str                 # "approved" | "rounds" | "unactionable" | "worker"
    rounds: int
    drafts: list[Draft] = field(default_factory=list)
    reviews: list[dict[str, Any]] = field(default_factory=list)
    model_calls: int = 0        # the ledger - see collaborate()
    memory: Memory | None = None


def collaborate(question: str, *, max_rounds: int = 2, max_steps: int = 8,
                memory: Memory | None = None,
                reviewer: Callable[..., dict[str, Any]] = review,
                **kwargs: Any) -> TeamResult:
    """Worker answers, reviewer judges, worker revises. Bounded.

    THE BOUND. Lab 4 put a step budget on the loop because a loop whose only
    exit is the model deciding it is done is an unbounded bill. A worker and
    a reviewer are that same hazard one floor up: the reviewer can always
    find one more thing, and the worker can always produce one more draft.
    max_rounds is the same fix applied to the same shape of problem.

    THE MEMORY. The rejected worker does not get its transcript back - it
    gets a fresh run with a revision brief. That would be expensive if it
    also re-derived every fact, so the Memory is carried across rounds and
    run() renders it into the second round's system prompt as prior
    knowledge. Lab 5 built that for the gap BETWEEN sessions; it turns out to
    pay again for the gap between a rejection and a retry.

    THE SEAM. `reviewer` is a parameter, not a hard-wired call. Production
    passes nothing and gets the real one. The smoke test passes a stub that
    always rejects, which is the only way to prove the bound stops the loop
    without waiting for a model to be stubborn on cue.

    THE LEDGER. model_calls counts every trip to the model: one per worker
    step, plus one per review. This is the number that decides whether the
    second agent was worth having, and it is counted rather than estimated
    because "roughly twice" is not a sentence you can defend in review.
    """
    mem = memory if memory is not None else Memory()
    drafts: list[Draft] = []
    reviews: list[dict[str, Any]] = []
    calls = 0
    brief = question

    for r in range(1, max_rounds + 1):
        result = run(brief, max_steps=max_steps, memory=mem, **kwargs)
        calls += len(result.steps)

        if not result.ok:
            # The worker ran out of steps and never produced an answer. There
            # is nothing to review, and reviewing nothing would burn a call to
            # be told so. Stop and say which layer failed - "the worker never
            # finished" and "the reviewer refused it" are different bugs.
            return TeamResult(ok=False, answer=None, reason="worker", rounds=r,
                              drafts=drafts, reviews=reviews,
                              model_calls=calls, memory=mem)

        draft = draft_of(question, result)
        drafts.append(draft)

        verdict = reviewer(draft, **kwargs)
        calls += 1
        reviews.append(verdict)

        if verdict["verdict"] == "approve":
            return TeamResult(ok=True, answer=draft.answer, reason="approved",
                              rounds=r, drafts=drafts, reviews=reviews,
                              model_calls=calls, memory=mem)

        if not actionable(verdict):
            # "revise", but nothing named. Another round would re-ask the same
            # question with no new information, so stop - and stop at ok=False,
            # because an answer nobody managed to approve has not been approved.
            return TeamResult(ok=False, answer=draft.answer,
                              reason="unactionable", rounds=r, drafts=drafts,
                              reviews=reviews, model_calls=calls, memory=mem)

        brief = REVISION.format(
            question=question,
            answer=draft.answer,
            issues="\n".join(f"  - {i}" for i in verdict["issues"]),
        )

    # Rounds exhausted with no approval. The last draft is handed back so the
    # caller can see what was on the table, flagged as what it is: unapproved.
    return TeamResult(ok=False, answer=drafts[-1].answer if drafts else None,
                      reason="rounds", rounds=max_rounds, drafts=drafts,
                      reviews=reviews, model_calls=calls, memory=mem)


# --- smoke test -----------------------------------------------------------
# FIVE properties. The reviewer is tested in BOTH directions before anything
# is built on it - one direction is not a test, because a reviewer hard-coded
# to "revise" passes a rejection test and one hard-coded to "approve" passes
# an approval test. Then the two termination guards are forced with stub
# reviewers, and only the last case spends a full run.

if __name__ == "__main__":
    import sys

    from tools import ToolResult

    failures: list[str] = []

    mem = Memory()
    mem.record(1, ToolResult(
        ok=True,
        tool="get_service_status",
        arguments={"name": "Spooler"},
        value={"name": "Spooler", "status": "stopped", "start_type": "automatic"},
    ))
    EVIDENCE = mem.render()
    QUESTION = "Is the Spooler service running on this host, and how does it start?"

    # 1. The reviewer must REJECT an answer that contradicts its evidence.
    #    A critic that cannot catch a flat contradiction will not catch
    #    anything subtler, so this is the floor, not the bar.
    wrong = Draft(
        question=QUESTION,
        answer="Yes - the Spooler service is running, and its startup type is automatic.",
        evidence=EVIDENCE,
        steps=1,
    )
    v_wrong = review(wrong)
    print(f"rejects  : {v_wrong['verdict']!r} - {v_wrong['issues']}")
    if v_wrong["verdict"] != "revise":
        print("FAIL: the reviewer approved an answer that contradicts the evidence")
        failures.append("rejects")
    elif not actionable(v_wrong):
        print("FAIL: the reviewer said revise but named no issue")
        failures.append("rejects")

    # 2. And it must APPROVE an answer the evidence supports. Without this
    #    case, `return {"verdict": "revise"}` passes the suite.
    right = Draft(
        question=QUESTION,
        answer="No - the Spooler service is stopped. Its startup type is automatic.",
        evidence=EVIDENCE,
        steps=1,
    )
    v_right = review(right)
    print(f"approves : {v_right['verdict']!r} - {v_right['issues']}")
    if v_right["verdict"] != "approve":
        print("FAIL: the reviewer rejected an answer its own evidence supports")
        failures.append("approves")

    # 3. The round budget. A stub reviewer that is never satisfied - the one
    #    reviewer a real model will not reliably impersonate on demand.
    def never_happy(draft: Draft, **kw: Any) -> dict[str, Any]:
        return {"verdict": "revise", "issues": ["name the startup type explicitly"]}

    bounded = collaborate("Is the Spooler service running on this host?",
                          max_rounds=2, max_steps=4, reviewer=never_happy)
    print(f"bound    : {bounded.rounds} round(s), reason={bounded.reason!r}, "
          f"ok={bounded.ok}, {bounded.model_calls} model call(s)")
    if bounded.ok or bounded.reason != "rounds" or len(bounded.reviews) != 2:
        print("FAIL: the round budget did not stop the loop")
        failures.append("bound")

    # 4. A rejection nobody can act on. It must stop AND it must not be
    #    mistaken for approval - an answer that failed review is not shipped
    #    just because the reviewer failed to explain itself.
    def vague(draft: Draft, **kw: Any) -> dict[str, Any]:
        return {"verdict": "revise", "issues": []}

    stuck = collaborate("Is the Spooler service running on this host?",
                        max_rounds=3, max_steps=4, reviewer=vague)
    print(f"vague    : {stuck.rounds} round(s), reason={stuck.reason!r}, ok={stuck.ok}")
    if stuck.ok or stuck.reason != "unactionable" or stuck.rounds != 1:
        print("FAIL: an unactionable rejection did not stop the loop cleanly")
        failures.append("unactionable")

    # 5. End to end, against the single-agent baseline, on the SAME question.
    #    This is the case the lab exists for. The pair is only worth having if
    #    you can say what it cost, so both numbers are printed and the ratio
    #    is computed rather than guessed.
    BASELINE_Q = "Which services on this host are stopped, and what are their startup types?"

    base = run(BASELINE_Q)
    base_calls = len(base.steps)
    team = collaborate(BASELINE_Q)

    print(f"baseline : {base_calls} model call(s), answer ok={base.ok}")
    print(f"team     : {team.model_calls} model call(s) over {team.rounds} round(s), "
          f"reason={team.reason!r}")
    print(f"overhead : {team.model_calls / base_calls:.2f}x the single agent")
    print(f"answer   : {team.answer}")
    if not team.ok:
        print(f"FAIL: the pair could not approve an answer (reason={team.reason})")
        failures.append("end to end")

    if failures:
        print(f"FAIL: {', '.join(failures)}")
        sys.exit(1)
    print("PASS: it drafts, it reviews, it revises, it stops, and it says what it cost")
    sys.exit(0)
