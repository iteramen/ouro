<#
.SYNOPSIS
    Unit test for Test-AgentReadyAnchors.ps1's fixed-size sweep list.
.DESCRIPTION
    The script has no -IssuesJson: every other check it runs (path and fragment anchors) is
    Get-AnchorFindings.ps1's own suite. What is pinned here is the one thing only the sweep
    itself can drop silently -- the `--limit 100` on its `gh issue list` read, which used to
    say nothing when the page came back full, so an issue past it went unchecked with no
    signal in the run's output. A scratch git repo with an `origin` remote lets Get-RepoSlug.ps1
    resolve a repository with no binding and no live gh; a `gh` function shadow stands in for
    the sweep's own read.
#>
$ErrorActionPreference = 'Stop'
# Two levels up is the plugin root (script under bin/) or, once vendored, the scripts dir itself.
$Base   = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Script = @((Join-Path $Base 'Test-AgentReadyAnchors.ps1'), (Join-Path $Base 'bin/Test-AgentReadyAnchors.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Script) { throw "Test-AgentReadyAnchors.ps1 not found under $Base" }

$failures = 0
function Assert-Match($Pattern, $Output, $What) {
    if ($Output -match $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- no match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}
function Assert-NoMatch($Pattern, $Output, $What) {
    if ($Output -notmatch $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- unexpected match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}
function Get-IssuesJson($Count) {
    '[' + ((1..$Count | ForEach-Object { '{"number":' + $_ + ',"title":"i","body":"","labels":[{"name":"agent-ready"}]}' }) -join ',') + ']'
}

# --- a full page of the fixed -limit 100 warns, since an issue past it goes unchecked ----------
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('ouro-anchors-full-' + [guid]::NewGuid().ToString('n'))
New-Item -ItemType Directory -Path $scratch | Out-Null
Push-Location -LiteralPath $scratch
try {
    git init -q . 2>$null
    git remote add origin https://github.com/o/n.git 2>$null
    $global:anchorIssuesJson = Get-IssuesJson 100
    function gh {
        if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
        $global:LASTEXITCODE = 0
        $global:anchorIssuesJson
    }
    $full = (& $Script 6>&1 | Out-String)
    Assert-Match 'warning::anchor gate: the agent-ready list filled its --limit 100 page' $full 'a full 100-row page warns naming the limit'

    $global:anchorIssuesJson = Get-IssuesJson 99
    $under = (& $Script 6>&1 | Out-String)
    Assert-NoMatch 'warning::anchor gate: the agent-ready list filled' $under 'a page under the limit warns nothing'
    Remove-Item Function:\gh
} finally { Pop-Location; Remove-Item -LiteralPath $scratch -Recurse -Force; Remove-Variable -Name anchorIssuesJson -Scope Global -ErrorAction Ignore }

# --- -Ref reads the issue body's anchors and a -Doc file from the commit, not the working tree --
$refScratch = Join-Path ([IO.Path]::GetTempPath()) ('ouro-anchors-ref-' + [guid]::NewGuid().ToString('n'))
New-Item -ItemType Directory -Path (Join-Path $refScratch 'docs') | Out-Null
Push-Location -LiteralPath $refScratch
try {
    git init -q . 2>$null
    git remote add origin https://github.com/o/n.git 2>$null
    'refOnlyFrag = 1' | Set-Content -LiteralPath 'a.ps1'
    '- `./a.ps1` - "refOnlyFrag = 1"' | Set-Content -LiteralPath 'docs/d.md'
    git add . 2>$null
    git -c user.name=t -c user.email=t@t -c commit.gpgsign=false commit -q --no-verify -m fixture 2>$null
    'workOnlyFrag = 2' | Set-Content -LiteralPath 'a.ps1'
    '- `./a.ps1` - "workOnlyFrag = 2"' | Set-Content -LiteralPath 'docs/d.md'
    'x' | Set-Content -LiteralPath 'docs/new.md'
    git add . 2>$null
    $global:anchorIssuesJson = '[{"number":7,"title":"i","body":"- `./a.ps1` - \"refOnlyFrag = 1\"","labels":[{"name":"agent-ready"}]}]'
    function gh {
        if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
        $global:LASTEXITCODE = 0
        $global:anchorIssuesJson
    }
    $tree = (& $Script 6>&1 | Out-String)
    Assert-Match 'STALE-ANCHOR: fragment no longer greps in ''a.ps1'': "refOnlyFrag' $tree 'no -Ref: the issue anchor, absent from the working tree, is stale'
    $ref = (& $Script -Ref HEAD 6>&1 | Out-String)
    Assert-Match '#7: OK' $ref '-Ref: the issue anchor present at the commit verifies though the working tree lost it'
    $doc = (& $Script -Ref HEAD -Doc docs/d.md 6>&1 | Out-String)
    Assert-Match 'docs/d.md: OK' $doc '-Ref: a -Doc file is read at the commit and its fragment verifies there'
    $docNoRef = (& $Script -Doc docs/d.md 6>&1 | Out-String)
    Assert-Match 'docs/d.md: OK' $docNoRef 'no -Ref: the working-tree doc is read, and its working-tree fragment verifies in the working tree'
    '- `./a.ps1` - "workOnlyFrag = 2"' | Set-Content -LiteralPath 'docs/br[1].md'
    $docBracket = (& $Script -Doc 'docs/br[1].md' 6>&1 | Out-String)
    Assert-Match ([regex]::Escape('docs/br[1].md: OK')) $docBracket 'no -Ref: a -Doc path holding a bracket is read as spelled, not as a pattern'
    $missing = (& $Script -Ref HEAD -Doc docs/new.md 6>&1 | Out-String)
    Assert-Match 'doc not found: docs/new.md' $missing '-Ref: a -Doc file absent at the commit is not found though the working tree holds it'
    $global:anchorIssuesJson = '[]'
    $badRef = try { (& $Script -Ref no-such-ref 6>&1 | Out-String) } catch { "threw: $_" }
    Assert-Match '^threw: .*no-such-ref' $badRef '-Ref: an unresolvable ref throws with an empty queue too, not a clean report'
    Remove-Item Function:\gh
} finally { Pop-Location; Remove-Item -LiteralPath $refScratch -Recurse -Force; Remove-Variable -Name anchorIssuesJson -Scope Global -ErrorAction Ignore }

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall agent-ready-anchors cases pass" -ForegroundColor Green
