#!/usr/bin/env python3
"""The one place in this repo that knows how to talk to a model.

Nothing else opens an HTTP connection to Ollama. Everything that needs the
model imports from here.

Why this file exists: a model call is not a function call. It is an
unreliable network request to a nondeterministic process, and those are two
different failure modes wearing the same costume. This module absorbs both
so the rest of the course can pretend calling a model is boring.

    python src/model_client.py; echo "exit: $?"
"""

from __future__ import annotations

import os
import hashlib
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

import requests

OLLAMA_PORT = 11434
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_SEED = 42          # fixed on purpose - see chat()
CHAT_TIMEOUT = 120         # seconds; first call loads the model into VRAM

# P04 agent-loop stall, call A (2026-09-27). The most tokens ONE call may
# generate. Chosen from data, not taste: the largest normal turn observed across
# 52 probe runs was 1,082 tokens, so 2,048 is ~2x headroom - and it sits well
# under the server's 4,096-token context, so the context SHIFT that made the
# 2026-09-27 runaway unbounded (15,637 tokens, prompt discarded six times) can
# never be reached. qwen3 at temperature 0 can fall into endless repetition -
# the vendor says so ("DO NOT use greedy decoding") - and this does not remove
# that cause. It turns an unbounded 120 s hang into a bounded, NAMED failure.
MAX_TOKENS = 2048

# ADR 0009 (2026-09-27): THE SAMPLING THE AGENT AND THE JUDGE RUN UNDER.
# Replaces Lab 1's temperature=0.0. These are qwen3:8b's own Modelfile values
# (`ollama show qwen3:8b --parameters`), written here rather than inherited so
# that a re-pull that changed the Modelfile cannot move our sampling without a
# line of this repo changing (call v). Measured against the alternatives under
# a rule fixed before the data (evidence/adr-0009-sampling.md):
#   greedy (0.0)  tail 9/295 calls >= 1,024 tokens, 1 cap hit, pass mean 9.8
#   these values  tail 0/294, 0 cap hits, pass mean 11.0, 13/13 verdicts stable
# and confirmed to BE what the unpinned request ran with by a paired check
# (unpinned, pinned, unpinned back to back): 53/53 stable rounds identical.
SAMPLING: dict[str, float] = {"temperature": 0.6, "top_k": 20, "top_p": 0.95,
                              "repeat_penalty": 1.0}

_THINK = re.compile(r"<think>(.*?)</think>", re.DOTALL)

# ADR 0009 MEASUREMENT SEAMS (2026-09-27). Both are INERT by default - with
# SAMPLING_OVERRIDE None and no observers, the request chat() sends is
# byte-identical to before, which tests/test_sampling_seams.py checks.
#
# SAMPLING_OVERRIDE: merged over the options chat() would send. A value of
# None REMOVES that option from the request, so the model's own Modelfile
# default applies - which is how arm S1 ("vendor") is expressed: qwen3:8b
# ships temperature 0.6 / top_k 20 / top_p 0.95, and our temperature=0.0 is the
# only thing turning it into greedy decoding. Set by a probe, never by a caller
# on the measured path; a module global rather than a parameter so that no call
# site changes and the arm cannot be half-applied (agent turns one way, judge
# calls another).
SAMPLING_OVERRIDE: dict[str, Any] | None = None

# CALL_OBSERVERS: each is called once per completed HTTP exchange with a plain
# dict (see _observe). Called BEFORE the generation-cap check raises, so a
# runaway is COUNTED rather than lost inside the exception - the event ADR 0009
# measures is exactly the one that used to leave no record.
CALL_OBSERVERS: list[Any] = []


def request_options(temperature: float, seed: int, max_tokens: int) -> dict[str, Any]:
    """The `options` object for one request, with SAMPLING_OVERRIDE applied."""
    options: dict[str, Any] = {**SAMPLING, "temperature": temperature,
                               "seed": seed, "num_predict": max_tokens}
    for key, value in (SAMPLING_OVERRIDE or {}).items():
        if value is None:
            options.pop(key, None)
        else:
            options[key] = value
    return options


def effective_sampling() -> dict[str, Any]:
    """The options a DEFAULT chat() call sends - for the run record's header.

    P04 build step 6a, call B (Jose, 2026-09-28: "because its function is to
    build the request, describing what is actually sent"). Built by
    request_options(), the same function chat() builds the wire request with,
    so the record cannot describe a request other than the one that went out:
    the four pinned values AND the seed (which ADR 0009 found carries
    repeatability) AND the generation cap AND any SAMPLING_OVERRIDE in force.

    Rejected: the SAMPLING constant alone (misses seed, cap and override - a
    probe-arm run would be recorded as S1); a hash (a committed baseline is
    read in a diff, and a hash says THAT something moved, never WHICH); a
    policy label like "adr-0009" (a label is not a value - call (v) again).

    VALID ONLY WHILE THE MEASURED PATH PASSES NO PER-CALL OVERRIDES. Today
    agent.run() -> think() -> chat() and the judge's ask_json() -> chat() both
    run on defaults; a call site that starts passing temperature= or
    max_tokens= makes this a description of a request nobody sent.
    """
    return request_options(SAMPLING["temperature"], DEFAULT_SEED, MAX_TOKENS)


def _observe(options: dict[str, Any], payload: dict[str, Any], elapsed: float) -> None:
    if not CALL_OBSERVERS:
        return
    message = payload.get("message") or {}
    produced = (message.get("thinking") or "") + "\x00" + (message.get("content") or "")
    record = {
        "options": dict(options),
        "done_reason": payload.get("done_reason"),
        "completion_tokens": payload.get("eval_count"),
        "prompt_tokens": payload.get("prompt_eval_count"),
        "duration_s": round(elapsed, 3),
        # Rule 1's control: if an arm's outputs hash identically to S0's, the
        # option that defines the arm was silently ignored by the server.
        "output_sha256": hashlib.sha256(produced.encode("utf-8")).hexdigest(),
    }
    for observer in CALL_OBSERVERS:
        observer(record)


class ModelError(Exception):
    """Every failure in this module surfaces as this one type.

    Callers three labs from now should catch ONE thing, not six different
    `requests` subclasses. Absorbing the transport's vocabulary is the
    whole job of a boundary.
    """

    # P04, agent-loop stall (2026-09-27). Which agent-loop turn was waiting
    # when this was raised, or None if nobody said. DECLARED here, rather than
    # stuck on the instance by whoever catches it, so every reader can rely on
    # the attribute existing: chat() cannot know the turn - it is one call with
    # no idea it is inside a loop - so agent.run() fills it in on the way out.
    turn: int | None = None


@dataclass
class Reply:
    """One model response, with the facts you need to reason about it."""

    text: str                      # the answer, thinking removed
    thinking: str                  # the reasoning block, kept, not discarded
    model: str
    prompt_tokens: int | None      # input cost
    completion_tokens: int | None  # output cost
    duration_s: float
    raw: dict[str, Any] = field(repr=False)   # the full decoded payload


def windows_host_ip() -> str:
    """Return the Windows host address as seen from WSL.

    WSL2 runs behind NAT, so its default gateway *is* the Windows host.
    That address is reassigned on reboot, which is exactly why it is
    resolved here at runtime instead of being written into a config file.
    """
    result = subprocess.run(
        ["ip", "route", "show", "default"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    fields = result.stdout.split()
    if len(fields) < 3:
        raise RuntimeError(
            "could not read a default gateway from `ip route show default`"
        )
    return fields[2]


def base_url() -> str:
    """Where Ollama lives. Explicit override wins; otherwise discover it."""
    override = os.environ.get("OLLAMA_BASE_URL")
    if override:
        return override.rstrip("/")
    return f"http://{windows_host_ip()}:{OLLAMA_PORT}"


def _split_thinking(content: str, thinking_field: str | None) -> tuple[str, str]:
    """Separate a reasoning model's scratch work from its answer.

    qwen3 thinks out loud inside <think>...</think> before answering. Newer
    Ollama versions hand that back in its own field; older ones leave it
    inline. Handle both, and keep the reasoning rather than deleting it -
    at Lab 10 you will want to score HOW an answer was reached, not just
    what it was.
    """
    if thinking_field:
        return content.strip(), thinking_field.strip()
    blocks = _THINK.findall(content)
    answer = _THINK.sub("", content).strip()
    return answer, "\n".join(b.strip() for b in blocks)


def server_reason(response: Any) -> str:
    """The server's OWN account of a rejection, appended to the error.

    ADDED 2026-10-14 (ADR 0026 amendment 14). raise_for_status() turns a 500 into
    "500 Server Error: Internal Server Error for url" and drops the body, where
    Ollama writes {"error": "<the actual cause>"}. heldout2-shell-identify failed
    that way on two Ollama versions and nothing on our side could say why. A
    rejection that cannot name its cause is the status line that withholds the fact
    that matters - so the body travels with the error, trimmed, never invented.
    """
    if response is None:
        return ""
    try:
        body = response.json()
        reason = body.get("error") if isinstance(body, dict) else None
    except ValueError:
        reason = (response.text or "").strip()
    return f" - server says: {str(reason)[:300]}" if reason else ""


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = SAMPLING["temperature"],
    seed: int = DEFAULT_SEED,
    timeout: int = CHAT_TIMEOUT,
    schema: dict | None = None,
    max_tokens: int = MAX_TOKENS,
    num_ctx: int | None = None,
) -> Reply:
    """Send a message list to the model and return a Reply.

    `messages` is a plain list of {"role": ..., "content": ...} dicts - the
    wire format IS the data structure. Don't wrap it until something forces
    you to.

    A fixed seed and a pinned sampling policy are defaults, not options
    nobody sets. Without them the same input gives different output for
    reasons you cannot control, and Phase III evaluation becomes impossible.

    AMENDED BY ADR 0009 (2026-09-27). This used to say temperature=0.0, on the
    grounds that determinism is the precondition for measurement. Two things
    the practicum later measured overturned HOW, not WHY: greedy decoding is
    what made qwen3 run away (the vendor: "DO NOT use greedy decoding"), and
    it never delivered byte-level determinism on this server anyway - 3/26
    call sequences repeated across 5 runs at 0.0, against 18/26 at 0.6 with
    the seed fixed. The repeatability comes from the SEED. What neither
    setting gives is repeatability across server sessions: the first send of
    a prompt can differ from every later one (see the ADR).
    """
    model = model or os.environ.get("LOCAL_MODEL", DEFAULT_MODEL)

    try:
        url = f"{base_url()}/api/chat"
    except Exception as exc:                       # gateway lookup failed
        raise ModelError(f"could not determine the Ollama address: {exc}") from exc

    options = request_options(temperature, seed, max_tokens)
    # ADR 0025 part 5: the answer step needs more than the server's 4,096-token
    # default context. Per call and only when asked: None leaves the request
    # byte-identical to before, so every measured P04 condition stands.
    if num_ctx is not None:
        options["num_ctx"] = num_ctx
    started = time.monotonic()
    try:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options,
        }
        if schema is not None:
            # Not an instruction - a decoder constraint. Tokens that would
            # violate this schema are masked to zero probability, so the
            # model cannot emit prose even when asked to.
            payload["format"] = schema

        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.ConnectionError as exc:
        raise ModelError(f"nothing is listening at {url}") from exc
    except requests.exceptions.Timeout as exc:
        raise ModelError(f"the model did not answer within {timeout}s") from exc
    except requests.exceptions.HTTPError as exc:
        raise ModelError(f"the server rejected the request: {exc}{server_reason(exc.response)}") from exc
    except ValueError as exc:
        raise ModelError("the server returned something that was not JSON") from exc

    elapsed = time.monotonic() - started
    _observe(options, payload, elapsed)

    # THE CAP IS NAMED HERE, AT THE BOUNDARY. Ollama stops at num_predict and
    # reports done_reason "length" - and returns whatever it had, which for a
    # schema-constrained call is half a JSON object. Let that through and it
    # fails later as a SchemaError: not a ModelError, so the harness's stall path
    # (call B) never sees it, and a runaway arrives as a crash with no record -
    # the exact defect call B exists to prevent. So a cut-off generation is a
    # model failure, said in the model's terms, and raised as one.
    if payload.get("done_reason") == "length":
        raise ModelError(
            f"the model hit the {max_tokens}-token generation cap without finishing "
            f"(a runaway generation - see MAX_TOKENS)"
        )

    message = payload.get("message") or {}
    text, thinking = _split_thinking(
        message.get("content", ""), message.get("thinking")
    )
    if not text:
        raise ModelError("the model returned an empty answer")

    return Reply(
        text=text,
        thinking=thinking,
        model=payload.get("model", model),
        prompt_tokens=payload.get("prompt_eval_count"),
        completion_tokens=payload.get("eval_count"),
        duration_s=elapsed,
        raw=payload,
    )



def unload(model: str | None = None) -> None:
    """Unload a model from the server, so the next call loads it fresh.

    P02 build step 6c finding (2026-10-09): with the seed fixed, runs that
    started on a FRESHLY LOADED qwen3 were byte-identical (3 of 3, 20/20 answers,
    23/23 verdicts), while one run started 4 s after another - on a WARM model -
    changed 15/20 answers and one verdict. Unloading before a measured run makes
    "fresh" a property of the code, not of how long someone waited.

    keep_alive 0 with no prompt is Ollama's documented unload. Raises ModelError
    if the server will not do it: a measured run that could not establish its
    starting state has no verdict to give (exit 2, never a quiet warm run).
    """
    model = model or os.environ.get("LOCAL_MODEL", DEFAULT_MODEL)
    try:
        response = requests.post(f"{base_url()}/api/generate",
                                 json={"model": model, "keep_alive": 0}, timeout=30)
        response.raise_for_status()
    except Exception as exc:                      # noqa: BLE001 - one boundary, one error type
        raise ModelError(f"could not unload {model} before a measured run: {exc}") from exc


def model_digest(model: str | None = None) -> str:
    """The digest Ollama reports for a model file. Raises ModelError if unknown.

    ADDED FOR PROJECT 4, build step 3 part 2c. The run record's header carries
    `model_digest` as a CONDITION field (ADR 0008, call 4b(ii)): if it differs
    from the baseline's, the comparison is ANNOTATED and still made, because
    real measurements taken under different conditions are the entire point of
    the harness.

    It lives here rather than in the harness because this module owns the
    connection to Ollama - the standing move of changing the lower layer when
    an upper layer needs something it withholds, rather than reaching around
    it. Sixth time on this course, seventh counting suite.py.

    WHY IT RAISES rather than returning "unknown". ADR 0007 made the run record
    self-contained EVIDENCE. A record that cannot say what produced it is not
    comparable next month, and a placeholder written where provenance belongs
    is the exact failure this project exists to prevent - it looks like
    evidence and is not. A harness about to run cases through this model needs
    Ollama up regardless, so failing here costs nothing that was going to work.

    /api/tags and not /api/show: tags is the endpoint that has carried a
    per-model `digest` across Ollama versions, and this needs to keep working
    when the bench is upgraded.
    """
    model = model or os.environ.get("LOCAL_MODEL", DEFAULT_MODEL)
    try:
        response = requests.get(f"{base_url()}/api/tags", timeout=10)
        response.raise_for_status()
        listed = response.json().get("models", [])
    except Exception as exc:                       # noqa: BLE001
        raise ModelError(
            f"cannot read the model digest from Ollama at {base_url()}: {exc}"
        ) from exc

    # A TAGGED name matches EXACTLY. P03 build step 7 (2026-10-19): the old rule matched on the
    # family ("qwen3") and returned the FIRST listed qwen3 - harmless while one was installed;
    # once qwen3:14b was pulled, every qwen3:8b record carried 14B's digest. Provenance that
    # names the wrong artifact is worse than none. A BARE name still resolves to its
    # ":latest" tag, which is what Ollama itself runs for a bare name.
    wanted = model if ":" in model else f"{model}:latest"
    for entry in listed:
        if entry.get("name", "") == wanted:
            digest = entry.get("digest")
            if digest:
                return digest
            break

    raise ModelError(
        f"Ollama has no digest for model {model!r}. Present: "
        f"{sorted(e.get('name', '?') for e in listed)}"
    )


def server_version() -> str:
    """The Ollama server's version string. Raises ModelError if it cannot be read.

    ADDED FOR PROJECT 6, build step 2 (2026-10-21). The same qwen3:14b - identical digest - given
    the same prompt on Ollama 0.40.4 (P3, 10-19) and 0.40.7 (P6, 10-21) returned the same verdict
    and a DIFFERENT reply. A digest pins the weights, not the output: the runtime is a condition
    too, so a record that names the digest must also name the server that ran it.
    """
    try:
        response = requests.get(f"{base_url()}/api/version", timeout=10)
        response.raise_for_status()
        version = response.json().get("version")
    except Exception as exc:                       # noqa: BLE001
        raise ModelError(f"cannot read the Ollama version at {base_url()}: {exc}") from exc
    if not version:
        raise ModelError(f"Ollama at {base_url()} reported no version")
    return str(version)


def ask(prompt: str, *, system: str | None = None, **kwargs: Any) -> str:
    """One-shot convenience: a prompt in, a string out.

    Exists so that simple callers stay simple. Anything that needs history
    or tool results calls chat() directly.
    """
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return chat(messages, **kwargs).text


# --- smoke test -----------------------------------------------------------
# Not a demo. It asserts the property this module exists to provide:
# the same question, asked twice, returns the same answer.

if __name__ == "__main__":
    import sys

    QUESTION = "What is the capital of Arizona? Answer with the city name only."

    try:
        first = chat([{"role": "user", "content": QUESTION}])
        second = chat([{"role": "user", "content": QUESTION}])
    except ModelError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)

    print(f"model      : {first.model}")
    print(f"answer 1   : {first.text!r}")
    print(f"answer 2   : {second.text!r}")
    print(f"tokens in  : {first.prompt_tokens}")
    print(f"tokens out : {first.completion_tokens}")
    print(f"duration   : {first.duration_s:.1f}s")
    print(f"thinking   : {len(first.thinking)} chars kept")

    if first.text != second.text:
        print("FAIL: identical input produced different answers")
        sys.exit(1)

    print("PASS: deterministic")
    sys.exit(0)
