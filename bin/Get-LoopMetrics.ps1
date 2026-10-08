<#
.SYNOPSIS
    Measure the loop over a trailing window -- rulings answered, landings, rework -- and
    print the numbers, or with -Comment append them to the [rolling_issues].loop_runs issue.
.DESCRIPTION
    The contract says the loop's constraint is ruling velocity (section 10), and nothing measured
    it. Three numbers and no more, each a git or gh read -- two of them claims the contract
    already makes, and Rework the convention the subjects are written in:

      Rulings  -- issues whose `needs-ruling` label came off in the window (an `unlabeled`
                  event), each issue counted once however often it was relabeled; and, over the
                  issues still carrying the label, the oldest age in whole days, measured from
                  each one's MOST RECENT `labeled needs-ruling` event -- a question re-asked
                  restarts its clock.
      Landed   -- first-parent commits on origin/<default_branch> whose committer date falls in
                  the window and whose subject ends in `(#N)`. A GitHub squash-merge and the
                  local squash-merge drill both leave that subject; the local drill closes its
                  PRs unmerged, so a merged-PR count misses those landings. PR #N's body with a
                  `Fixes #` line, bare or opened by one inline-code backtick, is loop-landed;
                  anything else, an N that is not a pull request included, is the rest.
                  A landing is dated by its committer date and read from the history as last
                  fetched: one committed before a run's fetch and pushed after it, or pushed
                  after one run's fetch and dated before the next run's window starts, can be
                  counted by neither run.
      Rework   -- of those landings, the ones whose subject carries the conventional `fix` type,
                  anchored at the subject's start, so a `feat` whose summary says fix and a type
                  merely starting with fix are not counted. It comes off the same walk as Landed,
                  so it is always a share of that number. A trend, not a defect rate: a `fix`
                  landing may repair something years old. A repository that does not write
                  conventional subjects reports zero here, and the row reads as ABSENT, not clean.

    A failed git or gh read throws with its output; it never reads as zero. The repository and
    default branch come from the binding, and every gh call targets that repository (GH_REPO).
    The history is the working directory's: run from the repo root with the default branch
    fetched, as the weekly pass's fetch-depth: 0 checkout provides.
.PARAMETER Since
    Window start; the window ends now. A date without an offset is UTC. Default: seven days ago.
    Each run's window is the seven days before it started, so consecutive runs overlap or leave
    a gap by how far their start times are from seven days apart, and a run started by hand
    overlaps the scheduled one. The numbers are a trend, not an audit.
.PARAMETER Comment
    Append the report as one comment on the issue titled by [rolling_issues].loop_runs --
    comments, never the body, so no run can overwrite the history. No such issue: create it
    carrying `umbrella`, then comment. No such key declared: print, say so, exit 0.
.PARAMETER AsModule
    Dot-source the function definitions without running anything. For the tests.
#>
param(
    [datetime]$Since = [datetime]::UtcNow.AddDays(-7),
    [switch]$Comment,
    [switch]$AsModule
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# The loop_runs issue is found by the shared exact-title resolver, never a second copy of the search.
. (Join-Path $PSScriptRoot 'Get-RollingIssue.ps1')

# GitHub and git timestamps arrive as ISO 8601 strings with an offset or, through ConvertFrom-Json,
# as a DateTime of either kind. Everything is compared in UTC; a value with no offset is UTC.
function ConvertTo-UtcTime($Value) {
    if ($Value -is [datetime]) {
        if ($Value.Kind -eq [DateTimeKind]::Unspecified) { return [datetime]::SpecifyKind($Value, [DateTimeKind]::Utc) }
        return $Value.ToUniversalTime()
    }
    $styles = [System.Globalization.DateTimeStyles]::AssumeUniversal -bor [System.Globalization.DateTimeStyles]::AdjustToUniversal
    return [datetime]::Parse([string]$Value, [cultureinfo]::InvariantCulture, $styles)
}

# The one date the script writes, the report's window, is formatted invariant. Under a culture with
# another calendar a current-culture format writes another year (th-TH writes 2026 as 2569), and the
# report would name a window that never was.
function Format-UtcTime {
    param([datetime]$Time, [string]$Pattern)
    return (ConvertTo-UtcTime $Time).ToString($Pattern, [cultureinfo]::InvariantCulture)
}

# A native failure does not throw under 'Stop': unguarded, a broken token reads as a quiet week.
# Only stdout is data -- under 2>&1 stderr arrives as ErrorRecords.
function Invoke-Read {
    param([string]$Exe, [string[]]$Arguments)
    $out = & $Exe @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed (exit $LASTEXITCODE): $out" }
    return @($out | Where-Object { $_ -is [string] })
}

# Tab-separated lines (gh --jq @tsv, git log --format with %x09) into objects. The last field
# keeps any tabs of its own, so a commit subject survives whole.
function ConvertFrom-Tsv {
    param([string[]]$Lines, [string[]]$Names)
    foreach ($line in $Lines) {
        if (-not $line) { continue }
        $fields = $line -split "`t", $Names.Count
        $row = [ordered]@{}
        for ($i = 0; $i -lt $Names.Count; $i++) { $row[$Names[$i]] = if ($i -lt $fields.Count) { $fields[$i] } else { '' } }
        [pscustomobject]$row
    }
}

# Only a `no such key` failure means undeclared. A key declared with an empty value is a broken
# binding, not an absent one: read as undeclared it would post nothing and exit green -- the same
# call the shared resolver makes ("resolved to an empty title").
function Get-BindingValue {
    param([string]$Key, [switch]$Optional)
    $out = python3 (Join-Path $PSScriptRoot 'ouro-binding.py') get $Key 2>&1
    if ($LASTEXITCODE -eq 0) {
        $value = ((@($out | Where-Object { $_ -is [string] })) -join "`n").Trim()
        if (-not $value) { throw "$Key resolved to an empty value" }
        return $value
    }
    if ($Optional -and "$out" -match 'no such key') { return $null }
    throw "ouro-binding.py get $Key failed (exit $LASTEXITCODE): $out"
}

# Repository issue events arrive newest first, so paging stops at a short page (the last one) or at
# a page whose oldest row is already outside the window; every row of the pages read is returned.
# -Read takes the repository and a page number and returns @tsv lines (for tests).
function Get-RepositoryIssueEvents {
    param([string]$Slug, [datetime]$Since, [scriptblock]$Read = {
            param($Slug, $Page)
            Invoke-Read gh @('api', "repos/$Slug/issues/events?per_page=100&page=$Page",
                '--jq', '.[] | [.event, .created_at, (.label.name // ""), .issue.number] | @tsv')
        })
    $from = ConvertTo-UtcTime $Since
    $events = [System.Collections.Generic.List[object]]::new()
    for ($page = 1; ; $page++) {
        $rows = @(ConvertFrom-Tsv -Names 'event', 'created_at', 'label', 'number' -Lines @(& $Read $Slug $page))
        foreach ($r in $rows) { $events.Add($r) }
        if ($rows.Count -lt 100 -or (ConvertTo-UtcTime $rows[-1].created_at) -lt $from) { break }
    }
    return $events.ToArray()
}

# The open needs-ruling backlog's issue numbers, capped: a page that fills warns rather than
# throws, since a ruling past it is undercounted, not the whole answer gone. -Read takes the
# limit and returns the id lines (for tests).
function Get-OpenRulingIds {
    param([int]$Limit = 1000, [scriptblock]$Read = {
            param($Limit)
            Invoke-Read gh @('issue', 'list', '--label', 'needs-ruling', '--state', 'open',
                '--limit', "$Limit", '--json', 'number', '--jq', '.[].number')
        })
    $ids = @(& $Read $Limit | Where-Object { $_ })
    if ($ids.Count -ge $Limit) {
        Write-Host "::warning::loop metrics: the needs-ruling list filled its --limit $Limit page: a ruling past it is not counted"
    }
    return $ids
}

# The default branch's first-parent line, newest first. No --since: git log stops at the first
# commit whose committer date is older than the cutoff, and a rebased or cherry-picked commit
# mid-line can carry an older date than its parent, silently dropping every landing behind it.
# -Read takes the ref and returns the log lines (for tests).
function Get-FirstParentCommits {
    param([string]$Ref, [scriptblock]$Read = {
            param($Ref)
            # ponytail: --max-count 5000 bounds the read; a window holding more first-parent commits than that undercounts -- raise it then.
            Invoke-Read git @('log', '--first-parent', $Ref, '--max-count=5000', '--format=%H%x09%P%x09%cI%x09%s')
        })
    return @(ConvertFrom-Tsv -Names 'sha', 'parents', 'committed', 'subject' -Lines @(& $Read $Ref))
}

# Rulings answered: distinct issues whose needs-ruling label came off in the window, so a question
# asked, answered, re-asked and answered again in one week is one ruling, not two.
# $Events rows: event, created_at, label, number.
function Get-RulingsAnswered {
    param([object[]]$Events, [datetime]$Since)
    $from = ConvertTo-UtcTime $Since
    return @(@($Events) | Where-Object {
            $_.event -eq 'unlabeled' -and $_.label -eq 'needs-ruling' -and (ConvertTo-UtcTime $_.created_at) -ge $from
        } | ForEach-Object { "$($_.number)" } | Sort-Object -Unique).Count
}

# The oldest open ruling in whole days, over the issues still carrying needs-ruling, each aged from
# its most recent labeling. $EventsByIssue maps an issue number to its rows (event, created_at,
# label). $null when no open issue has a needs-ruling labeling on record.
function Get-OldestOpenRulingDays {
    param([hashtable]$EventsByIssue, [datetime]$Now)
    $to = ConvertTo-UtcTime $Now
    $ages = @(foreach ($key in $EventsByIssue.Keys) {
            $last = @($EventsByIssue[$key]) | Where-Object { $_.event -eq 'labeled' -and $_.label -eq 'needs-ruling' } |
                ForEach-Object { ConvertTo-UtcTime $_.created_at } | Sort-Object | Select-Object -Last 1
            if ($last) { [int][math]::Floor(($to - $last).TotalDays) }
        })
    if ($ages.Count -eq 0) { return $null }
    return [int]($ages | Measure-Object -Maximum).Maximum
}

# Landed: walk the whole first-parent line from the tip (the first row), keeping each commit whose
# committer date is in the window and whose subject ends in `(#N)`. The walk never stops at a
# commit outside the window -- an older-dated commit mid-line has in-window landings behind it --
# and the walk, not the row list, decides membership: a commit a merge brought in through its second
# parent is on the branch but was not landed onto it. $Commits rows: sha, parents (space-separated),
# committed, subject. Each landing carries the subject it landed under, so what is counted off the
# subjects cannot drift from the list it is a share of.
function Get-Landings {
    param([object[]]$Commits, [datetime]$Since)
    $from = ConvertTo-UtcTime $Since
    $bySha = @{}
    foreach ($c in @($Commits)) { $bySha[$c.sha] = $c }
    $landings = [System.Collections.Generic.List[object]]::new()
    $c = @($Commits) | Select-Object -First 1
    while ($c) {
        if ((ConvertTo-UtcTime $c.committed) -ge $from -and $c.subject -match '\(#([0-9]+)\)\s*$') {
            $landings.Add([pscustomobject]@{ Number = [int]$Matches[1]; Subject = "$($c.subject)" })
        }
        $parents = @("$($c.parents)" -split '\s+' | Where-Object { $_ })
        $c = if ($parents.Count) { $bySha[$parents[0]] } else { $null }
    }
    return $landings.ToArray()
}

# The split. $Bodies maps N to PR #N's body, or to $null when N is not a pull request: landed
# work, but not the loop's delivery shape. Fix is the rework count off the same landings: the
# conventional type anchored at the subject's start, with an optional scope and the breaking `!`,
# so `fixup:` and a `feat` whose summary says fix are not it.
function Measure-Landed {
    param([object[]]$Landings, [hashtable]$Bodies)
    $loop = @(@($Landings) | Where-Object { $Bodies[$_.Number] -and $Bodies[$_.Number] -match '(?m)^`?Fixes #\d+' }).Count
    $fix = @(@($Landings) | Where-Object { $_.Subject -match '^fix(\([^)]*\))?!?:' }).Count
    return [pscustomobject]@{ Loop = $loop; Rest = @($Landings).Count - $loop; Fix = $fix }
}

function Get-PullRequestBody {
    param([int]$Number)
    $out = gh pr view $Number --json body --jq .body 2>&1
    if ($LASTEXITCODE -eq 0) { return (@($out | Where-Object { $_ -is [string] }) -join "`n") }
    # gh's answer for a number that is an issue, not a pull request. Only that reads as "not a PR";
    # any other failure -- auth, network, a wrong repository -- is a failed read and throws.
    if ("$out" -match 'Could not resolve to a PullRequest with the number') { return $null }
    throw "gh pr view $Number failed (exit $LASTEXITCODE): $out"
}

function Format-LoopMetrics {
    param([string]$Slug, [datetime]$Since, [datetime]$Now, [int]$Answered, $OldestOpenDays,
        [int]$OpenCount, $Landed)
    $format = 'yyyy-MM-dd HH:mm'
    $open = if ($OpenCount -eq 0) { 'none open' }
            elseif ($null -eq $OldestOpenDays) { "$OpenCount open, age unknown" }
            else { "$OpenCount open, the oldest $OldestOpenDays day(s)" }
    return @(
        "Loop metrics for $Slug, $(Format-UtcTime $Since $format) to $(Format-UtcTime $Now $format) UTC",
        '',
        '| Metric | Count | Detail |',
        '|---|---|---|',
        "| Rulings answered | $Answered | $open |",
        "| Landed | $($Landed.Loop + $Landed.Rest) | $($Landed.Loop) loop-landed (the PR carries a Fixes line), $($Landed.Rest) other |",
        "| Rework | $($Landed.Fix) | of those landings, the ones whose subject is typed fix |"
    ) -join "`n"
}

# Append-only comments, like the drift ledger and for the same reason: history must not be
# overwritable. Returns the issue number, or nothing when no loop_runs title is declared.
function Send-LoopMetrics {
    param([string]$Report, [string]$Title)
    if (-not $Title) {
        Write-Host "no [rolling_issues].loop_runs declared in .claude/ouro.toml: printed only, nothing posted."
        return
    }
    $n = Get-RollingIssueNumber -Title $Title -Key rolling_issues.loop_runs
    if (-not $n) {
        # Carries `umbrella`, as the docs-freshness sweep's issue does: a state label (contract
        # section 4), so the created issue satisfies the state-label invariant, and one outside the
        # default working view, so a container of numbers does not arrive asking to be triaged.
        $out = gh issue create --title $Title --label umbrella --body "Loop metrics: one comment per weekly pass, appended and never edited. A rolling issue is machinery, not backlog." 2>&1
        if ($LASTEXITCODE -ne 0) { throw "gh issue create '$Title' failed (exit $LASTEXITCODE): $out" }
        # Only stdout is the URL: under 2>&1 stderr arrives as ErrorRecords, and a warning there
        # can carry an issue URL of its own.
        $url = ((@($out) | Where-Object { $_ -is [string] }) -join "`n").Trim()
        if ($url -notmatch '^https?://\S+/issues/([0-9]+)$') { throw "gh issue create '$Title' printed no issue URL: $out" }
        $n = [int]$Matches[1]
    }
    $out = gh issue comment $n --body $Report 2>&1
    if ($LASTEXITCODE -ne 0) { throw "gh issue comment $n failed (exit $LASTEXITCODE): $out" }
    return $n
}

if ($AsModule) { return }

# gh writes UTF-8; a Windows runner otherwise decodes native stdout with the OEM code page.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$slug = Get-BindingValue repo.slug
$default = Get-BindingValue repo.default_branch
# Every gh call below targets the binding's repository, the shared resolver's included.
$env:GH_REPO = $slug
$from = ConvertTo-UtcTime $Since
$now = [datetime]::UtcNow

# Rulings.
$events = Get-RepositoryIssueEvents -Slug $slug -Since $from
$byIssue = @{}
foreach ($n in @(Get-OpenRulingIds)) {
    $byIssue[$n] = @(ConvertFrom-Tsv -Names 'event', 'created_at', 'label' -Lines @(
            Invoke-Read gh @('api', '--paginate', "repos/$slug/issues/$n/events", '--jq', '.[] | [.event, .created_at, (.label.name // "")] | @tsv')))
}
$answered = Get-RulingsAnswered -Events $events -Since $from
$oldest = Get-OldestOpenRulingDays -EventsByIssue $byIssue -Now $now

# Landed. origin/<default>, not a local branch: a checkout on another branch has no local default,
# and a local one may carry commits that never landed.
$landings = @(Get-Landings -Commits (Get-FirstParentCommits -Ref "origin/$default") -Since $from)
$bodies = @{}
foreach ($n in @($landings | ForEach-Object { $_.Number } | Sort-Object -Unique)) { $bodies[$n] = Get-PullRequestBody -Number $n }
$landed = Measure-Landed -Landings $landings -Bodies $bodies

$report = Format-LoopMetrics -Slug $slug -Since $from -Now $now -Answered $answered -OldestOpenDays $oldest `
    -OpenCount $byIssue.Count -Landed $landed
Write-Host $report

if ($Comment) {
    $n = Send-LoopMetrics -Report $report -Title (Get-BindingValue rolling_issues.loop_runs -Optional)
    if ($n) { Write-Host "appended to #$n" }
}
exit 0
