<#
.SYNOPSIS
    Unit test for Get-DriftAuditTargets.ps1's selection logic.
.DESCRIPTION
    Drives the pure functions with inline data -- no git, no fixture repo. That is deliberate:
    the selector's whole history of defects (a cursor advancing past docs it never covered, a
    bucket swallowing the cap, then a second bucket swallowing it again) lived in ranking and
    allocation arithmetic, and every one would have failed a test like these on the first run.
    Anything that needs real history would make the suite depend on the repo's daily churn,
    which is how a test earns deletion.
#>
$ErrorActionPreference = 'Stop'
# Two levels up is the plugin root (script under bin/) or, once vendored, the scripts dir itself.
$Base     = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Selector = @((Join-Path $Base 'Get-DriftAuditTargets.ps1'), (Join-Path $Base 'bin/Get-DriftAuditTargets.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Selector) { throw "Get-DriftAuditTargets.ps1 not found under $Base" }

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -ne $Actual) {
        Write-Host "FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red
        $script:failures++
    } else {
        Write-Host "  ok: $What" -ForegroundColor DarkGray
    }
}

# The script resolves the repo root from the working directory's git root even with -AsModule.
Push-Location -LiteralPath $Base
try { . $Selector -AsModule } finally { Pop-Location }

# --- ledger fold -------------------------------------------------------------------------
$text = @'
some prose
<!-- audit-run: sha=aaaa111 docs=a.md,b.md -->
more prose
<!-- audit-run: sha=bbbb222 docs=b.md,c.md -->
'@
$led = Get-AuditLedger -Text $text
Assert-Equal 3       $led.Count      'ledger folds every appended run'
Assert-Equal 'aaaa111' $led['a.md']  'a doc audited once keeps that sha'
Assert-Equal 'bbbb222' $led['b.md']  'a re-audited doc takes the NEWER sha'
Assert-Equal 'bbbb222' $led['c.md']  'the newest run is folded in'

Assert-Equal 0 (Get-AuditLedger -Text '').Count           'empty ledger text yields an empty ledger'
Assert-Equal 0 (Get-AuditLedger -Text 'no markers').Count 'prose without markers yields an empty ledger'

# A renamed or deleted doc must not keep an audit record -- otherwise the ledger asserts a
# verification that can never be checked against anything.
$pruned = Get-AuditLedger -Text $text -ValidPaths @('a.md', 'c.md')
Assert-Equal 2     $pruned.Count            'entries for paths that no longer exist are pruned'
Assert-Equal $false $pruned.ContainsKey('b.md') 'the pruned path is the missing one'

# --- citations ---------------------------------------------------------------------------
$cites = Get-DocCitations 'see `App/ZorbLib/ZorbLine.cs` and [x](../Utilities/scripts/Build.ps1)'
Assert-Equal $true  $cites.ContainsKey('ZorbLine.cs') 'a backticked path is a citation'
Assert-Equal $true  $cites.ContainsKey('Build.ps1')    'a markdown link target is a citation'
$prose = Get-DocCitations 'the App.sln solution and ZorbLine.cs in passing'
Assert-Equal 0 $prose.Count 'a bare prose mention is NOT a citation'
Assert-Equal 0 (Get-DocCitations 'nothing here').Count 'plain prose yields no citations'
Assert-Equal 0 (Get-DocCitations '`some prose with spaces.cs`').Count 'a backticked phrase is not a path'

# --- citations resolve to paths -----------------------------------------------------------
# What a changed-file list yields for the doc at $DocPath, through the same functions the selector
# chains: the citation keys, the changed keys, the fan-out cap, the citing doc's hits.
$tr = @('docs/guide.md', 'docs/other.md', 'docs/sub/n.ps1', 'docs/p.html', 'docs/x.sh', 'conf/a.toml',
        'bin/run.ps1', 'tests/fix/bin/run.ps1', 'skills/a/run.ps1', 'skills/b/run.ps1',
        'tools/t.py', 'docs/ext.md', 'README.md', 'docs/README.md', 'CHANGELOG.md',
        'docs/bin/run.ps1', 'sub/n.ps1', 'docs/x/README.md')
function Get-Hits($Text, $DocPath, $Changed) {
    $c = Get-DocCitations $Text -DocPath $DocPath -Tracked $tr
    $changedKeys = @(Get-ChangedKeys $Changed)
    $keys = Get-DistinctiveNames -ChangedLeaves $changedKeys -CitationsByDoc @{ $DocPath = $c }
    return (Get-DocHits -Citations $c -Distinctive $keys -KeyCounts (Get-ChangedKeyCounts $changedKeys) `
            -DocChanged ($Changed -ccontains $DocPath) -DocPath $DocPath) -join ','
}
Assert-Equal 'bin/run.ps1' (Get-Hits '`bin/run.ps1`' 'docs/guide.md' @('bin/run.ps1')) 'a backticked path is read from the repo root'
Assert-Equal '' (Get-Hits '`bin/run.ps1`' 'docs/guide.md' @('tests/fix/bin/run.ps1')) 'a backticked path does not match a same-named file elsewhere'
Assert-Equal 'docs/sub/n.ps1' (Get-Hits '[n](sub/n.ps1)' 'docs/guide.md' @('docs/sub/n.ps1')) 'a link target is read from the doc''s directory'
Assert-Equal 'bin/run.ps1' (Get-Hits '[b](../bin/run.ps1)' 'docs/guide.md' @('bin/run.ps1')) 'a link target with .. resolves against the doc''s directory'
Assert-Equal '' (Get-Hits '[b](../bin/run.ps1)' 'docs/guide.md' @('tests/fix/bin/run.ps1')) 'a resolved link target does not match a same-named file elsewhere'
Assert-Equal 'run.ps1' (Get-Hits '`run.ps1`' 'docs/guide.md' @('tests/fix/bin/run.ps1')) 'a name with no directory matches by file name'
Assert-Equal 'run.ps1' (Get-Hits '`gone/run.ps1`' 'docs/guide.md' @('bin/run.ps1')) 'a path that resolves to no tracked file matches by file name'
Assert-Equal 'skills/a/run.ps1' (Get-Hits '`skills/a/run.ps1`' 'docs/guide.md' @('skills/a/run.ps1')) 'of two same-named files, the cited one changing is evidence'
Assert-Equal '' (Get-Hits '`skills/a/run.ps1`' 'docs/guide.md' @('skills/b/run.ps1')) 'of two same-named files, the other one changing is not evidence'
Assert-Equal 'run.ps1' (Get-Hits '`run.ps1`' 'docs/guide.md' @('skills/b/run.ps1')) 'the bare name does match the other same-named file'
Assert-Equal 'README.md' (Get-Hits '`README.md`' 'docs/guide.md' @('docs/README.md')) 'a bare name that is also a tracked root file still matches by file name'
Assert-Equal 'tools/t.py'   (Get-Hits '`tools/t.py`'   'docs/guide.md' @('tools/t.py'))   'a .py citation is evidence'
Assert-Equal 'conf/a.toml'  (Get-Hits '`conf/a.toml`'  'docs/guide.md' @('conf/a.toml'))  'a .toml citation is evidence'
Assert-Equal 'docs/x.sh'    (Get-Hits '`docs/x.sh`'    'docs/guide.md' @('docs/x.sh'))    'a .sh citation is evidence'
Assert-Equal 'docs/ext.md'  (Get-Hits '`docs/ext.md`'  'docs/guide.md' @('docs/ext.md'))  'a .md citation is evidence'
Assert-Equal 'docs/p.html'  (Get-Hits '`docs/p.html`'  'docs/guide.md' @('docs/p.html'))  'a .html citation is evidence'
Assert-Equal 0 (Get-DocCitations '`docs/guide.md`' -DocPath 'docs/guide.md' -Tracked $tr).Count 'a doc''s citation of its own path is dropped'
Assert-Equal 0 (Get-DocCitations '[g](guide.md)' -DocPath 'docs/guide.md' -Tracked $tr).Count 'a link to the doc itself is dropped'
Assert-Equal 'docs/guide.md' (Get-Hits '`docs/guide.md`' 'docs/other.md' @('docs/guide.md')) 'another doc''s citation of that path is evidence'
Assert-Equal 0 (Get-DocCitations '`CHANGELOG.md`' -DocPath 'docs/guide.md' -Tracked $tr).Count 'a citation of CHANGELOG.md is dropped'
Assert-Equal 'docs/README.md' (Get-Hits '`docs/README.md`' 'docs/guide.md' @('docs/README.md')) 'a citation of another .md is evidence where CHANGELOG.md is not'

$bk = Get-DocCitations '`bin/run.ps1`' -DocPath 'docs/guide.md' -Tracked $tr
Assert-Equal $true  ($bk.ContainsKey('/bin/run.ps1') -and -not $bk.ContainsKey('/docs/bin/run.ps1')) 'a backticked path that exists under both bases is keyed by its root path'
$lk = Get-DocCitations '[n](sub/n.ps1)' -DocPath 'docs/guide.md' -Tracked $tr
Assert-Equal $true  ($lk.ContainsKey('/docs/sub/n.ps1') -and -not $lk.ContainsKey('/sub/n.ps1')) 'a link target that exists under both bases is keyed by its doc-relative path'
Assert-Equal '' (Get-Hits '`README.md`' 'docs/x/README.md' @('docs/x/README.md')) 'a doc''s own edit is not evidence for it'
Assert-Equal 'README.md' (Get-Hits '`README.md`' 'docs/x/README.md' @('docs/x/README.md', 'README.md')) 'another file of the same name changing is evidence beside the doc''s own edit'
Assert-Equal $true ((Get-Hits '`README.md`' 'docs/x/README.md' @('docs/x/README.md', 'Readme.md')) -ne '') 'a changed file whose name differs from the cited name only in case is evidence beside the doc''s own edit'
Assert-Equal 'README.md' (Get-Hits '`README.md`' 'docs/x/README.md' @('README.md')) 'the root README.md changing is evidence for the nested doc'
# The changed keys are built once per sha group, never per doc: Get-ChangedKeys throws while one
# doc's hits are read.
$groupCounts = Get-ChangedKeyCounts @(Get-ChangedKeys @('docs/x/README.md', 'README.md'))
$savedKeys = ${function:Get-ChangedKeys}
try {
    ${function:Get-ChangedKeys} = { throw 'Get-ChangedKeys called while reading one doc''s hits' }
    $perDoc = try {
        (Get-DocHits -Citations @{ 'README.md' = $true } -Distinctive @('README.md') -KeyCounts $groupCounts `
            -DocChanged $true -DocPath 'docs/x/README.md') -join ','
    } catch { "threw: $_" }
}
finally { ${function:Get-ChangedKeys} = $savedKeys }
Assert-Equal 'README.md' $perDoc 'a doc''s hits read the sha group''s changed keys without rebuilding them'
Assert-Equal 0 (Get-DocCitations '[u](https://github.com/o/r/blob/main/README.md)' -DocPath 'docs/guide.md' -Tracked $tr).Count 'a link to an http URL is not a citation'
Assert-Equal 0 (Get-DocCitations '`https://example.com/a/run.ps1`' -DocPath 'docs/guide.md' -Tracked $tr).Count 'a backticked URL is not a citation'
Assert-Equal 0 (Get-DocCitations '`C:\x\run.ps1`' -DocPath 'docs/guide.md' -Tracked $tr).Count 'a drive path is not a citation'
Assert-Equal 0 (Get-DocCitations '`my_repo:bin/run.ps1`' -DocPath 'docs/guide.md' -Tracked $tr).Count 'a path prefixed by a repository name holding _ is not a citation'
Assert-Equal 1 (Get-DocCitations '`bin/run.ps1`' -DocPath 'docs/guide.md' -Tracked $tr).Count 'the same path without the repository prefix is a citation'
Assert-Equal 1 (Get-DocCitations '[u](x/README.md)' -DocPath 'docs/guide.md' -Tracked $tr).Count 'a relative link is a citation where a URL is not'

# --- fan-out cap -------------------------------------------------------------------------
$byDoc = @{
    'd1.md' = @{ 'Local.resx' = $true; 'Rare.cs' = $true }
    'd2.md' = @{ 'Local.resx' = $true }
    'd3.md' = @{ 'Local.resx' = $true }
}
$keep = Get-DistinctiveNames -ChangedLeaves @('Local.resx', 'Rare.cs') -CitationsByDoc $byDoc -FanOutCap 2
Assert-Equal $true  ($keep -contains 'Rare.cs')    'a narrowly-cited file identifies a subject'
Assert-Equal $false ($keep -contains 'Local.resx') 'a file cited by everything is not distinctive'
$none = Get-DistinctiveNames -ChangedLeaves @('Unreferenced.cs') -CitationsByDoc $byDoc -FanOutCap 8
Assert-Equal 0 $none.Count 'a changed file no doc cites selects nothing'
$byPath = @{
    'd1.md' = @{ '/skills/a/SKILL.md' = $true; 'SKILL.md' = $true }
    'd2.md' = @{ '/skills/b/SKILL.md' = $true; 'SKILL.md' = $true }
    'd3.md' = @{ '/skills/b/SKILL.md' = $true; 'SKILL.md' = $true }
    'd4.md' = @{ '/skills/b/SKILL.md' = $true }
}
$keepP = Get-DistinctiveNames -ChangedLeaves @('/skills/a/SKILL.md', '/skills/b/SKILL.md', 'SKILL.md') -CitationsByDoc $byPath -FanOutCap 2
Assert-Equal $true  ($keepP -contains '/skills/a/SKILL.md') 'a path cited by few docs identifies a subject although its name is cited by many'
Assert-Equal $false ($keepP -contains '/skills/b/SKILL.md') 'a path cited by more docs than the cap identifies none of them'
Assert-Equal $false ($keepP -contains 'SKILL.md')           'a name cited by more docs than the cap identifies none of them'
Assert-Equal $true  ((Get-DistinctiveNames -ChangedLeaves @('/skills/b/SKILL.md') -CitationsByDoc $byPath -FanOutCap 3) -contains '/skills/b/SKILL.md') 'the same path is kept once the cap admits its citing docs'

# --- ranking and the rotation floor ------------------------------------------------------
$docs = 1..30 | ForEach-Object { "d$_.md" }
# Every doc carries evidence, so under a naive rank the oldest quiet docs never get a slot.
$ev = @{}; foreach ($d in $docs) { $ev[$d] = 'x' }
$wk = @{}; for ($i = 1; $i -le 30; $i++) { $wk["d$i.md"] = [double]$i }

$sel = Select-AuditTargets -Docs $docs -Evidence $ev -WeeksSince $wk -MaxTargets 10 -RotationFloor 3
Assert-Equal 10 $sel.Count 'selection honours MaxTargets'
Assert-Equal $true ($sel.path -contains 'd30.md') 'the stalest doc is always reached (never-audited is a separate case below)'

# The floor is the real regression guard: no amount of evidence may push rotation to zero.
$evAll = @{}; foreach ($d in $docs) { $evAll[$d] = 'x' }
$wkFlat = @{}; foreach ($d in $docs) { $wkFlat[$d] = 1.0 }
$wkFlat['d29.md'] = 99.0; $wkFlat['d30.md'] = 98.0
$sel2 = Select-AuditTargets -Docs $docs -Evidence $evAll -WeeksSince $wkFlat -MaxTargets 5 -RotationFloor 2
Assert-Equal $true ($sel2.path -contains 'd29.md') 'the floor reserves the oldest doc even when every doc has evidence'
Assert-Equal $true ($sel2.path -contains 'd30.md') 'the floor reserves the second-oldest too'
Assert-Equal 5     $sel2.Count                     'the floor does not inflate the cap'

# Never-audited beats everything: it is the only state we know nothing about.
$wkNever = @{}; foreach ($d in $docs) { $wkNever[$d] = 1.0 }
$wkNever.Remove('d7.md')
$sel3 = Select-AuditTargets -Docs $docs -Evidence @{} -WeeksSince $wkNever -MaxTargets 3 -RotationFloor 1
Assert-Equal $true ($sel3.path -contains 'd7.md') 'a never-audited doc outranks every audited one'

# Evidence outranks staleness among docs the floor did not already reserve.
$ev4 = @{ 'd2.md' = 'cites changed: X.cs' }
$wk4 = @{ 'd1.md' = 5.0; 'd2.md' = 1.0; 'd3.md' = 4.0 }
$sel4 = Select-AuditTargets -Docs @('d1.md','d2.md','d3.md') -Evidence $ev4 -WeeksSince $wk4 -MaxTargets 2 -RotationFloor 1
Assert-Equal $true ($sel4.path -contains 'd1.md') 'the floor takes the stalest doc'
Assert-Equal $true ($sel4.path -contains 'd2.md') 'evidence wins the remaining slot over a merely older doc'

# Degenerate inputs must not throw or silently return everything.
Assert-Equal 0 (Select-AuditTargets -Docs @() -Evidence @{} -WeeksSince @{} -MaxTargets 5 -RotationFloor 2).Count 'no docs yields no targets'
$selSmall = Select-AuditTargets -Docs @('a.md') -Evidence @{} -WeeksSince @{} -MaxTargets 10 -RotationFloor 10
Assert-Equal 1 $selSmall.Count 'a floor larger than the corpus does not duplicate docs'

# --- a work tree at a non-ASCII path, under an OEM console code page ---------------------
# git prints the root as UTF-8 and PowerShell decodes a native command's output with
# [Console]::OutputEncoding: under code page 437 the root names no directory, and the selector
# throws at its Push-Location. The caller's encoding must come back unchanged.
$uniRepo = Join-Path ([IO.Path]::GetTempPath()) ("drift-r$([char]0xE9)po-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$made = try { New-Item -ItemType Directory -Force -Path $uniRepo | Out-Null; $true } catch { $false }
if (-not $made) {
    Write-Host "  skip: cannot create a directory named with U+00E9 under $([IO.Path]::GetTempPath())" -ForegroundColor DarkGray
} else {
    $callerEncoding = [Console]::OutputEncoding
    Push-Location -LiteralPath $uniRepo
    try {
        git init -q . 2>$null
        Set-Content -LiteralPath (Join-Path $uniRepo 'doc.md') -Value '# Doc' -Encoding utf8
        git add doc.md 2>$null; git -c user.email=t@t -c user.name=t commit -q -m fixture 2>$null
        [Console]::OutputEncoding = [Text.Encoding]::GetEncoding(437)
        $got = try { (& $Selector | Out-String | ConvertFrom-Json).targets.path -join ',' } catch { "threw: $_" }
        $after = [Console]::OutputEncoding.CodePage
    }
    finally {
        [Console]::OutputEncoding = $callerEncoding
        Pop-Location
        Remove-Item -LiteralPath $uniRepo -Recurse -Force
    }
    Assert-Equal 'doc.md' $got 'the selector gets past its Push-Location in a repo at a non-ASCII path under code page 437'
    Assert-Equal 437 $after 'the caller''s console output encoding is unchanged by the selector'
}

# --- a doc and a code file named with non-ASCII characters, under an OEM console code page -
# Plain git output C-quotes such a path: the quoted doc name names no file and prunes its ledger
# entry, and a quoted changed-file leaf matches no citation. The -z records carry UTF-8, which
# code page 437 would decode to other characters.
$docName  = "caf$([char]0xE9).md"
$codeLeaf = "na$([char]0xEF)ve.cs"
$nameRepo = Join-Path ([IO.Path]::GetTempPath()) ('drift-names-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$made = try {
    New-Item -ItemType Directory -Force -Path (Join-Path $nameRepo 'src') | Out-Null
    Set-Content -LiteralPath (Join-Path $nameRepo $docName) -Value @('# Doc', ('see `src/{0}` and `{1}`' -f $codeLeaf, $docName)) -Encoding utf8
    Set-Content -LiteralPath (Join-Path $nameRepo "src/$codeLeaf") -Value 'class A {}' -Encoding utf8
    $true
} catch { $false }
if (-not $made) {
    Write-Host "  skip: cannot create files named with U+00E9 and U+00EF under $([IO.Path]::GetTempPath())" -ForegroundColor DarkGray
    if (Test-Path -LiteralPath $nameRepo) {
        try { Remove-Item -LiteralPath $nameRepo -Recurse -Force -ErrorAction Stop }
        catch { Write-Warning "could not remove ${nameRepo}: $($_.Exception.Message)" }
    }
} else {
    $callerEncoding = [Console]::OutputEncoding
    Push-Location -LiteralPath $nameRepo
    try {
        git init -q . 2>$null
        # Pinned: a machine's core.quotePath=false prints these names unquoted, and a plain read
        # under the UTF-8 switch would then pass.
        git config core.quotePath true
        git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m base 2>$null
        $baseSha = (git rev-parse HEAD).Trim()
        Set-Content -LiteralPath (Join-Path $nameRepo "src/$codeLeaf") -Value 'class B {}' -Encoding utf8
        Add-Content -LiteralPath (Join-Path $nameRepo $docName) -Value 'edited' -Encoding utf8
        git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m change 2>$null
        $nameLedger = Join-Path $nameRepo 'ledger.txt'
        Set-Content -LiteralPath $nameLedger -Value "<!-- audit-run: sha=$baseSha docs=$docName -->" -Encoding utf8
        [Console]::OutputEncoding = [Text.Encoding]::GetEncoding(437)
        $nameTargets = try { @((& $Selector -LedgerFile $nameLedger | Out-String | ConvertFrom-Json).targets) } catch { Write-Host "threw: $_"; @() }
        $after = [Console]::OutputEncoding.CodePage
        $got = @($nameTargets.path) -join ','
        $exists = [bool]$got -and (Test-Path -LiteralPath (Join-Path $nameRepo $got))
        $evid = @($nameTargets | ForEach-Object { "$($_.reason): $($_.via)" }) -join ','
    }
    finally {
        [Console]::OutputEncoding = $callerEncoding
        Pop-Location
        Remove-Item -LiteralPath $nameRepo -Recurse -Force
    }
    Assert-Equal $docName $got 'a doc named with U+00E9 is listed under its real name under code page 437'
    Assert-Equal $true $exists 'the listed doc path names the tracked file'
    Assert-Equal "evidence: cites changed: src/$codeLeaf" $evid 'a changed code file whose path carries U+00EF is evidence for the doc citing it'
    Assert-Equal 437 $after 'the caller''s console output encoding is unchanged after the selector reads the path lists'
}

# --- the claim key -----------------------------------------------------------------------
# The evidence decision is Get-ClaimKey, pure on inline data; `check`'s classification is the
# script's and is tested in tests/drift-claims. Ranges are what `check` returns for a doc.
$S1 = 'a' * 40
$mk = { param($sha = $S1) [pscustomobject]@{ sha = $sha; doc = 'd.md'; claims = @(
    [pscustomobject]@{ statement = 'one'; path = 'x.py'; start = 1; end = 2; sha256 = ('1' * 64) },
    [pscustomobject]@{ statement = 'two'; path = 'y.py'; start = 5; end = 6; sha256 = ('2' * 64) }) } }
$rg = { param($c1 = 'same', $c2 = 'same') @(
    [pscustomobject]@{ doc = 'd.md'; statement = 'one'; path = 'x.py'; start = 1; end = 2; class = $c1 },
    [pscustomobject]@{ doc = 'd.md'; statement = 'two'; path = 'y.py'; start = 5; end = 6; class = $c2 }) }

$k = Get-ClaimKey -Marker (& $mk) -LedgerSha $S1 -BaseRanges (& $rg) -HeadRanges (& $rg) -DocChanged $false
Assert-Equal $true  ($null -ne $k)                'a usable marker makes a claim key'
Assert-Equal ''     "$($k.Via)"                   'claims that all survive and an unchanged doc give no evidence'
Assert-Equal 0      $k.StaleClaims                'no stale claim counts 0'
Assert-Equal 0      @($k.Claims).Count            'no stale claim lists none'

$k = Get-ClaimKey -Marker (& $mk) -LedgerSha $S1 -BaseRanges (& $rg) -HeadRanges (& $rg 'changed') -DocChanged $false
Assert-Equal 'stale claims: 1' $k.Via             'one changed claim is evidence, naming the count'
Assert-Equal 1      $k.StaleClaims                'one changed claim counts 1'
Assert-Equal 'one|x.py|1,2|changed' ((@($k.Claims) | ForEach-Object { "$($_.statement)|$($_.path)|$($_.range -join ',')|$($_.class)" }) -join ';') 'the stale claim carries its statement, path, old range and class'

$k = Get-ClaimKey -Marker (& $mk) -LedgerSha $S1 -BaseRanges (& $rg) -HeadRanges (& $rg 'same' 'gone') -DocChanged $false
Assert-Equal 'stale claims: 1' $k.Via             'a gone claim is evidence'
Assert-Equal 'gone' @($k.Claims)[0].class         'the gone claim is listed as gone'

$k = Get-ClaimKey -Marker (& $mk) -LedgerSha $S1 -BaseRanges (& $rg) -HeadRanges (& $rg 'changed' 'changed') -DocChanged $false
Assert-Equal 2      $k.StaleClaims                'two stale claims count 2'

$mkCase = [pscustomobject]@{ sha = $S1; doc = 'd.md'; claims = @(
    [pscustomobject]@{ statement = 'Foo'; path = 'x.py'; start = 1; end = 2; sha256 = ('1' * 64) },
    [pscustomobject]@{ statement = 'foo'; path = 'y.py'; start = 5; end = 6; sha256 = ('2' * 64) }) }
$rgCase = { param($c1 = 'same', $c2 = 'same') @(
    [pscustomobject]@{ doc = 'd.md'; statement = 'Foo'; path = 'x.py'; start = 1; end = 2; class = $c1 },
    [pscustomobject]@{ doc = 'd.md'; statement = 'foo'; path = 'y.py'; start = 5; end = 6; class = $c2 }) }
$k = Get-ClaimKey -Marker $mkCase -LedgerSha $S1 -BaseRanges (& $rgCase) -HeadRanges (& $rgCase 'changed' 'changed') -DocChanged $false
Assert-Equal 2      $k.StaleClaims                'two stale claims whose statements differ only in case count 2'
Assert-Equal 'stale claims: 2' $k.Via             'and the evidence names both'

$mkOne = [pscustomobject]@{ sha = $S1; doc = 'd.md'; claims = @(
    [pscustomobject]@{ statement = 'one'; path = 'x.py'; start = 1; end = 2; sha256 = ('1' * 64) },
    [pscustomobject]@{ statement = 'one'; path = 'z.py'; start = 3; end = 4; sha256 = ('2' * 64) }) }
$oneBase = @(
    [pscustomobject]@{ doc = 'd.md'; statement = 'one'; path = 'x.py'; start = 1; end = 2; class = 'same' },
    [pscustomobject]@{ doc = 'd.md'; statement = 'one'; path = 'z.py'; start = 3; end = 4; class = 'same' })
$oneHead = @(
    [pscustomobject]@{ doc = 'd.md'; statement = 'one'; path = 'x.py'; start = 1; end = 2; class = 'changed' },
    [pscustomobject]@{ doc = 'd.md'; statement = 'one'; path = 'z.py'; start = 3; end = 4; class = 'gone' })
$k = Get-ClaimKey -Marker $mkOne -LedgerSha $S1 -BaseRanges $oneBase -HeadRanges $oneHead -DocChanged $false
Assert-Equal 1      $k.StaleClaims                'a claim with two stale ranges is one stale claim'
Assert-Equal 2      @($k.Claims).Count            'and lists both of its ranges'

$k = Get-ClaimKey -Marker (& $mk) -LedgerSha $S1 -BaseRanges (& $rg) -HeadRanges (& $rg 'moved') -DocChanged $false
Assert-Equal ''     "$($k.Via)"                   'a moved range is not stale'
Assert-Equal 0      $k.StaleClaims                'a moved range counts 0'

$k = Get-ClaimKey -Marker (& $mk) -LedgerSha $S1 -BaseRanges (& $rg) -HeadRanges (& $rg) -DocChanged $true
Assert-Equal 'doc text changed since its claims were recorded' $k.Via 'the doc''s own blob changing is evidence'
Assert-Equal 0      $k.StaleClaims                'a changed doc with no stale claim counts 0'
$k = Get-ClaimKey -Marker (& $mk) -LedgerSha $S1 -BaseRanges (& $rg) -HeadRanges (& $rg 'changed') -DocChanged $true
Assert-Equal 'stale claims: 1' $k.Via             'a stale claim is named before the doc''s own change'

Assert-Equal $true ($null -eq (Get-ClaimKey -Marker (& $mk ('b' * 40)) -LedgerSha $S1 -BaseRanges (& $rg) -HeadRanges (& $rg 'changed') -DocChanged $true)) 'a marker at a sha other than the doc''s audit-run sha keeps the file-level key'
Assert-Equal $true ($null -eq (Get-ClaimKey -Marker (& $mk) -LedgerSha $S1 -BaseRanges (& $rg 'same' 'changed') -HeadRanges (& $rg) -DocChanged $false)) 'a stored hash that does not recompute keeps the file-level key'
Assert-Equal $true ($null -eq (Get-ClaimKey -Marker (& $mk) -LedgerSha $S1 -BaseRanges (& $rg 'same' 'moved') -HeadRanges (& $rg) -DocChanged $false)) 'a range that only moved at the marker''s sha does not recompute either'
Assert-Equal $true ($null -eq (Get-ClaimKey -Marker $null -LedgerSha $S1 -BaseRanges @() -HeadRanges @() -DocChanged $true)) 'no marker, which an unparseable one comes to, keeps the file-level key'
Assert-Equal $true ($null -eq (Get-ClaimKey -Marker (& $mk) -LedgerSha $S1 -BaseRanges @((& $rg)[0]) -HeadRanges (& $rg) -DocChanged $false)) 'a marker with a record that has no checked range keeps the file-level key'
Assert-Equal $true ($null -eq (Get-ClaimKey -Marker ([pscustomobject]@{ sha = $S1; doc = 'd.md'; claims = @() }) -LedgerSha $S1 -BaseRanges @() -HeadRanges @() -DocChanged $true)) 'a marker with no claims keeps the file-level key'
Assert-Equal $true ($null -ne (Get-ClaimKey -Marker (& $mk) -LedgerSha $S1.ToUpperInvariant() -BaseRanges (& $rg) -HeadRanges (& $rg) -DocChanged $false)) 'the ledger sha is compared in lower case'

# --- the script call is a seam -----------------------------------------------------------
$realSeam = ${function:Invoke-DriftClaims}
$script:calls = [System.Collections.Generic.List[string]]::new()
function Invoke-DriftClaims {
    param([string[]]$Arguments, [string]$InputText = '')
    $script:calls.Add(($Arguments -join ' '))
    if ($script:stubThrows) { throw $script:stubThrows }
    if ($script:stubCheckThrows -and $Arguments[0] -eq 'check') { throw $script:stubCheckThrows }
    if ($script:stubCheckThrowsBase -and $Arguments[0] -eq 'check' -and $Arguments[$Arguments.IndexOf('--base') + 1] -eq $script:stubCheckThrowsBase) { throw "git failed at $script:stubCheckThrowsBase" }
    if ($Arguments[0] -eq 'parse') { return $script:stubMarkers }
    $classAt = if ($Arguments[$Arguments.IndexOf('--rev') + 1] -eq $S1) { $script:stubBaseClass } else { $script:stubHeadClass }
    $recs = $InputText | ConvertFrom-Json
    return (@{ ranges = @($recs | ForEach-Object { @{ doc = $_.doc; statement = $_.statement; path = $_.path; start = $_.start; end = $_.end; class = $classAt } }); claims = @() } | ConvertTo-Json -Depth 4)
}
$markerJson = '[{"sha":"' + $S1 + '","doc":"d.md","claims":[{"statement":"one","path":"x.py","start":1,"end":2,"sha256":"' + ('1' * 64) + '"}]}]'
$groups = @{ $S1 = @('d.md', 'e.md') }
$script:stubThrows = $null; $script:stubMarkers = $markerJson; $script:stubBaseClass = 'same'; $script:stubHeadClass = 'changed'

$r = Get-ClaimRanges -LedgerText 'x <!-- audit-claims: sha=... -->' -ShaGroups $groups -Head ('c' * 40)
Assert-Equal 'd.md' (@($r.Keys) -join ',') 'ranges come back for the doc the marker names, not for one it does not'
Assert-Equal 'changed' @($r['d.md'].Head)[0].class 'the ranges at HEAD are the second check'
Assert-Equal 'same' @($r['d.md'].Base)[0].class 'the ranges at the marker''s sha are the first check'
Assert-Equal 3 $script:calls.Count 'one parse and one check at each revision, however many docs share the sha'
Assert-Equal 1 @($script:calls | Where-Object { $_ -ceq "check --base $S1 --rev $S1 --cwd $RepoRoot" }).Count 'the check of the marker''s sha is run as check --base <marker sha> --rev <marker sha>'
Assert-Equal 1 @($script:calls | Where-Object { $_ -ceq "check --base $S1 --rev $('c' * 40) --cwd $RepoRoot" }).Count 'the check at HEAD is run as check --base <marker sha> --rev <head>'

$script:calls.Clear()
$r = Get-ClaimRanges -LedgerText 'only <!-- audit-run: sha=aaaa docs=d.md --> text' -ShaGroups $groups -Head ('c' * 40)
Assert-Equal 0 $script:calls.Count 'a ledger with no audit-claims marker never calls the script'
Assert-Equal 0 $r.Count 'and yields no ranges'

$script:stubMarkers = $markerJson -replace [regex]::Escape($S1), ('b' * 40)
$script:calls.Clear()
$r = Get-ClaimRanges -LedgerText '<!-- audit-claims: -->' -ShaGroups $groups -Head ('c' * 40)
Assert-Equal 0 $r.Count 'a marker at another sha than the group''s yields no ranges'
Assert-Equal 1 $script:calls.Count 'and costs no check'

$script:stubMarkers = '[]'
Assert-Equal 0 (Get-ClaimRanges -LedgerText '<!-- audit-claims: -->' -ShaGroups $groups -Head ('c' * 40)).Count 'a marker that does not parse (the script drops it) yields no ranges'

$script:stubMarkers = $markerJson; $script:stubThrows = 'python3 is not on PATH'
$warned = @(& { $script:r = Get-ClaimRanges -LedgerText '<!-- audit-claims: -->' -ShaGroups $groups -Head ('c' * 40) } 3>&1)
$r = $script:r
Assert-Equal 0 $r.Count 'a script that cannot run yields no ranges and does not throw'
Assert-Equal 1 @($warned).Count 'and says so in one warning line'
Assert-Equal $true ("$warned" -match 'python3 is not on PATH') 'naming the cause'
$script:stubThrows = $null; $script:stubCheckThrows = 'git failed'
$warned = @(& { $script:r = Get-ClaimRanges -LedgerText '<!-- audit-claims: -->' -ShaGroups $groups -Head ('c' * 40) } 3>&1)
Assert-Equal 0 $script:r.Count 'a check that fails yields no ranges for its sha and does not throw'
Assert-Equal 1 @($warned).Count 'and says so in one warning line'
Assert-Equal $true ("$warned" -match $S1 -and "$warned" -match 'git failed') 'naming the sha and the cause'
$script:stubCheckThrows = $null

$S2 = 'b' * 40
$script:stubMarkers = '[{"sha":"' + $S1 + '","doc":"d.md","claims":[{"statement":"old","path":"x.py","start":1,"end":2,"sha256":"' + ('1' * 64) + '"}]},{"sha":"' + $S2 + '","doc":"d.md","claims":[{"statement":"new","path":"x.py","start":1,"end":2,"sha256":"' + ('2' * 64) + '"}]}]'
$r = Get-ClaimRanges -LedgerText '<!-- audit-claims: -->' -ShaGroups @{ $S2 = @('d.md') } -Head ('c' * 40)
Assert-Equal $S2 "$($r['d.md'].Marker.sha)" 'of two markers for one doc at successive audit shas, the newer one is the one that counts'
Assert-Equal 'new' "$(@($r['d.md'].Marker.claims)[0].statement)" 'and it carries its own claims'

$script:stubMarkers = '[{"sha":"' + $S1 + '","doc":"d.md","claims":[{"statement":"one","path":"x.py","start":1,"end":2,"sha256":"' + ('1' * 64) + '"}]},{"sha":"' + $S2 + '","doc":"e.md","claims":[{"statement":"two","path":"x.py","start":1,"end":2,"sha256":"' + ('2' * 64) + '"}]}]'
$script:stubCheckThrowsBase = $S1
$warned = @(& { $script:r = Get-ClaimRanges -LedgerText '<!-- audit-claims: -->' -ShaGroups ([ordered]@{ $S1 = @('d.md'); $S2 = @('e.md') }) -Head ('c' * 40) } 3>&1)
Assert-Equal 'e.md' (@($script:r.Keys) -join ',') 'a sha group whose check fails does not stop the groups after it'
Assert-Equal 1 @($warned).Count 'and the failed group gives one warning line'
$script:stubCheckThrowsBase = $null
Set-Item Function:\Invoke-DriftClaims $realSeam

# --- claim keys through the selector, on the real script ----------------------------------
# One small repo, like the rows above: the markers are written by the script's own record and encode,
# so the selector reads what the weekly step writes. Skipped, visibly, without a python3 that runs the
# script; on Linux, where python3 is always there to run it, a skip is a failure.
$claimPy = Get-Command python3 -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$claimSkip = $null
if (-not $claimPy) { $claimSkip = 'no python3 on PATH' }
elseif (-not (Test-Path -LiteralPath (Join-Path (Split-Path $Selector -Parent) 'drift-claims.py'))) { $claimSkip = 'no drift-claims.py beside the selector' }
else {
    $null = & $claimPy.Source -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>&1
    if ($LASTEXITCODE -ne 0) { $claimSkip = "python3 at $($claimPy.Source) exited $LASTEXITCODE on the 3.11 version check (broken, or older than 3.11)" }
}
if ($IsLinux) { Assert-Equal '' "$claimSkip" 'on Linux the real-script rows run: a python3 3.11+ is always there to run them' }
if ($claimSkip) {
    Write-Host "  skip: $claimSkip" -ForegroundColor DarkGray
} else {
    $claimRepo = Join-Path ([IO.Path]::GetTempPath()) ('drift-claims-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path $claimRepo | Out-Null
    $callerEncoding = [Console]::OutputEncoding
    Push-Location -LiteralPath $claimRepo
    try {
        [Console]::OutputEncoding = [Text.Encoding]::GetEncoding(437)
        git init -q . 2>$null
        $xLines = 1..10 | ForEach-Object { "x line $_" }
        [IO.File]::WriteAllText((Join-Path $claimRepo 'x.py'), (($xLines -join "`n") + "`n"))
        foreach ($n in 1, 3, 4) { [IO.File]::WriteAllText((Join-Path $claimRepo "doc$n.md"), "# doc$n`n") }
        foreach ($n in 2, 5, 6) { [IO.File]::WriteAllText((Join-Path $claimRepo "doc$n.md"), "# doc$n`nsee ``x.py```n") }
        # doc7 holds a claim whose statement and path are non-ASCII: an em dash and U+00EF.
        $dash = [string][char]0x2014; $uml = [string][char]0x00EF
        [IO.File]::WriteAllText((Join-Path $claimRepo "$uml.py"), (($xLines -join "`n") + "`n"))
        [IO.File]::WriteAllText((Join-Path $claimRepo 'doc7.md'), "# doc7`n")
        git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m base 2>$null
        $baseSha = (git rev-parse HEAD).Trim()

        $recs = @(
            [ordered]@{ doc = 'doc1.md'; statement = 'a'; path = 'x.py'; start = 2; end = 4 },
            [ordered]@{ doc = 'doc2.md'; statement = 'b'; path = 'x.py'; start = 8; end = 9 },
            [ordered]@{ doc = 'doc3.md'; statement = 'c'; path = 'x.py'; start = 1; end = 1 },
            [ordered]@{ doc = 'doc5.md'; statement = 'e'; path = 'x.py'; start = 1; end = 1 },
            [ordered]@{ doc = 'doc7.md'; statement = "em $dash dash"; path = "$uml.py"; start = 2; end = 3 })
        $hashed = @((Invoke-DriftClaims -Arguments @('record', '--rev', $baseSha, '--cwd', $claimRepo) -InputText (ConvertTo-Json -InputObject $recs -Compress)) | ConvertFrom-Json)
        foreach ($h in $hashed) { if ($h.doc -eq 'doc5.md') { $h.sha256 = '0' * 64 } }
        $markerText = Invoke-DriftClaims -Arguments @('encode', '--sha', $baseSha) -InputText (ConvertTo-Json -InputObject $hashed -Compress)

        [IO.File]::WriteAllText((Join-Path $claimRepo 'x.py'), ((@('x new top') + @($xLines | ForEach-Object { if ($_ -eq 'x line 3') { 'x line 3 CHANGED' } else { $_ } })) -join "`n") + "`n")
        Add-Content -LiteralPath (Join-Path $claimRepo 'doc3.md') -Value 'new text' -Encoding utf8
        [IO.File]::WriteAllText((Join-Path $claimRepo "$uml.py"), (($xLines | ForEach-Object { if ($_ -eq 'x line 2') { 'x line 2 CHANGED' } else { $_ } }) -join "`n") + "`n")
        git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m change 2>$null

        $claimLedger = Join-Path $claimRepo 'ledger.txt'
        [IO.File]::WriteAllText($claimLedger, "<!-- audit-run: sha=$baseSha docs=doc1.md,doc2.md,doc3.md,doc4.md,doc5.md,doc6.md,doc7.md -->`n$markerText<!-- audit-claims: sha=$baseSha doc=`"doc6.md`"`n{not a claim}`n-->`n")
        $got = & $Selector -LedgerFile $claimLedger | Out-String | ConvertFrom-Json
        $byPath = @{}; foreach ($t in $got.targets) { $byPath[$t.path] = $t }

        # A check that exits nonzero throws through the real seam, and costs its sha's docs one warning line.
        $script:badCheck = ''
        try { Invoke-DriftClaims -Arguments @('check', '--base', 'zz', '--rev', 'zz', '--cwd', $claimRepo) -InputText '[]' | Out-Null }
        catch { $script:badCheck = $_.Exception.Message }
        $badWarned = @(& { $script:badRanges = Get-ClaimRanges -LedgerText $markerText -ShaGroups @{ $baseSha = @('doc1.md') } -Head 'zz' } 3>&1)
    }
    finally {
        [Console]::OutputEncoding = $callerEncoding
        Pop-Location
        Remove-Item -LiteralPath $claimRepo -Recurse -Force
    }
    Assert-Equal 'evidence: stale claims: 1' "$($byPath['doc1.md'].reason): $($byPath['doc1.md'].via)" 'a doc with a changed claim is selected for it'
    Assert-Equal 1 $byPath['doc1.md'].staleClaims 'its staleClaims is 1'
    Assert-Equal 'a|x.py|2,4|changed' (@($byPath['doc1.md'].claims | ForEach-Object { "$($_.statement)|$($_.path)|$($_.range -join ',')|$($_.class)" }) -join ';') 'and its claims name the stale one with its old range'
    Assert-Equal 'stale' $byPath['doc2.md'].reason 'a doc whose claim only moved has no evidence'
    Assert-Equal 0 $byPath['doc2.md'].staleClaims 'its staleClaims is 0'
    Assert-Equal 0 @($byPath['doc2.md'].claims).Count 'its claims is empty'
    Assert-Equal 'evidence: doc text changed since its claims were recorded' "$($byPath['doc3.md'].reason): $($byPath['doc3.md'].via)" 'a doc whose own text changed is evidence, its claim surviving'
    Assert-Equal 'stale' $byPath['doc4.md'].reason 'a doc with no marker and nothing cited has no evidence'
    Assert-Equal 0 $byPath['doc4.md'].staleClaims 'staleClaims is 0 on a file-level doc'
    Assert-Equal $true ($null -ne $byPath['doc4.md'].claims -and @($byPath['doc4.md'].claims).Count -eq 0) 'claims is present and empty on a file-level doc'
    Assert-Equal 'evidence: cites changed: x.py' "$($byPath['doc5.md'].reason): $($byPath['doc5.md'].via)" 'a marker whose hash does not recompute leaves the doc on the file-level key'
    Assert-Equal 'evidence: cites changed: x.py' "$($byPath['doc6.md'].reason): $($byPath['doc6.md'].via)" 'a marker that does not parse leaves the doc on the file-level key'
    Assert-Equal "em $dash dash|$uml.py|2,3|changed" (@($byPath['doc7.md'].claims | ForEach-Object { "$($_.statement)|$($_.path)|$($_.range -join ',')|$($_.class)" }) -join ';') 'a non-ASCII statement and path reach the JSON intact'
    Assert-Equal $true ($script:badCheck -match 'check exited') 'a script call that exits nonzero throws, naming the subcommand'
    Assert-Equal 0 $script:badRanges.Count 'a check at a revision that does not resolve leaves its docs without ranges'
    Assert-Equal 1 @($badWarned).Count 'and says so in one warning line'
}

# --- the history each target carries ------------------------------------------------------
# The drift session holds no git history command, so the selector writes what step 3 of /ouro:drift
# reads: the doc's last commit time and the one-line commits since it under the doc's directory,
# the doc's own last commit left out. Real git, a small repo, so a commit landing on the same second
# as the doc's own is not told apart by its time.
$histRepo = Join-Path ([IO.Path]::GetTempPath()) ('drift-history-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $histRepo | Out-Null
$callerEncoding = [Console]::OutputEncoding
Push-Location -LiteralPath $histRepo
try {
    git init -q . 2>$null
    function Add-HistCommit($Message, [hashtable]$Files) {
        foreach ($k in $Files.Keys) {
            $p = Join-Path $histRepo $k
            New-Item -ItemType Directory -Force -Path (Split-Path $p -Parent) | Out-Null
            [IO.File]::WriteAllText($p, $Files[$k])
        }
        git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m $Message 2>$null
        return (git rev-parse HEAD).Trim()
    }
    $h1 = Add-HistCommit 'base' @{ 'docs/a.md' = "# a`n"; 'docs/b.md' = "# b`n"; 'top.md' = "# top`n"; 'src/x.txt' = "1`n"; 'g[1]/n.md' = "# n`n" }
    $null = Add-HistCommit "code $([char]0xE9) change" @{ 'src/x.txt' = "2`n" }
    $null = Add-HistCommit 'docs sibling' @{ 'docs/c.txt' = "c`n" }
    $h4 = Add-HistCommit 'b edited' @{ 'docs/b.md' = "# b`nedited`n" }
    $null = Add-HistCommit 'in g1' @{ 'g1' = "z`n" }
    $null = Add-HistCommit 'in g[1]' @{ 'g[1]/y.txt' = "y`n" }
    $iso1 = (git log -1 --format=%cI $h1).Trim(); $iso4 = (git log -1 --format=%cI $h4).Trim()
    [Console]::OutputEncoding = [Text.Encoding]::GetEncoding(437)
    # A user's log.decorate = full would put "(HEAD -> main)" into every one-line commit.
    $env:GIT_CONFIG_COUNT = '1'; $env:GIT_CONFIG_KEY_0 = 'log.decorate'; $env:GIT_CONFIG_VALUE_0 = 'full'
    try { $histJson = & $Selector | Out-String }
    finally { Remove-Item Env:GIT_CONFIG_COUNT, Env:GIT_CONFIG_KEY_0, Env:GIT_CONFIG_VALUE_0 }
    [Console]::OutputEncoding = $callerEncoding
    $hist = @{}; foreach ($t in ($histJson | ConvertFrom-Json).targets) { $hist[$t.path] = $t }
    $asDate = { param($iso) ("{`"d`":`"$iso`"}" | ConvertFrom-Json).d }
    $hSubjects = { param($t) (@($t.commits) | ForEach-Object { $_ -replace '^[0-9a-f]+ ', '' }) -join '|' }
}
finally {
    [Console]::OutputEncoding = $callerEncoding
    Pop-Location
    Remove-Item -LiteralPath $histRepo -Recurse -Force
}
Assert-Equal (& $asDate $iso1) (& $asDate $hist['docs/a.md'].lastCommit) 'a target carries its doc''s last commit time'
Assert-Equal $true $histJson.Contains("`"lastCommit`": `"$iso1`"") 'in the ISO 8601 form git prints for %cI, text and all'
Assert-Equal (& $asDate $iso4) (& $asDate $hist['docs/b.md'].lastCommit) 'and it is that doc''s own, not the repository''s newest'
Assert-Equal 'b edited|docs sibling' (& $hSubjects $hist['docs/a.md']) 'its commits are the ones since, under the doc''s directory, newest first'
Assert-Equal $true (@($hist['docs/a.md'].commits)[0] -match '^[0-9a-f]{7,} b edited$') 'each commit is a one-line abbreviated sha and subject'
Assert-Equal "in g[1]|in g1|b edited|docs sibling|code $([char]0xE9) change" (& $hSubjects $hist['top.md']) 'a doc at the root takes every commit since, a non-ASCII subject intact under code page 437'
Assert-Equal 'in g[1]' (& $hSubjects $hist['g[1]/n.md']) 'a directory named with glob characters is matched literally, not as the file it would glob to'
Assert-Equal 0 @($hist['docs/b.md'].commits).Count 'a doc with no commit since its last touch carries an empty list'
Assert-Equal $true ($null -ne $hist['docs/b.md'].commits) 'and the list is present, not absent'
Assert-Equal 0 $hist['docs/a.md'].commitsOmitted 'a list under the cap omits none'

# Over the cap the list is cut and the count of what was left out is said.
$capRepo = Join-Path ([IO.Path]::GetTempPath()) ('drift-histcap-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $capRepo | Out-Null
Push-Location -LiteralPath $capRepo
try {
    git init -q . 2>$null
    [IO.File]::WriteAllText((Join-Path $capRepo 'only.md'), "# only`n")
    git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m base 2>$null
    1..4 | ForEach-Object {
        [IO.File]::WriteAllText((Join-Path $capRepo 'f.txt'), "$_`n")
        git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m "change $_" 2>$null
    }
    $capped = (& $Selector -MaxCommits 3 | Out-String | ConvertFrom-Json).targets | Where-Object { $_.path -eq 'only.md' }
}
finally { Pop-Location; Remove-Item -LiteralPath $capRepo -Recurse -Force }
Assert-Equal 'change 4|change 3|change 2' ((@($capped.commits) | ForEach-Object { $_ -replace '^[0-9a-f]+ ', '' }) -join '|') 'a list over -MaxCommits keeps the newest'
Assert-Equal 1 $capped.commitsOmitted 'and commitsOmitted counts the one left out'

# Without python3 on PATH the real seam throws, and the selector's reading degrades to the file-level key.
$savedPath = $env:PATH
$emptyDir = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('drift-nopy-' + [guid]::NewGuid().ToString('N').Substring(0, 8)))
try {
    $env:PATH = $emptyDir.FullName
    $nopyWarned = @(& { $script:nopy = Get-ClaimRanges -LedgerText '<!-- audit-claims: -->' -ShaGroups @{ ('a' * 40) = @('d.md') } -Head ('c' * 40) } 3>&1)
}
finally {
    $env:PATH = $savedPath
    Remove-Item -LiteralPath $emptyDir.FullName -Recurse -Force
}
Assert-Equal 0 $script:nopy.Count 'with no python3 on PATH no doc gets a claim key'
Assert-Equal 1 $nopyWarned.Count 'with no python3 on PATH the selector prints one warning line'
Assert-Equal $true ("$nopyWarned" -match 'python3 is not on PATH') 'and the warning names python3'

# --- the ledger harvest's author filter, through the selector ------------------------------
# The harvest writes the kept bodies of the ledger's comments to the file the selector reads. A
# comment holding an audit-run marker removes the docs it names from the targets, so who may write
# one is the property: the same comment from a stranger leaves the doc targeted, and from the bot
# or an approver skips it. Driven through the real filter and the real selector, in a repo of two
# untouched docs and a cap of one: the doc a marker names ranks below the one it does not.
$trustLib = @((Join-Path $Base 'Get-RollingIssue.ps1'), (Join-Path $Base 'bin/Get-RollingIssue.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $trustLib) { throw "Get-RollingIssue.ps1 not found under $Base" }
. $trustLib
$trustRepo = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('drift-trust-' + [guid]::NewGuid().ToString('N').Substring(0, 8)))
Push-Location -LiteralPath $trustRepo.FullName
try {
    Set-Content -LiteralPath 'a.md' -Value @('# A', 'plain prose') -Encoding utf8
    Set-Content -LiteralPath 'b.md' -Value @('# B', 'plain prose') -Encoding utf8
    git init -q . 2>$null
    git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m base 2>$null
    $trustHead = (git rev-parse HEAD).Trim()
    $marker = "<!-- audit-run: sha=$trustHead docs=a.md -->"
    $trustComments = {
        param($login)
        (@{ comments = @(@{ author = @{ login = $login }; body = $marker; createdAt = '2026-10-03T11:22:33Z' }) } | ConvertTo-Json -Depth 5 -Compress)
    }
    $targetsFor = {
        param($login)
        $kept = Get-TrustedComments -CommentsJson (& $trustComments $login) -Approvers 'Approver1'
        Set-Content -LiteralPath 'ledger.txt' -Value $(if ($kept.Kept) { $kept.Bodies } else { '' }) -Encoding utf8
        ,@((& $Selector -LedgerFile 'ledger.txt' -RotationFloor 0 -MaxTargets 1 | Out-String | ConvertFrom-Json).targets | ForEach-Object { $_.path })
    }
    Assert-Equal 'a.md' ((& $targetsFor 'stranger') -join ',') 'a stranger''s audit-run marker leaves the doc it names targeted'
    Assert-Equal 'a.md' ((& $targetsFor 'github-actions-evil') -join ',') 'a login that only starts with the bot''s name leaves the doc targeted'
    Assert-Equal 'b.md' ((& $targetsFor 'github-actions') -join ',') 'the same marker from the bot skips the doc it names'
    Assert-Equal 'b.md' ((& $targetsFor 'approver1') -join ',') 'the same marker from an approver, whatever the login''s case, skips the doc it names'
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $trustRepo.FullName -Recurse -Force
}

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nAll assertions passed" -ForegroundColor Green
exit 0
