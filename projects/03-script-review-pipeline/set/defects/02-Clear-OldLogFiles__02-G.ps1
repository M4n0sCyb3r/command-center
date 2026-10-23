<#
.SYNOPSIS
    Deletes log files older than a given age under one log folder.
.NOTES
    Reference script 02 - Project 3 review set (ADR 0028). Generic lab example;
    placeholder names only. Never run against a real environment.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Container })]
    [string]$LogRoot,

    [ValidateRange(1, 3650)]
    [int]$OlderThanDays = 30,

    [string]$Filter = '*.log'
)

$cutoff = (Get-Date).AddDays(-$OlderThanDays)
$files = Get-ChildItem -LiteralPath $LogRoot -Filter $Filter -File -Recurse -ErrorAction Stop |
    Where-Object { $_.LastWriteTime -gt $cutoff }

$removed = 0
foreach ($file in $files) {
    if ($PSCmdlet.ShouldProcess($file.FullName, 'Remove-Item')) {
        Remove-Item -LiteralPath $file.FullName -Force -ErrorAction Stop
        $removed++
    }
}

Write-Output "Removed $removed file(s) older than $OlderThanDays day(s) from $LogRoot"
