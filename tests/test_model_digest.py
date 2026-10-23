"""model_client.model_digest names the artifact asked for - never a sibling tag (P03 step 7).

  case 1  qwen3:14b listed FIRST: qwen3:8b still gets its own digest (the bug: it got 14B's)
  case 2  a bare name resolves to its :latest tag, and only to that
  case 3  a model not present raises ModelError - never a neighbour's digest

Run:  python tests/test_model_digest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import model_client                                   # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class Resp:
    def __init__(self, models): self.models = models
    def raise_for_status(self): pass
    def json(self): return {"models": self.models}


LISTED = [{"name": "qwen3:14b", "digest": "14b" * 4}, {"name": "hermes3:8b", "digest": "her" * 4},
          {"name": "qwen3:8b", "digest": "8b8" * 4}, {"name": "nomic-embed-text:latest", "digest": "nom" * 4}]
saved = (model_client.requests.get, model_client.base_url)
model_client.requests.get = lambda url, timeout=10: Resp(LISTED)
model_client.base_url = lambda: "http://scripted"
try:
    print("case 1  a sibling tag listed first")
    check("qwen3:8b -> its own digest", model_client.model_digest("qwen3:8b") == "8b8" * 4)
    check("qwen3:14b -> its own digest", model_client.model_digest("qwen3:14b") == "14b" * 4)
    print("case 2  bare names")
    check("nomic-embed-text -> :latest", model_client.model_digest("nomic-embed-text") == "nom" * 4)
    print("case 3  absent")
    for name in ("qwen3:4b", "qwen3"):
        try:
            model_client.model_digest(name); check(f"{name} absent -> ModelError", False)
        except model_client.ModelError:
            check(f"{name} absent -> ModelError", True)
finally:
    model_client.requests.get, model_client.base_url = saved

if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
