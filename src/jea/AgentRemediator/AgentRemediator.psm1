# AgentRemediator - the functions the P05 write endpoint exposes (ADRs 0034, 0035).
#
# READ THIS BEFORE ADDING A FUNCTION. Everything in AgentReader.psm1's header applies
# here, and this module is MORE dangerous: its bodies run as the endpoint's virtual
# account, a local administrator on SRV01, and they WRITE. The visible-function list in
# AgentRemediator.psrc is the blast radius (ADR 0034); the code below is the only thing
# that decides what each visible function actually does. So every function here must:
#   - change exactly ONE fixed item, named in this file - never one the caller names;
#   - take only values from a fixed ValidateSet - no path, name, script block or free text;
#   - offer -Preview, which changes NOTHING and says what would change;
#   - capture the before-state and return it with the result, so the caller can roll back
#     THROUGH THE SAME FUNCTION (ADR 0034: "rollback through the same door");
#   - call executables by full path, check exit codes AND output shape, fail loudly.
#
# JEA sessions have NO PROVIDERS (P01, 2026-09-18): no HKLM:, no Env:. The registry is
# reached through [Microsoft.Win32.Registry]; auditpol through [System.Environment].
#
# What a function RETURNS is a report. The agent records it and never uses it as
# verification - the verifier reads the machine through AgentReader as svc-agent-ro
# (ADR 0036). That is why a lying remediator (ADR 0035's sabotage) can be caught.
#
# The text helpers (Get-SafePropertyValue, Test-IsTypeNameOnly, ConvertTo-NativeOutputText)
# are COPIED from AgentReader.psm1, not shared: each endpoint loads one self-contained
# module, so a change to the reader can never alter the writer. Keep the copies in step.

Set-StrictMode -Version 2.0

# --- the three fixed targets (data/remediation-baseline.json; ADR 0035) ---------------
$script:ServiceName   = 'Spooler'
$script:AuditGuid     = '{0CCE9215-69AE-11D9-BED3-505054503030}'   # Logon/Logoff \ Logon
$script:AuditName     = 'Logon'
$script:RegistryKey   = 'SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'
$script:RegistryValue = 'UserAuthentication'

# --- text helpers, copied from AgentReader.psm1 (see header) --------------------------

function Get-SafePropertyValue {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][AllowNull()] $InputObject,
          [Parameter(Mandatory = $true)][string[]] $Path)
    $current = $InputObject
    foreach ($name in $Path) {
        if ($null -eq $current) { return $null }
        $property = $current.psobject.Properties[$name]
        if ($null -eq $property) { return $null }
        $current = $property.Value
    }
    return $current
}

function Test-IsTypeNameOnly {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string] $Text)
    return [bool]("$Text".Trim() -match '^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$')
}

function ConvertTo-NativeOutputText {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowNull()][object[]] $Output)
    $paths = @(, @('Exception', 'Message'), @('TargetObject'), @('ErrorDetails', 'Message'))
    foreach ($item in $Output) {
        if ($null -eq $item) { continue }
        if ($item -is [string]) { $item; continue }
        $sawText = $false
        $text = $null
        foreach ($path in $paths) {
            $candidate = "$(Get-SafePropertyValue -InputObject $item -Path $path)".Trim()
            if (-not $candidate) { continue }
            $sawText = $true
            if (Test-IsTypeNameOnly -Text $candidate) { continue }
            $text = $candidate
            break
        }
        if ($null -eq $text) {
            $candidate = "$item".Trim()
            if ($candidate -and -not (Test-IsTypeNameOnly -Text $candidate)) {
                $sawText = $true
                $text = $candidate
            }
        }
        if ($null -eq $text) {
            if ($sawText) { $text = "[no readable text from a $($item.GetType().Name)]" } else { $text = '' }
        }
        $text
    }
}

function Invoke-Native {
    <# Run an executable from System32 by full path; return its text; throw on a non-zero exit. #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string] $Name,
          [Parameter(Mandatory = $true)][string[]] $Arguments)
    $exe = [System.IO.Path]::Combine([System.Environment]::SystemDirectory, $Name)
    if (-not [System.IO.File]::Exists($exe)) { throw "$Name not found at $exe" }
    $LASTEXITCODE = 0
    $captured = & $exe @Arguments 2>&1
    $raw = @(ConvertTo-NativeOutputText -Output @($captured))
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code ${LASTEXITCODE}: $($raw -join ' ')" }
    return , ([string[]]$raw)
}

# --- pure helpers: no machine, testable anywhere (Test-AgentRemediator.ps1) -----------

function New-ChangeList {
    <#
    .SYNOPSIS
    Every REQUESTED field with its before and after - changed or not (Lab 13: a zero-line
    diff can still be an action). Same shape as target.Change in the Python fake.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][hashtable] $Before,
          [Parameter(Mandatory = $true)][hashtable] $Requested)
    foreach ($field in @($Requested.Keys | Sort-Object)) {
        if (-not $Before.ContainsKey($field)) { throw "no before-value for field '$field'" }
        $effect = 'set'
        if ("$($Before[$field])" -ceq "$($Requested[$field])") { $effect = 'no change' }
        [pscustomobject]@{ Field = $field; Before = $Before[$field]; After = $Requested[$field]; Effect = $effect }
    }
}

function ConvertTo-StartTypeName {
    <# Get-Service's StartType -> the baseline's word. Anything else is refused, not guessed. #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string] $StartType)
    switch ($StartType) {
        'Automatic' { return 'automatic' }
        'Manual'    { return 'manual' }
        'Disabled'  { return 'disabled' }
        default     { throw "unrecognised service start type '$StartType'" }
    }
}

function Get-ServicePlan {
    <#
    .SYNOPSIS
    The ordered steps that take the service from Before to Requested. Pure.

    .DESCRIPTION
    Order matters: a DISABLED service cannot be started, so a change that ends 'running'
    sets the start type first; a change that ends 'stopped' stops first, so the service is
    never left running under a start type that says it should not be. 'disabled' with
    'running' is a contradiction and is refused - nothing is changed.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][hashtable] $Before,
          [Parameter(Mandatory = $true)][hashtable] $Requested)
    $start = $Before['start_type']; if ($Requested.ContainsKey('start_type')) { $start = $Requested['start_type'] }
    $state = $Before['status'];     if ($Requested.ContainsKey('status'))     { $state = $Requested['status'] }
    if ($start -eq 'disabled' -and $state -eq 'running') {
        throw "refused: start_type 'disabled' with status 'running' is a contradiction"
    }
    $setType = $start -ne $Before['start_type']
    $setState = $state -ne $Before['status']
    $steps = @()
    if ($state -eq 'stopped') {
        if ($setState) { $steps += 'stop' }
        if ($setType)  { $steps += "starttype:$start" }
    } else {
        if ($setType)  { $steps += "starttype:$start" }
        if ($setState) { $steps += 'start' }
    }
    return , $steps
}

function ConvertFrom-AuditPolSubcategoryCsv {
    <# One subcategory's 'auditpol /get /r' CSV -> its Inclusion Setting. Shape-checked. #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]] $Line)
    $text = @($Line | Where-Object { "$_".Trim() })
    $rows = @($text | ConvertFrom-Csv)
    if ($rows.Count -ne 1) { throw "auditpol returned $($rows.Count) rows, expected 1. auditpol said: $($text -join ' ')" }
    $columns = @($rows[0].psobject.Properties.Name)
    foreach ($needed in 'Subcategory GUID', 'Inclusion Setting') {
        if ($columns -notcontains $needed) {
            throw "auditpol did not return audit policy (no '$needed' column). auditpol said: $($text -join ' ')"
        }
    }
    if ($rows[0].'Subcategory GUID' -ne $script:AuditGuid) {
        throw "auditpol returned subcategory $($rows[0].'Subcategory GUID'), expected $script:AuditGuid"
    }
    $setting = "$($rows[0].'Inclusion Setting')".Trim()
    if (@('Success and Failure', 'Success', 'Failure', 'No Auditing') -cnotcontains $setting) {
        throw "unrecognised audit setting '$setting'"
    }
    return $setting
}

function Get-AuditPolSetArguments {
    <# A setting -> auditpol /set arguments. Pure. #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string] $Setting)
    $map = @{ 'Success and Failure' = @('enable', 'enable'); 'Success' = @('enable', 'disable');
              'Failure' = @('disable', 'enable'); 'No Auditing' = @('disable', 'disable') }
    if (-not $map.ContainsKey($Setting)) { throw "unrecognised audit setting '$Setting'" }
    return , @('/set', "/subcategory:$script:AuditGuid", "/success:$($map[$Setting][0])", "/failure:$($map[$Setting][1])")
}

function New-RemediationResult {
    [CmdletBinding()]
    param([string] $Item, [hashtable] $Before, [hashtable] $Requested, [object[]] $Changes,
          [bool] $Applied, [AllowNull()] $ReportedAfter)
    # ONE shape for every state in the result (found 2026-10-20 on SRV01: a raw hashtable
    # crossed PSRP as a Hashtable and displayed as '{value}' beside Before's '@{value=1}').
    $after = $null
    if ($null -ne $ReportedAfter) { $after = [pscustomobject]$ReportedAfter }
    [pscustomobject]@{
        Host          = [System.Environment]::MachineName
        Item          = $Item
        Before        = [pscustomobject]$Before
        Requested     = [pscustomobject]$Requested
        Changes       = @($Changes)
        Applied       = $Applied
        ReportedAfter = $after                # the remediator's claim - never verification
    }
}

# --- readers the writer uses for previews and before-capture (its OWN read path) ------

function Read-ServiceState {
    $svc = Get-Service -Name $script:ServiceName -ErrorAction Stop
    @{ start_type = (ConvertTo-StartTypeName -StartType "$($svc.StartType)"); status = "$($svc.Status)".ToLowerInvariant() }
}

function Read-AuditState {
    $raw = Invoke-Native -Name 'auditpol.exe' -Arguments @('/get', "/subcategory:$script:AuditGuid", '/r')
    @{ setting = (ConvertFrom-AuditPolSubcategoryCsv -Line $raw) }
}

function Read-RegistryState {
    $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($script:RegistryKey, $false)
    if ($null -eq $key) { throw "registry key HKLM\$script:RegistryKey not found" }
    try {
        $value = $key.GetValue($script:RegistryValue, $null)
        if ($null -eq $value) { throw "registry value $script:RegistryValue not present" }
        @{ value = [int]$value }
    } finally { $key.Close() }
}

# --- the three visible functions -------------------------------------------------------

function Set-AgentServiceConfig {
    <#
    .SYNOPSIS
    Sets the Spooler service's start type and/or state. Nothing else. -Preview changes nothing.
    #>
    [CmdletBinding()]
    param([ValidateSet('automatic', 'manual', 'disabled')][string] $StartType,
          [ValidateSet('running', 'stopped')][string] $Status,
          [switch] $Preview)
    $requested = @{}
    if ($PSBoundParameters.ContainsKey('StartType')) { $requested['start_type'] = $StartType.ToLowerInvariant() }
    if ($PSBoundParameters.ContainsKey('Status'))    { $requested['status'] = $Status.ToLowerInvariant() }
    if ($requested.Count -eq 0) { throw 'refused: no values to write' }
    $before = Read-ServiceState
    $plan = Get-ServicePlan -Before $before -Requested $requested       # refuses contradictions
    $changes = @(New-ChangeList -Before $before -Requested $requested)
    if ($Preview) { return New-RemediationResult "service:$script:ServiceName" $before $requested $changes $false $null }
    foreach ($step in $plan) {
        if ($step -eq 'stop')  { Stop-Service -Name $script:ServiceName -Force -ErrorAction Stop }
        elseif ($step -eq 'start') { Start-Service -Name $script:ServiceName -ErrorAction Stop }
        else {
            $type = @{ automatic = 'Automatic'; manual = 'Manual'; disabled = 'Disabled' }[$step.Split(':')[1]]
            Set-Service -Name $script:ServiceName -StartupType $type -ErrorAction Stop
        }
    }
    New-RemediationResult "service:$script:ServiceName" $before $requested $changes $true (Read-ServiceState)
}

function Set-AgentAuditSetting {
    <#
    .SYNOPSIS
    Sets the Logon audit subcategory. Nothing else. -Preview changes nothing.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]
          [ValidateSet('Success and Failure', 'Success', 'Failure', 'No Auditing')][string] $Setting,
          [switch] $Preview)
    $requested = @{ setting = $Setting }
    $before = Read-AuditState
    $changes = @(New-ChangeList -Before $before -Requested $requested)
    if ($Preview) { return New-RemediationResult "audit:$script:AuditName" $before $requested $changes $false $null }
    $null = Invoke-Native -Name 'auditpol.exe' -Arguments (Get-AuditPolSetArguments -Setting $Setting)
    New-RemediationResult "audit:$script:AuditName" $before $requested $changes $true (Read-AuditState)
}

function Set-AgentRegistryValue {
    <#
    .SYNOPSIS
    Sets RDP-Tcp UserAuthentication (Network Level Authentication) to 0 or 1. Nothing else.
    -Preview changes nothing.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][ValidateSet(0, 1)][int] $Value,
          [switch] $Preview)
    $requested = @{ value = $Value }
    $before = Read-RegistryState
    $changes = @(New-ChangeList -Before $before -Requested $requested)
    if ($Preview) { return New-RemediationResult 'registry:rdp-nla' $before $requested $changes $false $null }
    $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($script:RegistryKey, $true)
    if ($null -eq $key) { throw "registry key HKLM\$script:RegistryKey not found" }
    try { $key.SetValue($script:RegistryValue, $Value, [Microsoft.Win32.RegistryValueKind]::DWord) }
    finally { $key.Close() }
    New-RemediationResult 'registry:rdp-nla' $before $requested $changes $true (Read-RegistryState)
}

# Only the three remediation functions leave this module. The readers and helpers are
# private: the endpoint exposes only what the role file lists, and the verifier never
# reads through this module (ADR 0034).
Export-ModuleMember -Function 'Set-AgentServiceConfig', 'Set-AgentAuditSetting', 'Set-AgentRegistryValue'
