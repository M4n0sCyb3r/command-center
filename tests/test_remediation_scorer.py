"""P05 build step 5: the `remediation` suite enters the harness - proven on a SCRIPTED model (CI).

  case 1  the suite: header names the scorer; 11 fixed cases on the fake (the 6 live SRV01
          cases are tests/test_remediation_live.py's); ADR 0039's minimum
          coverage present (per class a clean fix and a broken fix; off-menu, wrong argument,
          decline, broken rollback, audit-write failure); every expected_call is the baseline's
  case 2  every case passes with the stand-in model; right_fix True where the model was right,
          False on the scripted off-menu / wrong-argument replies, None when the agent stopped
  case 3  THE GATE HAS TWO HALVES - each sabotaged alone fails the case:
          right outcome + broken audit (a write fails after apply) -> fail, problems named;
          complete audit + wrong outcome expected -> fail
  case 4  stopped: passes only if nothing past the write-ahead line exists AND the machine is
          unchanged (a remediator that writes during preview is caught); a failure AFTER apply
          on a case expecting a stop is a fail, not a pass
  case 5  refusals (ScorerError, never a score): SRV01 target, unknown expected value, a planted
          drift detect() does not see, an expected_call that disagrees with the baseline
  case 6  the model unreachable on a live case -> ModelError (the harness's stall path, exit 2)
  case 7  the scorer table row, and scripted conditions never pose as a measurement

Run:  python tests/test_remediation_scorer.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import model_client                                    # noqa: E402
import proposer as P                                   # noqa: E402
import scorers                                         # noqa: E402
from suite import parse_suite                          # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


SUITE = REPO_ROOT / "evals" / "suites" / "remediation.jsonl"
lines = SUITE.read_text(encoding="utf-8").splitlines()
header, all_cases = json.loads(lines[0]), [json.loads(x) for x in lines[1:]]
# P05 step 7 added the `live` tier (SRV01, read-only); this file is about the fixed tier.
cases = [c for c in all_cases if c["tier"] == "fixed"]
by_id = {c["id"]: c for c in cases}


def stand_in(case, drift):
    return json.dumps({**case["expected_call"], "rationale": "scripted stand-in for qwen3:8b"})


score, cond = scorers.make_remediation_scorer(live_text=stand_in)

print("case 1  the suite")
check("header names the remediation scorer", header["suite"] == "remediation"
      and header["scorer"] == "remediation")
check("parses as a suite (ids unique, tiers known)", len(parse_suite(SUITE).cases) == 17)
check("11 fixed cases, all fake; 6 live, all srv01", len(cases) == 11
      and all(c["target"] == "fake" for c in cases)
      and sum(c["tier"] == "live" and c["target"] == "srv01" for c in all_cases) == 6)
want = {(c["class"], c["sabotage"], c["expected_outcome"]) for c in cases}
for cls in ("service", "audit", "registry"):
    check(f"{cls}: a clean fix (verified) and a broken fix (rolled-back)",
          (cls, "none", "verified") in want and (cls, "broken", "rolled-back") in want)
outs = {c["expected_outcome"] for c in cases}
check("across the suite: refused, declined, rollback-failed, stopped-before-apply",
      {"refused", "declined", "rollback-failed", scorers.STOPPED} <= outs, str(sorted(outs)))
check("an off-menu reply and a wrong-argument reply are SCRIPTED (a scenario, not a hope)",
      isinstance(by_id["off-menu"]["model"], dict) and isinstance(by_id["wrong-argument"]["model"], dict))

print("\ncase 2  every case passes on the stand-in model")
rows = {c["id"]: score(c) for c in cases}
for cid, row in rows.items():
    check(f"{cid:<20} {row.detail['outcome']:<22} -> pass", row.result == "pass", row.note)
check("11/11", sum(r.result == "pass" for r in rows.values()) == 11)
check("right_fix False on off-menu and wrong-argument",
      rows["off-menu"].detail["right_fix"] is False and rows["wrong-argument"].detail["right_fix"] is False)
check("right_fix True on every stand-in case",
      all(r.detail["right_fix"] is True for cid, r in rows.items()
          if cases[[c["id"] for c in cases].index(cid)]["model"] == "live"))
check("right_fix None when the agent stopped (no proposal survived to be graded)",
      rows["audit-write-fails"].detail["right_fix"] is None)
check("detail keys", set(rows["service-clean"].detail) == {
    "class", "sabotage", "model", "expected", "outcome", "right_fix", "call", "audit_problems",
    "audit_lines", "reasons", "completion_tokens", "model_seconds"})
check("a stand-in row says stand-in, never live", rows["service-clean"].detail["model"] == "stand-in")

print("\ncase 3  the gate has two halves")
# writes: drift1 proposal2 preview3 screen4 answer5 recheck6 intent7 applied8 verify9 outcome10
broken_audit = score({**by_id["service-clean"], "audit_fail_after": 8})
check("right outcome, audit broken after apply -> FAIL",
      broken_audit.detail["outcome"] == "verified" and broken_audit.result == "fail",
      broken_audit.note)
check("  ...and the problem is named (the outcome line never landed)",
      broken_audit.detail["audit_problems"] != [], str(broken_audit.detail["audit_problems"]))
wrong = score({**by_id["service-clean"], "expected_outcome": "rolled-back"})
check("complete audit, wrong outcome -> FAIL", wrong.result == "fail"
      and wrong.detail["audit_problems"] == [], wrong.note)

print("\ncase 4  stopped means stopped")
late = score({**by_id["audit-write-fails"], "audit_fail_after": 8})
check("a failure AFTER apply on a case expecting a stop -> FAIL", late.result == "fail", late.note)
check("problems = none for the real stop", rows["audit-write-fails"].detail["audit_problems"] == [])
check("the stopped log ends before the intent line", rows["audit-write-fails"].detail["audit_lines"] == 6)
import target as T                                     # noqa: E402
real_preview = T.FakeRemediator.preview


def writing_preview(self, call):
    """A remediator whose PREVIEW changes the machine. The audit log still shows a clean stop."""
    self._write(call.item, dict(call.values))
    return real_preview(self, call)


T.FakeRemediator.preview = writing_preview
try:
    moved = score(by_id["audit-write-fails"])
finally:
    T.FakeRemediator.preview = real_preview
check("stopped, but the machine changed (a writing preview) -> FAIL, named",
      moved.result == "fail" and any("machine changed" in p for p in moved.detail["audit_problems"]),
      str(moved.detail["audit_problems"]))
check("stopped_problems flags an intent line",
      scorers.stopped_problems([{"seq": 1, "who": "code", "event": "intent"}]) != [])

print("\ncase 5  refusals")
for label, case in [
        ("SRV01 target on the fixed tier", {**by_id["service-clean"], "target": "srv01"}),
        ("fake target on the live tier", {**by_id["service-clean"], "tier": "live"}),
        ("unknown expected value", {**by_id["service-clean"], "expected_outcome": "fixed"}),
        ("a drift equal to baseline", {**by_id["service-clean"],
                                       "drift": {"start_type": "disabled", "status": "stopped"}}),
        ("expected_call disagrees", {**by_id["service-clean"], "expected_call":
                                     {**by_id["service-clean"]["expected_call"], "values": {"start_type": "manual"}}}),
        ("unknown sabotage", {**by_id["service-clean"], "sabotage": "evil"})]:
    try:
        score(case)
        check(f"refused: {label}", False)
    except scorers.ScorerError:
        check(f"refused: {label}", True)

print("\ncase 6  the model unreachable is a stall, not a failed case")


def down(messages, schema):
    raise model_client.ModelError("connection refused (CI: no Ollama)")


real_default = P.default_generate
P.default_generate = down
# Build the real factory without touching Ollama: patch the digest read, then restore.
real_digest = model_client.model_digest
model_client.model_digest = lambda m=None: "sha256:test"
try:
    live_score, live_cond = scorers.make_remediation_scorer()
    try:
        live_score(by_id["service-clean"])
        check("ModelError raised", False)
    except model_client.ModelError as exc:
        check("ModelError raised, naming the case", "service-clean" in str(exc), str(exc)[:80])
    check("real conditions name qwen3:8b and carry sampling",
          live_cond.model_name == "qwen3:8b" and live_cond.sampling is not None)
    off = live_score(by_id["off-menu"])
    check("a SCRIPTED case never calls the model, even on the real path", off.result == "pass",
          off.note)
finally:
    model_client.model_digest = real_digest
    P.default_generate = real_default

print("\ncase 7  the table row")
check("SCORERS['remediation'] is this factory",
      scorers.SCORERS.get("remediation") is scorers.make_remediation_scorer)
check("resolve() finds it from the suite header",
      scorers.resolve(parse_suite(SUITE)) is scorers.make_remediation_scorer)
check("scripted conditions never pose as a measurement",
      cond.model_name == "scripted" and cond.sampling is None)

if failures:
    print(f"\nFAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("\nall checks passed")
