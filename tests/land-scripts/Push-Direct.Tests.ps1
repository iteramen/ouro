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
    [ship].direct, an empty or absent key, a checkout off the default branch, an empty range and a
    non-fast-forward each stop with exit 1 and origin unchanged; a pull-request requirement with no
    approving review prints `vehicle: pr`, pushes `direct/<issue>` and leaves the default branch
    on origin where it was, whether the requirement is a ruleset or classic protection; a required
    approval (either source, count or code-owner review) and a protection read that failed print
    `fallback: express`, exit 3, and push nothing; the help names each outcome.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'LandScripts.Fixture.ps1')
$Script = Find-Script 'Push-Direct.ps1'

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
        [System.IO.File]::WriteAllText($file, $text.Replace("[ship]`n", "[ship]`ndirect = [$Direct]`n"), $Utf8)
        $null = G $w.Main commit -q -am 'binding: direct'
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

Write-Host 'no pull-request requirement: a plain push, origin at HEAD'
$w = New-DirectWorld 'plain'; $m = $w.Main
Add-File $m 'docs/a.md'; Add-File $m 'README.md'
$head = @(G $m rev-parse HEAD)[0]
Set-Protection $NoRules $NoClassic
$c = Invoke-Child $Script @() $m
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
Assert-Equal 1 $c.Code 'exits 1'
Assert-Line $c.Lines 'STOP: the range changes 1 path(s) outside [ship].direct: src/b.txt - nothing is pushed; the run is express or tighter, by contract section 4''s classes' 'names the path'
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'origin is unchanged'
Assert-Equal 0 $c.Gh.Count 'no gh call is made'

$w = New-DirectWorld 'case' '"README.md"'; $m = $w.Main
Add-File $m 'readme.md'
$c = Invoke-Child $Script @() $m
Assert-Equal 1 $c.Code 'a glob matches case-sensitively: readme.md is outside README.md'
Assert-Line $c.Lines 'STOP: the range changes 1 path(s) outside [ship].direct: readme.md' 'and names it' -Prefix

Write-Host 'the union over the commits counts: a file one commit adds and a later one deletes'
$w = New-DirectWorld 'union'; $m = $w.Main
$before = Get-OriginTip $w.Origin 'master'
Add-File $m 'src/x.txt'
$null = G $m rm -q src/x.txt; $null = G $m commit -q -m 'delete x'
Add-File $m 'docs/a.md'
$c = Invoke-Child $Script @() $m
Assert-Equal 1 $c.Code 'exits 1'
Assert-Line $c.Lines 'STOP: the range changes 1 path(s) outside [ship].direct: src/x.txt' 'names the transient path' -Prefix
Assert-Equal $before (Get-OriginTip $w.Origin 'master') 'origin is unchanged'

Write-Host 'a rename out of the globs names the old path'
$w = New-DirectWorld 'rename'; $m = $w.Main
Add-File $m 'src/b.txt'; $null = G $m push -q origin master
$before = Get-OriginTip $w.Origin 'master'
New-Item -ItemType Directory -Force -Path (Join-Path $m 'docs') | Out-Null
$null = G $m mv src/b.txt docs/b.txt; $null = G $m commit -q -m 'move b into docs'
$c = Invoke-Child $Script @() $m
Assert-Equal 1 $c.Code 'exits 1'
Assert-Line $c.Lines 'STOP: the range changes 1 path(s) outside [ship].direct: src/b.txt' 'names src/b.txt' -Prefix
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
Assert-Equal 1 $c.Code 'exits 1'
Assert-Line $c.Lines 'STOP: the push was refused: ' 'carries git''s refusal' -Prefix
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
Complete-Suite 'Push-Direct'
