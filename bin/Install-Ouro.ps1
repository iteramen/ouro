<#
.SYNOPSIS
    Writes a repo's ouro onboarding files: the binding, the gitignore entries, the docs-freshness
    and CI workflows and the issue intake files on request, and the loop labels.
.DESCRIPTION
    Run from the consumer repo's root, or name it with -Into. Refuses unless that directory is a
    git work-tree root. Every step is idempotent and none overwrites a file: an existing one is
    named as skipped and the run goes on. A write that fails is refused as well: a REFUSED line
    names the file, or the gitignore entry, and why, and the run goes on and exits 0. A destination
    file that is a symbolic link is refused at every step, whether or not its target exists, since
    git does not read one. So is a destination under a directory that is one, at any depth below
    the work-tree root, since git does not follow it and the write would land wherever it points:
    the REFUSED line names that directory. The root itself and the path above it are not asked. In
    this order:

      1. .claude/ouro.toml, when the repo has none, from templates/ouro.toml.example: repo.slug
         and repo.default_branch from `gh repo view <origin's URL>` -- the repository this clone's
         origin remote names, never gh's own pick among the remotes, which in a fork's clone is
         the parent -- owner.ruling_approvers from `gh api user`, ship.review from -Review,
         ship.landing from -Landing, and the example's [[gate]] table removed. That gate is a
         placeholder: a made-up verify command makes every issue executable against a check that
         is not the repo's, while no gate makes execute stop and name the uncovered area. A value
         gh cannot resolve keeps the example's placeholder, and one WARNING line names every such
         key. A URL shaped the way gh spells a repository, [HOST/]OWNER/REPO, is never asked, and
         neither is a bare word: gh reads each as a repository name. One of them that resolves --
         octocat/Hello-World, or an https URL with the scheme forgotten -- would write a
         stranger's slug into the binding and create the labels there; a relative local path of
         that shape, ../mirror or ./x, names an owner GitHub cannot have, so it would only have
         failed. Both keep the placeholders, and the skip line names the URL. Every other value
         carrying a host or a path is asked, and where gh cannot resolve one the placeholders
         stay too, by that failure rather than by the skip.
      2. .gitignore: .claude/worktrees/ and .claude/ouro.local.toml (docs/binding.md), each
         appended unless a .gitignore in the work tree already ignores it. Only that source
         counts: .git/info/exclude and core.excludesFile ignore the path in this clone alone, and
         every other clone would still lack the entry. `git check-ignore --no-index` answers, run
         against an empty git dir over the work tree so neither clone-local source takes part; the
         dir is made with no template, so a machine's init.templateDir cannot seed its
         info/exclude either. .claude/worktrees/ is asked as .claude/worktrees/ouro-probe, an
         empty directory in a temp tree under TEMP that holds copies of the .gitignore files on
         its path, so asking writes nothing into the work tree. A directory, because git applies
         a rule ending in a slash only to a path it sees as a directory: *, !*.*, !*/ un-ignores
         a worktree and .claude/worktrees/*/ ignores one, and a path that is not there hides
         both. No trailing slash, because git answers a path ending in one from an empty pattern
         too (a blank line in a CRLF file, a line of only spaces), which ignores nothing. Only git's
         record for the path asked counts, and a negated match ignores nothing. An entry that the
         root .gitignore already holds but git does not ignore is un-ignored by a negation after
         it or in a deeper .gitignore: a WARNING names it, and no second line is appended. The
         root .gitignore's lines are compared with the entry as git reads them: the file's bytes,
         a UTF-8 byte-order mark skipped, and from each line one trailing CR dropped, the rest cut
         at a NUL, then the trailing spaces dropped. A string search is wrong both ways:
         **/.claude/* ignores both with neither string present, and a commented-out entry ignores
         nothing. The rest of the file is never rewritten; a missing .gitignore is created, and
         one that cannot be read or written, or is a symbolic link (git does not read one), is
         refused: a REFUSED line names each entry and why, and the run goes on.
      3. -DocsFreshness: .github/workflows/docs-freshness.yml, when absent, from
         templates/docs-freshness.yml with both placeholders filled. The branch comes from gh,
         else from a binding that existed before this run -- never from the stub step 1 wrote,
         whose branch is a placeholder. The tag is v + this plugin's .claude-plugin/plugin.json
         version (every version bump is also a tag). An unknown branch or an unreadable version
         refuses the copy and says why: a kept branch placeholder never fires, a kept tag
         placeholder fails to resolve the Action.
      4. -CI: .github/workflows/ci.yml, when absent, from templates/ci.yml with both placeholders
         filled the same way as -DocsFreshness's workflow: the branch from gh, else from a binding
         that existed before this run, and the tag v + this plugin's .claude-plugin/plugin.json
         version. An unknown branch or an unreadable version refuses the copy and says why.
      5. -IssueIntake: three files, each when absent, copied as the template's
         bytes: .github/ISSUE_TEMPLATE/work-item.yml from
         templates/work-item.yml, .github/ISSUE_TEMPLATE/config.yml from
         templates/issue-template-config.yml (the only name GitHub reads a template chooser
         config under), and .github/workflows/issue-intake-label.yml from
         templates/issue-intake-label.yml. None carries a placeholder and none takes a value
         from the binding, so nothing is filled. A template missing from the plugin, or one
         that cannot be read, refuses that file and says why, and the others are still copied.
      6. Labels, last, so a failure leaves steps 1-5 done: New-OuroLabels.ps1 -Repo <slug>
         -BindingPath <the binding step 1 or an earlier run wrote>, on the repository the binding
         names -- repo.slug read back with ouro-binding.py from a binding that was here before
         this run, else the slug step 1 wrote -- and only when that slug is known, is an owner
         and a name in letters, digits, dot, dash or underscore, is not the example's
         placeholder, and `gh auth status` succeeds. Otherwise the step prints why it was skipped
         and the run still exits 0. New-OuroLabels.ps1 also creates the binding's declared
         [labels] scope, area and type that the repository lacks, and never with --force: those
         are the team's, not the contract's, so an existing one is never edited.

    -WhatIf writes nothing, labels included (it propagates into New-OuroLabels.ps1). It prints the
    REFUSED line for a symbolic link and for a .gitignore that cannot be written, as a real run
    does; a directory that refuses a new file shows only when a real run writes.
.PARAMETER Into
    The consumer repo's root directory. Default: the working directory.
.PARAMETER DocsFreshness
    Also copy the docs-freshness gate workflow.
.PARAMETER CI
    Also copy the CI gate-loop workflow, which runs the repo's [[gate]] list through the ouro
    plugin's shipped actions/gates Action.
.PARAMETER IssueIntake
    Also copy the issue form, the template chooser config and the issue intake workflow, the files
    a new issue enters the loop through. The weekly-pass workflow is not among them.
.PARAMETER Review
    ship.review for a binding this run creates: copilot, adversarial-review, external-audit or
    none. Default: adversarial-review. An existing binding is never edited.
.PARAMETER CopilotBotId
    ship.copilot_bot_id; required with -Review copilot, as `ouro-binding.py check` requires it. On
    github.com it is one value for every repository, the one the plugin's docs/binding.md shows in
    its [ship] example, so there is nothing to look up. Elsewhere, or to check it, the ouro init
    skill (skills/init/SKILL.md, "If copilot") has the command: the reviewer's node_id in the
    timeline of a PR, in a repository the owner names, that already carries a Copilot review.
.PARAMETER ExternalCli
    ship.external_cli; required with -Review external-audit, as `ouro-binding.py check` requires
    it. One of the adapters bin/external-audit.py ships: grok, claude, codex or copilot today.
.PARAMETER ExternalModel
    ship.external_model; optional, only with -Review external-audit. Default: default, meaning
    the CLI's own default model, and written into the new binding only when another value is
    passed.
.PARAMETER Landing
    ship.landing for a binding this run creates: merge-squash, rebase-squash, rebase-merge or
    merge-merge -- how a branch is brought up to date and how its PR lands, as one named pair.
    Default: merge-squash, the loop's original shape (merge to sync, squash to land). An existing
    binding is never edited.
.EXAMPLE
    pwsh -File <ouro>/bin/Install-Ouro.ps1 -DocsFreshness -WhatIf
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Into = (Get-Location).Path,
    [switch]$DocsFreshness,
    [switch]$CI,
    [switch]$IssueIntake,
    [ValidateSet('copilot', 'adversarial-review', 'external-audit', 'none')]
    [string]$Review = 'adversarial-review',
    [string]$CopilotBotId,
    [string]$ExternalCli,
    [string]$ExternalModel = 'default',
    [ValidateSet('merge-squash', 'rebase-squash', 'rebase-merge', 'merge-merge')]
    [string]$Landing = 'merge-squash'
)

$ErrorActionPreference = 'Stop'
$Plugin = Split-Path $PSScriptRoot -Parent
$Utf8 = [System.Text.UTF8Encoding]::new($false)

# ship.external_cli's known set: bin/ouro-binding.py's EXTERNAL_CLIS, pinned equal to it and to
# bin/external-audit.py's own ADAPTERS by a test. -ExternalCli takes no [ValidateSet] of its own:
# unlike -Review and -Landing, it has no default inside any such set -- it is optional, required
# only under -Review external-audit -- and ValidateSet validates a parameter's default too, not
# only a value the caller passed, which would refuse every run that left it out.
$KnownExternalClis = @('grok', 'claude', 'codex', 'copilot')

# ValidateSet matches without case and binds the argument as typed, so each validated value is
# lowered here, before anything reads it: the binding holds what `ouro-binding.py check` accepts.
$Review = $Review.ToLowerInvariant()
$ExternalCli = $ExternalCli.ToLowerInvariant()
$Landing = $Landing.ToLowerInvariant()

# The validator's rules, asked before anything is written.
if ($Review -eq 'copilot' -and -not $CopilotBotId) {
    throw 'refusing: -Review copilot requires -CopilotBotId (Get-Help -Detailed names where to find it)'
}
if ($Review -eq 'external-audit' -and -not $ExternalCli) {
    throw "refusing: -Review external-audit requires -ExternalCli, one of: $($KnownExternalClis -join ', ')"
}
if ($ExternalCli -and $ExternalCli -notin $KnownExternalClis) {
    throw "refusing: -ExternalCli '$ExternalCli' is not one of: $($KnownExternalClis -join ', ')"
}

if (-not (Test-Path -LiteralPath $Into -PathType Container)) { throw "-Into '$Into' is not a directory" }
$Into = (Resolve-Path -LiteralPath $Into).Path
# Every git call in this run is about $Into, not about the caller's environment: git reads GIT_DIR
# before it looks at a path, and -C does not win over it. A directory that is no repository then
# answers `true` with an empty prefix, and past the check the remote lookup, the ignore probe and
# the labels all read the caller's repository -- its origin into this repo's binding, its slug into
# every label write. So the three are unset for the whole run, not for the check, and put back in
# the finally at the end. [NullString]::Value, because on Windows $null from PowerShell leaves a
# variable set to an empty string and an empty GIT_DIR makes git refuse a real root too, and
# because -WhatIf skips a Remove-Item on the Env: drive.
$gitVars = @{}
foreach ($name in 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR') {
    $gitVars[$name] = [Environment]::GetEnvironmentVariable($name)
    [Environment]::SetEnvironmentVariable($name, [NullString]::Value)
}
try {
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
    finally { [Console]::OutputEncoding = $encoding }
    if (-not $top) { throw "refusing: '$Into' is not inside a git work tree" }
    $top = (Resolve-Path -LiteralPath $top.Trim()).Path
    if ($rpCode -ne 0 -or $rp.Count -ne 2 -or $rp[0] -cne 'true' -or "$($rp[1])".Trim()) { throw "refusing: '$Into' is not the git root (that is '$top')" }

    # Runs gh, git or python and never throws: a missing command is a failed call like any other.
    # Output is decoded as UTF-8 for the same reason as the git call above.
    function Invoke-Tool {
        param([string]$Name, [string[]]$ArgList)
        $saved = [Console]::OutputEncoding
        try {
            [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
            $all = @(& $Name @ArgList 2>&1)
            $code = $LASTEXITCODE
        }
        catch { return [pscustomobject]@{ Ok = $false; Out = ''; Err = "$_" } }
        finally { [Console]::OutputEncoding = $saved }
        $err = @($all | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_".Trim() }) -join ' '
        $out = (@($all | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] }) -join "`n").Trim()
        [pscustomobject]@{ Ok = ($code -eq 0); Out = $out; Err = $err }
    }

    function ConvertTo-TomlString([string]$Value) { '"' + $Value.Replace('\', '\\').Replace('"', '\"') + '"' }

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
    # path), and the root is already established as git's.
    function Get-LinkedAncestor([string]$Rel) {
        $parts = $Rel.Split('/')
        for ($i = 1; $i -lt $parts.Count; $i++) {
            $dir = $parts[0..($i - 1)] -join '/'
            if (Test-SymbolicLink (Join-Path $Into $dir)) { return $dir }
        }
        return ''
    }

    # Replaces the one line of the example that matches. A template edit that renames or duplicates
    # the line throws, rather than writing a binding that silently kept its placeholder.
    # A remote URL's userinfo, wherever the text came from -- this script's own line, or gh echoing
    # the argument it rejected -- replaced by a marker, so a clone carrying a token in its remote does
    # not publish it to stdout, which in CI is the build log. It runs to the last @ before the path,
    # since a second @ is part of the credential, and it takes a URL with no scheme (`user@host:path`)
    # as well as one with. An @ inside a path is left alone: a match starts at the value's own start,
    # after whitespace, or after a quote or bracket, which is where gh's echo puts it.
    function Hide-Userinfo([string]$Text) {
        $Text -replace '(?<=^|[\s"''(\[<=:])([a-z][a-z0-9+.-]*://)?[^/\\\s"''()\[\]<>]*@', '${1}***@'
    }

    function Set-ExampleLine([string]$Text, [string]$Pattern, [string]$Line) {
        $rx = [regex]::new($Pattern, 'Multiline')
        if ($rx.Matches($Text).Count -ne 1) { throw "templates/ouro.toml.example: expected exactly one line matching /$Pattern/" }
        $rx.Replace($Text, $Line.Replace('$', '$$'), 1)
    }

    # The example's own slug, which marks a binding an earlier run wrote while gh was failing: its
    # values are the stub's placeholders, not the repo's. Steps 3 and 5 both ask, and neither may
    # treat a placeholder as a repository. The example itself answers wherever it can be read; where
    # it cannot -- a vendored tree has no templates/, since Vendor-Ouro copies bin/ -- the placeholder
    # the template ships stands in, so those trees still tell a filled binding from an unfilled one.
    # The suite asserts the two agree, so the template cannot drift away from this line unnoticed.
    $examplePath = Join-Path $Plugin 'templates/ouro.toml.example'
    $exampleSlug = 'owner/name'
    try {
        $fromExample = [regex]::Match([System.IO.File]::ReadAllText($examplePath), '(?m)^slug = "([^"]*)"').Groups[1].Value
        if ($fromExample) { $exampleSlug = $fromExample }
    }
    catch { }

    # The repository `origin` names is the one ouro manages. gh is asked for that URL by name: a bare
    # `gh repo view` picks among all the clone's remotes -- a `gh repo set-default` value, then
    # upstream, github, origin -- so in a fork's clone it answers the parent, and the binding and the
    # labels would both name a repository this clone only contributes to. The slug feeds step 1 and
    # step 5, the branch step 1 and step 3; only gh's answer counts as resolved.
    # config --get answers the URL or nothing, where get-url prints the remote's NAME for a remote
    # with no url set -- a name that would reach gh as a repository in the signed-in owner's
    # namespace. get-url is what is passed on, since it expands an insteadOf rewrite and config does
    # not; config only answers whether a URL is set at all.
    $originRaw = Invoke-Tool 'git' @('-C', $Into, 'config', '--get', 'remote.origin.url')
    $originUrl = Invoke-Tool 'git' @('-C', $Into, 'remote', 'get-url', 'origin')
    $slug = ''
    $branch = ''
    # A URL names a host or a path, so it carries one of these. A single bare word does not, and gh
    # reads such a word as a repository of its own -- `mirror` would resolve to <signed-in owner>/mirror
    # and the binding would name a repository this clone has nothing to do with. Carrying one is not
    # enough, though: git takes a relative local path as a remote URL, and a path can be spelled
    # exactly as gh spells a repository. gh's own error names that spelling -- [HOST/]OWNER/REPO -- so
    # the refusal takes it whole: `octocat/Hello-World` is a stranger's, `../mirror` and `./x` are
    # names of their own, and `github.com/octocat/Hello-World`, an https URL with the scheme
    # forgotten, resolves at gh as that repository. A real URL carries a scheme, a userinfo, a colon
    # or a rooted path, and none of those fits the shape.
    $RepoNameShape = '^(?:[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*/)?[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$'
    $originSet = $originRaw.Ok -and $originRaw.Out -and $originUrl.Ok -and $originUrl.Out -match '[:/\\]' -and
        $originUrl.Out -notmatch $RepoNameShape
    if (-not $originSet) {
        # Which of the four it is: git answers a missing remote, one with no url, and a url it cannot
        # read almost alike, and "no origin remote" is a false cause for the others. Remote names are
        # case-sensitive to git, so the list is searched that way.
        $remotes = Invoke-Tool 'git' @('-C', $Into, 'remote')
        $viewWhy = if (-not ($remotes.Ok -and (@($remotes.Out -split "`n" | ForEach-Object { $_.Trim() }) -ccontains 'origin'))) {
            'the clone has no origin remote'
        }
        elseif (-not ($originRaw.Ok -and $originRaw.Out)) { 'the origin remote has no URL set' }
        elseif (-not $originUrl.Ok) { "origin's URL could not be read: $(Hide-Userinfo $originUrl.Err)" }
        elseif ($originUrl.Out -match $RepoNameShape) {
            "origin's URL is shaped like a repository name, not a URL ($(Hide-Userinfo $originUrl.Out)); gh would read it as that repository, so it is not asked"
        }
        else { "origin's URL names no host or path ($(Hide-Userinfo $originUrl.Out))" }
    }
    else {
        $shownUrl = Hide-Userinfo $originUrl.Out
        $view = Invoke-Tool 'gh' @('repo', 'view', $originUrl.Out, '--json', 'nameWithOwner,defaultBranchRef')
        if ($view.Ok) {
            try {
                $repo = $view.Out | ConvertFrom-Json
                $slug = [string]$repo.nameWithOwner
                $branch = [string]$repo.defaultBranchRef.name
            }
            catch { }
        }
        # gh's own message is masked too: it echoes the argument it rejected, verbatim, so masking
        # only the copy this script composes publishes the credential anyway.
        $viewWhy = if (-not $view.Ok) { "gh repo view $shownUrl failed: $(Hide-Userinfo $view.Err)" } else { "gh repo view $shownUrl answered without it" }
    }

    # ── 1. the binding ───────────────────────────────────────────────────────────────────────────
    $bindingRel = '.claude/ouro.toml'
    $bindingPath = Join-Path $Into $bindingRel
    $bindingLinkedDir = Get-LinkedAncestor $bindingRel
    $bindingIsLink = Test-SymbolicLink $bindingPath
    # Only the WRITE is refused. $bindingExisted answers "was a binding here before this run", which
    # step 3 reads to find the default branch -- a read through the link, which is harmless and which
    # a symlinked binding answers correctly. Folding the refusal into it cost a working repo its
    # workflow and printed "the repo had no binding before this run" over a binding that was right
    # there. A read that does fail is reported by step 3 on its own terms.
    $bindingExisted = Test-Path -LiteralPath $bindingPath
    if ($bindingLinkedDir) {
        Write-Host "REFUSED: $bindingRel not written - $bindingLinkedDir is a symbolic link, which git does not follow" -ForegroundColor Yellow
    }
    elseif ($bindingIsLink) {
        Write-Host "REFUSED: $bindingRel not written - it is a symbolic link, which git does not read" -ForegroundColor Yellow
    }
    elseif ($bindingExisted) {
        Write-Host "skip: $bindingRel exists and is left as it is"
        if ($PSBoundParameters.ContainsKey('Review')) { Write-Host "  -Review is written into a new binding only; set ship.review in $bindingRel by hand" }
        if ($PSBoundParameters.ContainsKey('Landing')) { Write-Host "  -Landing is written into a new binding only; set ship.landing in $bindingRel by hand" }
    }
    else {
        $example = Join-Path $Plugin 'templates/ouro.toml.example'
        if (-not (Test-Path -LiteralPath $example)) { throw "templates/ouro.toml.example not found in the plugin at $Plugin" }
        $toml = [System.IO.File]::ReadAllText($example)
        $nl = if ($toml.Contains("`r`n")) { "`r`n" } else { "`n" }
        $placeholders = @()

        if ($slug) { $toml = Set-ExampleLine $toml '^slug = [^\r\n]*' "slug = $(ConvertTo-TomlString $slug)" }
        else { $placeholders += 'repo.slug' }
        if ($branch) { $toml = Set-ExampleLine $toml '^default_branch = [^\r\n]*' "default_branch = $(ConvertTo-TomlString $branch)" }
        else { $placeholders += 'repo.default_branch' }
        $user = Invoke-Tool 'gh' @('api', 'user', '--jq', '.login')
        if ($user.Ok -and $user.Out) { $toml = Set-ExampleLine $toml '^ruling_approvers = [^\r\n]*' "ruling_approvers = [$(ConvertTo-TomlString $user.Out)]" }
        else { $placeholders += 'owner.ruling_approvers' }

        $reviewLine = "review = $(ConvertTo-TomlString $Review)"
        if ($Review -eq 'copilot') { $reviewLine += "${nl}copilot_bot_id = $(ConvertTo-TomlString $CopilotBotId)" }
        if ($Review -eq 'external-audit') {
            $reviewLine += "${nl}external_cli = $(ConvertTo-TomlString $ExternalCli)"
            if ($ExternalModel -and $ExternalModel -ne 'default') {
                $reviewLine += "${nl}external_model = $(ConvertTo-TomlString $ExternalModel)"
            }
        }
        $toml = Set-ExampleLine $toml '^review = "[^\r\n]*' $reviewLine
        $toml = Set-ExampleLine $toml '^landing = [^\r\n]*' "landing = $(ConvertTo-TomlString $Landing)"

        # The whole table: its header through the line before the next table header (or the end).
        $toml = [regex]::Replace($toml, '(?ms)^\[\[gate\]\][^\n]*\n.*?(?=^\[|\z)', '')
        if ($toml -match '(?m)^\[\[gate\]\]') { throw 'templates/ouro.toml.example: the [[gate]] table could not be removed' }

        $bindingRefused = $false
        if ($PSCmdlet.ShouldProcess($bindingPath, 'Create the binding')) {
            try {
                [System.IO.Directory]::CreateDirectory((Split-Path $bindingPath -Parent)) | Out-Null
                [System.IO.File]::WriteAllText($bindingPath, $toml, $Utf8)
                Write-Host "wrote: $bindingRel (ship.review = $Review, ship.landing = $Landing; no [[gate]]: declare the repo's own verify commands)"
            }
            catch {
                $bindingRefused = $true
                Write-Host "REFUSED: $bindingRel not written - the write failed ($($_.Exception.GetBaseException().Message))" -ForegroundColor Yellow
            }
        }
        if ($placeholders -and -not $bindingRefused) {
            Write-Host "WARNING: $bindingRel keeps the example's placeholder for $($placeholders -join ', ') - gh could not resolve them; fill each by hand" -ForegroundColor Yellow
        }
    }

    # ── 2. the gitignore entries ─────────────────────────────────────────────────────────────────
    $ignoreRel = '.gitignore'
    $ignorePath = Join-Path $Into $ignoreRel
    # Only a .gitignore in the work tree reaches every clone. The repo's own git also reads
    # .git/info/exclude and core.excludesFile, and a rule there on .claude/ hides a .gitignore rule on
    # the entry beneath it, so the question goes to an empty git dir, with core.excludesFile pointed at
    # nothing and the repo's core.ignoreCase carried over. --template= keeps a machine's
    # init.templateDir from seeding that dir's info/exclude. -z, which git takes only with --stdin,
    # keeps the named source unquoted. The work tree git reads is a temp tree beside that dir: the
    # empty directory .claude/worktrees/ouro-probe, and a copy of each of .gitignore, .claude/.gitignore
    # and .claude/worktrees/.gitignore that is a regular file in the work tree (git reads no symbolic
    # link, and one that cannot be copied is left out, as git skips one it cannot open). The directory
    # entry is asked as that empty directory, because git applies a rule ending in a slash only to a
    # path it sees as a directory: *, !*.*, !*/ un-ignores a worktree and .claude/worktrees/*/ ignores
    # one, and a path that is not there hides both. It is asked with no trailing slash, because git
    # answers a path ending in one from an empty pattern too (a blank CRLF line, a line of only spaces,
    # a UTF-16 file's lines after the first), which ignores nothing. The tree is built with .NET calls,
    # which -WhatIf does not skip, so a -WhatIf run asks the question a real run asks, and asking
    # writes nothing into the work tree. A rule matching that name and not a real worktree's
    # (.claude/worktrees/ouro-*) still reads as ignoring the entry; /ouro:execute asks about the
    # worktree it creates, and stops.
    # Its records are source, line, pattern, path, and only the one naming the asked path counts: the
    # pipeline sends a line break after the NUL, and git checks that as a path of its own. A negated
    # match (!pattern) ignores nothing, so it appends. An entry git does not ignore although the root
    # .gitignore already holds that exact line is un-ignored by a negation after it or in a deeper
    # .gitignore (git may report no record at all, as for a negated directory), so the run warns rather
    # than appending it again on every run.
    # The file is read as git reads it: its bytes as UTF-8 with no byte-order-mark detection, one
    # leading U+FEFF skipped, and from each line one trailing CR dropped, the rest cut at a NUL (git
    # reads a pattern up to its first NUL), then the trailing spaces dropped. Each line is compared
    # ordinally: -ccontains and a culture-sensitive EndsWith ignore NUL, so a UTF-16 file would hold
    # the entry to them, and a text ending in LF then NUL would get no line break before the append.
    # A text ending in a lone CR gets an LF before the append, not a CRLF, which would turn that last
    # line into the pattern plus a CR. The read comes before ShouldProcess, so -WhatIf prints the
    # WARNING a real run prints. A read or a write that throws, or a .gitignore that is a symbolic
    # link (git does not read one), refuses each entry, and the run goes on.
    $isoGitDir = Join-Path ([System.IO.Path]::GetTempPath()) ('ouro-install-' + [guid]::NewGuid().ToString('N'))
    $probeTree = "$isoGitDir-tree"
    try {
        # Inside the try: git init can create the dir and still fail.
        git init -q --bare --template= $isoGitDir 2>$null
        if ($LASTEXITCODE -ne 0) { throw "git init --bare $isoGitDir failed (exit $LASTEXITCODE)" }
        $noExcludes = (Join-Path $isoGitDir 'no-excludes').Replace('\', '/')
        $ignoreCase = if ((git -C $Into config --bool core.ignoreCase 2>$null) -eq 'true') { 'true' } else { 'false' }
        [void][System.IO.Directory]::CreateDirectory((Join-Path $probeTree '.claude' 'worktrees' 'ouro-probe'))
        foreach ($rel in '.gitignore', '.claude/.gitignore', '.claude/worktrees/.gitignore') {
            try {
                $item = Get-Item -Force -LiteralPath (Join-Path $Into $rel) -ErrorAction Stop
                if (-not $item.PSIsContainer -and $item.LinkType -ne 'SymbolicLink') { [System.IO.File]::Copy($item.FullName, (Join-Path $probeTree $rel)) }
            }
            catch { }
        }
        foreach ($entry in '.claude/worktrees/', '.claude/ouro.local.toml') {
            $asked = if ($entry.EndsWith('/')) { "${entry}ouro-probe" } else { $entry }
            $out = @("$asked$([char]0)" | git --git-dir=$isoGitDir --work-tree=$probeTree -c "core.excludesFile=$noExcludes" -c "core.ignoreCase=$ignoreCase" check-ignore -v -z --no-index --stdin 2>$null)
            $code = $LASTEXITCODE
            $fields = ($out -join '').Split([char]0)
            $source = ''
            for ($i = 0; $i + 3 -lt $fields.Count; $i += 4) {
                if ($fields[$i + 3] -ceq $asked -and -not $fields[$i + 2].StartsWith('!')) { $source = $fields[$i] }
            }
            if ($code -eq 0 -and $source) { Write-Host "skip: $entry is already ignored ($source)"; continue }
            if ($code -notin 0, 1) { throw "git check-ignore --no-index $entry failed (exit $code)" }
            # No Test-Path gate: it answers False for a dangling link on Windows, which let the append
            # fall through and write the link's target into being, outside the tree.
            if (Test-SymbolicLink $ignorePath) {
                Write-Host "REFUSED: $entry not appended to $ignoreRel - it is a symbolic link, which git does not read" -ForegroundColor Yellow
                continue
            }
            try { $existing = if (Test-Path -LiteralPath $ignorePath) { $Utf8.GetString([System.IO.File]::ReadAllBytes($ignorePath)) } else { '' } }
            catch {
                Write-Host "REFUSED: $entry not appended to $ignoreRel - it cannot be read ($($_.Exception.GetBaseException().Message))" -ForegroundColor Yellow
                continue
            }
            $text = if ($existing.StartsWith([string][char]0xFEFF, [System.StringComparison]::Ordinal)) { $existing.Substring(1) } else { $existing }
            [string[]]$lines = foreach ($line in $text.Split([char]10)) {
                if ($line.EndsWith([string][char]13, [System.StringComparison]::Ordinal)) { $line = $line.Substring(0, $line.Length - 1) }
                $nul = $line.IndexOf([char]0)
                if ($nul -ge 0) { $line = $line.Substring(0, $nul) }
                $line.TrimEnd([char]32)
            }
            if ([Array]::IndexOf($lines, $entry) -ge 0) {
                Write-Host "WARNING: $entry is in $ignoreRel but git does not ignore it - a negation after it or in a deeper .gitignore un-ignores it; remove that negation, the run does not append the entry again" -ForegroundColor Yellow
                continue
            }
            # Opened for writing and closed at once, which writes nothing. The open comes before
            # ShouldProcess, so -WhatIf prints the REFUSED line a real run prints.
            if (Test-Path -LiteralPath $ignorePath) {
                try { [System.IO.FileStream]::new($ignorePath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite).Dispose() }
                catch {
                    Write-Host "REFUSED: $entry not appended to $ignoreRel - it cannot be written ($($_.Exception.GetBaseException().Message))" -ForegroundColor Yellow
                    continue
                }
            }
            if ($PSCmdlet.ShouldProcess($ignorePath, "Append $entry")) {
                $nl = if ($existing.Contains("`r`n")) { "`r`n" } else { "`n" }
                $lead = if (-not $existing -or $existing.EndsWith([string][char]10, [System.StringComparison]::Ordinal)) { '' }
                        elseif ($existing.EndsWith([string][char]13, [System.StringComparison]::Ordinal)) { [string][char]10 }
                        else { $nl }
                try { [System.IO.File]::AppendAllText($ignorePath, "$lead$entry$nl", $Utf8) }
                catch {
                    Write-Host "REFUSED: $entry not appended to $ignoreRel - it cannot be written ($($_.Exception.GetBaseException().Message))" -ForegroundColor Yellow
                    continue
                }
                Write-Host "wrote: $entry appended to $ignoreRel"
            }
        }
    }
    finally {
        if (Test-Path -LiteralPath $isoGitDir) {
            try { Remove-Item -LiteralPath $isoGitDir -Recurse -Force -WhatIf:$false -ErrorAction Stop }
            catch { Write-Warning "could not remove the temp git dir ${isoGitDir}: $($_.Exception.Message)" }
        }
        if (Test-Path -LiteralPath $probeTree) {
            try { Remove-Item -LiteralPath $probeTree -Recurse -Force -WhatIf:$false -ErrorAction Stop }
            catch { Write-Warning "could not remove the temp work tree ${probeTree}: $($_.Exception.Message)" }
        }
    }

    # ── 3. the docs-freshness workflow ───────────────────────────────────────────────────────────
    # An ignored binding is never committed, so no other clone and no CI job would read it: a
    # **/.claude/* rule does that to a new binding as surely as to the worktree directory.
    git -C $Into check-ignore -q --no-index $bindingRel 2>$null
    if ($LASTEXITCODE -eq 0) {
        $rule = (git -C $Into check-ignore -v --no-index $bindingRel 2>$null) -join ' '
        Write-Host "WARNING: $bindingRel is ignored ($rule) - un-ignore it or add it with git add -f, or no other clone and no CI job reads the binding" -ForegroundColor Yellow
    }

    if ($DocsFreshness) {
        $workflowRel = '.github/workflows/docs-freshness.yml'
        $workflowPath = Join-Path $Into $workflowRel
        $workflowLinkedDir = Get-LinkedAncestor $workflowRel
        if ($workflowLinkedDir) {
            Write-Host "REFUSED: $workflowRel not written - $workflowLinkedDir is a symbolic link, which git does not follow" -ForegroundColor Yellow
        }
        elseif (Test-SymbolicLink $workflowPath) {
            Write-Host "REFUSED: $workflowRel not written - it is a symbolic link, which git does not read" -ForegroundColor Yellow
        }
        elseif (Test-Path -LiteralPath $workflowPath) { Write-Host "skip: $workflowRel exists and is left as it is" }
        else {
            $why = @()
            $template = Join-Path $Plugin 'templates/docs-freshness.yml'
            if (-not (Test-Path -LiteralPath $template)) { $why += "templates/docs-freshness.yml not found in the plugin at $Plugin" }

            $workflowBranch = $branch
            if (-not $workflowBranch) {
                if (-not $bindingExisted) { $why += "the default branch is unknown: $viewWhy, and the repo had no binding before this run" }
                else {
                    $tool = Join-Path $PSScriptRoot 'ouro-binding.py'
                    # A binding an earlier run wrote while gh was failing is still the example's stub, and
                    # its branch is a placeholder like its slug. The example's own slug marks it.
                    $readSlug = Invoke-Tool 'python3' @($tool, 'get', 'repo.slug', $bindingPath)
                    $read = Invoke-Tool 'python3' @($tool, 'get', 'repo.default_branch', $bindingPath)
                    if ($exampleSlug -and $readSlug.Ok -and $readSlug.Out -ceq $exampleSlug) {
                        $why += "the default branch is unknown: $viewWhy, and $bindingRel still names the example's placeholder slug ($exampleSlug), so its branch is a placeholder too - fill repo.slug and repo.default_branch first"
                    }
                    elseif ($read.Ok -and $read.Out) { $workflowBranch = $read.Out }
                    else { $why += "the default branch is unknown: $viewWhy, and repo.default_branch could not be read from $bindingRel ($($read.Err))" }
                }
            }

            $manifest = Join-Path $Plugin '.claude-plugin/plugin.json'
            $version = $null
            try { $version = (Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json).version } catch { }
            if ($version -isnot [string] -or -not $version.Trim()) { $why += "the Action tag is unknown: no version could be read from $manifest" }

            if ($why) {
                foreach ($w in $why) { Write-Host "REFUSED: $workflowRel not written - $w" -ForegroundColor Yellow }
            }
            else {
                $tag = "v$($version.Trim())"
                # The owner segment of the uses: line is copied as the template has it.
                $yml = [System.IO.File]::ReadAllText($template).Replace('<your-default-branch>', $workflowBranch).Replace('<tag>', $tag)
                $workflowRefused = $false
                if ($PSCmdlet.ShouldProcess($workflowPath, 'Create the docs-freshness workflow')) {
                    try {
                        [System.IO.Directory]::CreateDirectory((Split-Path $workflowPath -Parent)) | Out-Null
                        [System.IO.File]::WriteAllText($workflowPath, $yml, $Utf8)
                        Write-Host "wrote: $workflowRel (branch $workflowBranch, Action tag $tag)"
                    }
                    catch {
                        $workflowRefused = $true
                        Write-Host "REFUSED: $workflowRel not written - the write failed ($($_.Exception.GetBaseException().Message))" -ForegroundColor Yellow
                    }
                }
                if (-not $workflowRefused) { Write-Host "  the Action is pinned at ${tag}: a repo that also runs the weekly pass pins OURO_REF to the same release" }
            }
        }
    }

    # ── 4. the CI workflow ───────────────────────────────────────────────────────────────────────
    if ($CI) {
        $workflowRel = '.github/workflows/ci.yml'
        $workflowPath = Join-Path $Into $workflowRel
        $workflowLinkedDir = Get-LinkedAncestor $workflowRel
        if ($workflowLinkedDir) {
            Write-Host "REFUSED: $workflowRel not written - $workflowLinkedDir is a symbolic link, which git does not follow" -ForegroundColor Yellow
        }
        elseif (Test-SymbolicLink $workflowPath) {
            Write-Host "REFUSED: $workflowRel not written - it is a symbolic link, which git does not read" -ForegroundColor Yellow
        }
        elseif (Test-Path -LiteralPath $workflowPath) { Write-Host "skip: $workflowRel exists and is left as it is" }
        else {
            $why = @()
            $template = Join-Path $Plugin 'templates/ci.yml'
            if (-not (Test-Path -LiteralPath $template)) { $why += "templates/ci.yml not found in the plugin at $Plugin" }

            $workflowBranch = $branch
            if (-not $workflowBranch) {
                if (-not $bindingExisted) { $why += "the default branch is unknown: $viewWhy, and the repo had no binding before this run" }
                else {
                    $tool = Join-Path $PSScriptRoot 'ouro-binding.py'
                    # A binding an earlier run wrote while gh was failing is still the example's stub, and
                    # its branch is a placeholder like its slug. The example's own slug marks it.
                    $readSlug = Invoke-Tool 'python3' @($tool, 'get', 'repo.slug', $bindingPath)
                    $read = Invoke-Tool 'python3' @($tool, 'get', 'repo.default_branch', $bindingPath)
                    if ($exampleSlug -and $readSlug.Ok -and $readSlug.Out -ceq $exampleSlug) {
                        $why += "the default branch is unknown: $viewWhy, and $bindingRel still names the example's placeholder slug ($exampleSlug), so its branch is a placeholder too - fill repo.slug and repo.default_branch first"
                    }
                    elseif ($read.Ok -and $read.Out) { $workflowBranch = $read.Out }
                    else { $why += "the default branch is unknown: $viewWhy, and repo.default_branch could not be read from $bindingRel ($($read.Err))" }
                }
            }

            $manifest = Join-Path $Plugin '.claude-plugin/plugin.json'
            $version = $null
            try { $version = (Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json).version } catch { }
            if ($version -isnot [string] -or -not $version.Trim()) { $why += "the Action tag is unknown: no version could be read from $manifest" }

            if ($why) {
                foreach ($w in $why) { Write-Host "REFUSED: $workflowRel not written - $w" -ForegroundColor Yellow }
            }
            else {
                $tag = "v$($version.Trim())"
                # The owner segment of the uses: line is copied as the template has it.
                $yml = [System.IO.File]::ReadAllText($template).Replace('<your-default-branch>', $workflowBranch).Replace('<tag>', $tag)
                $workflowRefused = $false
                if ($PSCmdlet.ShouldProcess($workflowPath, 'Create the CI workflow')) {
                    try {
                        [System.IO.Directory]::CreateDirectory((Split-Path $workflowPath -Parent)) | Out-Null
                        [System.IO.File]::WriteAllText($workflowPath, $yml, $Utf8)
                        Write-Host "wrote: $workflowRel (branch $workflowBranch, Action tag $tag)"
                    }
                    catch {
                        $workflowRefused = $true
                        Write-Host "REFUSED: $workflowRel not written - the write failed ($($_.Exception.GetBaseException().Message))" -ForegroundColor Yellow
                    }
                }
                if (-not $workflowRefused) { Write-Host "  the Action is pinned at ${tag}: a repo that also runs the docs-freshness workflow or the weekly pass keeps every pin at the same release" }
            }
        }
    }

    # ── 5. the issue intake files ────────────────────────────────────────────────────────────────
    # Each is written as the template's bytes: a text round trip may change them, and File.Copy carries
    # the plugin file's attributes. A template missing from the plugin, or one that cannot be read, is
    # refused before ShouldProcess, so -WhatIf prints the REFUSED line a real run prints and no
    # directory is created for it.
    if ($IssueIntake) {
        foreach ($intake in @(
                @{ Rel = '.github/ISSUE_TEMPLATE/work-item.yml'; Template = 'templates/work-item.yml'; Action = 'Create the issue form'; Owned = $true }
                @{ Rel = '.github/ISSUE_TEMPLATE/config.yml'; Template = 'templates/issue-template-config.yml'; Action = 'Create the template chooser config'; Owned = $false }
                @{ Rel = '.github/workflows/issue-intake-label.yml'; Template = 'templates/issue-intake-label.yml'; Action = 'Create the issue intake workflow'; Owned = $true })) {
            $intakePath = Join-Path $Into $intake.Rel
            $intakeLinkedDir = Get-LinkedAncestor $intake.Rel
            if ($intakeLinkedDir) {
                Write-Host "REFUSED: $($intake.Rel) not written - $intakeLinkedDir is a symbolic link, which git does not follow" -ForegroundColor Yellow
                continue
            }
            if (Test-SymbolicLink $intakePath) {
                Write-Host "REFUSED: $($intake.Rel) not written - it is a symbolic link, which git does not read" -ForegroundColor Yellow
                continue
            }
            $intakeTemplate = Join-Path $Plugin $intake.Template
            if (Test-Path -LiteralPath $intakePath) {
                # Never overwritten, but a copy of an ouro-owned file that differs from the template may
                # predate a change to it (the state list the intake workflow greps, say), so the line says
                # so. The chooser config is the repo's own once it exists: never named. Carriage returns
                # are not a difference: a Windows checkout may hold the copy as CRLF.
                $differs = $false
                if ($intake.Owned) { try {
                    $key = { param($p) [Convert]::ToBase64String([byte[]]@([System.IO.File]::ReadAllBytes($p) | Where-Object { $_ -ne 13 })) }
                    $differs = (Test-Path -LiteralPath $intakeTemplate -PathType Leaf) -and
                        ((& $key $intakePath) -cne (& $key $intakeTemplate))
                }
                catch { } }
                if ($differs) {
                    Write-Host "skip: $($intake.Rel) exists and differs from the plugin's $($intake.Template), left as it is - delete it and re-run with -IssueIntake to take the current one"
                }
                else { Write-Host "skip: $($intake.Rel) exists and is left as it is" }
                continue
            }
            if (-not (Test-Path -LiteralPath $intakeTemplate -PathType Leaf)) {
                Write-Host "REFUSED: $($intake.Rel) not written - $($intake.Template) not found in the plugin at $Plugin" -ForegroundColor Yellow
                continue
            }
            try { $intakeBytes = [System.IO.File]::ReadAllBytes($intakeTemplate) }
            catch {
                Write-Host "REFUSED: $($intake.Rel) not written - $($intake.Template) cannot be read ($($_.Exception.GetBaseException().Message))" -ForegroundColor Yellow
                continue
            }
            if ($PSCmdlet.ShouldProcess($intakePath, $intake.Action)) {
                try {
                    [System.IO.Directory]::CreateDirectory((Split-Path $intakePath -Parent)) | Out-Null
                    [System.IO.File]::WriteAllBytes($intakePath, $intakeBytes)
                    Write-Host "wrote: $($intake.Rel)"
                }
                catch {
                    Write-Host "REFUSED: $($intake.Rel) not written - the write failed ($($_.Exception.GetBaseException().Message))" -ForegroundColor Yellow
                }
            }
        }
    }

    # ── 6. the labels ────────────────────────────────────────────────────────────────────────────
    # On the repository the binding names, not on a fresh gh answer: a binding that was here before
    # this run is ouro's own record of the repository the loop works on, and it need not be the one
    # origin resolves to today. A binding this run wrote already names the slug gh resolved.
    $labelSlug = $slug
    $labelWhy = "the repo slug did not resolve ($viewWhy); rerun once gh can resolve origin's repository"
    if ($bindingExisted) {
        $readSlug = Invoke-Tool 'python3' @((Join-Path $PSScriptRoot 'ouro-binding.py'), 'get', 'repo.slug', $bindingPath)
        $labelSlug = if ($readSlug.Ok) { $readSlug.Out } else { '' }
        $labelWhy = if ($readSlug.Ok) { "$bindingRel names an empty repo.slug; fill it and rerun" }
        else { "repo.slug could not be read from $bindingRel ($($readSlug.Err)); fill it and rerun" }
        # Every reason to skip rather than call, each of which would otherwise end the run nonzero:
        # New-OuroLabels validates its -Repo, and gh answers a repository nobody can write with a 404.
        # The example's placeholder names no repository, and an example this run cannot read tells no
        # placeholder from a slug, so it skips instead of creating the labels somewhere.
        if ($labelSlug -ceq $exampleSlug) {
            $labelSlug = ''
            $labelWhy = "$bindingRel still names the example's placeholder slug ($exampleSlug); fill repo.slug and rerun"
        }
        elseif ($labelSlug -and $labelSlug -notmatch '^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$') {
            $labelWhy = "$bindingRel names a repo.slug that is not an owner and a name in letters, digits, dot, dash or underscore ($labelSlug); fix it and rerun"
            $labelSlug = ''
        }
    }
    if (-not $labelSlug) {
        Write-Host "skip: labels - $labelWhy"
    }
    else {
        $auth = Invoke-Tool 'gh' @('auth', 'status')
        if (-not $auth.Ok) { Write-Host "skip: labels - gh auth status failed, so gh cannot create them on $labelSlug; rerun after gh auth login" }
        else { & (Join-Path $PSScriptRoot 'New-OuroLabels.ps1') -Repo $labelSlug -BindingPath $bindingPath }
    }
    exit 0
}
finally {
    foreach ($name in $gitVars.Keys) {
        if ($null -ne $gitVars[$name]) { [Environment]::SetEnvironmentVariable($name, $gitVars[$name]) }
    }
}
