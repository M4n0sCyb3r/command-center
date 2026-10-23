"""The agent loop.

Lab 3 gave the model ONE shot: propose a tool call, dispatch it, stop. That
answers "is the Spooler running?" and nothing harder, because the model never
sees what the tool returned.

This module runs that exchange in a loop. The model proposes, we dispatch, we
append the result to the conversation, and we ask again - until the model says
it is finished or until it runs out of budget. Whichever comes first.

Lab 11 put a Guard in front of the dispatch. Three edits, in decreasing order
of how obvious they are: the menu is filtered to what the policy permits, the
step schema's tool enum is filtered the same way (the menu is instruction, the
enum is grammar - both have to move), and dispatch() became guard.run(). The
default is READ_ONLY, which offers exactly the two tools Labs 8-10 measured,
so this file still describes the agent those numbers describe.

Lab 12 hung a Tracer off the same loop. Every run now emits one correlated
event stream - the question and the policy it started under, a span per model
call carrying its own tokens and seconds, the guard's verdict, what each tool
returned, and how it ended. The loop is the only place instrumented, because
it is the only place that sees the model, the gate and the tool in one frame.

Lab 13 made two small edits here and left the big ones to approval.py and
guard.py. The operator's question now travels to the gate as `intent`, so a
person approving a change can see what was asked for; and the approval -
which screen, what was typed, how long it took - rides out in the
guard_decision event, so "who said yes, to what, and how fast" is in the
same record as everything else.

    python src/agent.py; echo "exit: $?"
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from guard import READ_ONLY, SUPERVISED, Guard, allowed_tools, menu_for
from memory import Memory
from model_client import ModelError, Reply, chat
from structured import parse_json
from tools import REGISTRY
from tracing import Tracer


def step_schema(tools: list[str] | None = None) -> dict[str, Any]:
    """The shape of ONE model turn: either a tool call, or a final answer.

    Built from REGISTRY, like Lab 3's call_schema(), so a new tool cannot
    drift out of the enum.

    Lab 11 narrows that enum to what the policy permits, and this is the edit
    that is easy to miss. The menu is INSTRUCTION - filtering it removes the
    invitation. This enum is a constrained-decoding GRAMMAR - it is what makes
    a tool name physically emittable. Filter the menu but not the enum and the
    model cannot see `delete_service` in its instructions while remaining
    perfectly able to produce it, because it has read more Windows
    documentation than we will ever show it. Hiding a thing is not the same as
    making it unreachable; both have to move.
    """
    names = sorted(REGISTRY) if tools is None else sorted(tools)
    return {
        "type": "object",
        "properties": {
            # The discriminator. Everything else is read in light of this.
            "action": {"type": "string", "enum": ["call_tool", "final"]},
            # Used when action == "call_tool".
            "tool": {"type": "string", "enum": names},
            "arguments": {"type": "object"},
            # Used when action == "final".
            "answer": {"type": "string"},
        },
        # ONLY action. See the note below - this is the interesting line.
        "required": ["action"],
        "additionalProperties": False,
    }


SYSTEM = """You are a Windows operations assistant working one step at a time.

You cannot run anything yourself. Each turn, return ONE of these:

  {{"action": "call_tool", "tool": "<name>", "arguments": {{...}}}}
      Ask for a tool to be run. You will be shown its result next turn.

  {{"action": "final", "answer": "<your answer>"}}
      You have everything you need. Answer the user's question.

Rules:
  - One tool per turn. Do not guess a result you have not been shown.
  - If a tool returns an error, read it and try a different call.
  - Answer only from tool results, never from memory of Windows defaults.

Tools:
{menu}"""


def think(messages: list[dict[str, str]], *,
          schema: dict[str, Any] | None = None,
          **kwargs: Any) -> tuple[dict[str, Any], Reply]:
    """One model turn: the whole transcript in, one decoded step AND the Reply out.

    Deliberately NOT ask_json(). That helper builds a fresh two-message
    conversation from a single prompt, which is right for a one-shot question
    and wrong here - a loop's entire job is to carry the transcript forward.
    So this goes one level down: chat() for the wire, parse_json() for the
    guard, and the message list belongs to the caller.

    Lab 12 changed the return type, and the reason is the move this course
    has now made four times (L1 windows_host_ip, L4 _parse -> parse_json,
    L5 ToolResult.arguments). The upper layer needs something the lower layer
    was throwing away: chat() has returned prompt_tokens, completion_tokens
    and duration_s since Lab 1, and this function was dropping all three on
    the floor because the loop only wanted the decoded step. The fix is to
    CHANGE THE LOWER LAYER, not to have run() open its own connection or
    time the call from outside and guess. Cost and latency are facts the
    boundary already holds; a trace that re-measures them from the outside
    is a second source of truth.
    """
    schema = step_schema() if schema is None else schema
    reply = chat(messages, schema=schema, **kwargs)
    return parse_json(reply.text, schema), reply


@dataclass
class Step:
    """One turn of the loop, recorded. The trace is the evidence."""

    n: int                              # 1-based turn number
    action: str                         # what the model asked for
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    ok: bool | None = None              # None when no tool ran this turn
    observation: Any = None             # what the tool returned
    error: str | None = None            # why it did not


@dataclass
class AgentResult:
    """How the run ended. Returned, never raised - same as ToolResult."""

    ok: bool
    answer: str | None
    reason: str                         # "final" | "budget"
    steps: list[Step] = field(default_factory=list)
    memory: Memory | None = None        # what the run OBSERVED, not how it got there
    # Lab 11. What the run was ALLOWED to do, and what it was refused. A
    # third view of the same run, and the only one an auditor cares about:
    # steps say what happened, memory says what is true, decisions say who
    # said yes.
    decisions: list[Any] = field(default_factory=list)
    # Lab 12. The key that ties those three views together, and ties all of
    # them to a record on disk that outlives this object. Carried on the
    # result so a caller who kept nothing else can still find the run.
    run_id: str | None = None


def observe(result: Any) -> dict[str, str]:
    """Render a ToolResult as a message the model can read next turn.

    Sent with role "user", not role "tool". Some APIs have a tool role, but it
    is bound to a tool_call_id the provider issued - and our proposals are
    plain JSON we decoded ourselves, not provider-issued calls. Claiming the
    tool role would be dressing our convention up as their protocol, and it
    breaks the moment Phase II points this at a frontier API.

    Errors are reported, not hidden. Lab 3 made dispatch() return failure as
    data precisely so it could arrive here as readable text: the model gets to
    see what it did wrong and try something else, which is the cheapest
    recovery mechanism in the whole design.
    """
    if result.ok:
        body = f"TOOL RESULT ({result.tool}): {json.dumps(result.value)}"
    else:
        body = f"TOOL ERROR ({result.tool}): {result.error}"
    return {"role": "user", "content": body}


PRIOR = """

What you already know from EARLIER sessions. Each line was observed by
running a tool, but it may be OUT OF DATE - check the timestamps, and run the
tool again if a fact matters and looks stale.

{known}"""


def run(question: str, *, max_steps: int = 8,
        memory: Memory | None = None, guard: Guard | None = None,
        tracer: Tracer | None = None, **kwargs: Any) -> AgentResult:
    """Think, act, observe, repeat - until the model finishes or the budget runs out.

    `memory` is optional and, when given, is used TWICE and differently:

      - Before the loop, what it already holds is rendered into the system
        prompt, labelled as prior and possibly stale. This is the whole
        payoff: a second run starts knowing what the first one learned.

      - During the loop, each successful result is recorded into it. Note
        that those facts are NOT re-injected into the prompt as they arrive.
        Within one run the transcript already carries every observation as a
        message, so rendering memory each turn would say everything twice and
        spend context to do it.

    That asymmetry is the point worth keeping. Memory is redundant with the
    transcript inside a single run, and it is the only thing that survives
    between runs. It earns its place at the boundary, not in the middle.
    """
    mem = memory if memory is not None else Memory()

    # A GUARD, not a policy. The guard carries the spent budget, and budget
    # is run-scoped state - taking a policy here would let this function
    # quietly build a fresh guard on every call and reset the counter, which
    # is exactly the cumulative limit it exists to enforce. Making the caller
    # construct it keeps the thing with state visible at the call site.
    gate = guard if guard is not None else Guard(READ_ONLY)

    # A REAL tracer by default, with a sink that goes nowhere. The tempting
    # alternative is `if tracer is not None:` around every emit, and that is
    # how instrumentation rots: the branches multiply, somebody adds an early
    # return inside one, and a run comes back half-recorded. Here the calls
    # below are unconditional, and switching tracing on means passing a sink,
    # not changing the loop.
    tap = tracer if tracer is not None else Tracer()

    # Both halves of the filter, from one policy. Default READ_ONLY yields
    # the two read tools in registry order - byte-identical to the prompt
    # Labs 8, 9 and 10 measured, which is what lets those numbers stand.
    system = SYSTEM.format(menu=menu_for(gate.policy))
    schema = step_schema(allowed_tools(gate.policy))
    known = mem.render()
    if known:
        # Rendered ONCE, from what was carried in - see the docstring.
        system += PRIOR.format(known=known)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
    steps: list[Step] = []

    # The conditions the run started under. A duration with no conditions
    # beside it is not evidence: "six steps" means nothing next month unless
    # the trace also says which question, which budget, and which policy -
    # the same run under REHEARSAL is a different run.
    tap.emit("run_start", question=question, max_steps=max_steps,
             ceiling=gate.policy.ceiling, max_writes=gate.policy.max_writes,
             dry_run=gate.policy.dry_run, tools=allowed_tools(gate.policy),
             prior_facts=len(mem))

    # THE BUDGET. 8, not 6: the four-service question consumes exactly 6
    # (list + 4 lookups + final), so a 6 budget leaves no room for a single
    # retry. Sized from an observed run, not from a count on paper.
    # Our code counts, not the model. A loop whose only exit is
    # the model deciding it is done is an unbounded bill and a hung process.
    for n in range(1, max_steps + 1):
        # TWO durations, and they are not the same number. `duration_s` on
        # this span is what the LOOP waited - the network, the queue, the
        # decode. `model_s` is what the model boundary itself timed. The gap
        # between them is ours, and you cannot see it with one clock.
        #
        # P04, agent-loop stall: the ModelError leaves with the TURN on it. The
        # boundary below cannot know it and the harness above cannot see it;
        # only this loop holds `n`. Same move as Lab 12's think() change - the
        # upper layer needed a fact the lower layer was dropping - except here
        # the fact is ours and the exception is the carrier. Re-raised, never
        # handled: a stalled turn is still not this loop's to recover from.
        try:
            with tap.span("model_call", turn=n) as box:
                step, reply = think(messages, schema=schema, **kwargs)
                box.update(action=step.get("action"), tool=step.get("tool"),
                           model=reply.model,
                           prompt_tokens=reply.prompt_tokens,
                           completion_tokens=reply.completion_tokens,
                           model_s=round(reply.duration_s, 3))
        except ModelError as exc:
            exc.turn = n
            raise

        # The model's own turn goes back into the transcript. Omit this and it
        # cannot see what it already asked for, so it asks again, and again,
        # until the budget stops it. That failure has a name: tool thrash.
        messages.append({"role": "assistant", "content": json.dumps(step)})

        action = step["action"]   # safe: the schema's one required field

        if action == "final":
            answer = step.get("answer")
            if answer is None:
                # Schema guaranteed the discriminator, not the field beside it.
                # This is our half of that bargain - and it is recoverable, so
                # we say so and let the model try again.
                steps.append(Step(n=n, action=action, ok=False,
                                  error="action 'final' with no 'answer' field"))
                messages.append({"role": "user", "content":
                                 "You returned action 'final' but no 'answer' "
                                 "field. Give the answer, or call a tool."})
                continue
            steps.append(Step(n=n, action=action, ok=True, observation=answer))
            tap.emit("run_end", ok=True, reason="final", steps=len(steps),
                     answer=answer, spent=gate.spent, dropped=tap.dropped)
            return AgentResult(ok=True, answer=answer, reason="final",
                               steps=steps, memory=mem, decisions=gate.log,
                               run_id=tap.run_id)

        tool = step.get("tool")
        if tool is None:
            steps.append(Step(n=n, action=action, ok=False,
                              error="action 'call_tool' with no 'tool' field"))
            messages.append({"role": "user", "content":
                             "You returned action 'call_tool' but named no "
                             "'tool'. Name one from the menu."})
            continue

        args = step.get("arguments") or {}
        # Gate 0, then Lab 3's three gates behind it. One line, because
        # dispatch() has returned failure as DATA since Lab 3: a refusal
        # arrives here shaped exactly like a tool crash, observe() renders it
        # as text, and the model is told who refused (policy or approver) and
        # gets to pick something legal. The recovery path was already built and paid for.
        #
        # Lab 13: the question goes with it. The approver is shown the
        # operator's ask beside the proposed change - never the model's
        # reasoning, which the schema does not even carry.
        result = gate.run({"tool": tool, "arguments": args}, intent=question)

        # The guard does not know this tracer exists, and that is deliberate.
        # Instrument at the seam that can see the WHOLE run, not inside every
        # module - a tracer threaded through guard.py, tools.py and memory.py
        # makes four modules depend on it and none of them testable without
        # it. The loop is the one place that sees the model, the gate and the
        # tool in the same frame, so the loop is where the record is written.
        decision = gate.log[-1] if gate.log else None
        if decision is not None:
            tap.emit("guard_decision", turn=n, tool=decision.tool,
                     risk=decision.risk, allowed=decision.allowed,
                     simulated=decision.simulated, reason=decision.reason,
                     spent=gate.spent, budget=gate.policy.max_writes,
                     screen=decision.fingerprint, answer=decision.answer,
                     approver_s=decision.approver_s, hasty=decision.hasty)
        tap.emit("tool_call", turn=n, tool=tool, arguments=args,
                 ok=result.ok, value=result.value, error=result.error)

        # Only successes land here; record() enforces that, not us.
        mem.record(n, result)

        steps.append(Step(n=n, action=action, tool=tool, arguments=args,
                          ok=result.ok, observation=result.value,
                          error=result.error))
        messages.append(observe(result))

    # Budget exhausted. Not an exception: a bounded loop hitting its bound is
    # expected behaviour, and the caller wants the trace of what it managed.
    tap.emit("run_end", ok=False, reason="budget", steps=len(steps),
             answer=None, spent=gate.spent, dropped=tap.dropped)
    return AgentResult(ok=False, answer=None, reason="budget",
                       steps=steps, memory=mem, decisions=gate.log,
                       run_id=tap.run_id)


# --- smoke test -----------------------------------------------------------
# Properties 1-5 are the Labs 4-5 set and must produce the SAME numbers they
# did at Lab 10 - that is the regression claim this lab makes. 6-8 are Lab
# 11's: the read-only default hides the dangerous half, a refused agent
# cannot touch the host, and the cumulative budget holds end to end through a
# live loop rather than only against forged calls. 9-11 are Lab 12's: the
# stream is complete and correlated, the bill is derived from it, and the
# record survives the process that wrote it. 12 is Lab 13's: a scripted
# person sits in the approver seam, end to end, through a live model.
#
# Note what 1-8 are doing for this lab. think() changed its return type and
# run() grew a parameter, and the proof that this was a REFACTOR rather than
# a rewrite is that eight properties written before the tracer existed still
# pass, with the same numbers. The old tests are the regression suite.

if __name__ == "__main__":
    import sys
    import tempfile
    from pathlib import Path

    from guard import Policy, Proposal
    from tools import SERVICES, reset_services
    from tracing import Event, collect, jsonl_sink, read_jsonl, summarize

    failures: list[str] = []

    QUESTION = "Which services on this host are stopped, and what are their startup types?"

    # 1. The chain. More than one tool, in an order nobody dictated.
    result = run(QUESTION)
    tools_used = [s.tool for s in result.steps if s.tool]
    print(f"steps    : {len(result.steps)}  tools: {tools_used}")
    print(f"answer   : {result.answer}")
    if not (result.ok and result.reason == "final"):
        print(f"FAIL: run did not finish cleanly (reason={result.reason})")
        failures.append("chain")
    elif "list_services" not in tools_used or "get_service_status" not in tools_used:
        print("FAIL: the model answered without chaining two different tools")
        failures.append("chain")

    # 2. The budget. Same question, deliberately starved. It MUST stop, and it
    #    must stop by returning - a budget that raises loses the trace.
    starved = run(QUESTION, max_steps=2)
    print(f"budget   : stopped after {len(starved.steps)} steps, reason={starved.reason!r}")
    if starved.ok or starved.reason != "budget" or len(starved.steps) != 2:
        print("FAIL: the step budget did not stop the loop")
        failures.append("budget")

    # 3. Survivable error. A service that is not in the fixture: the tool
    #    raises, dispatch converts it to data, observe() renders it, and the
    #    loop keeps going. Lab 3's whole reason for returning instead of
    #    raising is cashed in right here.
    recovered = run("Is the Telnet service running on this host?")
    failed = [s for s in recovered.steps if s.ok is False]
    print(f"recovery : {len(failed)} failed step(s), ended reason={recovered.reason!r}")
    if failed and failed[0].n == len(recovered.steps) and not recovered.ok:
        print("FAIL: a tool error ended the run instead of being fed back")
        failures.append("recovery")

    # 4. Memory holds exactly the successful tool calls - no more, no less.
    #    The trace records everything that happened; memory records only what
    #    was observed. If these two ever agree on a failed step, the wall
    #    between "what the run did" and "what is true" has come down.
    succeeded = [s for s in result.steps if s.tool and s.ok]
    print(f"memory   : {len(result.memory)} observation(s) from "
          f"{len(succeeded)} successful call(s)")
    if len(result.memory) != len(succeeded):
        print("FAIL: memory and the trace disagree about what was observed")
        failures.append("memory")

    # 5. Across runs. Persist what run 1 learned, load it into a fresh
    #    Memory, and start run 2 from it. The seeded facts must survive into
    #    the second run's memory - that is the property the whole module
    #    exists for, and it is the only one a single run cannot show.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "agent-memory.json"
        result.memory.save(path)
        carried = Memory.load(path)
        seeded_facts = {(o.tool, json.dumps(o.arguments, sort_keys=True))
                        for o in carried.latest()}

        second = run("Is the Spooler service running on this host?", memory=carried)
        kept = {(o.tool, json.dumps(o.arguments, sort_keys=True))
                for o in second.memory.latest()}

        print(f"carried  : {len(seeded_facts)} fact(s) into run 2, "
              f"which used {len(second.steps)} step(s) vs {len(result.steps)} cold")
        if not seeded_facts <= kept:
            print("FAIL: facts carried in from a previous run were lost")
            failures.append("carryover")
        if not second.ok:
            print(f"FAIL: seeded run did not finish (reason={second.reason})")
            failures.append("carryover")

    # 6. THE REGRESSION CLAIM. The read-only default must name exactly the
    #    two tools Labs 8-10 measured, in both the menu AND the grammar. If
    #    either grew, every number in Phase III was measured against a
    #    different agent and has to be re-measured.
    offered = allowed_tools(READ_ONLY)
    grammar = step_schema(offered)["properties"]["tool"]["enum"]
    print(f"policy   : read-only offers {offered}, grammar {grammar}")
    if offered != ["get_service_status", "list_services"] or sorted(grammar) != offered:
        print("FAIL: the read-only surface is not the Lab 10 surface")
        failures.append("regression")

    # 7. Refused, and the host is untouched. The agent is given a policy that
    #    PERMITS writes and an approver that refuses them, which is the
    #    realistic shape: the capability exists, the answer is no.
    reset_services()
    baseline = {n: dict(r) for n, r in SERVICES.items()}
    denier = Guard(SUPERVISED)          # default approver says no
    refused = run("Restart the Spooler service on this host.", guard=denier)
    blocked = [d for d in denier.log if not d.allowed]
    print(f"refused  : {len(blocked)} refusal(s), host changed: "
          f"{SERVICES != baseline}, answer: {str(refused.answer)[:70]!r}")
    if SERVICES != baseline:
        print("FAIL: a refused agent changed the host")
        failures.append("refusal")
    if not blocked:
        print("NOTE: the model never attempted a write, so nothing was refused")

    # 8. The cumulative budget, end to end. Two services are stopped and the
    #    budget is one. Whatever the model chooses to do, the host cannot
    #    move more than once - and that bound holds without anyone reading
    #    the transcript to check.
    reset_services()
    capped = Guard(Policy(ceiling="write", max_writes=1),
                   approver=lambda proposal: True)
    run("Restart every stopped service on this host.", guard=capped)
    moved = [n for n, r in SERVICES.items() if r != baseline[n]]
    print(f"budget   : spent={capped.spent}/1, services changed={moved}")
    if capped.spent > 1 or len(moved) > 1:
        print("FAIL: the run exceeded its blast-radius budget")
        failures.append("blast radius")
    reset_services()

    # 9. THE TRACE. One run under a real tracer: every event carries the same
    #    id, the stream opens with run_start and closes with run_end, there
    #    is exactly one model_call per step, and every model call reports its
    #    own cost. Then the summary - the two numbers this repo has never
    #    been able to state about itself.
    events: list[Event] = []
    traced = run(QUESTION, tracer=Tracer(collect(events)))
    kinds = [e.kind for e in events]
    calls = [e for e in events if e.kind == "model_call"]
    priced = [c for c in calls if c.data.get("prompt_tokens")]
    print(f"trace    : {len(events)} events {kinds[0]}..{kinds[-1]}, "
          f"{len(calls)} model call(s), {len(priced)} priced, "
          f"id={traced.run_id}")
    if not events or kinds[0] != "run_start" or kinds[-1] != "run_end":
        print("FAIL: the stream does not open and close the run")
        failures.append("trace")
    if len({e.run_id for e in events}) != 1 or events[0].run_id != traced.run_id:
        print("FAIL: the run and its trace do not share one id")
        failures.append("trace")
    if len(calls) != len(traced.steps) or len(priced) != len(calls):
        print("FAIL: a model call went unrecorded or unpriced")
        failures.append("trace")

    bill = summarize(events)
    print(f"cost     : {bill['tokens_in']} in / {bill['tokens_out']} out, "
          f"{bill['model_s']}s model of {bill['wall_s']}s wall "
          f"= {bill['model_share']} model share, dropped={bill['dropped']}")
    if bill["steps"] != len(traced.steps) or bill["dropped"] != 0:
        print("FAIL: the summary disagrees with the run, or events were lost")
        failures.append("trace")

    # 10. The overhead of watching. Instrumentation you cannot afford gets
    #     switched off, and instrumentation that is off is not
    #     instrumentation - so the price has to be a number, not a hope.
    #     Measured against the untraced run 1 above, same question.
    print(f"overhead : traced {bill['wall_s']}s vs untraced run of "
          f"{len(result.steps)} steps; {len(events)} events written")

    # 11. Durable, and in the place derived data lives. A trace written to
    #     evals/runs/ is gitignored for the same reason Lab 6's index is: it
    #     is output, not source, and committing it would put a 200-line file
    #     in every diff.
    run_dir = Path(__file__).resolve().parent.parent / "evals" / "runs"
    written = run_dir / f"{traced.run_id}.jsonl"
    disk = Tracer(jsonl_sink(written), run_id=traced.run_id)
    for event in events:
        disk.sink(event)
    rows = read_jsonl(written)
    print(f"written  : {len(rows)} line(s) -> evals/runs/{written.name}")
    if len(rows) != len(events) or summarize(rows)["run_id"] != traced.run_id:
        print("FAIL: the trace on disk is not the trace in memory")
        failures.append("durable")

    # 12. THE HUMAN GATE, end to end. A scripted person who reads the screen
    #     and types "yes". Four things must hold at once: the person was
    #     shown the operator's question and a real diff, the host moved, and
    #     the trace records WHICH screen was approved - the same fingerprint
    #     the person saw.
    from approval import console_approver

    reset_services()
    screens: list[str] = []
    approvals: list[Event] = []
    person = console_approver(read=lambda prompt: "yes", write=screens.append)
    staffed = Guard(SUPERVISED, approver=person)
    ask = "Restart the Spooler service on this host."
    human = run(ask, guard=staffed, tracer=Tracer(collect(approvals)))
    gated = [e for e in approvals if e.kind == "guard_decision"
             and e.data.get("screen")]
    print(f"human    : {len(screens)} screen(s) shown, "
          f"Spooler now {SERVICES['Spooler']['status']!r}, "
          f"traced screen(s) {[e.data['screen'] for e in gated]}")
    if screens:
        print("\n".join("           " + line for line in screens[0].splitlines()[3:9]))
    if not screens:
        print("NOTE: the model never proposed a write, so no one was asked")
    else:
        if ask not in screens[0] or "stopped    -> running" not in screens[0]:
            print("FAIL: the person was not shown the ask and the diff")
            failures.append("human gate")
        if not gated or gated[0].data["screen"] not in screens[0]:
            print("FAIL: the trace does not say which screen was approved")
            failures.append("human gate")
        if SERVICES["Spooler"]["status"] != "running":
            print("FAIL: an approved change did not happen")
            failures.append("human gate")
    reset_services()

    if failures:
        print(f"FAIL: {', '.join(failures)}")
        sys.exit(1)
    print("PASS: it thinks, it acts, it observes, it remembers, it stops, "
          "it cannot do more than it was allowed, it says what it cost, "
          "and it shows a person what they are approving")
    sys.exit(0)
