#!/usr/bin/env python3
"""Run every check file for one HOME. Build step 8, ADR 0010.

THE THREE HOMES (ADR 0010)
--------------------------
  ci     a hosted runner: reaches the repo only
  bench  <host> / WSL: the repo AND the model
  (lab)  bench + the VMs: that is the harness's `--tier live`, not this file

A check runs in the LOWEST home that reaches everything it needs, and every
higher home runs everything below it FIRST. So `--home bench` is a superset of
`--home ci`, and the bench's first command is the one CI runs - which is why
this is not the second entry point ADR 0008 rejected: it cannot become "the one
nobody runs locally".

HOW A FILE DECLARES ITS HOME
----------------------------
Every `tests/test_*.py` is a CI check unless it declares, at column 0:

    BENCH_ONLY = "one line saying what it needs that CI does not have"

Exclude-by-declaration, NOT an allow-list: a new test nobody listed would never
run in CI and nothing would say so (ADR 0006's draining tier, one level down).
This way a new test that secretly needs the model goes RED in CI - loud. The
declaration is read as TEXT, never imported: deciding whether to run a file must
not run it. An empty reason is a runner error, not an exclusion - the reason is
the only thing standing between the escape hatch and abuse of it.

EXIT CODES - the repo's contract, applied here
----------------------------------------------
  0  every selected check file exited 0
  1  at least one check file failed
  2  the run could not be established: no --home, no check files found, a
     bench-only declaration with no reason, a malformed declaration

`--home` has no default, for the reason `--tier` has none: the invocation
records the intent.

Run:  python tests/run_checks.py --home ci
      python tests/run_checks.py --home bench
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent

DECLARED = re.compile(r'^BENCH_ONLY\s*=\s*"(?P<reason>[^"]*)"\s*$', re.MULTILINE)
MENTIONED = re.compile(r"^BENCH_ONLY\b", re.MULTILINE)


class RunnerError(Exception):
    """The run cannot be established -> exit 2."""


def classify(files: list[Path]) -> tuple[list[Path], dict[Path, str]]:
    """Split check files into (ci, bench_only -> reason). Reads text only."""
    ci: list[Path] = []
    bench: dict[Path, str] = {}
    for f in files:
        src = f.read_text(encoding="utf-8")
        m = DECLARED.search(src)
        if m is None:
            if MENTIONED.search(src):
                raise RunnerError(f"{f.name}: BENCH_ONLY is set but not as one quoted line")
            ci.append(f)
            continue
        reason = m.group("reason").strip()
        if not reason:
            raise RunnerError(f"{f.name}: BENCH_ONLY has an empty reason")
        bench[f] = reason
    return ci, bench


def select(home: str, tests_dir: Path) -> tuple[list[Path], dict[Path, str]]:
    """Return (files to run, files excluded -> reason) for a home."""
    files = sorted(tests_dir.glob("test_*.py"))
    if not files:
        # A scan that matches nothing proves nothing - and would exit 0.
        raise RunnerError(f"no test_*.py files found in {tests_dir}")
    ci, bench = classify(files)
    if home == "ci":
        return ci, bench
    return sorted(ci + list(bench)), {}


def run(home: str, tests_dir: Path) -> int:
    selected, excluded = select(home, tests_dir)
    print(f"HOME {home}: {len(selected)} check file(s) selected, {len(excluded)} excluded")
    for f, reason in excluded.items():
        print(f"  excluded  {f.name}  (bench-only: {reason})")
    print()

    failed: list[str] = []
    for f in selected:
        proc = subprocess.run([sys.executable, str(f)], cwd=REPO,
                              capture_output=True, text=True)
        status = "ok  " if proc.returncode == 0 else "FAIL"
        print(f"  {status} {f.name}  exit={proc.returncode}")
        if proc.returncode != 0:
            failed.append(f.name)
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-15:]
            for line in tail:
                print(f"         | {line}")

    print()
    if failed:
        print(f"{len(failed)} of {len(selected)} check file(s) FAILED: {', '.join(failed)}")
        return 1
    print(f"all {len(selected)} check file(s) passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", required=True, choices=["ci", "bench"])
    ap.add_argument("--dir", type=Path, default=TESTS, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        return run(args.home, args.dir)
    except RunnerError as exc:
        print(f"RUNNER ERROR (exit 2): {exc}")
        return 2
    except Exception as exc:  # a crash is not a failed check (step 3 part 2b)
        print(f"RUNNER ERROR (exit 2): {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
