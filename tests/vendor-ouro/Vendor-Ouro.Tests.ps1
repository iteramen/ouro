<#
.SYNOPSIS
    End-to-end test of Vendor-Ouro.ps1 against scratch repos.
.DESCRIPTION
    The vendor script writes into a consumer's tree, so the assertions are about what a real run
    prints and leaves behind: its REFUSED lines, its count, the fixture's tree, and the targets of
    the links it met. Each case builds a fresh repo in TEMP and runs the script as a subprocess
    with -Into, the way a person runs it.

    Pinned: a plain repo receives every file the run lists and nothing else, with no REFUSED
    line. A destination that is a symbolic link is refused, live or dangling, and its target
    outside the tree is left unchanged, or not created: docs/ouro-contract.md,
    docs/ouro-docs-governance.md, and the curated .claude/rules/documentation.md, which is refused
    rather than named as already existing. A .claude that is a link to a directory outside the
    tree refuses every .claude/ destination with a line naming .claude, and nothing lands in the
    directory. The count leaves every refused file out, and -WhatIf prints the REFUSED lines and
    the count a real run prints, and writes nothing.

    The tail is read from a real run's output: the workflows line ends at the scripts directory
    with no parenthetical, a line of its own points the vendored skills' <ouro>/bin paths at that
    same directory, and the suppress_prefixes line holds the three bin paths the vendored binding
    doc cites beside its three present entries and no bare bin prefix, and one line names the two
    .gitignore entries the installer appends. A plugin root holding none
    of the ten files a run reaches by name warns once per missing file and plans the rest at
    exit 0.

    A -ScriptsDir that resolves outside the tree -- a `..` segment that walks out of it, a sibling
    whose name starts with the root's own, or a rooted path -- is refused before a single file is
    written, by a run and by -WhatIf alike. One nested inside receives the whole plan, and a `..`
    that walks back inside is not refused, under another case on Windows either: the plan carries
    what the test resolved, so the files land where it judged and the listing says so. Resolving
    to the root itself lands the whole plan there, through a link to the root too. `.. ` is the
    root only where a segment's trailing space is dropped, and a directory name inside the tree
    where it is kept: one case for both, differing only in where the scripts land.

    Vendored to the root beside a bin/ of the consumer's own, whose Get-RepoSlug.ps1 and
    apply-manifest.py throw on load, the vendored repo-slug suite and the vendored python suite
    each exit 0, so each loaded the flat script rather than the consumer's; and the vendored
    script-help suite prints its skip line and exits 0 though that bin/ is there, still skips when
    that bin/ holds a gate of the same name that is not the plugin's copy, a directory of that
    name, or a file it cannot read, and runs its guard instead once the gate's plugin copy sits
    under that bin/ byte for byte. The two rows that run a vendored suite invoking python3 skip
    where no `python3` at 3.11 or later is on PATH; the unreadable-gate row skips where the gate
    cannot be held open or the filesystem does not deny a read of a file held open with no sharing.

    A plugin root whose own name holds a '*', beside a sibling the pattern matches, vendors its
    own files and nothing of the sibling's -- the ten destinations a run reaches by name, which
    both roots hold under the same names, compared by content. A System-attribute file, and a
    skill directory that is one, are planned, and so is a link to a file. A link to a directory
    under a skill is not walked through, nor is one to the root above it.

    Only a runner that can create a symbolic link runs the link cases: Windows without the
    privilege prints the skip line for each, and the ubuntu runner is where they execute. The
    '*' case likewise runs only where that name can be created, which on Windows it cannot, and
    the System case only where that attribute can be set.

    A vendored tree (bin/Vendor-Ouro.ps1) copies the scripts flat and nothing it vendors from,
    so there the suite prints a skip line and exits 0.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Two levels up is the plugin root (suite under tests/<area>/) or, once vendored, the scripts dir.
$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Vendor = Join-Path $Base 'bin/Vendor-Ouro.ps1'
if (-not (Test-Path -LiteralPath $Vendor)) {
    if (Test-Path -LiteralPath (Join-Path $Base 'Vendor-Ouro.ps1')) {
        Write-Host 'skip: no bin/ beside the flat scripts (vendored layout); vendoring copies from the plugin' -ForegroundColor DarkGray
        exit 0
    }
    throw "bin/Vendor-Ouro.ps1 not found under $Base"
}

$failures = 0
# A long failing value is shortened to its length and where it first differs from the other side:
# a byte comparison's hex string can run to hundreds of kilobytes, and a failure line that printed
# it whole would flood the log.
function Get-ShortForm([string]$Value, [string]$Other) {
    if ($Value.Length -le 80) { return "'$Value'" }
    $max = [Math]::Min($Value.Length, $Other.Length)
    $i = 0
    while ($i -lt $max -and $Value[$i] -ceq $Other[$i]) { $i++ }
    "<length $($Value.Length), differs at $i>"
}
# -ceq: a byte-identical claim must not pass on a case-only difference.
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -ceq $Actual) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else {
        $e = [string]$Expected; $a = [string]$Actual
        Write-Host "  FAIL: $What -- expected $(Get-ShortForm $e $a), got $(Get-ShortForm $a $e)" -ForegroundColor Red
        $script:failures++
    }
}
# A plugin-root script the suite reads: a missing one is a red row naming it, not a throw that
# ends the suite before its summary. The layout check above reads bin/Vendor-Ouro.ps1 before any
# row, so the row can print for bin/Install-Ouro.ps1.
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
$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ('vendor-ouro-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $scratch | Out-Null

$script:fixtures = 0
function New-Fixture {
    $script:fixtures++
    $dir = Join-Path $scratch "repo$($script:fixtures)"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    git -C $dir init -q 2>$null
    (Resolve-Path -LiteralPath $dir).Path
}
# A new, empty directory outside every fixture: where a link points.
function New-Outside {
    $dir = Join-Path $scratch "outside$($script:fixtures)-$([guid]::NewGuid().ToString('N').Substring(0, 4))"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    (Resolve-Path -LiteralPath $dir).Path
}
# A symbolic link at $Rel in the fixture to $Target, which need not exist. Windows without the
# symbolic-link privilege cannot create one, and returns $false so the caller prints the skip line.
function New-Link([string]$Dir, [string]$Rel, [string]$Target) {
    $p = Join-Path $Dir $Rel
    New-Item -ItemType Directory -Force -Path (Split-Path $p -Parent) | Out-Null
    try { New-Item -ItemType SymbolicLink -Path $p -Target $Target -ErrorAction Stop | Out-Null; $true }
    catch { $false }
}
# The (plugin source, repo-relative destination) pairs a run reaches by name instead of by
# enumerating a directory.
$ByNamePairs = @(@('bin/apply-manifest.py', 'scripts/apply-manifest.py'),
    @('bin/drift-claims.py', 'scripts/drift-claims.py'),
    @('bin/external-audit.py', 'scripts/external-audit.py'),
    @('bin/forbidden-tokens.py', 'scripts/forbidden-tokens.py'),
    @('bin/ouro-binding.py', 'scripts/ouro-binding.py'),
    @('bin/record-counts.py', 'scripts/record-counts.py'),
    @('docs/binding.md', 'docs/ouro-binding.md'),
    @('docs/contract.md', 'docs/ouro-contract.md'),
    @('docs/docs-governance.md', 'docs/ouro-docs-governance.md'),
    @('templates/documentation-rule.md', '.claude/rules/documentation.md'))
$ByNameSources = @($ByNamePairs | ForEach-Object { $_[0] })
# A plugin root under $scratch: the script itself, the ten files a run reaches by name, and the
# $Files a run reaches by enumerating. Every file holds $Tag, so a copy says which root it is from.
# -NoByName leaves all ten out, which is the root a run must warn about rather than skip silently.
# The writes go through System.IO, since a root's name may hold a character a provider path globs.
function New-PluginRoot([string]$Name, [string]$Tag, [string[]]$Files, [switch]$NoByName) {
    $root = Join-Path $scratch $Name
    $named = if ($NoByName) { @() } else { $ByNameSources }
    foreach ($rel in $named + $Files) {
        $p = Join-Path $root $rel
        [System.IO.Directory]::CreateDirectory((Split-Path $p -Parent)) | Out-Null
        [System.IO.File]::WriteAllText($p, "$Tag $rel`n", $Utf8)
    }
    [System.IO.File]::Copy($Vendor, (Join-Path $root 'bin/Vendor-Ouro.ps1'))
    $root
}
# A link at $Path to $Target, made as the first $Kinds that leaves one behind, and $false where
# none does. New-Item reports success for a junction on Linux and creates nothing, so the answer
# is read back from the path rather than taken from the call.
function New-PluginLink([string]$Path, [string]$Target, [string[]]$Kinds = @('Junction', 'SymbolicLink')) {
    foreach ($kind in $Kinds) {
        try { New-Item -ItemType $kind -Path $Path -Target $Target -ErrorAction Stop | Out-Null } catch { }
        $link = if ([System.IO.Directory]::Exists($Path)) { [System.IO.DirectoryInfo]::new($Path).LinkTarget }
        else { [System.IO.FileInfo]::new($Path).LinkTarget }
        if ($link) { return $true }
    }
    $false
}
# The source side of a by-content pair: bytes as hex, read by path alone, '<no source>' where the
# root does not hold the file, and '<unreadable: ...>' where it exists but cannot be read -- none
# of the three is a hex string, so a comparison never mistakes one for a match.
function Get-RootHex([string]$Root, [string]$Rel) {
    $p = Join-Path $Root $Rel
    if (-not [System.IO.File]::Exists($p)) { return '<no source>' }
    try { [BitConverter]::ToString([System.IO.File]::ReadAllBytes($p)) }
    catch { "<unreadable: $($_.Exception.GetBaseException().Message)>" }
}
# The destination side: bytes as hex -- ReadAllText would drop a byte-order mark and decode UTF-16
# before comparing -- '<absent>' where the run wrote no destination, and '<unreadable: ...>' where
# it did but the file cannot be read back.
function Read-Hex([string]$Dir, [string]$Rel) {
    $p = Join-Path $Dir $Rel
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { return '<absent>' }
    try { [BitConverter]::ToString([System.IO.File]::ReadAllBytes($p)) }
    catch { "<unreadable: $($_.Exception.GetBaseException().Message)>" }
}
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
# Every entry below $Dir outside .git/: a file with its hash, a directory with a trailing slash, a
# symbolic link with its target.
function Get-TreeState([string]$Dir) {
    $root = (Resolve-Path -LiteralPath $Dir).Path
    @(Get-Entries $root | ForEach-Object {
            $rel = $_.FullName.Substring($root.Length).TrimStart('\', '/') -replace '\\', '/'
            if ($rel -ne '.git' -and $rel -notlike '.git/*') {
                if ($_.LinkTarget) { "$rel->$($_.LinkTarget)" }
                elseif ($_ -is [System.IO.DirectoryInfo]) { "$rel/" }
                else { "$rel=$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)" }
            }
        } | Sort-Object) -join '; '
}
# A path or value as one token of a -Command line: single-quoted, a quote inside it doubled.
function Get-Literal([string]$Value) { "'" + ($Value -replace "'", "''") + "'" }
# -Command, not -File: on Unix a wildcard in a native command's argument is expanded before the
# child starts, so -File under a root named with a '*' can start a sibling's copy of the script.
# A -Command line carries the path as written. A token spelled '-' plus word characters --
# `-match '^-\w+$'` -- is passed as is, and every other token is quoted, so a value holding a space
# reaches the child whole. The test reads the spelling, not the role: a value spelled that way is
# passed as is too, and a switch given a value (-WhatIf:$false) is quoted. No call passes either.
# $Environment is set on this process, which the child inherits, and put back after: one that was
# not set is removed, since on Windows SetEnvironmentVariable given $null from PowerShell leaves it
# set to an empty string, and an empty GIT_DIR makes git refuse a real root too.
function Invoke-Vendor([string]$Dir, [string[]]$Arguments = @(), [string]$Script = $Vendor, [hashtable]$Environment = @{}) {
    $rest = @($Arguments | ForEach-Object { if ($_ -match '^-\w+$') { $_ } else { Get-Literal $_ } })
    $saved = @{}
    foreach ($k in $Environment.Keys) {
        $saved[$k] = [Environment]::GetEnvironmentVariable($k)
        [Environment]::SetEnvironmentVariable($k, $Environment[$k])
    }
    try {
        $out = & $Pwsh -NoProfile -Command "& $(Get-Literal $Script) -Into $(Get-Literal $Dir) $($rest -join ' ')" 2>&1 | Out-String
        $code = $LASTEXITCODE
    }
    finally {
        foreach ($k in $saved.Keys) {
            if ($null -eq $saved[$k]) { Remove-Item -LiteralPath "Env:$k" -ErrorAction Ignore }
            else { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
        }
    }
    # SGR codes are stripped: a host that renders VT colours the child's warning stream, and a row
    # matches the text.
    [pscustomobject]@{ Code = $code; Lines = @(($out -replace '\x1b\[[0-9;]*m') -split '\r?\n') }
}
# The destinations a run lists as copied: the indented lines between its header and its count.
function Get-Listed($Run) {
    $in = $false
    @(foreach ($line in $Run.Lines) {
            if ($line -match '^\d+ file\(s\)\.$') { break }
            if ($in -and $line -match '^  (\S.*)$') { $Matches[1] }
            if ($line -match '^Vendoring ouro from ') { $in = $true }
        })
}
function Get-Refused($Run) { @($Run.Lines | Where-Object { $_ -like 'REFUSED: *' }) }
function Get-Count($Run) {
    $hit = @($Run.Lines | Where-Object { $_ -match '^\d+ file\(s\)\.$' })
    if ($hit.Count -eq 1) { [int]($hit[0] -replace ' .*') } else { -1 }
}
# A suite run out of a vendored fixture, as a subprocess: its exit code, and its output with the
# SGR codes stripped.
function Invoke-Child([string]$Exe, [string[]]$Arguments) {
    $out = & $Exe @Arguments 2>&1 | Out-String
    [pscustomobject]@{ Code = $LASTEXITCODE; Text = ($out -replace '\x1b\[[0-9;]*m') }
}
# A vendored suite's row. On a miss the child's own output is printed: that is what names the
# script it loaded.
function Assert-Child($Run, $What) {
    if ($Run.Code -eq 0) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- exit $($Run.Code):`n$($Run.Text)" -ForegroundColor Red; $script:failures++ }
}
# The path of `python3` on PATH at 3.11 or later -- the name the vendored scripts invoke, so python
# alone does not serve -- and $null where there is none.
function Find-Python {
    $cmd = @(Get-Command python3 -CommandType Application -ErrorAction SilentlyContinue) | Select-Object -First 1
    if (-not $cmd) { return $null }
    $ok = try { @(& $cmd.Source -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)' 2>$null) -join "`n" } catch { '' }
    if ($ok -match '(?m)^1$') { $cmd.Source } else { $null }
}

try {
    $noLink = '  skip: cannot create a symbolic link here (Windows without the privilege)'
    $notRead = 'REFUSED: {0} not written - it is a symbolic link, which git does not read'
    $notFollowed = 'REFUSED: {0} not written - {1} is a symbolic link, which git does not follow'

    # ── 6. the control: a plain repo receives every planned file, and nothing is refused ──
    # Runs first: the link cases compare against the plan it lists.
    $repo = New-Fixture
    $run = Invoke-Vendor $repo
    $Planned = @(Get-Listed $run)
    Assert-Equal 0 $run.Code 'a plain repo: the run exits 0'
    Assert-Equal 0 @(Get-Refused $run).Count 'and prints no REFUSED line'
    Assert-Equal $Planned.Count (Get-Count $run) 'and the count is of every file it lists'
    $files = @((Get-TreeState $repo) -split '; ' | Where-Object { $_ -match '=[0-9A-F]{64}$' } |
            ForEach-Object { $_ -replace '=[0-9A-F]{64}$' } | Sort-Object)
    Assert-Equal (@($Planned | Sort-Object) -join '; ') ($files -join '; ') 'and the tree holds exactly the files it lists'
    # A source that is not there must not read as a destination the run never wrote: the pairs
    # below compare one reader's answer with the other's, and would pass on two equal sentinels.
    Assert-Equal $false ((Get-RootHex $Base 'no/such/file') -ceq (Read-Hex $repo 'no/such/file')) `
        'and a path under neither root: the source reader and the destination reader disagree'
    Assert-Equal $true ($Planned -ccontains 'docs/ouro-binding.md') 'and the plan lists docs/ouro-binding.md'
    Assert-Equal 0 @($run.Lines | Where-Object { $_ -clike 'WARNING: *' }).Count 'and a complete plugin root warns about nothing'
    foreach ($pair in @(
            @('docs/contract.md', 'docs/ouro-contract.md'),
            @('docs/docs-governance.md', 'docs/ouro-docs-governance.md'),
            @('docs/binding.md', 'docs/ouro-binding.md'),
            @('templates/documentation-rule.md', '.claude/rules/documentation.md'),
            @('bin/apply-manifest.py', 'scripts/apply-manifest.py'),
            @('bin/drift-claims.py', 'scripts/drift-claims.py'),
            @('bin/forbidden-tokens.py', 'scripts/forbidden-tokens.py'),
            @('bin/record-counts.py', 'scripts/record-counts.py'),
            @('bin/Vendor-Ouro.ps1', 'scripts/Vendor-Ouro.ps1'))) {
        Assert-Equal $false ((Get-RootHex $Base $pair[0]) -ceq '<no source>') "and $($pair[0]) is there to compare"
        Assert-Equal (Get-RootHex $Base $pair[0]) (Read-Hex $repo $pair[1]) "and $($pair[1]) is $($pair[0]), byte for byte"
    }

    # ── Get-RootHex / Read-Hex on a file that exists but cannot be read ────────────────────
    # The row holds a file open with no sharing and proves the denial with a read of its own
    # first, as the script-help suite's unreadable-gate row does; where the technique does not
    # work here, it skips rather than asserting nothing. The control alongside it proves the
    # same fixture still hashes normally when it IS readable, so the row above is not passing
    # because nothing is ever compared.
    $readable = Join-Path $repo 'readable-431.bin'
    [System.IO.File]::WriteAllBytes($readable, [byte[]](1, 2, 3))
    Assert-Equal $true ((Get-RootHex $repo 'readable-431.bin') -cmatch '^[0-9A-F]{2}(-[0-9A-F]{2})*$') `
        'the control: a readable file hashes as hex'
    $unreadable = Join-Path $repo 'unreadable-431.bin'
    [System.IO.File]::WriteAllBytes($unreadable, [byte[]](1, 2, 3))
    $held = try { [System.IO.File]::Open($unreadable, 'Open', 'Read', 'None') } catch { $null }
    try {
        $denied = if ($held) { try { [void][System.IO.File]::ReadAllBytes($unreadable); $false } catch { $true } } else { $false }
        if (-not $held) { Write-Host '  skip: cannot hold a file open with no sharing here' -ForegroundColor DarkGray }
        elseif (-not $denied) { Write-Host '  skip: this filesystem does not deny a read of a file held open with no sharing' -ForegroundColor DarkGray }
        else {
            $rootHex = Get-RootHex $repo 'unreadable-431.bin'
            Assert-Equal $false ($rootHex -cmatch '^[0-9A-F]{2}(-[0-9A-F]{2})*$') 'Get-RootHex on an unreadable file answers a marker, not a hex string'
            Assert-Equal $false ($rootHex -ceq '<no source>') 'and not the missing-source sentinel'
            $destHex = Read-Hex $repo 'unreadable-431.bin'
            Assert-Equal $false ($destHex -cmatch '^[0-9A-F]{2}(-[0-9A-F]{2})*$') 'Read-Hex on an unreadable file answers a marker, not a hex string'
            Assert-Equal $false ($destHex -ceq '<absent>') 'and not the missing-destination sentinel'
        }
    }
    finally { if ($held) { $held.Dispose() } }

    # ── Assert-Equal shortens a long failing value ─────────────────────────────────────────
    # A byte comparison's hex string can run to hundreds of kilobytes; the failure line stays
    # bounded. This row deliberately triggers one FAIL to inspect the line it prints, and backs
    # the counter out afterward -- it is testing the reporter, not asserting a real equality.
    $longA = 'A' * 500
    $longB = ('A' * 40) + ('B' * 460)
    $before = $script:failures
    $captured = (& { Assert-Equal $longA $longB 'self-test: long value mismatch (reverted below)' } 6>&1 | Out-String)
    $script:failures = $before
    Assert-Equal $true ($captured.Length -lt 200) 'a long failing value is printed shortened, not whole'
    Assert-Equal $false ($captured -clike "*$longA*") 'and the FAIL line does not include the raw 500-character value'
    # The control: a short mismatch is still printed whole.
    $before = $script:failures
    $captured = (& { Assert-Equal 'abc' 'abd' 'self-test: short value mismatch (reverted below)' } 6>&1 | Out-String)
    $script:failures = $before
    Assert-Equal $true ($captured -clike "*'abc'*'abd'*") 'the control: a short failing value is still printed whole'

    # ── the tail: what the vendored tree still cites, left for a hand edit ────────────────
    # Read from the control run's output. $skipped is empty on a plain repo, so every '  - ' line
    # there is the fixed tail.
    $tail = @($run.Lines | Where-Object { $_ -clike '  - *' })
    $workflows = @($tail | Where-Object { $_ -cmatch '^  - \.github/workflows/\*: point every ouro/bin/' })
    Assert-Equal 1 $workflows.Count 'the tail points the workflows at the scripts directory once'
    # Joined, not indexed: a line the tail does not print is a red row of its own, not an index
    # out of bounds that ends the suite before the cases below it.
    Assert-Equal '  - .github/workflows/*: point every ouro/bin/ path at scripts/' ($workflows -join "`n") `
        'and that line ends there, naming no gate or suite of its own'
    $skillPaths = @($tail | Where-Object { $_ -cmatch '<ouro>/bin/' })
    Assert-Equal 1 $skillPaths.Count 'and one line names the plugin-relative bin paths the vendored skills carry'
    Assert-Equal $true (($skillPaths -join "`n") -clike '*scripts/*') 'and points them at the scripts directory'
    $suppress = @($tail | Where-Object { $_ -cmatch 'suppress_prefixes = ' })
    Assert-Equal 1 $suppress.Count 'and the suppress-prefixes line is printed once'
    # The values inside the brackets, not every quoted token on the line: an entry that slipped into
    # the reason text is not advice a consumer copies.
    $list = [regex]::Match(($suppress -join "`n"), 'suppress_prefixes = \[([^\]]*)\]').Groups[1].Value
    $values = @([regex]::Matches($list, '"([^"]+)"') | ForEach-Object { $_.Groups[1].Value })
    foreach ($prefix in 'bin/Get-RollingIssue.ps1', 'bin/Test-AgentReadyAnchors.ps1', 'bin/Test-AgentReadyShape.ps1',
        'docs/contract.md', 'docs/binding.md', 'templates/') {
        Assert-Equal $true ($values -ccontains $prefix) "holding $prefix"
    }
    Assert-Equal $false ($values -ccontains 'bin/') 'and no bare bin/ prefix, which would hide the scripts directory whole'
    Assert-Equal 6 $values.Count 'and nothing else'
    Assert-Equal $true (($suppress -join "`n") -clike '*docs/ouro-binding.md*') 'and its reason names the third vendored doc'
    # The installer's two .gitignore entries, which a vendored tree that never runs it lacks: without
    # the overlay's, the docs gate reports the vendored binding doc's own citations of that path.
    $ignore = @($tail | Where-Object { $_ -clike '  - .gitignore: *' })
    Assert-Equal 1 $ignore.Count 'the tail names the .gitignore entries once'
    foreach ($entry in '.claude/worktrees/', '.claude/ouro.local.toml') {
        Assert-Equal $true (($ignore -join "`n") -clike "*$entry*") "naming $entry"
    }
    Assert-Equal $true (($ignore -join "`n") -clike '*Install-Ouro.ps1*') 'and saying the installer is what writes them'

    # ── the root reached through a link is the root; a directory below it is not ───────────
    # A symbolic link where one can be made; on Windows without the privilege, a junction.
    $repo = New-Fixture
    $viaLink = Join-Path $scratch "via-$([guid]::NewGuid().ToString('N').Substring(0, 6))"
    $linked = try { New-Item -ItemType SymbolicLink -Path $viaLink -Target $repo -ErrorAction Stop | Out-Null; $true } catch { $false }
    if (-not $linked -and $IsWindows) {
        $linked = try { New-Item -ItemType Junction -Path $viaLink -Target $repo -ErrorAction Stop | Out-Null; $true } catch { $false }
    }
    if (-not $linked) { Write-Host '  skip: cannot create a link to the root here' -ForegroundColor DarkGray }
    else {
        $run = Invoke-Vendor $viaLink
        Assert-Equal 0 $run.Code '-Into the root reached through a link: the run exits 0'
        Assert-Equal 0 @($run.Lines | Where-Object { $_ -like '*is not the git root*' }).Count 'and nothing calls it anything but the root'
        Assert-Equal $true (Test-Path -LiteralPath (Join-Path $repo 'docs/ouro-contract.md')) 'and the files land in the real repo'
    }
    $repo = New-Fixture
    New-Item -ItemType Directory -Force -Path (Join-Path $repo 'sub') | Out-Null
    $run = Invoke-Vendor (Join-Path $repo 'sub')
    Assert-Equal $true ($run.Code -ne 0) 'a directory below the git root is refused'
    Assert-Equal $true (@($run.Lines | Where-Object { $_ -like '*is not the git root*' }).Count -gt 0) 'with the reason'

    # A work tree the repo points elsewhere: git prints an empty prefix outside that tree too, so
    # the prefix alone reads a directory git never looks at as a root, and copies the plan into it.
    $repo = New-Fixture
    $holder = Join-Path $scratch "holder-$([guid]::NewGuid().ToString('N').Substring(0, 6))"
    New-Item -ItemType Directory -Force -Path $holder | Out-Null
    git -C $holder init -q 2>$null | Out-Null
    git -C $holder config core.worktree ($repo -replace '\\', '/') 2>$null | Out-Null
    $run = Invoke-Vendor $holder
    Assert-Equal $true ($run.Code -ne 0) '-Into a directory whose work tree is elsewhere is refused'
    Assert-Equal $true (@($run.Lines | Where-Object { $_ -like '*is not the git root*' }).Count -gt 0) 'with the reason'
    Assert-Equal $false (Test-Path -LiteralPath (Join-Path $holder 'docs/ouro-contract.md')) 'and nothing is written there'

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
        $run = Invoke-Vendor $plain -Environment $vars
        Assert-Equal $true ($run.Code -ne 0) "-Into a directory that is no repository, with $what exported, is refused"
        Assert-Equal $true (@($run.Lines | Where-Object { $_ -match 'is not (inside a git work tree|the git root)' }).Count -gt 0) 'with the reason'
        Assert-Equal '' (Get-TreeState $plain) 'and nothing is written there'
    }
    # -WhatIf refuses it the same way, and reaches no ShouldProcess of its own: a run that unset
    # the variables through the Env: drive would keep them under -WhatIf and take the directory.
    $plain = Join-Path $scratch "plain-$([guid]::NewGuid().ToString('N').Substring(0, 6))"
    New-Item -ItemType Directory -Force -Path $plain | Out-Null
    $run = Invoke-Vendor $plain @('-WhatIf') -Environment @{ GIT_DIR = (Join-Path $repo '.git') }
    Assert-Equal $true ($run.Code -ne 0) 'and -WhatIf refuses it too'
    Assert-Equal 0 @($run.Lines | Where-Object { $_ -like 'What if:*' }).Count 'printing no What-if line'

    # Both scripts must unset through [NullString]::Value, and no run can tell that from behaviour
    # on one platform: on Windows $null leaves the variable set to an empty string, which makes git
    # refuse every root, while on Linux pwsh $null unsets it like [NullString]::Value. The source is
    # pinned instead, so the swap is red on either runner.
    foreach ($rel in 'bin/Install-Ouro.ps1', 'bin/Vendor-Ouro.ps1') {
        $src = Read-Root $rel
        Assert-Equal $true ($src -cmatch '(?m)^\s*\[Environment\]::SetEnvironmentVariable\(\$name, \[NullString\]::Value\)$') `
            "$rel unsets the git variables through [NullString]::Value"
    }

    # On Linux and macOS the link probe reads the link through System.IO before any Get-Item, which
    # there enumerates every ancestor directory of a directory. No timing pins that, so the source is.
    foreach ($rel in 'bin/Install-Ouro.ps1', 'bin/Vendor-Ouro.ps1') {
        $src = Read-Root $rel
        $m = [regex]::Match($src, '(?ms)^\s*function Test-SymbolicLink\(\[string\]\$Path\) \{\r?\n(.*?)^\s*\}')
        $body = if ($m.Success) { $m.Groups[1].Value } else { '' }
        $io = [regex]::Match($body, '(?m)^\s*if \(\$IsLinux -or \$IsMacOS\) \{ return \[bool\]\[System\.IO\.FileInfo\]::new\(\$Path\)\.LinkTarget \}')
        $gi = $body.IndexOf('Get-Item')
        Assert-Equal $true ($io.Success -and $gi -gt $io.Index) "$rel's Test-SymbolicLink reads LinkTarget on Linux and macOS before any Get-Item"
    }

    # A prefix is one path, and a path may hold a newline: a directory whose name starts with one
    # makes git print three lines, the second empty, which reads as a root. Not a name Windows makes.
    if ($IsWindows) { Write-Host '  skip: a newline is not a legal name here' -ForegroundColor DarkGray }
    else {
        $repo = New-Fixture
        $odd = Join-Path $repo "`nfoo"
        New-Item -ItemType Directory -Force -Path $odd | Out-Null
        $run = Invoke-Vendor $odd
        Assert-Equal $true ($run.Code -ne 0) '-Into a subdirectory whose name starts with a newline is refused'
        Assert-Equal $true (@($run.Lines | Where-Object { $_ -like '*is not the git root*' }).Count -gt 0) 'with the reason'
        Assert-Equal $false (Test-Path -LiteralPath (Join-Path $odd 'docs/ouro-contract.md')) 'and nothing is written there'
    }

    # ── -ScriptsDir that does not resolve inside the tree ─────────────────────────────────
    # The copy loop joins it to the root, so a `..` segment puts every script beside the repo,
    # where git never looks, and a rooted one makes a destination no write can reach -- which
    # died part way through the copy, after .claude/ had been written. Both are refused before
    # the plan is built, so the tree is untouched and -WhatIf refuses them the same way.
    foreach ($outside in '../escape', '..', 'an absolute path outside') {
        $repo = New-Fixture
        $target = New-Outside
        $arg = if ($outside -ceq 'an absolute path outside') { Join-Path $target 'scripts' } else { $outside }
        foreach ($mode in 'a run', '-WhatIf') {
            $extra = if ($mode -ceq '-WhatIf') { @('-WhatIf') } else { @() }
            $run = Invoke-Vendor $repo (@('-ScriptsDir', $arg) + $extra)
            Assert-Equal $true ($run.Code -ne 0) "-ScriptsDir $outside is refused ($mode)"
            Assert-Equal $true ((($run.Lines) -join ' ') -clike '*refusing: -ScriptsDir*') "with a refusal naming the parameter ($mode)"
            Assert-Equal '' (Get-TreeState $repo) "and nothing is written in the tree ($mode)"
            Assert-Equal '' (Get-TreeState $target) "and nothing outside it ($mode)"
        }
    }
    Assert-Equal $false (Test-Path -LiteralPath (Join-Path $scratch 'escape')) '../escape names no directory beside the repos'
    # A sibling whose name starts with the root's own: the destination starts with the root as a
    # string and is still outside it, so the test that decides has to compare whole path segments.
    $repo = New-Fixture
    $sibling = "$(Split-Path $repo -Leaf)x"
    $run = Invoke-Vendor $repo @('-ScriptsDir', "../$sibling/scripts")
    Assert-Equal $true ($run.Code -ne 0) "-ScriptsDir ../$sibling/scripts is refused"
    Assert-Equal $true ((($run.Lines) -join ' ') -clike '*refusing: -ScriptsDir*') 'with a refusal naming the parameter'
    Assert-Equal '' (Get-TreeState $repo) 'and nothing is written in the tree'
    Assert-Equal $false (Test-Path -LiteralPath (Join-Path $scratch $sibling)) 'and the sibling is never created'

    # ── -ScriptsDir nested inside the tree: the whole plan still lands ────────────────────
    $repo = New-Fixture
    $run = Invoke-Vendor $repo @('-ScriptsDir', 'tools/scripts')
    $nested = @($Planned | ForEach-Object { $_ -replace '^scripts/', 'tools/scripts/' } | Sort-Object)
    Assert-Equal 0 $run.Code '-ScriptsDir tools/scripts: the run exits 0'
    Assert-Equal 0 @(Get-Refused $run).Count 'and prints no REFUSED line'
    Assert-Equal $Planned.Count (Get-Count $run) 'and the count is of the whole plan'
    Assert-Equal ($nested -join '; ') (@(Get-Listed $run | Sort-Object) -join '; ') 'and it lists the plan under tools/scripts'
    $files = @((Get-TreeState $repo) -split '; ' | Where-Object { $_ -match '=[0-9A-F]{64}$' } |
            ForEach-Object { $_ -replace '=[0-9A-F]{64}$' } | Sort-Object)
    Assert-Equal ($nested -join '; ') ($files -join '; ') 'and the tree holds exactly those files'

    # ── the tail's three lines print the scripts directory with forward slashes ───────────
    # Windows keeps native separators in $ScriptsDir once it holds more than one segment; the
    # tail's own text is meant to read as a relative path a consumer pastes, not a native one.
    $repo = New-Fixture
    $run = Invoke-Vendor $repo @('-ScriptsDir', 'tools/sub')
    $tail = @($run.Lines | Where-Object { $_ -clike '  - *' })
    Assert-Equal 3 @($tail | Where-Object { $_ -cmatch 'tools/sub/' }).Count `
        "-ScriptsDir 'tools/sub': the three tail lines that interpolate it use forward slashes"
    Assert-Equal 0 @($tail | Where-Object { $_ -cmatch 'tools\\sub' }).Count 'and none of them keeps a backslash'

    # ── -ScriptsDir a value that starts with '-' and holds a space: bound whole ───────────
    # Invoke-Vendor's own predicate decides which tokens name a parameter; a value that merely
    # starts with '-' but holds a space fails `-match '^-\w+$'` and is quoted like any other
    # value, so it reaches -Command as one token instead of being split and re-parsed.
    $repo = New-Fixture
    $run = Invoke-Vendor $repo @('-ScriptsDir', '-a b')
    $dashed = @($Planned | ForEach-Object { $_ -replace '^scripts/', '-a b/' } | Sort-Object)
    Assert-Equal 0 $run.Code "-ScriptsDir '-a b': the run exits 0"
    Assert-Equal ($dashed -join '; ') (@(Get-Listed $run | Sort-Object) -join '; ') 'and it lists the plan under -a b'

    # ── the plan carries the resolved path, not the argument ─────────────────────────────
    # A `..` that walks back inside is not a refusal, and what it resolves to is where the files
    # go -- so the listing says scripts/, not the argument. Windows also drops a segment's
    # trailing spaces and dots, which is why the test's path and the copy's have to be the one
    # path: `.. ` resolves to the root there while a raw join keeps it, and the write died on a
    # directory of that name after .claude/ was already copied.
    $repo = New-Fixture
    $run = Invoke-Vendor $repo @('-ScriptsDir', 'tools/../scripts')
    Assert-Equal 0 $run.Code '-ScriptsDir tools/../scripts: the run exits 0'
    Assert-Equal (@($Planned | Sort-Object) -join '; ') (@(Get-Listed $run | Sort-Object) -join '; ') 'and it lists the plan under scripts'
    Assert-Equal $false (Test-Path -LiteralPath (Join-Path $repo 'tools')) 'and no tools directory is created'

    # Only Windows drops those trailing spaces, so only there does `.. ` name the root; a
    # filesystem that keeps them reads it as an ordinary directory name inside the tree, which
    # the provider creates nowhere -- the copy died part way through the plan. One fixture and
    # one run either way: only where the run lists the scripts, and where they land, differ.
    $repo = New-Fixture
    $run = Invoke-Vendor $repo @('-ScriptsDir', '.. ')
    $listedAt = if ($IsWindows) { './' } else { '.. /' }
    $landsAt = if ($IsWindows) { '' } else { '.. /' }
    $listed = @($Planned | ForEach-Object { $_ -replace '^scripts/', $listedAt } | Sort-Object)
    $landed = @($Planned | ForEach-Object { $_ -replace '^scripts/', $landsAt } | Sort-Object)
    Assert-Equal 0 $run.Code '-ScriptsDir ".. ": the run exits 0'
    Assert-Equal 0 @(Get-Refused $run).Count 'and prints no REFUSED line'
    Assert-Equal $Planned.Count (Get-Count $run) 'and every planned file is copied, none left by a part-way death'
    Assert-Equal ($listed -join '; ') (@(Get-Listed $run | Sort-Object) -join '; ') 'and it lists the plan where this filesystem spells `.. `'
    $files = @((Get-TreeState $repo) -split '; ' | Where-Object { $_ -match '=[0-9A-F]{64}$' } |
            ForEach-Object { $_ -replace '=[0-9A-F]{64}$' } | Sort-Object)
    Assert-Equal ($landed -join '; ') ($files -join '; ') 'and the tree holds exactly those files, none misplaced'
    Assert-Equal 0 @([System.IO.Directory]::GetFiles($scratch)).Count 'and nothing lands beside the repo'

    # `tools/..` is the root on either platform, where `.. ` is the root only on Windows.
    $repo = New-Fixture
    $run = Invoke-Vendor $repo @('-ScriptsDir', 'tools/..')
    Assert-Equal 0 $run.Code '-ScriptsDir tools/..: the run exits 0'
    Assert-Equal 0 @(Get-Refused $run).Count 'and prints no REFUSED line'
    Assert-Equal $Planned.Count (Get-Count $run) 'and copies every planned file'
    Assert-Equal $true (Test-Path -LiteralPath (Join-Path $repo 'Vendor-Ouro.ps1')) 'and the scripts land at the root'
    Assert-Equal $false (Test-Path -LiteralPath (Join-Path $repo 'tools')) 'and no tools directory is created'

    # ── the consumer's own bin/ beside the vendored flat scripts ─────────────────────────
    # Vendored to the root, a suite two levels below it resolves its subject from the consumer's
    # root, which may hold a bin/ of the consumer's own. Each decoy throws on load, so a suite
    # that read one fails with the decoy's message instead of testing the vendored script.
    $decoy = 'ouro-389-decoy'
    New-Item -ItemType Directory -Force -Path (Join-Path $repo 'bin') | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $repo 'bin/Get-RepoSlug.ps1'), "throw '$decoy'`n", $Utf8)
    [System.IO.File]::WriteAllText((Join-Path $repo 'bin/apply-manifest.py'), "raise RuntimeError('$decoy')`n", $Utf8)

    # The vendored resolver runs python3 by that name, so both children need it.
    $python = Find-Python
    if (-not $python) { Write-Host '  skip: no python3 3.11 or later on PATH as python3, which the vendored repo-slug and python suites run' -ForegroundColor DarkGray }
    else {
        $slugRun = Invoke-Child $Pwsh @('-NoProfile', '-File', (Join-Path $repo 'tests/repo-slug/Get-RepoSlug.Tests.ps1'))
        Assert-Child $slugRun 'the vendored repo-slug suite loads the flat resolver, not the decoy in the consumer''s bin/'
        $applyRun = Invoke-Child $python @((Join-Path $repo 'tests/apply-manifest/apply-manifest.Tests.py'))
        Assert-Child $applyRun 'the vendored python suite loads the flat applier, not the decoy in the consumer''s bin/'
    }

    $helpRun = Invoke-Child $Pwsh @('-NoProfile', '-File', (Join-Path $repo 'tests/script-help/Test-ScriptHelp.Tests.ps1'))
    Assert-Child $helpRun 'the vendored script-help suite exits 0 though the decoys gave the root a bin/'
    Assert-Equal $true ($helpRun.Text -cmatch '(?m)^skip: .*\(vendored layout\)') 'and says it skipped for the layout'
    # A consumer gate of the same name under bin/ is not the plugin's copy: the child still skips.
    [System.IO.File]::WriteAllText((Join-Path $repo 'bin/Test-DocsFreshness.ps1'), "throw '$decoy'`n", $Utf8)
    $helpRun = Invoke-Child $Pwsh @('-NoProfile', '-File', (Join-Path $repo 'tests/script-help/Test-ScriptHelp.Tests.ps1'))
    Assert-Child $helpRun 'a consumer gate of the same name under bin/ leaves the script-help suite skipping'
    Assert-Equal $true ($helpRun.Text -cmatch '(?m)^skip: .*\(vendored layout\)') 'and it says so'
    # A gate of that name the child cannot read is not the plugin's copy either: the child still
    # skips rather than throwing. The row holds the file open with no sharing and proves the denial
    # with a read of its own first; where the file cannot be held, or the filesystem ignores the
    # lock, it prints a skip line.
    $held = try { [System.IO.File]::Open((Join-Path $repo 'bin/Test-DocsFreshness.ps1'), 'Open', 'Read', 'None') } catch { $null }
    try {
        $denied = if ($held) { try { [void][System.IO.File]::ReadAllBytes((Join-Path $repo 'bin/Test-DocsFreshness.ps1')); $false } catch { $true } } else { $false }
        if (-not $held) { Write-Host '  skip: the gate cannot be held open with no sharing here' -ForegroundColor DarkGray }
        elseif (-not $denied) { Write-Host '  skip: this filesystem does not deny a read of a file held open with no sharing' -ForegroundColor DarkGray }
        else {
            $helpRun = Invoke-Child $Pwsh @('-NoProfile', '-File', (Join-Path $repo 'tests/script-help/Test-ScriptHelp.Tests.ps1'))
            Assert-Child $helpRun 'a gate of that name the child cannot read leaves the script-help suite skipping'
            Assert-Equal $true ($helpRun.Text -cmatch '(?m)^skip: .*\(vendored layout\)') 'and it says so too'
        }
    }
    finally { if ($held) { $held.Dispose() } }
    # A directory of that name is not a file to compare: the child still skips rather than throwing.
    [System.IO.File]::Delete((Join-Path $repo 'bin/Test-DocsFreshness.ps1'))
    [System.IO.Directory]::CreateDirectory((Join-Path $repo 'bin/Test-DocsFreshness.ps1')) | Out-Null
    $helpRun = Invoke-Child $Pwsh @('-NoProfile', '-File', (Join-Path $repo 'tests/script-help/Test-ScriptHelp.Tests.ps1'))
    Assert-Child $helpRun 'a directory of the gate''s name under bin/ leaves the script-help suite skipping'
    [System.IO.Directory]::Delete((Join-Path $repo 'bin/Test-DocsFreshness.ps1'))
    # The gate's plugin copy under bin/, byte for byte, says the tree is the plugin's, and the guard
    # runs over that bin/: the decoy there exposes no help, so the child fails instead of skipping.
    Copy-Item -LiteralPath (Join-Path $repo 'Test-DocsFreshness.ps1') -Destination (Join-Path $repo 'bin/Test-DocsFreshness.ps1')
    $helpRun = Invoke-Child $Pwsh @('-NoProfile', '-File', (Join-Path $repo 'tests/script-help/Test-ScriptHelp.Tests.ps1'))
    Assert-Equal $true ($helpRun.Code -ne 0 -and $helpRun.Text -cnotmatch '(?m)^skip: ' -and $helpRun.Text -cmatch 'exposes its help description') `
        'with the gate under bin/ as well, the script-help suite runs its guard over that bin/ instead of skipping'

    # Every script's destination then starts with `.`, and `<root>/.` is the root again -- so the
    # link walk, which must never ask about the root, answered for it: a root reached through a
    # link refused all 50 scripts and tests and left a half-vendored tree at exit 0.
    # A symbolic link and nothing else: Test-SymbolicLink answers for that link type alone, so a
    # junction root -- what the git-root case above falls back to, its own subject being git's
    # root detection -- cannot reach the walk, and this row would assert nothing while passing.
    $repo = New-Fixture
    $viaLink = Join-Path $scratch "viaroot-$([guid]::NewGuid().ToString('N').Substring(0, 6))"
    $linked = try { New-Item -ItemType SymbolicLink -Path $viaLink -Target $repo -ErrorAction Stop | Out-Null; $true } catch { $false }
    if (-not $linked) { Write-Host $noLink -ForegroundColor DarkGray }
    else {
        $run = Invoke-Vendor $viaLink @('-ScriptsDir', 'tools/..')
        Assert-Equal 0 $run.Code '-ScriptsDir resolving to a root reached through a link: the run exits 0'
        Assert-Equal 0 @(Get-Refused $run).Count 'and nothing is refused for the root being a link'
        Assert-Equal $Planned.Count (Get-Count $run) 'and every planned file is copied'
    }

    # Windows names one directory in any case, so a `..` that walks back in under another spelling
    # is the same directory -- and an ordinal test called it outside the tree.
    if ($IsWindows) {
        $repo = New-Fixture
        $run = Invoke-Vendor $repo @('-ScriptsDir', "../$((Split-Path $repo -Leaf).ToUpperInvariant())/scripts")
        Assert-Equal 0 $run.Code 'a `..` back into the root under another case: the run exits 0'
        Assert-Equal $Planned.Count (Get-Count $run) 'and the whole plan lands'
    }
    else { Write-Host '  skip: this filesystem tells two cases apart' -ForegroundColor DarkGray }

    # ── 1. a live link at docs/ouro-contract.md ──────────────────────────────────────────
    $repo = New-Fixture
    $target = Join-Path (New-Outside) 'contract.md'
    [System.IO.File]::WriteAllText($target, "outside, untouched`n", $Utf8)
    if (-not (New-Link $repo 'docs/ouro-contract.md' $target)) { Write-Host $noLink -ForegroundColor DarkGray }
    else {
        $run = Invoke-Vendor $repo
        Assert-Equal 0 $run.Code 'a live link at docs/ouro-contract.md: the run exits 0'
        Assert-Equal ($notRead -f 'docs/ouro-contract.md') (@(Get-Refused $run) -join "`n") 'and one REFUSED line names it'
        Assert-Equal "outside, untouched`n" ([System.IO.File]::ReadAllText($target)) 'and the target outside the tree is unchanged'
        Assert-Equal ($Planned.Count - 1) (Get-Count $run) 'and the count leaves it out'
        Assert-Equal (@($Planned | Where-Object { $_ -cne 'docs/ouro-contract.md' }) -join '; ') (@(Get-Listed $run) -join '; ') `
            'and the run goes on: every other planned file is listed'
    }

    # ── 2. a dangling link at docs/ouro-docs-governance.md ────────────────────────────────
    # The target's directory exists, so a write through the link would create the target.
    $repo = New-Fixture
    $target = Join-Path (New-Outside) 'governance.md'
    if (-not (New-Link $repo 'docs/ouro-docs-governance.md' $target)) { Write-Host $noLink -ForegroundColor DarkGray }
    else {
        $run = Invoke-Vendor $repo
        Assert-Equal 0 $run.Code 'a dangling link at docs/ouro-docs-governance.md: the run exits 0'
        Assert-Equal ($notRead -f 'docs/ouro-docs-governance.md') (@(Get-Refused $run) -join "`n") 'and one REFUSED line names it'
        Assert-Equal $false (Test-Path -LiteralPath $target) 'and the target is not created'
        Assert-Equal ($Planned.Count - 1) (Get-Count $run) 'and the count leaves it out'
    }

    # ── 3. a live and a dangling link at the curated rule ────────────────────────────────
    # The rule is skipped when present, and Test-Path answers for a link's target -- True for a
    # dangling one on Linux. A link is refused instead: a hand merge would edit its target.
    foreach ($kind in 'live', 'dangling') {
        $repo = New-Fixture
        $target = Join-Path (New-Outside) 'rule.md'
        if ($kind -eq 'live') { [System.IO.File]::WriteAllText($target, "my own rule`n", $Utf8) }
        if (-not (New-Link $repo '.claude/rules/documentation.md' $target)) { Write-Host $noLink -ForegroundColor DarkGray; continue }
        $run = Invoke-Vendor $repo
        Assert-Equal 0 $run.Code "a $kind link at .claude/rules/documentation.md: the run exits 0"
        Assert-Equal ($notRead -f '.claude/rules/documentation.md') (@(Get-Refused $run) -join "`n") "and one REFUSED line names it ($kind)"
        Assert-Equal 0 @($run.Lines | Where-Object { $_ -match 'already exists' }).Count "and no line names it as already existing ($kind)"
        if ($kind -eq 'live') { Assert-Equal "my own rule`n" ([System.IO.File]::ReadAllText($target)) 'and the target is unchanged' }
        else { Assert-Equal $false (Test-Path -LiteralPath $target) 'and the target is not created' }
    }

    # ── 4. .claude a link to a directory outside the tree ────────────────────────────────
    $claude = @($Planned | Where-Object { $_ -like '.claude/*' })
    Assert-Equal $true ($claude.Count -gt 1) 'the plan holds .claude/ destinations to refuse'
    $repo = New-Fixture
    $outside = New-Outside
    if (-not (New-Link $repo '.claude' $outside)) { Write-Host $noLink -ForegroundColor DarkGray }
    else {
        $run = Invoke-Vendor $repo
        Assert-Equal 0 $run.Code '.claude a link to a directory outside the tree: the run exits 0'
        Assert-Equal (@($claude | ForEach-Object { $notFollowed -f $_, '.claude' }) -join "`n") (@(Get-Refused $run) -join "`n") `
            'and every .claude/ destination is refused with a line naming .claude'
        Assert-Equal '' (Get-TreeState $outside) 'and nothing lands in the link target'
        Assert-Equal ($Planned.Count - $claude.Count) (Get-Count $run) 'and the count leaves them out'
    }

    # ── 4b. .claude a link to a directory that already holds the curated rule ──────────────
    # The rule's existence check must not follow the link either: "merge it by hand" would have
    # the owner edit a file outside the tree.
    $repo = New-Fixture
    $outside = New-Outside
    New-Item -ItemType Directory -Force -Path (Join-Path $outside 'rules') | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $outside 'rules/documentation.md'), "OUTSIDE-RULE`n")
    if (-not (New-Link $repo '.claude' $outside)) { Write-Host $noLink -ForegroundColor DarkGray }
    else {
        $run = Invoke-Vendor $repo
        Assert-Equal 0 $run.Code '.claude a link to a directory holding the rule: the run exits 0'
        Assert-Equal 1 @(Get-Refused $run | Where-Object { $_ -ceq ($notFollowed -f '.claude/rules/documentation.md', '.claude') }).Count `
            'and the rule is refused with a line naming .claude'
        Assert-Equal 0 @($run.Lines | Where-Object { $_ -like '*already exists*' }).Count 'and no line calls it a file to merge by hand'
        Assert-Equal "OUTSIDE-RULE`n" ([System.IO.File]::ReadAllText((Join-Path $outside 'rules/documentation.md'))) `
            'and the file outside the tree is unchanged'
    }

    # ── 5. -WhatIf prints the REFUSED lines a real run prints, and writes nothing ─────────
    $repo = New-Fixture
    $targets = New-Outside
    [System.IO.File]::WriteAllText((Join-Path $targets 'contract.md'), "outside, untouched`n", $Utf8)
    $outside = New-Outside
    $linked = @((New-Link $repo 'docs/ouro-contract.md' (Join-Path $targets 'contract.md')),
        (New-Link $repo 'docs/ouro-docs-governance.md' (Join-Path $targets 'governance.md')),
        (New-Link $repo '.claude' $outside)) -notcontains $false
    if (-not $linked) { Write-Host $noLink -ForegroundColor DarkGray }
    else {
        $before = (Get-TreeState $repo), (Get-TreeState $targets), (Get-TreeState $outside) -join ' | '
        $dry = Invoke-Vendor $repo @('-WhatIf')
        Assert-Equal 0 $dry.Code '-WhatIf over three links: the run exits 0'
        Assert-Equal $before ((Get-TreeState $repo), (Get-TreeState $targets), (Get-TreeState $outside) -join ' | ') `
            'and it writes nothing, in the tree or at a link target'
        $run = Invoke-Vendor $repo
        Assert-Equal (2 + $claude.Count) @(Get-Refused $run).Count 'and the real run refuses all three'
        Assert-Equal (@(Get-Refused $run) -join "`n") (@(Get-Refused $dry) -join "`n") 'and -WhatIf printed the same REFUSED lines'
        Assert-Equal (Get-Count $run) (Get-Count $dry) 'and the same count'
    }

    # A plugin root of its own for the cases below: what a run enumerates, what it lists when it
    # enumerates that root, and the ten destinations it reaches by name instead.
    $ownFiles = @('bin/Real-Gate.ps1', 'skills/real/SKILL.md', 'agents/real.md', 'tests/real/Real.Tests.ps1')
    $enumPlan = @('.claude/agents/real.md', '.claude/skills/real/SKILL.md', 'scripts/Real-Gate.ps1',
        'scripts/Vendor-Ouro.ps1', 'scripts/tests/real/Real.Tests.ps1')
    $ownPlan = @($enumPlan + @($ByNamePairs | ForEach-Object { $_[1] })) | Sort-Object
    $byName = $ByNamePairs

    # ── a plugin root holding none of the ten files a run reaches by name ────────────────
    # Each of the ten is one warning naming it, and the enumerated plan still lands: a silent skip
    # delivers the applier's fixture suite without the applier, at exit 0.
    $bare = New-PluginRoot 'bareroot' 'real' $ownFiles -NoByName
    $repo = New-Fixture
    $run = Invoke-Vendor $repo -Script (Join-Path $bare 'bin/Vendor-Ouro.ps1')
    Assert-Equal 0 $run.Code 'a plugin root missing every by-name source: the run exits 0'
    foreach ($rel in $ByNameSources) {
        Assert-Equal 1 @($run.Lines | Where-Object {
                $_ -cmatch ('^WARNING: ' + [regex]::Escape($rel) + ' not found in the plugin at .+ - skipped$')
            }).Count "and one warning names $rel"
    }
    Assert-Equal $ByNamePairs.Count @($run.Lines | Where-Object { $_ -clike 'WARNING: *' }).Count 'and warns about nothing else'
    Assert-Equal (@($enumPlan | Sort-Object) -join '; ') (@(Get-Listed $run | Sort-Object) -join '; ') `
        'and plans the rest'

    # ── 7. a plugin root whose own name holds a '*' ───────────────────────────────────────
    # On Linux, Get-ChildItem -LiteralPath globs a '*' or '?' in the directory's own path, so the
    # plan is read out of whatever sibling the pattern matches -- and that tree lands in the
    # consumer's repository at exit 0. The sibling holds the ten by-name destinations under the
    # same names, so those are compared by content: a provider path that globs the same way would
    # copy the sibling's bytes to the right destination. Where such a name cannot be created, the
    # case skips.
    $star = try { New-PluginRoot 'plug*1' 'real' $ownFiles } catch { $null }
    if (-not $star) { Write-Host '  skip: a `*` is not a legal name for a directory here' -ForegroundColor DarkGray }
    else {
        $null = New-PluginRoot 'plugab1' 'sibling' @('bin/Sibling.ps1', 'skills/sibling/SKILL.md',
            'agents/sibling.md', 'tests/sibling/Sibling.Tests.ps1')
        $repo = New-Fixture
        $run = Invoke-Vendor $repo -Script (Join-Path $star 'bin/Vendor-Ouro.ps1')
        Assert-Equal 0 $run.Code 'a plugin root named with a `*`: the run exits 0'
        Assert-Equal ($ownPlan -join '; ') (@(Get-Listed $run | Sort-Object) -join '; ') `
            'and it lists its own files, none of the sibling it matches'
        $files = @((Get-TreeState $repo) -split '; ' | Where-Object { $_ -match '=[0-9A-F]{64}$' } |
                ForEach-Object { $_ -replace '=[0-9A-F]{64}$' } | Sort-Object)
        Assert-Equal ($ownPlan -join '; ') ($files -join '; ') 'and the tree holds exactly those files'
        foreach ($pair in $byName) {
            Assert-Equal (Get-RootHex $star $pair[0]) (Read-Hex $repo $pair[1]) `
                "and $($pair[1]) is its own root's $($pair[0]), byte for byte"
        }
    }

    # ── 8. a System-attribute file, and a skill directory that is one, are planned ────────
    # A plain Get-ChildItem leaves out Hidden alone, so the enumeration skips Hidden alone:
    # skipping System with it drops such a file, and such a skill whole, at exit 0.
    $sysRoot = New-PluginRoot 'sysroot' 'real' $ownFiles
    [System.IO.Directory]::CreateDirectory((Join-Path $sysRoot 'skills/sys')) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $sysRoot 'skills/sys/SKILL.md'), "real`n", $Utf8)
    [System.IO.File]::WriteAllText((Join-Path $sysRoot 'skills/real/sys.md'), "real`n", $Utf8)
    $system = @((Join-Path $sysRoot 'skills/sys'), (Join-Path $sysRoot 'skills/real/sys.md'))
    $isSystem = @($system | ForEach-Object {
            $i = if ([System.IO.Directory]::Exists($_)) { [System.IO.DirectoryInfo]::new($_) } else { [System.IO.FileInfo]::new($_) }
            try { $i.Attributes = $i.Attributes -bor [System.IO.FileAttributes]::System } catch { }
            ($i.Attributes -band [System.IO.FileAttributes]::System) -ne 0
        }) -notcontains $false
    if (-not $isSystem) { Write-Host '  skip: no System attribute can be set here' -ForegroundColor DarkGray }
    else {
        $repo = New-Fixture
        $run = Invoke-Vendor $repo -Script (Join-Path $sysRoot 'bin/Vendor-Ouro.ps1')
        $expected = @($ownPlan + '.claude/skills/real/sys.md' + '.claude/skills/sys/SKILL.md') | Sort-Object
        Assert-Equal 0 $run.Code 'a System-attribute file and skill directory: the run exits 0'
        Assert-Equal ($expected -join '; ') (@(Get-Listed $run | Sort-Object) -join '; ') 'and both are in the plan'
    }

    # ── 9. a link under a skill: a file is planned, a directory is not walked into ───────
    # A recursive enumeration that does not stop at a link to a directory copies the target's
    # files into the consumer's repository, and one to an ancestor recurses until the path is too
    # long to hold, which kills the run.
    $lnkRoot = New-PluginRoot 'lnkroot' 'real' $ownFiles
    $outside = New-Outside
    [System.IO.File]::WriteAllText((Join-Path $outside 'OUTSIDE.md'), "outside`n", $Utf8)
    # A junction where one can be made -- Windows needs no privilege for it -- else a symbolic link.
    $made = @(foreach ($pair in @(@('skills/real/lnk', $outside), @('skills/real/loop', $lnkRoot))) {
            New-PluginLink (Join-Path $lnkRoot $pair[0]) $pair[1]
        }) -notcontains $false
    if (-not $made) { Write-Host '  skip: no link of either kind can be made here' -ForegroundColor DarkGray }
    else {
        # A link to a file is planned like any other file: only a link to a directory stops the
        # walk. Windows without the symbolic-link privilege cannot make one, and a junction is a
        # link to a directory, so there this half prints the skip line.
        $linked = @(foreach ($rel in 'skills/real/link.md', 'tests/real/link.md') {
                New-PluginLink (Join-Path $lnkRoot $rel) (Join-Path $outside 'OUTSIDE.md') @('SymbolicLink')
            }) -notcontains $false
        $expected = @($ownPlan)
        if ($linked) { $expected = @($expected + '.claude/skills/real/link.md' + 'scripts/tests/real/link.md') }
        else { Write-Host '  skip: no link to a file can be made here' -ForegroundColor DarkGray }
        $repo = New-Fixture
        $run = Invoke-Vendor $repo -Script (Join-Path $lnkRoot 'bin/Vendor-Ouro.ps1')
        Assert-Equal 0 $run.Code 'a link under a skill, and one to the root above it: the run exits 0'
        Assert-Equal ((@($expected | Sort-Object)) -join '; ') (@(Get-Listed $run | Sort-Object) -join '; ') `
            'and the plan holds what the root itself holds, nothing reached through a link to a directory'
    }
}
finally {
    if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force }
}

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall vendor-ouro cases pass" -ForegroundColor Green
exit 0
