<#
.SYNOPSIS
    Push the feature branch to origin: plainly, or with the lease the sync printed, and never with
    the plain force flag.
.DESCRIPTION
    /ouro:land step 4. Run from the repo root on the feature branch; the repository, the default
    branch and the protected prefixes come from the binding (.claude/ouro.toml, read through
    ouro-binding.py beside this script).

    Without -Lease, or with `-Lease none`, the push is `git push -u origin <branch>`, which
    refuses to overwrite a commit origin holds: a non-fast-forward stops the run, nothing is
    overwritten, and the stop says to fetch and read what origin has. With a lease, the tip
    /ouro:land step 2 printed after `lease:` once it rebased a branch origin already had, the push
    is `--force-with-lease=refs/heads/<branch>:<lease>`: a push someone made since is refused, not
    overwritten. The lease must be a commit this clone has, which the sync fetched. Afterwards
    origin's tip is read back and must be the local HEAD. The default branch and a protected
    prefix are refused.
.PARAMETER Lease
    The tip origin's branch had before the rebase, as step 2 printed it, or `none`.
.EXAMPLE
    pwsh -File <ouro>/bin/Push-Branch.ps1
.EXAMPLE
    pwsh -File <ouro>/bin/Push-Branch.ps1 -Lease 0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b
#>
param([string]$Lease)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Get-LandBinding.ps1')

function Invoke-PushBranch {
    param([string]$Lease)
    $b = Get-LandBinding
    $branch = Get-CurrentBranch
    if ($branch -ceq $b.Default) { throw "the checkout is on $branch, the default branch - a landing pushes the feature branch, and the default branch moves only by the merge" }
    $hit = @($b.Prefixes | Where-Object { $branch.StartsWith($_, [StringComparison]::Ordinal) })
    if ($hit.Count) { throw "$branch has the protected prefix $($hit[0]) - the repository's own release procedure owns it" }
    $head = @(Invoke-Read git @('rev-parse', 'HEAD'))[0]
    if ($Lease -and $Lease -ne 'none') {
        if ((Invoke-Native git @('rev-parse', '--verify', '--quiet', "$Lease^{commit}")).Code -ne 0) {
            throw "the lease '$Lease' is not a commit this clone has - it is the tip step 2 printed after lease:, which the sync fetched"
        }
        $p = Invoke-Native git @('push', "--force-with-lease=refs/heads/${branch}:$Lease", 'origin', $branch)
        if ($p.Code -ne 0) {
            throw "the leased push was refused: $($p.Text)`norigin's $branch moved since $Lease; nothing is overwritten - fetch, read what moved, and decide"
        }
        Write-Output "pushed $branch to origin at $head, with the lease $Lease"
    }
    else {
        $p = Invoke-Native git @('push', '-u', 'origin', $branch)
        if ($p.Code -ne 0) {
            throw "the push was refused: $($p.Text)`na non-fast-forward means origin's $branch holds commits this clone lacks: fetch and read them; the plain force flag is never the push"
        }
        Write-Output "pushed $branch to origin at $head"
    }
    $ls = Invoke-Native git @('ls-remote', '--heads', 'origin', "refs/heads/$branch")
    if ($ls.Code -ne 0 -or -not $ls.Out.Count) { throw "cannot read origin's $branch after the push: $($ls.Text)" }
    $tip = ($ls.Out[0] -split '\s+')[0]
    if ($tip -ne $head) { throw "origin's $branch is at $tip, not the pushed $head - someone pushed in between; fetch and read" }
}

if ($MyInvocation.InvocationName -ne '.') {
    $code = 1
    Invoke-LandEntry Invoke-PushBranch $PSBoundParameters ([ref]$code)
    exit $code
}
