"""P05 build step 3: the approval screen and the audit log (CI, no model, no VM).

  case 1  the screen: fixed order, host read live, baseline vs observed, the checks, the
          automatic-undo line, the before-state - and NO model rationale (ADR 0038 amendment 1)
  case 2  the console asker: draw, THEN clear typeahead, THEN read; only `yes` approves;
          EOF/Ctrl+C decline; a fast yes is flagged hasty
  case 3  the file log: JSONL, seq 1..n; refuses an unknown event or `who`, and anything that
          looks like a credential - writing nothing; a disk failure is AuditError
  case 4  every outcome leaves a complete log (check_complete == []); intent before applied;
          the rationale is on the record as the model's
  case 5  fail-closed: an audit failure at ANY line before apply raises and changes nothing;
          after apply, verification and rollback still run and the log admits it is incomplete
  case 6  state-bound approval: the machine changes while the screen is up -> declined, no intent
  case 7  check_complete catches tampering: edited screen, dropped intent, a reordered line,
          a missing `who`, a credential
  case 8  end to end with the console asker scripted: the screen printed is the screen hashed

Run:  python tests/test_audit_and_screen.py
"""

from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import audit as A                                      # noqa: E402
import remediate as M                                  # noqa: E402
import remediation_gate as G                           # noqa: E402
import target as T                                     # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


base = T.load_baseline()
FIX = T.Call("Set-AgentServiceConfig", "service:Spooler", {"start_type": "disabled", "status": "stopped"})
RATIONALE = "Spooler is running and set to automatic; the baseline says disabled and stopped. TRUST ME."
YES = lambda **kw: True                               # noqa: E731
class Clock:
    def __init__(self): self.t = 0.0
    def now(self): return self.t
    def sleep(self, s): self.t += s


def nowait():
    c = Clock()
    return dict(clock=c.now, sleep=c.sleep)


def drifted(broken=False):
    tgt, host = T.fake_target(base, broken=broken)
    host.set("service:Spooler", start_type="automatic", status="running")
    (d,) = M.detect(tgt, base, clock=lambda: 1792467600.0)
    return tgt, host, d


def run(tgt, d, approve=YES, audit=None, call=FIX):
    log = audit if audit is not None else A.MemoryAudit()
    r = M.remediate(tgt, base, d, call, approve, audit=log, rationale=RATIONALE, **nowait())
    return r, log


print("case 1  the screen")
tgt, host, d = drifted()
r, log = run(tgt, d)
s = r.screen
heads = [h for h in ("Host     :", "Drift    :", "Action   :", "Change   :", "Checks   :", "Undo     :",
                     "Screen   :") if h in s]
check("seven sections, in fixed order", heads == sorted(heads, key=s.index) and len(heads) == 7)
check("host is the one the reader reported", "Host     : FAKE-SRV01" in s)
check("baseline and observed both shown, drifted fields marked",
      "disabled" in s and "automatic" in s and s.count("<- drifted") == 2)
check("read time shown in UTC", "2026-10-20T" in s)
check("the three checks are listed", all(f"    {n}." in s for n in (1, 2, 3)))
check("the settling ceiling is stated", "within 30 s counts as a FAIL" in s)
check("the automatic undo is stated", "WITHOUT ASKING" in s)
check("the undo shows the before-state", "start_type -> automatic" in s and "status -> running" in s)
check("NO rationale on the screen (amendment 1)", "TRUST ME" not in s and "Model" not in s
      and "rationale" not in s.lower())
fp1 = G.fingerprint("FAKE-SRV01", "service:Spooler", {"start_type": "automatic", "status": "running"},
                    FIX, r.preview)
fp2 = G.fingerprint("FAKE-SRV01", "service:Spooler", {"start_type": "manual", "status": "running"},
                    FIX, r.preview)
check("a different before-state is a different screen", fp1 != fp2 and fp1 in s)

print("case 2  the console asker")
order = []
ask = G.console_asker(read=lambda p: (order.append("read"), "yes")[1],
                      write=lambda t: order.append("write"), drain=lambda: (order.append("drain"), True)[1],
                      clock=iter([0.0, 5.0]).__next__)
a = ask("SCREEN")
check("draw, then clear typeahead, then read", order == ["write", "drain", "read"], str(order))
check("'yes' approves, not hasty at 5 s", a.approved is True and a.hasty is False and a.seconds == 5.0)
for typed in ("y", "", "no", "yes please", "ok"):
    a = G.console_asker(read=lambda p, t=typed: t, write=lambda t: None, drain=lambda: True)("S")
    check(f"{typed!r} declines", a.approved is False)
a = G.console_asker(read=lambda p: "  YES ", write=lambda t: None, drain=lambda: True)("S")
check("' YES ' approves (trimmed, any case)", a.approved is True)


def eof(p):
    raise EOFError


a = G.console_asker(read=eof, write=lambda t: None, drain=lambda: True)("S")
check("EOF declines, with a reason", a.approved is False and "end of input" in a.reason)
a = G.console_asker(read=lambda p: "yes", write=lambda t: None, drain=lambda: False,
                    clock=iter([0.0, 0.4]).__next__)("S")
check("a 0.4 s yes is flagged hasty", a.hasty is True)
check("an uncleared buffer is noted", "typeahead not cleared" in a.reason)

print("case 3  the file log")
tmp = Path(tempfile.mkdtemp()) / "r.audit.jsonl"
log = A.AuditLog("r1", path=tmp, clock=lambda: 1.0)
log.write("drift", "code", item="x")
log.write("proposal", "model", rationale="r")
lines = A.read_log(tmp)
check("JSONL, seq 1..2, run_id on every line", [ln["seq"] for ln in lines] == [1, 2]
      and {ln["run_id"] for ln in lines} == {"r1"})
for label, call in [("unknown event", lambda: log.write("deleted", "code")),
                    ("unknown who", lambda: log.write("drift", "operator")),
                    ("credential field name", lambda: log.write("drift", "code", password="x")),
                    ("credential in a value", lambda: log.write("drift", "code", note="password=hunter2")),
                    ("private key in a value", lambda: log.write("drift", "code", k="-----BEGIN RSA PRIVATE KEY")),
                    ("nested credential", lambda: log.write("drift", "code", cfg={"api_key": "x"}))]:
    try:
        call(); ok = False
    except A.AuditError:
        ok = True
    check(f"refuses {label}", ok)
check("refusals wrote nothing", len(A.read_log(tmp)) == 2)
log.write("proposal", "model", prompt_tokens=300, completion_tokens=40)
check("token COUNTS are not credentials", len(A.read_log(tmp)) == 3)
for k in ("token", "auth_token", "Bearer"):
    try:
        log.write("drift", "code", **{k: "x"}); ok = False
    except A.AuditError:
        ok = True
    check(f"refuses a field named {k!r}", ok)


def broken_disk(*a, **k):
    raise OSError(28, "No space left on device")


try:
    A.AuditLog("r2", path=tmp, opener=broken_disk).write("drift", "code"); ok = False
except A.AuditError as e:
    ok = "No space left" in str(e)
check("a disk failure is AuditError, naming the cause", ok)

print("case 4  every outcome leaves a complete log")
cases = {
    "verified": (False, YES, FIX),
    "rolled-back": (True, YES, FIX),
    "rollback-failed": ("broken-rollback", YES, FIX),
    "declined": (False, lambda **kw: False, FIX),
    "refused": (False, YES, T.Call("Stop-Computer", "service:Spooler", {"status": "stopped"})),
}
for want, (broken, approve, call) in cases.items():
    tgt, host, d = drifted(broken)
    r, log = run(tgt, d, approve=approve, call=call)
    probs = A.check_complete(log.lines)
    check(f"{want}: outcome and complete log", r.outcome == want and probs == [], f"{r.outcome} {probs}")
tgt, host, d = drifted()
r, log = run(tgt, d)
ev = [ln["event"] for ln in log.lines]
check("intent is written before applied", ev.index("intent") < ev.index("applied"), str(ev))
prop = next(ln for ln in log.lines if ln["event"] == "proposal")
check("the rationale is on the record, as the model's", prop["who"] == "model" and prop["rationale"] == RATIONALE)
check("the applied line says the report is not trusted",
      next(ln for ln in log.lines if ln["event"] == "applied")["trusted"] is False)
check("every line names who decided", all(ln["who"] in A.WHO for ln in log.lines))

print("case 5  fail-closed")
PRE = ["drift", "proposal", "preview", "screen", "answer", "recheck", "intent"]
for k, ev in enumerate(PRE):
    tgt, host, d = drifted()
    snap = host.snapshot()
    applied = []
    real = tgt.remediator.apply
    tgt.remediator.apply = lambda c, real=real: (applied.append(c), real(c))[1]
    try:
        run(tgt, d, audit=A.MemoryAudit(fail_after=k)); raised = False
    except A.AuditError:
        raised = True
    check(f"audit fails at '{ev}': raises, never applies, machine untouched",
          raised and applied == [] and host.snapshot() == snap)
tgt, host, d = drifted()
log = A.MemoryAudit(fail_after=len(PRE))          # the 'applied' line fails
r, _ = run(tgt, d, audit=log)
check("audit fails after apply: verification still ran", r.outcome == "verified" and M.detect(tgt, base) == [])
check("... and the result says the audit is incomplete", r.audit_incomplete is True)
check("... and check_complete refuses that log", A.check_complete(log.lines) != [])
tgt, host, d = drifted(True)
r, _ = run(tgt, d, audit=A.MemoryAudit(fail_after=len(PRE)))
check("audit fails after a bad apply: rollback still ran", r.outcome == "rolled-back"
      and tgt.reader.read("service:Spooler") == {"start_type": "automatic", "status": "running"})

print("case 6  the machine changes while the screen is up")
tgt, host, d = drifted()


def meddle(**kw):
    host.set("service:Spooler", start_type="manual")     # someone at the console
    return True


r, log = run(tgt, d, approve=meddle)
ev = [ln["event"] for ln in log.lines]
check("declined, with the reason", r.outcome == "declined" and "changed after the screen" in r.reasons[0])
check("no intent, no apply", "intent" not in ev and "applied" not in ev)
check("the log is still complete", A.check_complete(log.lines) == [])

print("case 7  check_complete catches tampering")
tgt, host, d = drifted()
r, log = run(tgt, d)
good = log.lines


def tamper(fn):
    lines = copy.deepcopy(good)
    fn(lines)
    return A.check_complete(lines)


check("an edited screen", any("sha256" in p for p in tamper(
    lambda L: next(x for x in L if x["event"] == "screen").update(text="different"))))
check("a dropped intent", tamper(lambda L: L.pop([x["event"] for x in L].index("intent"))) != [])
check("a reordered line", any("seq" in p for p in tamper(lambda L: L.insert(0, L.pop(3)))))
check("a missing who", tamper(lambda L: L[2].pop("who")) != [])
check("a credential", tamper(lambda L: L[0].update(note="password: x")) != [])
check("no outcome line", tamper(lambda L: L.pop()) != [])
check("the untouched log is complete", A.check_complete(good) == [])

print("case 8  end to end with the console asker")
tgt, host, d = drifted()
shown = []
ask = G.console_asker(read=lambda p: "yes", write=shown.append, drain=lambda: True,
                      clock=iter([0.0, 6.0]).__next__)
r, log = run(tgt, d, approve=lambda screen, **kw: ask(screen))
ans = next(ln for ln in log.lines if ln["event"] == "answer")
scr = next(ln for ln in log.lines if ln["event"] == "screen")
check("verified", r.outcome == "verified")
check("the screen printed is the screen hashed", shown == [scr["text"]] and A.sha256(shown[0]) == scr["sha256"])
check("the answer is recorded: typed 'yes', 6 s, not hasty",
      ans["approved"] is True and ans["answer"] == "yes" and ans["seconds"] == 6.0 and ans["hasty"] is False)

print()
print(f"FAIL: {len(failures)} failing" if failures else "ok: all checks pass")
sys.exit(1 if failures else 0)
