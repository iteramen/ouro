<#
.SYNOPSIS
    Fixture suite for the hooks the plugin ships: every hook hooks/hooks.json registers runs once
    per recorded event under tests/hooks/<hook>/, and its exit code is compared with the
    fixture's decision.
.DESCRIPTION
    Reads hooks/hooks.json the way Claude Code's plugin loader does, with ${CLAUDE_PLUGIN_ROOT}
    standing for the plugin root, and pins the surface first: .claude-plugin/plugin.json names the
    file, it holds at least one PreToolUse entry, every entry is a command with a timeout, and the
    script each command names exists. Then each hook runs once per fixture in
    tests/hooks/<hook>/, <hook> being the script's file name without its extension, the event
    piped to it on standard input as Claude Code sends it. A fixture is one JSON file:

      decision   allow or deny: exit 0 or exit 2. Any other exit fails the row by its code, since
                 Claude Code lets the call run on every exit but 2, and on 127 or a timeout.
      event      the PreToolUse payload, sent verbatim.
      reason     optional: a regex the refusal's stderr must match.
      env        optional: variables set for that run, a null removing one.
      only       optional: a regex over linux, windows or darwin; elsewhere the row prints a skip
                 line, for a guard whose read (a process's directory) one platform lacks.
      repo       optional: {"staged": [...], "unstaged": [...]} builds a scratch repository under
                 TEMP with those files committed, then each staged one changed and added, each
                 unstaged one changed in the worktree only; a path in both is staged then changed
                 again. ${REPO} in the file is its path.
      spawn      optional: {"cwd": <dir>} starts a sleeping process in that directory, created
                 first, for the run; ${PID} in the file is its PID.

    ${FIXTURES} anywhere in the file is the fixture directory, and ${TEMP} the temporary
    directory, each with forward slashes. A hook with no fixture that allows and none that denies
    fails the suite: a guard is proven with one command it passes and one it stops. Mutation
    table:

      rule                                             | case that fails without it
      -------------------------------------------------|------------------------------------
      plugin.json names the registry                   | the manifest row
      a hook with no fixture directory is a failure    | a registered hook with no directory
      allow is exit 0 and deny is exit 2, nothing else | every fixture row
      stderr carries the reason                        | the deny rows with a reason
      a hook needs an allow and a deny                 | a directory holding one kind only

    A vendored tree carries no plugin root and no hooks/, so there the suite prints a skip line
    and exits 0.
#>
$ErrorActionPreference = 'Stop'

# Two levels up is the plugin root (suite under tests/hooks/) or, once vendored, the scripts dir.
$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Registry = Join-Path $Base 'hooks/hooks.json'
if (-not (Test-Path -LiteralPath $Registry)) {
    if (Test-Path -LiteralPath (Join-Path $Base 'Vendor-Ouro.ps1')) {
        Write-Host 'skip: no hooks/ beside the flat scripts (vendored layout); the hooks ship with the plugin' -ForegroundColor DarkGray
        exit 0
    }
    throw "hooks/hooks.json not found under $Base"
}
$BaseSlash = $Base -replace '\\', '/'

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ("$Expected" -eq "$Actual") { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}
function Assert-Match($Pattern, $Output, $What) {
    if ($Output -match $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- no match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}

# --- the surface -----------------------------------------------------------------------------
$manifest = Get-Content -LiteralPath (Join-Path $Base '.claude-plugin/plugin.json') -Raw -Encoding utf8 | ConvertFrom-Json
Assert-Equal './hooks/hooks.json' $manifest.hooks 'plugin.json names hooks/hooks.json'
$reg = Get-Content -LiteralPath $Registry -Raw -Encoding utf8 | ConvertFrom-Json
Assert-Equal $true ([bool]$reg.hooks.PreToolUse -and @($reg.hooks.PreToolUse).Count -ge 1) 'the registry holds a PreToolUse entry'

$hooks = @()
foreach ($event in $reg.hooks.PSObject.Properties) {
    foreach ($group in @($event.Value)) {
        foreach ($h in @($group.hooks)) {
            Assert-Equal 'command' $h.type "$($event.Name) entry is a command hook"
            Assert-Equal $true ($null -ne $h.timeout -and $h.timeout -gt 0) "$($event.Name) entry `"$($h.command)`" sets a timeout"
            $cmd = $h.command.Replace('${CLAUDE_PLUGIN_ROOT}', $BaseSlash)
            $tokens = @([regex]::Matches($cmd, '"([^"]*)"|(\S+)') | ForEach-Object { if ($_.Groups[1].Success) { $_.Groups[1].Value } else { $_.Groups[2].Value } })
            $script = $tokens | Where-Object { $_ -match '\.(py|ps1|sh|js)$' } | Select-Object -First 1
            Assert-Equal $true ($null -ne $script -and (Test-Path -LiteralPath $script -PathType Leaf)) "the script `"$($h.command)`" names exists under the plugin root"
            if ($script) {
                $hooks += [pscustomobject]@{
                    Event = $event.Name; Command = $h.command; Exe = $tokens[0]; Args = @($tokens | Select-Object -Skip 1)
                    Name = [System.IO.Path]::GetFileNameWithoutExtension($script)
                }
            }
        }
    }
}

# --- each hook over its recorded events --------------------------------------------------------
function Invoke-Hook($Hook, [string]$Stdin, [hashtable]$Env) {
    $exe = (Get-Command $Hook.Exe -ErrorAction Stop).Source
    $psi = [System.Diagnostics.ProcessStartInfo]::new($exe)
    foreach ($a in $Hook.Args) { $psi.ArgumentList.Add($a) }
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    $psi.StandardInputEncoding = $utf8
    $psi.StandardOutputEncoding = $utf8
    $psi.StandardErrorEncoding = $utf8
    $psi.Environment['PYTHONDONTWRITEBYTECODE'] = '1'
    foreach ($k in $Env.Keys) {
        if ($null -eq $Env[$k]) { $psi.Environment.Remove($k) | Out-Null } else { $psi.Environment[$k] = [string]$Env[$k] }
    }
    $p = [System.Diagnostics.Process]::Start($psi)
    $p.StandardInput.Write($Stdin)
    $p.StandardInput.Close()
    $out = $p.StandardOutput.ReadToEndAsync()
    $err = $p.StandardError.ReadToEndAsync()
    $p.WaitForExit()
    [pscustomobject]@{ Code = $p.ExitCode; Out = $out.Result; Err = $err.Result }
}

# Every registered Python hook must refuse an unsupported runtime with the blocking exit.
foreach ($hook in $hooks) {
    $scriptPath = @($hook.Args | Where-Object { $_ -match '\.py$' })[0]
    if (-not $scriptPath) { continue }
    $scriptJson = ConvertTo-Json -Compress $scriptPath
    $scriptDirJson = ConvertTo-Json -Compress (Split-Path -Parent $scriptPath)
    $fake = "import runpy, sys; sys.path.insert(0, $scriptDirJson); sys.version_info = (3, 11, 2, 'final', 0); runpy.run_path($scriptJson, run_name='__main__')"
    $r = Invoke-Hook ([pscustomobject]@{ Exe = $hook.Exe; Args = @('-c', $fake) }) '' @{}
    Assert-Equal 2 $r.Code "$($hook.Name): Python 3.11 blocks"
    Assert-Match 'needs? Python 3\.12\+' $r.Err "$($hook.Name): names the runtime floor"
}

$platform = if ($IsWindows) { 'windows' } elseif ($IsMacOS) { 'darwin' } else { 'linux' }
$tempSlash = [System.IO.Path]::GetTempPath().TrimEnd('\', '/') -replace '\\', '/'
$python = (Get-Command python3 -ErrorAction Stop).Source

# A scratch repository with one committed file per name, then the staged and unstaged changes.
function New-FixtureRepo($Spec) {
    $repo = New-Item -ItemType Directory -Path (Join-Path ([System.IO.Path]::GetTempPath()) ('hooks-repo-' + [guid]::NewGuid()))
    $git = @('-C', $repo.FullName, '-c', 'user.name=t', '-c', 'user.email=t@invalid', '-c', 'commit.gpgsign=false', '-c', 'core.autocrlf=false')
    & git @git init -q | Out-Null
    $names = @(@($Spec.staged) + @($Spec.unstaged) + @($Spec.clean) | Where-Object { $_ } | Sort-Object -Unique)
    foreach ($n in $names) { [System.IO.File]::WriteAllText((Join-Path $repo.FullName $n), "base`n") }
    & git @git add -A | Out-Null
    & git @git commit -q -m base | Out-Null
    foreach ($n in @($Spec.staged) | Where-Object { $_ }) { [System.IO.File]::WriteAllText((Join-Path $repo.FullName $n), "staged`n"); & git @git add -- $n | Out-Null }
    foreach ($n in @($Spec.unstaged) | Where-Object { $_ }) { [System.IO.File]::AppendAllText((Join-Path $repo.FullName $n), "worktree`n") }
    $repo
}

foreach ($hook in $hooks) {
    $dir = Join-Path $PSScriptRoot $hook.Name
    $dirSlash = $dir -replace '\\', '/'
    $cases = @(if (Test-Path -LiteralPath $dir -PathType Container) { Get-ChildItem -LiteralPath $dir -Filter '*.json' | Sort-Object Name })
    Assert-Equal $true ($cases.Count -gt 0) "$($hook.Name): tests/hooks/$($hook.Name)/ holds a recorded event"
    $seen = @{}
    foreach ($case in $cases) {
        $raw = (Get-Content -LiteralPath $case.FullName -Raw -Encoding utf8).Replace('${FIXTURES}', $dirSlash).Replace('${TEMP}', $tempSlash)
        $fx = $raw | ConvertFrom-Json
        if ($fx.PSObject.Properties['only'] -and $platform -notmatch $fx.only) {
            Write-Host "  skip: $($hook.Name)/$($case.BaseName) runs on $($fx.only) only, not $platform" -ForegroundColor DarkGray
            continue
        }
        $repo = $null; $sleeper = $null
        try {
            if ($fx.PSObject.Properties['repo']) {
                $repo = New-FixtureRepo $fx.repo
                $raw = $raw.Replace('${REPO}', ($repo.FullName -replace '\\', '/'))
            }
            if ($fx.PSObject.Properties['spawn']) {
                $cwd = New-Item -ItemType Directory -Force -Path $fx.spawn.cwd
                # One argument with no space in it: a pwsh that re-splits the list on spaces would hand
                # python a broken program, and a sleeper that died is a PID no guard can read.
                $hidden = if ($IsWindows) { @{ WindowStyle = 'Hidden' } } else { @{} }
                $sleeper = Start-Process -FilePath $python -ArgumentList @('-c', "__import__('time').sleep(120)") -WorkingDirectory $cwd.FullName -PassThru @hidden
                Start-Sleep -Milliseconds 300
                if ($sleeper.HasExited) { throw "$($case.Name): the sleeper exited at once (exit $($sleeper.ExitCode)); the row has no process to read" }
                $raw = $raw.Replace('${PID}', [string]$sleeper.Id)
            }
            $fx = $raw | ConvertFrom-Json
            $env = @{}
            if ($fx.PSObject.Properties['env']) { foreach ($pp in $fx.env.PSObject.Properties) { $env[$pp.Name] = $pp.Value } }
            $want = switch ($fx.decision) { 'allow' { 0 } 'deny' { 2 } default { throw "$($case.Name): decision must be allow or deny" } }
            $seen[$fx.decision] = $true
            $r = Invoke-Hook $hook ($fx.event | ConvertTo-Json -Depth 20 -Compress) $env
            $what = "$($hook.Name)/$($case.BaseName): $($fx.decision) is exit $want"
            if ($r.Code -eq $want) { Write-Host "  ok: $what" -ForegroundColor DarkGray }
            else { Write-Host "  FAIL: $what -- got exit $($r.Code) (Claude Code lets the call run on any exit but 2)`n$($r.Err)$($r.Out)" -ForegroundColor Red; $failures++ }
            if ($fx.PSObject.Properties['reason']) { Assert-Match $fx.reason $r.Err "$($hook.Name)/$($case.BaseName): stderr names the reason" }
        }
        finally {
            if ($sleeper) { Stop-Process -Id $sleeper.Id -Force -ErrorAction SilentlyContinue }
            if ($repo) { Remove-Item -LiteralPath $repo.FullName -Recurse -Force }
        }
    }
    Assert-Equal $true ($seen['allow'] -and $seen['deny']) "$($hook.Name): one event it allows and one it refuses"
}

if ($failures) { Write-Host "`n$failures failure(s)" -ForegroundColor Red; exit 1 }
Write-Host "`nall hook fixtures pass" -ForegroundColor Green
exit 0
