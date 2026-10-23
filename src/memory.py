"""What the agent remembers, and how it can prove it.

Lab 4's loop keeps everything in one place: the `messages` list. That list is
a CONVERSATION, not a record of facts. It grows without bound, it mixes the
model's guesses in with the tools' answers, and it is thrown away the moment
run() returns.

This module separates the two. An Observation is something a tool actually
returned, stamped with the call that produced it and the moment it was true.
The transcript is how the agent got there; memory is what it is entitled to
claim.

The rule the whole module is built on:

    Store the observation, render the summary.

A summary can always be derived from an observation. An observation can never
be recovered from a summary.

    python src/memory.py; echo "exit: $?"
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from tools import ToolResult


def now_utc() -> str:
    """The moment an observation was made, as an ISO-8601 UTC string.

    timezone.utc, not the older utcnow(): utcnow() returns a datetime with no
    timezone attached, so it LOOKS like local time to anything that reads it
    back later. Deprecated since 3.12 for exactly that reason.

    Seconds resolution is deliberate. Microseconds are noise here, and they
    make every saved file diff against itself.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Observation:
    """One thing a tool actually returned.

    frozen=True: a remembered observation is never edited. If the world
    changed, the agent records a NEW observation with a later timestamp - it
    does not overwrite the old one. That is the same instinct as ToolResult
    returning instead of raising: the evidence survives the thing that
    happened to it.

    Honest caveat: frozen stops you rebinding these attributes, not mutating
    the dict inside `arguments`. Python cannot enforce the second one. The
    guarantee is a strong convention, not a wall.
    """

    step: int                    # which turn of the loop produced it
    tool: str                    # which tool was run ...
    arguments: dict[str, Any]    # ... and what it was asked. Together: provenance.
    value: Any                   # the tool's raw return, unmodified
    at: str                      # ISO-8601 UTC. Staleness has to be visible.


class Memory:
    """Everything the agent has actually observed, in the order it observed it.

    Not the transcript. The transcript is how the agent GOT here - it holds
    the model's guesses, its malformed turns, its own words. This holds only
    what a tool returned. That separation is the whole point: "everything in
    here was observed" has to be a claim you can stand behind.
    """

    def __init__(self, observations: list[Observation] | None = None) -> None:
        # list(...) makes a COPY, for two reasons. A caller who hands us a
        # list and then mutates it must not be able to reach in here. And the
        # default must not be a shared list - never write
        # `def __init__(self, observations=[])`. A default argument is
        # evaluated ONCE, when the def line runs at import, so every Memory
        # ever created would share the same list object. `or []` handles None.
        self._observations: list[Observation] = list(observations or [])

    def record(self, step: int, result: ToolResult) -> Observation | None:
        """Turn a successful tool result into a remembered observation.

        Failures are deliberately NOT recorded. A failed call is a fact about
        the RUN, not about the world: "no such tool" and "bad arguments" are
        the agent's own mistakes, and even a genuine exception is ambiguous -
        it could mean the service is absent, or the fixture is wrong, or the
        call was malformed. Lab 4's Step trace already records every failure.
        Memory holds only what a tool successfully returned.

        Returns the Observation it made, or None when there was nothing to
        record - the outcome comes back as a value the caller can branch on,
        the same instinct as ToolResult not raising.
        """
        if not result.ok:
            return None

        obs = Observation(
            step=step,
            tool=result.tool,
            arguments=result.arguments,
            value=result.value,
            at=now_utc(),
        )
        self._observations.append(obs)
        return obs

    def __len__(self) -> int:
        """Lets you write len(mem) instead of mem.count() - use the language's
        own vocabulary where it has one."""
        return len(self._observations)

    def latest(self) -> list[Observation]:
        """One observation per distinct call - the most recent of each.

        Storage keeps every observation, forever. Asking for the Spooler
        twice stores two records, because the older one is evidence of what
        was true earlier and destroying evidence is exactly what this module
        exists to avoid.

        But the PROMPT should show the current answer, not a history. So
        deduplication happens here, at read time, as a view over storage that
        changes nothing underneath it.

        The key is (tool, arguments), but a dict cannot be a dict key - it is
        mutable, so it is unhashable. json.dumps with sort_keys=True turns it
        into a canonical string, so {"a": 1, "b": 2} and {"b": 2, "a": 1}
        collapse to the same key instead of looking like two different calls.
        """
        newest: dict[tuple[str, str], Observation] = {}
        for obs in self._observations:
            key = (obs.tool, json.dumps(obs.arguments, sort_keys=True))
            newest[key] = obs        # a later observation replaces an earlier one
        return list(newest.values())

    def render(self) -> str:
        """The facts, as text the model can read.

        Three deliberate choices.

        It states the PROVENANCE on every line - which tool, which arguments,
        which step, and when. A bare "Spooler is stopped" is indistinguishable
        from something the model made up. With the call attached, it is
        checkable: you can run that exact call again and compare.

        It states the TIME, so staleness is visible. "Observed at 05:24" is a
        different claim from "is true now", and the model should be able to
        tell the difference when a run spans hours.

        And it returns an EMPTY STRING when there is nothing - not a heading
        with nothing under it. "KNOWN FACTS:" followed by blank space is an
        invitation to fill it in, which is precisely the failure this module
        was built to prevent.
        """
        facts = self.latest()
        if not facts:
            return ""

        lines = [
            "KNOWN FACTS - each was OBSERVED by actually running a tool.",
            "Do not add to this list from your own knowledge of Windows.",
        ]
        for obs in facts:
            args = ", ".join(f"{k}={v!r}" for k, v in sorted(obs.arguments.items()))
            lines.append(
                f"  [step {obs.step}, observed {obs.at}] "
                f"{obs.tool}({args}) -> {json.dumps(obs.value)}"
            )
        return "\n".join(lines)

    

    # --- persistence --------------------------------------------------

    FORMAT_VERSION = 1

    def save(self, path: str | Path) -> None:
        """Write memory to disk as JSON.

        This is the point of the whole module. A Memory that dies with the
        process is just a variable; one that survives is the difference
        between an agent that re-derives the same four facts every run and
        one that knows what it learned yesterday.

        The file carries a FORMAT_VERSION for a reason that costs nothing now
        and saves an afternoon later: this file will outlive the code that
        wrote it. Add a field to Observation in Lab 9 and an unversioned
        loader will read a Lab 5 file and either crash somewhere unhelpful or,
        worse, succeed with a field silently missing. A version turns that
        into one loud error at the door.

        saved_at is the file's own timestamp, separate from each
        observation's `at`. It answers "how old is this memory?" without
        having to scan every row.
        """
        payload = {
            "version": self.FORMAT_VERSION,
            "saved_at": now_utc(),
            "observations": [asdict(o) for o in self._observations],
        }
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Memory":
        """Read memory back from disk.

        @classmethod because this is an alternative CONSTRUCTOR: it is called
        on the class, not on an instance - `Memory.load(p)` - and hands back a
        new Memory. `cls` is the class itself, the way `self` is the instance.

        A missing file returns EMPTY memory rather than raising. "I have not
        learned anything yet" is the normal state of a first run, not an
        error, and making the caller wrap every startup in a try/except would
        be punishing them for the ordinary case.

        A version we do not recognise DOES raise. That is the opposite call,
        and deliberately: a missing file means no facts, while an unreadable
        file means facts we cannot vouch for. Guessing at the second one is
        how a stale field becomes a confident wrong answer.
        """
        p = Path(path)
        if not p.exists():
            return cls()

        payload = json.loads(p.read_text(encoding="utf-8"))
        version = payload.get("version")
        if version != cls.FORMAT_VERSION:
            raise ValueError(
                f"unsupported memory format {version!r} "
                f"(this build reads {cls.FORMAT_VERSION})"
            )
        return cls([Observation(**row) for row in payload["observations"]])


# --- smoke test -----------------------------------------------------------
# FIVE properties. Every one of them is a design decision from this lab,
# asserted rather than described - a docstring that says "failures are not
# recorded" is a comment; a test that fails when they are is a guarantee.

if __name__ == "__main__":
    import sys
    import tempfile

    from tools import dispatch

    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        print(f"{'ok  ' if condition else 'FAIL'} : {name}{'  ' + detail if detail else ''}")
        if not condition:
            failures.append(name)

    mem = Memory()

    # 1. Nothing observed yet renders as nothing at all - not an empty
    #    heading. A "KNOWN FACTS:" with blank space under it invites the
    #    model to fill it in, which is the failure this module prevents.
    check("empty memory renders empty", mem.render() == "")

    ok_call = dispatch({"tool": "get_service_status", "arguments": {"name": "Spooler"}})
    mem.record(1, ok_call)

    # 2. A failed call leaves NO trace. Three ways to fail, none recorded:
    #    unknown tool, invalid arguments, and a tool that raised.
    before = len(mem)
    mem.record(2, dispatch({"tool": "no_such_tool", "arguments": {}}))
    mem.record(3, dispatch({"tool": "get_service_status", "arguments": {"bogus": 1}}))
    mem.record(4, dispatch({"tool": "get_service_status", "arguments": {"name": "Telnet"}}))
    check("failures are never recorded", len(mem) == before, f"(still {len(mem)})")

    # 3. Storage keeps history; latest() is a VIEW over it. Ask the same
    #    question again and both answers survive, but only one is current.
    mem.record(5, dispatch({"tool": "get_service_status", "arguments": {"name": "Spooler"}}))
    mem.record(6, dispatch({"tool": "get_service_status", "arguments": {"name": "W32Time"}}))
    check("history kept, view deduplicated",
          len(mem) == 3 and len(mem.latest()) == 2,
          f"(stored {len(mem)}, distinct {len(mem.latest())})")

    # 4. Every rendered line carries its provenance. Without the call
    #    attached, a remembered fact is indistinguishable from a guess.
    text = mem.render()
    check("render states provenance",
          "get_service_status(name='Spooler')" in text and "observed 20" in text)

    # 5. The round trip. Save, load, and the observations must be IDENTICAL -
    #    frozen dataclasses compare by value, so == is a real check here.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "memory.json"
        mem.save(path)
        back = Memory.load(path)
        check("round trip preserves observations",
              back.latest() == mem.latest() and len(back) == len(mem))

        # A missing file is not an error: a first run has learned nothing.
        check("missing file loads empty", len(Memory.load(Path(tmp) / "nope.json")) == 0)

        # An unreadable file IS an error. No facts is fine; facts we cannot
        # vouch for is not.
        bad = Path(tmp) / "bad.json"
        bad.write_text(json.dumps({"version": 99, "observations": []}), encoding="utf-8")
        try:
            Memory.load(bad)
            check("unknown format refused", False, "(it loaded anyway)")
        except ValueError as exc:
            check("unknown format refused", True, f"({exc})")

    if failures:
        print(f"\nFAIL: {', '.join(failures)}")
        sys.exit(1)
    print("\nPASS: it remembers only what it saw, and it can prove where it saw it")
    sys.exit(0)
