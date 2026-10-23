[CmdletBinding(SupportsShouldProcess)]
param()
# A native executable: -WhatIf cannot stop it, so the dry run would really write.
auditpol /set /subcategory:"Logon" /success:enable /failure:enable
