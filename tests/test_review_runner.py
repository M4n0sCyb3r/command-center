"""P03 build step 4: the review runner, proven with a SCRIPTED model.

  case 1  the prompt: the committed rubric is the system message; the script arrives with
          line numbers the model can cite, and they match the file
  case 2  same prompt, schema and sampling for every arm - only the model name differs
  case 3  a valid review is scored by the step-3 catch rule (a hit on the planted lines)
  case 4  failures are rows, never catches: bad JSON, a verdict that contradicts its
          findings, a model error - each an `error` row, not caught, not a false alarm
  case 5  held-out is refused without --final; development is the default
  case 6  the default generator unloads the model before EVERY call (fresh load, ADR 0025)

Run:  python tests/test_review_runner.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import model_client                                   # noqa: E402
import review as R                                    # noqa: E402
import review_runner as RR                            # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def reply(text: str) -> model_client.Reply:
    return model_client.Reply(text=text, thinking="", model="scripted", prompt_tokens=100,
                              completion_tokens=20, duration_s=0.5, raw={})


items = R.load_set()
dev = [i for i in items if i.split == "development"]
d = next(i for i in dev if i.id == "01-G")          # inverted exclusion check
ctrl = next(i for i in dev if i.id == "01-C")

print("case 1  the prompt")
msgs = RR.build_messages(d)
check("system message is the committed rubric", msgs[0]["content"].startswith(RR.RUBRIC.read_text(encoding="utf-8")))
lines = d.text().rstrip("\n").split("\n")
a = d.defect["lines"][0][0]
check("the planted line arrives numbered as itself", f"{a:>3}| {lines[a - 1]}" in msgs[1]["content"])

print("case 2  every arm sees the same thing")
seen: list[tuple] = []
def spy(m, schema, model):
    seen.append((json.dumps(m), json.dumps(schema), model))
    return reply('{"verdict": "approve", "findings": []}')
for arm in RR.ARMS:
    RR.review_item(d, RR.ARMS[arm], spy)
check("same messages and schema, different model",
      seen[0][:2] == seen[1][:2] and seen[0][2] != seen[1][2], str([s[2] for s in seen]))

print("case 3  a valid review is scored by the catch rule")
hit = json.dumps({"verdict": "revise", "findings": [{"line_start": a, "line_end": a, "category": "correctness",
                  "severity": "blocking", "why": "inverted", "fix": "-contains"}]})
row = RR.review_item(d, "m", lambda m, s, mo: reply(hit))
check("caught, right reason, tokens recorded", row["caught"] and row["right_reason"] and row["prompt_tokens"] == 100)

print("case 4  failures are rows, never catches")
for label, gen in [("bad JSON", lambda m, s, mo: reply("not json")),
                   ("verdict contradicts findings", lambda m, s, mo: reply('{"verdict": "revise", "findings": []}')),
                   ("model error", lambda m, s, mo: (_ for _ in ()).throw(model_client.ModelError("down")))]:
    r = RR.review_item(d, "m", gen)
    c = RR.review_item(ctrl, "m", gen)
    check(f"{label}: error row, not caught; control not a false alarm",
          "error" in r and r["caught"] is False and "error" in c and c["false_alarm"] is False)

print("case 5  the split")
try:
    RR.select("held_out", final=False)
    check("held-out refused without --final", False)
except SystemExit:
    check("held-out refused without --final", True)
check("development selected by default: 32 items", len(RR.select("development", False)) == 32)

print("case 6  fresh load before every call")
calls: list[str] = []
saved = (model_client.unload, model_client.chat)
model_client.unload = lambda model=None: calls.append(f"unload {model}")
model_client.chat = lambda messages, **kw: (calls.append(f"chat {kw.get('model')} {kw.get('max_tokens')} {kw.get('timeout')}"), reply('{"verdict":"approve","findings":[]}'))[1]
try:
    for it in (d, ctrl):
        RR.review_item(it, "hermes3:8b")
finally:
    model_client.unload, model_client.chat = saved
every = [f"unload {m}" for m in dict.fromkeys(RR.ARMS.values())]
check("every P03 model is unloaded before each chat (alone), with the review budget (6144, 300 s)",
      calls == (every + [f"chat hermes3:8b {RR.REVIEW_MAX_TOKENS} {RR.REVIEW_TIMEOUT}"]) * 2 and len(every) == 3
      and (RR.REVIEW_MAX_TOKENS, RR.REVIEW_TIMEOUT) == (6144, 300), str(calls))

if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
