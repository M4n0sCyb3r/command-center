[CmdletBinding(SupportsShouldProcess)]
param()
# Reaches another machine: SRV01 cannot rehearse what this does to it.
Invoke-Command -ComputerName dc01 -ScriptBlock { Get-Service -Name W32Time }
