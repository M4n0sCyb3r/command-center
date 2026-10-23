"""P05 build step 2: detect, apply one fix, verify, roll back - on the fake (CI, no model, no VM).

  case 1  detection is code: nothing at baseline; drift found with its evidence; an unreadable
          target before anything is done is TargetUnreachable (a tooling error)
  case 2  an honest fix of each class is `verified` on the first read
  case 3  BrokenRemediator (bad fix, honest rollback) -> `rolled-back`; the machine is back to the
          BEFORE-state, which is the drifted one - by definition, not by accident
  case 4  a bad fix AND a bad rollback -> `rollback-failed`
  case 5  a wrong-argument call through an honest remediator: rule 1 catches it -> `rolled-back`
  case 6  only a literal True approves; anything else is `declined` and touches nothing
  case 7  refused before anything: off-menu, or a call aimed at a different item than the drift;
          the approver is never asked
  case 8  collateral change (rule 3): a fix that also moves another item is not verified, and a
          rollback that cannot undo the collateral is `rollback-failed` - honestly
  case 9  settling: a slow machine verifies once it settles; one that never settles hits the
          ceiling (fake clock - no real waiting); a verifier error is a failure, never a pass
  case 10 never retried: one apply per approval, one rollback

Run:  python tests/test_remediate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import remediate as M                                 # noqa: E402
import target as T                                    # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class Clock:
    """A fake clock: sleep() advances time instantly, so the 30 s ceiling costs nothing."""

    def __init__(self) -> None:
        self.t = 1000.0

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


YES = lambda **kw: True                               # noqa: E731
base = T.load_baseline()
FIX = {
    "service:Spooler": (dict(start_type="automatic", status="running"),
                        T.Call("Set-AgentServiceConfig", "service:Spooler",
                               {"start_type": "disabled", "status": "stopped"})),
    "audit:Logon": (dict(setting="No Auditing"),
                    T.Call("Set-AgentAuditSetting", "audit:Logon", {"setting": "Success and Failure"})),
    "registry:rdp-nla": (dict(value=0),
                         T.Call("Set-AgentRegistryValue", "registry:rdp-nla", {"value": 1})),
}


def drifted(item: str, broken=False):
    tgt, host = T.fake_target(base, broken=broken)
    host.set(item, **FIX[item][0])
    (d,) = M.detect(tgt, base)
    return tgt, host, d


def run(tgt, d, call, approve=YES, clock=None):
    c = clock or Clock()
    return M.remediate(tgt, base, d, call, approve, clock=c.now, sleep=c.sleep)


print("case 1  detection is code")
tgt, host = T.fake_target(base)
check("nothing drifted at baseline", M.detect(tgt, base) == [])
host.set("service:Spooler", start_type="automatic", status="running")
ds = M.detect(tgt, base, clock=lambda: 42.0)
check("one drift, with its evidence", len(ds) == 1 and ds[0].item == "service:Spooler"
      and ds[0].observed == {"start_type": "automatic", "status": "running"}
      and ds[0].baseline == {"start_type": "disabled", "status": "stopped"} and ds[0].read_at == 42.0)
check("names the fields that differ", ds[0].fields == ["start_type", "status"])


class DeadReader:
    def host(self): return "X"
    def read(self, item): raise ConnectionError("WinRM: no route")


dead = T.Target(reader=DeadReader(), remediator=tgt.remediator)
try:
    M.detect(dead, base); ok = False
except M.TargetUnreachable:
    ok = True
check("unreadable before anything -> TargetUnreachable", ok)

print("case 2  an honest fix of each class is verified")
for item in FIX:
    tgt, host, d = drifted(item)
    r = run(tgt, d, FIX[item][1])
    check(f"{item}: verified on the first read", r.outcome == "verified" and r.reads == 1, r.outcome)
    check(f"{item}: machine at baseline", M.detect(tgt, base) == [])
    check(f"{item}: the remediator's report is recorded", r.report is not None
          and r.report.before == FIX[item][0])

print("case 3  a bad fix with an honest rollback -> rolled-back")
tgt, host, d = drifted("audit:Logon", broken=True)
before = host.snapshot()
r = run(tgt, d, FIX["audit:Logon"][1])
check("outcome rolled-back", r.outcome == "rolled-back", r.outcome)
check("rule 1 and rule 2 named", [x[:6] for x in r.reasons] == ["rule 1", "rule 2"], str(r.reasons))
check("the report claimed success anyway", r.report.reported_after == {"setting": "Success and Failure"})
check("machine back to the BEFORE-state (the drifted one)", host.snapshot() == before
      and host.snapshot()["audit:Logon"] == {"setting": "No Auditing"})

print("case 4  a bad fix and a bad rollback -> rollback-failed")
tgt, host, d = drifted("registry:rdp-nla", broken="broken-rollback")
r = run(tgt, d, FIX["registry:rdp-nla"][1])
check("outcome rollback-failed", r.outcome == "rollback-failed", r.outcome)
check("the rollback failure is named", any(x.startswith("rollback:") for x in r.reasons), str(r.reasons))

print("case 5  a wrong-argument call is caught by rule 1")
tgt, host, d = drifted("service:Spooler")
wrong = T.Call("Set-AgentServiceConfig", "service:Spooler", {"start_type": "manual", "status": "stopped"})
r = run(tgt, d, wrong)
check("outcome rolled-back", r.outcome == "rolled-back", r.outcome)
check("only rule 1 fails (the preview was honoured)", [x[:6] for x in r.reasons] == ["rule 1"],
      str(r.reasons))
check("machine back to the drifted before-state",
      tgt.reader.read("service:Spooler") == {"start_type": "automatic", "status": "running"})

print("case 6  only a literal True approves")
for answer in ("yes", 1, False, None, "True"):
    tgt, host, d = drifted("audit:Logon")
    snap = host.snapshot()
    r = run(tgt, d, FIX["audit:Logon"][1], approve=lambda a=answer, **kw: a)
    check(f"approve -> {answer!r}: declined, untouched", r.outcome == "declined" and host.snapshot() == snap,
          r.outcome)
seen = {}
tgt, host, d = drifted("audit:Logon")
run(tgt, d, FIX["audit:Logon"][1], approve=lambda **kw: (seen.update(kw), True)[1])
check("the approver is shown the screen, the drift, the call and the preview",
      set(seen) == {"screen", "drift", "call", "preview"} and len(seen["preview"]) == 1)

print("case 7  refused before anything")
asked = []
ask = lambda **kw: (asked.append(1), True)[1]                       # noqa: E731
tgt, host, d = drifted("service:Spooler")
snap = host.snapshot()
r = run(tgt, d, T.Call("Stop-Computer", "service:Spooler", {"status": "stopped"}), approve=ask)
check("off-menu -> refused, untouched", r.outcome == "refused" and host.snapshot() == snap)
r = run(tgt, d, FIX["audit:Logon"][1], approve=ask)
check("a call aimed at another item -> refused", r.outcome == "refused" and host.snapshot() == snap,
      str(r.reasons))
check("the approver was never asked", asked == [])

print("case 8  collateral change")


class Collateral(T.FakeRemediator):
    """Fixes the item - and quietly flips RDP NLA off, and its rollback leaves that alone."""

    def _write(self, item, values):
        super()._write(item, values)
        if item != "registry:rdp-nla":
            T.FakeRemediator._write(self, "registry:rdp-nla", {"value": 0})


tgt, host = T.fake_target(base)
tgt = T.Target(reader=tgt.reader, remediator=Collateral(host, base))
host.set("audit:Logon", setting="No Auditing")
(d,) = M.detect(tgt, base)
r = run(tgt, d, FIX["audit:Logon"][1])
check("rule 3 names the moved item", any("rule 3" in x and "registry:rdp-nla" in x for x in r.reasons),
      str(r.reasons))
check("not verified, and the rollback cannot undo it -> rollback-failed",
      r.outcome == "rollback-failed", r.outcome)

print("case 9  settling")


class LagReader:
    """Returns the pre-apply machine for `lag` verify rounds, then the truth (a slow service).

    Rounds 1 and 2 are the before-read and the post-approval recheck (step 3); both see the
    truth, which is still the pre-apply state. Verify rounds start at 3.
    """

    def __init__(self, inner, lag, first="service:Spooler", raises=0):
        self.inner, self.lag, self.first, self.raises = inner, lag, first, raises
        self.rounds, self.stale = 0, {}

    def host(self):
        return self.inner.host()

    def read(self, item):
        if item == self.first:
            self.rounds += 1
        if self.rounds <= 2:
            self.stale[item] = self.inner.read(item)
            return self.inner.read(item)
        if 2 < self.rounds <= 2 + self.raises:
            raise TimeoutError("WinRM read timed out")
        if self.rounds <= 2 + self.raises + self.lag:
            return dict(self.stale[item])
        return self.inner.read(item)


def lagged(lag, raises=0):
    tgt, host, d = drifted("service:Spooler")
    return T.Target(reader=LagReader(tgt.reader, lag, raises=raises), remediator=tgt.remediator), host, d


tgt, host, d = lagged(3)
c = Clock()
r = run(tgt, d, FIX["service:Spooler"][1], clock=c)
check("slow but settles: verified after 4 reads", r.outcome == "verified" and r.reads == 4, f"{r.outcome} {r.reads}")
check("waited 3 intervals of 1.0 s (fake clock)", c.t - 1000.0 == 3.0, str(c.t - 1000.0))
tgt, host, d = lagged(10_000)
c = Clock()
r = run(tgt, d, FIX["service:Spooler"][1], clock=c)
check("never settles: not verified at the 30 s ceiling", r.outcome in ("rolled-back", "rollback-failed")
      and any(x.startswith("rule 1") for x in r.reasons), r.outcome)
check("gave up at the ceiling, not before", 30.0 <= c.t - 1000.0 < 31.0 + 30.0, str(c.t - 1000.0))
tgt, host, d = lagged(0, raises=2)
r = run(tgt, d, FIX["service:Spooler"][1])
check("verifier errors then recovers: verified, errors were not passes", r.outcome == "verified"
      and r.reads == 3, f"{r.outcome} {r.reads}")
tgt, host, d = lagged(0, raises=10_000)
r = run(tgt, d, FIX["service:Spooler"][1])
check("verifier never answers: rollback-failed (nothing could be verified, not even the undo)",
      r.outcome == "rollback-failed" and any("verifier error" in x for x in r.reasons), r.outcome)

print("case 9b a clock that never moves cannot hang the agent")
tgt, host, d = lagged(10_000)
r = M.remediate(tgt, base, d, FIX["service:Spooler"][1], YES, clock=lambda: 0.0, sleep=lambda s: None)
check("stalled clock: still stops, at the read bound (30/1.0 + 2 = 32 verify reads)",
      r.outcome in ("rolled-back", "rollback-failed") and r.reads >= 32, f"{r.outcome} {r.reads}")

print("case 10 never retried")


class Counting:
    def __init__(self, inner):
        self.inner, self.applies, self.rollbacks = inner, 0, 0

    def menu(self):
        return self.inner.menu()

    def preview(self, call):
        return self.inner.preview(call)

    def apply(self, call):
        self.applies += 1
        return self.inner.apply(call)

    def rollback(self, rep):
        self.rollbacks += 1
        return self.inner.rollback(rep)


tgt, host, d = drifted("audit:Logon", broken=True)
cnt = Counting(tgt.remediator)
r = run(T.Target(reader=tgt.reader, remediator=cnt), d, FIX["audit:Logon"][1])
check("one apply, one rollback, for a failed fix", (cnt.applies, cnt.rollbacks) == (1, 1),
      f"{cnt.applies}/{cnt.rollbacks}")
tgt, host, d = drifted("audit:Logon")
cnt = Counting(tgt.remediator)
run(T.Target(reader=tgt.reader, remediator=cnt), d, FIX["audit:Logon"][1])
check("one apply, no rollback, for a good fix", (cnt.applies, cnt.rollbacks) == (1, 0))

print()
print(f"FAIL: {len(failures)} failing" if failures else "ok: all checks pass")
sys.exit(1 if failures else 0)
