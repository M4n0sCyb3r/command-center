<#
.SYNOPSIS
    Registers a daily scheduled task that runs a cleanup script under a given account.
.NOTES
    Reference script 09 - Project 3 review set (ADR 0028). Generic lab example;
    placeholder names only. Never run against a real environment.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [string]$ScriptPath,

    [Parameter(Mandatory)]
    [string]$UserName,

    [Parameter(Mandatory)]
    [string]$Password,

    [ValidatePattern('^\d{2}:\d{2}$')]
    [string]$At = '02:00',

    [string]$TaskName = 'LabLogCleanup'
)

if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
    Write-Error -Message "Script not found: $ScriptPath"
    exit 2
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -File `"$ScriptPath`""
$trigger = New-ScheduledTaskTrigger -Daily -At $At

if ($PSCmdlet.ShouldProcess($TaskName, 'Register-ScheduledTask')) {
    $register = @{
        TaskName    = $TaskName
        Action      = $action
        Trigger     = $trigger
        User        = $UserName
        Password    = $Password
        RunLevel    = 'Limited'
        ErrorAction = 'Stop'
    }
    Register-ScheduledTask @register | Out-Null
    Write-Output "Registered $TaskName to run $ScriptPath daily at $At"
}
