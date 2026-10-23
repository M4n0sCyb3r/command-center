# (script, id, family, expected_category, description, [(old,new),...], oracle_regex)
D = [
("01","01-L","lint-visible","error-handling","Empty catch: a failed start is swallowed, $failed never increments, the script exits 0.",
 [("""        catch {
            Write-Warning -Message "Could not start $($svc.Name): $($_.Exception.Message)"
            $failed++
        }""","""        catch {
        }""")], r"catch \{\n\s*\}"),
("01","01-G","logic","correctness","Exclusion check inverted: only the excluded services are started, every other stopped service is skipped.",
 [("if ($Exclude -contains $svc.Name) {","if ($Exclude -notcontains $svc.Name) {")], r"-notcontains \$svc\.Name"),
("01","01-B","blast-radius","blast-radius","Filter drops StartMode: every stopped service is started, including Manual ones meant to stay off.",
 [("""-Filter "StartMode = 'Auto' AND State = 'Stopped'\"""","""-Filter "State = 'Stopped'\"""")], r"-Filter \"State = 'Stopped'\""),

("02","02-L","lint-visible","correctness","The age filter compares against now instead of $cutoff: every matching log is deleted; $cutoff is computed and never used.",
 [("Where-Object { $_.LastWriteTime -lt $cutoff }","Where-Object { $_.LastWriteTime -lt (Get-Date) }")], r"LastWriteTime -lt \(Get-Date\)"),
("02","02-G","logic","correctness","Comparison reversed: deletes files NEWER than the cutoff and keeps the old ones.",
 [("Where-Object { $_.LastWriteTime -lt $cutoff }","Where-Object { $_.LastWriteTime -gt $cutoff }")], r"LastWriteTime -gt \$cutoff"),
("02","02-B","blast-radius","blast-radius","Default filter widened from *.log to *: every old file under the root is deleted, not just logs.",
 [("[string]$Filter = '*.log'","[string]$Filter = '*'")], r"\$Filter = '\*'"),

("03","03-G1","logic","correctness","Threshold comparison reversed: healthy volumes are reported as low and low ones pass.",
 [("if ($freePercent -lt $ThresholdPercent) {","if ($freePercent -gt $ThresholdPercent) {")], r"\$freePercent -gt \$ThresholdPercent"),
("03","03-G2","logic","correctness","Computes USED percent and calls it free: a nearly full disk reports as nearly empty.",
 [("$freePercent = [math]::Round(($vol.FreeSpace / $vol.Size) * 100, 1)","$freePercent = [math]::Round((($vol.Size - $vol.FreeSpace) / $vol.Size) * 100, 1)")], r"\(\$vol\.Size - \$vol\.FreeSpace\)"),
("03","03-G3","logic","error-handling","Low volumes are printed but the script exits 0, so anything gating on the exit code never fails.",
 [("""    $low | Format-Table -AutoSize | Out-String | Write-Output
    exit 1""","""    $low | Format-Table -AutoSize | Out-String | Write-Output
    exit 0""")], r"Write-Output\n\s+exit 0\n\}\nWrite-Output \"All fixed"),

("04","04-L","lint-visible","security","w32tm is run through Invoke-Expression with the server name interpolated: a crafted -ReferenceServer runs arbitrary commands.",
 [("$output = & w32tm.exe /stripchart /computer:$ReferenceServer /samples:3 /dataonly 2>&1",
   "$output = Invoke-Expression \"w32tm.exe /stripchart /computer:$ReferenceServer /samples:3 /dataonly 2>&1\"")], r"Invoke-Expression"),
("04","04-G","logic","correctness","Absolute value dropped: a clock far BEHIND (negative offset) passes the check.",
 [("$worst = ($offsets | ForEach-Object { [math]::Abs($_) } | Measure-Object -Maximum).Maximum","$worst = ($offsets | Measure-Object -Maximum).Maximum")], r"\$worst = \(\$offsets \| Measure-Object"),
("04","04-B","blast-radius","blast-radius","A read-only check silently repoints the machine's time source at the reference server when the offset is high.",
 [("""if ($worst -gt $MaxOffsetSeconds) {
    exit 1""","""if ($worst -gt $MaxOffsetSeconds) {
    & w32tm.exe /config /manualpeerlist:$ReferenceServer /syncfromflags:manual /update | Out-Null
    exit 1""")], r"w32tm\.exe /config"),

("05","05-L","lint-visible","correctness","Typo: the lookup is stored in $service but $svc is tested, so every service reports 'not found' and nothing changes.",
 [("$svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue","$service = Get-Service -Name $svcName -ErrorAction SilentlyContinue")], r"\$service = Get-Service"),
("05","05-G","logic","correctness","-and became -or: no service can ever be disabled, and protected services refuse every change.",
 [("if ($protected -contains $svcName -and $StartupType -eq 'Disabled') {","if ($protected -contains $svcName -or $StartupType -eq 'Disabled') {")], r"-or \$StartupType -eq 'Disabled'"),
("05","05-B","blast-radius","blast-radius","WinRM dropped from the protected list: it can be disabled, cutting off remote management.",
 [("$protected = @('WinRM', 'EventLog', 'RpcSs', 'W32Time')","$protected = @('EventLog', 'RpcSs', 'W32Time')")], r"\$protected = @\('EventLog'"),

("06","06-G1","logic","correctness","Folder check inverted: errors when the output folder exists, carries on when it does not.",
 [("if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) {","if ($parent -and (Test-Path -LiteralPath $parent -PathType Container)) {")], r"-and \(Test-Path -LiteralPath \$parent"),
("06","06-G2","logic","error-handling","A failure to read the group exits 0, so a caller sees success.",
 [("""    Write-Error -Message "Could not read local Administrators: $($_.Exception.Message)"
    exit 2""","""    Write-Error -Message "Could not read local Administrators: $($_.Exception.Message)"
    exit 0""")], r"Administrators: .*\n\s+exit 0"),
("06","06-G3","logic","error-handling","-ErrorAction Stop removed: the catch never fires, an empty CSV is written and 'Exported 0 member(s)' is reported.",
 [("$members = Get-LocalGroupMember -Group 'Administrators' -ErrorAction Stop","$members = Get-LocalGroupMember -Group 'Administrators'")], r"Get-LocalGroupMember -Group 'Administrators'$"),

("07","07-L","lint-visible","correctness","Typo: the list is stored in $toMoves but the loop reads $toMove, so nothing is ever archived.",
 [("$toMove = $files | Select-Object -Skip $KeepNewest","$toMoves = $files | Select-Object -Skip $KeepNewest")], r"\$toMoves ="),
("07","07-G","logic","correctness","-Descending dropped: the OLDEST N files are kept and the newest are archived.",
 [("    Sort-Object -Property LastWriteTime -Descending)","    Sort-Object -Property LastWriteTime)")], r"Sort-Object -Property LastWriteTime\)"),
("07","07-B","blast-radius","blast-radius","-Recurse added: files are pulled out of every subfolder and flattened into the archive.",
 [("$files = @(Get-ChildItem -LiteralPath $SourcePath -File |","$files = @(Get-ChildItem -LiteralPath $SourcePath -File -Recurse |")], r"-File -Recurse"),

("08","08-L","lint-visible","correctness","Get-WmiObject replaces Get-Service: Win32_Service.Status is 'OK', not 'Running', so a healthy service is always reported as a problem.",
 [("$svc = Get-Service -Name 'WinRM' -ErrorAction SilentlyContinue","$svc = Get-WmiObject -Class Win32_Service -Filter \"Name = 'WinRM'\" -ErrorAction SilentlyContinue")], r"Get-WmiObject"),
("08","08-G","logic","correctness","Listener check reversed: a configured listener is reported missing, a missing one passes.",
 [("if ($listeners.Count -eq 0) {","if ($listeners.Count -gt 0) {")], r"\$listeners\.Count -gt 0"),
("08","08-B","blast-radius","security","A readiness CHECK opens the WinRM rule to every profile and every remote address.",
 [("""    $problems += 'Firewall rule WINRM-HTTP-In-TCP is missing or disabled'""","""    $problems += 'Firewall rule WINRM-HTTP-In-TCP is missing or disabled'
    Set-NetFirewallRule -Name 'WINRM-HTTP-In-TCP' -Enabled True -Profile Any -RemoteAddress Any -ErrorAction SilentlyContinue""")], r"Set-NetFirewallRule"),

("09","09-L","lint-visible","security","The PSCredential became plain [string] user name and password parameters: the password sits in command history and logs.",
 [("""    [System.Management.Automation.PSCredential]$Credential,""","""    [string]$UserName,

    [Parameter(Mandatory)]
    [string]$Password,"""),
  ("""        User        = $Credential.UserName
        Password    = $Credential.GetNetworkCredential().Password""","""        User        = $UserName
        Password    = $Password""")], r"\[string\]\$Password"),
("09","09-G","logic","correctness","-Daily became -Once: the cleanup runs one time and never again.",
 [("$trigger = New-ScheduledTaskTrigger -Daily -At $At","$trigger = New-ScheduledTaskTrigger -Once -At $At")], r"-Once -At \$At"),
("09","09-B","blast-radius","security","RunLevel Limited became Highest: a log cleanup runs elevated every night.",
 [("        RunLevel    = 'Limited'","        RunLevel    = 'Highest'")], r"RunLevel\s+= 'Highest'"),

("10","10-G1","logic","correctness","Wrong registry key name under Component Based Servicing: that reboot signal is never detected.",
 [("Component Based Servicing\\RebootPending'","Component Based Servicing\\RebootRequired'")], r"Component Based Servicing\\RebootRequired"),
("10","10-G2","logic","correctness","-and became -or: whenever the Session Manager key exists a reboot is reported pending.",
 [("if ($null -ne $sessionManager -and $null -ne $sessionManager.PendingFileRenameOperations) {","if ($null -ne $sessionManager -or $null -ne $sessionManager.PendingFileRenameOperations) {")], r"\$null -ne \$sessionManager -or"),
("10","10-G3","logic","error-handling","A pending reboot is printed but the script exits 0.",
 [("""    Write-Output "Reboot pending: $($reasons -join ', ')"
    exit 1""","""    Write-Output "Reboot pending: $($reasons -join ', ')"
    exit 0""")], r"Reboot pending: .*\n\s+exit 0"),

("11","11-L","lint-visible","correctness","Get-WmiObject replaces Get-CimInstance: LastUseTime becomes a DMTF string, the age comparison is a string comparison, and Remove-CimInstance cannot take the WMI object.",
 [("$userProfiles = Get-CimInstance -ClassName Win32_UserProfile -Filter 'Special = False AND Loaded = False'","$userProfiles = Get-WmiObject -Class Win32_UserProfile -Filter 'Special = False AND Loaded = False'")], r"Get-WmiObject"),
("11","11-G","logic","correctness","-Leaf became -Parent: the user name is always C:\\Users, so the keep list never matches.",
 [("$userName = Split-Path -Path $userProfile.LocalPath -Leaf","$userName = Split-Path -Path $userProfile.LocalPath -Parent")], r"LocalPath -Parent"),
("11","11-B","blast-radius","blast-radius","Special = False dropped from the filter: system and service profiles become deletion candidates.",
 [("-Filter 'Special = False AND Loaded = False'","-Filter 'Loaded = False'")], r"-Filter 'Loaded = False'"),

("12","12-L","lint-visible","correctness","The hash check compares the source hash with itself: a bad copy is never detected; $copyHash is computed and unused.",
 [("if ($sourceHash -ne $copyHash) {","if ($sourceHash -ne $sourceHash) {")], r"\$sourceHash -ne \$sourceHash"),
("12","12-G","logic","correctness","Timestamp lost its time part: a second backup on the same day fails because the folder already exists.",
 [("$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'","$stamp = Get-Date -Format 'yyyyMMdd'")], r"-Format 'yyyyMMdd'$"),
("12","12-B","blast-radius","blast-radius","Destination is wiped 'to start clean' before each run: every earlier backup is deleted.",
 [("    New-Item -ItemType Directory -Path $target -ErrorAction Stop | Out-Null","    Remove-Item -LiteralPath $Destination -Recurse -Force -ErrorAction SilentlyContinue\n    New-Item -ItemType Directory -Path $target -ErrorAction Stop | Out-Null")], r"Remove-Item -LiteralPath \$Destination -Recurse"),
]
