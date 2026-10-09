<#
.SYNOPSIS
    Test that the weekly pass's two model sessions, the drift audit and the intake grading, stay
    uncredentialed and tool-bound, that the steps which apply the intake manifest, post the drift
    audit's files and append the drift claim markers run no model, check the tree first and follow
    the session whose writes they cover, and that the job names its repository.
.DESCRIPTION
    The property this pins is a security boundary, and it lives in prose nowhere else: each
    session reads text the contract calls untrusted -- the grading session newly-filed issue
    bodies, the drift session the ledger's comments and issue text -- so it must hold no token and
    no tool that reaches the tracker. Every part of that is one edit away from being undone -- a
    `gh` grant added back for convenience, a `git log` added back for history, the job token
    restored on the step, a checkout left persisting its credential -- and each of those edits
    looks entirely ordinary in a diff. Nothing else in tests/ reads templates/weekly-pass.yml.

    The template is parsed as TEXT, by step block: no YAML parser ships with pwsh, and the
    Action-path suite reads its file the same way. A step block runs from its `- name:`/`- uses:`
    line to the next one at the same indent. The two intake steps are found by what they RUN --
    the grading step is the one invoking the intake skill, the applying step the one invoking the
    applier, the posting step the one invoking the drift outbox -- never by their names, so renaming
    a step does not quietly stop testing it.

    Every case runs Get-IntakeStepFindings over the real template and over MUTANTS built from it
    by one substitution each, so the negative cases cannot drift from the file they mutate: the
    real text must produce no finding, and each mutant must produce its own. A mutant that did
    not change the text is itself a failure -- otherwise a drifted anchor would leave a case
    silently asserting nothing.

    A vendored tree (bin/Vendor-Ouro.ps1) copies the scripts flat and, of templates/, only the
    documentation rule, so there the suite prints a skip line and exits 0.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Two levels up is the plugin root (suite under tests/<area>/) or, once vendored, the scripts dir.
$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Template = Join-Path $Base 'templates/weekly-pass.yml'
if (-not (Test-Path -LiteralPath $Template)) {
    if (Test-Path -LiteralPath (Join-Path $Base 'Test-DocsFreshness.ps1')) {
        Write-Host 'skip: no templates/ beside the flat gate scripts (vendored layout); the weekly pass ships with the plugin' -ForegroundColor DarkGray
        exit 0
    }
    throw "templates/weekly-pass.yml not found under $Base"
}

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -eq $Actual) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}

# The read-only git subcommands the grading session is granted. `git grep` and `git log` are
# deliberately absent: measured, the first runs a command (--open-files-in-pager) and the second
# writes a file (--output=), and this suite exists so neither comes back unnoticed. Widening this
# list is a deliberate edit here as well as in the template -- which is the point of the pairing.
$ReadOnlyGit = @('rev-parse')
# The whole grant, order-insensitive. A second copy of a value is usually a liability; here it is
# the assertion: the grant is the boundary, so growing it by one entry must turn a suite red
# rather than merely read as a longer line in a diff.
$ExpectedGrant = @('Skill', 'Read', 'Grep', 'Glob', 'Agent', 'Task', 'Edit(TestResults/intake-manifest/**)', 'Bash(git rev-parse:*)')
# The drift session's git reads: the ones that run no program and write no file. `git grep`, `git log`,
# `git diff` and `git show` are absent for the grading step's reason, and the history step 3 of the
# skill reads comes from the targets file.
$DriftReadOnlyGit = @('ls-files', 'cat-file', 'rev-parse')

# Every step that runs a model, recognised by what it RUNS rather than by its name. This list is
# the reason the rest of the suite is worth anything: each check below examines a step it was
# anchored on, so a step NOT in this list -- one added later, carrying the job token and a gh
# grant, reading the same untrusted targets file -- would otherwise be a session nothing here
# looks at. A legitimate fourth model step is added HERE, with the posture it is allowed, in the
# same edit that adds it to the template.
$ModelSteps = @(
    @{ id = 'the claude auth probe'; runs = 'reply with exactly' }
    @{ id = 'the drift audit'; runs = '/ouro:drift' }
    @{ id = 'the intake grading session'; runs = '/ouro:intake' }
)
# The drift audit reads the ledger's comments and issue text, which anyone with comment rights can
# write, so its posture is the grading step's: no token, no gh tool, no git command that runs a
# program or writes a file, and Edit spelled with its output directory. Enumerated for the same
# reason -- widening it is a deliberate edit here too, rather than a longer line in a diff.
$ExpectedDriftGrant = @('Skill', 'Read', 'Grep', 'Glob', 'Agent', 'Task', 'Edit(TestResults/drift-outbox/**)',
    'Bash(git ls-files:*)', 'Bash(git cat-file:*)', 'Bash(git rev-parse:*)')

# The job token's whole permission set, sorted: widening it is a deliberate edit here as well as in
# the template. Each entry is read from a step's GitHub API use: both checkouts fetch, most steps
# read issues and write comments and labels, loop metrics and outcomes read pull requests, and
# loop outcomes reads the Actions run list.
$ExpectedPermissions = @('actions: read', 'contents: read', 'issues: write', 'pull-requests: read')

function Get-StepBlocks([string]$Text) {
    # Each step starts at `      - ` under `steps:`; a block runs to the next step or the end.
    $lines = $Text -split "`r?`n"
    $starts = @(0..($lines.Count - 1) | Where-Object { $lines[$_] -match '^      - ' })
    $blocks = @()
    for ($i = 0; $i -lt $starts.Count; $i++) {
        $from = $starts[$i]
        $to = if ($i + 1 -lt $starts.Count) { $starts[$i + 1] - 1 } else { $lines.Count - 1 }
        $blocks += , ($lines[$from..$to] -join "`n")
    }
    return $blocks
}

function Get-JobBlocks([string]$Text) {
    # Same shape as the step split, one level up: each job starts at `  <id>:` under `jobs:`.
    # Only below that line -- `on:` and `concurrency:` have two-space children of their own.
    $lines = $Text -split "`r?`n"
    $jobsAt = @(0..($lines.Count - 1) | Where-Object { $lines[$_] -match '^jobs:\s*$' })
    if ($jobsAt.Count -ne 1) { return @() }
    $after = @($lines[($jobsAt[0] + 1)..($lines.Count - 1)])
    $starts = @(0..($after.Count - 1) | Where-Object { $after[$_] -match '^  [A-Za-z0-9_-]+:\s*$' })
    $blocks = @()
    for ($i = 0; $i -lt $starts.Count; $i++) {
        $from = $starts[$i]
        $to = if ($i + 1 -lt $starts.Count) { $starts[$i + 1] - 1 } else { $after.Count - 1 }
        $blocks += , ($after[$from..$to] -join "`n")
    }
    return $blocks
}

# Comment lines are dropped before a block is scanned for credentials: this template explains its
# own token posture in prose, and the word `token` in a comment must not stand in for a grant.
function Remove-Comments([string]$Block) {
    return (($Block -split "`r?`n") | Where-Object { $_ -notmatch '^\s*#' }) -join "`n"
}

# The tree check a step opens with, pinned as code: one `$dirty =` line reading both the repo root
# and the plugin checkout (untracked files included), a throw on it, and both before the command
# the step runs. $Run is the command line's regex; $Name says which step in each finding.
function Get-TreeCheckFindings([string]$Code, [string]$Run, [string]$RunText, [string]$Name, [string]$Session = 'grading') {
    $f = @()
    $runAt = [regex]::Match($Code, '(?m)^\s*' + $Run + '\s*$')
    if (-not $runAt.Success) { $f += "the $Name step does not run $RunText as a command of its own" }
    $rootHalf = [regex]::Match($Code, '(?m)^\s*\$dirty\s*=\s*@\(git status --porcelain --untracked-files=no\)')
    $pluginHalf = [regex]::Match($Code, '(?m)^\s*\$dirty\s*=.*@\(git -C ouro status --porcelain --untracked-files=all\)\s*$')
    $oneLine = [regex]::Match($Code, '(?m)^\s*\$dirty\s*=\s*@\(git status --porcelain --untracked-files=no\)\s*\+\s*@\(git -C ouro status --porcelain --untracked-files=all\)\s*$')
    $throwAt = [regex]::Match($Code, '(?m)^\s*if\s*\(\$dirty\)\s*\{\s*throw\s')
    if (-not $rootHalf.Success -and -not $pluginHalf.Success) {
        $f += "the $Name step runs a plugin script out of a tree it never checked, though the $Session session before it holds Edit"
        return $f
    }
    if (-not $rootHalf.Success) { $f += "the $Name step's tree check does not read the repo root" }
    if (-not $pluginHalf.Success) { $f += "the $Name step's tree check does not read the plugin checkout, untracked files included" }
    if ($rootHalf.Success -and $pluginHalf.Success -and -not $oneLine.Success) {
        $f += "the $Name step's tree check does not read both halves into one `$dirty line, so a later assignment overwrites the first"
    }
    if (-not $throwAt.Success) { $f += "the $Name step reads the tree but does not throw on a change" }
    if ($runAt.Success) {
        $later = @($rootHalf, $pluginHalf, $throwAt) | Where-Object { $_.Success -and $_.Index -gt $runAt.Index }
        if ($later) { $f += "the $Name step checks the tree only after the plugin script has run" }
    }
    return $f
}

# The --allowedTools entries a step grants, or $null when it passes none.
function Get-Grant([string]$Code) {
    if ($Code -match '--allowedTools\s+"([^"]*)"') {
        return @($Matches[1] -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    }
    return $null
}

# What a session that reads untrusted text may be granted: no gh tool, no unrestricted Bash, only
# $ReadOnly git subcommands, and exactly the $Expected list. $Name says which session in each finding.
function Get-GrantFindings([string]$Code, [string]$Name, $Expected, $ReadOnly) {
    $f = @()
    $granted = Get-Grant $Code
    if ($null -eq $granted) { return @("the $Name passes no --allowedTools, so it runs with the harness default") }
    foreach ($g in $granted) {
        if ($g -match '(?i)\bgh\b' -or $g -match '(?i)^Bash\(\s*gh[\s)(]') {
            $f += "the $Name grants a gh tool: $g"
        }
        elseif ($g -match '^Bash\((.+)\)$') {
            $cmd = $Matches[1].Trim()
            if ($cmd -match '^git\s+([a-z][a-z-]*)') {
                if ($ReadOnly -notcontains $Matches[1]) {
                    $f += "the $Name grants a git tool outside the read-only set: $g"
                }
            }
            else { $f += "the $Name grants a Bash command that is not a git read: $g" }
        }
        elseif ($g -eq 'Bash') { $f += "the $Name grants unrestricted Bash" }
    }
    $missing = @($Expected | Where-Object { $granted -notcontains $_ })
    $extra = @($granted | Where-Object { $Expected -notcontains $_ })
    if ($missing -or $extra) {
        $f += "the $Name's grant is not the enumerated read-only set (missing: $($missing -join ' ') / extra: $($extra -join ' '))"
    }
    return $f
}

# A step-level env of the same name is how a job-level token is cleared for one step; an empty
# value is the clearing. Anything else assigned to a *TOKEN* name, and any secrets. reference,
# is a credential handed to the session that reads untrusted text.
# Only the two explicit empty-string spellings count as cleared, deliberately: a bare
# `GH_TOKEN:`, a `~` and a `null` are read as NOT cleared and turn this red. Whether Actions
# hands a null env value to the step as an empty string or leaves the job's value standing was
# not measured, so the check fails closed -- a false red on an odd spelling is cheap, a green
# on a step that still holds the token is not.
function Get-CredentialFindings([string]$Code, [string]$Name) {
    $f = @()
    if ($Code -notmatch "(?m)^\s*GH_TOKEN:\s*(''|"""")\s*$") {
        $f += "the $Name does not clear GH_TOKEN, so it inherits the job token"
    }
    foreach ($line in ($Code -split "`r?`n")) {
        if ($line -match '(?i)^\s*([A-Za-z_][A-Za-z0-9_]*TOKEN[A-Za-z0-9_]*)\s*:\s*(.+)$') {
            $tokenName, $value = $Matches[1], $Matches[2].Trim()
            if ($value -ne "''" -and $value -ne '""') { $f += "the $Name carries a credential: $tokenName is set to $value" }
        }
        if ($line -match 'secrets\.') { $f += "the $Name reads a secret: $($line.Trim())" }
    }
    return $f
}

# What confines a session's reads: the plugin it loads is the workspace's own checkout, and the
# settings file that denies a read outside the workspace is written outside the workspace.
function Get-ConfinementFindings([string]$Code, [string]$Name) {
    $f = @()
    if ($Code -notmatch '--plugin-dir\s+ouro(\s|`|$)') { $f += "the $Name does not load the plugin from the in-tree ouro checkout (--plugin-dir ouro)" }
    if ($Code -notmatch '--settings\s+\$confine(\s|`|$)') { $f += "the $Name passes no read-confining --settings" }
    if ($Code -notmatch '\$confine\s*=\s*Join-Path\s+\$env:RUNNER_TEMP\s') { $f += "the $Name writes its read-confining settings file somewhere other than RUNNER_TEMP" }
    if ($Code -notmatch 'Set-Content\s+-LiteralPath\s+\$confine\s+-Value\s+''\{"permissions":\{"blockReadsOutsideWorkingDirectories":true\}\}''') {
        $f += "the $Name's settings file does not hold permissions.blockReadsOutsideWorkingDirectories: true"
    }
    return $f
}

function Get-StepName([string]$Block) {
    if ($Block -match '(?m)^\s*-\s*name:\s*(.+)$') { return $Matches[1].Trim() }
    if ($Block -match '(?m)^\s*-\s*uses:\s*(.+)$') { return $Matches[1].Trim() }
    return '(unnamed step)'
}

function Get-IntakeStepFindings([string]$Text) {
    $findings = [System.Collections.Generic.List[string]]::new()
    $blocks = Get-StepBlocks $Text

    # --- the census of model steps ------------------------------------------------------------
    # Pinned before anything else, because every other check is anchored on a step it already
    # knows about: the set of steps that run a model must be exactly the declared set, and each
    # must be one of them. A fourth is a finding on its own, named, whatever its posture.
    # The trigger is an INVOCATION: the CLI's name in command position, followed by a flag. Not the
    # flag's spelling -- `-p` has a documented long form (`--print`), and keying on the short one
    # would let a fourth model step in under the other. And not the bare word either: two
    # deterministic steps name `.claude/ouro.toml` in a throw message, and matching `\bclaude\b`
    # read both of them as model sessions, which turned this suite red on the correct template.
    # The lookbehind drops a path segment (`.claude/`, `ouro/.claude`) and a hyphenated name; the
    # required flag drops prose such as `::error::claude CLI auth is broken`. A backtick is allowed
    # between the two, because that is how a pwsh `run:` block continues a line.
    $ModelInvocation = '(?<![.\w/])claude[\s`]+-'
    $modelBlocks = @($blocks | Where-Object { (Remove-Comments $_) -match $ModelInvocation })
    foreach ($b in $modelBlocks) {
        if (-not @($ModelSteps | Where-Object { (Remove-Comments $b) -match [regex]::Escape($_.runs) })) {
            $findings.Add("an undeclared step runs a model: $(Get-StepName $b) -- every model step is declared in this suite with the posture it is allowed, so one that is not is a session nothing here checks")
        }
    }
    foreach ($m in $ModelSteps) {
        $hits = @($modelBlocks | Where-Object { (Remove-Comments $_) -match [regex]::Escape($m.runs) })
        if ($hits.Count -ne 1) { $findings.Add("expected exactly one step for $($m.id), found $($hits.Count)") }
    }
    if ($modelBlocks.Count -ne $ModelSteps.Count) {
        $findings.Add("the weekly pass runs $($modelBlocks.Count) model step(s) and this suite declares $($ModelSteps.Count)")
    }

    # The two work sessions run at the binding's [models].weekly, read in their own step before the
    # claude line; the auth probe keeps --model haiku. The read is found by its assignment:
    # the step's own throw message names the same command.
    foreach ($runs in '/ouro:drift', '/ouro:intake') {
        $blk = @($modelBlocks | Where-Object { (Remove-Comments $_) -match [regex]::Escape($runs) })
        if ($blk.Count -ne 1) { continue }
        $t = Remove-Comments $blk[0]
        # The whole read: an absent key falls back to sonnet, any other failure throws. Without the
        # fallback an absent key would hand gh's error text to --model.
        $readBlock = [regex]::Match($t, '\$model = python3 ouro/bin/ouro-binding\.py get models\.weekly 2>&1\s*' +
            'if \(\$LASTEXITCODE -ne 0\) \{\s*if \("\$model" -notmatch ''no such key''\) \{ throw [^\r\n]*\}\s*' +
            '\$model = ''sonnet''\s*\}\s*\$model = "\$model"\.Trim\(\)')
        $read = if ($readBlock.Success) { $readBlock.Index } else { -1 }; $use = $t.IndexOf('--model $model')
        if ($read -lt 0 -or $use -lt 0 -or $read -gt $use) {
            $findings.Add("the $runs session does not run at the binding's [models].weekly, read in its step before the claude line")
        }
    }

    # The auth probe checks that auth works and does no work of its own, so it is granted nothing.
    $probe = @($modelBlocks | Where-Object { (Remove-Comments $_) -match 'reply with exactly' })
    if ($probe.Count -eq 1 -and $null -ne (Get-Grant (Remove-Comments $probe[0]))) {
        $findings.Add('the claude auth probe grants tools, though it only checks that the CLI can answer')
    }
    # The drift audit's posture is the grading step's, and stated rather than assumed: it holds no
    # token, no gh tool and no git command that runs a program or writes a file, and its output is
    # the files it writes into the directory the step after it posts from.
    $drift = @($modelBlocks | Where-Object { (Remove-Comments $_) -match '/ouro:drift' })
    if ($drift.Count -eq 1) {
        $dc = Remove-Comments $drift[0]
        foreach ($x in (Get-GrantFindings $dc 'drift audit step' $ExpectedDriftGrant $DriftReadOnlyGit)) { $findings.Add($x) }
        foreach ($x in (Get-CredentialFindings $dc 'drift audit step')) { $findings.Add($x) }
        foreach ($x in (Get-ConfinementFindings $dc 'drift audit step')) { $findings.Add($x) }
        # The prompt is double-quoted, so the step's own shell expands nothing the session needs to
        # read, and it names the two paths the harvest and the poster share with the session.
        if ($dc -cmatch '"/ouro:drift [^"]*--issue\b') {
            $findings.Add("the drift audit's prompt carries --issue, though the session posts nothing and holds no gh tool to use a number")
        }
        if ($dc -cnotmatch '"/ouro:drift [^"]*--ledger TestResults\\audit-ledger\.txt[ "]') {
            $findings.Add("the drift audit's prompt is handed no --ledger TestResults\audit-ledger.txt, so the session has no recorded false positives to read")
        }
        if ($dc -cnotmatch '"/ouro:drift [^"]*--outbox TestResults\\drift-outbox[ "]') {
            $findings.Add("the drift audit's prompt is handed no --outbox TestResults\drift-outbox, so the session has nowhere to write its output")
        }
        if ($dc -notmatch '(?m)^\s*New-Item\s+-ItemType\s+Directory\s+TestResults\\drift-outbox\s*\|\s*Out-Null\s*$') {
            $findings.Add('the drift audit step does not create its output directory TestResults\drift-outbox without -Force, so a directory left by an earlier run would be posted as this run''s')
        }
    }

    # --- the job env ----------------------------------------------------------------------------
    # The steps that shell out to gh name the binding's repository themselves (Get-RepoSlug.ps1
    # runs in the same process, before each of them), so what this env backstops is a gh call that
    # omits -R, which this file cannot spell for a step that does not load the resolver. Read from
    # the pass's OWN job, found by what it runs: a second job carrying the line satisfies nothing.
    # A trailing comment is ordinary YAML -- the persist-credentials check below allows one too.
    # Quotes around the value are accepted here. The env block runs to the job's next four-space
    # KEY, or to the end of the block: a comment line sits at any indent, and a job may spell its
    # env after steps, and neither is a job that failed to name a repository.
    # Checked before the returns below.
    $passJobs = @(Get-JobBlocks $Text | Where-Object { $_ -match '/ouro:intake' })
    if ($passJobs.Count -ne 1) {
        $findings.Add("expected exactly one job running the weekly pass, found $($passJobs.Count)")
    }
    else {
        $jobEnv = if ($passJobs[0] -match '(?ms)^    env:\s*?$(.*?)(?=^    [A-Za-z0-9_-]+:|\z)') { $Matches[1] } else { '' }
        if ($jobEnv -notmatch '(?m)^      GH_REPO:\s*["'']?\$\{\{\s*github\.repository\s*\}\}["'']?\s*(#.*)?$') {
            $findings.Add('the job running the weekly pass does not set GH_REPO to ${{ github.repository }} in its own env, so a gh call that omits -R reads and writes whatever repository gh picks from the clone')
        }
        # The job's token permissions, pinned exactly. With no block the token gets whatever the
        # repository's default is, and a write added to the block is a wider token for every step.
        # The compare is the whole block, order-insensitive, so one entry more or less is a finding.
        $permBlock = if ($passJobs[0] -match '(?ms)^    permissions:\s*?$(.*?)(?=^    [A-Za-z0-9_-]+:|\z)') { $Matches[1] } else { $null }
        if ($null -eq $permBlock) {
            $findings.Add('the job running the weekly pass names no permissions block, so its token gets the repository''s default')
        }
        else {
            $granted = @((Remove-Comments $permBlock) -split "`r?`n" | ForEach-Object { if ($_ -match '^      ([A-Za-z-]+):\s*([a-z]+)\s*(#.*)?$') { "$($Matches[1]): $($Matches[2])" } elseif ($_.Trim()) { "unparsed: $($_.Trim())" } } | Sort-Object)
            if (($granted -join ', ') -cne ($ExpectedPermissions -join ', ')) {
                $findings.Add("the job's permissions block is '$($granted -join ', ')', not '$($ExpectedPermissions -join ', ')'")
            }
        }
    }
    # A step may repeat the job value; one that DISAGREES is a second answer to a question the
    # job already answered.
    foreach ($b in $blocks) {
        foreach ($line in ((Remove-Comments $b) -split "`r?`n")) {
            if ($line -match '^\s+GH_REPO:\s*(.+?)\s*(#.*)?$') {
                $value = $Matches[1].Trim() -replace '^["'']|["'']$', ''
                if ($value -ne '${{ github.repository }}') {
                    $findings.Add("$(Get-StepName $b) sets GH_REPO to $value, which is not the job's `${{ github.repository }}")
                }
            }
        }
    }

    $grading = @($blocks | Where-Object { $_ -match '/ouro:intake' })
    if ($grading.Count -ne 1) { $findings.Add("expected exactly one step running the intake skill, found $($grading.Count)"); return $findings }
    $grade = $grading[0]
    $gradeCode = Remove-Comments $grade
    # Checked before the applying step is looked for, not after: a merged step that both grades and
    # applies satisfies "a step runs the applier" while being the exact arrangement this splits.
    if ($gradeCode -match 'apply-manifest\.py') { $findings.Add('the grading step applies its own manifest, so the session that reads untrusted text also writes to the tracker') }
    $applying = @($blocks | Where-Object { $_ -match 'apply-manifest\.py' -and $_ -ne $grade })
    if ($applying.Count -ne 1) { $findings.Add("expected exactly one step running the manifest applier, found $($applying.Count)"); return $findings }
    $applyCode = Remove-Comments $applying[0]

    # --- the grading step's tool grant and credentials ----------------------------------------
    foreach ($x in (Get-GrantFindings $gradeCode 'grading step' $ExpectedGrant $ReadOnlyGit)) { $findings.Add($x) }
    foreach ($x in (Get-CredentialFindings $gradeCode 'grading step')) { $findings.Add($x) }
    foreach ($x in (Get-ConfinementFindings $gradeCode 'grading step')) { $findings.Add($x) }

    # --- the handover, both directions --------------------------------------------------------
    if ($gradeCode -notmatch '--targets\s') { $findings.Add('the grading step is handed no --targets file, so the session would select its own workload') }
    if ($gradeCode -notmatch '--manifest\s') { $findings.Add('the grading step is handed no --manifest directory to write its proposal into') }

    # --- the applying step --------------------------------------------------------------------
    if ($applyCode -match '--unattended=') {
        $findings.Add('the applying step spells --unattended with a value, which the applier refuses by name')
    }
    elseif ($applyCode -notmatch '--unattended(\s|$)') {
        $findings.Add('the applying step does not pass --unattended, so the manifest is applied unbounded')
    }
    # The applier bounds where a step may post by the intake's own targets file, the one the
    # grading step was handed, outside its Edit grant and hash-checked below; it refuses an
    # unattended run without it.
    $gradeTargets = [regex]::Match($gradeCode, '--targets\s+(\S+)').Groups[1].Value
    $applyTargets = [regex]::Match($applyCode, '--targets\s+(\S+)').Groups[1].Value
    if (-not $applyTargets) {
        $findings.Add('the applying step passes no --targets, so the applier refuses every unattended manifest')
    }
    elseif ($applyTargets -ne $gradeTargets) {
        $findings.Add("the applying step bounds the manifest by '$applyTargets', not the '$gradeTargets' the grading step was handed")
    }
    # The grant is a property of one CLI version and the tree check does not see the gitignored
    # TestResults, so the selecting step records the file's hash and the applying step compares it
    # before the applier runs.
    $selecting = @($blocks | Where-Object { $_ -match 'Get-IntakeTargets\.ps1' })
    if ($selecting.Count -ne 1 -or (Remove-Comments $selecting[0]) -notmatch 'INTAKE_TARGETS_SHA256=[^\r\n]*Get-FileHash[^\r\n]*intake-targets\.json(?![\w.])[^\r\n]*GITHUB_ENV') {
        $findings.Add('the selecting step does not write the targets file''s SHA-256 to GITHUB_ENV as INTAKE_TARGETS_SHA256')
    }
    $applyAt = $applyCode.IndexOf('apply-manifest.py')
    $compare = [regex]::Match($applyCode, 'if\s*\(\s*\$(\w+)\s+-cne\s+\$env:INTAKE_TARGETS_SHA256\s*\)\s*\{\s*throw')
    if ($applyCode -notmatch 'INTAKE_TARGETS_SHA256' -or $applyCode -notmatch 'Get-FileHash[^\r\n]*intake-targets\.json(?![\w.])') {
        $findings.Add('the applying step never compares the targets file''s SHA-256 with the recorded one, so a rewrite of the file is not seen')
    }
    elseif (-not $compare.Success -or $compare.Index -gt $applyAt -or $applyCode.Substring(0, $compare.Index) -notmatch ('\$' + $compare.Groups[1].Value + '\s*=[^\r\n]*Get-FileHash[^\r\n]*intake-targets\.json(?![\w.])')) {
        $findings.Add('the applying step compares the targets file''s SHA-256 after it calls the applier, or without a throw on a mismatch')
    }
    elseif ($applyCode -match '\$env:INTAKE_TARGETS_SHA256\s*=') {
        $findings.Add('the applying step assigns INTAKE_TARGETS_SHA256, so the recorded hash it compares is its own')
    }
    # The week's targets are selected against the binding's slug, and the applier refuses a
    # manifest naming an issue it does not create with no --repo, so a step that drops it grades
    # a week and applies none of it.
    if ($applyCode -notmatch '--repo(\s|$)') {
        $findings.Add('the applying step passes no --repo, so a graded week ends in the applier''s refusal with nothing applied')
    }
    # A CI runner holds no private token list, and the applier refuses a manifest it cannot scan.
    if ($applyCode -notmatch '--no-forbidden-check(\s|$)') {
        $findings.Add('the applying step passes no --no-forbidden-check, so a runner without the private token list refuses every manifest')
    }
    if ($applyCode -match $ModelInvocation) { $findings.Add('a model runs in the applying step, which is the step that holds the token') }
    # The grading session runs with Edit, which was measured to confine on 2.1.283 (2.1.274's
    # Write did not); a grant is a property of one CLI version, so the applying step still checks
    # that the manifest was the only thing that session wrote before it runs a script out of the
    # same tree with the token in hand.
    if ($applyCode -notmatch 'git\s+status\s+--porcelain') {
        $findings.Add('the applying step runs the applier out of a tree it never checked, though the grading session before it holds Edit')
    }
    # That check cannot see a new file under --untracked-files=no, and the pythons the step then
    # runs put their own directory first on sys.path, so a module planted beside them executes in
    # the step that holds the token. The plugin-checkout half is widened to every untracked file,
    # which the plugin's own ignore file keeps clean; the repo-root half stays as it is, since in a
    # consumer the plugin checkout is itself untracked there. PYTHONSAFEPATH on the step's env, not
    # -P on a command, covers both invocations and anything either spawns; PYTHONNOUSERSITE keeps
    # the runner's user site, where a usercustomize module runs at startup, off sys.path as well.
    # Either switch spelling of --untracked-files is the same git; the flag order is the shipped one.
    # The stop names what the widened half reports: a file that appeared, not only one that changed.
    if ($applyCode -match 'throw\s+"a tracked file changed') {
        $findings.Add('the applying step''s stop says a tracked file changed, where its check of the plugin checkout fires on a file that appeared')
    }
    if ($applyCode -notmatch 'git\s+-C\s+ouro\s+status\s+--porcelain\s+(--untracked-files=all|-uall)(\s|\))') {
        $findings.Add('the applying step''s check of the plugin checkout does not report an untracked file, so a module planted beside the applier passes it')
    }
    if ($applyCode -match 'git\s+status\s+--porcelain\s+(--untracked-files=all|-uall)') {
        $findings.Add('the applying step''s check of the repo root reports untracked files, which in a consumer include the plugin checkout itself')
    }
    # The env keys are read from the step's own env block, wherever it sits among the step's keys:
    # the lines under `env:` at the step's key indent, up to its next key. A line of the run block
    # that happens to spell one is not an env entry, and a run header of any chomping is not a
    # boundary this reading needs.
    $applyEnv = if ($applying[0] -match '(?ms)^        env:[ \t]*(#.*?)?$(.*?)(?=^        [A-Za-z0-9_-]+:|\z)') { $Matches[2] } else { '' }
    if ($applyEnv -notmatch '(?m)^\s+PYTHONSAFEPATH:\s*["'']?1["'']?\s*(#.*)?$') {
        $findings.Add('the applying step does not set PYTHONSAFEPATH to 1 in its env, so a module planted beside the binding tool or the applier shadows the stdlib in the step that holds the token')
    }
    if ($applyEnv -notmatch '(?m)^\s+PYTHONNOUSERSITE:\s*["'']?1["'']?\s*(#.*)?$') {
        $findings.Add('the applying step does not set PYTHONNOUSERSITE to 1 in its env, so a usercustomize module in the runner''s user site runs at each python''s startup in the step that holds the token')
    }

    # --- the steps after it that run a plugin script -------------------------------------------
    # Loop metrics and Append usage both run a script out of the same checkouts, after the applying
    # step, with !cancelled()/always() so an ordinary failure there (a rate limit, a transient gh
    # error) does not skip them. That must not also cover the one failure that matters: a tracked
    # bin script the tree check caught as modified. Each repeats the same check at its own top.
    $metrics = @($blocks | Where-Object { $_ -match 'Get-LoopMetrics\.ps1' })
    if ($metrics.Count -ne 1) { $findings.Add("expected exactly one step running the loop metrics, found $($metrics.Count)") }
    else {
        foreach ($x in (Get-TreeCheckFindings (Remove-Comments $metrics[0]) 'pwsh\s+-NoProfile\s+-File\s+ouro/bin/Get-LoopMetrics\.ps1\s+-Comment' 'pwsh -NoProfile -File ouro/bin/Get-LoopMetrics.ps1 -Comment' 'loop metrics')) { $findings.Add($x) }
    }
    # The loop outcomes step is the same shape: a python script out of the same checkouts with the
    # job token in hand, so it opens with the tree check and carries both env keys. It is found by
    # what it runs, in code only: the comment above it sits at the tail of the previous block.
    $outcomes = @($blocks | Where-Object { (Remove-Comments $_) -match 'loop-outcomes\.py' })
    if ($outcomes.Count -ne 1) { $findings.Add("expected exactly one step running the loop outcomes, found $($outcomes.Count)") }
    else {
        $oc = Remove-Comments $outcomes[0]
        foreach ($x in (Get-TreeCheckFindings $oc 'python3\s+ouro/bin/loop-outcomes\.py\s+--comment' 'python3 ouro/bin/loop-outcomes.py --comment' 'loop outcomes')) { $findings.Add($x) }
        $outcomesEnv = if ($outcomes[0] -match '(?ms)^        env:[ \t]*(#.*?)?$(.*?)(?=^        [A-Za-z0-9_-]+:|\z)') { $Matches[2] } else { '' }
        if ($outcomesEnv -notmatch '(?m)^\s+PYTHONSAFEPATH:\s*["'']?1["'']?\s*(#.*)?$') {
            $findings.Add('the loop outcomes step does not set PYTHONSAFEPATH to 1 in its env, so a module planted beside the script shadows the stdlib in the step that holds the token')
        }
        if ($outcomesEnv -notmatch '(?m)^\s+PYTHONNOUSERSITE:\s*["'']?1["'']?\s*(#.*)?$') {
            $findings.Add('the loop outcomes step does not set PYTHONNOUSERSITE to 1 in its env, so a usercustomize module in the runner''s user site runs at python''s startup in the step that holds the token')
        }
    }
    $usage = @($blocks | Where-Object { $_ -match 'gh issue comment \$n --body' })
    if ($usage.Count -ne 1) { $findings.Add("expected exactly one step appending usage to the rolling issue, found $($usage.Count)") }
    elseif ((Remove-Comments $usage[0]) -notmatch 'git\s+status\s+--porcelain') {
        $findings.Add('the append-usage step runs a plugin script out of a tree it never checked, though the grading session before it holds Edit')
    }
    elseif ((Remove-Comments $usage[0]).IndexOf('git status --porcelain') -gt (Remove-Comments $usage[0]).IndexOf('. ouro/bin/Get-RollingIssue.ps1')) {
        $findings.Add('the append-usage step checks the tree only after it loads a plugin script')
    }

    # --- the step that posts the drift audit's files ----------------------------------------------
    # The drift session writes the ledger body and the comments as files; this step posts them, so
    # it holds the token and runs a plugin script and runs no model. It opens with the tree check,
    # runs the poster before any gh call and before it resolves the ledger, and names the issue the
    # harvest exported and no other: the number is never read from a file the session wrote. It is
    # found by what it runs.
    $posting = @($blocks | Where-Object { (Remove-Comments $_) -match 'drift-claims\.py\s+outbox' })
    if ($posting.Count -ne 1) { $findings.Add("expected exactly one step running the drift outbox, found $($posting.Count)") }
    else {
        $pc = Remove-Comments $posting[0]
        if ($pc -match $ModelInvocation) { $findings.Add('a model runs in the posting step, which is the step that holds the token') }
        foreach ($x in (Get-TreeCheckFindings $pc '\$files\s*=\s*@\(python3\s+ouro/bin/drift-claims\.py\s+outbox\s+--dir\s+TestResults/drift-outbox\s+--targets\s+TestResults/drift-targets\.json\)' '$files = @(python3 ouro/bin/drift-claims.py outbox --dir TestResults/drift-outbox --targets TestResults/drift-targets.json)' 'posting' 'drift')) { $findings.Add($x) }
        $treeAt = $pc.IndexOf('git status --porcelain')
        $outboxAt = $pc.IndexOf('drift-claims.py outbox')
        $loadAt = $pc.IndexOf('. ouro/bin/Get-RollingIssue.ps1')
        $resolveAt = $pc.IndexOf('Get-RollingIssueNumber')
        $firstGh = [regex]::Match($pc, '\bgh\s+issue\s').Index
        if ($treeAt -ge 0 -and $loadAt -ge 0 -and $treeAt -gt $loadAt) { $findings.Add('the posting step checks the tree only after it loads a plugin script') }
        if ($outboxAt -lt 0 -or $resolveAt -lt 0 -or $outboxAt -gt $resolveAt -or $outboxAt -gt $firstGh) {
            $findings.Add('the posting step makes a gh call before the poster has accepted the directory')
        }
        if ($resolveAt -lt 0 -or $resolveAt -gt $firstGh) {
            $findings.Add('the posting step makes a gh call before it resolves the rolling issue, so the call names whatever repository gh picks')
        }
        if ($pc -notmatch '(?m)^\s*if\s*\(\s*"\$num"\s+-ne\s+\$env:DRIFT_ISSUE_NUMBER\s*\)\s*\{\s*throw\s') {
            $findings.Add('the posting step does not stop when the ledger resolves to another number than the harvest''s')
        }
        if ($pc -notmatch 'git\s+-C\s+ouro\s+status\s+--porcelain\s+--untracked-files=all') {
            $findings.Add('the posting step''s check of the plugin checkout does not report an untracked file, so a module planted beside the script passes it')
        }
        if ($pc -notmatch '(?m)^\s*if\s*\(\$LASTEXITCODE\s+-ne\s+0\)\s*\{\s*throw\s[^\r\n]*outbox[^\r\n]*\}') {
            $findings.Add('the posting step does not stop on a refusal of the poster, so a refused directory is followed by gh calls')
        }
        if ($pc -notmatch 'gh\s+issue\s+edit\s+\$env:DRIFT_ISSUE_NUMBER\s+--body-file\s') { $findings.Add('the posting step does not rewrite the ledger body of $env:DRIFT_ISSUE_NUMBER with gh issue edit --body-file') }
        if ($pc -notmatch 'gh\s+issue\s+comment\s+\$env:DRIFT_ISSUE_NUMBER\s+--body-file\s') { $findings.Add('the posting step does not post each comment to $env:DRIFT_ISSUE_NUMBER with gh issue comment --body-file') }
        foreach ($call in [regex]::Matches($pc, '\bgh\s+issue\s+(\S+)\s+(\S+)')) {
            $verb, $target = $call.Groups[1].Value, $call.Groups[2].Value
            if ($verb -cne 'edit' -and $verb -cne 'comment') { $findings.Add("the posting step runs gh issue $verb, beyond editing the ledger body and commenting on it") }
            if ($target -cne '$env:DRIFT_ISSUE_NUMBER') { $findings.Add("the posting step runs gh issue $verb on $target, not on `$env:DRIFT_ISSUE_NUMBER") }
        }
        if ($pc -match '(?m)^\s*gh\s+(?!issue\s)') { $findings.Add('the posting step runs a gh command other than gh issue edit and gh issue comment') }
        if ($pc -match 'Get-Content|ConvertFrom-Json|ReadAllText|Import-') {
            $findings.Add('the posting step reads the content of a file, so a value the session wrote could reach a gh call')
        }
        $posterEnv = if ($posting[0] -match '(?ms)^        env:[ \t]*(#.*?)?$(.*?)(?=^        [A-Za-z0-9_-]+:|\z)') { $Matches[2] } else { '' }
        if ($posterEnv -notmatch '(?m)^\s+PYTHONSAFEPATH:\s*["'']?1["'']?\s*(#.*)?$') {
            $findings.Add('the posting step does not set PYTHONSAFEPATH to 1 in its env, so a module planted beside the poster shadows the stdlib in the step that holds the token')
        }
        if ($posterEnv -notmatch '(?m)^\s+PYTHONNOUSERSITE:\s*["'']?1["'']?\s*(#.*)?$') {
            $findings.Add('the posting step does not set PYTHONNOUSERSITE to 1 in its env, so a usercustomize module in the runner''s user site runs at python''s startup in the step that holds the token')
        }
        $driftAt = @(0..($blocks.Count - 1) | Where-Object { (Remove-Comments $blocks[$_]) -match '/ouro:drift' })
        $postAt = @(0..($blocks.Count - 1) | Where-Object { $blocks[$_] -eq $posting[0] })
        $claimAt = @(0..($blocks.Count - 1) | Where-Object { (Remove-Comments $blocks[$_]) -match 'drift-claims\.py\s+ingest' })
        if ($driftAt.Count -ne 1 -or $postAt[0] -lt $driftAt[0]) { $findings.Add('the posting step does not come after the drift session') }
        if ($claimAt.Count -ne 1 -or $postAt[0] -gt $claimAt[0]) { $findings.Add('the posting step does not come before the claims step') }
    }

    # --- the step that appends the claim markers -------------------------------------------------
    # The drift session writes `claim-records` blocks into comments, which are data it authored.
    # The step that hashes them and posts the `audit-claims` markers holds the job token and runs
    # a plugin script, so it runs no model, opens with the tree check, and follows the session
    # whose writes that check covers. It is found by what it runs.
    $claims = @($blocks | Where-Object { $_ -match 'drift-claims\.py\s+ingest' })
    if ($claims.Count -ne 1) { $findings.Add("expected exactly one step running the claims ingest, found $($claims.Count)") }
    else {
        $cc = Remove-Comments $claims[0]
        if ($cc -match $ModelInvocation) { $findings.Add('a model runs in the claims step, which is the step that holds the token') }
        if ($cc -notmatch 'git\s+status\s+--porcelain') {
            $findings.Add('the claims step runs a plugin script out of a tree it never checked, though the drift session before it runs Bash')
        }
        elseif ($cc.IndexOf('git status --porcelain') -gt $cc.IndexOf('drift-claims.py')) {
            $findings.Add('the claims step checks the tree only after the plugin script has run')
        }
        if ($cc.IndexOf('git status --porcelain') -gt $cc.IndexOf('. ouro/bin/Get-RollingIssue.ps1')) {
            $findings.Add('the claims step checks the tree only after it loads a plugin script')
        }
        if ($cc -notmatch 'git\s+-C\s+ouro\s+status\s+--porcelain\s+--untracked-files=all') {
            $findings.Add('the claims step''s check of the plugin checkout does not report an untracked file, so a module planted beside the script passes it')
        }
        if ($cc -notmatch 'gh\s+issue\s+comment\s+\S+\s+--body-file\s') {
            $findings.Add('the claims step does not post each body with gh issue comment --body-file')
        }
        $resolveAt = $cc.IndexOf('Get-RollingIssueNumber')
        $firstGh = [regex]::Match($cc, '\bgh\s+issue\s').Index
        if ($resolveAt -lt 0 -or $resolveAt -gt $firstGh) {
            $findings.Add('the claims step makes a gh call before it resolves the rolling issue, so the call names whatever repository gh picks')
        }
        # The comments file ingest reads is written from the trusted-author filter's output and from
        # nothing else, and it exists before ingest runs.
        $filterAt = $cc.IndexOf('Get-TrustedComments')
        $ingestAt = $cc.IndexOf('drift-claims.py ingest')
        if ($filterAt -lt 0 -or $filterAt -gt $ingestAt) {
            $findings.Add('the claims step hands ingest comments no trusted-author filter has read, so a stranger''s audit-run marker chooses which docs'' records are kept')
        }
        elseif ($cc -notmatch '(?m)^\s*Set-Content\s+TestResults/drift-comments\.json\s+-Value\s+\$trusted\.Json\b') {
            $findings.Add('the claims step does not write the comments file ingest reads from the filter''s output')
        }
        if ($cc -match 'drift-comments\.json[^\r\n]*\|\s*Set-Content|gh\s+issue\s+view[^\r\n]*\|\s*Set-Content') {
            $findings.Add('the claims step writes the comments file straight from gh, past the trusted-author filter')
        }
        $driftAt = @(0..($blocks.Count - 1) | Where-Object { $blocks[$_] -match '/ouro:drift' })
        $claimsAt =@(0..($blocks.Count - 1) | Where-Object { $blocks[$_] -eq $claims[0] })
        if ($driftAt.Count -ne 1 -or $claimsAt[0] -lt $driftAt[0]) {
            $findings.Add('the claims step does not come after the drift session')
        }
    }

    # --- the step that harvests the audit ledger --------------------------------------------------
    # The selector reads the file this step writes, so every body in it is a body the filter kept.
    $harvestSteps = @($blocks | Where-Object { (Remove-Comments $_) -match 'audit-ledger\.txt[^\r\n]*-Encoding' -and (Remove-Comments $_) -match 'gh\s+issue\s+view' })
    if ($harvestSteps.Count -ne 1) { $findings.Add("expected exactly one step harvesting the audit ledger, found $($harvestSteps.Count)") }
    else {
        $hc = Remove-Comments $harvestSteps[0]
        $hFilterAt = $hc.IndexOf('Get-TrustedComments')
        $hWriteAt = [regex]::Match($hc, '(?m)^\s*Set-Content\s+TestResults\\audit-ledger\.txt\s+-Value\s+\$\(if \(\$ledger\.Kept\) \{ \$ledger\.Bodies \}').Index
        if ($hFilterAt -lt 0) { $findings.Add('the harvest step reads the ledger without the trusted-author filter, so a stranger''s audit-run marker removes the docs it names from the next run''s targets') }
        elseif ($hWriteAt -le $hFilterAt) { $findings.Add('the harvest step writes the ledger file from something other than the filter''s kept bodies') }
        if ($hc -match '--jq\s+''?\.comments\[\]\.body') { $findings.Add('the harvest step reads every comment body straight from gh, past the trusted-author filter') }
        if ($hc -notmatch '::warning::[^\r\n]*\$\(\$ledger\.Total\)') { $findings.Add('the harvest step does not warn when the ledger has comments and none is kept') }
        if ($hc -notmatch '(?m)^\s*if\s*\(\$ledger\.Total -gt 0 -and \$ledger\.Kept -eq 0\)') { $findings.Add('the harvest step''s warning does not fire on a ledger with comments and none kept') }
    }

    # --- the ouro version stamp -----------------------------------------------------------------
    # A pinned OURO_REF can fall many releases behind the newest one with nothing on the pass
    # recording which ref and commit the run actually checked out. The stamp step is picked
    # out by what it computes, not by its name: the plugin checkout's HEAD read into $sha (the step's
    # own error text names the same command, so the match is on the assignment), written in the form
    # ouro=<OURO_REF>@<sha>. The two report surfaces carry its value onward.
    $stamp = @($blocks | Where-Object {
        (Remove-Comments $_) -match '\$sha\s*=\s*git\s+-C\s+ouro\s+rev-parse\s+HEAD' -and
        (Remove-Comments $_) -match 'OURO_STAMP=ouro=\$\{\{\s*vars\.OURO_REF\s*\}\}@\$sha' })
    if ($stamp.Count -ne 1) { $findings.Add("expected exactly one step computing the ouro version stamp, found $($stamp.Count)") }
    $docsWriter = @($blocks | Where-Object { $_ -match 'DOCS_ISSUE_TITLE' -and $_ -match 'Get-RollingIssue\.ps1' })
    if ($docsWriter.Count -ne 1) { $findings.Add("expected exactly one step writing the docs-freshness report, found $($docsWriter.Count)") }
    elseif ((Remove-Comments $docsWriter[0]) -notmatch 'OURO_STAMP') {
        $findings.Add('the docs-freshness report does not carry the ouro version stamp')
    }
    if ($usage.Count -eq 1 -and (Remove-Comments $usage[0]) -notmatch 'OURO_STAMP') {
        $findings.Add('the append-usage comment does not carry the ouro version stamp')
    }

    # --- the artifact upload ------------------------------------------------------------------
    # A refused post leaves the directory it refused; the upload is how it is read afterwards.
    $upload = @($blocks | Where-Object { (Remove-Comments $_) -match 'uses:\s*actions/upload-artifact' })
    if ($upload.Count -ne 1 -or (Remove-Comments $upload[0]) -notmatch '(?m)^\s*TestResults/drift-outbox/\*\*\s*$') {
        $findings.Add('the artifact upload does not include the drift outbox, so a refused post cannot be read')
    }

    # --- both checkouts -----------------------------------------------------------------------
    # Either checkout leaving its token in .git/config hands the grading session, which has Read
    # over the whole tree, the credential the step above just cleared.
    $checkouts = @($blocks | Where-Object { $_ -match 'uses:\s*actions/checkout' })
    if ($checkouts.Count -lt 2) { $findings.Add("expected both checkouts (the repo and the plugin), found $($checkouts.Count)") }
    foreach ($c in $checkouts) {
        # A trailing comment on the line is ordinary YAML, and one of the two carries one.
        if ((Remove-Comments $c) -notmatch '(?m)^\s*persist-credentials:\s*false\s*(#.*)?$') {
            $which = if ($c -match '(?m)^\s*path:\s*(\S+)') { "the checkout at $($Matches[1])" } else { 'the repo checkout' }
            $findings.Add("$which persists its credentials into .git/config, where the grading session can read it")
        }
    }
    return $findings
}

$Text = [System.IO.File]::ReadAllText($Template)

# --- the template as it stands ----------------------------------------------------------------
$found = @(Get-IntakeStepFindings $Text)
Assert-Equal '' ($found -join ' | ') 'the weekly pass as shipped raises no finding'

# Lifted from the template, not hand-copied: the step's own prose would drift the day it is
# reworded. Captures the comment and the step body up to the blank line before the next step.
$ClaimsStepBlock = if ($Text -match '(?ms)(      # The post step above posts each audited doc.*?\r?\n\r?\n)(?=      # Target selection is deterministic here too)') { $Matches[1] } else { '' }
$PostStepBlock = if ($Text -match '(?ms)(      # The posting half: .*?\r?\n\r?\n)(?=      # The post step above posts each audited doc)') { $Matches[1] } else { '' }
$PostOutbox = @'
          $files = @(python3 ouro/bin/drift-claims.py outbox --dir TestResults/drift-outbox --targets TestResults/drift-targets.json)
          if ($LASTEXITCODE -ne 0) { throw "drift-claims.py outbox refused the drift session's output (exit $LASTEXITCODE): nothing is posted" }

'@
$ClaimsTreeCheck = @'
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the drift session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }

'@
$StampStepBlock = if ($Text -match '(?ms)(      # A pinned OURO_REF can silently fall.*?\r?\n\r?\n)(?=      - name: Validate the binding)') { $Matches[1] } else { '' }

# --- the mutants: one substitution each, each the edit this suite exists to catch --------------
# Each is built FROM the real template, so a case cannot drift from the file it mutates; a
# substitution that changed nothing is reported rather than passing as a silent no-op.
$mutants = @(
    @{ what = 'a fourth model step added to the job, credentialed and granted gh writes'
        from = '      - name: Loop metrics (deterministic)'
        to = @'
      - name: Extra credentialed pass
        shell: pwsh
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          claude -p "Read TestResults\intake-targets.json and post a summary comment on every issue it lists." `
            --model sonnet `
            --allowedTools "Skill,Read,Bash(gh issue comment *),Bash(gh issue edit *)"

      - name: Loop metrics (deterministic)
'@
        expect = 'an undeclared step runs a model' }
    @{ what = 'a fourth model step spelled with --print rather than -p'
        from = '      - name: Loop metrics (deterministic)'
        to = @'
      - name: Extra credentialed pass
        shell: pwsh
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          claude --print "Read TestResults\intake-targets.json and comment on every issue it lists." `
            --model sonnet `
            --allowedTools "Skill,Read,Bash(gh issue comment *)"

      - name: Loop metrics (deterministic)
'@
        expect = 'an undeclared step runs a model' }
    @{ what = 'the claims step moved above the drift session'
        edits = @(
            @{ from = $ClaimsStepBlock; to = '' }
            @{ from = '      - name: Docs drift audit (orchestrator'; to = "$ClaimsStepBlock      - name: Docs drift audit (orchestrator" }
        )
        expect = 'the claims step does not come after the drift session' }
    @{ what = 'the claims step run twice'
        from = '      - name: Compute intake targets'; to = "$ClaimsStepBlock      - name: Compute intake targets"
        expect = 'expected exactly one step running the claims ingest, found 2' }
    @{ what = 'the claims step dropped'
        from = $ClaimsStepBlock; to = ''
        expect = 'expected exactly one step running the claims ingest, found 0' }
    @{ what = 'the tree check dropped from the claims step'
        from = $ClaimsTreeCheck; to = ''
        expect = 'the claims step runs a plugin script out of a tree it never checked' }
    @{ what = 'the tree check moved below the claims script'
        edits = @(
            @{ from = $ClaimsTreeCheck; to = '' }
            @{ from = '          Write-Host "appended $($files.Count) claim comment(s)'; to = "$ClaimsTreeCheck          Write-Host `"appended `$(`$files.Count) claim comment(s)" }
        )
        expect = 'the claims step checks the tree only after the plugin script has run' }
    @{ what = 'the tree check moved below the resolver in the claims step'
        edits = @(
            @{ from = $ClaimsTreeCheck; to = '' }
            @{ from = '          $head = "$((Get-Content TestResults\drift-targets.json'; to = "$ClaimsTreeCheck          `$head = `"`$((Get-Content TestResults\drift-targets.json" }
        )
        expect = 'the claims step checks the tree only after it loads a plugin script' }
    @{ what = 'the plugin checkout reported without its untracked files in the claims step'
        from = "$ClaimsTreeCheck"
        to = $ClaimsTreeCheck.Replace('git -C ouro status --porcelain --untracked-files=all', 'git -C ouro status --porcelain --untracked-files=no')
        expect = 'the claims step''s check of the plugin checkout does not report an untracked file' }
    @{ what = 'a model run inside the claims step'
        from = '          Write-Host "appended $($files.Count) claim comment(s)'
        to = "          claude -p `"summarise the claims`" --model sonnet`n          Write-Host `"appended `$(`$files.Count) claim comment(s)"
        expect = 'a model runs in the claims step' }
    @{ what = 'the rolling issue left unresolved in the claims step'
        from = @'
          . ouro/bin/Get-RollingIssue.ps1
          $num = Get-RollingIssueNumber -Title $env:DRIFT_ISSUE_TITLE -Key rolling_issues.drift_audit
          if ("$num" -ne $env:DRIFT_ISSUE_NUMBER) { throw "the drift ledger resolves to #$num, not the harvest's #$env:DRIFT_ISSUE_NUMBER" }

'@
        to = ''
        expect = 'the claims step makes a gh call before it resolves the rolling issue' }
    @{ what = 'a body posted inline rather than from its file'
        from = "--body-file `$file`n            if (`$LASTEXITCODE -ne 0) { throw `"appending"; to = "--body `$file`n            if (`$LASTEXITCODE -ne 0) { throw `"appending"
        expect = 'the claims step does not post each body with gh issue comment --body-file' }
    @{ what = 'the job permissions block dropped'
        from = "    permissions:`n      contents: read`n      issues: write`n      pull-requests: read`n      actions: read`n"; to = ''
        expect = 'names no permissions block' }
    @{ what = 'contents widened to write in the job permissions'
        from = '      contents: read'; to = '      contents: write'
        expect = "the job's permissions block is" }
    @{ what = 'actions: read dropped from the job permissions'
        from = "      actions: read`n"; to = ''
        expect = "the job's permissions block is" }
    @{ what = 'a write-all permission added to the job'
        from = '      actions: read'; to = "      actions: read`n      packages: write"
        expect = "the job's permissions block is" }
    @{ what = 'the claims step reads the comments past the filter'
        from = '          $trusted = Get-TrustedComments -CommentsJson $raw'; to = '          $trusted = [pscustomobject]@{ Json = $raw; Kept = 0; Total = 0; Dropped = 0 }'
        expect = 'the claims step hands ingest comments no trusted-author filter has read' }
    @{ what = 'the claims step writes the comments file straight from gh'
        from = '          Set-Content TestResults/drift-comments.json -Value $trusted.Json -Encoding utf8'; to = '          Set-Content TestResults/drift-comments.json -Value $raw -Encoding utf8'
        expect = 'the claims step does not write the comments file ingest reads from the filter' }
    @{ what = 'the harvest reads every comment body without the filter'
        from = '            $ledger = Get-TrustedComments -CommentsJson $raw'; to = '            $ledger = [pscustomobject]@{ Bodies = @(); Kept = 0; Total = 0; Dropped = 0 }'
        expect = 'the harvest step reads the ledger without the trusted-author filter' }
    @{ what = 'the harvest ledger written from the unfiltered read'
        from = '-Value $(if ($ledger.Kept) { $ledger.Bodies } else { '''' })'; to = '-Value $raw'
        expect = 'the harvest step writes the ledger file from something other than the filter''s kept bodies' }
    @{ what = 'the harvest warning dropped'
        from = 'if ($ledger.Total -gt 0 -and $ledger.Kept -eq 0) {'; to = 'if ($false) {'
        expect = 'the harvest step''s warning does not fire' }
    @{ what = 'a pull-request write added to the drift audit grant'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(gh pr merge *)"'
        expect = 'the drift audit step grants a gh tool' }
    @{ what = 'the comment verb put back in the drift audit grant'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(gh issue comment *)"'
        expect = 'the drift audit step grants a gh tool' }
    @{ what = 'the edit verb put back in the drift audit grant'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(gh issue edit *)"'
        expect = 'the drift audit step grants a gh tool' }
    @{ what = 'the view verb put back in the drift audit grant'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(gh issue view *)"'
        expect = 'the drift audit step grants a gh tool' }
    @{ what = 'the create verb put back in the drift audit grant'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(gh issue create *)"'
        expect = 'the drift audit step grants a gh tool' }
    @{ what = 'the reopen verb put back in the drift audit grant'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(gh issue reopen *)"'
        expect = 'the drift audit step grants a gh tool' }
    @{ what = 'git grep (--open-files-in-pager) put back in the drift audit grant'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(git grep:*)"'
        expect = 'the drift audit step grants a git tool outside the read-only set' }
    @{ what = 'git blame (--contents <path> reads any file) put back in the drift audit grant'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(git blame:*)"'
        expect = 'the drift audit step grants a git tool outside the read-only set' }
    @{ what = 'git blame put back with a scoped spelling in the drift audit grant'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(git blame --contents:*)"'
        expect = 'the drift audit step grants a git tool outside the read-only set' }
    @{ what = 'git log (--output=) put back in the drift audit grant'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(git log:*)"'
        expect = 'the drift audit step grants a git tool outside the read-only set' }
    @{ what = 'git diff (--output=) put back in the drift audit grant'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(git diff:*)"'
        expect = 'the drift audit step grants a git tool outside the read-only set' }
    @{ what = 'git show (--output=) put back in the drift audit grant'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(git show:*)"'
        expect = 'the drift audit step grants a git tool outside the read-only set' }
    @{ what = 'git config (a writer of the global config) added to the drift audit grant'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(git config:*)"'
        expect = 'the drift audit step grants a git tool outside the read-only set' }
    @{ what = 'a Bash command that is no git read added to the drift audit grant'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(python3:*)"'
        expect = 'the drift audit step grants a Bash command that is not a git read' }
    @{ what = 'unrestricted Bash granted to the drift audit step'
        from = ',Bash(git rev-parse:*)"'; to = ',Bash"'
        expect = 'the drift audit step grants unrestricted Bash' }
    @{ what = 'the drift audit Edit grant widened to the whole results tree'
        from = 'Edit(TestResults/drift-outbox/**)'; to = 'Edit(TestResults/**)'
        expect = 'the drift audit step''s grant is not the enumerated read-only set' }
    @{ what = 'the drift audit Edit grant dropped, which would leave the session unable to write its output'
        from = ',Edit(TestResults/drift-outbox/**)'; to = ''
        expect = 'the drift audit step''s grant is not the enumerated read-only set' }
    @{ what = 'a git read dropped from the drift audit grant'
        from = ',Bash(git rev-parse:*)"'; to = '"'
        expect = 'the drift audit step''s grant is not the enumerated read-only set' }
    @{ what = 'the job token restored on the drift audit step'
        from = "          GH_TOKEN: ''`n        run: |`n          if (`$env:DRIFT_ISSUE_NUMBER -notmatch"
        to = "          GH_TOKEN: `${{ github.token }}`n        run: |`n          if (`$env:DRIFT_ISSUE_NUMBER -notmatch"
        expect = 'the drift audit step does not clear GH_TOKEN' }
    @{ what = 'the drift audit step''s token clearing dropped from its env'
        from = "          GH_TOKEN: ''`n        run: |`n          if (`$env:DRIFT_ISSUE_NUMBER -notmatch"
        to = "          FORCE_COLOR: '0'`n        run: |`n          if (`$env:DRIFT_ISSUE_NUMBER -notmatch"
        expect = 'the drift audit step does not clear GH_TOKEN' }
    @{ what = 'a second credential handed to the drift audit step'
        from = "          GH_TOKEN: ''`n        run: |`n          if (`$env:DRIFT_ISSUE_NUMBER -notmatch"
        to = "          GH_TOKEN: ''`n          OURO_READ_TOKEN: `${{ secrets.OURO_READ_TOKEN }}`n        run: |`n          if (`$env:DRIFT_ISSUE_NUMBER -notmatch"
        expect = 'the drift audit step carries a credential' }
    @{ what = 'the ledger number put back in the drift audit prompt'
        from = 'drift-targets.json --ledger'; to = 'drift-targets.json --issue $env:DRIFT_ISSUE_NUMBER --ledger'
        expect = 'the drift audit''s prompt carries --issue' }
    @{ what = 'the ledger file dropped from the drift audit prompt'
        from = ' --ledger TestResults\audit-ledger.txt'; to = ''
        expect = 'the drift audit''s prompt is handed no --ledger' }
    @{ what = 'the ledger file near miss in the drift audit prompt (another file name)'
        from = '--ledger TestResults\audit-ledger.txt'; to = '--ledger TestResults\audit-ledger.txt.bak'
        expect = 'the drift audit''s prompt is handed no --ledger' }
    @{ what = 'the output directory dropped from the drift audit prompt'
        from = ' --outbox TestResults\drift-outbox'; to = ''
        expect = 'the drift audit''s prompt is handed no --outbox' }
    @{ what = 'the output directory near miss in the drift audit prompt (a sibling directory)'
        from = '--outbox TestResults\drift-outbox'; to = '--outbox TestResults\drift-outbox-x'
        expect = 'the drift audit''s prompt is handed no --outbox' }
    @{ what = 'the drift audit step made to create its output directory with -Force'
        from = 'New-Item -ItemType Directory TestResults\drift-outbox'; to = 'New-Item -ItemType Directory -Force TestResults\drift-outbox'
        expect = 'the drift audit step does not create its output directory' }
    @{ what = 'the drift audit step no longer creating its output directory'
        from = "          New-Item -ItemType Directory TestResults\drift-outbox | Out-Null`n"; to = ''
        expect = 'the drift audit step does not create its output directory' }
    @{ what = 'the drift outbox dropped from the artifact upload'
        from = "            TestResults/drift-outbox/**`n"; to = ''
        expect = 'the artifact upload does not include the drift outbox' }
    @{ what = 'the posting step dropped'
        from = $PostStepBlock; to = ''
        expect = 'expected exactly one step running the drift outbox, found 0' }
    @{ what = 'the posting step run twice'
        from = '      - name: Compute intake targets'; to = "$PostStepBlock      - name: Compute intake targets"
        expect = 'expected exactly one step running the drift outbox, found 2' }
    @{ what = 'the posting step moved above the drift session'
        edits = @(
            @{ from = $PostStepBlock; to = '' }
            @{ from = '      # The drift audit is two steps'; to = "$PostStepBlock      # The drift audit is two steps" }
        )
        expect = 'the posting step does not come after the drift session' }
    @{ what = 'the posting step moved below the claims step'
        edits = @(
            @{ from = $PostStepBlock; to = '' }
            @{ from = '      # Target selection is deterministic here too'; to = "$PostStepBlock      # Target selection is deterministic here too" }
        )
        expect = 'the posting step does not come before the claims step' }
    @{ what = 'the tree check dropped from the posting step'
        from = "$ClaimsTreeCheck$PostOutbox"; to = $PostOutbox
        expect = 'the posting step runs a plugin script out of a tree it never checked' }
    @{ what = 'the tree check moved below the poster in the posting step'
        from = "$ClaimsTreeCheck$PostOutbox"; to = "$PostOutbox$ClaimsTreeCheck"
        expect = 'the posting step checks the tree only after the plugin script has run' }
    @{ what = 'the tree check moved below the resolver load in the posting step'
        edits = @(
            @{ from = "$ClaimsTreeCheck$PostOutbox"; to = $PostOutbox }
            @{ from = '          $num = Get-RollingIssueNumber -Title $env:DRIFT_ISSUE_TITLE -Key rolling_issues.drift_audit
          if ("$num" -ne $env:DRIFT_ISSUE_NUMBER) { throw "the drift ledger resolves to #$num, not the harvest''s #$env:DRIFT_ISSUE_NUMBER" }
          gh issue edit'; to = "$ClaimsTreeCheck          `$num = Get-RollingIssueNumber -Title `$env:DRIFT_ISSUE_TITLE -Key rolling_issues.drift_audit`n          if (`"`$num`" -ne `$env:DRIFT_ISSUE_NUMBER) { throw `"the drift ledger resolves to #`$num, not the harvest's #`$env:DRIFT_ISSUE_NUMBER`" }`n          gh issue edit" }
        )
        expect = 'the posting step checks the tree only after it loads a plugin script' }
    @{ what = 'the plugin checkout reported without its untracked files in the posting step'
        from = "$ClaimsTreeCheck$PostOutbox"
        to = "$($ClaimsTreeCheck.Replace('git -C ouro status --porcelain --untracked-files=all', 'git -C ouro status --porcelain --untracked-files=no'))$PostOutbox"
        expect = 'the posting step''s check of the plugin checkout does not report an untracked file' }
    @{ what = 'the poster moved below the gh calls in the posting step'
        edits = @(
            @{ from = $PostOutbox; to = '' }
            @{ from = '          Write-Host "posted the body and'; to = "$PostOutbox          Write-Host `"posted the body and" }
        )
        expect = 'the posting step makes a gh call before the poster has accepted the directory' }
    @{ what = 'the poster moved below the resolver in the posting step'
        edits = @(
            @{ from = $PostOutbox; to = '' }
            @{ from = '          gh issue edit $env:DRIFT_ISSUE_NUMBER --body-file $files[0]'; to = "$PostOutbox          gh issue edit `$env:DRIFT_ISSUE_NUMBER --body-file `$files[0]" }
        )
        expect = 'the posting step makes a gh call before the poster has accepted the directory' }
    @{ what = 'the poster''s refusal not stopping the posting step'
        from = '          if ($LASTEXITCODE -ne 0) { throw "drift-claims.py outbox refused the drift session''s output (exit $LASTEXITCODE): nothing is posted" }
'; to = ''
        expect = 'the posting step does not stop on a refusal of the poster' }
    @{ what = 'the rolling issue left unresolved in the posting step'
        from = '          . ouro/bin/Get-RollingIssue.ps1
          $num = Get-RollingIssueNumber -Title $env:DRIFT_ISSUE_TITLE -Key rolling_issues.drift_audit
          if ("$num" -ne $env:DRIFT_ISSUE_NUMBER) { throw "the drift ledger resolves to #$num, not the harvest''s #$env:DRIFT_ISSUE_NUMBER" }
          gh issue edit'; to = '          gh issue edit'
        expect = 'the posting step makes a gh call before it resolves the rolling issue' }
    @{ what = 'the ledger-number mismatch check dropped from the posting step'
        from = '          if ("$num" -ne $env:DRIFT_ISSUE_NUMBER) { throw "the drift ledger resolves to #$num, not the harvest''s #$env:DRIFT_ISSUE_NUMBER" }
          gh issue edit'; to = '          gh issue edit'
        expect = 'the posting step does not stop when the ledger resolves to another number' }
    @{ what = 'the body edited on a number the resolver returned, not the harvest''s'
        from = 'gh issue edit $env:DRIFT_ISSUE_NUMBER --body-file $files[0]'; to = 'gh issue edit $num --body-file $files[0]'
        expect = 'the posting step runs gh issue edit on $num, not on $env:DRIFT_ISSUE_NUMBER' }
    @{ what = 'the comments posted to an issue number read from a file the session wrote'
        from = 'gh issue comment $env:DRIFT_ISSUE_NUMBER --body-file $file
            if ($LASTEXITCODE -ne 0) { throw "posting'
        to = 'gh issue comment (Get-Content TestResults\drift-outbox\issue.txt) --body-file $file
            if ($LASTEXITCODE -ne 0) { throw "posting'
        expect = 'the posting step reads the content of a file' }
    @{ what = 'a number from a file the session wrote used in a comment call'
        from = 'gh issue comment $env:DRIFT_ISSUE_NUMBER --body-file $file
            if ($LASTEXITCODE -ne 0) { throw "posting'
        to = 'gh issue comment $(Get-Content TestResults\drift-outbox\issue.txt) --body-file $file
            if ($LASTEXITCODE -ne 0) { throw "posting'
        expect = 'the posting step runs gh issue comment on' }
    @{ what = 'a close added to the posting step'
        from = '          Write-Host "posted the body and'; to = "          gh issue close `$env:DRIFT_ISSUE_NUMBER`n          Write-Host `"posted the body and"
        expect = 'the posting step runs gh issue close' }
    @{ what = 'a label edit on another issue added to the posting step'
        from = '          Write-Host "posted the body and'; to = "          gh issue edit 7 --add-label agent-ready`n          Write-Host `"posted the body and"
        expect = 'the posting step runs gh issue edit on 7' }
    @{ what = 'a gh api call added to the posting step'
        from = '          Write-Host "posted the body and'; to = "          gh api repos/o/r/issues/1 -X PATCH`n          Write-Host `"posted the body and"
        expect = 'the posting step runs a gh command other than' }
    @{ what = 'the ledger body edited inline rather than from its file'
        from = 'gh issue edit $env:DRIFT_ISSUE_NUMBER --body-file $files[0]'; to = 'gh issue edit $env:DRIFT_ISSUE_NUMBER --body $files[0]'
        expect = 'the posting step does not rewrite the ledger body' }
    @{ what = 'the ledger body edit dropped from the posting step'
        from = '          gh issue edit $env:DRIFT_ISSUE_NUMBER --body-file $files[0]
          if ($LASTEXITCODE -ne 0) { throw "rewriting the body of #$env:DRIFT_ISSUE_NUMBER failed (exit $LASTEXITCODE)" }
'; to = ''
        expect = 'the posting step does not rewrite the ledger body' }
    @{ what = 'the comments posted inline rather than from their files'
        from = "--body-file `$file`n            if (`$LASTEXITCODE -ne 0) { throw `"posting"; to = "--body `$file`n            if (`$LASTEXITCODE -ne 0) { throw `"posting"
        expect = 'the posting step does not post each comment' }
    @{ what = 'a model run added to the posting step'
        from = '          Write-Host "posted the body and'; to = "          claude -p `"summarise`" --model sonnet`n          Write-Host `"posted the body and"
        expect = 'a model runs in the posting step' }
    @{ what = 'PYTHONSAFEPATH dropped from the posting step''s env'
        from = "      - name: Post the drift audit's output (deterministic)`n        shell: pwsh`n        env:`n          PYTHONSAFEPATH: '1'`n"
        to = "      - name: Post the drift audit's output (deterministic)`n        shell: pwsh`n        env:`n"
        expect = 'the posting step does not set PYTHONSAFEPATH' }
    @{ what = 'PYTHONNOUSERSITE dropped from the posting step''s env'
        from = "      - name: Post the drift audit's output (deterministic)`n        shell: pwsh`n        env:`n          PYTHONSAFEPATH: '1'`n          PYTHONNOUSERSITE: '1'`n"
        to = "      - name: Post the drift audit's output (deterministic)`n        shell: pwsh`n        env:`n          PYTHONSAFEPATH: '1'`n"
        expect = 'the posting step does not set PYTHONNOUSERSITE' }
    @{ what = 'the poster pointed at another directory in the posting step'
        from = '$files = @(python3 ouro/bin/drift-claims.py outbox --dir TestResults/drift-outbox --targets TestResults/drift-targets.json)'; to = '$files = @(python3 ouro/bin/drift-claims.py outbox --dir TestResults/drift-outbox-old --targets TestResults/drift-targets.json)'
        expect = 'the posting step does not run' }
    @{ what = 'the auth probe handed tools it has no use for'
        from = 'claude -p "reply with exactly: ok" --model haiku'
        to = 'claude -p "reply with exactly: ok" --model haiku --allowedTools "Bash(gh issue edit *)"'
        expect = 'the claude auth probe grants tools' }
    @{ what = 'a gh tool added back to the grading grant'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(gh issue edit * --add-label needs-triage)"'
        expect = 'grants a gh tool' }
    @{ what = 'a git tool that writes a file added back (git log --output=)'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(git log:*)"'
        expect = 'grants a git tool outside the read-only set' }
    @{ what = 'a git tool that runs a command added back (git grep --open-files-in-pager)'
        from = 'Bash(git rev-parse:*)"'; to = 'Bash(git rev-parse:*),Bash(git grep:*)"'
        expect = 'grants a git tool outside the read-only set' }
    @{ what = 'unrestricted Bash granted to the grading step'
        from = ',Bash(git rev-parse:*)"'; to = ',Bash"'
        expect = 'grants unrestricted Bash' }
    @{ what = 'the job token restored on the grading step'
        from = "          GH_TOKEN: ''`n        run: |`n          New-Item -ItemType Directory -Force TestResults\intake-manifest"
        to = "          GH_TOKEN: `${{ github.token }}`n        run: |`n          New-Item -ItemType Directory -Force TestResults\intake-manifest"
        expect = 'the grading step does not clear GH_TOKEN' }
    @{ what = 'a second credential handed to the grading step'
        from = "          GH_TOKEN: ''`n        run: |`n          New-Item -ItemType Directory -Force TestResults\intake-manifest"
        to = "          GH_TOKEN: ''`n          OURO_READ_TOKEN: `${{ secrets.OURO_READ_TOKEN }}`n        run: |`n          New-Item -ItemType Directory -Force TestResults\intake-manifest"
        expect = 'the grading step carries a credential' }
    @{ what = 'GH_REPO dropped from the job env'
        from = "`n      GH_REPO: `${{ github.repository }}"; to = ''
        expect = 'does not set GH_REPO to ${{ github.repository }} in its own env' }
    @{ what = 'GH_REPO moved from the job onto a single step, leaving every other step unscoped'
        from = @'
      GH_REPO: ${{ github.repository }}
    steps:
      - uses: actions/checkout@v7
'@
        to = @'
    steps:
      - uses: actions/checkout@v7
        env:
          GH_REPO: ${{ github.repository }}
'@
        expect = 'does not set GH_REPO to ${{ github.repository }} in its own env' }
    @{ what = 'GH_REPO moved out of the pass''s job into a second job that carries it'
        edits = @(
            @{ from = "`n      GH_REPO: `${{ github.repository }}"; to = '' }
            @{ from = "jobs:`n  weekly:"; to = "jobs:`n  notify:`n    runs-on: ubuntu-latest`n    env:`n      GH_REPO: `${{ github.repository }}`n    steps:`n      - run: echo hi`n  weekly:" }
        )
        expect = 'does not set GH_REPO to ${{ github.repository }} in its own env' }
    @{ what = 'a step overriding GH_REPO with another repository'
        from = @'
      - name: Write the docs-freshness report to its rolling issue
        shell: pwsh
'@
        to = @'
      - name: Write the docs-freshness report to its rolling issue
        env:
          GH_REPO: attacker/elsewhere
        shell: pwsh
'@
        expect = 'sets GH_REPO to attacker/elsewhere' }
    @{ what = 'the Edit grant dropped, which would leave the session unable to write its manifest'
        from = ',Edit(TestResults/intake-manifest/**)'; to = ''
        expect = 'is not the enumerated read-only set' }
    @{ what = 'the plugin checkout persisting its read token again'
        from = "          persist-credentials: false   # as above"; to = "          # persist-credentials: false   # as above"
        expect = 'persists its credentials' }
    @{ what = 'the applier called without --unattended'
        from = 'TestResults\intake-manifest --unattended'; to = 'TestResults\intake-manifest'
        expect = 'does not pass --unattended' }
    @{ what = 'the flag spelled as --unattended=true, which the applier refuses'
        from = '--unattended'; to = '--unattended=true'
        expect = 'spells --unattended with a value' }
    @{ what = 'the applier called without --targets, which bounds where a step may post'
        from = ' --no-forbidden-check --targets TestResults\intake-targets.json'; to = ' --no-forbidden-check'
        expect = 'passes no --targets' }
    @{ what = 'the applier bounded by another file than the one the grading step was handed'
        from = '--no-forbidden-check --targets TestResults\intake-targets.json'; to = '--no-forbidden-check --targets TestResults\other-targets.json'
        expect = 'not the ''TestResults\intake-targets.json'' the grading step was handed' }
    @{ what = 'the selecting step not recording the targets file''s hash'
        from = '          "INTAKE_TARGETS_SHA256=$((Get-FileHash TestResults\intake-targets.json -Algorithm SHA256).Hash)" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8'; to = '          $null = 1'
        expect = 'does not write the targets file''s SHA-256 to GITHUB_ENV' }
    @{ what = 'the applying step not hashing the targets file it is about to be bounded by'
        from = '$targetsHash = (Get-FileHash TestResults\intake-targets.json -Algorithm SHA256).Hash'; to = '$targetsHash = $env:INTAKE_TARGETS_SHA256'
        expect = 'never compares the targets file''s SHA-256' }
    @{ what = 'the applying step hashing a sibling file of the targets file'
        from = '$targetsHash = (Get-FileHash TestResults\intake-targets.json -Algorithm'; to = '$targetsHash = (Get-FileHash TestResults\intake-targets.json.orig -Algorithm'
        expect = 'never compares the targets file''s SHA-256' }
    @{ what = 'the hash mismatch not stopping the applying step'
        from = '{ throw "TestResults\intake-targets.json changed after'; to = '{ Write-Host "TestResults\intake-targets.json changed after'
        expect = 'or without a throw on a mismatch' }
    @{ what = 'the hash compare made unreachable, its throw text still naming the variable'
        from = 'if ($targetsHash -cne $env:INTAKE_TARGETS_SHA256) { throw'; to = 'if ($false) { throw'
        expect = 'or without a throw on a mismatch' }
    @{ what = 'the recorded hash overwritten with the fresh one before the compare'
        from = 'if ($targetsHash -cne $env:INTAKE_TARGETS_SHA256) { throw'; to = '$env:INTAKE_TARGETS_SHA256 = $targetsHash; if ($targetsHash -cne $env:INTAKE_TARGETS_SHA256) { throw'
        expect = 'assigns INTAKE_TARGETS_SHA256' }
    @{ what = 'the hash compared only after the applier ran'
        edits = @(
            @{ from = 'if ($targetsHash -cne $env:INTAKE_TARGETS_SHA256) { throw "TestResults\intake-targets.json changed'; to = 'if ($false) { throw "TestResults\intake-targets.json changed' }
            @{ from = "recorded '`$env:INTAKE_TARGETS_SHA256'"; to = "recorded ''" }
            @{ from = 'if ($LASTEXITCODE -ne 0) { throw "applying the intake manifest failed'; to = 'if ($targetsHash -cne $env:INTAKE_TARGETS_SHA256) { throw "late" }; if ($LASTEXITCODE -ne 0) { throw "applying the intake manifest failed' })
        expect = 'after it calls the applier' }
    @{ what = 'the applier called without --repo, which names the repository the week was graded against'
        from = '--unattended --repo $slug'; to = '--unattended'
        expect = 'passes no --repo' }
    @{ what = 'the applier called without --no-forbidden-check, on a runner that holds no private token list'
        from = '--repo $slug --no-forbidden-check'; to = '--repo $slug'
        expect = 'passes no --no-forbidden-check' }
    @{ what = 'the pristine-tree check dropped from the applying step'
        from = '          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)'; to = '          $dirty = @()'
        expect = 'runs the applier out of a tree it never checked' }
    @{ what = 'the pristine-tree check dropped from the loop metrics step'
        from = @'
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          pwsh -NoProfile -File ouro/bin/Get-LoopMetrics.ps1 -Comment
'@
        to = '          pwsh -NoProfile -File ouro/bin/Get-LoopMetrics.ps1 -Comment'
        expect = 'the loop metrics step runs a plugin script out of a tree it never checked' }
    @{ what = 'the pristine-tree check dropped from the append-usage step'
        from = @'
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          if (-not (Test-Path TestResults\drift-audit-result.json)) { exit 0 }
'@
        to = '          if (-not (Test-Path TestResults\drift-audit-result.json)) { exit 0 }'
        expect = 'the append-usage step runs a plugin script out of a tree it never checked' }
    @{ what = 'the model sessions run a fixed model'
        from = '--model $model `'; to = '--model sonnet `'
        expect = 'the /ouro:drift session does not run at the binding''s [models].weekly' }
    @{ what = 'the model read falls back to opus'
        from = "`$model = 'sonnet'"; to = "`$model = 'opus'"
        expect = 'the /ouro:drift session does not run at the binding''s [models].weekly' }
    @{ what = 'the model read drops its no-such-key check'
        from = "if (`"`$model`" -notmatch 'no such key') { throw `"ouro-binding.py get models.weekly failed (exit `$LASTEXITCODE): `$model`" }"; to = ''
        expect = 'the /ouro:intake session does not run at the binding''s [models].weekly' }
    @{ what = 'the model sessions read another key'
        from = 'get models.weekly 2>&1'; to = 'get models.builder 2>&1'
        expect = 'the /ouro:intake session does not run at the binding''s [models].weekly' }
    @{ what = 'the loop metrics tree check moved below the plugin script'
        from = @'
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          pwsh -NoProfile -File ouro/bin/Get-LoopMetrics.ps1 -Comment
'@
        to = @'
          pwsh -NoProfile -File ouro/bin/Get-LoopMetrics.ps1 -Comment
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
'@
        expect = 'the loop metrics step checks the tree only after the plugin script has run' }
    @{ what = 'the loop outcomes step dropped'
        from = @'
      - name: Loop outcomes (deterministic)
        if: ${{ !cancelled() }}
        shell: pwsh
        env:
          PYTHONSAFEPATH: '1'
          PYTHONNOUSERSITE: '1'
        run: |
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          python3 ouro/bin/loop-outcomes.py --comment

'@
        to = ''
        expect = 'expected exactly one step running the loop outcomes, found 0' }
    @{ what = 'the loop outcomes step run twice'
        from = '      - name: Append usage to the rolling issue'
        to = "      - name: Loop outcomes again`n        shell: pwsh`n        run: python3 ouro/bin/loop-outcomes.py --comment`n`n      - name: Append usage to the rolling issue"
        expect = 'expected exactly one step running the loop outcomes, found 2' }
    @{ what = 'the pristine-tree check dropped from the loop outcomes step'
        from = @'
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          python3 ouro/bin/loop-outcomes.py --comment
'@
        to = '          python3 ouro/bin/loop-outcomes.py --comment'
        expect = 'the loop outcomes step runs a plugin script out of a tree it never checked' }
    @{ what = 'the loop outcomes tree check moved below the plugin script'
        from = @'
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          python3 ouro/bin/loop-outcomes.py --comment
'@
        to = @'
          python3 ouro/bin/loop-outcomes.py --comment
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
'@
        expect = 'the loop outcomes step checks the tree only after the plugin script has run' }
    @{ what = 'PYTHONSAFEPATH dropped from the loop outcomes step''s env'
        from = "      - name: Loop outcomes (deterministic)`n        if: `${{ !cancelled() }}`n        shell: pwsh`n        env:`n          PYTHONSAFEPATH: '1'`n"
        to = "      - name: Loop outcomes (deterministic)`n        if: `${{ !cancelled() }}`n        shell: pwsh`n        env:`n"
        expect = 'the loop outcomes step does not set PYTHONSAFEPATH' }
    @{ what = 'PYTHONNOUSERSITE dropped from the loop outcomes step''s env'
        from = @'
          PYTHONNOUSERSITE: '1'
        run: |
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          python3 ouro/bin/loop-outcomes.py --comment
'@
        to = @'
        run: |
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          python3 ouro/bin/loop-outcomes.py --comment
'@
        expect = 'the loop outcomes step does not set PYTHONNOUSERSITE' }
    @{ what = '--comment dropped from the loop outcomes command'
        from = '          python3 ouro/bin/loop-outcomes.py --comment'
        to = '          python3 ouro/bin/loop-outcomes.py'
        expect = 'does not run python3 ouro/bin/loop-outcomes.py --comment' }
    @{ what = 'the loop outcomes command reduced to an echoed string'
        from = '          python3 ouro/bin/loop-outcomes.py --comment'
        to = "          Write-Host 'python3 ouro/bin/loop-outcomes.py --comment'"
        expect = 'does not run python3 ouro/bin/loop-outcomes.py --comment' }
    @{ what = 'the loop outcomes command reduced to an assignment'
        from = '          python3 ouro/bin/loop-outcomes.py --comment'
        to = "          `$cmd = 'python3 ouro/bin/loop-outcomes.py --comment'"
        expect = 'does not run python3 ouro/bin/loop-outcomes.py --comment' }
    @{ what = 'the throw dropped from the loop outcomes tree check'
        from = @'
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          python3 ouro/bin/loop-outcomes.py --comment
'@
        to = '          python3 ouro/bin/loop-outcomes.py --comment'
        expect = 'reads the tree but does not throw on a change' }
    @{ what = 'the plugin-checkout half dropped from the loop outcomes tree check'
        from = @'
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          python3 ouro/bin/loop-outcomes.py --comment
'@
        to = @'
          $dirty = @(git status --porcelain --untracked-files=no)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          python3 ouro/bin/loop-outcomes.py --comment
'@
        expect = 'tree check does not read the plugin checkout' }
    @{ what = 'the repo-root half dropped from the loop outcomes tree check'
        from = @'
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          python3 ouro/bin/loop-outcomes.py --comment
'@
        to = @'
          $dirty = @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          python3 ouro/bin/loop-outcomes.py --comment
'@
        expect = 'tree check does not read the repo root' }
    @{ what = 'the loop outcomes tree check reordered as throw, plugin script, then the read'
        from = @'
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          python3 ouro/bin/loop-outcomes.py --comment
'@
        to = @'
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          python3 ouro/bin/loop-outcomes.py --comment
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
'@
        expect = 'the loop outcomes step checks the tree only after the plugin script has run' }
    @{ what = 'the loop outcomes tree check split into two assignments'
        from = @'
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          python3 ouro/bin/loop-outcomes.py --comment
'@
        to = @'
          $dirty = @(git status --porcelain --untracked-files=no)
          $dirty = @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          python3 ouro/bin/loop-outcomes.py --comment
'@
        expect = 'the loop outcomes step''s tree check does not read both halves into one' }
    @{ what = 'the loop metrics tree check reordered as throw, plugin script, then the read'
        from = @'
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          pwsh -NoProfile -File ouro/bin/Get-LoopMetrics.ps1 -Comment
'@
        to = @'
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          pwsh -NoProfile -File ouro/bin/Get-LoopMetrics.ps1 -Comment
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
'@
        expect = 'the loop metrics step checks the tree only after the plugin script has run' }
    @{ what = 'the loop metrics tree check split into two assignments'
        from = @'
          $dirty = @(git status --porcelain --untracked-files=no) + @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          pwsh -NoProfile -File ouro/bin/Get-LoopMetrics.ps1 -Comment
'@
        to = @'
          $dirty = @(git status --porcelain --untracked-files=no)
          $dirty = @(git -C ouro status --porcelain --untracked-files=all)
          if ($dirty) { throw "a file changed or appeared while the grading session ran, so no plugin script runs from this checkout:`n$($dirty -join "`n")" }
          pwsh -NoProfile -File ouro/bin/Get-LoopMetrics.ps1 -Comment
'@
        expect = 'the loop metrics step''s tree check does not read both halves into one' }
    @{ what = 'the plugin-checkout half of the check narrowed back to tracked files'
        from = 'git -C ouro status --porcelain --untracked-files=all'; to = 'git -C ouro status --porcelain --untracked-files=no'
        expect = 'does not report an untracked file' }
    @{ what = 'the repo-root half of the check widened to every untracked file'
        from = '$dirty = @(git status --porcelain --untracked-files=no)'; to = '$dirty = @(git status --porcelain --untracked-files=all)'
        expect = 'reports untracked files, which in a consumer include the plugin checkout' }
    @{ what = 'PYTHONSAFEPATH dropped from the applying step''s env'
        from = "          PYTHONSAFEPATH: '1'`n"; to = ''
        expect = 'does not set PYTHONSAFEPATH' }
    @{ what = 'PYTHONNOUSERSITE dropped from the applying step''s env'
        from = "          PYTHONNOUSERSITE: '1'`n"; to = ''
        expect = 'does not set PYTHONNOUSERSITE' }
    @{ what = 'the env block dropped and PYTHONSAFEPATH spelled as a line of the run block instead'
        from = "        env:`n          PYTHONSAFEPATH: '1'`n          PYTHONNOUSERSITE: '1'`n        run: |`n"; to = "        run: |`n          PYTHONSAFEPATH: '1'`n"
        expect = 'does not set PYTHONSAFEPATH' }
    @{ what = 'the env block dropped, the run header chomped, and both keys spelled as lines of the run block'
        from = "        env:`n          PYTHONSAFEPATH: '1'`n          PYTHONNOUSERSITE: '1'`n        run: |`n"; to = "        run: |-`n          PYTHONSAFEPATH: '1'`n          PYTHONNOUSERSITE: '1'`n"
        expect = 'does not set PYTHONSAFEPATH' }
    @{ what = 'the stop worded for a tracked file again'
        from = 'throw "a file changed or appeared while the grading session ran'; to = 'throw "a tracked file changed while the grading session ran'
        expect = 'stop says a tracked file changed' }
    @{ what = 'the repo-root half widened with the short switch'
        from = '$dirty = @(git status --porcelain --untracked-files=no)'; to = '$dirty = @(git status --porcelain -uall)'
        expect = 'reports untracked files, which in a consumer include the plugin checkout' }
    @{ what = 'PYTHONSAFEPATH set to an empty string, which Python reads as unset'
        from = "          PYTHONSAFEPATH: '1'"; to = "          PYTHONSAFEPATH: ''"
        expect = 'does not set PYTHONSAFEPATH' }
    @{ what = 'a model run added to the applying step'
        from = '          python3 ouro/bin/apply-manifest.py'; to = "          claude -p `"apply it`"`n          python3 ouro/bin/apply-manifest.py"
        expect = 'a model runs in the applying step' }
    @{ what = 'the two steps merged back into one that grades and applies'
        from = '            > TestResults\intake-grading-result.json'; to = "            > TestResults\intake-grading-result.json`n          python3 ouro/bin/apply-manifest.py TestResults\intake-manifest --unattended"
        expect = 'the grading step applies its own manifest' }
    @{ what = 'the ouro version stamp step is dropped'
        from = $StampStepBlock; to = ''
        expect = 'expected exactly one step computing the ouro version stamp, found 0' }
    @{ what = 'the stamp reads the consumer checkout''s HEAD, not the plugin''s'
        from = '$sha = git -C ouro rev-parse HEAD'; to = '$sha = git rev-parse HEAD'
        expect = 'expected exactly one step computing the ouro version stamp, found 0' }
    @{ what = 'the stamp drops the ref it was asked for'
        from = '"OURO_STAMP=ouro=${{ vars.OURO_REF }}@$sha"'; to = '"OURO_STAMP=$sha"'
        expect = 'expected exactly one step computing the ouro version stamp, found 0' }
    @{ what = 'the docs-freshness report loses the version stamp'
        from = 'rewritten each run ($env:OURO_STAMP).'; to = 'rewritten each run.'
        expect = 'the docs-freshness report does not carry the ouro version stamp' }
    @{ what = 'the append-usage comment loses the version stamp'
        from = '${{ github.run_id }} ($env:OURO_STAMP):'; to = '${{ github.run_id }}:'
        expect = 'the append-usage comment does not carry the ouro version stamp' }
)

# One substitution, or an ordered `edits` list for an edit that has two ends -- a line moved from
# one place to another is not expressible as one. $null when any single substitution changed
# nothing: that is a drifted anchor, and the caller reports it rather than asserting on a no-op.
function Get-Mutated([string]$Text, $Case) {
    $edits = if ($Case.ContainsKey('edits')) { @($Case.edits) } else { @(@{ from = $Case.from; to = $Case.to }) }
    $out = $Text
    foreach ($e in $edits) {
        # An anchor lifted out of the template rather than spelled here comes back empty when it
        # no longer parses; reported as a drifted anchor, like any other substitution that misses.
        if (-not $e.from) { return $null }
        $next = $out.Replace($e.from, $e.to)
        if ($next -eq $out) { return $null }
        $out = $next
    }
    return $out
}

# The read confinement of each model session, one substitution at a time. Each anchor runs on into the
# step's own --allowedTools, so it names one of the two steps and not both.
$bt = [string][char]96
$sp = ' ' * 12
foreach ($who in @(@{ name = 'drift audit'; tail = 'Edit(TestResults/drift-outbox'; expect = 'the drift audit step' },
                   @{ name = 'grading'; tail = 'Edit(TestResults/intake-manifest'; expect = 'the grading step' })) {
    $head = "$sp--plugin-dir ouro $bt`n$sp--settings `$confine $bt`n$sp--output-format json $bt`n$sp--allowedTools `"Skill,Read,Grep,Glob,Agent,Task,$($who.tail)"
    $mutants += @(
        @{ what = "the plugin directory dropped from the $($who.name) session"
            from = $head; to = $head.Replace("$sp--plugin-dir ouro $bt`n", '')
            expect = "$($who.expect) does not load the plugin from the in-tree" }
        @{ what = "the settings dropped from the $($who.name) session"
            from = $head; to = $head.Replace("$sp--settings `$confine $bt`n", '')
            expect = "$($who.expect) passes no read-confining --settings" }
        @{ what = "the settings of the $($who.name) session pointed at another file"
            from = $head; to = $head.Replace('--settings $confine', '--settings $env:RUNNER_TEMP')
            expect = "$($who.expect) passes no read-confining --settings" }
    )
}
$mutants += @(
    @{ what = 'the settings file written without the read block'
        from = '"blockReadsOutsideWorkingDirectories":true'; to = '"blockReadsOutsideWorkingDirectories":false'
        expect = 'does not hold permissions.blockReadsOutsideWorkingDirectories: true' }
    @{ what = 'the settings file written inside the workspace'
        from = '$confine = Join-Path $env:RUNNER_TEMP'; to = '$confine = Join-Path $PWD'
        expect = 'somewhere other than RUNNER_TEMP' }
    @{ what = 'the intake session''s Edit widened to the whole results directory'
        from = 'Edit(TestResults/intake-manifest/**)'; to = 'Edit(TestResults/**)'
        expect = 'is not the enumerated read-only set' }
)

foreach ($m in $mutants) {
    $mutated = Get-Mutated $Text $m
    if ($null -eq $mutated) {
        Write-Host "FAIL: the mutant for '$($m.what)' changed nothing -- its anchor no longer appears in the template" -ForegroundColor Red
        $failures++
        continue
    }
    $got = @(Get-IntakeStepFindings $mutated)
    $hit = @($got | Where-Object { $_ -match [regex]::Escape($m.expect) })
    Assert-Equal 1 ([math]::Min($hit.Count, 1)) "the suite goes red on $($m.what)"
    if ($hit.Count -eq 0) { Write-Host "      findings were: $($got -join ' | ')" -ForegroundColor DarkYellow }
}

# --- rewrites that must NOT turn it red --------------------------------------------------------
# This suite runs on every consumer's runner, so a false red is a broken weekly pass in a repo
# that changed nothing that matters. Each of these is the same YAML as the shipped line.
# Lifted from the template, not copied: the block carries a comment, and a copy of it here would
# drift the day that comment is reworded.
$JobEnvBlock = if ($Text -match '(?ms)(^    env:\s*?$.*?)(?=^    [A-Za-z0-9_-]+:)') { $Matches[1] } else { '' }
$accepted = @(
    @{ what = 'the drift audit step''s cleared token spelled with double quotes'
        from = "          GH_TOKEN: ''`n        run: |`n          if (`$env:DRIFT_ISSUE_NUMBER -notmatch"; to = "          GH_TOKEN: `"`"`n        run: |`n          if (`$env:DRIFT_ISSUE_NUMBER -notmatch" }
    @{ what = 'a trailing comment on the posting step''s env line'
        from = "      - name: Post the drift audit's output (deterministic)`n        shell: pwsh`n        env:`n"; to = "      - name: Post the drift audit's output (deterministic)`n        shell: pwsh`n        env:   # both keys, one place`n" }
    @{ what = 'a trailing comment on the GH_REPO line'
        from = "`n      GH_REPO: `${{ github.repository }}"; to = "`n      GH_REPO: `${{ github.repository }}   # named once for the whole job" }
    @{ what = 'the GH_REPO value quoted'
        from = "`n      GH_REPO: `${{ github.repository }}"; to = "`n      GH_REPO: `"`${{ github.repository }}`"" }
    @{ what = 'a step repeating the job value'
        from = "      - name: Loop metrics (deterministic)`n"; to = "      - name: Loop metrics (deterministic)`n        env:`n          GH_REPO: `${{ github.repository }}`n" }
    @{ what = 'a comment line inside the env block, at the job''s own indent'
        from = "      GH_TOKEN: `${{ github.token }}`n"; to = "      GH_TOKEN: `${{ github.token }}`n    # one place for the whole job`n" }
    @{ what = "a trailing comment on the apply step's env line"
        from = "        env:`n          PYTHONSAFEPATH: '1'"; to = "        env:   # both keys, one place`n          PYTHONSAFEPATH: '1'" }
    @{ what = "the apply step's env spelled after its run block"
        edits = @(
            @{ from = "intake manifest (deterministic)`n        shell: pwsh`n        env:`n          PYTHONSAFEPATH: '1'`n          PYTHONNOUSERSITE: '1'`n        run: |`n"; to = "intake manifest (deterministic)`n        shell: pwsh`n        run: |`n" }
            @{ from = "          if (`$LASTEXITCODE -ne 0) { throw `"applying the intake manifest failed (exit `$LASTEXITCODE)`" }`n"
                to = "          if (`$LASTEXITCODE -ne 0) { throw `"applying the intake manifest failed (exit `$LASTEXITCODE)`" }`n        env:`n          PYTHONSAFEPATH: '1'`n          PYTHONNOUSERSITE: '1'`n" }
        ) }
    @{ what = "the job's env spelled after its steps"
        edits = @(
            @{ from = $JobEnvBlock; to = '' }
            @{ from = "          if-no-files-found: ignore`n"; to = "          if-no-files-found: ignore`n`n$JobEnvBlock" }
        ) }
)
foreach ($a in $accepted) {
    $variant = Get-Mutated $Text $a
    if ($null -eq $variant) {
        Write-Host "FAIL: the variant for '$($a.what)' changed nothing -- its anchor no longer appears in the template" -ForegroundColor Red
        $failures++
        continue
    }
    Assert-Equal '' ((@(Get-IntakeStepFindings $variant)) -join ' | ') "the suite stays green on $($a.what)"
}

# --- the harvest step, executed ----------------------------------------------------------------
# Everything above reads the template as text. This runs the harvest step's own script, once per
# case, in a child pwsh under a stub resolver and a stub gh, so the path the pass takes when no
# ledger exists is pinned by what it does: the number comes from stdout only (a URL a warning
# carries on stderr does not win it), and a failed create, or one that prints no issue URL, throws
# before anything is exported. Case values reach the child through environment variables, never
# spliced into its code.
$harvest = @(Get-StepBlocks $Text | Where-Object { $_ -match '(?m)^\s*- name: Harvest the audit ledger from the rolling issue\s*$' })
if ($harvest.Count -ne 1) {
    Write-Host "FAIL: expected one harvest step to execute, found $($harvest.Count)" -ForegroundColor Red; $failures++
} else {
    $hLines = @($harvest[0] -split "`r?`n")
    $runAt = [array]::FindIndex([string[]]$hLines, [Predicate[string]]{ param($l) $l -match '^\s*run: \|\s*$' })
    $runCode = (@($hLines[($runAt + 1)..($hLines.Count - 1)]) | ForEach-Object { $_ -replace '^ {10}', '' }) -join "`n"
    $prelude = @'
$ErrorActionPreference = 'Stop'
function gh {
    Add-Content -LiteralPath $env:HARVEST_GH_LOG -Value ('gh ' + ($args -join ' '))
    if ($args[0] -eq 'issue' -and $args[1] -eq 'create') {
        $global:LASTEXITCODE = [int]$env:HARVEST_RC
        if ($env:HARVEST_ERR) { [System.Management.Automation.ErrorRecord]::new([Exception]::new($env:HARVEST_ERR), 'stderr', 'NotSpecified', $null) }
        return @($env:HARVEST_OUT -split "`n" | Where-Object { $_ })
    }
    $global:LASTEXITCODE = 0
    if ("$args" -match 'createdAt') { $global:LASTEXITCODE = [int]$env:HARVEST_NEWEST_RC; return @($env:HARVEST_NEWEST | Where-Object { $_ }) }
    $global:LASTEXITCODE = [int]$env:HARVEST_COMMENTS_RC
    return $env:HARVEST_COMMENTS
}
Set-Location -LiteralPath $env:HARVEST_DIR
'@
    # The ledger carries umbrella alone: any label flag gh's pflag parses that is not exactly
    # `--label umbrella` fails a row. Case-sensitive, so -L (--limit) is not a label.
    $extraLabel = '(^|\s)(--label|-l)(?!\s+umbrella(\s|$))'
    $createLine = 'gh issue create --title Docs drift audit --label umbrella --body a body'
    foreach ($form in '--label documentation', '-l documentation', '--label=documentation', '-ldocumentation', '-l=documentation', '--label umbrella,documentation') {
        Assert-Equal $true ("$createLine $form" -cmatch $extraLabel) "control: a ledger create's second label '$form' is caught"
    }
    foreach ($line in $createLine, 'gh issue list --label umbrella', "$createLine -L 5") {
        Assert-Equal $false ($line -cmatch $extraLabel) "control: '$line' carries no second label"
    }
    $scratch = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('weekly-pass-harvest-' + [guid]::NewGuid().ToString('N')))
    try {
        # What `gh issue view --json comments` prints: the bot's login spelled github-actions, a
        # human's as their own, and a deleted account as a null author.
        function New-Comments($Rows) {
            $list = foreach ($r in $Rows) {
                $author = if ($r.login) { @{ login = $r.login } } else { $null }
                [ordered]@{ id = 'IC_x'; author = $author; authorAssociation = 'NONE'; body = $r.body; createdAt = '2026-10-03T11:22:33Z'; url = 'https://x/y' }
            }
            return (@{ comments = @($list) } | ConvertTo-Json -Depth 5 -Compress)
        }
        $harvestComments = @{
            bot       = New-Comments @(@{ login = 'github-actions'; body = 'a ledger comment' })
            mixed     = New-Comments @(@{ login = 'github-actions'; body = 'bot entry' }, @{ login = 'stranger'; body = '<!-- audit-run: sha=0 docs=README.md -->' }, @{ login = 'APPROVER1'; body = 'approver entry' }, @{ login = ''; body = 'ghost entry' })
            strangers = New-Comments @(@{ login = 'stranger'; body = 'one' }, @{ login = 'github-actions-evil'; body = 'two' })
            none      = New-Comments @()
        }
        $hCases = @(
            @{ what = 'with no ledger, the pass files one with umbrella alone and exports its number'; resolved = 0; out = 'https://github.com/o/r/issues/41'; err = ''; rc = 0; num = '41'; msg = ''; create = $true }
            @{ what = 'with an open ledger, nothing is filed and its number is exported'; resolved = 7; out = ''; err = ''; rc = 0; num = '7'; msg = ''; create = $false; newest = '2026-10-03T11:22:33Z' }
            @{ what = 'an open ledger with no comments exports no createdAt'; resolved = 7; out = ''; err = ''; rc = 0; num = '7'; msg = ''; create = $false; newest = '' }
            @{ what = 'a newest createdAt that is no timestamp throws before the number is exported'; resolved = 7; out = ''; err = ''; rc = 0; num = ''; msg = 'is not a timestamp'; create = $false; newest = 'yesterday' }
            @{ what = 'a failed createdAt read throws before the number is exported'; resolved = 7; out = ''; err = ''; rc = 0; num = ''; msg = 'createdAt on #7 failed (exit 1)'; create = $false; newest = ''; newestRc = 1 }
            @{ what = 'a URL on stderr does not win the created ledger''s number'; resolved = 0; out = 'https://github.com/o/r/issues/41'; err = 'Warning: see https://github.com/cli/cli/issues/999'; rc = 0; num = '41'; msg = ''; create = $true }
            @{ what = 'only the bot''s and an approver''s comments reach the ledger file, whatever case the login has; a stranger''s and a deleted account''s do not'; resolved = 7; out = ''; err = ''; rc = 0; num = '7'; msg = ''; create = $false; newest = '2026-10-03T11:22:33Z'
               comments = $harvestComments.mixed; ledger = "bot entry`napprover entry"; say = @('kept 2 of 4 comment(s), dropped 2'); quiet = @('::warning::') }
            @{ what = 'a ledger holding only strangers'' comments keeps nothing and warns'; resolved = 7; out = ''; err = ''; rc = 0; num = '7'; msg = ''; create = $false; newest = '2026-10-03T11:22:33Z'
               comments = $harvestComments.strangers; ledger = ''; say = @('kept 0 of 2 comment(s), dropped 2', '::warning::none of the 2 comment(s) on #7'); quiet = @() }
            @{ what = 'a ledger with no comments keeps nothing and does not warn'; resolved = 7; out = ''; err = ''; rc = 0; num = '7'; msg = ''; create = $false; newest = ''
               comments = $harvestComments.none; ledger = ''; say = @('kept 0 of 0 comment(s)'); quiet = @('::warning::') }
            @{ what = 'a failed comments read throws before the number is exported'; resolved = 7; out = ''; err = ''; rc = 0; num = ''; msg = 'reading the comments of #7 failed'; create = $false; newest = ''; commentsRc = 1 }
            @{ what = 'a failed create throws and exports no number'; resolved = 0; out = 'HTTP 403'; err = ''; rc = 1; num = ''; msg = 'failed (exit 1)'; create = $true }
            @{ what = 'a create that prints no issue URL throws and exports no number'; resolved = 0; out = 'something else'; err = ''; rc = 0; num = ''; msg = 'printed no issue URL'; create = $true }
        )
        $i = 0
        foreach ($c in $hCases) {
            $i++
            if (-not $c.ContainsKey('newest')) { $c.newest = '' }
            if (-not $c.ContainsKey('newestRc')) { $c.newestRc = 0 }
            if (-not $c.ContainsKey('commentsRc')) { $c.commentsRc = 0 }
            if (-not $c.ContainsKey('comments')) { $c.comments = $harvestComments.bot }
            if (-not $c.ContainsKey('ledger')) { $c.ledger = 'a ledger comment' }
            if (-not $c.ContainsKey('say')) { $c.say = @() }
            if (-not $c.ContainsKey('quiet')) { $c.quiet = @() }
            $dir = New-Item -ItemType Directory -Path (Join-Path $scratch.FullName "case$i")
            New-Item -ItemType Directory -Path (Join-Path $dir.FullName 'ouro/bin') | Out-Null
            # The real library, so the filter that runs is the shipped one; only the resolver is stubbed, and
            # the binding read is replaced by an approver list the case names.
            $realLib = (Join-Path $Base 'bin/Get-RollingIssue.ps1') -replace "'", "''"
            Set-Content -LiteralPath (Join-Path $dir.FullName 'ouro/bin/Get-RollingIssue.ps1') -Value ". '$realLib'`nfunction Get-RollingIssueNumber { param(`$Title, `$Key) return [int]`$env:HARVEST_RESOLVED }`n`$PSDefaultParameterValues['Get-TrustedComments:Approvers'] = @('Approver1')"
            $script = Join-Path $dir.FullName 'harvest.ps1'
            Set-Content -LiteralPath $script -Value ($prelude + "`n" + $runCode) -Encoding utf8
            $envFile = Join-Path $dir.FullName 'github_env'; $ghLog = Join-Path $dir.FullName 'gh.log'
            New-Item -ItemType File -Path $envFile, $ghLog | Out-Null
            $saved = @{}
            $vars = @{ HARVEST_DIR = $dir.FullName; HARVEST_GH_LOG = $ghLog; HARVEST_RESOLVED = "$($c.resolved)"; HARVEST_OUT = $c.out; HARVEST_ERR = $c.err
                       HARVEST_RC = "$($c.rc)"; HARVEST_NEWEST = "$($c.newest)"; HARVEST_COMMENTS = $c.comments; HARVEST_COMMENTS_RC = "$($c.commentsRc)"; HARVEST_NEWEST_RC = "$($c.newestRc)"; GITHUB_ENV = $envFile; DRIFT_ISSUE_TITLE = 'Docs drift audit' }
            foreach ($k in $vars.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $vars[$k]) }
            try { $childOut = (& pwsh -NoProfile -File $script 2>&1 | Out-String); $childRc = $LASTEXITCODE }
            finally { foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) } }
            $exportedLines = @(Get-Content -LiteralPath $envFile)
            $exported = (@($exportedLines | Where-Object { $_ -like 'DRIFT_ISSUE_NUMBER=*' })) -join "`n"
            $since = (@($exportedLines | Where-Object { $_ -like 'DRIFT_CLAIMS_SINCE=*' })) -join "`n"
            $wantSince = if ($c.newest -match '^\d{4}-') { "DRIFT_CLAIMS_SINCE=$($c.newest)" } else { '' }
            # The ledger file stays the comment bodies the stub's first call returned, whatever the export does.
            $ledger = ("$(Get-Content -LiteralPath (Join-Path $dir.FullName 'TestResults\audit-ledger.txt') -Raw -ErrorAction Ignore)" -replace "`r`n", "`n").Trim()
            $wantLedger = if ($c.resolved) { $c.ledger } else { '' }
            $log = "$(Get-Content -LiteralPath $ghLog -Raw)"
            $ok = if ($c.num) { $childRc -eq 0 -and $exported -ceq "DRIFT_ISSUE_NUMBER=$($c.num)" } else { $childRc -ne 0 -and $exported -eq '' -and $childOut.Contains($c.msg) }
            if ($c.num) { $ok = $ok -and $since -ceq $wantSince -and $ledger -ceq $wantLedger }
            foreach ($s in $c.say) { $ok = $ok -and $childOut.Contains($s) }
            foreach ($s in $c.quiet) { $ok = $ok -and -not $childOut.Contains($s) }
            if ($c.create) { $ok = $ok -and $log -match 'gh issue create .*--label umbrella --body' -and $log -cnotmatch $extraLabel }
            else { $ok = $ok -and $log -notmatch 'gh issue create' }
            if ($ok) { Write-Host "  ok: the harvest step, run: $($c.what)" -ForegroundColor DarkGray }
            else { Write-Host "FAIL: the harvest step, run: $($c.what) -- exit $childRc, exported '$exported', since '$since', ledger '$ledger'`n$childOut" -ForegroundColor Red; $failures++ }
        }

        # --- the claims step, executed -------------------------------------------------------
        # The same child-process harness. The step's own script runs under the real filter and a
        # stub gh, git and python3; the stub python3 copies the comments file `ingest` was handed,
        # so the case reads what ingest would have read, not what the step meant to write.
        $claimsStep = @(Get-StepBlocks $Text | Where-Object { $_ -match 'drift-claims\.py\s+ingest' })
        if ($claimsStep.Count -ne 1) {
            Write-Host "FAIL: expected one claims step to execute, found $($claimsStep.Count)" -ForegroundColor Red; $failures++
        } else {
            $cLines = @($claimsStep[0] -split "`r?`n")
            $cRunAt = [array]::FindIndex([string[]]$cLines, [Predicate[string]]{ param($l) $l -match '^\s*run: \|\s*$' })
            $cCode = (@($cLines[($cRunAt + 1)..($cLines.Count - 1)]) | ForEach-Object { $_ -replace '^ {10}', '' }) -join "`n"
            $cPrelude = @'
$ErrorActionPreference = 'Stop'
function git { $global:LASTEXITCODE = 0 }
function gh {
    Add-Content -LiteralPath $env:CLAIMS_GH_LOG -Value ('gh ' + ($args -join ' '))
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'issue' -and $args[1] -eq 'view') { return $env:CLAIMS_COMMENTS }
}
function python3 {
    Add-Content -LiteralPath $env:CLAIMS_GH_LOG -Value ('python3 ' + ($args -join ' '))
    $global:LASTEXITCODE = 0
    $at = [array]::IndexOf([string[]]$args, '--comments')
    Copy-Item -LiteralPath $args[$at + 1] -Destination $env:CLAIMS_CAPTURE
}
Set-Location -LiteralPath $env:CLAIMS_DIR
'@
            $marker = '<!-- audit-run: sha=0 docs=README.md -->' + "`n" + '```claim-records' + "`n" + '[]' + "`n" + '```'
            $cComments = @{
                mixed     = New-Comments @(@{ login = 'github-actions'; body = 'bot entry' }, @{ login = 'stranger'; body = $marker }, @{ login = 'Approver1'; body = 'approver entry' })
                strangers = New-Comments @(@{ login = 'stranger'; body = $marker })
            }
            $cCases = @(
                @{ what = 'ingest reads the bot''s and an approver''s comments and not a stranger''s audit-run marker'; comments = $cComments.mixed; want = @('bot entry', 'approver entry'); wantNot = @('audit-run', 'claim-records', 'stranger'); kept = 'kept 2 of 3' }
                @{ what = 'ingest reads an empty list when every comment is a stranger''s'; comments = $cComments.strangers; want = @('"comments":[]'); wantNot = @('audit-run', 'stranger'); kept = 'kept 0 of 1' }
            )
            $j = 0
            foreach ($c in $cCases) {
                $j++
                $dir = New-Item -ItemType Directory -Path (Join-Path $scratch.FullName "claims$j")
                New-Item -ItemType Directory -Path (Join-Path $dir.FullName 'ouro/bin'), (Join-Path $dir.FullName 'TestResults') | Out-Null
                $realLib = (Join-Path $Base 'bin/Get-RollingIssue.ps1') -replace "'", "''"
                Set-Content -LiteralPath (Join-Path $dir.FullName 'ouro/bin/Get-RollingIssue.ps1') -Value ". '$realLib'`nfunction Get-RollingIssueNumber { param(`$Title, `$Key) return 7 }`n`$PSDefaultParameterValues['Get-TrustedComments:Approvers'] = @('Approver1')"
                Set-Content -LiteralPath (Join-Path $dir.FullName 'TestResults/drift-targets.json') -Value ('{"head":"' + ('a' * 40) + '"}')
                $script = Join-Path $dir.FullName 'claims.ps1'
                Set-Content -LiteralPath $script -Value ($cPrelude + "`n" + $cCode) -Encoding utf8
                $ghLog = Join-Path $dir.FullName 'gh.log'; $capture = Join-Path $dir.FullName 'captured.json'
                New-Item -ItemType File -Path $ghLog | Out-Null
                $saved = @{}
                $vars = @{ CLAIMS_DIR = $dir.FullName; CLAIMS_GH_LOG = $ghLog; CLAIMS_CAPTURE = $capture; CLAIMS_COMMENTS = $c.comments
                           DRIFT_ISSUE_TITLE = 'Docs drift audit'; DRIFT_ISSUE_NUMBER = '7'; DRIFT_CLAIMS_SINCE = '2026-10-03T11:22:00Z' }
                foreach ($k in $vars.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $vars[$k]) }
                try { $childOut = (& pwsh -NoProfile -File $script 2>&1 | Out-String); $childRc = $LASTEXITCODE }
                finally { foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) } }
                $seen = if (Test-Path -LiteralPath $capture) { Get-Content -LiteralPath $capture -Raw } else { $null }
                $ok = $childRc -eq 0 -and $null -ne $seen -and $childOut.Contains($c.kept)
                if ($null -ne $seen) {
                    foreach ($s in $c.want) { $ok = $ok -and $seen.Contains($s) }
                    foreach ($s in $c.wantNot) { $ok = $ok -and -not $seen.Contains($s) }
                    # The kept comment is passed on as written: its createdAt is not reformatted.
                    if ($c.want.Count -gt 1) { $ok = $ok -and $seen.Contains('"createdAt":"2026-10-03T11:22:33Z"') }
                }
                if ($ok) { Write-Host "  ok: the claims step, run: $($c.what)" -ForegroundColor DarkGray }
                else { Write-Host "FAIL: the claims step, run: $($c.what) -- exit $childRc, ingest read '$seen'`n$childOut" -ForegroundColor Red; $failures++ }
            }
        }

        # --- the usage step, executed ----------------------------------------------------------
        # The step posts fields of a file a session could have written, as the job token's bot, to
        # the ledger the next harvest reads. Run under a stub gh, once per result file: whatever the
        # file holds, the comment it posts carries numbers and no text from it.
        function Invoke-UsageStep([string]$Template, [string]$ResultJson, [string]$Label) {
            $step = @(Get-StepBlocks $Template | Where-Object { $_ -match '(?m)^\s*- name: Append usage to the rolling issue\s*$' })
            if ($step.Count -ne 1) { return @{ rc = 1; posted = ''; out = "expected one usage step, found $($step.Count)" } }
            $uLines = @($step[0] -split "`r?`n")
            $uRunAt = [array]::FindIndex([string[]]$uLines, [Predicate[string]]{ param($l) $l -match '^\s*run: \|\s*$' })
            $uCode = (@($uLines[($uRunAt + 1)..($uLines.Count - 1)]) | ForEach-Object { $_ -replace '^ {10}', '' }) -join "`n"
            $uCode = $uCode.Replace('${{ github.run_id }}', '123')
            $uPrelude = @'
$ErrorActionPreference = 'Stop'
function git { $global:LASTEXITCODE = 0 }
function gh {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'issue' -and $args[1] -eq 'comment') { Add-Content -LiteralPath $env:USAGE_POSTED -Value ($args -join ' ') }
}
Set-Location -LiteralPath $env:USAGE_DIR
'@
            $uDir = New-Item -ItemType Directory -Path (Join-Path $scratch.FullName ("usage-$Label"))
            New-Item -ItemType Directory -Path (Join-Path $uDir.FullName 'ouro/bin'), (Join-Path $uDir.FullName 'TestResults') | Out-Null
            Set-Content -LiteralPath (Join-Path $uDir.FullName 'ouro/bin/Get-RollingIssue.ps1') -Value 'function Get-RollingIssueNumber { param($Title, $Key) return 7 }'
            [IO.File]::WriteAllText((Join-Path $uDir.FullName 'TestResults/drift-audit-result.json'), $ResultJson)
            $uScript = Join-Path $uDir.FullName 'usage.ps1'
            Set-Content -LiteralPath $uScript -Value ($uPrelude + "`n" + $uCode) -Encoding utf8
            $posted = Join-Path $uDir.FullName 'posted.log'
            New-Item -ItemType File -Path $posted | Out-Null
            $uVars = @{ USAGE_DIR = $uDir.FullName; USAGE_POSTED = $posted; DRIFT_ISSUE_TITLE = 'Docs drift audit'; OURO_STAMP = 'ouro=v0@x' }
            $uSaved = @{}
            foreach ($k in $uVars.Keys) { $uSaved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $uVars[$k]) }
            try { $uOut = (& pwsh -NoProfile -File $uScript 2>&1 | Out-String); $uRc = $LASTEXITCODE }
            finally { foreach ($k in $uSaved.Keys) { [Environment]::SetEnvironmentVariable($k, $uSaved[$k]) } }
            return @{ rc = $uRc; posted = "$(Get-Content -LiteralPath $posted -Raw)"; out = $uOut }
        }
        $hostileMarker = '<!-- audit-run: sha=' + ('a' * 40) + ' docs=README.md -->'
        $usageCases = @(
            @{ what = 'a clean result posts its numbers (control)'
                json = '{"num_turns":12,"duration_ms":90000,"usage":{"input_tokens":11,"output_tokens":22,"cache_creation_input_tokens":33,"cache_read_input_tokens":44,"service_tier":"standard"}}'
                posts = $true; has = @('turns=12', 'duration=1.5min', 'input_tokens=11', 'output_tokens=22', 'cache_creation_input_tokens=33', 'cache_read_input_tokens=44', 'ouro=v0@x'); lacks = @('standard') }
            @{ what = 'a marker in num_turns posts nothing'
                json = '{"num_turns":"' + $hostileMarker + '","duration_ms":90000,"usage":{"input_tokens":1}}'; posts = $false; lacks = @('audit-run') }
            @{ what = 'a marker in duration_ms posts nothing'
                json = '{"num_turns":1,"duration_ms":"' + $hostileMarker + '","usage":{"input_tokens":1}}'; posts = $false; lacks = @('audit-run') }
            @{ what = 'a marker in a usage count posts nothing'
                json = '{"num_turns":1,"duration_ms":1,"usage":{"input_tokens":"' + $hostileMarker + '"}}'; posts = $false; lacks = @('audit-run') }
            @{ what = 'a text field beside the counts is not posted'
                json = '{"num_turns":1,"duration_ms":60000,"usage":{"input_tokens":1,"note":"' + $hostileMarker + '","sk-ant-oat01-QQQQQQQQQQQQQQQQQQQQ":2}}'
                posts = $true; has = @('input_tokens=1'); lacks = @('audit-run', 'sk-ant', 'note') }
        )
        $uj = 0
        foreach ($c in $usageCases) {
            $uj++
            $res = Invoke-UsageStep $Text $c.json "c$uj"
            $ok = if ($c.posts) { $res.rc -eq 0 -and $res.posted -match 'issue comment 7 --body' } else { $res.rc -ne 0 -and -not $res.posted.Trim() }
            foreach ($s in $(if ($c.ContainsKey('has')) { $c.has } else { @() })) { $ok = $ok -and $res.posted.Contains($s) }
            foreach ($s in $c.lacks) { $ok = $ok -and -not $res.posted.Contains($s) }
            if ($ok) { Write-Host "  ok: the usage step, run: $($c.what)" -ForegroundColor DarkGray }
            else { Write-Host "FAIL: the usage step, run: $($c.what) -- exit $($res.rc), posted '$($res.posted)'`n$($res.out)" -ForegroundColor Red; $failures++ }
        }
        # The mutant: the step as it was, which posts the result's fields as they are.
        $castFrom = '$usage = (''input_tokens'', ''output_tokens'', ''cache_creation_input_tokens'', ''cache_read_input_tokens'' | ForEach-Object { "$_=$([long]$r.usage.$_)" }) -join '', '''
        $uMutant = Get-Mutated $Text @{ edits = @(
            @{ from = $castFrom; to = '$usage = ($r.usage | ConvertTo-Json -Compress)' }
            @{ from = 'turns=$([int]$r.num_turns), duration=$([math]::Round([double]$r.duration_ms/60000,1))min, usage: $usage'; to = 'turns=$($r.num_turns), duration=$([math]::Round($r.duration_ms/60000,1))min, usage=$usage' }
        ) }
        if ($null -eq $uMutant) { Write-Host 'FAIL: the usage mutant changed nothing -- its anchor no longer appears in the template' -ForegroundColor Red; $failures++ }
        else {
            $res = Invoke-UsageStep $uMutant ($usageCases[4].json) 'mutant'
            if ($res.posted.Contains('audit-run') -or $res.posted.Contains('sk-ant')) { Write-Host '  ok: the usage step with its casts removed posts the result''s text, so the rows above go red on it' -ForegroundColor DarkGray }
            else { Write-Host "FAIL: the usage step with its casts removed still posted no text from the result -- the rows above prove nothing`n$($res.posted)" -ForegroundColor Red; $failures++ }
        }
    } finally { Remove-Item -LiteralPath $scratch.FullName -Recurse -Force }
}

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall weekly-pass step cases pass" -ForegroundColor Green
exit 0
