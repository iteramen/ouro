<#
.SYNOPSIS
    End-to-end test of Install-Ouro.ps1 against scratch repos.
.DESCRIPTION
    The installer writes into a consumer's tree, so the assertions are about the TREE after real
    runs: which files exist, their exact bytes, and that a second run or a -WhatIf run leaves
    every file byte-identical. Each case builds a fresh repo in TEMP, with an origin remote naming
    the repository the gh stub answers for -- the installer resolves the slug from origin's URL --
    and runs the installer as a subprocess from that repo's root, the way a person runs it.

    gh is a stub on PATH in every case but one. The stub answers from STUB_GH_* variables and
    logs each call, so the runs where gh resolves the repo, or is authenticated without resolving
    it, need neither network nor credentials, and a label create is a logged line, never a label.
    The one real-gh case runs with an empty GH_CONFIG_DIR and no token, so it stays offline too.

    Each fixture points core.excludesFile at a file of the suite's own, empty unless the case
    ignores through it: a developer's global ignore of .claude/ would otherwise answer the
    check-ignore questions the installer asks.

    The tag is read from the plugin's own plugin.json and a copied workflow is compared with the
    template it came from, so nothing here names a version or an owner. A vendored tree
    (bin/Vendor-Ouro.ps1) copies the scripts flat and nothing under templates/, so there the
    suite prints a skip line and exits 0.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path -LiteralPath (Join-Path $Base 'templates/ouro.toml.example'))) {
    if (Test-Path -LiteralPath (Join-Path $Base 'Install-Ouro.ps1')) {
        Write-Host "skip: no templates/ beside the flat scripts (vendored layout); the installer copies from the plugin" -ForegroundColor DarkGray
        exit 0
    }
    throw "templates/ouro.toml.example not found under $Base"
}
$Installer = Join-Path $Base 'bin/Install-Ouro.ps1'
$BindingTool = Join-Path $Base 'bin/ouro-binding.py'

$failures = 0
# -ceq: a byte-identical claim must not pass on a case-only difference.
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -ceq $Actual) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}
# A template the suite or a fixture reads: a missing one is a red row naming it, not a throw
# that ends the suite before its summary. The layout check above, the installer and plugin.json
# are read unguarded and stay as they are.
function Read-Root([string]$Rel) {
    $p = Join-Path $Base $Rel
    if (-not [System.IO.File]::Exists($p)) {
        Write-Host "  FAIL: the plugin root holds no $Rel to read" -ForegroundColor Red
        $script:failures++
        return ''
    }
    [System.IO.File]::ReadAllText($p)
}

$Pwsh = (Get-Process -Id $PID).Path
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$Example = Read-Root 'templates/ouro.toml.example'
$Template = Read-Root 'templates/docs-freshness.yml'
$CITemplate = Read-Root 'templates/ci.yml'
# The placeholder the installer falls back to where the example cannot be read, read out of the
# installer itself: a case below asserts the example agrees, so neither can drift unnoticed.
# The assignment is read wherever it is nested, and an empty answer is a failed read, not a
# placeholder: unanchored from the left, it survives the statement moving into a block.
$InstallerPlaceholder = [regex]::Match([System.IO.File]::ReadAllText($Installer), "(?m)^\s*\`$exampleSlug = '([^']*)'").Groups[1].Value
if (-not $InstallerPlaceholder) { throw "could not read `$exampleSlug from $Installer" }
$Tag = 'v' + (Get-Content -LiteralPath (Join-Path $Base '.claude-plugin/plugin.json') -Raw | ConvertFrom-Json).version
$WorkflowRel = '.github/workflows/docs-freshness.yml'
$CIWorkflowRel = '.github/workflows/ci.yml'
# Each -IssueIntake destination and the plugin template it is copied from, in the installer's order.
$IntakeCopies = [ordered]@{
    '.github/ISSUE_TEMPLATE/work-item.yml'     = 'templates/work-item.yml'
    '.github/ISSUE_TEMPLATE/config.yml'        = 'templates/issue-template-config.yml'
    '.github/workflows/issue-intake-label.yml' = 'templates/issue-intake-label.yml'
}
$RepoJson = '{"nameWithOwner":"o/n","defaultBranchRef":{"name":"trunk"}}'
$Resolved = @{ Repo = $RepoJson; User = 'me'; Auth = '1' }
$Filled = $Template.Replace('<your-default-branch>', 'trunk').Replace('<tag>', $Tag)
$CIFilled = $CITemplate.Replace('<your-default-branch>', 'trunk').Replace('<tag>', $Tag)
$BoundBinding = @'
schema = 1
[repo]
slug = "o/n"
default_branch = "trunk"
[ship]
policy = "stop-at-pr"
review = "adversarial-review"
[owner]
ruling_approvers = ["me"]
'@

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ('install-ouro-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$stubDir = Join-Path $scratch 'stub'
$ghLog = Join-Path $scratch 'gh.log'
$noExcludes = Join-Path $scratch 'no-excludes'
New-Item -ItemType Directory -Force -Path $stubDir | Out-Null
New-Item -ItemType File -Force -Path $noExcludes | Out-Null
$stub = @'
# gh stub: answers from STUB_GH_* and logs every call. PowerShell resolves it ahead of the real gh
# because its directory comes first on PATH, and runs it in the caller's process. That process may
# carry -WhatIf, so the log is written with a .NET call, which no ShouldProcess preference skips.
$a = @($args)
[System.IO.File]::AppendAllText($env:STUB_GH_LOG, ($a -join ' ') + [Environment]::NewLine)
$verb = "$($a[0]) $($a[1])"
if ($verb -eq 'repo view') {
    # STUB_GH_REPO_BY_URL answers per argument, one "<substring>=<json>" per line: a fork-shaped
    # clone, where origin and upstream name different repositories. A call with no argument
    # matches nothing there and falls back to STUB_GH_REPO_JSON, which plays gh's own pick.
    $target = if ($a.Count -ge 3 -and -not "$($a[2])".StartsWith('-')) { "$($a[2])" } else { '' }
    foreach ($line in @("$env:STUB_GH_REPO_BY_URL" -split "`n" | Where-Object { $_ })) {
        $pair = $line -split '=', 2
        if ($target -and $target.Contains($pair[0])) { $pair[1]; exit 0 }
    }
    if ($env:STUB_GH_REPO_JSON) { $env:STUB_GH_REPO_JSON; exit 0 }
    # Real gh echoes the argument it rejected, verbatim: `argument error: expected the
    # "[HOST/]OWNER/REPO" format, got "<the URL>"`. With STUB_GH_ECHO_ARG the stub does too, so a
    # case can see whether a credential reaches the output through gh's message rather than ours.
    if ($env:STUB_GH_ECHO_ARG -eq '1' -and $target) {
        Write-Error "argument error: expected the `"[HOST/]OWNER/REPO`" format, got `"$target`"" -ErrorAction Continue
    }
    exit 1
}
if ($verb -eq 'api user' -and $env:STUB_GH_USER) { $env:STUB_GH_USER; exit 0 }
if ($verb -eq 'auth status' -and $env:STUB_GH_AUTH -eq '1') { exit 0 }
if ($verb -eq 'label create') { exit 0 }
if ($verb -eq 'api --paginate' -and "$($a[2])" -match '^repos/[^/]+/[^/]+/labels\?per_page=100$') {
    # STUB_GH_LABELS is a JSON array of {name}; the stub prints one name per line, every page
    # at once, as --paginate with --jq '.[].name' does. Unset plays a repository with no labels.
    # UTF-8, as gh writes: the caller decodes it, and a non-ASCII name is what shows whether it does.
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    if ($env:STUB_GH_LABELS) { ($env:STUB_GH_LABELS | ConvertFrom-Json) | ForEach-Object { $_.name } }
    exit 0
}
exit 1
'@
[System.IO.File]::WriteAllText((Join-Path $stubDir 'gh.ps1'), $stub, $Utf8)

$script:fixtures = 0
function Write-Fixture([string]$Dir, [string]$Rel, [string]$Text) {
    $p = Join-Path $Dir $Rel
    New-Item -ItemType Directory -Force -Path (Split-Path $p -Parent) | Out-Null
    [System.IO.File]::WriteAllText($p, $Text, $Utf8)
}
# $Remotes is the clone's remotes, name to URL: one origin naming the repository the stub answers
# for, since the installer resolves the slug from origin's URL. @{} is a clone with none.
function New-Fixture([hashtable]$Files = @{}, [string]$Excludes = $noExcludes,
    [hashtable]$Remotes = @{ origin = 'https://github.com/o/n.git' }) {
    $script:fixtures++
    $dir = Join-Path $scratch "repo$($script:fixtures)"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    git -C $dir init -q 2>$null
    git -C $dir config core.excludesFile ($Excludes -replace '\\', '/')
    foreach ($name in $Remotes.Keys) { git -C $dir remote add $name $Remotes[$name] }
    foreach ($rel in $Files.Keys) { Write-Fixture $dir $rel $Files[$rel] }
    (Resolve-Path -LiteralPath $dir).Path
}
# A symbolic link whose target is not there, at $Rel in the fixture. Every write site must refuse
# such a destination rather than follow it out of the repository. Windows without the symbolic-link
# privilege cannot create one, and returns $false so the caller prints the suite's skip line; the
# ubuntu runner is where these cases actually execute.
function New-DanglingLink([string]$Dir, [string]$Rel, [string]$TargetRel) {
    $p = Join-Path $Dir $Rel
    New-Item -ItemType Directory -Force -Path (Split-Path $p -Parent) | Out-Null
    try { New-Item -ItemType SymbolicLink -Path $p -Target (Join-Path $Dir $TargetRel) -ErrorAction Stop | Out-Null; $true }
    catch { $false }
}
# A symbolic link at $Rel in the fixture to a new, empty directory outside it, which it returns --
# or $null where no link can be created, and the caller prints the skip line as above.
function New-OutsideLink([string]$Dir, [string]$Rel) {
    $target = Join-Path $scratch "outside$($script:fixtures)"
    $p = Join-Path $Dir $Rel
    New-Item -ItemType Directory -Force -Path $target, (Split-Path $p -Parent) | Out-Null
    try { New-Item -ItemType SymbolicLink -Path $p -Target $target -ErrorAction Stop | Out-Null; $target }
    catch { $null }
}
function Read-Fixture([string]$Dir, [string]$Rel) {
    $p = Join-Path $Dir $Rel
    if (Test-Path -LiteralPath $p) { [System.IO.File]::ReadAllText($p) } else { '<absent>' }
}
# Bytes as hex: ReadAllText would drop a byte-order mark and decode UTF-16 before comparing.
function Get-Hex([byte[]]$Bytes) { [BitConverter]::ToString($Bytes) }
function Read-FixtureHex([string]$Dir, [string]$Rel) {
    $p = Join-Path $Dir $Rel
    if (Test-Path -LiteralPath $p -PathType Leaf) { Get-Hex ([System.IO.File]::ReadAllBytes($p)) } else { '<absent>' }
}
# The source side of a by-content pair: bytes as hex, read by path alone, and '<no source>' where
# the root does not hold the file, which is neither a hex string nor Read-FixtureHex's answer. A
# source that is not there must not read as a destination the run never wrote: the installer skips
# a missing template with a REFUSED line and writes nothing, so two equal sentinels would pass.
function Get-RootHex([string]$Root, [string]$Rel) {
    $p = Join-Path $Root $Rel
    if ([System.IO.File]::Exists($p)) { Get-Hex ([System.IO.File]::ReadAllBytes($p)) } else { '<no source>' }
}
# A by-content pair: the source is there, with its own sentinel, and the destination is the
# source byte for byte.
function Assert-CopyOf([string]$Dir, [string]$Rel, [string]$What) {
    $src = $IntakeCopies[$Rel]
    Assert-Equal $false ((Get-RootHex $Base $src) -ceq '<no source>') "$src is there to compare"
    Assert-Equal (Get-RootHex $Base $src) (Read-FixtureHex $Dir $Rel) $What
}
Assert-Equal $false ((Get-RootHex $Base 'no/such/file') -ceq (Read-FixtureHex $Base 'no/such/file')) `
    'a path under neither root: the source reader and the destination reader disagree'
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
# Every file outside .git/ with its hash: the whole working tree, untracked files included.
function Get-TreeState([string]$Dir) {
    $root = (Resolve-Path -LiteralPath $Dir).Path
    @(Get-Entries $root | Where-Object { $_ -is [System.IO.FileInfo] } |
        ForEach-Object { $_.FullName.Substring($root.Length).TrimStart('\', '/') -replace '\\', '/' } |
        Where-Object { $_ -notmatch '^\.git/' } | Sort-Object |
        ForEach-Object { "$_=$((Get-FileHash -LiteralPath (Join-Path $root $_) -Algorithm SHA256).Hash)" }) -join '; '
}
function Get-Line([string]$Text, [string]$Key) { [regex]::Match($Text, "(?m)^$Key = [^\r\n]*").Value }
function Get-MatchingLineCount([string]$Text, [string[]]$Patterns) {
    @($Text -split '\r?\n' | Where-Object { $line = $_; @($Patterns | Where-Object { $line -notmatch $_ }).Count -eq 0 }).Count
}
function Test-Binding([string]$Dir) {
    $o = (& python3 $BindingTool check (Join-Path $Dir '.claude/ouro.toml') 2>&1 | Out-String).Trim()
    "exit=$LASTEXITCODE $o"
}
# $null unsets: [Environment]::SetEnvironmentVariable given $null from PowerShell leaves the
# variable set to an empty string, and an empty GIT_DEFAULT_HASH breaks every git init after it.
function Set-EnvVar([string]$Name, $Value) {
    if ($null -eq $Value) { Remove-Item -LiteralPath "Env:$Name" -ErrorAction Ignore }
    else { [Environment]::SetEnvironmentVariable($Name, $Value) }
}
function Invoke-Installer {
    param([string]$Dir, [string[]]$Arguments = @(), [hashtable]$Gh = @{}, [switch]$RealGh, [string]$Script = $Installer,
        [hashtable]$Environment = @{})
    if ($RealGh) {
        $vars = @{ GH_CONFIG_DIR = (Join-Path $scratch 'gh-config'); GH_TOKEN = $null; GITHUB_TOKEN = $null
            GH_ENTERPRISE_TOKEN = $null; GITHUB_ENTERPRISE_TOKEN = $null; GH_REPO = $null; GH_HOST = $null }
    }
    else {
        $vars = @{ PATH = $stubDir + [System.IO.Path]::PathSeparator + $env:PATH; STUB_GH_LOG = $ghLog
            STUB_GH_REPO_JSON = $Gh['Repo']; STUB_GH_REPO_BY_URL = $Gh['ByUrl']
            STUB_GH_ECHO_ARG = $Gh['EchoArg']
            STUB_GH_USER = $Gh['User']; STUB_GH_AUTH = $Gh['Auth'] }
    }
    foreach ($k in $Environment.Keys) { $vars[$k] = $Environment[$k] }
    $saved = @{}
    foreach ($k in $vars.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); Set-EnvVar $k $vars[$k] }
    Remove-Item -LiteralPath $ghLog -ErrorAction Ignore
    Push-Location -LiteralPath $Dir
    try {
        $out = & $Pwsh -NoProfile -File $Script @Arguments 2>&1 | Out-String
        $code = $LASTEXITCODE
        # SGR codes are stripped: a host that renders VT colours the child's warning stream, and a
        # row matches the text.
        $out = $out -replace '\x1b\[[0-9;]*m'
    }
    finally {
        Pop-Location
        foreach ($k in $saved.Keys) { Set-EnvVar $k $saved[$k] }
    }
    $calls = @()
    if (Test-Path -LiteralPath $ghLog) { $calls = @(Get-Content -LiteralPath $ghLog) }
    [pscustomobject]@{ Code = $code; Out = $out; Gh = $calls }
}

try {
    # The stub is only a stub if PowerShell picks it over the real gh.
    $savedPath = $env:PATH
    $env:PATH = $stubDir + [System.IO.Path]::PathSeparator + $env:PATH
    try { $resolvedGh = (Get-Command gh | Select-Object -First 1).Source } finally { $env:PATH = $savedPath }
    if ($resolvedGh -ne (Join-Path $stubDir 'gh.ps1')) { throw "the gh stub does not shadow gh on PATH (resolved '$resolvedGh')" }

    # ── Invoke-Installer strips SGR codes, as the vendor suite's run helper does ───────────
    # The ubuntu runner gives pwsh no TTY, so a real run there never colours its own warning
    # stream; this stub reproduces what a host that renders VT writes, so the strip is proven
    # without one.
    $sgrStub = Join-Path $scratch 'sgr-stub.ps1'
    [System.IO.File]::WriteAllText($sgrStub, ('Write-Host "{0}[33mWARNING: stub{0}[0m"' -f [char]27), $Utf8)
    $repo = New-Fixture
    $run = Invoke-Installer $repo -Script $sgrStub
    Assert-Equal $true ($run.Out -cmatch '(?m)^WARNING: stub\r?$') 'Invoke-Installer strips SGR codes the way the vendor suite''s helper does'
    Assert-Equal $false ($run.Out.Contains([char]27)) 'and no escape character remains in the captured text'

    # ── a fresh repo and the real gh: no remote, so nothing resolves ──────────────────────
    # No origin, so the real gh is never asked for a repository and the case stays offline.
    $repo = New-Fixture -Remotes @{}
    $run = Invoke-Installer $repo -RealGh
    $toml = Read-Fixture $repo '.claude/ouro.toml'
    Assert-Equal 0 $run.Code 'fresh repo, real gh, no remote: the run exits 0'
    Assert-Equal 'exit=0 ok' (Test-Binding $repo) 'the binding is created and ouro-binding.py check accepts it'
    Assert-Equal $false ($toml -match '(?m)^\[\[gate\]\]') 'the binding carries no [[gate]] table'
    foreach ($key in 'slug', 'default_branch', 'ruling_approvers') {
        Assert-Equal (Get-Line $Example $key) (Get-Line $toml $key) "$key keeps the example's placeholder when gh cannot resolve it"
    }
    Assert-Equal 1 (Get-MatchingLineCount $run.Out 'WARNING', 'repo\.slug', 'repo\.default_branch', 'owner\.ruling_approvers') `
        'one WARNING line names every key left as a placeholder'
    Assert-Equal 'review = "adversarial-review"' (Get-Line $toml 'review') '-Review defaults to adversarial-review'
    Assert-Equal ".claude/worktrees/`n.claude/ouro.local.toml`n" (Read-Fixture $repo '.gitignore') `
        'a missing .gitignore is created with the two entries'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^skip: labels - the repo slug did not resolve') 'labels are skipped, and the line says why'
    Assert-Equal '<absent>' (Read-Fixture $repo $WorkflowRel) 'no workflow without -DocsFreshness'
    foreach ($rel in $IntakeCopies.Keys) { Assert-Equal '<absent>' (Read-FixtureHex $repo $rel) "no $rel without -IssueIntake" }
    Assert-Equal 0 (Get-MatchingLineCount $run.Out '^WARNING: \.claude/ouro\.toml is ignored') 'no ignore warning when the binding is not ignored'
    $before = Get-TreeState $repo
    $run = Invoke-Installer $repo -RealGh
    Assert-Equal 0 $run.Code 'a second run exits 0'
    Assert-Equal $before (Get-TreeState $repo) 'a second run changes no file'

    # ── gh authenticated, but the repo view fails: labels still wait for the slug ─────────
    $repo = New-Fixture
    $run = Invoke-Installer $repo -Gh @{ User = 'me'; Auth = '1' }
    $toml = Read-Fixture $repo '.claude/ouro.toml'
    Assert-Equal 0 $run.Code 'an authenticated gh with no resolvable repo: the run exits 0'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^skip: labels - the repo slug did not resolve') `
        'labels are skipped because the slug did not resolve, although gh is authenticated'
    Assert-Equal 0 (@($run.Gh -match '^label create').Count) 'and no label create is attempted'
    Assert-Equal 'ruling_approvers = ["me"]' (Get-Line $toml 'ruling_approvers') 'owner.ruling_approvers comes from gh api user on its own'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out 'WARNING', 'repo\.slug', 'repo\.default_branch') 'the warning names the two unresolved keys'
    Assert-Equal 0 (Get-MatchingLineCount $run.Out 'WARNING.*owner\.ruling_approvers') 'and not the resolved one'

    # ── gh resolves everything: values filled, the branch from gh, labels created ─────────
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-DocsFreshness') -Gh $Resolved
    $toml = Read-Fixture $repo '.claude/ouro.toml'
    Assert-Equal 0 $run.Code 'gh resolves the repo: the run exits 0'
    Assert-Equal 'slug = "o/n"' (Get-Line $toml 'slug') 'repo.slug comes from gh repo view'
    Assert-Equal 'default_branch = "trunk"' (Get-Line $toml 'default_branch') 'repo.default_branch comes from gh repo view'
    Assert-Equal 1 (@($run.Gh -match '^repo view https://github\.com/o/n\.git --json ').Count) `
        'and the one repo view names origin''s URL, the single remote of this clone'
    Assert-Equal 'ruling_approvers = ["me"]' (Get-Line $toml 'ruling_approvers') 'owner.ruling_approvers comes from gh api user'
    Assert-Equal 0 (Get-MatchingLineCount $run.Out 'WARNING') 'no placeholder warning when gh resolved every value'
    Assert-Equal 'exit=0 ok' (Test-Binding $repo) 'the filled binding passes check'
    Assert-Equal $Filled (Read-Fixture $repo $WorkflowRel) 'the workflow is the template with the branch from gh and the plugin tag'
    $creates = @($run.Gh -match '^label create ')
    Assert-Equal $true ($creates.Count -gt 0) 'labels are created when the slug resolved and gh is authenticated'
    Assert-Equal 0 (@($creates | Where-Object { $_ -notmatch ' --repo o/n ' }).Count) 'every label create names the resolved slug'
    # architecture is one of the contract's states: created with a colour, a description saying
    # what the state means, and --force, which updates a label of that name the repo already has.
    $arch = @($creates -clike 'label create architecture *')
    Assert-Equal 1 $arch.Count 'the architecture state label is created'
    Assert-Equal $true (($arch -join "`n") -cmatch '^label create architecture --repo o/n --color [0-9A-F]{6} --description A design being shaped: .+ --force$') `
        'with a colour, a description of a design being shaped, and --force'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out ([regex]::Escape($Tag)), 'OURO_REF') 'the tag and the OURO_REF same-release reminder are printed'
    $before = Get-TreeState $repo
    $run = Invoke-Installer $repo @('-DocsFreshness') -Gh $Resolved
    Assert-Equal 0 $run.Code 'a second -DocsFreshness run exits 0'
    Assert-Equal $before (Get-TreeState $repo) 'a second -DocsFreshness run changes no file'

    # ── a fork's clone: ouro manages the repository origin names ─────────────────────────
    # gh picks among a clone's remotes -- a `gh repo set-default` value, then upstream, github,
    # origin -- so a bare `repo view` here answers the parent, which STUB_GH_REPO_JSON plays.
    $forkJson = '{"nameWithOwner":"fork/proj","defaultBranchRef":{"name":"fork-main"}}'
    $parentJson = '{"nameWithOwner":"parent/proj","defaultBranchRef":{"name":"parent-main"}}'
    $forkGh = @{ Repo = $parentJson; ByUrl = "fork/proj=$forkJson`nparent/proj=$parentJson"; User = 'me'; Auth = '1' }
    $repo = New-Fixture -Remotes @{ origin = 'https://github.com/fork/proj.git'; upstream = 'https://github.com/parent/proj.git' }
    $run = Invoke-Installer $repo -Gh $forkGh
    $toml = Read-Fixture $repo '.claude/ouro.toml'
    Assert-Equal 0 $run.Code "a fork's clone: the run exits 0"
    Assert-Equal 'slug = "fork/proj"' (Get-Line $toml 'slug') 'repo.slug is the repository origin names, not the parent gh picks'
    Assert-Equal 'default_branch = "fork-main"' (Get-Line $toml 'default_branch') "and repo.default_branch is that repository's"
    Assert-Equal 0 (@($run.Gh -match '^repo view(\s+--|$)').Count) 'no bare repo view is made'
    Assert-Equal 1 (@($run.Gh -match '^repo view https://github\.com/fork/proj\.git --json ').Count) "the one repo view names origin's URL"
    $creates = @($run.Gh -match '^label create ')
    Assert-Equal $true ($creates.Count -gt 0) 'labels are created'
    Assert-Equal 0 (@($creates | Where-Object { $_ -notmatch ' --repo fork/proj ' }).Count) 'and every label create names the fork'

    # ── a re-run: the labels go on the repository the binding names ───────────────────────
    [System.IO.File]::WriteAllText((Join-Path $repo '.claude/ouro.toml'),
        ($toml -replace '(?m)^slug = [^\r\n]*', 'slug = "third/proj"'), $Utf8)
    $run = Invoke-Installer $repo -Gh $forkGh
    $creates = @($run.Gh -match '^label create ')
    Assert-Equal 0 $run.Code 'a re-run over a binding naming a third repository: the run exits 0'
    Assert-Equal $true ($creates.Count -gt 0) 'it creates the labels'
    Assert-Equal 0 (@($creates | Where-Object { $_ -notmatch ' --repo third/proj ' }).Count) `
        'on the repository the binding names, not the one gh resolves for origin'

    # ── a clone with no origin: the slug keeps the placeholder ────────────────────────────
    # gh answers a bare `repo view` here, so a slug resolved at all is one read from gh's pick.
    $repo = New-Fixture -Remotes @{}
    $run = Invoke-Installer $repo -Gh $Resolved
    $toml = Read-Fixture $repo '.claude/ouro.toml'
    Assert-Equal 0 $run.Code 'a clone with no origin remote: the run exits 0'
    foreach ($key in 'slug', 'default_branch') {
        Assert-Equal (Get-Line $Example $key) (Get-Line $toml $key) "$key keeps the example's placeholder with no origin to resolve"
    }
    Assert-Equal 1 (Get-MatchingLineCount $run.Out 'WARNING', 'repo\.slug', 'repo\.default_branch') 'one WARNING names both keys'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^skip: labels - the repo slug did not resolve \(the clone has no origin remote\)') `
        'the labels are skipped, and the line says there is no origin'
    Assert-Equal 0 (@($run.Gh -match '^repo view').Count) 'and gh is never asked for a repository'

    # ── gh resolves the slug but is not authenticated ─────────────────────────────────────
    $repo = New-Fixture
    $run = Invoke-Installer $repo -Gh @{ Repo = $RepoJson }
    Assert-Equal 0 $run.Code 'an unauthenticated gh: the run exits 0'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^skip: labels - gh auth status failed') 'labels are skipped, naming gh auth status'
    Assert-Equal 0 (@($run.Gh -match '^label create').Count) 'and no label create is attempted'

    # ── an existing binding is left, and -DocsFreshness takes its branch ──────────────────
    $repo = New-Fixture @{ '.claude/ouro.toml' = $BoundBinding }
    $run = Invoke-Installer $repo @('-DocsFreshness', '-Review', 'external-audit', '-ExternalCli', 'grok')
    $copy = Read-Fixture $repo $WorkflowRel
    Assert-Equal 0 $run.Code 'an existing binding: the run exits 0'
    Assert-Equal $BoundBinding (Read-Fixture $repo '.claude/ouro.toml') 'an existing binding is left byte-identical, -Review notwithstanding'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^skip: \.claude/ouro\.toml exists') 'and the run says it was skipped'
    Assert-Equal $Filled $copy 'gh failed, so the workflow branch is the one the existing binding names'
    Assert-Equal $false ($copy -match '<your-default-branch>|<tag>') 'the copy keeps neither placeholder'
    Assert-Equal $true ($copy -match "/actions/docs-freshness@$([regex]::Escape($Tag))\r?\n") "the Action tag is v + the plugin's plugin.json version"
    $before = Get-TreeState $repo
    $run = Invoke-Installer $repo @('-DocsFreshness')
    Assert-Equal $before (Get-TreeState $repo) 'a second run over an existing binding and workflow changes no file'

    # ── an existing binding's declared [labels]: created only where missing, never --force ─
    $declaredBinding = $BoundBinding + "`n[labels]`nscope = []`narea = [`"missing-area`", `"existing-area`"]`ntype = []`n"
    $repo = New-Fixture @{ '.claude/ouro.toml' = $declaredBinding }
    $run = Invoke-Installer $repo -Gh @{ Auth = '1' } -Environment @{ STUB_GH_LABELS = '[{"name":"existing-area"}]' }
    Assert-Equal 0 $run.Code 'the declared-labels run exits 0'
    $creates = @($run.Gh -match '^label create ')
    Assert-Equal 1 (@($creates -clike 'label create missing-area *').Count) 'the declared area label the repo lacks is created'
    Assert-Equal 0 (@(($creates -clike 'label create missing-area *') -match '--force').Count) `
        'without --force: the team''s label is never edited'
    Assert-Equal 0 (@($creates -clike 'label create existing-area *').Count) `
        'the declared area label the repo already has is left untouched: no create call for it'
    Assert-Equal 1 (@($run.Gh -match '^api --paginate repos/o/n/labels\?per_page=100 ').Count) 'the repo''s labels are read once, every page'

    # ── one name declared twice, in two cases, or already on the repo past any page cap ────
    # GitHub label names ignore case. A name under two keys, or in two spellings, is created once;
    # an existing label is never created again, however far down the repo's list it sits.
    $dupBinding = $BoundBinding + "`n[labels]`nscope = [`"shared`", `"Later-Area`", `"scope-only`"]`narea = [`"shared`", `"later-area`", `"far-area`", `"caf\u00e9`", `"after-cafe`"]`ntype = [`"bug`"]`n"
    $many = '[' + ((1..150 | ForEach-Object { '{"name":"filler-' + $_ + '"}' }) -join ',') + ',{"name":"Far-Area"},{"name":"CAF\u00c9"}]'
    $repo = New-Fixture @{ '.claude/ouro.toml' = $dupBinding }
    $run = Invoke-Installer $repo -Gh @{ Auth = '1' } -Environment @{ STUB_GH_LABELS = $many }
    Assert-Equal 0 $run.Code 'a name declared twice exits 0, not a failed second create'
    $creates = @($run.Gh -match '^label create ')
    Assert-Equal 1 (@($creates -clike 'label create shared *').Count) 'a name under two keys is created once'
    Assert-Equal 1 (@($creates -match '^label create later-area ').Count) 'two spellings of one name are created once'
    Assert-Equal 1 (@($creates -clike 'label create bug *').Count) 'and the declared names after the duplicate are still created'
    Assert-Equal 0 (@($creates -match '^label create far-area ').Count) 'a label the repo has, past the 150th, is not created again'
    Assert-Equal 1 (@($creates -clike 'label create scope-only *').Count) 'a name declared under scope alone is created: scope is read'
    # The stub is a .ps1 the child runs in-process, so its output is never decoded as native bytes:
    # this row pins the case-insensitive match of a non-ASCII name, not New-OuroLabels' UTF-8 read of
    # gh, which only a native gh shows (under code page 437 the undecoded read turns café into caf├⌐).
    Assert-Equal 0 (@($creates -match '^label create caf').Count) 'a non-ASCII label the repo has is matched, not created again'
    Assert-Equal 1 (@($creates -clike 'label create after-cafe *').Count) 'and the name declared after it is still created'

    # ── an existing workflow is left ──────────────────────────────────────────────────────
    $mine = "name: mine`n"
    $repo = New-Fixture @{ '.claude/ouro.toml' = $BoundBinding; '.github/workflows/docs-freshness.yml' = $mine }
    $run = Invoke-Installer $repo @('-DocsFreshness')
    Assert-Equal $mine (Read-Fixture $repo $WorkflowRel) 'an existing workflow is left byte-identical'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^skip: \.github/workflows/docs-freshness\.yml exists') 'and the run says it was skipped'

    # ── -CI: written when absent, with the branch and tag filled ─────────────────────────
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-CI') -Gh $Resolved
    Assert-Equal 0 $run.Code '-CI with gh resolved: the run exits 0'
    Assert-Equal $CIFilled (Read-Fixture $repo $CIWorkflowRel) 'the CI workflow is the template with the branch from gh and the plugin tag'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out "^wrote: $([regex]::Escape($CIWorkflowRel))") 'and a wrote: line names it'
    $before = Get-TreeState $repo
    $run = Invoke-Installer $repo @('-CI') -Gh $Resolved
    Assert-Equal 0 $run.Code 'a second -CI run exits 0'
    Assert-Equal $before (Get-TreeState $repo) 'and changes no file'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out "^skip: $([regex]::Escape($CIWorkflowRel)) exists") 'the second run says it was skipped'

    # ── -CI: an existing workflow is left ─────────────────────────────────────────────────
    $mine = "name: mine`n"
    $repo = New-Fixture @{ '.claude/ouro.toml' = $BoundBinding; '.github/workflows/ci.yml' = $mine }
    $run = Invoke-Installer $repo @('-CI')
    Assert-Equal $mine (Read-Fixture $repo $CIWorkflowRel) 'an existing CI workflow is left byte-identical'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^skip: \.github/workflows/ci\.yml exists') 'and the run says it was skipped'

    # ── -CI: no binding before the run and no gh: the branch is unknown, the copy refused ──
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-CI')
    Assert-Equal 0 $run.Code 'a refused -CI copy does not fail the run'
    Assert-Equal '<absent>' (Read-Fixture $repo $CIWorkflowRel) "no CI workflow: the binding this run wrote names only the example's branch"
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^REFUSED: \.github/workflows/ci\.yml not written - the default branch is unknown') 'the refusal says why'

    # ── -CI: an unreadable plugin version refuses the copy ────────────────────────────────
    $ciPlugin = Join-Path $scratch 'plugin-without-version-ci'
    foreach ($rel in 'bin/Install-Ouro.ps1', 'bin/New-OuroLabels.ps1', 'bin/ouro-binding.py', 'templates/ouro.toml.example', 'templates/ci.yml') {
        Write-Fixture $ciPlugin $rel (Read-Root $rel)
    }
    Write-Fixture $ciPlugin '.claude-plugin/plugin.json' '{}'
    $repo = New-Fixture @{ '.claude/ouro.toml' = $BoundBinding }
    $run = Invoke-Installer $repo @('-CI') -Script (Join-Path $ciPlugin 'bin/Install-Ouro.ps1')
    Assert-Equal 0 $run.Code 'a plugin.json with no version, -CI: the run exits 0'
    Assert-Equal '<absent>' (Read-Fixture $repo $CIWorkflowRel) 'no CI workflow when the plugin version cannot be read'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^REFUSED: \.github/workflows/ci\.yml not written - the Action tag is unknown') 'the refusal says why'

    # ── -IssueIntake: the three files, each its template's bytes, before the labels ───────
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-IssueIntake') -Gh $Resolved
    Assert-Equal 0 $run.Code '-IssueIntake with gh resolved: the run exits 0'
    foreach ($rel in $IntakeCopies.Keys) {
        Assert-CopyOf $repo $rel "$rel is $($IntakeCopies[$rel]) byte for byte"
        Assert-Equal 1 (Get-MatchingLineCount $run.Out "^wrote: $([regex]::Escape($rel))$") "and one wrote: line names $rel"
    }
    $lines = [string[]]($run.Out -split '\r?\n')
    $wroteAt = @(foreach ($rel in $IntakeCopies.Keys) { [Array]::IndexOf($lines, "wrote: $rel") })
    $okAt = [Array]::FindIndex($lines, [Predicate[string]] { param($l) $l.StartsWith('ok: ') })
    Assert-Equal $true ($wroteAt -notcontains -1 -and $okAt -ge 0 -and ($wroteAt | Measure-Object -Maximum).Maximum -lt $okAt) `
        "and the last of those lines comes before the label step's first ok: line"
    $before = Get-TreeState $repo
    $run = Invoke-Installer $repo @('-IssueIntake') -Gh $Resolved
    Assert-Equal $before (Get-TreeState $repo) 'a second -IssueIntake run changes no file'
    foreach ($rel in $IntakeCopies.Keys) {
        Assert-Equal 1 (Get-MatchingLineCount $run.Out "^skip: $([regex]::Escape($rel)) exists and is left as it is$") "and one skip: line names $rel"
    }

    # ── -IssueIntake: an existing template chooser config is left ─────────────────────────
    $mine = "blank_issues_enabled: false`n"
    $repo = New-Fixture @{ '.github/ISSUE_TEMPLATE/config.yml' = $mine }
    $run = Invoke-Installer $repo @('-IssueIntake')
    Assert-Equal (Get-Hex ($Utf8.GetBytes($mine))) (Read-FixtureHex $repo '.github/ISSUE_TEMPLATE/config.yml') `
        'an existing .github/ISSUE_TEMPLATE/config.yml is left byte-identical'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^skip: \.github/ISSUE_TEMPLATE/config\.yml exists and is left as it is$') 'and one skip: line names it: the repo''s own chooser config is never named as differing'
    foreach ($rel in '.github/ISSUE_TEMPLATE/work-item.yml', '.github/workflows/issue-intake-label.yml') {
        Assert-CopyOf $repo $rel "and $rel is written"
    }

    # ── -IssueIntake: a copy that predates a template change is named; a CRLF copy is not ─────
    $labelTemplate = Read-Root 'templates/issue-intake-label.yml'
    $stale = $labelTemplate.Replace('|umbrella|architecture', '|umbrella')
    Assert-Equal $true ($stale -cne $labelTemplate) 'the stale copy really lacks architecture in its state grep'
    $crlf = $labelTemplate.Replace("`n", "`r`n")
    foreach ($case in @(@{ Body = $stale; Differs = $true; What = 'a copy with the seven-state grep' },
            @{ Body = $crlf; Differs = $false; What = 'a CRLF copy of the current template' })) {
        $repo = New-Fixture @{ '.github/workflows/issue-intake-label.yml' = $case.Body }
        $run = Invoke-Installer $repo @('-IssueIntake')
        $differsLine = '^skip: \.github/workflows/issue-intake-label\.yml exists and differs from the plugin''s templates/issue-intake-label\.yml, left as it is - delete it and re-run with -IssueIntake to take the current one$'
        $sameLine = '^skip: \.github/workflows/issue-intake-label\.yml exists and is left as it is$'
        Assert-Equal ([int]$case.Differs) (Get-MatchingLineCount $run.Out $differsLine) "$($case.What): named as differing, $($case.Differs)"
        Assert-Equal ([int](-not $case.Differs)) (Get-MatchingLineCount $run.Out $sameLine) "$($case.What): the plain skip line, $(-not $case.Differs)"
        Assert-Equal (Get-Hex ($Utf8.GetBytes($case.Body))) (Read-FixtureHex $repo '.github/workflows/issue-intake-label.yml') "$($case.What): left byte-identical"
    }

    # ── no binding before the run and no gh: the branch is unknown, the copy refused ──────
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-DocsFreshness')
    Assert-Equal 0 $run.Code 'a refused copy does not fail the run'
    Assert-Equal '<absent>' (Read-Fixture $repo $WorkflowRel) "no workflow: the binding this run wrote names only the example's branch"
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^REFUSED: .*the default branch is unknown') 'the refusal says why'

    # ── a stub an earlier gh-less run wrote: its branch is the example's placeholder ──────
    $repo = New-Fixture
    $run = Invoke-Installer $repo
    $run = Invoke-Installer $repo @('-DocsFreshness')
    Assert-Equal 0 $run.Code 'a -DocsFreshness rerun over a stub binding exits 0'
    Assert-Equal '<absent>' (Read-Fixture $repo $WorkflowRel) "no workflow: the stub's branch is the example's placeholder, not the repo's"
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^REFUSED: .*placeholder slug') 'the refusal names the placeholder slug'
    # And the labels: the placeholder names no repository, so creating them on it would fail the
    # run, where every other step says why it was skipped and goes on.
    $run = Invoke-Installer $repo -Gh $Resolved
    Assert-Equal 0 $run.Code 'a rerun over a stub binding with gh resolving and signed in exits 0'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out "^skip: labels - \.claude/ouro\.toml still names the example's placeholder slug") `
        'the labels are skipped, naming the placeholder slug'
    Assert-Equal 0 (@($run.Gh -match '^label create').Count) 'and no label create is attempted'

    # ── a binding whose slug is no repository: skipped, never handed to gh ────────────────
    # New-OuroLabels validates its -Repo and gh answers an unwritable repository with a 404, so a
    # slug of another shape would end the run nonzero after steps 1-4 had written.
    # Each is the TOML value as a binding spells it: a slug with two slashes, a number, a list,
    # and an empty string. ouro-binding.py prints a list as JSON, which reaches gh as it is.
    foreach ($bad in '"a/b/c"', '123', '["fork/proj"]', '""') {
        $repo = New-Fixture @{ '.claude/ouro.toml' = ($BoundBinding -replace '(?m)^slug = [^\r\n]*', "slug = $bad") }
        $run = Invoke-Installer $repo -Gh $Resolved
        Assert-Equal 0 $run.Code "a binding whose slug is '$bad': the run exits 0"
        Assert-Equal 1 (Get-MatchingLineCount $run.Out '^skip: labels - ') "and the labels are skipped, saying why ('$bad')"
        Assert-Equal 0 (@($run.Gh -match '^label create').Count) "and no label create is attempted ('$bad')"
    }

    # A slug that differs from the placeholder only in case is a repository, not the placeholder:
    # GitHub keeps the case it was created with, and the rest of this read is case-sensitive too.
    $repo = New-Fixture @{ '.claude/ouro.toml' = ($BoundBinding -replace '(?m)^slug = [^\r\n]*', 'slug = "Owner/Name"') }
    $run = Invoke-Installer $repo -Gh $Resolved
    $creates = @($run.Gh -match '^label create ')
    Assert-Equal 0 $run.Code 'a binding whose slug differs from the placeholder only in case: the run exits 0'
    Assert-Equal $true ($creates.Count -gt 0) 'and its labels are created'
    Assert-Equal 0 (@($creates | Where-Object { $_ -notmatch ' --repo Owner/Name ' }).Count) 'on the repository it names'

    # ── the example unreadable: the shipped placeholder stands in ────────────────────────
    # A plugin tree with no templates/ is what Vendor-Ouro.ps1 leaves behind, and an example that
    # is there but locked is a read that used to end the run before step 1. Those trees still have
    # to tell a filled binding from one nobody filled, so the installer carries the placeholder.
    Assert-Equal "slug = `"$InstallerPlaceholder`"" (Get-Line $Example 'slug') `
        "the example's slug is the placeholder the installer falls back to"
    $plugin = Join-Path $scratch 'plugin-without-example'
    foreach ($rel in 'bin/Install-Ouro.ps1', 'bin/New-OuroLabels.ps1', 'bin/ouro-binding.py') {
        Write-Fixture $plugin $rel (Read-Root $rel)
    }
    Write-Fixture $plugin '.claude-plugin/plugin.json' '{"version":"0.0.0"}'
    $repo = New-Fixture @{ '.claude/ouro.toml' = $BoundBinding }
    $run = Invoke-Installer $repo -Gh $Resolved -Script (Join-Path $plugin 'bin/Install-Ouro.ps1')
    $creates = @($run.Gh -match '^label create ')
    Assert-Equal 0 $run.Code 'a plugin with no example to read: the run exits 0'
    Assert-Equal $true ($creates.Count -gt 0) 'and a filled binding still gets its labels'
    Assert-Equal 0 (@($creates | Where-Object { $_ -notmatch ' --repo o/n ' }).Count) 'on the repository it names'

    # The same tree, over a binding nobody filled: the placeholder is still refused, and step 3
    # refuses the workflow whose branch would be a placeholder too.
    $repo = New-Fixture @{ '.claude/ouro.toml' = ($BoundBinding -replace '(?m)^slug = [^\r\n]*', "slug = `"$InstallerPlaceholder`"") }
    $run = Invoke-Installer $repo @('-DocsFreshness') -Gh @{ User = 'me'; Auth = '1' } -Script (Join-Path $plugin 'bin/Install-Ouro.ps1')
    Assert-Equal 0 $run.Code 'a plugin with no example, over a placeholder binding: the run exits 0'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out "^skip: labels - .*still names the example's placeholder slug") `
        'the labels are skipped, naming the placeholder'
    Assert-Equal 0 (@($run.Gh -match '^label create').Count) 'and no label create is attempted'
    Assert-Equal '<absent>' (Read-Fixture $repo $WorkflowRel) 'and no workflow is written on a placeholder branch'

    # ── a token in origin's URL is not printed ───────────────────────────────────────────
    # gh fails here for its own reason, and the skip line used to carry the whole remote URL --
    # in CI, into the build log. A second userinfo segment is part of the credential, not of the
    # host, so the strip runs to the authority's last @.
    # Each secret is asserted on its own: Get-MatchingLineCount asks for a line matching EVERY
    # pattern, so one call naming two secrets passes while a line carries either.
    foreach ($case in @(
            @{ Url = 'https://oauth2:ghp_NOTAREALTOKEN@github.com/o/n.git'; Shown = 'https://\*\*\*@github\.com/o/n\.git' },
            @{ Url = 'https://oauth2:ghp_NOTAREALTOKEN@more_secret@github.com/o/n.git'; Shown = 'https://\*\*\*@github\.com/o/n\.git' },
            # No scheme to anchor on: a scp-style remote carries its credential the same way.
            @{ Url = 'x-access-token:ghp_NOTAREALTOKEN@github.com:o/n.git'; Shown = '\*\*\*@github\.com:o/n\.git' })) {
        $repo = New-Fixture -Remotes @{ origin = $case.Url }
        # The stub echoes the argument it was given, as gh does when it rejects one: masking only
        # the copy the installer composes publishes the credential through gh's own message.
        $run = Invoke-Installer $repo -Gh @{ User = 'me'; EchoArg = '1' }
        Assert-Equal 0 $run.Code 'a credential-carrying origin URL, gh failing: the run exits 0'
        # The instrument first: with no echo from gh there is nothing for the masking to fail at,
        # and the asserts below would pass for a reason that has nothing to do with masking.
        Assert-Equal 1 (Get-MatchingLineCount $run.Out 'expected the "\[HOST/\]OWNER/REPO" format') `
            "and gh's own message, which echoes the URL, reaches the output"
        foreach ($secret in 'ghp_NOTAREALTOKEN', 'more_secret', 'x-access-token') {
            Assert-Equal 0 (Get-MatchingLineCount $run.Out $secret) "and no line carries $secret"
        }
        Assert-Equal 1 (Get-MatchingLineCount $run.Out "^skip: labels - .*$($case.Shown)") `
            'while the line still names the URL asked, its userinfo marked'
    }

    # ── an origin git does not answer with a URL ─────────────────────────────────────────
    # Remote names are case-sensitive to git, and a remote with no url set makes `get-url` print
    # the remote's NAME, which gh would read as a repository in the signed-in owner's namespace.
    $repo = New-Fixture -Remotes @{}
    git -C $repo config remote.Origin.url 'https://github.com/o/n.git' 2>$null | Out-Null
    $run = Invoke-Installer $repo -Gh $Resolved
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^skip: labels - .*the clone has no origin remote') `
        'a remote named Origin is not the origin remote'
    # A remote URL that is one bare word names no host and no path, and gh reads such a word as a
    # repository in the signed-in owner's namespace -- so `mirror` would write a stranger's slug
    # into the binding and create the labels there.
    foreach ($word in 'mirror', 'origin', 'Origin') {
        $repo = New-Fixture -Remotes @{}
        git -C $repo config remote.origin.url $word 2>$null | Out-Null
        $run = Invoke-Installer $repo -Gh $Resolved
        Assert-Equal 0 $run.Code "an origin URL of '$word': the run exits 0"
        Assert-Equal 1 (Get-MatchingLineCount $run.Out "^skip: labels - .*names no host or path \($word\)") `
            "and the skip says the URL names no host or path ('$word')"
        Assert-Equal 0 (@($run.Gh -match '^repo view').Count) "and it never reaches gh as a repository ('$word')"
    }
    # git takes a relative local path as a remote URL, and such a path carries a slash -- so one
    # spelled the way gh spells a repository, [HOST/]OWNER/REPO, passed the test above while
    # reading to gh as exactly that repository: `octocat/Hello-World` wrote a stranger's slug into
    # the binding and created the labels there, `../mirror` and `./x` reached gh as repository
    # names of their own, and `github.com/o/r` -- an https URL with the scheme forgotten -- carries
    # a host and still resolves at gh as that repository. gh strips a leading `www.` from a host
    # it reads, so `www.github.com/o/r` resolves too: the host is any number of dot-separated
    # labels, and a shape narrowed to two would reopen this for that spelling.
    foreach ($url in 'octocat/Hello-World', 'o/r', '../mirror', './x', 'github.com/o/r', 'www.github.com/o/r') {
        $repo = New-Fixture -Remotes @{}
        git -C $repo config remote.origin.url $url 2>$null | Out-Null
        $run = Invoke-Installer $repo -Gh $Resolved
        $toml = Read-Fixture $repo '.claude/ouro.toml'
        Assert-Equal 0 $run.Code "an origin URL of '$url': the run exits 0"
        Assert-Equal 1 (Get-MatchingLineCount $run.Out "^skip: labels - .*is shaped like a repository name, not a URL \($([regex]::Escape($url))\); gh would read it as that repository") `
            "and the skip says the URL is shaped like a repository name ('$url')"
        Assert-Equal 0 (@($run.Gh -match '^repo view').Count) "and it never reaches gh as a repository ('$url')"
        foreach ($key in 'slug', 'default_branch') {
            Assert-Equal (Get-Line $Example $key) (Get-Line $toml $key) "and $key keeps the example's placeholder ('$url')"
        }
    }

    $repo = New-Fixture -Remotes @{}
    git -C $repo config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*' 2>$null | Out-Null
    $run = Invoke-Installer $repo -Gh $Resolved
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^skip: labels - .*the origin remote has no URL set') `
        'an origin with no URL says so'
    Assert-Equal 0 (@($run.Gh -match '^repo view origin').Count) 'and the remote name never reaches gh as a repository'

    # ── an unreadable plugin version refuses the copy ─────────────────────────────────────
    $plugin = Join-Path $scratch 'plugin-without-version'
    foreach ($rel in 'bin/Install-Ouro.ps1', 'bin/New-OuroLabels.ps1', 'bin/ouro-binding.py', 'templates/ouro.toml.example', 'templates/docs-freshness.yml') {
        Write-Fixture $plugin $rel (Read-Root $rel)
    }
    Write-Fixture $plugin '.claude-plugin/plugin.json' '{}'
    $repo = New-Fixture @{ '.claude/ouro.toml' = $BoundBinding }
    $run = Invoke-Installer $repo @('-DocsFreshness') -Script (Join-Path $plugin 'bin/Install-Ouro.ps1')
    Assert-Equal 0 $run.Code 'a plugin.json with no version: the run exits 0'
    Assert-Equal '<absent>' (Read-Fixture $repo $WorkflowRel) 'no workflow when the plugin version cannot be read'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^REFUSED: .*the Action tag is unknown') 'the refusal says why'

    # ── a template missing from the plugin refuses that file, and the rest are copied ─────
    # The first file's template is the one missing, so the two after it show the loop going on.
    $plugin = Join-Path $scratch 'plugin-without-issue-form'
    foreach ($rel in 'bin/Install-Ouro.ps1', 'bin/New-OuroLabels.ps1', 'bin/ouro-binding.py', 'templates/ouro.toml.example', 'templates/docs-freshness.yml',
            'templates/issue-template-config.yml', 'templates/issue-intake-label.yml') {
        Write-Fixture $plugin $rel (Read-Root $rel)
    }
    $repo = New-Fixture @{ '.claude/ouro.toml' = $BoundBinding }
    $run = Invoke-Installer $repo @('-IssueIntake') -Script (Join-Path $plugin 'bin/Install-Ouro.ps1')
    Assert-Equal 0 $run.Code 'a plugin without templates/work-item.yml: the run exits 0'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^REFUSED: \.github/ISSUE_TEMPLATE/work-item\.yml not written - templates/work-item\.yml not found in the plugin at ') `
        'and a REFUSED line names the issue form and the missing template'
    foreach ($rel in '.github/ISSUE_TEMPLATE/config.yml', '.github/workflows/issue-intake-label.yml') {
        Assert-CopyOf $repo $rel "and $rel is written"
    }
    Assert-Equal '<absent>' (Read-FixtureHex $repo '.github/ISSUE_TEMPLATE/work-item.yml') 'and no .github/ISSUE_TEMPLATE/work-item.yml is written'

    # ── a template that cannot be read refuses that file before ShouldProcess ─────────────
    # A lock that shares nothing blocks the installer's read, which runs in its own process. The
    # case first reads the locked file itself, and skips where the lock does not block that read.
    $plugin = Join-Path $scratch 'plugin-locked-intake-workflow'
    foreach ($rel in 'bin/Install-Ouro.ps1', 'bin/New-OuroLabels.ps1', 'bin/ouro-binding.py', 'templates/ouro.toml.example', 'templates/docs-freshness.yml',
            'templates/work-item.yml', 'templates/issue-template-config.yml', 'templates/issue-intake-label.yml') {
        Write-Fixture $plugin $rel (Read-Root $rel)
    }
    $lockedTemplate = Join-Path $plugin 'templates/issue-intake-label.yml'
    $templateLock = [System.IO.File]::Open($lockedTemplate, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::None)
    try {
        $templateReadable = try { $null = [System.IO.File]::ReadAllBytes($lockedTemplate); $true } catch { $false }
        if ($templateReadable) { Write-Host '  skip: a lock that shares nothing does not block a read here' -ForegroundColor DarkGray }
        else {
            $unreadable = '^REFUSED: \.github/workflows/issue-intake-label\.yml not written - templates/issue-intake-label\.yml cannot be read \(.+\)$'
            $repo = New-Fixture @{ '.claude/ouro.toml' = $BoundBinding }
            $run = Invoke-Installer $repo @('-IssueIntake') -Script (Join-Path $plugin 'bin/Install-Ouro.ps1')
            Assert-Equal 0 $run.Code 'a template that cannot be read: the run exits 0'
            Assert-Equal 1 (Get-MatchingLineCount $run.Out $unreadable) 'and a REFUSED line says the template cannot be read'
            Assert-Equal $false (Test-Path -LiteralPath (Join-Path $repo '.github/workflows')) 'and no .github/workflows directory is left behind'
            foreach ($rel in '.github/ISSUE_TEMPLATE/work-item.yml', '.github/ISSUE_TEMPLATE/config.yml') {
                Assert-CopyOf $repo $rel "and $rel is written"
            }
            $repo = New-Fixture @{ '.claude/ouro.toml' = $BoundBinding }
            $before = Get-TreeState $repo
            $run = Invoke-Installer $repo @('-IssueIntake', '-WhatIf') -Script (Join-Path $plugin 'bin/Install-Ouro.ps1')
            Assert-Equal 1 (Get-MatchingLineCount $run.Out $unreadable) 'and -WhatIf prints the same REFUSED line'
            Assert-Equal 0 (Get-MatchingLineCount $run.Out 'What if:.*issue-intake-label') 'and plans no write for it'
            Assert-Equal $before (Get-TreeState $repo) 'and the tree is unchanged (a template that cannot be read, -WhatIf)'
        }
    }
    finally { $templateLock.Dispose() }

    # ── gitignore: **/.claude/* ignores both paths with neither string present ────────────
    $ignore = "**/.claude/*`n"
    $repo = New-Fixture @{ '.gitignore' = $ignore }
    $run = Invoke-Installer $repo
    Assert-Equal $ignore (Read-Fixture $repo '.gitignore') '**/.claude/* already ignores both paths: nothing is appended'
    Assert-Equal 2 (Get-MatchingLineCount $run.Out '^skip: .* is already ignored') 'both entries are reported as already ignored'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^WARNING: \.claude/ouro\.toml is ignored') 'the run warns that the rule also ignores the new binding'
    Assert-Equal 2 (Get-MatchingLineCount $run.Out '^skip: .* is already ignored \(\.gitignore\)') 'and each skip line names .gitignore as the source'

    # ── gitignore: .git/info/exclude and core.excludesFile reach this clone only ──────────
    Write-Fixture $scratch 'excludes-clone-local' ".claude/ouro.local.toml`n"
    $repo = New-Fixture @{ '.git/info/exclude' = ".claude/worktrees/`n" } -Excludes (Join-Path $scratch 'excludes-clone-local')
    $run = Invoke-Installer $repo
    Assert-Equal ".claude/worktrees/`n.claude/ouro.local.toml`n" (Read-Fixture $repo '.gitignore') `
        'an entry only .git/info/exclude or core.excludesFile ignores is still appended'
    Assert-Equal 0 (Get-MatchingLineCount $run.Out '^skip: .* is already ignored') 'and neither is reported as already ignored'

    # ── gitignore: a clone-local rule on .claude/ hides no .gitignore rule beneath it ──────
    Write-Fixture $scratch 'excludes-parent' ".claude/`n"
    $ignore = ".claude/worktrees/`n.claude/ouro.local.toml`n"
    $repo = New-Fixture @{ '.gitignore' = $ignore; '.git/info/exclude' = ".claude/`n" } -Excludes (Join-Path $scratch 'excludes-parent')
    $run = Invoke-Installer $repo
    $rerun = Invoke-Installer $repo
    Assert-Equal $ignore (Read-Fixture $repo '.gitignore') `
        'a .claude/ rule in .git/info/exclude and core.excludesFile over both entries: two runs append nothing'
    Assert-Equal 2 (Get-MatchingLineCount $rerun.Out '^skip: .* is already ignored \(\.gitignore\)') 'and the second run names .gitignore for both entries'

    # ── gitignore: a machine's init.templateDir takes no part ─────────────────────────────
    Write-Fixture $scratch 'template/info/exclude' ".claude/`n"
    $repo = New-Fixture
    $run = Invoke-Installer $repo -Environment @{ GIT_CONFIG_COUNT = '1'; GIT_CONFIG_KEY_0 = 'init.templateDir'
        GIT_CONFIG_VALUE_0 = ((Join-Path $scratch 'template') -replace '\\', '/') }
    Assert-Equal ".claude/worktrees/`n.claude/ouro.local.toml`n" (Read-Fixture $repo '.gitignore') `
        'an init.templateDir whose info/exclude holds .claude/: both entries are still appended'
    Assert-Equal 0 (Get-MatchingLineCount $run.Out '^skip: .* is already ignored') 'and neither is reported as already ignored'

    # ── gitignore: a negated entry is not ignored ─────────────────────────────────────────
    $ignore = ".claude/*`n!.claude/ouro.local.toml`n"
    $repo = New-Fixture @{ '.gitignore' = $ignore }
    $run = Invoke-Installer $repo
    Assert-Equal "${ignore}.claude/ouro.local.toml`n" (Read-Fixture $repo '.gitignore') `
        '.claude/* then !.claude/ouro.local.toml: the negated entry is appended'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^skip: \.claude/worktrees/ is already ignored \(\.gitignore\)') `
        'and .claude/worktrees/ is skipped, naming .gitignore'
    Assert-Equal 0 (Get-MatchingLineCount $run.Out '^skip: \.claude/ouro\.local\.toml') 'and .claude/ouro.local.toml is not skipped'

    # ── gitignore: ? and ?? match the line break piped after an entry, not the entry ──────
    $ignore = "?`n??`n"
    $repo = New-Fixture @{ '.gitignore' = $ignore }
    $run = Invoke-Installer $repo
    Assert-Equal "${ignore}.claude/worktrees/`n.claude/ouro.local.toml`n" (Read-Fixture $repo '.gitignore') `
        'a .gitignore of ? and ??: both entries are appended'
    Assert-Equal 0 (Get-MatchingLineCount $run.Out '^skip: .* is already ignored') 'and neither is reported as already ignored'

    # ── gitignore: a negation in a deeper .gitignore outranks any root line ──────────────
    # The negated directory gets no record from git at all; the negated file gets a negation record.
    $ignore = ".claude/*`n!.claude/.gitignore`n"
    $repo = New-Fixture @{ '.gitignore' = $ignore; '.claude/.gitignore' = "!*.toml`n!worktrees/`n" }
    $run = Invoke-Installer $repo
    $rerun = Invoke-Installer $repo
    Assert-Equal "${ignore}.claude/worktrees/`n.claude/ouro.local.toml`n" (Read-Fixture $repo '.gitignore') `
        'a .claude/.gitignore negating *.toml and worktrees/: two runs append each entry once'
    Assert-Equal 2 (Get-MatchingLineCount $rerun.Out '^WARNING: \.claude/(worktrees/|ouro\.local\.toml) is in \.gitignore but git does not ignore it') `
        'and the second run warns for both instead of appending again'

    # ── gitignore: a line git reads with a trailing tab does not hold the entry ───────────
    # git drops one CR and then the trailing spaces from each line, and nothing else.
    $TAB = [string][char]9; $CR = [string][char]13; $LF = [string][char]10
    $bytes = $Utf8.GetBytes(".claude/worktrees/$TAB$LF")
    $repo = New-Fixture
    [System.IO.File]::WriteAllBytes((Join-Path $repo '.gitignore'), $bytes)
    $run = Invoke-Installer $repo
    $afterFirst = Get-Hex ([System.IO.File]::ReadAllBytes((Join-Path $repo '.gitignore')))
    $rerun = Invoke-Installer $repo
    Assert-Equal (Get-Hex ($bytes + $Utf8.GetBytes(".claude/worktrees/$LF.claude/ouro.local.toml$LF"))) $afterFirst `
        '.claude/worktrees/ then a tab: the first run appends the entry'
    Assert-Equal 1 (Get-MatchingLineCount $rerun.Out '^skip: \.claude/worktrees/ is already ignored') 'and the second run reports it already ignored'
    Assert-Equal 0 (Get-MatchingLineCount ($run.Out + $rerun.Out) '^WARNING: \.claude/worktrees/') 'and no WARNING names it'

    # ── gitignore: an empty pattern ignores no worktree beneath .claude/worktrees/ ────────
    # git reads a blank line in a CRLF file, or a line of only spaces, as an empty pattern:
    # check-ignore answers .claude/worktrees/ itself from it, and it ignores nothing.
    foreach ($case in @(@{ Eol = $CR + $LF; Blank = ''; Shown = 'a CRLF blank line' }, @{ Eol = $LF; Blank = '   '; Shown = 'three spaces (LF)' })) {
        $eol = $case.Eol
        $bytes = $Utf8.GetBytes("node_modules/$eol$($case.Blank)${eol}build/$eol")
        $repo = New-Fixture
        [System.IO.File]::WriteAllBytes((Join-Path $repo '.gitignore'), $bytes)
        $run = Invoke-Installer $repo
        $afterFirst = Get-Hex ([System.IO.File]::ReadAllBytes((Join-Path $repo '.gitignore')))
        $rerun = Invoke-Installer $repo
        Assert-Equal (Get-Hex ($bytes + $Utf8.GetBytes(".claude/worktrees/$eol.claude/ouro.local.toml$eol"))) $afterFirst `
            "node_modules/, $($case.Shown), build/: the first run appends .claude/worktrees/"
        Assert-Equal 1 (Get-MatchingLineCount $rerun.Out '^skip: \.claude/worktrees/ is already ignored \(\.gitignore\)') `
            "and the second run reports it already ignored ($($case.Shown))"
    }

    # ── gitignore: .claude/worktrees/* and a CRLF blank line hold the entry ───────────────
    # The directory is ignored here, and asked about itself git names the empty pattern, not the rule.
    $bytes = $Utf8.GetBytes(".claude/worktrees/*$CR$LF$CR$LF")
    $repo = New-Fixture
    [System.IO.File]::WriteAllBytes((Join-Path $repo '.gitignore'), $bytes)
    $run = Invoke-Installer $repo
    $rerun = Invoke-Installer $repo
    $skip = '^skip: \.claude/worktrees/ is already ignored \(\.gitignore\)'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out $skip) '.claude/worktrees/* then a CRLF blank line: the first run reports .claude/worktrees/ already ignored'
    Assert-Equal 1 (Get-MatchingLineCount $rerun.Out $skip) 'and the second run reports it already ignored (.claude/worktrees/*)'
    Assert-Equal (Get-Hex ($bytes + $Utf8.GetBytes(".claude/ouro.local.toml$CR$LF"))) (Get-Hex ([System.IO.File]::ReadAllBytes((Join-Path $repo '.gitignore')))) `
        'and the file never gains a .claude/worktrees/ line (.claude/worktrees/*)'

    # ── gitignore: a rule ending in a slash applies to the worktree directory ─────────────
    # git applies such a rule only to a path it sees as a directory: a negation of directories
    # un-ignores the worktree git would add, and a directory-only rule ignores it.
    $skip = '^skip: \.claude/worktrees/ is already ignored \(\.gitignore\)'
    foreach ($case in @(@{ Ignore = "*`n!*.*`n!*/`n"; Shown = '*, !*.*, !*/' },
            @{ Ignore = ".claude/worktrees/*`n!.claude/worktrees/*/`n"; Shown = '.claude/worktrees/*, !.claude/worktrees/*/' })) {
        $repo = New-Fixture @{ '.gitignore' = $case.Ignore }
        $run = Invoke-Installer $repo
        $afterFirst = Read-Fixture $repo '.gitignore'
        $rerun = Invoke-Installer $repo
        Assert-Equal "$($case.Ignore).claude/worktrees/`n.claude/ouro.local.toml`n" $afterFirst `
            "$($case.Shown): the first run appends .claude/worktrees/"
        Assert-Equal 1 (Get-MatchingLineCount $rerun.Out $skip) "and the second run reports it already ignored ($($case.Shown))"
    }
    $ignore = ".claude/worktrees/*/`n"
    $repo = New-Fixture @{ '.gitignore' = $ignore }
    $run = Invoke-Installer $repo
    Assert-Equal 1 (Get-MatchingLineCount $run.Out $skip) '.claude/worktrees/*/: the first run reports .claude/worktrees/ already ignored'
    Assert-Equal "${ignore}.claude/ouro.local.toml`n" (Read-Fixture $repo '.gitignore') 'and appends no line for it (.claude/worktrees/*/)'
    $repo = New-Fixture @{ '.gitignore' = "*`n!*.*`n!*/`n" }
    $before = Get-TreeState $repo
    $run = Invoke-Installer $repo @('-WhatIf')
    Assert-Equal 0 $run.Code '*, !*.*, !*/ with -WhatIf: the run exits 0'
    Assert-Equal 0 (Get-MatchingLineCount $run.Out '^skip: \.claude/worktrees/ is already ignored') `
        'and asks the question a real run asks: no line reports .claude/worktrees/ already ignored'
    Assert-Equal $before (Get-TreeState $repo) 'and the tree is unchanged (*, !*.*, !*/ with -WhatIf)'
    $repo = New-Fixture @{ '.gitignore' = $ignore }
    $before = Get-TreeState $repo
    $run = Invoke-Installer $repo @('-WhatIf')
    Assert-Equal 1 (Get-MatchingLineCount $run.Out $skip) `
        '.claude/worktrees/*/ with -WhatIf: the question reads the copied .gitignore and reports .claude/worktrees/ already ignored'
    Assert-Equal 0 (Get-MatchingLineCount $run.Out 'What if:.*Append \.claude/worktrees/') 'and plans no append for it'
    Assert-Equal $before (Get-TreeState $repo) 'and the tree is unchanged (.claude/worktrees/*/ with -WhatIf)'

    # ── gitignore: a .gitignore inside .claude/worktrees/ holds the entry ─────────────────
    $ignore = "node_modules/`n"
    $repo = New-Fixture @{ '.gitignore' = $ignore; '.claude/worktrees/.gitignore' = "*`n" }
    $run = Invoke-Installer $repo
    $rerun = Invoke-Installer $repo
    $skipDeep = '^skip: \.claude/worktrees/ is already ignored \(\.claude/worktrees/\.gitignore\)'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out $skipDeep) `
        'node_modules/ and a .claude/worktrees/.gitignore of *: the first run reports .claude/worktrees/ already ignored, naming that file'
    Assert-Equal 1 (Get-MatchingLineCount $rerun.Out $skipDeep) 'and so does the second'
    Assert-Equal "${ignore}.claude/ouro.local.toml`n" (Read-Fixture $repo '.gitignore') 'and neither run appends .claude/worktrees/'

    # ── gitignore: a UTF-16 .gitignore holds no line git reads ────────────────────────────
    # It holds .claude/ouro.local.toml, and git reads every line after the first as an empty
    # pattern, which ignores neither entry.
    $utf16 = [System.Text.UnicodeEncoding]::new($false, $true)
    $bytes = [byte[]]($utf16.GetPreamble() + $utf16.GetBytes("node_modules/$LF.claude/ouro.local.toml$LF"))
    $repo = New-Fixture
    [System.IO.File]::WriteAllBytes((Join-Path $repo '.gitignore'), $bytes)
    $run = Invoke-Installer $repo
    $rerun = Invoke-Installer $repo
    Assert-Equal (Get-Hex ($bytes + $Utf8.GetBytes("$LF.claude/worktrees/$LF.claude/ouro.local.toml$LF"))) (Get-Hex ([System.IO.File]::ReadAllBytes((Join-Path $repo '.gitignore')))) `
        'a UTF-16LE .gitignore holding .claude/ouro.local.toml: two runs append each entry once, after a line break'
    Assert-Equal 1 (Get-MatchingLineCount $rerun.Out '^skip: \.claude/ouro\.local\.toml is already ignored') 'and the second run reports it already ignored'

    # ── gitignore: a byte-order mark, a trailing space and CRLF still hold the entry ──────
    $bytes = [byte[]]([byte[]](0xEF, 0xBB, 0xBF) + $Utf8.GetBytes(".claude/worktrees/ $CR$LF!.claude/worktrees/$CR$LF"))
    $repo = New-Fixture
    [System.IO.File]::WriteAllBytes((Join-Path $repo '.gitignore'), $bytes)
    $run = Invoke-Installer $repo
    $rerun = Invoke-Installer $repo
    $warning = '^WARNING: \.claude/worktrees/ is in \.gitignore but git does not ignore it'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out $warning) 'a byte-order mark, .claude/worktrees/ and a space, CRLF, then its negation: the first run warns'
    Assert-Equal 1 (Get-MatchingLineCount $rerun.Out $warning) 'and so does the second'
    Assert-Equal (Get-Hex ($bytes + $Utf8.GetBytes(".claude/ouro.local.toml$CR$LF"))) (Get-Hex ([System.IO.File]::ReadAllBytes((Join-Path $repo '.gitignore')))) `
        'and the file never gains a second .claude/worktrees/ line'

    # ── gitignore: a root .gitignore that cannot be read refuses each entry ───────────────
    $repo = New-Fixture
    New-Item -ItemType Directory -Path (Join-Path $repo '.gitignore') | Out-Null
    $before = Get-TreeState $repo
    $run = Invoke-Installer $repo @('-WhatIf')
    $rerun = Invoke-Installer $repo @('-WhatIf')
    Assert-Equal 0 $run.Code 'a .gitignore that is a directory, -WhatIf: the run exits 0'
    Assert-Equal 0 $rerun.Code 'and so does a second'
    foreach ($entry in '.claude/worktrees/', '.claude/ouro.local.toml') {
        $refused = "^REFUSED: $([regex]::Escape($entry)) not appended to \.gitignore - it cannot be read \(.+\)$"
        Assert-Equal 1 (Get-MatchingLineCount $run.Out $refused) "a REFUSED line names $entry"
        Assert-Equal 1 (Get-MatchingLineCount $rerun.Out $refused) 'and the second run names it again'
    }
    Assert-Equal $before (Get-TreeState $repo) 'and the tree is unchanged'

    # ── gitignore: a root .gitignore that cannot be written refuses each entry ────────────
    # IsReadOnly is the read-only attribute on Windows and clears the write bits on Linux, where
    # root opens the file anyway, so the case first opens it for writing itself.
    $ignore = "node_modules/`n"
    $repo = New-Fixture @{ '.gitignore' = $ignore }
    $whatIfRepo = New-Fixture @{ '.gitignore' = $ignore }
    $lockedIgnores = @((Join-Path $repo '.gitignore'), (Join-Path $whatIfRepo '.gitignore'))
    try {
        foreach ($p in $lockedIgnores) { (Get-Item -Force -LiteralPath $p).IsReadOnly = $true }
        $writable = try { [System.IO.FileStream]::new($lockedIgnores[0], [System.IO.FileMode]::Open, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite).Dispose(); $true } catch { $false }
        if ($writable) { Write-Host '  skip: cannot make a file unwritable here (root on POSIX ignores the mode)' -ForegroundColor DarkGray }
        else {
            $bytes = [System.IO.File]::ReadAllBytes($lockedIgnores[0])
            $run = Invoke-Installer $repo @('-DocsFreshness') -Gh $Resolved
            Assert-Equal 0 $run.Code 'a .gitignore that cannot be written: the run exits 0'
            foreach ($entry in '.claude/worktrees/', '.claude/ouro.local.toml') {
                $refused = "^REFUSED: $([regex]::Escape($entry)) not appended to \.gitignore - it cannot be written \(.+\)$"
                Assert-Equal 1 (Get-MatchingLineCount $run.Out $refused) "a REFUSED line names $entry"
            }
            Assert-Equal (Get-Hex $bytes) (Get-Hex ([System.IO.File]::ReadAllBytes($lockedIgnores[0]))) 'and the .gitignore bytes are unchanged'
            Assert-Equal $Filled (Read-Fixture $repo $WorkflowRel) 'and the later steps run: the workflow is the filled template'
            Assert-Equal $true (@($run.Gh -match '^label create ').Count -gt 0) 'and labels are created'
            $before = Get-TreeState $whatIfRepo
            $run = Invoke-Installer $whatIfRepo @('-DocsFreshness', '-WhatIf') -Gh $Resolved
            Assert-Equal 0 $run.Code 'a .gitignore that cannot be written, -WhatIf: the run exits 0'
            foreach ($entry in '.claude/worktrees/', '.claude/ouro.local.toml') {
                $refused = "^REFUSED: $([regex]::Escape($entry)) not appended to \.gitignore - it cannot be written \(.+\)$"
                Assert-Equal 1 (Get-MatchingLineCount $run.Out $refused) "and -WhatIf prints the REFUSED line a real run prints for $entry"
            }
            Assert-Equal 0 (Get-MatchingLineCount $run.Out 'What if:.*Append ') 'and plans no append'
            Assert-Equal $before (Get-TreeState $whatIfRepo) 'and the tree is unchanged (a .gitignore that cannot be written, -WhatIf)'
        }
    }
    finally {
        foreach ($p in $lockedIgnores) { (Get-Item -Force -LiteralPath $p).IsReadOnly = $false }
    }

    # ── a .github/workflows that is a regular file refuses the workflow ───────────────────
    # A file where the directory should be refuses the write on every OS and for every user.
    $repo = New-Fixture @{ '.github/workflows' = "not a directory`n" }
    $run = Invoke-Installer $repo @('-DocsFreshness') -Gh $Resolved
    Assert-Equal 0 $run.Code 'a .github/workflows that is a regular file: the run exits 0'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^REFUSED: \.github/workflows/docs-freshness\.yml not written - the write failed \(.+\)$') `
        'and a REFUSED line says the workflow write failed'
    Assert-Equal 0 (Get-MatchingLineCount $run.Out 'the Action is pinned at') 'and no Action-pinned line prints'
    Assert-Equal 'exit=0 ok' (Test-Binding $repo) 'and the binding is written'
    Assert-Equal ".claude/worktrees/`n.claude/ouro.local.toml`n" (Read-Fixture $repo '.gitignore') 'and both gitignore entries are written'
    Assert-Equal $true (@($run.Gh -match '^label create ').Count -gt 0) 'and labels are created'

    # ── a .github/workflows that is a regular file refuses the intake workflow ────────────
    $repo = New-Fixture @{ '.github/workflows' = "not a directory`n" }
    $run = Invoke-Installer $repo @('-IssueIntake')
    Assert-Equal 0 $run.Code 'a .github/workflows that is a regular file, -IssueIntake: the run exits 0'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^REFUSED: \.github/workflows/issue-intake-label\.yml not written - the write failed \(.+\)$') `
        'and a REFUSED line says the intake workflow write failed'
    foreach ($rel in '.github/ISSUE_TEMPLATE/work-item.yml', '.github/ISSUE_TEMPLATE/config.yml') {
        Assert-CopyOf $repo $rel "and $rel is written"
    }

    # ── a .claude that is a regular file refuses the binding ──────────────────────────────
    $repo = New-Fixture @{ '.claude' = "not a directory`n" }
    $run = Invoke-Installer $repo
    Assert-Equal 0 $run.Code 'a .claude that is a regular file, gh failing: the run exits 0'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^REFUSED: \.claude/ouro\.toml not written - the write failed \(.+\)$') `
        'and a REFUSED line says the binding write failed'
    Assert-Equal 0 (Get-MatchingLineCount $run.Out '^WARNING: .*placeholder') 'and no placeholder WARNING prints for the binding not written'
    Assert-Equal ".claude/worktrees/`n.claude/ouro.local.toml`n" (Read-Fixture $repo '.gitignore') 'and both gitignore entries are appended'

    # ── gitignore: a root that refuses a new file refuses each entry ──────────────────────
    # chmod 555 on POSIX, a deny of write data (add file) on Windows. Root ignores the mode, so the
    # case first creates a file there itself.
    $repo = New-Fixture @{ '.claude/ouro.toml' = $BoundBinding }
    $me = if ($IsWindows) { [System.Security.Principal.WindowsIdentity]::GetCurrent().Name } else { '' }
    try {
        if ($IsWindows) { icacls $repo /deny "${me}:(WD)" | Out-Null } else { chmod 555 $repo }
        $probe = Join-Path $repo 'write-probe'
        $creatable = try { [System.IO.File]::WriteAllText($probe, ''); $true } catch { $false }
        if ($creatable) {
            Remove-Item -LiteralPath $probe -Force
            Write-Host '  skip: cannot make a directory refuse a new file here (root on POSIX ignores the mode)' -ForegroundColor DarkGray
        }
        else {
            $run = Invoke-Installer $repo -Gh $Resolved
            Assert-Equal 0 $run.Code 'a root that refuses a new file, no .gitignore: the run exits 0'
            foreach ($entry in '.claude/worktrees/', '.claude/ouro.local.toml') {
                $refused = "^REFUSED: $([regex]::Escape($entry)) not appended to \.gitignore - it cannot be written \(.+\)$"
                Assert-Equal 1 (Get-MatchingLineCount $run.Out $refused) "and a REFUSED line names $entry"
            }
        }
    }
    finally {
        if ($IsWindows) { icacls $repo /remove:d $me | Out-Null } else { chmod 755 $repo }
    }

    # ── gitignore: a CR then a space, or two CRs, leave the entry's line unread by git ────
    foreach ($tail in @(($CR + ' '), ($CR + $CR))) {
        $shown = Get-Hex ($Utf8.GetBytes($tail))
        $bytes = $Utf8.GetBytes(".claude/worktrees/$tail$LF")
        $repo = New-Fixture
        [System.IO.File]::WriteAllBytes((Join-Path $repo '.gitignore'), $bytes)
        $run = Invoke-Installer $repo
        $rerun = Invoke-Installer $repo
        # The installer appends with CRLF when the file already holds one.
        $eol = if (($tail + $LF).Contains($CR + $LF)) { $CR + $LF } else { $LF }
        Assert-Equal (Get-Hex ($bytes + $Utf8.GetBytes(".claude/worktrees/$eol.claude/ouro.local.toml$eol"))) (Get-Hex ([System.IO.File]::ReadAllBytes((Join-Path $repo '.gitignore')))) `
            ".claude/worktrees/ then bytes $shown, two runs append the entry once"
        Assert-Equal 0 (Get-MatchingLineCount ($run.Out + $rerun.Out) '^WARNING: \.claude/worktrees/') "and no WARNING names it (bytes $shown)"
    }

    # ── gitignore: git skips one byte-order mark, not two ─────────────────────────────────
    $bytes = [byte[]]([byte[]](0xEF, 0xBB, 0xBF, 0xEF, 0xBB, 0xBF) + $Utf8.GetBytes(".claude/worktrees/$LF"))
    $repo = New-Fixture
    [System.IO.File]::WriteAllBytes((Join-Path $repo '.gitignore'), $bytes)
    $run = Invoke-Installer $repo
    $rerun = Invoke-Installer $repo
    Assert-Equal (Get-Hex ($bytes + $Utf8.GetBytes(".claude/worktrees/$LF.claude/ouro.local.toml$LF"))) (Get-Hex ([System.IO.File]::ReadAllBytes((Join-Path $repo '.gitignore')))) `
        'two byte-order marks before .claude/worktrees/: two runs append the entry once'
    Assert-Equal 0 (Get-MatchingLineCount ($run.Out + $rerun.Out) '^WARNING: \.claude/worktrees/') 'and no WARNING names it (two byte-order marks)'

    # ── gitignore: git reads a pattern up to its first NUL ────────────────────────────────
    $NUL = [string][char]0
    $bytes = $Utf8.GetBytes(".claude/worktrees/$NUL$LF")
    $repo = New-Fixture @{ '.claude/.gitignore' = "!worktrees/`n" }
    [System.IO.File]::WriteAllBytes((Join-Path $repo '.gitignore'), $bytes)
    $run = Invoke-Installer $repo
    $rerun = Invoke-Installer $repo
    $warning = '^WARNING: \.claude/worktrees/ is in \.gitignore but git does not ignore it'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out $warning) '.claude/worktrees/ then a NUL, negated deeper: the first run warns'
    Assert-Equal 1 (Get-MatchingLineCount $rerun.Out $warning) 'and so does the second'
    Assert-Equal (Get-Hex ($bytes + $Utf8.GetBytes(".claude/ouro.local.toml$LF"))) (Get-Hex ([System.IO.File]::ReadAllBytes((Join-Path $repo '.gitignore')))) `
        'and the file never gains a second .claude/worktrees/ line'

    # ── gitignore: a text ending in a lone CR gets an LF, not a CRLF, before the append ───
    $bytes = $Utf8.GetBytes("node_modules/$CR$LF.claude/worktrees/$CR")
    $repo = New-Fixture
    [System.IO.File]::WriteAllBytes((Join-Path $repo '.gitignore'), $bytes)
    $run = Invoke-Installer $repo
    $rerun = Invoke-Installer $repo
    Assert-Equal (Get-Hex ($bytes + $Utf8.GetBytes("$LF.claude/ouro.local.toml$CR$LF"))) (Get-Hex ([System.IO.File]::ReadAllBytes((Join-Path $repo '.gitignore')))) `
        'a CRLF file ending in a lone CR: the append leaves the last line as git read it'
    Assert-Equal 2 (Get-MatchingLineCount $rerun.Out '^skip: .* is already ignored') 'and the second run finds both entries ignored'

    # ── gitignore: the CR is dropped before the line is cut at a NUL ──────────────────────
    # A CR then a NUL leaves a line git reads as the entry plus a CR, which holds nothing.
    $bytes = $Utf8.GetBytes(".claude/worktrees/$CR$NUL$LF")
    $repo = New-Fixture @{ '.claude/.gitignore' = "!worktrees/`n" }
    [System.IO.File]::WriteAllBytes((Join-Path $repo '.gitignore'), $bytes)
    $run = Invoke-Installer $repo
    $rerun = Invoke-Installer $repo
    $warning = '^WARNING: \.claude/worktrees/ is in \.gitignore but git does not ignore it'
    Assert-Equal (Get-Hex ($bytes + $Utf8.GetBytes(".claude/worktrees/$LF.claude/ouro.local.toml$LF"))) (Get-Hex ([System.IO.File]::ReadAllBytes((Join-Path $repo '.gitignore')))) `
        '.claude/worktrees/ then a CR and a NUL, negated deeper: two runs append the entry once'
    Assert-Equal 0 (Get-MatchingLineCount $run.Out $warning) 'and the first run does not warn'
    Assert-Equal 1 (Get-MatchingLineCount $rerun.Out $warning) 'and the second run warns about the line it appended'

    # ── gitignore: a .gitignore that is a symbolic link is refused; git does not read one ──
    $repo = New-Fixture
    [System.IO.File]::WriteAllBytes((Join-Path $repo 'real-ignore'), $Utf8.GetBytes(".claude/worktrees/$LF.claude/ouro.local.toml$LF"))
    $linked = try { New-Item -ItemType SymbolicLink -Path (Join-Path $repo '.gitignore') -Target (Join-Path $repo 'real-ignore') -ErrorAction Stop | Out-Null; $true } catch { $false }
    if (-not $linked) { Write-Host '  skip: cannot create a symbolic link here (Windows without the privilege)' -ForegroundColor DarkGray }
    else {
        $run = Invoke-Installer $repo
        Assert-Equal 0 $run.Code 'a .gitignore that is a symbolic link: the run exits 0'
        Assert-Equal 2 (Get-MatchingLineCount $run.Out '^REFUSED: .* not appended to \.gitignore - it is a symbolic link') 'and a REFUSED line names each entry'
        Assert-Equal 0 (Get-MatchingLineCount $run.Out '^WARNING: \.claude/(worktrees/|ouro\.local\.toml) ') 'and no WARNING blames a negation'
    }

    # ── a destination that is a symbolic link to a MISSING target is refused, not written ──
    # The link that resolves is visible to any link API; this one is not, and it is the one a write
    # follows out of the repository. Only a runner that can create a symbolic link runs these, so
    # on Windows without the privilege each prints the skip line and asserts nothing.
    $noLink = '  skip: cannot create a symbolic link here (Windows without the privilege)'
    $symlinked = '^REFUSED: {0} not written - it is a symbolic link, which git does not read$'

    # Two earlier fixes failed here and both failed the same way: they asked whether the target
    # existed, via [System.IO.File]::Exists, which on Linux returns TRUE for a dangling link -- so
    # "the path is there but its target is not" is not expressible with it and every such predicate
    # answered "not a link". The installer reads the link itself instead, with Get-Item -Force.
    # No separate assertion guards that probe: one calling Get-Item directly passes even when the
    # installer's own helper is dead (measured, by stubbing it to return $false). The blocks below
    # are the guard -- each drives the real installer, and that mutation turns five of them red.

    $repo = New-Fixture
    if (-not (New-DanglingLink $repo '.gitignore' 'real-ignore')) { Write-Host $noLink -ForegroundColor DarkGray }
    else {
        $run = Invoke-Installer $repo
        Assert-Equal 0 $run.Code 'a .gitignore that is a symbolic link to a missing target: the run exits 0'
        Assert-Equal 2 (Get-MatchingLineCount $run.Out '^REFUSED: .* not appended to \.gitignore - it is a symbolic link') 'and a REFUSED line names each entry'
        Assert-Equal 0 (Get-MatchingLineCount $run.Out '^wrote: .* appended to \.gitignore$') 'and no wrote: line names .gitignore'
        Assert-Equal $false (Test-Path -LiteralPath (Join-Path $repo 'real-ignore')) 'and the link target is still absent'
    }

    # The same with the target's directory missing too: the append throws there, so the REFUSED
    # line must still name the link rather than the write that failed.
    $repo = New-Fixture
    if (-not (New-DanglingLink $repo '.gitignore' 'nodir/real-ignore')) { Write-Host $noLink -ForegroundColor DarkGray }
    else {
        $run = Invoke-Installer $repo
        Assert-Equal 0 $run.Code 'a .gitignore symbolic link whose target directory is missing too: the run exits 0'
        Assert-Equal 2 (Get-MatchingLineCount $run.Out '^REFUSED: .* not appended to \.gitignore - it is a symbolic link') 'and a REFUSED line names each entry'
        Assert-Equal 0 (Get-MatchingLineCount $run.Out '^REFUSED: .* it cannot be (read|written)') 'and no REFUSED line blames a failed read or write'
        Assert-Equal $false (Test-Path -LiteralPath (Join-Path $repo 'nodir')) 'and the link target is still absent'
    }

    $repo = New-Fixture
    if (-not (New-DanglingLink $repo '.claude/ouro.toml' 'real-binding.toml')) { Write-Host $noLink -ForegroundColor DarkGray }
    else {
        $run = Invoke-Installer $repo -Gh $Resolved
        Assert-Equal 0 $run.Code 'a binding that is a symbolic link to a missing target: the run exits 0'
        Assert-Equal 1 (Get-MatchingLineCount $run.Out ($symlinked -f '\.claude/ouro\.toml')) 'and a REFUSED line names the binding'
        if ((Get-MatchingLineCount $run.Out ($symlinked -f '\.claude/ouro\.toml')) -ne 1) { Write-Host "  the installer printed:`n$($run.Out)" -ForegroundColor DarkGray }
        Assert-Equal $false (Test-Path -LiteralPath (Join-Path $repo 'real-binding.toml')) 'and the link target is still absent'
    }

    $repo = New-Fixture @{ '.claude/ouro.toml' = $BoundBinding }
    if (-not (New-DanglingLink $repo $WorkflowRel 'real-workflow.yml')) { Write-Host $noLink -ForegroundColor DarkGray }
    else {
        $run = Invoke-Installer $repo @('-DocsFreshness') -Gh $Resolved
        Assert-Equal 0 $run.Code 'a docs-freshness workflow that is a symbolic link to a missing target: the run exits 0'
        Assert-Equal 1 (Get-MatchingLineCount $run.Out ($symlinked -f [regex]::Escape($WorkflowRel))) 'and a REFUSED line names the workflow'
        if ((Get-MatchingLineCount $run.Out ($symlinked -f [regex]::Escape($WorkflowRel))) -ne 1) { Write-Host "  the installer printed:`n$($run.Out)" -ForegroundColor DarkGray }
        Assert-Equal $false (Test-Path -LiteralPath (Join-Path $repo 'real-workflow.yml')) 'and the link target is still absent'
    }

    $repo = New-Fixture
    $linked = @(foreach ($rel in $IntakeCopies.Keys) { New-DanglingLink $repo $rel "real-$(Split-Path $rel -Leaf)" }) -notcontains $false
    if (-not $linked) { Write-Host $noLink -ForegroundColor DarkGray }
    else {
        $run = Invoke-Installer $repo @('-IssueIntake') -Gh $Resolved
        Assert-Equal 0 $run.Code 'three intake files that are symbolic links to missing targets: the run exits 0'
        foreach ($rel in $IntakeCopies.Keys) {
            Assert-Equal 1 (Get-MatchingLineCount $run.Out ($symlinked -f [regex]::Escape($rel))) "and a REFUSED line names $rel"
            if ((Get-MatchingLineCount $run.Out ($symlinked -f [regex]::Escape($rel))) -ne 1) { Write-Host "  the installer printed:`n$($run.Out)" -ForegroundColor DarkGray }
            Assert-Equal $false (Test-Path -LiteralPath (Join-Path $repo "real-$(Split-Path $rel -Leaf)")) "and its link target is still absent ($rel)"
        }
    }

    # ── a directory on a destination's path that is a symbolic link is refused, not written through ──
    # New-Item -Force creates a destination's parents straight through a linked directory, and the
    # file lands wherever the link points. Each target here is a directory outside the fixture that
    # exists, as in the measured escape. The REFUSED line names the linked directory, not the file.
    $viaLink = '^REFUSED: {0} not written - {1} is a symbolic link, which git does not follow$'

    $repo = New-Fixture
    $outside = New-OutsideLink $repo '.github'
    if (-not $outside) { Write-Host $noLink -ForegroundColor DarkGray }
    else {
        $dry = Invoke-Installer $repo @('-DocsFreshness', '-IssueIntake', '-WhatIf') -Gh $Resolved
        $run = Invoke-Installer $repo @('-DocsFreshness', '-IssueIntake') -Gh $Resolved
        Assert-Equal 0 $run.Code '.github a symbolic link to a directory outside the tree: the run exits 0'
        foreach ($rel in @($WorkflowRel) + @($IntakeCopies.Keys)) {
            Assert-Equal 1 (Get-MatchingLineCount $run.Out ($viaLink -f [regex]::Escape($rel), '\.github')) "and a REFUSED line names .github for $rel"
            Assert-Equal 1 (Get-MatchingLineCount $dry.Out ($viaLink -f [regex]::Escape($rel), '\.github')) "and -WhatIf prints the same line for $rel"
        }
        if ((Get-MatchingLineCount $run.Out '^REFUSED: ') -ne 4) { Write-Host "  the installer printed:`n$($run.Out)" -ForegroundColor DarkGray }
        Assert-Equal '' (Get-TreeState $outside) 'and no file lands in the link target'
    }

    # One level deeper: the walk runs down to the destination's parent, and a destination beside
    # the linked directory is still written.
    $repo = New-Fixture
    $outside = New-OutsideLink $repo '.github/ISSUE_TEMPLATE'
    if (-not $outside) { Write-Host $noLink -ForegroundColor DarkGray }
    else {
        $run = Invoke-Installer $repo @('-IssueIntake') -Gh $Resolved
        Assert-Equal 0 $run.Code '.github/ISSUE_TEMPLATE a symbolic link to a directory outside the tree: the run exits 0'
        foreach ($rel in @($IntakeCopies.Keys)[0, 1]) {
            Assert-Equal 1 (Get-MatchingLineCount $run.Out ($viaLink -f [regex]::Escape($rel), '\.github/ISSUE_TEMPLATE')) "and a REFUSED line names .github/ISSUE_TEMPLATE for $rel"
        }
        if ((Get-MatchingLineCount $run.Out '^REFUSED: ') -ne 2) { Write-Host "  the installer printed:`n$($run.Out)" -ForegroundColor DarkGray }
        Assert-Equal '' (Get-TreeState $outside) 'and no file lands in the link target'
        $rel = @($IntakeCopies.Keys)[2]
        Assert-CopyOf $repo $rel "and $rel, beside it, is written"
    }

    $repo = New-Fixture
    $outside = New-OutsideLink $repo '.claude'
    if (-not $outside) { Write-Host $noLink -ForegroundColor DarkGray }
    else {
        $run = Invoke-Installer $repo -Gh $Resolved
        Assert-Equal 0 $run.Code '.claude a symbolic link to a directory outside the tree: the run exits 0'
        Assert-Equal 1 (Get-MatchingLineCount $run.Out ($viaLink -f '\.claude/ouro\.toml', '\.claude')) 'and a REFUSED line names .claude for the binding'
        if ((Get-MatchingLineCount $run.Out '^REFUSED: ') -ne 1) { Write-Host "  the installer printed:`n$($run.Out)" -ForegroundColor DarkGray }
        Assert-Equal '' (Get-TreeState $outside) 'and the binding does not land in the link target'
    }

    # A repo whose root is reached through a link, as under a symlinked home, installs as any other.
    # This pins the setup, not where the walk starts: the installer's root is git's, which resolves
    # the link, so a walk that also asked the root passes here too (measured).
    $repo = New-Fixture
    $rootLink = Join-Path $scratch "link$($script:fixtures)"
    $linked = try { New-Item -ItemType SymbolicLink -Path $rootLink -Target $repo -ErrorAction Stop | Out-Null; $true } catch { $false }
    if (-not $linked) { Write-Host $noLink -ForegroundColor DarkGray }
    else {
        $run = Invoke-Installer $rootLink @('-DocsFreshness', '-IssueIntake') -Gh $Resolved
        Assert-Equal 0 $run.Code 'a repo whose root is reached through a symbolic link: the run exits 0'
        Assert-Equal 0 (Get-MatchingLineCount $run.Out '^REFUSED: ') 'and nothing is refused'
        Assert-Equal 'slug = "o/n"' (Get-Line (Read-Fixture $repo '.claude/ouro.toml') 'slug') 'and the binding is written'
        Assert-Equal $Filled (Read-Fixture $repo $WorkflowRel) 'and the workflow is written'
        foreach ($rel in $IntakeCopies.Keys) {
            Assert-CopyOf $repo $rel "and $rel is written"
        }
    }

    # ── gitignore: a git init that fails leaves no temp git dir ───────────────────────────
    $initFailTemp = Join-Path $scratch 'temp-init-fail'
    New-Item -ItemType Directory -Force -Path $initFailTemp | Out-Null
    $repo = New-Fixture
    $run = Invoke-Installer $repo -Environment @{ GIT_DEFAULT_HASH = 'bogus'; TMP = $initFailTemp; TEMP = $initFailTemp; TMPDIR = $initFailTemp }
    Assert-Equal $true ($run.Code -ne 0) 'a git init that fails stops the run'
    Assert-Equal 0 @([System.IO.Directory]::GetFileSystemEntries($initFailTemp)).Count 'and leaves no temp git dir behind'

    # ── gitignore: a relative core.excludesFile is not a .gitignore ───────────────────────
    $repo = New-Fixture @{ 'my-excludes' = ".claude/worktrees/`n.claude/ouro.local.toml`n" } -Excludes 'my-excludes'
    $run = Invoke-Installer $repo
    Assert-Equal ".claude/worktrees/`n.claude/ouro.local.toml`n" (Read-Fixture $repo '.gitignore') `
        'an entry only a relative core.excludesFile ignores is still appended'

    # ── gitignore: a commented-out entry ignores nothing; the rest of the file is kept ────
    $ignore = "node_modules/`n# .claude/worktrees/`n.claude/ouro.local.toml"
    $repo = New-Fixture @{ '.gitignore' = $ignore }
    $run = Invoke-Installer $repo
    Assert-Equal "$ignore`n.claude/worktrees/`n" (Read-Fixture $repo '.gitignore') `
        'only the unignored entry is appended, on its own line, after the untouched file'

    # ── -WhatIf: every write planned, none made ───────────────────────────────────────────
    $repo = New-Fixture @{ '.gitignore' = "node_modules/`n" }
    $before = Get-TreeState $repo
    $run = Invoke-Installer $repo @('-DocsFreshness', '-CI', '-IssueIntake', '-WhatIf') -Gh $Resolved
    Assert-Equal 0 $run.Code '-WhatIf exits 0'
    Assert-Equal $before (Get-TreeState $repo) '-WhatIf leaves every file byte-identical, .gitignore included'
    Assert-Equal $true ($run.Out -match 'What if:.*ouro\.toml') '-WhatIf names the binding it would write'
    Assert-Equal $true ($run.Out -match 'What if:.*docs-freshness\.yml') '-WhatIf names the docs-freshness workflow it would write'
    Assert-Equal $true ($run.Out -match 'What if:.*ci\.yml') '-WhatIf names the CI workflow it would write'
    foreach ($rel in $IntakeCopies.Keys) {
        Assert-Equal $true ($run.Out -match "What if:.*$([regex]::Escape($rel) -replace '/', '[\\/]')") "-WhatIf names $rel"
    }
    Assert-Equal $true ($run.Out -match 'What if:.*gh label create') '-WhatIf reaches New-OuroLabels.ps1'
    Assert-Equal $true (@($run.Gh -match '^repo view').Count -gt 0) 'the gh stub still logs under -WhatIf, so the next check can fail'
    Assert-Equal 0 (@($run.Gh -match '^label create').Count) 'and no label create runs'

    # ── the clone check: -WhatIf against a committed binding reports what a clone lacks ────
    $declaredBinding = $BoundBinding + "`n[labels]`nscope = []`narea = [`"missing-area`"]`ntype = []`n"
    $repo = New-Fixture @{ '.claude/ouro.toml' = $declaredBinding; '.gitignore' = "node_modules/`n" }
    $before = Get-TreeState $repo
    $run = Invoke-Installer $repo @('-DocsFreshness', '-WhatIf') -Gh @{ Auth = '1' }
    Assert-Equal 0 $run.Code 'the clone check exits 0'
    Assert-Equal $before (Get-TreeState $repo) 'and writes nothing'
    Assert-Equal $true ($run.Out -match 'What if:.*Append \.claude/worktrees/') 'it reports the missing gitignore line'
    Assert-Equal $true ($run.Out -match 'What if:.*docs-freshness\.yml') 'it reports the missing workflow file a first install wrote'
    Assert-Equal $true ($run.Out -match 'What if:.*missing-area') 'it reports the missing declared label'
    Assert-Equal 0 (@($run.Gh -match '^label create').Count) 'and makes no label create call'

    # ── -Review ───────────────────────────────────────────────────────────────────────────
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-Review', 'copilot')
    Assert-Equal $true ($run.Code -ne 0) '-Review copilot without -CopilotBotId is refused'
    Assert-Equal $true ($run.Out -match 'CopilotBotId') 'the refusal names -CopilotBotId'
    Assert-Equal '' (Get-TreeState $repo) 'and nothing is written'
    $run = Invoke-Installer $repo @('-Review', 'copilot', '-CopilotBotId', 'BOT_stub')
    $toml = Read-Fixture $repo '.claude/ouro.toml'
    Assert-Equal 'review = "copilot"' (Get-Line $toml 'review') '-Review copilot is written into the new binding'
    Assert-Equal 'copilot_bot_id = "BOT_stub"' (Get-Line $toml 'copilot_bot_id') 'with -CopilotBotId beside it'
    Assert-Equal 'exit=0 ok' (Test-Binding $repo) 'and the binding passes check'
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-Review', 'none')
    Assert-Equal 'review = "none"' (Get-Line (Read-Fixture $repo '.claude/ouro.toml') 'review') '-Review none is written into the new binding'
    Assert-Equal 'exit=0 ok' (Test-Binding $repo) 'and the binding passes check'

    # ── -Review external-audit, -ExternalCli, -ExternalModel ───────────────────────────────
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-Review', 'external-audit')
    Assert-Equal $true ($run.Code -ne 0) '-Review external-audit without -ExternalCli is refused'
    Assert-Equal $true ($run.Out -match 'ExternalCli') 'the refusal names -ExternalCli'
    Assert-Equal '' (Get-TreeState $repo) 'and nothing is written'
    $run = Invoke-Installer $repo @('-Review', 'external-audit', '-ExternalCli', 'grok')
    $toml = Read-Fixture $repo '.claude/ouro.toml'
    Assert-Equal 'review = "external-audit"' (Get-Line $toml 'review') '-Review external-audit is written into the new binding'
    Assert-Equal 'external_cli = "grok"' (Get-Line $toml 'external_cli') 'with -ExternalCli beside it'
    Assert-Equal '' (Get-Line $toml 'external_model') 'and no -ExternalModel means no external_model line'
    Assert-Equal 'exit=0 ok' (Test-Binding $repo) 'and the binding passes check'
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-Review', 'external-audit', '-ExternalCli', 'grok', '-ExternalModel', 'grok-4-fast')
    $toml = Read-Fixture $repo '.claude/ouro.toml'
    Assert-Equal 'external_model = "grok-4-fast"' (Get-Line $toml 'external_model') '-ExternalModel is written beside -ExternalCli when it names a value'
    Assert-Equal 'exit=0 ok' (Test-Binding $repo) 'and that binding passes check too'
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-Review', 'external-audit', '-ExternalCli', 'grok', '-ExternalModel', 'default')
    Assert-Equal '' (Get-Line (Read-Fixture $repo '.claude/ouro.toml') 'external_model') `
        "-ExternalModel default writes no external_model line, the same as omitting it"
    $repo = New-Fixture
    # nosuchcli, not a real adapter name -- a name outside the set, one that never ships.
    $run = Invoke-Installer $repo @('-Review', 'external-audit', '-ExternalCli', 'nosuchcli')
    Assert-Equal $true ($run.Code -ne 0) '-ExternalCli naming an adapter that is not shipped is refused'
    Assert-Equal $true ($run.Out -match 'grok') 'and the refusal names the shipped set'
    Assert-Equal '' (Get-TreeState $repo) 'and nothing is written'

    # The adapter names: the validator's set, the installer's -ExternalCli set and the driver's
    # own --cli set are never three different lists. -ExternalCli carries no [ValidateSet] of its
    # own (it has no default inside one, so ValidateSet would refuse every run that left it out),
    # so its set is read the way $InstallerPlaceholder is: out of the source, by name.
    $externalCliSet = @([regex]::Match([System.IO.File]::ReadAllText($Installer), '\$KnownExternalClis = @\(([^)]*)\)').Groups[1].Value -split ',\s*' |
            ForEach-Object { $_.Trim("'") })
    Assert-Equal $true ($externalCliSet.Count -gt 0) 'could read $KnownExternalClis out of the installer'
    $repo = New-Fixture @{ '.claude/ouro.toml' = ($BoundBinding -replace 'review = "adversarial-review"', "review = `"external-audit`"`nexternal_cli = `"bogus-cli`"") }
    $checked = Test-Binding $repo
    # Sets, not the ordered lists each side happens to print: the driver lists its adapters
    # sorted, the validator and the installer do not, so a further adapter can widen all three in
    # a different order without this pin going red over ordering alone.
    $validatorCliSet = @([regex]::Match($checked, 'ship\.external_cli: must be one of ([^\r\n]*)').Groups[1].Value -split ',\s*')
    Assert-Equal (($validatorCliSet | Sort-Object) -join ',') (($externalCliSet | Sort-Object) -join ',') "the installer's -ExternalCli set is pinned to the validator's"
    $driver = Join-Path $Base 'bin/external-audit.py'
    if (-not (Test-Path -LiteralPath $driver)) { $driver = Join-Path $Base 'external-audit.py' }
    $driverRefusal = (& python3 $driver --cli bogus-cli --goal g --range a..b 2>&1 | Out-String).Trim()
    $driverCliSet = @([regex]::Match($driverRefusal, 'adapters: ([^\r\n]*)').Groups[1].Value -split ',\s*')
    Assert-Equal (($validatorCliSet | Sort-Object) -join ',') (($driverCliSet | Sort-Object) -join ',') "the driver's --cli set is pinned to the validator's"

    # The installer's own set, read from its parameter metadata, pinned to the validator's: a
    # `ship.review` the installer would write and `check` would refuse are never two different
    # lists. The validator's set is read from its own refusal, not retyped here, so neither can
    # drift unnoticed.
    $reviewSet = @((Get-Command $Installer).Parameters['Review'].Attributes |
            Where-Object { $_ -is [System.Management.Automation.ValidateSetAttribute] } |
            Select-Object -First 1 -ExpandProperty ValidValues)
    $repo = New-Fixture @{ '.claude/ouro.toml' = ($BoundBinding -replace 'review = "adversarial-review"', 'review = "bogus"') }
    $checked = Test-Binding $repo
    $validatorSet = @([regex]::Match($checked, 'ship\.review: must be one of ([^\r\n]*)').Groups[1].Value -split ',\s*')
    Assert-Equal ($validatorSet -join ',') ($reviewSet -join ',') "the installer's -Review set is pinned to the validator's"
    # A name outside the set is refused by the parameter itself, before the script body runs -- the
    # shape -Landing's own refusal row below takes.
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-Review', 'bogus')
    Assert-Equal $true ($run.Code -ne 0) 'a -Review outside the four names is refused'
    Assert-Equal 0 (@('copilot', 'adversarial-review', 'external-audit', 'none' |
                Where-Object { $run.Out -notmatch $_ }).Count) 'and the refusal lists the four names it accepts'
    Assert-Equal '' (Get-TreeState $repo) 'nothing is written'
    Assert-Equal 0 $run.Gh.Count 'and no gh call is made'

    # ── the hard rename: the retired value, refused with no alias ──────────────────────────
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-Review', 'grok-audit')
    Assert-Equal $true ($run.Code -ne 0) "-Review grok-audit, the retired value, is refused by the parameter itself"
    Assert-Equal '' (Get-TreeState $repo) 'nothing is written'
    $grokAuditBinding = $BoundBinding -replace 'review = "adversarial-review"', 'review = "grok-audit"'
    $repo = New-Fixture @{ '.claude/ouro.toml' = $grokAuditBinding }
    Assert-Equal $true ((Test-Binding $repo) -match '^exit=1 ') "and a committed binding naming it fails ``check``"
    Assert-Equal $true ((Test-Binding $repo) -match 'ship\.review: must be one of ') 'naming the allowed set'
    Assert-Equal $false ((Test-Binding $repo) -match 'grok-audit') 'and not offering it back as an alias'

    # ── -Landing ──────────────────────────────────────────────────────────────────────────
    # The landing policy is a choice made at onboarding, and merge-squash is the selected one: a
    # new binding says so outright rather than leaving the loop's shape to be inferred from two
    # absent keys.
    $repo = New-Fixture
    $run = Invoke-Installer $repo
    Assert-Equal 'landing = "merge-squash"' (Get-Line (Read-Fixture $repo '.claude/ouro.toml') 'landing') `
        'a new binding carries landing = "merge-squash" with no -Landing passed'
    Assert-Equal 'exit=0 ok' (Test-Binding $repo) 'and that binding passes check'
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-Landing', 'rebase-squash')
    Assert-Equal 'landing = "rebase-squash"' (Get-Line (Read-Fixture $repo '.claude/ouro.toml') 'landing') `
        '-Landing rebase-squash is written into the new binding'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^wrote: ', 'ship\.landing = rebase-squash') `
        'and the wrote: line names the landing'
    Assert-Equal 'exit=0 ok' (Test-Binding $repo) 'and that binding passes check too'
    # A name outside the four is refused by the parameter itself, before the script body runs --
    # `squash-merge` is the pair's own spelling reversed, the typo a reader of the table makes.
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-Landing', 'squash-merge')
    Assert-Equal $true ($run.Code -ne 0) 'a -Landing outside the four names is refused'
    # The set the binder echoes, which only the ValidateSet puts there: a refusal that named the
    # parameter alone would also pass while the script took the value and wrote it.
    Assert-Equal 0 (@('merge-squash', 'rebase-squash', 'rebase-merge', 'merge-merge' |
        Where-Object { $run.Out -notmatch $_ }).Count) 'and the refusal lists the four names it accepts'
    Assert-Equal '' (Get-TreeState $repo) 'nothing is written'
    Assert-Equal 0 $run.Gh.Count 'and no gh call is made'
    # An existing binding is the owner's, landing included.
    $repo = New-Fixture @{ '.claude/ouro.toml' = $BoundBinding }
    $run = Invoke-Installer $repo @('-Landing', 'merge-merge')
    Assert-Equal $BoundBinding (Read-Fixture $repo '.claude/ouro.toml') 'an existing binding is left byte-identical, -Landing notwithstanding'
    Assert-Equal 1 (Get-MatchingLineCount $run.Out '^\s+-Landing is written into a new binding only') `
        'and the run says -Landing reached no file'

    # ── a cased value on either validated set ─────────────────────────────────────────────
    # ValidateSet matches without case and binds the argument as typed, so both are lowered
    # before anything reads them: the binding must hold a value `check` accepts.
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-Review', 'External-Audit', '-ExternalCli', 'GROK')
    $toml = Read-Fixture $repo '.claude/ouro.toml'
    Assert-Equal 'review = "external-audit"' (Get-Line $toml 'review') 'a cased -Review reaches the binding lowercase'
    Assert-Equal 'external_cli = "grok"' (Get-Line $toml 'external_cli') 'and a cased -ExternalCli reaches it lowercase too'
    Assert-Equal 'exit=0 ok' (Test-Binding $repo) 'and that binding passes check'
    $repo = New-Fixture
    $run = Invoke-Installer $repo @('-Landing', 'Rebase-Merge')
    Assert-Equal 'landing = "rebase-merge"' (Get-Line (Read-Fixture $repo '.claude/ouro.toml') 'landing') `
        'a cased -Landing reaches the binding lowercase'
    Assert-Equal 'exit=0 ok' (Test-Binding $repo) 'and that binding passes check'

    # ── the git-root refusal, and -Into ───────────────────────────────────────────────────
    $repo = New-Fixture
    New-Item -ItemType Directory -Force -Path (Join-Path $repo 'sub') | Out-Null
    $run = Invoke-Installer (Join-Path $repo 'sub')
    Assert-Equal $true ($run.Code -ne 0) 'a directory below the git root is refused'
    Assert-Equal $true ($run.Out -match 'is not the git root') 'with the reason'
    Assert-Equal '' (Get-TreeState $repo) 'and nothing is written'
    $run = Invoke-Installer $scratch @('-Into', $repo)
    Assert-Equal 0 $run.Code '-Into names the root when the working directory is elsewhere'
    Assert-Equal $true (Test-Path -LiteralPath (Join-Path $repo '.claude/ouro.toml')) 'and the binding lands under -Into'

    # ── the root reached through a link: git answers the physical path, -Into keeps the link ───
    # A symbolic link where one can be made; on Windows without the privilege, a junction, which
    # needs none.
    $repo = New-Fixture
    $viaLink = Join-Path $scratch "via-$([guid]::NewGuid().ToString('N').Substring(0, 6))"
    $linked = try { New-Item -ItemType SymbolicLink -Path $viaLink -Target $repo -ErrorAction Stop | Out-Null; $true } catch { $false }
    if (-not $linked -and $IsWindows) {
        $linked = try { New-Item -ItemType Junction -Path $viaLink -Target $repo -ErrorAction Stop | Out-Null; $true } catch { $false }
    }
    if (-not $linked) { Write-Host '  skip: cannot create a link to the root here' -ForegroundColor DarkGray }
    else {
        $run = Invoke-Installer $scratch @('-Into', $viaLink)
        Assert-Equal 0 $run.Code '-Into the root reached through a link: the run exits 0'
        Assert-Equal $false ($run.Out -match 'is not the git root') 'and nothing calls it anything but the root'
        Assert-Equal $true (Test-Path -LiteralPath (Join-Path $repo '.claude/ouro.toml')) 'and the binding lands in the real repo'
    }
    $run = Invoke-Installer $scratch @('-Into', (Join-Path $repo '.git'))
    Assert-Equal $true ($run.Code -ne 0) '-Into the .git directory is still refused'

    # A work tree the repo points elsewhere: git prints an empty prefix outside that tree too, so
    # the prefix alone reads a directory git never looks at as a root, and installs into it.
    $repo = New-Fixture
    $holder = Join-Path $scratch "holder-$([guid]::NewGuid().ToString('N').Substring(0, 6))"
    New-Item -ItemType Directory -Force -Path $holder | Out-Null
    git -C $holder init -q 2>$null | Out-Null
    git -C $holder config core.worktree ($repo -replace '\\', '/') 2>$null | Out-Null
    $run = Invoke-Installer $scratch @('-Into', $holder)
    Assert-Equal $true ($run.Code -ne 0) '-Into a directory whose work tree is elsewhere is refused'
    Assert-Equal $true ($run.Out -match 'is not the git root') 'with the reason'
    Assert-Equal $false (Test-Path -LiteralPath (Join-Path $holder '.claude/ouro.toml')) 'and nothing is written there'
    Assert-Equal '' (Get-TreeState $repo) 'and nothing in the work tree either'

    # A work tree forced from the environment: GIT_DIR names a repository before git looks at a
    # path, and with no work tree of its own git makes the directory it is run in the work tree,
    # so a directory that is no repository answers `true` with an empty prefix.
    # A fresh directory per run: one an accepted run wrote into still holds those files, and the
    # next run's exit code would be about them rather than about $Into.
    $repo = New-Fixture
    foreach ($vars in @(@{ GIT_DIR = (Join-Path $repo '.git') },
            @{ GIT_DIR = (Join-Path $repo '.git'); GIT_WORK_TREE = '.' })) {
        $what = (@($vars.Keys) | Sort-Object) -join ' and '
        $plain = Join-Path $scratch "plain-$([guid]::NewGuid().ToString('N').Substring(0, 6))"
        New-Item -ItemType Directory -Force -Path $plain | Out-Null
        $run = Invoke-Installer $scratch @('-Into', $plain) -Environment $vars
        # The exit code and the reason in one claim: a run that exported GIT_WORK_TREE takes the
        # directory and then dies at the isolated `git init`, which is a nonzero exit and no refusal.
        Assert-Equal $true (($run.Code -ne 0) -and ($run.Out -match 'is not (inside a git work tree|the git root)')) `
            "-Into a directory that is no repository, with $what exported, is refused"
        Assert-Equal '' (Get-TreeState $plain) 'and nothing is written there'
    }
    # -WhatIf refuses it the same way, and reaches no ShouldProcess of its own: a run that unset
    # the variables through the Env: drive would keep them under -WhatIf and take the directory.
    $plain = Join-Path $scratch "plain-$([guid]::NewGuid().ToString('N').Substring(0, 6))"
    New-Item -ItemType Directory -Force -Path $plain | Out-Null
    $run = Invoke-Installer $scratch @('-Into', $plain, '-WhatIf') -Environment @{ GIT_DIR = (Join-Path $repo '.git') }
    Assert-Equal $true ($run.Code -ne 0) 'and -WhatIf refuses it too'
    Assert-Equal $false ($run.Out -match 'What if:') 'printing no What-if line'

    # Past the root check the run keeps asking git -- the remote lookup, the ignore probe, the
    # labels -- and -C does not win over GIT_DIR: a caller's variable puts that repository's origin
    # into this repo's binding and its slug into every label write, and sends the ignore probe to
    # that repository's info/exclude, which answers `already ignored` so nothing is appended. A real
    # root is still accepted, and everything the run writes and asks is about -Into.
    $callerJson = '{"nameWithOwner":"caller/CALLER","defaultBranchRef":{"name":"caller-main"}}'
    $targetJson = '{"nameWithOwner":"target/TARGET","defaultBranchRef":{"name":"target-main"}}'
    $twoGh = @{ ByUrl = "CALLER=$callerJson`nTARGET=$targetJson"; User = 'me'; Auth = '1' }
    # The caller's own clone ignores both entries, and only a probe reading its info/exclude rather
    # than -Into's finds them. Read back: a fixture that wrote nothing would pass either way.
    $caller = New-Fixture @{ '.git/info/exclude' = ".claude/worktrees/`n.claude/ouro.local.toml`n" } `
        -Remotes @{ origin = 'https://github.com/caller/CALLER.git' }
    Assert-Equal ".claude/worktrees/`n.claude/ouro.local.toml`n" (Read-Fixture $caller '.git/info/exclude') `
        "the caller's clone ignores both entries in its own info/exclude"
    foreach ($vars in @(@{ GIT_DIR = (Join-Path $caller '.git') },
            @{ GIT_DIR = (Join-Path $caller '.git'); GIT_WORK_TREE = '.' },
            @{ GIT_COMMON_DIR = (Join-Path $caller '.git') })) {
        $what = (@($vars.Keys) | Sort-Object) -join ' and '
        $target = New-Fixture -Remotes @{ origin = 'https://github.com/target/TARGET.git' }
        $run = Invoke-Installer $scratch @('-Into', $target) -Gh $twoGh -Environment $vars
        $toml = Read-Fixture $target '.claude/ouro.toml'
        Assert-Equal 0 $run.Code "-Into a real root with $what exported: the run exits 0"
        Assert-Equal 'slug = "target/TARGET"' (Get-Line $toml 'slug') 'and the binding names the repository -Into names'
        Assert-Equal 'default_branch = "target-main"' (Get-Line $toml 'default_branch') "with that repository's default branch"
        Assert-Equal 0 @($run.Gh | Where-Object { $_ -match 'caller/CALLER' }).Count 'and no gh call names the other repository'
        Assert-Equal ".claude/worktrees/`n.claude/ouro.local.toml`n" (Read-Fixture $target '.gitignore') `
            'and both gitignore entries are appended'
    }

    # git prints the two answers as two lines, and a prefix is one path -- but a path may hold a
    # newline, and a directory whose name starts with one prints three, the second of them empty.
    # Windows has no such name to make, so the case runs where a name may carry one.
    if ($IsWindows) { Write-Host '  skip: a newline is not a legal name here' -ForegroundColor DarkGray }
    else {
        $repo = New-Fixture
        $odd = Join-Path $repo "`nfoo"
        New-Item -ItemType Directory -Force -Path $odd | Out-Null
        $run = Invoke-Installer $scratch @('-Into', $odd)
        Assert-Equal $true ($run.Code -ne 0) '-Into a subdirectory whose name starts with a newline is refused'
        Assert-Equal $true ($run.Out -match 'is not the git root') 'with the reason'
        Assert-Equal $false (Test-Path -LiteralPath (Join-Path $odd '.claude/ouro.toml')) 'and nothing is written there'
    }

    # ── the script's own comment-based help parses ───────────────────────────────────────
    # A help line whose first word is a dot and a name is read as a help keyword, and an
    # unknown keyword makes PowerShell drop the block: Get-Help then answers with the syntax
    # it generates, as a plain string. Scoped to this script by name.
    # That string carries neither a description nor a parameters property, so both reads are
    # null-safe: the case must report a failed assertion, not throw. Get-Help reads even a path
    # as a wildcard pattern, so it is escaped: a raw `x[1]` directory would match `x1` instead.
    $help = Get-Help -Full ([WildcardPattern]::Escape($Installer))
    $helpText = if ($help.PSObject.Properties['description']) { ($help.description | Out-String).Trim() } else { '' }
    $helpParams = if ($help.PSObject.Properties['parameters']) { @($help.parameters.parameter).Count } else { 0 }
    Assert-Equal $true ($helpText.Length -gt 0) 'the installer exposes its help description'
    Assert-Equal 11 $helpParams 'and the help lists eleven parameter entries: the nine declared, plus WhatIf and Confirm'
}
finally {
    if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force }
}

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall install-ouro cases pass" -ForegroundColor Green
exit 0
