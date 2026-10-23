#!/usr/bin/env python3
"""P06 build step 4's BENCH check: the real bench's hands on SRV01, nothing the QE suite reads.

    python tools/p06_bench_check.py

What CI cannot prove (tests/test_srv01_bench.py fakes ansible-playbook) is proved here, once,
on the real SRV01 - and nothing here changes what the QE suite checks:

  1  marker round trip   write "bench-check:<time>" to C:\\ProgramData\\P06\\promote-marker.txt,
                         read it back equal (the restore proof depends on this file)
  2  analyzer verdicts   PowerShell's parser ON SRV01 over the samples in
                         projects/06-command-center/samples/: the clean one passes; the native
                         executable, the remote target and the missing SupportsShouldProcess
                         and a state-changing cmdlet with no -WhatIf are each refused FOR THAT REASON;
                         step 5's clean/bad pair both pass
  3  dry run             the clean sample through RealBench.dry_run: analyze then -WhatIf, ok
  4  QE                  the QE lab's SRV01 suite through RealBench.qe(): reporter exit 0, every
                         check PASS, every requested scope completed

No checkpoint, no restore, no live run: those are step 5's playbook. The vault password is
asked once (a pipe per call - tools/probe_vault_pipe.py).

Exit 0 iff all four hold; 1 if any verdict is wrong; 2 if the bench could not be reached.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import promote as PR                                   # noqa: E402
import srv01_bench as SB                               # noqa: E402

SAMPLES = REPO / "projects" / "06-command-center" / "samples"
EXPECT = {                                             # sample -> a word the refusal must contain
    "clean-spooler-disabled.ps1": None,
    "refuse-native-auditpol.ps1": "native executable",
    "refuse-remote-target.ps1": "another machine",
    "refuse-no-shouldprocess.ps1": "SupportsShouldProcess",
    "refuse-cmdlet-no-whatif.ps1": "cannot take -WhatIf",
    "promote-clean-applog.ps1": None,                  # step 5's pair: both must pass
    "promote-bad-syslog.ps1": None,
}


def main() -> int:
    b = SB.RealBench()
    bad: list[str] = []

    def say(ok: bool, label: str, detail: str = "") -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""), flush=True)
        if not ok:
            bad.append(label)

    try:
        print("1  marker round trip")
        v = f"bench-check:{time.strftime('%Y%m%dT%H%M%S')}"
        b._marker(v)
        got = b._marker()
        say(got == v, "written and read back equal", f"{got!r}")

        print("2  analyzer verdicts on SRV01")
        for name, word in EXPECT.items():
            res = b._action("analyze", **b._script_vars(SAMPLES / name))
            found = (res.get("output") or [{}])[0] or {}
            refusals = found.get("refusals") or []
            cmds = "; ".join(str(c) for c in found.get("commands") or [])
            if word is None:
                say(not refusals and not res.get("error"), f"{name}: passes", cmds or str(res)[:200])
            else:
                say(any(word in r for r in refusals), f"{name}: refused ({word})",
                    "; ".join(refusals)[:300] or str(res)[:200])

        print("3  dry run of the clean sample")
        r = b.dry_run(SAMPLES / "clean-spooler-disabled.ps1")
        say(r.ok, "analyze + -WhatIf ok", r.output.replace("\n", " | ")[:300])

        print("4  QE through the bench (the full SRV01 suite - a few minutes)")
        q = b.qe()
        fails = sorted(k for k, s in q.checks.items() if s != "pass")
        n_checks = sum(not k.startswith("scope:") for k in q.checks)
        n_scopes = sum(k.startswith("scope:") for k in q.checks)
        say(q.exit_code == 0 and not fails, f"reporter exit {q.exit_code}; {n_checks} checks, "
            f"{n_scopes} scopes", f"not passing: {fails}" if fails else "")
    except PR.Refused as exc:
        print(f"p06_bench_check: the clean sample was REFUSED: {exc}", file=sys.stderr)
        return 1
    except PR.BenchError as exc:
        print(f"p06_bench_check: bench unreachable or broken: {exc}", file=sys.stderr)
        return 2

    print()
    print("ALL HOLD" if not bad else f"{len(bad)} FAILED: {bad}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
