"""P02 build step 6c: the answer scorer - one factory, one table row (ADR 0025).

  case 1  the gate, row by row: answerable passes only on answered + the
          expected chunk cited; unanswerable passes on EITHER honest refusal;
          refused-citation never passes; answered-an-unanswerable fails
  case 2  the factory, scripted end to end: retrieval -> answer -> row,
          the passed-over warning lands in the row, conditions say "scripted"
  case 3  garbage from the model is a FAILED row (outcome schema-error), not a
          crash - the model was reached and answered unusably
  case 4  the report tallies outcomes and retries for a string-outcome scorer,
          and prints nothing extra for the agent scorer (bool outcome)
  case 5  the suite: 23 cases, 12/4/7 by category; every answerable case is
          the SAME question with the SAME expect as retrieval.jsonl - the two
          suites cannot drift apart silently; every expect is a manifest path

No model, no index, no network. CI home.
Run:  python tests/test_answer_scorer.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import answer as A                                   # noqa: E402
import report                                        # noqa: E402
import scorers                                       # noqa: E402
from manifest import load_manifest                   # noqa: E402
from retrieval import Chunk, Hit                     # noqa: E402
from suite import parse_suite                        # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


EXP = "data/runbooks/service-restart.md#symptoms"
MS = "data/microsoft/sc-query.md#overview"


def ans(outcome: str, cited=()) -> A.Answer:
    return A.Answer("q", outcome, "t", list(cited), 1, [(EXP, 0.6)])


answerable = {"id": "a", "tier": "fixed", "expect": EXP, "category": "answerable"}
unanswerable = {"id": "u", "tier": "fixed", "expect": None, "category": "near-miss"}

print("case 1  the gate")
table = [
    (answerable, ans("answered", [EXP]), "pass"),
    (answerable, ans("answered", [MS]), "fail"),                  # answered from the wrong chunk
    (answerable, ans("refused-not-in-sources"), "fail"),          # over-refusal (the w32tm finding)
    (answerable, ans("refused-no-retrieval"), "fail"),
    (unanswerable, ans("refused-no-retrieval"), "pass"),
    (unanswerable, ans("refused-not-in-sources"), "pass"),
    (unanswerable, ans("refused-citation"), "fail"),              # untraceable is not a refusal
    (unanswerable, ans("answered", [EXP]), "fail"),               # answered a near-miss
]
for case, a, want in table:
    got = scorers.answer_row(case, a, []).result
    check(f"{case['category']:<10} {a.outcome:<24} cited={a.cited} -> {want}", got == want, got)

print("case 2  the factory, scripted")
hits = [Hit(Chunk(MS, "data/microsoft/sc-query.md", "sc.exe query", "Overview", "x", "microsoft"), 0.62),
        Hit(Chunk(EXP, "data/runbooks/service-restart.md", "Service restart", "Symptoms", "y", "ours"), 0.58)]
reply = json.dumps({"status": "answered", "cited": [MS], "answer": "Use sc query."})
score, cond = scorers.make_answer_scorer(generate=lambda m, s: reply, retriever=lambda q: hits)
row = score({"id": "x", "tier": "fixed", "question": "q", "expect": EXP, "category": "answerable"})
check("fails: answered, but not from the expected chunk", row.result == "fail")
check("the passed-over warning is in the row",
      len(row.detail["warnings"]) == 1 and "service-restart.md" in row.detail["warnings"][0],
      str(row.detail["warnings"]))
check("retrieved ids and top score recorded",
      row.detail["retrieved"] == [MS, EXP] and row.detail["top_score"] == 0.62)
check("scripted conditions never pose as a measurement",
      cond.model_name == "scripted" and cond.sampling is None)
row = score({"id": "y", "tier": "fixed", "question": "q", "expect": MS, "category": "answerable"})
check("passes when the expected chunk is the cited one", row.result == "pass")

print("case 3  garbage")
score, _ = scorers.make_answer_scorer(generate=lambda m, s: "not json", retriever=lambda q: hits)
row = score({"id": "g", "tier": "fixed", "question": "q", "expect": None, "category": "near-miss"})
check("a failed row, not an exception", row.result == "fail")
check("outcome says schema-error, error kept", row.detail["outcome"] == "schema-error"
      and row.detail["errors"], str(row.detail["errors"])[:60])

print("case 4  the report tally")


def record(rows: list[dict]) -> dict:
    return {"header": {"suite_path": "evals/suites/answer.jsonl", "scorer": "answer",
                       "model_name": "m", "model_digest": "d", "host_target": "h",
                       "sampling": None, "suite_sha256": "0" * 64,
                       "suite_header_sha256": "0" * 64, "started_at": "t",
                       "tier_selection": "fixed", "incomplete": None, "rebaseline": None},
            "cases": rows}


def r(outcome, attempts, result="pass"):
    return {"id": f"c{outcome}{attempts}", "tier": "fixed", "result": result,
            "detail": {"outcome": outcome, "attempts": attempts}, "note": None}


lines = report.render(record([r("answered", 1), r("answered", 2),
                              r("refused-not-in-sources", 1),
                              r("refused-no-retrieval", 0)]), record_path="x")
text = "\n".join(lines)
check("OUTCOMES line counts each outcome",
      "OUTCOMES   answered 2  refused-no-retrieval 1  refused-not-in-sources 1" in text,
      next((l for l in lines if "OUTCOMES" in l), "missing"))
check("RETRIES counts only model-called cases", "RETRIES    1 of 3 model-called" in text)
agent_lines = report.render(record([{"id": "a", "tier": "fixed", "result": "pass",
                                     "detail": {"outcome": True}, "note": None}]), record_path="x")
check("no tally for a bool outcome (the agent scorer)",
      not any("OUTCOMES" in l for l in agent_lines))

print("case 5  the suite")
suite = parse_suite(REPO_ROOT / "evals" / "suites" / "answer.jsonl")
cats = {}
for c in suite.cases:
    cats[c["category"]] = cats.get(c["category"], 0) + 1
check("27 cases: 16 answerable, 4 out-of-domain, 7 near-miss",   # 23 -> 27 P02 step 8
      cats == {"answerable": 16, "out-of-domain": 4, "near-miss": 7}, str(cats))
check("expect present iff answerable",
      all((c["expect"] is not None) == (c["category"] == "answerable") for c in suite.cases))
ret = {c["id"]: c for c in parse_suite(REPO_ROOT / "evals" / "suites" / "retrieval.jsonl").cases}
shared = [c for c in suite.cases if c["category"] != "near-miss"]
check("every non-near-miss case matches retrieval.jsonl word for word",
      all(c["id"] in ret and ret[c["id"]]["question"] == c["question"]
          and ret[c["id"]]["expect"] == c["expect"] for c in shared))
paths = {e.path for e in load_manifest().entries}
check("every expect names a manifest document",
      all(c["expect"].split("#")[0] in paths for c in suite.cases if c["expect"]))

print("case 6  the real path unloads the model first (fresh-load rule, 2026-10-09)")
import tempfile                                       # noqa: E402
import model_client                                   # noqa: E402
import retrieval                                      # noqa: E402
calls: list[str] = []
saved = (model_client.unload, model_client.model_digest, retrieval.load_index, retrieval.INDEX_PATH)
own_index = Path(tempfile.mkdtemp()) / "index.json"
own_index.write_text('{"model": "fixture-embedder", "chunks": []}', encoding="utf-8")
model_client.unload = lambda name=None: calls.append(("unload", name))
model_client.model_digest = lambda name=None: (calls.append(("digest", name)), "d" * 64)[1]
retrieval.load_index = lambda **kw: []
retrieval.INDEX_PATH = own_index
try:
    _, cond = scorers.make_answer_scorer()
    check("unload called exactly once, for the measured model",
          [c for c in calls if c[0] == "unload"] == [("unload", model_client.DEFAULT_MODEL)], str(calls))
    check("conditions record num_ctx with the sampling",
          cond.sampling and cond.sampling.get("num_ctx") == A.ANSWER_NUM_CTX, str(cond.sampling))
    check("the digest names model and index",
          cond.model_digest.startswith("d" * 64) and " index:" in cond.model_digest)
    calls.clear()
    scorers.make_answer_scorer(generate=lambda m, s: "{}", retriever=lambda q: [])
    check("a scripted run unloads nothing", calls == [], str(calls))
finally:
    model_client.unload, model_client.model_digest, retrieval.load_index, retrieval.INDEX_PATH = saved

print()
if failures:
    print(f"FAIL: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PASS: answer scorer - mechanical gate, scripted factory, tally, suite integrity")
