# SOP — Bringing the bench up

**Scope:** starting a lab session on <host>. Covers the Python environment in WSL
and the Ollama model service on Windows, and the diagnosis order when a lab command
reports that the model is unreachable.

**Applies to:** `<repo>` and any repo on the same bench. Written 2026-09-13 from three consecutive bench failures in one session.

**Not in scope:** anything touching employer systems, data, credentials or networks. The
labs use generic placeholder values only.

---

## 1. Preconditions

| Thing | Where it runs | How you know |
|---|---|---|
| WSL2 Ubuntu | the Linux side | prompt reads `<user>@<host>` |
| `.venv` (Python 3.14.4) | the Linux side | prompt is prefixed `(.venv)` |
| Ollama server | **Windows**, not WSL | a process named `ollama`, listening on 11434 |
| `qwen3:8b`, `nomic-embed-text` | Windows, pulled | `ollama list` |

The single fact that explains every failure below: **the model is a network service on
another machine.** WSL2 is NAT'd, so `localhost` inside WSL is not Windows. The labs reach
Windows through the default gateway, and that address changes on reboot.

---

## 2. Procedure

### 2.1 Start the model service (Windows PowerShell)

```powershell
Start-Process ollama -ArgumentList "serve"
```

Or launch **Ollama** from the Start menu, which runs the same server from the system tray.
The tray app is preferred: it does not depend on a window staying open.

Since **0.34.0** the tray app also opens an **Apps** catalogue — Cline, Copilot CLI, Claude
Code, Codex CLI, Hermes and others. **Click none of it.** Those are third-party agent tools
unrelated to this course. Opening the app is what starts the server; the catalogue is just a
window sitting on top of it. Minimise it, do not close it.

Verify:

```powershell
Get-Process ollama -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, StartTime
Get-NetTCPConnection -LocalPort 11434 -ErrorAction SilentlyContinue | Select-Object State, OwningProcess
```

A process **and** a `Listen` state on 11434 means the Windows side is done. `Start-Process`
sometimes produces no visible window — that is normal and not a failure.

### 2.2 Enter the environment (WSL)

```bash
cd <repo> && source .venv/bin/activate && which python
```

`which python` must end in `.venv/bin/python`, and the prompt must show `(.venv)`. **Every
new terminal needs this** — including a new pane in VS Code. The venv is not inherited.

### 2.3 Prove the bench (WSL)

```bash
python src/bench_check.py; echo "exit code: $?"
```

`PASS: bench online` and exit `0`. Nothing else counts as ready — not a running process,
not a green editor. Run this **before** starting any lab work; two seconds here saves
discovering the problem three minutes into a measurement.

---

## 3. Diagnosis order when `bench_check` fails

Narrowest first. Each step eliminates a whole class of cause.

| # | Question | Command | If wrong |
|---|---|---|---|
| 1 | Is the server running at all? | `Get-Process ollama` (PowerShell) | Start it — §2.1 |
| 2 | Is it bound where WSL can reach it? | `$env:OLLAMA_HOST` (PowerShell) | Must be `0.0.0.0`. If empty, Ollama bound to `127.0.0.1` and is invisible to WSL |
| 3 | Has the gateway moved? | `ip route show default` (WSL) | Compare with the address `bench_check` printed. If it changed, the firewall rule's scope is stale |
| 4 | Is the firewall dropping it? | `Get-NetFirewallRule -DisplayName "Ollama - WSL only"` (PowerShell) | Re-create it, scoped to `172.16.0.0/12` |

**The tell that separates 1 from 4:** a host that is up with nothing on the port refuses
instantly. A hang in `sock.connect` means packets went nowhere — firewall drop or wrong
address, not a stopped process.

---

## 4. Failure modes seen in practice

| Symptom | Cause | Fix |
|---|---|---|
| `FAIL: nothing is listening at http://<gw>:11434` | Ollama not running — it does **not** survive a reboot | §2.1 |
| Server was running, then everything stops | The PowerShell window opened by `Start-Process ollama serve` **is** the server; closing it kills the process | Minimize it and leave it open. *(Corrected 2026-10-07: this row used to say "use the tray app" — see the next row.)* |
| `curl` / harness time out from WSL; `netstat -ano \| findstr :11434` shows **nothing** `LISTENING`; `Get-Process ollama*` shows only **`ollama app`** | The tray app is running but **no server is** — seen 2026-10-06, and the harness `STALLED` at case 1 (exit 2, no verdict) | `Stop-Process -Name "ollama app"`, then `ollama serve` in its own window (or a VS Code terminal tab named `ollama`), left open. It runs in the foreground and looks frozen — it is not. No tray icon in this mode: the open window is the indicator |
| `bench_check` says `nothing is listening`, but `curl` a moment later answers | **Startup race** — checked before the server finished starting (seen 2026-10-07) | After `ollama serve`, wait for the `inference compute … RTX 5080` log line, then run `bench_check` |
| `Start-Process` returns, no window appears | Normal — it started detached | Verify with `Get-Process`, not by looking for a window |
| Tray app opens a menu of apps (Cline, Codex, Hermes…) and it is unclear what to click | Ollama 0.34.0 ships a launcher catalogue for third-party agent tools | Click nothing. The server started when the app opened; verify port 11434 is in `Listen` |
| `bench_check` prints `FAIL: Ollama changed X -> Y since the bench was last verified` (A and B passed) | Ollama on Windows **updates itself** — 0.33.3 → 0.34.4 → 0.40.1 by 2026-10-08; the 0.40.1 update also changed the reported model digest | `python tools/upgrade_check.py`. **CLEAN** (exit 0): it rewrote `bench-pin.json` — commit it and carry on. **MOVED** (exit 1): the pin stays; log what moved before building on the bench |
| `ModuleNotFoundError` on a repo module | venv not activated, or running from the wrong directory | §2.2; `src` is not a package, so run `python src/x.py` from the repo root |
| Unresolved-import warnings in VS Code | VS Code opened on the Windows side and cannot execute the Linux interpreter | Bottom-left must read `WSL: Ubuntu`; reopen with `code .` from WSL |
| `Ctrl+P` finds no repo files | No folder open in the window | `code .` from WSL in the repo |
| `ModelError: the model did not answer within 120s` | A single call exceeded the deadline; vague prompts make qwen3 think longer | Pass `timeout=` through, and make the harness record the failure rather than raise |
| Same question is fast, then times out, then fast again | Temperature 0 pins sampling, not server execution state (KV-cache, batching, model reload) | Scope determinism claims to a session; re-measure after any restart |
| `git` fails on `index.lock` | Claude's device shell cannot delete files on this repo | `rm -f .git/*.lock .git/objects/*/tmp_obj_*` before any git command |
| `device_bash` fails: "no Plan9 drive shares mounted" | Claude's Linux workspace cannot mount the folder (Windows update, Sept 8 2026) | Treat as normal: Claude writes through the file bridge, the user runs every command |

---

## 5. Verification

The bench is up when all four hold:

1. `Get-Process ollama` returns a process, and 11434 is in `Listen`
2. the WSL prompt is prefixed `(.venv)` and `which python` ends in `.venv/bin/python`
3. `python src/bench_check.py` prints `PASS: bench online` and exits `0`
4. `ollama list` shows the models the session needs

---

## 6. Why this SOP exists

One session lost roughly forty minutes to three separate instances of "the model is not
reachable," each with a different cause and each diagnosed from scratch. The procedure is
four commands; the value is the **order**, and the table of symptoms that maps a message to
a cause without re-deriving it at midnight.

