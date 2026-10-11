# install_canary_task.ps1 — register Windows Task Scheduler entries for the
# layered uptime canary.
#
# Spec: openspec/specs/uptime-and-alarms/spec.md §5 (file layout)
# + §7 (scheduler drift mitigation — XML-serializable entries).
#
# Creates two tasks in the user's task library:
#   TinyAssets-Canary-L1  — every 2 min, runs scripts/uptime_canary.py
#   TinyAssets-Alarm      — every 2 min, runs scripts/uptime_alarm.py
#
# Both are decoupled (separate tasks) per design-note §5: probe liveness is
# independent of alarm liveness. Each runs out-of-process so a tray crash
# cannot silence the canary.
#
# Usage
# -----
#   powershell -ExecutionPolicy Bypass -File scripts/install_canary_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/install_canary_task.ps1 -Uninstall
#
# Idempotent: if tasks already exist, they are re-registered (overwriting
# schedule + command). Safe to re-run.

[CmdletBinding()]
param(
    [switch]$Uninstall,
    [string]$CanaryUrl = $env:TINYASSETS_MCP_CANARY_URL,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$CanaryScript = Join-Path $RepoRoot "scripts\uptime_canary.py"
$AlarmScript  = Join-Path $RepoRoot "scripts\uptime_alarm.py"

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $PythonExe) {
        Write-Error "python not found on PATH; pass -PythonExe <path>."
    }
}

$L1Name    = "TinyAssets-Canary-L1"
$AlarmName = "TinyAssets-Alarm"

function Remove-TaskIfPresent([string]$Name) {
    $existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
        Write-Host "[install_canary] removed existing task: $Name"
    }
}

if ($Uninstall) {
    Remove-TaskIfPresent $L1Name
    Remove-TaskIfPresent $AlarmName
    Write-Host "[install_canary] uninstalled."
    exit 0
}

# Each task runs every 2 min, indefinitely, under the current user's
# context, hidden (no console flash). StartAt=now rounded-forward so the
# two tasks don't step on each other — stagger alarm 30s after probe.

$Now = Get-Date
$ProbeStart = $Now.AddMinutes(1)
$AlarmStart = $Now.AddMinutes(1).AddSeconds(30)

$ProbeTrigger = New-ScheduledTaskTrigger `
    -Once -At $ProbeStart `
    -RepetitionInterval (New-TimeSpan -Minutes 2)

$AlarmTrigger = New-ScheduledTaskTrigger `
    -Once -At $AlarmStart `
    -RepetitionInterval (New-TimeSpan -Minutes 2)

$ProbeArgs = "`"$CanaryScript`""
if (-not [string]::IsNullOrWhiteSpace($CanaryUrl)) {
    $ProbeArgs += " --url `"$CanaryUrl`""
}

$ProbeAction = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $ProbeArgs `
    -WorkingDirectory $RepoRoot

$AlarmAction = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$AlarmScript`"" `
    -WorkingDirectory $RepoRoot

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 1)

$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Remove-TaskIfPresent $L1Name
Register-ScheduledTask `
    -TaskName $L1Name `
    -Trigger $ProbeTrigger `
    -Action $ProbeAction `
    -Settings $Settings `
    -Principal $Principal `
    -Description "TinyAssets uptime canary Layer 1 — probes public MCP every 2 min." | Out-Null
Write-Host "[install_canary] registered $L1Name (starts $ProbeStart, 2-min repeat)"

Remove-TaskIfPresent $AlarmName
Register-ScheduledTask `
    -TaskName $AlarmName `
    -Trigger $AlarmTrigger `
    -Action $AlarmAction `
    -Settings $Settings `
    -Principal $Principal `
    -Description "TinyAssets uptime alarm — reads uptime.log, escalates on 2+ consecutive reds." | Out-Null
Write-Host "[install_canary] registered $AlarmName (starts $AlarmStart, 2-min repeat)"

Write-Host ""
Write-Host "Done. Verify with:"
Write-Host "  Get-ScheduledTask -TaskName TinyAssets-Canary-L1,TinyAssets-Alarm | Format-List"
Write-Host "  Get-Content .agents/uptime.log -Tail 5"
Write-Host "  Get-Content .agents/uptime_alarms.log -Tail 5    # only exists once an alarm fires"
