<#
.SYNOPSIS
    Test of Invoke-LandBatch.ps1 over scratch repositories and the gh stand-in.
.DESCRIPTION
    The script is dot-sourced and its function called per row with the subcommand; the fixture
    beside this file holds the stand-in and builds each repository: a main checkout on master, a
    bare origin, and feature branches pushed there -- two clean ones, one stacked on the first, one
    conflicting with the first, one with two authors. A stubbed `pr merge` moves origin's master
    to the local squash, the way GitHub's squash-merge lands the same tree.

    Pinned, subcommand by subcommand: `check` reads the merge answer, the rulesets, classic
    protection, the merge queue and the squash setting, and ends with the verdict the table gives
    each case, refusing a merge answer, a merge queue and refused squashes; `start` clears the
    batch files; `add` records a squash with the range's author and the message, and refuses a
    subject without the number, a head that is not origin's tip, a base outside the batch, two
    authors, a second squash of the same PR, and leaves a conflict in the tree; `record` commits
    a resolution and routes the set; `stacked` applies a stacked PR's own diff and routes;
    `amend` and `route` write the route, the latter adding a `merge=` path two squashes change;
    `land` lands one PR per call with the head pinned and the squash's subject and body, checks
    the merge commit's parent and tree, refuses a routed or stopped batch, and stops -- writing
    the stop file -- on a moved tip, a HEAD past the last squash, a PR not MERGEABLE or a draft, an
    unanswered thread, a PR not MERGED, and a parent or tree mismatch; `report` prints the stop
    and origin's log; `reset` refuses while a commit sits after the last squash, else resets;
    `push` checks every head, draft and thread, pushes once and reads origin back, naming a
    non-fast-forward; `close` closes a PR only for a squash on origin and an unmoved head;
    `watch` hands the landed merges, origin's tip, or none to the watch; `worktree` adds the batch
    worktree and refuses an unignored path. The child rows pin the entry point.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'LandScripts.Fixture.ps1')
$Script = Find-Script 'Invoke-LandBatch.ps1'
. $Script
$script:polls = 0
function Wait-Poll { param([int]$Seconds) $script:polls += $Seconds }

function Msg([int]$Pr, [string]$Subject = "feat: change $Pr (#$Pr)") {
    $f = Join-Path $Scratch "msg-$Pr-$([guid]::NewGuid().ToString('N').Substring(0, 6)).txt"
    [System.IO.File]::WriteAllText($f, "$Subject`n`nCloses #$Pr`n", $Utf8)
    $f
}
function View([string]$Head, [string]$Base = 'master', [string]$Mergeable = 'MERGEABLE', [string]$Status = 'CLEAN',
    [bool]$Draft = $false, [string]$State = 'OPEN', [string]$MergeOid = '') {
    $v = [ordered]@{ headRefOid = $Head; baseRefName = $Base; mergeable = $Mergeable; mergeStateStatus = $Status
        isDraft = $Draft; state = $State; mergeCommit = $null }
    if ($MergeOid) { $v.mergeCommit = @{ oid = $MergeOid } }
    $v
}
function Thread([string]$Login) { @{ isResolved = $false; comments = @{ totalCount = 1; nodes = @(@{ author = @{ login = $Login }; path = 'a.ps1'; url = 'https://x/1' }) } } }
function Page([object[]]$Threads) { @{ data = @{ repository = @{ pullRequest = @{ author = @{ login = 'me' }; reviewThreads = @{ pageInfo = @{ hasNextPage = $false; endCursor = $null }; nodes = @($Threads) } } } } } }
$NoThreads = Answer @(Page @())
function Set-Lines([string]$Dir, [string]$File, [string[]]$Lines, [string]$Message, [string]$Author = 'Ann <ann@x.test>') {
    [System.IO.File]::WriteAllText((Join-Path $Dir $File), (($Lines -join "`n") + "`n"), $Utf8)
    $null = G $Dir add -- $File
    $null = G $Dir commit -q --author=$Author -m $Message
    @(G $Dir rev-parse HEAD)[0]
}
# master: shared.txt (ten lines) and notes.txt; branches pushed to origin:
#   feat/a   (pull request 1) changes shared.txt l1, appends to notes.txt
#   feat/b   (pull request 2) adds b.txt (and, with the union driver, appends to notes.txt)
#   feat/c   (pull request 3) stacked on feat/a: adds c.txt
#   feat/d   (pull request 4) changes shared.txt l1 another way: conflicts with feat/a
#   feat/two (pull request 5) two authors
function New-BatchWorld([string]$Name, [bool]$Union = $false, [string]$Landing = '') {
    $w = New-World $Name -Landing $Landing
    $m = $w.Main
    $ten = @(1..10 | ForEach-Object { "l$_" })
    if ($Union) {
        [System.IO.File]::WriteAllText((Join-Path $m '.gitattributes'), "notes.txt merge=union`n", $Utf8)
        $null = G $m add .gitattributes
        $null = G $m commit -q -m attributes
    }
    $null = Set-Lines $m 'shared.txt' $ten 'shared'
    $null = Set-Lines $m 'notes.txt' @('n1') 'notes'
    $null = G $m push -q origin master
    $w.Base = @(G $m rev-parse master)[0]
    $null = G $m checkout -q -b feat/a master
    $aLines = @($ten); $aLines[0] = 'A1'
    $null = Set-Lines $m 'shared.txt' $aLines 'a: l1'
    $w.A = Set-Lines $m 'notes.txt' @('n1', 'a') 'a: notes'
    $null = G $m checkout -q -b feat/c feat/a
    $w.C = Set-Lines $m 'c.txt' @('c') 'c: stacked'
    $null = G $m checkout -q -b feat/b master
    $w.B = Set-Lines $m 'b.txt' @('b') 'b: file'
    # Only with the union driver does feat/b touch notes.txt too: two appends to one tail conflict otherwise.
    if ($Union) { $w.B = Set-Lines $m 'notes.txt' @('n1', 'b') 'b: notes' }
    $null = G $m checkout -q -b feat/d master
    $dLines = @($ten); $dLines[0] = 'D1'
    $w.D = Set-Lines $m 'shared.txt' $dLines 'd: l1 another way'
    $null = G $m checkout -q -b feat/two master
    $null = Set-Lines $m 'two.txt' @('t') 'two: ann'
    $w.Two = Set-Lines $m 'two2.txt' @('t') 'two: bob' 'Bob <bob@y.test>'
    $null = G $m checkout -q master
    foreach ($br in 'feat/a', 'feat/b', 'feat/c', 'feat/d', 'feat/two') { $null = G $m push -q origin $br }
    $null = G $m fetch -q origin
    foreach ($br in 'feat/a', 'feat/b', 'feat/c', 'feat/d', 'feat/two') { $null = G $m branch -q -D $br }
    $w
}
# Each gh call as its verb and subject: `pr view 1`, `api graphql 2`, `pr list`.
function Calls([string[]]$Gh) {
    @($Gh | ForEach-Object {
            $a = $_ -split ' '
            if ($a[0] -eq 'pr' -and $a[1] -in 'view', 'merge', 'close') { "$($a[0]) $($a[1]) $($a[2])" }
            elseif ($a[0] -eq 'api' -and $a[1] -eq 'graphql' -and $_ -match '-F number=(\d+)') { "api graphql $($Matches[1])" }
            else { "$($a[0]) $($a[1])" }
        }) -join ','
}
function Rows([string]$Main) {
    $f = Join-Path (@(G $Main rev-parse --absolute-git-dir)[0]) 'land-batch.tsv'
    if (Test-Path -LiteralPath $f) { @([System.IO.File]::ReadAllLines($f) | Where-Object { $_ }) } else { @() }
}
function BatchFile([string]$Main, [string]$Name) {
    $f = Join-Path (@(G $Main rev-parse --absolute-git-dir)[0]) "land-batch.$Name"
    if (Test-Path -LiteralPath $f) { @([System.IO.File]::ReadAllLines($f) | Where-Object { $_ }) -join ';' } else { '' }
}
# The table a clean batch of pull requests 1 and 2 reads, with any key replaced.
function Table($w, [hashtable]$Over = @{}) {
    $t = @{ 'pr view 1' = Answer (View $w.A); 'pr view 2' = Answer (View $w.B); 'pr view 3' = Answer (View $w.C -Base feat/a)
        'pr view 4' = Answer (View $w.D); 'pr view 5' = Answer (View $w.Two)
        'pr list --base feat/a' = Answer @(); 'pr list --base feat/b' = Answer @(); 'pr list --base feat/c' = Answer @()
        'api graphql 1' = $NoThreads; 'api graphql 2' = $NoThreads; 'api graphql 3' = $NoThreads
        'pr merge 1' = @{ Exit = 0; Out = '' }; 'pr merge 2' = @{ Exit = 0; Out = '' }
        'pr close 1' = @{ Exit = 0; Out = '' }; 'pr close 2' = @{ Exit = 0; Out = '' }; 'pr close 3' = @{ Exit = 0; Out = '' } }
    foreach ($k in $Over.Keys) { $t[$k] = $Over[$k] }
    $t
}
# The branch-rules answers of a branch with nothing on it.
$Clear = @{ 'api repos/o/n/rules/branches/master' = Answer @()
    'api repos/o/n/branches/master' = Answer @{ name = 'master'; protection = @{ enabled = $false } }
    'api graphql' = Answer @{ data = @{ repository = @{ mergeQueue = $null } } }
    'api repos/o/n' = Answer @{ allow_squash_merge = $true } }
function Rules([hashtable]$Over = @{}) { $t = $Clear.Clone(); foreach ($k in $Over.Keys) { $t[$k] = $Over[$k] }; $t }

Write-Host 'check: the readings and the verdict, case by case'
$w = New-BatchWorld 'wc'
Push-Location -LiteralPath $w.Main
try {
    Set-Gh (Rules)
    $r = Invoke-Step { Invoke-LandBatch check }
    Assert-Equal '' $r.Stop 'a clear branch is no stop'
    Assert-Equal 'ship.merge: not declared - the batch lands squash commits, as a binding without the key does,rules: pull_request 0, status_checks 0, strict_checks 0, merge_queue 0, squash_refused 0,classic protection: off,merge queue: False,allow_squash_merge: True,verdict: land (5a) or push (5b)' ($r.Lines -join ',') 'every reading and the open verdict'
    Assert-Equal 'api repos/o/n/rules/branches/master,api repos/o/n/branches/master,api graphql -f query=query($owner: String!, $name: String!, $branch: String!) { repository(owner: $owner, name: $name) { mergeQueue(branch: $branch) { url } } } -f owner=o -f name=n -f branch=master,api repos/o/n' ($r.Gh -join ',') 'the four reads, each naming the repository'
    Set-Gh (Rules @{ 'api repos/o/n/rules/branches/master' = Answer @(@{ type = 'pull_request'; parameters = @{ allowed_merge_methods = @('squash', 'merge') } }, @{ type = 'required_status_checks'; parameters = @{ strict_required_status_checks_policy = $false } }) })
    $r = Invoke-Step { Invoke-LandBatch check }
    Assert-Match '^verdict: land \(5a\) only - a ruleset requires pull requests or status checks' $r.Lines[-1] 'required pull requests or checks: 5a only'
    Set-Gh (Rules @{ 'api repos/o/n/rules/branches/master' = Answer @(@{ type = 'required_status_checks'; parameters = @{ strict_required_status_checks_policy = $true } }) })
    $r = Invoke-Step { Invoke-LandBatch check }
    Assert-Match '^verdict: a set of two or more PRs goes to /ouro:land per PR - a ruleset requires branches to be up to date' $r.Lines[-1] 'strict checks: per PR for two or more'
    Set-Gh (Rules @{ 'api repos/o/n/rules/branches/master' = Answer @(@{ type = 'merge_queue'; parameters = @{} }) })
    $r = Invoke-Step { Invoke-LandBatch check }
    Assert-Match '^a merge queue guards master - gh pr merge enqueues' $r.Stop 'a merge-queue rule refuses'
    Set-Gh (Rules @{ 'api graphql' = Answer @{ data = @{ repository = @{ mergeQueue = @{ url = 'https://x/q' } } } } })
    $r = Invoke-Step { Invoke-LandBatch check }
    Assert-Match '^a merge queue guards master' $r.Stop 'a merge queue seen through GraphQL refuses'
    Set-Gh (Rules @{ 'api repos/o/n/rules/branches/master' = Answer @(@{ type = 'pull_request'; parameters = @{ allowed_merge_methods = @('merge') } }) })
    $r = Invoke-Step { Invoke-LandBatch check }
    Assert-Match '^a ruleset on master refuses squash merges' $r.Stop 'a ruleset without squash refuses'
    Set-Gh (Rules @{ 'api repos/o/n' = Answer @{ allow_squash_merge = $false } })
    $r = Invoke-Step { Invoke-LandBatch check }
    Assert-Match '^the repository refuses squash merges \(allow_squash_merge false\)' $r.Stop 'the repository setting refuses'
    Set-Gh (Rules @{ 'api repos/o/n/rules/branches/master' = @{ Exit = 1; Out = 'HTTP 403: Upgrade to GitHub Pro or make this repository public to enable this feature' } })
    $r = Invoke-Step { Invoke-LandBatch check }
    Assert-Line $r.Lines 'rules: not on this plan - nothing to find' 'a plan-gated 403 is nothing to find'
    Assert-Equal 'verdict: land (5a) or push (5b)' $r.Lines[-1] 'and the verdict stays open'
    Set-Gh (Rules @{ 'api repos/o/n/rules/branches/master' = @{ Exit = 1; Out = 'HTTP 500' } })
    $r = Invoke-Step { Invoke-LandBatch check }
    Assert-Match '^verdict: per PR through /ouro:land - the batch cannot tell whether a merge queue or a required pull request is there, and landing blind is not safe: the rules read failed \(exit 1\): HTTP 500' $r.Lines[-1] 'a rules read that fails sends the set to /ouro:land per PR'
    Set-Gh (Rules @{ 'api repos/o/n/branches/master' = Answer @{ name = 'master'; protection = @{ enabled = $true; required_status_checks = @{ contexts = @('ci') } } }
            'api repos/o/n/branches/master/protection' = @{ Exit = 1; Out = 'HTTP 404: Not Found' } })
    $r = Invoke-Step { Invoke-LandBatch check }
    Assert-Line $r.Lines 'classic protection: enabled, status checks 1' 'classic protection is read from the branch'
    Assert-Line $r.Lines 'classic protection details: did not read (admin access) - up-to-date branches, pull requests and checks are all treated as required' 'unreadable details are treated as all three'
    Assert-Match '^verdict: a set of two or more PRs goes to /ouro:land per PR - classic protection whose details did not read' $r.Lines[-1] 'and the verdict is per PR for two or more'
    Set-Gh (Rules @{ 'api repos/o/n/branches/master' = Answer @{ name = 'master'; protection = @{ enabled = $true; required_status_checks = $null } }
            'api repos/o/n/branches/master/protection' = Answer @{ required_status_checks = @{ strict = $false }; required_pull_request_reviews = @{ dismiss_stale_reviews = $false } } })
    $r = Invoke-Step { Invoke-LandBatch check }
    Assert-Line $r.Lines 'classic protection details: strict False, pull requests True' 'the details read'
    Assert-Match '^verdict: land \(5a\) only - classic protection requires pull requests' $r.Lines[-1] 'required pull requests: 5a only'
}
finally { Pop-Location }
$wm = New-World 'wm' -Landing 'merge-merge'
Push-Location -LiteralPath $wm.Main
try {
    Set-Gh (Rules)
    $r = Invoke-Step { Invoke-LandBatch check }
    Assert-Match "^ship.merge: merge - both of this drill's paths land squash commits only" $r.Stop 'a merge answer refuses the batch'
    Assert-Equal 0 $r.Gh.Count 'before any read'
}
finally { Pop-Location }

Write-Host 'add, route, land twice, reset: the 5a drill on #1 and #2'
$w = New-BatchWorld 'w5a'; $m = $w.Main
Push-Location -LiteralPath $m
try {
    Set-Gh (Table $w)
    $r = Invoke-Step { Invoke-LandBatch start }
    Assert-Equal 'batch started: the previous batch files are gone' ($r.Lines -join ';') 'start reports'
    $r = Invoke-Step { Invoke-LandBatch route }
    Assert-Equal 'no batch recorded - step 2' $r.Stop 'route with no batch stops'
    $r = Invoke-Step { Invoke-LandBatch add -Pr 1 -Branch feat/a -Message (Msg 1) }
    Assert-Equal '' $r.Stop 'add #1 goes through'
    $s1 = @(G $m rev-parse HEAD)[0]
    Assert-Equal "#1 squashed as $s1 (row 1)" ($r.Lines -join ';') 'the squash is reported'
    Assert-Equal "1`tfeat/a`t$($w.A)`t$s1" @(Rows $m)[0] 'the row holds the PR, the branch, the head and the squash'
    Assert-Equal 'Ann <ann@x.test>' (@(G $m log -1 --format='%aN <%aE>')[0]) 'the squash carries the range''s author'
    Assert-Equal 'feat: change 1 (#1)' (@(G $m log -1 --format=%s)[0]) 'and the message''s subject'
    Assert-Equal 'Closes #1' (@(G $m log -1 --format=%b)[0]) 'and its body'
    Assert-Equal $w.Base (@(G $m rev-parse 'HEAD^')[0]) 'the first squash sits on the pre-batch tip'
    Assert-Line $r.Gh 'pr view 1 -R o/n --json headRefOid,baseRefName' 'the head and base are read'
    $r = Invoke-Step { Invoke-LandBatch add -Pr 1 -Branch feat/a -Message (Msg 1) }
    Assert-Equal '#1 adds nothing on top of the batch - already landed or named twice' $r.Stop 'the same PR twice adds nothing'
    Assert-Equal 1 @(Rows $m).Count 'and no row'
    $r = Invoke-Step { Invoke-LandBatch add -Pr 2 -Branch feat/b -Message (Msg 2) }
    Assert-Equal '' $r.Stop 'add #2 goes through'
    $s2 = @(G $m rev-parse HEAD)[0]
    Assert-Equal 2 @(Rows $m).Count 'two rows'
    Assert-Equal 'b' ((Get-Content -LiteralPath (Join-Path $m 'b.txt')) -join ',') 'the second squash is in the composed tree'
    $r = Invoke-Step { Invoke-LandBatch route }
    Assert-Equal 'route: 5a' ($r.Lines -join ';') 'no route line: 5a'
    Assert-Equal '' (BatchFile $m route) 'and the route file is empty'

    # GitHub's squash lands the validated tree: the stubbed merge pushes the squash to origin's master.
    Set-Gh (Table $w @{ 'pr view 1' = Seq @((Answer (View $w.A)), (Answer (View $w.A)), (Answer (View $w.A -State MERGED -MergeOid $s1)))
            'pr merge 1' = @{ Exit = 0; Out = ''; Run = "git -C '$m' push -q origin ${s1}:refs/heads/master" }
            'pr view 2' = Seq @((Answer (View $w.B)), (Answer (View $w.B)), (Answer (View $w.B -State MERGED -MergeOid $s2)))
            'pr merge 2' = @{ Exit = 0; Out = ''; Run = "git -C '$m' push -q origin ${s2}:refs/heads/master" } })
    $r = Invoke-Step { Invoke-LandBatch land }
    Assert-Equal '' $r.Stop 'land #1 goes through'
    Assert-Equal "#1 landed as $s1 (1 of 2)" $r.Lines[-1] 'the landing is reported'
    Assert-Equal $s1 (@(G $w.Origin rev-parse master)[0]) 'origin''s master is the first squash'
    Assert-Line $r.Gh "pr merge 1 -R o/n --squash --match-head-commit $($w.A) --delete-branch --subject feat: change 1 (#1) --body-file $(Join-Path (@(G $m rev-parse --absolute-git-dir)[0]) 'land-batch.body')" 'the merge pins the head, squashes, deletes the branch and carries the squash''s subject and body'
    Assert-Equal "1`t$s1" (BatchFile $m landed) 'the landed file holds the PR and its merge commit'
    Assert-Equal 'pr view 1,pr view 2,pr view 1,api graphql 1,api graphql 2,pr list,pr merge 1,pr view 1' (Calls $r.Gh) 'the first call reads every PR before anything lands, then the threads of every PR still to land, the based-on count, the merge and the read-back'
    $r = Invoke-Step { Invoke-LandBatch land }
    Assert-Equal '' $r.Stop 'land #2 goes through'
    Assert-Equal "#2 landed as $s2 (2 of 2)" $r.Lines[-1] 'the second landing is reported'
    Assert-Equal $s2 (@(G $w.Origin rev-parse master)[0]) 'origin''s master is the second squash'
    Assert-NoMatch 'pr view 1' ($r.Gh -join "`n") 'the second call reads only what is still to land'
    $r = Invoke-Step { Invoke-LandBatch land }
    Assert-Equal 'every recorded PR has landed - run reset' $r.Stop 'a third call says the batch is done'
    $r = Invoke-Step { Invoke-LandBatch watch }
    Assert-Equal "watching the merges land recorded: $s1 $s2" $r.Lines[0] 'watch takes the landed merge commits'
    Assert-Match "^cannot list the runs of $s1" $r.Stop 'and hands them to the watch, which asks for their runs'
    $r = Invoke-Step { Invoke-LandBatch reset }
    Assert-Equal '' $r.Stop 'reset goes through'
    Assert-Equal $s2 (@(G $m rev-parse HEAD)[0]) 'local master is origin''s, which is tree-equal to the last squash'
    Assert-Equal '' (BatchFile $m stop) 'no stop was written'
}
finally { Pop-Location }

Write-Host 'land: the refusals and the stops, each written to the stop file'
$w = New-BatchWorld 'w5s'; $m = $w.Main
Push-Location -LiteralPath $m
try {
    Set-Gh (Table $w)
    $r = Invoke-Step { Invoke-LandBatch land }
    Assert-Equal 'no batch recorded - step 2' $r.Stop 'no batch stops'
    $null = Invoke-Step { Invoke-LandBatch start }
    $null = Invoke-Step { Invoke-LandBatch add -Pr 1 -Branch feat/a -Message (Msg 1) }
    $s1 = @(G $m rev-parse HEAD)[0]
    $null = Invoke-Step { Invoke-LandBatch amend -Note 'a comment fixed' }
    $r = Invoke-Step { Invoke-LandBatch land }
    Assert-Equal "this batch routes to 5b, not 5a:`namended after step 2: a comment fixed" $r.Stop 'a routed batch refuses land'
    $r = Invoke-Step { Invoke-LandBatch route }
    Assert-Equal 'ROUTE: 5b -;amended after step 2: a comment fixed' ($r.Lines -join ';') 'route prints the lines'
    # A new batch starts from the pre-batch tip, the drill's precondition.
    $null = G $m reset -q --hard $w.Base
    $null = Invoke-Step { Invoke-LandBatch start }
    $null = Invoke-Step { Invoke-LandBatch add -Pr 1 -Branch feat/a -Message (Msg 1) }
    $s1 = @(G $m rev-parse HEAD)[0]
    $null = G $w.Origin update-ref refs/heads/master $w.B
    $r = Invoke-Step { Invoke-LandBatch land }
    Assert-Match "^master is at $($w.B.Substring(0, 7)), not $($w.Base) - it moved since validation$" $r.Stop 'a moved tip stops'
    Assert-Equal "STOP: master is at $($w.B.Substring(0, 7)), not $($w.Base) - it moved since validation" (BatchFile $m stop) 'and the stop is written'
    $r = Invoke-Step { Invoke-LandBatch land }
    Assert-Match '^this batch already stopped:' $r.Stop 'a stopped batch refuses every later call'
    $r = Invoke-Step { Invoke-LandBatch report }
    Assert-Equal "STOP: master is at $($w.B.Substring(0, 7)), not $($w.Base) - it moved since validation" $r.Lines[0] 'report prints the stop'
    Assert-Equal 'on origin/master since the batch began:' $r.Lines[1] 'and what origin gained'
    Assert-Match 'b: file$' $r.Lines[2] 'origin''s commits since the pre-batch tip'
    $null = G $w.Origin update-ref refs/heads/master $w.Base
    $null = G $m reset -q --hard $w.Base
    $null = Invoke-Step { Invoke-LandBatch start }
    $null = Invoke-Step { Invoke-LandBatch add -Pr 1 -Branch feat/a -Message (Msg 1) }
    $s1 = @(G $m rev-parse HEAD)[0]
    $null = New-Commit $m 'extra.txt'
    $r = Invoke-Step { Invoke-LandBatch land }
    Assert-Equal 'HEAD is not the last recorded squash - a squash was amended or a commit added after step 2' $r.Stop 'a commit after the last squash stops'
    $r = Invoke-Step { Invoke-LandBatch reset }
    Assert-Match "^local master has commits after the last squash $s1, and a reset would drop them:`n.*change extra.txt`ncarry them onto what landed instead: git rebase --onto origin/master $s1$" $r.Stop 'reset refuses to drop the extra commit'
    $null = G $m reset -q --hard $s1
    foreach ($case in @(
            @{ Over = @{ 'pr view 1' = Answer (View $w.A -Mergeable CONFLICTING -Status DIRTY) }; Stop = "^#1 reads 'CONFLICTING' against master before anything landed$"; What = 'a PR not MERGEABLE before anything landed' }
            @{ Over = @{ 'pr view 1' = Answer (View $w.A -Draft $true) }; Stop = '^#1 is still a draft, or its draft state did not read, before anything landed'; What = 'a draft before anything landed' }
            @{ Over = @{ 'pr view 1' = Seq @((Answer (View $w.A)), (Answer (View $w.A -Base dev))) }; Stop = "^#1's base reads 'dev', not master - a PR retargeted since step 2"; What = 'a retargeted PR' }
            @{ Over = @{ 'pr view 1' = Seq @((Answer (View $w.A)), (Answer (View $w.A -Status BLOCKED))) }; Stop = "^#1 is 'MERGEABLE BLOCKED', not MERGEABLE CLEAN$"; What = 'a PR not CLEAN' }
            @{ Over = @{ 'api graphql 1' = Answer @(Page @((Thread rev))) }; Stop = "^#1 has a review thread nobody answered - reply in it \(/ouro:land step 8\) or resolve it:`nrev a.ps1 https://x/1$"; What = 'an unanswered thread' }
            @{ Over = @{ 'pr view 1' = Answer (View $w.A) }; Stop = "^#1 reads 'OPEN ', not MERGED with a merge commit - after 'Base branch was modified'"; What = 'a PR not MERGED after the merge call' }
        )) {
        [System.IO.File]::Delete((Join-Path (@(G $m rev-parse --absolute-git-dir)[0]) 'land-batch.stop'))
        Set-Gh (Table $w $case.Over)
        $r = Invoke-Step { Invoke-LandBatch land }
        Assert-Match $case.Stop $r.Stop "$($case.What) stops"
        Assert-Match (($case.Stop -split "`n")[0]) ((BatchFile $m stop) -replace '^STOP: ', '') "$($case.What) is written to the stop file"
        Assert-Equal '' (BatchFile $m landed) "$($case.What) records nothing as landed"
    }
    [System.IO.File]::Delete((Join-Path (@(G $m rev-parse --absolute-git-dir)[0]) 'land-batch.stop'))
    # A merge whose commit is not the squash's tree: origin moves to another commit on the same parent.
    $fake = @(G $m commit-tree "$($w.Base)^{tree}" -p $w.Base -m fake)[0]
    Set-Gh (Table $w @{ 'pr view 1' = Seq @((Answer (View $w.A)), (Answer (View $w.A)), (Answer (View $w.A -State MERGED -MergeOid $fake)))
            'pr merge 1' = @{ Exit = 0; Out = ''; Run = "git -C '$m' push -q origin ${fake}:refs/heads/master" } })
    $r = Invoke-Step { Invoke-LandBatch land }
    Assert-Equal "#1 merged as $fake, whose tree is not the validated $s1's" $r.Stop 'a tree mismatch stops after the merge'
    Assert-Equal "1`t$fake" (BatchFile $m landed) 'the merge is recorded before the check'
    $null = G $w.Origin update-ref refs/heads/master $w.Base
}
finally { Pop-Location }

Write-Host 'add: the refusals; a conflict then record; stacked'
$w = New-BatchWorld 'wa'; $m = $w.Main
Push-Location -LiteralPath $m
try {
    Set-Gh (Table $w)
    $null = Invoke-Step { Invoke-LandBatch start }
    $r = Invoke-Step { Invoke-LandBatch add -Pr 1 -Branch feat/a -Message (Msg 1 'feat: no number') }
    Assert-Match "^the subject in .* reads 'feat: no number' - it must end in \(#1\), with line 2 blank$" $r.Stop 'a subject without the number stops'
    $r = Invoke-Step { Invoke-LandBatch add -Pr 1 -Branch feat/a }
    Assert-Equal '-Message <file> is required: the file holding the commit message' $r.Stop 'no message stops'
    $r = Invoke-Step { Invoke-LandBatch add -Pr 1 -Branch feat/b -Message (Msg 1) }
    Assert-Equal "#1's head is '$($w.A)', not origin/feat/b - refetch and start the batch over" $r.Stop 'a head that is not origin''s tip of the branch stops'
    $r = Invoke-Step { Invoke-LandBatch add -Pr 3 -Branch feat/c -Message (Msg 3) }
    Assert-Equal "#3's base feat/a is not in the batch - land it first, or this diff misses what it builds on" $r.Stop 'a base outside the batch stops'
    $r = Invoke-Step { Invoke-LandBatch add -Pr 5 -Branch feat/two -Message (Msg 5) }
    Assert-Match "^#5's range has more than one author:`nAnn <ann@x.test>`nBob <bob@y.test>`ntwo addresses for one person are a mailmap entry" $r.Stop 'two authors stop'
    Assert-Equal 0 @(Rows $m).Count 'nothing recorded by any refusal'
    Assert-Equal $w.Base (@(G $m rev-parse HEAD)[0]) 'and HEAD is where it was'
    $r = Invoke-Step { Invoke-LandBatch add -Pr 1 -Branch feat/a -Message (Msg 1) }
    Assert-Equal '' $r.Stop 'add #1'
    $s1 = @(G $m rev-parse HEAD)[0]
    $r = Invoke-Step { Invoke-LandBatch add -Pr 4 -Branch feat/d -Message (Msg 4) }
    Assert-Equal '#4 conflicts with the batch - resolve it by step 3, then run record' $r.Stop 'a conflict stops for step 3'
    Assert-Equal 'shared.txt' (@(G $m ls-files -u | ForEach-Object { ($_ -split "`t")[1] } | Sort-Object -Unique) -join ',') 'with the conflict left in the tree'
    $msg4 = Msg 4
    $r = Invoke-Step { Invoke-LandBatch record -Pr 4 -Branch feat/d -Message $msg4 }
    Assert-Match "^unresolved paths remain:`nshared.txt$" $r.Stop 'record refuses while a path is unresolved'
    $resolved = @('R1') + @(2..10 | ForEach-Object { "l$_" })
    [System.IO.File]::WriteAllText((Join-Path $m 'shared.txt'), (($resolved -join "`n") + "`n"), $Utf8)
    $null = G $m add shared.txt
    $r = Invoke-Step { Invoke-LandBatch record -Pr 4 -Branch feat/d -Message $msg4 }
    Assert-Equal '' $r.Stop 'record commits the resolution'
    $s4 = @(G $m rev-parse HEAD)[0]
    Assert-Equal "#4 recorded as $s4 (row 2); the batch now routes to 5b" ($r.Lines -join ';') 'and reports'
    Assert-Equal 'Ann <ann@x.test>' (@(G $m log -1 --format='%aN <%aE>')[0]) 'with the range''s author'
    Assert-Equal "4`tfeat/d`t$($w.D)`t$s4" @(Rows $m)[1] 'the row is recorded'
    Assert-Equal 'conflict resolved by hand: #4' (BatchFile $m route) 'and the route'
    $r = Invoke-Step { Invoke-LandBatch record -Pr 4 -Branch feat/d -Message $msg4 }
    Assert-Equal "nothing staged, and HEAD is not an unrecorded commit with #4's message" $r.Stop 'record refuses a second time: HEAD is already recorded'
    $r = Invoke-Step { Invoke-LandBatch stacked -Pr 1 -Branch feat/a -Message (Msg 1) }
    Assert-Equal "#1's base is master - it is not stacked, land it with add" $r.Stop 'stacked refuses a PR based on the default branch'
    $r = Invoke-Step { Invoke-LandBatch stacked -Pr 3 -Branch feat/c -Message (Msg 3) }
    Assert-Equal '' $r.Stop 'stacked applies the stacked PR''s own diff'
    $s3 = @(G $m rev-parse HEAD)[0]
    Assert-Equal "#3 landed as its own diff, $s3 (row 3); the batch routes to 5b" ($r.Lines -join ';') 'and reports'
    Assert-Equal 'c' ((Get-Content -LiteralPath (Join-Path $m 'c.txt')) -join ',') 'the stacked file is in the tree'
    Assert-Equal 'R1' ((Get-Content -LiteralPath (Join-Path $m 'shared.txt'))[0]) 'and the resolution stands'
    Assert-Equal 'conflict resolved by hand: #4;stacked: #3 is based on feat/a' (BatchFile $m route) 'the stacked route is recorded'
    Assert-Equal '' (@(G $m status --porcelain) -join ';') 'the tree is clean'
}
finally { Pop-Location }

Write-Host 'route with a merge= attribute, push, close: the 5b drill'
$w = New-BatchWorld 'w5b' -Union $true; $m = $w.Main
Push-Location -LiteralPath $m
try {
    Set-Gh (Table $w)
    $null = Invoke-Step { Invoke-LandBatch start }
    $null = Invoke-Step { Invoke-LandBatch add -Pr 1 -Branch feat/a -Message (Msg 1) }
    $s1 = @(G $m rev-parse HEAD)[0]
    $null = Invoke-Step { Invoke-LandBatch add -Pr 2 -Branch feat/b -Message (Msg 2) }
    $s2 = @(G $m rev-parse HEAD)[0]
    $r = Invoke-Step { Invoke-LandBatch route }
    Assert-Equal 'ROUTE: 5b -;merge= attribute on a path two PRs change: notes.txt: merge: union' ($r.Lines -join ';') 'a merge= path two squashes change routes the set to 5b'
    $r = Invoke-Step { Invoke-LandBatch land }
    Assert-Match '^this batch routes to 5b, not 5a:' $r.Stop 'land refuses'
    $r = Invoke-Step { Invoke-LandBatch watch }
    Assert-Equal "watching origin/master after the push: $($w.Base)" $r.Lines[0] 'before the push, watch names origin''s tip as it is'
    Set-Gh (Table $w @{ 'pr view 2' = Answer (View $w.A) })
    $r = Invoke-Step { Invoke-LandBatch push }
    Assert-Match "^#2's head is '$($w.A)', not the $($w.B) squashed in step 2 - what was pushed to it is not in its squash" $r.Stop 'a moved head stops the push'
    Set-Gh (Table $w @{ 'pr view 2' = Answer (View $w.B -Draft $true) })
    $r = Invoke-Step { Invoke-LandBatch push }
    Assert-Match '^#2 is still a draft' $r.Stop 'a draft stops the push'
    Set-Gh (Table $w @{ 'api graphql 2' = Answer @(Page @((Thread rev))) })
    $r = Invoke-Step { Invoke-LandBatch push }
    Assert-Match '^#2 has a review thread nobody answered' $r.Stop 'an unanswered thread stops the push'
    Assert-Equal $w.Base (@(G $w.Origin rev-parse master)[0]) 'and origin is unchanged'
    $null = G $w.Origin update-ref refs/heads/master $w.D
    Set-Gh (Table $w)
    $r = Invoke-Step { Invoke-LandBatch push }
    Assert-Match "^the push was refused: .*`nmaster moved since the batch was composed: reset \(which reads the batch files first\), then steps 1-4 again as a new batch on the new tip; nothing is closed$" $r.Stop 'a non-fast-forward names the recomposition'
    $null = G $w.Origin update-ref refs/heads/master $w.Base
    $r = Invoke-Step { Invoke-LandBatch push }
    Assert-Equal '' $r.Stop 'the push goes through'
    Assert-Equal "pushed master to origin at $s2" $r.Lines[-1] 'and is reported'
    Assert-Equal $s2 (@(G $w.Origin rev-parse master)[0]) 'origin''s master is the last squash'
    $r = Invoke-Step { Invoke-LandBatch close -Pr 1 -Sha $w.A }
    Assert-Equal "$($w.A) is not on origin/master - #1 has not landed, so it is not closed" $r.Stop 'a commit not on origin does not close the PR'
    Set-Gh (Table $w @{ 'pr view 1' = Answer (View $w.B) })
    $r = Invoke-Step { Invoke-LandBatch close -Pr 1 -Sha $s1 }
    Assert-Match "^#1's head is '$($w.B)', not the $($w.A) squashed in step 2 - what was pushed after it has not landed, so #1 is not closed$" $r.Stop 'a moved head does not close the PR'
    Set-Gh (Table $w)
    $r = Invoke-Step { Invoke-LandBatch close -Pr 1 -Sha $s1 }
    Assert-Equal '' $r.Stop 'close #1'
    Assert-Line $r.Gh "pr close 1 -R o/n --comment Landed on master via the local squash-merge drill - $s1. --delete-branch" 'the close names the landing commit and deletes the branch'
    $r = Invoke-Step { Invoke-LandBatch close -Pr 9 -Sha $s1 }
    Assert-Equal 'no row for #9 in land-batch.tsv - step 2' $r.Stop 'a PR not in the batch is not closed'
    $r = Invoke-Step { Invoke-LandBatch watch }
    Assert-Equal "watching origin/master after the push: $s2" $r.Lines[0] 'watch takes origin''s tip after the push'
    $r = Invoke-Step { Invoke-LandBatch start }
    $r = Invoke-Step { Invoke-LandBatch watch }
    Assert-Equal 'nothing landed: nothing to watch' ($r.Lines -join ';') 'with no batch, nothing to watch'
}
finally { Pop-Location }

Write-Host 'worktree: the batch worktree, and an unignored path'
$w = New-World 'ww'
$null = G $w.Main checkout -q -b other master
Push-Location -LiteralPath $w.Main
try {
    Set-Gh @{}
    $r = Invoke-Step { Invoke-LandBatch worktree }
    Assert-Equal '' $r.Stop 'the worktree is added'
    Assert-Equal ".claude/worktrees/land-batch on master at origin's tip - every later call runs from there" (($r.Lines -join ';') -replace '^batch worktree: ', '') 'and reported'
    Assert-Equal 'master' (@(G (Join-Path $w.Main '.claude/worktrees/land-batch') rev-parse --abbrev-ref HEAD)[0]) 'on the default branch'
    $null = G $w.Main worktree remove .claude/worktrees/land-batch
    [System.IO.File]::WriteAllText((Join-Path $w.Main '.gitignore'), "nothing`n", $Utf8)
    $r = Invoke-Step { Invoke-LandBatch worktree }
    Assert-Equal '.claude/worktrees/ is not ignored - add it to .gitignore' $r.Stop 'an unignored worktrees directory stops'
    Assert-Equal $false (Test-Path -LiteralPath (Join-Path $w.Main '.claude/worktrees/land-batch')) 'and the directory is removed again'
}
finally { Pop-Location }

Write-Host 'the entry point: a child process, the exit codes and the STOP line'
$w = New-BatchWorld 'we'; $m = $w.Main
Set-Gh (Table $w)
$c = Invoke-Child $Script @('start') $m
Assert-Equal 0 $c.Code 'start exits 0'
Assert-Equal 'batch started: the previous batch files are gone' ($c.Lines -join ';') 'and reports'
$c = Invoke-Child $Script @('add', '-Pr', '1', '-Branch', 'feat/a', '-Message', (Msg 1)) $m
Assert-Equal 0 $c.Code 'add exits 0'
Assert-Line $c.Lines '#1 squashed as ' 'and reports the squash' -Prefix
$c = Invoke-Child $Script @() $m
Assert-Equal 1 $c.Code 'no subcommand exits 1'
Assert-Equal 'STOP: the first argument is the subcommand, one of: check, worktree, start, add, stacked, record, amend, route, land, report, reset, push, close, watch' ($c.Lines -join ';') 'with the STOP line naming them'
$c = Invoke-Child $Script @('land') $w.Origin
Assert-Equal 1 $c.Code 'a repository with no binding exits 1'
$help = Get-Help ([WildcardPattern]::Escape($Script))
Assert-Match 'land-batch as one script' ($help.Synopsis) 'the script exposes its help'
Complete-Suite 'Invoke-LandBatch'
