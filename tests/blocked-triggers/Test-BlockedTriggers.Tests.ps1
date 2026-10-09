<#
.SYNOPSIS
    Unit test for Test-BlockedTriggers.ps1's trigger-line reading, finding kinds and dedupe.
.DESCRIPTION
    Drives the script with inline issue JSON -- no gh, no live backlog -- the same idiom as
    Test-IssueLabelInvariants.Tests.ps1. A -Comment run is driven under a `gh` function shadow
    that records every call instead of writing anywhere, so the dedupe rule and the no-write
    guarantees are both pinned against what the script actually invoked.

    One case per rule the script states, and each is also this suite's mutation table: removing
    the rule the case exercises makes exactly that case fail.

      rule                                          | case that fails without it
      ----------------------------------------------|--------------------------------------------
      trigger marker must be the first line          | 'a marker below a heading first line...'
      BOM + surrounding whitespace stripped first    | 'a BOM and leading blank...'
      references read only from the trigger line     | 'a reference on line two...'
      cross-repo citation is skipped                 | 'a repo-prefixed reference...'
      every reference must be closed to fire         | 'one open reference blocks the finding'
      an unreadable reference counts as open          | 'an unreadable reference blocks the finding'
      a trigger with no reference is not a finding    | 'a trigger naming nothing...'
      fingerprint lists references ascending          | 'two closed references fire...'
      dedupe: identical fingerprint stays silent      | 'a repeated identical finding...'
      dedupe: a changed reference set posts again      | 'one more closed reference reopens it'
      a marker mid-comment is not a marker             | 'a marker quoted mid-comment...'
      dedupe reads only the bot's and approvers'       | 'a stranger's fingerprint does not silence...'
      no binding tool beside the script: bot only      | 'with no binding tool an approver's fingerprint...'
      a binding that cannot be read fails the gate     | 'a binding that cannot be read fails the gate...'
      no label/close/rolling-issue gh call ever fires  | 'no recorded gh call...'
      a failed post throws with gh's own output        | 'a failed comment post throws'
#>
$ErrorActionPreference = 'Stop'
# A marker counts from the bot or a ruling approver; the binding read is stood in for here.
$PSDefaultParameterValues['Get-TrustedComments:Approvers'] = @('Approver1')
# Two levels up is the plugin root (script under bin/) or, once vendored, the scripts dir itself.
$Base   = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Script = @((Join-Path $Base 'Test-BlockedTriggers.ps1'), (Join-Path $Base 'bin/Test-BlockedTriggers.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Script) { throw "Test-BlockedTriggers.ps1 not found under $Base" }

$failures = 0
function Assert-Match($Pattern, $Output, $What) {
    if ($Output -match $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- no match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}
function Assert-NoMatch($Pattern, $Output, $What) {
    if ($Output -notmatch $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- unexpected match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}
function Assert-Equal($Expected, $Actual, $What) {
    if ("$Expected" -eq "$Actual") { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}
# Write-Host writes to the information stream (6), not stdout -- 2>&1 captures nothing here.
function Run($issuesJson, $refStatesJson = '') {
    (& $Script -IssuesJson $issuesJson -ReferenceStatesJson $refStatesJson 6>&1 | Out-String)
}
# RunComment runs in a repo that has a binding unless a row hands it its own directory: without one
# only the bot counts and the approver rows cannot hold.
$bound = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('blocked-bound-' + [guid]::NewGuid().ToString('N')))
Push-Location -LiteralPath $bound.FullName
try {
    git init -q .
    New-Item -ItemType Directory -Path '.claude' | Out-Null
    Set-Content -LiteralPath '.claude/ouro.toml' -Value "[owner]`nruling_approvers = [`"Approver1`"]"
}
finally { Pop-Location }
# A -Comment run under a `gh` shadow that records every call rather than writing anywhere.
function RunComment($issuesJson, $refStatesJson = '', $in = $bound.FullName) {
    $global:blockedGhCalls = [System.Collections.Generic.List[string]]::new()
    function gh { $global:blockedGhCalls.Add($args -join ' '); $global:LASTEXITCODE = 0 }
    Push-Location -LiteralPath $in
    try { (& $Script -IssuesJson $issuesJson -ReferenceStatesJson $refStatesJson -Comment 6>&1 | Out-String) }
    finally { Pop-Location; Remove-Item Function:\gh -ErrorAction Ignore }
}

# --- fired: two closed references, ascending, in one comment ---------------------------------
$fired = Run '[{"number":1,"title":"a","body":"**Unblocks when:** #12 and #5 both close","comments":[]}]' `
    '{"12":"closed","5":"closed"}'
Assert-Match 'fired: #5, #12' $fired 'two closed references fire, listed ascending regardless of body order'

# --- one open reference blocks the finding ----------------------------------------------------
$oneOpen = Run '[{"number":2,"title":"b","body":"**Unblocks when:** #12 and #5 both close","comments":[]}]' `
    '{"12":"closed","5":"open"}'
Assert-NoMatch 'fired' $oneOpen 'one open reference blocks the finding'
Assert-NoMatch '#2:.*no trigger line' $oneOpen 'a valid trigger line with an open reference is not the no-trigger-line kind either'

# --- an unreadable reference counts as open -----------------------------------------------------
$unread = Run '[{"number":3,"title":"c","body":"**Unblocks when:** #99 closes","comments":[]}]' '{}'
Assert-NoMatch 'fired' $unread 'an unreadable reference blocks the finding'
Assert-Match 'reference #99 is unreadable, counted as open' $unread 'the run reports the reference as unreadable'

# --- a heading first line has no trigger line ---------------------------------------------------
$heading = Run '[{"number":4,"title":"d","body":"## Not a trigger\n\nmore text","comments":[]}]'
Assert-Match 'no trigger line' $heading 'a heading first line has no trigger line'
# The marker on line two, below a heading, with its reference closed: a whole-body marker search
# would fire it. The rows above that fire (issue 12 on line one) are the control.
$headingThenMarker = Run '[{"number":13,"title":"m","body":"## heading\n**Unblocks when:** #1 closes","comments":[]}]' '{"1":"closed"}'
Assert-Match   '#13:.*no trigger line' $headingThenMarker 'a marker below a heading first line is not a trigger line'
Assert-NoMatch 'fired' $headingThenMarker 'so its closed reference fires nothing'

# --- a blank first line has no trigger line, same as a heading ----------------------------------
$blank = Run '[{"number":5,"title":"e","body":"\nsome text after a blank line","comments":[]}]'
Assert-Match 'no trigger line' $blank 'a blank first line has no trigger line'

# --- BOM and surrounding whitespace are stripped before the marker is compared -------------------
$bom = Run (@'
[{"number":6,"title":"f","body":"﻿   **Unblocks when:** #12 closes  ","comments":[]}]
'@) '{"12":"closed"}'
Assert-Match 'fired: #12' $bom 'a BOM and leading/trailing whitespace do not stop the marker matching'

# --- a reference on line two is not read; only the trigger line counts --------------------------
$lineTwo = Run '[{"number":7,"title":"g","body":"**Unblocks when:** an event\nsee also #12","comments":[]}]' '{"12":"closed"}'
Assert-NoMatch 'fired' $lineTwo 'a reference on line two of the body is never read'
Assert-Match 'not checkable' $lineTwo 'a trigger line with no reference of its own is not checkable, not a finding'

# --- a repo-prefixed reference is skipped, and does not hold back or create a finding ------------
$crossRepo = Run '[{"number":8,"title":"h","body":"**Unblocks when:** other/repo#99 and #12 close","comments":[]}]' `
    '{"12":"closed","99":"open"}'
Assert-Match 'fired: #12' $crossRepo 'the cross-repo reference is skipped; the local one alone fires it'
$crossRepoOnly = Run '[{"number":9,"title":"i","body":"**Unblocks when:** other/repo#99 closes","comments":[]}]' '{"99":"open"}'
Assert-Match 'not checkable' $crossRepoOnly 'a trigger line naming only a cross-repo reference is not checkable, not a finding'

# --- dedupe: an identical fingerprint already posted stays silent; a changed one posts again -----
$fingerprint = '**Blocked check** (automated): fired #12'
$issueWithComment = '[{"number":10,"title":"j","body":"**Unblocks when:** #12 closes","comments":[{"author":{"login":"github-actions"},"body":"' + $fingerprint + '\n\nmore text"}]}]'
RunComment $issueWithComment '{"12":"closed"}' | Out-Null
Assert-Equal 0 $blockedGhCalls.Count 'a repeated identical finding posts nothing'

$issueWithWiderRef = '[{"number":10,"title":"j","body":"**Unblocks when:** #12 and #34 close","comments":[{"author":{"login":"github-actions"},"body":"' + $fingerprint + '\n\nmore text"}]}]'
RunComment $issueWithWiderRef '{"12":"closed","34":"closed"}' | Out-Null
Assert-Equal 1 $blockedGhCalls.Count 'one more closed reference changes the fingerprint, so the run posts again'
Assert-Match 'issue comment 10 --body' $blockedGhCalls[0] 'the post names the issue it is about'
Assert-Match 'fired #12, #34' $blockedGhCalls[0] 'the new post carries the widened fingerprint'

# --- a marker quoted mid-comment does not silence a finding --------------------------------------
$midComment = '[{"number":11,"title":"k","body":"**Unblocks when:** #12 closes","comments":[{"author":{"login":"github-actions"},"body":"see ' + $fingerprint + ' above"}]}]'
RunComment $midComment '{"12":"closed"}' | Out-Null
Assert-Equal 1 $blockedGhCalls.Count 'a marker not on a comment''s first line does not count, so the run still posts'

# --- the dedupe reads only the bot's and the approvers' comments ----------------------------------
# The rows above (10, 11) are the bot's. Every row here carries the same fingerprint and differs in its author alone.
$body = '"body":"' + $fingerprint + '"'
foreach ($case in @(
        @{ n = 30; a = '"author":{"login":"stranger"},';          posts = 1; what = 'a stranger''s fingerprint does not silence the notice' },
        @{ n = 31; a = '"author":{"login":"xgithub-actions"},';   posts = 1; what = 'a login merely holding the bot''s name does not silence it' },
        @{ n = 32; a = '"author":{"login":"github-actions-x"},';  posts = 1; what = 'a login the bot''s name merely starts does not silence it' },
        @{ n = 33; a = '"author":null,';                          posts = 1; what = 'a comment with a null author does not silence it' },
        @{ n = 34; a = '';                                        posts = 1; what = 'a comment with no author does not silence it' },
        @{ n = 35; a = '"author":{"login":"Approver1"},';         posts = 0; what = 'a ruling approver''s fingerprint silences it' },
        @{ n = 36; a = '"author":{"login":"approver1"},';         posts = 0; what = 'an approver''s login in another case silences it' },
        @{ n = 37; a = '"author":{"login":"GitHub-Actions"},';    posts = 0; what = 'the bot''s login in another case silences it' })) {
    RunComment ('[{"number":' + $case.n + ',"title":"s","body":"**Unblocks when:** #12 closes","comments":[{' + $case.a + $body + '}]}]') '{"12":"closed"}' | Out-Null
    Assert-Equal $case.posts $blockedGhCalls.Count $case.what
}

# Only the trusted comments are compared: a stranger after the bot is not the last thing said, and a
# stranger before the bot does not stand in for it.
$stale = '"body":"**Blocked check** (automated): fired #12, #34"'
RunComment ('[{"number":38,"title":"s","body":"**Unblocks when:** #12 closes","comments":[{"author":{"login":"github-actions"},' + $body + '},{"author":{"login":"stranger"},' + $stale + '}]}]') '{"12":"closed"}' | Out-Null
Assert-Equal 0 $blockedGhCalls.Count 'a stranger''s later marker comment does not displace the bot''s as the last thing said'
RunComment ('[{"number":39,"title":"s","body":"**Unblocks when:** #12 closes","comments":[{"author":{"login":"stranger"},' + $body + '},{"author":{"login":"github-actions"},' + $stale + '}]}]') '{"12":"closed"}' | Out-Null
Assert-Equal 1 $blockedGhCalls.Count 'the bot''s newest comment is the one compared, whatever a stranger said before it'

# Without a binding there are no approvers to read: the bot alone counts, and the run says so.
$bare = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('blocked-nobinding-' + [guid]::NewGuid().ToString('N')))
Push-Location -LiteralPath $bare.FullName
try {
    git init -q .
    $bareApprover = RunComment ('[{"number":40,"title":"s","body":"**Unblocks when:** #12 closes","comments":[{"author":{"login":"Approver1"},' + $body + '}]}]') '{"12":"closed"}' (Get-Location).Path
    $approverPosts = $blockedGhCalls.Count
    $bareBot = RunComment ('[{"number":41,"title":"s","body":"**Unblocks when:** #12 closes","comments":[{"author":{"login":"github-actions"},' + $body + '}]}]') '{"12":"closed"}' (Get-Location).Path
    $botPosts = $blockedGhCalls.Count
}
finally { Pop-Location }
Remove-Item -LiteralPath $bare.FullName -Recurse -Force
Assert-Equal 1 $approverPosts 'with no binding an approver''s fingerprint does not silence the notice'
Assert-Equal 0 $botPosts 'with no binding the bot''s fingerprint still does'
Assert-Match 'INFO - no \.claude/ouro\.toml' $bareApprover 'a repo with no binding says only the bot counts'

# A binding the repo has, read by a vendored copy with no ouro-binding.py beside it: the bot alone
# counts there too, and the run says so, rather than dying on python's "can't open file".
$repoDir = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('blocked-vendored-' + [guid]::NewGuid().ToString('N')))
$vdir = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('blocked-notool-' + [guid]::NewGuid().ToString('N')))
$realScript = $Script
try {
    foreach ($dep in 'Test-BlockedTriggers.ps1', 'Get-RepoSlug.ps1', 'Get-RollingIssue.ps1') {
        Copy-Item -LiteralPath (Join-Path (Split-Path $realScript -Parent) $dep) -Destination $vdir.FullName
    }
    $Script = Join-Path $vdir.FullName 'Test-BlockedTriggers.ps1'
    Push-Location -LiteralPath $repoDir.FullName
    try {
        git init -q .
        New-Item -ItemType Directory -Path '.claude' | Out-Null
        Set-Content -LiteralPath '.claude/ouro.toml' -Value '[owner]'
        $noToolApprover = RunComment ('[{"number":42,"title":"s","body":"**Unblocks when:** #12 closes","comments":[{"author":{"login":"Approver1"},' + $body + '}]}]') '{"12":"closed"}' (Get-Location).Path
        $noToolApproverPosts = $blockedGhCalls.Count
        RunComment ('[{"number":43,"title":"s","body":"**Unblocks when:** #12 closes","comments":[{"author":{"login":"github-actions"},' + $body + '}]}]') '{"12":"closed"}' (Get-Location).Path | Out-Null
        $noToolBotPosts = $blockedGhCalls.Count
    }
    finally { Pop-Location; $Script = $realScript }
}
finally {
    Remove-Item -LiteralPath $repoDir.FullName -Recurse -Force
    Remove-Item -LiteralPath $vdir.FullName -Recurse -Force
}
Assert-Equal 1 $noToolApproverPosts 'with no binding tool an approver''s fingerprint does not silence the notice'
Assert-Equal 0 $noToolBotPosts 'with no binding tool the bot''s fingerprint still does'
Assert-Match 'INFO - no ouro-binding\.py beside this script' $noToolApprover 'a vendored copy with no binding tool says only the bot counts'

# A binding that is there and cannot be read fails the gate: the default approvers stand-in is
# dropped so the real read runs.
$badDir = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('blocked-badtoml-' + [guid]::NewGuid().ToString('N')))
$standIn = $PSDefaultParameterValues['Get-TrustedComments:Approvers']
$PSDefaultParameterValues.Remove('Get-TrustedComments:Approvers')
$badThrown = ''
Push-Location -LiteralPath $badDir.FullName
try {
    git init -q .
    New-Item -ItemType Directory -Path '.claude' | Out-Null
    Set-Content -LiteralPath '.claude/ouro.toml' -Value 'this is = = not toml'
    try { RunComment ('[{"number":44,"title":"s","body":"**Unblocks when:** #12 closes","comments":[{"author":{"login":"github-actions"},' + $body + '}]}]') '{"12":"closed"}' (Get-Location).Path | Out-Null }
    catch { $badThrown = "$_" }
}
finally {
    Pop-Location
    $PSDefaultParameterValues['Get-TrustedComments:Approvers'] = $standIn
    Remove-Item -LiteralPath $badDir.FullName -Recurse -Force
}
Assert-Match 'ouro-binding\.py get owner\.ruling_approvers failed' $badThrown 'a binding that cannot be read fails the gate instead of trusting the bot alone'

# --- no-trigger-line findings post too, and with their own fingerprint --------------------------
$noTrigger = '[{"number":12,"title":"l","body":"## no marker here","comments":[]}]'
RunComment $noTrigger | Out-Null
Assert-Equal 1 $blockedGhCalls.Count 'a no-trigger-line finding posts one comment'
Assert-Match 'no trigger line' $blockedGhCalls[0] 'its fingerprint names the kind'

# --- no recorded gh call ever adds/removes a label, closes an issue, or names the rolling issue ---
$mixed = '[' +
    '{"number":20,"title":"fired","body":"**Unblocks when:** #1 closes","comments":[]},' +
    '{"number":21,"title":"no-trigger","body":"no marker","comments":[]}' +
    ']'
RunComment $mixed '{"1":"closed"}' | Out-Null
Assert-Equal 2 $blockedGhCalls.Count 'both findings post exactly one comment each'
foreach ($call in $blockedGhCalls) {
    Assert-NoMatch '--add-label|--remove-label|issue close|issue edit' $call "no recorded gh call relabels or closes ($call)"
    Assert-NoMatch 'Docs drift audit|Loop runs' $call "no recorded gh call names a rolling issue ($call)"
}

# --- a FAILED gh issue list throws instead of reporting a clean empty backlog -----
function gh {
    if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
    Write-Error 'failed to search issues: HTTP 403: Resource not accessible by integration' -ErrorAction Continue
    $global:LASTEXITCODE = 1
}
$thrown = ''
$out    = ''
try { $out = (& $Script 6>&1 | Out-String) } catch { $thrown = "$_" }
Assert-Match   'gh issue list failed \(exit 1\)' $thrown 'a failed gh issue list throws'
Assert-Match   'HTTP 403'                        $thrown 'the throw carries the captured stderr'
Assert-NoMatch 'No blocked-trigger findings'     $out    'a gh failure is never a clean empty backlog'
Remove-Item Function:\gh -ErrorAction Ignore

# --- a failed comment post throws, carrying gh's own output ------------------------------------------
function gh {
    if ($args[0] -eq 'issue' -and $args[1] -eq 'comment') {
        Write-Error 'HTTP 403: Resource not accessible by integration' -ErrorAction Continue
        $global:LASTEXITCODE = 1
        return
    }
    $global:LASTEXITCODE = 0
}
$thrown = ''
try {
    & $Script -IssuesJson '[{"number":14,"title":"n","body":"**Unblocks when:** #1 closes","comments":[]}]' `
        -ReferenceStatesJson '{"1":"closed"}' -Comment 6>&1 | Out-Null
} catch { $thrown = "$_" }
Remove-Item Function:\gh -ErrorAction Ignore
Assert-Match 'gh issue comment 14 failed \(exit 1\)' $thrown 'a failed comment post throws'
Assert-Match 'HTTP 403' $thrown 'and the throw carries gh''s own output'

Remove-Item -LiteralPath $bound.FullName -Recurse -Force
if ($failures) { Write-Host "`n$failures failure(s)." -ForegroundColor Red; exit 1 }
Write-Host "`nAll blocked-trigger tests passed." -ForegroundColor Green
exit 0
