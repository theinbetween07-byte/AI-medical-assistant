#requires -Version 5.1
<#
.SYNOPSIS
    Install the locked agent skills into <ProjectRoot>/.opencode/skills.

.DESCRIPTION
    Reads docs/agent-skills/skills.lock.json, fetches each repository at the
    pinned commit with a sparse checkout, and copies only the declared source
    path. Existing directories are skipped unless -Force is supplied.

.PARAMETER ProjectRoot
    Repository root. Defaults to two levels above this script.

.PARAMETER Skill
    Install only the named skill. Can be repeated.

.PARAMETER Force
    Overwrite existing skill directories. The old directory is renamed to
    <name>.bak.<timestamp> first.

.PARAMETER DryRun
    Print the plan without writing anything.
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [string[]]$Skill = @(),
    [switch]$Force,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path

$LockPath = Join-Path $PSScriptRoot "skills.lock.json"
if (-not (Test-Path -LiteralPath $LockPath)) {
    throw "Lock file not found: $LockPath"
}

$Lock = Get-Content -LiteralPath $LockPath -Raw -Encoding UTF8 | ConvertFrom-Json
$InstallRoot = Join-Path $ProjectRoot ($Lock.installRoot -replace '/', '\')
$TmpRoot = Join-Path $ProjectRoot ".deepworks\tmp\skills-src"

if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $TmpRoot | Out-Null
}

Write-Output "Project root : $ProjectRoot"
Write-Output "Install root : $InstallRoot"
Write-Output "Lock file    : $LockPath"
Write-Output ""

$Selected = @($Lock.skills)
if ($Skill.Count -gt 0) {
    $Selected = @($Selected | Where-Object { $Skill -contains $_.name })
    $missing = @($Skill | Where-Object { $Selected.name -notcontains $_ })
    if ($missing.Count -gt 0) {
        throw ("Unknown skill(s): " + ($missing -join ", "))
    }
}

$Failed = @()

foreach ($s in $Selected) {
    $name = [string]$s.name
    $target = Join-Path $InstallRoot $name
    Write-Output ("[{0}] {1} @ {2}" -f $s.layer, $name, $s.commit.Substring(0, 8))

    if ($DryRun) {
        Write-Output ("  plan: {0} -> {1}" -f $s.sourcePath, $target)
        continue
    }

    if (Test-Path -LiteralPath $target) {
        if (-not $Force) {
            Write-Output "  SKIP: already exists (use -Force to replace)"
            continue
        }
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $backup = "$target.bak.$stamp"
        Move-Item -LiteralPath $target -Destination $backup
        Write-Output "  backup: $backup"
    }

    $work = Join-Path $TmpRoot $name
    if (Test-Path -LiteralPath $work) {
        Remove-Item -LiteralPath $work -Recurse -Force
    }

    try {
        git init --quiet $work
        if ($LASTEXITCODE -ne 0) { throw "git init failed" }
        git -C $work remote add origin $s.repo
        if ($LASTEXITCODE -ne 0) { throw "git remote add failed" }

        $rev = $null
        git -C $work fetch --quiet --depth 1 --filter=blob:none origin $s.commit 2>$null
        if ($LASTEXITCODE -eq 0) {
            $rev = "FETCH_HEAD"
        } else {
            git -C $work fetch --quiet --depth 1 origin 2>$null
            if ($LASTEXITCODE -eq 0) { $rev = [string]$s.commit }
        }
        if (-not $rev) { throw "fetch failed for $($s.repo) @ $($s.commit)" }

        git -C $work sparse-checkout init --cone 2>$null | Out-Null
        git -C $work sparse-checkout set $s.sourcePath 2>$null | Out-Null
        git -C $work checkout --quiet --detach $rev 2>$null
        if ($LASTEXITCODE -ne 0) {
            git -C $work sparse-checkout disable 2>$null | Out-Null
            git -C $work checkout --quiet --detach $rev
            if ($LASTEXITCODE -ne 0) { throw "checkout failed for $rev" }
        }

        $src = Join-Path $work ($s.sourcePath -replace '/', '\')
        if (-not (Test-Path -LiteralPath (Join-Path $src "SKILL.md"))) {
            throw "SKILL.md not found at $src"
        }
        Copy-Item -LiteralPath $src -Destination $target -Recurse -Force

        $actual = (git -C $work rev-parse HEAD).Trim()
        Write-Output ("  OK: installed at {0} (HEAD {1})" -f $target, $actual.Substring(0, 8))
    } catch {
        $Failed += $name
        Write-Output ("  FAIL: " + $_.Exception.Message)
    }
}

if ($DryRun) {
    Write-Output ""
    Write-Output "Dry run complete. Nothing was written."
    return
}

Write-Output ""
Write-Output "--- verify ---"
foreach ($s in $Selected) {
    $path = Join-Path $InstallRoot $s.name
    $skill = Join-Path $path "SKILL.md"
    if (-not (Test-Path -LiteralPath $skill)) {
        Write-Output ("MISSING  {0}" -f $s.name)
        $Failed += $s.name
        continue
    }
    $line = Select-String -LiteralPath $skill -Pattern '^name:\s*(.+)$' -Encoding UTF8 | Select-Object -First 1
    $declared = if ($line) { $line.Matches[0].Groups[1].Value.Trim() } else { "<none>" }
    $flag = if ($declared -eq $s.name) { "OK" } else { "MISMATCH" }
    $count = (Get-ChildItem -LiteralPath $path -Recurse -File).Count
    Write-Output ("{0,-8} {1,-28} -> {2,-28} files={3}" -f $flag, $s.name, $declared, $count)
    if ($flag -ne "OK") { $Failed += $s.name }
}

if ($Failed.Count -gt 0) {
    Write-Output ""
    Write-Output ("Failed: " + (($Failed | Sort-Object -Unique) -join ", "))
    exit 1
}
Write-Output ""
Write-Output "All selected skills are installed and verified."
