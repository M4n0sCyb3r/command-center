<#
.SYNOPSIS
    Checks the WinRM service, a listener and the inbound firewall rule; exits 1 on any gap.
.NOTES
    Reference script 08 - Project 3 review set (ADR 0028). Generic lab example;
    placeholder names only. Never run against a real environment.
#>
[CmdletBinding()]
param()

$problems = @()

$svc = Get-Service -Name 'WinRM' -ErrorAction SilentlyContinue
if ($null -eq $svc) {
    $problems += 'WinRM service is not installed'
}
elseif ($svc.Status -ne 'Running') {
    $problems += "WinRM service is $($svc.Status)"
}

$listeners = @(Get-ChildItem -Path 'WSMan:\localhost\Listener' -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) {
    $problems += 'No WinRM listener is configured'
}

$rule = Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP' -ErrorAction SilentlyContinue
if ($null -eq $rule -or $rule.Enabled -ne 'True') {
    $problems += 'Firewall rule WINRM-HTTP-In-TCP is missing or disabled'
}

if ($problems.Count -gt 0) {
    $problems | ForEach-Object { Write-Output "FAIL: $_" }
    exit 1
}
Write-Output 'PASS: WinRM service, listener and firewall rule are in place'
exit 0
