<#
.SYNOPSIS
Register (or remove) the hourly Windows Task Scheduler job that runs the full
dev-box hygiene pass.

.DESCRIPTION
The SessionStart hook (.claude/hooks/dev_hygiene_hook.py) inventories all classes.
This hourly task also covers long-running lanes when no new session starts.

What the task runs, hourly:

    python scripts/dev_hygiene.py --apply --if-low-disk <FloorGb>
        --escalate-below <FloorGb> --summary-out .claude/logs/dev-hygiene-full.json

`--if-low-disk` means the destructive part only happens under actual pressure;
above the floor the pass inventories and writes its summary without removing
anything. The summary is what the SessionStart hook surfaces to the founder, so
an escalation from an unattended pass is seen at the next session start.

Runs as the INTERACTIVE USER, unelevated, on purpose. ACL-locked temp husks need
elevation and this task must never acquire it: `dev_hygiene.py` reports them for
`scripts/clear_sandbox_temp_dirs.ps1 -Apply` instead of trying to force them.

.PARAMETER FloorGb
Free-space floor in GB for both --if-low-disk and --escalate-below. Default 40.

.PARAMETER PythonExe
Interpreter to use. Defaults to the repo venv, then the PATH `python`.

.PARAMETER Remove
Unregister the task instead of installing it.

.PARAMETER WhatIfOnly
Print the registration plan and exit without touching the scheduler.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts/install_dev_hygiene_task.ps1 -WhatIfOnly

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts/install_dev_hygiene_task.ps1
#>
[CmdletBinding()]
param(
    [double]$FloorGb = 40,
    [string]$PythonExe = '',
    [switch]$Remove,
    [switch]$WhatIfOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TaskName = 'TinyAssets-DevHygiene'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "removed scheduled task '$TaskName'"
    }
    else {
        Write-Host "no scheduled task '$TaskName' to remove"
    }
    exit 0
}

if (-not $PythonExe) {
    $venv = Join-Path $repo '.venv\Scripts\python.exe'
    $PythonExe = if (Test-Path $venv) { $venv } else { 'python' }
}

$script = Join-Path $repo 'scripts\dev_hygiene.py'
if (-not (Test-Path $script)) {
    throw "dev_hygiene.py not found at $script -- run this from the repo's scripts/ directory"
}
$summary = Join-Path $repo '.claude\logs\dev-hygiene-full.json'
$log = Join-Path $repo '.claude\logs\dev-hygiene.log'

$argumentList = @(
    "`"$script`""
    '--apply'
    '--if-low-disk', $FloorGb
    '--escalate-below', $FloorGb
    '--repo', "`"$repo`""
    '--log', "`"$log`""
    '--summary-out', "`"$summary`""
    '--quiet'
) -join ' '

Write-Host "task      : $TaskName"
Write-Host "runs      : hourly, as $env:USERNAME (unelevated), starting 5 minutes from now"
Write-Host "command   : $PythonExe $argumentList"
Write-Host "workdir   : $repo"
Write-Host "floor     : $FloorGb GB free -- below this the pass removes; above it, reports only"

if ($WhatIfOnly) {
    Write-Host ''
    Write-Host '(--WhatIfOnly: nothing registered)'
    exit 0
}

$action = New-ScheduledTaskAction -Execute $PythonExe -Argument $argumentList -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Hours 1)
# ExecutionTimeLimit bounds a pass that gets stuck on a wedged git or docker call.
# StartWhenAvailable catches the hours the box was asleep, which is when a long
# unattended lane has been filling the disk.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Host ''
Write-Host "registered. Inspect with:  Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "Run it now with:           Start-ScheduledTask -TaskName $TaskName"
Write-Host "Remove it with:            ... install_dev_hygiene_task.ps1 -Remove"
