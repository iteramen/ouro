<#
.SYNOPSIS
    Test of Watch-PullRequestChecks.ps1 over the gh stand-in.
.DESCRIPTION
    The script is dot-sourced and its function called per row, with the pause between re-reads
    counted instead of slept; the fixture beside this file holds the stand-in and the scratch
    repository.

    Pinned: with -Pr, the head is read from the pull request and only its pull_request runs are
    listed; the set watched is the newest run per workflow and event, so a run a newer run of the
    same workflow supersedes is neither watched nor judged; a run that appears after the first
    watches is watched too, and the loop ends when a list brings nothing new; the last list is the
    verdict, each run by its own conclusion: success, skipped and neutral pass, a failure stops
    with the run and its conclusion, and a run still in progress stops. No run after six lists,
    fifty seconds apart in all, stops with -Pr (nothing built the head) and is reported with -Sha
    (path filters); `-Sha none` watches nothing. With -Sha the runs of every commit are listed on
    the default branch and the newest per workflow and event is taken across them all. Neither
    parameter, or both, stops before any call; a list that fails stops; a tree with no binding
    stops before any call. The child rows pin the entry point: exit 0 and the verdict lines on
    the happy path, exit 1 with the STOP line otherwise, and the help block.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'LandScripts.Fixture.ps1')
$Script = Find-Script 'Watch-PullRequestChecks.ps1'
. $Script
# The pause between re-reads is counted, not slept.
$script:polls = 0
function Wait-Poll { param([int]$Seconds) $script:polls += $Seconds }

function Run([long]$Id, [long]$Workflow, [string]$Event, [string]$Created, [string]$Status = 'completed', [string]$Conclusion = 'success') {
    [ordered]@{ databaseId = $Id; workflowDatabaseId = $Workflow; workflowName = "wf$Workflow"; event = $Event
        createdAt = $Created; status = $Status; conclusion = $Conclusion }
}
$H = 'a' * 40; $H2 = 'b' * 40
$t1 = '2026-10-12T10:00:00Z'; $t2 = '2026-10-12T10:05:00Z'

$w = New-World 'w'
Push-Location -LiteralPath $w.Main
try {
    Write-Host 'the newest run per workflow and event is watched and judged'
    Set-Gh @{ 'pr view 7' = Answer @{ headRefOid = $H }
        "run list --commit $H" = Answer @((Run 11 1 pull_request $t1), (Run 12 1 pull_request $t2), (Run 13 2 pull_request $t1 completed skipped))
        'run watch 12' = @{ Exit = 0; Out = '' }; 'run watch 13' = @{ Exit = 0; Out = '' } }
    $r = Invoke-Step { Invoke-WatchPullRequestChecks -Pr 7 }
    Assert-Equal '' $r.Stop 'the run ends without a stop'
    Assert-Line $r.Gh 'pr view 7 -R o/n --json headRefOid' 'the head is read from the pull request, naming the repository'
    Assert-Line $r.Gh "run list -R o/n --commit $H --limit 100 --json databaseId,workflowDatabaseId,workflowName,event,createdAt,status,conclusion --event pull_request" 'the runs are listed by commit and event, on the server'
    Assert-Line $r.Gh 'run watch 12 -R o/n --interval 60' 'the newest run of workflow 1 is watched'
    Assert-Line $r.Gh 'run watch 13 -R o/n --interval 60' 'the run of workflow 2 is watched'
    Assert-NoMatch 'run watch 11' ($r.Gh -join "`n") 'the superseded run of workflow 1 is not watched'
    Assert-Line $r.Lines 'run 12 (wf1/pull_request): success' 'the verdict names the newest run with its conclusion'
    Assert-Line $r.Lines 'run 13 (wf2/pull_request): skipped' 'a skipped run passes'
    Assert-NoMatch 'run 11 ' ($r.Lines -join "`n") 'the superseded run is not judged'
    Assert-Equal 2 @($r.Gh | Where-Object { $_ -like 'run list*' }).Count 'the list is read once more after the watches, and brings nothing new'

    Write-Host 'a run that appears after the first watches is watched too'
    Set-Gh @{ 'pr view 7' = Answer @{ headRefOid = $H }
        "run list --commit $H" = Seq @((Answer @(Run 11 1 pull_request $t1)), (Answer @((Run 11 1 pull_request $t1), (Run 14 3 pull_request $t2))))
        'run watch 11' = @{ Exit = 0; Out = '' }; 'run watch 14' = @{ Exit = 0; Out = '' } }
    $r = Invoke-Step { Invoke-WatchPullRequestChecks -Pr 7 }
    Assert-Equal '' $r.Stop 'the run ends without a stop'
    Assert-Equal 'run watch 11 -R o/n --interval 60,run watch 14 -R o/n --interval 60' (@($r.Gh | Where-Object { $_ -like 'run watch*' }) -join ',') 'both runs are watched, in the order they appeared'
    Assert-Equal 3 @($r.Gh | Where-Object { $_ -like 'run list*' }).Count 'three lists: the first, one after each round of watches'
    Assert-Line $r.Lines 'run 14 (wf3/pull_request): success' 'the late run is judged'

    Write-Host 'no run after a minute stops with -Pr: nothing built the head'
    Set-Gh @{ 'pr view 7' = Answer @{ headRefOid = $H }; "run list --commit $H" = Answer @() }
    $script:polls = 0
    $r = Invoke-Step { Invoke-WatchPullRequestChecks -Pr 7 }
    Assert-Match "^no pull_request run for #7's head $H after a minute - nothing is known to have built the head: push \(or re-push\) it" $r.Stop 'the stop names the zero-run case and what to do'
    Assert-Equal 6 @($r.Gh | Where-Object { $_ -like 'run list*' }).Count 'the list is read six times'
    Assert-Equal 50 $script:polls 'five pauses of ten seconds between them'

    Write-Host 'a failed conclusion stops, naming the run; a run still in progress stops'
    Set-Gh @{ 'pr view 7' = Answer @{ headRefOid = $H }
        "run list --commit $H" = Answer @((Run 11 1 pull_request $t1 completed failure), (Run 13 2 pull_request $t1 completed neutral))
        'run watch 11' = @{ Exit = 0; Out = '' }; 'run watch 13' = @{ Exit = 0; Out = '' } }
    $r = Invoke-Step { Invoke-WatchPullRequestChecks -Pr 7 }
    Assert-Match '^run\(s\) concluded outside success/skipped/neutral: 11:failure - read the red run' $r.Stop 'the stop names the red run and its conclusion'
    Assert-Line $r.Lines 'run 13 (wf2/pull_request): neutral' 'a neutral run passes and is still listed'
    Set-Gh @{ 'pr view 7' = Answer @{ headRefOid = $H }
        "run list --commit $H" = Answer @(Run 11 1 pull_request $t1 in_progress $null)
        'run watch 11' = @{ Exit = 0; Out = '' } }
    $r = Invoke-Step { Invoke-WatchPullRequestChecks -Pr 7 }
    Assert-Equal 'run 11 is still in_progress, with no conclusion yet' $r.Stop 'a run without a conclusion in the last list stops'

    Write-Host 'with -Sha, every commit is listed on the default branch and the newest per workflow is taken across them'
    Set-Gh @{ "run list --commit $H" = Answer @(Run 11 1 push $t1)
        "run list --commit $H2" = Answer @((Run 15 1 push $t2), (Run 16 2 push $t1 completed neutral))
        'run watch 15' = @{ Exit = 0; Out = '' }; 'run watch 16' = @{ Exit = 0; Out = '' } }
    $r = Invoke-Step { Invoke-WatchPullRequestChecks -Sha $H, $H2 }
    Assert-Equal '' $r.Stop 'the run ends without a stop'
    Assert-Line $r.Gh "run list -R o/n --commit $H --limit 100 --json databaseId,workflowDatabaseId,workflowName,event,createdAt,status,conclusion --branch master" 'each commit is listed on the default branch, with no event filter'
    Assert-NoMatch 'run watch 11' ($r.Gh -join "`n") 'the older run of workflow 1, on the first commit, is superseded by the second commit''s'
    Assert-Line $r.Gh 'run watch 15 -R o/n --interval 60' 'the newest run of workflow 1 is watched'
    Assert-Line $r.Gh 'run watch 16 -R o/n --interval 60' 'the run of workflow 2 is watched'
    Assert-NoMatch 'pr view' ($r.Gh -join "`n") 'no pull request is read'

    Write-Host 'no run after a minute is reported, not a stop, with -Sha; -Sha none watches nothing'
    Set-Gh @{ "run list --commit $H" = Answer @() }
    $script:polls = 0
    $r = Invoke-Step { Invoke-WatchPullRequestChecks -Sha $H }
    Assert-Equal '' $r.Stop 'no stop'
    Assert-Line $r.Lines "no run for any of $H after a minute: no workflow was triggered for them (path filters)" 'the absence is reported with its usual cause'
    Assert-Equal 50 $script:polls 'after the same six reads'
    $r = Invoke-Step { Invoke-WatchPullRequestChecks -Sha none }
    Assert-Equal 'nothing landed: nothing to watch' ($r.Lines -join ';') 'none is nothing to watch'
    Assert-Equal 0 $r.Gh.Count 'and no call is made'

    Write-Host 'neither parameter, or both, stops before any call; a failed list stops'
    $r = Invoke-Step { Invoke-WatchPullRequestChecks }
    Assert-Equal 'name -Pr <number> or -Sha <commit>..., one of the two' $r.Stop 'neither parameter stops'
    $r = Invoke-Step { Invoke-WatchPullRequestChecks -Pr 7 -Sha $H }
    Assert-Equal 'name -Pr <number> or -Sha <commit>..., one of the two' $r.Stop 'both parameters stop'
    Assert-Equal 0 $r.Gh.Count 'before any gh call'
    Set-Gh @{ 'pr view 7' = Answer @{ headRefOid = $H }; "run list --commit $H" = Fail 'boom' }
    $r = Invoke-Step { Invoke-WatchPullRequestChecks -Pr 7 }
    Assert-Match "^cannot list the runs of $H \(exit 1\): boom" $r.Stop 'a list that fails stops with its output'
}
finally { Pop-Location }

Write-Host 'a tree with no binding stops before any call'
$bare = (New-Item -ItemType Directory -Path (Join-Path $Scratch 'unbound')).FullName
$null = git init -q $bare 2>&1
Push-Location -LiteralPath $bare
try {
    Set-Gh @{ 'pr view 7' = Answer @{ headRefOid = $H } }
    $r = Invoke-Step { Invoke-WatchPullRequestChecks -Pr 7 }
    Assert-Match '^ouro-binding.py check failed \(exit 1\): .*no binding' $r.Stop 'the binding check stops the run'
    Assert-Equal 0 $r.Gh.Count 'and no gh call is made'
}
finally { Pop-Location }

Write-Host 'the entry point: a child process, the exit codes and the STOP line'
Set-Gh @{ 'pr view 7' = Answer @{ headRefOid = $H }
    "run list --commit $H" = Answer @(Run 12 1 pull_request $t2)
    'run watch 12' = @{ Exit = 0; Out = '' } }
$c = Invoke-Child $Script @('-Pr', '7') $w.Main
Assert-Equal 0 $c.Code 'the happy path exits 0'
Assert-Line $c.Lines 'run 12 (wf1/pull_request): success' 'and prints the verdict'
$c = Invoke-Child $Script @() $w.Main
Assert-Equal 1 $c.Code 'no parameter exits 1'
Assert-Equal 'STOP: name -Pr <number> or -Sha <commit>..., one of the two' ($c.Lines -join ';') 'with the STOP line alone'
Set-Gh @{ 'pr view 7' = Answer @{ headRefOid = $H }
    "run list --commit $H" = Answer @(Run 12 1 pull_request $t2 completed failure)
    'run watch 12' = @{ Exit = 0; Out = '' } }
$c = Invoke-Child $Script @('-Pr', '7') $w.Main
Assert-Equal 1 $c.Code 'a red run exits 1'
Assert-Line $c.Lines 'STOP: run(s) concluded outside success/skipped/neutral: 12:failure' 'with the STOP line' -Prefix
$help = Get-Help ([WildcardPattern]::Escape($Script))
Assert-Match 'Watch the GitHub Actions runs' ($help.Synopsis) 'the script exposes its help'
Complete-Suite 'Watch-PullRequestChecks'
