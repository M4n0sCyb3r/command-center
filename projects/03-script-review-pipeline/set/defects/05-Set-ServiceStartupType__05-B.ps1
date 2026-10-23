<#
.SYNOPSIS
    Sets the startup type of named services, refusing to disable protected ones.
.NOTES
    Reference script 05 - Project 3 review set (ADR 0028). Generic lab example;
    placeholder names only. Never run against a real environment.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [string[]]$Name,

    [Parameter(Mandatory)]
    [ValidateSet('Automatic', 'Manual', 'Disabled')]
    [string]$StartupType
)

$protected = @('EventLog', 'RpcSs', 'W32Time')

foreach ($svcName in $Name) {
    if ($protected -contains $svcName -and $StartupType -eq 'Disabled') {
        Write-Warning -Message "Refusing to disable protected service $svcName"
        continue
    }
    $svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
    if ($null -eq $svc) {
        Write-Warning -Message "Service $svcName not found"
        continue
    }
    if ($PSCmdlet.ShouldProcess($svcName, "Set startup type to $StartupType")) {
        Set-Service -Name $svcName -StartupType $StartupType -ErrorAction Stop
        Write-Output "$svcName startup type set to $StartupType"
    }
}
