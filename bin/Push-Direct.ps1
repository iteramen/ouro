<#
.SYNOPSIS
    Verify the direct publication floor, then push the pinned commits through branch protection.
.DESCRIPTION
    Run from a clean default-branch checkout after committing. The script fetches origin and
    asks ouro-direct.py beside it for a plan from that base's binding. Defaults:20 added-plus-
    deleted lines across three files over the entire unpublished range, repository-approved
    paths/types, a nonempty applicable cheap-gate set, and meaningful declared checks for code.
    ship.direct_max_lines and ship.direct_max_files override the size limits.
    Protected files and uncertain Git/command inputs use a PR. Globs remain case-sensitive
    fnmatch patterns on forward-slash paths; * crosses /.

    Every planned run command must actually pass locally. CI alternatives and notices do not
    replace it. The script rechecks final inputs, remote base, branch and protection before
    publication. Fixes and reverts have the same floor. Nothing is forced.

    Invalid checkout, disabled lane, empty range or a failed operation prints STOP and exits1.
    Ordinary ineligibility, a failed gate or changed inputs prints fallback: express, exit3,
    without publishing. Protection is read through both rules and classic endpoints:
      - No pull-request requirement: `vehicle: push`, a plain push of the checked commit (never
        forced; a non-fast-forward stops, exit 1), then origin's tip is read back and must be HEAD.
        Prints `pushed <default> to origin at <sha>`. Exit 0.
      - A pull-request requirement and no required approving review: `vehicle: pr`. The commits are
        pushed to the branch `direct/<Issue>` with Push-Branch.ps1 (printing its `pushed` line), the
        local default branch returns to origin's tip (the commits live on the branch), and the
        checkout returns to it. The pull request is the caller's to open and to merge once its
        required checks are green. Exit 0.
      - A required approving review, or failed/unrecognized protection: fallback: express,
        nothing pushed. Exit 3. A plan not offering protection, or an unprotected branch, is
        not a failed read. The caller retains default-branch CI watching and PR handling.
    Inline command execution currently requires native Linux; ordinary Windows delivery can
    continue through the driver's CI-backed PR route. Gate diagnostics are prefixed data,
    separate from the publisher's vehicle/pushed/direct-checks records.
.PARAMETER Issue
    The issue the commits fix: names the branch under `vehicle: pr`. Required only there.
.PARAMETER ExpectedHead
    If supplied, require this exact commit before planning or executing checks. Both publication
    vehicles remain pinned to the checked commit; a moved local head is never silently accepted.
.PARAMETER ExpectedBase
    If supplied, require the freshly fetched origin tip to match this exact planning base. A
    changed unpublished range must be replanned before it can authorize publication.
.EXAMPLE
    pwsh -File <ouro>/bin/Push-Direct.ps1
.EXAMPLE
    pwsh -File <ouro>/bin/Push-Direct.ps1 -Issue 940
#>
param([int]$Issue, [ValidatePattern('^(?:[0-9a-f]{40}|[0-9a-f]{64})$')][string]$ExpectedHead,
    [ValidatePattern('^(?:[0-9a-f]{40}|[0-9a-f]{64})$')][string]$ExpectedBase)

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

function Get-DirectPlan([string]$Root, [string]$Base, [string]$Head, [switch]$Check) {
    $helper = Join-Path $PSScriptRoot 'ouro-direct.py'
    if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw 'ouro-direct.py is unavailable; no direct publication is permitted' }
    $arguments = @($helper, '--repo', $Root, '--base', $Base, '--head', $Head)
    if ($Check) { $arguments += '--check' }
    $r = Invoke-Native python3 $arguments
    if ($r.Code -notin 0, 3) { throw "direct policy could not be read (exit $($r.Code)): $($r.Text)" }
    try { $plan = ($r.Out -join "`n") | ConvertFrom-Json }
    catch { throw 'direct policy returned no readable plan' }
    $kind = if ($Check) { 'checked-plan' } else { 'plan' }
    if ($plan.kind -cne $kind -or $plan.base -cne $Base -or $plan.head -cne $Head -or $plan.execution_proof -ne $false) {
        throw 'direct policy returned an unexpected plan identity'
    }
    if (($r.Code -eq 0) -ne ($plan.eligible -eq $true -and $plan.route -ceq 'direct')) {
        throw 'direct policy returned inconsistent eligibility'
    }
    $plan
}

function Stop-DirectPolicy([string]$Reason) {
    $script:Fallback = $true
    foreach ($line in ($Reason -split '\r\n|\r|\n')) { Write-Output "reason: $line" }
    Write-Output 'fallback: express'
}

function Invoke-PushDirect {
    param([int]$Issue, [string]$ExpectedHead, [string]$ExpectedBase)
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
    $root = @(Invoke-Read git @('rev-parse', '--show-toplevel'))[0]
    $base = @(Invoke-Read git @('rev-parse', "origin/$default"))[0]
    if ($ExpectedBase -and $base -cne $ExpectedBase) { throw 'the requested direct planning base changed before checks - nothing is pushed' }
    $head = @(Invoke-Read git @('rev-parse', 'HEAD'))[0]
    if ($ExpectedHead -and $head -cne $ExpectedHead) { throw 'the requested direct publication head changed before checks - nothing is pushed' }
    if ($base -ceq $head) { throw "nothing to push: no commit over origin/$default changes a file" }
    $plan = Get-DirectPlan $root $base $head
    if (-not $plan.eligible) { Stop-DirectPolicy ($plan.reasons -join '; '); return }

    $prot = Get-DirectProtection $b.Slug $default
    Write-Output "protection: $($prot.Reading)"
    if ($prot.Failed -or $prot.Approval) {
        Stop-DirectPolicy $(if ($prot.Failed) { $prot.Failed } else { "an approving review is required on $default" })
        return
    }
    if ($prot.Pr -and -not (Get-Command Invoke-PushBranch -CommandType Function).Parameters.ContainsKey('ExpectedHead')) {
        throw 'Push-Branch.ps1 lacks checked-head support; update the direct helper bundle before retrying'
    }
    if (-not @($plan.commands).Count) { throw 'direct policy returned no mandatory commands' }
    $planned = $plan | ConvertTo-Json -Depth 30 -Compress
    $checked = Get-DirectPlan $root $base $head -Check
    foreach ($output in $checked.outputs) {
        foreach ($line in (($output.out + $output.err) -split '\r\n|\r|\n' | Where-Object { $_ })) {
            Write-Output "check-output: $line"
        }
        $outcome = if ($null -eq $output.exit) { 'unverified' } else { $output.exit }
        Write-Output ('gate `{0}`: exit {1}' -f $output.cmd, $outcome)
    }
    if (-not $checked.eligible) { Stop-DirectPolicy ($checked.reasons -join '; '); return }
    $executed = @($checked.checks)
    if (($checked.commands | ConvertTo-Json -Depth 20 -Compress) -cne ($plan.commands | ConvertTo-Json -Depth 20 -Compress) -or
            $executed.Count -ne @($plan.commands).Count) { throw 'direct execution returned a different mandatory-check plan' }
    # A passed command is useful only while the same final inputs and trusted policy remain.
    $after = Get-DirectPlan $root $base $head
    if (($after | ConvertTo-Json -Depth 30 -Compress) -cne $planned) {
        Stop-DirectPolicy 'final inputs or direct policy changed during mandatory checks'; return
    }
    $remote = Invoke-Native git @('ls-remote', '--heads', 'origin', "refs/heads/$default")
    if ($remote.Code -ne 0 -or $remote.Out.Count -ne 1 -or ($remote.Out[0] -split '\s+')[0] -cne $base) {
        Stop-DirectPolicy "origin's $default changed or cannot be verified after mandatory checks"; return
    }
    if ((Get-CurrentBranch) -cne $default) { Stop-DirectPolicy 'the checked-out branch changed during mandatory checks'; return }
    $prot = Get-DirectProtection $b.Slug $default
    if ($prot.Failed -or $prot.Approval) {
        Stop-DirectPolicy $(if ($prot.Failed) { $prot.Failed } else { "an approving review is required on $default" }); return
    }
    if ((Get-CurrentBranch) -cne $default -or @(Invoke-Read git @('rev-parse', 'HEAD'))[0] -cne $head) {
        Stop-DirectPolicy 'the checked publication head or branch changed during the protection read'; return
    }
    Write-Output ('direct-checks: ' + (ConvertTo-Json -InputObject $executed -Depth 10 -Compress))
    if ($prot.Pr) {
        if (-not $Issue) { throw "$default requires a pull request, and the branch is named for the issue: pass -Issue <N>" }
        if (-not (Get-Command Invoke-PushBranch -CommandType Function).Parameters.ContainsKey('ExpectedHead')) {
            throw 'Push-Branch.ps1 lacks checked-head support; update the direct helper bundle before retrying'
        }
        Write-Output 'vehicle: pr'
        $vehicle = "direct/$Issue"
        $exists = Invoke-Native git @('show-ref', '--verify', '--quiet', "refs/heads/$vehicle")
        if ($exists.Code -notin 0, 1) { throw 'cannot establish the local PR branch identity - nothing is pushed' }
        if ($exists.Code -eq 0) {
            if (@(Invoke-Read git @('rev-parse', "refs/heads/$vehicle"))[0] -cne $head) {
                throw 'the local PR branch holds a different commit; preserve it before retrying - nothing is pushed'
            }
            $null = Invoke-Read git @('checkout', '--no-overwrite-ignore', '-q', $vehicle)
        }
        else { $null = Invoke-Read git @('checkout', '--no-overwrite-ignore', '-q', '-b', $vehicle, $head) }
        try {
            Invoke-PushBranch -ExpectedHead $head
            # The commits are on origin's branch now: the local default branch goes back to origin's
            # checked base only if it still names H. A concurrent owner's H2 keeps its reference.
            if (@(Get-Worktrees | Where-Object { $_.Branch -ceq $default }).Count) { throw 'the default branch is now used by another worktree; its reference is retained' }
            $null = Invoke-Read git @('update-ref', "refs/heads/$default", $base, $head)
        }
        finally { $null = Invoke-Read git @('checkout', '--no-overwrite-ignore', '-q', $default) }
        return
    }
    Write-Output 'vehicle: push'
    $p = Invoke-Native git @('push', 'origin', "${head}:refs/heads/$default")
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
