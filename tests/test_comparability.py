"""P04 build step 7: header comparability (ADR 0008, call 4b(ii)).

THE CALL (Jose, 2026-09-28) = C: identity at TWO levels - a canonical hash of
the suite HEADER (suppresses the whole drift section) and a canonical hash of
each case's DEFINITION on every row (that case alone is REDEFINED, not
compared). "because it hides anything that could lie" - sharpened: it hides
exactly what could lie, and nothing else. Rejected A (file bytes: a reorder or
one added case silences every comparison), B (one canonical hash: one reworded
case silences the other twelve).

WHAT THIS PROVES
  case 1  the hashes: canonical (key order, line order, whitespace do not move
          them); description and tier excluded; question and vocabulary included
  case 2  drift: a redefined case is withheld even when its result flipped;
          the others still compare
  -- end to end through eval_harness.run(), against a baseline it promoted --
  case 3  STEP 5's CARRY: a REORDERED suite is compared, not suppressed - zero drift
  case 4  one reworded case -> REDEFINED alone; the other 12 compared
  case 5  the STATED CHECK, identity: the baseline's scorer edited by hand ->
          drift SUPPRESSED, the field and both values printed, exit unchanged
  case 6  identity from the suite: vocabulary changed -> suppressed, names
          suite_header_sha256
  case 7  STEP 5's OTHER CARRY: identity mismatch AND a removed case -> still
          exit 2, VANISHED printed under the suppression
  case 8  the STATED CHECK, condition: model_digest and sampling edited by hand
          -> annotated with both values, drift STILL compared, exit unchanged

No model: scripted runner + scripted judge.
Run:  python tests/test_comparability.py
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import drift                                       # noqa: E402
import eval_harness                                # noqa: E402
import run_record                                  # noqa: E402
import scorers                                     # noqa: E402
import suite as suite_mod                          # noqa: E402
from agent import AgentResult, Step                # noqa: E402
from suite import parse_suite                      # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


SUITE = REPO_ROOT / "evals" / "suites" / "agent.jsonl"
LINES = SUITE.read_text(encoding="utf-8").splitlines()
HEAD, CASES = json.loads(LINES[0]), [json.loads(l) for l in LINES[1:] if l.strip()]
IDS = [c["id"] for c in CASES]
tmp = Path(tempfile.mkdtemp())


def write_suite(name: str, head: dict, cases: list[dict]) -> Path:
    p = tmp / name / "agent.jsonl"          # same stem, so drift pairs it with agent.json
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(x) for x in [head, *cases]) + "\n", encoding="utf-8")
    return p


def runner(case):
    calls = [(t["tool"], t["arguments"]) for t in case.get("expected_tools", [])]
    steps = [Step(n=i + 1, action="call", tool=t, arguments=a, ok=True)
             for i, (t, a) in enumerate(calls)]
    steps.append(Step(n=len(calls) + 1, action="final"))
    return AgentResult(ok=True, answer=f"scripted answer for {case['id']}", reason="final", steps=steps)


BASE = tmp / "baselines" / "agent.json"


def harness(suite_path: Path, *, rebaseline=False, baseline=BASE):
    eval_harness.resolve = lambda loaded: (lambda **kw: scorers.make_agent_scorer(
        runner=runner, judge=lambda q, a, e, **k: e, **kw))
    argv = ["--suite", str(suite_path), "--tier", "fixed", "--out", str(tmp / "runs"),
            "--baseline", str(baseline)] + (["--rebaseline"] if rebaseline else [])
    o, e = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
        try:
            code = eval_harness.run(eval_harness.build_parser().parse_args(argv))
        except Exception as exc:                     # noqa: BLE001 - fail by name
            code = f"CRASH {type(exc).__name__}: {exc}"
    return code, o.getvalue(), e.getvalue()


# ---------------------------------------------------------------------------
print("case 1  the hashes")
c = CASES[0]
reordered_keys = dict(reversed(list(c.items())))
check("key order does not move a case hash", suite_mod.case_sha256(c) == suite_mod.case_sha256(reordered_keys))
check("tier is excluded (tier-moved has its own report)",
      suite_mod.case_sha256(c) == suite_mod.case_sha256({**c, "tier": "live"}))
check("id is excluded (it is the match key)",
      suite_mod.case_sha256(c) == suite_mod.case_sha256({**c, "id": "renamed"}))
check("a reworded question DOES move it",
      suite_mod.case_sha256(c) != suite_mod.case_sha256({**c, "question": c["question"] + " now?"}))
h = parse_suite(SUITE).header
from dataclasses import replace as dc_replace         # noqa: E402
check("description is excluded from the header hash",
      suite_mod.header_sha256(h) == suite_mod.header_sha256(dc_replace(h, description="reworded prose")))
vocab2 = json.loads(json.dumps(h.vocabulary)); vocab2["service"].append("BITS")
check("vocabulary IS in the header hash",
      suite_mod.header_sha256(h) != suite_mod.header_sha256(dc_replace(h, vocabulary=vocab2)))
spaced = tmp / "spaced" / "agent.jsonl"; spaced.parent.mkdir(parents=True)
spaced.write_text("\n".join(json.dumps(x, indent=None, separators=(" , ", " : "))
                            for x in [HEAD, *reversed(CASES)]) + "\n", encoding="utf-8")
s2 = parse_suite(spaced)
check("whitespace + line order: same header hash, same case hashes, different BYTES",
      suite_mod.header_sha256(s2.header) == suite_mod.header_sha256(h)
      and {x["id"]: suite_mod.case_sha256(x) for x in s2.cases}
      == {x["id"]: suite_mod.case_sha256(x) for x in CASES}
      and run_record.suite_sha256(spaced) != run_record.suite_sha256(SUITE))

# ---------------------------------------------------------------------------
print("\ncase 2  drift withholds a redefined case, and only that one")
def rec(rows): return {"header": {"tier_selection": "fixed"}, "cases": rows}
def r(i, res, hsh): return {"id": i, "tier": "fixed", "result": res, "case_sha256": hsh, "detail": {}}
was = rec([r("a", "pass", "1" * 64), r("b", "pass", "2" * 64)])
now = rec([r("a", "fail", "9" * 64), r("b", "fail", "2" * 64)])
d = drift.compare(now, was)
check("a (redefined AND flipped) is REDEFINED, not REGRESSED", d.redefined == ("a",) and "a" not in d.regressed,
      f"redefined={d.redefined} regressed={d.regressed}")
check("b (same definition, flipped) is still REGRESSED", d.regressed == ("b",))
check("compared counts both; neither counted unchanged", d.compared == 2 and d.unchanged == 0)

# ---------------------------------------------------------------------------
print("\ncase 3  a reordered suite is compared, not suppressed (step 5's carry)")
original = write_suite("orig", HEAD, CASES)
code0, out, err = harness(original, rebaseline=True)
check("baseline promoted, exit 0", code0 == 0 and BASE.exists(), f"got {code0}")
reordered = write_suite("reordered", HEAD, list(reversed(CASES)))
code, out, err = harness(reordered)
check("exit unchanged (0)", code == 0, f"got {code}")
check("NOT suppressed", "SUPPRESSED" not in out)
check(f"compared {len(IDS)} unchanged {len(IDS)}", f"compared {len(IDS)}  unchanged {len(IDS)}" in out,
      next((l for l in out.splitlines() if "DRIFT" in l), "no DRIFT line"))

# ---------------------------------------------------------------------------
print("\ncase 4  one reworded case -> REDEFINED alone")
reworded = [dict(x) for x in CASES]
reworded[0]["question"] = reworded[0]["question"].rstrip("?") + " right now?"
code, out, err = harness(write_suite("reworded", HEAD, reworded))
check("exit unchanged (0)", code == 0, f"got {code}")
check(f"REDEFINED {IDS[0]}", f"REDEFINED {IDS[0]}" in out)
check(f"the other {len(IDS) - 1} compared unchanged, redefined 1",
      f"compared {len(IDS)}  unchanged {len(IDS) - 1}" in out and "redefined 1" in out,
      next((l for l in out.splitlines() if "DRIFT" in l), "no DRIFT line"))
check("not suppressed", "SUPPRESSED" not in out)


def edited_baseline(name: str, **header_edits) -> Path:
    doc = json.loads(BASE.read_text(encoding="utf-8"))
    doc["header"].update(header_edits)
    p = tmp / "edited" / name / "agent.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
print("\ncase 5  STATED CHECK, identity: the baseline's scorer edited by hand")
code, out, err = harness(original, baseline=edited_baseline("scorer", scorer="retrieval"))
check("exit unchanged (0) - the verdict was established", code == 0, f"got {code}")
check("DRIFT SUPPRESSED", "DRIFT      SUPPRESSED" in out)
check("names the field and BOTH values",
      "scorer" in out and "baseline retrieval" in out and "now      agent" in out)
check("no drift counters printed", "matched by id" not in out)

# ---------------------------------------------------------------------------
print("\ncase 6  identity from the suite: vocabulary changed")
head2 = json.loads(json.dumps(HEAD)); head2["vocabulary"]["service"].append("BITS")
code, out, err = harness(write_suite("vocab", head2, CASES))
check("exit unchanged (0)", code == 0, f"got {code}")
check("SUPPRESSED, naming suite_header_sha256",
      "DRIFT      SUPPRESSED" in out and "suite_header_sha256" in out)

# ---------------------------------------------------------------------------
print("\ncase 7  identity mismatch AND a removed case: exit 2 still fires")
code, out, err = harness(write_suite("vocab-minus", head2, CASES[:-1]))
check("exit 2", code == 2, f"got {code}")
check("suppressed", "DRIFT      SUPPRESSED" in out)
check(f"VANISHED {IDS[-1]} printed under the suppression", f"VANISHED  {IDS[-1]}" in out)

# ---------------------------------------------------------------------------
print("\ncase 8  STATED CHECK, condition: model_digest and sampling edited by hand")
code, out, err = harness(original, baseline=edited_baseline(
    "cond", model_digest="sha256:" + "e" * 64, sampling={"temperature": 0.0, "seed": 42}))
check("exit unchanged (0)", code == 0, f"got {code}")
check("CONDITIONS annotated", "CONDITIONS differ from the baseline" in out)
check("model_digest with both values", "model_digest" in out and "sha256:eeeeeeeee" in out
      and "scripted-runner-no-model" in out)
check("sampling with both values", "baseline seed=42 temperature=0.0" in out
      and "now      none - this scorer generates nothing" in out)
check("drift STILL compared", f"compared {len(IDS)}  unchanged {len(IDS)}" in out and "SUPPRESSED" not in out)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED")
    sys.exit(1)
print("all checks passed")
