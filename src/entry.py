"""The last-resort guard for a read-only job's entry point. P06, after build step 6.

Found by the step-6 cold reads: `validate`, `review` and `ask` had no last-resort catch, so an
unexpected exception exited with Python's default code **1** - the same code as `validate`'s
*mismatch* and `review`'s *revise*. A crash read as a finding. `promote` and `remediate` already
map a crash by their record (2 or 3); these three only read, so a crash can only ever mean
"no answer": **2**.

    if __name__ == "__main__":
        run_guarded(main, "validate")

What it does NOT catch - one limit, two choices:
  an import error    the guard runs after the module has loaded; a failure while importing
                     (a missing package, a syntax error) still exits Python's 1. Seen while
                     building this: answer.py on a shell with a broken jsonschema.
  SystemExit         argparse's 2, and every deliberate sys.exit(), pass through unchanged
  KeyboardInterrupt  Ctrl+C stays a signal; the Command Center records a killed read-only job
                     as 2 already (build step 1)
Jose's pick (2026-10-23): catch it in each entry point, crash = 2 - over mapping it in
command_center (it cannot tell a crash's 1 from a real 1 without guessing from stderr).
"""

from __future__ import annotations

import sys
import traceback
from typing import Callable

EXIT_CRASH = 2


def run_guarded(main: Callable[[], int], job: str) -> None:
    """Run `main()` and exit with its code; an unexpected exception exits EXIT_CRASH (2)."""
    try:
        code = main()
    except Exception as exc:                              # noqa: BLE001 - the last resort
        traceback.print_exc()
        print(f"{job}: CRASH - {type(exc).__name__}: {exc}. No answer: exit {EXIT_CRASH}, "
              f"not a finding.", file=sys.stderr)
        sys.exit(EXIT_CRASH)
    sys.exit(code)
