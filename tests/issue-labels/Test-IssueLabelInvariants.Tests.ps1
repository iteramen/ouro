<#
.SYNOPSIS
    Unit test for Test-IssueLabelInvariants.ps1's finding logic.
.DESCRIPTION
    Drives the script with inline issue JSON -- no gh, no live backlog. That is
    deliberate: a test that queried the real repo would pass or fail on whatever
    anyone labelled that morning, which is how a test earns deletion. The cases
    below are the three finding kinds plus the shapes most likely to be mistaken
    for them -- a modifier riding correctly on agent-ready, and a label whose
    name merely starts like a state name.
#>
$ErrorActionPreference = 'Stop'
# Two levels up is the plugin root (script under bin/) or, once vendored, the scripts dir itself.
$Base   = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Script = @((Join-Path $Base 'Test-IssueLabelInvariants.ps1'), (Join-Path $Base 'bin/Test-IssueLabelInvariants.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Script) { throw "Test-IssueLabelInvariants.ps1 not found under $Base" }

$failures = 0
function Assert-Match($Pattern, $Output, $What) {
    if ($Output -match $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- no match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}
function Assert-NoMatch($Pattern, $Output, $What) {
    if ($Output -notmatch $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- unexpected match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}
# Write-Host writes to the information stream (6), not stdout -- 2>&1 captures nothing here.
function Run($json) { (& $Script -IssuesJson $json 6>&1 | Out-String) }

# --- clean: one state each, a modifier on the state that permits it ------------------------
$clean = Run '[
  {"number":1,"title":"a","labels":[{"name":"bug"},{"name":"agent-ready"}]},
  {"number":2,"title":"b","labels":[{"name":"blocked"}]},
  {"number":3,"title":"c","labels":[{"name":"agent-ready"},{"name":"trivial"}]},
  {"number":4,"title":"d","labels":[{"name":"agent-ready"},{"name":"checkpoint"}]}
]'
Assert-Match   'invariant holds across 4' $clean 'clean backlog reports no findings'
Assert-NoMatch 'modifier:'                $clean 'trivial/checkpoint on agent-ready is not a finding'

# --- missing --------------------------------------------------------------------------------
$missing = Run '[{"number":7,"title":"no state","labels":[{"name":"bug"},{"name":"ui"}]}]'
Assert-Match 'missing: #7' $missing 'an issue with only area/type labels is missing'

# --- conflict -------------------------------------------------------------------------------
$conflict = Run '[{"number":8,"title":"two","labels":[{"name":"blocked"},{"name":"needs-ruling"}]}]'
Assert-Match 'conflict: #8 carries 2 state labels' $conflict 'two state labels conflict'
Assert-NoMatch 'missing: #8' $conflict 'a conflicting issue is not also reported missing'

# --- modifier without agent-ready -------------------------------------------------------------
$mod = Run '[{"number":9,"title":"orphan mod","labels":[{"name":"blocked"},{"name":"trivial"}]}]'
Assert-Match 'modifier: #9' $mod 'trivial without agent-ready is a finding'

# --- a modifier on an issue with NO state is both findings, not one --------------------------
$both = Run '[{"number":10,"title":"both","labels":[{"name":"checkpoint"}]}]'
Assert-Match 'missing: #10'  $both 'no state is reported'
Assert-Match 'modifier: #10' $both 'and the orphan modifier is reported too'

# --- near-miss names must not count as states ------------------------------------------------
$near = Run '[{"number":11,"title":"near","labels":[{"name":"needs-triage-later"},{"name":"agent-ready"}]}]'
Assert-NoMatch 'conflict: #11' $near 'a label merely prefixed like a state is not a second state'

# --- architecture is a state: alone it is the issue's one state, beside another a conflict ----
$arch = Run '[{"number":14,"title":"a design being shaped","labels":[{"name":"architecture"}]}]'
Assert-Match 'invariant holds across 1' $arch 'architecture alone is a state: no finding'
$archTwo = Run '[{"number":15,"title":"shaped and shelved","labels":[{"name":"architecture"},{"name":"idea"}]}]'
Assert-Match 'conflict: #15 carries 2 state labels' $archTwo 'architecture beside another state is a conflict'

# --- a FAILED gh issue list throws instead of reporting a clean empty backlog -
# A function shadow stands in for the native gh: stderr as an error record, exit code set. Every
# shadow here answers `repo view`, the call that names the repository (bin/Get-RepoSlug.ps1): in a
# repo with no binding it is the first call the gate makes, and a shadow that failed it would skip
# the gate rather than exercise the case under test.
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
Assert-NoMatch 'invariant holds'                 $out    'a gh failure is never a clean empty backlog'

# --- stderr noise on a SUCCESSFUL listing must not break the parse --------------------------
function gh {
    if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
    Write-Error 'A new release of gh is available' -ErrorAction Continue
    '[{"number":1,"title":"a","labels":[{"name":"agent-ready"}]}]'
    $global:LASTEXITCODE = 0
}
$out = (& $Script 6>&1 | Out-String)
Assert-Match 'invariant holds across 1' $out 'stderr noise on exit 0 does not break parsing'

# --- a full page of the fixed -limit 500 warns, since an issue past it goes unchecked ---------
function gh {
    if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
    $global:LASTEXITCODE = 0
    '[' + ((1..500 | ForEach-Object { '{"number":' + $_ + ',"title":"i' + $_ + '","labels":[{"name":"agent-ready"}]}' }) -join ',') + ']'
}
$full = (& $Script 6>&1 | Out-String)
Assert-Match 'warning::state-label gate: the open-issue list filled its --limit 500 page' $full 'a full 500-row page warns naming the limit'
function gh {
    if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
    $global:LASTEXITCODE = 0
    '[' + ((1..499 | ForEach-Object { '{"number":' + $_ + ',"title":"i' + $_ + '","labels":[{"name":"agent-ready"}]}' }) -join ',') + ']'
}
$under = (& $Script 6>&1 | Out-String)
Assert-NoMatch 'warning::state-label gate: the open-issue list filled' $under 'a page under the limit warns nothing'

# --- the whole conflict and modifier lines: each label in single backticks -----------------
# The label separator sits in a single-quoted string inside $(...), where a doubled backtick is
# two literal backticks; the prefix matches above pass either way, so these pin the whole line.
# Assert-Match compares with -match, so each expected line is escaped.
$twoStates = Run '[{"number":12,"title":"two states","labels":[{"name":"umbrella"},{"name":"needs-triage"}]}]'
Assert-Match ([regex]::Escape('conflict: #12 carries 2 state labels (`umbrella`, `needs-triage`) -- two states')) $twoStates `
    'a conflict names each state label in single backticks'
$twoMods = Run '[{"number":13,"title":"two modifiers","labels":[{"name":"blocked"},{"name":"trivial"},{"name":"checkpoint"}]}]'
Assert-Match ([regex]::Escape('modifier: #13 carries `trivial`, `checkpoint` without `agent-ready` -- two modifiers')) $twoMods `
    'two orphan modifiers are each named in single backticks'

if ($failures) { Write-Host "`n$failures failure(s)." -ForegroundColor Red; exit 1 }
Write-Host "`nAll issue-label invariant tests passed." -ForegroundColor Green
exit 0
