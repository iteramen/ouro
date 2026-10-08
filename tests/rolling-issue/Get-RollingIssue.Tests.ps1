<#
.SYNOPSIS
    Unit test for Get-RollingIssue.ps1's title resolution and issue selection.
.DESCRIPTION
    The defect this pins: two gates carried the rolling issue's title as a literal, so a
    repo that renamed it got a green gate that posted nothing, and a failed `gh` call was
    indistinguishable from "no such issue" -- both produced an empty string and a skipped
    comment.

    So the outcomes are asserted to be DIFFERENT, which is the whole point: an absent key
    throws, an absent issue warns and returns 0, a closed-only match is reopened and
    returned, and an open hit returns its number. Selection is by exact title, never the
    token search that `in:title` performs.

    Binding reads use a scratch binding in TEMP; issue lookups use the -IssuesJson
    injection or a stub gh on PATH, which answers in gh's output format and records each
    call's argv, so the live search and the default reopen are asserted by the arguments
    they pass, and the suite needs neither `gh` nor network.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Resolver = @((Join-Path $Base 'Get-RollingIssue.ps1'), (Join-Path $Base 'bin/Get-RollingIssue.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Resolver) { throw "Get-RollingIssue.ps1 not found under $Base" }
. $Resolver

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -eq $Actual) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}
function Assert-Throws($Script, $Match, $What) {
    try { & $Script; Write-Host "  FAIL: $What -- did not throw" -ForegroundColor Red; $script:failures++ }
    catch {
        if ("$_" -match $Match) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
        else { Write-Host "  FAIL: $What -- threw '$_', expected /$Match/" -ForegroundColor Red; $script:failures++ }
    }
}

$issues = '[{"number":11,"title":"Docs drift audit"},{"number":22,"title":"Docs drift audit follow-ups"},{"number":33,"title":"Renamed audit"}]'

# --- selection is by EXACT title, not the token search in:title performs ----------------
Assert-Equal 11 (Get-RollingIssueNumber -Title 'Docs drift audit' -IssuesJson $issues) `
    'an exact title match returns its number'
# The hijack `in:title` alone would allow: every token of this title appears in issues 11 and 22,
# and a token search would return one of them. The equality test must return neither.
Assert-Equal 0 (Get-RollingIssueNumber -Title 'Docs drift' -IssuesJson $issues) `
    'a token-subset title matches nothing -- selection is equality, not in:title'
Assert-Equal 33 (Get-RollingIssueNumber -Title 'Renamed audit' -IssuesJson $issues) `
    'renaming the title changes the target with no script edit'

# --- an absent issue is a WARN and 0, distinguishable from a failure --------------------
Assert-Equal 0 (Get-RollingIssueNumber -Title 'Nothing Titled This' -IssuesJson $issues) `
    'an absent issue returns 0 rather than throwing'
Assert-Equal 0 (Get-RollingIssueNumber -Title 'Docs drift audit' -IssuesJson '[]') `
    'an empty issue list returns 0'

# --- a CLOSED ledger is reopened, never duplicated ----------------------------------------
# The incident: a ledger closed in a backlog tidy read as "no issue", and the sweep filed a
# second one with the same title. The reopen is recorded through -Reopen, so no `gh` runs.
$reopened = [System.Collections.Generic.List[object]]::new()
$recordReopen = { param($Number, $Body) $reopened.Add([pscustomobject]@{ Number = $Number; Body = $Body }) }
function Resolve-Ledger($Json, $Title = 'Docs freshness') {
    try { Get-RollingIssueNumber -Title $Title -IssuesJson $Json -Reopen $recordReopen } catch { "threw: $_" }
}
$closed = '{"number":44,"title":"Docs freshness","state":"CLOSED"}'
$open = '{"number":55,"title":"Docs freshness","state":"OPEN"}'
Assert-Equal 55 (Resolve-Ledger "[$closed,$open]") 'an open match wins over a closed one with the same title'
Assert-Equal 0 $reopened.Count 'an open match reopens nothing'
Assert-Equal 44 (Resolve-Ledger "[$closed]") 'a closed-only match is reopened and its number returned'
Assert-Equal 1 $reopened.Count 'the closed-only match is reopened exactly once'
if ($reopened.Count -eq 1) {
    Assert-Equal 44 $reopened[0].Number 'the reopen targets the closed match'
    Assert-Equal $true ($reopened[0].Body -match 'machinery, not backlog') 'the reopen comment says why'
}
Assert-Equal 0 (Resolve-Ledger "[$closed,$open]" 'Loop runs') 'no match among open and closed issues still returns 0'

# The two WARN lines above name $Key only when the title was actually read through it. Every call
# in this file so far passes -Title directly, so $Key was never consulted, and naming it would
# credit the wrong source for the title -- its own recorder, so the shared one above is untouched.
$isolatedReopen = { param($Number, $Body) }
$warnedClosed = (@(Get-RollingIssueNumber -Title 'Docs freshness' -IssuesJson "[$closed]" -Reopen $isolatedReopen 6>&1) |
        Where-Object { $_ -is [System.Management.Automation.InformationRecord] }) -join "`n"
Assert-Equal $true ($warnedClosed -eq "WARN - 'Docs freshness' is closed: reopening #44 rather than filing a duplicate.") `
    'a closed-only match reopened with -Title passed warns naming the title alone, no key'
$warnedMiss = (@(Get-RollingIssueNumber -Title 'Loop runs' -IssuesJson "[$closed,$open]" -Reopen $isolatedReopen 6>&1) |
        Where-Object { $_ -is [System.Management.Automation.InformationRecord] }) -join "`n"
Assert-Equal $true ($warnedMiss -eq "WARN - no issue titled 'Loop runs', open or closed: a gate's comment has nowhere to go; a sweep that creates the issue continues.") `
    'no match with -Title passed warns naming the title alone, no key'

# Exact means case too: `in:title` ignores case, so a case-variant title reaches the filter and
# must neither be reopened as the ledger nor hide the closed ledger that does match.
$variantClosed = '{"number":6,"title":"docs FRESHNESS","state":"CLOSED"}'
$variantOpen = '{"number":8,"title":"DOCS FRESHNESS","state":"OPEN"}'
Assert-Equal 0 (Resolve-Ledger "[$variantClosed]") 'a closed case-variant title is not the ledger'
Assert-Equal 1 $reopened.Count 'a closed case-variant title is not reopened'
Assert-Equal 44 (Resolve-Ledger "[$variantOpen,$closed]") 'an open case-variant title does not hide the closed exact match'

# --- an unreadable binding THROWS: a -Comment run with no target is the silent no-op ----
$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("rolling-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $scratch | Out-Null
try {
    # A tree without the binding tool must NOT throw: before this resolver existed, such a
    # tree posted with a hardcoded title and succeeded whenever that title was the default,
    # so throwing would turn a working post into a red step that posts nothing.
    Assert-Equal 0 (Get-RollingIssueNumber -ToolDir $scratch -IssuesJson $issues) `
        'a missing binding tool warns and returns 0 rather than throwing'
    # ...and the escape hatch it names actually works.
    Assert-Equal 11 (Get-RollingIssueNumber -ToolDir $scratch -Title 'Docs drift audit' -IssuesJson $issues) `
        '-Title resolves without the binding tool, as the warning instructs'

    # A python that exits 0 but writes to stderr must not corrupt the title. Under 2>&1 the
    # noise arrives as ErrorRecords; folding it into the title makes it match nothing and the
    # gate silently posts nowhere -- the very failure this resolver exists to remove.
    # The stub tool reads nothing, but the no-binding skip runs before it, so the case runs in
    # a scratch repo with a stub binding: the suite must not depend on the repo it runs from.
    $noisy = Join-Path $scratch 'noisy'
    New-Item -ItemType Directory -Force -Path (Join-Path $noisy '.claude') | Out-Null
    'schema = 1' | Set-Content -LiteralPath (Join-Path $noisy '.claude/ouro.toml') -Encoding utf8
    @'
import sys
print("warning: a notice on stderr", file=sys.stderr)
print("Docs drift audit")
'@ | Set-Content -LiteralPath (Join-Path $noisy 'ouro-binding.py') -Encoding utf8
    Push-Location -LiteralPath $noisy
    try {
        git init -q . 2>$null
        Assert-Equal 11 (Get-RollingIssueNumber -ToolDir $noisy -IssuesJson $issues) `
            'stderr on a successful binding read does not corrupt the resolved title'
    }
    finally { Pop-Location }

    # A real binding tool, but the repo declares no such key.
    # Two candidates again: a vendored tree is flat, and Vendor-Ouro.ps1 now copies the
    # binding tool beside the scripts precisely so these assertions can run there.
    $tool = @((Join-Path $Base 'ouro-binding.py'), (Join-Path $Base 'bin/ouro-binding.py')) |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($tool) {
        # A repo with no binding at all is not a broken read: the resolver says so and returns 0
        # without calling python, so pwsh and git are enough for an unbound repo.
        $unbound = Join-Path $scratch 'unbound'
        New-Item -ItemType Directory -Force -Path $unbound | Out-Null
        Push-Location -LiteralPath $unbound
        try {
            git init -q . 2>$null
            $got = try { @(Get-RollingIssueNumber -ToolDir (Split-Path $tool -Parent) -IssuesJson $issues 6>&1) } catch { @("threw: $_") }
            $said = ($got | Where-Object { $_ -is [System.Management.Automation.InformationRecord] }) -join "`n"
            Assert-Equal '0' (@($got | Where-Object { $_ -isnot [System.Management.Automation.InformationRecord] }) -join "`n") `
                'a repo with no binding returns 0 rather than throwing'
            Assert-Equal $true ($said -match 'INFO - rolling_issues\.drift_audit not read \(no .*ouro\.toml\)') `
                'the skipped binding read is announced'
        }
        finally { Pop-Location }

        $repo = Join-Path $scratch 'repo'
        New-Item -ItemType Directory -Force -Path (Join-Path $repo '.claude') | Out-Null
        Push-Location -LiteralPath $repo
        try {
            git init -q . 2>$null
            @'
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
[rolling_issues]
drift_audit = "Some Other Title"
'@ | Set-Content -LiteralPath (Join-Path $repo '.claude/ouro.toml') -Encoding utf8

            $got = @(Get-RollingIssueNumber -ToolDir (Split-Path $tool -Parent) -IssuesJson $issues 6>&1)
            Assert-Equal 0 (@($got | Where-Object { $_ -isnot [System.Management.Automation.InformationRecord] }) -join "`n") `
                'the title is read from the binding, so a declared-but-absent issue returns 0'
            $saidByKey = ($got | Where-Object { $_ -is [System.Management.Automation.InformationRecord] }) -join "`n"
            Assert-Equal $true ($saidByKey -eq "WARN - no issue titled 'Some Other Title' (rolling_issues.drift_audit), open or closed: a gate's comment has nowhere to go; a sweep that creates the issue continues.") `
                'with no -Title, the title read from the binding, the WARN names the key that resolved it'

            Assert-Throws { Get-RollingIssueNumber -Key 'rolling_issues.docs_freshness' -ToolDir (Split-Path $tool -Parent) -IssuesJson $issues } `
                'no such key' 'an undeclared key throws rather than silently posting nowhere'

            # ouro-binding.py and gh print UTF-8, and PowerShell decodes a native command's stdout
            # with [Console]::OutputEncoding: under code page 437 a non-ASCII title matches no issue
            # and the resolver returns 0. Each read is asserted alone -- the binding read through
            # -IssuesJson, the gh read through -Title and a native gh stub that prints UTF-8 bytes
            # (a .ps1 stub runs in-process and is never decoded) -- and neither may change the
            # caller's encoding.
            $han = "Docs $([char]0x6587) audit"
            $bindingPath = Join-Path $repo '.claude/ouro.toml'
            (Get-Content -LiteralPath $bindingPath -Raw) -replace 'Some Other Title', $han | Set-Content -LiteralPath $bindingPath -Encoding utf8
            $hanJson = '[{"number":77,"title":"' + $han + '","state":"OPEN"}]'
            $stubDir = Join-Path $scratch 'stub'
            New-Item -ItemType Directory -Force -Path $stubDir | Out-Null
            $utf8 = [System.Text.UTF8Encoding]::new($false)
            [System.IO.File]::WriteAllText((Join-Path $stubDir 'issues.json'), $hanJson, $utf8)
            # The stub logs every call's argv: -IssuesJson and -Reopen bypass the live calls, so
            # only the recorded arguments show the search covering every state and the reopen.
            [System.IO.File]::WriteAllText((Join-Path $stubDir 'gh.py'), @'
import json, pathlib, sys
here = pathlib.Path(__file__).parent
args = sys.argv[1:]
with open(here / "argv.jsonl", "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")
if args == ["stub-ping"]:
    print("stub-ok")
elif args[:2] == ["issue", "list"]:
    sys.stdout.buffer.write(here.joinpath("issues.json").read_bytes())
elif args[:2] != ["issue", "reopen"]:
    sys.exit("stub gh: unexpected call " + json.dumps(args))
'@, $utf8)
            if ($IsWindows) {
                $ghStub = Join-Path $stubDir 'gh.cmd'
                [System.IO.File]::WriteAllText($ghStub, "@python3 `"%~dp0gh.py`" %*`r`n", $utf8)
            } else {
                $ghStub = Join-Path $stubDir 'gh'
                [System.IO.File]::WriteAllText($ghStub, "#!/bin/sh`nexec python3 `"`$0.py`" `"`$@`"`n", $utf8)
                chmod +x $ghStub
            }
            $callerEncoding = [Console]::OutputEncoding
            $savedPath = $env:PATH
            try {
                [Console]::OutputEncoding = [System.Text.Encoding]::GetEncoding(437)
                $fromBinding = Get-RollingIssueNumber -ToolDir (Split-Path $tool -Parent) -IssuesJson $hanJson
                $afterBinding = [Console]::OutputEncoding.CodePage
                $env:PATH = $stubDir + [System.IO.Path]::PathSeparator + $env:PATH
                $resolvedGh = (Get-Command gh -CommandType Application | Select-Object -First 1).Source
                $ping = "$(gh stub-ping)".Trim()
                $fromGh = Get-RollingIssueNumber -Title $han
                $afterGh = [Console]::OutputEncoding.CodePage
                $fromReopen = Get-RollingIssueNumber -Title 'Docs freshness' -IssuesJson "[$closed]"
            }
            finally {
                $env:PATH = $savedPath
                [Console]::OutputEncoding = $callerEncoding
            }
            Assert-Equal 77 $fromBinding 'a non-ASCII title read from the binding under code page 437 selects its issue'
            Assert-Equal 437 $afterBinding 'the binding read leaves the caller''s console output encoding unchanged'
            Assert-Equal $ghStub $resolvedGh 'the gh stub shadows gh on PATH'
            Assert-Equal 'stub-ok' $ping 'gh answers a call only the stub knows'
            Assert-Equal 77 $fromGh 'a non-ASCII title matched against gh''s output under code page 437 selects its issue'
            Assert-Equal 437 $afterGh 'the gh read leaves the caller''s console output encoding unchanged'

            $calls = @(Get-Content -LiteralPath (Join-Path $stubDir 'argv.jsonl') -Encoding utf8 |
                    ForEach-Object { , [string[]]@($_ | ConvertFrom-Json) })
            function Get-FlagValue([string[]]$Argv, [string]$Flag) {
                $i = [array]::IndexOf($Argv, $Flag)
                if ($i -ge 0 -and $i + 1 -lt $Argv.Count) { $Argv[$i + 1] } else { $null }
            }
            $lists = @($calls | Where-Object { $_[0] -ceq 'issue' -and $_[1] -ceq 'list' })
            Assert-Equal 1 $lists.Count 'the live search is one gh issue list call'
            if ($lists.Count -eq 1) {
                Assert-Equal 'all' (Get-FlagValue $lists[0] '--state') 'the live search covers every state'
                Assert-Equal 'number,title,state' (Get-FlagValue $lists[0] '--json') 'the live search reads each row''s state'
            }
            $reopens = @($calls | Where-Object { $_[0] -ceq 'issue' -and $_[1] -ceq 'reopen' })
            Assert-Equal 44 $fromReopen 'the default -Reopen returns the closed match it reopened'
            Assert-Equal 1 $reopens.Count 'the default -Reopen is one gh call'
            if ($reopens.Count -eq 1) {
                Assert-Equal '44' $reopens[0][2] 'the default -Reopen reopens the closed match'
                Assert-Equal $true ((Get-FlagValue $reopens[0] '--comment') -match 'machinery, not backlog') `
                    'and comments why in the same gh call'
            }
        }
        finally { Pop-Location }
    }
    else { Write-Host "  skip: ouro-binding.py not beside the resolver (vendored layout)" -ForegroundColor DarkGray }
}
finally {
    if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force }
}

# --- Get-TrustedComments: a comment counts when its author is the bot or an approver ------
# What `gh issue view --json comments` prints. The filter is an exact login compare, case aside:
# a login that holds the bot's or an approver's name as a prefix, a suffix or a substring is a
# stranger, a deleted account has no author, and a kept comment is copied as its own text.
function New-CommentsJson($Rows) {
    $list = foreach ($r in $Rows) {
        $author = if ($r.login) { @{ login = $r.login } } else { $null }
        [ordered]@{ author = $author; body = $r.body; createdAt = '2026-10-03T11:22:33Z'; id = 'IC_1' }
    }
    return (@{ comments = @($list) } | ConvertTo-Json -Depth 5 -Compress)
}
$cases = @(
    @{ login = 'github-actions'; keep = $true; what = 'the bot' }
    @{ login = 'GitHub-Actions'; keep = $true; what = 'the bot with another case' }
    @{ login = 'octo'; keep = $true; what = 'an approver' }
    @{ login = 'OCTO'; keep = $true; what = 'an approver with another case' }
    @{ login = 'cjchin'; keep = $true; what = 'a second approver' }
    @{ login = 'stranger'; keep = $false; what = 'a stranger' }
    @{ login = 'github-actions[bot]'; keep = $false; what = 'the bot with a suffix gh does not print' }
    @{ login = 'github-actions-evil'; keep = $false; what = 'a login that starts with the bot''s' }
    @{ login = 'not-github-actions'; keep = $false; what = 'a login that ends with the bot''s' }
    @{ login = 'octopus'; keep = $false; what = 'a login that starts with an approver''s' }
    @{ login = 'octo '; keep = $false; what = 'an approver''s login with a trailing space' }
    @{ login = ''; keep = $false; what = 'a deleted account, which has no author' }
)
foreach ($c in $cases) {
    $got = Get-TrustedComments -CommentsJson (New-CommentsJson @(@{ login = $c.login; body = 'b' })) -Approvers 'octo', 'cjchin'
    Assert-Equal $(if ($c.keep) { 1 } else { 0 }) $got.Kept "the filter $(if ($c.keep) { 'keeps' } else { 'drops' }) $($c.what)"
}
$mixed = New-CommentsJson @(@{ login = 'stranger'; body = 'one' }, @{ login = 'github-actions'; body = "two`nlines" }, @{ login = ''; body = 'three' }, @{ login = 'octo'; body = "caf$([char]0xE9)" })
$got = Get-TrustedComments -CommentsJson $mixed -Approvers 'octo'
Assert-Equal '4/2/2' "$($got.Total)/$($got.Kept)/$($got.Dropped)" 'the counts are the comments read, kept and dropped'
Assert-Equal "two`nlines|caf$([char]0xE9)" ($got.Bodies -join '|') 'the kept bodies are in order, a multi-line body whole'
Assert-Equal $true $got.Json.Contains('"createdAt":"2026-10-03T11:22:33Z"') 'a kept comment keeps its createdAt as written'
Assert-Equal $false $got.Json.Contains('stranger') 'the Json holds no dropped comment'
Assert-Equal 2 @((ConvertFrom-Json $got.Json).comments).Count 'the Json is {"comments":[...]} of the kept comments'
$none = Get-TrustedComments -CommentsJson '{"comments":[]}' -Approvers 'octo'
Assert-Equal '0/0/0' "$($none.Total)/$($none.Kept)/$($none.Dropped)" 'an issue with no comments keeps and drops nothing'
Assert-Equal '{"comments":[]}' $none.Json 'and its Json is an empty list'
$single = Get-TrustedComments -CommentsJson (New-CommentsJson @(@{ login = 'octo'; body = 'x' })) -Approvers @()
Assert-Equal 0 $single.Kept 'with no approvers only the bot is trusted'
Assert-Throws { Get-TrustedComments -CommentsJson '[]' -Approvers 'octo' } 'not an object with a comments array' 'a JSON list is refused'
Assert-Throws { Get-TrustedComments -CommentsJson '{"comments":{}}' -Approvers 'octo' } 'not an object with a comments array' 'comments that is no list is refused'
Assert-Throws { Get-TrustedComments -CommentsJson 'nonsense' -Approvers 'octo' } '.' 'text that is no JSON is refused'

# The approvers come from the binding unless passed; a read that fails throws, so a filter that
# cannot name its approvers does not fall back to trusting everyone.
$trustTool = New-Item -ItemType Directory -Path (Join-Path ([System.IO.Path]::GetTempPath()) ('trusted-' + [guid]::NewGuid().ToString('N').Substring(0, 8)))
try {
    Set-Content -LiteralPath (Join-Path $trustTool.FullName 'ouro-binding.py') -Value @'
import sys
print('["octo"]' if sys.argv[1:] == ["get", "owner.ruling_approvers"] else "wrong args: %s" % sys.argv[1:])
'@
    $fromBinding = Get-TrustedComments -CommentsJson (New-CommentsJson @(@{ login = 'octo'; body = 'a' }, @{ login = 'cjchin'; body = 'b' })) -ToolDir $trustTool.FullName
    Assert-Equal 'a' ($fromBinding.Bodies -join '|') 'the approvers are read with `get owner.ruling_approvers`, and only those are kept'
    Set-Content -LiteralPath (Join-Path $trustTool.FullName 'ouro-binding.py') -Value @'
import sys
print("no such key", file=sys.stderr)
sys.exit(1)
'@
    Assert-Throws { Get-TrustedComments -CommentsJson (New-CommentsJson @(@{ login = 'stranger'; body = 'a' })) -ToolDir $trustTool.FullName } 'owner.ruling_approvers failed' 'a failed binding read throws'
}
finally { Remove-Item -LiteralPath $trustTool.FullName -Recurse -Force }

# --- neither gate may carry the title as a literal any more -----------------------------
# Two candidates, the same probe the resolver itself is found with above: a vendored tree is
# flat, so a bin/-only lookup silently finds nothing -- and these are the assertions that pin
# the defect, so losing them without a word is worse than not having them.
foreach ($g in @('Test-IssueLabelInvariants.ps1', 'Test-RepoVariablesDoc.ps1')) {
    $p = @((Join-Path $Base $g), (Join-Path $Base "bin/$g")) |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $p) { Write-Host "  skip: $g not found in either layout" -ForegroundColor DarkGray; continue }
    $text = Get-Content -LiteralPath $p -Raw
    Assert-Equal $false ($text -match '"Docs drift audit"') "$g carries no hardcoded rolling-issue title"
    Assert-Equal $true  ($text -match 'Get-RollingIssueNumber') "$g resolves the target through the binding"
    Assert-Equal $true  ($text -match 'RollingIssueTitle') "$g exposes -RollingIssueTitle, the escape hatch the warning names"
}

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall rolling-issue cases pass" -ForegroundColor Green
exit 0
