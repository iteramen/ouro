<#
.SYNOPSIS
    Fixture suite for Get-CompileProgram.ps1: the renderer of /ouro:compile.
.DESCRIPTION
    Feeds the script small hand-written analyzer graphs, with the pull request, blocked-issue and
    issue reads handed over as JSON, and asserts on its stdout, stderr and exit code. Rows: the
    wave re-pack at the default, at 4 and past the ceiling, and a width of 0; an in-flight issue
    dropped from the waves, its empty-footprint twin printed once, and the `Fixes` line forms that
    do and do not count; scope read before the re-pack, with its refused forms; the batch lines,
    the unbatched collision, the in-flight member and the batch left with one; the empty-menu line
    ahead of `finish` and `--explain`; unblock ranking and the trigger-line forms that name a
    ruling and those that do not; an umbrella's children, fences and a closed child; cleanup's
    duplicate boundary at two thirds; a limit-sized read; the self-checks, which exit 3 with
    nothing on stdout; a schema other than 2, a read that fails, the usage errors, and
    byte-identical output from standard input and from -Graph.
    It needs pwsh only, so a Windows leg with no bash runs every row.
#>
$ErrorActionPreference = 'Stop'

$Base   = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Script = @((Join-Path $Base 'Get-CompileProgram.ps1'), (Join-Path $Base 'bin/Get-CompileProgram.ps1')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Script) { throw "Get-CompileProgram.ps1 not found under $Base" }
$pwshExe = (Get-Process -Id $PID).Path

$failures = 0
function Assert-Equal($Expected, $Actual, $What) {
    if ($Expected -ceq $Actual) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- expected '$Expected', got '$Actual'" -ForegroundColor Red; $script:failures++ }
}
function Assert-Match($Pattern, $Text, $What) {
    if ($Text -cmatch $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- no match for '$Pattern' in:`n$Text" -ForegroundColor Red; $script:failures++ }
}
function Assert-NoMatch($Pattern, $Text, $What) {
    if ($Text -cnotmatch $Pattern) { Write-Host "  ok: $What" -ForegroundColor DarkGray }
    else { Write-Host "FAIL: $What -- unexpected match for '$Pattern' in:`n$Text" -ForegroundColor Red; $script:failures++ }
}

function Invoke-Cp([string[]]$Argv, [string]$Stdin = '', [hashtable]$Environment = @{}) {
    $psi = [System.Diagnostics.ProcessStartInfo]::new($pwshExe)
    foreach ($a in (@('-NoProfile', '-File', $Script) + $Argv)) { $psi.ArgumentList.Add($a) }
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $psi.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    foreach ($k in $Environment.Keys) { $psi.Environment[$k] = $Environment[$k] }
    $p = [System.Diagnostics.Process]::Start($psi)
    $p.StandardInput.Write($Stdin)
    $p.StandardInput.Close()
    $out = $p.StandardOutput.ReadToEndAsync()
    $err = $p.StandardError.ReadToEndAsync()
    $p.WaitForExit()
    [pscustomobject]@{ Out = $out.Result; Err = $err.Result; Code = $p.ExitCode }
}

# A graph as raw JSON. Issues are [number, state, footprint[], unresolved[]].
function New-Graph($Issues, [string]$Collisions = '[]', [string]$Batches = '[]', [string]$Waves = '[]', [string]$Unplaced = '[]', [string]$Leverage = '[]', [int]$Schema = 2, [string]$Mirrored = '["M.md"]') {
    $rows = foreach ($i in $Issues) {
        $fp = (@($i[2]) | Where-Object { $_ } | ForEach-Object { '"' + $_ + '"' }) -join ','
        $un = (@($i[3]) | Where-Object { $_ } | ForEach-Object { '"' + $_ + '"' }) -join ','
        "{`"number`":$($i[0]),`"title`":`"t$($i[0])`",`"state`":`"$($i[1])`",`"footprint`":[$fp],`"unresolved`":[$un]}"
    }
    "{`"schema`":$Schema,`"repo`":`"o/n`",`"head`":`"0123456789abcdef`",`"mirrored`":$Mirrored,`"issues`":[$($rows -join ',')],`"collisions`":$Collisions,`"batches`":$Batches,`"waves`":$Waves,`"unplaced`":$Unplaced,`"leverage`":$Leverage}"
}
function Invoke-Prog($GraphJson, [string[]]$Extra = @(), [string]$Prs = '[]') {
    Invoke-Cp (@('-PrsJson', $Prs) + $Extra) $GraphJson
}
function Get-Section([string]$Text) { ($Text -split "`n\n", 2)[1] }

$scratch = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('compile-program-test-' + [guid]::NewGuid().ToString('n')))
try {
    # Three ready issues sharing nothing: one analyzer wave of three.
    $three = New-Graph @(@(1, 'agent-ready', @('a')), @(2, 'agent-ready', @('b')), @(3, 'agent-ready', @('c'))) -Waves '[[1,2,3]]'

    # --- row 1: the re-pack ------------------------------------------------------------------------
    $r = Invoke-Prog $three
    Assert-Equal 0 $r.Code 'row 1: exit 0'
    Assert-Match '(?m)^Wave 1 \(2\):\n  #1 t1\n  #2 t2\nWave 2 \(1\):\n  #3 t3$' $r.Out 'row 1: a wave of three is two waves of at most 2 by default, in number order'
    $r = Invoke-Prog $three @('-Width', '3')
    Assert-Match '(?m)^Wave 1 \(3\):' $r.Out 'row 1 control: width 3 keeps one wave of three'
    $r = Invoke-Prog $three @('-Width', '9')
    Assert-Match '(?m)^Wave 1 \(3\):' $r.Out 'row 1: width 9 is clamped, so a wave of three is not split below 4'
    Assert-Match '--width 9 is above the ceiling of 4: taken as 4' $r.Out 'row 1: and a line says so'
    $five1 = New-Graph @(@(1, 'agent-ready', @('a')), @(2, 'agent-ready', @('b')), @(3, 'agent-ready', @('c')), @(4, 'agent-ready', @('d')), @(5, 'agent-ready', @('e'))) -Waves '[[1,2,3,4,5]]'
    $r = Invoke-Prog $five1 @('-Width', '5')
    Assert-Match '(?m)^Wave 1 \(4\):' $r.Out 'row 1: width 5, one past the ceiling, is taken as 4'
    Assert-Match '(?m)^Wave 2 \(1\):' $r.Out 'row 1: so five issues take two waves'
    $r = Invoke-Prog $three @('-Width', '4')
    Assert-NoMatch 'above the ceiling' $r.Out 'row 1 control: width 4 draws no clamp line'
    $r = Invoke-Prog $three @('-Width', '0')
    Assert-Equal 2 $r.Code 'row 1: width 0 is a usage error'
    Assert-Equal '' $r.Out 'row 1: with nothing on stdout'
    $r = Invoke-Prog $three @('-Width', 'x')
    Assert-Equal 2 $r.Code 'row 1: a non-integer width is a usage error'

    # --- row 2: in flight --------------------------------------------------------------------------
    $fly = New-Graph @(@(86, 'agent-ready', @('a')), @(122, 'agent-ready', @(), @('gone.md')), @(700, 'agent-ready', @('a', 'b'))) `
        -Collisions '[{"a":86,"b":700,"files":["a"]}]' -Batches '[[86,700]]' -Waves '[[86],[700]]' -Unplaced '[122]'
    $prs = '[{"number":116,"body":"x\r\nFixes #86\r\n"},{"number":166,"body":"Fixes #122"}]'
    $r = Invoke-Prog $fly -Prs $prs
    Assert-Equal 0 $r.Code 'row 2: exit 0 with the self-check passing'
    Assert-Match 'in flight 2' $r.Out 'row 2: both counted in flight'
    Assert-Equal 1 ([regex]::Matches($r.Out, '#122 ')).Count 'row 2: the in-flight issue with no footprint prints once'
    Assert-Match '(?m)^Wave 1 \(1\):\n  #700 t700 \[collides with #86 \(in flight, PR #116\)\]$' $r.Out 'row 2: the waves hold only the queued issue, which carries the in-flight collision'
    Assert-NoMatch 'The footprint says nothing' $r.Out 'row 2: and the empty-footprint list holds no in-flight issue'
    $r = Invoke-Prog $fly -Prs '[{"number":116,"body":"see Fixes #86"},{"number":166,"body":"Fixes #1220\nfixes #122"}]'
    Assert-Match 'in flight 0' $r.Out 'row 2 control: a Fixes mid-line, a longer number and a lower-case form are not in flight'

    # --- row 3: scope is read before the re-pack ------------------------------------------------------
    $four = New-Graph @(@(1, 'agent-ready', @('a')), @(2, 'agent-ready', @('b')), @(3, 'agent-ready', @('c')), @(4, 'agent-ready', @('d'))) -Waves '[[1,2,3,4]]'
    $list = '[{"number":1,"title":"t1","state":"OPEN","labels":[{"name":"skills"}]},{"number":2,"title":"t2","state":"OPEN","labels":[{"name":"gates"}]},{"number":3,"title":"t3","state":"OPEN","labels":[{"name":"skills"}]},{"number":4,"title":"t4","state":"OPEN","labels":[{"name":"skills"}]}]'
    $r = Invoke-Prog $four @('-Scope', 'skills', '-Areas', '["skills","gates"]', '-IssuesJson', $list)
    Assert-Equal 0 $r.Code 'row 3: a declared scope exits 0'
    Assert-Match '(?m)^Wave 1 \(2\):\n  #1 t1\n  #3 t3\nWave 2 \(1\):\n  #4 t4$' $r.Out 'row 3: the scope filters first, then the re-pack'
    Assert-NoMatch '#2 ' $r.Out 'row 3: and the issue outside it is not printed'
    $r = Invoke-Prog $four @('-Scope', 'Skills', '-Areas', 'skills,gates', '-IssuesJson', $list)
    Assert-Equal 0 $r.Code 'row 3: a label is compared without case, from a comma list'
    $r = Invoke-Prog $four @('-Scope', 'skill', '-Areas', '["skills","gates"]', '-IssuesJson', $list)
    Assert-Equal 2 $r.Code 'row 3: a label that is only a prefix of a declared area is refused'
    Assert-Match 'skills, gates' $r.Err 'row 3: and the message names the declared areas'
    $r = Invoke-Prog $four @('-Scope', 'skills', '-IssuesJson', $list)
    Assert-Equal 0 $r.Code 'row 3: with no area declared the scope is refused but the run goes on'
    Assert-Match 'no \[labels\]\.area is declared' $r.Out 'row 3: and a line says it ran unscoped'
    Assert-Match '(?m)^Wave 1 \(2\):\n  #1 t1\n  #2 t2$' $r.Out 'row 3: with every issue printed'

    # --- row 4: batches ------------------------------------------------------------------------------
    $bat = New-Graph @(@(1, 'agent-ready', @('a', 'b')), @(2, 'agent-ready', @('a', 'b')), @(3, 'agent-ready', @('b', 'c')), @(4, 'agent-ready', @('c')), @(5, 'agent-ready', @('d')), @(6, 'agent-ready', @('d', 'z')), @(7, 'agent-ready', @('z'))) `
        -Collisions '[{"a":1,"b":2,"files":["a","b"]},{"a":1,"b":3,"files":["b"]},{"a":2,"b":3,"files":["b"]},{"a":3,"b":4,"files":["c"]},{"a":5,"b":6,"files":["d"]},{"a":6,"b":7,"files":["z"]}]' `
        -Batches '[[1,2],[5,6]]' -Waves '[[1,4,5,7],[2],[3,6]]'
    $r = Invoke-Prog $bat @('-Intent', 'batch')
    Assert-Equal 0 $r.Code 'row 4: exit 0'
    Assert-Match '(?m)^/ouro:fuse 1 2\n  shared: #1 #2 on a, b$' $r.Out 'row 4: a batch is a fuse argument list with its shared files'
    Assert-Match '(?m)^  #1 collides with unbatched #3 \(1\)$' $r.Out 'row 4: a collision with an issue in no batch says unbatched'
    Assert-Match '(?m)^  #2 collides with unbatched #3 \(1\)$' $r.Out 'row 4: for each member'
    Assert-Match '(?m)^  #6 collides with unbatched #7 \(1\)$' $r.Out 'row 4: in the second batch too'
    Assert-Equal 3 ([regex]::Matches($r.Out, 'collides with unbatched')).Count 'row 4: three cross lines, as collisions holds'
    $bat2 = $bat.Replace('"batches":[[1,2],[5,6]]', '"batches":[[1,2],[3,4],[5,6]]')
    $r = Invoke-Prog $bat2 @('-Intent', 'batch')
    Assert-Match '(?m)^  #1 collides across batches with #3 \(1\)$' $r.Out 'row 4: a collision with a member of another batch says across batches'
    $r = Invoke-Prog $bat @('-Intent', 'batch') '[{"number":9,"body":"Fixes #2"}]'
    Assert-NoMatch '(?m)^/ouro:fuse 1 2' $r.Out 'row 4: a batch left with one member is dropped'
    Assert-Match 'dropped from the batch of #1 #2: #2' $r.Out 'row 4: and a line names the in-flight member'
    $many = New-Graph @(@(1, 'agent-ready', @('a')), @(2, 'agent-ready', @('a')), @(3, 'agent-ready', @('b')), @(4, 'agent-ready', @('b')), @(5, 'agent-ready', @('c')), @(6, 'agent-ready', @('d'))) `
        -Collisions '[{"a":1,"b":2,"files":["a"]},{"a":1,"b":3,"files":["x"]},{"a":1,"b":4,"files":["y"]},{"a":1,"b":5,"files":["z"]},{"a":1,"b":6,"files":["w"]}]' -Batches '[[1,2],[3,4]]' -Waves '[[1],[2],[3],[4],[5],[6]]'
    $r = Invoke-Prog $many @('-Intent', 'batch') '[{"number":9,"body":"Fixes #6"}]'
    Assert-Match '(?m)^  #1 collides across batches with #3, #4 \(2\); collides with unbatched #5, #6 \(in flight, PR #9\) \(2\)$' $r.Out 'row 4: one line per member lists its cross-batch collisions by number, the unbatched ones apart and marked'
    Assert-NoMatch 'on [xyzw]' $r.Out 'row 4: and names no file'
    $trio = New-Graph @(@(1, 'agent-ready', @('a')), @(2, 'agent-ready', @('a')), @(3, 'agent-ready', @('a'))) `
        -Collisions '[{"a":1,"b":2,"files":["a"]},{"a":1,"b":3,"files":["a"]},{"a":2,"b":3,"files":["a"]}]' -Batches '[[1,2,3]]' -Waves '[[1],[2],[3]]'
    $r = Invoke-Prog $trio @('-Intent', 'batch') '[{"number":9,"body":"Fixes #3"}]'
    Assert-Equal 0 $r.Code 'row 4: a batch with one in-flight member of three exits 0'
    Assert-Match '(?m)^/ouro:fuse 1 2$' $r.Out 'row 4: and prints the other two'
    $r = Invoke-Prog (New-Graph @(,@(1, 'agent-ready', @('a'))) -Waves '[[1]]') @('-Intent', 'batch')
    Assert-Match 'No batch to print' $r.Out 'row 4 control: no batch says so'

    # --- row 5: the empty menu ---------------------------------------------------------------------------
    $none = New-Graph @(,@(550, 'needs-ruling', @('x'))) -Leverage '[{"number":550,"count":0,"gates":[]}]'
    $tj = '{"number":550,"title":"t550","state":"OPEN","body":"","labels":[]}'
    $r = Invoke-Prog $none @('-Intent', 'finish', '-Target', '550', '-TargetJson', $tj, '-IssuesJson', '[]', '-BlockedJson', '[]')
    Assert-Equal 0 $r.Code 'row 5: finish on an empty menu exits 0'
    Assert-Match 'The `agent-ready` menu is empty' $r.Out 'row 5: the menu is empty'
    Assert-Match '(?m)^#550 t550 \[needs-ruling\]$' $r.Out 'row 5: and the finish output follows the line'
    Assert-Equal $true ($r.Out.IndexOf('menu is empty') -lt $r.Out.IndexOf('#550 t550')) 'row 5: the line comes first'
    $r = Invoke-Prog $none @('-Explain', '550', '-BlockedJson', '[]')
    Assert-Match 'menu is empty' $r.Out 'row 5: --explain prints the line too'
    Assert-Match 'wave: none \(not agent-ready\)' $r.Out 'row 5: and its own account'
    $r = Invoke-Prog $three
    Assert-NoMatch 'menu is empty' $r.Out 'row 5 control: a queue with ready issues prints no such line'

    # --- row 6: unblock ----------------------------------------------------------------------------------------
    $ub = New-Graph @(@(5, 'needs-ruling', @('x')), @(6, 'needs-ruling', @('y')), @(7, 'needs-ruling', @('z')), @(50, 'agent-ready', @('x'))) -Waves '[[50]]' `
        -Leverage '[{"number":5,"count":1,"gates":[50]},{"number":6,"count":0,"gates":[]},{"number":7,"count":0,"gates":[]}]'
    $bl = '[{"number":20,"title":"b","body":"**Unblocks when:** #6 is ruled"},{"number":21,"title":"b","body":"**Unblocks when:** #6 and #7"},{"number":22,"title":"b","body":"**Unblocks when:** #55 is closed"},{"number":23,"title":"b","body":"waiting\n**Unblocks when:** #5"},{"number":24,"title":"b","body":"**Unblocks when:** o/r#5 closes"},{"number":25,"title":"b","body":"﻿  **Unblocks when:** #7 "},{"number":26,"title":"b","body":"**unblocks when:** #5"}]'
    $r = Invoke-Prog $ub @('-Intent', 'unblock', '-BlockedJson', $bl)
    Assert-Equal 0 $r.Code 'row 6: exit 0'
    $order = ([regex]::Matches($r.Out, '(?m)^#(\d+) t\d+$') | ForEach-Object { $_.Groups[1].Value }) -join ','
    Assert-Equal '6,7,5' $order 'row 6: ranked by the blocked count, then the ready count, then the number'
    Assert-Match '(?m)^#6 t6\n  blocked issues whose trigger names it: #20 #21\n' $r.Out 'row 6: #6 is named by the two trigger lines that name it'
    Assert-Match '(?m)^#7 t7\n  blocked issues whose trigger names it: #21 #25\n' $r.Out 'row 6: a byte-order mark and spaces before the marker still count'
    Assert-Match '(?m)^#5 t5\n  blocked issues whose trigger names it: none\n  ready work its build would collide with: 1 #50$' $r.Out 'row 6: a second line, a longer number, a cross-repository reference and a lower-case marker name nothing; the leverage is the second column'
    Assert-Match 'judgment each.*#6 #7 #5' $r.Out 'row 6: the top rows are named for a sentence of judgment'

    $tie = New-Graph @(@(5, 'needs-ruling', @('x')), @(6, 'needs-ruling', @('y')), @(7, 'needs-ruling', @('z')), @(50, 'agent-ready', @('x', 'y')), @(51, 'agent-ready', @('y'))) -Waves '[[50],[51]]' `
        -Leverage '[{"number":6,"count":2,"gates":[50,51]},{"number":5,"count":1,"gates":[50]},{"number":7,"count":0,"gates":[]}]'
    $r = Invoke-Prog $tie @('-Intent', 'unblock', '-BlockedJson', '[]')
    $order = ([regex]::Matches($r.Out, '(?m)^#(\d+) t\d+$') | ForEach-Object { $_.Groups[1].Value }) -join ','
    Assert-Equal '6,5,7' $order 'row 6: with the blocked counts tied, the one holding more ready work ranks first, then by number'
    $rows = (1..200 | ForEach-Object { "{`"number`":$($_ + 1000),`"title`":`"b`",`"body`":`"x`"}" }) -join ','
    $r = Invoke-Prog $ub @('-Intent', 'unblock', '-BlockedJson', "[$rows]")
    Assert-Match 'warning: the blocked issue read returned 200 rows' $r.Out 'row 6: a 200-row blocked read warns, ahead of the ranking'
    $rows = (1..199 | ForEach-Object { "{`"number`":$($_ + 1000),`"title`":`"b`",`"body`":`"x`"}" }) -join ','
    Assert-NoMatch 'warning' (Invoke-Prog $ub @('-Intent', 'unblock', '-BlockedJson', "[$rows]")).Out 'row 6 control: 199 rows warn nothing'

    # --- row 7: finish ---------------------------------------------------------------------------------------------
    $fin = New-Graph @(@(30, 'agent-ready', @('a')), @(31, 'agent-ready', @('a')), @(32, 'needs-ruling', @('q'))) -Waves '[[30],[31]]' -Collisions '[{"a":30,"b":31,"files":["a"]}]' `
        -Leverage '[{"number":32,"count":0,"gates":[]}]'
    $all = '[{"number":30,"title":"t30","state":"OPEN","labels":[{"name":"agent-ready"}]},{"number":31,"title":"t31","state":"OPEN","labels":[{"name":"agent-ready"}]},{"number":32,"title":"t32","state":"OPEN","labels":[{"name":"needs-ruling"}]},{"number":33,"title":"t33","state":"OPEN","labels":[{"name":"blocked"}]},{"number":34,"title":"t34","state":"CLOSED","labels":[]},{"number":35,"title":"t35","state":"OPEN","labels":[]}]'
    $umb = '{"number":29,"title":"u","state":"OPEN","labels":[{"name":"umbrella"}],"body":"1. #31 second\n- [ ] #30 first\n- #32 ruling\n* #33 blocked\n- #34 done\n```\n- #35 in a fence\n```\nSee #35 in prose, and - a list item that mentions #35 later\n| #35 | table |"}'
    $blk = '[{"number":33,"title":"t33","body":"**Unblocks when:** #32 is ruled"}]'
    $r = Invoke-Prog $fin @('-Intent', 'finish', '-Target', '29', '-TargetJson', $umb, '-IssuesJson', $all, '-BlockedJson', $blk)
    Assert-Equal 0 $r.Code 'row 7: an umbrella exits 0'
    Assert-Match 'Umbrella #29 u: 5 children' $r.Out 'row 7: five children, the fenced, prose and table mentions not read'
    Assert-Match '(?m)^  #30 t30 -- agent-ready, wave 1\n  #31 t31 -- agent-ready, wave 2\n  #32 t32 -- needs-ruling\n    blocked issues whose trigger names it: #33\n    ready work its build would collide with: 0\n  #33 t33 -- blocked: \*\*Unblocks when:\*\* #32 is ruled\n  #34 t34 -- closed$' $r.Out 'row 7: the ready children in wave order, then the ruling with its columns, the blocked one with its trigger, the closed one last as closed'
    $r = Invoke-Prog $fin @('-Intent', 'finish', '-Target', '30', '-TargetJson', '{"number":30,"title":"t30","state":"OPEN","body":"","labels":[]}', '-IssuesJson', $all)
    Assert-Match '(?m)^wave: 1$' $r.Out 'row 7: a plain issue target gets its explain account'
    $areaList = $all.Replace('"title":"t31","state":"OPEN","labels":[{"name":"agent-ready"}]', '"title":"t31","state":"OPEN","labels":[{"name":"gates"}]')
    $r = Invoke-Prog $fin @('-Intent', 'finish', '-Target', 'gates', '-Areas', 'skills,gates', '-IssuesJson', $areaList)
    Assert-Match '(?m)^Wave 1 \(1\):
  #31 t31 \[collides with #30 \(outside scope\)\]$' $r.Out 'row 7: an area target is throughput over the issues carrying it, noting its collision with one outside'
    Assert-NoMatch '(?m)^  #30 ' $r.Out 'row 7: and listing no other'
    $r = Invoke-Prog $fin @('-Intent', 'finish', '-Target', 'nope', '-Areas', 'skills,gates', '-IssuesJson', $areaList)
    Assert-Equal 2 $r.Code 'row 7: an area that is not declared is refused'
    $r = Invoke-Prog $fin @('-Intent', 'finish')
    Assert-Equal 2 $r.Code 'row 7: finish without a target is a usage error'

    # --- row 8: --explain and the earlier waves --------------------------------------------------------------------------
    $ex = New-Graph @(@(1, 'agent-ready', @('a')), @(2, 'agent-ready', @('a', 'b')), @(3, 'agent-ready', @('b')), @(4, 'agent-ready', @('c'))) `
        -Collisions '[{"a":1,"b":2,"files":["a"]},{"a":2,"b":3,"files":["b"]}]' -Waves '[[1,3,4],[2]]'
    $r = Invoke-Prog $ex @('-Explain', '2', '-Width', '4')
    Assert-Equal 0 $r.Code 'row 8: exit 0'
    Assert-Match '(?m)^wave: 2
  earlier wave 1: collides with #1 #3
in flight' $r.Out 'row 8: N is in wave 2, and the earlier wave names the issues it collides with'
    $r = Invoke-Prog $ex @('-Explain', '2')
    Assert-Match '(?m)^wave: 3
  earlier wave 1: collides with #1 #3
  earlier wave 2: collides with none
' $r.Out 'row 8: at the default width N is in the re-packed wave, the one throughput prints'
    Assert-Match '(?m)^  #1 \(1\): a\n  #3 \(1\): b$' $r.Out 'row 8: collisions with their files'
    $r = Invoke-Prog $ex @('-Explain', '99')
    Assert-Equal 2 $r.Code 'row 8: an issue the analyzer did not read is a usage error'

    # --- row 9: cleanup ------------------------------------------------------------------------------------------------------
    $cl = New-Graph @(
        @(1, 'agent-ready', @('a', 'b', 'c', 'd'), @('gone.md')),
        @(2, 'agent-ready', @('a', 'b', 'c', 'd', 'e', 'f')),
        @(3, 'agent-ready', @('a', 'b', 'c', 'd', 'e', 'f', 'g')),
        @(4, 'agent-ready', @('A', 'B', 'C')),
        @(5, 'agent-ready', @('M.md', 'a', 'b')),
        @(6, 'agent-ready', @('M.md', 'a', 'b'))) -Waves '[[1],[2],[3],[4],[5],[6]]'
    $r = Invoke-Prog $cl @('-Intent', 'cleanup')
    Assert-Equal 0 $r.Code 'row 9: exit 0'
    Assert-Match '(?m)^  #1: gone\.md$' $r.Out 'row 9: the unresolved path'
    Assert-Match '(?m)^  #1 #2 share 4 of 6 files$' $r.Out 'row 9: exactly two thirds of the union is a candidate'
    Assert-NoMatch '#1 #3 ' $r.Out 'row 9: four of seven is not'
    Assert-Match '(?m)^  #2 #3 share 6 of 7 files$' $r.Out 'row 9: six of seven is'
    Assert-NoMatch '#4 ' $r.Out 'row 9: a case variant shares nothing'
    Assert-NoMatch '#5 #6' $r.Out 'row 9: two files, even identical after the mirrored one is left out, are too few'
    Assert-Match 'Invoke-BranchSweep.ps1' $r.Out 'row 9: the branch sweep is pointed at, not run'

    # --- row 10: a read that returns its limit ----------------------------------------------------------------------------------
    $rows = (1..200 | ForEach-Object { "{`"number`":$($_ + 1000),`"body`":`"x`"}" }) -join ','
    $r = Invoke-Prog $three -Prs "[$rows]"
    Assert-Match 'warning: the open pull request read returned 200 rows' $r.Out 'row 10: a 200-row pull request read warns'
    $rows = (1..199 | ForEach-Object { "{`"number`":$($_ + 1000),`"body`":`"x`"}" }) -join ','
    $r = Invoke-Prog $three -Prs "[$rows]"
    Assert-NoMatch 'warning' $r.Out 'row 10 control: 199 rows warn nothing'

    # --- row 11: the self-checks --------------------------------------------------------------------------------------------------
    $lost = New-Graph @(@(1, 'agent-ready', @('a')), @(2, 'agent-ready', @('b'))) -Waves '[[1]]'
    $r = Invoke-Prog $lost
    Assert-Equal 3 $r.Code 'row 11: a ready issue the graph places nowhere exits 3'
    Assert-Equal '' $r.Out 'row 11: with nothing on stdout'
    Assert-Match 'self-check failed' $r.Err 'row 11: and the check named on stderr'
    $dup = New-Graph @(,@(1, 'agent-ready', @('a'))) -Waves '[[1],[1]]'
    Assert-Equal 3 (Invoke-Prog $dup).Code 'row 11: an issue placed twice exits 3'
    $six = New-Graph @(@(1, 'agent-ready', @('a')), @(2, 'agent-ready', @('a')), @(3, 'agent-ready', @('a')), @(4, 'agent-ready', @('a')), @(5, 'agent-ready', @('a')), @(6, 'agent-ready', @('a'))) `
        -Batches '[[1,2,3,4,5,6]]' -Waves '[[1],[2],[3],[4],[5],[6]]'
    $r = Invoke-Prog $six @('-Intent', 'batch')
    Assert-Equal 3 $r.Code 'row 11: a batch of six exits 3'
    $issues6 = @(@(1, 'agent-ready', @('a')), @(2, 'agent-ready', @('b')), @(3, 'agent-ready', @('c')), @(4, 'agent-ready', @('d')), @(5, 'agent-ready', @('e')), @(6, 'agent-ready', @('f')))
    $twice = New-Graph $issues6 -Waves '[[1],[1],[2],[3],[4],[5],[6]]'
    $sixBatch = New-Graph $issues6 -Batches '[[1,2,3,4,5,6]]' -Waves '[[1],[2],[3],[4],[5],[6]]'
    $tj1 = '{"number":1,"title":"t","state":"OPEN","body":"","labels":[]}'
    foreach ($case in @(@('places #1 twice', $twice), @('holds a batch of six', $sixBatch))) {
        foreach ($intent in 'throughput', 'unblock', 'cleanup', 'batch') {
            $r = Invoke-Prog $case[1] @('-Intent', $intent, '-BlockedJson', '[]')
            Assert-Equal 3 $r.Code "row 11: a graph that $($case[0]) exits 3 under $intent"
            Assert-Equal '' $r.Out "row 11: with nothing on stdout under $intent"
        }
        Assert-Equal 3 (Invoke-Prog $case[1] @('-Explain', '2', '-BlockedJson', '[]')).Code "row 11: a graph that $($case[0]) exits 3 under --explain"
        Assert-Equal 3 (Invoke-Prog $case[1] @('-Intent', 'finish', '-Target', '1', '-TargetJson', $tj1, '-IssuesJson', '[]')).Code "row 11: a graph that $($case[0]) exits 3 under finish"
    }
    $five = New-Graph @(@(1, 'agent-ready', @('a')), @(2, 'agent-ready', @('a')), @(3, 'agent-ready', @('a')), @(4, 'agent-ready', @('a')), @(5, 'agent-ready', @('a'))) `
        -Batches '[[1,2,3,4,5]]' -Waves '[[1],[2],[3],[4],[5]]'
    Assert-Equal 0 (Invoke-Prog $five @('-Intent', 'batch')).Code 'row 11 control: a batch of five passes'

    # --- row 12: input errors, determinism ----------------------------------------------------------------------------------------------
    $r = Invoke-Prog (New-Graph @(,@(1, 'agent-ready', @('a'))) -Waves '[[1]]' -Schema 3)
    Assert-Equal 1 $r.Code 'row 12: schema 3 exits 1'
    Assert-Match 'schema 3' $r.Err 'row 12: and names the value'
    Assert-Equal '' $r.Out 'row 12: with nothing on stdout'
    Assert-Equal 1 (Invoke-Prog 'not json').Code 'row 12: input that is not JSON exits 1'
    Assert-Equal 1 (Invoke-Prog '').Code 'row 12: empty input exits 1'
    $r = Invoke-Cp @('-Repo', 'o/n') $three -Environment @{ PATH = $scratch.FullName }
    Assert-Equal 1 $r.Code 'row 12: a failed pull request read exits 1'
    Assert-Equal '' $r.Out 'row 12: with nothing on stdout'
    Assert-Equal 2 (Invoke-Cp @('-PrsJson', '[]', '-Bogus', 'x') $three).Code 'row 12: an unknown parameter is a usage error'
    Assert-Equal 2 (Invoke-Prog $three @('-Intent', 'Throughput')).Code 'row 12: an intent is matched with its case'
    $gf = Join-Path $scratch.FullName 'graph.json'
    [IO.File]::WriteAllText($gf, $three, [Text.UTF8Encoding]::new($false))
    $viaFile = Invoke-Cp @('-PrsJson', '[]', '-Graph', $gf)
    $viaIn = Invoke-Prog $three
    Assert-Equal 0 $viaFile.Code 'row 12: -Graph reads a saved file'
    Assert-Equal (Get-Section $viaIn.Out) (Get-Section $viaFile.Out) 'row 12: and prints what standard input prints, but for the source line'
    Assert-Equal $viaIn.Out (Invoke-Prog $three).Out 'row 12: the same input twice is byte-identical'

    # --- row 20 (B8): a wave member's collision with an issue outside the scope is noted ----------------------
    $wsc = New-Graph @(@(1, 'agent-ready', @('a')), @(2, 'agent-ready', @('a')), @(3, 'agent-ready', @('c'))) -Collisions '[{"a":1,"b":2,"files":["a"]}]' -Waves '[[1,3],[2]]'
    $wscList = '[{"number":1,"title":"t1","state":"OPEN","labels":[{"name":"skills"}]},{"number":2,"title":"t2","state":"OPEN","labels":[{"name":"gates"}]},{"number":3,"title":"t3","state":"OPEN","labels":[{"name":"skills"}]}]'
    $r = Invoke-Prog $wsc @('-Scope', 'skills', '-Areas', 'skills,gates', '-IssuesJson', $wscList)
    Assert-Equal 0 $r.Code 'row 20: scoped throughput exits 0'
    Assert-Match '(?m)^  #1 t1 \[collides with #2 \(outside scope\)\]$' $r.Out 'row 20: a collision with an issue outside the scope is noted'
    Assert-Match '(?m)^  #3 t3$' $r.Out 'row 20: and a member with none is bare'
    $r = Invoke-Prog $wsc @('-Scope', 'skills', '-Areas', 'skills,gates', '-IssuesJson', $wscList) '[{"number":9,"body":"Fixes #2"}]'
    Assert-Match '(?m)^  #1 t1 \[collides with #2 \(in flight, PR #9\) \(outside scope\)\]$' $r.Out 'row 20: an issue both in flight and outside the scope carries both marks'
    $r = Invoke-Prog $wsc @('-Width', '4')
    Assert-NoMatch 'collides with' $r.Out 'row 20 control: unscoped and with no pull request, no note'

    # --- row 13 (B1): in-flight issues are marked wherever unblock and cleanup print them ----------------
    $ubf = New-Graph @(@(5, 'needs-ruling', @('x')), @(50, 'agent-ready', @('x')), @(51, 'agent-ready', @('x'))) -Waves '[[50],[51]]' `
        -Leverage '[{"number":5,"count":2,"gates":[50,51]}]'
    $blf = '[{"number":20,"title":"b","body":"**Unblocks when:** #5 is ruled"}]'
    $r = Invoke-Prog $ubf @('-Intent', 'unblock', '-BlockedJson', $blf) '[{"number":70,"body":"Fixes #5"},{"number":71,"body":"Fixes #50"}]'
    Assert-Equal 0 $r.Code 'row 13: unblock with in-flight rows exits 0'
    Assert-Match '(?m)^#5 t5 \(in flight, PR #70\)\n  blocked issues whose trigger names it: #20\n  ready work its build would collide with: 2 #50 \(in flight, PR #71\) #51$' $r.Out 'row 13: a ruling row''s own issue and the leverage column carry the in-flight mark'
    $r = Invoke-Prog $ubf @('-Intent', 'unblock', '-BlockedJson', $blf)
    Assert-Match '(?m)^#5 t5\n  blocked issues whose trigger names it: #20\n  ready work its build would collide with: 2 #50 #51$' $r.Out 'row 13 control: with no pull request nothing is marked'
    $r = Invoke-Prog $ubf @('-Explain', '5', '-BlockedJson', $blf) '[{"number":71,"body":"Fixes #50"}]'
    Assert-Match '(?m)^ready work its build would collide with: 2 #50 \(in flight, PR #71\) #51$' $r.Out 'row 13: --explain shares the unblock lines, so the leverage column is marked there too'
    $clf = New-Graph @(
        @(1, 'agent-ready', @('a', 'b', 'c', 'd'), @('gone.md')),
        @(2, 'agent-ready', @('a', 'b', 'c', 'd', 'e', 'f')),
        @(3, 'agent-ready', @('z'))) -Waves '[[1],[2],[3]]'
    $r = Invoke-Prog $clf @('-Intent', 'cleanup') '[{"number":9,"body":"Fixes #1"}]'
    Assert-Match '(?m)^  #1 \(in flight, PR #9\): gone\.md$' $r.Out 'row 13: a cleanup unresolved-path line marks an in-flight issue'
    Assert-Match '(?m)^  #1 \(in flight, PR #9\) #2 share 4 of 6 files$' $r.Out 'row 13: and so does a duplicate-candidate pair'
    $r = Invoke-Prog $clf @('-Intent', 'cleanup')
    Assert-Match '(?m)^  #1: gone\.md$' $r.Out 'row 13 control: not in flight, the unresolved line is bare'
    Assert-Match '(?m)^  #1 #2 share 4 of 6 files$' $r.Out 'row 13 control: and so is the pair'

    # --- row 14 (B2): a surviving batch member's collision with a dropped sibling prints ---------------------
    $r = Invoke-Prog $trio @('-Intent', 'batch') '[{"number":9,"body":"Fixes #3"}]'
    Assert-Equal 0 $r.Code 'row 14: a batch with one in-flight member of three exits 0, the recount agreeing with the lines'
    Assert-Match '(?m)^/ouro:fuse 1 2$' $r.Out 'row 14: the other two are printed'
    Assert-Match '(?m)^  #1 collides with dropped batch members #3 \(in flight, PR #9\) \(1\)$' $r.Out 'row 14: #1 names its collision with the in-flight sibling, marked'
    Assert-Match '(?m)^  #2 collides with dropped batch members #3 \(in flight, PR #9\) \(1\)$' $r.Out 'row 14: and #2 too'
    Assert-NoMatch 'collides across batches' $r.Out 'row 14: it is not an across-batches collision'
    $trioList = '[{"number":1,"title":"t1","state":"OPEN","labels":[{"name":"skills"}]},{"number":2,"title":"t2","state":"OPEN","labels":[{"name":"skills"}]},{"number":3,"title":"t3","state":"OPEN","labels":[{"name":"gates"}]}]'
    $r = Invoke-Prog $trio @('-Intent', 'batch', '-Scope', 'skills', '-Areas', 'skills,gates', '-IssuesJson', $trioList)
    Assert-Equal 0 $r.Code 'row 14: a batch with one out-of-scope member exits 0'
    Assert-Match '(?m)^  #1 collides with dropped batch members #3 \(outside scope\) \(1\)$' $r.Out 'row 14: a sibling dropped for scope is named and marked outside scope'
    $r = Invoke-Prog $bat @('-Intent', 'batch')
    Assert-NoMatch 'dropped batch members' $r.Out 'row 14 control: with no member dropped, no line names one'
    $r = Invoke-Prog $trio @('-Intent', 'batch')
    Assert-NoMatch 'dropped batch members' $r.Out 'row 14 control: and with no pull request, none either'

    # --- row 15 (B3): the precision line scopes the footprint and names the source ----------------------------
    $anaPath = @((Join-Path $Base 'Get-FootprintGraph.ps1'), (Join-Path $Base 'bin/Get-FootprintGraph.ps1')) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    $anaHelp = (([IO.File]::ReadAllText($anaPath) -split "`n" | ForEach-Object { $_.Trim() }) -join ' ') -replace '\s+', ' '
    $r = Invoke-Prog $three
    $prec = @($r.Out -split "`n" | Where-Object { $_.StartsWith('Precision:') })
    Assert-Equal 1 $prec.Count 'row 15: one precision line'
    Assert-Match 'read from the files the issues'' text names \(anchors and the `Doc impact on close` line\), not from a build' $prec[0] 'row 15: it says where the footprint is read from'
    Assert-Match 'quoted from the analyzer''s help' $prec[0] 'row 15: and names the source of the figures'
    foreach ($frag in 'measured on the anchor paths, before root files joined the footprint', 'footprint less its Doc-impact files over 160 landed issues: mean recall 0.83 against the files the landing touched', '3.7% of same-week pairs with disjoint footprints touched a common file', 'three queues of closed issues: pair precision 0.61 against a 0.29 base rate, and 0.18 of the colliding pairs held') {
        Assert-Equal $true $prec[0].Contains($frag) "row 15: the line quotes '$frag'"
        Assert-Equal $true $anaHelp.Contains($frag) "row 15: and the analyzer help holds the same words"
    }
    Assert-Match 'A proposal, never a guarantee' $prec[0] 'row 15: it keeps the proposal line'

    # --- row 16 (B4): the mirrored list is compared in the analyzer's spelling ---------------------------------
    $mir = New-Graph @(@(1, 'agent-ready', @('M.md', 'a.ps1', 'b.ps1')), @(2, 'agent-ready', @('M.md', 'a.ps1', 'b.ps1'))) -Waves '[[1],[2]]' -Mirrored ('[".' + ([string][char]92 * 2) + 'M.md"]')
    $r = Invoke-Prog $mir @('-Intent', 'cleanup')
    Assert-Equal 0 $r.Code 'row 16: exit 0'
    Assert-NoMatch '#1 #2 share' $r.Out 'row 16: a mirrored path spelled with a leading .\ is left out of the footprints, so two of three files are too few'
    Assert-Match 'mirrored files passed: M\.md' $r.Out 'row 16: and the header names it in the normalized spelling'
    $mirD = New-Graph @(@(1, 'agent-ready', @('M.md', 'a.ps1', 'b.ps1')), @(2, 'agent-ready', @('M.md', 'a.ps1', 'b.ps1'))) -Waves '[[1],[2]]' -Mirrored '["./M.md"]'
    Assert-NoMatch '#1 #2 share' (Invoke-Prog $mirD @('-Intent', 'cleanup')).Out 'row 16: a ./ spelling is left out too'
    $mirN = New-Graph @(@(1, 'agent-ready', @('M.md', 'a.ps1', 'b.ps1')), @(2, 'agent-ready', @('M.md', 'a.ps1', 'b.ps1'))) -Waves '[[1],[2]]' -Mirrored '["m.md"]'
    Assert-Match '(?m)^  #1 #2 share 3 of 3 files$' (Invoke-Prog $mirN @('-Intent', 'cleanup')).Out 'row 16 control: a case variant of the mirrored name leaves nothing out'

    # --- row 17 (B5): a closing fence matches the opening one in character and length --------------------------
    $kidList = '[{"number":30,"title":"t30","state":"OPEN","labels":[]},{"number":31,"title":"t31","state":"OPEN","labels":[]},{"number":32,"title":"t32","state":"OPEN","labels":[]}]'
    $bq = [string][char]96
    function Assert-Kids([string]$Expected, [string]$Body, [string]$What) {
        $tjf = ConvertTo-Json -Compress -InputObject ([ordered]@{ number = 29; title = 'u'; state = 'OPEN'; labels = @(@{ name = 'umbrella' }); body = $Body })
        $k = Invoke-Prog $none @('-Intent', 'finish', '-Target', '29', '-TargetJson', $tjf, '-IssuesJson', $kidList, '-BlockedJson', '[]')
        Assert-Equal 0 $k.Code "${What}: finish exits 0"
        Assert-Equal $Expected (([regex]::Matches($k.Out, '(?m)^  #(\d+) ') | ForEach-Object { $_.Groups[1].Value }) -join ',') $What
    }
    $f4 = $bq * 4; $f3 = $bq * 3
    Assert-Kids '32' "${f4}text`n- #30 example`n${f3}`n- #31 example`n${f4}`n- #32 real child" 'row 17: a shorter fence inside a longer one does not close it'
    Assert-Kids '32' "~~~~`n- #30 example`n~~~`n- #31 example`n~~~~`n- #32 real child" 'row 17: the tilde variant'
    Assert-Kids '32' "${f3}`n~~~`n- #31 inside`n${f3}`n- #32 real child" 'row 17: a different fence character inside a fence does not close it'
    Assert-Kids '32' "${f3}`n- #30 inside`n${f3}text`n- #31 inside`n${f3}`n- #32 real child" 'row 17: a closing line with an info string does not close'
    Assert-Kids '32' "${f3}text`n- #30 example`n${f4}`n- #32 real child" 'row 17: a longer closing fence closes a shorter one'
    Assert-Kids '31,32' "${f3}`n- #30 inside`n${f3}`n- #31 real`n- #32 after a closed fence" 'row 17 control: a plain fence closes, and the line after it is read'
    Assert-Kids '30,31,32' "- #30 a`n- #31 b`n- #32 c" 'row 17 control: with no fence all three are read'
    Assert-Kids '31' "${f3} a${bq}b`n- #31 real" 'row 17: a backtick line whose info string holds a backtick is not a fence'
    Assert-Kids '' "${f3} ab`n- #31 inside" 'row 17 control: the same line without the backtick opens one'

    # --- row 22 (R3-B1): -Explain and finish on an issue mark every batch member ---------------------------------
    $bg = New-Graph @(@(1, 'agent-ready', @('a')), @(2, 'agent-ready', @('a', 'b')), @(3, 'agent-ready', @('b'))) `
        -Collisions '[{"a":1,"b":2,"files":["a"]},{"a":2,"b":3,"files":["b"]}]' -Batches '[[1,2,3]]' -Waves '[[1,3],[2]]'
    $bgList = '[{"number":1,"title":"t1","state":"OPEN","labels":[{"name":"skills"}]},{"number":2,"title":"t2","state":"OPEN","labels":[{"name":"skills"}]},{"number":3,"title":"t3","state":"OPEN","labels":[{"name":"gates"}]}]'
    $bgT = '{"number":1,"title":"t1","state":"OPEN","body":"","labels":[]}'
    $r = Invoke-Prog $bg @('-Explain', '1') '[{"number":9,"body":"Fixes #3"}]'
    Assert-Equal 0 $r.Code 'row 22: --explain with an in-flight batchmate exits 0'
    Assert-Match '(?m)^batch: #1 #2 #3 \(in flight, PR #9\)$' $r.Out 'row 22: a batchmate that is no direct collision is marked in flight'
    $r = Invoke-Prog $bg @('-Intent', 'finish', '-Target', '1', '-TargetJson', $bgT, '-IssuesJson', $bgList) '[{"number":9,"body":"Fixes #3"}]'
    Assert-Match '(?m)^batch: #1 #2 #3 \(in flight, PR #9\)$' $r.Out 'row 22: and finish on an ordinary issue prints the same'
    $r = Invoke-Prog $bg @('-Explain', '1', '-Scope', 'skills', '-Areas', 'skills,gates', '-IssuesJson', $bgList)
    Assert-Match '(?m)^batch: #1 #2 #3 \(outside scope\)$' $r.Out 'row 22: a batchmate outside the scope is marked'
    $r = Invoke-Prog $bg @('-Intent', 'finish', '-Target', '1', '-TargetJson', $bgT, '-IssuesJson', $bgList, '-Scope', 'skills', '-Areas', 'skills,gates')
    Assert-Match '(?m)^batch: #1 #2 #3 \(outside scope\)$' $r.Out 'row 22: also under finish'
    $r = Invoke-Prog $bg @('-Explain', '1')
    Assert-Match '(?m)^batch: #1 #2 #3$' $r.Out 'row 22 control: with no pull request and no scope the batch is bare'

    # --- row 23 (R3-B2): a fence opened after a list marker is a fence ---------------------------------------------
    Assert-Kids '31' "- ${f3}text`n  - #30 example`n  ${f3}`n- #31 real child" 'row 23: a fence after a - marker holds #30, and its indented closer closes it'
    Assert-Kids '31' "- ~~~text`n  - #30 example`n  ~~~`n- #31 real child" 'row 23: the tilde variant'
    Assert-Kids '31' "1. ${f3}text`n   - #30 example`n   ${f3}`n2. #31 real child" 'row 23: after a 1. marker'
    Assert-Kids '31' "1) ${f3}text`n   - #30 example`n   ${f3}`n2) #31 real child" 'row 23: after a 1) marker'
    Assert-Kids '31' "* ${f3}`n  - #30 example`n  ${f3}`n+ #31 real child" 'row 23: after a * marker'
    Assert-Kids '30,31,32' "- #30 not a fence ${f3}`n- #31 real`n- #32 real" 'row 23 control: a fence mark later in a list line opens nothing'
    Assert-Kids '' "- ${f3}text`n  - #30 inside`n- #31 inside" 'row 23 control: an unclosed list-marker fence swallows what follows'

    # --- row 24 (R3-B3): a hash after . or - is not a bare issue reference ---------------------------------------------
    $ub2 = New-Graph @(@(4, 'needs-ruling', @('x')), @(5, 'needs-ruling', @('y'))) -Leverage '[{"number":4,"count":0,"gates":[]},{"number":5,"count":0,"gates":[]}]'
    foreach ($trig in 'o/r-#5 closes', 'o/r.#5 closes') {
        $r = Invoke-Prog $ub2 @('-Intent', 'unblock', '-BlockedJson', ('[{"number":20,"title":"b","body":"**Unblocks when:** ' + $trig + '"}]'))
        $order = ([regex]::Matches($r.Out, '(?m)^#(\d+) t\d+$') | ForEach-Object { $_.Groups[1].Value }) -join ','
        Assert-Equal '4,5' $order "row 24: '$trig' attributes nothing, so #4 ranks first"
        Assert-Match '(?m)^#5 t5\n  blocked issues whose trigger names it: none\n' $r.Out "row 24: '$trig' names no blocked issue for #5"
    }
    foreach ($trig in '#5 closes', '(#5) closes', 'ruled: #5') {
        $r = Invoke-Prog $ub2 @('-Intent', 'unblock', '-BlockedJson', ('[{"number":20,"title":"b","body":"**Unblocks when:** ' + $trig + '"}]'))
        $order = ([regex]::Matches($r.Out, '(?m)^#(\d+) t\d+$') | ForEach-Object { $_.Groups[1].Value }) -join ','
        Assert-Equal '5,4' $order "row 24 control: '$trig' still attributes to #5"
    }

    # --- row 25 (R3-B4): an unmarked three-member batch prints whole -------------------------------------------------------
    $r = Invoke-Prog $trio @('-Intent', 'batch')
    Assert-Equal 0 $r.Code 'row 25: a three-member batch with no pull request exits 0'
    Assert-Match '(?m)^/ouro:fuse 1 2 3$' $r.Out 'row 25: and prints all three as fuse arguments'

    # --- row 21 (R2-B1): a blocked or open child named by a Fixes line is marked, though the graph never read it -------
    $umbF = '{"number":29,"title":"u","state":"OPEN","labels":[{"name":"umbrella"}],"body":"- #33 d\n- #35 e"}'
    $r = Invoke-Prog $fin @('-Intent', 'finish', '-Target', '29', '-TargetJson', $umbF, '-IssuesJson', $all, '-BlockedJson', $blk) '[{"number":72,"body":"Fixes #33"},{"number":73,"body":"Fixes #35"}]'
    Assert-Equal 0 $r.Code 'row 21: a finish umbrella with in-flight blocked and open children exits 0'
    Assert-Match '(?m)^  #33 t33 -- blocked, in flight, PR #72: \*\*Unblocks when:\*\* #32 is ruled$' $r.Out 'row 21: the blocked child carries the in-flight mark'
    Assert-Match '(?m)^  #35 t35 -- open \[\], in flight, PR #73$' $r.Out 'row 21: and so does the open one'
    Assert-Match '(?m)in flight 0$' $r.Out 'row 21: the header counts graph members only, so it stays 0'
    $r = Invoke-Prog $ub @('-Intent', 'unblock', '-BlockedJson', $bl) '[{"number":72,"body":"Fixes #20"}]'
    Assert-Match '(?m)^#6 t6\n  blocked issues whose trigger names it: #20 \(in flight, PR #72\) #21\n' $r.Out 'row 21: unblock''s blocked column marks a blocked issue a pull request fixes'
    Assert-Match '(?m)in flight 0$' $r.Out 'row 21: and the header count still reads graph members only'
    $r = Invoke-Prog $ub @('-Intent', 'unblock', '-BlockedJson', $bl) '[{"number":72,"body":"Fixes #20"},{"number":73,"body":"Fixes #20"}]'
    Assert-Match '#20 \(in flight, PR #72\) #21' $r.Out 'row 21: with two pull requests the lower number is named'
    $r = Invoke-Prog $ub @('-Intent', 'unblock', '-BlockedJson', $bl)
    Assert-Match '(?m)^#6 t6\n  blocked issues whose trigger names it: #20 #21\n' $r.Out 'row 21 control: with no pull request the blocked column is bare'

    # --- row 18 (B6): -Scope marks what is outside it in unblock and in finish -------------------------------------
    $scopeIssues = '[{"number":5,"title":"t5","state":"OPEN","labels":[{"name":"needs-ruling"},{"name":"skills"}]},{"number":50,"title":"t50","state":"OPEN","labels":[{"name":"agent-ready"},{"name":"gates"}]},{"number":51,"title":"t51","state":"OPEN","labels":[{"name":"agent-ready"},{"name":"skills"}]},{"number":20,"title":"b","state":"OPEN","labels":[{"name":"blocked"},{"name":"gates"}]},{"number":21,"title":"b","state":"OPEN","labels":[{"name":"blocked"},{"name":"skills"}]}]'
    $blS = '[{"number":20,"title":"b","body":"**Unblocks when:** #5"},{"number":21,"title":"b","body":"**Unblocks when:** #5"}]'
    $ubs = New-Graph @(@(5, 'needs-ruling', @('x')), @(50, 'agent-ready', @('x')), @(51, 'agent-ready', @('x'))) -Waves '[[50],[51]]' -Leverage '[{"number":5,"count":2,"gates":[50,51]}]'
    $r = Invoke-Prog $ubs @('-Intent', 'unblock', '-Scope', 'skills', '-Areas', 'skills,gates', '-IssuesJson', $scopeIssues, '-BlockedJson', $blS)
    Assert-Equal 0 $r.Code 'row 18: scoped unblock exits 0'
    Assert-Match '(?m)^#5 t5\n  blocked issues whose trigger names it: #20 \(outside scope\) #21\n  ready work its build would collide with: 2 #50 \(outside scope\) #51$' $r.Out 'row 18: a blocked issue and a gate outside the scope are marked, the ones inside are bare, and both still count'
    $r = Invoke-Prog $ubs @('-Explain', '5', '-Scope', 'skills', '-Areas', 'skills,gates', '-IssuesJson', $scopeIssues, '-BlockedJson', $blS)
    Assert-Match '(?m)^blocked issues whose trigger names it: #20 \(outside scope\) #21$' $r.Out 'row 18: --explain shares the marks'
    $r = Invoke-Prog $ubs @('-Intent', 'unblock', '-BlockedJson', $blS)
    Assert-Match '(?m)^  blocked issues whose trigger names it: #20 #21$' $r.Out 'row 18 control: unscoped, nothing is marked'

    $finS = New-Graph @(@(30, 'agent-ready', @('a')), @(31, 'agent-ready', @('b')), @(32, 'needs-ruling', @('q')), @(35, 'needs-ruling', @('r'))) -Waves '[[30,31]]'
    $umbS = '{"number":29,"title":"u","state":"OPEN","labels":[{"name":"umbrella"}],"body":"- #30 a\n- #31 b\n- #32 c\n- #33 d\n- #34 e\n- #35 f"}'
    $allS = '[{"number":30,"title":"t30","state":"OPEN","labels":[{"name":"agent-ready"},{"name":"skills"}]},{"number":31,"title":"t31","state":"OPEN","labels":[{"name":"agent-ready"},{"name":"gates"}]},{"number":32,"title":"t32","state":"OPEN","labels":[{"name":"needs-ruling"},{"name":"gates"}]},{"number":33,"title":"t33","state":"OPEN","labels":[{"name":"blocked"},{"name":"gates"}]},{"number":34,"title":"t34","state":"OPEN","labels":[{"name":"bug"},{"name":"gates"}]},{"number":35,"title":"t35","state":"OPEN","labels":[{"name":"needs-ruling"},{"name":"skills"}]}]'
    $r = Invoke-Prog $finS @('-Intent', 'finish', '-Target', '29', '-TargetJson', $umbS, '-IssuesJson', $allS, '-BlockedJson', '[]', '-Scope', 'skills', '-Areas', 'skills,gates') '[{"number":9,"body":"Fixes #35"}]'
    Assert-Equal 0 $r.Code 'row 18: a scoped umbrella exits 0'
    Assert-Match '(?m)^  #30 t30 -- agent-ready, wave 1\n  #31 t31 -- agent-ready, outside the scope\n' $r.Out 'row 18: the ready path marks a child outside the scope (as before)'
    Assert-Match '(?m)^  #32 t32 -- needs-ruling, outside the scope\n    blocked issues whose trigger names it: none\n    ready work its build would collide with: 0\n' $r.Out 'row 18: a ruling child outside the scope is marked'
    Assert-Match '(?m)^  #35 t35 -- needs-ruling, in flight, PR #9\n' $r.Out 'row 18: and a ruling child in flight says so'
    Assert-Match '(?m)^  #33 t33 -- blocked, outside the scope: no trigger line$' $r.Out 'row 18: a blocked child outside the scope is marked'
    Assert-Match '(?m)^  #34 t34 -- open \[bug,gates\], outside the scope$' $r.Out 'row 18: so is an open one'
    $r = Invoke-Prog $finS @('-Intent', 'finish', '-Target', '29', '-TargetJson', $umbS, '-IssuesJson', $allS, '-BlockedJson', '[]')
    Assert-NoMatch 'outside the scope' $r.Out 'row 18 control: unscoped, no child is marked outside the scope'
    Assert-Match '(?m)^  #34 t34 -- open \[bug,gates\]$' $r.Out 'row 18 control: and an open child is bare'

    # --- row 19 (B7): finish lists ready children in wave order, then those in no wave by number -------------------------
    $ord = New-Graph @(@(40, 'agent-ready', @()), @(41, 'agent-ready', @('a')), @(42, 'agent-ready', @('b')), @(43, 'agent-ready', @('c'))) -Waves '[[41],[42],[43]]' -Unplaced '[40]'
    $umbO = '{"number":29,"title":"u","state":"OPEN","labels":[{"name":"umbrella"}],"body":"- #43 d\n- #42 c\n- #41 b\n- #40 a"}'
    $allO = '[{"number":40,"title":"t40","state":"OPEN","labels":[]},{"number":41,"title":"t41","state":"OPEN","labels":[]},{"number":42,"title":"t42","state":"OPEN","labels":[]},{"number":43,"title":"t43","state":"OPEN","labels":[]}]'
    $r = Invoke-Prog $ord @('-Intent', 'finish', '-Target', '29', '-TargetJson', $umbO, '-IssuesJson', $allO, '-BlockedJson', '[]') '[{"number":9,"body":"Fixes #43"}]'
    Assert-Equal 0 $r.Code 'row 19: exit 0'
    Assert-Equal '41,42,40,43' (([regex]::Matches($r.Out, '(?m)^  #(\d+) ') | ForEach-Object { $_.Groups[1].Value }) -join ',') 'row 19: the waved children first in wave order, then the one with no footprint and the one in flight by number'

    # --- row 26 (F3): a finish child in flight and outside the scope says both ------------------------------
    $r = Invoke-Prog $finS @('-Intent', 'finish', '-Target', '29', '-TargetJson', $umbS, '-IssuesJson', $allS, '-BlockedJson', '[]', '-Scope', 'skills', '-Areas', 'skills,gates') '[{"number":9,"body":"Fixes #31"}]'
    Assert-Equal 0 $r.Code 'row 26: a scoped umbrella with an in-flight ready child outside the scope exits 0'
    Assert-Match '(?m)^  #31 t31 -- agent-ready, in flight, PR #9, outside the scope$' $r.Out 'row 26: a ready child in flight and outside the scope carries both marks'
    $r = Invoke-Prog $finS @('-Intent', 'finish', '-Target', '29', '-TargetJson', $umbS, '-IssuesJson', $allS, '-BlockedJson', '[]') '[{"number":9,"body":"Fixes #31"}]'
    Assert-Match '(?m)^  #31 t31 -- agent-ready, in flight, PR #9$' $r.Out 'row 26 control: unscoped, the in-flight ready child carries only the flight mark'
}
finally {
    Remove-Item -LiteralPath $scratch.FullName -Recurse -Force
}

if ($failures -gt 0) { Write-Host "$failures failure(s)" -ForegroundColor Red; exit 1 }
Write-Host 'all compile-program cases pass' -ForegroundColor Green
exit 0
