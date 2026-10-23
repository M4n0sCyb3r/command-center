"""The remediation approval screen (Project 5, build step 3; ADR 0038 + amendment 1).

Lab 13's gate, extended for a change the AGENT proposed rather than one an
operator asked for. Fixed order, same shape every time, every line computed by
code:

  1 Host      read from the machine at render time - never a configured string
              (sops/identify-the-shell.md: read the machine, never trust the window)
  2 Drift     the baseline (what the machine is SUPPOSED to be - the stand-in for
              Lab 13's "the ask") against what the read-only verifier observed
  3 Change    the remediator's preview, against the machine now
  4 Checks    ADR 0036's three rules for this change, and the settling ceiling
  5 Undo      the before-state, and that a failed check undoes it WITHOUT ASKING
  7 Answer    Lab 13's rules: all three classes are write-level, so type `yes`

There is no item 6. The model's rationale was item 6 until amendment 1: Lab 13
ruled "show the ask, never the argument", and the rationale adds persuasion but
no information. It goes to the audit log instead.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from approval import FAST_S, _clip, drain_typeahead

RULE = "=" * 72


@dataclass(frozen=True)
class Answer:
    """What the person typed, and enough about HOW to audit it."""

    approved: bool
    answer: str | None
    seconds: float
    hasty: bool
    drained: bool
    reason: str


def fingerprint(host: str, item: str, before: dict, call: Any, preview: list) -> str:
    """The state this screen describes. Same call against a moved machine = a different screen."""
    payload = json.dumps({"host": host, "item": item, "before": before,
                          "call": [call.function, call.item, call.values],
                          "preview": [[c.field, c.before, c.after] for c in preview]},
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _when(ts: float) -> str:
    if ts > 1e9:
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"t={ts}"


def render(*, host: str, drift: Any, call: Any, preview: list, before: dict, n_watched: int,
           settle: tuple[float, float], fp: str) -> str:
    """The screen. Every argument comes from code; nothing here is the model's prose."""
    interval, ceiling = settle
    lines = [
        RULE,
        f"APPROVAL REQUIRED - remediation{'risk: WRITE':>41}",
        "-" * 72,
        f"Host     : {host}   (read from the machine just now)",
        f"Drift    : {drift.item}  ({drift.cls} {drift.name})",
        f"    {'field':<12} {'baseline (should be)':<24} observed",
    ]
    for f in sorted(drift.baseline):
        mark = "" if drift.observed.get(f) == drift.baseline[f] else "   <- drifted"
        lines.append(f"    {f:<12} {str(drift.baseline[f]):<24} {drift.observed.get(f)}{mark}")
    lines += [
        f"    read at {_when(drift.read_at)} by the read-only verifier",
        f"Action   : {call.function} {json.dumps(call.values, sort_keys=True)}",
        "Change   :",
        f"    {'field':<12} {'before':<22}    {'after':<22} effect",
    ]
    for c in preview:
        arrow = "==" if c.before == c.after else "->"
        lines.append(f"    {c.field:<12} {str(c.before):<22} {arrow} {str(c.after):<22} {c.effect}")
    others = n_watched - 1
    lines += [
        "Checks   : afterwards the read-only verifier re-reads every watched value:",
        f"    1. {drift.item} equals its baseline",
        "    2. only the fields above change, and only to the values above",
        f"    3. the other {others} watched value{'s' if others != 1 else ''} do not move",
        f"    re-read every {interval:g} s; no pass within {ceiling:g} s counts as a FAIL",
        "Undo     : if ANY check fails, this change is undone automatically, WITHOUT ASKING:",
        "    " + ", ".join(f"{f} -> {before[f]}" for f in sorted(call.values)),
        "    (back to the observed state above - the drift, not the baseline)",
        f"Screen   : {fp}  (your answer applies to this exact state)",
        RULE,
    ]
    return "\n".join(lines)


def console_asker(read: Callable[[str], str] = input, write: Callable[[str], Any] = print,
                  clock: Callable[[], float] = time.monotonic,
                  drain: Callable[[], bool] = drain_typeahead,
                  hasty_below: float = FAST_S) -> Callable[[str], Answer]:
    """Ask a person at a terminal. Lab 13's order is the control: draw the screen, THEN clear
    typeahead, THEN start the clock and read. Only `yes` (any case, trimmed) approves."""

    def ask(screen_text: str) -> Answer:
        write(screen_text)
        drained = drain()
        started = clock()
        try:
            raw: str | None = read("Type 'yes' to approve. Anything else refuses: ")
        except (EOFError, KeyboardInterrupt):
            raw = None
        took = round(clock() - started, 3)
        given = None if raw is None else raw.strip()
        approved = given is not None and given.lower() == "yes"
        reason = ("no answer from the operator (end of input)" if raw is None else
                  "approved by the operator" if approved else "the operator declined this change")
        if not drained:
            reason += " [typeahead not cleared]"
        return Answer(approved, _clip(given), took, approved and took < hasty_below, drained, reason)

    return ask
