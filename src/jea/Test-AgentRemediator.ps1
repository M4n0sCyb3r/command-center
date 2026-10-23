<#
.SYNOPSIS
Offline tests for the AgentRemediator module - no server, no admin rights, no endpoint.

.DESCRIPTION
Everything that DECIDES something is a pure private function, so it is tested here on
sample values; only the three thin calls to the machine (Get/Set-Service, auditpol, the
registry) are left for the endpoint proof.

  case 1  the service plan: ends 'running' -> start type FIRST; ends 'stopped' -> stop
          FIRST; nothing to do -> no steps; disabled + running -> refused
  case 2  the change list: every requested field, before/after, 'no change' when equal
  case 3  auditpol: a real one-row CSV parses; the privilege-error text (P01's capture)
          is refused with auditpol's own words; a different GUID is refused
  case 4  auditpol /set arguments for each of the four settings
  case 5  the export list is exactly the three remediation functions, and every
          parameter of every exported function is a ValidateSet or a switch -
          nothing a caller sends can be a name, a path or free text
  case 6  every state in a result is the same shape (Before, ReportedAfter: objects, not
          hashtables - found on SRV01, where ReportedAfter crossed PSRP as '{value}')

Private functions are reached as in Test-AgentReaderParser.ps1: Import-Module -PassThru,
then `& $module { ... }`, which runs under the module's own StrictMode.

Run (Windows PowerShell, no admin):
  powershell -ExecutionPolicy Bypass -File .\src\jea\Test-AgentRemediator.ps1
Exit 0 = all pass.
#>

$ErrorActionPreference = 'Stop'
$module = Import-Module (Join-Path $PSScriptRoot 'AgentRemediator\AgentRemediator.psm1') -Force -PassThru
$failures = New-Object System.Collections.Generic.List[string]

function Check([string] $Label, [bool] $Ok, [string] $Detail = '') {
    if ($Ok) { Write-Host "  ok   $Label" } else { Write-Host "  FAIL $Label  $Detail"; $failures.Add($Label) }
}
function Plan($before, $requested) { & $module { param($b, $r) Get-ServicePlan -Before $b -Requested $r } $before $requested }
function Throws([scriptblock] $Block) { try { $null = & $Block; return $null } catch { return "$($_.Exception.Message)" } }

Write-Host 'case 1  the service plan (order matters: a disabled service cannot be started)'
$p = Plan @{ start_type = 'disabled'; status = 'stopped' } @{ start_type = 'automatic'; status = 'running' }
Check 'disabled/stopped -> automatic/running: start type FIRST, then start' (($p -join ',') -eq 'starttype:automatic,start') ($p -join ',')
$p = Plan @{ start_type = 'automatic'; status = 'running' } @{ start_type = 'disabled'; status = 'stopped' }
Check 'automatic/running -> disabled/stopped: stop FIRST, then start type' (($p -join ',') -eq 'stop,starttype:disabled') ($p -join ',')
$p = Plan @{ start_type = 'disabled'; status = 'stopped' } @{ start_type = 'disabled'; status = 'stopped' }
Check 'already there -> no steps' (@($p).Count -eq 0) ($p -join ',')
$p = Plan @{ start_type = 'manual'; status = 'running' } @{ status = 'stopped' }
Check 'status only -> stop only' (($p -join ',') -eq 'stop') ($p -join ',')
$msg = Throws { Plan @{ start_type = 'automatic'; status = 'stopped' } @{ start_type = 'disabled'; status = 'running' } }
Check 'disabled + running -> refused' ($msg -like '*refused*contradiction*') $msg
$msg = Throws { Plan @{ start_type = 'automatic'; status = 'running' } @{ start_type = 'disabled' } }
Check 'disable while it stays running -> refused (would leave a contradiction)' ($msg -like '*refused*') $msg

Write-Host "`ncase 2  the change list"
$c = @(& $module { New-ChangeList -Before @{ start_type = 'automatic'; status = 'running' } -Requested @{ start_type = 'disabled'; status = 'running' } })
Check 'one row per requested field, sorted' (($c | ForEach-Object Field) -join ',' -eq 'start_type,status')
Check 'changed field -> set' ($c[0].Effect -eq 'set' -and $c[0].Before -eq 'automatic' -and $c[0].After -eq 'disabled')
Check 'unchanged field -> no change, still listed' ($c[1].Effect -eq 'no change')
$c = @(& $module { New-ChangeList -Before @{ value = 1 } -Requested @{ value = 1 } })
Check 'int 1 vs int 1 -> no change' ($c[0].Effect -eq 'no change')
$msg = Throws { & $module { New-ChangeList -Before @{ value = 1 } -Requested @{ other = 1 } } }
Check 'a field with no before-value -> refused' ($msg -like '*no before-value*') $msg

Write-Host "`ncase 3  auditpol one-subcategory CSV"
$good = @('', 'Machine Name,Policy Target,Subcategory,Subcategory GUID,Inclusion Setting,Exclusion Setting',
          'SRV01,System,Logon,{0CCE9215-69AE-11D9-BED3-505054503030},Success and Failure,', '')
$s = & $module { param($l) ConvertFrom-AuditPolSubcategoryCsv -Line $l } $good
Check 'a real row -> Success and Failure' ($s -eq 'Success and Failure') $s
$priv = @('Error 0x00000522 occurred:', 'A required privilege is not held by the client.')
$msg = Throws { & $module { param($l) ConvertFrom-AuditPolSubcategoryCsv -Line $l } $priv }
Check "privilege error -> refused, quoting auditpol's own words" ($msg -like '*required privilege*') $msg
$other = $good -replace '0CCE9215', '0CCE9216'
$msg = Throws { & $module { param($l) ConvertFrom-AuditPolSubcategoryCsv -Line $l } $other }
Check 'a different subcategory GUID -> refused' ($msg -like '*expected*0CCE9215*') $msg
$weird = $good -replace 'Success and Failure', 'Sometimes'
$msg = Throws { & $module { param($l) ConvertFrom-AuditPolSubcategoryCsv -Line $l } $weird }
Check 'an unknown setting -> refused, not guessed' ($msg -like '*unrecognised*') $msg

Write-Host "`ncase 4  auditpol /set arguments"
foreach ($pair in @(@('Success and Failure', 'enable', 'enable'), @('Success', 'enable', 'disable'),
                    @('Failure', 'disable', 'enable'), @('No Auditing', 'disable', 'disable'))) {
    $a = & $module { param($s) Get-AuditPolSetArguments -Setting $s } $pair[0]
    $want = "/set /subcategory:{0CCE9215-69AE-11D9-BED3-505054503030} /success:$($pair[1]) /failure:$($pair[2])"
    Check "$($pair[0]) -> $($pair[1])/$($pair[2])" (($a -join ' ') -eq $want) ($a -join ' ')
}

Write-Host "`ncase 5  the exported surface"
$exported = @($module.ExportedFunctions.Keys | Sort-Object)
Check 'exactly the three remediation functions' (($exported -join ',') -eq 'Set-AgentAuditSetting,Set-AgentRegistryValue,Set-AgentServiceConfig') ($exported -join ',')
$common = [System.Management.Automation.PSCmdlet]::CommonParameters + [System.Management.Automation.PSCmdlet]::OptionalCommonParameters
foreach ($name in $exported) {
    $cmd = Get-Command -Module $module.Name -Name $name
    foreach ($param in $cmd.Parameters.Values) {
        if ($common -contains $param.Name) { continue }
        $isSwitch = $param.ParameterType -eq [switch]
        $hasSet = @($param.Attributes | Where-Object { $_ -is [System.Management.Automation.ValidateSetAttribute] }).Count -gt 0
        Check "$name -$($param.Name) is a ValidateSet or a switch" ($isSwitch -or $hasSet)
    }
}
$psrc = Import-PowerShellDataFile (Join-Path $PSScriptRoot 'AgentRemediator\RoleCapabilities\AgentRemediator.psrc')
Check 'the role file shows exactly the exported functions' ((@($psrc.VisibleFunctions | Sort-Object) -join ',') -eq ($exported -join ','))
$pssc = Import-PowerShellDataFile (Join-Path $PSScriptRoot 'AgentRemediator.pssc')
Check 'the session file names svc-agent-rw and NOT the verifier' ($pssc.RoleDefinitions.Keys -contains 'CORP\svc-agent-rw' -and $pssc.RoleDefinitions.Keys -notcontains 'CORP\svc-agent-ro')
Check 'NoLanguage, RestrictedRemoteServer, transcripts on' ($pssc.LanguageMode -eq 'NoLanguage' -and $pssc.SessionType -eq 'RestrictedRemoteServer' -and $pssc.TranscriptDirectory)

Write-Host "`ncase 6  one shape for every state in a result (found on SRV01: ReportedAfter was a raw hashtable)"
$res = & $module { New-RemediationResult 'registry:rdp-nla' @{ value = 1 } @{ value = 0 } @() $true @{ value = 0 } }
Check 'Before is an object' ($res.Before -is [System.Management.Automation.PSCustomObject])
Check 'ReportedAfter is an object, same shape as Before' ($res.ReportedAfter -is [System.Management.Automation.PSCustomObject] -and $res.ReportedAfter.value -eq 0)
$pre = & $module { New-RemediationResult 'registry:rdp-nla' @{ value = 1 } @{ value = 1 } @() $false $null }
Check 'a preview has no ReportedAfter at all' ($null -eq $pre.ReportedAfter -and -not $pre.Applied)

if ($failures.Count) { Write-Host "`nFAILED ($($failures.Count)): $($failures -join '; ')"; exit 1 }
Write-Host "`nall checks passed"; exit 0
