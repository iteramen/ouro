<#
.SYNOPSIS
    End-to-end test that the docs gate reads its configuration from the binding.
.DESCRIPTION
    The defect this pins: the [docs] table validated and was then discarded, so the same
    intended configuration behaved differently depending on how it arrived. Measured before
    the fix -- `report_only = ["S9"]` in the binding let a dangling path exit 0, while
    `-ReportOnlySignals S9` on the command line exited 1.

    So the assertions are about EXIT CODES from real runs, not about internal state: the
    gate is invoked as a subprocess against a scratch repo, because the wiring runs after
    the -AsModule guard and is invisible to a dot-source.

    Invocation uses the call operator with a real array, never `pwsh -File`: -File does not
    bind a multi-value array parameter, which is a live trap for anyone configuring the gate
    through a [[gate]].run string instead of the binding. One case pins that difference.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Gate = @((Join-Path $Base 'Test-DocsFreshness.ps1'), (Join-Path $Base 'bin/Test-DocsFreshness.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Gate) { throw "Test-DocsFreshness.ps1 not found under $Base" }
if (-not (Test-Path -LiteralPath (Join-Path (Split-Path $Gate -Parent) 'ouro-binding.py'))) {
    Write-Host "skip: no ouro-binding.py beside the gate (vendored layout); the binding wiring cannot run" -ForegroundColor DarkGray
    exit 0
}

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -eq $Actual) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}

$BASE_TOML = @'
schema = 1
[repo]
slug = "o/n"
default_branch = "master"
[[gate]]
areas = ["*"]
run = "x"
[ship]
policy = "stop-at-pr"
review = "adversarial-review"
[owner]
ruling_approvers = ["me"]
'@

$root = Join-Path ([System.IO.Path]::GetTempPath()) ("docsbind-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
function Reset-Fixture {
    param([string]$DocsToml = '', [switch]$NoBinding)
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
    foreach ($d in '.claude', 'docs', 'src', 'gen', 'vend') {
        New-Item -ItemType Directory -Force -Path (Join-Path $root $d) | Out-Null
    }
    Push-Location -LiteralPath $root
    try {
        git init -q . 2>$null; git config user.email t@t; git config user.name t
        $bt = [char]96
        # One dangling backticked path (S3), one work-remaining heading (S10), one doc under a
        # tree that an index could cover (S4b), one generated doc, one vendored doc, and an
        # index that lists a file which does not exist (S4a).
        Set-Content -LiteralPath (Join-Path $root 'docs/probe.md') `
            -Value "# Probe`n`nSee ${bt}docs/does-not-exist-anywhere.md${bt} here.`n" -Encoding utf8
        Set-Content -LiteralPath (Join-Path $root 'docs/plan.md') `
            -Value "# Plan`n`n## Open questions`n`nsomething`n" -Encoding utf8
        Set-Content -LiteralPath (Join-Path $root 'src/module.md') -Value "# Module`n" -Encoding utf8
        Set-Content -LiteralPath (Join-Path $root 'gen/api.md')    -Value "# Generated`n" -Encoding utf8
        Set-Content -LiteralPath (Join-Path $root 'vend/dep.md')   -Value "# Vendored`n" -Encoding utf8
        Set-Content -LiteralPath (Join-Path $root 'docs/index.md') `
            -Value "# Index`n`n- ${bt}src/module.md${bt}`n- ${bt}src/deleted.md${bt}`n" -Encoding utf8
        if (-not $NoBinding) {
            Set-Content -LiteralPath (Join-Path $root '.claude/ouro.toml') -Value ($BASE_TOML + $DocsToml) -Encoding utf8
        }
        git add -A 2>$null; git commit -q -m fixture 2>$null
    }
    finally { Pop-Location }
}
function Invoke-Gate {
    param([hashtable]$Params = @{})
    Push-Location -LiteralPath $root
    try { & $Gate @Params *> $null; return $LASTEXITCODE }
    finally { Pop-Location }
}
function Gate-Output {
    param([hashtable]$Params = @{})
    Push-Location -LiteralPath $root
    try { return (& $Gate @Params *>&1 | Out-String) }
    catch { return "$_" }
    finally { Pop-Location }
}

try {
    # ── the defaults, and the no-binding case ──────────────────────────────────────────────
    Reset-Fixture
    Assert-Equal 0 (Invoke-Gate) 'no [docs] table: shipped defaults apply and nothing blocks'

    Reset-Fixture -NoBinding
    $o = Gate-Output
    Assert-Equal 0 (Invoke-Gate) 'a repo with NO binding at all still runs and does not block'
    Assert-Equal $true ($o -match 'INFO - \[docs\] not read') 'and it says why, rather than looking configured'

    # ── report_only: the key the whole issue turns on ──────────────────────────────────────
    Reset-Fixture "`n[docs]`nreport_only = [`"S9`"]`n"
    Assert-Equal 1 (Invoke-Gate) 'report_only from the binding makes S3 block'
    Reset-Fixture
    Assert-Equal 1 (Invoke-Gate @{ ReportOnlySignals = @('S9') }) 'the same value as a parameter blocks identically'
    Reset-Fixture "`n[docs]`nreport_only = [`"S9`"]`n"
    Assert-Equal 0 (Invoke-Gate @{ ReportOnlySignals = @('S9', 'S3', 'S10') }) 'an explicit parameter overrides the binding'

    # ── planning_paths: observable only while S10 would otherwise block ────────────────────
    Reset-Fixture "`n[docs]`nreport_only = [`"S9`", `"S3`"]`n"
    Assert-Equal 1 (Invoke-Gate) 'S10 blocks when report_only omits it (the control for the next case)'
    Reset-Fixture "`n[docs]`nreport_only = [`"S9`", `"S3`"]`nplanning_paths = ['^docs/plan\.md$']`n"
    Assert-Equal 0 (Invoke-Gate) 'planning_paths from the binding exempts the doc and S10 stops blocking'

    # ── index_path / indexed_trees / index_exempt: S4a and S4b both arm from the binding ────
    Reset-Fixture "`n[docs]`nreport_only = [`"S9`", `"S3`", `"S10`"]`nindex_path = `"docs/index.md`"`nindexed_trees = [`"src/`"]`n"
    $o = Gate-Output
    Assert-Equal $true ($o -match 'S4a') 'index_path and indexed_trees from the binding arm S4a (the index lists a missing file)'
    Reset-Fixture "`n[docs]`nreport_only = [`"S9`", `"S3`", `"S10`"]`nindex_path = `"docs/index.md`"`nindexed_trees = [`"gen/`"]`n"
    $o = Gate-Output
    Assert-Equal $true ($o -match 'S4b') 'indexed_trees from the binding arms S4b (gen/api.md is unindexed)'
    Reset-Fixture "`n[docs]`nreport_only = [`"S9`", `"S3`", `"S10`"]`nindex_path = `"docs/index.md`"`nindexed_trees = [`"gen/`"]`nindex_exempt = ['^gen/']`n"
    $o = Gate-Output
    Assert-Equal $false ($o -match 'S4b') 'index_exempt from the binding suppresses that S4b'

    # ── exclude / generated_pattern: both remove a doc from scope ──────────────────────────
    Reset-Fixture "`n[docs]`nreport_only = [`"S9`", `"S3`", `"S10`"]`n"
    $withVend = Gate-Output
    Reset-Fixture "`n[docs]`nreport_only = [`"S9`", `"S3`", `"S10`"]`nexclude = [`"/vend/`"]`n"
    $noVend = Gate-Output
    Assert-Equal $true (($withVend -match 'across 6 docs') -and ($noVend -match 'across 5 docs')) `
        'exclude from the binding drops a doc from scope (6 -> 5)'
    Reset-Fixture "`n[docs]`nreport_only = [`"S9`", `"S3`", `"S10`"]`ngenerated_pattern = '^gen/'`n"
    Assert-Equal $true ((Gate-Output) -match 'across 5 docs') 'generated_pattern from the binding drops a doc from scope'

    # ── extensions: what counts as a path-shaped token for S3 ──────────────────────────────
    Reset-Fixture "`n[docs]`nreport_only = [`"S9`", `"S10`"]`nextensions = [`".rs`"]`n"
    Assert-Equal 0 (Invoke-Gate) 'extensions from the binding: .md is no longer path-shaped, so S3 finds nothing'

    # ── suppress_prefixes: the S3 finding is suppressed by prefix ──────────────────────────
    # Two S3 candidates in the fixture (the probe doc and the index's deleted entry), so assert
    # on the signal itself rather than the exit code, which either one would keep at 1.
    Reset-Fixture "`n[docs]`nreport_only = [`"S9`", `"S10`"]`n"
    Assert-Equal $true ((Gate-Output) -match 'S3') 'the control: S3 fires without suppress_prefixes'
    Reset-Fixture "`n[docs]`nreport_only = [`"S9`", `"S10`"]`nsuppress_prefixes = [`"docs/does-not-exist`", `"src/deleted`"]`n"
    Assert-Equal $false ((Gate-Output) -match 'S3') 'suppress_prefixes from the binding suppresses every S3 finding'

    # ── html_globs: an HTML page comes into scope ──────────────────────────────────────────
    Reset-Fixture "`n[docs]`nreport_only = [`"S9`", `"S3`", `"S10`"]`nhtml_globs = [`"docs/*.html`"]`n"
    Push-Location -LiteralPath $root
    Set-Content -LiteralPath (Join-Path $root 'docs/page.html') -Value '<a href="nope.md">x</a>' -Encoding utf8
    git add -A 2>$null; git commit -q -m html 2>$null
    Pop-Location
    Assert-Equal $true ((Gate-Output) -match 'across 7 docs') 'html_globs from the binding brings the page into scope'

    # ── banned: the free-key table blocks ──────────────────────────────────────────────────
    Reset-Fixture "`n[docs.banned]`n`"does-not-exist-anywhere`" = `"gone`"`n"
    Assert-Equal 1 (Invoke-Gate) 'the banned free-key table is read from the binding and S8 blocks'

    # ── fail loud: a declared value the validator accepts but the gate cannot use ──────────
    Reset-Fixture "`n[docs]`nindex_path = `"docs/index.md`"`nindexed_trees = `"not-a-list`"`n"
    $o = Gate-Output
    Assert-Equal $true (($o -match 'declared but unusable') -or ($o -match 'failed \(exit')) `
        'a declared-but-unusable value fails loud rather than falling back to the default'

    # ── fail loud AND say why: ouro-binding.py's reason is on stderr ───────────────────────
    # A binding that is not valid TOML makes `ouro-binding.py get` exit 1 with
    # `error: <path>: <reason>` on stderr. The value is stdout-only; the error must not be, or
    # it ends in an empty colon -- the same way the Python 3.11+ floor message would vanish.
    Reset-Fixture "`n[docs`n"
    $o = Gate-Output
    Assert-Equal $true (($o -match 'failed \(exit 1\): ') -and ($o -match 'error: .*ouro\.toml')) `
        "the gate's error carries ouro-binding.py's stderr, not just 'failed (exit 1):'"
}
finally {
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
}

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall docs-binding cases pass" -ForegroundColor Green
exit 0
