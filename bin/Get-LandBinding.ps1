<#
.SYNOPSIS
    The helpers the landing scripts share. Dot-source, then call Get-LandBinding and the readers.
.DESCRIPTION
    A library, not a gate: this file defines functions and runs nothing on its own. The landing
    scripts beside it (Watch-PullRequestChecks.ps1, Merge-PullRequest.ps1, Remove-LandedBranch.ps1)
    dot-source it for:

      Get-LandBinding          validates .claude/ouro.toml at the working directory's repo root
                               through ouro-binding.py beside this file (`check`, then `get` per
                               key) and returns Slug, Default, Prefixes, Sync and Merge -- the last
                               two the declared `ship.sync` and `ship.merge` or $null where the
                               binding declares neither the key nor a `[ship].landing` preset. A
                               check that fails, a key that does not read, or an answer outside the
                               two values a key admits THROWS: a read that did not answer is never
                               taken for an undeclared key.
      Get-PullRequest          `gh pr view --json` of the named fields, as one object; a read that
                               fails, answers no JSON or lacks a field throws.
      Get-OpenPullRequestsBasedOn
                               how many open pull requests target a branch; a read that fails
                               throws, so a caller never deletes on a count it does not have.
      Get-OpenReviewThreads    the review threads of a pull request that nobody answered -- not
                               resolved, opened by anyone but the pull request's author, with no
                               reply -- one `login path url` line each. GraphQL, every page read
                               (`--paginate --slurp`, gh 2.46 or later): a REST comment list
                               carries no resolved state.
      Invoke-Native, Invoke-Read
                               a native command's stdout lines, exit code and text; Invoke-Read
                               throws on a non-zero exit.
      Invoke-LandEntry         runs a script's main function with the console decoding UTF-8 (gh,
                               git and python print it; a Windows console otherwise decodes with the
                               OEM code page), prints a throw as `STOP: <message>` and hands back
                               the exit code, 0 or 1, by reference.
      Wait-Poll                the pause between two re-reads of GitHub, a function so a suite can
                               shorten it.
      Get-Worktrees, Get-FullPath, Test-Within, Get-CurrentBranch
                               the worktrees git lists with the main checkout first, paths
                               compared as the filesystem compares them, and the checked-out
                               branch (a detached HEAD throws).

    Every gh call names the binding's repository with -R: a bare call addresses whatever gh picks
    from the clone's remotes, which in a fork's clone is the parent.
#>


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
    if ($Optional -and $r.Code -eq 1 -and $r.Text -match 'no such key') { return $null }
    throw "$Key did not read (exit $($r.Code)): $($r.Text)"
}

function Get-LandBinding {
    # A misspelled key reads as absent, and a missing protected_branch_prefixes protects nothing:
    # the whole binding is checked before the first gh call.
    $check = Invoke-Native python3 @((Join-Path $PSScriptRoot 'ouro-binding.py'), 'check')
    if ($check.Code -ne 0) {
        throw "ouro-binding.py check failed (exit $($check.Code)): $($check.Text) - no binding, nothing to land"
    }
    $sync = Get-BindingValue ship.sync -Optional
    if ($null -ne $sync -and $sync -notin 'merge', 'rebase') { throw "ship.sync reads '$sync', not merge or rebase" }
    $merge = Get-BindingValue ship.merge -Optional
    if ($null -ne $merge -and $merge -notin 'squash', 'merge') { throw "ship.merge reads '$merge', not squash or merge" }
    [pscustomobject]@{
        Slug     = Get-BindingValue repo.slug
        Default  = Get-BindingValue repo.default_branch
        Prefixes = @(Get-BindingValue repo.protected_branch_prefixes -Optional |
            Where-Object { $_ } | ConvertFrom-Json | Where-Object { $_ })
        Sync     = $sync
        Merge    = $merge
    }
}

function Get-PullRequest {
    param([string]$Slug, [int]$Pr, [string[]]$Fields)
    $r = Invoke-Native gh @('pr', 'view', "$Pr", '-R', $Slug, '--json', ($Fields -join ','))
    if ($r.Code -ne 0) { throw "cannot read #$Pr (gh pr view exit $($r.Code)): $($r.Text)" }
    $text = ($r.Out -join "`n").Trim()
    $view = try { $text | ConvertFrom-Json } catch { $null }
    if ($null -eq $view) { throw "cannot read #${Pr}: gh pr view answered no JSON: $text" }
    foreach ($f in $Fields) {
        if (-not $view.PSObject.Properties[$f]) { throw "cannot read #${Pr}: gh pr view answered without '$f'" }
    }
    return $view
}

function Get-OpenPullRequestsBasedOn {
    param([string]$Slug, [string]$Branch)
    $r = Invoke-Native gh @('pr', 'list', '-R', $Slug, '--base', $Branch, '--state', 'open', '--json', 'number')
    if ($r.Code -ne 0) { throw "cannot count the PRs based on $Branch (exit $($r.Code)): $($r.Text)" }
    $text = ($r.Out -join "`n").Trim()
    if (-not ($text.StartsWith('[') -and $text.EndsWith(']'))) {
        throw "cannot count the PRs based on ${Branch}: not a JSON array: $text"
    }
    return @($text | ConvertFrom-Json).Count
}

function Get-OpenReviewThreads {
    param([string]$Slug, [int]$Pr)
    $owner, $name = $Slug -split '/', 2
    $query = 'query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) { author { login } reviewThreads(first: 100, after: $endCursor) {
  pageInfo { hasNextPage endCursor } nodes { isResolved comments(first: 1) { totalCount nodes { author { login } path url } } } } } } }'
    $r = Invoke-Native gh @('api', 'graphql', '--paginate', '--slurp', '-F', "number=$Pr",
        '-f', "owner=$owner", '-f', "name=$name", '-f', "query=$query")
    if ($r.Code -ne 0) { throw "cannot read #$Pr's review threads (exit $($r.Code)): $($r.Text)" }
    $text = ($r.Out -join "`n").Trim()
    $pages = try { @($text | ConvertFrom-Json) } catch { $null }
    if ($null -eq $pages) { throw "cannot read #$Pr's review threads: no JSON: $text" }
    $open = @()
    foreach ($page in $pages) {
        $pull = $page.data.repository.pullRequest
        $prAuthor = if ($pull.author) { "$($pull.author.login)" } else { '' }
        foreach ($t in @($pull.reviewThreads.nodes)) {
            if ($t.isResolved -or $t.comments.totalCount -ne 1) { continue }
            $c = $t.comments.nodes[0]
            $login = if ($c.author) { "$($c.author.login)" } else { '' }
            if ($login -eq $prAuthor) { continue }
            $open += "$login $($c.path) $($c.url)"
        }
    }
    return $open
}

function Wait-Poll {
    param([int]$Seconds)
    Start-Sleep -Seconds $Seconds
}

# Paths compare as the filesystem does: case-insensitive on Windows, ordinal elsewhere.
$PathCompare = if ($IsWindows) { 'OrdinalIgnoreCase' } else { 'Ordinal' }
function Get-FullPath([string]$Path) {
    [System.IO.Path]::GetFullPath($Path).TrimEnd([char[]]@('\', '/'))
}
function Test-Within([string]$Path, [string]$Root) {
    $Path.Equals($Root, $PathCompare) -or
        $Path.StartsWith($Root + [System.IO.Path]::DirectorySeparatorChar, $PathCompare)
}
# The worktrees git lists, the main checkout first: @{ Path; Branch }.
function Get-Worktrees {
    $entries = @()
    foreach ($line in (Invoke-Read git @('worktree', 'list', '--porcelain'))) {
        if ($line -like 'worktree *') { $entries += @{ Path = Get-FullPath $line.Substring(9); Branch = $null } }
        elseif ($line -like 'branch refs/heads/*') { $entries[-1].Branch = $line.Substring(18) }
    }
    @($entries)
}
# The branch checked out in the working directory, or a throw for a detached HEAD.
function Get-CurrentBranch {
    $name = @(Invoke-Read git @('rev-parse', '--abbrev-ref', 'HEAD'))[0]
    if ($name -eq 'HEAD') { throw 'HEAD is detached - check out the feature branch first' }
    return $name
}

# The function's lines flow out to the caller's stdout; the exit code comes back by reference, so
# no line is swallowed into an exit expression.
function Invoke-LandEntry {
    param([string]$Function, [hashtable]$Parameters, [ref]$ExitCode)
    $encoding = [Console]::OutputEncoding
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    try { & $Function @Parameters; $ExitCode.Value = 0 }
    catch { Write-Output "STOP: $($_.Exception.Message)"; $ExitCode.Value = 1 }
    finally { [Console]::OutputEncoding = $encoding }
}
