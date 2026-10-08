<#
.SYNOPSIS
    Resolve a rolling issue's number from the binding. Dot-source, then call
    Get-RollingIssueNumber -Key rolling_issues.<name>.
.DESCRIPTION
    A library, not a gate: this file defines one function and runs nothing on its own, so
    it is dot-sourced rather than invoked. The resolver's outcomes and its parameters are
    documented on the function it defines -- Get-Help Get-RollingIssueNumber -Full.
#>

# The repository every gh call below names comes from the shared resolution, never gh's pick.
. (Join-Path $PSScriptRoot 'Get-RepoSlug.ps1')


<#
.SYNOPSIS
    Resolve a rolling issue's number from the binding. Dot-source, then call
    Get-RollingIssueNumber -Key rolling_issues.<name>.
.DESCRIPTION
    One resolver so the gates that post findings cannot disagree about where they post,
    and cannot drift from the binding that names it. Every caller sets the working
    directory to the repo root first -- `ouro-binding.py` reads the binding from there.

    The title comes from the binding, never a literal. A gate carrying its own copy of
    the title silently posts nowhere the moment a repo renames the issue, and the two
    gates that did this were byte-identical and both wrong.

    Six outcomes, deliberately distinguishable -- the whole defect being fixed here is
    that they used to look alike:

      - the repo has no .claude/ouro.toml at all          -> INFO and return 0, without
        calling python: pwsh and git are enough for a repo with no binding. Outside a git
        work tree there is no root to look in, and the read throws.
      - the key is absent, or the binding cannot be read  -> THROW. A -Comment run with
        no target is the silent no-op this exists to remove, not a reason to continue.
      - neither the binding nor `origin` names a repo     -> THROW, before any gh call
        (Get-RepoSlug.ps1). Not 0: that is "not filed yet", which a caller answers by creating
        the issue, and a create naming no repository files it wherever gh picks.
      - `gh` fails                                        -> THROW, with the exit code
        (a failed read must never read as "nothing to report").
      - the only exact-title match is closed              -> REOPEN it, comment once, and
        return its number. A rolling ledger is machinery, not backlog: read as "no issue",
        a ledger closed in a backlog tidy makes the next sweep file a second one.
      - the issue does not exist                          -> WARN and return 0. That is a
        real state -- the repo has not filed it yet -- and the caller decides.

    An open exact-title match always wins over a closed one with the same title.
.PARAMETER Key
    Dotted binding key naming the title. Default: rolling_issues.drift_audit.
.PARAMETER ToolDir
    Directory holding ouro-binding.py. Default: this script's own directory.
.PARAMETER Title
    Skip the binding read and use this title (for tests).
.PARAMETER IssuesJson
    Skip the `gh` call and select from this JSON (for tests). Same injection idiom as
    Test-AgentReadyShape.ps1. A row without `state` counts as open.
.PARAMETER Reopen
    Replace the `gh` reopen-and-comment for a closed-only match (for tests). Called with
    the issue number and the comment body.
#>

function Get-RollingIssueNumber {
    param(
        [string]$Key = 'rolling_issues.drift_audit',
        [string]$ToolDir = $PSScriptRoot,
        [string]$Title,
        [string]$IssuesJson,
        [scriptblock]$Reopen = {
            param($Number, $Body)
            # One call: a reopen whose separate comment then failed would leave the ledger open
            # with no word on why, and no later run adds it.
            $out = gh issue reopen $Number --comment $Body 2>&1
            if ($LASTEXITCODE -ne 0) { throw "gh issue reopen $Number failed (exit $LASTEXITCODE): $out" }
        }
    )

    # Every gh call this function makes names the repository resolved here: the search below,
    # and the default -Reopen, which an injected -IssuesJson does not bypass.
    function Set-GhRepo {
        $found = Get-RepoSlug -ToolDir $ToolDir
        if (-not $found.Slug) { throw "no repository to address ($($found.Why)): a resolver that cannot name one has nowhere to search or post" }
        $env:GH_REPO = $found.Slug
    }

    # Named in the two WARN lines below only when it was actually read: with -Title passed, the
    # binding read this function would otherwise make never runs, and $Key still holds its
    # untouched default or whatever the caller passed alongside a -Title of their own.
    $namedByKey = -not $Title
    if (-not $Title) {
        $tool = Join-Path $ToolDir 'ouro-binding.py'
        if (-not (Test-Path -LiteralPath $tool)) {
            # No binding tool beside the script. WARN and return 0 rather than throw: on a
            # tree without it the caller previously posted using a hardcoded title and
            # SUCCEEDED whenever that title was the default, so throwing would turn a working
            # post into a red step that posts nothing -- a regression, not a fix. There is
            # still no safe default to guess, so the caller is told to pass -Title.
            # Vendor-Ouro.ps1 now copies the tool, so this path is the hand-assembled tree.
            Write-Host "WARN - no ouro-binding.py beside this script: cannot resolve $Key. Pass -Title (or -RollingIssueTitle on the gate) to post without the binding."
            return 0
        }
        # No binding at all is not a broken read: skip before python, from the root
        # ouro-binding.py resolves. Outside a work tree there is none, and the read throws.
        # git prints the root as UTF-8: decoded with a caller's OEM code page, a non-ASCII root
        # names no file, and the skip would fire in a repo that has a binding.
        $encoding = [Console]::OutputEncoding
        try {
            [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
            $top = (git rev-parse --show-toplevel 2>$null)
        }
        finally { [Console]::OutputEncoding = $encoding }
        if ($top) {
            $binding = Join-Path $top.Trim() '.claude/ouro.toml'
            if (-not (Test-Path -LiteralPath $binding -PathType Leaf)) {
                Write-Host "INFO - $Key not read (no $binding): no rolling issue to resolve; pass -Title (or -RollingIssueTitle on the gate) to post without a binding."
                return 0
            }
        }
        # ouro-binding.py prints UTF-8 too: decoded with a caller's OEM code page, a non-ASCII title
        # matches no issue, and the gate warns and posts nowhere.
        $encoding = [Console]::OutputEncoding
        try {
            [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
            $out = python3 $tool get $Key 2>&1
        }
        finally { [Console]::OutputEncoding = $encoding }
        if ($LASTEXITCODE -ne 0) {
            throw "ouro-binding.py get $Key failed (exit $LASTEXITCODE): $out"
        }
        # Only stdout is the value: under 2>&1 stderr arrives as ErrorRecords, and a python
        # that exits 0 may still write there (a DeprecationWarning, PYTHONDEVMODE, a
        # sitecustomize notice). Folding those into the title makes it match nothing, and the
        # gate then warns and posts nothing -- the exact silent no-op this file removes, one
        # layer down. Same filter both callers apply to their own gh reads.
        $Title = ((@($out) | Where-Object { $_ -is [string] }) -join "`n").Trim()
        if (-not $Title) { throw "$Key resolved to an empty title" }
    }
    $keyNote = if ($namedByKey) { " ($Key)" } else { '' }

    # Exact-title match, case included. `in:title` alone is a TOKEN search -- it matches any
    # issue whose title merely contains the same words, in any case -- so it is only a
    # prefilter, and the case-sensitive equality test below is what selects: `-eq` would let a
    # closed case-variant be reopened as the ledger. Filtered in PowerShell rather than jq so a
    # title with spaces or quotes needs no escaping. Every state, not just open: a closed ledger
    # searched open-only reads as missing, and the sweep that creates on a miss files a duplicate.
    if ($PSBoundParameters.ContainsKey('IssuesJson')) {
        $raw = $IssuesJson
    } else {
        Set-GhRepo
        # gh prints UTF-8 as well: decoded with a caller's OEM code page, a non-ASCII title equals no
        # row, and the sweep that creates on a miss files a duplicate.
        $encoding = [Console]::OutputEncoding
        try {
            [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
            # ponytail: --limit 200 bounds the token prefilter; closed look-alikes accumulate, raise it if a ledger title is that common.
            $raw = gh issue list --search "$Title in:title" --state all --limit 200 --json number,title,state
        }
        finally { [Console]::OutputEncoding = $encoding }
        if ($LASTEXITCODE -ne 0) {
            throw "gh issue list for '$Title' failed (exit $LASTEXITCODE): $raw"
        }
    }
    $hits = @(@($raw | ConvertFrom-Json) | Where-Object { $_.title -ceq $Title })
    $open = $hits | Where-Object { -not ($_.PSObject.Properties['state'] -and $_.state -eq 'CLOSED') } | Select-Object -First 1
    if ($open) { return [int]$open.number }

    $closed = $hits | Select-Object -First 1
    if ($closed) {
        $run = if ($env:GITHUB_RUN_ID) { "$env:GITHUB_SERVER_URL/$env:GITHUB_REPOSITORY/actions/runs/$env:GITHUB_RUN_ID" } else { 'a run outside GitHub Actions' }
        Write-Host "WARN - '$Title'$keyNote is closed: reopening #$($closed.number) rather than filing a duplicate."
        # An injected -IssuesJson bypassed the search, and with it the naming above; the default
        # -Reopen below is a gh write all the same.
        if ($PSBoundParameters.ContainsKey('IssuesJson') -and -not $PSBoundParameters.ContainsKey('Reopen')) { Set-GhRepo }
        $null = & $Reopen ([int]$closed.number) "Reopened by ${run}: a rolling ledger is machinery, not backlog. Closed, it reads as missing, and the next sweep files a second issue with this title."
        return [int]$closed.number
    }

    Write-Host "WARN - no issue titled '$Title'$keyNote, open or closed: a gate's comment has nowhere to go; a sweep that creates the issue continues."
    return 0
}
