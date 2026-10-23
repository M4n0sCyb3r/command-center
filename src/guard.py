"""Guardrails and blast radius.

Labs 8, 9 and 10 asked "is the agent RIGHT?" and produced a number for it.
This module asks the other question: "what happens when it is WRONG?"

They are not the same property and one does not fix the other. An agent at
99% accuracy holding an unbounded delete tool is more dangerous than one at
80% that can only read, because accuracy is a number you improve and blast
radius is a bound you IMPOSE. Improving the model is not a safety control;
it has no ceiling you can prove.

Where this sits: Lab 3's dispatch() has three gates, and every one of them
asks "is this call well-formed?" - does the tool exist, are the arguments
legal, did the function raise. None asks "is this call ALLOWED?" That is a
different question with a different owner, so it goes in FRONT of dispatch as
gate 0 rather than inside it. tools.py declares how dangerous each tool is;
this file decides what is permitted today. Author and operator, two files.

Lab 13 rebuilt gate 0d. The approver is now shown a PREVIEW of the change
(approval.py), a call that cannot be previewed is refused without asking
anyone, only a literal True counts as yes, and an approval is bound to the
state it was shown - re-checked immediately before dispatch.

    python src/guard.py; echo "exit: $?"

Runs hermetically. No model, no network - every call in the smoke test is
forged, because waiting for a model to propose something destructive on cue
is not a test.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable

from approval import PreviewError, Proposal, as_verdict, build_proposal
from tools import REGISTRY, RISK_LEVELS, ToolResult, dispatch


def rank(risk: str) -> int:
    """Position in RISK_LEVELS. The tuple's ORDER is the comparison."""
    return RISK_LEVELS.index(risk)


# Proposal moved to approval.py at Lab 13 - it is what a PERSON is shown,
# and that is now a module's worth of design. It is still importable from
# here, so `from guard import Proposal` in older code keeps working.


def deny_all(proposal: Proposal) -> bool:
    """The default approver. Says no to everything.

    This is the whole of R2. A guard's defaults are what runs in every code
    path nobody thought about, so the default has to be the safe answer even
    though it makes the guard useless until someone deliberately makes it
    useful. A permissive default is a guard you have not written yet.
    """
    return False


@dataclass(frozen=True)
class Policy:
    """What is permitted this run. Set by the operator, not the model.

    Two knobs that look alike and are not:

      ceiling         the highest risk level that can run AT ALL. A wall.
                      Nothing above it executes and no approver can lift it.

      approve_above   the level above which a human is asked. A door.

    The distinction is the point. If the approver can approve anything, there
    is no policy - there is a tired person at 2am one click away from
    delete_service. Some things should not be one yes away, and that is what
    a ceiling is for.

      max_writes      CUMULATIVE budget of mutating calls per run. The
                      dangerous thing an agent does is rarely one bad action;
                      it is a plausible action repeated past the point of
                      sense, and a per-call gate cannot see that because it
                      only ever sees one call.

      dry_run         mutating calls are refused and REPORTED instead of
                      executed. Read calls still run for real - a rehearsal
                      you cannot observe teaches nothing.
    """

    ceiling: str = "read"
    approve_above: str = "read"
    max_writes: int = 0
    dry_run: bool = False

    def __post_init__(self) -> None:
        for field_name in ("ceiling", "approve_above"):
            value = getattr(self, field_name)
            if value not in RISK_LEVELS:
                raise ValueError(
                    f"Policy.{field_name}={value!r}; must be one of {RISK_LEVELS}"
                )


# Three named policies, because "Policy(ceiling='write', max_writes=3)"
# scattered through a codebase is a policy nobody can audit.
READ_ONLY = Policy()
SUPERVISED = Policy(ceiling="write", max_writes=3)
REHEARSAL = Policy(ceiling="destructive", dry_run=True)


@dataclass
class Decision:
    """Why a call was allowed or refused. Data, never an exception.

    Fifth application of the pattern in this course, after ToolResult (L3),
    AgentResult (L4), attempt() (L8) and the judge (L10). A refusal that
    raises destroys the trace at exactly the moment the trace matters most -
    the audit question is never "did it crash", it is "what did it try to do
    and who said no".
    """

    allowed: bool
    tool: str
    risk: str
    reason: str
    simulated: bool = False
    # Lab 13: the approval, when a person was asked. Which screen they
    # answered, what they typed, how long it took, and whether that was
    # suspiciously quick. None/False when no one was asked - and "no one was
    # asked" is itself worth being able to see in an audit.
    fingerprint: str | None = None
    answer: str | None = None
    approver_s: float | None = None
    hasty: bool = False
    # Who said no. Found in the first live run of Lab 13: a PERSON declined,
    # the model was told "REFUSED BY POLICY", and it explained the refusal
    # to the user as a group-policy problem and told them to open gpedit.msc.
    # The error text is the model's only evidence, so it has to name the
    # right refuser - "policy" for rules, "approver" for a person.
    refused_by: str = "policy"


def allowed_tools(policy: Policy) -> list[str]:
    """Names of the tools this policy permits, in registry order."""
    return [t.name for t in REGISTRY.values() if rank(t.risk) <= rank(policy.ceiling)]


def menu_for(policy: Policy) -> str:
    """The tool menu, filtered to what the policy permits.

    Defence in depth, and it is important to be honest about which half is
    the defence. Filtering the menu means the model is never TEMPTED by a
    tool that cannot run - but a menu is a request, and a prompt is not a
    control. The enforcement is check() below. This just removes the
    invitation.

    Risk is labelled in the menu on purpose. The model cannot be trusted to
    act on it, which is exactly why the label costs nothing: it may help, it
    cannot be relied on, and nothing downstream reads it.
    """
    import json

    lines: list[str] = []
    permitted = set(allowed_tools(policy))
    for tool in REGISTRY.values():
        if tool.name not in permitted:
            continue
        lines.append(f"- {tool.name}: {tool.description}")
        lines.append(f"  arguments: {json.dumps(tool.parameters)}")
        if rank(tool.risk) > rank("read"):
            note = "requires approval" if rank(tool.risk) > rank(policy.approve_above) \
                else "permitted"
            lines.append(f"  risk: {tool.risk} ({note})")
    return "\n".join(lines)


class Guard:
    """Gate 0. Stands between a proposed call and dispatch().

    Holds the run-scoped state a per-call check cannot have: how much of the
    budget is spent, and every decision made. One Guard per run.
    """

    def __init__(self, policy: Policy | None = None,
                 approver: Callable[[Proposal], bool] | None = None) -> None:
        self.policy = policy if policy is not None else READ_ONLY
        # The FOURTH seam in this course, after Lab 7's reviewer=, Lab 8's
        # runner= and Lab 10's judge=. Same reason every time: a control you
        # cannot stub is a control you cannot test, and "I ran it and it
        # seemed to stop" is not evidence that a budget holds.
        self.approver = approver if approver is not None else deny_all
        self.spent = 0
        self.log: list[Decision] = []

    def check(self, call: dict[str, Any], intent: str = "") -> Decision:
        """Decide. Runs nothing, changes nothing - including the budget.

        `intent` is the operator's original request. It is only ever shown
        to the approver; no rule in this file reads it, because a rule that
        parses English is a prompt pretending to be a control.

        Order matters, and it is cheapest-and-most-absolute first: an unknown
        tool cannot be rated, a ceiling cannot be argued with, a spent budget
        is arithmetic, and only then is a human's time spent.
        """
        name = call.get("tool")
        args = call.get("arguments") or {}

        # Gate 0a: unknown tool. Fail closed - an unrated tool is refused
        # rather than waved through. dispatch() would also catch this, but a
        # guard that relies on a later layer to be safe is not a guard.
        tool = REGISTRY.get(name)
        if tool is None:
            return Decision(allowed=False, tool=str(name), risk="unknown",
                            reason=f"unknown tool {name!r}: cannot rate it, so refuse it")

        # Gate 0b: the ceiling. No approver is consulted, on purpose - this
        # is the wall, and a wall you can talk your way past is a door.
        if rank(tool.risk) > rank(self.policy.ceiling):
            return Decision(allowed=False, tool=tool.name, risk=tool.risk,
                            reason=f"{tool.risk} exceeds the policy ceiling "
                                   f"({self.policy.ceiling}); not approvable")

        mutating = rank(tool.risk) > rank("read")

        # Gate 0c: the cumulative budget. R3 - the forty-restart outage is
        # made of forty individually reasonable calls, and this is the only
        # check in the file that can see more than one of them.
        if mutating and not self.policy.dry_run and self.spent >= self.policy.max_writes:
            return Decision(allowed=False, tool=tool.name, risk=tool.risk,
                            reason=f"blast-radius budget spent "
                                   f"({self.spent}/{self.policy.max_writes} "
                                   f"mutating calls used this run)")

        # Gate 0d: the human. Last because it is the most expensive check in
        # the file - everything above it is arithmetic, and a person asked to
        # rubber-stamp calls that a rule could have refused stops reading.
        #
        # Lab 13 splits it in two. First the PREVIEW: if the call cannot be
        # shown, it is refused here and nobody is asked - a person woken to
        # approve something no one can describe is being asked to guess.
        # Then the person, and then the answer judged strictly: only True
        # (or a Verdict whose approved is True, for this exact screen) is yes.
        approval: dict[str, Any] = {}
        if rank(tool.risk) > rank(self.policy.approve_above):
            try:
                proposal = build_proposal(tool, args, intent=intent,
                                          spent=self.spent,
                                          budget=self.policy.max_writes)
            except PreviewError as exc:
                return Decision(allowed=False, tool=tool.name, risk=tool.risk,
                                reason=f"cannot show this change, so cannot "
                                       f"approve it: {exc}")
            verdict = as_verdict(self.approver(proposal), proposal)
            approval = {"fingerprint": verdict.fingerprint,
                        "answer": verdict.answer,
                        "approver_s": verdict.seconds,
                        "hasty": verdict.hasty}
            if not verdict.approved:
                return Decision(allowed=False, tool=tool.name, risk=tool.risk,
                                reason=f"not approved: {verdict.reason}",
                                refused_by="approver", **approval)

        # Allowed. In a dry run a mutating call is allowed to be REPORTED,
        # never executed; run() turns that into a result that cannot be
        # mistaken for one.
        simulated = mutating and self.policy.dry_run
        return Decision(allowed=True, tool=tool.name, risk=tool.risk,
                        reason="dry run: permitted but not executed" if simulated
                        else "approved" if approval else "permitted",
                        simulated=simulated, **approval)

    def still_current(self, call: dict[str, Any], decision: Decision) -> bool:
        """Is the host still in the state the approver was shown?

        Time-of-check to time-of-use. A person reads a screen for thirty
        seconds; in those thirty seconds a scheduled task, another operator,
        or another agent can change the very thing on the screen. The yes
        was to THAT state. So the preview is recomputed as late as possible
        - immediately before dispatch - and its fingerprint compared.
        A preview that now fails counts as changed: whatever was shown is
        no longer true.
        """
        try:
            now = build_proposal(REGISTRY[decision.tool],
                                 call.get("arguments") or {})
        except PreviewError:
            return False
        return now.fingerprint == decision.fingerprint

    def run(self, call: dict[str, Any], intent: str = "") -> ToolResult:
        """Check, then dispatch if permitted. Returns a ToolResult either way.

        A refusal comes back as ToolResult(ok=False, error=...) rather than
        an exception, and that is not tidiness - it is the whole recovery
        mechanism. Lab 4's observe() renders any failed result to the model as
        readable text, so a denied agent is TOLD it was denied and can try
        something legal. An exception here would end the run instead.

        The dry-run branch is the ruling worth remembering. A simulated call
        returns ok=False, because `ok` means the tool ran and returned a
        value, and it did not. Returning a plausible fake value with ok=True
        would be recorded by Lab 5's Memory as an observed fact about a host
        nobody touched - memory as a second chance to hallucinate, straight
        back in through the safety layer. Nothing happened, so there is no
        result; there is only an explanation.
        """
        decision = self.check(call, intent=intent)

        # Lab 13: an approval is spent against the state it described. If the
        # host moved while the person was reading, the yes is void - and the
        # record says so, rather than recording an approval for a call that
        # then did something the approver never saw.
        if (decision.allowed and not decision.simulated
                and decision.fingerprint is not None
                and not self.still_current(call, decision)):
            decision = replace(decision, allowed=False,
                               reason="stale approval: the host changed after "
                                      "the approver's screen was drawn; ask again")
        self.log.append(decision)

        if not decision.allowed:
            error = f"REFUSED BY {decision.refused_by.upper()}: {decision.reason}"
            if decision.refused_by == "approver":
                # An instruction, and honest about being one: a prompt cannot
                # stop a model inventing a cause, it can only stop us handing
                # it a misleading one. The enforcement is the refusal itself.
                # And only claim a PERSON when one typed something - the
                # default deny_all is not a person, and saying it was would be
                # this file inventing a cause of its own.
                who = ("A person reviewed this exact change and declined it."
                       if decision.answer is not None
                       else "The configured approver declined it.")
                error += (f". {who} This is not a permission or policy error; "
                          f"do not guess at a cause.")
            return ToolResult(ok=False, tool=decision.tool,
                              arguments=call.get("arguments") or {},
                              error=error)

        if decision.simulated:
            return ToolResult(ok=False, tool=decision.tool,
                              arguments=call.get("arguments") or {},
                              error=f"DRY RUN: {decision.tool} was permitted but "
                                    f"not executed, so there is no result. The "
                                    f"host is unchanged.")

        result = dispatch(call)

        # Spend the budget on what was actually DONE, not on what was asked.
        # Two consequences, both deliberate. A dry run costs nothing, because
        # a rehearsal has no blast radius - bounding the number of turns is
        # the loop's job (Lab 4's max_steps), not this file's. And a mutating
        # call that FAILED still spends, because the budget bounds attempts
        # against a real host: "it errored" is not proof that nothing moved.
        if rank(decision.risk) > rank("read"):
            self.spent += 1

        return result


# --- smoke test -----------------------------------------------------------
# Nine properties from Lab 11 and five from Lab 13, all hermetic. The interesting ones are 4 (the cumulative
# budget, which is the only check a per-call gate cannot make), 5 (the
# ceiling refuses without ever consulting the approver) and 6 (a dry run
# leaves no fact behind).

if __name__ == "__main__":
    import sys

    from tools import SERVICES, reset_services, tool_menu

    failures: list[str] = []

    READ_CALL = {"tool": "get_service_status", "arguments": {"name": "Spooler"}}
    WRITE_CALL = {"tool": "restart_service", "arguments": {"name": "Spooler"}}
    NUKE_CALL = {"tool": "delete_service", "arguments": {"name": "Spooler"}}

    def always_yes(proposal: Proposal) -> bool:
        return True

    # 1. The default policy reads, and refuses everything else.
    g = Guard()
    ok = g.run(READ_CALL)
    denied = g.run(WRITE_CALL)
    print(f"read-only: read ok={ok.ok}, write refused={not denied.ok}")
    print(f"           {denied.error}")
    if not ok.ok or denied.ok:
        print("FAIL: the default policy is not read-only")
        failures.append("default policy")

    # 2. Fail closed. A policy that PERMITS writes still refuses them when
    #    nobody supplied an approver, because the default approver says no.
    g = Guard(SUPERVISED)
    shut = g.run(WRITE_CALL)
    print(f"no approver: refused={not shut.ok} - {shut.error}")
    if shut.ok:
        print("FAIL: a write ran with no approver configured")
        failures.append("fail closed")

    # 3. With an approver, the write actually happens.
    g = Guard(SUPERVISED, approver=always_yes)
    did = g.run(WRITE_CALL)
    print(f"approved : ok={did.ok} value={did.value} spent={g.spent}")
    if not did.ok or SERVICES["Spooler"]["status"] != "running":
        print("FAIL: an approved write did not run")
        failures.append("approved write")
    reset_services()

    # 4. THE CUMULATIVE BUDGET. Three approvals, a budget of two. Every one
    #    of these calls is individually reasonable and individually approved;
    #    only a run-scoped counter can see that the third is too many. This
    #    is the property a per-call gate structurally cannot have.
    g = Guard(Policy(ceiling="write", max_writes=2), approver=always_yes)
    outcomes = [g.run(WRITE_CALL).ok for _ in range(3)]
    print(f"budget   : {outcomes} spent={g.spent} - third refused: "
          f"{g.log[-1].reason}")
    if outcomes != [True, True, False]:
        print("FAIL: the cumulative budget did not stop the third write")
        failures.append("budget")
    reset_services()

    # 5. The ceiling is a wall, not a door. A destructive call under a write
    #    ceiling is refused, and the approver is NEVER ASKED - proved by a
    #    counting stub, because "it was refused" does not tell you whether a
    #    human was woken up to refuse it.
    asked: list[Proposal] = []

    def counting_yes(proposal: Proposal) -> bool:
        asked.append(proposal)
        return True

    g = Guard(SUPERVISED, approver=counting_yes)
    nuked = g.run(NUKE_CALL)
    print(f"ceiling  : refused={not nuked.ok}, approver consulted "
          f"{len(asked)} time(s)")
    if nuked.ok or asked:
        print("FAIL: an approver was able to lift the ceiling")
        failures.append("ceiling")
    if "Spooler" not in SERVICES:
        print("FAIL: delete_service ran anyway")
        failures.append("ceiling")

    # 6. Dry run. Permitted, not executed, and - the ruling - it leaves NO
    #    result behind for Lab 5's Memory to record as a fact.
    before = dict(SERVICES["Spooler"])
    g = Guard(REHEARSAL, approver=always_yes)
    rehearsed = g.run(NUKE_CALL)
    print(f"dry run  : ok={rehearsed.ok} ({rehearsed.error})")
    if rehearsed.ok:
        print("FAIL: a dry run returned a success for something that never ran")
        failures.append("dry run")
    if SERVICES.get("Spooler") != before:
        print("FAIL: a dry run changed the host")
        failures.append("dry run")
    if g.spent != 0:
        print("FAIL: a rehearsal spent blast-radius budget")
        failures.append("dry run")
    # And the read half of a rehearsal is real, or the rehearsal is blind.
    seen = g.run(READ_CALL)
    if not seen.ok:
        print("FAIL: a dry run blocked a read")
        failures.append("dry run")

    # 7. An unknown tool is refused by the guard itself, not left to
    #    dispatch(). A guard that relies on a later layer to be safe is not
    #    a guard.
    g = Guard(Policy(ceiling="destructive", approve_above="destructive"))
    unknown = g.check({"tool": "format_c_drive", "arguments": {}})
    print(f"unknown  : allowed={unknown.allowed} - {unknown.reason}")
    if unknown.allowed:
        print("FAIL: an unrated tool was permitted")
        failures.append("unknown tool")

    # 8. The read-only menu is the Lab 10 menu. This is the regression check
    #    that matters: if READ_ONLY changed the prompt by one character,
    #    every number in Labs 8, 9 and 10 was measured against a different
    #    agent and would have to be re-measured.
    read_menu = menu_for(READ_ONLY)
    leaked = [n for n in ("restart_service", "set_start_type",
                          "delete_service", "restart_all_services")
              if n in read_menu]
    print(f"menu     : read-only names {len(allowed_tools(READ_ONLY))} tool(s), "
          f"leaked={leaked}")
    if leaked:
        print("FAIL: a mutating tool appeared in a read-only menu")
        failures.append("menu")
    if read_menu == tool_menu():
        print("FAIL: the filtered menu is the unfiltered menu")
        failures.append("menu")
    if len(allowed_tools(Policy(ceiling="destructive"))) != len(REGISTRY):
        print("FAIL: a destructive ceiling did not permit every tool")
        failures.append("menu")

    # 9. Every decision is on the record. The audit question is never "did it
    #    crash", it is "what did it try to do, and who said no".
    g = Guard(SUPERVISED, approver=always_yes)
    for call in (READ_CALL, WRITE_CALL, NUKE_CALL):
        g.run(call)
    print(f"audit    : {[(d.tool, d.allowed) for d in g.log]}")
    if len(g.log) != 3:
        print("FAIL: the audit log did not record every decision")
        failures.append("audit")
    reset_services()

    if SERVICES != {"Spooler":  {"status": "stopped", "start_type": "automatic"},
                    "W32Time":  {"status": "running", "start_type": "automatic"},
                    "WinRM":    {"status": "running", "start_type": "automatic"},
                    "wuauserv": {"status": "stopped", "start_type": "manual"}}:
        print("FAIL: the fixture did not survive the smoke test")
        failures.append("fixture")

    # --- Lab 13: the human gate ----------------------------------------
    # 10. A call that cannot be SHOWN is refused, and nobody is asked. Fax
    #     does not exist, so there is no diff to draw.
    asked.clear()
    g = Guard(SUPERVISED, approver=counting_yes)
    ghost = g.run({"tool": "restart_service", "arguments": {"name": "Fax"}})
    print(f"unshown  : refused={not ghost.ok}, approver consulted "
          f"{len(asked)} time(s), spent={g.spent}")
    if ghost.ok or asked or g.spent:
        print("FAIL: a call nobody could preview reached the approver")
        failures.append("unshown")

    # 11. The approver sees the ask and the diff, not a bare tool name.
    g = Guard(SUPERVISED, approver=counting_yes)
    g.run(WRITE_CALL, intent="Get the print spooler running.")
    shown = asked[-1]
    print(f"shown    : intent={shown.intent!r}, "
          f"diff={[(c.target, c.before, c.after) for c in shown.changes]}, "
          f"screen={shown.fingerprint}")
    if shown.intent != "Get the print spooler running." or not shown.changes \
            or g.log[-1].fingerprint != shown.fingerprint:
        print("FAIL: the approver was not shown the change it approved")
        failures.append("shown")
    reset_services()

    # 11b. WHO SAID NO. A person's refusal and a rule's refusal must read
    #      differently, because the error text is all the model gets and it
    #      WILL explain the refusal to the user from it.
    by_person = Guard(SUPERVISED).run(WRITE_CALL).error
    by_rule = Guard(READ_ONLY).run(WRITE_CALL).error
    print(f"who      : person -> {by_person[:30]!r}; rule -> {by_rule[:28]!r}")
    if not by_person.startswith("REFUSED BY APPROVER") \
            or not by_rule.startswith("REFUSED BY POLICY") \
            or "'yes'" in by_person:
        print("FAIL: the refusal does not say who refused, or leaks the answer")
        failures.append("who refused")

    # 12. ONLY True IS YES. An approver that answers the string "no" -
    #     truthy - must not move the host. Lab 11's `if not approver(p)`
    #     would have run this restart.
    g = Guard(SUPERVISED, approver=lambda p: "no")
    truthy = g.run(WRITE_CALL)
    print(f"truthy   : refused={not truthy.ok} - {g.log[-1].reason}")
    if truthy.ok or SERVICES["Spooler"]["status"] != "stopped":
        print("FAIL: a truthy non-answer was treated as approval")
        failures.append("only True")

    # 13. STALE APPROVAL. The approver says yes - and while the screen is up,
    #     somebody else starts the Spooler. The yes was to "stopped ->
    #     running"; the host is no longer stopped. Void, not executed, and
    #     no budget spent.
    def yes_but_meanwhile(proposal: Proposal) -> bool:
        SERVICES["Spooler"]["status"] = "running"   # the other operator
        SERVICES["Spooler"]["start_type"] = "manual"
        return True

    g = Guard(SUPERVISED, approver=yes_but_meanwhile)
    raced = g.run(WRITE_CALL)
    print(f"stale    : refused={not raced.ok}, spent={g.spent} - "
          f"{g.log[-1].reason}")
    if raced.ok or g.spent or g.log[-1].allowed:
        print("FAIL: an approval for a state that no longer exists was honoured")
        failures.append("stale")
    if SERVICES["Spooler"]["start_type"] != "manual":
        print("FAIL: the stale call ran anyway")
        failures.append("stale")
    reset_services()

    # 14. And the same approval, host untouched, DOES run - the staleness
    #     check must not refuse the ordinary case, or it gets switched off.
    g = Guard(SUPERVISED, approver=always_yes)
    fresh = g.run(WRITE_CALL)
    print(f"fresh    : ok={fresh.ok}, spent={g.spent}, reason={g.log[-1].reason!r}")
    if not fresh.ok or g.spent != 1:
        print("FAIL: an up-to-date approval was refused")
        failures.append("fresh")
    reset_services()

    if failures:
        print(f"FAIL: {', '.join(failures)}")
        sys.exit(1)
    print("PASS: rated, walled, budgeted, rehearsable, shown, current, "
          "and on the record")
    sys.exit(0)
