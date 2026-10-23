"""Project 3, build step 3: the review contract and its scorer (ADRs 0028-0030).

THE CONTRACT
------------
A reviewer - PSScriptAnalyzer, the drafter reviewing itself, the critic, the
escalation model - returns ONE shape, `{verdict, findings[]}`:

    finding = {line_start, line_end, category, severity, why, fix}

with five categories, two severities and at most MAX_FINDINGS findings. The
schema is handed to the decoder AND checked again here (Lab 2: the constraint
lives on someone else's machine). Two things a schema cannot say are checked
in code: line_start <= line_end, and the verdict agrees with the findings -
`revise` if and only if a finding is blocking. A reply that breaks either is
a SchemaError, not a review.

THE CATCH RULE (ADR 0030)
-------------------------
A defect is CAUGHT when a BLOCKING finding's [line_start, line_end] overlaps
any of the defect's line ranges widened by TOLERANCE lines. Code decides; no
model reads a finding to judge it. `right_reason` (the catching finding names
the expected category) is a second number, never required. On a clean
CONTROL, any blocking finding is a FALSE ALARM. Catch rate and false-alarm
rate are reported side by side and never averaged (Lab 10).

WHY THE CAP MATTERS
-------------------
Without MAX_FINDINGS a reviewer catches every planted defect by flagging every
line. The cap plus the controls' false-alarm count is what makes "caught"
mean something.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from structured import SchemaError

REPO_ROOT = Path(__file__).resolve().parent.parent
P3 = REPO_ROOT / "projects" / "03-script-review-pipeline"
SET_DIR = P3 / "set"

CATEGORIES = ("correctness", "error-handling", "blast-radius", "security", "style")
SEVERITIES = ("blocking", "advisory")
VERDICTS = ("approve", "revise")
MAX_FINDINGS = 5
TOLERANCE = 1
# 2026-10-18, step 4: `why` had minLength only, and hermes3:8b opened one finding and wrote a
# ~24,000-character run-on `why` until the generation cap cut it (14/32 reviews). ADR 0030 says
# `why` is one sentence; these limits put that in the GRAMMAR, so the decoder must close the string.
MAX_WHY = 300
MAX_FIX = 300


def review_schema(n_lines: int) -> dict[str, Any]:
    """The decoder/validation schema for a review of a script n_lines long."""
    line = {"type": "integer", "minimum": 1, "maximum": max(1, n_lines)}
    finding = {
        "type": "object",
        "additionalProperties": False,
        "required": ["line_start", "line_end", "category", "severity", "why", "fix"],
        "properties": {
            "line_start": line,
            "line_end": line,
            "category": {"type": "string", "enum": list(CATEGORIES)},
            "severity": {"type": "string", "enum": list(SEVERITIES)},
            "why": {"type": "string", "minLength": 1, "maxLength": MAX_WHY},
            "fix": {"type": "string", "maxLength": MAX_FIX},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "findings"],
        "properties": {
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "findings": {"type": "array", "maxItems": MAX_FINDINGS, "items": finding},
        },
    }


@dataclass(frozen=True)
class Finding:
    line_start: int
    line_end: int
    category: str
    severity: str
    why: str
    fix: str

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"


@dataclass(frozen=True)
class Review:
    verdict: str
    findings: tuple[Finding, ...]

    @property
    def blocking(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.blocking)


def validate_review(data: Any, n_lines: int) -> Review:
    """Schema, then the two rules a schema cannot express. SchemaError on any breach."""
    try:
        jsonschema.validate(data, review_schema(n_lines))
    except jsonschema.ValidationError as exc:
        raise SchemaError(f"review did not match the schema: {exc.message}") from exc
    findings = tuple(Finding(**f) for f in data["findings"])
    for f in findings:
        if f.line_start > f.line_end:
            raise SchemaError(f"finding has line_start {f.line_start} > line_end {f.line_end}")
    has_blocking = any(f.blocking for f in findings)
    if (data["verdict"] == "revise") != has_blocking:
        raise SchemaError(
            f"verdict {data['verdict']!r} disagrees with the findings "
            f"({'a' if has_blocking else 'no'} blocking finding) - revise iff a finding blocks"
        )
    return Review(data["verdict"], findings)


# --- the set -----------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    """One thing a reviewer reads: a defective copy, or a clean control."""
    id: str
    script: str
    split: str
    path: Path
    defect: dict[str, Any] | None = field(default=None)

    @property
    def is_control(self) -> bool:
        return self.defect is None

    def text(self) -> str:
        return self.path.read_text(encoding="ascii").replace("\r\n", "\n")

    def n_lines(self) -> int:
        return len(self.text().rstrip("\n").split("\n"))


def load_set(set_dir: Path = SET_DIR) -> list[Item]:
    """48 items: every defect in defects.jsonl, plus each reference script as a control."""
    split = json.loads((set_dir / "split.json").read_text(encoding="utf-8"))
    side = {s: "held_out" for s in split["held_out"]} | {s: "development" for s in split["development"]}
    lines = (set_dir / "defects.jsonl").read_text(encoding="utf-8").splitlines()
    items = [Item(d["id"], d["script"], d["split"], set_dir / d["file"], d)
             for d in map(json.loads, lines[1:])]
    for script in sorted(side):
        items.append(Item(f"{script[:2]}-C", script, side[script], set_dir / "reference" / script))
    return items


# --- scoring -----------------------------------------------------------------

def _overlaps(f: Finding, ranges: list[list[int]]) -> bool:
    return any(f.line_start <= b + TOLERANCE and f.line_end >= a - TOLERANCE for a, b in ranges)


def score_item(item: Item, review: Review) -> dict[str, Any]:
    """One row. Defect: caught / right_reason. Control: false_alarm."""
    row: dict[str, Any] = {"id": item.id, "split": item.split, "control": item.is_control,
                           "findings": len(review.findings), "blocking": len(review.blocking)}
    if item.is_control:
        row["false_alarm"] = bool(review.blocking)
        return row
    hits = [f for f in review.blocking if _overlaps(f, item.defect["lines"])]
    row["family"] = item.defect["family"]
    row["caught"] = bool(hits)
    row["right_reason"] = any(f.category == item.defect["expected_category"] for f in hits)
    return row


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Catch rate per family and overall; false alarms on controls. Never averaged together."""
    defects = [r for r in rows if not r["control"]]
    controls = [r for r in rows if r["control"]]
    out: dict[str, Any] = {
        "caught": sum(r["caught"] for r in defects), "defects": len(defects),
        "right_reason": sum(r["right_reason"] for r in defects),
        "false_alarms": sum(r["false_alarm"] for r in controls), "controls": len(controls),
        "by_family": {},
    }
    for fam in ("lint-visible", "logic", "blast-radius"):
        rs = [r for r in defects if r["family"] == fam]
        out["by_family"][fam] = {"caught": sum(r["caught"] for r in rs), "of": len(rs)}
    return out


# --- the zero-model arm: PSScriptAnalyzer mapped into the same contract ------

PSSA_CATEGORY = {
    "PSAvoidUsingEmptyCatchBlock": "error-handling",
    "PSUseDeclaredVarsMoreThanAssignments": "correctness",
    "PSAvoidUsingInvokeExpression": "security",
    "PSAvoidUsingPlainTextForPassword": "security",
    "PSAvoidUsingUsernameAndPasswordParams": "security",
    "PSAvoidUsingConvertToSecureStringWithPlainText": "security",
    "PSAvoidUsingWMICmdlet": "correctness",
    "PSPossibleIncorrectComparisonWithNull": "correctness",
}


def pssa_review(hits: list[dict[str, Any]]) -> Review:
    """Every rule hit becomes a BLOCKING finding on its line (ADR 0030); same cap as a model."""
    ordered = sorted(hits, key=lambda h: h["Line"])[:MAX_FINDINGS]
    findings = tuple(Finding(h["Line"], h["Line"], PSSA_CATEGORY.get(h["RuleName"], "style"),
                             "blocking", h["RuleName"], "") for h in ordered)
    return Review("revise" if findings else "approve", findings)


class ReadingError(Exception):
    """A saved reading that cannot prove what it read."""


def read_pssa(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """A saved reading: {analyzer, folder, files[], findings[]}.

    `files` is REQUIRED. A bare list of findings cannot prove which files were scanned:
    on 2026-10-18 a scan run from the wrong folder produced `[]`, indistinguishable from
    a clean scan of the right one, and the test passed on it. A reading that does not
    list its files is refused.
    """
    raw = path.read_bytes().decode("utf-8-sig").strip()
    data = json.loads(raw) if raw else None
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise ReadingError(f"{path.name}: not a reading with a files list - cannot prove what was scanned")
    findings = data.get("findings") or []
    findings = findings if isinstance(findings, list) else [findings]
    files = [str(f) for f in data["files"]]
    stray = {h["ScriptName"] for h in findings} - set(files)
    if stray:
        raise ReadingError(f"{path.name}: findings for files the reading does not list: {sorted(stray)}")
    return files, findings


def pssa_arm(items: list[Item], *readings: Path) -> list[dict[str, Any]]:
    """Score every item from the readings. An item no reading LISTS is an error, not a clean pass."""
    scanned: set[str] = set()
    by_file: dict[str, list[dict[str, Any]]] = {}
    for p in readings:
        files, findings = read_pssa(p)
        scanned.update(files)
        for h in findings:
            by_file.setdefault(h["ScriptName"], []).append(h)
    unread = [it.id for it in items if it.path.name not in scanned]
    if unread:
        raise ReadingError(f"{len(unread)} item(s) were never scanned: {unread[:6]}")
    return [score_item(it, pssa_review(by_file.get(it.path.name, []))) for it in items]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Score the PSScriptAnalyzer arm from saved readings.")
    ap.add_argument("--split", choices=("development", "held_out"), default="development")
    ap.add_argument("readings", nargs="+", type=Path)
    a = ap.parse_args()
    chosen = [it for it in load_set() if it.split == a.split]
    s = summarize(pssa_arm(chosen, *a.readings))
    print(json.dumps({"arm": "pssa", "split": a.split, **s}, indent=2))
