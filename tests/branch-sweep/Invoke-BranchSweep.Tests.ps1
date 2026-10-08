<#
.SYNOPSIS
    End-to-end test of Invoke-BranchSweep.ps1 against scratch repositories.
.DESCRIPTION
    Each case builds a scratch repository in TEMP whose origin is a local bare repository, and
    runs the sweep as a subprocess from its main checkout, the way a person runs it. gh is a
    stub on PATH answering `pr list --head <branch>` and `pr list --base <branch>` from a
    per-case table in the real command's output contract -- a JSON array, `[]` for none -- and
    logging every call, so no case touches a real remote or needs a credential. Git's global and
    system configuration are replaced by an empty file for the whole suite.

    Pinned: a squash-merged branch that `git branch --merged` does not list is finished by its PR;
    so is a local branch whose remote is gone, a merged-PR branch left on origin, and a worktree
    whose PR closed. Skipped, each with its reason: the default branch, the main checkout's branch,
    a branch checked out in a worktree outside .claude/worktrees/, a protected prefix, an open PR,
    no PR, commits beyond the PR's head, a worktree with uncommitted changes, a locked one (with
    and without a reason), a prunable one (its directory deleted by hand, which is no failure),
    an origin branch another open PR is based on, and a PR read that fails or comes back
    truncated -- which also makes the run exit 1.
    Report mode leaves every ref, on both sides, and every directory as it found them; -Delete
    removes exactly the finished set; an orphan directory, and a worktree with no branch checked
    out, are reported and left. A worktree whose removal hits a lock is reported "left on disk",
    checked on the filesystem, while `git worktree list` no longer shows it; a worktree holding
    the current directory is not removed. Every gh call names the binding's repository, and
    every head query asks for up to 1000 PRs. A caller's exported GIT_DIR, GIT_WORK_TREE or
    GIT_COMMON_DIR naming a second repository moves neither mode: that repository's refs, its
    worktree list and its worktrees directory are what they were, and no gh call asks about its
    branches, while the caller gets its own three back -- the ones it never set still unset --
    whichever way the run ends: each of its exits, and a fetch that throws. On Linux $null and ''
    both unset like [NullString]::Value, so an unset through $null, or a restore without its
    guard, passes there; the Windows leg's behaviour rows catch both swaps.

    A binding that fails `ouro-binding.py check` -- protected_branch_prefixes misspelled -- exits
    1 with no ref changed, no fetch and no gh call. A branch deleted on origin after the clone's
    last fetch is not reported: the sweep fetches. A head query answering 1000 rows is
    unreadable. Packed branches with loose twins spelled with other case -- one at a commit of
    its own, one at its twin's -- are both skipped where the filesystem folds case (Windows,
    macOS), and are two refs each elsewhere; either way every loose twin's commit stays
    referenced by it. Names that differ only by a zero-width space, or by NFC against NFD, are
    never twins; where the filesystem does not fold case each is reported and handled apart.
    Ignored files go with their worktree: one holding only an ignored bin/__pycache__/, and one
    holding five ignored paths, are finished and then deleted, each line listing every ignored
    path, while one with an untracked file beside them stays, and one whose ignored scratch/
    holds a nested repository with a commit stays in both modes, repository and all. A worktree
    git refuses to remove -- one with an initialized submodule -- is failed, still listed, and
    never "left on disk". A worktree skipped for commits beyond its PR's head does not stop the
    run: a finished branch sorting after it is still reported, and removed.

    Every comparison of names or lines is ordinal, as git compares refs: -ceq compares by culture
    and would read a zero-width-joined name as its plain twin. The console decodes child output
    as UTF-8, as the sweep prints it.

    In one case only, a git wrapper first on PATH moves a branch just before three of the
    sweep's calls: origin's before its ls-remote re-read (the branch is then skipped as beyond
    its PR's head), origin's before its push --delete (the lease refuses: failed, the branch
    still on origin), and a local one before its rev-parse re-read (failed, the branch still at
    the commit it moved to); the run exits 1.

    The lock is a process whose current directory is inside the worktree on Windows, and a
    worktrees directory without write permission elsewhere; as root, where permissions do not
    stop a removal, that case prints a skip line.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Two levels up is the plugin root (suite under tests/<area>/) or, once vendored, the scripts dir.
$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Script = @((Join-Path $Base 'Invoke-BranchSweep.ps1'), (Join-Path $Base 'bin/Invoke-BranchSweep.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Script) { throw "Invoke-BranchSweep.ps1 not found under $Base" }

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ([string]::Equals("$Expected", "$Actual", [StringComparison]::Ordinal)) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}
# The run printed exactly this line, or (with -Prefix) a line starting with it.
function Assert-Line($Run, [string]$Line, [switch]$Prefix) {
    $hit = @($Run.Lines | Where-Object {
            if ($Prefix) { $_.StartsWith($Line, [StringComparison]::Ordinal) }
            else { [string]::Equals($_, $Line, [StringComparison]::Ordinal) } })
    Assert-Equal $true ($hit.Count -gt 0) "the run prints '$Line$(if ($Prefix) { '...' })'"
}

$Pwsh = (Get-Process -Id $PID).Path
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ('branch-sweep-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$stubDir = Join-Path $scratch 'stub'
New-Item -ItemType Directory -Force -Path $stubDir | Out-Null
$stub = @'
# gh stub: `pr list --head <branch>` answers from the table in STUB_GH_TABLE -- {branch: {Exit, Out}}
# -- and `pr list --base <branch>` from its `base:<branch>` row; every call is logged. Anything the
# table does not name fails, so no query goes unseen.
$a = @($args)
[System.IO.File]::AppendAllText($env:STUB_GH_LOG, ($a -join ' ') + "`n")
$table = Get-Content -LiteralPath $env:STUB_GH_TABLE -Raw | ConvertFrom-Json -AsHashtable
$i = [array]::IndexOf($a, '--head')
$j = [array]::IndexOf($a, '--base')
$key = if ($i -ge 0) { $a[$i + 1] } elseif ($j -ge 0) { 'base:' + $a[$j + 1] }
if ("$($a[0]) $($a[1])" -eq 'pr list' -and $key -and $table.ContainsKey($key)) {
    $row = $table[$key]
    $row.Out
    exit $row.Exit
}
"stub gh: no answer for: $($a -join ' ')"
exit 1
'@
[System.IO.File]::WriteAllText((Join-Path $stubDir 'gh.ps1'), $stub, $Utf8)
# The race case alone puts this git first on PATH, ahead of the gh stub's directory.
$gitStubDir = Join-Path $scratch 'git-stub'
New-Item -ItemType Directory -Force -Path $gitStubDir | Out-Null
$gitStub = @'
# git wrapper: every call goes through to the real git (STUB_GIT_REAL), but first, when the call's
# verb and one of its arguments match a row in STUB_GIT_RACE -- [{Verb, Ref, Sha, Repo}] -- it
# moves that ref in that repository to that commit: a branch changing under the sweep.
$a = @($args)
foreach ($r in @(Get-Content -LiteralPath $env:STUB_GIT_RACE -Raw | ConvertFrom-Json)) {
    if ($a[0] -ceq $r.Verb -and $a -ccontains $r.Ref) {
        $null = & $env:STUB_GIT_REAL -C $r.Repo update-ref $r.Ref $r.Sha
    }
}
& $env:STUB_GIT_REAL @a
exit $LASTEXITCODE
'@
[System.IO.File]::WriteAllText((Join-Path $gitStubDir 'git.ps1'), $gitStub, $Utf8)
$RealGit = (Get-Command git -CommandType Application | Select-Object -First 1).Source
# A caller that runs the sweep in its own process, the way a script calls another rather than
# spawning it, and reads the three git variables once it returns. `exit` inside a called script
# ends that script alone and its code is read back here; a git read that throws ends it as a
# terminating error, which reaches the caller and would end this script too -- caught, so the
# lines below print whichever way the run ended, and a caught run reports 1.
$CallerScript = Join-Path $scratch 'caller.ps1'
$callerBody = @'
$code = 1
try { & $env:STUB_SWEEP @args; $code = $LASTEXITCODE }
catch { Write-Output "caught: $(@($_.Exception.Message -split "`n")[0])" }
foreach ($n in 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR') {
    $v = [Environment]::GetEnvironmentVariable($n)
    $what = if ($null -eq $v) { 'unset' } else { "'$v'" }
    Write-Output "after: $n is $what"
}
exit $code
'@
[System.IO.File]::WriteAllText($CallerScript, $callerBody, $Utf8)

$Binding = @'
schema = 1
[repo]
slug = "o/n"
default_branch = "master"
protected_branch_prefixes = ["release/"]
[ship]
policy = "stop-at-pr"
review = "none"
[owner]
ruling_approvers = ["o"]
'@

# $null unsets: [Environment]::SetEnvironmentVariable given $null from PowerShell sets ''.
function Set-EnvVar([string]$Name, $Value) {
    if ($null -eq $Value) { Remove-Item -LiteralPath "Env:$Name" -ErrorAction Ignore }
    else { [Environment]::SetEnvironmentVariable($Name, $Value) }
}
function G {
    $dir, $rest = $args
    $out = git -C $dir @rest 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git -C $dir $($rest -join ' ') failed: $out" }
    @($out | Where-Object { $_ -is [string] })
}
function New-Commit([string]$Dir, [string]$File) {
    [System.IO.File]::WriteAllText((Join-Path $Dir $File), "$File $([guid]::NewGuid())`n", $Utf8)
    $null = G $Dir add -- $File
    $null = G $Dir commit -q -m "change $File"
    @(G $Dir rev-parse HEAD)[0]
}
# A main checkout on master with the binding committed, and a bare origin holding master.
function New-World([string]$Name) {
    $root = Join-Path $scratch $Name
    # The table is case-sensitive, as branch names are.
    $w = @{ Origin = Join-Path $root 'origin.git'; Main = Join-Path $root 'main'
        Table = [System.Collections.Specialized.OrderedDictionary]::new([StringComparer]::Ordinal) }
    $null = git init -q --bare -b master $w.Origin 2>&1
    $null = git init -q -b master $w.Main 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git init in $root failed" }
    New-Item -ItemType Directory -Force -Path (Join-Path $w.Main '.claude') | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $w.Main '.claude/ouro.toml'), $Binding, $Utf8)
    $null = G $w.Main add -A
    $null = G $w.Main commit -q -m init
    $null = G $w.Main remote add origin $w.Origin
    $null = G $w.Main push -q origin master
    return $w
}
# One row in the real `gh pr list --json headRefOid,number,state` shape: keys sorted, as gh prints.
function New-PrRow([int]$Number, [string]$State, [string]$Head) {
    [ordered]@{ headRefOid = $Head; number = $Number; state = $State }
}
function Set-Answer($World, [string]$Branch, [object[]]$Rows) {
    $World.Table[$Branch] = @{ Exit = 0; Out = (ConvertTo-Json -InputObject @($Rows) -Compress -Depth 5) }
}
# The open PRs based on $Branch, as `gh pr list --base <b> --state open --json number` prints them.
function Set-BaseAnswer($World, [string]$Branch, [int[]]$Open = @()) {
    $rows = @($Open | ForEach-Object { [ordered]@{ number = $_ } })
    $World.Table["base:$Branch"] = @{ Exit = 0; Out = (ConvertTo-Json -InputObject $rows -Compress) }
}
function Invoke-Sweep {
    param($World, [string[]]$Arguments = @(), [string]$From = $World.Main, [string]$GitRace,
        [hashtable]$Environment = @{}, [string]$Caller)
    $tableFile = Join-Path $scratch "table-$([guid]::NewGuid().ToString('N')).json"
    [System.IO.File]::WriteAllText($tableFile, (ConvertTo-Json -InputObject $World.Table -Depth 5), $Utf8)
    $log = Join-Path $scratch "gh-$([guid]::NewGuid().ToString('N')).log"
    [System.IO.File]::WriteAllText($log, '', $Utf8)
    $vars = @{ PATH = $stubDir + [System.IO.Path]::PathSeparator + $env:PATH
        STUB_GH_TABLE = $tableFile; STUB_GH_LOG = $log }
    if ($GitRace) {
        $vars.PATH = $gitStubDir + [System.IO.Path]::PathSeparator + $vars.PATH
        $vars.STUB_GIT_RACE = $GitRace; $vars.STUB_GIT_REAL = $RealGit
    }
    # -Caller runs the sweep from a script inside the child process rather than as the child
    # process; -Environment is what the caller exported around it.
    if ($Caller) { $vars.STUB_SWEEP = $Script }
    foreach ($k in $Environment.Keys) { $vars[$k] = $Environment[$k] }
    $target = if ($Caller) { $Caller } else { $Script }
    $saved = @{}
    foreach ($k in $vars.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); Set-EnvVar $k $vars[$k] }
    Push-Location -LiteralPath $From
    try {
        $lines = @(& $Pwsh -NoProfile -File $target @Arguments 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    }
    finally {
        Pop-Location
        foreach ($k in $saved.Keys) { Set-EnvVar $k $saved[$k] }
    }
    $lines | ForEach-Object { Write-Host "    | $_" -ForegroundColor DarkGray }
    [pscustomobject]@{ Code = $code; Lines = $lines; Gh = @(Get-Content -LiteralPath $log) }
}
function Get-Heads([string]$Dir) { (@(G $Dir for-each-ref '--format=%(refname:lstrip=2)' refs/heads) | Sort-Object) -join ',' }
# Every entry below $Dir, hidden ones included, and the walk stops at a link to a directory.
# System.IO, not Get-ChildItem: on Linux, -LiteralPath still globs a '*' or '?' in the directory's
# own path, and would enumerate a sibling.
function Get-Entries([string]$Dir) {
    foreach ($p in [System.IO.Directory]::GetFileSystemEntries($Dir)) {
        $item = if ([System.IO.Directory]::Exists($p)) { [System.IO.DirectoryInfo]::new($p) } else { [System.IO.FileInfo]::new($p) }
        $item
        if ($item -is [System.IO.DirectoryInfo] -and -not $item.LinkTarget) { Get-Entries $p }
    }
}
# Every ref on both sides with its commit, the worktree list, and every path under the worktrees
# directory: what report mode must leave exactly as it found it. One line per item, so a
# comparison names what changed.
function Get-State($World) {
    $wtDir = Join-Path $World.Main '.claude/worktrees'
    @(G $World.Main for-each-ref '--format=%(refname) %(objectname)' | ForEach-Object { "main $_" }
        G $World.Origin for-each-ref '--format=%(refname) %(objectname)' | ForEach-Object { "origin $_" }
        G $World.Main worktree list --porcelain | Where-Object { $_ } | ForEach-Object { "list $_" }
        if (Test-Path -LiteralPath $wtDir) {
            Get-Entries $wtDir | ForEach-Object { "path $($_.FullName.Substring($wtDir.Length))" }
        })
}
function Get-Changes($Before, $After) {
    @(Compare-Object $Before $After | ForEach-Object { "$($_.SideIndicator) $($_.InputObject)" }) -join '; '
}

$savedGit = @{}
foreach ($k in 'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_NOSYSTEM', 'GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL',
    'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL') { $savedGit[$k] = [Environment]::GetEnvironmentVariable($k) }
$held = $null
$savedEncoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = $Utf8
    $emptyConfig = Join-Path $scratch 'gitconfig'
    [System.IO.File]::WriteAllText($emptyConfig, '', $Utf8)
    Set-EnvVar GIT_CONFIG_GLOBAL $emptyConfig; Set-EnvVar GIT_CONFIG_NOSYSTEM '1'
    Set-EnvVar GIT_AUTHOR_NAME t; Set-EnvVar GIT_AUTHOR_EMAIL t@t
    Set-EnvVar GIT_COMMITTER_NAME t; Set-EnvVar GIT_COMMITTER_EMAIL t@t

    # The stub is only a stub if PowerShell picks it over the real gh.
    $savedPath = $env:PATH
    $env:PATH = $stubDir + [System.IO.Path]::PathSeparator + $env:PATH
    try { $resolvedGh = (Get-Command gh | Select-Object -First 1).Source } finally { $env:PATH = $savedPath }
    if ($resolvedGh -ne (Join-Path $stubDir 'gh.ps1')) { throw "the gh stub does not shadow gh on PATH (resolved '$resolvedGh')" }
    $env:PATH = $gitStubDir + [System.IO.Path]::PathSeparator + $stubDir + [System.IO.Path]::PathSeparator + $env:PATH
    try { $resolvedGit = (Get-Command git | Select-Object -First 1).Source } finally { $env:PATH = $savedPath }
    if ($resolvedGit -ne (Join-Path $gitStubDir 'git.ps1')) { throw "the git wrapper does not shadow git on PATH (resolved '$resolvedGit')" }

    # ── one repository holding every kind of branch ─────────────────────────────────────────
    Write-Host 'finished and skipped branches, report mode then -Delete'
    $w = New-World 'w1'; $m = $w.Main
    # Squash-merged on GitHub, which then deleted the branch: the local branch's remote is gone.
    $null = G $m checkout -q -b feat/squashed master
    $null = New-Commit $m 'sq1.txt'; $sqHead = New-Commit $m 'sq2.txt'
    $null = G $m push -q -u origin feat/squashed
    $null = G $m checkout -q master
    $null = G $m merge -q --squash feat/squashed
    $null = G $m commit -q -m 'squashed (#1)'
    $null = G $m push -q origin master
    $null = G $w.Origin update-ref -d refs/heads/feat/squashed
    Set-Answer $w 'feat/squashed' @(New-PrRow 1 MERGED $sqHead)
    # Merged, and left on origin; no local branch.
    $null = G $m checkout -q -b feat/on-origin master
    $onOriginHead = New-Commit $m 'on-origin.txt'
    $null = G $m push -q origin feat/on-origin
    $null = G $m checkout -q master
    $null = G $m branch -q -D feat/on-origin
    Set-Answer $w 'feat/on-origin' @(New-PrRow 2 MERGED $onOriginHead)
    Set-BaseAnswer $w 'feat/on-origin'
    # Merged and left on origin, with another open PR based on it: deleting it would close that PR.
    $null = G $m checkout -q -b feat/stacked-base master
    $stackedHead = New-Commit $m 'stacked.txt'
    $null = G $m push -q origin feat/stacked-base
    $null = G $m checkout -q master
    $null = G $m branch -q -D feat/stacked-base
    Set-Answer $w 'feat/stacked-base' @(New-PrRow 14 MERGED $stackedHead)
    Set-BaseAnswer $w 'feat/stacked-base' @(15)
    # A worktree whose PR was closed unmerged: its commits stay on GitHub under the PR.
    $staleDir = Join-Path $m '.claude/worktrees/stale-wt'
    $null = G $m worktree add -q -b feat/stale-wt .claude/worktrees/stale-wt master
    $staleHead = New-Commit $staleDir 'stale.txt'
    $null = G $staleDir push -q origin feat/stale-wt
    Set-Answer $w 'feat/stale-wt' @(New-PrRow 3 CLOSED $staleHead)
    Set-BaseAnswer $w 'feat/stale-wt'
    # Protected, locally and on origin: never asked about.
    $null = G $m branch release/1.0 master
    $null = G $m push -q origin release/1.0
    Set-Answer $w 'release/1.0' @(New-PrRow 13 MERGED @(G $m rev-parse master)[0])
    # An open PR beside an older closed one: the open one decides.
    $null = G $m checkout -q -b feat/open master
    $openHead = New-Commit $m 'open.txt'
    $null = G $m push -q origin feat/open
    Set-Answer $w 'feat/open' @((New-PrRow 5 OPEN $openHead), (New-PrRow 4 CLOSED $openHead))
    # Never had a PR.
    $null = G $m checkout -q -b feat/no-pr master
    $null = New-Commit $m 'no-pr.txt'
    Set-Answer $w 'feat/no-pr' @()
    # Merged at the pushed head, then a local commit the PR never carried.
    $null = G $m checkout -q -b feat/beyond master
    $beyondHead = New-Commit $m 'beyond.txt'
    $null = G $m push -q -u origin feat/beyond
    $null = New-Commit $m 'beyond-local.txt'
    Set-Answer $w 'feat/beyond' @(New-PrRow 7 MERGED $beyondHead)
    Set-BaseAnswer $w 'feat/beyond'
    # Merged at a head pushed from elsewhere; the local branch is one commit behind it.
    $null = G $m checkout -q -b feat/behind master
    $null = New-Commit $m 'behind1.txt'; $behindHead = New-Commit $m 'behind2.txt'
    $null = G $m push -q origin feat/behind
    $null = G $m reset -q --hard HEAD~1
    Set-Answer $w 'feat/behind' @(New-PrRow 8 MERGED $behindHead)
    Set-BaseAnswer $w 'feat/behind'
    # A merged PR whose head commit this clone never saw: only equality could count.
    $null = G $m checkout -q -b feat/unknown-head master
    $null = New-Commit $m 'unknown.txt'
    Set-Answer $w 'feat/unknown-head' @(New-PrRow 11 MERGED ('ab' * 20))
    # A merged PR's worktree with an untracked file in it.
    $dirtyDir = Join-Path $m '.claude/worktrees/dirty-wt'
    $null = G $m worktree add -q -b feat/dirty-wt .claude/worktrees/dirty-wt master
    $dirtyHead = New-Commit $dirtyDir 'dirty.txt'
    [System.IO.File]::WriteAllText((Join-Path $dirtyDir 'notes.txt'), 'unsaved', $Utf8)
    Set-Answer $w 'feat/dirty-wt' @(New-PrRow 9 MERGED $dirtyHead)
    # A directory under the worktrees directory that git does not list.
    $orphanDir = Join-Path $m '.claude/worktrees/orphan'
    New-Item -ItemType Directory -Force -Path $orphanDir | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $orphanDir 'kept.txt'), 'kept', $Utf8)
    # A worktree with no branch: nothing to ask a PR about.
    $null = G $m worktree add -q --detach .claude/worktrees/detached master
    # Merged PRs' worktrees locked with `git worktree lock`, with a reason and without one.
    foreach ($c in ('locked-wt', 17), ('locked-bare', 18)) {
        $n, $pr = $c
        $null = G $m worktree add -q -b "feat/$n" ".claude/worktrees/$n" master
        $head = New-Commit (Join-Path $m ".claude/worktrees/$n") "$n.txt"
        Set-Answer $w "feat/$n" @(New-PrRow $pr MERGED $head)
    }
    $null = G $m worktree lock --reason 'on a removable disk' .claude/worktrees/locked-wt
    $null = G $m worktree lock .claude/worktrees/locked-bare
    # A merged PR's branch checked out in a worktree outside .claude/worktrees/, and on origin.
    $elsewhereDir = Join-Path $scratch 'w1/elsewhere'
    $null = G $m worktree add -q -b feat/elsewhere $elsewhereDir master
    $elsewhereHead = New-Commit $elsewhereDir 'elsewhere.txt'
    $null = G $elsewhereDir push -q origin feat/elsewhere
    Set-Answer $w 'feat/elsewhere' @(New-PrRow 19 MERGED $elsewhereHead)
    Set-BaseAnswer $w 'feat/elsewhere'
    # A merged PR's worktree whose directory was deleted by hand: git lists it as prunable.
    $goneDir = Join-Path $m '.claude/worktrees/gone-wt'
    $null = G $m worktree add -q -b feat/gone-wt .claude/worktrees/gone-wt master
    Set-Answer $w 'feat/gone-wt' @(New-PrRow 20 MERGED (New-Commit $goneDir 'gone.txt'))
    Remove-Item -LiteralPath $goneDir -Recurse -Force
    # The main checkout ends on a merged PR's branch.
    $null = G $m checkout -q -b feat/here master
    Set-Answer $w 'feat/here' @(New-PrRow 12 MERGED (New-Commit $m 'here.txt'))
    $null = G $m fetch -q --prune origin

    $merged = @(G $m branch --merged master '--format=%(refname:short)')
    $elsewherePath = @(G $m worktree list --porcelain | Where-Object { $_ -like '* *elsewhere' } |
        ForEach-Object { [System.IO.Path]::GetFullPath($_.Substring(9)) })[0]
    Assert-Equal 1 @(G $m worktree list --porcelain | Where-Object { $_ -like 'prunable*' }).Count 'git lists the worktree whose directory is gone as prunable'
    Assert-Equal $false ($merged -contains 'feat/squashed') 'git branch --merged does not list the squash-merged branch'

    $before = Get-State $w
    $run = Invoke-Sweep $w
    Assert-Equal 0 $run.Code 'report mode exits 0'
    Assert-Equal '[gone]' @(G $m for-each-ref '--format=%(upstream:track)' refs/heads/feat/squashed)[0] 'feat/squashed tracks a remote that is gone'
    Assert-Line $run 'finished: local feat/squashed (PR #1 MERGED)'
    Assert-Line $run 'finished: remote feat/on-origin (PR #2 MERGED)'
    Assert-Line $run 'finished: worktree .claude/worktrees/stale-wt (feat/stale-wt, PR #3 CLOSED)'
    Assert-Line $run 'finished: local feat/stale-wt (PR #3 CLOSED)'
    Assert-Line $run 'finished: remote feat/stale-wt (PR #3 CLOSED)'
    Assert-Line $run 'finished: local feat/behind (PR #8 MERGED)'
    Assert-Line $run 'finished: remote feat/behind (PR #8 MERGED)'
    Assert-Line $run 'finished: remote feat/beyond (PR #7 MERGED)'
    Assert-Line $run "skip: local feat/beyond - commits beyond PR #7's head"
    Assert-Line $run "skip: local feat/unknown-head - commits beyond PR #11's head"
    Assert-Line $run 'skip: local release/1.0 - protected prefix release/'
    Assert-Line $run 'skip: remote release/1.0 - protected prefix release/'
    Assert-Line $run 'skip: local feat/open - open PR #5'
    Assert-Line $run 'skip: remote feat/open - open PR #5'
    Assert-Line $run 'skip: local feat/no-pr - no PR'
    Assert-Line $run 'skip: worktree .claude/worktrees/dirty-wt - uncommitted changes in .claude/worktrees/dirty-wt'
    Assert-Line $run 'skip: local feat/dirty-wt - uncommitted changes in .claude/worktrees/dirty-wt'
    Assert-Line $run 'skip: local feat/here - checked out in the main checkout'
    Assert-Line $run 'skip: local master - default branch'
    Assert-Line $run 'skip: remote master - default branch'
    Assert-Line $run 'orphan: .claude/worktrees/orphan - on disk, not a worktree git lists; left in place'
    Assert-Line $run 'skip: worktree .claude/worktrees/detached - no branch checked out'
    Assert-Line $run 'skip: remote feat/stacked-base - open PR #15 is based on it'
    foreach ($t in 'worktree .claude/worktrees/locked-wt', 'local feat/locked-wt') { Assert-Line $run "skip: $t - locked: on a removable disk" }
    foreach ($t in 'worktree .claude/worktrees/locked-bare', 'local feat/locked-bare') { Assert-Line $run "skip: $t - locked: no reason given" }
    foreach ($t in 'local', 'remote') { Assert-Line $run "skip: $t feat/elsewhere - checked out at $elsewherePath" }
    foreach ($t in 'worktree .claude/worktrees/gone-wt', 'local feat/gone-wt') {
        Assert-Line $run "skip: $t - prunable: its directory is gone - run git worktree prune"
    }
    Assert-Equal 8 @($run.Lines -like 'finished: *').Count 'report mode names eight finished items and no more'
    Assert-Line $run 'report only: 8 finished, nothing deleted; -Delete removes them'
    Assert-Equal '' (Get-Changes $before (Get-State $w)) 'report mode leaves every ref, both sides, the worktree list and every file as it found them'
    Assert-Equal 0 @($run.Gh | Where-Object { $_ -notmatch '(^| )-R o/n( |$)' }).Count 'every gh call names the binding''s repository with -R'
    Assert-Equal 0 @($run.Gh | Where-Object { $_ -match '--head (master|release/1\.0|feat/here|feat/elsewhere)( |$)' }).Count 'the default, protected, main-checkout and checked-out-elsewhere branches are never looked up'
    $heads = @($run.Gh | Where-Object { $_ -match '(^| )--head ' })
    Assert-Equal $true ($heads.Count -gt 0) 'the sweep looked up at least one head'
    Assert-Equal 0 @($heads | Where-Object { $_ -notmatch '(^| )--limit 1000( |$)' }).Count 'every head query asks for up to 1000 PRs, past gh''s default of 30'

    $run = Invoke-Sweep $w @('-Delete')
    Assert-Equal 0 $run.Code '-Delete exits 0'
    Assert-Line $run 'deleted: worktree .claude/worktrees/stale-wt (feat/stale-wt, PR #3 CLOSED)'
    Assert-Line $run 'deleted: local feat/stale-wt (PR #3 CLOSED)'
    Assert-Line $run 'deleted: remote feat/stale-wt (PR #3 CLOSED)'
    Assert-Line $run 'deleted: local feat/squashed (PR #1 MERGED)'
    Assert-Line $run 'deleted: remote feat/on-origin (PR #2 MERGED)'
    Assert-Line $run 'deleted: local feat/behind (PR #8 MERGED)'
    Assert-Line $run 'deleted: remote feat/behind (PR #8 MERGED)'
    Assert-Line $run 'deleted: remote feat/beyond (PR #7 MERGED)'
    Assert-Line $run "skip: local feat/beyond - commits beyond PR #7's head"
    Assert-Equal 8 @($run.Lines -like 'deleted: *').Count '-Delete reports eight removals and no more'
    Assert-Equal 'feat/beyond,feat/dirty-wt,feat/elsewhere,feat/gone-wt,feat/here,feat/locked-bare,feat/locked-wt,feat/no-pr,feat/open,feat/unknown-head,master,release/1.0' (Get-Heads $m) '-Delete leaves exactly the skipped local branches'
    Assert-Line $run 'skip: remote feat/stacked-base - open PR #15 is based on it'
    Assert-Equal 'feat/elsewhere,feat/open,feat/stacked-base,master,release/1.0' (Get-Heads $w.Origin) '-Delete leaves exactly the skipped branches on origin'
    Assert-Equal $false (Test-Path -LiteralPath $staleDir) 'the finished worktree is gone from the filesystem'
    Assert-Equal $true (Test-Path -LiteralPath (Join-Path $dirtyDir 'notes.txt')) 'the dirty worktree and its untracked file stay'
    Assert-Equal $true (Test-Path -LiteralPath (Join-Path $orphanDir 'kept.txt')) 'the orphan directory and its file stay'
    Assert-Equal $true (Test-Path -LiteralPath (Join-Path $m '.claude/worktrees/detached/.claude/ouro.toml')) 'the worktree with no branch stays'
    $listed = @(G $m worktree list --porcelain | Where-Object { $_ -like 'worktree *' } |
        ForEach-Object { Split-Path -Leaf $_.Substring(9) })
    foreach ($n in 'locked-wt', 'locked-bare', 'elsewhere', 'gone-wt') { Assert-Equal $true ($listed -contains $n) "the $n worktree stays registered" }
    Assert-Equal $true (Test-Path -LiteralPath (Join-Path $elsewhereDir 'elsewhere.txt')) 'the worktree outside .claude/worktrees/ stays whole'

    # ── a PR read that fails, and one that comes back cut off ───────────────────────────────
    Write-Host 'an unreadable PR state never reads as finished'
    $w = New-World 'w2'; $m = $w.Main
    $tips = @{}
    foreach ($b in 'feat/gh-fails', 'feat/gh-truncated') {
        $null = G $m checkout -q -b $b master
        $tips[$b] = New-Commit $m "$($b -replace '/', '-').txt"
        $null = G $m push -q origin $b
        $null = G $m checkout -q master
    }
    $w.Table['feat/gh-fails'] = @{ Exit = 1; Out = 'HTTP 502: Bad Gateway (https://api.github.com/graphql)' }
    # Would read as MERGED at the tip, if a reply missing its closing bracket were taken whole.
    $w.Table['feat/gh-truncated'] = @{ Exit = 0; Out = "[{`"headRefOid`":`"$($tips['feat/gh-truncated'])`",`"number`":21,`"state`":`"MERGED`"}" }
    # On origin only, merged at its tip; the read of the PRs based on it fails.
    $null = G $m checkout -q -b feat/base-fails master
    $baseFailsHead = New-Commit $m 'base-fails.txt'
    $null = G $m push -q origin feat/base-fails
    $null = G $m checkout -q master
    $null = G $m branch -q -D feat/base-fails
    Set-Answer $w 'feat/base-fails' @(New-PrRow 22 MERGED $baseFailsHead)
    $w.Table['base:feat/base-fails'] = @{ Exit = 1; Out = 'HTTP 502: Bad Gateway (https://api.github.com/graphql)' }
    # 1000 PRs under one head name, all closed at its tip: the 1000 read may not be all of them.
    $null = G $m checkout -q -b feat/many master
    $manyHead = New-Commit $m 'many.txt'
    $null = G $m checkout -q master
    Set-Answer $w 'feat/many' @(1..1000 | ForEach-Object { New-PrRow $_ CLOSED $manyHead })
    foreach ($label in 'report mode', '-Delete') {
        $run = Invoke-Sweep $w @(if ($label -eq '-Delete') { '-Delete' })
        Assert-Equal 1 $run.Code "$label exits 1 when a PR state is unreadable"
        Assert-Line $run 'skip: local feat/gh-fails - PR state unreadable: exit 1: HTTP 502' -Prefix
        Assert-Line $run 'skip: remote feat/gh-fails - PR state unreadable: exit 1: HTTP 502' -Prefix
        Assert-Line $run 'skip: local feat/gh-truncated - PR state unreadable: not a JSON array of PRs' -Prefix
        Assert-Line $run 'skip: remote feat/base-fails - PR state unreadable: exit 1: HTTP 502' -Prefix
        Assert-Line $run 'skip: local feat/many - PR state unreadable: more PRs than the 1000 read'
        Assert-Equal 0 @($run.Lines -match '^(finished|deleted): ').Count "$label calls nothing finished"
        Assert-Line $run '4 failure(s): see the lines above'
    }
    Assert-Equal 'feat/gh-fails,feat/gh-truncated,feat/many,master' (Get-Heads $m) 'the three branches stay local after -Delete'
    Assert-Equal 'feat/base-fails,feat/gh-fails,feat/gh-truncated,master' (Get-Heads $w.Origin) 'all three branches stay on origin after -Delete'

    # ── a worktree whose removal hits a lock ────────────────────────────────────────────────
    Write-Host 'a removal that hits a lock is verified on the filesystem'
    $w = New-World 'w3'; $m = $w.Main
    $heldDir = Join-Path $m '.claude/worktrees/held'
    $null = G $m worktree add -q -b feat/held .claude/worktrees/held master
    $heldHead = New-Commit $heldDir 'held.txt'
    $null = G $heldDir push -q origin feat/held
    Set-Answer $w 'feat/held' @(New-PrRow 30 MERGED $heldHead)
    $lockable = $true
    if ($IsWindows) {
        $held = Start-Process -FilePath $Pwsh -ArgumentList '-NoProfile', '-Command', 'Start-Sleep 300' `
            -WorkingDirectory $heldDir -WindowStyle Hidden -PassThru
        Start-Sleep -Seconds 2
    }
    elseif ((id -u) -eq '0') { $lockable = $false }
    else { chmod a-w (Join-Path $m '.claude/worktrees') }
    if ($lockable) {
        $run = Invoke-Sweep $w @('-Delete')
        if ($held) { Stop-Process -Id $held.Id -Force; $held.WaitForExit(); $held = $null }
        else { chmod u+w (Join-Path $m '.claude/worktrees') }
        Assert-Equal 1 $run.Code 'a worktree left on disk makes -Delete exit 1'
        Assert-Line $run 'left on disk: .claude/worktrees/held - ' -Prefix
        Assert-Equal $true (Test-Path -LiteralPath $heldDir) 'the directory is still on disk'
        Assert-Equal 0 @(G $m worktree list --porcelain | Where-Object { $_ -like '*held*' }).Count 'git worktree list no longer shows it: only the filesystem can'
        Assert-Line $run 'skip: local feat/held - its worktree was not removed'
        Assert-Line $run 'skip: remote feat/held - an earlier step failed'
        Assert-Equal 'feat/held,master' (Get-Heads $m) 'the branch stays local'
        Assert-Equal 'feat/held,master' (Get-Heads $w.Origin) 'the branch stays on origin'
    }
    else { Write-Host '  skip: running as root, a worktrees directory without write permission is no lock' -ForegroundColor DarkGray }

    # ── the worktree the sweep runs from ────────────────────────────────────────────────────
    Write-Host 'the worktree holding the current directory stays'
    $w = New-World 'w4'; $m = $w.Main
    $hereDir = Join-Path $m '.claude/worktrees/here'
    $null = G $m worktree add -q -b feat/cwd .claude/worktrees/here master
    $cwdHead = New-Commit $hereDir 'cwd.txt'
    $null = G $hereDir push -q origin feat/cwd
    Set-Answer $w 'feat/cwd' @(New-PrRow 40 MERGED $cwdHead)
    Set-BaseAnswer $w 'feat/cwd'
    $run = Invoke-Sweep $w @('-Delete') -From $hereDir
    Assert-Equal 0 $run.Code '-Delete from inside a finished worktree exits 0'
    Assert-Line $run 'skip: worktree .claude/worktrees/here - .claude/worktrees/here holds the current directory'
    Assert-Line $run 'skip: local feat/cwd - .claude/worktrees/here holds the current directory'
    Assert-Equal $true (Test-Path -LiteralPath (Join-Path $hereDir 'cwd.txt')) 'the worktree stays whole'
    Assert-Equal 'feat/cwd,master' (Get-Heads $m) 'its branch stays'

    # ── a caller's exported git variables ───────────────────────────────────────────────────
    # git reads GIT_DIR before it looks at a path, and -C does not win over it: under a GIT_DIR
    # naming another repository, the candidates, the worktrees and the refs are that repository's,
    # while `rev-parse --show-toplevel` still answers the working directory, so the guard that
    # keeps the worktree holding it can never match. GIT_WORK_TREE and GIT_COMMON_DIR each send
    # at least one of those reads to another repository too, so each of them appears in the rows
    # below. Any command that exports one of them reaches the sweep: `git submodule foreach` sets
    # GIT_DIR per submodule.
    Write-Host 'an exported GIT_DIR, GIT_WORK_TREE or GIT_COMMON_DIR never moves the run'
    $other = New-World 'w16-other'; $om = $other.Main
    $null = G $om checkout -q -b feat/other master
    $otherTip = New-Commit $om 'other.txt'
    $null = G $om push -q origin feat/other
    $null = G $om worktree add -q -b feat/other-wt .claude/worktrees/other-wt master
    $otherWtTip = New-Commit (Join-Path $om '.claude/worktrees/other-wt') 'other-wt.txt'
    $null = G $om checkout -q master
    $otherState = Get-State $other
    foreach ($vars in @(@{ GIT_DIR = (Join-Path $om '.git') },
            @{ GIT_DIR = (Join-Path $om '.git'); GIT_WORK_TREE = $om },
            @{ GIT_COMMON_DIR = (Join-Path $om '.git') })) {
        $names = @($vars.Keys) | Sort-Object
        $what = $names -join ' and '
        $w = New-World "w16-$($names -join '-')"; $m = $w.Main
        $null = G $m checkout -q -b feat/done master
        $doneTip = New-Commit $m 'done.txt'
        $null = G $m push -q origin feat/done
        $null = G $m checkout -q master
        $null = G $m worktree add -q -b feat/done-wt .claude/worktrees/done-wt master
        $doneWtTip = New-Commit (Join-Path $m '.claude/worktrees/done-wt') 'done-wt.txt'
        Set-Answer $w 'feat/done' @(New-PrRow 900 MERGED $doneTip)
        Set-BaseAnswer $w 'feat/done'
        Set-Answer $w 'feat/done-wt' @(New-PrRow 901 MERGED $doneWtTip)
        Set-BaseAnswer $w 'feat/done-wt'
        # This repository's table answers for the other repository's branch names too: the PR
        # states are read under this binding's slug, so a run that took its candidates from there
        # would read them as finished and remove them.
        Set-Answer $w 'feat/other' @(New-PrRow 902 MERGED $otherTip)
        Set-BaseAnswer $w 'feat/other'
        Set-Answer $w 'feat/other-wt' @(New-PrRow 903 MERGED $otherWtTip)
        Set-BaseAnswer $w 'feat/other-wt'

        # The sweep's own fetch writes origin/HEAD the first time: done here, so the comparison
        # below is about what the run changed.
        $null = G $m fetch -q --prune origin
        $before = Get-State $w
        $run = Invoke-Sweep $w -Environment $vars
        Assert-Equal 0 $run.Code "report mode with $what exported exits 0"
        Assert-Line $run 'finished: local feat/done (PR #900 MERGED)'
        Assert-Line $run 'finished: remote feat/done (PR #900 MERGED)'
        Assert-Line $run 'finished: worktree .claude/worktrees/done-wt (feat/done-wt, PR #901 MERGED)'
        Assert-Equal 0 @($run.Lines | Where-Object { $_ -match 'feat/other' }).Count 'and names nothing of the other repository'
        Assert-Equal 0 @($run.Gh | Where-Object { $_ -match '(^| )--(head|base) feat/other' }).Count 'no gh call asks about its branches'
        Assert-Equal '' (Get-Changes $before (Get-State $w)) 'report mode leaves this repository as it found it'
        Assert-Equal '' (Get-Changes $otherState (Get-State $other)) 'and the other repository too'

        $run = Invoke-Sweep $w @('-Delete') -Environment $vars
        Assert-Equal 0 $run.Code "-Delete with $what exported exits 0"
        Assert-Line $run 'deleted: local feat/done (PR #900 MERGED)'
        Assert-Line $run 'deleted: remote feat/done (PR #900 MERGED)'
        Assert-Line $run 'deleted: worktree .claude/worktrees/done-wt (feat/done-wt, PR #901 MERGED)'
        Assert-Line $run 'deleted: local feat/done-wt (PR #901 MERGED)'
        Assert-Equal 0 @($run.Lines | Where-Object { $_ -match 'feat/other' }).Count '-Delete names nothing of the other repository'
        Assert-Equal 0 @($run.Gh | Where-Object { $_ -match '(^| )--(head|base) feat/other' }).Count 'and asks nothing about its branches'
        Assert-Equal 'master' (Get-Heads $m) '-Delete removes this repository''s finished branches'
        Assert-Equal 'master' (Get-Heads $w.Origin) 'on origin too'
        Assert-Equal '' (Get-Changes $otherState (Get-State $other)) 'and the other repository is untouched: refs, worktree list, worktrees directory'
    }
    # The unset goes through [NullString]::Value, and the restore puts a variable back only where
    # the caller had it set. On Linux $null and '' both unset, so either swap passes there; on
    # Windows each leaves an empty string, and the Windows leg's behaviour rows catch both: an unset
    # through $null turns nearly every row red, an unguarded restore the 'is unset' rows below.

    # The caller's own three, put back: the sweep runs in the caller's process when a script calls
    # it rather than spawning it. Pinned below: its three exits -- the binding check's, the
    # failure count's, the last line's -- and a git read that throws, each of which goes through
    # the finally. A caller with none set must not gain an empty one: an empty GIT_DIR is not
    # "unset" on Windows, and git refuses a real root under it -- `fatal: not a git repository: ''`.
    Write-Host 'the caller keeps the variables it had, whichever way the run ends'
    $w = New-World 'w17'; $m = $w.Main
    $null = G $m checkout -q -b feat/kept master
    $keptTip = New-Commit $m 'kept.txt'
    $null = G $m checkout -q master
    Set-Answer $w 'feat/kept' @(New-PrRow 950 MERGED $keptTip)
    Set-BaseAnswer $w 'feat/kept'
    $callerDir = Join-Path $om '.git'
    # The fetch is the first git read of the run, and Invoke-Read throws on a failed one: an
    # origin that is no repository ends the sweep as a terminating error rather than an exit, and
    # the caller catches it. It comes before the broken binding, which would stop the run earlier.
    foreach ($case in @(@{ Code = 0; What = 'a run that finds a finished branch' },
            @{ Code = 1; What = 'a run whose PR read fails'; Table = @{ Exit = 1; Out = 'boom' } },
            @{ Code = 1; What = 'a run whose fetch throws'; Origin = $true
                Caught = 'caught: git fetch --prune --quiet origin failed' },
            @{ Code = 1; What = 'a run whose binding check fails'; Binding = $true })) {
        if ($case.ContainsKey('Table')) { $w.Table['feat/kept'] = $case.Table }
        if ($case.ContainsKey('Origin')) {
            $null = G $m remote set-url origin (Join-Path $scratch 'no-such-origin.git')
        }
        if ($case.ContainsKey('Binding')) {
            [System.IO.File]::WriteAllText((Join-Path $m '.claude/ouro.toml'),
                $Binding.Replace('protected_branch_prefixes', 'protected_branch_prefix'), $Utf8)
        }
        $run = Invoke-Sweep $w -Caller $CallerScript -Environment @{ GIT_DIR = $callerDir }
        Assert-Equal $case.Code $run.Code "$($case.What) ends at $($case.Code) through the caller"
        if ($case.ContainsKey('Caught')) { Assert-Line $run $case.Caught -Prefix }
        else {
            Assert-Equal 0 @($run.Lines | Where-Object { $_ -like 'caught:*' }).Count `
                'with nothing for the caller to catch: an exit ends the called script alone'
        }
        Assert-Line $run "after: GIT_DIR is '$callerDir'"
        Assert-Line $run 'after: GIT_WORK_TREE is unset'
        Assert-Line $run 'after: GIT_COMMON_DIR is unset'
    }

    # ── origin changed after the clone's last fetch ─────────────────────────────────────────
    Write-Host 'the sweep fetches: a branch deleted on origin since the last fetch is not reported'
    $w = New-World 'w5'; $m = $w.Main
    $null = G $m checkout -q -b feat/pruned master
    $prunedHead = New-Commit $m 'pruned.txt'
    $null = G $m push -q origin feat/pruned
    $null = G $m checkout -q master
    $null = G $m branch -q -D feat/pruned
    Set-Answer $w 'feat/pruned' @(New-PrRow 50 MERGED $prunedHead)
    Set-BaseAnswer $w 'feat/pruned'
    # Merged and deleted on origin, and no fetch since: the clone still tracks it.
    $null = G $w.Origin update-ref -d refs/heads/feat/pruned
    $tracked = { @(G $m for-each-ref refs/remotes/origin/feat/pruned).Count -eq 1 }
    Assert-Equal $true (& $tracked) 'before the run the clone still tracks the branch origin deleted'
    $run = Invoke-Sweep $w
    Assert-Equal 0 $run.Code 'report mode exits 0'
    Assert-Equal 0 @($run.Lines -match 'feat/pruned').Count 'a branch deleted on origin since the last fetch is not reported'
    Assert-Equal $false (& $tracked) 'the sweep''s fetch pruned the stale tracking ref'

    # ── local names that differ only in case ────────────────────────────────────────────────
    Write-Host 'local branch names that differ only in case'
    $w = New-World 'w6'; $m = $w.Main
    $caseHeads = @{}
    foreach ($c in ('feat/case', 60), ('feat/same', 61)) {
        $b, $pr = $c
        $null = G $m checkout -q -b $b master
        $caseHeads[$b] = New-Commit $m "$($b -replace '/', '-').txt"
        $null = G $m checkout -q master
        Set-Answer $w $b @(New-PrRow $pr MERGED $caseHeads[$b])
    }
    $null = G $m pack-refs --all
    # Loose twins with no PR: feat/Case at a commit of its own, feat/Same at feat/same's. Where the
    # filesystem folds case, each twin's file answers for the packed name too.
    $looseHead = @(G $m commit-tree 'master^{tree}' -p master -m 'loose, no PR')[0]
    $null = G $m update-ref refs/heads/feat/Case $looseHead
    $null = G $m update-ref refs/heads/feat/Same $caseHeads['feat/same']
    Set-Answer $w 'feat/Case' @(); Set-Answer $w 'feat/Same' @()
    $folds = $IsWindows -or $IsMacOS
    foreach ($label in 'report mode', '-Delete') {
        $run = Invoke-Sweep $w @(if ($label -eq '-Delete') { '-Delete' })
        Assert-Equal 0 $run.Code "$label exits 0"
        if ($folds) {
            $pairs = ('feat/case', 'feat/Case'), ('feat/Case', 'feat/case'),
                ('feat/same', 'feat/Same'), ('feat/Same', 'feat/same')
            foreach ($pair in $pairs) {
                Assert-Line $run "skip: local $($pair[0]) - another local branch differs only in case: $($pair[1])"
            }
            Assert-Equal 0 @($run.Gh | Where-Object { $_ -match '--head feat/(case|Case|same|Same)( |$)' }).Count "$label never looks up a name with a twin"
        }
        else {
            $verb = if ($label -eq '-Delete') { 'deleted' } else { 'finished' }
            Assert-Line $run "${verb}: local feat/case (PR #60 MERGED)"
            Assert-Line $run "${verb}: local feat/same (PR #61 MERGED)"
            Assert-Line $run 'skip: local feat/Case - no PR'
            Assert-Line $run 'skip: local feat/Same - no PR'
        }
    }
    Assert-Equal $true (@(G $m for-each-ref --points-at $looseHead '--format=%(refname)' refs/heads) -contains 'refs/heads/feat/Case') '-Delete leaves feat/Case at its own commit'
    Assert-Equal $true (@(G $m for-each-ref --points-at $caseHeads['feat/same'] '--format=%(refname)' refs/heads) -contains 'refs/heads/feat/Same') '-Delete leaves feat/Same at the commit it shares with feat/same'

    # ── a binding that fails its check ──────────────────────────────────────────────────────
    Write-Host 'a binding that fails its check stops the run before any fetch or gh call'
    $w = New-World 'w7'; $m = $w.Main
    # Protected, merged at its tip, on both sides: only the prefix keeps it.
    $null = G $m checkout -q -b release/2.0 master
    $releaseHead = New-Commit $m 'release.txt'
    $null = G $m push -q origin release/2.0
    $null = G $m checkout -q master
    Set-Answer $w 'release/2.0' @(New-PrRow 70 MERGED $releaseHead)
    Set-BaseAnswer $w 'release/2.0'
    # Deleted on origin since the last fetch: a fetch would prune the tracking ref.
    $null = G $m push -q origin master:refs/heads/feat/fetch-probe
    $null = G $w.Origin update-ref -d refs/heads/feat/fetch-probe
    $misspelt = $Binding.Replace('protected_branch_prefixes', 'protected_branch_prefix')
    [System.IO.File]::WriteAllText((Join-Path $m '.claude/ouro.toml'), $misspelt, $Utf8)
    $before = Get-State $w
    $run = Invoke-Sweep $w @('-Delete')
    Assert-Equal 1 $run.Code 'a binding that fails its check exits 1'
    Assert-Line $run 'unknown key: repo.protected_branch_prefix'
    Assert-Equal '' (Get-Changes $before (Get-State $w)) 'no ref, on either side, changes: not even a fetch ran'
    Assert-Equal 0 $run.Gh.Count 'no gh call is made'

    # ── origin moving under the sweep ───────────────────────────────────────────────────────
    Write-Host 'origin moving after the check: the re-read skips it, the lease refuses it'
    $w = New-World 'w8'; $m = $w.Main
    $race = @()
    $moved = @{}
    $races = ('feat/race-early', 80, 'ls-remote'), ('feat/race-late', 81, 'push'),
        ('feat/race-local', 82, 'rev-parse')
    foreach ($c in $races) {
        $b, $pr, $verb = $c
        $null = G $m checkout -q -b $b master
        $head = New-Commit $m "$($b -replace '/', '-').txt"
        # A commit the PR never carried: origin's under a ref the sweep does not fetch.
        $moved[$b] = New-Commit $m "$($b -replace '/', '-')-next.txt"
        $null = G $m checkout -q master
        Set-Answer $w $b @(New-PrRow $pr MERGED $head)
        $repo = $m
        if ($verb -ne 'rev-parse') {
            $null = G $m push -q origin "${head}:refs/heads/$b" "$($moved[$b]):refs/keep/$b"
            $null = G $m branch -q -D $b
            Set-BaseAnswer $w $b
            $repo = $w.Origin
        }
        else { $null = G $m branch -q -f $b $head }
        # The race: the branch moves just before this call of the sweep's.
        $race += [ordered]@{ Verb = $verb; Ref = "refs/heads/$b"; Sha = $moved[$b]; Repo = $repo }
    }
    $raceFile = Join-Path $scratch 'race.json'
    [System.IO.File]::WriteAllText($raceFile, (ConvertTo-Json -InputObject $race -Depth 5), $Utf8)
    $run = Invoke-Sweep $w @('-Delete') -GitRace $raceFile
    Assert-Equal 1 $run.Code 'a delete the lease refuses makes -Delete exit 1'
    Assert-Line $run "skip: remote feat/race-early - commits beyond PR #80's head"
    Assert-Line $run 'failed: remote feat/race-late - ' -Prefix
    Assert-Line $run "failed: local feat/race-local - refs/heads/feat/race-local answers at '$($moved['feat/race-local'])', not the checked " -Prefix
    Assert-Equal $moved['feat/race-local'] (@(G $m for-each-ref '--format=%(objectname)' refs/heads/feat/race-local) -join ',') 'feat/race-local stays, at the commit it moved to'
    Assert-Line $run '2 failure(s): see the lines above'
    foreach ($b in 'feat/race-early', 'feat/race-late') {
        Assert-Equal $moved[$b] (@(G $w.Origin for-each-ref '--format=%(objectname)' "refs/heads/$b") -join ',') "$b stays on origin, at the commit it moved to"
    }

    # ── ignored files go with the worktree; a nested repository keeps it ────────────────────
    Write-Host 'ignored files go with their worktree, unless one is a nested repository'
    $w = New-World 'w9'; $m = $w.Main
    # Merged PRs' worktrees, each with a tracked bin/: one holding only what the gates leave (an
    # ignored bin/__pycache__/), one with an untracked file beside it, one with five ignored paths,
    # and one whose ignored scratch/ holds a repository with a commit of its own.
    foreach ($c in ('py-wt', 90), ('mix-wt', 91), ('many-wt', 92), ('nested-wt', 93)) {
        $n, $pr = $c
        $dir = Join-Path $m ".claude/worktrees/$n"
        $null = G $m worktree add -q -b "feat/$n" ".claude/worktrees/$n" master
        $ignore = "__pycache__/`n.env`n*.local`nvendor/`nscratch/`n"
        [System.IO.File]::WriteAllText((Join-Path $dir '.gitignore'), $ignore, $Utf8)
        New-Item -ItemType Directory -Force -Path (Join-Path $dir 'bin') | Out-Null
        [System.IO.File]::WriteAllText((Join-Path $dir 'bin/tool.py'), "print('tool')`n", $Utf8)
        $null = G $dir add .gitignore bin/tool.py
        $null = G $dir commit -q -m 'a tracked bin/, and local files ignored'
        Set-Answer $w "feat/$n" @(New-PrRow $pr MERGED @(G $dir rev-parse HEAD)[0])
        if ($n -ne 'nested-wt') {
            New-Item -ItemType Directory -Force -Path (Join-Path $dir 'bin/__pycache__') | Out-Null
            $pyc = Join-Path $dir 'bin/__pycache__/ouro-binding.cpython-312.pyc'
            [System.IO.File]::WriteAllText($pyc, 'pyc', $Utf8)
        }
    }
    $pyDir = Join-Path $m '.claude/worktrees/py-wt'
    $mixDir = Join-Path $m '.claude/worktrees/mix-wt'
    $manyDir = Join-Path $m '.claude/worktrees/many-wt'
    $nestedDir = Join-Path $m '.claude/worktrees/nested-wt'
    [System.IO.File]::WriteAllText((Join-Path $mixDir 'notes.txt'), 'unsaved', $Utf8)
    foreach ($f in '.env', 'a.local', 'b.local') {
        [System.IO.File]::WriteAllText((Join-Path $manyDir $f), 'local only', $Utf8)
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $manyDir 'vendor') | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $manyDir 'vendor/lib.js'), 'lib', $Utf8)
    $nestedRepo = Join-Path $nestedDir 'scratch'
    $null = git init -q -b master $nestedRepo 2>&1
    $nestedHead = New-Commit $nestedRepo 'experiment.txt'
    $py = '.claude/worktrees/py-wt (feat/py-wt, PR #90 MERGED, with ignored files: bin/__pycache__/)'
    $many = '.claude/worktrees/many-wt (feat/many-wt, PR #92 MERGED, with ignored files: .env, a.local, b.local, bin/__pycache__/, vendor/)'
    $mixSkip = 'uncommitted changes in .claude/worktrees/mix-wt'
    $nestedSkip = 'nested git repository in ignored scratch/ in .claude/worktrees/nested-wt'
    foreach ($label in 'report mode', '-Delete') {
        $run = Invoke-Sweep $w @(if ($label -eq '-Delete') { '-Delete' })
        Assert-Equal 0 $run.Code "$label exits 0"
        $verb = if ($label -eq '-Delete') { 'deleted' } else { 'finished' }
        Assert-Line $run "${verb}: worktree $py"
        Assert-Line $run "${verb}: local feat/py-wt (PR #90 MERGED)"
        Assert-Line $run "${verb}: worktree $many"
        Assert-Line $run "skip: worktree .claude/worktrees/mix-wt - $mixSkip"
        foreach ($t in 'worktree .claude/worktrees/nested-wt', 'local feat/nested-wt') { Assert-Line $run "skip: $t - $nestedSkip" }
        if ($label -eq 'report mode') {
            Assert-Equal $true (Test-Path -LiteralPath (Join-Path $pyDir 'bin/__pycache__')) 'report mode removes nothing'
        }
    }
    Assert-Equal $false (Test-Path -LiteralPath $pyDir) 'the worktree holding only __pycache__ is gone by default'
    Assert-Equal $false (Test-Path -LiteralPath $manyDir) 'the worktree with five ignored paths is gone, every one of them'
    Assert-Equal $true (Test-Path -LiteralPath (Join-Path $mixDir 'notes.txt')) 'uncommitted changes keep a worktree'
    $nestedNow = if (Test-Path -LiteralPath (Join-Path $nestedRepo '.git')) {
        @(G $nestedRepo rev-parse HEAD) -join ','
    }
    Assert-Equal $nestedHead $nestedNow 'the nested repository and its commit survive -Delete'
    Assert-Equal 'feat/mix-wt,feat/nested-wt,master' (Get-Heads $m) 'only the kept worktrees keep their branches'

    # ── a worktree skipped for commits beyond its PR, and a finished branch after it ────────
    Write-Host 'a worktree with commits beyond its PR is skipped, and the run goes on'
    $w = New-World 'w11'; $m = $w.Main
    $aDir = Join-Path $m '.claude/worktrees/a-beyond'
    $null = G $m worktree add -q -b feat/a-beyond .claude/worktrees/a-beyond master
    Set-Answer $w 'feat/a-beyond' @(New-PrRow 110 MERGED (New-Commit $aDir 'a.txt'))
    $null = New-Commit $aDir 'a-local.txt'
    # Sorts after feat/a-beyond: the run must still reach it.
    $null = G $m checkout -q -b feat/b-done master
    Set-Answer $w 'feat/b-done' @(New-PrRow 111 MERGED (New-Commit $m 'b.txt'))
    $null = G $m checkout -q master
    foreach ($label in 'report mode', '-Delete') {
        $run = Invoke-Sweep $w @(if ($label -eq '-Delete') { '-Delete' })
        Assert-Equal 0 $run.Code "$label exits 0"
        foreach ($t in 'worktree .claude/worktrees/a-beyond', 'local feat/a-beyond') { Assert-Line $run "skip: $t - commits beyond PR #110's head" }
        Assert-Line $run "$(if ($label -eq '-Delete') { 'deleted' } else { 'finished' }): local feat/b-done (PR #111 MERGED)"
    }
    Assert-Equal 'feat/a-beyond,master' (Get-Heads $m) 'the later branch is removed and the skipped one stays'
    Assert-Equal $true (Test-Path -LiteralPath (Join-Path $aDir 'a-local.txt')) 'the skipped worktree stays whole'

    # ── names that differ by an invisible or a composed character ───────────────────────────
    Write-Host 'branch names compare ordinal: a zero-width space or a decomposed accent counts'
    $w = New-World 'w12'; $m = $w.Main
    $zw = "feat/x$([char]0x200B)y"; $plain = 'feat/xy'
    $nfc = "feat/caf$([char]0xE9)"; $nfd = "feat/cafe$([char]0x301)"
    # The zero-width name has a worktree and an open PR; its plain twin is merged and local only.
    $zwDir = Join-Path $m '.claude/worktrees/zw-wt'
    $null = G $m worktree add -q -b $zw .claude/worktrees/zw-wt master
    Set-Answer $w $zw @(New-PrRow 121 OPEN (New-Commit $zwDir 'zw.txt'))
    foreach ($c in ($plain, 120, 'MERGED'), ($nfc, 122, 'MERGED'), ($nfd, 0, $null)) {
        $b, $pr, $state = $c
        $null = G $m checkout -q -b $b master
        $head = New-Commit $m "$pr.txt"
        Set-Answer $w $b @(if ($state) { New-PrRow $pr $state $head })
        $null = G $m checkout -q master
    }
    $folds = $IsWindows -or $IsMacOS
    foreach ($label in 'report mode', '-Delete') {
        $run = Invoke-Sweep $w @(if ($label -eq '-Delete') { '-Delete' })
        Assert-Equal 0 $run.Code "$label exits 0"
        Assert-Equal 0 @($run.Lines -match 'differs only in case').Count "$label twin-skips neither pair: they differ by more than case"
        if (-not $folds) {
            $verb = if ($label -eq '-Delete') { 'deleted' } else { 'finished' }
            Assert-Line $run "${verb}: local $plain (PR #120 MERGED)"
            Assert-Line $run "${verb}: local $nfc (PR #122 MERGED)"
            Assert-Line $run 'skip: worktree .claude/worktrees/zw-wt - open PR #121'
            Assert-Line $run "skip: local $zw - open PR #121"
            Assert-Line $run "skip: local $nfd - no PR"
        }
    }
    if (-not $folds) {
        Assert-Equal $true (Test-Path -LiteralPath (Join-Path $zwDir 'zw.txt')) 'the zero-width branch''s worktree stays: its own PR is open'
        $left = @(G $m for-each-ref '--format=%(refname:lstrip=2)' refs/heads)
        $kept = @($zw, $nfd, 'master' | Where-Object {
                $n = $_; @($left | Where-Object { [string]::Equals($_, $n, [StringComparison]::Ordinal) }).Count -eq 1
            })
        Assert-Equal "3 of $($left.Count)" "$($kept.Count) of $($left.Count)" '-Delete leaves exactly the zero-width, the NFD and the default branch'
    }

    # ── a worktree git refuses to remove ────────────────────────────────────────────────────
    Write-Host 'a worktree git refuses to remove is failed, not left on disk'
    $w = New-World 'w10'; $m = $w.Main
    $subRepo = Join-Path $scratch 'w10/sub'
    $null = git init -q -b master $subRepo 2>&1
    $null = New-Commit $subRepo 'sub.txt'
    $subDir = Join-Path $m '.claude/worktrees/sub-wt'
    $null = G $m worktree add -q -b feat/sub-wt .claude/worktrees/sub-wt master
    $null = G $subDir -c protocol.file.allow=always submodule add -q $subRepo sub
    $null = G $subDir commit -q -m 'add a submodule'
    Set-Answer $w 'feat/sub-wt' @(New-PrRow 100 MERGED @(G $subDir rev-parse HEAD)[0])
    $run = Invoke-Sweep $w @('-Delete')
    Assert-Equal 1 $run.Code 'a worktree git refuses to remove makes -Delete exit 1'
    Assert-Line $run 'failed: worktree .claude/worktrees/sub-wt - ' -Prefix
    Assert-Equal 0 @($run.Lines -like 'left on disk: *').Count 'a worktree git refused is not reported left on disk'
    Assert-Line $run 'skip: local feat/sub-wt - its worktree was not removed'
    Assert-Equal 1 @(G $m worktree list --porcelain | Where-Object { $_ -like 'worktree *sub-wt' }).Count 'git still lists it'
    Assert-Equal 'feat/sub-wt,master' (Get-Heads $m) 'its branch stays'

    # ── a clean worktree on an unborn branch whose name origin has ─────────────────────────
    Write-Host 'a worktree whose branch has no commit is kept, not judged'
    $w = New-World 'w13'; $m = $w.Main
    $null = G $m checkout -q -b feat/unborn master
    $unbornHead = New-Commit $m 'unborn.txt'
    $null = G $m push -q origin feat/unborn
    $null = G $m checkout -q master
    $null = G $m branch -q -D feat/unborn
    Set-Answer $w 'feat/unborn' @(New-PrRow 130 MERGED $unbornHead)
    Set-BaseAnswer $w 'feat/unborn'
    $unbornDir = Join-Path $m '.claude/worktrees/unborn-wt'
    $null = G $m worktree add -q --detach .claude/worktrees/unborn-wt master
    $null = G $unbornDir checkout -q --orphan feat/unborn
    $null = G $unbornDir rm -r -q --cached .
    [System.IO.Directory]::GetFileSystemEntries($unbornDir) | Where-Object { (Split-Path $_ -Leaf) -ne '.git' } |
        ForEach-Object { Remove-Item -LiteralPath $_ -Recurse -Force }
    Assert-Equal '' ((@(G $unbornDir status --porcelain)) -join ';') 'the unborn worktree is clean'
    Assert-Equal 0 @(G $m for-each-ref refs/heads/feat/unborn).Count 'and its branch has no ref yet'
    foreach ($label in 'report mode', '-Delete') {
        $run = Invoke-Sweep $w @(if ($label -eq '-Delete') { '-Delete' })
        Assert-Equal 0 $run.Code "$label exits 0 beside a worktree on an unborn branch"
        Assert-Line $run 'skip: worktree .claude/worktrees/unborn-wt - its branch has no commit to judge'
    }
    Assert-Equal $true (Test-Path -LiteralPath $unbornDir) 'the unborn worktree stays'

    # ── a finished worktree holding another worktree ────────────────────────────────────────
    Write-Host 'a worktree that holds another worktree stays'
    $w = New-World 'w14'; $m = $w.Main
    $outerDir = Join-Path $m '.claude/worktrees/outer'
    $null = G $m worktree add -q -b feat/outer .claude/worktrees/outer master
    [System.IO.File]::WriteAllText((Join-Path $outerDir '.gitignore'), ".claude/worktrees/`n", $Utf8)
    $null = G $outerDir add .gitignore
    $null = G $outerDir commit -q -m 'ignore worktrees made from here'
    Set-Answer $w 'feat/outer' @(New-PrRow 140 MERGED @(G $outerDir rev-parse HEAD)[0])
    Set-BaseAnswer $w 'feat/outer'
    # A worktree made from inside the finished one, with work nobody committed.
    $innerDir = Join-Path $outerDir '.claude/worktrees/inner'
    $null = G $outerDir worktree add -q -b feat/inner .claude/worktrees/inner master
    [System.IO.File]::WriteAllText((Join-Path $innerDir 'wip.txt'), 'unsaved', $Utf8)
    Set-Answer $w 'feat/inner' @()
    foreach ($label in 'report mode', '-Delete') {
        $run = Invoke-Sweep $w @(if ($label -eq '-Delete') { '-Delete' })
        Assert-Equal 0 $run.Code "$label exits 0 beside a worktree holding another"
        Assert-Line $run 'skip: worktree .claude/worktrees/outer - holds worktree .claude/worktrees/outer/.claude/worktrees/inner'
        Assert-Line $run 'skip: local feat/outer - holds worktree .claude/worktrees/outer/.claude/worktrees/inner'
    }
    Assert-Equal $true (Test-Path -LiteralPath (Join-Path $innerDir 'wip.txt')) 'the inner worktree and its uncommitted file survive -Delete'
    Assert-Equal 'feat/inner,feat/outer,master' (Get-Heads $m) 'both branches stay'

    # ── an ignored name holding a CR ─────────────────────────────────────────────────────────
    if ($IsWindows) { Write-Host '  skip: a CR in a directory name cannot exist on Windows' -ForegroundColor DarkGray }
    else {
        Write-Host 'an ignored path that does not come back as named keeps its worktree'
        $w = New-World 'w15'; $m = $w.Main
        $crDir = Join-Path $m '.claude/worktrees/cr-wt'
        $null = G $m worktree add -q -b feat/cr-wt .claude/worktrees/cr-wt master
        [System.IO.File]::WriteAllText((Join-Path $crDir '.gitignore'), "ig*`n", $Utf8)
        $null = G $crDir add .gitignore
        $null = G $crDir commit -q -m 'ignore ig*'
        Set-Answer $w 'feat/cr-wt' @(New-PrRow 150 MERGED @(G $crDir rev-parse HEAD)[0])
        Set-BaseAnswer $w 'feat/cr-wt'
        # A nested repository under an ignored name that holds a CR: its commit is in no PR.
        $crNest = Join-Path $crDir "ig`rx"
        $null = git init -q $crNest 2>&1
        $null = New-Commit $crNest 'n.txt'
        $run = Invoke-Sweep $w @('-Delete')
        Assert-Equal 1 $run.Code 'an ignored path not on disk as named makes -Delete exit 1'
        Assert-Line $run 'skip: worktree .claude/worktrees/cr-wt - status of .claude/worktrees/cr-wt unreadable: ignored ' -Prefix
        Assert-Equal $true (Test-Path -LiteralPath (Join-Path $crNest '.git')) 'the nested repository under the CR name survives'
    }
}
finally {
    if ($held) { Stop-Process -Id $held.Id -Force -ErrorAction Ignore; $held.WaitForExit() }
    foreach ($k in $savedGit.Keys) { Set-EnvVar $k $savedGit[$k] }
    [Console]::OutputEncoding = $savedEncoding
    if (-not $IsWindows) { chmod -R u+w $scratch 2>$null }
    Remove-Item -LiteralPath $scratch -Recurse -Force
}

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall branch-sweep cases pass" -ForegroundColor Green
exit 0
