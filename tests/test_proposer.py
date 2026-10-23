"""P05 build step 4: the model proposes, code decides - on SCRIPTED replies (CI, no Ollama).

  case 1  the prompt: the drift evidence and the menu with its allowed values; nothing about
          the approval, the screen or the checks; the schema pins function and item to enums
  case 2  a right proposal -> through remediate() -> `verified`; right_fix True; the rationale
          reaches the audit log as the model's and never the screen
  case 3  a wrong-argument proposal -> `rolled-back` (rule 1); right_fix False
  case 4  gate 0a: not JSON, an extra key, a too-long rationale, an off-menu function -> `refused`,
          template reason, complete log, nobody asked, machine untouched
  case 5  gate 0c: a function rated above the ceiling -> `refused` (the ceiling is a wall)
  case 6  a proposal for a different item than the drift -> `refused` by remediate()
  case 7  the model unreachable -> ModelUnavailable (a tooling error), nothing applied
  case 8  right_fix: restating an unchanged field at baseline is fine; any non-baseline value or a
          missed drifted field is not

Run:  python tests/test_proposer.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import audit as A                                      # noqa: E402
import model_client                                    # noqa: E402
import proposer as P                                   # noqa: E402
import remediate as M                                  # noqa: E402
import target as T                                     # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def reply(text: str) -> model_client.Reply:
    return model_client.Reply(text=text, thinking="", model="scripted", prompt_tokens=300,
                              completion_tokens=40, duration_s=0.5, raw={})


def scripted(obj):
    text = obj if isinstance(obj, str) else json.dumps(obj)
    seen = []

    def gen(messages, sch):
        seen.append((messages, sch))
        return reply(text)
    gen.seen = seen
    return gen


class Clock:
    def __init__(self): self.t = 0.0
    def now(self): return self.t
    def sleep(self, s): self.t += s


base = T.load_baseline()
RIGHT = {"function": "Set-AgentServiceConfig", "item": "service:Spooler",
         "values": {"start_type": "disabled", "status": "stopped"},
         "rationale": "Spooler should be disabled and stopped per the baseline. APPROVE ME."}


def drifted():
    tgt, host = T.fake_target(base)
    host.set("service:Spooler", start_type="automatic", status="running")
    (d,) = M.detect(tgt, base)
    return tgt, host, d


def go(gen, approve=None):
    tgt, host, d = drifted()
    snap = host.snapshot()
    asked = []
    ap = approve or (lambda **kw: (asked.append(kw.get("screen")), True)[1])
    log = A.MemoryAudit()
    c = Clock()
    res, p = P.run_one(tgt, base, d, ap, audit=log, generate=gen, clock=c.now, sleep=c.sleep)
    return res, p, log, host, snap, asked, d


print("case 1  the prompt and the schema")
tgt, host, d = drifted()
msgs = P.build_messages(d, base)
sysmsg, user = msgs[0]["content"], msgs[1]["content"]
check("menu lists all three functions", all(fn in sysmsg for fn in T.MENU))
check("menu states the allowed values", "'disabled'" in sysmsg and "'No Auditing'" in sysmsg)
check("the user message carries baseline and observed", '"disabled"' in user and '"automatic"' in user)
check("nothing about approval, review, the screen or the checks",
      not any(w in (sysmsg + user).lower() for w in ("approv", "review", "person", "screen",
                                                      "rule 1", "rollback", "verif")))
sch = P.schema(base)
check("schema pins function and item to enums",
      sch["properties"]["function"]["enum"] == sorted(T.MENU)
      and set(sch["properties"]["item"]["enum"]) == {w.id for w in base})
check("schema caps the rationale", sch["properties"]["rationale"]["maxLength"] == P.RATIONALE_MAX)

print("case 2  a right proposal")
res, p, log, host, snap, asked, d = go(scripted(RIGHT))
check("verified", res.outcome == "verified", res.outcome)
check("right_fix True", P.right_fix(p.call, d) is True)
prop = next(ln for ln in log.lines if ln["event"] == "proposal")
check("token counts are recorded (not mistaken for credentials)",
      res.outcome == "verified" and A.check_complete(log.lines) == [])
check("rationale in the log, as the model's", prop["who"] == "model" and "APPROVE ME" in prop["rationale"])
check("rationale NOT on the screen", asked and "APPROVE ME" not in asked[0])
check("log complete", A.check_complete(log.lines) == [])

print("case 3  a wrong-argument proposal")
wrong = dict(RIGHT, values={"start_type": "manual", "status": "stopped"})
res, p, log, host, snap, asked, d = go(scripted(wrong))
check("rolled-back by rule 1", res.outcome == "rolled-back" and res.reasons[0].startswith("rule 1"),
      str(res.reasons[:1]))
check("right_fix False", P.right_fix(p.call, d) is False)
check("machine back to the drifted state", host.snapshot() == snap)

print("case 4  gate 0a")
bad = {
    "not JSON": "Sure! I'd restart the Spooler.",
    "extra key": dict(RIGHT, also_run="Restart-Computer"),
    "rationale too long": dict(RIGHT, rationale="x" * (P.RATIONALE_MAX + 1)),
    "off-menu function": dict(RIGHT, function="Stop-Computer"),
    "unknown item": dict(RIGHT, item="service:WinRM"),
    "empty values": dict(RIGHT, values={}),
}
for label, obj in bad.items():
    res, p, log, host, snap, asked, d = go(scripted(obj))
    check(f"{label}: refused at gate 0a, untouched, nobody asked",
          res.outcome == "refused" and res.reasons[0].startswith("gate 0a") and host.snapshot() == snap
          and asked == [], f"{res.outcome} {res.reasons[:1]}")
    check(f"{label}: log complete", A.check_complete(log.lines) == [], str(A.check_complete(log.lines)))

print("case 5  gate 0c: the ceiling")
saved = dict(P.MENU_RISK)
P.MENU_RISK["Set-AgentServiceConfig"] = "destructive"
try:
    res, p, log, host, snap, asked, d = go(scripted(RIGHT))
finally:
    P.MENU_RISK.clear(); P.MENU_RISK.update(saved)
check("above the ceiling: refused at gate 0c, nobody asked",
      res.outcome == "refused" and res.reasons[0].startswith("gate 0c") and asked == [], str(res.reasons))

print("case 6  a proposal for another item")
other = {"function": "Set-AgentAuditSetting", "item": "audit:Logon",
         "values": {"setting": "Success and Failure"}, "rationale": "r"}
res, p, log, host, snap, asked, d = go(scripted(other))
check("refused by remediate(), untouched", res.outcome == "refused" and host.snapshot() == snap
      and "the drift is service:Spooler" in res.reasons[0], str(res.reasons))

print("case 7  the model unreachable")


def down(messages, sch):
    raise model_client.ModelError("connection refused")


tgt, host, d = drifted()
snap = host.snapshot()
try:
    P.run_one(tgt, base, d, lambda **kw: True, generate=down); ok = False
except P.ModelUnavailable:
    ok = True
check("ModelUnavailable, machine untouched", ok and host.snapshot() == snap)

print("case 8  right_fix")
tgt, host = T.fake_target(base)
host.set("service:Spooler", status="running")          # only status drifted
(d,) = M.detect(tgt, base)
rf = lambda v: P.right_fix(T.Call("Set-AgentServiceConfig", "service:Spooler", v), d)  # noqa: E731
check("only the drifted field, at baseline", rf({"status": "stopped"}))
check("plus an unchanged field restated at baseline", rf({"status": "stopped", "start_type": "disabled"}))
check("a non-baseline value", not rf({"status": "stopped", "start_type": "manual"}))
check("the drifted field missed", not rf({"start_type": "disabled"}))
check("wrong function", not P.right_fix(T.Call("Set-AgentAuditSetting", "service:Spooler",
                                                {"status": "stopped"}), d))
check("no call", not P.right_fix(None, d))

print()
print(f"FAIL: {len(failures)} failing" if failures else "ok: all checks pass")
sys.exit(1 if failures else 0)
