# No [CmdletBinding(SupportsShouldProcess)]: -WhatIf would never reach the cmdlets inside.
Set-Service -Name Spooler -StartupType Disabled
