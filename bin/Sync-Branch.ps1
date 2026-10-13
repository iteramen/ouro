<#
.SYNOPSIS
    Bring the feature branch up to date with the default branch the way the binding says: a merge
    of origin's default branch, or a rebase onto it behind a backup branch and a lease.
.DESCRIPTION
    /ouro:land step 2. Run from the repo root on the feature branch; the repository, the default
    branch and the sync answer come from the binding (.claude/ouro.toml, read through
    ouro-binding.py beside this script). CI runs on the branch head as it is and GitHub surfaces
    staleness only at merge time, so the default branch is brought in first and CI validates the
    combined result.

    It fetches origin's default branch and prints what the base gained since the branch forked.
    Where the base gained nothing it stops there, exit 0, and says so: nothing is rewritten and
    step 4 pushes plainly. Otherwise, by the answer:

      merge   `git merge origin/<default> --no-edit`. A conflict stops with the conflicted paths,
              the merge left in the tree for the session to resolve, build, test and commit (or
              abandon with `git merge --abort`); an append-only file keeps both sides' entries.
      rebase  Where origin already has the branch, the two readable signs of a branch someone
              else is working on each stop the run, since a lease does not cover that case:
              origin's branch holds a commit the local branch lacks, or an open pull request is
              based on the branch. (A third, the branch checked out in another worktree, cannot
              hold here: git refuses one branch in two worktrees, and this runs on the branch's
              own.) Then a backup branch is
              named at the current tip (`backup/<branch>-<timestamp>`), the branch is rebased onto
              origin's default branch, and the lease for step 4 is printed: origin's tip, or
              `none` where origin had no branch. A rebase that stops leaves git's message and the
              lease in the stop line; `git rebase --abort` returns to the tip the backup holds.

    After a sync that brought anything in, the files both sides changed since the old base are
    listed, one line each: the session reads the base's change to each against the branch's own
    additions, since a twin, copy or moved block of the changed text that the branch added keeps
    the old text and merges cleanly. The pre-sync tip (HEAD^1 after a merge, the backup after a
    rebase) and the old base are printed for that reading and for the read-verification a
    conflicted sync takes before it is pushed.
.PARAMETER Sync
    `merge` or `rebase`. Default: the binding's `ship.sync`, or `merge` where it declares none
    (printed).
.EXAMPLE
    pwsh -File <ouro>/bin/Sync-Branch.ps1
#>
param([string]$Sync)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Get-LandBinding.ps1')

# The paths both the branch and the base changed since the old base, in diff order.
function Get-BothSidesChanged {
    param([string]$OldBase, [string]$PreSync, [string]$Base)
    $ours = @(@(Invoke-Read git @('diff', '--name-only', '-z', $OldBase, $PreSync)) -join "`n" -split "`0" | Where-Object { $_ })
    $theirs = @(@(Invoke-Read git @('diff', '--name-only', '-z', $OldBase, $Base)) -join "`n" -split "`0" | Where-Object { $_ })
    @($ours | Where-Object { $theirs -ccontains $_ })
}

function Invoke-SyncBranch {
    param([string]$Sync)
    $b = Get-LandBinding
    $branch = Get-CurrentBranch
    if ($branch -ceq $b.Default) { throw "the checkout is on $branch, the default branch - sync from the feature branch" }
    if (-not $Sync) {
        if ($b.Sync) { $Sync = $b.Sync }
        else { Write-Output 'ship.sync: not declared - merge, the default'; $Sync = 'merge' }
    }
    if ($Sync -notin 'merge', 'rebase') { throw "-Sync reads '$Sync', not merge or rebase" }
    $default = $b.Default; $base = "origin/$default"
    $fetch = Invoke-Native git @('fetch', 'origin', $default)
    if ($fetch.Code -ne 0) { throw "cannot fetch origin/${default}: $($fetch.Text)" }
    $gained = @(Invoke-Read git @('--no-pager', 'log', '--oneline', "HEAD..$base"))
    Write-Output "the base gained since branching ($($gained.Count)):"
    foreach ($g in $gained) { Write-Output "  $g" }
    if ((Invoke-Native git @('merge-base', '--is-ancestor', $base, 'HEAD')).Code -eq 0) {
        Write-Output 'the base gained nothing - no sync, so step 4 pushes plainly, without a lease'
        return
    }
    $preSync = @(Invoke-Read git @('rev-parse', 'HEAD'))[0]
    $oldBase = @(Invoke-Read git @('merge-base', $preSync, $base))[0]
    $lease = 'none'

    if ($Sync -eq 'merge') {
        $m = Invoke-Native git @('merge', $base, '--no-edit')
        if ($m.Code -ne 0) {
            $unmerged = @(@(Invoke-Read git @('ls-files', '-u', '-z')) -join "`n" -split "`0" | Where-Object { $_ } |
                ForEach-Object { ($_ -split "`t", 2)[1] } | Sort-Object -Unique)
            if ($unmerged.Count) {
                throw "the merge of $base conflicts in:`n$(($unmerged | ForEach-Object { '  ' + $_ }) -join "`n")`nresolve each (an append-only file keeps both sides' entries), build, test, then git commit; or abandon it with git merge --abort. Before pushing, read-verify every resolution: each line either parent added since $oldBase is in the result, or the record says why not"
            }
            throw "git merge $base failed: $($m.Text)"
        }
        Write-Output "merged $base into ${branch}: $(@(Invoke-Read git @('rev-parse', 'HEAD'))[0])"
    }
    else {
        $ls = Invoke-Native git @('ls-remote', '--heads', 'origin', "refs/heads/$branch")
        if ($ls.Code -ne 0) { throw "cannot list $branch on origin: $($ls.Text)" }
        if ($ls.Out.Count) {
            $lease = ($ls.Out[0] -split '\s+')[0]
            $f = Invoke-Native git @('fetch', 'origin', "refs/heads/$branch")
            if ($f.Code -ne 0) { throw "cannot fetch origin's ${branch}: $($f.Text)" }
            if ((Invoke-Native git @('merge-base', '--is-ancestor', $lease, 'HEAD')).Code -ne 0) {
                throw "shared branch: origin's $branch holds a commit the local branch lacks ($lease) - the rewrite is yours to decide"
            }
            $based = Get-OpenPullRequestsBasedOn $b.Slug $branch
            if ($based -ne 0) { throw "shared branch: an open PR is based on $branch ($based of them) - the rewrite is yours to decide" }
        }
        $backup = "backup/$branch-$(Get-Date -Format yyyyMMddHHmmss)"
        $bk = Invoke-Native git @('branch', $backup, 'HEAD')
        if ($bk.Code -ne 0) { throw "cannot create the backup branch ${backup}: $($bk.Text)" }
        Write-Output "backup: $backup at $(@(Invoke-Read git @('rev-parse', '--short', 'HEAD'))[0])"
        $r = Invoke-Native git @('rebase', $base)
        if ($r.Code -ne 0) {
            throw "the rebase onto $base stopped: $($r.Text)`nresolve, build, test, then git rebase --continue; or abandon it with git rebase --abort, which returns to the tip $backup holds. The lease for step 4 is $lease"
        }
        Write-Output "rebased $branch onto ${base}: $(@(Invoke-Read git @('rev-parse', 'HEAD'))[0])"
    }

    Write-Output "pre-sync tip: $preSync$(if ($Sync -eq 'merge') { ' (HEAD^1)' } else { " ($backup)" }); old base: $oldBase"
    $both = @(Get-BothSidesChanged $oldBase $preSync $base)
    if ($both.Count) {
        Write-Output "both sides changed, read each against the branch's own additions (git diff $oldBase $base -- <file>):"
        foreach ($p in $both) { Write-Output "  $p" }
    }
    else { Write-Output 'no file both sides changed' }
    if ($Sync -eq 'rebase') { Write-Output "lease: $lease" }
}

if ($MyInvocation.InvocationName -ne '.') {
    $code = 1
    Invoke-LandEntry Invoke-SyncBranch $PSBoundParameters ([ref]$code)
    exit $code
}
