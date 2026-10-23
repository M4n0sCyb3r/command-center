#!/usr/bin/env python3
"""Review ONE script with the measured reviewer - the Command Center's `review` job. P06 step 2.

    python src/review_script.py path/to/script.ps1

Project 3's runner only reviews its own planted set: it exists to MEASURE reviewers. This file
is the adapter ADR 0041 named: an operator hands it a script, and it is reviewed with the same
contract Project 3 measured - same rubric, same prompt (review_runner.messages_for), same
schema with its line bounds, same two code-checked rules (review.validate_review), same
budget, same fresh-and-alone model load. Nothing about the review is new; only where the
script comes from is.

WHY qwen3:14b AND NOTHING ELSE (ADR 0043)
-----------------------------------------
One measured model per role. On Project 3's set qwen3:14b alone caught 17/24 (dev) and 10/12
(held-out), the most of any arm, at the lowest cost per catch. There is no --model flag: a
reviewer picked at the command line is a reviewer nobody measured.

EXIT CODES
----------
  0  approve - no blocking finding
  1  revise  - at least one blocking finding; each printed with its lines, category and fix
  2  no review: the file is unreadable, empty or not text; the model is unreachable; or the
     reply broke the contract (schema, line bounds, verdict vs findings). A broken reply is
     NOT an approval - a review we cannot read approves nothing.

A review is advice. Exit 0 means "this reviewer found nothing blocking", not "safe": Project 3
measured what it misses (7/24 on dev). The promote flow (step 3) runs the script on SRV01 and
the QE suite after review for exactly that reason.

The record (evals/runs/*-review-<name>.json) holds the script's sha256, the model, its digest,
the Ollama version that ran it, the raw reply and the parsed review - enough to re-read the
decision next month. Both conditions are needed: on 2026-10-21 the same digest on 0.40.4 and
0.40.7 gave the same verdict in different words.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

RUNS = REPO_ROOT / "evals" / "runs"
EXIT_APPROVE, EXIT_REVISE, EXIT_NO_REVIEW = 0, 1, 2
MAX_BYTES = 200_000            # a review prompt is bounded by num_ctx; refuse early, not mid-call


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_script(path: Path) -> str:
    """The script as text with \\n line endings. ValueError says why it can't be reviewed."""
    if not path.is_file():
        raise ValueError(f"{path} is not a file")
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError(f"{path} is {len(raw)} bytes; the reviewer's context holds {MAX_BYTES}")
    try:
        text = raw.decode("utf-8-sig")            # PowerShell ISE writes a BOM
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} is not UTF-8 text ({exc.reason})") from None
    text = text.replace("\r\n", "\n")
    if not text.strip():
        raise ValueError(f"{path} is empty")
    return text


def main(argv: list[str] | None = None, *, generate: Callable | None = None,
         digest: Callable[[str], str] | None = None, runtime: Callable[[], str] | None = None,
         runs: Path | None = None) -> int:
    ap = argparse.ArgumentParser(description="Review one script with the measured reviewer "
                                             "(qwen3:14b, Project 3's contract).")
    ap.add_argument("script", type=Path)
    args = ap.parse_args(argv)

    # Imported after parsing: a usage error should never depend on the model stack importing.
    import model_client
    import review as R
    import review_runner as RR
    from structured import SchemaError, parse_json

    model = RR.ARMS["escalator"]                  # qwen3:14b - ADR 0043's reviewer
    generate = generate or RR.default_generate
    digest = digest or model_client.model_digest
    runtime = runtime or model_client.server_version
    runs = runs or RUNS

    try:
        text = read_script(args.script)
    except ValueError as exc:
        print(f"review: cannot review: {exc}", file=sys.stderr)
        return EXIT_NO_REVIEW
    n = len(text.rstrip("\n").split("\n"))
    record = {"kind": "review-one-script", "schema": 1, "script": str(args.script),
              "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "lines": n,
              "model": model, "model_digest": None, "ollama_version": None, "started": _now(),
              "verdict": None, "exit_code": None}

    t0 = time.monotonic()
    try:
        record["model_digest"] = digest(model)
        record["ollama_version"] = runtime()
        schema = R.review_schema(n)
        reply = generate(RR.messages_for(args.script.name, text), schema, model)
        record.update(raw=reply.text, prompt_tokens=reply.prompt_tokens,
                      completion_tokens=reply.completion_tokens)
        review = R.validate_review(parse_json(reply.text, schema), n)
    except (model_client.ModelError, SchemaError) as exc:
        record.update(error=f"{type(exc).__name__}: {exc}"[:500], exit_code=EXIT_NO_REVIEW)
        code = EXIT_NO_REVIEW
        print(f"review: no review - {type(exc).__name__}: {exc}", file=sys.stderr)
    else:
        code = EXIT_REVISE if review.blocking else EXIT_APPROVE
        record.update(verdict=review.verdict, exit_code=code,
                      findings=[f.__dict__ for f in review.findings])
        print(f"=== review of {args.script.name} ({n} lines) by {model}: {review.verdict.upper()}")
        lines = text.split("\n")
        for f in sorted(review.findings, key=lambda f: (not f.blocking, f.line_start)):
            span = f"{f.line_start}" if f.line_start == f.line_end else f"{f.line_start}-{f.line_end}"
            print(f"  [{f.severity:<8}] line {span:<7} {f.category}: {f.why}")
            print(f"             | {lines[f.line_start - 1].strip()[:100]}")
            if f.fix:
                print(f"             fix: {f.fix}")
        if not review.findings:
            print("  no findings")
    record["wall_s"] = round(time.monotonic() - t0, 3)
    record["ended"] = _now()
    try:
        runs.mkdir(parents=True, exist_ok=True)
        stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in args.script.name)
        out = runs / f"{record['started'].replace('-', '').replace(':', '')}-review-{stem}.json"
        out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"  record  {out}")
    except OSError as exc:
        print(f"review: WARNING - the record could not be written ({exc})", file=sys.stderr)
    return code


if __name__ == "__main__":
    from entry import run_guarded       # a crash exits 2, never a finding's 1
    run_guarded(main, "review")
