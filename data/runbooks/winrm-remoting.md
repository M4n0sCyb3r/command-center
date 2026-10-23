# WinRM remoting failures

## Symptoms

Remote commands fail to connect while the host answers ping and console logon
works. Automation that ran yesterday fails today with no change to the script,
which points at the listener or the firewall rather than at the code.

## Procedure

Confirm the WinRM service is running, then confirm a listener exists and is
bound to the expected port. Test connectivity from the control node, not from
the target, because a test run on the target proves nothing about the path.

## Firewall notes

The rule must be scoped to the management network. A rule opened to any address
is a finding at audit even when remoting works, and "it works" is not the
standard being measured.
