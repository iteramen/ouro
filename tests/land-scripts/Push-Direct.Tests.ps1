<#
.SYNOPSIS
    Test of Push-Direct.ps1 over scratch repositories and a gh stand-in.
.DESCRIPTION
    Each row runs the script as a child from a main checkout whose origin is a local bare
    repository, with the gh stand-in answering the two protection endpoints from a table the row
    writes (the fixture beside this file refuses any other call). Origin and the exit code are read
    directly.

    Pinned: with no pull-request requirement the commits are pushed plainly and origin's tip is
    HEAD (rules and classic protection absent, and both refused by the plan); a path outside
    [ship].direct and an unproved publication floor route to a PR; an empty or absent key, a
    checkout off the default branch and an empty range stop. Origin remains unchanged on refusal.
    A pull-request requirement with no
    approving review prints `vehicle: pr`, pushes `direct/<issue>` and leaves the default branch
    on origin where it was, whether the requirement is a ruleset or classic protection; a required
    approval (either source, count or code-owner review) and a protection read that failed print
    `fallback: express`, exit 3, and push nothing; the help names each outcome.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'LandScripts.Fixture.ps1')
$Script = Find-Script 'Push-Direct.ps1'
# Scratch Git repositories use the fixture's empty config. Preserve then clear
# inherited metadata redirects, including command-scope config from a host runner.
# The public planner continues to refuse those inputs in actual consumer use.
foreach ($key in 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_CONFIG_COUNT') {
    $SavedEnv[$key] = [Environment]::GetEnvironmentVariable($key)
    Set-EnvVar $key $null
}

function Get-OriginTip([string]$Origin, [string]$Branch) {
    $line = @(G $Origin for-each-ref '--format=%(objectname)' "refs/heads/$Branch")
    if ($line.Count) { $line[0] } else { '' }
}
# The binding with ship.direct, committed and pushed, so the range starts after it.
function New-DirectWorld([string]$Name, [string]$Direct = '"docs/*", "README.md"') {
    $w = New-World $Name
    $file = Join-Path $w.Main '.claude/ouro.toml'
    $text = [System.IO.File]::ReadAllText($file)
    if ($Direct) {
        $text = $text.Replace("[ship]`n", "[ship]`ndirect = [$Direct]`n")
        $text += "`n[[gate]]`ncheap = true`nrun = `"python3 checks/direct-check.py`"`n"
        [System.IO.File]::WriteAllText($file, $text, $Utf8)
        New-Item -ItemType Directory -Path (Join-Path $w.Main 'checks') | Out-Null
        [System.IO.File]::WriteAllText((Join-Path $w.Main 'checks/direct-check.py'), "print('cheap gate ran')`n", $Utf8)
        $null = G $w.Main add -A
        $null = G $w.Main commit -q -m 'binding: direct'
        $null = G $w.Main push -q origin master
    }
    $w
}
function Add-File([string]$Dir, [string]$Path) {
    $full = Join-Path $Dir $Path
    New-Item -ItemType Directory -Force -Path (Split-Path $full -Parent) | Out-Null
    $null = New-Commit $Dir $Path
}
$NoRules = Answer @()
$NoClassic = Fail 'gh: Branch not protected (HTTP 404)'
function Rules($Count, [bool]$Owner = $false) {
    Answer @(@{ type = 'pull_request'; parameters = @{ required_approving_review_count = $Count; require_code_owner_review = $Owner } })
}
function Classic($Count) { Answer @{ required_pull_request_reviews = @{ required_approving_review_count = $Count } } }
function Set-Protection($Rules, $Classic) {
    Set-Gh @{ 'api repos/o/n/rules/branches/master' = $Rules; 'api repos/o/n/branches/master/protection' = $Classic }
}

function Save-DirectBase($World) {
    $null = G $World.Main add -A
    $null = G $World.Main commit -q -m 'trusted publication checks'
    $null = G $World.Main push -q origin master
}
function Set-DirectCheck($World, [string]$Body) {
    [IO.File]::WriteAllText((Join-Path $World.Main 'checks/direct-check.py'), $Body, $Utf8)
}
$markCheck = "from pathlib import Path`nPath('.git/floor-ran').write_text('ran')`n"


Write-Host 'the caller cannot authorize a different public publication head'
$w = New-DirectWorld 'requested-head-changed'; $m = $w.Main
$before = Get-OriginTip $w.Origin 'master'; Add-File $m 'docs/a.md'
Set-Protection $NoRules $NoClassic
$c = Invoke-Child $Script @('-ExpectedHead', $before) $m
Assert-Equal 1 $c.Code 'the changed caller head is refused before checks'
Assert-Line $c.Lines 'STOP: the requested direct publication head changed before checks - nothing is pushed' 'names the requested head substitution'
Assert-Equal 0 $c.Gh.Count 'changed caller head stops before protection queries'
Assert-Equal $false (Test-Path -LiteralPath (Join-Path $m '.git/floor-ran')) 'no floor executes for another head'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'the default branch stays unchanged'
$requestedHead = @(G $m rev-parse HEAD)[0]
$c = Invoke-Child $Script @('-ExpectedHead', $requestedHead, '-ExpectedBase', $requestedHead) $m
Assert-Equal 1 $c.Code 'a changed planning base is refused before mandatory checks'
Assert-Line $c.Lines 'STOP: the requested direct planning base changed before checks - nothing is pushed' 'names the range identity mismatch'
Assert-Equal 0 $c.Gh.Count 'changed planning base stops before protection queries'
Assert-Equal $false (Test-Path -LiteralPath (Join-Path $m '.git/floor-ran')) 'no floor executes for a different unpublished range'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'a mismatched base publishes nothing'

Write-Host 'an obsolete companion cannot silently ignore the checked-head parameter'
$legacy = Join-Path $Scratch 'obsolete-companion'
New-Item -ItemType Directory -Path $legacy > $null
foreach ($name in 'Push-Direct.ps1', 'Get-LandBinding.ps1', 'ouro-binding.py', 'ouro-direct.py') {
    Copy-Item -LiteralPath (Find-Script $name) -Destination (Join-Path $legacy $name)
}
$legacyExecutorSource = Join-Path (Split-Path $Script -Parent) 'ouro-process.py'
if (-not (Test-Path -LiteralPath $legacyExecutorSource)) { $legacyExecutorSource = Join-Path (Split-Path $Script -Parent) 'ouro/runtime/process.py' }
Copy-Item -LiteralPath $legacyExecutorSource -Destination (Join-Path $legacy 'ouro-process.py')
# The old companion's API had only Lease. This stand-in must never execute;
# the original real companion's unsafe publication is retained in native818 evidence.
[IO.File]::WriteAllText((Join-Path $legacy 'Push-Branch.ps1'), 'function Invoke-PushBranch { param([string]$Lease); [IO.File]::WriteAllText(".git/obsolete-companion-called", "called") }', $Utf8)
$w = New-DirectWorld 'obsolete-companion-world'; $m = $w.Main
Add-File $m 'docs/a.md'; $before = Get-OriginTip $w.Origin 'master'
Set-Protection (Rules 0) $NoClassic
$c = Invoke-Child (Join-Path $legacy 'Push-Direct.ps1') @('-Issue', '7') $m
Assert-Equal 1 $c.Code 'old companion refuses before execution or publication'
Assert-Line $c.Lines 'STOP: Push-Branch.ps1 lacks checked-head support; update the direct helper bundle before retrying' 'identifies the required coherent update'
Assert-Equal $false (Test-Path -LiteralPath (Join-Path $m '.git/obsolete-companion-called')) 'the incompatible companion is never called'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'default remote stays unchanged'
Assert-Equal '' (Get-OriginTip $w.Origin 'direct/7') 'no unchecked PR is published'

# Observe the actual public executor admission; do not mock Windows/WSL into Linux.
$executor = Join-Path (Split-Path $Script -Parent) 'ouro-process.py'
if (-not (Test-Path -LiteralPath $executor)) { $executor = Join-Path (Split-Path $Script -Parent) 'ouro/runtime/process.py' }
$admission = @(& python3 -c 'import importlib.util,sys; s=importlib.util.spec_from_file_location("admission",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.check_platform()' $executor 2>&1)
$inlineSupported = $LASTEXITCODE -eq 0
if (-not $inlineSupported) {
    Write-Host 'unsupported inline execution: actual public fallback, with no publication or invented receipts'
    foreach ($requiresPr in @($false, $true)) {
        $w = New-DirectWorld "unsupported-inline-$requiresPr"; $m = $w.Main
        Set-DirectCheck $w ($markCheck + "print('must not execute on unsupported caller')`n")
        Save-DirectBase $w
        $before = Get-OriginTip $w.Origin 'master'; Add-File $m 'docs/a.md'
        Set-Protection $(if ($requiresPr) { Rules 0 } else { $NoRules }) $NoClassic
        $c = Invoke-Child $Script @('-Issue', '7', '-ExpectedBase', $before) $m
        Assert-Equal 3 $c.Code 'unsupported caller requests the ordinary PR flow'
        Assert-Line $c.Lines 'fallback: express' 'names the existing fallback'
        Assert-Line $c.Lines 'reason: validation ' 'reports actual caller admission' -Prefix
        Assert-Equal $false (Test-Path -LiteralPath (Join-Path $m '.git/floor-ran')) 'no command starts without admission'
        Assert-Equal 0 @($c.Lines | Where-Object { $_ -cmatch '^(vehicle:|pushed |direct-checks:)' }).Count 'no publication or successful-check record'
        Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'default branch unchanged'
        Assert-Equal '' (Get-OriginTip $w.Origin 'direct/7') 'no unchecked PR branch'
    }
    $w = New-DirectWorld 'unsupported-empty-floor'; $m = $w.Main
    $bindingFile = Join-Path $m '.claude/ouro.toml'
    [IO.File]::WriteAllText($bindingFile, [IO.File]::ReadAllText($bindingFile).Replace('cheap = true', 'cheap = false'), $Utf8)
    Save-DirectBase $w; Add-File $m 'docs/a.md'
    Set-Protection $NoRules $NoClassic
    $c = Invoke-Child $Script @() $m
    Assert-Equal 3 $c.Code 'empty mandatory floor still refuses structurally'
    Assert-Line $c.Lines 'reason: no nonempty applicable cheap-gate floor' 'planning cannot silently grant an empty floor'
    Assert-Equal 0 $c.Gh.Count 'empty floor stops before protection queries'
    $w = New-DirectWorld 'unsupported-approver'; $m = $w.Main; Add-File $m 'docs/a.md'
    $before = Get-OriginTip $w.Origin 'master'
    Set-Protection (Rules 1) $NoClassic
    $c = Invoke-Child $Script @('-Issue', '7') $m
    Assert-Equal 3 $c.Code 'approving review routes away from direct before execution'
    Assert-Line $c.Lines 'reason: an approving review is required on master' 'review requirement remains explicit'
    Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'required review cannot be bypassed'
    Write-Host 'SKIP: native-inline publication positives require admitted native Linux; this host only proved refusal and portable policy'
    & python3 (Join-Path $PSScriptRoot 'direct_policy_tests.py') 2>&1 | ForEach-Object { Write-Host "    | $_" }
    if ($LASTEXITCODE -ne 0) { $script:failures++; Write-Host '  FAIL: shared direct policy controls failed' -ForegroundColor Red }
    Complete-Suite 'Push-Direct unsupported-caller and shared-policy'
}

Write-Host 'a late protection change cannot activate an obsolete PR companion'
$w = New-DirectWorld 'obsolete-companion-transition'; $m = $w.Main
Set-DirectCheck $w $markCheck; Save-DirectBase $w
Add-File $m 'docs/a.md'; $before = Get-OriginTip $w.Origin 'master'
Set-Gh @{ 'api repos/o/n/rules/branches/master' = (Seq @($NoRules, (Rules 0))); 'api repos/o/n/branches/master/protection' = $NoClassic }
$c = Invoke-Child (Join-Path $legacy 'Push-Direct.ps1') @('-Issue', '7') $m
Assert-Equal 1 $c.Code 'late PR requirement checks the companion before invocation'
Assert-Line $c.Lines 'STOP: Push-Branch.ps1 lacks checked-head support; update the direct helper bundle before retrying' 'changed protection cannot bypass bundle compatibility'
Assert-Equal $true (Test-Path -LiteralPath (Join-Path $m '.git/floor-ran')) 'the real mandatory floor passed before protection changed'
Assert-Equal $false (Test-Path -LiteralPath (Join-Path $m '.git/obsolete-companion-called')) 'the incompatible companion remains uncalled'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'default remote is unchanged'
Assert-Equal '' (Get-OriginTip $w.Origin 'direct/7') 'no unchecked PR branch appears'

Write-Host 'no pull-request requirement: a plain push, origin at HEAD'
$w = New-DirectWorld 'plain'; $m = $w.Main
$before = Get-OriginTip $w.Origin 'master'
Add-File $m 'docs/a.md'; Add-File $m 'README.md'
$head = @(G $m rev-parse HEAD)[0]
Set-Protection $NoRules $NoClassic
$c = Invoke-Child $Script @('-ExpectedHead', $head, '-ExpectedBase', $before) $m
Assert-Equal 0 $c.Code 'exits 0'
Assert-Line $c.Lines 'vehicle: push' 'names the vehicle'
Assert-Line $c.Lines "pushed master to origin at $head" 'prints the push'
Assert-Equal $head (Get-OriginTip $w.Origin 'master') 'origin has the default branch at HEAD'

Write-Host 'the plan offers neither rules nor classic protection: the same plain push'
$w = New-DirectWorld 'gated'; $m = $w.Main
Add-File $m 'docs/a.md'
$head = @(G $m rev-parse HEAD)[0]
$gated = Fail 'gh: Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)'
Set-Protection $gated $gated
$c = Invoke-Child $Script @() $m
Assert-Equal 0 $c.Code 'exits 0'
Assert-Equal $head (Get-OriginTip $w.Origin 'master') 'origin has the default branch at HEAD'

Write-Host 'a path outside [ship].direct stops before any gh call, naming it'
$w = New-DirectWorld 'outside'; $m = $w.Main
$before = Get-OriginTip $w.Origin 'master'
Add-File $m 'docs/a.md'; Add-File $m 'src/b.txt'
Set-Protection $NoRules $NoClassic
$c = Invoke-Child $Script @() $m
Assert-Equal 3 $c.Code 'routes to a PR'
Assert-Line $c.Lines 'reason: path is outside trusted ship.direct: src/b.txt' 'names the path'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'origin is unchanged'
Assert-Equal 0 $c.Gh.Count 'no gh call is made'

$w = New-DirectWorld 'case' '"README.md"'; $m = $w.Main
Add-File $m 'readme.md'
$c = Invoke-Child $Script @() $m
Assert-Equal 3 $c.Code 'a glob matches case-sensitively: readme.md is outside README.md'
Assert-Line $c.Lines 'reason: path is outside trusted ship.direct: readme.md' 'and names it'

Write-Host 'the union over the commits counts: a file one commit adds and a later one deletes'
$w = New-DirectWorld 'union'; $m = $w.Main
$before = Get-OriginTip $w.Origin 'master'
Add-File $m 'src/x.txt'
$null = G $m rm -q src/x.txt; $null = G $m commit -q -m 'delete x'
Add-File $m 'docs/a.md'
$c = Invoke-Child $Script @() $m
Assert-Equal 3 $c.Code 'routes to a PR'
Assert-Line $c.Lines 'reason: path is outside trusted ship.direct: src/x.txt' 'names the transient path'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'origin is unchanged'

Write-Host 'a rename out of the globs names the old path'
$w = New-DirectWorld 'rename'; $m = $w.Main
Add-File $m 'src/b.txt'; $null = G $m push -q origin master
$before = Get-OriginTip $w.Origin 'master'
New-Item -ItemType Directory -Force -Path (Join-Path $m 'docs') | Out-Null
$null = G $m mv src/b.txt docs/b.txt; $null = G $m commit -q -m 'move b into docs'
$c = Invoke-Child $Script @() $m
Assert-Equal 3 $c.Code 'routes to a PR'
Assert-Line $c.Lines 'reason: path is outside trusted ship.direct: src/b.txt' 'names src/b.txt'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'origin is unchanged'

Write-Host 'a dirty working tree stops before any push'
$w = New-DirectWorld 'dirty'; $m = $w.Main
$before = Get-OriginTip $w.Origin 'master'
Add-File $m 'docs/a.md'
[System.IO.File]::WriteAllText((Join-Path $m 'scratch.txt'), "x`n", $Utf8)
Set-Protection $NoRules $NoClassic
$c = Invoke-Child $Script @() $m
Assert-Equal 1 $c.Code 'exits 1'
Assert-Line $c.Lines 'STOP: the working tree is not clean (1 entries, first: ?? scratch.txt)' 'names the tree' -Prefix
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'origin is unchanged'
Assert-Equal 0 $c.Gh.Count 'no gh call is made'

Write-Host 'an empty or absent key turns the lane off'
$w = New-DirectWorld 'off' ''; $m = $w.Main
Add-File $m 'docs/a.md'
$before = Get-OriginTip $w.Origin 'master'
$c = Invoke-Child $Script @() $m
Assert-Equal 1 $c.Code 'an absent key exits 1'
Assert-Line $c.Lines 'STOP: ship.direct is empty or absent in the binding - the direct lane is off here' 'and says the lane is off'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'origin is unchanged'

Write-Host 'off the default branch, and an empty range, stop'
$w = New-DirectWorld 'shape'; $m = $w.Main
$c = Invoke-Child $Script @() $m
Assert-Equal 1 $c.Code 'an empty range exits 1'
Assert-Line $c.Lines 'STOP: nothing to push: no commit over origin/master changes a file' 'and says so'
$null = G $m checkout -q -b feat/x master
Add-File $m 'docs/a.md'
$c = Invoke-Child $Script @() $m
Assert-Equal 1 $c.Code 'a feature branch exits 1'
Assert-Line $c.Lines 'STOP: the checkout is on feat/x, not the default branch master' 'and names the branch' -Prefix

Write-Host 'a non-fast-forward is refused, origin unchanged'
$w = New-DirectWorld 'nff'; $m = $w.Main
$side = Join-Path $Scratch 'nff-side'
$null = git clone -q $w.Origin $side 2>&1
$null = New-Commit $side 'other.txt'; $null = G $side push -q origin master
$theirs = Get-OriginTip $w.Origin 'master'
Add-File $m 'docs/a.md'
Set-Protection $NoRules $NoClassic
$c = Invoke-Child $Script @() $m
Assert-Equal 3 $c.Code 'the fetched divergent range routes to a PR before push'
Assert-Line $c.Lines 'reason: base is not an ancestor of head' 'names the unproved ancestry'
Assert-Equal $theirs (Get-OriginTip $w.Origin 'master') 'origin is unchanged'

Write-Host 'origin moves between the push and the read-back: the push landed, the read-back stops'
$w = New-DirectWorld 'readback'; $m = $w.Main
$before = Get-OriginTip $w.Origin 'master'
Add-File $m 'docs/a.md'
$head = @(G $m rev-parse HEAD)[0]
Set-Protection $NoRules $NoClassic
# A post-receive hook that puts the ref back where it was: what a push landing after ours looks like to the read-back.
# git runs a hook on Linux and macOS only with its executable bit; Windows git runs it as written.
$hookPath = Join-Path $w.Origin 'hooks/post-receive'
[System.IO.File]::WriteAllText($hookPath, "#!/bin/sh`nread old new ref`ngit update-ref `"`$ref`" `"`$old`"`n", $Utf8)
if (-not $IsWindows) { & chmod +x $hookPath }
$c = Invoke-Child $Script @() $m
Assert-Equal 1 $c.Code 'exits 1'
Assert-Line $c.Lines "pushed master to origin at $head" 'the push itself landed'
Assert-Line $c.Lines "STOP: origin's master is at $before, not the pushed $head - someone pushed in between; fetch and read" 'the read-back names both tips'

Write-Host 'a pull-request requirement with no approval: the branch goes, the default branch stays'
foreach ($case in @(@('ruleset', (Rules 0), $NoClassic), @('classic', $NoRules, (Classic 0)),
        @('ruleset checks', (Answer @(@{ type = 'required_status_checks'; parameters = @{} })), $NoClassic),
        @('classic checks', $NoRules, (Answer @{ required_status_checks = @{ strict = $false; contexts = @('ci') } })))) {
    $w = New-DirectWorld "pr-$($case[0])"; $m = $w.Main
    $before = Get-OriginTip $w.Origin 'master'
    Add-File $m 'docs/a.md'
    $head = @(G $m rev-parse HEAD)[0]
    Set-Protection $case[1] $case[2]
    $c = Invoke-Child $Script @('-Issue', '7') $m
    Assert-Equal 0 $c.Code "$($case[0]): exits 0"
    Assert-Line $c.Lines 'vehicle: pr' "$($case[0]): names the vehicle"
    Assert-Line $c.Lines "pushed direct/7 to origin at $head" "$($case[0]): prints the branch push"
    Assert-Equal $head (Get-OriginTip $w.Origin 'direct/7') "$($case[0]): origin has direct/7 at HEAD"
    Assert-Equal $before (Get-OriginTip $w.Origin 'master') "$($case[0]): the default branch on origin is unchanged"
    Assert-Equal 'master' (@(G $m rev-parse --abbrev-ref HEAD)[0]) "$($case[0]): the checkout is back on the default branch"
    Assert-Equal $before (@(G $m rev-parse master)[0]) "$($case[0]): the local default branch is back at origin's tip"
    Assert-Equal $head (@(G $m rev-parse direct/7)[0]) "$($case[0]): the local branch holds the commits"
}
$w = New-DirectWorld 'pr-noissue'; $m = $w.Main
Add-File $m 'docs/a.md'
Set-Protection (Rules 0) $NoClassic
$c = Invoke-Child $Script @() $m
Assert-Equal 1 $c.Code 'without -Issue the pull-request vehicle exits 1'
Assert-Line $c.Lines 'STOP: master requires a pull request, and the branch is named for the issue: pass -Issue <N>' 'and asks for it'
Assert-Equal 0 (@(G $w.Origin for-each-ref 'refs/heads/direct/').Count) 'nothing is pushed'

Write-Host 'a required approval, or a read that failed: fallback to express, exit 3, nothing pushed'
$cases = @(
    @('ruleset count', (Rules 1), $NoClassic),
    @('ruleset code owner', (Rules 0 $true), $NoClassic),
    @('classic count', $NoRules, (Classic 2)),
    @('rules read failed', (Fail 'boom: the stand-in refused'), $NoClassic),
    @('classic read failed', $NoRules, (Fail 'boom: the stand-in refused')),
    @('classic code owner', $NoRules, (Answer @{ required_pull_request_reviews = @{ required_approving_review_count = 0; require_code_owner_reviews = $true } })),
    @('rule without parameters', (Answer @(@{ type = 'pull_request'; parameters = $null })), $NoClassic),
    @('rules answer null', (Answer $null), $NoClassic),
    @('classic answer null', $NoRules, (Answer $null))
)
foreach ($case in $cases) {
    $w = New-DirectWorld "fb-$($case[0] -replace ' ', '-')"; $m = $w.Main
    $before = Get-OriginTip $w.Origin 'master'
    Add-File $m 'docs/a.md'
    Set-Protection $case[1] $case[2]
    $c = Invoke-Child $Script @('-Issue', '7') $m
    Assert-Equal 3 $c.Code "$($case[0]): exits 3"
    Assert-Line $c.Lines 'fallback: express' "$($case[0]): prints the fallback"
    Assert-Equal $before (Get-OriginTip $w.Origin 'master') "$($case[0]): the default branch on origin is unchanged"
    Assert-Equal '' (Get-OriginTip $w.Origin 'direct/7') "$($case[0]): no branch is pushed"
}

Write-Host 'the help names each outcome'
$help = Get-Help ([WildcardPattern]::Escape($Script)) -Full | Out-String
foreach ($line in 'vehicle: push', 'vehicle: pr', 'fallback: express', 'Exit 3') {
    Assert-Match ([regex]::Escape($line)) $help "the help names '$line'"
}
Write-Host 'the public entry requires an applicable cheap floor and a genuine passing command'
$w = New-DirectWorld 'no-cheap'; $m = $w.Main
$bindingFile = Join-Path $m '.claude/ouro.toml'
[IO.File]::WriteAllText($bindingFile, [IO.File]::ReadAllText($bindingFile).Replace('cheap = true', 'cheap = false'), $Utf8)
Set-DirectCheck $w ($markCheck + "print('should not run')`n")
Save-DirectBase $w
$before = Get-OriginTip $w.Origin 'master'; Add-File $m 'docs/a.md'
Set-Protection $NoRules $NoClassic
$c = Invoke-Child $Script @() $m
Assert-Equal 3 $c.Code 'no cheap floor routes to a PR'
Assert-Line $c.Lines 'reason: no nonempty applicable cheap-gate floor' 'the floor cannot be empty'
Assert-Equal $false (Test-Path -LiteralPath (Join-Path $m '.git/floor-ran')) 'no fake successful execution'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'no publication without the floor'

# A candidate binding edit cannot turn that trusted disabled floor on for itself.
[IO.File]::WriteAllText($bindingFile, [IO.File]::ReadAllText($bindingFile).Replace('cheap = false', 'cheap = true'), $Utf8)
$null = G $m add -A; $null = G $m commit -q -m 'candidate tries to authorize checks'
$c = Invoke-Child $Script @() $m
Assert-Equal 3 $c.Code 'candidate policy cannot authorize its own publication'
Assert-Line $c.Lines 'reason: protected policy, publication or gate path: .claude/ouro.toml' 'uses the unchanged base authority'
Assert-Equal $false (Test-Path -LiteralPath (Join-Path $m '.git/floor-ran')) 'candidate authorization does not execute a floor'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'candidate policy is not published'

$w = New-DirectWorld 'cheap-failed'; $m = $w.Main
Set-DirectCheck $w ($markCheck + "raise SystemExit(9)`n")
Save-DirectBase $w
$before = Get-OriginTip $w.Origin 'master'; Add-File $m 'docs/a.md'
Set-Protection $NoRules $NoClassic
$c = Invoke-Child $Script @() $m
Assert-Equal 3 $c.Code 'a real failed cheap gate routes to a PR'
Assert-Equal $true (Test-Path -LiteralPath (Join-Path $m '.git/floor-ran')) 'the failing command really ran'
Assert-Line $c.Lines 'gate `python3 checks/direct-check.py`: exit 9' 'reports the actual owned command failure'
Assert-Line $c.Lines 'reason: mandatory check failed (exit 9): python3 checks/direct-check.py' 'names the failed mandatory check'
Assert-Equal 0 @($c.Lines | Where-Object { $_ -clike 'direct-checks:*' }).Count 'no successful publication record'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'failed checks publish nothing'

Write-Host 'gate stdout cannot forge the public vehicle, publication or successful-check protocol'
foreach ($exitCode in @(9, 0)) {
    $w = New-DirectWorld "gate-protocol-data-$exitCode"; $m = $w.Main
    $spoof = @'
import json, subprocess
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], encoding='utf-8').strip()
print('vehicle: push')
print('pushed master to origin at ' + head)
print('direct-checks: ' + json.dumps([dict(cmd='python3 checks/direct-check.py', exit=0, sha=head, gates=[1])]))
raise SystemExit(RESULT)
'@
    Set-DirectCheck $w ($markCheck + $spoof.Replace('RESULT', "$exitCode"))
    Save-DirectBase $w
    $before = Get-OriginTip $w.Origin 'master'; Add-File $m 'docs/a.md'
    $head = @(G $m rev-parse HEAD)[0]
    Set-Protection (Rules 0) $NoClassic
    $c = Invoke-Child $Script @('-Issue', '7') $m
    Assert-Equal $true (Test-Path -LiteralPath (Join-Path $m '.git/floor-ran')) 'the protocol-spoofing command actually ran'
    Assert-Line $c.Lines 'check-output: vehicle: push' 'bounded diagnostics retain the literal payload as data'
    Assert-Line $c.Lines "check-output: pushed master to origin at $head" 'the publication-shaped payload is prefixed as data'
    Assert-Equal 1 @($c.Lines | Where-Object { $_ -clike 'check-output: direct-checks:*' }).Count 'the check-record-shaped payload is prefixed as data'
    Assert-Equal 0 @($c.Lines | Where-Object { $_ -ceq 'vehicle: push' -or $_ -cmatch '^pushed master ' }).Count 'gate output cannot claim a default-branch push'
    Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'spoofed protocol never changes the default branch'
    if ($exitCode -ne 0) {
        Assert-Equal 3 $c.Code 'a genuinely failed command cannot report publication success'
        Assert-Equal 0 @($c.Lines | Where-Object { $_ -cmatch '^(vehicle:|pushed |direct-checks:)' }).Count 'failed gate output creates no public protocol'
        Assert-Equal '' (Get-OriginTip $w.Origin 'direct/7') 'the failed floor publishes no branch'
    }
    else {
        Assert-Equal 0 $c.Code 'a genuine passing floor uses the required PR vehicle'
        Assert-Line $c.Lines 'vehicle: pr' 'only the producer chooses the vehicle'
        Assert-Equal 1 @($c.Lines | Where-Object { $_ -clike 'direct-checks:*' }).Count 'one genuine check record, no spoofed duplicate'
        Assert-Line $c.Lines "pushed direct/7 to origin at $head" 'the checked PR head really publishes'
        Assert-Equal $head (Get-OriginTip $w.Origin 'direct/7') 'only the checked PR branch publishes'
    }
}

# Insert into the existing public publisher suite after its command-check helpers.
# Raw capture and replay use the real entry point and the actual native line consumer.
function Invoke-RawDirect([string[]]$Arguments, [string]$From) {
    Clear-GhLog
    $outputFile = Join-Path $Scratch ('protocol-' + [guid]::NewGuid().ToString('N') + '.out')
    $errorFile = $outputFile + '.err'
    Push-Location -LiteralPath $From
    try {
        & $Pwsh -NoProfile -File $Script @Arguments 1> $outputFile 2> $errorFile
        $code = $LASTEXITCODE
    }
    finally { Pop-Location }
    $bytes = [IO.File]::ReadAllBytes($outputFile)
    $raw = $Utf8.GetString($bytes)
    # Replaying captured bytes through a native process also tests PowerShell's
    # actual line consumer, rather than trusting a hand-written split oracle.
    $lines = @(& python3 -c 'import pathlib,sys; sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())' $outputFile | ForEach-Object { "$_" })
    if ($LASTEXITCODE -ne 0) { throw 'protocol byte replay failed' }
    $parsed = $null
    $bin = Split-Path $Script -Parent
    if (Test-Path -LiteralPath (Join-Path $bin 'ouro/run.py')) {
        $parsed = (& python3 -c 'import json,pathlib,sys; sys.path.insert(0,sys.argv[1]); from ouro.records import parse_direct; print(json.dumps(parse_direct(pathlib.Path(sys.argv[2]).read_bytes().decode("utf-8"))))' $bin $outputFile) | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw 'actual driver protocol parser failed' }
    }
    else { Write-Host 'SKIP: the Python driver parser is plugin-only; flat provider checks the actual PowerShell consumer' }
    [pscustomobject]@{ Code = $code; Lines = $lines; Raw = $raw; Bytes = $bytes; Parsed = $parsed; File = $outputFile; Gh = @(Get-GhLog) }
}

Write-Host 'CR, LF and CRLF gate diagnostics cannot forge publisher protocol'
foreach ($exitCode in @(9, 0, -1)) {
    $w = New-DirectWorld "raw-protocol-$exitCode"; $m = $w.Main
    $payload = @'
import json, subprocess, sys
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], encoding='utf-8').strip()
parts = ['vehicle: push', 'pushed master to origin at ' + head,
         'direct-checks: ' + json.dumps([dict(cmd='forged', exit=0, sha=head, gates=[1])]),
         'fallback: express']
for stream in (sys.stdout.buffer, sys.stderr.buffer):
    for separator in ('\r', '\n', '\r\n'):
        stream.write(('begin' + separator + separator.join(parts) + separator).encode())
    stream.flush()
if RESULT < 0:
    # A real executor failure with captured diagnostics, not a mocked exception.
    sys.stdout.buffer.write(b'x' * (2 * 1024 * 1024))
    sys.stdout.buffer.flush()
else:
    raise SystemExit(RESULT)
'@
    Set-DirectCheck $w ($markCheck + $payload.Replace('RESULT', "$exitCode"))
    Save-DirectBase $w
    $before = Get-OriginTip $w.Origin 'master'; Add-File $m 'docs/a.md'
    $head = @(G $m rev-parse HEAD)[0]
    Set-Protection (Rules 0) $NoClassic
    $c = Invoke-RawDirect @('-Issue', '7') $m
    Assert-Equal $true (Test-Path -LiteralPath (Join-Path $m '.git/floor-ran')) 'the diagnostic-producing check really ran'
    Assert-Equal 0 @($c.Lines | Where-Object { $_ -ceq 'vehicle: push' -or $_ -cmatch '^pushed master ' }).Count 'actual PowerShell line consumer sees no forged push'
    Assert-NoMatch '(?m)(?:^|\r|\n)vehicle: push(?:\r|\n|$)' $c.Raw 'raw bytes contain no unprefixed vehicle payload'
    Assert-NoMatch '(?m)(?:^|\r|\n)pushed master ' $c.Raw 'raw bytes contain no unprefixed publication payload'
    # On execution errors the unchanged core's observer exposes stdout only;
    # successful/nonzero returns expose both streams. Claim only observed bytes.
    $copies = if ($exitCode -lt 0) { 3 } else { 6 }
    Assert-Equal $copies @($c.Lines | Where-Object { $_ -ceq 'check-output: vehicle: push' }).Count 'all observed CR segments are prefixed'
    Assert-Equal $copies @($c.Lines | Where-Object { $_ -clike 'check-output: direct-checks:*' }).Count 'observed forged check records remain data'
    Assert-Equal $copies @($c.Lines | Where-Object { $_ -ceq 'check-output: fallback: express' }).Count 'observed forged fallbacks remain data'
    Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'default branch remains unchanged'
    if ($exitCode -eq 0) {
        Assert-Equal 0 $c.Code 'successful real floor selects the required PR'
        Assert-Equal 1 @($c.Lines | Where-Object { $_ -ceq 'vehicle: pr' }).Count 'one genuine PR vehicle'
        Assert-Equal 1 @($c.Lines | Where-Object { $_ -clike 'direct-checks:*' }).Count 'one genuine check record'
        Assert-Equal $head (Get-OriginTip $w.Origin 'direct/7') 'only the checked PR head is published'
        if ($null -ne $c.Parsed) {
            Assert-Equal 'pr' $c.Parsed.vehicle 'actual Python driver parser sees the genuine PR'
            Assert-Equal $true $c.Parsed.pushed 'actual Python driver parser sees the real branch push'
        }
    }
    else {
        Assert-Equal 3 $c.Code 'failed execution requests the ordinary PR flow'
        Assert-Equal 0 @($c.Lines | Where-Object { $_ -cmatch '^(vehicle:|pushed |direct-checks:)' }).Count 'failed execution creates no publication record'
        Assert-Equal '' (Get-OriginTip $w.Origin 'direct/7') 'failed execution pushes no branch'
        if ($null -ne $c.Parsed) {
            Assert-Equal '' $c.Parsed.vehicle 'actual Python driver parser sees no vehicle after failure'
            Assert-Equal $false $c.Parsed.pushed 'actual Python driver parser sees no push after failure'
        }
        if ($exitCode -lt 0) { Assert-Line $c.Lines 'reason: runtime output limit exceeded' 'the real executor error is retained' }
    }
}

Write-Host 'a commit made during the final protection read keeps its branch and file'
$w = New-DirectWorld 'late-protection-owner-head'; $m = $w.Main
Set-DirectCheck $w $markCheck; Save-DirectBase $w
Add-File $m 'docs/a.md'; $before = Get-OriginTip $w.Origin 'master'
$quotedMain = $m.Replace("'", "''")
$lateRule = Rules 0
$lateRule['Run'] = "[IO.File]::WriteAllText('$quotedMain/docs/owner.md', 'owner H2', [Text.UTF8Encoding]::new(`$false)); & git -C '$quotedMain' add docs/owner.md; & git -C '$quotedMain' commit -qm owner-H2; `$ownerHead = & git -C '$quotedMain' rev-parse HEAD; [IO.File]::WriteAllText('$quotedMain/.git/owner-h2', `$ownerHead)"
Set-Gh @{ 'api repos/o/n/rules/branches/master' = (Seq @((Rules 0), $lateRule)); 'api repos/o/n/branches/master/protection' = $NoClassic }
$c = Invoke-Child $Script @('-Issue', '7') $m
$ownerHead = [IO.File]::ReadAllText((Join-Path $m '.git/owner-h2'))
Assert-Equal 3 $c.Code 'late owner commit refuses publication'
Assert-Line $c.Lines 'reason: the checked publication head or branch changed during the protection read' 'the observed late commit is named'
Assert-Equal $ownerHead (@(G $m rev-parse refs/heads/master)[0]) 'owner H2 retains the default-branch reference'
Assert-Equal 'owner H2' ([IO.File]::ReadAllText((Join-Path $m 'docs/owner.md'))) 'owner file remains intact'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'no unchecked default publication'
Assert-Equal '' (Get-OriginTip $w.Origin 'direct/7') 'no unchecked PR publication'

Write-Host 'the PR vehicle resets the default ref only with the checked old-head identity'
$w = New-DirectWorld 'late-default-ref-cas'; $m = $w.Main
Set-DirectCheck $w $markCheck; Save-DirectBase $w
Add-File $m 'docs/a.md'; $head = @(G $m rev-parse HEAD)[0]
$before = Get-OriginTip $w.Origin 'master'
$ownerHead = @(G $m commit-tree "${head}^{tree}" -p $head -m 'concurrent owner H2')[0]
$hook = @'
#!/bin/sh
test "$(git symbolic-ref --short HEAD)" = 'direct/7' || exit 0
git update-ref refs/heads/master OWNER_HEAD CHECKED_HEAD || exit 1
printf ran > .git/cas-hook-ran
'@
$hookPath = Join-Path $m '.git/hooks/post-checkout'
[IO.File]::WriteAllText($hookPath, $hook.Replace('OWNER_HEAD', $ownerHead).Replace('CHECKED_HEAD', $head), $Utf8)
if (-not $IsWindows) { & chmod +x $hookPath }
Set-Protection (Rules 0) $NoClassic
$c = Invoke-Child $Script @('-Issue', '7') $m
Assert-Equal 1 $c.Code 'late default ref makes the restoration fail visibly after the checked PR push'
Assert-Equal $true (Test-Path -LiteralPath (Join-Path $m '.git/cas-hook-ran')) 'the default ref really advanced after switching to the PR branch'
Assert-Equal $ownerHead (@(G $m rev-parse refs/heads/master)[0]) 'compare-and-swap preserves the concurrent owner commit'
Assert-Equal $ownerHead (@(G $m rev-parse HEAD)[0]) 'checkout returns to the preserved owner head'
Assert-Equal $head (Get-OriginTip $w.Origin 'direct/7') 'partial result truthfully retains only the checked PR publication'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'default remote remains at its prior base'
Assert-Line $c.Lines "pushed direct/7 to origin at $head" 'the actual successful branch push remains visible'
Assert-Equal 0 @($c.Lines | Where-Object { $_ -cmatch '^pushed master ' }).Count 'no default push is invented'

Write-Host 'compare-and-swap retains the existing other-worktree branch protection'
$w = New-DirectWorld 'late-default-other-worktree'; $m = $w.Main
Set-DirectCheck $w $markCheck; Save-DirectBase $w
Add-File $m 'docs/a.md'; $head = @(G $m rev-parse HEAD)[0]
$before = Get-OriginTip $w.Origin 'master'
$otherTree = Join-Path (Split-Path $m -Parent) 'owner-worktree'
$hook = @'
#!/bin/sh
test "$(git symbolic-ref --short HEAD)" = 'direct/7' || exit 0
git worktree add -q "$OURO_TEST_OTHER_TREE" master || exit 1
printf 'owner elsewhere' > "$OURO_TEST_OTHER_TREE/owner.txt"
'@
$hookPath = Join-Path $m '.git/hooks/post-checkout'
[IO.File]::WriteAllText($hookPath, $hook, $Utf8)
if (-not $IsWindows) { & chmod +x $hookPath }
Set-Protection (Rules 0) $NoClassic
$savedOtherTree = [Environment]::GetEnvironmentVariable('OURO_TEST_OTHER_TREE')
try {
    $env:OURO_TEST_OTHER_TREE = $otherTree
    $c = Invoke-Child $Script @('-Issue', '7') $m
}
finally { Set-EnvVar 'OURO_TEST_OTHER_TREE' $savedOtherTree }
Assert-Equal 1 $c.Code 'occupied default branch stops restoration visibly'
Assert-Equal $head (@(G $m rev-parse refs/heads/master)[0]) 'default ref remains intact for the other worktree'
Assert-Equal $head (@(G $otherTree rev-parse HEAD)[0]) 'other worktree retains its actual head'
Assert-Equal 'owner elsewhere' ([IO.File]::ReadAllText((Join-Path $otherTree 'owner.txt'))) 'other worktree data is untouched'
Assert-Equal $head (Get-OriginTip $w.Origin 'direct/7') 'only the checked PR head was published'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'default remote is unchanged'

Write-Host 'a late ignored owner file is not overwritten when restoring the default checkout'
$w = New-DirectWorld 'late-ignored-owner-file'; $m = $w.Main
Set-DirectCheck $w $markCheck
New-Item -ItemType Directory -Force -Path (Join-Path $m 'docs') > $null
[IO.File]::WriteAllText((Join-Path $m 'docs/owner.md'), 'base owner file', $Utf8)
$null = G $m add docs/owner.md
[IO.File]::AppendAllText((Join-Path $m '.gitignore'), "docs/owner.md`n", $Utf8)
Save-DirectBase $w
$before = Get-OriginTip $w.Origin 'master'
$null = G $m rm -q docs/owner.md
$null = G $m commit -qm 'remove tracked owner file'
$head = @(G $m rev-parse HEAD)[0]
$quotedMain = $m.Replace("'", "''")
$lateRule = Rules 0
$lateRule['Run'] = "[IO.Directory]::CreateDirectory('$quotedMain/docs') > `$null; [IO.File]::WriteAllText('$quotedMain/docs/owner.md', 'late ignored owner data', [Text.UTF8Encoding]::new(`$false))"
Set-Gh @{ 'api repos/o/n/rules/branches/master' = (Seq @((Rules 0), $lateRule)); 'api repos/o/n/branches/master/protection' = $NoClassic }
$c = Invoke-Child $Script @('-Issue', '7') $m
Assert-Equal 1 $c.Code 'unsafe default checkout fails visibly after the checked PR publication'
Assert-Equal 'late ignored owner data' ([IO.File]::ReadAllText((Join-Path $m 'docs/owner.md'))) 'late ignored bytes survive the non-forced checkout'
Assert-Equal $head (Get-OriginTip $w.Origin 'direct/7') 'only the checked PR commit is published'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'default remote is unchanged'
Assert-Equal 'direct/7' (@(G $m rev-parse --abbrev-ref HEAD)[0]) 'unproved checkout is retained for manual reconciliation'

Write-Host 'an existing different local PR branch is retained rather than reset'
$w = New-DirectWorld 'existing-pr-owner-head'; $m = $w.Main
Set-DirectCheck $w $markCheck; Save-DirectBase $w
Add-File $m 'docs/a.md'; $head = @(G $m rev-parse HEAD)[0]
$before = Get-OriginTip $w.Origin 'master'
$ownerHead = @(G $m commit-tree "${head}^{tree}" -p $head -m 'existing owner PR work')[0]
$null = G $m branch direct/7 $ownerHead
Set-Protection (Rules 0) $NoClassic
$c = Invoke-Child $Script @('-Issue', '7') $m
Assert-Equal 1 $c.Code 'different local PR branch refuses publication'
Assert-Line $c.Lines 'STOP: the local PR branch holds a different commit; preserve it before retrying - nothing is pushed' 'names the retained local branch'
Assert-Equal $ownerHead (@(G $m rev-parse refs/heads/direct/7)[0]) 'existing owner PR reference is intact'
Assert-Equal $head (@(G $m rev-parse HEAD)[0]) 'original candidate checkout remains intact'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'default origin unchanged'
Assert-Equal '' (Get-OriginTip $w.Origin 'direct/7') 'existing owner branch was not published'

Write-Host 'code coverage is explicit and a targeted code check need not be cheap'
foreach ($covered in @($false, $true)) {
    $w = New-DirectWorld "code-$covered" '"src/*"'; $m = $w.Main
    if ($covered) {
        $file = Join-Path $m '.claude/ouro.toml'
        [IO.File]::AppendAllText($file, "`n[[gate]]`npaths = [`"src/`"]`ndirect_code_check = true`nrun = `"python3 checks/code-check.py`"`n", $Utf8)
        [IO.File]::WriteAllText((Join-Path $m 'checks/code-check.py'), "from pathlib import Path`nassert Path('src/app.py').read_text().strip() == 'pass'`nprint('targeted code ran')`n", $Utf8)
        Save-DirectBase $w
    }
    $before = Get-OriginTip $w.Origin 'master'
    New-Item -ItemType Directory -Path (Join-Path $m 'src') | Out-Null
    [IO.File]::WriteAllText((Join-Path $m 'src/app.py'), "pass`n", $Utf8)
    $null = G $m add -A; $null = G $m commit -q -m 'tiny source change'
    $head = @(G $m rev-parse HEAD)[0]
    Set-Protection $NoRules $NoClassic
    $c = Invoke-Child $Script @() $m
    if ($covered) {
        Assert-Equal 0 $c.Code 'a real passing noncheap code check permits the tiny source change'
        Assert-Line $c.Lines 'check-output: targeted code ran' 'the targeted command really ran'
        $record = @($c.Lines | Where-Object { $_ -clike 'direct-checks:*' })[0].Substring('direct-checks: '.Length) | ConvertFrom-Json
        Assert-Equal 2 @($record).Count 'both cheap and noncheap obligations are recorded'
        Assert-Equal 0 @($record | Where-Object { $_.exit -ne 0 -or $_.sha -cne $head }).Count 'records bind actual successes to the published head'
        Assert-Equal $head (Get-OriginTip $w.Origin 'master') 'the checked source head was published'
    }
    else {
        Assert-Equal 3 $c.Code 'tree-only cheap checks cannot authorize source'
        Assert-Line $c.Lines 'reason: changed code lacks an explicit applicable direct_code_check' 'names missing coverage'
        Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'uncovered source was not published'
    }
}

Write-Host 'the public entry enforces the exact cumulative line and file boundaries'
foreach ($row in @(@(20, 3, 0), @(21, 3, 3), @(4, 4, 3))) {
    $lines, $files, $expected = $row
    $w = New-DirectWorld "bar-$lines-$files"; $m = $w.Main
    $before = Get-OriginTip $w.Origin 'master'
    New-Item -ItemType Directory -Path (Join-Path $m 'docs') | Out-Null
    for ($i = 0; $i -lt $files; $i++) {
        $count = [int][Math]::Floor($lines / $files) + $(if ($i -lt ($lines % $files)) { 1 } else { 0 })
        [IO.File]::WriteAllText((Join-Path $m "docs/$i.md"), ("line`n" * $count), $Utf8)
    }
    $null = G $m add -A; $null = G $m commit -q -m 'size boundary'
    $head = @(G $m rev-parse HEAD)[0]
    Set-Protection $NoRules $NoClassic
    $c = Invoke-Child $Script @() $m
    Assert-Equal $expected $c.Code "$lines lines/$files files uses the ruled public floor"
    Assert-Equal $(if ($expected -eq 0) { $head } else { $before }) (Get-OriginTip $w.Origin 'master') 'only an eligible checked head reaches origin'
}

Write-Host 'final inputs, remote tip and approving-review requirements are rechecked after gates'
foreach ($change in @('dirty', 'head', 'branch', 'remote', 'protection')) {
    $w = New-DirectWorld "during-$change"; $m = $w.Main
    $extra = ''
    if ($change -eq 'dirty') { $extra = "Path('docs/a.md').write_text('changed by gate')`n" }
    elseif ($change -eq 'head') { $extra = "import subprocess`nsubprocess.run(['git','commit','--allow-empty','-qm','gate moved head'],check=True)`n" }
    elseif ($change -eq 'branch') { $extra = "import subprocess`nsubprocess.run(['git','checkout','-qb','gate-moved'],check=True)`n" }
    elseif ($change -eq 'remote') {
        $side = Join-Path $Scratch 'gate-remote-side'
        $null = git clone -q $w.Origin $side 2>&1
        $null = New-Commit $side 'other.txt'
        $extra = "import subprocess`nsubprocess.run(['git','-C'," + (Json $side) + ",'push','-q','origin','master'],check=True)`n"
    }
    Set-DirectCheck $w ($markCheck + $extra)
    Save-DirectBase $w
    # The remote-side control must descend from the latest trusted base.
    if ($change -eq 'remote') {
        $null = G $side fetch -q origin
        $null = G $side rebase origin/master
    }
    $before = Get-OriginTip $w.Origin 'master'; Add-File $m 'docs/a.md'
    if ($change -eq 'protection') {
        Set-Gh @{ 'api repos/o/n/rules/branches/master' = (Seq @($NoRules, (Rules 1))); 'api repos/o/n/branches/master/protection' = $NoClassic }
    }
    else { Set-Protection $NoRules $NoClassic }
    $c = Invoke-Child $Script @() $m
    Assert-Equal 3 $c.Code "$change during a real gate refuses direct publication"
    Assert-Equal $true (Test-Path -LiteralPath (Join-Path $m '.git/floor-ran')) "${change}: the gate actually ran"
    Assert-Equal 0 @($c.Lines | Where-Object { $_ -clike 'direct-checks:*' }).Count "${change}: no completed publication record"
    if ($change -eq 'remote') {
        Assert-Equal (@(G $side rev-parse HEAD)[0]) (Get-OriginTip $w.Origin 'master') 'only the independently advanced remote head remains'
    }
    else { Assert-Equal $before (Get-OriginTip $w.Origin 'master') "${change}: origin is unchanged" }
}

Write-Host 'a missing shared helper fails closed instead of using legacy path-only publication'
$portable = Join-Path $Scratch 'missing-policy'
New-Item -ItemType Directory -Path $portable | Out-Null
foreach ($name in 'Push-Direct.ps1', 'Push-Branch.ps1', 'Get-LandBinding.ps1', 'ouro-binding.py') {
    Copy-Item -LiteralPath (Find-Script $name) -Destination (Join-Path $portable $name)
}
$w = New-DirectWorld 'missing-policy-world'; $m = $w.Main; Add-File $m 'docs/a.md'
$before = Get-OriginTip $w.Origin 'master'
Set-Protection $NoRules $NoClassic
$c = Invoke-Child (Join-Path $portable 'Push-Direct.ps1') @() $m
Assert-Equal 1 $c.Code 'missing helper is a hard public-entry refusal'
Assert-Line $c.Lines 'STOP: ouro-direct.py is unavailable; no direct publication is permitted' 'names the missing authority'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'missing authority publishes nothing'

Write-Host 'a missing flat owned executor also refuses instead of starting an unowned check'
$portable = Join-Path $Scratch 'missing-owned-executor'
New-Item -ItemType Directory -Path $portable | Out-Null
foreach ($name in 'Push-Direct.ps1', 'Push-Branch.ps1', 'Get-LandBinding.ps1', 'ouro-binding.py', 'ouro-direct.py') {
    Copy-Item -LiteralPath (Find-Script $name) -Destination (Join-Path $portable $name)
}
$w = New-DirectWorld 'missing-owned-executor-world'; $m = $w.Main
Set-DirectCheck $w ($markCheck + "print('must not run without owned executor')`n")
Save-DirectBase $w
Add-File $m 'docs/a.md'; $before = Get-OriginTip $w.Origin 'master'
Set-Protection $NoRules $NoClassic
$c = Invoke-Child (Join-Path $portable 'Push-Direct.ps1') @() $m
Assert-Equal 3 $c.Code 'missing owned executor routes to a PR without launching a check'
Assert-Line $c.Lines 'reason: owned direct executor is unavailable; use a PR' 'the missing execution authority is reported'
Assert-Equal $false (Test-Path -LiteralPath (Join-Path $m '.git/floor-ran')) 'the gate was not launched without ownership'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'no unowned publication'

Write-Host 'a post-checkout hook cannot substitute an unchecked head for the checked PR vehicle'
$w = New-DirectWorld 'checkout-head-substitution'; $m = $w.Main
Set-DirectCheck $w ($markCheck + "print('checked the original head')`n")
Save-DirectBase $w
Add-File $m 'docs/a.md'
$before = Get-OriginTip $w.Origin 'master'
$checkedHead = @(G $m rev-parse HEAD)[0]
$hook = @'
#!/bin/sh
test "$(git symbolic-ref --short HEAD)" = 'direct/7' || exit 0
test -f .git/direct-hook-ran && exit 0
printf ran > .git/direct-hook-ran
printf 'unchecked by checkout hook\n' >> docs/a.md
git add docs/a.md
git commit -qm unchecked-post-checkout
'@
$hookPath = Join-Path $m '.git/hooks/post-checkout'
[IO.File]::WriteAllText($hookPath, $hook, $Utf8)
if (-not $IsWindows) { & chmod +x $hookPath }
Set-Protection (Rules 0) $NoClassic
$c = Invoke-Child $Script @('-Issue', '7') $m
$uncheckedHead = @(G $m rev-parse direct/7)[0]
Assert-Equal $true (Test-Path -LiteralPath (Join-Path $m '.git/floor-ran')) 'the original checked head passed a real floor'
Assert-Equal $true (Test-Path -LiteralPath (Join-Path $m '.git/direct-hook-ran')) 'the post-checkout control actually executed'
Assert-Equal $true ($uncheckedHead -cne $checkedHead) 'the hook really produced H2 after checking H'
Assert-Equal 1 $c.Code 'the changed PR head is refused'
Assert-Line $c.Lines 'STOP: the checked publication head changed before the branch push - nothing is pushed' 'names the unchecked PR head substitution'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'the default branch is unchanged'
Assert-Equal '' (Get-OriginTip $w.Origin 'direct/7') 'no unchecked PR branch was published'
Assert-Equal 0 @($c.Lines | Where-Object { $_ -cmatch '^pushed ' }).Count 'no publication success is reported'

Write-Host 'the shared policy controls run inside the existing landing suite'
$policyTests = Join-Path $PSScriptRoot 'direct_policy_tests.py'
& python3 $policyTests 2>&1 | ForEach-Object { Write-Host "    | $_" }
if ($LASTEXITCODE -ne 0) {
    Write-Host '  FAIL: shared direct policy controls failed' -ForegroundColor Red
    $script:failures++
}
Complete-Suite 'Push-Direct'
