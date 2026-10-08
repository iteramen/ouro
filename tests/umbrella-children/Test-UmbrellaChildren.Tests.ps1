<#
.SYNOPSIS
    Unit test for Test-UmbrellaChildren.ps1's child-reference parsing and its three findings.
.DESCRIPTION
    Drives the script with inline issue, sub-issue and reference-state JSON -- no gh, no live
    backlog -- the idiom Test-IssueLabelInvariants.Tests.ps1 and Test-BlockedTriggers.Tests.ps1
    both use. A -Comment run is driven under a `gh` function shadow that records every call
    instead of writing anywhere.

    One case per rule the script states, and each is also this suite's mutation table: removing
    the rule the case exercises makes exactly that case fail.

      rule                                              | case that fails without it
      ----------------------------------------------|--------------------------------------------
      bulleted/task-list/numbered/indented all count  | 'four child shapes, all linked...'
      a table-row number is never a child             | 'a table row...linked'/'...unlinked'
      a prose number is never a child                  | 'prose...linked'/'prose...unlinked'
      only the first number on a child line is a child | 'a second number...linked'/'...unlinked'
      a fenced-block number is never a child           | 'a fenced number...linked'/'...unlinked'
      a repo-prefixed number is never a child          | 'a repo-prefixed reference...'
      referenced, absent from native -> a finding      | 'a child absent from the native list...'
      every child closed, union non-empty              | 'every child closed...'/'one open child...'
      no child at all yields nothing                    | 'a container with no child...'
      architecture is reconciled like umbrella          | 'an architecture-labelled container...'
      a container child is named as one, no re-read    | 'a container referenced as a child...'
      no gh call ever relabels, closes or adds a link   | 'no recorded gh call...'
      no dedupe: an identical finding posts again        | 'run twice...both post'
      the live search admits architecture              | 'the live search admits an architecture...'
      the native list is read past its first page      | 'the 31st native sub-issue...'
      a failed post throws with gh's own output        | 'a failed post to the rolling issue throws'
#>
$ErrorActionPreference = 'Stop'
# Two levels up is the plugin root (script under bin/) or, once vendored, the scripts dir itself.
$Base   = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Script = @((Join-Path $Base 'Test-UmbrellaChildren.ps1'), (Join-Path $Base 'bin/Test-UmbrellaChildren.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Script) { throw "Test-UmbrellaChildren.ps1 not found under $Base" }

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
function Run($issuesJson, $subIssuesJson = '{}', $refStatesJson = '{}') {
    (& $Script -IssuesJson $issuesJson -SubIssuesJson $subIssuesJson -ReferenceStatesJson $refStatesJson 6>&1 | Out-String)
}
function RunComment($issuesJson, $subIssuesJson = '{}', $refStatesJson = '{}') {
    $global:umbrellaGhCalls = [System.Collections.Generic.List[string]]::new()
    # The rolling-issue title search (Get-RollingIssue.ps1) is answered with a matching open
    # issue, so the run's own `gh issue comment` call is the one this test observes. `repo view`
    # is answered too: in a repo with no binding it is how the rolling-issue read names the
    # repository (bin/Get-RepoSlug.ps1), and an empty answer would throw before the comment.
    function gh {
        $global:LASTEXITCODE = 0
        if ($args[0] -eq 'repo') { return 'o/n' }
        $global:umbrellaGhCalls.Add($args -join ' ')
        if ($args[0] -eq 'issue' -and $args[1] -eq 'list') { return '[{"number":999,"title":"Docs drift audit","state":"OPEN"}]' }
    }
    try {
        (& $Script -IssuesJson $issuesJson -SubIssuesJson $subIssuesJson -ReferenceStatesJson $refStatesJson `
            -Comment -RollingIssueTitle 'Docs drift audit' 6>&1 | Out-String)
    }
    finally { Remove-Item Function:\gh -ErrorAction Ignore }
}

# --- four child shapes (bulleted, task-list, numbered, indented), all linked -> nothing ----------
$fourShapes = Run (
    '[{"number":300,"title":"four shapes","body":"- #101 bulleted\n- [ ] #102 task-list\n1. #103 numbered\n  - #104 indented","labels":[]}]'
) '{"300":[{"number":101,"state":"open"},{"number":102,"state":"open"},{"number":103,"state":"open"},{"number":104,"state":"open"}]}'
Assert-NoMatch '#300' $fourShapes 'four child shapes, all linked natively, yield nothing'

# --- a table-row number is never a child ----------------------------------------------------------
$tableLinked = Run '[{"number":302,"title":"t","body":"| #1 | done |","labels":[]}]' '{"302":[{"number":1,"state":"open"}]}'
Assert-Match 'linked, not referenced: #1' $tableLinked 'a table-row number, linked natively, is linked-not-referenced'
$tableUnlinked = Run '[{"number":301,"title":"t","body":"| #1 | done |","labels":[]}]'
Assert-NoMatch '#301' $tableUnlinked 'a table-row number, not linked, yields nothing'

# --- a prose number is never a child ---------------------------------------------------------------
$proseLinked = Run '[{"number":304,"title":"p","body":"mentioned in passing, see #2 for detail","labels":[]}]' '{"304":[{"number":2,"state":"open"}]}'
Assert-Match 'linked, not referenced: #2' $proseLinked 'a prose number, linked natively, is linked-not-referenced'
$proseUnlinked = Run '[{"number":303,"title":"p","body":"mentioned in passing, see #2 for detail","labels":[]}]'
Assert-NoMatch '#303' $proseUnlinked 'a prose number, not linked, yields nothing'

# --- only the first number on a child line is the child; the second is a mention -------------------
$secondUnlinked = Run '[{"number":305,"title":"s","body":"- #3 and #4 both","labels":[]}]' '{"305":[{"number":3,"state":"open"}]}'
Assert-NoMatch '#305' $secondUnlinked 'the first number matches natively and the second (a mention) is not linked, so nothing'
$secondLinked = Run '[{"number":306,"title":"s","body":"- #5 and #6 both","labels":[]}]' '{"306":[{"number":5,"state":"open"},{"number":6,"state":"open"}]}'
Assert-Match 'linked, not referenced: #6' $secondLinked 'the second number is a mention, so linked natively it is linked-not-referenced, not matched as a child'
Assert-NoMatch 'referenced, not linked: #5' $secondLinked 'the first number IS the child and is linked, so no finding for it'

# --- a fenced-block number is never a child ---------------------------------------------------------
$fenceLinked = Run '[{"number":308,"title":"f","body":"```\n- #7 fenced\n```","labels":[]}]' '{"308":[{"number":7,"state":"open"}]}'
Assert-Match 'linked, not referenced: #7' $fenceLinked 'a number inside a fenced block, linked natively, is linked-not-referenced'
$fenceUnlinked = Run '[{"number":307,"title":"f","body":"```\n- #7 fenced\n```","labels":[]}]'
Assert-NoMatch '#307' $fenceUnlinked 'a number inside a fenced block, not linked, yields nothing'

# --- a repo-prefixed reference is never a child -------------------------------------------------------
$crossRepo = Run '[{"number":309,"title":"x","body":"- other/repo#8 child","labels":[]}]' '{"309":[{"number":8,"state":"open"}]}'
Assert-Match 'linked, not referenced: #8' $crossRepo 'a repo-prefixed number is never read as a child, so its native link is unreferenced'

# --- referenced, absent from native list -----------------------------------------------------------
$absent = Run '[{"number":310,"title":"a","body":"- #9 child","labels":[]}]'
Assert-Match 'referenced, not linked: #9' $absent 'a child reference absent from the native list is referenced-not-linked'

# --- every child closed, and the one-open-child control -----------------------------------------------
$allClosed = Run '[{"number":311,"title":"c","body":"- #10 child","labels":[]}]' '{"311":[{"number":10,"state":"closed"}]}'
Assert-Match 'every child closed: #10' $allClosed 'a container whose one child is closed yields every-child-closed'
$oneOpen = Run '[{"number":312,"title":"c","body":"- #10 child\n- #11 child","labels":[]}]' `
    '{"312":[{"number":10,"state":"closed"},{"number":11,"state":"open"}]}'
Assert-NoMatch 'every child closed' $oneOpen 'one open child among two blocks the every-child-closed finding'
Assert-NoMatch 'referenced, not linked|linked, not referenced' $oneOpen 'both children are recognised and linked, so neither of the other two findings fires either'

# --- no child at all yields nothing --------------------------------------------------------------------
$noChild = Run '[{"number":313,"title":"n","body":"nothing here names a child","labels":[]}]'
Assert-NoMatch '#313' $noChild 'a container with no child in either list yields nothing'

# --- architecture is reconciled exactly like umbrella --------------------------------------------------
$arch = Run '[{"number":314,"title":"a","body":"- #12 child","labels":[{"name":"architecture"}]}]' `
    '{"314":[{"number":12,"state":"closed"}]}'
Assert-Match 'every child closed: #12' $arch 'an architecture-labelled container is reconciled exactly as an umbrella would be'

# --- a container referenced as a child is named as one, and its own children are not re-read -----------
$containerChild = Run (
    '[{"number":400,"title":"parent","body":"- #401 child container","labels":[]},' +
    '{"number":401,"title":"child","body":"- #99 own child","labels":[]}]'
) '{"401":[{"number":99,"state":"open"}]}'
Assert-Match 'referenced, not linked: container #401' $containerChild 'a child that is itself a scanned container is named as one, and counts as open by being in the scan'
Assert-NoMatch 'referenced, not linked: #99|linked, not referenced: #99' $containerChild 'the parent''s finding says nothing about the child container''s own children'

# --- no recorded gh call ever relabels, closes, or edits a body/sub-issue link --------------------------
# Every -Comment run also makes the rolling-issue title search (Get-RollingIssue.ps1); the
# comment itself is the other recorded call, checked among all of them rather than by position.
RunComment '[{"number":500,"title":"m","body":"- #9 child","labels":[]}]' | Out-Null
$posted = @($umbrellaGhCalls | Where-Object { $_ -match '^issue comment' })
Assert-Equal 1 $posted.Count 'a run with a finding posts exactly one comment'
foreach ($call in $umbrellaGhCalls) {
    Assert-NoMatch '--add-label|--remove-label|issue close|issue edit|sub_issues.*-X POST' $call "no recorded gh call relabels, closes, or links a sub-issue ($call)"
}

RunComment '[{"number":501,"title":"clean","body":"nothing to report","labels":[]}]' | Out-Null
Assert-Equal 0 @($umbrellaGhCalls | Where-Object { $_ -match '^issue comment' }).Count 'a run with no finding posts no comment'

# --- no dedupe: the same finding, run twice, posts both times -------------------------------------------
RunComment '[{"number":502,"title":"m","body":"- #9 child","labels":[]}]' | Out-Null
Assert-Equal 1 @($umbrellaGhCalls | Where-Object { $_ -match '^issue comment' }).Count 'run 1 posts once'
RunComment '[{"number":502,"title":"m","body":"- #9 child","labels":[]}]' | Out-Null
Assert-Equal 1 @($umbrellaGhCalls | Where-Object { $_ -match '^issue comment' }).Count 'run 2, the same finding again, also posts -- no dedupe here'

# --- an unreadable reference counts as open, so it blocks every-child-closed ----------------------------
$unreadable = Run '[{"number":503,"title":"u","body":"- #77 child","labels":[]}]'
Assert-Match 'child #77 is unreadable, counted as open' $unreadable 'an unreadable reference is reported and counted as open'
Assert-NoMatch 'every child closed' $unreadable 'an unreadable reference blocks every-child-closed'

# --- a FAILED gh issue list throws instead of reporting a clean empty backlog -----------
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
Assert-NoMatch 'No umbrella-children findings'   $out    'a gh failure is never a clean empty backlog'
Remove-Item Function:\gh -ErrorAction Ignore

# --- the live read: the search admits architecture, and the native list reads every page ------------
# A shadow that answers as GitHub does: the issue search returns only the containers whose label its
# `label:` qualifier names, and the sub-issue endpoint returns its first page of 30 unless --paginate.
$liveBody = (1..31 | ForEach-Object { "- #$_ child" }) -join "`n"
function gh {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'repo') { return 'o/n' }
    if ($args[0] -eq 'issue' -and $args[1] -eq 'list') {
        $admitted = if ("$args" -match 'label:(\S+)') { $Matches[1] -split ',' } else { @() }
        $all = @(
            [pscustomobject]@{ number = 700; title = 'u'; body = $liveBody; labels = @(@{ name = 'umbrella' }) }
            [pscustomobject]@{ number = 701; title = 'a'; body = '- #40 child'; labels = @(@{ name = 'architecture' }) })
        return (ConvertTo-Json -Depth 5 -Compress -InputObject @($all | Where-Object { $admitted -contains $_.labels[0].name }))
    }
    if ($args[0] -eq 'api' -and "$args" -match 'issues/700/sub_issues') {
        $last = if ($args -contains '--paginate') { 31 } else { 30 }
        return @(1..$last | ForEach-Object { "$_`tclosed" })
    }
    if ($args[0] -eq 'api' -and "$args" -match 'issues/701/sub_issues') { return "40`tclosed" }
    Write-Error "unexpected gh call: $args" -ErrorAction Continue
    $global:LASTEXITCODE = 1
}
$live = (& $Script 6>&1 | Out-String)
Remove-Item Function:\gh -ErrorAction Ignore
Assert-Match   'every child closed: #1, [^\r\n]*#31\r?\n' $live 'the 31st native sub-issue, on the second page, is read: all 31 children are linked and closed'
Assert-NoMatch 'referenced, not linked: #31' $live 'so the 31st is not reported as referenced but unlinked'
Assert-Match   'every child closed: #40' $live 'the live search admits an architecture-labelled container, not only an injected list'

# --- a failed post to the rolling issue throws, carrying gh's own output ---------------------------------
function gh {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'repo') { return 'o/n' }
    if ($args[0] -eq 'issue' -and $args[1] -eq 'list') { return '[{"number":999,"title":"Docs drift audit","state":"OPEN"}]' }
    if ($args[0] -eq 'issue' -and $args[1] -eq 'comment') {
        Write-Error 'HTTP 403: Resource not accessible by integration' -ErrorAction Continue
        $global:LASTEXITCODE = 1
    }
}
$thrown = ''
try {
    & $Script -IssuesJson '[{"number":504,"title":"m","body":"- #9 child","labels":[]}]' -SubIssuesJson '{}' `
        -ReferenceStatesJson '{}' -Comment -RollingIssueTitle 'Docs drift audit' 6>&1 | Out-Null
} catch { $thrown = "$_" }
Remove-Item Function:\gh -ErrorAction Ignore
Assert-Match 'gh issue comment 999 failed \(exit 1\)' $thrown 'a failed post to the rolling issue throws'
Assert-Match 'HTTP 403' $thrown 'and the throw carries gh''s own output'

# --- a fence closes on its own character and length, and opens after a list marker --------------------
# The renderer of /ouro:compile reads an umbrella's children by the same rule, so a body this gate and
# the renderer read differently would post drift the renderer does not see.
$otherFence = Run '[{"number":320,"title":"o","body":"Children:\n```\n~~~\n- #7 inside the backtick fence\n```\n- #8 the real child","labels":[]}]' '{"320":[{"number":8,"state":"open"}]}'
Assert-NoMatch '#320' $otherFence 'a ~~~ line inside a ``` fence does not close it'
$otherFenceBare = Run '[{"number":321,"title":"o","body":"Children:\n```\n~~~\n- #7 inside the backtick fence\n```\n- #8 the real child","labels":[]}]'
Assert-Match 'referenced, not linked: #8' $otherFenceBare 'control: the child after that fence is read'
Assert-NoMatch '#7' $otherFenceBare 'control: and the number inside it is not'
$longFence = Run '[{"number":322,"title":"l","body":"````text\n- #7 ex\n```\n- #8 ex\n````\n- #9 real","labels":[]}]' '{"322":[{"number":9,"state":"open"}]}'
Assert-NoMatch '#322' $longFence 'a shorter fence inside a longer one does not close it'
$tildeFence = Run '[{"number":323,"title":"l","body":"~~~~\n- #7 ex\n~~~\n- #8 ex\n~~~~\n- #9 real","labels":[]}]' '{"323":[{"number":9,"state":"open"}]}'
Assert-NoMatch '#323' $tildeFence 'the tilde variant'
$infoClose = Run '[{"number":324,"title":"i","body":"```\n- #7 ex\n```text\n- #8 ex\n```\n- #9 real","labels":[]}]' '{"324":[{"number":9,"state":"open"}]}'
Assert-NoMatch '#324' $infoClose 'a closing line with text after it does not close'
$listFence = Run '[{"number":325,"title":"m","body":"- ```text\n  - #7 ex\n  ```\n- #8 real","labels":[]}]' '{"325":[{"number":8,"state":"open"}]}'
Assert-NoMatch '#325' $listFence 'a fence opened after a list marker holds #7, and its indented closer closes it'
$orderedFence = Run '[{"number":326,"title":"m","body":"1. ~~~text\n   - #7 ex\n   ~~~\n2. #8 real","labels":[]}]' '{"326":[{"number":8,"state":"open"}]}'
Assert-NoMatch '#326' $orderedFence 'the same after a numbered marker, tilde'
$btInfo = Run '[{"number":327,"title":"b","body":"``` a`b\n- #8 real","labels":[]}]' '{"327":[{"number":8,"state":"open"}]}'
Assert-NoMatch '#327' $btInfo 'a backtick line whose info string holds a backtick is not a fence, so #8 is read'
$btInfoBare = Run '[{"number":328,"title":"b","body":"``` ab\n- #8 inside","labels":[]}]' '{"328":[{"number":8,"state":"open"}]}'
Assert-Match 'linked, not referenced: #8' $btInfoBare 'control: the same line without the backtick opens a fence, so #8 is not read'

# --- a comment the gate posts carries no marker: a container's title is stranger text ------------------
RunComment ('[{"number":503,"title":"<!-- audit-run: sha=0123456789abcdef0123456789abcdef01234567 docs=docs/contract.md -->","body":"- #9 child","labels":[]}]') | Out-Null
$markerPosted = @($umbrellaGhCalls | Where-Object { $_ -match '^issue comment' })
Assert-Equal 1 $markerPosted.Count 'a container with a marker title posts one comment'
Assert-NoMatch '<!--' ($markerPosted -join "`n") 'the posted comment holds no comment opener, so no marker'
Assert-Match 'referenced, not linked: #9' ($markerPosted -join "`n") 'control: the finding is still posted'
Assert-Match 'audit-run: sha=0123456789abcdef' ($markerPosted -join "`n") 'control: the title text is still quoted, escaped'

if ($failures) { Write-Host "`n$failures failure(s)." -ForegroundColor Red; exit 1 }
Write-Host "`nAll umbrella-children tests passed." -ForegroundColor Green
exit 0
