#!/usr/bin/env python3
"""Does the answer say only what its cited sources say? (P02 step 6d, ADR 0026)

THE DIVISION OF LABOUR - Lab 10's rule, one level down:
  the CODE splits the answer into sentences        -> the judge cannot drop one
  the JUDGE copies, per sentence, a supporting quote -> extraction, not a verdict
  the CODE checks every quote is really in the text -> no support it did not copy

REPORTED, NEVER GATED. This file reads a finished run record and writes its
own reading beside it. It never runs inside the answer scorer: the step-6
finding was that a run reproduces only when its sequence of model calls does,
so judge calls interleaved with answers would move the answers off the
baseline. A separate pass also cannot reach the harness's exit code.

    python src/faithfulness.py --calibrate              # the judge, measured (bench)
    python src/faithfulness.py --calibrate --pairs evals/fixtures/faithfulness-heldout.jsonl
    python src/faithfulness.py --record evals/runs/X.json   # one run, judged (bench)
    python src/faithfulness.py --rescore evals/runs/<reading>.json   # a saved reading, re-scored (no model)
    python tests/test_faithfulness.py                    # the code, no model (CI)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
CALIBRATION = REPO / "evals" / "fixtures" / "faithfulness-calibration.jsonl"
# Amendment 7: answers to NEW questions, labelled and committed before the judge read
# them. Measured with the same code and bar; never used to design the judge.
HELDOUT = REPO / "evals" / "fixtures" / "faithfulness-heldout.jsonl"
HELDOUT_V2 = REPO / "evals" / "fixtures" / "faithfulness-heldout-v2.jsonl"   # amendment 11

# The judge's generation cap - ITS OWN, not the answer step's MAX_TOKENS (2,048).
# Sized like P04's: ~2x the largest call observed. tools/probe_judge_runaway.py
# measured 3,084 tokens (thinking included) for a judge call that finished with the
# right quotes; at 2,048 two calibration calls still ran away after the prompt fix.
# A cap only truncates - every call that finished under 2,048 is unchanged.
JUDGE_MAX_TOKENS = 6144
MIN_QUOTE_CHARS = 12          # a quote shorter than this proves nothing ("the service")
FALSE_PASS_MAX = 0            # Lab 10's bar: the dangerous error is never tolerated
AGREEMENT_MIN = 0.90

# --- the code's half: sentences, normalisation, verification ----------------

# Split after . ! ? when the next sentence starts with a capital, digit, quote,
# bracket or backtick. "e.g., print" and "5.5" do not split.
# AMENDMENT 13 (2026-10-14): a sentence may end inside a closing quote - "...has a bug.' This"
# (heldout2-jea-dc-first) stayed one sentence, so one quote had to cover two claims.
_SPLIT = re.compile(r'(?:(?<=[.!?])|(?<=[.!?][\'"\u2019\u201d]))\s+(?=[A-Z0-9"(\[`*])')   # "*": bold list item (amendment 8)
_ABBREV = re.compile(r"\b(e\.g|i\.e|etc|vs)\.$", re.IGNORECASE)
_STOP = frozenset("that this with from have will must when then than they their there these "
                  "those which while what your into also only does were been being because "
                  "should would could about after before other more most some such".split())


# AMENDMENT 8 (2026-10-12): list markers are LAYOUT, not content. A numbered list split
# into fragments ("2.") that no quote can support, and rule 2b demanded quotes carry
# "1)" as if it were a fact (heldout-dfs-referral; the winrm/patching answers on
# 2026-10-10). Removed before the code reads ANY text - the answer's sentences, the
# judge's quote and the source - so a quote that runs across list items without their
# numbers (heldout-wsus-verify) is the continuous text it really is.
#   at a line start:  "1. "  "2) "  "(3) "  "- "  "+ "  "• "
#   inline:           "1) "  "(2) "   - never "1. " inline: "version 1. Then" is prose
_MARKER_LINE = re.compile(r"(?m)^[ \t]*(?:\(?\d{1,2}[.)]|[-+\u2022])[ \t]+")
_MARKER_INLINE = re.compile(r"(?<![\w.(])\(?\d{1,2}\)[ \t]+")
# AMENDMENT 13: an inline numbered list ("follow these steps: 1. Open ... 2. Click ...")
# split into bare "2." fragments no quote can support (heldout2-wsus-approve-test).
# An inline "N." is still prose on its own ("version 1. Then") - it is a marker only as
# part of a RUN: "1." then "2." (then "3." ...), in order, each before a capital or markup.
_INLINE_DOT = re.compile(r"(?<![\w.(])(\d{1,2})\.[ \t]+(?=[A-Z*`(\[\"'])")


def _strip_inline_run(text: str) -> str:
    run, want = [], 1
    for m in _INLINE_DOT.finditer(text):
        if int(m.group(1)) == want:
            run.append(m.span())
            want += 1
    if len(run) < 2:                      # a lone "1." is prose
        return text
    for start, end in reversed(run):
        text = text[:start] + text[end:]
    return text


def strip_list_markers(text: str) -> str:
    return _strip_inline_run(_MARKER_INLINE.sub("", _MARKER_LINE.sub("", text)))


def split_sentences(text: str) -> list[str]:
    parts: list[str] = []
    for piece in _SPLIT.split(strip_list_markers(text).strip()):
        piece = piece.strip()
        if not piece:
            continue
        if parts and _ABBREV.search(parts[-1]):
            parts[-1] = f"{parts[-1]} {piece}"
        else:
            parts.append(piece)
    return parts


def normalize(text: str) -> str:
    text = strip_list_markers(text)          # amendment 8 - before newlines collapse
    # Every quotation mark folds to one: a mark is punctuation, never the fact.
    # AMENDMENT 6 (2026-10-10): the judge copied the source's "it works" as 'it works'
    # (JSON strings make double quotes awkward to emit) - three sightings, each a
    # correct passage rejected as "not in the cited text".
    # AMENDMENT 14 (2026-10-16): a path separator folds too - "\\" and "/" are layout of a
    # path, not the fact. Must match judge_text() below, which sends the judge "/" so it
    # never has to emit a JSON escape.
    text = text.replace("\\", "/")
    text = (text.replace("’", "'").replace("‘", "'")
                .replace("“", "'").replace("”", "'").replace('"', "'"))
    text = re.sub(r"[`*_]", "", text.lower())
    return re.sub(r"\s+", " ", text).strip()


# AMENDMENT 13: "first", "second", "third" and "once" left the list. In these answers they
# are ordinary words far more often than quantities - "check first" (heldout2-bench-diagnosis,
# whose pair failed ONLY on that word), "once complete" (heldout2-wsus-approve-test). A rule
# that fires on ordinary words catches for the wrong reason, which amendment 12 now counts.
_NUMBER_WORDS = frozenset("zero one two three four five six seven eight nine ten eleven twelve "
                          "fifteen twenty thirty forty fifty sixty hundred thousand half "
                          "twice".split())


def _numbers(text: str) -> set[str]:
    """Digits and number words. In a runbook a number IS the fact: five minutes,
    ten percent, port 5986. A quote that supports a sentence must carry every
    number the sentence states - otherwise "below two percent ... failed" is
    'supported' by "below five percent ... failed" (ADR 0026, rule 2b)."""
    words = re.findall(r"[a-z0-9]+", normalize(text))
    return {w for w in words if w.isdigit() or w in _NUMBER_WORDS}


def _content_stems(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", normalize(text))
    return {w[:5] for w in words if len(w) >= 4 and w not in _STOP}


# AMENDMENT 9 (2026-10-12): one quote per sentence is the schema's limit, not the
# judge's. A sentence that summarises two passages (heldout-wsus-verify, sentence 4)
# got both, joined with "..." - the right support, rejected as "not in the cited text"
# because the code looked for the ellipsis too. An ellipsis in a quote is the judge's
# own separator: if the WHOLE quote is not in the source, every piece between the
# ellipses must stand alone (at least MIN_QUOTE_CHARS, verbatim in the cited text).
# Code only - the judge is asked exactly what it was asked before, so its readings
# are unchanged and the effect is predictable to the pair. The whole-quote check runs
# first, so a quote that passed before still passes (a source may contain "..." itself).
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|\u2026)\s*")


def quote_pieces(q: str, src: str) -> list[str]:
    """The passages a normalised quote claims: itself if it is in the normalised
    source, else the pieces between its ellipses (a trailing "..." is a cut, not a piece)."""
    if q in src:
        return [q]
    return [piece for piece in _ELLIPSIS.split(q) if piece]


@dataclass
class SentenceCheck:
    sentence: str
    quote: str
    supported: bool
    why: str                  # "ok" or the reason the quote was not accepted


def verify(sentence: str, quote: str, sources: str) -> SentenceCheck:
    """The code's ruling on one judge extraction. Order = the first failing rule."""
    q = normalize(quote)
    if not q:
        return SentenceCheck(sentence, quote, False, "no quote - the judge found no support")
    src = normalize(sources)
    pieces = quote_pieces(q, src)            # amendment 9
    if not pieces or any(len(piece) < MIN_QUOTE_CHARS for piece in pieces):
        return SentenceCheck(sentence, quote, False, f"quote under {MIN_QUOTE_CHARS} chars")
    if any(piece not in src for piece in pieces):
        return SentenceCheck(sentence, quote, False, "quote is not in the cited text")
    if not (_content_stems(sentence) & _content_stems(quote)):
        return SentenceCheck(sentence, quote, False, "quote shares no content word with the sentence")
    missing = _numbers(sentence) - _numbers(quote)
    if missing:
        return SentenceCheck(sentence, quote, False, f"quote lacks the sentence's number(s) {sorted(missing)}")
    return SentenceCheck(sentence, quote, True, "ok")


def schema(n: int) -> dict[str, Any]:
    """Exactly one quote per sentence - the judge cannot skip one."""
    return {"type": "object",
            "properties": {"quotes": {"type": "array", "minItems": n, "maxItems": n,
                                      "items": {"type": "string"}}},
            "required": ["quotes"], "additionalProperties": False}


# --- the judge's half: copy, never decide -----------------------------------

JUDGE_SYSTEM = (
    "You are a copying tool, not a grader. You are given SOURCES and a numbered list of "
    "SENTENCES. The sentences were written in their own words. Your only job is to copy, "
    "for each sentence, the passage of the SOURCES that supports it - copied out of the "
    "SOURCES exactly as it is written there. Only the copied passage must be exact; the "
    "sentence never has to match the SOURCES word for word.\n"
    "A passage supports a sentence when it states the same facts. None of these stop a "
    "passage from supporting a sentence: different wording or word order; synonyms; "
    "'must' or 'should' where the SOURCES state the same rule plainly; a sentence that "
    "leaves out details the passage gives; a sentence that joins points the SOURCES make "
    "separately.\n"
    "EVERY part of a sentence must be supported - an example, an aside or anything in "
    "brackets counts as part of it. If any part is not in the SOURCES, the entry for "
    "that sentence is an empty string, even when the rest of it is supported.\n"
    "Give an empty string ONLY when the sentence states something the SOURCES do not: a "
    "fact, a number, a step or a piece of advice that is not in them; a name (a service, "
    "a tool, a protocol) that does not appear in them; or a claim that differs from or "
    "reverses what they say. An empty string means 'the SOURCES do not say this' - never "
    "use it to express doubt about wording. Never use your own knowledge. Return one "
    "entry per sentence, in order."
)
# 6d PROBE (2026-10-10): the first wording said "copy ... the shortest exact passage
# that states what the sentence says - copied character for character". qwen3 read
# "exact" as a demand that the SENTENCE match the source word for word, spent
# 2,000-3,100 tokens of thinking deciding paraphrases did not count, and returned
# empty quotes for faithful restatements (3 runaways at the 2,048 cap, 1 empty even
# at 6,144). The two requirements are now stated apart: the sentence may differ in
# wording; the QUOTE is copied exactly. A principle, no example - quoting a
# calibration pair here would teach the judge its own test (test_faithfulness case 7).
# AMENDMENT 6b (2026-10-10): the probe's full thinking showed the judge still DECIDING,
# three ways - "not about the meaning but exact wording"; "the sentence adds 'must'
# ... that's an added instruction"; "the safest answer is to return empty strings".
# The old "or the sentence adds a fact ... give an empty string" clause asked it to
# grade. Now: what counts as support is listed, what earns an empty string is listed
# narrowly (and keeps every defect the unfaithful pairs carry: new fact, number,
# step, advice, name, a changed or reversed claim), and empty is named as a claim,
# not a hedge. Still a principle - no example.
# AMENDMENT 6c (2026-10-10): 6b took false fail 7 -> 0 but false pass 0 -> 1. The probe:
# the judge SAW an unsupported name inside "(e.g., ...)" and reasoned "the instruction
# says that if the sentence says less than the passage, it's still okay ... the main
# point is in the SOURCES". 6b's own wording licensed PARTIAL support - the blind spot
# owed since amendment 3. Now: "leaves out details" (no reading that excuses an
# addition) and every part of a sentence must be supported. Designed against the
# calibration set twice now: a bar met after this is optimistic until a held-out
# --record run, labelled BEFORE reading the judge, agrees.

Extract = Callable[[list[str], str], list[str]]
Fresh = Callable[[], None]
Progress = Callable[[str], None]

# AMENDMENT 5 (2026-10-10): every judge reading starts on a freshly loaded model.
# The probe sent real-restart-recovery's exact request as the 2nd call and got three
# good quotes; inside --calibrate, as the 15th call, the same request got three empty
# ones. A verdict that depends on its neighbours makes the calibration set
# non-additive (adding a pair could move pairs nobody touched) and confounds every
# one-change-per-measure comparison with order. The 6c fresh-load rule, per call.


def _no_reload() -> None:
    """The default for scripted judges (CI): nothing is loaded, nothing to unload."""


# AMENDMENT 14 (2026-10-16): heldout2-shell-identify's judge call died with "prediction
# aborted, token repeat limit reached" on Ollama 0.40.3 and 0.40.4 alike. Streamed to the
# abort (tools/probe_judge_runaway.py): the judge's thinking was sound, then the FIRST
# quote - copying "PS C:\\WINDOWS..." - emitted the JSON escape "\\" 203 times. Escaping a
# backslash inside a decoder-constrained string is where qwen3 loops. The judge is never
# asked to emit one: what it reads has "\\" as "/" (normalize() folds the same way, so a
# quote copied from the folded text still verifies against the original source).

# AMENDMENT 16 (2026-10-16): the judge was the SAME model that writes the answers, so it
# shares their blind spots - the review of the calls named it the first thing to change.
# The judge's model is now its own setting: JUDGE_MODEL, defaulting to the answer model
# so every committed reading keeps its meaning. Project 3 brings the second model; a
# reading says which judged it, and calibrate() prints a warning while they are the same.

def judge_model() -> str:
    import model_client
    return os.environ.get("JUDGE_MODEL") or os.environ.get("LOCAL_MODEL") or model_client.DEFAULT_MODEL


def answer_model() -> str:
    import model_client
    return os.environ.get("LOCAL_MODEL") or model_client.DEFAULT_MODEL


def judge_text(text: str) -> str:
    return text.replace("\\", "/")


def extract_quotes(sentences: list[str], sources: str) -> list[str]:
    from answer import ANSWER_NUM_CTX
    from structured import ask_json
    numbered = "\n".join(f"{i}. {judge_text(s)}" for i, s in enumerate(sentences, 1))
    prompt = f"SOURCES:\n{judge_text(sources)}\n\nSENTENCES:\n{numbered}"
    data = ask_json(prompt, schema(len(sentences)), system=JUDGE_SYSTEM, num_ctx=ANSWER_NUM_CTX,
                    model=judge_model(),
                    max_tokens=JUDGE_MAX_TOKENS)
    return list(data["quotes"])


@dataclass
class Assessment:
    faithful: bool | None     # None: nothing to judge (no sentences)
    checks: list[SentenceCheck] = field(default_factory=list)

    @property
    def unsupported(self) -> list[str]:
        return [c.sentence for c in self.checks if not c.supported]


def assess(answer_text: str, source_texts: list[str], *, extract: Extract = extract_quotes) -> Assessment:
    sentences = split_sentences(answer_text)
    if not sentences:
        return Assessment(None)
    sources = "\n\n".join(source_texts)
    quotes = extract(sentences, sources)
    if len(quotes) != len(sentences):        # a server that ignored the schema
        raise ValueError(f"judge returned {len(quotes)} quotes for {len(sentences)} sentences")
    checks = [verify(s, q, sources) for s, q in zip(sentences, quotes)]
    return Assessment(all(c.supported for c in checks), checks)


# --- calibration: the judge, measured ----------------------------------------

def load_calibration(path: Path = CALIBRATION) -> list[dict[str, Any]]:
    from suite import parse_suite
    return list(parse_suite(path).cases)


# AMENDMENT 12 (2026-10-14): the bar is scored at the DEFECT SENTENCE. Held-out v2's
# "false pass 0" was luck: bench-diagnosis's inverted sentence PASSED on a verbatim
# quote, and the pair failed only because rule 2b misread "first" in another sentence.
# A verdict-level count calls that a catch. Now an unfaithful pair is caught only when
# the sentence its label names is flagged; a catch elsewhere is a WRONG REASON and
# counts as a false pass - the dangerous error is passing the false claim, whatever
# else failed. A crashed judge is an ERROR: a miss for agreement, never a catch (a
# crash used to count as catching an unfaithful pair - the same kind of luck).

def defect_sentence(pair: dict[str, Any]) -> str:
    """The sentence an unfaithful label points at. Real and held-out pairs name it in
    `defect`; a constructed pair carries exactly one deliberate defect in its LAST
    sentence (ADR 0026, the calibration set's construction rule)."""
    if pair.get("defect"):
        return split_sentences(pair["defect"])[0]
    return split_sentences(pair["answer"])[-1]


def classify(pair: dict[str, Any], row: dict[str, Any]) -> str:
    """One pair's outcome: AGREE, FALSE PASS, WRONG REASON, FALSE FAIL or ERROR."""
    if row["error"]:
        return "ERROR"
    faithful = row["judged"] == "faithful"
    if pair["label"] == "faithful":
        return "AGREE" if faithful else "FALSE FAIL"
    if faithful:
        return "FALSE PASS"
    target = defect_sentence(pair)
    hit = [c for c in row["checks"] if c["sentence"] == target]
    if not hit:          # the label points at a sentence the splitter never produced
        raise ValueError(f"{pair['id']}: defect sentence not among the judged sentences")
    return "WRONG REASON" if hit[0]["supported"] else "AGREE"


def score(pairs: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The bar, from judged rows alone - no model. Two error rates, two denominators
    (Lab 10); a wrong-reason catch is a false pass; an error is a miss of its own."""
    by_id = {r["id"]: r for r in rows}
    n_true = sum(p["label"] == "faithful" for p in pairs)
    n_false = len(pairs) - n_true
    counts = {"AGREE": 0, "FALSE PASS": 0, "WRONG REASON": 0, "FALSE FAIL": 0, "ERROR": 0}
    misses: list[dict[str, Any]] = []
    for p in pairs:
        r = by_id[p["id"]]
        kind = classify(p, r)
        counts[kind] += 1
        r["outcome"] = kind
        if kind != "AGREE":
            misses.append({"id": p["id"], "kind": kind, "construction": p.get("construction"),
                           "error": r["error"], "checks": r["checks"],
                           "defect": defect_sentence(p) if p["label"] == "unfaithful" else ""})
    n = len(pairs)
    fp = counts["FALSE PASS"] + counts["WRONG REASON"]
    agreement = counts["AGREE"] / n if n else 0.0
    return {"n": n, "agreement": agreement, "false_pass": fp,
            "wrong_reason": counts["WRONG REASON"],
            "false_pass_rate": fp / n_false if n_false else 0.0,
            "false_fail": counts["FALSE FAIL"],
            "false_fail_rate": counts["FALSE FAIL"] / n_true if n_true else 0.0,
            "errors": counts["ERROR"],
            "passes_bar": n > 0 and fp <= FALSE_PASS_MAX and agreement >= AGREEMENT_MIN,
            "misses": misses, "rows": rows}


def calibrate(pairs: list[dict[str, Any]], texts: dict[str, str], *,
              extract: Extract = extract_quotes, fresh: Fresh = _no_reload,
              progress: Progress = lambda line: None) -> dict[str, Any]:
    """Judge every pair (a fresh load before each), then score() the rows."""
    rows: list[dict[str, Any]] = []          # amendment 10: every pair, kept
    for i, p in enumerate(pairs, 1):
        try:
            fresh()
            got = assess(p["answer"], [texts[s] for s in p["sources"]], extract=extract)
            verdict, err = bool(got.faithful), ""
            detail = [asdict(c) for c in got.checks]
        except Exception as exc:                 # noqa: BLE001 - counted, named
            verdict, err, detail = False, f"{type(exc).__name__}: {exc}", []
        row = {"id": p["id"], "label": p["label"],
               "judged": "faithful" if verdict else "unfaithful", "error": err, "checks": detail}
        rows.append(row)
        kind = classify(p, row)
        # One line per pair AS IT FINISHES: with a fresh load per pair the pass takes
        # minutes, and a silent pass is indistinguishable from a hung one (2026-10-10).
        progress(f"  [{i:>2}/{len(pairs)}] {p['id']:<34} label={p['label']:<10} "
                 f"judged={row['judged']:<10} {'' if kind == 'AGREE' else kind}"
                 + (f"  ({err[:60]})" if err else ""))
    return score(pairs, rows)


# AMENDMENT 10 (2026-10-12): a reading is SAVED, every pair, every sentence. Amendment 7
# said why cleanmgr-sageset failed; amendment 9's run printed different reasons, and
# nothing could settle which was right - --calibrate printed its reasons and kept none.
# A claim about why the judge failed must point at a file. The header is the condition
# (what the reading was taken WITH) so two readings can be told apart: same pairs, same
# prompt, same model and server, or not. Written under evals/runs (gitignored, like
# every run); a reading an ADR cites is copied byte for byte into the project's
# evidence/ and committed.

def judge_prompt_sha256() -> str:
    import hashlib
    return hashlib.sha256(JUDGE_SYSTEM.encode("utf-8")).hexdigest()


def reading_document(got: dict[str, Any], condition: dict[str, Any]) -> dict[str, Any]:
    summary = {k: got[k] for k in ("n", "agreement", "false_pass", "wrong_reason",
                                   "false_pass_rate", "false_fail", "false_fail_rate",
                                   "errors", "passes_bar")}
    return {"kind": "faithfulness-calibration", "condition": condition,
            "summary": summary, "rows": got["rows"]}


def _repo_relative(path: Path) -> str:
    """The pair file as the repo names it, whatever directory the command ran from."""
    full = path.resolve()
    return full.relative_to(REPO).as_posix() if full.is_relative_to(REPO) else str(full)


def write_reading(doc: dict[str, Any], out_dir: Path) -> Path:
    stamp = doc["condition"]["started_at"].replace("-", "").replace(":", "")
    stem = Path(doc["condition"]["pairs"]).stem
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stamp}-calibration-{stem}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)                    # a crash never leaves half a reading
    return path


# --- a run record, judged ------------------------------------------------------

def record_index_hash(record: dict[str, Any]) -> str:
    digest = record["header"]["model_digest"]
    if " index:" not in digest:
        raise ValueError("record does not name its index - not an answer-suite record")
    return digest.rsplit(" index:", 1)[1]


def judge_record(record: dict[str, Any], texts: dict[str, str], *,
                 extract: Extract = extract_quotes, fresh: Fresh = _no_reload) -> list[dict[str, Any]]:
    rows = []
    for case in record["cases"]:
        d = case["detail"]
        if d.get("outcome") != "answered":
            continue
        try:
            fresh()
            got = assess(d["answer"], [texts[c] for c in d["cited"]], extract=extract)
        except Exception as exc:                 # noqa: BLE001 - one case, named, not the pass
            # REPORTED, NEVER GATED: one judge failure is a row that says so,
            # never a pass that stops (2026-10-10: one runaway ended the run).
            rows.append({"id": case["id"], "result": case["result"], "faithful": None,
                         "unsupported": [], "checks": [],
                         "error": f"{type(exc).__name__}: {exc}"})
            continue
        rows.append({"id": case["id"], "result": case["result"], "faithful": got.faithful,
                     "unsupported": got.unsupported,
                     "checks": [asdict(c) for c in got.checks], "error": ""})
    return rows


def report(got: dict[str, Any], title: str) -> None:
    print(f"faithfulness judge {title}")
    print(f"  agreement   {got['agreement']:.3f}  (bar >= {AGREEMENT_MIN})")
    print(f"  FALSE PASS  {got['false_pass']}  rate {got['false_pass_rate']:.3f}  (bar: {FALSE_PASS_MAX})"
          f"  - of which WRONG REASON {got['wrong_reason']}")
    print(f"  false fail  {got['false_fail']}  rate {got['false_fail_rate']:.3f}")
    print(f"  errors      {got['errors']}  (the judge call failed - a miss, never a catch)")
    for m in got["misses"]:
        print(f"  - {m['kind']}  {m['id']}  ({m['construction']}) {m['error'][:90]}")
        if m["kind"] in ("FALSE PASS", "WRONG REASON"):
            print(f"      defect passed: {m['defect'][:90]}")
        for c in m["checks"]:
            if not c["supported"]:
                print(f"      {c['why']:<44} | {c['sentence'][:70]}")


def verdict_line(got: dict[str, Any]) -> int:
    print("PASS: the judge meets the bar" if got["passes_bar"]
          else "FAIL: the judge does not meet the bar - its readings stay UNCALIBRATED")
    return 0 if got["passes_bar"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--calibrate", action="store_true")
    mode.add_argument("--record")
    mode.add_argument("--rescore", metavar="READING",
                      help="score a saved calibration reading again, no model (amendment 12)")
    ap.add_argument("--pairs", default=str(CALIBRATION),
                    help="labelled pairs for --calibrate (default: the calibration set)")
    args = ap.parse_args(argv)

    import model_client
    from model_client import ModelError
    try:
        if args.calibrate:
            import retrieval
            texts = {c.chunk_id: c.text for c in retrieval.load_corpus()}
            pairs = load_calibration(Path(args.pairs))
            from bench_check import check_reachable
            from run_record import suite_sha256, utc_now_iso
            condition = {"started_at": utc_now_iso(),
                         "pairs": _repo_relative(Path(args.pairs)),
                         "pairs_sha256": suite_sha256(args.pairs),
                         "judge_prompt_sha256": judge_prompt_sha256(),
                         "judge_max_tokens": JUDGE_MAX_TOKENS,
                         "model": judge_model(),
                         "model_digest": model_client.model_digest(judge_model()),
                         "answer_model": answer_model(),
                         "judge_is_answer_model": judge_model() == answer_model(),
                         "ollama_version": check_reachable(model_client.base_url()),
                         "fresh_load_per_pair": True}
            if judge_model() == answer_model():
                print(f"note: the judge ({judge_model()}) is the model that writes the answers - it shares "
                      "their blind spots (ADR 0026 amendment 16). Set JUDGE_MODEL to judge with another.")
            print(f"calibrating on {len(pairs)} pairs from {Path(args.pairs).name}, "
                  "a fresh load before each ...", flush=True)
            got = calibrate(pairs, texts, fresh=model_client.unload,     # amendment 5
                            progress=lambda line: print(line, flush=True))
            report(got, f"calibration: {got['n']} pairs ({Path(args.pairs).name})")
            saved = write_reading(reading_document(got, condition), REPO / "evals" / "runs")
            print(f"  reading saved: {saved.relative_to(REPO).as_posix()}  (every pair, every sentence)")
            return verdict_line(got)

        if args.rescore:
            # Amendment 12: score a SAVED reading again, no model. Refused if the label
            # file is not byte-identical to the one the reading was taken against.
            from run_record import suite_sha256
            doc = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
            cond = doc["condition"]
            pairs_path = REPO / cond["pairs"]
            if suite_sha256(pairs_path) != cond["pairs_sha256"]:
                print(f"faithfulness: {cond['pairs']} changed since this reading was taken - "
                      "its rows were judged against other labels. Refusing.", file=sys.stderr)
                return 2
            got = score(load_calibration(pairs_path), doc["rows"])
            report(got, f"RESCORE of {Path(args.rescore).name}: {got['n']} pairs ({pairs_path.name}), "
                        f"Ollama {cond['ollama_version']}, prompt {cond['judge_prompt_sha256'][:8]}")
            return verdict_line(got)

        import retrieval
        from run_record import read, suite_sha256
        path = Path(args.record)
        record = read(path)
        if record_index_hash(record) != suite_sha256(retrieval.INDEX_PATH):
            print("faithfulness: the index changed since this record was written - the cited "
                  "text is not the text that was answered from. Refusing.", file=sys.stderr)
            return 2
        texts = {c.chunk_id: c.text for c, _ in retrieval.load_index()}
        rows = judge_record(record, texts, fresh=model_client.unload)   # amendment 5
        out = path.with_name(path.stem + "-faithfulness.json")
        out.write_text(json.dumps({"record": str(path), "rows": rows}, indent=2), encoding="utf-8")
        print(f"faithfulness of {path.name} - REPORTED, NEVER GATED (ADR 0026); "
              f"judge UNCALIBRATED unless --calibrate passes")
        for r in rows:
            mark = ("UNJUDGED  " if r["faithful"] is None
                    else "faithful  " if r["faithful"] else "UNFAITHFUL")
            print(f"  {mark} {r['id']:<30} gate={r['result']}"
                  + (f"  {r['error'][:80]}" if r.get("error") else ""))
            for c in r["checks"]:
                if not c["supported"]:
                    print(f"      {c['why']:<44} | {c['sentence'][:70]}")
        k = sum(1 for r in rows if r["faithful"])
        u = sum(1 for r in rows if r["faithful"] is None)
        print(f"  {k} of {len(rows)} answered case(s) faithful, {u} unjudged  ->  {out}")
        return 0
    except (ModelError, ValueError, OSError) as exc:
        print(f"faithfulness: could not run: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
