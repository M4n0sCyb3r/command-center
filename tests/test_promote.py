"""P06 build step 3: `promote` on fakes - the `command-center` suite, gated in CI (ADR 0044).

  case 1  the suite: header names the scorer; 17 promote + 6 route cases; ADR 0044's named
          forks are all present (clean promoted, breaks-and-restored, reviewer block, restore
          failing -> 3, tooling before the live run -> 2); every promote code 0/1/2/3 appears
  case 2  every case passes on the fakes
  case 3  THE GATE HAS TEETH - each property removed alone turns named cases red:
            restore trusted without reading the machine  -> restore-unconfirmed passes as 1: FAIL
            live_started never written                   -> crash-after-live answers 2: FAIL
            the deny-list emptied                        -> dc-only-refused reaches review: FAIL
            QE-before ignored                            -> qe-before-red runs live: FAIL
  case 4  the deny-list: DC-only scripts refused, member-server scripts not; whole words only
  case 5  the CLI as a process, REAL bench: reviewer unreachable (pinned dead) -> 2, record
          not-started at review, live_started false, no vault prompt; an unreadable script -> 2;
          there is no --fake flag (-> 2)
  case 6  the harness as a process (its own baseline path) exits 0, 28/28; the committed
          baseline was taken from this exact suite and holds 28 passes

NEEDS NO MODEL, NO MACHINE, NO NETWORK - CI.

Run:  python tests/test_promote.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import promote as PR                                   # noqa: E402
import scorers                                         # noqa: E402
from suite import parse_suite                          # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


SUITE = REPO / "evals" / "suites" / "command-center.jsonl"
BASELINE = REPO / "evals" / "baselines" / "command-center.json"
suite = parse_suite(SUITE)
header = json.loads(SUITE.read_text(encoding="utf-8").splitlines()[0])
cases = suite.cases
by_id = {c["id"]: c for c in cases}
score, cond = scorers.make_command_center_scorer(suite=suite, tier="fixed")


def quiet(fn, *a, **k):
    """The flow talks to the operator on stderr; a test reads the record instead."""
    with contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **k)


print("case 1  the suite")
check("header names the command-center scorer",
      header["suite"] == "command-center" and header["scorer"] == "command-center")
kinds = [c["kind"] for c in cases]
check("22 promote cases + 6 route cases, all fixed",       # + dry-run-refused (4), 4 override (5)
      kinds.count("promote") == 22 and kinds.count("route") == 6
      and all(c["tier"] == "fixed" for c in cases), f"{len(cases)} cases")
for cid in ("clean-promoted", "breaks-server-restored", "reviewer-blocks", "restore-fails",
            "tooling-before-live", "route-unknown-job", "route-pass-through-3",
            "dry-run-refused"):                         # step 4: the analyzer refuses
    check(f"ADR 0044's fork present: {cid}", cid in by_id)
codes = {c["expected_exit"] for c in cases if c["kind"] == "promote"}
check("every promote code 0/1/2/3 is expected somewhere", codes == {0, 1, 2, 3}, str(codes))
check("conditions say no model was called",
      cond.model_digest == "no-model-called" and cond.sampling is None)

print("\ncase 2  every case passes on the fakes")
rows = {c["id"]: quiet(score, c) for c in cases}
for cid, row in rows.items():
    check(f"{cid:<26} exit {row.detail['exit']}", row.result == "pass", row.note)

print("\ncase 3  the gate has teeth - each property removed alone turns cases red")


@contextlib.contextmanager
def patched(obj, name, value):
    saved = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, saved)


def trusting_restore(bench, rec, before, why):         # the actor grades itself
    bench.restore(rec.data["checkpoint"])
    return rec.end(PR.EXIT_NOT_PROMOTED, "restored", why)


with patched(PR, "_restore", trusting_restore):
    r = quiet(score, by_id["restore-unconfirmed"])
    check("restore trusted without reading the machine -> restore-unconfirmed FAILS",
          r.result == "fail", r.note)

real_write = PR._Record.write


def forgetful_write(self):                               # live_started never reaches disk
    self.data["live_started"] = False
    real_write(self)


with patched(PR._Record, "write", forgetful_write):
    r = quiet(score, by_id["crash-after-live"])
    check("live_started never written -> crash-after-live FAILS (answers 2 after a live run)",
          r.result == "fail" and r.detail["exit"] == 2, r.note)

with patched(PR, "dc_only_hits", lambda text: []):
    r = quiet(score, by_id["dc-only-refused"])
    check("deny-list emptied -> dc-only-refused FAILS (it reached the reviewer)",
          r.result == "fail" and "review" in r.detail["calls"], r.note)

real_flow = PR._flow


def no_canary(script, bench, rec, promoted, **kw):      # QE-before read but never gated
    real_qe = bench.qe
    first = [True]

    def green():
        if first[0]:
            first[0] = False
            real_qe()
            return PR.QEReport(0, {})
        return real_qe()
    bench.qe = green
    return real_flow(script, bench, rec, promoted, **kw)


with patched(PR, "_flow", no_canary):
    r = quiet(score, by_id["qe-before-red"])
    check("QE-before ignored -> qe-before-red FAILS (it ran live on a red canary)",
          r.result == "fail" and "live_run" in r.detail["calls"], r.note)

def keep_blocking(script, bench, rec, promoted, override=None):   # the override ignored
    return real_flow(script, bench, rec, promoted, override=None)


with patched(PR, "_flow", keep_blocking):
    r = quiet(score, by_id["review-overridden-promoted"])
    check("an override that is never consulted -> review-overridden-promoted FAILS",
          r.result == "fail" and r.detail["exit"] == 1, r.note)


def rubber_stamp(text):                                  # any input counts as an override
    return "anything"


with patched(PR, "parse_override", rubber_stamp):
    r = quiet(score, by_id["review-override-declined"])
    check("an override screen that accepts 'yes' -> review-override-declined FAILS",
          r.result == "fail", r.note)

print("\ncase 4b  the override parser")
for typed, want in (("OVERRIDE false alarm on line 4", "false alarm on line 4"),
                    ("  OVERRIDE  the slash is not there  ", "the slash is not there"),
                    ("OVERRIDE", None), ("OVERRIDE short", None), ("override false alarm x", None),
                    ("yes", None), ("", None), ("OVERRIDEfalse alarm here", None)):
    check(f"{typed!r:<40} -> {want!r}", PR.parse_override(typed) == want, str(PR.parse_override(typed)))

print("\ncase 4c  the override SCREEN reads only a real decision")
import io as _io                                          # noqa: E402
from types import SimpleNamespace as _NS                  # noqa: E402
_rev = PR.Review("revise", [{"severity": "blocking", "line_start": 4, "line_end": 4,
                             "category": "correctness", "why": "scripted"}])
for typed, want, label in (
        ("\n\nOVERRIDE false alarm on line 4\n", "false alarm on line 4",
         "empty lines are re-asked, not taken as the decision"),
        ("\n\n\n", None, "three empty lines -> no override"),
        ("", None, "end of input -> no override"),
        ("yes\nOVERRIDE false alarm on line 4\n", None, "the first non-empty line decides")):
    saved = sys.stdin
    sys.stdin = _io.StringIO(typed)
    try:
        got = quiet(PR.screen_override, _rev)
    finally:
        sys.stdin = saved
    check(f"{label}", got == want, repr(got))

print("\ncase 4  the deny-list")
dc = {"Import-Module ActiveDirectory": ["ActiveDirectory"],
      "#Requires -Modules ADDSDeployment": ["ADDSDeployment"],
      "Install-ADDSDomainController -DomainName example.test": ["Install-ADDSDomainController"],
      "Get-ADUser -Filter *": ["Get-ADUser"],
      "Set-ItemProperty HKLM:\\SYSTEM\\CurrentControlSet\\Services\\NTDS\\Parameters": ["NTDS"],
      "Add-DnsServerPrimaryZone -Name example.test": ["Add-DnsServerPrimaryZone"],
      "Import-Module DnsServer": ["DnsServer"],
      "ntdsutil.exe snapshot": ["ntdsutil"],
      "New-GPO -Name test": ["New-GPO"]}
for text, want in dc.items():
    got = PR.dc_only_hits(text)
    check(f"refused: {text[:44]!r}", got == want, str(got))
member = ("Get-Service -Name Spooler", "Set-Service -Name Spooler -StartupType Disabled",
          "Set-ItemProperty HKLM:\\SYSTEM\\CurrentControlSet\\Services\\LanmanServer\\Parameters "
          "-Name SMB1 -Value 0", "Install-WindowsFeature -Name SNMP-Service",
          "auditpol /set /subcategory:Logon /success:enable",
          "$adapter = Get-NetAdapter; $padding = 'ADUsers-style string'")
for text in member:
    check(f"not refused: {text[:44]!r}", PR.dc_only_hits(text) == [], str(PR.dc_only_hits(text)))
REMOTE = "Invoke-Command -ComputerName dc01 -ScriptBlock { Restart-Service W32Time }"
check("KNOWN GAP, pinned: a script that reaches ANOTHER host is not refused (a deny-list "
      "refuses only what it lists - ADR 0042's weakness, carried to step 4)",
      PR.dc_only_hits(REMOTE) == [])

print("\ncase 5  the CLI as a process")
with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    script = tmp / "change.ps1"
    script.write_text("Get-Service -Name Spooler\n", encoding="utf-8")

    def cli(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(REPO / "src" / "promote.py"), *argv],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                                   "OLLAMA_BASE_URL": "http://127.0.0.1:9"},   # bench too
                              stdin=subprocess.DEVNULL)
    runs = tmp / "runs"
    p = cli(str(script), "--runs", str(runs), "--promoted", str(tmp / "promoted"))
    recs = [json.loads(f.read_text()) for f in runs.glob("*.json")]
    check("real bench, reviewer unreachable -> exit 2", p.returncode == 2, p.stderr[-200:])
    check("no vault password was asked for", "vault password" not in p.stderr.lower())
    check("its record: not-started, live_started false, stopped at review",
          len(recs) == 1 and recs[0]["state"] == "not-started" and recs[0]["live_started"]
          is False and recs[0]["steps"][-1]["step"] == "review", str(recs[:1])[:300])
    p = cli(str(tmp / "missing.ps1"), "--runs", str(runs))
    check("an unreadable script -> exit 2", p.returncode == 2)
    p = cli(str(script), "--fake")
    check("there is no --fake flag (argparse refuses -> 2)",
          p.returncode == 2 and "unrecognized" in p.stderr)

print("\ncase 6  the harness, as a process; and the committed baseline is THIS suite's")
# The harness gets its OWN baseline path (tests/test_isolation.py: a test's result must not
# depend on what a human last promoted). The committed baseline is checked as a FILE: it
# exists, it was taken from this exact suite, and every case in it passed.
with tempfile.TemporaryDirectory() as t:
    p = subprocess.run([sys.executable, str(REPO / "src" / "eval_harness.py"),
                        "--suite", str(SUITE), "--tier", "fixed", "--out", t,
                        "--baseline", str(Path(t) / "none.json")],
                       capture_output=True, text=True, timeout=300)
check("eval_harness --tier fixed exits 0", p.returncode == 0, (p.stdout + p.stderr)[-400:])
check("and reports 28/28 gating", "28/28 passed" in p.stdout)
check("the baseline is committed", BASELINE.is_file(), str(BASELINE.relative_to(REPO)))
if BASELINE.is_file():
    import hashlib                                      # noqa: E402
    pinned = json.loads(BASELINE.read_text(encoding="utf-8"))
    check("it was taken from this exact suite file (re-take it when the suite changes)",
          pinned["header"]["suite_sha256"] == hashlib.sha256(SUITE.read_bytes()).hexdigest())
    check("all 28 cases pass in it", len(pinned["cases"]) == 28
          and all(c["result"] == "pass" for c in pinned["cases"]))

print()
if failures:
    print(f"FAILED: {len(failures)} check(s)")
    sys.exit(1)
print("all promote checks passed")
