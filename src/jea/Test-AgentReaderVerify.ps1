<#
.SYNOPSIS
Offline tests for P05 build step 7: the verifier's two reads on AgentReader.

.DESCRIPTION
The machine reads (Get-CimInstance, the registry) cannot run off SRV01, so - as with the
audit parser - the CONVERSIONS are separate pure functions, tested here on sample values.
The surface checks are the other half: what the module exports, what each role file shows,
and which session file maps the new role. The pick being protected (Jose, 2026-10-20) is
"SRV01 only" - case 4 fails if DC01's session file ever names AgentReaderVerify.

Exit code 0 = all cases pass. Runs on PowerShell 7 and Windows PowerShell 5.1.
#>

$ErrorActionPreference = 'Stop'
$module = Import-Module (Join-Path $PSScriptRoot 'AgentReader\AgentReader.psm1') -Force -PassThru
$failures = New-Object System.Collections.Generic.List[string]

function Check([string] $Label, [bool] $Ok, [string] $Detail = '') {
    if ($Ok) { Write-Host "  ok   $Label" } else { Write-Host "  FAIL $Label  $Detail"; $failures.Add($Label) }
}
function Throws([scriptblock] $Block) { try { $null = & $Block; return $null } catch { return "$($_.Exception.Message)" } }
function Mode($m)  { & $module { param($x) ConvertFrom-ServiceStartMode -StartMode $x } $m }
function State($s) { & $module { param($x) ConvertFrom-ServiceStateName -State $x } $s }
function Reg($v, $k) { & $module { param($a, $b) ConvertFrom-RegistryReading -Value $a -Kind $b } $v $k }

Write-Host 'case 1  start mode -> the baseline word (Win32_Service says Auto, not Automatic)'
foreach ($pair in @(@('Auto', 'automatic'), @('Manual', 'manual'), @('Disabled', 'disabled'))) {
    $got = Mode $pair[0]
    Check "$($pair[0]) -> $($pair[1])" ($got -ceq $pair[1]) $got
}
$msg = Throws { Mode 'Automatic' }
Check "Get-Service's word 'Automatic' -> refused (this reader is NOT the writer's code path)" ($msg -like '*unrecognised*') $msg
$msg = Throws { Mode 'Boot' }
Check 'Boot -> refused, not guessed' ($msg -like '*unrecognised*') $msg
$msg = Throws { Mode '' }
Check 'empty -> refused' ($msg -like '*unrecognised*') $msg

Write-Host "`ncase 2  state -> lower case; transient states REPORTED, not refused"
Check 'Running -> running' ((State 'Running') -ceq 'running')
Check 'Stopped -> stopped' ((State 'Stopped') -ceq 'stopped')
Check 'Start Pending -> start pending (the settling loop needs to see it)' ((State 'Start Pending') -ceq 'start pending')
$msg = Throws { State 'running' }
Check 'wrong case from the source -> refused (the source is exact)' ($msg -like '*unrecognised*') $msg
$msg = Throws { State 'Sleeping' }
Check 'an unknown state -> refused' ($msg -like '*unrecognised*') $msg

Write-Host "`ncase 3  registry reading -> integer; absent or wrong type refused"
$got = Reg 1 'DWord'
Check 'DWORD 1 -> int 1' ($got -is [int] -and $got -eq 1) "$got"
$got = Reg 0 'DWord'
Check 'DWORD 0 -> int 0 (zero is a value, not absent)' ($got -is [int] -and $got -eq 0) "$got"
$msg = Throws { Reg $null 'absent' }
Check 'absent -> refused' ($msg -like '*not present*') $msg
$msg = Throws { Reg '1' 'String' }
Check "REG_SZ '1' -> refused (the writer's reader would have accepted it)" ($msg -like '*expected DWord*') $msg

Write-Host "`ncase 4  the surface: exports, role files, and which server maps what"
$exported = @($module.ExportedFunctions.Keys | Sort-Object)
Check 'exactly three exports' (($exported -join ',') -eq 'Get-AgentAuditPolicy,Get-AgentRegistryState,Get-AgentServiceState') ($exported -join ',')
foreach ($name in 'Get-AgentServiceState', 'Get-AgentRegistryState') {
    $cmd = Get-Command -Module $module.Name -Name $name
    $common = [System.Management.Automation.PSCmdlet]::CommonParameters + [System.Management.Automation.PSCmdlet]::OptionalCommonParameters
    $own = @($cmd.Parameters.Keys | Where-Object { $common -notcontains $_ })
    Check "$name takes no parameters (the caller cannot name an item)" ($own.Count -eq 0) ($own -join ',')
}
$psd = Import-PowerShellDataFile (Join-Path $PSScriptRoot 'AgentReader\AgentReader.psd1')
Check 'manifest exports exactly the module exports' ((@($psd.FunctionsToExport | Sort-Object) -join ',') -eq ($exported -join ','))
$base = Import-PowerShellDataFile (Join-Path $PSScriptRoot 'AgentReader\RoleCapabilities\AgentReader.psrc')
Check 'AgentReader role still shows only the audit read (P01 unchanged)' ((@($base.VisibleFunctions) -join ',') -eq 'Get-AgentAuditPolicy')
$verify = Import-PowerShellDataFile (Join-Path $PSScriptRoot 'AgentReader\RoleCapabilities\AgentReaderVerify.psrc')
Check 'AgentReaderVerify shows exactly the two reads' ((@($verify.VisibleFunctions | Sort-Object) -join ',') -eq 'Get-AgentRegistryState,Get-AgentServiceState')
$srv = Import-PowerShellDataFile (Join-Path $PSScriptRoot 'AgentReader.pssc')
$dc  = Import-PowerShellDataFile (Join-Path $PSScriptRoot 'AgentReader-DC.pssc')
$srvRoles = @($srv.RoleDefinitions['CORP\svc-agent-ro'].RoleCapabilities)
$dcRoles  = @($dc.RoleDefinitions['CORP\svc-agent-ro'].RoleCapabilities)
Check 'SRV01 session file maps AgentReader + AgentReaderVerify' ((($srvRoles | Sort-Object) -join ',') -eq 'AgentReader,AgentReaderVerify') ($srvRoles -join ',')
Check "DC01 session file does NOT map AgentReaderVerify (Jose's pick: SRV01 only)" ($dcRoles -notcontains 'AgentReaderVerify') ($dcRoles -join ',')
Check 'svc-agent-rw is in neither reader session file' ($srv.RoleDefinitions.Keys -notcontains 'CORP\svc-agent-rw' -and $dc.RoleDefinitions.Keys -notcontains 'CORP\svc-agent-rw')

Write-Host "`ncase 5  independence: the verifier's reads are not the writer's code"
$reader = Get-Content -Raw (Join-Path $PSScriptRoot 'AgentReader\AgentReader.psm1')
# Code only: help blocks (<# ... #>) and line comments may NAME a cmdlet without calling it.
$reader = $reader -replace '(?s)<#.*?#>', ''
Check 'the reader does not call Get-Service (the writer does)' ($reader -notmatch '(?m)^[^#]*\bGet-Service\b')
Check 'the reader does not call Get-CimInstance (not loaded in the JEA session - SRV01, 2026-10-20)' ($reader -notmatch '(?m)^[^#]*\bGet-CimInstance\b')
Check 'the reader opens the registry key read-only only' (($reader -notmatch 'OpenSubKey\([^)]*\$true'))

if ($failures.Count) { Write-Host "`nFAILED ($($failures.Count)): $($failures -join '; ')"; exit 1 }
Write-Host "`nall checks passed"; exit 0
