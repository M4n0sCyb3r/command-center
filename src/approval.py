"""The human gate: what a person is shown, and what counts as yes.

Lab 11 built the approver seam and every test since has stubbed it with a
lambda. This module is what goes in that seam when the approver is a person,
and the lab is about one uncomfortable fact: A GATE THAT SHOWS NOTHING IS
WORSE THAN NO GATE. "restart_service? [y/N]" teaches the operator to press y,
and a trained reflex is not a control - it is a delay with a signature on it,
which is exactly the evidence an audit will use to say a human approved it.

Three ways a human gate fails, and the three things this file does about them:

  BLIND       the person is shown the name of an action, not its effect.
              -> render() shows the DIFF against the host as it is now, how
                 much of the host it touches, and whether it can be undone.

  REFLEXIVE   yes is too cheap - Enter, or "y", for anything.
              -> the required answer scales with blast radius: "yes" for a
                 write, the target's exact name for a destructive call. The
                 default is refusal, and so is every surprise (EOF, Ctrl+C,
                 a typo). The time taken is recorded, because a two-second
                 approval of a delete is a finding even when it is legal.

  STALE       the person approved the truth at 10:02; the call runs at 10:07
              against a host somebody else changed in between.
              -> the approval is bound to a FINGERPRINT of the state it was
                 shown. guard.py recomputes it immediately before dispatch,
                 and a mismatch voids the approval.

Where the gate belongs: on the CHANGE, at the moment of the change, after
every rule that could have refused it without a person. Not on the agent at
the start of the run - "approve this agent to have write access" is a policy
decision wearing an approval's clothes, and nobody can judge forty calls they
have not seen yet.

    python src/approval.py; echo "exit: $?"          # hermetic
    HUMAN=1 python src/approval.py                   # a real person, a real model
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from tools import SERVICES, Change, Tool

# Below this many seconds, an approval is flagged HASTY. Flagged, not
# refused: a rule that refuses fast answers teaches people to count to three
# before pressing Enter, which measures patience rather than attention. The
# flag is for the person reading the audit trail next week.
FAST_S = 2.0


class PreviewError(Exception):
    """The call cannot be shown to a person, so it cannot be approved."""


@dataclass(frozen=True)
class Proposal:
    """What the approver is shown. Everything it needs, nothing it doesn't.

    Moved here from guard.py at Lab 13 and extended. The first five fields
    are Lab 11's, unchanged, so every stub written against them still works.

    Two decisions about what is NOT on this screen, and both are Lab 7's
    ruling applied to a person: the model's reasoning is absent (hand
    someone the argument and review turns into agreement), and so is the
    transcript. What IS here that Lab 11 lacked is `intent` - the operator's
    own request, in their own words. That is the one judgement a person can
    make that no rule in guard.py can: does this action serve what was
    actually asked? Show the ASK, never the ARGUMENT.
    """

    tool: str
    risk: str
    arguments: dict[str, Any]
    spent: int                                  # mutating calls already made this run
    budget: int                                 # how many are allowed in total
    intent: str = ""                            # what the operator asked for
    changes: tuple[Change, ...] = ()            # the diff, against the host as it is NOW
    undo: str | None = None                     # None = cannot be undone
    scope: str = ""                             # "1 of 4 services"
    fingerprint: str = ""                       # the state this screen describes


@dataclass(frozen=True)
class Verdict:
    """What a person decided, and enough about HOW to audit it later.

    `fingerprint` is the whole anti-staleness mechanism: a yes is a yes to a
    particular screen, not to a tool name. `seconds` is measured on the
    monotonic clock (Lab 12's ruling: never subtract wall clocks).
    """

    approved: bool
    fingerprint: str
    answer: str | None = None       # what they actually typed, clipped
    seconds: float | None = None    # how long the screen was up
    hasty: bool = False             # approved faster than FAST_S
    reason: str = ""


def fingerprint(tool: str, arguments: dict[str, Any],
                changes: tuple[Change, ...]) -> str:
    """A short hash of the call AND the before-state it would act on.

    The call is in it so an approval for "restart Spooler" cannot be
    replayed for "restart WinRM". The `before` values are in it so the same
    call against a host that has moved on is a different screen. `after`
    is deliberately NOT in it - that is derived from the call, and adding it
    would add nothing but a second way to disagree.
    """
    payload = json.dumps(
        {"tool": tool, "arguments": arguments,
         "before": [[c.target, c.field, c.before] for c in changes]},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def scope_of(changes: tuple[Change, ...]) -> str:
    """Blast radius in words: how much of the host this call TOUCHES.

    Counted over targets touched, not fields changed. restart_all_services
    changes two fields on this host and interrupts four services, and the
    number that matters at 2am is four.
    """
    touched = {c.target for c in changes}
    # Deleted targets are not in SERVICES afterwards, but they are now.
    total = len(set(SERVICES) | touched)
    words = f"{len(touched)} of {total} services"
    if total and len(touched) == total:
        words += " - THE WHOLE HOST"
    return words


def build_proposal(tool: Tool, arguments: dict[str, Any], *, intent: str = "",
                   spent: int = 0, budget: int = 0) -> Proposal:
    """Preview the call and package everything a person needs to judge it.

    Raises PreviewError, never anything else. A preview that cannot be
    produced - no preview declared, a target that does not exist, arguments
    the preview cannot take - means the call cannot be shown, and the
    caller's job is to refuse it WITHOUT waking anyone. Asking a person to
    approve a call nobody can describe is asking them to guess.
    """
    if tool.preview is None:
        raise PreviewError(f"{tool.name} declares no preview")
    try:
        changes = tuple(tool.preview(**arguments))
    except Exception as exc:        # noqa: BLE001 - preview is untrusted too
        raise PreviewError(f"{tool.name} cannot be previewed: "
                           f"{type(exc).__name__}: {exc}") from exc
    if not changes:
        raise PreviewError(f"{tool.name} previewed no changes; "
                           f"there is nothing to approve")
    return Proposal(
        tool=tool.name, risk=tool.risk, arguments=dict(arguments),
        spent=spent, budget=budget, intent=intent, changes=changes,
        undo=tool.undo, scope=scope_of(changes),
        fingerprint=fingerprint(tool.name, arguments, changes),
    )


def required_answer(proposal: Proposal) -> str:
    """What the person must type. Friction proportional to blast radius.

    A write takes "yes" - a word, not a key, so it cannot be produced by a
    finger resting on Enter. A destructive call takes the target's exact
    name, or the COUNT of targets when there are several, because you
    cannot type the count without having read the scope line. This is the
    GitHub "type the repository name to delete it" pattern, and it works for
    the same reason: it forces the eyes onto the one fact that matters.
    """
    if proposal.risk != "destructive":
        return "yes"
    targets = sorted({c.target for c in proposal.changes})
    return targets[0] if len(targets) == 1 else str(len(targets))


def _clip(text: str | None, limit: int = 60) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return f"{text[:limit]}...(+{len(text) - limit} chars)"


def render(proposal: Proposal) -> str:
    """The screen. Plain text, fixed order, same shape every time.

    Same shape every time is a design choice, not laziness: a reader who
    knows where the scope line is will look at it, and a screen that
    rearranges itself trains people to skim.
    """
    rule = "=" * 72
    lines = [
        rule,
        f"APPROVAL REQUIRED{'risk: ' + proposal.risk.upper():>55}",
        "-" * 72,
        f"Asked    : {proposal.intent or '(no request recorded - be suspicious)'}",
        f"Action   : {proposal.tool} {json.dumps(proposal.arguments, sort_keys=True)}",
        f"Touches  : {proposal.scope}",
        "Change   :",
        f"    {'target':<10} {'field':<11} {'before':<10}    {'after':<10} effect",
    ]
    for c in proposal.changes:
        same = c.before == c.after
        effect = f"{c.effect} (no state change - still an interruption)" \
            if same and c.effect == "restart" else c.effect
        arrow = "==" if same else "->"
        lines.append(f"    {c.target:<10} {c.field:<11} {str(c.before):<10} "
                     f"{arrow} {str(c.after):<10} {effect}")
    lines += [
        f"Undo     : {proposal.undo or 'CANNOT BE UNDONE'}",
        f"Budget   : this would be mutating call {proposal.spent + 1} of "
        f"{proposal.budget} allowed this run",
        f"Screen   : {proposal.fingerprint}  (your answer applies to this exact state)",
        rule,
    ]
    return "\n".join(lines)


def drain_typeahead() -> bool:
    """Throw away anything typed BEFORE the screen was drawn.

    Found in the first live run of Lab 13. The operator typed "no" while the
    model was still thinking; the line sat in the terminal's input buffer,
    and the instant the screen appeared input() read it - an answer recorded
    0.0 seconds after a screen nobody had seen. It was a refusal that time.
    Typed early as "wuauserv", it would have approved a change that CANNOT
    BE UNDONE against a screen nobody read.

    Returns True if the buffer was actually flushed. Only a real terminal has
    one to flush: a pipe (the tests) or a platform with no termios returns
    False, and approve() records that, so an audit can tell a clean prompt
    from one that could not be cleaned.
    """
    import sys
    try:
        import termios
        if not sys.stdin.isatty():
            return False
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
        return True
    except Exception:           # noqa: BLE001 - no termios, closed stdin
        return False


def console_approver(read: Callable[[str], str] = input,
                     write: Callable[[str], Any] = print,
                     clock: Callable[[], float] = time.monotonic,
                     hasty_below: float = FAST_S,
                     drain: Callable[[], bool] = drain_typeahead
                     ) -> Callable[[Proposal], Verdict]:
    """An approver that asks a person at a terminal.

    `read`, `write`, `clock` and `drain` are injectable - the approver seam
    has a seam of its own, because a gate you can only test by sitting at it
    is a gate you will test once. The smoke test below scripts the person.

    THE ORDER IS THE CONTROL: draw the screen, THEN discard typeahead, THEN
    start the clock and read. Only keystrokes made after the screen existed
    can count as an answer to it.
    """

    def approve(proposal: Proposal) -> Verdict:
        write(render(proposal))
        want = required_answer(proposal)
        drained = drain()
        started = clock()
        try:
            raw: str | None = read(f"Type {want!r} to approve. Anything else refuses: ")
        except (EOFError, KeyboardInterrupt):
            # No terminal, a closed pipe, or Ctrl+C. All three mean nobody
            # said yes, so all three are a no - and none is a crash, because
            # a gate that throws takes the audit record down with it.
            raw = None
        took = round(clock() - started, 3)

        given = None if raw is None else raw.strip()
        if want == "yes":
            approved = given is not None and given.lower() == "yes"
        else:
            # Exact, case-sensitive: the tool is case-sensitive about names,
            # so the confirmation is too. "spooler" is not "Spooler".
            approved = given == want
        # The reason goes into the audit record AND, through guard.py, into
        # the model's transcript. So it states what happened and nothing
        # else - in particular it never repeats the confirmation word, which
        # belongs on the operator's screen and nowhere a model can read it.
        if raw is None:
            reason = "no answer from the operator (end of input)"
        elif approved:
            reason = "approved by the operator"
        else:
            reason = "the operator declined this change"
        if not drained:
            # Not a refusal - tests and pipes legitimately cannot flush. A
            # note on the record, so it is visible rather than assumed.
            reason += " [typeahead not cleared]"
        return Verdict(approved=approved, fingerprint=proposal.fingerprint,
                       answer=_clip(given), seconds=took,
                       hasty=approved and took < hasty_below, reason=reason)

    return approve


def as_verdict(raw: Any, proposal: Proposal) -> Verdict:
    """Turn whatever an approver returned into a Verdict. ONLY True IS YES.

    Lab 11's stubs return bools and must keep working, so a bool is
    accepted. Everything else is judged strictly, because Python's idea of
    truth is not an approval: `"no"` is truthy, `1` is truthy, a non-empty
    dict is truthy, and `if approver(p):` would have approved all three.

    Checked by attribute rather than isinstance, on purpose. When this file
    runs as a script it is loaded TWICE - once as `__main__`, once as
    `approval` when guard.py imports it - and the two Verdict classes are
    different objects. An isinstance check would call a real refusal
    "not a Verdict" and fall through; duck typing on `approved is True`
    cannot be fooled that way.
    """
    if raw is True:
        return Verdict(True, proposal.fingerprint, reason="approved")
    if raw is False:
        return Verdict(False, proposal.fingerprint, reason="the approver said no")
    approved = getattr(raw, "approved", None)
    if approved is True:
        if getattr(raw, "fingerprint", None) != proposal.fingerprint:
            return Verdict(False, proposal.fingerprint,
                           reason="approval was for a different screen")
        return Verdict(True, raw.fingerprint, getattr(raw, "answer", None),
                       getattr(raw, "seconds", None),
                       bool(getattr(raw, "hasty", False)),
                       getattr(raw, "reason", "approved"))
    if approved is False:
        return Verdict(False, proposal.fingerprint, getattr(raw, "answer", None),
                       getattr(raw, "seconds", None), False,
                       getattr(raw, "reason", "the approver said no"))
    return Verdict(False, proposal.fingerprint,
                   reason=f"approver returned {type(raw).__name__} "
                          f"{_clip(repr(raw), 30)}; only True is yes")


# --- smoke test -----------------------------------------------------------
# Hermetic. Seven properties about the SCREEN and the ANSWER; the properties
# about the GATE (preview-or-refuse, staleness) live in guard.py's test,
# because that is the file that enforces them.

if __name__ == "__main__":
    import os
    import sys

    from tools import REGISTRY, reset_services

    if os.environ.get("HUMAN") == "1":
        # The live demo. Imports come from the MODULES, not from this
        # __main__ copy - see as_verdict() for why that matters.
        from agent import run
        from approval import console_approver as live_approver
        from guard import Guard, Policy

        reset_services()
        policy = Policy(ceiling="destructive", approve_above="read", max_writes=2)
        for question in (
            "The print spooler is stopped. Get it running.",
            "Remove the Windows Update service (wuauserv) from this host.",
        ):
            print(f"\n>>> {question}")
            gate = Guard(policy, approver=live_approver())
            result = run(question, guard=gate)
            print(f"answer   : {result.answer}")
            asked_about = [d for d in gate.log if d.risk != "read"]
            if not asked_about:
                print("decision : the model proposed no change, so no one was asked")
            for d in asked_about:
                print(f"decision : {d.tool} allowed={d.allowed} "
                      f"typed={d.answer!r} seconds={d.approver_s} "
                      f"hasty={d.hasty} - {d.reason}")
        print(f"\nhost now : {SERVICES}")
        reset_services()
        sys.exit(0)

    failures: list[str] = []
    reset_services()
    pristine = {n: dict(r) for n, r in SERVICES.items()}

    def scripted(*answers: str | None):
        """A person who types these answers, in order. None = Ctrl+D."""
        queue = list(answers)

        def read(prompt: str) -> str:
            value = queue.pop(0)
            if value is None:
                raise EOFError
            return value
        return read

    screens: list[str] = []
    restart = build_proposal(REGISTRY["restart_service"], {"name": "Spooler"},
                             intent="Restart the Spooler service.", budget=3)
    delete = build_proposal(REGISTRY["delete_service"], {"name": "Spooler"},
                            intent="Clean up the print services.", budget=1)
    wide = build_proposal(REGISTRY["restart_all_services"], {}, budget=1)

    # 1. The screen shows the EFFECT, not just the name: the diff against the
    #    host as it is right now, the scope, the undo, the budget, the ask.
    screen = render(restart)
    print(screen)
    needed = ["Restart the Spooler service.", "stopped    -> running",
              "1 of 4 services", "stop the service again", "call 1 of 3",
              restart.fingerprint]
    missing = [n for n in needed if n not in screen]
    if missing:
        print(f"FAIL: the screen is missing {missing}")
        failures.append("screen")

    # 2. Blast radius counts what is TOUCHED. restart_all changes two fields
    #    and interrupts four services; the screen has to say four, and has
    #    to say that a running->running line is still an interruption.
    wide_screen = render(wide)
    print(f"wide     : {wide.scope}; {wide_screen.count('still an interruption')} "
          f"zero-diff line(s) marked; ask shown as "
          f"{'(no request' in wide_screen}")
    if "4 of 4 services - THE WHOLE HOST" not in wide_screen \
            or wide_screen.count("still an interruption") != 2:
        print("FAIL: the wide call understated its blast radius")
        failures.append("scope")
    if "CANNOT BE UNDONE" not in render(delete):
        print("FAIL: an irreversible call did not say so")
        failures.append("undo")

    # 3. The fingerprint is bound to the call AND the state. Same call, same
    #    host -> same screen. Different target, or a host that has moved ->
    #    a different screen, so an old yes cannot be reused.
    again = build_proposal(REGISTRY["restart_service"], {"name": "Spooler"})
    other = build_proposal(REGISTRY["restart_service"], {"name": "WinRM"})
    SERVICES["Spooler"]["status"] = "running"
    moved = build_proposal(REGISTRY["restart_service"], {"name": "Spooler"})
    reset_services()
    print(f"prints   : same={again.fingerprint == restart.fingerprint} "
          f"other-target={other.fingerprint != restart.fingerprint} "
          f"host-moved={moved.fingerprint != restart.fingerprint}")
    if not (again.fingerprint == restart.fingerprint
            and other.fingerprint != restart.fingerprint
            and moved.fingerprint != restart.fingerprint):
        print("FAIL: the fingerprint does not pin the call and the state")
        failures.append("fingerprint")

    # 4. A call that cannot be shown raises PreviewError - one exception
    #    type, so the guard has one thing to catch.
    try:
        build_proposal(REGISTRY["restart_service"], {"name": "Fax"})
    except PreviewError as exc:
        print(f"unshown  : {exc}")
    else:
        print("FAIL: a call against a missing service produced a screen")
        failures.append("preview error")

    # 5. THE MEANINGFUL YES. Scripted people, one answer each.
    def verdicts(proposal: Proposal, *answers: str | None) -> list[bool]:
        out = []
        for a in answers:
            v = console_approver(read=scripted(a), write=screens.append)(proposal)
            out.append(v.approved)
        return out

    write_answers = ["", "y", "sure", "YES", " yes ", None]
    write_got = verdicts(restart, *write_answers)
    print(f"write    : {list(zip(write_answers, write_got))}")
    if write_got != [False, False, False, True, True, False]:
        print("FAIL: a write was approved by something other than 'yes'")
        failures.append("meaningful yes")

    delete_answers = ["yes", "spooler", "Spooler"]
    delete_got = verdicts(delete, *delete_answers)
    wide_got = verdicts(wide, "yes", "4")
    print(f"delete   : {list(zip(delete_answers, delete_got))}; "
          f"restart_all: yes={wide_got[0]} 4={wide_got[1]}")
    if delete_got != [False, False, True] or wide_got != [False, True]:
        print("FAIL: a destructive call was approved without naming its target")
        failures.append("friction")

    # 6. Time is part of the record. A scripted clock: 0.4s and 9s.
    ticks = iter([100.0, 100.4, 200.0, 209.0])
    fast = console_approver(read=scripted("yes"), write=screens.append,
                            clock=lambda: next(ticks))(restart)
    slow = console_approver(read=scripted("yes"), write=screens.append,
                            clock=lambda: next(ticks))(restart)
    print(f"timing   : {fast.seconds}s hasty={fast.hasty}; "
          f"{slow.seconds}s hasty={slow.hasty}")
    if not fast.hasty or slow.hasty:
        print("FAIL: approval latency is not being judged")
        failures.append("hasty")

    # 8. TYPEAHEAD. The order is the control: screen, then drain, then
    #    read. A recording person proves the sequence, and a drain that
    #    reports failure must leave a mark on the record.
    order: list[str] = []
    recorder = console_approver(
        read=lambda prompt: order.append("read") or "yes",
        write=lambda text: order.append("screen"),
        drain=lambda: order.append("drain") or True)
    cleaned = recorder(restart)
    uncleaned = console_approver(read=scripted("yes"), write=screens.append,
                                 drain=lambda: False)(restart)
    print(f"order    : {order}; cleared reason={cleaned.reason!r}; "
          f"uncleared reason={uncleaned.reason!r}")
    if order != ["screen", "drain", "read"]:
        print("FAIL: input could be read before the screen was drawn and cleared")
        failures.append("typeahead")
    if "typeahead" in cleaned.reason or "typeahead not cleared" not in uncleaned.reason:
        print("FAIL: the record does not say whether typeahead was cleared")
        failures.append("typeahead")

    # 7. ONLY True IS YES. Every one of these is truthy in Python.
    lies = ["no", 1, {"approved": True}, object()]
    judged = [as_verdict(x, restart).approved for x in lies]
    stale = as_verdict(Verdict(True, "000000000000"), restart)
    print(f"truthy   : {judged}; yes-to-another-screen -> {stale.approved} "
          f"({stale.reason})")
    if any(judged) or stale.approved or not as_verdict(True, restart).approved:
        print("FAIL: something other than True was read as approval")
        failures.append("only True")

    if SERVICES != pristine:
        print("FAIL: building a screen changed the host")
        failures.append("side effect")

    if failures:
        print(f"FAIL: {', '.join(failures)}")
        sys.exit(1)
    print("PASS: shown the effect, asked for a real yes, timed, and bound "
          "to the state it described")
    sys.exit(0)
