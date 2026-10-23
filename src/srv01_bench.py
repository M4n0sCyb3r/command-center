"""The REAL bench behind promote's seam: SRV01 for real. P06 build step 4 (ADR 0042).

promote.run() cannot tell this from fake_bench.FakeBench - same seven methods, same results.
What step 3 proved on fakes is the logic that runs here.

HOW EACH ACTION REACHES THE MACHINE (Jose's picks, 2026-10-22)
-------------------------------------------------------------
review       src/review_script.py's main(), in-process: qwen3:14b, Project 3's contract. Its
             record gives the findings and the model digest. 0 approve, 1 revise, else no review.
qe           the QE lab's suite, UNCHANGED (ADR 0042), from its own directory:
             ansible-playbook playbooks/srv01-validation-suite.yml, then scripts/qe_report.py.
             The verdict is the REPORTER's exit code (0/1/2). The per-check results come from
             the report file, and so do the COMPLETED SCOPES: the suite is fail-fast, so "4
             checks then stopped" must never compare equal to "30 checks" - each scope appears
             as its own entry ("scope:<name>": completed | missing).
dry_run      pick 3: playbooks/ansible/p06-srv01.yml `analyze` - PowerShell's own parser, every
             command resolved ON SRV01. Any refusal raises promote.Refused (exit 2): native
             executables, dynamic invocation, remote targets, .NET static calls, redirection,
             no SupportsShouldProcess. Only then `whatif`. A dry run that cannot write.
checkpoint   pick 1: WSL has no Hyper-V rights (probed 2026-10-22), so a PERSON runs it. The flow
             writes marker value A ("<name>:pre") on SRV01, prints the exact [HOST] lines, and
             reads back what the person pastes: it must name the checkpoint and say the VM's
             checkpoint type is Standard (a Production restore is a boot, and a boot fails
             eventhealth for an hour). Confirmed -> marker value B ("<name>:post").
live_run     p06-srv01.yml `run`. ok = no terminating error and an empty error stream.
restore      pick 1: a person runs Restore-VMSnapshot + Start-VM; the flow then READS SRV01:
             the marker must say A. B means the restore did not happen; anything else means a
             state nobody described. Either is a BenchError -> promote exits 3. The QE half of
             "confirmed" is promote's own (re-run QE, compare to the before report).

THE VAULT PASSWORD (pick 2): asked ONCE, on the first Ansible call, held in this object only,
and handed to each ansible-playbook over an anonymous pipe (--vault-password-file /dev/fd/N,
pass_fds). Never a file, an environment variable, an argument, chat or git. Proved by
tools/probe_vault_pipe.py on 2026-10-22 (Claude predicted it would fail; it worked).

DC01: every playbook call carries `--limit srv01`, and p06-srv01.yml says `hosts: srv01` and
asserts it. Two locks. The QE suite's own playbook is `hosts: srv01` too.

WHAT THE FLOW ITSELF LEAVES ON SRV01: C:\\ProgramData\\P06\\ (the marker file, and the copied
script under run\\). That is promote's bookkeeping, outside every QE check - "nothing on SRV01
changed" (exit 2) means nothing the script or the QE suite would see.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import promote as PR

REPO = Path(__file__).resolve().parent.parent
PLAYBOOK = REPO / "playbooks" / "ansible" / "p06-srv01.yml"
QE_LAB = Path(os.environ.get("P06_QE_LAB", str(Path.home() / "windows-qe-lab")))
LIMIT = "srv01"                     # the only host this bench may touch
VM = "SRV01"
MARKER_WAIT_S, MARKER_TRIES = 15, 20            # after Start-VM: WinRM back within ~5 minutes

Runner = Callable[..., subprocess.CompletedProcess]
Ask = Callable[[str], str]


def _ansible_errors(text: str) -> str:
    """The lines that say WHAT failed. Found on the first bench run: the last 400 characters of
    the output were deprecation warnings, and the real error had scrolled off."""
    keep = [ln.strip() for ln in text.splitlines()
            if any(k in ln for k in ("fatal:", "FAILED!", "ERROR!", "UNREACHABLE!"))
            and "DEPRECATION" not in ln]
    return (" | ".join(keep) or text.strip()[-400:])[:1500]


def _read_paste(prompt: str) -> str:
    """Read what the person pastes, up to a line that says END (or end of input)."""
    print(prompt, file=sys.stderr, flush=True)
    PR.flush_typeahead()                         # only what is pasted AFTER the prompt counts
    lines = []
    for line in sys.stdin:
        if line.strip().upper() == "END":
            break
        lines.append(line)
    return "".join(lines)


class RealBench:
    target = ("SRV01 (srv01, 10.10.10.20) via the QE lab's Ansible inventory as Administrator; "
              "checkpoint/restore by a person at the Hyper-V host")

    def __init__(self, *, qe_lab: Path = QE_LAB, runner: Runner = subprocess.run,
                 ask_password: Callable[[], str] | None = None, paste: Ask = _read_paste,
                 review_main: Callable[..., int] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.qe_lab, self.runner, self.paste, self.sleep = qe_lab, runner, paste, sleep
        self._ask_password = ask_password or (lambda: getpass.getpass(
            "Ansible vault password (asked once for this promote run): "))
        self._pw: bytes | None = None
        self._review_main = review_main
        self._scratch = Path(tempfile.mkdtemp(prefix="p06-bench-"))
        self.checkpoint_confirmed: dict[str, bool] = {}

    # --- plumbing ---------------------------------------------------------------------------
    def _password(self) -> bytes:
        if self._pw is None:
            # Discard anything typed before the hidden prompt: on 2026-10-22 a password typed
            # ahead of the prompt was ECHOED to the terminal (echo is only off once getpass has
            # the terminal) and then read from the buffer. Typed early, it is now thrown away
            # and asked for again, hidden.
            PR.flush_typeahead()
            self._pw = (self._ask_password() + "\n").encode()
        return self._pw

    def _ansible(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
        """One ansible-playbook call: --limit srv01 always; the vault password over a pipe."""
        r, w = os.pipe()
        try:
            os.write(w, self._password())
        finally:
            os.close(w)
        cmd = ["ansible-playbook", *args, "--limit", LIMIT, "--vault-password-file", f"/dev/fd/{r}"]
        try:
            return self.runner(cmd, cwd=cwd, pass_fds=(r,), capture_output=True, text=True)
        except OSError as exc:
            raise PR.BenchError(f"ansible-playbook could not start: {exc}") from exc
        finally:
            os.close(r)

    def _action(self, action: str, **extra: Any) -> dict[str, Any]:
        out = self._scratch / f"{action}-{time.monotonic_ns()}.json"
        req = self._scratch / f"req-{out.stem}.json"
        req.write_text(json.dumps({"p06_action": action, "p06_out": str(out), **extra}),
                       encoding="utf-8")
        p = self._ansible(["-i", str(self.qe_lab / "inventory" / "hosts.yml"), str(PLAYBOOK),
                           "-e", f"@{req}"], cwd=REPO)
        if not out.is_file():
            raise PR.BenchError(f"{action}: no result file (ansible exit {p.returncode}): "
                                f"{_ansible_errors(p.stdout + p.stderr)}")
        try:
            result = json.loads(out.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise PR.BenchError(f"{action}: unreadable result file: {exc}") from exc
        return result

    def _script_vars(self, script: Path) -> dict[str, str]:
        raw = script.read_bytes()
        return {"p06_script_local": str(script.resolve()),
                "p06_script_sha": hashlib.sha256(raw).hexdigest()[:16]}

    def _marker(self, value: str | None = None) -> str | None:
        if value is None:
            res = self._action("marker_read")
        else:
            res = self._action("marker_write", p06_marker_value=value)
        if res.get("failed") or res.get("error"):
            raise PR.BenchError(f"marker {'read' if value is None else 'write'} failed: "
                                f"{res.get('msg') or res.get('error')}")
        out = res.get("output") or [{}]
        return (out[0] or {}).get("value")

    # --- the seam ---------------------------------------------------------------------------
    def review(self, script: Path) -> PR.Review:
        import review_script                                     # the review job, in-process
        runs = self._scratch / "review"
        main = self._review_main or review_script.main
        code = main([str(script)], runs=runs)
        recs = [json.loads(p.read_text(encoding="utf-8")) for p in runs.glob("*.json")] \
            if runs.is_dir() else []
        rec = recs[0] if len(recs) == 1 else {}
        if code not in (0, 1) or not rec:
            raise PR.BenchError(f"no review (review exit {code}; "
                                f"{rec.get('error', 'no record') if rec else 'no record'})")
        return PR.Review("approve" if code == 0 else "revise", rec.get("findings", []),
                         {"model": rec.get("model"), "model_digest": rec.get("model_digest"),
                          "ollama_version": rec.get("ollama_version"),
                          "script_sha256": rec.get("sha256")})

    def qe(self) -> PR.QEReport:
        report = self.qe_lab / "reports" / "srv01-qe-results.json"
        try:
            report.unlink(missing_ok=True)       # SOP step 4: a dead run must not grade the last
        except OSError as exc:
            raise PR.BenchError(f"cannot clear the old QE report: {exc}") from exc
        self._ansible(["-i", "inventory/hosts.yml", "playbooks/srv01-validation-suite.yml"],
                      cwd=self.qe_lab)                     # its exit is NOT the verdict
        if not report.is_file():
            return PR.QEReport(2, {})
        rep = self.runner([sys.executable, "scripts/qe_report.py", str(report)], cwd=self.qe_lab,
                          capture_output=True, text=True)
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
        except ValueError:
            return PR.QEReport(2, {})
        checks: dict[str, str] = {}
        for t in data.get("results", []) if isinstance(data, dict) else []:
            name = t.get("test", "?")
            checks[name] = "pass" if t.get("result") == "PASS" else "fail"
        done = set(data.get("completed_scopes", [])) if isinstance(data, dict) else set()
        for s in (data.get("requested_scopes", []) if isinstance(data, dict) else []):
            checks[f"scope:{s}"] = "pass" if s in done else "missing"
        # keep a copy beside the record: the QE lab's file is overwritten by the next run
        keep = self._scratch / f"qe-{time.monotonic_ns()}.json"
        shutil.copyfile(report, keep)
        return PR.QEReport(rep.returncode if rep.returncode in (0, 1, 2) else 2, checks)

    def dry_run(self, script: Path) -> PR.StepResult:
        sv = self._script_vars(script)
        res = self._action("analyze", **sv)
        if res.get("failed") or res.get("error"):
            raise PR.BenchError(f"the analyzer failed on SRV01: {res.get('msg') or res.get('error')}")
        found = (res.get("output") or [{}])[0] or {}
        refusals = list(found.get("refusals") or [])
        if refusals:
            raise PR.Refused("; ".join(refusals)[:1500])
        res = self._action("whatif", **sv)
        # "What if:" lines go through the HOST (ShouldProcess -> host UI), not any output
        # stream, so `*>&1` cannot catch them; win_powershell keeps them in host_out. Found
        # on the step-4 bench check: the dry run's record was empty.
        text = "\n".join([str((o or {}).get("output", "")) for o in res.get("output") or []]
                         + [str(res.get("host_out") or "")]).strip()
        ok = not res.get("failed") and not res.get("error")
        detail = text if ok else f"{text}\nERRORS: {res.get('error') or res.get('msg')}"
        cmds = "; ".join(str(c) for c in found.get("commands") or [])
        return PR.StepResult(ok, f"commands: {cmds}\n{detail}")

    def checkpoint(self, name: str) -> None:
        self._marker(f"{name}:pre")
        pasted = self.paste(
            f"\n=== CHECKPOINT - your turn ===\n"
            f"In Windows PowerShell AS ADMINISTRATOR on <host> (the Hyper-V host), run:\n\n"
            f"  Checkpoint-VM -Name {VM} -SnapshotName '{name}'\n"
            f"  Get-VM -Name {VM} | Select-Object Name, State, CheckpointType\n"
            f"  Get-VMSnapshot -VMName {VM} -Name '{name}' | Select-Object Name, CreationTime\n\n"
            f"Paste the output of the last two lines here, then a line that says END.\n")
        ok = name in pasted and "Standard" in pasted
        self.checkpoint_confirmed[name] = ok
        if not ok:
            print("promote: the paste does not show the checkpoint by name AND CheckpointType "
                  "Standard - not confirmed.", file=sys.stderr)
            return
        self._marker(f"{name}:post")             # A then B: a restore must bring back A

    def checkpoint_exists(self, name: str) -> bool:
        return self.checkpoint_confirmed.get(name, False)

    def live_run(self, script: Path) -> PR.StepResult:
        res = self._action("run", **self._script_vars(script))
        text = "\n".join(str((o or {}).get("output", "")) for o in res.get("output") or [])
        ok = not res.get("failed") and not res.get("error")
        return PR.StepResult(ok, text if ok else f"{text}\nERRORS: {res.get('error') or res.get('msg')}")

    def restore(self, name: str) -> None:
        answer = self.paste(
            f"\n=== RESTORE - your turn ===\n"
            f"In Windows PowerShell AS ADMINISTRATOR on <host>, run:\n\n"
            f"  Restore-VMSnapshot -VMName {VM} -Name '{name}' -Confirm:$false\n"
            f"  Start-VM -Name {VM}\n"
            f"  Get-VM -Name {VM} | Select-Object Name, State\n\n"
            f"Paste the output (State must be Running), then a line that says END. If the restore "
            f"failed, paste the error instead.\n")
        if "Running" not in answer:
            raise PR.BenchError("the restore was not reported Running")
        want, last = f"{name}:pre", None
        for _ in range(MARKER_TRIES):
            try:
                last = self._marker()
                break
            except PR.BenchError:
                self.sleep(MARKER_WAIT_S)                 # WinRM not back yet
        else:
            raise PR.BenchError("SRV01 did not answer after the restore")
        if last != want:
            raise PR.BenchError(f"the marker on SRV01 says {last!r}, not {want!r} - SRV01 is not "
                                f"at the checkpoint")
