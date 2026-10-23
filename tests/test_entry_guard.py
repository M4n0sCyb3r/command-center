"""A crash in a read-only job exits 2 ("no answer"), never 1 (a finding). Found by the P06 step-6
cold reads; Jose's pick 2026-10-23: catch it in each entry point (src/entry.py).

  case 1  run_guarded: a returned code passes through (0, 1, 2); a crash -> 2 with the job named
          on stderr; SystemExit passes through unchanged (argparse's 2, a deliberate exit)
  case 2  a REAL crash through the real file, as a process: validate.py, review_script.py and
          answer.py are run by `python -c` with their main() replaced by one that raises; each
          must exit 2 - with Python's default it was 1
  case 3  each of the three files ends in run_guarded (a fourth read-only entry point added
          later without it fails here, by name)

NO MODEL, NO MACHINE - CI.   Run:  python tests/test_entry_guard.py
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

import entry                                           # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def code_of(main) -> tuple[int, str]:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            entry.run_guarded(main, "testjob")
        except SystemExit as exc:
            return int(exc.code), err.getvalue()
    return -1, err.getvalue()


print("case 1  run_guarded")
for n in (0, 1, 2):
    c, _ = code_of(lambda n=n: n)
    check(f"a returned {n} passes through", c == n, str(c))


def boom() -> int:
    raise RuntimeError("planted")


c, err = code_of(boom)
check("an unexpected exception -> 2", c == 2, str(c))
check("... and stderr names the job and the crash", "testjob: CRASH - RuntimeError: planted" in err)


def deliberate() -> int:
    sys.exit(2)


c, _ = code_of(deliberate)
check("SystemExit passes through unchanged", c == 2, str(c))

print("\ncase 2  a real crash, through the real file, as a process")
DRIVER = r"""
import runpy, sys
sys.path.insert(0, {src!r})
src = open({path!r}, encoding="utf-8").read()
# replace the entry point's own main with one that raises, then run the file as __main__
g = {{"__name__": "__main__", "__file__": {path!r}}}
code = compile(src.replace("run_guarded(main,", "run_guarded(_boom,").replace("run_guarded(_cli,", "run_guarded(_boom,"), {path!r}, "exec")
def _boom():
    raise RuntimeError("planted crash")
g["_boom"] = _boom
exec(code, g)
"""
for name in ("validate.py", "review_script.py", "answer.py"):
    path = str(SRC / name)
    p = subprocess.run([sys.executable, "-c", DRIVER.format(src=str(SRC), path=path)],
                       capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
                            "OLLAMA_BASE_URL": "http://127.0.0.1:9"})
    check(f"{name}: a crash exits 2", p.returncode == 2,
          f"exit {p.returncode}; {p.stderr.strip().splitlines()[-1] if p.stderr.strip() else ''}"[:200])
    check(f"{name}: and says CRASH", "CRASH - RuntimeError: planted crash" in p.stderr)

print("\ncase 3  every read-only entry point ends in run_guarded")
for name, job in (("validate.py", "validate"), ("review_script.py", "review"), ("answer.py", "ask")):
    tail = (SRC / name).read_text(encoding="utf-8").rstrip().splitlines()[-1]
    check(f"{name} -> run_guarded(..., {job!r})", f'run_guarded(' in tail and f'"{job}"' in tail, tail)

print()
if failures:
    print(f"FAILED: {len(failures)} check(s)")
    sys.exit(1)
print("all entry-guard checks passed")
