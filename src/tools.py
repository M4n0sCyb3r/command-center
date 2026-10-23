"""Tool calling from scratch.

A model cannot run anything. It emits a *request* - a tool name and some
arguments - and this module decides whether to honour it and does the running.

Lab 11 added one field: every tool declares its BLAST RADIUS at registration.
That field is declared here and acted on in guard.py, and the split is
deliberate - the person who wrote the function knows whether it mutates
anything, and the person running the agent decides what is permitted today.
Those are different jobs and they live in different files.

Lab 13 added the second field: every tool that CHANGES the host declares a
PREVIEW - a function that says, from the current state and without touching
it, exactly what the call would change. PowerShell calls this -WhatIf and
Ansible calls it --check --diff. The human gate shows it to a person, and a
tool that cannot say what it will do is a tool nobody can approve.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from jsonschema import ValidationError, validate

from structured import ask_json

# Fake Windows services. Deliberately fake: a tool that shells out to
# `sc query` returns different data every day, and Phase III cannot evaluate
# an agent whose ground truth moves. Real targets arrive at Project 1.
#
# From Lab 11 this dict is also MUTABLE STATE - the write tools change it.
# Anything that mutates it in a test must restore it; see reset_services().
SERVICES = {
    "Spooler":  {"status": "stopped", "start_type": "automatic"},
    "W32Time":  {"status": "running", "start_type": "automatic"},
    "WinRM":    {"status": "running", "start_type": "automatic"},
    "wuauserv": {"status": "stopped", "start_type": "manual"},
}

# The pristine copy. Labs 8, 9 and 10 all take their ground truth from
# SERVICES, so a test that leaves it mutated does not fail loudly - it
# quietly changes the answer key for every eval that runs after it.
_BASELINE = {name: dict(record) for name, record in SERVICES.items()}


def reset_services() -> None:
    """Restore SERVICES to its baseline. Call after any test that mutates."""
    SERVICES.clear()
    SERVICES.update({name: dict(record) for name, record in _BASELINE.items()})


# --- blast radius ---------------------------------------------------------
# The vocabulary, in increasing order of what a wrong call costs you. The
# ORDER is load-bearing: guard.py compares positions in this tuple, so a
# policy that permits "write" permits "read" without listing it.
#
#   read         observes; changes nothing. A wrong call wastes a turn.
#   write        changes state, and the change can be undone.
#   destructive  irreversible, or so wide that undoing it is a project.
RISK_LEVELS = ("read", "write", "destructive")


# --- previews (Lab 13) ----------------------------------------------------


@dataclass(frozen=True)
class Change:
    """One line of a diff: what a call would do to one field of one target.

    `effect` is there because a state diff can UNDERSTATE an action.
    Restarting a service that is already running is `running -> running` -
    a zero-line diff - and it is still an interruption. The approver has to
    see every target the call TOUCHES, not only the fields that end up
    different, so the effect travels with the line.
    """

    target: str
    field: str
    before: Any
    after: Any
    effect: str


def current(target: str, field: str) -> Any:
    """Read one field of the host as it is right now.

    The single reader previews use, so "what is it now" has one answer.
    `exists` is a pseudo-field for tools that add or remove a whole service.
    """
    if field == "exists":
        return "yes" if target in SERVICES else "no"
    if target not in SERVICES:
        raise KeyError(f"no such service: {target!r}")
    return SERVICES[target][field]


@dataclass
class Tool:
    """One tool: a function, plus everything the model needs to ask for it."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable
    # Two layers of fail-closed, and they guard different mistakes.
    # register() makes `risk` REQUIRED, so a tool declared through the
    # decorator cannot be unclassified - forgetting it is a TypeError at
    # import, which is the loudest and cheapest failure available.
    # This default only catches a Tool built by hand around the decorator,
    # and it defaults to the WORST level rather than the safest: if nobody
    # said how dangerous it is, assume the answer is "very".
    risk: str = "destructive"
    # Lab 13. What the call WOULD do, computed without doing it. None on a
    # hand-built Tool means "cannot be shown", and the gate refuses what it
    # cannot show - the same fail-closed direction as `risk` above.
    preview: Callable[..., list[Change]] | None = None
    # How to put it back, in words a person can act on. None means it
    # cannot be put back, and the screen says so in capitals. Missing is
    # read as the WORSE answer, again on purpose.
    undo: str | None = None


REGISTRY: dict[str, Tool] = {}


def register(name: str, description: str, parameters: dict[str, Any], risk: str,
             preview: Callable[..., list[Change]] | None = None,
             undo: str | None = None):
    """Decorator. Files a function in REGISTRY under `name`.

    `risk` is required and must be one of RISK_LEVELS. Both halves of that
    matter: omitting it is a TypeError from Python itself, and inventing a
    level ("harmless", "safe-ish") is a ValueError from us. Either way the
    module fails to import, which is the loudest failure available and the
    cheapest one to fix.

    Lab 13: a tool rated above `read` must also pass `preview=`. Same
    reasoning, same place: a mutating tool with no preview can never be
    shown to an approver, so it can never be approved, so declaring it is a
    mistake - and the cheapest time to find that mistake is import.
    """

    if risk not in RISK_LEVELS:
        raise ValueError(
            f"tool {name!r} declared risk={risk!r}; must be one of {RISK_LEVELS}"
        )
    if risk != "read" and preview is None:
        raise ValueError(
            f"tool {name!r} changes the host (risk={risk!r}) but declares no "
            f"preview; a change nobody can see is a change nobody can approve"
        )

    def decorator(fn: Callable) -> Callable:
        if name in REGISTRY:
            raise ValueError(f"two tools are both named {name!r}")
        REGISTRY[name] = Tool(
            name=name,
            description=description,
            parameters=parameters,
            fn=fn,
            risk=risk,
            preview=preview,
            undo=undo,
        )
        return fn      # unchanged on purpose

    return decorator


@register(
    name="get_service_status",
    description=(
        "Look up one Windows service by its exact service name (for example "
        "'Spooler' or 'W32Time') and return its status and startup type."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Exact service name, case-sensitive.",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    },
    risk="read",
)
def get_service_status(name: str) -> dict[str, str]:
    """Return {name, status, start_type} for one service."""
    if name not in SERVICES:
        raise KeyError(f"no such service: {name!r}")
    record = SERVICES[name]
    return {
        "name": name,
        "status": record["status"],
        "start_type": record["start_type"],
    }


@register(
    name="list_services",
    description="List the names of every service on the host. Takes no arguments.",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    risk="read",
)
def list_services() -> list[str]:
    """Return every known service name."""
    return sorted(SERVICES)


# --- the dangerous half ---------------------------------------------------
# Everything above this line observes. Everything below it CHANGES the host,
# which is what makes guard.py necessary rather than decorative.
#
# Note the pair `restart_service` / `restart_all_services`: same verb, same
# reversibility, different rating. Blast radius is SCOPE x REVERSIBILITY, and
# a three-level enum only has room for one number - so the rule is that the
# level is the WORSE of the two axes. `delete_service` is destructive because
# it cannot be undone; `restart_all_services` is destructive because it takes
# down the whole host at once, and "reversible" is no comfort during the
# outage. If those axes ever need to be reasoned about separately, this field
# has to become two fields.
#
# Lab 13: each tool below has a PREVIEW defined just above it. Two functions
# describing one action is a real risk - they can drift, and a preview that
# says one thing while the tool does another is worse than no preview,
# because it is believed. They sit side by side so a change to one is a
# visible reason to read the other. Checking the preview against what
# actually happened is Project 5's job (post-change verification).


def _preview_restart(name: str) -> list[Change]:
    return [Change(name, "status", current(name, "status"), "running", "restart")]


@register(
    name="restart_service",
    description=(
        "Restart one Windows service by its exact service name. The service "
        "is left running. Changes the state of the host."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Exact service name, case-sensitive.",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    },
    risk="write",
    preview=_preview_restart,
    undo="stop the service again by hand - no stop tool is registered",
)
def restart_service(name: str) -> dict[str, str]:
    """Set one service running. Reversible: stop it again."""
    if name not in SERVICES:
        raise KeyError(f"no such service: {name!r}")
    before = SERVICES[name]["status"]
    SERVICES[name]["status"] = "running"
    return {"name": name, "was": before, "now": "running"}


def _preview_start_type(name: str, start_type: str) -> list[Change]:
    return [Change(name, "start_type", current(name, "start_type"),
                   start_type, "set")]


@register(
    name="set_start_type",
    description=(
        "Change one Windows service's startup type to automatic, manual or "
        "disabled. Changes the state of the host."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Exact service name, case-sensitive.",
            },
            "start_type": {
                "type": "string",
                "enum": ["automatic", "manual", "disabled"],
            },
        },
        "required": ["name", "start_type"],
        "additionalProperties": False,
    },
    risk="write",
    preview=_preview_start_type,
    undo="call set_start_type again with the 'before' value shown",
)
def set_start_type(name: str, start_type: str) -> dict[str, str]:
    """Change one service's startup type. Reversible: set it back.

    Rated `write` because the change is undone by one more call. Worth
    noticing that start_type="disabled" is materially worse than a restart -
    the service will not come back after a reboot - and the rating does not
    see that, because a three-level enum reasons about the TOOL and not about
    one argument's value. A policy that cares would need an argument-level
    rule, which is a real thing that real systems have and this course does
    not build.
    """
    if name not in SERVICES:
        raise KeyError(f"no such service: {name!r}")
    before = SERVICES[name]["start_type"]
    SERVICES[name]["start_type"] = start_type
    return {"name": name, "was": before, "now": start_type}


def _preview_delete(name: str) -> list[Change]:
    if current(name, "exists") == "no":
        raise KeyError(f"no such service: {name!r}")
    return [Change(name, "exists", "yes", "no", "delete")]


@register(
    name="delete_service",
    description=(
        "Permanently remove a Windows service from the host. This cannot be "
        "undone."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Exact service name, case-sensitive.",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    },
    risk="destructive",
    preview=_preview_delete,
    undo=None,          # there is no undo, and the screen will say so
)
def delete_service(name: str) -> dict[str, str]:
    """Remove a service. NARROW scope, zero reversibility."""
    if name not in SERVICES:
        raise KeyError(f"no such service: {name!r}")
    SERVICES.pop(name)
    return {"name": name, "deleted": "true"}


def _preview_restart_all() -> list[Change]:
    return [Change(n, "status", current(n, "status"), "running", "restart")
            for n in sorted(SERVICES)]


@register(
    name="restart_all_services",
    description=(
        "Restart every service on the host at once. Takes no arguments. "
        "Every service is interrupted simultaneously."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    risk="destructive",
    preview=_preview_restart_all,
    undo="nothing to set back, but every service listed was interrupted - "
         "the outage itself cannot be taken back",
)
def restart_all_services() -> dict[str, Any]:
    """Restart everything. WIDE scope, full reversibility - still destructive.

    The rating that teaches the lesson. Nothing here is unrecoverable; every
    service can be stopped again by the tool above. It is destructive because
    scope alone can carry an action over the line, and an agent that reaches
    for this instead of four targeted calls has taken the host down to answer
    a question about one service.
    """
    changed = sorted(SERVICES)
    for record in SERVICES.values():
        record["status"] = "running"
    return {"restarted": changed, "count": len(changed)}


def tool_menu() -> str:
    """The menu, as text, for the system prompt."""
    lines = []
    for tool in REGISTRY.values():
        lines.append(f"- {tool.name}: {tool.description}")
        lines.append(f"  arguments: {json.dumps(tool.parameters)}")
    return "\n".join(lines)


def call_schema() -> dict[str, Any]:
    """The shape a tool request must have. Built FROM the registry."""
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": sorted(REGISTRY)},
            "arguments": {"type": "object"},
        },
        "required": ["tool", "arguments"],
        "additionalProperties": False,
    }


@dataclass
class ToolResult:
    """What happened when we tried to run a tool. Never raised - returned."""

    ok: bool
    tool: str
    # The call this result came from. Added at Lab 5: memory records what a
    # tool returned AND what it was asked, and the only place that pairing is
    # guaranteed correct is here, where the call was actually made. Passing
    # them separately would let a caller mismatch a step-4 result with
    # step-3 arguments.
    # default_factory=dict, not = {}: a bare {} default would be created once
    # and shared by every ToolResult ever made. Same trap as a mutable default
    # argument, same fix.
    arguments: dict[str, Any] = field(default_factory=dict)
    value: Any = None
    error: str | None = None


def dispatch(call: dict[str, Any]) -> ToolResult:
    """Take a proposed tool call and decide whether to honour it.

    Three gates, in order. Nothing executes until all three pass.

    Note what is NOT here: permission. This function asks "is this call
    well-formed?", never "is this call allowed?". Lab 11 puts that question
    in front of this one rather than inside it, so that the answer to
    "should I run it" and the answer to "can I run it" stay separable.
    """
    name = call.get("tool")
    # Pulled up out of gate 2: a failed call is now as traceable as a
    # successful one, because every exit below carries its arguments.
    args = call.get("arguments") or {}

    # Gate 1: does this tool exist? The decoder's enum should have made this
    # impossible - but that guarantee lives on the Ollama server, and Phase II
    # swaps in a frontier API that may only *try* to comply. Check it here.
    tool = REGISTRY.get(name)
    if tool is None:
        return ToolResult(ok=False, tool=str(name), arguments=args,
                          error=f"no such tool: {name!r}")

    # Gate 2: are the arguments legal? The tool's OWN schema decides, and
    # additionalProperties: False means an argument nobody wrote code for is
    # a rejection rather than a surprise keyword at call time.
    try:
        validate(instance=args, schema=tool.parameters)
    except ValidationError as exc:
        return ToolResult(ok=False, tool=tool.name, arguments=args,
                          error=f"bad arguments: {exc.message}")

    # Gate 3: the tool's own failure. Broad `except` on purpose: from here a
    # tool is untrusted code, and one tool raising must not kill the loop.
    try:
        value = tool.fn(**args)
    except Exception as exc:
        return ToolResult(ok=False, tool=tool.name, arguments=args,
                          error=f"{type(exc).__name__}: {exc}")

    return ToolResult(ok=True, tool=tool.name, arguments=args, value=value)


SYSTEM = (
    "You are a Windows operations assistant. You cannot run anything yourself. "
    "Pick exactly ONE tool from the menu below and return the call as JSON: "
    'the tool name in "tool", its arguments in "arguments".\n\n'
    "Tools:\n{menu}"
)


def propose(question: str, **kwargs: Any) -> dict[str, Any]:
    """Ask the model which tool it wants. Returns a proposal - nothing runs."""
    return ask_json(question, call_schema(), system=SYSTEM.format(menu=tool_menu()), **kwargs)


# --- smoke test -----------------------------------------------------------
# Asserts FOUR properties (plus Lab 13's seventh, below): the model can pick a real tool, an invented tool
# is refused, a malformed argument is refused BEFORE anything executes, and
# every registered tool carries a legal blast radius.

if __name__ == "__main__":
    import sys

    from model_client import ModelError

    failures: list[str] = []

    # 1. Happy path: the model proposes, dispatch disposes.
    try:
        call = propose("Is the print spooler running on this host?")
        print(f"proposed : {call}")
        result = dispatch(call)
        print(f"result   : {result}")
        if not result.ok:
            print(f"FAIL: dispatch refused a valid proposal: {result.error}")
            failures.append("happy path")
    except (ModelError, Exception) as exc:
        print(f"FAIL: happy path raised {type(exc).__name__}: {exc}")
        failures.append("happy path")

    # 2. Gate 1: a tool that does not exist. No model involved - we forge the
    #    call, because waiting for a model to misbehave on cue is not a test.
    forged = {"tool": "restart_domain_controller", "arguments": {}}
    bad_tool = dispatch(forged)
    if bad_tool.ok:
        print("FAIL: dispatch ran a tool that does not exist")
        failures.append("gate 1")
    else:
        print(f"gate 1   : refused - {bad_tool.error}")

    # 3. Gate 2: a real tool, an argument nobody wrote code for.
    forged = {"tool": "get_service_status",
              "arguments": {"name": "Spooler", "force": True}}
    bad_args = dispatch(forged)
    if bad_args.ok:
        print("FAIL: an unknown argument reached the function")
        failures.append("gate 2")
    else:
        print(f"gate 2   : refused - {bad_args.error}")

    # 4. Classification. Two halves, and both have to hold or the guard in
    #    Lab 11 is reasoning about a field that may be missing or invented.
    unclassified = [t.name for t in REGISTRY.values() if t.risk not in RISK_LEVELS]
    if unclassified:
        print(f"FAIL: tools with no legal blast radius: {unclassified}")
        failures.append("risk field")
    else:
        rated = {t.name: t.risk for t in REGISTRY.values()}
        print(f"risk     : {rated}")

    try:
        register(name="pretend", description="never registered",
                 parameters={"type": "object"}, risk="harmless")
    except ValueError as exc:
        print(f"risk gate: refused - {exc}")
    else:
        print("FAIL: an invented risk level was accepted")
        failures.append("risk gate")

    # 5. The tools that change things actually change things, and the
    #    fixture survives the test. Forged calls again - and note that
    #    dispatch() runs every one of these WITHOUT ASKING ANYONE, because
    #    permission is not its job and does not yet exist. That is the hole
    #    guard.py fills.
    before = dict(SERVICES["Spooler"])
    wrote = dispatch({"tool": "restart_service", "arguments": {"name": "Spooler"}})
    print(f"write    : {wrote.value}  (nothing asked permission)")
    if not wrote.ok or SERVICES["Spooler"]["status"] != "running":
        print("FAIL: a write tool did not write")
        failures.append("write")

    blew_up = dispatch({"tool": "restart_all_services", "arguments": {}})
    print(f"destruct : {blew_up.value}")
    if not blew_up.ok or blew_up.value["count"] != len(_BASELINE):
        print("FAIL: the wide tool did not reach every service")
        failures.append("destructive")

    # 7. Lab 13: every mutating tool can be PREVIEWED, and a preview changes
    #    nothing. Run BEFORE the fixture check below, so a preview that
    #    secretly wrote would be caught by it too.
    reset_services()
    snapshot = {n: dict(r) for n, r in SERVICES.items()}
    unshowable = [t.name for t in REGISTRY.values()
                  if t.risk != "read" and t.preview is None]
    diff = REGISTRY["restart_service"].preview(name="Spooler")
    wide = REGISTRY["restart_all_services"].preview()
    print(f"preview  : {diff[0].target} {diff[0].field} "
          f"{diff[0].before} -> {diff[0].after}; restart_all touches {len(wide)}")
    if unshowable:
        print(f"FAIL: mutating tools with no preview: {unshowable}")
        failures.append("preview")
    if (diff[0].before, diff[0].after) != ("stopped", "running"):
        print("FAIL: the preview does not describe the current state")
        failures.append("preview")
    if SERVICES != snapshot:
        print("FAIL: a preview changed the host")
        failures.append("preview")
    try:
        register(name="unshowable", description="never registered",
                 parameters={"type": "object"}, risk="write")
    except ValueError as exc:
        print(f"show gate: refused - {exc}")
    else:
        print("FAIL: a mutating tool with no preview was accepted")
        failures.append("preview")

    # 6. The fixture is the answer key for Labs 9 and 10. A test that leaves
    #    it mutated does not fail here - it silently changes the ground truth
    #    of every eval that runs after it.
    reset_services()
    if SERVICES != _BASELINE:
        print("FAIL: reset_services() did not restore the fixture")
        failures.append("reset")
    else:
        print(f"reset    : fixture restored, Spooler back to {before['status']!r}")

    if failures:
        print(f"FAIL: {', '.join(failures)}")
        sys.exit(1)
    print("PASS: model proposes, registry disposes, every tool is rated")
