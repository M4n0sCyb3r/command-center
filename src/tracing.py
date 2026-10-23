"""The run record: one event stream, one clock, one id.

Eleven labs have produced three separate views of a run and no way to line
them up. `AgentResult.steps` says what the loop did. `Memory` says what is
true. `Guard.log` says who said yes. Three lists, no shared key, no
timestamps - and every one of them lives in memory and dies with the
process. Ask "why was that run slow" or "what did it cost" and nothing in
this repo can answer, because nothing wrote it down.

That is the difference between LOGGING and OBSERVABILITY. A log is a
sequence of things your code chose to say. A trace is a record of what the
RUN did, correlated by an id, ordered by a sequence number, stamped with
two clocks, and durable enough to read tomorrow. Labs 8-11 measured this
agent from the outside, by hand, once. This module makes the measurement
come for free on every run, including the ones nobody is watching.

Why `tracing.py` and not `trace.py`: `trace` is a standard-library module,
and `src` is not a package - running `python src/agent.py` puts `src` FIRST
on `sys.path`, so a file named `trace.py` would shadow the stdlib for every
import in the process, including ones we do not make ourselves. Shadowing a
stdlib name is a bug that shows up as somebody else's library breaking.

    python src/tracing.py; echo "exit: $?"
"""

from __future__ import annotations

import json
import random
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

# The kinds of thing worth recording about a run. A closed vocabulary, like
# Lab 11's RISK_LEVELS: a trace whose event names are free text cannot be
# queried, because "tool_call", "toolcall" and "tool call" are three
# different facts to anything that reads it later.
KINDS = (
    "run_start",        # question in, policy in force
    "model_call",       # one model turn: duration, tokens, what it decided
    "guard_decision",   # allowed or refused, and why
    "tool_call",        # a tool actually ran, and what came back
    "run_end",          # how it ended, and the tracer's own health
)

# How much of any one string the trace keeps. A trace is read at 2am by
# somebody who needs the shape of the run, not a second copy of the
# transcript - and an event stream that grows with the payload stops being
# cheap enough to leave switched on.
LIMIT = 200


def new_run_id() -> str:
    """A run id that sorts by time.

    The first thing anyone does with a directory of runs is find the newest
    one, and the tool they will have is `ls`. A bare uuid4 sorts randomly,
    so the timestamp goes in front and the random half only breaks ties
    between runs started in the same second.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def _safe(value: Any, limit: int = LIMIT) -> Any:
    """Make a value safe to write to a trace, and say so when it is cut.

    Two jobs. Anything that is not JSON becomes its `repr`, because an event
    stream that cannot be serialised is an event stream that is lost at the
    worst moment. And long strings are TRUNCATED VISIBLY: a fragment with no
    mark is read by a future human as the whole value, and they will debug
    the wrong thing for an hour. If you cut it, say you cut it.
    """
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return f"{value[:limit]}...(+{len(value) - limit} chars)"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _safe(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v, limit) for v in value]
    return _safe(repr(value), limit)


@dataclass(frozen=True)
class Event:
    """One recorded fact about a run. Frozen: a trace you can edit is not evidence.

    TWO CLOCKS, and this is the line worth remembering. `ts` is wall-clock
    UTC, and it exists only to line this run up against other systems' logs -
    the Windows event log, Ollama's own output, a ticket. `elapsed_s` comes
    from `time.monotonic()` and is the only one you may do ARITHMETIC with,
    because the wall clock can be stepped backwards by NTP mid-run and turn
    a six-second call into a negative duration. Correlate with one, measure
    with the other, and never subtract two wall clocks.
    """

    run_id: str
    seq: int                  # 1-based, per run: ordering that survives equal timestamps
    ts: str                   # wall clock, ISO-8601 UTC - for CORRELATION
    elapsed_s: float          # monotonic, since run start - for ARITHMETIC
    kind: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Flat JSON object: one event, one line, no nesting to unwrap."""
        return {"run_id": self.run_id, "seq": self.seq, "ts": self.ts,
                "elapsed_s": self.elapsed_s, "kind": self.kind, **self.data}


# --- sinks ----------------------------------------------------------------
# A sink is where events go. It is the FIFTH seam in this course, after
# Lab 7's reviewer=, Lab 8's runner=, Lab 10's judge= and Lab 11's approver=,
# and it is here for the same reason as all four: a list sink makes the
# tracer testable without a disk, and the tests below prove properties of
# the tracer rather than of the filesystem.


def collect(events: list[Event]) -> Callable[[Event], None]:
    """A sink that appends to a list. For tests, and for reading a run live."""
    return events.append


def jsonl_sink(path: str | Path) -> Callable[[Event], None]:
    """A sink that appends one JSON object per line to a file.

    JSONL and not JSON, because a run that crashes halfway leaves a valid
    JSONL file with fewer lines and an invalid JSON file with no closing
    bracket. The format has to survive the failure it exists to explain.

    Opened per event rather than held open: a handle held for the length of a
    run is a handle lost when the process dies, and this costs nothing at
    agent speeds.
    """
    target = Path(path)

    def write(event: Event) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.as_dict(), default=str) + "\n")

    return write


class Tracer:
    """Records what a run did. One Tracer per run, like one Guard per run.

    THE RULE THIS CLASS IS BUILT AROUND: instrumentation may not fail the
    thing it instruments. A disk fills, a directory is read-only, a value
    will not serialise - none of that is a reason for an agent run to die,
    because the observer is not the work. So `emit()` catches everything the
    sink can throw and carries on.

    And then the correction that keeps that from being a lie: a dropped
    event is COUNTED. Silent swallowing makes "the tracer broke" and
    "nothing happened" look identical, which is the same defect Lab 11
    refused in the dry run - never hand a reader a plausible fake. The count
    rides out in the `run_end` event, so a trace reports its own health.
    """

    def __init__(self, sink: Callable[[Event], None] | None = None, *,
                 run_id: str | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sample: float = 1.0) -> None:
        self.run_id = run_id or new_run_id()
        self.sink = sink if sink is not None else (lambda event: None)
        self.clock = clock
        # Sampling belongs in the tracer, not in the caller. The moment the
        # decision "is this run traced" lives at the call sites, half of them
        # forget and a run is recorded in pieces. 1.0 = record everything,
        # which is right for a lab and wrong for a service.
        self.sample = sample
        self.enabled = sample >= 1.0 or random.random() < sample
        self.started = self.clock()
        self.seq = 0
        self.dropped = 0

    def emit(self, kind: str, **data: Any) -> Event | None:
        """Record one event. Never raises. Returns the Event, or None if dropped.

        `kind` is checked against KINDS and a bad one is recorded as
        `kind="unknown"` with the name kept in the data - a typo in a call
        site should show up in the trace as a typo, not as an exception in
        production and not as a silently accepted new event type.
        """
        if not self.enabled:
            return None
        try:
            if kind not in KINDS:
                data = {"requested_kind": kind, **data}
                kind = "unknown"
            self.seq += 1
            event = Event(
                run_id=self.run_id,
                seq=self.seq,
                ts=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                elapsed_s=round(self.clock() - self.started, 4),
                kind=kind,
                data={k: _safe(v) for k, v in data.items()},
            )
            self.sink(event)
            return event
        except Exception:       # noqa: BLE001 - deliberate: see the docstring
            self.dropped += 1
            return None

    @contextmanager
    def span(self, kind: str, **data: Any) -> Iterator[dict[str, Any]]:
        """Time a block and emit ONE event for it when it ends.

        Yields a dict the body fills in with what it learned - the caller
        cannot know the token count before the call returns, so the event
        cannot be built before it either.

        The event is emitted in a `finally`, which is the whole point: the
        span you most need is the one wrapped around the call that blew up.
        The exception is re-raised untouched. A tracer OBSERVES failure; it
        does not handle it, because a tracer that swallows an exception has
        quietly become error handling that nobody knows is there.
        """
        started = self.clock()
        extra: dict[str, Any] = {}
        try:
            yield extra
        except Exception as exc:
            extra.setdefault("error", f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self.emit(kind, duration_s=round(self.clock() - started, 4),
                      **{**data, **extra})


# --- reading a trace back -------------------------------------------------


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Load a written trace. Blank lines skipped; a torn last line is dropped.

    A process that died mid-write leaves a partial final line, and refusing
    to read the other 200 events because of it would be the tool failing at
    exactly the job it was built for.
    """
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def summarize(events: Any) -> dict[str, Any]:
    """Derive the run-level numbers from the event stream.

    Takes Events OR the plain dicts read back off disk, and that is the
    design point rather than a convenience. The events are FACTS and this is
    a DERIVATION, so it must work on a file written last week by a process
    that no longer exists - if summarising required the live objects, the
    trace would only be readable by the run that produced it, which is the
    one situation where you did not need it.

    Nothing here is stored at emit time. A total written into the stream is a
    number that can disagree with the events beside it; a number computed
    from them cannot.
    """
    rows = [e.as_dict() if isinstance(e, Event) else dict(e) for e in events]
    by_kind: dict[str, list[dict[str, Any]]] = {k: [] for k in KINDS}
    for row in rows:
        by_kind.setdefault(row.get("kind", "unknown"), []).append(row)

    start = (by_kind.get("run_start") or [{}])[0]
    end = (by_kind.get("run_end") or [{}])[-1]
    calls = by_kind.get("model_call", [])
    tools = by_kind.get("tool_call", [])
    gates = by_kind.get("guard_decision", [])

    model_s = round(sum(c.get("model_s") or 0 for c in calls), 3)
    waited_s = round(sum(c.get("duration_s") or 0 for c in calls), 3)
    wall_s = round(max((r.get("elapsed_s") or 0 for r in rows), default=0), 3)

    return {
        "run_id": rows[0]["run_id"] if rows else None,
        "question": start.get("question"),
        "ceiling": start.get("ceiling"),
        "ok": end.get("ok"),
        "reason": end.get("reason"),
        "steps": end.get("steps"),
        "model_calls": len(calls),
        "tool_calls": len(tools),
        "tools": sorted({t.get("tool") for t in tools if t.get("tool")}),
        "refusals": sum(1 for g in gates if g.get("allowed") is False),
        "failed_tools": sum(1 for t in tools if t.get("ok") is False),
        "tokens_in": sum(c.get("prompt_tokens") or 0 for c in calls),
        "tokens_out": sum(c.get("completion_tokens") or 0 for c in calls),
        "model_s": model_s,
        "waited_s": waited_s,
        "wall_s": wall_s,
        # The number nobody has been able to state about this agent: how much
        # of a run is the model, and how much is us. Below ~0.9 there is real
        # time to go and find; at 0.99 the only way to make the agent faster
        # is to make fewer calls or shorter prompts.
        "model_share": round(model_s / wall_s, 3) if wall_s else None,
        "events": len(rows),
        "dropped": end.get("dropped", 0),
    }


# --- smoke test -----------------------------------------------------------
# Hermetic on purpose: not one line of this needs Ollama. Seven properties,
# every one of them a claim this module makes about itself.

if __name__ == "__main__":
    import sys
    import tempfile

    failures: list[str] = []

    # 1. Identity and order. Every event carries the same run id, seq counts
    #    from 1 with no gaps, and elapsed never goes backwards. Without these
    #    three the stream is a pile, not a trace.
    seen: list[Event] = []
    tracer = Tracer(collect(seen))
    tracer.emit("run_start", question="Is the Spooler running?")
    tracer.emit("model_call", action="call_tool")
    tracer.emit("tool_call", tool="get_service_status", ok=True)
    tracer.emit("run_end", reason="final")
    ids = {e.run_id for e in seen}
    seqs = [e.seq for e in seen]
    elapsed = [e.elapsed_s for e in seen]
    print(f"stream   : {len(seen)} events, run_id(s)={len(ids)}, seq={seqs}")
    if len(ids) != 1 or seqs != [1, 2, 3, 4]:
        print("FAIL: the stream has no single id or no unbroken ordering")
        failures.append("ordering")
    if elapsed != sorted(elapsed) or elapsed[0] < 0:
        print("FAIL: elapsed time is not monotonic")
        failures.append("ordering")

    # 2. Two clocks. ts must parse as an aware UTC timestamp (correlation),
    #    elapsed must be small and non-negative (arithmetic). A run that took
    #    milliseconds cannot have an elapsed measured in wall-clock epoch
    #    seconds - that mistake is exactly what one clock invites.
    stamp = datetime.fromisoformat(seen[0].ts)
    print(f"clocks   : ts={seen[0].ts} tz={stamp.tzinfo}, elapsed={seen[-1].elapsed_s}s")
    if stamp.tzinfo is None or seen[-1].elapsed_s > 5:
        print("FAIL: the two clocks are not what they claim to be")
        failures.append("clocks")

    # 3. THE RULE. A sink that throws on every call must not take the run
    #    with it - and the drops must be counted, or a broken tracer is
    #    indistinguishable from a quiet one.
    def hostile(event: Event) -> None:
        raise OSError("no space left on device")

    broken = Tracer(hostile)
    for _ in range(3):
        broken.emit("model_call", action="final")
    survived = "yes"                      # reached only if emit() never raised
    print(f"failure  : sink raised 3x, dropped={broken.dropped}, run survived: {survived}")
    if broken.dropped != 3:
        print("FAIL: a failing sink either killed the run or was swallowed silently")
        failures.append("never-fail")

    # 4. Spans. One event per span, carrying a duration and whatever the body
    #    learned - and an event even when the body raises, because that is
    #    the span you actually need.
    spans: list[Event] = []
    timed = Tracer(collect(spans))
    with timed.span("model_call", turn=1) as box:
        time.sleep(0.01)
        box["prompt_tokens"] = 900        # not knowable before the call returns
    try:
        with timed.span("tool_call", tool="get_service_status"):
            raise RuntimeError("connection reset")
    except RuntimeError:
        pass
    ok_span, bad_span = spans
    print(f"spans    : ok={ok_span.data.get('duration_s')}s "
          f"tokens={ok_span.data.get('prompt_tokens')}, "
          f"failed={bad_span.data.get('error')!r}")
    if len(spans) != 2 or ok_span.data.get("prompt_tokens") != 900:
        print("FAIL: a span did not carry back what the body learned")
        failures.append("span")
    if ok_span.data.get("duration_s", 0) < 0.01 or "connection reset" not in str(
            bad_span.data.get("error")):
        print("FAIL: a span lost its duration or its exception")
        failures.append("span")

    # 5. Truncation is visible. A cut string must announce the cut; a short
    #    one must be left exactly alone.
    long_answer = "x" * 500
    tracer.emit("run_end", answer=long_answer, reason="final")
    cut = seen[-1].data["answer"]
    print(f"truncate : 500 chars -> {len(cut)} chars, marked: "
          f"{cut.endswith('(+300 chars)')}")
    if not cut.endswith("(+300 chars)") or seen[-1].data["reason"] != "final":
        print("FAIL: truncation is invisible, or it ate a short value")
        failures.append("truncation")

    # 6. Durability. The whole point is that the record outlives the process,
    #    so write it, read it back with a parser that was never told about
    #    our classes, and check it is still a valid stream.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run.jsonl"
        disk = Tracer(jsonl_sink(path), run_id="fixed-id")
        disk.emit("run_start", question="Which services are stopped?")
        disk.emit("run_end", reason="budget", dropped=disk.dropped)
        lines = path.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines]
        print(f"durable  : {len(rows)} line(s) re-read, "
              f"kinds={[r['kind'] for r in rows]}, id={rows[0]['run_id']}")
        if len(rows) != 2 or rows[0]["run_id"] != "fixed-id" or "ts" not in rows[1]:
            print("FAIL: the written trace did not survive a round trip")
            failures.append("durable")

    # 7. The derivation. A synthetic run, written to disk, re-read as plain
    #    dicts by code that knows nothing about our classes, and summarised.
    #    That path is the claim: the numbers come from the RECORD, not from
    #    the objects, so a trace from last week still answers questions.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "synthetic.jsonl"
        fake = Tracer(jsonl_sink(path))
        fake.emit("run_start", question="Which services are stopped?",
                  ceiling="read", tools=["get_service_status", "list_services"])
        fake.emit("model_call", turn=1, action="call_tool", model="qwen3:8b",
                  prompt_tokens=800, completion_tokens=40, model_s=2.0,
                  duration_s=2.1)
        fake.emit("guard_decision", turn=1, tool="list_services", risk="read",
                  allowed=True, reason="permitted")
        fake.emit("tool_call", turn=1, tool="list_services", ok=True)
        fake.emit("model_call", turn=2, action="final", model="qwen3:8b",
                  prompt_tokens=1200, completion_tokens=60, model_s=3.0,
                  duration_s=3.1)
        fake.emit("run_end", ok=True, reason="final", steps=2, dropped=0)

        summary = summarize(read_jsonl(path))
        print(f"summary  : {summary['model_calls']} calls, "
              f"{summary['tokens_in']}/{summary['tokens_out']} tokens, "
              f"model {summary['model_s']}s of {summary['waited_s']}s waited")
        if (summary["tokens_in"] != 2000 or summary["tokens_out"] != 100
                or summary["model_calls"] != 2 or summary["tool_calls"] != 1
                or summary["model_s"] != 5.0 or summary["reason"] != "final"):
            print("FAIL: the summary does not match the events it was derived from")
            failures.append("summary")
        if summarize(seen[:4])["run_id"] != seen[0].run_id:
            print("FAIL: summarize() does not accept live Events")
            failures.append("summary")

    if failures:
        print(f"FAIL: {', '.join(failures)}")
        sys.exit(1)
    print("PASS: one id, one order, two clocks, a record that survives both "
          "the failure it describes and its own")
    sys.exit(0)
