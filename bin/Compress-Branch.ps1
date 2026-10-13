<#
.SYNOPSIS
    Compress a feature branch's fix-up commits into its arc before an explicit merge: the checks
    and the backup before the session recomposes the history, and the identity checks and the
    leased push after.
.DESCRIPTION
    /ouro:land step 10, under a merge answer of merge, for a range carrying fix-up, review-round or
    work-in-progress commits. Run from the repo root on the feature branch; the repository and the
    default branch come from the binding (.claude/ouro.toml, read through ouro-binding.py beside
    this script), and every gh call names that repository with -R. Two subcommands, around the one
    step that is the session's: composing the arc.

      prepare  Fetches origin's default branch and reads origin's tip of the branch, the lease.
               Stops when origin has no branch (push it and open the PR first); when origin's
               branch holds a commit the local branch lacks, or an open pull request is based on
               the branch (the two readable signs of a branch someone else is working on, which a
               lease does not cover: the rewrite is the user's to decide); when the local branch
               has commits origin lacks (push them and let the checks run, or drop them, first:
               the compression rewrites exactly the head the pull request shows); and when the
               range from the default branch's remote tip to HEAD, merge commits excluded, mailmap
               applied, has more than one author email, case folded -- compression never folds one
               person's commits into another's, so such a range lands as it is, uncompressed.
               Then it names a backup branch at the current tip and prints `backup:`, `lease:`,
               `base:` and `author:` for the session, which composes the arc on top of the base:
               `git reset <base>` leaves the changes in the working tree, and the session commits
               them back in groups, each with a clean subject and `--author="<the author>"`.
      push     With -Backup, the branch `prepare` named. Stops when the working tree is not clean
               (the arc is not all committed); when HEAD's tree differs from the backup's (the
               rewrite changed more than the history: `git reset --hard <backup>` goes back, and
               nothing is pushed); when HEAD is not built on the base; and when any commit of the
               new range has an author other than the range's one author (recommit it with
               --author). Then it pushes with `--force-with-lease=refs/heads/<branch>:<lease>`,
               the lease being the backup's commit, which `prepare` checked was origin's tip: a
               push someone made since is refused, not overwritten. The checks must pass on the
               new head before the merge step runs: back to /ouro:land step 7.

    Every refusal is a `STOP: <why, and what to do>` line and exit 1. The plain force flag is never
    the push.
.PARAMETER Command
    `prepare` or `push`, first on the line.
.PARAMETER Backup
    For push: the backup branch `prepare` printed.
.EXAMPLE
    pwsh -File <ouro>/bin/Compress-Branch.ps1 prepare
.EXAMPLE
    pwsh -File <ouro>/bin/Compress-Branch.ps1 push -Backup backup/feat/x-20260101120000
#>
param([Parameter(Position = 0)][string]$Command, [string]$Backup)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Get-LandBinding.ps1')

# The distinct author emails of a range, case folded, and the range's author line.
function Get-RangeAuthors([string]$Range) {
    $emails = @(Invoke-Read git @('log', '--no-merges', '--format=%aE', $Range) | ForEach-Object { $_.ToLowerInvariant() } | Sort-Object -Unique)
    [pscustomobject]@{
        Emails = $emails
        Names  = @(Invoke-Read git @('log', '--no-merges', '--format=%aN <%aE>', $Range) | Sort-Object -Unique)
        Author = if ($emails.Count) { @(Invoke-Read git @('log', '-1', '--no-merges', '--format=%aN <%aE>', $Range))[0] } else { '' }
    }
}

function Invoke-CompressPrepare($b, [string]$Branch) {
    $f = Invoke-Native git @('fetch', 'origin', $b.Default)
    if ($f.Code -ne 0) { throw "cannot fetch origin/$($b.Default): $($f.Text)" }
    $ls = Invoke-Native git @('ls-remote', '--heads', 'origin', "refs/heads/$Branch")
    if ($ls.Code -ne 0) { throw "cannot list $Branch on origin: $($ls.Text)" }
    if (-not $ls.Out.Count) { throw "origin has no $Branch - push it (step 4) and open the PR first" }
    $lease = ($ls.Out[0] -split '\s+')[0]
    $f = Invoke-Native git @('fetch', 'origin', "refs/heads/$Branch")
    if ($f.Code -ne 0) { throw "cannot fetch origin's ${Branch}: $($f.Text)" }
    if ((Invoke-Native git @('merge-base', '--is-ancestor', $lease, 'HEAD')).Code -ne 0) {
        throw "shared branch: origin's $Branch holds a commit the local branch lacks ($lease) - the rewrite is yours to decide"
    }
    $head = @(Invoke-Read git @('rev-parse', 'HEAD'))[0]
    if ($lease -ne $head) { throw "the local $Branch has commits origin lacks - push them (step 4) and let the checks run, or drop them, first" }
    $based = Get-OpenPullRequestsBasedOn $b.Slug $Branch
    if ($based -ne 0) { throw "shared branch: an open PR is based on $Branch ($based of them) - the rewrite is yours to decide" }
    $base = @(Invoke-Read git @('merge-base', "origin/$($b.Default)", 'HEAD'))[0]
    $authors = Get-RangeAuthors "$base..HEAD"
    if (-not $authors.Emails.Count) { throw "no author read from $base..HEAD - nothing to compress" }
    if ($authors.Emails.Count -gt 1) { throw "more than one author - the range lands as it is, uncompressed:`n$($authors.Names -join "`n")" }
    $name = "backup/$Branch-$(Get-Date -Format yyyyMMddHHmmss)"
    $bk = Invoke-Native git @('branch', $name, 'HEAD')
    if ($bk.Code -ne 0) { throw "cannot create the backup branch ${name}: $($bk.Text)" }
    Write-Output "backup: $name"
    Write-Output "lease: $lease"
    Write-Output "base: $base"
    Write-Output "author: $($authors.Author)"
    Write-Output "compose the arc on $base (git reset $base, then commit the changes back in groups, each with --author=`"$($authors.Author)`"), then run: push -Backup $name"
}

function Invoke-CompressPush($b, [string]$Branch, [string]$Backup) {
    if (-not $Backup) { throw 'push takes -Backup <the backup branch prepare printed>' }
    $bk = Invoke-Native git @('rev-parse', '--verify', '--quiet', "refs/heads/$Backup")
    if ($bk.Code -ne 0) { throw "no branch $Backup - it is the backup prepare printed" }
    $lease = $bk.Out[0]
    if (@(Invoke-Read git @('status', '--porcelain')).Count) { throw 'the working tree is not clean - the arc is not all committed' }
    $tree = @(Invoke-Read git @('rev-parse', 'HEAD^{tree}'))[0]
    if ($tree -ne @(Invoke-Read git @('rev-parse', "$Backup^{tree}"))[0]) {
        throw "the compressed tree differs from $Backup - nothing is pushed; go back with git reset --hard $Backup"
    }
    $base = @(Invoke-Read git @('merge-base', "origin/$($b.Default)", $Backup))[0]
    if ((Invoke-Native git @('merge-base', '--is-ancestor', $base, 'HEAD')).Code -ne 0) { throw "HEAD is not built on $base" }
    $want = Get-RangeAuthors "$base..$Backup"
    $have = Get-RangeAuthors "$base..HEAD"
    if (-not $have.Emails.Count) { throw "no commit between $base and HEAD - the arc is not committed" }
    if (@($have.Emails | Where-Object { $_ -notin $want.Emails }).Count) {
        throw "a compressed commit's author is not $($want.Author) - recommit it with --author:`n$($have.Names -join "`n")"
    }
    $p = Invoke-Native git @('push', "--force-with-lease=refs/heads/${Branch}:$lease", 'origin', $Branch)
    if ($p.Code -ne 0) { throw "the leased push was refused: $($p.Text)`norigin's $Branch moved since $lease; nothing is overwritten" }
    Write-Output "compressed and pushed $Branch as $(@(Invoke-Read git @('rev-parse', 'HEAD'))[0]) - back to step 7: the checks must pass on this head before the merge"
}

function Invoke-CompressBranch {
    param([string]$Command, [string]$Backup)
    if ($Command -notin 'prepare', 'push') { throw 'the first argument is the subcommand: prepare, or push -Backup <branch>' }
    $b = Get-LandBinding
    $branch = Get-CurrentBranch
    if ($branch -ceq $b.Default) { throw "the checkout is on $branch, the default branch - compress from the feature branch" }
    $hit = @($b.Prefixes | Where-Object { $branch.StartsWith($_, [StringComparison]::Ordinal) })
    if ($hit.Count) { throw "$branch has the protected prefix $($hit[0]) - the repository's own release procedure owns it" }
    if ($Command -eq 'prepare') { Invoke-CompressPrepare $b $branch } else { Invoke-CompressPush $b $branch $Backup }
}

if ($MyInvocation.InvocationName -ne '.') {
    $code = 1
    Invoke-LandEntry Invoke-CompressBranch $PSBoundParameters ([ref]$code)
    exit $code
}
