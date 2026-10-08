<#
.SYNOPSIS
    Report-only gate: a change that moves a consumer-facing surface carries an unreleased
    changelog entry. Repo-agnostic: every repo-specific value is a parameter, and the
    shipped defaults name no repository.
.DESCRIPTION
    Diff-scoped, unlike every other gate here: the changed paths of
    `git diff --name-only <base>...HEAD` (three-dot, so the comparison is against the merge
    base) are classified against the surfaces a consumer's own files name, and when any of
    them moved, the changelog's unreleased section at HEAD must hold more bullets than the
    same section at the base, at least one of them new. A branch that bumps the version in its
    own PR moves the heading, so a section under a `## [version]` the base does not have counts
    as unreleased too; a heading is matched by its bracketed version, so a corrected date or
    title is the same release.

    The surfaces, each read from the file's content at both refs rather than from its path
    alone:

      skill front matter - the YAML block between the first two `---` lines of a SKILL.md,
        where a skill's invocation flags and its credential and tool requirements are stated.
      binding schema     - the region of the binding validator from its first value set through
        `validate()`, because a renamed or removed key, or a value `check` newly refuses, fails
        a consumer's `check`.
      script parameters  - the `param(` block and the `.PARAMETER` help of a gate script;
        its other help sections are not compared.
      script usage       - the usage lines of a python tool's module docstring, which are
        what a consumer's workflow step copies.
      shipped template   - any changed file under the templates a consumer copied.
      action definition  - any changed file that is an Action's own action.yml, because a
        consumer's workflow names it, and its inputs, in a `uses:` and a `with:` at a pinned tag.

    Separately, when -VersionPath's manifest exists at HEAD and its version has no `## ` heading
    in the changelog there, that is its own finding, release heading: a release version with no
    heading tells nobody what shipped. It is not cleared by an unreleased bullet, since the
    version needs the heading itself, not another line under [UNRELEASED]. -VersionPath ''
    turns the check off.

    Report-only: the run prints its findings and exits 0 whatever it finds. -Blocking makes a
    finding exit 1, so promoting the gate is a binding edit rather than a change here.

    The base defaults to origin/<[repo].default_branch> read from the binding. A repo with no
    binding, or a vendored tree with no ouro-binding.py beside this script, falls back to
    origin/master with an INFO line, as the docs-freshness gate falls back to its defaults; a
    repo that has a binding and the tool, with no python3 on PATH, stops the run naming that, as
    it already stops for a python3 older than 3.11. A base ref this clone does not have -- a
    shallow checkout, a throwaway clone -- is an INFO line and a clean exit: there is nothing to
    diff. On the default branch itself the diff is empty and the run is clean.
.PARAMETER Base
    The ref the diff is taken against. Default: origin/<[repo].default_branch>.
.PARAMETER RepoRoot
    Repo to read. Defaults to the git work tree of the working directory.
.PARAMETER ChangelogPath
    Repo-relative path of the changelog.
.PARAMETER UnreleasedHeading
    The heading line whose bullets are the unreleased entries, matched by its bracketed name: the
    spacing between `##` and `[`, and any text after the `]`, do not count.
.PARAMETER SkillPattern
    Regex over a changed path whose YAML front matter is a skill's declared interface.
.PARAMETER SchemaPath
    Repo-relative path of the binding validator that declares the schema.
.PARAMETER SchemaStartPattern
    Regex for the first line of the schema region in that file. The region is the binding surface:
    a renamed or removed key, or a value the check newly refuses, inside it obliges an entry.
.PARAMETER SchemaEndPattern
    Regex for the line the region ends at, so the binding surface (a renamed or removed key, or a
    value the check newly refuses) runs through it. The region takes that line's indented body: with
    one (a `def` block), the region runs through it and stops before the next non-blank line that
    starts at column 0;
    with none, the line is the region's own last line, as a one-line marker like today's
    `REQUIRED = (...)` reads. With the start marker found at neither ref, the whole file is
    compared instead, so a renamed declaration cannot silently disarm the check.
.PARAMETER ScriptPattern
    Regex over a changed path whose param block and .PARAMETER help are an interface.
.PARAMETER ModulePattern
    Regex over a changed path whose module docstring carries usage lines.
.PARAMETER UsageLinePattern
    Regex selecting the usage lines inside such a docstring.
.PARAMETER TemplatePattern
    Regex over a changed path that is a shipped template.
.PARAMETER ActionPattern
    Regex over a changed path that is an Action's own definition file.
.PARAMETER VersionPath
    Repo-relative path of a JSON manifest whose version names the release HEAD is at. An empty
    value turns the check off.
.PARAMETER Blocking
    Exit 1 on a finding. Default is report-only, exit 0.
.PARAMETER AsModule
    Dot-source the function definitions without running anything.
#>
param(
    [string]$Base,
    [string]$RepoRoot,
    [string]$ChangelogPath = 'CHANGELOG.md',
    [string]$UnreleasedHeading = '## [UNRELEASED]',
    [string]$SkillPattern = '^skills/[^/]+/SKILL\.md$',
    [string]$SchemaPath = 'bin/ouro-binding.py',
    [string]$SchemaStartPattern = '^POLICIES\s*=',
    [string]$SchemaEndPattern = '^def validate\(',
    [string]$ScriptPattern = '^bin/[^/]+\.ps1$',
    [string]$ModulePattern = '^bin/[^/]+\.py$',
    [string]$UsageLinePattern = '^\s*(usage\b|python3?\b|-{1,2}\w)',
    [string]$TemplatePattern = '^templates/',
    [string]$ActionPattern = '^actions/[^/]+/action\.ya?ml$',
    [string]$VersionPath = '.claude-plugin/plugin.json',
    [switch]$Blocking,
    [switch]$AsModule
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# $RepoRoot is resolved after the -AsModule guard below, not here: -AsModule promises to define
# the functions and run nothing, and a dot-source from outside a work tree must not throw.

# One surface class per line of the report, and the reason each finding carries.
$script:SurfaceReasons = [ordered]@{
    'skill front matter' = "a skill's invocation flags, credentials or tools are declared here"
    'binding schema'     = "a renamed or removed key, or a value the check newly refuses, fails a consumer's binding check"
    'script parameters'  = 'a consumer invokes this script by these parameters'
    'script usage'       = "a consumer's step copies these usage lines"
    'shipped template'   = 'a consumer copied this file into its own repo'
    'action definition'  = "a consumer's workflow names this Action and its inputs at a pinned tag"
    'release heading'    = 'the manifest names a version the changelog has no heading for'
}

# git prints UTF-8; decoded with a caller's OEM code page two different bytes collapse to the
# same replacement character, and a real change between the refs then compares equal.
function Invoke-Git {
    param([string]$Root, [string[]]$Arguments)
    $encoding = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $out = & git -C $Root -c core.quotepath=false @Arguments 2>$null
    }
    finally { [Console]::OutputEncoding = $encoding }
    [pscustomobject]@{ Code = $LASTEXITCODE; Lines = @($out) }
}

# $null when the path does not exist at that ref -- a file added or deleted by the change.
function Get-FileAtRef {
    param([string]$Root, [string]$Ref, [string]$Path)
    $r = Invoke-Git -Root $Root -Arguments @('show', "${Ref}:${Path}")
    if ($r.Code -ne 0) { return $null }
    return $r.Lines
}

function Get-FrontMatter {
    param([string[]]$Lines)
    $lines = @($Lines)
    if ($lines.Count -eq 0 -or "$($lines[0])".Trim() -ne '---') { return '' }
    for ($i = 1; $i -lt $lines.Count; $i++) {
        if ("$($lines[$i])".Trim() -eq '---') {
            if ($i -le 1) { return '' }
            return (@($lines[1..($i - 1)]) -join "`n")
        }
    }
    return ''
}

# The param block comes from the PowerShell parser, not a brace count: a parenthesis inside a
# default value's string or comment makes a counted scan end the block in the wrong place.
function Get-ScriptInterface {
    param([string[]]$Lines)
    $text = (@($Lines) -join "`n")
    $ast = [System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$null, [ref]$null)
    $block = if ($ast -and $ast.ParamBlock) { $ast.ParamBlock.Extent.Text } else { '' }
    $help = @()
    $section = ''
    foreach ($line in @($Lines)) {
        if ($line -match '^\s*\.(SYNOPSIS|DESCRIPTION|PARAMETER|EXAMPLE|INPUTS|OUTPUTS|NOTES|LINK|COMPONENT|ROLE|FUNCTIONALITY)\b') {
            $section = $Matches[1].ToUpperInvariant()
        }
        elseif ($line -match '^\s*#>') { $section = '' }
        if ($section -eq 'PARAMETER') { $help += $line.TrimEnd() }
    }
    (@($block) + $help) -join "`n"
}

function Get-SchemaRegion {
    param([string[]]$Lines, [string]$StartPattern, [string]$EndPattern)
    $region = @()
    # before -> in (the value sets and declarations) -> body (the end marker's own indented
    # block, if it has one) -> done.
    $state = 'before'
    foreach ($line in @($Lines)) {
        $text = "$line"
        if ($state -eq 'before') {
            if ($text -notmatch $StartPattern) { continue }
            $state = 'in'
        }
        if ($state -eq 'done') { continue }
        if ($state -eq 'in') {
            $region += $text.TrimEnd()
            if ($text -match $EndPattern) { $state = 'body' }
            continue
        }
        # state 'body': a blank or indented line still belongs to it; a line back at column 0
        # ends it, and does not belong. A one-line marker with no such lines after it ends here
        # too, once the trailing-blank trim below drops what this loop tentatively kept.
        if ($text.Trim() -eq '' -or $text -match '^\s') { $region += $text.TrimEnd() }
        else { $state = 'done' }
    }
    while ($region.Count -gt 0 -and $region[$region.Count - 1] -eq '') {
        $region = if ($region.Count -gt 1) { @($region[0..($region.Count - 2)]) } else { @() }
    }
    $region -join "`n"
}

function Get-ModuleUsage {
    param([string[]]$Lines, [string]$Pattern)
    $lines = @($Lines)
    $start = -1
    $quote = $null
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = "$($lines[$i])"
        if ($line -match '"""' -or $line -match "'''") {
            $start = $i
            $quote = if ($line -match '"""') { '"""' } else { "'''" }
            break
        }
        if ($line.Trim() -and $line -notmatch '^\s*#') { return '' }  # code before any docstring
    }
    if ($start -lt 0) { return '' }
    $quotePattern = [regex]::Escape($quote)
    $body = @("$($lines[$start])" -replace "^.*?$quotePattern", '')
    if ($start + 1 -lt $lines.Count) { $body += @($lines[($start + 1)..($lines.Count - 1)]) }
    $usage = @()
    foreach ($line in $body) {
        $text = "$line"
        $closed = $text -match $quotePattern
        if ($closed) { $text = ($text -split $quotePattern)[0] }
        if ($text -match $Pattern) { $usage += $text.TrimEnd() }
        if ($closed) { break }
    }
    $usage -join "`n"
}

# A heading's key is its bracketed version, so a corrected date or title is the same release;
# a heading with no bracket is keyed by its whole line.
function Get-HeadingKey {
    param([string]$Line)
    $t = "$Line".Trim()
    if ($t -match '^##\s*(\[[^\]]*\])') { return "## $($Matches[1])" }
    return $t
}

# The keys of a changelog's `## ` headings: what the base already released.
function Get-ReleaseHeadings {
    param([string[]]$Lines)
    @(@($Lines) | Where-Object { "$_" -match '^##\s' } | ForEach-Object { Get-HeadingKey -Line $_ })
}

# The bullets under the unreleased heading, and under any `## ` heading the base does not have:
# a branch that lands alone bumps in its own PR, so its entries sit under the version it names.
function Get-UnreleasedBullets {
    param([string[]]$Lines, [string]$Heading, [string[]]$KnownHeadings = @())
    $bullets = @()
    $inSection = $false
    $headingKey = Get-HeadingKey -Line $Heading
    foreach ($line in @($Lines)) {
        $text = "$line"
        if ($text -match '^##\s') {
            $inSection = ((Get-HeadingKey -Line $text) -eq $headingKey) -or ((Get-HeadingKey -Line $text) -notin @($KnownHeadings))
            continue
        }
        if (-not $inSection) { continue }
        if ($text -match '^\s*[-*]\s+\S') { $bullets += $text.Trim() }
    }
    $bullets
}

function New-Finding {
    param([string]$File, [string]$Surface)
    if (-not $SurfaceReasons.Contains($Surface)) { throw "unknown surface class '$Surface'" }
    [pscustomobject]@{ File = $File; Surface = $Surface; Reason = $SurfaceReasons[$Surface] }
}

# One changed path may move two surfaces -- the binding validator is both a schema and a python
# tool with usage lines -- so each test stands on its own rather than in an elseif chain.
function Get-SurfaceFindings {
    param([string]$Root, [string]$BaseRef, [string[]]$ChangedPaths)
    $findings = @()
    foreach ($path in @($ChangedPaths)) {
        $baseLines = $null
        $headLines = $null
        $needsContent = ($path -match $SkillPattern) -or ($path -eq $SchemaPath) -or
                        ($path -match $ScriptPattern) -or ($path -match $ModulePattern)
        if ($needsContent) {
            $baseLines = Get-FileAtRef -Root $Root -Ref $BaseRef -Path $path
            $headLines = Get-FileAtRef -Root $Root -Ref 'HEAD'   -Path $path
        }
        if ($path -match $SkillPattern) {
            if ((Get-FrontMatter -Lines $baseLines) -ne (Get-FrontMatter -Lines $headLines)) {
                $findings += New-Finding -File $path -Surface 'skill front matter'
            }
        }
        if ($path -eq $SchemaPath) {
            $b = Get-SchemaRegion -Lines $baseLines -StartPattern $SchemaStartPattern -EndPattern $SchemaEndPattern
            $h = Get-SchemaRegion -Lines $headLines -StartPattern $SchemaStartPattern -EndPattern $SchemaEndPattern
            # Neither ref carries the start marker: the declaration was renamed or this is
            # another file under that path. Compare it whole rather than two empty strings.
            if (-not $b -and -not $h) {
                $b = (@($baseLines) -join "`n")
                $h = (@($headLines) -join "`n")
            }
            if ($b -ne $h) { $findings += New-Finding -File $path -Surface 'binding schema' }
        }
        if ($path -match $ScriptPattern) {
            if ((Get-ScriptInterface -Lines $baseLines) -ne (Get-ScriptInterface -Lines $headLines)) {
                $findings += New-Finding -File $path -Surface 'script parameters'
            }
        }
        if ($path -match $ModulePattern) {
            $b = Get-ModuleUsage -Lines $baseLines -Pattern $UsageLinePattern
            $h = Get-ModuleUsage -Lines $headLines -Pattern $UsageLinePattern
            if ($b -ne $h) { $findings += New-Finding -File $path -Surface 'script usage' }
        }
        if ($path -match $TemplatePattern) {
            $findings += New-Finding -File $path -Surface 'shipped template'
        }
        if ($path -match $ActionPattern) {
            $findings += New-Finding -File $path -Surface 'action definition'
        }
    }
    $findings
}

if ($AsModule) { return }

# The repo root comes from the working directory, never from this script's own location: the
# script ships in a plugin checkout that lives inside, beside, or nowhere near the repo it
# reads. Same idiom as every other gate here.
if (-not $RepoRoot) {
    $r = Invoke-Git -Root '.' -Arguments @('rev-parse', '--show-toplevel')
    if ($r.Code -ne 0 -or -not $r.Lines) { throw 'not inside a git work tree: run from the consumer repo, or pass -RepoRoot' }
    $RepoRoot = "$($r.Lines[0])".Trim()
}

if (-not $Base) {
    $bindingFile = Join-Path $RepoRoot '.claude/ouro.toml'
    $bindingTool = Join-Path $PSScriptRoot 'ouro-binding.py'
    $branch = $null
    if ((Test-Path -LiteralPath $bindingFile) -and (Test-Path -LiteralPath $bindingTool) -and
        (Get-Command python3 -ErrorAction SilentlyContinue)) {
        $encoding = [Console]::OutputEncoding
        try {
            [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
            $raw = python3 $bindingTool get repo.default_branch $bindingFile 2>&1
        }
        finally { [Console]::OutputEncoding = $encoding }
        if ($LASTEXITCODE -ne 0) {
            throw "ouro-binding.py get repo.default_branch failed (exit $LASTEXITCODE): $(((@($raw) | ForEach-Object { "$_" }) -join "`n").Trim())"
        }
        $branch = ((@($raw) | Where-Object { $_ -is [string] }) -join "`n").Trim()
    }
    if (-not $branch) {
        $why = if (-not (Test-Path -LiteralPath $bindingFile)) { "no $bindingFile" }
               elseif (-not (Test-Path -LiteralPath $bindingTool)) { 'no ouro-binding.py beside this script' }
               elseif (-not (Get-Command python3 -ErrorAction SilentlyContinue)) { throw "no python3 on PATH, and $bindingFile is a binding: a repo that has one needs python3 3.11+ on PATH, because its [repo].default_branch is read through ouro-binding.py" }
               else { 'repo.default_branch is empty' }
        $branch = 'master'
        Write-Host "INFO - [repo].default_branch not read ($why): diffing against origin/$branch"
    }
    $Base = "origin/$branch"
}

if ((Invoke-Git -Root $RepoRoot -Arguments @('rev-parse', '--verify', '--quiet', "$Base^{commit}")).Code -ne 0) {
    Write-Host "INFO - $Base is not a ref in this clone: nothing to diff"
    exit 0
}

$diff = Invoke-Git -Root $RepoRoot -Arguments @('diff', '--name-only', "$Base...HEAD")
if ($diff.Code -ne 0) { throw "git diff --name-only $Base...HEAD failed (exit $($diff.Code))" }
$changed = @($diff.Lines | ForEach-Object { "$_".Trim() } | Where-Object { $_ })

$findings = @(Get-SurfaceFindings -Root $RepoRoot -BaseRef $Base -ChangedPaths $changed)

# A base with no changelog makes every bullet at HEAD a new one.
$baseLines = Get-FileAtRef -Root $RepoRoot -Ref $Base -Path $ChangelogPath
$headChangelogLines = Get-FileAtRef -Root $RepoRoot -Ref 'HEAD' -Path $ChangelogPath
$known = @(Get-ReleaseHeadings -Lines $baseLines)
$baseBullets = @(Get-UnreleasedBullets -Lines $baseLines -Heading $UnreleasedHeading -KnownHeadings $known)
$headBullets = @(Get-UnreleasedBullets -Lines $headChangelogLines -Heading $UnreleasedHeading -KnownHeadings $known)
$newBullets = @($headBullets | Where-Object { $_ -notin $baseBullets })

# More bullets than at the base, one of them new: a reworded bullet alone is no entry.
if ($newBullets.Count -gt 0 -and $headBullets.Count -gt $baseBullets.Count) { $findings = @() }

# A release version with no changelog heading, checked after the rule above so a new unreleased
# bullet never silences it: the version needs its own heading, not another [UNRELEASED] line.
if ($VersionPath) {
    $manifestLines = Get-FileAtRef -Root $RepoRoot -Ref 'HEAD' -Path $VersionPath
    if ($manifestLines) {
        $version = $null
        try {
            $manifest = (@($manifestLines) -join "`n") | ConvertFrom-Json -ErrorAction Stop
            if ($manifest -and ($manifest.PSObject.Properties.Name -contains 'version') -and
                ($manifest.version -is [string]) -and $manifest.version) {
                $version = $manifest.version
            }
        }
        catch { }
        if ($version) {
            $headHeadings = @(Get-ReleaseHeadings -Lines $headChangelogLines)
            $wantKeys = @("## [v$version]", "## [$version]")
            if (-not (@($headHeadings) | Where-Object { $_ -in $wantKeys })) {
                $findings += New-Finding -File $VersionPath -Surface 'release heading'
            }
        }
        else {
            Write-Host "INFO - ${VersionPath}: no string 'version' at HEAD"
        }
    }
}

$bySurface = @($findings | Group-Object Surface | Sort-Object Name)
foreach ($g in $bySurface) {
    Write-Output ("--- {0} ({1}) ---" -f $g.Name, $g.Count)
    foreach ($f in ($g.Group | Sort-Object File)) {
        Write-Output ("  {0}  -> {1}  ({2})" -f $f.File, $f.Surface, $f.Reason)
    }
}

Write-Output ''
foreach ($g in $bySurface) { Write-Output ("  {0}: {1}" -f $g.Name, $g.Count) }
Write-Output ("total: {0} finding(s) across {1} changed file(s), {2} new unreleased entry(s)" -f
    $findings.Count, $changed.Count, $newBullets.Count)

if ($Blocking -and $findings.Count -gt 0) { exit 1 }
exit 0
