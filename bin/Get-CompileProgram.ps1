<#
.SYNOPSIS
    Renders the footprint analyzer's JSON as a proposed program, in text: waves, fuse batches,
    the rulings that hold the most work, what an umbrella needs, and cleanup candidates.
.DESCRIPTION
    The renderer of /ouro:compile. Read-only: it writes nothing to the tracker and nothing to the
    tree, and it runs no work. It reads the output of Get-FootprintGraph.ps1 (schema 2) from
    standard input, or from -Graph <file> for a replay, and prints one intent, or the account of
    one issue under -Explain. It is deterministic: the same graph and the same reads print
    byte-identical text.

    Reads beside the graph, each by `gh ... -R <repo>` unless the matching parameter hands the JSON
    over (for tests): the open pull requests, always (an issue named by a `Fixes #N` line at the
    start of a pull request's body is in flight); the `blocked` issues, when an intent needs the
    trigger lines; the issues of every state with their labels, when a scope or a `finish` target
    is given; and one issue, for a numeric `finish` target. A failed read exits 1 with the reason,
    and prints nothing on stdout, so a failed read never reads as an empty queue. A read that
    returns as many rows as its --limit prints a warning line, since a row past it is not seen.

    Intents (-Intent, default throughput):
      throughput -- the waves, re-packed in issue-number order into waves of at most -Width (default
                    2, ceiling 4: a larger value is taken as 4, with a line saying so), one line per
                    issue; then the issues whose footprint is empty, then the in-flight issues.
      unblock    -- one row per needs-ruling issue: the open blocked issues whose first line,
                    `**Unblocks when:**`, names it (a `#N` right after a word character, `/`, `.`
                    or `-` is part of another reference, not this one), then the analyzer's
                    leverage count labelled as the ready work its build would collide with. Ranked
                    by the first count, then the second, then the number. The three top rows are
                    named for a sentence of judgment, which the caller writes.
      finish     -- -Target <issue | area>. An umbrella: its children (list items whose first token
                    is an issue number, outside fenced blocks; a fence opens at the start of a line
                    or right after a list marker, and closes on a line of the same character, at
                    least as long as the opening one, with no text after it) with
                    their state, the ready ones in wave order and then those in no wave by number,
                    the blocked ones with their trigger line, the needs-ruling ones with the two
                    unblock columns, a closed one listed as closed. An issue: its wave and
                    the issues in earlier waves it collides with. An area: throughput over the
                    issues carrying that label.
      cleanup    -- each issue's unresolved paths, and the duplicate candidates: pairs whose
                    non-mirrored footprints overlap by at least two thirds of their union, both
                    holding at least three such files, named for the caller to judge from the bodies.
                    One line points at Invoke-BranchSweep.ps1 for branches with no pull request.
      batch      -- the analyzer's batches, in its order, each as /ouro:fuse arguments, with its
                    members' shared files and each collision a member has outside the batch. A
                    batch is a proposed fuse set, not a region: batches are not conflict-free, so
                    they land one after another and /ouro:fuse re-checks each build against the
                    diff. In-flight and out-of-scope members are dropped; a batch left with fewer
                    than two members is dropped; a member's collision with a dropped member of its
                    own batch is printed, marked. -Width does not apply.
    An in-flight issue is dropped from throughput's waves and from batch's fuse commands. Elsewhere
    it is marked `(in flight, PR #n)`: in unblock's rows and columns, cleanup's lines and pairs, a
    collision list or note, a batch collision line, and the batch an -Explain or a finish on an
    issue prints. Finish's own children spell it `in flight, PR #n` after a comma, as they spell
    `outside the scope`, and throughput's in-flight list gives `(PR #n)`. The `(dropped from the
    batch of ...)` note and unblock's `For a sentence of judgment` line print bare numbers. A
    `Fixes` line marks an issue the graph never read, a blocked one, the same way; the `in flight`
    count in the header is of graph issues only.
    -Explain <N> replaces the intent: N's footprint and unresolved paths, every collision (most
    shared files first), its batch, its re-packed wave and the earlier-wave issues it collides
    with, whether it is in flight, and for a needs-ruling N its two unblock columns.

    -Scope <area label> limits every list to the issues carrying it, read before the waves are
    re-packed; a collision, a gate, a blocked issue or a child outside it still counts and is
    marked `(outside scope)` (finish's children: `outside the scope`). The label must be
    one of -Areas (the binding's [labels].area, a JSON array or a comma list). With no area
    declared the scope is refused with a line, and the run goes on unscoped; a label not among the
    declared ones exits 2.

    The output opens with the graph's head, the mirrored files, the counts, the precision line and
    any warning. With no agent-ready issue read it then says the menu is empty and that the next
    action is triage, whatever the intent; the intent's own output follows.

    Before it prints, the script checks its own lists: every in-scope agent-ready issue appears
    once among the waves, the empty-footprint list and the in-flight list; no wave exceeds the
    width; every batch holds five or fewer; and, under `batch`, the pairs it collects for the batch
    lines, each surviving member against an issue outside the surviving members, equal a recount
    from `collisions`; the printed lines are not read back. The first three run before any intent
    prints. A failed check prints nothing on stdout, names the check on stderr and exits 3.

    Exit 0 on output; 1 on a failed read, a graph that is not schema 2 or not JSON; 2 on a usage
    error or a refused argument; 3 on a failed self-check. Needs pwsh 7, and gh unless every read
    is handed over.
.PARAMETER Graph
    A saved Get-FootprintGraph.ps1 output to read in place of standard input.
.PARAMETER Intent
    throughput (default), unblock, finish, cleanup or batch.
.PARAMETER Width
    The largest wave, from 1 to 4. Default 2. Applies to throughput, finish and -Explain.
.PARAMETER Explain
    An issue number: print the account of that issue in place of the intent.
.PARAMETER Target
    For the finish intent: an issue number, an umbrella's number or an area label.
.PARAMETER Scope
    An area label that limits every list.
.PARAMETER Areas
    The binding's [labels].area: a JSON array, or a comma-separated list. Empty when undeclared.
.PARAMETER Repo
    owner/name every gh call names. Default: Get-RepoSlug.ps1 (the binding, else origin).
.PARAMETER PrsJson
    JSON array [{number,body}] of open pull requests, in place of the gh read (for tests).
.PARAMETER BlockedJson
    JSON array [{number,title,body}] of open blocked issues, in place of the gh read (for tests).
.PARAMETER IssuesJson
    JSON array [{number,title,state,labels:[{name}]}] of issues of every state, in place of the gh
    read (for tests).
.PARAMETER TargetJson
    JSON object {number,title,state,body,labels:[{name}]} of the finish target, in place of the gh
    read (for tests).
.PARAMETER Unexpected
    Collects any other argument, so that one is a usage error (exit 2) rather than ignored.
.EXAMPLE
    pwsh -File <ouro>/bin/Get-FootprintGraph.ps1 -Mirrored CHANGELOG.md | pwsh -File <ouro>/bin/Get-CompileProgram.ps1 -Intent batch
#>
param(
    [string]$Graph = '',
    [string]$Intent = 'throughput',
    [string]$Width = '2',
    [string]$Explain = '',
    [string]$Target = '',
    [string]$Scope = '',
    [string]$Areas = '',
    [string]$Repo = '',
    [string]$PrsJson = '',
    [string]$BlockedJson = '',
    [string]$IssuesJson = '',
    [string]$TargetJson = '',
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Unexpected = @()
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)

class UsageError : System.Exception { UsageError([string]$Message) : base($Message) {} }
class CheckError : System.Exception { CheckError([string]$Message) : base($Message) {} }

$script:Out = [System.Collections.Generic.List[string]]::new()
function Say([string]$Line) { $script:Out.Add($Line) }
function Join-Nums($Nums) { (@($Nums) | ForEach-Object { "#$_" }) -join ' ' }

function Read-Gh([string]$What, [string[]]$GhArgs) {
    if (-not $script:Slug) {
        if ($Repo) { $script:Slug = $Repo }
        else {
            . (Join-Path $PSScriptRoot 'Get-RepoSlug.ps1')
            $r = Get-RepoSlug
            if (-not $r.Slug) { throw "no repository to read $What from ($($r.Why)): pass -Repo, or run in a clone whose binding or origin names one" }
            $script:Slug = $r.Slug
        }
    }
    $o = & gh @GhArgs -R $script:Slug 2>&1
    if ($LASTEXITCODE -ne 0) { throw "gh read of $What failed (exit ${LASTEXITCODE}): $(@($o) -join ' ')" }
    (@($o) | Where-Object { $_ -is [string] }) -join "`n"
}
function ConvertTo-Rows([string]$Text, [string]$What) {
    $rows = try { $Text | ConvertFrom-Json -NoEnumerate } catch { $null }
    if ($rows -isnot [array]) { throw "$What is not a JSON array: '$Text'" }
    , @($rows)
}
function Add-LimitWarning($Rows, [int]$Limit, [string]$What) {
    if ($Rows.Count -ge $Limit) { $script:Warnings.Add("warning: $What returned $Limit rows, its --limit: a row past it is not seen") }
}
function Get-Labels($Obj) { @(@($Obj.labels) | ForEach-Object { "$($_.name)" }) }
function Test-HasLabel($Obj, [string]$Name) { [bool](Get-Labels $Obj | Where-Object { $_ -ieq $Name }) }

try {
    $script:Warnings = [System.Collections.Generic.List[string]]::new()
    $script:Slug = ''
    if ($Unexpected) { throw [UsageError]::new("unknown parameter or argument: $($Unexpected -join ' ')") }
    if ($Intent -cnotin 'throughput', 'unblock', 'finish', 'cleanup', 'batch') { throw [UsageError]::new("unknown intent '$Intent': throughput, unblock, finish, cleanup or batch") }
    $w = 0
    if (-not [int]::TryParse($Width, [ref]$w) -or $w -lt 1) { throw [UsageError]::new("-Width is an integer of at least 1, not '$Width'") }
    $widthLine = ''
    if ($w -gt 4) { $widthLine = "--width $w is above the ceiling of 4: taken as 4"; $w = 4 }
    $explainN = 0
    if ($Explain -ne '' -and (-not [int]::TryParse($Explain, [ref]$explainN) -or $explainN -lt 1)) { throw [UsageError]::new("-Explain is an issue number, not '$Explain'") }
    if ($Intent -ceq 'finish' -and $Explain -eq '' -and -not $Target) { throw [UsageError]::new('the finish intent needs -Target <issue|area>') }

    $areaList = @()
    if ($Areas.Trim().StartsWith('[')) { $areaList = @(($Areas | ConvertFrom-Json) | ForEach-Object { "$_" }) }
    elseif ($Areas.Trim()) { $areaList = @($Areas -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }

    # --- the graph ---------------------------------------------------------------------------
    $raw = if ($Graph) {
        if (-not (Test-Path -LiteralPath $Graph -PathType Leaf)) { throw "-Graph file not found: $Graph" }
        [System.IO.File]::ReadAllText((Resolve-Path -LiteralPath $Graph).Path, [System.Text.UTF8Encoding]::new($false))
    } else { [Console]::In.ReadToEnd() }
    $g = try { $raw | ConvertFrom-Json } catch { $null }
    if ($null -eq $g -or $null -eq $g.PSObject.Properties['schema']) { throw 'the graph is not the analyzer output: no JSON object with a schema key' }
    if ("$($g.schema)" -cne '2') { throw "the graph has schema $($g.schema), and this renderer reads schema 2: batches changed meaning at 2" }
    $source = if ($Graph) { "graph file $(Split-Path -Leaf $Graph)" } else { 'analyzer output on standard input' }

    $issueBy = @{}
    foreach ($i in @($g.issues)) { $issueBy[[int]$i.number] = $i }
    $ready = @(@($g.issues) | Where-Object { $_.state -ceq 'agent-ready' } | ForEach-Object { [int]$_.number } | Sort-Object)
    $ruling = @(@($g.issues) | Where-Object { $_.state -ceq 'needs-ruling' } | ForEach-Object { [int]$_.number } | Sort-Object)
    $mirrored = @(@($g.mirrored) | ForEach-Object { "$_" -replace '\\', '/' -replace '^(\./)+', '' })

    $col = @{}
    foreach ($c in @($g.collisions)) {
        foreach ($pair in @(@([int]$c.a, [int]$c.b), @([int]$c.b, [int]$c.a))) {
            if (-not $col.ContainsKey($pair[0])) { $col[$pair[0]] = [System.Collections.Generic.List[object]]::new() }
            $col[$pair[0]].Add([pscustomobject]@{ Other = $pair[1]; Files = @(@($c.files) | ForEach-Object { "$_" }) })
        }
    }
    $batchOf = @{}
    $batches = @()
    $bi = 0
    foreach ($b in @($g.batches)) {
        $members = @(@($b) | ForEach-Object { [int]$_ })
        $batches += , $members
        foreach ($n in $members) { $batchOf[$n] = $bi }
        $bi++
    }

    # --- in flight ---------------------------------------------------------------------------
    $prs = if ($PrsJson) { ConvertTo-Rows $PrsJson '-PrsJson' }
           else { ConvertTo-Rows (Read-Gh 'the open pull requests' @('pr', 'list', '--state', 'open', '--limit', '200', '--json', 'number,body')) 'the open pull request read' }
    Add-LimitWarning $prs 200 'the open pull request read'
    $inFlight = @{}
    $fixes = @{}
    foreach ($p in ($prs | Sort-Object { [int]$_.number })) {
        foreach ($line in ("$($p.body)" -split "`r?`n")) {
            $m = [regex]::Match($line, '^Fixes #(\d+)')
            if ($m.Success) {
                $n = [int]$m.Groups[1].Value
                if (-not $fixes.ContainsKey($n)) { $fixes[$n] = [int]$p.number }
                if ($issueBy.ContainsKey($n) -and -not $inFlight.ContainsKey($n)) { $inFlight[$n] = [int]$p.number }
            }
        }
    }

    # --- scope and the issue list ------------------------------------------------------------
    $scopeLabel = ''
    $finishArea = ''
    $finishNum = 0
    if ($Intent -ceq 'finish' -and $Explain -eq '') {
        if ([int]::TryParse($Target, [ref]$finishNum)) { if ($finishNum -lt 1) { throw [UsageError]::new("-Target is an issue number or an area label, not '$Target'") } }
        else { $finishArea = $Target }
    }
    $wantScope = if ($finishArea) { $finishArea } else { $Scope }
    if ($wantScope) {
        if ($areaList.Count -eq 0) {
            $script:Warnings.Add("warning: no [labels].area is declared, so the scope '$wantScope' is refused and the run goes on unscoped")
            if ($finishArea) { throw [UsageError]::new("the area target '$finishArea' needs a declared [labels].area") }
        } elseif (-not ($areaList | Where-Object { $_ -ieq $wantScope })) {
            throw [UsageError]::new("'$wantScope' is not a declared area: $($areaList -join ', ')")
        } else { $scopeLabel = $wantScope }
    }
    $listRows = $null
    $needList = [bool]$scopeLabel -or ($Intent -ceq 'finish' -and $Explain -eq '' -and $finishNum -gt 0)
    if ($needList) {
        $listRows = if ($IssuesJson) { ConvertTo-Rows $IssuesJson '-IssuesJson' }
                    else { ConvertTo-Rows (Read-Gh 'the issues' @('issue', 'list', '--state', 'all', '--limit', '1000', '--json', 'number,title,state,labels')) 'the issue read' }
        Add-LimitWarning $listRows 1000 'the issue read'
    }
    $listBy = @{}
    if ($listRows) { foreach ($r in $listRows) { $listBy[[int]$r.number] = $r } }
    function Test-InScope([int]$N) {
        if (-not $scopeLabel) { return $true }
        $listBy.ContainsKey($N) -and (Test-HasLabel $listBy[$N] $scopeLabel)
    }
    function Get-Title([int]$N) {
        if ($issueBy.ContainsKey($N)) { return "$($issueBy[$N].title)" }
        if ($listBy.ContainsKey($N)) { return "$($listBy[$N].title)" }
        ''
    }
    function Format-Issue([int]$N) { $t = Get-Title $N; if ($t) { "#$N $t" } else { "#$N" } }
    function Get-Marks([int]$N) {
        $s = ''
        if ($fixes.ContainsKey($N)) { $s += " (in flight, PR #$($fixes[$N]))" }
        if (-not (Test-InScope $N)) { $s += ' (outside scope)' }
        $s
    }
    function Format-Other([int]$N) { "#$N$(Get-Marks $N)" }

    # --- waves, re-packed ----------------------------------------------------------------------
    $waves = [System.Collections.Generic.List[object]]::new()
    foreach ($wv in @($g.waves)) {
        $keep = @(@($wv) | ForEach-Object { [int]$_ } | Where-Object { -not $inFlight.ContainsKey($_) -and (Test-InScope $_) })
        for ($k = 0; $k -lt $keep.Count; $k += $w) {
            $waves.Add([int[]]@($keep[$k..([Math]::Min($k + $w, $keep.Count) - 1)]))
        }
    }
    $waveOf = @{}
    for ($k = 0; $k -lt $waves.Count; $k++) { foreach ($n in $waves[$k]) { $waveOf[$n] = $k + 1 } }
    $unplaced = @(@($g.unplaced) | ForEach-Object { [int]$_ } | Where-Object { -not $inFlight.ContainsKey($_) -and (Test-InScope $_) })
    $flightList = @($inFlight.Keys | Where-Object { Test-InScope $_ } | Sort-Object)

    # --- the unblock columns -------------------------------------------------------------------
    $blockedRows = $null
    function Get-BlockedRows {
        if ($null -ne $script:blockedRows) { return $script:blockedRows }
        $rows = if ($BlockedJson) { ConvertTo-Rows $BlockedJson '-BlockedJson' }
                else { ConvertTo-Rows (Read-Gh 'the blocked issues' @('issue', 'list', '--label', 'blocked', '--state', 'open', '--limit', '200', '--json', 'number,title,body')) 'the blocked issue read' }
        Add-LimitWarning $rows 200 'the blocked issue read'
        $script:blockedRows = $rows
        $rows
    }
    function Get-TriggerLine($Row) {
        $first = ("$($Row.body)" -split "`n")[0].TrimStart([char]0xFEFF).Trim()
        if ($first.StartsWith('**Unblocks when:**', [StringComparison]::Ordinal)) { $first } else { '' }
    }
    function Get-Unblock([int]$N) {
        $names = @(Get-BlockedRows | Where-Object {
                $t = Get-TriggerLine $_
                $t -and [bool]([regex]::Matches($t, '(?<![\w/.-])#(\d+)') | Where-Object { [int]$_.Groups[1].Value -eq $N })
            } | ForEach-Object { [int]$_.number } | Sort-Object)
        $lev = @(@($g.leverage) | Where-Object { [int]$_.number -eq $N })
        $gates = if ($lev.Count) { @(@($lev[0].gates) | ForEach-Object { [int]$_ }) } else { @() }
        [pscustomobject]@{ Blocked = $names; Gates = $gates }
    }
    function Add-UnblockLines([int]$N, [string]$Indent) {
        $u = Get-Unblock $N
        Say "${Indent}blocked issues whose trigger names it: $(if ($u.Blocked.Count) { (@($u.Blocked | ForEach-Object { Format-Other $_ })) -join ' ' } else { 'none' })"
        Say "${Indent}ready work its build would collide with: $($u.Gates.Count)$(if ($u.Gates.Count) { ' ' + ((@($u.Gates | ForEach-Object { Format-Other $_ })) -join ' ') })"
    }

    # --- the sections ----------------------------------------------------------------------------
    function Test-Lists {
        $seen = @(@($waves | ForEach-Object { $_ }) + $unplaced + @($flightList | Where-Object { $issueBy[$_].state -ceq 'agent-ready' })) | Sort-Object
        $want = @($ready | Where-Object { Test-InScope $_ })
        if (($want -join ',') -cne (@($seen) -join ',')) { throw [CheckError]::new("the lists place [$(@($seen) -join ',')] but the analyzer read these in-scope agent-ready issues: [$($want -join ',')]") }
        foreach ($wv in $waves) { if ($wv.Count -gt $w) { throw [CheckError]::new("a wave of $($wv.Count) exceeds the width $w") } }
        foreach ($b in $batches) { if ($b.Count -gt 5) { throw [CheckError]::new("the analyzer's batch $($b -join ',') holds more than five") } }
    }
    function Add-Throughput([int[]]$Only = $null) {
        for ($k = 0; $k -lt $waves.Count; $k++) {
            Say "Wave $($k + 1) ($($waves[$k].Count)):"
            foreach ($n in $waves[$k]) {
                $note = ''
                if ($col.ContainsKey($n)) { foreach ($c in ($col[$n] | Sort-Object Other)) { if ($inFlight.ContainsKey($c.Other) -or -not (Test-InScope $c.Other)) { $note += " [collides with $(Format-Other $c.Other)]" } } }
                Say "  $(Format-Issue $n)$note"
            }
        }
        if ($unplaced.Count) {
            Say 'The footprint says nothing about these:'
            foreach ($n in $unplaced) { Say "  $(Format-Issue $n)" }
        }
        if ($flightList.Count) {
            Say 'In flight (dropped from the waves):'
            foreach ($n in $flightList) { Say "  $(Format-Issue $n) (PR #$($inFlight[$n]))" }
        }
    }
    function Add-Batch {
        $printed = 0
        $got = [System.Collections.Generic.List[string]]::new()
        $want = [System.Collections.Generic.List[string]]::new()
        for ($k = 0; $k -lt $batches.Count; $k++) {
            $all = $batches[$k]
            $mem = @($all | Where-Object { -not $inFlight.ContainsKey($_) -and (Test-InScope $_) })
            $dropped = @($all | Where-Object { $mem -notcontains $_ })
            if ($dropped.Count) { Say "(dropped from the batch of $(Join-Nums $all): $(Join-Nums $dropped), in flight or outside the scope)" }
            if ($mem.Count -lt 2) { continue }
            $printed++
            Say "/ouro:fuse $($mem -join ' ')"
            foreach ($c in @($g.collisions)) {
                if ($mem -contains [int]$c.a -and $mem -contains [int]$c.b) { Say "  shared: #$($c.a) #$($c.b) on $(@($c.files) -join ', ')" }
            }
            foreach ($n in $mem) {
                if (-not $col.ContainsKey($n)) { continue }
                $across = @($col[$n] | Where-Object { $mem -notcontains $_.Other } | ForEach-Object { $_.Other } | Sort-Object)
                $parts = @()
                $kin = @($across | Where-Object { $all -contains $_ })
                $inBatch = @($across | Where-Object { $all -notcontains $_ -and $batchOf.ContainsKey($_) })
                $loose = @($across | Where-Object { -not $batchOf.ContainsKey($_) })
                if ($kin.Count) { $parts += "collides with dropped batch members $((@($kin | ForEach-Object { Format-Other $_ })) -join ', ') ($($kin.Count))" }
                if ($inBatch.Count) { $parts += "collides across batches with $((@($inBatch | ForEach-Object { Format-Other $_ })) -join ', ') ($($inBatch.Count))" }
                if ($loose.Count) { $parts += "collides with unbatched $((@($loose | ForEach-Object { Format-Other $_ })) -join ', ') ($($loose.Count))" }
                if ($parts.Count) { Say "  #$n $($parts -join '; ')" }
                foreach ($o in $across) { $got.Add("$n>$o") }
            }
            Say '  A proposed fuse set, not a region, and not conflict-free: batches land one after another, and /ouro:fuse re-checks each build against the diff.'
        }
        if (-not $printed) { Say 'No batch to print. See throughput.' }
        # Recount from collisions, one side at a time.
        foreach ($c in @($g.collisions)) {
            foreach ($pair in @(@([int]$c.a, [int]$c.b), @([int]$c.b, [int]$c.a))) {
                $x = $pair[0]; $y = $pair[1]
                if (-not $batchOf.ContainsKey($x)) { continue }
                $bx = $batches[$batchOf[$x]]
                $mem = @($bx | Where-Object { -not $inFlight.ContainsKey($_) -and (Test-InScope $_) })
                if ($mem.Count -lt 2 -or $mem -notcontains $x -or $mem -contains $y) { continue }
                $want.Add("$x>$y")
            }
        }
        $gotSet = (@($got | Sort-Object -Unique) -join ',')
        $wantSet = (@($want | Sort-Object -Unique) -join ',')
        if ($gotSet -cne $wantSet) { throw [CheckError]::new("the batch lines name [$gotSet] across batches, and collisions holds [$wantSet]") }
    }
    function Add-Explain([int]$N) {
        if (-not $issueBy.ContainsKey($N)) { throw [UsageError]::new("#$N is not among the issues the analyzer read") }
        $i = $issueBy[$N]
        Say "$(Format-Issue $N) [$($i.state)]"
        Say "footprint: $(if (@($i.footprint).Count) { @($i.footprint) -join ', ' } else { 'none' })"
        Say "unresolved: $(if (@($i.unresolved).Count) { @($i.unresolved) -join ', ' } else { 'none' })"
        Say 'collisions (most shared files first):'
        $cs = if ($col.ContainsKey($N)) { @($col[$N] | Sort-Object @{ Expression = { $_.Files.Count }; Descending = $true }, Other) } else { @() }
        foreach ($c in $cs) { Say "  $(Format-Other $c.Other) ($($c.Files.Count)): $($c.Files -join ', ')" }
        if (-not $cs.Count) { Say '  none' }
        Say "batch: $(if ($batchOf.ContainsKey($N)) { (@($batches[$batchOf[$N]] | ForEach-Object { Format-Other $_ })) -join ' ' } else { 'none' })"
        if ($inFlight.ContainsKey($N)) { Say "wave: none (in flight)" }
        elseif ($waveOf.ContainsKey($N)) {
            $wn = $waveOf[$N]
            Say "wave: $wn"
            for ($k = 1; $k -lt $wn; $k++) {
                $others = @($cs | ForEach-Object { $_.Other })
                $hit = @($waves[$k - 1] | Where-Object { $others -contains $_ })
                Say "  earlier wave ${k}: collides with $(if ($hit.Count) { Join-Nums $hit } else { 'none' })"
            }
        }
        elseif ($unplaced -contains $N) { Say 'wave: none (empty footprint)' }
        else { Say "wave: none ($(if ($i.state -ceq 'needs-ruling') { 'not agent-ready' } else { 'outside the scope' }))" }
        Say "in flight: $(if ($inFlight.ContainsKey($N)) { "yes, PR #$($inFlight[$N])" } else { 'no' })"
        if ($i.state -ceq 'needs-ruling') { Add-UnblockLines $N '' }
    }
    function Get-Rows {
        $rows = foreach ($n in $ruling) {
            if (-not (Test-InScope $n)) { continue }
            $u = Get-Unblock $n
            [pscustomobject]@{ N = $n; B = $u.Blocked.Count; G = $u.Gates.Count }
        }
        @($rows | Sort-Object @{ Expression = { $_.B }; Descending = $true }, @{ Expression = { $_.G }; Descending = $true }, N)
    }
    function Add-Unblock {
        $rows = @(Get-Rows)
        if (-not $rows.Count) { Say 'No needs-ruling issue to rank.' }
        foreach ($r in $rows) { Say "$(Format-Issue $r.N)$(Get-Marks $r.N)"; Add-UnblockLines $r.N '  ' }
        if ($rows.Count) { Say "For a sentence of judgment each, from the issue's own question: $(Join-Nums ($rows | Select-Object -First 3 | ForEach-Object { $_.N }))" }
    }
    function Add-Cleanup {
        Say 'unresolved paths:'
        $k = 0
        foreach ($i in @($g.issues)) {
            if (-not (Test-InScope ([int]$i.number))) { continue }
            if (@($i.unresolved).Count) { Say "  $(Format-Other ([int]$i.number)): $(@($i.unresolved) -join ', ')"; $k++ }
        }
        if (-not $k) { Say '  none' }
        Say 'duplicate candidates (non-mirrored files overlap by at least two thirds of their union, both with three or more):'
        $rows = @(@($g.issues) | Where-Object { Test-InScope ([int]$_.number) } | ForEach-Object {
                [pscustomobject]@{ N = [int]$_.number; F = @(@($_.footprint) | Where-Object { $mirrored -cnotcontains "$_" } | ForEach-Object { "$_" }) }
            })
        $k = 0
        for ($x = 0; $x -lt $rows.Count; $x++) {
            for ($y = $x + 1; $y -lt $rows.Count; $y++) {
                $a = $rows[$x]; $b = $rows[$y]
                if ($a.F.Count -lt 3 -or $b.F.Count -lt 3) { continue }
                $inter = @($a.F | Where-Object { $b.F -ccontains $_ }).Count
                $union = $a.F.Count + $b.F.Count - $inter
                if (3 * $inter -ge 2 * $union) { Say "  $(Format-Other $a.N) $(Format-Other $b.N) share $inter of $union files"; $k++ }
            }
        }
        if (-not $k) { Say '  none' } else { Say 'Read both bodies of each pair: "one deliverable" or "siblings", with the sentence that decides it.' }
        Say 'Branches and worktrees with no pull request: Invoke-BranchSweep.ps1 reports them (not run here).'
    }
    function Add-Finish {
        if ($finishArea) { Add-Throughput; return }
        $t = if ($TargetJson) { $TargetJson | ConvertFrom-Json }
             else { Read-Gh "issue #$finishNum" @('issue', 'view', "$finishNum", '--json', 'number,title,state,body,labels') | ConvertFrom-Json }
        if (-not (Test-HasLabel $t 'umbrella')) {
            if ($issueBy.ContainsKey($finishNum)) { Add-Explain $finishNum }
            else { Say "$(Format-Issue $finishNum) is $("$($t.state)".ToLowerInvariant()) and carries no umbrella label; the analyzer did not read it." }
            return
        }
        $kids = [System.Collections.Generic.List[int]]::new()
        $fenceChar = ''
        $fenceLen = 0
        foreach ($line in ("$($t.body)" -split "`r?`n")) {
            $fm = [regex]::Match($line, '^\s*(`{3,}|~{3,})(.*)$')
            $fo = [regex]::Match($line, '^\s*(?:(?:[-*+]|\d+[.)])\s+)?(`{3,}|~{3,})(.*)$')
            if ($fenceChar) {
                if ($fm.Success -and $fm.Groups[1].Value[0] -ceq $fenceChar -and $fm.Groups[1].Value.Length -ge $fenceLen -and -not $fm.Groups[2].Value.Trim()) { $fenceChar = '' }
                continue
            }
            if ($fo.Success -and -not ($fo.Groups[1].Value[0] -ceq '`' -and $fo.Groups[2].Value.Contains('`'))) { $fenceChar = $fo.Groups[1].Value[0]; $fenceLen = $fo.Groups[1].Value.Length; continue }
            $m = [regex]::Match($line, '^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?#(\d+)')
            if ($m.Success -and -not $kids.Contains([int]$m.Groups[1].Value)) { $kids.Add([int]$m.Groups[1].Value) }
        }
        Say "Umbrella #$finishNum $($t.title): $($kids.Count) children"
        $rank = foreach ($n in $kids) {
            $st = if ($listBy.ContainsKey($n)) { "$($listBy[$n].state)".ToUpperInvariant() } else { '' }
            $kind = if ($st -ceq 'CLOSED') { 3 } elseif ($issueBy.ContainsKey($n) -and $issueBy[$n].state -ceq 'agent-ready') { 0 } elseif ($issueBy.ContainsKey($n)) { 1 } else { 2 }
            $pos = if ($waveOf.ContainsKey($n)) { $waveOf[$n] } else { [int]::MaxValue }
            [pscustomobject]@{ N = $n; Kind = $kind; Pos = $pos; St = $st }
        }
        foreach ($r in ($rank | Sort-Object Kind, Pos, N)) {
            $n = $r.N
            if ($r.St -ceq 'CLOSED') { Say "  $(Format-Issue $n) -- closed"; continue }
            if (-not $listBy.ContainsKey($n)) { Say "  #$n -- state not read"; continue }
            $labs = Get-Labels $listBy[$n]
            $tail = "$(if ($fixes.ContainsKey($n)) { ", in flight, PR #$($fixes[$n])" })$(if (-not (Test-InScope $n)) { ', outside the scope' })"
            if ($issueBy.ContainsKey($n) -and $issueBy[$n].state -ceq 'agent-ready') {
                $where = if ($inFlight.ContainsKey($n)) { "in flight, PR #$($inFlight[$n])$(if (-not (Test-InScope $n)) { ', outside the scope' })" } elseif ($waveOf.ContainsKey($n)) { "wave $($waveOf[$n])" } elseif ($unplaced -contains $n) { 'no footprint' } else { 'outside the scope' }
                Say "  $(Format-Issue $n) -- agent-ready, $where"
            } elseif ($issueBy.ContainsKey($n)) {
                Say "  $(Format-Issue $n) -- needs-ruling$tail"; Add-UnblockLines $n '    '
            } elseif ($labs | Where-Object { $_ -ieq 'blocked' }) {
                $row = @(Get-BlockedRows | Where-Object { [int]$_.number -eq $n })
                $trig = if ($row.Count) { Get-TriggerLine $row[0] } else { '' }
                Say "  $(Format-Issue $n) -- blocked${tail}: $(if ($trig) { $trig } else { 'no trigger line' })"
            } else { Say "  $(Format-Issue $n) -- open [$($labs -join ',')]$tail" }
        }
    }

    # --- the output --------------------------------------------------------------------------------
    Test-Lists
    if ($Explain -ne '') { Add-Explain $explainN }
    else {
        switch ($Intent) {
            'throughput' { Add-Throughput }
            'unblock' { Add-Unblock }
            'cleanup' { Add-Cleanup }
            'batch' { Add-Batch }
            'finish' { Add-Finish }
        }
    }
    $body = $script:Out
    $script:Out = [System.Collections.Generic.List[string]]::new()
    Say "compile: $source; head $("$($g.head)".Substring(0, [Math]::Min(12, "$($g.head)".Length))) (the paths were resolved there)"
    Say "mirrored files passed: $(if ($mirrored.Count) { $mirrored -join ', ' } else { 'none' })"
    Say "issues read $(@($g.issues).Count); agent-ready $($ready.Count); needs-ruling $($ruling.Count); in flight $($inFlight.Count)"
    Say 'Precision: footprints are read from the files the issues'' text names (anchors and the `Doc impact on close` line), not from a build, and the figures were measured on the anchor paths, before root files joined the footprint, in historical replays over closed issues, not measurements of this queue, and are quoted from the analyzer''s help: the footprint less its Doc-impact files over 160 landed issues: mean recall 0.83 against the files the landing touched, and 3.7% of same-week pairs with disjoint footprints touched a common file; the batch rule, replayed over three queues of closed issues: pair precision 0.61 against a 0.29 base rate, and 0.18 of the colliding pairs held. A proposal, never a guarantee: re-check against the diff after each build, as /ouro:fuse step 2 does.'
    if ($scopeLabel) { Say "scope: issues carrying '$scopeLabel'" }
    if ($widthLine) { Say $widthLine }
    foreach ($x in $script:Warnings) { Say $x }
    Say 'A branch with no pull request is not seen.'
    if ($ready.Count -eq 0) { Say 'The `agent-ready` menu is empty: no `agent-ready` issue was read. As contract section 10 says, the next action is triage, not idling.' }
    Say ''
    foreach ($x in $body) { Say $x }
    [Console]::Out.Write(($script:Out -join "`n") + "`n")
    exit 0
}
catch [UsageError] { [Console]::Error.WriteLine("usage: $($_.Exception.Message)"); exit 2 }
catch [CheckError] { [Console]::Error.WriteLine("Get-CompileProgram: self-check failed: $($_.Exception.Message)"); exit 3 }
catch { [Console]::Error.WriteLine("Get-CompileProgram: $($_.Exception.Message)"); exit 1 }
