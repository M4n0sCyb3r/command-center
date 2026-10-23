"""Agent-loop stall, call A: a runaway generation is BOUNDED and NAMED.

The 2026-09-27 stall was qwen3 generating 15,637 tokens at temperature 0 until
our 120 s timeout. Call A caps one call at MAX_TOKENS and makes the cut-off a
ModelError, so it takes call B's stall path (record kept, case + turn named,
exit 2) instead of arriving as half a JSON object that fails as SchemaError.

  case 1  every chat() request carries num_predict = MAX_TOKENS (2,048)
  case 2  done_reason "length" -> ModelError naming the cap
  case 3  POSITIVE CONTROL: done_reason "stop" with the same text -> a Reply.
          A chat() that raised on everything would pass case 2 clean.
  case 4  the cap sits under the context window, so context shift is unreachable
  case 6  a rejection names the server's own reason, never an invented one (amendment 14)
  case 5  end to end: a capped turn leaves agent.run() as ModelError WITH its turn,
          so the harness records it (the path tests/test_stall.py covers)

No model: requests.post inside model_client is replaced by a script.
Run:  python tests/test_generation_cap.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import model_client                                   # noqa: E402
from model_client import MAX_TOKENS, ModelError       # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class FakeResponse:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


sent: list[dict] = []


def script(done_reasons: list[str], text: str):
    queue = list(done_reasons)

    def post(url, json=None, timeout=None):
        sent.append(json)
        return FakeResponse({"model": "scripted", "done_reason": queue.pop(0),
                             "message": {"content": text}, "eval_count": 1})
    model_client.requests.post = post


model_client.base_url = lambda: "http://scripted:11434"
STEP = json.dumps({"action": "call_tool", "tool": "list_services", "arguments": {}})

print("generation cap - agent-loop stall, call A\n")

print("case 1  every request carries the cap")
script(["stop"], STEP)
model_client.chat([{"role": "user", "content": "hi"}])
check("num_predict == MAX_TOKENS == 2048",
      sent[-1]["options"].get("num_predict") == MAX_TOKENS == 2048, repr(sent[-1]["options"]))

print("\ncase 2  done_reason 'length' is a ModelError that names the cap")
script(["length"], '{"action": "call_to')           # half a JSON object, as Ollama returns it
try:
    model_client.chat([{"role": "user", "content": "hi"}], schema={"type": "object"})
    err = None
except ModelError as exc:
    err = exc
except Exception as exc:                            # noqa: BLE001
    err = exc
check("raised ModelError (not SchemaError, not a Reply)", isinstance(err, ModelError), repr(err))
check("the message names the cap", err is not None and "2048-token generation cap" in str(err))

print("\ncase 3  positive control - done_reason 'stop' returns a Reply")
script(["stop"], STEP)
reply = model_client.chat([{"role": "user", "content": "hi"}])
check("a Reply with the text", reply.text == STEP)

print("\ncase 4  the cap is under the server's context window")
print("  n_ctx_slot = 4096 in Ollama's log; the longest turn-prompt observed is ~614 tokens.")
check("MAX_TOKENS + 614 < 4096 - context shift cannot be reached", MAX_TOKENS + 614 < 4096)

print("\ncase 5  end to end - a capped turn leaves agent.run() with its turn")
import agent                                          # noqa: E402
agent.chat = model_client.chat                        # the real boundary, scripted wire
script(["stop", "stop", "length"], STEP)              # turns 1-2 answer, turn 3 runs away
try:
    agent.run("Are any services down right now?")
    exc5 = None
except ModelError as exc:
    exc5 = exc
check("ModelError with turn 3", exc5 is not None and exc5.turn == 3,
      repr(getattr(exc5, "turn", None)))

print("\ncase 6  a rejection carries the server's own reason (ADR 0026 amendment 14)")


class Rejected:
    """A 500 the way requests delivers it: raise_for_status raises, the body survives."""
    def __init__(self, body, text=""):
        self._b, self.text, self.status_code = body, text, 500
    def json(self):
        if self._b is None:
            raise ValueError("not json")
        return self._b
    def raise_for_status(self):
        raise model_client.requests.exceptions.HTTPError("500 Server Error: Internal Server Error", response=self)


def rejected_with(resp):
    model_client.requests.post = lambda url, json=None, timeout=None: resp
    try:
        model_client.chat([{"role": "user", "content": "hi"}])
    except ModelError as exc:
        return str(exc)
    return "NO ERROR"


msg = rejected_with(Rejected({"error": "failed to sample token"}))
check("Ollama's {\"error\": ...} body is named in the ModelError",
      "500 Server Error" in msg and "server says: failed to sample token" in msg, msg)
msg = rejected_with(Rejected(None, text="upstream exploded"))
check("a non-JSON body is passed through as text", "server says: upstream exploded" in msg, msg)
msg = rejected_with(Rejected({}))
check("an empty body adds nothing - no reason is invented", "server says" not in msg, msg)

print()
if failures:
    print(f"FAIL: {len(failures)} check(s) failed")
    sys.exit(1)
print("PASS: a runaway is cut off at the cap and arrives as a named model failure")
