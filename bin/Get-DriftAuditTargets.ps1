<#
.SYNOPSIS
    Deterministic target selection for the weekly LLM docs-drift audit.

.DESCRIPTION
    Ranks every tracked doc and returns the top -MaxTargets. One score, two keys:

      1. evidence  - has anything this doc CITES changed, or has code moved in the doc's own
                     directory, since THIS DOC was last audited (not since a global window).
      2. staleness - how long since this doc was last audited. Never-audited sorts first.

    Audit history comes from an append-only ledger (see Get-AuditLedger). A doc the run does
    not reach keeps its old ledger entry, so next week its staleness is higher and its evidence
    window is wider: unaudited work compounds instead of evaporating. -RotationFloor slots are
    reserved for the longest-unaudited docs so a busy week can never starve coverage to zero.

    A doc CITES a file in a backticked token or a markdown link target. A backticked token is
    read relative to the repo root, a link target relative to the doc's directory. A citation
    with a directory that resolves to a tracked file matches a changed file on that path alone. A
    citation with no directory, or one that resolves to no tracked file, matches any changed file
    of that name. A doc's citation of its own path, a citation of a CHANGELOG*.md and a citation
    that starts with a URL scheme are dropped. The doc's own path is never a changed file for it.

    A doc with a usable claim marker is keyed by its claims instead. The ledger text holds
    `audit-claims` markers (read by drift-claims.py beside this script); the newest one for a doc is
    usable when its sha is the doc's latest audit-run sha and every stored range hashes the same
    at that sha. Evidence is then a claim whose range is changed or gone at HEAD (via `stale
    claims: <n>`; a moved range is not stale), or the doc's own file changing since that sha. The
    doc's cited files and its directory's code are not consulted. A doc without a usable marker
    keeps the file-level key above: it has no marker the script parses (it drops one that does not,
    so the doc's newest parseable marker is the one read), that marker's sha is not the doc's latest
    audit-run sha, it holds no claims, or a stored hash does not recompute at its sha (a record
    that `check` returns no range for counts as one that does not). A doc whose latest audit-run
    sha is unreachable ranks as never-audited instead. Without python3 3.11+ on PATH or the script
    beside this one, every doc keeps the file-level key, with one warning line when the ledger holds
    an `audit-claims` marker; a `check` that fails does the same for the docs of its sha, with one
    warning line for that sha. An unusable marker falls back without a warning.

    The LLM never chooses its own workload; this script is the only selector.

    Every target also carries what step 3 of /ouro:drift reads from git history, so the CI session
    holds no history command: `lastCommit`, the ISO 8601 commit time of the doc's last commit, and
    `commits`, the one-line commits made since that commit under the doc's directory (the whole
    repository for a doc at the root), newest first, the doc's own last commit left out. The list is
    cut at -MaxCommits, and `commitsOmitted` counts what the cut left out.

.NOTES
    Excludes: every -ExcludePrefix path (the binding's overlays.drift -- deliberately
    point-in-time trees), CHANGELOG*.md (append-only history), LICENSE. Gitignored trees never appear (git ls-files is the source).
    `overflow` lists the docs that had EVIDENCE but did not fit -- the ones worth reporting as
    unaudited. Docs cut on staleness alone are not listed: 200-odd docs miss the cap every run
    and naming them all would bury the signal. Nothing is lost either way, because an unreached
    doc keeps its ledger entry and outranks next run.

    Every claim the ledger makes is checkable: an entry naming a path that no longer exists is
    pruned, and an entry whose sha is unreachable degrades to never-audited. Both failure modes
    err toward auditing MORE, never toward silently skipping.

    Every target in the JSON carries `staleClaims`, the number of its claims that are stale (0 when
    none), and `claims`, an array with one entry per stale range: statement, path, range (its old
    start and end line) and class (changed or gone); empty when none, and always for a doc on the
    file-level key.

.PARAMETER LedgerFile
    File holding the concatenated audit-run and audit-claims markers harvested from the rolling issue's comments.
    Absent or empty means a bootstrap run: every doc is never-audited.

.PARAMETER MaxCommits
    Most commits each target's `commits` list holds. Default 50.

.PARAMETER AsModule
    Dot-source the function definitions without running anything. For the tests.

.EXAMPLE
    pwsh -File <ouro>/bin/Get-DriftAuditTargets.ps1 -LedgerFile ledger.txt -OutFile targets.json
#>
param(
    [string]$LedgerFile,
    [int]$MaxTargets = 40,
    [int]$RotationFloor = 10,
    [int]$MaxCommits = 50,
    [string]$OutFile,
    [string[]]$ExcludePrefix = @(),
    [switch]$AsModule
)

$ErrorActionPreference = 'Stop'
# git prints the root as UTF-8: decoded with a caller's OEM code page, a non-ASCII root names no
# directory, and the Push-Location into it throws.
$encoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $RepoRoot = (git rev-parse --show-toplevel 2>$null)
}
finally { [Console]::OutputEncoding = $encoding }
if (-not $RepoRoot) { throw 'not inside a git work tree: run from the consumer repo' }
$RepoRoot = $RepoRoot.Trim()

# One marker per completed run -- `<!-- audit-run: sha=<HEAD> docs=a.md,b.md -->` -- appended to
# the rolling issue, never rewritten. A single mutable blob rewritten weekly by an LLM session
# is a silent-corruption vector: a truncated entry would be indistinguishable from a real one.
# Appended entries stay independently checkable, and a bad one cannot take the others with it.
$AuditRunPattern = '<!--\s*audit-run:\s*sha=([0-9a-fA-F]+)\s+docs=([^>]*?)\s*-->'

# Fold markers oldest-to-newest into path -> sha of its most recent audit. Later wins.
# -ValidPaths prunes entries for docs that have since been renamed or deleted; without it the
# ledger grows orphans forever and a renamed doc silently keeps its predecessor's audit date.
function Get-AuditLedger {
    param([string]$Text, [string[]]$ValidPaths)
    $ledger = @{}
    if ([string]::IsNullOrWhiteSpace($Text)) { return $ledger }
    $valid = $null
    if ($PSBoundParameters.ContainsKey('ValidPaths')) {
        $valid = [System.Collections.Generic.HashSet[string]]::new([string[]]$ValidPaths, [System.StringComparer]::OrdinalIgnoreCase)
    }
    foreach ($m in [regex]::Matches($Text, $AuditRunPattern)) {
        $sha = $m.Groups[1].Value
        foreach ($p in ($m.Groups[2].Value -split ',')) {
            $path = $p.Trim()
            if (-not $path) { continue }
            if ($valid -and -not $valid.Contains($path)) { continue }
            $ledger[$path] = $sha
        }
    }
    return $ledger
}

# Files a doc CITES: backticked path-ish tokens and markdown link targets. A backticked token is
# read relative to the repo root, a link target relative to the doc's directory. A citation that has
# a directory and resolves to a tracked file is keyed "/<path>" and matches a changed file on that
# path alone. A citation with no directory, or one that resolves to no tracked file, is keyed by its
# file name and matches any changed file of that name. A doc's citation of its own path, a citation
# of a CHANGELOG*.md and a citation that starts with a URL scheme are dropped. The doc's own path is
# never a changed file for it. Deliberately NOT a substring scan of the body -- "mentions the string
# App.sln somewhere" matched 18 docs and identified none of them. A citation is a claim about a
# file; a passing mention is not.
$CiteCodeExt = '\.(cs|xaml|ps1|csproj|sln|yml|yaml|json|resx|props|targets|c|h|txt|py|toml|sh|md|html)$'

# $Target joined to $BaseDir ('' for the repo root) with . and .. folded out; a leading / reads
# from the root; $null when the path climbs above the root.
function Resolve-CitedPath {
    param([string]$Target, [string]$BaseDir)
    $joined = if ($Target.StartsWith('/')) { $Target } else { "$BaseDir/$Target" }
    $parts = [System.Collections.Generic.List[string]]::new()
    foreach ($seg in $joined.Split('/')) {
        if ($seg -eq '' -or $seg -eq '.') { continue }
        if ($seg -ne '..') { $parts.Add($seg); continue }
        if ($parts.Count -eq 0) { return $null }
        $parts.RemoveAt($parts.Count - 1)
    }
    return ($parts -join '/')
}

# The key one citation target contributes, or $null when it is not a citation.
function Get-CitationKey {
    param([string]$Target, [string]$BaseDir, [string]$DocPath, $TrackedSet)
    if ($Target -match '^[a-zA-Z][a-zA-Z0-9+._-]*:') { return $null }
    $norm = $Target -replace '\\', '/'
    $leaf = ($norm -split '/')[-1]
    if ($leaf -notmatch $CiteCodeExt) { return $null }
    if ($leaf -match '^CHANGELOG.*\.md$') { return $null }
    $path = Resolve-CitedPath $norm $BaseDir
    if ($path -and $path -ceq $DocPath) { return $null }
    if ($norm -notmatch '/') { return $leaf }
    if ($path -and $TrackedSet.Contains($path)) { return "/$path" }
    return $leaf
}

function Get-DocCitations {
    param([string]$Text, [string]$DocPath = '', [string[]]$Tracked = @())
    $set = @{}
    if ([string]::IsNullOrWhiteSpace($Text)) { return $set }
    $trackedSet = [System.Collections.Generic.HashSet[string]]::new([string[]]$Tracked, [System.StringComparer]::Ordinal)
    $docDir = if ($DocPath.Contains('/')) { $DocPath.Substring(0, $DocPath.LastIndexOf('/')) } else { '' }
    foreach ($m in [regex]::Matches($Text, '`([^`\r\n]+)`')) {
        $t = $m.Groups[1].Value.Trim()
        if ($t -match '[ <>{}*]') { continue }
        $key = Get-CitationKey -Target $t -BaseDir '' -DocPath $DocPath -TrackedSet $trackedSet
        if ($key) { $set[$key] = $true }
    }
    foreach ($m in [regex]::Matches($Text, '\]\(([^)\s#]+)')) {
        $key = Get-CitationKey -Target $m.Groups[1].Value -BaseDir $docDir -DocPath $DocPath -TrackedSet $trackedSet
        if ($key) { $set[$key] = $true }
    }
    return $set
}

# The keys a changed path can match: "/<path>" for a path citation, its file name for a name
# citation.
function Get-ChangedKeys {
    param([string[]]$Changed)
    foreach ($c in $Changed) {
        if (-not $c) { continue }
        "/$c"
        ($c -split '/')[-1]
    }
}

# A changed file identifies a subject only if few docs cite it. A shared resource file cited by
# 17 docs tells us nothing about any of them; a widget's code-behind cited by 4 tells us
# plenty. The cap generalises the hand-kept stop list, which could never track a moving repo.
# -ChangedLeaves holds Get-ChangedKeys' keys, and the count is of docs citing each key.
function Get-DistinctiveNames {
    param([string[]]$ChangedLeaves, $CitationsByDoc, [int]$FanOutCap = 8)
    $keep = @()
    foreach ($leaf in ($ChangedLeaves | Sort-Object -Unique)) {
        $n = 0
        foreach ($doc in $CitationsByDoc.Keys) { if ($CitationsByDoc[$doc].ContainsKey($leaf)) { $n++ } }
        if ($n -gt 0 -and $n -le $FanOutCap) { $keep += $leaf }
    }
    return $keep
}

# The first three of $Keys a doc's citations hold, as the paths and names they cite.
function Get-CitedChanges {
    param($Citations, $Keys)
    return @($Keys | Where-Object { $_ -and $Citations.ContainsKey($_) } | Select-Object -First 3 | ForEach-Object { $_ -replace '^/', '' })
}

# How many changed files supply each of Get-ChangedKeys' keys: built once per sha group, since
# rebuilt for every doc it costs seconds at 200 docs and 3,000 changed files.
function Get-ChangedKeyCounts {
    param([string[]]$Keys)
    $counts = @{}
    foreach ($k in $Keys) { $counts[$k] = 1 + $counts[$k] }
    return $counts
}

# The cited files of $Citations among $Distinctive that changed, the doc's own edit left out: a
# key of the doc's own path counts only while another changed file supplies it too.
function Get-DocHits {
    param($Citations, $Distinctive, [hashtable]$KeyCounts, [bool]$DocChanged, [string]$DocPath)
    $own = if ($DocChanged) { @("/$DocPath", ($DocPath -split '/')[-1]) } else { @() }
    return @(Get-CitedChanges -Citations $Citations -Keys @($Distinctive | Where-Object { $_ -and $KeyCounts[$_] - [int]($own -contains $_) -gt 0 }))
}

# The ranking, pure on purpose: every selection bug this file has had lived in arithmetic like
# this, where a fixture-free test catches it on the first run. -RotationFloor is that lesson as
# code -- evidence-bearing docs would otherwise fill every slot in a busy week and leave quiet
# docs at zero coverage indefinitely.
function Select-AuditTargets {
    param(
        [string[]]$Docs,
        [hashtable]$Evidence,        # path -> reason string; absent means no evidence
        [hashtable]$WeeksSince,      # path -> weeks since last audit ([double]::MaxValue = never)
        [int]$MaxTargets = 40,
        [int]$RotationFloor = 10
    )
    $rank = foreach ($d in $Docs) {
        $w = if ($WeeksSince.ContainsKey($d)) { $WeeksSince[$d] } else { [double]::MaxValue }
        [pscustomobject]@{
            path      = $d
            reason    = if ($Evidence.ContainsKey($d)) { 'evidence' } else { 'stale' }
            via       = if ($Evidence.ContainsKey($d)) { $Evidence[$d] } else { $null }
            weeks     = $w
            hasEvid   = $Evidence.ContainsKey($d)
        }
    }
    $rank = @($rank)
    if ($rank.Count -eq 0) { return @() }

    $floor = [Math]::Min($RotationFloor, $MaxTargets)
    $byStale = @($rank | Sort-Object @{e = { $_.weeks }; Descending = $true}, path)
    $reserved = @($byStale | Select-Object -First $floor)
    $reservedPaths = @{}
    foreach ($r in $reserved) { $reservedPaths[$r.path] = $true }

    $room = $MaxTargets - $reserved.Count
    $rest = @($rank | Where-Object { -not $reservedPaths.ContainsKey($_.path) } |
        Sort-Object @{e = { $_.hasEvid }; Descending = $true}, @{e = { $_.weeks }; Descending = $true}, path)
    $chosen = @($reserved) + @($rest | Select-Object -First ([Math]::Max(0, $room)))

    return @($chosen | Sort-Object @{e = { $_.hasEvid }; Descending = $true}, @{e = { $_.weeks }; Descending = $true}, path)
}

# The one call into the claims script: drift-claims.py beside this file, run by python3 with UTF-8
# on both pipes. Throws when python3 or the script is missing or the script exits nonzero (a
# python below 3.11 exits with its floor message). The suite stubs this function.
function Invoke-DriftClaims {
    param([string[]]$Arguments, [string]$InputText = '')
    $script = Join-Path $PSScriptRoot 'drift-claims.py'
    if (-not (Test-Path -LiteralPath $script)) { throw "drift-claims.py is not beside the selector ($PSScriptRoot)" }
    $python = Get-Command python3 -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $python) { throw 'python3 is not on PATH' }
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    $psi = [System.Diagnostics.ProcessStartInfo]::new($python.Source)
    foreach ($a in @($script) + $Arguments) { $psi.ArgumentList.Add($a) }
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardInputEncoding = $utf8
    $psi.StandardOutputEncoding = $utf8
    $psi.StandardErrorEncoding = $utf8
    $psi.Environment['PYTHONDONTWRITEBYTECODE'] = '1'
    $proc = [System.Diagnostics.Process]::Start($psi)
    $errTask = $proc.StandardError.ReadToEndAsync()
    $proc.StandardInput.Write($InputText)
    $proc.StandardInput.Close()
    $stdout = $proc.StandardOutput.ReadToEnd()
    $proc.WaitForExit()
    if ($proc.ExitCode -ne 0) { throw "drift-claims.py $($Arguments[0]) exited $($proc.ExitCode): $($errTask.Result.Trim())" }
    return $stdout
}

# The claim key of one doc, or $null when the doc keeps the file-level key. The marker counts only
# when its sha is the doc's audit-run sha and every stored range hashes the same at that sha
# (-BaseRanges, the `check` ranges at that sha). -HeadRanges are the same records checked at HEAD:
# a changed or gone range makes its claim stale, a moved one does not. Evidence is a stale claim or
# the doc's own blob changing since the marker's sha, which new text has no claims to show.
function Get-ClaimKey {
    param($Marker, [string]$LedgerSha, $BaseRanges, $HeadRanges, [bool]$DocChanged)
    if (-not $Marker -or $Marker.sha -cne $LedgerSha.ToLowerInvariant()) { return $null }
    $recorded = @($Marker.claims)
    $base = @($BaseRanges)
    if ($recorded.Count -eq 0 -or $base.Count -ne $recorded.Count -or @($base | Where-Object { $_.class -cne 'same' }).Count -gt 0) { return $null }
    $stale = @($HeadRanges | Where-Object { $_.class -ceq 'changed' -or $_.class -ceq 'gone' })
    $names = @($stale | ForEach-Object { $_.statement } | Sort-Object -Unique -CaseSensitive)
    $via = if ($names.Count -gt 0) { "stale claims: $($names.Count)" } elseif ($DocChanged) { 'doc text changed since its claims were recorded' } else { $null }
    return [pscustomobject]@{
        Via        = $via
        StaleClaims = $names.Count
        Claims     = @($stale | ForEach-Object { [ordered]@{ statement = $_.statement; path = $_.path; range = @($_.start, $_.end); class = $_.class } })
    }
}

# doc -> @{ Marker; Base; Head } for each doc of -ShaGroups (sha -> docs) whose newest audit-claims
# marker in -LedgerText carries that group's sha. Base and Head are the ranges `check` returns for
# the marker's records at that sha and at -Head. A script that cannot run leaves the table empty
# for the docs it covers, with one warning line each time.
function Get-ClaimRanges {
    param([string]$LedgerText, $ShaGroups, [string]$Head)
    $found = @{}
    if ($LedgerText -notmatch 'audit-claims:') { return $found }
    try { $markers = @((Invoke-DriftClaims -Arguments @('parse') -InputText $LedgerText) | ConvertFrom-Json) }
    catch { Write-Warning "claim markers not read; every doc keeps the file-level key: $($_.Exception.Message)"; return $found }
    $newest = @{}
    foreach ($m in $markers) { $newest[[string]$m.doc] = $m }
    foreach ($sha in $ShaGroups.Keys) {
        $cand = @($ShaGroups[$sha] | Where-Object { $newest.ContainsKey($_) -and $newest[$_].sha -ceq $sha.ToLowerInvariant() })
        if ($cand.Count -eq 0) { continue }
        $records = @(foreach ($d in $cand) {
            foreach ($c in @($newest[$d].claims)) {
                [ordered]@{ doc = $d; statement = $c.statement; path = $c.path; start = $c.start; end = $c.end; sha256 = $c.sha256 }
            }
        })
        $stdin = ConvertTo-Json -InputObject $records -Depth 4 -Compress
        try {
            $base = @((Invoke-DriftClaims -Arguments @('check', '--base', $sha, '--rev', $sha, '--cwd', $RepoRoot) -InputText $stdin | ConvertFrom-Json).ranges)
            $at = @((Invoke-DriftClaims -Arguments @('check', '--base', $sha, '--rev', $Head, '--cwd', $RepoRoot) -InputText $stdin | ConvertFrom-Json).ranges)
        }
        catch { Write-Warning "claims of $sha not checked; its docs keep the file-level key: $($_.Exception.Message)"; continue }
        foreach ($d in $cand) {
            $found[$d] = @{
                Marker = $newest[$d]
                Base   = @($base | Where-Object { $_.doc -ceq $d })
                Head   = @($at | Where-Object { $_.doc -ceq $d })
            }
        }
    }
    return $found
}

# What step 3 of /ouro:drift reads for one doc: its last commit and the commits since it under its
# directory. A sha range, so the doc's own last commit is out of the list whatever second it landed
# on; a literal pathspec, so a directory named with a glob character names itself. A doc with no
# commit yet has no time and no list.
function Get-DocHistory {
    param([string]$Doc, [string]$Head, [int]$Max)
    try {
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $last = "$(git log -1 '--format=%H %cI' -- ":(literal)$Doc")".Trim()
        if ($LASTEXITCODE -ne 0) { throw "git log -1 on $Doc failed (exit $LASTEXITCODE)" }
        if (-not $last) { return @{ Time = $null; Commits = @(); Omitted = 0 } }
        $sha, $time = $last -split ' ', 2
        $dir = (Split-Path $Doc -Parent) -replace '\\', '/'
        $lines = if ($dir) { @(git log --oneline --no-decorate "$sha..$Head" -- ":(literal)$dir") } else { @(git log --oneline --no-decorate "$sha..$Head") }
        if ($LASTEXITCODE -ne 0) { throw "git log $sha..$Head on $Doc's directory failed (exit $LASTEXITCODE)" }
        $lines = @($lines | Where-Object { $_ })
        return @{ Time = $time; Commits = @($lines | Select-Object -First $Max); Omitted = [Math]::Max(0, $lines.Count - $Max) }
    }
    finally { [Console]::OutputEncoding = $encoding }
}

if ($AsModule) { return }

Push-Location -LiteralPath $RepoRoot
try {
    $head = (git rev-parse HEAD).Trim()

    $excluded = @($ExcludePrefix | ForEach-Object { ($_ -replace '/\*\*$', '/') })
    # -z and UTF-8, here and for the changed-file lists below: plain output C-quotes a path holding
    # a non-ASCII character, a backslash, a double quote or a tab, and the quoted string names no
    # file. The caller's encoding comes back after each read.
    try {
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $tracked = @((@(git ls-files -z '*.md') -join "`n").Split([char]0, [System.StringSplitOptions]::RemoveEmptyEntries))
        $allTracked = @((@(git ls-files -z) -join "`n").Split([char]0, [System.StringSplitOptions]::RemoveEmptyEntries))
    }
    finally { [Console]::OutputEncoding = $encoding }
    $docs = $tracked | Where-Object {
        $path = $_
        -not ($excluded | Where-Object { $path.StartsWith($_) }) -and
        $_ -notmatch 'CHANGELOG' -and
        $_ -ne 'LICENSE'
    } | Sort-Object

    $ledgerText = ''
    if ($LedgerFile -and (Test-Path -LiteralPath $LedgerFile)) {
        $ledgerText = Get-Content -LiteralPath $LedgerFile -Raw
    }
    $ledger = Get-AuditLedger -Text $ledgerText -ValidPaths $docs
    $bootstrap = ($ledger.Count -eq 0)

    $citations = @{}
    foreach ($d in $docs) {
        $citations[$d] = if (Test-Path -LiteralPath $d) { Get-DocCitations (Get-Content -LiteralPath $d -Raw) -DocPath $d -Tracked $allTracked } else { @{} }
    }

    # One diff per DISTINCT ledger sha, not one per doc: docs audited in the same run share a
    # sha, so this is a handful of git calls rather than 200.
    $shaGroups = @{}
    foreach ($d in $docs) {
        $sha = $ledger[$d]
        if (-not $sha) { continue }
        # Unreachable sha (history rewrite, shallow clone) degrades to never-audited, which
        # ranks the doc higher rather than dropping it.
        git cat-file -e "$sha^{commit}" 2>$null
        if ($LASTEXITCODE -ne 0) { continue }
        if (-not $shaGroups.ContainsKey($sha)) { $shaGroups[$sha] = @() }
        $shaGroups[$sha] += $d
    }

    $claimRanges = Get-ClaimRanges -LedgerText $ledgerText -ShaGroups $shaGroups -Head $head

    $evidence  = @{}
    $claimInfo = @{}
    $weeksSince = @{}
    foreach ($sha in $shaGroups.Keys) {
        try {
            [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
            $changed = @((@(git diff -z --name-only "$sha..HEAD") -join "`n").Split([char]0, [System.StringSplitOptions]::RemoveEmptyEntries))
        }
        finally { [Console]::OutputEncoding = $encoding }
        $changedKeys = @(Get-ChangedKeys $changed)
        $keyCounts = Get-ChangedKeyCounts $changedKeys
        $codeDirs = @($changed | Where-Object { $_ -notlike '*.md' } |
            ForEach-Object { (Split-Path $_ -Parent) -replace '\\', '/' } | Where-Object { $_ } | Sort-Object -Unique)

        # Fan-out is measured across the WHOLE corpus, never this sha's group: "how many docs
        # cite this file" is a property of the repo, and scoping it to a group would make the
        # cap depend on how many docs happened to share an audit run.
        $distinctive = Get-DistinctiveNames -ChangedLeaves $changedKeys -CitationsByDoc $citations

        $iso = (git log -1 --format=%cI $sha).Trim()
        $weeks = if ($iso) { ([datetimeoffset]::Now - [datetimeoffset]::Parse($iso)).TotalDays / 7 } else { [double]::MaxValue }

        foreach ($d in $shaGroups[$sha]) {
            $weeksSince[$d] = $weeks
            $docChanged = $changed -ccontains $d
            if ($claimRanges.ContainsKey($d)) {
                $cr = $claimRanges[$d]
                $ck = Get-ClaimKey -Marker $cr.Marker -LedgerSha $sha -BaseRanges $cr.Base -HeadRanges $cr.Head -DocChanged $docChanged
                if ($ck) {
                    $claimInfo[$d] = $ck
                    if ($ck.Via) { $evidence[$d] = $ck.Via }
                    continue
                }
            }
            $hit = @(Get-DocHits -Citations $citations[$d] -Distinctive $distinctive -KeyCounts $keyCounts -DocChanged $docChanged -DocPath $d)
            if ($hit.Count -gt 0) {
                $evidence[$d] = "cites changed: $($hit -join ', ')"
                continue
            }
            $dd = (Split-Path $d -Parent) -replace '\\', '/'
            # Code beside the doc, not merely a neighbouring doc edit -- requiring a non-.md
            # sibling took this rule from 104 docs to 6, all of the difference being noise.
            if ($dd -and $codeDirs -contains $dd) { $evidence[$d] = 'code changed in its own directory' }
        }
    }

    $targets = Select-AuditTargets -Docs $docs -Evidence $evidence -WeeksSince $weeksSince `
        -MaxTargets $MaxTargets -RotationFloor $RotationFloor
    $keep = @{}
    foreach ($t in $targets) { $keep[$t.path] = $true }
    $overflow = @($docs | Where-Object { -not $keep.ContainsKey($_) -and ($evidence.ContainsKey($_)) })
    $history = @{}
    foreach ($t in $targets) { $history[$t.path] = Get-DocHistory -Doc $t.path -Head $head -Max $MaxCommits }

    $result = [ordered]@{
        head       = $head
        bootstrap  = $bootstrap
        ledgerSize = $ledger.Count
        targets    = @($targets | ForEach-Object {
            [ordered]@{
                path   = $_.path
                reason = $_.reason
                via    = $_.via
                weeksSinceAudit = if ($_.weeks -eq [double]::MaxValue) { $null } else { [Math]::Round($_.weeks, 1) }
                staleClaims     = if ($claimInfo.ContainsKey($_.path)) { $claimInfo[$_.path].StaleClaims } else { 0 }
                claims          = @(if ($claimInfo.ContainsKey($_.path)) { $claimInfo[$_.path].Claims })
                lastCommit      = $history[$_.path].Time
                commits         = @($history[$_.path].Commits)
                commitsOmitted  = $history[$_.path].Omitted
            }
        })
        overflow   = $overflow
    }
    $json = $result | ConvertTo-Json -Depth 5
    if ($OutFile) { $json | Set-Content -Path $OutFile -Encoding utf8 } else { $json }
}
finally {
    Pop-Location
}
