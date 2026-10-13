<#
.SYNOPSIS
    Test of Sync-Branch.ps1 over scratch repositories and the gh stand-in.
.DESCRIPTION
    The script is dot-sourced and its function called per row; the fixture beside this file holds
    the stand-in and builds each repository: a main checkout, a bare origin whose master gains a
    commit after the feature branches fork, and feature branches that change a file the base also
    changed -- in another hunk, so the merge is clean, or in the same hunk, so it conflicts.

    Pinned: under the merge answer (the default, named as one where the binding declares none) a
    merge commit lands on the branch with the pre-sync tip as its first parent and origin's
    default branch as its second, the files both sides changed are listed and the ones only the
    branch changed are not; a base that gained nothing is reported and nothing is rewritten; a
    conflict stops with the conflicted path, the merge left in the tree. Under rebase, a branch
    origin lacks is rebased behind a backup at the old tip with `lease: none`; one origin has
    prints origin's tip as the lease after the two shared-branch signs are read (origin holding a
    commit the local branch lacks, an open PR based on the branch), each shown stopping with no
    backup made; a rebase that conflicts stops naming the abort and the lease. The default branch
    and an answer outside merge and rebase stop. The child rows pin the entry point.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'LandScripts.Fixture.ps1')
$Script = Find-Script 'Sync-Branch.ps1'
. $Script

function Set-Lines([string]$Dir, [string]$File, [string[]]$Lines, [string]$Message) {
    [System.IO.File]::WriteAllText((Join-Path $Dir $File), (($Lines -join "`n") + "`n"), $Utf8)
    $null = G $Dir add -- $File
    $null = G $Dir commit -q -m $Message
    @(G $Dir rev-parse HEAD)[0]
}
$Ten = @(1..10 | ForEach-Object { "l$_" })
# A world whose master holds shared.txt, with feature branches forked before the base moved:
#   feat/x   changes l1 and adds x.txt (clean against the base)
#   feat/c   changes l10 (the base's own line: a conflict)
# then master gains base.txt and a change to l10, pushed to origin.
function New-SyncWorld([string]$Name) {
    $w = New-World $Name
    $m = $w.Main
    $null = Set-Lines $m 'shared.txt' $Ten 'shared'
    $null = G $m push -q origin master
    $null = G $m checkout -q -b feat/x master
    $null = New-Commit $m 'x.txt'
    $xLines = @($Ten); $xLines[0] = 'L1'
    $w.XTip = Set-Lines $m 'shared.txt' $xLines 'branch changes l1'
    $null = G $m checkout -q -b feat/c master
    $cLines = @($Ten); $cLines[9] = 'c10'
    $null = Set-Lines $m 'shared.txt' $cLines 'branch changes l10'
    $null = G $m checkout -q master
    $null = New-Commit $m 'base.txt'
    $bLines = @($Ten); $bLines[9] = 'L10'
    $w.BaseTip = Set-Lines $m 'shared.txt' $bLines 'base changes l10'
    $null = G $m push -q origin master
    $w.OldBase = @(G $m rev-parse 'master~2')[0]
    $w
}
$NoPrs = @{ 'pr list --base feat/x' = Answer @() }

Write-Host 'merge: the base is merged in, both-sides files listed, the pre-sync tip and old base printed'
$w = New-SyncWorld 'w1'; $m = $w.Main
$null = G $m checkout -q feat/x
Push-Location -LiteralPath $m
try {
    Set-Gh $NoPrs
    $r = Invoke-Step { Invoke-SyncBranch }
    Assert-Equal '' $r.Stop 'the run ends without a stop'
    $head = @(G $m rev-parse HEAD)[0]
    Assert-Line $r.Lines 'ship.sync: not declared - merge, the default' 'a binding with no answer merges and says so'
    Assert-Line $r.Lines 'the base gained since branching (2):' 'the base''s commits are counted'
    Assert-Line $r.Lines "merged origin/master into feat/x: $head" 'the merge is reported with the new head'
    Assert-Line $r.Lines "pre-sync tip: $($w.XTip) (HEAD^1); old base: $($w.OldBase)" 'the pre-sync tip and the old base are named'
    Assert-Equal "$($w.XTip) $($w.BaseTip)" (@(G $m log -1 --format=%P) -join ' ') 'the merge commit''s parents are the pre-sync tip and origin''s default branch'
    Assert-Line $r.Lines "both sides changed, read each against the branch's own additions (git diff $($w.OldBase) origin/master -- <file>):" 'the both-sides list is introduced'
    Assert-Line $r.Lines '  shared.txt' 'the file both changed is listed'
    Assert-NoMatch 'x\.txt' ($r.Lines -join "`n") 'a file only the branch changed is not'
    Assert-NoMatch '^lease:' ($r.Lines -join "`n") 'no lease under merge'
    Assert-Equal 0 $r.Gh.Count 'the merge path asks gh nothing'
    Assert-Equal '' (@(G $m status --porcelain) -join ';') 'the tree is clean'

    Write-Host 'a base that gained nothing: reported, nothing rewritten'
    $r = Invoke-Step { Invoke-SyncBranch }
    Assert-Equal '' $r.Stop 'no stop'
    Assert-Equal 'ship.sync: not declared - merge, the default,the base gained since branching (0):,the base gained nothing - no sync, so step 4 pushes plainly, without a lease' ($r.Lines -join ',') 'the answer, the empty gain, and the no-sync line'
    Assert-Equal $head (@(G $m rev-parse HEAD)[0]) 'HEAD is unchanged'

    Write-Host 'a conflict stops with the path, the merge left in the tree'
    $null = G $m checkout -q feat/c
    $r = Invoke-Step { Invoke-SyncBranch }
    Assert-Match "^the merge of origin/master conflicts in:`n  shared.txt`nresolve each \(an append-only file keeps both sides' entries\), build, test, then git commit; or abandon it with git merge --abort" $r.Stop 'the stop names the conflicted file and the two ways out'
    Assert-Match "read-verify every resolution: each line either parent added since $($w.OldBase) is in the result" $r.Stop 'and the verification before the push'
    Assert-Equal 1 @(G $m ls-files -u | ForEach-Object { ($_ -split "`t")[1] } | Sort-Object -Unique).Count 'the conflict is left in the tree for the session'
    $null = G $m merge --abort

    Write-Host '-Sync outside merge and rebase stops; the default branch stops'
    $r = Invoke-Step { Invoke-SyncBranch -Sync squash }
    Assert-Equal "-Sync reads 'squash', not merge or rebase" $r.Stop 'a bad answer stops'
    $null = G $m checkout -q master
    $r = Invoke-Step { Invoke-SyncBranch }
    Assert-Equal 'the checkout is on master, the default branch - sync from the feature branch' $r.Stop 'the default branch stops'
}
finally { Pop-Location }

Write-Host 'rebase: a branch origin lacks is rebased behind a backup, lease none'
$w = New-SyncWorld 'w2'; $m = $w.Main
$null = G $m checkout -q feat/x
Push-Location -LiteralPath $m
try {
    Set-Gh $NoPrs
    $r = Invoke-Step { Invoke-SyncBranch -Sync rebase }
    Assert-Equal '' $r.Stop 'the run ends without a stop'
    $backups = @(G $m for-each-ref '--format=%(refname:lstrip=2) %(objectname)' 'refs/heads/backup/')
    Assert-Equal 1 $backups.Count 'one backup branch'
    Assert-Match "^backup/feat/x-\d{14} $($w.XTip)$" $backups[0] 'at the pre-sync tip'
    $backup = ($backups[0] -split ' ')[0]
    Assert-Line $r.Lines "backup: $backup at $($w.XTip.Substring(0, 7))" 'the backup is reported'
    $head = @(G $m rev-parse HEAD)[0]
    Assert-Line $r.Lines "rebased feat/x onto origin/master: $head" 'the rebase is reported with the new head'
    Assert-Equal 1 @((@(G $m log -1 --format=%P) -join ' ') -split ' ').Count 'the head has one parent'
    $null = git -C $m merge-base --is-ancestor origin/master HEAD 2>&1
    Assert-Equal 0 $LASTEXITCODE 'and sits on origin''s default branch'
    Assert-Line $r.Lines "pre-sync tip: $($w.XTip) ($backup); old base: $($w.OldBase)" 'the pre-sync tip is the backup'
    Assert-Line $r.Lines '  shared.txt' 'the file both changed is listed'
    Assert-Equal 'lease: none' $r.Lines[-1] 'the last line is the lease, none where origin had no branch'
    Assert-Equal 0 $r.Gh.Count 'with no branch on origin, gh is asked nothing'

    Write-Host 'rebase: a branch origin has prints its tip as the lease after the two signs'
    $null = G $m reset -q --hard $w.XTip
    $null = G $m branch -D $backup
    $null = G $m push -q -u origin feat/x
    $r = Invoke-Step { Invoke-SyncBranch -Sync rebase }
    Assert-Equal '' $r.Stop 'the run ends without a stop'
    Assert-Equal "lease: $($w.XTip)" $r.Lines[-1] 'the lease is origin''s tip'
    Assert-Equal 'pr list -R o/n --base feat/x --state open --json number' ($r.Gh -join ';') 'the PRs based on the branch are counted, naming the repository'

    Write-Host 'the shared-branch signs each stop, with no backup made'
    $null = G $m reset -q --hard $w.XTip
    foreach ($bk in @(G $m for-each-ref '--format=%(refname:lstrip=2)' 'refs/heads/backup/')) { $null = G $m branch -D $bk }
    $null = G $w.Origin update-ref refs/heads/feat/x $w.BaseTip
    $r = Invoke-Step { Invoke-SyncBranch -Sync rebase }
    Assert-Equal "shared branch: origin's feat/x holds a commit the local branch lacks ($($w.BaseTip)) - the rewrite is yours to decide" $r.Stop 'origin ahead stops'
    $null = G $w.Origin update-ref refs/heads/feat/x $w.XTip
    Set-Gh @{ 'pr list --base feat/x' = Answer @(@{ number = 12 }) }
    $r = Invoke-Step { Invoke-SyncBranch -Sync rebase }
    Assert-Equal 'shared branch: an open PR is based on feat/x (1 of them) - the rewrite is yours to decide' $r.Stop 'an open PR based on the branch stops'
    Set-Gh @{ 'pr list --base feat/x' = Fail }
    $r = Invoke-Step { Invoke-SyncBranch -Sync rebase }
    Assert-Match '^cannot count the PRs based on feat/x' $r.Stop 'a count that fails stops'
    Assert-Equal 0 @(G $m for-each-ref 'refs/heads/backup/').Count 'no backup was made by any stop'
    Assert-Equal $w.XTip (@(G $m rev-parse HEAD)[0]) 'and the branch is where it was'

    Write-Host 'a rebase that conflicts stops naming the abort and the lease'
    Set-Gh $NoPrs
    $null = G $m checkout -q feat/c
    $r = Invoke-Step { Invoke-SyncBranch -Sync rebase }
    Assert-Match '^the rebase onto origin/master stopped: .*CONFLICT' $r.Stop 'the stop carries git''s message'
    Assert-Match 'or abandon it with git rebase --abort, which returns to the tip backup/feat/c-\d{14} holds\. The lease for step 4 is none$' $r.Stop 'and names the abort, the backup and the lease'
    $null = G $m rebase --abort
}
finally { Pop-Location }

Write-Host 'the entry point: a child process, the exit codes and the STOP line'
$w = New-SyncWorld 'w3'; $m = $w.Main
$null = G $m checkout -q feat/x
Set-Gh $NoPrs
$c = Invoke-Child $Script @() $m
Assert-Equal 0 $c.Code 'the happy path exits 0'
Assert-Line $c.Lines 'merged origin/master into feat/x: ' 'and reports the merge' -Prefix
$null = G $m checkout -q master
$c = Invoke-Child $Script @() $m
Assert-Equal 1 $c.Code 'the default branch exits 1'
Assert-Equal 'STOP: the checkout is on master, the default branch - sync from the feature branch' ($c.Lines -join ';') 'with the STOP line alone'
$c = Invoke-Child $Script @('-Sync', 'squash') $m
Assert-Equal 1 $c.Code 'a bad answer exits 1'
$help = Get-Help ([WildcardPattern]::Escape($Script))
Assert-Match 'Bring the feature branch up to date' ($help.Synopsis) 'the script exposes its help'
Complete-Suite 'Sync-Branch'
