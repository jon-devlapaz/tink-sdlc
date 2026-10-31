"""Operator-confirmed seed checks are independent acceptance evidence (#194)."""
import json
from tinkery_fixture import TINKERY, confirmed, command, provisional, DEFAULT

from test_sdlc_stage import StageBase, PASSING


class SeedChecks(StageBase):
    def bind(self, checks, profile='light'):
        self.new_run('r', profile=profile, approve=False)
        seed = confirmed(self.base / 'TEST-sessions', checks)
        self.stage('r', 1, '--seed-contract', str(seed), '--tinkery', str(TINKERY))
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
        result = self.sdlc('mark', 'r', item, 'passed', '--evidence', f'synthetic operator observation of {item}',
                           '--reviewer', 'synthetic-operator', '--source', 'isolated-test')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_failing_seed_command_overrules_passing_worker_checks(self):
        self.bind([command('test -f accepted')])
        self.approve_all()
        failed = self.sdlc('verify', 'r')
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('Seed check failed: seed-1', failed.stderr)
        receipt = self.path / '04-test/output/verification.json'
        self.assertEqual(json.loads(receipt.read_text())['result'], 'failed')
        (self.root / 'accepted').touch()
        passed = self.sdlc('verify', 'r')
        self.assertEqual(passed.returncode, 0, passed.stderr)
        record = json.loads(receipt.read_text())
        self.assertEqual(record['seed_checks'], {'seed-1': 'proven'})
        self.assertEqual(record['checklist_items'], {'a': 'proven'})
        log = (self.path / '04-test/output/test-log.md').read_text()
        self.assertIn('# seed check seed-1', log)
        self.assertIn('# checklist item a', log)

    def test_seed_edit_drop_and_file_removal_stale_definition_gate(self):
        self.bind([command()], profile='full')
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
                # Changed/removed checks are explicit departures, not seed equality.
                if mutation != 'remove':
                    view = json.loads(self.sdlc('status', 'r', '--json').stdout)['run']
                    self.assertTrue(view['definition']['departures'])
                self.assertNotEqual(self.sdlc('decide', 'r', '3', 'approved', '--reviewer', 'h',
                                              '--source', 's', '--reason', 'r').returncode, 0)
                self.save(original)

    def test_judge_requires_operator_attestation_but_command_does_not(self):
        self.bind([command(), DEFAULT[0]])
        self.approve_all()
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)
        status = self.sdlc('status', 'r').stdout
        self.assertIn('seed-2', status)
        mark = self.sdlc('mark', 'r', 'seed-2', 'passed', '--evidence', 'worker guess')
        self.assertNotEqual(mark.returncode, 0)
        self.assertIn('operator', mark.stderr)
        self.attest('seed-2')
        result = self.sdlc('verify', 'r')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.path / '04-test/output/verification.json').read_text())
                         ['seed_checks']['seed-2'], 'attested')

    def test_one_quote_cannot_attest_several_judge_checks(self):
        # #77: the Stacker run recorded one operator quote for four marks (tink-arcade runs/stacker/marks/).
        sound = dict(command(), expect='sound plays on the intended output device')
        self.bind([command(), DEFAULT[0], sound, command()])
        # #99 removes command-expect marks. Preserve #77's protection for human judge checks.
        value = self.definitions()
        for item in value['items']:
            if item['kind'] == 'cmd':
                item.update(kind='judge', how_to_judge=item.pop('expect'))
                item.pop('cmd')
                item.pop('cwd')
        self.save(value)
        self.approve_all()
        quote = 'i played it. it was pretty perfect haha'

        def mark(item, *extra, evidence=quote, result='passed'):
            return self.sdlc('mark', 'r', item, result, '--evidence', evidence, '--reviewer', 'synthetic-operator',
                             '--source', 'isolated-test', *extra)

        first = mark('seed-1')
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn('exit zero; output looks right', first.stdout)  # The mark shows its own expectation.
        for item in ('seed-2', 'seed-3', 'seed-4'):
            refused = mark(item)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn('seed-1', refused.stderr)
            self.assertIn('--covers-all', refused.stderr)
        self.assertEqual(len(list((self.path / 'marks').glob('*.json'))), 1)
        # Distinct evidence passes, an explicit shared record passes and says so, and unknown blocks verify.
        self.assertEqual(mark('seed-2', evidence='the style matches the arcade').returncode, 0)
        self.assertEqual(mark('seed-4', '--covers-all').returncode, 0)
        self.assertEqual(mark('seed-3', evidence='did not listen for the device', result='unknown').returncode, 0)
        status = self.sdlc('status', 'r').stdout
        self.assertIn('evidence covers several expectations', status)
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)
        self.assertEqual(mark('seed-3', evidence='heard it on the speakers I meant').returncode, 0)
        result = self.sdlc('verify', 'r')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_passing_verify_with_only_provisional_seed_checks_says_nothing_proves_the_goal(self):
        warning = 'Nothing proves the confirmed goal yet: seed-1 is provisional (awaiting executable exists).'
        self.bind([provisional('./future')])
        self.approve_all()
        result = self.sdlc('verify', 'r')
        self.assertEqual(result.returncode, 0, result.stderr)  # Provisional checks stay nonblocking (SDLC.md).
        self.assertIn(warning, result.stdout)
        view = json.loads(self.sdlc('status', 'r', '--json').stdout)['run']
        self.assertTrue(view['next_action'].startswith(warning), view['next_action'])
        self.assertIn('0 attested, 1 provisional)', self.sdlc('status', 'r').stdout)

    def test_one_proven_seed_check_is_proof_of_the_goal(self):
        self.bind([command('test -f accepted')])
        (self.root / 'accepted').touch()
        self.approve_all()
        result = self.sdlc('verify', 'r')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('Nothing proves', result.stdout)
        view = json.loads(self.sdlc('status', 'r', '--json').stdout)['run']
        self.assertEqual(view['next_action'], 'independent PR review and release checks.')

    def test_a_passing_verify_names_pending_live_checks_beside_a_proven_one(self):
        # tinkery#615: one passing seed check must not hide the provisional ones still unproven.
        warning = ('Part of the confirmed goal is unproven: seed-2 is provisional (awaiting executable exists); '
                   'seed-3 is provisional (awaiting executable exists).')
        self.bind([command(), provisional(), provisional('false'), {**DEFAULT[0], 'how_to_judge': 'Inspect'}])
        self.approve_all()
        self.attest('seed-4')
        result = self.sdlc('verify', 'r')
        self.assertEqual(result.returncode, 0, result.stderr)  # Provisional checks stay nonblocking (SDLC.md).
        self.assertIn('seed-1: proven, seed-2: pending, seed-3: pending, seed-4: attested', result.stdout)
        self.assertIn(warning, result.stdout)
        view = json.loads(self.sdlc('status', 'r', '--json').stdout)['run']
        self.assertEqual(view['next_action'], warning + ' Then independent PR review and release checks.')

    def test_provisional_activation_requires_evidence_and_renewed_review(self):
        self.bind([provisional('./future')])
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
        result = self.sdlc('verify', 'r')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Seed check failed: seed-1', result.stderr)
        # A changed activation needs definition approval, then command output is sufficient.
        value['items'][0]['activation']['cmd'] = 'true'
        self.save(value)
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)
        self.approve_all()
        self.assertEqual(self.sdlc('verify', 'r').returncode, 0)

    def test_activation_evidence_paths_must_exist_in_the_checkout(self):
        # Shape of the Stacker run's seed-3 and seed-4 evidence (tink-arcade ca64bd9), whose log was absent (#76).
        self.bind([provisional('./future')])
        value = self.definitions()
        value['items'][0]['activation'] = {
            'cmd': 'true', 'cwd': '.',
            'evidence': 'Runs green in 03-build/output/stability-{1,2}.log and 03-build/output/red-probes/; '
                        'the command now ships (see 03-build/output/rules-command.log, tests/test_rules.py).'}
        self.save(value)
        self.approve_all()
        result = self.sdlc('verify', 'r')
        self.assertNotEqual(result.returncode, 0)
        for missing in ('03-build/output/stability-1.log', '03-build/output/stability-2.log',
                        '03-build/output/red-probes/', '03-build/output/rules-command.log', 'tests/test_rules.py'):
            self.assertIn(missing, result.stderr)
        output = self.path / '03-build/output'
        (output / 'red-probes').mkdir(parents=True)
        for name in ('stability-1.log', 'stability-2.log', 'rules-command.log'):
            (output / name).write_text('synthetic log\n')
        result = self.sdlc('verify', 'r')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('tests/test_rules.py', result.stderr)
        self.assertNotIn('rules-command.log', result.stderr)
        (self.root / 'tests').mkdir(exist_ok=True)
        (self.root / 'tests/test_rules.py').write_text('')  # Repository-relative evidence resolves from the root.
        result = self.sdlc('verify', 'r')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_provisional_cannot_be_marked_or_activated_without_evidence(self):
        self.bind([provisional('false')])
        self.approve_all()
        self.assertNotEqual(self.sdlc('mark', 'r', 'seed-1', 'passed', '--evidence', 'guess').returncode, 0)
        value = self.definitions()
        value['items'][0]['activation'] = {'cmd': 'true', 'cwd': '.', 'evidence': ''}
        self.save(value)
        self.assertNotEqual(self.sdlc('decide', 'r', '3', 'approved', '--reviewer', 'h',
                                      '--source', 's', '--reason', 'r').returncode, 0)
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)

    def test_shell_fields_are_verbatim(self):
        cmd = 'test -d `pwd` && printf "%s" "literal | expect: not a field"'
        self.bind([command(cmd)])
        self.assertEqual(self.definitions()['items'][0]['cmd'], cmd)
        self.approve_all()
        result = self.sdlc('verify', 'r')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Seed checks (operator-confirmed)', result.stdout)
        self.assertIn('Worker-written checks:', result.stdout)

    def test_cwd_and_shell_command_are_preserved(self):
        (self.root / 'sub').mkdir()
        (self.root / 'sub/proof').write_text('exact output\n')
        self.bind([command('cat proof | grep "exact output"', 'sub')])
        self.approve_all()
        self.assertEqual(self.sdlc('verify', 'r').returncode, 0)

    def test_markdown_only_seed_refuses_intake_without_mutation(self):
        self.new_run('r', approve=False)
        seed = self.base / 'seed-contract.md'
        seed.write_text('## Acceptance criteria\nCommand: true\n')
        self.refuses('r', 1, '--seed-contract', str(seed), '--tinkery', str(TINKERY),
                     message='Tinkery')
