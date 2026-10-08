<#
.SYNOPSIS
    Unit test for Get-IntakeTargets.ps1's selection, its reads and its output.
.DESCRIPTION
    Drives the whole script with an injected world -- the issue list, the rulings list and the
    rolling titles as fixture JSON -- so no gh and no live backlog. A test that queried the
    real repository would pass or fail on whatever anyone filed that morning, which is how a
    test earns deletion.

    Pinned: one case per exclusion class, each fixture shaped so only its own rule can drop it --
    a rolling issue carrying nothing but needs-triage, an issue carrying another state and
    neither of the other two marks, an issue carrying the marker and nothing else. Then the
    shapes most likely to be mistaken for them: a title that merely contains a rolling title, a
    case variant of one, a label merely prefixed like a state, a marker quoted mid-comment, one
    in lower case, and a human comment that OPENS with the marker's first words but is not a
    verdict. Then the shapes that must still be caught: a marker in the second comment rather
    than the first, one indented by a space, and a state label in another case -- GitHub label
    names are case-insensitive and so is the match.

    Then the reads. A failed gh throws rather than reporting a quiet week; stderr noise on a
    successful one does not break the parse; a page that fills the limit throws rather than
    passing off a truncated window as the whole of it; and a body that is not a whole JSON array
    throws, because ConvertFrom-Json turns an empty body, a whitespace one and a `null` into
    nothing at all -- which is what an empty window looks like -- and an unterminated array into
    the rows it happened to get.
#>
$ErrorActionPreference = 'Stop'
# Two levels up is the plugin root (script under bin/) or, once vendored, the scripts dir itself.
$Base   = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Script = @((Join-Path $Base 'Get-IntakeTargets.ps1'), (Join-Path $Base 'bin/Get-IntakeTargets.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Script) { throw "Get-IntakeTargets.ps1 not found under $Base" }

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -eq $Actual) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}
function Assert-Throws($Block, $Match, $What) {
    try { & $Block; Write-Host "FAIL: $What -- did not throw" -ForegroundColor Red; $script:failures++ }
    catch {
        if ("$_" -match $Match) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
        else { Write-Host "FAIL: $What -- threw '$_', expected /$Match/" -ForegroundColor Red; $script:failures++ }
    }
}

$Rolling = @('Docs drift audit', 'Loop runs')
# The rulings queue is its own read: three open needs-ruling issues, unrelated to the window.
$RulingsJson = '[{"number":301},{"number":302},{"number":303}]'

function Run {
    param([string]$Json, [hashtable]$Extra = @{})
    (& $Script -NewSince '2026-09-09' -IssuesJson $Json -RulingsJson $RulingsJson `
        -RollingIssueTitles $Rolling @Extra | Out-String | ConvertFrom-Json)
}
# The numbers the selection kept, and the reason it recorded for one it dropped.
function Kept($Result) { @($Result.targets | ForEach-Object { $_.number }) -join ',' }
function Reason($Result, $Number) { @($Result.excluded | Where-Object { $_.number -eq $Number })[0].reason }

# --- one case per exclusion class, plus the two shapes that must survive ---------------------
# issue 3 carries nothing but needs-triage, so only the rolling rule can drop it; issue 4 carries no marker
# and a title of its own, so only the state rule can; issue 5 only the marker rule. Each mutation of
# the selector therefore shows up as exactly one of these three.
$fixture = @'
[
  {"number":1,"title":"a new issue","body":"body one","labels":[{"name":"needs-triage"}],"comments":[]},
  {"number":2,"title":"arrived with no state label","body":"body two","labels":[{"name":"bug"}],"comments":[]},
  {"number":3,"title":"Docs drift audit","body":"the ledger","labels":[{"name":"needs-triage"}],"comments":[]},
  {"number":4,"title":"waiting on an event","body":"body four","labels":[{"name":"blocked"}],"comments":[]},
  {"number":5,"title":"graded last week","body":"body five","labels":[{"name":"needs-triage"}],"comments":[{"body":"**Intake triage** (automated)\r\n\r\nNEEDS-RULING - a question."}]}
]
'@
$got = Run $fixture
Assert-Equal '1,2' (Kept $got) 'the selection admits exactly the ungraded issues of the window'
Assert-Equal 'rolling issue'        (Reason $got 3) 'a rolling issue is excluded, and named as one'
Assert-Equal 'state label: blocked' (Reason $got 4) 'an issue carrying another state label is excluded, and the label is named'
Assert-Equal 'intake marker'        (Reason $got 5) 'an issue already carrying the marker is excluded'
Assert-Equal 3 @($got.excluded).Count 'exactly the three exclusions fired'
Assert-Equal 'needs-triage' @($got.targets | Where-Object { $_.number -eq 1 }).labels[0].name `
    'an issue carrying only needs-triage is a target: the absence of a verdict is not one'

# The rulings queue rides along so the grading pass needs no second query for it.
Assert-Equal 3 $got.rulingsQueue 'the rulings-queue count is the fixture''s'
Assert-Equal '2026-09-09' $got.newSince 'the window the file was built for is recorded on it'

# The fields a grading pass reads survive the round trip: a target without its body and comments
# would send the session back to gh for them.
$first = @($got.targets | Where-Object { $_.number -eq 1 })[0]
Assert-Equal 'body one' $first.body 'a target carries its body'
Assert-Equal 'Docs drift audit' @($got.excluded | Where-Object { $_.number -eq 3 })[0].title `
    'an excluded row carries its title, so a CI log says what was dropped'
$marked = Run '[{"number":9,"title":"t","body":"b","labels":[{"name":"needs-triage"}],"comments":[{"body":"a note"},{"body":"another"}]}]'
Assert-Equal 2 @(@($marked.targets)[0].comments).Count 'a target carries its comments'

# --- the shapes most likely to be mistaken for an exclusion ----------------------------------
# Exact-title match, never `in:title`: token search matches any title carrying the same words.
$nearTitle = Run '[{"number":10,"title":"Docs drift audit follow-up","body":"b","labels":[{"name":"needs-triage"}],"comments":[]},{"number":11,"title":"docs drift audit","body":"b","labels":[{"name":"needs-triage"}],"comments":[]}]'
Assert-Equal '10,11' (Kept $nearTitle) 'a title that merely contains a rolling title, and a case variant of one, are ordinary issues'

$nearLabel = Run '[{"number":12,"title":"t","body":"b","labels":[{"name":"needs-triage-later"},{"name":"needs-triage"}],"comments":[]}]'
Assert-Equal '12' (Kept $nearLabel) 'a label merely prefixed like a state is not a state label'

$graded = Run '[{"number":13,"title":"t","body":"b","labels":[{"name":"agent-ready"}],"comments":[]},{"number":14,"title":"t","body":"b","labels":[{"name":"umbrella"}],"comments":[]}]'
Assert-Equal '' (Kept $graded) 'every state other than needs-triage excludes'
Assert-Equal 'state label: agent-ready' (Reason $graded 13) 'the state that excluded is the one named'

# A design being shaped is a graded state: the owner converts it, the intake never grades it.
$shaped = Run '[{"number":34,"title":"t","body":"b","labels":[{"name":"architecture"}],"comments":[]}]'
Assert-Equal '' (Kept $shaped) 'an issue carrying architecture is graded, so never a target'
Assert-Equal 'state label: architecture' (Reason $shaped 34) 'and architecture is the state named'

# GitHub label names are case-insensitive, and so is the match: a Blocked is a blocked, and a
# Needs-Triage is still the absence of a verdict rather than a second state.
$labelCase = Run '[{"number":30,"title":"t","body":"b","labels":[{"name":"Blocked"}],"comments":[]},{"number":31,"title":"t","body":"b","labels":[{"name":"Needs-Triage"}],"comments":[]}]'
Assert-Equal '31' (Kept $labelCase) 'a state label in another case still excludes, and a Needs-Triage is still a target'
Assert-Equal 'state label: Blocked' (Reason $labelCase 30) 'the excluded label is named as the issue spells it'

# The marker is the comment's FIRST line, case included, and carries `(automated)`: without that
# suffix a human comment opening with the same words would silence the issue for every pass.
$nearMarker = Run '[{"number":15,"title":"t","body":"b","labels":[{"name":"needs-triage"}],"comments":[{"body":"as discussed:\n**Intake triage** (automated)\nlooks right"}]},{"number":16,"title":"t","body":"quoting **Intake triage** (automated) in the body","labels":[{"name":"needs-triage"}],"comments":[]},{"number":17,"title":"t","body":"b","labels":[{"name":"needs-triage"}],"comments":[{"body":"**intake triage** (automated)\n\nlower case"}]},{"number":19,"title":"t","body":"b","labels":[{"name":"needs-triage"}],"comments":[{"body":"**Intake triage** got this wrong, please re-run\n\n- the owner"}]}]'
Assert-Equal '15,16,17,19' (Kept $nearMarker) 'a marker quoted mid-comment or in a body, one in lower case, and a human comment merely opening with its words, are not the marker'

$lf = Run '[{"number":18,"title":"t","body":"b","labels":[{"name":"needs-triage"}],"comments":[{"body":"**Intake triage** (automated)\n\nPROMOTE."}]}]'
Assert-Equal '' (Kept $lf) 'the marker is read whether the comment arrives with LF or CRLF line breaks'

# The realistic shape: the verdict is rarely the first comment. An author's note, or triage's own
# **Triage** marker, usually precedes it.
$second = Run '[{"number":32,"title":"t","body":"b","labels":[{"name":"needs-triage"}],"comments":[{"body":"**Triage**\n\nPROMOTE - verified."},{"body":"**Intake triage** (automated)\n\nNEEDS-RULING."}]}]'
Assert-Equal '' (Kept $second) 'a marker in the second comment is found: every comment is read, not just the first'

$indented = Run '[{"number":33,"title":"t","body":"b","labels":[{"name":"needs-triage"}],"comments":[{"body":"  **Intake triage** (automated)\n\nindented by a space"}]}]'
Assert-Equal '' (Kept $indented) 'a marker line indented by whitespace is still the marker'

$empty = Run '[]'
Assert-Equal 0 @($empty.targets).Count 'an empty window selects nothing and does not throw'
Assert-Equal 3 $empty.rulingsQueue 'the rulings count is read even when the window is empty'

# --- -OutFile: the handed file is what the pass reads ----------------------------------------
$outFile = Join-Path ([IO.Path]::GetTempPath()) ('intake-targets-' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.json')
try {
    & $Script -NewSince '2026-09-09' -IssuesJson $fixture -RulingsJson $RulingsJson -RollingIssueTitles $Rolling -OutFile $outFile
    $onDisk = Get-Content -LiteralPath $outFile -Raw | ConvertFrom-Json
    Assert-Equal '1,2' (Kept $onDisk) 'the written file holds the same selection'
    Assert-Equal 3 $onDisk.rulingsQueue 'the written file holds the rulings count'
}
finally { Remove-Item -LiteralPath $outFile -Force -ErrorAction SilentlyContinue }

# --- the window is a parameter, and an unreadable one is not a week --------------------------
Assert-Throws { & $Script -NewSince '09/09/2026' -IssuesJson '[]' -RulingsJson '[]' -RollingIssueTitles @() } `
    'is not a yyyy-MM-dd date' 'a window in another format throws rather than selecting the wrong set'
Assert-Throws { & $Script -NewSince 'last week' -IssuesJson '[]' -RulingsJson '[]' -RollingIssueTitles @() } `
    'is not a yyyy-MM-dd date' 'a window that is not a date at all throws'

# --- a page that fills the limit is a truncated window, not the whole of it -------------------
Assert-Throws { & $Script -NewSince '2026-09-09' -Limit 2 -RollingIssueTitles @() -RulingsJson '[]' `
        -IssuesJson '[{"number":1,"title":"a","labels":[],"comments":[]},{"number":2,"title":"b","labels":[],"comments":[]}]' } `
    'filled the -Limit page' 'an issue page that fills the limit throws'
Assert-Throws { & $Script -NewSince '2026-09-09' -Limit 2 -RollingIssueTitles @() `
        -IssuesJson '[{"number":1,"title":"a","labels":[],"comments":[]}]' -RulingsJson '[{"number":1},{"number":2}]' } `
    'the count would understate the queue' 'a rulings page that fills the limit throws'

# --- a body that is not a whole JSON array is not a quiet week --------------------------------
# Every one of these parses without error: '', whitespace and `null` to nothing at all, a bare
# object and a bare string to one row, and an unterminated array to the rows it got -- so none of
# them is visible in an exit code, and the first three are indistinguishable from an empty window.
# Each case pins the MESSAGE it fails with, not merely that something failed. The three checks
# overlap -- an empty body would also fail the bracket test -- so an assertion that only caught
# "it threw" would pass with the empty-body check deleted, and the operator would get "did not
# return a whole JSON array" for a read that returned nothing at all.
foreach ($bad in @(
        @{ text = '';                          what = 'an empty body';                    msg = 'returned an empty body' },
        @{ text = "   `n  ";                   what = 'a whitespace-only body';           msg = 'returned an empty body' },
        @{ text = 'null';                      what = 'a literal null';                   msg = 'did not return a whole JSON array' },
        @{ text = '{"message":"oops"}';        what = 'a bare object';                    msg = 'did not return a whole JSON array' },
        @{ text = '"hello"';                   what = 'a bare string';                    msg = 'did not return a whole JSON array' },
        @{ text = '[{"number":1,"title":"a"';  what = 'an unterminated array';            msg = 'did not return a whole JSON array' },
        @{ text = '[{"title":"a"}]';           what = 'a row carrying no issue number';   msg = 'returned a row carrying no issue number' })) {
    Assert-Throws { & $Script -NewSince '2026-09-09' -RollingIssueTitles @() -RulingsJson '[]' -IssuesJson $bad.text } `
        "IssuesJson $($bad.msg)" "$($bad.what) from the issue read throws, saying so in those words"
}
Assert-Throws { & $Script -NewSince '2026-09-09' -RollingIssueTitles @() -IssuesJson '[]' -RulingsJson 'null' } `
    'RulingsJson did not return a whole JSON array' 'the rulings read is shape-checked too, not just the issue read'

# --- a FAILED gh read throws instead of reporting a quiet week -----------------
# A function shadow stands in for the native gh: stderr as an error record, exit code set. Every
# shadow here answers `repo view`, the call that names the repository (bin/Get-RepoSlug.ps1): in a
# repo with no binding it is the first call the selection makes, and a shadow that failed it would
# throw on the naming rather than on the read under test. It is not counted below either: the
# count is of the reads the selection makes.
function gh {
    if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
    Write-Error 'failed to search issues: HTTP 403: Resource not accessible by integration' -ErrorAction Continue
    $global:LASTEXITCODE = 1
}
$thrown = ''
$out = ''
try { $out = (& $Script -NewSince '2026-09-09' -RollingIssueTitles @() | Out-String) } catch { $thrown = "$_" }
Assert-Equal $true ($thrown -match '^gh issue list .*failed \(exit 1\)') 'a failed gh issue list throws'
Assert-Equal $true ($thrown -match 'HTTP 403')          'the throw carries the captured stderr'
Assert-Equal ''    $out.Trim()                          'a gh failure never emits a target list'

# --- stderr noise on a SUCCESSFUL read must not break the parse -------------------------------
function gh {
    if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
    $line = $args -join ' '
    Write-Error 'A new release of gh is available' -ErrorAction Continue
    $global:LASTEXITCODE = 0
    if ($line -match '--label needs-ruling') { '[{"number":301}]' }
    else { '[{"number":20,"title":"t","body":"b","labels":[{"name":"needs-triage"}],"comments":[]}]' }
}
$noisy = (& $Script -NewSince '2026-09-09' -RollingIssueTitles @() | Out-String | ConvertFrom-Json)
Assert-Equal '20' (Kept $noisy) 'stderr noise on exit 0 does not break parsing'
Assert-Equal 1 $noisy.rulingsQueue 'the rulings read is parsed past its own noise'

# The window reaches gh as the created:>= qualifier it was handed, not a reformatted one, and the
# rulings queue is a read of its own -- every call is recorded, since the last one is the rulings.
$ghCalls = [System.Collections.Generic.List[string]]::new()
function gh {
    if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
    $line = $args -join ' '
    $ghCalls.Add($line)
    $global:LASTEXITCODE = 0
    '[]'
}
$null = & $Script -NewSince '2026-09-09' -RollingIssueTitles @()
Assert-Equal 2 $ghCalls.Count 'the selection makes exactly two reads'
Assert-Equal 1 @($ghCalls | Where-Object { $_ -match 'issue list --state open --search created:>=2026-09-09' }).Count `
    'the window is searched as the date it was handed'
Assert-Equal 1 @($ghCalls | Where-Object { $_ -match 'issue list --label needs-ruling --state open' }).Count `
    'the rulings queue is counted by a read of its own, so the session needs no second query'
Remove-Item Function:\gh

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall intake-target cases pass" -ForegroundColor Green
exit 0
