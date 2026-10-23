<#
.SYNOPSIS
    Exports the members of the local Administrators group to a CSV file.
.NOTES
    Reference script 06 - Project 3 review set (ADR 0028). Generic lab example;
    placeholder names only. Never run against a real environment.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$OutputPath
)

$parent = Split-Path -Path $OutputPath -Parent
if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) {
    Write-Error -Message "Output folder $parent does not exist"
    exit 2
}

try {
    $members = Get-LocalGroupMember -Group 'Administrators'
}
catch {
    Write-Error -Message "Could not read local Administrators: $($_.Exception.Message)"
    exit 2
}

$members |
    Select-Object -Property Name, ObjectClass, PrincipalSource |
    Export-Csv -LiteralPath $OutputPath -NoTypeInformation -Encoding UTF8

Write-Output "Exported $(@($members).Count) member(s) to $OutputPath"
