"""P02 build step 6a: the answer step's code, proven without a model (ADR 0025).

Every reply here is SCRIPTED, so each failure mode happens on cue instead of
whenever qwen3 happens to slip. That is why this lives in the CI home: the
citation check and the retry are code, and code that only runs when the model
misbehaves is code that is never tested.

  case 1  a valid answer: answered, one call, cited ids kept
  case 2  an invented id, then a valid reply: answered on attempt 2; the retry
          names the bad id and lists the valid ones
  case 3  invented twice: refused-citation after EXACTLY two calls
  case 4  answered with nothing cited: a citation failure, not an answer
  case 5  not_in_sources, cites nothing: an honest refusal, one call
  case 6  not_in_sources but cites something: inconsistent -> retried
  case 7  empty retrieval: refused before the model is ever called
  case 8  the decoder schema enumerates the retrieved ids; the LOCAL shape
          does not - so an invented id is a CitationError, not a SchemaError
  case 9  garbage JSON: SchemaError propagates, NOT retried
  case 10 an over-budget prompt is refused BEFORE sending (ADR 0025 part 5)
  case 11 chat(): num_ctx absent by default (request unchanged), sent when asked
  case 12 the server-side backstop: prompt tokens + generation > num_ctx raises

Run:  python tests/test_answer_step.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import answer as A                                   # noqa: E402
import model_client                                  # noqa: E402
from retrieval import Chunk, Hit                     # noqa: E402
from structured import SchemaError                   # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def hit(cid: str, score: float = 0.7, text: str = "Restart the spooler.") -> Hit:
    path, _, slug = cid.partition("#")
    return Hit(Chunk(cid, path, "Service restart", slug, text, "ours"), score)


HITS = [hit("data/runbooks/service-restart.md#symptoms", 0.71),
        hit("data/runbooks/service-restart.md#procedure", 0.66),
        hit("data/microsoft/sc-query.md#overview", 0.60)]
IDS = [h.chunk.chunk_id for h in HITS]


class Script:
    """A fake model: returns the scripted replies in order, records every call."""

    def __init__(self, *replies: dict | str):
        self.replies = [r if isinstance(r, str) else json.dumps(r) for r in replies]
        self.calls: list[tuple[list[dict], dict]] = []

    def __call__(self, messages, schema):
        self.calls.append((messages, schema))
        if len(self.calls) > len(self.replies):
            raise AssertionError(f"model called {len(self.calls)} times, script has {len(self.replies)}")
        return self.replies[len(self.calls) - 1]


def reply(status: str = "answered", cited: list[str] | None = None, text: str = "Restart it.") -> dict:
    return {"status": status, "cited": IDS[:1] if cited is None else cited, "answer": text}


print("case 1  a valid answer")
s = Script(reply())
a = A.answer("q", hits=HITS, generate=s)
check("answered", a.outcome == A.ANSWERED, a.outcome)
check("one call", a.attempts == 1 and len(s.calls) == 1)
check("cited kept", a.cited == IDS[:1])
check("retrieved recorded in rank order with scores", a.retrieved == [(i, h.score) for i, h in zip(IDS, HITS)])

print("case 2  invented id, then valid")
bad = "data/runbooks/service-restrat.md#procedure"
s = Script(reply(cited=[bad]), reply())
a = A.answer("q", hits=HITS, generate=s)
check("answered on attempt 2", a.outcome == A.ANSWERED and a.attempts == 2, f"{a.outcome} {a.attempts}")
check("the failure is counted", len(a.errors) == 1 and bad in a.errors[0])
retry = s.calls[1][0][-1]["content"]
check("retry names the bad id", bad in retry)
check("retry lists every valid id", all(i in retry for i in IDS))
check("retry carries the first reply as the assistant turn", s.calls[1][0][-2]["role"] == "assistant")

print("case 3  invented twice")
s = Script(reply(cited=[bad]), reply(cited=[bad]))
a = A.answer("q", hits=HITS, generate=s)
check("refused-citation", a.outcome == A.REFUSED_CITATION, a.outcome)
check("exactly two calls, never a loop", len(s.calls) == 2 and a.attempts == 2)
check("no answer text leaks out of a refusal", a.text == "" and a.cited == [])
check("both failures counted", len(a.errors) == 2)

print("case 4  answered, nothing cited")
s = Script(reply(cited=[]), reply(cited=[]))
a = A.answer("q", hits=HITS, generate=s)
check("refused-citation", a.outcome == A.REFUSED_CITATION, a.outcome)
check("the error says why", "cited is empty" in a.errors[0])

print("case 5  honest refusal")
s = Script(reply("not_in_sources", [], "The runbook does not cover startup type."))
a = A.answer("q", hits=HITS, generate=s)
check("refused-not-in-sources", a.outcome == A.REFUSED_NOT_IN_SOURCES, a.outcome)
check("one call, no errors", a.attempts == 1 and a.errors == [])
check("distinct from a citation refusal", A.REFUSED_NOT_IN_SOURCES != A.REFUSED_CITATION)

print("case 6  refusal that cites something")
s = Script(reply("not_in_sources", IDS[:1]), reply("not_in_sources", []))
a = A.answer("q", hits=HITS, generate=s)
check("retried, then the refusal stands", a.outcome == A.REFUSED_NOT_IN_SOURCES and a.attempts == 2)
check("the inconsistency is counted", "a refusal cites nothing" in a.errors[0])

print("case 7  empty retrieval")
s = Script()
a = A.answer("q", hits=[], generate=s)
check("refused-no-retrieval", a.outcome == A.REFUSED_NO_RETRIEVAL, a.outcome)
check("model never called", s.calls == [] and a.attempts == 0)

print("case 8  decoder schema vs local shape")
s = Script(reply())
A.answer("q", hits=HITS, generate=s)
sent = s.calls[0][1]
check("decoder may only cite retrieved ids, in rank order", sent["properties"]["cited"]["items"].get("enum") == IDS)
check("local shape has no enum", "enum" not in A.SHAPE["properties"]["cited"]["items"])
check("key order: status, cited, answer", list(sent["properties"]) == ["status", "cited", "answer"])
check("SHAPE was not mutated by wire_schema", A.SHAPE["properties"]["cited"]["items"] == {"type": "string"})
check("prompt labels every source with its id", all(f"[{i}]" in s.calls[0][0][1]["content"] for i in IDS))

print("case 9  garbage is not retried")
s = Script("this is not json", reply())
try:
    A.answer("q", hits=HITS, generate=s)
    check("SchemaError raised", False, "returned instead")
except SchemaError:
    check("SchemaError raised", True)
check("one call only", len(s.calls) == 1)

print("case 10 the budget refuses before sending")
huge = [hit(IDS[0], text="x" * 40_000)]
s = Script(reply())
try:
    A.answer("q", hits=huge, generate=s)
    check("ContextBudgetError raised", False, "returned instead")
except A.ContextBudgetError as exc:
    check("ContextBudgetError raised", True, str(exc)[:60])
check("nothing was sent", s.calls == [])
worst = [hit(i, text="x" * 7_500) for i in IDS]      # today's largest sections, three of them
s = Script(reply())
a = A.answer("q", hits=worst, generate=s)
check("today's worst case fits 16,384 (the check is a backstop)", a.outcome == A.ANSWERED)


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload


sent_payloads: list[dict] = []
prompt_tokens = {"n": 900}


def fake_post(url, json=None, timeout=None):
    sent_payloads.append(json)
    return FakeResponse({"model": "qwen3:8b", "done_reason": "stop",
                         "prompt_eval_count": prompt_tokens["n"], "eval_count": 40,
                         "message": {"content": __import__("json").dumps(reply())}})


model_client.requests.post = fake_post
model_client.base_url = lambda: "http://fake:11434"

print("case 11 chat() and num_ctx")
model_client.chat([{"role": "user", "content": "hi"}])
check("absent by default - the measured request is unchanged", "num_ctx" not in sent_payloads[-1]["options"])
A.answer("q", hits=HITS)                              # the real default_generate path
check("the answer step sends num_ctx 16384", sent_payloads[-1]["options"].get("num_ctx") == A.ANSWER_NUM_CTX)
check("and the enum schema as format", sent_payloads[-1]["format"]["properties"]["cited"]["items"].get("enum") == IDS)

print("case 12 the server-side backstop")
prompt_tokens["n"] = A.ANSWER_NUM_CTX - model_client.MAX_TOKENS + 1
try:
    A.answer("q", hits=HITS)
    check("ContextBudgetError raised", False, "returned instead")
except A.ContextBudgetError:
    check("ContextBudgetError raised", True)

print()
if failures:
    print(f"FAIL: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PASS: answer step - outcomes, citation check, one retry, budget")
