# SOP — stand up a read-only JEA endpoint for a service identity

**Status:** active
**Applies to:** Windows Server 2016+ member servers and domain controllers
**Produced by:** Project 1, Wintel Validation Agent (build steps 1–6, 2026-09-16 → 2026-09-19)
**Evidence:** `projects/01-wintel-validation-agent/evidence/`

## Purpose

Give a non-interactive process — an agent, a script, a monitoring job — the ability to read
**privileged** configuration from a Windows server, without giving the account that runs it
any privilege of its own, and without giving it a shell.

## Why this shape and not an admin account

The naive version is a service account in `Administrators` plus an allow-list in your code.
That is one wall, built by the thing being contained. If the allow-list has a bug, a prompt
is injected, or an argument is mis-parsed, nothing else is in the way.

JEA supplies a **second wall, built by a different system**. The account holds nothing; the
endpoint supplies elevation for a named list of commands and refuses everything else. Both
walls have to fail before anything happens.

## Scope

Covers: one exposed read-only function, one service account, one or more target servers.
Does **not** cover: write or remediation commands (they need an approval gate — see Lab 11
and Lab 13), multiple agent identities, or credential rotation at scale.

## Preconditions

- A domain (or local) account that can create the service account and, on a DC, a group and
  a GPO.
- The target server's WinRM is healthy and reachable, proved before you start.
- A **validation baseline** you can re-run afterwards. Registering an endpoint is an
  operator change; a security control that quietly breaks something else is not a security
  control.
- Console access to each target. Registration restarts WinRM and **cannot** be run over the
  remote channel you would be using.

## Safety

- Do the member server **first**. A domain controller is not where you discover that the
  module has a bug.
- Never put the service account password in a repo, a chat, an environment variable or a
  script. Type it at the console; keep it in a password manager.
- On a domain controller, **do not rely on a Hyper-V checkpoint as your undo.** See step 6.

---

## Procedure

### 1 — Create the service account, and read it back

Non-administrator. Member of the default users group and nothing else. Set the password not
to expire, or the domain's default will lock the process out silently weeks later.

Then, in a **separate command**, read the account back and confirm every attribute.

> A step is done when a separate read shows the result — not when the command appears to
> have run. `New-ADUser` printing nothing is not evidence that an account exists. This
> project lost an account to that assumption and never established how.

### 2 — Write the module, and make it testable off the target

One exported function. **No parameters that reach a command line** — if callers need
filtering, they filter their own side.

Split the work in two: the part that talks to the system, and a **private** parsing function
that takes text and returns objects. The parser is the part you can test with no server, no
admin rights and no domain.

Constraints that apply inside a JEA session and nowhere else:

- **There are no PowerShell providers.** `Env:`, `HKLM:` and friends do not exist, so
  `$env:SystemRoot` is empty. Build paths with .NET:
  `[System.IO.Path]::Combine([System.Environment]::SystemDirectory,'tool.exe')`.
- `Set-StrictMode` makes reading an unset variable a terminating error — initialise
  `$LASTEXITCODE` before you read it, and reach possibly-absent properties through
  `psobject.Properties`.
- Native tools emit blank lines; accept them explicitly (`[AllowEmptyString()]`) or parameter
  binding will reject them.

### 3 — Write the two policy files

| File | Answers | Key settings |
|---|---|---|
| `<Name>.psrc` (role capability) | *what may run* | `VisibleFunctions` — your function only |
| `<Name>.pssc` (session configuration) | *who may connect, and how* | `RoleDefinitions` naming the account, `SessionType = RestrictedRemoteServer`, `LanguageMode = NoLanguage`, `RunAsVirtualAccount = $true`, `TranscriptDirectory` |

Validate before you deploy anything:

```powershell
Test-PSSessionConfigurationFile -Path .\<Name>.pssc
```

Keep both files in version control. A `.pssc` is copied at registration time, so what is
registered on a machine is a **snapshot** — if two servers need different settings, keep two
files rather than one parameterised file, so the repo holds exactly the snapshots that exist.

### 4 — Deploy the files, register at the console

Copy the module tree to `C:\Program Files\WindowsPowerShell\Modules\<Name>\` and the `.pssc`
somewhere temporary. Verify the byte sizes on the target match the repo.

Then, **at that machine's console**:

```powershell
hostname                                   # prove the machine before changing it
Register-PSSessionConfiguration -Name <Name> -Path C:\Windows\Temp\<Name>.pssc -Force
```

If the role grant fails — *"Some or all identity references could not be translated"* — the
endpoint is registered but **nobody has access**. That is fail-closed and correct. Fix the
account, re-run. Do not work around it.

### 5 — Prove it, as the account, four ways

From a shell as the service account:

1. The session **opens** against `-ConfigurationName <Name>`.
2. `Get-Command` returns your function plus the session built-ins, and nothing else.
3. Your function **returns real data** — which only works if the endpoint's elevation is real.
4. An unlisted command (`Get-Process`) is *not recognized*, **and** the same account against
   the **default** endpoint is refused with `Access is denied`.

Point 4's second half is the one people skip. Without it you have proved the room is small,
not that it is the only room.

> Choose a first exposed function that **can fail** — one the account is refused outside the
> endpoint. A function any user could already run passes whether the elevation works or not,
> which is a false pass.

### 6 — On a domain controller only: choose the ceiling deliberately

**A DC has no local groups.** The `RunAsVirtualAccount` setting that lands in local
`Administrators` on a member server lands in **Domain Admins** on a DC.

Instead:

1. Create a **Domain Local** security group. Leave it **empty** — its members are the
   per-session virtual accounts WinRM creates and destroys. Describe that in the object.
2. Set `RunAsVirtualAccountGroups = '<Group>'` in that server's `.pssc`.
3. Grant the specific right your tool needs (for `auditpol`, `SeSecurityPrivilege`) via a
   **new GPO linked to the Domain Controllers OU at link order 1** — not by editing the
   Default Domain Controllers Policy, because you want an undo that is one unlink away.
4. **The GPO must also name `Administrators`.** User Rights Assignment is **replace, not
   additive**: the winning GPO supplies the entire list of holders. Omit the built-ins and
   you silently strip them from every domain controller.

No cmdlet sets User Rights inside a GPO — use GPMC by hand, then verify by export rather
than by looking at what you typed:

```powershell
secedit /export /areas USER_RIGHTS /cfg C:\Windows\Temp\ura.inf
Select-String -Path C:\Windows\Temp\ura.inf -Pattern Se<Right>
```

`secedit` reports what the machine **effectively has after policy applied**, which is a
different claim from what you asked for.

> This is also why the undo is a GPO unlink and not a checkpoint. Reverting a domain
> controller risks **USN rollback** — the directory believes it replicated changes it no
> longer holds and quietly stops replicating. On a DC, the undo must exist at the change's
> own level.

### 7 — Run the negative control

**Mandatory. This is the step that turns the design into a measurement.**

Remove the thing you claim is load-bearing — unlink the rights GPO, `gpupdate /force` — and
run the agent's call again. **It must fail.** Then restore and confirm it works again.

Until you have done this, a passing call is consistent both with your design *and* with the
over-privileged design you rejected. A test both hypotheses predict has distinguished
nothing.

Expect the negative control to find bugs, because the happy path never executes the error
branch. Read the failure message it produces and check that it **names the real fault**.

### 8 — Connect the client

Use a library that speaks **PSRP**, not plain WinRM — only PSRP can open a session against a
*named* configuration. Ask for the password at startup and hold it in memory only.

Add a **drift check**: at startup, ask the live endpoint what it offers and refuse to
proceed if it differs from your own allow-list. The server is the authority; your list is a
claim about it.

Open **one session per run**, not per call — connecting costs several times what a call
costs.

### 9 — Re-run the validation baseline

On every server you touched. Take the verdict from the independent reporter's exit code, not
from reading a task recap.

### 10 — Record it

One evidence file per step: what was run, what came back, what it proves, and **how to undo
it**. Irreversible choices get an ADR; ten reversible lines do not.

---

## Verification — all eight before you call it done

1. The service account exists, is enabled, is in no privileged group — confirmed by a
   separate read.
2. `Get-PSSessionConfiguration` shows the expected `Permission`, `SessionType`,
   `RunAsVirtualAccount` and (on a DC) `RunAsVirtualAccountGroups`.
3. The session opens as the service account.
4. Only the intended function is visible.
5. The function returns real data through the endpoint.
6. An unlisted command fails **and** the default endpoint refuses the account.
7. **The negative control breaks it, and restoring fixes it.**
8. The validation baseline passes on every touched server.

---

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| `Some or all identity references could not be translated`; endpoint registered, nobody has access | The account named in `RoleDefinitions` does not exist | Fail-closed and correct. Create/verify the account with a separate read, re-register |
| `Cannot bind argument to parameter 'Path' because it is null` inside the endpoint, works fine in a normal shell | **JEA sessions have no providers** — `$env:X` is empty | Build paths with `[System.IO.Path]` / `[System.Environment]`, never a provider |
| Terminating error reading a variable or property that "should" exist | `Set-StrictMode` | Initialise `$LASTEXITCODE`; read properties via `psobject.Properties` |
| Parser rejects the tool's output at parameter binding | Native tools emit blank lines | `[AllowEmptyString()]` on the parameter |
| Tool "succeeds" but the parser reports a missing column | **The tool exited 0 while printing an error** | Check the exit code **and** the output shape; surface what the tool actually said |
| Error message contains `System.Management.Automation.RemoteException` | `2>&1` captures error *records*; across PSRP an exception with an empty message stringifies to its own type name | Convert the capture to text yourself: `Exception.Message` → `TargetObject` → `ErrorDetails.Message`; treat a bare dotted type name as **no text**, not as text |
| `Access is denied` for the service account at its OWN endpoint | Often just a wrong password — indistinguishable from a closed door | Check `badPwdCount` / `LastBadPasswordAttempt` on the DC; reset rather than guess. **Re-run every denial check afterwards: a refusal is evidence only when the credential is proven** — give each denial a positive control (the account opens its own endpoint) |
| Endpoint works on a DC — but you cannot say why | Virtual account may be in Domain Admins | Run the negative control. Do not ship an unproved ceiling |
| Built-in `Administrators` silently loses a user right | **URA is replace, not additive** | The GPO must name every holder, including `Administrators`; verify with `secedit /export` |
| Registration over the remote channel hangs or drops | Registration restarts WinRM | Register at the console only |
| `win_copy` fails: "does not support raw params" | A space in `C:\Program Files\...` with short-form args | Use JSON args: `-a '{"src":"...","dest":"..."}'` |
| Client cannot open the named endpoint | `pywinrm` cannot target a named configuration | Use a PSRP client (`pypsrp`) |
| Client cannot resolve `server.domain.tld` from WSL | WSL resolves through Windows, not the DC | Address by IP, or point the resolver at the DC |
| `The term 'Get-CimInstance' is not recognized` inside the endpoint; works in a normal shell | The restricted session does not load every module for code in yours (found P05 step 7, SRV01) | Reach the system through .NET (`[System.Management.ManagementObject]` for WMI, `[Microsoft.Win32.Registry]` for the registry) — never assume a cmdlet a module would supply |
| New role mapped in the `.pssc`, endpoint still shows the old functions | The session file is read once, at registration | Copy the `.pssc` to the server and **re-register at the console**; a module change alone needs no re-registration |
| `The WSMan service could not launch a host process` for the mapped account, `Get-PSSessionConfiguration` shows AccessAllowed | Registered with `RunAsVirtualAccount = $false`: the session host must start as the plain account, which has no remote-session right on the server (P05 step 7, SRV01 — persists after WinRM is back up) | Expected when that is the negative control — record it as fail-closed, and note the privileged call itself never ran. Otherwise restore the virtual account and re-register |
| Test cannot see a private module function when dot-sourced | `Export-ModuleMember` working correctly | `$m = Import-Module ... -PassThru; & $m { ... }` — also runs the test under the module's own StrictMode |
| Module change appears not to deploy | — | Replacing the module file **is** the deployment. The `.pssc`/`.psrc` reference it by name and PowerShell loads it per session: **no re-registration, no WinRM restart** |
| Test suite passes but never caught a known defect | The rig had never executed (execution policy), or every fixture was hand-built | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`; add a fixture captured from a **real** failure. A test rig that has not run is a claim, not a gate |
| Baseline drops one check after a policy change | A check with a `lookback_minutes` window describes recent history, not configuration | Re-run once the events age out; consider splitting history checks out of the baseline |

## Standing rule

**Prove the ceiling by breaking it.** Everything else in this procedure is setup.
