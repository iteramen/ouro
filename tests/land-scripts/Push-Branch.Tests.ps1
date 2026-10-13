<#
.SYNOPSIS
    Test of Push-Branch.ps1 over scratch repositories.
.DESCRIPTION
    The script is dot-sourced and its function called per row; the fixture beside this file builds
    each repository, a main checkout with a bare origin. No gh call is made.

    Pinned: a plain push lands the branch on origin at the local HEAD with the upstream set, and a
    second push with nothing new is no failure; a non-fast-forward is refused with origin
    unchanged and the stop says to fetch; a leased push after a local rewrite lands when origin
    still holds the lease, is refused with origin unchanged when origin moved, and a lease this
    clone does not have stops before any push; `-Lease none` is the plain push; the default branch
    and a protected prefix stop. The child rows pin the entry point.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'LandScripts.Fixture.ps1')
$Script = Find-Script 'Push-Branch.ps1'
. $Script

function Get-OriginTip([string]$Origin, [string]$Branch) {
    $line = @(G $Origin for-each-ref '--format=%(objectname)' "refs/heads/$Branch")
    if ($line.Count) { $line[0] } else { '' }
}

Write-Host 'a plain push lands the branch with its upstream; nothing new is no failure'
$w = New-World 'w1'; $m = $w.Main
$null = G $m checkout -q -b feat/x master
$x1 = New-Commit $m 'x1.txt'
Push-Location -LiteralPath $m
try {
    Set-Gh @{}
    $r = Invoke-Step { Invoke-PushBranch }
    Assert-Equal '' $r.Stop 'the run ends without a stop'
    Assert-Equal "pushed feat/x to origin at $x1" ($r.Lines -join ';') 'the push is reported with the head'
    Assert-Equal $x1 (Get-OriginTip $w.Origin 'feat/x') 'origin has the branch at the local head'
    Assert-Equal 'origin/feat/x' (@(G $m rev-parse --abbrev-ref 'feat/x@{upstream}')[0]) 'the upstream is set'
    Assert-Equal 0 $r.Gh.Count 'no gh call is made'
    $r = Invoke-Step { Invoke-PushBranch }
    Assert-Equal '' $r.Stop 'a push with nothing new is no failure'

    Write-Host 'a non-fast-forward is refused, origin unchanged'
    $null = G $m checkout -q master
    $base = New-Commit $m 'base.txt'
    $null = G $m push -q origin master
    $null = G $w.Origin update-ref refs/heads/feat/x $base
    $null = G $m checkout -q feat/x
    $r = Invoke-Step { Invoke-PushBranch }
    Assert-Match "^the push was refused: .*(non-fast-forward|fetch first)" $r.Stop 'the stop carries git''s refusal'
    Assert-Match 'the plain force flag is never the push$' $r.Stop 'and rules out the plain force'
    Assert-Equal $base (Get-OriginTip $w.Origin 'feat/x') 'origin is unchanged'

    Write-Host 'a leased push lands a rewrite while origin holds the lease, and is refused once origin moved'
    $null = G $w.Origin update-ref refs/heads/feat/x $x1
    $null = G $m fetch -q origin feat/x
    $null = G $m commit -q --amend -m 'rewritten x1'
    $x1b = @(G $m rev-parse HEAD)[0]
    $r = Invoke-Step { Invoke-PushBranch -Lease $x1 }
    Assert-Equal '' $r.Stop 'the leased push goes through'
    Assert-Equal "pushed feat/x to origin at $x1b, with the lease $x1" ($r.Lines -join ';') 'and is reported with the lease'
    Assert-Equal $x1b (Get-OriginTip $w.Origin 'feat/x') 'origin has the rewritten head'
    $null = G $w.Origin update-ref refs/heads/feat/x $base
    $null = G $m commit -q --amend -m 'rewritten again'
    $r = Invoke-Step { Invoke-PushBranch -Lease $x1b }
    Assert-Match "^the leased push was refused: .*`norigin's feat/x moved since $x1b; nothing is overwritten - fetch, read what moved, and decide$" $r.Stop 'a stale lease is refused'
    Assert-Equal $base (Get-OriginTip $w.Origin 'feat/x') 'origin is unchanged'
    $r = Invoke-Step { Invoke-PushBranch -Lease ('f' * 40) }
    Assert-Match "^the lease 'f{40}' is not a commit this clone has" $r.Stop 'a lease this clone lacks stops'
    Assert-Equal $base (Get-OriginTip $w.Origin 'feat/x') 'before any push'
    $null = G $w.Origin update-ref refs/heads/feat/x $x1b
    $null = G $m fetch -q origin feat/x
    $null = G $m reset -q --hard $x1b
    $null = New-Commit $m 'x2.txt'
    $r = Invoke-Step { Invoke-PushBranch -Lease none }
    Assert-Equal '' $r.Stop '-Lease none is the plain push'
    Assert-Match '^pushed feat/x to origin at [0-9a-f]{40}$' ($r.Lines -join ';') 'reported without a lease'

    Write-Host 'the default branch and a protected prefix stop'
    $null = G $m checkout -q master
    $r = Invoke-Step { Invoke-PushBranch }
    Assert-Match '^the checkout is on master, the default branch' $r.Stop 'the default branch stops'
    $null = G $m checkout -q -b release/1 master
    $r = Invoke-Step { Invoke-PushBranch }
    Assert-Equal "release/1 has the protected prefix release/ - the repository's own release procedure owns it" $r.Stop 'a protected prefix stops'
    Assert-Equal '' (Get-OriginTip $w.Origin 'release/1') 'and nothing is pushed'
}
finally { Pop-Location }

Write-Host 'the entry point: a child process, the exit codes and the STOP line'
$w = New-World 'w2'; $m = $w.Main
$null = G $m checkout -q -b feat/y master
$y1 = New-Commit $m 'y1.txt'
$c = Invoke-Child $Script @() $m
Assert-Equal 0 $c.Code 'the happy path exits 0'
Assert-Equal "pushed feat/y to origin at $y1" ($c.Lines -join ';') 'and prints the push'
$null = G $m checkout -q master
$c = Invoke-Child $Script @() $m
Assert-Equal 1 $c.Code 'the default branch exits 1'
Assert-Line $c.Lines 'STOP: the checkout is on master, the default branch' 'with the STOP line' -Prefix
$help = Get-Help ([WildcardPattern]::Escape($Script))
Assert-Match 'Push the feature branch to origin' ($help.Synopsis) 'the script exposes its help'
Complete-Suite 'Push-Branch'
