"""Operator-confirmed seed checks are independent acceptance evidence (#194)."""
import json

from test_sdlc_stage import StageBase, PASSING


class SeedChecks(StageBase):
    def bind(self, checks, profile='light'):
        self.new_run('r', profile=profile, approve=False)
        seed = self.base / 'seed-contract.md'
        seed.write_text('status: confirmed for intake\n# Seed\n## Acceptance criteria\n' + checks
                        + '\n## Boundaries\nNo extra work.\n')
        self.stage('r', 1, '--seed-contract', str(seed))
        self.path = self.root / 'runs/r'
        self.set_checks([PASSING])

    def approve_all(self, profile='light'):
        for stage in ([3] if profile == 'light' else [1, 2, 3]):
            self.decide('r', stage)

    def definitions(self):
        return json.loads((self.path / 'seed-checks.json').read_text())

    def save(self, value):
        (self.path / 'seed-checks.json').write_text(json.dumps(value))

    def attest(self, item):
        result = self.sdlc('mark', 'r', item, 'passed', '--evidence', 'synthetic operator observation',
                           '--reviewer', 'synthetic-operator', '--source', 'isolated-test')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_failing_seed_command_overrules_passing_worker_checks(self):
        self.bind('A1 [user] Independent check\n   cmd: `test -f accepted` | expect: exit 0 | cwd: .\n')
        self.approve_all()
        self.attest('seed-1-expect')
        failed = self.sdlc('verify', 'r')
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('Seed check failed: seed-1', failed.stderr)
        receipt = self.path / '04-test/output/verification.json'
        self.assertEqual(json.loads(receipt.read_text())['result'], 'failed')
        (self.root / 'accepted').touch()
        passed = self.sdlc('verify', 'r')
        self.assertEqual(passed.returncode, 0, passed.stderr)
        record = json.loads(receipt.read_text())
        self.assertEqual(record['seed_checks'], {'seed-1': 'proven', 'seed-1-expect': 'attested'})
        self.assertEqual(record['checklist_items'], {'a': 'proven'})
        log = (self.path / '04-test/output/test-log.md').read_text()
        self.assertIn('# seed check seed-1', log)
        self.assertIn('# checklist item a', log)

    def test_seed_edit_drop_and_file_removal_stale_definition_gate(self):
        self.bind('A1 [user] Check\ncmd: `true`\nexpect: success\ncwd: .\n', profile='full')
        self.approve_all('full')
        original = self.definitions()
        for mutation in ['edit', 'drop', 'remove']:
            with self.subTest(mutation=mutation):
                changed = json.loads(json.dumps(original))
                if mutation == 'edit':
                    changed['items'][0]['description'] = 'weaker'
                elif mutation == 'drop':
                    changed['items'] = []
                self.save(changed)
                if mutation == 'remove':
                    (self.path / 'seed-checks.json').unlink()
                status = self.sdlc('status', 'r').stdout
                self.assertIn('Stage 1: stale', status)
                self.assertIn('Stage 3: stale', status)
                self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)
                # Re-approval must not bless a missing or weakened imported check.
                self.assertNotEqual(self.sdlc('decide', 'r', '1', 'approved', '--reviewer', 'h',
                                              '--source', 's', '--reason', 'r').returncode, 0)
                self.save(original)

    def test_you_judge_and_expect_require_operator_attestation(self):
        self.bind('A1 [user] Output\ncmd: `true` | expect: looks right | cwd: .\n'
                  'A2 [user] Appearance\nyou judge: The layout is readable\n')
        self.approve_all()
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)
        status = self.sdlc('status', 'r').stdout
        self.assertIn('seed-2', status)
        mark = self.sdlc('mark', 'r', 'seed-2', 'passed', '--evidence', 'worker guess')
        self.assertNotEqual(mark.returncode, 0)
        self.assertIn('operator', mark.stderr)
        self.attest('seed-1-expect')
        self.attest('seed-2')
        result = self.sdlc('verify', 'r')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.path / '04-test/output/verification.json').read_text())
                         ['seed_checks']['seed-2'], 'attested')

    def test_provisional_activation_requires_evidence_and_renewed_review(self):
        self.bind('A1 [agent] Future command\nprovisional: executable exists\n'
                  'cmd: `./future` | expect: success | cwd: .\n')
        self.approve_all()
        result = self.sdlc('verify', 'r')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.path / '04-test/output/verification.json').read_text())
                         ['seed_checks'], {'seed-1': 'pending'})
        view = json.loads(self.sdlc('status', 'r', '--json').stdout)['run']
        pending = next(item for item in view['checklist'] if item['id'] == 'seed-1')
        self.assertEqual(pending['status'], 'pending')
        self.assertEqual(pending['proof'], 'attested')  # API 1 proof describes the path, not completion.
        self.assertTrue(pending['pending'])
        value = self.definitions()
        value['items'][0]['activation'] = {'cmd': 'false', 'cwd': '.', 'evidence': 'executable now exists'}
        self.save(value)
        self.assertIn('Stage 3: stale', self.sdlc('status', 'r').stdout)
        self.approve_all()
        self.attest('seed-1-expect')
        result = self.sdlc('verify', 'r')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Seed check failed: seed-1', result.stderr)
        # A different activated command needs a fresh operator observation too.
        value['items'][0]['activation']['cmd'] = 'true'
        self.save(value)
        self.approve_all()
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)
        self.attest('seed-1-expect')
        self.assertEqual(self.sdlc('verify', 'r').returncode, 0)

    def test_provisional_cannot_be_marked_or_activated_without_evidence(self):
        self.bind('A1 [agent] Future\nprovisional: executable exists | cmd: `false` | expect: success | cwd: .\n')
        self.approve_all()
        self.assertNotEqual(self.sdlc('mark', 'r', 'seed-1', 'passed', '--evidence', 'guess').returncode, 0)
        value = self.definitions()
        value['items'][0]['activation'] = {'cmd': 'true', 'cwd': '.', 'evidence': ''}
        self.save(value)
        self.assertNotEqual(self.sdlc('decide', 'r', '3', 'approved', '--reviewer', 'h',
                                      '--source', 's', '--reason', 'r').returncode, 0)
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)

    def test_inline_lean_list_and_backtick_shell_fields_are_verbatim(self):
        self.new_run('r', approve=False)
        seed = self.base / 'lean.md'
        command = 'printf "%s\\n" "value | expect: literal" | grep literal'
        seed.write_text('status: confirmed for intake\n## Acceptance checks\n'
                        '- [ ] A1 [user] Exact shell text\n'
                        f'  cmd: `{command}` | expect: literal | cwd: .\n'
                        '\n## Next steps\ncmd: `false`\n')
        self.stage('r', 1, '--seed-contract', str(seed))
        self.path = self.root / 'runs/r'
        self.set_checks([PASSING])
        self.assertEqual(self.definitions()['items'][0]['cmd'], command)
        self.approve_all()
        self.attest('seed-1-expect')
        out = self.sdlc('verify', 'r')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('Seed checks (operator-confirmed)', out.stdout)
        self.assertIn('Worker-written checks:', out.stdout)

    def test_cwd_and_shell_command_are_preserved(self):
        (self.root / 'sub').mkdir()
        (self.root / 'sub/proof').write_text('exact output\n')
        self.bind('A1 [user] Shell pipeline\ncmd: `cat proof | grep "exact output"`\n'
                  'expect: exact output\ncwd: sub\n')
        self.approve_all()
        self.attest('seed-1-expect')
        self.assertEqual(self.sdlc('verify', 'r').returncode, 0)

    def test_malformed_executable_check_refuses_intake_without_mutation(self):
        self.new_run('r', approve=False)
        seed = self.base / 'bad.md'
        seed.write_text('## Acceptance checks\nA1 [user] Missing fields\ncmd: `true`\n')
        self.refuses('r', 1, '--seed-contract', str(seed), message='expect: and cwd:')
