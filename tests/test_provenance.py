"""The report's BASELINE commit line tells the truth about the file ON DISK.

FOUND LIVE, 2026-09-28 (build step 7). git_provenance() asked "which commit
last touched this path?" - and a deletion touches it. After cc4efec retired the
v6 baselines and fresh ones were promoted, the report printed `commit cc4efec`
for files git had never seen. ADR 0008 4b's owed line ("a stale pin must fail
loudly") failed silently in exactly the migration case.

  case 1  never tracked                   -> NOT COMMITTED
  case 2  committed, deleted, re-created  -> NOT COMMITTED   (the live case)
  case 3  committed, then edited          -> MODIFIED since <sha>
  case 4  POSITIVE CONTROL: committed and clean -> "<sha> <date>", the real sha

Each in a throwaway git repo - never this one (see test_isolation.py).
Run:  python tests/test_provenance.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import report                                       # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def repo() -> Path:
    d = Path(tempfile.mkdtemp())
    git(d, "init", "-q")
    return d


def git(d: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(d), "-c", "user.email=t@example.invalid",
                           "-c", "user.name=t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def commit(d: Path, msg: str) -> str:
    git(d, "add", "-A"); git(d, "commit", "-q", "-m", msg)
    return git(d, "rev-parse", "--short", "HEAD")


print("case 1  never tracked")
d = repo(); (d / "keep.txt").write_text("x"); commit(d, "init")
(d / "agent.json").write_text("{}")
got = report.git_provenance(d / "agent.json")
check("NOT COMMITTED", got.startswith("NOT COMMITTED"), got)

print("\ncase 2  committed, deleted, re-created (the live case)")
d = repo(); (d / "agent.json").write_text('{"v": 6}'); commit(d, "v6 baseline")
(d / "agent.json").unlink(); deleted = commit(d, "retire v6")
(d / "agent.json").write_text('{"v": 7}')
got = report.git_provenance(d / "agent.json")
check("NOT COMMITTED - not the deletion commit", got.startswith("NOT COMMITTED"), got)
check(f"and never names the deleting commit {deleted}", deleted not in got)

print("\ncase 3  committed, then edited (a re-promote before commit)")
d = repo(); (d / "agent.json").write_text('{"v": 1}'); first = commit(d, "baseline")
(d / "agent.json").write_text('{"v": 2}')
got = report.git_provenance(d / "agent.json")
check(f"MODIFIED since {first}", got.startswith(f"MODIFIED since {first}"), got)

print("\ncase 4  POSITIVE CONTROL: committed and clean")
d = repo(); (d / "agent.json").write_text('{"v": 1}'); sha = commit(d, "baseline")
got = report.git_provenance(d / "agent.json")
check(f"'{sha} <date>'", got.startswith(sha + " ") and "T" in got, got)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED")
    sys.exit(1)
print("all checks passed")
