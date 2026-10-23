"""P05 build step 8: the agent's SRV01 door - SrvRemediator, named parameters, the pause
(CI - no VM, no model).

The real writes happen on SRV01 through AgentRemediator as svc-agent-rw. What CI CAN check is
everything around them, with scripted sessions that answer the way the endpoints do - the
remediator's deep rows (nested Before/Changes/ReportedAfter, an int registry value), the
reader's flat strings - both backed by one FakeHost, so a write is visible to the next read:

  case 1  identity: the fixer refuses any session but svc-agent-rw on AgentRemediator
  case 2  the menu check runs BEFORE the wire: off-menu / wrong class / bad value never sent
  case 3  parameters: field -> PowerShell name, -Preview only on preview, the item never sent
  case 4  preview -> typed Changes; apply -> ApplyReport (registry int); rollback writes before
  case 5  refusals: wrong Item answered, two rows, a "preview" that applied, no before-value
  case 6  host_client: a parameter is a plain name and a plain value, nothing else; the
          remediator's expected list is exactly the menu
  case 7  the CLI, --target srv01: a clean fix -> 0; the pause sabotage -> rolled-back, 1,
          audit complete with the operator's line (who: human); usage and unreachable -> 2

Run:  python tests/test_srv_remediator.py
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import audit as A                                      # noqa: E402
import host_client as H                                # noqa: E402
import model_client                                    # noqa: E402
import remediate as R                                  # noqa: E402
import remediation_agent as agent                      # noqa: E402
import target as T                                     # noqa: E402

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


BASE = T.load_baseline()
GUID = T.AUDIT_GUIDS["audit:Logon"]
FIELD = {v: k for k, v in T.PS_PARAM.items()}           # StartType -> start_type
ITEM_OF = {fn: next(w.id for w in BASE if w.cls == c) for fn, c in T.MENU.items()}


class FixerSession:
    """A scripted AgentRemediator: computes Before from the host, writes unless -Preview."""

    def __init__(self, host, user="CORP\\svc-agent-rw", endpoint="AgentRemediator"):
        self.server, self.username, self.configuration_name = "10.10.10.20", user, endpoint
        self.host, self.calls, self.tamper = host, [], None

    def call(self, fn, params=None, *, deep=False):
        params = dict(params or {})
        self.calls.append((fn, dict(params), deep))     # a COPY: the record must not change
        item = ITEM_OF[fn]
        preview = params.pop("Preview", False)
        req = {FIELD[k]: v for k, v in params.items()}
        before = dict(self.host.snapshot()[item])
        changes = [{"Field": f, "Before": before[f], "After": v,
                    "Effect": "no change" if before[f] == v else "set"} for f, v in req.items()]
        if not preview:
            self.host.set(item, **req)
        row = {"Host": "SRV01", "Item": item, "Before": before, "Requested": req,
               "Changes": changes, "Applied": not preview,
               "ReportedAfter": None if preview else dict(self.host.snapshot()[item])}
        if self.tamper:
            row = self.tamper(row)
        return SimpleNamespace(rows=tuple(row if isinstance(row, list) else [row]))


class ReaderSession:
    """A scripted AgentReader on the same host: flat rows of strings, as PSRP gives them."""

    def __init__(self, host):
        self.server, self.username, self.configuration_name = "10.10.10.20", "CORP\\svc-agent-ro", "AgentReader"
        self.host = host

    def call(self, cmd):
        s = self.host.snapshot()
        if cmd == "Get-AgentServiceState":
            rows = [{"Item": "service:Spooler", **{k: str(v) for k, v in s["service:Spooler"].items()}}]
        elif cmd == "Get-AgentRegistryState":
            rows = [{"Item": "registry:rdp-nla", "value": str(s["registry:rdp-nla"]["value"])}]
        else:
            rows = [{"Subcategory": "Logon", "Guid": GUID, "Setting": s["audit:Logon"]["setting"]}]
        return SimpleNamespace(rows=tuple(rows))


def rig():
    host = T.FakeHost(BASE, name="10.10.10.20")
    fixer = FixerSession(host)
    return host, fixer, T.SrvRemediator(fixer, BASE)


print("case 1  identity")
host, fixer, rem = rig()
check("svc-agent-rw on AgentRemediator -> accepted", rem.menu() == sorted(T.MENU))
check("svc-agent-ro -> refused (the checker cannot be the fixer)",
      refused(lambda: T.SrvRemediator(FixerSession(host, user="CORP\\svc-agent-ro"), BASE), ValueError))
check("svc-agent-rw on AgentReader -> refused",
      refused(lambda: T.SrvRemediator(FixerSession(host, endpoint="AgentReader"), BASE), ValueError))
check("a session with no identity -> refused",
      refused(lambda: T.SrvRemediator(SimpleNamespace(call=None), BASE), ValueError))

print("\ncase 2  the menu check runs before anything crosses the wire")
for label, call in [
        ("off-menu function", T.Call("Remove-AgentRegistryKey", "registry:rdp-nla", {"value": 1})),
        ("function for the wrong class", T.Call("Set-AgentRegistryValue", "service:Spooler", {"value": 1})),
        ("value outside the ValidateSet", T.Call("Set-AgentRegistryValue", "registry:rdp-nla", {"value": 2})),
        ("a field the class does not have", T.Call("Set-AgentAuditSetting", "audit:Logon", {"path": "x"})),
        ("no values", T.Call("Set-AgentServiceConfig", "service:Spooler", {}))]:
    check(f"{label} -> RemediationRefused", refused(lambda: rem.preview(call), T.RemediationRefused))
check("  ...and none of them reached the session", fixer.calls == [], str(fixer.calls))

print("\ncase 3  parameters")
host, fixer, rem = rig()
host.set("service:Spooler", start_type="automatic", status="running")
call = T.Call("Set-AgentServiceConfig", "service:Spooler", {"start_type": "disabled", "status": "stopped"})
rem.preview(call)
fn, params, deep = fixer.calls[-1]
check("preview sends StartType/Status and -Preview, deep rows",
      (fn, params, deep) == ("Set-AgentServiceConfig",
                             {"StartType": "disabled", "Status": "stopped", "Preview": True}, True),
      str(fixer.calls[-1]))
check("  ...and changed nothing", host.snapshot()["service:Spooler"]["status"] == "running")
rem.apply(call)
check("apply sends no -Preview", "Preview" not in fixer.calls[-1][1], str(fixer.calls[-1]))
check("the item is never a parameter (fixed server-side)",
      all("Item" not in p and "Name" not in p for _, p, _ in fixer.calls))

print("\ncase 4  preview, apply, rollback")
host, fixer, rem = rig()
host.set("registry:rdp-nla", value=0)
call = T.Call("Set-AgentRegistryValue", "registry:rdp-nla", {"value": 1})
fixer.tamper = lambda row: {**row, "Before": {"value": str(row["Before"]["value"])},
                            "ReportedAfter": row["ReportedAfter"] and {"value": str(row["ReportedAfter"]["value"])},
                            "Changes": [{**c, "Before": str(c["Before"]), "After": str(c["After"])}
                                        for c in row["Changes"]]}
ch = rem.preview(call)
check("preview -> one Change, typed ints, effect set",
      [(c.field, c.before, c.after, c.effect) for c in ch] == [("value", 0, 1, "set")], str(ch))
rep = rem.apply(call)
check("apply -> ApplyReport, before {'value': 0} as int (even off a string)",
      rep.before == {"value": 0} and rep.reported_after == {"value": 1}, str(rep))
check("  ...and the machine changed", host.snapshot()["registry:rdp-nla"] == {"value": 1})
fixer.tamper = None
rb = rem.rollback(rep)
check("rollback writes the before-value through the same function",
      fixer.calls[-1][:2] == ("Set-AgentRegistryValue", {"Value": 0})
      and host.snapshot()["registry:rdp-nla"] == {"value": 0}, str(fixer.calls[-1]))
check("  ...and reports it", rb.before == {"value": 1} and rb.reported_after == {"value": 0})

print("\ncase 5  refusals - the fixer's answer is checked, not trusted")
audit_call = T.Call("Set-AgentAuditSetting", "audit:Logon", {"setting": "Success and Failure"})
for label, tamper, fn in [
        ("answered for another item", lambda r: {**r, "Item": "audit:Logoff"}, "preview"),
        ("two rows", lambda r: [r, r], "preview"),
        ("a 'preview' that says it applied", lambda r: {**r, "Applied": True}, "preview"),
        ("preview listing other fields", lambda r: {**r, "Changes": []}, "preview"),
        ("apply with no before-value", lambda r: {**r, "Before": {}}, "apply")]:
    host, fixer, rem = rig()
    fixer.tamper = tamper
    check(f"{label} -> ReadError", refused(lambda: getattr(rem, fn)(audit_call), T.ReadError))

print("\ncase 6  host_client parameters")
for label, params in [("a list value", {"Value": [1]}), ("a dict value", {"Value": {"a": 1}}),
                      ("an object value", {"Value": object()}), ("a float", {"Value": 1.0}),
                      ("a name with a dash", {"-Value": 1}), ("a name with a space", {"Va lue": 1}),
                      ("a name that is script", {"Value;Stop-Computer": 1})]:
    check(f"{label} -> HostError", refused(lambda: H.check_params(params), H.HostError))
check("str / int / bool -> accepted",
      not refused(lambda: H.check_params({"Setting": "Success", "Value": 1, "Preview": True})))
wired = H.Host("10.10.10.20", "CORP\\svc-agent-rw", "x", configuration_name="AgentRemediator",
               allowed=H.ALLOWED_REMEDIATOR)
sent = []
wired._invoke = lambda cmd, params=None: (sent.append((cmd, params)) or ([], 0.0))
check("Host.call itself refuses a bad value before invoking",
      refused(lambda: wired.call("Set-AgentRegistryValue", {"Value": [1]}), H.HostError) and sent == [])
wired.call("Set-AgentRegistryValue", {"Value": 1, "Preview": True}, deep=True)
check("  ...and passes good ones through as named parameters",
      sent == [("Set-AgentRegistryValue", {"Value": 1, "Preview": True})], str(sent))
check("ALLOWED_REMEDIATOR == the menu, exactly", tuple(sorted(T.MENU)) == H.ALLOWED_REMEDIATOR)
check("the fixer's list shares nothing with the reader's",
      not set(H.ALLOWED_REMEDIATOR) & set(H.ALLOWED_SRV01))

print("\ncase 7  the CLI, --target srv01")
OUT = Path(tempfile.mkdtemp(prefix="p05s8-"))


def reply(text):
    return model_client.Reply(text=text, thinking="", model="scripted", prompt_tokens=None,
                              completion_tokens=None, duration_s=0.0, raw={})


def right(messages, schema):
    item = re.search(r"Drifted item: (\S+)", messages[1]["content"]).group(1)
    w = next(w for w in BASE if w.id == item)
    fn = next(f for f, c in T.MENU.items() if c == w.cls)
    return reply(json.dumps({"function": fn, "item": item, "values": w.fields, "rationale": "s"}))


def srv_cli(argv, host, *, pause=None, opener=None):
    def open_(stack):
        return T.Target(reader=T.SrvReader(ReaderSession(host), BASE),
                        remediator=T.SrvRemediator(FixerSession(host), BASE), kind="srv01")
    now = [0.0]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            code = agent.main(["--target", "srv01", "--out", str(OUT), *argv],
                              approve=lambda **k: True, generate=right,
                              srv_open=opener or open_, pause=pause,
                              clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
        except SystemExit as exc:
            code = exc.code
    return code, buf.getvalue()


host = T.FakeHost(BASE, name="10.10.10.20")
code, text = srv_cli([], host)
check("SRV01 at baseline -> 0, nothing to fix", code == 0 and "Nothing to fix" in text, text[-90:])
host.set("audit:Logon", setting="No Auditing")             # the operator breaks it at the console
code, text = srv_cli([], host)
check("audit drift on SRV01 -> fixed, verified, 0", code == 0 and "OUTCOME: verified" in text, text[-160:])
log = A.read_log(Path(re.search(r"audit log: (\S+)", text).group(1)))
check("  ...audit complete, no pause line", A.check_complete(log) == []
      and "pause" not in [l["event"] for l in log])

host.set("audit:Logon", setting="No Auditing")
said = "set Logon to Failure at the console 19:02"
def saboteur():
    host.set("audit:Logon", setting="Failure")            # a THIRD value: rollback must write
    return said
code, text = srv_cli(["--pause-before-verify"], host, pause=saboteur)
check("pause sabotage -> rolled-back, exit 1", code == 1 and "OUTCOME: rolled-back" in text, text[-200:])
check("  ...SRV01 is back at its before-state (the drift), not the saboteur's value",
      host.snapshot()["audit:Logon"] == {"setting": "No Auditing"}, str(host.snapshot()["audit:Logon"]))
log = A.read_log(Path(re.search(r"audit log: (\S+)", text).group(1)))
ev = [l["event"] for l in log]
pause_line = next((l for l in log if l["event"] == "pause"), {})
check("  ...audit complete", A.check_complete(log) == [], str(A.check_complete(log)))
check("  ...pause sits between applied and verify, who: human, the operator's words",
      ev.index("applied") < ev.index("pause") < ev.index("verify")
      and pause_line.get("who") == "human" and pause_line.get("operator_note") == said, str(ev))

code, text = srv_cli(["--drift", "audit:Logon", "setting=Success"], host)
check("--drift with srv01 -> 2, refused as USAGE (not by some later crash)",
      code == 2 and "UsageError" in text and "a person breaks" in text, text[-120:])
code, text = srv_cli(["--sabotage", "broken"], host)
check("--sabotage with srv01 -> 2, refused as usage", code == 2 and "UsageError" in text)
buf = io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
    code = agent.main(["--target", "fake", "--pause-before-verify", "--out", str(OUT)])
check("--pause-before-verify with fake -> 2", code == 2, buf.getvalue()[-120:])


def unreachable(stack):
    raise R.TargetUnreachable("could not open 'AgentRemediator' on 10.10.10.20")
code, text = srv_cli([], host, opener=unreachable)
check("an endpoint that will not open -> 2, nothing applied",
      code == 2 and "Nothing was applied" in text, text[-120:])

print("\ncase 8  the console pause resumes only on a deliberate line (bench, 2026-10-21)")
lines = iter(["", "   ", "set Logon to Failure 20:12"])
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    got = agent.console_pause(read=lambda prompt: next(lines))
check("two empty Enters are ignored; the note is returned",
      got == "set Logon to Failure 20:12" and buf.getvalue().count("empty line ignored") == 2, repr(got))
with contextlib.redirect_stdout(io.StringIO()):
    got = agent.console_pause(read=lambda prompt: "none")
check("'none' resumes with no change, on the record", got == "none")

print()
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("all checks passed")
