<#
.SYNOPSIS
    Merge a pull request the way the binding declares, with every guard the driver's merge step needs,
    and judge the result by GitHub's answer rather than gh's exit code.
.DESCRIPTION
    Run from the repo root of the clone that holds the branch; the repository, the default branch
    and the merge answer come from the binding (.claude/ouro.toml, read through ouro-binding.py
    beside this script), and every gh call names that repository with -R.

    Before the merge, in this order, each a `STOP: <why, and what to do>` line and exit 1:
      - the subject ends in `(#<Pr>)`, the subject the loop metrics count, and the body file is
        there and not empty;
      - the pull request's head, base and merge state are read, up to five times ten seconds
        apart while the state is UNKNOWN (routine while GitHub recomputes after the base moved);
      - it is not a draft; its base is the default branch (a PR retargeted since it was opened
        would merge into a branch nobody checked); it reads MERGEABLE CLEAN and nothing else --
        `gh pr merge` itself refuses only BLOCKED, BEHIND and DIRTY, so UNKNOWN and UNSTABLE
        would merge;
      - `gh pr checks` exits 0: 8 is a pending check, 1 with `no checks reported` is the zero-run
        case below, anything else is a failing check;
      - at least one pull_request run exists for the head, filtered on the server by commit and
        event: a CONFLICTING head dispatches no run at all, and a PR that became MERGEABLE because
        the default branch moved still has none, so nothing is known to have built it;
      - no review thread nobody answered: not resolved, opened by anyone but the PR's author, with
        no reply. A reply in the thread or resolving it answers; a commit pushed after it does not;
      - the head is fetched and its authors read, merge commits excluded, mailmap applied, by email
        with case folded. More than one author lands by merge commit under either answer, since a
        squash would reassign one person's work to whoever pressed the button; the exception is
        printed with the authors. Under -Linear yes it stops instead: a document the session read
        requires a linear history. Two addresses for one person are a .mailmap entry, not a
        second author.

    The merge pins the head the checks were read for (`--match-head-commit`), so a push after the
    read is refused rather than landed; deletes the branch only when no open pull request is based
    on it (a failed count keeps the branch); and never rebase-merges. Afterwards the pull request
    is read back: MERGED with a merge commit is the answer, printed as `#<Pr> merged as <sha>`.
    Anything else stops: `Base branch was modified` means the default branch moved between the
    read and the merge and nothing merged -- sync the branch again (Sync-Branch.ps1) and run this
    again; a refused merge commit is the repository disallowing merge commits or a ruleset
    requiring linear history, and the stop names the fix: that setting or the binding, or a
    mailmap entry where the addresses are one person's. The run never falls back to a squash. A
    read that fails after the merge stops without calling the PR merged or not merged.
.PARAMETER Pr
    The pull request to merge.
.PARAMETER Subject
    The merge commit's subject, `type(scope): summary (#<Pr>)`: it must end in `(#<Pr>)`.
.PARAMETER BodyFile
    A file holding the merge commit's body: the branch-wide summary, the logical changes as
    bullets, the follow-ups linked. Must exist and be non-empty.
.PARAMETER Linear
    `yes` where a document the session read ([authority].local) requires a linear history, else
    `no`. Required: the script cannot read the documents. With `yes`, a range with more than one
    author stops before any merge.
.PARAMETER Merge
    `squash` or `merge`. Default: the binding's `ship.merge`, or `squash` where the binding
    declares none (printed).
.PARAMETER ExpectedHead
    Optional full commit checked by the caller. A different current pull-request head stops
    before any merge; the same head remains pinned with --match-head-commit.
.EXAMPLE
    pwsh -File <ouro>/bin/Merge-PullRequest.ps1 -Pr <n> -Subject "fix(x): y (#<n>)" -BodyFile body.md -Linear no
#>
param([int]$Pr, [string]$Subject, [string]$BodyFile, [string]$Linear, [string]$Merge, [string]$ExpectedHead)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Get-LandBinding.ps1')

function Invoke-MergePullRequest {
    param([int]$Pr, [string]$Subject, [string]$BodyFile, [string]$Linear, [string]$Merge, [string]$ExpectedHead)
    if ($Pr -le 0) { throw '-Pr <number> is required' }
    if (-not $Subject) { throw '-Subject is required: "type(scope): summary (#<Pr>)"' }
    if (-not $Subject.TrimEnd().EndsWith("(#$Pr)")) {
        throw "the subject reads '$Subject' - it must end in (#$Pr), the subject the loop metrics count"
    }
    if (-not $BodyFile) { throw '-BodyFile is required: the merge body, written with the file-write tool' }
    if (-not (Test-Path -LiteralPath $BodyFile -PathType Leaf) -or (Get-Item -LiteralPath $BodyFile).Length -eq 0) {
        throw "BodyFile ($BodyFile) is empty or missing - write the merge body with the file-write tool"
    }
    if ($Linear -notin 'yes', 'no') {
        throw "-Linear reads '$Linear', not yes or no: yes where a document [authority].local names requires a linear history"
    }
    if ($ExpectedHead -and $ExpectedHead -cnotmatch '^[0-9a-f]{40}$') { throw '-ExpectedHead must be a full commit object id' }
    $b = Get-LandBinding
    if (-not $Merge) {
        if ($b.Merge) { $Merge = $b.Merge }
        else { Write-Output 'ship.merge: not declared - squash, the default'; $Merge = 'squash' }
    }
    if ($Merge -notin 'squash', 'merge') { throw "-Merge reads '$Merge', not squash or merge" }
    $how = if ($Merge -eq 'squash') { '--squash' } else { '--merge' }
    Write-Output "merge answer: $Merge"

    $fields = @('headRefOid', 'headRefName', 'baseRefName', 'mergeable', 'mergeStateStatus', 'isDraft')
    $view = $null; $state = ''
    for ($i = 1; $i -le 5; $i++) {
        $view = Get-PullRequest $b.Slug $Pr $fields
        $state = "$($view.mergeable) $($view.mergeStateStatus)"
        if ($state -notmatch 'UNKNOWN') { break }
        if ($i -lt 5) { Wait-Poll 10 }
    }
    $head = "$($view.headRefOid)"; $branch = "$($view.headRefName)"
    if (-not $head) { throw "cannot read #$Pr's head, base and state" }
    if ($ExpectedHead -and $head -cne $ExpectedHead) { throw "#$Pr's head moved from the checked $ExpectedHead to $head - nothing is merged" }
    if ($view.isDraft -ne $false) {
        throw "#$Pr is still a draft, or its draft state did not read - mark it ready (gh pr ready) once the review has converged, then run this again"
    }
    if ("$($view.baseRefName)" -ne $b.Default) { throw "#$Pr's base reads '$($view.baseRefName)', not $($b.Default)" }
    if ($state -ne 'MERGEABLE CLEAN') { throw "#$Pr is '$state', not MERGEABLE CLEAN" }

    $checks = Invoke-Native gh @('pr', 'checks', "$Pr", '-R', $b.Slug)
    if ($checks.Code -ne 0) {
        throw "gh pr checks exits $($checks.Code), not 0 - 8 is a pending check: let Watch-PullRequestChecks.ps1 finish and run this again; 1 with 'no checks reported' means nothing built the head; any other failing check is a fix round: $($checks.Text)"
    }
    $r = Invoke-Native gh @('run', 'list', '-R', $b.Slug, '--commit', $head, '--event', 'pull_request', '--json', 'databaseId')
    $count = if ($r.Code -eq 0) { @(($r.Out -join "`n") | ConvertFrom-Json).Count } else { -1 }
    if ($count -lt 1) {
        throw "no pull_request run reads for $head - nothing built it: push (or re-push) the head so CI runs, then come back"
    }
    $open = @(Get-OpenReviewThreads $b.Slug $Pr)
    if ($open.Count) {
        throw "#$Pr has a review thread nobody answered - reply in it or resolve it:`n$($open -join "`n")"
    }

    $fetch = Invoke-Native git @('fetch', '-q', 'origin', $b.Default, "refs/pull/$Pr/head")
    if ($fetch.Code -ne 0) { throw "cannot fetch origin/$($b.Default) and #$Pr's head: $($fetch.Text)" }
    if ((Invoke-Native git @('cat-file', '-e', "$head^{commit}")).Code -ne 0) { throw "#$Pr's head $head did not fetch" }
    $range = "origin/$($b.Default)..$head"
    $emails = @(Invoke-Read git @('log', '--no-merges', '--format=%aE', $range) |
        ForEach-Object { $_.ToLowerInvariant() } | Sort-Object -Unique)
    if (-not $emails.Count) { throw "no author read from $range - the range is empty or holds only merge commits" }
    $fix = 'change that setting or the binding'
    if ($emails.Count -gt 1) {
        $authors = @(Invoke-Read git @('log', '--no-merges', '--format=%aN <%aE>', $range) | Sort-Object -Unique)
        if ($Linear -eq 'yes') {
            throw "#$Pr's range has more than one author, so it would land by merge commit, and a document step 0 read requires a linear history - add a .mailmap entry where the addresses are one person's, or the owner changes the document:`n$($authors -join "`n")"
        }
        if ($how -eq '--squash') {
            Write-Output "authorship exception: #$Pr's range has more than one author - it lands by merge commit, not squash:"
            $authors | Write-Output
        }
        $fix = 'allow merge commits, or, where the addresses are one person''s, add a .mailmap entry for them'
        $how = '--merge'
    }

    $arguments = @('pr', 'merge', "$Pr", '-R', $b.Slug, $how, '--match-head-commit', $head)
    $based = try { Get-OpenPullRequestsBasedOn $b.Slug $branch } catch { -1 }
    if ($based -eq 0) { $arguments += '--delete-branch' }
    elseif ($based -lt 0) { Write-Output "the branch stays: the count of PRs based on $branch failed" }
    else { Write-Output "the branch stays: $based open PR(s) are based on $branch" }
    $arguments += @('--subject', $Subject, '--body-file', $BodyFile)
    $merged = Invoke-Native gh $arguments
    if ($merged.Text) { Write-Output $merged.Text }

    $after = try { Get-PullRequest $b.Slug $Pr @('state', 'mergeCommit') }
             catch { throw "cannot read #$Pr after the merge - check it by hand: $($_.Exception.Message)" }
    $oid = if ($after.mergeCommit) { "$($after.mergeCommit.oid)" } else { '' }
    if ("$($after.state)" -ne 'MERGED' -or -not $oid) {
        throw "#$Pr reads '$($after.state) $oid', not MERGED with a merge commit - after 'Base branch was modified' the default branch moved and nothing merged: sync the branch (Sync-Branch.ps1), then run this again; under --merge, a refusal (gh's message is above) is the repository disallowing merge commits or a ruleset requiring linear history: $fix; never land it as a squash instead"
    }
    Write-Output "#$Pr merged as $oid"
}

if ($MyInvocation.InvocationName -ne '.') {
    $code = 1
    Invoke-LandEntry Invoke-MergePullRequest $PSBoundParameters ([ref]$code)
    exit $code
}
