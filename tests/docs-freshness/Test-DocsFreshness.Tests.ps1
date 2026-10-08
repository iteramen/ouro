<#
.SYNOPSIS
    Unit test for Test-DocsFreshness.ps1's S3 candidate rules and its repo-agnostic defaults.
.DESCRIPTION
    Drives the gate against scratch git repos built in TEMP -- no dependence on the
    surrounding checkout's layout, so the suite behaves the same from the plugin repo, a
    nested consumer checkout, or a vendored scripts dir. It runs in ten parts:

    Part 1 pins the gate's functions on one scratch repo: the S3 token-shape matrix in a doc
    and on a page, Test-GitIgnored, Test-TrackedPath, the shipped defaults and the
    blocking-signal sentence, each detailed below.
    Part 2 pins the checked-in fixture tree under fixtures/: fixtures/good/** yields no finding
    and fixtures/bad/** exactly one each, of a known signal, with the ignore-list parse and the
    index-required rule beside it.
    Part 3 pins a bound repo at a non-ASCII path under code page 437: the gate finds the root
    and the binding, and the caller's console encoding comes back unchanged.
    Part 4 pins a non-ASCII docs.index_path under code page 437: S4 still arms, and the
    caller's console encoding comes back unchanged.
    Part 5 pins a run with no python3 on PATH: a repo that declares a binding stops, and one
    with no binding runs on the defaults.
    Part 6 pins an HTML page in scope through the gate itself: a `file:` ignore entry naming it
    parses, its <code> spans resolve, and a page under an excluded tree is not scanned.
    Part 7 pins a doc index that names an HTML page: S4a and S4b take a page as they take a
    markdown doc.
    Part 8 pins S2's suppressed prefix against a parent-relative link's root-relative form.
    Part 9 pins S1's suppressed prefix over an HTML page, in both spellings.
    Part 10 pins a target starting with a single slash: markdown resolves it against the repo
    root, an HTML href skips it, and a target starting with two slashes is skipped in both.

    Beside the four Assert-* helpers -- Assert-GateRan fails a row once, naming the throw, when
    the gate threw instead of scanning -- the suite defines three: Set-EnvVar sets or unsets one
    environment variable for Part 1's Test-GitIgnored rows (TMP/TEMP/TMPDIR, GIT_DEFAULT_HASH,
    GIT_CONFIG_*), Get-DirIdentity names a directory as the filesystem spells it, and
    Get-PathWithoutPython3 is the caller's PATH less every directory that answers python3, both
    for Part 5.

    The S3 token-shape matrix is the point of Part 1: exactly two of the twelve shapes in the fixture
    doc are findings, the missing in-repo path spelled from the root and the one spelled
    parent-relative from the doc's own directory. The other ten each name something the gate
    cannot verify or need not -- another repo, the runner's filesystem, a rooted path, a
    parent-relative path that resolves outside the repo, a path gitignored by design (spelled
    from the root or parent-relative), or an in-repo path that exists -- and reporting any of
    the unverifiable ones makes the verdict machine-dependent. The scheme-prefixed and
    drive-absolute rows also guard the cross-repo citation form against a future
    candidate-filter change; the rooted row is the one a Windows-only run cannot catch.

    An HTML page in scope takes the same S3, from its <code> spans: bad/code-span-path.html
    spells those shapes as a page does, and the findings are the two missing in-repo paths, the
    two sharing a (planned) line, and the one past a fence-shaped line, which is ordinary text
    on a page.

    Test-GitIgnored is pinned case by case: a committed .gitignore counts and a per-clone
    .git/info/exclude does not; a committed rule still counts beneath a parent-directory rule in
    .git/info/exclude, in core.excludesFile, or in an init.templateDir's info/exclude; a negated
    path is not ignored while its sibling is; a path no rule ignores stays unignored when ? or ??
    matches the line break piped after it; a committed .gitignore under a non-ASCII directory
    counts, and a doc there is in scope, even when the caller has changed $OutputEncoding; a git
    init that fails makes the question throw; and with TMP, TEMP and TMPDIR pointed at an empty
    directory, that directory is still empty afterwards. Test-TrackedPath is pinned because a
    hardcoded path separator there makes every path fail on Unix and turns S1/S2 red on a clean
    repo.

    Also asserts that the shipped defaults name no repository: the suppression list and the
    banned table are empty, and S4 and the HTML scan stay unarmed until a consumer declares
    them.

    The blocking-signal sentence the sweep writes into its rolling issue is pinned here
    because the prose it replaces drifted: it is generated from $EmittedSignals minus the
    report-only set, and $EmittedSignals is checked against the emitters in the gate's own
    source so a new signal cannot appear in findings but not in the sentence.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Two levels up is the plugin root (gate under bin/) or, once vendored, the scripts dir itself.
$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Gate = @((Join-Path $Base 'Test-DocsFreshness.ps1'), (Join-Path $Base 'bin/Test-DocsFreshness.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Gate) { throw "Test-DocsFreshness.ps1 not found under $Base" }
. $Gate -AsModule

$failures = 0
# The two parts that run the gate capture its output, and a throw as "threw: <message>". A gate
# that threw ran no scan, so its content assertions would fail on their own terms and never name
# the cause; Assert-GateRan fails once with the captured text instead, and the caller skips them.
# The floor message of a python3 below 3.11 is the throw this exists for, and so is a bound repo
# with no python3, which Part 5 pins. A gate that ran but degraded -- an unbound repo, or a tree
# with no ouro-binding.py -- throws nothing and prints an INFO line into the same capture, so the
# content assertions match patterns on the capture and print it whole on a miss.
function Assert-GateRan($Out, $What) {
    if ($Out -match '(?m)^threw: ') {
        Write-Host "  FAIL: $What -- the gate threw: $(($Out -replace '(?s)^.*?threw: ', '').Trim())" -ForegroundColor Red
        $script:failures++
        return $false
    }
    return $true
}
function Assert-Match($Pattern, $Output, $What) {
    if ($Output -match $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- no match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}
function Assert-NoMatch($Pattern, $Output, $What) {
    if ($Output -notmatch $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- unexpected match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -eq $Actual) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}

$Fixture = Join-Path ([System.IO.Path]::GetTempPath()) ("docsfresh-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
# Outside the work tree: the empty temp directory, the template and the excludes file the
# Test-GitIgnored cases below point git at.
$IgnoreScratch = Join-Path ([System.IO.Path]::GetTempPath()) ("docsfresh-ign-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
try {
    New-Item -ItemType Directory -Force -Path (Join-Path $Fixture 'docs') | Out-Null
    Push-Location -LiteralPath $Fixture
    git init -q . 2>$null
    git config user.email t@t; git config user.name t

    # Gitignored BY DESIGN: present in the doc, absent from the index, forever unresolvable.
    # 'cfg/' not a bare filename: a token with no slash is rejected by Test-PathShaped one step
    # BEFORE the gitignore skip is reached, so a bare name would test nothing. The other lines
    # are Test-GitIgnored's: a rule beneath a directory a clone-local source ignores, a negation,
    # and ? and ??, which match the LF or CR LF the pipeline sends git after the path it asks.
    Set-Content -LiteralPath (Join-Path $Fixture '.gitignore') -Value 'cfg/', 'private/local.toml', 'gen/*.toml', '!gen/keep.toml', '?', '??' -Encoding utf8
    Set-Content -LiteralPath (Join-Path $Fixture 'docs/real.md') -Value '# Real' -Encoding utf8

    # A committed .gitignore and a doc under a directory named with U+00E9: git quotes that name in
    # its plain output, in check-ignore -v and in ls-files alike.
    $Cafe = "caf$([char]0xE9)"
    $cafeMade = try { New-Item -ItemType Directory -Force -Path (Join-Path $Fixture $Cafe) | Out-Null; $true } catch { $false }
    if ($cafeMade) {
        Set-Content -LiteralPath (Join-Path $Fixture "$Cafe/.gitignore") -Value 'local.toml' -Encoding utf8
        Set-Content -LiteralPath (Join-Path $Fixture "$Cafe/notes.md") -Value '# Notes' -Encoding utf8
    }
    else { Write-Host "  skip: cannot create a directory named with U+00E9 under $Fixture" -ForegroundColor DarkGray }

    $bt = [char]96
    $tokens = @(
        "- exists, in repo:      ${bt}docs/real.md${bt}"
        "- MISSING, in repo:     ${bt}docs/gone.md${bt}"
        "- parent-escaping:      ${bt}../../sibling-repo/docs/x.md${bt}"
        "- parent-relative, exists:  ${bt}../docs/real.md${bt}"
        "- parent-relative, MISSING: ${bt}../docs/gone2.md${bt}"
        "- home-relative:        ${bt}~/.config/tool/settings.json${bt}"
        "- UNC share:            ${bt}//host/share/notes.md${bt}"
        "- gitignored by design: ${bt}cfg/local.settings.toml${bt}"
        "- parent-relative, gitignored by design: ${bt}../cfg/local.settings.toml${bt}"
        "- rooted, POSIX:        ${bt}/etc/app/config.json${bt}"
        "- scheme-prefixed:      ${bt}other-repo:src/lib.rs${bt}"
        "- drive-absolute:       ${bt}C:/elsewhere/readme.md${bt}"
    ) -join "`n"
    Set-Content -LiteralPath (Join-Path $Fixture 'docs/tokens.md') -Value "# Tokens`n`n$tokens`n" -Encoding utf8
    Set-Content -LiteralPath (Join-Path $Fixture 'docs/planned.md') `
        -Value "# Planned`n`nsee [x](gone3.md) and ${bt}../docs/real.md${bt} (planned).`n" -Encoding utf8
    git add -A 2>$null; git commit -q -m fixture 2>$null

    $docs = @(Get-InScopeDocs -Root $Fixture)
    Assert-Equal $(if ($cafeMade) { 4 } else { 3 }) $docs.Count 'only the tracked docs are in scope (the gitignored file is not a doc)'
    if ($cafeMade) {
        Assert-Equal $true ($docs -contains "$Cafe/notes.md") 'a tracked doc under a non-ASCII directory is in scope'
    }

    $s3 = @(Get-CodePathFindings -Root $Fixture -Docs $docs -IgnoreTokens @() -IgnoreFiles @())
    Assert-Equal 2 $s3.Count 'exactly two of the twelve token shapes are S3 findings'
    Assert-Equal 'docs/gone.md ../docs/gone2.md' (@($s3 | ForEach-Object Target) -join ' ') `
        'the findings are the missing in-repo paths, root-relative and parent-relative, and none of the unverifiable shapes'

    # S1 reads the same line-global count: a broken link beside an in-repo parent-relative
    # backtick path is one of two references, so the line's (planned) marker suppresses neither.
    $s1 = @(Get-MarkdownLinkFindings -Root $Fixture -Docs $docs -IgnoreTokens @() -IgnoreFiles @())
    Assert-Equal 'gone3.md' (@($s1 | ForEach-Object Target) -join ' ') `
        'a broken link on a (planned) line is reported when a parent-relative in-repo path shares the line'

    Assert-Equal $true  (Test-GitIgnored -RootFull $Fixture -RelPath 'cfg/local.settings.toml') 'a gitignored path is recognized as ignored'
    Assert-Equal $false (Test-GitIgnored -RootFull $Fixture -RelPath 'docs/gone.md')            'a merely-absent path is not ignored'

    # The skip must follow COMMITTED policy only. A per-clone exclude would make the verdict
    # machine-dependent -- the property the skip exists to protect.
    $exclude = Join-Path $Fixture '.git/info/exclude'
    Add-Content -LiteralPath $exclude -Value 'notes/' -Encoding utf8
    Assert-Equal $false (Test-GitIgnored -RootFull $Fixture -RelPath 'notes/scratch.md') `
        'a path ignored only by .git/info/exclude is NOT treated as ignored'

    # One level down, the repo's own git lets a clone-local rule on the parent directory hide the
    # committed rule beneath it. That, a machine's init.templateDir, a negation and a non-ASCII name
    # must all leave the committed answer standing. Each question is a cache miss, asked with TMP,
    # TEMP and TMPDIR on an empty directory: the git dir it goes through must be gone afterwards.
    $emptyTemp = Join-Path $IgnoreScratch 'temp'
    New-Item -ItemType Directory -Force -Path $emptyTemp, (Join-Path $IgnoreScratch 'template/info') | Out-Null
    Set-Content -LiteralPath (Join-Path $IgnoreScratch 'template/info/exclude') -Value 'private/' -Encoding utf8
    Set-Content -LiteralPath (Join-Path $IgnoreScratch 'excludes') -Value 'private/' -Encoding utf8
    # $null unsets: [Environment]::SetEnvironmentVariable given $null from PowerShell leaves the
    # variable set to an empty string, and an empty GIT_DEFAULT_HASH breaks every git init after it.
    function Set-EnvVar([string]$Name, $Value) {
        if ($null -eq $Value) { Remove-Item -LiteralPath "Env:$Name" -ErrorAction Ignore }
        else { [Environment]::SetEnvironmentVariable($Name, $Value) }
    }
    $savedTemp = @{}
    foreach ($v in 'TMP', 'TEMP', 'TMPDIR') { $savedTemp[$v] = [Environment]::GetEnvironmentVariable($v); Set-EnvVar $v $emptyTemp }
    try {
        Set-Content -LiteralPath $exclude -Value 'private/' -Encoding utf8
        $script:IgnoreCheckCache.Clear()
        Assert-Equal $true (Test-GitIgnored -RootFull $Fixture -RelPath 'private/local.toml') `
            'a committed rule beneath a directory .git/info/exclude ignores still counts'
        Set-Content -LiteralPath $exclude -Value '' -Encoding utf8
        git config core.excludesFile ((Join-Path $IgnoreScratch 'excludes') -replace '\\', '/')
        $script:IgnoreCheckCache.Clear()
        Assert-Equal $true (Test-GitIgnored -RootFull $Fixture -RelPath 'private/local.toml') `
            'a committed rule beneath a directory core.excludesFile ignores still counts'
        git config --unset core.excludesFile

        Assert-Equal $false (Test-GitIgnored -RootFull $Fixture -RelPath 'gen/keep.toml')  'a path a committed negation re-includes is not ignored'
        Assert-Equal $true  (Test-GitIgnored -RootFull $Fixture -RelPath 'gen/other.toml') 'its sibling the negation does not name is still ignored'
        Assert-Equal $false (Test-GitIgnored -RootFull $Fixture -RelPath 'open/settings.toml') `
            'a path no rule ignores is not ignored when ? or ?? matches the line break piped after it'

        if ($cafeMade) {
            Assert-Equal $true (Test-GitIgnored -RootFull $Fixture -RelPath "$Cafe/local.toml") `
                'a committed .gitignore under a non-ASCII directory counts'
            # The path reaches git encoded with $OutputEncoding, which a caller may have changed.
            $savedOutputEncoding = $OutputEncoding
            try {
                $OutputEncoding = [System.Text.Encoding]::ASCII
                $script:IgnoreCheckCache.Clear()
                Assert-Equal $true (Test-GitIgnored -RootFull $Fixture -RelPath "$Cafe/local.toml") `
                    'and still counts when the caller has set $OutputEncoding to ASCII'
            }
            finally { $OutputEncoding = $savedOutputEncoding }
        }

        # git init can create the dir and still fail: the question throws, and the dir goes too.
        $savedHash = [Environment]::GetEnvironmentVariable('GIT_DEFAULT_HASH')
        Set-EnvVar 'GIT_DEFAULT_HASH' 'bogus'
        try {
            $script:IgnoreCheckCache.Clear()
            $threw = $false
            try { Test-GitIgnored -RootFull $Fixture -RelPath 'private/local.toml' | Out-Null } catch { $threw = $true }
            Assert-Equal $true $threw 'a git init that fails makes the question throw'
        }
        finally { Set-EnvVar 'GIT_DEFAULT_HASH' $savedHash }

        # GIT_CONFIG_* hands init.templateDir to every git the gate starts.
        $gitConfig = @{ GIT_CONFIG_COUNT = '1'; GIT_CONFIG_KEY_0 = 'init.templateDir'
            GIT_CONFIG_VALUE_0 = ((Join-Path $IgnoreScratch 'template') -replace '\\', '/') }
        $savedConfig = @{}
        foreach ($k in $gitConfig.Keys) { $savedConfig[$k] = [Environment]::GetEnvironmentVariable($k); Set-EnvVar $k $gitConfig[$k] }
        try {
            $script:IgnoreCheckCache.Clear()
            Assert-Equal $true (Test-GitIgnored -RootFull $Fixture -RelPath 'private/local.toml') `
                'a committed rule beneath a directory an init.templateDir info/exclude ignores still counts'
        }
        finally { foreach ($k in $savedConfig.Keys) { Set-EnvVar $k $savedConfig[$k] } }
    }
    finally { foreach ($v in $savedTemp.Keys) { Set-EnvVar $v $savedTemp[$v] } }
    # System.IO, not Get-ChildItem: on Linux, -LiteralPath still globs a '*' or '?' in the
    # directory's own path, and would enumerate a sibling.
    Assert-Equal 0 @([System.IO.Directory]::GetFileSystemEntries($emptyTemp)).Count `
        'with TMP, TEMP and TMPDIR on an empty directory, that directory is still empty afterwards'

    # Test-TrackedPath is where a hardcoded separator breaks every path on Unix.
    $idx = Get-TrackedIndex -RootFull $Fixture
    Assert-Equal $true (Test-TrackedPath $idx (Join-Path $Fixture 'docs/real.md')) `
        'a tracked file resolves through Test-TrackedPath on this platform'

    # S4 and the HTML scan stay silent until a consumer declares them.
    Assert-Equal 0 @(Get-IndexFindings -Root $Fixture -Index '' -Trees @()).Count 'S4 is unarmed without IndexPath and IndexedTrees'
    Assert-Equal 0 @(Get-InScopeHtmlDocs -Root $Fixture -Globs @()).Count          'HTML scanning is unarmed without HtmlGlobs'
    Assert-Equal 0 @(Get-BannedReferenceFindings -Root $Fixture -Docs $docs -Banned ([ordered]@{})).Count 'S8 is unarmed with an empty banned table'
    Assert-Equal 0 @(Get-WorkRemainingFindings -Root $Fixture -Docs $docs -Exemptions @()).Count 'the fixture docs carry no work-remaining content'

    # The shipped defaults must name no repository.
    Assert-Equal 0 $SuppressPrefixes.Count  'the default suppression list is empty'
    Assert-Equal 0 $BannedSubstrings.Count  'the default banned table is empty'
    Assert-Equal 0 $IndexedTrees.Count      'no indexed trees are assumed'
    Assert-Equal 0 $PlanningPaths.Count     'no planning paths are assumed'
    Assert-Equal ''  $IndexPath             'no index path is assumed'
    Assert-Equal ''  $DocFxApiPattern       'no generated-docs pattern is assumed'
    Assert-Equal 'S9 S3 S10' ($ReportOnlySignals -join ' ') 'S9, S3 and S10 are report-only by default'
    Assert-Equal $true ($PathExtensions -contains '.toml') 'the default extension list covers .toml'

    # The sweep's rolling-issue sentence is generated, never written: the source of this gate
    # published a hand-written one that drifted into calling a blocking signal report-only.
    Assert-Equal 'Gate signals (blocking): S1, S2, S3, S4a, S4b, S7, S8, S10. Report-only: S9.' `
        (Get-BlockingSignalSentence -ReportOnly @('S9')) `
        'the sentence lists emitted-minus-report-only for report_only = S9'
    Assert-Equal 'Gate signals (blocking): S1, S2, S4a, S4b, S7, S8. Report-only: S3, S9, S10.' `
        (Get-BlockingSignalSentence) `
        'the sentence follows the shipped report-only default without being told it'
    Assert-Equal 'Gate signals (blocking): none. Report-only: S1, S2, S3, S4a, S4b, S7, S8, S9, S10.' `
        (Get-BlockingSignalSentence -ReportOnly $EmittedSignals) `
        'an all-report-only repo says none rather than emitting an empty list'

    # The constant must equal the signals the gate can actually emit -- read off the source, so
    # a new emitter that skipped the constant fails here and not in a consumer's issue body.
    $emitters = @([regex]::Matches((Get-Content $Gate -Raw), "New-Finding -Signal '([^']+)'") |
        ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
    Assert-Equal ($emitters -join ' ') (($EmittedSignals | Sort-Object -Unique) -join ' ') `
        'the exported signal set matches every signal the gate emits'
}
finally {
    Pop-Location
    if (Test-Path -LiteralPath $Fixture) { Remove-Item -LiteralPath $Fixture -Recurse -Force }
    if (Test-Path -LiteralPath $IgnoreScratch) { Remove-Item -LiteralPath $IgnoreScratch -Recurse -Force }
}

# =======================================================================================
# Part 2 -- the fixture tree under fixtures/.
#
# fixtures/good/** must produce zero findings, fixtures/bad/** exactly one each, of a known
# signal. The live repo is deliberately NOT a test target: it changes daily, and a test that
# fails for unrelated reasons is a test that gets deleted.
#
# The tree is COPIED into a scratch git repo in TEMP rather than scanned in place, for the
# same reason Part 1 builds its repo from scratch: every signal resolves against
# `git ls-files`, so scanning in place would make the suite pass or fail on whether the
# fixtures happen to be committed in the surrounding checkout -- red on the branch that adds
# them, and red again in any vendored layout where the tests ship without a work tree.
# =======================================================================================

# Two prefixes the fixture tree names but does not contain (a sibling repo's sources, an
# external host's tree). $SuppressPrefixes ships EMPTY -- a consumer's list must not be a
# plugin default, which Part 1 just asserted -- so the fixture supplies its own.
$SuppressPrefixes = @('vendorlib/', 'hostsite/')

$FixtureSrc = Join-Path $PSScriptRoot 'fixtures'
$FixtureDir = Join-Path ([System.IO.Path]::GetTempPath()) ("docsfix-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
try {
    New-Item -ItemType Directory -Force -Path $FixtureDir | Out-Null
    Copy-Item -Path (Join-Path $FixtureSrc '*') -Destination $FixtureDir -Recurse -Force
    Push-Location -LiteralPath $FixtureDir
    git init -q . 2>$null
    git config user.email t@t; git config user.name t
    git add -A 2>$null; git commit -q -m fixtures 2>$null

    # A doc that exists on disk but is not tracked is not the repo's doc: it cannot be fixed by
    # editing the repo, and CI -- which only ever has tracked files -- can never see it. Asked
    # BEFORE any other enumeration, so the tracked-index cache is built with the file present
    # rather than answering from an index taken before it existed.
    $untracked = Join-Path $FixtureDir 'good/untracked-scratch.md'
    try {
        Set-Content -LiteralPath $untracked -Value '# scratch' -Encoding utf8
        $withUntracked = @(Get-InScopeDocs -Root $FixtureDir)
        Assert-Equal $true ($withUntracked.Count -gt 0) 'fixture docs are enumerated'
        Assert-Equal $false ($withUntracked -contains 'good/untracked-scratch.md') 'an untracked doc is not enumerated'
    } finally {
        Remove-Item -LiteralPath $untracked -Force -ErrorAction SilentlyContinue
    }

    Assert-Equal $true  (Test-PathShaped 'docs/team/code-policies.md') 'slash + known extension is path-shaped'
    Assert-Equal $false (Test-PathShaped 'Directory.Build.props')      'bare filename with extension but no slash is not path-shaped'
    Assert-Equal $false (Test-PathShaped 'IZorbTool')                  'bare interface name is not a path'
    Assert-Equal $false (Test-PathShaped 'ZorbBase')                   'bare base-class name is not a path'

    $docs = @(Get-InScopeDocs -Root $FixtureDir)
    $s1   = @(Get-MarkdownLinkFindings -Root $FixtureDir -Docs $docs)
    Assert-Equal 6 $s1.Count 'S1 finds one broken link per bad fixture, the two mixed (planned) lines, and html-comment.md''s two live links'
    $s1Broken = @($s1 | Where-Object File -eq 'bad/broken-link.md')
    Assert-Equal 1 $s1Broken.Count 'S1 names the right file'
    # A suppressed prefix names a file from the root, so a link spelled parent-relative is tested
    # by its root-relative form, as S3 tests a backticked one.
    Assert-Equal 0 @($s1 | Where-Object { $_.File -eq 'bad/parent-relative.md' -and $_.Target -like '*vendorlib/*' }).Count `
        'S1 suppresses a parent-relative link to a file under a suppressed prefix'
    Assert-Equal 'no-such-file.md' $(if ($s1Broken.Count) { $s1Broken[0].Target } else { '<none>' }) 'S1 names the right target'

    # A single-line ```lang code``` span is inline code, not a fence delimiter -- GitHub
    # renders it that way because a fence info string cannot itself contain a backtick. The
    # bare '^\s*```' pattern cannot tell the difference and toggles fence state anyway,
    # silently un-scanning everything after it for the rest of the file.
    $fenceFinding = @($s1 | Where-Object File -eq 'bad/single-line-fence.md')
    Assert-Equal 1 $fenceFinding.Count `
        'a single-line triple-backtick span does not invert fence state -- the broken link after it is still found'
    Assert-Equal 'does-not-exist.md' $(if ($fenceFinding.Count) { $fenceFinding[0].Target } else { '<none>' }) `
        'S1 names the right target past the single-line span'

    # bad/dead-href.html pins S1 over HTML: one resolving and one dead relative href, an
    # in-page anchor and an https URL -- exactly the dead one is a finding.
    $s1Html = @(Get-HtmlHrefFindings -Root $FixtureDir -Docs @('bad/dead-href.html'))
    Assert-Equal 1 $s1Html.Count 'S1 over an .html fixture reports only the dead relative href'
    Assert-Equal 'S1' $(if ($s1Html.Count) { $s1Html[0].Signal } else { '<none>' }) 'an html href finding is S1, not a new signal'
    Assert-Equal 'no-such-page.md' $(if ($s1Html.Count) { $s1Html[0].Target } else { '<none>' }) 'S1 names the dead href target'
    Assert-Equal 'bad/dead-href.html' $(if ($s1Html.Count) { $s1Html[0].File } else { '<none>' }) 'S1 names the html file'

    Assert-Equal '3-ci-build-checks-in-constant-development-and-improvement' `
        (ConvertTo-GitHubSlug '3. CI Build Checks (in constant development and improvement)') `
        'slug drops punctuation and hyphenates'
    Assert-Equal $true ('a-real-heading' -ceq (ConvertTo-GitHubSlug 'A Real Heading')) 'slug lowercases'
    Assert-Equal 'status--result-codes' (ConvertTo-GitHubSlug 'Status / result codes') `
        'slug keeps a double hyphen when punctuation sits between two spaces'
    Assert-Equal 'forward-roadmap-encryption--remote-control' `
        (ConvertTo-GitHubSlug 'Forward roadmap: encryption + remote control') `
        'slug replaces each space independently, not per whitespace run'
    Assert-Equal $true ('some_heading' -ceq (ConvertTo-GitHubSlug 'Some_Heading')) 'slug preserves underscores'

    $s2 = @(Get-AnchorFindings -Root $FixtureDir -Docs $docs)
    Assert-Equal 3 $s2.Count 'S2 finds one dead anchor per bad fixture, plus the two mixed (planned) anchor lines'
    Assert-Equal 1 @($s2 | Where-Object File -eq 'bad/bad-anchor.md').Count 'S2 names the right file'
    # S2 reads the same line-global count as S1 and S3: a dead anchor beside an in-repo
    # parent-relative backtick path is one of two references, so (planned) suppresses neither.
    Assert-Equal 1 @($s2 | Where-Object File -eq 'bad/parent-relative.md').Count `
        'a dead anchor on a (planned) line is reported when a parent-relative in-repo path shares the line'

    # bad/planned-mixed-anchor.md pins the anchor half of the fix: a (planned) line naming a
    # broken link AND a broken self-anchor must still report BOTH -- a count that excludes
    # every `#...` target makes this line look like a lone reference
    # (or, for an anchor-only line, zero) and (planned) suppressed the anchor break too.
    Assert-Equal 1 @($s1 | Where-Object File -eq 'bad/planned-mixed-anchor.md').Count `
        'a (planned) line mixing a broken link and a broken self-anchor still reports the link (S1)'
    Assert-Equal 1 @($s2 | Where-Object File -eq 'bad/planned-mixed-anchor.md').Count `
        'a (planned) line mixing a broken link and a broken self-anchor still reports the anchor (S2)'

    # bad/html-comment.md pins comment-blindness: content inside <!-- --> is invisible to the
    # renderer, so links, paths, and headings there are neither findings nor RefCount
    # contributors -- while prose before an opener, after a closer, and around a stripped
    # inline comment IS still scanned.
    $hc = @($s1 | Where-Object File -eq 'bad/html-comment.md')
    Assert-Equal 2 $hc.Count 'only the two live links outside HTML comments are S1 findings'
    Assert-Equal 0 @($hc | Where-Object Target -match 'gone-').Count `
        'commented-out dead links are not findings'
    Assert-Equal $false ((Get-DocHeadingSlugs -FullPath (Join-Path $FixtureDir 'bad/html-comment.md')) -contains 'ghost-heading') `
        'a heading inside an HTML comment is not an anchor target'

    $targetSlugs = Get-DocHeadingSlugs -FullPath (Join-Path $FixtureDir 'good/target.md')
    Assert-Equal $false ($targetSlugs -contains 'not-a-real-heading') `
        'a heading-shaped line inside a fenced code block is not a valid anchor target'

    $ignore = @()
    Assert-Equal $true  (Test-Suppressed 'vendorlib/foo.c' 'see `vendorlib/foo.c`'   $ignore) 'sibling-repo prefix suppressed'
    Assert-Equal $true  (Test-Suppressed 'hostsite/x.php'  'see `hostsite/x.php`'    $ignore) 'external host suppressed'
    Assert-Equal $true  (Test-Suppressed 'a/b.md'          'see `a/b.md` (planned).' $ignore) 'planned marker suppressed (default RefCount=1)'
    Assert-Equal $false (Test-Suppressed 'a/b.md'          'see `a/b.md`.'           $ignore) 'unmarked missing ref not suppressed'

    # (planned) disambiguates to a single reference. On a line naming two, it is not clear
    # which one the author meant, so neither is suppressed -- a stale neighbor is never
    # silently hidden behind a genuinely aspirational one.
    Assert-Equal $true  (Test-Suppressed 'a/b.md' 'see `a/b.md` (planned).' $ignore 1) `
        'the sole reference on a (planned) line is suppressed'
    Assert-Equal $false (Test-Suppressed 'a/b.md' 'see `a/b.md` and `c/d.md` (planned).' $ignore 2) `
        'neither of two references on one (planned) line is suppressed'

    # Get-LineReferenceCount is what makes the disambiguation above true across signal types, not
    # just within one function's own candidate list. Before this, S1 and S3 each counted only
    # their own matches on a line, so a line combining a broken markdown link with a broken
    # backtick path looked like a lone reference to BOTH functions independently -- one (planned)
    # marker suppressed two unrelated real breaks.
    Assert-Equal 1 (Get-LineReferenceCount 'see `a/b.md` (planned).')                    'one backtick reference'
    Assert-Equal 1 (Get-LineReferenceCount 'see [link](a/b.md) (planned).')              'one markdown-link reference'
    Assert-Equal 2 (Get-LineReferenceCount 'see [link](a/b.md) and `c/d.md` (planned).') `
        'a link and a backtick path on the same line count together, not per signal type'

    # A `#anchor`-only target (no path) is an S2-only candidate; excluded from the count, a lone
    # self-anchor line scores RefCount=0 and a link+self-anchor line RefCount=1 (counting only the
    # link), either of which lets (planned) suppress an anchor break riding along the line.
    Assert-Equal 1 (Get-LineReferenceCount 'details in [this section](#nonexistent) (planned).') `
        'a lone self-anchor reference counts as one, not zero'
    Assert-Equal 2 (Get-LineReferenceCount 'see [future](a.md) (planned); details in [x](#nonexistent).') `
        'a link and a self-anchor on the same line count together'

    # A parent-relative backtick path counts when it resolves inside the root the caller names,
    # and not when it resolves outside it -- the same rule the S3 candidate filter applies.
    $docsDir = Join-Path $FixtureDir 'docs'
    Assert-Equal 1 (Get-LineReferenceCount 'see `../x.md` (planned).' -DocDir $docsDir -RootFull $FixtureDir) `
        'a parent-relative backtick path inside the root counts'
    Assert-Equal 0 (Get-LineReferenceCount 'see `../../x.md` (planned).' -DocDir $docsDir -RootFull $FixtureDir) `
        'a parent-relative backtick path outside the root does not count'

    $s3 = @(Get-CodePathFindings -Root $FixtureDir -Docs $docs -IgnoreTokens $ignore)
    Assert-Equal 2 $s3.Count 'S3 finds one missing path per bad fixture, plus the mixed (planned) line'
    Assert-Equal 0 @($s3 | Where-Object File -eq 'bad/html-comment.md').Count `
        'a backticked path inside an HTML comment is not an S3 finding'
    Assert-Equal 1 @($s3 | Where-Object File -eq 'bad/missing-path.md').Count 'S3 names the right file'
    # A suppressed prefix names a file by its root-relative path; the parent-relative spelling
    # of the same file is suppressed by that form, not reported by its own.
    Assert-Equal 0 @($s3 | Where-Object File -eq 'bad/parent-relative.md').Count `
        'a parent-relative spelling of a file under a suppressed prefix is suppressed'

    # bad/planned-mixed-line.md pins the fix: a (planned) line naming both a broken link and a
    # broken backtick path must still report BOTH -- the marker disambiguates to at most one
    # reference, and this line has two of different shapes.
    Assert-Equal 1 @($s1 | Where-Object File -eq 'bad/planned-mixed-line.md').Count `
        'a (planned) line mixing a broken link and a broken backtick path still reports the link (S1)'
    Assert-Equal 1 @($s3 | Where-Object File -eq 'bad/planned-mixed-line.md').Count `
        'a (planned) line mixing a broken link and a broken backtick path still reports the backtick path (S3)'

    # bad/code-span-path.html pins S3 over an HTML page, whose repo paths sit in <code> spans
    # because a page has no backticks to read. Every other rule is the markdown one: the
    # path-shape test, the unverifiable shapes, the suppressed prefix, resolution against the
    # tracked index from the root and from the page's own directory, and the (planned) marker's
    # one-reference disambiguation. Twelve spans, nine of them candidates, five findings.
    $s3Html = @(Get-CodePathFindings -Root $FixtureDir -Docs @('bad/code-span-path.html') -IgnoreTokens $ignore -Html)
    Assert-Equal 'src/html/does-not-exist.cs ../good/gone.md src/html/planned-a.cs src/html/planned-b.cs src/html/after-fence.cs' `
        (@($s3Html | ForEach-Object Target) -join ' ') `
        'S3 over an HTML page reports the missing <code> paths, root-relative and parent-relative, and none of the resolving, unverifiable, suppressed or singly-(planned) shapes'
    Assert-Equal 'S3' $(if ($s3Html.Count) { $s3Html[0].Signal } else { '<none>' }) `
        'an HTML code-span finding is S3, not a new signal'
    Assert-Equal 'bad/code-span-path.html' $(if ($s3Html.Count) { $s3Html[0].File } else { '<none>' }) `
        'S3 names the HTML page'
    Assert-Equal 3 $(if ($s3Html.Count) { $s3Html[0].Line } else { -1 }) `
        'S3 names the line the <code> span sits on'
    # A page is read line by line, not through Get-ProseLines: markdown's fence delimiter is
    # ordinary text on a page, and taking it for one would blind the rest of a file S7 never
    # scans.
    Assert-Equal 1 @($s3Html | Where-Object Target -eq 'src/html/after-fence.cs').Count `
        'a fence-shaped line on a page does not blind the <code> spans after it'

    # The two shapes do not cross: a <code> span in a markdown doc is raw HTML the renderer
    # shows, and a backtick on a page is ordinary text.
    Assert-Equal 0 @(Get-CodeSpanCandidates '<code>src/html/does-not-exist.cs</code>' -DocDir $FixtureDir -RootFull $FixtureDir).Count `
        'a <code> span is not a markdown S3 candidate'
    Assert-Equal 'src/html/does-not-exist.cs' `
        (@(Get-CodeSpanCandidates '<code>src/html/does-not-exist.cs</code>' -DocDir $FixtureDir -RootFull $FixtureDir -Html) -join ' ') `
        'a <code> span is an S3 candidate on an HTML page'
    Assert-Equal 0 @(Get-CodeSpanCandidates '`src/does/not/exist.cs`' -DocDir $FixtureDir -RootFull $FixtureDir -Html).Count `
        'a backtick span is not a candidate on an HTML page'

    # Get-LineReferenceCount reads the page shape too: counted by the markdown shapes alone, a
    # line naming two <code> paths scores zero and the (planned) marker suppresses both.
    Assert-Equal 2 (Get-LineReferenceCount '<p>see <code>a/b.md</code> and <code>c/d.md</code> (planned).</p>' -Html) `
        'two <code> paths on one line count as two references'

    # The ignore list reaches S3 on a page by both entry kinds -- an entry is the only way to
    # excuse a finding on a page the doc itself is right about.
    $s3HtmlTokenIgnored = @(Get-CodePathFindings -Root $FixtureDir -Docs @('bad/code-span-path.html') `
        -IgnoreTokens @('src/html/does-not-exist.cs') -Html)
    Assert-Equal 4 $s3HtmlTokenIgnored.Count 'S3 IgnoreTokens suppresses a matching <code> token on an HTML page'
    $s3HtmlFileIgnored = @(Get-CodePathFindings -Root $FixtureDir -Docs @('bad/code-span-path.html') `
        -IgnoreTokens $ignore -IgnoreFiles @('bad/code-span-path.html') -Html)
    Assert-Equal 0 $s3HtmlFileIgnored.Count 'S3 IgnoreFiles entry suppresses the whole HTML page'

    # bad/unclosed-fence.md pins S7: an opened fence with no closing delimiter must be reported
    # even though S1/S2/S3 -- scanning independently -- go blind the moment $inFence flips true
    # and never see it flip back. Three breaks are planted after the opening fence to confirm they
    # stay invisible to S1; S7 is the backstop that catches the file structurally, not a fix for
    # the swallow itself.
    $s7 = @(Get-UnclosedFenceFindings -Root $FixtureDir -Docs $docs)
    Assert-Equal 1 $s7.Count 'S7 finds the one fixture with an unclosed fence'
    Assert-Equal 'bad/unclosed-fence.md' $(if ($s7.Count) { $s7[0].File } else { '<none>' }) 'S7 names the right file'
    Assert-Equal 3 $(if ($s7.Count) { $s7[0].Line } else { -1 }) 'S7 names the line the fence opened on'
    Assert-Equal 0 @($s1 | Where-Object File -eq 'bad/unclosed-fence.md').Count `
        'S1 stays blind to content after an unclosed fence -- S7 is what catches this file'

    # bad/banned-reference.md pins S8: a substring from the banned table is a finding wherever
    # and however it appears -- plain URL, link, fence, or comment. The table ships EMPTY (Part 1
    # asserts that), so the fixture supplies its own.
    $banned = [ordered]@{ 'known-dead-host.fixture-repo.net' = 'renamed; Pages URLs do not follow renames' }
    $s8 = @(Get-BannedReferenceFindings -Root $FixtureDir -Docs $docs -Banned $banned)
    Assert-Equal 1 $s8.Count 'S8 finds the one fixture referencing a known-dead host'
    Assert-Equal 'bad/banned-reference.md' $(if ($s8.Count) { $s8[0].File } else { '<none>' }) 'S8 names the right file'

    # bad/work-remaining.md pins S10: a banned H1 title, a work-remaining H2, and a prose
    # checkbox (even with no space after the bracket) are findings; "Known limitations"
    # (current-state behavior boundaries) and fenced content are not.
    $s10 = @(Get-WorkRemainingFindings -Root $FixtureDir -Docs $docs)
    Assert-Equal 3 $s10.Count 'S10 finds the H1 title, the H2 heading, and the prose checkbox, nothing else'
    Assert-Equal 'bad/work-remaining.md' $(if ($s10.Count) { $s10[0].File } else { '<none>' }) 'S10 names the right file'
    Assert-Equal 1 @($s10 | Where-Object Target -eq 'Fixture roadmap').Count 'S10 flags a banned word in an H1 title'
    Assert-Equal 1 @($s10 | Where-Object Target -eq 'Remaining Work').Count 'S10 flags the work-remaining heading'
    Assert-Equal 1 @($s10 | Where-Object Target -match '^- \[ \]').Count 'S10 flags the no-space checkbox line'
    Assert-Equal 0 @($s10 | Where-Object Target -match 'limitations').Count 'Known limitations heading is not banned'
    $s10Exempt = @(Get-WorkRemainingFindings -Root $FixtureDir -Docs $docs -Exemptions @('^bad/work-remaining\.md$'))
    Assert-Equal 0 $s10Exempt.Count 'an S10 exemption pattern suppresses the whole file'

    # Get-IgnoreList splits `file:`-prefixed entries from bare tokens so a whole-document
    # exemption can never double as a repo-wide token-suffix suppression (or vice versa). A
    # `file:` entry must also resolve to exactly one in-scope doc -- 'some/doc.md' is supplied
    # here as the sole match.
    $tempIgnoreDir = Join-Path ([System.IO.Path]::GetTempPath()) "docs-freshness-ignore-test-$([guid]::NewGuid())"
    New-Item -ItemType Directory -Path $tempIgnoreDir | Out-Null
    try {
        Set-Content -LiteralPath (Join-Path $tempIgnoreDir '.docs-freshness-ignore') -Value @(
            'file: some/doc.md  # whole-file exemption'
            'legacy/token.cs  # token suppression'
        )
        $parsed = Get-IgnoreList -Root $tempIgnoreDir -Docs @('some/doc.md')
        Assert-Equal 1 @($parsed.Files).Count   'file: entry parses into Files'
        Assert-Equal 'some/doc.md' $(if (@($parsed.Files).Count) { $parsed.Files[0] } else { '<none>' }) `
            'Files entry has the file: prefix stripped'
        Assert-Equal 1 @($parsed.Tokens).Count  'bare entry parses into Tokens, not Files'
        Assert-Equal 'legacy/token.cs' $(if (@($parsed.Tokens).Count) { $parsed.Tokens[0] } else { '<none>' }) `
            'Tokens entry is the unprefixed line'
    } finally {
        Remove-Item -LiteralPath $tempIgnoreDir -Recurse -Force
    }

    # A `file:` entry is an unbounded suffix match (EndsWith) -- 'file: README.md' would
    # otherwise silently exempt every README in the repo from S1/S2/S3, and a dangling entry
    # (its doc moved or was deleted) would silently do nothing. Both must fail loudly at parse
    # time instead.
    $tempAmbigDir = Join-Path ([System.IO.Path]::GetTempPath()) "docs-freshness-ignore-ambiguous-$([guid]::NewGuid())"
    New-Item -ItemType Directory -Path $tempAmbigDir | Out-Null
    try {
        Set-Content -LiteralPath (Join-Path $tempAmbigDir '.docs-freshness-ignore') -Value 'file: README.md'
        $threw = $false
        $ambigMessage = ''
        try { Get-IgnoreList -Root $tempAmbigDir -Docs @('good/README.md', 'bad/README.md') | Out-Null }
        catch { $threw = $true; $ambigMessage = $_.Exception.Message }
        Assert-Equal $true $threw 'a file: entry matching 2+ in-scope docs throws instead of silently suppressing all of them'
        Assert-Equal $true ($ambigMessage -match 'README\.md') 'the ambiguity error names the entry'
    } finally {
        Remove-Item -LiteralPath $tempAmbigDir -Recurse -Force
    }

    $tempDanglingDir = Join-Path ([System.IO.Path]::GetTempPath()) "docs-freshness-ignore-dangling-$([guid]::NewGuid())"
    New-Item -ItemType Directory -Path $tempDanglingDir | Out-Null
    try {
        Set-Content -LiteralPath (Join-Path $tempDanglingDir '.docs-freshness-ignore') -Value 'file: no/such/doc.md'
        $threw = $false
        try { Get-IgnoreList -Root $tempDanglingDir -Docs @('good/other.md') | Out-Null }
        catch { $threw = $true }
        Assert-Equal $true $threw 'a file: entry matching no in-scope doc throws instead of silently doing nothing'
    } finally {
        Remove-Item -LiteralPath $tempDanglingDir -Recurse -Force
    }

    # The ignore list's whole-document exemption (S1/S2/S3) and its token-suffix suppression
    # (S1/S2/S3, via Test-Suppressed) are separate parameters -- a doc listed in IgnoreFiles
    # must not leak into IgnoreTokens and silently suppress unrelated tokens elsewhere.
    $s1Ignored = @(Get-MarkdownLinkFindings -Root $FixtureDir -Docs $docs -IgnoreFiles @('bad/broken-link.md'))
    Assert-Equal 0 @($s1Ignored | Where-Object File -eq 'bad/broken-link.md').Count `
        'S1 IgnoreFiles entry suppresses the whole file, not just a matching token'
    $s2Ignored = @(Get-AnchorFindings -Root $FixtureDir -Docs $docs -IgnoreFiles @('bad/bad-anchor.md'))
    Assert-Equal 0 @($s2Ignored | Where-Object File -eq 'bad/bad-anchor.md').Count `
        'S2 IgnoreFiles entry suppresses the whole file, not just a matching token'

    # -IgnoreTokens end-to-end through S1/S2/S3 -- only -IgnoreFiles is exercised above, and a
    # token entry is the form a consumer reaches for first.
    $s1TokenIgnored = @(Get-MarkdownLinkFindings -Root $FixtureDir -Docs $docs -IgnoreTokens @('no-such-file.md'))
    Assert-Equal 0 @($s1TokenIgnored | Where-Object File -eq 'bad/broken-link.md').Count `
        'S1 IgnoreTokens suppresses a matching link target'
    Assert-Equal 1 @($s1TokenIgnored | Where-Object File -eq 'bad/single-line-fence.md').Count `
        'S1 IgnoreTokens does not suppress an unrelated finding in another file'
    $s2TokenIgnored = @(Get-AnchorFindings -Root $FixtureDir -Docs $docs -IgnoreTokens @('good/target.md'))
    Assert-Equal 0 @($s2TokenIgnored | Where-Object File -eq 'bad/bad-anchor.md').Count `
        'S2 IgnoreTokens suppresses a matching anchor target by suffix'
    $s3TokenIgnored = @(Get-CodePathFindings -Root $FixtureDir -Docs $docs -IgnoreTokens @('src/does/not/exist.cs'))
    Assert-Equal 0 @($s3TokenIgnored | Where-Object File -eq 'bad/missing-path.md').Count `
        'S3 IgnoreTokens suppresses a matching backtick token'
    Assert-Equal 1 @($s3TokenIgnored | Where-Object File -eq 'bad/planned-mixed-line.md').Count `
        'S3 IgnoreTokens does not suppress an unrelated token in another file'

    # S3 carries the same `file:` whole-document exemption. Both S3-bearing bad fixtures are
    # listed -- bad/planned-mixed-line.md's finding is independent of the other's and would
    # otherwise leave a stray 1 behind.
    $s3Ignored = @(Get-CodePathFindings -Root $FixtureDir -Docs $docs -IgnoreTokens $ignore `
        -IgnoreFiles @('bad/missing-path.md', 'bad/planned-mixed-line.md'))
    Assert-Equal 0 $s3Ignored.Count 'S3 IgnoreFiles entry suppresses the whole file'

    # S9's URL extraction (the probing itself never runs in tests -- fixtures are outside the
    # live sweep's scope, and Test-UrlDead is exercised only by the weekly sweep).
    $urls = @(Get-ExternalUrlOccurrences -Root $FixtureDir -Docs $docs)
    Assert-Equal 1 @($urls | Where-Object Url -eq 'https://fixture-dead-host.fixture-repo.net/some/path').Count `
        'trailing punctuation is trimmed from an extracted URL'
    Assert-Equal 0 @($urls | Where-Object { $_.Url -match 'example\.com|localhost|docs\.local' }).Count `
        'exempt hosts (placeholders, localhost, reserved TLDs) are not extracted'
    Assert-Equal 0 @($urls | Where-Object Url -match 'fenced-host').Count `
        'a URL inside a code fence is not extracted'

    # S4's "must this doc be indexed?" rule, against a SYNTHETIC index convention: two indexed
    # trees and four exemption regexes, none of them this repo's or any consumer's. The rule is
    # in-tree AND not exempt -- so a doc outside every declared tree is never index-required,
    # however it is named.
    $idxTrees  = @('src/', 'lib/')
    $idxExempt = @('(^|/)CHANGELOG', '/tests/', 'api/generated/', '^docs/')
    Assert-Equal $true  (Test-IndexRequired 'src/motion/README.md'          -Trees $idxTrees -Exemptions $idxExempt) 'a doc in an indexed tree is index-required'
    Assert-Equal $true  (Test-IndexRequired 'lib/core/overview.md'          -Trees $idxTrees -Exemptions $idxExempt) 'every declared tree arms the rule, not just the first'
    Assert-Equal $false (Test-IndexRequired 'src/widget/CHANGELOG.md'       -Trees $idxTrees -Exemptions $idxExempt) 'CHANGELOG exempt'
    Assert-Equal $false (Test-IndexRequired 'src/tests/fixtures/x.md'       -Trees $idxTrees -Exemptions $idxExempt) 'tests tree exempt'
    Assert-Equal $false (Test-IndexRequired 'lib/api/generated/index.md'    -Trees $idxTrees -Exemptions $idxExempt) 'generated tree exempt'
    Assert-Equal $false (Test-IndexRequired 'docs/guide/getting-started.md' -Trees $idxTrees -Exemptions $idxExempt) 'a doc outside every indexed tree is not index-required'
    Assert-Equal $false (Test-IndexRequired 'tools/README.md'               -Trees $idxTrees -Exemptions $idxExempt) 'an unlisted top-level tree is not index-required'
}
finally {
    Pop-Location
    if (Test-Path -LiteralPath $FixtureDir) { Remove-Item -LiteralPath $FixtureDir -Recurse -Force }
}

# =======================================================================================
# Part 3 -- a bound repo at a non-ASCII path, run with no -RepoRoot under an OEM console code
# page. git prints the root as UTF-8 and PowerShell decodes it with [Console]::OutputEncoding:
# under code page 437 the root names no directory, so the gate reports a bound repo as having
# no binding and then throws resolving the root. The caller's encoding must come back unchanged.
# =======================================================================================
$UniRepo = Join-Path ([System.IO.Path]::GetTempPath()) ("docsfresh-r$([char]0xE9)po-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$made = try { New-Item -ItemType Directory -Force -Path (Join-Path $UniRepo '.claude') | Out-Null; $true } catch { $false }
if (-not $made) {
    Write-Host "  skip: cannot create a directory named with U+00E9 under $([System.IO.Path]::GetTempPath())" -ForegroundColor DarkGray
} else {
    $callerEncoding = [Console]::OutputEncoding
    Push-Location -LiteralPath $UniRepo
    try {
        git init -q . 2>$null
        Set-Content -LiteralPath (Join-Path $UniRepo '.claude/ouro.toml') -Value 'schema = 1' -Encoding utf8
        Set-Content -LiteralPath (Join-Path $UniRepo 'real.md') -Value '# Real' -Encoding utf8
        git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m fixture 2>$null
        [Console]::OutputEncoding = [System.Text.Encoding]::GetEncoding(437)
        # Streamed, not assigned inside the try: the INFO line comes before the throw, and an
        # assignment the throw interrupts would drop it.
        $out = & { try { & $Gate 6>&1 } catch { "threw: $_" } } | Out-String
        $after = [Console]::OutputEncoding.CodePage
    }
    finally {
        [Console]::OutputEncoding = $callerEncoding
        Pop-Location
        Remove-Item -LiteralPath $UniRepo -Recurse -Force
    }
    if (Assert-GateRan $out 'the gate runs under code page 437 in a bound repo at a non-ASCII path') {
        Assert-NoMatch 'not read \(no ' $out 'a bound repo at a non-ASCII path is not reported as unbound under code page 437'
        Assert-Match 'total: 0 finding\(s\) across 1 docs' $out 'the gate resolves the root and scans the repo'
    }
    Assert-Equal 437 $after 'the caller''s console output encoding is unchanged by the gate'
}

# =======================================================================================
# Part 4 -- a binding whose docs.index_path is non-ASCII, read under an OEM console code page.
# ouro-binding.py prints UTF-8 and PowerShell decodes it with [Console]::OutputEncoding: under
# code page 437 the path names no file, and S4 disarms without a word. The index lists a file
# that does not exist, so an armed S4 reports S4a. The caller's encoding must come back unchanged.
# =======================================================================================
$IdxRepo = Join-Path ([System.IO.Path]::GetTempPath()) ("docsfresh-idx-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$IdxRel = "docs/index-$([char]0x6587).md"
$made = try {
    New-Item -ItemType Directory -Force -Path (Join-Path $IdxRepo '.claude'), (Join-Path $IdxRepo 'docs') | Out-Null
    Set-Content -LiteralPath (Join-Path $IdxRepo $IdxRel) -Value '# Index', '', '- `src/gone.md`' -Encoding utf8
    $true
} catch { $false }
if (-not $made) {
    Write-Host "  skip: cannot create a file named with U+6587 under $IdxRepo" -ForegroundColor DarkGray
    if (Test-Path -LiteralPath $IdxRepo) {
        try { Remove-Item -LiteralPath $IdxRepo -Recurse -Force -ErrorAction Stop }
        catch { Write-Warning "could not remove ${IdxRepo}: $($_.Exception.Message)" }
    }
} else {
    $callerEncoding = [Console]::OutputEncoding
    Push-Location -LiteralPath $IdxRepo
    try {
        git init -q . 2>$null
        Set-Content -LiteralPath (Join-Path $IdxRepo '.claude/ouro.toml') -Value 'schema = 1', '[docs]', "index_path = `"$IdxRel`"", 'indexed_trees = ["src/"]' -Encoding utf8
        git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m fixture 2>$null
        [Console]::OutputEncoding = [System.Text.Encoding]::GetEncoding(437)
        $out = & { try { & $Gate 6>&1 } catch { "threw: $_" } } | Out-String
        $after = [Console]::OutputEncoding.CodePage
    }
    finally {
        [Console]::OutputEncoding = $callerEncoding
        Pop-Location
        Remove-Item -LiteralPath $IdxRepo -Recurse -Force
    }
    if (Assert-GateRan $out 'the gate runs under code page 437 with a non-ASCII docs.index_path') {
        Assert-Match 'S4a' $out 'a non-ASCII docs.index_path read under code page 437 arms S4 (the index lists a missing file)'
    }
    Assert-Equal 437 $after 'the caller''s console output encoding is unchanged by the binding reads'
}

# =======================================================================================
# Part 5 -- a repo that declares a binding, run with no python3 on PATH. The [docs] table names
# which signals block, so a run on the defaults applies a policy the repo did not declare: the
# gate stops instead. A repo with no binding keeps running on the defaults with its INFO line.
# =======================================================================================
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

$NoPyRepo = Join-Path ([System.IO.Path]::GetTempPath()) ("docsfresh-nopy-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$savedPath = $env:PATH
$realPython3ForShim = (Get-Command python3 -ErrorAction SilentlyContinue).Source
$realGitForShim = (Get-Command git -ErrorAction SilentlyContinue).Source
$gitLinkDirForShim = $null
try {
    New-Item -ItemType Directory -Force -Path (Join-Path $NoPyRepo '.claude'), (Join-Path $NoPyRepo 'docs') | Out-Null
    $bindingPath = Join-Path $NoPyRepo '.claude/ouro.toml'
    Set-Content -LiteralPath $bindingPath -Value 'schema = 1', '[docs]', 'report_only = ["S9"]' -Encoding utf8
    Set-Content -LiteralPath (Join-Path $NoPyRepo 'docs/note.md') -Value '# Note', '', "See ``docs/gone.md`` for the rest." -Encoding utf8
    Push-Location -LiteralPath $NoPyRepo
    git init -q . 2>$null
    git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m fixture 2>$null
    Pop-Location

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

    $env:PATH = Get-PathWithoutPython3
    # git can share python3's own directory (both in /usr/bin on Linux), which the scrub above
    # then drops for free -- a scratch link keeps it reachable so the no-binding case below runs
    # instead of skipping past git going missing along with python3.
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
        # -RepoRoot, so resolving the root needs no git: the stop comes before any scan.
        $out = & { try { & $Gate -RepoRoot $NoPyRepo 6>&1 } catch { "threw: $_" } } | Out-String
        Assert-Match '(?m)^threw: ' $out 'a declared binding with no python3 on PATH stops the run'
        Assert-Match '(?s)threw: .*ouro\.toml.*python3 3\.11\+' $out `
            'the stop names the binding file and the python3 floor a repo with a binding needs'

        # A working `python`, in its own directory rather than python3's, must not let the read
        # through either.
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
                    $outPyOnly = & { try { & $Gate -RepoRoot $NoPyRepo 6>&1 } catch { "threw: $_" } } | Out-String
                    Assert-Match '(?m)^threw: ' $outPyOnly 'a working python with no python3 still stops the run'
                    Assert-Match '(?s)threw: .*ouro\.toml.*python3 3\.11\+' $outPyOnly `
                        'the stop names python3, not the python that is actually on PATH'
                }
                else { Write-Host '  skip: the python shim is not a usable interpreter on this machine' -ForegroundColor DarkGray }
            }
            finally { $env:PATH = $pyOnlySaved }
        }
        finally { Remove-Item -LiteralPath $pyOnlyDir -Recurse -Force }

        # The same tree with no binding at all is the onboarding path, and it still runs.
        if ($gitStillThere) {
            Remove-Item -LiteralPath $bindingPath -Force
            $out = & { try { & $Gate -RepoRoot $NoPyRepo 6>&1 } catch { "threw: $_" } } | Out-String
            $code = $LASTEXITCODE
            if (Assert-GateRan $out 'a repo with no binding and no python3 runs on the defaults') {
                Assert-Match 'not read \(no .*ouro\.toml\): running on defaults' $out `
                    'no binding at all is still the INFO line, not a stop'
                Assert-Equal 0 $code 'no binding at all still exits 0 on a report-only signal'
            }
        }
        else { Write-Host '  skip: git is not reachable with python3 off PATH on this machine' -ForegroundColor DarkGray }
    }
}
finally {
    $env:PATH = $savedPath
    if ($gitLinkDirForShim) { Remove-Item -LiteralPath $gitLinkDirForShim -Recurse -Force }
    if (Test-Path -LiteralPath $NoPyRepo) { Remove-Item -LiteralPath $NoPyRepo -Recurse -Force }
}

# =======================================================================================
# Part 6 -- an HTML page in scope, through the gate itself. S3 reads its <code> spans, and a
# `file:` entry naming it is a valid exemption: entries are validated against the in-scope doc
# list at parse time, so a list that omits the pages makes the only escape hatch from a finding
# on one a parse error that stops the whole run. -HtmlGlobs is passed, so this needs no binding
# and no python.
# =======================================================================================
$HtmlRepo = Join-Path ([System.IO.Path]::GetTempPath()) ("docsfresh-html-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
try {
    New-Item -ItemType Directory -Force -Path (Join-Path $HtmlRepo 'docs') | Out-Null
    Set-Content -LiteralPath (Join-Path $HtmlRepo 'docs/real.md') -Value '# Real' -Encoding utf8
    Set-Content -LiteralPath (Join-Path $HtmlRepo 'docs/page.html') -Value @(
        '<p>resolves: <code>docs/real.md</code></p>'
        '<p>MISSING: <code>docs/gone.md</code></p>') -Encoding utf8
    # A page cannot write a placeholder unescaped -- a literal < inside <code> is a tag -- so the
    # escaped spelling has to reach the skip class that drops its markdown twin; and an escaped
    # ampersand has to resolve to the name on disk.
    Set-Content -LiteralPath (Join-Path $HtmlRepo 'docs/a&b.md') -Value '# Amp' -Encoding utf8
    Add-Content -LiteralPath (Join-Path $HtmlRepo 'docs/page.html') -Value @(
        '<p>placeholder: <code>src/&lt;area&gt;/notes.md</code></p>'
        '<p>escaped ampersand: <code>docs/a&amp;b.md</code></p>'
        '<p>see <a href="gone-link.md">x</a> and <code>docs/gone-beside.md</code> (planned).</p>') -Encoding utf8
    # The exempted page carries a dead href as well: a file: entry clears S3 on a page and not
    # S1, which is the documented split, and the row says so rather than leaving it to be found.
    Set-Content -LiteralPath (Join-Path $HtmlRepo 'docs/exempt.html') -Value @(
        '<p>MISSING: <code>docs/also-gone.md</code></p>'
        '<p>dead link: <a href="also-gone-link.md">x</a></p>') -Encoding utf8
    Set-Content -LiteralPath (Join-Path $HtmlRepo '.docs-freshness-ignore') `
        -Value 'file: docs/exempt.html  # the page is shown, not referenced' -Encoding utf8
    Push-Location -LiteralPath $HtmlRepo
    git init -q . 2>$null
    git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m fixture 2>$null
    Pop-Location

    $out = & { try { & $Gate -RepoRoot $HtmlRepo -HtmlGlobs 'docs/*.html' 6>&1 } catch { "threw: $_" } } | Out-String
    if (Assert-GateRan $out 'a file: entry naming an in-scope HTML page parses instead of stopping the run') {
        Assert-Match 'docs/page\.html:2\s+-> docs/gone\.md\s+\(<code> repo path does not exist\)' $out `
            'S3 reports the dead <code> path on an in-scope HTML page, with the reason its shape earns'
        Assert-NoMatch 'docs/real\.md' $out 'the resolving <code> path on that page is not a finding'
        Assert-NoMatch 'also-gone\.md' $out 'the exempted page contributes no S3 finding'
        Assert-Match 'also-gone-link\.md' $out `
            'while its dead href is still reported: a file: entry clears S3 on a page, not S1'
        Assert-NoMatch 'area' $out 'an escaped placeholder in a <code> span is skipped, as its markdown twin is'
        Assert-NoMatch 'a&b' $out 'an escaped ampersand resolves to the file on disk'
        Assert-Match 'gone-beside\.md' $out `
            'a (planned) line carrying a dead href and a dead <code> path reports the path, the href being the second reference'
    }

    # A glob that also names a markdown doc hands the same file to the ignore list twice, which
    # reads as ambiguous and stops the run; and a page under an exclusion is out of scope for
    # every signal that reads it, not only for the ones that read a doc.
    $both = & { try { & $Gate -RepoRoot $HtmlRepo -HtmlGlobs 'docs/*' 6>&1 } catch { "threw: $_" } } | Out-String
    if (Assert-GateRan $both 'a glob naming a markdown doc as well does not stop the run') {
        Assert-Equal 1 ([regex]::Matches($both, 'docs/gone\.md').Count) `
            'and the doc it names twice is scanned once'
    }
    # The excluded tree holds a page of its own, so the exemption above still names a doc that is
    # in scope: excluding the tree that holds an exempted page makes the entry stale, which is the
    # documented rule and a different case from this one.
    New-Item -ItemType Directory -Force -Path (Join-Path $HtmlRepo 'docs/gen') | Out-Null
    Set-Content -LiteralPath (Join-Path $HtmlRepo 'docs/gen/built.html') `
        -Value '<p>MISSING: <code>docs/generated-gone.md</code></p>' -Encoding utf8
    Push-Location -LiteralPath $HtmlRepo
    git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m generated 2>$null
    Pop-Location
    $excluded = & { try { & $Gate -RepoRoot $HtmlRepo -HtmlGlobs @('docs/*.html', 'docs/gen/*.html') -ScopeExclusions '/gen/' 6>&1 } catch { "threw: $_" } } | Out-String
    if (Assert-GateRan $excluded 'an excluded tree leaves the gate running') {
        Assert-NoMatch 'generated-gone' $excluded 'a page under an excluded tree is not scanned'
    }
}
finally {
    if (Test-Path -LiteralPath $HtmlRepo) { Remove-Item -LiteralPath $HtmlRepo -Recurse -Force }
}

# A repo with no doc and no page in scope is the one state where both halves of the ignore
# list's argument are empty. Joined without keeping an array they collapse to nothing, and the
# entry check then iterates once over null and dies on a member of it, where it should name the
# stale entry. Nothing else in this suite reaches that state.
$EmptyRepo = Join-Path ([System.IO.Path]::GetTempPath()) ("docsfresh-empty-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
try {
    New-Item -ItemType Directory -Force -Path $EmptyRepo | Out-Null
    Set-Content -LiteralPath (Join-Path $EmptyRepo 'notes.txt') -Value 'not a doc' -Encoding utf8
    Set-Content -LiteralPath (Join-Path $EmptyRepo '.docs-freshness-ignore') `
        -Value 'file: docs/moved.md  # stale entry' -Encoding utf8
    Push-Location -LiteralPath $EmptyRepo
    git init -q . 2>$null
    git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m fixture 2>$null
    Pop-Location

    $empty = & { try { & $Gate -RepoRoot $EmptyRepo -HtmlGlobs 'site/*.html' 6>&1 } catch { "threw: $_" } } | Out-String
    Assert-Match 'resolves to no in-scope doc' $empty `
        'with no doc and no page in scope, a stale file: entry is named rather than dying on a member of nothing'
}
finally {
    if (Test-Path -LiteralPath $EmptyRepo) { Remove-Item -LiteralPath $EmptyRepo -Recurse -Force }
}

# =======================================================================================
# Part 7 -- the doc index names an HTML page. The entry parser, S4a's resolution set and S4b's
# iteration each have to take a page as well as a markdown doc: accepting the entry alone would
# report a page that is present as resolving to nothing, since S4a resolved against markdown.
# =======================================================================================
$IndexRepo = Join-Path ([System.IO.Path]::GetTempPath()) ("docsfresh-index-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$savedGlobs = $HtmlGlobs
try {
    New-Item -ItemType Directory -Force -Path (Join-Path $IndexRepo 'docs'), (Join-Path $IndexRepo 'site'), (Join-Path $IndexRepo 'src') | Out-Null
    $bt = [char]96
    Set-Content -LiteralPath (Join-Path $IndexRepo 'docs/index.md') -Encoding utf8 -Value @(
        '# Index', ''
        "- ${bt}docs/real.md${bt} -- a markdown doc that exists"
        "- ${bt}site/page.html${bt} -- a page that exists"
        "- ${bt}site/gone.html${bt} -- a page that does not")
    Set-Content -LiteralPath (Join-Path $IndexRepo 'docs/real.md') -Value '# Real' -Encoding utf8
    Set-Content -LiteralPath (Join-Path $IndexRepo 'site/page.html') -Value '<p>page</p>' -Encoding utf8
    Set-Content -LiteralPath (Join-Path $IndexRepo 'src/code.cs') -Value '// code' -Encoding utf8
    Set-Content -LiteralPath (Join-Path $IndexRepo 'src/notes.html') -Value '<p>beside code, unindexed</p>' -Encoding utf8
    Set-Content -LiteralPath (Join-Path $IndexRepo 'src/style.css') -Value 'p {}' -Encoding utf8
    Push-Location -LiteralPath $IndexRepo
    git init -q . 2>$null
    git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m fixture 2>$null
    Pop-Location

    # src/* is deliberately broad: it reaches the stylesheet beside the page, which no entry can
    # name, so it must not become an S4b finding.
    $HtmlGlobs = @('site/*.html', 'src/*')
    $idx = @(Get-IndexFindings -Root $IndexRepo -Index 'docs/index.md' -Trees @('src/'))
    $s4a = @($idx | Where-Object Signal -eq 'S4a' | ForEach-Object Target)
    $s4b = @($idx | Where-Object Signal -eq 'S4b' | ForEach-Object File)
    Assert-Equal $true ($s4a -contains 'site/gone.html') 'S4a reports an indexed page that does not exist'
    Assert-Equal $false ($s4a -contains 'site/page.html') 'and not an indexed page that does, which a markdown-only resolution set would'
    Assert-Equal $false ($s4a -contains 'docs/real.md') 'and still not an indexed markdown doc that exists'
    Assert-Equal $true ($s4b -contains 'src/notes.html') 'S4b reports a page beside code that no entry covers'
    Assert-Equal $false ($s4b -contains 'src/style.css') 'and not a stylesheet a broad glob reaches, which no entry could name'

    # With no doc and no page in scope, every entry resolves to nothing and each is reported,
    # the index itself being read by path rather than as an in-scope doc.
    $HtmlGlobs = @()
    $savedExcl = $ScopeExclusions
    try {
        $ScopeExclusions = @('/docs/')
        $empty = & { try { @(Get-IndexFindings -Root $IndexRepo -Index 'docs/index.md' -Trees @('nothing/')) } catch { "threw: $_" } }
    }
    finally { $ScopeExclusions = $savedExcl }
    Assert-Equal $false ((@($empty) -join ' ') -match '^threw: ') 'an index read against an empty scope runs to the end'
    Assert-Equal 3 @($empty | Where-Object { $_ -isnot [string] -and $_.Signal -eq 'S4a' }).Count 'and every entry resolves to nothing there'
}
finally {
    $HtmlGlobs = $savedGlobs
    if (Test-Path -LiteralPath $IndexRepo) { Remove-Item -LiteralPath $IndexRepo -Recurse -Force }
}

# =======================================================================================
# Part 8 -- S2 tests a suppressed prefix against a parent-relative link's root-relative form.
# S2 returns before suppression can matter unless the link target is tracked, and the fixture
# tree contains neither suppressed prefix, so this row needs a repo of its own that does.
# =======================================================================================
$SuppRepo = (New-Item -ItemType Directory -Path (Join-Path ([System.IO.Path]::GetTempPath()) ("docsfresh-supp-" + [guid]::NewGuid().ToString('N').Substring(0, 8)))).FullName
$savedPrefixes = $SuppressPrefixes
try {
    New-Item -ItemType Directory -Path (Join-Path $SuppRepo 'docs'), (Join-Path $SuppRepo 'vendorlib'), (Join-Path $SuppRepo 'own') | Out-Null
    Set-Content -LiteralPath (Join-Path $SuppRepo 'vendorlib/lib.md') -Value '# Lib' -Encoding utf8
    Set-Content -LiteralPath (Join-Path $SuppRepo 'own/page.md') -Value '# Page' -Encoding utf8
    Set-Content -LiteralPath (Join-Path $SuppRepo 'docs/a.md') -Encoding utf8 -Value @(
        '# A', ''
        'Under the prefix: [lib](../vendorlib/lib.md#nowhere).', ''
        'Outside it: [page](../own/page.md#nowhere).')
    Push-Location -LiteralPath $SuppRepo
    git init -q . 2>$null
    git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m fixture 2>$null
    Pop-Location

    $SuppressPrefixes = @('vendorlib/')
    $s2Supp = @(Get-AnchorFindings -Root $SuppRepo -Docs @('docs/a.md') | ForEach-Object Target)
    Assert-Equal $false ($s2Supp -contains '../vendorlib/lib.md#nowhere') 'S2 suppresses a parent-relative link to a tracked file under a suppressed prefix'
    # The control: the same shape outside the prefix is reported, so the repo reaches S2 at all.
    Assert-Equal $true ($s2Supp -contains '../own/page.md#nowhere') 'and still reports the same dead anchor outside the prefix'
}
finally {
    $SuppressPrefixes = $savedPrefixes
    Remove-Item -LiteralPath $SuppRepo -Recurse -Force
}

# =======================================================================================
# Part 9 -- S1 over an HTML page tests a suppressed prefix too, both spellings, as S1 over
# markdown already does above. Get-HtmlHrefFindings needs a repo of its own: the shared
# fixture tree carries no suppressed prefix.
# =======================================================================================
$HtmlSuppRepo = (New-Item -ItemType Directory -Path (Join-Path ([System.IO.Path]::GetTempPath()) ("docsfresh-htmlsupp-" + [guid]::NewGuid().ToString('N').Substring(0, 8)))).FullName
$savedHtmlPrefixes = $SuppressPrefixes
try {
    New-Item -ItemType Directory -Path (Join-Path $HtmlSuppRepo 'docs') | Out-Null
    Set-Content -LiteralPath (Join-Path $HtmlSuppRepo 'root.html') -Encoding utf8 -Value @(
        '<html><body>'
        '<a href="vendorlib/missing.md">lib</a>'
        '<a href="own/missing.md">own</a>'
        '</body></html>')
    Set-Content -LiteralPath (Join-Path $HtmlSuppRepo 'docs/p.html') -Encoding utf8 -Value @(
        '<html><body>'
        '<a href="../vendorlib/missing.md">lib</a>'
        '<a href="../other/missing.md">other</a>'
        '</body></html>')
    Push-Location -LiteralPath $HtmlSuppRepo
    git init -q . 2>$null
    git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m fixture 2>$null
    Pop-Location

    $SuppressPrefixes = @('vendorlib/')
    $htmlSupp = @(Get-HtmlHrefFindings -Root $HtmlSuppRepo -Docs @('root.html', 'docs/p.html') | ForEach-Object Target)
    Assert-Equal $false ($htmlSupp -contains 'vendorlib/missing.md') 'S1 over HTML suppresses a root-relative href under a suppressed prefix'
    Assert-Equal $false ($htmlSupp -contains '../vendorlib/missing.md') 'and a parent-relative href resolving under it too'
    Assert-Equal $true ($htmlSupp -contains 'own/missing.md') 'and still reports a dead href outside the prefix'
    Assert-Equal $true ($htmlSupp -contains '../other/missing.md') 'and still reports a dead parent-relative href outside the prefix'
}
finally {
    $SuppressPrefixes = $savedHtmlPrefixes
    Remove-Item -LiteralPath $HtmlSuppRepo -Recurse -Force
}

# =======================================================================================
# Part 10 -- a link or href starting with a single slash: markdown resolves it against the repo
# root at S1 and S2, an HTML href names a site root and is skipped, and a target starting with
# two slashes is skipped in both. Ruling 2026-09-26.
# =======================================================================================
$RootRepo = (New-Item -ItemType Directory -Path (Join-Path ([System.IO.Path]::GetTempPath()) ("docsfresh-rootrel-" + [guid]::NewGuid().ToString('N').Substring(0, 8)))).FullName
$savedRootPrefixes = $SuppressPrefixes
try {
    New-Item -ItemType Directory -Path (Join-Path $RootRepo 'docs'), (Join-Path $RootRepo 'own') | Out-Null
    Set-Content -LiteralPath (Join-Path $RootRepo 'own/page.md') -Value '# Page' -Encoding utf8
    Set-Content -LiteralPath (Join-Path $RootRepo 'docs/a.md') -Encoding utf8 -Value @(
        '# A', ''
        'Matching anchor: [page](/own/page.md#page).', ''
        'Missing anchor: [page](/own/page.md#nope).', ''
        'Missing file: [gone](/own/missing.md#x).', ''
        'Two slashes: [cdn](//cdn.example.com/x.js).')
    Set-Content -LiteralPath (Join-Path $RootRepo 'docs/p.html') -Encoding utf8 -Value @(
        '<html><body>'
        '<a href="/own/missing.md">gone</a>'
        '<a href="//cdn.example.com/x.js">cdn</a>'
        '<p>see <a href="/own/missing.md">x</a> and <code>docs/gone-beside.md</code> (planned).</p>'
        '<p>see <a href="//cdn.example.com/x.js">y</a> and <code>docs/gone-two.md</code> (planned).</p>'
        '<p>alone <code>docs/gone-alone.md</code> here.</p>'
        '</body></html>')
    Push-Location -LiteralPath $RootRepo
    git init -q . 2>$null
    git add -A 2>$null; git -c user.email=t@t -c user.name=t commit -q -m fixture 2>$null
    Pop-Location

    $s1Root = @(Get-MarkdownLinkFindings -Root $RootRepo -Docs @('docs/a.md') | ForEach-Object Target)
    Assert-Equal $true ($s1Root -contains '/own/missing.md#x') 'S1 reports a root-spelled link to a file that does not exist'
    Assert-Equal $false ($s1Root -contains '/own/page.md#page') 'and not a root-spelled link to a file that exists, with a matching anchor'
    Assert-Equal $false ($s1Root -contains '/own/page.md#nope') 'and not the same file with a missing anchor -- that is S2''s job'
    Assert-Equal $false ($s1Root -contains '//cdn.example.com/x.js') 'and not a two-slash target, which names another host'

    $s2Root = @(Get-AnchorFindings -Root $RootRepo -Docs @('docs/a.md') | ForEach-Object Target)
    Assert-Equal $true ($s2Root -contains '/own/page.md#nope') 'S2 reports a root-spelled link whose anchor has no matching heading'
    Assert-Equal $false ($s2Root -contains '/own/page.md#page') 'and not the same file with a matching anchor'
    Assert-Equal $false ($s2Root -contains '/own/missing.md#x') 'and not a root-spelled link to a file that does not exist -- that is S1''s job'

    $SuppressPrefixes = @('own/')
    $s1Supp = @(Get-MarkdownLinkFindings -Root $RootRepo -Docs @('docs/a.md') | ForEach-Object Target)
    Assert-Equal $false ($s1Supp -contains '/own/missing.md#x') 'a suppressed prefix naming the root-relative form suppresses the root-spelled missing file'
    $SuppressPrefixes = @()

    $htmlRoot = @(Get-HtmlHrefFindings -Root $RootRepo -Docs @('docs/p.html'))
    Assert-Equal 0 $htmlRoot.Count 'S1 over HTML skips a root-spelled href and a two-slash href alike'
    # The line's reference count reads hrefs as S1 does: a / or // href S1 skips is no second
    # reference, so a (planned) marker beside it still covers the lone <code> path on that line.
    $s3Root = @(Get-CodePathFindings -Root $RootRepo -Docs @('docs/p.html') -IgnoreTokens @() -IgnoreFiles @() -Html | ForEach-Object Target)
    Assert-Equal $true ($s3Root -contains 'docs/gone-alone.md') 'the same page reports a lone missing <code> path, so the two rows below are not vacuous'
    Assert-Equal $false ($s3Root -contains 'docs/gone-beside.md') 'a (planned) code path beside a root-spelled href is still suppressed'
    Assert-Equal $false ($s3Root -contains 'docs/gone-two.md') 'and beside a two-slash href too'
}
finally {
    $SuppressPrefixes = $savedRootPrefixes
    Remove-Item -LiteralPath $RootRepo -Recurse -Force
}

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall docs-freshness cases pass" -ForegroundColor Green
exit 0
