"""P06 build steps 1-2 check: the job table, the refusal, pass-through, the run record, digests. ADRs 0041, 0043, 0044.

  case 1  the REAL table: exactly the routes that run today, every entry point exists, the
          CLI's choices come from the same dict (one source - Lab 3's registry), and every
          model it names is the constant the entry point really uses (step 2)
  case 2  an unknown job is refused with 2 and NOTHING runs - not even a record
  case 3  POSITIVE CONTROL through real projects: `validate --server bogus` reaches the child,
          whose argparse exits 2 - the record shows the 2 is the CHILD's (state finished); and
          `review` with Ollama unreachable is refused BEFORE the child runs (no digest, no run)
  case 4  pass-through: planted children exiting 0/1/2/3 come back unchanged, observed as a
          PROCESS exit code - the thing an operator's shell actually sees
  case 5  arguments reach the child verbatim (including ones that look like our own flags),
          the child reads THIS process's stdin (Project 5's approval screen needs it), and runs
          in the operator's working directory (so `review ./x.ps1` means what it says)
  case 6  the record: written before the child starts, finished after; both clocks; evidence =
          files the child created under the runs dir, never the record itself
  case 7  a child killed by a signal: read-only job -> 2, writing job -> 3; state "killed"
  case 8  a child that cannot be started -> 2, state "not-started"
  case 9  digests and the Ollama version: read BEFORE the child starts and present in the
          record it sees; either unreadable -> 2, state "not-started", the child never runs;
          a job with no model asks Ollama nothing

No model, no lab: everything here runs in CI.

Run:  python tests/test_command_center.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

import command_center as cc  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def records(runs: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(runs.glob("*-cc-*.json"))]


# A planted job table is driven through a REAL process: a tiny launcher imports the module,
# swaps in the planted table, and exits with main()'s return - so the exit code is observed
# exactly as a shell sees it, not as a Python return value.
LAUNCHER = """
import json, sys
from pathlib import Path
sys.path.insert(0, {src!r})
import command_center as cc
jobs = {{k: cc.Job(**v) for k, v in json.loads({jobs!r}).items()}}
sys.exit(cc.main(sys.argv[1:], jobs=jobs, repo=Path({repo!r})))
"""


def planted(tmp: Path, scripts: dict[str, tuple[str, bool]]) -> Path:
    """Write planted entry points into tmp/src/; return the launcher path."""
    (tmp / "src").mkdir(exist_ok=True)
    jobs = {}
    for name, (body, writes) in scripts.items():
        (tmp / "src" / f"{name}.py").write_text(body, encoding="utf-8")
        jobs[name] = {"project": "PX", "entry": f"src/{name}.py", "writes": writes,
                      "purpose": "planted"}
    launcher = tmp / "launch.py"
    launcher.write_text(LAUNCHER.format(src=str(SRC), jobs=json.dumps(jobs), repo=str(tmp)),
                        encoding="utf-8")
    return launcher


def launch(launcher: Path, runs: Path, *argv: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(launcher), "--runs", str(runs), *argv],
                          input=stdin, capture_output=True, text=True, timeout=60)


print("case 1  the real job table")
check("routes are exactly the ones built so far",
      sorted(cc.JOBS) == ["ask", "promote", "remediate", "review", "validate"], str(sorted(cc.JOBS)))
missing = [j.entry for j in cc.JOBS.values() if not (REPO / j.entry).is_file()]
check("every entry point exists", not missing, str(missing))
choices = next(a.choices for a in cc.build_parser(cc.JOBS)._actions if a.dest == "job")
check("the CLI's choices ARE the table", sorted(choices) == sorted(cc.JOBS))
check("only remediate and promote are marked as writing",   # promote: P06 step 3
      sorted(n for n, j in cc.JOBS.items() if j.writes) == ["promote", "remediate"])
import model_client, promote, proposer, retrieval, review_runner  # noqa: E401,E402
truth = {"validate": (), "ask": (model_client.DEFAULT_MODEL, retrieval.EMBED_MODEL),
         "review": (review_runner.ARMS["escalator"],), "remediate": (proposer.MODEL,),
         "promote": (promote.REVIEWER,)}
check("every model named is the one the entry point uses",
      {n: j.models for n, j in cc.JOBS.items()} == truth,
      str({n: j.models for n, j in cc.JOBS.items()}))
check("review is the one-script adapter, not P3's measurement runner",
      cc.JOBS["review"].entry == "src/review_script.py" and truth["review"] == ("qwen3:14b",))
check("promote's reviewer IS the review job's model (one measured model per role, ADR 0043)",
      promote.REVIEWER == truth["review"][0])

with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    runs = tmp / "runs"

    print("case 2  an unknown job is refused before anything runs")
    p = subprocess.run([sys.executable, str(SRC / "command_center.py"), "--runs", str(runs),
                        "frobnicate"], capture_output=True, text=True, timeout=60)
    check("exit 2", p.returncode == 2, f"exit={p.returncode}")
    check("no record written, no runs dir created", not runs.exists())
    p = subprocess.run([sys.executable, str(SRC / "command_center.py"), "--runs", str(runs)],
                       capture_output=True, text=True, timeout=60)
    check("no job named -> exit 2", p.returncode == 2 and not runs.exists(), f"exit={p.returncode}")

    print("case 3  positive control through real projects")
    p = subprocess.run([sys.executable, str(SRC / "command_center.py"), "--runs", str(runs),
                        "validate", "--server", "bogus"], capture_output=True, text=True, timeout=60)
    recs = records(runs)
    check("exit 2 passed through from the child", p.returncode == 2, f"exit={p.returncode}")
    check("the child's own usage message reached the terminal", "validate.py" in p.stderr)
    check("one record, state finished, child_returncode 2",
          len(recs) == 1 and recs[0]["state"] == "finished" and recs[0]["child_returncode"] == 2,
          str([(r["state"], r["child_returncode"]) for r in recs]))
    r3 = tmp / "runs3b"
    script = tmp / "x.ps1"
    script.write_text("Get-Service\n", encoding="utf-8")
    env = {**os.environ, "OLLAMA_BASE_URL": "http://127.0.0.1:9"}     # pinned unreachable
    p = subprocess.run([sys.executable, str(SRC / "command_center.py"), "--runs", str(r3),
                        "review", str(script)], capture_output=True, text=True, timeout=120, env=env)
    rec = records(r3)
    check("review with Ollama unreachable -> 2", p.returncode == 2, f"exit={p.returncode}")
    check("refused before the child: state not-started, no child return code, error names the model",
          len(rec) == 1 and rec[0]["state"] == "not-started" and rec[0]["child_returncode"] is None
          and "qwen3:14b" in rec[0].get("error", ""), str(rec and rec[0].get("error")))
    check("the child never ran (no review record)", not list(r3.glob("*-review-x.ps1.json")))

    print("case 4  pass-through of 0/1/2/3, observed as a process exit code")
    body = "import sys\nsys.exit({code})\n"
    launcher = planted(tmp, {f"exit{c}": (body.format(code=c), False) for c in range(4)})
    for c in range(4):
        r = tmp / f"runs4-{c}"
        p = launch(launcher, r, f"exit{c}")
        rec = records(r)
        check(f"child {c} -> {c}", p.returncode == c and rec[0]["exit_code"] == c
              and rec[0]["child_returncode"] == c, f"exit={p.returncode}")

    print("case 5  arguments verbatim; stdin inherited")
    echo = ("import json, os, sys\n"
            "line = sys.stdin.readline().strip()\n"
            "print(json.dumps({'argv': sys.argv[1:], 'stdin': line, 'cwd': os.getcwd()}))\n")
    launcher = planted(tmp, {"echo": (echo, False)})
    argv = ["--list", "--runs", "x y", "-h", "--target=srv01"]
    here = tmp / "operator-is-here"
    here.mkdir()
    p = subprocess.run([sys.executable, str(launcher), "--runs", str(tmp / "runs5"), "echo", *argv],
                       input="approve\n", capture_output=True, text=True, timeout=60, cwd=here)
    try:
        got = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        got = {}
    check("every argument after the job reached the child unchanged", got.get("argv") == argv,
          str(got.get("argv")))
    check("the child read this process's stdin", got.get("stdin") == "approve")
    check("the child runs where the operator is standing",
          Path(got.get("cwd", "")).resolve() == here.resolve(), str(got.get("cwd")))
    check("exit 0", p.returncode == 0, f"exit={p.returncode}")

    print("case 6  the run record")
    probe = ("import json, sys, pathlib\n"
             "runs = pathlib.Path(sys.argv[1])\n"
             "started = [json.loads(p.read_text()) for p in runs.glob('*-cc-*.json')]\n"
             "(runs / 'child-evidence.json').write_text(json.dumps(started))\n"
             "sys.exit(1)\n")
    launcher = planted(tmp, {"probe": (probe, False)})
    r6 = tmp / "runs6"
    p = launch(launcher, r6, "probe", str(r6))
    seen = json.loads((r6 / "child-evidence.json").read_text())
    rec = records(r6)[0]
    check("the record existed BEFORE the child ran, state started",
          len(seen) == 1 and seen[0]["state"] == "started" and seen[0]["exit_code"] is None)
    check("after: state finished, exit 1, both clocks",
          rec["state"] == "finished" and rec["exit_code"] == 1 and rec["ended"]
          and isinstance(rec["duration_s"], float))
    check("evidence lists the child's file and not the record itself",
          rec["evidence"] == ["child-evidence.json"], str(rec["evidence"]))
    check("the record names the job, its project and the exact command",
          rec["job"] == "probe" and rec["project"] == "PX"
          and rec["command"][1:] == [str(tmp / "src" / "probe.py"), str(r6)], str(rec["command"][1:]))
    check("no stray temp files left", not list(r6.glob("*.tmp")))

    print("case 7  a child killed by a signal")
    if os.name == "posix":
        kill = "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n"
        launcher = planted(tmp, {"ro": (kill, False), "rw": (kill, True)})
        p = launch(launcher, tmp / "runs7r", "ro")
        rec = records(tmp / "runs7r")[0]
        check("read-only job killed -> 2", p.returncode == 2 and rec["state"] == "killed"
              and rec["signal"] == "SIGKILL", f"exit={p.returncode} {rec.get('signal')}")
        p = launch(launcher, tmp / "runs7w", "rw")
        rec = records(tmp / "runs7w")[0]
        check("writing job killed -> 3 (undescribed)", p.returncode == 3 and rec["exit_code"] == 3,
              f"exit={p.returncode}")
        check("the operator is told to check and restore", "restore the checkpoint" in p.stderr)
    else:
        print("  skip (signals are POSIX; CI and the bench's WSL both run this)")

    print("case 8  a child that cannot be started")
    jobs = {"ghost": cc.Job("PX", "src/ghost.py", True, "planted")}
    r8 = tmp / "runs8"
    code = cc.run_job("ghost", [], jobs=jobs, runs=r8, repo=tmp)
    rec = records(r8)[0]
    # A missing SCRIPT still starts the interpreter (which exits 2), so force a spawn failure
    # with an executable that does not exist - the case Popen itself raises on.
    saved = cc.sys.executable
    cc.sys.executable = str(tmp / "no-such-python")
    try:
        r8b = tmp / "runs8b"
        code_b = cc.run_job("ghost", [], jobs=jobs, runs=r8b, repo=tmp)
        rec_b = records(r8b)[0]
    finally:
        cc.sys.executable = saved
    check("missing script: interpreter's own 2 passed through", code == 2 and rec["state"] == "finished")
    check("spawn failure -> 2, state not-started, error recorded",
          code_b == 2 and rec_b["state"] == "not-started" and "error" in rec_b,
          f"{code_b} {rec_b['state']}")

    print("case 9  model digests")
    seen = ("import json, sys, pathlib\n"
            "runs = pathlib.Path(sys.argv[1])\n"
            "rec = [json.loads(p.read_text()) for p in runs.glob('*-cc-*.json')]\n"
            "(runs / 'seen.json').write_text(json.dumps(rec))\n")
    (tmp / "src" / "seen.py").write_text(seen, encoding="utf-8")
    jobs = {"m": cc.Job("PX", "src/seen.py", False, "planted", ("model-a", "model-b"))}
    asked: list[str] = []

    def good(m: str) -> str:
        asked.append(m)
        return "digest-of-" + m

    r9 = tmp / "runs9"
    code = cc.run_job("m", [str(r9)], jobs=jobs, runs=r9, repo=tmp, digest=good,
                      runtime=lambda: "0.0.0-test")
    before = json.loads((r9 / "seen.json").read_text())
    check("every model's digest read, in table order", asked == ["model-a", "model-b"], str(asked))
    check("the record the child saw already held the digests",
          code == 0 and before[0]["models"] == {"model-a": "digest-of-model-a",
                                                 "model-b": "digest-of-model-b"},
          str(before[0].get("models")))
    check("and the Ollama version that will run them", before[0]["ollama_version"] == "0.0.0-test")

    def no_runtime() -> str:
        raise LookupError("no /api/version")

    r9c = tmp / "runs9c"
    code = cc.run_job("m", [str(r9c)], jobs=jobs, runs=r9c, repo=tmp, digest=good, runtime=no_runtime)
    rec = records(r9c)
    check("no version, no run: -> 2, not-started, child never ran",
          code == 2 and rec[0]["state"] == "not-started" and "Ollama server" in rec[0]["error"]
          and not (r9c / "seen.json").exists(), str(rec and rec[0].get("error")))
    jobs0 = {"n": cc.Job("PX", "src/seen.py", False, "planted")}
    r9d = tmp / "runs9d"
    code = cc.run_job("n", [str(r9d)], jobs=jobs0, runs=r9d, repo=tmp, digest=good, runtime=no_runtime)
    check("a job with no model never asks Ollama anything", code == 0, f"exit={code}")

    def bad(m: str) -> str:
        if m == "model-b":
            raise LookupError("model not found")
        return "digest-of-" + m

    r9b = tmp / "runs9b"
    code = cc.run_job("m", [str(r9b)], jobs=jobs, runs=r9b, repo=tmp, digest=bad,
                      runtime=lambda: "0.0.0-test")
    rec = records(r9b)
    check("a missing digest -> 2", code == 2, f"exit={code}")
    check("state not-started; the error names the model",
          len(rec) == 1 and rec[0]["state"] == "not-started" and "model-b" in rec[0]["error"],
          str(rec and rec[0].get("error")))
    check("the child never ran", not (r9b / "seen.json").exists())

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
    sys.exit(1)
print("all command-center checks passed")
