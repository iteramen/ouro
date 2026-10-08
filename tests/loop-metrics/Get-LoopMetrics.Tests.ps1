<#
.SYNOPSIS
    Unit test for Get-LoopMetrics.ps1's counting, its reads and its -Comment path.
.DESCRIPTION
    Drives the counting functions against inline fixture data, dot-sourced with -AsModule: no gh
    and no network. A test that read the live repository would pass or fail on whatever landed
    that week. The one git history it walks is a scratch repository built in TEMP.

    Pinned: a needs-ruling relabeled twice in the window counts once, one answered before the
    window not at all; the oldest open ruling ages from its most recent labeling; repository events
    page until a short page or one reaching past the window; a (#N) commit off the default branch's
    first-parent line is not landed, nor one before the window, nor a release commit with no (#N),
    while an older-dated commit mid-line does not hide the landings behind it; a PR body with a
    Fixes line, bare or opened by one backtick, or in lowercase, is loop-landed, while a mid-line
    Fixes (inline code or not), a body without one, an N that is an issue, a Fixes with no number
    or no space, a double backtick and leading spaces are the rest; each landing carries its
    subject, off which rework counts the conventional fix type -- plain, scoped, breaking and
    capitalised -- while a feat whose summary says fix, a type merely starting with fix, a
    release landing, a revert of a fix and a fix before the window are not rework; a failed read
    throws; dates format the same under any culture. The -Comment path runs against a stub `gh`:
    one comment on an existing issue, a created `umbrella` issue when
    there is none, and nothing posted when loop_runs is not declared -- while a loop_runs declared
    empty throws.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Two levels up is the plugin root (script under bin/) or, once vendored, the scripts dir itself.
$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Script = @((Join-Path $Base 'Get-LoopMetrics.ps1'), (Join-Path $Base 'bin/Get-LoopMetrics.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Script) { throw "Get-LoopMetrics.ps1 not found under $Base" }
. $Script -AsModule

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -eq $Actual) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}
function Assert-Throws($Block, $Match, $What) {
    try { & $Block; Write-Host "  FAIL: $What -- did not throw" -ForegroundColor Red; $script:failures++ }
    catch {
        if ("$_" -match $Match) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
        else { Write-Host "  FAIL: $What -- threw '$_', expected /$Match/" -ForegroundColor Red; $script:failures++ }
    }
}
function New-ScratchDir($Prefix) {
    $d = Join-Path ([System.IO.Path]::GetTempPath()) ("$Prefix-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $d | Out-Null
    return $d
}

$since = [datetime]::new(2026, 9, 6, 0, 0, 0, [DateTimeKind]::Utc)
$now = [datetime]::new(2026, 9, 13, 0, 0, 0, [DateTimeKind]::Utc)

# --- Rulings answered ------------------------------------------------------------------------
$relabeled = ConvertFrom-Json @'
[
  {"event":"unlabeled","label":"needs-ruling","number":7,"created_at":"2026-09-07T10:00:00Z"},
  {"event":"labeled","label":"needs-ruling","number":7,"created_at":"2026-09-08T10:00:00Z"},
  {"event":"unlabeled","label":"needs-ruling","number":7,"created_at":"2026-09-09T10:00:00Z"}
]
'@
Assert-Equal 1 (Get-RulingsAnswered -Events $relabeled -Since $since) 'an issue relabeled needs-ruling twice in the window counts once'
$before = ConvertFrom-Json '[{"event":"unlabeled","label":"needs-ruling","number":8,"created_at":"2026-09-05T23:59:59Z"}]'
Assert-Equal 0 (Get-RulingsAnswered -Events $before -Since $since) 'a ruling answered before the window is not counted'
$otherLabel = ConvertFrom-Json '[{"event":"unlabeled","label":"needs-triage","number":9,"created_at":"2026-09-07T10:00:00Z"}]'
Assert-Equal 0 (Get-RulingsAnswered -Events $otherLabel -Since $since) 'another label coming off is not a ruling'
Assert-Equal 0 (Get-RulingsAnswered -Events @() -Since $since) 'no events, no rulings'

# --- Repository events: paging stops at a short page or at one reaching past the window -------
function New-EventLines($Count, $Start, $Label = 'needs-triage') {
    # $Count unlabeled rows, newest first, one minute apart from $Start downwards, issue numbers 1000+.
    for ($i = 0; $i -lt $Count; $i++) {
        "unlabeled`t$(Format-UtcTime $Start.AddMinutes(-$i) "yyyy-MM-dd'T'HH:mm:ss'Z'")`t$Label`t$(1000 + $i)"
    }
}
$pagesRead = [System.Collections.Generic.List[int]]::new()
$page1Full = @(New-EventLines 100 ([datetime]::new(2026, 9, 12, 12, 0, 0, [DateTimeKind]::Utc)))
$page2 = @("unlabeled`t2026-09-07T00:00:00Z`tneeds-ruling`t77", "labeled`t2026-09-05T00:00:00Z`tneeds-ruling`t78")
$events = @(Get-RepositoryIssueEvents -Slug 'o/r' -Since $since -Read { param($Slug, $Page) $pagesRead.Add($Page); if ($Page -eq 1) { $page1Full } elseif ($Page -eq 2) { $page2 } else { throw "page $Page read" } })
Assert-Equal '1,2' ($pagesRead -join ',') 'a full page 1 still in the window is followed by page 2, whose last row ends the paging'
Assert-Equal 102 $events.Count 'every row of the pages read is returned'
Assert-Equal 1 (Get-RulingsAnswered -Events $events -Since $since) 'an in-window ruling on page 2 after a full page 1 is still read'

$pagesRead.Clear()
$page1Old = @(New-EventLines 100 ([datetime]::new(2026, 9, 6, 1, 0, 0, [DateTimeKind]::Utc)))   # reaches 09-05 22:21
$null = Get-RepositoryIssueEvents -Slug 'o/r' -Since $since -Read { param($Slug, $Page) $pagesRead.Add($Page); if ($Page -eq 1) { $page1Old } else { throw "page $Page read" } }
Assert-Equal '1' ($pagesRead -join ',') 'a full page whose last row is before the window ends the paging'

$pagesRead.Clear()
$null = Get-RepositoryIssueEvents -Slug 'o/r' -Since $since -Read { param($Slug, $Page) $pagesRead.Add($Page); if ($Page -eq 1) { @($page1Full[0..2]) } else { throw "page $Page read" } }
Assert-Equal '1' ($pagesRead -join ',') 'a short page is the last one'

# --- The oldest open ruling ages from its most recent labeling --------------------------------
$open = @{
    20 = ConvertFrom-Json '[{"event":"labeled","label":"needs-ruling","created_at":"2026-09-01T00:00:00Z"},{"event":"unlabeled","label":"needs-ruling","created_at":"2026-09-02T00:00:00Z"},{"event":"labeled","label":"needs-ruling","created_at":"2026-09-10T00:00:00Z"}]'
    21 = ConvertFrom-Json '[{"event":"labeled","label":"needs-ruling","created_at":"2026-09-08T12:00:00Z"},{"event":"labeled","label":"bug","created_at":"2026-08-01T00:00:00Z"}]'
}
# issue 20 was re-asked on 09-10 (3 days); issue 21 has waited since 09-08 12:00 (4.5 days); issue 21's bug label
# is older still and is not a ruling. The first labeling would say 12, the bug label 43.
Assert-Equal 4 (Get-OldestOpenRulingDays -EventsByIssue $open -Now $now) 'the oldest open ruling ages from each issue''s most recent labeling'
Assert-Equal $null (Get-OldestOpenRulingDays -EventsByIssue @{} -Now $now) 'no open ruling has no age'

# --- The open needs-ruling backlog is capped, and a full page warns rather than reading silent -
$fullPage = @(Get-OpenRulingIds -Limit 3 -Read { param($Limit) @('101', '102', '103') } 6>&1)
Assert-Equal 1 @($fullPage | Where-Object { "$_" -match '::warning::.*--limit 3' }).Count 'a needs-ruling page that fills warns naming its limit'
Assert-Equal '101,102,103' (($fullPage | Where-Object { "$_" -notmatch '::warning::' }) -join ',') 'and the ids read are still returned whole'
$underPage = @(Get-OpenRulingIds -Limit 3 -Read { param($Limit) @('101', '102') } 6>&1)
Assert-Equal 0 @($underPage | Where-Object { "$_" -match '::warning::' }).Count 'a needs-ruling page under the limit warns nothing'
# The rows above prove the function; this one pins the call site. The script reads the needs-ruling
# list in exactly one place, the function, so an inline list put back beside it skips the warning.
$ScriptText = [System.IO.File]::ReadAllText($Script)
Assert-Equal 1 ([regex]::Matches($ScriptText, "'--label',\s*'needs-ruling'")).Count 'the script lists needs-ruling issues only inside Get-OpenRulingIds'
Assert-Equal 1 ([regex]::Matches($ScriptText, '@\(Get-OpenRulingIds\)')).Count 'and its one read of the ruling list calls that function'

# --- Landed: the default branch's first-parent line, in the window, subject ending in (#N) -----
# Tip first, as git log prints. M is a merge whose second parent S carries (issue 55): on the branch,
# never landed onto it. OLD is before the window; R is a release commit with no (#N).
$commits = ConvertFrom-Json @'
[
  {"sha":"R",   "parents":"P100",  "committed":"2026-09-12T10:00:00Z",      "subject":"chore(release): v1.2.3"},
  {"sha":"P100","parents":"P96",   "committed":"2026-09-11T10:00:00Z",      "subject":"feat(actions): an Action (#100)"},
  {"sha":"P96", "parents":"M",     "committed":"2026-09-10T12:00:00Z",      "subject":"fix: a junction (#96)"},
  {"sha":"M",   "parents":"P94 S", "committed":"2026-09-10T10:00:00Z",      "subject":"Merge branch 'side'"},
  {"sha":"S",   "parents":"P94",   "committed":"2026-09-09T10:00:00Z",      "subject":"feat: side work (#55)"},
  {"sha":"P94", "parents":"P80",   "committed":"2026-09-08T10:00:00+03:00", "subject":"docs: an issue number (#94)"},
  {"sha":"P80", "parents":"OLD",   "committed":"2026-09-07T10:00:00Z",      "subject":"fix(land): a pull_request run (#80)"},
  {"sha":"OLD", "parents":"",      "committed":"2026-09-05T10:00:00Z",      "subject":"fix: before the window (#40)"}
]
'@
$landings = @(Get-Landings -Commits $commits -Since $since)
$numbers = @($landings | ForEach-Object { $_.Number })
Assert-Equal '100,96,94,80' ($numbers -join ',') 'the first-parent (#N) commits in the window are landed, tip first'
Assert-Equal $false ($numbers -contains 55) 'a (#N) commit off the first-parent line is not landed'
Assert-Equal $false ($numbers -contains 40) 'a (#N) commit before the window is not counted'
Assert-Equal 4 $numbers.Count 'a release commit with no (#N) is not counted'
Assert-Equal 'fix: a junction (#96)' (@($landings | Where-Object { $_.Number -eq 96 })[0].Subject) 'each landing carries the subject it landed under'
Assert-Equal 0 @(Get-Landings -Commits @() -Since $since).Count 'no commits, nothing landed'

# \d in .NET matches the decimal digits of any script, and the [int] cast right after it would
# read them too -- so a subject ending in another script's digits must not land as an (#N) commit.
$nonAsciiSubject = ConvertFrom-Json @'
[{"sha":"NA","parents":"","committed":"2026-09-10T10:00:00Z","subject":"fix: another script's digits (#٤٢)"}]
'@
Assert-Equal 0 @(Get-Landings -Commits $nonAsciiSubject -Since $since).Count 'a subject ending in another script''s digits is not landed'

# End to end through git: a first-parent line A(09-10) <- B(committer 09-02) <- C(09-08) <- D(09-07)
# <- E(09-01). git log --since stops at B, the first commit older than the cutoff, and would count A
# alone; the read takes the line whole and the walk filters every commit by its own date.
$historyRoot = New-ScratchDir 'loopmetrics-history'
Push-Location -LiteralPath $historyRoot
try {
    git init -q -b master . 2>$null
    foreach ($c in @(
            @('2026-09-01T10:00:00Z', 'E: before the window (#1)'),
            @('2026-09-07T10:00:00Z', 'D (#2)'),
            @('2026-09-08T10:00:00Z', 'C (#3)'),
            @('2026-09-02T10:00:00Z', 'B: committed with a date older than its parent (#4)'),
            @('2026-09-10T10:00:00Z', 'A (#5)'))) {
        $env:GIT_AUTHOR_DATE = $c[0]; $env:GIT_COMMITTER_DATE = $c[0]
        git -c user.name=t -c user.email=t@t -c commit.gpgsign=false commit -q --no-verify --allow-empty -m $c[1]
        if ($LASTEXITCODE -ne 0) { throw "scratch commit '$($c[1])' failed" }
    }
    git update-ref refs/remotes/origin/master HEAD
    $landedThroughGit = @(Get-Landings -Commits (Get-FirstParentCommits -Ref 'origin/master') -Since $since | ForEach-Object { $_.Number })
    Assert-Equal '5,3,2' ($landedThroughGit -join ',') 'an older-dated commit mid-line does not hide the in-window landings behind it'
}
finally {
    Remove-Item Env:GIT_AUTHOR_DATE, Env:GIT_COMMITTER_DATE -ErrorAction SilentlyContinue
    Pop-Location
    Remove-Item -LiteralPath $historyRoot -Recurse -Force
}

$bodies = @{
    100 = "## Summary`r`nAn Action.`r`n`r`nFixes #82`r`n"
    96  = "## Summary`nA fix with no issue behind it."
    94  = $null   # #94 is an issue, not a pull request
    80  = "Fixes #79"
}
$landed = Measure-Landed -Landings $landings -Bodies $bodies
Assert-Equal 2 $landed.Loop 'a PR body with a Fixes line is loop-landed'
Assert-Equal 2 $landed.Rest 'a PR body without one, and an N that is an issue, are the rest'
$midLine = Measure-Landed -Landings @([pscustomobject]@{ Number = 12; Subject = 'docs: a plan (#12)' }) `
    -Bodies @{ 12 = "Split out of the plan; Fixes #11 follows in a later PR." }
Assert-Equal 1 $midLine.Rest 'a Fixes #N in the middle of a line is not a Fixes line'
$bt = [string][char]96
$ticked = Measure-Landed -Landings @([pscustomobject]@{ Number = 12; Subject = 'fix: a gate (#12)' }) `
    -Bodies @{ 12 = "## Summary`n${bt}Fixes #12${bt}`nMore." }
Assert-Equal 1 $ticked.Loop 'a Fixes #N written as inline code on its own line is a Fixes line'
$tickedMid = Measure-Landed -Landings @([pscustomobject]@{ Number = 12; Subject = 'docs: a plan (#12)' }) `
    -Bodies @{ 12 = "Split out of the plan; ${bt}Fixes #11${bt} follows in a later PR." }
Assert-Equal 1 $tickedMid.Rest 'an inline-code Fixes #N in the middle of a line is not a Fixes line'
# Near misses: each must stay "other".
foreach ($near in @(
        @{ Body = "${bt}Fixes #${bt}"; Why = 'no number' },
        @{ Body = "Fixes#12"; Why = 'no space' },
        @{ Body = "${bt}${bt}Fixes #12${bt}${bt}"; Why = 'a double backtick' },
        @{ Body = "  Fixes #12"; Why = 'leading spaces' })) {
    $r = Measure-Landed -Landings @([pscustomobject]@{ Number = 12; Subject = 'docs: x (#12)' }) -Bodies @{ 12 = $near.Body }
    Assert-Equal 1 $r.Rest "a near miss ($($near.Why)) is not a Fixes line"
}
$lower = Measure-Landed -Landings @([pscustomobject]@{ Number = 12; Subject = 'docs: x (#12)' }) -Bodies @{ 12 = 'fixes #12' }
Assert-Equal 1 $lower.Loop 'a lowercase fixes #N still reads as a Fixes line (the match is case-insensitive)'

# --- Rework: the conventional fix type, off the same walk -------------------------------------
# Tip first, one case per commit. Five subjects are typed fix and one of them is before the
# window, so a count that is a share of the landings reports four.
$reworkCommits = ConvertFrom-Json @'
[
  {"sha":"F8","parents":"F7","committed":"2026-09-12T14:00:00Z","subject":"revert: fix(land): a pull_request run (#108)"},
  {"sha":"F7","parents":"F6","committed":"2026-09-12T12:00:00Z","subject":"chore(release): v1.2.3 (#107)"},
  {"sha":"F6","parents":"F5","committed":"2026-09-12T10:00:00Z","subject":"fixup: not a conventional type (#106)"},
  {"sha":"F5","parents":"F4","committed":"2026-09-11T10:00:00Z","subject":"feat(report): fix the wording (#105)"},
  {"sha":"F4","parents":"F3","committed":"2026-09-10T10:00:00Z","subject":"fix(land)!: a breaking scoped repair (#104)"},
  {"sha":"F3","parents":"F2","committed":"2026-09-09T10:00:00Z","subject":"fix!: a breaking repair (#103)"},
  {"sha":"F2","parents":"F1","committed":"2026-09-08T10:00:00Z","subject":"fix(land): a pull_request run (#102)"},
  {"sha":"F1","parents":"OLDFIX","committed":"2026-09-07T10:00:00Z","subject":"fix: a junction (#101)"},
  {"sha":"OLDFIX","parents":"","committed":"2026-09-05T10:00:00Z","subject":"fix: before the window (#100)"}
]
'@
$rework = Measure-Landed -Landings (Get-Landings -Commits $reworkCommits -Since $since) -Bodies @{}
Assert-Equal 8 ($rework.Loop + $rework.Rest) 'rework is counted off the same landings as Landed'
Assert-Equal 4 $rework.Fix 'the fix type is rework: plain, scoped, breaking and breaking scoped'
Assert-Equal 1 (Measure-Landed -Landings @([pscustomobject]@{ Number = 109; Subject = 'Fix: a capitalised type (#109)' }) -Bodies @{}).Fix 'a capitalised Fix type is rework: the type is read without case'
foreach ($case in @(
        @{ subject = 'feat(report): fix the wording (#105)'; what = 'a feat whose summary says fix is not rework' }
        @{ subject = 'fixup: not a conventional type (#106)'; what = 'a type merely starting with fix is not rework' }
        @{ subject = 'chore(release): v1.2.3 (#107)'; what = 'a release landing is not rework' }
        @{ subject = 'revert: fix(land): a pull_request run (#108)'; what = 'a revert of a fix is not rework: the type is read at the subject''s start' })) {
    $one = @(Get-Landings -Commits $reworkCommits -Since $since | Where-Object { $_.Subject -eq $case.subject })
    # The leading 1 pins that the case selected a landing: drift selects none, and 0 of nothing passes.
    Assert-Equal '1,0' "$($one.Count),$((Measure-Landed -Landings $one -Bodies @{}).Fix)" $case.what
}

# --- The report ------------------------------------------------------------------------------
$report = Format-LoopMetrics -Slug 'o/r' -Since $since -Now $now -Answered 1 -OldestOpenDays 4 -OpenCount 2 -Landed $landed
Assert-Equal $true ($report -match '(?m)^\| Landed \| 4 \| 2 loop-landed') 'the report carries the landed row with its split'
Assert-Equal 3 ([regex]::Matches($report, '(?m)^\| (Rulings answered|Landed|Rework) \|').Count) 'the report has exactly the three metric rows'
# The count above names the three; this one pins that no fourth row sits beside them, whatever its name.
Assert-Equal 3 ([regex]::Matches($report, '(?m)^\| (?!Metric \||-)').Count) 'and no fourth metric row under any name'
# Loop, Rest and Fix are all 2 in $landed, so a rework row reading the wrong one would pass there.
$distinct = Format-LoopMetrics -Slug 'o/r' -Since $since -Now $now -Answered 1 -OldestOpenDays 4 -OpenCount 2 `
    -Landed ([pscustomobject]@{ Loop = 2; Rest = 2; Fix = 1 })
Assert-Equal $true ($distinct -match '(?m)^\| Rework \| 1 \|') 'the rework row carries the fix count, not another of the landed numbers'
$culture = [cultureinfo]::CurrentCulture
try {
    [cultureinfo]::CurrentCulture = [cultureinfo]::new('th-TH')   # Thai Buddhist calendar: 2026 is 2569
    $thai = Format-LoopMetrics -Slug 'o/r' -Since $since -Now $now -Answered 0 -OldestOpenDays $null -OpenCount 0 -Landed $landed
}
finally { [cultureinfo]::CurrentCulture = $culture }
Assert-Equal $true ($thai -match '2026-09-06 00:00 to 2026-09-13 00:00 UTC') 'the report''s dates are Gregorian under a culture with another calendar'

# --- reads: stderr is not data, and a failed read throws --------------------------------------
$mixed = @(Invoke-Read pwsh @('-NoProfile', '-Command', '[Console]::Error.WriteLine(''noise on stderr''); ''data''', ''))
Assert-Equal 'data' ($mixed -join '|') 'stderr on a successful read is not part of the data'

$ghCalls = [System.Collections.Generic.List[string]]::new()
$ghReply = @{}
function gh {
    # `repo view` names the repository the rolling resolver posts to (bin/Get-RepoSlug.ps1), which
    # in a repo with no binding it asks before the reads below; the replies here are keyed by the
    # prefixes of those reads, and the count of them is what the cases assert.
    if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
    $line = $args -join ' '
    $ghCalls.Add($line)
    foreach ($prefix in $ghReply.Keys) {
        if ($line.StartsWith($prefix)) { $global:LASTEXITCODE = $ghReply[$prefix].Exit; return $ghReply[$prefix].Out }
    }
    $global:LASTEXITCODE = 0
}

$ghReply['api'] = @{ Exit = 1; Out = 'HTTP 403: rate limit exceeded' }
Assert-Throws { Invoke-Read gh @('api', 'repos/o/r/issues/events') } 'failed \(exit 1\)' 'a failed gh read throws rather than reading as zero'

$ghReply.Clear()
$ghReply['pr view 100'] = @{ Exit = 0; Out = 'Fixes #82' }
$ghReply['pr view 94'] = @{ Exit = 1; Out = 'GraphQL: Could not resolve to a PullRequest with the number of 94. (repository.pullRequest)' }
$ghReply['pr view 95'] = @{ Exit = 1; Out = 'HTTP 401: Bad credentials' }
$ghReply['pr view 96'] = @{ Exit = 1; Out = "GraphQL: Could not resolve to a Repository with the name 'o/no-such-repo'. (repository)" }
Assert-Equal 'Fixes #82' (Get-PullRequestBody -Number 100) 'a pull request''s body is read'
Assert-Equal $null (Get-PullRequestBody -Number 94) 'an N that is an issue, not a pull request, reads as no body'
Assert-Throws { Get-PullRequestBody -Number 95 } 'Bad credentials' 'any other failed pr read throws'
Assert-Throws { Get-PullRequestBody -Number 96 } 'Could not resolve to a Repository' 'a wrong repository throws rather than reading as not a pull request'

# --- the -Comment path -----------------------------------------------------------------------
$ghReply.Clear(); $ghCalls.Clear()
Assert-Equal $null (Send-LoopMetrics -Report 'REPORT' -Title '') 'no loop_runs title declared posts nothing'
Assert-Equal 0 $ghCalls.Count 'no loop_runs title declared makes no gh call'

$ghReply['issue list'] = @{ Exit = 0; Out = '[{"number":5,"title":"Loop runs","state":"OPEN"}]' }
Assert-Equal 5 (Send-LoopMetrics -Report 'REPORT' -Title 'Loop runs') 'an existing loop_runs issue gets the report'
Assert-Equal 1 @($ghCalls | Where-Object { $_ -like 'issue comment 5 --body REPORT' }).Count 'exactly one comment is appended per run'
Assert-Equal 0 @($ghCalls | Where-Object { $_ -like 'issue create*' -or $_ -like 'issue edit*' }).Count 'an existing issue is neither created again nor edited'

$ghCalls.Clear()
$ghReply['issue list'] = @{ Exit = 0; Out = '[]' }
$ghReply['issue create'] = @{ Exit = 0; Out = 'https://github.com/o/r/issues/12' }
Assert-Equal 12 (Send-LoopMetrics -Report 'REPORT' -Title 'Loop runs') 'no loop_runs issue: it is created and gets the report'
Assert-Equal 1 @($ghCalls | Where-Object { $_ -like 'issue create --title Loop runs --label umbrella *' }).Count 'the created issue carries umbrella'
Assert-Equal 1 @($ghCalls | Where-Object { $_ -like 'issue comment 12 --body REPORT' }).Count 'the created issue gets the report as a comment'

$ghCalls.Clear()
$ghReply['issue list'] = @{ Exit = 0; Out = '[]' }
$ghReply['issue create'] = @{ Exit = 0; Out = 'https://github.com/o/r/issues/٤٢' }
Assert-Throws { Send-LoopMetrics -Report 'REPORT' -Title 'Loop runs' } 'printed no issue URL' 'an issue URL ending in another script''s digits reads as no issue number'
Remove-Item Function:\gh

# --- the loop_runs key is optional; declared, it must not be empty ----------------------------
$tool = Join-Path (Split-Path $Script -Parent) 'ouro-binding.py'
if (Test-Path -LiteralPath $tool) {
    $scratch = New-ScratchDir 'loopmetrics'
    New-Item -ItemType Directory -Force -Path (Join-Path $scratch '.claude') | Out-Null
    Push-Location -LiteralPath $scratch
    try {
        git init -q . 2>$null
        $binding = @'
schema = 1
[repo]
slug = "o/n"
default_branch = "master"
[[gate]]
areas = ["*"]
run = "x"
[ship]
policy = "stop-at-pr"
review = "adversarial-review"
[owner]
ruling_approvers = ["me"]
'@
        $binding | Set-Content -LiteralPath '.claude/ouro.toml' -Encoding utf8
        Assert-Equal $null (Get-BindingValue rolling_issues.loop_runs -Optional) 'an undeclared loop_runs reads as no title'
        Assert-Equal 'o/n' (Get-BindingValue repo.slug) 'the repository comes from the binding'
        Assert-Throws { Get-BindingValue rolling_issues.loop_runs } 'no such key' 'a required key that is absent throws'
        ($binding + "`n[rolling_issues]`nloop_runs = `"Loop runs`"`n") | Set-Content -LiteralPath '.claude/ouro.toml' -Encoding utf8
        Assert-Equal 'Loop runs' (Get-BindingValue rolling_issues.loop_runs -Optional) 'a declared loop_runs is read'
        ($binding + "`n[rolling_issues]`nloop_runs = `"`"`n") | Set-Content -LiteralPath '.claude/ouro.toml' -Encoding utf8
        Assert-Throws { Get-BindingValue rolling_issues.loop_runs -Optional } 'resolved to an empty value' 'a loop_runs declared empty throws rather than reading as undeclared'
    }
    finally {
        Pop-Location
        Remove-Item -LiteralPath $scratch -Recurse -Force
    }
}
else { Write-Host "  skip: ouro-binding.py not beside the script (vendored layout)" -ForegroundColor DarkGray }

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall loop-metrics cases pass" -ForegroundColor Green
exit 0
