<#
.SYNOPSIS
    Cross-checks GitHub repo variables against a doc's Repo variables table.
.DESCRIPTION
    Repo variables live in GitHub settings, not in git -- no diff-driven gate can
    see them change, so the weekly drift audit runs this deterministic sibling.
    Three finding kinds: an undocumented repo variable, a documented variable that
    no longer exists, and a value mismatch. Report-only: findings always exit 0
    (-Comment posts them to the rolling issue named by [rolling_issues].drift_audit); a FAILED
    gh variable list throws with its stderr instead of being classified.

    The variables are those of the repository the binding's [repo].slug names, or else of the one
    origin names (Get-RepoSlug.ps1). With neither, the gate reports that and reads nothing.
.PARAMETER VariablesJson
    JSON array [{name,value}] to check against (for tests). Default: gh variable list.
.PARAMETER DocPath
    The doc carrying the table, repo-relative or absolute. Required; the consumer's workflow passes it.
    Findings quote the path as given, so pass the repo-relative form when -Comment posts them.
.PARAMETER Comment
    Post findings as a comment on the rolling issue named by [rolling_issues].drift_audit,
    reopening that issue first if it is closed.
#>
param(
    [string]$VariablesJson = '',
    [string]$DocPath,
    [switch]$Comment,
    [string]$RollingIssueTitle
)

$ErrorActionPreference = 'Stop'

# The rolling issue's title comes from the binding, never a literal here.
. (Join-Path $PSScriptRoot 'Get-RollingIssue.ps1')
# The repository this gate's own gh calls name.
. (Join-Path $PSScriptRoot 'Get-RepoSlug.ps1')
if (-not $DocPath) { throw '-DocPath is required: the repo-relative or absolute doc carrying the Repo variables table' }
# git prints the root as UTF-8: decoded with a caller's OEM code page, a non-ASCII root names no
# directory, and a relative -DocPath does not resolve.
$encoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $RepoRoot = (git rev-parse --show-toplevel 2>$null)
}
finally { [Console]::OutputEncoding = $encoding }
if (-not $RepoRoot) { throw 'not inside a git work tree: run from the consumer repo' }
$RepoRoot = $RepoRoot.Trim()

# No -VariablesJson means the list below comes from gh, so the repository is named here -- and
# the comment that follows addresses the same one.
if (-not $VariablesJson) {
    $repo = Get-RepoSlug
    if (-not $repo.Slug) {
        Write-Host "::warning::repo-variable gate: no repo variable read ($($repo.Why)): a gate that cannot name its repository enforces nothing"
        exit 0
    }
    $env:GH_REPO = $repo.Slug
}

$vars = if ($VariablesJson) { $VariablesJson | ConvertFrom-Json }
        else {
            # A native failure doesn't throw under 'Stop': gh writes stderr, stdout stays
            # empty, and $null here classifies every documented variable as phantom.
            $raw = gh variable list --json name,value 2>&1
            if ($LASTEXITCODE -ne 0) { throw "gh variable list failed (exit $LASTEXITCODE): $raw" }
            # Only stdout is JSON: under 2>&1 stderr arrives as ErrorRecords, and a
            # successful gh may still write notices there (GH_DEBUG traces do).
            ($raw | Where-Object { $_ -is [string] }) -join "`n" | ConvertFrom-Json
        }

# Join-Path does not treat an absolute second argument as absolute, it concatenates -- and a caller
# whose work tree is reached through a junction hands us an absolute path. Fully
# qualified, not rooted: on Windows IsPathRooted is also true of `/docs/x.md`, the spelling a
# consumer reaches for to mean repo-relative, and that must keep resolving under the repo root.
$docFull = if ([IO.Path]::IsPathFullyQualified($DocPath)) { $DocPath } else { Join-Path $RepoRoot $DocPath }

# Rows of the '### Repo variables' table only -- the doc has other backticked tables.
$inSection  = $false
$documented = [ordered]@{}
foreach ($line in (Get-Content -LiteralPath $docFull -Encoding UTF8)) {
    if ($line -match '^###\s') { $inSection = [bool]($line -match '^###\s+Repo variables'); continue }
    if ($inSection -and $line -match '^\|\s*`([A-Z0-9_]+)`\s*\|\s*`([^`]*)`') {
        $documented[$Matches[1]] = $Matches[2]
    }
}

$findings = @()
foreach ($v in $vars) {
    if (-not $documented.Contains($v.name)) {
        $findings += "undocumented: repo variable ``$($v.name)`` = ``$($v.value)`` has no row in $DocPath"
    } elseif ($documented[$v.name] -ne $v.value) {
        $findings += "value drift: ``$($v.name)`` is ``$($v.value)`` in repo settings but ``$($documented[$v.name])`` in $DocPath"
    }
}
foreach ($name in $documented.Keys) {
    if (-not ($vars | Where-Object name -eq $name)) {
        $findings += "phantom: ``$name`` documented in $DocPath but no such repo variable exists"
    }
}

if ($findings.Count -eq 0) {
    Write-Host "Repo variables and $DocPath agree ($(@($vars).Count) variables, $($documented.Count) documented)."
    exit 0
}

Write-Host "$($findings.Count) repo-variable doc finding(s):"
$findings | ForEach-Object { Write-Host "  $_" }

if ($Comment) {
    $n = if ($RollingIssueTitle) { Get-RollingIssueNumber -Title $RollingIssueTitle }
         else                     { Get-RollingIssueNumber }
    if ($n) {
        $body = "**Repo-variable cross-check** (deterministic):`n`n" + (($findings | ForEach-Object { "- $_" }) -join "`n")
        gh issue comment $n --body $body | Out-Null
    }
}
exit 0
