"""A fake SRV01, checkpoint store and QE runner for `promote` - P06 build step 3 (ADR 0044).

The CI half of ADR 0044's testing: every fork of the promotion flow, proved on every push,
with no machine, no Hyper-V and no model. `promote.run()` cannot tell this from the real
bench - it sees only the `Bench` seam - which is the point: the logic CI proves is the logic
SRV01 will run (Project 5's Target, one level up).

THE FAKE MACHINE is a dict of settings. The fake QE suite owns a list of CHECKS, each one a
setting and the value it must hold; a check passes iff the machine holds that value. Settings
no check reads exist too (`feature:*`), so a script can change the machine and still pass -
the shape of a real clean change.

THE FAKE SCRIPT is the case's `effect`: the settings the live run writes. The script FILE is
still real text (the deny-list reads it); only what it does to the machine is scripted.

FAULTS are named per step, so each fork of the flow can be reached on purpose:
  {"review": "revise" | "error"}   the reviewer blocks, or gives no review
  {"dry_run": "fail" | "refuse" | "error" | "crash"}   refuse: promote.Refused (step 4)
  {"checkpoint": "error" | "vanish"}       vanish: created, then not found when asked
  {"live_run": "fail" | "error" | "crash"} fail: the script errors AFTER writing its effect
  {"qe_after": "error" | "crash"}
  {"restore": "error" | "partial"}         partial: one setting is not put back
  {"confirm": "error"}                     the QE run after a restore fails as a tool
"error" raises BenchError (a tool failure, which the flow handles); "crash" raises
RuntimeError (a bug, which only the last-resort catch sees).

`calls` lists every action in order, so a case can assert what was NOT done - that a blocked
script was never dry-run, that nothing was written before the checkpoint was confirmed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from promote import BenchError, QEReport, Refused, Review, StepResult

TARGET = "FakeBench (in memory) - no machine, no Hyper-V, no model is touched"

# The fake QE suite: (check name, setting it reads, value it requires). Shaped like the QE
# lab's SRV01 checks (services, audit policy, firewall, RDP, registry), not a copy of them.
CHECKS: tuple[tuple[str, str, str], ...] = (
    ("svc-spooler-disabled", "service:Spooler", "disabled"),
    ("svc-w32time-running", "service:W32Time", "running"),
    ("audit-logon-sf", "audit:Logon", "Success and Failure"),
    ("fw-domain-on", "firewall:Domain", "on"),
    ("rdp-nla-required", "rdp:nla", "1"),
    ("reg-smb1-off", "reg:SMB1", "0"),
)
BASELINE: dict[str, str] = {setting: want for _, setting, want in CHECKS} | {
    "feature:Telnet-Client": "absent",
    "feature:SNMP": "absent",
}


class FakeBench:
    target = TARGET

    def __init__(self, *, start: dict[str, str] | None = None,
                 effect: dict[str, str] | None = None,
                 faults: dict[str, str] | None = None,
                 findings: list[dict[str, Any]] | None = None) -> None:
        self.machine: dict[str, str] = dict(BASELINE) | dict(start or {})
        self.effect = dict(effect or {})
        self.faults = dict(faults or {})
        self.findings = list(findings or [])
        self.checkpoints: dict[str, dict[str, str]] = {}
        self.calls: list[str] = []
        self.qe_runs = 0

    def _fault(self, step: str, *kinds: str) -> str | None:
        kind = self.faults.get(step)
        if kind == "error" and "error" in kinds:
            raise BenchError(f"fake {step}: injected tool failure")
        if kind == "crash" and "crash" in kinds:
            raise RuntimeError(f"fake {step}: injected crash")
        return kind

    # --- the seam -----------------------------------------------------------------------
    def review(self, script: Path) -> Review:
        self.calls.append("review")
        kind = self._fault("review", "error")
        if kind == "revise":
            return Review("revise", self.findings or [{"line_start": 1, "line_end": 1,
                          "category": "correctness", "severity": "blocking",
                          "why": "scripted block", "fix": "scripted"}], {"model": "scripted"})
        return Review("approve", [], {"model": "scripted"})

    def qe(self) -> QEReport:
        self.qe_runs += 1
        after_live = "live_run" in self.calls
        restored = "restore" in self.calls
        label = "qe-confirm" if restored else ("qe-after" if after_live else "qe-before")
        self.calls.append(label)
        if restored:
            self._fault("confirm", "error")
        elif after_live:
            self._fault("qe_after", "error", "crash")
        checks = {name: "pass" if self.machine.get(setting) == want else "fail"
                  for name, setting, want in CHECKS}
        return QEReport(0 if all(v == "pass" for v in checks.values()) else 1, checks)

    def dry_run(self, script: Path) -> StepResult:
        self.calls.append("dry_run")
        kind = self._fault("dry_run", "error", "crash")
        if kind == "refuse":
            raise Refused("line 2: 'auditpol' is a native executable - -WhatIf cannot stop it")
        if kind == "fail":
            return StepResult(False, "WhatIf: scripted failure")
        return StepResult(True, "WhatIf: " + ", ".join(f"{k}={v}" for k, v in self.effect.items()))

    def checkpoint(self, name: str) -> None:
        self.calls.append("checkpoint")
        self._fault("checkpoint", "error")
        if self.faults.get("checkpoint") != "vanish":
            self.checkpoints[name] = dict(self.machine)

    def checkpoint_exists(self, name: str) -> bool:
        return name in self.checkpoints

    def live_run(self, script: Path) -> StepResult:
        self.calls.append("live_run")
        self._fault("live_run", "error", "crash")
        self.machine.update(self.effect)              # the script's effect lands first ...
        if self.faults.get("live_run") == "fail":     # ... then it errors half-way
            return StepResult(False, "script threw after writing")
        return StepResult(True, "done")

    def restore(self, name: str) -> None:
        self.calls.append("restore")
        self._fault("restore", "error")
        snap = dict(self.checkpoints[name])
        if self.faults.get("restore") == "partial" and self.effect:
            k = sorted(self.effect)[0]
            snap[k] = self.effect[k]                  # one setting not put back
        self.machine = snap

    # --- for the scorer -----------------------------------------------------------------
    def changed(self, against: dict[str, str]) -> list[str]:
        return sorted(k for k in set(self.machine) | set(against)
                      if self.machine.get(k) != against.get(k))
