<#
.SYNOPSIS
    Watch the GitHub Actions runs that built a pull request's head, or a set of commits on the
    default branch, until they end, and judge each by its own conclusion.
.DESCRIPTION
    The driver's checks step. Run from the repo root; the repository and the
    default branch come from the binding (.claude/ouro.toml, read through ouro-binding.py beside
    this script), and every gh call names that repository with -R.

    With -Pr, the runs are the head's `pull_request` runs (`gh run list --commit <head> --event
    pull_request`); with -Sha, every run of each commit on the default branch (`--branch <default>
    --commit <sha>`). Either way the set watched is the newest run per workflow and event across
    the commits: the workflow by its id, not its name, which two workflow files can share; the
    newest by `createdAt`, the higher run id on a tie. A run a newer run of the same workflow and
    event supersedes is not judged -- that is the run a workflow's `concurrency` cancel ends as
    cancelled. The list is read up to six times, ten seconds apart, until it holds a run; then
    each run is watched to its end (`gh run watch`), the list is read again for any run a watch
    outlived -- one the PR's ready_for_review event starts, or a workflow that started seconds
    later -- and those are watched too, until a list brings no run not already watched. The last
    list is the verdict: each run must be completed with a conclusion of success, skipped or
    neutral. `gh pr checks --watch` is never used: while the head has no check yet, as when its
    only run is queued with no jobs, it exits 1 with `no checks reported`.

    Every refusal is one `STOP: <why, and what to do>` line and exit 1. With -Pr, no run after a
    minute is the zero-run case: nothing is known to have built the head,
    so the stop says to push (or re-push) it and come back. With -Sha, no run after a minute is
    reported and is no failure: a workflow with a `paths:` filter runs for a push only when that
    push's diff touches its list. `-Sha none` watches nothing and exits 0, for a batch in which
    nothing landed. A run that concluded outside success, skipped and neutral is named with its
    conclusion for the caller to judge; a run still in progress in the last list is a stop
    too, since it has no conclusion yet.
.PARAMETER Pr
    The pull request whose head's pull_request runs are watched.
.PARAMETER Sha
    Commits on the default branch whose runs are watched together, or `none` for nothing.
    Space-separated on the command line (`-Sha abc def`).
.EXAMPLE
    pwsh -File <ouro>/bin/Watch-PullRequestChecks.ps1 -Pr 123
.EXAMPLE
    pwsh -File <ouro>/bin/Watch-PullRequestChecks.ps1 -Sha 0a1b2c3 4d5e6f7
#>
param([int]$Pr, [string[]]$Sha = @())

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Get-LandBinding.ps1')

# The newest run per workflow and event across the commits, oldest run id first.
function Get-NewestRuns {
    param([string]$Slug, [string[]]$Commits, [string]$Branch, [string]$Event)
    $all = @()
    foreach ($c in $Commits) {
        $arguments = @('run', 'list', '-R', $Slug, '--commit', $c, '--limit', '100', '--json',
            'databaseId,workflowDatabaseId,workflowName,event,createdAt,status,conclusion')
        if ($Branch) { $arguments += @('--branch', $Branch) }
        if ($Event) { $arguments += @('--event', $Event) }
        $r = Invoke-Native gh $arguments
        if ($r.Code -ne 0) { throw "cannot list the runs of $c (exit $($r.Code)): $($r.Text)" }
        $all += @(($r.Out -join "`n") | ConvertFrom-Json)
    }
    $newest = @{}
    foreach ($run in $all) {
        $key = "$($run.workflowDatabaseId)/$($run.event)"
        $have = $newest[$key]
        # createdAt is RFC 3339 in UTC, so an ordinal comparison orders it.
        $later = $null -eq $have -or
            [string]::CompareOrdinal("$($run.createdAt)", "$($have.createdAt)") -gt 0 -or
            ("$($run.createdAt)" -eq "$($have.createdAt)" -and [long]$run.databaseId -gt [long]$have.databaseId)
        if ($later) { $newest[$key] = $run }
    }
    @($newest.Values | Sort-Object { [long]$_.databaseId })
}

function Invoke-WatchPullRequestChecks {
    param([int]$Pr, [string[]]$Sha = @())
    if (($Pr -gt 0) -eq ($Sha.Count -gt 0)) { throw 'name -Pr <number> or -Sha <commit>..., one of the two' }
    $b = Get-LandBinding
    if ($Pr -gt 0) {
        $head = "$((Get-PullRequest $b.Slug $Pr @('headRefOid')).headRefOid)"
        if (-not $head) { throw "#$Pr's head read empty" }
        $commits = @($head); $branch = $null; $event = 'pull_request'; $what = "#$Pr's head $head"
    }
    else {
        if ($Sha.Count -eq 1 -and $Sha[0] -eq 'none') { Write-Output 'nothing landed: nothing to watch'; return }
        $commits = $Sha; $branch = $b.Default; $event = $null; $what = $Sha -join ' '
    }
    $runs = @()
    for ($i = 1; $i -le 6; $i++) {
        $runs = @(Get-NewestRuns $b.Slug $commits $branch $event)
        if ($runs.Count) { break }
        if ($i -lt 6) { Wait-Poll 10 }
    }
    if (-not $runs.Count) {
        if ($Pr -gt 0) {
            throw "no pull_request run for $what after a minute - nothing is known to have built the head: push (or re-push) it so CI runs, then come back"
        }
        Write-Output "no run for any of $what after a minute: no workflow was triggered for them (path filters)"
        return
    }
    $watched = @{}
    while ($true) {
        $new = @($runs | Where-Object { -not $watched.ContainsKey("$($_.databaseId)") })
        if (-not $new.Count) { break }
        foreach ($run in $new) {
            Write-Output "watching run $($run.databaseId) ($($run.workflowName)/$($run.event))"
            $null = Invoke-Native gh @('run', 'watch', "$($run.databaseId)", '-R', $b.Slug, '--interval', '60')
            $watched["$($run.databaseId)"] = $true
        }
        $runs = @(Get-NewestRuns $b.Slug $commits $branch $event)
    }
    $bad = @()
    foreach ($run in $runs) {
        if ($run.status -ne 'completed') { throw "run $($run.databaseId) is still $($run.status), with no conclusion yet" }
        Write-Output "run $($run.databaseId) ($($run.workflowName)/$($run.event)): $($run.conclusion)"
        if ($run.conclusion -notin 'success', 'skipped', 'neutral') { $bad += "$($run.databaseId):$($run.conclusion)" }
    }
    if ($bad.Count) {
        throw "run(s) concluded outside success/skipped/neutral: $($bad -join ' ') - read the red run's log: a flake is re-run once, anything else is a fix"
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $code = 1
    Invoke-LandEntry Invoke-WatchPullRequestChecks $PSBoundParameters ([ref]$code)
    exit $code
}
