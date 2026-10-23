#!/usr/bin/env python3
"""Ask a lab server a question and check the answer - the Command Center's `validate` job. P06 step 2.

    python src/validate.py --server dc01
    python src/validate.py --server srv01 --expect "Logon=Success and Failure" --expect "Account Lockout=Failure"

Project 1 built the read: a non-admin account (CORP\\svc-agent-ro) reaching a JEA endpoint that
offers a fixed, reviewed list of functions, with the client refusing to run if the endpoint
offers anything else (the drift check, ADR 0003). It never had a command an operator could ask
a question with - host_client.py's __main__ is a connection proof. This is that command.

WHAT IT ADDS, AND WHAT IT DOESN'T (ADR 0041: an adapter says what it adds)
-------------------------------------------------------------------------
Adds: a server NAME instead of an address, every row printed, and --expect - the operator's
statement of what the answer should be, checked by code (Jose's pick, step 2). Adds nothing on
the wire: the same Host, the same endpoint, the same drift check, the same one function
(Get-AgentAuditPolicy). No model anywhere - a question with a fixed answer needs a comparison,
not a reader.

READ-ONLY BY CONSTRUCTION
-------------------------
The account cannot write, the endpoint offers no write function, and this file calls exactly
one function by name. The Command Center's table marks the job read-only, so a killed run maps
to 2 - nothing can have changed.

EXIT CODES
----------
  0  read, and every --expect matched (or none was given - then it is a plain read)
  1  read, and at least one --expect did not match; each named with what the server said
  2  no answer: bad arguments, no session, endpoint drift, the call failed, or an --expect
     names a subcategory the server does not report (a question that could not be asked is
     not a "no")

The password is asked for at the terminal and held only in memory (ADR 0002). The record
(evals/runs/*-validate-<server>.json) holds every row read and every check made.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

RUNS = REPO_ROOT / "evals" / "runs"
# Addresses, not names: WSL resolves through Windows, not DC01 (host_client.py).
SERVERS = {"dc01": "10.10.10.10", "srv01": "10.10.10.20"}
READER = "CORP\\svc-agent-ro"
CMD = "Get-AgentAuditPolicy"
EXIT_OK, EXIT_MISMATCH, EXIT_NO_ANSWER = 0, 1, 2


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_expect(raw: str) -> tuple[str, str]:
    name, sep, value = raw.partition("=")
    if not sep or not name.strip() or not value.strip():
        raise argparse.ArgumentTypeError(f"--expect needs SUBCATEGORY=SETTING, got {raw!r}")
    return name.strip(), value.strip()


def compare(rows: list[dict], expects: list[tuple[str, str]]) -> tuple[list[dict], list[str]]:
    """Each expectation against the rows. Returns (checks, unknown subcategory names).
    Names match case-insensitively (an operator typing 'logon' means Logon); settings match
    exactly after trimming - 'Success' is not 'Success and Failure'."""
    by_name = {str(r.get("Subcategory", "")).strip().lower(): r for r in rows}
    checks, unknown = [], []
    for name, want in expects:
        row = by_name.get(name.lower())
        if row is None:
            unknown.append(name)
            continue
        got = str(row.get("Setting", "")).strip()
        checks.append({"subcategory": row.get("Subcategory"), "expected": want, "read": got,
                       "match": got == want})
    return checks, unknown


def main(argv: list[str] | None = None, *, open_host: Callable | None = None,
         password: Callable[[str], str] | None = None, runs: Path | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read a lab server's audit policy through Project 1's "
                                             "read-only JEA endpoint and check it.")
    ap.add_argument("--server", required=True, choices=sorted(SERVERS))
    ap.add_argument("--expect", action="append", type=parse_expect, default=[],
                    metavar="SUBCATEGORY=SETTING")
    args = ap.parse_args(argv)                    # every usage error exits 2 before a prompt

    import host_client as H                       # after parsing: pypsrp is not needed to say "usage"
    address = SERVERS[args.server]
    # svc-agent-ro sees one function on DC01 and three on SRV01 (P05 step 7's verify role).
    # The drift check refuses unless the endpoint offers EXACTLY this list.
    allowed = H.ALLOWED_SRV01 if args.server == "srv01" else H.ALLOWED
    open_host = open_host or (lambda pw: H.Host(address, READER, pw, allowed=allowed))
    password = password or getpass.getpass
    runs = runs or RUNS
    record = {"kind": "validate", "schema": 1, "server": args.server, "address": address,
              "account": READER, "command": CMD, "started": _now(),
              "expect": [{"subcategory": n, "setting": v} for n, v in args.expect],
              "rows": None, "checks": None, "exit_code": None}

    try:
        pw = password(f"Password for {READER}: ")
        with open_host(pw) as host:
            visible = host.check_drift()
            result = host.call(CMD)
            record.update(endpoint=getattr(host, "configuration_name", None),
                          visible=list(visible), rows=[dict(r) for r in result.rows],
                          call_seconds=round(result.seconds, 3))
    except (H.HostError, EOFError) as exc:       # EOFError: no terminal to ask a password on
        print(f"validate: NO ANSWER from {args.server} ({address}): {exc}", file=sys.stderr)
        record.update(error=str(exc)[:500], exit_code=EXIT_NO_ANSWER)
        _save(record, runs)
        return EXIT_NO_ANSWER

    rows = record["rows"]
    print(f"=== {args.server} ({address}) via {record['endpoint']} as {READER}: "
          f"{len(rows)} subcategories")
    checks, unknown = compare(rows, args.expect)
    record["checks"] = checks
    if not args.expect:
        for r in rows:
            print(f"  {str(r.get('Subcategory', '?')):<42} {r.get('Setting', '?')}")
    for c in checks:
        mark = "ok  " if c["match"] else "FAIL"
        print(f"  {mark} {c['subcategory']}: read {c['read']!r}, expected {c['expected']!r}")
    if unknown:
        known = sorted(str(r.get("Subcategory")) for r in rows)
        print(f"validate: {args.server} reports no subcategory named {unknown} - the question "
              f"cannot be asked. It reports {len(known)}: {', '.join(known)}", file=sys.stderr)
        code = EXIT_NO_ANSWER
    elif any(not c["match"] for c in checks):
        code = EXIT_MISMATCH
    else:
        code = EXIT_OK
    record["exit_code"] = code
    _save(record, runs)
    return code


def _save(record: dict, runs: Path) -> None:
    record["ended"] = _now()
    try:
        runs.mkdir(parents=True, exist_ok=True)
        out = runs / f"{record['started'].replace('-', '').replace(':', '')}-validate-{record['server']}.json"
        out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"  record  {out}")
    except OSError as exc:
        print(f"validate: WARNING - the record could not be written ({exc})", file=sys.stderr)


if __name__ == "__main__":
    from entry import run_guarded       # a crash exits 2, never a finding's 1
    run_guarded(main, "validate")
