<#
.SYNOPSIS
    The fixture the land-scripts suites share: a gh stand-in, scratch repositories, assertions.
.DESCRIPTION
    Dot-sourced by each *.Tests.ps1 beside it; not a suite itself. Each suite dot-sources the script
    under test too and calls its functions per row, in this process (a cold pwsh start per row is
    what the slow suites pay for); a few rows per suite start `pwsh -NoProfile -File <script>` as a
    child to pin the entry point: its parameters, exit codes and STOP lines.

    gh is a stand-in first on PATH, a pwsh script that answers every call from a JSON table the
    suite writes per case, keyed by the call's signature -- `pr view 7`, `pr checks 7`,
    `pr merge 7`, `pr list --base <branch>`, `run list --commit <sha>`, `run watch <id>`,
    `api graphql <pr>` -- each row {Exit, Out} or {Seq: [rows]}, where the Nth call takes the Nth
    row and the last row repeats. Every call is logged, one line, so a row can assert what the
    script asked and in what order. A call the table does not name exits 1 and names itself, so no
    query goes unseen. Nothing touches a real remote or needs a credential.

    Scratch repositories live under TEMP, each a main checkout whose origin is a local bare
    repository with the binding committed. Git's global and system configuration are replaced by
    an empty file, and the committer identity is fixed, for the whole suite.
#>

$Utf8 = [System.Text.UTF8Encoding]::new($false)
$Pwsh = (Get-Process -Id $PID).Path
# Two levels up is the plugin root (suite under tests/<area>/) or, once vendored, the scripts dir.
$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
function Find-Script([string]$Name) {
    $found = @((Join-Path $Base $Name), (Join-Path $Base "bin/$Name")) |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $found) { throw "$Name not found under $Base" }
    $found
}

$failures = 0
# Ordinal, as git compares names: -eq compares by culture.
function Assert-Equal($Expected, $Actual, $What) {
    if ([string]::Equals("$Expected", "$Actual", [StringComparison]::Ordinal)) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}
function Assert-Match($Pattern, $Text, $What) {
    if ("$Text" -match $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- no match for '$Pattern' in:`n$Text" -ForegroundColor Red; $script:failures++ }
}
function Assert-NoMatch($Pattern, $Text, $What) {
    if ("$Text" -notmatch $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- unexpected match for '$Pattern' in:`n$Text" -ForegroundColor Red; $script:failures++ }
}
# One of the lines is exactly this, or (with -Prefix) starts with it.
function Assert-Line($Lines, [string]$Line, $What, [switch]$Prefix) {
    $hit = @(@($Lines) | Where-Object {
            if ($Prefix) { "$_".StartsWith($Line, [StringComparison]::Ordinal) }
            else { [string]::Equals("$_", $Line, [StringComparison]::Ordinal) } })
    if ($hit.Count -gt 0) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- no line '$Line$(if ($Prefix) { '...' })' in:`n$($Lines -join "`n")" -ForegroundColor Red; $script:failures++ }
}

# New-Item without -Force: it fails where the path exists, so this can never resolve to a
# directory that was not made here.
$ScratchItem = New-Item -ItemType Directory -Path (Join-Path ([System.IO.Path]::GetTempPath()) ('land-scripts-' + [guid]::NewGuid().ToString('N').Substring(0, 8)))
$Scratch = $ScratchItem.FullName
$StubDir = (New-Item -ItemType Directory -Path (Join-Path $Scratch 'stub')).FullName
$StateDir = (New-Item -ItemType Directory -Path (Join-Path $Scratch 'state')).FullName
$TableFile = Join-Path $Scratch 'gh-table.json'
$LogFile = Join-Path $Scratch 'gh.log'
$stub = @'
# gh stand-in: answers from the table in STUB_GH_TABLE, keyed by the call's signature; a row is
# {Exit, Out} or {Seq: [rows]}, the Nth call taking the Nth row and the last repeating; every call
# is logged to STUB_GH_LOG. A call the table does not name exits 1 and names itself.
$a = @($args)
[System.IO.File]::AppendAllText($env:STUB_GH_LOG, (($a -join ' ') -replace "`r?`n", ' ') + "`n")
$table = Get-Content -LiteralPath $env:STUB_GH_TABLE -Raw | ConvertFrom-Json -AsHashtable
function Get-Arg([string]$Flag) { $i = [array]::IndexOf($a, $Flag); if ($i -ge 0 -and $i + 1 -lt $a.Count) { "$($a[$i + 1])" } }
$verb = "$($a[0]) $($a[1])"
$key = switch ($verb) {
    'pr view'     { "pr view $($a[2])" }
    'pr checks'   { "pr checks $($a[2])" }
    'pr merge'    { "pr merge $($a[2])" }
    'pr list'     { "pr list --base $(Get-Arg '--base')" }
    'run list'    { "run list --commit $(Get-Arg '--commit')" }
    'run watch'   { "run watch $($a[2])" }
    'pr close'    { "pr close $($a[2])" }
    'api graphql' { $n = Get-Arg '-F'; if ($n) { "api graphql $($n -replace '^number=', '')" } else { 'api graphql' } }
    default       { $verb }
}
if (-not $table.ContainsKey($key)) { "stub gh: no answer for: $($a -join ' ')"; exit 1 }
$row = $table[$key]
if ($row.ContainsKey('Seq')) {
    $counter = Join-Path $env:STUB_GH_STATE (($key -replace '[^A-Za-z0-9]', '_') + '.n')
    $n = if (Test-Path -LiteralPath $counter) { [int](Get-Content -LiteralPath $counter -Raw) } else { 0 }
    [System.IO.File]::WriteAllText($counter, "$($n + 1)")
    $row = $row.Seq[[Math]::Min($n, $row.Seq.Count - 1)]
}
# A row's Run is a command the stand-in runs first: the side effect GitHub would have, such as
# origin's default branch moving when a stubbed `pr merge` lands.
if ($row.ContainsKey('Run') -and "$($row.Run)") { Invoke-Expression $row.Run | Out-Null }
if ($row.ContainsKey('Out') -and "$($row.Out)") { "$($row.Out)" }
exit $row.Exit
'@
[System.IO.File]::WriteAllText((Join-Path $StubDir 'gh.ps1'), $stub, $Utf8)

function Set-EnvVar([string]$Name, $Value) {
    if ($null -eq $Value) { Remove-Item -LiteralPath "Env:$Name" -ErrorAction Ignore }
    else { [Environment]::SetEnvironmentVariable($Name, $Value) }
}
$SavedEnv = @{}
foreach ($k in 'PATH', 'STUB_GH_TABLE', 'STUB_GH_LOG', 'STUB_GH_STATE', 'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_NOSYSTEM',
    'GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL') {
    $SavedEnv[$k] = [Environment]::GetEnvironmentVariable($k)
}
$SavedEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = $Utf8
$env:PATH = $StubDir + [System.IO.Path]::PathSeparator + $env:PATH
$env:STUB_GH_TABLE = $TableFile; $env:STUB_GH_LOG = $LogFile; $env:STUB_GH_STATE = $StateDir
$emptyConfig = Join-Path $Scratch 'gitconfig'
[System.IO.File]::WriteAllText($emptyConfig, '', $Utf8)
Set-EnvVar GIT_CONFIG_GLOBAL $emptyConfig; Set-EnvVar GIT_CONFIG_NOSYSTEM '1'
Set-EnvVar GIT_AUTHOR_NAME t; Set-EnvVar GIT_AUTHOR_EMAIL t@t
Set-EnvVar GIT_COMMITTER_NAME t; Set-EnvVar GIT_COMMITTER_EMAIL t@t
# The stand-in is only a stand-in if PowerShell picks it over the real gh.
$resolvedGh = (Get-Command gh | Select-Object -First 1).Source
if ($resolvedGh -ne (Join-Path $StubDir 'gh.ps1')) { throw "the gh stand-in does not shadow gh on PATH (resolved '$resolvedGh')" }

function Json($Object) { ConvertTo-Json -InputObject $Object -Compress -Depth 20 }
function Answer($Object, [int]$Exit = 0) { @{ Exit = $Exit; Out = (Json $Object) } }
function Fail([string]$Text = 'boom: the stand-in refused') { @{ Exit = 1; Out = $Text } }
function Seq([object[]]$Rows) { @{ Seq = $Rows } }
# A new table, or Reset-Gh, starts every Seq over; a step clears the log alone, so a drill of
# several calls over one table reads its sequences across the calls.
function Clear-GhLog { [System.IO.File]::WriteAllText($LogFile, '', $Utf8) }
function Reset-Gh {
    Clear-GhLog
    foreach ($f in [System.IO.Directory]::GetFiles($StateDir)) { [System.IO.File]::Delete($f) }
}
function Set-Gh([hashtable]$Table) {
    [System.IO.File]::WriteAllText($TableFile, (ConvertTo-Json -InputObject $Table -Depth 20), $Utf8)
    Reset-Gh
}
function Get-GhLog { @([System.IO.File]::ReadAllLines($LogFile) | Where-Object { $_ }) }

function G {
    $dir, $rest = $args
    $out = git -C $dir @rest 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git -C $dir $($rest -join ' ') failed: $out" }
    @($out | Where-Object { $_ -is [string] })
}
function New-Commit([string]$Dir, [string]$File, [string]$Author = 't <t@t>') {
    [System.IO.File]::WriteAllText((Join-Path $Dir $File), "$File $([guid]::NewGuid())`n", $Utf8)
    $null = G $Dir add -- $File
    $null = G $Dir commit -q --author=$Author -m "change $File"
    @(G $Dir rev-parse HEAD)[0]
}
# A main checkout on master with the binding committed, and a bare origin holding master.
function New-World([string]$Name, [string]$Landing = '') {
    $root = Join-Path $Scratch $Name
    $w = @{ Origin = Join-Path $root 'origin.git'; Main = Join-Path $root 'main' }
    $null = git init -q --bare -b master $w.Origin 2>&1
    $null = git init -q -b master $w.Main 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git init in $root failed" }
    New-Item -ItemType Directory -Force -Path (Join-Path $w.Main '.claude') | Out-Null
    $landing = if ($Landing) { "landing = `"$Landing`"`n" } else { '' }
    $binding = @"
schema = 1
[repo]
slug = "o/n"
default_branch = "master"
protected_branch_prefixes = ["release/"]
[ship]
policy = "stop-at-pr"
review = "none"
$landing[owner]
ruling_approvers = ["o"]
"@
    [System.IO.File]::WriteAllText((Join-Path $w.Main '.claude/ouro.toml'), $binding, $Utf8)
    [System.IO.File]::WriteAllText((Join-Path $w.Main '.gitignore'), ".claude/worktrees/`n", $Utf8)
    $null = G $w.Main add -A
    $null = G $w.Main commit -q -m init
    $null = G $w.Main remote add origin $w.Origin
    $null = G $w.Main push -q origin master
    return $w
}

# A function of the script under test, called here; what it printed, the message it threw (or
# ''), and the gh calls it made.
function Invoke-Step([scriptblock]$Step) {
    Clear-GhLog
    $lines = [System.Collections.Generic.List[string]]::new()
    $stop = ''
    try { & $Step | ForEach-Object { $lines.Add("$_") } } catch { $stop = $_.Exception.Message }
    foreach ($l in $lines) { Write-Host "    | $l" -ForegroundColor DarkGray }
    if ($stop) { Write-Host "    | STOP: $stop" -ForegroundColor DarkGray }
    [pscustomobject]@{ Lines = @($lines); Stop = $stop; Gh = @(Get-GhLog) }
}
# The script as a person runs it: a pwsh child from $From, its exit code and lines.
function Invoke-Child([string]$Script, [string[]]$Arguments, [string]$From) {
    Clear-GhLog
    Push-Location -LiteralPath $From
    try {
        $lines = @(& $Pwsh -NoProfile -File $Script @Arguments 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    }
    finally { Pop-Location }
    foreach ($l in $lines) { Write-Host "    | $l" -ForegroundColor DarkGray }
    [pscustomobject]@{ Code = $code; Lines = @($lines); Gh = @(Get-GhLog) }
}

function Complete-Suite([string]$Name) {
    foreach ($k in $SavedEnv.Keys) { Set-EnvVar $k $SavedEnv[$k] }
    [Console]::OutputEncoding = $SavedEncoding
    Remove-Item -LiteralPath $ScratchItem.FullName -Recurse -Force -ErrorAction Continue
    if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
    Write-Host "`nall $Name cases pass" -ForegroundColor Green
    exit 0
}
