<#
.SYNOPSIS
    The anchor parser shared by the anchor and shape gates. Dot-source, then call
    Get-AnchorFindings -Text <body>.
.DESCRIPTION
    A library, not a gate: this file defines one function and runs nothing on its own, so
    it is dot-sourced rather than invoked. What the parser accepts and what it reports is
    documented on the function it defines -- Get-Help Get-AnchorFindings -Full.
#>


<#
.SYNOPSIS
    The anchor parser shared by the anchor and shape gates. Dot-source, then call
    Get-AnchorFindings -Text <body>.
.DESCRIPTION
    One parser so the two gates can never disagree about what counts as an anchor:
    Test-AgentReadyAnchors.ps1 checks that parsed anchors still verify at HEAD,
    Test-AgentReadyShape.ps1 checks that at least one parses at all. Callers set
    the working directory to the repo root first -- the path and fragment checks
    run git against the current directory.

    A citation into ANOTHER repository is written <repo>:<path> with its fragment in
    backticks (ouro docs/contract.md section 3), because this gate verifies only the
    repo it runs in. The scheme-prefix rule below drops the path, but only for a repo
    name starting with a letter and made of [A-Za-z0-9+.-]; an underscore or a leading
    digit falls through and is reported as a path. A backticked fragment is skipped
    only where it is not ALSO path-shaped: backticks INSIDE double quotes are stripped
    and grepped as usual (rule 2), and a whitespace-free backticked token with a slash
    and a suffix is checked as a path (rule 1). Neither spelling counts toward the
    shape gate's one-parseable-anchor minimum.

    An ANCHOR LINE is a list item, bulleted or numbered at any indent, whose first backticked
    span passes rule 1's filters and names one tracked file, matched literally -- spelled as the
    index spells it, or by a unique literal suffix match, and never as a pattern. On it, every
    double-quoted fragment in the length bounds is grepped in that one file, whatever its
    characters, and a miss is a finding naming the file. On every other line a fragment is
    grepped only if it looks like code, repo-wide, and a miss holding a backtick is skipped as a
    prose-code composite. A continuation line under an item is judged by itself.
#>

function Get-AnchorFindings {
    param([string]$Text)

    $findings = @()

    # The `Doc impact on close:` declaration names DESTINATIONS -- docs the change will create
    # or rewrite -- so a path there may legitimately not exist at HEAD, and a quotation there
    # may be the text a doc will be reduced to rather than text that is in it today. Scanning
    # it reports a perfectly fresh issue as stale (seen on three at once: two naming a README
    # the issue itself creates, one quoting a sentence the doc will collapse to). Anchors are
    # the evidence block; the declaration is a promise about the future, so it is excluded --
    # in both spellings, the `## Doc impact on close` heading and the inline one-liner.
    $Text = [regex]::Replace($Text, '(?ms)^#{1,6}[ \t]*Doc impact on close\b.*?(?=^#{1,6}[ \t]|\z)', '')
    $Text = [regex]::Replace($Text, '(?m)^Doc impact on close\b.*$', '')

    # 1. Backticked repo paths must resolve. A span containing whitespace (a command line
    # or prose) is not a path anchor -- whitespace spans follow Get-DocCitations' rule in
    # Get-DriftAuditTargets.ps1. A dead path cited inside a command line is deliberately
    # out of scope: a token that does not resolve there is as often a to-be-created
    # deliverable as a stale path.
    function Get-PathCandidate([string]$Span) {
        $c = $Span.Trim()
        if ($c -match '\s') { return }
        if (-not ($c -match '[/\\]' -and $c -match '\.[A-Za-z0-9]{1,7}(\z|[:#])')) { return }
        # An anchor is a relative repo path, verifiable at HEAD. Everything else is skipped
        # here -- before the [:#] split, while a token still carries the colon its scheme or
        # drive letter is recognized by: env-var ($, %) and <placeholder> tokens, which name
        # nothing at HEAD; rooted (/x, \x), UNC (\\host\x), home (~/x) and parent-escaping
        # (../x) tokens, and any scheme- or drive-qualified token (https://x, file:///x,
        # C:/x, C:x), which name the runner's filesystem rather than the repo and would make
        # the verdict machine-dependent. The prefix is anchored and its name class excludes
        # / and \, so a colon after the first separator (the src/x.ps1:20 line-anchor form)
        # never matches; a colon inside the first segment is read as a scheme. Only a
        # leading .. is caught -- an interior escape (src/../../x) still reaches Test-Path.
        if ($c -match '^(\$|%|<|~[\\/]|\.\.[\\/]|[\\/]|[A-Za-z][A-Za-z0-9+.-]*:)') { return }
        ($c -split '[:#]')[0].Trim() | Where-Object { $_ }
    }
    # Backslash-to-slash, leading-./ stripped: the form the index and both literal reads below
    # compare against.
    function Get-NormalizedPath([string]$Path) {
        $Path -replace '\\', '/' -replace '^(\./)+', ''
    }
    # The exact index entry, read with -z and as UTF-8, as Get-DriftAuditTargets.ps1 reads the
    # index: plain output C-quotes a non-ASCII name, and the quoted string names no file. The
    # caller's encoding comes back after each read.
    function Test-ExactTracked([string]$Path) {
        $saved = [Console]::OutputEncoding
        try {
            [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
            $exact = @((@(git ls-files -z -- ":(literal)$Path" 2>$null) -join "`n").Split([char]0, [StringSplitOptions]::RemoveEmptyEntries)) -ceq $Path
        }
        finally { [Console]::OutputEncoding = $saved }
        return $exact.Count -eq 1
    }
    # The whole tracked index, listed once per call so both suffix reads below compare against
    # the same spellings with an ordinal, case-sensitive ends-with -- git has no literal-suffix
    # pathspec, so the match is made here rather than in one.
    $savedEnc = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $trackedFiles = @((@(git ls-files -z 2>$null) -join "`n").Split([char]0, [StringSplitOptions]::RemoveEmptyEntries))
    }
    finally { [Console]::OutputEncoding = $savedEnc }
    function Get-LiteralSuffixHits([string]$Suffix) {
        $trackedFiles | Where-Object { $_.EndsWith($Suffix, [System.StringComparison]::Ordinal) }
    }
    # The tracked file a candidate names: the entry spelled exactly so, else the one tracked file
    # it is a unique literal suffix of; nothing when none or several are. Never a pattern -- an
    # anchor line never matches a glob span.
    function Resolve-AnchorPath([string]$Path) {
        $p = Get-NormalizedPath $Path
        if (Test-ExactTracked $p) { return $p }
        $hits = @(Get-LiteralSuffixHits $p)
        if ($hits.Count -eq 1) { $hits[0] }
    }
    $candidates = @([regex]::Matches($Text, '`([^`\r\n]+)`') | ForEach-Object { Get-PathCandidate $_.Groups[1].Value })
    $pathMatches = $candidates | Sort-Object -Unique
    # Each candidate as the tracked file it names, else as written: the set a footprint reads.
    # Distinct and ordinal-sorted, since a path is compared case-sensitively.
    $pathSet = [System.Collections.Generic.SortedSet[string]]::new([StringComparer]::Ordinal)
    foreach ($c in ($candidates | Select-Object -Unique)) {
        $resolvedPath = Resolve-AnchorPath $c
        [void]$pathSet.Add($(if ($resolvedPath) { $resolvedPath } else { Get-NormalizedPath $c }))
    }
    foreach ($p in $pathMatches) {
        $normalized = Get-NormalizedPath $p
        if (Test-ExactTracked $normalized) { continue }
        # Test-Path is a wildcard read, kept as the fallback so a root-relative glob span
        # (src/*.ps1) still resolves. Only when both the exact entry and Test-Path miss does the
        # literal suffix read below run.
        if (-not (Test-Path $p)) {
            # Bodies often write paths relative to a subtree (Sub/Dir/README.md
            # for Top/Sub/Dir/README.md). A unique literal tracked-file suffix match
            # counts as resolved; zero matches is the real dead-anchor signal.
            $suffixHits = @(Get-LiteralSuffixHits $normalized)
            if ($suffixHits.Count -eq 0) {
                $findings += "path does not resolve: '$p'"
            } elseif ($suffixHits.Count -gt 1) {
                $findings += "path ambiguous ($($suffixHits.Count) tracked files end with '$p') - body should use the repo-relative path"
            }
        }
    }

    # 2. Quoted fragments must literal-grep in tracked text files. A fragment quoting a
    # source line that itself contains quotes is written with \" escapes; unescape before
    # grepping, and count the nested quote as a code-ish char (prose does not nest quotes).
    # Enclosing backticks are body markup, not source text -- strip them before grepping.
    # Quotes pair left to right; the bounds apply after the unescape. A bound in the pattern
    # leaves a phrase unpaired, lending its closing quote to the next one; the trailing \? and
    # the end-of-line branch are what keep the scan linear on a line whose quotes are all escaped.
    function Get-QuotedFragments([string]$Chunk) {
        [regex]::Matches($Chunk, '"(?<frag>(?:[^"\\\r\n]|\\.)*)\\?(?<close>"|(?=[\r\n]|\z))') |
            Where-Object   { $_.Groups['close'].Value -eq '"' } |
            ForEach-Object { $_.Groups['frag'].Value -replace '\\"', '"' } |
            Where-Object   { $_.Length -ge 12 -and $_.Length -le 120 } |
            ForEach-Object { $_ -replace '^`+' -replace '`+$' }
    }
    # An anchor-shaped line -- a list item, at any indent, whose first backticked span passes
    # rule 1's filters and resolves -- cites one file, and every fragment on it is verified there,
    # whatever its characters: the code-like filter below guesses at prose, and on such a line
    # the body has said what it quotes. Every other line keeps the filter and the repo-wide grep.
    # Keyed exactly: fragments and paths are case-sensitive to the grep. A line with no quote
    # has nothing to verify, so it is not resolved, and each candidate is resolved once.
    $anchorFrags = [System.Collections.Specialized.OrderedDictionary]::new([StringComparer]::Ordinal)
    $resolved = [System.Collections.Generic.Dictionary[string, object]]::new([StringComparer]::Ordinal)
    $otherLines = foreach ($line in $Text -split '\r?\n') {
        $file = $null
        if ($line.Contains('"') -and $line -match '^\s*(?:[-*+]|\d+[.)])\s' -and $line -match '`([^`\r\n]+)`') {
            $candidate = Get-PathCandidate $Matches[1]
            if ($candidate) {
                if (-not $resolved.ContainsKey($candidate)) { $resolved[$candidate] = Resolve-AnchorPath $candidate }
                $file = $resolved[$candidate]
            }
        }
        if (-not $file) { $line; continue }
        foreach ($frag in Get-QuotedFragments $line) { $anchorFrags["$file`n$frag"] = @($file, $frag) }
    }
    $fragMatches = Get-QuotedFragments ($otherLines -join "`n") |
        Where-Object {
            # Only fragments that look like code: contain a code-ish char, and are not prose
            # (prose fragments have many spaces and no code punctuation).
            ($_ -match '[(){}\[\];=_."<>&:!]') -and -not ($_ -match '^[A-Za-z ,''-]+$')
        } | Sort-Object -Unique
    $checkedFrags = 0
    foreach ($pair in $anchorFrags.Values) {
        $file, $frag = $pair
        $hit = git grep -F -e "$frag" -- ":(literal)$file" 2>$null | Select-Object -First 1
        $checkedFrags++
        if (-not $hit) {
            $findings += "fragment no longer greps in '$file': `"$frag`""
        }
    }
    foreach ($frag in $fragMatches) {
        # no --max-count: git before 2.38 rejects it, and the first hit is all that is read
        $hit = git grep -F -- "$frag" 2>$null | Select-Object -First 1
        # Interior backticks may be verbatim (docs contain literal backticks) -- trust a
        # grep that hits. One that misses is a prose-code composite, not an anchor: skip
        # it and do not count it, rather than flag it.
        if (-not $hit -and $frag -match '`') { continue }
        $checkedFrags++
        if (-not $hit) {
            $findings += "fragment no longer greps: `"$frag`""
        }
    }

    [pscustomobject]@{
        Findings  = $findings
        PathCount = @($pathMatches).Count
        Paths     = [string[]]@($pathSet)
        FragCount = $checkedFrags
    }
}
