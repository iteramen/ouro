<#
.SYNOPSIS
    Regression suite for the intake-label template's run step: a failed `gh issue view`
    must fail the step, not be read as "no state label".
.DESCRIPTION
    `templates/issue-intake-label.yml`'s stamping step ships nowhere else in tests/: install-ouro
    only copies its bytes, and actionlint does not execute a `run:` block. This suite runs the
    step's own `run: |` text, extracted as text the same way the weekly-pass suite's harvest case
    lifts its step (tests/weekly-pass/Test-WeeklyPassSteps.Tests.ps1, "the harvest step, executed"),
    in a real bash behind a stub `gh` shell function -- the real gh is never called.

    The step is found by what it RUNS (its `gh issue edit` line), never by its `- name:`, so a
    rename does not quietly stop testing it.

    Four cases, from the issue's Deliverable: a failed `issue view` must fail the step with no
    edit; no labels must stamp; `idea` among the labels must leave the issue alone; and a
    non-state label that merely contains a state name (`needs-rulingx`) must still stamp --
    proving the grep is anchored to whole lines, not just that a read happened.

    A bash that cannot run a script given by its Windows path (the WSL launcher, which strips the
    backslashes) is no usable bash here: Get-WorkingBash proves each candidate with such a script,
    and the cases skip with a visible line when none passes, everywhere but Linux, where bash is
    always present and a skip is a detection failure. A vendored tree (bin/Vendor-Ouro.ps1 copies the
    scripts flat and drops templates/) skips the whole suite, as the weekly-pass suite does.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Two levels up is the plugin root (suite under tests/<area>/) or, once vendored, the scripts dir.
$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Template = Join-Path $Base 'templates/issue-intake-label.yml'
if (-not (Test-Path -LiteralPath $Template)) {
    if (Test-Path -LiteralPath (Join-Path $Base 'Test-DocsFreshness.ps1')) {
        Write-Host 'skip: no templates/ beside the flat gate scripts (vendored layout); the label workflow ships with the plugin' -ForegroundColor DarkGray
        exit 0
    }
    throw "templates/issue-intake-label.yml not found under $Base"
}

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ("$Expected" -eq "$Actual") { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}
function Assert-True($Cond, $What) {
    if ($Cond) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What" -ForegroundColor Red; $script:failures++ }
}

# Same shape as the weekly-pass suite's Get-StepBlocks: a step starts at `      - ` under `steps:`.
function Get-StepBlocks([string]$Text) {
    $lines = $Text -split "`r?`n"
    $starts = @(0..($lines.Count - 1) | Where-Object { $lines[$_] -match '^      - ' })
    $blocks = @()
    for ($i = 0; $i -lt $starts.Count; $i++) {
        $from = $starts[$i]
        $to = if ($i + 1 -lt $starts.Count) { $starts[$i + 1] - 1 } else { $lines.Count - 1 }
        $blocks += , ($lines[$from..$to] -join "`n")
    }
    return $blocks
}

function Get-WorkingBash {
    $seen = @{}
    $working = @()
    $probeDir = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('working-bash-' + [guid]::NewGuid().ToString('N')))
    $probe = Join-Path $probeDir.FullName 'probe.sh'
    [System.IO.File]::WriteAllText($probe, "printf ok`n", [System.Text.UTF8Encoding]::new($false))
    foreach ($cmd in (Get-Command bash -All -ErrorAction SilentlyContinue)) {
        $path = $cmd.Source
        if (-not $path -or $seen.ContainsKey($path)) { continue }
        $seen[$path] = $true
        $eap = $ErrorActionPreference
        $ok = $false
        try {
            $ErrorActionPreference = 'Continue'
            $out = (& $path $probe 2>$null | Out-String)
            $ok = ($LASTEXITCODE -eq 0 -and $out.Trim() -ceq 'ok')
        }
        catch { $ok = $false }
        finally { $ErrorActionPreference = $eap }
        if ($ok) { $working += $path }
    }
    Remove-Item -LiteralPath $probeDir.FullName -Recurse -Force
    $working | Sort-Object { if ($_ -match '[\\/][Gg]it[\\/]') { 0 } else { 1 } } | Select-Object -First 1
}

$Text = [System.IO.File]::ReadAllText($Template)
$blocks = @(Get-StepBlocks $Text)
$step = @($blocks | Where-Object { $_ -match 'gh issue edit' })
if ($step.Count -ne 1) { throw "expected exactly one step running gh issue edit, found $($step.Count)" }
$lines = @($step[0] -split "`n")
$runAt = [array]::FindIndex([string[]]$lines, [Predicate[string]]{ param($l) $l -match '^\s*run: \|\s*$' })
if ($runAt -lt 0) { throw 'the stamping step has no run: | block' }
$RunBody = (@($lines[($runAt + 1)..($lines.Count - 1)]) | ForEach-Object { $_ -replace '^ {10}', '' }) -join "`n"

if ($IsWindows) {
    $stubDir = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('wsl-launcher-stub-' + [guid]::NewGuid().ToString('N')))
    $savedPath = $env:PATH
    try {
        [System.IO.File]::WriteAllText((Join-Path $stubDir.FullName 'bash.cmd'), "@echo off`r`nif ""%1""==""-c"" exit /b 0`r`nexit /b 127`r`n")
        $env:PATH = $stubDir.FullName
        Assert-True ($null -eq (Get-WorkingBash)) 'a bash that passes -c but cannot run a script by its Windows path is not a working bash'
    }
    finally {
        $env:PATH = $savedPath
        Remove-Item -LiteralPath $stubDir.FullName -Recurse -Force
    }
}
else { Write-Host 'skip: the launcher-stub row runs on Windows only' -ForegroundColor DarkGray }

$gitBash = @(Get-Command bash -All -ErrorAction SilentlyContinue | ForEach-Object Source | Where-Object { $_ -match '[\\/][Gg]it[\\/]' })
if ($gitBash.Count -gt 0) {
    $found = Get-WorkingBash
    Assert-True ($found -and $found -match '[\\/][Gg]it[\\/]') 'a Git bash on PATH is still found by Get-WorkingBash'
}
else { Write-Host 'skip: no Git bash on PATH -- the Git-bash-found row is skipped' -ForegroundColor DarkGray }

if ($gitBash.Count -gt 0) {
    $noiseDir = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('bash-env-noise-' + [guid]::NewGuid().ToString('N')))
    $savedBashEnv = $env:BASH_ENV
    try {
        $noise = Join-Path $noiseDir.FullName 'noise.sh'
        [System.IO.File]::WriteAllText($noise, "echo startup-noise >&2`n", [System.Text.UTF8Encoding]::new($false))
        $env:BASH_ENV = $noise -replace '\\', '/'
        $found = Get-WorkingBash
        Assert-True ($found -and $found -match '[\\/][Gg]it[\\/]') 'a bash that writes a startup warning on stderr is still a working bash'
    }
    finally {
        $env:BASH_ENV = $savedBashEnv
        Remove-Item -LiteralPath $noiseDir.FullName -Recurse -Force
    }
}
else { Write-Host 'skip: no Git bash on PATH -- the stderr-noise row is skipped' -ForegroundColor DarkGray }

$bash = Get-WorkingBash
if (-not $bash) {
    if ($IsLinux) {
        Write-Host 'FAIL: no bash that runs is on PATH, but this is Linux, where one is always present' -ForegroundColor Red
        exit 1
    }
    Write-Host 'INFO: no bash that runs is on PATH -- the run-step cases are skipped' -ForegroundColor DarkGray
    if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
    exit 0
}

# Each case supplies the stub `gh issue view`'s exit code and stdout (already the shape the real
# `--jq '.labels[].name'` would print: one label name per line, or nothing). The function logs
# every call it sees, so the real gh is never reached and each assertion reads that log, not a
# guess about what ran.
$cases = @(
    @{ what = 'a failed issue view fails the step with no edit'
        viewRc = 1; viewOut = ''
        expectFail = $true; expectEdit = $false }
    @{ what = 'no labels: the step stamps needs-triage'
        viewRc = 0; viewOut = ''
        expectFail = $false; expectEdit = $true }
    @{ what = 'idea among the labels: the step leaves the issue alone'
        viewRc = 0; viewOut = "bug`nidea"
        expectFail = $false; expectEdit = $false }
    @{ what = 'a non-state label that only contains a state name (needs-rulingx) still stamps'
        viewRc = 0; viewOut = 'needs-rulingx'
        expectFail = $false; expectEdit = $true }
)

$scratch = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('issue-intake-label-' + [guid]::NewGuid().ToString('N')))
try {
    $i = 0
    foreach ($c in $cases) {
        $i++
        $dir = New-Item -ItemType Directory -Path (Join-Path $scratch.FullName "case$i")
        $ghLog = (Join-Path $dir.FullName 'gh.log') -replace '\\', '/'
        New-Item -ItemType File -Path (Join-Path $dir.FullName 'gh.log') | Out-Null
        $script = Join-Path $dir.FullName 'step.sh'
        $prelude = @"
GH_LOG='$ghLog'
: > "`$GH_LOG"
gh() {
  printf '%s\n' "gh `$*" >> "`$GH_LOG"
  if [ "`$1" = 'issue' ] && [ "`$2" = 'view' ]; then
    if [ $($c.viewRc) -ne 0 ]; then
      return $($c.viewRc)
    fi
    printf '%s' '$($c.viewOut)'
    return 0
  fi
  return 0
}
NUMBER=7
"@
        [System.IO.File]::WriteAllText($script, ($prelude + "`n" + $RunBody), [System.Text.UTF8Encoding]::new($false))
        $eap = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $childOut = (& $bash $script 2>&1 | Out-String)
            $rc = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $eap }
        $log = "$(Get-Content -LiteralPath (Join-Path $dir.FullName 'gh.log') -Raw)"

        $failed = ($rc -ne 0)
        Assert-Equal $c.expectFail $failed "$($c.what) -- exit status"
        $edited = [bool]($log -match 'gh issue edit 7 --add-label needs-triage')
        Assert-Equal $c.expectEdit $edited "$($c.what) -- gh issue edit call"
        if ($c.expectEdit) {
            $editCount = @([regex]::Matches($log, 'gh issue edit')).Count
            Assert-Equal 1 $editCount "$($c.what) -- exactly one edit call"
        }
        if (-not ($log -match 'gh issue view 7')) {
            Write-Host "FAIL: $($c.what) -- the stub gh was never asked to view the issue (log: $log)" -ForegroundColor Red
            $failures++
        }
        if ($failed -and -not $c.expectFail) {
            Write-Host "      output: $childOut" -ForegroundColor DarkYellow
        }
    }
}
finally { Remove-Item -LiteralPath $scratch.FullName -Recurse -Force }

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall issue-intake-label step cases pass" -ForegroundColor Green
exit 0
