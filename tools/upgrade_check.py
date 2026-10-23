#!/usr/bin/env python3
"""After Ollama updates itself: prove nothing moved, then move the pin.

bench_check.py fails when the server is not the version in bench-pin.json. This
is the one command that settles it. It runs the three checks done by hand on
2026-10-08 and reads them for you:

  1. python src/retrieval.py         rebuild the index under THIS server; Lab 6 gate
  2. tools/probe_embed_context.py    the server still refuses to truncate (ADR 0024)
  3. src/eval_harness.py --tier fixed  the retrieval suite vs its committed baseline

CLEAN means: the gate passed, 0 chunks would be truncated, and every case in the
baseline has the same result, the same rank and the same top score (within
SCORE_TOLERANCE) - rank 2-3 reshuffles are ignored, nothing grades on them. Only
then is bench-pin.json rewritten, and you commit it.

Why retrieval is the yardstick: embeddings are deterministic, so "identical" is a
claim that can be checked. Generation is not repeatable across server sessions
under any setting (ADR 0009), so a qwen3 comparison here would cry wolf.

Why it never touches the baseline: if something MOVED, deciding whether the new
reading is the new truth is --rebaseline's job and a person's call (ADR 0008).
A pin that advanced on a moved reading is the moving answer key again.

EXIT  0 clean, pin now matches the server
      1 something moved - the pin is unchanged, read the report
      2 a check could not run (server down, crash, harness exit 2)

Run from the repo root, in WSL:   python tools/upgrade_check.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

SUITE = "evals/suites/retrieval.jsonl"
BASELINE = REPO / "evals" / "baselines" / "retrieval.json"
SCORE_TOLERANCE = 1e-6


def compare(baseline_cases: list[dict], run_cases: list[dict]) -> list[str]:
    """Every way the run differs from the baseline that matters. [] means identical."""
    now = {c["id"]: c for c in run_cases}
    moved: list[str] = []
    for b in baseline_cases:
        cid = b["id"]
        r = now.get(cid)
        if r is None:
            moved.append(f"{cid}: missing from this run")
            continue
        if r.get("result") != b.get("result"):
            moved.append(f"{cid}: result {b.get('result')} -> {r.get('result')}")
        bd, rd = b.get("detail") or {}, r.get("detail") or {}
        if rd.get("rank") != bd.get("rank"):
            moved.append(f"{cid}: rank {bd.get('rank')} -> {rd.get('rank')}")
        bs, rs = bd.get("top_score"), rd.get("top_score")
        if not isinstance(bs, (int, float)) or not isinstance(rs, (int, float)):
            moved.append(f"{cid}: top_score not comparable ({bs!r} vs {rs!r})")
        elif abs(rs - bs) > SCORE_TOLERANCE:
            moved.append(f"{cid}: top_score {bs:.6f} -> {rs:.6f}")
    for cid in now.keys() - {b["id"] for b in baseline_cases}:
        moved.append(f"{cid}: new in this run, not in the baseline")
    return moved


def truncated_count(probe_output: str) -> int | None:
    """The probe always exits 0, so its verdict is read from its last line."""
    m = re.search(r"(\d+) would be truncated", probe_output)
    return int(m.group(1)) if m else None


def record_path(harness_output: str) -> Path | None:
    m = re.search(r"^\s*record\s+(\S+)\s*$", harness_output, re.MULTILINE)
    return REPO / m.group(1) if m else None


def write_pin(path: Path, version: str, summary: str) -> None:
    pin = {
        "ollama_version": version,
        "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verified_by": f"tools/upgrade_check.py: {summary}",
        "note": "Moved only by tools/upgrade_check.py after a clean re-measurement. "
                "See src/bench_check.py check C.",
        # ADR 0026 amendment 12: 0.40.3 -> 0.40.4 left retrieval identical and moved two judge
        # readings. Say what the pin covers, so nobody reads it as more.
        "covers": "retrieval (embeddings) only - generation (the answer step, the judge) is NOT "
                  "re-verified; compare readings across versions with the version in view",
    }
    path.write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8")


def step(label: str, cmd: list[str]) -> subprocess.CompletedProcess:
    print(f"--- {label}: {' '.join(cmd)}")
    proc = subprocess.run([sys.executable, *cmd], cwd=REPO, capture_output=True, text=True)
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-4:]
    for line in tail:
        print(f"    | {line}")
    print(f"    exit {proc.returncode}")
    return proc


def main() -> int:
    import bench_check
    from model_client import base_url

    try:
        version = bench_check.check_reachable(base_url())
    except Exception as exc:
        print(f"CANNOT RUN (exit 2): Ollama not reachable: {exc}")
        return 2
    pinned = bench_check.load_pin()
    print(f"server {version}   pinned {pinned}\n")

    gate = step("1 rebuild index + Lab 6 gate", ["src/retrieval.py"])
    if gate.returncode not in (0, 1):
        print("CANNOT RUN (exit 2): retrieval.py crashed")
        return 2

    probe = step("2 truncation probe", ["tools/probe_embed_context.py"])
    cut = truncated_count(probe.stdout)
    if probe.returncode != 0 or cut is None:
        print("CANNOT RUN (exit 2): the probe did not report a truncation count")
        return 2

    harness = step("3 retrieval suite vs baseline",
                   ["src/eval_harness.py", "--suite", SUITE, "--tier", "fixed"])
    record = record_path(harness.stdout)
    if harness.returncode == 2 or record is None or not record.exists():
        print("CANNOT RUN (exit 2): the harness did not produce a run record")
        return 2

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    run = json.loads(record.read_text(encoding="utf-8"))
    moved = compare(baseline["cases"], run["cases"])

    problems: list[str] = []
    if gate.returncode != 0:
        problems.append("Lab 6 gate FAILED (step 1)")
    if cut:
        problems.append(f"{cut} chunk(s) would be truncated (step 2)")
    problems += moved

    n = len(baseline["cases"])
    print()
    if problems:
        print(f"MOVED - the pin stays at {pinned}. {len(problems)} difference(s):")
        for p in problems:
            print(f"  - {p}")
        print("Something on the bench changed what retrieval measures. Log it before")
        print("building on it; re-pinning the baseline is a --rebaseline decision.")
        return 1

    summary = (f"gate PASS, 0 truncated, all {n} retrieval cases identical "
               f"(result, rank, top score) to evals/baselines/retrieval.json")
    write_pin(bench_check.PIN_PATH, version, summary)
    print(f"CLEAN - {summary}.")
    print(f"bench-pin.json now says {version}. Commit it:")
    print(f'  git add bench-pin.json && git commit -m "Bench: verified Ollama {version}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
