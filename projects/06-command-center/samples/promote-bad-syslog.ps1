[CmdletBinding(SupportsShouldProcess)]
param()
# Raise the System log to 64 MB so boot and driver errors are kept longer for troubleshooting.
Limit-EventLog -LogName System -MaximumSize 64MB
