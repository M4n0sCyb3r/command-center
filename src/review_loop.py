"""Project 3, build step 5: the revision loop - critic reviews, drafter revises (ADR 0032).

THE LOOP (Jose's pick, 2026-10-18: Hermes reviews, as ADR 0031 designed)
------------------------------------------------------------------------
    critique -> [revise -> critique] x <= 2

The CODE decides when it stops (Lab 4), and every stop records why:
  approved        the critic returned no blocking finding
  escalate-risk   a blocking finding is blast-radius or security: ADR 0032 escalates NOW,
                  without spending local rounds. The escalation tier is step 6; until then
                  the row stops here, marked, with the script as it stands.
  escalate-cap    still blocked after 2 revisions
  no-change       the reviser returned the script byte-identical: another round learns nothing
  error           a call failed; the row keeps the last good script and counts as an error

The critique is the step-4 critic prompt, byte for byte (review_runner.build_messages), so
round 0 of this loop IS the step-4 critic run - its verdicts must reproduce. For that, each call
runs with ONLY its own model loaded (see _alone).

"FIXED" NEEDS A LINTER READING, SO SCORING IS A SECOND PASS
----------------------------------------------------------
ADR 0028/0032: a defect is fixed when (1) its oracle no longer matches, (2) the final script
lints clean, and (3) no OTHER planted oracle for that script now matches (the fix did not
plant a sibling bug). A clean control is "broke (detectable)" when the loop changed it and (2)
or (3) fails.

"DETECTABLE" IS IN THE NAME ON PURPOSE (Jose, 2026-10-18)
The first scored run said broke 0 - and both rewritten controls were broken by hand reading:
11-C lost its guard for profiles with no LastUseTime (they would be DELETED), 12-C lost
ShouldProcess and inverted its hash check. Lint plus the planted oracles can only see the bugs
we planted, never a new one. So this number is a floor, reported beside a separate hand audit
of every changed control (evidence), never merged with it.

PSScriptAnalyzer runs on Windows, so:
    python src/review_loop.py                    # run: writes the record + every final script
    (Jose: lint the finals folder, saved as a reading with files[])
    python src/review_loop.py --score RECORD READING
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import model_client                                   # noqa: E402
import review as R                                    # noqa: E402
import review_runner as RR                            # noqa: E402
from structured import SchemaError, parse_json        # noqa: E402

CRITIC = RR.ARMS["critic"]                            # hermes3:8b (ADR 0031)
REVISER = RR.ARMS["drafter-alone"]                    # qwen3:8b - the drafter revises
ESCALATOR = RR.ARMS["escalator"]                      # qwen3:14b (ADR 0031 seam, ADR 0032 5b)
ESCALATE_ON = {"escalate-risk", "escalate-cap", "no-change"}   # still blocked: stuck or risky
MAX_ROUNDS = 2                                        # ADR 0032 5a
RISK = {"blast-radius", "security"}                   # ADR 0032 5b rule 1

REVISE_SYSTEM = """You revise a PowerShell script so that it fixes the reviewer's blocking findings.
Change only what the findings require. Keep everything else exactly as it is: parameters,
comments, messages, formatting and exit codes. Do not add features.
Reply with the complete revised script inside ONE ```powershell fenced block, and nothing else.
"""

FENCE = re.compile(r"```(?:powershell|ps1|pwsh)?[ \t]*\n(.*?)```", re.S | re.I)


class ReviseError(Exception):
    """The reviser's reply did not contain a script."""


def revise_messages(item: R.Item, text: str, blocking: tuple[R.Finding, ...]) -> list[dict[str, str]]:
    lines = text.rstrip("\n").split("\n")
    def quote(f: R.Finding) -> str:
        span = "\n".join(lines[f.line_start - 1:f.line_end])
        return (f"- lines {f.line_start}-{f.line_end} [{f.category}]: {f.why}\n"
                f"  suggested fix: {f.fix}\n  the lines:\n{span}")
    user = (f"Script: {item.script}\n\nBlocking findings:\n" + "\n".join(map(quote, blocking))
            + f"\n\nThe script:\n```powershell\n{text.rstrip(chr(10))}\n```")
    return [{"role": "system", "content": REVISE_SYSTEM}, {"role": "user", "content": user}]


def extract_script(reply_text: str) -> str:
    blocks = FENCE.findall(reply_text.replace("\r\n", "\n"))   # normalise FIRST: a CRLF reply hid its fence
    if len(blocks) != 1:
        raise ReviseError(f"expected one fenced script, found {len(blocks)}")
    return blocks[0].rstrip("\n") + "\n"


# Two seams, so the tests can script both models.
Critique = Callable[[list[dict[str, str]], dict[str, Any]], "model_client.Reply"]
Revise = Callable[[list[dict[str, str]]], "model_client.Reply"]


def _alone(model: str) -> None:
    """Unload BOTH loop models, so `model` loads fresh and ALONE.

    Step 5 run 20261019T032719Z (void): unloading only the called model left qwen3:8b resident
    (Ollama keep-alive, 5 min) after each revision. 4 of 32 round-0 critiques then differed from
    the step-4 critic - all 4 with qwen resident, none of the 4 that ran before any revision -
    and 2 of them broke the contract. A second resident model changes how the server allocates
    and computes, and that is enough to flip a seeded generation. Fresh means alone (ADR 0025)."""
    RR.unload_all()


def default_critique(messages, schema):
    _alone(CRITIC)
    return model_client.chat(messages, model=CRITIC, schema=schema, num_ctx=RR.NUM_CTX,
                             max_tokens=RR.REVIEW_MAX_TOKENS, timeout=RR.REVIEW_TIMEOUT)


def default_escalate(messages, schema):
    _alone(ESCALATOR)
    return model_client.chat(messages, model=ESCALATOR, schema=schema, num_ctx=RR.NUM_CTX,
                             max_tokens=RR.REVIEW_MAX_TOKENS, timeout=RR.REVIEW_TIMEOUT)


def default_revise(messages):
    _alone(REVISER)
    return model_client.chat(messages, model=REVISER, num_ctx=RR.NUM_CTX,
                             max_tokens=RR.REVIEW_MAX_TOKENS, timeout=RR.REVIEW_TIMEOUT)


def _cost(role: str, reply) -> dict[str, Any]:
    return {"role": role, "prompt_tokens": reply.prompt_tokens,
            "completion_tokens": reply.completion_tokens, "duration_s": round(reply.duration_s, 3)}


def _review(item: R.Item, text: str, call: Critique, role: str, trace: list) -> R.Review:
    n = len(text.rstrip("\n").split("\n"))
    schema = R.review_schema(n)
    reply = call(RR.build_messages(item, text), schema)
    review = R.validate_review(parse_json(reply.text, schema), n)
    trace.append({**_cost(role, reply), "verdict": review.verdict,
                  "findings": [f.__dict__ for f in review.findings]})
    return review


def escalate_item(item: R.Item, row: dict[str, Any], current: str, escalate: Critique) -> None:
    """ADR 0032 am. 2 (Jose, 2026-10-18): the escalation reviewer reads BOTH copies, as two
    separate reviews with the same prompt. The CURRENT copy's review decides (its verdict is
    final); the ORIGINAL's review measures the escalator by the step-3 catch rule."""
    original = item.text()
    try:
        orig = _review(item, original, escalate, "escalate", row["trace"])
        cur = orig if current == original else _review(item, current, escalate, "escalate", row["trace"])
    except (SchemaError, model_client.ModelError) as exc:
        # ADR 0032 am. 3: FAIL SAFE - a reviewer that could not finish never approves.
        row.update(terminal="needs-human", error=f"escalation {type(exc).__name__}: {exc}"[:300])
        return
    row["escalation"] = {"original": R.score_item(item, orig), "current_verdict": cur.verdict,
                         "calls": 1 if cur is orig else 2}
    row["terminal"] = "needs-human" if cur.blocking else "approved-after-escalation"


def loop_item(item: R.Item, critique: Critique = default_critique, revise: Revise = default_revise,
              escalate: Critique | None = None) -> tuple[dict[str, Any], str]:
    """Run one item through the loop (and, with `escalate`, the escalation tier).
    Returns (row, final script text). Terminal: approved / approved-after-escalation /
    needs-human / error - or escalation-pending when no escalator is given (step 5)."""
    original = item.text()
    current, rounds, trace, first = original, 0, [], None
    row: dict[str, Any] = {"id": item.id, "control": item.is_control}
    if not item.is_control:
        row["family"] = item.defect["family"]
    t0 = time.monotonic()
    try:
        while True:
            n = len(current.rstrip("\n").split("\n"))
            schema = R.review_schema(n)
            reply = critique(RR.build_messages(item, current), schema)
            review = R.validate_review(parse_json(reply.text, schema), n)
            trace.append({**_cost("critique", reply), "verdict": review.verdict,
                          "findings": [f.__dict__ for f in review.findings]})
            if first is None:
                first = R.score_item(item, review)   # round 0 = the step-4 critic arm
            if not review.blocking:
                stop = "approved"; break
            if any(f.category in RISK for f in review.blocking):
                stop = "escalate-risk"; break
            if rounds == MAX_ROUNDS:
                stop = "escalate-cap"; break
            reply = revise(revise_messages(item, current, review.blocking))
            trace.append(_cost("revise", reply))
            new = extract_script(reply.text)
            rounds += 1
            if new == current:
                stop = "no-change"; break
            current = new
    except (SchemaError, ReviseError, model_client.ModelError) as exc:
        stop = "error"
        row["error"] = f"{type(exc).__name__}: {exc}"[:300]
    row.update(stop=stop, rounds=rounds, changed=current != original,
               calls=len(trace), trace=trace)
    row["round0"] = first                             # None only if the very first critique failed
    # ADR 0032 am. 3 (Jose, 2026-10-19): FAIL SAFE. A call that errored (runaway, timeout, a
    # broken reply) routes the script to a human - never approved, never left in limbo. The
    # error stays on the row; `stop` still says "error", so the count is never hidden.
    row["terminal"] = {"approved": "approved", "error": "needs-human"}.get(stop, "escalation-pending")
    if escalate is not None and stop in ESCALATE_ON:
        escalate_item(item, row, current, escalate)
    row["calls"] = len(row["trace"])
    row["wall_s"] = round(time.monotonic() - t0, 3)  # includes the escalation calls
    return row, current


def loop_item_pipeline(item: R.Item) -> tuple[dict[str, Any], str]:
    """The pipeline as designed, for one item - the harness's `review` scorer calls this."""
    return loop_item(item, default_critique, default_revise, default_escalate)


def run(split: str = "development", final: bool = False, critique: Critique = default_critique,
        revise: Revise = default_revise, out_dir: Path | None = None,
        escalate: Critique | None = default_escalate) -> dict[str, Any]:
    items = RR.select(split, final)
    rows = []
    for k, it in enumerate(items, start=1):
        row, text = loop_item(it, critique, revise, escalate)
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{it.id}.ps1").write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
        row["final_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        rows.append(row)
        print(f"  [{k:>2}/{len(items)}] {it.id:<7} {row['stop']:<14} rounds {row['rounds']}  "
              f"-> {row['terminal']:<26}"
              f"{row['wall_s']:>6.1f}s", file=sys.stderr, flush=True)
    by_role: dict[str, dict[str, float]] = {}
    for r in rows:
        for c in r["trace"]:
            s = by_role.setdefault(c["role"], {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0})
            s["calls"] += 1
            s["prompt_tokens"] += c["prompt_tokens"] or 0
            s["completion_tokens"] += c["completion_tokens"] or 0
            s["seconds"] = round(s["seconds"] + (c["duration_s"] or 0), 1)
    stops: dict[str, int] = {}
    terminals: dict[str, int] = {}
    for r in rows:
        stops[r["stop"]] = stops.get(r["stop"], 0) + 1
        terminals[r["terminal"]] = terminals.get(r["terminal"], 0) + 1
    return {"arm": "pipeline" if escalate else "critic+revision", "critic": CRITIC, "reviser": REVISER,
            "escalator": ESCALATOR if escalate else None, "terminals": terminals, "split": split,
            "items": len(rows), "max_rounds": MAX_ROUNDS,
            "rubric_sha256": hashlib.sha256(RR.RUBRIC.read_bytes()).hexdigest()[:16],
            "revise_prompt_sha256": hashlib.sha256(REVISE_SYSTEM.encode()).hexdigest()[:16],
            "stops": stops, "cost": by_role, "rows": rows}


# --- second pass: score the final scripts against a linter reading -------------

def score(record: dict[str, Any], finals: Path, reading: Path) -> dict[str, Any]:
    files, findings = R.read_pssa(reading)
    wanted = {f"{r['id']}.ps1" for r in record["rows"]}
    if not wanted <= set(files):
        raise R.ReadingError(f"the reading does not list {len(wanted - set(files))} final script(s)")
    lint: dict[str, list[str]] = {}
    for h in findings:
        lint.setdefault(h["ScriptName"], []).append(h["RuleName"])
    items = {it.id: it for it in R.load_set()}
    oracles: dict[str, list[tuple[str, str]]] = {}
    for it in items.values():
        if not it.is_control:
            oracles.setdefault(it.script, []).append((it.id, it.defect["oracle"]))
    out = {"defects": 0, "fixed": 0, "controls": 0, "rewritten": 0, "broke_detectable": 0,
           "by_family": {f: {"fixed": 0, "of": 0} for f in ("lint-visible", "logic", "blast-radius")}}
    for r in record["rows"]:
        it = items[r["id"]]
        text = (finals / f"{r['id']}.ps1").read_bytes().decode("utf-8").replace("\r\n", "\n")
        rules = lint.get(f"{r['id']}.ps1", [])
        planted = [i for i, rx in oracles[it.script] if i != it.id and re.search(rx, text, re.M)]
        r["lint"], r["oracles_firing"] = rules, planted
        if it.is_control:
            r["broke_detectable"] = r["changed"] and bool(rules or planted)
            out["controls"] += 1; out["rewritten"] += r["changed"]; out["broke_detectable"] += r["broke_detectable"]
            out["controls_to_human"] = out.get("controls_to_human", 0) + (r.get("terminal") == "needs-human")
        else:
            gone = not re.search(it.defect["oracle"], text, re.M)
            r["fixed"] = r["changed"] and gone and not rules and not planted
            out["defects"] += 1; out["fixed"] += r["fixed"]
            # The pipeline's outcome for a bugged script: it either stopped it (needs-human),
            # shipped it fixed, or SHIPPED IT BROKEN (approved without a fix) - the number that matters.
            shipped = r.get("terminal", "").startswith("approved")
            out["shipped_broken"] = out.get("shipped_broken", 0) + (shipped and not r["fixed"])
            out["stopped"] = out.get("stopped", 0) + (r.get("terminal") == "needs-human")
            fam = out["by_family"][r["family"]]; fam["of"] += 1; fam["fixed"] += r["fixed"]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P03 revision loop: run, or --score a run.")
    ap.add_argument("--split", choices=("development", "held_out"), default="development")
    ap.add_argument("--final", action="store_true", help="permit held-out (build step 8 only)")
    ap.add_argument("--score", nargs=2, metavar=("RECORD", "READING"), type=Path)
    ap.add_argument("--no-escalate", action="store_true", help="the step-5 loop alone (no escalation tier)")
    a = ap.parse_args(argv)
    if a.score:
        rec_path, reading = a.score
        record = json.loads(rec_path.read_text(encoding="utf-8"))
        s = score(record, rec_path.with_suffix(""), reading)
        record["scored"] = {**s, "reading": reading.name}
        rec_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(f"=== P03 {record['arm']} on {record['split']} - scored with {reading.name}")
        print(f"  fixed        {s['fixed']}/{s['defects']}  "
              + "  ".join(f"{k} {v['fixed']}/{v['of']}" for k, v in s["by_family"].items()))
        if "shipped_broken" in s:
            print(f"  bugged       shipped broken {s['shipped_broken']}/{s['defects']}   stopped (needs-human) {s['stopped']}"
                  f"   clean sent to a human {s['controls_to_human']}/{s['controls']}")
        print(f"  controls     rewritten {s['rewritten']}/{s['controls']}   broke (detectable) {s['broke_detectable']}"
              "   <- a floor: lint + planted oracles cannot see a NEW bug; hand-audit every rewrite")
        return 0
    try:
        models = (CRITIC, REVISER) if a.no_escalate else (CRITIC, REVISER, ESCALATOR)
        digests = {m: model_client.model_digest(m)[:12] for m in models}
        started = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        finals = RR.RUNS / f"{started}-p3-loop-{a.split}"
        record = run(a.split, a.final, out_dir=finals, escalate=None if a.no_escalate else default_escalate)
    except model_client.ModelError as exc:
        print(f"review_loop: could not establish the run: {exc}", file=sys.stderr)
        return 2
    record.update(started=started, digests=digests)
    out = finals.with_suffix(".json")
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    r0 = [r["round0"] for r in record["rows"] if r["round0"]]
    caught0 = sum(x.get("caught", False) for x in r0); fa0 = sum(x.get("false_alarm", False) for x in r0)
    print(f"=== P03 {record['arm']} ({CRITIC} reviews, {REVISER} revises"
          + ("" if a.no_escalate else f", {ESCALATOR} escalation") + f") on {a.split}")
    nd = sum(not r["control"] for r in record["rows"]); nc = len(record["rows"]) - nd
    print(f"  round 0      caught {caught0}/{nd}   false alarms {fa0}/{nc}   (must equal the step-4 critic)")
    print("  stops        " + "  ".join(f"{k} {v}" for k, v in sorted(record["stops"].items())))
    print("  terminals    " + "  ".join(f"{k} {v}" for k, v in sorted(record["terminals"].items())))
    esc = [r["escalation"]["original"] for r in record["rows"] if "escalation" in r]
    if esc:
        ed = [x for x in esc if not x["control"]]
        print(f"  escalator on originals (escalated only): caught {sum(x['caught'] for x in ed)}/{len(ed)}"
              f"   false alarms {sum(x['false_alarm'] for x in esc if x['control'])}/{len(esc) - len(ed)}")
    for role, c in record["cost"].items():
        print(f"  {role:<12} {c['calls']} calls  {c['prompt_tokens']} in / {c['completion_tokens']} out  {c['seconds']} s")
    print(f"  record       {out.relative_to(R.REPO_ROOT)}")
    print(f"  finals       {finals.relative_to(R.REPO_ROOT)}   <- lint this folder, then --score")
    return 0 if not any("error" in r for r in record["rows"]) else 2   # routed safely, still reported


if __name__ == "__main__":
    sys.exit(main())
