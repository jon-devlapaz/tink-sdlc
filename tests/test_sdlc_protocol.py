"""Provider contract: exercise the shipped runtime, not a second implementation."""
import fcntl
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_sdlc_workflow as fixtures


class ProtocolTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    git = fixtures.WorkflowTests.git
    config = fixtures.WorkflowTests.config
    cli = fixtures.WorkflowTests.cli
    approve = fixtures.WorkflowTests.approve
    create_ready = fixtures.WorkflowTests.create_ready

    def machine(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(self.root / '_system/scripts/sdlc.py'), *args, '--json'],
                                cwd='/', capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        value = json.loads(result.stdout)
        self.assertEqual(value['protocol'], 'tink-sdlc')
        self.assertEqual(value['api_version'], 1)
        self.assertEqual(value['workspace'], str(self.root))
        return value

    def view(self):
        view = self.machine('status', 'example')['run']
        self.assertLessEqual({'slug', 'meta', 'gates', 'verification_status', 'verification', 'has_lock',
                              'actions', 'checklist', 'checklist_state', 'artifacts', 'decisions', 'errors',
                              'next_action', 'verification_config', 'log', 'cli_status'}, set(view))
        for name in ('verify', 'mark'):
            self.assertIs(type(view['actions'][name]['allowed']), bool)
            self.assertIsInstance(view['actions'][name]['reason'], str)
        self.assertLessEqual({'path', 'text', 'truncated'}, set(view['log']))
        return view

    def checked_run(self, profile='light'):
        self.cli('new', 'example', '--profile', profile)
        items = [{'id': 'automatic', 'description': 'Real assertion', 'verify': 'Execute the check',
                  'check': {'argv': [sys.executable, '-c', 'print("check output Ω"); assert 2 + 2 == 4'], 'timeout_seconds': 5}},
                 {'id': 'manual', 'description': 'Observed result', 'verify': 'Record evidence'}]
        (self.root / 'runs/example/checklist.json').write_text(json.dumps({'schema': 1, 'items': items}))
        for stage in (['3'] if profile == 'light' else ['1', '2', '3']):
            self.approve(stage)

    def mark_manual(self):
        self.cli('mark', 'example', 'manual', 'passed', '--evidence', 'Synthetic fixture: observed expected result')

    def test_capabilities_are_machine_readable_and_not_the_package_version(self):
        value = self.machine('capabilities')
        self.assertEqual(set(value['capabilities']), {'status-json', 'verify', 'mark', 'log-file'})
        self.assertEqual(self.machine('status')['runs'], [])

    def test_status_is_read_only_and_reports_pending_actions_and_log_reference(self):
        self.cli('new', 'example')
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        view = self.view()
        self.assertEqual(view['slug'], 'example')
        self.assertEqual(view['meta']['profile'], 'light')
        self.assertEqual(view['verification_status'], 'blocked')
        self.assertEqual(view['gates'], [{'stage': 3, 'status': 'pending', 'blocked': False}])
        for action in ('verify', 'mark'):
            self.assertFalse(view['actions'][action]['allowed'])
            self.assertTrue(view['actions'][action]['reason'])
        self.assertEqual(view['log']['path'], 'runs/example/04-test/output/test-log.md')
        self.assertEqual(view['log']['text'], '')
        self.assertFalse(view['log']['truncated'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(view['cli_status'] + '\n', self.cli('status', 'example'))

    def test_real_light_run_evidence_and_actions(self):
        self.checked_run()
        initial = self.view()
        self.assertEqual(initial['verification_status'], 'not-run')
        self.assertTrue(initial['actions']['verify']['allowed'])
        self.assertTrue(initial['actions']['mark']['allowed'])
        self.assertEqual(initial['actions']['verify']['timeout_seconds'], 40)
        self.cli('verify', 'example', ok=False)
        failed = self.view()
        self.assertEqual(failed['verification_status'], 'failed')
        self.assertIn('Checklist incomplete', failed['next_action'])
        self.mark_manual()
        self.cli('verify', 'example')
        view = self.view()
        self.assertEqual(view['verification_status'], 'current')
        self.assertTrue(view['verification']['passed'])
        self.assertTrue(view['verification']['candidate']['tree'])
        self.assertEqual([i['status'] for i in view['checklist']], ['passed', 'passed'])
        self.assertEqual([i['proof'] for i in view['checklist']], ['automated', 'attested'])
        self.assertIn('Synthetic fixture', view['checklist'][1]['mark']['evidence'])
        self.assertIn('check output Ω', view['log']['text'])
        self.assertEqual(view['cli_status'] + '\n', self.cli('status', 'example'))
        summary = self.machine('status')['runs'][0]
        self.assertEqual(summary['verification_status'], 'current')
        self.assertEqual(summary['checklist_summary'], {'total': 2, 'passed': 2})
        self.assertEqual(view['decisions'][0]['stage'], 3)
        self.assertIsInstance(view['decisions'][0]['timestamp_ns'], int)

    def test_full_profile_uses_the_same_gate_calculation(self):
        self.checked_run('full')
        view = self.view()
        self.assertEqual([g['stage'] for g in view['gates']], [1, 2, 3])
        self.assertEqual(set(view['artifacts']), {'intent', 'spec', 'plan'})
        (self.root / 'runs/example/01-plan/output/intent.md').write_text('changed intent')
        stale = self.view()
        self.assertEqual([g['status'] for g in stale['gates']], ['stale'] * 3)
        self.assertEqual([g['blocked'] for g in stale['gates']], [False, True, True])
        self.assertFalse(stale['actions']['verify']['allowed'])
        self.assertFalse(stale['actions']['mark']['allowed'])
        self.assertEqual(stale['cli_status'] + '\n', self.cli('status', 'example'))

    def test_changed_candidate_mark_and_log_invalidate_current_evidence(self):
        self.checked_run()
        self.mark_manual()
        self.cli('verify', 'example')
        code = self.root / 'code.py'
        code.write_text('changed candidate')
        view = self.view()
        self.assertEqual(view['verification_status'], 'stale')
        self.assertFalse(view['verification']['passed'])
        self.assertTrue(view['checklist'][1]['needs_recheck'])
        self.assertEqual(view['checklist'][0]['status'], 'pending')
        self.cli('verify', 'example')
        self.mark_manual()
        self.assertEqual(self.view()['verification_status'], 'stale')
        self.cli('verify', 'example')
        (self.root / self.view()['log']['path']).write_text('altered log')
        self.assertEqual(self.view()['verification_status'], 'stale')

    def test_status_and_review_entry_reject_the_same_stale_evidence(self):
        self.checked_run()
        self.mark_manual()
        path = self.root / 'runs/example'
        changes = {
            'candidate': lambda: (self.root / 'code.py').write_text('changed candidate'),
            'log': lambda: (path / '04-test/output/test-log.md').write_text('altered log'),
            'policy': lambda: self.config([{'argv': [sys.executable, '-c', 'print("new check")'], 'timeout_seconds': 5}]),
            'observation': self.mark_manual,
        }
        with patch.object(fixtures.workflow, 'ROOT', self.root):
            for name, change in changes.items():
                with self.subTest(changed=name):
                    self.cli('verify', 'example')
                    self.assertTrue(self.view()['verification']['passed'])
                    fixtures.workflow.stage_entry_gates(path, 'example', 5)
                    change()
                    self.assertEqual(self.view()['verification_status'], 'stale')
                    with self.assertRaisesRegex(ValueError, 'run verify first'):
                        fixtures.workflow.stage_entry_gates(path, 'example', 5)

    def test_failed_timeout_and_interrupted_receipts_do_not_reuse_a_pass(self):
        self.create_ready()
        self.cli('verify', 'example')
        for code, timeout in [('raise SystemExit(7)', 5), ('import time; time.sleep(3)', 1)]:
            self.config([{'argv': [sys.executable, '-c', code], 'timeout_seconds': timeout}])
            self.cli('verify', 'example', ok=False)
            view = self.view()
            self.assertEqual(view['verification_status'], 'failed')
            self.assertFalse(view['verification']['passed'])
        receipt = self.root / 'runs/example/04-test/output/verification.json'
        receipt.write_text(json.dumps({'result': 'running'}))
        self.assertEqual(self.view()['verification_status'], 'interrupted')

    def test_writer_lock_prevents_actions_and_current_display(self):
        self.create_ready()
        self.cli('verify', 'example')
        with (self.root / 'runs/example/.writer-lock').open() as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            view = self.view()
            self.assertEqual(view['verification_status'], 'running')
            self.assertTrue(view['has_lock'])
            self.assertFalse(view['verification']['passed'])
            self.assertFalse(view['actions']['verify']['allowed'])
            self.assertFalse(view['actions']['mark']['allowed'])
        self.assertEqual(self.view()['verification_status'], 'current')

    def test_invalid_configuration_does_not_disable_valid_manual_observation(self):
        self.checked_run()
        self.config([])
        view = self.view()
        self.assertFalse(view['actions']['verify']['allowed'])
        self.assertIn('nonempty', view['actions']['verify']['reason'])
        self.assertTrue(view['actions']['mark']['allowed'])

    def test_missing_bug_baseline_blocks_verification(self):
        self.create_ready('--kind', 'bug')
        view = self.view()
        self.assertFalse(view['actions']['verify']['allowed'])
        self.assertIn('reproduction test lock', view['actions']['verify']['reason'])

    def test_missing_and_invalid_runs_have_structured_errors(self):
        missing = self.machine('status', 'missing', ok=False)
        self.assertEqual(missing['error']['code'], 'not-found')
        invalid = self.machine('status', '../outside', ok=False)
        self.assertEqual(invalid['error']['code'], 'invalid-state')
        self.cli('new', 'example')
        (self.root / 'runs/example/run.json').write_text('{')
        invalid = self.machine('status', 'example', ok=False)
        self.assertEqual(invalid['error']['code'], 'invalid-state')
        listed = self.machine('status')['runs'][0]
        self.assertEqual(listed['verification_status'], 'invalid')
        self.assertTrue(listed['error'])

    def assert_invalid_run_is_isolated(self):
        detail = self.machine('status', 'example', ok=False)
        self.assertEqual(detail['error']['code'], 'invalid-state')
        listed = {run['slug']: run for run in self.machine('status')['runs']}
        self.assertEqual(set(listed), {'example', 'healthy'})
        self.assertEqual(listed['example']['verification_status'], 'invalid')
        self.assertTrue(listed['example']['error'])
        self.assertEqual(listed['healthy']['verification_status'], 'blocked')
        self.assertEqual(listed['healthy']['kind'], 'feature')

    def test_malformed_metadata_and_receipt_fields_do_not_hide_healthy_runs(self):
        self.cli('new', 'example')
        self.cli('new', 'healthy')
        metadata = self.root / 'runs/example/run.json'
        original = json.loads(metadata.read_text())
        for kind in (None, True, 17, [], {}):
            with self.subTest(kind=kind):
                metadata.write_text(json.dumps({**original, 'kind': kind}))
                self.assert_invalid_run_is_isolated()
        metadata.write_text(json.dumps(original))
        receipt = self.root / 'runs/example/04-test/output/verification.json'
        receipt.parent.mkdir(parents=True)
        malformed = [
            *({field: value} for field in ('error', 'log') for value in (False, 17, [], {})),
            *({'candidate': value} for value in (False, 17, 'invalid', [], {},
                                                {'head': 17, 'tree': 'tree'},
                                                {'head': '', 'tree': 'tree'}, {'head': 'commit'})),
        ]
        for fields in malformed:
            with self.subTest(receipt=fields):
                receipt.write_text(json.dumps({'result': 'failed', **fields}))
                self.assert_invalid_run_is_isolated()
        receipt.write_text(json.dumps({'result': 'failed', 'error': 'Readable failure'}))
        self.assertEqual(self.view()['verification']['error'], 'Readable failure')
        del original['kind']
        metadata.write_text(json.dumps(original))
        self.assertEqual(self.view()['meta']['kind'], 'feature')

    def test_malformed_historical_decisions_do_not_hide_healthy_runs(self):
        self.create_ready()
        self.cli('new', 'healthy')
        self.approve('3')
        receipt = sorted((self.root / 'runs/example/decisions').glob('*.json'))[0]
        original = json.loads(receipt.read_text())
        malformed = [*({field: value} for field in ('reviewer', 'source', 'reason')
                       for value in (False, 17, [], {})),
                     *({'decision': value} for value in (None, True, [], 'pending', 'stale', 'new-state'))]
        for fields in malformed:
            with self.subTest(decision=fields):
                receipt.write_text(json.dumps({**original, **fields}))
                self.assert_invalid_run_is_isolated()
        receipt.write_text(json.dumps({**original, 'reviewer': None, 'source': None, 'reason': None,
                                       'future-field': {'extra': True}}))
        projected = self.view()['decisions'][0]
        self.assertEqual(projected['decision'], 'approved')
        self.assertIsNone(projected['reviewer'])

    def test_deep_json_is_isolated_with_a_structured_error(self):
        self.cli('new', 'example')
        self.cli('new', 'healthy')
        (self.root / 'runs/example/run.json').write_text('[' * 20000 + '0' + ']' * 20000)
        self.assert_invalid_run_is_isolated()

    def test_looping_evidence_symlink_is_isolated_across_python_versions(self):
        self.cli('new', 'example')
        self.cli('new', 'healthy')
        brief = self.root / 'runs/example/brief.md'
        brief.unlink()
        brief.symlink_to(brief.name)
        self.assert_invalid_run_is_isolated()

    def test_large_log_tail_is_bounded_and_explicit(self):
        self.create_ready()
        log = self.root / 'runs/example/04-test/output/test-log.md'
        log.parent.mkdir(parents=True)
        log.write_text('x' * 300000)
        view = self.view()
        self.assertEqual(len(view['log']['text']), 262144)
        self.assertTrue(view['log']['truncated'])

    def test_symlinked_evidence_is_not_exposed_by_the_api(self):
        self.create_ready()
        brief = self.root / 'runs/example/brief.md'
        brief.unlink()
        brief.symlink_to(self.root / 'code.py')
        result = self.machine('status', 'example', ok=False)
        self.assertIn('symlink', result['error']['message'].lower())


if __name__ == '__main__':
    unittest.main()
