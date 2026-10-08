<#
.SYNOPSIS
    Unit test for Get-RepoSlug.ps1 and for the repository every gate's gh calls name.
.DESCRIPTION
    The defect this pins: the gates called gh with no repository named, so each call addressed
    whichever repository gh picks from the clone's remotes. Run from a fork's clone -- how the
    gates are wired as [[gate]] entries -- that is the parent, and four of the calls write
    labels and comments.

    So the fixture is fork-shaped: a scratch repo whose `origin` names one repository and whose
    `upstream` names another. Every gate is driven there against a gh function shadow that
    records $env:GH_REPO beside each call, and the assertion is on the recorded repository, not
    on the source of the script.

    The resolution's own cases are here too: the binding outranks the remotes, only `origin` is
    asked about, a URL that gh would read as a repository name is not asked at all,
    and no message repeats the URL, which can carry a credential.

    No case needs gh or the network.
#>
$ErrorActionPreference = 'Stop'

# Two levels up is the plugin root (scripts under bin/) or, once vendored, the scripts dir.
$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
function Find-Script([string]$Name) {
    @((Join-Path $Base $Name), (Join-Path $Base "bin/$Name")) |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
$Resolver = Find-Script 'Get-RepoSlug.ps1'
if (-not $Resolver) { throw "Get-RepoSlug.ps1 not found under $Base" }
$Bin = Split-Path $Resolver -Parent
. $Resolver
. (Find-Script 'Get-RollingIssue.ps1')

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -eq $Actual) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}
function Assert-Match($Pattern, $Text, $What) {
    if ($Text -match $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- no match for '$Pattern' in:`n$Text" -ForegroundColor Red; $script:failures++ }
}
function Assert-NoMatch($Pattern, $Text, $What) {
    if ($Text -notmatch $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- unexpected match for '$Pattern' in:`n$Text" -ForegroundColor Red; $script:failures++ }
}

# The gh shadow: every call is recorded with the repository it named, and answered from the
# fixtures below. A write verb returns nothing, as gh's own writes print no JSON.
$global:ghCalls = @()
$global:ghSlug = 'forkowner/app'
$global:ghRepoViewOk = $true
function gh {
    $argv = $args -join ' '
    $global:ghCalls += [pscustomobject]@{ Argv = $argv; Repo = "$env:GH_REPO" }
    $global:LASTEXITCODE = 0
    if ($argv -match '^repo view') {
        if (-not $global:ghRepoViewOk) { $global:LASTEXITCODE = 1; return "could not resolve to a Repository" }
        return $global:ghSlug
    }
    if ($argv -match '^issue list --label agent-ready') {
        return '[{"number":1,"title":"stale","body":"See `bin/NoSuchFile.ps1`.","labels":[{"name":"agent-ready"}],"comments":[]}]'
    }
    if ($argv -match '^issue list --label needs-ruling') { return '[{"number":5}]' }
    if ($argv -match '^issue list --state open --label (agent-ready|needs-ruling) --limit 500') { return '[]' }
    if ($argv -match '^pr list --state open') { return '[]' }
    if ($argv -match '^issue list --label blocked') { return '[]' }
    # Closed, so the resolver's own write -- the reopen -- is among the calls recorded.
    if ($argv -match '^issue list --search') { return '[{"number":9,"title":"Ledger","state":"CLOSED"}]' }
    if ($argv -match '^issue list --state open --search') {
        return '[{"number":7,"title":"new","body":"b","labels":[],"comments":[],"createdAt":"2026-09-02T00:00:00Z","url":"u"}]'
    }
    if ($argv -match '^issue list --state open --limit') { return '[{"number":3,"title":"no state","labels":[]}]' }
    if ($argv -match '^issue view') {
        return '[{"number":1,"title":"stale","body":"prose","labels":[{"name":"agent-ready"}],"comments":[]}]'
    }
    if ($argv -match '^variable list') { return '[{"name":"FOO","value":"1"}]' }
}

# A repo with the remotes of a fork's clone. -Slug writes a binding naming a third repository;
# -Origin '' leaves the clone with no remote at all.
function New-FixtureRepo {
    param([string]$Origin = 'https://github.com/forkowner/app.git', [string]$Slug = '')
    $dir = Join-Path ([IO.Path]::GetTempPath()) ('ouro-repo-slug-' + [guid]::NewGuid().ToString('n'))
    New-Item -ItemType Directory -Path $dir | Out-Null
    Push-Location -LiteralPath $dir
    git init -q .
    if ($Origin) {
        git remote add origin $Origin
        git remote add upstream https://github.com/parent/app.git
    }
    'nothing documented here' | Set-Content -LiteralPath (Join-Path $dir 'doc.md') -Encoding utf8
    git add -A
    git -c user.name=t -c user.email=t@t -c commit.gpgsign=false commit -q --no-verify -m fixture
    if ($Slug) {
        New-Item -ItemType Directory -Path (Join-Path $dir '.claude') | Out-Null
        @"
schema = 1
[repo]
slug = "$Slug"
default_branch = "master"
[[gate]]
areas = ["*"]
run = "x"
[ship]
policy = "stop-at-pr"
review = "adversarial-review"
[owner]
ruling_approvers = ["me"]
"@ | Set-Content -LiteralPath (Join-Path $dir '.claude/ouro.toml') -Encoding utf8
    }
    Pop-Location
    return $dir
}

# Each case starts with no repository named and no calls recorded: a leftover GH_REPO from the
# case before would pass an assertion the run under test never satisfied.
function Reset-Case {
    $global:ghCalls = @()
    Remove-Item env:GH_REPO -ErrorAction Ignore
}
function Get-IssueCalls { @($global:ghCalls | Where-Object { $_.Argv -notmatch '^repo view' }) }

$made = @()
try {
    # --- the resolution -------------------------------------------------------------------
    $fork = New-FixtureRepo; $made += $fork
    Push-Location -LiteralPath $fork
    try {
        Reset-Case
        $got = Get-RepoSlug -ToolDir $Bin
        Assert-Equal 'forkowner/app' $got.Slug 'with no binding, the slug is the repository origin names'
        Assert-Match 'https://github\.com/forkowner/app\.git' $global:ghCalls[0].Argv 'gh is asked about origin''s URL, not upstream''s'
        Assert-NoMatch 'parent/app' ($global:ghCalls.Argv -join "`n") 'upstream is never asked about'

        # gh answering nothing is not a slug, and the reason repeats no part of the URL: a
        # remote can carry a credential and a gate's output is a build log.
        Reset-Case
        $global:ghRepoViewOk = $false
        $failed = Get-RepoSlug -ToolDir $Bin
        $global:ghRepoViewOk = $true
        Assert-Equal '' $failed.Slug 'a gh that cannot name the repository resolves nothing'
        Assert-Match 'gh could not name the repository' $failed.Why 'the reason says gh could not name it'
        Assert-NoMatch 'github\.com' $failed.Why 'the reason repeats no part of the URL'
    }
    finally { Pop-Location }

    # A binding outranks the remotes: the fork-shaped clone still addresses what it declares.
    if (Test-Path -LiteralPath (Join-Path $Bin 'ouro-binding.py')) {
        $bound = New-FixtureRepo -Slug 'declared/repo'; $made += $bound
        Push-Location -LiteralPath $bound
        try {
            Reset-Case
            $got = Get-RepoSlug -ToolDir $Bin
            Assert-Equal 'declared/repo' $got.Slug 'the binding''s [repo].slug outranks the remotes'
            Assert-Equal 0 @($global:ghCalls).Count 'a binding that reads asks gh nothing'
        }
        finally { Pop-Location }

        # A binding that is there and does not read throws: resolving origin instead would name
        # another repository in a fork's clone, and say nothing about it.
        $broken = New-FixtureRepo -Slug 'declared/repo'; $made += $broken
        "schema = 1`n[repo]`ndefault_branch = `"master`"" |
            Set-Content -LiteralPath (Join-Path $broken '.claude/ouro.toml') -Encoding utf8
        Push-Location -LiteralPath $broken
        try {
            Reset-Case
            $threw = try { $null = Get-RepoSlug -ToolDir $Bin; '' } catch { "$_" }
            Assert-Match 'no such key: repo\.slug' $threw 'a binding that does not read throws rather than falling back to origin'
            Assert-Equal 0 @($global:ghCalls).Count 'a binding that does not read asks gh nothing'
        }
        finally { Pop-Location }
    }
    else { Write-Host '  skip: no ouro-binding.py beside the resolver (vendored without the tool)' -ForegroundColor DarkGray }

    # python3 is the one name Get-RepoSlug calls: a working `python` in its own directory, never
    # python3's, must not let the read through.
    function Get-DirIdentity([string]$Dir) {
        try { (Get-Item -LiteralPath $Dir -ErrorAction Stop).FullName.TrimEnd('\', '/') }
        catch { $Dir.TrimEnd('\', '/') }
    }
    function Get-PathWithoutPython3 {
        $drop = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
        foreach ($c in @(Get-Command python3 -All -ErrorAction SilentlyContinue)) {
            if ($c.Source) { [void]$drop.Add((Get-DirIdentity (Split-Path -Parent $c.Source))) }
        }
        $keep = @()
        foreach ($dir in ($env:PATH -split [System.IO.Path]::PathSeparator)) {
            if ([string]::IsNullOrWhiteSpace($dir)) { continue }
            if ($dir -match 'WindowsApps') { continue }
            if ($drop.Contains((Get-DirIdentity $dir))) { continue }
            $keep += $dir
        }
        ($keep -join [System.IO.Path]::PathSeparator)
    }
    if ((Test-Path -LiteralPath (Join-Path $Bin 'ouro-binding.py')) -and (Get-Command python3 -ErrorAction SilentlyContinue)) {
        $py3 = New-FixtureRepo -Slug 'declared/repo'; $made += $py3
        $realPython3 = (Get-Command python3).Source
        $realGit = (Get-Command git).Source

        # A python3-only PATH: a scratch link (a wrapper, on Windows) to the real interpreter,
        # one to git the same way, and nothing else -- so `python` is not reachable at all and a
        # resolver that shelled out to `python` instead of `python3` could not pass by accident
        # of the caller's own PATH also carrying that name.
        $py3OnlyDir = Join-Path ([IO.Path]::GetTempPath()) ('py3-only-' + [guid]::NewGuid().ToString('n').Substring(0, 8))
        New-Item -ItemType Directory -Path $py3OnlyDir | Out-Null
        try {
            if ($IsWindows) {
                Set-Content -LiteralPath (Join-Path $py3OnlyDir 'python3.cmd') -Encoding ascii -Value "@`"$realPython3`" %*"
                # git's own directory, not a .cmd wrapper: ouro-binding.py resolves the repo root
                # through Python's own subprocess call for "git", which on Windows appends only
                # .exe to an extension-less name -- a .cmd there would not be found.
                $py3OnlyGitDir = Split-Path -Parent $realGit
            }
            else {
                $py3ShimPath = Join-Path $py3OnlyDir 'python3'
                Set-Content -LiteralPath $py3ShimPath -Encoding utf8 -Value "#!/bin/sh`nexec `"$realPython3`" `"`$@`""
                [System.IO.File]::SetUnixFileMode($py3ShimPath, [System.IO.UnixFileMode]'UserRead, UserWrite, UserExecute, GroupRead, GroupExecute, OtherRead, OtherExecute')
                New-Item -ItemType SymbolicLink -Path (Join-Path $py3OnlyDir 'git') -Target $realGit | Out-Null
                $py3OnlyGitDir = $null
            }
            $py3OnlySaved = $env:PATH
            try {
                $env:PATH = if ($py3OnlyGitDir) { $py3OnlyDir + [IO.Path]::PathSeparator + $py3OnlyGitDir } else { $py3OnlyDir }
                Push-Location -LiteralPath $py3
                try {
                    Reset-Case
                    $got = Get-RepoSlug -ToolDir $Bin
                    Assert-Equal 'declared/repo' $got.Slug 'python3-only: the slug resolves'
                }
                finally { Pop-Location }
            }
            finally { $env:PATH = $py3OnlySaved }
        }
        finally { Remove-Item -LiteralPath $py3OnlyDir -Recurse -Force }

        $pyOnlyDir = Join-Path ([IO.Path]::GetTempPath()) ('py-only-shim-' + [guid]::NewGuid().ToString('n').Substring(0, 8))
        New-Item -ItemType Directory -Path $pyOnlyDir | Out-Null
        try {
            if ($IsWindows) {
                Set-Content -LiteralPath (Join-Path $pyOnlyDir 'python.cmd') -Encoding ascii -Value "@`"$realPython3`" %*"
            }
            else {
                $shimPath = Join-Path $pyOnlyDir 'python'
                Set-Content -LiteralPath $shimPath -Encoding utf8 -Value "#!/bin/sh`nexec `"$realPython3`" `"`$@`""
                [System.IO.File]::SetUnixFileMode($shimPath, [System.IO.UnixFileMode]'UserRead, UserWrite, UserExecute, GroupRead, GroupExecute, OtherRead, OtherExecute')
            }
            # git can share python3's own directory (both in /usr/bin on Linux), which the scrub
            # below then drops for free -- a scratch link keeps it reachable so a failure here can
            # only be the python3 the resolver actually calls, never git going missing under it.
            $gitOnlyDir = Join-Path ([IO.Path]::GetTempPath()) ('py-only-git-' + [guid]::NewGuid().ToString('n').Substring(0, 8))
            New-Item -ItemType Directory -Path $gitOnlyDir | Out-Null
            try {
                if ($IsWindows) {
                    Set-Content -LiteralPath (Join-Path $gitOnlyDir 'git.cmd') -Encoding ascii -Value "@`"$realGit`" %*"
                }
                else {
                    New-Item -ItemType SymbolicLink -Path (Join-Path $gitOnlyDir 'git') -Target $realGit | Out-Null
                }
                $pathSaved = $env:PATH
                try {
                    $env:PATH = $pyOnlyDir + [IO.Path]::PathSeparator + $gitOnlyDir + [IO.Path]::PathSeparator + (Get-PathWithoutPython3)
                    $pyWorks = $false
                    try { & python --version *>$null; $pyWorks = ($LASTEXITCODE -eq 0) } catch {}
                    $python3Gone = -not (Get-Command python3 -ErrorAction SilentlyContinue)
                    if ($pyWorks -and $python3Gone) {
                        Push-Location -LiteralPath $py3
                        try {
                            Reset-Case
                            $threw = try { $null = Get-RepoSlug -ToolDir $Bin; '' } catch { "$_" }
                            Assert-Match "'python3' is not recognized" $threw 'python-only: Get-RepoSlug fails naming python3'
                        }
                        finally { Pop-Location }
                    }
                    else { Write-Host '  skip: the python shim is not a usable interpreter on this machine' -ForegroundColor DarkGray }
                }
                finally { $env:PATH = $pathSaved }
            }
            finally { Remove-Item -LiteralPath $gitOnlyDir -Recurse -Force }
        }
        finally { Remove-Item -LiteralPath $pyOnlyDir -Recurse -Force }
    }
    else { Write-Host '  skip: no ouro-binding.py or python3 available to test the python-only PATH' -ForegroundColor DarkGray }

    # Guards: a remote URL gh would read as a repository name is not handed to it.
    foreach ($case in @(
            @{ Url = 'mirror'; Why = 'names no host or path' },
            @{ Url = 'octocat/Hello-World'; Why = 'spelled the way gh spells a repository' },
            @{ Url = 'github.com/octocat/Hello-World'; Why = 'spelled the way gh spells a repository' })) {
        $odd = New-FixtureRepo -Origin $case.Url; $made += $odd
        Push-Location -LiteralPath $odd
        try {
            Reset-Case
            $got = Get-RepoSlug -ToolDir $Bin
            Assert-Equal '' $got.Slug "origin '$($case.Url)' resolves nothing"
            Assert-Match ([regex]::Escape($case.Why)) $got.Why "origin '$($case.Url)': the reason says '$($case.Why)'"
            Assert-Equal 0 @($global:ghCalls).Count "origin '$($case.Url)' is never handed to gh"
        }
        finally { Pop-Location }
    }

    # Neither source: the reason names both halves.
    $bare = New-FixtureRepo -Origin ''; $made += $bare
    Push-Location -LiteralPath $bare
    try {
        Reset-Case
        $got = Get-RepoSlug -ToolDir $Bin
        Assert-Equal '' $got.Slug 'no binding and no origin resolves nothing'
        # Either half: a vendored tree without the binding tool names that instead of the file.
        Assert-Match '(ouro\.toml|ouro-binding\.py)' $got.Why 'the reason names the binding it could not read'
        Assert-Match 'no origin remote' $got.Why 'the reason names the remote it could not read'
    }
    finally { Pop-Location }

    # --- every gate's gh calls name the repository origin names ---------------------------
    # Each gate is run in the fork-shaped clone, with no binding: the acceptance case.
    $runs = @(
        @{ Name = 'Test-AgentReadyAnchors.ps1'; Run = { & (Join-Path $Bin 'Test-AgentReadyAnchors.ps1') -Comment -Demote } },
        @{ Name = 'Test-AgentReadyShape.ps1'; Run = { & (Join-Path $Bin 'Test-AgentReadyShape.ps1') -Issue 1 -Demote -AreaLabels @() -TypeLabels @() } },
        @{ Name = 'Get-RollingIssue.ps1'; Run = { Get-RollingIssueNumber -Title 'Ledger' -ToolDir $Bin } },
        @{ Name = 'Get-IntakeTargets.ps1'; Run = { & (Join-Path $Bin 'Get-IntakeTargets.ps1') -NewSince 2026-09-01 -RollingIssueTitles 'x' } },
        @{ Name = 'Test-IssueLabelInvariants.ps1'; Run = { & (Join-Path $Bin 'Test-IssueLabelInvariants.ps1') -Comment -RollingIssueTitle 'Ledger' } },
        @{ Name = 'Test-RepoVariablesDoc.ps1'; Run = { & (Join-Path $Bin 'Test-RepoVariablesDoc.ps1') -DocPath 'doc.md' -Comment -RollingIssueTitle 'Ledger' } },
        @{ Name = 'Get-FootprintGraph.ps1'; Run = { & (Join-Path $Bin 'Get-FootprintGraph.ps1') } },
        @{ Name = 'Get-CompileProgram.ps1'; Run = { & (Join-Path $Bin 'Get-CompileProgram.ps1') -Graph $emptyGraph -Intent unblock }; ViaR = $true }
    )
    $emptyGraph = Join-Path $fork 'graph.json'
    '{"schema":2,"head":"0123456789abcdef","mirrored":[],"issues":[],"collisions":[],"batches":[],"waves":[],"unplaced":[],"leverage":[]}' | Set-Content -LiteralPath $emptyGraph -Encoding utf8
    Push-Location -LiteralPath $fork
    try {
        foreach ($r in $runs) {
            Reset-Case
            $null = try { & $r.Run 6>&1 } catch { "threw: $_" }
            $calls = Get-IssueCalls
            Assert-Equal $true ($calls.Count -gt 0) "$($r.Name): reaches gh in the fork's clone"
            $wrong = if ($r.ViaR) { @($calls | Where-Object { $_.Argv -notmatch ' -R forkowner/app$' }) } else { @($calls | Where-Object { $_.Repo -ne 'forkowner/app' }) }
            Assert-Equal 0 $wrong.Count "$($r.Name): every gh call names forkowner/app ($(($wrong | ForEach-Object { "$($_.Repo)|$($_.Argv)" }) -join '; '))"
        }
        # The writes are in that set: a gate demoting a label or posting a comment, and the
        # resolver reopening a closed ledger, are the half of the defect that changed another
        # repository's backlog.
        Reset-Case
        $null = try { & (Join-Path $Bin 'Test-AgentReadyShape.ps1') -Issue 1 -Demote -AreaLabels @() -TypeLabels @() 6>&1 } catch { "threw: $_" }
        $null = try { Get-RollingIssueNumber -Title 'Ledger' -ToolDir $Bin 6>&1 } catch { "threw: $_" }
        $writes = @(Get-IssueCalls | Where-Object { $_.Argv -match '^issue (edit|comment|reopen)' })
        Assert-Equal $true ($writes.Count -ge 4) 'the demotion''s two edits and comment and the ledger reopen are recorded'
        Assert-Equal 0 @($writes | Where-Object { $_.Repo -ne 'forkowner/app' }).Count 'every write names forkowner/app'

        # Injected issues are not injected writes: -IssuesJson replaces the reads, and the
        # -Demote edits and comment still go to gh.
        Reset-Case
        $injected = '[{"number":15,"title":"n","body":"prose","labels":[{"name":"agent-ready"}],"comments":[]}]'
        $null = try { & (Join-Path $Bin 'Test-AgentReadyShape.ps1') -IssuesJson $injected -Issue 15 -Demote -AreaLabels @() -TypeLabels @() 6>&1 } catch { "threw: $_" }
        $injWrites = @(Get-IssueCalls)
        Assert-Equal 3 $injWrites.Count 'with -IssuesJson the demotion still makes its two edits and its comment'
        Assert-Equal 0 @($injWrites | Where-Object { $_.Repo -ne 'forkowner/app' }).Count 'every -IssuesJson write names forkowner/app'

        # The same for the resolver: -IssuesJson bypasses the search, never the default -Reopen.
        Reset-Case
        $closed = '[{"number":9,"title":"Ledger","state":"CLOSED"}]'
        $null = try { Get-RollingIssueNumber -Title 'Ledger' -ToolDir $Bin -IssuesJson $closed 6>&1 } catch { "threw: $_" }
        $reopens = @(Get-IssueCalls | Where-Object { $_.Argv -match '^issue reopen' })
        Assert-Equal 1 $reopens.Count 'with -IssuesJson the closed ledger is still reopened through gh'
        Assert-Equal 'forkowner/app' "$($reopens[0].Repo)" 'the -IssuesJson reopen names forkowner/app'

        # A caller's own GH_REPO is replaced, not honoured, and stays replaced: the binding and
        # origin are the two sources.
        Reset-Case
        $env:GH_REPO = 'someone/else'
        $null = try { & (Join-Path $Bin 'Test-AgentReadyAnchors.ps1') 6>&1 } catch { "threw: $_" }
        Assert-Equal 0 @(Get-IssueCalls | Where-Object { $_.Repo -ne 'forkowner/app' }).Count 'a pre-set GH_REPO is replaced, not honoured'
        Assert-Equal 'forkowner/app' "$env:GH_REPO" 'the replacement outlives the run'
        Reset-Case
        $env:GH_REPO = 'someone/else'
        $fgCalls = @(try { & (Join-Path $Bin 'Get-FootprintGraph.ps1') 6>&1 } catch { "threw: $_" })
        $fgIssue = @(Get-IssueCalls | Where-Object { $_.Argv -match '^issue list' })
        Assert-Equal 2 $fgIssue.Count 'Get-FootprintGraph.ps1 makes its two issue reads under a pre-set GH_REPO'
        Assert-Equal 0 @($fgIssue | Where-Object { $_.Repo -ne 'forkowner/app' }).Count 'and a caller''s own GH_REPO is replaced there too'
    }
    finally { Pop-Location }

    # --- naming no repository: an annotated skip, and no gh call --------------------------
    # The skips are ::warning:: annotations, not INFO: exit 0 with an INFO line reads in a CI log
    # exactly like a gate that ran.
    Push-Location -LiteralPath $bare
    try {
        Reset-Case
        $out = (& (Join-Path $Bin 'Test-AgentReadyAnchors.ps1') -Comment -Demote -Doc 'doc.md' 6>&1 | Out-String)
        Assert-Match '::warning::anchor gate: no issue read or written' $out 'the anchor gate annotates the skip'
        Assert-Equal 0 (Get-IssueCalls).Count 'the anchor gate makes no gh call'
        Assert-Match 'doc\.md: OK' $out 'the -Doc checks still run'
        Assert-Equal 0 $LASTEXITCODE 'the anchor gate exits 0 on the skip'

        Reset-Case
        $out = (& (Join-Path $Bin 'Test-AgentReadyShape.ps1') -Issue 1 -Demote -AreaLabels @() -TypeLabels @() 6>&1 | Out-String)
        Assert-Match '::warning::shape gate: no issue checked' $out 'the shape gate annotates the skip'
        Assert-Equal 0 (Get-IssueCalls).Count 'the shape gate makes no gh call'
        Assert-Equal 0 $LASTEXITCODE 'the shape gate exits 0 on the skip'

        # Injected reads do not exempt a run that can write.
        Reset-Case
        $out = (& (Join-Path $Bin 'Test-AgentReadyShape.ps1') -IssuesJson $injected -Issue 15 -Demote -AreaLabels @() -TypeLabels @() 6>&1 | Out-String)
        Assert-Match '::warning::shape gate: no issue checked' $out 'a -Demote run on injected issues annotates the skip too'
        Assert-Equal 0 (Get-IssueCalls).Count 'that run makes no gh call'

        # 0 is "not filed yet", which a caller answers by creating the issue: this must not be 0.
        Reset-Case
        $threw = try { $null = Get-RollingIssueNumber -Title 'Ledger' -ToolDir $Bin 6>&1; '' } catch { "$_" }
        Assert-Match 'no repository to address' $threw 'the rolling resolver throws rather than returning 0'
        Assert-Equal 0 (Get-IssueCalls).Count 'the rolling resolver makes no gh call'

        Reset-Case
        $threw = try { $null = Get-RollingIssueNumber -Title 'Ledger' -ToolDir $Bin -IssuesJson $closed 6>&1; '' } catch { "$_" }
        Assert-Match 'no repository to address' $threw 'the injected closed ledger throws rather than reopening unnamed'
        Assert-Equal 0 (Get-IssueCalls).Count 'the injected reopen makes no gh call'

        Reset-Case
        $threw = try { & (Join-Path $Bin 'Get-IntakeTargets.ps1') -NewSince 2026-09-01 -RollingIssueTitles 'x' | Out-Null; '' } catch { "$_" }
        Assert-Match 'the repository to select from is not named' $threw 'the intake selector throws rather than selecting'
        Assert-Equal 0 (Get-IssueCalls).Count 'the intake selector makes no gh call'

        Reset-Case
        $out = (& (Join-Path $Bin 'Test-IssueLabelInvariants.ps1') -Comment -RollingIssueTitle 'Ledger' 6>&1 | Out-String)
        Assert-Match '::warning::state-label gate: no issue read' $out 'the label-invariant gate annotates the skip'
        Assert-Equal 0 (Get-IssueCalls).Count 'the label-invariant gate makes no gh call'

        Reset-Case
        $out = (& (Join-Path $Bin 'Test-RepoVariablesDoc.ps1') -DocPath 'doc.md' -Comment -RollingIssueTitle 'Ledger' 6>&1 | Out-String)
        Assert-Match '::warning::repo-variable gate: no repo variable read' $out 'the repo-variable gate annotates the skip'
        Assert-Equal 0 (Get-IssueCalls).Count 'the repo-variable gate makes no gh call'
    }
    finally { Pop-Location }
}
finally {
    Remove-Item function:gh -ErrorAction Ignore
    Remove-Variable -Name ghCalls, ghSlug, ghRepoViewOk -Scope Global -ErrorAction Ignore
    Remove-Item env:GH_REPO -ErrorAction Ignore
    foreach ($d in $made) { Remove-Item -LiteralPath $d -Recurse -Force }
}

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall repo-slug cases pass" -ForegroundColor Green
exit 0
