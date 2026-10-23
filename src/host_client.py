"""One door to a Windows host, for Project 1 - the Wintel Validation Agent.

Lab 1 gave the agent one door to the model: a single place that opens the
connection, sends one kind of thing, and raises one kind of error. This is the
same shape, pointed at a server instead of a model.

What it does NOT do, on purpose:

- it never sends script text. `call()` sends a NAMED command through PSRP, so
  "the model never writes PowerShell" (ADR 0002) holds at the protocol level -
  the endpoint runs in NoLanguage mode and would refuse script text anyway;
- it never takes the password from a file or an environment variable. The
  caller passes it in; `__main__` asks for it with getpass and keeps it in
  memory only (ADR 0002);
- it never trusts the endpoint to be what we think it is. `check_drift()`
  compares the commands the endpoint actually offers against ALLOWED below,
  and refuses on any difference (ADR 0003).

Transport is `pypsrp`, not `pywinrm`: only PSRP can open a session against a
NAMED configuration, which is what a JEA endpoint is (ADR 0003).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from pypsrp.powershell import PowerShell, RunspacePool
from pypsrp.wsman import WSMan

#: The JEA endpoint registered by build step 2.
ENDPOINT = "AgentReader"

#: WinRM over HTTP. NTLM encrypts the message body, which is the protection the
#: QE lab has been running on (ADR 0002). HTTPS listeners are out of scope for P1.
PORT = 5985

#: Our half of the drift check: the checks this code knows about. The endpoint's
#: role file is the other half. If the two disagree, something changed outside
#: the reviewed path, and the agent refuses to run.
ALLOWED: tuple[str, ...] = ("Get-AgentAuditPolicy",)

#: P05 build step 7: SRV01's AgentReader also maps the AgentReaderVerify role - the
#: verifier's two fixed reads. DC01's does not (Jose's pick, 2026-10-20: SRV01 only).
#: One expected list PER ENDPOINT, chosen by the caller: the drift check is only as
#: good as the list it compares against, and one list for two different surfaces would
#: fail on one host or stop checking the other.
ALLOWED_SRV01: tuple[str, ...] = ALLOWED + ("Get-AgentServiceState", "Get-AgentRegistryState")

#: P05 build step 8: the WRITE endpoint's list (AgentRemediator as svc-agent-rw, ADR 0034).
#: Its own list, checked the same way: a remediator that offers a fourth function is drift.
REMEDIATOR_ENDPOINT = "AgentRemediator"
ALLOWED_REMEDIATOR: tuple[str, ...] = (
    "Set-AgentAuditSetting", "Set-AgentRegistryValue", "Set-AgentServiceConfig")

#: What a parameter VALUE may be. Named parameters are how the remediator takes its fixed
#: values; a value is data, never code - no script block, no object, no nested structure.
#: (bool is a switch such as -Preview.) The endpoint's ValidateSet is the second check.
PARAM_TYPES: tuple[type, ...] = (str, int, bool)

#: Windows adds these to every RestrictedRemoteServer session. They are not ours
#: and they are not a finding, so the drift check ignores them.
SESSION_BUILTINS: frozenset[str] = frozenset(
    {
        "Clear-Host",
        "Exit-PSSession",
        "Get-Command",
        "Get-FormatData",
        "Get-Help",
        "Measure-Object",
        "Out-Default",
        "Select-Object",
    }
)


class HostError(RuntimeError):
    """The one error type this module raises.

    Anything that goes wrong on the way to the host - connection refused, access
    denied, a command that is not on the list, an error stream from the endpoint -
    arrives here. Callers handle one exception, not five libraries' worth.
    """


@dataclass(frozen=True)
class Result:
    """What one call returned, and what it cost.

    Frozen for the same reason Lab 12's Event is: a result you can edit after the
    fact is not evidence.
    """

    command: str
    rows: tuple[Mapping[str, Any], ...]
    seconds: float

    @property
    def count(self) -> int:
        return len(self.rows)


def _as_dict(obj: Any) -> Mapping[str, Any]:
    """Turn one object off the wire into a plain dict.

    PSRP hands back its own object type; the values we want sit in its adapted
    properties. Anything else is returned under a single 'value' key rather than
    being dropped, because a silently missing field reads as an empty result.
    """
    props = getattr(obj, "adapted_properties", None)
    if not props:
        props = getattr(obj, "extended_properties", None)
    if not props:
        return {"value": obj if obj is None else str(obj)}
    return {str(k): (None if v is None else str(v)) for k, v in dict(props).items()}


def _deep(obj: Any) -> Any:
    """Like _as_dict, but keeps STRUCTURE and native types (P05 build step 8).

    The remediator returns nested objects - Before, ReportedAfter, a list of Changes - and an
    int registry value. _as_dict flattens each nested value to its display string ("@{value=1}"),
    which is fine for P01's flat audit rows and useless here. P01 and the reader keep _as_dict
    (their tests pin strings); only callers that ask for deep=True get this.
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _deep(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_deep(v) for v in obj]
    props: dict[str, Any] = {}
    for attr in ("adapted_properties", "extended_properties"):
        props.update(dict(getattr(obj, attr, None) or {}))
    if props:
        return {str(k): _deep(v) for k, v in props.items()}
    return str(obj)


class Host:
    """An open session to one host's JEA endpoint.

    Use it as a context manager so the session is always closed:

        with Host("srv01.corp.lab", r"CORP\\svc-agent-ro", pw) as host:
            host.check_drift()
            result = host.call("Get-AgentAuditPolicy")
    """

    def __init__(
        self,
        server: str,
        username: str,
        password: str,
        configuration_name: str = ENDPOINT,
        port: int = PORT,
        allowed: tuple[str, ...] = ALLOWED,
    ) -> None:
        self.server = server
        self.allowed = tuple(allowed)
        self.username = username
        self.configuration_name = configuration_name
        self._password = password
        self._port = port
        self._wsman: WSMan | None = None
        self._pool: RunspacePool | None = None
        self.connect_seconds: float | None = None

    def __enter__(self) -> "Host":
        started = time.monotonic()
        try:
            self._wsman = WSMan(
                self.server,
                port=self._port,
                ssl=False,
                auth="ntlm",
                username=self.username,
                password=self._password,
                cert_validation=False,
            )
            self._wsman.__enter__()
            self._pool = RunspacePool(
                self._wsman, configuration_name=self.configuration_name
            )
            self._pool.open()
        except Exception as exc:  # noqa: BLE001 - one error type leaves this module
            self.close()
            raise HostError(
                f"could not open '{self.configuration_name}' on {self.server} "
                f"as {self.username}: {exc}"
            ) from exc
        # monotonic, never wall clock: the only clock you may subtract (Lab 12).
        self.connect_seconds = time.monotonic() - started
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        for closer in (self._pool, self._wsman):
            if closer is None:
                continue
            try:
                closer.close() if closer is self._pool else closer.__exit__(None, None, None)
            except Exception:  # noqa: BLE001 - closing must not mask the real error
                pass
        self._pool = None
        self._wsman = None

    def _invoke(self, cmdlet: str, params: Mapping[str, Any] | None = None
                ) -> tuple[list[Any], float]:
        if self._pool is None:
            raise HostError("no open session - use Host as a context manager")
        ps = PowerShell(self._pool)
        ps.add_cmdlet(cmdlet)
        for name, value in (params or {}).items():
            ps.add_parameter(name, value)          # a NAMED parameter: data, not script text
        started = time.monotonic()
        try:
            output = ps.invoke()
        except Exception as exc:  # noqa: BLE001
            raise HostError(f"{cmdlet} failed on {self.server}: {exc}") from exc
        seconds = time.monotonic() - started
        if ps.had_errors:
            errors = "; ".join(str(e) for e in ps.streams.error) or "no detail"
            raise HostError(f"{cmdlet} returned an error from {self.server}: {errors}")
        return list(output), seconds

    def visible_commands(self) -> tuple[str, ...]:
        """What the endpoint says it offers, sorted."""
        output, _ = self._invoke("Get-Command")
        names = {str(_as_dict(obj).get("Name", "")) for obj in output}
        return tuple(sorted(n for n in names if n))

    def check_drift(self) -> tuple[str, ...]:
        """Refuse to run if the endpoint and ALLOWED disagree (ADR 0003).

        The allowed commands are written down twice - here and in the role file
        on the server. Two lists that can disagree will, eventually. This reads
        the server's list at startup and fails closed on any difference, so the
        agent never runs against a surface nobody reviewed.
        """
        visible = self.visible_commands()
        ours = set(visible) - SESSION_BUILTINS
        expected = set(self.allowed)
        if ours != expected:
            missing = sorted(expected - ours)
            extra = sorted(ours - expected)
            raise HostError(
                "endpoint drift: "
                f"missing={missing or 'none'} unexpected={extra or 'none'} "
                f"(endpoint offers {sorted(ours)})"
            )
        return visible

    def call(self, cmdlet: str, params: Mapping[str, Any] | None = None, *,
             deep: bool = False) -> Result:
        """Run one allowed, named command - with named parameters if given - and time it.

        Parameters are still not script text: each is a name and a plain value (PARAM_TYPES),
        added with add_parameter. A name must look like a PowerShell parameter name.
        """
        if cmdlet not in self.allowed:
            raise HostError(f"{cmdlet} is not on this agent's list: {list(self.allowed)}")
        check_params(params)
        output, seconds = self._invoke(cmdlet, params)
        rows = tuple((_deep(obj) if deep else _as_dict(obj)) for obj in output)
        return Result(command=cmdlet, rows=rows, seconds=seconds)


def check_params(params: Mapping[str, Any] | None) -> None:
    """Refuse anything that is not name -> plain value. Raises HostError."""
    for name, value in (params or {}).items():
        if not (isinstance(name, str) and name.isidentifier() and name[:1].isalpha()):
            raise HostError(f"parameter name {name!r} is not a plain identifier")
        if type(value) not in PARAM_TYPES:
            raise HostError(f"-{name}: {type(value).__name__} is not allowed as a value "
                            f"(only {', '.join(t.__name__ for t in PARAM_TYPES)})")


def _main(argv: Iterable[str]) -> int:
    """Build step 4's proof: the same thing you did by hand, from Python, timed."""
    import argparse
    import getpass

    parser = argparse.ArgumentParser(description=__doc__)
    # An address, not a name: WSL resolves through Windows, not through DC01,
    # so "srv01.corp.lab" does not resolve here (the QE inventory uses addresses
    # for the same reason). NTLM does not care about the name; Kerberos would.
    parser.add_argument("--server", default="10.10.10.20")
    parser.add_argument("--user", default="CORP\\svc-agent-ro")
    parser.add_argument("--rows", type=int, default=5, help="how many rows to print")
    args = parser.parse_args(list(argv))

    password = getpass.getpass(f"Password for {args.user}: ")

    try:
        with Host(args.server, args.user, password) as host:
            print(f"connected in {host.connect_seconds:.3f}s")
            visible = host.check_drift()
            print(f"drift check passed; endpoint offers: {', '.join(visible)}")
            result = host.call("Get-AgentAuditPolicy")
            print(f"{result.command}: {result.count} rows in {result.seconds:.3f}s")
            for row in result.rows[: args.rows]:
                print(f"  {row.get('Subcategory', '?')}: {row.get('Setting', '?')}")
    except HostError as exc:
        print(f"REFUSED/FAILED: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
