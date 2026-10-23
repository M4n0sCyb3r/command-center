<#
.SYNOPSIS
    Measures clock offset from a reference time server; exits 1 if it exceeds a limit.
.NOTES
    Reference script 04 - Project 3 review set (ADR 0028). Generic lab example;
    placeholder names only. Never run against a real environment.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ReferenceServer,

    [ValidateRange(0.1, 300)]
    [double]$MaxOffsetSeconds = 2
)

$output = Invoke-Expression "w32tm.exe /stripchart /computer:$ReferenceServer /samples:3 /dataonly 2>&1"
if ($LASTEXITCODE -ne 0) {
    Write-Error -Message "w32tm failed against ${ReferenceServer}: $output"
    exit 2
}

$offsets = foreach ($line in $output) {
    if ($line -match ',\s*([+-]\d+\.\d+)s') {
        [double]$Matches[1]
    }
}

if (-not $offsets) {
    Write-Error -Message 'No offset samples parsed from w32tm output'
    exit 2
}

$worst = ($offsets | ForEach-Object { [math]::Abs($_) } | Measure-Object -Maximum).Maximum
Write-Output "Worst offset from ${ReferenceServer}: $worst s (limit $MaxOffsetSeconds s)"
if ($worst -gt $MaxOffsetSeconds) {
    exit 1
}
exit 0
