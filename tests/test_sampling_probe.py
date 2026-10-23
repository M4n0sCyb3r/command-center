"""ADR 0009's probe: the rule applied in code does what call 3 says.

  case 1  percentiles are nearest-rank and None on empty (never a fake 0)
  case 2  rule 1: S2 byte-identical to S0 -> VOID (and a one-hash difference -> not void)
  case 3  rule 2: an arm more than 1 pass below S0 is OUT however clean its tail
  case 4  rule 4: S0 with zero tail -> honest null, KEEP S0, rule-of-three bound
  case 5  rule 3: lowest tail RATE wins; a raw count would have picked wrong
  case 6  rule 3 ties: equal rate and cap hits -> S0 keeps it
  case 7  measure() end to end with a scripted server: every call tagged with
          its role and case, an agent cap hit is COUNTED and the probe carries on,
          and the override is cleared afterwards

No model, no network. Run:  python tests/test_sampling_probe.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import sampling_probe as sp                            # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def arm(name, tokens, passes, hashes=None, cap=()):
    """Synthetic arm: `tokens` per call, `passes` = pass count per run (13 cases)."""
    calls = [{"type": "call", "arm": name, "role": "agent" if i % 2 else "judge",
              "completion_tokens": t, "done_reason": "length" if i in cap else "stop",
              "output_sha256": (hashes[i] if hashes else f"{name}-{i}")}
             for i, t in enumerate(tokens)]
    cases = [{"type": "case", "arm": name, "run": r + 1, "case": f"c{k}",
              "result": "pass" if k < p else "fail"}
             for r, p in enumerate(passes) for k in range(13)]
    return {"meta": [{"override": None, "git_head": "x" * 7}], "calls": calls, "cases": cases}


print("case 1  percentiles")
check("p50 of 1..10 is 5", sp.pct(list(range(1, 11)), 50) == 5)
check("p95 of 1..10 is 10", sp.pct(list(range(1, 11)), 95) == 10)
check("empty -> None, not 0", sp.pct([], 50) is None)

print("\ncase 2  rule 1 - the control")
s0 = arm("S0", [100, 1500, 200], [10, 10], hashes=["a", "b", "c"])
same = arm("S2", [100, 1500, 200], [10, 10], hashes=["a", "b", "c"])
out = sp.decide({"S0": s0, "S2": same})
check("identical S2 -> VOID", any("S2 VOID" in l for l in out), "\n".join(out))
check("a void arm cannot win", not any("WINNER S2" in l for l in out))
diff = arm("S2", [100, 300, 200], [10, 10], hashes=["a", "z", "c"])
out = sp.decide({"S0": s0, "S2": diff})
check("one differing output -> NOT void", not any("VOID" in l for l in out))

print("\ncase 3  rule 2 - the guard")
clean_but_worse = arm("S1", [100, 100, 100], [8, 8])        # zero tail, 2 fewer passes
out = sp.decide({"S0": s0, "S1": clean_but_worse})
check("S1 OUT on the guard", any("S1 mean pass 8.00" in l and "OUT" in l for l in out))
check("so S0 wins despite its tail", any("WINNER S0" in l for l in out), out[-1])
exactly_one_below = arm("S1", [100, 100, 100], [9, 9])
out = sp.decide({"S0": s0, "S1": exactly_one_below})
check("exactly 1 below is still in (the bar is MORE than 1)",
      any("S1 mean pass 9.00" in l and "-> in" in l for l in out))

print("\ncase 4  rule 4 - the honest null")
quiet = arm("S0", [100] * 300, [10, 10])
out = sp.decide({"S0": quiet, "S1": arm("S1", [100] * 300, [10, 10])})
check("zero S0 tail -> NULL, keep S0", any("4 NULL" in l for l in out)
      and any("KEEP S0" in l for l in out))
check("the bound is 3/n = 0.0100", any("0.0100" in l for l in out))
check("and no winner is declared", not any("WINNER" in l for l in out))

print("\ncase 5  rule 3 - rate, not count")
# S1 makes 3x the calls (a looping agent) with 2 tail calls: rate 2/30 < S0's 1/3,
# but a raw COUNT (2 > 1) would have called S0 cleaner.
many = arm("S1", [100] * 28 + [1500, 1500], [10, 10])
out = sp.decide({"S0": s0, "S1": many})
check("S1 wins on rate 0.0667 vs 0.3333", any("WINNER S1" in l for l in out), "\n".join(out))

print("\ncase 6  rule 3 - a tie keeps S0")
twin = arm("S1", [100, 1500, 200], [10, 10])
out = sp.decide({"S0": s0, "S1": twin})
check("equal rate, equal cap hits -> S0", any("WINNER S0" in l for l in out))
capped = arm("S1", [100, 1500, 200], [10, 10], cap=(1,))
out = sp.decide({"S0": s0, "S1": capped})
check("equal rate, more cap hits -> S0", any("WINNER S0" in l for l in out))

print("\ncase 7  measure() with a scripted server")
import model_client                                    # noqa: E402
import score                                           # noqa: E402
import scorers                                         # noqa: E402
from agent import AgentResult, Step                    # noqa: E402

model_client.base_url = lambda: "http://scripted:11434"
model_client.model_digest = lambda name: "scripted-digest"


class FakeResponse:
    def __init__(self, p): self._p = p
    def raise_for_status(self): pass
    def json(self): return self._p


sent: list[dict] = []
state = {"n": 0}


def post(url, json=None, timeout=None):
    sent.append(json)
    state["n"] += 1
    # the 2nd agent turn of the whole probe runs away
    done = "length" if state["n"] == 3 else "stop"
    return FakeResponse({"model": "s", "done_reason": done, "eval_count": 2048 if done == "length" else 50,
                         "message": {"content": "{}"}})


model_client.requests.post = post


def fake_runner(case):
    model_client.chat([{"role": "user", "content": case["question"]}])   # raises on the cap
    calls = [(t["tool"], t["arguments"]) for t in case.get("expected_tools", [])]
    steps = [Step(n=i + 1, action="call", tool=t, arguments=a, ok=True)
             for i, (t, a) in enumerate(calls)] + [Step(n=len(calls) + 1, action="final")]
    return AgentResult(ok=True, answer="scripted", reason="final", steps=steps)


def fake_extract(question, answer, expected, **kw):
    model_client.chat([{"role": "user", "content": answer}])
    return expected


scorers.default_agent_runner = fake_runner
score.extract_facts = fake_extract
with tempfile.TemporaryDirectory() as d:
    path = sp.measure("S1", 1, Path(d))
    recs = [json.loads(l) for l in path.read_text().splitlines()]
calls = [r for r in recs if r["type"] == "call"]
cases = [r for r in recs if r["type"] == "case"]
check("a meta line first, naming the arm and override",
      recs[0]["type"] == "meta" and recs[0]["override"] == sp.ARMS["S1"])
check("every call tagged with a role", all(c["role"] in ("agent", "judge") for c in calls),
      str({c["role"] for c in calls}))
check("every call tagged with a case and run", all(c["case"] and c["run"] == 1 for c in calls))
check("the arm's override reached the wire: no temperature sent",
      all("temperature" not in s["options"] for s in sent))
check("the runaway was counted as a cap hit", sum(c["done_reason"] == "length" for c in calls) == 1)
err = [c for c in cases if c["result"] == "agent-error"]
check("its case is 'agent-error', not a crash", len(err) == 1, str(err))
check("and the probe carried on through the whole suite", len(cases) == 13, str(len(cases)))
check("override cleared afterwards", model_client.SAMPLING_OVERRIDE is None)
check("observer removed afterwards", model_client.CALL_OBSERVERS == [])

print("\ncase 8  call (v)'s pin check - reproduces S1's stable sequences, or says which not")


def pinned(name, per_run):
    """per_run: {run: [(case, role, hash), ...]}"""
    return {"meta": [{"override": None, "git_head": "x"}], "cases": [],
            "calls": [{"type": "call", "arm": name, "run": r, "case": c, "role": ro,
                       "output_sha256": h, "completion_tokens": 1, "done_reason": "stop"}
                      for r, rows in per_run.items() for c, ro, h in rows]}


s1 = pinned("S1", {1: [("a", "agent", "h1"), ("b", "agent", "x1")],
                   2: [("a", "agent", "h1"), ("b", "agent", "x2")]})   # a stable, b not
good = pinned("S1p", {1: [("a", "agent", "h1"), ("b", "agent", "zz")]})
bad = pinned("S1p", {1: [("a", "agent", "OTHER"), ("b", "agent", "x1")]})
out = sp.pin_check({"S1": s1, "S1p": good})
check("only STABLE pairs are required (b varied under S1 itself)", "1/1" in out[0], out[0])
check("all stable pairs reproduced -> ADOPT", "ADOPT: the pinned" in out[-1])
out = sp.pin_check({"S1": s1, "S1p": bad})
check("one stable pair differs -> DO NOT ADOPT, naming it",
      "DO NOT ADOPT" in out[-1] and any("differs: a / agent" in l for l in out), "\n".join(out))
check("S1p is never ranked by the rule",
      not any("S1p" in l for l in sp.decide({"S0": s0, "S1p": good})))

print("\ncase 9  two invocations of one arm stay separate runs (the [A, B, A, B] bug)")
with tempfile.TemporaryDirectory() as d:
    for stamp in ("20260101T000000Z", "20260101T010000Z"):
        rows = [{"type": "meta", "arm": "S1p", "override": None, "git_head": "x"}]
        for r in (1, 2):
            rows += [{"type": "call", "arm": "S1p", "run": r, "case": "a", "role": "agent",
                      "output_sha256": h, "completion_tokens": 1, "done_reason": "stop"}
                     for h in ("A", "B")]
            rows += [{"type": "case", "arm": "S1p", "run": r, "case": f"c{k}",
                      "result": "pass"} for k in range(13)]
        (Path(d) / f"sampling-S1p-{stamp}.jsonl").write_text(
            "\n".join(json.dumps(x) for x in rows), encoding="utf-8")
    loaded = sp.load([str(x) for x in Path(d).glob("*.jsonl")])
st = sp.stats(loaded["S1p"])
check("4 runs, not 2", st["runs"] == 4, str(st["runs"]))
check("13 passes per run, not 26", st["pass_per_run"] == [13, 13, 13, 13], str(st["pass_per_run"]))
s1_ref = pinned("S1", {1: [("a", "agent", "A"), ("a", "agent", "B")],
                       2: [("a", "agent", "A"), ("a", "agent", "B")]})
out = sp.pin_check({"S1": s1_ref, "S1p": loaded["S1p"]})
check("each run's sequence is [A, B] again -> the check passes", "1/1" in out[0], out[0])

print("\ncase 10  the paired check's bar")
v = sp.pair_verdict([("a", "a", "a"), ("b", "b", "b")])
check("every stable round matched -> ADOPT", "ADOPT: the pinned" in v[-1], v[0])
v = sp.pair_verdict([("a", "a", "a"), ("x", "q", "y")])
check("an unstable round is excluded, not failed", "1 stable" in v[0] and "ADOPT: the pinned" in v[-1], v[0])
v = sp.pair_verdict([("a", "a", "a"), ("b", "Z", "b")])
check("one stable round where P differs -> DO NOT ADOPT", "DO NOT ADOPT" in v[-1])
v = sp.pair_verdict([("x", "x", "y"), ("p", "q", "r")])
check("zero stable rounds -> NO VERDICT, never a pass", "NO VERDICT" in v[-1])

print("\ncase 11  the paired check end to end, scripted server")
import requests as _rq                                # noqa: E402
bodies: list[dict] = []


def pair_post(url, json=None, timeout=None):
    bodies.append(json)
    # the answer depends ONLY on the options that decide sampling -> a correct
    # pin must reproduce the unpinned answer when the server supplies 0.6
    o = json["options"]
    t = o.get("temperature", 0.6)                      # the Modelfile default
    return FakeResponse({"model": "s", "done_reason": "stop", "eval_count": 5,
                         "message": {"content": f"t={t}"}})


model_client.requests.post = pair_post
real_capture = sp.capture_first_turns
sp.capture_first_turns = lambda cases: [(c["id"], [{"role": "user", "content": c["id"]}], None)
                                        for c in cases[:2]]
with tempfile.TemporaryDirectory() as d:
    sp.pair(2, Path(d))
    lines = [json.loads(l) for l in next(Path(d).glob("pair-check-*.jsonl")).read_text().splitlines()]
check("2 cases x 2 rounds recorded", len(lines) == 4, str(len(lines)))
check("send order is U1, P, U2 (the pinned send in the middle)",
      "temperature" not in bodies[0]["options"] and bodies[1]["options"]["temperature"] == 0.6
      and "temperature" not in bodies[2]["options"])
check("a correct pin matches every stable round", all(l["P"] == l["U1"] == l["U2"] for l in lines))
sp.PINNED = {**sp.PINNED, "temperature": 0.7}           # SABOTAGE: a wrong pinned value
with tempfile.TemporaryDirectory() as d:
    sp.pair(1, Path(d))
    lines = [json.loads(l) for l in next(Path(d).glob("pair-check-*.jsonl")).read_text().splitlines()]
check("POSITIVE CONTROL: a wrong value breaks EVERY pair",
      all(l["P"] != l["U1"] and l["U1"] == l["U2"] for l in lines))
sp.capture_first_turns = real_capture
check("override and observers cleared afterwards",
      model_client.SAMPLING_OVERRIDE is None and model_client.CALL_OBSERVERS == [])

print("\ncase 12  capture_first_turns takes the REAL first request off the wire (no network)")
from suite import parse_suite                         # noqa: E402
real_cases = parse_suite(sp.SUITE).select("fixed")[:3]
def never(*a, **k):
    raise AssertionError("capture sent a request to the network")


model_client.requests.post = never
got = sp.capture_first_turns(real_cases)
check("one request per case", len(got) == 3, str(len(got)))
check("each carries its own question as the last message",
      all(msgs[-1]["content"].find(c["question"]) >= 0 for (cid, msgs, _), c in zip(got, real_cases)))
check("each carries the agent's step schema as `format`", all(isinstance(f, dict) for _, _, f in got))
check("the caller's post is restored afterwards (the capturing one does not leak)",
      model_client.requests.post is never)

print()
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("PASS: the probe measures what call 1 said and decides the way call 3 said")
