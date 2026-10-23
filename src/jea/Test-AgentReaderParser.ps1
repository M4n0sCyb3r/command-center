<#
.SYNOPSIS
Offline tests for the AgentReader module's private functions.

.DESCRIPTION
ConvertFrom-AuditPolCsv is kept separate from the call to auditpol.exe so that
the parsing can be tested on sample text - on any machine, with no admin rights,
no JEA endpoint and no domain controller. This script is what cashes that in.

Case 2 is the one that matters. It is the real output auditpol produces when the
caller lacks SeSecurityPrivilege, captured on 2026-09-18 by disabling the DC01
rights GPO on purpose. Before the shape check existed, that text parsed cleanly
as CSV and the function died pointing at a missing 'Machine Name' property -
blaming the parser for a privilege problem.

HOW THE PRIVATE FUNCTION IS REACHED: ConvertFrom-AuditPolCsv is not exported, on
purpose, so it is invisible to a caller and to a dot-source. Import-Module
-PassThru hands back the module object, and `& $module { ... }` runs a script
block INSIDE that module's session state, where its private functions live.
Set-StrictMode from the module applies there too, so the tests run under the
same strictness the endpoint does.

Cases 4 to 7 cover ConvertTo-NativeOutputText, added 2026-09-19 (design call 7).
`2>&1` captures stderr as ERROR RECORDS, not strings, and reading one of those
carelessly can hand back a .NET type name instead of the message. Case 7 is the
one that matters: it proves the privilege sentence survives all the way into the
thrown message, which is the whole point of keeping the merged capture.

Exit code 0 = all cases pass. Anything else = a failure, named.
#>

$ErrorActionPreference = 'Stop'

$module = Import-Module (Join-Path $PSScriptRoot 'AgentReader\AgentReader.psm1') -Force -PassThru

function Invoke-Parser {
    param([string[]] $Line)
    & $module { param($l) ConvertFrom-AuditPolCsv -Line ([string[]]$l) } $Line
}

function Invoke-Converter {
    param([object[]] $Output)
    & $module { param($o) ConvertTo-NativeOutputText -Output ([object[]]$o) } $Output
}

function Invoke-TypeNameTest {
    param([string] $Text)
    & $module { param($t) Test-IsTypeNameOnly -Text $t } $Text
}

function New-StderrRecord {
    # What PowerShell hands you for one line of a native command's stderr when
    # the call was made with 2>&1. Built here so the converter can be tested
    # without a native command, an endpoint or a domain controller.
    param([string] $Message)
    New-Object System.Management.Automation.ErrorRecord(
        (New-Object System.Exception $Message),
        'NativeCommandError',
        [System.Management.Automation.ErrorCategory]::NotSpecified,
        $null
    )
}

$script:failures = 0

function Assert-True {
    param([bool] $Condition, [string] $Because)
    if ($Condition) {
        Write-Host "  PASS  $Because"
    } else {
        Write-Host "  FAIL  $Because" -ForegroundColor Red
        $script:failures++
    }
}

Write-Host 'Case 1: real auditpol CSV parses into policy rows'
$good = @(
    'Machine Name,Policy Target,Subcategory,Subcategory GUID,Inclusion Setting,Exclusion Setting',
    'DC01,System,Security State Change,{0cce9210-69ae-11d9-bed3-505054503030},Success,',
    '',
    'DC01,System,System Integrity,{0cce9212-69ae-11d9-bed3-505054503030},Success and Failure,'
)
$rows = @(Invoke-Parser -Line $good)
Assert-True ($rows.Count -eq 2)                            'two rows, blank line dropped'
Assert-True ($rows[0].Computer    -eq 'DC01')              'Computer read from Machine Name'
Assert-True ($rows[1].Subcategory -eq 'System Integrity')  'Subcategory read'
Assert-True ($rows[1].Setting -eq 'Success and Failure')   'Setting read from Inclusion Setting'

Write-Host "Case 2: auditpol's privilege error is reported as a privilege error"
$denied = @(
    'Error 0x00000522 occurred:',
    'A required privilege is not held by the client.'
)
$threw = $false
$message = ''
try {
    Invoke-Parser -Line $denied | Out-Null
} catch {
    $threw = $true
    $message = $_.Exception.Message
}
Assert-True $threw                                         'it throws rather than returning junk rows'
Assert-True ($message -like '*required privilege is not held*') "the message repeats what auditpol said - got: $message"
Assert-True ($message -notlike '*cannot be found on this object*') 'it does not blame a missing property'

Write-Host 'Case 3: empty input is still an explicit failure'
$threw = $false
try { Invoke-Parser -Line @('', '  ') | Out-Null } catch { $threw = $true }
Assert-True $threw                                         'blank-only input throws'

Write-Host 'Case 4: a mixed capture comes back as text, in order'
$mixed = @(
    'Machine Name,Policy Target,Subcategory,Subcategory GUID,Inclusion Setting,Exclusion Setting',
    (New-StderrRecord 'A required privilege is not held by the client.'),
    'DC01,System,System Integrity,{0cce9212-69ae-11d9-bed3-505054503030},Success and Failure,'
)
$text = @(Invoke-Converter -Output $mixed)
Assert-True ($text.Count -eq 3)                                  'one line out per item in, order kept'
Assert-True ($text[0] -like 'Machine Name,*')                    'a stdout string passes through untouched'
Assert-True ($text[1] -eq 'A required privilege is not held by the client.') "the error record yields its message - got: $($text[1])"
$nonStrings = @($text | Where-Object { -not ($_ -is [string]) })
Assert-True ($nonStrings.Count -eq 0)                            'every item out is a string'

Write-Host 'Case 5: a record with no readable message never yields a type name'
$opaque = New-StderrRecord 'System.Management.Automation.RemoteException'
$text = @(Invoke-Converter -Output @($opaque))
Assert-True ($text.Count -eq 1)                                  'one item in, one out'
Assert-True ($text[0] -notmatch 'RemoteException$')              'the type name is not presented as a message'
Assert-True ($text[0] -like '*no readable text*')                "it says so instead - got: $($text[0])"

Write-Host 'Case 6: the type-name guard tells a class from a sentence'
Assert-True (Invoke-TypeNameTest 'System.Management.Automation.RemoteException')    'a dotted type name is recognised'
Assert-True (-not (Invoke-TypeNameTest 'A required privilege is not held by the client.')) 'a sentence is not'
Assert-True (-not (Invoke-TypeNameTest 'DC01,System,Security State Change,{0cce9210-69ae-11d9-bed3-505054503030},Success,')) 'a CSV row is not'
Assert-True (-not (Invoke-TypeNameTest 'Error 0x00000522 occurred:'))               'an auditpol error line is not'

Write-Host 'Case 7: END TO END - the privilege sentence reaches the thrown message'
# This is the defect of 2026-09-18 reproduced through both functions: the real
# capture from a caller without SeSecurityPrivilege, converted then parsed.
# The blank records are not decoration: auditpol emits blank stderr lines, and
# PowerShell wraps those in error records too. Leaving them out of this case is
# exactly why the offline suite passed while the live run printed four
# '[no readable text...]' fragments on 2026-09-19.
$captured = @(
    (New-StderrRecord 'Error 0x00000522 occurred:'),
    (New-StderrRecord ''),
    (New-StderrRecord 'A required privilege is not held by the client.'),
    (New-StderrRecord ''),
    (New-StderrRecord ''),
    (New-StderrRecord '')
)
$message = ''
$threw = $false
try {
    Invoke-Parser -Line ([string[]]@(Invoke-Converter -Output $captured)) | Out-Null
} catch {
    $threw = $true
    $message = $_.Exception.Message
}
Assert-True $threw                                               'it still throws'
Assert-True ($message -like '*required privilege is not held*')  "the operator is told the real cause - got: $message"
Assert-True ($message -notlike '*RemoteException*')              'no .NET type name anywhere in the message'
Assert-True ($message -notlike '*cannot be found on this object*') 'and it still does not blame a missing property'
Assert-True ($message -notlike '*no readable text*')             "blank stderr lines leave no fragments behind - got: $message"

Write-Host 'Case 8: an empty record is empty, not unreadable'
# ABSENT IS NOT UNREADABLE. A blank stderr line has nothing in it to read, so
# it becomes an empty string and disappears downstream like any blank line.
# Case 5 is its opposite: something WAS there and could not be read.
#
# MEASURED on DC01, 2026-09-19, by making auditpol fail on a bad category:
#   message=[]  target=[]  tostring=[System.Management.Automation.RemoteException]
# An exception with no message prints its own class name, so ToString() must
# not be treated as evidence that text existed.
$text = @(Invoke-Converter -Output @((New-StderrRecord ''), 'DC01,System,System Integrity,{0cce9212-69ae-11d9-bed3-505054503030},Success and Failure,'))
Assert-True ($text.Count -eq 2)                                  'the empty item still occupies its place'
Assert-True ($text[0] -eq '')                                    "an empty record becomes an empty string - got: '$($text[0])'"
Assert-True ($text[0] -notlike '*no readable text*')             'it is NOT labelled unreadable'
Assert-True ($text[1] -like 'DC01,*')                            'the line after it is untouched'

Write-Host ''
if ($script:failures -eq 0) {
    Write-Host 'ALL CASES PASS' -ForegroundColor Green
    exit 0
}
Write-Host "$($script:failures) CASE(S) FAILED" -ForegroundColor Red
exit 1
