# SOP — make every shell say which machine it is

**Status:** active
**Applies to:** <host> (host), DC01, SRV01 — any Windows PowerShell window in this lab

## Purpose

Windows PowerShell opens at `PS C:\WINDOWS\system32>` on every machine. The host window, the
DC01 console and the SRV01 console are therefore **visually identical**, and a Hyper-V VM
console looks like a local window. There is no cue at the point of typing that says where a
command is about to run.

## Why this matters here

This is not a cosmetic preference. It has cost real work:

- **2026-09-16 — DC01 was renamed to SRV01.** A `Rename-Computer` intended for the member
  server ran in the domain controller's console. Repair took `netdom computername
  /add /makeprimary`, a restart, and a stale DNS record removal.
- **2026-09-19 — three wrong-window commands in one session.** `Get-VM` run inside both VM
  consoles instead of on the Hyper-V host; a PowerShell block pasted into bash.

The defect is the same one fixed in `AgentReader.psm1` at build step 6: **a status line that
does not name what it is describing.** A prompt that reads `C:\WINDOWS\system32` on four
different machines is reporting the directory and withholding the fact that matters.

## Procedure

Run once per machine, in that machine's own PowerShell window.

1. Confirm where you are before changing anything:

   ```powershell
   hostname
   ```

2. Create the profile file if it does not exist:

   ```powershell
   if (-not (Test-Path $PROFILE)) { New-Item -ItemType File -Path $PROFILE -Force | Out-Null }
   ```

3. Append the prompt function — **only if it is not already there**:

   ```powershell
   if (-not (Select-String -Path $PROFILE -Pattern 'COMPUTERNAME' -Quiet)) {
       Add-Content -Path $PROFILE -Value 'function prompt { "[$env:COMPUTERNAME] $(Get-Location)> " }'
   }
   ```

   The guard is the point. A bare `Add-Content` appends every time it runs, so re-running
   this procedure silently writes a second copy of the function. Here that is harmless —
   the last definition wins and both are identical — but a procedure that cannot be safely
   re-run is one that will eventually do damage in a place that is less forgiving.
   **Written 2026-09-19 without the guard, and duplicated on the first run.**

4. Load it in the current window without reopening:

   ```powershell
   . $PROFILE
   ```

5. Verify — the prompt must now begin with the machine name:

   ```
   [DC01] C:\Windows\system32>
   ```

## Scope and limits

- `$PROFILE` is per user, per host application. A window opened as a *different* user, or
  PowerShell ISE, will not pick it up. Re-run there if needed.
- It does **not** cover WSL bash. That prompt already shows `user@<host>`, and the
  giveaway for a wrong paste there is `command not found` on a PowerShell cmdlet.
- It does not survive a VM rebuild. Re-run it as part of any server rebuild.

## Standing rule, which the prompt supports but does not replace

**Run `hostname` before any command that changes state.** The prompt reduces the chance of a
mistake; the read proves the machine. Same relationship as the exit code and the column
shape in `Get-AgentAuditPolicy` — one is a convention, the other is a check.

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| Prompt unchanged after step 3 | Profile written but not loaded | Run `. $PROFILE`, or open a new window |
| Every new window prints `...profile.ps1 cannot be loaded because running scripts is disabled` | Windows **client** default policy is `Restricted` (`Get-ExecutionPolicy -List` shows every scope `Undefined`); servers default to `RemoteSigned`, which is why this never showed on DC01/SRV01. Hit on the <host> host 2026-09-25 — the prompt had never worked there | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` (no admin, this user only; no confirmation prompt appears), then open a **new** terminal — a restored one replays the old error |
| Function appears twice in `$PROFILE` | Step 3 re-run without the `Select-String` guard | Harmless; remove the duplicate line with a text editor if it bothers you |
| `Add-Content` fails, access denied | Profile path under a protected location, or window not elevated where required | Check `$PROFILE`, re-run elevated |
| Prompt correct locally, absent in a VM console | Profile was written on the host, not in the guest | Repeat the whole procedure at that guest's console |
| Prompt lost after a rebuild | `$PROFILE` lives on the rebuilt disk | Re-run; add to the rebuild checklist |
