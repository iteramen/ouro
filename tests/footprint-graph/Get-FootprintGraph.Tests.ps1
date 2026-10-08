<#
.SYNOPSIS
    Fixture suite for Get-FootprintGraph.ps1: collisions, batches, waves and ruling leverage.
.DESCRIPTION
    Builds a scratch git repository in TEMP with a few tracked files and one untracked file, runs
    the script in a child pwsh against -IssuesJson, and parses its stdout. Fifteen rows: a shared
    file (collision, batch, two waves); disjoint footprints (one wave); a Doc impact declaration
    as the only mention, inline and as a heading section; a mirrored file (no link, still in the
    footprint, and the control without -Mirrored plus the case and ./ near misses); an issue with no
    path (unplaced, and the control of a mirrored-only footprint, which is placed); needs-ruling
    leverage ranked by count, then number; a glob expanded against the tracked files only; a path
    that resolves to nothing; the same issues in another order giving byte-identical stdout; batches
    capped at five, merged along the strongest collisions and, on a tie, the lower a then the lower b
    (rows 12 to 15); a
    failing read, through a gh stand-in function that answers every call it is given and records
    each one (exit 1, empty stdout, the stand-in's own text on stderr, and the usage and no-
    repository exits beside it); and a read that returns 500 rows, with a 499-row control.
    It needs pwsh and git only, so a Windows leg with no bash runs every row.
#>
$ErrorActionPreference = 'Stop'

# Two levels up is the plugin root (suite under tests/<area>/) or, once vendored, the scripts dir.
$Base   = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Script = @((Join-Path $Base 'Get-FootprintGraph.ps1'), (Join-Path $Base 'bin/Get-FootprintGraph.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Script) { throw "Get-FootprintGraph.ps1 not found under $Base" }
$pwshExe = (Get-Process -Id $PID).Path
$bt = '`'

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -ceq $Actual) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}
function Assert-Match($Pattern, $Text, $What) {
    if ($Text -match $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- no match for '$Pattern' in:`n$Text" -ForegroundColor Red; $script:failures++ }
}
function Assert-NoMatch($Pattern, $Text, $What) {
    if ($Text -notmatch $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- unexpected match for '$Pattern' in:`n$Text" -ForegroundColor Red; $script:failures++ }
}

# A parsed value as a short string, nested arrays kept nested: [[1,2],[3]].
function Format-Value($V) {
    if ($V -isnot [array]) { return "$V" }
    $parts = foreach ($x in $V) { Format-Value $x }
    '[' + ($parts -join ',') + ']'
}

function Invoke-Pwsh([string[]]$Argv, [string]$Cwd, [hashtable]$Environment = @{}) {
    $psi = [System.Diagnostics.ProcessStartInfo]::new($pwshExe)
    foreach ($a in (@('-NoProfile', '-File') + $Argv)) { $psi.ArgumentList.Add($a) }
    $psi.WorkingDirectory = $Cwd
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $psi.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    foreach ($k in $Environment.Keys) { $psi.Environment[$k] = $Environment[$k] }
    $p = [System.Diagnostics.Process]::Start($psi)
    $out = $p.StandardOutput.ReadToEndAsync()
    $err = $p.StandardError.ReadToEndAsync()
    $p.WaitForExit()
    [pscustomobject]@{ Out = $out.Result; Err = $err.Result; Code = $p.ExitCode }
}

function New-Issue([int]$Number, [string]$Label, [string]$Body, [string]$Title = '') {
    [ordered]@{ number = $Number; title = $(if ($Title) { $Title } else { "issue $Number" }); body = $Body; labels = @(@{ name = $Label }) }
}
function Invoke-Graph($Issues, [string[]]$Extra = @()) {
    $json = ConvertTo-Json -InputObject @($Issues) -Depth 6 -Compress
    $r = Invoke-Pwsh (@($Script, '-IssuesJson', $json) + $Extra) $scratchPath
    $parsed = if ($r.Out) { try { $r.Out | ConvertFrom-Json } catch { $null } } else { $null }
    [pscustomobject]@{ Out = $r.Out; Err = $r.Err; Code = $r.Code; Json = $parsed }
}
function Format-Collisions($Json) { (@($Json.collisions) | ForEach-Object { "$($_.a)-$($_.b):$(Format-Value $_.files)" }) -join '|' }
function Format-Leverage($Json) { (@($Json.leverage) | ForEach-Object { "$($_.number):$($_.count):$(Format-Value $_.gates)" }) -join '|' }
function Get-Footprint($Json, [int]$N) { Format-Value (@($Json.issues | Where-Object number -eq $N)[0].footprint) }

$scratch = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('footprint-graph-test-' + [guid]::NewGuid().ToString('n')))
$scratchPath = $scratch.FullName
$bareDir = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('footprint-graph-bare-' + [guid]::NewGuid().ToString('n')))
try {
    # --- the scratch repository: tracked files, one untracked file, a commit, an origin --------
    foreach ($d in 'src', 'src/sub', 'docs') { New-Item -ItemType Directory -Path (Join-Path $scratchPath $d) | Out-Null }
    $cafe = 'docs/caf' + [char]0xE9 + '.md'
    foreach ($f in 'src/a.txt', 'src/b.txt', 'src/c.txt', 'src/Makefile', 'src/sub/d.txt', 'docs/notes.md', 'docs/log.md', 'README.md', 'CLAUDE.md', $cafe) {
        Set-Content -LiteralPath (Join-Path $scratchPath $f) -Value "words in $f"
    }
    git -C $scratchPath init -q
    git -C $scratchPath add .
    git -C $scratchPath -c user.name=suite -c user.email=suite@example.invalid -c commit.gpgsign=false commit -q -m fixture
    if ($LASTEXITCODE -ne 0) { throw 'the scratch repository took no commit' }
    Set-Content -LiteralPath (Join-Path $scratchPath 'src/untracked.txt') -Value 'on disk, never added'
    Set-Content -LiteralPath (Join-Path $scratchPath 'TODO.md') -Value 'a root file on disk, never added'
    git -C $scratchPath remote add origin https://github.com/o/n.git
    git -C $bareDir.FullName init -q

    # --- row 1: two issues anchoring one file collide, batch together, and take two waves -------
    $g = Invoke-Graph @((New-Issue 1 'agent-ready' "- ${bt}src/a.txt${bt} -- the file"), (New-Issue 2 'agent-ready' "- ${bt}src/a.txt${bt} -- the same file"))
    Assert-Equal 0 $g.Code 'row 1: exit 0'
    Assert-Equal '1-2:[src/a.txt]' (Format-Collisions $g.Json) 'row 1: one collision naming the shared file'
    Assert-Equal '[[1,2]]' (Format-Value $g.Json.batches) 'row 1: one batch of both'
    Assert-Equal '[[1],[2]]' (Format-Value $g.Json.waves) 'row 1: two waves'
    Assert-Equal 2 $g.Json.schema 'row 1: schema is 2'

    # --- row 2: disjoint footprints -------------------------------------------------------------
    $g = Invoke-Graph @((New-Issue 1 'agent-ready' "${bt}src/a.txt${bt}"), (New-Issue 2 'agent-ready' "${bt}src/b.txt${bt}"))
    Assert-Equal '' (Format-Collisions $g.Json) 'row 2: no collision'
    Assert-Equal '[]' (Format-Value $g.Json.batches) 'row 2: no batch'
    Assert-Equal '[[1,2]]' (Format-Value $g.Json.waves) 'row 2: one wave holding both'

    # State comes from the labels: both labels read as agent-ready, a case variant still reads, and
    # neither label skips the issue with a warning.
    $both = New-Issue 3 'needs-ruling' "${bt}src/a.txt${bt}"; $both.labels = @(@{ name = 'needs-ruling' }, @{ name = 'Agent-Ready' })
    $neither = New-Issue 4 'bug' "${bt}src/a.txt${bt}"
    $g = Invoke-Graph @((New-Issue 1 'agent-ready' "${bt}src/a.txt${bt}"), $both, $neither)
    Assert-Equal '1-3:[src/a.txt]' (Format-Collisions $g.Json) 'row 2: an issue carrying both labels counts as agent-ready, whatever the label case'
    Assert-Equal '1,3' (@($g.Json.issues | ForEach-Object number) -join ',') 'row 2: an issue with neither label is not read'
    Assert-Match 'issue #4 carries neither' $g.Err 'row 2: and the skip is named on stderr'

    # --- row 3: a Doc impact declaration is the only mention (inline and heading forms) ---------
    $g = Invoke-Graph @(
        (New-Issue 1 'agent-ready' "Spec only.`nDoc impact on close: ${bt}docs/notes.md${bt}"),
        (New-Issue 2 'agent-ready' "- ${bt}docs/notes.md${bt} -- anchored"),
        (New-Issue 3 'agent-ready' "## Why`n`nprose`n`n### Doc impact on close`n`n${bt}docs/notes.md${bt}`n`n### Later`n`nprose"))
    Assert-Equal '1-2:[docs/notes.md]|1-3:[docs/notes.md]|2-3:[docs/notes.md]' (Format-Collisions $g.Json) 'row 3: the inline and the heading declaration each collide with the anchor and with each other'
    Assert-Equal '[docs/notes.md]' (Get-Footprint $g.Json 1) 'row 3: the inline declaration is in the footprint'
    $g = Invoke-Graph @(
        (New-Issue 1 'agent-ready' "Spec.`n`n## Doc impact on close: ${bt}docs/notes.md${bt}`n`nprose"),
        (New-Issue 2 'agent-ready' "Spec.`nDoc impact on close - ${bt}docs/log.md${bt}: rewritten"),
        (New-Issue 3 'agent-ready' "- ${bt}docs/notes.md${bt}"), (New-Issue 4 'agent-ready' "- ${bt}docs/log.md${bt}"))
    Assert-Equal '1-3:[docs/notes.md]|2-4:[docs/log.md]' (Format-Collisions $g.Json) 'row 3: a path on the heading line, or after a later colon on the inline line, still counts'
    $g = Invoke-Graph @((New-Issue 1 'agent-ready' "Spec only.`nDoc impact on close: ${bt}docs/log.md${bt}"), (New-Issue 2 'agent-ready' "- ${bt}docs/notes.md${bt}"))
    Assert-Equal '' (Format-Collisions $g.Json) 'row 3 control: a declaration naming another file collides with nothing'

    # --- row 4: a mirrored file links no two issues and stays in both footprints ----------------
    $pair = @((New-Issue 1 'agent-ready' "${bt}docs/log.md${bt} ${bt}src/a.txt${bt}"), (New-Issue 2 'agent-ready' "${bt}docs/log.md${bt} ${bt}src/b.txt${bt}"))
    $g = Invoke-Graph $pair @('-Mirrored', 'docs/log.md')
    Assert-Equal '' (Format-Collisions $g.Json) 'row 4: sharing only a mirrored file is no collision'
    Assert-Equal '[[1,2]]' (Format-Value $g.Json.waves) 'row 4: and the two share a wave'
    Assert-Equal 'docs/log.md,src/a.txt' (Get-Footprint $g.Json 1).Trim('[]') 'row 4: the mirrored file is still in issue 1''s footprint'
    Assert-Equal 'docs/log.md,src/b.txt' (Get-Footprint $g.Json 2).Trim('[]') 'row 4: and in issue 2''s'
    Assert-Equal '[docs/log.md]' (Format-Value $g.Json.mirrored) 'row 4: the mirrored list is echoed'
    $g = Invoke-Graph $pair @('-Mirrored', 'src/zzz.txt,docs/log.md')
    Assert-Equal '' (Format-Collisions $g.Json) 'row 4: a comma list, as pwsh -File passes it, names each path'
    Assert-Equal '[docs/log.md,src/zzz.txt]' (Format-Value $g.Json.mirrored) 'row 4: and is echoed split and sorted'
    $g = Invoke-Graph $pair @('-Mirrored', 'src/zzz.txt, docs/log.md')
    Assert-Equal '' (Format-Collisions $g.Json) 'row 4: a space after the comma is not part of the path'
    Assert-Equal '[docs/log.md,src/zzz.txt]' (Format-Value $g.Json.mirrored) 'row 4: and the echoed list is trimmed'
    $g = Invoke-Graph $pair
    Assert-Equal '1-2:[docs/log.md]' (Format-Collisions $g.Json) 'row 4 control: without -Mirrored the same file collides'
    $g = Invoke-Graph $pair @('-Mirrored', 'DOCS/log.md')
    Assert-Equal '[DOCS/log.md]' (Format-Value $g.Json.mirrored) 'row 4 control: a mirrored path nothing normalizes is echoed as given'
    $g = Invoke-Graph $pair @('-Mirrored', 'DOCS/log.md')
    Assert-Equal '1-2:[docs/log.md]' (Format-Collisions $g.Json) 'row 4 near miss: a case variant of the mirrored path mirrors nothing'
    $g = Invoke-Graph $pair @('-Mirrored', './docs/log.md')
    Assert-Equal '' (Format-Collisions $g.Json) 'row 4 near miss: a ./ spelling of the mirrored path still mirrors it'
    Assert-Equal '[docs/log.md]' (Format-Value $g.Json.mirrored) 'row 4 (A4): and the list echoes the spelling the link was made with, not the caller''s ./ one'
    $g = Invoke-Graph $pair @('-Mirrored', ('.' + [char]92 + 'docs' + [char]92 + 'log.md'))
    Assert-Equal '[docs/log.md]' (Format-Value $g.Json.mirrored) 'row 4 (A4): a backslash spelling is echoed with slashes'
    Assert-Equal '' (Format-Collisions $g.Json) 'row 4 (A4): and mirrors the same file'

    # --- row 5: no path at all is unplaced; a mirrored-only footprint is not ---------------------
    $g = Invoke-Graph @((New-Issue 1 'agent-ready' "${bt}src/a.txt${bt}"), (New-Issue 3 'agent-ready' 'prose only, no path'), (New-Issue 4 'agent-ready' "${bt}docs/log.md${bt}")) @('-Mirrored', 'docs/log.md')
    Assert-Equal '[3]' (Format-Value $g.Json.unplaced) 'row 5: the pathless issue is unplaced'
    Assert-Equal '[[1,4]]' (Format-Value $g.Json.waves) 'row 5: it is in no wave, and the mirrored-only issue is placed'

    # --- row 6: leverage ranks needs-ruling issues by the ready work their build would collide with
    $g = Invoke-Graph @(
        (New-Issue 1 'agent-ready' "${bt}src/a.txt${bt}"), (New-Issue 2 'agent-ready' "${bt}src/b.txt${bt}"), (New-Issue 3 'agent-ready' "${bt}src/c.txt${bt}"),
        (New-Issue 10 'needs-ruling' "${bt}src/c.txt${bt}"),
        (New-Issue 20 'needs-ruling' "${bt}src/a.txt${bt} ${bt}src/b.txt${bt}"),
        (New-Issue 5 'needs-ruling' "${bt}src/a.txt${bt}"),
        (New-Issue 30 'needs-ruling' 'no path'))
    Assert-Equal '20:2:[1,2]|5:1:[1]|10:1:[3]|30:0:[]' (Format-Leverage $g.Json) 'row 6: count descending, then number; a pathless ruling issue is listed with 0'
    Assert-Equal '[[1,2,3]]' (Format-Value $g.Json.waves) 'row 6: needs-ruling issues take no part in collisions or waves'

    # --- row 7: a glob span expands against the tracked files ------------------------------------
    $g = Invoke-Graph @((New-Issue 1 'agent-ready' "${bt}src/*.txt${bt}"), (New-Issue 2 'agent-ready' "${bt}src/b.txt${bt}"), (New-Issue 3 'agent-ready' "${bt}src/?.txt${bt}"), (New-Issue 4 'agent-ready' "${bt}nomatch/*.md${bt}"), (New-Issue 5 'agent-ready' "${bt}src/sub/d.txt${bt}"))
    Assert-Equal '[src/a.txt,src/b.txt,src/c.txt,src/sub/d.txt]' (Get-Footprint $g.Json 1) 'row 7: * expands to the tracked files, a nested one included, not the untracked one on disk'
    Assert-Equal '[src/a.txt,src/b.txt,src/c.txt]' (Get-Footprint $g.Json 3) 'row 7: ? expands the same way'
    Assert-Equal '1-2:[src/b.txt]|1-3:[src/a.txt,src/b.txt,src/c.txt]|1-5:[src/sub/d.txt]|2-3:[src/b.txt]' (Format-Collisions $g.Json) 'row 7: the expanded files collide with the literal ones, the nested file included'
    Assert-Equal '[nomatch/*.md]' (Get-Footprint $g.Json 4) 'row 7: a glob that matches nothing stays as written'

    $g = Invoke-Graph @((New-Issue 1 'agent-ready' "${bt}$cafe${bt}" "title caf$([char]0xE9)"), (New-Issue 2 'agent-ready' "${bt}docs/caf*.md${bt}"))
    Assert-Equal "[$cafe]" (Get-Footprint $g.Json 1) 'row 7: a tracked non-ASCII name is cited literally and resolves'
    Assert-Equal "[$cafe]" (Get-Footprint $g.Json 2) 'row 7: and is reached by a glob'
    Assert-Equal "1-2:[$cafe]" (Format-Collisions $g.Json) 'row 7: the two collide on it'
    Assert-Equal '[]' (Format-Value @($g.Json.issues)[0].unresolved) 'row 7: and it is not unresolved'
    Assert-Equal "title caf$([char]0xE9)" @($g.Json.issues)[0].title 'row 7: a non-ASCII title is written as UTF-8'

    # --- row 8: a path that resolves to nothing stays and is listed unresolved -------------------
    $g = Invoke-Graph @((New-Issue 1 'agent-ready' "${bt}nope/gone.md${bt} ${bt}src/a.txt${bt}"))
    Assert-Equal '[nope/gone.md,src/a.txt]' (Get-Footprint $g.Json 1) 'row 8: the unresolvable path is kept in the footprint'
    Assert-Equal '[nope/gone.md]' (Format-Value @($g.Json.issues)[0].unresolved) 'row 8: and listed unresolved, without the tracked file'

    # --- row 9: another arrival order, byte-identical stdout --------------------------------------
    $set = @(
        (New-Issue 7 'agent-ready' "${bt}src/a.txt${bt} ${bt}src/*.txt${bt}"), (New-Issue 3 'agent-ready' "${bt}src/b.txt${bt}"), (New-Issue 9 'agent-ready' 'prose'),
        (New-Issue 4 'needs-ruling' "${bt}src/c.txt${bt}"), (New-Issue 12 'needs-ruling' "${bt}docs/notes.md${bt}"), (New-Issue 5 'agent-ready' "${bt}docs/notes.md${bt}"))
    $first = Invoke-Graph $set
    $rev = [object[]]@($set); [array]::Reverse($rev)
    $second = Invoke-Graph $rev
    Assert-Equal 0 $second.Code 'row 9: the reordered run exits 0'
    Assert-Match '"number": 12' $first.Out 'row 9: the output holds the issues'
    Assert-NoMatch "`r" $first.Out 'row 9: the output has LF line endings only'
    Assert-Match "`n\z" $first.Out 'row 9: and ends with one'
    Assert-Equal $first.Out $second.Out 'row 9: reversed input gives byte-identical stdout'
    Assert-Equal ($first.Out | ConvertFrom-Json | ConvertTo-Json -Depth 9 -Compress) ($second.Out | ConvertFrom-Json | ConvertTo-Json -Depth 9 -Compress) 'row 9: and the same parsed object'

    # --- rows 12 to 14: batches are fuse sets of at most five, merged along the strongest collisions
    $sevenOnA = @(1..7 | ForEach-Object { New-Issue $_ 'agent-ready' "- ${bt}src/a.txt${bt}" })
    $g = Invoke-Graph $sevenOnA
    Assert-Equal '[[1,2,3,4,5],[6,7]]' (Format-Value $g.Json.batches) 'row 12: seven issues on one file split at five, the rest a batch of their own'
    Assert-Equal 21 @($g.Json.collisions).Count 'row 12: collisions still lists every pair, across the batches too'
    $g = Invoke-Graph @($sevenOnA[0..4])
    Assert-Equal '[[1,2,3,4,5]]' (Format-Value $g.Json.batches) 'row 12 control: exactly five issues stay one batch'

    $sixOnA = @(1..6 | ForEach-Object { New-Issue $_ 'agent-ready' $(if ($_ -ge 5) { "- ${bt}src/a.txt${bt} ${bt}src/b.txt${bt}" } else { "- ${bt}src/a.txt${bt}" }) })
    $g = Invoke-Graph $sixOnA
    Assert-Equal '[[1,2,3,4],[5,6]]' (Format-Value $g.Json.batches) 'row 13: the pair sharing two files is taken first and stays together'

    $edgeFiles = [ordered]@{ 'src/a.txt' = (1, 5); 'src/b.txt' = (1, 7); 'src/c.txt' = (2, 4); 'src/sub/d.txt' = (2, 5); 'docs/notes.md' = (2, 6); 'docs/log.md' = (3, 4) }
    $tied = @(1..8 | ForEach-Object {
        $n = $_
        $cited = @($edgeFiles.Keys | Where-Object { $edgeFiles[$_] -contains $n } | ForEach-Object { "${bt}$_${bt}" })
        New-Issue $n 'agent-ready' $(if ($cited) { '- ' + ($cited -join ' ') } else { 'prose, no path' })
    })
    $g = Invoke-Graph $tied
    Assert-Equal '[[1,2,4,5,7]]' (Format-Value $g.Json.batches) 'row 14: collisions of equal strength go lower a first, then lower b, and joins past five are refused'
    Assert-Equal '[8]' (Format-Value $g.Json.unplaced) 'row 14: an issue with no path is unplaced, not batched'
    $revTied = [object[]]@($tied); [array]::Reverse($revTied)
    $g2 = Invoke-Graph $revTied
    Assert-Equal $g.Out $g2.Out 'row 14: the same graph in reverse arrival order gives byte-identical stdout'

    # row 15: a merged group's first member need not be its lowest, so batches are ordered after the merge
    $g = Invoke-Graph @(
        (New-Issue 1 'agent-ready' "${bt}src/a.txt${bt}"), (New-Issue 2 'agent-ready' "${bt}src/b.txt${bt}"),
        (New-Issue 3 'agent-ready' "${bt}src/c.txt${bt} ${bt}src/sub/d.txt${bt}"), (New-Issue 6 'agent-ready' "${bt}src/c.txt${bt}"),
        (New-Issue 7 'agent-ready' "${bt}src/a.txt${bt} ${bt}src/sub/d.txt${bt}"), (New-Issue 8 'agent-ready' "${bt}src/b.txt${bt}"))
    Assert-Equal '[[1,3,6,7],[2,8]]' (Format-Value $g.Json.batches) 'row 15: batches are sorted by their lowest member, not by the order the merge built them'

    # --- row 16 (A2): a wave excludes every earlier member's files, not only the first's ----------
    $g = Invoke-Graph @((New-Issue 1 'agent-ready' "${bt}src/a.txt${bt}"), (New-Issue 2 'agent-ready' "${bt}src/b.txt${bt}"), (New-Issue 3 'agent-ready' "${bt}src/b.txt${bt}"))
    Assert-Equal '[[1,2],[3]]' (Format-Value $g.Json.waves) 'row 16: the third issue waits for the second, whose file entered the wave after the first'
    $g = Invoke-Graph @((New-Issue 1 'agent-ready' "${bt}src/a.txt${bt}"), (New-Issue 2 'agent-ready' "${bt}src/b.txt${bt}"), (New-Issue 3 'agent-ready' "${bt}src/c.txt${bt}"))
    Assert-Equal '[[1,2,3]]' (Format-Value $g.Json.waves) 'row 16 control: three disjoint issues share one wave'

    # --- row 17 (A1): a root-level file named in backticks enters the footprint --------------------
    $g = Invoke-Graph @(
        (New-Issue 1 'agent-ready' "- ${bt}README.md${bt} -- the file"),
        (New-Issue 2 'agent-ready' "- ${bt}README.md:12${bt} and ${bt}CLAUDE.md#top${bt}"),
        (New-Issue 3 'agent-ready' "Spec.`nDoc impact on close: ${bt}CLAUDE.md${bt}"),
        (New-Issue 4 'agent-ready' "Spec.`n`n### Doc impact on close`n`n${bt}README.md${bt}`n`n### Later`n`nprose"))
    Assert-Equal '[README.md]' (Get-Footprint $g.Json 1) 'row 17: a backticked tracked root file in the body is in the footprint'
    Assert-Equal '[CLAUDE.md,README.md]' (Get-Footprint $g.Json 2) 'row 17: a :line or #frag after it is stripped, as the anchor rule strips it'
    Assert-Equal '[CLAUDE.md]' (Get-Footprint $g.Json 3) 'row 17: the inline Doc impact line counts'
    Assert-Equal '[README.md]' (Get-Footprint $g.Json 4) 'row 17: and the Doc impact heading section'
    Assert-Equal '1-2:[README.md]|1-4:[README.md]|2-3:[CLAUDE.md]|2-4:[README.md]' (Format-Collisions $g.Json) 'row 17: so the root files collide'
    Assert-Equal '[]' (Format-Value @($g.Json.issues)[0].unresolved) 'row 17: and a tracked one is not unresolved'
    $g = Invoke-Graph @((New-Issue 5 'agent-ready' "${bt}TODO.md${bt} ${bt}readme.md${bt} ${bt}Readme.md${bt} ${bt}throughput${bt} ${bt}README.md#top please${bt} ${bt}README${bt} ${bt}src${bt} ${bt}src/Makefile${bt}"))
    Assert-Equal '[]' (Get-Footprint $g.Json 5) 'row 17 near misses: an untracked root name, a case variant, a slashless word, a span with a space, a name that is not a file and a separator span the anchor rule does not read are all dropped'
    Assert-Equal '[5]' (Format-Value $g.Json.unplaced) 'row 17: and the issue is unplaced'
    $g = Invoke-Graph @((New-Issue 1 'agent-ready' "${bt}README.md${bt} ${bt}src/a.txt${bt}"), (New-Issue 2 'agent-ready' "${bt}README.md${bt} ${bt}src/b.txt${bt}")) @('-Mirrored', 'README.md')
    Assert-Equal 'README.md,src/a.txt' (Get-Footprint $g.Json 1).Trim('[]') 'row 17: a mirrored root file stays in the footprint'
    Assert-Equal '' (Format-Collisions $g.Json) 'row 17: and links no two issues'
    $g = Invoke-Graph @((New-Issue 1 'agent-ready' "${bt}README.md${bt}"), (New-Issue 2 'agent-ready' "${bt}README.md${bt}"))
    Assert-Equal '1-2:[README.md]' (Format-Collisions $g.Json) 'row 17 control: without -Mirrored it collides'

    # --- row 18 (R2-A1): only a bare name, a line suffix or a #frag follows a root file's name ------
    $g = Invoke-Graph @((New-Issue 1 'agent-ready' "${bt}README.md:docs/notes.md${bt} ${bt}src/a.txt${bt}"), (New-Issue 2 'agent-ready' "${bt}README.md${bt} ${bt}src/b.txt${bt}"))
    Assert-Equal '[src/a.txt]' (Get-Footprint $g.Json 1) 'row 18: a <repo>:<path> citation of another tree adds no local root file'
    Assert-Equal '[[1,2]]' (Format-Value $g.Json.waves) 'row 18: so the two issues share a wave'
    Assert-Equal '' (Format-Collisions $g.Json) 'row 18: and do not collide'
    $g = Invoke-Graph @((New-Issue 1 'agent-ready' "${bt}README.md:20${bt}"), (New-Issue 2 'agent-ready' "${bt}README.md:20-30${bt}"), (New-Issue 3 'agent-ready' "${bt}README.md#top${bt}"))
    Assert-Equal '[README.md]' (Get-Footprint $g.Json 1) 'row 18: a :line suffix counts'
    Assert-Equal '[README.md]' (Get-Footprint $g.Json 2) 'row 18: a :line-line range counts'
    Assert-Equal '[README.md]' (Get-Footprint $g.Json 3) 'row 18: a #frag counts'
    $g = Invoke-Graph @((New-Issue 4 'agent-ready' "${bt}ouro:README.md${bt} ${bt}README.md:20-${bt} ${bt}README.md:x${bt} ${bt}README.md:${bt} ${bt}README.md:20:30${bt} ${bt}README.md#${bt}"))
    Assert-Equal '[]' (Get-Footprint $g.Json 4) 'row 18 near misses: a repo-prefixed name, an open range, a non-numeric suffix, an empty one, two suffixes and an empty fragment add nothing'
    Assert-Equal '[4]' (Format-Value $g.Json.unplaced) 'row 18: and the issue is unplaced'

    # --- row 19 (F1): spans are paired the way the anchor parser pairs them ---------------------------------
    $dbt = $bt + $bt
    $g = Invoke-Graph @(
        (New-Issue 1 'agent-ready' "|step|file|`n|---|---|`n|${bt}git log -1${bt}|${bt}README.md${bt}|"),
        (New-Issue 2 'agent-ready' "${bt}x y${bt},${bt}README.md${bt}"),
        (New-Issue 3 'agent-ready' "${bt}x y${bt}/${bt}CLAUDE.md${bt}"),
        (New-Issue 4 'agent-ready' "${dbt}README.md${dbt}"),
        (New-Issue 5 'agent-ready' "${dbt}docs/notes.md${dbt}"),
        (New-Issue 6 'agent-ready' "| step | file |`n|---|---|`n| ${bt}git log -1${bt} | ${bt}README.md${bt} |"))
    Assert-Equal '[README.md]' (Get-Footprint $g.Json 1) 'row 19: a compact table cell pair does not hide the root file'
    Assert-Equal '[README.md]' (Get-Footprint $g.Json 2) 'row 19: a span with a space, a comma, then a root file'
    Assert-Equal '[CLAUDE.md]' (Get-Footprint $g.Json 3) 'row 19: a span with a space, a slash, then a root file'
    Assert-Equal '[README.md]' (Get-Footprint $g.Json 4) 'row 19: a double-backtick root file reads as the parser reads it, as its inner span'
    Assert-Equal '[docs/notes.md]' (Get-Footprint $g.Json 5) 'row 19 control: the parser reads a double-backtick separator path the same way'
    Assert-Equal '[README.md]' (Get-Footprint $g.Json 6) 'row 19 control: the spaced table reads the same'
    $g = Invoke-Graph @((New-Issue 7 'agent-ready' "${bt}git log -1${bt}|${bt}x y${bt}|${bt}README.md please${bt}"))
    Assert-Equal '[]' (Get-Footprint $g.Json 7) 'row 19 control: spans holding whitespace, and the punctuation between them, add nothing'
    $g = Invoke-Graph @((New-Issue 8 'agent-ready' "${bt} README.md ${bt}"))
    Assert-Equal '[README.md]' (Get-Footprint $g.Json 8) 'row 19: padding inside a span is trimmed, as the parser trims it'

    # --- the gh stand-in: answers every call, records each one, proves itself (rows 10 and 11) ---
    $stub = Join-Path $scratchPath 'stub-wrapper.ps1'
    @'
function gh {
    $call = $args -join ' '
    Add-Content -LiteralPath $env:STUB_LOG -Value $call
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'repo') { return 'o/n' }
    if ($args[0] -eq 'issue' -and $args[1] -eq 'list') {
        $label = $args[[array]::IndexOf($args, '--label') + 1]
        $rArg = if ($args -contains '-R') { $args[[array]::IndexOf($args, '-R') + 1] } else { '' }
        Add-Content -LiteralPath $env:STUB_LOG -Value "ENV label=$label GH_REPO=$env:GH_REPO R=$rArg"
        $row = { param($n, $l, $b) '{"number":' + $n + ',"title":"stub","body":"' + $b + '","labels":[{"name":"' + $l + '"}]}' }
        $many = { param($count, $l) '[' + ((1..$count | ForEach-Object { & $row $_ $l 'x' }) -join ',') + ']' }
        switch ("$env:STUB_MODE|$label") {
            'fail-first|agent-ready'   { $global:LASTEXITCODE = 1; return 'stub-read-failed' }
            'fail-second|needs-ruling' { $global:LASTEXITCODE = 1; return 'stub-read-failed' }
            'empty-first|agent-ready'  { return '' }
            'blank-first|agent-ready'  { return '   ' }
            'object-second|needs-ruling' { return '{"a":1}' }
            'ok|agent-ready'           { return '[' + (& $row 1 'agent-ready' ('`src/a.txt`')) + ']' }
            'ok|needs-ruling'          { return '[' + (& $row 2 'needs-ruling' ('`src/a.txt`')) + ']' }
            'full-ready|agent-ready'   { return (& $many 500 'agent-ready') }
            'near-full|agent-ready'    { return (& $many 499 'agent-ready') }
            'full-ruling|needs-ruling' { return (& $many 500 'needs-ruling') }
            default                    { return '[]' }
        }
    }
    Add-Content -LiteralPath $env:STUB_LOG -Value 'UNEXPECTED-CALL'
    $global:LASTEXITCODE = 99
}
& $env:STUB_SCRIPT
exit $LASTEXITCODE
'@ | Set-Content -LiteralPath $stub
    function Invoke-Stubbed([string]$Mode, [string]$Cwd = $scratchPath) {
        $log = Join-Path $scratchPath ("stub-$Mode.log")
        if (Test-Path -LiteralPath $log) { Remove-Item -LiteralPath $log }
        $r = Invoke-Pwsh @($stub) $Cwd @{ STUB_MODE = $Mode; STUB_LOG = $log; STUB_SCRIPT = $Script; GH_REPO = 'caller/other' }
        $calls = if (Test-Path -LiteralPath $log) { @(Get-Content -LiteralPath $log) } else { @() }
        [pscustomobject]@{ Out = $r.Out; Err = $r.Err; Code = $r.Code; Calls = $calls -join "`n" }
    }

    # The stand-in proves itself before a row relies on it: its slug and its issues are the only
    # source of the repo and the footprint in this run.
    $ok = Invoke-Stubbed 'ok'
    Assert-Equal 0 $ok.Code 'stand-in: the happy path exits 0'
    Assert-Match '"repo": "o/n"' $ok.Out 'stand-in: the slug is the stand-in''s answer'
    Assert-Match 'issue list --state open --label agent-ready --limit 500 --json number,title,body,labels' $ok.Calls 'stand-in: the agent-ready read was made as specified'
    Assert-Match 'issue list --state open --label needs-ruling --limit 500 --json number,title,body,labels' $ok.Calls 'stand-in: the needs-ruling read was made as specified'
    Assert-Equal '2:1:[1]' (Format-Leverage ($ok.Out | ConvertFrom-Json)) 'stand-in: its two issues were read and ranked'
    Assert-NoMatch 'UNEXPECTED-CALL' $ok.Calls 'stand-in: no call it did not answer'

    # --- row 20 (R3-A1): both issue reads address the bound repository, whatever GH_REPO the caller holds
    Assert-Equal 2 @($ok.Calls -split "`n" | Where-Object { $_ -match '^ENV label=' }).Count 'row 20: the stand-in recorded the environment of both issue reads'
    Assert-Equal 2 @($ok.Calls -split "`n" | Where-Object { $_ -match '^ENV label=\S+ GH_REPO=o/n ' }).Count 'row 20: each read ran with GH_REPO set to the slug the origin names'
    Assert-NoMatch 'caller/other' $ok.Calls 'row 20: and neither read addressed the caller''s own GH_REPO'

    # --- row 10: a failing read exits 1 with empty stdout -----------------------------------------
    foreach ($mode in 'fail-first', 'fail-second') {
        $f = Invoke-Stubbed $mode
        Assert-Equal 1 $f.Code "row 10: $mode exits 1"
        Assert-Equal '' $f.Out "row 10: $mode writes nothing to stdout"
        Assert-Match 'stub-read-failed' $f.Err "row 10: $mode names the failing read's own output on stderr"
        Assert-NoMatch 'UNEXPECTED-CALL' $f.Calls "row 10: $mode made no call the stand-in did not answer"
    }
    $f = Invoke-Stubbed 'fail-first'
    Assert-Match '--label agent-ready' $f.Err 'row 10: the error names the label that failed'
    foreach ($mode in 'empty-first', 'blank-first', 'object-second') {
        $e = Invoke-Stubbed $mode
        Assert-Equal 1 $e.Code "row 10: $mode (gh exits 0 with no JSON array) exits 1"
        Assert-Equal '' $e.Out "row 10: $mode writes nothing to stdout"
        Assert-Match 'did not return a JSON array' $e.Err "row 10: $mode says the read was not an array"
    }
    $nr = Invoke-Stubbed 'ok' $bareDir.FullName
    Assert-Equal 1 $nr.Code 'row 10: no slug and no -IssuesJson exits 1'
    Assert-Equal '' $nr.Out 'row 10: and writes nothing to stdout'
    Assert-Match 'no repository' $nr.Err 'row 10: and says why'
    Assert-Equal '' $nr.Calls 'row 10: and calls gh for nothing'
    $u = Invoke-Pwsh @($Script, '-IssuesJson', '{"number":1}') $scratchPath
    Assert-Equal 2 $u.Code 'row 10: -IssuesJson that is not an array is a usage error, exit 2'
    Assert-Equal '' $u.Out 'row 10: with nothing on stdout'
    $u = Invoke-Pwsh @($Script, '-IssuesJson', '[]', '-Bogus', 'x') $scratchPath
    Assert-Equal 2 $u.Code 'row 10: an unknown parameter is a usage error, exit 2'
    Assert-Equal '' $u.Out 'row 10: with nothing on stdout'
    Assert-Match 'unknown parameter' $u.Err 'row 10: and named on stderr'
    $u = Invoke-Pwsh @($Script, '-IssuesJson', '[]') $scratchPath
    Assert-Equal 0 $u.Code 'row 10 control: the same call without the unknown parameter exits 0'
    $u = Invoke-Pwsh @($Script, '-IssuesJson', '[{"number":1,"labels":[{"name":"agent-ready"}]},{"number":1,"labels":[{"name":"agent-ready"}]}]') $scratchPath
    Assert-Equal 2 $u.Code 'row 10: a repeated issue number is a usage error, exit 2'

    # --- row 11: a 500-row read warns on stderr, naming the label ---------------------------------
    $full = Invoke-Stubbed 'full-ready'
    Assert-Equal 0 $full.Code 'row 11: the 500-row read still exits 0'
    Assert-Match 'agent-ready.*500|500.*agent-ready' $full.Err 'row 11: the warning on stderr names agent-ready'
    Assert-NoMatch 'needs-ruling' $full.Err 'row 11: and does not name the label that came back short'
    Assert-Match '"number": 500' $full.Out 'row 11: all 500 rows are in the output'
    $fullR = Invoke-Stubbed 'full-ruling'
    Assert-Match 'needs-ruling.*500|500.*needs-ruling' $fullR.Err 'row 11: a full needs-ruling page names needs-ruling'
    $near = Invoke-Stubbed 'near-full'
    Assert-Equal 0 $near.Code 'row 11 control: a 499-row read exits 0'
    Assert-NoMatch '500|limit' $near.Err 'row 11 control: a 499-row read warns nothing'
    Assert-Match '"number": 499' $near.Out 'row 11 control: its 499 rows are in the output'
}
finally {
    Remove-Item -LiteralPath $scratch.FullName -Recurse -Force
    Remove-Item -LiteralPath $bareDir.FullName -Recurse -Force
}

if ($failures -gt 0) { Write-Host "$failures failure(s)" -ForegroundColor Red; exit 1 }
Write-Host 'all footprint-graph cases pass' -ForegroundColor Green
exit 0
