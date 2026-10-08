<#
.SYNOPSIS
    Creates (or updates) the ouro loop labels on a GitHub repo.
.DESCRIPTION
    The eight state labels and two modifiers are fixed by the contract (ouro docs/contract.md).
    Idempotent: `gh label create --force` updates color and description when the label already
    exists. With -BindingPath, also creates the labels the binding's [labels] scope, area and
    type declare -- only the ones the repo lacks, and never with --force: those labels are the
    team's, so an existing one is never edited. Nothing else on the repo is touched.
.PARAMETER Repo
    The GitHub repo as owner/name.
.PARAMETER BindingPath
    Path to a `.claude/ouro.toml` whose [labels] scope, area and type declare labels to create
    alongside the contract's. Omitted, or unreadable, or declaring none: only the contract's
    labels are created.
.EXAMPLE
    pwsh -File <ouro>/bin/New-OuroLabels.ps1 -Repo owner/name -WhatIf
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[^/\s]+/[^/\s]+$')]
    [string]$Repo,
    [string]$BindingPath
)

$ErrorActionPreference = 'Stop'

$Labels = @(
    @{ Name = 'agent-ready';  Color = '0E8A16'; Description = 'Well-specified, mechanically executable by an agent; verified specs, no open design decisions' }
    @{ Name = 'human-ready';  Color = '1D76DB'; Description = 'Ready now, nothing gates it, but only a person can do it: bench repro, manual QC, release check' }
    @{ Name = 'needs-ruling'; Color = 'D93F0B'; Description = 'Blocked on an open design decision; the ruling question is in the issue body' }
    @{ Name = 'blocked';      Color = 'B60205'; Description = 'Waiting on an external event or an unlanded dependency; trigger stated in the body' }
    @{ Name = 'needs-triage'; Color = 'FBCA04'; Description = 'Ungraded; the default for a new issue' }
    @{ Name = 'idea';         Color = 'C5DEF5'; Description = 'No commitment, no trigger - kept for the record, not scheduled' }
    @{ Name = 'umbrella';     Color = 'BFD4F2'; Description = 'A container; its children carry the real states; never executed directly' }
    @{ Name = 'architecture'; Color = '5319E7'; Description = 'A design being shaped: interconnected, more than a ruling, not an idea; only the owner converts it' }
    @{ Name = 'trivial';      Color = 'C2E0C6'; Description = 'Rides on agent-ready: gate-verified acceptance; pre-authorizes the full loop incl. merge' }
    @{ Name = 'checkpoint';   Color = '0E8A16'; Description = 'Rides on agent-ready: deliverable is a reviewable finding on the issue, not a diff. Read-only.' }
)

foreach ($l in $Labels) {
    if ($PSCmdlet.ShouldProcess("$Repo label '$($l.Name)' #$($l.Color)", 'gh label create --force')) {
        gh label create $l.Name --repo $Repo --color $l.Color --description $l.Description --force
        if ($LASTEXITCODE -ne 0) { throw "gh label create '$($l.Name)' failed (exit $LASTEXITCODE)" }
        Write-Host "ok: $($l.Name)"
    }
}

# The binding's own [labels]: the team's scope, area and type labels. Created only where the
# repo lacks them, and never with --force -- unlike the contract's ten above, an existing one is
# never edited, since it is the team's, not the contract's.
if ($BindingPath -and (Test-Path -LiteralPath $BindingPath -PathType Leaf)) {
    $tool = Join-Path $PSScriptRoot 'ouro-binding.py'
    function Get-DeclaredLabels {
        param([string]$Key)
        if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { return @() }
        $saved = [Console]::OutputEncoding
        try {
            [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
            $out = python3 $tool get $Key $BindingPath 2>&1
        }
        finally { [Console]::OutputEncoding = $saved }
        if ($LASTEXITCODE -ne 0) {
            if ("$out" -match 'no such key') { return @() }
            throw "ouro-binding.py get $Key failed (exit $LASTEXITCODE): $out"
        }
        return @("$out" | ConvertFrom-Json)
    }
    $declared = @()
    foreach ($key in 'scope', 'area', 'type') {
        foreach ($name in (Get-DeclaredLabels "labels.$key")) {
            $declared += [pscustomobject]@{ Name = $name; Key = $key }
        }
    }
    if ($declared.Count -gt 0) {
        # Every page of the repo's labels: a capped list would read an existing label past the cap
        # as missing, and its create (no --force) would fail and stop the rest.
        # Decoded as UTF-8, as Install-Ouro.ps1 reads its tools: in the console code page a
        # non-ASCII name the repo has would not match its declared spelling, and be created again.
        $saved = [Console]::OutputEncoding
        try {
            [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
            $listOut = gh api --paginate "repos/$Repo/labels?per_page=100" --jq '.[].name' 2>&1
        }
        finally { [Console]::OutputEncoding = $saved }
        if ($LASTEXITCODE -ne 0) { throw "gh api repos/$Repo/labels failed (exit $LASTEXITCODE): $listOut" }
        # GitHub compares label names without case, and one name may be declared under two keys:
        # a name is created once, and only when neither the repo nor this run has it yet.
        $known = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
        foreach ($n in @($listOut | Where-Object { $_ -is [string] -and $_ })) { [void]$known.Add($n) }
        foreach ($d in $declared) {
            if (-not $known.Add($d.Name)) { continue }
            if ($PSCmdlet.ShouldProcess("$Repo label '$($d.Name)'", 'gh label create')) {
                gh label create $d.Name --repo $Repo --color 'ededed' --description "$($d.Key): $($d.Name)"
                if ($LASTEXITCODE -ne 0) { throw "gh label create '$($d.Name)' failed (exit $LASTEXITCODE)" }
                Write-Host "ok: $($d.Name)"
            }
        }
    }
}
