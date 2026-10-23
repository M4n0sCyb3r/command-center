# Restarting a stuck Windows service

## Symptoms

A service reports "stopped" while the host is otherwise healthy. Dependent
features fail quietly: print jobs queue with no error, scheduled work never
runs. The event log usually holds one entry at the moment of failure and
nothing afterward, because a stopped service writes nothing.

## Procedure

Query the service first; never restart blind. Confirm the current status and
the startup type before touching it. Restart the service, then query again and
confirm the status actually changed. A restart command that returns success is
not evidence that the service is running.

## Recovery options

If the service stops again within minutes, stop restarting it. Set the recovery
action to restart on first failure only, and collect the failure event before
the next attempt. A service that stops repeatedly is reporting a cause that
lives somewhere else, usually a missing dependency or a changed permission.
