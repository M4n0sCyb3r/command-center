#!/usr/bin/env python3
"""The answer step: retrieved chunks in, a cited answer or a NAMED refusal out.

P02 build step 6a. Decided in ADR 0018 (the model picks, the code checks; one
retry), ADR 0023 (refusing a near-miss is this step's job) and ADR 0025 (three
outcomes, a context budget, the decoder may only cite what was retrieved).

FOUR OUTCOMES, never collapsed:
  answered                the model answered and every cited id was retrieved
  refused-no-retrieval    retrieve() returned [] - the model was never called
  refused-not-in-sources  the model read the chunks and said they don't answer
  refused-citation        two replies in a row failed the citation check

An honest "no" (not-in-sources) and an invented source (citation) are different
events. Counting them as one number would hide the one we most need to see.

WHAT IS CHECKED WHERE:
  shape       parse_json() against SHAPE - garbage is SchemaError, not retried
  membership  _check() in code - CitationError, retried once
  budget      _check_budget() BEFORE sending - ContextBudgetError, never sent

The schema sent to the decoder (wire_schema) enumerates the retrieved ids, so a
mistyped path cannot be generated at all. The LOCAL check uses SHAPE, which has
no enum, so an id that slips past the decoder anyway reaches the membership
check and is reported as a citation failure - not hidden inside a SchemaError.

    python tests/test_answer_step.py      # no model: scripted replies
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable

from model_client import MAX_TOKENS, ModelError, chat
from retrieval import Chunk, Hit, retrieve
from structured import parse_json

ANSWERED = "answered"
REFUSED_NO_RETRIEVAL = "refused-no-retrieval"
REFUSED_NOT_IN_SOURCES = "refused-not-in-sources"
REFUSED_CITATION = "refused-citation"

STATUS_ANSWERED = "answered"
STATUS_NOT_IN_SOURCES = "not_in_sources"

# ADR 0025 part 5. The server's default context is 4,096 tokens and MAX_TOKENS
# reserves 2,048 of them for generation (thinking included). Three chunks can
# reach ~9k tokens, and Ollama drops the START of an over-long prompt - the
# instructions - without an error. So: an explicit context, and a budget that
# refuses before sending. 2.5 chars/token is deliberately pessimistic (command
# syntax is dense - ADR 0024); replace with the bench's measured ratio.
ANSWER_NUM_CTX = 16_384
CHARS_PER_TOKEN_FLOOR = 2.5

# Key order is decoding order (ADR 0025 part 7): decide, choose sources, THEN write.
SHAPE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": [STATUS_ANSWERED, STATUS_NOT_IN_SOURCES]},
        "cited": {"type": "array", "items": {"type": "string"}},
        "answer": {"type": "string"},
    },
    "required": ["status", "cited", "answer"],
    "additionalProperties": False,
}

SYSTEM = (
    "You answer operational questions about Windows Server using ONLY the sources "
    "provided. Each source starts with its id in square brackets.\n"
    "- If the sources contain the answer: status \"answered\", list in \"cited\" the id "
    "of every source you used, and write the answer from those sources only.\n"
    "- If the sources are about the topic but do not answer THIS question: status "
    "\"not_in_sources\", \"cited\" empty, and say in one sentence what is missing.\n"
    "Never use knowledge that is not in the sources."
)


class CitationError(Exception):
    """The reply was valid data but cited what it was never shown, or cited
    inconsistently with its status. Deliberately NOT SchemaError (ADR 0018)."""


class ContextBudgetError(Exception):
    """The prompt would not fit the context with room to generate. Raised
    INSTEAD of sending - a truncated prompt is never sent (ADR 0025 part 5)."""


@dataclass
class Answer:
    question: str
    outcome: str
    text: str
    cited: list[str]
    attempts: int                       # model calls made: 0, 1 or 2
    retrieved: list[tuple[str, float]]  # (chunk_id, score), rank order
    errors: list[str] = field(default_factory=list)   # one per failed attempt
    sources: dict[str, Chunk] = field(default_factory=dict)  # the CITED chunks, by id


Generate = Callable[[list[dict[str, str]], dict[str, Any]], str]


def wire_schema(ids: list[str]) -> dict[str, Any]:
    """SHAPE, with cited restricted to the retrieved ids - for the DECODER only."""
    schema = json.loads(json.dumps(SHAPE))
    schema["properties"]["cited"]["items"] = {"type": "string", "enum": list(ids)}
    return schema


def build_messages(question: str, hits: list[Hit]) -> list[dict[str, str]]:
    sources = "\n\n".join(
        f"[{h.chunk.chunk_id}]\n{h.chunk.title} - {h.chunk.heading}\n{h.chunk.text}"
        for h in hits
    )
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Sources:\n\n{sources}\n\nQuestion: {question}"},
    ]


def estimate_tokens(messages: list[dict[str, str]]) -> int:
    return math.ceil(sum(len(m["content"]) for m in messages) / CHARS_PER_TOKEN_FLOOR)


def _check_budget(messages: list[dict[str, str]], num_ctx: int) -> None:
    need = estimate_tokens(messages) + MAX_TOKENS
    if need > num_ctx:
        raise ContextBudgetError(
            f"prompt ~{need - MAX_TOKENS} tokens + {MAX_TOKENS} for generation = {need} "
            f"> num_ctx {num_ctx}; refusing to send a prompt the server would truncate"
        )


def _check(data: dict[str, Any], ids: list[str]) -> None:
    """Membership and consistency. Raises CitationError naming what is wrong."""
    cited = data["cited"]
    unknown = [c for c in cited if c not in ids]
    if unknown:
        raise CitationError(f"cited ids that were not retrieved: {unknown}")
    if data["status"] == STATUS_ANSWERED and not cited:
        raise CitationError("status is answered but cited is empty - an answer must name its source")
    if data["status"] == STATUS_NOT_IN_SOURCES and cited:
        raise CitationError(f"status is not_in_sources but cited {cited} - a refusal cites nothing")


def _retry_message(error: CitationError, ids: list[str]) -> dict[str, str]:
    return {"role": "user", "content": (
        f"Your reply failed the citation check: {error}. "
        f"The only valid ids are: {ids}. Reply again in the same JSON shape."
    )}


def default_generate(messages: list[dict[str, str]], schema: dict[str, Any],
                     *, num_ctx: int = ANSWER_NUM_CTX) -> str:
    reply = chat(messages, schema=schema, num_ctx=num_ctx)
    # Backstop, one-sided: a prefix-cache hit can only LOWER prompt_tokens.
    if reply.prompt_tokens is not None and reply.prompt_tokens + MAX_TOKENS > num_ctx:
        raise ContextBudgetError(
            f"server evaluated {reply.prompt_tokens} prompt tokens; with {MAX_TOKENS} "
            f"for generation that exceeds num_ctx {num_ctx}"
        )
    return reply.text


def answer(question: str, *, k: int = 3, hits: list[Hit] | None = None,
           index: Any = None, generate: Generate | None = None,
           num_ctx: int = ANSWER_NUM_CTX) -> Answer:
    """Retrieve (unless hits are given), ask once, check, retry at most once."""
    hits = retrieve(question, k=k, index=index) if hits is None else hits
    retrieved = [(h.chunk.chunk_id, h.score) for h in hits]
    if not hits:
        return Answer(question, REFUSED_NO_RETRIEVAL, "", [], 0, retrieved)

    if generate is None:
        def generate(m: list[dict[str, str]], s: dict[str, Any]) -> str:
            return default_generate(m, s, num_ctx=num_ctx)

    ids = [chunk_id for chunk_id, _ in retrieved]
    schema = wire_schema(ids)
    messages = build_messages(question, hits)
    _check_budget(messages, num_ctx)

    errors: list[str] = []
    for attempt in (1, 2):
        text = generate(messages, schema)
        data = parse_json(text, SHAPE)        # SchemaError propagates: garbage is not retried
        try:
            _check(data, ids)
        except CitationError as exc:
            errors.append(str(exc))
            if attempt == 2:
                return Answer(question, REFUSED_CITATION, "", [], 2, retrieved, errors)
            messages = messages + [{"role": "assistant", "content": text},
                                   _retry_message(exc, ids)]
            _check_budget(messages, num_ctx)
            continue
        outcome = ANSWERED if data["status"] == STATUS_ANSWERED else REFUSED_NOT_IN_SOURCES
        by_id = {h.chunk.chunk_id: h.chunk for h in hits}
        return Answer(question, outcome, data["answer"], list(data["cited"]),
                      attempt, retrieved, errors,
                      {cid: by_id[cid] for cid in data["cited"]})
    raise AssertionError("unreachable: the loop returns on attempt 2")


# --- 6b: rendering (ADRs 0019, 0020, 0021) ---------------------------------
# Every word of a citation comes from the Chunk and the manifest. Nothing the
# model wrote in "answer" is ever treated as a citation (ADR 0018), so the text
# a Microsoft page contains cannot relabel itself as ours.


@dataclass(frozen=True)
class Citation:
    chunk_id: str
    path: str
    title: str
    heading: str
    authorship: str            # the FIELD (ADR 0019) - the label is rendered from it
    upstream: str | None       # microsoft only: "<repo>@<commit>:<upstream_path>" (ADR 0021)

    def label(self) -> str:
        who = "ours" if self.authorship == "ours" else "Microsoft, CC BY 4.0"
        line = f"[{who}] {self.path} - {self.title} > {self.heading}"
        return line + (f"\n      upstream: {self.upstream}" if self.upstream else "")


@dataclass(frozen=True)
class Rendered:
    outcome: str
    text: str
    citations: tuple[Citation, ...]
    warnings: tuple[str, ...]      # reported, never gated (ADR 0019)
    attribution: str | None        # once per answer that cites Microsoft (ADR 0021)

    def __str__(self) -> str:
        if self.outcome != ANSWERED:
            why = {REFUSED_NO_RETRIEVAL: "the corpus does not cover this",
                   REFUSED_NOT_IN_SOURCES: "the sources found do not answer this",
                   REFUSED_CITATION: "no answer could be traced to a source"}[self.outcome]
            return f"No answer: {why}." + (f"\n{self.text}" if self.text else "")
        lines = [self.text, "", "Sources:"] + [f"  {c.label()}" for c in self.citations]
        lines += [f"WARNING: {w}" for w in self.warnings]
        if self.attribution:
            lines += ["", self.attribution]
        return "\n".join(lines)


def render(a: Answer, manifest: Any) -> Rendered:
    """Turn an Answer into what a person reads. Raises KeyError if a cited
    chunk's source is not in the manifest - the index and the manifest have
    drifted, and a citation we cannot attribute is not rendered at all."""
    entries = {e.path: e for e in manifest.entries}
    up = manifest.upstream
    citations: list[Citation] = []
    for cid in a.cited:
        chunk = a.sources[cid]
        entry = entries[chunk.source]
        if entry.authorship != chunk.authorship:
            raise ValueError(f"{cid}: index says {chunk.authorship!r}, manifest says "
                             f"{entry.authorship!r} - rebuild the index")
        upstream = (f"{up.repo}@{up.commit}:{entry.upstream_path}"
                    if entry.authorship == "microsoft" else None)
        citations.append(Citation(cid, chunk.source, chunk.title, chunk.heading,
                                  entry.authorship, upstream))

    cited_paths = {c.path for c in citations}
    warnings = []
    for c in citations:
        target = entries[c.path].targets
        if c.authorship == "microsoft" and target and target not in cited_paths:
            warnings.append(f"cited Microsoft page {c.path} but not {target}, "
                            f"the runbook it competes with")
    warnings = list(dict.fromkeys(warnings))          # one per page, order kept

    microsoft = any(c.authorship == "microsoft" for c in citations)
    return Rendered(a.outcome, a.text, tuple(citations), tuple(warnings),
                    up.attribution if microsoft else None)


def _cli() -> int:
    """The `ask` job: 0 answered (or an honest refusal, printed); 2 could not answer."""
    import sys
    q = " ".join(sys.argv[1:]) or "print jobs are piling up and nothing comes out"
    try:
        a = answer(q)
    except (ModelError, ContextBudgetError) as exc:
        print(f"could not answer: {exc}")
        return 2
    from manifest import load_manifest
    record = {k: v for k, v in a.__dict__.items() if k != "sources"}
    print(json.dumps(record, indent=2))
    print("\n--- rendered ---")
    print(render(a, load_manifest()))
    return 0


if __name__ == "__main__":
    from entry import run_guarded       # a crash exits 2, never Python's default 1
    run_guarded(_cli, "ask")
