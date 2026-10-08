<#
.SYNOPSIS
    Fixture suite for Test-CommandBlocks.ps1's extraction, parse half and grep half.
.DESCRIPTION
    Drives the gate against static fixture trees under fixtures/ with -RepoRoot -- no git
    commit, no temp copy, since the gate reads plain files. One case per rule the gate states,
    and each is also this suite's mutation table: removing the rule the case exercises makes
    exactly that case fail.

      rule                                              | case that fails without it
      --------------------------------------------------|------------------------------------
      a block that does not close is a parse finding      | fixtures/hazards' unterminated if
      git remote (bare/-v/--verbose/get-url/show) counts  | fixtures/hazards' eight git remote lines
      bare git remote counts whatever follows but a word  | its quoted, backticked, 2>&1 lines
      a sh fence is extracted like a bash one             | fixtures/hazards' sh block
      any other git remote form is not a finding          | fixtures/notfindings' add, rm, --help
      a rule matches with its case                        | fixtures/notfindings' $gh_repo
      gh repo set-default is a finding                    | fixtures/hazards' set-default line
      GH_REPO is a finding                                | fixtures/hazards' `$GH_REPO` line
      a >60-char placeholder with an apostrophe parses     | fixtures/notfindings' MERGE_ANSWER
      two expansions sharing a line are not one placeholder| fixtures/notfindings' EMAIL= line
      a quoted gh subcommand is not a finding              | fixtures/notfindings' "gh pr ready"
      a toml block is never extracted                      | fixtures/notfindings' GH_REPO toml
      agents/*.md is scanned the same as a SKILL.md         | fixtures/notfindings' agents doc
      no bash on PATH: parse skips, greps still decide exit | the no-bash case below
#>
$ErrorActionPreference = 'Stop'

# Two levels up is the plugin root (gate under bin/) or, once vendored, the scripts dir itself.
$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Script = @((Join-Path $Base 'Test-CommandBlocks.ps1'), (Join-Path $Base 'bin/Test-CommandBlocks.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Script) { throw "Test-CommandBlocks.ps1 not found under $Base" }

$Fixtures = Join-Path $PSScriptRoot 'fixtures'

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ("$Expected" -eq "$Actual") { Write-Host "  ok: $What" -ForegroundColor DarkGray }
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

# Write-Host writes to the information stream (6), not stdout -- 2>&1 captures nothing here, the
# same idiom as the sibling suites.
function Invoke-Gate {
    param([string]$RepoRootArg)
    $out = (& $Script -RepoRoot $RepoRootArg 6>&1 | Out-String)
    [pscustomobject]@{ Out = $out; Code = $LASTEXITCODE }
}

# --- the gate over this repo's own tree: no findings, whatever it currently counts -------------
$real = Invoke-Gate $Base
Assert-Equal 0 $real.Code 'the gate over this plugin''s own skills and agents exits 0'
Assert-Match 'command block\(s\) in \d+ document\(s\): no findings\.' $real.Out 'and reports the block and document count'

# --- fixtures/hazards: one block that fails bash -n, one line per grep rule --------------------
$hazards = Invoke-Gate (Join-Path $Fixtures 'hazards')
Assert-Equal 1 $hazards.Code 'the planted findings exit 1'
# The parse half needs a bash that runs. A runner without one (the Windows CI account has no Git
# Bash on its PATH) skips it, as the gate says, and the parse rows skip with it. Linux always
# carries bash, so there the skip would be a broken detection, not a missing shell: it fails.
$bashRan = $hazards.Out -notmatch 'INFO: no bash that runs is on PATH'
if ($IsLinux) { Assert-Equal $true $bashRan 'on Linux the parse half runs: a bash is always there to run it' }
if ($bashRan) {
    Assert-Match '12 command-block finding\(s\):' $hazards.Out 'exactly the twelve planted findings are reported'
    Assert-Match  'skills/bad/SKILL\.md:13: parse --' $hazards.Out 'the unterminated if is a parse finding at its own line'
}
else {
    Write-Host '  skip: no bash that runs is on PATH here, so the parse rows cannot run (the Linux legs run them)'
    Assert-Match '11 command-block finding\(s\):' $hazards.Out 'without bash, exactly the eleven grep findings are reported'
}
Assert-Match  'skills/bad/SKILL\.md:19: git-remote --' $hazards.Out 'bare `git remote` is a finding'
Assert-Match  'skills/bad/SKILL\.md:20: gh-set-default --' $hazards.Out '`gh repo set-default` is a finding'
Assert-Match  'skills/bad/SKILL\.md:21: gh-repo-env --' $hazards.Out '`GH_REPO` is a finding'
Assert-Match  'skills/bad/SKILL\.md:22: git-remote --' $hazards.Out '`git remote -v` is a finding'
Assert-Match  'skills/bad/SKILL\.md:23: git-remote --' $hazards.Out '`git remote get-url` is a finding'
Assert-Match  'skills/bad/SKILL\.md:24: git-remote --' $hazards.Out '`git remote show` is a finding'
Assert-Match  'skills/bad/SKILL\.md:25: git-remote --' $hazards.Out '`git remote --verbose` is a finding'
Assert-Match  'skills/bad/SKILL\.md:26: git-remote --' $hazards.Out 'a bare `git remote` inside double quotes is a finding'
Assert-Match  'skills/bad/SKILL\.md:27: git-remote --' $hazards.Out 'a bare `git remote` inside backticks is a finding'
Assert-Match  'skills/bad/SKILL\.md:28: git-remote --' $hazards.Out 'a bare `git remote 2>&1` is a finding'
Assert-Match  'skills/bad/SKILL\.md:34: gh-repo-env --' $hazards.Out 'a `GH_REPO` line in an sh fence is a finding'

# --- fixtures/notfindings: acceptance 3's non-findings, plus the write-subcommand control -------
$clean = Invoke-Gate (Join-Path $Fixtures 'notfindings')
Assert-Equal 0 $clean.Code 'none of the planted controls is a finding'
Assert-Match '5 command block\(s\) in 2 document\(s\): no findings\.' $clean.Out `
    'both the SKILL.md and the agents/*.md doc are scanned, with no findings'
foreach ($rule in @('parse --', 'git-remote --', 'gh-set-default --', 'gh-repo-env --')) {
    Assert-NoMatch ([regex]::Escape($rule)) $clean.Out "no `"$rule`" finding fires on any control case"
}

# --- no bash on PATH: the parse half is skipped, the grep half still decides the exit code ------
# The gate runs in this process and, given -RepoRoot, needs nothing on PATH, so PATH becomes one
# empty directory: no runner can have a bash in it, where pwsh's own directory might.
$beforeCount = @(Get-Command bash -All -ErrorAction SilentlyContinue).Count
$origPath = $env:PATH
$noBash = $null
$emptyDir = New-Item -ItemType Directory -Path (Join-Path ([System.IO.Path]::GetTempPath()) ('nobash-' + [guid]::NewGuid()))
try {
    $env:PATH = $emptyDir.FullName
    Assert-Equal 0 @(Get-Command bash -All -ErrorAction SilentlyContinue).Count 'the empty PATH holds no bash'
    $noBash = Invoke-Gate (Join-Path $Fixtures 'hazards')
}
finally {
    $env:PATH = $origPath
    Remove-Item -LiteralPath $emptyDir.FullName -Recurse -Force
}
Assert-Equal 1 $noBash.Code 'the grep findings alone still exit 1'
Assert-Match   'INFO: no bash that runs is on PATH' $noBash.Out 'the parse half reports it is skipped'
Assert-Match   '11 command-block finding\(s\):' $noBash.Out 'the parse finding is gone, the eleven grep findings remain'
Assert-NoMatch 'parse --' $noBash.Out 'the unterminated if is not reported when bash never ran'
Assert-Match   'git-remote --' $noBash.Out 'and the grep half still ran'
Assert-Equal   $beforeCount (@(Get-Command bash -All -ErrorAction SilentlyContinue).Count) `
    'PATH is restored to what it was before the no-bash case'

if ($failures) { Write-Host "`n$failures failure(s)." -ForegroundColor Red; exit 1 }
Write-Host "`nAll command-block tests passed." -ForegroundColor Green
exit 0
