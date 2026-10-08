<#
.SYNOPSIS
    Unit test for Test-AgentReadyShape.ps1's five structural checks.
.DESCRIPTION
    Drives the script with inline issue JSON and injected label sets -- no live gh, no live
    backlog, and no dependence on the ambient binding's [labels] (a consumer running this
    suite may declare any sets). Cases are the five finding kinds, the shapes most likely
    to be mistaken for them (an intake marker that is not the triage marker, an L size,
    a cross-domain issue's two area labels), the empty-set skip, and the single-issue exit code. The
    binding-read path (ouro-binding.py) is exercised only for its fail-loud guard shape
    by the gates that read real bindings; here every set is injected. A gh function shadow
    records -Demote's label calls and comment, and the comment's identity: the ouro version
    from the plugin.json beside the script, or the running repo's short HEAD.
#>
$ErrorActionPreference = 'Stop'
# Two levels up is the plugin root (script under bin/) or, once vendored, the scripts dir itself.
$Base   = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Script = @((Join-Path $Base 'Test-AgentReadyShape.ps1'), (Join-Path $Base 'bin/Test-AgentReadyShape.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Script) { throw "Test-AgentReadyShape.ps1 not found under $Base" }

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
function Run { param($json, [hashtable]$Extra = @{})
    (& $Script -IssuesJson $json -AreaLabels @('app', 'sdk') -TypeLabels @('bug', 'feature') @Extra 6>&1 | Out-String)
}

# A body that satisfies every check: one backticked repo path, a Size line, a doc-impact
# line. The JSON lives here as single-quoted here-strings; \n inside is JSON's own escape.
$cleanBody = 'See `bin/Test-AgentReadyShape.ps1` for the gate.\n\nSize: M\n\nDoc impact on close: none'
$triage    = '{"body":"**Triage**\n\nPROMOTE - verified."}'

# --- clean: every check satisfied -----------------------------------------------------------
$clean = Run ('[{"number":1,"title":"a","body":"' + $cleanBody + '","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"bug"}],"comments":[' + $triage + ']}]')
Assert-Match   '#1: OK'                  $clean 'a fully-shaped issue passes'
Assert-Match   'total shape findings: 0' $clean 'clean issue counts no findings'

# --- every check missing at once ------------------------------------------------------------
$bare = Run '[{"number":2,"title":"bare","body":"just prose, promoted by hand","labels":[{"name":"agent-ready"}],"comments":[]}]'
Assert-Match 'anchor: no parseable anchor'          $bare 'missing anchor is a finding'
Assert-Match 'size: no `Size:` line'                $bare 'missing Size line is a finding'
Assert-Match 'doc-impact: no `Doc impact on close:' $bare 'missing doc-impact line is a finding'
Assert-Match 'provenance: no comment starting'      $bare 'missing **Triage** comment is a finding'
Assert-Match 'labels: 0 area label\(s\)'            $bare 'no area label is a finding'
Assert-Match 'labels: 0 type label\(s\)'            $bare 'no type label is a finding'

# --- L never rides on agent-ready; the issue's two area labels are not the finding ----------
$wrong = Run ('[{"number":3,"title":"c","body":"`bin/Test-AgentReadyShape.ps1`\n\nSize: L\n\nDoc impact on close: none","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"sdk"},{"name":"bug"}],"comments":[' + $triage + ']}]')
Assert-Match   'size: `Size: L` - only S or M' $wrong 'Size L is a finding'
Assert-Match   'size: `Size: L` - only S or M ride on agent-ready \(L is a SPLIT verdict\): split it through /ouro:triage into single-deliverable S or M issues with this one as their umbrella or closed, or return it for a ruling if triage finds no split lines' $wrong 'the L finding names the split'
Assert-NoMatch 'area label'                    $wrong 'two area labels are not a finding'
Assert-NoMatch 'type label'                    $wrong 'exactly one type label is not a finding'

# --- a cross-domain issue carries several area labels; a type stays exactly one -------------
$cross = Run ('[{"number":22,"title":"u","body":"' + $cleanBody + '","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"sdk"},{"name":"bug"}],"comments":[' + $triage + ']}]')
Assert-Match   '#22: OK' $cross 'two area labels pass the gate'
$twoTypes = Run ('[{"number":23,"title":"v","body":"' + $cleanBody + '","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"bug"},{"name":"feature"}],"comments":[' + $triage + ']}]')
Assert-Match   'labels: 2 type label\(s\) - the binding requires exactly one of: bug, feature' $twoTypes 'two type labels are a finding'
Assert-NoMatch 'area label' $twoTypes 'one area label is not a finding'

# --- a malformed size is a finding that names no split: only an exact L is a SPLIT ----------
$dotted = Run ('[{"number":18,"title":"q","body":"`bin/Test-AgentReadyShape.ps1`\n\nSize: M.\n\nDoc impact on close: none","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"bug"}],"comments":[' + $triage + ']}]')
Assert-Match   'size: `Size: M\.` - only S or M ride on agent-ready \(L is a SPLIT verdict\)\r?\n' $dotted 'a trailing period is a size finding, worded as before'
Assert-NoMatch 'S or M issues' $dotted 'a malformed size names no split'
$nearL = Run ('[{"number":19,"title":"r","body":"`bin/Test-AgentReadyShape.ps1`\n\nSize: L.\n\nDoc impact on close: none","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"bug"}],"comments":[' + $triage + ']},{"number":20,"title":"s","body":"`bin/Test-AgentReadyShape.ps1`\n\nSize: l\n\nDoc impact on close: none","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"bug"}],"comments":[' + $triage + ']}]')
Assert-Match   'size: `Size: L\.` - only S or M' $nearL 'L with a trailing period is a size finding'
Assert-Match   'size: `Size: l` - only S or M'  $nearL 'a lowercase l is a size finding'
Assert-NoMatch 'S or M issues' $nearL 'only an exact L names the split, not L. or l'

# --- a body states one size: different values on several Size lines are one finding ---------
function Run-Sizes($sizes) {
    $b = '`bin/Test-AgentReadyShape.ps1`\n\n' + (($sizes | ForEach-Object { "Size: $_" }) -join '\n\nprose\n\n') + '\n\nDoc impact on close: none'
    Run ('[{"number":21,"title":"t","body":"' + $b + '","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"bug"}],"comments":[' + $triage + ']}]')
}
$ml = Run-Sizes 'M', 'L'
Assert-Match   'size: 2 `Size:` lines in the body \(`Size: M`, `Size: L`\) - a body states exactly one size\r?\n' $ml 'M then L is one finding naming both in body order'
Assert-NoMatch '(?s)SHAPE: size:.*SHAPE: size:' $ml 'M then L is a single size finding'
$lm = Run-Sizes 'L', 'M'
Assert-Match   'size: 2 `Size:` lines in the body \(`Size: L`, `Size: M`\) - a body states exactly one size\r?\n' $lm 'L then M is one finding naming both in body order'
Assert-NoMatch '(?s)SHAPE: size:.*SHAPE: size:' $lm 'L then M is a single size finding'
Assert-NoMatch 'S or M issues' $lm 'conflicting sizes name no split, even with L first'
$mm = Run-Sizes 'M', 'M'
Assert-Match   '#21: OK' $mm 'M twice is checked as one M line'
$ll = Run-Sizes 'L', 'L'
Assert-Match   'size: `Size: L` - only S or M ride on agent-ready \(L is a SPLIT verdict\): split it through /ouro:triage' $ll 'L twice gets the exact-L finding'
Assert-NoMatch '(?s)SHAPE: size:.*SHAPE: size:' $ll 'L twice is a single size finding'
$mml = Run-Sizes 'M', 'M', 'L'
Assert-Match   'size: 3 `Size:` lines in the body \(`Size: M`, `Size: M`, `Size: L`\) - a body states exactly one size' $mml 'a repeated value still counts and lists every line'
$lcase = Run-Sizes 'L', 'l'
Assert-Match   'size: 2 `Size:` lines in the body \(`Size: L`, `Size: l`\) - a body states exactly one size' $lcase 'L and l are different values: values compare case-sensitively'

# --- a Size line's value ends at its line break; an empty one is no Size line ---------------
# \r, \t and \u00a0 are JSON's own escapes: the body really carries CR, tab and no-break space.
function Run-Body($rest) {
    Run ('[{"number":24,"title":"w","body":"`bin/Test-AgentReadyShape.ps1`' + $rest + '","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"bug"}],"comments":[' + $triage + ']}]')
}
$emptyLf = Run-Body '\n\nSize:\nM\n\nDoc impact on close: none'
Assert-Match   'size: no `Size:` line in the body\r?\n' $emptyLf 'an empty Size line takes no value from a next line M (LF)'
$emptyCrlf = Run-Body '\r\n\r\nSize:\r\nM\r\n\r\nDoc impact on close: none'
Assert-Match   'size: no `Size:` line in the body\r?\n' $emptyCrlf 'an empty Size line takes no value from a next line M (CRLF)'
$emptyCr = Run-Body '\n\nSize:\rM\n\nDoc impact on close: none'
Assert-Match   'size: no `Size:` line in the body\r?\n' $emptyCr 'a lone CR ends a Size line as a line feed does'
$emptyDoc = Run-Body '\n\nSize: \nDoc impact on close: none'
Assert-Match   'size: no `Size:` line in the body\r?\n' $emptyDoc 'an empty Size line above the doc-impact line is the no-Size-line finding'
Assert-NoMatch 'Size: Doc' $emptyDoc 'an empty Size line names no value taken from the next line'
$emptyProse = Run-Body '\n\nSize:\nprose\n\nSize: M\n\nDoc impact on close: none'
Assert-Match   '#24: OK' $emptyProse 'an empty Size line, a prose line, then Size: M is one M line'
$emptyAbove = Run-Body '\n\nSize:\nSize: M\n\nDoc impact on close: none'
Assert-Match   '#24: OK' $emptyAbove 'an empty Size line directly above Size: M does not swallow it'
$tab = Run-Body '\n\nSize:\tM\n\nDoc impact on close: none'
Assert-Match   '#24: OK' $tab 'a tab between the colon and the value is checked as before'
$nbsp = Run-Body '\n\nSize:\u00a0M\n\nDoc impact on close: none'
Assert-Match   '#24: OK' $nbsp 'a no-break space between the colon and the value is checked as before'

# --- the intake marker is not the triage marker ---------------------------------------------
$intake = Run ('[{"number":4,"title":"d","body":"' + $cleanBody + '","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"bug"}],"comments":[{"body":"**Intake triage** (automated)\n\ngraded once, unattended."}]}]')
Assert-Match 'provenance: no comment starting' $intake 'the unattended intake marker does not satisfy provenance'

# --- **Triage** must open the comment, not appear mid-body ----------------------------------
$midway = Run ('[{"number":5,"title":"e","body":"' + $cleanBody + '","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"bug"}],"comments":[{"body":"as discussed:\n**Triage**\nlooks fine"}]}]')
Assert-Match 'provenance: no comment starting' $midway 'a mid-comment **Triage** line does not satisfy provenance'

# --- empty or undeclared label sets skip their check ----------------------------------------
$skip = (& $Script -IssuesJson ('[{"number":6,"title":"f","body":"' + $cleanBody + '","labels":[{"name":"agent-ready"}],"comments":[' + $triage + ']}]') -AreaLabels @() -TypeLabels @() 6>&1 | Out-String)
Assert-NoMatch 'labels:'  $skip 'empty label sets skip the label check'
Assert-Match   '#6: OK'   $skip 'the issue passes on the remaining checks'

# --- a modifier is not an area/type label ---------------------------------------------------
$mod = Run ('[{"number":7,"title":"g","body":"' + $cleanBody + '","labels":[{"name":"agent-ready"},{"name":"checkpoint"},{"name":"app"},{"name":"bug"}],"comments":[' + $triage + ']}]')
Assert-Match '#7: OK' $mod 'trivial/checkpoint riding along breaks nothing'

# --- an issue not carrying agent-ready is not enforced --------------------------------------
$notready = Run '[{"number":8,"title":"h","body":"prose","labels":[{"name":"needs-ruling"}],"comments":[]}]'
Assert-Match 'INFO - not agent-ready' $notready 'a non-agent-ready issue is skipped, not failed'

# --- single-issue mode: findings exit 1, clean exits 0 --------------------------------------
$null = Run '[{"number":9,"title":"i","body":"prose","labels":[{"name":"agent-ready"}],"comments":[]}]' @{ Issue = 9 }
if ($LASTEXITCODE -eq 1) { Write-Host '  ok: -Issue mode exits 1 on findings' -ForegroundColor DarkGray }
else { Write-Host "FAIL: -Issue mode exit was $LASTEXITCODE, expected 1" -ForegroundColor Red; $failures++ }
$null = Run ('[{"number":10,"title":"j","body":"' + $cleanBody + '","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"bug"}],"comments":[' + $triage + ']}]') @{ Issue = 10 }
if ($LASTEXITCODE -eq 0) { Write-Host '  ok: -Issue mode exits 0 clean' -ForegroundColor DarkGray }
else { Write-Host "FAIL: -Issue mode exit was $LASTEXITCODE, expected 0" -ForegroundColor Red; $failures++ }

# --- the issue form renders doc impact as a heading; both forms satisfy the check -----------
$heading = Run ('[{"number":12,"title":"k","body":"See `bin/Test-AgentReadyShape.ps1`.\n\nSize: S\n\n### Doc impact on close\n\nnone","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"bug"}],"comments":[' + $triage + ']}]')
Assert-Match '#12: OK' $heading 'the issue-form heading satisfies the doc-impact check'

# --- the checks are case-sensitive: the gate exists to catch hand-promotions ----------------
$lower = Run ('[{"number":13,"title":"l","body":"See `bin/Test-AgentReadyShape.ps1`.\n\nsize: m\n\nDoc impact on close: none","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"bug"}],"comments":[{"body":"**triage**\n\nlooks good"}]}]')
Assert-Match 'size: no `Size:` line'           $lower 'a lowercase size line is not a Size line'
Assert-Match 'provenance: no comment starting' $lower 'a lowercase **triage** is not the marker'

# --- a URL/env-var/placeholder/absolute token is not an anchor (nothing verifies it at HEAD)
$placeholder = Run ('[{"number":14,"title":"m","body":"See `<ouro>/bin/Test-AgentReadyShape.ps1`.\n\nSize: M\n\nDoc impact on close: none","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"bug"}],"comments":[' + $triage + ']}]')
Assert-Match 'anchor: no parseable anchor' $placeholder 'a placeholder-only body has no anchor'
$absolute = Run ('[{"number":15,"title":"n","body":"See `C:/opt/gate/gate.ps1`.\n\nSize: M\n\nDoc impact on close: none","labels":[{"name":"agent-ready"},{"name":"app"},{"name":"bug"}],"comments":[' + $triage + ']}]')
Assert-Match 'anchor: no parseable anchor' $absolute 'an absolute-path-only body has no anchor'

# --- a vendored copy without ouro-binding.py skips the label checks loudly ------------------
# The fixture is a vendored directory without the binding tool: the no-tool fallback is under test.
$vdir = Join-Path ([IO.Path]::GetTempPath()) ('ouro-shape-vendored-' + [guid]::NewGuid().ToString('n'))
New-Item -ItemType Directory -Path $vdir | Out-Null
try {
    Copy-Item (Join-Path (Split-Path $Script -Parent) 'Get-AnchorFindings.ps1') $vdir
    Copy-Item (Join-Path (Split-Path $Script -Parent) 'Get-RepoSlug.ps1') $vdir
    Copy-Item $Script $vdir
    $vout = (& (Join-Path $vdir 'Test-AgentReadyShape.ps1') -IssuesJson '[{"number":14,"title":"m","body":"prose","labels":[{"name":"agent-ready"}],"comments":[]}]' 6>&1 | Out-String)
    Assert-Match   'vendored without the binding tool' $vout 'the missing tool is announced, not fatal'
    Assert-NoMatch 'labels:'                           $vout 'label checks skip without the tool'
} finally { Remove-Item -LiteralPath $vdir -Recurse -Force }

# --- a repo with no binding skips the label checks loudly, without calling python -----------
# The tool is beside the script and the repo has no .claude/ouro.toml: pwsh and git are enough.
if (Test-Path -LiteralPath (Join-Path (Split-Path $Script -Parent) 'ouro-binding.py')) {
    $nbrepo = Join-Path ([IO.Path]::GetTempPath()) ('ouro-shape-nobinding-' + [guid]::NewGuid().ToString('n'))
    New-Item -ItemType Directory -Path $nbrepo | Out-Null
    Push-Location -LiteralPath $nbrepo
    try {
        git init -q . 2>$null
        $nbout = try { (& $Script -IssuesJson '[{"number":16,"title":"o","body":"prose","labels":[{"name":"agent-ready"}],"comments":[]}]' 6>&1 | Out-String) } catch { "threw: $_" }
        Assert-NoMatch 'threw:'                                   $nbout 'a repo without a binding does not throw'
        Assert-Match   'INFO - labels\.area not read \(no .*ouro\.toml\)' $nbout 'the missing binding is announced'
        Assert-NoMatch 'labels:'                                  $nbout 'label checks skip without a binding'
    } finally { Pop-Location; Remove-Item -LiteralPath $nbrepo -Recurse -Force }

    # ...and a repo that has a binding still reads it: the skip is for a missing file only.
    $brepo = Join-Path ([IO.Path]::GetTempPath()) ('ouro-shape-bound-' + [guid]::NewGuid().ToString('n'))
    New-Item -ItemType Directory -Path (Join-Path $brepo '.claude') | Out-Null
    Push-Location -LiteralPath $brepo
    try {
        git init -q . 2>$null
        @'
schema = 1
[repo]
slug = "o/n"
default_branch = "master"
[labels]
area = ["app"]
[[gate]]
areas = ["*"]
run = "x"
[ship]
policy = "stop-at-pr"
review = "adversarial-review"
[owner]
ruling_approvers = ["me"]
'@ | Set-Content -LiteralPath (Join-Path $brepo '.claude/ouro.toml') -Encoding utf8
        $bout = try { (& $Script -IssuesJson '[{"number":17,"title":"p","body":"prose","labels":[{"name":"agent-ready"}],"comments":[]}]' 6>&1 | Out-String) } catch { "threw: $_" }
        Assert-Match   'labels: 0 area label\(s\) - the binding requires at least one of: app' $bout 'a repo with a binding still reads its label sets'
        Assert-NoMatch 'not read'                                                            $bout 'the no-binding skip does not fire in a bound repo'
    } finally { Pop-Location; Remove-Item -LiteralPath $brepo -Recurse -Force }
}
else { Write-Host '  skip: ouro-binding.py not beside the script (vendored without the tool)' -ForegroundColor DarkGray }

# --- a full page of the fixed -limit 100 warns, since an issue past it goes unchecked ----------
$fullDir = Join-Path ([IO.Path]::GetTempPath()) ('ouro-shape-full-' + [guid]::NewGuid().ToString('n'))
New-Item -ItemType Directory -Path $fullDir | Out-Null
Push-Location -LiteralPath $fullDir
try {
    git init -q . 2>$null
    git remote add origin https://github.com/o/n.git 2>$null
    function gh {
        if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
        $global:LASTEXITCODE = 0
        '[' + ((1..100 | ForEach-Object { '{"number":' + $_ + ',"title":"i","body":"x","labels":[{"name":"agent-ready"}],"comments":[]}' }) -join ',') + ']'
    }
    $full = (& $Script -AreaLabels @('app', 'sdk') -TypeLabels @('bug', 'feature') 6>&1 | Out-String)
    Assert-Match 'warning::shape gate: the agent-ready list filled its --limit 100 page' $full 'a full 100-row page warns naming the limit'
    function gh {
        if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
        $global:LASTEXITCODE = 0
        '[' + ((1..99 | ForEach-Object { '{"number":' + $_ + ',"title":"i","body":"x","labels":[{"name":"agent-ready"}],"comments":[]}' }) -join ',') + ']'
    }
    $under = (& $Script -AreaLabels @('app', 'sdk') -TypeLabels @('bug', 'feature') 6>&1 | Out-String)
    Assert-NoMatch 'warning::shape gate: the agent-ready list filled' $under 'a page under the limit warns nothing'
    Remove-Item Function:\gh
} finally { Pop-Location; Remove-Item -LiteralPath $fullDir -Recurse -Force }

# --- -Demote outside single-issue mode refuses ----------------------------------------------
$thrown = ''
try { $null = & $Script -IssuesJson '[]' -Demote 6>&1 } catch { $thrown = "$_" }
Assert-Match 'single-issue mode only' $thrown '-Demote without -Issue throws'

# --- -Demote removes, then adds, then comments ----------------------------------------------
# A gh function shadow records the calls, and exits 1 on the call matching $ghFailOn. gh runs
# one edit's removal and addition as independent writes, so the demotion is two edits: the
# removal names only labels the issue carries, the add follows it, and the comment that
# claims the demotion posts only after both.
$global:ghCalls = @()
$global:ghFailOn = ''
function gh {
    # `repo view` names the repository the writes below address (bin/Get-RepoSlug.ps1), which the
    # gate asks for in a repo with no binding; the recorded calls are the demotion's own.
    if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
    $call = $args -join ' '
    $global:ghCalls += $call
    $global:LASTEXITCODE = if ($global:ghFailOn -and $call -match $global:ghFailOn) { 1 } else { 0 }
}
function Run-Demote($Labels, $FailOn = '') {
    $global:ghCalls = @(); $global:ghFailOn = $FailOn
    $json = '[{"number":15,"title":"n","body":"prose","labels":[' + (($Labels | ForEach-Object { '{"name":"' + $_ + '"}' }) -join ',') + '],"comments":[]}]'
    try { $null = & $Script -IssuesJson $json -Issue 15 -Demote -AreaLabels @() -TypeLabels @() 6>$null; '' } catch { "$_" }
}
$thrown = Run-Demote @('agent-ready')
if ($LASTEXITCODE -eq 1) { Write-Host '  ok: -Demote run still exits 1 on findings' -ForegroundColor DarkGray }
else { Write-Host "FAIL: -Demote run exit was $LASTEXITCODE, expected 1 (threw: $thrown)" -ForegroundColor Red; $failures++ }
Assert-Match '^issue edit 15 --remove-label agent-ready$' $ghCalls[0] 'only agent-ready carried: the first call removes agent-ready and names no other label'
Assert-Match '^issue edit 15 --add-label needs-triage$'   $ghCalls[1] 'the second call adds needs-triage and names no other label'
Assert-Match '^issue comment 15 '                         $ghCalls[2] 'the comment posts after both label calls'
# The comment names the ouro release that ran: with an ouro plugin.json in the parent of the script's
# directory, its version and no @ stamp. A vendored tree has none there; the TEMP copy below pins both.
$manifest = try { Get-Content -LiteralPath (Join-Path (Split-Path (Split-Path $Script -Parent) -Parent) '.claude-plugin/plugin.json') -Raw | ConvertFrom-Json } catch { $null }
if ($manifest.name -ceq 'ouro') {
    Assert-Match   ('^issue comment 15 --body Shape gate \(ouro v' + [regex]::Escape($manifest.version.Trim()) + ', Test-AgentReadyShape\.ps1\): ') $ghCalls[2] 'in an ouro plugin tree the comment names ouro v and the plugin.json version'
    Assert-NoMatch '\.ps1 @ ' $ghCalls[2] 'in an ouro plugin tree the comment names no @ stamp'
}
else { Write-Host '  skip: no ouro plugin.json beside the script (vendored)' -ForegroundColor DarkGray }
$null = Run-Demote @('agent-ready', 'trivial')
Assert-Match '^issue edit 15 --remove-label agent-ready --remove-label trivial$' $ghCalls[0] 'agent-ready and trivial carried: the removal names both and not checkpoint'
$null = Run-Demote @('agent-ready', 'Trivial')
Assert-Match '^issue edit 15 --remove-label agent-ready --remove-label trivial$' $ghCalls[0] 'a modifier carried as Trivial is removed: label names match case-insensitively, as gh matches them'
$thrown = Run-Demote @('agent-ready') '--add-label'
Assert-Match   'needs-triage'        $thrown 'a failed add throws naming needs-triage'
Assert-Match   'repository may lack' $thrown 'a failed add says the repository may lack the label'
Assert-Match   'no state label'      $thrown 'a failed add says the issue carries no state label'
Assert-NoMatch 'issue comment'       ($ghCalls -join "`n") 'a failed add posts no comment'
$thrown = Run-Demote @('agent-ready', 'checkpoint') '--remove-label'
Assert-Match   'agent-ready, checkpoint.*nothing has changed' $thrown 'a failed removal throws naming the removal and saying nothing has changed'
Assert-NoMatch '--add-label|issue comment' ($ghCalls -join "`n") 'a failed removal runs no add and posts no comment'

# --- the comment's identity comes from the plugin tree the script sits in -------------------
# A copy of the gate in a TEMP tree, run from a separate scratch repo with one commit: plugin.json is
# read in the parent of the script's directory, never in the repo the gate runs in. Without an ouro
# one there with a version, or when it does not parse, the comment keeps the repo's short HEAD.
$idRoot = Join-Path ([IO.Path]::GetTempPath()) ('ouro-shape-identity-' + [guid]::NewGuid().ToString('n'))
$idTree = Join-Path $idRoot 'tree'
New-Item -ItemType Directory -Path (Join-Path $idTree 'bin'), (Join-Path $idRoot 'repo') | Out-Null
Push-Location -LiteralPath (Join-Path $idRoot 'repo')
try {
    Copy-Item -LiteralPath $Script, (Join-Path (Split-Path $Script -Parent) 'Get-AnchorFindings.ps1'),
        (Join-Path (Split-Path $Script -Parent) 'Get-RepoSlug.ps1') -Destination (Join-Path $idTree 'bin')
    git init -q . 2>$null
    # -Demote writes, so the gate names its repository first; with no binding here that is the
    # repository origin names (bin/Get-RepoSlug.ps1).
    git remote add origin https://github.com/o/n.git 2>$null
    git -c user.name=t -c user.email=t@t -c commit.gpgsign=false commit -q --no-verify --allow-empty -m fixture 2>$null
    $stamp = '^issue comment 15 --body Shape gate \(Test-AgentReadyShape\.ps1 @ ' + [regex]::Escape((git rev-parse --short HEAD).Trim()) + '\): '
    function Get-CopyComment($Manifest) {
        $dir = Join-Path $idTree '.claude-plugin'
        if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
        if ($null -ne $Manifest) {
            New-Item -ItemType Directory -Path $dir | Out-Null
            Set-Content -LiteralPath (Join-Path $dir 'plugin.json') -Value $Manifest -NoNewline
        }
        $global:ghCalls = @(); $global:ghFailOn = ''
        try { $null = & (Join-Path $idTree 'bin/Test-AgentReadyShape.ps1') -IssuesJson '[{"number":15,"title":"n","body":"prose","labels":[{"name":"agent-ready"}],"comments":[]}]' -Issue 15 -Demote -AreaLabels @() -TypeLabels @() 6>$null }
        catch { return "threw: $_" }
        "$(@($global:ghCalls) -match '^issue comment 15 ')"
    }
    Assert-Match $stamp (Get-CopyComment $null) 'no plugin.json beside the copy: the comment names @ and the scratch repo short HEAD'
    Assert-Match $stamp (Get-CopyComment '{"name":"other","version":"9.9.9"}') 'a plugin.json whose name is not ouro: the comment takes the same fallback'
    Assert-Match '^issue comment 15 --body Shape gate \(ouro v7\.7\.7, Test-AgentReadyShape\.ps1\): ' (Get-CopyComment '{"name":"ouro","version":"7.7.7"}') 'an ouro plugin.json beside the copy and none in the repo it runs in: the comment names its version'
    foreach ($bad in '{"name":"ouro","version":""}', '{"name":"ouro","version":" "}', '{"name":"ouro","version":7}', '{"name":"ouro",', '{"name":"Ouro","version":"7.7.7"}') {
        Assert-Match $stamp (Get-CopyComment $bad) "plugin.json ${bad}: the comment takes the fallback and nothing throws"
    }
    # A caller's strict mode reaches the script it runs: a missing plugin.json or version must not throw.
    Set-StrictMode -Version Latest
    try {
        Assert-Match $stamp (Get-CopyComment $null) 'under strict mode, no plugin.json: the comment takes the fallback and nothing throws'
        Assert-Match $stamp (Get-CopyComment '{"name":"ouro"}') 'under strict mode, a plugin.json with no version: the comment takes the fallback and nothing throws'
    }
    finally { Set-StrictMode -Off }
}
finally { Pop-Location; Remove-Item function:Get-CopyComment -ErrorAction Ignore; Remove-Item -LiteralPath $idRoot -Recurse -Force }
Remove-Item function:gh, function:Run-Demote; Remove-Variable -Name ghCalls, ghFailOn -Scope Global

if ($failures) { Write-Host "`n$failures failure(s)." -ForegroundColor Red; exit 1 }
Write-Host "`nAll agent-ready-shape tests passed." -ForegroundColor Green
exit 0
