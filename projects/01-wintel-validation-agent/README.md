# Project 1 — Wintel Validation Agent

A read-only agent that answers questions about the lab's Windows servers (DC01, SRV01)
by running a fixed, reviewed set of checks — never PowerShell it wrote itself.

**Status:** **complete** · build order 1st (ADR 0001) · six build steps (2026-09-16 → 2026-09-19); endpoint live on SRV01 and DC01, its failure message names the fault, card and SOP written 2026-09-20

## Decisions

| ADR | Decision |
|---|---|
| `docs/adr/0002-p1-transport.md` | Direct WinRM from Python; password asked at startup, held only in memory |
| `docs/adr/0003-p1-account-and-privilege.md` | Non-admin agent account; admin-only reads through a JEA endpoint; `pypsrp` |
| `docs/adr/0004-p1-jea-role-mapping.md` | The JEA role names the account itself, not a group |
| `docs/adr/0005-p1-dc01-run-as-identity.md` | On a DC the endpoint runs as a virtual account in a named group, not Domain Admins |

Design call 7 (2026-09-19) is deliberately **not** an ADR — keeping `2>&1` and converting
the capture to text ourselves is ten lines inside one function, reversible in a commit.
It is recorded in `evidence/step-06-error-text.md` and in the module's comments.

## Build steps

| # | Step | Status | Evidence |
|---|---|---|---|
| 1 | Agent's non-admin domain account on DC01 (`CORP\svc-agent-ro`) | **done** 2026-09-16 | `evidence/step-01-agent-account.md` |
| 2 | JEA role capability + session configuration files; register on SRV01 | **done** 2026-09-18 | `evidence/step-02-jea-endpoint.md` |
| 3 | Prove it from Windows: visible commands, one run, unlisted command absent | **done** 2026-09-18 | `evidence/step-02-jea-endpoint.md` |
| 4 | `pypsrp` in `.venv`; same proof from WSL, timed | **done** 2026-09-18 | `evidence/step-04-pypsrp-from-wsl.md` |
| 5a | QE baselines re-run after the SRV01 endpoint | **done** 2026-09-18 (SRV01 30/30, DC01 34/34) | `evidence/step-05-baselines-after-the-endpoint.md` |
| 5b | DC01 endpoint with a narrowed run-as identity, proved by a negative control | **done** 2026-09-18 | `evidence/step-05-dc01-endpoint.md` |
| 6 | The failure message reports auditpol's own words, not a .NET class name | **done** 2026-09-19 | `evidence/step-06-error-text.md` |

## Open

- ~~Cosmetic defect: `System.Management.Automation.RemoteException` fragments in the
  privilege-denied message.~~ **Closed 2026-09-19, build step 6.** The mechanism was
  measured, not guessed: a blank stderr line arrives as an ErrorRecord whose
  RemoteException has an empty `Message`, so its `ToString()` prints its own class name.
- ~~Project card and the SOP.~~ **Closed 2026-09-20.** `notes/project-01-wintel-validation-agent.md` (14,646 b) and `sops/jea-read-only-endpoint.md` (13,351 b).
- ~~The four identical `PS C:\WINDOWS\system32>` prompts.~~ **Closed 2026-09-20.** `sops/identify-the-shell.md` applied at the DC01 and SRV01 consoles; both now print `[DC01]` / `[SRV01]`. The host was done 2026-09-19.
- The unclean DC01 shutdown at 17:25 on 2026-09-19 is unexplained.
- DC01 expires 2027-02-11 with one rearm left — run it late January 2027.
