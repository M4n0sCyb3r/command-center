"""The remediation audit log (Project 5, build step 3; ADR 0038).

The trace (Lab 12) and the audit log answer different questions, so they obey
OPPOSITE rules:

  trace      "what did the run cost and where did time go?"   FAIL-OPEN.
             A failed trace write is counted and dropped - instrumentation must
             never break the agent.
  audit log  "who approved what, and what happened?"           FAIL-CLOSED.
             An action with no record is the one thing it cannot tolerate, so a
             failed audit write STOPS the agent before its next action.

and one rule the trace does not need:

  WRITE-AHEAD  the `intent` line ("about to apply X, on the strength of answer Y")
               is flushed and fsynced BEFORE the change. A crash mid-change still
               leaves a record that the change began.

Every line names WHO decided: "code", "model" or "human". It records the exact
screen text and its sha256, so a reader can prove which screen an answer was
given to. It never records a credential: a line that looks like one is refused,
which - fail-closed - stops the agent rather than writing the secret.

check_complete() is what the harness scorer (step 5) uses to grade a log.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO_ROOT / "evals" / "runs"

WHO = ("code", "model", "human")

# Field names that must never appear, and value shapes that look like a secret.
# `token` only as a whole name or a suffix (`token`, `auth_token`), so a COUNT like
# `prompt_tokens` passes - P05 step 4: the substring rule refused the model's token counts, and a
# fail-closed log that refuses harmless lines stops every run.
SECRET_KEY = re.compile(r"(?i)(passw|secret|credential|api_?key|bearer|(^|_)token$)")
SECRET_VALUE = re.compile(r"(?i)(password\s*[:=]|-----BEGIN [A-Z ]*PRIVATE KEY)")

# The events, and which of them every completed remediation must contain, in order.
EVENTS = ("drift", "proposal", "preview", "screen", "answer", "recheck", "intent", "applied",
          "verify", "rollback", "rollback_verify", "outcome", "audit_incomplete",
          "pause")     # build step 8: the bench-only sabotage pause (ADR 0035) - never required
REQUIRED = {
    "refused":         ("drift", "proposal", "outcome"),
    "declined":        ("drift", "proposal", "preview", "screen", "answer", "outcome"),
    "verified":        ("drift", "proposal", "preview", "screen", "answer", "recheck", "intent",
                        "applied", "verify", "outcome"),
    "rolled-back":     ("drift", "proposal", "preview", "screen", "answer", "recheck", "intent",
                        "applied", "verify", "rollback", "rollback_verify", "outcome"),
    "rollback-failed": ("drift", "proposal", "preview", "screen", "answer", "recheck", "intent",
                        "applied", "verify", "rollback", "rollback_verify", "outcome"),
}


class AuditError(RuntimeError):
    """The record could not be written. Before an apply this stops the agent (exit 2)."""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _secret_in(obj: Any, path: str = "") -> str | None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if SECRET_KEY.search(str(k)):
                return f"{path}{k} (field name)"
            hit = _secret_in(v, f"{path}{k}.")
            if hit:
                return hit
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            hit = _secret_in(v, f"{path}{i}.")
            if hit:
                return hit
    elif isinstance(obj, str) and SECRET_VALUE.search(obj):
        return f"{path.rstrip('.')} (value)"
    return None


class AuditLog:
    """Append-only JSONL, one line per decision, each fsynced before returning."""

    def __init__(self, run_id: str, path: Path | None = None,
                 clock: Callable[[], float] = time.time,
                 opener: Callable[..., Any] = open) -> None:
        self.run_id = run_id
        self.path = Path(path) if path else RUNS_DIR / f"{run_id}.audit.jsonl"
        self._clock = clock
        self._open = opener          # injectable, so CI can make the disk fail on demand
        self.seq = 0

    def write(self, event: str, who: str, **fields: Any) -> dict[str, Any]:
        """Write one line durably, or raise AuditError. Never half-succeeds silently."""
        if event not in EVENTS:
            raise AuditError(f"unknown audit event {event!r}")
        if who not in WHO:
            raise AuditError(f"{event}: who must be one of {WHO}, got {who!r}")
        line = {"run_id": self.run_id, "seq": self.seq + 1, "ts": self._clock(),
                "event": event, "who": who, **fields}
        hit = _secret_in(line)
        if hit:
            raise AuditError(f"{event}: refused to write what looks like a credential at {hit}")
        try:
            text = json.dumps(line, sort_keys=True, default=str)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._open(self.path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception as exc:
            raise AuditError(f"{event}: audit write failed: {type(exc).__name__}: {exc}") from exc
        self.seq += 1
        return line


def read_log(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def check_complete(lines: list[dict[str, Any]]) -> list[str]:
    """ADR 0039's audit half of the gate. Returns the problems; empty means complete.

    Complete = the outcome line exists and names a known outcome; every event that outcome
    requires is present and in order; `intent` precedes `applied`; every line names who
    decided; the recorded screen sha256 equals the hash of the recorded screen text; seq runs
    1..n with no gap; no line holds anything that looks like a credential; and the log does
    not admit to being incomplete.
    """
    problems: list[str] = []
    if not lines:
        return ["empty log"]
    if [ln.get("seq") for ln in lines] != list(range(1, len(lines) + 1)):
        problems.append("seq is not 1..n (a line is missing or reordered)")
    for ln in lines:
        if ln.get("who") not in WHO:
            problems.append(f"seq {ln.get('seq')}: who={ln.get('who')!r}")
        hit = _secret_in(ln)
        if hit:
            problems.append(f"seq {ln.get('seq')}: credential-shaped content at {hit}")
    events = [ln.get("event") for ln in lines]
    if "audit_incomplete" in events:
        problems.append("the agent recorded that its own audit was incomplete")
    outcome = next((ln for ln in lines if ln.get("event") == "outcome"), None)
    if outcome is None:
        return problems + ["no outcome line"]
    want = REQUIRED.get(outcome.get("outcome"))
    if want is None:
        return problems + [f"unknown outcome {outcome.get('outcome')!r}"]
    pos = -1
    for ev in want:
        if ev not in events[pos + 1:]:
            problems.append(f"missing or out of order: {ev}")
        else:
            pos = events.index(ev, pos + 1)
    if "applied" in events and ("intent" not in events
                                or events.index("intent") > events.index("applied")):
        problems.append("applied without a prior intent (not write-ahead)")
    for ln in lines:
        if ln.get("event") == "screen" and ln.get("sha256") != sha256(ln.get("text", "")):
            problems.append("screen sha256 does not match the recorded screen text")
    ans = next((ln for ln in lines if ln.get("event") == "answer"), None)
    scr = next((ln for ln in lines if ln.get("event") == "screen"), None)
    if ans and scr and ans.get("screen_sha256") != scr.get("sha256"):
        problems.append("the answer names a different screen than the one recorded")
    return problems


class MemoryAudit(AuditLog):
    """The same rules, kept in memory - for CI and for callers that pass no log.

    Not a weaker log: it refuses the same events, the same `who`, the same credential
    shapes. `fail_after=n` makes the n+1-th write raise, so CI can prove fail-closed.
    """

    def __init__(self, run_id: str = "memory", clock: Callable[[], float] = time.time,
                 fail_after: int | None = None) -> None:
        super().__init__(run_id, path=Path("/dev/null"), clock=clock)
        self.lines: list[dict[str, Any]] = []
        self.fail_after = fail_after

    def write(self, event: str, who: str, **fields: Any) -> dict[str, Any]:
        if self.fail_after is not None and self.seq >= self.fail_after:
            raise AuditError(f"{event}: audit write failed: simulated disk failure")
        if event not in EVENTS:
            raise AuditError(f"unknown audit event {event!r}")
        if who not in WHO:
            raise AuditError(f"{event}: who must be one of {WHO}, got {who!r}")
        line = {"run_id": self.run_id, "seq": self.seq + 1, "ts": self._clock(),
                "event": event, "who": who, **fields}
        hit = _secret_in(line)
        if hit:
            raise AuditError(f"{event}: refused to write what looks like a credential at {hit}")
        line = json.loads(json.dumps(line, sort_keys=True, default=str))
        self.lines.append(line)
        self.seq += 1
        return line
