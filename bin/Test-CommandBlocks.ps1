<#
.SYNOPSIS
    Checks the command blocks a skill or agent doc tells an agent to run.
.DESCRIPTION
    A fenced bash or sh block under a skill or an agent doc is executed text: an agent copies it
    out and runs it, so its bytes are as much a change as any other executable line, yet nothing
    else reads them. Two defects landed that way; this gate is the re-scoped check
    the owner ruled for on 2026-09-26, after three fix rounds on a wider design each bred a new
    false-positive class. There is no rule here for a `gh` call that names no repository.

    The gate extracts every fenced block whose info string is exactly `bash` or `sh` from every
    SKILL.md under skills/ and every .md file under agents/, then does two things to each block,
    independent of one another:

      parse - replaces each placeholder with the plain word PLACEHOLDER and hands the result to
        `bash -n`. A placeholder is a `<` followed by a letter or `[`, through the next `>` on
        the same line, with no angle bracket between them, of any length: `<owner>/<repo>` and a
        whole sentence like `<step 0's merge answer: squash or merge>` both become one word, so
        neither an embedded apostrophe nor two expansions sharing a line trips the parser.

        Where no bash that actually runs is on PATH, this half prints an INFO line and is
        skipped; the grep half below still runs and still decides the exit code. A launcher with
        no shell behind it -- on Windows, bash on PATH can be the WSL launcher at
        C:\Windows\System32\bash.exe with no distro installed -- counts as no bash: every `bash`
        found on PATH is proven with `bash -c 'exit 0'` before it is trusted, and where more than
        one proves out, a Git Bash is preferred over a bare WSL one.

      grep - reads each of the block's lines as text, quotes and comments included, against a
        small table of environment-resolved-repository forms: `git remote` used to read a remote
        (bare, `-v`/`--verbose`, `get-url`, `show`; any other subcommand or option, such as `add`,
        `rm` or `--help`, is not this), `gh repo set-default`, and `GH_REPO`, each matched with its
        case. A new row is one entry in the table
        below and one fixture case; a finding here is cleared by rewording the line, not by
        restructuring the shell.

    A finding prints as `path:line: rule -- why`, with the offending line under it. With none,
    the gate prints the block and document count and exits 0.
.PARAMETER RepoRoot
    Repo to scan. Defaults to the git work tree of the working directory.
.PARAMETER Globs
    Repo-relative patterns of the documents whose blocks are checked. A pattern with no
    directory part matches in the repo root; one with a directory part matches at any depth
    under its first path segment.
.EXAMPLE
    pwsh -File <ouro>/bin/Test-CommandBlocks.ps1
#>
param(
    [string]$RepoRoot,
    [string[]]$Globs = @('skills/*/SKILL.md', 'agents/*.md')
)

$ErrorActionPreference = 'Stop'

# A placeholder is a less-than sign followed by a letter or an opening bracket, through the next
# greater-than sign on the same line (the character class excludes both angle brackets and the
# line terminators, so a match can never cross a line or swallow a second placeholder).
$script:PlaceholderPattern = '<[A-Za-z\[][^<>\r\n]*>'

# The hazard table: one row per rule, matched case-sensitively against each block line as written.
# `git remote` counts only in its read forms: bare (no subcommand or option word after it), -v or
# --verbose, get-url, show.
$script:Hazards = @(
    @{ Rule = 'git-remote'
       Why  = 'reads a remote from git remote (bare, -v/--verbose, get-url, show) instead of the binding'
       Pattern = '(?<![\w-])git\s+remote(?:\s+(?:-v|--verbose|get-url|show)(?![\w-])|(?!\s+[A-Za-z-]))' }
    @{ Rule = 'gh-set-default'
       Why  = 'gh repo set-default changes the environment''s default repository instead of reading the binding'
       Pattern = '(?<![\w-])gh\s+repo\s+set-default\b' }
    @{ Rule = 'gh-repo-env'
       Why  = 'GH_REPO reads the repository from the environment instead of the binding'
       Pattern = '(?<![\w-])GH_REPO\b' }
)

# Documents matching each pattern: under its first path segment, or in the root itself when the
# pattern has no directory part.
function Get-BlockDocs {
    param([string]$Root, [string[]]$Patterns)
    $found = [ordered]@{}
    foreach ($g in $Patterns) {
        $nested = $g.Contains('/')
        $dir = if ($nested) { Join-Path $Root ($g -split '/')[0] } else { $Root }
        $how = if ($nested) { [IO.SearchOption]::AllDirectories } else { [IO.SearchOption]::TopDirectoryOnly }
        if (-not [IO.Directory]::Exists($dir)) { continue }
        foreach ($f in [IO.Directory]::EnumerateFiles($dir, '*', $how)) {
            $rel = $f.Substring($Root.Length).TrimStart('/', '\') -replace '\\', '/'
            if ($rel -like $g) { $found[$rel] = $true }
        }
    }
    @($found.Keys)
}

# Each fenced bash/sh block as the 1-based line of its opening fence and its body lines. The
# info string must be exactly bash or sh; a fence may sit indented inside a numbered list.
function Get-CommandBlocks {
    param([string[]]$Lines)
    $blocks = @(); $fence = 0; $marker = ''; $body = @()
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        $t = $Lines[$i].Trim()
        if (-not $fence) {
            $m = [regex]::Match($t, '^(```|~~~)(bash|sh)\s*$')
            if ($m.Success) { $fence = $i + 1; $marker = $m.Groups[1].Value; $body = @() }
            continue
        }
        if ($t -match "^$([regex]::Escape($marker))\s*$") {
            $blocks += [pscustomobject]@{ Fence = $fence; Body = $body }; $fence = 0; continue
        }
        $body += $Lines[$i]
    }
    @($blocks)
}

# The bash on PATH to parse with, or $null where none actually runs. A launcher (the WSL stub
# with no distro behind it) fails the probe below and is never returned; where more than one
# candidate proves out, a Git Bash is preferred over a bare WSL one.
function Get-WorkingBash {
    $seen = @{}
    $working = @()
    foreach ($cmd in (Get-Command bash -All -ErrorAction SilentlyContinue)) {
        $path = $cmd.Source
        if (-not $path -or $seen.ContainsKey($path)) { continue }
        $seen[$path] = $true
        $eap = $ErrorActionPreference
        $ok = $false
        try {
            $ErrorActionPreference = 'Continue'
            & $path -c 'exit 0' 2>&1 | Out-Null
            $ok = ($LASTEXITCODE -eq 0)
        }
        catch { $ok = $false }
        finally { $ErrorActionPreference = $eap }
        if ($ok) { $working += $path }
    }
    $working | Sort-Object { if ($_ -match '[\\/][Gg]it[\\/]') { 0 } else { 1 } } | Select-Object -First 1
}

if (-not $RepoRoot) {
    # git prints the root as UTF-8: decoded with the caller's OEM code page, a non-ASCII root
    # names no directory and nothing is scanned.
    $encoding = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $RepoRoot = (git rev-parse --show-toplevel 2>$null)
    }
    finally { [Console]::OutputEncoding = $encoding }
    if (-not $RepoRoot) { throw 'not inside a git work tree: run from the repo root, or pass -RepoRoot' }
    $RepoRoot = $RepoRoot.Trim()
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

$bash = Get-WorkingBash
if (-not $bash) {
    Write-Host 'INFO: no bash that runs is on PATH -- the parse half is skipped; the grep rules still run'
}

$findings = @()
$blockCount = 0
$docs = @(Get-BlockDocs -Root $RepoRoot -Patterns $Globs)
foreach ($rel in $docs) {
    $lines = @(Get-Content -LiteralPath (Join-Path $RepoRoot $rel) -Encoding UTF8)
    foreach ($block in (Get-CommandBlocks -Lines $lines)) {
        $blockCount++

        for ($idx = 0; $idx -lt $block.Body.Count; $idx++) {
            $lineText = $block.Body[$idx]
            foreach ($h in $script:Hazards) {
                if ($lineText -cmatch $h.Pattern) {
                    $findings += [pscustomobject]@{
                        File = $rel; Line = $block.Fence + $idx + 1; Rule = $h.Rule
                        Why  = $h.Why; Text = $lineText.Trim()
                    }
                }
            }
        }

        if (-not $bash) { continue }

        # The block goes to bash on stdin. PowerShell ends piped text with a newline of its own
        # (a CRLF on Windows), so the block ends in a comment line: that carriage return would
        # otherwise read as the command a block ending mid-pipeline is missing.
        $src = (($block.Body -join "`n") -replace $script:PlaceholderPattern, 'PLACEHOLDER') + "`n#"
        $eap = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $stderr = ($src | & $bash -n 2>&1 | Out-String)
            $rc = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $eap }
        if ($rc -eq 0) { continue }

        # bash numbers the block's own lines; the finding names the document's. An error at the
        # end of the input reads past the last line, so the block's own last line bounds it.
        $m = [regex]::Match($stderr, 'line (\d+)')
        $ln = if ($m.Success) { [Math]::Min([int]$m.Groups[1].Value, $block.Body.Count) } else { $block.Body.Count }
        $reason = (($stderr -split "`n" | Where-Object { $_.Trim() } | Select-Object -First 1) -replace '^[^:]*: line \d+: ', '').Trim()
        $findings += [pscustomobject]@{
            File = $rel; Line = $block.Fence + $ln; Rule = 'parse'
            Why  = $reason; Text = $block.Body[$ln - 1].Trim()
        }
    }
}

if (-not $findings) {
    Write-Host "$blockCount command block(s) in $($docs.Count) document(s): no findings."
    exit 0
}
Write-Host "$($findings.Count) command-block finding(s):"
foreach ($f in ($findings | Sort-Object File, Line, Rule)) {
    Write-Host "  $($f.File):$($f.Line): $($f.Rule) -- $($f.Why)"
    if ($f.Text) { Write-Host "      $($f.Text)" }
}
exit 1
