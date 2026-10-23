"""The corpus manifest: the one file that decides what the index may know.

Project 2, build step 1. ADR 0011 ruled out a directory walk - a file dropped
into a folder must not become something the assistant answers from. ADR 0015
made the list DATA (JSONL + a header row, schema-checked) rather than code, so
loading it can never execute anything. ADR 0016 set the fields and the checks
below.

Two layers of checking, on purpose:

  1. The JSON Schema (data/corpus.schema.json) - SHAPE. Which fields each kind
     of row may and must carry. Declarative, readable by a non-programmer.
  2. This file - FACTS the schema cannot see: does the path exist, is it inside
     the repo, does `targets` name one of OUR rows, does the decoy's keyword
     really appear in both titles.

Every failure raises ValueError naming the file and the LINE. A manifest error
is found by the person reading the diff that caused it; a line number is what
gets them there.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "data" / "corpus.jsonl"
SCHEMA_PATH = REPO_ROOT / "data" / "corpus.schema.json"


@dataclass(frozen=True)
class Upstream:
    """Where every vendored Microsoft page came from - stated once, in the header."""

    repo: str
    commit: str
    licence: str
    attribution: str


@dataclass(frozen=True)
class Entry:
    """One document the index is allowed to know, and who wrote it."""

    path: str                          # repo-relative - also the citation key (ADR 0020)
    authorship: str                    # "ours" | "microsoft"
    role: str | None = None            # microsoft only: "competitor" | "decoy"
    upstream_path: str | None = None   # microsoft only
    targets: str | None = None         # microsoft only: the `ours` path it contends with
    shared_keyword: str | None = None  # decoys only
    sha256: str | None = None          # microsoft only: content hash, verified at load (ADR 0027 amendment 4)


@dataclass(frozen=True)
class Manifest:
    upstream: Upstream
    decoy_rule: str
    entries: tuple[Entry, ...]
    root: Path

    def file(self, entry: Entry) -> Path:
        return self.root / entry.path


def _validators(schema_path: Path) -> tuple[Draft202012Validator, Draft202012Validator]:
    """One validator for the header, one for entry rows, both from the one file."""
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)   # a broken schema is a tooling error, not a pass
    defs = schema["$defs"]
    header = Draft202012Validator({"$defs": defs, "$ref": "#/$defs/header"})
    entry = Draft202012Validator({"$defs": defs, "$ref": "#/$defs/entry"})
    return header, entry


def strip_front_matter(raw: str, *, name: str) -> str:
    """Remove a leading '---' ... '---' block (ADR 0017).

    Front matter is metadata for a publishing system (ms.author, ms.date), and
    its `title` repeats the H1 - none of it answers a question. Only a block
    that STARTS the file counts; a '---' further down is a horizontal rule.
    An opening '---' with no closing one is refused: guessing where metadata
    ends would index half of it as prose.
    """
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return raw
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[i + 1:])
    raise ValueError(f"{name}: front matter opened with '---' and never closed")


def _title(path: Path) -> str | None:
    """The document's '# ' line - what the decoy rule's keyword is checked against.

    Read AFTER front matter is stripped (P02 step 5). Microsoft front matter holds YAML
    comments that start '# ' ("# customer intent: ..."); read raw, the first of those
    would be taken as the title and the decoy check would test a comment.
    """
    text = strip_front_matter(path.read_text(encoding="utf-8"), name=path.name)
    match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def content_sha256(path: Path) -> str:
    """sha256 of a document's CONTENT - CRLF folded to LF, so a checkout with other line
    endings cannot fail the check over a byte that carries no content (ADR 0027 amendment 4)."""
    import hashlib
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_manifest(path: Path = MANIFEST_PATH, *, root: Path = REPO_ROOT,
                  schema_path: Path = SCHEMA_PATH) -> Manifest:
    """Read, validate and return the manifest. Raises ValueError on anything wrong."""
    path = Path(path)
    root = Path(root).resolve()

    def fail(lineno: int, message: str) -> None:
        raise ValueError(f"{path.name} line {lineno}: {message}")

    header_v, entry_v = _validators(schema_path)

    lines = [(n, raw) for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if raw.strip()]
    if not lines:
        raise ValueError(f"{path.name} is empty - no header, no documents")

    rows: list[tuple[int, dict]] = []
    for lineno, raw in lines:
        try:
            rows.append((lineno, json.loads(raw)))
        except json.JSONDecodeError as exc:
            fail(lineno, f"not valid JSON ({exc.msg})")

    # --- layer 1: shape --------------------------------------------------
    lineno, header = rows[0]
    error = best_match(header_v.iter_errors(header))
    if error is not None:
        fail(lineno, f"header: {error.message}")

    entries: list[tuple[int, Entry]] = []
    for lineno, row in rows[1:]:
        error = best_match(entry_v.iter_errors(row))
        if error is not None:
            fail(lineno, error.message)
        entries.append((lineno, Entry(**row)))

    if not entries:
        raise ValueError(f"{path.name} lists no documents - an empty corpus answers nothing")

    # --- layer 2: facts the schema cannot see -----------------------------
    seen: dict[str, int] = {}
    for lineno, entry in entries:
        if entry.path in seen:
            fail(lineno, f"{entry.path} already listed on line {seen[entry.path]}")
        seen[entry.path] = lineno

        file = (root / entry.path).resolve()
        if not file.is_relative_to(root):          # belt and braces over the schema's '..' rule
            fail(lineno, f"{entry.path} resolves outside the repo")
        if not file.is_file():
            fail(lineno, f"{entry.path} does not exist")

    # AMENDMENT 4 of ADR 0027 (2026-10-16): the manifest pinned the upstream COMMIT but no
    # content, so an edited vendored page reached the index silently - and P02 step 7a-ii
    # measured what that buys an attacker: a tampered default (1800 -> 86400 seconds),
    # repeated by the answer step, cited and confident. Every Microsoft page now carries the
    # hash of the content that was vendored; a changed page is refused HERE, before any chunk
    # is cut from it, so it can reach neither the index nor the model. A legitimate re-vendor
    # changes the hashes in a reviewed commit (tools/rehash_vendored.py) whose diff shows the
    # sentences that changed upstream. Limit: if the upstream page itself is poisoned, the
    # hash proves only that we hold what was published - the diff review is the defense.
    for lineno, entry in entries:
        if entry.sha256 is not None:
            actual = content_sha256(root / entry.path)
            if actual != entry.sha256:
                fail(lineno, f"{entry.path} does not match its manifest sha256 "
                             f"({actual[:12]}... on disk, {entry.sha256[:12]}... pinned) - a vendored page "
                             "changed; re-vendor in a reviewed commit (tools/rehash_vendored.py)")

    ours = {e.path for _, e in entries if e.authorship == "ours"}
    for lineno, entry in entries:
        if entry.targets is None:
            continue
        # A renamed runbook must BREAK the build, not silently orphan its competitors.
        if entry.targets not in ours:
            fail(lineno, f"targets {entry.targets}, which is not an `ours` entry in this manifest")
        if entry.shared_keyword is not None:
            # The decoy rule, mechanically: the word must be in BOTH titles.
            # Whether the page answers a different question stays a human call.
            word = entry.shared_keyword.lower()
            for side in (entry.path, entry.targets):
                title = _title(root / side) or ""
                if word not in title.lower():
                    fail(lineno, f"shared_keyword {entry.shared_keyword!r} is not in the title "
                                 f"of {side} ({title!r})")

    up = header["upstream"]
    return Manifest(
        upstream=Upstream(up["repo"], up["commit"], up["licence"], up["attribution"]),
        decoy_rule=header["decoy_rule"],
        entries=tuple(e for _, e in entries),
        root=root,
    )
