"""P05 build step 7: the SRV01 verifier behind the Target seam (CI - no VM, no model).

The real reads happen on SRV01 through AgentReader. What CI CAN check is everything around
them, using a scripted session that answers the way PSRP does (every value a string):

  case 1  identity: the reader refuses any session that is not svc-agent-ro on AgentReader
  case 2  parity of SHAPE: at baseline, SrvReader returns exactly what FakeReader returns -
          same keys, same types (registry value an int, not "1"), so detect() sees no drift
  case 3  a transient service state is reported (the settling loop needs it), and is drift
  case 4  refusals: wrong item answered, zero or two rows, a non-integer value, an audit
          GUID missing or duplicated
  case 5  the reader holds nothing that writes, and calls only the SRV01 read list
  case 6  host_client: one expected list per endpoint - SRV01's list passes SRV01's surface,
          and P01's list still flags the extra reads as drift (the check still bites)

Run:  python tests/test_srv_reader.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import host_client as H                               # noqa: E402
import remediate as R                                 # noqa: E402
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


GUID = T.AUDIT_GUIDS["audit:Logon"]
AT_BASELINE = {
    "Get-AgentServiceState": [{"Host": "SRV01", "Item": "service:Spooler",
                               "start_type": "disabled", "status": "stopped"}],
    "Get-AgentRegistryState": [{"Host": "SRV01", "Item": "registry:rdp-nla", "value": "1"}],
    "Get-AgentAuditPolicy": [
        {"Computer": "SRV01", "Subcategory": "Logoff", "Guid": "{0CCE9216-69AE-11D9-BED3-505054503030}",
         "Setting": "Success"},
        {"Computer": "SRV01", "Subcategory": "Logon", "Guid": GUID, "Setting": "Success and Failure"},
    ],
}


class Session:
    """A scripted AgentReader session. Answers like PSRP: rows of strings."""

    def __init__(self, answers=None, user="CORP\\svc-agent-ro", endpoint="AgentReader"):
        self.server, self.username, self.configuration_name = "10.10.10.20", user, endpoint
        self.answers = {k: [dict(r) for r in v] for k, v in (answers or AT_BASELINE).items()}
        self.calls: list[str] = []

    def call(self, cmd):
        self.calls.append(cmd)
        return SimpleNamespace(rows=tuple(self.answers[cmd]))


baseline = T.load_baseline()

print("case 1  identity")
check("svc-agent-ro on AgentReader -> accepted", T.SrvReader(Session(), baseline).host() == "10.10.10.20")
check("svc-agent-rw -> refused (the fixer cannot be the checker)",
      refused(lambda: T.SrvReader(Session(user="CORP\\svc-agent-rw"), baseline), ValueError))
check("AgentRemediator endpoint -> refused",
      refused(lambda: T.SrvReader(Session(endpoint="AgentRemediator"), baseline), ValueError))
check("an admin account -> refused",
      refused(lambda: T.SrvReader(Session(user="CORP\\Administrator"), baseline), ValueError))

print("case 2  parity of shape with the fake, at baseline")
srv = T.SrvReader(Session(), baseline)
fake, _host = T.fake_target(baseline)
for w in baseline:
    a, b = srv.read(w.id), fake.reader.read(w.id)
    check(f"{w.id}: SRV01 read == fake read, types included", a == b and
          all(type(a[k]) is type(b[k]) for k in a), f"{a} vs {b}")
tgt = T.Target(reader=srv, remediator=fake.remediator, kind="srv01")
check("detect() on SRV01 at baseline -> no drift", R.detect(tgt, baseline) == [])

print("case 3  a transient state is reported, and is drift")
s = Session(); s.answers["Get-AgentServiceState"][0]["status"] = "start pending"
r = T.SrvReader(s, baseline).read("service:Spooler")
check("'start pending' comes through as read", r["status"] == "start pending", str(r))
d = R.detect(T.Target(reader=T.SrvReader(s, baseline), remediator=fake.remediator), baseline)
check("...and detect() names service:Spooler", [x.item for x in d] == ["service:Spooler"])
s = Session(); s.answers["Get-AgentRegistryState"][0]["value"] = "0"
d = R.detect(T.Target(reader=T.SrvReader(s, baseline), remediator=fake.remediator), baseline)
check("registry 0 -> drift on registry:rdp-nla, value an int",
      [(x.item, x.observed) for x in d] == [("registry:rdp-nla", {"value": 0})])


def bad(cmd, mutate, item):
    s = Session()
    mutate(s.answers[cmd])
    return refused(lambda: T.SrvReader(s, baseline).read(item), T.ReadError)


print("case 4  refusals")
check("the endpoint answers for a different item -> refused",
      bad("Get-AgentServiceState", lambda rows: rows[0].update(Item="service:W32Time"), "service:Spooler"))
check("zero rows -> refused", bad("Get-AgentServiceState", lambda rows: rows.clear(), "service:Spooler"))
check("two rows -> refused",
      bad("Get-AgentRegistryState", lambda rows: rows.append(dict(rows[0])), "registry:rdp-nla"))
check("value 'one' -> refused", bad("Get-AgentRegistryState", lambda rows: rows[0].update(value="one"),
                                    "registry:rdp-nla"))
check("value missing -> refused", bad("Get-AgentRegistryState", lambda rows: rows[0].pop("value"),
                                      "registry:rdp-nla"))
check("audit GUID absent -> refused", bad("Get-AgentAuditPolicy", lambda rows: rows.pop(), "audit:Logon"))
check("audit GUID twice -> refused",
      bad("Get-AgentAuditPolicy", lambda rows: rows.append(dict(rows[-1])), "audit:Logon"))
check("an item not in the baseline -> KeyError",
      refused(lambda: T.SrvReader(Session(), baseline).read("service:W32Time"), KeyError))
s = Session()
_ = [T.SrvReader(s, baseline).read(w.id) for w in baseline]
check("a ReadError inside detect() becomes TargetUnreachable (fail closed)",
      refused(lambda: R.detect(T.Target(reader=T.SrvReader(Session({**AT_BASELINE,
              "Get-AgentServiceState": []}), baseline), remediator=fake.remediator), baseline),
              R.TargetUnreachable))

print("case 5  nothing that writes; only the SRV01 read list")
WRITERS = ("apply", "rollback", "set", "_write", "write", "preview")
check("the reader has no write method", not any(hasattr(srv, m) for m in WRITERS))
reach = [k for k, v in vars(srv).items() if any(hasattr(v, m) for m in WRITERS)]
check("the reader holds nothing that writes", reach == [], str(reach))
check("every call is on host_client.ALLOWED_SRV01", set(s.calls) <= set(H.ALLOWED_SRV01), str(s.calls))
check("the read table and ALLOWED_SRV01 name the same functions",
      set(T.SRV01_READS.values()) == set(H.ALLOWED_SRV01))

print("case 6  host_client: one expected list per endpoint")
SRV01_SURFACE = tuple(sorted(H.SESSION_BUILTINS | set(H.ALLOWED_SRV01)))
DC01_SURFACE = tuple(sorted(H.SESSION_BUILTINS | set(H.ALLOWED)))


def drift(allowed, surface):
    h = H.Host("10.10.10.20", "CORP\\svc-agent-ro", "unused", allowed=allowed)
    h.visible_commands = lambda: surface
    try:
        h.check_drift()
        return None
    except H.HostError as exc:
        return str(exc)


check("SRV01's list vs SRV01's surface -> passes", drift(H.ALLOWED_SRV01, SRV01_SURFACE) is None)
check("P01's list vs DC01's surface -> passes (P01 unchanged)", drift(H.ALLOWED, DC01_SURFACE) is None)
msg = drift(H.ALLOWED, SRV01_SURFACE) or ""
check("P01's list vs SRV01's surface -> drift, naming the extra reads",
      "Get-AgentServiceState" in msg and "Get-AgentRegistryState" in msg, msg)
msg = drift(H.ALLOWED_SRV01, DC01_SURFACE) or ""
check("SRV01's list vs DC01's surface -> drift, naming what is missing", "missing=" in msg and
      "Get-AgentServiceState" in msg, msg)
h = H.Host("x", "CORP\\svc-agent-ro", "unused")
check("Host defaults to P01's list", h.allowed == H.ALLOWED)
check("call() refuses a read not on the session's list",
      refused(lambda: h.call("Get-AgentServiceState"), H.HostError))

if failures:
    print(f"\nFAILED ({len(failures)}): {'; '.join(failures)}")
    raise SystemExit(1)
print("\nall checks passed")
