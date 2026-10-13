<#
.SYNOPSIS
    /ouro:land-batch as one script: squash a set of reviewed pull requests locally, validate the
    composition, then land it on GitHub one pull request at a time, or by one push, through
    subcommands over the batch files in the git directory.
.DESCRIPTION
    Run from the repo root of the checkout the batch runs in (the main checkout on the default
    branch, or the batch worktree), where the batch files live in the git directory:
    land-batch.tsv (one row per squashed pull request: PR, BRANCH, HEAD_SHA, SQUASH, in merge
    order), land-batch.route (the lines that send the set to the push drill), land-batch.landed
    (PR and merge commit per landed pull request) and land-batch.stop (why the batch stopped, which
    refuses every later `land`). The repository, the default branch and the merge answer come from
    the binding (.claude/ouro.toml, read through ouro-binding.py beside this script); every gh call
    names that repository with -R. Every refusal is a `STOP: <why, and what to do>` line and exit 1.

    The subcommands, in the order the drill runs them:

      check     Step 0. Reads `ship.merge` and what the default branch accepts (its rulesets,
                classic protection, a merge queue, squash merges), prints each reading, and ends
                with one `verdict:` line: the batch is refused (squash merges refused, a merge
                answer of merge or none, a merge queue), or goes to /ouro:land per pull request
                (strict checks, for a set of two or more; a rules read that failed, since landing
                blind is not safe), or may land through `land` only (required pull requests or
                checks), or through `land` or `push`. A 403 naming the plan is nothing to find.
      worktree  The batch worktree, when the main checkout is on another branch: checks
                .claude/worktrees/ is ignored, adds .claude/worktrees/land-batch on the local
                default branch and fast-forwards it to origin's.
      start     Step 1. Removes any previous batch's files.
      add       Step 2, one pull request: -Pr, -Branch and -Message (a file holding the commit
                message, subject ending in `(#<Pr>)`, line two blank). Checks the head is origin's
                tip of the branch and the base is the default branch or a branch already in the
                batch; reads the range's one author (two addresses are a stop: a squash would
                reassign one person's work); squashes origin's branch onto HEAD and commits with
                that author; records the row, and the stacked route when the base is not the
                default branch. A conflict stops with the squash left in the tree for step 3.
      stacked   Step 3, a pull request stacked on a branch already in the batch: dry-runs its own
                diff against the batch (`git merge-tree`), discards the conflicted squash, applies
                the diff three-way, commits with the range's author, records the row and the route.
      record    Step 3, a conflict resolved by hand: with every path resolved, commits the staged
                resolution with the message, or takes a commit already made with that subject when
                its author is the range's, and records the row and the route.
      amend     Step 4, after a squash was amended: records the route with -Note.
      route     Step 5. Adds a route line for each path two or more squashes change that carries a
                `merge=` attribute, then prints `route: 5a` or `ROUTE: 5b -` with the lines.
      land      Step 5a, one pull request per call, the next recorded one not yet landed: refuses
                while the route file holds a line or the batch has stopped; before the first merge
                checks HEAD is the last recorded squash holding every recorded one, and every pull
                request reads MERGEABLE and not a draft; then checks origin's tip is the previously
                landed commit, the pull request is based on the default branch and MERGEABLE CLEAN,
                no pull request still to land has an unanswered review thread, and squash-merges
                it with the head pinned, the branch deleted only when no open PR is based on it,
                the squash's subject and body; reads it back MERGED with a merge commit, records
                it, and checks that commit's parent is the previous tip and its tree is the
                squash's. Any stop is written to land-batch.stop.
      report    After a stop: the stop file, then what is on origin's default branch since the
                batch began.
      reset     After the last merge or a stop: resets the local default branch to origin's, unless
                a commit sits after the last squash, which the stop names.
      push      Step 5b: with every recorded pull request still at its squashed head, not a draft
                and with no unanswered review thread, pushes the local default branch once and
                checks origin's tip is HEAD.
      close     Step 5b, one pull request: -Pr and -Sha, its squash commit on origin; closes it
                with the landing comment once the commit is on origin's default branch and the
                head is still the squashed one.
      watch     Step 6: the merge commits `land` recorded, or origin's tip after `push`, handed to
                Watch-PullRequestChecks.ps1; nothing landed is nothing to watch.
.PARAMETER Command
    The subcommand, first on the line.
.PARAMETER Pr
    The pull request, for add, stacked, record and close.
.PARAMETER Branch
    The pull request's head branch, for add, stacked and record.
.PARAMETER Message
    A file holding the squash commit's message, for add, stacked and record.
.PARAMETER Note
    What the amend fixed, for amend.
.PARAMETER Sha
    The pull request's squash commit on origin, for close.
.EXAMPLE
    pwsh -File <ouro>/bin/Invoke-LandBatch.ps1 check
.EXAMPLE
    pwsh -File <ouro>/bin/Invoke-LandBatch.ps1 add -Pr 123 -Branch feat/x -Message msg-123.txt
#>
param([Parameter(Position = 0)][string]$Command, [int]$Pr, [string]$Branch, [string]$Message, [string]$Note, [string]$Sha)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Get-LandBinding.ps1')
. (Join-Path $PSScriptRoot 'Watch-PullRequestChecks.ps1')

function Get-BatchFile([string]$Name) {
    Join-Path (@(Invoke-Read git @('rev-parse', '--absolute-git-dir'))[0]) "land-batch.$Name"
}
function Read-BatchLines([string]$Name) {
    $f = Get-BatchFile $Name
    if (Test-Path -LiteralPath $f -PathType Leaf) { @([System.IO.File]::ReadAllLines($f) | Where-Object { $_ }) } else { @() }
}
function Add-BatchLine([string]$Name, [string]$Text) {
    [System.IO.File]::AppendAllText((Get-BatchFile $Name), $Text + "`n", [System.Text.UTF8Encoding]::new($false))
}
# The recorded rows: PR, Branch, Head, Squash, in merge order.
function Read-Rows {
    @(Read-BatchLines tsv | ForEach-Object {
            $f = $_ -split "`t"
            [pscustomobject]@{ Pr = [int]$f[0]; Branch = $f[1]; Head = $f[2]; Squash = $f[3] }
        })
}
function Add-Row([int]$Pr, [string]$Branch, [string]$Head) {
    Add-BatchLine tsv "$Pr`t$Branch`t$Head`t$(@(Invoke-Read git @('rev-parse', 'HEAD'))[0])"
}
# A stop `land` records, so every later call refuses.
function Stop-Batch([string]$Why) {
    Add-BatchLine stop "STOP: $Why"
    throw $Why
}
function Get-Head { @(Invoke-Read git @('rev-parse', 'HEAD'))[0] }
function Test-Sha([string]$A, [string]$B) { [string]::Equals($A, $B, [StringComparison]::Ordinal) }

# The message as `git stripspace` reads it: trailing whitespace cut, blank runs folded, no
# leading or trailing blank line. The subject must end in (#<Pr>), with line two blank.
function Get-CleanMessage([string]$Path, [int]$Pr) {
    if (-not $Path) { throw '-Message <file> is required: the file holding the commit message' }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "no readable commit message at $Path" }
    $raw = @([System.IO.File]::ReadAllLines($Path) | ForEach-Object { $_.TrimEnd() })
    $clean = @(); $blank = $false
    foreach ($l in $raw) {
        if ($l -eq '') { $blank = $true; continue }
        if ($blank -and $clean.Count) { $clean += '' }
        $blank = $false; $clean += $l
    }
    $subject = if ($clean.Count) { $clean[0] } else { '' }
    if ($subject -notmatch "\(#$Pr\)\s*$" -or ($clean.Count -gt 1 -and $clean[1] -ne '')) {
        throw "the subject in $Path reads '$subject' - it must end in (#$Pr), with line 2 blank"
    }
    return $subject
}
# The one author of a range, `Name <email>`, or a throw: none, or more than one.
function Get-RangeAuthor([string]$Range, [int]$Pr, [string]$More) {
    $emails = @(Invoke-Read git @('log', '--no-merges', '--format=%aE', $Range) | ForEach-Object { $_.ToLowerInvariant() } | Sort-Object -Unique)
    if (-not $emails.Count) {
        throw "no author read from $Range for #$Pr - already landed (drop it from the set), holds only merge commits (it brings no commit of its own to squash: drop it from the set), or a ref did not resolve (refetch and start the batch over)"
    }
    if ($emails.Count -gt 1) {
        $authors = @(Invoke-Read git @('log', '--no-merges', '--format=%aN <%aE>', $Range) | Sort-Object -Unique)
        throw "#$Pr's range has more than one author:`n$($authors -join "`n")`n$More"
    }
    return @(Invoke-Read git @('log', '-1', '--no-merges', '--format=%aN <%aE>', $Range))[0]
}
$TwoAuthors = 'two addresses for one person are a mailmap entry, not a second author; otherwise a squash would reassign their work, and this drill lands nothing it has not squashed - land it on its own with /ouro:land, and recompose the batch without it'
# The pull request's head and base, with the head checked against origin's branch tip.
function Get-CheckedHead([string]$Slug, [int]$Pr, [string]$Branch) {
    $v = Get-PullRequest $Slug $Pr @('headRefOid', 'baseRefName')
    $tip = Invoke-Native git @('rev-parse', '--verify', '--quiet', "refs/remotes/origin/$Branch")
    if (-not "$($v.headRefOid)" -or $tip.Code -ne 0 -or -not (Test-Sha "$($v.headRefOid)" $tip.Out[0])) {
        throw "#$Pr's head is '$($v.headRefOid)', not origin/$Branch - refetch and start the batch over"
    }
    return $v
}
function Test-BaseInBatch([string]$Base, [string]$Default, [int]$Pr) {
    if ($Base -ceq $Default) { return }
    if (-not @(Read-Rows | Where-Object { $_.Branch -ceq $Base }).Count) {
        throw "#$Pr's base $Base is not in the batch - land it first, or this diff misses what it builds on"
    }
}
function Get-MergeState([string]$Slug, [int]$Pr) {
    $v = $null
    for ($i = 1; $i -le 5; $i++) {
        $v = Get-PullRequest $Slug $Pr @('baseRefName', 'mergeable', 'mergeStateStatus', 'isDraft')
        if ("$($v.mergeable) $($v.mergeStateStatus)" -notmatch 'UNKNOWN') { break }
        if ($i -lt 5) { Wait-Poll 10 }
    }
    return $v
}
function Get-Json([string]$Text) { try { $Text | ConvertFrom-Json } catch { $null } }

function Invoke-BatchCheck($b) {
    if ($b.Merge -eq 'merge') {
        throw "ship.merge: merge - both of this drill's paths land squash commits only, so a branch whose PRs land as merge commits is not its business: the set goes to /ouro:land per PR. Nothing has been squashed, pushed, merged or closed"
    }
    Write-Output $(if ($b.Merge) { "ship.merge: $($b.Merge)" } else { 'ship.merge: not declared - the batch lands squash commits, as a binding without the key does' })
    $slug = $b.Slug; $default = $b.Default
    $owner, $name = $slug -split '/', 2
    $perPr = @(); $only5a = @(); $strict = @()
    # A 403 naming the plan: rulesets and, on a private repository, classic protection are gated
    # by plan, so there is nothing to find. Any other failure on the rules or the queue read means
    # the batch cannot tell what the branch requires.
    function Test-PlanGated($r) { $r.Code -ne 0 -and $r.Text -match 'Upgrade to GitHub Pro|make this repository public' }

    $rules = Invoke-Native gh @('api', "repos/$slug/rules/branches/$default")
    if ($rules.Code -eq 0) {
        $set = @(Get-Json ($rules.Out -join "`n"))
        $pullRequest = @($set | Where-Object { $_.type -eq 'pull_request' })
        $checks = @($set | Where-Object { $_.type -eq 'required_status_checks' })
        $strictRules = @($checks | Where-Object { $_.parameters -and $_.parameters.strict_required_status_checks_policy -eq $true })
        $queue = @($set | Where-Object { $_.type -eq 'merge_queue' })
        $squashRefused = @($pullRequest | Where-Object {
                $methods = if ($_.parameters -and $_.parameters.PSObject.Properties['allowed_merge_methods']) { @($_.parameters.allowed_merge_methods) } else { @('squash') }
                -not ($methods -contains 'squash')
            })
        Write-Output "rules: pull_request $($pullRequest.Count), status_checks $($checks.Count), strict_checks $($strictRules.Count), merge_queue $($queue.Count), squash_refused $($squashRefused.Count)"
        if ($squashRefused.Count) { throw "a ruleset on $default refuses squash merges - both of this drill's paths land squash commits: the batch is not for this branch" }
        if ($queue.Count) { throw "a merge queue guards $default - gh pr merge enqueues instead of merging, so no merge can be checked against its squash: hand the set to the queue" }
        if ($pullRequest.Count -or $checks.Count) { $only5a += 'a ruleset requires pull requests or status checks' }
        if ($strictRules.Count) { $strict += 'a ruleset requires branches to be up to date' }
    }
    elseif (Test-PlanGated $rules) { Write-Output 'rules: not on this plan - nothing to find' }
    else { $perPr += "the rules read failed (exit $($rules.Code)): $($rules.Text)" }

    $branch = Invoke-Native gh @('api', "repos/$slug/branches/$default")
    $classic = if ($branch.Code -eq 0) { Get-Json ($branch.Out -join "`n") } else { $null }
    $protected = $classic -and $classic.protection -and $classic.protection.enabled -eq $true
    $contexts = if ($protected -and $classic.protection.required_status_checks -and $classic.protection.required_status_checks.PSObject.Properties['contexts']) { @($classic.protection.required_status_checks.contexts).Count } else { 0 }
    Write-Output "classic protection: $(if ($branch.Code -ne 0) { "unreadable (exit $($branch.Code))" } elseif ($protected) { "enabled, status checks $contexts" } else { 'off' })"
    if ($protected) {
        if ($contexts) { $only5a += 'classic protection requires status checks' }
        $prot = Invoke-Native gh @('api', "repos/$slug/branches/$default/protection")
        if ($prot.Code -eq 0) {
            $p = Get-Json ($prot.Out -join "`n")
            $classicStrict = $p.required_status_checks -and $p.required_status_checks.strict -eq $true
            $classicPr = $null -ne $p.required_pull_request_reviews
            Write-Output "classic protection details: strict $classicStrict, pull requests $classicPr"
            if ($classicStrict) { $strict += 'classic protection requires branches to be up to date' }
            if ($classicPr) { $only5a += 'classic protection requires pull requests' }
        }
        else {
            Write-Output 'classic protection details: did not read (admin access) - up-to-date branches, pull requests and checks are all treated as required'
            $strict += 'classic protection whose details did not read'; $only5a += 'classic protection whose details did not read'
        }
    }
    elseif ($branch.Code -ne 0 -and -not (Test-PlanGated $branch)) { $perPr += "the branch read failed (exit $($branch.Code)): $($branch.Text)" }

    $q = Invoke-Native gh @('api', 'graphql', '-f', 'query=query($owner: String!, $name: String!, $branch: String!) { repository(owner: $owner, name: $name) { mergeQueue(branch: $branch) { url } } }',
        '-f', "owner=$owner", '-f', "name=$name", '-f', "branch=$default")
    if ($q.Code -eq 0) {
        $g = Get-Json ($q.Out -join "`n")
        $hasQueue = $g -and $g.data -and $g.data.repository -and $null -ne $g.data.repository.mergeQueue
        Write-Output "merge queue: $hasQueue"
        if ($hasQueue) { throw "a merge queue guards $default - gh pr merge enqueues instead of merging, so no merge can be checked against its squash: hand the set to the queue" }
    }
    elseif (Test-PlanGated $q) { Write-Output 'merge queue: not on this plan' }
    else { $perPr += "the merge-queue read failed (exit $($q.Code)): $($q.Text)" }

    $repo = Invoke-Native gh @('api', "repos/$slug")
    $allow = if ($repo.Code -eq 0) { (Get-Json ($repo.Out -join "`n")).allow_squash_merge } else { $null }
    Write-Output "allow_squash_merge: $(if ($null -eq $allow) { 'null (no admin access; a refusal surfaces at the first merge)' } else { $allow })"
    if ($allow -eq $false) { throw "the repository refuses squash merges (allow_squash_merge false) - both of this drill's paths land squash commits: the batch is not for this branch" }

    if ($perPr.Count) {
        Write-Output "verdict: per PR through /ouro:land - the batch cannot tell whether a merge queue or a required pull request is there, and landing blind is not safe: $($perPr -join '; ')"
    }
    elseif ($strict.Count) {
        Write-Output "verdict: a set of two or more PRs goes to /ouro:land per PR - $($strict -join '; '): land finds the next PR BEHIND, and push is refused since the checks never ran on the squashes$(if ($only5a.Count) { '; a set of one may land through land, never push' })"
    }
    elseif ($only5a.Count) {
        Write-Output "verdict: land (5a) only - $($only5a -join '; '): a set that routes to push goes to /ouro:land per PR instead"
    }
    else { Write-Output 'verdict: land (5a) or push (5b)' }
}

function Invoke-BatchWorktree($b) {
    $dir = '.claude/worktrees/land-batch'
    $null = New-Item -ItemType Directory -Force -Path $dir
    if ((Invoke-Native git @('check-ignore', '-q', '--no-index', $dir)).Code -ne 0) {
        Remove-Item -LiteralPath $dir -Force
        throw '.claude/worktrees/ is not ignored - add it to .gitignore'
    }
    $add = Invoke-Native git @('worktree', 'add', $dir, $b.Default)
    if ($add.Code -ne 0) { throw "git worktree add refused: $($add.Text) - a worktree already registered there is removed first; git also refuses while the main checkout is itself on $($b.Default)" }
    $ff = Invoke-Native git @('-C', $dir, 'merge', '--ff-only', "origin/$($b.Default)")
    if ($ff.Code -ne 0) { throw "the worktree's $($b.Default) cannot fast-forward to origin's: $($ff.Text) - see git log origin/$($b.Default)..$($b.Default)" }
    if (-not (Test-Sha @(Invoke-Read git @('-C', $dir, 'rev-parse', 'HEAD'))[0] @(Invoke-Read git @('rev-parse', "origin/$($b.Default)"))[0])) {
        throw "local $($b.Default) is not origin/$($b.Default) - see git log origin/$($b.Default)..$($b.Default)"
    }
    Write-Output "batch worktree: $dir on $($b.Default) at origin's tip - every later call runs from there"
}

function Invoke-BatchStart {
    $gd = Split-Path (Get-BatchFile tsv) -Parent
    foreach ($f in [System.IO.Directory]::GetFiles($gd, 'land-batch.*')) { [System.IO.File]::Delete($f) }
    Write-Output 'batch started: the previous batch files are gone'
}

function Invoke-BatchAdd($b, [int]$Pr, [string]$Branch, [string]$Message) {
    if ($Pr -le 0 -or -not $Branch) { throw 'add takes -Pr <number>, -Branch <head branch> and -Message <file>' }
    $null = Get-CleanMessage $Message $Pr
    $v = Get-CheckedHead $b.Slug $Pr $Branch
    $base = "$($v.baseRefName)"
    Test-BaseInBatch $base $b.Default $Pr
    $range = if ($base -ceq $b.Default) { "origin/$($b.Default)..origin/$Branch" } else { "origin/$base..origin/$Branch" }
    $author = Get-RangeAuthor $range $Pr $TwoAuthors
    $m = Invoke-Native git @('merge', '--squash', "origin/$Branch")
    if ($m.Code -ne 0) {
        if (@(Invoke-Read git @('ls-files', '-u')).Count) { throw "#$Pr conflicts with the batch - resolve it by step 3, then run record" }
        throw "git merge --squash origin/$Branch failed for #$Pr without a conflict: $($m.Text)"
    }
    if ((Invoke-Native git @('diff', '--cached', '--quiet')).Code -eq 0) { throw "#$Pr adds nothing on top of the batch - already landed or named twice" }
    $c = Invoke-Native git @('commit', '-q', "--author=$author", '-F', $Message)
    if ($c.Code -ne 0) {
        $null = Invoke-Native git @('reset', '-q', '--hard', 'HEAD')
        throw "git commit failed for #${Pr}: $($c.Text) - the squash is undone"
    }
    Add-Row $Pr $Branch "$($v.headRefOid)"
    if ($base -cne $b.Default) { Add-BatchLine route "stacked: #$Pr is based on $base" }
    Write-Output "#$Pr squashed as $(Get-Head) (row $(@(Read-Rows).Count))$(if ($base -cne $b.Default) { ' - stacked: the batch routes to 5b' })"
}

function Invoke-BatchStacked($b, [int]$Pr, [string]$Branch, [string]$Message) {
    if ($Pr -le 0 -or -not $Branch) { throw 'stacked takes -Pr <number>, -Branch <head branch> and -Message <file>' }
    $null = Get-CleanMessage $Message $Pr
    $v = Get-CheckedHead $b.Slug $Pr $Branch
    $base = "$($v.baseRefName)"
    if ($base -ceq $b.Default) { throw "#$Pr's base is $base - it is not stacked, land it with add" }
    Test-BaseInBatch $base $b.Default $Pr
    $author = Get-RangeAuthor "origin/$base..origin/$Branch" $Pr "a stale base reports $base's own commits here, if it was force-pushed since #$Pr was cut - fetch and rebase the stacked branch onto origin/$base, then run this again; otherwise $TwoAuthors"
    $dry = Invoke-Native git @('merge-tree', '--write-tree', "--merge-base=origin/$base", 'HEAD', "origin/$Branch")
    if ($dry.Code -eq 1) { throw "#$Pr's own diff conflicts with the batch:`n$(@($dry.Out | Select-Object -Skip 1) -join "`n")" }
    if ($dry.Code -ne 0) { throw "git merge-tree failed for #$Pr (exit $($dry.Code)): $($dry.Text) - the squash is still in the tree" }
    $patch = Get-BatchFile patch
    $d = Invoke-Native git @('diff', '--binary', "--output=$patch", "origin/$base", "origin/$Branch")
    if ($d.Code -ne 0) { throw "git diff failed for #${Pr}: $($d.Text)" }
    if ((Get-Item -LiteralPath $patch).Length -eq 0) { throw "#$Pr adds nothing on top of its base - already landed or named twice" }
    $null = Invoke-Native git @('reset', '-q', '--hard', 'HEAD')
    $a = Invoke-Native git @('apply', '--index', '--3way', $patch)
    if ($a.Code -ne 0) {
        $null = Invoke-Native git @('reset', '-q', '--hard', 'HEAD')
        throw "git apply --index --3way failed for #${Pr}: $($a.Text) - the batch is unchanged"
    }
    if ((Invoke-Native git @('diff', '--cached', '--quiet')).Code -eq 0) { throw "#$Pr adds nothing on top of the batch - already landed or named twice" }
    $c = Invoke-Native git @('commit', '-q', "--author=$author", '-F', $Message)
    if ($c.Code -ne 0) {
        $null = Invoke-Native git @('reset', '-q', '--hard', 'HEAD')
        throw "git commit failed for #${Pr}: $($c.Text) - the applied diff is undone"
    }
    Add-Row $Pr $Branch "$($v.headRefOid)"
    Add-BatchLine route "stacked: #$Pr is based on $base"
    Write-Output "#$Pr landed as its own diff, $(Get-Head) (row $(@(Read-Rows).Count)); the batch routes to 5b"
}

function Invoke-BatchRecord($b, [int]$Pr, [string]$Branch, [string]$Message) {
    if ($Pr -le 0 -or -not $Branch) { throw 'record takes -Pr <number>, -Branch <head branch> and -Message <file>' }
    $subject = Get-CleanMessage $Message $Pr
    $unresolved = @(Invoke-Read git @('ls-files', '-u') | ForEach-Object { ($_ -split "`t", 2)[1] } | Sort-Object -Unique)
    if ($unresolved.Count) { throw "unresolved paths remain:`n$($unresolved -join "`n")" }
    $v = Get-CheckedHead $b.Slug $Pr $Branch
    $base = "$($v.baseRefName)"
    $range = if ($base -ceq $b.Default) { "origin/$($b.Default)..origin/$Branch" } else { "origin/$base..origin/$Branch" }
    $author = Get-RangeAuthor $range $Pr $TwoAuthors
    if ((Invoke-Native git @('diff', '--cached', '--quiet')).Code -eq 0) {
        $head = Get-Head
        $recorded = @(Read-Rows | Where-Object { Test-Sha $_.Squash $head }).Count -gt 0
        $headSubject = @(Invoke-Read git @('log', '-1', '--format=%s'))[0]
        if ($recorded -or $headSubject -cne $subject) { throw "nothing staged, and HEAD is not an unrecorded commit with #$Pr's message" }
        $headAuthor = @(Invoke-Read git @('log', '-1', '--format=%aN <%aE>'))[0]
        $headEmail = ($headAuthor -replace '^.*<(.*)>$', '$1').ToLowerInvariant()
        $email = ($author -replace '^.*<(.*)>$', '$1').ToLowerInvariant()
        if ($headEmail -ne $email) { throw "HEAD's author is $headAuthor, not #$Pr's range author $author - amend it with: git commit --amend --no-edit --author=`"$author`" - then run this again" }
    }
    else {
        $c = Invoke-Native git @('commit', '-q', "--author=$author", '-F', $Message)
        if ($c.Code -ne 0) { throw "git commit failed for #${Pr}: $($c.Text) - the resolution is still staged" }
    }
    Add-Row $Pr $Branch "$($v.headRefOid)"
    Add-BatchLine route "conflict resolved by hand: #$Pr"
    if ($base -cne $b.Default) { Add-BatchLine route "stacked: #$Pr is based on $base" }
    Write-Output "#$Pr recorded as $(Get-Head) (row $(@(Read-Rows).Count)); the batch now routes to 5b"
}

function Invoke-BatchAmend([string]$Note) {
    if (-not $Note) { throw 'amend takes -Note <what the amend fixed>' }
    Add-BatchLine route "amended after step 2: $Note"
    Write-Output 'recorded: the batch routes to 5b'
}

function Invoke-BatchRoute {
    $rows = @(Read-Rows)
    if (-not $rows.Count) { throw 'no batch recorded - step 2' }
    $counts = @{}
    foreach ($r in $rows) {
        $paths = @(@(Invoke-Read git @('diff-tree', '--no-commit-id', '--name-only', '-r', '-z', $r.Squash)) -join "`n" -split "`0" | Where-Object { $_ })
        foreach ($p in $paths) { $counts[$p] = 1 + $(if ($counts.ContainsKey($p)) { $counts[$p] } else { 0 }) }
    }
    $shared = @($counts.Keys | Where-Object { $counts[$_] -ge 2 } | Sort-Object)
    if ($shared.Count) {
        foreach ($line in @(Invoke-Read git (@('check-attr', 'merge', '--') + $shared))) {
            if ($line -notmatch ': merge: unspecified$') { Add-BatchLine route "merge= attribute on a path two PRs change: $line" }
        }
    }
    $route = @(Read-BatchLines route | Sort-Object -Unique)
    if ($route.Count) { Write-Output 'ROUTE: 5b -'; $route | Write-Output } else { Write-Output 'route: 5a' }
}

function Invoke-BatchLand($b) {
    $rows = @(Read-Rows)
    if (-not $rows.Count) { throw 'no batch recorded - step 2' }
    $route = @(Read-BatchLines route | Sort-Object -Unique)
    if ($route.Count) { throw "this batch routes to 5b, not 5a:`n$($route -join "`n")" }
    $stops = @(Read-BatchLines stop)
    if ($stops.Count) { throw "this batch already stopped:`n$($stops -join "`n")" }
    $landed = @(Read-BatchLines landed)
    $n = $landed.Count + 1
    if ($n -gt $rows.Count) { throw 'every recorded PR has landed - run reset' }
    $row = $rows[$n - 1]
    $prev = if ($landed.Count) { ($landed[-1] -split "`t")[1] } else { @(Invoke-Read git @('rev-parse', "$($rows[0].Squash)^"))[0] }
    $default = $b.Default; $slug = $b.Slug

    if ((Invoke-Native git @('fetch', 'origin', $default)).Code -ne 0) { Stop-Batch "cannot fetch $default before #$($row.Pr)" }
    $tip = @(Invoke-Read git @('rev-parse', "origin/$default"))[0]
    if (-not (Test-Sha $tip $prev)) { Stop-Batch "$default is at $($tip.Substring(0, 7)), not $prev - it moved since validation" }
    if ($n -eq 1) {
        if (-not (Test-Sha (Get-Head) $rows[-1].Squash)) { Stop-Batch 'HEAD is not the last recorded squash - a squash was amended or a commit added after step 2' }
        $lost = @($rows | Where-Object { (Invoke-Native git @('merge-base', '--is-ancestor', $_.Squash, 'HEAD')).Code -ne 0 } | ForEach-Object { $_.Squash })
        if ($lost.Count) { Stop-Batch "recorded squashes no longer on HEAD: $($lost -join ' ')" }
        foreach ($r in $rows) {
            $s = Get-MergeState $slug $r.Pr
            if ("$($s.mergeable)" -ne 'MERGEABLE') { Stop-Batch "#$($r.Pr) reads '$($s.mergeable)' against $default before anything landed" }
            if ($s.isDraft -ne $false) { Stop-Batch "#$($r.Pr) is still a draft, or its draft state did not read, before anything landed - mark it ready (/ouro:execute section 4) and start a new batch (steps 1-4)" }
        }
    }
    $s = Get-MergeState $slug $row.Pr
    if ("$($s.baseRefName)" -ne $default) { Stop-Batch "#$($row.Pr)'s base reads '$($s.baseRefName)', not $default - a PR retargeted since step 2 would merge into a branch nobody validated" }
    $state = "$($s.mergeable) $($s.mergeStateStatus)"
    if ($state -ne 'MERGEABLE CLEAN') { Stop-Batch "#$($row.Pr) is '$state', not MERGEABLE CLEAN" }
    foreach ($r in @($rows | Select-Object -Skip ($n - 1))) {
        $open = @(try { Get-OpenReviewThreads $slug $r.Pr } catch { Stop-Batch $_.Exception.Message })
        if ($open.Count) { Stop-Batch "#$($r.Pr) has a review thread nobody answered - reply in it (/ouro:land step 8) or resolve it:`n$($open -join "`n")" }
    }
    $arguments = @('pr', 'merge', "$($row.Pr)", '-R', $slug, '--squash', '--match-head-commit', $row.Head)
    $based = try { Get-OpenPullRequestsBasedOn $slug $row.Branch } catch { -1 }
    if ($based -eq 0) { $arguments += '--delete-branch' }
    $bodyFile = Get-BatchFile body
    [System.IO.File]::WriteAllText($bodyFile, (@(Invoke-Read git @('log', '-1', '--format=%b', $row.Squash)) -join "`n"), [System.Text.UTF8Encoding]::new($false))
    $arguments += @('--subject', @(Invoke-Read git @('log', '-1', '--format=%s', $row.Squash))[0], '--body-file', $bodyFile)
    $m = Invoke-Native gh $arguments
    if ($m.Text) { Write-Output $m.Text }
    $after = try { Get-PullRequest $slug $row.Pr @('state', 'mergeCommit') } catch { Stop-Batch "cannot read #$($row.Pr) after the squash-merge - check it by hand; nothing is recorded for it: $($_.Exception.Message)" }
    $oid = if ($after.mergeCommit) { "$($after.mergeCommit.oid)" } else { '' }
    if ("$($after.state)" -ne 'MERGED' -or -not $oid) {
        Stop-Batch "#$($row.Pr) reads '$($after.state) $oid', not MERGED with a merge commit - after 'Base branch was modified' the default branch moved and nothing merged for #$($row.Pr): the PRs not on the report's origin list go to a new batch (steps 1-4), since this stop file refuses every later call"
    }
    Add-BatchLine landed "$($row.Pr)`t$oid"
    if ((Invoke-Native git @('fetch', 'origin', $default)).Code -ne 0) { Stop-Batch "#$($row.Pr) merged as $oid, but $default could not be fetched to check it" }
    $parent = @(Invoke-Read git @('rev-parse', "$oid^"))[0]
    if (-not (Test-Sha $parent $prev)) { Stop-Batch "#$($row.Pr) merged as $oid onto $parent, not onto $prev - someone pushed in between" }
    if (-not (Test-Sha @(Invoke-Read git @('rev-parse', "$oid^{tree}"))[0] @(Invoke-Read git @('rev-parse', "$($row.Squash)^{tree}"))[0])) {
        Stop-Batch "#$($row.Pr) merged as $oid, whose tree is not the validated $($row.Squash)'s"
    }
    Write-Output "#$($row.Pr) landed as $oid ($n of $($rows.Count))"
}

function Invoke-BatchReport($b) {
    $rows = @(Read-Rows)
    if (-not $rows.Count) { throw 'no batch recorded - step 2' }
    $f = Invoke-Native git @('fetch', 'origin', $b.Default)
    if ($f.Code -ne 0) { throw "cannot fetch origin/$($b.Default): $($f.Text)" }
    foreach ($s in @(Read-BatchLines stop)) { Write-Output $s }
    Write-Output "on origin/$($b.Default) since the batch began:"
    foreach ($l in @(Invoke-Read git @('--no-pager', 'log', '--oneline', "$($rows[0].Squash)^..origin/$($b.Default)"))) { Write-Output "  $l" }
}

function Invoke-BatchReset($b) {
    $rows = @(Read-Rows)
    if (-not $rows.Count) { throw 'no batch recorded - step 2' }
    $last = $rows[-1].Squash
    $f = Invoke-Native git @('fetch', 'origin', $b.Default)
    if ($f.Code -ne 0) { throw "cannot fetch origin/$($b.Default): $($f.Text)" }
    if (-not (Test-Sha (Get-Head) $last)) {
        $after = @(Invoke-Read git @('--no-pager', 'log', '--oneline', "$last..HEAD"))
        throw "local $($b.Default) has commits after the last squash $last, and a reset would drop them:`n$($after -join "`n")`ncarry them onto what landed instead: git rebase --onto origin/$($b.Default) $last"
    }
    $null = Invoke-Read git @('reset', '--hard', "origin/$($b.Default)")
    Write-Output "local $($b.Default) reset to origin's $(@(Invoke-Read git @('rev-parse', '--short', 'HEAD'))[0])"
}

function Invoke-BatchPush($b) {
    $rows = @(Read-Rows)
    if (-not $rows.Count) { throw 'no batch recorded - step 2' }
    foreach ($r in $rows) {
        $v = Get-PullRequest $b.Slug $r.Pr @('headRefOid', 'isDraft')
        if (-not (Test-Sha "$($v.headRefOid)" $r.Head)) { throw "#$($r.Pr)'s head is '$($v.headRefOid)', not the $($r.Head) squashed in step 2 - what was pushed to it is not in its squash: recompose the batch (the non-fast-forward bullet)" }
        if ($v.isDraft -ne $false) { throw "#$($r.Pr) is still a draft, or its draft state did not read - it is marked ready once its fix commit verifies (/ouro:execute section 4)" }
        $open = @(Get-OpenReviewThreads $b.Slug $r.Pr)
        if ($open.Count) { throw "#$($r.Pr) has a review thread nobody answered - reply in it (/ouro:land step 8) or resolve it:`n$($open -join "`n")" }
    }
    $p = Invoke-Native git @('push', 'origin', $b.Default)
    if ($p.Code -ne 0) {
        $why = if ($p.Text -match 'GH013') { 'a ruleset the check could not read, or one added since (GH013): the set lands through /ouro:land per PR' }
            elseif ($p.Text -match 'GH006') { 'classic protection the check could not read (GH006): the set lands through /ouro:land per PR' }
            elseif ($p.Text -match 'non-fast-forward|fetch first') { "$($b.Default) moved since the batch was composed: reset (which reads the batch files first), then steps 1-4 again as a new batch on the new tip" }
            else { 'see git''s message' }
        throw "the push was refused: $($p.Text)`n$why; nothing is closed"
    }
    $ls = Invoke-Native git @('ls-remote', 'origin', "refs/heads/$($b.Default)")
    $tip = if ($ls.Code -eq 0 -and $ls.Out.Count) { ($ls.Out[0] -split '\s+')[0] } else { '' }
    if (-not (Test-Sha $tip (Get-Head))) { throw "origin's $($b.Default) is not local HEAD after the push" }
    Write-Output "pushed $($b.Default) to origin at $(Get-Head)"
}

function Invoke-BatchClose($b, [int]$Pr, [string]$Sha) {
    if ($Pr -le 0 -or -not $Sha) { throw 'close takes -Pr <number> and -Sha <its squash commit on origin>' }
    $row = @(Read-Rows | Where-Object { $_.Pr -eq $Pr }) | Select-Object -First 1
    if (-not $row) { throw "no row for #$Pr in land-batch.tsv - step 2" }
    $f = Invoke-Native git @('fetch', 'origin', $b.Default)
    if ($f.Code -ne 0) { throw "cannot fetch origin/$($b.Default): $($f.Text)" }
    if ((Invoke-Native git @('merge-base', '--is-ancestor', $Sha, "origin/$($b.Default)")).Code -ne 0) { throw "$Sha is not on origin/$($b.Default) - #$Pr has not landed, so it is not closed" }
    $v = Get-PullRequest $b.Slug $Pr @('headRefOid')
    if (-not (Test-Sha "$($v.headRefOid)" $row.Head)) { throw "#$Pr's head is '$($v.headRefOid)', not the $($row.Head) squashed in step 2 - what was pushed after it has not landed, so #$Pr is not closed" }
    $c = Invoke-Native gh @('pr', 'close', "$Pr", '-R', $b.Slug, '--comment', "Landed on $($b.Default) via the local squash-merge drill - $Sha.", '--delete-branch')
    if ($c.Code -ne 0) { throw "gh pr close failed for #${Pr}: $($c.Text)" }
    Write-Output "#$Pr closed as landed at $Sha"
}

function Invoke-BatchWatch($b) {
    $landed = @(Read-BatchLines landed | ForEach-Object { ($_ -split "`t")[1] })
    if ($landed.Count) { $shas = $landed; Write-Output "watching the merges land recorded: $($shas -join ' ')" }
    elseif (@(Read-BatchLines route).Count -and @(Read-Rows).Count) {
        $f = Invoke-Native git @('fetch', 'origin', $b.Default)
        if ($f.Code -ne 0) { throw "cannot fetch origin/$($b.Default): $($f.Text)" }
        $shas = @(@(Invoke-Read git @('rev-parse', "origin/$($b.Default)"))[0]); Write-Output "watching origin/$($b.Default) after the push: $($shas[0])"
    }
    else { $shas = @('none') }
    Invoke-WatchPullRequestChecks -Sha $shas
}

function Invoke-LandBatch {
    param([string]$Command, [int]$Pr, [string]$Branch, [string]$Message, [string]$Note, [string]$Sha)
    $commands = 'check', 'worktree', 'start', 'add', 'stacked', 'record', 'amend', 'route', 'land', 'report', 'reset', 'push', 'close', 'watch'
    if ($Command -notin $commands) { throw "the first argument is the subcommand, one of: $($commands -join ', ')" }
    $b = Get-LandBinding
    switch ($Command) {
        'check' { Invoke-BatchCheck $b }
        'worktree' { Invoke-BatchWorktree $b }
        'start' { Invoke-BatchStart }
        'add' { Invoke-BatchAdd $b $Pr $Branch $Message }
        'stacked' { Invoke-BatchStacked $b $Pr $Branch $Message }
        'record' { Invoke-BatchRecord $b $Pr $Branch $Message }
        'amend' { Invoke-BatchAmend $Note }
        'route' { Invoke-BatchRoute }
        'land' { Invoke-BatchLand $b }
        'report' { Invoke-BatchReport $b }
        'reset' { Invoke-BatchReset $b }
        'push' { Invoke-BatchPush $b }
        'close' { Invoke-BatchClose $b $Pr $Sha }
        'watch' { Invoke-BatchWatch $b }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $code = 1
    Invoke-LandEntry Invoke-LandBatch $PSBoundParameters ([ref]$code)
    exit $code
}
