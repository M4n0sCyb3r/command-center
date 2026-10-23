"""Retrieval done honestly.

A model cannot know your documents. This module finds the right piece of text
BEFORE the model is asked anything - and, more importantly, can say with a
number whether it found the right piece. Retrieval quality and answer quality
are separate failures; this file is the half that can be measured alone.
"""

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from model_client import ModelError, base_url
from manifest import MANIFEST_PATH, load_manifest, strip_front_matter
from suite import parse_suite

# What the index may know is decided by data/corpus.jsonl, never by what
# happens to sit in a folder (ADR 0011). Lab 6 globbed data/runbooks/*.md;
# P02 build step 1 replaced that with the manifest.


@dataclass(frozen=True)
class Chunk:
    """One retrievable piece of text, and where it came from.

    frozen=True for the same reason Observation was: a chunk is a fact about
    a document. If code downstream can edit the text after it was indexed,
    the vector on file no longer describes the text you are holding.
    """

    chunk_id: str    # "data/runbooks/service-restart.md#procedure" - manifest path + slug (ADR 0020)
    source: str      # the manifest path, verbatim - never recomputed from the filesystem
    title: str       # the document's H1
    heading: str     # the section's H2
    text: str        # the body, headings stripped
    authorship: str  # "ours" | "microsoft" - copied from the manifest, never from the folder (ADR 0019)

    def embed_text(self) -> str:
        """What actually gets embedded - NOT the bare body.

        A body reading "Query the service first" is near-identical across four
        of these documents. Prefixing title and heading is what makes the
        vector describe THIS section of THIS document instead of a generic
        paragraph about services.
        """
        return f"{self.title} - {self.heading}\n{self.text}"


def slug(text: str) -> str:
    """'Recovery options' -> 'recovery-options'. Stable, lowercase, URL-safe."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ADR 0017: on a Microsoft page these sections are link lists - many words shared
# with the runbooks, no answer in any of them. Dropped by manifest authorship,
# never by sniffing the file. Compared lowercase.
MS_DROP_HEADINGS = frozenset({
    "related links", "related topics", "related content", "see also",
    "additional references",
})

FENCES = ("```", "~~~")

# ADR 0017: the lead becomes a chunk under this heading - id "<path>#overview".
# A document that ALSO has a "## Overview" section would collide; load_corpus's
# duplicate-id check refuses that loudly rather than letting one shadow the other.
OVERVIEW_HEADING = "Overview"

# ADR 0024: the most characters one chunk may hand the embedder (title + heading + body).
# nomic-embed-text reads ~2,048 tokens; Ollama silently cuts anything longer, so the
# end of a long section was never seen. Measured 2026-10-07 (tools/probe_embed_context.py):
# largest chunk that fits 8,197 chars / 1,865 tokens; densest text 3.8 chars per token.
# 7,500 sits just above our largest chunk (7,090), so no `ours` chunk changes. A section
# over the cap is cut into parts at blank lines, then at line ends - never mid-line, never
# inside a code fence. If this estimate is ever wrong, embed() refuses to truncate, so the
# index build fails naming the chunk instead of indexing half of it.
MAX_EMBED_CHARS = 7500
# A section over the cap is cut into parts of at most this many characters. Lower than
# the cap on purpose: a whole section under the cap has been read by the server (probe),
# a part is new text nobody has measured. First bench run (2026-10-07) proved the margin
# is needed - two w32tm parts of 7,340 / 7,485 chars were refused (command syntax runs
# denser than 3.6 chars per token). Cutting smaller costs nothing once we are cutting.
PART_TARGET_CHARS = 5000
PART_MARK = "~"      # "<path>#<slug>~2": slug() never emits '~', so a part id cannot collide


def _blocks(text: str) -> list[str]:
    """Paragraph blocks: split at blank lines, but a code fence is one block, whole."""
    blocks, current, in_fence = [], [], False
    for line in text.splitlines():
        if line.lstrip().startswith(FENCES):
            in_fence = not in_fence
        if not in_fence and not line.strip() and not line.lstrip().startswith(FENCES):
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _pack(text: str, budget: int, *, name: str) -> list[str]:
    """Cut text into pieces of at most `budget` characters (ADR 0024).

    Whole paragraphs first. A paragraph too big on its own (a long table is one
    paragraph) is cut at line ends - a table row stays whole. A single line or a
    code fence too big on its own is refused: cutting either would split a
    sentence or a command, and a half-command retrieved is worse than none.
    """
    units: list[tuple[str, str]] = []           # (text, joiner placed BEFORE it)
    for block in _blocks(text):
        if len(block) <= budget:
            units.append((block, "\n\n"))
            continue
        if any(line.lstrip().startswith(FENCES) for line in block.splitlines()):
            raise ValueError(f"{name}: a code block of {len(block)} chars exceeds the "
                             f"chunk budget {budget} and will not be cut (ADR 0024)")
        for i, line in enumerate(block.splitlines()):
            if len(line) > budget:
                raise ValueError(f"{name}: one line of {len(line)} chars exceeds the "
                                 f"chunk budget {budget} and will not be cut (ADR 0024)")
            units.append((line, "\n\n" if i == 0 else "\n"))
    pieces, current = [], ""
    for unit, joiner in units:
        candidate = unit if not current else current + joiner + unit
        if len(candidate) <= budget:
            current = candidate
        else:
            pieces.append(current)
            current = unit
    if current:
        pieces.append(current)
    return pieces


def split_document(path: Path, *, source: str, authorship: str) -> list[Chunk]:
    """Cut one markdown file into chunks, one per '##' section.

    The heading is the cut line because the author already decided there that
    one idea ended and the next began. Cutting every N characters instead -
    the default in most tutorials - splits mid-sentence and produces chunks
    that answer nothing.

    `source` and `authorship` come from the manifest entry, not from `path`:
    the file is only where the bytes are read from. The id is the manifest
    path so the manifest, the index and a citation share one string (ADR 0020).
    Authorship is NOT added to embed_text() - P02 step 3's call: this step is a
    rename, so every score must come back identical; an authorship prefix in
    the embedding is its own experiment, after the Microsoft slice lands.

    P02 step 4a (ADR 0017): front matter is stripped first; inside a code
    fence no line is a heading and none is dropped (a PowerShell '# comment'
    is code, not a title); Microsoft link-list sections are dropped.

    P02 step 4b (ADR 0017, finding 1): text between the H1 and the first '##'
    becomes an "Overview" chunk. It used to be discarded - four of our SOPs
    lost their leads, and a Microsoft page that keeps its whole body above the
    first '##' would have indexed only its link list. A document with no lead
    text gets no Overview chunk, so the runbooks are unchanged.
    """
    raw = strip_front_matter(path.read_text(encoding="utf-8"), name=path.name)

    chunks: list[Chunk] = []
    title: str | None = None
    heading: str | None = None
    body: list[str] = []

    def flush() -> None:
        """Close the section in progress, if it earned a chunk.

        heading None means the lead: everything after the H1 and before the
        first '##'. It is a section like any other, named OVERVIEW_HEADING.
        """
        if title is None:
            return          # text above the H1 anchors to nothing; not indexed
        name = OVERVIEW_HEADING if heading is None else heading
        text = "\n".join(body).strip()
        if not text:
            return          # a heading with no body retrieves nothing; drop it
        if authorship == "microsoft" and name.lower() in MS_DROP_HEADINGS:
            return          # a link list: shared words, no answer (ADR 0017)
        whole = Chunk(chunk_id=f"{source}#{slug(name)}", source=source, title=title,
                      heading=name, text=text, authorship=authorship)
        if len(whole.embed_text()) <= MAX_EMBED_CHARS:
            chunks.append(whole)        # the common case: id and text exactly as before
            return
        # ADR 0024: too long to be read whole. Budget leaves room for the longest label.
        label = f"{title} - {name} (part 99 of 99)\n"
        pieces = _pack(text, PART_TARGET_CHARS - len(label), name=f"{path.name}#{slug(name)}")
        for i, piece in enumerate(pieces, start=1):
            chunks.append(Chunk(chunk_id=f"{source}#{slug(name)}{PART_MARK}{i}", source=source,
                                title=title, heading=f"{name} (part {i} of {len(pieces)})",
                                text=piece, authorship=authorship))

    in_fence = False
    for line in raw.splitlines():
        if line.lstrip().startswith(FENCES):
            in_fence = not in_fence
            body.append(line)
        elif in_fence:
            body.append(line)           # code is body, whatever it starts with
        elif line.startswith("## "):
            # A chunk with no document title is unanchored - "Query the service
            # first" could belong to any file here. Fail loudly, not later.
            if title is None:
                raise ValueError(f"{path.name}: '## ' before any '# Title' line, "
                                 "so chunks cannot be anchored")
            flush()                     # close the previous section first
            heading = line[3:].strip()
            body = []
        elif line.startswith("# "):
            if title is None:
                title = line[2:].strip()
                body = []               # the lead starts AFTER the H1
            # any later H1 is dropped, as before
        else:
            body.append(line)
    if in_fence:
        raise ValueError(f"{path.name}: a code fence opened and never closed")
    if title is None:
        raise ValueError(f"{path.name}: no '# Title' line, so chunks cannot be anchored")
    flush()                             # the last section has no '##' after it

    return chunks


def load_corpus(manifest_path: Path = MANIFEST_PATH, **manifest_kwargs) -> list[Chunk]:
    """Every chunk of every document the manifest lists, in MANIFEST order.

    The order is the file's order - stable, and changed only by a diff someone
    reviewed. A file in data/runbooks/ that the manifest does not list is not
    indexed: that is the point.
    """
    manifest = load_manifest(manifest_path, **manifest_kwargs)
    chunks = [chunk
              for entry in manifest.entries
              for chunk in split_document(manifest.file(entry), source=entry.path,
                                          authorship=entry.authorship)]

    # chunk_id is the name the evaluation set will use to say "this one was
    # the right answer". Two chunks sharing an id makes every score a lie,
    # so it is caught here rather than discovered as a weird result later.
    # Same check, same reason, as "two tools are both named X" in Lab 3.
    ids = [chunk.chunk_id for chunk in chunks]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate chunk ids: {duplicates}")

    return chunks


EMBED_MODEL = "nomic-embed-text"
EMBED_TIMEOUT = 120          # first call loads the model into memory


def embed(texts: list[str], *, model: str = EMBED_MODEL) -> list[list[float]]:
    """Turn text into vectors. One HTTP call, many texts.

    Goes through base_url() rather than a hardcoded address: Lab 1 made the
    WSL-to-Windows gateway a runtime lookup because it changes on reboot, and
    reaching around that boundary here would reintroduce the bug in a new file.
    """
    if not texts:
        raise ValueError("nothing to embed")

    try:
        response = requests.post(
            f"{base_url()}/api/embed",
            # ADR 0024: never let the server cut a chunk silently. Too long = an error
            # naming the failure, not a vector of the first half.
            json={"model": model, "input": texts, "truncate": False},
            timeout=EMBED_TIMEOUT,
        )
        response.raise_for_status()
        vectors = response.json()["embeddings"]
    except (requests.RequestException, KeyError, ValueError) as exc:
        raise ModelError(f"embedding failed: {type(exc).__name__}: {exc}") from exc

    # Results come back as a bare list - paired with the inputs by POSITION and
    # nothing else. If the server ever returns a different count, every vector
    # after that point is attached to the wrong text, and nothing downstream
    # would look wrong. It would just be quietly, permanently incorrect.
    if len(vectors) != len(texts):
        raise ModelError(f"asked for {len(texts)} vectors, got {len(vectors)}")

    return vectors


def cosine(a: list[float], b: list[float]) -> float:
    """Similarity of two vectors: 1.0 identical direction, 0.0 unrelated.

    Cosine measures the ANGLE between vectors and ignores their length. That
    is the point: a 300-character chunk and a six-word question have wildly
    different magnitudes but can mean the same thing. Straight-line distance
    would punish that difference and rank by length instead of meaning.
    """
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        raise ValueError("a zero vector has no direction")

    return dot / (norm_a * norm_b)


INDEX_PATH = Path(__file__).resolve().parent.parent / "data" / "index.json"
INDEX_VERSION = 2   # 1 -> 2 at P02 step 3: ids are manifest paths, chunks carry authorship (ADR 0020)


@dataclass(frozen=True)
class Hit:
    """One chunk and how well it matched. The unit search returns."""

    chunk: Chunk
    score: float


def build_index(chunks: list[Chunk] | None = None, *, path: Path = INDEX_PATH) -> int:
    """Embed every chunk once and write the vectors to disk.

    Embedding is the slow, expensive step. Doing it per question would make
    every search wait on the model for text that has not changed since the
    last search. The index is a CACHE of a pure function - same chunk, same
    model, same vector - so it is safe to keep and cheap to throw away.
    """
    chunks = load_corpus() if chunks is None else chunks
    vectors = embed([chunk.embed_text() for chunk in chunks])

    payload = {
        # Stamped, not assumed. A vector only means something relative to the
        # model that produced it: mix nomic vectors with another model's and
        # cosine still returns a confident number, and the number is garbage.
        "version": INDEX_VERSION,
        "model": EMBED_MODEL,
        "entries": [
            {"chunk": asdict(chunk), "vector": vector}
            for chunk, vector in zip(chunks, vectors)
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return len(chunks)


def load_index(*, path: Path = INDEX_PATH) -> list[tuple[Chunk, list[float]]]:
    """Read the index back, refusing anything we cannot trust."""
    if not path.exists():
        raise FileNotFoundError(f"no index at {path} - run build_index() first")

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != INDEX_VERSION:
        raise ValueError(f"index version {payload.get('version')!r}, expected {INDEX_VERSION}")
    if payload.get("model") != EMBED_MODEL:
        raise ValueError(
            f"index was built with {payload.get('model')!r}, now using {EMBED_MODEL!r} - "
            "vectors from different models are not comparable; rebuild"
        )

    return [(Chunk(**entry["chunk"]), entry["vector"]) for entry in payload["entries"]]


def search(question: str, *, k: int = 3,
           index: list[tuple[Chunk, list[float]]] | None = None) -> list[Hit]:
    """The k best-matching chunks, best first.

    k is the honest knob in this whole file. Raise it and the right chunk is
    likelier to be in there somewhere; raise it too far and you hand the model
    a pile of near-misses and dare it to pick. Lab 6 exists to put a number on
    that trade instead of guessing at it.
    """
    if k < 1:
        raise ValueError("k must be at least 1")

    index = load_index() if index is None else index
    question_vector = embed([question])[0]

    hits = [Hit(chunk=chunk, score=cosine(question_vector, vector))
            for chunk, vector in index]
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits[:k]


SUITE_PATH = Path(__file__).resolve().parent.parent / "evals" / "suites" / "retrieval.jsonl"


@dataclass(frozen=True)
class Case:
    """One evaluation case. expect=None means the corpus cannot answer it.

    BUILD STEP 3 PART 2 added `id` and `tier`, and both are SPINE fields - the
    three things run_record.CaseRow requires of every scorer, of which this
    module supplies two and score_one() supplies the third (`passed`).

    `id` is named after the CHUNK the case expects, not after the question.
    That is deliberate: ADR 0007 matches a run against its baseline by id and
    never by position, so an id has to survive its question being reworded. The
    two unanswerable cases are named for WHY the corpus cannot answer them,
    having no target to be named after.

    `tier` is `fixed` for all fourteen. Retrieval reads a local index built
    deterministically from the committed runbooks (its hash is the run's
    `model_digest` condition), so nothing here reads a moving fact - the `live` tier belongs to Project 1's
    real endpoint and currently holds no cases at all, which is a fact the
    report has to state out loud rather than print as a zero (ADR 0006 call 2).
    """

    id: str
    tier: str
    question: str
    expect: str | None


def load_suite(*, path: Path = SUITE_PATH) -> list[Case]:
    """Read the golden set as this module's Case type.

    BUILD STEP 3 PART 2: the line-by-line parsing moved to suite.parse_suite(),
    which owns the rules score.py has to agree with - one header per file, an
    id on every case, a known tier, no duplicate ids. Written down twice, those
    rules would eventually differ in one place, and the failure would be quiet:
    one loader accepting a file the other rejects means the harness and this
    module disagree about what a case IS.

    What stays here is the part only retrieval knows: that a case needs
    `question` and `expect`, and that `expect` may be null. Note the check is
    for PRESENCE and not truth - `"expect": null` is a case that must be
    REFUSED, and testing it for truthiness would silently drop both negatives
    and take best_unanswerable_top_score with them.

    Signature and return type are unchanged, so every caller is untouched.
    """
    suite = parse_suite(path)
    cases: list[Case] = []
    for record in suite.cases:
        if "question" not in record or "expect" not in record:
            raise ValueError(
                f"{suite.path.name} case {record['id']!r}: needs 'question' and 'expect'"
            )
        cases.append(Case(id=record["id"], tier=record["tier"],
                          question=record["question"], expect=record["expect"]))
    return cases


def evaluate(*, k: int = 3, path: Path = SUITE_PATH,
             index: list[tuple[Chunk, list[float]]] | None = None,
             verbose: bool = True) -> dict:
    """Score retrieval ALONE. No model writes a word in this function.

    PROJECT 4 REFACTOR (build step 3): the per-case work moved into
    score_one() and the aggregates into derive(), both defined below. This
    function is now the composition of the two and its behaviour is
    UNCHANGED - same signature, same printing, same keys, same numbers. That
    was proved rather than asserted: the figures it returned before the
    refactor were captured and pinned as literals in
    tests/test_retrieval_seam.py, and the refactored code has to reproduce
    them.

    WHY IT MOVED. Project 4's run record is one ROW PER CASE with every total
    derived on read (ADR 0007 call 3a). A function that swallows the whole
    suite and hands back only aggregates cannot fill that record: you would
    know recall moved without ever learning WHICH case moved it, and the drift
    comparison matches cases by id. So the upper layer needed something this
    layer withheld, and the ruling this repo has now applied five times is to
    CHANGE THE LOWER LAYER rather than reach around it - Lab 1's
    windows_host_ip, Lab 4's parse_json, Lab 5's ToolResult.arguments, Lab 12's
    think() returning a tuple, and this.
    """
    rows = score_suite(k=k, path=path, index=index)
    if verbose:
        for row in rows:
            _print_row(row)
    return derive(rows, k=k)


def score_suite(*, k: int = 3, path: Path = SUITE_PATH,
                index: list[tuple[Chunk, list[float]]] | None = None) -> list[dict]:
    """Every case scored, as ROWS. evaluate() is derive() over this.

    Extracted at P02 build step 2(b) (ADR 0023) because threshold_verdict()
    needs the rows - which negatives were refused, which real question scored
    lowest - and evaluate() returns only totals. Same move as P04 step 3:
    change the lower layer rather than reach around it. evaluate()'s output
    is unchanged.
    """
    index = load_index() if index is None else index
    cases = load_suite(path=path)

    # A typo in an expected id would score zero forever, and you would spend
    # the evening "fixing retrieval" to chase a chunk that never existed.
    # The suite is checked against the index before anything is measured.
    known = {chunk.chunk_id for chunk, _ in index}
    missing = sorted({c.expect for c in cases if c.expect is not None and c.expect not in known})
    if missing:
        raise ValueError(f"suite expects chunk ids not present in the index: {missing}")

    return [score_one(case, k=k, index=index) for case in cases]


# WHAT THIS THRESHOLD PROMISES (ADR 0023, call 2b-iii): a cheap PRE-FILTER,
# not a guarantee. Two things, and nothing more:
#   1. no answerable question is ever refused  - strict, because the P04
#      harness grades answerable cases on rank and cannot see over-refusal;
#   2. at least REFUSAL_FLOOR of the out-of-domain questions are refused.
# It does NOT promise to refuse questions that share the corpus's words: a
# similarity score measures topic, not answerability. Those are the answer
# step's job (since P02 step 6c: evals/suites/answer.jsonl, category near-miss).
#
# MEASURED 2026-10-05, 44 chunks / 9 files, nomic-embed-text, 16 cases
# (12 answerable, 4 out-of-domain): worst answerable 0.5511; negatives 0.3563,
# 0.3763, 0.4651, and 0.5825 (sourdough - shares "restart", a KNOWN MISS).
# Set at the middle of the gap between the highest refusable negative (0.4651)
# and the worst real question (0.5511). Change the corpus or the model and
# re-run `python src/retrieval.py` before trusting either number.
REFUSE_BELOW = 0.51
REFUSAL_FLOOR = 0.75     # 3 of 4 on 2026-10-05; the known miss is the 4th


def score_one(case: Case, *, k: int = 3,
              index: list[tuple[Chunk, list[float]]] | None = None) -> dict:
    """Score ONE retrieval case. The per-case half of evaluate().

    Returns a plain dict rather than a dataclass because this is what lands in
    a run record's `detail` payload, and a record read next year must be
    loadable without importing this module.

    WHAT `passed` MEANS, and the one judgement call in an otherwise mechanical
    extraction:

      answerable case   -> passed when the expected chunk came back at all,
                           i.e. rank > 0. That is recall@k, per case, and it
                           is exactly what the old loop counted.

      unanswerable case -> passed when the top score falls BELOW REFUSE_BELOW,
                           i.e. retrieve() would correctly say nothing.

    The old loop asserted nothing per case about an unanswerable question - it
    only fed best_negative, and the quality claim lived at suite level in
    `separated`. A row needs a per-case verdict, and the honest choices were
    this or passed=True. **passed=True would be a vacuous field**, and a
    vacuous field is worse than no field: it inflates every pass rate by the
    number of negatives while looking like a measurement. REFUSE_BELOW is
    already the module's measured threshold and retrieve() already behaves
    exactly this way, so this asserts a property the module ALREADY CLAIMS
    rather than inventing a new one.

    It changes no aggregate in derive(), so evaluate()'s numbers are untouched.
    """
    hits = search(case.question, k=k, index=index)
    ids = [hit.chunk.chunk_id for hit in hits]
    top = hits[0].score if hits else 0.0

    if case.expect is None:
        return {"question": case.question, "answerable": False, "expect": None,
                "rank": 0, "top_score": top, "hit_ids": ids,
                "passed": top < REFUSE_BELOW}

    # Rank, not just hit/miss: position 1 and position 3 are different
    # outcomes even though both are "in the top 3".
    rank = ids.index(case.expect) + 1 if case.expect in ids else 0
    return {"question": case.question, "answerable": True, "expect": case.expect,
            "rank": rank, "top_score": top, "hit_ids": ids,
            "passed": rank > 0}


def derive(rows: list[dict], *, k: int = 3) -> dict:
    """Compute every suite-level number from the rows. Nothing is stored.

    Same rule as run_record.summarize() and tracing.summarize(): a total
    written down beside the rows it counts is a number that can disagree with
    them, and a number computed from them cannot. Here that rule buys
    something concrete - recall@k in a committed baseline is RECOMPUTABLE from
    the rows in the same file, so a wrong aggregate is detectable rather than
    merely believed.
    """
    positives = [r for r in rows if r["answerable"]]
    negatives = [r for r in rows if not r["answerable"]]

    found = sum(1 for r in positives if r["rank"])
    reciprocal = sum(1 / r["rank"] for r in positives if r["rank"])
    worst_positive = min((r["top_score"] for r in positives), default=1.0)
    best_negative = max((r["top_score"] for r in negatives), default=0.0)

    return {
        "k": k,
        "answerable_cases": len(positives),
        # Did the right chunk come back at all? The ceiling on any answer.
        "recall_at_k": found / len(positives),
        # Where did it come back? 1.0 means always first; 0.5 means typically
        # second. Recall cannot see this, and position is what the model reads.
        "mrr": reciprocal / len(positives),
        "worst_answerable_top_score": worst_positive,
        "best_unanswerable_top_score": best_negative,
        # If the worst real question still outscores the best fake one, a
        # refusal threshold EXISTS and its safe band is measured, not guessed.
        "separated": worst_positive > best_negative,
    }


def threshold_verdict(rows: list[dict], threshold: float = REFUSE_BELOW,
                      floor: float = REFUSAL_FLOOR) -> tuple[bool, str]:
    """Does the COMMITTED threshold keep the promise written above it? (ADR 0023)

    ADR 0022a checked the threshold against a BAND. Measurement on 2026-10-05
    broke the band: one shared word ("restart") lifted a bread question above
    a real runbook question, so no band existed. The promise was too big for
    a similarity score, and this checks the promise that IS keepable:

      TOP edge, strict  - every answerable row must score ABOVE the threshold.
                          A tie fails: retrieve() would keep it, but by a tie.
      refusal RATE      - unanswerable rows scoring BELOW the threshold (what
                          retrieve() refuses), as a share of all of them, must
                          reach the floor. Every miss is NAMED, passing or not,
                          so a known miss stays visible instead of averaged in.

    No negatives at all is a FAIL, never a vacuous 100%.
    Takes rows, not derive()'s totals: the verdict needs to know WHICH case.
    """
    answerable = [r for r in rows if r["answerable"]]
    negatives = [r for r in rows if not r["answerable"]]
    if not negatives:
        return False, "no unanswerable cases: the refusal rate cannot be measured"

    refused_real = [r for r in answerable if r["top_score"] <= threshold]
    missed = [r for r in negatives if r["top_score"] >= threshold]
    rate = (len(negatives) - len(missed)) / len(negatives)
    worst = min((r["top_score"] for r in answerable), default=1.0)
    named = "; ".join(f"{r['top_score']:.4f} '{r['question'][:40]}'" for r in missed)
    head = (f"threshold {threshold}, worst answerable {worst:.4f}, refused "
            f"{len(negatives) - len(missed)} of {len(negatives)} (floor {floor:.2f})")

    if refused_real:
        return False, (f"{head}: TOP edge crossed - an answerable question would "
                       f"be refused ({len(refused_real)} at or below the threshold)")
    if rate < floor:
        return False, f"{head}: refusal rate below the floor - answered: {named}"
    return True, head + (f"; known miss: {named}" if missed else "")


def _print_row(row: dict) -> None:
    """evaluate()'s verbose output, character for character as it was."""
    top, ids = row["top_score"], row["hit_ids"]
    if not row["answerable"]:
        print(f"  no-answer  top={top:.4f}  {row['question'][:46]}")
        return
    rank = row["rank"]
    label = f"rank {rank}" if rank else "MISS  "
    print(f"  {label:<9}  top={top:.4f}  {row['question'][:46]}")
    if not rank:
        print(f"             wanted {row['expect']}")
        print(f"             got    {ids}")


def retrieve(question: str, *, k: int = 3,
             index: list[tuple[Chunk, list[float]]] | None = None) -> list[Hit]:
    """search(), plus the ability to say nothing.

    search() always returns k results because sorting always succeeds. That is
    the dishonest default: hand a model three chunks about print spoolers and
    ask it about France, and it will write you a confident paragraph. An empty
    list is a real answer - it means the corpus does not cover this.
    """
    hits = search(question, k=k, index=index)
    if not hits or hits[0].score < REFUSE_BELOW:
        return []
    return hits


# --- smoke test -----------------------------------------------------------
# Six properties. The last two are new in kind: they do not check that the
# code RAN, they check that retrieval is still GOOD. A quality gate.

if __name__ == "__main__":
    import sys

    failures: list[str] = []

    # 1. Corpus loads, and every chunk carries its provenance into the text
    #    that actually gets embedded.
    chunks = load_corpus()
    sources = {chunk.source for chunk in chunks}
    print(f"corpus    : {len(chunks)} chunks from {len(sources)} files")
    first = chunks[0]
    if first.title not in first.embed_text() or first.heading not in first.embed_text():
        print("FAIL: embedded text drops title or heading")
        failures.append("provenance")

    # 2. The index round-trips: what comes back equals what went in.
    count = build_index(chunks)
    restored = load_index()
    print(f"index     : {count} vectors of {len(restored[0][1])} dimensions")
    if len(restored) != count or restored[0][0] != chunks[0]:
        print("FAIL: index did not round-trip")
        failures.append("round-trip")

    # 3. An index built by a DIFFERENT embedding model must be refused. Its
    #    vectors are meaningless here, and cosine would not complain.
    tampered = INDEX_PATH.with_name("index-tampered.json")
    payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    payload["model"] = "some-other-embedder"
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    try:
        load_index(path=tampered)
        print("FAIL: accepted an index from another model")
        failures.append("model stamp")
    except ValueError:
        print("model     : foreign index refused")
    finally:
        tampered.unlink(missing_ok=True)

    # 4. Cosine behaves: a vector against itself is 1.0, mismatched sizes raise.
    vector = restored[0][1]
    if abs(cosine(vector, vector) - 1.0) > 1e-9:
        print("FAIL: cosine of a vector with itself is not 1.0")
        failures.append("cosine")
    try:
        cosine([1.0, 0.0], [1.0, 0.0, 0.0])
        print("FAIL: compared vectors of different dimensions")
        failures.append("cosine guard")
    except ValueError:
        pass

    # 5. THE QUALITY GATE. Thresholds sit just under what was measured, so
    #    real drift trips them and ordinary noise does not.
    #    RE-SET 2026-10-21 (ADR 0046): the old 0.90 / 0.85 were set on the
    #    12-case suite and never moved when P02 step 8 grew it to 16 answerable
    #    (137e07f, baseline 0.875 / 0.802) - from 10-18 this gate could not
    #    pass on ANY Ollama version. Now just under the accepted 0.40.8
    #    baseline: recall 13/16 = 0.8125, MRR 25/32 = 0.78125. One more miss,
    #    or one rank-1 case slipping to rank 2, fails it.
    RECALL_FLOOR = 0.80
    MRR_FLOOR = 0.77
    rows = score_suite(k=3)
    report = derive(rows, k=3)
    print(f"retrieval : recall@3={report['recall_at_k']:.2f} "
          f"mrr={report['mrr']:.2f} over {report['answerable_cases']} cases")
    if report["recall_at_k"] < RECALL_FLOOR:
        print(f"FAIL: recall@3 fell to {report['recall_at_k']:.2f}")
        failures.append("recall")
    if report["mrr"] < MRR_FLOOR:
        print(f"FAIL: mrr fell to {report['mrr']:.2f}")
        failures.append("mrr")
    # Separation is REPORTED, no longer GATED (ADR 0023): a negative that
    # shares the corpus's words can outscore a real question, and the
    # threshold no longer promises otherwise. threshold_verdict() carries
    # the promise it can keep.
    print(f"separated : {report['separated']} (worst answerable "
          f"{report['worst_answerable_top_score']:.4f}, best unanswerable "
          f"{report['best_unanswerable_top_score']:.4f})")
    in_band, why = threshold_verdict(rows)
    print(f"threshold : {why}")
    if not in_band:
        print(f"FAIL: {why}")
        failures.append("threshold")

    # 6. Refusal works in both directions: silence on nonsense, hits on real.
    if retrieve("what is the capital of France"):
        print("FAIL: answered a question the corpus cannot answer")
        failures.append("refusal")
    if not retrieve("print jobs are piling up and nothing comes out"):
        print("FAIL: refused a question the corpus covers")
        failures.append("over-refusal")

    if failures:
        print(f"FAIL: {', '.join(failures)}")
        sys.exit(1)
    print("PASS: retrieval measured, and honest about what it does not know")
