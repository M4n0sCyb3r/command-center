# Time drift on a domain member

## Symptoms

Authentication fails intermittently with clock skew errors. The account is
valid and the password is correct, but the ticket is rejected. Drift above five
minutes breaks Kerberos outright; smaller drift produces failures that look
random.

## Procedure

Check the time service status and the configured time source. A domain member
must take time from the domain hierarchy, never from an external server
directly. Resynchronise, then confirm the offset is under one second.

## Escalation

If the member is configured correctly and drift persists, the fault is upstream
at the domain controller holding the PDC emulator role. Stop working on the
member.
