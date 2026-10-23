"""P06 build step 4: the REAL bench's own logic, proved without SRV01 (CI).

Everything srv01_bench.RealBench decides is decided in Python: what it passes to
ansible-playbook, how it hands over the vault password, what a pasted checkpoint must show,
what the marker must read after a restore, how a QE report becomes per-check results. Those
are checked here against a FAKE `runner` that plays ansible-playbook and the QE reporter. What
Python cannot check - PowerShell's parser on SRV01, Hyper-V, the QE suite itself - is the
bench's (`tests/` cannot reach SRV01; step 5's playbook and the bench checks in the step-4
evidence do).

  case 1  every Ansible call: --limit srv01; the vault password over a pipe (/dev/fd/N in
          pass_fds), read back intact by the "child"; never in argv or the environment;
          asked ONCE across many calls
  case 2  checkpoint: a paste must name the checkpoint AND say Standard, or it is not confirmed
          (and marker B is never written); a good paste -> marker A then B
  case 3  restore: the marker must read A ("<name>:pre"). B -> BenchError (the restore did not
          happen); no "Running" in the paste -> BenchError; SRV01 silent -> BenchError
  case 4  dry run: any analyzer refusal -> promote.Refused (not a StepResult); none -> -WhatIf
  case 5  QE: per-check results + every requested scope (completed or missing); the reporter's
          code is the verdict; no report file -> 2; the old report is deleted first
  case 6  review: exit 0/1 -> approve/revise with the record's findings + digest; 2 -> BenchError
  case 7  end to end: promote.run() with the REAL bench on the fake runner - a clean script is
          promoted (0); a script that breaks a QE check is restored, the marker reads A -> 1;
          a restore that never happened (marker B) -> 3
  case 8  the playbook: hosts srv01, asserts inventory_hostname == 'srv01', five actions only;
          one register per task (a skipped task overwrites a shared one); result file in always:

Run:  python tests/test_srv01_bench.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import promote as PR                                   # noqa: E402
import srv01_bench as SB                               # noqa: E402

failures: list[str] = []
PW = "not-a-real-password-é"


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class FakeAnsible:
    """Plays ansible-playbook (both playbooks) and qe_report.py against an in-memory SRV01."""

    def __init__(self, qe_lab: Path) -> None:
        self.qe_lab = qe_lab
        self.calls: list[dict] = []
        self.marker: str | None = None
        self.refusals: list[str] = []
        self.qe_scopes = ["identity", "services", "auditpolicy"]
        self.qe_fail: set[str] = set()                 # check names that FAIL
        self.qe_stop_after: str | None = None          # fail-fast: scopes after this missing
        self.write_report = True
        self.silent = 0                                # marker reads that fail (WinRM down)
        self.applied: dict[str, str] = {}              # what live runs did
        self.effect: dict[str, str] = {}

    def __call__(self, cmd, cwd=None, pass_fds=(), capture_output=False, text=False, **kw):
        if cmd[0] != "ansible-playbook":               # qe_report.py
            data = json.loads(Path(cmd[-1]).read_text())
            rc = 1 if any(r["result"] == "FAIL" for r in data["results"]) else 0
            if set(data["requested_scopes"]) - set(data["completed_scopes"]) and rc == 0:
                rc = 2
            return SimpleNamespace(returncode=rc, stdout="", stderr="")
        vault = cmd[cmd.index("--vault-password-file") + 1]
        fd = int(vault.rsplit("/", 1)[1])
        pw = os.read(fd, 4096).decode()
        call = {"cmd": cmd, "pass_fds": pass_fds, "pw": pw, "env_has_pw": any(
            PW in v for v in os.environ.values()), "limit": cmd[cmd.index("--limit") + 1] if "--limit" in cmd else None}
        self.calls.append(call)
        if any(a.endswith("srv01-validation-suite.yml") for a in cmd):
            if self.write_report:
                checks = {"identity": "Server Identity", "services": "Required Services",
                          "auditpolicy": "Advanced Audit Policy"}
                done, results = [], []
                for s in self.qe_scopes:
                    name = checks[s]
                    bad = name in self.qe_fail or (s == "services" and
                                                   self.applied.get("spooler") == "auto")
                    results.append({"test": name, "expected": {}, "actual": {},
                                    "result": "FAIL" if bad else "PASS"})
                    done.append(s)
                    if bad or s == self.qe_stop_after:
                        break
                rep = self.qe_lab / "reports" / "srv01-qe-results.json"
                rep.parent.mkdir(parents=True, exist_ok=True)
                rep.write_text(json.dumps({"schema_version": 2, "requested_scopes": self.qe_scopes,
                                           "completed_scopes": done, "results": results}))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        req = json.loads(Path(cmd[cmd.index("-e") + 1][1:]).read_text())
        call["action"] = req["p06_action"]
        out: dict = {"action": req["p06_action"], "failed": False, "output": [], "error": [],
                     "msg": ""}
        a = req["p06_action"]
        if a == "marker_read" and self.silent > 0:
            self.silent -= 1
            return SimpleNamespace(returncode=4, stdout="unreachable", stderr="")
        if a == "marker_write":
            self.marker = req["p06_marker_value"]
            out["output"] = [{"value": self.marker}]
        elif a == "marker_read":
            out["output"] = [{"value": self.marker}]
        elif a == "analyze":
            out["output"] = [{"refusals": self.refusals, "should_process": True,
                              "commands": ["line 2: Set-Service (Cmdlet)"]}]    # strings, as SRV01 sends them
        elif a == "whatif":                            # as SRV01 sends it: in host_out
            out["output"] = []
            out["host_out"] = "What if: Performing the operation ..."
        elif a == "run":
            self.applied.update(self.effect)
            out["output"] = [{"output": "done"}]
        Path(req["p06_out"]).write_text(json.dumps(out))
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def bench(tmp: Path, *, pastes: list[str] | None = None, review_code: int = 0):
    fa = FakeAnsible(tmp / "qe-lab")
    asked = []
    pastes = list(pastes or [])

    def review_main(argv, runs):
        runs.mkdir(parents=True, exist_ok=True)
        rec = {"model": "qwen3:14b", "model_digest": "d" * 12, "ollama_version": "0.40.8",
               "sha256": "s", "findings": [{"line_start": 1}] if review_code == 1 else []}
        if review_code in (0, 1):
            (runs / "r.json").write_text(json.dumps(rec))
        return review_code

    b = SB.RealBench(qe_lab=fa.qe_lab, runner=fa, ask_password=lambda: asked.append(1) or PW,
                     paste=lambda prompt: pastes.pop(0), review_main=review_main,
                     sleep=lambda s: None)
    return b, fa, asked


def quiet(fn, *a, **k):
    with contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **k)


GOOD = "Name  CheckpointType\nSRV01 Standard\nName\n{name}\n"

with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    script = tmp / "fix.ps1"
    script.write_text("[CmdletBinding(SupportsShouldProcess)]\nparam()\nSet-Service Spooler -StartupType Disabled\n")

    print("case 1  every Ansible call")
    b, fa, asked = bench(tmp)
    for _ in range(3):
        b._marker("x")
    b.qe()
    check("every call carries --limit srv01", all(c["limit"] == "srv01" for c in fa.calls),
          str([c["limit"] for c in fa.calls]))
    check("dc01 appears in no call", not any("dc01" in " ".join(c["cmd"]) for c in fa.calls))
    check("the password reached each child intact, over the pipe",
          all(c["pw"] == PW + "\n" for c in fa.calls))
    check("the pipe's fd was passed (pass_fds) and named /dev/fd/N",
          all(len(c["pass_fds"]) == 1 and c["cmd"][c["cmd"].index("--vault-password-file") + 1]
              == f"/dev/fd/{c['pass_fds'][0]}" for c in fa.calls))
    check("never in argv", not any(PW in " ".join(c["cmd"]) for c in fa.calls))
    check("never in the environment", not any(c["env_has_pw"] for c in fa.calls))
    check("asked ONCE across 4 calls", len(asked) == 1, str(len(asked)))

    print("\ncase 2  checkpoint")
    b, fa, _ = bench(tmp, pastes=["Name\ncp1\nCheckpointType Production\n"])
    quiet(b.checkpoint, "cp1")
    check("Production in the paste -> not confirmed", b.checkpoint_exists("cp1") is False)
    check("... and marker B never written (still A)", fa.marker == "cp1:pre", str(fa.marker))
    b, fa, _ = bench(tmp, pastes=["SRV01 Standard\nsome-other-checkpoint\n"])
    quiet(b.checkpoint, "cp1")
    check("a paste not naming the checkpoint -> not confirmed", b.checkpoint_exists("cp1") is False)
    b, fa, _ = bench(tmp, pastes=[GOOD.format(name="cp1")])
    quiet(b.checkpoint, "cp1")
    writes = [c for c in fa.calls if c.get("action") == "marker_write"]
    check("a good paste -> confirmed; marker A then B",
          b.checkpoint_exists("cp1") and len(writes) == 2 and fa.marker == "cp1:post")

    print("\ncase 3  restore")
    def restored(marker, paste="State Running", silent=0):
        b, fa, _ = bench(tmp, pastes=[paste])
        fa.marker, fa.silent = marker, silent
        try:
            quiet(b.restore, "cp1")
            return None
        except PR.BenchError as exc:
            return str(exc)
    check("marker A -> restore accepted", restored("cp1:pre") is None)
    err = restored("cp1:post")
    check("marker B -> BenchError (the restore did not happen)", err is not None and "not at the "
          "checkpoint" in err, str(err))
    check("no 'Running' in the paste -> BenchError", restored("cp1:pre", paste="error: ...") is not None)
    check("SRV01 back after 3 silent reads -> accepted", restored("cp1:pre", silent=3) is None)
    check("SRV01 silent for every try -> BenchError",
          restored("cp1:pre", silent=SB.MARKER_TRIES) is not None)

    print("\ncase 4  dry run")
    b, fa, _ = bench(tmp)
    fa.refusals = ["line 3: 'auditpol' is a native executable - -WhatIf cannot stop it"]
    try:
        b.dry_run(script)
        got = "no exception"
    except PR.Refused as exc:
        got = f"Refused: {exc}"
    check("an analyzer refusal -> promote.Refused", got.startswith("Refused") and "auditpol" in got, got)
    check("... and -WhatIf never ran", not any(c.get("action") == "whatif" for c in fa.calls))
    b, fa, _ = bench(tmp)
    r = b.dry_run(script)
    check("no refusal -> analyze then -WhatIf, ok",
          r.ok and [c.get("action") for c in fa.calls] == ["analyze", "whatif"] and "What if" in r.output
          and "Set-Service (Cmdlet)" in r.output)

    print("\ncase 5  QE")
    b, fa, _ = bench(tmp)
    q = b.qe()
    check("all green -> 0; three checks + three scopes", q.exit_code == 0 and len(q.checks) == 6
          and q.checks.get("scope:auditpolicy") == "pass", str(q.checks))
    fa.qe_fail = {"Required Services"}
    q = b.qe()
    check("fail-fast: a failing check -> 1, later scope MISSING (not silently absent)",
          q.exit_code == 1 and q.checks.get("Required Services") == "fail"
          and q.checks.get("scope:auditpolicy") == "missing", str(q.checks))
    fa.qe_fail, fa.write_report = set(), False
    q = b.qe()
    check("no report file -> 2 (the old one was deleted first, not re-graded)", q.exit_code == 2)

    print("\ncase 6  review")
    for code, want in ((0, "approve"), (1, "revise")):
        b, _, _ = bench(tmp, review_code=code)
        r = b.review(script)
        check(f"review exit {code} -> {want}, digest carried", r.verdict == want
              and r.meta["model_digest"] == "d" * 12)
    b, _, _ = bench(tmp, review_code=2)
    try:
        b.review(script)
        check("review exit 2 -> BenchError", False)
    except PR.BenchError:
        check("review exit 2 -> BenchError", True)

    print("\ncase 7  end to end: promote.run() with the REAL bench")
    def e2e(effect, restore_marker_ok=True):
        name_holder = {}

        def paste(prompt):
            if "CHECKPOINT" in prompt:
                name = prompt.split("-SnapshotName '")[1].split("'")[0]
                name_holder["n"] = name
                return GOOD.format(name=name)
            if not restore_marker_ok:
                fa.marker = name_holder["n"] + ":post"      # the restore never happened
            else:
                fa.marker = name_holder["n"] + ":pre"       # Hyper-V brought back state A
                fa.applied.clear()
            return "SRV01 Running"
        b, fa, _ = bench(tmp)
        b.paste = paste
        fa.effect = effect
        d = Path(tempfile.mkdtemp(dir=tmp))
        code = quiet(PR.run, script, b, runs=d / "runs", promoted=d / "promoted")
        rec = json.loads(next((d / "runs").glob("*.json")).read_text())
        return code, rec
    code, rec = e2e({})
    check("a clean script -> 0, promoted", code == 0 and rec["state"] == "promoted", rec["reason"])
    code, rec = e2e({"spooler": "auto"})
    check("a script breaking a QE check -> restored, marker A, QE matches -> 1",
          code == 1 and rec["state"] == "restored", rec["reason"])
    code, rec = e2e({"spooler": "auto"}, restore_marker_ok=False)
    check("restore reported but the marker says B -> 3", code == 3
          and rec["state"] == "undescribed", rec["reason"])

print("\ncase 8  the playbook")
pb = (REPO / "playbooks" / "ansible" / "p06-srv01.yml").read_text()
check("hosts: srv01", "\n  hosts: srv01\n" in pb)
check("asserts inventory_hostname == 'srv01'", "inventory_hostname == 'srv01'" in pb)
check("the analyzer refuses a state-changing cmdlet without -WhatIf (found designing step 5)",
      "Parameters.ContainsKey('WhatIf')" in pb and "cannot take -WhatIf" in pb)
check("five actions, no more",
      "p06_action in ['marker_write', 'marker_read', 'analyze', 'whatif', 'run']" in pb)
import re                                              # noqa: E402
regs = re.findall(r"^\s*register:\s*(\S+)", pb, re.M)
check("no two tasks register the same variable (a SKIPPED task still registers - found on the "
      "first bench run, 2026-10-22: the marker read came back None)",
      len(regs) == len(set(regs)) and len(regs) == 5, str(regs))
check("the result file is written in `always:` - even when the action fails",
      "always:" in pb and pb.index("always:") < pb.index("Write the one result file"))
code_lines = [ln for ln in pb.splitlines() if not ln.lstrip().startswith("#")]
check("no 'dc01' outside comments", not any("dc01" in ln.lower() for ln in code_lines))

print()
if failures:
    print(f"FAILED: {len(failures)} check(s)")
    sys.exit(1)
print("all srv01 bench checks passed")
