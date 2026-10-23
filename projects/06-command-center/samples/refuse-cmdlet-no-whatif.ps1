[CmdletBinding(SupportsShouldProcess)]
param()
# Write-EventLog changes state (it writes an event) but has no -WhatIf: it would run for real in a dry run.
Write-EventLog -LogName Application -Source Application -EventId 1000 -Message "P06 dry-run probe"
