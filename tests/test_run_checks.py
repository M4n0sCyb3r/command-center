"""Build step 8 check: the check runner selects by home, and fails loudly. ADR 0010.

  case 1  the REAL tests dir: ci excludes exactly the declared bench-only files,
          bench runs a strict superset of ci (rule 2: a higher home runs
          everything below it)
  case 2  the exit contract on planted dirs: all pass -> 0, one fails -> 1,
          and 2 for each way the run cannot be established
  case 3  POSITIVE CONTROL on the exclusion: a declared file is excluded in ci,
          run in bench, and its reason is printed

Planted declarations are built by concatenation so that no line of THIS file
starts with the marker - the runner reads files as text, and would otherwise
classify this check as bench-only.

Run:  python tests/test_run_checks.py
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import run_checks  # noqa: E402

MARK = "BENCH" + "_ONLY"
PASS = "import sys\nsys.exit(0)\n"
FAIL = "import sys\nprint('planted failure')\nsys.exit(1)\n"
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def planted(files: dict[str, str], home: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        for name, body in files.items():
            (Path(tmp) / name).write_text(body, encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run_checks.main(["--home", home, "--dir", tmp])
        return code, buf.getvalue()


print("case 1  the real tests dir")
ci, ci_excluded = run_checks.select("ci", TESTS)
bench, bench_excluded = run_checks.select("bench", TESTS)
names = sorted(p.name for p in ci_excluded)
check("ci excludes exactly the declared bench-only files", names == ["test_retrieval_seam.py"],
      str(names))
check("every exclusion carries a reason", all(ci_excluded.values()))
check("bench excludes nothing", not bench_excluded)
check("bench is a STRICT superset of ci", set(ci) < set(bench),
      f"ci={len(ci)} bench={len(bench)}")
check("this file is itself a ci check", TESTS / Path(__file__).name in ci)

print("\ncase 2  the exit contract")
code, _ = planted({"test_a.py": PASS, "test_b.py": PASS}, "ci")
check("all pass -> 0", code == 0, f"exit={code}")
code, out = planted({"test_a.py": PASS, "test_b.py": FAIL}, "ci")
check("one fails -> 1, and it is named", code == 1 and "test_b.py" in out, f"exit={code}")
check("the failing file's own output is shown", "planted failure" in out)
code, out = planted({}, "ci")
check("no check files -> 2 (a scan matching nothing proves nothing)", code == 2, f"exit={code}")
code, out = planted({"test_a.py": f'{MARK} = ""\n' + PASS}, "ci")
check("empty reason -> 2, not a silent exclusion", code == 2 and "empty reason" in out,
      f"exit={code}")
code, out = planted({"test_a.py": f"{MARK} = True\n" + PASS}, "ci")
check("malformed declaration -> 2", code == 2, f"exit={code}")
code, out = planted({"test_a.py": f'{MARK} = ""\n' + PASS}, "bench")
check("empty reason is refused in bench too", code == 2, f"exit={code}")
try:
    with contextlib.redirect_stderr(io.StringIO()):
        run_checks.main([])
    check("no --home -> argparse exit 2", False, "returned")
except SystemExit as exc:
    check("no --home -> exit 2 (no default: the invocation records the intent)",
          exc.code == 2, f"exit={exc.code}")

print("\ncase 3  positive control on the exclusion")
files = {"test_a.py": PASS, "test_model.py": f'{MARK} = "needs a model"\n' + FAIL}
code, out = planted(files, "ci")
check("ci: the declared file is excluded, so the run passes", code == 0, f"exit={code}")
check("ci: the exclusion and its reason are printed",
      "excluded  test_model.py  (bench-only: needs a model)" in out)
code, out = planted(files, "bench")
check("bench: the declared file RUNS (and here, fails)", code == 1 and "test_model.py" in out,
      f"exit={code}")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED")
    sys.exit(1)
print("all checks passed")
