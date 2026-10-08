<#
.SYNOPSIS
    Test that the docs-freshness Action's relative path still reaches the gate.
.DESCRIPTION
    The defect this pins: actions/docs-freshness/action.yml runs the gate through a path
    relative to its own directory, and nothing else reads that path. Move or rename
    bin/Test-DocsFreshness.ps1 and the action file stays green in every other gate, then
    fails on the first consumer run.

    So the relative path is read OUT OF the action file, never restated here -- a second copy
    would have to be edited by hand and would prove only that it was. ouro-binding.py must
    resolve beside the resolved gate, because the gate finds it by its own directory and
    reads the consumer's [docs] table through it.

    A vendored tree (bin/Vendor-Ouro.ps1) copies the scripts flat and nothing under actions/,
    so there the suite prints a skip line and exits 0.

    The gate step's own PATH lines are read out of the action file and run here, once with a
    pinned interpreter and once without, because that is the pair a consumer chooses between
    through the setup-python input and neither is exercised by any other gate: unpinned, the
    step must leave PATH as the runner has it rather than end at Split-Path, which refuses an
    empty path under the error preference a pwsh step runs with.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Action = Join-Path $Base 'actions/docs-freshness/action.yml'
if (-not (Test-Path -LiteralPath $Action)) {
    if (Test-Path -LiteralPath (Join-Path $Base 'Test-DocsFreshness.ps1')) {
        Write-Host "skip: no actions/ beside the flat gate scripts (vendored layout); the Action ships only with the plugin" -ForegroundColor DarkGray
        exit 0
    }
    throw "actions/docs-freshness/action.yml not found under $Base"
}

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -eq $Actual) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}

# Comment lines are dropped first: an old path left in a comment must not stand in for a broken one
# in the step that runs.
$actionText = (Get-Content -LiteralPath $Action) -notmatch '^\s*#' -join "`n"
$refs = @([regex]::Matches($actionText, '\$\{\{\s*github\.action_path\s*\}\}([^''"\s]+)') |
    ForEach-Object { $_.Groups[1].Value })
Assert-Equal 1 $refs.Count 'the action file runs exactly one path through github.action_path'
if ($refs.Count -eq 1) {
    $gate = Join-Path (Split-Path $Action -Parent) $refs[0]
    Assert-Equal 'Test-DocsFreshness.ps1' (Split-Path $gate -Leaf) 'the github.action_path reference names the docs-freshness gate'
    Assert-Equal $true (Test-Path -LiteralPath $gate -PathType Leaf) "the gate resolves from the action directory ($($refs[0]))"
    Assert-Equal $true (Test-Path -LiteralPath (Join-Path (Split-Path $gate -Parent) 'ouro-binding.py') -PathType Leaf) `
        'ouro-binding.py resolves beside the gate the action file points at'
}

# The input that decides whether the interpreter is pinned, and the guard that lets the gate step
# survive its absence. Read out of the file, never restated: a second copy would prove only itself.
# The condition is matched inside the step it guards and counted, because on the gate step
# instead it would skip the gate and leave a consumer's workflow green having run nothing.
Assert-Equal $true ($actionText -match "(?m)^\s*setup-python:\s*$") 'the action declares a setup-python input'
Assert-Equal $true ($actionText -match "(?m)^\s*default:\s*'true'\s*$") 'which defaults to true, so an unchanged consumer keeps the pin'
Assert-Equal $true ($actionText -match "(?ms)^\s*- uses: actions/setup-python@[^\n]*\n(?:[ \t]+(?!- )[^\n]*\n)*?[ \t]+if: inputs\.setup-python == 'true'\s*$") `
    'and that step, no other, runs only under it'
Assert-Equal 1 ([regex]::Matches($actionText, '(?m)^\s*if:\s').Count) 'which is the only condition in the file'

# The step's PATH lines, with the placeholder filled both ways, run in a child pwsh under the
# error preference the runner appends. The gate invocation itself is dropped: what is under test
# is whether PATH survives an unpinned run, not the gate, which every other suite runs.
# Bounded by the block's own indent, not by a line starting at column 0, which nothing in an
# action file does: unbounded, a second run: block anywhere below would be swept into this one.
$runBlocks = @([regex]::Matches($actionText, '(?m)^([ \t]+)run: \|\r?\n((?:\1[ \t]+.*(?:\r?\n|$))+)') |
    ForEach-Object { $_.Groups[2].Value })
Assert-Equal 1 $runBlocks.Count 'the action file runs exactly one script block'
$pathLines = @(($runBlocks | Select-Object -First 1) -split "`n" | Where-Object { $_ -notmatch 'Test-DocsFreshness\.ps1' })
Assert-Equal $true ($pathLines -join "`n" -match 'steps\.python\.outputs\.python-path') `
    'the gate step reads the pinned interpreter from the setup step'

# The shell the probes run in, and the path the pinned case stands an interpreter up with. Both
# are this pwsh, resolved before the probe is built: a member of $null is a terminating error
# under the strict mode this suite sets, and every row below needs a shell to run the lines in.
$pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
$real = if ($pwshCmd) { $pwshCmd.Source } else { $null }

$probe = {
    param($Lines, $Pinned)
    $script = ($Lines -join "`n").Replace('${{ steps.python.outputs.python-path }}', $Pinned)
    $full = "`$ErrorActionPreference = 'Stop'`n`$before = `$env:PATH`n" + $script +
            "`n'changed=' + (`$env:PATH -ne `$before)`n'leads=' + `$env:PATH.StartsWith([IO.Path]::PathSeparator)" +
            "`n'ld=' + `$env:LD_LIBRARY_PATH"
    $out = & $real -NoProfile -Command $full 2>&1
    [pscustomobject]@{ Code = $LASTEXITCODE; Text = ($out | ForEach-Object { "$_" }) -join "`n" }
}

# The tool cache's python links its own libpython: pinned on Linux, the step puts <root>/lib at
# the head of LD_LIBRARY_PATH, which update-environment would have set; elsewhere it is untouched.
# Unset here, a pinned run on Linux loaded the host's libpython of another patch and segfaulted.
if ($real) {
    $savedLd = $env:LD_LIBRARY_PATH
    try {
        $env:LD_LIBRARY_PATH = ''
        $fake = [IO.Path]::Combine([IO.Path]::GetTempPath(), 'ouro-fake-py', 'x64', 'bin', 'python3')
        $fakeLib = [IO.Path]::Combine([IO.Path]::GetTempPath(), 'ouro-fake-py', 'x64', 'lib')
        $ldRun = & $probe $pathLines $fake
        Assert-Equal 0 $ldRun.Code 'a pinned run with an interpreter path the step only reads exits 0'
        if ($IsLinux) {
            Assert-Equal $true ($ldRun.Text -match ('(?m)^ld=' + [regex]::Escape($fakeLib) + '$')) `
                'on Linux a pinned run puts the interpreter''s lib on LD_LIBRARY_PATH'
        }
        else {
            Assert-Equal $true ($ldRun.Text -match '(?m)^ld=$') 'elsewhere a pinned run leaves LD_LIBRARY_PATH as it was'
        }
    }
    finally { $env:LD_LIBRARY_PATH = $savedLd }
}

if ($real) {
    $unpinned = & $probe $pathLines ''
    Assert-Equal 0 $unpinned.Code 'with no pinned interpreter the step does not end at the prepend'
    Assert-Equal $true ($unpinned.Text -match 'changed=False') 'and leaves PATH as the runner has it'
    Assert-Equal $true ($unpinned.Text -match 'leads=False') 'with no separator left at its head'

    $pinnedRun = & $probe $pathLines $real
    Assert-Equal 0 $pinnedRun.Code 'with a pinned interpreter the step runs'
    Assert-Equal $true ($pinnedRun.Text -match 'changed=True') 'and puts its directory on PATH'
}
else { Write-Host '  skip: no pwsh on PATH to run the step lines in here' -ForegroundColor DarkGray }

# The Windows guard: setup-python's Windows installer already symlinks python3.exe beside
# python.exe, so this fires only where that symlink could not be made. A dummy python.exe is
# enough, since Get-Command resolves by name.
if ($IsWindows -and $real) {
    $pinProbe = {
        param($Lines, $Pinned)
        $script = ($Lines -join "`n").Replace('${{ steps.python.outputs.python-path }}', $Pinned)
        $full = "`$ErrorActionPreference = 'Stop'`n" + $script +
                "`n(Get-Command python3 -ErrorAction SilentlyContinue).Source"
        $out = & $real -NoProfile -Command $full 2>&1
        [pscustomobject]@{ Code = $LASTEXITCODE; Text = (($out | ForEach-Object { "$_" }) -join "`n").Trim() }
    }

    $noPy3Dir = Join-Path ([IO.Path]::GetTempPath()) ('ouro-pin-guard-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $noPy3Dir | Out-Null
    try {
        $pyExe = Join-Path $noPy3Dir 'python.exe'
        $py3Exe = Join-Path $noPy3Dir 'python3.exe'
        Set-Content -LiteralPath $pyExe -Value 'stand-in' -Encoding ascii
        $r4 = & $pinProbe $pathLines $pyExe
        Assert-Equal 0 $r4.Code 'the pin block runs against a scratch python.exe with no python3.exe'
        Assert-Equal $true (Test-Path -LiteralPath $py3Exe -PathType Leaf) 'python3.exe now exists beside it'
        Assert-Equal $true ($r4.Text -and $r4.Text.TrimEnd('\', '/') -ieq $py3Exe.TrimEnd('\', '/')) `
            '(Get-Command python3).Source is inside that directory'
    }
    finally { Remove-Item -LiteralPath $noPy3Dir -Recurse -Force }

    $hasPy3Dir = Join-Path ([IO.Path]::GetTempPath()) ('ouro-pin-guard-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $hasPy3Dir | Out-Null
    try {
        $pyExe2 = Join-Path $hasPy3Dir 'python.exe'
        $py3Exe2 = Join-Path $hasPy3Dir 'python3.exe'
        Set-Content -LiteralPath $pyExe2 -Value 'stand-in' -Encoding ascii
        Set-Content -LiteralPath $py3Exe2 -Value 'already here' -Encoding ascii
        $r5 = & $pinProbe $pathLines $pyExe2
        Assert-Equal 0 $r5.Code 'the pin block runs against a scratch python.exe with python3.exe already present'
        # Content, not mtime: Copy-Item carries the source file's own write time to the
        # destination, so an overwrite here would not show up as a changed mtime.
        Assert-Equal 'already here' ((Get-Content -LiteralPath $py3Exe2 -Raw).Trim()) `
            'an existing python3.exe is not copied over (its content is unchanged)'
    }
    finally { Remove-Item -LiteralPath $hasPy3Dir -Recurse -Force }
}
else { Write-Host '  skip: the Windows pin guard runs on Windows only' -ForegroundColor DarkGray }

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall action-path cases pass" -ForegroundColor Green
exit 0
