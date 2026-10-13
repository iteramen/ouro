<#
.SYNOPSIS
    Test of Merge-PullRequest.ps1 over the gh stand-in and a scratch repository.
.DESCRIPTION
    The script is dot-sourced and its function called per row, with the pause between re-reads
    counted instead of slept; the fixture beside this file holds the stand-in and builds the
    repository: a main checkout, a bare origin holding master, a feature branch with its
    `refs/pull/<n>/head`, so the author read runs over real commits.

    Pinned, each guard with the case it refuses shown refused and no merge called: the subject
    must end in (#<Pr>) and the body file be there and non-empty; -Linear is yes or no and -Merge
    squash or merge; a draft, a base other than the default branch, a state other than MERGEABLE
    CLEAN after five reads (UNKNOWN re-polled, four pauses), a `gh pr checks` exit other than 0,
    zero pull_request runs for the head, and a review thread nobody answered -- while a resolved
    thread, one with a reply, or one the PR's author opened lets the merge through, and a thread
    on the second page is read. A range with more than one author lands by merge commit under a
    squash answer and prints the exception; under -Linear yes it stops. Two spellings of one
    address by case are one author. The merge pins the head, deletes the branch only when no open
    PR is based on it (a failed count keeps it), takes the answer from -Merge, else the binding's
    preset, else squash with a line saying so. Afterwards the PR is read back: not MERGED stops,
    and a read that fails stops without a verdict. The child rows pin the entry point.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'LandScripts.Fixture.ps1')
$Script = Find-Script 'Merge-PullRequest.ps1'
. $Script
$script:polls = 0
function Wait-Poll { param([int]$Seconds) $script:polls += $Seconds }

# One `gh pr view` answer with every field the script asks for across its reads.
function View([string]$Head, [string]$Branch = 'feat/x', [string]$Base = 'master', [string]$Mergeable = 'MERGEABLE',
    [string]$Status = 'CLEAN', [bool]$Draft = $false, [string]$State = 'OPEN', [string]$MergeOid = '') {
    $v = [ordered]@{ headRefOid = $Head; headRefName = $Branch; baseRefName = $Base; mergeable = $Mergeable
        mergeStateStatus = $Status; isDraft = $Draft; state = $State; mergeCommit = $null }
    if ($MergeOid) { $v.mergeCommit = @{ oid = $MergeOid } }
    $v
}
function Thread([string]$Login, [bool]$Resolved = $false, [int]$Count = 1, [string]$Path = 'a.ps1', [string]$Url = 'https://x/1') {
    @{ isResolved = $Resolved; comments = @{ totalCount = $Count; nodes = @(@{ author = @{ login = $Login }; path = $Path; url = $Url }) } }
}
function Page([object[]]$Threads, [bool]$More = $false) {
    @{ data = @{ repository = @{ pullRequest = @{ author = @{ login = 'me' }
                    reviewThreads = @{ pageInfo = @{ hasNextPage = $More; endCursor = $null }; nodes = @($Threads) } } } } }
}
$NoThreads = Answer @(Page @())
$OneRun = Answer @(@{ databaseId = 11 })
$Merged = 'c' * 40

# The happy table for pull request 7, with any key replaced.
function Table([hashtable]$Over = @{}) {
    $t = @{ 'pr view 7' = Seq @((Answer (View $H)), (Answer (View $H -State MERGED -MergeOid $Merged)))
        'pr checks 7' = @{ Exit = 0; Out = 'All checks were successful' }
        "run list --commit $H" = $OneRun
        'api graphql 7' = $NoThreads
        'pr list --base feat/x' = Answer @()
        'pr merge 7' = @{ Exit = 0; Out = '' } }
    foreach ($k in $Over.Keys) { $t[$k] = $Over[$k] }
    $t
}

$w = New-World 'w'; $m = $w.Main
# feat/x: two commits by one author, pushed, with pull request 7's head ref on origin.
$null = G $m checkout -q -b feat/x master
$null = New-Commit $m 'x1.txt' 'Ann <ann@x.test>'
$H = New-Commit $m 'x2.txt' 'Ann <ann@x.test>'
$null = G $m push -q -u origin feat/x
$null = G $w.Origin update-ref refs/pull/7/head $H
# feat/two: a second author; pull request 8.
$null = G $m checkout -q -b feat/two master
$null = New-Commit $m 'y1.txt' 'Ann <ann@x.test>'
$H8 = New-Commit $m 'y2.txt' 'Bob <bob@y.test>'
$null = G $m push -q -u origin feat/two
$null = G $w.Origin update-ref refs/pull/8/head $H8
# feat/case: one person, two spellings of the address by case; pull request 9.
$null = G $m checkout -q -b feat/case master
$null = New-Commit $m 'z1.txt' 'Ann <Ann@X.test>'
$H9 = New-Commit $m 'z2.txt' 'Ann <ann@x.test>'
$null = G $m push -q -u origin feat/case
$null = G $w.Origin update-ref refs/pull/9/head $H9
$null = G $m checkout -q master
$Body = Join-Path $Scratch 'body.md'
[System.IO.File]::WriteAllText($Body, "Summary.`n", $Utf8)
$Empty = Join-Path $Scratch 'empty.md'
[System.IO.File]::WriteAllText($Empty, '', $Utf8)

Push-Location -LiteralPath $m
try {
    Write-Host 'the happy path: every guard read, then a pinned squash with the branch deleted'
    Set-Gh (Table)
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Equal '' $r.Stop 'the run ends without a stop'
    Assert-Line $r.Lines 'ship.merge: not declared - squash, the default' 'a binding with no answer takes squash and says so'
    Assert-Line $r.Lines 'merge answer: squash' 'the answer is printed'
    Assert-Line $r.Lines "#7 merged as $Merged" 'the verdict is the merge commit GitHub reports'
    Assert-Line $r.Gh "pr merge 7 -R o/n --squash --match-head-commit $H --delete-branch --subject fix(x): y (#7) --body-file $Body" 'the merge pins the head, squashes, deletes the branch, and carries the subject and body'
    $order = @($r.Gh | ForEach-Object { ($_ -split ' ')[0, 1] -join ' ' }) -join ','
    Assert-Equal 'pr view,pr checks,run list,api graphql,pr list,pr merge,pr view' $order 'the reads come in the guard order, the merge, then the read-back'
    Assert-Line $r.Gh 'pr checks 7 -R o/n' 'the checks are read for the PR, naming the repository'
    Assert-Line $r.Gh "run list -R o/n --commit $H --event pull_request --json databaseId" 'the runs are counted by commit and event, on the server'
    Assert-Match '^api graphql --paginate --slurp -F number=7 -f owner=o -f name=n -f query=' (@($r.Gh | Where-Object { $_ -like 'api graphql*' })[0]) 'the threads are read through GraphQL, every page, with the repository as variables'

    Write-Host 'the merge answer: -Merge, else the binding''s preset'
    Set-Gh (Table)
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no -Merge merge }
    Assert-Equal '' $r.Stop 'no stop'
    Assert-Line $r.Gh "pr merge 7 -R o/n --merge --match-head-commit $H --delete-branch --subject fix(x): y (#7) --body-file $Body" '-Merge merge lands an explicit merge commit'
    Assert-NoMatch 'not declared' ($r.Lines -join "`n") 'and the default line is not printed'

    Write-Host 'the guards, each shown refused before any merge'
    Set-Gh (Table @{ 'pr view 7' = Answer (View $H -Draft $true) })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Match '^#7 is still a draft, or its draft state did not read - mark it ready \(gh pr ready\)' $r.Stop 'a draft stops'
    Assert-NoMatch 'pr merge' ($r.Gh -join "`n") 'and nothing merges'
    Set-Gh (Table @{ 'pr view 7' = Answer (View $H -Base dev) })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Equal "#7's base reads 'dev', not master" $r.Stop 'a base other than the default branch stops'
    Set-Gh (Table @{ 'pr view 7' = Seq @((Answer (View $H -Mergeable UNKNOWN -Status UNKNOWN)), (Answer (View $H)), (Answer (View $H -State MERGED -MergeOid $Merged))) })
    $script:polls = 0
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Equal '' $r.Stop 'UNKNOWN then CLEAN merges'
    Assert-Equal 10 $script:polls 'after one pause'
    Assert-Equal 3 @($r.Gh | Where-Object { $_ -like 'pr view*' }).Count 'two reads before the merge, one after'
    Set-Gh (Table @{ 'pr view 7' = Answer (View $H -Mergeable MERGEABLE -Status UNKNOWN) })
    $script:polls = 0
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Equal "#7 is 'MERGEABLE UNKNOWN', not MERGEABLE CLEAN" $r.Stop 'UNKNOWN five times stops'
    Assert-Equal 40 $script:polls 'after four pauses'
    Assert-Equal 5 @($r.Gh | Where-Object { $_ -like 'pr view*' }).Count 'and five reads'
    Set-Gh (Table @{ 'pr view 7' = Answer (View $H -Status UNSTABLE) })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Equal "#7 is 'MERGEABLE UNSTABLE', not MERGEABLE CLEAN" $r.Stop 'UNSTABLE, which gh pr merge would take, stops'
    Set-Gh (Table @{ 'pr checks 7' = @{ Exit = 8; Out = 'pending' } })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Match '^gh pr checks exits 8, not 0 - 8 is a pending check' $r.Stop 'a pending check stops'
    Set-Gh (Table @{ "run list --commit $H" = Answer @() })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Match "^no pull_request run reads for $H - nothing built it" $r.Stop 'zero runs for the head stops'
    Set-Gh (Table @{ "run list --commit $H" = Fail })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Match "^no pull_request run reads for $H" $r.Stop 'a failed count stops the same way'

    Write-Host 'review threads: an unanswered one stops; resolved, replied or the author''s own lets the merge through'
    Set-Gh (Table @{ 'api graphql 7' = Answer @(Page @((Thread rev))) })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Match "^#7 has a review thread nobody answered - reply in it \(/ouro:land step 8\) or resolve it:`nrev a.ps1 https://x/1$" $r.Stop 'an open thread stops, naming who, where and the link'
    Assert-NoMatch 'pr merge' ($r.Gh -join "`n") 'and nothing merges'
    Set-Gh (Table @{ 'api graphql 7' = Answer @((Page @() $true), (Page @((Thread rev2 -Url 'https://x/2')))) })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Match 'rev2 a.ps1 https://x/2' $r.Stop 'a thread on the second page is read'
    Set-Gh (Table @{ 'api graphql 7' = Answer @(Page @((Thread rev -Resolved $true), (Thread rev -Count 2), (Thread me))) })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Equal '' $r.Stop 'a resolved thread, an answered one and the author''s own do not stop'
    Set-Gh (Table @{ 'api graphql 7' = Fail })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Match "^cannot read #7's review threads \(exit 1\)" $r.Stop 'a failed read stops'

    Write-Host 'authorship: more than one author lands by merge commit, or stops under -Linear yes; case is one author'
    Set-Gh (Table @{ 'pr view 8' = Seq @((Answer (View $H8 -Branch feat/two)), (Answer (View $H8 -Branch feat/two -State MERGED -MergeOid $Merged)))
            'pr checks 8' = @{ Exit = 0; Out = '' }; "run list --commit $H8" = $OneRun; 'api graphql 8' = $NoThreads
            'pr list --base feat/two' = Answer @(); 'pr merge 8' = @{ Exit = 0; Out = '' } })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 8 -Subject 'feat(two): z (#8)' -BodyFile $Body -Linear no }
    Assert-Equal '' $r.Stop 'two authors merge'
    Assert-Line $r.Lines "authorship exception: #8's range has more than one author - it lands by merge commit, not squash:" 'the exception is printed'
    Assert-Line $r.Lines 'Bob <bob@y.test>' 'with the authors'
    Assert-Line $r.Gh "pr merge 8 -R o/n --merge --match-head-commit $H8 --delete-branch --subject feat(two): z (#8) --body-file $Body" 'and the merge is an explicit merge commit'
    Reset-Gh
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 8 -Subject 'feat(two): z (#8)' -BodyFile $Body -Linear yes }
    Assert-Match "^#8's range has more than one author, so it would land by merge commit, and a document step 0 read requires a linear history" $r.Stop 'under -Linear yes it stops'
    Assert-NoMatch 'pr merge' ($r.Gh -join "`n") 'before any merge'
    Set-Gh (Table @{ 'pr view 9' = Seq @((Answer (View $H9 -Branch feat/case)), (Answer (View $H9 -Branch feat/case -State MERGED -MergeOid $Merged)))
            'pr checks 9' = @{ Exit = 0; Out = '' }; "run list --commit $H9" = $OneRun; 'api graphql 9' = $NoThreads
            'pr list --base feat/case' = Answer @(); 'pr merge 9' = @{ Exit = 0; Out = '' } })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 9 -Subject 'fix(case): c (#9)' -BodyFile $Body -Linear yes }
    Assert-Equal '' $r.Stop 'one address in two spellings is one author'
    Assert-Line $r.Gh "pr merge 9 -R o/n --squash --match-head-commit $H9 --delete-branch --subject fix(case): c (#9) --body-file $Body" 'and squashes'

    Write-Host 'the branch stays when an open PR is based on it, or the count fails'
    Set-Gh (Table @{ 'pr list --base feat/x' = Answer @(@{ number = 12 }) })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Equal '' $r.Stop 'the merge goes through'
    Assert-Line $r.Lines 'the branch stays: 1 open PR(s) are based on feat/x' 'and says why the branch stays'
    Assert-Line $r.Gh "pr merge 7 -R o/n --squash --match-head-commit $H --subject fix(x): y (#7) --body-file $Body" 'without --delete-branch'
    Set-Gh (Table @{ 'pr list --base feat/x' = Fail })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Equal '' $r.Stop 'a failed count still merges'
    Assert-Line $r.Lines 'the branch stays: the count of PRs based on feat/x failed' 'and keeps the branch'

    Write-Host 'the read-back: not MERGED stops; a read that fails stops without a verdict'
    Set-Gh (Table @{ 'pr view 7' = Answer (View $H) })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Match "^#7 reads 'OPEN ', not MERGED with a merge commit - after 'Base branch was modified'" $r.Stop 'a PR still open after the merge call stops'
    Assert-Match 'change that setting or the binding; never land it as a squash instead$' $r.Stop 'naming the fix for a refused merge commit'
    Set-Gh (Table @{ 'pr view 7' = Seq @((Answer (View $H)), (Fail)) })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Match '^cannot read #7 after the merge - check it by hand' $r.Stop 'a failed read-back is not a verdict'

    Write-Host 'the parameters, each refused before any gh call'
    $cases = @(
        @{ P = @{ Subject = 'fix(x): y (#7)'; BodyFile = $Body; Linear = 'no' }; Stop = '^-Pr <number> is required$'; What = 'no -Pr' }
        @{ P = @{ Pr = 7; BodyFile = $Body; Linear = 'no' }; Stop = '^-Subject is required'; What = 'no -Subject' }
        @{ P = @{ Pr = 7; Subject = 'fix(x): y'; BodyFile = $Body; Linear = 'no' }; Stop = "^the subject reads 'fix\(x\): y' - it must end in \(#7\)"; What = 'a subject without (#7)' }
        @{ P = @{ Pr = 7; Subject = 'fix(x): y (#7)'; Linear = 'no' }; Stop = '^-BodyFile is required'; What = 'no -BodyFile' }
        @{ P = @{ Pr = 7; Subject = 'fix(x): y (#7)'; BodyFile = (Join-Path $Scratch 'missing.md'); Linear = 'no' }; Stop = 'is empty or missing'; What = 'a missing body file' }
        @{ P = @{ Pr = 7; Subject = 'fix(x): y (#7)'; BodyFile = $Empty; Linear = 'no' }; Stop = 'is empty or missing'; What = 'an empty body file' }
        @{ P = @{ Pr = 7; Subject = 'fix(x): y (#7)'; BodyFile = $Body }; Stop = "^-Linear reads '', not yes or no"; What = 'no -Linear' }
        @{ P = @{ Pr = 7; Subject = 'fix(x): y (#7)'; BodyFile = $Body; Linear = 'maybe' }; Stop = "^-Linear reads 'maybe', not yes or no"; What = '-Linear maybe' }
        @{ P = @{ Pr = 7; Subject = 'fix(x): y (#7)'; BodyFile = $Body; Linear = 'no'; Merge = 'rebase' }; Stop = "^-Merge reads 'rebase', not squash or merge"; What = '-Merge rebase' }
    )
    Set-Gh (Table)
    foreach ($case in $cases) {
        $p = $case.P
        $r = Invoke-Step { Invoke-MergePullRequest @p }
        Assert-Match $case.Stop $r.Stop "$($case.What) stops"
        Assert-Equal 0 $r.Gh.Count "$($case.What) stops before any gh call"
    }
}
finally { Pop-Location }

Write-Host 'a binding that declares a landing preset answers the merge'
$w2 = New-World 'w2' -Landing 'merge-merge'
$null = G $w2.Main checkout -q -b feat/x master
$Hp = New-Commit $w2.Main 'p.txt' 'Ann <ann@x.test>'
$null = G $w2.Main push -q -u origin feat/x
$null = G $w2.Origin update-ref refs/pull/7/head $Hp
$null = G $w2.Main checkout -q master
Push-Location -LiteralPath $w2.Main
try {
    Set-Gh (Table @{ 'pr view 7' = Seq @((Answer (View $Hp)), (Answer (View $Hp -State MERGED -MergeOid $Merged))); "run list --commit $Hp" = $OneRun })
    $r = Invoke-Step { Invoke-MergePullRequest -Pr 7 -Subject 'fix(x): y (#7)' -BodyFile $Body -Linear no }
    Assert-Equal '' $r.Stop 'the merge goes through'
    Assert-Line $r.Lines 'merge answer: merge' 'with the preset''s answer'
    Assert-Line $r.Gh "pr merge 7 -R o/n --merge --match-head-commit $Hp --delete-branch --subject fix(x): y (#7) --body-file $Body" 'as an explicit merge commit'
}
finally { Pop-Location }

Write-Host 'the entry point: a child process, the exit codes and the STOP line'
Set-Gh (Table)
$c = Invoke-Child $Script @('-Pr', '7', '-Subject', 'fix(x): y (#7)', '-BodyFile', $Body, '-Linear', 'no') $m
Assert-Equal 0 $c.Code 'the happy path exits 0'
Assert-Line $c.Lines "#7 merged as $Merged" 'and prints the verdict'
$c = Invoke-Child $Script @() $m
Assert-Equal 1 $c.Code 'no parameter exits 1'
Assert-Equal 'STOP: -Pr <number> is required' ($c.Lines -join ';') 'with the STOP line alone'
Set-Gh (Table @{ 'pr view 7' = Answer (View $H -Draft $true) })
$c = Invoke-Child $Script @('-Pr', '7', '-Subject', 'fix(x): y (#7)', '-BodyFile', $Body, '-Linear', 'no') $m
Assert-Equal 1 $c.Code 'a refused guard exits 1'
Assert-Line $c.Lines 'STOP: #7 is still a draft' 'with its STOP line' -Prefix
$help = Get-Help ([WildcardPattern]::Escape($Script))
Assert-Match 'Merge a pull request the way the binding declares' ($help.Synopsis) 'the script exposes its help'
Complete-Suite 'Merge-PullRequest'
