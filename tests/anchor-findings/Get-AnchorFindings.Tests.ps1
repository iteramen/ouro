<#
.SYNOPSIS
    Unit test for Get-AnchorFindings.ps1's path and fragment checks.
.DESCRIPTION
    Drives the shared anchor parser against a scratch git repo built in TEMP -- no live gh,
    no dependence on the surrounding checkout's layout, so the suite behaves the same
    from the plugin repo, a nested consumer checkout, or a vendored scripts dir (one
    comment-identity case runs only where an ouro plugin.json sits beside the gate). Cases
    are the parser's false-positive classes (a backticked command line, a //-span,
    enclosing backticks and prose-code composites in quoted fragments, the prose
    between two quoted phrases on one line, an unterminated quote, env-var /
    placeholder tokens, and the non-repo-relative path families -- rooted, home,
    leading-parent-escape, scheme-qualified, drive-letter, drive-relative, UNC), the
    cross-repo citation form -- both halves contributing nothing, each pinned by a mirror case
    that DOES count (the same fragment double-quoted, an underscored repo name) so neither half
    can pass on an unrelated filter -- the true positives the
    fixes must not lose (a dead path, a dead fragment, one behind a short quoted
    phrase, mid-token skip chars, a fragment holding an escaped quote), a verbatim
    doc quote whose interior backticks really grep, line-anchored relative
    paths, an empty candidate from the [:#] split, and a clean conventional body.
    Four cases bound the fragment length on either side of the unescape, and one is a
    wall-clock assertion -- a 65536-character body scanned inside five seconds -- so this
    suite can go red on time on a loaded shared runner.
    The same scratch repo runs Test-AgentReadyAnchors.ps1 -Demote behind a gh function shadow
    that answers the issue list with one agent-ready issue holding a dead path: the removal
    names only the labels the issue carries, the add of needs-ruling follows it, and a failed
    call throws saying what it left. Its -Comment names ouro and the version of the plugin.json
    in the parent of the gate's directory, and otherwise the running repo's short HEAD.
#>
$ErrorActionPreference = 'Stop'
# Two levels up is the plugin root (parser under bin/) or, once vendored, the scripts dir itself.
$Base   = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Parser = @((Join-Path $Base 'Get-AnchorFindings.ps1'), (Join-Path $Base 'bin/Get-AnchorFindings.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Parser) { throw "Get-AnchorFindings.ps1 not found under $Base" }
. $Parser
$Gate = Join-Path (Split-Path $Parser -Parent) 'Test-AgentReadyAnchors.ps1'
if (-not (Test-Path -LiteralPath $Gate)) { throw "Test-AgentReadyAnchors.ps1 not found beside $Parser" }

$failures = 0
function Assert-Match($Pattern, $Output, $What) {
    if ($Output -match $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- no match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}
function Assert-NoMatch($Pattern, $Output, $What) {
    if ($Output -notmatch $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- unexpected match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}
function Assert-Case($Body, $Findings, $Paths, $Frags, $What) {
    $r = Get-AnchorFindings -Text $Body
    $got = "findings=$($r.Findings.Count) paths=$($r.PathCount) frags=$($r.FragCount)"
    if ($r.Findings.Count -eq $Findings -and $r.PathCount -eq $Paths -and $r.FragCount -eq $Frags) {
        Write-Host "  ok: $What" -ForegroundColor DarkGray
    } else {
        Write-Host "FAIL: $What -- expected findings=$Findings paths=$Paths frags=$Frags, got $got`n  $($r.Findings -join "`n  ")" -ForegroundColor Red
        $script:failures++
    }
    $r
}

# Scratch repo: the parser greps git's index, so the fixture only needs `git add`.
$scratch = Join-Path ([IO.Path]::GetTempPath()) "anchor-findings-test-$PID"
if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $scratch 'src') -Force | Out-Null
$bt = '`'
@(
    '$fixtureAnchor = 12345',
    "# release rule: bumps go in ${bt}plugin.json${bt} and a tag.",
    '# plain words that live in the fixture'
) | Set-Content -LiteralPath (Join-Path $scratch 'src/fixture.ps1')
# A second file holding a line the fixture lacks, and one reached by a unique suffix match.
'$otherAnchor = 67890' | Set-Content -LiteralPath (Join-Path $scratch 'src/other.ps1')
New-Item -ItemType Directory -Path (Join-Path $scratch 'src/deep') -Force | Out-Null
'leaf words in the deep file' | Set-Content -LiteralPath (Join-Path $scratch 'src/deep/leaf.md')
# A directory whose name carries a suffix, holding one file: a span naming it names no file.
New-Item -ItemType Directory -Path (Join-Path $scratch 'src/pkg.d') -Force | Out-Null
'plain words inside the package dir' | Set-Content -LiteralPath (Join-Path $scratch 'src/pkg.d/inner.md')
# A tracked file with a non-ASCII name, which plain git ls-files output C-quotes.
$umlautName = 'src/uml/' + [char]0xFC + 'mlaut.md'
$umlautMade = try {
    New-Item -ItemType Directory -Path (Join-Path $scratch 'src/uml') -Force | Out-Null
    'plain words in the umlaut file' | Set-Content -LiteralPath (Join-Path $scratch $umlautName); $true
} catch { $false }
# A tracked file under a bracketed directory name -- a route file the way a web framework spells
# an id segment. Read as a wildcard, [id] is a one-character class and matches nothing here.
New-Item -ItemType Directory -Path (Join-Path $scratch 'src/[id]') -Force | Out-Null
'page words that live in the bracket file' | Set-Content -LiteralPath (Join-Path $scratch 'src/[id]/page.ps1')
git -C $scratch init -q
git -C $scratch add .
# On disk and never added: a path the grep, which reads the index, cannot see.
'plain words that live untracked' | Set-Content -LiteralPath (Join-Path $scratch 'src/untracked.md')
# The gate names its repository before its first gh call, and with no binding here that is the
# repository origin names (bin/Get-RepoSlug.ps1).
git -C $scratch remote add origin https://github.com/o/n.git

Push-Location -LiteralPath $scratch
try {
    # --- class 1: a whitespace span is a command line or prose, never a path ----------------
    Assert-Case "Run ${bt}pwsh -NoProfile -File src/fixture.ps1${bt} first." 0 0 0 `
        'command line is not a path candidate' | Out-Null
    Assert-Case "Deliverable: ${bt}create docs/gates.md with the table${bt}." 0 0 0 `
        'to-be-created path inside a prose span is not a finding' | Out-Null

    # --- class 2: enclosing backticks are stripped from quoted fragments --------------------
    Assert-Case ('Anchor: "' + $bt + '$fixtureAnchor = 12345' + $bt + '"') 0 0 1 `
        'enclosing backticks stripped, fragment counted and greps' | Out-Null

    # --- class 3: //-spans and prose-code composites are not anchors ------------------------
    Assert-Case "Loads ${bt}//cdn.example.com/lib.js${bt} at startup." 0 0 0 `
        'protocol-relative //-span is not a path' | Out-Null
    Assert-Case ('See "set ' + $bt + '$fixtureAnchor = 12345' + $bt + ' and ' + $bt + '$other' + $bt + '" above.') 0 0 0 `
        'quoted prose-code composite is skipped, not grepped' | Out-Null

    # --- interior backticks that ARE verbatim source must still verify ----------------------
    Assert-Case ('Rule: "bumps go in ' + $bt + 'plugin.json' + $bt + ' and a tag."') 0 0 1 `
        'fragment whose interior backticks grep verbatim stays an anchor' | Out-Null

    # --- true positives the fixes must not lose ---------------------------------------------
    Assert-Case "See ${bt}nope/definitely-missing.xyz${bt}." 1 1 0 `
        'dead path is still a finding' | Out-Null
    # Built by concatenation so the literal never greps in a tracked copy of this file.
    $dead = 'zzz_dead_' + 'fragment_xyz();'
    Assert-Case ('Anchor: "' + $dead + '"') 1 0 1 `
        'dead fragment is still a finding' | Out-Null

    # --- rule 1 reads a span literally first; a bracket name is a wildcard to nothing --
    Assert-Case ("See ${bt}src/[id]/page.ps1${bt} here.") 0 1 0 `
        'a bracket-named file cited by its full path resolves, matched literally' | Out-Null
    Assert-Case ("See ${bt}[id]/page.ps1${bt} here.") 0 1 0 `
        'and by a subtree suffix, matched literally against the index' | Out-Null
    # A glob still resolves through Test-Path, the fallback kept for when the exact read misses.
    Assert-Case ("See ${bt}src/*.ps1${bt} here.") 0 1 0 `
        'a root-relative glob resolves through the Test-Path fallback' | Out-Null

    # --- Paths: each candidate as the tracked file it names, else as written ----------
    # An exact entry, a unique suffix (resolved to the file), an unresolvable path (kept as
    # written, ./ and backslashes normalized), the same file cited twice, and a case variant of
    # an exact entry, which is another path as the parser compares them.
    $pathsBody = "See ${bt}src/fixture.ps1${bt}, ${bt}deep/leaf.md${bt}, ${bt}.\nope\gone.txt${bt}, ${bt}src/fixture.ps1:3${bt}, ${bt}SRC/fixture.ps1${bt}."
    $pathsGot = @((Get-AnchorFindings -Text $pathsBody).Paths) -join '|'
    $pathsWant = 'SRC/fixture.ps1|nope/gone.txt|src/deep/leaf.md|src/fixture.ps1'
    if ($pathsGot -ceq $pathsWant) { Write-Host '  ok: Paths holds the resolved, suffix-resolved and unresolvable candidates, once each, ordinal-sorted' -ForegroundColor DarkGray }
    else { Write-Host "FAIL: Paths -- expected '$pathsWant', got '$pathsGot'" -ForegroundColor Red; $failures++ }
    $noPaths = @((Get-AnchorFindings -Text 'plain prose, no path').Paths)
    if ($noPaths.Count -eq 0) { Write-Host '  ok: a body with no path has an empty Paths' -ForegroundColor DarkGray }
    else { Write-Host "FAIL: Paths of a pathless body: '$($noPaths -join '|')'" -ForegroundColor Red; $failures++ }

    # --- quotes pair left to right; an out-of-bounds phrase is consumed, not reused ----------
    Assert-Case 'The "nine char" gate; then it stops here "short" today.' 0 0 0 `
        'two short quoted phrases on one line yield no fragment' | Out-Null
    $paired = Assert-Case ('Old "short" and, per the note; see "' + $dead + '" here.') 1 0 1 `
        'a short phrase before a dead fragment: the dead fragment is still reached'
    Assert-Match   ([regex]::Escape($dead)) ($paired.Findings -join "`n") 'the finding names the dead fragment'
    Assert-NoMatch 'per the note'           ($paired.Findings -join "`n") 'and not the prose between the two phrases'
    Assert-Case ('A "' + ('y' * 130) + '" and, per the note; see "ok" here.') 0 0 0 `
        'an over-120 phrase is consumed too, so the prose after it is no fragment' | Out-Null
    Assert-Case 'Only one " quote on this line; nothing_else();' 0 0 0 `
        'an unterminated quote closes no pair and yields no fragment' | Out-Null
    $maxBody = '"\' * 32768
    $null = Get-AnchorFindings -Text 'warm "$fixtureAnchor = 12345" up'  # warm the regex and the JIT: the first call in a process is far slower than the rest
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $maxRun = Get-AnchorFindings -Text $maxBody
    $sw.Stop()
    Assert-Match 'frags=0 findings=0 within-5s=True' `
        "frags=$($maxRun.FragCount) findings=$($maxRun.Findings.Count) within-5s=$($sw.ElapsedMilliseconds -lt 5000) at $($sw.ElapsedMilliseconds)ms" `
        'a 65536-character body (GitHub cap) of escaped quotes ending in a lone backslash yields no fragment, scanned inside 5s'
    $escaped = Assert-Case 'Anchor: "set $x = \"hi\"; done" in the file.' 1 0 1 `
        'a fragment holding an escaped quote is one fragment'
    Assert-Match ([regex]::Escape('set $x = "hi"; done')) ($escaped.Findings -join "`n") `
        'the escaped quote is unescaped and the fragment is read whole'

    # --- the length bounds measure the unescaped fragment, as the grep will see it ----------
    Assert-Case 'See "no_go();" here.' 0 0 0 `
        'a code-ish phrase under the lower bound is not grepped' | Out-Null
    Assert-Case ('See "' + ('zzz_gone_' * 15) + '" here.') 0 0 0 `
        'a code-ish phrase over the upper bound is not grepped' | Out-Null
    Assert-Case 'Anchor: "\"a\": true," here.' 0 0 0 `
        'twelve raw characters, ten unescaped: under the lower bound, not grepped' | Out-Null
    Assert-Case ('Anchor: "' + ('zzz_gone_' * 12) + ('\"' * 8) + '" here.') 1 0 1 `
        'a hundred and twenty-four raw characters, a hundred and sixteen unescaped: checked' | Out-Null

    # --- URLs and env-var style tokens are skipped before the [:#] split ---------
    Assert-Case "See ${bt}https://cdn.example.com/lib.js${bt} for details." 0 0 0 `
        'bare backticked URL is not a path candidate' | Out-Null
    Assert-Case "Reads ${bt}`$env:CONFIG/gate.ps1${bt} at startup." 0 0 0 `
        'env-var style token is skipped, not counted' | Out-Null
    Assert-Case "Reads ${bt}%TEMP%/gate.ps1${bt} at startup." 0 0 0 `
        'percent-var token is skipped, not counted' | Out-Null
    Assert-Case "Run ${bt}<plugin>/src/fixture.ps1${bt} from the root." 0 0 0 `
        'placeholder token is skipped, not counted' | Out-Null
    # Pins the ^ anchor: a skip char mid-token must not skip the candidate.
    Assert-Case ('See ' + $bt + 'x$y/foo.ps1' + $bt + ' here.') 1 1 0 `
        'skip char mid-token is still a candidate and still flags' | Out-Null

    # --- absolute tokens name the runner's filesystem, not HEAD -- skipped -------
    Assert-Case ('See ' + $bt + 'C:\opt\gate\gate.ps1' + $bt + ' here.') 0 0 0 `
        'drive-letter absolute path is skipped, not counted' | Out-Null
    Assert-Case ('See ' + $bt + 'C:/opt/gate/gate.ps1' + $bt + ' here.') 0 0 0 `
        'drive-letter path in forward-slash form is skipped, not counted' | Out-Null
    Assert-Case ('See ' + $bt + '\\server\share\gate.ps1' + $bt + ' here.') 0 0 0 `
        'UNC path is skipped, not counted' | Out-Null
    # --- the rest of the non-relative families: an anchor is a relative repo path
    Assert-Case "See ${bt}/nope/rooted.md${bt} here." 0 0 0 `
        'rooted forward-slash token is skipped, not counted' | Out-Null
    Assert-Case ('See ' + $bt + '\Windows\win.ini' + $bt + ' here.') 0 0 0 `
        'rooted backslash token is skipped, not counted' | Out-Null
    # The root-relative spelling of a real tracked file: still not an anchor, because
    # what it names is the runner's filesystem root, not the repo root.
    Assert-Case "See ${bt}/src/fixture.ps1${bt} here." 0 0 0 `
        'root-relative spelling of a tracked file is skipped, not verified' | Out-Null
    Assert-Case "See ${bt}~/.claude/settings.json${bt} here." 0 0 0 `
        'home-relative token is skipped, not counted' | Out-Null
    # A leading .. names the runner's filesystem above the repo -- machine-dependent.
    Assert-Case "See ${bt}../outside.md${bt} here." 0 0 0 `
        'parent-escaping token is skipped, not counted' | Out-Null
    Assert-Case ('See ' + $bt + '..\outside.md' + $bt + ' here.') 0 0 0 `
        'parent-escaping backslash token is skipped, not counted' | Out-Null
    Assert-Case "See ${bt}file:///C:/opt/x.ps1${bt} here." 0 0 0 `
        'file:// scheme token is skipped, not split into a bare prefix' | Out-Null
    Assert-Case "See ${bt}ftp://host/x.ps1${bt} here." 0 0 0 `
        'non-http scheme token is skipped, not split into a bare prefix' | Out-Null
    Assert-Case "See ${bt}C:x/y.ps1${bt} here." 0 0 0 `
        'drive-relative token is skipped, not split into a bare drive letter' | Out-Null
    Assert-Case "See ${bt}AB:/two-letters.ps1${bt} here." 0 0 0 `
        'multi-letter colon prefix is not relative either and is skipped' | Out-Null
    # The cross-repo citation form of docs/contract.md section 3: a <repo>:<path> token and a
    # backticked fragment. Both halves must contribute nothing -- this gate verifies only the
    # repo it runs in, so a sibling repo's path and quote are unverifiable here, not dead.
    # The fragment must be one that WOULD count double-quoted (>= 12 chars, code-ish, not
    # prose): a short or prosey token is dropped by rules that have nothing to do with
    # backticking, and the case would pass while pinning nothing. The mirror case below is
    # what proves the backtick spelling is doing the work.
    Assert-Case "See ${bt}byovox:src/stt.rs${bt} -- ${bt}let routed = layout.resolve(key);${bt} here." 0 0 0 `
        'cross-repo citation contributes no path and no fragment' | Out-Null
    Assert-Case 'See "let routed = layout.resolve(key);" here.' 1 0 1 `
        'the same fragment double-quoted IS checked -- backticks instead of quotes is what skips it' | Out-Null
    Assert-Case "See ${bt}my_repo:src/stt.rs${bt} here." 1 1 0 `
        'an underscored repo name is NOT scheme-skipped -- the documented character class is real' | Out-Null
    # Guards against an over-broad colon-prefix skip: a relative path carrying a :line
    # anchor still splits and resolves.
    Assert-Case ('See ' + $bt + 'src/fixture.ps1:20' + $bt + ' here.') 0 1 0 `
        'relative path with a :line anchor still resolves' | Out-Null
    Assert-Case ('See ' + $bt + 'src/fixture.ps1#L20' + $bt + ' here.') 0 1 0 `
        'relative path with a #L line anchor still resolves' | Out-Null

    # --- an empty candidate from the [:#] split is dropped, not globbed ---------------------
    Assert-Case "See ${bt}#L12/foo.ps1${bt} here." 0 0 0 `
        'span starting with the [:#] separator yields no candidate' | Out-Null

    # --- the Doc impact on close declaration is a promise, not an anchor --------------------
    # It names docs the change will create or rewrite, so a path there need not exist at HEAD
    # and a quotation there may be text the doc will be reduced to. Both spellings excluded.
    Assert-Case ("Doc impact on close: new " + $bt + "docs/not-yet.md" + $bt + " (absent at HEAD).") 0 0 0 `
        'inline doc-impact line: a not-yet-created path is not a candidate' | Out-Null
    Assert-Case ("## Doc impact on close`n`nnew " + $bt + "docs/not-yet.md" + $bt + ", indexed in " + $bt + "docs/index.md" + $bt + ".") 0 0 0 `
        'doc-impact heading: its whole block is excluded' | Out-Null
    Assert-Case ('Doc impact on close: the runbook collapses to "run the script; on a stop, do what it names".') 0 0 0 `
        'inline doc-impact line: a quoted future sentence is not a fragment' | Out-Null
    # The heading form must stop at the next heading, not swallow the rest of the body.
    Assert-Case ("## Doc impact on close`n`nnew " + $bt + "docs/not-yet.md" + $bt + ".`n`n## Anchors`n`nSee " + $bt + "src/gone.ps1" + $bt + " here.") 1 1 0 `
        'doc-impact heading ends at the next heading; a dead path after it still flags' | Out-Null
    # An inline declaration excludes its own line only.
    Assert-Case ("Doc impact on close: new " + $bt + "docs/not-yet.md" + $bt + ".`nSee " + $bt + "src/gone.ps1" + $bt + " here.") 1 1 0 `
        'inline doc-impact excludes one line; the next line is still scanned' | Out-Null

    # --- clean conventional body ------------------------------------------------------------
    Assert-Case ('See ' + $bt + 'src/fixture.ps1' + $bt + ' -- "$fixtureAnchor = 12345" holds the value.') 0 1 1 `
        'conventional path + fragment body is clean and counted' | Out-Null

    # --- an anchor-shaped line verifies every fragment, in the file it cites ----------
    # A list item whose first backticked span passes rule 1 and resolves: every quoted fragment
    # in the length bounds is verified, plain words too, and grepped in that one file.
    # Built by concatenation so the literal never greps in a tracked copy of this file.
    $plainDead = 'plain words ' + 'nowhere at all here'
    $anchorMiss = Assert-Case ('- ' + $bt + 'src/fixture.ps1' + $bt + ' -- "' + $plainDead + '"') 1 1 1 `
        'on an anchor line, a plain-words fragment absent from the cited file is a finding and counted'
    Assert-Match 'src/fixture\.ps1' ($anchorMiss.Findings -join "`n") 'and the finding names the cited file'
    Assert-Case ('- ' + $bt + 'src/fixture.ps1' + $bt + ' -- "plain words that live in the fixture"') 0 1 1 `
        'the same shape present in the cited file is counted and clean' | Out-Null
    Assert-Case ('- ' + $bt + 'src/fixture.ps1' + $bt + ' -- "$otherAnchor = 67890"') 1 1 1 `
        'a fragment present in another tracked file and absent from the cited one is a finding' | Out-Null
    Assert-Case ('  1. ' + $bt + 'src/fixture.ps1:2' + $bt + ' -- "$otherAnchor = 67890"') 1 1 1 `
        'a numbered, indented item citing the file with a :line suffix scopes to that file too' | Out-Null
    Assert-Case ('- ' + $bt + 'src/fixture.ps1' + $bt + ' -- "plain words that live in the fixture" and "$otherAnchor = 67890"') 1 1 2 `
        'two fragments on one anchor line are each verified in the cited file' | Out-Null
    $leaf = Assert-Case ('- ' + $bt + 'deep/leaf.md' + $bt + ' -- "leaf words in the deep file" beside "$fixtureAnchor = 12345"') 1 1 2 `
        'a path resolved by the unique suffix match scopes the grep to the file it resolved to'
    Assert-Match 'src/deep/leaf\.md' ($leaf.Findings -join "`n") 'and the finding names the resolved file'
    # The suffix read is literal, not a suffix match against a wildcard: the unique suffix above
    # is a real tracked-file suffix, and a bracket name resolves the same way, not as a pattern.
    Assert-Case ('- ' + $bt + 'src/[id]/page.ps1' + $bt + ' -- "page words that live in the bracket file"') 0 1 1 `
        'an anchor line citing a bracket-named file, with a fragment it holds, is clean' | Out-Null
    $bracketMiss = Assert-Case ('- ' + $bt + 'src/[id]/page.ps1' + $bt + ' -- "' + $plainDead + '"') 1 1 1 `
        'and with a fragment it lacks, one finding names the file, and rule 1 reports no path finding'
    Assert-Match ([regex]::Escape('src/[id]/page.ps1')) ($bracketMiss.Findings -join "`n") 'the finding names the bracket-named file exactly'
    # An anchor line never matches a glob span: unlike the two real tracked files src/*.ps1 names
    # above, this glob matches exactly one, which the old wildcard suffix read used to resolve.
    Assert-Case ('- ' + $bt + 'src/deep/*.md' + $bt + ' -- "' + $plainDead + '"') 0 1 0 `
        'a glob matching exactly one tracked file is still not an anchor line: plain words are not grepped there' | Out-Null
    Assert-Case ('- ' + $bt + 'src/fixture.ps1' + $bt + ' -- "' + $bt + 'no' + $bt + ' such ' + $bt + 'text' + $bt + ' in it"') 1 1 1 `
        'on an anchor line a miss with interior backticks is a finding, not a skipped composite' | Out-Null
    # Everywhere else, today's behaviour: the code-like filter, the repo-wide grep, the skip.
    Assert-Case ('Prose citing ' + $bt + 'src/fixture.ps1' + $bt + ' -- "' + $plainDead + '"') 0 1 0 `
        'the same plain-words fragment on a prose line is dropped unverified' | Out-Null
    Assert-Case ('- ' + $bt + 'pwsh -File src/fixture.ps1' + $bt + ' -- "' + $plainDead + '"') 0 0 0 `
        'on a list item whose first span is a command line, it is dropped unverified' | Out-Null
    Assert-Case ('- ' + $bt + 'nope/missing.xyz' + $bt + ' -- "' + $plainDead + '"') 1 1 0 `
        'on a list item whose first span does not resolve, it is dropped unverified' | Out-Null
    Assert-Case ('- see ' + $bt + 'pwsh x' + $bt + ' then ' + $bt + 'src/fixture.ps1' + $bt + ' -- "' + $plainDead + '"') 0 1 0 `
        'the first backticked span decides, not a later one' | Out-Null
    Assert-Case ("- ${bt}src/fixture.ps1${bt} -- the fixture`n  " + '"' + $plainDead + '"') 0 1 0 `
        'a continuation line under an anchor item is judged by itself' | Out-Null
    Assert-Case 'Anchor: "$otherAnchor = 67890" in the tree.' 0 0 1 `
        'a code-like fragment on a line that cites no file is still grepped repo-wide' | Out-Null
    Assert-Case ("- ${bt}byovox:src/stt.rs${bt} -- " + '"' + $plainDead + '"') 0 0 0 `
        'a citation into another repository is never an anchor line' | Out-Null
    # An anchor line names a file in the index the grep reads, not whatever Test-Path answers.
    Assert-Case ('- ' + $bt + 'src/*.ps1' + $bt + ' -- "$fixtureAnchor = 12345"') 0 1 1 `
        'a glob span names no one tracked file, so its line reads as before and the fragment greps repo-wide' | Out-Null
    Assert-Case ('- ' + $bt + 'src/untracked.md' + $bt + ' -- "plain words that live untracked"') 0 1 0 `
        'an untracked file is no anchor line, so its plain-words fragment is not reported against it' | Out-Null
    Assert-Case ('- ' + $bt + './src/fixture.ps1' + $bt + ' -- "plain words that live in the fixture"') 0 1 1 `
        'a ./ spelling names the same tracked file' | Out-Null
    Assert-Case ('- ' + $bt + 'src/pkg.d' + $bt + ' -- "plain words inside the package dir"') 0 1 0 `
        'a span naming a directory in the index is no anchor line, however few files it holds' | Out-Null
    if ($umlautMade) {
        # Under an OEM console code page, as a Windows console has, so the index is read as the
        # UTF-8 git writes rather than decoded in the caller's encoding.
        $callerEncoding = [Console]::OutputEncoding
        try {
            [Console]::OutputEncoding = [System.Text.Encoding]::GetEncoding(437)
            Assert-Case ('- ' + $bt + $umlautName + $bt + ' -- "plain words in the umlaut file"') 0 1 1 `
                'a tracked file with a non-ASCII name, spelled exactly, is found and its fragment greps' | Out-Null
            Assert-Case ('- ' + $bt + $umlautName.Substring(4) + $bt + ' -- "plain words in the umlaut file"') 0 1 1 `
                'and by its suffix' | Out-Null
            $umlautMiss = Assert-Case ('- ' + $bt + $umlautName + $bt + ' -- "' + $plainDead + '"') 1 1 1 `
                'a miss there names the file as written, not C-quoted'
            $after = [Console]::OutputEncoding.CodePage
        }
        finally { [Console]::OutputEncoding = $callerEncoding }
        Assert-Match ([regex]::Escape("'$umlautName'")) ($umlautMiss.Findings -join "`n") 'the finding carries the unquoted name'
        Assert-Match '^437$' "$after" 'the caller''s console output encoding is unchanged by the parser'
    }
    else { Write-Host "  skip: this filesystem refuses a non-ASCII file name" -ForegroundColor DarkGray }
    # Fragments are case-sensitive to the grep, so a hit does not stand in for a case-variant miss.
    Assert-Case ("- ${bt}src/fixture.ps1${bt} -- `"PLAIN WORDS THAT LIVE IN THE FIXTURE`"`n- ${bt}src/fixture.ps1${bt} -- `"plain words that live in the fixture`"") 1 1 2 `
        'a case-variant miss is reported beside the hit' | Out-Null
    Assert-Case ('- ' + $bt + 'src/fixture.ps1' + $bt + ' -- "' + ('plain words ' * 11) + '"') 0 1 0 `
        'the length bounds still apply on an anchor line: a hundred-and-thirty-two-character phrase is not checked' | Out-Null
    # A list line with no quote has nothing to verify and resolves nothing: four hundred of them
    # citing one path, which rule 1 resolves once, are read inside five seconds.
    $listBody = ((1..400) | ForEach-Object { "- ${bt}nope/missing.md${bt} -- item $_" }) -join "`n"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $null = Get-AnchorFindings -Text $listBody
    $sw.Stop()
    Assert-Match 'within-5s=True' "within-5s=$($sw.ElapsedMilliseconds -lt 5000) at $($sw.ElapsedMilliseconds)ms" `
        'four hundred unquoted list lines are read inside five seconds'

    # --- the fragment grep on a git that rejects --max-count ---------------------------------
    # git before 2.38 has no --max-count: the call errors out, the hit list is empty, and a
    # fragment reads as dead, or, where it holds a backtick, is dropped from the count. A git shim
    # first on PATH fails that option the way such a git does and hands every other call to the
    # real git, so the row runs on any git: the same clean body must still resolve its fragment.
    # The shim is a .cmd on Windows and a sh script elsewhere, since PowerShell resolves a native
    # command through PATH on each call; it names no path, it drops its own directory, the first
    # PATH entry, and calls git by name, so a git installed under a non-ASCII path is reached too.
    $shimDir = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('anchor-git-shim-' + [guid]::NewGuid().ToString('N')))
    if ($IsWindows) {
        [IO.File]::WriteAllLines((Join-Path $shimDir.FullName 'git.cmd'), @(
            '@echo off',
            'echo %* | findstr /C:"--max-count" >nul',
            'if not errorlevel 1 (',
            '  echo error: unknown option max-count 1>&2',
            '  exit /b 129',
            ')',
            'set "PATH=%PATH:*;=%"',
            'git %*'), [Text.Encoding]::ASCII)
    } else {
        $shimSh = Join-Path $shimDir.FullName 'git'
        [IO.File]::WriteAllText($shimSh, (@(
            '#!/bin/sh',
            'case "$*" in',
            '  *--max-count*) echo "error: unknown option max-count" >&2; exit 129;;',
            'esac',
            'PATH="${PATH#*:}"',
            'exec git "$@"') -join "`n") + "`n")
        chmod +x $shimSh
    }
    $savedPath = $env:PATH
    $env:PATH = $shimDir.FullName + [IO.Path]::PathSeparator + $env:PATH
    try {
        $shimmed = (Get-Command git).Source
        $null = git grep -F --max-count 1 -- 'x' 2>$null  # the instrument: the shim must reject the option
        $rejects = $LASTEXITCODE -eq 129
        $null = git grep -F -- '$fixtureAnchor = 12345' 2>$null  # and pass the rest to a git that hits
        $passes = $LASTEXITCODE -eq 0
        if ($shimmed -ne (Get-ChildItem -LiteralPath $shimDir.FullName | Select-Object -First 1).FullName -or -not $rejects -or -not $passes) {
            Write-Host "  skip: the git shim does not shadow git here or does not behave (git resolves to $shimmed, rejects=$rejects, passes=$passes), so the older-git shape is untested" -ForegroundColor DarkGray
        } else {
            Assert-Case ('See ' + $bt + 'src/fixture.ps1' + $bt + ' -- "$fixtureAnchor = 12345" holds the value.') 0 1 1 `
                'on a git that rejects --max-count, a live fragment still resolves' | Out-Null
        }
    } finally {
        $env:PATH = $savedPath
        Remove-Item -LiteralPath $shimDir.FullName -Recurse -Force
    }

    # --- the anchor gate's -Demote removes, then adds needs-ruling --------------------------
    # A gh function shadow answers the issue list with one agent-ready issue whose body holds a
    # dead backticked path, emitting only the fields the call asks for; it records every call
    # and exits 1 on the call matching $ghFailOn. The gate's comment step rides on -Comment,
    # not -Demote, so these runs post none.
    $global:ghCalls = @()
    $global:ghFailOn = ''
    $global:ghLabels = @()
    function gh {
        $call = $args -join ' '
        # The repository resolution asks gh about origin's URL; these cases are about the issue
        # calls that follow it, which tests/repo-slug asserts name that repository.
        if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
        $global:ghCalls += $call
        $global:LASTEXITCODE = if ($global:ghFailOn -and $call -match $global:ghFailOn) { 1 } else { 0 }
        if ($args[0] -eq 'issue' -and $args[1] -eq 'list') {
            $fields = @($args[[array]::IndexOf($args, '--json') + 1] -split ',')
            [pscustomobject]@{
                number = 30; title = 'dead'; body = 'See `nope/definitely-missing.xyz`.'
                labels = @($global:ghLabels | ForEach-Object { @{ name = $_ } })
            } | Select-Object $fields | ConvertTo-Json -AsArray -Depth 4
        }
    }
    function Run-Demote($Labels, $FailOn = '') {
        $global:ghCalls = @(); $global:ghFailOn = $FailOn; $global:ghLabels = $Labels
        try { $null = & $Gate -Demote 6>$null; '' } catch { "$_" }
    }
    $thrown = Run-Demote @('agent-ready')
    Assert-Match '^$'                                         $thrown     'the gate runs to its end (nothing thrown)'
    Assert-Match '^issue list '                               $ghCalls[0] 'the gate lists the agent-ready issues first'
    Assert-Match '^issue edit 30 --remove-label agent-ready$' $ghCalls[1] 'only agent-ready carried: the first edit removes agent-ready and names no other label'
    Assert-Match '^issue edit 30 --add-label needs-ruling$'   $ghCalls[2] 'the second edit adds needs-ruling and names no other label'
    Assert-Match '^3$'                                        $ghCalls.Count 'no call follows the two edits'
    $null = Run-Demote @('agent-ready', 'trivial')
    Assert-Match '^issue edit 30 --remove-label agent-ready --remove-label trivial$' $ghCalls[1] 'agent-ready and trivial carried: the removal names both and not checkpoint'
    $null = Run-Demote @('agent-ready', 'CHECKPOINT')
    Assert-Match '^issue edit 30 --remove-label agent-ready --remove-label checkpoint$' $ghCalls[1] 'a modifier carried as CHECKPOINT is removed: label names match case-insensitively, as gh matches them'
    $thrown = Run-Demote @('agent-ready') '--add-label'
    Assert-Match   'needs-ruling'        $thrown 'a failed add throws naming needs-ruling'
    Assert-Match   'repository may lack' $thrown 'a failed add says the repository may lack the label'
    Assert-Match   'no state label'      $thrown 'a failed add says the issue carries no state label'
    $thrown = Run-Demote @('agent-ready', 'checkpoint') '--remove-label'
    Assert-Match   'agent-ready, checkpoint.*nothing has changed' $thrown 'a failed removal throws naming the removal and saying nothing has changed'
    Assert-NoMatch '--add-label' ($ghCalls -join "`n") 'a failed removal runs no add'

    # --- the anchor gate's -Comment names the ouro release that ran -------------------------
    # Every run is from a separate scratch repo with one commit, so a stamp has a HEAD to name. With
    # an ouro plugin.json in the parent of the gate's directory, its version and no @ stamp; a
    # vendored tree has none there. A copy in a TEMP tree pins both sides: plugin.json is read beside
    # the script, never in the repo the gate runs in, and without an ouro one with a version, or when
    # it does not parse, the stamp is that repo's short HEAD.
    function Get-GateComment($GatePath) {
        $global:ghCalls = @(); $global:ghFailOn = ''; $global:ghLabels = @('agent-ready')
        try { $null = & $GatePath -Comment 6>$null } catch { return "threw: $_" }
        "$(@($global:ghCalls) -match '^issue comment 30 ')"
    }
    $idRoot = Join-Path ([IO.Path]::GetTempPath()) ('ouro-anchor-identity-' + [guid]::NewGuid().ToString('n'))
    $idTree = Join-Path $idRoot 'tree'
    New-Item -ItemType Directory -Path (Join-Path $idTree 'bin'), (Join-Path $idRoot 'repo') | Out-Null
    Push-Location -LiteralPath (Join-Path $idRoot 'repo')
    try {
        git init -q . 2>$null
        git remote add origin https://github.com/o/n.git 2>$null
        git -c user.name=t -c user.email=t@t -c commit.gpgsign=false commit -q --no-verify --allow-empty -m fixture 2>$null
        $manifest = try { Get-Content -LiteralPath (Join-Path (Split-Path (Split-Path $Gate -Parent) -Parent) '.claude-plugin/plugin.json') -Raw | ConvertFrom-Json } catch { $null }
        if ($manifest.name -ceq 'ouro') {
            $comment = Get-GateComment $Gate
            Assert-Match   ('^issue comment 30 --body Anchor gate \(ouro v' + [regex]::Escape($manifest.version.Trim()) + ', Test-AgentReadyAnchors\.ps1\): ') $comment 'in an ouro plugin tree the comment names ouro v and the plugin.json version'
            Assert-NoMatch '\.ps1 @ ' $comment 'in an ouro plugin tree the comment names no @ stamp'
        }
        else { Write-Host '  skip: no ouro plugin.json beside the gate (vendored)' -ForegroundColor DarkGray }
        # Both files the gate dot-sources travel with it, or the copy cannot run.
        Copy-Item -LiteralPath $Gate, $Parser, (Join-Path (Split-Path $Gate -Parent) 'Get-RepoSlug.ps1') -Destination (Join-Path $idTree 'bin')
        $stamp = '^issue comment 30 --body Anchor gate \(Test-AgentReadyAnchors\.ps1 @ ' + [regex]::Escape((git rev-parse --short HEAD).Trim()) + '\): '
        function Get-CopyComment($Manifest) {
            $dir = Join-Path $idTree '.claude-plugin'
            if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
            if ($null -ne $Manifest) {
                New-Item -ItemType Directory -Path $dir | Out-Null
                Set-Content -LiteralPath (Join-Path $dir 'plugin.json') -Value $Manifest -NoNewline
            }
            Get-GateComment (Join-Path $idTree 'bin/Test-AgentReadyAnchors.ps1')
        }
        Assert-Match $stamp (Get-CopyComment $null) 'no plugin.json beside the copy: the comment names @ and the scratch repo short HEAD'
        Assert-Match $stamp (Get-CopyComment '{"name":"other","version":"9.9.9"}') 'a plugin.json whose name is not ouro: the comment takes the same fallback'
        Assert-Match '^issue comment 30 --body Anchor gate \(ouro v7\.7\.7, Test-AgentReadyAnchors\.ps1\): ' (Get-CopyComment '{"name":"ouro","version":"7.7.7"}') 'an ouro plugin.json beside the copy and none in the repo it runs in: the comment names its version'
        foreach ($bad in '{"name":"ouro","version":""}', '{"name":"ouro","version":" "}', '{"name":"ouro","version":7}', '{"name":"ouro",', '{"name":"Ouro","version":"7.7.7"}') {
            Assert-Match $stamp (Get-CopyComment $bad) "plugin.json ${bad}: the comment takes the fallback and nothing throws"
        }
        # A caller's strict mode reaches the script it runs: a missing plugin.json or version must not throw.
        Set-StrictMode -Version Latest
        try {
            Assert-Match $stamp (Get-CopyComment $null) 'under strict mode, no plugin.json: the comment takes the fallback and nothing throws'
            Assert-Match $stamp (Get-CopyComment '{"name":"ouro"}') 'under strict mode, a plugin.json with no version: the comment takes the fallback and nothing throws'
        }
        finally { Set-StrictMode -Off }
    }
    finally { Pop-Location; Remove-Item function:Get-CopyComment -ErrorAction Ignore; Remove-Item -LiteralPath $idRoot -Recurse -Force }
    Remove-Item function:Get-GateComment
    Remove-Item function:gh, function:Run-Demote; Remove-Variable -Name ghCalls, ghFailOn, ghLabels -Scope Global
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $scratch -Recurse -Force
}

if ($failures -gt 0) { Write-Host "$failures failure(s)" -ForegroundColor Red; exit 1 }
Write-Host 'all anchor-findings cases pass' -ForegroundColor Green
exit 0
