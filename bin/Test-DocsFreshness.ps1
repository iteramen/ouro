<#
.SYNOPSIS
    Deterministic documentation-rot detection. Repo-agnostic: every repo-specific
    table is a parameter, and the shipped defaults name no repository.
.DESCRIPTION
    Gate mode exits 1 on a blocking finding. Sweep mode adds S9 (external-URL
    liveness, network) and exits 0 on any finding. Both stop before the scan when a
    binding read this gate must make fails: a repo that declares a binding and has
    ouro-binding.py beside this script needs python3 3.11+ on PATH.

    Every repo-specific value is read from the binding's [docs] table unless the caller
    passed it explicitly, in which case the parameter wins. A key the binding does not
    declare falls back to the default below, and a repo with no binding, or a vendored tree
    with no ouro-binding.py beside this script, runs entirely on the defaults with an INFO
    line saying which -- that is what keeps onboarding at one gate line and zero config. A
    repo that has a binding and the tool, with no python3 on PATH, is a configuration error
    and stops the run naming it, as a python3 older than 3.11 already does: the first binding
    read throws with ouro-binding.py's Python 3.11+ message. Note an empty list in
    [docs] means EMPTY, not "use the default": declaring `exclude = []` scans everything.

    Signals: S1 dead markdown link / HTML href, S2 dead heading anchor, S3 dead
    path in a code span (backticks in markdown, <code> in an HTML page), S4a index
    entry, markdown or HTML, resolving to nothing, S4b indexed-tree doc or in-scope
    page missing from the index, S7
    unclosed code fence, S8 known-dead reference, S9 dead external URL, S10
    work-remaining content in a doc.

    S4 arms only when both -IndexPath and -IndexedTrees are given; S8 only when
    -BannedSubstrings is non-empty; HTML scanning only when -HtmlGlobs is given.
    Unconfigured means the signal is silent, not that it fails open.

    Which signals block is -ReportOnlySignals. It defaults to S9, S3 and S10 so a
    repo can adopt the gate before it is clean, then narrow the list as it ratchets.
.PARAMETER Mode
    Gate (default) exits 1 on a blocking finding. Sweep adds S9 and exits 0 on any.
    A failed binding read stops either before the scan.
.PARAMETER RepoRoot
    Repo to scan. Defaults to the git work tree of the working directory.
.PARAMETER PathExtensions
    Extensions that make a code-span token path-shaped for S3.
.PARAMETER ScopeExclusions
    Substrings matched against "/<relative path>"; a doc matching any is not scanned.
.PARAMETER SuppressPrefixes
    Path prefixes whose references are never reported (another repo, build output).
    Empty by default -- a consumer's list must not ship as a plugin default.
.PARAMETER DocFxApiPattern
    Regex for a generated API-doc tree to exclude. Empty disables it.
.PARAMETER HtmlGlobs
    Repo-relative globs of HTML pages whose relative hrefs S1 resolves and whose <code>
    paths S3 resolves. Empty disables it.
.PARAMETER IndexPath
    The doc index S4 checks. With -IndexedTrees, arms S4a/S4b.
.PARAMETER IndexedTrees
    Path prefixes whose docs must appear in the index.
.PARAMETER IndexExemptions
    Regexes exempting a path from S4b.
.PARAMETER BannedSubstrings
    Ordered map of known-dead substring -> reason. Empty disables S8.
.PARAMETER PlanningPaths
    Regexes for docs where work-remaining content is legitimate (S10 exemption).
.PARAMETER ReportOnlySignals
    Signals that never block. Default: S9, S3, S10.
.PARAMETER AsModule
    Dot-source the function definitions without running anything. For the tests.
#>
param(
    [ValidateSet('Gate', 'Sweep')]
    [string]$Mode = 'Gate',
    [string]$RepoRoot,
    [string[]]$PathExtensions = @(
        '.md', '.txt', '.rst',
        '.cs', '.c', '.h', '.cpp', '.hpp', '.rs', '.go', '.java', '.kt', '.swift',
        '.py', '.rb', '.js', '.ts', '.tsx', '.jsx', '.ps1', '.sh', '.bat',
        '.yml', '.yaml', '.json', '.toml', '.xml', '.ini', '.cfg', '.sql',
        '.csproj', '.sln', '.props', '.targets', '.resx', '.xaml', '.gradle', '.kts'
    ),
    # Deliberately short. The doc list is TRACKED FILES ONLY, so anything a repo gitignores --
    # target/, build/, dist/, obj/, bin/ output -- never reaches the scan and needs no entry
    # here. What is left is the committed-but-not-ours case. Substring-matched against
    # "/<relpath>", so an over-eager entry silently unscans real docs: '/build/' would have
    # dropped docs/build/instructions.md, and '/bin/' a bin/README.md.
    [string[]]$ScopeExclusions = @(
        'node_modules/', '/vendor/', '/_site/', '.claude/'
    ),
    [string[]]$SuppressPrefixes = @(),
    [string]$DocFxApiPattern = '',
    [string[]]$HtmlGlobs = @(),
    [string]$IndexPath = '',
    [string[]]$IndexedTrees = @(),
    [string[]]$IndexExemptions = @(),
    [System.Collections.IDictionary]$BannedSubstrings = [ordered]@{},
    [string[]]$PlanningPaths = @(),
    [string[]]$ReportOnlySignals = @('S9', 'S3', 'S10'),
    [switch]$AsModule
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# $RepoRoot is resolved after the -AsModule guard below, not here: -AsModule promises to define
# the functions and run nothing, and a dot-source from outside a work tree must not throw.

# $ScopeExclusions, $DocFxApiPattern, $PathExtensions and $ReportOnlySignals are parameters.
# The shipped defaults cover build output and vendored trees that exist under those names in
# any ecosystem; anything repo-specific is the consumer's to pass.

# Every signal this gate can emit, in the sentence's numeric order (the report groups
# lexicographically, which differs at S10). ONE list, not a convention: New-Finding
# rejects anything absent from it, so an emitter added without an entry here throws on its
# first finding, and Get-BlockingSignalSentence generates the sweep's rolling-issue prose from
# the same constant. A hand-written second copy is exactly how the source of this gate came to
# publish a sentence calling a blocking signal report-only.
$script:EmittedSignals = @('S1', 'S2', 'S3', 'S4a', 'S4b', 'S7', 'S8', 'S9', 'S10')

# The sentence the docs-freshness sweep writes into its rolling issue: emitted minus
# report-only. Both halves are ordered by $EmittedSignals, never by how the binding happens to
# list them, so a reordered `report_only` produces no diff in the rewritten body. A declared
# signal this gate cannot emit is named in neither half -- it is silent in the gate too.
function Get-BlockingSignalSentence {
    param([string[]]$ReportOnly = $ReportOnlySignals, [string[]]$Signals = $EmittedSignals)
    $blocking = @($Signals | Where-Object { $_ -notin $ReportOnly })
    $quiet    = @($Signals | Where-Object { $_ -in $ReportOnly })
    "Gate signals (blocking): {0}. Report-only: {1}." -f
        $(if ($blocking) { $blocking -join ', ' } else { 'none' }),
        $(if ($quiet)    { $quiet    -join ', ' } else { 'none' })
}

function Get-InScopeDocs {
    param([string]$Root)
    $rootFull = (Resolve-Path $Root).Path
    # Tracked files only, for the same reason S1/S2/S3 resolve against the index (see
    # Get-TrackedIndex): a doc that is on this disk but not in the repo -- a local
    # graphify-out report, a scratch note -- cannot be fixed by editing the repo and is
    # invisible to CI, so gating on it only ever produces a finding nobody can clear.
    (Get-TrackedIndex -RootFull $rootFull).Files | Where-Object { $_ -like '*.md' } |
        Where-Object { Test-InScopePath $_ }
}

# The scope filters both lists take: a page the consumer excluded, or one under a generated
# tree, is out of scope for every signal that reads it, not only for the ones that read a doc.
function Test-InScopePath {
    param([string]$Rel)
    foreach ($x in $ScopeExclusions) { if ("/$Rel" -like "*$x*") { return $false } }
    if ($DocFxApiPattern -and $Rel -match $DocFxApiPattern) { return $false }
    return $true
}

# HTML pages whose relative hrefs resolve like markdown link targets (S1) and whose <code>
# spans resolve like backtick spans (S3). Off unless the consumer names globs: most repos have
# no in-scope HTML, and naming a path here would make the plugin name a path in a consumer.
function Get-InScopeHtmlDocs {
    param([string]$Root, [string[]]$Globs = $HtmlGlobs)
    if (-not $Globs) { return }
    $rootFull = (Resolve-Path $Root).Path
    (Get-TrackedIndex -RootFull $rootFull).Files | Where-Object {
        $f = $_
        [bool](@($Globs | Where-Object { $f -like $_ }).Count)
    } | Where-Object { Test-InScopePath $_ }
}

function Test-PathShaped {
    param([string]$Token)
    if ($Token -notmatch '/') { return $false }
    foreach ($e in $PathExtensions) { if ($Token.EndsWith($e)) { return $true } }
    return $false
}

# S1/S2/S3 all resolve against the tracked file list ONLY -- never Test-Path. Test-Path
# answers against the working tree, so the verdict would depend on build state: a
# generated, gitignored file (e.g. a generated source file) resolves on a box
# that has been built and not on a clean checkout or a CI runner reusing another
# workflow's _work directory. A gate whose answer depends on what was built last is not a
# gate. Cached per root: the fixture suite calls the signal functions dozens of times and
# each git spawn on Windows costs ~100ms.
# A path that is gitignored BY DESIGN can never appear in the tracked index, so resolving it
# against that index always fails -- a doc legitimately naming a machine-local override, a
# signing key, or a local env file is reported forever with no way to fix it but a
# suppression. Asked once per token, only on the path that would otherwise emit a finding.
#
# ONLY a tracked .gitignore counts. Every other source is per-clone, so honoring it would make
# the verdict depend on the machine -- the exact property this skip exists to protect -- and a
# per-clone rule on a parent directory hides the tracked rule beneath it. So the question goes
# to an empty git dir made with no template, over the same work tree, with core.excludesFile
# pointed at nothing: .git/info/exclude, core.excludesFile and init.templateDir take no part.
# The dir lives for this one question. -v -z names the source of the matching pattern unquoted,
# and the path is ignored only when that source is a tracked file and the pattern is not a
# negation (!pattern). Only git's record for the path asked counts: the pipeline sends a line
# break after it, and git checks that as a path of its own.
#
# git still reads the work tree's .gitignore files, not the committed ones: an untracked
# .gitignore, or an uncommitted edit to a tracked one, can change the answer. A clean checkout
# has neither.
#
# The cost, stated: a repo that gitignores a tree AND cites a file inside it from a doc gets
# that reference skipped rather than reported. That is repo-owned and deterministic, but it is
# broader than the hand-curated prefix list it replaces, and nothing narrows it: a suppression
# only ever widens what the gate skips.
$script:IgnoreCheckCache = @{}
function Test-GitIgnored {
    param([string]$RootFull, [string]$RelPath)
    $key = "$RootFull|$RelPath"
    if ($script:IgnoreCheckCache.ContainsKey($key)) { return $script:IgnoreCheckCache[$key] }
    $gitDir = Join-Path ([System.IO.Path]::GetTempPath()) ('ouro-docsfresh-' + [guid]::NewGuid().ToString('N'))
    $ignored = $false
    try {
        # Inside the try: git init can create the dir and still fail.
        git init -q --bare --template= $gitDir 2>$null
        if ($LASTEXITCODE -ne 0) { throw "git init --bare $gitDir failed (exit $LASTEXITCODE)" }
        $noExcludes = (Join-Path $gitDir 'no-excludes').Replace('\', '/')
        $ignoreCase = if ((git -C $RootFull config --bool core.ignoreCase 2>$null) -eq 'true') { 'true' } else { 'false' }
        # UTF-8 both ways, for the same reason as the root lookup below: the path piped to git is
        # encoded with $OutputEncoding, which a caller may have changed, and git's reply is decoded
        # with [Console]::OutputEncoding.
        $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $encoding = [Console]::OutputEncoding
        try {
            [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
            $out = @("$RelPath$([char]0)" | git -C $RootFull --git-dir=$gitDir --work-tree=$RootFull -c "core.excludesFile=$noExcludes" -c "core.ignoreCase=$ignoreCase" check-ignore -v -z --no-index --stdin 2>$null)
            $code = $LASTEXITCODE
        }
        finally { [Console]::OutputEncoding = $encoding }
        # Records of source, line, pattern, path; PowerShell split the output at line breaks.
        $fields = ($out -join "`n").Split([char]0)
        for ($i = 0; $i + 3 -lt $fields.Count; $i += 4) {
            if ($fields[$i + 3] -cne $RelPath) { continue }
            $ignored = $code -eq 0 -and -not $fields[$i + 2].StartsWith('!') -and
                       (Get-TrackedIndex -RootFull $RootFull).FileSet.Contains($fields[$i])
        }
    }
    finally {
        if (Test-Path -LiteralPath $gitDir) {
            try { Remove-Item -LiteralPath $gitDir -Recurse -Force -WhatIf:$false -ErrorAction Stop }
            catch { Write-Warning "could not remove the temp git dir ${gitDir}: $($_.Exception.Message)" }
        }
    }
    $script:IgnoreCheckCache[$key] = $ignored
    return $ignored
}

$script:TrackedIndexCache = @{}
function Get-TrackedIndex {
    param([string]$RootFull)
    if ($script:TrackedIndexCache.ContainsKey($RootFull)) { return $script:TrackedIndexCache[$RootFull] }
    # -z and UTF-8: plain ls-files C-quotes a name holding a non-ASCII character, a backslash, a
    # double quote or a tab, and the quoted name matches nothing.
    $encoding = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $files = @((@(git -C $RootFull ls-files -z) -join "`n").Split([char]0, [System.StringSplitOptions]::RemoveEmptyEntries))
    }
    finally { [Console]::OutputEncoding = $encoding }
    $fileSet = [System.Collections.Generic.HashSet[string]]::new([string[]]$files, [System.StringComparer]::OrdinalIgnoreCase)
    $dirSet  = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($f in $files) {
        $d = $f
        while (($i = $d.LastIndexOf('/')) -gt 0) {
            $d = $d.Substring(0, $i)
            if (-not $dirSet.Add($d)) { break }   # ancestors already added via a sibling
        }
    }
    $idx = [PSCustomObject]@{ RootFull = $RootFull; Files = $files; FileSet = $fileSet; DirSet = $dirSet }
    $script:TrackedIndexCache[$RootFull] = $idx
    return $idx
}

# Pure path arithmetic (GetFullPath normalizes ../ lexically) -- never touches disk.
# -AllowDirectory: a markdown link may target a directory (GitHub renders its listing);
# a backticked path candidate never does (S3 filters trailing '/').
function Test-TrackedPath {
    param($Index, [string]$CandidatePath, [switch]$AllowDirectory)
    $full   = [System.IO.Path]::GetFullPath($CandidatePath)
    # DirectorySeparatorChar, never a literal '\': on Unix a backslash is a legal FILENAME
    # character and GetFullPath returns '/'-only paths, so a '\' prefix matches nothing and
    # every path -- including every valid one -- reads as outside the repo. S1 and S2 resolve
    # exclusively through here and both block, so that is a red gate on every clean non-Windows
    # checkout.
    $prefix = $Index.RootFull.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    $rel = $full.Substring($prefix.Length).TrimEnd('\', '/') -replace '\\', '/'
    if ($Index.FileSet.Contains($rel)) { return $true }
    return ($AllowDirectory -and $Index.DirSet.Contains($rel))
}

function New-Finding {
    param([string]$Signal, [string]$File, [int]$Line, [string]$Target, [string]$Reason)
    # The coupling that keeps the generated sentence honest: a signal nobody declared cannot
    # be emitted, so $EmittedSignals can never be missing one the gate actually reports.
    if ($Signal -notin $EmittedSignals) { throw "unknown signal '$Signal': add it to `$EmittedSignals" }
    [PSCustomObject]@{
        Signal = $Signal; File = $File; Line = $Line
        Target = $Target; Reason = $Reason
    }
}

# A link or path inside `code` is being shown, not referenced. Docs about markdown
# necessarily contain markdown that must not be resolved.
function Remove-CodeSpans {
    param([string]$Line)
    return ($Line -replace '`[^`]*`', '')
}

# [text](target) where target is not a URL, not a bare anchor, not a mailto.
$LinkPattern = '\[[^\]]*\]\(([^)\s]+)\)'

# A fence delimiter is 3+ backticks, after optional whitespace, followed by an info
# string containing NO further backtick. A single-line span like ```` ```dotnet add x``` ````
# has a closing ``` on the same line -- GitHub renders that as inline code, not a fence --
# so it must NOT match here, or fence state inverts for the rest of the file.
$FencePattern = '^\s*`{3,}[^`]*$'

$BacktickPattern = '`([^`]+)`'

# An HTML page's inline-code span -- S3's candidate shape there, since a backtick is ordinary
# text on a page. Lower-case and within one line, as the href pattern below is: an uppercased or
# split span is a miss rather than a wrong answer, and a gate must not differ by page style.
$CodeTagPattern = '<code[^>]*>([^<]*)</code>'

# A page cannot spell a placeholder any other way: a literal < inside <code> is a tag, so
# `src/<area>/x.md` is written escaped and would otherwise pass the skip class that drops its
# markdown twin. The five predefined entities and numeric references, nothing else: a gate that
# decoded named entities would need a table it cannot keep true of every page.
function Convert-HtmlEntity {
    param([string]$Text)
    $out = [regex]::Replace($Text, '&#(x[0-9a-fA-F]+|[0-9]+);', {
        param($m)
        $d = $m.Groups[1].Value
        $n = if ($d[0] -eq 'x' -or $d[0] -eq 'X') { [Convert]::ToInt32($d.Substring(1), 16) } else { [int]$d }
        if ($n -ge 0 -and $n -le 0x10FFFF) { [char]::ConvertFromUtf32($n) } else { $m.Value }
    })
    $out.Replace('&lt;', '<').Replace('&gt;', '>').Replace('&quot;', '"').Replace('&#39;', "'").Replace('&amp;', '&')
}

# Yields only renderable prose lines (with their 1-based numbers): fenced code is skipped,
# <!-- --> comment content is skipped or stripped -- a commented-out reference must neither
# block the gate nor count toward a line's RefCount. Comment state is checked before fence
# state so a fence delimiter inside a comment does not toggle $inFence; the opener check
# sits after the in-fence skip so a <!-- inside a fence stays code. S7 deliberately does
# not use this -- it tracks raw fence delimiters.
function Get-ProseLines {
    param([string]$FullPath)
    $lineNo = 0; $inFence = $false; $inComment = $false
    # -Encoding UTF8 is load-bearing: repo docs are UTF-8 without a BOM, and Windows
    # PowerShell's default Get-Content decodes those as cp1252. An em dash then reads as
    # three chars, one of which ('a-circumflex') is a letter, so ConvertTo-GitHubSlug keeps
    # it -- '### Enable/disable a feature - Product' slugged to
    # 'enabledisable-a-feature-a-product' and every correct anchor to a heading
    # containing a dash, arrow, or accent reported S2.
    foreach ($line in (Get-Content -LiteralPath $FullPath -Encoding UTF8)) {
        $lineNo++
        if ($inComment) {
            if ($line -notmatch '-->') { continue }
            $inComment = $false
            $line = ($line -split '-->', 2)[1]
        }
        if ($line -match $FencePattern) { $inFence = -not $inFence; continue }
        if ($inFence) { continue }
        $line = $line -replace '<!--.*?-->', ''
        if ($line -match '<!--') { $inComment = $true; $line = ($line -split '<!--', 2)[0] }
        [PSCustomObject]@{ Line = $line; LineNo = $lineNo }
    }
}

# An HTML page's lines, with their 1-based numbers. Read raw, the way S1 over a page reads them:
# Get-ProseLines models markdown, whose fence delimiter is ordinary text on a page and would
# blind the rest of a file S7 never scans. So a <code> span inside an HTML comment is read too,
# as a dead href inside one already is.
function Get-HtmlLines {
    param([string]$FullPath)
    $lineNo = 0
    foreach ($line in (Get-Content -LiteralPath $FullPath -Encoding UTF8)) {
        $lineNo++
        [PSCustomObject]@{ Line = $line; LineNo = $lineNo }
    }
}

# Every non-URL markdown-link candidate on a code-span-stripped line: a path target, an
# anchor, or both. S1 keeps those with a PathPart, S2 those with an Anchor,
# Get-LineReferenceCount counts them all -- one filter, three consumers.
function Get-LinkCandidates {
    param([string]$StrippedLine)
    foreach ($m in [regex]::Matches($StrippedLine, $LinkPattern)) {
        $target = $m.Groups[1].Value
        if ($target -match '^(https?:|mailto:|//)') { continue }
        $pathPart = ($target -split '#')[0]
        $anchor   = if ($target -match '#') { ($target -split '#', 2)[1] } else { '' }
        if ([string]::IsNullOrWhiteSpace($pathPart) -and [string]::IsNullOrWhiteSpace($anchor)) { continue }
        [PSCustomObject]@{ Target = $target; PathPart = $pathPart; Anchor = $anchor }
    }
}

# The root-relative form of a parent-relative token (../x.md), resolved against the doc's own
# directory lexically: GetFullPath folds the ../ segments and never touches disk. $null when it
# lands outside the repo root.
function Resolve-ParentRelative {
    param([string]$Token, [string]$DocDir, [string]$RootFull)
    $full   = [System.IO.Path]::GetFullPath((Join-Path $DocDir $Token))
    $prefix = $RootFull.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { return $null }
    return ($full.Substring($prefix.Length) -replace '\\', '/')
}

# Path-shaped tokens from a line's inline-code spans -- S3's candidate shape, shared with
# Get-LineReferenceCount. -Html reads <code> spans, the shape an HTML page spells one in. A
# parent-relative token needs the doc's own directory to resolve; a caller with no doc gets none.
function Get-CodeSpanCandidates {
    param([string]$Line, [string]$DocDir, [string]$RootFull, [switch]$Html)
    $pattern = if ($Html) { $CodeTagPattern } else { $BacktickPattern }
    foreach ($m in [regex]::Matches($Line, $pattern)) {
        $token = $m.Groups[1].Value.Trim()
        if ($Html) { $token = (Convert-HtmlEntity $token).Trim() }
        if (-not (Test-PathShaped $token))                        { continue }
        if ($token -match '^(https?:|\*|\$)')                     { continue }
        if ($token -match '[ *<>{}:…]' -or $token.EndsWith('/'))  { continue }
        # A token that resolves outside this repo is unverifiable, not dead: the runner's home
        # (~/x.json), a UNC share (//host/s/x.md) or anything rooted (/etc/x.json) names a tree
        # the gate cannot see, and reporting it makes the verdict machine-dependent. A single
        # leading separator covers the rooted and UNC forms on both platforms -- on Unix there
        # is no drive colon to catch /etc/x.json, so the colon in the character class above does
        # not reach it. The anchor parser applies the same rule to issue bodies, and skips a
        # ../ token as well; this gate resolves one below.
        if ($token -match '^(~[\\/]|[\\/])')                        { continue }
        # A parent-relative token that lands inside the repo root is a candidate like any other;
        # one that lands outside names another tree and is skipped, as above.
        if ($token -match '^\.\.[\\/]') {
            if (-not $DocDir -or -not $RootFull) { continue }
            if ($null -eq (Resolve-ParentRelative $token $DocDir $RootFull)) { continue }
        }
        $token
    }
}

function Get-MarkdownLinkFindings {
    param([string]$Root, [string[]]$Docs, [string[]]$IgnoreFiles = @(), [string[]]$IgnoreTokens = @())
    $rootFull = (Resolve-Path $Root).Path
    $trackedIndex = Get-TrackedIndex $rootFull
    foreach ($doc in $Docs) {
        # A `file:` ignore entry exempts the whole file -- Test-Suppressed's token-level
        # EndsWith check can't express that, since the token here is the link target, not
        # the referencing doc's own path.
        if ($IgnoreFiles | Where-Object { $doc -eq $_ -or $doc.EndsWith($_) }) { continue }
        $full  = Join-Path $rootFull $doc
        $docDir = Split-Path $full -Parent
        foreach ($pl in (Get-ProseLines -FullPath $full)) {
            $line = $pl.Line
            $candidates = @(Get-LinkCandidates (Remove-CodeSpans $line) |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_.PathPart) })
            # RefCount is line-global (Get-LineReferenceCount), not $candidates.Count -- see
            # the comment on Test-Suppressed.
            $refCount = Get-LineReferenceCount $line -DocDir $docDir -RootFull $rootFull
            foreach ($c in $candidates) {
                # (planned) reads the code-span-bearing line -- a marker outside the backticks
                # that Remove-CodeSpans stripped must still be seen. A parent-relative target, or
                # a root-relative one (below), is tested against a suppressed prefix by its
                # root-relative form, as S3 tests a token.
                $relPath = if ($c.PathPart -match '^\.\.[\\/]') { Resolve-ParentRelative $c.PathPart $docDir $rootFull }
                           elseif ($c.PathPart -match '^/') { $c.PathPart.Substring(1) }
                           else { $c.PathPart }
                if (Test-Suppressed $c.PathPart $line $IgnoreTokens $refCount -RelPath $relPath) { continue }
                # A markdown link starting with a single slash resolves against the repository
                # root, as GitHub renders it -- Get-LinkCandidates already dropped the two-slash,
                # another-host form.
                $targetPath = if ($c.PathPart -match '^/') { Join-Path $rootFull $relPath } else { Join-Path $docDir $c.PathPart }
                if (-not (Test-TrackedPath $trackedIndex $targetPath -AllowDirectory)) {
                    New-Finding -Signal 'S1' -File $doc -Line $pl.LineNo `
                        -Target $c.Target -Reason 'link target does not exist'
                }
            }
        }
    }
}

# href="#x" and href="" never match; scheme-bearing targets are dropped below.
$HrefPattern = 'href="([^"#][^"]*)"'
function Get-HtmlHrefFindings {
    param([string]$Root, [string[]]$Docs)
    $rootFull = (Resolve-Path $Root).Path
    $trackedIndex = Get-TrackedIndex $rootFull
    foreach ($doc in $Docs) {
        $full   = Join-Path $rootFull $doc
        $docDir = Split-Path $full -Parent
        $lineNo = 0
        foreach ($line in (Get-Content -LiteralPath $full -Encoding UTF8)) {
            $lineNo++
            foreach ($m in [regex]::Matches($line, $HrefPattern)) {
                $target = $m.Groups[1].Value
                if ($target -match '^(https?:|mailto:)') { continue }
                # An href starting with a slash, one or two, names a site root or another host on
                # a published page, never a path in this repository.
                if ($target -match '^/') { continue }
                $pathPart = ($target -split '#')[0]
                # A suppressed prefix names a file from the root, so a parent-relative href is
                # tested by its root-relative form too, as S1 on markdown already does. Scope is
                # the prefix only -- no ignore list, no (planned) marker: those stay off HTML.
                $relPath = if ($pathPart -match '^\.\.[\\/]') { Resolve-ParentRelative $pathPart $docDir $rootFull } else { $pathPart }
                if ($SuppressPrefixes | Where-Object { $pathPart.StartsWith($_) -or ($relPath -and $relPath.StartsWith($_)) }) { continue }
                if (-not (Test-TrackedPath $trackedIndex (Join-Path $docDir $pathPart) -AllowDirectory)) {
                    New-Finding -Signal 'S1' -File $doc -Line $lineNo `
                        -Target $target -Reason 'link target does not exist'
                }
            }
        }
    }
}

function ConvertTo-GitHubSlug {
    param([string]$Heading)
    $s = $Heading.Trim().ToLowerInvariant()
    $s = $s -replace '[^\p{L}\p{Nd}\s_-]', ''   # drop punctuation, keep letters/digits/space/underscore/hyphen
    $s = $s -replace '\s', '-'                  # per-character: two spaces -> two hyphens (matches GitHub's tr)
    return $s
}

function Get-DocHeadingSlugs {
    param([string]$FullPath)
    $slugs = @()
    foreach ($pl in (Get-ProseLines -FullPath $FullPath)) {
        if ($pl.Line -match '^\s{0,3}#{1,6}\s+(.+?)\s*$') {
            $slugs += ConvertTo-GitHubSlug $Matches[1]
        }
    }
    return $slugs
}

function Get-AnchorFindings {
    param([string]$Root, [string[]]$Docs, [string[]]$IgnoreFiles = @(), [string[]]$IgnoreTokens = @())
    $rootFull = (Resolve-Path $Root).Path
    $trackedIndex = Get-TrackedIndex $rootFull
    foreach ($doc in $Docs) {
        # A `file:` ignore entry exempts the whole file -- see the matching comment in
        # Get-MarkdownLinkFindings.
        if ($IgnoreFiles | Where-Object { $doc -eq $_ -or $doc.EndsWith($_) }) { continue }
        $full   = Join-Path $rootFull $doc
        $docDir = Split-Path $full -Parent
        foreach ($pl in (Get-ProseLines -FullPath $full)) {
            $line = $pl.Line
            $candidates = @(Get-LinkCandidates (Remove-CodeSpans $line) |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_.Anchor) })
            $refCount = Get-LineReferenceCount $line -DocDir $docDir -RootFull $rootFull
            foreach ($c in $candidates) {
                # (planned), the parent-relative form and the root-relative form -- see
                # Get-MarkdownLinkFindings.
                $relPath = if ($c.PathPart -match '^\.\.[\\/]') { Resolve-ParentRelative $c.PathPart $docDir $rootFull }
                           elseif ($c.PathPart -match '^/') { $c.PathPart.Substring(1) }
                           else { $c.PathPart }
                if (Test-Suppressed $c.PathPart $line $IgnoreTokens $refCount -RelPath $relPath) { continue }
                $targetFile = if ([string]::IsNullOrWhiteSpace($c.PathPart)) { $full }
                              elseif ($c.PathPart -match '^/') { Join-Path $rootFull $relPath }
                              else { Join-Path $docDir $c.PathPart }
                if (-not (Test-TrackedPath $trackedIndex $targetFile)) { continue }  # S1's job
                if ((Get-DocHeadingSlugs -FullPath $targetFile) -notcontains $c.Anchor) {
                    New-Finding -Signal 'S2' -File $doc -Line $pl.LineNo `
                        -Target $c.Target -Reason 'anchor has no matching heading'
                }
            }
        }
    }
}

# $SuppressPrefixes is a parameter and ships EMPTY. A consumer's list names that consumer's
# sibling repos and build output; shipping one as a default would silently suppress genuine
# findings in every other repo.

# Two entry kinds, told apart by a `file:` prefix -- see .docs-freshness-ignore's header.
# `file: <path>` is a whole-document S1/S2/S3 exemption; a bare token is a repo-wide
# suffix suppression honored by Test-Suppressed (S1/S2/S3). One list conflating both
# meant a doc-exemption entry also silently suppressed any unrelated token elsewhere
# in the repo that happened to share its suffix.
#
# A `file:` entry is validated against Docs here, at parse time, using the exact
# `$doc -eq $entry -or $doc.EndsWith($entry)` rule the finding functions apply at scan
# time: it must resolve to EXACTLY one in-scope doc. Nothing warned before this --
# `file: README.md` would silently exempt every README in the repo (41 of them) from
# S1/S2/S3, and a dangling entry (its doc moved or was deleted) would silently do nothing.
function Get-IgnoreList {
    param([string]$Root, [string[]]$Docs = @())
    $f = Join-Path $Root '.docs-freshness-ignore'
    if (-not (Test-Path -LiteralPath $f)) { return [PSCustomObject]@{ Files = @(); Tokens = @() } }
    $files  = @()
    $tokens = @()
    Get-Content -LiteralPath $f |
        ForEach-Object { ($_ -split '#')[0].Trim() } |
        Where-Object   { $_ -ne '' } |
        ForEach-Object {
            if ($_ -match '^file:\s*(.+)$') { $files += $Matches[1].Trim() }
            else                            { $tokens += $_ }
        }
    foreach ($entry in $files) {
        $hits = @($Docs | Where-Object { $_ -eq $entry -or $_.EndsWith($entry) })
        if ($hits.Count -eq 0) {
            throw "docs-freshness-ignore: 'file: $entry' resolves to no in-scope doc -- stale entry, or a typo?"
        }
        if ($hits.Count -gt 1) {
            throw "docs-freshness-ignore: 'file: $entry' is ambiguous -- matches $($hits.Count) in-scope docs ($($hits -join ', ')). Use a longer, unambiguous path."
        }
    }
    [PSCustomObject]@{ Files = $files; Tokens = $tokens }
}

# RefCount is how many path-shaped references share Line, counted GLOBALLY across every
# signal type by Get-LineReferenceCount below -- not a single signal function's own
# candidate list, which undercounts a mixed line (e.g. one broken markdown link plus one
# broken code-span path each look like a lone reference to whichever function is scoring
# them). (planned) only ever disambiguates to a single reference; on a line with more than
# one, suppressing would risk silencing a genuinely stale neighbor, so none are suppressed
# and the line is reported normally.
# RelPath is the token's root-relative form where that differs from the token (a parent-relative
# one); a suppressed prefix names files from the root, so it is tested against both.
function Test-Suppressed {
    param([string]$Token, [string]$Line, [string[]]$IgnoreTokens, [int]$RefCount = 1, [string]$RelPath)
    if ($Line -match '\(planned\)' -and $RefCount -le 1) { return $true }
    foreach ($p in $SuppressPrefixes) {
        if ($Token.StartsWith($p) -or ($RelPath -and $RelPath.StartsWith($p))) { return $true }
    }
    foreach ($i in $IgnoreTokens) { if ($Token -eq $i -or $Token.EndsWith($i)) { return $true } }
    return $false
}

# The line-global reference count Test-Suppressed's RefCount needs: every markdown-link
# candidate S1 OR S2 would score (a path target, an anchor, or both -- the anchor-only
# `[t](#a)` shape included) PLUS every path-shaped code-span token, added together via the
# same shared candidate filters the signal functions use. Kept as its own pass because it
# must total across all three shapes, not just one caller's own list. -Html counts the page
# shape, or a (planned) marker on a line naming two <code> paths would score zero and suppress
# both; and on a page it counts the hrefs S1 reads there, since a marker on a line carrying one
# of each would otherwise hide the path while the link is reported.
function Get-LineReferenceCount {
    param([string]$Line, [string]$DocDir, [string]$RootFull, [switch]$Html)
    # The finder's own predicate, not a stricter one: a scheme it reports on is a reference the
    # count has to see, or a marker on a line carrying one hides the path beside it.
    $hrefs = if ($Html) { @([regex]::Matches($Line, $HrefPattern) |
        Where-Object { $_.Groups[1].Value -notmatch '^(https?:|mailto:|/)' }).Count } else { 0 }
    return @(Get-LinkCandidates (Remove-CodeSpans $Line)).Count + $hrefs +
           @(Get-CodeSpanCandidates $Line -DocDir $DocDir -RootFull $RootFull -Html:$Html).Count
}

function Get-CodePathFindings {
    param([string]$Root, [string[]]$Docs, [string[]]$IgnoreTokens, [string[]]$IgnoreFiles = @(), [switch]$Html)
    $rootFull = (Resolve-Path $Root).Path
    # Suffix match because docs commonly reference paths tree-relatively (Sub/Dir/x.md for
    # Top/Sub/Dir/x.md) rather than from the repo root.
    $trackedIndex = Get-TrackedIndex $rootFull
    $reason = if ($Html) { '<code> repo path does not exist' } else { 'backticked repo path does not exist' }

    foreach ($doc in $Docs) {
        # A `file:` ignore entry exempts the whole file -- see the matching comment in
        # Get-MarkdownLinkFindings. Originally S1/S2-only; extended here once a real entry
        # (the implementation plan, all 12 of its findings S3) needed it to actually apply.
        if ($IgnoreFiles | Where-Object { $doc -eq $_ -or $doc.EndsWith($_) }) { continue }
        $full   = Join-Path $rootFull $doc
        $docDir = Split-Path $full -Parent
        foreach ($pl in $(if ($Html) { Get-HtmlLines -FullPath $full } else { Get-ProseLines -FullPath $full })) {
            $line = $pl.Line
            $candidates = @(Get-CodeSpanCandidates $line -DocDir $docDir -RootFull $rootFull -Html:$Html)
            $refCount = Get-LineReferenceCount $line -DocDir $docDir -RootFull $rootFull -Html:$Html
            foreach ($token in $candidates) {
                # A parent-relative token is read by its root-relative form where a form matters:
                # a suppressed prefix names files from the root, and git check-ignore refuses a
                # path outside the work tree, which a ../ token is as written.
                $relPath = if ($token -match '^\.\.[\\/]') { Resolve-ParentRelative $token $docDir $rootFull } else { $token }
                if (Test-Suppressed $token $line $IgnoreTokens $refCount -RelPath $relPath) { continue }
                $suffix   = "/$token"
                $bySuffix = [bool]($trackedIndex.Files | Where-Object { $_ -eq $token -or $_.EndsWith($suffix) } | Select-Object -First 1)
                $resolved = (Test-TrackedPath $trackedIndex (Join-Path $rootFull $token)) -or
                            (Test-TrackedPath $trackedIndex (Join-Path $docDir   $token)) -or $bySuffix
                if ($resolved) { continue }
                if (Test-GitIgnored -RootFull $rootFull -RelPath $relPath) { continue }
                New-Finding -Signal 'S3' -File $doc -Line $pl.LineNo -Target $token -Reason $reason
            }
        }
    }
}

# S7 -- a code fence opened and never closed. Every scan above tracks fence state
# independently and stops treating content as prose the moment $inFence flips true; if it
# never flips back before EOF, everything from the opening delimiter onward is invisible to
# S1/S2/S3 -- one forgotten closing ``` darkens the rest of the file. Fully deterministic (no
# heuristic, no suppression list), so it blocks like S1-S4 rather than reporting like S9.
function Get-UnclosedFenceFindings {
    param([string]$Root, [string[]]$Docs)
    $rootFull = (Resolve-Path $Root).Path
    foreach ($doc in $Docs) {
        $full     = Join-Path $rootFull $doc
        $inFence  = $false
        $openLine = 0
        $lineNo   = 0
        foreach ($line in (Get-Content -LiteralPath $full -Encoding UTF8)) {
            $lineNo++
            if ($line -match $FencePattern) {
                $inFence = -not $inFence
                if ($inFence) { $openLine = $lineNo }
            }
        }
        if ($inFence) {
            New-Finding -Signal 'S7' -File $doc -Line $openLine `
                -Target '```' -Reason 'code fence opened here has no closing delimiter -- S1/S2/S3 stop scanning after this line'
        }
    }
}

# S4 is opt-in: a doc must be indexed iff it lives in a tree the consumer declares. Both
# -IndexPath and -IndexedTrees are required to arm it, because a repo with no index
# convention has nothing for this signal to say. -IndexExemptions are regexes matched
# case-insensitively (PowerShell -match's default) against the repo-relative path.
function Test-IndexRequired {
    param([string]$RelPath, [string[]]$Trees = $IndexedTrees, [string[]]$Exemptions = $IndexExemptions)
    $inTree = $false
    foreach ($t in $Trees) { if ($RelPath.StartsWith($t)) { $inTree = $true; break } }
    if (-not $inTree) { return $false }
    foreach ($pattern in $Exemptions) {
        if ($RelPath -match $pattern) { return $false }
    }
    return $true
}

function Get-IndexFindings {
    param([string]$Root, [string]$Index = $IndexPath, [string[]]$Trees = $IndexedTrees)
    if (-not $Index -or -not $Trees) { return }   # unarmed: no index convention declared
    $indexRel  = $Index
    $indexFull = Join-Path $Root $indexRel
    if (-not (Test-Path -LiteralPath $indexFull)) { return }

    $raw     = Get-Content -LiteralPath $indexFull -Encoding UTF8
    $entries = @()
    $lineNo  = 0
    $inExcluded = $false
    foreach ($line in $raw) {
        $lineNo++
        if ($line -match '^##\s+Excluded from index') { $inExcluded = $true; continue }
        if ($inExcluded) { continue }
        if ($line -match '^- `([^`]+\.(?:md|html))`') {
            $entries += [PSCustomObject]@{ Path = $Matches[1]; Line = $lineNo }
        }
    }

    # An index may name a page as well as a doc, so both signals take both lists: an entry the
    # parser accepts but S4a cannot resolve would report a page that is present as missing. A
    # page is in scope only under -HtmlGlobs, as it is for every other signal, and only a page:
    # a broad glob also brings in a stylesheet or a script, which no entry can name.
    $docs = @(Get-InScopeDocs -Root $Root) + @(Get-InScopeHtmlDocs -Root $Root | Where-Object { $_ -like '*.html' })

    # S4a -- an index entry that resolves to nothing.
    foreach ($e in $entries) {
        $hit = $docs | Where-Object { $_.EndsWith($e.Path) } | Select-Object -First 1
        if (-not $hit) {
            New-Finding -Signal 'S4a' -File $indexRel -Line $e.Line `
                -Target $e.Path -Reason 'index entry resolves to no file'
        }
    }

    # S4b -- a doc beside code that no index entry covers.
    foreach ($doc in $docs) {
        if (-not (Test-IndexRequired -RelPath $doc -Trees $Trees)) { continue }
        $covered = $entries | Where-Object { $doc.EndsWith($_.Path) } | Select-Object -First 1
        if (-not $covered) {
            New-Finding -Signal 'S4b' -File $doc -Line 0 `
                -Target $indexRel -Reason 'doc beside code is not in the index'
        }
    }
}

# S8 -- a reference to something KNOWN dead, however it is written. Substring table with a
# reason per entry; raw-line scan on purpose (fences and comments included -- a dead host
# misleads wherever it appears). Zero false positives by construction, so it blocks.
# $BannedSubstrings is a parameter (substring -> reason) and ships EMPTY: what is known dead
# is per-repo. The reason is not decoration -- S8 blocks, and a blocked build has to say why.
function Get-BannedReferenceFindings {
    param([string]$Root, [string[]]$Docs, $Banned = $BannedSubstrings)
    if (-not $Banned -or $Banned.Count -eq 0) { return }
    $rootFull = (Resolve-Path $Root).Path
    foreach ($doc in $Docs) {
        $lineNo = 0
        foreach ($line in (Get-Content -LiteralPath (Join-Path $rootFull $doc) -Encoding UTF8)) {
            $lineNo++
            foreach ($b in $Banned.Keys) {
                if ($line.IndexOf($b, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                    New-Finding -Signal 'S8' -File $doc -Line $lineNo `
                        -Target $b -Reason $Banned[$b]
                }
            }
        }
    }
}

# S9 -- external URL liveness. Sweep-only (network): every unique http(s) URL in prose
# (fences/comments skipped via Get-ProseLines) gets a HEAD, retried as GET; only
# definitively-dead outcomes (404/410, DNS failure) are findings -- 403/405/429/5xx/
# timeouts are inconclusive, not rot. Report-only into the weekly rolling issue: external
# sites break for reasons that are not ours, so this never gates. Born from the 2026
# repo-rename outage, where every github.io link 404'd for months with nothing watching.
$UrlPattern = 'https?://[^\s<>"''()\]]+'
$ExemptUrlHosts = [ordered]@{
    'localhost'        = 'local example'
    '127.0.0.1'        = 'local example'
    'example.com'      = 'RFC 2606 placeholder'
}
# RFC 2606/6761 reserved TLDs -- documentation placeholders, never live.
$ExemptTldPattern = '://[^/]*\.(local|localhost|invalid|test|example)([/:]|$)'
function Get-ExternalUrlOccurrences {
    param([string]$Root, [string[]]$Docs)
    $rootFull = (Resolve-Path $Root).Path
    foreach ($doc in $Docs) {
        foreach ($pl in (Get-ProseLines -FullPath (Join-Path $rootFull $doc))) {
            foreach ($m in [regex]::Matches($pl.Line, $UrlPattern)) {
                $url = $m.Value.TrimEnd('.', ',', ';', ':', ')', ']', '>', "'", '"', '`')
                $skip = $url -match $ExemptTldPattern
                if (-not $skip) {
                    foreach ($h in $ExemptUrlHosts.Keys) { if ($url -match [regex]::Escape($h)) { $skip = $true; break } }
                }
                if (-not $skip) { [PSCustomObject]@{ Url = $url; File = $doc; Line = $pl.LineNo } }
            }
        }
    }
}
function Test-UrlDead {
    param([string]$Url)
    # HEAD first (cheap), but NO verdict is final on HEAD -- some hosts (nuget.org)
    # answer HEAD with 404 while GET returns 200. Only the GET attempt declares death.
    try {
        Invoke-WebRequest -Uri $Url -Method Head -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop | Out-Null
        return $null
    } catch { }
    try {
        Invoke-WebRequest -Uri $Url -Method Get -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop | Out-Null
        return $null
    } catch {
        $status = 0
        try { $status = [int]$_.Exception.Response.StatusCode } catch { }
        # A github.com 404 is inconclusive: anonymous requests to PRIVATE repos 404 by
        # design (a repository's own settings, releases and issues pages). github.io keeps full 404
        # semantics -- Pages is always public, and that host class is the outage S9
        # exists for.
        if (($status -eq 404 -or $status -eq 410) -and $Url -notmatch '^https?://github\.com/') { return "dead($status)" }
        if ($status -eq 0 -and "$($_.Exception.Message)$($_.Exception.InnerException.Message)" -match 'No such host') { return 'dead(dns)' }
        return $null   # 403/405/429/5xx/timeout/github.com-404: inconclusive, not rot
    }
}
function Get-DeadUrlFindings {
    param([string]$Root, [string[]]$Docs)
    $occurrences = @(Get-ExternalUrlOccurrences -Root $Root -Docs $Docs)
    $byUrl = @($occurrences | Group-Object Url)
    # Write-Host, not Write-Output -- this function's pipeline output IS the findings
    # array; a status string here would pollute it.
    Write-Host ("S9: probing {0} unique external URLs across {1} occurrences..." -f $byUrl.Count, $occurrences.Count)
    foreach ($g in $byUrl) {
        $verdict = Test-UrlDead $g.Name
        if ($verdict) {
            foreach ($o in $g.Group) {
                New-Finding -Signal 'S9' -File $o.File -Line $o.Line -Target $g.Name -Reason "external URL $verdict"
            }
        }
    }
}

# S10 -- work-remaining content in a non-planning doc. Docs describe what the system IS;
# what work REMAINS lives in issues (the loop contract's doc/issue boundary). Two shapes,
# both prose-only via Get-ProseLines (a checkbox or heading inside a fence is being shown,
# not tracked): a section heading whose text names undone work, and a markdown checkbox
# line. "Known limitations" is deliberately NOT banned -- behavior boundaries of what ships
# are current-state; "Known issues" (defect lists) is.
$S10HeadingPattern  = '(?i)\b(TODO|FIXME|Roadmap|Backlog|Next steps|Future work|Future plans|Remaining|Known issues|Known follow-ups|Open questions|Open items|Work in progress)\b'
$S10CheckboxPattern = '^\s*[-*+] \[[ xX]\]'
# Exemptions are -PlanningPaths: regexes for docs where work-remaining content is legitimate
# (policy docs whose topic is future state, and procedural checklists run per release or per
# test pass). Empty by default -- which paths those are is per-repo.
function Get-WorkRemainingFindings {
    param([string]$Root, [string[]]$Docs, $Exemptions = $PlanningPaths)
    $rootFull = (Resolve-Path $Root).Path
    foreach ($doc in $Docs) {
        $exempt = $false
        foreach ($pattern in $Exemptions) { if ($doc -match $pattern) { $exempt = $true; break } }
        if ($exempt) { continue }
        foreach ($pl in (Get-ProseLines -FullPath (Join-Path $rootFull $doc))) {
            if ($pl.Line -match '^\s{0,3}#{1,6}\s+(.+?)\s*$') {
                $headingText = $Matches[1]
                if ($headingText -match $S10HeadingPattern) {
                    New-Finding -Signal 'S10' -File $doc -Line $pl.LineNo `
                        -Target $headingText -Reason 'work-remaining section in a non-planning doc -- distill to an issue (the loop contract, doc/issue boundary)'
                }
            }
            elseif ($pl.Line -match $S10CheckboxPattern) {
                New-Finding -Signal 'S10' -File $doc -Line $pl.LineNo `
                    -Target ($pl.Line.Trim()) -Reason 'checkbox list in a non-planning doc -- work tracking belongs in issues (the loop contract, doc/issue boundary)'
            }
        }
    }
}

if ($AsModule) { return }

# The repo root comes from the working directory, never from this script's own location: the
# script ships in a plugin checkout that lives inside, beside, or nowhere near the repo it is
# scanning. Same idiom as every other gate here.
if (-not $RepoRoot) {
    # git prints the root as UTF-8: decoded with a caller's OEM code page, a non-ASCII root names
    # no directory, so a bound repo reads as unbound and resolving the root throws.
    $encoding = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $RepoRoot = (git rev-parse --show-toplevel 2>$null)
    }
    finally { [Console]::OutputEncoding = $encoding }
    if (-not $RepoRoot) { throw 'not inside a git work tree: run from the consumer repo, or pass -RepoRoot' }
    $RepoRoot = $RepoRoot.Trim()
}

# ---------------------------------------------------------------------------------------
# Bind the [docs] table to the parameters above -- AFTER the repo root is known, because the
# binding belongs to the repo being scanned, not to whatever directory the run started in.
#
# A declared key that cannot be read is a hard error, never a silent fallback: the defect this
# closes was eleven keys validating and then being discarded, and swallowing a bad value would
# reproduce it in a quieter form. A key the binding does not declare, and a repo with no
# binding at all, are not errors -- they are the documented "runs on its own defaults" case.
$bindingFile = Join-Path $RepoRoot '.claude/ouro.toml'
$bindingTool = Join-Path $PSScriptRoot 'ouro-binding.py'
$haveBinding = (Test-Path -LiteralPath $bindingFile) -and (Test-Path -LiteralPath $bindingTool) -and
               [bool](Get-Command python3 -ErrorAction SilentlyContinue)

if (-not $haveBinding) {
    # Two ways to get here are ordinary: no binding (an unbound repo), no ouro-binding.py (a
    # vendored tree that dropped the tool). Say which, because a silently unconfigured run looks
    # identical to a configured one. A binding and the tool with no python is neither ordinary
    # nor a fallback: the defaults would stand in for the policy the repo declared.
    $why = if (-not (Test-Path -LiteralPath $bindingFile)) { "no $bindingFile" }
           elseif (-not (Test-Path -LiteralPath $bindingTool)) { 'no ouro-binding.py beside this script' }
           else { throw "no python3 on PATH, and $bindingFile is a binding: a repo that has one needs python3 3.11+ on PATH, because its [docs] table is read through ouro-binding.py" }
    Write-Host "INFO - [docs] not read ($why): running on defaults and any explicit parameters"
} else {
    function Get-BindingDocsValue {
        param([string]$Key, [string]$Tool, [string]$BindingPath)
        # Only stdout is the value: under 2>&1 stderr arrives as ErrorRecords, and a python that
        # exits 0 may still write there (a DeprecationWarning, PYTHONDEVMODE, a sitecustomize
        # notice). Folding those in silently corrupts a string key -- an index path that then
        # resolves to nothing disarms S4 with no error at all. ouro-binding.py prints UTF-8, so the
        # same happens to a non-ASCII value decoded with a caller's OEM code page.
        $encoding = [Console]::OutputEncoding
        try {
            [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
            $raw = python3 $Tool get "docs.$Key" $BindingPath 2>&1
        }
        finally { [Console]::OutputEncoding = $encoding }
        $out = ((@($raw) | Where-Object { $_ -is [string] }) -join "`n").Trim()
        if ($LASTEXITCODE -ne 0) {
            # "no such key" is the undeclared case. Anything else -- an unreadable or invalid
            # binding, a python below the 3.11 floor -- is a configuration error the caller must
            # see, and ouro-binding.py says which on stderr: the message carries every line, or
            # it ends in an empty colon.
            if ("$raw" -match 'no such key') { return $null }
            throw "ouro-binding.py get docs.$Key failed (exit $LASTEXITCODE): $(((@($raw) | ForEach-Object { "$_" }) -join "`n").Trim())"
        }
        return $out
    }

    $BindingKeys = [ordered]@{
        PathExtensions    = @{ key = 'extensions';        kind = 'list' }
        ScopeExclusions   = @{ key = 'exclude';           kind = 'list' }
        SuppressPrefixes  = @{ key = 'suppress_prefixes'; kind = 'list' }
        DocFxApiPattern   = @{ key = 'generated_pattern'; kind = 'string' }
        HtmlGlobs         = @{ key = 'html_globs';        kind = 'list' }
        IndexPath         = @{ key = 'index_path';        kind = 'string' }
        IndexedTrees      = @{ key = 'indexed_trees';     kind = 'list' }
        IndexExemptions   = @{ key = 'index_exempt';      kind = 'list' }
        PlanningPaths     = @{ key = 'planning_paths';    kind = 'list' }
        ReportOnlySignals = @{ key = 'report_only';       kind = 'list' }
        BannedSubstrings  = @{ key = 'banned';            kind = 'map' }
    }

    foreach ($param in $BindingKeys.Keys) {
        if ($PSBoundParameters.ContainsKey($param)) { continue }   # an explicit argument wins
        $spec = $BindingKeys[$param]
        $raw  = Get-BindingDocsValue -Key $spec.key -Tool $bindingTool -BindingPath $bindingFile
        if ($null -eq $raw) { continue }                            # undeclared: keep the default
        try {
            switch ($spec.kind) {
                'string' { Set-Variable -Name $param -Value $raw -Scope Script }
                'list'   { Set-Variable -Name $param -Value @($raw | ConvertFrom-Json) -Scope Script }
                'map'    {
                    $h = [ordered]@{}
                    foreach ($p in ($raw | ConvertFrom-Json).PSObject.Properties) { $h[$p.Name] = [string]$p.Value }
                    Set-Variable -Name $param -Value $h -Scope Script
                }
            }
        } catch {
            throw "docs.$($spec.key) is declared but unusable ($($_.Exception.Message)): $raw"
        }
    }
}
# ---------------------------------------------------------------------------------------


# Both doc lists must exist before Get-IgnoreList -- it validates every `file:` entry against
# the in-scope docs at parse time, and an in-scope HTML page is a valid target: S3 reports on
# one, so the exemption that excuses such a finding has to parse.
$docs       = @(Get-InScopeDocs -Root $RepoRoot)
$htmlDocs   = @(Get-InScopeHtmlDocs -Root $RepoRoot)
# One file a glob names and the markdown list already holds is one doc: handed over twice it
# reads as ambiguous to a file: entry, which stops the run.
$ignoreList = Get-IgnoreList -Root $RepoRoot -Docs @(@($docs) + @($htmlDocs) | Select-Object -Unique)

$findings  = @()
$findings += Get-MarkdownLinkFindings  -Root $RepoRoot -Docs $docs -IgnoreFiles $ignoreList.Files -IgnoreTokens $ignoreList.Tokens
$findings += Get-HtmlHrefFindings      -Root $RepoRoot -Docs $htmlDocs
$findings += Get-AnchorFindings        -Root $RepoRoot -Docs $docs -IgnoreFiles $ignoreList.Files -IgnoreTokens $ignoreList.Tokens
$findings += Get-CodePathFindings      -Root $RepoRoot -Docs $docs -IgnoreTokens $ignoreList.Tokens -IgnoreFiles $ignoreList.Files
$findings += Get-CodePathFindings      -Root $RepoRoot -Docs $htmlDocs -IgnoreTokens $ignoreList.Tokens -IgnoreFiles $ignoreList.Files -Html
$findings += Get-IndexFindings         -Root $RepoRoot
$findings += Get-UnclosedFenceFindings -Root $RepoRoot -Docs $docs
$findings += Get-BannedReferenceFindings -Root $RepoRoot -Docs $docs
$findings += Get-WorkRemainingFindings -Root $RepoRoot -Docs $docs

# S9 is report-only -- Sweep mode only, and excluded from the Gate exit decision
# below even though Gate mode never computes it in the first place.
if ($Mode -eq 'Sweep') {
    $findings += Get-DeadUrlFindings -Root $RepoRoot -Docs $docs
}

$findings = @($findings | Where-Object { $_ -ne $null })
$bySignal = @($findings | Group-Object Signal | Sort-Object Name)

foreach ($g in $bySignal) {
    Write-Output ("--- {0} ({1}) ---" -f $g.Name, $g.Count)
    foreach ($f in ($g.Group | Sort-Object File, Line)) {
        Write-Output ("  {0}:{1}  -> {2}  ({3})" -f $f.File, $f.Line, $f.Target, $f.Reason)
    }
}

Write-Output ''
foreach ($g in $bySignal) { Write-Output ("  {0}: {1}" -f $g.Name, $g.Count) }
Write-Output ("total: {0} finding(s) across {1} docs" -f $findings.Count, ($docs.Count + $htmlDocs.Count))

$blocking = @($findings | Where-Object { $_.Signal -notin $ReportOnlySignals })
if ($Mode -eq 'Gate' -and $blocking.Count -gt 0) { exit 1 }
exit 0
