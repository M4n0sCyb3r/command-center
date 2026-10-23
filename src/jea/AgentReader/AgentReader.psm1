# AgentReader - the functions the P01 JEA endpoint exposes.
#
# READ THIS BEFORE ADDING A FUNCTION (ADR 0003, "the sharp edge"):
# JEA does NOT restrict the code inside these functions. Their bodies run in
# full PowerShell, with the endpoint's elevated run-as identity. The role file
# decides WHO may call a function; only the code below decides WHAT it does.
# So every function here must:
#   - only read, never change anything;
#   - call executables by full path (no PATH lookup);
#   - take no parameter that is a path, a script block, an expression or free text;
#   - check the exit code and fail loudly instead of returning partial output.
#
# AND (learned the hard way, 2026-09-18): a JEA session has NO PROVIDERS, so the
# Env:, HKLM: and other drives do not exist there. `$env:SystemRoot` is empty
# inside the endpoint even though it works in a normal shell. Use .NET calls
# ([System.Environment], [System.IO.Path]) for anything a provider would serve.
#
# AND (2026-09-19): `2>&1` does not turn stderr into strings. It puts ERROR
# RECORDS into the same collection as the stdout strings, and asking one of
# those for its text can hand back a .NET type name instead of a message. So
# anything captured with `2>&1` goes through ConvertTo-NativeOutputText below
# before it is read, quoted or parsed. Never interpolate a raw capture into a
# message - see design call 7.
#
# AND (2026-10-20, P05 step 7): a cmdlet that works in a normal shell can be MISSING inside
# the endpoint. Get-CimInstance came back "not recognized" from SRV01: the restricted
# session does not auto-load the CimCmdlets module for code running in this one. So a
# function here reaches WMI through .NET ([System.Management.ManagementObject]), the same
# rule as the registry - not through a cmdlet a module would have to supply.

Set-StrictMode -Version 2.0

function Get-SafePropertyValue {
    <#
    .SYNOPSIS
    Reads a (possibly nested) property, returning $null if any step is absent.

    .DESCRIPTION
    Set-StrictMode -Version 2.0 makes reading a property that does not exist a
    terminating error, so the usual `$x.Exception.Message` cannot be used to
    probe an object of unknown shape. Going through psobject.Properties asks
    whether the property is there before reading it, which is the only way to
    inspect a foreign object under strict mode without wrapping every read in
    a try/catch.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        $InputObject,

        [Parameter(Mandatory = $true)]
        [string[]] $Path
    )

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
    <#
    .SYNOPSIS
    True when a string is a bare .NET type name rather than a human message.

    .DESCRIPTION
    'System.Management.Automation.RemoteException' is a dotted identifier with
    no spaces. No sentence auditpol produces looks like that, and no CSV row
    does either - rows carry commas. This is the guard that lets the converter
    reject a non-message WITHOUT having to know which PowerShell mechanism
    produced it: we do not need to explain the type name, only to refuse to
    present it as if it were what the command said.

    Applied ONLY to text pulled off a non-string object. A plain stdout string
    is passed through untouched, so a single-token line of real output can
    never be discarded by this rule.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string] $Text
    )

    return [bool]("$Text".Trim() -match '^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$')
}

function ConvertTo-NativeOutputText {
    <#
    .SYNOPSIS
    Turns a `2>&1` capture into plain text lines, one per captured item.

    .DESCRIPTION
    A native command captured with `2>&1` yields a MIXED collection: stdout
    arrives as strings, stderr as ErrorRecord objects. The two are no longer
    distinguishable by stream, which means the job of getting text back out of
    them is now ours. Done implicitly - by interpolating the capture into a
    string - an error record can render as its own type name, and the message
    that results quotes a class instead of the cause. That is the defect this
    function exists to remove.

    Strings pass straight through. For anything else the readable text is
    looked for in the places PowerShell actually puts a native command's
    stderr line, best first, and any candidate that is only a type name is
    skipped. An item that had text we could not read is LABELLED as such; an
    item that had no text at all becomes an empty string, because a blank
    stderr line is a blank line and not a failure to read one.

    Kept separate from the call to auditpol.exe for the same reason the CSV
    parser is: it can then be tested on sample objects, on any machine, with
    no endpoint and no admin rights.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [AllowNull()]
        [object[]] $Output
    )

    # Best first: Exception.Message is where a native command's stderr line
    # lands; TargetObject holds the offending line for some record shapes;
    # ErrorDetails.Message is set when something has supplied a friendlier one.
    $paths = @(
        , @('Exception', 'Message')
        , @('TargetObject')
        , @('ErrorDetails', 'Message')
    )

    foreach ($item in $Output) {
        if ($null -eq $item) { continue }

        if ($item -is [string]) {
            $item
            continue
        }

        # THREE outcomes, and the difference between the last two is the
        # correction of 2026-09-19 (found live, not by the offline tests):
        #   - text we can read            -> the text;
        #   - text that is only a class   -> a label saying it was unreadable;
        #   - no text at all              -> an empty string.
        # A native command's BLANK stderr lines also arrive as error records,
        # and a blank line carries no information. Labelling those as
        # unreadable puts noise in the message and makes an empty line look
        # like a failure to read one. ABSENT IS NOT UNREADABLE. The empty
        # string falls out downstream exactly like a blank stdout line does.
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
            # ToString() is the last resort and the least trustworthy source.
            # MEASURED on DC01, 2026-09-19: a blank stderr line arrives as an
            # ErrorRecord whose Exception is a RemoteException with an EMPTY
            # Message and an EMPTY TargetObject, and whose ToString() returns
            # 'System.Management.Automation.RemoteException' - the type name,
            # because an exception with no message prints its own class. That
            # is the whole defect of 2026-09-18, named.
            #
            # So a type name from ToString() is NOT evidence that text existed
            # and we failed to read it. It is the object saying it has nothing
            # to say. Only a real property value sets $sawText.
            $candidate = "$item".Trim()
            if ($candidate -and -not (Test-IsTypeNameOnly -Text $candidate)) {
                $sawText = $true
                $text = $candidate
            }
        }

        if ($null -eq $text) {
            if ($sawText) {
                # There WAS text and all of it was a class name. Say so. A type
                # name presented as a message is the bug; a type name LABELLED
                # as unreadable is a clue.
                $text = "[no readable text from a $($item.GetType().Name)]"
            } else {
                $text = ''
            }
        }

        $text
    }
}

function ConvertFrom-AuditPolCsv {
    <#
    .SYNOPSIS
    Turns 'auditpol /r' CSV lines into one object per subcategory.

    .DESCRIPTION
    Kept separate from the call to auditpol.exe so the parsing can be tested
    anywhere, on sample lines, without Windows and without admin rights.
    #>
    [CmdletBinding()]
    param(
        # auditpol's output contains blank lines, so empty strings and an empty
        # array both have to be allowed in - they are dropped below.
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [AllowEmptyString()]
        [string[]] $Line
    )

    $text = @($Line | Where-Object { "$_".Trim() })
    $rows = @($text | ConvertFrom-Csv)
    if ($rows.Count -eq 0) {
        throw 'auditpol returned no rows'
    }

    # auditpol reports SOME failures in its OUTPUT while still exiting 0 - a
    # missing SeSecurityPrivilege is one of them (found 2026-09-18 by disabling
    # the DC01 rights GPO on purpose and watching this function fail with a
    # parser error instead). An exit code is a convention, not a guarantee, so
    # the SHAPE of the output is checked as well: if the expected columns are
    # absent, what came back is a message rather than a policy, and it is
    # surfaced verbatim instead of being parsed into a misleading error.
    $columns = @($rows[0].psobject.Properties.Name)
    foreach ($needed in 'Machine Name', 'Subcategory', 'Subcategory GUID', 'Inclusion Setting') {
        if ($columns -notcontains $needed) {
            throw "auditpol did not return audit policy (no '$needed' column). auditpol said: $($text -join ' ')"
        }
    }

    foreach ($row in $rows) {
        [pscustomobject]@{
            Computer    = $row.'Machine Name'
            Subcategory = $row.'Subcategory'
            Guid        = $row.'Subcategory GUID'
            Setting     = $row.'Inclusion Setting'
        }
    }
}

function Get-AgentAuditPolicy {
    <#
    .SYNOPSIS
    Returns the effective audit policy of this machine, one object per subcategory.

    .DESCRIPTION
    Wraps 'auditpol.exe /get /category:* /r'. auditpol needs administrator
    rights, so a non-admin account gets "A required privilege is not held by
    the client" when it runs this directly. Through the JEA endpoint it
    succeeds. That difference is what build step 3 tests.

    Deliberately takes no parameters: nothing the caller sends can reach the
    command line. Filtering happens on the caller's side.
    #>
    [CmdletBinding()]
    param()

    # [System.Environment]::SystemDirectory, not $env:SystemRoot - see the note
    # at the top of this file.
    $exe = [System.IO.Path]::Combine([System.Environment]::SystemDirectory, 'auditpol.exe')
    if (-not [System.IO.File]::Exists($exe)) {
        throw "auditpol.exe not found at $exe"
    }

    # Assigned first: under Set-StrictMode, reading $LASTEXITCODE before any
    # native command has run in this session is itself an error.
    $LASTEXITCODE = 0
    $captured = & $exe /get '/category:*' /r 2>&1

    # EVERYTHING captured with 2>&1 goes through the converter before it is
    # read, quoted or parsed. $captured is mixed strings and ErrorRecords;
    # $raw is text.
    $raw = @(ConvertTo-NativeOutputText -Output @($captured))

    if ($LASTEXITCODE -ne 0) {
        throw "auditpol failed with exit code ${LASTEXITCODE}: $($raw -join ' ')"
    }

    # NOTE: this check is necessary but NOT sufficient - see the shape check in
    # ConvertFrom-AuditPolCsv. auditpol has been observed exiting 0 on failure.

    ConvertFrom-AuditPolCsv -Line ([string[]]$raw)
}

# --- P05 build step 7: the verifier's reads on SRV01 (ADR 0034) ----------------------
#
# Two more reads, each fixed to ONE item named here - never an item the caller names - so
# the read-only door cannot become a general service or registry browser. They are shown
# ONLY by RoleCapabilities\AgentReaderVerify.psrc, which only SRV01's session file maps
# (Jose's pick, 2026-10-20): DC01's endpoint does not offer them.
#
# INDEPENDENCE (ADR 0034: "different code"). These are the verifier's reads of what the
# remediator changed, so they are written here, NOT copied from AgentRemediator.psm1:
#   - the service is read through WMI (Win32_Service, via .NET - see the 2026-10-20 note at
#     the top), not Get-Service, so a defect in the remediator's ServiceController path is
#     not shared by the thing checking it;
#   - the registry value's TYPE is checked (REG_DWORD), which the writer's reader does not.
# Transient service states ('start pending') are REPORTED, not refused: the verifier's
# settling loop needs to see them, and only a value we cannot name is an error.

$script:VerifyServiceName   = 'Spooler'
$script:VerifyRegistryKey   = 'SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'
$script:VerifyRegistryValue = 'UserAuthentication'

function ConvertFrom-ServiceStartMode {
    <# Win32_Service.StartMode -> the baseline's word. Pure. Unknown is refused, not guessed. #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string] $StartMode)
    switch ("$StartMode".Trim()) {
        'Auto'     { return 'automatic' }
        'Manual'   { return 'manual' }
        'Disabled' { return 'disabled' }
        default    { throw "unrecognised service start mode '$StartMode'" }
    }
}

function ConvertFrom-ServiceStateName {
    <# Win32_Service.State -> lower case ('Running' -> 'running', 'Start Pending' -> 'start pending'). Pure. #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string] $State)
    $known = @('Stopped', 'Start Pending', 'Stop Pending', 'Running', 'Continue Pending',
               'Pause Pending', 'Paused', 'Unknown')
    $t = "$State".Trim()
    if ($known -cnotcontains $t) { throw "unrecognised service state '$State'" }
    return $t.ToLowerInvariant()
}

function ConvertFrom-RegistryReading {
    <# A value and its kind -> the baseline's integer. Pure. Absent or not a DWORD is refused. #>
    [CmdletBinding()]
    param([AllowNull()] $Value, [Parameter(Mandatory = $true)][string] $Kind)
    if ($null -eq $Value) { throw "registry value $script:VerifyRegistryValue not present" }
    if ($Kind -ne 'DWord') { throw "registry value $script:VerifyRegistryValue is $Kind, expected DWord" }
    return [int]$Value
}

function Get-AgentServiceState {
    <#
    .SYNOPSIS
    The Spooler service's start type and state, read from WMI's Win32_Service through .NET.
    Reads only. No parameters.

    .DESCRIPTION
    First written with Get-CimInstance; on SRV01 (2026-10-20) the endpoint answered "The term
    'Get-CimInstance' is not recognized" - the cmdlet's module is not loaded in the restricted
    session. ManagementObject is the .NET class underneath WMI and needs no module.
    Get() fetches one instance by its key and throws if there is none.
    #>
    [CmdletBinding()]
    param()
    # Loaded by its full name through .NET, not Add-Type: no cmdlet this read depends on.
    $null = [System.Reflection.Assembly]::Load('System.Management, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b03f5f7f11d50a3a')
    $path = "Win32_Service.Name='$script:VerifyServiceName'"
    $svc = [System.Management.ManagementObject]::new($path)
    try {
        try { $svc.Get() } catch { throw "WMI could not read ${path}: $($_.Exception.Message)" }
        [pscustomobject]@{
            Host       = [System.Environment]::MachineName
            Item       = "service:$script:VerifyServiceName"
            start_type = (ConvertFrom-ServiceStartMode -StartMode "$($svc['StartMode'])")
            status     = (ConvertFrom-ServiceStateName -State "$($svc['State'])")
        }
    } finally { $svc.Dispose() }
}

function Get-AgentRegistryState {
    <#
    .SYNOPSIS
    RDP-Tcp UserAuthentication (Network Level Authentication), as an integer. Reads only.
    No parameters.
    #>
    [CmdletBinding()]
    param()
    # $false = open READ-ONLY. JEA has no HKLM: drive, so .NET it is (see the header).
    $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($script:VerifyRegistryKey, $false)
    if ($null -eq $key) { throw "registry key HKLM\$script:VerifyRegistryKey not found" }
    try {
        $value = $key.GetValue($script:VerifyRegistryValue, $null)
        $kind = 'absent'
        if ($null -ne $value) { $kind = "$($key.GetValueKind($script:VerifyRegistryValue))" }
        [pscustomobject]@{
            Host  = [System.Environment]::MachineName
            Item  = 'registry:rdp-nla'
            value = (ConvertFrom-RegistryReading -Value $value -Kind $kind)
        }
    } finally { $key.Close() }
}

# ConvertFrom-AuditPolCsv, the ConvertFrom-* helpers and the text helpers are deliberately
# NOT exported: the endpoint exposes only what the role files list, and a caller has no
# reason to parse raw text or to inspect our error handling. Exporting a function does NOT
# show it - a role file does. DC01's role shows only Get-AgentAuditPolicy.
Export-ModuleMember -Function 'Get-AgentAuditPolicy', 'Get-AgentServiceState', 'Get-AgentRegistryState'
