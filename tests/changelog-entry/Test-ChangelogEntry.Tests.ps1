<#
.SYNOPSIS
    Fixture suite for Test-ChangelogEntry.ps1's surfaces and its entry check.
.DESCRIPTION
    Drives the gate against a scratch git repository built in TEMP -- never inside a working
    tree -- so the verdict does not depend on the surrounding checkout. One base commit carries
    the fixture tree under fixtures/base; each case is a branch off it with the overlay under
    fixtures/cases/<case> applied, and the gate is run with -Base at the base commit.

    Each surface is asserted twice over: the case that moves it with no unreleased entry is one
    finding naming the file and the class, and the same branch with fixtures/entry/CHANGELOG.md
    committed on top is quiet. Beside each is the change that touches the same file without
    moving the surface -- a skill's body, a script's body, a validator line outside the schema
    region, a docstring's prose -- because a gate that reported those would fire on almost every
    pull request and be turned off.

    The rest are the paths that have no finding to report: a change outside the surfaces,
    a base with no changelog at all (every bullet at HEAD is then new), the default branch
    itself (base and HEAD are the same commit, so the diff is empty), and a base ref this clone
    does not have, which is an INFO line and a clean exit rather than a failed git diff.

    With no -Base the base is read from [repo].default_branch: a repo that declares a binding
    and has no python3 on PATH stops the run rather than diffing against a guessed base, while a
    repo with no binding keeps the origin/master fallback and its INFO line.

    Four changelog edits that are not an entry -- a reworded unreleased bullet, a bullet under a
    released heading, a released heading's date corrected, a released heading gaining whitespace
    before its bracket -- leave a surface move a finding, and an entry under a version heading the
    base does not have (a branch that bumps in its own PR) is one. One surface case moves a skill
    on a path with a non-ASCII byte, which git prints quoted unless told not to. Report-only is
    asserted by exit code on every case, and -Blocking twice, on a finding and on a clean diff:
    promoting the gate must stay a caller's decision.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Two levels up is the plugin root (gate under bin/) or, once vendored, the scripts dir itself.
$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Gate = @((Join-Path $Base 'Test-ChangelogEntry.ps1'), (Join-Path $Base 'bin/Test-ChangelogEntry.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Gate) { throw "Test-ChangelogEntry.ps1 not found under $Base" }

$Fixtures = Join-Path $PSScriptRoot 'fixtures'

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -eq $Actual) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}
function Assert-Match($Pattern, $Output, $What) {
    if ($Output -match $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- no match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}

# System.IO, not Copy-Item -Recurse: on Linux a '*' or '?' in the source directory's own path
# still globs through -LiteralPath, and the copy would take a sibling's tree.
function Copy-Tree {
    param([string]$From, [string]$To)
    foreach ($file in [System.IO.Directory]::GetFiles($From, '*', [System.IO.SearchOption]::AllDirectories)) {
        $rel = $file.Substring($From.Length).TrimStart([char[]]@('\', '/'))
        $dest = Join-Path $To $rel
        $dir = Split-Path $dest -Parent
        if (-not [System.IO.Directory]::Exists($dir)) { [System.IO.Directory]::CreateDirectory($dir) | Out-Null }
        # Bytes, not File.Copy: File.Copy keeps the source's modification time, and a fresh clone
        # writes a base fixture and a case fixture in the same second. Two same-size files then
        # match git's stat check (seconds, size, inode), so git never re-reads the copy and the
        # case commits nothing. A write here gets its own time.
        [System.IO.File]::WriteAllBytes($dest, [System.IO.File]::ReadAllBytes($file))
    }
}

# Every directory of the caller's PATH that does not answer the gate's own question,
# Get-Command python3: a spelling a name list did not foresee (a .ps1, a PATHEXT entry a machine
# adds) answers that question while failing such a list, and the row would then report the
# staging rather than the gate. Asked once over the whole PATH, since -All names every command
# of that name and each one's own file. The gate asks for the versioned name alone, so a
# `python` beside git is left where it is.
# One directory by the name the filesystem gives it, on both sides of the comparison below:
# Get-Command reports a directory under a spelling of its own, the long name with the path
# folded on Windows and a folded path for a script on Linux, while a PATH entry may carry an 8.3
# component, a doubled separator or a . or .. segment, which trimming and case-folding do not
# bridge. The 8.3 row below runs on Windows alone; the doubled-separator row runs on both.
# Asking for each entry is not what makes a dead network share on PATH slow: Get-Command -All
# above has already waited on it, and the ask that follows is answered in milliseconds. A PATH
# entry naming a path that does not exist is kept as written rather than thrown on (a blank one
# never gets here), and one that names a file answers with the file, which can equal no directory.
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
        # A blank entry names nothing; on Windows Get-Item would answer it with the working directory.
        if ([string]::IsNullOrWhiteSpace($dir)) { continue }
        # The App Execution Alias directory answers `python3` with a zero-byte reparse point,
        # which Get-Command resolves and running would send to the Store.
        if ($dir -match 'WindowsApps') { continue }
        if ($drop.Contains((Get-DirIdentity $dir))) { continue }
        $keep += $dir
    }
    ($keep -join [System.IO.Path]::PathSeparator)
}

$Repo = Join-Path ([System.IO.Path]::GetTempPath()) ("changelog-entry-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
function Invoke-RepoGit {
    param([string[]]$Arguments)
    $out = git -C $Repo -c user.name=ouro -c user.email=ouro@invalid -c commit.gpgsign=false @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git $($Arguments -join ' ') failed (exit $LASTEXITCODE): $out" }
}

# Write-Host writes to the information stream (6); the findings go to stdout. Both are the report.
# A gate that throws is this case's failure, with the exception as its output, not an abort that
# takes the remaining cases with it.
function Invoke-Gate {
    param([hashtable]$Extra = @{})
    $text = ''
    $code = 1
    try { $text = (& $Gate -RepoRoot $Repo @Extra 6>&1 | Out-String); $code = $LASTEXITCODE }
    catch { $text = "threw: $_" }
    [pscustomobject]@{ Text = $text; Code = $code }
}

function New-Case {
    param([string]$Name, [string]$Ref, [string]$Case = $Name)
    Invoke-RepoGit @('checkout', '-q', '-B', $Name, $Ref)
    Copy-Tree -From (Join-Path $Fixtures "cases/$Case") -To $Repo
    Invoke-RepoGit @('add', '-A')
    Invoke-RepoGit @('commit', '-q', '-m', $Name)
}

function Add-Entry {
    Copy-Tree -From (Join-Path $Fixtures 'entry') -To $Repo
    Invoke-RepoGit @('add', '-A')
    Invoke-RepoGit @('commit', '-q', '-m', 'entry')
}

try {
    New-Item -ItemType Directory -Force -Path $Repo | Out-Null
    git -C $Repo init -q -b main 2>$null
    if ($LASTEXITCODE -ne 0) { throw "git init in $Repo failed (exit $LASTEXITCODE)" }
    # The comparisons are content equality between two refs; a checkout that rewrites line
    # endings would make the fixtures' own bytes depend on the machine running the suite.
    Invoke-RepoGit @('config', 'core.autocrlf', 'false')
    Copy-Tree -From (Join-Path $Fixtures 'base') -To $Repo
    Invoke-RepoGit @('add', '-A')
    Invoke-RepoGit @('commit', '-q', '-m', 'base')
    $baseSha = (git -C $Repo rev-parse HEAD).Trim()

    # A second base for the repo that has no changelog yet: there every bullet at HEAD is new.
    Invoke-RepoGit @('checkout', '-q', '-B', 'no-changelog', $baseSha)
    Invoke-RepoGit @('rm', '-q', 'CHANGELOG.md')
    Invoke-RepoGit @('commit', '-q', '-m', 'no changelog')
    $noChangelogSha = (git -C $Repo rev-parse HEAD).Trim()

    # --- one per surface: the move with no entry is a finding, the same move with one is quiet ---
    $surfaces = @(
        @{ Case = 'skill-frontmatter'; Surface = 'skill front matter'; File = 'skills/demo/SKILL.md' }
        @{ Case = 'schema-key';        Surface = 'binding schema';     File = 'bin/ouro-binding.py' }
        @{ Case = 'schema-rule';       Surface = 'binding schema';     File = 'bin/ouro-binding.py' }
        @{ Case = 'schema-value';      Surface = 'binding schema';     File = 'bin/ouro-binding.py' }
        @{ Case = 'script-param';      Surface = 'script parameters';  File = 'bin/Demo-Gate.ps1' }
        @{ Case = 'module-usage';      Surface = 'script usage';       File = 'bin/demo-tool.py' }
        @{ Case = 'template';          Surface = 'shipped template';   File = 'templates/demo.yml' }
        @{ Case = 'action-definition'; Surface = 'action definition';  File = 'actions/demo/action.yml' }
        @{ Case = 'quoted-path';       Surface = 'skill front matter'; File = 'skills/café/SKILL.md' }
    )
    foreach ($s in $surfaces) {
        New-Case -Name $s.Case -Ref $baseSha
        $r = Invoke-Gate @{ Base = $baseSha }
        Assert-Match ("total: 1 finding\(s\)") $r.Text "$($s.Case): one finding with no entry"
        Assert-Match ([regex]::Escape("$($s.File)  -> $($s.Surface)")) $r.Text `
            "$($s.Case): the finding names the file and the surface class"
        Assert-Equal 0 $r.Code "$($s.Case): report-only exits 0 on a finding"

        Add-Entry
        $r = Invoke-Gate @{ Base = $baseSha }
        Assert-Match ("total: 0 finding\(s\)") $r.Text "$($s.Case): an unreleased entry silences it"
        Assert-Equal 0 $r.Code "$($s.Case): exits 0 with the entry"
    }

    # --- Get-ModuleUsage reads a ''' docstring the same as a """ one -----------------------------
    # A shared pair of commits, not the global base: the module-usage case above already proves
    # """ is read, and diffing a ''' module against the plain base (which uses """) would itself
    # be a script-usage move under either the old or the new code, proving nothing about the quote
    # style. Both sides here use ''' so the only difference is the usage line moving inside it.
    Invoke-RepoGit @('checkout', '-q', '-B', 'module-usage-single-base', $baseSha)
    Copy-Tree -From (Join-Path $Fixtures 'cases/module-usage-single-base') -To $Repo
    Invoke-RepoGit @('add', '-A')
    Invoke-RepoGit @('commit', '-q', '-m', 'single-quote docstring base')
    $singleQuoteSha = (git -C $Repo rev-parse HEAD).Trim()

    Invoke-RepoGit @('checkout', '-q', '-B', 'module-usage-single-move', $singleQuoteSha)
    Copy-Tree -From (Join-Path $Fixtures 'cases/module-usage-single-move') -To $Repo
    Invoke-RepoGit @('add', '-A')
    Invoke-RepoGit @('commit', '-q', '-m', 'single-quote usage move')
    $r = Invoke-Gate @{ Base = $singleQuoteSha }
    Assert-Match ("total: 1 finding\(s\)") $r.Text "module-usage-single: a moved usage line inside a ''' docstring is reported"
    Assert-Match ([regex]::Escape('bin/demo-tool.py  -> script usage')) $r.Text `
        'module-usage-single: the finding names the file and the surface class'
    Assert-Equal 0 $r.Code 'module-usage-single: report-only exits 0 on a finding'

    Add-Entry
    $r = Invoke-Gate @{ Base = $singleQuoteSha }
    Assert-Match ("total: 0 finding\(s\)") $r.Text "module-usage-single: an unreleased entry silences it"
    Assert-Equal 0 $r.Code 'module-usage-single: exits 0 with the entry'

    # --- the python3?\b alternative: a bare usage line with no "usage:" prefix -------------------
    # A line that starts "python3 ..." with no "usage:" or "usage " before it matches only through
    # the pattern's python3?\b alternative -- the module-usage cases above never exercise it alone,
    # since their usage line always begins "usage: python3 ...". The old default,
    # '^\s*(usage\b|python\b|-{1,2}\w)', has no \b right after "python" that a following "3"
    # clears, so it never matches "python3 ..." and the line is excluded from extraction on both
    # sides: the arguments changing below is invisible to it. The current default's python3?\b
    # alternative includes the line on both sides, so the changed arguments differ and are reported.
    Invoke-RepoGit @('checkout', '-q', '-B', 'module-usage-python3-only-base', $baseSha)
    Copy-Tree -From (Join-Path $Fixtures 'cases/module-usage-python3-only-base') -To $Repo
    Invoke-RepoGit @('add', '-A')
    Invoke-RepoGit @('commit', '-q', '-m', 'bare python3 usage line base')
    $python3OnlySha = (git -C $Repo rev-parse HEAD).Trim()

    Invoke-RepoGit @('checkout', '-q', '-B', 'module-usage-python3-only-move', $python3OnlySha)
    Copy-Tree -From (Join-Path $Fixtures 'cases/module-usage-python3-only-move') -To $Repo
    Invoke-RepoGit @('add', '-A')
    Invoke-RepoGit @('commit', '-q', '-m', 'bare python3 usage line move')

    $rOld = Invoke-Gate @{ Base = $python3OnlySha; UsageLinePattern = '^\s*(usage\b|python\b|-{1,2}\w)' }
    Assert-Match ("total: 0 finding\(s\)") $rOld.Text `
        'module-usage-python3-only: the old default misses a bare python3 usage line changing'

    $r = Invoke-Gate @{ Base = $python3OnlySha }
    Assert-Match ("total: 1 finding\(s\)") $r.Text `
        'module-usage-python3-only: the current default catches a bare python3 usage line changing'
    Assert-Match ([regex]::Escape('bin/demo-tool.py  -> script usage')) $r.Text `
        'module-usage-python3-only: the finding names the file and the surface class'
    Assert-Equal 0 $r.Code 'module-usage-python3-only: report-only exits 0 on a finding'

    Add-Entry
    $r = Invoke-Gate @{ Base = $python3OnlySha }
    Assert-Match ("total: 0 finding\(s\)") $r.Text "module-usage-python3-only: an unreleased entry silences it"
    Assert-Equal 0 $r.Code 'module-usage-python3-only: exits 0 with the entry'

    # --- a module with neither delimiter still yields no usage lines -----------------------------
    # Neither side carries a docstring at all: the changed line is a bare module-level assignment
    # (`USAGE = "..."`) that DOES match UsageLinePattern, but sits outside any docstring, before
    # Get-ModuleUsage ever finds one -- so an extractor that honours the delimiters returns '' on
    # both sides, while one that scans every matching line regardless of delimiter would see the
    # two sides differ and misreport a script-usage move.
    Invoke-RepoGit @('checkout', '-q', '-B', 'module-no-docstring-base', $baseSha)
    Copy-Tree -From (Join-Path $Fixtures 'cases/module-no-docstring-base') -To $Repo
    Invoke-RepoGit @('add', '-A')
    Invoke-RepoGit @('commit', '-q', '-m', 'no docstring base')
    $noDocstringSha = (git -C $Repo rev-parse HEAD).Trim()

    Invoke-RepoGit @('checkout', '-q', '-B', 'module-no-docstring-move', $noDocstringSha)
    Copy-Tree -From (Join-Path $Fixtures 'cases/module-no-docstring-move') -To $Repo
    Invoke-RepoGit @('add', '-A')
    Invoke-RepoGit @('commit', '-q', '-m', 'no docstring move')
    $r = Invoke-Gate @{ Base = $noDocstringSha }
    Assert-Match ("total: 0 finding\(s\)") $r.Text `
        'module-no-docstring: a module with neither delimiter still yields no usage lines'
    Assert-Equal 0 $r.Code 'module-no-docstring: exits 0'

    # --- the same files moved where the surface is not: no finding -------------------------------
    $quiet = @(
        @{ Case = 'skill-body';      What = "a skill's body outside the front matter" }
        @{ Case = 'schema-outside';  What = 'a validator line outside the schema region' }
        @{ Case = 'script-body';     What = "a script's body outside its param block" }
        @{ Case = 'module-prose';    What = "a docstring's prose outside its usage lines" }
        @{ Case = 'action-readme';   What = "a file beside the action.yml in its own directory" }
        @{ Case = 'outside';         What = 'a change outside the surfaces' }
    )
    foreach ($q in $quiet) {
        New-Case -Name $q.Case -Ref $baseSha
        $r = Invoke-Gate @{ Base = $baseSha }
        Assert-Match ("total: 0 finding\(s\)") $r.Text "$($q.Case): $($q.What) is no finding"
        Assert-Equal 0 $r.Code "$($q.Case): exits 0"
    }

    # --- a base with no changelog at all: the entry at HEAD is new -------------------------------
    Invoke-RepoGit @('checkout', '-q', '-B', 'template-fresh', $noChangelogSha)
    Copy-Tree -From (Join-Path $Fixtures 'cases/template') -To $Repo
    Copy-Tree -From (Join-Path $Fixtures 'entry') -To $Repo
    Invoke-RepoGit @('add', '-A')
    Invoke-RepoGit @('commit', '-q', '-m', 'template and a first changelog')
    $r = Invoke-Gate @{ Base = $noChangelogSha }
    Assert-Match ("total: 0 finding\(s\)") $r.Text 'a base with no changelog makes every bullet at HEAD new'
    Assert-Equal 0 $r.Code 'exits 0 with no changelog at the base'

    # --- the default branch itself: base and HEAD are the same commit ---------------------------
    Invoke-RepoGit @('checkout', '-q', '-B', 'on-base', $baseSha)
    $r = Invoke-Gate @{ Base = $baseSha }
    Assert-Match ("total: 0 finding\(s\) across 0 changed file\(s\)") $r.Text 'on the base commit the diff is empty'
    Assert-Equal 0 $r.Code 'exits 0 on the base commit'

    # --- a base ref this clone does not have ----------------------------------------------------
    $r = Invoke-Gate @{ Base = 'origin/absent' }
    Assert-Match 'INFO - origin/absent is not a ref in this clone' $r.Text 'a missing base ref is an INFO line'
    Assert-Equal 0 $r.Code 'a missing base ref exits 0'

    # --- a changelog edit that is not an entry leaves the finding ------------------------------
    $notEntries = @(
        @{ Case = 'reword';             What = 'a reworded unreleased bullet' }
        @{ Case = 'released-only';      What = 'a bullet under a released heading' }
        @{ Case = 'heading-edit';       What = "a released heading's corrected date" }
        @{ Case = 'heading-whitespace'; What = 'a released heading gaining whitespace before its bracket' }
    )
    foreach ($n in $notEntries) {
        New-Case -Name $n.Case -Ref $baseSha
        $r = Invoke-Gate @{ Base = $baseSha }
        Assert-Match ("total: 1 finding\(s\)") $r.Text "$($n.Case): $($n.What) is no entry"
        Assert-Equal 0 $r.Code "$($n.Case): exits 0"
    }

    # --- the bump lands in the PR: the entry sits under the new version heading ------------------
    New-Case -Name 'bump' -Ref $baseSha
    $r = Invoke-Gate @{ Base = $baseSha }
    Assert-Match ("total: 0 finding\(s\)") $r.Text 'bump: an entry under a heading the base lacks is an entry'
    Assert-Equal 0 $r.Code 'bump: exits 0'

    # --- the unreleased heading itself gains whitespace before its bracket: still recognised ------
    # Get-UnreleasedBullets entered the section on exact equality with -UnreleasedHeading, so
    # a HEAD heading of `##  [UNRELEASED]` (two spaces) keyed equal to the base's `## [UNRELEASED]`
    # through Get-HeadingKey but was never matched as the unreleased section itself, and its
    # bullets -- the new one included -- were silently dropped. A shipped-template move needs an
    # entry; this bullet, under the two-space heading, must still clear it.
    New-Case -Name 'unreleased-heading-whitespace' -Ref $baseSha
    $r = Invoke-Gate @{ Base = $baseSha }
    Assert-Match ("total: 0 finding\(s\)") $r.Text `
        'unreleased-heading-whitespace: a new bullet under a two-space UNRELEASED heading clears the finding'
    Assert-Equal 0 $r.Code 'unreleased-heading-whitespace: exits 0'

    # --- a plugin manifest version with no changelog heading: its own finding class ---------------
    # The fixtures keep the manifest at plugin/plugin.json, passed as -VersionPath: a dot-directory
    # such as the default's .claude-plugin is skipped by a vendored tree's copy on Linux, where a
    # dot-name is hidden. The default path is exercised by the gate's own run on this repository.
    New-Case -Name 'version-no-heading' -Ref $baseSha
    $r = Invoke-Gate @{ Base = $baseSha; VersionPath = 'plugin/plugin.json' }
    Assert-Match ("total: 1 finding\(s\)") $r.Text 'version-no-heading: one finding with no heading'
    Assert-Match ([regex]::Escape('plugin/plugin.json  -> release heading')) $r.Text `
        'version-no-heading: the finding names the manifest and the release-heading class'
    Assert-Equal 0 $r.Code 'version-no-heading: report-only exits 0'

    Add-Entry
    $r = Invoke-Gate @{ Base = $baseSha; VersionPath = 'plugin/plugin.json' }
    Assert-Match ("total: 1 finding\(s\)") $r.Text 'version-no-heading: an unreleased entry does not silence it'
    Assert-Equal 0 $r.Code 'version-no-heading: exits 0 with the entry'

    New-Case -Name 'version-heading' -Ref $baseSha
    $r = Invoke-Gate @{ Base = $baseSha; VersionPath = 'plugin/plugin.json' }
    Assert-Match ("total: 0 finding\(s\)") $r.Text "version-heading: the version's own heading clears it"
    Assert-Equal 0 $r.Code 'version-heading: exits 0'

    New-Case -Name 'version-heading-bare' -Ref $baseSha
    $r = Invoke-Gate @{ Base = $baseSha; VersionPath = 'plugin/plugin.json' }
    Assert-Match ("total: 0 finding\(s\)") $r.Text "version-heading-bare: a heading without the v clears it too"

    New-Case -Name 'version-off' -Ref $baseSha -Case 'version-no-heading'
    $r = Invoke-Gate @{ Base = $baseSha; VersionPath = '' }
    Assert-Match ("total: 0 finding\(s\)") $r.Text "-VersionPath '' turns the release-heading check off"
    Assert-Equal 0 $r.Code 'version-off: exits 0'

    # --- -Blocking is the only way a finding fails the run --------------------------------------
    New-Case -Name 'outside-blocking' -Ref $baseSha -Case 'outside'
    $r = Invoke-Gate @{ Base = $baseSha; Blocking = $true }
    Assert-Equal 0 $r.Code '-Blocking exits 0 on a clean diff'
    # Two surfaces move here, so a mutation that disables either one still leaves this case a
    # finding to exit on: only the exit decision itself is under test.
    New-Case -Name 'two-surfaces' -Ref $baseSha
    $r = Invoke-Gate @{ Base = $baseSha; Blocking = $true }
    Assert-Match ("total: [1-9]\d* finding\(s\)") $r.Text '-Blocking still reports the findings'
    Assert-Equal 1 $r.Code '-Blocking turns a finding into exit 1'

    # --- the base of a repo that declares a binding is read, never guessed ----------------------
    # With no -Base the branch comes from [repo].default_branch through ouro-binding.py, and with
    # no python3 the fallback would diff a repo that declares another branch against origin/master.
    New-Item -ItemType Directory -Force -Path (Join-Path $Repo '.claude') | Out-Null
    $bindingPath = Join-Path $Repo '.claude/ouro.toml'
    Set-Content -LiteralPath $bindingPath -Encoding utf8 `
        -Value 'schema = 1', '[repo]', 'slug = "example/repo"', 'default_branch = "trunk"'
    # The scrub answers the gate's question, not a list of file names: a directory holding a
    # python3.ps1 answers Get-Command and would survive such a list, and then the staging row
    # below would report this suite rather than the gate. Whether git survives a scrub is the
    # machine's layout, not the scrub's doing -- where git shares a directory with python3 both
    # go, which the rows below already skip on -- so what is asserted here is that the planted
    # spelling costs git nothing it was not already costing. Planted where the caller's own PATH
    # cannot reach it, and removed with the directory it sits in.
    $ShimDir = Join-Path ([System.IO.Path]::GetTempPath()) ("nopy-shim-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $ShimDir | Out-Null
    Set-Content -LiteralPath (Join-Path $ShimDir 'python3.ps1') -Value 'exit 9' -Encoding utf8
    $shimSaved = $env:PATH
    try {
        $env:PATH = Get-PathWithoutPython3
        $plainGit = [bool](Get-Command git -ErrorAction SilentlyContinue)
        $env:PATH = $ShimDir + [System.IO.Path]::PathSeparator + $shimSaved
        $shimSeen = [bool](Get-Command python3 -ErrorAction SilentlyContinue)
        $env:PATH = Get-PathWithoutPython3
        $shimGone = -not (Get-Command python3 -ErrorAction SilentlyContinue)
        $shimGit  = [bool](Get-Command git -ErrorAction SilentlyContinue)
    }
    finally { $env:PATH = $shimSaved; Remove-Item -LiteralPath $ShimDir -Recurse -Force }
    if ($shimSeen) {
        Assert-Equal $true $shimGone 'the scrub hides a python3 spelled as a .ps1, which a name list would miss'
        Assert-Equal $plainGit $shimGit 'and the planted spelling costs git nothing the scrub was not already costing'
    }
    else { Write-Host "  skip: a planted python3.ps1 is not resolved as python3 on this machine" -ForegroundColor DarkGray }

    # A PATH entry in 8.3 form names the same directory as the Source Get-Command reports, which
    # is always the long one; the identity above is what makes them one directory. Staged only
    # where the filesystem hands out a short name at all.
    $LongDir = Join-Path ([System.IO.Path]::GetTempPath()) ("nopy shim long name " + [guid]::NewGuid().ToString('N').Substring(0, 6))
    New-Item -ItemType Directory -Force -Path $LongDir | Out-Null
    try {
        Set-Content -LiteralPath (Join-Path $LongDir 'python3.ps1') -Value 'exit 9' -Encoding utf8
        $shortDir = try { (New-Object -ComObject Scripting.FileSystemObject).GetFolder($LongDir).ShortPath } catch { $null }
        if ($shortDir -and $shortDir -ne $LongDir) {
            $shortSaved = $env:PATH
            try {
                $env:PATH = $shortDir + [System.IO.Path]::PathSeparator + $shortSaved
                $shortSeen = [bool](Get-Command python3 -ErrorAction SilentlyContinue)
                $env:PATH = Get-PathWithoutPython3
                $shortGone = -not (Get-Command python3 -ErrorAction SilentlyContinue)
            }
            finally { $env:PATH = $shortSaved }
            if ($shortSeen) {
                Assert-Equal $true $shortGone 'the scrub hides a python3 whose PATH entry is spelled in 8.3 form'
            }
            else { Write-Host "  skip: a python3 under a short-named PATH entry is not resolved here" -ForegroundColor DarkGray }
        }
        else { Write-Host "  skip: this filesystem hands out no 8.3 name for $LongDir" -ForegroundColor DarkGray }
    }
    finally { Remove-Item -LiteralPath $LongDir -Recurse -Force }

    # A PATH entry that doubles a separator, as %JAVA_HOME%\bin does under a home spelled with a
    # trailing one, names the directory Get-Command reports with the path folded, so comparing the
    # entry as written would keep its python3.
    $DoubleDir = (New-Item -ItemType Directory -Path (Join-Path ([System.IO.Path]::GetTempPath()) ("nopy-double-" + [guid]::NewGuid().ToString('N').Substring(0, 8)))).FullName
    try {
        Set-Content -LiteralPath (Join-Path $DoubleDir 'python3.ps1') -Value 'exit 9' -Encoding utf8
        $ds = [System.IO.Path]::DirectorySeparatorChar
        $doubleLeaf = Split-Path $DoubleDir -Leaf
        $doubled = (Split-Path $DoubleDir -Parent).TrimEnd($ds) + $ds + $ds + $doubleLeaf
        $doubleSaved = $env:PATH
        try {
            $env:PATH = $doubled + [System.IO.Path]::PathSeparator + $doubleSaved
            $doubleSeen = [bool](Get-Command python3 -All -ErrorAction SilentlyContinue | Where-Object { $_.Source -like "*$doubleLeaf*" })
            $env:PATH = Get-PathWithoutPython3
            $doubleGone = -not (Get-Command python3 -ErrorAction SilentlyContinue)
        }
        finally { $env:PATH = $doubleSaved }
        if ($doubleSeen) {
            Assert-Equal $true $doubleGone 'the scrub hides a python3 whose PATH entry doubles a separator'
        }
        else { Write-Host "  skip: a python3 under a doubled-separator PATH entry is not resolved here" -ForegroundColor DarkGray }
    }
    finally { Remove-Item -LiteralPath $DoubleDir -Recurse -Force }

    # A blank PATH entry names nothing, so the scrub drops it rather than asking the filesystem
    # about it.
    $blankSaved = $env:PATH
    try {
        $env:PATH = ' ' + [System.IO.Path]::PathSeparator + $blankSaved
        $blankKept = @((Get-PathWithoutPython3) -split [System.IO.Path]::PathSeparator) -contains ' '
    }
    finally { $env:PATH = $blankSaved }
    Assert-Equal $false $blankKept 'the scrub drops a whitespace-only PATH entry'
    $realPython3ForShim = (Get-Command python3 -ErrorAction SilentlyContinue).Source
    $realGitForShim = (Get-Command git -ErrorAction SilentlyContinue).Source
    $savedPath = $env:PATH
    $gitLinkDirForShim = $null
    try {
        $env:PATH = Get-PathWithoutPython3
        # git can share python3's own directory (both in /usr/bin on Linux), which the scrub
        # above then drops for free -- a scratch link keeps it reachable so the no-binding case
        # below runs instead of skipping past git going missing along with python3.
        if ($realGitForShim) {
            $gitLinkDirForShim = Join-Path ([System.IO.Path]::GetTempPath()) ('nopy-git-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
            New-Item -ItemType Directory -Force -Path $gitLinkDirForShim | Out-Null
            if ($IsWindows) {
                Set-Content -LiteralPath (Join-Path $gitLinkDirForShim 'git.cmd') -Encoding ascii -Value "@`"$realGitForShim`" %*"
            }
            else {
                New-Item -ItemType SymbolicLink -Path (Join-Path $gitLinkDirForShim 'git') -Target $realGitForShim | Out-Null
            }
            $env:PATH = $gitLinkDirForShim + [System.IO.Path]::PathSeparator + $env:PATH
        }
        $pythonGone = -not (Get-Command python3 -ErrorAction SilentlyContinue)
        $gitStillThere = [bool](Get-Command git -ErrorAction SilentlyContinue)
        Assert-Equal $true $pythonGone 'the scrubbed PATH hides python3 from the gate'
        if ($pythonGone) {
            $r = Invoke-Gate
            Assert-Match '(?m)^threw: ' $r.Text 'a declared binding with no python3 on PATH stops the run'
            Assert-Match '(?s)threw: .*ouro\.toml.*python3 3\.11\+' $r.Text `
                'the stop names the binding file and the python3 floor a repo with a binding needs'

            # A working `python`, in its own directory rather than python3's, must not let the
            # read through either.
            $pyOnlyDir = Join-Path ([System.IO.Path]::GetTempPath()) ("py-only-shim-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
            New-Item -ItemType Directory -Force -Path $pyOnlyDir | Out-Null
            try {
                if ($IsWindows) {
                    Set-Content -LiteralPath (Join-Path $pyOnlyDir 'python.cmd') -Encoding ascii -Value "@`"$realPython3ForShim`" %*"
                }
                else {
                    $pyShimPath = Join-Path $pyOnlyDir 'python'
                    Set-Content -LiteralPath $pyShimPath -Encoding utf8 -Value "#!/bin/sh`nexec `"$realPython3ForShim`" `"`$@`""
                    # chmod, not the external command: by this point $env:PATH is already the
                    # scrub above, which drops chmod's own directory along with python3's.
                    [System.IO.File]::SetUnixFileMode($pyShimPath, [System.IO.UnixFileMode]'UserRead, UserWrite, UserExecute, GroupRead, GroupExecute, OtherRead, OtherExecute')
                }
                $pyOnlySaved = $env:PATH
                try {
                    $env:PATH = $pyOnlyDir + [System.IO.Path]::PathSeparator + (Get-PathWithoutPython3)
                    $pyWorks = $false
                    try { & python --version *>$null; $pyWorks = ($LASTEXITCODE -eq 0) } catch {}
                    $python3StillGone = -not (Get-Command python3 -ErrorAction SilentlyContinue)
                    if ($pyWorks -and $python3StillGone) {
                        $rPyOnly = Invoke-Gate
                        Assert-Match '(?m)^threw: ' $rPyOnly.Text 'a working python with no python3 still stops the run'
                        Assert-Match '(?s)threw: .*ouro\.toml.*python3 3\.11\+' $rPyOnly.Text `
                            'the stop names python3, not the python that is actually on PATH'
                    }
                    else { Write-Host '  skip: the python shim is not a usable interpreter on this machine' -ForegroundColor DarkGray }
                }
                finally { $env:PATH = $pyOnlySaved }
            }
            finally { Remove-Item -LiteralPath $pyOnlyDir -Recurse -Force }

            # The same tree with no binding at all is the onboarding path, and it still falls back.
            if ($gitStillThere) {
                Remove-Item -LiteralPath $bindingPath -Force
                $r = Invoke-Gate
                Assert-Match 'not read \(no .*ouro\.toml\): diffing against origin/master' $r.Text `
                    'no binding at all is still the INFO line and the origin/master base'
                Assert-Equal 0 $r.Code 'no binding at all exits 0'
            }
            else { Write-Host '  skip: git is not reachable with python3 off PATH on this machine' -ForegroundColor DarkGray }
        }
    }
    finally {
        $env:PATH = $savedPath
        if ($gitLinkDirForShim) { Remove-Item -LiteralPath $gitLinkDirForShim -Recurse -Force }
    }

    # --- the remaining default--Base arms: a successful read, a missing tool, an empty value -----
    $bindingToolPath = Join-Path (Split-Path $Gate -Parent) 'ouro-binding.py'

    # A binding whose default_branch is read: the gate diffs against origin/<that branch>, not a
    # guess. There is no such ref in this clone, so the ref-missing INFO line names it -- proof
    # the branch came from the binding rather than the origin/master fallback.
    if ((Test-Path -LiteralPath $bindingToolPath) -and (Get-Command python3 -ErrorAction SilentlyContinue)) {
        New-Item -ItemType Directory -Force -Path (Join-Path $Repo '.claude') | Out-Null
        Set-Content -LiteralPath $bindingPath -Encoding utf8 `
            -Value 'schema = 1', '[repo]', 'slug = "example/repo"', 'default_branch = "trunk"'
        $r = Invoke-Gate
        Assert-Match 'INFO - origin/trunk is not a ref in this clone' $r.Text `
            "a binding's default_branch is read and diffed against, never guessed"
        Assert-Equal 0 $r.Code 'a missing origin/trunk ref still exits 0'
    }
    else { Write-Host '  skip: ouro-binding.py or python3 unavailable; the successful default-base read cannot run' -ForegroundColor DarkGray }

    # No ouro-binding.py beside the gate itself (a vendored copy without the tool): the fallback
    # fires and names that reason, never a guess at the branch. Independent of python3.
    New-Item -ItemType Directory -Force -Path (Join-Path $Repo '.claude') | Out-Null
    Set-Content -LiteralPath $bindingPath -Encoding utf8 `
        -Value 'schema = 1', '[repo]', 'slug = "example/repo"', 'default_branch = "trunk"'
    $vdir = Join-Path ([System.IO.Path]::GetTempPath()) ('changelog-vendored-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $vdir | Out-Null
    try {
        Copy-Item -LiteralPath $Gate -Destination $vdir
        $vGate = Join-Path $vdir (Split-Path $Gate -Leaf)
        $vtext = ''
        $vcode = 1
        try { $vtext = (& $vGate -RepoRoot $Repo 6>&1 | Out-String); $vcode = $LASTEXITCODE }
        catch { $vtext = "threw: $_" }
        Assert-Match 'not read \(no ouro-binding\.py beside this script\): diffing against origin/master' $vtext `
            'no ouro-binding.py beside a copied gate falls back to origin/master, not a guess'
        Assert-Equal 0 $vcode 'the missing-tool fallback still exits 0'
    }
    finally { Remove-Item -LiteralPath $vdir -Recurse -Force }

    # repo.default_branch declared and empty: the fallback fires and names that reason too.
    if ((Test-Path -LiteralPath $bindingToolPath) -and (Get-Command python3 -ErrorAction SilentlyContinue)) {
        Set-Content -LiteralPath $bindingPath -Encoding utf8 `
            -Value 'schema = 1', '[repo]', 'slug = "example/repo"', 'default_branch = ""'
        $r = Invoke-Gate
        Assert-Match 'not read \(repo\.default_branch is empty\): diffing against origin/master' $r.Text `
            'an empty default_branch falls back to origin/master rather than diffing against it'
        Assert-Equal 0 $r.Code 'the empty-branch fallback still exits 0'
    }
    else { Write-Host '  skip: ouro-binding.py or python3 unavailable; the empty-default-branch arm cannot run' -ForegroundColor DarkGray }
}
finally {
    if (Test-Path -LiteralPath $Repo) { Remove-Item -Recurse -Force -LiteralPath $Repo }
}

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall changelog-entry cases pass" -ForegroundColor Green
exit 0
