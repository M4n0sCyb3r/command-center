"""P06 build step 2 check: the `validate` job's adapter, src/validate.py. ADR 0041; Jose's pick.

  case 1  exit codes: no --expect -> 0 (a plain read); every --expect matched -> 0; one
          mismatch -> 1, naming what the server said; a subcategory the server does not report
          -> 2 (a question that could not be asked is not a "no")
  case 2  matching: subcategory names case-insensitive, settings exact - 'Success' is not
          'Success and Failure'
  case 3  usage errors exit 2 BEFORE the password is asked: bad server, malformed --expect
  case 4  no answer -> 2: the session fails, the drift check refuses, no terminal for the password
  case 5  the default door: the real Host class is built with the server's ADDRESS, the read-only
          account, and the exact allowed list for that server (DC01: 1 function, SRV01: 3)
  case 6  the record holds every row read and every check made; the password is in neither
          the record nor the output

No network: the host is faked. Runs in CI.

Run:  python tests/test_validate.py
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import host_client as H  # noqa: E402
import validate as V  # noqa: E402

failures: list[str] = []
SECRET = "pl@ceholder-not-a-real-pw"
ROWS = [{"Subcategory": "Logon", "Setting": "Success and Failure"},
        {"Subcategory": "Account Lockout", "Setting": "Failure"},
        {"Subcategory": "File System", "Setting": "No Auditing"}]


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class FakeHost:
    def __init__(self, *, open_error=None, drift_error=None):
        self.open_error, self.drift_error, self.calls = open_error, drift_error, []
        self.configuration_name = "AgentReader"

    def __call__(self, pw):
        self.pw = pw
        return self

    def __enter__(self):
        if self.open_error:
            raise self.open_error
        return self

    def __exit__(self, *a):
        pass

    def check_drift(self):
        if self.drift_error:
            raise self.drift_error
        return ("Get-AgentAuditPolicy",)

    def call(self, cmd):
        self.calls.append(cmd)
        return SimpleNamespace(rows=tuple(ROWS), seconds=0.1)


def run(argv, host=None, runs=None, password=None):
    asked = []

    def pw(prompt):
        asked.append(prompt)
        if password:
            return password(prompt)
        return SECRET

    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            code = V.main(argv, open_host=host or FakeHost(), password=pw, runs=runs)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), asked


with tempfile.TemporaryDirectory() as t:
    runs = Path(t)

    print("case 1  exit codes")
    code, out, _ = run(["--server", "dc01"], runs=runs)
    check("no --expect -> 0, every row printed", code == 0 and all(r["Subcategory"] in out for r in ROWS),
          f"exit={code}")
    code, _, _ = run(["--server", "dc01", "--expect", "Logon=Success and Failure",
                      "--expect", "Account Lockout=Failure"], runs=runs)
    check("all matched -> 0", code == 0, f"exit={code}")
    code, out, _ = run(["--server", "dc01", "--expect", "Logon=Success"], runs=runs)
    check("mismatch -> 1, naming what was read", code == 1 and "'Success and Failure'" in out,
          f"exit={code}")
    code, out, _ = run(["--server", "dc01", "--expect", "Logn=Success"], runs=runs)
    check("unknown subcategory -> 2, listing what exists", code == 2 and "Account Lockout" in out,
          f"exit={code}")

    print("case 2  matching")
    checks, unknown = V.compare(ROWS, [("logon", "Success and Failure"), ("LOGON", "success and failure")])
    check("names case-insensitive", not unknown and checks[0]["match"])
    check("settings exact", checks[1]["match"] is False)

    print("case 3  usage errors before the password")
    for argv in (["--server", "dc02"], ["--server", "dc01", "--expect", "Logon"],
                 ["--server", "dc01", "--expect", "=Failure"], []):
        code, _, asked = run(argv, runs=runs)
        check(f"{argv} -> 2, no password asked", code == 2 and not asked, f"exit={code}")

    print("case 4  no answer")
    host = FakeHost(open_error=H.HostError("could not open 'AgentReader' on 10.10.10.10"))
    code, out, _ = run(["--server", "dc01"], host=host, runs=runs)
    check("session fails -> 2", code == 2 and "NO ANSWER" in out, f"exit={code}")
    host = FakeHost(drift_error=H.HostError("endpoint offers Stop-Service, not on the list"))
    code, _, _ = run(["--server", "srv01"], host=host, runs=runs)
    check("drift check refuses -> 2, and nothing was called", code == 2 and host.calls == [],
          f"exit={code}")

    def eof(prompt):
        raise EOFError

    code, _, _ = run(["--server", "dc01"], runs=runs, password=eof)
    check("no terminal for the password -> 2", code == 2, f"exit={code}")

    print("case 5  the default door")
    built = []

    class Recorder:
        def __init__(self, server, user, password, allowed=H.ALLOWED, **kw):
            built.append((server, user, tuple(allowed)))

        def __enter__(self):
            raise H.HostError("recorder: not connecting")

        def __exit__(self, *a):
            pass

    real = H.Host
    H.Host = Recorder
    try:
        for server in ("dc01", "srv01"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                V.main(["--server", server], password=lambda p: SECRET, runs=runs)
    finally:
        H.Host = real
    check("DC01: 10.10.10.10, svc-agent-ro, one function",
          built[0] == ("10.10.10.10", "CORP\\svc-agent-ro", ("Get-AgentAuditPolicy",)), str(built[0]))
    check("SRV01: 10.10.10.20, svc-agent-ro, the three-function verify list",
          built[1] == ("10.10.10.20", "CORP\\svc-agent-ro", tuple(H.ALLOWED_SRV01)), str(built[1]))

    print("case 6  the record")
    r6 = runs / "r6"
    code, out, _ = run(["--server", "srv01", "--expect", "Logon=Success"], runs=r6)
    rec = json.loads(next(r6.glob("*-validate-srv01.json")).read_text())
    check("every row and every check recorded",
          rec["rows"] == ROWS and len(rec["checks"]) == 1 and rec["checks"][0]["match"] is False
          and rec["exit_code"] == 1 and rec["visible"] == ["Get-AgentAuditPolicy"])
    all_text = out + "".join(p.read_text() for p in runs.rglob("*.json"))
    check("the password appears in no record and no output", SECRET not in all_text)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
    sys.exit(1)
print("all validate checks passed")
