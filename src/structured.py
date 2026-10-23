#!/usr/bin/env python3
"""Get a dict out of a model, or fail loudly. Never something in between.

Lab 1 gave us `ask()`, which returns a string. A string is fine for a human
reading a screen and useless for a program that has to branch. This module
turns a model into a source of *data*.

Two guarantees, and it is worth being precise about which is which:

  * SHAPE is guaranteed. The JSON parses, the keys are present, the values
    are inside their enums. Code downstream can branch without defending.
  * TRUTH is NOT guaranteed. A schema cannot make a model correct. It only
    makes a wrong answer arrive in a form you can measure. Measuring it is
    Phase III's job, not this file's.

Confusing those two is the most expensive mistake available here.

    python src/structured.py; echo "exit: $?"
"""

from __future__ import annotations

import json
from typing import Any

import jsonschema

from model_client import ask, chat

# How much of an offending payload to quote back in an error. Enough to
# recognise the problem, not enough to dump 4KB into a log line.
MAX_ECHO = 200


class SchemaError(Exception):
    """The model answered, but the answer was not usable data.

    Deliberately NOT `ModelError`. Those two mean different things:

        ModelError  - we could not talk to the model at all.
        SchemaError - we talked to it fine and got back garbage.

    A caller may reasonably retry the first and refuse the second, or
    re-prompt on the second and escalate on the first. Collapsing them into
    one type throws that decision away before the caller ever gets to make
    it. This is the same instinct as the QE exit-code contract: 1 (the check
    failed) and 2 (the check could not run) are not the same event.
    """


def parse_json(text: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Turn model text into a validated dict, or raise SchemaError.

    Separate from ask_json() on purpose. Keeping the guard in its own
    function means it can be tested with text you chose, instead of hoping
    the model misbehaves on cue during a test run.

    Validating locally even though the server already constrained the
    decoder is not redundant. The constraint lives on Ollama. In Phase II
    this points at a frontier API that may only *try* to comply. This
    function is what makes that swap a config change instead of a rewrite.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaError(
            f"the model did not return JSON: {text[:MAX_ECHO]!r}"
        ) from exc

    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        raise SchemaError(
            f"JSON did not match the schema ({exc.message}): {text[:MAX_ECHO]!r}"
        ) from exc

    if not isinstance(data, dict):
        raise SchemaError(
            f"expected a JSON object, got {type(data).__name__}: {text[:MAX_ECHO]!r}"
        )

    return data


def ask_json(
    prompt: str,
    schema: dict[str, Any],
    *,
    system: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Ask a question, get a validated dict back.

    The schema does double duty: it constrains the decoder on the way out
    (so the model cannot emit prose) and validates the result on the way
    back in (so we do not depend on the server having honoured it). One
    definition, both jobs - they can never drift apart.
    """
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    reply = chat(messages, schema=schema, **kwargs)
    return parse_json(reply.text, schema)


# --- smoke test -----------------------------------------------------------
# Asserts TWO properties, not one. A test that only proves the happy path
# would also pass if parse_json() were `return json.loads(text)` with no
# validation at all. The guard has to be shown firing.

if __name__ == "__main__":
    import sys

    from model_client import ModelError

    SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "fail"]},
            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
            "reason": {"type": "string"},
        },
        "required": ["verdict", "severity", "reason"],
        "additionalProperties": False,
    }

    QUESTION = (
        "A Windows service named Spooler is stopped and its startup type is "
        "Automatic. Judge it and explain your reasoning."
    )

    failures: list[str] = []

    # 1. Happy path - constrained, parsed, validated.
    try:
        data = ask_json(QUESTION, SCHEMA)
        print(f"parsed   : {data}")
        print(f"verdict  : {data['verdict']!r}")
        print(f"severity : {data['severity']!r}")
    except (ModelError, SchemaError) as exc:
        print(f"FAIL: happy path raised {type(exc).__name__}: {exc}")
        failures.append("happy path")

    # 2. Guard path - the SAME question with no schema, so the model answers
    #    in prose. Feeding that to parse_json() must raise. A guard that never
    #    fires is indistinguishable from no guard at all.
    try:
        prose = ask(QUESTION)
        parse_json(prose, SCHEMA)
    except SchemaError as exc:
        print(f"guard    : fired as designed - {exc}")
    except ModelError as exc:
        print(f"FAIL: could not reach the model: {exc}")
        failures.append("guard path")
    else:
        print("FAIL: unconstrained prose passed the schema - the guard is not guarding")
        failures.append("guard path")

    if failures:
        print(f"FAIL: {', '.join(failures)}")
        sys.exit(1)

    print("PASS: shape guaranteed, guard fires")
    sys.exit(0)
