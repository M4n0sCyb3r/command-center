# Monthly patching window

## Symptoms

The update service sits in a manual start state and patches never install.
Compliance reporting shows the host as missing updates while the host itself
reports no error, because nothing ran to produce one.

## Procedure

Confirm the update service start type before the window opens. Stage updates,
reboot inside the agreed window, then confirm the build number changed. A
reboot that does not change the build number applied nothing.

## Change control

No patch is applied outside the agreed window without a written exception. The
exception names the requester, the reason and the rollback step. An
undocumented patch is indistinguishable from an incident.
