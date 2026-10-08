<#
.SYNOPSIS
    The file-footprint graph of the open agent-ready and needs-ruling issues, as one JSON object:
    which issues collide, which share a batch, which can run at once, and which ruling's build
    would collide with the most ready work.
.DESCRIPTION
    Read-only: writes nothing to the tracker and nothing to the tree. Each issue's footprint is
    the set of files its body names, read by the anchor parser's own path rule
    (Get-AnchorFindings.ps1, the Paths property), plus the files named in the body's `Doc impact on
    close` declaration, which the parser excludes from the body and which this script passes to it
    on its own. The parser's rule needs a `/` or `\` in the span, so a root-level file such as
    README.md is never one of its paths: a backticked span with no whitespace and no separator
    that spells a file tracked at HEAD exactly, bare or followed by `:<digits>`,
    `:<digits>-<digits>` or `#<frag>`, is added here, in the body and in the `Doc impact on close`
    declaration alike; spans are paired as the parser pairs them, and a span that still holds
    whitespace once trimmed adds nothing. A span with any other text after a `:` (a
    `<repo>:<path>` citation of another tree) adds nothing. A path holding `*` or `?` is expanded
    against the tracked files, as git ls-files reads it as a pathspec. Paths are compared
    case-sensitively, as the parser compares them.

    Over the agent-ready issues it computes:

      collisions -- each pair of issues whose footprints share a file that is not mirrored, with
                    the shared files.
      batches    -- proposed /ouro:fuse sets of at most five issues, the groups of two or more
                    members. The collisions are taken by shared-file count, largest first (a
                    tie goes to the lower a, then the lower b); a collision joins its two
                    issues' groups only when the joined group holds five or fewer. A batch is
                    a proposed fuse set, not a region: two issues in different batches may
                    still collide, and the batches are not conflict-free.
      waves      -- a greedy partition: take the issues with a non-empty footprint in ascending
                    number; wave 1 takes each one that shares no non-mirrored file with an issue
                    already in it; repeat on the rest until none is left.
      unplaced   -- the issues whose footprint is empty, placed in no wave.

    For each needs-ruling issue, `leverage` lists the agent-ready issues whose footprint shares a
    non-mirrored file with its own, and their count, sorted by count descending and then by number.

    The output is one JSON object on stdout, UTF-8, keys in this order: schema, repo, head,
    mirrored, issues, collisions, batches, waves, unplaced, leverage. Every list is sorted, so the
    same input gives byte-identical output whatever order the issues arrive in. An issue carrying
    both labels counts as agent-ready; an issue carrying neither is skipped with a warning on
    stderr.

    Exit 0 on output; 1 on a failed read or when no repository can be named (stdout is then
    empty, so a failed read never reads as an empty backlog); 2 on a usage error. Diagnostics go
    to stderr only.

    Precision. Both figures below were measured on the anchor paths, before root files joined the
    footprint. A replay of the batch rule over three queues of closed issues: pair precision
    0.61 against a 0.29 base rate, and 0.18 of the colliding pairs held, where a partition into
    fives could hold at most 0.34. So batches land one after another, each re-checked against the
    diff as /ouro:fuse step 2 does.

    A measurement of the footprint less its Doc-impact files over 160 landed issues: mean recall
    0.83 against the files the landing touched (prose-only 0.96, code-touching 0.74; most
    misses under tests/, then the root docs), and 3.7% of same-week pairs with disjoint
    footprints touched a common file.
    The output is a proposal, never a guarantee that two issues will not conflict.

    The issues are those of the repository the binding's `[repo].slug` names, or else of the one
    `origin` names (Get-RepoSlug.ps1). Run from the repository's work tree; needs pwsh 7 and git
    on PATH, and gh unless -IssuesJson is given.
.PARAMETER IssuesJson
    JSON array [{number,title,body,labels:[{name}]}] to read in place of the two gh issue list
    reads (for tests). Each issue's state is read from its labels.
.PARAMETER Mirrored
    Paths of files that change only because a policy or a gate requires a claim to be mirrored
    into them (contract section 4); a comma-separated list is split into paths. Such a file stays
    in an issue's footprint but links no two issues. Default: none, since which files those are is
    the consumer's to say.
.PARAMETER Unexpected
    Collects any other argument, so that one is a usage error (exit 2) rather than ignored.
.EXAMPLE
    pwsh -File <ouro>/bin/Get-FootprintGraph.ps1 -Mirrored CHANGELOG.md,.claude-plugin/plugin.json
#>
param(
    [string]$IssuesJson = '',
    [string[]]$Mirrored = @(),
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Unexpected = @()
)

$ErrorActionPreference = 'Stop'

# git and gh write UTF-8; PowerShell decodes a native command's output with
# [Console]::OutputEncoding, the OEM code page on a Windows runner, which mangles a non-ASCII
# path or title before it is compared.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

class UsageError : System.Exception {
    UsageError([string]$Message) : base($Message) {}
}

function Write-Diag([string]$Message) { [Console]::Error.WriteLine($Message) }

# A path or number set kept distinct and ordinally sorted.
function Get-OrdinalSorted([string[]]$Items) {
    $set = [System.Collections.Generic.SortedSet[string]]::new([StringComparer]::Ordinal)
    foreach ($i in $Items) { [void]$set.Add($i) }
    , [string[]]@($set)
}

# stdout of a git read, NUL-separated entries split out. A failed read throws with its output.
function Get-GitList([string[]]$GitArgs) {
    $out = & git @GitArgs 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git $($GitArgs -join ' ') failed (exit ${LASTEXITCODE}): $(@($out) -join "`n")" }
    $text = (@($out) | Where-Object { $_ -is [string] }) -join "`n"
    , [string[]]@($text.Split([char]0, [StringSplitOptions]::RemoveEmptyEntries))
}

# The text of every `Doc impact on close` declaration in a body, each ready to hand to the parser
# on its own: the inline line's remainder after the colon, or the heading section's body.
function Get-DocImpactText([string]$Body) {
    $chunks = [System.Collections.Generic.List[string]]::new()
    foreach ($m in [regex]::Matches($Body, '(?ms)^#{1,6}[ \t]*Doc impact on close\b(?<h>[^\r\n]*)\r?\n?(?<b>.*?)(?=^#{1,6}[ \t]|\z)')) {
        $chunks.Add($m.Groups['h'].Value + "`n" + $m.Groups['b'].Value)
    }
    foreach ($m in [regex]::Matches($Body, '(?m)^Doc impact on close\b(?<b>.*)$')) {
        $chunks.Add($m.Groups['b'].Value)
    }
    , $chunks.ToArray()
}

function Read-GhIssues([string]$Label) {
    $out = & gh issue list --state open --label $Label --limit 500 --json 'number,title,body,labels' 2>&1
    if ($LASTEXITCODE -ne 0) { throw "gh issue list --label $Label failed (exit ${LASTEXITCODE}): $(@($out) -join "`n")" }
    $text = (@($out) | Where-Object { $_ -is [string] }) -join "`n"
    $rows = try { $text | ConvertFrom-Json -NoEnumerate } catch { $null }
    if ($rows -isnot [array]) { throw "gh issue list --label $Label did not return a JSON array: '$text'" }
    if ($rows.Count -ge 500) {
        Write-Diag "warning: gh issue list --label $Label returned 500 rows, the --limit 500 page is full: an issue past it is not read"
    }
    $rows
}

try {
    $RepoRoot = (git rev-parse --show-toplevel 2>$null)
    if (-not $RepoRoot) { throw 'not inside a git work tree: run from the repository' }
    $RepoRoot = $RepoRoot.Trim()

    # One definition of "path" with the anchor gates, and the repository every gh call names.
    . (Join-Path $PSScriptRoot 'Get-AnchorFindings.ps1')
    . (Join-Path $PSScriptRoot 'Get-RepoSlug.ps1')

    # `pwsh -File` hands a comma list over as one string.
    $Mirrored = @($Mirrored | ForEach-Object { "$_" -split ',' } | ForEach-Object { $_.Trim() })
    if ($Unexpected) { throw [UsageError]::new("unknown parameter or argument: $($Unexpected -join ' ')") }
    foreach ($m in $Mirrored) {
        if (-not "$m".Trim()) { throw [UsageError]::new('-Mirrored holds an empty path') }
    }

    Push-Location -LiteralPath $RepoRoot
    try {
        $repoSlug = ''
        $raw = @()
        if ($IssuesJson) {
            try { $parsed = $IssuesJson | ConvertFrom-Json -NoEnumerate } catch { throw [UsageError]::new("-IssuesJson is not JSON: $_") }
            if ($parsed -isnot [array]) { throw [UsageError]::new('-IssuesJson is not a JSON array') }
            $raw = @($parsed)
            foreach ($r in $raw) {
                if ($null -eq $r -or $r -isnot [pscustomobject] -or $null -eq $r.PSObject.Properties['number'] -or $r.number -isnot [int] -and $r.number -isnot [long]) {
                    throw [UsageError]::new('-IssuesJson holds an element with no integer number')
                }
            }
            $dup = @($raw | Group-Object number | Where-Object Count -gt 1)
            if ($dup) { throw [UsageError]::new("-IssuesJson holds issue number $($dup[0].Name) twice") }
        } else {
            $repo = Get-RepoSlug
            if (-not $repo.Slug) { throw "no repository to read issues from ($($repo.Why)): pass -IssuesJson, or run in a clone whose binding or origin names one" }
            $repoSlug = $repo.Slug
            $env:GH_REPO = $repoSlug
            $byNumber = @{}
            foreach ($label in 'agent-ready', 'needs-ruling') {
                foreach ($r in Read-GhIssues $label) { $byNumber[[int]$r.number] = $r }
            }
            $raw = @($byNumber.Values)
        }

        $head = (git rev-parse HEAD 2>&1)
        if ($LASTEXITCODE -ne 0) { throw "git rev-parse HEAD failed (exit ${LASTEXITCODE}): $head" }
        $head = "$head".Trim()

        $tracked = [System.Collections.Generic.HashSet[string]]::new([string[]](Get-GitList @('ls-files', '-z')), [StringComparer]::Ordinal)
        $mirroredNorm = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($m in $Mirrored) { [void]$mirroredNorm.Add(($m -replace '\\', '/' -replace '^(\./)+', '')) }

        $issues = [System.Collections.Generic.List[object]]::new()
        foreach ($r in ($raw | Sort-Object { [int]$_.number })) {
            $names = @($r.labels | ForEach-Object { "$($_.name)" })
            $state = if ($names -icontains 'agent-ready') { 'agent-ready' } elseif ($names -icontains 'needs-ruling') { 'needs-ruling' } else { '' }
            if (-not $state) {
                Write-Diag "warning: issue #$($r.number) carries neither agent-ready nor needs-ruling: skipped"
                continue
            }
            $body = if ($null -eq $r.body) { '' } else { "$($r.body)" }
            $found = [System.Collections.Generic.List[string]]::new()
            # The parser reads paths only inside backticks, so a body with none has none.
            if ($body.Contains('`')) { foreach ($p in (Get-AnchorFindings -Text $body).Paths) { $found.Add($p) } }
            foreach ($chunk in (Get-DocImpactText $body)) {
                if ($chunk.Contains('`')) { foreach ($p in (Get-AnchorFindings -Text $chunk).Paths) { $found.Add($p) } }
            }
            # The parser's path rule needs a separator, so a root-level file is read here. The body
            # holds the Doc impact text too, so one scan covers both.
            foreach ($m in [regex]::Matches($body, '`([^`\r\n]+)`')) {
                $span = $m.Groups[1].Value.Trim()
                if ($span -match '\s') { continue }
                $sm = [regex]::Match($span,'^([^:#]+)(?::\d+(?:-\d+)?|#.+)?$')
                if (-not $sm.Success) { continue }
                $name = $sm.Groups[1].Value
                if ($name -and $name -notmatch '[/\\]' -and $tracked.Contains($name)) { $found.Add($name) }
            }
            $footprint = [System.Collections.Generic.List[string]]::new()
            foreach ($p in $found) {
                if ($p.Contains('*') -or $p.Contains('?')) {
                    $hits = Get-GitList @('ls-files', '-z', '--', ":(top)$p")
                    if ($hits.Count -gt 0) { foreach ($h in $hits) { $footprint.Add($h) }; continue }
                }
                $footprint.Add($p)
            }
            $fp = Get-OrdinalSorted $footprint.ToArray()
            $issues.Add([pscustomobject]@{
                number     = [int]$r.number
                title      = if ($null -eq $r.title) { '' } else { "$($r.title)" }
                state      = $state
                footprint  = $fp
                unresolved = [string[]]@($fp | Where-Object { -not $tracked.Contains($_) })
                linking    = [System.Collections.Generic.HashSet[string]]::new([string[]]@($fp | Where-Object { -not $mirroredNorm.Contains($_) }), [StringComparer]::Ordinal)
            })
        }

        $ready = @($issues | Where-Object state -ceq 'agent-ready')
        $ruling = @($issues | Where-Object state -ceq 'needs-ruling')

        # Files two issues share, ignoring mirrored ones.
        function Get-Shared($A, $B) {
            $s = [System.Collections.Generic.HashSet[string]]::new($A.linking, [StringComparer]::Ordinal)
            $s.IntersectWith($B.linking)
            Get-OrdinalSorted @($s)
        }

        $collisions = [System.Collections.Generic.List[object]]::new()
        for ($x = 0; $x -lt $ready.Count; $x++) {
            for ($y = $x + 1; $y -lt $ready.Count; $y++) {
                $shared = Get-Shared $ready[$x] $ready[$y]
                if ($shared.Count -eq 0) { continue }
                $collisions.Add([ordered]@{ a = $ready[$x].number; b = $ready[$y].number; files = $shared })
            }
        }

        $maxBatch = 5
        $group = @{}
        foreach ($i in $ready) { $group[$i.number] = [System.Collections.Generic.List[int]]::new([int[]]@($i.number)) }
        foreach ($c in ($collisions | Sort-Object @{ Expression = { $_.files.Count }; Descending = $true }, @{ Expression = { $_.a } }, @{ Expression = { $_.b } })) {
            $ga = $group[[int]$c.a]; $gb = $group[[int]$c.b]
            if ([object]::ReferenceEquals($ga, $gb) -or $ga.Count + $gb.Count -gt $maxBatch) { continue }
            $ga.AddRange($gb)
            foreach ($n in $gb) { $group[$n] = $ga }
        }
        $batches = @($ready | Where-Object { $group[$_.number].Count -ge 2 -and $group[$_.number][0] -eq $_.number } |
            ForEach-Object { , [int[]]@($group[$_.number] | Sort-Object) } | Sort-Object { $_[0] })

        $waves = [System.Collections.Generic.List[object]]::new()
        $remaining = @($ready | Where-Object { $_.footprint.Count -gt 0 })
        while ($remaining.Count -gt 0) {
            $taken = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            $members = [System.Collections.Generic.List[int]]::new()
            $left = [System.Collections.Generic.List[object]]::new()
            foreach ($i in $remaining) {
                if ($taken.Overlaps($i.linking)) { $left.Add($i); continue }
                $members.Add($i.number)
                $taken.UnionWith($i.linking)
            }
            $waves.Add([int[]]$members.ToArray())
            $remaining = $left.ToArray()
        }

        $unplaced = [int[]]@($ready | Where-Object { $_.footprint.Count -eq 0 } | ForEach-Object { $_.number })

        $leverage = @($ruling | ForEach-Object {
            $r = $_
            $gates = [int[]]@($ready | Where-Object { (Get-Shared $r $_).Count -gt 0 } | ForEach-Object { $_.number })
            [ordered]@{ number = $r.number; count = $gates.Count; gates = $gates }
        } | Sort-Object @{ Expression = { $_.count }; Descending = $true }, @{ Expression = { $_.number } })

        $result = [ordered]@{
            schema     = 2
            repo       = $repoSlug
            head       = $head
            mirrored   = Get-OrdinalSorted ([string[]]@($mirroredNorm))
            issues     = @($issues | ForEach-Object {
                [ordered]@{ number = $_.number; title = $_.title; state = $_.state; footprint = $_.footprint; unresolved = $_.unresolved }
            })
            collisions = $collisions.ToArray()
            batches    = $batches
            waves      = $waves.ToArray()
            unplaced   = $unplaced
            leverage   = $leverage
        }
        $json = ConvertTo-Json -InputObject $result -Depth 8
    }
    finally { Pop-Location }
    [Console]::Out.Write(($json -replace "`r`n", "`n") + "`n")
    exit 0
}
catch [UsageError] { Write-Diag "usage: $($_.Exception.Message)"; exit 2 }
catch { Write-Diag "Get-FootprintGraph: $($_.Exception.Message)"; exit 1 }
