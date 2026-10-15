<#
.SYNOPSIS
    Delete a merged pull request's branch on origin and locally, the worktree that holds it
    under .claude/worktrees/, and the backup branches the landing named.
.DESCRIPTION
    /ouro:land step 11. Run from the repo's main checkout, with every tool shell moved out of the
    worktree first; the repository, the default branch and the protected prefixes come from the
    binding (.claude/ouro.toml, read through ouro-binding.py beside this script), and every gh call
    names that repository with -R.

    Every precondition is checked before the first deletion, so a refusal -- one `STOP: <why,
    and what to do>` line and exit 1 -- leaves everything as it was:
      - the pull request reads MERGED: on GitHub, deleting an open pull request's head branch
        closes it. Anything else, or a read that fails, stops;
      - the branch is the PR's head (or -Branch), not the default branch and under no protected
        prefix, and no open pull request is based on it -- deleting the branch another open PR
        targets closes or strands that PR, and a failed count keeps the branch;
      - the worktree that has the branch checked out, when there is one, sits under the main
        checkout's .claude/worktrees/ (one elsewhere is not this script's to remove), this
        script's own current directory is not inside it, and no other process has its current
        directory inside it: on Linux every /proc/<pid>/cwd is read, on macOS `lsof -a -d cwd`,
        and the stop names each PID with its directory -- move the shell out or let the job
        finish, then run this again. Windows is not scanned: the OS refuses to delete a directory
        a process holds, and the `left on disk` stop below reports it. The main checkout is not
        itself on the branch (switch it to the default branch first).
    Then, in order, each printing one `deleted:` or `removed:` line, and a step that fails stops
    with nothing further deleted:
      - origin's branch, only where origin still lists it (`git ls-remote --exit-code`, by its
        full refs/heads/ name: a bare name also matches <prefix>/<branch>, and a tag of the same
        name makes the delete ambiguous); exit 2 means the merge's --delete-branch already took
        it, and that is nothing to do;
      - the worktree, removed from the main checkout (`git worktree remove`, never --force), its
        directory then checked on the filesystem: a removal that hits a process whose current
        directory is inside the worktree deletes the tracked files, fails on the directory and
        deregisters the entry anyway, so `git worktree list` comes back clean while the directory
        is still on disk. That stop reads `left on disk`; move every shell out and delete the
        directory by hand. A worktree git refuses (an untracked file such as gate output, a
        submodule) stops with git's message;
      - the local branch, with `git branch -D`: a squash-merged branch is never "fully merged"
        to -d;
      - every `backup/<branch>-<timestamp>` branch /ouro:land step 2 or step 10 named.
.PARAMETER Pr
    The merged pull request whose branch is cleaned up.
.PARAMETER Branch
    The branch to delete. Default: the pull request's head branch, as GitHub names it.
.EXAMPLE
    pwsh -File <ouro>/bin/Remove-LandedBranch.ps1 -Pr 123
#>
param([int]$Pr, [string]$Branch)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Get-LandBinding.ps1')

# One 'pid N in <cwd>' per process whose current directory is $Root or inside it. Windows is not
# scanned. Invoke-BranchSweep.ps1 carries the same function: it is a single script.
function Get-ProcessesInside([string]$Root) {
    if ($IsLinux) {
        foreach ($dir in [System.IO.Directory]::GetDirectories('/proc')) {
            $id = [System.IO.Path]::GetFileName($dir)
            if ($id -notmatch '^\d+$') { continue }
            # A process of another user, or one that exited since the listing, has no readable link.
            $cwd = try { (Get-Item -LiteralPath "$dir/cwd" -ErrorAction SilentlyContinue).LinkTarget } catch { $null }
            if ($cwd -and (Test-Within $cwd $Root)) { "pid $id in $cwd" }
        }
    }
    elseif ($IsMacOS) {
        $id = $null
        foreach ($l in (& lsof -a -d cwd -Fpn 2>$null)) {
            if ($l -cmatch '^p(\d+)$') { $id = $Matches[1] }
            elseif ($l.StartsWith('n') -and (Test-Within $l.Substring(1) $Root)) { "pid $id in $($l.Substring(1))" }
        }
    }
}

function Invoke-RemoveLandedBranch {
    param([int]$Pr, [string]$Branch)
    if ($Pr -le 0) { throw '-Pr <number> is required' }
    $b = Get-LandBinding
    $view = Get-PullRequest $b.Slug $Pr @('state', 'headRefName')
    if ("$($view.state)" -ne 'MERGED') {
        throw "#$Pr is '$($view.state)', not MERGED - deleting an open pull request's head branch closes it; the branch stays"
    }
    if (-not $Branch) { $Branch = "$($view.headRefName)" }
    if (-not $Branch) { throw "#$Pr's head branch read empty - name it with -Branch" }
    if ($Branch -ceq $b.Default) { throw "$Branch is the default branch - not this script's to delete" }
    $protected = @($b.Prefixes | Where-Object { $Branch.StartsWith($_, [StringComparison]::Ordinal) })
    if ($protected.Count) { throw "$Branch has the protected prefix $($protected[0]) - not this script's to delete" }
    $based = Get-OpenPullRequestsBasedOn $b.Slug $Branch
    if ($based -ne 0) { throw "$based open PR(s) are based on $Branch - the branch stays" }

    # Every precondition before the first deletion, so a refusal leaves everything as it was.
    $worktrees = @(Get-Worktrees)
    $main = $worktrees[0]
    $wtRoot = Get-FullPath (Join-Path $main.Path '.claude/worktrees')
    $wt = @($worktrees | Select-Object -Skip 1 | Where-Object { $_.Branch -ceq $Branch }) | Select-Object -First 1
    if ($wt) {
        if (-not (Test-Within $wt.Path $wtRoot)) {
            throw "$Branch is checked out at $($wt.Path), outside $wtRoot - not this script's to remove; remove that worktree yourself, then run this again"
        }
        $here = Get-FullPath (@(Invoke-Read git @('rev-parse', '--show-toplevel'))[0])
        if (Test-Within $here $wt.Path) {
            throw "the current directory is inside $($wt.Path) - move every shell out of it (each holds its own current directory), then run this from the main checkout $($main.Path)"
        }
        $busy = @(Get-ProcessesInside $wt.Path)
        if ($busy) {
            throw "a process has its current directory inside $($wt.Path): $($busy -join '; ') - move the shell out of it or let the job finish, then run this again"
        }
    }
    if ($main.Branch -ceq $Branch) {
        throw "the main checkout $($main.Path) is on $Branch - switch it to $($b.Default) first, then run this again"
    }

    $ls = Invoke-Native git @('ls-remote', '--exit-code', '--heads', 'origin', "refs/heads/$Branch")
    switch ($ls.Code) {
        0 {
            $r = Invoke-Native git @('push', 'origin', '--delete', "refs/heads/$Branch")
            if ($r.Code -ne 0) { throw "cannot delete $Branch on origin: $($r.Text)" }
            Write-Output "deleted: origin $Branch"
        }
        2 { Write-Output "$Branch is already deleted on origin - nothing to do" }
        default { throw "cannot list $Branch on origin (exit $($ls.Code)): $($ls.Text)" }
    }

    if ($wt) {
        $r = Invoke-Native git @('-C', $main.Path, 'worktree', 'remove', $wt.Path)
        $listed = @(Get-Worktrees | Where-Object { $_.Path.Equals($wt.Path, $PathCompare) })
        if ($listed.Count) {
            throw "git refused to remove the worktree $($wt.Path) (exit $($r.Code)): $($r.Text) - an untracked file or a submodule keeps it; clear that, then run this again"
        }
        if (Test-Path -LiteralPath $wt.Path) {
            throw "left on disk: $($wt.Path) - git deregistered the worktree, but a process whose current directory is inside it kept the directory (worktree remove exit $($r.Code): $($r.Text)); move every shell out and delete the directory by hand, then run this again for the branch"
        }
        Write-Output "removed: worktree $($wt.Path)"
    }

    $local = Invoke-Native git @('rev-parse', '--verify', '--quiet', "refs/heads/$Branch")
    if ($local.Code -eq 0) {
        $r = Invoke-Native git @('-C', $main.Path, 'branch', '-D', $Branch)
        if ($r.Code -ne 0) { throw "cannot delete the local ${Branch}: $($r.Text)" }
        Write-Output "deleted: local $Branch"
    }
    else { Write-Output "no local $Branch - nothing to do" }
    foreach ($backup in @(Invoke-Read git @('for-each-ref', '--format=%(refname:lstrip=2)', "refs/heads/backup/$Branch-*"))) {
        $r = Invoke-Native git @('-C', $main.Path, 'branch', '-D', $backup)
        if ($r.Code -ne 0) { throw "cannot delete the backup branch ${backup}: $($r.Text)" }
        Write-Output "deleted: backup $backup"
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $code = 1
    Invoke-LandEntry Invoke-RemoveLandedBranch $PSBoundParameters ([ref]$code)
    exit $code
}
