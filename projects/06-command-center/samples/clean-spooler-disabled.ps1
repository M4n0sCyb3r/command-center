[CmdletBinding(SupportsShouldProcess)]
param()
# Keep the Print Spooler disabled and stopped - SRV01's baseline, so a no-op on a clean SRV01.
Set-Service -Name Spooler -StartupType Disabled
Stop-Service -Name Spooler -Force
