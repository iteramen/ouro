<#
.SYNOPSIS
    Reconciles an umbrella's or architecture issue's body-listed children against its native
    GitHub sub-issue list.
.DESCRIPTION
    An umbrella lists its children in its body; GitHub holds a native sub-issue list for the
    same issue (ouro docs/contract.md sec3). Nothing compares the two, so they drift. This gate
    reads every open issue carrying the `umbrella` or `architecture` label and reports where the
    two lists disagree.

    A body line is a child reference when it is a list item -- at any indent, bulleted or
    numbered, an optional task-list box allowed after the marker -- whose first token is a hash
    sign and digits, unless a word character or a slash stands directly before the hash sign (a
    citation into another repository, never a child). Any further number on that line is a
    mention, not a child. A line inside a fenced code block is not read: a fence opens at the
    start of a line or right after a list marker (a backtick fence whose info string holds a
    backtick opens nothing), and closes on a line of the same character, at least as long as the
    opening one, with no text after it. Table rows, prose and headings hold mentions only.

    Three finding kinds, per container:
      referenced, not linked -- a child reference whose number is absent from the native list.
      linked, not referenced -- a native sub-issue with no child reference in the body.
      every child closed     -- the union of both lists is not empty and every issue in it is
                                 closed.
    There is no descent: a child that is itself a container counts by its own open or closed
    state and is named as a container in the finding, and no read is made of its own children on
    the parent's behalf. A child reference that resolves to nothing is unreadable and counts as
    open.

    With -Comment the gate posts one comment, grouped by container, on the rolling issue named
    by [rolling_issues].drift_audit -- only when there is a finding. No dedupe: like the other
    gates that post there, a standing finding is repeated weekly.

    The issues are those of the repository the binding's [repo].slug names, or else of the one
    origin names (Get-RepoSlug.ps1). With neither, the gate reports that and reads nothing.
.PARAMETER IssuesJson
    JSON array [{number,title,body,labels:[{name}]}] of umbrella/architecture issues to check
    (for tests). Default: gh issue list.
.PARAMETER SubIssuesJson
    JSON object {"<container number>":[{"number":N,"state":"open"|"closed"}, ...], ...}, the
    native sub-issue list per container (for tests, used only alongside -IssuesJson). Default:
    gh api repos/<repo>/issues/<n>/sub_issues per container.
.PARAMETER ReferenceStatesJson
    JSON object {"<number>":"open"|"closed", ...} for a referenced child absent from its
    container's native list (for tests, used only alongside -IssuesJson). A number absent from
    it is unreadable. Default: gh api per reference.
.PARAMETER Comment
    Post one comment, grouped by container, on the rolling issue named by
    [rolling_issues].drift_audit -- only when there is a finding.
.PARAMETER RollingIssueTitle
    Post to this rolling-issue title instead of resolving [rolling_issues].drift_audit (for
    tests, or to run without a binding).
#>
param(
    [string]$IssuesJson = '',
    [string]$SubIssuesJson = '',
    [string]$ReferenceStatesJson = '',
    [switch]$Comment,
    [string]$RollingIssueTitle
)

$ErrorActionPreference = 'Stop'

# The rolling issue's title comes from the binding, never a literal here.
. (Join-Path $PSScriptRoot 'Get-RollingIssue.ps1')
# The repository every gh call below names, shared with the gates that read the same backlog.
. (Join-Path $PSScriptRoot 'Get-RepoSlug.ps1')

# gh writes UTF-8. PowerShell decodes a native command's stdout with [Console]::OutputEncoding,
# which on a Windows runner is the OEM code page, so without this every non-ASCII character in
# an issue body -- an em dash, most often -- is mangled before it is compared against the tree.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

# A word character or a slash directly before the hash sign is a citation into another
# repository, the same reading the anchor gate gives a cross-repo cite -- never a child.
$ChildLinePattern = '^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(?:\[[ xX]\][ \t]+)?(?<![\w/])#(\d+)\b'

function Get-ChildReferences([string]$Body) {
    $refs = [System.Collections.Generic.List[int]]::new()
    $fenceChar = ''
    $fenceLen = 0
    foreach ($line in (("$Body" -replace "`r`n", "`n") -split "`n")) {
        $fm = [regex]::Match($line, '^\s*(`{3,}|~{3,})(.*)$')
        if ($fenceChar) {
            if ($fm.Success -and $fm.Groups[1].Value[0] -ceq $fenceChar -and $fm.Groups[1].Value.Length -ge $fenceLen -and -not $fm.Groups[2].Value.Trim()) { $fenceChar = '' }
            continue
        }
        $fo = [regex]::Match($line, '^\s*(?:(?:[-*+]|\d+[.)])\s+)?(`{3,}|~{3,})(.*)$')
        if ($fo.Success -and -not ($fo.Groups[1].Value[0] -ceq '`' -and $fo.Groups[2].Value.Contains('`'))) { $fenceChar = $fo.Groups[1].Value[0]; $fenceLen = $fo.Groups[1].Value.Length; continue }
        if ($line -match $ChildLinePattern) { $refs.Add([int]$Matches[1]) }
    }
    return @($refs | Sort-Object -Unique)
}

if (-not $IssuesJson) {
    $repo = Get-RepoSlug
    if (-not $repo.Slug) {
        Write-Host "::warning::umbrella-children gate: no issue read ($($repo.Why)): a gate that cannot name its repository enforces nothing"
        exit 0
    }
    $env:GH_REPO = $repo.Slug
}

$issues = if ($IssuesJson) { $IssuesJson | ConvertFrom-Json }
          else {
              # A native failure doesn't throw under 'Stop': unguarded, a broken token would
              # report a clean empty backlog and exit 0. Only stdout is JSON.
              # A comma inside one `label:` qualifier is OR: either label admits the issue.
              $raw = gh issue list --search 'label:umbrella,architecture' --state open --limit 200 `
                  --json number,title,body,labels 2>&1
              if ($LASTEXITCODE -ne 0) { throw "gh issue list failed (exit $LASTEXITCODE): $raw" }
              ($raw | Where-Object { $_ -is [string] }) -join "`n" | ConvertFrom-Json
          }

$containerNumbers = @{}
foreach ($i in $issues) { $containerNumbers["$($i.number)"] = $true }

$injectedSubIssues = @{}
if ($SubIssuesJson) {
    ($SubIssuesJson | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $injectedSubIssues[$_.Name] = @($_.Value) }
}
$injectedRefStates = @{}
if ($ReferenceStatesJson) {
    ($ReferenceStatesJson | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $injectedRefStates[$_.Name] = "$($_.Value)" }
}

function Get-NativeList([string]$Number) {
    if ($IssuesJson) {
        if ($injectedSubIssues.ContainsKey($Number)) { return $injectedSubIssues[$Number] }
        return @()
    }
    # The endpoint pages at 30 by default and a parent may hold 100 sub-issues: --paginate reads
    # every page, and one tab-separated line per entry reads the same whatever the page count.
    $out = gh api --paginate "repos/$($env:GH_REPO)/issues/$Number/sub_issues?per_page=100" --jq '.[] | [.number, .state] | @tsv' 2>&1
    if ($LASTEXITCODE -ne 0) { throw "gh api repos/$($env:GH_REPO)/issues/$Number/sub_issues failed (exit $LASTEXITCODE): $out" }
    return @(foreach ($line in @($out | Where-Object { $_ -is [string] })) {
        if ($line -match '^(\d+)\t(\w+)$') { [pscustomobject]@{ number = [int]$Matches[1]; state = $Matches[2] } }
    })
}

# $null means unreadable: the caller counts that as open.
function Get-ReferenceState([string]$Number) {
    if ($containerNumbers.ContainsKey($Number)) { return 'open' }   # in the scan itself: --state open
    if ($IssuesJson) {
        if ($injectedRefStates.ContainsKey($Number)) { return $injectedRefStates[$Number] }
        return $null
    }
    $out = gh api "repos/$($env:GH_REPO)/issues/$Number" --jq '.state' 2>&1
    if ($LASTEXITCODE -ne 0) { return $null }
    $state = (($out | Where-Object { $_ -is [string] }) -join "`n").Trim()
    return $(if ($state) { $state } else { $null })
}

function Get-ChildLabel([string]$Number) {
    if ($containerNumbers.ContainsKey($Number)) { return "container #$Number" }
    return "#$Number"
}

$findings = @()   # {Container, Kind, Text}
foreach ($container in $issues) {
    $refs = Get-ChildReferences $container.body
    $native = @(Get-NativeList "$($container.number)")
    $nativeNumbers = @($native | ForEach-Object { [int]$_.number })

    $referencedNotLinked = @($refs | Where-Object { $nativeNumbers -notcontains $_ })
    $linkedNotReferenced = @($nativeNumbers | Where-Object { $refs -notcontains $_ })

    foreach ($n in $referencedNotLinked) {
        $findings += [pscustomobject]@{ Container = $container; Kind = 'referenced, not linked'; Text = Get-ChildLabel "$n" }
    }
    foreach ($n in $linkedNotReferenced) {
        $findings += [pscustomobject]@{ Container = $container; Kind = 'linked, not referenced'; Text = Get-ChildLabel "$n" }
    }

    $union = @(@($refs) + @($nativeNumbers) | Sort-Object -Unique)
    if ($union.Count -gt 0) {
        $allClosed = $true
        foreach ($n in $union) {
            $native1 = $native | Where-Object { [int]$_.number -eq $n } | Select-Object -First 1
            $state = if ($native1) { "$($native1.state)" } else { Get-ReferenceState "$n" }
            if ($null -eq $state) {
                $allClosed = $false
                Write-Host "#$($container.number): child #$n is unreadable, counted as open"
            }
            elseif ($state.ToLowerInvariant() -ne 'closed') { $allClosed = $false }
        }
        if ($allClosed) {
            $names = ($union | ForEach-Object { Get-ChildLabel "$_" }) -join ', '
            $findings += [pscustomobject]@{ Container = $container; Kind = 'every child closed'; Text = $names }
        }
    }
}

if ($findings.Count -eq 0) {
    Write-Host "No umbrella-children findings across $(@($issues).Count) open umbrella/architecture issue(s)."
    exit 0
}

Write-Host "$($findings.Count) umbrella-children finding(s) across $(@($issues).Count) open umbrella/architecture issue(s):"
$byContainer = $findings | Group-Object { $_.Container.number }
foreach ($g in $byContainer) {
    $title = ($g.Group | Select-Object -First 1).Container.title
    Write-Host "#$($g.Name) $title" -ForegroundColor Yellow
    foreach ($f in $g.Group) { Write-Host "  $($f.Kind): $($f.Text)" -ForegroundColor Yellow }
}

if ($Comment) {
    $lines = foreach ($g in $byContainer) {
        $title = ($g.Group | Select-Object -First 1).Container.title
        "- #$($g.Name) ($title):"
        foreach ($f in $g.Group) { "  - $($f.Kind): $($f.Text)" }
    }
    $body = "**Umbrella-children check** (deterministic):`n`n" +
        ($lines -join "`n") +
        "`n`nAn umbrella's or architecture issue's body-listed children and its native sub-issue " +
        "list disagree above -- the ouro contract."
    $n = if ($RollingIssueTitle) { Get-RollingIssueNumber -Title $RollingIssueTitle }
         else                     { Get-RollingIssueNumber }
    if ($n) {
        $posted = gh issue comment $n --body (ConvertTo-InertCommentText $body) 2>&1
        if ($LASTEXITCODE -ne 0) { throw "gh issue comment $n failed (exit $LASTEXITCODE): $posted" }
    }
}
exit 0
