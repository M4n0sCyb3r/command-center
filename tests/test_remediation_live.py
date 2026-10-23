"""P05 build step 7: the remediation suite's `live` tier - SRV01, READ-ONLY (CI: no VM, no model).

The six live cases read SRV01 through the verifier. CI cannot reach SRV01 (ADR 0010), so the
session is INJECTED: a scripted AgentReader session answering the way PSRP does. What is
proved here is the scoring and the boundaries; the bench run against the real SRV01 is the
evidence.

  case 1  the suite: 6 live cases, one detect and one parity per class, all srv01, no write
          fields (no model, approver, sabotage or expected_call)
  case 2  SRV01 at baseline -> all 6 pass; detail carries both reads
  case 3  drift -> the detect case FAILS naming the fields; a value the fake cannot represent
          ('start pending') -> parity FAILS, named
  case 4  unreachable / endpoint drift / malformed reading -> ScorerError (exit 2), never a row
  case 5  a live run never builds a remediator and never asks the model; the password is asked
          ONCE for the whole run, and never on --tier fixed
  case 6  conditions say what the run touched: read-only live -> no model, SRV01 named;
          fixed -> the fake only

Run:  python tests/test_remediation_live.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import model_client                                    # noqa: E402
import scorers                                         # noqa: E402
import target as T                                     # noqa: E402
from suite import parse_suite                          # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


SUITE_PATH = REPO_ROOT / "evals" / "suites" / "remediation.jsonl"
suite = parse_suite(SUITE_PATH)
live = [c for c in suite.cases if c["tier"] == "live"]

GUID = T.AUDIT_GUIDS["audit:Logon"]


def answers(**over):
    a = {"Get-AgentServiceState": [{"Item": "service:Spooler", "start_type": "disabled",
                                    "status": "stopped"}],
         "Get-AgentRegistryState": [{"Item": "registry:rdp-nla", "value": "1"}],
         "Get-AgentAuditPolicy": [{"Guid": GUID, "Subcategory": "Logon",
                                   "Setting": "Success and Failure"}]}
    for k, v in over.items():
        a[k] = v
    return a


class Session:
    opened = 0

    def __init__(self, ans=None, fail=None, user="CORP\\svc-agent-ro"):
        self.ans, self.fail = ans or answers(), fail
        self.server, self.username, self.configuration_name = "10.10.10.20", user, "AgentReader"

    def __enter__(self):
        if self.fail:
            raise self.fail
        Session.opened += 1
        return self

    def __exit__(self, *e):
        pass

    def call(self, cmd):
        return SimpleNamespace(rows=tuple(dict(r) for r in self.ans[cmd]))


def build(**kw):
    session = kw.pop("session", None) or Session()
    return scorers.make_remediation_scorer(live_text=lambda c, d: "{}", suite=suite,
                                           srv01_session=lambda: session, **kw)


print("case 1  the suite's live tier")
check("6 live cases, all target srv01", len(live) == 6 and all(c["target"] == "srv01" for c in live))
check("per class one detect and one parity",
      sorted((c["class"], c["check"]) for c in live) ==
      sorted((k, ch) for k in ("service", "audit", "registry") for ch in ("detect", "parity")))
WRITE_FIELDS = {"model", "approver", "sabotage", "expected_call", "drift", "audit_fail_after"}
check("no live case carries a write field", all(not (WRITE_FIELDS & set(c)) for c in live),
      str([sorted(WRITE_FIELDS & set(c)) for c in live]))

print("\ncase 2  SRV01 at baseline -> all six pass")
score, _ = build()
rows = {c["id"]: score(c) for c in live}
check("6/6 pass", all(r.result == "pass" for r in rows.values()),
      str({k: r.note for k, r in rows.items() if r.result != "pass"}))
r = rows["srv01-registry-parity"]
check("parity detail carries both reads, value an int on both sides",
      r.detail["srv01"] == {"value": 1} and r.detail["fake"] == {"value": 1}, str(r.detail))
check("every live row keeps its tier", all(r.tier == "live" for r in rows.values()))

print("\ncase 3  drift fails detect; an unrepresentable value fails parity")
score, _ = build(session=Session(answers(**{"Get-AgentRegistryState": [
    {"Item": "registry:rdp-nla", "value": "0"}]})))
by = {c["id"]: c for c in live}
d = score(by["srv01-registry-detect"])
check("registry 0 -> detect FAILS, field named", d.result == "fail" and
      d.detail["drifted_fields"] == ["value"] and "value" in d.note, d.note)
check("...but parity still PASSES (0 is a value the fake can hold)",
      score(by["srv01-registry-parity"]).result == "pass")
score, _ = build(session=Session(answers(**{"Get-AgentServiceState": [
    {"Item": "service:Spooler", "start_type": "disabled", "status": "start pending"}]})))
p = score(by["srv01-service-parity"])
check("'start pending' -> parity FAILS: the fake cannot represent it", p.result == "fail" and
      "cannot be represented" in p.note, p.note)

print("\ncase 4  cannot establish -> ScorerError (exit 2), never a row")


def refused(case, session):
    s, _ = build(session=session)
    try:
        s(case)
        return False
    except scorers.ScorerError:
        return True


check("unreachable (the session will not open)",
      refused(by["srv01-audit-detect"], Session(fail=ConnectionError("no route to 10.10.10.20"))))
check("the endpoint answers for another item",
      refused(by["srv01-service-detect"], Session(answers(**{"Get-AgentServiceState": [
          {"Item": "service:W32Time", "start_type": "auto", "status": "running"}]}))))
check("a session as the WRITER identity", refused(by["srv01-service-detect"],
                                                   Session(user="CORP\\svc-agent-rw")))
check("an unknown check", refused({**by["srv01-service-detect"], "check": "fix"}, Session()))
check("an item of the wrong class",
      refused({**by["srv01-service-detect"], "class": "audit"}, Session()))

print("\ncase 5  read-only: no remediator, no model, one password prompt")
built = []
real_ft = T.fake_target


def spy_ft(*a, **k):
    t = real_ft(*a, **k)
    built.append(t)
    return t


T.fake_target = spy_ft
real_rem = T.FakeRemediator.apply
T.FakeRemediator.apply = lambda *a, **k: (_ for _ in ()).throw(AssertionError("apply on live"))
try:
    score, _ = build()
    for c in live:
        score(c)
finally:
    T.fake_target = real_ft
    T.FakeRemediator.apply = real_rem
check("no apply() on any live case (the fake is built for parity's READ only)", True)
check("fake built only by parity cases (3), never by detect",
      len(built) == 3, str(len(built)))

asked = []
import getpass                                         # noqa: E402
real_gp = getpass.getpass
getpass.getpass = lambda prompt="": (asked.append(prompt), "pw")[1]
import host_client as H                                # noqa: E402
real_host = H.Host


class FakeHost:
    def __init__(self, server, user, pw, **kw):
        self.args = (server, user, pw, kw)

    def __enter__(self):
        s = Session()
        s.check_drift = lambda: None
        return s

    def __exit__(self, *e):
        pass


H.Host = FakeHost
try:
    score, _ = scorers.make_remediation_scorer(live_text=lambda c, d: "{}", suite=suite)
    for c in live:
        score(c)
    check("password asked ONCE for six live cases", len(asked) == 1, str(asked))
    asked.clear()
    score, _ = scorers.make_remediation_scorer(live_text=lambda c, d: "{}", suite=suite,
                                               tier="fixed")
    fixed = [c for c in suite.cases if c["tier"] == "fixed"][:2]
    for c in fixed:
        score(c)
    check("--tier fixed never asks", asked == [], str(asked))
finally:
    getpass.getpass = real_gp
    H.Host = real_host

print("\ncase 6  conditions name what the run touched")
real_digest = model_client.model_digest
model_client.model_digest = lambda m: (_ for _ in ()).throw(AssertionError("digest read"))
try:
    _, cond = scorers.make_remediation_scorer(suite=suite, tier="live",
                                              srv01_session=lambda: Session())
    check("--tier live: no model asked for its digest", True)
    check("--tier live: model 'none', SRV01 named, fake not",
          cond.model_name.startswith("none") and cond.sampling is None
          and cond.host_target == scorers.HOST_TARGET_SRV01, str(cond))
finally:
    model_client.model_digest = real_digest
model_client.model_digest = lambda m: "sha256-test"
real_eff = model_client.effective_sampling
try:
    _, cond = scorers.make_remediation_scorer(suite=suite, tier="fixed")
    check("--tier fixed: the fake only, as before step 7",
          cond.host_target == scorers.HOST_TARGET_REMEDIATION, cond.host_target)
    _, cond = scorers.make_remediation_scorer(suite=suite, tier="all")
    check("--tier all: both named", scorers.HOST_TARGET_REMEDIATION in cond.host_target
          and scorers.HOST_TARGET_SRV01 in cond.host_target, cond.host_target)
finally:
    model_client.model_digest = real_digest

if failures:
    print(f"\nFAILED ({len(failures)}): {'; '.join(failures)}")
    raise SystemExit(1)
print("\nall checks passed")
