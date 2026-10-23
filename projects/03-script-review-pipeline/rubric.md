# Review rubric — Project 3 (ADR 0030)

You are reviewing ONE PowerShell script. Report what is wrong with it as findings. Each finding
names the lines it is about, one category, and a severity. Report at most 5 findings — the most
important first. If nothing blocks, say so: an empty list with verdict `approve` is a valid review.

## Categories (pick exactly one per finding)

- **correctness** — the script does not do what its synopsis says: a wrong comparison, an inverted
  condition, the wrong property or path, a value computed and then ignored.
- **error-handling** — a failure is hidden or reported as success: a swallowed exception, a missing
  `-ErrorAction Stop` that stops a `catch` from firing, an exit code that says 0 after a failure.
- **blast-radius** — the script can change or destroy more than its task needs: a wider filter, a
  recursive delete, a protection removed, a read-only check that changes the machine.
- **security** — credentials or code execution are handled unsafely: plaintext passwords, building
  a command line from input, running with more privilege than the task needs.
- **style** — readability or convention only; never a reason to block.

## Severity

- **blocking** — the script should not be run as written. Every finding in the first four
  categories that would change what happens on a machine is blocking.
- **advisory** — worth fixing, but the script is safe and correct to run.

## Verdict

`revise` if and only if at least one finding is blocking; otherwise `approve`.

## Wording

`why` is ONE sentence saying what goes wrong (at most 300 characters). `fix` is the change, briefly
(at most 300 characters). No qualifications, no restating — the line numbers carry the location.

## Lines

`line_start` and `line_end` are 1-based line numbers in the script as given, inclusive. Point at the
line where the defect IS — the comparison that is wrong, the call that is unsafe — not at a line
that merely shows a symptom of it.
