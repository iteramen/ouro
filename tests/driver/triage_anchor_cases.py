"""Promotion preflight against generated issues and top-level evidence files only."""


def exercise(h):
    answer = {"verdict": "PROMOTE", "size": "S", "flow": "express", "doc_impact": "none",
              "comment": "One deliverable; promote."}
    fragments = ('/fixture/location/config.yaml', '2026-01-01-preview')
    config = "\n".join(fragments) + "\n"
    valid = '- `src/app.py` -- "def compute_total(items):"\n'
    for spelling in ('config.yaml', './config.yaml'):
        for index, fragment in enumerate(fragments):
            bare = not spelling.startswith('./')
            body = '## What\nUpdate the setting.\n\n' + valid + f'- `{spelling}` -- "{fragment}"\n'
            case = h.Case(f'triage-root-{bare}-{index}', files={'config.yaml': config},
                          table={'_issue_1': h.issue(labels=('needs-triage', 'gates', 'bug'), body=body, comments=[])})
            case.node('TRIAGE', answer)
            if bare:
                code, out, err = case.ouro('triage', '1', '--dry-run')
                h.check('bare root evidence refuses in dry-run before any write, despite another valid anchor',
                        code == 3 and not case.wrote() and 'verdict NEEDS-RULING' in out
                        and './config.yaml' in out and 'verdict PROMOTE' not in out)
            code, out, err = case.ouro('triage', '1')
            comments = [row['body'] for row in case.issue_state()['comments'] if row['body'].startswith('**Triage**')]
            if bare:
                h.check('bare root evidence posts one corrective final verdict and never adds agent-ready',
                        code == 3 and len(comments) == 1 and 'Verdict: NEEDS-RULING' in comments[0]
                        and './config.yaml' in comments[0] and 'anchor gate' in comments[0]
                        and 'agent-ready' not in h.labels_of(case) and 'needs-ruling' in h.labels_of(case)
                        and not any('--add-label agent-ready' in sig for sig in case.sigs())
                        and case.issue_state()['body'] == body)
            else:
                h.check('explicit top-level evidence promotes with either quote spelling after real shape preflight',
                        code == 0 and len(comments) == 1 and 'Verdict: PROMOTE' in comments[0]
                        and 'agent-ready' in h.labels_of(case) and 'needs-ruling' not in h.labels_of(case))

    for verdict in ('PROMOTE', 'CHECKPOINT'):
        body = '## What\nUpdate the setting.\n\n' + valid
        case = h.Case('triage-doc-preflight-' + verdict,
                      table={'_issue_1': h.issue(labels=('needs-triage', 'gates', 'bug'), body=body, comments=[])})
        case.node('TRIAGE', {**answer, 'verdict': verdict, 'doc_impact': ''})
        code, out, err = case.ouro('triage', '1', '--dry-run')
        h.check('prospective shape refusal is equally truthful and read-only in dry-run',
                code == 3 and not case.wrote() and 'verdict NEEDS-RULING' in out
                and 'Test-AgentReadyShape.ps1' in out and 'doc-impact:' in out)
        code, out, err = case.ouro('triage', '1')
        comments = case.issue_state()['comments']
        h.check('real prospective shape gate refuses missing doc impact before publishing either promotion kind',
                code == 3 and len(comments) == 1 and 'Verdict: NEEDS-RULING' in comments[0]['body']
                and 'Test-AgentReadyShape.ps1' in comments[0]['body'] and 'doc-impact:' in comments[0]['body']
                and 'verdict NEEDS-RULING' in out and 'verdict ' + verdict not in out
                and not any('--add-label agent-ready' in sig or '--add-label checkpoint' in sig for sig in case.sigs())
                and case.issue_state()['body'] == body and 'agent-ready' not in h.labels_of(case))

    body = '## What\nUpdate the setting.\n\n' + valid
    case = h.Case('triage-preflight-infrastructure',
                  table={'_issue_1': h.issue(labels=('needs-triage', 'gates', 'bug'), body=body, comments=[])})
    case.node('TRIAGE', {**answer, 'writes': {'.claude/ouro.toml': 'not valid TOML['}})
    code, out, err = case.ouro('triage', '1')
    h.check('gate infrastructure failure remains a driver error before verdict or remote writes',
            code == 1 and not case.wrote() and 'shape preflight failed before any triage write' in err
            and 'verdict NEEDS-RULING' not in out and 'verdict PROMOTE' not in out)

    case = h.Case('triage-auth-error', table={
        '_issue_1': h.issue(labels=('needs-triage', 'gates', 'bug'), body=body, comments=[]),
        'api user --jq .login': {'Exit': 1, 'Out': 'fixture authentication unavailable'}})
    case.node('TRIAGE', answer)
    code, out, err = case.ouro('triage', '1')
    h.check('authentication failure never becomes an invalid-anchor ruling',
            code == 1 and not case.wrote() and not case.prompts('TRIAGE')
            and 'fixture authentication unavailable' in err and 'verdict NEEDS-RULING' not in out)

    case = h.Case('triage-comment-error', table={
        '_issue_1': h.issue(labels=('needs-triage', 'gates', 'bug'), body=body, comments=[]),
        'issue comment 1*': {'Exit': 1, 'Out': 'fixture comment service unavailable'}})
    case.node('TRIAGE', answer)
    code, out, err = case.ouro('triage', '1')
    h.check('a later remote comment failure reports the error and never adds agent-ready',
            code == 1 and 'fixture comment service unavailable' in err
            and not any('--add-label agent-ready' in sig for sig in case.sigs())
            and 'agent-ready' not in h.labels_of(case) and 'verdict NEEDS-RULING' not in out)
