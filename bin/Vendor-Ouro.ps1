<#
.SYNOPSIS
    Copies the ouro plugin's contents into a repo's tree - the standalone exit.
.DESCRIPTION
    For a repo that wants the loop without the plugin dependency. Copies:

      skills/<name>/*   -> <repo>/.claude/skills/<name>/  (all but init: it runs the plugin's installer)
      agents/*          -> <repo>/.claude/agents/
      bin/*.ps1         -> <repo>/<ScriptsDir>/           (default scripts)
      bin/ouro-binding.py -> <repo>/<ScriptsDir>/         (the gates read the binding through it)
      bin/apply-manifest.py -> <repo>/<ScriptsDir>/       (triage and the weekly pass run it)
      bin/external-audit.py -> <repo>/<ScriptsDir>/       (the external-audit skill runs it)
      bin/drift-claims.py -> <repo>/<ScriptsDir>/         (hashes the drift audit's claim evidence)
      bin/forbidden-tokens.py -> <repo>/<ScriptsDir>/     (checks text against the private token list)
      bin/record-counts.py -> <repo>/<ScriptsDir>/        (the review skill runs it over a PR record)
      tests/*           -> <repo>/<ScriptsDir>/tests/     (the fixture suites find the scripts there)
      docs/contract.md  -> <repo>/docs/ouro-contract.md
      docs/docs-governance.md         -> <repo>/docs/ouro-docs-governance.md
      docs/binding.md   -> <repo>/docs/ouro-binding.md   (vendored skills and the contract cite it)
      templates/documentation-rule.md -> <repo>/.claude/rules/documentation.md  (skipped if present)

    Refuses unless -Into is a git work-tree root and -ScriptsDir is repo-relative and resolves
    inside it, before a single file is written, so -WhatIf refuses what a run refuses. Prints
    every file it copies.
    Never touches the target's workflows or settings; it ends with the list of what the human
    must edit by hand, the two .gitignore entries the installer would have appended among them.
    A destination that is a symbolic link, whether or not its target exists, or that lies under a
    directory below the root that is one, is not written: a REFUSED line names it, or that
    directory, since git neither reads a link nor follows one, and the run goes on and exits 0.
    The binding itself stays where it is -- it is the repo's own file. The validator is
    copied beside the scripts because the gates resolve the rolling issue's title through it;
    without it a vendored -Comment run cannot find its target.
.PARAMETER Into
    The consumer repo's root directory.
.PARAMETER ScriptsDir
    Repo-relative directory receiving bin/*.ps1 and tests/. Default: scripts. A rooted path is
    refused, as is one that resolves outside the work tree -- a `..` segment that walks out of it.
    A `..` that walks back inside is not refused, and the plan carries the resolved path either
    way, so `tools/../scripts` is `scripts`.
.EXAMPLE
    pwsh -File <ouro>/bin/Vendor-Ouro.ps1 -Into C:\src\my-repo -WhatIf
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [string]$Into,
    [string]$ScriptsDir = 'scripts'
)

$ErrorActionPreference = 'Stop'
$Plugin = Split-Path $PSScriptRoot -Parent

if (-not (Test-Path -LiteralPath $Into -PathType Container)) { throw "-Into '$Into' is not a directory" }
$Into = (Resolve-Path -LiteralPath $Into).Path
# Both answers below are about $Into, not about the caller's environment: git reads GIT_DIR before
# it looks at a path, and with no work tree of its own it makes the directory it is run in the work
# tree, so every directory answers `true` with an empty prefix and the copy lands where git never
# reads it. The three are unset for the two calls and put back in their finally: these are the only
# git calls the run makes, so nothing after the check can read them. (The installer asks git again
# after its check, and so unsets them for its whole run.) [NullString]::Value, because on Windows
# $null from PowerShell leaves a variable set to an empty string and an empty GIT_DIR makes git
# refuse a real root too, and because -WhatIf skips a Remove-Item on the Env: drive.
$gitVars = @{}
foreach ($name in 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR') {
    $gitVars[$name] = [Environment]::GetEnvironmentVariable($name)
    [Environment]::SetEnvironmentVariable($name, [NullString]::Value)
}
# git prints the root as UTF-8: decoded with a caller's OEM code page, a non-ASCII root names no
# directory, and resolving it throws before the git-root check.
$encoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $top = (git -C $Into rev-parse --show-toplevel 2>$null)
    # Ask git whether this is the root rather than compare spellings: $Into keeps the path it was
    # given, a junction or a symbolic link, while --show-toplevel answers the physical one. Inside
    # the work tree an empty prefix is the root, however the path was reached. Both answers are
    # read because a work tree the repo itself forces elsewhere -- its own core.worktree -- prints
    # an empty prefix outside that tree too, where nothing git reads lands. Exactly two lines,
    # because a prefix is one path and a path may hold a newline: a directory whose name starts
    # with one prints three, and its empty first line reads as a root.
    $rp = @(git -C $Into rev-parse --is-inside-work-tree --show-prefix 2>$null)
    $rpCode = $LASTEXITCODE
}
finally {
    [Console]::OutputEncoding = $encoding
    foreach ($name in $gitVars.Keys) {
        if ($null -ne $gitVars[$name]) { [Environment]::SetEnvironmentVariable($name, $gitVars[$name]) }
    }
}
if (-not $top) { throw "refusing: '$Into' is not inside a git work tree" }
$top = (Resolve-Path -LiteralPath $top.Trim()).Path
if ($rpCode -ne 0 -or $rp.Count -ne 2 -or $rp[0] -cne 'true' -or "$($rp[1])".Trim()) { throw "refusing: '$Into' is not the git root (that is '$top')" }

# -ScriptsDir is repo-relative, and the copy loop joins it to the root: a `..` segment walks out
# of the work tree, where git does not look and every script lands unseen, and a rooted one joins
# to a destination no write can reach -- C:\repo\C:\elsewhere -- which dies part way through the
# copy, after .claude/ is written. Both are refused before the plan is built, since a run that
# cannot place its scripts has nothing partial worth doing, and so -WhatIf refuses them too.
# GetFullPath collapses the `..` segments without asking the filesystem, which a directory that is
# not there yet cannot answer; a link on the way is the copy loop's refusal below, whose walk now
# starts below the root rather than at `$Into/..`, the way its own comment says.
if ([System.IO.Path]::IsPathRooted($ScriptsDir)) { throw "refusing: -ScriptsDir '$ScriptsDir' is not repo-relative" }
$sep = [System.IO.Path]::DirectorySeparatorChar
$scriptsPath = [System.IO.Path]::GetFullPath((Join-Path $Into $ScriptsDir)).TrimEnd($sep)
$treeRoot = $Into.TrimEnd($sep)
# Windows names the same directory in any case, so an ordinal test would refuse a `..` that walks
# back into the tree under another spelling; Invoke-BranchSweep.ps1 picks its comparison the same
# way. The root itself is inside the tree, which is a separate test from the prefix: `<root>x` is
# not `<root>`, so the separator is what makes the prefix a whole segment.
$compare = if ($IsWindows) { 'OrdinalIgnoreCase' } else { 'Ordinal' }
if (-not ($scriptsPath.Equals($treeRoot, $compare) -or $scriptsPath.StartsWith($treeRoot + $sep, $compare))) {
    throw "refusing: -ScriptsDir '$ScriptsDir' is outside the work tree (that is '$scriptsPath')"
}
# The plan then carries what the test judged, not the argument: Windows drops a segment's trailing
# spaces and dots, so there `.. ` normalises to the root here while the raw join keeps it and the
# write dies on a directory of that name -- the partial copy this refusal exists to prevent. A
# filesystem that keeps them reads `.. ` as an ordinary name, inside the tree either way. Below the
# root, that is the resolved path relative to it, which `tools/../scripts` and `./scripts` reach
# alike; at the root, `.`.
$ScriptsDir = $scriptsPath.Substring($treeRoot.Length).Trim($sep)
if (-not $ScriptsDir) { $ScriptsDir = '.' }

# True when the path itself is a symbolic link, whether or not its target exists. A destination
# that is a link is never written through: git does not read one, and the write would land
# outside the tree.
#
# Two traps this is shaped around, both measured on Linux (pwsh 7.4, .NET 8):
#   - [System.IO.File]::Exists returns TRUE for a DANGLING link, so "exists but the target does
#     not" cannot be expressed with it. Any predicate carrying that clause reports $false for
#     every dangling link and the caller writes through it.
#   - Test-Path likewise answers True there, so it cannot gate this check either -- and on
#     Windows it answers False for a dangling link, which is the opposite error.
# On Linux and macOS, LinkTarget reads the link itself (readlink), so it answers for a dangling
# link as for a live one, and it enumerates nothing: Get-Item on a directory there reads every
# ancestor directory, which costs tens of milliseconds under a large /tmp. On Windows LinkTarget
# also answers for a junction, so Get-Item -Force stays there: -Force because an item with the
# Hidden attribute is not found without it, and .LinkType because it reads the link itself.
function Test-SymbolicLink([string]$Path) {
    if ($IsLinux -or $IsMacOS) { return [bool][System.IO.FileInfo]::new($Path).LinkTarget }
    $item = try { Get-Item -Force -LiteralPath $Path -ErrorAction Stop } catch { $null }
    if (-not $item) { return $false }
    return $item.LinkType -eq 'SymbolicLink'
}

# The first directory on $Rel's path, below the work-tree root, that is a symbolic link, or ''.
# New-Item -Force creates a destination's parents straight through one, so the write would land
# wherever it points; git does not follow it. The walk starts below the root and never asks the
# root or anything above it: that path may be reached through a link (a symlinked home, a /mnt
# path), and the root is already established as git's. A `.` segment -- what -ScriptsDir resolving
# to the root leaves behind -- is that root spelled again, and `<root>/.` is the same item, so the
# walk drops it rather than answering for the root it must not ask about.
function Get-LinkedAncestor([string]$Rel) {
    $parts = @($Rel.Split('/') | Where-Object { $_ -cne '.' })
    for ($i = 1; $i -lt $parts.Count; $i++) {
        $dir = $parts[0..($i - 1)] -join '/'
        if (Test-SymbolicLink (Join-Path $Into $dir)) { return $dir }
    }
    return ''
}

# (source, repo-relative destination) pairs. Missing sources are reported, not fatal.
$plan = @()
$skipped = @()
# System.IO, not Get-ChildItem: on Linux, -LiteralPath still globs a '*' or '?' in the directory's
# own path, and would enumerate a sibling. Hidden alone is skipped, which is what a plain
# Get-ChildItem leaves out; case-insensitive, as -Filter matched on every platform.
$enum = [System.IO.EnumerationOptions]@{ MatchCasing = 'CaseInsensitive'; AttributesToSkip = 'Hidden' }
# Every file below $Dir in name order: hidden ones left out, a link to a directory not walked into
# -- a link to an ancestor would otherwise recurse until the path is too long to hold.
function Get-PluginFiles([string]$Dir) {
    foreach ($p in [System.IO.Directory]::GetFileSystemEntries($Dir, '*', $enum) | Sort-Object) {
        if (-not [System.IO.Directory]::Exists($p)) { $p }
        elseif (-not [System.IO.DirectoryInfo]::new($p).LinkTarget) { Get-PluginFiles $p }
    }
}
$skillsRoot = Join-Path $Plugin 'skills'
$skillDirs = @(if ([System.IO.Directory]::Exists($skillsRoot)) {
    [System.IO.Directory]::GetDirectories($skillsRoot, '*', $enum) | Sort-Object
})
foreach ($skillDir in $skillDirs) {
    # init runs the plugin's installer, which copies from the plugin's templates/: neither the
    # installer's templates nor the plugin root exist in a vendored tree.
    $skillName = Split-Path $skillDir -Leaf
    if ($skillName -eq 'init') { continue }
    foreach ($f in Get-PluginFiles $skillDir) {
        $rel = $f.Substring($skillDir.Length).TrimStart('\', '/')
        $plan += , @($f, (Join-Path '.claude/skills' $skillName $rel))
    }
}
$agentsRoot = Join-Path $Plugin 'agents'
$agentFiles = @(if ([System.IO.Directory]::Exists($agentsRoot)) {
    [System.IO.Directory]::GetFiles($agentsRoot, '*', $enum) | Sort-Object
})
foreach ($f in $agentFiles) {
    $plan += , @($f, (Join-Path '.claude/agents' (Split-Path $f -Leaf)))
}
foreach ($f in [System.IO.Directory]::GetFiles((Join-Path $Plugin 'bin'), '*.ps1', $enum) | Sort-Object) {
    $plan += , @($f, (Join-Path $ScriptsDir (Split-Path $f -Leaf)))
}
# The gates read the binding through it -- a vendored tree without it cannot resolve the
# rolling issue's title, and a gate that cannot resolve its target posts nothing.
$binding = Join-Path $Plugin 'bin/ouro-binding.py'
if (Test-Path -LiteralPath $binding) {
    $plan += , @($binding, (Join-Path $ScriptsDir 'ouro-binding.py'))
}
else { Write-Warning "bin/ouro-binding.py not found in the plugin at $Plugin - skipped" }
# The triage skill and the weekly pass both run the applier by name, and its suite is vendored
# with it -- a tree told to run a file the copy leaves out carries a gate that cannot pass.
$applier = Join-Path $Plugin 'bin/apply-manifest.py'
if (Test-Path -LiteralPath $applier) {
    $plan += , @($applier, (Join-Path $ScriptsDir 'apply-manifest.py'))
}
else { Write-Warning "bin/apply-manifest.py not found in the plugin at $Plugin - skipped" }
# The external-audit skill runs the driver by name, and its suite is vendored with it, the same
# way the applier is.
$externalAuditor = Join-Path $Plugin 'bin/external-audit.py'
if (Test-Path -LiteralPath $externalAuditor) {
    $plan += , @($externalAuditor, (Join-Path $ScriptsDir 'external-audit.py'))
}
else { Write-Warning "bin/external-audit.py not found in the plugin at $Plugin - skipped" }
# The drift audit's claim evidence is hashed and read back by this script, and its suite is
# vendored with it, the same way the applier is.
$claimHasher = Join-Path $Plugin 'bin/drift-claims.py'
if (Test-Path -LiteralPath $claimHasher) {
    $plan += , @($claimHasher, (Join-Path $ScriptsDir 'drift-claims.py'))
}
else { Write-Warning "bin/drift-claims.py not found in the plugin at $Plugin - skipped" }
# The private token list's engine is copied by name beside the applier.
$tokenMatcher = Join-Path $Plugin 'bin/forbidden-tokens.py'
if (Test-Path -LiteralPath $tokenMatcher) {
    $plan += , @($tokenMatcher, (Join-Path $ScriptsDir 'forbidden-tokens.py'))
}
else { Write-Warning "bin/forbidden-tokens.py not found in the plugin at $Plugin - skipped" }
# The review skill runs it by name over a PR record, and its suite is vendored with it.
$countTracer = Join-Path $Plugin 'bin/record-counts.py'
if (Test-Path -LiteralPath $countTracer) {
    $plan += , @($countTracer, (Join-Path $ScriptsDir 'record-counts.py'))
}
else { Write-Warning "bin/record-counts.py not found in the plugin at $Plugin - skipped" }
$testsRoot = Join-Path $Plugin 'tests'
$testFiles = @(if ([System.IO.Directory]::Exists($testsRoot)) { Get-PluginFiles $testsRoot })
foreach ($f in $testFiles) {
    $rel = $f.Substring($testsRoot.Length).TrimStart('\', '/')
    $plan += , @($f, (Join-Path $ScriptsDir 'tests' $rel))
}
$contract = Join-Path $Plugin 'docs/contract.md'
if (Test-Path -LiteralPath $contract) { $plan += , @($contract, 'docs/ouro-contract.md') }
else { Write-Warning "docs/contract.md not found in the plugin at $Plugin - skipped" }
$governance = Join-Path $Plugin 'docs/docs-governance.md'
if (Test-Path -LiteralPath $governance) { $plan += , @($governance, 'docs/ouro-docs-governance.md') }
else { Write-Warning "docs/docs-governance.md not found in the plugin at $Plugin - skipped" }
# The vendored skills and the vendored contract cite it, and a vendored tree has no plugin
# directory to resolve it in.
$bindingDoc = Join-Path $Plugin 'docs/binding.md'
if (Test-Path -LiteralPath $bindingDoc) { $plan += , @($bindingDoc, 'docs/ouro-binding.md') }
else { Write-Warning "docs/binding.md not found in the plugin at $Plugin - skipped" }
# The target's own documentation rule is curated -- hand-merging beats clobbering, so an
# existing one is left alone and named in the tail instead. A symbolic link there is not named:
# a hand merge would edit its target, so it is planned and the copy loop refuses it.
$rule = Join-Path $Plugin 'templates/documentation-rule.md'
$ruleRel = '.claude/rules/documentation.md'
$rulePath = Join-Path $Into $ruleRel
if (-not (Test-Path -LiteralPath $rule)) { Write-Warning "templates/documentation-rule.md not found in the plugin at $Plugin - skipped" }
elseif (-not (Test-SymbolicLink $rulePath) -and -not (Get-LinkedAncestor $ruleRel) -and
    (Test-Path -LiteralPath $rulePath)) { $skipped += "$ruleRel already exists: merge $rule into it by hand" }
else { $plan += , @($rule, $ruleRel) }

if ($plan.Count -eq 0) { throw "nothing to vendor from $Plugin" }

Write-Host "Vendoring ouro from $Plugin into $Into"
# A destination that is a symbolic link, or under a directory that is one, is refused before
# ShouldProcess, so -WhatIf prints the REFUSED line a real run prints. The count is of the rest.
$copied = 0
foreach ($pair in $plan) {
    $src, $rel = $pair
    $dest = Join-Path $Into $rel
    $shown = $rel -replace '\\', '/'
    $linkedDir = Get-LinkedAncestor $shown
    if ($linkedDir) {
        Write-Host "REFUSED: $shown not written - $linkedDir is a symbolic link, which git does not follow" -ForegroundColor Yellow
        continue
    }
    if (Test-SymbolicLink $dest) {
        Write-Host "REFUSED: $shown not written - it is a symbolic link, which git does not read" -ForegroundColor Yellow
        continue
    }
    Write-Host "  $shown"
    if ($PSCmdlet.ShouldProcess($dest, "Copy $src")) {
        # System.IO, not New-Item: where the filesystem keeps a segment's trailing space, the
        # provider reads `.. ` as the parent and reports success having created nothing, so the
        # copy below dies part way through the plan.
        [System.IO.Directory]::CreateDirectory((Split-Path $dest -Parent)) | Out-Null
        Copy-Item -LiteralPath $src -Destination $dest -Force
    }
    $copied++
}
Write-Host "$copied file(s)."

Write-Host ''
Write-Host 'Not touched - edit by hand:'
foreach ($s in $skipped) { Write-Host "  - $s" }
# Shown with forward slashes on every platform: Windows keeps native separators in $ScriptsDir,
# and these lines read as a relative path a consumer pastes, not a native one.
$ScriptsDirShown = $ScriptsDir -replace '\\', '/'
Write-Host "  - .github/workflows/*: point every ouro/bin/ path at $ScriptsDirShown/"
Write-Host "  - .claude/skills/*: point every <ouro>/bin/ path at $ScriptsDirShown/"
Write-Host "  - .github/workflows/*: actions/ is not vendored, so a step that uses: the ouro docs-freshness Action becomes an actions/setup-python step (python-version '3.12': reading [docs] needs python3 3.11+, and an older python3 fails the gate with the floor message) and a pwsh step running $ScriptsDirShown/Test-DocsFreshness.ps1 -Mode Gate"
Write-Host '  - .claude/settings.json (or settings.local.json): remove the ouro marketplace and plugin entries'
Write-Host '  - .claude/ouro.toml stays; skills read it from the repo root either way'
# The values are printed, not cited: the example binding is not in the copy plan, and the line
# above tells the human to remove the plugin entries it lives under.
Write-Host '  - .claude/ouro.toml [docs]: exclude = ["node_modules/", "/vendor/", "/_site/", ".claude/", "/tests/docs-freshness/fixtures/"] as the whole list (a declared list replaces the default) -- the vendored fixture tree is deliberately broken'
Write-Host '  - .claude/ouro.toml [docs]: suppress_prefixes = ["bin/Get-RollingIssue.ps1", "bin/Test-AgentReadyAnchors.ps1", "bin/Test-AgentReadyShape.ps1", "docs/contract.md", "docs/binding.md", "templates/"] -- the vendored docs/ouro-contract.md, docs/ouro-docs-governance.md and docs/ouro-binding.md cite plugin paths'
# The installer appends these two on every onboarding, and vendoring does not run it, so a tree
# it never onboarded lacks them. The docs gate honours a committed .gitignore only.
Write-Host '  - .gitignore: .claude/worktrees/ and .claude/ouro.local.toml, committed, the two entries Install-Ouro.ps1 appends (docs/ouro-binding.md, what a consumer must gitignore) -- without the overlay''s, the docs gate reports the vendored binding doc''s citations of that path'
