"""P04 build step 6a: the run record's header carries SAMPLING as a CONDITION.

THE CALL (Jose, 2026-09-28) = B: record the EFFECTIVE request options, built by
request_options() - "because its function is to build the request, describing
what is actually sent". Rejected: A the SAMPLING constant (misses seed, cap and
any override), C a hash (a diff cannot say which setting moved), D a policy
label (a label is not a value).

WHAT THIS PROVES
  case 1  THE POINT: effective_sampling() == the `options` the wire received,
          under the default AND under an override (so a probe-arm run cannot
          be recorded as S1) - and clearing the override restores it
  case 2  the factories: agent -> effective_sampling(); retrieval -> null;
          scripted runner -> null
  case 3  Header() refuses a missing or malformed sampling
  case 4  read() refuses a record with NO sampling key (a pre-v5 writer) and a
          v4 record; POSITIVE CONTROL: null and a dict both round-trip exactly
  case 5  the report prints it, sorted, and says what null means

NO MODEL, NO NETWORK: requests.post and model_digest are replaced by scripts.
Run:  python tests/test_sampling_condition.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import model_client                                   # noqa: E402
import report                                         # noqa: E402
import run_record as rr                               # noqa: E402
import scorers                                        # noqa: E402
from suite import parse_suite                         # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class FakeResponse:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


sent: list[dict] = []


def post(url, json=None, timeout=None):
    sent.append(json)
    return FakeResponse({"model": "scripted", "done_reason": "stop", "eval_count": 5,
                         "prompt_eval_count": 3, "message": {"content": "ok"}})


model_client.requests.post = post
model_client.base_url = lambda: "http://scripted:11434"
model_client.model_digest = lambda name=None: "sha256:scripted-digest"


def wire() -> dict:
    model_client.chat([{"role": "user", "content": "hi"}])
    return sent[-1]["options"]


# ---------------------------------------------------------------------------
print("case 1  the header's sampling IS what the wire received")
recorded = model_client.effective_sampling()
check("default: recorded == sent", recorded == wire(), f"{recorded}")
# Written out by hand, so a change to the pin shows up here as well as in ADR 0009.
check("default carries the seed and the cap, not just the four values",
      recorded == {"temperature": 0.6, "top_k": 20, "top_p": 0.95,
                   "repeat_penalty": 1.0, "seed": 42, "num_predict": 2048}, str(recorded))

model_client.SAMPLING_OVERRIDE = {"temperature": 0.0, "top_k": None}
try:
    under = model_client.effective_sampling()
    check("override: recorded == sent", under == wire(), str(under))
    check("override: temperature moved to 0.0 in the record", under.get("temperature") == 0.0)
    check("override: a None override REMOVES the key from the record too",
          "top_k" not in under, str(under))
finally:
    model_client.SAMPLING_OVERRIDE = None
check("cleared: back to the default exactly", model_client.effective_sampling() == recorded)

# ---------------------------------------------------------------------------
# HERMETIC INDEX (build step 8, found by the FIRST CI run, 2026-09-28).
# data/index.json is gitignored, so a fresh clone - which is what a CI runner
# is - does not have one. The retrieval factory reads that file for its
# CONDITIONS even when an index is injected, so this check passed only in homes
# that happened to hold the file. A test names its own index, as it names its
# own baseline (tests/test_isolation.py). The factory reads
# `retrieval.INDEX_PATH` at call time, so pointing it here is enough.
def _own_index() -> Path:
    import json as _json
    import tempfile as _tempfile
    import retrieval as _retrieval
    p = Path(_tempfile.mkdtemp()) / "index.json"
    p.write_text(_json.dumps({"model": "fixture-embedder", "chunks": []}), encoding="utf-8")
    _retrieval.INDEX_PATH = p
    return p


print("\ncase 2  the factories say what their scorer sends")
agent_suite = parse_suite(REPO_ROOT / "evals" / "suites" / "agent.jsonl")
_, cond = scorers.make_agent_scorer(suite=agent_suite)
check("agent (real path): conditions.sampling == effective_sampling()",
      cond.sampling == model_client.effective_sampling(), str(cond.sampling))
_, cond = scorers.make_agent_scorer(runner=lambda case: None)
check("agent (scripted runner): null - a fixture calls no model", cond.sampling is None)
_own_index()
_, cond = scorers.make_retrieval_scorer(index=[])   # hermetic: see _own_index()
check("retrieval: null - it embeds, it generates nothing", cond.sampling is None)

# ---------------------------------------------------------------------------
print("\ncase 3  Header() refuses a missing or malformed sampling")
BASE = dict(suite_path="evals/suites/agent.jsonl", suite_sha256="0" * 64, suite_header_sha256="a" * 64,
            tier_selection="fixed", scorer="agent", model_name="qwen3:8b",
            model_digest="sha256:x", host_target="fixture",
            started_at="2026-09-28T00:00:00Z", rebaseline=None)
try:
    rr.Header(**BASE)
    check("missing sampling -> TypeError", False, "constructed without it")
except TypeError as exc:
    check("missing sampling -> TypeError", "sampling" in str(exc), str(exc))
for label, bad in (("{} (empty)", {}), ("a list", [0.6]), ("a string value", {"temperature": "0.6"}),
                   ("a bool value", {"temperature": True}), ("a non-string key", {1: 0.6})):
    try:
        rr.Header(**BASE, sampling=bad)
        check(f"{label} -> ValueError", False, "accepted")
    except ValueError:
        check(f"{label} -> ValueError", True)

# ---------------------------------------------------------------------------
print("\ncase 4  read(): absent and v4 refused; null and a dict round-trip")
with tempfile.TemporaryDirectory() as tmp:
    for value in (None, recorded):
        path = rr.write(Path(tmp) / str(value is None), rr.Header(**BASE, sampling=value), [])
        got = rr.read(path)["header"]["sampling"]
        check(f"POSITIVE CONTROL: sampling={'null' if value is None else 'dict'} reads back exactly",
              got == value, str(got))
    doc = json.loads(path.read_text(encoding="utf-8"))
    del doc["header"]["sampling"]
    absent = Path(tmp) / "absent.json"
    absent.write_text(json.dumps(doc), encoding="utf-8")
    old = json.loads(path.read_text(encoding="utf-8"))
    old["record_version"] = 4
    v4 = Path(tmp) / "v4.json"
    v4.write_text(json.dumps(old), encoding="utf-8")
    for label, p, needle in (("no sampling key", absent, "sampling"), ("record_version 4", v4, "record_version")):
        try:
            rr.read(p)
            check(f"{label} -> refused", False, "read")
        except ValueError as exc:
            check(f"{label} -> refused, naming why", needle in str(exc), str(exc))

# ---------------------------------------------------------------------------
print("\ncase 5  the report prints it")
line = report.fmt_sampling(recorded)
check("sorted keys, every value", line == "num_predict=2048 repeat_penalty=1.0 seed=42 "
      "temperature=0.6 top_k=20 top_p=0.95", line)
check("null says what it means", report.fmt_sampling(None).startswith("none"), report.fmt_sampling(None))

print()
if failures:
    print(f"{len(failures)} check(s) FAILED")
    sys.exit(1)
print("all checks passed")
