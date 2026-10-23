"""P05 build step 5: the agent CLI's exit codes (ADR 0039) and one fix per run (Jose's pick).

  case 1  0 - no drift (nothing to fix); a clean fix held; its audit log is on disk and complete
  case 2  1 - rolled-back (sabotaged fix), declined, refused (off-menu): all exit 1
  case 3  3 - broken rollback: exit 3 and the RESTORE FROM THE CHECKPOINT line
  case 4  2 - nothing applied: unknown item, bad --drift, model down, audit unwritable
          (machine checked unchanged), missing --target (argparse)
  case 5  one fix per run: two drifts -> the first fixed, the second still drifted and named
  case 6  the last-resort catch reads the audit log: a crash BEFORE intent -> 2, AFTER intent -> 3

Run:  python tests/test_remediation_agent.py
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import audit as A                                      # noqa: E402
import model_client                                    # noqa: E402
import proposer as P                                   # noqa: E402
import remediate as R                                  # noqa: E402
import remediation_agent as agent                      # noqa: E402
import target as T                                     # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


BASE = T.load_baseline()
OUT = Path(tempfile.mkdtemp(prefix="p05s5-"))


def reply(text: str) -> model_client.Reply:
    return model_client.Reply(text=text, thinking="", model="scripted", prompt_tokens=None,
                              completion_tokens=None, duration_s=0.0, raw={})


def right(messages, schema):
    """The stand-in model: reads the drifted item off the prompt, answers the baseline."""
    item = re.search(r"Drifted item: (\S+)", messages[1]["content"]).group(1)
    w = next(w for w in BASE if w.id == item)
    fn = next(f for f, c in T.MENU.items() if c == w.cls)
    return reply(json.dumps({"function": fn, "item": item, "values": w.fields,
                             "rationale": "scripted"}))


def off_menu(messages, schema):
    return reply(json.dumps({"function": "Remove-AgentRegistryKey", "item": "registry:rdp-nla",
                             "values": {"value": 1}, "rationale": "x"}))


def down(messages, schema):
    raise model_client.ModelError("connection refused")


YES = lambda **k: True                                  # noqa: E731
NO = lambda **k: False                                  # noqa: E731
SVC = ["--drift", "service:Spooler", "start_type=automatic", "status=running"]


def cli(argv, *, approve=YES, generate=right, fake=None, out=OUT):
    now = [0.0]                       # virtual clock: a failing fix must not wait 30 s in CI
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        try:
            code = agent.main(["--target", "fake", "--out", str(out), *argv],
                              approve=approve, generate=generate, fake=fake,
                              clock=lambda: now[0],
                              sleep=lambda s: now.__setitem__(0, now[0] + s))
        except SystemExit as exc:
            code = exc.code
    return code, buf_out.getvalue() + buf_err.getvalue()


print("case 1  exit 0")
code, text = cli([])
check("no drift -> 0, 'Nothing to fix'", code == 0 and "Nothing to fix" in text, text[:80])
code, text = cli(SVC)
check("clean service fix -> 0, OUTCOME: verified", code == 0 and "OUTCOME: verified" in text)
log_path = Path(re.search(r"audit log: (\S+)", text).group(1))
check("the audit log is on disk, in --out, and complete",
      log_path.parent == OUT and A.check_complete(A.read_log(log_path)) == [])
for item, kv in (("audit:Logon", ["setting=No Auditing"]), ("registry:rdp-nla", ["value=0"])):
    code, text = cli(["--drift", item, *kv])
    check(f"clean {item} fix -> 0 (value=0 parsed as the integer 0)", code == 0, text[-120:])

print("\ncase 2  exit 1 - nothing the agent did stands, the state is described")
for label, argv, kw, outcome in [
        ("sabotaged fix", [*SVC, "--sabotage", "broken"], {}, "rolled-back"),
        ("operator declines", SVC, {"approve": NO}, "declined"),
        ("off-menu proposal", ["--drift", "registry:rdp-nla", "value=0"],
         {"generate": off_menu}, "refused")]:
    code, text = cli(argv, **kw)
    check(f"{label} -> 1, {outcome}", code == 1 and f"OUTCOME: {outcome}" in text, text[-120:])

print("\ncase 3  exit 3 - undescribed state")
code, text = cli(["--drift", "registry:rdp-nla", "value=0", "--sabotage", "broken-rollback"])
check("broken rollback -> 3", code == 3, text[-120:])
check("  ...and it says RESTORE FROM THE CHECKPOINT", "RESTORE FROM THE CHECKPOINT" in text)

print("\ncase 4  exit 2 - nothing applied")
code, text = cli(["--drift", "service:Nope", "status=running"])
check("unknown item -> 2", code == 2 and "not a watched item" in text, text[-100:])
code, text = cli(["--drift", "service:Spooler"])
check("--drift with no FIELD=VALUE -> 2", code == 2)
code, text = cli(SVC, generate=down)
check("model down -> 2, says nothing was applied", code == 2 and "Nothing was applied" in text)
blocker = OUT / "not-a-dir"
blocker.write_text("x")
tgt, host = T.fake_target(BASE)
code, text = cli(SVC, fake=(tgt, host), out=blocker)
check("audit log unwritable -> 2 (fail-closed)", code == 2 and "audit log could not" in text, text[-120:])
check("  ...and the machine still holds the drift (nothing applied)",
      host.snapshot()["service:Spooler"] == {"start_type": "automatic", "status": "running"})
buf = io.StringIO()
with contextlib.redirect_stderr(buf):
    try:
        agent.main(["--drift", "service:Spooler", "status=running"])
        code = 0
    except SystemExit as exc:
        code = exc.code
check("missing --target -> argparse exit 2", code == 2)

print("\ncase 5  one fix per run")
tgt, host = T.fake_target(BASE)
code, text = cli([*SVC, "--drift", "audit:Logon", "setting=No Auditing"], fake=(tgt, host))
check("two drifts -> exit 0 for the one fix", code == 0, text[-80:])
check("the first (baseline order) was fixed", host.snapshot()["service:Spooler"] ==
      {"start_type": "disabled", "status": "stopped"})
check("the second was NOT touched", host.snapshot()["audit:Logon"] == {"setting": "No Auditing"})
check("  ...and is named", "not touched this run: audit:Logon" in text)

print("\ncase 6  the last-resort catch asks the audit log")
real_settle = R._settle


def boom(*a, **k):
    raise RuntimeError("a bug in our code")


R._settle = boom
try:
    code, text = cli(SVC)
    check("crash AFTER the intent line -> 3, not 2", code == 3 and "AFTER the intent" in text,
          text[-120:])
finally:
    R._settle = real_settle


def crash_approver(**k):
    raise RuntimeError("a bug before anything was applied")


code, text = cli(SVC, approve=crash_approver)
check("crash BEFORE the intent line -> 2", code == 2 and "No intent line" in text, text[-120:])

if failures:
    print(f"\nFAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("\nall checks passed")
