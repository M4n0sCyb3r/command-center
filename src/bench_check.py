#!/usr/bin/env python3
"""Lab 0 - bench check.

Proves the local model bench is usable end to end:

  A. the Ollama server is reachable from WSL
  B. the configured model actually generates

  C. the server is the version the bench was last VERIFIED on (bench-pin.json)

Exits 0 when all three pass, 1 otherwise, so this can gate later automation.

    python src/bench_check.py; echo "exit code: $?"

Why C exists (2026-10-08): Ollama on Windows updates itself - 0.33.3 -> 0.34.4
-> 0.40.1 in five weeks, each found by someone happening to read the version
line. The second update also changed the reported model digest. A server change
can move every measurement in the repo, so it must be an announced event, not a
discovery. C turns it into one: a mismatch fails here, at the start of a
session, and names the one command that settles it - tools/upgrade_check.py,
which re-measures and moves the pin only if nothing moved.

The address logic used to live here. It moved to model_client.py in Lab 1,
so there is exactly one place that knows how to find the model.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

from model_client import base_url

DEFAULT_MODEL = "qwen3:8b"
PROMPT = "Reply with exactly: bench online"
CONNECT_TIMEOUT = 5      # seconds - reachability should be instant
GENERATE_TIMEOUT = 120   # seconds - first call loads the model into VRAM
PIN_PATH = Path(__file__).resolve().parent.parent / "bench-pin.json"


def load_pin(path: Path = PIN_PATH) -> str | None:
    """The Ollama version the bench was last verified on, or None if unreadable."""
    try:
        version = json.loads(path.read_text(encoding="utf-8"))["ollama_version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return version if isinstance(version, str) and version else None


def pin_verdict(pinned: str | None, actual: str) -> str | None:
    """Check C. None when the server matches the pin, else the reason it does not."""
    if pinned is None:
        return f"bench-pin.json is missing or unreadable (server reports {actual})"
    if pinned != actual:
        return f"Ollama changed {pinned} -> {actual} since the bench was last verified"
    return None


def check_reachable(base: str) -> str:
    """Check A - is anything answering? Returns the server version."""
    response = requests.get(f"{base}/api/version", timeout=CONNECT_TIMEOUT)
    response.raise_for_status()
    return response.json().get("version", "unknown")


def check_inference(base: str, model: str) -> tuple[str, int | None]:
    """Check B - does the model actually generate? Returns (text, tokens)."""
    response = requests.post(
        f"{base}/api/generate",
        json={"model": model, "prompt": PROMPT, "stream": False},
        timeout=GENERATE_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("response", "").strip(), payload.get("eval_count")


def main() -> int:
    model = os.environ.get("LOCAL_MODEL", DEFAULT_MODEL)

    try:
        base = base_url()
    except Exception as exc:
        print(f"FAIL: could not determine the Ollama address: {exc}")
        return 1

    print(f"base url : {base}")
    print(f"model    : {model}")

    # --- Check A -------------------------------------------------------
    try:
        version = check_reachable(base)
    except requests.exceptions.ConnectionError:
        print(f"FAIL: nothing is listening at {base}")
        print("      - is Ollama running on Windows?")
        print("      - is OLLAMA_HOST set to 0.0.0.0?")
        print("      - does the firewall rule 'Ollama - WSL only' still exist?")
        return 1
    except requests.exceptions.Timeout:
        print(f"FAIL: {base} did not answer within {CONNECT_TIMEOUT}s")
        return 1
    except requests.exceptions.HTTPError as exc:
        print(f"FAIL: the server answered with an error: {exc}")
        return 1

    pinned = load_pin()
    print(f"version  : {version}  (pinned {pinned})")
    drift = pin_verdict(pinned, version)

    # --- Check B -------------------------------------------------------
    try:
        text, tokens = check_inference(base, model)
    except requests.exceptions.Timeout:
        print(f"FAIL: generation exceeded {GENERATE_TIMEOUT}s")
        print("      the model may be too large for available VRAM")
        return 1
    except requests.exceptions.HTTPError as exc:
        print(f"FAIL: generation was rejected: {exc}")
        print(f"      is '{model}' pulled? check with: ollama list")
        return 1
    except ValueError:
        print("FAIL: the server returned something that was not JSON")
        return 1

    print(f"response : {text!r}")
    print(f"tokens   : {tokens}")

    if not text:
        print("FAIL: the model returned an empty response")
        return 1

    # --- Check C - reported last, so a version change never hides A or B --
    if drift:
        print(f"FAIL: {drift}")
        print("      run:  python tools/upgrade_check.py")
        print("      it re-measures retrieval against the pinned baseline and moves")
        print("      the pin only if nothing moved; then commit bench-pin.json")
        return 1

    print("PASS: bench online")
    return 0


if __name__ == "__main__":
    sys.exit(main())
