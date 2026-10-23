<#
.SYNOPSIS
    Starts services set to start automatically that are currently stopped.
.NOTES
    Reference script 01 - Project 3 review set (ADR 0028). Generic lab example;
    placeholder names only. Never run against a real environment.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string[]]$Exclude = @('RemoteRegistry', 'sppsvc')
)

$failed = 0
$candidates = Get-CimInstance -ClassName Win32_Service -Filter "State = 'Stopped'"

foreach ($svc in $candidates) {
    if ($Exclude -contains $svc.Name) {
        Write-Verbose -Message "Skipping excluded service $($svc.Name)"
        continue
    }
    if ($PSCmdlet.ShouldProcess($svc.Name, 'Start-Service')) {
        try {
            Start-Service -Name $svc.Name -ErrorAction Stop
            Write-Output "Started $($svc.Name)"
        }
        catch {
            Write-Warning -Message "Could not start $($svc.Name): $($_.Exception.Message)"
            $failed++
        }
    }
}

if ($failed -gt 0) {
    exit 1
}
exit 0
