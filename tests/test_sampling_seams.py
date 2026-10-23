"""ADR 0009's measurement seams in model_client: inert by default, exact when set.

  case 1  THE DEFAULT IS ADR 0009's: temperature 0.6, top_k 20, top_p 0.95,
          repeat_penalty 1.0, seed 42, num_predict 2048 (amended 2026-09-27;
          until then it was {temperature 0.0, seed 42, num_predict 2048})
  case 2  each probe arm still sends EXACTLY the request it was measured with,
          although the default moved under it: S0 = the old greedy request,
          S1 = nothing sampling-related at all, S2 = S0 + presence_penalty
  case 4  an observer sees a normal call, with tokens and done_reason
  case 5  THE POINT: a call cut off at the cap is OBSERVED and THEN raises -
          the runaway is counted, not lost inside the exception
  case 6  the output hash is the control for rule 1: same output -> same
          hash, different output -> different hash
  case 7  POSITIVE CONTROL: clearing the override restores case 1 exactly

No model: requests.post inside model_client is replaced by a script.
Run:  python tests/test_sampling_seams.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import model_client                                   # noqa: E402
from model_client import ModelError                   # noqa: E402

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
replies: list[dict] = []


def post(url, json=None, timeout=None):
    sent.append(json)
    return FakeResponse(replies.pop(0))


model_client.requests.post = post
model_client.base_url = lambda: "http://scripted:11434"
MSG = [{"role": "user", "content": "hi"}]


def reply(content="ok", done="stop", tokens=7):
    return {"model": "scripted", "done_reason": done, "eval_count": tokens,
            "prompt_eval_count": 3, "message": {"content": content}}


def call(**kw):
    replies.append(reply(**kw))
    return model_client.chat(MSG)


DEFAULT = {"temperature": 0.6, "top_k": 20, "top_p": 0.95, "repeat_penalty": 1.0,
           "seed": 42, "num_predict": 2048}
GREEDY = {"temperature": 0.0, "seed": 42, "num_predict": 2048}      # pre-ADR-0009

print("case 1  no override -> ADR 0009's pinned sampling")
call()
check("options == ADR 0009's six values", sent[-1]["options"] == DEFAULT, str(sent[-1]["options"]))

print("\ncase 2  each probe arm still sends exactly what it was measured with")
import sampling_probe as sp                            # noqa: E402
for arm, want in (("S0", GREEDY), ("S1", {"seed": 42, "num_predict": 2048}),
                  ("S2", {**GREEDY, "presence_penalty": 1.5}), ("S1p", DEFAULT)):
    model_client.SAMPLING_OVERRIDE = sp.ARMS[arm]
    call()
    check(f"{arm} sends {sorted(want)}", sent[-1]["options"] == want, str(sent[-1]["options"]))

print("\ncase 4  an observer sees a normal call")
model_client.SAMPLING_OVERRIDE = None
seen: list[dict] = []
model_client.CALL_OBSERVERS.append(seen.append)
call(tokens=412)
check("one record", len(seen) == 1)
check("tokens and done_reason recorded",
      seen[-1]["completion_tokens"] == 412 and seen[-1]["done_reason"] == "stop", str(seen[-1]))
check("the options actually sent are recorded", seen[-1]["options"] == DEFAULT)

print("\ncase 5  THE POINT: a capped call is observed, THEN raises")
try:
    call(content='{"half": ', done="length", tokens=2048)
    check("the capped call raised ModelError", False, "it returned")
except ModelError:
    check("the capped call raised ModelError", True)
check("and it was observed first - counted, not lost",
      len(seen) == 2 and seen[-1]["done_reason"] == "length"
      and seen[-1]["completion_tokens"] == 2048, str(seen[-1]))

print("\ncase 6  the output hash - rule 1's control")
call(content="same"); call(content="same"); call(content="different")
h = [r["output_sha256"] for r in seen[-3:]]
check("same output -> same hash", h[0] == h[1])
check("different output -> different hash", h[1] != h[2])

print("\ncase 7  POSITIVE CONTROL - clearing the override restores case 1")
model_client.SAMPLING_OVERRIDE = {"temperature": None}
call()
check("override in force (sanity)", "temperature" not in sent[-1]["options"])
model_client.SAMPLING_OVERRIDE = None
model_client.CALL_OBSERVERS.clear()
n = len(seen)
call()
check("options back to exactly the default", sent[-1]["options"] == DEFAULT)
check("no observer -> nothing recorded", len(seen) == n)

print()
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("PASS: the seams change nothing until a probe sets them, and count a runaway when it happens")
