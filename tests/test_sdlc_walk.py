"""Walk lint: structural preconditions of the ICM walk test.

The lint checks structure only. It cannot prove that a cold agent can orient.
Fresh scaffold with pins and no rules block: 0 warnings, 0 failures.
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
STAGES = ['01-plan', '02-design', '03-build', '04-test', '05-deploy', '06-maintain']
BEGIN = '<!-- tink:rules begin skillset=planning-skillset hash=abc123 -->'
END = '<!-- tink:rules end -->'
ROUTER_BEGIN = '<!-- AI-Native SDLC Router -->'
ROUTER_END = '<!-- End AI-Native SDLC Router -->'


def tree_hash(root):
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob('*')):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            digest.update(f'L {rel} {os.readlink(path)}\n'.encode())
        elif path.is_dir():
            digest.update(f'D {rel}\n'.encode())
        else:
            digest.update(f'F {rel} {path.stat().st_mode & 0o777:o} '.encode() + path.read_bytes() + b'\n')
    return digest.hexdigest()


class WalkBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'repo'
        self.root.mkdir()
        result = subprocess.run(['python3', str(INIT), str(self.root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.bin = Path(self.temp.name).resolve() / 'bin'
        self.bin.mkdir()

    # helpers
    def path(self, rel):
        return self.root / rel

    def read(self, rel):
        return self.path(rel).read_text()

    def write(self, rel, text):
        target = self.path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def append(self, rel, text):
        self.write(rel, self.read(rel) + text)

    def sdlc(self, *args, env=None):
        return subprocess.run(['python3', str(self.path('_system/scripts/sdlc.py')), *args],
                              cwd='/', capture_output=True, text=True, env=env)

    def walk(self, *args, env=None):
        result = self.sdlc('walk', '--json', *args, env=env)
        self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
        return result.returncode, json.loads(result.stdout)

    def check(self, data, cid):
        found = [c for c in data['checks'] if c['id'] == cid]
        self.assertEqual(len(found), 1, cid)
        return found[0]

    def status_of(self, cid, env=None):
        _, data = self.walk(env=env)
        return self.check(data, cid)

    def shim(self, code=0, message=''):
        """A stub tink on PATH. Logs its argv and cwd outside the repo."""
        script = self.bin / 'tink'
        log = self.bin / 'tink.log'
        script.write_text(f'#!/bin/sh\necho "$@ | $(pwd)" >> "{log}"\necho "{message}"\nexit {code}\n')
        script.chmod(0o755)
        return log

    def env_with_shim(self):
        env = dict(os.environ)
        env['PATH'] = f'{self.bin}:{env["PATH"]}'
        return env

    def env_without_tink(self):
        env = dict(os.environ)
        env['PATH'] = '/usr/bin:/bin'
        if shutil.which('tink', path=env['PATH']):
            self.skipTest('tink lives in /usr/bin or /bin')
        return env

    def add_block(self, body='rule text\n', begin=BEGIN, end=END):
        self.append('AGENTS.md', f'\n{begin}\n{body}{end}\n')

    def new_run(self, slug='example'):
        result = self.sdlc('new', slug)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class FreshScaffold(WalkBase):
    def test_fresh_scaffold_has_no_warnings_or_failures(self):
        code, data = self.walk()
        self.assertEqual(code, 0)
        self.assertEqual(data['summary'], {'pass': 7, 'warn': 0, 'fail': 0}, json.dumps(data, indent=1))

    def test_all_seven_ids_present_in_order(self):
        _, data = self.walk()
        self.assertEqual([c['id'] for c in data['checks']], ['W1', 'W2', 'W3', 'W4', 'W5', 'W6', 'W7'])
        self.assertEqual([c['name'] for c in data['checks']],
                         ['entry-file', 'pointers-resolve', 'stage-contracts', 'token-budget',
                          'skillsets-consistent', 'status-derivable', 'rules-block-current'])

    def test_human_output_shape(self):
        result = self.sdlc('walk')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertTrue(lines[0].startswith('PASS W1 entry-file: '), lines[0])
        self.assertEqual(sum(1 for l in lines if l.startswith('PASS W')), 7)
        for stage in STAGES:
            self.assertEqual(sum(1 for l in lines if stage in l and 'tokens' in l), 1, stage)
        self.assertEqual(lines[-1], 'Walk: 7 passed, 0 warned, 0 failed')

    def test_json_schema(self):
        _, data = self.walk()
        self.assertEqual(set(data), {'schema', 'checks', 'tokens', 'summary'})
        self.assertEqual(data['schema'], 1)
        for c in data['checks']:
            self.assertEqual(set(c), {'id', 'name', 'status', 'title', 'detail', 'items'})
            self.assertIn(c['status'], ('pass', 'warn', 'fail'))
            self.assertIsInstance(c['title'], str)
            self.assertIsInstance(c['detail'], str)
            self.assertIsInstance(c['items'], list)
        self.assertEqual(set(data['summary']), {'pass', 'warn', 'fail'})
        self.assertEqual(sum(data['summary'].values()), 7)

    def test_token_numbers_per_stage(self):
        _, data = self.walk()
        self.assertEqual(sorted(data['tokens']), STAGES)
        agents = len(self.read('AGENTS.md'))
        context = len(self.read('stages/05-deploy/CONTEXT.md'))
        review = len(self.read('_shared/REVIEW.md'))
        self.assertEqual(data['tokens']['05-deploy'], (agents + context + review) // 4)
        for value in data['tokens'].values():
            self.assertIsInstance(value, int)

    def test_usage_error_is_two_line_exit_two(self):
        result = self.sdlc('walk', '--bogus')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(result.stderr.strip().splitlines()), 2, result.stderr)
        self.assertIn('walk', result.stderr)

    def test_read_only_tree_hash_including_runs_and_tink(self):
        self.new_run('alpha')
        self.new_run('beta')
        self.shim(0)
        self.add_block()
        before = tree_hash(self.root)
        env = self.env_with_shim()
        for args in ([], ['--json']):
            result = self.sdlc('walk', *args, env=env)
            self.assertIn(result.returncode, (0, 1), result.stderr)
        self.assertEqual(before, tree_hash(self.root))
        self.assertTrue((self.bin / 'tink.log').exists(), 'W7 subprocess did run')

    def test_exit_zero_with_warnings(self):
        self.write('.tink/skillsets/extra-skillset.json', '{}')
        result = self.sdlc('walk')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('WARN W5 skillsets-consistent', result.stdout)

    def test_exit_one_with_failure(self):
        self.path('AGENTS.md').unlink()
        result = self.sdlc('walk')
        self.assertEqual(result.returncode, 1)
        self.assertIn('FAIL W1 entry-file', result.stdout)
        self.assertRegex(result.stdout.splitlines()[-1], r'^Walk: \d+ passed, \d+ warned, [1-9]\d* failed$')


class W1EntryFile(WalkBase):
    def test_missing_agents_md_fails(self):
        self.path('AGENTS.md').unlink()
        self.assertEqual(self.status_of('W1')['status'], 'fail')

    def test_missing_router_markers_fail(self):
        self.write('AGENTS.md', '# Only prose\n')
        c = self.status_of('W1')
        self.assertEqual(c['status'], 'fail')
        self.assertIn('Router', c['detail'] + ' '.join(map(str, c['items'])) + c['title'])

    def test_missing_end_marker_fails(self):
        self.write('AGENTS.md', self.read('AGENTS.md').replace(ROUTER_END, ''))
        self.assertEqual(self.status_of('W1')['status'], 'fail')

    def test_sixty_lines_pass_sixty_one_warn(self):
        n = len(self.read('AGENTS.md').splitlines())
        self.append('AGENTS.md', 'filler\n' * (60 - n))
        self.assertEqual(self.status_of('W1')['status'], 'pass')
        self.append('AGENTS.md', 'one more\n')
        c = self.status_of('W1')
        self.assertEqual(c['status'], 'warn')  # preserved project instructions are not the router's fault
        self.assertIn('project instructions', c['title'])

    def test_a_router_over_sixty_lines_fails(self):
        text = self.read('AGENTS.md').replace(ROUTER_END, 'filler\n' * 60 + ROUTER_END)
        self.write('AGENTS.md', text)
        c = self.status_of('W1')
        self.assertEqual(c['status'], 'fail')
        self.assertIn('router is', ' '.join(map(str, c['items'])))

    def test_generated_block_lines_not_counted(self):
        n = len(self.read('AGENTS.md').splitlines())
        self.append('AGENTS.md', 'filler\n' * (59 - n))  # add_block adds one blank separator line
        self.add_block('line\n' * 200)
        c = self.status_of('W1', env=self.env_without_tink())
        self.assertEqual(c['status'], 'pass', c)

    def test_generated_block_within_cap_allowed_and_size_reported(self):
        body = 'a' * 999 + '\n'
        self.add_block(body)
        c = self.status_of('W1')
        self.assertEqual(c['status'], 'pass')
        self.assertIn('1000 bytes', c['title'] + c['detail'])

    def test_generated_block_exactly_cap_passes_over_cap_fails(self):
        self.add_block('a' * 8191 + '\n')
        self.assertEqual(self.status_of('W1')['status'], 'pass')
        self.write('AGENTS.md', self.read('AGENTS.md').replace('a' * 8191, 'a' * 8192))
        self.assertEqual(self.status_of('W1')['status'], 'fail')

    def test_unbalanced_generated_markers_fail(self):
        self.append('AGENTS.md', f'\n{BEGIN}\nrules\n')
        self.assertEqual(self.status_of('W1')['status'], 'fail')

    def test_end_without_begin_fails(self):
        self.append('AGENTS.md', f'\n{END}\n')
        self.assertEqual(self.status_of('W1')['status'], 'fail')

    def test_duplicate_generated_blocks_fail(self):
        self.add_block()
        self.add_block()
        self.assertEqual(self.status_of('W1')['status'], 'fail')


class W2Pointers(WalkBase):
    def test_dead_pointer_in_router_fails_with_line(self):
        self.write('AGENTS.md', self.read('AGENTS.md').replace('_system/SDLC.md', '_system/GONE.md'))
        c = self.status_of('W2')
        self.assertEqual(c['status'], 'fail')
        self.assertTrue(any(i.startswith('AGENTS.md:') and '_system/GONE.md' in i for i in c['items']), c['items'])

    def test_dead_pointer_in_context_fails_with_line(self):
        self.append('stages/03-build/CONTEXT.md', '\nSee `_shared/nope.md` for more.\n')
        c = self.status_of('W2')
        self.assertEqual(c['status'], 'fail')
        self.assertTrue(any(i.startswith('stages/03-build/CONTEXT.md:') and '_shared/nope.md' in i for i in c['items']), c['items'])

    def test_runs_pointers_exempt(self):
        self.append('stages/03-build/CONTEXT.md', '\nWrite `runs/<slug>/never-exists.md` and `runs/x/y.md`.\n')
        self.assertEqual(self.status_of('W2')['status'], 'pass')

    def test_placeholder_glob_resolves(self):
        self.append('stages/03-build/CONTEXT.md', '\nSee `stages/<stage-name>/CONTEXT.md` and `.tink/skillsets/<name>-skillset.json`.\n')
        self.assertEqual(self.status_of('W2')['status'], 'pass')

    def test_placeholder_glob_without_match_fails(self):
        self.append('stages/03-build/CONTEXT.md', '\nSee `stages/<stage-name>/MISSING.md`.\n')
        self.assertEqual(self.status_of('W2')['status'], 'fail')

    def test_placeholder_with_missing_prefix_dir_fails(self):
        self.append('stages/03-build/CONTEXT.md', '\nSee `_shared/nodir/<x>.md`.\n')
        self.assertEqual(self.status_of('W2')['status'], 'fail')

    def test_command_span_with_trailing_args_uses_path_token(self):
        self.append('stages/03-build/CONTEXT.md', '\nRun `_system/scripts/sdlc.py <slug>`.\n')
        self.assertEqual(self.status_of('W2')['status'], 'pass')
        self.append('stages/03-build/CONTEXT.md', '\nRun `_system/scripts/gone.sh <slug>`.\n')
        self.assertEqual(self.status_of('W2')['status'], 'fail')

    def test_other_backticks_ignored(self):
        self.append('stages/03-build/CONTEXT.md', '\nUse `tink use x` and `src/whatever.py`.\n')
        self.assertEqual(self.status_of('W2')['status'], 'pass')


class W3Contracts(WalkBase):
    def test_missing_gate(self):
        text = self.read('stages/02-design/CONTEXT.md').replace('Gate:', 'G:').replace('human', 'person')
        self.write('stages/02-design/CONTEXT.md', text)
        c = self.status_of('W3')
        self.assertEqual(c['status'], 'fail')
        self.assertTrue(any('02-design' in i and 'gate' in i.lower() for i in c['items']), c['items'])

    def test_missing_output(self):
        text = self.read('stages/03-build/CONTEXT.md').replace('Output:', 'Result:')
        self.write('stages/03-build/CONTEXT.md', text)
        c = self.status_of('W3')
        self.assertEqual(c['status'], 'fail')
        self.assertTrue(any('03-build' in i and 'output' in i.lower() for i in c['items']), c['items'])

    def test_missing_inputs(self):
        text = self.read('stages/05-deploy/CONTEXT.md').replace('Inputs:', 'Needs:')
        self.write('stages/05-deploy/CONTEXT.md', text)
        c = self.status_of('W3')
        self.assertEqual(c['status'], 'fail')
        self.assertTrue(any('05-deploy' in i and 'inputs' in i.lower() for i in c['items']), c['items'])

    def test_optional_stage_still_must_pass(self):
        text = self.read('stages/06-maintain/CONTEXT.md').replace('Gate:', 'G:').replace('human', 'person')
        self.write('stages/06-maintain/CONTEXT.md', text)
        self.assertEqual(self.status_of('W3')['status'], 'fail')

    def test_case_insensitive(self):
        text = self.read('stages/05-deploy/CONTEXT.md').replace('Inputs:', 'INPUTS:').replace('Output:', 'output:')
        self.write('stages/05-deploy/CONTEXT.md', text)
        self.assertEqual(self.status_of('W3')['status'], 'pass')


class W4Tokens(WalkBase):
    def test_warn_above_8000(self):
        self.append('stages/03-build/CONTEXT.md', '\n' + 'x' * (8001 * 4))
        _, data = self.walk()
        c = self.check(data, 'W4')
        self.assertEqual(c['status'], 'warn')
        self.assertGreater(data['tokens']['03-build'], 8000)
        self.assertLess(data['tokens']['01-plan'], 8000)

    def test_exactly_8000_passes(self):
        base = self.walk()[1]['tokens']['03-build']
        # grow until just at the boundary in chars
        chars = (len(self.read('AGENTS.md')) + len(self.read('stages/03-build/CONTEXT.md')))
        self.append('stages/03-build/CONTEXT.md', 'x' * (8000 * 4 - chars))
        _, data = self.walk()
        self.assertEqual(data['tokens']['03-build'], 8000)
        self.assertEqual(self.check(data, 'W4')['status'], 'pass')
        self.assertGreater(base, 0)

    def test_fail_above_16000(self):
        self.append('stages/03-build/CONTEXT.md', '\n' + 'x' * (16001 * 4))
        c = self.status_of('W4')
        self.assertEqual(c['status'], 'fail')

    def test_shared_files_counted_once_when_referenced(self):
        self.append('_shared/REVIEW.md', 'y' * 4000)
        _, data = self.walk()
        self.assertEqual(data['tokens']['05-deploy'],
                         (len(self.read('AGENTS.md')) + len(self.read('stages/05-deploy/CONTEXT.md')) + len(self.read('_shared/REVIEW.md'))) // 4)
        self.assertEqual(data['tokens']['04-test'],
                         (len(self.read('AGENTS.md')) + len(self.read('stages/04-test/CONTEXT.md'))) // 4)

    def test_generated_block_counts_toward_budget(self):
        before = self.walk()[1]['tokens']['01-plan']
        self.add_block('z' * 4000 + '\n')
        after = self.walk(env=self.env_without_tink())[1]['tokens']['01-plan']
        self.assertGreaterEqual(after - before, 1000)


class W5Skillsets(WalkBase):
    def test_token_without_pin_fails(self):
        self.append('stages/03-build/CONTEXT.md', '\nAlso `ghost-skillset`.\n')
        c = self.status_of('W5')
        self.assertEqual(c['status'], 'fail')
        self.assertTrue(any('ghost-skillset' in i and 'CONTEXT.md:' in i for i in c['items']), c['items'])

    def test_token_in_sdlc_md_without_pin_fails(self):
        self.append('_system/SDLC.md', '\nMention phantom-skillset here.\n')
        c = self.status_of('W5')
        self.assertEqual(c['status'], 'fail')
        self.assertTrue(any('SDLC.md:' in i and 'phantom-skillset' in i for i in c['items']), c['items'])

    def test_unreferenced_pin_warns(self):
        self.write('.tink/skillsets/extra-skillset.json', '{}')
        c = self.status_of('W5')
        self.assertEqual(c['status'], 'warn')
        self.assertTrue(any('extra-skillset' in i for i in c['items']))

    def test_no_pins_dir_warns_once(self):
        shutil.rmtree(self.path('.tink'))
        c = self.status_of('W5')
        self.assertEqual(c['status'], 'warn')
        self.assertIn('no stage skillset pins installed', c['title'] + c['detail'])
        self.assertEqual(c['items'], [])


class W6Status(WalkBase):
    def test_no_runs_passes(self):
        c = self.status_of('W6')
        self.assertEqual(c['status'], 'pass')
        self.assertIn('no runs', c['detail'])

    def test_valid_runs_pass(self):
        self.new_run('alpha')
        c = self.status_of('W6')
        self.assertEqual(c['status'], 'pass')
        self.assertNotIn('no runs', c['detail'])

    def test_status_is_derived_through_the_one_entry_point(self):
        self.new_run('alpha')
        self.assertFalse(list(self.path('_system/scripts').glob('*.sh')))
        self.assertEqual(self.status_of('W6')['status'], 'pass')

    def test_corrupt_run_json_fails(self):
        self.new_run('alpha')
        self.write('runs/alpha/run.json', '{not json')
        c = self.status_of('W6')
        self.assertEqual(c['status'], 'fail')
        self.assertTrue(any('alpha' in i for i in c['items']), c['items'])

    def test_legacy_run_without_run_json_skipped(self):
        self.write('runs/legacy/brief.md', 'old')
        self.write('runs/.hidden/run.json', '{not json')
        c = self.status_of('W6')
        self.assertEqual(c['status'], 'pass')


class W7Rules(WalkBase):
    def test_exit_zero_passes_and_runs_check_from_root(self):
        log = self.shim(0)
        self.add_block()
        c = self.status_of('W7', env=self.env_with_shim())
        self.assertEqual(c['status'], 'pass', c)
        line = log.read_text().strip()
        self.assertTrue(line.startswith('use planning-skillset --check | '), line)
        self.assertEqual(Path(line.split(' | ')[1]).resolve(), self.root)

    def test_exit_one_fails_with_message(self):
        self.shim(1, 'rules block is stale: regenerate')
        self.add_block()
        c = self.status_of('W7', env=self.env_with_shim())
        self.assertEqual(c['status'], 'fail')
        self.assertIn('rules block is stale: regenerate', c['detail'] + ' '.join(c['items']))

    def test_other_exit_warns(self):
        self.shim(3, 'boom')
        self.add_block()
        self.assertEqual(self.status_of('W7', env=self.env_with_shim())['status'], 'warn')

    def test_absent_tink_skipped(self):
        self.add_block()
        c = self.status_of('W7', env=self.env_without_tink())
        self.assertEqual(c['status'], 'pass')
        self.assertIn('skipped', c['title'] + c['detail'])

    def test_no_block_skipped_and_tink_never_invoked(self):
        log = self.shim(1, 'must not run')
        c = self.status_of('W7', env=self.env_with_shim())
        self.assertEqual(c['status'], 'pass')
        self.assertIn('skipped', c['title'] + c['detail'])
        self.assertFalse(log.exists())

    def test_malformed_block_not_invoked(self):
        log = self.shim(0)
        self.append('AGENTS.md', f'\n{BEGIN}\nrules\n')
        c = self.status_of('W7', env=self.env_with_shim())
        self.assertEqual(c['status'], 'pass')
        self.assertFalse(log.exists())


if __name__ == '__main__':
    unittest.main()
