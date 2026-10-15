<#
.SYNOPSIS
    The direct lane's push: the commits on the default branch go to origin, or to a branch a pull
    request carries, only when every file they change is one the binding's [ship].direct names.
.DESCRIPTION
    Run from the repo root on the default branch, after the commits are made. The repository, the
    default branch and the globs (`[ship].direct`) come from the binding (.claude/ouro.toml, read
    through ouro-binding.py beside this script). Nothing is forced, and nothing is ever bypassed.

    The range is the commits origin's default branch (fetched first) lacks. Before any push, in this
    order, each a `STOP: <why>` line and exit 1:
      - the checkout is not on the default branch, or the working tree is not clean;
      - `[ship].direct` is empty or absent: the lane is off;
      - the range holds no commit that changes a file;
      - any path any commit in the range touches (the union over the commits, so a file one
        commit adds and a later one deletes still counts) matches no glob. The stop names each such
        path and says the run is express or tighter. The matcher is `ouro-binding.py direct`, the
        one fnmatch.fnmatchcase reading of the globs that `check` uses, on repository-relative
        paths with forward slashes, so `*` also crosses `/`.

    Then the default branch's protection is read: the rules endpoint, then classic protection. A
    pull request is required by a pull_request rule, a required_status_checks rule, or classic
    protection of reviews or of status checks. One outcome follows.
      - No pull-request requirement: `vehicle: push`, a plain `git push origin <default>` (never
        forced; a non-fast-forward stops, exit 1), then origin's tip is read back and must be HEAD.
        Prints `pushed <default> to origin at <sha>`. Exit 0.
      - A pull-request requirement and no required approving review: `vehicle: pr`. The commits are
        pushed to the branch `direct/<Issue>` with Push-Branch.ps1 (printing its `pushed` line), the
        local default branch returns to origin's tip (the commits live on the branch), and the
        checkout returns to it. The pull request is the caller's to open and to merge once its
        required checks are green. Exit 0.
      - A required approving review (a count above zero, or a code-owner review), or a protection
        read that failed or answered in a shape not understood (a plan not offering the protection,
        or a branch having none, is no failure): `fallback: express`, nothing pushed. Exit 3: the
        run continues as express.
.PARAMETER Issue
    The issue the commits fix: names the branch under `vehicle: pr`. Required only there.
.EXAMPLE
    pwsh -File <ouro>/bin/Push-Direct.ps1
.EXAMPLE
    pwsh -File <ouro>/bin/Push-Direct.ps1 -Issue 940
#>
param([int]$Issue)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Get-LandBinding.ps1')
. (Join-Path $PSScriptRoot 'Push-Branch.ps1')
$script:Fallback = $false

# What the default branch's protection asks of a push: @{ Pr; Approval; Failed; Reading }. A pull
# request is required by a pull_request rule, a required_status_checks rule or classic protection of
# either; an answer of an unexpected shape is a failed read.
function Get-DirectProtection([string]$Slug, [string]$Default) {
    $pr = $false; $approval = $false; $failed = ''; $parts = @()
    function Test-Absent($r) { $r.Code -ne 0 -and $r.Text -match 'Upgrade to GitHub Pro|make this repository public|Branch not protected' }
    function Get-Prop($o, [string]$n) {
        if ($o -is [System.Management.Automation.PSCustomObject] -and $o.PSObject.Properties[$n]) { $o.PSObject.Properties[$n].Value }
    }

    $rules = Invoke-Native gh @('api', "repos/$Slug/rules/branches/$Default")
    if ($rules.Code -eq 0) {
        try {
            $set = ($rules.Out -join "`n") | ConvertFrom-Json -NoEnumerate
            if ($set -isnot [array]) { throw 'not a JSON array' }
            $count = 0
            foreach ($r in $set) {
                $type = Get-Prop $r 'type'
                if ($type -eq 'required_status_checks') { $pr = $true; $count++ }
                elseif ($type -eq 'pull_request') {
                    $pr = $true; $count++
                    $params = Get-Prop $r 'parameters'
                    if ($params -isnot [System.Management.Automation.PSCustomObject]) { throw 'a pull_request rule without parameters' }
                    if ((Get-Prop $params 'required_approving_review_count') -gt 0) { $approval = $true }
                    if ((Get-Prop $params 'require_code_owner_review') -eq $true) { $approval = $true }
                }
            }
            $parts += "rules: pull request or status checks $count"
        }
        catch { $failed = "the rules answer has an unexpected shape ($($_.Exception.Message)): $($rules.Text)" }
    }
    elseif (Test-Absent $rules) { $parts += 'rules: none' }
    else { $failed = "the rules read failed (exit $($rules.Code)): $($rules.Text)" }

    $classic = Invoke-Native gh @('api', "repos/$Slug/branches/$Default/protection")
    if ($classic.Code -eq 0) {
        try {
            $p = ($classic.Out -join "`n") | ConvertFrom-Json
            if ($p -isnot [System.Management.Automation.PSCustomObject]) { throw 'not a JSON object' }
            $rv = Get-Prop $p 'required_pull_request_reviews'
            if ($null -ne $rv) {
                $pr = $true
                if ((Get-Prop $rv 'required_approving_review_count') -gt 0) { $approval = $true }
                if ((Get-Prop $rv 'require_code_owner_reviews') -eq $true) { $approval = $true }
            }
            $checks = $null -ne (Get-Prop $p 'required_status_checks')
            if ($checks) { $pr = $true }
            $parts += "classic: pull requests $($null -ne $rv), status checks $checks"
        }
        catch { if (-not $failed) { $failed = "the classic protection answer has an unexpected shape ($($_.Exception.Message)): $($classic.Text)" } }
    }
    elseif (Test-Absent $classic) { $parts += 'classic: none' }
    elseif (-not $failed) { $failed = "the classic protection read failed (exit $($classic.Code)): $($classic.Text)" }

    @{ Pr = $pr; Approval = $approval; Failed = $failed; Reading = ($parts -join '; ') }
}

function Invoke-PushDirect {
    param([int]$Issue)
    $b = Get-LandBinding
    $default = $b.Default
    $branch = Get-CurrentBranch
    if ($branch -cne $default) { throw "the checkout is on $branch, not the default branch $default - the direct lane commits on $default" }
    if (-not @((Get-BindingValue ship.direct -Optional) | Where-Object { $_ } | ConvertFrom-Json | Where-Object { $_ }).Count) {
        throw 'ship.direct is empty or absent in the binding - the direct lane is off here'
    }
    $dirty = @(Invoke-Read git @('status', '--porcelain'))
    if ($dirty.Count) { throw "the working tree is not clean ($($dirty.Count) entries, first: $($dirty[0])) - commit or discard them first; only commits are pushed" }

    $fetch = Invoke-Native git @('fetch', '-q', 'origin', $default)
    if ($fetch.Code -ne 0) { throw "cannot fetch origin's ${default}: $($fetch.Text)" }
    $head = @(Invoke-Read git @('rev-parse', 'HEAD'))[0]
    $log = Invoke-Native git @('-c', 'core.quotepath=false', 'log', '--format=', '--name-only', '--no-renames', '-z', "origin/$default..HEAD")
    if ($log.Code -ne 0) { throw "cannot read the range origin/$default..HEAD (exit $($log.Code)): $($log.Text)" }
    $paths = @(($log.Out -join "`n") -split "[`0`n]" | Where-Object { $_ } | Select-Object -Unique)
    if (-not $paths.Count) { throw "nothing to push: no commit over origin/$default changes a file" }
    $miss = Invoke-Native python3 (@((Join-Path $PSScriptRoot 'ouro-binding.py'), 'direct') + $paths)
    if ($miss.Code -eq 1) { throw "the range changes $($miss.Out.Count) path(s) outside [ship].direct: $($miss.Out -join ', ') - nothing is pushed; the run is express or tighter, by contract section 4's classes" }
    if ($miss.Code -ne 0) { throw "ouro-binding.py direct failed (exit $($miss.Code)): $($miss.Text)" }

    $prot = Get-DirectProtection $b.Slug $default
    Write-Output "protection: $($prot.Reading)"
    if ($prot.Failed -or $prot.Approval) {
        $script:Fallback = $true
        Write-Output "reason: $(if ($prot.Failed) { $prot.Failed } else { "an approving review is required on $default" })"
        Write-Output 'fallback: express'
        return
    }
    if ($prot.Pr) {
        if (-not $Issue) { throw "$default requires a pull request, and the branch is named for the issue: pass -Issue <N>" }
        Write-Output 'vehicle: pr'
        $vehicle = "direct/$Issue"
        $null = Invoke-Read git @('checkout', '-q', '-B', $vehicle, 'HEAD')
        try {
            Invoke-PushBranch
            # The commits are on origin's branch now: the local default branch goes back to origin's
            # tip, so the next fetch fast-forwards once the pull request lands.
            $null = Invoke-Read git @('branch', '-f', $default, "origin/$default")
        }
        finally { $null = Invoke-Read git @('checkout', '-q', $default) }
        return
    }
    Write-Output 'vehicle: push'
    $p = Invoke-Native git @('push', 'origin', $default)
    if ($p.Code -ne 0) { throw "the push was refused: $($p.Text)`na non-fast-forward means origin's $default holds commits this clone lacks, and a protection the read did not show refuses the rest: nothing is forced" }
    Write-Output "pushed $default to origin at $head"
    $ls = Invoke-Native git @('ls-remote', '--heads', 'origin', "refs/heads/$default")
    if ($ls.Code -ne 0 -or -not $ls.Out.Count) { throw "cannot read origin's $default after the push: $($ls.Text)" }
    $tip = ($ls.Out[0] -split '\s+')[0]
    if ($tip -ne $head) { throw "origin's $default is at $tip, not the pushed $head - someone pushed in between; fetch and read" }
}

if ($MyInvocation.InvocationName -ne '.') {
    $code = 1
    Invoke-LandEntry Invoke-PushDirect $PSBoundParameters ([ref]$code)
    if ($code -eq 0 -and $script:Fallback) { $code = 3 }
    exit $code
}
