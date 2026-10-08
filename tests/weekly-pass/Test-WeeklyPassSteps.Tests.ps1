<#
.SYNOPSIS
    Test that the weekly pass's intake grading step stays uncredentialed and tool-bound, that
    the step which applies its manifest runs no model, that the step which appends the drift
    claim markers runs no model, checks the tree first and follows the drift session, and that
    the job names its repository.
.DESCRIPTION
    The property this pins is a security boundary, and it lives in prose nowhere else: the
    grading session reads newly-filed issue bodies, which the contract calls untrusted, so it
    must hold no token and no tool that reaches the tracker. Every part of that is one edit away
    from being undone -- a `gh` grant added back for convenience, a `git log` added back for
    history, the job token restored on the step, a checkout left persisting its credential -- and
    each of those edits looks entirely ordinary in a diff. Nothing else in tests/ reads
    templates/weekly-pass.yml.

    The template is parsed as TEXT, by step block: no YAML parser ships with pwsh, and the
    Action-path suite reads its file the same way. A step block runs from its `- name:`/`- uses:`
    line to the next one at the same indent. The two intake steps are found by what they RUN --
    the grading step is the one invoking the intake skill, the applying step the one invoking the
    applier -- never by their names, so renaming a step does not quietly stop testing it.

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
$ExpectedGrant = @('Skill', 'Read', 'Grep', 'Glob', 'Agent', 'Task', 'Edit(TestResults/**)', 'Bash(git rev-parse:*)')

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
# The drift audit legitimately holds the job token and writes through `gh issue`: it grades the
# repo's own docs, not newly-filed issue text, and its ledger is a write by design. Enumerated for
# the same reason as the grading grant -- so widening THAT one is a deliberate edit here too,
# rather than a longer line in a diff. It holds no create and no reopen: the harvest step resolves
# or files the ledger before the session starts, and hands it the number.
$ExpectedDriftGrant = @('Skill', 'Read', 'Grep', 'Glob', 'Agent', 'Task',
    'Bash(git log:*)', 'Bash(git diff:*)', 'Bash(git show:*)', 'Bash(git ls-files:*)',
    'Bash(git grep:*)', 'Bash(git cat-file:*)', 'Bash(git rev-parse:*)', 'Bash(git blame:*)',
    'Bash(gh issue list *)', 'Bash(gh issue view *)', 'Bash(gh issue comment *)',
    'Bash(gh issue edit *)')

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
function Get-TreeCheckFindings([string]$Code, [string]$Run, [string]$RunText, [string]$Name) {
    $f = @()
    $runAt = [regex]::Match($Code, '(?m)^\s*' + $Run + '\s*$')
    if (-not $runAt.Success) { $f += "the $Name step does not run $RunText as a command of its own" }
    $rootHalf = [regex]::Match($Code, '(?m)^\s*\$dirty\s*=\s*@\(git status --porcelain --untracked-files=no\)')
    $pluginHalf = [regex]::Match($Code, '(?m)^\s*\$dirty\s*=.*@\(git -C ouro status --porcelain --untracked-files=all\)\s*$')
    $oneLine = [regex]::Match($Code, '(?m)^\s*\$dirty\s*=\s*@\(git status --porcelain --untracked-files=no\)\s*\+\s*@\(git -C ouro status --porcelain --untracked-files=all\)\s*$')
    $throwAt = [regex]::Match($Code, '(?m)^\s*if\s*\(\$dirty\)\s*\{\s*throw\s')
    if (-not $rootHalf.Success -and -not $pluginHalf.Success) {
        $f += "the $Name step runs a plugin script out of a tree it never checked, though the grading session before it holds Edit"
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
    # The drift audit's posture is the opposite of the grading step's, and stated rather than
    # assumed: it may hold the job token and write through `gh issue`, exactly this far.
    $drift = @($modelBlocks | Where-Object { (Remove-Comments $_) -match '/ouro:drift' })
    if ($drift.Count -eq 1) {
        $dg = Get-Grant (Remove-Comments $drift[0])
        if ($null -eq $dg) { $findings.Add('the drift audit passes no --allowedTools, so it runs with the harness default') }
        else {
            $dMissing = @($ExpectedDriftGrant | Where-Object { $dg -notcontains $_ })
            $dExtra = @($dg | Where-Object { $ExpectedDriftGrant -notcontains $_ })
            if ($dMissing -or $dExtra) {
                $findings.Add("the drift audit's grant is not the enumerated set (missing: $($dMissing -join ' ') / extra: $($dExtra -join ' '))")
            }
        }
        # The session is handed its ledger's number, expanded by the step's own shell, since the
        # grant holds no create or reopen and the session reads no environment variable.
        # Inside the double-quoted prompt only: a single-quoted one hands the session the variable's
        # name. Case-sensitive, since only Windows folds an environment variable's case.
        if ((Remove-Comments $drift[0]) -cnotmatch '"/ouro:drift [^"]*--issue (\$env:DRIFT_ISSUE_NUMBER|\$\{env:DRIFT_ISSUE_NUMBER\}|\$\(\$env:DRIFT_ISSUE_NUMBER\))[ "]') {
            $findings.Add("the drift audit's prompt carries no --issue `$env:DRIFT_ISSUE_NUMBER, so the session is not handed its ledger")
        }
    }

    # --- the job env ----------------------------------------------------------------------------
    # The steps that shell out to gh name the binding's repository themselves (Get-RepoSlug.ps1
    # runs in the same process, before each of them), so what this env backstops is the drift
    # session's own gh calls, whose `-R` its skill mandates and this file cannot spell. Read from
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
            $findings.Add('the job running the weekly pass does not set GH_REPO to ${{ github.repository }} in its own env, so a drift-session gh call that omits -R reads and writes whatever repository gh picks from the clone')
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

    # --- the grading step's tool grant -------------------------------------------------------
    if ($gradeCode -match '--allowedTools\s+"([^"]*)"') {
        $granted = @($Matches[1] -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        foreach ($g in $granted) {
            if ($g -match '(?i)\bgh\b' -or $g -match '(?i)^Bash\(\s*gh[\s)(]') {
                $findings.Add("the grading step grants a gh tool: $g")
            }
            elseif ($g -match '^Bash\((.+)\)$') {
                $cmd = $Matches[1].Trim()
                if ($cmd -match '^git\s+([a-z][a-z-]*)') {
                    if ($ReadOnlyGit -notcontains $Matches[1]) {
                        $findings.Add("the grading step grants a git tool outside the read-only set: $g")
                    }
                }
                else { $findings.Add("the grading step grants a Bash command that is not a git read: $g") }
            }
            elseif ($g -eq 'Bash') { $findings.Add('the grading step grants unrestricted Bash') }
        }
        $missing = @($ExpectedGrant | Where-Object { $granted -notcontains $_ })
        $extra = @($granted | Where-Object { $ExpectedGrant -notcontains $_ })
        if ($missing -or $extra) {
            $findings.Add("the grading step's grant is not the enumerated read-only set (missing: $($missing -join ' ') / extra: $($extra -join ' '))")
        }
    }
    else { $findings.Add('the grading step passes no --allowedTools, so it runs with the harness default') }

    # --- the grading step's credentials -------------------------------------------------------
    # A step-level env of the same name is how a job-level token is cleared for one step; an empty
    # value is the clearing. Anything else assigned to a *TOKEN* name, and any secrets. reference,
    # is a credential handed to the session that reads untrusted text.
    # Only the two explicit empty-string spellings count as cleared, deliberately: a bare
    # `GH_TOKEN:`, a `~` and a `null` are read as NOT cleared and turn this red. Whether Actions
    # hands a null env value to the step as an empty string or leaves the job's value standing was
    # not measured, so the check fails closed -- a false red on an odd spelling is cheap, a green
    # on a step that still holds the token is not.
    if ($gradeCode -notmatch "(?m)^\s*GH_TOKEN:\s*(''|"""")\s*$") {
        $findings.Add('the grading step does not clear GH_TOKEN, so it inherits the job token')
    }
    foreach ($line in ($gradeCode -split "`r?`n")) {
        if ($line -match '(?i)^\s*([A-Za-z_][A-Za-z0-9_]*TOKEN[A-Za-z0-9_]*)\s*:\s*(.+)$') {
            $name, $value = $Matches[1], $Matches[2].Trim()
            if ($value -ne "''" -and $value -ne '""') { $findings.Add("the grading step carries a credential: $name is set to $value") }
        }
        if ($line -match 'secrets\.') { $findings.Add("the grading step reads a secret: $($line.Trim())") }
    }

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
        $driftAt = @(0..($blocks.Count - 1) | Where-Object { $blocks[$_] -match '/ouro:drift' })
        $claimsAt = @(0..($blocks.Count - 1) | Where-Object { $blocks[$_] -eq $claims[0] })
        if ($driftAt.Count -ne 1 -or $claimsAt[0] -lt $driftAt[0]) {
            $findings.Add('the claims step does not come after the drift session')
        }
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
$ClaimsStepBlock = if ($Text -match '(?ms)(      # The drift session posts each audited doc.*?\r?\n\r?\n)(?=      # Target selection is deterministic here too)') { $Matches[1] } else { '' }
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
        from = '--body-file $file'; to = '--body $file'
        expect = 'the claims step does not post each body with gh issue comment --body-file' }
    @{ what = 'the drift audit grant widened with a pull-request write'
        from = 'Bash(gh issue edit *)"'; to = 'Bash(gh issue edit *),Bash(gh pr merge *)"'
        expect = "the drift audit's grant is not the enumerated set" }
    @{ what = 'the create verb put back in the drift audit grant'
        from = 'Bash(gh issue edit *)"'; to = 'Bash(gh issue edit *),Bash(gh issue create *)"'
        expect = "the drift audit's grant is not the enumerated set" }
    @{ what = 'the reopen verb put back in the drift audit grant'
        from = 'Bash(gh issue edit *)"'; to = 'Bash(gh issue edit *),Bash(gh issue reopen *)"'
        expect = "the drift audit's grant is not the enumerated set" }
    @{ what = 'the ledger number dropped from the drift audit prompt'
        from = ' --issue $env:DRIFT_ISSUE_NUMBER'; to = ''
        expect = "the drift audit's prompt carries no --issue" }
    @{ what = 'the drift audit prompt single-quoted, so the session gets the variable name'
        from = 'claude -p "/ouro:drift --targets TestResults\drift-targets.json --issue $env:DRIFT_ISSUE_NUMBER --ci"'
        to = "claude -p '/ouro:drift --targets TestResults\drift-targets.json --issue `$env:DRIFT_ISSUE_NUMBER --ci'"
        expect = "the drift audit's prompt carries no --issue" }
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
        from = "GH_TOKEN: ''"; to = 'GH_TOKEN: ${{ github.token }}'
        expect = 'does not clear GH_TOKEN' }
    @{ what = 'a second credential handed to the grading step'
        from = "          GH_TOKEN: ''"; to = "          GH_TOKEN: ''`n          OURO_READ_TOKEN: `${{ secrets.OURO_READ_TOKEN }}"
        expect = 'carries a credential' }
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
        from = ',Edit(TestResults/**)'; to = ''
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
    @{ what = 'the ledger number spelled ${env:...}'
        from = '--issue $env:DRIFT_ISSUE_NUMBER --ci'; to = '--issue ${env:DRIFT_ISSUE_NUMBER} --ci' }
    @{ what = 'the ledger number spelled $($env:...)'
        from = '--issue $env:DRIFT_ISSUE_NUMBER --ci'; to = '--issue $($env:DRIFT_ISSUE_NUMBER) --ci' }
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
    return 'a ledger comment'
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
        $hCases = @(
            @{ what = 'with no ledger, the pass files one with umbrella alone and exports its number'; resolved = 0; out = 'https://github.com/o/r/issues/41'; err = ''; rc = 0; num = '41'; msg = ''; create = $true }
            @{ what = 'with an open ledger, nothing is filed and its number is exported'; resolved = 7; out = ''; err = ''; rc = 0; num = '7'; msg = ''; create = $false; newest = '2026-10-03T11:22:33Z' }
            @{ what = 'an open ledger with no comments exports no createdAt'; resolved = 7; out = ''; err = ''; rc = 0; num = '7'; msg = ''; create = $false; newest = '' }
            @{ what = 'a newest createdAt that is no timestamp throws before the number is exported'; resolved = 7; out = ''; err = ''; rc = 0; num = ''; msg = 'is not a timestamp'; create = $false; newest = 'yesterday' }
            @{ what = 'a failed createdAt read throws before the number is exported'; resolved = 7; out = ''; err = ''; rc = 0; num = ''; msg = 'createdAt on #7 failed (exit 1)'; create = $false; newest = ''; newestRc = 1 }
            @{ what = 'a URL on stderr does not win the created ledger''s number'; resolved = 0; out = 'https://github.com/o/r/issues/41'; err = 'Warning: see https://github.com/cli/cli/issues/999'; rc = 0; num = '41'; msg = ''; create = $true }
            @{ what = 'a failed create throws and exports no number'; resolved = 0; out = 'HTTP 403'; err = ''; rc = 1; num = ''; msg = 'failed (exit 1)'; create = $true }
            @{ what = 'a create that prints no issue URL throws and exports no number'; resolved = 0; out = 'something else'; err = ''; rc = 0; num = ''; msg = 'printed no issue URL'; create = $true }
        )
        $i = 0
        foreach ($c in $hCases) {
            $i++
            if (-not $c.ContainsKey('newest')) { $c.newest = '' }
            if (-not $c.ContainsKey('newestRc')) { $c.newestRc = 0 }
            $dir = New-Item -ItemType Directory -Path (Join-Path $scratch.FullName "case$i")
            New-Item -ItemType Directory -Path (Join-Path $dir.FullName 'ouro/bin') | Out-Null
            Set-Content -LiteralPath (Join-Path $dir.FullName 'ouro/bin/Get-RollingIssue.ps1') -Value 'function Get-RollingIssueNumber { param($Title, $Key) return [int]$env:HARVEST_RESOLVED }'
            $script = Join-Path $dir.FullName 'harvest.ps1'
            Set-Content -LiteralPath $script -Value ($prelude + "`n" + $runCode) -Encoding utf8
            $envFile = Join-Path $dir.FullName 'github_env'; $ghLog = Join-Path $dir.FullName 'gh.log'
            New-Item -ItemType File -Path $envFile, $ghLog | Out-Null
            $saved = @{}
            $vars = @{ HARVEST_DIR = $dir.FullName; HARVEST_GH_LOG = $ghLog; HARVEST_RESOLVED = "$($c.resolved)"; HARVEST_OUT = $c.out; HARVEST_ERR = $c.err
                       HARVEST_RC = "$($c.rc)"; HARVEST_NEWEST = "$($c.newest)"; HARVEST_NEWEST_RC = "$($c.newestRc)"; GITHUB_ENV = $envFile; DRIFT_ISSUE_TITLE = 'Docs drift audit' }
            foreach ($k in $vars.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $vars[$k]) }
            try { $childOut = (& pwsh -NoProfile -File $script 2>&1 | Out-String); $childRc = $LASTEXITCODE }
            finally { foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) } }
            $exportedLines = @(Get-Content -LiteralPath $envFile)
            $exported = (@($exportedLines | Where-Object { $_ -like 'DRIFT_ISSUE_NUMBER=*' })) -join "`n"
            $since = (@($exportedLines | Where-Object { $_ -like 'DRIFT_CLAIMS_SINCE=*' })) -join "`n"
            $wantSince = if ($c.newest -match '^\d{4}-') { "DRIFT_CLAIMS_SINCE=$($c.newest)" } else { '' }
            # The ledger file stays the comment bodies the stub's first call returned, whatever the export does.
            $ledger = "$(Get-Content -LiteralPath (Join-Path $dir.FullName 'TestResults\audit-ledger.txt') -Raw -ErrorAction Ignore)".Trim()
            $wantLedger = if ($c.resolved) { 'a ledger comment' } else { '' }
            $log = "$(Get-Content -LiteralPath $ghLog -Raw)"
            $ok = if ($c.num) { $childRc -eq 0 -and $exported -ceq "DRIFT_ISSUE_NUMBER=$($c.num)" } else { $childRc -ne 0 -and $exported -eq '' -and $childOut.Contains($c.msg) }
            if ($c.num) { $ok = $ok -and $since -ceq $wantSince -and $ledger -ceq $wantLedger }
            if ($c.create) { $ok = $ok -and $log -match 'gh issue create .*--label umbrella --body' -and $log -cnotmatch $extraLabel }
            else { $ok = $ok -and $log -notmatch 'gh issue create' }
            if ($ok) { Write-Host "  ok: the harvest step, run: $($c.what)" -ForegroundColor DarkGray }
            else { Write-Host "FAIL: the harvest step, run: $($c.what) -- exit $childRc, exported '$exported', since '$since', ledger '$ledger'`n$childOut" -ForegroundColor Red; $failures++ }
        }
    } finally { Remove-Item -LiteralPath $scratch.FullName -Recurse -Force }
}

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall weekly-pass step cases pass" -ForegroundColor Green
exit 0
