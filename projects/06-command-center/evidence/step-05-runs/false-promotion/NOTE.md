# A FALSE PROMOTION — moved out of `promoted/` (Jose's pick, 2026-10-22)

`promote-bad-syslog-2bb632b1/` was written to `promoted/` by run B on 2026-10-22 at 22:0x
(exit 0, "cleared"). **It is not cleared.** The script raises the System log to 64 MB; SRV01's
QE baseline pins it at 20,971,520 bytes.

Why QE passed: the script uses `Limit-EventLog`, the classic API, which writes
`HKLM:\SYSTEM\CurrentControlSet\Services\EventLog\System\MaxSize`. The QE lab's check
(`validate-event-log.yml`, line 5) reads `Get-WinEvent -ListLog System`, the modern API, which
still said 20,971,520 after the change. Read three ways on SRV01 right after run B:

| Read through | System log size |
|---|---|
| registry `MaxSize` | 67,108,864 |
| `Get-EventLog -List` (classic) | 65,536 KB |
| `Get-WinEvent -ListLog` (modern — what QE reads) | 20,971,520 |

The change was real; the check that pins that exact setting could not see it. Kept here, not
deleted, because it is the clearest evidence this project has of ADR 0042's accepted weakness —
"the QE suite only sees what it checks" — in its sharpest form: the right setting, read through
the wrong door. `promoted/` stays a list a person can trust.

SRV01 was restored to `pre-P06-step5` at the end of the session (playbook step 4).
