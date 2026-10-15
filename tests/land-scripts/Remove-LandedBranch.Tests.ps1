<#
.SYNOPSIS
    Test of Remove-LandedBranch.ps1 over the gh stand-in and scratch repositories.
.DESCRIPTION
    The script is dot-sourced and its function called per row; the fixture beside this file holds
    the stand-in and builds each repository: a main checkout on master, a bare origin, a feature
    branch on both sides, a worktree under .claude/worktrees/ holding it, and a backup branch the
    landing named.

    Pinned: the happy path deletes origin's branch, removes the worktree (the directory gone from
    the filesystem), deletes the local branch and the backup, one line each. Refused with nothing
    deleted: a PR not MERGED, an open PR based on the branch or a count that fails, the default
    branch, a protected prefix, the main checkout itself on the branch, this process's current
    directory inside the worktree, and a worktree outside .claude/worktrees/. A branch origin no
    longer lists is reported and the run goes on; a branch with no local copy and no worktree is
    only deleted on origin; -Branch names another branch than the PR's head. A worktree git
    refuses to remove (an untracked file) stops with git's message, the directory still there. A
    process whose current directory is inside the worktree stops the run before any deletion,
    naming its PID, where /proc exists (Linux; elsewhere the row prints a skip line); on Windows,
    which is not scanned, the same process ends the run at the `left on disk` stop.
    The child rows pin the entry point.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'LandScripts.Fixture.ps1')
$Script = Find-Script 'Remove-LandedBranch.ps1'
. $Script

$MergedView = Answer @{ state = 'MERGED'; headRefName = 'feat/x' }
function Table([hashtable]$Over = @{}) {
    $t = @{ 'pr view 7' = $MergedView; 'pr list --base feat/x' = Answer @() }
    foreach ($k in $Over.Keys) { $t[$k] = $Over[$k] }
    $t
}
# A world with feat/x on both sides, a worktree on it, and a backup branch.
function New-LandedWorld([string]$Name) {
    $w = New-World $Name
    $null = G $w.Main checkout -q -b feat/x master
    $null = New-Commit $w.Main 'x1.txt'
    $null = G $w.Main push -q -u origin feat/x
    $null = G $w.Main branch backup/feat/x-20260101000000 feat/x
    $null = G $w.Main checkout -q master
    $w.Worktree = Join-Path $w.Main '.claude/worktrees/x'
    $null = G $w.Main worktree add -q $w.Worktree feat/x
    $w
}
function Get-Heads([string]$Dir) { (@(G $Dir for-each-ref '--format=%(refname:lstrip=2)' refs/heads) | Sort-Object) -join ',' }

Write-Host 'the happy path: origin, worktree, local branch and backup, in that order'
$w = New-LandedWorld 'w1'
Push-Location -LiteralPath $w.Main
try {
    Set-Gh (Table)
    $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 }
    Assert-Equal '' $r.Stop 'the run ends without a stop'
    Assert-Line $r.Gh 'pr view 7 -R o/n --json state,headRefName' 'the PR is read, naming the repository'
    Assert-Line $r.Gh 'pr list -R o/n --base feat/x --state open --json number' 'the PRs based on the branch are counted'
    Assert-Equal "deleted: origin feat/x,removed: worktree $((Get-Item -LiteralPath $w.Main).FullName)$([IO.Path]::DirectorySeparatorChar).claude$([IO.Path]::DirectorySeparatorChar)worktrees$([IO.Path]::DirectorySeparatorChar)x,deleted: local feat/x,deleted: backup backup/feat/x-20260101000000" ($r.Lines -join ',') 'one line per deletion, in order'
    Assert-Equal 'master' (Get-Heads $w.Origin) 'origin keeps master alone'
    Assert-Equal 'master' (Get-Heads $w.Main) 'the local branches are master alone'
    Assert-Equal $false (Test-Path -LiteralPath $w.Worktree) 'the worktree directory is gone from the filesystem'
    Assert-Equal 1 @(G $w.Main worktree list --porcelain | Where-Object { $_ -like 'worktree *' }).Count 'and git lists the main checkout alone'
}
finally { Pop-Location }

Write-Host 'the guards, each refused with nothing deleted'
$w = New-LandedWorld 'w2'
$before = (Get-Heads $w.Origin) + '|' + (Get-Heads $w.Main)
Push-Location -LiteralPath $w.Main
try {
    Set-Gh (Table @{ 'pr view 7' = Answer @{ state = 'OPEN'; headRefName = 'feat/x' } })
    $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 }
    Assert-Match "^#7 is 'OPEN', not MERGED - deleting an open pull request's head branch closes it" $r.Stop 'a PR not MERGED stops'
    Set-Gh (Table @{ 'pr view 7' = Fail })
    $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 }
    Assert-Match '^cannot read #7 \(gh pr view exit 1\)' $r.Stop 'a PR that does not read stops'
    Set-Gh (Table @{ 'pr list --base feat/x' = Answer @(@{ number = 12 }) })
    $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 }
    Assert-Equal '1 open PR(s) are based on feat/x - the branch stays' $r.Stop 'an open PR based on the branch stops'
    Set-Gh (Table @{ 'pr list --base feat/x' = Fail })
    $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 }
    Assert-Match '^cannot count the PRs based on feat/x \(exit 1\)' $r.Stop 'a count that fails stops'
    Set-Gh (Table @{ 'pr view 7' = Answer @{ state = 'MERGED'; headRefName = 'master' } })
    $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 }
    Assert-Equal "master is the default branch - not this script's to delete" $r.Stop 'the default branch stops'
    Set-Gh (Table @{ 'pr view 7' = Answer @{ state = 'MERGED'; headRefName = 'release/1' } })
    $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 }
    Assert-Equal "release/1 has the protected prefix release/ - not this script's to delete" $r.Stop 'a protected prefix stops'
    $r = Invoke-Step { Invoke-RemoveLandedBranch }
    Assert-Equal '-Pr <number> is required' $r.Stop 'no -Pr stops'
    Assert-Equal 0 $r.Gh.Count 'before any call'
    Assert-Equal $before ((Get-Heads $w.Origin) + '|' + (Get-Heads $w.Main)) 'every branch on both sides is as it was'
    Assert-Equal $true (Test-Path -LiteralPath $w.Worktree) 'and the worktree is still there'
}
finally { Pop-Location }

Write-Host 'the current directory inside the worktree stops before any deletion'
Push-Location -LiteralPath $w.Worktree
try {
    Set-Gh (Table)
    $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 }
    Assert-Match '^the current directory is inside .* - move every shell out of it' $r.Stop 'the stop says to move out'
    Assert-Equal $before ((Get-Heads $w.Origin) + '|' + (Get-Heads $w.Main)) 'nothing is deleted, origin''s branch included'
}
finally { Pop-Location }

Write-Host 'the main checkout on the branch stops; a worktree outside .claude/worktrees/ stops'
$w = New-World 'w3'
$null = G $w.Main checkout -q -b feat/x master
$null = New-Commit $w.Main 'x1.txt'
$null = G $w.Main push -q -u origin feat/x
Push-Location -LiteralPath $w.Main
try {
    Set-Gh (Table)
    $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 }
    Assert-Match '^the main checkout .* is on feat/x - switch it to master first' $r.Stop 'the main checkout on the branch stops'
    Assert-Equal 'feat/x,master' (Get-Heads $w.Origin) 'with origin''s branch still there'
    $null = G $w.Main checkout -q master
    $outside = Join-Path $Scratch 'w3/elsewhere'
    $null = G $w.Main worktree add -q $outside feat/x
    $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 }
    Assert-Match "^feat/x is checked out at .*elsewhere, outside .* - not this script's to remove" $r.Stop 'a worktree elsewhere stops'
    Assert-Equal 'feat/x,master' (Get-Heads $w.Origin) 'with origin''s branch still there'
    $null = G $w.Main worktree remove $outside
}
finally { Pop-Location }

Write-Host 'origin already deleted is nothing to do; no local branch and no worktree is origin alone; -Branch names the branch'
Push-Location -LiteralPath $w.Main
try {
    $null = G $w.Origin update-ref -d refs/heads/feat/x
    Set-Gh (Table)
    $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 }
    Assert-Equal '' $r.Stop 'no stop'
    Assert-Equal 'feat/x is already deleted on origin - nothing to do,deleted: local feat/x' ($r.Lines -join ',') 'origin is reported, the local branch deleted'
    $null = G $w.Main branch feat/y master
    $null = G $w.Main push -q origin feat/y
    $null = G $w.Main branch -D feat/y
    Set-Gh (Table @{ 'pr list --base feat/y' = Answer @() })
    $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 -Branch feat/y }
    Assert-Equal '' $r.Stop 'no stop'
    Assert-Equal 'deleted: origin feat/y,no local feat/y - nothing to do' ($r.Lines -join ',') '-Branch picks the branch; with no local copy, origin alone'
    Assert-Line $r.Gh 'pr list -R o/n --base feat/y --state open --json number' 'and the count is of that branch'
    Assert-Equal 'master' (Get-Heads $w.Origin) 'origin keeps master alone'
}
finally { Pop-Location }

Write-Host 'a worktree git refuses to remove stops with its message, the directory still there'
$w = New-LandedWorld 'w4'
[System.IO.File]::WriteAllText((Join-Path $w.Worktree 'gate-output.log'), "x`n", $Utf8)
Push-Location -LiteralPath $w.Main
try {
    Set-Gh (Table)
    $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 }
    Assert-Match '^git refused to remove the worktree .* - an untracked file or a submodule keeps it' $r.Stop 'the refusal is named'
    Assert-Match 'untracked' $r.Stop 'with git''s own reason'
    Assert-Equal $true (Test-Path -LiteralPath $w.Worktree) 'the directory is still there'
    Assert-Equal 'master' (Get-Heads $w.Origin) 'origin''s branch went first'
    Assert-Equal 'backup/feat/x-20260101000000,feat/x,master' (Get-Heads $w.Main) 'the local branch and the backup stay'
}
finally { Pop-Location }

Write-Host 'a process inside the worktree: Linux stops naming its PID before any deletion; Windows is not scanned'
$w = New-LandedWorld 'w6'
$before = (Get-Heads $w.Origin) + '|' + (Get-Heads $w.Main)
$hidden = if ($IsWindows) { @{ WindowStyle = 'Hidden' } } else { @{} }
$held = Start-Process -FilePath $Pwsh -ArgumentList '-NoProfile', '-Command', 'Start-Sleep 300' -WorkingDirectory $w.Worktree -PassThru @hidden
Push-Location -LiteralPath $w.Main
try {
    Start-Sleep -Seconds 2
    if ($held.HasExited) { throw "the sleeper exited at once (exit $($held.ExitCode)); the row has no process to find" }
    Set-Gh (Table)
    if ($IsWindows) {
        $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 }
        Assert-Match '^left on disk: ' $r.Stop 'no scan: the removal hits the held directory and stops left on disk'
    }
    elseif (Test-Path -LiteralPath '/proc/self/cwd') {
        $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 }
        Assert-Match "^a process has its current directory inside .*: pid $($held.Id) in .* - move the shell out of it or let the job finish" $r.Stop 'the stop names the PID and its directory'
        Assert-Equal $before ((Get-Heads $w.Origin) + '|' + (Get-Heads $w.Main)) 'nothing is deleted, origin''s branch included'
        Assert-Equal $true (Test-Path -LiteralPath $w.Worktree) 'the worktree is still there'
        Stop-Process -Id $held.Id -Force
        $held.WaitForExit()
        $r = Invoke-Step { Invoke-RemoveLandedBranch -Pr 7 }
        Assert-Equal '' $r.Stop 'with the process gone, the run goes through'
        Assert-Equal $false (Test-Path -LiteralPath $w.Worktree) 'and removes the worktree'
    }
    else { Write-Host '  skip: no /proc here, so no process is read' -ForegroundColor DarkGray }
}
finally {
    Pop-Location
    Stop-Process -Id $held.Id -Force -ErrorAction Ignore
    $held.WaitForExit()
}

Write-Host 'the entry point: a child process, the exit codes and the STOP line'
$w = New-LandedWorld 'w5'
Set-Gh (Table)
$c = Invoke-Child $Script @('-Pr', '7') $w.Main
Assert-Equal 0 $c.Code 'the happy path exits 0'
Assert-Line $c.Lines 'deleted: backup backup/feat/x-20260101000000' 'and prints the deletions'
Assert-Equal $false (Test-Path -LiteralPath $w.Worktree) 'the worktree is gone'
$c = Invoke-Child $Script @() $w.Main
Assert-Equal 1 $c.Code 'no parameter exits 1'
Assert-Equal 'STOP: -Pr <number> is required' ($c.Lines -join ';') 'with the STOP line alone'
Set-Gh (Table @{ 'pr view 7' = Answer @{ state = 'CLOSED'; headRefName = 'feat/x' } })
$c = Invoke-Child $Script @('-Pr', '7') $w.Main
Assert-Equal 1 $c.Code 'a refused guard exits 1'
Assert-Line $c.Lines "STOP: #7 is 'CLOSED', not MERGED" 'with its STOP line' -Prefix
$help = Get-Help ([WildcardPattern]::Escape($Script))
Assert-Match 'Delete a merged pull request''s branch' ($help.Synopsis) 'the script exposes its help'
Complete-Suite 'Remove-LandedBranch'
