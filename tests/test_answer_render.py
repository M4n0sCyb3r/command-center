"""P02 build step 6b: what a person reads - citations, authorship, warning, credit.

Decided in ADR 0019 (authorship is a field set by code; warn when a Microsoft
page is cited without the runbook it competes with - reported, not gated),
ADR 0020 (a citation is its manifest path) and ADR 0021 (a Microsoft citation
carries its upstream reference; any answer citing Microsoft carries ONE CC BY
attribution line).

  case 1  ours only: the label says ours, no upstream, no attribution, no warning
  case 2  Microsoft cited WITH its target: upstream ref + one attribution, no warning
  case 3  Microsoft cited WITHOUT its target: the warning names both paths
  case 4  two Microsoft chunks of one page: still ONE attribution line, ONE warning
  case 5  the model's prose cannot become a citation - a path written in the
          answer text is not in the citations
  case 6  refusals render as refusals: no Sources, no attribution
  case 7  index and manifest disagree on authorship: refused, never rendered
  case 8  a cited source missing from the manifest: refused, never rendered

No model, no index: chunks and the manifest are literals. CI home.
Run:  python tests/test_answer_render.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import answer as A                                   # noqa: E402
from manifest import Entry, Manifest, Upstream       # noqa: E402
from retrieval import Chunk                          # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


OURS = "data/runbooks/service-restart.md"
MS = "data/microsoft/sc-query.md"
ATTR = "Includes material from Microsoft ... licensed under CC BY 4.0."
MANIFEST = Manifest(
    upstream=Upstream("MicrosoftDocs/windowsserverdocs", "824bd98", "CC BY 4.0", ATTR),
    decoy_rule="rule",
    entries=(Entry(OURS, "ours"),
             Entry(MS, "microsoft", "competitor",
                   "WindowsServerDocs/administration/windows-commands/sc-query.md", OURS)),
    root=REPO_ROOT,
)


def chunk(path: str, slug: str, authorship: str) -> Chunk:
    return Chunk(f"{path}#{slug}", path, "Title", slug.capitalize(), "body", authorship)


C_OURS = chunk(OURS, "procedure", "ours")
C_MS1 = chunk(MS, "syntax", "microsoft")
C_MS2 = chunk(MS, "examples", "microsoft")


def answered(*chunks: Chunk, text: str = "Restart it.") -> A.Answer:
    ids = [c.chunk_id for c in chunks]
    return A.Answer("q", A.ANSWERED, text, ids, 1, [(i, 0.6) for i in ids], [],
                    {c.chunk_id: c for c in chunks})


print("case 1  ours only")
r = A.render(answered(C_OURS), MANIFEST)
check("one citation, authorship field = ours", [c.authorship for c in r.citations] == ["ours"])
check("label says ours", "[ours]" in str(r))
check("no upstream, no attribution, no warning",
      r.citations[0].upstream is None and r.attribution is None and r.warnings == ())

print("case 2  Microsoft with its target")
r = A.render(answered(C_OURS, C_MS1), MANIFEST)
ms = [c for c in r.citations if c.authorship == "microsoft"][0]
check("upstream = repo@commit:path",
      ms.upstream == "MicrosoftDocs/windowsserverdocs@824bd98:WindowsServerDocs/administration/windows-commands/sc-query.md",
      str(ms.upstream))
check("attribution recorded as a field", r.attribution == ATTR)
check("attribution printed exactly once", str(r).count(ATTR) == 1)
check("no warning - ours was cited too", r.warnings == ())

print("case 3  Microsoft without its target")
r = A.render(answered(C_MS1), MANIFEST)
check("one warning", len(r.warnings) == 1)
check("names the page and the passed-over runbook", MS in r.warnings[0] and OURS in r.warnings[0], r.warnings[0] if r.warnings else "")
check("still rendered - reported, not gated", r.outcome == A.ANSWERED and len(r.citations) == 1)

print("case 4  two chunks of one Microsoft page")
r = A.render(answered(C_MS1, C_MS2), MANIFEST)
check("one attribution line", str(r).count(ATTR) == 1)
check("one warning, not two", len(r.warnings) == 1)

print("case 5  the model's prose is not a citation")
r = A.render(answered(C_OURS, text=f"See {MS} and data/runbooks/fake.md"), MANIFEST)
check("citations come only from cited ids", [c.path for c in r.citations] == [OURS])
check("a Microsoft path in the prose earns no attribution", r.attribution is None)

print("case 6  refusals")
for outcome in (A.REFUSED_NO_RETRIEVAL, A.REFUSED_NOT_IN_SOURCES, A.REFUSED_CITATION):
    r = A.render(A.Answer("q", outcome, "", [], 1, []), MANIFEST)
    check(f"{outcome}: says no answer, no Sources",
          str(r).startswith("No answer:") and "Sources:" not in str(r) and r.attribution is None)

print("case 7  authorship disagreement")
liar = Chunk(f"{MS}#syntax", MS, "Title", "Syntax", "body", "ours")
try:
    A.render(answered(liar), MANIFEST)
    check("ValueError raised", False, "rendered instead")
except ValueError as exc:
    check("ValueError raised", True, str(exc)[:60])

print("case 8  source missing from the manifest")
stray = chunk("data/runbooks/not-in-manifest.md", "x", "ours")
try:
    A.render(answered(stray), MANIFEST)
    check("KeyError raised", False, "rendered instead")
except KeyError:
    check("KeyError raised", True)

print()
if failures:
    print(f"FAIL: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PASS: rendering - authorship from the manifest, one credit line, the warning, refusals")
