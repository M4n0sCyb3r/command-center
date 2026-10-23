"""P02 build step 4 check (4a + 4b): the splitter handles a second document shape (ADR 0017).

WHAT THIS PROVES
----------------
Each fix on a small fixture built to need it. On today's corpus all three fixes
change NOTHING (proved separately: old vs new splitter, 44 chunks, every field
identical) - which is exactly why that proof cannot show a fix WORKS. A fix
that did nothing would pass it too. These fixtures are the positive controls.

1. Front matter is stripped; the H1 is still the title; '---' later in a file
   is a horizontal rule, not metadata; an unclosed block is refused.
2. Inside a code fence, '# ' and '## ' lines are body - not a title, not a cut
   line, not dropped. An unclosed fence is refused.
3. Microsoft link-list sections are dropped, case-insensitively - and ONLY for
   authorship "microsoft": the same file as "ours" keeps them (the control
   that shows the drop is keyed on the manifest, never on the file).
4. A '## ' before any '# Title' is refused.
5. (step 4b) Lead text becomes an "Overview" chunk: an SOP-shaped fixture gets
   one, a runbook-shaped one (no lead) gets none, text above the H1 is not in
   it, and front matter never leaks into it - the case where 1's checks bite.

NEEDS NO MODEL AND NO NETWORK - file reading only, so it runs in the CI home.

Run:  python tests/test_splitter.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import retrieval                      # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def split(text: str, authorship: str = "ours"):
    path = Path(tempfile.mkdtemp()) / "doc.md"
    path.write_text(text, encoding="utf-8")
    return retrieval.split_document(path, source="docs/doc.md", authorship=authorship)


def refused(text: str, expect_in: str) -> tuple[bool, str]:
    try:
        split(text)
    except ValueError as exc:
        return expect_in in str(exc), str(exc)
    return False, "accepted"


print("1. front matter")
chunks = split("---\ntitle: Publishing title\nms.author: someone\nms.date: 01/01/2026\n---\n"
               "# Real Title\n\n## Use\nRun it.\n")
check("one chunk", len(chunks) == 1, f"got {len(chunks)}")
check("title is the H1, not front matter", chunks[0].title == "Real Title", chunks[0].title)
check("no front matter in any text", all("ms.author" not in c.embed_text() for c in chunks))
chunks = split("# T\n\n## A\nabove\n---\nbelow\n")
check("a later '---' is body (horizontal rule)", "---" in chunks[0].text and "below" in chunks[0].text)
ok, msg = refused("---\ntitle: x\n# T\n\n## A\nbody\n", "never closed")
check("unclosed front matter refused", ok, msg)

print("2. code fences")
chunks = split("# T\n\n## Steps\n```powershell\n# Stop the service first\nStop-Service Fax\n"
               "## not a heading\n```\nDone.\n")
check("one chunk (fenced '## ' is not a cut line)", len(chunks) == 1, f"got {len(chunks)}")
check("heading is Steps", chunks[0].heading == "Steps", chunks[0].heading)
check("fenced '# ' comment kept in body", "# Stop the service first" in chunks[0].text)
check("fenced '## ' line kept in body", "## not a heading" in chunks[0].text)
check("title untouched by fenced '# '", chunks[0].title == "T", chunks[0].title)
chunks = split("# T\n\n## A\n~~~\n# tilde fence\n~~~\n")
check("tilde fence also honoured", "# tilde fence" in chunks[0].text)
ok, msg = refused("# T\n\n## A\n```\nnever closed\n", "never closed")
check("unclosed fence refused", ok, msg)

print("3. Microsoft link-list sections")
page = ("# sc query\n\n## Syntax\nsc query [<servername>]\n\n"
        "## See also\n- [Command-Line Syntax Key](x.md)\n\n"
        "## Related Links\n- [sc config](y.md)\n")
ms = [c.heading for c in split(page, authorship="microsoft")]
check("microsoft: link lists dropped, answer kept", ms == ["Syntax"], str(ms))
ours = [c.heading for c in split(page, authorship="ours")]
check("CONTROL ours: same file keeps all three", ours == ["Syntax", "See also", "Related Links"],
      str(ours))
check("every ADR 0017 heading is in the drop list",
      {"related links", "related topics", "related content", "see also",
       "additional references"} == set(retrieval.MS_DROP_HEADINGS))

print("4. anchoring")
ok, msg = refused("## Orphan\nbody\n# Late Title\n", "before any '# Title'")
check("'## ' before the title refused", ok, msg)
ok, msg = refused("no headings at all\n", "no '# Title'")
check("no title refused", ok, msg)

print("5. the lead becomes an Overview chunk")
sop = split("# Bring the bench up\n\n**Scope:** starting a session.\n\n## Procedure\nStart Ollama.\n")
check("SOP-shaped: Overview first, then Procedure", [c.heading for c in sop] == ["Overview", "Procedure"],
      str([c.heading for c in sop]))
check("Overview id is <path>#overview", sop[0].chunk_id == "docs/doc.md#overview", sop[0].chunk_id)
check("Overview text is the lead", sop[0].text == "**Scope:** starting a session.", repr(sop[0].text))
runbook = split("# Restart a service\n\n## Procedure\nQuery it first.\n")
check("runbook-shaped (no lead): no Overview", [c.heading for c in runbook] == ["Procedure"],
      str([c.heading for c in runbook]))
pre = split("stray line above the title\n# T\nlead\n\n## A\nbody\n")
check("text above the H1 is not in the Overview", "stray" not in pre[0].text and pre[0].text == "lead",
      repr(pre[0].text))
fm = split("---\nms.author: someone\n---\n# T\n\nThe page's real lead.\n\n## A\nbody\n",
           authorship="microsoft")
check("front matter stripped; lead kept as Overview", fm[0].heading == "Overview"
      and fm[0].text == "The page's real lead." and "ms.author" not in fm[0].embed_text(),
      repr(fm[0].text))
whole = split("# W32Time\n\nThe whole page lives above any section.\n\n## Related Topics\n- [x](x.md)\n",
              authorship="microsoft")
check("MS page with everything in the lead: Overview kept, link list dropped",
      [c.heading for c in whole] == ["Overview"], str([c.heading for c in whole]))
lead_only = split("# T\n\nonly a lead, no sections\n")
check("a lead with no '##' at all still becomes a chunk", [c.heading for c in lead_only] == ["Overview"],
      str([c.heading for c in lead_only]))

print()
print("case 6  (step 5, ADR 0024) a section too long to embed is cut into parts - nothing lost")
para = "word " * 300                                   # ~1,500 chars per paragraph
long_md = "# T\n\n## Big\n" + "\n\n".join(f"p{i} {para}" for i in range(12)) + "\n\n## Small\nshort\n"
parts = split(long_md, "microsoft")
big = [c for c in parts if c.chunk_id.startswith("docs/doc.md#big")]
check("a short section keeps its plain id (no part mark)",
      [c.chunk_id for c in parts if "small" in c.chunk_id] == ["docs/doc.md#small"])
check("the long section becomes several parts", len(big) > 1, f"got {len(big)}")
check("part ids are #big~1..~n, in order",
      [c.chunk_id for c in big] == [f"docs/doc.md#big~{i}" for i in range(1, len(big) + 1)])
check("every part fits the part target", all(len(c.embed_text()) <= retrieval.PART_TARGET_CHARS for c in big),
      str([len(c.embed_text()) for c in big]))
check("headings say 'part i of n'", big[0].heading == f"Big (part 1 of {len(big)})", big[0].heading)
check("nothing lost: every paragraph lands in exactly one part, in order",
      [ln.strip() for c in big for ln in c.text.split("\n\n")] == [f"p{i} {para}".strip() for i in range(12)])
table = "# T\n\n## Table\n| a | b |\n|---|---|\n" + "\n".join(f"| row{i} | {'x' * 200} |" for i in range(80)) + "\n"
rows = split(table, "microsoft")
check("a giant table is cut at row ends - every row whole",
      all(ln.startswith("|") and ln.endswith("|") for c in rows for ln in c.text.splitlines()),
      str(len(rows)))
check("CONTROL: no section near the cap is cut - the runbook shape is untouched",
      [c.chunk_id for c in split("# T\n\n## A\n" + "y" * 5000 + "\n")] == ["docs/doc.md#a"])
ok, msg = refused("# T\n\n## Code\n```\n" + "z\n" * 4000 + "```\n", "will not be cut")
check("a code block too big to embed is refused, not cut", ok, msg)
ok, msg = refused("# T\n\n## Line\n" + "q" * 9000 + "\n", "will not be cut")
check("a single line too big to embed is refused, not cut", ok, msg)
src = (Path(__file__).resolve().parent.parent / "src" / "retrieval.py").read_text(encoding="utf-8")
check("embed() asks the server never to truncate", '"truncate": False' in src)

print()
if failures:
    print(f"FAIL: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PASS: splitter fixes hold on fixtures built to need them")
