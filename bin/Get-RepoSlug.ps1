<#
.SYNOPSIS
    Resolve the repository a gate's gh calls must name. Dot-source, then call Get-RepoSlug.
.DESCRIPTION
    A library, not a gate: this file defines one function and runs nothing on its own, so it is
    dot-sourced rather than invoked. A caller sets `$env:GH_REPO` from the slug once, before its
    first gh call -- gh reads that variable "for commands that otherwise operate on a local
    repository" (gh help environment), so one line covers every call in the file, the array
    forms included. A command that operates on the local repository picks among the clone's
    remotes, which in a fork's clone is the parent, and a gate then reads and writes there.

    That assignment replaces a `$env:GH_REPO` its caller had set, and leaves it replaced: the
    binding and `origin` are the two sources, so a value from anywhere else is not one of them.

    Two sources, in order:

      - `[repo].slug` from `.claude/ouro.toml` at the working directory's repo root. A binding
        that is there and does not read THROWS: python3 3.11+ is required of a repo that has one,
        and resolving something else would name another repository.
      - else the repository `origin` names, which gh resolves from origin's own URL. Only a URL
        carrying a host or a path is asked about, and never one spelled the way gh spells a
        repository: git takes a relative path as a remote URL, and gh reads `octocat/Hello-World`
        as that owner's repository.

    Neither resolves: `Slug` is empty and `Why` names both halves, for a caller that must then
    make no gh call. No message carries the URL -- a remote can carry a credential in it, and a
    gate's output is a build log.
.PARAMETER ToolDir
    Directory holding ouro-binding.py. Default: this script's own directory.
#>


function Get-RepoSlug {
    param([string]$ToolDir = $PSScriptRoot)

    # git, python and gh all print UTF-8, which PowerShell decodes with [Console]::OutputEncoding
    # -- the OEM code page on a Windows runner. The caller's encoding comes back after the reads:
    # a gate runs in-process too.
    $encoding = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

        $tool = Join-Path $ToolDir 'ouro-binding.py'
        $top = (git rev-parse --show-toplevel 2>$null)
        $binding = if ($top) { Join-Path $top.Trim() '.claude/ouro.toml' } else { '' }
        $bindingWhy =
            if (-not (Test-Path -LiteralPath $tool)) { 'no ouro-binding.py beside this script' }
            elseif (-not $top) { 'not inside a git work tree' }
            elseif (-not (Test-Path -LiteralPath $binding -PathType Leaf)) { "no $binding" }
            else { '' }
        if (-not $bindingWhy) {
            # Only stdout is the value: under 2>&1 stderr arrives as ErrorRecords, and a python
            # that exits 0 may still write there.
            $out = python3 $tool get repo.slug 2>&1
            if ($LASTEXITCODE -ne 0) { throw "ouro-binding.py get repo.slug failed (exit $LASTEXITCODE): $out" }
            $slug = ((@($out) | Where-Object { $_ -is [string] }) -join "`n").Trim()
            if (-not $slug) { throw 'repo.slug resolved to an empty value' }
            return [pscustomobject]@{ Slug = $slug; Why = '' }
        }

        # origin by name: a bare `gh repo view` picks among all the clone's remotes, so in a
        # fork's clone it answers the parent -- the repository this one only contributes to.
        $url = "$(@(git remote get-url origin 2>$null) | Select-Object -First 1)".Trim()
        $originWhy =
            if ($LASTEXITCODE -ne 0 -or -not $url) { 'the clone has no origin remote with a URL' }
            elseif ($url -notmatch '[:/\\]') { "origin's URL names no host or path" }
            elseif ($url -match '^(?:[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*/)?[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$') {
                "origin's URL is spelled the way gh spells a repository, so gh would read it as that repository"
            }
            else { '' }
        if (-not $originWhy) {
            $out = gh repo view $url --json nameWithOwner --jq .nameWithOwner 2>&1
            $slug = ((@($out) | Where-Object { $_ -is [string] }) -join "`n").Trim()
            if ($LASTEXITCODE -eq 0 -and $slug) { return [pscustomobject]@{ Slug = $slug; Why = '' } }
            $originWhy = "gh could not name the repository origin's URL points at"
        }
        return [pscustomobject]@{ Slug = ''; Why = "$bindingWhy, and $originWhy" }
    }
    finally { [Console]::OutputEncoding = $encoding }
}
