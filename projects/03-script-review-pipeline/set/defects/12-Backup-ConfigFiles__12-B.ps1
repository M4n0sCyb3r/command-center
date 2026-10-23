<#
.SYNOPSIS
    Copies named config files into a timestamped backup folder and verifies each copy by hash.
.NOTES
    Reference script 12 - Project 3 review set (ADR 0028). Generic lab example;
    placeholder names only. Never run against a real environment.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [string[]]$Path,

    [Parameter(Mandatory)]
    [string]$Destination
)

if (-not (Test-Path -LiteralPath $Destination -PathType Container)) {
    Write-Error -Message "Backup destination not found: $Destination"
    exit 2
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$target = Join-Path -Path $Destination -ChildPath "config-$stamp"
$failed = 0

if ($PSCmdlet.ShouldProcess($target, 'Create backup folder')) {
    Remove-Item -LiteralPath $Destination -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $target -ErrorAction Stop | Out-Null
    foreach ($file in $Path) {
        try {
            $copy = Copy-Item -LiteralPath $file -Destination $target -PassThru -ErrorAction Stop
            $sourceHash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
            $copyHash = (Get-FileHash -LiteralPath $copy.FullName -Algorithm SHA256).Hash
            if ($sourceHash -ne $copyHash) {
                Write-Warning -Message "Hash mismatch for $file"
                $failed++
            }
        }
        catch {
            Write-Warning -Message "Could not back up ${file}: $($_.Exception.Message)"
            $failed++
        }
    }
}

if ($failed -gt 0) {
    exit 1
}
Write-Output "Backed up $($Path.Count) file(s) to $target"
exit 0
