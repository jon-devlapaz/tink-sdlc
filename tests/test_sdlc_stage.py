"""The stage launcher: `sdlc.py stage <run> <n>` does commit, worktree, rules compile, launch prompt.

Written before the implementation. Every refusal must leave the repo untouched.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / 'scripts/init.py'
PYTHON = shutil.which('python3')
GIT = shutil.which('git')
IDENT = {'GIT_AUTHOR_NAME': 'Launcher', 'GIT_AUTHOR_EMAIL': 'l@local',
         'GIT_COMMITTER_NAME': 'Launcher', 'GIT_COMMITTER_EMAIL': 'l@local'}
CONFIRMED = 'seed contract — confirmed for intake; not approved for implementation\nidea\n'
PASSING = {'argv': ['python3', '-c', 'pass'], 'timeout_seconds': 20}
DIRS = {1: ('plan', '01-plan'), 2: ('design', '02-design'), 3: ('build', '03-build'),
        5: ('deploy', '05-deploy'), 6: ('maintain', '06-maintain')}


def tree_hash(root):
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob('*')):
        rel = path.relative_to(root)
        if rel.parts[0] == '.git':
            continue
        if path.is_symlink():
            digest.update(f'L {rel} {os.readlink(path)}\n'.encode())
        elif path.is_dir():
            digest.update(f'D {rel}\n'.encode())
        else:
            digest.update(f'F {rel} '.encode() + path.read_bytes() + b'\n')
    return digest.hexdigest()


class StageBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.base = base
        self.root = base / 'work' / 'proj'
        self.root.mkdir(parents=True)
        self.bin = base / 'bin'
        self.bin.mkdir()
        self.log = base / 'tink.log'
        shim = self.bin / 'tink'
        shim.write_text('#!/bin/sh\n'
                        'if [ "$1" = "--version" ]; then echo "tink ${TINK_VERSION:-1.0.47}"; exit 0; fi\n'
                        'if [ "$1 $2" = "use --help" ]; then\n'
                        '  if [ -n "$TINK_NO_USE" ]; then echo "error: unrecognized subcommand \'use\'" >&2; exit 2; fi\n'
                        '  echo "usage: tink use"; exit 0\n'
                        'fi\n'
                        'echo "$PWD|$*" >> "$TINK_LOG"\n'
                        'if [ -n "$TINK_FAIL" ]; then echo "boom: bad pin" >&2; echo "second line" >&2; exit 1; fi\n'
                        'echo "compiled"\nexit 0\n')
        shim.chmod(0o755)
        self.home = base / 'home'
        self.home.mkdir()
        tools = base / 'tools'  # only python3 and git from the host, so a real tink-route on the host PATH cannot leak in
        tools.mkdir()
        (tools / 'python3').symlink_to(PYTHON)
        (tools / 'git').symlink_to(GIT)
        self.env = {'PATH': f'{self.bin}:{tools}:/usr/bin:/bin', 'HOME': str(self.home),
                    'TINK_LOG': str(self.log), 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}
        self.run_cmd([PYTHON, str(INIT), str(self.root)], cwd=self.root)
        self.git('init', '-q', '-b', 'main')
        self.git('add', '-A')
        self.git('commit', '-qm', 'baseline', env=IDENT)

    # helpers
    def run_cmd(self, argv, cwd=None, env=None, ok=True):
        result = subprocess.run(argv, cwd=cwd or '/', capture_output=True, text=True, env=env if env is not None else self.env)
        if ok is not None:
            self.assertEqual(result.returncode == 0, ok, ' '.join(argv) + '\n' + result.stdout + result.stderr)
        return result

    def git(self, *args, cwd=None, env=None, ok=True):
        merged = {**self.env, **(env or {})}
        return self.run_cmd([GIT, *args], cwd=cwd or self.root, env=merged, ok=ok)

    def sdlc(self, *args, cwd='/', env=None, root=None):
        script = (root or self.root) / '_system/scripts/sdlc.py'
        return self.run_cmd([PYTHON, str(script), *args], cwd=cwd, env={**self.env, **(env or {})}, ok=None)

    def stage(self, run, n, *extra, ok=True, env=None, cwd='/'):
        result = self.sdlc('stage', run, str(n), *extra, env=env, cwd=cwd)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def decide(self, run, stage):
        result = self.sdlc('decide', run, str(stage), 'approved', '--reviewer', 'h', '--source', 's', '--reason', 'r')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def new_run(self, run, profile='light', approve=True, check=PASSING):
        self.assertEqual(self.sdlc('new', run, '--profile', profile).returncode, 0)
        run_dir = self.root / 'runs' / run
        (run_dir / 'checklist.json').write_text(json.dumps({'schema': 1, 'items': [
            {'id': 'a', 'description': 'd', 'verify': 'v', **({'check': check} if check else {})}]}))
        if approve:
            for s in ([3] if profile == 'light' else [1, 2, 3]):
                self.decide(run, s)

    def set_checks(self, checks):
        (self.root / '_system/verification.json').write_text(json.dumps({'require_tink': False, 'checks': checks}))

    def snapshot_state(self):
        return (tree_hash(self.root), self.git('worktree', 'list').stdout, self.git('log', '--oneline', '--all').stdout,
                self.git('branch', '--list').stdout, self.git('status', '--porcelain').stdout, sorted(p.name for p in self.root.parent.iterdir()))

    def refuses(self, *args, message, env=None):
        before = self.snapshot_state()
        result = self.stage(*args, ok=False, env=env)
        self.assertIn(message, result.stderr)
        self.assertTrue(result.stderr.startswith('Error: '))
        self.assertNotIn('Launch prompt', result.stdout)
        self.assertEqual(self.snapshot_state(), before)
        return result

    def tink_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def commit_run(self):
        self.git('add', '-A')
        self.git('commit', '-qm', 'setup', env=IDENT)


class Refusals(StageBase):
    def test_missing_run(self):
        self.refuses('ghost', 1, message='Run is missing')

    def test_stage_four_is_refused(self):
        self.new_run('r')
        self.refuses('r', 4, message='stage 4 runs inside the stage-3 session (see stages/04-test/CONTEXT.md)')

    def test_stage_out_of_range(self):
        self.new_run('r')
        self.refuses('r', 7, message='stage must be one of')

    def test_light_refuses_stage_two(self):
        self.new_run('r')
        self.refuses('r', 2, message='light runs define in stage 1; the single definition gate is recorded as stage 3')

    def test_light_stage_three_needs_gate(self):
        self.new_run('r', approve=False)
        result = self.refuses('r', 3, message='stage 3')
        self.assertIn('sdlc.py decide r 3 approved', result.stderr)

    def test_light_stage_three_stale_gate(self):
        self.new_run('r')
        (self.root / 'runs/r/brief.md').write_text('changed after approval\n')
        result = self.refuses('r', 3, message='stage 3')
        self.assertIn('stale', result.stderr)

    def test_full_stage_two_needs_stage_one(self):
        self.new_run('f', profile='full', approve=False)
        result = self.refuses('f', 2, message='stage 1')
        self.assertIn('sdlc.py decide f 1 approved', result.stderr)

    def test_full_stage_three_gate_combinations(self):
        self.new_run('f', profile='full', approve=False)
        self.refuses('f', 3, message='stage 1')
        self.decide('f', 1)
        result = self.refuses('f', 3, message='stage 2')
        self.assertIn('sdlc.py decide f 2 approved', result.stderr)
        self.decide('f', 2)
        self.stage('f', 3, '--check', env=IDENT)  # both entry gates now current; no third gate is required

    def test_full_stage_two_ok_with_stage_one(self):
        self.new_run('f', profile='full', approve=False)
        self.decide('f', 1)
        self.assertIn('Begin stage 2 (design)', self.stage('f', 2, '--check').stdout)

    def test_stage_five_needs_every_gate(self):
        self.new_run('f', profile='full', approve=False)
        self.decide('f', 1)
        self.decide('f', 2)
        result = self.refuses('f', 5, message='stage 3')
        self.assertIn('sdlc.py decide f 3 approved', result.stderr)

    def test_stage_six_needs_every_gate(self):
        self.new_run('r', approve=False)
        self.refuses('r', 6, message='stage 3')

    def test_stage_five_needs_verification(self):
        self.new_run('r')
        self.refuses('r', 5, message='run verify first: stage 5 reviews current evidence')

    def test_no_identity_refused_before_any_write(self):
        self.new_run('r')
        self.refuses('r', 3, message='committer identity', env={'HOME': str(self.home)})

    def test_worktree_and_here_are_a_usage_error(self):
        self.new_run('r')
        result = self.stage('r', 3, '--here', '--worktree', str(self.base / 'x'), ok=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn('not allowed with', result.stderr)
        self.assertIn('Commands:', result.stderr)
        self.assertIn('stage', result.stderr.split('Commands:')[1])

    def test_path_exists_refused(self):
        self.new_run('r')
        target = self.root.parent / 'proj-r'
        target.mkdir()
        result = self.refuses('r', 3, message=str(target), env=IDENT)
        self.assertIn('git worktree remove', result.stderr)

    def test_branch_exists_refused(self):
        self.new_run('r')
        self.git('branch', 'r')
        result = self.refuses('r', 3, message="branch 'r' already exists", env=IDENT)
        self.assertIn('git branch -D r', result.stderr)

    def test_seed_contract_missing_refused(self):
        self.new_run('r')
        self.refuses('r', 1, '--seed-contract', str(self.root / 'nope.md'), message='seed contract')

    def test_seed_contract_only_for_stage_one(self):
        self.new_run('r')
        (self.root / 'pi.md').write_text('x')
        self.refuses('r', 3, '--seed-contract', str(self.root / 'pi.md'), message='stage 1', env=IDENT)


class HereMode(StageBase):
    def test_stage_one_here(self):
        self.new_run('r', approve=False)
        result = self.stage('r', 1)
        self.assertEqual(self.tink_calls(), [f'{self.root}|use planning-skillset --snapshot runs/r/01-plan'])
        lines = result.stdout.splitlines()
        self.assertEqual(lines[-3:], [f'Checkout: {self.root}', 'Launch prompt (start a NEW session there):',
                                      'Begin stage 1 (plan) of SDLC run `r`.'])
        self.assertEqual(self.git('log', '--oneline').stdout.count('\n'), 1)  # no commit in --here mode

    def test_stage_one_seed_contract_repo_relative(self):
        self.new_run('r', approve=False)
        (self.root / 'seed-contract.md').write_text(CONFIRMED)
        result = self.stage('r', 1, '--seed-contract', 'seed-contract.md', cwd=self.root)
        self.assertEqual(result.stdout.splitlines()[-1],
                         'Begin stage 1 (plan) of SDLC run `r`. The operator-confirmed seed contract is at runs/r/seed-contract.md; '
                         'it is input, not authorization.')
        self.assertEqual((self.root / 'runs/r/seed-contract.md').read_text(), CONFIRMED)

    def test_stage_one_seed_contract_absolute_outside_repo_is_copied_into_the_run(self):
        self.new_run('r', approve=False)
        outside = self.base / 'notes.md'
        outside.write_text(CONFIRMED)
        result = self.stage('r', 1, '--seed-contract', str(outside))
        self.assertIn('seed contract is at runs/r/seed-contract.md;', result.stdout.splitlines()[-1])
        self.assertEqual((self.root / 'runs/r/seed-contract.md').read_text(), CONFIRMED)
        self.assertEqual(outside.read_text(), CONFIRMED)

    def test_the_run_binds_its_own_copy_not_the_original(self):
        self.new_run('r', approve=False)
        target = self.base / 'contract.md'
        target.write_text(CONFIRMED)
        self.stage('r', 1, '--seed-contract', str(target))
        recorded = json.loads((self.root / 'runs/r/run.json').read_text())['seed_contract']
        self.assertEqual(recorded, {'path': 'runs/r/seed-contract.md', 'sha256': hashlib.sha256(CONFIRMED.encode()).hexdigest(),
                                    'source': str(target)})
        self.decide('r', 3)
        self.assertIn('Stage 3: approved', self.sdlc('status', 'r').stdout)
        target.write_text('seed contract — confirmed for intake\nedited elsewhere\n')
        self.assertIn('Stage 3: approved', self.sdlc('status', 'r').stdout)  # the original is not the record
        copy = self.root / 'runs/r/seed-contract.md'
        copy.write_text('seed contract — confirmed for intake\nchanged scope\n')
        self.assertNotIn('Stage 3: approved', self.sdlc('status', 'r').stdout)

    def test_reopening_with_the_edited_original_rebinds_and_stales_approval(self):
        self.new_run('r', approve=False)
        target = self.base / 'contract.md'
        target.write_text(CONFIRMED)
        self.stage('r', 1, '--seed-contract', str(target))
        self.decide('r', 3)
        target.write_text(CONFIRMED + 'revised\n')
        self.stage('r', 1, '--seed-contract', str(target))
        self.assertEqual((self.root / 'runs/r/seed-contract.md').read_text(), CONFIRMED + 'revised\n')
        self.assertNotIn('Stage 3: approved', self.sdlc('status', 'r').stdout)

    def test_check_preview_copies_nothing(self):
        self.new_run('r', approve=False)
        target = self.base / 'contract.md'
        target.write_text(CONFIRMED)
        before = (self.root / 'runs/r/run.json').read_text()
        out = self.stage('r', 1, '--check', '--seed-contract', str(target)).stdout
        self.assertIn('Would copy', out)
        self.assertFalse((self.root / 'runs/r/seed-contract.md').exists())
        self.assertEqual((self.root / 'runs/r/run.json').read_text(), before)

    def test_decide_refuses_a_run_copy_that_no_longer_matches_the_record(self):
        self.new_run('r', approve=False)
        target = self.base / 'contract.md'
        target.write_text(CONFIRMED)
        self.stage('r', 1, '--seed-contract', str(target))
        copy = self.root / 'runs/r/seed-contract.md'
        for change in ('overwrite', 'delete'):
            with self.subTest(change=change):
                if change == 'overwrite':
                    copy.write_text(CONFIRMED + 'new scope\n')
                else:
                    copy.unlink()
                result = self.sdlc('decide', 'r', '3', 'approved', '--reviewer', 'h', '--source', 's', '--reason', 'r')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('seed contract', result.stderr)
                self.assertEqual(list((self.root / 'runs/r/decisions').glob('*.json')), [])
        copy.write_text(CONFIRMED)
        self.decide('r', 3)

    def test_worktree_launch_carries_the_seed_contract_copy(self):
        self.new_run('r', approve=False)
        self.commit_run()
        target = self.base / 'contract.md'
        target.write_text(CONFIRMED)
        self.stage('r', 1, '--worktree', str(self.base / 'wt1'), '--seed-contract', str(target), env=IDENT)
        bound = json.loads((self.base / 'wt1/runs/r/run.json').read_text())
        self.assertEqual(bound['seed_contract']['path'], 'runs/r/seed-contract.md')
        self.assertEqual((self.base / 'wt1/runs/r/seed-contract.md').read_text(), CONFIRMED)
        self.assertEqual(self.git('status', '--porcelain', '--', 'runs/r').stdout.strip(), '')

    def test_failed_compile_leaves_no_empty_parent_directories(self):
        self.new_run('r')
        deep = self.base / 'deep' / 'a' / 'wt'
        self.stage('r', 3, '--worktree', str(deep), ok=False, env={**IDENT, 'TINK_FAIL': '1'})
        self.assertFalse((self.base / 'deep').exists())

    def test_failed_worktree_creation_leaves_no_branch(self):
        self.new_run('r')
        ghost = self.root.parent / 'proj-r'
        self.git('worktree', 'add', '--detach', str(ghost), 'HEAD')
        shutil.rmtree(ghost)
        self.stage('r', 3, ok=False, env=IDENT)
        self.assertEqual(self.git('branch', '--list', 'r').stdout.strip(), '')

    def test_stage_two_and_six_default_here(self):
        self.new_run('f', profile='full', approve=False)
        self.decide('f', 1)
        out = self.stage('f', 2).stdout
        self.assertIn(f'Checkout: {self.root}', out)
        self.assertEqual(self.tink_calls(), [f'{self.root}|use design-skillset --snapshot runs/f/02-design'])
        self.new_run('r')
        self.stage('r', 6)
        self.assertEqual(self.tink_calls()[-1], f'{self.root}|use maintenance-skillset --snapshot runs/r/06-maintain')

    def test_here_flag_forces_current_checkout_for_build(self):
        self.new_run('r')
        out = self.stage('r', 3, '--here').stdout
        self.assertIn(f'Checkout: {self.root}', out)
        self.assertEqual(self.tink_calls(), [f'{self.root}|use build-skillset --snapshot runs/r/03-build'])
        self.assertEqual(self.git('worktree', 'list').stdout.count('\n'), 1)

    def test_idempotent_rerun(self):
        self.new_run('r')
        self.stage('r', 3, '--here')
        state = self.snapshot_state()
        self.stage('r', 3, '--here')
        self.assertEqual(self.snapshot_state(), state)
        self.assertEqual(len(self.tink_calls()), 2)

    def test_tink_absent_notice_still_prints_prompt(self):
        self.new_run('r', approve=False)
        dirs = {str(Path(PYTHON).parent), str(Path(GIT).parent)}
        if any((Path(d) / 'tink').exists() for d in dirs):
            self.skipTest('a real tink shares a directory with python3/git')
        result = self.stage('r', 1, env={'PATH': ':'.join(sorted(dirs))})
        self.assertIn('skills: skipped (tink not installed); the agent will run without stage disciplines', result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], 'Begin stage 1 (plan) of SDLC run `r`.')
        self.assertEqual(self.tink_calls(), [])

    def test_no_skillset_line_gives_notice(self):
        context = self.root / 'stages/01-plan/CONTEXT.md'
        context.write_text(context.read_text().replace('Skillset: `planning-skillset`', 'Skillset: none'))
        self.new_run('r', approve=False)
        result = self.stage('r', 1)
        self.assertIn('skills: skipped (stages/01-plan/CONTEXT.md names no skillset)', result.stdout)
        self.assertEqual(self.tink_calls(), [])
        self.assertEqual(result.stdout.splitlines()[-1], 'Begin stage 1 (plan) of SDLC run `r`.')

    def test_tink_failure_fails_closed(self):
        self.new_run('r', approve=False)
        result = self.stage('r', 1, ok=False, env={'TINK_FAIL': '1'})
        self.assertIn('boom: bad pin', result.stderr)
        self.assertNotIn('second line', result.stderr)
        self.assertIn('stage not opened: fix the skillset problem above', result.stderr)
        self.assertNotIn('Launch prompt', result.stdout)
        self.assertNotIn('Begin stage', result.stdout)

    def test_check_writes_nothing(self):
        self.new_run('r')
        before = self.snapshot_state()
        result = self.stage('r', 3, '--check', env=IDENT)
        self.assertEqual(self.snapshot_state(), before)
        self.assertEqual(self.tink_calls(), [])
        out = result.stdout
        self.assertIn('Would commit: yes', out)
        self.assertIn(f'Would create worktree: {self.root.parent}/proj-r (branch r)', out)
        self.assertIn('Would run: tink use build-skillset --snapshot runs/r/03-build', out)
        self.assertEqual(out.splitlines()[-1], 'Begin stage 3 (build) of SDLC run `r`.')

    def test_check_names_no_checkout_it_does_not_create(self):
        self.new_run('r')
        out = self.stage('r', 3, '--check', env=IDENT).stdout
        self.assertNotIn(f'Checkout: {self.root.parent}', out)
        self.assertIn('Checkout: none yet', out)
        self.assertFalse((self.root.parent / 'proj-r').exists())

    def test_check_here_and_without_tink(self):
        self.new_run('r', approve=False)
        out = self.stage('r', 1, '--check').stdout
        self.assertIn('Would commit: no', out)
        self.assertIn('Would create worktree: none', out)
        dirs = {str(Path(PYTHON).parent), str(Path(GIT).parent)}
        if not any((Path(d) / 'tink').exists() for d in dirs):
            out = self.stage('r', 1, '--check', env={'PATH': ':'.join(sorted(dirs))}).stdout
            self.assertIn('skills: skipped (tink not installed)', out)


class WorktreeMode(StageBase):
    def test_light_build_opens_a_worktree(self):
        self.new_run('r')
        result = self.stage('r', 3, env=IDENT)
        worktree = self.root.parent / 'proj-r'
        self.assertTrue(worktree.is_dir())
        # one new commit, touching only runs/r
        self.assertEqual(self.git('log', '--oneline').stdout.count('\n'), 2)
        self.assertEqual(self.git('log', '-1', '--format=%s').stdout.strip(), 'stage 3 open: r (approved artifacts and receipts)')
        touched = set(self.git('show', '--name-only', '--format=', 'HEAD').stdout.split())
        self.assertTrue(touched and all(name.startswith('runs/r/') for name in touched), touched)
        self.assertEqual(self.git('branch', '--show-current', cwd=worktree).stdout.strip(), 'r')
        self.assertEqual(self.git('rev-parse', 'r').stdout, self.git('rev-parse', 'HEAD').stdout)
        self.assertEqual(self.tink_calls(), [f'{worktree}|use build-skillset --snapshot runs/r/03-build'])
        status = self.run_cmd([PYTHON, '_system/scripts/sdlc.py', 'status', 'r'], cwd=worktree)
        self.assertIn('Stage 3: approved', status.stdout)
        self.assertEqual(result.stdout.splitlines()[-3:], [f'Checkout: {worktree}', 'Launch prompt (start a NEW session there):',
                                                            'Begin stage 3 (build) of SDLC run `r`.'])
        self.assertEqual(self.git('status', '--porcelain', '--', 'runs/r').stdout, '')

    def test_walk_lint_still_passes_after_a_launch(self):
        self.new_run('r')
        self.stage('r', 3, env=IDENT)
        worktree = self.root.parent / 'proj-r'
        walk = self.sdlc('walk', root=worktree, cwd=worktree)
        self.assertEqual(walk.returncode, 0, walk.stdout + walk.stderr)

    def test_no_commit_when_nothing_changed(self):
        self.new_run('r')
        self.commit_run()
        before = self.git('log', '--oneline').stdout
        out = self.stage('r', 3, '--check').stdout
        self.assertIn('Would commit: no', out)
        self.stage('r', 3, env=IDENT)
        self.assertEqual(self.git('log', '--oneline').stdout, before)

    def test_custom_worktree_path(self):
        self.new_run('r')
        target = self.base / 'elsewhere' / 'wt'
        self.stage('r', 3, '--worktree', str(target), env=IDENT)
        self.assertEqual(self.git('branch', '--show-current', cwd=target).stdout.strip(), 'r')

    def test_dirty_files_warning(self):
        self.new_run('r')
        (self.root / 'stray.txt').write_text('x')
        result = self.stage('r', 3, env=IDENT)
        self.assertIn('warning: not carried into the new checkout: stray.txt', result.stdout)
        self.assertFalse((self.root.parent / 'proj-r/stray.txt').exists())
        self.assertNotIn('runs/r', result.stdout.split('warning:')[1].split('\n')[0])

    def test_warning_lists_at_most_five(self):
        self.new_run('r')
        for i in range(8):
            (self.root / f'stray{i}.txt').write_text('x')
        line = [l for l in self.stage('r', 3, env=IDENT).stdout.splitlines() if l.startswith('warning:')][0]
        self.assertEqual(line.count('stray'), 5)

    def test_other_staged_files_are_not_committed(self):
        self.new_run('r')
        (self.root / 'other.txt').write_text('x')
        self.git('add', 'other.txt')
        self.stage('r', 3, env=IDENT)
        touched = set(self.git('show', '--name-only', '--format=', 'HEAD').stdout.split())
        self.assertNotIn('other.txt', touched)

    def test_stage_five_needs_current_verification_then_detached_review_worktree(self):
        self.set_checks([PASSING])
        self.new_run('r')
        self.commit_run()
        self.refuses('r', 5, message='run verify first: stage 5 reviews current evidence')
        verify = self.run_cmd([PYTHON, str(self.root / '_system/scripts/sdlc.py'), 'verify', 'r'], cwd=self.root)
        self.assertIn('passed', verify.stdout)
        result = self.stage('r', 5, env=IDENT)
        review = self.root.parent / 'proj-review-r'
        self.assertTrue(review.is_dir())
        self.assertEqual(self.git('branch', '--show-current', cwd=review).stdout.strip(), '')
        self.assertIn('(detached HEAD)', self.git('worktree', 'list').stdout)
        self.assertEqual(self.tink_calls(), [f'{review}|use deployment-skillset --snapshot runs/r/05-deploy'])
        self.assertEqual(result.stdout.splitlines()[-3:], [f'Checkout: {review}', 'Launch prompt (start a NEW session there):',
                                                            'Begin stage 5 (deploy) of SDLC run `r`.'])

    def test_stage_five_refuses_stale_verification(self):
        self.set_checks([PASSING])
        self.new_run('r')
        self.commit_run()
        self.run_cmd([PYTHON, str(self.root / '_system/scripts/sdlc.py'), 'verify', 'r'], cwd=self.root)
        (self.root / 'code.py').write_text('changed after verification\n')
        self.refuses('r', 5, message='run verify first: stage 5 reviews current evidence', env=IDENT)

    def review_candidate_setup(self, module='cand'):
        """A committed baseline module plus a check that only passes on the changed contents."""
        (self.root / 'cand.py').write_text('value = "baseline"\n')
        self.git('add', 'cand.py')
        self.git('commit', '-qm', 'baseline cand', env=IDENT)
        self.set_checks([{'argv': ['python3', '-c', f'import {module}; assert {module}.value == "verified"'], 'timeout_seconds': 20}])
        self.new_run('r')
        self.commit_run()

    def verify_run(self):
        return self.run_cmd([PYTHON, str(self.root / '_system/scripts/sdlc.py'), 'verify', 'r'], cwd=self.root)

    def test_stage_five_refuses_dirty_candidate_that_passed_verification(self):
        for variant in ('unstaged', 'staged', 'untracked'):
            with self.subTest(variant=variant):
                self.tearDown_fixture = None
                self.setUp()
                self.review_candidate_setup(module='newmod' if variant == 'untracked' else 'cand')
                if variant == 'untracked':
                    (self.root / 'newmod.py').write_text('value = "verified"\n')
                else:
                    (self.root / 'cand.py').write_text('value = "verified"\n')
                    if variant == 'staged':
                        self.git('add', 'cand.py')
                self.verify_run()
                result = self.refuses('r', 5, message='commit the candidate changes', env=IDENT)
                self.assertIn('cand.py' if variant != 'untracked' else 'newmod.py', result.stderr)

    def test_stage_five_refuses_executable_mode_missing_from_commit_when_filemode_disabled(self):
        self.review_candidate_setup()
        candidate = self.root / 'cand.py'
        candidate.write_text('value = "verified"\n')
        candidate.chmod(0o755)
        self.git('config', 'core.fileMode', 'false')
        self.git('add', 'cand.py')
        self.git('commit', '-qm', 'implementation', env=IDENT)
        self.assertEqual(self.git('status', '--porcelain').stdout, '')
        self.verify_run()
        result = self.refuses('r', 5, message='cand.py', env=IDENT)
        self.assertIn('mode', result.stderr.lower())

    def test_stage_five_ignores_unrelated_dirty_run_artifacts(self):
        self.set_checks([PASSING])
        self.new_run('r')
        self.commit_run()
        self.verify_run()
        other = self.root / 'runs/other/01-plan/output/intent.md'
        other.parent.mkdir(parents=True)
        other.write_text('unrelated run evidence\n')
        result = self.stage('r', 5, '--check', env=IDENT)
        self.assertIn('Would create worktree:', result.stdout)
        self.assertIn(f'warning: not carried into the new checkout: {other.relative_to(self.root)}', result.stdout)

    def test_stage_five_opens_when_the_verified_candidate_is_committed(self):
        self.review_candidate_setup()
        (self.root / 'cand.py').write_text('value = "verified"\n')
        self.git('add', 'cand.py')
        self.git('commit', '-qm', 'implementation', env=IDENT)
        self.verify_run()
        self.stage('r', 5, env=IDENT)
        review = self.root.parent / 'proj-review-r'
        self.assertEqual((review / 'cand.py').read_text(), 'value = "verified"\n')
        status = self.sdlc('status', 'r', root=review, cwd=review)
        self.assertIn('Verification: current', status.stdout)

    def test_tink_mount_artifacts_are_not_part_of_the_candidate(self):
        self.set_checks([PASSING])
        self.new_run('r')
        self.commit_run()
        self.verify_run()
        # what `tink mount` / `tink-route` leave behind
        (self.root / '.tink').mkdir(exist_ok=True)
        (self.root / '.tink/.gitignore').write_text('.active/\ncache/\n')
        (self.root / '.tink/.active/some-skill').mkdir(parents=True)
        (self.root / '.tink/.active/some-skill/SKILL.md').write_text('x\n')
        (self.root / '.tink/cache').mkdir()
        (self.root / '.tink/cache/blob').write_text('x\n')
        status = self.sdlc('status', 'r')
        self.assertIn('Verification: current', status.stdout)
        self.stage('r', 5, env=IDENT)

    def mark(self, result):
        out = self.sdlc('mark', 'r', 'a', result, '--evidence', 'e')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_a_mark_after_verification_makes_it_stale_and_blocks_stage_five(self):
        for profile in ('light', 'full'):
            for later in ('failed', 'passed'):
                with self.subTest(profile=profile, later=later):
                    self.setUp()
                    self.set_checks([PASSING])
                    self.new_run('r', profile=profile, check=None)
                    self.mark('passed')
                    self.commit_run()
                    self.verify_run()
                    self.assertIn('Verification: current', self.sdlc('status', 'r').stdout)
                    self.mark(later)
                    self.assertIn('Verification: failed, stale, or blocked', self.sdlc('status', 'r').stdout)
                    self.commit_run()
                    self.refuses('r', 5, message='run verify first', env=IDENT)

    def test_editing_a_mark_receipt_stales_verification(self):
        self.set_checks([PASSING])
        self.new_run('r', check=None)
        self.mark('passed')
        self.commit_run()
        self.verify_run()
        receipt = next((self.root / 'runs/r/marks').glob('*.json'))
        receipt.write_text(receipt.read_text().replace('"passed"', '"failed"'))
        self.assertIn('Verification: failed, stale, or blocked', self.sdlc('status', 'r').stdout)
        self.commit_run()
        self.refuses('r', 5, message='run verify first', env=IDENT)

    def test_deleting_or_corrupting_every_mark_receipt_stales_verification(self):
        for how in ('delete', 'corrupt'):
            with self.subTest(how=how):
                self.setUp()
                self.set_checks([PASSING])
                self.new_run('r', check=None)
                self.mark('passed')
                self.commit_run()
                self.verify_run()
                marks = self.root / 'runs/r/marks'
                if how == 'delete':
                    shutil.rmtree(marks)
                else:
                    for receipt in marks.glob('*.json'):
                        receipt.write_text('{"item": "a", "result": "passed", "time_ns": "soon"}')
                status = self.sdlc('status', 'r').stdout
                self.assertIn('Verification: failed, stale, or blocked', status)
                self.commit_run()
                self.refuses('r', 5, message='run verify first', env=IDENT)

    def route_shim(self, version):
        shim = self.bin / 'tink-route'
        shim.write_text(f'#!/bin/sh\necho "tink-route {version}"\n')
        shim.chmod(0o755)

    def test_stage_warns_when_tink_route_predates_whole_library_routing(self):
        for old in ('0.8.0', '0.9.0', '0.9.0rc1', '0.10.0-rc1', '0.8'):
            with self.subTest(version=old):
                self.setUp()
                self.new_run('r')
                self.route_shim(old)
                result = self.stage('r', 3, env=IDENT)
                self.assertIn(f'warning: tink-route {old}', result.stdout)
                self.assertIn('whole-library routing', result.stdout)
                self.assertNotIn('ignores', result.stdout)
                self.assertIn('Launch prompt', result.stdout)
                self.assertIn('pipx install --force git+https://github.com/jon-devlapaz/tink-route.git', result.stdout)

    def test_stage_warns_when_tink_route_version_is_unrecognized(self):
        self.new_run('r')
        self.route_shim('version unknown')
        result = self.stage('r', 3, env=IDENT)
        self.assertIn('warning: could not determine whether tink-route supports whole-library routing', result.stdout)
        self.assertIn('upgrade:', result.stdout)

    def test_stage_is_quiet_when_tink_route_is_current_or_absent(self):
        self.new_run('a')
        self.route_shim('0.10.0')
        self.assertNotIn('tink-route', self.stage('a', 3, env=IDENT).stdout)
        (self.bin / 'tink-route').unlink()
        self.new_run('b')
        self.assertNotIn('tink-route', self.stage('b', 3, env=IDENT).stdout)

    def test_a_tink_without_use_is_refused_before_anything_happens(self):
        self.new_run('r')
        old = {**IDENT, 'TINK_NO_USE': '1', 'TINK_VERSION': '1.0.41'}
        result = self.refuses('r', 3, message="tink 1.0.41 has no `tink use`", env=old)
        self.assertIn('upgrade tink', result.stderr)
        self.assertEqual(self.tink_calls(), [])
        check = self.stage('r', 3, '--check', ok=False, env=old)
        self.assertIn("has no `tink use`", check.stderr)
        self.assertFalse((self.base / 'work' / 'proj-r').exists())

    def test_a_tink_with_use_passes_the_capability_check_silently(self):
        self.new_run('r')
        result = self.stage('r', 3, env={**IDENT, 'TINK_VERSION': '1.0.47'})
        self.assertNotIn('has no `tink use`', result.stdout + result.stderr)

    def test_tink_failure_removes_the_worktree_and_retry_works(self):
        self.new_run('r')
        result = self.stage('r', 3, ok=False, env={**IDENT, 'TINK_FAIL': '1'})
        worktree = self.root.parent / 'proj-r'
        self.assertFalse(worktree.exists())
        self.assertEqual(self.git('branch', '--list', 'r').stdout.strip(), '')
        self.assertNotIn(str(worktree) + '\n', self.git('worktree', 'list').stdout)
        self.assertIn('stage not opened: fix the skillset problem above', result.stderr)
        self.assertIn('re-run the same command', result.stderr)
        self.assertNotIn('Launch prompt', result.stdout)
        retry = self.stage('r', 3, env=IDENT)
        self.assertIn('Launch prompt', retry.stdout)
        self.assertTrue(worktree.is_dir())

    def test_tink_failure_removes_a_detached_review_worktree(self):
        self.set_checks([PASSING])
        self.new_run('r')
        self.commit_run()
        self.verify_run()
        self.stage('r', 5, ok=False, env={**IDENT, 'TINK_FAIL': '1'})
        self.assertFalse((self.root.parent / 'proj-review-r').exists())
        self.stage('r', 5, env=IDENT)

    def test_worktree_failure_after_commit_says_what_was_committed(self):
        self.new_run('r')
        blocker = self.base / 'blocker'
        blocker.write_text('a file, not a directory')
        result = self.stage('r', 3, '--worktree', str(blocker / 'wt'), ok=False, env=IDENT)
        head = self.git('rev-parse', '--short', 'HEAD').stdout.strip()
        self.assertIn('worktree creation failed', result.stderr)
        self.assertIn(f'already committed {head}: stage 3 open: r (approved artifacts and receipts)', result.stderr)
        self.assertNotIn('Launch prompt', result.stdout)
        self.assertEqual(self.tink_calls(), [])


class DeliveryWarning(StageBase):
    """Stages 3 and 5 warn, without refusing, when a PR cannot be delivered from this checkout."""
    MARKER = 'warning: PR delivery may not be possible'
    SECRET = 'ghp_SECRET_TOKEN_VALUE'

    def setUp(self):
        super().setUp()
        self.gh_log = self.base / 'gh.log'
        self.env['PATH'] = f"{self.bin}:{self.base / 'tools'}"  # no host PATH, so a real gh cannot leak in
        self.env['GH_LOG'] = str(self.gh_log)

    def gh(self, body='exit 0'):
        shim = self.bin / 'gh'
        shim.write_text(f'#!/bin/sh\necho "$*" >> "$GH_LOG"\necho {self.SECRET}\necho {self.SECRET} >&2\n{body}\n')
        shim.chmod(0o755)

    def origin(self, url):
        self.git('remote', 'add', 'origin', url)

    def gh_calls(self):
        return self.gh_log.read_text().splitlines() if self.gh_log.exists() else []

    def opened(self, result):
        self.assertIn('Launch prompt', result.stdout)
        self.assertNotIn(self.SECRET, result.stdout + result.stderr)
        self.assertTrue((self.base / 'work' / 'proj-r').is_dir() or (self.base / 'work' / 'proj-review-r').is_dir())

    def assert_commands(self, out):
        self.assertIn('git push -u origin r', out)
        self.assertIn('gh pr create --fill', out)

    def test_no_origin_warns_and_the_stage_still_opens(self):
        self.new_run('r')
        result = self.stage('r', 3, env=IDENT)
        self.assertEqual(result.stdout.count(self.MARKER), 1)
        self.assertIn('no `origin` remote', result.stdout)
        self.assert_commands(result.stdout)
        self.opened(result)

    def test_github_origin_without_gh_warns(self):
        self.origin('https://github.com/o/r.git')
        self.new_run('r')
        result = self.stage('r', 3, env=IDENT)
        self.assertEqual(result.stdout.count(self.MARKER), 1)
        self.assertIn('`gh` is not installed', result.stdout)
        self.assert_commands(result.stdout)
        self.opened(result)

    def test_github_origin_forms_are_all_recognized(self):
        for url in ('https://github.com/o/r', 'git@github.com:o/r.git', 'ssh://git@github.com/o/r.git',
                    'https://user:token@github.com/o/r.git'):
            with self.subTest(url=url):
                self.setUp()
                self.origin(url)
                self.new_run('r')
                result = self.stage('r', 3, env=IDENT)
                self.assertIn('`gh` is not installed', result.stdout)
                self.assertNotIn('token', result.stdout + result.stderr)

    def test_gh_auth_failure_warns_with_exit_status_only(self):
        self.origin('git@github.com:o/r.git')
        self.gh('exit 1')
        self.new_run('r')
        result = self.stage('r', 3, env=IDENT)
        self.assertEqual(result.stdout.count(self.MARKER), 1)
        self.assertIn('`gh auth status` exited 1', result.stdout)
        self.assert_commands(result.stdout)
        self.assertEqual(self.gh_calls(), ['auth status'])
        self.opened(result)

    def test_gh_auth_timeout_says_it_could_not_confirm(self):
        self.origin('https://github.com/o/r.git')
        self.gh('exec /bin/sleep 30')
        self.new_run('r')
        result = self.stage('r', 3, env=IDENT)
        self.assertIn('could not confirm', result.stdout)
        self.assertNotIn('not signed in', result.stdout)
        self.assert_commands(result.stdout)
        self.opened(result)

    def test_signed_in_gh_is_quiet(self):
        self.origin('https://github.com/o/r.git')
        self.gh('exit 0')
        self.new_run('r')
        result = self.stage('r', 3, env=IDENT)
        self.assertNotIn(self.MARKER, result.stdout)
        self.assertEqual(self.gh_calls(), ['auth status'])
        self.opened(result)

    def test_other_forge_is_not_probed(self):
        for url in ('https://gitlab.com/o/r.git', 'https://github.com.example.org/o/r.git', 'git@example.com:github.com/r.git'):
            with self.subTest(url=url):
                self.setUp()
                self.origin(url)
                self.gh('exit 1')
                self.new_run('r')
                result = self.stage('r', 3, env=IDENT)
                self.assertNotIn(self.MARKER, result.stdout)
                self.assertEqual(self.gh_calls(), [])
                self.opened(result)

    def test_stage_five_warns_and_opens(self):
        self.set_checks([PASSING])
        self.new_run('r')
        self.commit_run()
        self.run_cmd([PYTHON, str(self.root / '_system/scripts/sdlc.py'), 'verify', 'r'], cwd=self.root)
        result = self.stage('r', 5, env=IDENT)
        self.assertEqual(result.stdout.count(self.MARKER), 1)
        self.assert_commands(result.stdout)
        self.opened(result)

    def test_check_prints_the_warning_and_writes_nothing(self):
        self.new_run('r')
        self.commit_run()
        before = self.snapshot_state()
        result = self.stage('r', 3, '--check', env=IDENT)
        self.assertEqual(result.stdout.count(self.MARKER), 1)
        self.assert_commands(result.stdout)
        self.assertEqual(self.snapshot_state(), before)

    def test_other_stages_do_not_warn(self):
        self.new_run('f', profile='full', approve=False)
        self.assertNotIn(self.MARKER, self.stage('f', 1, '--check', env=IDENT).stdout)
        self.decide('f', 1)
        self.assertNotIn(self.MARKER, self.stage('f', 2, '--check', env=IDENT).stdout)
        self.decide('f', 2)
        self.decide('f', 3)
        self.assertNotIn(self.MARKER, self.stage('f', 6, '--check', env=IDENT).stdout)


if __name__ == '__main__':
    unittest.main()
