<#
.SYNOPSIS
    Test of Test-LandState.ps1 over scratch repositories and the gh stand-in.
.DESCRIPTION
    The script is dot-sourced and its function called per row; the fixture beside this file holds
    the stand-in (which answers `gh auth status`) and builds each repository.

    Pinned: on a feature branch with a clean tree the run prints the branch, the default branch,
    the sync and merge answers (the defaults, named as defaults, where the binding declares none;
    a landing preset's values where it does), the review answer, the authority and overlay lists,
    the gh login, and the commits to land oldest first, against the local default branch or
    origin's where there is no local one. Refused, each shown refused: the default branch, a
    protected prefix on the branch, a `worktree-*` branch, a tree with staged or unstaged changes
    to tracked files, a gh that is not logged in, a detached HEAD, a tree with no binding, and a
    branch with nothing to land. An untracked file is listed and does not stop the run. The child
    rows pin the entry point.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'LandScripts.Fixture.ps1')
$Script = Find-Script 'Test-LandState.ps1'
. $Script

$LoggedIn = @{ 'auth status' = @{ Exit = 0; Out = 'Logged in to github.com' } }

Write-Host 'the happy path: a feature branch with one commit, a binding with no answers'
$w = New-World 'w1'; $m = $w.Main
$null = G $m checkout -q -b feat/x master
$c1 = New-Commit $m 'x1.txt'
Push-Location -LiteralPath $m
try {
    Set-Gh $LoggedIn
    $r = Invoke-Step { Invoke-TestLandState }
    Assert-Equal '' $r.Stop 'the run ends without a stop'
    Assert-Equal 'auth status' ($r.Gh -join ';') 'gh is asked whether it is logged in, and nothing else'
    Assert-Equal "branch: feat/x,default: master,ship.sync: not declared - step 2 merges, the default,ship.merge: not declared - step 10 squashes, the default,ship.review: none,authority.local: none,overlays.land: none,gh: logged in,commits to land (1, against refs/heads/master):,  $($c1.Substring(0, 7)) change x1.txt" ($r.Lines -join ',') 'every line, in order'

    Write-Host 'an untracked file is listed, not a stop; a change to a tracked file stops'
    [System.IO.File]::WriteAllText((Join-Path $m 'notes.md'), "n`n", $Utf8)
    $r = Invoke-Step { Invoke-TestLandState }
    Assert-Equal '' $r.Stop 'no stop'
    Assert-Line $r.Lines 'untracked: notes.md - rides no push; add it to the branch or leave it' 'the untracked file is named'
    [System.IO.File]::Delete((Join-Path $m 'notes.md'))
    [System.IO.File]::WriteAllText((Join-Path $m 'x1.txt'), "changed`n", $Utf8)
    $r = Invoke-Step { Invoke-TestLandState }
    Assert-Match "^the working tree is not clean - commit or stash first:`n   M x1.txt$" $r.Stop 'an unstaged change stops, naming the file'
    $null = G $m add x1.txt
    $r = Invoke-Step { Invoke-TestLandState }
    Assert-Match "^the working tree is not clean - commit or stash first:`n  M  x1.txt$" $r.Stop 'a staged change stops too'
    $null = G $m checkout -q -- x1.txt
    $null = G $m reset -q --hard HEAD

    Write-Host 'gh not logged in stops; nothing to land stops'
    Set-Gh @{ 'auth status' = @{ Exit = 1; Out = 'You are not logged into any GitHub hosts' } }
    $r = Invoke-Step { Invoke-TestLandState }
    Assert-Match '^gh is not logged in \(gh auth status exit 1\): You are not logged into any GitHub hosts' $r.Stop 'the stop carries gh''s answer'
    Set-Gh $LoggedIn
    $null = G $m checkout -q -b feat/empty master
    $r = Invoke-Step { Invoke-TestLandState }
    Assert-Equal 'nothing to land: feat/empty has no commit beyond refs/heads/master' $r.Stop 'a branch at the default branch has nothing to land'

    Write-Host 'the default branch, a protected prefix, a worktree-* name and a detached HEAD each stop'
    $null = G $m checkout -q master
    $r = Invoke-Step { Invoke-TestLandState }
    Assert-Equal 'the checkout is on master, the default branch - land from the feature branch' $r.Stop 'the default branch stops'
    Assert-Equal 0 $r.Gh.Count 'before any gh call'
    $null = G $m checkout -q -b release/1 feat/x
    $r = Invoke-Step { Invoke-TestLandState }
    Assert-Equal "release/1 has the protected prefix release/ - the repository's own release procedure owns it, never this landing" $r.Stop 'a protected prefix stops, naming the prefix'
    $null = G $m checkout -q -b worktree-abc feat/x
    $r = Invoke-Step { Invoke-TestLandState }
    Assert-Match '^worktree-abc is the name a worktree made without -b sits on - rename it before pushing \(git branch -m worktree-abc <feature/name>\)' $r.Stop 'a worktree-* branch stops'
    $null = G $m checkout -q --detach feat/x
    $r = Invoke-Step { Invoke-TestLandState }
    Assert-Equal 'HEAD is detached - check out the feature branch first' $r.Stop 'a detached HEAD stops'
    $null = G $m checkout -q feat/x

    Write-Host 'with no local default branch the commits count against origin''s'
    $null = G $m branch -D master
    $r = Invoke-Step { Invoke-TestLandState }
    Assert-Equal '' $r.Stop 'no stop'
    Assert-Line $r.Lines 'commits to land (1, against refs/remotes/origin/master):' 'the base is origin''s default branch'
}
finally { Pop-Location }

Write-Host 'a binding with a landing preset, authority documents and overlays prints them'
$w2 = New-World 'w2' -Landing 'rebase-merge'
$binding = Join-Path $w2.Main '.claude/ouro.toml'
[System.IO.File]::AppendAllText($binding, "`n[authority]`nlocal = [`"CLAUDE.md`", `"docs/contract.md`"]`n[overlays]`nland = [`".claude/ouro.toml`"]`n", $Utf8)
$null = G $w2.Main commit -q -am 'authority and overlays'
$null = G $w2.Main push -q origin master
$null = G $w2.Main checkout -q -b feat/y master
$null = New-Commit $w2.Main 'y.txt'
Push-Location -LiteralPath $w2.Main
try {
    Set-Gh $LoggedIn
    $r = Invoke-Step { Invoke-TestLandState }
    Assert-Equal '' $r.Stop 'no stop'
    Assert-Line $r.Lines 'ship.sync: rebase' 'the preset answers the sync'
    Assert-Line $r.Lines 'ship.merge: merge' 'and the merge'
    Assert-Line $r.Lines 'authority.local: CLAUDE.md, docs/contract.md' 'the documents to read are listed'
    Assert-Line $r.Lines 'overlays.land: .claude/ouro.toml' 'and the overlays'
}
finally { Pop-Location }

Write-Host 'a tree with no binding stops before any call'
$bare = (New-Item -ItemType Directory -Path (Join-Path $Scratch 'unbound')).FullName
$null = git init -q $bare 2>&1
Push-Location -LiteralPath $bare
try {
    Set-Gh $LoggedIn
    $r = Invoke-Step { Invoke-TestLandState }
    Assert-Match '^ouro-binding.py check failed \(exit 1\): .*no binding' $r.Stop 'the binding check stops the run'
    Assert-Equal 0 $r.Gh.Count 'and no gh call is made'
}
finally { Pop-Location }

Write-Host 'the entry point: a child process, the exit codes and the STOP line'
Set-Gh $LoggedIn
$c = Invoke-Child $Script @() $m
Assert-Equal 0 $c.Code 'the happy path exits 0'
Assert-Line $c.Lines 'branch: feat/x' 'and prints the state'
Assert-Line $c.Lines "  $($c1.Substring(0, 7)) change x1.txt" 'with the commits to land'
$c = Invoke-Child $Script @() $w2.Origin
Assert-Equal 1 $c.Code 'a bare repository, with no binding, exits 1'
Assert-Line $c.Lines 'STOP: ouro-binding.py check failed' 'with the STOP line' -Prefix
$help = Get-Help ([WildcardPattern]::Escape($Script))
Assert-Match 'Check that the branch in the working directory' ($help.Synopsis) 'the script exposes its help'
Complete-Suite 'Test-LandState'
