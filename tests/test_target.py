"""P05 build step 1: the Target seam, the fake target and the baseline (CI, no model, no VM).

  case 1  the baseline is the answer key: it loads, one item per class, and a malformed one is
          REFUSED (unknown class, wrong fields, a value outside the allowed set, a duplicate id)
  case 2  the fake starts at baseline, and the console (host.set) is what drift looks like
  case 3  reader and remediator are separate, and the reader cannot reach a remediator
  case 4  preview changes nothing and lists every requested field
  case 5  apply captures the before-state; rollback restores it exactly
  case 6  the menu refuses: off-menu, wrong class, unknown field, value outside the set -
          and a refusal leaves the machine untouched
  case 7  BrokenRemediator reports success while the machine says otherwise - the gap the
          verifier (step 2) exists to see

Run:  python tests/test_target.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import target as T                                    # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def refused(fn, exc=Exception) -> bool:
    try:
        fn()
    except exc:
        return True
    return False


base = T.load_baseline()
by_id = {w.id: w for w in base}

print("case 1  the baseline is the answer key")
check("three items, one per class", sorted(w.cls for w in base) == ["audit", "registry", "service"])
check("Spooler disabled + stopped", by_id["service:Spooler"].fields ==
      {"start_type": "disabled", "status": "stopped"})
check("Logon = Success and Failure", by_id["audit:Logon"].fields == {"setting": "Success and Failure"})
check("RDP NLA = 1", by_id["registry:rdp-nla"].fields == {"value": 1})
raw = json.loads(T.BASELINE_PATH.read_text(encoding="utf-8"))


def bad(mutate) -> bool:
    doc = json.loads(json.dumps(raw))
    mutate(doc)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(doc, f)
    return refused(lambda: T.load_baseline(Path(f.name)), T.BaselineError)


check("refuses an unknown class", bad(lambda d: d["watched"][0].update({"class": "firewall"})))
check("refuses a missing field", bad(lambda d: d["watched"][0]["fields"].pop("status")))
check("refuses a value outside the set",
      bad(lambda d: d["watched"][0]["fields"].update({"start_type": "boot"})))
check("refuses a duplicate id", bad(lambda d: d["watched"][2].update({"id": d["watched"][0]["id"]})))
check("refuses two of one class", bad(lambda d: d["watched"][2].update(
    {"class": "service", "fields": {"start_type": "manual", "status": "running"}})))

print("case 2  the fake starts at baseline; the console is drift")
tgt, host = T.fake_target(base)
check("every reader value == baseline", all(tgt.reader.read(w.id) == w.fields for w in base))
check("host() names the machine", tgt.reader.host() == "FAKE-SRV01")
host.set("service:Spooler", start_type="automatic", status="running")
check("drift is visible to the reader",
      tgt.reader.read("service:Spooler") == {"start_type": "automatic", "status": "running"})
check("a read is a copy (mutating it changes nothing)",
      (tgt.reader.read("audit:Logon").update(setting="x"), tgt.reader.read("audit:Logon"))[1]
      == {"setting": "Success and Failure"})

print("case 3  reader and remediator are separate")
check("Target refuses one object as both",
      refused(lambda: T.Target(reader=tgt.remediator, remediator=tgt.remediator), ValueError))
WRITERS = ("apply", "rollback", "set", "_write")
reach = [k for k, v in vars(tgt.reader).items()
         if isinstance(v, (T.FakeRemediator, T.FakeHost)) or any(hasattr(v, m) for m in WRITERS)]
check("the reader holds no host, no remediator, nothing that writes", reach == [], str(reach))
check("the reader has no write method",
      not any(hasattr(tgt.reader, m) for m in WRITERS + ("write",)))

print("case 4  preview changes nothing")
tgt, host = T.fake_target(base)
host.set("service:Spooler", start_type="automatic", status="running")
before = host.snapshot()
fix = T.Call("Set-AgentServiceConfig", "service:Spooler", {"start_type": "disabled", "status": "stopped"})
pv = tgt.remediator.preview(fix)
check("machine unchanged by preview", host.snapshot() == before)
check("lists both requested fields", [(c.field, c.before, c.after) for c in pv] ==
      [("start_type", "automatic", "disabled"), ("status", "running", "stopped")])
same = tgt.remediator.preview(T.Call("Set-AgentAuditSetting", "audit:Logon",
                                     {"setting": "Success and Failure"}))
check("an unchanged field is still listed, marked 'no change'",
      [(c.field, c.effect) for c in same] == [("setting", "no change")])

print("case 5  apply captures before; rollback restores it")
rep = tgt.remediator.apply(fix)
check("before-state captured", rep.before == {"start_type": "automatic", "status": "running"})
check("machine now matches the request", tgt.reader.read("service:Spooler") == fix.values)
others = {k: v for k, v in host.snapshot().items() if k != "service:Spooler"}
check("nothing else moved", others == {k: v for k, v in before.items() if k != "service:Spooler"})
tgt.remediator.rollback(rep)
check("rollback restores the before-state exactly", host.snapshot() == before)

print("case 6  the menu refuses, and a refusal touches nothing")
tgt, host = T.fake_target(base)
snap = host.snapshot()
R = T.RemediationRefused
check("off-menu function", refused(lambda: tgt.remediator.apply(
    T.Call("Remove-Item", "service:Spooler", {"status": "stopped"})), R))
check("wrong class for the function", refused(lambda: tgt.remediator.apply(
    T.Call("Set-AgentRegistryValue", "service:Spooler", {"status": "stopped"})), R))
check("unwatched item", refused(lambda: tgt.remediator.apply(
    T.Call("Set-AgentServiceConfig", "service:WinRM", {"status": "stopped"})), R))
check("unknown field", refused(lambda: tgt.remediator.apply(
    T.Call("Set-AgentServiceConfig", "service:Spooler", {"path": "x"})), R))
check("value outside the set", refused(lambda: tgt.remediator.apply(
    T.Call("Set-AgentRegistryValue", "registry:rdp-nla", {"value": 2})), R))
check("empty values", refused(lambda: tgt.remediator.apply(
    T.Call("Set-AgentAuditSetting", "audit:Logon", {})), R))
check("preview refuses the same way", refused(lambda: tgt.remediator.preview(
    T.Call("Remove-Item", "service:Spooler", {"status": "stopped"})), R))
check("machine untouched by every refusal", host.snapshot() == snap)
check("menu is exactly the three functions", tgt.remediator.menu() ==
      ["Set-AgentAuditSetting", "Set-AgentRegistryValue", "Set-AgentServiceConfig"])

print("case 7  BrokenRemediator: the report lies, the machine does not")
tgt, host = T.fake_target(base, broken=True)
host.set("audit:Logon", setting="No Auditing")
rep = tgt.remediator.apply(T.Call("Set-AgentAuditSetting", "audit:Logon",
                                  {"setting": "Success and Failure"}))
check("report claims the requested value", rep.reported_after == {"setting": "Success and Failure"})
seen = tgt.reader.read("audit:Logon")
check("machine holds a different value", seen != rep.reported_after, str(seen))
check("the wrong value is still an allowed one (it looks plausible)",
      seen["setting"] in T.ALLOWED["audit"]["setting"])
tgt.remediator.rollback(rep)
check("its rollback is honest: the before-state comes back",
      tgt.reader.read("audit:Logon") == {"setting": "No Auditing"})
tgt, host = T.fake_target(base, broken="broken-rollback")
host.set("audit:Logon", setting="No Auditing")
rep = tgt.remediator.apply(T.Call("Set-AgentAuditSetting", "audit:Logon",
                                  {"setting": "Success and Failure"}))
tgt.remediator.rollback(rep)
check("broken-rollback: the rollback writes a wrong value too",
      tgt.reader.read("audit:Logon") != {"setting": "No Auditing"})

print()
print(f"{'FAIL' if failures else 'ok'}: {len(failures)} failing" if failures else "ok: all checks pass")
sys.exit(1 if failures else 0)
