<#
.SYNOPSIS
    Deterministic freshness gate for agent-ready issue anchors and doc code anchors.

.DESCRIPTION
    The agent-ready contract (ouro docs/contract.md) anchors issue claims on
    repo paths and short verbatim code fragments; "a fragment that no longer greps IS the
    staleness signal". This script checks that signal mechanically for every open issue
    labeled agent-ready:

      1. Backticked repo paths in the body (a single whitespace-free repo-relative token
         with / or \ and a file extension; command lines, prose spans, and rooted, home,
         parent-escaping, scheme- or drive-qualified tokens are not paths) must resolve
         at HEAD.
      2. Double-quoted fragments in the body (12 to 120 chars, following the contract's
         `File - Symbol - "fragment"` shape or free-standing, enclosing backticks
         stripped) must literal-grep (git grep -F). On an anchor line, a list item whose
         first backticked span names a tracked file, every fragment is grepped in that
         file; elsewhere a fragment that looks like code is grepped in the repo's tracked
         text files, and one with interior backticks that does not grep is a prose-code
         composite, skipped rather than flagged (Get-AnchorFindings.ps1).

    Findings are reported per issue. Default is report-only; -Comment posts the findings
    as a comment on each affected issue; -Demote additionally swaps agent-ready ->
    needs-ruling (off by default while the gate earns trust).

    A body with paths but no greppable quoted fragments gets an INFO line, not a finding -
    older issues predate the anchor convention; triage refreshes them over time.

    -Doc runs the same two checks over documentation files that use the anchor convention
    (the consumer's workflow names them). Doc findings emit GitHub warning annotations and count
    into the total; the exit code stays 0 while this half earns trust, same as -Demote.
    A doc anchor rots silently otherwise: the link checker proves the path exists, nothing
    proves the quoted fragment is still in it.

    The issues are those of the repository the binding's `[repo].slug` names, or else of the one
    `origin` names (Get-RepoSlug.ps1). With neither, no issue is read or written and the -Doc
    checks still run.

.EXAMPLE
    pwsh -File <ouro>/bin/Test-AgentReadyAnchors.ps1
    pwsh -File <ouro>/bin/Test-AgentReadyAnchors.ps1 -Comment
    pwsh -File <ouro>/bin/Test-AgentReadyAnchors.ps1 -Doc docs/anchored-doc.md
#>
param(
    [switch]$Comment,
    [switch]$Demote,
    [string[]]$Doc
)

$ErrorActionPreference = 'Stop'

# gh writes UTF-8. PowerShell decodes a native command's stdout with [Console]::OutputEncoding,
# which on a Windows runner is the OEM code page, so without this every non-ASCII character in
# an issue body -- an em dash, most often -- is mangled before it is compared against the tree.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$RepoRoot = (git rev-parse --show-toplevel 2>$null)
if (-not $RepoRoot) { throw 'not inside a git work tree: run from the consumer repo' }
$RepoRoot = $RepoRoot.Trim()

# The parser is shared with Test-AgentReadyShape.ps1 -- one definition of "anchor".
. (Join-Path $PSScriptRoot 'Get-AnchorFindings.ps1')
# The repository every gh call below names, shared with the gates that read the same backlog.
. (Join-Path $PSScriptRoot 'Get-RepoSlug.ps1')

Push-Location -LiteralPath $RepoRoot
try {
    $repo = Get-RepoSlug
    $issues = @()
    if ($repo.Slug) {
        $env:GH_REPO = $repo.Slug
        $issuesJson = gh issue list --label agent-ready --state open --limit 100 --json number,title,body,labels
        if ($LASTEXITCODE -ne 0) { throw "gh issue list failed (exit $LASTEXITCODE)" }
        $issues = @($issuesJson | ConvertFrom-Json)
        if ($issues.Count -ge 100) {
            Write-Host "::warning::anchor gate: the agent-ready list filled its --limit 100 page: an issue past it goes unchecked"
        }
        Write-Host "agent-ready open issues: $($issues.Count)"
    }
    else {
        Write-Host "::warning::anchor gate: no issue read or written ($($repo.Why)): a gate that cannot name its repository enforces nothing"
    }
    $totalFindings = 0

    foreach ($issue in $issues) {
        $result       = Get-AnchorFindings -Text $issue.body
        $findings     = $result.Findings
        $pathCount    = $result.PathCount
        $checkedFrags = $result.FragCount

        if ($findings.Count -gt 0) {
            $totalFindings += $findings.Count
            Write-Host "#$($issue.number) $($issue.title)" -ForegroundColor Yellow
            $findings | ForEach-Object { Write-Host "  STALE-ANCHOR: $_" -ForegroundColor Yellow }

            if ($Comment) {
                # The comment names the ouro release that ran: the version in the plugin.json of the tree
                # this script sits in, not of the repo it runs in. A vendored tree has none there and a
                # consumer plugin carries another name: both keep the short HEAD of the repo the gate
                # runs in, which for vendored scripts is their own version. So does a read or a field that
                # fails; both are inside the try, so a caller's strict mode cannot throw here. A vendored
                # copy in a consumer plugin that is itself named ouro shows that plugin's version.
                $ouroVersion = ''
                try {
                    $manifest = Get-Content -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) '.claude-plugin/plugin.json') -Raw | ConvertFrom-Json
                    if ($manifest.name -ceq 'ouro' -and $manifest.version -is [string]) { $ouroVersion = $manifest.version.Trim() }
                } catch { }
                $identity = if ($ouroVersion) { "ouro v$ouroVersion, Test-AgentReadyAnchors.ps1" } else { "Test-AgentReadyAnchors.ps1 @ $((git rev-parse --short HEAD).Trim())" }
                $bodyText = "Anchor gate ($identity): " +
                    "$($findings.Count) anchor(s) no longer verify:`n`n" +
                    (($findings | ForEach-Object { "- $_" }) -join "`n") +
                    "`n`nThe code moved or the item landed - re-triage with /ouro:triage $($issue.number) before executing (ouro contract)."
                gh issue comment $issue.number --body $bodyText | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "gh issue comment $($issue.number) failed (exit $LASTEXITCODE)" }
            }
            if ($Demote) {
                # Modifiers ride only on agent-ready and never stand alone (contract §4).
                # gh writes one edit's removal and addition independently, and a label the repository
                # lacks fails only its own half. So: remove first, naming only labels the issue carries,
                # then add. A failed add leaves no state label -- the label-invariants gate's safe-to-
                # restore finding -- never a rejected promotion still on agent-ready.
                $names = @($issue.labels | ForEach-Object { $_.name })
                $remove = @('agent-ready') + @('trivial', 'checkpoint' | Where-Object { $names -contains $_ })
                $removeFlags = @($remove | ForEach-Object { '--remove-label', $_ })
                gh issue edit $issue.number @removeFlags | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "gh issue edit $($issue.number) removing $($remove -join ', ') failed (exit $LASTEXITCODE): nothing has changed" }
                gh issue edit $issue.number --add-label needs-ruling | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "gh issue edit $($issue.number) adding needs-ruling failed (exit $LASTEXITCODE): the repository may lack the label, and the issue now carries no state label" }
                Write-Host "  demoted: agent-ready -> needs-ruling" -ForegroundColor Yellow
            }
        }
        elseif ($checkedFrags -eq 0 -and $pathCount -eq 0) {
            Write-Host "#$($issue.number): INFO - no greppable anchors in body (pre-convention issue; triage will refresh it)"
        }
        else {
            Write-Host "#$($issue.number): OK ($pathCount paths, $checkedFrags fragments)"
        }
    }

    foreach ($docPath in $Doc) {
        if (-not (Test-Path $docPath)) {
            Write-Host "::warning::anchor gate: doc not found: $docPath"
            continue
        }
        $result   = Get-AnchorFindings -Text (Get-Content $docPath -Raw)
        $findings = $result.Findings
        if ($findings.Count -gt 0) {
            $totalFindings += $findings.Count
            Write-Host "$docPath" -ForegroundColor Yellow
            foreach ($f in $findings) {
                Write-Host "  STALE-ANCHOR: $f" -ForegroundColor Yellow
                Write-Host "::warning file=$($docPath -replace '\\','/')::stale anchor: $f"
            }
        }
        else {
            Write-Host "${docPath}: OK ($($result.PathCount) paths, $($result.FragCount) fragments)"
        }
    }

    Write-Host "total stale-anchor findings: $totalFindings"
    # Report-only by design: the gate informs, the human (or triage) acts.
    exit 0
}
finally {
    Pop-Location
}
