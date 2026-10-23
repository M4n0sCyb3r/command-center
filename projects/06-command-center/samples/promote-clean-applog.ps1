[CmdletBinding(SupportsShouldProcess)]
param()
# Raise the Application log to 32 MB so application errors are kept longer during patch windows.
Limit-EventLog -LogName Application -MaximumSize 32MB
