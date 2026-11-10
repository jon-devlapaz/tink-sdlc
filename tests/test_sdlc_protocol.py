"""Provider contract: exercise the shipped runtime, not a second implementation."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from tinkery_fixture import TINKERY, confirmed
import unittest
import uuid
from unittest.mock import patch

import test_sdlc_workflow as fixtures
from test_sdlc_stage import StageBase


class ProtocolTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    git = fixtures.WorkflowTests.git
    config = fixtures.WorkflowTests.config
    cli = fixtures.WorkflowTests.cli
    approve = fixtures.WorkflowTests.approve
    define_checklist = fixtures.WorkflowTests.define_checklist
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
        self.assertEqual(set(value['capabilities']), {'status-json', 'verify', 'mark', 'log-file', 'pr', 'close', 'label'})
        self.assertEqual(self.machine('status')['runs'], [])

    def test_run_ids_distinguish_creations_in_both_status_views(self):
        seen = set()
        for profile in ('light', 'full', 'light'):
            with self.subTest(profile=profile):
                self.cli('new', 'example', '--profile', profile)
                metadata = self.root / 'runs/example/run.json'
                before = metadata.read_bytes()
                run_id = json.loads(before)['run_id']
                self.assertEqual(uuid.UUID(run_id).version, 4)
                self.assertEqual(str(uuid.UUID(run_id)), run_id)
                self.assertNotIn(run_id, seen)
                seen.add(run_id)
                self.assertEqual(self.view()['run_id'], run_id)
                self.assertEqual(self.machine('status')['runs'][0]['run_id'], run_id)
                self.cli('new', 'example', ok=False)
                self.assertEqual(metadata.read_bytes(), before)
                shutil.rmtree(metadata.parent)

    def test_run_id_survives_decisions_verification_and_reopening(self):
        self.checked_run()
        metadata = self.root / 'runs/example/run.json'
        run_id = json.loads(metadata.read_text())['run_id']
        self.mark_manual()
        self.cli('verify', 'example')
        self.assertEqual(self.view()['run_id'], run_id)
        self.assertEqual(self.view()['verification_status'], 'current')
        (self.root / 'runs/example/brief.md').write_text('Revised scope')
        self.assertEqual(self.view()['gates'][0]['status'], 'stale')
        self.approve()
        self.mark_manual()
        self.cli('verify', 'example')
        self.assertEqual(self.view()['verification_status'], 'current')
        self.assertEqual(json.loads(metadata.read_text())['run_id'], run_id)
        self.assertEqual(self.machine('status')['runs'][0]['run_id'], run_id)

    def test_a_run_without_an_id_is_refused_without_repair(self):
        self.cli('new', 'example')
        self.cli('new', 'healthy')
        metadata = self.root / 'runs/example/run.json'
        record = json.loads(metadata.read_text())
        record.pop('run_id')
        metadata.write_text(json.dumps(record))
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assert_invalid_run_is_isolated()
        self.assertIn('run_id must be a canonical UUIDv4', self.cli('status', 'example', ok=False))
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_malformed_run_ids_are_refused_without_repair_or_hidden_healthy_runs(self):
        self.cli('new', 'example')
        self.cli('new', 'healthy')
        metadata = self.root / 'runs/example/run.json'
        original = json.loads(metadata.read_text())
        for run_id in (None, True, 17, [], {}, '', 'not-a-uuid',
                       'A9CF2941-8B28-4A28-8F77-739194B421D3',
                       'a9cf29418b284a288f77739194b421d3',
                       'a9cf2941-8b28-1a28-8f77-739194b421d3',
                       'a9cf2941-8b28-4a28-0f77-739194b421d3'):
            with self.subTest(run_id=run_id):
                metadata.write_text(json.dumps({**original, 'run_id': run_id}))
                before = {p: p.read_bytes() for p in metadata.parent.rglob('*') if p.is_file()}
                self.assert_invalid_run_is_isolated()
                error = self.cli('decide', 'example', '3', 'changes-requested',
                                 '--reviewer', 'fixture', '--source', 'test', '--reason', 'test', ok=False)
                self.assertIn('run_id', error)
                self.assertEqual(before, {p: p.read_bytes() for p in metadata.parent.rglob('*') if p.is_file()})

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

    def test_a_seed_binding_without_record_intake_is_refused(self):
        self.cli('new', 'example')
        self.cli('new', 'healthy')
        path = self.root / 'runs/example'
        seed = path / 'seed-contract.md'
        seed.write_text('## Acceptance criteria\nTEST handwritten requirement\n')
        metadata = json.loads((path / 'run.json').read_text())
        metadata['seed_contract'] = {'path': 'runs/example/seed-contract.md',
                                     'sha256': hashlib.sha256(seed.read_bytes()).hexdigest(),
                                     'source': '/tmp/TEST-session/seed-contract.md'}
        (path / 'run.json').write_text(json.dumps(metadata))
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assert_invalid_run_is_isolated()
        for command in (['verify', 'example'],
                        ['decide', 'example', '3', 'approved', '--reviewer', 'h', '--source', 's', '--reason', 'r']):
            with self.subTest(command=command[0]):
                self.assertIn('Seed binding has no record intake', self.cli(*command, ok=False))
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_real_light_run_evidence_and_actions(self):
        self.checked_run()
        initial = self.view()
        self.assertEqual(initial['verification_status'], 'not-run')
        self.assertTrue(initial['actions']['verify']['allowed'])
        self.assertTrue(initial['actions']['mark']['allowed'])
        self.assertEqual(initial['actions']['verify']['timeout_seconds'], 40)
        self.cli('verify', 'example', ok=False)
        pending = self.view()
        self.assertEqual(pending['verification_status'], 'awaiting-attestation')
        self.assertFalse(pending['verification']['passed'])
        self.assertEqual(pending['verification']['pending_attestations'], ['manual'])
        self.assertIn('Checklist incomplete', pending['next_action'])
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

    def test_malformed_decisions_do_not_hide_healthy_runs(self):
        self.create_ready()
        self.cli('new', 'healthy')
        self.approve('3')
        receipt = sorted((self.root / 'runs/example/decisions').glob('*.json'))[0]
        original = json.loads(receipt.read_text())
        malformed = [*({field: value} for field in ('reviewer', 'source', 'reason')
                       for value in (None, False, 17, [], {})),
                     *({'decision': value} for value in (None, True, [], 'pending', 'stale', 'new-state'))]
        for fields in malformed:
            with self.subTest(decision=fields):
                receipt.write_text(json.dumps({**original, **fields}))
                self.assert_invalid_run_is_isolated()
        receipt.write_text(json.dumps({**original, 'future-field': {'extra': True}}))
        projected = self.view()['decisions'][0]
        self.assertEqual(projected['decision'], 'approved')
        self.assertEqual(projected['reviewer'], original['reviewer'])

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


class SeedBindingStatusTests(StageBase):
    machine = ProtocolTests.machine

    def bound_run(self):
        self.new_run('example', profile='full', approve=False)
        seed = confirmed(self.base / 'TEST-sessions')
        self.stage('example', 1, '--seed-contract', str(seed), '--tinkery', str(TINKERY))
        metadata = json.loads((self.root / 'runs/example/run.json').read_text())
        return {key: metadata['seed_contract'][key] for key in ('path', 'sha256', 'source')}

    def test_seed_binding_in_run_view(self):
        expected = self.bound_run()
        self.new_run('unbound', approve=False)
        self.assertEqual(self.machine('status', 'example')['run']['seed_contract'], expected)
        self.assertIsNone(self.machine('status', 'unbound')['run']['seed_contract'])
        metadata = self.root / 'runs/unbound/run.json'
        metadata.write_text(json.dumps({**json.loads(metadata.read_text()), 'seed_contract': None}))
        self.assertIsNone(self.machine('status', 'unbound')['run']['seed_contract'])

    def test_seed_binding_in_run_list(self):
        expected = self.bound_run()
        self.new_run('unbound', approve=False)
        listed = {run['slug']: run for run in self.machine('status')['runs']}
        self.assertEqual(listed['example']['seed_contract'], expected)
        self.assertIsNone(listed['unbound']['seed_contract'])
        metadata = self.root / 'runs/unbound/run.json'
        metadata.write_text(json.dumps({**json.loads(metadata.read_text()), 'seed_contract': None}))
        listed = {run['slug']: run for run in self.machine('status')['runs']}
        self.assertIsNone(listed['unbound']['seed_contract'])

    def tree(self):
        return {str(p.relative_to(self.root)): p.read_bytes() if p.is_file() else None
                for p in self.root.rglob('*')}

    def test_seed_status_is_read_only_and_human_matches_cli_status(self):
        expected = self.bound_run()
        self.new_run('unbound', approve=False)
        # Reads report intake metadata, not a newly hashed or adopted seed.
        (self.root / expected['path']).write_text('TEST changed run-local copy')
        source = self.base / 'TEST-sessions'
        for seed in source.rglob('seed-contract.md'):
            seed.unlink()
        before = self.tree()
        listed = {run['slug']: run for run in self.machine('status')['runs']}
        self.assertEqual(self.tree(), before)
        for slug, binding in [('example', expected), ('unbound', None)]:
            with self.subTest(slug=slug):
                view = self.machine('status', slug)['run']
                self.assertEqual(self.tree(), before)
                human = self.sdlc('status', slug)
                self.assertEqual(human.returncode, 0, human.stderr)
                self.assertEqual(self.tree(), before)
                self.assertEqual(human.stdout, view['cli_status'] + '\n')
                self.assertEqual(view['seed_contract'], binding)
                self.assertEqual(listed[slug]['seed_contract'], binding)
                lines = [line for line in human.stdout.splitlines() if line.startswith('Seed contract:')]
                self.assertEqual(lines, [f"Seed contract: {expected['path']} (sha256: {expected['sha256']}; source: {expected['source']})"]
                                 if binding else [])

    def test_malformed_seed_bindings_are_invalid_without_repair_or_hidden_healthy_runs(self):
        self.bound_run()
        self.new_run('healthy', approve=False)
        metadata = self.root / 'runs/example/run.json'
        original = json.loads(metadata.read_text())
        binding = original['seed_contract']
        malformed = [False, True, 17, '', 'seed', [], {},
                     *({k: v for k, v in binding.items() if k != field}
                       for field in ('path', 'sha256', 'source', 'definitions_sha256')),
                     *({**binding, field: value} for field in ('path', 'sha256', 'source')
                       for value in (None, False, 17, [], {}, '', ' ')),
                     *({**binding, 'sha256': value} for value in ('bad-digest', 'a' * 63, 'G' * 64, 'A' * 64))]
        for value in malformed:
            with self.subTest(binding=value):
                metadata.write_text(json.dumps({**original, 'seed_contract': value}))
                before = self.tree()
                detail = self.machine('status', 'example', ok=False)
                self.assertEqual(detail['error']['code'], 'invalid-state')
                self.assertIn('Seed binding', detail['error']['message'])
                listed = {run['slug']: run for run in self.machine('status')['runs']}
                self.assertEqual(listed['example']['verification_status'], 'invalid')
                self.assertIn('Seed binding', listed['example']['error'])
                self.assertEqual(listed['healthy']['verification_status'], 'blocked')
                self.assertIsNone(listed['healthy']['seed_contract'])
                human = self.sdlc('status', 'example')
                self.assertNotEqual(human.returncode, 0)
                self.assertIn('Seed binding', human.stderr)
                self.assertEqual(self.tree(), before)


class DeliveryRecordTests(unittest.TestCase):
    """#146: a run records its one PR and how it ended, as typed records clients can read."""
    setUp = fixtures.WorkflowTests.setUp
    git = fixtures.WorkflowTests.git
    config = fixtures.WorkflowTests.config
    cli = fixtures.WorkflowTests.cli
    approve = fixtures.WorkflowTests.approve
    define_checklist = fixtures.WorkflowTests.define_checklist
    create_ready = fixtures.WorkflowTests.create_ready
    machine = ProtocolTests.machine
    view = ProtocolTests.view

    URL = 'https://github.com/TEST/example/pull/7'
    MERGE = 'a' * 40

    def label_none(self):
        self.cli('label', 'example', '--kind', 'none', '--words', 'No corrections', '--reviewer', 'human', '--source', 'review:1')

    def verified(self):
        self.create_ready()
        self.cli('verify', 'example')
        self.assertEqual(self.view()['verification_status'], 'current')

    def test_new_runs_have_no_pr_or_closure(self):
        self.create_ready()
        view = self.view()
        self.assertIsNone(view['pr'])
        self.assertIsNone(view['closure'])
        summary = self.machine('status')['runs'][0]
        self.assertIsNone(summary['pr'])
        self.assertIsNone(summary['closure'])

    def test_pr_needs_current_verification(self):
        self.create_ready()
        self.assertIn('verify example first', self.cli('pr', 'example', self.URL, ok=False))
        self.assertFalse((self.root / 'runs/example/pr.json').exists())

    def test_pr_records_url_and_verified_candidate_and_names_the_next_record(self):
        self.verified()
        self.assertIn('Once the PR is open, record it: python3 _system/scripts/sdlc.py pr example', self.view()['next_action'])
        out = self.cli('pr', 'example', self.URL)
        run_id = json.loads((self.root / 'runs/example/run.json').read_text())['run_id']
        self.assertIn(f'Tink-Run: {run_id}', out)
        view = self.view()
        self.assertEqual(view['pr']['url'], self.URL)
        self.assertEqual(view['pr']['candidate'], view['verification']['candidate']['head'])
        self.assertIn('sdlc.py close example --outcome merged', view['next_action'])
        self.assertIn(f'PR: {self.URL}', view['cli_status'])
        self.assertEqual(self.machine('status')['runs'][0]['pr']['url'], self.URL)

    def test_a_run_has_one_pr(self):
        self.verified()
        self.cli('pr', 'example', self.URL)
        before = (self.root / 'runs/example/pr.json').read_bytes()
        self.assertIn('already recorded', self.cli('pr', 'example', self.URL))
        self.assertIn('a run has one PR', self.cli('pr', 'example', 'https://github.com/TEST/example/pull/8', ok=False))
        self.assertEqual((self.root / 'runs/example/pr.json').read_bytes(), before)

    def test_pr_must_be_a_full_github_pull_url(self):
        self.verified()
        for url in ('github.com/TEST/example/pull/7', 'https://github.com/TEST/example/issues/7',
                    'https://example.com/TEST/example/pull/7', 'https://github.com/TEST/example/pull/7/files'):
            with self.subTest(url=url):
                self.assertIn('full GitHub URL', self.cli('pr', 'example', url, ok=False))

    def test_merged_needs_the_recorded_pr_and_full_merge_commit(self):
        self.verified()
        self.label_none()
        self.assertIn('Record the PR first', self.cli('close', 'example', '--outcome', 'merged', '--merge', self.MERGE, ok=False))
        self.cli('pr', 'example', self.URL)
        self.assertIn('40-character merge commit', self.cli('close', 'example', '--outcome', 'merged', '--merge', 'abc1234', ok=False))
        self.assertIn('40-character merge commit', self.cli('close', 'example', '--outcome', 'merged', ok=False))
        self.assertFalse((self.root / 'runs/example/closure.json').exists())
        self.cli('close', 'example', '--outcome', 'merged', '--merge', self.MERGE)
        view = self.view()
        self.assertEqual(view['closure']['outcome'], 'merged')
        self.assertEqual(view['closure']['merge'], self.MERGE)
        self.assertEqual(view['next_action'], f'Closed: merged as {self.MERGE}.')
        self.assertEqual(self.machine('status')['runs'][0]['closure']['outcome'], 'merged')

    def test_abandoned_needs_no_pr_and_takes_no_merge(self):
        self.create_ready()
        self.label_none()
        self.assertIn('no merge commit', self.cli('close', 'example', '--outcome', 'abandoned', '--merge', self.MERGE, ok=False))
        self.cli('close', 'example', '--outcome', 'abandoned')
        self.assertEqual(self.view()['next_action'], 'Closed: abandoned.')

    def test_a_run_closes_once_and_its_pr_is_then_fixed(self):
        self.create_ready()
        self.label_none()
        self.cli('close', 'example', '--outcome', 'abandoned')
        before = (self.root / 'runs/example/closure.json').read_bytes()
        self.assertIn('already closed as abandoned', self.cli('close', 'example', '--outcome', 'abandoned', ok=False))
        self.assertIn('This run is closed', self.cli('pr', 'example', self.URL, ok=False))
        self.assertEqual((self.root / 'runs/example/closure.json').read_bytes(), before)

    def test_malformed_records_invalidate_the_run(self):
        self.create_ready()
        for name, value in (('pr.json', {'url': 'not a url', 'candidate': 'x'}),
                            ('closure.json', {'outcome': 'merged'})):
            with self.subTest(name=name):
                record = self.root / 'runs/example' / name
                record.write_text(json.dumps(value))
                summary = self.machine('status')['runs'][0]
                self.assertEqual(summary['verification_status'], 'invalid')
                self.assertIn(name, summary['error'])
                record.unlink()


class LabelTests(unittest.TestCase):
    """#147: the operator's judgments of a run, append-only and theirs alone."""
    setUp = fixtures.WorkflowTests.setUp
    git = fixtures.WorkflowTests.git
    config = fixtures.WorkflowTests.config
    cli = fixtures.WorkflowTests.cli
    approve = fixtures.WorkflowTests.approve
    define_checklist = fixtures.WorkflowTests.define_checklist
    create_ready = fixtures.WorkflowTests.create_ready
    machine = ProtocolTests.machine
    view = ProtocolTests.view

    WORDS = "Shape's check command can't import tomli as cloned — Ω"

    def label_command(self, *extra, kind='correction', words=WORDS, reviewer='jondev'):
        return [sys.executable, str(self.root / '_system/scripts/sdlc.py'), 'label', 'example', '--kind', kind,
                '--words', words, '--reviewer', reviewer, '--source', 'retro 2026-11-03', *extra]

    def label(self, *extra, kind='correction', words=WORDS, reviewer='jondev', ok=True, env=None):
        argv = self.label_command(*extra, kind=kind, words=words, reviewer=reviewer)
        result = subprocess.run(argv, cwd='/', capture_output=True, text=True, env={**os.environ, **(env or {})})
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def lines(self):
        return [json.loads(line) for line in (self.root / 'runs/example/labels.jsonl').read_text().splitlines()]

    def wait_for_run_name_lock(self, process):
        deadline = time.monotonic() + 3
        with (self.root / 'runs/.example.lock').open('a+b') as lock:
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                time.sleep(.01)
        self.fail('The label command never acquired the run-name lock.')

    def test_label_expected_identity_preserves_words_and_signer(self):
        self.cli('new', 'example')
        run_id = self.view()['run_id']
        words = self.WORDS + '\nKeep this second line. '
        self.label('--expected-run-id', run_id, kind='accepted', words=words)
        record, = self.lines()
        self.assertEqual((record['kind'], record['words'], record['reviewer'], record['source']),
                         ('accepted', words, 'jondev', 'retro 2026-11-03'))

    def test_label_refuses_a_replacement_with_the_same_name(self):
        self.cli('new', 'example')
        run_id = self.view()['run_id']
        original = self.root / 'runs/example'
        original.rename(self.root / 'runs/previous')
        self.cli('new', 'example')
        self.assertNotEqual(self.view()['run_id'], run_id)
        output = self.label('--expected-run-id', run_id, ok=False)
        self.assertIn('Run identity changed; reread the run before saving a label.', output)
        self.assertFalse((original / 'labels.jsonl').exists())

    def test_label_rechecks_identity_after_waiting_for_the_writer(self):
        self.cli('new', 'example')
        run_id = self.view()['run_id']
        self.cli('new', 'replacement')
        original = self.root / 'runs/example'
        with (original / '.writer-lock').open('a+b') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            process = subprocess.Popen(self.label_command('--expected-run-id', run_id),
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.wait_for_run_name_lock(process)
                original.rename(self.root / 'runs/previous')
                (self.root / 'runs/replacement').rename(original)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                stdout, stderr = process.communicate(timeout=10)
        self.assertNotEqual(process.returncode, 0, stdout + stderr)
        self.assertIn('Run identity changed; reread the run before saving a label.', stderr)
        self.assertFalse((original / 'labels.jsonl').exists())
        self.assertFalse((self.root / 'runs/previous/labels.jsonl').exists())

    def test_label_holds_the_creation_lock_until_its_write_finishes(self):
        self.cli('new', 'example')
        run_id = self.view()['run_id']
        with (self.root / 'runs/example/.writer-lock').open('a+b') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            process = subprocess.Popen(self.label_command('--expected-run-id', run_id),
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.wait_for_run_name_lock(process)
                with subprocess.Popen([sys.executable, str(self.root / '_system/scripts/sdlc.py'),
                                       'new', 'example'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True) as creation:
                    with self.assertRaises(subprocess.TimeoutExpired):
                        creation.communicate(timeout=.2)
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                    created_out, created_err = creation.communicate(timeout=10)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertEqual(len(self.lines()), 1)
        self.assertNotEqual(creation.returncode, 0, created_out + created_err)
        self.assertIn('Run already exists; nothing overwritten.', created_err)

    def test_labels_keep_words_verbatim_and_count_by_kind(self):
        self.create_ready()
        self.assertEqual(self.view()['labels'], {'count': 0, 'kinds': {'correction': 0, 'none': 0, 'accepted': 0, 'rejected': 0}, 'outside': 0})
        self.label('--about', 'runs/example/checklist.json')
        self.label('--about', 'outside')
        self.label(kind='accepted', words='Result serves the goal')
        first = self.lines()[0]
        self.assertEqual(first['words'], self.WORDS)
        self.assertEqual(first['about'], 'runs/example/checklist.json')
        self.assertEqual(first['about_sha256'], hashlib.sha256((self.root / 'runs/example/checklist.json').read_bytes()).hexdigest())
        self.assertEqual(first['consent'], 'local')
        view = self.view()
        self.assertEqual(view['labels']['count'], 3)
        self.assertEqual(view['labels']['kinds']['correction'], 2)
        self.assertEqual(view['labels']['outside'], 1)
        self.assertIn('Labels: 2 correction, 1 accepted; 1 outside the run', view['cli_status'])
        self.assertEqual(self.machine('status')['runs'][0]['labels']['count'], 3)

    def test_labels_are_human_only(self):
        self.create_ready()
        self.assertIn('human-only', self.label(reviewer='Agent Pi', ok=False))
        self.assertIn('human-only', self.label('--actor', '{"by":"agent"}', ok=False))
        self.assertFalse((self.root / 'runs/example/labels.jsonl').exists())

    def test_a_build_step_session_cannot_label(self):
        self.create_ready()
        self.assertIn('build step session', self.label(ok=False, env={'TINK_SDLC_STEP_SESSION': '1'}))
        self.assertFalse((self.root / 'runs/example/labels.jsonl').exists())

    def test_about_must_be_a_checkout_file_or_outside(self):
        self.create_ready()
        beside = Path(tempfile.mkdtemp(prefix='tink-sdlc-TEST-beside-'))
        self.addCleanup(shutil.rmtree, beside)
        (beside / 'TEST-beside.md').write_text('outside the checkout')
        for about in ('runs/example/missing.md', os.path.relpath(beside / 'TEST-beside.md', self.root), str(beside / 'TEST-beside.md')):
            with self.subTest(about=about):
                self.assertIn('--about must name a file', self.label('--about', about, ok=False))

    def test_an_edited_or_removed_earlier_label_invalidates_the_run(self):
        self.create_ready()
        self.label()
        self.label(kind='none', words='Nothing else')
        file = self.root / 'runs/example/labels.jsonl'
        original = file.read_text()
        first, second = original.splitlines(keepends=True)
        for changed in (original.replace(self.WORDS, 'Softened words'), second, first.replace('correction', 'accepted') + second):
            with self.subTest(changed=changed[:40]):
                file.write_text(changed)
                summary = self.machine('status')['runs'][0]
                self.assertEqual(summary['verification_status'], 'invalid')
                self.assertIn('labels.jsonl line', summary['error'])
                self.assertIn('append-only', self.label(ok=False))
        file.write_text(original)
        self.assertEqual(self.view()['labels']['count'], 2)

    def test_an_agent_attributed_label_line_invalidates_the_run(self):
        self.create_ready()
        file = self.root / 'runs/example/labels.jsonl'
        line = {'kind': 'none', 'words': 'No corrections', 'reviewer': 'Agent Pi', 'source': 'TEST', 'consent': 'local',
                'time_ns': 1, 'prev': hashlib.sha256(b'').hexdigest()}
        file.write_text(json.dumps(line) + '\n')
        self.assertIn('not an operator label', self.machine('status')['runs'][0]['error'])
        self.assertIn('not an operator label', self.cli('close', 'example', '--outcome', 'abandoned', ok=False))

    def test_a_closed_run_keeps_its_labels(self):
        self.create_ready()
        self.label(kind='none', words='No corrections')
        self.cli('close', 'example', '--outcome', 'abandoned')
        self.assertIn('closed; its labels can no longer change', self.label(kind='rejected', ok=False))
        (self.root / 'runs/example/labels.jsonl').unlink()
        summary = self.machine('status')['runs'][0]
        self.assertEqual(summary['verification_status'], 'invalid')
        self.assertIn('closed but has no labels', summary['error'])

    def test_delivery_records_refuse_the_main_checkout_while_the_run_builds_in_a_worktree(self):
        self.create_ready()
        worktree = Path(tempfile.mkdtemp(prefix='tink-sdlc-TEST-build-')) / 'checkout'
        self.addCleanup(shutil.rmtree, worktree.parent)
        self.git('worktree', 'add', '-q', '-b', 'example', str(worktree), 'HEAD')
        self.assertIn(f'Run example builds in {worktree.resolve()}; record it there', self.label(ok=False))
        self.assertIn('builds in', self.cli('close', 'example', '--outcome', 'abandoned', ok=False))
        self.assertIn('builds in', self.cli('pr', 'example', 'https://github.com/TEST/example/pull/1', ok=False))
        self.assertFalse((self.root / 'runs/example/labels.jsonl').exists())

    def build_in_worktree(self):
        """The run committed in the main checkout, then built in a linked worktree, as stage 3 does."""
        self.create_ready()
        self.git('add', '-A')
        self.git('-c', 'user.name=TEST', '-c', 'user.email=test@local', 'commit', '-qm', 'TEST run')
        worktree = Path(tempfile.mkdtemp(prefix='tink-sdlc-TEST-build-')) / 'checkout'
        self.addCleanup(shutil.rmtree, worktree.parent, True)
        self.git('worktree', 'add', '-q', '-b', 'example', str(worktree), 'HEAD')
        return worktree

    def there(self, worktree, *args, ok=True):
        result = subprocess.run([sys.executable, str(worktree / '_system/scripts/sdlc.py'), *args],
                                cwd='/', capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_closing_in_a_worktree_keeps_the_records_after_it_is_removed(self):
        # #170: vault-links run 2 read "Verification: not run" with no labels once its worktree was removed.
        worktree = self.build_in_worktree()
        self.there(worktree, 'verify', 'example')
        self.there(worktree, 'label', 'example', '--kind', 'none', '--words', 'No corrections',
                   '--reviewer', 'jondev', '--source', 'retro')
        self.there(worktree, 'pr', 'example', 'https://github.com/TEST/example/pull/7')
        merge = 'b' * 40
        self.assertIn('Copied its records to', self.there(worktree, 'close', 'example', '--outcome', 'merged', '--merge', merge))
        self.git('worktree', 'remove', '--force', str(worktree))
        summary = self.machine('status', 'example')['run']
        self.assertEqual(summary['closure']['outcome'], 'merged')
        self.assertEqual(summary['labels']['count'], 1)
        self.assertEqual(summary['verification']['result'], 'passed')
        self.assertEqual(summary['next_action'], f'Closed: merged as {merge}.')
        text = self.cli('status', 'example')
        self.assertIn('Verification: last passed on', text)
        self.assertIn('Labels: 1 none', text)
        self.assertIn(f'Closed: merged as {merge}', text)

    def test_closing_in_a_worktree_refuses_to_overwrite_uncommitted_main_records(self):
        worktree = self.build_in_worktree()
        self.there(worktree, 'label', 'example', '--kind', 'none', '--words', 'No corrections',
                   '--reviewer', 'jondev', '--source', 'retro')
        notes = self.root / 'runs/example/brief.md'
        notes.write_text('operator edit, uncommitted')
        self.assertIn('uncommitted changes', self.there(worktree, 'close', 'example', '--outcome', 'abandoned', ok=False))
        self.assertFalse((worktree / 'runs/example/closure.json').exists())
        self.assertEqual(notes.read_text(), 'operator edit, uncommitted')
        self.git('checkout', '--', 'runs/example/brief.md')
        self.there(worktree, 'close', 'example', '--outcome', 'abandoned')
        self.assertTrue((self.root / 'runs/example/closure.json').is_file())

    def test_closing_in_a_worktree_of_a_bare_repository_copies_nothing(self):
        # A bare repository has no main working tree to keep records in; close works as before.
        self.create_ready()
        self.git('add', '-A')
        self.git('-c', 'user.name=TEST', '-c', 'user.email=test@local', 'commit', '-qm', 'TEST run')
        parent = Path(tempfile.mkdtemp(prefix='tink-sdlc-TEST-bare-'))
        self.addCleanup(shutil.rmtree, parent, True)
        subprocess.run(['git', 'clone', '-q', '--bare', str(self.root), str(parent / 'repo.git')], check=True)
        worktree = parent / 'checkout'
        subprocess.run(['git', '-C', str(parent / 'repo.git'), 'worktree', 'add', '-q', '-b', 'example', str(worktree), 'HEAD'],
                       check=True, capture_output=True)
        self.there(worktree, 'label', 'example', '--kind', 'none', '--words', 'No corrections',
                   '--reviewer', 'jondev', '--source', 'retro')
        out = self.there(worktree, 'close', 'example', '--outcome', 'abandoned')
        self.assertNotIn('Copied', out)
        self.assertTrue((worktree / 'runs/example/closure.json').is_file())
        self.assertFalse((parent / 'repo.git/runs').exists())

    def test_close_needs_a_label(self):
        self.create_ready()
        self.assertIn('Label the run before closing it', self.cli('close', 'example', '--outcome', 'abandoned', ok=False))
        self.label(kind='none', words='No corrections')
        self.cli('close', 'example', '--outcome', 'abandoned')


if __name__ == '__main__':
    unittest.main()
