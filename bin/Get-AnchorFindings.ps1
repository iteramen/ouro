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

    -Ref <commit-ish> reads the repository at that commit instead of the working tree: the
    tracked paths come from git ls-tree, a root-relative wildcard span or a directory resolves
    when any path in that list matches it, and a fragment is grepped with git grep at the ref.
    The checkout's files, index and branch are never read or moved, so a landing checks its
    anchors against origin's default branch from any checkout. Without it, the working tree and
    index are read as before.

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
    characters, and a miss is a finding naming the file. A span whose brace set expands to
    several tracked files cites each, and a fragment is a miss only when none holds it. On every
    other line a fragment is grepped only if it looks like code, repo-wide, and a miss holding a
    backtick is skipped as a prose-code composite. A continuation line under an item is judged by itself.
#>

function Get-AnchorFindings {
    param([string]$Text, [string]$Ref)

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
    # or prose) is not a path anchor. A dead path cited inside a command line is deliberately
    # out of scope: a token that does not resolve there is as often a to-be-created
    # deliverable as a stale path.
    # A span's result is kept for the body, keyed by its exact text: the anchor-line pass below
    # asks for the same spans, and a span cited twice costs no second expansion, as its paths
    # are resolved once.
    function Get-PathCandidate([string]$Span) {
        if (-not $script:spanPaths.ContainsKey($Span)) { $script:spanPaths[$Span] = @(Get-PathCandidateOnce $Span) }
        $script:spanPaths[$Span]
    }
    function Get-PathCandidateOnce([string]$Span) {
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
        $skip = '^(\$|%|<|~[\\/]|\.\.[\\/]|[\\/]|[A-Za-z][A-Za-z0-9+.-]*:)'
        if ($c -match $skip) { return }
        # An alternative is a path of its own, so it meets the same prefix rule.
        $path = ($c -split '[:#]')[0].Trim()
        try { $paths = @(Expand-BraceSet $path) } catch [System.OverflowException] { $script:cappedSpans += $path; return }
        $paths | Where-Object { $_ -and $_ -notmatch $skip }
    }
    # A brace set with a top-level comma expands to one path per alternative, nested sets too,
    # each then checked as a path of its own. An unbalanced or comma-less set stays literal and
    # the scan goes on to the next one. The expansions of a body's brace sets past 64 are a
    # finding on the span that crosses, not a check: each one costs a git read. A span with no
    # set counts none.
    function Expand-BraceSet([string]$Path) {
        # One pass pairs each '{' with its '}' and keeps the commas at each set's own depth; a
        # '{' with no close, and a set with no comma of its own, stay literal characters. The
        # expansion then walks the sets this pass found and rescans nothing, so an unbalanced
        # span costs one pass however many braces it holds.
        $stack = [System.Collections.Generic.Stack[int]]::new()
        $commaAt = @{}
        $sets = @{}
        for ($i = 0; $i -lt $Path.Length; $i++) {
            switch ($Path[$i]) {
                '{' { $stack.Push($i) }
                '}' {
                    if ($stack.Count) {
                        $o = $stack.Pop()
                        if ($commaAt.ContainsKey($o)) { $sets[$o] = @{ close = $i; commas = @($commaAt[$o]) } }
                    }
                }
                ',' {
                    if ($stack.Count) {
                        $top = $stack.Peek()
                        if (-not $commaAt.ContainsKey($top)) { $commaAt[$top] = [System.Collections.Generic.List[int]]::new() }
                        $commaAt[$top].Add($i)
                    }
                }
            }
        }
        # A set with a comma adds at least one leaf, so 64 of them are past the cap before any
        # expansion; the walk below nests one call per set, and that bound keeps it shallow.
        if ($sets.Count -gt 63) { throw [System.OverflowException]::new('brace expansion cap') }
        # The leaves stream out one at a time, so the cap stops the work at the 65th and not after
        # every combination has been built.
        $keys = @($sets.Keys | Sort-Object)
        Expand-Range $Path $sets $keys 0 $Path.Length | ForEach-Object {
            if ($sets.Count -gt 0 -and ++$script:braceLeaves -gt 64) { throw [System.OverflowException]::new('brace expansion cap') }
            $_
        }
    }
    # The expansions of Path[From, To): the leftmost set in the range, each alternative expanded,
    # and the rest of the range after the set expanded once per alternative. Keys is the sets'
    # open indices in order, so a range holds its leftmost set after a walk over the sets, not
    # over the characters.
    function Expand-Range([string]$Path, $Sets, $Keys, [int]$From, [int]$To) {
        $open = -1
        foreach ($k in $Keys) { if ($k -ge $From) { if ($k -lt $To) { $open = $k }; break } }
        if ($open -lt 0) { return $Path.Substring($From, $To - $From) }
        $set = $Sets[$open]
        $prefix = $Path.Substring($From, $open - $From)
        $starts = @($open) + $set.commas
        $ends = $set.commas + @($set.close)
        for ($k = 0; $k -lt $starts.Count; $k++) {
            Expand-Range $Path $Sets $Keys ($starts[$k] + 1) $ends[$k] | ForEach-Object {
                $alt = $_
                Expand-Range $Path $Sets $Keys ($set.close + 1) $To | ForEach-Object { $prefix + $alt + $_ }
            }
        }
    }
    # Backslash-to-slash, leading-./ stripped: the form the index and both literal reads below
    # compare against.
    function Get-NormalizedPath([string]$Path) {
        $Path -replace '\\', '/' -replace '^(\./)+', ''
    }
    # The exact index entry, read with -z and as UTF-8: plain output C-quotes a non-ASCII name,
    # and the quoted string names no file. The caller's encoding comes back after each read.
    function Test-ExactTracked([string]$Path) {
        if ($Ref) { return $trackedFiles -ccontains $Path }
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
        $trackedFiles = if ($Ref) {
            @((@(git ls-tree -r -z --full-tree --name-only $Ref 2>$null) -join "`n").Split([char]0, [StringSplitOptions]::RemoveEmptyEntries))
        } else {
            @((@(git ls-files -z 2>$null) -join "`n").Split([char]0, [StringSplitOptions]::RemoveEmptyEntries))
        }
        if ($Ref -and $LASTEXITCODE -ne 0) { throw "cannot read the tree at ref '$Ref'" }
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
    # One expansion budget per body, spent by brace sets alone: the cap bounds the path reads a
    # body's sets cost, so the span that takes the body past 64 expansions is the finding,
    # however small each span is.
    $script:cappedSpans = @()
    $script:braceLeaves = 0
    $script:spanPaths = [System.Collections.Generic.Dictionary[string, object]]::new([StringComparer]::Ordinal)
    $candidates = @([regex]::Matches($Text, '`([^`\r\n]+)`') | ForEach-Object { Get-PathCandidate $_.Groups[1].Value })
    foreach ($span in ($script:cappedSpans | Select-Object -Unique)) {
        $findings += "brace sets expand past 64 paths in this body - trim the sets: '$span'"
    }
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
        # A name Test-Path cannot probe, a component past the filesystem's length limit or an
        # unclosed wildcard bracket with a match in the directory, is an error on some hosts;
        # absent is the answer here, and the suffix read below still decides a tracked file.
        # The answer is assigned inside the try: on Linux the cmdlet writes False and then
        # throws, and a try block's output joined with the catch's would be a two-element array.
        if ($Ref) {
            $present = [bool]@($trackedFiles | Where-Object { $_ -like $normalized -or $_ -like "$normalized/*" })
        } else {
            try { $present = [bool](Test-Path $p) } catch { $present = $false }
        }
        if (-not $present) {
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
    # A backticked span holding an unescaped quote is a command, whose quoted argument is no
    # fragment, and is dropped first. Quoted strings and spans are scanned together, left to
    # right, so a backtick inside a quoted fragment never opens a span.
    function Get-QuotedFragments([string]$Chunk) {
        $Chunk = [regex]::Replace($Chunk, '"(?:[^"\\\r\n]|\\.)*\\?"|`[^`\r\n]*`', { param($m) if ($m.Value[0] -eq '`' -and $m.Value -match '(?<!\\)"') { ' ' } else { $m.Value } })
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
    # A span that expands to several candidates cites each one that resolves, and a fragment on
    # its line is dead only when none of them holds it.
    $otherLines = foreach ($line in $Text -split '\r?\n') {
        $files = @()
        if ($line.Contains('"') -and $line -match '^\s*(?:[-*+]|\d+[.)])\s' -and $line -match '`([^`\r\n]+)`') {
            foreach ($candidate in Get-PathCandidate $Matches[1]) {
                if (-not $resolved.ContainsKey($candidate)) { $resolved[$candidate] = Resolve-AnchorPath $candidate }
                if ($resolved[$candidate]) { $files += $resolved[$candidate] }
            }
            $files = @($files | Select-Object -Unique)
        }
        if (-not $files) { $line; continue }
        foreach ($frag in Get-QuotedFragments $line) { $anchorFrags["$($files -join "`n")`n$frag"] = @(, $files) + $frag }
    }
    $fragMatches = Get-QuotedFragments ($otherLines -join "`n") |
        Where-Object {
            # Only fragments that look like code: contain a code-ish char, and are not prose
            # (prose fragments have many spaces and no code punctuation).
            ($_ -match '[(){}\[\];=_."<>&:!]') -and -not ($_ -match '^[A-Za-z ,''-]+$')
        } | Sort-Object -Unique
    $checkedFrags = 0
    foreach ($pair in $anchorFrags.Values) {
        $files, $frag = $pair
        $specs = @($files | ForEach-Object { ":(literal)$_" })
        $hit = if ($Ref) { git grep -F -e "$frag" $Ref -- @specs 2>$null | Select-Object -First 1 }
               else { git grep -F -e "$frag" -- @specs 2>$null | Select-Object -First 1 }
        $checkedFrags++
        if (-not $hit) {
            $findings += "fragment no longer greps in '$($files -join "', '")': `"$frag`""
        }
    }
    foreach ($frag in $fragMatches) {
        # no --max-count: git before 2.38 rejects it, and the first hit is all that is read
        $hit = if ($Ref) { git grep -F -e "$frag" $Ref 2>$null | Select-Object -First 1 }
               else { git grep -F -- "$frag" 2>$null | Select-Object -First 1 }
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
