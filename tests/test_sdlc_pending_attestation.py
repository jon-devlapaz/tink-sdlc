import hashlib
import json

from test_sdlc_stage import StageBase, IDENT
from tinkery_fixture import TINKERY, confirmed, command, DEFAULT


class PendingAttestation(StageBase):
    def setUp(self):
        super().setUp()
        self.new_run('r', approve=False, check={
            'argv': ['python3', '-c', 'print("worker proof")'], 'timeout_seconds': 5})
        seed = confirmed(self.base / 'TEST-sessions', [command(), DEFAULT[0]])
        self.stage('r', 1, '--seed-contract', str(seed), '--tinkery', str(TINKERY))
        self.path = self.root / 'runs/r'
        definitions = self.path / 'seed-checks.json'
        value = json.loads(definitions.read_text())
        value['items'][0]['cmd'] = 'printf "seed proof\\n"'
        definitions.write_text(json.dumps(value))
        self.output = self.path / '04-test/output'
        self.set_checks([{'argv': ['python3', '-c', 'print("configured proof")'], 'timeout_seconds': 5}])
        self.decide('r', 3)
        self.assertEqual(self.sdlc('lock-tests', 'r', 'AGENTS.md', '--source', 'synthetic independent review',
                                   '--failure-evidence', 'synthetic fixture baseline').returncode, 0)

    def receipt(self):
        return json.loads((self.output / 'verification.json').read_text())

    def view(self):
        result = self.sdlc('status', 'r', '--json')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)['run']

    def attest(self, result='passed'):
        marked = self.sdlc('mark', 'r', 'seed-2', result, '--reviewer', 'synthetic operator',
                           '--source', 'isolated fixture', '--evidence', f'synthetic observation: {result}')
        self.assertEqual(marked.returncode, 0, marked.stderr)

    def pending(self):
        result = self.sdlc('verify', 'r')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Checklist incomplete: seed-2', result.stderr)
        return result

    def test_commands_run_and_log_before_nonzero_attestation_error(self):
        self.pending()
        log = (self.output / 'test-log.md').read_text()
        for text in ('configured proof', '# checklist item a', 'worker proof', '# seed check seed-1', 'seed proof'):
            self.assertIn(text, log)
        config = self.root / '_system/verification.json'
        original = config.read_text()
        failures = [
            (config, json.dumps({'require_tink': False, 'checks': [
                {'argv': ['python3', '-c', 'raise SystemExit(7)'], 'timeout_seconds': 5}]}), 'Check failed (7)'),
            (self.path / 'checklist.json', json.dumps({'schema': 1, 'items': [
                {'id': 'a', 'description': 'd', 'verify': 'v', 'check': {
                    'argv': ['python3', '-c', 'raise SystemExit(8)'], 'timeout_seconds': 5}}]}), 'Checklist check failed: a'),
            (self.root / 'failing-seed', 'present', 'Seed check failed: seed-1'),
        ]
        config.write_text(original)
        definitions = self.path / 'seed-checks.json'
        value = json.loads(definitions.read_text())
        value['items'][0]['cmd'] = 'test ! -f failing-seed && printf "seed proof\\n"'
        definitions.write_text(json.dumps(value))
        self.decide('r', 3)
        for file, text, error in failures:
            with self.subTest(error=error):
                before = file.read_bytes() if file.exists() else None
                file.write_text(text)
                self.decide('r', 3)
                failed = self.sdlc('verify', 'r')
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn(error, failed.stderr)
                self.assertEqual(self.receipt()['result'], 'failed')
                self.assertIsNone(self.view()['verification']['candidate'])
                if before is None:
                    file.unlink()
                else:
                    file.write_bytes(before)

    def test_receipt_retains_candidate_policy_lock_and_pending_ids(self):
        self.pending()
        record = self.receipt()
        self.assertEqual(record['result'], 'awaiting-attestation')
        self.assertEqual(record['candidate']['head'], self.git('rev-parse', 'HEAD').stdout.strip())
        self.assertRegex(record['candidate']['tree'], r'^[0-9a-f]{64}$')
        for key, file in [('policy', self.root / '_system/verification.json'),
                          ('test_lock', self.path / 'test-lock.json')]:
            self.assertEqual(record[key], hashlib.sha256(file.read_bytes()).hexdigest())
        self.assertTrue(record['inputs'])
        self.assertTrue(record['checklist'])
        self.assertEqual(record['manual_marks'], {})
        self.assertEqual(record['pending_attestations'], ['seed-2'])
        self.assertEqual(record['seed_checks'], {'seed-1': 'proven', 'seed-2': 'pending'})
        self.assertEqual(record['checklist_items'], {'a': 'proven'})
        self.assertEqual(record['log'], hashlib.sha256((self.output / 'test-log.md').read_bytes()).hexdigest())
        self.attest()
        self.assertEqual(self.sdlc('verify', 'r').returncode, 0)
        self.assertEqual(self.receipt()['candidate'], record['candidate'])
        self.assertNotIn('pending_attestations', self.receipt())
        checklist = self.path / 'checklist.json'
        value = json.loads(checklist.read_text())
        value['items'] += [{'id': name, 'description': name, 'verify': 'Observe'} for name in ('b', 'c')]
        checklist.write_text(json.dumps(value))
        self.decide('r', 3)
        result = self.sdlc('verify', 'r')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Checklist incomplete: b, c', result.stderr)
        self.assertEqual(self.receipt()['pending_attestations'], ['b', 'c'])
        self.assertEqual(self.receipt()['checklist_items'], {'a': 'proven', 'b': 'pending', 'c': 'pending'})

    def test_fresh_status_is_awaiting_attestation_and_never_passes_a_gate(self):
        self.pending()
        view = self.view()
        self.assertEqual(view['verification_status'], 'awaiting-attestation')
        self.assertFalse(view['verification']['passed'])
        self.assertEqual(view['verification']['result'], 'awaiting-attestation')
        self.assertEqual(view['verification']['pending_attestations'], ['seed-2'])
        self.assertIn('Checklist incomplete: seed-2', view['next_action'])
        self.assertIn('Verification: awaiting-attestation', self.sdlc('status', 'r').stdout)
        summary = json.loads(self.sdlc('status', '--json').stdout)['runs'][0]
        self.assertEqual(summary['verification_status'], 'awaiting-attestation')
        self.assertEqual(next(i for i in view['checklist'] if i['id'] == 'seed-2')['status'], 'pending')
        refused = self.sdlc('pr', 'r', 'https://github.com/TEST/example/pull/1')
        self.assertNotEqual(refused.returncode, 0)
        self.assertFalse((self.path / 'pr.json').exists())
        receipt = self.output / 'verification.json'
        original = receipt.read_text()
        for invalid in (None, [], 'seed-2', [{}]):
            with self.subTest(pending_ids=invalid):
                record = json.loads(original)
                record['pending_attestations'] = invalid
                receipt.write_text(json.dumps(record))
                refused = self.sdlc('status', 'r', '--json')
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn('pending_attestations', refused.stdout)
        receipt.write_text(original)
        self.attest()
        self.assertEqual(self.sdlc('verify', 'r').returncode, 0)
        self.assertTrue(self.view()['verification']['passed'])

    def test_freshness_retires_after_marks_and_input_changes_but_not_evidence_commits(self):
        self.pending()
        head = self.receipt()['candidate']['head']
        self.git('add', '.')
        self.git('commit', '-qm', 'synthetic evidence only', env=IDENT)
        self.assertNotEqual(self.git('rev-parse', 'HEAD').stdout.strip(), head)
        self.assertEqual(self.view()['verification_status'], 'awaiting-attestation')
        self.assertEqual(self.view()['verification']['candidate']['head'], head)
        changes = [self.root / 'new-source', self.root / '_system/verification.json',
                   self.path / 'test-lock.json', self.output / 'test-log.md', self.path / 'brief.md',
                   self.path / 'checklist.json', self.path / 'seed-checks.json']
        for file in changes:
            with self.subTest(file=file.name):
                before = file.read_bytes() if file.exists() else None
                if file.name in ('checklist.json', 'seed-checks.json'):
                    value = json.loads(before)
                    value['items'][0]['description'] += ' changed'
                    file.write_text(json.dumps(value))
                else:
                    file.write_bytes((before or b'') + b'\n')
                if file.name in ('brief.md', 'checklist.json', 'seed-checks.json'):
                    self.decide('r', 3)
                view = self.view()
                self.assertIn(view['verification_status'], ('stale', 'not-run'))
                self.assertFalse(view['verification']['passed'])
                if before is None:
                    file.unlink()
                else:
                    file.write_bytes(before)
                if file.name in ('brief.md', 'checklist.json', 'seed-checks.json'):
                    self.decide('r', 3)
                self.assertEqual(self.view()['verification_status'], 'awaiting-attestation')
        for result in ('unknown', 'failed', 'passed'):
            with self.subTest(mark=result):
                self.pending()
                self.attest(result)
                self.assertIn(self.view()['verification_status'], ('stale', 'not-run'))
                self.assertFalse(self.view()['verification']['passed'])
        self.assertEqual(self.sdlc('verify', 'r').returncode, 0)
        self.assertEqual(self.view()['verification_status'], 'current')

    def test_mark_and_decide_still_refuse_agent_recorded_human_decisions(self):
        self.pending()
        before = {p: p.read_bytes() for folder in ('marks', 'decisions')
                  for p in (self.path / folder).glob('*.json')}
        for flags in [('--reviewer', 'Agent Pi'), ('--reviewer', 'worker not human'),
                      ('--reviewer', 'synthetic operator', '--actor', '{"by":"agent"}')]:
            for args in [('mark', 'r', 'seed-2', 'passed', '--evidence', 'agent guess'),
                         ('decide', 'r', '3', 'approved', '--reason', 'agent guess')]:
                with self.subTest(args=args, flags=flags):
                    refused = self.sdlc(*args, *flags, '--source', 'isolated fixture')
                    self.assertNotEqual(refused.returncode, 0)
                    self.assertIn('human-only', refused.stderr)
        refused = self.sdlc('mark', 'r', 'seed-2', 'passed', '--evidence', 'worker only')
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('operator', refused.stderr)
        after = {p: p.read_bytes() for folder in ('marks', 'decisions')
                 for p in (self.path / folder).glob('*.json')}
        self.assertEqual(after, before)
        self.assertEqual(self.view()['verification_status'], 'awaiting-attestation')
