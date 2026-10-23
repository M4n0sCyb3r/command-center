"""The Target seam (Project 5, build step 1).

The remediation agent reaches a machine ONLY through a Target, and a Target is
two objects that never hold a reference to each other:

  Reader      observes. It is the VERIFIER (ADR 0034): on SRV01 it will be the
              read-only AgentReader endpoint as svc-agent-ro. It cannot write.
  Remediator  changes. On SRV01 it will be the AgentRemediator endpoint as
              svc-agent-rw. Its menu is a fixed list of functions with allowed
              values - the PowerShell ValidateSet the real endpoint will use -
              so the fake refuses exactly what SRV01 will refuse.

Why two objects and not one class with read() and write(): ADR 0034 says the
thing that checks a fix must not be the thing that made it. In code that means
the verifier must be UNABLE to ask the remediator anything, and the only way
to make that a property instead of a promise is that it holds no reference to
one. tests/test_target.py walks the reader's attributes to prove it.

Every remediation function obeys one contract (ADR 0034):
  preview(call)  what it WOULD change, computed from the machine now; changes nothing
  apply(call)    captures the before-state, applies, and REPORTS what it did.
                 The report is recorded, never trusted as verification (ADR 0036).
Rollback is apply() of the same function with the captured before-values -
a rollback is a write, so it goes through the same door and the same menu.

The baseline (data/remediation-baseline.json) is the answer key: committed
before anything runs against it, validated on load, and identical for both
targets (ADR 0033: one scenario set, two homes).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import validate

from tools import Change

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "data" / "remediation-baseline.json"

# --- the allowed values, per class ------------------------------------------
# One table, read by the baseline schema AND by the remediator's argument
# check, so "a value the baseline may hold" and "a value the fix may write"
# cannot drift apart.
ALLOWED: dict[str, dict[str, tuple]] = {
    "service":  {"start_type": ("automatic", "manual", "disabled"),
                 "status": ("running", "stopped")},
    "audit":    {"setting": ("Success and Failure", "Success", "Failure", "No Auditing")},
    "registry": {"value": (0, 1)},
}

# The menu: one remediation function per class (ADR 0035), named as the
# AgentRemediator endpoint will name them in step 6.
MENU: dict[str, str] = {
    "Set-AgentServiceConfig": "service",
    "Set-AgentAuditSetting":  "audit",
    "Set-AgentRegistryValue": "registry",
}

BASELINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["version", "watched"],
    "properties": {
        "version": {"const": 1},
        "watched": {
            "type": "array",
            "minItems": 3,
            "items": {
                "type": "object",
                "required": ["id", "class", "name", "fields"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "class": {"enum": sorted(ALLOWED)},
                    "name": {"type": "string", "minLength": 1},
                    "fields": {"type": "object", "minProperties": 1},
                },
            },
        },
    },
}


class BaselineError(ValueError):
    """The answer key is malformed. Always fatal: a run against it measures nothing."""


class RemediationRefused(ValueError):
    """The remediator would not run this call. Nothing on the machine changed."""


@dataclass(frozen=True)
class Watched:
    """One value the agent watches: what it is, and what it should be."""

    id: str
    cls: str
    name: str
    fields: dict[str, Any]


def load_baseline(path: Path = BASELINE_PATH) -> list[Watched]:
    """Read and validate the answer key. Refuses rather than guesses."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        validate(raw, BASELINE_SCHEMA)
    except Exception as exc:                       # jsonschema.ValidationError
        raise BaselineError(f"{path}: {getattr(exc, 'message', exc)}") from None
    items: list[Watched] = []
    seen: set[str] = set()
    for row in raw["watched"]:
        if row["id"] in seen:
            raise BaselineError(f"{path}: duplicate id {row['id']!r}")
        seen.add(row["id"])
        allowed = ALLOWED[row["class"]]
        if set(row["fields"]) != set(allowed):
            raise BaselineError(f"{row['id']}: fields {sorted(row['fields'])} != "
                                f"{sorted(allowed)} for class {row['class']!r}")
        for f, v in row["fields"].items():
            if v not in allowed[f]:
                raise BaselineError(f"{row['id']}: {f}={v!r} not in {allowed[f]}")
        items.append(Watched(row["id"], row["class"], row["name"], dict(row["fields"])))
    classes = sorted(w.cls for w in items)
    if classes != sorted(ALLOWED):
        raise BaselineError(f"{path}: need exactly one item per class {sorted(ALLOWED)}, "
                            f"got {classes}")
    return items


# --- the call ---------------------------------------------------------------


@dataclass(frozen=True)
class Call:
    """A remediation request: one menu function, one watched item, the values to write."""

    function: str
    item: str
    values: dict[str, Any]


@dataclass(frozen=True)
class ApplyReport:
    """What the REMEDIATOR says it did. Recorded; never used as verification."""

    call: Call
    before: dict[str, Any]
    reported_after: dict[str, Any]


# --- the fake machine -------------------------------------------------------


class FakeHost:
    """The pretend SRV01: one dict of values per watched item.

    Shared by the fake reader and the fake remediator - as SRV01 is shared by
    two endpoints - and by tests, which use set() as the console: the operator
    (or a saboteur) changing the machine from outside the agent.
    """

    def __init__(self, baseline: list[Watched], name: str = "FAKE-SRV01") -> None:
        self.name = name
        self._state = {w.id: dict(w.fields) for w in baseline}

    def set(self, item: str, **fields: Any) -> None:
        """Change the machine from OUTSIDE the agent (drift, or ADR 0035's undo)."""
        if item not in self._state:
            raise KeyError(item)
        self._state[item].update(fields)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self._state)


class FakeReader:
    """The verifier's view of the fake: a read function and a name, nothing else.

    It does NOT hold the FakeHost, because the host has set() - and on SRV01 the
    verifier's account cannot write (ADR 0034). Holding only a closure that copies
    out one item keeps "the verifier cannot write and cannot reach the remediator"
    a property tests/test_target.py can check, not a convention.
    """

    def __init__(self, host: FakeHost) -> None:
        state, name = host._state, host.name
        self._read = lambda item: copy.deepcopy(state[item])
        self._name = name

    def host(self) -> str:
        return self._name

    def read(self, item: str) -> dict[str, Any]:
        return self._read(item)


class FakeRemediator:
    """The fixer's door on the fake. Its own read path (for previews and the
    before-capture), its own menu check, its own write."""

    def __init__(self, host: FakeHost, baseline: list[Watched]) -> None:
        self._host = host
        self._items = {w.id: w for w in baseline}

    def menu(self) -> list[str]:
        return sorted(MENU)

    def _check(self, call: Call) -> Watched:
        if call.function not in MENU:
            raise RemediationRefused(f"{call.function!r} is not on the menu {sorted(MENU)}")
        w = self._items.get(call.item)
        if w is None:
            raise RemediationRefused(f"{call.item!r} is not a watched item")
        if MENU[call.function] != w.cls:
            raise RemediationRefused(f"{call.function} cannot change a {w.cls} item")
        allowed = ALLOWED[w.cls]
        if not call.values:
            raise RemediationRefused("no values to write")
        for f, v in call.values.items():
            if f not in allowed:
                raise RemediationRefused(f"{f!r} is not a field of a {w.cls} item")
            if v not in allowed[f]:
                raise RemediationRefused(f"{f}={v!r} not in {allowed[f]}")
        return w

    def _current(self, item: str) -> dict[str, Any]:
        return dict(self._host._state[item])

    def preview(self, call: Call) -> list[Change]:
        """What apply() would change. Every requested field is listed, changed or not
        (Lab 13: a zero-line diff can still be an action)."""
        w = self._check(call)
        now = self._current(w.id)
        return [Change(target=w.id, field=f, before=now[f], after=v,
                       effect="no change" if now[f] == v else "set")
                for f, v in sorted(call.values.items())]

    def _write(self, item: str, values: dict[str, Any]) -> None:
        self._host._state[item].update(values)

    def apply(self, call: Call) -> ApplyReport:
        w = self._check(call)
        before = self._current(w.id)
        self._write(w.id, dict(call.values))
        return ApplyReport(call=call, before=before,
                           reported_after={**before, **call.values})

    def rollback(self, report: ApplyReport) -> ApplyReport:
        """Put back the before-state the report captured - through the same menu."""
        restore = Call(report.call.function, report.call.item,
                       {f: report.before[f] for f in report.call.values})
        return self.apply(restore)


def _wrong(cls: str, values: dict[str, Any]) -> dict[str, Any]:
    """For each field, an ALLOWED value other than the one asked for - plausible, and wrong."""
    return {f: next(a for a in ALLOWED[cls][f] if a != v) for f, v in values.items()}


class BrokenRemediator(FakeRemediator):
    """ADR 0035's CI sabotage: the FIX reports success and writes a wrong value.

    Its rollback is honest. That is the scenario ADR 0039 expects to end
    `rolled-back`: a bad fix, caught by the verifier, undone correctly. Only a
    verifier that reads the machine - not the report - can tell the fix lied.
    """

    _rolling_back = False

    def _write(self, item: str, values: dict[str, Any]) -> None:
        if not self._rolling_back:
            values = _wrong(self._items[item].cls, values)
        super()._write(item, values)

    def rollback(self, report: ApplyReport) -> ApplyReport:
        self._rolling_back = True
        try:
            return super().rollback(report)
        finally:
            self._rolling_back = False


class BrokenRollbackRemediator(FakeRemediator):
    """The worst case, for `rollback-failed`: the fix AND the rollback write wrong values."""

    def _write(self, item: str, values: dict[str, Any]) -> None:
        super()._write(item, _wrong(self._items[item].cls, values))


# --- SRV01: the real verifier (build step 7) ---------------------------------

#: Which AgentReader function reads which class. Each SRV01 read is FIXED to one item on the
#: server side (AgentReader.psm1) - the caller cannot name a service or a key - so the item a
#: row reports is checked against the item asked for, not trusted.
SRV01_READS: dict[str, str] = {
    "service":  "Get-AgentServiceState",
    "registry": "Get-AgentRegistryState",
    "audit":    "Get-AgentAuditPolicy",
}

#: Audit subcategories are addressed by GUID, not by display name (names are localised; the
#: remediator writes by GUID too). One entry per watched audit item.
AUDIT_GUIDS: dict[str, str] = {"audit:Logon": "{0CCE9215-69AE-11D9-BED3-505054503030}"}

#: The only identity and endpoint the verifier may read through (ADR 0034).
VERIFIER_USER = "svc-agent-ro"
VERIFIER_ENDPOINT = "AgentReader"
#: Where the `live` tier reads (an address: WSL resolves through Windows, not DC01).
SRV01_SERVER = "10.10.10.20"
VERIFIER_ACCOUNT = "CORP\\svc-agent-ro"


class ReadError(RuntimeError):
    """The endpoint answered, but not with one well-formed reading of the item asked for."""


class SrvReader:
    """The verifier's view of SRV01: AgentReader as svc-agent-ro. Same two methods as
    FakeReader - host() and read() - so the agent cannot tell the targets apart.

    It holds an open session (host_client.Host, or anything with `call()`) and NOTHING that
    writes. It refuses, at construction, a session that is not the read-only identity on the
    read-only endpoint: "the verifier cannot write" is checked here, not assumed (ADR 0034).

    Values come off PSRP as strings (host_client._as_dict). read() returns the SAME types the
    fake returns - registry value as int - because detect() compares with ==, and "1" != 1
    would report drift that is not there. tests/test_srv_reader.py pins that.
    """

    def __init__(self, session: Any, baseline: list[Watched]) -> None:
        user = str(getattr(session, "username", ""))
        endpoint = str(getattr(session, "configuration_name", ""))
        if user.split("\\")[-1].lower() != VERIFIER_USER:
            raise ValueError(f"the verifier must read as {VERIFIER_USER}, not {user!r} (ADR 0034)")
        if endpoint != VERIFIER_ENDPOINT:
            raise ValueError(f"the verifier must read through {VERIFIER_ENDPOINT}, not {endpoint!r}")
        self._call = session.call
        self._name = str(getattr(session, "server", "SRV01"))
        self._cls = {w.id: w.cls for w in baseline}

    def host(self) -> str:
        return self._name

    def _one(self, item: str, rows: tuple) -> dict[str, Any]:
        if len(rows) != 1:
            raise ReadError(f"{item}: expected one row, got {len(rows)}")
        row = dict(rows[0])
        if row.get("Item") != item:
            raise ReadError(f"asked for {item}, the endpoint answered for {row.get('Item')!r}")
        return row

    def read(self, item: str) -> dict[str, Any]:
        cls = self._cls.get(item)
        if cls is None:
            raise KeyError(item)
        rows = tuple(self._call(SRV01_READS[cls]).rows)
        if cls == "service":
            row = self._one(item, rows)
            return {"start_type": row.get("start_type"), "status": row.get("status")}
        if cls == "registry":
            row = self._one(item, rows)
            raw = row.get("value")
            if raw is None or not str(raw).lstrip("-").isdigit():
                raise ReadError(f"{item}: value {raw!r} is not an integer")
            return {"value": int(raw)}
        guid = AUDIT_GUIDS.get(item)
        if guid is None:
            raise ReadError(f"{item}: no subcategory GUID known for it")
        hits = [r for r in rows if str(r.get("Guid", "")).upper() == guid.upper()]
        if len(hits) != 1:
            raise ReadError(f"{item}: {len(hits)} audit rows with GUID {guid}, expected 1")
        return {"setting": hits[0].get("Setting")}


# --- SRV01: the real remediator (build step 8) -------------------------------

#: The only identity and endpoint the fixer may write through (ADR 0034).
REMEDIATOR_USER = "svc-agent-rw"
REMEDIATOR_ENDPOINT = "AgentRemediator"
REMEDIATOR_ACCOUNT = "CORP\\svc-agent-rw"

#: Python field -> the PowerShell parameter that carries it. The item is NOT a parameter:
#: each AgentRemediator function is fixed server-side to one service / subcategory / value.
PS_PARAM: dict[str, str] = {"start_type": "StartType", "status": "Status",
                            "setting": "Setting", "value": "Value"}


class SrvRemediator:
    """The fixer's door on SRV01: AgentRemediator as svc-agent-rw. Same three methods as
    FakeRemediator - preview, apply, rollback - plus menu().

    Two checks before anything crosses the wire: the SAME menu/argument check the fake runs
    (so the fake refuses exactly what this refuses, and nothing off-menu reaches SRV01), and
    the endpoint's own ValidateSet after it. What comes back is a REPORT - recorded, never
    verification (ADR 0036); the verifier is SrvReader, a different account on a different
    endpoint.
    """

    _check = FakeRemediator._check           # one menu check for both targets

    def __init__(self, session: Any, baseline: list[Watched]) -> None:
        user = str(getattr(session, "username", ""))
        endpoint = str(getattr(session, "configuration_name", ""))
        if user.split("\\")[-1].lower() != REMEDIATOR_USER:
            raise ValueError(f"the remediator must write as {REMEDIATOR_USER}, not {user!r}")
        if endpoint != REMEDIATOR_ENDPOINT:
            raise ValueError(f"the remediator must write through {REMEDIATOR_ENDPOINT}, "
                             f"not {endpoint!r}")
        self._call = session.call
        self._items = {w.id: w for w in baseline}

    def menu(self) -> list[str]:
        return sorted(MENU)

    @staticmethod
    def _typed(cls: str, values: dict[str, Any]) -> dict[str, Any]:
        """Back to the baseline's types: a registry value is an int (ADR 0039 parity)."""
        out = dict(values)
        if cls == "registry" and "value" in out and out["value"] is not None:
            out["value"] = int(out["value"])
        return out

    def _send(self, call: Call, preview: bool) -> dict[str, Any]:
        w = self._check(call)
        params: dict[str, Any] = {PS_PARAM[f]: v for f, v in sorted(call.values.items())}
        if preview:
            params["Preview"] = True
        rows = tuple(self._call(call.function, params, deep=True).rows)
        if len(rows) != 1:
            raise ReadError(f"{call.function}: expected one result, got {len(rows)}")
        row = dict(rows[0])
        if row.get("Item") != w.id:
            raise ReadError(f"asked to change {w.id}, the endpoint answered for "
                            f"{row.get('Item')!r}")
        return row

    def preview(self, call: Call) -> list[Change]:
        w = self._check(call)
        row = self._send(call, preview=True)
        if row.get("Applied") not in (False, "False"):
            raise ReadError(f"{call.function} -Preview reports Applied={row.get('Applied')!r}")
        out = []
        for c in row.get("Changes") or []:
            before = self._typed(w.cls, {c["Field"]: c.get("Before")})[c["Field"]]
            after = self._typed(w.cls, {c["Field"]: c.get("After")})[c["Field"]]
            out.append(Change(target=w.id, field=c["Field"], before=before, after=after,
                              effect=c.get("Effect")))
        if sorted(c.field for c in out) != sorted(call.values):
            raise ReadError(f"preview listed {[c.field for c in out]}, "
                            f"asked for {sorted(call.values)}")
        return sorted(out, key=lambda c: c.field)

    def apply(self, call: Call) -> ApplyReport:
        w = self._check(call)
        row = self._send(call, preview=False)
        before = self._typed(w.cls, dict(row.get("Before") or {}))
        after = self._typed(w.cls, dict(row.get("ReportedAfter") or {}))
        missing = [f for f in call.values if f not in before]
        if missing:
            # Without a before-value there is nothing exact to roll back to: say so loudly.
            raise ReadError(f"{call.function} returned no before-value for {missing}")
        return ApplyReport(call=call, before=before, reported_after=after)

    def rollback(self, report: ApplyReport) -> ApplyReport:
        restore = Call(report.call.function, report.call.item,
                       {f: report.before[f] for f in report.call.values})
        return self.apply(restore)


# --- the seam ---------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """What the agent holds. Two doors, never the same object."""

    reader: Any
    remediator: Any
    kind: str = field(default="fake")

    def __post_init__(self) -> None:
        if self.reader is self.remediator:
            raise ValueError("reader and remediator must be separate objects (ADR 0034)")


REMEDIATORS = {"honest": FakeRemediator, "broken": BrokenRemediator,
               "broken-rollback": BrokenRollbackRemediator}


def fake_target(baseline: list[Watched] | None = None, *, broken: bool | str = False
                ) -> tuple[Target, FakeHost]:
    """A fake target at baseline, and its host (the test's console).

    broken: False/"honest", True/"broken" (bad fix, honest rollback), or "broken-rollback".
    """
    baseline = baseline if baseline is not None else load_baseline()
    host = FakeHost(baseline)
    kind = {False: "honest", True: "broken"}.get(broken, broken)
    rem = REMEDIATORS[kind](host, baseline)
    return Target(reader=FakeReader(host), remediator=rem, kind="fake"), host
