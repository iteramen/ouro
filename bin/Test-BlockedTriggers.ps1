<#
.SYNOPSIS
    Reports a blocked issue whose Unblocks-when trigger has fired, or that carries none.
.DESCRIPTION
    A blocked issue opens with an Unblocks-when line naming what it waits on (ouro
    docs/contract.md sec4). Nothing unattended reads that line -- the only reader is
    interactive triage's one-line check -- so an issue stays blocked long after its trigger
    closed, and a blocked issue with no trigger line at all goes unnoticed. This gate reads
    every open `blocked` issue and reports both.

    The trigger line is the body's first line, after a byte-order mark and surrounding
    whitespace are stripped: it is a trigger line only when it starts, case-sensitively, with
    the contract's bold marker `**Unblocks when:**`. Anything else -- a blank line included --
    has no trigger line.

    References are read from the trigger line only: every number written as a hash sign and
    digits, unless a word character or a slash stands directly before the hash sign (a
    citation into another repository, skipped). A reference may name an issue or a pull
    request; closed, merged, and closed as not planned all read as closed through one field.
    A reference that resolves to nothing is unreadable and counts as open.

    Two finding kinds:
      fired            -- the trigger line holds at least one reference and every one is closed.
      no trigger line  -- the first line is not a trigger line.
    A trigger line with no reference waits on an outside event and is not a finding.

    With -Comment the gate posts one comment per finding on the blocked issue itself. The
    comment's first line is the finding's fingerprint: the literal marker
    `**Blocked check** (automated):` followed by the kind and, for a fired trigger, its
    references in ascending order. Among the issue's own comments by the job token's bot or a
    login in [owner].ruling_approvers (Get-TrustedMarkerBodies), the newest one whose first
    line starts with that marker is the last thing said; when it equals, character for
    character, the line this run would post, the run stays silent on that issue. A marker
    found anywhere but a comment's first line does not count. The gate changes no label and
    closes nothing.

    The issues are those of the repository the binding's [repo].slug names, or else of the one
    origin names (Get-RepoSlug.ps1). With neither, the gate reports that and reads nothing.
.PARAMETER IssuesJson
    JSON array [{number,title,body,comments:[{author:{login},body}]}] of blocked issues to check (for tests).
    Default: gh issue list --label blocked.
.PARAMETER ReferenceStatesJson
    JSON object {"<number>":"open"|"closed", ...} of referenced issues' states (for tests, used
    only alongside -IssuesJson). A number absent from it is unreadable. Default: gh api per
    reference.
.PARAMETER Comment
    Post one comment per finding on the blocked issue it is about, deduped against that
    issue's own comments.
#>
param(
    [string]$IssuesJson = '',
    [string]$ReferenceStatesJson = '',
    [switch]$Comment
)

$ErrorActionPreference = 'Stop'

# The repository every gh call below names, shared with the gates that read the same backlog.
. (Join-Path $PSScriptRoot 'Get-RepoSlug.ps1')
# The trust filter is the drift ledger's: a marker counts only from the job token's bot or a ruling approver.
. (Join-Path $PSScriptRoot 'Get-RollingIssue.ps1')

# gh writes UTF-8. PowerShell decodes a native command's stdout with [Console]::OutputEncoding,
# which on a Windows runner is the OEM code page, so without this every non-ASCII character in
# an issue body -- an em dash, most often -- is mangled before it is compared against the tree.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$TriggerMarker = '**Unblocks when:**'
$CommentMarker = '**Blocked check** (automated):'
# A citation into another repository (a word character or a slash directly before the hash
# sign) is skipped rather than read, the same reading the anchor gate gives a cross-repo cite.
$RefPattern = [regex]'(?<![\w/])#(\d+)'

# No -IssuesJson means the list below comes from gh, so the repository is named here.
if (-not $IssuesJson) {
    $repo = Get-RepoSlug
    if (-not $repo.Slug) {
        Write-Host "::warning::blocked-trigger gate: no issue read ($($repo.Why)): a gate that cannot name its repository enforces nothing"
        exit 0
    }
    $env:GH_REPO = $repo.Slug
}

$issues = if ($IssuesJson) { $IssuesJson | ConvertFrom-Json }
          else {
              # A native failure doesn't throw under 'Stop': unguarded, a broken token would
              # report a clean empty backlog and exit 0. Only stdout is JSON.
              $raw = gh issue list --label blocked --state open --limit 200 --json number,title,body,comments 2>&1
              if ($LASTEXITCODE -ne 0) { throw "gh issue list failed (exit $LASTEXITCODE): $raw" }
              ($raw | Where-Object { $_ -is [string] }) -join "`n" | ConvertFrom-Json
          }

$injectedStates = @{}
if ($ReferenceStatesJson) {
    ($ReferenceStatesJson | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $injectedStates[$_.Name] = "$($_.Value)" }
}

# $null means unreadable: the caller counts that as open.
function Get-ReferenceState([string]$Number) {
    if ($IssuesJson) {
        if ($injectedStates.ContainsKey($Number)) { return $injectedStates[$Number] }
        return $null
    }
    $out = gh api "repos/$($env:GH_REPO)/issues/$Number" --jq '.state' 2>&1
    if ($LASTEXITCODE -ne 0) { return $null }
    $state = (($out | Where-Object { $_ -is [string] }) -join "`n").Trim()
    return $(if ($state) { $state } else { $null })
}

$findings = @()
foreach ($issue in $issues) {
    $body = "$($issue.body)".TrimStart([char]0xFEFF)
    $lines = ($body -replace "`r`n", "`n") -split "`n"
    $first = $(if ($lines.Count -gt 0) { $lines[0] } else { '' }).Trim()

    if (-not $first.StartsWith($TriggerMarker, [System.StringComparison]::Ordinal)) {
        $findings += [pscustomobject]@{ Issue = $issue; Kind = 'no trigger line'; Refs = @() }
        continue
    }

    $refs = @($RefPattern.Matches($first) | ForEach-Object { [int]$_.Groups[1].Value } | Sort-Object -Unique)
    if ($refs.Count -eq 0) {
        Write-Host "#$($issue.number): not checkable (trigger line names no reference)"
        continue
    }

    $allClosed = $true
    foreach ($r in $refs) {
        $state = Get-ReferenceState "$r"
        if ($null -eq $state) {
            $allClosed = $false
            Write-Host "#$($issue.number): reference #$r is unreadable, counted as open"
        }
        elseif ($state -ne 'closed') { $allClosed = $false }
    }
    if ($allClosed) { $findings += [pscustomobject]@{ Issue = $issue; Kind = 'fired'; Refs = $refs } }
}

if ($findings.Count -eq 0) {
    Write-Host "No blocked-trigger findings across $(@($issues).Count) blocked issue(s)."
    exit 0
}

Write-Host "$($findings.Count) blocked-trigger finding(s) across $(@($issues).Count) blocked issue(s):"
foreach ($f in $findings) {
    $refText = if ($f.Kind -eq 'fired') { ($f.Refs | ForEach-Object { "#$_" }) -join ', ' } else { '' }
    $summary = if ($f.Kind -eq 'fired') { "fired: $refText" } else { $f.Kind }
    Write-Host "  #$($f.Issue.number): $summary" -ForegroundColor Yellow

    if (-not $Comment) { continue }

    $fingerprint = if ($f.Kind -eq 'fired') { "$CommentMarker fired $refText" } else { "$CommentMarker no trigger line" }

    # Already said: the newest comment whose first line starts with the marker, by the bot or a
    # ruling approver. A stranger's comment cannot pre-empt the notice.
    $candidates = @(@($f.Issue.comments) | Where-Object {
            $_ -and $_.body -and ((((("$($_.body)") -replace "`r`n", "`n") -split "`n")[0]).Trim()).StartsWith($CommentMarker, [System.StringComparison]::Ordinal) })
    $newest = $null
    foreach ($b in @(Get-TrustedMarkerBodies $candidates)) {
        $newest = ((("$b") -replace "`r`n", "`n") -split "`n")[0].Trim()
    }
    if ($newest -ceq $fingerprint) {
        Write-Host "  #$($f.Issue.number): already said, staying silent" -ForegroundColor DarkGray
        continue
    }

    $bodyText = "$fingerprint`n`n" +
        "What this read: the issue's first body line as its trigger, and the state of every " +
        "reference on it.`n`n" +
        "A person re-triages this issue; nothing here changes its label or closes it."
    $posted = gh issue comment $f.Issue.number --body $bodyText 2>&1
    if ($LASTEXITCODE -ne 0) { throw "gh issue comment $($f.Issue.number) failed (exit $LASTEXITCODE): $posted" }
}
exit 0
