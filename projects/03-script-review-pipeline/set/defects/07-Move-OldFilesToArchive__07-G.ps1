<#
.SYNOPSIS
    Moves all but the newest N files from a folder into an archive folder.
.NOTES
    Reference script 07 - Project 3 review set (ADR 0028). Generic lab example;
    placeholder names only. Never run against a real environment.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [string]$SourcePath,

    [Parameter(Mandatory)]
    [string]$ArchivePath,

    [ValidateRange(1, 1000)]
    [int]$KeepNewest = 10
)

foreach ($folder in @($SourcePath, $ArchivePath)) {
    if (-not (Test-Path -LiteralPath $folder -PathType Container)) {
        Write-Error -Message "Folder not found: $folder"
        exit 2
    }
}

$files = @(Get-ChildItem -LiteralPath $SourcePath -File |
    Sort-Object -Property LastWriteTime)
$toMove = $files | Select-Object -Skip $KeepNewest

foreach ($file in $toMove) {
    $destination = Join-Path -Path $ArchivePath -ChildPath $file.Name
    if (Test-Path -LiteralPath $destination) {
        Write-Warning -Message "Skipping $($file.Name): already in archive"
        continue
    }
    if ($PSCmdlet.ShouldProcess($file.FullName, "Move to $ArchivePath")) {
        Move-Item -LiteralPath $file.FullName -Destination $destination -ErrorAction Stop
    }
}

Write-Output "Kept $([math]::Min($files.Count, $KeepNewest)) newest file(s) in $SourcePath"
