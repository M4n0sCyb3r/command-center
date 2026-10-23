<#
.SYNOPSIS
    Reports fixed volumes below a free-space threshold; exits 1 if any are low.
.NOTES
    Reference script 03 - Project 3 review set (ADR 0028). Generic lab example;
    placeholder names only. Never run against a real environment.
#>
[CmdletBinding()]
param(
    [ValidateRange(1, 99)]
    [int]$ThresholdPercent = 15
)

$volumes = Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType = 3'
$low = @()

foreach ($vol in $volumes) {
    if ($vol.Size -le 0) {
        continue
    }
    $freePercent = [math]::Round(($vol.FreeSpace / $vol.Size) * 100, 1)
    if ($freePercent -lt $ThresholdPercent) {
        $low += [pscustomobject]@{
            Drive       = $vol.DeviceID
            FreePercent = $freePercent
            FreeGB      = [math]::Round($vol.FreeSpace / 1GB, 1)
        }
    }
}

if ($low.Count -gt 0) {
    $low | Format-Table -AutoSize | Out-String | Write-Output
    exit 1
}
Write-Output "All fixed volumes have at least $ThresholdPercent percent free"
exit 0
