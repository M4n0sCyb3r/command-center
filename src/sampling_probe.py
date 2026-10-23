"""ADR 0009's instrument: measure the token tail of each sampling arm.

WHY THIS EXISTS. qwen3 at temperature 0 can repeat forever (the vendor: "DO NOT
use greedy decoding"); the 2,048-token cap bounds that, it does not remove it.
The fixes change what the agent IS, so ADR 0009 decides from data - and the
data plan was fixed BEFORE any of it was collected (Jose, 2026-09-27):

  call 1 = B   measure the TAIL of generated tokens per call, not only cap hits:
               a runaway is too rare to count in an affordable sample, and the
               tail shows the same failure far sooner. The bench pass count is
               a GUARD. (A proxy - and the ADR says so.)
  call 2 = q   three arms, one variable each:
                 S0  current  temperature 0.0, seed 42, cap 2,048
                 S1  vendor   our temperature override REMOVED -> the Modelfile's
                              0.6 / top_k 20 / top_p 0.95 apply
                 S2  greedy + presence_penalty 1.5
               S3 (thinking off) excluded: it rebuilds the agent - its own ADR.
  call 3 = m   the rule, applied by summarize() below, in this order:
                 1 CONTROL  S2 byte-identical to S0 -> the option was ignored:
                            S2 is VOID, not a loser
                 2 GUARD    mean pass count > 1 case below S0's -> out
                 3 WINNER   lowest tail rate (calls >= 1,024 tokens); tie -> fewer
                            cap hits; tie -> S0 (the status quo keeps Lab 1)
                 4 NULL     S0 has zero tail calls -> the tail is not observable
                            at this size; keep S0 + cap, report the size needed
                 5 REPEAT   repeatability reported, never gated

"Fewest tail calls" is read as a RATE (tail / calls): arms make different numbers
of calls - a looping agent makes more turns - so a raw count would favour the
arm that did less work.

Run (WSL, Ollama up), one arm at a time:
    python src/sampling_probe.py --arm S0 --runs 5
    python src/sampling_probe.py --arm S1 --runs 5
    python src/sampling_probe.py --arm S2 --runs 5
    python src/sampling_probe.py --summarize evals/runs/sampling-*.jsonl
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# AMENDED AFTER ADOPTION (ADR 0009): chat()'s defaults ARE S1p now, so each
# arm spells out what it sent when it was measured, rather than relying on a
# default that has since moved. S0 = greedy, and the Modelfile keys unsent;
# S1 = nothing sampling-related sent at all. Re-running an arm reproduces the
# request it was measured with - checked in tests/test_sampling_seams.py.
_UNSENT = {"top_k": None, "top_p": None, "repeat_penalty": None}
ARMS: dict[str, dict[str, Any] | None] = {
    "S0": {"temperature": 0.0, **_UNSENT},
    "S1": {"temperature": None, **_UNSENT},
    "S2": {"temperature": 0.0, **_UNSENT, "presence_penalty": 1.5},
    # Added AFTER the verdict, for call (v)'s check - it is not a fourth
    # contestant and summarize()'s rule never ranks it. S1 measured "our
    # temperature override removed", i.e. whatever the Modelfile supplied.
    # (v) writes those values into our code instead; this arm proves the
    # written values ARE what was in effect: its outputs must be byte-identical
    # to S1's on every (case, role) pair S1 held stable across all 5 runs.
    "S1p": {"temperature": 0.6, "top_k": 20, "top_p": 0.95, "repeat_penalty": 1.0},
}
TAIL_AT = 1024                       # half of MAX_TOKENS - the tail threshold, fixed in advance
SUITE = REPO_ROOT / "evals" / "suites" / "agent.jsonl"


# --------------------------------------------------------------------------
# measure
# --------------------------------------------------------------------------

def measure(arm: str, runs: int, out_dir: Path) -> Path:
    import model_client
    import run_record
    import score
    import scorers
    from model_client import ModelError
    from suite import parse_suite

    suite = parse_suite(SUITE)
    cases = suite.select("fixed")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"sampling-{arm}-{stamp}.jsonl"
    out_dir.mkdir(parents=True, exist_ok=True)

    where = {"run": None, "case": None, "role": None}

    def write(obj: dict[str, Any]) -> None:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        fh.flush()                   # a probe killed mid-arm keeps what it measured

    def observer(rec: dict[str, Any]) -> None:
        write({"type": "call", "arm": arm, **where, **rec})

    # Role tagging lives HERE, in the probe, not in model_client: the client
    # cannot know whether it is serving an agent turn or a judge call, and
    # teaching it would put this experiment's vocabulary into the boundary.
    def runner(case):
        where["role"] = "agent"
        return scorers.default_agent_runner(case)

    def judge(question, answer, expected, **kw):
        where["role"] = "judge"
        return score.extract_facts(question, answer, expected, **kw)

    name = model_client.DEFAULT_MODEL
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                          capture_output=True, text=True).stdout.strip()
    model_client.SAMPLING_OVERRIDE = ARMS[arm]
    model_client.CALL_OBSERVERS.append(observer)
    try:
        with path.open("w", encoding="utf-8") as fh:
            write({"type": "meta", "arm": arm, "override": ARMS[arm], "runs": runs,
                   "model": name, "model_digest": model_client.model_digest(name),
                   "suite_sha256": run_record.suite_sha256(SUITE), "git_head": head,
                   "tail_at": TAIL_AT, "max_tokens": model_client.MAX_TOKENS,
                   "started_at": stamp})
            scorer, _ = scorers.make_agent_scorer(runner=runner, judge=judge, suite=suite)
            for r in range(1, runs + 1):
                passed = 0
                for case in cases:
                    where.update(run=r, case=case["id"], role=None)
                    try:
                        row = scorer(case)
                        result, note = row.result, row.note
                    except ModelError as exc:
                        # The AGENT hit the cap or timed out. The harness would stop
                        # the run (call B); a probe COUNTS it and carries on, because
                        # the rate of exactly this event is what is being measured.
                        result, note = "agent-error", f"{type(exc).__name__}: {exc}"
                    passed += result == "pass"
                    write({"type": "case", "arm": arm, "run": r, "case": case["id"],
                           "result": result, "note": note})
                print(f"  {arm} run {r}/{runs}: {passed}/{len(cases)} pass", flush=True)
    finally:
        model_client.SAMPLING_OVERRIDE = None
        model_client.CALL_OBSERVERS.remove(observer)
    return path


# --------------------------------------------------------------------------
# summarize - the rule, applied
# --------------------------------------------------------------------------

def pct(values: list[int], p: float) -> int | None:
    """Nearest-rank percentile. None for an empty list - never 0."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def load(paths: list[str]) -> dict[str, dict[str, Any]]:
    arms: dict[str, dict[str, Any]] = {}
    for p in sorted(paths):
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            # A RUN IS (file, run number), NOT the run number alone. Found on the
            # first real use of the pin check (2026-09-27): S1p was invoked twice,
            # both files numbered their runs 1 and 2, and merging by number
            # concatenated two runs' calls into one sequence - [A, B, A, B] - which
            # the check read as "the pin does not reproduce S1". It would also have
            # doubled that arm's per-run pass counts. The file's own stamp is the
            # namespace, so a run from one invocation can never merge with another.
            if "run" in rec:
                rec["run"] = f"{Path(p).stem}#{rec['run']}"
            a = arms.setdefault(rec["arm"], {"meta": [], "calls": [], "cases": []})
            {"meta": a["meta"], "call": a["calls"], "case": a["cases"]}[rec["type"]].append(rec)
    return arms


def stats(arm: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for role in ("agent", "judge", "all"):
        calls = [c for c in arm["calls"] if role == "all" or c["role"] == role]
        toks = [c["completion_tokens"] or 0 for c in calls]
        tail = sum(1 for t in toks if t >= TAIL_AT)
        out[role] = {"calls": len(calls), "p50": pct(toks, 50), "p95": pct(toks, 95),
                     "max": max(toks) if toks else None, "tail": tail,
                     "cap_hits": sum(1 for c in calls if c["done_reason"] == "length"),
                     "tail_rate": (tail / len(calls)) if calls else None}
    runs = sorted({c["run"] for c in arm["cases"]})
    per_run = [sum(1 for c in arm["cases"] if c["run"] == r and c["result"] == "pass")
               for r in runs]
    out["runs"] = len(runs)
    out["pass_per_run"] = per_run
    out["pass_mean"] = (sum(per_run) / len(per_run)) if per_run else None
    by_case: dict[str, set[str]] = {}
    for c in arm["cases"]:
        by_case.setdefault(c["case"], set()).add(c["result"])
    out["repeatable"] = sum(1 for v in by_case.values() if len(v) == 1)
    out["cases"] = len(by_case)
    out["agent_errors"] = sum(1 for c in arm["cases"] if c["result"] == "agent-error")
    out["unscored"] = sum(1 for c in arm["cases"] if c["result"] == "unscored")
    return out


def decide(arms: dict[str, dict[str, Any]]) -> list[str]:
    """Apply call 3's rule in its fixed order. Returns the reasoning, line by line."""
    if "S0" not in arms:
        return ["NO VERDICT: S0 was not measured - every rule is relative to it."]
    st = {a: stats(v) for a, v in arms.items()}
    lines, alive = [], [a for a in ("S0", "S1", "S2") if a in st]

    # 1 CONTROL
    if "S2" in st:
        s0 = {c["output_sha256"] for c in arms["S0"]["calls"]}
        s2 = [c["output_sha256"] for c in arms["S2"]["calls"]]
        same = sum(1 for h in s2 if h in s0)
        lines.append(f"1 CONTROL  {same}/{len(s2)} of S2's outputs are byte-identical to an S0 output")
        if s2 and same == len(s2):
            alive.remove("S2")
            lines.append("           -> S2 VOID: presence_penalty was ignored by the server, not tested")

    # 2 GUARD
    floor = st["S0"]["pass_mean"] - 1
    for a in list(alive):
        ok = st[a]["pass_mean"] >= floor
        lines.append(f"2 GUARD    {a} mean pass {st[a]['pass_mean']:.2f} vs floor {floor:.2f} "
                     f"-> {'in' if ok else 'OUT'}")
        if not ok:
            alive.remove(a)

    # 4 NULL - checked before the winner: a winner over an unobservable tail is noise
    s0 = st["S0"]["all"]
    if s0["tail"] == 0:
        bound = 3 / s0["calls"] if s0["calls"] else float("nan")
        lines.append(f"4 NULL     S0 had 0 tail calls in {s0['calls']}: the tail is not observable "
                     f"at this size (95% upper bound on S0's tail rate ~ {bound:.4f}, rule of three)")
        lines.append("           -> KEEP S0 + the cap. No arm can be shown to beat zero.")
        return lines

    # 3 WINNER
    def key(a):
        return (st[a]["all"]["tail_rate"], st[a]["all"]["cap_hits"], 0 if a == "S0" else 1)
    ranked = sorted(alive, key=key)
    for a in ranked:
        s = st[a]["all"]
        lines.append(f"3 RANK     {a} tail {s['tail']}/{s['calls']} = {s['tail_rate']:.4f}  "
                     f"cap hits {s['cap_hits']}")
    win = ranked[0]
    lines.append(f"           -> WINNER {win}")

    # 5 REPEAT
    for a in ("S0", "S1", "S2"):
        if a in st:
            lines.append(f"5 REPEAT   {a} {st[a]['repeatable']}/{st[a]['cases']} cases gave the same "
                         f"verdict in every run (reported, not gated)")
    return lines


def pin_check(arms: dict[str, dict[str, Any]]) -> list[str]:
    """Call (v)'s check: S1p reproduces every sequence S1 held stable, byte for byte."""
    if "S1" not in arms or "S1p" not in arms:
        return []

    def sequences(calls):
        seq: dict[tuple, dict[int, list[str]]] = {}
        for c in calls:
            seq.setdefault((c["case"], c["role"]), {}).setdefault(c["run"], []).append(c["output_sha256"])
        return seq

    s1, s1p = sequences(arms["S1"]["calls"]), sequences(arms["S1p"]["calls"])
    stable = {k: next(iter(v.values())) for k, v in s1.items()
              if len({tuple(x) for x in v.values()}) == 1}
    held = [k for k, want in stable.items()
            if k in s1p and all(got == want for got in s1p[k].values())]
    lines = [f"PIN CHECK  S1p reproduced {len(held)}/{len(stable)} of S1's stable (case, role) "
             f"sequences byte for byte"]
    missed = sorted(set(stable) - set(held))
    for k in missed:
        lines.append(f"           differs: {k[0]} / {k[1]}")
    lines.append("           -> " + ("ADOPT: the pinned values are what was in effect"
                                    if stable and not missed else
                                    "DO NOT ADOPT: a value in effect under S1 is not the one written down"))
    return lines


def report(paths: list[str]) -> None:
    arms = load(paths)
    for a in ("S0", "S1", "S2", "S1p"):
        if a not in arms:
            continue
        s = stats(arms[a])
        meta = arms[a]["meta"][0]
        print(f"=== {a}  override {meta['override']}  runs {s['runs']}  head {meta['git_head'][:7]}")
        for role in ("agent", "judge", "all"):
            r = s[role]
            print(f"  {role:<6} calls {r['calls']:>4}  p50 {r['p50']}  p95 {r['p95']}  max {r['max']}  "
                  f"tail(>={TAIL_AT}) {r['tail']}  cap hits {r['cap_hits']}")
        print(f"  pass per run {s['pass_per_run']}  mean {s['pass_mean']}  "
              f"agent errors {s['agent_errors']}  unscored {s['unscored']}  "
              f"repeatable {s['repeatable']}/{s['cases']}")
    print("\n=== THE RULE (call 3, fixed before measuring)")
    # S1p is never passed to the rule: it was added after the verdict.
    for line in decide({a: v for a, v in arms.items() if a != "S1p"}):
        print("  " + line)
    check = pin_check(arms)
    if check:
        print("\n=== CALL (v) - the pinned values, checked before adopting")
        for line in check:
            print("  " + line)


# --------------------------------------------------------------------------
# pair - call (v)'s second check, after the first was confounded
# --------------------------------------------------------------------------
#
# WHY THIS EXISTS. The first pin check compared S1p against S1 ACROSS
# SESSIONS, and the data broke its premise: the same pinned values matched S1
# on 17/18 sequences in one invocation and 1/18 ten minutes later. Byte-level
# repeatability holds within a session, not across them - so that check could
# not tell "a wrong value" from "a different server state".
#
# THIS CHECK REMOVES THE CONFOUND instead of arguing with it (Jose: "a wrong
# value would break every pair"). Each round sends ONE fixed request three
# times back to back: unpinned, PINNED, unpinned. If the two unpinned sends
# agree, the server state held steady on both sides of the pinned one, so the
# settings are the only difference it saw. Bar, fixed before running: on every
# such STABLE round the pinned output equals them - 100%. Unstable rounds are
# excluded and counted; zero stable rounds is NO VERDICT, never a pass.

PINNED = ARMS["S1p"]
UNPINNED = ARMS["S1"]


def pair_verdict(rounds: list[tuple[str, str, str]]) -> list[str]:
    """rounds: (unpinned_1, pinned, unpinned_2) output hashes, in send order."""
    stable = [r for r in rounds if r[0] == r[2]]
    held = [r for r in stable if r[1] == r[0]]
    lines = [f"PAIR CHECK {len(rounds)} rounds; {len(stable)} stable (U1 == U2); "
             f"pinned matched on {len(held)}/{len(stable)} stable rounds"]
    if not stable:
        lines.append("           -> NO VERDICT: the server never held still across a round")
    elif len(held) == len(stable):
        lines.append("           -> ADOPT: the pinned values are what the unpinned request runs with")
    else:
        lines.append("           -> DO NOT ADOPT: on a steady server the pinned request answered differently")
    return lines


def capture_first_turns(cases: list[dict[str, Any]]) -> list[tuple[str, list, dict]]:
    """The exact first request agent.run() sends for each case - captured, not rebuilt.

    Rebuilding the prompt here would test OUR COPY of the agent's prompt; a
    drift between the copy and agent.py would pass silently. So the real loop
    runs, and the first request it tries to send is taken off the wire."""
    import agent
    import model_client
    import requests
    import tools

    taken: list[dict] = []
    real_post = model_client.requests.post

    def grab(url, json=None, timeout=None):
        taken.append(json)
        raise requests.exceptions.ConnectionError("captured - not sent")

    out = []
    model_client.requests.post = grab
    try:
        for case in cases:
            taken.clear()
            tools.reset_services()
            try:
                agent.run(case["question"])
            except model_client.ModelError:
                pass
            first = taken[0]
            out.append((case["id"], first["messages"], first.get("format")))
    finally:
        model_client.requests.post = real_post
    return out


def pair(rounds_per_case: int, out_dir: Path) -> Path:
    import model_client
    from suite import parse_suite

    cases = parse_suite(SUITE).select("fixed")
    turns = capture_first_turns(cases)
    seen: list[str] = []
    model_client.CALL_OBSERVERS.append(lambda rec: seen.append(rec["output_sha256"]))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"pair-check-{stamp}.jsonl"
    rounds: list[tuple[str, str, str]] = []
    try:
        with path.open("w", encoding="utf-8") as fh:
            for case_id, messages, schema in turns:
                for n in range(1, rounds_per_case + 1):
                    trio = []
                    for label, override in (("U1", UNPINNED), ("P", PINNED), ("U2", UNPINNED)):
                        model_client.SAMPLING_OVERRIDE = override
                        try:
                            model_client.chat(messages, schema=schema)
                        except model_client.ModelError:
                            pass                       # still observed - see CALL_OBSERVERS
                        trio.append(seen[-1])
                    rounds.append(tuple(trio))
                    fh.write(json.dumps({"case": case_id, "round": n, "U1": trio[0],
                                         "P": trio[1], "U2": trio[2]}) + "\n")
                    fh.flush()
                print(f"  {case_id}: {rounds_per_case} rounds", flush=True)
    finally:
        model_client.SAMPLING_OVERRIDE = None
        model_client.CALL_OBSERVERS.clear()
    for line in pair_verdict(rounds):
        print("  " + line)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", choices=sorted(ARMS))
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", default=str(REPO_ROOT / "evals" / "runs"))
    ap.add_argument("--summarize", nargs="+", metavar="FILE")
    ap.add_argument("--pair", type=int, metavar="ROUNDS",
                    help="call (v)'s paired check: ROUNDS x (unpinned, pinned, unpinned) per case")
    args = ap.parse_args()
    if args.pair:
        print(f"wrote {pair(args.pair, Path(args.out))}")
        return 0
    if args.summarize:
        files = [f for pattern in args.summarize for f in glob.glob(pattern)]
        if not files:
            print("no files matched", file=sys.stderr)
            return 2
        report(files)
        return 0
    if not args.arm:
        ap.error("--arm or --summarize is required")
    print(f"wrote {measure(args.arm, args.runs, Path(args.out))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
