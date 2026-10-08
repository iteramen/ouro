<#
.SYNOPSIS
    Deterministic target selection for the weekly unattended intake triage.

.DESCRIPTION
    The LLM never chooses its own workload; this script is the intake's only selector. It lists
    the open issues created since the window's date, applies the intake skill's three exclusions,
    and writes what is left with the fields a grading pass reads -- number, title, body, comments,
    labels. No model runs in it.

    The three exclusions, and what each one is not:

      rolling     -- the issues named in [rolling_issues] are the weekly pass's own report
                     surfaces, not backlog. Matched by exact title, case included, never
                     `in:title`: token search matches any issue whose title merely carries the
                     same words, which is how a gate ended up posting to a look-alike.
      state label -- an issue carrying a state other than `needs-triage` has been graded already,
                     by a person or by an earlier pass. `needs-triage` is the absence of a
                     verdict, not one, so an issue carrying only that is a target -- and so is
                     one that arrived carrying no state at all, which the session stamps.
      marker      -- a comment whose FIRST line opens with `**Intake triage** (automated)` is a
                     previous pass's verdict, so a re-run of the same week grades nothing twice.
                     Trimmed, case-sensitive, and matched as a PREFIX -- deliberately not the
                     shape gate's whole-line reading of ITS marker, which is a different string.
                     An issue quoting the marker in its body or mid-comment is still a target,
                     and so is a human comment opening with its words but not `(automated)`.

    Every read fails loud, and in three separate ways, because a quiet week is a legitimate
    outcome here: anything that merely LOOKS like one is passed on without a word. A native
    failure does not throw under 'Stop', so the exit code is checked. ConvertFrom-Json then turns
    an empty body, a whitespace-only one and a literal `null` into nothing at all -- exactly what
    an empty window looks like -- and an unterminated array into the rows it did get, so the
    body's SHAPE is checked as well as its exit code. And a page filled to the limit throws
    rather than passing off part of a window as the whole of it.

    Both reads address the repository the binding's `[repo].slug` names, or else the one `origin`
    names (Get-RepoSlug.ps1). With neither, the selection throws rather than handing the pass
    whatever repository gh picks from the clone's remotes.

    The open `needs-ruling` count rides along so the grading pass needs no second query for it.

.NOTES
    Modelled on Get-DriftAuditTargets.ps1, the drift audit's selector, minus its -AsModule: the
    world this one reads is `gh`, so the suite injects that world as JSON and drives the whole
    script rather than its parts.

.PARAMETER NewSince
    Window start, yyyy-MM-dd. The issues considered are those created on or after it.
.PARAMETER OutFile
    Write the JSON here instead of to stdout.
.PARAMETER Limit
    Page size for both `gh` reads. A read that returns this many rows throws rather than emitting
    a truncated list; raise it if a window really holds that many.
.PARAMETER RollingIssueTitles
    The rolling issues' exact titles (for tests, and to run without a binding). Default: every
    value of [rolling_issues]. It cannot be passed through `pwsh -File`, which hands every
    argument over as a separate string: the first title binds, the SECOND binds positionally to
    -OutFile, and the run then writes its JSON to a file named after that title and prints
    nothing. Pass it in-process instead -- `& <dir>/Get-IntakeTargets.ps1 -NewSince <date>
    -RollingIssueTitles 'A','B'` -- or with `pwsh -NoProfile -Command` around the same call.
.PARAMETER IssuesJson
    JSON array [{number,title,body,labels:[{name}],comments:[{body}]}] to select from (for
    tests). Default: gh issue list.
.PARAMETER RulingsJson
    JSON array of the open `needs-ruling` issues (for tests). Default: gh issue list.

.EXAMPLE
    pwsh -File <ouro>/bin/Get-IntakeTargets.ps1 -NewSince 2026-09-09 -OutFile intake-targets.json
#>
param(
    [Parameter(Mandatory)]
    [string]$NewSince,
    [string]$OutFile,
    [int]$Limit = 200,
    [string[]]$RollingIssueTitles,
    [string]$IssuesJson,
    [string]$RulingsJson
)

$ErrorActionPreference = 'Stop'

# The repository both gh reads below name, shared with the gates that read the same backlog.
. (Join-Path $PSScriptRoot 'Get-RepoSlug.ps1')

# The contract's eight states. Written out rather than read from the binding: the contract fixes
# them, which is why ouro-binding.py refuses one declared under [labels].
$States = @('agent-ready', 'human-ready', 'needs-ruling', 'blocked', 'needs-triage', 'idea',
    'umbrella', 'architecture')
$Marker = '**Intake triage** (automated)'

# A native failure does not throw under 'Stop'. Only stdout is data: under 2>&1 stderr arrives as
# ErrorRecords, and a gh that exits 0 may still write there (an upgrade notice). gh, git and
# ouro-binding.py all print UTF-8, which PowerShell decodes with [Console]::OutputEncoding -- the
# OEM code page on a Windows runner, where a title carrying an em dash then equals no rolling
# title. The caller's encoding comes back after every read: this script is also run in-process.
function Invoke-Read {
    param([string]$Exe, [string[]]$Arguments)
    $encoding = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $out = & $Exe @Arguments 2>&1
    }
    finally { [Console]::OutputEncoding = $encoding }
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed (exit $LASTEXITCODE): $out" }
    return @($out | Where-Object { $_ -is [string] })
}

# `gh --json` returns a JSON array and nothing else, so that is what is required back. The exit
# code alone does not cover this: ConvertFrom-Json parses an empty body, a whitespace-only one
# and a literal `null` to nothing -- which is indistinguishable from the empty window that is a
# normal outcome -- and it parses an UNTERMINATED array to the rows it got, so a reply cut off
# mid-flight would be selected as though it were the whole week. The test is the body's own
# shape: an array from `[` to `]`, every row carrying the number the selection addresses it by.
#
# The closing `]` is what catches truncation; the opening `[` catches nothing the closing one
# does not, since no valid JSON value ends in `]` without starting in `[`. It is kept because
# together the two state the shape a reader expects, not because a case can reach it alone. The
# empty-body test is likewise reachable through the bracket test -- it exists for its message,
# which is the one an operator sees when a read comes back with nothing at all.
function ConvertFrom-IssueListJson {
    param([string]$Text, [string]$What)
    $trimmed = "$Text".Trim()
    $show = if ($trimmed.Length -gt 200) { $trimmed.Substring(0, 200) + '...' } else { $trimmed }
    if (-not $trimmed) { throw "$What returned an empty body: a read that says nothing is not a quiet week" }
    if (-not ($trimmed.StartsWith('[') -and $trimmed.EndsWith(']'))) {
        throw "$What did not return a whole JSON array: got [$show]"
    }
    $rows = @($trimmed | ConvertFrom-Json)
    foreach ($r in $rows) {
        if ($null -eq $r.number) { throw "$What returned a row carrying no issue number: got [$show]" }
    }
    return $rows
}

# A previous pass's verdict comment. The marker is matched as a PREFIX of the comment's first
# line, trimmed: the intake writes `**Intake triage** (automated)` and the line may carry more
# after it, so a whole-line compare would match none of them. That is deliberately NOT the shape
# gate's reading of ITS marker -- Test-AgentReadyShape.ps1 compares the whole trimmed first line
# with `-ceq '**Triage**'` -- and the two markers are different strings by design (contract
# section 4). Case-sensitive like that gate: a hand-written variant is not machine provenance.
#
# `(automated)` is part of the match, not decoration. Matching the bare `**Intake triage**` would
# read a human comment opening "**Intake triage** got this wrong, please re-run" as a verdict,
# and the issue carrying it would be dropped from this and every later pass -- never graded, and
# silently. Erring the other way merely re-grades an issue, which is visible in a second comment.
function Test-IntakeMarker {
    param($Comments)
    foreach ($c in @($Comments)) {
        if (-not $c.body) { continue }
        # First line only. .Trim() takes the trailing CR of a CRLF body with it, so the line needs
        # no separate normalising, and it is what lets a marker indented by a space still count.
        $first = (("$($c.body)") -split "`n")[0].Trim()
        if ($first.StartsWith($Marker, [System.StringComparison]::Ordinal)) { return $true }
    }
    return $false
}

$parsedSince = [datetime]::MinValue
if (-not [datetime]::TryParseExact($NewSince, 'yyyy-MM-dd', [cultureinfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::None, [ref]$parsedSince)) {
    throw "-NewSince '$NewSince' is not a yyyy-MM-dd date: the created:>= qualifier it builds would not name the week, and neither an empty list nor the whole backlog is one week's intake"
}

if (-not $PSBoundParameters.ContainsKey('RollingIssueTitles')) {
    # Unlike the gates that post findings, this one does not fall back when it cannot read the
    # binding: selecting without [rolling_issues] hands the pass its own report surfaces to grade,
    # and an intake commenting on the drift ledger is worse than a red step.
    $tool = Join-Path $PSScriptRoot 'ouro-binding.py'
    if (-not (Test-Path -LiteralPath $tool)) {
        throw "no ouro-binding.py beside this script: [rolling_issues] cannot be read. Pass -RollingIssueTitles in-process to select without the binding (pwsh -File cannot bind an array -- see the parameter's help)."
    }
    $root = @(Invoke-Read git @('rev-parse', '--show-toplevel'))[0]
    if (-not $root) { throw 'not inside a git work tree: run from the consumer repo' }
    $binding = Join-Path $root.Trim() '.claude/ouro.toml'
    if (-not (Test-Path -LiteralPath $binding -PathType Leaf)) {
        throw "no $binding : [rolling_issues] cannot be read. Pass -RollingIssueTitles in-process to select without a binding (pwsh -File cannot bind an array -- see the parameter's help)."
    }
    $encoding = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $out = python3 $tool get rolling_issues 2>&1
    }
    finally { [Console]::OutputEncoding = $encoding }
    if ($LASTEXITCODE -eq 0) {
        $table = ((@($out) | Where-Object { $_ -is [string] }) -join "`n").Trim() | ConvertFrom-Json
        $titles = @($table.PSObject.Properties | ForEach-Object { "$($_.Value)" })
        # A title declared empty is a broken binding, not an absent key: read as one it would
        # exclude nothing and the pass would grade the issue that key names.
        if (@($titles | Where-Object { -not $_.Trim() })) {
            throw '[rolling_issues] declares an empty title: an empty exclusion matches nothing, and the pass would grade its own report surface'
        }
        $RollingIssueTitles = $titles
    }
    elseif ("$out" -match 'no such key') {
        $RollingIssueTitles = @()   # a repo that declared no rolling issue has none to exclude
    }
    else { throw "ouro-binding.py get rolling_issues failed (exit $LASTEXITCODE): $out" }
}

# Named before the first read that is not injected. Unresolved throws, like every other read
# here: a window selected from whatever repository gh picks is not this repo's week, and an
# intake grades and comments on what this list carries.
if (-not ($PSBoundParameters.ContainsKey('IssuesJson') -and $PSBoundParameters.ContainsKey('RulingsJson'))) {
    $repo = Get-RepoSlug
    if (-not $repo.Slug) { throw "the repository to select from is not named ($($repo.Why))" }
    $env:GH_REPO = $repo.Slug
}

$search = "created:>=$NewSince"
$issues = if ($PSBoundParameters.ContainsKey('IssuesJson')) {
    ConvertFrom-IssueListJson -Text $IssuesJson -What '-IssuesJson'
} else {
    ConvertFrom-IssueListJson -What "the issue list for '$search'" -Text (
        (Invoke-Read gh @('issue', 'list', '--state', 'open', '--search', $search, '--limit', "$Limit",
                '--json', 'number,title,body,labels,comments,createdAt,url')) -join "`n")
}
if ($issues.Count -ge $Limit) {
    throw "the issue list for '$search' filled the -Limit page ($Limit rows): the rest of the window is not in it, and a truncated page would read as the whole week. Raise -Limit."
}

$rulings = if ($PSBoundParameters.ContainsKey('RulingsJson')) {
    ConvertFrom-IssueListJson -Text $RulingsJson -What '-RulingsJson'
} else {
    ConvertFrom-IssueListJson -What 'the open needs-ruling list' -Text (
        (Invoke-Read gh @('issue', 'list', '--label', 'needs-ruling', '--state', 'open',
                '--limit', "$Limit", '--json', 'number')) -join "`n")
}
if ($rulings.Count -ge $Limit) {
    throw "the open needs-ruling list filled the -Limit page ($Limit rows): the count would understate the queue the cap reads. Raise -Limit."
}

$targets = [System.Collections.Generic.List[object]]::new()
$excluded = [System.Collections.Generic.List[object]]::new()
foreach ($i in $issues) {
    $drop = $null
    $names = @($i.labels | ForEach-Object { $_.name })
    # Labels are matched case-insensitively, as gh matches them: a state spelled in another case
    # is the same label, and needs-triage is the absence of a verdict rather than one.
    $graded = @($names | Where-Object { $States -contains $_ -and $_ -ne 'needs-triage' })
    # -ccontains is the exact-title test, case included: a case variant is another issue.
    if ($RollingIssueTitles -ccontains "$($i.title)") { $drop = 'rolling issue' }
    elseif ($graded.Count -gt 0) { $drop = "state label: $($graded -join ', ')" }
    elseif (Test-IntakeMarker $i.comments) { $drop = 'intake marker' }

    if ($drop) { $excluded.Add([ordered]@{ number = $i.number; title = $i.title; reason = $drop }) }
    else { $targets.Add($i) }
}

$result = [ordered]@{
    newSince     = $NewSince
    rulingsQueue = $rulings.Count
    targets      = @($targets)
    excluded     = @($excluded)
}
$json = $result | ConvertTo-Json -Depth 10
if ($OutFile) { $json | Set-Content -Path $OutFile -Encoding utf8 } else { $json }
