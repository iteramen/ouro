<#
.SYNOPSIS
    Test of Compress-Branch.ps1 over scratch repositories and the gh stand-in.
.DESCRIPTION
    The script is dot-sourced and its function called per row; the fixture beside this file holds
    the stand-in (which answers the based-on count) and builds each repository: a main checkout, a
    bare origin, and a feature branch of three fix-up commits by one author, pushed.

    Pinned: `prepare` names a backup at the tip and prints the lease (origin's tip), the base and
    the author; it refuses, with no backup made, a branch origin lacks, origin holding a commit the
    local branch lacks, local commits origin lacks, an open PR based on the branch, and a range
    with two authors. After the session composes the arc, `push` lands it with the lease and
    origin holds the new head; it refuses a tree that is not clean, a tree that differs from the
    backup's, a HEAD not built on the base, a commit by another author, a missing or unknown
    backup, and a lease origin no longer holds, each with origin unchanged. The default branch and
    an unknown subcommand stop. The child rows pin the entry point.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'LandScripts.Fixture.ps1')
$Script = Find-Script 'Compress-Branch.ps1'
. $Script

$NoPrs = @{ 'pr list --base feat/x' = Answer @() }
function Get-OriginTip([string]$Origin, [string]$Branch) {
    $line = @(G $Origin for-each-ref '--format=%(objectname)' "refs/heads/$Branch")
    if ($line.Count) { $line[0] } else { '' }
}
function Get-Backups([string]$Main) { @(G $Main for-each-ref '--format=%(refname:lstrip=2)' 'refs/heads/backup/') }
# feat/x: three commits by Ann on master, pushed.
function New-FixupWorld([string]$Name) {
    $w = New-World $Name
    # A second commit on master, so the base has a parent for the off-the-base row.
    $null = New-Commit $w.Main 'm2.txt'
    $null = G $w.Main push -q origin master
    $w.Base = @(G $w.Main rev-parse master)[0]
    $null = G $w.Main checkout -q -b feat/x master
    $null = New-Commit $w.Main 'x1.txt' 'Ann <ann@x.test>'
    $null = New-Commit $w.Main 'x2.txt' 'Ann <ann@x.test>'
    $w.Tip = New-Commit $w.Main 'x3.txt' 'Ann <ann@x.test>'
    $null = G $w.Main push -q -u origin feat/x
    $w
}
# The session's step: one commit holding the whole range, by the range's author.
function Compose([string]$Main, [string]$Base, [string]$Author = 'Ann <ann@x.test>', [string]$Subject = 'feat(x): the arc') {
    $null = G $Main reset -q $Base
    $null = G $Main add -A
    $null = G $Main commit -q --author=$Author -m $Subject
    @(G $Main rev-parse HEAD)[0]
}

Write-Host 'prepare then push: the arc lands with the lease'
$w = New-FixupWorld 'w1'; $m = $w.Main
Push-Location -LiteralPath $m
try {
    Set-Gh $NoPrs
    $r = Invoke-Step { Invoke-CompressBranch prepare }
    Assert-Equal '' $r.Stop 'prepare goes through'
    $backups = @(Get-Backups $m)
    Assert-Equal 1 $backups.Count 'one backup branch'
    Assert-Match '^backup/feat/x-\d{14}$' $backups[0] 'named for the branch and the time'
    $backup = $backups[0]
    Assert-Equal $w.Tip (@(G $m rev-parse $backup)[0]) 'at the tip'
    Assert-Equal "backup: $backup,lease: $($w.Tip),base: $($w.Base),author: Ann <ann@x.test>" (($r.Lines | Select-Object -First 4) -join ',') 'the four lines the session carries'
    Assert-Match "^compose the arc on $($w.Base) \(git reset $($w.Base), then commit the changes back in groups, each with --author=`"Ann <ann@x.test>`"\), then run: push -Backup $backup$" $r.Lines[4] 'and what to do next'
    Assert-Equal 'pr list -R o/n --base feat/x --state open --json number' ($r.Gh -join ';') 'the PRs based on the branch are counted, naming the repository'
    $arc = Compose $m $w.Base
    $r = Invoke-Step { Invoke-CompressBranch push -Backup $backup }
    Assert-Equal '' $r.Stop 'push goes through'
    Assert-Equal "compressed and pushed feat/x as $arc - back to step 7: the checks must pass on this head before the merge" ($r.Lines -join ';') 'and reports'
    Assert-Equal $arc (Get-OriginTip $w.Origin 'feat/x') 'origin holds the arc'
    Assert-Equal 1 @(G $m rev-list --count "$($w.Base)..HEAD").Count 'one commit in the range'
    Assert-Equal (@(G $m rev-parse "$backup^{tree}")[0]) (@(G $m rev-parse 'HEAD^{tree}')[0]) 'with the backup''s tree'
}
finally { Pop-Location }

Write-Host 'push: the refusals, each with origin unchanged'
$w = New-FixupWorld 'w2'; $m = $w.Main
Push-Location -LiteralPath $m
try {
    Set-Gh $NoPrs
    $null = Invoke-Step { Invoke-CompressBranch prepare }
    $backup = @(Get-Backups $m)[0]
    $r = Invoke-Step { Invoke-CompressBranch push }
    Assert-Equal 'push takes -Backup <the backup branch prepare printed>' $r.Stop 'no backup named stops'
    $r = Invoke-Step { Invoke-CompressBranch push -Backup backup/feat/x-00000000000000 }
    Assert-Equal 'no branch backup/feat/x-00000000000000 - it is the backup prepare printed' $r.Stop 'an unknown backup stops'
    $null = G $m reset -q $w.Base
    $r = Invoke-Step { Invoke-CompressBranch push -Backup $backup }
    Assert-Equal 'the working tree is not clean - the arc is not all committed' $r.Stop 'uncommitted changes stop'
    $null = G $m add -A
    [System.IO.File]::WriteAllText((Join-Path $m 'x3.txt'), "changed`n", $Utf8)
    $null = G $m add -A
    $null = G $m commit -q --author='Ann <ann@x.test>' -m 'feat(x): the arc, changed'
    $r = Invoke-Step { Invoke-CompressBranch push -Backup $backup }
    Assert-Equal "the compressed tree differs from $backup - nothing is pushed; go back with git reset --hard $backup" $r.Stop 'a tree that differs stops'
    $null = G $m reset -q --hard $backup
    $null = Compose $m $w.Base 'Bob <bob@y.test>'
    $r = Invoke-Step { Invoke-CompressBranch push -Backup $backup }
    Assert-Match "^a compressed commit's author is not Ann <ann@x.test> - recommit it with --author:`nBob <bob@y.test>$" $r.Stop 'another author stops'
    $null = G $m reset -q --hard $backup
    $null = G $m reset -q --soft "$($w.Base)^"
    $null = G $m commit -q --author='Ann <ann@x.test>' -m 'feat(x): the arc, off the base'
    $r = Invoke-Step { Invoke-CompressBranch push -Backup $backup }
    Assert-Equal "HEAD is not built on $($w.Base)" $r.Stop 'a HEAD off the base stops'
    Assert-Equal $w.Tip (Get-OriginTip $w.Origin 'feat/x') 'origin is unchanged by every refusal'
    $null = G $m reset -q --hard $backup
    $null = Compose $m $w.Base
    $null = G $w.Origin update-ref refs/heads/feat/x $w.Base
    $r = Invoke-Step { Invoke-CompressBranch push -Backup $backup }
    Assert-Match "^the leased push was refused: .*`norigin's feat/x moved since $($w.Tip); nothing is overwritten$" $r.Stop 'a lease origin no longer holds is refused'
    Assert-Equal $w.Base (Get-OriginTip $w.Origin 'feat/x') 'and origin is unchanged'
}
finally { Pop-Location }

Write-Host 'prepare: the refusals, each with no backup made'
$w = New-FixupWorld 'w3'; $m = $w.Main
Push-Location -LiteralPath $m
try {
    Set-Gh @{ 'pr list --base feat/x' = Answer @(@{ number = 12 }) }
    $r = Invoke-Step { Invoke-CompressBranch prepare }
    Assert-Equal 'shared branch: an open PR is based on feat/x (1 of them) - the rewrite is yours to decide' $r.Stop 'an open PR based on the branch stops'
    Set-Gh $NoPrs
    $extra = New-Commit $m 'x4.txt' 'Ann <ann@x.test>'
    $r = Invoke-Step { Invoke-CompressBranch prepare }
    Assert-Equal 'the local feat/x has commits origin lacks - push them (step 4) and let the checks run, or drop them, first' $r.Stop 'a local commit origin lacks stops'
    $null = G $m push -q origin "${extra}:refs/heads/feat/x"
    $null = G $m reset -q --hard $w.Tip
    $r = Invoke-Step { Invoke-CompressBranch prepare }
    Assert-Equal "shared branch: origin's feat/x holds a commit the local branch lacks ($extra) - the rewrite is yours to decide" $r.Stop 'origin ahead stops'
    $null = G $w.Origin update-ref refs/heads/feat/x $w.Tip
    $null = G $m checkout -q -b feat/y master
    $null = New-Commit $m 'y.txt' 'Ann <ann@x.test>'
    $r = Invoke-Step { Invoke-CompressBranch prepare }
    Assert-Equal 'origin has no feat/y - push it (step 4) and open the PR first' $r.Stop 'a branch origin lacks stops'
    $null = New-Commit $m 'y2.txt' 'Bob <bob@y.test>'
    $null = G $m push -q -u origin feat/y
    Set-Gh @{ 'pr list --base feat/y' = Answer @() }
    $r = Invoke-Step { Invoke-CompressBranch prepare }
    Assert-Match "^more than one author - the range lands as it is, uncompressed:`nAnn <ann@x.test>`nBob <bob@y.test>$" $r.Stop 'two authors stop'
    Assert-Equal 0 @(Get-Backups $m).Count 'no backup was made by any refusal'
    $null = G $m checkout -q master
    $r = Invoke-Step { Invoke-CompressBranch prepare }
    Assert-Equal 'the checkout is on master, the default branch - compress from the feature branch' $r.Stop 'the default branch stops'
    $r = Invoke-Step { Invoke-CompressBranch squash }
    Assert-Equal 'the first argument is the subcommand: prepare, or push -Backup <branch>' $r.Stop 'an unknown subcommand stops'
    Assert-Equal 0 $r.Gh.Count 'before any gh call'
}
finally { Pop-Location }

Write-Host 'the entry point: a child process, the exit codes and the STOP line'
$w = New-FixupWorld 'w4'; $m = $w.Main
Set-Gh $NoPrs
$c = Invoke-Child $Script @('prepare') $m
Assert-Equal 0 $c.Code 'prepare exits 0'
Assert-Line $c.Lines "lease: $($w.Tip)" 'and prints the lease'
$c = Invoke-Child $Script @() $m
Assert-Equal 1 $c.Code 'no subcommand exits 1'
Assert-Equal 'STOP: the first argument is the subcommand: prepare, or push -Backup <branch>' ($c.Lines -join ';') 'with the STOP line alone'
$help = Get-Help ([WildcardPattern]::Escape($Script))
Assert-Match 'Compress a feature branch' ($help.Synopsis) 'the script exposes its help'
Complete-Suite 'Compress-Branch'
