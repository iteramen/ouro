<#
.SYNOPSIS
    Find every local branch, remote branch and .claude/worktrees/ worktree whose pull request is
    finished, and report them -- or with -Delete, remove them.
.DESCRIPTION
    Git cannot say whether a branch is finished: a squash merge never lands the branch's own
    commits on the default branch, so `git branch --merged` and `git branch -d` do not recognise
    it. The pull request's state does. Run from the repo's main checkout; the repository, the
    default branch and the protected prefixes come from the binding, and every gh call names the
    binding's repository with -R -- a bare gh picks a repository from the remotes. The binding
    is validated first (`ouro-binding.py check`): a misspelled key would read as absent, and an
    absent protected_branch_prefixes protects nothing. An invalid binding prints the check's
    output and exits 1 before any fetch or gh call. Every git and python call is about the
    repository the run was started in: GIT_DIR, GIT_WORK_TREE and GIT_COMMON_DIR are unset for
    the whole run and put back for the caller afterwards, since git reads them before it looks at
    a path, and one naming another repository takes the candidates, the worktrees and the refs
    from there while the current directory stays here.

    Candidates, after `git fetch --prune origin`: the local branches, origin's branches, and the
    worktrees git lists under .claude/worktrees/ in the main checkout. Never a candidate, each
    listed with why: the default branch, the branch checked out in the main checkout, a branch
    checked out in any other worktree outside .claude/worktrees/, a branch matching a
    [repo].protected_branch_prefixes entry, a worktree with no branch checked out, and -- where
    the filesystem folds case (Windows, macOS) -- a local branch whose name differs only in case
    from another's: the two can share one ref file, so neither is touched. An orphan, a
    directory under .claude/worktrees/ that git does not list, is reported and never deleted.

    Each branch's pull requests are read with `gh pr list --head <branch> --state all --limit
    1000`. An open one skips the branch; none skips it; a MERGED or a CLOSED one makes it
    finished, since a closed pull request's commits stay on GitHub under it. A read that fails,
    or returns 1000 rows or more (the list may have been cut there), skips the branch and makes
    the run exit 1: a failure never reads as finished.

    Finished is not yet removable. A local tip, and separately origin's tip, goes only when it is a
    finished pull request's head commit or an ancestor of it -- work the pull request never carried
    stays for the owner. A head commit not present locally counts only when it equals the tip. A
    worktree stays, and so does the branch checked out in it, when that branch has no commit yet
    (unborn: no tip to judge), when it holds this script's current directory, holds another worktree
    (which would go with it), is locked (`git worktree lock`), is prunable (its directory is gone:
    `git worktree prune` is the owner's call), or has uncommitted changes. Ignored files do not keep
    it: a worktree is made per issue, so they are disposable, and `git worktree remove` deletes them
    with it -- a .env, a local overlay, a bin/__pycache__/. The worktree's finished and deleted
    lines list every ignored path, as `git status --ignored` names them (an ignored directory is one
    path), so the report shows all that -Delete takes. The one exception: an ignored directory
    holding a nested git repository (a .git directory or file right inside it; nothing deeper is
    searched) keeps the worktree, since its commits are work no PR carried. An origin branch that
    another open pull request is based on stays too: deleting it would close or strand that pull
    request.

    Reports by default: nothing is deleted and no ref changes but what `git fetch --prune origin`
    does. -Delete removes each finished branch in order, and a failed step ends that branch's
    run. Exit 1 means the binding check, the fetch or another git read failed, a PR state could
    not be read, or a removal failed; a skip alone is not a failure.
.PARAMETER Delete
    Remove the finished set, per branch: the worktree (`git worktree remove`, never --force),
    checked afterwards -- one git still lists was refused (a worktree with a submodule, say) and
    is reported "failed"; one git no longer lists but whose directory is still on disk hit a
    lock (a process whose current directory is inside it), which deregisters it anyway, and is
    reported "left on disk"; then the local branch, by `git branch -D` once its name is seen
    still answering at the tip that was checked, so a ref that moved before that re-read is
    refused rather than deleted, and git's own refusals (mid-rebase, mid-bisect) stand; then
    origin's branch, when origin still lists it and its tip still passes the head-commit rule,
    by `git push --force-with-lease=<ref>:<tip> origin --delete`, which refuses if origin's tip
    moved after that check. Origin's delete is leased; the local re-read and delete are two
    process spawns, and a concurrent write to a branch checked out nowhere in that window is not
    caught.
#>
param([switch]$Delete)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# gh, git and python print UTF-8; a Windows console otherwise decodes it with the OEM code page.
$startEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {

# A native call's stdout lines, its exit code, and everything it printed for a message. Only
# stdout is data: under 2>&1 stderr arrives as ErrorRecords.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    $out = & $Exe @Arguments 2>&1
    [pscustomobject]@{
        Code = $LASTEXITCODE
        Out  = @($out | Where-Object { $_ -is [string] })
        Text = ((@($out) | ForEach-Object { "$_" }) -join ' ').Trim()
    }
}
function Invoke-Read {
    param([string]$Exe, [string[]]$Arguments)
    $r = Invoke-Native $Exe $Arguments
    if ($r.Code -ne 0) { throw "$Exe $($Arguments -join ' ') failed (exit $($r.Code)): $($r.Text)" }
    return $r.Out
}

function Get-BindingValue {
    param([string]$Key, [switch]$Optional)
    $r = Invoke-Native python3 @((Join-Path $PSScriptRoot 'ouro-binding.py'), 'get', $Key)
    if ($r.Code -eq 0) {
        $value = ($r.Out -join "`n").Trim()
        if (-not $value) { throw "$Key resolved to an empty value" }
        return $value
    }
    if ($Optional -and $r.Text -match 'no such key') { return $null }
    throw "ouro-binding.py get $Key failed (exit $($r.Code)): $($r.Text)"
}

$PathCompare = if ($IsWindows) { 'OrdinalIgnoreCase' } else { 'Ordinal' }
function Get-FullPath([string]$Path) {
    [System.IO.Path]::GetFullPath($Path).TrimEnd([char[]]@('\', '/'))
}
function Test-Within([string]$Path, [string]$Root) {
    $Path.Equals($Root, $PathCompare) -or
        $Path.StartsWith($Root + [System.IO.Path]::DirectorySeparatorChar, $PathCompare)
}
function Get-Tag($Pr) { "PR #$($Pr.number) $($Pr.state)" }

# The first of the finished pull requests whose head commit is $Tip or a descendant of it. A head
# commit this clone does not have can only be compared for equality.
function Find-CoveringPr {
    param([string]$Tip, [object[]]$Prs)
    foreach ($pr in $Prs) {
        if ($Tip -eq $pr.headRefOid) { return $pr }
        $known = (Invoke-Native git @('cat-file', '-e', "$($pr.headRefOid)^{commit}")).Code -eq 0
        if ($known -and
            (Invoke-Native git @('merge-base', '--is-ancestor', $Tip, $pr.headRefOid)).Code -eq 0) {
            return $pr
        }
    }
    return $null
}

# The branch's pull requests: @{ Skip = reason } or @{ Prs = the finished ones, merged first }. A
# read that fails, or answers anything but a whole JSON array of rows, is Unreadable: gh --json
# prints an array and nothing else, and ConvertFrom-Json parses a truncated one to the rows it got.
function Get-PrVerdict {
    param([string]$Branch)
    # gh lists 30 by default, and an OPEN pull request past the 30th sharing this head name would
    # go unseen. 1000 is the ceiling: a reply of 1000 rows may have been cut there, so it is
    # unreadable below.
    $r = Invoke-Native gh @('pr', 'list', '-R', $slug, '--head', $Branch, '--state', 'all',
        '--limit', '1000', '--json', 'number,state,headRefOid')
    if ($r.Code -ne 0) {
        return @{ Skip = "PR state unreadable: exit $($r.Code): $($r.Text)"; Unreadable = $true }
    }
    $text = ($r.Out -join "`n").Trim()
    $rows = $null
    if ($text.StartsWith('[') -and $text.EndsWith(']')) {
        try { $rows = @($text | ConvertFrom-Json) } catch { $rows = $null }
    }
    $whole = $null -ne $rows -and @($rows | Where-Object {
            -not ($_.PSObject.Properties['number'] -and $_.PSObject.Properties['headRefOid'] -and
                $_.PSObject.Properties['state'] -and $_.state -in 'OPEN', 'MERGED', 'CLOSED')
        }).Count -eq 0
    if (-not $whole) {
        return @{ Skip = "PR state unreadable: not a JSON array of PRs: $text"; Unreadable = $true }
    }
    if ($rows.Count -ge 1000) {
        return @{ Skip = 'PR state unreadable: more PRs than the 1000 read'; Unreadable = $true }
    }
    $open = @($rows | Where-Object state -EQ 'OPEN')
    if ($open) { return @{ Skip = "open PR #$($open[0].number)" } }
    if (-not $rows) { return @{ Skip = 'no PR' } }
    $done = @($rows | Sort-Object @{ Expression = { $_.state -ne 'MERGED' } },
        @{ Expression = { [int]$_.number }; Descending = $true })
    return @{ Prs = $done }
}

# Deleting an origin branch that another open pull request is based on closes or strands that
# pull request -- the rule /ouro:land's merge and cleanup steps follow. $null, or @{ Skip = why }.
function Get-BaseVerdict {
    param([string]$Branch)
    $r = Invoke-Native gh @('pr', 'list', '-R', $slug, '--base', $Branch, '--state', 'open',
        '--json', 'number')
    if ($r.Code -ne 0) {
        return @{ Skip = "PR state unreadable: exit $($r.Code): $($r.Text)"; Unreadable = $true }
    }
    $text = ($r.Out -join "`n").Trim()
    $rows = $null
    if ($text.StartsWith('[') -and $text.EndsWith(']')) {
        try { $rows = @($text | ConvertFrom-Json) } catch { $rows = $null }
    }
    if ($null -eq $rows -or @($rows | Where-Object { -not $_.PSObject.Properties['number'] })) {
        return @{ Skip = "PR state unreadable: not a JSON array of PRs: $text"; Unreadable = $true }
    }
    if ($rows) { return @{ Skip = "open PR #$($rows[0].number) is based on it" } }
    return $null
}

# Every git call in this run is about the repository of the working directory, not about the
# caller's environment: git reads GIT_DIR before it looks at a path, and -C does not win over it.
# With a GIT_DIR a caller exported, the candidates, the worktrees and the refs are that
# repository's, while `rev-parse --show-toplevel` still answers the working directory, so the
# guard that keeps the worktree holding it can never match -- and -Delete then deletes that
# repository's branches, on its origin too, for pull request states read under this binding's
# slug. GIT_WORK_TREE and GIT_COMMON_DIR each send at least one of those reads to another
# repository too. Anything that exports any of them around a command reaches the sweep:
# `git submodule foreach` sets GIT_DIR for each submodule it runs in. So the three are unset for
# the whole run, before the first python or git call, and put back in the finally below, which
# every way out of the run goes through -- its exits and a git read that throws.
# [NullString]::Value, because on Windows $null from PowerShell leaves a variable set to an empty
# string and an empty GIT_DIR makes git refuse a real root too.
$gitVars = @{}
foreach ($name in 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR') {
    $gitVars[$name] = [Environment]::GetEnvironmentVariable($name)
    [Environment]::SetEnvironmentVariable($name, [NullString]::Value)
}
try {
    # A misspelled key reads as absent, and a missing protected_branch_prefixes protects nothing: the
    # whole binding is checked before any fetch or gh call.
    $check = @(& python3 (Join-Path $PSScriptRoot 'ouro-binding.py') check 2>&1)
    if ($LASTEXITCODE -ne 0) {
        Write-Output "ouro-binding.py check failed (exit $LASTEXITCODE):"
        $check | ForEach-Object { Write-Output "$_" }
        exit 1
    }

    $slug = Get-BindingValue repo.slug
    $default = Get-BindingValue repo.default_branch
    $prefixes = @(Get-BindingValue repo.protected_branch_prefixes -Optional |
        Where-Object { $_ } | ConvertFrom-Json | Where-Object { $_ })

    $null = Invoke-Read git @('fetch', '--prune', '--quiet', 'origin')

    # Worktrees: the first porcelain entry is the main checkout. Every path compared comes from git,
    # the current directory's worktree included, so a short or linked spelling cannot hide a match.
    $entries = [System.Collections.Generic.List[hashtable]]::new()
    # A `locked` or `prunable` line may carry a reason after the keyword.
    foreach ($line in (Invoke-Read git @('worktree', 'list', '--porcelain'))) {
        if ($line -like 'worktree *') {
            $entries.Add(@{ Path = Get-FullPath $line.Substring(9); Branch = $null; Locked = $null
                    Prunable = $false })
        }
        elseif ($line -like 'branch refs/heads/*') {
            $entries[$entries.Count - 1].Branch = $line.Substring(18)
        }
        elseif ($line -cmatch '^locked( (.*))?$') {
            $entries[$entries.Count - 1].Locked =
                if ($Matches[2]) { $Matches[2] } else { 'no reason given' }
        }
        elseif ($line -cmatch '^prunable( |$)') { $entries[$entries.Count - 1].Prunable = $true }
    }
    $mainBranch = $entries[0].Branch
    $wtRoot = Get-FullPath (Join-Path $entries[0].Path '.claude/worktrees')
    $here = Get-FullPath (@(Invoke-Read git @('rev-parse', '--show-toplevel'))[0])
    $worktrees = @($entries | Select-Object -Skip 1 | Where-Object { Test-Within $_.Path $wtRoot })
    # A branch checked out in a worktree outside .claude/worktrees/ belongs to that worktree, as the
    # main checkout's does: never a candidate.
    $elsewhere = [hashtable]::new([StringComparer]::Ordinal)
    foreach ($e in @($entries | Select-Object -Skip 1 | Where-Object { $_.Branch })) {
        if (-not (Test-Within $e.Path $wtRoot)) { $elsewhere[$e.Branch] = $e.Path }
    }
    function Get-Rel([string]$Path) {
        '.claude/worktrees/' + $Path.Substring($wtRoot.Length + 1).Replace('\', '/')
    }

    # Branch names compare as git compares them: ordinal. PowerShell's -ceq and Sort-Object compare by
    # culture, which reads `x<U+200B>y` and `xy` as one name.
    function Test-Same([string]$A, [string]$B) { [string]::Equals($A, $B, [StringComparison]::Ordinal) }

    # Branch name -> tip commit, case-sensitive as refs are.
    function Get-Tips([string]$Refs, [int]$Strip) {
        $tips = [hashtable]::new([StringComparer]::Ordinal)
        $format = "--format=%(refname:lstrip=$Strip)%09%(objectname)"
        foreach ($l in (Invoke-Read git @('for-each-ref', $format, $Refs))) {
            $n, $sha = $l -split "`t", 2
            if (-not (Test-Same $n 'HEAD')) { $tips[$n] = $sha }
        }
        return $tips
    }
    $local = Get-Tips refs/heads 2
    $remote = Get-Tips refs/remotes/origin 3

    # Where the filesystem folds case, two local names that differ only in case can share one file:
    # which commit each answers at, and what deleting one takes, is not knowable. Neither is touched.
    # Grouped by the invariant upper case, compared ordinal: case alone, not culture, makes twins.
    $twins = [hashtable]::new([StringComparer]::Ordinal)
    if ($IsWindows -or $IsMacOS) {
        $byUpper = [hashtable]::new([StringComparer]::Ordinal)
        foreach ($n in $local.Keys) { $byUpper[$n.ToUpperInvariant()] += @($n) }
        foreach ($g in @($byUpper.Values | Where-Object { $_.Count -gt 1 })) {
            foreach ($n in $g) {
                $twins[$n] = @($g | Where-Object { -not (Test-Same $_ $n) }) -join ', '
            }
        }
    }

    # Whether git still lists a worktree at this path.
    function Test-Listed([string]$Path) {
        @(Invoke-Read git @('worktree', 'list', '--porcelain') |
            Where-Object { $_ -like 'worktree *' } |
            Where-Object { (Get-FullPath $_.Substring(9)).Equals($Path, $PathCompare) }).Count -gt 0
    }

    $failures = 0
    $finished = 0
    $names = [System.Collections.Generic.SortedSet[string]]::new([StringComparer]::Ordinal)
    foreach ($n in @($local.Keys) + @($remote.Keys)) { $null = $names.Add($n) }
    foreach ($name in $names) {
        $wt = @($worktrees | Where-Object { Test-Same $_.Branch $name }) | Select-Object -First 1
        $wtRel = if ($wt) { Get-Rel $wt.Path }
        $things = @(if ($wt) { "worktree $wtRel" }
            if ($local.ContainsKey($name)) { "local $name" }
            if ($remote.ContainsKey($name)) { "remote $name" })

        $protected = @($prefixes | Where-Object { $name.StartsWith($_, [StringComparison]::Ordinal) })
        $verdict = if (Test-Same $name $default) { @{ Skip = 'default branch' } }
            elseif (Test-Same $name $mainBranch) { @{ Skip = 'checked out in the main checkout' } }
            elseif ($elsewhere.ContainsKey($name)) {
                @{ Skip = "checked out at $($elsewhere[$name])" }
            }
            elseif ($protected) { @{ Skip = "protected prefix $($protected[0])" } }
            elseif ($twins.ContainsKey($name)) {
                @{ Skip = "another local branch differs only in case: $($twins[$name])" }
            }
            else { Get-PrVerdict $name }
        if ($verdict.ContainsKey('Skip')) {
            if ($verdict.ContainsKey('Unreadable')) { $failures++ }
            foreach ($t in $things) { Write-Output "skip: $t - $($verdict.Skip)" }
            continue
        }
        $prs = $verdict.Prs
        $beyond = "commits beyond PR #$($prs[0].number)'s head"

        # The local branch and its worktree go or stay together: the worktree holds the branch.
        $localPr = if ($local.ContainsKey($name)) { Find-CoveringPr $local[$name] $prs }
        $localWhy = if ($local.ContainsKey($name) -and -not $localPr) { $beyond }
            # A worktree on a branch with no commit yet (unborn, while origin has one of that name)
            # has no tip to hold against a PR: it cannot be judged, so it stays.
            elseif ($wt -and -not $local.ContainsKey($name)) { 'its branch has no commit to judge' }
        # Ignored files never keep a worktree -- it is made per issue -- but its line lists them all,
        # unless one is a nested repository.
        $ignored = @()
        if ($wt -and -not $localWhy) {
            # Another worktree inside this one goes with it on removal, uncommitted work and all.
            $inner = @($entries | Where-Object {
                    -not $_.Path.Equals($wt.Path, $PathCompare) -and (Test-Within $_.Path $wt.Path)
                } | Select-Object -First 1)
            $localWhy = if (Test-Within $here $wt.Path) { "$wtRel holds the current directory" }
                elseif ($wt.Locked) { "locked: $($wt.Locked)" }
                elseif ($wt.Prunable) { 'prunable: its directory is gone - run git worktree prune' }
                elseif ($inner) { "holds worktree $(Get-Rel $inner[0].Path)" }
            if (-not $localWhy) {
                # -z: every path unquoted, as it is on disk. An ignored directory is one entry.
                $status = Invoke-Native git @('-C', $wt.Path, 'status', '--porcelain', '-z',
                    '--ignored')
                $items = @(($status.Out -join "`n") -split "`0" | Where-Object { $_ })
                $ignored = @($items | Where-Object { $_.StartsWith('!! ') } |
                    ForEach-Object { $_.Substring(3) })
                # An ignored directory holding a repository of its own (a .git directory or file right
                # inside it) holds commits no PR carried: that is work, not build output.
                $nested = @($ignored | Where-Object {
                        $_.EndsWith('/') -and (Test-Path -LiteralPath (Join-Path $wt.Path "$($_).git"))
                    })
                # PowerShell splits git's output at a CR as well as a LF, so a name holding a CR comes
                # back altered and the check above would miss it: each ignored path must exist as named.
                $unseen = @($ignored | Where-Object {
                        -not (Test-Path -LiteralPath (Join-Path $wt.Path $_.TrimEnd('/')))
                    })
                $localWhy = if ($status.Code -ne 0) {
                        $failures++; "status of $wtRel unreadable: $($status.Text)"
                    }
                    elseif ($unseen) {
                        $failures++
                        "status of $wtRel unreadable: ignored $($unseen[0]) is not on disk as named"
                    }
                    elseif ($items.Count -gt $ignored.Count) { "uncommitted changes in $wtRel" }
                    elseif ($nested) {
                        $more = if ($nested.Count -gt 1) { ', ...' }
                        "nested git repository in ignored $($nested[0])$more in $wtRel"
                    }
            }
        }

        $ok = $true
        if ($wt -and $localWhy) { Write-Output "skip: worktree $wtRel - $localWhy" }
        elseif ($wt) {
            # Only here is there a covering PR to name: a skipped worktree may have none.
            $with = if ($ignored) { ", with ignored files: $($ignored -join ', ')" }
            $what = "$wtRel ($name, $(Get-Tag $localPr)$with)"
            if (-not $Delete) { $finished++; Write-Output "finished: worktree $what" }
            else {
                $r = Invoke-Native git @('worktree', 'remove', $wt.Path)
                # A worktree git still lists was refused (one with a submodule, say) and is whole. One
                # it no longer lists but whose directory is still there hit a held directory: the
                # removal deregisters it anyway, so only the filesystem shows what is left.
                if (Test-Listed $wt.Path) {
                    $ok = $false; $failures++; Write-Output "failed: worktree $wtRel - $($r.Text)"
                }
                elseif (Test-Path -LiteralPath $wt.Path) {
                    $ok = $false; $failures++
                    Write-Output "left on disk: $wtRel - worktree remove exit $($r.Code): $($r.Text)"
                }
                elseif ($r.Code -ne 0) {
                    $ok = $false; $failures++; Write-Output "failed: worktree $wtRel - $($r.Text)"
                }
                else { $finished++; Write-Output "deleted: worktree $what" }
            }
        }
        if ($local.ContainsKey($name)) {
            if ($localWhy) { Write-Output "skip: local $name - $localWhy" }
            elseif (-not $ok) { Write-Output "skip: local $name - its worktree was not removed" }
            elseif (-not $Delete) {
                $finished++; Write-Output "finished: local $name ($(Get-Tag $localPr))"
            }
            else {
                # By name only while the name still answers at the tip that was checked: on a
                # case-insensitive filesystem a loose ref spelled differently answers for this name,
                # and `git branch -D` would delete that one. branch -D rather than update-ref -d keeps
                # git's own refusals (a branch mid-rebase or mid-bisect in any worktree) and removes
                # the branch's config section.
                $now = Invoke-Native git @('rev-parse', '--verify', '--quiet', "refs/heads/$name")
                $at = @($now.Out | Select-Object -First 1)
                $r = if ("$at" -cne $local[$name]) {
                    [pscustomobject]@{ Code = 1
                        Text = "refs/heads/$name answers at '$at', not the checked $($local[$name])" }
                }
                else { Invoke-Native git @('branch', '-D', $name) }
                if ($r.Code -ne 0) {
                    $ok = $false; $failures++; Write-Output "failed: local $name - $($r.Text)"
                }
                else { $finished++; Write-Output "deleted: local $name ($(Get-Tag $localPr))" }
            }
        }
        if (-not $remote.ContainsKey($name)) { continue }
        $remotePr = Find-CoveringPr $remote[$name] $prs
        if (-not $remotePr) { Write-Output "skip: remote $name - $beyond"; continue }
        if (-not $ok) { Write-Output "skip: remote $name - an earlier step failed"; continue }
        $base = Get-BaseVerdict $name
        if ($base) {
            if ($base.ContainsKey('Unreadable')) { $failures++ }
            Write-Output "skip: remote $name - $($base.Skip)"; continue
        }
        if (-not $Delete) {
            $finished++; Write-Output "finished: remote $name ($(Get-Tag $remotePr))"; continue
        }
        # Origin as it is now, not as fetched: its tip must still pass the head-commit rule.
        $ls = Invoke-Native git @('ls-remote', '--exit-code', '--heads', 'origin', "refs/heads/$name")
        if ($ls.Code -eq 2) { Write-Output "skip: remote $name - already deleted on origin"; continue }
        if ($ls.Code -ne 0) { $failures++; Write-Output "failed: remote $name - $($ls.Text)"; continue }
        $tip = ($ls.Out[0] -split '\s+')[0]
        $remotePr = Find-CoveringPr $tip $prs
        if (-not $remotePr) { Write-Output "skip: remote $name - $beyond"; continue }
        # The lease: origin refuses the delete if its tip moved after the check above.
        $r = Invoke-Native git @('push', '--quiet', "--force-with-lease=refs/heads/${name}:$tip",
            'origin', '--delete', "refs/heads/$name")
        if ($r.Code -ne 0) { $failures++; Write-Output "failed: remote $name - $($r.Text)" }
        else { $finished++; Write-Output "deleted: remote $name ($(Get-Tag $remotePr))" }
    }

    # A worktree with no branch checked out has no PR to ask about.
    foreach ($e in @($worktrees | Where-Object { -not $_.Branch })) {
        Write-Output "skip: worktree $(Get-Rel $e.Path) - no branch checked out"
    }

    # A directory git does not list is not a worktree the sweep can judge: reported, never removed.
    if ([System.IO.Directory]::Exists($wtRoot)) {
        foreach ($d in ([System.IO.Directory]::GetDirectories($wtRoot) | Sort-Object)) {
            $p = Get-FullPath $d
            if (-not @($worktrees | Where-Object { Test-Within $_.Path $p })) {
                Write-Output "orphan: $(Get-Rel $p) - on disk, not a worktree git lists; left in place"
            }
        }
    }

    if (-not $Delete) {
        Write-Output "report only: $finished finished, nothing deleted; -Delete removes them"
    }
    if ($failures) { Write-Output "$failures failure(s): see the lines above"; exit 1 }
    exit 0
}
finally {
    foreach ($name in $gitVars.Keys) {
        if ($null -ne $gitVars[$name]) { [Environment]::SetEnvironmentVariable($name, $gitVars[$name]) }
    }
}
}
finally { [Console]::OutputEncoding = $startEncoding }
