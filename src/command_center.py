#!/usr/bin/env python3
"""The Command Center's front door - one job table, one process per job. P06 build step 1.

Implements ADR 0041 (a fixed job table; the operator names the job; no model routes) and the
pass-through half of ADR 0044 (a single-project job exits with its child's code, unchanged).

    python src/command_center.py <job> [arguments for that job ...]
    python src/command_center.py --list

WHY A TABLE AND NOT A MODEL (ADR 0041)
--------------------------------------
The operator already knows which job they want. A model reading English and picking a route
would add a failure mode and no capability - Lab 3's rule ("the model proposes, the code
disposes") applied one level up. `JOBS` is the only list of routes, read by the argument check
AND the dispatcher, so the two cannot disagree (Lab 3's registry). A name not in it is refused
by argparse with exit 2 before anything runs (Lab 11's gate 0).

The table holds only routes that RUN today (Jose's pick, build step 1). Step 2 added `validate`
(src/validate.py, P1's read with --expect) and pointed `review` at src/review_script.py - one
operator script, qwen3:14b - instead of Project 3's measurement runner, which reviews only its
own planted set and stays a measurement tool, not a job. Step 3 added `promote`
(src/promote.py, ADR 0042's flow behind one Bench seam): proved on fakes in CI by the
`command-center` suite; its real SRV01 bench (src/srv01_bench.py) was wired in step 4.

WHY A PROCESS AND NOT AN IMPORT
-------------------------------
Each project already speaks an exit-code contract; Project 5's 3 means "restore the
checkpoint". Imported, a child's sys.exit(3) is an exception we would have to catch and
translate - and translation is where meanings blur. Run as a process, the code is observed
exactly as the project defined it (why Project 4 put exit-code checks in tests/). The child
INHERITS this terminal - stdin, stdout, stderr - so Project 5's approval screen and validate's
password prompt still talk to the person at the keyboard. Nothing is captured, so nothing can
be swallowed. It also inherits the working directory, so `review ./fix.ps1` means the file
where the operator is standing; every entry point anchors its own paths on its own file.

MODEL DIGESTS (step 2; Jose's pick: no digest, no run)
------------------------------------------------------
Each row names the models its job uses. Before the child starts, each model's digest is read
from Ollama and written into the record - the exact build that did the work, not just its tag
(ADR 0007: a record that cannot say what produced it is not evidence). If a digest cannot be
read, the job is refused with 2 and the record says so (state "not-started"): the job needs
that model anyway, and a placeholder where provenance belongs looks like evidence and is not.
The names in the table are checked in CI against the constants the entry points really use.

The Ollama server's VERSION is recorded beside the digests, under the same rule. Found on the
bench the night step 2 ran: identical digest, identical prompt, 0.40.4 -> 0.40.7, same verdict,
different reply. A digest pins the weights, not the output.

THE RUN RECORD
--------------
One JSON file per run in evals/runs/ (a reading, gitignored - INDEX.md's role rule). It is
written TWICE: once BEFORE the child starts (state "started") and once after. If this process
dies mid-run, the file on disk still says a job began and never reported back - the same
reason Project 5 writes its intent line before it applies anything. If the first write fails,
nothing runs: no record, no run.

The record adds no verdict of its own (ADR 0041). It holds the job, the command, both clocks
(wall time for "when", a monotonic clock for "how long" - Lab 12), the child's code, and
`evidence`: files that appeared under the runs directory while the child ran - the child's own
records and audit logs, found without parsing its output.

EXIT CODES (ADR 0044)
---------------------
  child's code   the job ran and exited normally - passed through UNCHANGED (0, 1, 2 or 3)
  2              the job never ran: unknown job, bad arguments, record not writable, a
                 model digest could not be read, child could not be started
  2 or 3         the child was KILLED by a signal (Ctrl+C included) and so returned no code
                 of its own. A read-only job -> 2: nothing can have changed. A job that writes
                 -> 3: this process cannot see how far the child got, and "maybe half-changed"
                 must never read as "nothing changed" (Project 5's rule, ADR 0039).

A 2 from the child and a 2 from here look the same on the shell; the record says whose it is
(`state` and `child_returncode`). The README's exit-code table (step 6) carries every job.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "evals" / "runs"

EXIT_NOT_RUN = 2
EXIT_UNDESCRIBED = 3


@dataclass(frozen=True)
class Job:
    project: str
    entry: str          # path relative to the repo root; run with this interpreter
    writes: bool        # can this job change a machine? decides a killed child's exit code
    purpose: str
    models: tuple[str, ...] = ()     # every Ollama model the job uses; digests recorded per run


JOBS: dict[str, Job] = {
    "validate":  Job("P1", "src/validate.py", False,
                     "read a server's audit policy via JEA; check --expect SUBCATEGORY=SETTING"),
    "ask":       Job("P2", "src/answer.py", False,
                     "answer one question from the runbook vault, with citations",
                     ("qwen3:8b", "nomic-embed-text")),
    "review":    Job("P3", "src/review_script.py", False,
                     "review one script with the measured reviewer; 0 approve, 1 revise",
                     ("qwen3:14b",)),
    "remediate": Job("P5", "src/remediation_agent.py", True,
                     "detect drift, propose a fix, ask a human, apply, verify (SRV01 or fake)",
                     ("qwen3:8b",)),
    "promote":   Job("P6", "src/promote.py", True,
                     "classify, review, QE, dry run, checkpoint, live run on SRV01; promoted/ "
                     "or restore", ("qwen3:14b",)),
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path: Path, record: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)               # a reader never sees half a record


def _files(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()} if root.is_dir() else set()


def _killed_exit(job: Job) -> int:
    return EXIT_UNDESCRIBED if job.writes else EXIT_NOT_RUN


def _ollama_digest(model: str) -> str:
    sys.path.insert(0, str(REPO / "src"))
    import model_client                               # lazy: jobs without a model never need it
    try:
        return model_client.model_digest(model)
    except model_client.ModelError as exc:
        raise LookupError(str(exc)) from exc


def _ollama_version() -> str:
    sys.path.insert(0, str(REPO / "src"))
    import model_client
    try:
        return model_client.server_version()
    except model_client.ModelError as exc:
        raise LookupError(str(exc)) from exc


def run_job(name: str, job_args: list[str], *, jobs: dict[str, Job] | None = None,
            runs: Path | None = None, repo: Path | None = None,
            digest: Callable[[str], str] | None = None,
            runtime: Callable[[], str] | None = None) -> int:
    """Run one job as a child process; return the exit code this process should end with.
    `digest(model)` and `runtime()` raise LookupError when they cannot read (tests inject them)."""
    jobs, runs, repo, digest = jobs or JOBS, runs or RUNS, repo or REPO, digest or _ollama_digest
    runtime = runtime or _ollama_version
    job = jobs[name]                                  # the CLI has already refused unknowns
    command = [sys.executable, str(repo / job.entry), *job_args]
    record: dict = {
        "kind": "command-center-run", "schema": 1,
        "job": name, "project": job.project, "entry": job.entry, "writes": job.writes,
        "args": job_args, "command": command,
        "state": "started", "started": _now(), "ended": None, "duration_s": None,
        "child_returncode": None, "exit_code": None, "evidence": [],
        "models": {m: None for m in job.models},
        "ollama_version": None,
    }
    if os.environ.get("LOCAL_MODEL"):                 # answer.py honours it; say so, don't hide it
        record["env_LOCAL_MODEL"] = os.environ["LOCAL_MODEL"]
    try:
        runs.mkdir(parents=True, exist_ok=True)
        path = runs / f"{_now().replace('-', '').replace(':', '')}-cc-{name}-{secrets.token_hex(3)}.json"
    except OSError as exc:
        print(f"command_center: cannot write the run record ({exc}). Nothing was run.",
              file=sys.stderr)
        return EXIT_NOT_RUN

    m = "the Ollama server"
    try:
        for m in job.models:
            record["models"][m] = digest(m)
        if job.models:
            m = "the Ollama server"
            record["ollama_version"] = runtime()
    except (LookupError, OSError) as exc:
        record.update(state="not-started", exit_code=EXIT_NOT_RUN, ended=_now(),
                      error=f"no digest for {m}: {exc}")
        try:
            _write(path, record)
        except OSError:
            pass
        print(f"command_center: cannot read the digest of {m} ({exc}). No digest, no run - "
              f"is `ollama serve` running? Nothing was run.", file=sys.stderr)
        return EXIT_NOT_RUN

    try:
        _write(path, record)
    except OSError as exc:
        print(f"command_center: cannot write the run record ({exc}). Nothing was run.",
              file=sys.stderr)
        return EXIT_NOT_RUN

    before = _files(runs)
    t0 = time.monotonic()
    print(f"command_center: {name} -> {job.project} {job.entry}   record {path.name}",
          file=sys.stderr)
    try:
        child = subprocess.Popen(command)              # inherits terminal AND working dir
    except OSError as exc:
        record.update(state="not-started", exit_code=EXIT_NOT_RUN, ended=_now(),
                      error=f"{type(exc).__name__}: {exc}")
        _write(path, record)
        print(f"command_center: could not start {job.entry}: {exc}. Nothing was run.",
              file=sys.stderr)
        return EXIT_NOT_RUN

    while True:
        try:
            rc = child.wait()
            break
        except KeyboardInterrupt:
            # Ctrl+C reaches the child too (same terminal); IT decides what that means.
            # We keep waiting so the record holds what the child actually did.
            continue

    record["duration_s"] = round(time.monotonic() - t0, 3)
    record["ended"] = _now()
    record["child_returncode"] = rc
    if rc < 0:                                         # POSIX: killed by signal -rc
        sig = signal.Signals(-rc).name if -rc in signal.Signals._value2member_map_ else str(-rc)
        record.update(state="killed", signal=sig, exit_code=_killed_exit(job))
    else:
        record.update(state="finished", exit_code=rc)
    record["evidence"] = sorted(str(p.relative_to(runs)) for p in _files(runs) - before
                                if p != path and p.suffix != ".tmp")
    try:
        _write(path, record)
    except OSError as exc:
        # The child ran; its code is still the truth. The "started" record stays on disk.
        print(f"command_center: WARNING - the run finished but its record could not be "
              f"updated ({exc}); {path.name} still says 'started'.", file=sys.stderr)
    if record["state"] == "killed":
        print(f"command_center: {name} was killed by {record['signal']} and returned no code. "
              + ("It can write - its state is UNDESCRIBED: check, and restore the checkpoint "
                 "if needed." if job.writes else "It is read-only - nothing changed."),
              file=sys.stderr)
    return record["exit_code"]


def build_parser(jobs: dict[str, Job]) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="command_center",
        description="Run one job of the Command Center. Arguments after the job name go to "
                    "that job's own entry point, unchanged.")
    ap.add_argument("--list", action="store_true", help="print the job table and exit")
    ap.add_argument("--runs", type=Path, default=None, help=argparse.SUPPRESS)  # tests only
    ap.add_argument("job", nargs="?", choices=sorted(jobs))
    ap.add_argument("job_args", nargs=argparse.REMAINDER)
    return ap


def main(argv: list[str] | None = None, *, jobs: dict[str, Job] | None = None,
         repo: Path | None = None, digest: Callable[[str], str] | None = None,
         runtime: Callable[[], str] | None = None) -> int:
    jobs = jobs or JOBS
    ap = build_parser(jobs)
    args = ap.parse_args(argv)                        # unknown job -> argparse exits 2
    if args.list:
        for name, j in sorted(jobs.items()):
            print(f"  {name:<10} {j.project}  {'writes   ' if j.writes else 'read-only'}  "
                  f"{j.entry:<26} {', '.join(j.models) or 'no model':<28} {j.purpose}")
        return 0
    if args.job is None:
        ap.error("name a job (or --list)")            # exits 2
    return run_job(args.job, args.job_args, jobs=jobs, runs=args.runs, repo=repo, digest=digest,
                   runtime=runtime)


if __name__ == "__main__":
    sys.exit(main())
