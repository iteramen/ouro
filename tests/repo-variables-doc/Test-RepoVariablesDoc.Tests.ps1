<#
.SYNOPSIS
    Unit test for Test-RepoVariablesDoc.ps1's gh-failure and classification logic.
.DESCRIPTION
    Drives the script in-session so a `gh` function shadow stands in for the real
    CLI -- no live repo settings, no network. The regression case:
    a FAILED `gh variable list` must throw with the captured stderr, never be
    classified as an all-phantom listing. An empty *successful* listing staying a
    legitimate all-phantom signal is pinned by its own case, as is the
    -VariablesJson bypass never touching gh.
#>
$ErrorActionPreference = 'Stop'
# Two levels up is the plugin root (script under bin/) or, once vendored, the scripts dir itself.
$Base   = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Script = @((Join-Path $Base 'Test-RepoVariablesDoc.ps1'), (Join-Path $Base 'bin/Test-RepoVariablesDoc.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Script) { throw "Test-RepoVariablesDoc.ps1 not found under $Base" }

# Hand the script the fixture's absolute path, never one derived with GetRelativePath: where the
# work tree is reached through a junction `git rev-parse --show-toplevel` resolves through it and
# $PSScriptRoot does not, and across two roots GetRelativePath returns its second argument
# unchanged instead of throwing -- so the "repo-relative" path was silently absolute.
$DocAbs = Join-Path $PSScriptRoot 'variables-doc.fixture.md'

$failures = 0
function Assert-Match($Pattern, $Output, $What) {
    if ($Output -match $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- no match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}
function Assert-NoMatch($Pattern, $Output, $What) {
    if ($Output -notmatch $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- unexpected match for '$Pattern' in:`n$Output" -ForegroundColor Red; $script:failures++ }
}

# --- regression: gh fails -> throw with stderr, no classification ------------------
# A function shadow resolves before the native gh; it emits stderr the way a native command
# does under 2>&1 (an error record) and sets the exit code the fix must check. Every shadow here
# answers `repo view`, the call that names the repository (bin/Get-RepoSlug.ps1): in a repo with
# no binding it is the first call the gate makes, and a shadow that failed it would skip the gate
# rather than exercise the case under test.
function gh {
    if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
    Write-Error 'failed to get variables: HTTP 403: Resource not accessible by integration' -ErrorAction Continue
    $global:LASTEXITCODE = 1
}
$thrown = ''
$out    = ''
try { $out = (& $Script -DocPath $DocAbs 6>&1 | Out-String) } catch { $thrown = "$_" }
Assert-Match   'gh variable list failed \(exit 1\)' $thrown 'a failed gh variable list throws'
Assert-Match   'HTTP 403'                           $thrown 'the throw carries the captured stderr'
Assert-NoMatch 'phantom:'                           $out    'a gh failure is never classified as phantoms'

# --- empty SUCCESSFUL listing stays the legitimate all-phantom signal -----------------------
function gh {
    if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
    '[]'; $global:LASTEXITCODE = 0
}
$out = (& $Script -DocPath $DocAbs 6>&1 | Out-String)
Assert-Match 'phantom: `REAL_VAR`'     $out 'documented var absent from an empty listing is phantom'
Assert-Match 'phantom: `DOC_ONLY_VAR`' $out 'both documented vars report phantom'
Assert-NoMatch 'NOT_A_VAR'             $out 'rows outside the Repo variables section are ignored'

# --- happy path: listing and doc agree ------------------------------------------------------
function gh {
    if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
    '[{"name":"REAL_VAR","value":"x"},{"name":"DOC_ONLY_VAR","value":"42"}]'; $global:LASTEXITCODE = 0
}
$out = (& $Script -DocPath $DocAbs 6>&1 | Out-String)
Assert-Match 'agree \(2 variables, 2 documented\)' $out 'matching listing reports agreement'

# --- the consumer's repo-relative form, on the same gh shadow -------------------------------
# Skipped exactly where the derivation degrades -- a fully qualified result means repo root and
# this suite sit on different roots (the junction), and there is no repo-relative form to hand over.
# git prints the root as UTF-8: read it the way the gate does, or a non-ASCII checkout path
# yields a root that does not exist and a relative form that climbs out with '..'.
$suiteEncoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $suiteRoot = (git rev-parse --show-toplevel).Trim()
}
finally { [Console]::OutputEncoding = $suiteEncoding }
$DocRel = [IO.Path]::GetRelativePath($suiteRoot, $DocAbs)
if ([IO.Path]::IsPathFullyQualified($DocRel)) {
    Write-Host "skip: repo root and $PSScriptRoot are on different roots; no repo-relative form exists" -ForegroundColor DarkGray
} else {
    $out = (& $Script -DocPath $DocRel 6>&1 | Out-String)
    Assert-Match 'agree \(2 variables, 2 documented\)' $out 'a repo-relative -DocPath still resolves'

    # --- and with a leading slash, which on Windows is rooted but not fully qualified ---------
    # The spelling a consumer reaches for to mean "from the repo root"; IsPathRooted is true of it,
    # which is why the script tests IsPathFullyQualified. On POSIX a leading slash IS absolute,
    # so the case has no meaning there.
    if ($IsWindows) {
        $out = (& $Script -DocPath ('/' + ($DocRel -replace '\\', '/')) 6>&1 | Out-String)
        Assert-Match 'agree \(2 variables, 2 documented\)' $out 'a leading-slash -DocPath still resolves under the repo root'
    } else {
        Write-Host 'skip: a leading slash is an absolute path on POSIX; nothing to pin' -ForegroundColor DarkGray
    }
}

# --- stderr noise on a SUCCESSFUL listing must not break the parse --------------------------
# gh writes notices to stderr with exit 0 (GH_DEBUG traces, update notices); only stdout is JSON.
function gh {
    if ($args[0] -eq 'repo') { $global:LASTEXITCODE = 0; return 'o/n' }
    Write-Error 'A new release of gh is available' -ErrorAction Continue
    '[{"name":"REAL_VAR","value":"x"},{"name":"DOC_ONLY_VAR","value":"42"}]'
    $global:LASTEXITCODE = 0
}
$out = (& $Script -DocPath $DocAbs 6>&1 | Out-String)
Assert-Match 'agree \(2 variables, 2 documented\)' $out 'stderr noise on exit 0 does not break parsing'

# --- -VariablesJson bypasses gh entirely ----------------------------------------------------
function gh { throw 'gh must not be called when -VariablesJson is provided' }
$out = (& $Script -VariablesJson '[{"name":"REAL_VAR","value":"x"},{"name":"DOC_ONLY_VAR","value":"42"}]' -DocPath $DocAbs 6>&1 | Out-String)
Assert-Match 'agree \(2 variables, 2 documented\)' $out '-VariablesJson path untouched by the fix'

# --- the degradation this suite must never lean on again ------------------------------------
# No junction needed: across two roots GetRelativePath hands back the absolute second argument.
if ($IsWindows) {
    Assert-Match '^C:\\' ([IO.Path]::GetRelativePath('D:\ci\work', 'C:\dev\work\x.md')) 'GetRelativePath returns an absolute path across drive roots'
} else {
    Write-Host 'skip: drive roots are a Windows notion; the cross-root degradation has no form here' -ForegroundColor DarkGray
}

# --- a work tree at a non-ASCII path, under an OEM console code page -----------------------
# git prints the root as UTF-8 and PowerShell decodes a native command's output with
# [Console]::OutputEncoding: under code page 437 the root names no directory, so a relative
# -DocPath does not resolve. The caller's encoding must come back unchanged.
$uniRepo = Join-Path ([IO.Path]::GetTempPath()) ("repovars-r$([char]0xE9)po-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$made = try { New-Item -ItemType Directory -Force -Path (Join-Path $uniRepo 'docs') | Out-Null; $true } catch { $false }
if (-not $made) {
    Write-Host "skip: cannot create a directory named with U+00E9 under $([IO.Path]::GetTempPath())" -ForegroundColor DarkGray
} else {
    $callerEncoding = [Console]::OutputEncoding
    Push-Location -LiteralPath $uniRepo
    try {
        git init -q . 2>$null
        Copy-Item -LiteralPath $DocAbs -Destination (Join-Path $uniRepo 'docs/vars.md')
        [Console]::OutputEncoding = [Text.Encoding]::GetEncoding(437)
        $out = try { & $Script -VariablesJson '[{"name":"REAL_VAR","value":"x"},{"name":"DOC_ONLY_VAR","value":"42"}]' -DocPath docs/vars.md 6>&1 | Out-String } catch { "threw: $_" }
        $after = [Console]::OutputEncoding.CodePage
    }
    finally {
        [Console]::OutputEncoding = $callerEncoding
        Pop-Location
        Remove-Item -LiteralPath $uniRepo -Recurse -Force
    }
    Assert-Match 'agree \(2 variables, 2 documented\)' $out 'a relative -DocPath resolves in a repo at a non-ASCII path under code page 437'
    Assert-Match '^437$' "$after" 'the caller''s console output encoding is unchanged by the script'
}

if ($failures) { Write-Host "`n$failures failure(s)." -ForegroundColor Red; exit 1 }
Write-Host "`nAll repo-variables-doc tests passed." -ForegroundColor Green
exit 0
