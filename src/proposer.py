"""The model's one job: propose the fix (Project 5, build step 4; ADR 0037).

Code detects the drift and tells the model exactly what drifted. The model
answers with ONE call from the remediator's menu and a short rationale. That is
all it does. It never reads the machine, never judges whether drift exists, and
never judges whether its fix worked - those are code (ADRs 0036, 0037).

Every proposal passes GATE 0 before anyone is asked:

  0a  the reply is JSON matching the schema (constrained decoding on Ollama, and
      validated here anyway - Lab 2: the constraint lives on the server, the check
      lives in our code)
  0b  the function is on the menu            - unknown: cannot rate it, refuse
  0c  its blast radius is within the ceiling - this policy's ceiling is `write`
  0d  one mutating call per approval         - structural: remediate() applies once

Lab 11's Guard is bound to tools.REGISTRY (the lab's fake services), so P05
applies the SAME gate-0 order to the remediator's menu, using guard.rank() for
the ceiling - one vocabulary, two menus. Anything refused here is outcome
`refused`, recorded, and nobody is woken (Lab 13: refuse without a person what a
rule can refuse). Refusal text is a template naming the rule - never the model's
prose (ADR 0037).

The rationale goes to the audit log only, never the screen (ADR 0038 am. 1). The prompt
does not mention the approval, the screen or the checks: a model told that a person will review
its proposal writes rationales aimed at the reviewer (step 4: the first prompt said so; removed).

    python src/proposer.py      # bench: the live model against the fake, all three drifts
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import model_client
from audit import AuditLog, MemoryAudit
from guard import rank
from remediate import Drift, Result, detect, remediate
from structured import SchemaError, parse_json
from target import ALLOWED, MENU, Call, Target, Watched

MODEL = "qwen3:8b"                 # ADR 0037; ADR 0009 sampling is chat()'s default
NUM_CTX = 8192
MAX_TOKENS = 6144                  # P03's review budget (ADR 0031 am. 1) - a runaway is an error
TIMEOUT = 300
RATIONALE_MAX = 400
CEILING = "write"                  # the highest blast radius this agent may propose
MENU_RISK = {fn: "write" for fn in MENU}       # every remediation function is reversible
RESIDENT = ("qwen3:8b", "hermes3:8b", "qwen3:14b")   # P03: a resident model changes answers


class ModelUnavailable(RuntimeError):
    """The model could not be reached or loaded. A tooling error (exit 2), not a refusal."""


def schema(baseline: list[Watched]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["function", "item", "values", "rationale"],
        "properties": {
            "function": {"enum": sorted(MENU)},
            "item": {"enum": [w.id for w in baseline]},
            "values": {"type": "object", "minProperties": 1},
            "rationale": {"type": "string", "maxLength": RATIONALE_MAX},
        },
    }


def menu_text(baseline: list[Watched]) -> str:
    lines = []
    for fn, cls in sorted(MENU.items()):
        fields = ", ".join(f"{f} in {list(v)}" for f, v in ALLOWED[cls].items())
        items = ", ".join(w.id for w in baseline if w.cls == cls)
        lines.append(f"- {fn}: changes a {cls} item ({items}); values: {fields}")
    return "\n".join(lines)


SYSTEM = """You are the remediation planner for a Windows Server. Code has already read the
machine and found one value that differs from its baseline. Propose exactly ONE call from the
menu below that restores that item to its baseline. You cannot run anything.

Menu (the only functions that exist):
{menu}

Answer with JSON only:
{{"function": <menu function>, "item": <item id>, "values": {{<field>: <value>, ...}},
  "rationale": <one or two sentences, at most {rmax} characters>}}"""


def build_messages(drift: Drift, baseline: list[Watched]) -> list[dict[str, str]]:
    system = SYSTEM.format(menu=menu_text(baseline), rmax=RATIONALE_MAX)
    user = (f"Drifted item: {drift.item} ({drift.cls} {drift.name})\n"
            f"Baseline:     {json.dumps(drift.baseline, sort_keys=True)}\n"
            f"Observed now: {json.dumps(drift.observed, sort_keys=True)}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


Generate = Callable[[list[dict[str, str]], dict[str, Any]], "model_client.Reply"]


def default_generate(messages: list[dict[str, str]], sch: dict[str, Any]):
    for m in RESIDENT:                                 # fresh and alone (ADR 0025, P03 step 5)
        try:
            model_client.unload(m)
        except model_client.ModelError:
            if m == MODEL:
                raise
    return model_client.chat(messages, model=MODEL, schema=sch, num_ctx=NUM_CTX,
                             max_tokens=MAX_TOKENS, timeout=TIMEOUT)


@dataclass
class Proposal:
    call: Call | None
    rationale: str | None = None
    refused: str | None = None          # the gate-0 rule that refused it, as a template
    text: str = ""                      # the model's raw reply, for the audit log
    meta: dict[str, Any] = field(default_factory=dict)


def propose(drift: Drift, baseline: list[Watched], generate: Generate = default_generate
            ) -> Proposal:
    """Ask the model; apply gate 0. Raises ModelUnavailable if no answer could be had."""
    sch = schema(baseline)
    t0 = time.monotonic()
    try:
        reply = generate(build_messages(drift, baseline), sch)
    except model_client.ModelError as exc:
        raise ModelUnavailable(str(exc)) from exc
    meta = {"model": reply.model, "prompt_tokens": reply.prompt_tokens,
            "completion_tokens": reply.completion_tokens,
            "seconds": round(time.monotonic() - t0, 3)}
    try:                                                     # gate 0a
        data = parse_json(reply.text, sch)
    except SchemaError:
        return Proposal(None, refused="gate 0a: the reply was not a valid proposal "
                        "(not JSON, or not the proposal schema)", text=reply.text, meta=meta)
    call = Call(data["function"], data["item"], dict(data["values"]))
    p = Proposal(call, rationale=data["rationale"], text=reply.text, meta=meta)
    if call.function not in MENU:                            # gate 0b
        p.refused = f"gate 0b: {call.function!r} is not on the menu"
    elif rank(MENU_RISK[call.function]) > rank(CEILING):     # gate 0c
        p.refused = f"gate 0c: {MENU_RISK[call.function]} exceeds the ceiling ({CEILING})"
    return p


def expected_call(drift: Drift) -> Call:
    """The right fix: the class's function, the drifted item, the baseline values."""
    fn = next(f for f, c in MENU.items() if c == drift.cls)
    return Call(fn, drift.item, dict(drift.baseline))


def right_fix(call: Call | None, drift: Drift) -> bool:
    """REPORTED, never gated (ADR 0039). The drifted fields restored is enough; restating an
    unchanged field at its baseline value is not wrong."""
    if call is None:
        return False
    exp = expected_call(drift)
    return (call.function == exp.function and call.item == exp.item
            and all(call.values.get(f) == drift.baseline[f] for f in drift.fields)
            and all(drift.baseline.get(f) == v for f, v in call.values.items()))


def run_one(target: Target, baseline: list[Watched], drift: Drift, approve: Callable[..., Any],
            *, audit: AuditLog | None = None, generate: Generate = default_generate,
            **kw: Any) -> tuple[Result, Proposal]:
    """Propose, gate, then hand an admitted call to remediate(). A refused proposal still
    leaves a complete log: drift, proposal (who: model), outcome."""
    log = audit if audit is not None else MemoryAudit()
    p = propose(drift, baseline, generate)
    if p.refused is not None:
        log.write("drift", "code", item=drift.item, baseline=drift.baseline,
                  observed=drift.observed, read_at=drift.read_at)
        log.write("proposal", "model", reply=p.text[:2000], rationale=p.rationale,
                  function=p.call.function if p.call else None,
                  item=p.call.item if p.call else None,
                  values=p.call.values if p.call else None, **p.meta)
        res = Result("refused", drift, p.call or Call("", "", {}), reasons=[p.refused])
        log.write("outcome", "code", outcome="refused", reasons=res.reasons)
        return res, p
    res = remediate(target, baseline, drift, p.call, approve, audit=log,
                    rationale=p.rationale, proposer="model", **kw)
    return res, p


def main() -> int:
    """Bench: the live model proposes a fix for each drift class on the fake target, with a
    scripted yes. Measures right-fix-chosen and the outcome; prints tokens and seconds."""
    import target as T
    base = T.load_baseline()
    breaks = {"service:Spooler": dict(start_type="automatic", status="running"),
              "audit:Logon": dict(setting="No Auditing"),
              "registry:rdp-nla": dict(value=0)}
    rows, right = [], 0
    for item, drifted in breaks.items():
        tgt, host = T.fake_target(base)
        host.set(item, **drifted)
        (d,) = detect(tgt, base)
        try:
            res, p = run_one(tgt, base, d, lambda **k: True)
        except ModelUnavailable as exc:
            print(f"model unavailable: {exc}")
            return 2
        ok = right_fix(p.call, d)
        right += ok
        rows.append((item, res.outcome, ok, p.meta.get("completion_tokens"), p.meta.get("seconds")))
        call = f"{p.call.function} {json.dumps(p.call.values, sort_keys=True)}" if p.call else "-"
        print(f"{item:<18} {res.outcome:<16} right_fix={ok!s:<5} "
              f"out={p.meta.get('completion_tokens')} {p.meta.get('seconds')}s  {call}")
        if p.refused:
            print(f"    refused: {p.refused}")
    print(f"\nright fix chosen: {right}/{len(rows)}   verified: "
          f"{sum(r[1] == 'verified' for r in rows)}/{len(rows)}   model: {MODEL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
