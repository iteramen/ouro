<#
.SYNOPSIS
    Deterministic contract-shape gate for agent-ready issues.
.DESCRIPTION
    The agent-ready label is a promise (ouro docs/contract.md): spec verified, mechanically
    executable, no open design decisions. The semantic bar needs triage; every structural
    consequence of skipping triage can be checked by a script. Five checks per open
    agent-ready issue:

      anchor     -- >= 1 parseable anchor in the body (the anchor gate's own parser, so the
                    two gates can never disagree); a spec living only in comments fails, and
                    an env-var, <placeholder>, scheme-qualified, or non-repo-relative path
                    token is not an anchor -- nothing could verify it at HEAD.
      size       -- a body states one size: a `Size:` body line valued S or M (L is a SPLIT
                    verdict and never rides on agent-ready); repeated lines must agree.
      doc-impact -- a `Doc impact on close:` body line; the `### Doc impact on close`
                    heading the issue form renders counts too.
      provenance -- a comment whose first line is exactly **Triage** (the interactive triage
                    skill's marker); a hand-promotion that skipped the skill has none, and
                    the unattended intake's marker is different by design.
      labels     -- at least one area label and exactly one type label, sets read from the binding's
                    [labels] via ouro-binding.py; an empty or undeclared set skips its check
                    (a binding that declares no type set gets no type finding, for instance), and a
                    vendored copy without ouro-binding.py beside it, or a repo with no
                    .claude/ouro.toml, skips both with an INFO line. The sets must be
                    disjoint; `ouro-binding.py check` enforces that.

    Every text check is case-sensitive: the gate exists to catch hand-promotions, and a
    hand-written `size: m` or `**triage**` is exactly what it must not wave through.

    Default is the full sweep: report-only, always exits 0; -Comment posts findings on each
    affected issue. -Issue <N> checks one issue and exits nonzero on findings (for a
    consumer's at-promotion event workflow); -Demote (single-issue mode only) additionally
    swaps agent-ready -> needs-triage (clearing the modifiers that ride on it -- they never
    stand alone) and comments the missing pieces -- a shape failure means triage never
    happened, unlike the anchor gate's needs-ruling, which means the code moved out from
    under a graded spec.

    The issues are those of the repository the binding's `[repo].slug` names, or else of the one
    `origin` names (Get-RepoSlug.ps1). With neither, the run reports that and checks nothing.
.PARAMETER Issue
    Check a single issue by number; findings exit 1.
.PARAMETER Comment
    Post findings as a comment on each affected issue.
.PARAMETER Demote
    With -Issue only: swap agent-ready -> needs-triage and comment the findings.
.PARAMETER IssuesJson
    JSON array [{number,title,body,labels:[{name}],comments:[{body}]}] to check (for
    tests). Default: gh issue list / gh issue view.
.PARAMETER AreaLabels
    Area label set (for tests). Default: `ouro-binding.py get labels.area`.
.PARAMETER TypeLabels
    Type label set (for tests). Default: `ouro-binding.py get labels.type`.
.EXAMPLE
    pwsh -File <ouro>/bin/Test-AgentReadyShape.ps1 -Comment
    pwsh -File <ouro>/bin/Test-AgentReadyShape.ps1 -Issue 41 -Demote
#>
param(
    [int]$Issue = 0,
    [switch]$Comment,
    [switch]$Demote,
    [string]$IssuesJson = '',
    [string[]]$AreaLabels,
    [string[]]$TypeLabels
)

$ErrorActionPreference = 'Stop'

# gh writes UTF-8. PowerShell decodes a native command's stdout with [Console]::OutputEncoding,
# which on a Windows runner is the OEM code page, so without this every non-ASCII character in
# an issue body -- an em dash, most often -- is mangled before it is compared against the tree.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
if ($Demote -and -not $Issue) { throw '-Demote is single-issue mode only: pass -Issue <N>' }

$RepoRoot = (git rev-parse --show-toplevel 2>$null)
if (-not $RepoRoot) { throw 'not inside a git work tree: run from the consumer repo' }
$RepoRoot = $RepoRoot.Trim()

# The parser is shared with Test-AgentReadyAnchors.ps1 -- one definition of "anchor".
. (Join-Path $PSScriptRoot 'Get-AnchorFindings.ps1')
# The repository every gh call below names, shared with the gates that read the same backlog.
. (Join-Path $PSScriptRoot 'Get-RepoSlug.ps1')

# Label sets come from the binding unless a test injected them. A missing key means the
# repo declared no such set (skip, like an empty set); any other failure throws -- a broken
# binding read must never silently skip a check.
function Get-BindingLabelSet {
    param([string]$Key)
    $tool = Join-Path $PSScriptRoot 'ouro-binding.py'
    if (-not (Test-Path -LiteralPath $tool)) {
        # A vendored directory may lack the binding tool: fall back loudly rather than fail.
        Write-Host "INFO - no ouro-binding.py beside this script (vendored without the binding tool): $Key check skipped; pass -AreaLabels/-TypeLabels to enforce it"
        return @()
    }
    # No binding at all is not a broken read: pwsh and git are enough for an unbound repo, so
    # skip before python is called. A binding that exists still reads, or throws below.
    $binding = Join-Path $RepoRoot '.claude/ouro.toml'
    if (-not (Test-Path -LiteralPath $binding -PathType Leaf)) {
        Write-Host "INFO - $Key not read (no $binding): check skipped; pass -AreaLabels/-TypeLabels to enforce it"
        return @()
    }
    $out = python3 $tool get $Key 2>&1
    if ($LASTEXITCODE -ne 0) {
        if ("$out" -match 'no such key') { return @() }
        throw "ouro-binding.py get $Key failed (exit $LASTEXITCODE): $out"
    }
    return @("$out" | ConvertFrom-Json)
}

Push-Location -LiteralPath $RepoRoot
try {
    if (-not $PSBoundParameters.ContainsKey('AreaLabels')) { $AreaLabels = Get-BindingLabelSet 'labels.area' }
    if (-not $PSBoundParameters.ContainsKey('TypeLabels')) { $TypeLabels = Get-BindingLabelSet 'labels.type' }

    # Every gh call this run can make is named here: the reads, unless -IssuesJson injects them,
    # and -Comment's or -Demote's writes, which it does not.
    if (-not $IssuesJson -or $Comment -or $Demote) {
        $repo = Get-RepoSlug
        if (-not $repo.Slug) {
            Write-Host "::warning::shape gate: no issue checked ($($repo.Why)): a gate that cannot name its repository enforces nothing"
            exit 0
        }
        $env:GH_REPO = $repo.Slug
    }

    if ($IssuesJson) {
        $issues = @($IssuesJson | ConvertFrom-Json)
        if ($Issue) {
            $issues = @($issues | Where-Object number -eq $Issue)
            if (-not $issues) { throw "issue #$Issue not in -IssuesJson" }
        }
    } elseif ($Issue) {
        $raw = gh issue view $Issue --json number,title,body,labels,comments
        if ($LASTEXITCODE -ne 0) { throw "gh issue view $Issue failed (exit $LASTEXITCODE)" }
        $issues = @($raw | ConvertFrom-Json)
    } else {
        $raw = gh issue list --label agent-ready --state open --limit 100 --json number,title,body,labels
        if ($LASTEXITCODE -ne 0) { throw "gh issue list failed (exit $LASTEXITCODE)" }
        $issues = @($raw | ConvertFrom-Json)
        if ($issues.Count -ge 100) {
            Write-Host "::warning::shape gate: the agent-ready list filled its --limit 100 page: an issue past it goes unchecked"
        }
    }

    Write-Host "agent-ready issues to shape-check: $($issues.Count)"
    $totalFindings = 0

    foreach ($i in $issues) {
        $names = @($i.labels | ForEach-Object { $_.name })
        if ($names -notcontains 'agent-ready') {
            # Single-issue mode can race a label change; a sweep list is pre-filtered.
            Write-Host "#$($i.number): INFO - not agent-ready, nothing to enforce"
            continue
        }
        $body = if ($i.body) { $i.body } else { '' }
        $findings = @()

        $parsed = Get-AnchorFindings -Text $body
        if (($parsed.PathCount + $parsed.FragCount) -eq 0) {
            $findings += 'anchor: no parseable anchor in the body (a backticked repo path or a quoted code fragment) - a spec living only in comments or screenshots is not executable'
        }

        # Every Size line counts; lines that repeat one value are checked as that one line.
        # A value ends at its line break, so an empty Size line carries none and is no Size line.
        $sizes = @([regex]::Matches($body, '(?m)^Size:[^\S\r\n]*(\S+)') | ForEach-Object { $_.Groups[1].Value })
        $distinct = @($sizes | Select-Object -Unique)   # case-sensitive, body order kept
        if ($sizes.Count -eq 0) {
            $findings += 'size: no `Size:` line in the body'
        } elseif ($distinct.Count -gt 1) {
            $listed = ($sizes | ForEach-Object { '`Size: ' + $_ + '`' }) -join ', '
            $findings += "size: $($sizes.Count) ``Size:`` lines in the body ($listed) - a body states exactly one size"
        } elseif ($distinct[0] -cnotin @('S', 'M')) {
            $findings += "size: ``Size: $($distinct[0])`` - only S or M ride on agent-ready (L is a SPLIT verdict)" +
                $(if ($distinct[0] -ceq 'L') { ': split it through /ouro:triage into single-deliverable S or M issues with this one as their umbrella or closed, or return it for a ruling if triage finds no split lines' } else { '' })
        }

        # The issue form renders the field as a '### Doc impact on close' heading; both count.
        if ($body -cnotmatch '(?m)^(#{1,6}\s*)?Doc impact on close\b') {
            $findings += 'doc-impact: no `Doc impact on close:` line in the body'
        }

        # Comments ride on the issue in -IssuesJson / gh issue view; a sweep fetches per issue.
        $comments = $i.comments
        if ($null -eq $comments -and -not $IssuesJson) {
            $raw = gh issue view $i.number --json comments
            if ($LASTEXITCODE -ne 0) { throw "gh issue view $($i.number) failed (exit $LASTEXITCODE)" }
            $comments = ($raw | ConvertFrom-Json).comments
        }
        $hasTriageMark = @($comments) | Where-Object {
            $_.body -and (($_.body -replace "`r`n", "`n") -split "`n")[0].Trim() -ceq '**Triage**'
        }
        if (-not $hasTriageMark) {
            $findings += 'provenance: no comment starting with the literal line **Triage** - the promotion skipped interactive triage'
        }

        # Area labels select gates, so a change spanning areas carries each; a type is one classification.
        foreach ($set in @(@{ Name = 'area'; Set = $AreaLabels; Exact = $false }, @{ Name = 'type'; Set = $TypeLabels; Exact = $true })) {
            if (-not $set.Set -or $set.Set.Count -eq 0) { continue }
            $on = @($names | Where-Object { $set.Set -contains $_ })
            if ($on.Count -eq 0 -or ($set.Exact -and $on.Count -ne 1)) {
                $rule = if ($set.Exact) { 'exactly one' } else { 'at least one' }
                $findings += "labels: $($on.Count) $($set.Name) label(s) - the binding requires $rule of: $($set.Set -join ', ')"
            }
        }

        if ($findings.Count -eq 0) {
            Write-Host "#$($i.number): OK"
            continue
        }

        $totalFindings += $findings.Count
        Write-Host "#$($i.number) $($i.title)" -ForegroundColor Yellow
        $findings | ForEach-Object { Write-Host "  SHAPE: $_" -ForegroundColor Yellow }

        if ($Demote) {
            # State change before the report of it: the comment below claims the demotion,
            # so a failed edit must throw here, not after a comment describing it.
            # Modifiers ride only on agent-ready and never stand alone (contract §4).
            # gh writes one edit's removal and addition independently, and a label the repository
            # lacks fails only its own half. So: remove first, naming only labels the issue carries,
            # then add. A failed add leaves no state label -- the label-invariants gate's safe-to-
            # restore finding -- never a rejected promotion still on agent-ready.
            $remove = @('agent-ready') + @('trivial', 'checkpoint' | Where-Object { $names -contains $_ })
            $removeFlags = @($remove | ForEach-Object { '--remove-label', $_ })
            gh issue edit $i.number @removeFlags | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "gh issue edit $($i.number) removing $($remove -join ', ') failed (exit $LASTEXITCODE): nothing has changed" }
            gh issue edit $i.number --add-label needs-triage | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "gh issue edit $($i.number) adding needs-triage failed (exit $LASTEXITCODE): the repository may lack the label, and the issue now carries no state label" }
            Write-Host '  demoted: agent-ready -> needs-triage' -ForegroundColor Yellow
        }
        if ($Comment -or $Demote) {
            # The comment names the ouro release that ran: the version in the plugin.json of the tree
            # this script sits in, not of the repo it runs in. A vendored tree has none there and a
            # consumer plugin carries another name: both keep the short HEAD of the repo the gate runs
            # in, which for vendored scripts is their own version. So does a read or a field that fails;
            # both are inside the try, so a caller's strict mode cannot throw here. A vendored copy in a
            # consumer plugin that is itself named ouro shows that plugin's version.
            $ouroVersion = ''
            try {
                $manifest = Get-Content -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) '.claude-plugin/plugin.json') -Raw | ConvertFrom-Json
                if ($manifest.name -ceq 'ouro' -and $manifest.version -is [string]) { $ouroVersion = $manifest.version.Trim() }
            } catch { }
            $identity = if ($ouroVersion) { "ouro v$ouroVersion, Test-AgentReadyShape.ps1" } else { "Test-AgentReadyShape.ps1 @ $((git rev-parse --short HEAD).Trim())" }
            $bodyText = "Shape gate ($identity): " +
                "$($findings.Count) structural finding(s) against the agent-ready contract:`n`n" +
                (($findings | ForEach-Object { "- $_" }) -join "`n") +
                $(if ($Demote) { "`n`nDemoted agent-ready -> needs-triage: " } else { "`n`n" }) +
                "re-triage with /ouro:triage $($i.number) (ouro contract)."
            gh issue comment $i.number --body $bodyText | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "gh issue comment $($i.number) failed (exit $LASTEXITCODE)" }
        }
    }

    Write-Host "total shape findings: $totalFindings"
    if ($Issue -and $totalFindings -gt 0) { exit 1 }
    # Sweep is report-only by design: the gate informs, the human (or triage) acts.
    exit 0
}
finally {
    Pop-Location
}
