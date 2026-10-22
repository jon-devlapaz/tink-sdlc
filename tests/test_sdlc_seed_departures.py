"""Seed equality is evidence, departures need humans, command output is proof (#99)."""
import json
import time

from test_sdlc_stage import StageBase, PASSING
from tinkery_fixture import TINKERY, confirmed, command, provisional, DEFAULT


class SeedDepartures(StageBase):
    def bind(self, records=None, worker=False):
        self.new_run('r', approve=False)
        self.path = self.root / 'runs/r'
        if not worker:
            (self.path / 'checklist.json').write_text(json.dumps({'schema': 1, 'items': []}))
        self.seed = confirmed(self.base / 'TEST-sessions', records or [command()])
        self.stage('r', 1, '--seed-contract', str(self.seed), '--tinkery', str(TINKERY))
        self.set_checks([PASSING])

    def view(self):
        result = self.sdlc('status', 'r', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['run']

    def change(self, mutate):
        target = self.path / 'seed-checks.json'
        value = json.loads(target.read_text())
        mutate(value['items'])
        target.write_text(json.dumps(value))

    def test_matching_seed_auto_passes_with_evidence_and_no_human_question(self):
        self.bind()
        before = self.snapshot_state()
        view = self.view()
        self.assertEqual(view['gates'][0]['status'], 'approved')
        self.assertIn('matches confirmed seed (sha256:', view['definition']['evidence'])
        self.assertIsNone(view['definition']['question'])
        self.assertEqual(view['definition']['departures'], [])
        self.assertEqual(view['decisions'][-1]['basis'], 'confirmed-seed')
        self.assertEqual(self.snapshot_state(), before)  # Status never writes an approval.
        self.assertNotIn('After human review', view['cli_status'])
        (self.path / 'brief.md').write_text('TEST rendering of the unchanged seed checks')
        self.assertEqual(self.view()['gates'][0]['status'], 'approved')

    def test_human_rejection_survives_input_edits_and_automatic_or_agent_records(self):
        self.bind()
        rejected = self.sdlc('decide', 'r', '3', 'changes-requested', '--reviewer', 'TEST human',
                             '--source', 'TEST rejection', '--reason', 'TEST do not proceed')
        self.assertEqual(rejected.returncode, 0, rejected.stderr)
        self.assertEqual(self.view()['gates'][0]['status'], 'changes-requested')
        brief = self.path / 'brief.md'
        brief.write_text(brief.read_text() + '\nTEST edit after rejection\n')
        self.assertEqual(self.view()['gates'][0]['status'], 'changes-requested')
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)
        rejection = json.loads(sorted((self.path / 'decisions').glob('3-*.json'))[-1].read_text())
        # Historical automatic or agent receipts cannot supersede a human rejection.
        for fields in [{'basis': 'confirmed-seed', 'reviewer': 'confirmed-seed binding (automatic)'},
                       {'reviewer': 'Agent TEST', 'actor': {'by': 'agent'}}]:
            record = {**rejection, 'decision': 'approved', **fields}
            (self.path / 'decisions' / f'3-{time.time_ns()}-TEST.json').write_text(json.dumps(record))
            self.assertEqual(self.view()['gates'][0]['status'], 'changes-requested')
            self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)
        self.decide('r', 3)  # Only a new synthetic human decision lifts the rejection.
        self.assertEqual(self.view()['gates'][0]['status'], 'approved')
        self.assertEqual(self.sdlc('verify', 'r').returncode, 0)

    def test_seed_bytes_and_definitions_must_match_before_auto_pass(self):
        self.bind()
        self.seed = self.path / 'seed-contract.md'
        self.seed.write_text(self.seed.read_text() + 'TEST change')
        self.assertNotEqual(self.view()['gates'][0]['status'], 'approved')
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)

    def test_added_removed_changed_checks_and_activation_are_plain_departures(self):
        self.bind([command(), provisional(), provisional('false'), {**DEFAULT[0], 'how_to_judge': 'Inspect'}])
        self.change(lambda items: (items.pop(3), items[0].update(cmd='false'),
                                  items[1].update(activation={'cmd': 'true', 'cwd': '.', 'evidence': 'TEST ready'})))
        worker = {'schema': 1, 'items': [{'id': 'extra', 'description': 'TEST extra check',
                                        'verify': 'TEST output', 'check': PASSING}]}
        (self.path / 'checklist.json').write_text(json.dumps(worker))
        view = self.view()
        diff = view['definition']['departures']
        self.assertTrue(any('seed-4 removed' in line for line in diff))
        self.assertTrue(any('seed-1 cmd changed: `false` instead of `true`' in line for line in diff))
        self.assertTrue(any('seed-2 activated' in line for line in diff))
        self.assertTrue(any('extra added worker check' in line for line in diff))
        self.assertNotEqual(view['gates'][0]['status'], 'approved')
        self.assertEqual(view['definition']['question'], 'Accept these departures from the confirmed seed?')
        for line in diff:
            self.assertEqual(view['cli_status'].count(line), 1)
        self.decide('r', 3)
        self.assertEqual(self.view()['gates'][0]['status'], 'approved')
        self.assertIsNone(self.view()['definition']['question'])
        self.change(lambda items: items[0].update(cmd='true'))
        self.assertEqual(self.view()['gates'][0]['status'], 'stale')

    def test_legacy_binding_uses_intake_digest_not_a_new_baseline(self):
        self.bind([provisional('./future')])
        metadata = self.path / 'run.json'
        value = json.loads(metadata.read_text())
        value['seed_contract'].pop('definitions')
        metadata.write_text(json.dumps(value))
        self.assertEqual(self.view()['gates'][0]['status'], 'approved')
        self.change(lambda items: items[0].update(activation={'cmd': 'true', 'cwd': '.', 'evidence': 'TEST ready'}))
        self.assertTrue(self.view()['definition']['departures'])
        self.assertNotEqual(self.view()['gates'][0]['status'], 'approved')
        self.change(lambda items: items[0].update(statement='TEST weakened'))
        self.assertNotEqual(self.view()['gates'][0]['status'], 'approved')
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)

    def test_command_output_proves_command_without_expect_mark(self):
        self.bind([command('test -f accepted')])
        view = self.view()
        self.assertEqual([item['id'] for item in view['checklist']], ['seed-1'])
        self.assertFalse(any(item.get('operator_attestation') for item in view['checklist']))
        failed = self.sdlc('verify', 'r')
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('Seed check failed: seed-1', failed.stderr)
        (self.root / 'accepted').touch()
        passed = self.sdlc('verify', 'r')
        self.assertEqual(passed.returncode, 0, passed.stderr)
        receipt = json.loads((self.path / '04-test/output/verification.json').read_text())
        self.assertEqual(receipt['seed_checks'], {'seed-1': 'proven'})
        self.assertIn('$ ["bash", "-c", "test -f accepted"]',
                      (self.path / '04-test/output/test-log.md').read_text())

    def test_activated_command_is_output_proven_but_activation_needs_human(self):
        self.bind([provisional('./future')])
        self.change(lambda items: items[0].update(activation={'cmd': 'true', 'cwd': '.', 'evidence': 'TEST ready'}))
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)
        self.decide('r', 3)
        passed = self.sdlc('verify', 'r')
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual([item['id'] for item in self.view()['checklist']], ['seed-1'])

    def test_judge_still_needs_actual_human_attestation(self):
        self.bind([DEFAULT[0]])
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)
        refused = self.sdlc('mark', 'r', 'seed-1', 'passed', '--evidence', 'TEST guess')
        self.assertNotEqual(refused.returncode, 0)
        marked = self.sdlc('mark', 'r', 'seed-1', 'passed', '--evidence', 'TEST observed',
                           '--reviewer', 'TEST human', '--source', 'TEST synthetic observation')
        self.assertEqual(marked.returncode, 0, marked.stderr)
        self.assertEqual(self.sdlc('verify', 'r').returncode, 0)


class HumanOnlyGates(StageBase):
    def test_decide_refuses_declared_agents_without_writes(self):
        self.new_run('r', approve=False)
        for flags in [('--reviewer', 'Agent: TEST worker'), ('--reviewer', 'TEST worker; not human'),
                      ('--reviewer', 'TEST name', '--actor', '{"by":"agent"}')]:
            with self.subTest(flags=flags):
                before = self.snapshot_state()
                result = self.sdlc('decide', 'r', '3', 'approved', *flags, '--source', 'TEST', '--reason', 'TEST')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('human-only gate', result.stderr)
                self.assertEqual(self.snapshot_state(), before)

    def test_historical_agent_decision_stays_readable_but_does_not_approve(self):
        self.new_run('r')
        path = self.root / 'runs/r/decisions'
        receipt = next(path.glob('3-*.json'))
        original = json.loads(receipt.read_text())
        for mutation in [{'reviewer': 'Agent: TEST worker'}, {'reviewer': 'TEST not human'}, {'actor': {'by': 'agent'}}]:
            with self.subTest(mutation=mutation):
                receipt.write_text(json.dumps({**original, **mutation}))
                before = self.snapshot_state()
                status = self.sdlc('status', 'r', '--json')
                self.assertEqual(status.returncode, 0, status.stderr)
                view = json.loads(status.stdout)['run']
                self.assertEqual(view['gates'][0]['status'], 'pending')
                self.assertEqual(view['gates'][0]['attribution'], 'agent-recorded')
                self.assertEqual(view['decisions'][-1]['attribution'], 'agent-recorded')
                self.assertEqual(view['verification_status'], 'blocked')
                self.assertEqual(self.snapshot_state(), before)
                self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)

    def test_mark_refuses_agents_and_discloses_legacy_agent_marks(self):
        self.new_run('r', approve=False)
        seed = confirmed(self.base / 'TEST-sessions')
        self.stage('r', 1, '--seed-contract', str(seed), '--tinkery', str(TINKERY))
        self.decide('r', 3)
        self.set_checks([PASSING])
        for flags in [('--reviewer', 'Agent TEST'), ('--reviewer', 'TEST not human'),
                      ('--reviewer', 'TEST person', '--actor', '{"by":"agent"}')]:
            with self.subTest(flags=flags):
                before = self.snapshot_state()
                result = self.sdlc('mark', 'r', 'seed-1', 'passed', '--evidence', 'TEST observation',
                                   *flags, '--source', 'TEST')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('human-only gate', result.stderr)
                self.assertEqual(self.snapshot_state(), before)
        marked = self.sdlc('mark', 'r', 'seed-1', 'passed', '--evidence', 'TEST observation',
                           '--reviewer', 'TEST human', '--source', 'TEST')
        self.assertEqual(marked.returncode, 0, marked.stderr)
        self.assertEqual(self.sdlc('verify', 'r').returncode, 0)
        receipt = next((self.root / 'runs/r/marks').glob('*.json'))
        record = json.loads(receipt.read_text())
        (receipt.parent / 'TEST-malformed.json').write_text('[]')
        for mutation in [{'reviewer': 'Agent TEST'}, {'reviewer': 'TEST not human'}, {'actor': {'by': 'agent'}}]:
            receipt.write_text(json.dumps({**record, **mutation}))
            view = json.loads(self.sdlc('status', 'r', '--json').stdout)['run']
            item = next(item for item in view['checklist'] if item['id'] == 'seed-1')
            self.assertEqual(item['status'], 'pending')
            self.assertEqual(item['attribution'], 'agent-recorded')
            self.assertNotEqual(view['verification_status'], 'current')
            self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)
