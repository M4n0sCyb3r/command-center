# SOP — Closing out a lab session

**Slug:** `lab-session-closeout`
**Scenario type:** Repo & release hygiene
**Source:** cross-cutting — written from Labs 0–5 and the field-notes build
**First written:** 2026-09-09

---

## Purpose

Take a finished piece of lab work from "it runs on my machine" to "it is verified,
committed, documented, and on the remote," so that deleting the chat that produced
it loses nothing.

## Scope and non-scope

**In scope.** The procedure that runs at the end of every working session in this
repo: verify, commit, write the review card, rebuild the offline PDF, update the
artifact, record the position (step 9), push, and confirm the push landed.

**Not in scope.** How to build any particular lab. The teaching content lives in
`notes/`; this document is only the release procedure around it.

**Explicitly not covered.** Anything touching employer systems, data, credentials
or networks. This repo is portfolio material and contains generic placeholder
values only.

## Preconditions

| Requirement | Value |
|---|---|
| Shell | WSL2 Ubuntu on <host>. PowerShell commands stay in PowerShell. |
| Working directory | `<repo>` — confirm with `pwd`, do not assume |
| Virtual environment | `.venv` activated. Not `ansible-venv`; that belongs to the QE lab. |
| Model service | Ollama reachable on the Windows host (`OLLAMA_HOST=0.0.0.0`, firewall rule scoped to `172.16.0.0/12`) |
| Git remote | `origin` → the private GitHub repo, tracking `main` |
| Authority to push | The human. The assistant's device shell has **no** network route to github.com. |

## Safety

No step in this procedure acts on a remote host, and no model output is executed.
The blast radius is this repository.

Two structural gates are deliberate and should not be removed:

- **The push is a human action.** The assistant commits locally; the proxy returns
  403 for github.com from its shell. Treat that as the approval gate, not a defect —
  nothing reaches the remote without a person running the command.
- **The assistant cannot delete files in this repo.** File-deletion permission was
  declined on purpose. The cost is stale `.git/*.lock` files after every
  assistant-run git command; the benefit is that no automated step can remove work.

Rollback: every step before the push is local. `git reset --soft HEAD~1` undoes a
commit while keeping the changes staged. After the push, prefer a forward-fixing
commit over a force-push — the history is portfolio evidence.

## Procedure

### 1. Clear the locks before touching git

The assistant's shell leaves `.git/index.lock`, `.git/HEAD.lock` and
`tmp_obj_*` behind because it cannot delete them. The *next* git command fails
if they are still there.

```bash
cd <repo>
rm -f .git/*.lock .git/objects/*/tmp_obj_*
```

Expected: no output. Silence is success.

### 2. Confirm what is actually in the working tree

```bash
git status --short
```

Expected: only the files this session was supposed to change. An unexpected
modified file is a question to answer *before* committing, not after.

### 3. Verify the artifact against its own check

Never commit on the assumption that it works. Run the smoke test and let its
exit code decide.

```bash
source .venv/bin/activate
python src/<module>.py; echo "exit: $?"
```

Expected: a `PASS:` line and `exit: 0`. A test that prints PASS but exits
non-zero is a broken test, not a passing run.

### 4. Sweep for duplicate definitions

A passing test does not prove the file is what you think it is. Python lets a
second `def` of the same name silently replace the first, so a heredoc appended
twice leaves half the file dead and every test still green.

```bash
for f in src/*.py; do
  dups=$(grep -oP '^(def|class)\s+\K\w+' "$f" | sort | uniq -d)
  [ -n "$dups" ] && echo "$f -> DUPLICATES: $dups" || echo "$f -> clean"
done
```

Expected: every file reports `clean`.

### 5. Commit, gated on the test

Chain the commit behind the test so a failure stops before anything is recorded.
The `&&` is the whole point — it is the same exit-code-as-contract used by the QE
reporter.

```bash
rm -f .git/*.lock .git/objects/*/tmp_obj_* && \
source .venv/bin/activate && \
python src/<module>.py && \
git add -A && \
git commit -F - <<'MSG'
Lab NN: <what shipped, in one line>

<what it does, what decisions were made, what broke>
MSG
```

Expected: `[main <sha>] Lab NN: ...` and a file-count summary.

### 6. Write the review card

`notes/lab-NN-<slug>.md`, using the eight fixed sections: *In one line / Why this
lab exists / What you built / Words you now own / Skills exercised / Commands
worth keeping / The one thing to remember / The trap.*

Add any new terms to `notes/GLOSSARY.md` and a row to `notes/README.md`.

The rule, stated plainly: **if the card does not exist, the lab is not finished.**
`INDEX.md` records what shipped and `LOG.md` records what happened; the card is
the only artifact written to be re-read later, offline, away from a machine.

### 7. Rebuild the offline PDF

Built in the assistant's cloud container, not in WSL — WSL has no pandoc. The
markdown cards are the source of truth; the PDF is regenerated from them.

```bash
./notes/build-pdf.sh notes notes/Agent-Lab-Field-Notes.pdf
```

Expected: a PDF whose page count has grown by roughly the length of the new card,
and whose title line contains real dashes rather than `??`.

### 8. Update the artifact in place

Read the **live** artifact first and patch that. Never publish from a local saved
copy — see failure modes.

### 9. Record the position — one fact, one home

| Fact | Home | When |
|---|---|---|
| A lab is complete | `INDEX.md` lab row (status + artifact) | lab sessions |
| Where a project stands | `projects/NN-slug/README.md` § *Build order* — that step's row | every project session |
| A project's one-line status | `INDEX.md` project row | **only** when it changes phase (started, step count, complete) |
| The proof behind a step | `projects/NN-slug/evidence/` | when a step ships |
| What happened | `LOG.md` | every session |

Never copy a position into a second file — every copy is a file that goes stale (the
handoff doc and the INDEX project cells both did; consolidated 2026-10-07).

`LOG.md` gets a new entry at the **top** with four parts: *Shipped / Also shipped /
Broke / Next.* Write "Broke: nothing" honestly when nothing did; the value of the
section is that it is trusted.

### 10. Push, and confirm it landed

```bash
rm -f .git/*.lock .git/objects/*/tmp_obj_* && \
git push && \
git log --oneline -1 main origin/main
```

Expected: the push summary, then a single line showing `HEAD -> main, origin/main`
on the **same** commit. Two different lines means the push did not land.

## Verification

The session is closed out when all seven are true:

1. The artifact exists and its own check passes with exit 0 — observed, not assumed.
2. A commit exists naming the lab.
3. The `notes/` card is written and the glossary and README rows are added.
4. The PDF is rebuilt and committed.
5. The artifact is updated at its existing URL.
6. The position is recorded once, in its home (step 9), and `LOG.md` has the entry.
7. `git log --oneline -1 main origin/main` prints **one** line covering both refs.

Until all seven hold, the chat that produced the work is still the only copy of
something, and must not be deleted.

## Failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| `fatal: Unable to create '.git/index.lock': File exists` | A previous assistant-run git command left a lock; its shell cannot delete files | `rm -f .git/*.lock .git/objects/*/tmp_obj_*` and retry. Run it *before* every git block, not after the failure. |
| Test passes, but the module contains dead code | A heredoc was appended twice; the second `def` silently shadowed the first | Run the duplicate sweep in step 4. Confirmed real on 2026-09-09 in `src/agent.py`. |
| `LOG.md` claims something shipped that is not in the file | A log entry is a *claim*, written from intent rather than from the file | Diff the claim against the artifact before trusting it. Confirmed real on 2026-09-08 — a 16-module expansion was logged and never landed in `INDEX.md`. |
| PDF title renders as `Agent Lab Field Notes ?? Labs 0???3` | wkhtmltopdf was not told the input is UTF-8 | All three: `--encoding utf-8`, `<meta charset="utf-8">` in the generated HTML, and a plain-ASCII `--metadata title`. |
| PDF build aborts entirely on a header or footer flag | The container's wkhtmltopdf is built against unpatched Qt and fails rather than ignoring the flag | Pass no `--header-*` / `--footer-*` flags at all. |
| A PDF rebuild silently restyles the whole document | The build was ad hoc and uncommitted, so each rebuild reflected whoever ran it | `notes/build-pdf.sh` is committed. Take the palette from the artifact, never from taste. |
| Republishing the artifact deletes sections that were there | Published from a stale local snapshot instead of the live page | Read the live artifact first and patch *that*. `Claude outputs/field-notes.html` is a snapshot and will be behind. |
| Git commands run against the wrong directory | A WSL path (`/mnt/c/...`) pasted into PowerShell fails silently as a `cd`, leaving the shell wherever it was | Keep Linux commands in WSL and PowerShell commands in PowerShell. `pwd` before the first git command. |
| `ModuleNotFoundError` on a package known to be installed | A fresh WSL shell drops both the directory and the venv; or the wrong venv is active | `cd` and `source .venv/bin/activate` in the same chain as the command. Two projects, two environments, deliberately. |
| Push fails with a proxy 403 | The assistant's device shell has no network route to github.com | Expected. The push is the human's step; do not work around it. |
| `bash: syntax error near unexpected token 'newline'` | A placeholder or stray tag was pasted and executed literally | Read the block before running it. The heredoc before the error has usually already run — check the file before re-running. |
| A file that should ship is missing from the repo | `.gitignore` matched it. A rule like `.env.*` swallows `.env.example`; excluding a *directory* (`data/`) stops git descending, so a later `!data/README.md` negation never fires | Negate the specific file (`!.env.example`), and exclude directory *contents* (`data/*`) rather than the directory. |

## References

- `START-HERE.md` — session entry point and the end-of-session checklist
- `INDEX.md` — what shipped; `LOG.md` — what happened
- `notes/README.md` — the card format; `notes/build-pdf.sh` — the PDF pipeline and its three gotchas
- `docs/reading-the-repo.md` — reading this repo without the assistant
- `docs/windows-qe-lab-handoff.md` — the exit-code contract this SOP's `&&` gating borrows from
