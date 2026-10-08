<#
.SYNOPSIS
    Audits the state-label invariant across open GitHub issues.
.DESCRIPTION
    Every open issue carries exactly one state label (ouro docs/contract.md).
    Labels live in GitHub, not in git, so no diff-driven gate can see
    one drift -- the weekly drift audit runs this deterministic sibling, alongside
    the repo-variable cross-check.

    Three finding kinds:
      missing   -- no state label at all (invisible: in no working view, no queue)
      conflict  -- two or more state labels (the backlog says two things at once)
      modifier  -- trivial/checkpoint riding on something that is not agent-ready

    Deliberately report-only, and deliberately not self-healing. Restoring a
    missing label is safe, but picking which of two conflicting states to drop
    is a judgement the script cannot make, so it reports all three kinds and
    leaves every one to a human. Findings always exit 0 (-Comment posts to the
    rolling issue named by [rolling_issues].drift_audit); a FAILED gh issue list throws with its
    stderr instead of reporting a clean empty backlog.

    The issues are those of the repository the binding's [repo].slug names, or else of the one
    origin names (Get-RepoSlug.ps1). With neither, the gate reports that and reads nothing.
.PARAMETER IssuesJson
    JSON array [{number,title,labels:[{name}]}] to check (for tests).
    Default: gh issue list.
.PARAMETER Comment
    Post findings as a comment on the rolling issue named by [rolling_issues].drift_audit,
    reopening that issue first if it is closed.
#>
param(
    [string]$IssuesJson = '',
    [switch]$Comment,
    [string]$RollingIssueTitle
)

$ErrorActionPreference = 'Stop'

# The rolling issue's title comes from the binding, never a literal here.
. (Join-Path $PSScriptRoot 'Get-RollingIssue.ps1')
# The repository this gate's own gh calls name.
. (Join-Path $PSScriptRoot 'Get-RepoSlug.ps1')

# gh writes UTF-8. PowerShell decodes a native command's stdout with [Console]::OutputEncoding,
# which on a Windows runner is the OEM code page, so without this every non-ASCII character in
# an issue body -- an em dash, most often -- is mangled before it is compared against the tree.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$States    = @('agent-ready','human-ready','needs-ruling','blocked','needs-triage','idea','umbrella','architecture')
$Modifiers = @('trivial','checkpoint')

# No -IssuesJson means the list below comes from gh, so the repository is named here -- and the
# comment that follows addresses the same one.
if (-not $IssuesJson) {
    $repo = Get-RepoSlug
    if (-not $repo.Slug) {
        Write-Host "::warning::state-label gate: no issue read ($($repo.Why)): a gate that cannot name its repository enforces nothing"
        exit 0
    }
    $env:GH_REPO = $repo.Slug
}

$issues = if ($IssuesJson) { $IssuesJson | ConvertFrom-Json }
          else {
              # A native failure doesn't throw under 'Stop': unguarded, a broken token would
              # report a clean empty backlog and exit 0. Only stdout is JSON.
              $raw = gh issue list --state open --limit 500 --json number,title,labels 2>&1
              if ($LASTEXITCODE -ne 0) { throw "gh issue list failed (exit $LASTEXITCODE): $raw" }
              $live = ($raw | Where-Object { $_ -is [string] }) -join "`n" | ConvertFrom-Json
              if (@($live).Count -ge 500) {
                  Write-Host "::warning::state-label gate: the open-issue list filled its --limit 500 page: an issue past it goes unchecked"
              }
              $live
          }

$findings = @()
foreach ($i in $issues) {
    $names  = @($i.labels | ForEach-Object { $_.name })
    $onIt   = @($names | Where-Object { $States -contains $_ })
    $mods   = @($names | Where-Object { $Modifiers -contains $_ })

    if ($onIt.Count -eq 0) {
        $findings += "missing: #$($i.number) carries no state label -- $($i.title)"
    } elseif ($onIt.Count -gt 1) {
        $findings += "conflict: #$($i.number) carries $($onIt.Count) state labels (``$($onIt -join '`, `')``) -- $($i.title)"
    }

    # A modifier is meaningless without the state it rides on: trivial pre-authorizes
    # the agent-ready loop, checkpoint redirects its deliverable. Neither means
    # anything on an issue no agent will pick up.
    if ($mods.Count -gt 0 -and $onIt -notcontains 'agent-ready') {
        $findings += "modifier: #$($i.number) carries ``$($mods -join '`, `')`` without ``agent-ready`` -- $($i.title)"
    }
}

if ($findings.Count -eq 0) {
    Write-Host "State-label invariant holds across $(@($issues).Count) open issue(s)."
    exit 0
}

Write-Host "$($findings.Count) state-label finding(s) across $(@($issues).Count) open issue(s):"
$findings | ForEach-Object { Write-Host "  $_" }

if ($Comment) {
    $n = if ($RollingIssueTitle) { Get-RollingIssueNumber -Title $RollingIssueTitle }
         else                     { Get-RollingIssueNumber }
    if ($n) {
        $body = "**Issue state-label invariant** (deterministic):`n`n" +
                (($findings | ForEach-Object { "- $_" }) -join "`n") +
                "`n`nEvery open issue carries exactly one state label -- the ouro contract."
        gh issue comment $n --body $body | Out-Null
    }
}
exit 0
