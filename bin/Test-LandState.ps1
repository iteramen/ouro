<#
.SYNOPSIS
    Check that the branch in the working directory is one /ouro:land may land, and print what the
    landing needs to know: the binding's answers, the documents to read, and the commits to land.
.DESCRIPTION
    /ouro:land step 0's refusals and step 1. Run from the repo root on the feature branch; the
    repository, the default branch, the protected prefixes and the ship answers come from the
    binding (.claude/ouro.toml, read through ouro-binding.py beside this script).

    Refused, each a `STOP: <why, and what to do>` line and exit 1: no binding, or one that fails
    its check; a detached HEAD; the default branch itself; a branch, or the default branch, under
    a protected prefix (the repository's own release procedure owns those; the stop names the
    prefix and offers no way around); a `worktree-*` branch, the name a worktree made without -b
    sits on (rename it before pushing: renaming the remote branch after the PR exists closes the
    PR); staged or unstaged changes to tracked files; gh not logged in; nothing to land beyond the
    default branch. Untracked files do not stop the run: they ride no push, so they are listed for
    the session to add or leave.

    Printed, one line each: the branch and the default branch; `ship.sync` and `ship.merge`, the
    declared value or the default the landing takes where the binding declares none (merge to
    sync, squash to land), the way a `[ship].landing` preset answers both; `ship.review`, which
    step 6 switches on; `[authority].local`, the documents the session reads for a landing rule;
    `[overlays].land`, the checklists step 3 reads; then the commits to land, oldest first,
    against the local default branch or, where there is none, origin's.
.EXAMPLE
    pwsh -File <ouro>/bin/Test-LandState.ps1
#>
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Get-LandBinding.ps1')

function Invoke-TestLandState {
    $b = Get-LandBinding
    $branch = Get-CurrentBranch
    if ($branch -ceq $b.Default) { throw "the checkout is on $branch, the default branch - land from the feature branch" }
    foreach ($name in @($branch, $b.Default)) {
        $hit = @($b.Prefixes | Where-Object { $name.StartsWith($_, [StringComparison]::Ordinal) })
        if ($hit.Count) {
            throw "$name has the protected prefix $($hit[0]) - the repository's own release procedure owns it, never this landing"
        }
    }
    if ($branch -like 'worktree-*') {
        throw "$branch is the name a worktree made without -b sits on - rename it before pushing (git branch -m $branch <feature/name>); renaming the remote branch after the PR exists closes the PR"
    }
    $status = @(Invoke-Read git @('status', '--porcelain', '-z')) -join "`n" -split "`0" | Where-Object { $_ }
    $tracked = @($status | Where-Object { -not $_.StartsWith('?? ') })
    if ($tracked.Count) {
        throw "the working tree is not clean - commit or stash first:`n$(($tracked | ForEach-Object { '  ' + $_ }) -join "`n")"
    }
    $auth = Invoke-Native gh @('auth', 'status')
    if ($auth.Code -ne 0) { throw "gh is not logged in (gh auth status exit $($auth.Code)): $($auth.Text)" }

    Write-Output "branch: $branch"
    Write-Output "default: $($b.Default)"
    Write-Output $(if ($b.Sync) { "ship.sync: $($b.Sync)" } else { 'ship.sync: not declared - step 2 merges, the default' })
    Write-Output $(if ($b.Merge) { "ship.merge: $($b.Merge)" } else { 'ship.merge: not declared - step 10 squashes, the default' })
    $review = Get-BindingValue ship.review -Optional
    Write-Output $(if ($review) { "ship.review: $review" } else { 'ship.review: not declared' })
    foreach ($key in 'authority.local', 'overlays.land') {
        $raw = Get-BindingValue $key -Optional
        $list = @(if ($raw) { $raw | ConvertFrom-Json | Where-Object { $_ } })
        Write-Output "${key}: $(if ($list.Count) { $list -join ', ' } else { 'none' })"
    }
    Write-Output 'gh: logged in'
    foreach ($u in @($status | Where-Object { $_.StartsWith('?? ') })) {
        Write-Output "untracked: $($u.Substring(3)) - rides no push; add it to the branch or leave it"
    }
    $base = "refs/heads/$($b.Default)"
    if ((Invoke-Native git @('rev-parse', '--verify', '--quiet', $base)).Code -ne 0) { $base = "refs/remotes/origin/$($b.Default)" }
    $commits = @(Invoke-Read git @('--no-pager', 'log', '--oneline', '--reverse', "$base..HEAD"))
    if (-not $commits.Count) { throw "nothing to land: $branch has no commit beyond $base" }
    Write-Output "commits to land ($($commits.Count), against $base):"
    foreach ($c in $commits) { Write-Output "  $c" }
}

if ($MyInvocation.InvocationName -ne '.') {
    $code = 1
    Invoke-LandEntry Invoke-TestLandState $PSBoundParameters ([ref]$code)
    exit $code
}
