"""Actual public-driver recovery controls on native Linux; no production evidence is fabricated."""
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch


def run(h):
    from ouro import validation
    try:
        validation.check_platform()
    except h.runmod.DriverError as ex:
        print("SKIP: native BUILD recovery controls; " + str(ex))
        return
    if not sys.platform.startswith("linux"):
        print("SKIP: BUILD recovery process observation requires native Linux")
        return

    fresh = h.lane_case('recovery-not-a-fresh-lane')
    original = h.git(fresh.main, 'rev-parse', 'HEAD')
    code, out, err = fresh.ouro('run', '1', '--recover-build', 'docs/note.md')
    h.check('recovery cannot be silently ignored to start a fresh direct BUILD or publish',
            code == 2 and 'open normal-path' in err and not fresh.prompts('BUILD')
            and not fresh.wrote() and h.git(fresh.main, 'rev-parse', 'HEAD') == original)

    partial = 'def compute_total(items):\n    return sum(items) + 4  # preserved partial\n'
    finished = partial + '# completed through normal BUILD\n'

    def setup(name):
        c = h.validation_case('recovery-' + name)
        c.node('REVIEW', {'verdict': 'FINDINGS', 'findings': [h.finding('M')],
                         'false_record_sentences': 0, 'files_read': ['src/app.py']}, 1)
        c.node('BUILD', {**h.BUILD, 'subject': '', 'writes': {
            'src/app.py': partial, 'partial-unselected.txt': 'preserved outside recovery\n'}}, 2)
        code, out, err = h.validation_run(c, '--validate')
        wt = h.validation_wt(c)
        head = h.git(wt, 'rev-parse', 'HEAD')
        receipts = [path for path in h.validation_receipts(c)
                    if json.loads(path.read_text())["commands"] == h.VALIDATION_CMDS]
        h.check('recovery fixture has a real validated head and rejected dirty fix without a fix commit',
                code == 1 and 'no subject' in err and receipts
                and json.loads(receipts[0].read_text())['sha'] == head
                and (wt / 'src/app.py').read_text() == partial
                and c.pr() is not None)
        c.clear_prompts()
        return c, wt, head, receipts[0], receipts[0].read_bytes()

    def checkouts(wt):
        admin = pathlib.Path(h.git(wt, 'rev-parse', '--absolute-git-dir'))
        return sorted((admin / 'ouro-build-recovery').glob('attempt-*/checkout'))

    def ordinary(c, root, *args):
        return h.sh(sys.executable, h.BIN / 'ouro', 'run', '1', *args, cwd=root,
                    env=c.env({'VALIDATION_STATE': str(c.root)}))

    c, old, old_head, receipt, proof = setup('selected')
    before = h.validation_logs(c)
    original = {name: (old / name).read_bytes() for name in ('src/app.py', 'partial-unselected.txt')}
    c.node('BUILD', {**h.BUILD, 'writes': {'src/app.py': finished}}, 1)
    c.node('REVIEW', h.CLEAN, 1)
    templates, external = c.root / 'generated-git-template', c.root / 'external-generated-info'
    templates.mkdir(); external.mkdir()
    (external / 'exclude').write_text('preserve this generated developer exclude\n')
    (templates / 'info').symlink_to(external, target_is_directory=True)
    original_exclude = (external / 'exclude').read_bytes()
    code, out, err = c.ouro('run', '1', '--recover-build', 'src/app.py', '--validate',
                            extra={'VALIDATION_STATE': str(c.root), 'GIT_TEMPLATE_DIR': str(templates)})
    roots = checkouts(old)
    new = h.validation_wt(c)
    new_head = h.git(new, 'rev-parse', 'HEAD')
    executed = h.validation_logs(c)[len(before):]
    h.check('explicit selected-file recovery finishes a genuine new BUILD and validates its exact new inputs',
            code == 0 and len(roots) == 1 and new != old and new_head != old_head
            and [row[1] for row in executed] == [old_head, old_head, new_head, new_head]
            and (new / 'src/app.py').read_text() == finished
            and c.prompts('REVIEW') == ['REVIEW.1.prompt.txt'])
    h.check('recovery preserves the original dirty source, unselected edits and all original receipts',
            h.git(old, 'rev-parse', 'HEAD') == old_head and receipt.read_bytes() == proof
            and all((old / name).read_bytes() == data for name, data in original.items())
            and not (new / 'partial-unselected.txt').exists()
            and not h.git(new, 'status', '--porcelain', '--untracked-files=all'))
    copied = new / '.ouro-build-recovery/files/src/app.py'
    h.check('private cloning ignores generated template aliases and preserves external developer files',
            (external / 'exclude').read_bytes() == original_exclude
            and not (roots[0] / '.git/info').is_symlink()
            and b'/.ouro-build-recovery/' in (roots[0] / '.git/info/exclude').read_bytes())
    # Recovery must genuinely rerun both the pending batch and the automatic
    # driver gate on each committed input, including the preserved original head.
    # Fresh same-head proof is valid; copying the original pending receipt is not.
    receipts = h.validation_receipts(c)
    expected_batches = {(head, commands) for head in (old_head, new_head)
                        for commands in (tuple(h.VALIDATION_CMDS), ('python3 gates/check.py',))}
    records = [json.loads(path.read_text()) for path in receipts]
    fresh_proof = (len(records) == len(expected_batches)
                   and {(row['sha'], tuple(row['commands'])) for row in records} == expected_batches)
    for path, row in zip(receipts, records):
        evidence = path.parent / row['attempt']
        fresh_proof = (fresh_proof and path.read_bytes() != proof
                       and len(row['results']) == len(row['commands']))
        for index, (command, result) in enumerate(zip(row['commands'], row['results'])):
            log = evidence / f'{index}.log'
            fresh_proof = (fresh_proof and result['cmd'] == command and result['sha'] == row['sha']
                           and result['exit'] == 0 and result['log'] == str(log)
                           and result['digest'] == hashlib.sha256(log.read_bytes()).hexdigest())
    h.check('owner-selected inputs are ignored references, not staged results or copied validation proof',
            copied.read_text() == partial
            and not (new / '.ouro-build-recovery/files/partial-unselected.txt').exists()
            and '.ouro-build-recovery/source.json' in (c.nodes / 'BUILD.1.prompt.txt').read_text()
            and fresh_proof and receipt.read_bytes() == proof)
    from ouro import recovery
    resumed = SimpleNamespace(env=SimpleNamespace(main=roots[0]), worktree=new, n=1, k=None,
                              branch=h.git(new, 'branch', '--show-current'), head=lambda: new_head,
                              wt_git=lambda *args, **kwargs: h.runmod.sh.run(['git', *args], cwd=new))
    private = roots[0].parent / 'source.json'
    archived = private.with_name('source-archived.json')
    private.rename(archived)
    try:
        h.check('old recovery inputs expire before consulting archived metadata', recovery.brief(resumed) == '')
    finally:
        archived.rename(private)
    h.check('exact selected bytes survive outside worker-visible input and worktree cleanup',
            (roots[0].parent / 'files/src/app.py').read_text() == partial)
    before = h.validation_logs(c)
    c.clear_prompts()
    code, out, err = ordinary(c, roots[0], '--validate')
    h.check('ordinary public resume follows the recovered Git branch without a recovery stage flag',
            code == 0 and not c.prompts('BUILD') and not c.prompts('REVIEW')
            and h.validation_logs(c) == before and h.git(old, 'rev-parse', 'HEAD') == old_head)

    from ouro import recovery
    private_clone, foreign = c.root / 'metadata-write-control', c.root / 'foreign-ignore-control'
    (private_clone / '.git').mkdir(parents=True)
    foreign.mkdir()
    (foreign / 'exclude').write_bytes(b'preserve external generated ignore')
    (private_clone / '.git/info').symlink_to(foreign, target_is_directory=True)
    refused = False
    try:
        recovery.exclude_reference(private_clone)
    except h.runmod.Refused:
        refused = True
    h.check('held-directory append refuses an aliased info directory without changing its target',
            refused and (foreign / 'exclude').read_bytes() == b'preserve external generated ignore')
    (private_clone / '.git/info').unlink()
    (private_clone / '.git/info').mkdir()
    (private_clone / '.git/info/exclude').symlink_to(foreign / 'exclude')
    refused = False
    try:
        recovery.exclude_reference(private_clone)
    except h.runmod.Refused:
        refused = True
    h.check('held-directory append refuses an aliased exclude file without changing its target',
            refused and (foreign / 'exclude').read_bytes() == b'preserve external generated ignore')

    c, old, head, receipt, proof = setup('refusals')
    for value in ('../outside', '/tmp/outside', '.claude/ouro.toml', 'CLAUDE.md', 'src/missing.py', 'src\\app.py'):
        code, out, err = h.validation_run(c, '--recover-build', value, '--validate')
        h.check('unsafe or unselected recovery path refuses before a new BUILD: ' + value,
                code == 2 and not c.prompts('BUILD') and not checkouts(old)
                and h.git(old, 'rev-parse', 'HEAD') == head and receipt.read_bytes() == proof)
    many = [f'count-{index}.txt' for index in range(33)]
    for name in many:
        (old / name).write_text('selected fixture')
    flags = [item for name in many for item in ('--recover-build', name)]
    code, out, err = h.validation_run(c, *flags, '--validate')
    h.check('more than 32 selected files refuse before a recovery checkout or BUILD',
            code == 2 and '1-32' in err and not checkouts(old) and not c.prompts('BUILD'))
    large = [f'large-{index}.txt' for index in range(5)]
    for name in large:
        (old / name).write_bytes(b'x' * (1024 * 1024))
    flags = [item for name in large for item in ('--recover-build', name)]
    code, out, err = h.validation_run(c, *flags, '--validate')
    h.check('aggregate selected content above 4 MiB refuses without copying or committing it',
            code == 2 and '4 MiB' in err and not checkouts(old) and not c.prompts('BUILD'))
    (old / 'binary.bin').write_bytes(b'no\0binary')
    code, out, err = h.validation_run(c, '--recover-build', 'binary.bin', '--validate')
    h.check('binary partial input refuses without replacing any original source',
            code == 2 and 'binary' in err and not checkouts(old) and (old / 'src/app.py').read_text() == partial)
    override = c.root / 'external-index-control'
    override.write_bytes(pathlib.Path(h.git(old, 'rev-parse', '--path-format=absolute', '--git-path', 'index')).read_bytes())
    override_before = override.read_bytes()
    code, out, err = c.ouro('run', '1', '--recover-build', 'src/app.py', '--validate',
                            extra={'VALIDATION_STATE': str(c.root), 'GIT_INDEX_FILE': str(override)})
    h.check('repository file overrides refuse without rewriting a developer index',
            code == 2 and 'directory/file overrides' in err and override.read_bytes() == override_before
            and not checkouts(old) and not c.prompts('BUILD'))
    link = old / 'alias.py'
    link.symlink_to(old / 'src/app.py')
    code, out, err = h.validation_run(c, '--recover-build', 'alias.py', '--validate')
    h.check('a selected symlink is never followed or committed by recovery',
            code == 2 and 'aliased' in err and link.is_symlink() and not checkouts(old))
    link.unlink()
    reader = SimpleNamespace(worktree=old,
                             wt_git=lambda *args, **kwargs: h.runmod.sh.run(['git', *args], cwd=old))
    for kind in ('file', 'directory'):
        outside = c.root / ('outside-' + kind)
        outside.mkdir()
        (outside / 'app.py').write_text('outside bytes must never become a reference')
        selected = old / 'src/app.py' if kind == 'file' else old / 'src'
        backup = selected.with_name(selected.name + '-owned-backup')
        real_open = recovery.os.open
        swapped = []
        def raced_open(name, flags, *args, **kwargs):
            if not swapped and kwargs.get('dir_fd') is not None and str(name) == ('app.py' if kind == 'file' else 'src'):
                selected.rename(backup)
                selected.symlink_to(outside / 'app.py' if kind == 'file' else outside,
                                    target_is_directory=kind == 'directory')
                swapped.append(True)
            return real_open(name, flags, *args, **kwargs)
        refused = False
        try:
            with patch.object(recovery.os, 'open', side_effect=raced_open):
                try:
                    recovery.snapshot(reader, ['src/app.py'])
                except h.runmod.Refused:
                    refused = True
            h.check('a ' + kind + ' alias substituted during open cannot disclose outside bytes',
                    refused and swapped and not checkouts(old)
                    and (outside / 'app.py').read_text() == 'outside bytes must never become a reference')
        finally:
            if swapped:
                selected.unlink()
                backup.rename(selected)
    admin = pathlib.Path(h.git(old, 'rev-parse', '--absolute-git-dir'))
    lock = admin / 'ouro-validation/lock'
    lock.write_text('uncertain prior execution')
    code, out, err = h.validation_run(c, '--recover-build', 'src/app.py', '--validate')
    h.check('an uncertain validation lease is retained and blocks recovery before clone creation',
            code == 2 and 'validation lease' in err and lock.read_text() == 'uncertain prior execution'
            and not checkouts(old) and not c.prompts('BUILD'))
    lock.unlink()  # This test owns the deliberately malformed fixture file.
    marker = c.root / 'live-worker-finished'
    ready, release = c.root / 'live-worker-ready', c.root / 'live-worker-release'
    program = ("import pathlib,time\nready=pathlib.Path(" + repr(str(ready)) + ")\nrelease=pathlib.Path(" + repr(str(release))
               + ")\nready.write_text('ready')\ndeadline=time.monotonic()+60\nwhile not release.exists() and time.monotonic()<deadline: time.sleep(0.02)\n"
               + "assert release.exists(), 'controller did not release the owned fixture'\npathlib.Path(" + repr(str(marker)) + ").write_text('finished')")
    child = subprocess.Popen([sys.executable, '-c', program], cwd=old)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        h.check('the live-worker fixture is ready and waits for explicit release', ready.exists() and child.poll() is None)
        code, out, err = h.validation_run(c, '--recover-build', 'src/app.py', '--validate')
        h.check('a real live worker refuses recovery without being signalled',
                code == 2 and 'live process' in err and not checkouts(old)
                and not c.prompts('BUILD') and h.git(old, 'rev-parse', 'HEAD') == head)
        release.write_text('release')
        child.wait(timeout=10)
        h.check('the original live worker completes naturally and retains source/evidence',
                child.returncode == 0 and marker.read_text() == 'finished'
                and (old / 'src/app.py').read_text() == partial and receipt.read_bytes() == proof)
    finally:
        release.write_text('release')
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()

    c, old, head, receipt, proof = setup('retry')
    c.node('BUILD', {**h.BUILD, 'subject': '', 'writes': {'src/app.py': partial + '# retry partial\n'}}, 1)
    code, out, err = h.validation_run(c, '--recover-build', 'src/app.py', '--validate')
    first = checkouts(old)[0]
    attempted = h.validation_wt(c)
    reference = attempted / '.ouro-build-recovery/files/src/app.py'
    before_reference = reference.read_bytes()
    h.check('a failed recovery stays rejected with its private checkout and reference intact',
            code == 1 and 'no subject' in err and h.git(attempted, 'rev-parse', 'HEAD') == head
            and reference.read_text() == partial and receipt.read_bytes() == proof)
    active = SimpleNamespace(env=SimpleNamespace(main=first), worktree=attempted, n=1, k=None,
                             branch=h.git(attempted, 'branch', '--show-current'), head=lambda: head,
                             wt_git=lambda *args, **kwargs: h.runmod.sh.run(['git', *args], cwd=attempted))
    reference.write_bytes(before_reference + b'# changed after owner selection\n')
    refused = False
    try:
        recovery.brief(active)
    except h.runmod.DriverError:
        refused = True
    finally:
        reference.write_bytes(before_reference)
    h.check('changed worker-visible reference cannot replace the private owner-selected input',
            refused and (first.parent / 'files/src/app.py').read_text() == partial)
    c.clear_prompts()
    code, out, err = ordinary(c, first, '--validate')
    h.check('ordinary dirty recovery resume still hits the unchanged clean-input barrier',
            code == 1 and 'clean tracked/untracked' in err and not c.prompts('BUILD')
            and reference.read_bytes() == before_reference)
    c.node('BUILD', {**h.BUILD, 'writes': {'src/app.py': finished}}, 1)
    c.node('REVIEW', h.CLEAN, 1)
    code, out, err = ordinary(c, first, '--recover-build', 'src/app.py', '--validate')
    last = h.validation_wt(c)
    h.check('explicit retry preserves every previous partial/reference and validates a normal new commit',
            code == 0 and h.git(last, 'rev-parse', 'HEAD') != head
            and h.git(old, 'rev-parse', 'HEAD') == head and receipt.read_bytes() == proof
            and reference.read_bytes() == before_reference
            and (attempted / 'src/app.py').read_text() == partial + '# retry partial\n')

    c, old, head, receipt, proof = setup('cap')
    for index in range(h.runmod.FIX_CAP):
        h.git(old, 'commit', '--allow-empty', '-qm', 'spent manual fix ' + str(index))
    advanced = h.git(old, 'rev-parse', 'HEAD')
    branch = h.git(old, 'branch', '--show-current')
    h.git(old, 'push', '-q', 'origin', 'HEAD:refs/heads/' + branch)
    # These are simulated trusted GitHub review comments, never production receipts.
    comment_path = c.state / 'pr-7-comments.json'
    comments = json.loads(comment_path.read_text())
    for comment in comments:
        comment['body'] = comment['body'].replace(head, advanced)
    comment_path.write_text(json.dumps(comments))
    code, out, err = h.validation_run(c, '--recover-build', 'src/app.py', '--validate')
    h.check('explicit recovery cannot spend another BUILD after the existing fix cap',
            code == 2 and 'fix-round cap' in err and not checkouts(old)
            and not c.prompts('BUILD') and h.git(old, 'rev-parse', 'HEAD') == advanced
            and (old / 'src/app.py').read_text() == partial and receipt.read_bytes() == proof)

    c, old, head, receipt, proof = setup('newer-head')
    newer = c.root / 'newer'
    h.git(c.main, 'worktree', 'add', '-qb', 'fixture-newer', str(newer), head)
    h.git(newer, 'commit', '--allow-empty', '-qm', 'newer PR head')
    h.git(newer, 'push', '-q', 'origin', 'HEAD:refs/heads/' + h.git(old, 'branch', '--show-current'))
    code, out, err = h.validation_run(c, '--recover-build', 'src/app.py', '--validate')
    h.check('a newer remote/PR head refuses recovery without publishing or rewriting the dirty source',
            code == 2 and 'current PR/remote head' in err and not checkouts(old)
            and not c.prompts('BUILD') and h.git(old, 'rev-parse', 'HEAD') == head
            and receipt.read_bytes() == proof)
