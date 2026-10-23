<#
.SYNOPSIS
    Removes local user profiles unused for a given number of days, keeping named accounts.
.NOTES
    Reference script 11 - Project 3 review set (ADR 0028). Generic lab example;
    placeholder names only. Never run against a real environment.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateRange(30, 3650)]
    [int]$InactiveDays = 90,

    [string[]]$KeepUser = @('Administrator', 'labadmin')
)

$cutoff = (Get-Date).AddDays(-$InactiveDays)
$userProfiles = Get-CimInstance -ClassName Win32_UserProfile -Filter 'Special = False AND Loaded = False'

foreach ($userProfile in $userProfiles) {
    $userName = Split-Path -Path $userProfile.LocalPath -Parent
    if ($KeepUser -contains $userName) {
        continue
    }
    if ($null -eq $userProfile.LastUseTime -or $userProfile.LastUseTime -ge $cutoff) {
        continue
    }
    if ($PSCmdlet.ShouldProcess($userProfile.LocalPath, 'Remove user profile')) {
        Remove-CimInstance -InputObject $userProfile -ErrorAction Stop
        Write-Output "Removed profile $($userProfile.LocalPath)"
    }
}
