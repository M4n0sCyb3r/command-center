"""P06 build step 2 check: the `review` job's adapter, src/review_script.py. ADRs 0041, 0043.

  case 1  the contract is Project 3's, not a copy: the prompt sent is review_runner's
          messages_for(), the schema is review.review_schema(n) for this script's line count,
          the model is qwen3:14b, and build_messages() for a P3 item is unchanged by the refactor
  case 2  exit codes: approve -> 0, a blocking finding -> 1, a reply that breaks the contract
          -> 2 (NOT an approval), a model error -> 2
  case 3  no digest, no review: a digest that cannot be read -> 2 and the model is never called
  case 4  the file: missing, empty, not UTF-8, too big -> 2 before any model call; a BOM and
          CRLF line endings are normalized, so line numbers mean what PowerShell shows
  case 5  the record: script sha256, model, digest, Ollama version, verdict, exit code; no
          --model flag exists

No model: `generate` and `digest` are injected. Runs in CI.

Run:  python tests/test_review_script.py
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import model_client  # noqa: E402
import review as R  # noqa: E402
import review_runner as RR  # noqa: E402
import review_script as RS  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


APPROVE = json.dumps({"verdict": "approve", "findings": []})
REVISE = json.dumps({"verdict": "revise", "findings": [
    {"line_start": 2, "line_end": 2, "category": "blast-radius", "severity": "blocking",
     "why": "Stops every service on the host.", "fix": "Name the one service."}]})
LIES = json.dumps({"verdict": "revise", "findings": []})       # revise with nothing blocking


class Spy:
    def __init__(self, text: str | None = None, error: Exception | None = None):
        self.text, self.error, self.calls = text, error, []

    def __call__(self, messages, schema, model):
        self.calls.append((messages, schema, model))
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text, prompt_tokens=10, completion_tokens=5)


def run(script: Path, gen: Spy, runs: Path, digest=lambda m: "sha256:feed") -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            code = RS.main([str(script)], generate=gen, digest=digest,
                           runtime=lambda: "0.0.0-test", runs=runs)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue()


with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    script = tmp / "fix.ps1"
    body = "$ErrorActionPreference = 'Stop'\nGet-Service | Stop-Service\nWrite-Output done\n"
    script.write_text(body, encoding="utf-8")

    print("case 1  Project 3's contract, not a copy")
    gen = Spy(APPROVE)
    code, _ = run(script, gen, tmp / "r1")
    messages, schema, model = gen.calls[0]
    check("prompt == review_runner.messages_for(name, text)",
          messages == RR.messages_for("fix.ps1", body))
    check("schema == review.review_schema(3)", schema == R.review_schema(3))
    check("model is qwen3:14b (ADR 0043)", model == "qwen3:14b", model)
    item = R.load_set()[0]
    old_user = f"Script: {item.script}\n\n{RR.numbered(item.text().rstrip(chr(10)))}"
    check("build_messages() for a P3 item is unchanged by the refactor",
          RR.build_messages(item)[1]["content"] == old_user
          and RR.build_messages(item)[0]["content"].endswith(RR.SYSTEM_TAIL))

    print("case 2  exit codes")
    code, out = run(script, Spy(APPROVE), tmp / "r2a")
    check("approve -> 0", code == 0, f"exit={code}")
    code, out = run(script, Spy(REVISE), tmp / "r2b")
    check("blocking finding -> 1", code == 1, f"exit={code}")
    check("the finding is printed with its line and the line's text",
          "line 2" in out and "Get-Service | Stop-Service" in out and "Name the one service." in out)
    code, out = run(script, Spy(LIES), tmp / "r2c")
    check("a reply that breaks the contract -> 2, not an approval", code == 2, f"exit={code}")
    code, out = run(script, Spy(error=model_client.ModelError("timed out")), tmp / "r2d")
    check("model error -> 2", code == 2, f"exit={code}")

    print("case 3  no digest, no review")
    gen = Spy(APPROVE)

    def no_digest(m):
        raise model_client.ModelError("connection refused")

    code, _ = run(script, gen, tmp / "r3", digest=no_digest)
    check("-> 2", code == 2, f"exit={code}")
    check("the model was never called", gen.calls == [])

    print("case 4  the file")
    cases = {"missing": tmp / "nope.ps1", "empty": tmp / "empty.ps1",
             "not utf-8": tmp / "latin.ps1", "too big": tmp / "big.ps1"}
    cases["empty"].write_text("  \n\n", encoding="utf-8")
    cases["not utf-8"].write_bytes(b"Write-Output \xe9t\xe9\n")
    cases["too big"].write_text("#" * (RS.MAX_BYTES + 1), encoding="utf-8")
    for label, path in cases.items():
        gen = Spy(APPROVE)
        code, _ = run(path, gen, tmp / "r4")
        check(f"{label} -> 2, model never called", code == 2 and gen.calls == [], f"exit={code}")
    crlf = tmp / "crlf.ps1"
    crlf.write_bytes(b"\xef\xbb\xbf" + body.replace("\n", "\r\n").encode("utf-8"))
    gen = Spy(APPROVE)
    code, _ = run(crlf, gen, tmp / "r4b")
    check("BOM + CRLF: same prompt and schema as the LF file",
          gen.calls and gen.calls[0][0][1]["content"] == RR.messages_for("crlf.ps1", body)[1]["content"]
          and gen.calls[0][1] == R.review_schema(3))

    print("case 5  the record; no --model flag")
    r5 = tmp / "r5"
    code, _ = run(script, Spy(REVISE), r5)
    rec = json.loads(next(r5.glob("*-review-fix.ps1.json")).read_text())
    import hashlib
    check("sha256, model, digest, verdict, exit code",
          rec["sha256"] == hashlib.sha256(body.encode()).hexdigest() and rec["model"] == "qwen3:14b"
          and rec["model_digest"] == "sha256:feed" and rec["ollama_version"] == "0.0.0-test"
          and rec["verdict"] == "revise"
          and rec["exit_code"] == 1 and len(rec["findings"]) == 1)
    out = io.StringIO()
    with contextlib.redirect_stderr(out):
        try:
            RS.main(["--model", "hermes3:8b", str(script)], generate=Spy(APPROVE),
                    digest=lambda m: "x", runtime=lambda: "x", runs=r5)
            code = 0
        except SystemExit as exc:
            code = exc.code
    check("--model is refused (exit 2): a reviewer picked at the prompt is unmeasured", code == 2)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
    sys.exit(1)
print("all review-script checks passed")
