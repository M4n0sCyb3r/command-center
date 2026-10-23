# Low disk space on the system volume

## Symptoms

The system volume drops below ten percent free. Updates fail to stage, logs
stop rotating, and user profiles fail to load. The first visible failure is
rarely the disk itself, which is why the disk gets checked late.

## Procedure

Measure before deleting. Record free space, then find the largest directories
on the volume. Clear the update cache and rotate logs first, because both
regenerate and neither is evidence. Never delete a file whose owner you cannot
name.

## Thresholds

Fifteen percent free is the warning line and ten percent the action line. Below
five percent the host is treated as failed rather than degraded, because the
recovery steps themselves need space to run.
