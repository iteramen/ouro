<#
.SYNOPSIS
    Test that actions/gates/action.yml runs a consumer's own [[gate]] loop correctly: ci in place
    of run where a gate declares one, <ouro> resolved to the Action's own plugin root (never the
    caller's workspace), and both CI templates route a fork away from OURO_RUNS_ON.
.DESCRIPTION
    actions/gates/action.yml shipped with nothing pinning three of its own behaviours, so
    nothing here went red if any of them broke:
      (a) a gate whose run fails and ci passes must still pass the loop -- actions/gates/action.yml
          stops preferring ci over run.
      (b) <ouro> in a gate's run or ci must resolve to this Action's own plugin root, never $PWD or
          the caller's checked-out workspace: a gate script the plugin ships lives there, not in
          the consumer's repo.
      (c) templates/ci.yml and templates/docs-freshness.yml must each route a pull_request from a
          fork to ubuntu-latest rather than OURO_RUNS_ON's runner, because the install suite's -CI
          byte comparison reads the same template on both sides and cannot catch a clause deleted
          from it.

    (a) and (b) are driven for real: the "Gates from the binding" run: block is read out of the
    action file -- never restated here, the same reasoning as Test-ActionPath.Tests.ps1 -- with
    ${{ github.action_path }} filled with this Action's own directory (as GitHub fills it) and
    ${{ steps.python.outputs.python-path }} filled empty (the unpinned case; the action-path suite
    already proves the pinned one), then run in a scratch git "consumer" repo with a crafted
    .claude/ouro.toml, exactly as a bound repo's own workflow would run it.

    (c) is a text read of both templates: a mutation that deletes the fork clause from either
    breaks no execution here, so it is pinned as prose instead.

    (d) select-by-paths is driven the same way, in a consumer with real commits, with
    ${{ inputs.select-by-paths }}, ${{ github.event_name }},
    ${{ github.event.pull_request.base.sha }} and ${{ github.event.before }} filled per row; every
    other row fills them as an input left off on a push.

    A vendored tree (bin/Vendor-Ouro.ps1) copies the scripts flat and nothing under actions/, so
    there the suite prints a skip line and exits 0, as Test-ActionPath.Tests.ps1 does for
    actions/docs-freshness.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Action = Join-Path $Base 'actions/gates/action.yml'
if (-not (Test-Path -LiteralPath $Action)) {
    if (Test-Path -LiteralPath (Join-Path $Base 'Test-DocsFreshness.ps1')) {
        Write-Host "skip: no actions/ beside the flat gate scripts (vendored layout); the Action ships only with the plugin" -ForegroundColor DarkGray
        exit 0
    }
    throw "actions/gates/action.yml not found under $Base"
}

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -eq $Actual) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}
function Assert-Match($Pattern, $Output, $What) {
    if ($Output -match $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- no match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}
function Assert-NoMatch($Pattern, $Output, $What) {
    if ($Output -notmatch $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- unexpected match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}
function Get-DirIdentity([string]$Dir) {
    try { (Get-Item -LiteralPath $Dir -ErrorAction Stop).FullName.TrimEnd('\', '/') }
    catch { $Dir.TrimEnd('\', '/') }
}
function Get-PathWithoutPython3 {
    $drop = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($c in @(Get-Command python3 -All -ErrorAction SilentlyContinue)) {
        if ($c.Source) { [void]$drop.Add((Get-DirIdentity (Split-Path -Parent $c.Source))) }
    }
    $keep = @()
    foreach ($dir in ($env:PATH -split [System.IO.Path]::PathSeparator)) {
        if ([string]::IsNullOrWhiteSpace($dir)) { continue }
        if ($dir -match 'WindowsApps') { continue }
        if ($drop.Contains((Get-DirIdentity $dir))) { continue }
        $keep += $dir
    }
    ($keep -join [System.IO.Path]::PathSeparator)
}

# CI must exercise the declared minimum, rather than hiding failures behind a newer runtime.
$actionText = Get-Content -LiteralPath $Action -Raw
Assert-Match "(?m)^\s+python-version: '3\.12'\s*$" $actionText 'gates Action exercises Python 3.12'
Assert-Match '(?m)^  selection-mode:\r?$' $actionText 'the Action declares its additive selection-mode output'
Assert-Match '(?m)^    value: \$\{\{ steps\.loop\.outputs\.selection-mode \}\}\r?$' $actionText 'the public output maps to the actual loop step'
Assert-Match '(?m)^      id: loop\r?$' $actionText 'the gate loop carries the id used by its output'

# Exercise the workflow's actual classification, including suites that spell SKIP in capitals.
$providerText = Get-Content -LiteralPath (Join-Path $Base '.github/workflows/no-binding.yml') -Raw
$classification = [regex]::Match($providerText,
    '(?m)^[ \t]+\$cases = [^\r\n]*\r?\n[ \t]+\$verdict = [^\r\n]*').Value
Assert-Equal $true ([bool]$classification) 'No binding exposes one suite classification block'
$classify = [scriptblock]::Create($classification + "`n`$verdict")
foreach ($row in @(
    @{ Lines = @('skip: no subject'); Code = 0; Want = 'SKIP' },
    @{ Lines = @('SKIP: runtime suite requires the plugin layout'); Code = 0; Want = 'SKIP' },
    @{ Lines = @('  SkIp: no subject  ', ''); Code = 0; Want = 'SKIP' },
    @{ Lines = @('SKIP: optional row', 'a real case passed'); Code = 0; Want = 'PASS' },
    @{ Lines = @('SKIP: no subject'); Code = 1; Want = 'FAIL' }
)) {
    $out, $code = $row.Lines, $row.Code
    Assert-Equal $row.Want (& $classify) "No binding classifies '$($row.Lines -join '; ')' at exit $code"
}

# Execute the provider's own fresh-copy inventory guard, including equal-count substitutions.
$inventory = [regex]::Match($providerText, '(?ms)^          function Get-SuitePaths\(.*?(?=^          # A red suite)').Value
Assert-Equal $true ([bool]$inventory) 'No binding exposes its fresh relative-path inventory guard'
foreach ($name in 'driver', 'runtime') {
    Assert-Equal 2 ([regex]::Matches($providerText, "(?m)^      - '!tests/$name/\*\*'\s*$").Count) "both provider triggers exclude only tests/$name/"
}
if ($inventory) {
    $inventoryRoot = Join-Path ([IO.Path]::GetTempPath()) ('flat-inventory-' + [guid]::NewGuid().ToString('N'))
    $plugin = Join-Path $inventoryRoot 'plugin'
    $consumer = Join-Path $inventoryRoot 'consumer'
    try {
        foreach ($rel in 'ordinary/Real.Tests.ps1', 'runtime-extra/Keep.Tests.py', 'driver/Driver.Tests.py', 'runtime/Runtime.Tests.py') {
            $file = Join-Path $plugin "tests/$rel"
            New-Item -ItemType Directory -Force -Path (Split-Path $file -Parent) | Out-Null
            [IO.File]::WriteAllText($file, '# generated inventory fixture')
            if ($rel -notmatch '^(?:driver|runtime)/') {
                $dest = Join-Path $consumer "scripts/tests/$rel"
                New-Item -ItemType Directory -Force -Path (Split-Path $dest -Parent) | Out-Null
                Copy-Item -LiteralPath $file -Destination $dest
            }
        }
        $probe = Join-Path $inventoryRoot 'inventory.ps1'
        [IO.File]::WriteAllText($probe, ("`$ErrorActionPreference='Stop'`nSet-Location -LiteralPath '" + $plugin.Replace("'", "''") + "'`n`$root='" + $consumer.Replace("'", "''") + "'`n" + $inventory))
        $probePwsh = (Get-Command pwsh).Source
        $result = & $probePwsh -NoProfile -File $probe *>&1
        Assert-Equal 0 $LASTEXITCODE 'fresh inventory retains ordinary and similarly-prefixed suites, omitting only the two plugin roots'
        $ordinary = Join-Path $consumer 'scripts/tests/ordinary/Real.Tests.ps1'
        $saved = [IO.File]::ReadAllBytes($ordinary)
        Remove-Item -LiteralPath $ordinary
        $result = & $probePwsh -NoProfile -File $probe *>&1
        Assert-Equal 1 $LASTEXITCODE 'fresh inventory refuses a missing runnable suite'
        $replacement = Join-Path (Split-Path $ordinary -Parent) 'Wrong.Tests.ps1'
        [IO.File]::WriteAllBytes($replacement, $saved)
        $result = & $probePwsh -NoProfile -File $probe *>&1
        Assert-Equal 1 $LASTEXITCODE 'fresh inventory refuses an equal-count substituted suite path'
        Remove-Item -LiteralPath $replacement
        [IO.File]::WriteAllBytes($ordinary, $saved)
        $extra = Join-Path $consumer 'scripts/tests/runtime/Unexpected.Tests.py'
        New-Item -ItemType Directory -Force -Path (Split-Path $extra -Parent) | Out-Null
        [IO.File]::WriteAllText($extra, '# unexpected plugin-only suite')
        $result = & $probePwsh -NoProfile -File $probe *>&1
        Assert-Equal 1 $LASTEXITCODE 'fresh inventory refuses unexpected plugin-only destination suites'
    }
    finally { if (Test-Path -LiteralPath $inventoryRoot) { Remove-Item -LiteralPath $inventoryRoot -Recurse -Force } }
}

# --- (c) both CI templates route a fork's pull_request away from OURO_RUNS_ON, as prose --------
# Not exercised by any workflow run here, so pinned by reading the runs-on line itself, never a
# second copy of the clause: a mutation that deletes it from either template must show up as a
# missing match, not a restated string that only proves it was restated.
foreach ($t in @('templates/ci.yml', 'templates/docs-freshness.yml')) {
    $path = Join-Path $Base $t
    if (Test-Path -LiteralPath $path) {
        $text = Get-Content -LiteralPath $path -Raw
        Assert-Match "(?m)^\s*runs-on:.*head\.repo\.full_name != github\.repository" $text `
            "${t}: runs-on routes a fork's pull_request away from OURO_RUNS_ON"
    }
    else { Write-Host "  skip: $t not found" -ForegroundColor DarkGray }
}

# --- (a) and (b): the action's own run: block, executed against a scratch consumer -------------
# A block line is either properly indented content or blank -- the gate loop's own blank lines
# between comment paragraphs carry no indentation at all, so a line pattern that requires the
# block's indent on every line (Test-ActionPath.Tests.ps1's, whose own action file has none
# inside its one run: block) truncates this one at the first blank line.
$actionText = Get-Content -LiteralPath $Action -Raw
$runBlocks = @([regex]::Matches($actionText, '(?m)^([ \t]+)run: \|\r?\n((?:(?:\1[ \t]*\S.*|[ \t]*)(?:\r?\n|$))+)') |
    ForEach-Object { $_.Groups[2].Value })
Assert-Equal 1 $runBlocks.Count 'the action file runs exactly one script block'

$pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
$pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $pwshCmd -or -not $pythonCmd -or $runBlocks.Count -ne 1) {
    Write-Host '  skip: no pwsh or python3 on PATH to run the gate step lines here' -ForegroundColor DarkGray
}
else {
    # ${{ github.action_path }} is this Action's own directory, exactly as GitHub fills it for a
    # composite step; ${{ steps.python.outputs.python-path }} is filled empty (unpinned), which
    # Test-ActionPath.Tests.ps1 already proves leaves PATH alone -- what is under test here is the
    # gate loop itself, not the pin.
    $ActionDir = Join-Path $Base 'actions/gates'
    $script = $runBlocks[0].Replace('${{ github.action_path }}', $ActionDir).
        Replace('${{ steps.python.outputs.python-path }}', '')

    # The composite step runs in the job workspace: the caller's checkout. A scratch git repo, not
    # this checkout, so ouro-binding.py's default_path (git rev-parse --show-toplevel +
    # .claude/ouro.toml) reads a binding this suite crafts rather than the plugin's own.
    # The selection expressions default to an input left off on a push, which has no base commit.
    function Invoke-GateStep {
        param([string]$ConsumerDir, [string]$Select = 'false', [string]$EventName = 'push', [string]$BaseSha = '', [string]$Before = '', [string]$Summary = '', [string]$Output = '', [string]$Parallel = '1')
        $body = $script.Replace('${{ inputs.select-by-paths }}', $Select).Replace('${{ inputs.parallel }}', $Parallel).
            Replace('${{ github.event_name }}', $EventName).
            Replace('${{ github.event.pull_request.base.sha }}', $BaseSha).
            Replace('${{ github.event.before }}', $Before)
        $savedSummary = $env:GITHUB_STEP_SUMMARY
        $savedOutput = $env:GITHUB_OUTPUT
        $env:GITHUB_STEP_SUMMARY = $Summary
        $env:GITHUB_OUTPUT = $Output
        Push-Location -LiteralPath $ConsumerDir
        try {
            $out = & $pwshCmd.Source -NoProfile -Command $body *>&1
            [pscustomobject]@{ Code = $LASTEXITCODE; Text = ($out | ForEach-Object { "$_" }) -join "`n" }
        }
        finally { Pop-Location; $env:GITHUB_STEP_SUMMARY = $savedSummary; $env:GITHUB_OUTPUT = $savedOutput }
    }

    $Consumer = Join-Path ([System.IO.Path]::GetTempPath()) ('action-gates-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    try {
        New-Item -ItemType Directory -Force -Path $Consumer | Out-Null
        git -C $Consumer init -q 2>$null
        if ($LASTEXITCODE -ne 0) { throw "git init in $Consumer failed (exit $LASTEXITCODE)" }
        New-Item -ItemType Directory -Force -Path (Join-Path $Consumer '.claude') | Out-Null
        $bindingPath = Join-Path $Consumer '.claude/ouro.toml'

        # (a) a gate whose run fails and ci passes: the loop must run ci, and pass. Each command
        # is a real child process (pwsh -NoProfile -Command exit N), the same shape a bound repo's
        # own [[gate]] entries take, so $LASTEXITCODE inside the loop is that process's own exit,
        # never the loop's.
        Set-Content -LiteralPath $bindingPath -Encoding utf8 -Value @(
            'schema = 1'
            ''
            '[[gate]]'
            'run = "pwsh -NoProfile -Command exit 1"'
            'ci = "pwsh -NoProfile -Command exit 0"'
        )
        $fullOutput = Join-Path $Consumer 'selection-full.txt'
        $r = Invoke-GateStep -ConsumerDir $Consumer -Output $fullOutput
        Assert-Equal "selection-mode=full`n" ([IO.File]::ReadAllText($fullOutput)) 'full output contains only the fixed selection enum'
        Assert-Equal 0 $r.Code 'a gate whose run fails and ci passes: the loop runs ci in its place and passes'
        Assert-Match 'PASS exit=0' $r.Text 'the gate that ran is reported PASS'
        Assert-Match '1/1 gates passed' $r.Text 'exactly the one gate ran, through ci'
        Assert-Match 'TIMING gate=1 result=PASS seconds=\d+\.\d{3} platform=' $r.Text 'timings remain available without a job-summary file'

        # parallel: each gate marks its start and waits for the other's mark, so both pass only
        # when they run at the same time; a third gate fails, and is still reported by name.
        Set-Content -LiteralPath (Join-Path $Consumer 'meet.py') -Encoding utf8 -Value @(
            'import pathlib, sys, time'
            'pathlib.Path(sys.argv[1]).touch()'
            'deadline = time.monotonic() + 60'
            'while not pathlib.Path(sys.argv[2]).exists() and time.monotonic() < deadline: time.sleep(0.05)'
            'print("met" if pathlib.Path(sys.argv[2]).exists() else "alone")'
            'print("on-stderr-" + sys.argv[1], file=sys.stderr)'
            'sys.exit(0 if pathlib.Path(sys.argv[2]).exists() else 3)'
        )
        Set-Content -LiteralPath $bindingPath -Encoding utf8 -Value @(
            'schema = 1', '',
            '[[gate]]', 'run = "python3 meet.py a.mark b.mark"', '',
            '[[gate]]', 'run = "python3 meet.py b.mark a.mark"', '',
            '[[gate]]', 'run = "pwsh -NoProfile -Command exit 4"'
        )
        $r = Invoke-GateStep -ConsumerDir $Consumer -Parallel '2'
        Assert-Equal 1 $r.Code 'parallel 2: the failing gate fails the loop'
        Assert-Equal 2 ([regex]::Matches($r.Text, '(?m)^met\s*$').Count) 'parallel 2: the two meeting gates ran at the same time, each output in its own group'
        Assert-Match '2/3 gates passed' $r.Text 'parallel 2: every gate is counted'
        Assert-Equal 2 ([regex]::Matches($r.Text, 'on-stderr-[ab]\.mark').Count) "parallel 2: each gate's stderr is in its output"
        Assert-Match '::error::gate failed: pwsh -NoProfile -Command exit 4' $r.Text 'parallel 2: the failed gate is named'
        Remove-Item -LiteralPath (Join-Path $Consumer 'a.mark'), (Join-Path $Consumer 'b.mark')
        $r = Invoke-GateStep -ConsumerDir $Consumer -Parallel 'x'
        Assert-Equal 1 $r.Code 'parallel x: refused'
        Assert-Match 'parallel must be a positive whole number' $r.Text 'parallel x: the refusal names the input'

        # (b) <ouro> resolves to this Action's own plugin root, never the caller's workspace. A
        # helper script, not an inline expression, so the check runs in the child process the
        # loop actually spawns rather than being evaluated by the calling shell while building its
        # argument list.
        $checkRoot = Join-Path $Consumer 'check-root.ps1'
        Set-Content -LiteralPath $checkRoot -Encoding utf8 -Value @(
            'param([string]$OuroRoot, [string]$ConsumerRoot)'
            "`$marker = Join-Path `$OuroRoot 'bin/ouro-binding.py'"
            "`$exists = Test-Path -LiteralPath `$marker"
            "`$sameAsConsumer = (`$OuroRoot.TrimEnd('\','/')) -ieq (`$ConsumerRoot.TrimEnd('\','/'))"
            'if ($exists -and -not $sameAsConsumer) { exit 0 } else { exit 1 }'
        )
        $checkRootFwd = $checkRoot -replace '\\', '/'
        $consumerFwd = $Consumer -replace '\\', '/'
        Set-Content -LiteralPath $bindingPath -Encoding utf8 -Value @(
            'schema = 1'
            ''
            '[[gate]]'
            "run = ""pwsh -NoProfile -File '$checkRootFwd' -OuroRoot '<ouro>' -ConsumerRoot '$consumerFwd'"""
        )
        $callerOutput = Join-Path $Consumer 'caller-output.txt'
        [IO.File]::WriteAllText($callerOutput, 'caller metadata')
        $originalOutput = $env:GITHUB_OUTPUT
        try {
            $env:GITHUB_OUTPUT = $callerOutput
            $r2 = Invoke-GateStep -ConsumerDir $Consumer
            Assert-Equal 'caller metadata' ([IO.File]::ReadAllText($callerOutput)) 'a missing-output fixture cannot pollute the real caller metadata'
            Assert-Equal $callerOutput $env:GITHUB_OUTPUT 'fixture restores its caller output environment'
        }
        finally { $env:GITHUB_OUTPUT = $originalOutput }
        Assert-NoMatch 'selection output could not be written' $r2.Text 'missing GITHUB_OUTPUT is normal and isolated from the caller'
        Assert-Equal 0 $r2.Code '<ouro> resolves to bin/ouro-binding.py beside the Action, not the consumer workspace'
        Assert-Match 'PASS exit=0' $r2.Text 'the root-resolution gate is reported PASS'

        # Real failure timing must include the command's work, preserve its exit and continue.
        New-Item -ItemType Directory -Path (Join-Path $Consumer 'private-suite-dir') | Out-Null
        Set-Content -LiteralPath (Join-Path $Consumer 'private-suite-dir/timing-fail.ps1') -Encoding utf8 -Value @('Start-Sleep -Milliseconds 180', 'exit 2')
        Set-Content -LiteralPath $bindingPath -Encoding utf8 -Value @(
            'schema = 1', '[[gate]]'
            'run = "pwsh -NoProfile -Command exit 0"'
            'ci = "pwsh -NoProfile -File private-suite-dir/timing-fail.ps1 TIMING_ARGUMENT_CANARY"'
            '[[gate]]', 'run = "pwsh -NoProfile -Command exit 0"'
        )
        $summaryFile = Join-Path $Consumer 'timings.md'
        Set-Content -LiteralPath $summaryFile -Encoding utf8 -Value '# Previous step'
        $failedOutput = Join-Path $Consumer 'selection-failed.txt'
        $timed = Invoke-GateStep -ConsumerDir $Consumer -Summary $summaryFile -Output $failedOutput
        Assert-Equal "selection-mode=full`n" ([IO.File]::ReadAllText($failedOutput)) 'selection output does not hide an actual gate failure'
        $summary = [IO.File]::ReadAllText($summaryFile)
        Assert-Equal 1 $timed.Code 'timing a failed gate retains the loop failure'
        Assert-Match 'FAIL exit=2' $timed.Text 'the original failed-command exit remains visible'
        Assert-Match 'PASS exit=0' $timed.Text 'a real succeeding gate still runs after failure'
        Assert-Match '^# Previous step' $summary 'timings append without replacing another step summary'
        Assert-Match 'selection: full; selected: 2/2; skipped: 0; failed: 1' $summary 'full summary counts actual selected and failed gates'
        $elapsed = [regex]::Match($summary, '(?m)^\| 1 \| timing-fail\.ps1 \| FAIL \| (\d+\.\d{3}) \|')
        Assert-Equal $true $elapsed.Success 'summary identifies a plain script by its safe basename'
        if ($elapsed.Success) {
            Assert-Equal $true ([double]::Parse($elapsed.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture) -ge 0.15) 'failed duration includes the actual fixture sleep'
        }
        Assert-Match '\| 2 \| gate #2 \| PASS \|' $summary 'non-script commands use their binding index'
        Assert-NoMatch 'TIMING_ARGUMENT_CANARY|private-suite-dir|pwsh|NoProfile' $summary 'summary adds no command arguments or directories'
        $badSummary = Invoke-GateStep -ConsumerDir $Consumer -Summary $Consumer -Output $Consumer
        Assert-Equal 1 $badSummary.Code 'an unwritable summary cannot replace the original failure exit'
        Assert-Match '::warning::gate timing summary could not be written' $badSummary.Text 'summary-write failure is observable without its private path'
        Assert-Match '::warning::gate selection output could not be written' $badSummary.Text 'failed output write cannot mask the original gate failure'

        # A missing python3 must fail with one clear error, not the misleading "the binding
        # declares no [[gate]] entries" that follows a swallowed read.
        Set-Content -LiteralPath $bindingPath -Encoding utf8 -Value @(
            'schema = 1'
            ''
            '[[gate]]'
            'run = "pwsh -NoProfile -Command exit 0"'
        )
        $passingBadSummary = Invoke-GateStep -ConsumerDir $Consumer -Summary $Consumer -Output $Consumer
        Assert-Equal 0 $passingBadSummary.Code 'an unwritable summary cannot turn a passing gate red'
        Assert-Match '::warning::gate selection output could not be written' $passingBadSummary.Text 'failed output write also preserves a passing gate'
        $noPySaved = $env:PATH
        try {
            $env:PATH = Get-PathWithoutPython3
            if (-not (Get-Command python3 -ErrorAction SilentlyContinue)) {
                $r3 = Invoke-GateStep -ConsumerDir $Consumer
                Assert-Match '::error::no python3 on PATH' $r3.Text 'no python3 on PATH fails with one clear error'
                Assert-NoMatch 'the binding declares no \[\[gate\]\] entries' $r3.Text 'not the misleading empty-gates message'
                Assert-Equal 1 $r3.Code 'the missing-interpreter stop exits 1'
            }
            else { Write-Host '  skip: python3 could not be hidden from PATH on this machine' -ForegroundColor DarkGray }
        }
        finally { $env:PATH = $noPySaved }
    }
    finally {
        if (Test-Path -LiteralPath $Consumer) { Remove-Item -LiteralPath $Consumer -Recurse -Force }
    }

    # (d) select-by-paths, in a consumer with real commits: gate A declares ./a/ less a/skip.txt, B x/ and
    # :(glob)b/**, D *.md, and C declares none. Each row commits one change on top of the base
    # commit and runs the block with the input on, as a pull_request against that base, unless the
    # row says otherwise. A skipped gate's PASS pattern is the one every full-set row matches.
    $Sel = Join-Path ([System.IO.Path]::GetTempPath()) ('action-gates-sel-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    try {
        New-Item -ItemType Directory -Path $Sel | Out-Null
        function Invoke-SelGit {
            git -C $Sel -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false @args
            if ($LASTEXITCODE -ne 0) { throw "git $args in $Sel failed (exit $LASTEXITCODE)" }
        }
        Invoke-SelGit init -q
        foreach ($f in @('a/f.txt', 'b/f.txt', 'z/f.txt', 'ab/f.txt', 'd/deep.md', '.github/workflows/w.yml', 'actions/gates/action.yml', 'actions/gates-x/f.txt', '.claude/ouro.toml')) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent (Join-Path $Sel $f)) | Out-Null
        }
        foreach ($f in @('a/f.txt', 'a/skip.txt', 'b/f.txt', 'z/f.txt', 'ab/f.txt', 'top.md', 'd/deep.md', '.github/workflows/w.yml', 'actions/gates/action.yml', 'actions/gates-x/f.txt')) {
            Set-Content -LiteralPath (Join-Path $Sel $f) -Encoding utf8 -Value '# base'
        }
        Set-Content -LiteralPath (Join-Path $Sel '.claude/ouro.toml') -Encoding utf8 -Value @(
            'schema = 1'
            ''
            '[[gate]]'
            'run = "pwsh -NoProfile -Command exit 0 # gate-A"'
            'paths = ["./a/", ":(exclude)a/skip.txt"]'
            ''
            '[[gate]]'
            'run = "pwsh -NoProfile -Command exit 0 # gate-B"'
            'paths = ["x/", ":(glob)b/**"]'
            ''
            '[[gate]]'
            'run = "pwsh -NoProfile -Command exit 0 # gate-C"'
            ''
            '[[gate]]'
            'run = "pwsh -NoProfile -Command exit 0 # gate-D"'
            'paths = ["*.md"]'
        )
        Invoke-SelGit add -A
        Invoke-SelGit commit -q -m base
        $selBase = (git -C $Sel rev-parse HEAD).Trim()

        function Invoke-SelectRow {
            param([string]$Change, [string]$Select = 'true', [string]$EventName = 'pull_request', [string]$BaseSha = $selBase, [string]$Before = '', [string]$Summary = '', [string]$Output = '')
            Invoke-SelGit checkout -q --detach $selBase
            Add-Content -LiteralPath (Join-Path $Sel $Change) -Encoding utf8 -Value '# changed'
            Invoke-SelGit commit -q -am "change $Change"
            Invoke-GateStep -ConsumerDir $Sel -Select $Select -EventName $EventName -BaseSha $BaseSha -Before $Before -Summary $Summary -Output $Output
        }
        function Assert-Selection($R, [string[]]$Ran, [string[]]$Skipped, [string]$Row) {
            foreach ($n in $Ran) { Assert-Match "PASS exit=0  .*# gate-$n" $R.Text "${Row}: gate $n runs" }
            foreach ($n in $Skipped) {
                Assert-NoMatch "PASS exit=0  .*# gate-$n" $R.Text "${Row}: gate $n does not run"
                Assert-Match "SKIP .*# gate-$n" $R.Text "${Row}: gate $n is skipped, and the log names it"
            }
            if ($Skipped.Count -eq 0) { Assert-NoMatch 'SKIP ' $R.Text "${Row}: no gate is skipped" }
            Assert-Match "--- $($Ran.Count)/$($Ran.Count) gates passed, $($Skipped.Count) skipped" $R.Text "${Row}: the summary counts the gates that ran and the skipped"
            Assert-Equal 0 $R.Code "${Row}: the loop passes"
        }

        $r = Invoke-SelectRow -Change 'z/f.txt' -Select 'false'
        Assert-Selection $r @('A', 'B', 'C', 'D') @() 'input off'
        Assert-NoMatch 'select-by-paths' $r.Text 'input off: the loop logs no selection'

        # git normalises ./a/ to a/ only when it sees the entry as written.
        $selectedSummary = Join-Path $Sel 'selected-timings.md'
        $pathsOutput = Join-Path $Sel 'selection-paths.txt'
        $r = Invoke-SelectRow -Change 'a/f.txt' -Summary $selectedSummary -Output $pathsOutput
        Assert-Equal "selection-mode=paths`n" ([IO.File]::ReadAllText($pathsOutput)) 'a real path-selected diff emits paths only'
        $summary = [IO.File]::ReadAllText($selectedSummary)
        Assert-Match 'selection: paths; selected: 2/4; skipped: 2; failed: 0' $summary 'selective summary reports the real selection'
        Assert-Match '\| 2 \| gate #2 \| SKIP \| - \|' $summary 'an unselected gate has no invented execution duration'
        Assert-Match '\| 3 \| gate #3 \| PASS \| \d+\.\d{3} \|' $summary 'selected rows retain their original binding index'
        Assert-NoMatch 'gate-A|gate-B|gate-C|gate-D' $summary 'selection summary excludes command comments'
        Assert-Selection $r @('A', 'C') @('B', 'D') 'a diff touching only a/, under ./a/'
        Assert-NoMatch 'every gate runs' $r.Text 'a diff touching only a/: no full set'

        $r = Invoke-SelectRow -Change 'b/f.txt'
        Assert-Selection $r @('B', 'C') @('A', 'D') 'a diff touching only b/, under B''s second entry, :(glob)b/**'

        # A wildcard entry matches at every level, never only the files it would glob in the root:
        # top.md exists and is unchanged, and pwsh on Linux and macOS globs a bare *.md to it when
        # git is called through pwsh's native-argument binder.
        $r = Invoke-SelectRow -Change 'd/deep.md'
        Assert-Selection $r @('C', 'D') @('A', 'B') 'a diff touching only d/deep.md, under *.md'

        $r = Invoke-SelectRow -Change 'z/f.txt'
        Assert-Selection $r @('C') @('A', 'B', 'D') 'a diff touching only an unclaimed path'
        Assert-NoMatch 'every gate runs' $r.Text 'an unclaimed path: no full set'

        Invoke-SelGit checkout -q --detach $selBase
        $r = Invoke-GateStep -ConsumerDir $Sel -Select 'true' -EventName 'pull_request' -BaseSha $selBase
        Assert-Selection $r @('C') @('A', 'B', 'D') 'an empty diff'

        # The binding is a path like any other: a gate that reads it names it in its paths.
        $r = Invoke-SelectRow -Change '.claude/ouro.toml'
        Assert-Selection $r @('C') @('A', 'B', 'D') 'a changed .claude/ouro.toml'
        Assert-NoMatch 'every gate runs' $r.Text 'a changed binding: no full set'

        $r = Invoke-SelectRow -Change '.github/workflows/w.yml'
        Assert-Selection $r @('C') @('A', 'B', 'D') 'a change under .github/workflows/'
        Assert-NoMatch 'every gate runs' $r.Text 'a changed workflow: no full set'

        $r = Invoke-SelectRow -Change 'actions/gates/action.yml'
        Assert-Selection $r @('A', 'B', 'C', 'D') @() 'a change under actions/gates/'
        Assert-Match 'select-by-paths: every gate runs: the diff touches actions/gates/' $r.Text 'a changed gates Action: the log names the reason'

        # A sibling directory sharing the prefix is not the Action.
        $r = Invoke-SelectRow -Change 'actions/gates-x/f.txt'
        Assert-Selection $r @('C') @('A', 'B', 'D') 'a change under actions/gates-x/'
        Assert-NoMatch 'every gate runs' $r.Text 'a sibling of the gates Action: no full set'

        # The one diff every selector reads: an exclude pathspec drops its path, a sibling sharing
        # a prefix is another directory, and with no rename pairing a delete or a move is seen at
        # the path it left.
        $r = Invoke-SelectRow -Change 'a/skip.txt'
        Assert-Selection $r @('C') @('A', 'B', 'D') 'a diff touching only the path gate A excludes'
        $r = Invoke-SelectRow -Change 'ab/f.txt'
        Assert-Selection $r @('C') @('A', 'B', 'D') 'a diff touching only ab/, a sibling of a/'
        Invoke-SelGit checkout -q --detach $selBase
        Invoke-SelGit rm -q -- a/f.txt
        Invoke-SelGit commit -q -m 'delete a/f.txt'
        $r = Invoke-GateStep -ConsumerDir $Sel -Select 'true' -EventName 'pull_request' -BaseSha $selBase
        Assert-Selection $r @('A', 'C') @('B', 'D') 'a diff that deletes a/f.txt'
        Invoke-SelGit checkout -q --detach $selBase
        Invoke-SelGit mv -- b/f.txt z/moved.txt
        Invoke-SelGit commit -q -m 'move b/f.txt to z/'
        $r = Invoke-GateStep -ConsumerDir $Sel -Select 'true' -EventName 'pull_request' -BaseSha $selBase
        Assert-Selection $r @('B', 'C') @('A', 'D') 'a diff that moves b/f.txt out of b/'

        # A push selects by the diff against the tip the branch had before it, github.event.before;
        # GitHub fills no pull_request base on a push.
        $r = Invoke-SelectRow -Change 'a/f.txt' -EventName 'push' -BaseSha '' -Before $selBase
        Assert-Selection $r @('A', 'C') @('B', 'D') 'a push whose before is the base'
        Assert-NoMatch 'every gate runs' $r.Text 'a push with a previous tip: no full set'

        $r = Invoke-SelectRow -Change 'z/f.txt' -EventName 'push' -BaseSha '' -Before ''
        Assert-Selection $r @('A', 'B', 'C', 'D') @() 'a push with no before'
        Assert-Match 'select-by-paths: every gate runs: the push has no previous tip' $r.Text 'a push with no before: the log names the reason'

        $r = Invoke-SelectRow -Change 'z/f.txt' -EventName 'push' -BaseSha '' -Before '0000000000000000000000000000000000000000'
        Assert-Selection $r @('A', 'B', 'C', 'D') @() 'a push of a new branch or a tag'
        Assert-Match 'select-by-paths: every gate runs: the push has no previous tip' $r.Text 'a zero before: the log names the reason'

        $r = Invoke-SelectRow -Change 'z/f.txt' -EventName 'push' -BaseSha '' -Before '0123456789abcdef0123456789abcdef01234567'
        Assert-Selection $r @('A', 'B', 'C', 'D') @() 'a push whose before is not in the checkout'
        Assert-Match 'select-by-paths: every gate runs: the diff against \S+ cannot be read' $r.Text 'a before the checkout lacks: the log names the reason'

        $fallbackSummary = Join-Path $Sel 'fallback-timings.md'
        $fallbackOutput = Join-Path $Sel 'selection-fallback.txt'
        $r = Invoke-SelectRow -Change 'z/f.txt' -EventName 'schedule' -BaseSha '' -Summary $fallbackSummary -Output $fallbackOutput
        Assert-Equal "selection-mode=fallback`n" ([IO.File]::ReadAllText($fallbackOutput)) 'a full event fallback emits fallback only'
        $summary = [IO.File]::ReadAllText($fallbackSummary)
        Assert-Match 'selection: fallback; selected: 4/4; skipped: 0; failed: 0' $summary 'scheduled full fallback is reported truthfully'
        $expectedPlatform = if ($IsWindows) { 'Windows' } elseif ($IsLinux) { 'Linux' } elseif ($IsMacOS) { 'macOS' } else { 'other' }
        Assert-Match "Platform: $expectedPlatform;" $summary 'summary reports the actual executing platform'
        Assert-Selection $r @('A', 'B', 'C', 'D') @() 'a schedule event'
        Assert-Match 'select-by-paths: every gate runs: the event is schedule, not pull_request or push' $r.Text 'a schedule event: the log names the reason'

        $r = Invoke-SelectRow -Change 'z/f.txt' -EventName 'workflow_dispatch' -BaseSha ''
        Assert-Selection $r @('A', 'B', 'C', 'D') @() 'a manual workflow dispatch'
        Assert-Match 'select-by-paths: every gate runs: the event is workflow_dispatch, not pull_request or push' $r.Text 'manual dispatch retains the full-set fallback'

        $badBaseOutput = Join-Path $Sel 'selection-bad-base.txt'
        $r = Invoke-SelectRow -Change 'z/f.txt' -BaseSha '0123456789abcdef0123456789abcdef01234567' -Output $badBaseOutput
        Assert-Equal "selection-mode=fallback`n" ([IO.File]::ReadAllText($badBaseOutput)) 'an unreadable base emits conservative fallback'
        Assert-Selection $r @('A', 'B', 'C', 'D') @() 'a base commit missing from the checkout'
        Assert-Match 'select-by-paths: every gate runs: the diff against \S+ cannot be read' $r.Text 'a missing base: the log names the reason'

        $r = Invoke-SelectRow -Change 'z/f.txt' -BaseSha ''
        Assert-Selection $r @('A', 'B', 'C', 'D') @() 'no base commit'
        Assert-Match 'select-by-paths: every gate runs: the pull request has no base commit' $r.Text 'no base commit: the log names the reason'

        # A git call that fails after gate A was already found unchanged: B's pathspec has magic
        # git refuses. The edit stays uncommitted, so the diff never touches the binding.
        Invoke-SelGit checkout -q --detach $selBase
        $selBinding = Join-Path $Sel '.claude/ouro.toml'
        $bad = (Get-Content -LiteralPath $selBinding -Raw).Replace('paths = ["x/", ":(glob)b/**"]', 'paths = [":(bad)b/"]')
        [IO.File]::WriteAllText($selBinding, $bad)
        try {
            Add-Content -LiteralPath (Join-Path $Sel 'z/f.txt') -Encoding utf8 -Value '# changed'
            Invoke-SelGit commit -q -m 'change z/f.txt' -- z/f.txt
            $badDiffOutput = Join-Path $Sel 'selection-bad-diff.txt'
            $r = Invoke-GateStep -ConsumerDir $Sel -Select 'true' -EventName 'pull_request' -BaseSha $selBase -Output $badDiffOutput
            Assert-Equal "selection-mode=fallback`n" ([IO.File]::ReadAllText($badDiffOutput)) 'an unreadable gate diff emits conservative fallback'
            Assert-Selection $r @('A', 'B', 'C', 'D') @() 'a gate whose paths git cannot read'
            Assert-Match 'select-by-paths: every gate runs: the diff against \S+ cannot be read' $r.Text 'a pathspec git refuses: the log names the reason'
        }
        finally { Invoke-SelGit checkout -q -- .claude/ouro.toml }

        # A pull request that commits its own executable git at the root: .NET on Linux and macOS
        # tries a bare program name in the working directory before PATH, so the Action must start
        # the git that PATH resolves. The fake is run once directly, so its silence below counts.
        if ($IsWindows) {
            Write-Host '  skip: a committed executable git at the root is a Linux and macOS row' -ForegroundColor DarkGray
        }
        else {
            Invoke-SelGit checkout -q --detach $selBase
            $fakeGit = Join-Path $Sel 'git'
            Set-Content -LiteralPath $fakeGit -Encoding utf8 -Value @('#!/bin/sh', 'echo FAKE-GIT-RAN', 'exit 0')
            chmod 755 $fakeGit
            if ($LASTEXITCODE -ne 0) { throw "chmod 755 $fakeGit failed (exit $LASTEXITCODE)" }
            Assert-Match 'FAKE-GIT-RAN' ((& $fakeGit) -join "`n") 'the committed fake git prints its marker when run'
            Add-Content -LiteralPath (Join-Path $Sel 'a/f.txt') -Encoding utf8 -Value '# changed'
            Invoke-SelGit add -- git a/f.txt
            Invoke-SelGit commit -q -m 'add git, change a/f.txt'
            $r = Invoke-GateStep -ConsumerDir $Sel -Select 'true' -EventName 'pull_request' -BaseSha $selBase
            Assert-Selection $r @('A', 'C') @('B', 'D') 'a pull request that commits an executable git'
            Assert-NoMatch 'FAKE-GIT-RAN' $r.Text 'a committed git at the root: the Action never runs it'
        }
    }
    finally {
        if (Test-Path -LiteralPath $Sel) { Remove-Item -LiteralPath $Sel -Recurse -Force }
    }
}

# The pinned-interpreter block is docs-freshness's own, line for line: the LD_LIBRARY_PATH half
# is proven there (tests/action-path), and a copy that drifts would run the gates on another libpython.
$pinBlock = { param($Text) [regex]::Match($Text, '(?ms)^\s*\$pinned = .*?^\s*\}\s*$\s*^\s*\}').Value -replace '\s+', ' ' }
$gatesAction = [IO.File]::ReadAllText($Action)
$docsAction = [IO.File]::ReadAllText((Join-Path $Base 'actions/docs-freshness/action.yml'))
Assert-Equal $true ((& $pinBlock $gatesAction) -match 'LD_LIBRARY_PATH') 'the gates action sets LD_LIBRARY_PATH for a pinned python'
Assert-Equal (& $pinBlock $docsAction) (& $pinBlock $gatesAction) 'and its pinned-interpreter block is docs-freshness''s, line for line'

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall action-gates cases pass" -ForegroundColor Green
exit 0
