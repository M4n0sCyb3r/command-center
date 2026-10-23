<#
.SYNOPSIS
    Reports whether the machine has a reboot pending; exits 1 if it does.
.NOTES
    Reference script 10 - Project 3 review set (ADR 0028). Generic lab example;
    placeholder names only. Never run against a real environment.
#>
[CmdletBinding()]
param()

$reasons = @()

if (Test-Path -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') {
    $reasons += 'Component Based Servicing'
}
if (Test-Path -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') {
    $reasons += 'Windows Update'
}
$sessionManager = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -ErrorAction SilentlyContinue
if ($null -ne $sessionManager -and $null -ne $sessionManager.PendingFileRenameOperations) {
    $reasons += 'Pending file rename operations'
}

if ($reasons.Count -gt 0) {
    Write-Output "Reboot pending: $($reasons -join ', ')"
    exit 1
}
Write-Output 'No reboot pending'
exit 0
