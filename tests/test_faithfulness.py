"""P02 build step 6d: the faithfulness judge's CODE, proven with no model (ADR 0026).

  case 1  sentences: split where a sentence ends, not inside "e.g." or "5.5"
  case 2  verify(), one rule at a time - empty, short, not in the text, no
          shared word, a missing number - and normalisation across line breaks
  case 3  the schema forces exactly one quote per sentence
  case 4  assess(): every sentence must be supported; a judge returning the
          wrong number of quotes is an error, never a verdict
  case 4b list markers are layout (amendment 8)
  case 4f the judge's model is set apart from the answer model (amendment 16)
  case 4c an ellipsis in a quote joins passages; each piece must stand alone (amendment 9)
  case 4e the judge's input carries no backslash; normalize() folds it (amendment 14)
  case 4d inline numbered runs, closing-quote splits, ordinal words (amendment 13)
  case 5  calibration on the REAL pairs with two scripted judges:
            PERFECT - copies support, leaves defects empty -> meets the bar
            LAZY    - quotes the best-overlapping source sentence for every
                      sentence, defect or not -> must FAIL the bar (positive
                      control) - and the NUMBER rule must still catch both
                      changed-number pairs, because that rule does not depend
                      on the judge's honesty
  case 5d the held-out v2 pairs: counts, defects, ids disjoint, shared cited chunks pinned (amendment 11)
  case 5f the held-out v3 pairs: counts, defect, ids disjoint, the one shared cited chunk pinned (amendment 15)
  case 5c a saved reading: every pair, every sentence, its condition (amendment 10)
  case 5e the bar at the defect sentence: wrong-reason catches are false passes, errors never catch (amendment 12)
  case 6  a run record: only answered rows are judged; the index hash is read
          from the record header; a record without one is refused

Run:  python tests/test_faithfulness.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import faithfulness as F                             # noqa: E402
import retrieval                                     # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


print("case 1  sentences")
check("two sentences", F.split_sentences("Stop it. Then start it.") == ["Stop it.", "Then start it."])
check("'e.g.' does not split", len(F.split_sentences("A stopped service (e.g., Spooler) fails. Restart it.")) == 2)
check("'e.g. Spooler' does not split", len(F.split_sentences("Use a service e.g. Spooler is fine. Next.")) == 2)
check("'5.5' does not split", F.split_sentences("Version 5.5 is fine.") == ["Version 5.5 is fine."])
check("empty -> nothing", F.split_sentences("   ") == [])

print("case 2  verify, rule by rule")
SRC = "Fifteen percent free is the warning line and ten percent the action line. Below\nfive percent the host is treated as failed rather than degraded."
S = "Below five percent the host counts as failed."
check("empty quote", F.verify(S, "", SRC).why.startswith("no quote"))
check("short quote", "chars" in F.verify(S, "failed", SRC).why)
check("quote not in the text", "not in the cited text" in F.verify(S, "below five percent the host is destroyed", SRC).why)
check("quote sharing no content word",
      "no content word" in F.verify("Kerberos breaks.", "Fifteen percent free is the warning line", SRC).why)
check("a changed number is caught even with a verbatim quote",
      "number" in F.verify("Below two percent the host is treated as failed.",
                           "the host is treated as failed rather than degraded", SRC).why)
check("normalisation: case and a line break inside the quoted passage",
      F.verify(S, "BELOW FIVE percent the host is treated as failed", SRC).supported)
check("normalisation: curly quotes match straight ones",
      F.verify("It is a finding at audit.", "a \u201cfinding\u201d at audit", 'is a "finding" at audit').supported)
check("amendment 6: a single-quoted copy of a double-quoted passage is the same passage",
      F.verify("It is a finding even when it works.", "a finding even when 'it works'",
               'is a finding even when "it works" today').supported)
check("amendment 6: folding marks does not let changed WORDS through",
      not F.verify("It is a finding even when it works.", "a finding even when 'it failed'",
                   'is a finding even when "it works" today').supported)
check("a good quote across a line break is supported",
      F.verify(S, "Below five percent the host is treated as failed", SRC).supported)

print("case 3  schema")
sch = F.schema(3)["properties"]["quotes"]
check("minItems = maxItems = n", sch["minItems"] == 3 and sch["maxItems"] == 3)

print("case 4  assess")
got = F.assess("Below five percent the host is treated as failed. It is fine.", [SRC],
               extract=lambda sents, src: ["Below five percent the host is treated as failed", ""])
check("one unsupported sentence -> not faithful", got.faithful is False and got.unsupported == ["It is fine."])
try:
    F.assess("One. Two.", [SRC], extract=lambda s, src: ["x"])
    check("wrong quote count raises", False)
except ValueError:
    check("wrong quote count raises", True)
check("nothing to judge -> None", F.assess("", [SRC], extract=lambda s, src: []).faithful is None)

print("case 4b list markers are layout (amendment 8)")
LIST = "To change it:\n1. Click **Start** and open the console.\n2. Right-click the folder.\n3. Type the time."
check("a numbered list leaves no bare-number fragments",
      F.split_sentences(LIST) == ["To change it:\nClick **Start** and open the console.",
                                  "Right-click the folder.", "Type the time."],
      str(F.split_sentences(LIST)))
check("inline markers go too, and are not numbers rule 2b demands",
      F._numbers(F.split_sentences("It could stem from: 1) the firewall, or (2) the listener.")[0]) == set())
check("a real number survives (port 5985, five minutes)",
      F._numbers("Open port 5985 for five minutes.") == {"5985", "five"})
check("a quote spanning list items without their numbers is continuous source text",
      F.verify("Open the console and right-click the folder.",
               "Click Start and open the console. Right-click the folder.", LIST).supported)
check("a list item opening in bold still starts its own sentence",
      len(F.split_sentences("Do this:\n1. **First** step here.\n2. **Second** step here.")) == 2)
check("'version 1. Then' inline is prose, not a marker", "1" in F._numbers("Use version 1. Then stop."))

print("case 4d three wrong-reason failures removed (amendment 13)")
STEPS = "To approve it, follow these steps: 1. Open the console. 2. Click **Updates**. 3. Close the window."
check("an inline numbered run splits into its steps, no bare '2.' fragments",
      F.split_sentences(STEPS) == ["To approve it, follow these steps: Open the console.",
                                   "Click **Updates**.", "Close the window."], str(F.split_sentences(STEPS)))
check("... and its markers are not numbers rule 2b demands", F._numbers(STEPS) == set(), str(F._numbers(STEPS)))
check("a lone inline '1.' is still prose", "1" in F._numbers("Use version 1. Then stop."))
check("a run must start at 1: '2. ... 3.' alone is left as written",
      "2" in F._numbers("Section 2. Then section 3. Then stop."))
QUOTED = "The SOP states: 'Do the member server first. A domain controller is not the place.' This keeps it safe."
check("a sentence ending inside a closing quote is split there",
      F.split_sentences(QUOTED) == ["The SOP states: 'Do the member server first.",
                                    "A domain controller is not the place.'", "This keeps it safe."],
      str(F.split_sentences(QUOTED)))
check("'first', 'second', 'third', 'once' are ordinary words, not numbers",
      F._numbers("Check first, then a second look, a third pass, and close it once complete.") == set())
check("'twice', 'half' and real quantities still are",
      F._numbers("Reboot twice, keep half free, wait five minutes, open port 5985.")
      == {"twice", "half", "five", "5985"})

print("case 4e the judge never emits a backslash (amendment 14)")
PATH_SRC = "Windows PowerShell opens at `PS C:\\WINDOWS\\system32>` on every machine."
check("what the judge reads has '/' for '\\'", "\\" not in F.judge_text(PATH_SRC) and "C:/WINDOWS/system32" in F.judge_text(PATH_SRC))
check("normalize() folds the separator on every side",
      F.normalize("C:\\WINDOWS\\system32") == F.normalize("C:/WINDOWS/system32"))
check("a quote copied from the folded text verifies against the ORIGINAL source",
      F.verify("It opens at PS C:\\WINDOWS\\system32> on every machine.", F.judge_text(PATH_SRC), PATH_SRC).supported)
import structured as _structured
_seen: list[str] = []
_real = _structured.ask_json
_structured.ask_json = lambda prompt, schema, **kw: (_seen.append(prompt), {"quotes": [""]})[1]
try:
    F.extract_quotes(["It opens at C:\\WINDOWS."], PATH_SRC)
finally:
    _structured.ask_json = _real
check("extract_quotes() sends the judge no backslash at all - sources AND sentences",
      len(_seen) == 1 and "\\" not in _seen[0] and "C:/WINDOWS" in _seen[0], repr(_seen[0][-80:]) if _seen else "no call")

print("case 4f the judge's model is its own setting (amendment 16)")
import os as _os2
_saved = {k: _os2.environ.pop(k, None) for k in ("JUDGE_MODEL", "LOCAL_MODEL")}
try:
    check("by default the judge IS the answer model (committed readings keep their meaning)",
          F.judge_model() == F.answer_model())
    _os2.environ["JUDGE_MODEL"] = "other-judge:7b"
    check("JUDGE_MODEL picks a different judge", F.judge_model() == "other-judge:7b" and F.answer_model() != "other-judge:7b")
    _calls: list[dict] = []
    _structured.ask_json = lambda prompt, schema, **kw: (_calls.append(kw), {"quotes": [""]})[1]
    try:
        F.extract_quotes(["One sentence."], "Source text here.")
    finally:
        _structured.ask_json = _real
    check("extract_quotes() sends the judge model, not the answer model", _calls and _calls[0].get("model") == "other-judge:7b",
          str(_calls[0].get("model") if _calls else None))
finally:
    for k, v in _saved.items():
        _os2.environ.pop(k, None)
        if v is not None:
            _os2.environ[k] = v

print("case 4c an ellipsis joins two real passages (amendment 9)")
ELL_SRC = ("Open the console and click Reports. Pick a filter if you want one. "
       "The last section of the pane shows the status summary of the update.")
ELL_SENT = "The report pane shows the status summary after you open Reports."
check("two verbatim passages joined by '...' support one sentence",
      F.verify(ELL_SENT, "Open the console and click Reports. ... The last section of the pane "
               "shows the status summary of the update.", ELL_SRC).supported)
check("the unicode ellipsis is the same separator",
      F.verify(ELL_SENT, "Open the console and click Reports.\u2026the status summary of the update.", ELL_SRC).supported)
check("one invented piece still fails the whole quote",
      F.verify(ELL_SENT, "Open the console and click Reports. ... The pane shows a full audit trail.",
               ELL_SRC).why == "quote is not in the cited text")
check("a piece under 12 chars proves nothing, even beside a real one",
      F.verify(ELL_SENT, "Open the console and click Reports. ... the update", ELL_SRC).why.startswith("quote under"))
check("a trailing '...' is a cut, not a piece",
      F.verify(ELL_SENT, "the status summary of the update...", ELL_SRC).supported)
check("rule 2b still reads the whole quote: a number in no piece still fails",
      not F.verify("Wait 24 hours, then open Reports.",
                   "Open the console and click Reports. ... Pick a filter if you want one.", ELL_SRC).supported)
check("a source that contains '...' itself: the whole quote is matched first",
      F.verify("Browse to the folder under Windows.", "Browse to C:\\Windows\\... and open it",
               "Browse to C:\\Windows\\... and open it").supported)

print("case 5  calibration on the real pairs")
texts = {c.chunk_id: c.text for c in retrieval.load_corpus()}
pairs = F.load_calibration()
real = [p for p in pairs if p["construction"].startswith("real answer")]
check("12 constructed pairs, 6 and 6", len(pairs) - len(real) == 12
      and sum(p["label"] == "faithful" for p in pairs if p not in real) == 6)
check("9 real pairs, 7 and 2, each with a written reason", len(real) == 9
      and sum(p["label"] == "faithful" for p in real) == 7 and all(p.get("reason") for p in real))
# A real unfaithful pair names its defect - it need not be the last sentence (amendment 4).
check("every real defect is a sentence of its answer",
      all(p["defect"] in F.split_sentences(p["answer"]) for p in real if p["label"] == "unfaithful"))


def lazy(sentences: list[str], sources: str) -> list[str]:
    pool = F.split_sentences(" ".join(sources.split()))
    out = []
    for s in sentences:
        best = max(pool, key=lambda p: len(F._content_stems(p) & F._content_stems(s)))
        out.append(best.rstrip(".") if F._content_stems(best) & F._content_stems(s) else "")
    return out


DEFECTS = {F.defect_sentence(p) for p in pairs if p["label"] == "unfaithful"}   # amendment 12: one rule, shared


def perfect(sentences: list[str], sources: str) -> list[str]:
    return ["" if s in DEFECTS else q for s, q in zip(sentences, lazy(sentences, sources))]


good = F.calibrate(pairs, texts, extract=perfect)
check("PERFECT judge meets the bar", good["passes_bar"] and good["false_pass"] == 0,
      f"agreement {good['agreement']:.2f} fp {good['false_pass']} misses {[m['id'] for m in good['misses']]}")
bad = F.calibrate(pairs, texts, extract=lazy)
fp_ids = sorted(m["id"] for m in bad["misses"] if m["kind"] in ("FALSE PASS", "WRONG REASON"))
check("LAZY judge fails the bar (the bar can fail)", not bad["passes_bar"], f"false passes {fp_ids}")
check("the number rule catches both changed-number pairs WITHOUT the judge's help",
      "thresholds-number" not in fp_ids and "skew-number" not in fp_ids, str(fp_ids))

order: list[str] = []
F.calibrate(pairs[:3], texts, extract=lambda s, src: (order.append("judge"), [""] * len(s))[1],
            fresh=lambda: order.append("fresh"))
check("amendment 5: a fresh load before EVERY pair, never after",
      order == ["fresh", "judge"] * 3, str(order))

print("case 5b the held-out pairs (amendment 7)")
held = F.load_calibration(F.HELDOUT)
check("12 held-out pairs, 9 and 3, each with a reason",
      len(held) == 12 and sum(p["label"] == "faithful" for p in held) == 9
      and all(p.get("reason") for p in held))
# The defect is a POINTER to a sentence; compared after the same marker removal the
# splitter applies (amendment 8), so the label file never changes to suit the code.
check("every held-out defect is a sentence of its answer",
      all(F.split_sentences(p["defect"])[0] in F.split_sentences(p["answer"])
          for p in held if p["label"] == "unfaithful"))
check("no held-out id is also a calibration id (they are disjoint sets)",
      not ({p["id"] for p in held} & {p["id"] for p in pairs}))
check("every held-out source is a real chunk", all(s in texts for p in held for s in p["sources"]))

print("case 5d the held-out v2 pairs (amendment 11)")
held2 = F.load_calibration(REPO_ROOT / "evals" / "fixtures" / "faithfulness-heldout-v2.jsonl")
check("12 held-out v2 pairs, 9 and 3, each with a reason",
      len(held2) == 12 and sum(p["label"] == "faithful" for p in held2) == 9
      and all(p.get("reason") for p in held2))
check("every v2 defect is a sentence of its answer",
      all(F.split_sentences(p["defect"])[0] in F.split_sentences(p["answer"])
          for p in held2 if p["label"] == "unfaithful"))
check("v2 ids are disjoint from v1 and from the calibration set",
      not ({p["id"] for p in held2} & ({p["id"] for p in held} | {p["id"] for p in pairs})))
# What is held out is the ANSWERS. Every v2 question targets a chunk no other set uses, but
# retrieval also cited these four, which v1 or calibration pairs cite too. Pinned, not hidden:
# a fifth would mean the label file changed (amendment 11).
SHARED_CITED = {"data/microsoft/3-approve-and-deploy-updates-in-wsus.md#overview",
                "data/microsoft/3-approve-and-deploy-updates-in-wsus.md#a-name-bkm-3-3-a-3-3-review-installed-updates-with-wsus-reports",
                "data/microsoft/Windows-Time-Service-Tools-and-Settings.md#network-port",
                "data/runbooks/winrm-remoting.md#procedure"}
check("v2 shares exactly the four named cited chunks with earlier sets",
      {s for p in held2 for s in p["sources"]} & {s for p in held + pairs for s in p["sources"]} == SHARED_CITED)
check("every v2 source is a real chunk", all(s in texts for p in held2 for s in p["sources"]))

print("case 5f the held-out v3 pairs (amendment 15)")
held3 = F.load_calibration(REPO_ROOT / "evals" / "fixtures" / "faithfulness-heldout-v3.jsonl")
check("11 held-out v3 pairs, 10 and 1, each with a reason",
      len(held3) == 11 and sum(p["label"] == "faithful" for p in held3) == 10
      and all(p.get("reason") for p in held3))
check("every v3 defect is a sentence of its answer",
      all(F.defect_sentence(p) in F.split_sentences(p["answer"]) for p in held3 if p["label"] == "unfaithful"))
check("v3 ids are disjoint from every earlier set",
      not ({p["id"] for p in held3} & ({p["id"] for p in held + held2 + pairs})))
check("v3 shares exactly one cited chunk with earlier sets (retrieval chose it), pinned",
      {s for p in held3 for s in p["sources"]} & {s for p in held + held2 + pairs for s in p["sources"]}
      == {"data/runbooks/time-sync.md#procedure"})
check("every v3 source is a real chunk", all(s in texts for p in held3 for s in p["sources"]))

print("case 5c a reading is saved: every pair, every sentence, with its condition (amendment 10)")
import tempfile
small = pairs[:4]
got = F.calibrate(small, texts, extract=perfect)
check("one row per pair - passing pairs too, not only the misses",
      [r["id"] for r in got["rows"]] == [p["id"] for p in small]
      and all(r["checks"] for r in got["rows"]), str([r["id"] for r in got["rows"]]))
check("each row carries the quote and the code's ruling per sentence",
      all({"sentence", "quote", "supported", "why"} <= set(c) for r in got["rows"] for c in r["checks"]))
cond = {"started_at": "2026-10-12T03:30:00Z", "pairs": "evals/fixtures/faithfulness-heldout.jsonl",
        "pairs_sha256": "a" * 64, "judge_prompt_sha256": F.judge_prompt_sha256(),
        "judge_max_tokens": F.JUDGE_MAX_TOKENS, "model": "m", "model_digest": "d",
        "ollama_version": "v", "fresh_load_per_pair": True}
with tempfile.TemporaryDirectory() as tmp:
    path = F.write_reading(F.reading_document(got, cond), Path(tmp))
    back = json.loads(path.read_text(encoding="utf-8"))
    check("named by time and pair file, sorts under ls",
          path.name == "20261012T033000Z-calibration-faithfulness-heldout.json", path.name)
    check("reads back: condition, summary, rows", back["condition"] == cond
          and back["summary"]["n"] == len(small) and len(back["rows"]) == len(small))
    check("no temp file left behind", [q.name for q in Path(tmp).iterdir()] == [path.name])
import os as _os
_here = Path.cwd()
_os.chdir(REPO_ROOT)
check("a relative --pairs path is named as the repo names it",
      F._repo_relative(Path("evals/fixtures/faithfulness-heldout.jsonl")) == "evals/fixtures/faithfulness-heldout.jsonl")
_os.chdir(REPO_ROOT / "tests")
check("... from any directory",
      F._repo_relative(Path("../evals/fixtures/faithfulness-heldout.jsonl")) == "evals/fixtures/faithfulness-heldout.jsonl")
_os.chdir(_here)
h = F.judge_prompt_sha256()
check("the prompt hash is a sha256 and moves when the prompt does",
      len(h) == 64 and h != __import__("hashlib").sha256((F.JUDGE_SYSTEM + " ").encode()).hexdigest())
lazy_got = F.calibrate(pairs, texts, extract=lazy)
check("a miss's checks in the summary are the same object as its row's",
      all(m["checks"] == next(r for r in lazy_got["rows"] if r["id"] == m["id"])["checks"] for m in lazy_got["misses"]))

print("case 5e the bar is scored at the defect sentence (amendment 12)")
U = {"id": "u", "label": "unfaithful", "answer": "The port is 5985. Open it to everyone."}
FA = {"id": "f", "label": "faithful", "answer": "The port is 5985."}
def row(judged, flags, err=""):
    sents = F.split_sentences(U["answer"])
    return {"id": "u", "judged": judged, "error": err,
            "checks": [{"sentence": x, "quote": "", "supported": ok, "why": ""} for x, ok in zip(sents, flags)]}
check("a constructed pair's defect is its last sentence", F.defect_sentence(U) == "Open it to everyone.")
check("caught on the defect sentence -> AGREE", F.classify(U, row("unfaithful", [True, False])) == "AGREE")
check("caught on ANOTHER sentence, defect passed -> WRONG REASON",
      F.classify(U, row("unfaithful", [False, True])) == "WRONG REASON")
check("judged faithful -> FALSE PASS", F.classify(U, row("faithful", [True, True])) == "FALSE PASS")
check("a crashed judge on an unfaithful pair is an ERROR, never a catch",
      F.classify(U, {"id": "u", "judged": "unfaithful", "error": "ModelError: 500", "checks": []}) == "ERROR")
got = F.score([U, FA], [row("unfaithful", [False, True]),
                        {"id": "f", "judged": "faithful", "error": "", "checks": []}])
check("a wrong-reason catch counts as a false pass and fails the bar",
      got["false_pass"] == 1 and got["wrong_reason"] == 1 and not got["passes_bar"], str({k: got[k] for k in ("false_pass", "wrong_reason", "agreement")}))
try:
    F.classify({**U, "defect": "A sentence the answer never had."}, row("unfaithful", [False, False]))
    check("a defect pointer that matches no judged sentence is refused, not guessed", False)
except ValueError:
    check("a defect pointer that matches no judged sentence is refused, not guessed", True)
# The committed readings, re-scored with no model. Pinned: a change here means the
# scoring rule or a label file moved (ADR 0026 amendment 12).
EV = REPO_ROOT / "projects" / "02-ask-the-vault" / "evidence" / "judge-readings"
def rescored(name, labels):
    doc = json.loads((EV / name).read_text(encoding="utf-8"))
    return F.score(F.load_calibration(REPO_ROOT / labels), doc["rows"])
v1 = rescored("20261013T034428Z-calibration-faithfulness-heldout.json", "evals/fixtures/faithfulness-heldout.jsonl")
check("held-out v1 re-scored: 11/12, FP 0, FF 1, no errors (all 3 caught on their defect)",
      round(v1["agreement"], 3) == 0.917 and v1["false_pass"] == 0 and v1["false_fail"] == 1 and v1["errors"] == 0)
v2 = rescored("20261013T041241Z-calibration-faithfulness-heldout-v2.json", "evals/fixtures/faithfulness-heldout-v2.jsonl")
check("held-out v2 re-scored: 4/12, FALSE PASS 1 (bench-diagnosis, wrong reason), FF 6, 1 error",
      round(v2["agreement"], 3) == 0.333 and v2["false_pass"] == 1 and v2["wrong_reason"] == 1
      and v2["false_fail"] == 6 and v2["errors"] == 1
      and [m["id"] for m in v2["misses"] if m["kind"] == "WRONG REASON"] == ["heldout2-bench-diagnosis"],
      str({k: v2[k] for k in ("agreement", "false_pass", "wrong_reason", "false_fail", "errors")}))

print("case 6  a run record")
rec = {"header": {"model_digest": "abc index:" + "f" * 64},
       "cases": [{"id": "a", "result": "pass", "detail": {"outcome": "answered", "cited": ["c1"],
                  "answer": "Below five percent the host is treated as failed."}},
                 {"id": "r", "result": "pass", "detail": {"outcome": "refused-not-in-sources",
                  "cited": [], "answer": "No."}}]}
check("index hash read from the header", F.record_index_hash(rec) == "f" * 64)
rows = F.judge_record(rec, {"c1": SRC},
                      extract=lambda s, src: ["Below five percent the host is treated as failed"])
check("only answered rows judged", [r["id"] for r in rows] == ["a"] and rows[0]["faithful"] is True)
def boom(sents, src):
    raise RuntimeError("runaway")
rows = F.judge_record(rec, {"c1": SRC}, extract=boom)
check("one judge failure is an UNJUDGED row, not a stopped pass",
      len(rows) == 1 and rows[0]["faithful"] is None and "runaway" in rows[0]["error"])
order = []
F.judge_record(rec, {"c1": SRC}, extract=lambda s, src: (order.append("judge"), ["x" * 20])[1],
               fresh=lambda: order.append("fresh"))
check("amendment 5: a fresh load before each JUDGED case only", order == ["fresh", "judge"], str(order))
check("the judge has its own cap, above the answer step's", F.JUDGE_MAX_TOKENS > 2048)
try:
    F.record_index_hash({"header": {"model_digest": "abc"}})
    check("a record without an index is refused", False)
except ValueError:
    check("a record without an index is refused", True)

print("case 7  the judge prompt quotes no calibration pair (it must not learn its own test)")
leaks = [p["id"] for p in pairs
         for sent in F.split_sentences(p["answer"])
         if len(sent) > 20 and F.normalize(sent)[:40] in F.normalize(F.JUDGE_SYSTEM)]
check("no calibration sentence appears in JUDGE_SYSTEM", leaks == [], str(leaks))

print()
if failures:
    print(f"FAIL: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PASS: faithfulness - the code splits, verifies quotes and numbers, and its bar can fail")
