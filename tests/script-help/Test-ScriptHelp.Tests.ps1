<#
.SYNOPSIS
    Test that every bin/*.ps1 exposes its own comment-based help.
.DESCRIPTION
    A help block can silently stop belonging to its script, and nothing else notices: the script
    still runs, and every other gate stays green. It has happened for two unrelated reasons -- a
    help line whose first word is an unknown keyword makes PowerShell drop the whole block, and
    too few blank lines between the block and a function below it hand the block to the
    function. So every bin/*.ps1 is asked for its help description, with no exception list, and
    each script is its own assertion, so the failure names the file that lost its help.

    Each script is resolved by its PATH, never by command name: a library whose function shares
    the file's base name, or a copy of the script on PATH, makes Get-Help by name answer for
    another command or for two. Get-Help reads even a path as a wildcard pattern, so the path is
    escaped first: under a checkout directory named `x[1]`, the raw path matches `x1` instead and
    answers with a sibling's help. A detached block makes Get-Help answer with the syntax it
    generates, as a plain string with no description property, so the read is null-safe and the
    case reports a failed assertion rather than throwing.

    The suite enumerates bin/ itself, and finding no script there is a failure of its own: a
    guard over zero scripts would pass while asserting nothing.

    A vendored tree (bin/Vendor-Ouro.ps1) copies the scripts flat into a directory the consumer
    may share with scripts of its own, a bin/ of its own included, so the flat gate script beside
    the suite says the tree is vendored unless the plugin's own copy of it, byte for byte, sits
    under that bin/ as well (a file the suite cannot read is not read as it), and there the suite
    prints a skip line and exits 0.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Two levels up is the plugin root (suite under tests/<area>/) or, once vendored, the scripts dir.
$Base = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
# The flat gate script says the tree is vendored, unless the plugin's own copy of it sits under
# bin/ beside it, byte for byte: a consumer's bin/ may hold a gate of that name, a directory of
# it, or a file this suite cannot read, and this suite reads none of those as the plugin's copy.
$flatGate = Join-Path $Base 'Test-DocsFreshness.ps1'
$binGate = Join-Path $Base 'bin/Test-DocsFreshness.ps1'
$binIsPlugin = try {
    (Test-Path -LiteralPath $flatGate -PathType Leaf) -and (Test-Path -LiteralPath $binGate -PathType Leaf) -and
        (Get-FileHash -LiteralPath $flatGate -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $binGate -Algorithm SHA256).Hash
}
catch { $false }
if ((Test-Path -LiteralPath $flatGate -PathType Leaf) -and -not $binIsPlugin) {
    Write-Host 'skip: the gate scripts are flat beside the suite (vendored layout); the guard covers the plugin''s bin/' -ForegroundColor DarkGray
    exit 0
}
$Bin = Join-Path $Base 'bin'

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -eq $Actual) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "  FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}

$Glob = '*.ps1'
# .NET, not Get-ChildItem: on Linux, -LiteralPath still globs a '*' or '?' in the directory's own
# path, and would enumerate a sibling. Case-insensitive, as -Filter matched on every platform.
$anyCase = [System.IO.EnumerationOptions]@{ MatchCasing = 'CaseInsensitive' }
$files = @(if ([System.IO.Directory]::Exists($Bin)) {
    [System.IO.Directory]::GetFiles($Bin, $Glob, $anyCase) | Sort-Object |
        ForEach-Object { [System.IO.FileInfo]::new($_) }
})
Assert-Equal $true ($files.Count -gt 0) "bin/$Glob under $Base matches at least one script"

foreach ($file in $files) {
    # Get-Help throws on a file that does not parse, and on a path it cannot read as a pattern
    # even escaped (an unclosed '['): that is this script's failure, with why, not the suite's.
    # For a parse failure Get-Help's own message says only that no help was found, so the
    # parser's first error is the reason given instead.
    $why = ''
    $help = try { Get-Help -Full ([WildcardPattern]::Escape($file.FullName)) }
            catch {
                $errs = $null
                $null = [System.Management.Automation.Language.Parser]::ParseFile(
                    $file.FullName, [ref]$null, [ref]$errs)
                $why = " ($(if ($errs) { $errs[0].Message } else { $_.Exception.Message }))"; ''
            }
    $text = if ($help -and $help.PSObject.Properties['description']) {
        ($help.description | Out-String).Trim()
    } else { '' }
    Assert-Equal $true ($text.Length -gt 0) "bin/$($file.Name) exposes its help description$why"
}

if ($failures -gt 0) { Write-Host "`n$failures assertion(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nall script-help cases pass" -ForegroundColor Green
exit 0
