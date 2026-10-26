# load_secrets.ps1 — PowerShell mirror of load_secrets.sh.
#
# Dot-source to export into the caller's shell:
#   . .\scripts\load_secrets.ps1
#
# Vendor selection: $env:TINYASSETS_SECRETS_VENDOR — "1password" (default),
# "bitwarden", or "plaintext" (migration opt-out).
#
# Exit semantics mirror the bash loader. When dot-sourced, uses
# `return` to avoid killing the caller session on error.

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$KeysFile = Join-Path $RepoRoot "scripts\secrets_keys.txt"
if ($env:TINYASSETS_SECRETS_PROFILE -eq "canary") {
    $KeysFile = Join-Path $RepoRoot "scripts\canary_secret_keys.txt"
}
$PlaintextPath = if ($env:TINYASSETS_SECRETS_PLAINTEXT_PATH) {
    $env:TINYASSETS_SECRETS_PLAINTEXT_PATH
} else {
    Join-Path $HOME "workflow-secrets.env"
}
$Vendor = if ($env:TINYASSETS_SECRETS_VENDOR) { $env:TINYASSETS_SECRETS_VENDOR } else { "1password" }
$VaultName = if ($env:TINYASSETS_SECRETS_VAULT) { $env:TINYASSETS_SECRETS_VAULT } else { "tinyassets" }

function Fail-LoadSecrets {
    param([int]$Code, [string]$Message)
    Write-Error "[load_secrets] ERROR: $Message"
    # When dot-sourced, `exit` would kill the caller session. `return`
    # from a function + `throw` downstream lets callers recover.
    throw $Message
}

function Read-SecretKeys {
    if (!(Test-Path $KeysFile)) {
        Fail-LoadSecrets 12 "keys file missing: $KeysFile"
    }
    Get-Content $KeysFile | Where-Object {
        $line = $_.Trim()
        $line -and -not $line.StartsWith("#")
    } | ForEach-Object { ($_ -split '\s+')[0] }
}

function Set-SecretEnv {
    param([string]$Key, [string]$Value)
    # Set-Item env: both exports and visibility to child processes.
    Set-Item -Path "env:$Key" -Value $Value
}

function Load-OnePassword {
    if (-not (Get-Command op -ErrorAction SilentlyContinue)) {
        Fail-LoadSecrets 10 "1Password CLI 'op' not installed. Install: https://developer.1password.com/docs/cli/get-started/"
    }
    $whoami = & op whoami 2>$null
    if ($LASTEXITCODE -ne 0) {
        Fail-LoadSecrets 11 "1Password session not authenticated. Run: op signin"
    }
    $missing = @()
    $count = 0
    foreach ($key in (Read-SecretKeys)) {
        $value = & op item get $key --vault $VaultName --fields password --reveal 2>$null
        if (-not $value -or $LASTEXITCODE -ne 0) {
            $missing += $key
            continue
        }
        Set-SecretEnv -Key $key -Value $value
        $count++
    }
    if ($missing.Count -gt 0) {
        Fail-LoadSecrets 13 "keys not found in vault '$VaultName': $($missing -join ', ')"
    }
    Write-Host "[load_secrets] 1Password: loaded $count key(s) from vault '$VaultName'"
}

function Load-Bitwarden {
    if (-not (Get-Command bw -ErrorAction SilentlyContinue)) {
        Fail-LoadSecrets 10 "Bitwarden CLI 'bw' not installed. Install: https://bitwarden.com/help/cli/"
    }
    $statusJson = & bw status 2>$null
    $status = ($statusJson | ConvertFrom-Json).status
    if ($status -ne "unlocked") {
        Fail-LoadSecrets 11 "Bitwarden vault not unlocked. Run: `$env:BW_SESSION = bw unlock --raw"
    }
    $missing = @()
    $count = 0
    foreach ($key in (Read-SecretKeys)) {
        $value = & bw get password $key 2>$null
        if (-not $value -or $LASTEXITCODE -ne 0) {
            $missing += $key
            continue
        }
        Set-SecretEnv -Key $key -Value $value
        $count++
    }
    if ($missing.Count -gt 0) {
        Fail-LoadSecrets 13 "keys not found in Bitwarden: $($missing -join ', ')"
    }
    Write-Host "[load_secrets] Bitwarden: loaded $count key(s)"
}

function Load-Plaintext {
    if (!(Test-Path $PlaintextPath)) {
        Fail-LoadSecrets 14 "plaintext file not readable: $PlaintextPath"
    }
    # Parse KEY=VALUE lines; skip blanks + comments.
    $fileVars = @{}
    foreach ($line in (Get-Content $PlaintextPath)) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        $eq = $trimmed.IndexOf("=")
        if ($eq -lt 1) { continue }
        $k = $trimmed.Substring(0, $eq).Trim()
        $v = $trimmed.Substring($eq + 1)
        # Strip optional surrounding quotes (both ' and ").
        if ($v.Length -ge 2) {
            $first = $v[0]; $last = $v[$v.Length - 1]
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $v = $v.Substring(1, $v.Length - 2)
            }
        }
        $fileVars[$k] = $v
    }
    $missing = @()
    $count = 0
    foreach ($key in (Read-SecretKeys)) {
        if (-not $fileVars.ContainsKey($key)) {
            $missing += $key
            continue
        }
        Set-SecretEnv -Key $key -Value $fileVars[$key]
        $count++
    }
    if ($missing.Count -gt 0) {
        Fail-LoadSecrets 13 "keys missing in plaintext file: $($missing -join ', ')"
    }
    Write-Host "[load_secrets] plaintext: loaded $count key(s) from $PlaintextPath"
    Write-Warning "[load_secrets] plaintext mode is migration-period only. Cut over to a vault."
}

switch ($Vendor) {
    "1password" { Load-OnePassword }
    "bitwarden" { Load-Bitwarden }
    "plaintext" { Load-Plaintext }
    default {
        if ($env:TINYASSETS_SECRETS_PLAINTEXT_FALLBACK -eq "1" -and (Test-Path $PlaintextPath)) {
            Write-Warning "[load_secrets] unknown vendor '$Vendor'; falling back to plaintext (TINYASSETS_SECRETS_PLAINTEXT_FALLBACK=1)"
            Load-Plaintext
        } else {
            Fail-LoadSecrets 10 "unknown TINYASSETS_SECRETS_VENDOR='$Vendor' (expected: 1password | bitwarden | plaintext)"
        }
    }
}
