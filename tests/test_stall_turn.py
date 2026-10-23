"""Agent-loop stall, call B, build step 3: the stall's TURN reaches the record.

chat() cannot know which turn it is serving; agent.run() can. So run() stamps
`turn` on a ModelError on its way out, and the harness (step 2) copies it into
the record's `incomplete` marker.

  case 1  a timeout on turn 3 leaves agent.run() with exc.turn == 3
  case 2  POSITIVE CONTROL: a timeout on turn 1 says 1. A run() that stamped a
          constant would pass case 1 if the constant happened to be 3.
  case 3  it is still RE-RAISED - the loop does not swallow a stall
  case 4  the trace still records the failed span, with its turn and the error
          (Lab 12: span() emits in a finally)
  case 5  the attribute is declared on the class, so an unstamped ModelError
          reads None rather than raising AttributeError

No model: agent.chat is replaced by a script that answers N turns, then times out.
Run:  python tests/test_stall_turn.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import agent                                        # noqa: E402
from model_client import ModelError, Reply          # noqa: E402
from tracing import Tracer, collect                 # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def scripted_chat(answer_turns: int):
    """Answer `answer_turns` turns with a tool call, then time out."""
    calls = {"n": 0}

    def chat(messages, **kwargs):
        calls["n"] += 1
        if calls["n"] > answer_turns:
            raise ModelError("the model did not answer within 120s")
        step = {"action": "call_tool", "tool": "list_services", "arguments": {}}
        return Reply(text=json.dumps(step), thinking="", model="scripted",
                     prompt_tokens=1, completion_tokens=1, duration_s=0.0, raw={})
    return chat


def stall_turn(answer_turns: int, events: list | None = None):
    agent.chat = scripted_chat(answer_turns)
    tracer = Tracer(collect(events)) if events is not None else None
    try:
        agent.run("Are any services down right now?", tracer=tracer)
    except ModelError as exc:
        return exc
    return None


print("stall turn - agent-loop stall, call B, build step 3\n")

print("case 1  a timeout on turn 3")
exc = stall_turn(2)
check("agent.run() raised ModelError", isinstance(exc, ModelError))
check("exc.turn == 3", getattr(exc, "turn", None) == 3, repr(getattr(exc, "turn", None)))

print("\ncase 2  positive control - a timeout on turn 1 says 1")
exc = stall_turn(0)
check("exc.turn == 1", getattr(exc, "turn", None) == 1, repr(getattr(exc, "turn", None)))

print("\ncase 3  the stall is re-raised, not swallowed into an AgentResult")
agent.chat = scripted_chat(0)
try:
    result = agent.run("Is the print spooler running?")
    raised = False
except ModelError:
    raised = True
check("run() raised rather than returning", raised)

print("\ncase 4  the trace still records the failed span")
events: list = []
stall_turn(2, events)
spans = [e for e in events if e.kind == "model_call"]
check("3 model_call spans: 2 answered, 1 failed", len(spans) == 3, str(len(spans)))
last = spans[-1].data if spans else {}
check("the failed span carries turn 3 and the error",
      last.get("turn") == 3 and "120s" in str(last.get("error")), repr(last)[:120])

print("\ncase 5  the attribute is declared, so an unstamped error reads None")
check("ModelError('x').turn is None", ModelError("x").turn is None)

print()
if failures:
    print(f"FAIL: {len(failures)} check(s) failed")
    sys.exit(1)
print("PASS: a stall leaves the loop carrying the turn it happened on")
