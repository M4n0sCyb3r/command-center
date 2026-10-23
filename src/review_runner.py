"""Project 3, build step 4: run a model reviewer over the set and score it (ADRs 0028-0031).

ONE PROMPT, ONE RUBRIC, ONE SAMPLING POLICY FOR EVERY MODEL ARM
---------------------------------------------------------------
The arms differ in the MODEL only - `drafter-alone` is qwen3:8b (the drafter
reviewing), `critic` is hermes3:8b. Same system prompt (the committed rubric),
same numbered script, same JSON schema, same sampling (ADR 0009's pinned
values, seed 42), same fresh load before every call (ADR 0025). Change any of
those per arm and a catch-rate difference stops being attributable to the model.

THE SPLIT IS ENFORCED HERE
--------------------------
`--split held_out` is refused unless `--final` is passed (ADR 0028: held-out
data is read once, for the final measurement - build step 8). Development is
the default.

FAILURES ARE ROWS, NEVER CATCHES
--------------------------------
A reply that is not a valid review (bad JSON, schema breach, verdict that
contradicts its findings, a model error) becomes an `error` row: it counts as
NOT caught and NOT a false alarm, and the error count is reported beside the
rates. A broken reviewer must not look like a cautious one.

Run (bench, Ollama up):
    python src/review_runner.py --arm drafter-alone
    python src/review_runner.py --arm critic
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import model_client                                   # noqa: E402
import review as R                                    # noqa: E402
from structured import SchemaError, parse_json        # noqa: E402

ARMS = {"drafter-alone": "qwen3:8b", "critic": "hermes3:8b",
        # ADR 0032's "escalate everything" ceiling: the escalation tier reviewing every original.
        "escalator": "qwen3:14b"}
RUBRIC = R.P3 / "rubric.md"
# ADR 0031 amendment 1 (2026-10-18, Jose): a review gets its OWN budget, the same for every arm.
# The shared 2,048-token cap and 120 s timeout were set for Project 2's short answers; qwen3:8b's
# thinking ran past them on 15/32 reviews (10 cap, 5 timeout), mean 1,459 output tokens on the rest.
REVIEW_MAX_TOKENS = 6144
REVIEW_TIMEOUT = 300
NUM_CTX = 12288
RUNS = R.REPO_ROOT / "evals" / "runs"

SYSTEM_TAIL = """
You review the script the user sends. Line numbers are given at the start of each line as
`NNN| `; they are not part of the script. Reply with JSON only, matching the schema:
{"verdict": "approve" | "revise", "findings": [ ... at most 5 ... ]}.
"""


def numbered(text: str) -> str:
    return "\n".join(f"{i:>3}| {line}" for i, line in enumerate(text.split("\n"), start=1))


def build_messages(item: R.Item, text: str | None = None) -> list[dict[str, str]]:
    """The review prompt. `text` lets the revision loop (step 5) review a REVISED copy with the
    exact same prompt the step-4 arms used; omitted, it is the item's own file."""
    return messages_for(item.script, item.text() if text is None else text)


def messages_for(script: str, body: str) -> list[dict[str, str]]:
    """The prompt itself, for any script. P06 step 2's `review` job calls this directly, so an
    operator's script is reviewed with EXACTLY the prompt Project 3 measured - one prompt, one
    home, never a copy that drifts."""
    system = RUBRIC.read_text(encoding="utf-8") + SYSTEM_TAIL
    user = f"Script: {script}\n\n{numbered(body.rstrip(chr(10)))}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


Generate = Callable[[list[dict[str, str]], dict[str, Any], str], "model_client.Reply"]


def unload_all() -> None:
    """Unload EVERY P03 model, so the called one loads fresh and alone. Step 5 (void run
    20261019T032719Z): a resident qwen3:8b - kept by Ollama's keep-alive after an earlier call -
    flipped 4/32 hermes3:8b verdicts. A previous run's model is the same hazard."""
    for m in dict.fromkeys(ARMS.values()):
        model_client.unload(m)


def default_generate(messages: list[dict[str, str]], schema: dict[str, Any], model: str):
    unload_all()                                      # fresh AND alone before every call (ADR 0025)
    return model_client.chat(messages, model=model, schema=schema, num_ctx=NUM_CTX,
                             max_tokens=REVIEW_MAX_TOKENS, timeout=REVIEW_TIMEOUT)


def review_item(item: R.Item, model: str, generate: Generate = default_generate) -> dict[str, Any]:
    n = item.n_lines()
    schema = R.review_schema(n)
    row: dict[str, Any] = {"id": item.id, "split": item.split, "control": item.is_control}
    t0 = time.monotonic()
    try:
        reply = generate(build_messages(item), schema, model)
        row.update(prompt_tokens=reply.prompt_tokens, completion_tokens=reply.completion_tokens,
                   duration_s=round(reply.duration_s, 3), raw=reply.text)
        review = R.validate_review(parse_json(reply.text, schema), n)
    except (SchemaError, model_client.ModelError) as exc:
        row.update(error=f"{type(exc).__name__}: {exc}"[:300], wall_s=round(time.monotonic() - t0, 3))
        if not item.is_control:
            row.update(family=item.defect["family"], caught=False, right_reason=False)
        else:
            row["false_alarm"] = False
        return row
    row.update(R.score_item(item, review))
    row["review"] = {"verdict": review.verdict, "findings": [f.__dict__ for f in review.findings]}
    row["wall_s"] = round(time.monotonic() - t0, 3)
    return row


def select(split: str, final: bool) -> list[R.Item]:
    if split == "held_out" and not final:
        raise SystemExit("refused: held-out data is read once, for the final measurement (--final, step 8)")
    return [it for it in R.load_set() if it.split == split]


def run(arm: str, split: str, final: bool = False, generate: Generate = default_generate) -> dict[str, Any]:
    model = ARMS[arm]
    items = select(split, final)
    rows = []
    for n, it in enumerate(items, start=1):
        row = review_item(it, model, generate)
        rows.append(row)
        verdict = ("ERROR" if "error" in row else
                   ("false-alarm" if row.get("false_alarm") else "clean") if row["control"] else
                   ("caught" if row["caught"] else "missed"))
        # Progress on stderr, one line per review: a silent multi-minute run looks hung
        # (bench SOP), and type-ahead into a "hung" window runs when it finishes.
        print(f"  [{n:>2}/{len(items)}] {it.id:<7} {verdict:<11} {row.get('wall_s', 0):>6.1f}s",
              file=sys.stderr, flush=True)
    tok_in = sum(r.get("prompt_tokens") or 0 for r in rows)
    tok_out = sum(r.get("completion_tokens") or 0 for r in rows)
    summary = {**R.summarize(rows), "errors": sum("error" in r for r in rows),
               "prompt_tokens": tok_in, "completion_tokens": tok_out,
               "model_seconds": round(sum(r.get("duration_s") or 0 for r in rows), 1)}
    return {"arm": arm, "model": model, "split": split, "items": len(rows),
            "sampling": model_client.request_options(model_client.SAMPLING["temperature"],
                                                     model_client.DEFAULT_SEED, REVIEW_MAX_TOKENS),
            "num_ctx": NUM_CTX, "timeout_s": REVIEW_TIMEOUT,
            "rubric_sha256": __import__("hashlib").sha256(RUBRIC.read_bytes()).hexdigest()[:16],
            "summary": summary, "rows": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run one model reviewer arm over the P03 set.")
    ap.add_argument("--arm", required=True, choices=sorted(ARMS))
    ap.add_argument("--split", choices=("development", "held_out"), default="development")
    ap.add_argument("--final", action="store_true", help="permit held-out (build step 8 only)")
    a = ap.parse_args(argv)
    try:
        digest = model_client.model_digest(ARMS[a.arm])
        record = run(a.arm, a.split, a.final)
    except model_client.ModelError as exc:
        print(f"review_runner: could not establish the run: {exc}", file=sys.stderr)
        return 2
    record["model_digest"] = digest
    record["started"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / f"{record['started'].replace(':', '').replace('-', '')}-p3-{a.arm}-{a.split}.json"
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    s = record["summary"]
    print(f"=== P03 review arm: {a.arm} ({record['model']}, digest {digest[:12]}) on {a.split}")
    print(f"  caught       {s['caught']}/{s['defects']}  "
          + "  ".join(f"{k} {v['caught']}/{v['of']}" for k, v in s["by_family"].items()))
    print(f"  right reason {s['right_reason']}   false alarms {s['false_alarms']}/{s['controls']}   errors {s['errors']}")
    print(f"  tokens       {s['prompt_tokens']} in / {s['completion_tokens']} out   model {s['model_seconds']} s")
    print(f"  record       {out.relative_to(R.REPO_ROOT)}")
    return 0 if s["errors"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
