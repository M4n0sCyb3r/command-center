"""P03 build step 5: the revision loop, proven with SCRIPTED models (no Ollama needed).

  case 1  round 0 is the step-4 critic: same prompt, byte for byte
  case 2  the code decides every stop: approved / escalate-risk / escalate-cap (after exactly
          2 revisions) / no-change / error - and an error keeps the last good script
  case 3  the reviser sees the flagged lines and the whole script; its reply must hold ONE script
  case 4  "fixed" = oracle gone AND lints clean AND no sibling oracle fires; a control is
          "broke (detectable)" only if changed and it fails lint or fires an oracle; a reading that
          does not list every final script is refused
  case 6  escalation reads both copies; the current copy's review decides; failures never approve
  case 5  every call runs ALONE - both loop models unloaded first (void run 20261019T032719Z:
          a resident qwen3:8b flipped 4/32 critic verdicts) - with the review budget

Run:  python tests/test_review_loop.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import model_client                                   # noqa: E402
import review as R                                    # noqa: E402
import review_loop as L                               # noqa: E402
import review_runner as RR                            # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def reply(text: str) -> model_client.Reply:
    return model_client.Reply(text=text, thinking="", model="scripted", prompt_tokens=100,
                              completion_tokens=20, duration_s=0.5, raw={})


def blocking(line: int, category: str = "correctness") -> str:
    return json.dumps({"verdict": "revise", "findings": [{"line_start": line, "line_end": line,
                       "category": category, "severity": "blocking", "why": "w", "fix": "f"}]})


APPROVE = '{"verdict": "approve", "findings": []}'
items = {i.id: i for i in R.load_set()}
d, ctrl = items["01-G"], items["01-C"]
a = d.defect["lines"][0][0]
fenced = lambda text: reply(f"```powershell\n{text}```")

print("case 1  round 0 is the step-4 critic")
seen: list = []
L.loop_item(d, lambda m, s: (seen.append(m), reply(APPROVE))[1], lambda m: None)
check("first critique prompt == review_runner.build_messages(item)", seen[0] == RR.build_messages(d))

print("case 2  the code decides every stop")
row, text = L.loop_item(d, lambda m, s: reply(APPROVE), lambda m: 1 / 0)
check("approved at round 0: no revise call, unchanged", row["stop"] == "approved" and row["calls"] == 1 and not row["changed"])
row, _ = L.loop_item(d, lambda m, s: reply(blocking(a, "blast-radius")), lambda m: 1 / 0)
check("blocking blast-radius -> escalate-risk with no local round", row["stop"] == "escalate-risk" and row["rounds"] == 0)
k = iter(range(100))
row, text = L.loop_item(d, lambda m, s: reply(blocking(a)), lambda m: fenced(d.text() + f"# r{next(k)}\n"))
check("still blocked -> escalate-cap after exactly 2 revisions, 3 critiques",
      row["stop"] == "escalate-cap" and row["rounds"] == 2 and [c["role"] for c in row["trace"]].count("critique") == 3
      and text.endswith("# r1\n"), row["stop"])
row, _ = L.loop_item(d, lambda m, s: reply(blocking(a)), lambda m: fenced(d.text()))
check("byte-identical revision -> no-change", row["stop"] == "no-change" and row["rounds"] == 1 and not row["changed"])
crit = iter([blocking(a), blocking(a)])
revs = iter([fenced(d.text() + "# r0\n"), reply("I fixed it.")])
row, text = L.loop_item(d, lambda m, s: reply(next(crit)), lambda m: next(revs))
check("reply with no script -> error, keeps the last good script",
      row["stop"] == "error" and "ReviseError" in row["error"] and text.endswith("# r0\n"), row.get("error", ""))
row, _ = L.loop_item(d, lambda m, s: reply('{"verdict": "revise", "findings": []}'), lambda m: 1 / 0)
check("invalid first critique -> error row with no round-0 score", row["stop"] == "error" and row["round0"] is None)

print("case 3  what the reviser sees, and what it must return")
f = R.Finding(a, a, "correctness", "blocking", "inverted", "-contains")
m = L.revise_messages(d, d.text(), (f,))
flagged = d.text().split("\n")[a - 1]
check("the flagged line is quoted and the whole script is included",
      flagged in m[1]["content"] and d.text().rstrip("\n") in m[1]["content"] and m[0]["content"] == L.REVISE_SYSTEM)
check("one fence -> the script, LF-normalised", L.extract_script("ok\n```powershell\r\na\r\nb\r\n```") == "a\nb\n")
for bad in ("no fence", "```powershell\na\n```\n```powershell\nb\n```"):
    try:
        L.extract_script(bad); check(f"refused: {bad[:12]!r}", False)
    except L.ReviseError:
        check(f"refused: {bad[:12]!r}", True)

print("case 4  scoring the finals")
ref = ctrl.text()
sibling = ref.replace("StartMode = 'Auto' AND State = 'Stopped'", "State = 'Stopped'")
assert sibling != ref
cases = {"01-G": (ref, [], True), "01-L": (sibling, [], False), "01-B": (ref, ["PSAvoidUsingWMICmdlet"], False)}
with tempfile.TemporaryDirectory() as tmp:
    finals = Path(tmp) / "run"; finals.mkdir()
    rows = []
    for i, (text, _, _) in cases.items():
        (finals / f"{i}.ps1").write_bytes(text.replace("\n", "\r\n").encode())
        rows.append({"id": i, "control": False, "family": items[i].defect["family"], "changed": True})
    (finals / "01-C.ps1").write_bytes(sibling.replace("\n", "\r\n").encode())
    rows.append({"id": "01-C", "control": True, "changed": True})
    files = [f"{r['id']}.ps1" for r in rows]
    hits = [{"ScriptName": f"{i}.ps1", "RuleName": rule, "Line": 1} for i, (_, rules, _) in cases.items() for rule in rules]
    reading = Path(tmp) / "reading.json"
    reading.write_text(json.dumps({"analyzer": "x", "folder": "run", "files": files, "findings": hits}))
    rec = {"rows": rows}
    s = L.score(rec, finals, reading)
    got = {r["id"]: r.get("fixed", r.get("broke_detectable")) for r in rec["rows"]}
    check("restored -> fixed; sibling oracle -> not fixed; lint hit -> not fixed",
          [got[i] for i in cases] == [c[2] for c in cases.values()], str(got))
    check("changed control that fires a planted oracle -> broke (detectable)", got["01-C"] is True and s["broke_detectable"] == 1)
    rec["rows"][3]["changed"] = False
    s = L.score(rec, finals, reading)
    check("an unchanged control never counts as broke", s["broke_detectable"] == 0 and s["rewritten"] == 0)
    reading.write_text(json.dumps({"analyzer": "x", "folder": "run", "files": files[:2], "findings": []}))
    try:
        L.score(rec, finals, reading); check("a reading missing finals is refused", False)
    except R.ReadingError:
        check("a reading missing finals is refused", True)

print("case 6  escalation (ADR 0032 am. 2): both copies, current decides")
seen6: list = []
def esc(verdicts):
    it = iter(verdicts)
    def call(m, s):
        seen6.append(m[1]["content"]); return reply(next(it))
    return call
k = iter(range(100))
rev = lambda m: fenced(d.text() + f"# r{next(k)}\n")
row, text = L.loop_item(d, lambda m, s: reply(blocking(a)), rev, esc([blocking(a), APPROVE]))
check("cap -> two escalation reviews: the ORIGINAL first, then the revised copy",
      row["escalation"]["calls"] == 2 and "# r" not in seen6[0] and "# r1" in seen6[1], str(row.get("escalation")))
check("revised copy approved -> approved-after-escalation; original scored by the catch rule",
      row["terminal"] == "approved-after-escalation" and row["escalation"]["original"]["caught"] is True)
seen6.clear()
row, _ = L.loop_item(d, lambda m, s: reply(blocking(a, "security")), lambda m: 1 / 0, esc([blocking(a)]))
check("risk stop, script unchanged -> ONE escalation review, still blocked -> needs-human",
      row["terminal"] == "needs-human" and row["escalation"]["calls"] == 1 and len(seen6) == 1)
row, _ = L.loop_item(d, lambda m, s: reply(APPROVE), lambda m: 1 / 0, lambda m, s: 1 / 0)
check("approved locally -> no escalation call", row["terminal"] == "approved" and "escalation" not in row)
row, _ = L.loop_item(d, lambda m, s: reply(blocking(a)), lambda m: reply("no script"), lambda m, s: 1 / 0)
check("a loop error is not escalated - it fails SAFE to needs-human (ADR 0032 am. 3)",
      row["terminal"] == "needs-human" and row["stop"] == "error" and "escalation" not in row)
row, _ = L.loop_item(d, lambda m, s: reply(blocking(a, "security")), lambda m: 1 / 0,
                     lambda m, s: (_ for _ in ()).throw(model_client.ModelError("cap")))
check("an escalation failure is never an approval - needs-human, error kept",
      row["terminal"] == "needs-human" and "escalation" in row["error"])
row, _ = L.loop_item(d, lambda m, s: reply(blocking(a, "security")), lambda m: 1 / 0)
check("no escalator given (step 5 mode) -> escalation-pending", row["terminal"] == "escalation-pending")

print("case 5  the default reviser")
calls: list[str] = []
saved = (model_client.unload, model_client.chat)
model_client.unload = lambda model=None: calls.append(f"unload {model}")
model_client.chat = lambda messages, **kw: (calls.append(f"chat {kw.get('model')} {kw.get('max_tokens')} {kw.get('timeout')} {kw.get('schema')}"), reply("x"))[1]
try:
    L.default_revise([])
    L.default_critique([], {"s": 1})
finally:
    model_client.unload, model_client.chat = saved
both = ["unload qwen3:8b", "unload hermes3:8b", "unload qwen3:14b"]
check("each call runs ALONE: every P03 model unloaded, then chat with 6144 / 300",
      calls == both + ["chat qwen3:8b 6144 300 None"] + both + ["chat hermes3:8b 6144 300 {'s': 1}"], str(calls))

if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
