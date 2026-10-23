"""Read-only drift detection on SRV01 (Project 5, build step 7; ADRs 0033, 0034).

The first time the agent's detection runs against the real machine. It opens ONE session -
AgentReader as svc-agent-ro, the identity that cannot write - checks that the endpoint offers
exactly SRV01's reviewed read list, reads the three watched items through the same
`SrvReader` the verifier will use, and compares them with the baseline using the same
`detect()` the fake target runs. Nothing here can change the machine: there is no remediator
in this file, and the session it opens has no write function to call.

EXIT CODES (the repo's 0/1/2 contract)
  0  SRV01 matches the baseline - no drift
  1  drift found; each item is named with what was read and what the baseline says
  2  the reading could not be established: no session, endpoint drift, a malformed reading

    python src/srv01_detect.py                 # password asked for, never stored
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import host_client as H                                 # noqa: E402
import remediate as R                                   # noqa: E402
import target as T                                      # noqa: E402


class _NoRemediator:
    """Target needs a second door. This one has no methods: detection never writes."""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    # An address, not a name: WSL resolves through Windows, not DC01 (host_client.py).
    ap.add_argument("--server", default="10.10.10.20")
    ap.add_argument("--user", default="CORP\\svc-agent-ro")
    args = ap.parse_args(argv)
    baseline = T.load_baseline()
    password = getpass.getpass(f"Password for {args.user}: ")
    try:
        with H.Host(args.server, args.user, password, allowed=H.ALLOWED_SRV01) as session:
            print(f"connected to {session.configuration_name} on {args.server} as {args.user} "
                  f"in {session.connect_seconds:.3f}s")
            session.check_drift()
            print(f"endpoint drift check passed: {', '.join(H.ALLOWED_SRV01)}")
            reader = T.SrvReader(session, baseline)
            for w in baseline:
                print(f"  read {w.id}: {reader.read(w.id)}")
            found = R.detect(T.Target(reader=reader, remediator=_NoRemediator(), kind="srv01"),
                             baseline)
    except (H.HostError, T.ReadError, R.TargetUnreachable, ValueError) as exc:
        print(f"COULD NOT ESTABLISH: {exc}")
        return 2
    if not found:
        print("no drift: SRV01 matches the baseline on all three items")
        return 0
    for d in found:
        print(f"DRIFT {d.item}: fields {d.fields} read {d.observed}, baseline {d.baseline}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
