"""Record-bound intake regressions; no Markdown acceptance parser remains (#73)."""
import hashlib
import json
from pathlib import Path

from tinkery_fixture import FIXTURES, TINKERY, changed, cli, confirmed, corpus, command, provisional, DEFAULT
from test_sdlc_stage import StageBase, PASSING
import test_sdlc_seed_checks as seed_fixtures


class RecordIntake(StageBase):
    definitions = seed_fixtures.SeedChecks.definitions
    approve_all = seed_fixtures.SeedChecks.approve_all
    attest = seed_fixtures.SeedChecks.attest

    def prepare(self, records=None):
        self.new_run('r', approve=False)
        self.set_checks([PASSING])
        return confirmed(self.base / 'TEST-sessions', records)

    def intake(self, seed):
        self.stage('r', 1, '--seed-contract', str(seed), '--tinkery', str(TINKERY))
        self.path = self.root / 'runs/r'

    def test_fixture_is_pinned_to_recorded_rust_confirmation(self):
        self.assertEqual(hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),
                         '74d1267a94d3ab8006ad3badaf5735a2522aadf566fb1a8afb46726ca4a9db00')
        data = corpus()
        self.assertEqual(data['provenance']['commit'], '785a6937f2d946fc78afebcdb6e4f811430056a1')
        for case in data['cases']:
            self.assertTrue(json.loads(case['status']['stdout'])['handoff']['verified'])
            self.assertNotIn('viewer', json.loads(case['status']['stdout']))

    def test_every_kind_locks_source_and_optional_fields(self):
        records = [command(), provisional(), provisional('false'), {**DEFAULT[0], 'how_to_judge': 'Inspect'}]
        self.intake(self.prepare(records))
        items = self.definitions()['items']
        self.assertEqual([i['kind'] for i in items], ['cmd', 'provisional', 'provisional', 'judge'])
        for record, item in zip(records, items):
            self.assertEqual({k: item[k] for k in record}, record)
            self.assertTrue(item['locked'])
        self.approve_all()
        self.attest('seed-1-expect')
        self.attest('seed-4')
        self.assertEqual(self.sdlc('verify', 'r').returncode, 0)

    def test_stacker_fixture_is_unchanged_and_records_replace_ambiguous_markdown(self):
        # Byte-identical copy from the real TEST session supplied in #73; never edit the original.
        fixture = Path(__file__).parent / 'fixtures/stacker-r1-seed-contract.md'
        original = fixture.read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(),
                         '206e8b3b18c6baf499119b558034e6befdeee472247f871c3bed7fc6b68d7909')
        self.assertIn(b'Provisional:', original)
        records = json.loads((fixture.parent / 'stacker-records.json').read_text())
        self.new_run('r', approve=False)
        self.set_checks([PASSING])
        seed = confirmed(self.base / 'TEST-stacker-sessions', records, template=original)
        self.intake(seed)
        self.assertEqual(seed.read_text().count('## Acceptance criteria\n'), 1)
        self.assertNotIn('## Acceptance checks', seed.read_text())
        self.assertEqual((self.path / 'seed-contract.md').read_bytes(), seed.read_bytes())
        self.assertIn('## Suggested first slice', seed.read_text())
        self.assertEqual([i['kind'] for i in self.definitions()['items']], ['judge', 'provisional', 'provisional'])
        self.approve_all()
        self.attest('seed-1')
        self.assertEqual(self.sdlc('verify', 'r').returncode, 0)
        self.assertEqual(fixture.read_bytes(), original)

    def test_stale_or_legacy_handoff_refuses_without_mutation(self):
        seed = self.prepare([command()])
        original = {p.name: p.read_bytes() for p in seed.parent.iterdir() if p.is_file()}
        for mutation in ('records', 'seed', 'decisions', 'v1', 'missing', 'missing-confirmation',
                         'simulated', 'rendered'):
            with self.subTest(mutation=mutation):
                changed(seed, mutation)
                status = json.loads(cli('seed', 'status', '--json', seed.parent))
                self.assertIs(status['handoff']['verified'], False)
                session_before = {p.name: p.read_bytes() for p in seed.parent.iterdir() if p.is_file()}
                self.refuses('r', 1, '--seed-contract', str(seed), '--tinkery', str(TINKERY),
                             message='Tinkery handoff is not verified')
                self.assertEqual(session_before, {p.name: p.read_bytes() for p in seed.parent.iterdir() if p.is_file()})
                for name, data in original.items():
                    (seed.parent / name).write_bytes(data)

    def test_record_formatting_is_not_content_and_source_changes_do_not_change_lock(self):
        seed = self.prepare([command()])
        checks = seed.parent / 'seed-checks.json'
        checks.write_text(json.dumps(json.loads(checks.read_text()), sort_keys=True, separators=(',', ':')))
        self.intake(seed)
        self.approve_all()
        checks.write_text(json.dumps([command('false')]))
        self.assertIn('Stage 3: approved', self.sdlc('status', 'r').stdout)

    def test_activation_cwd_must_remain_checkout_relative(self):
        self.intake(self.prepare([provisional()]))
        for cwd in ['/tmp/TEST', '../outside', 'a/../b', 'C:/TEST', 'a\\b', 'a\nb']:
            with self.subTest(cwd=cwd):
                value = self.definitions()
                value['items'][0]['activation'] = {'cmd': 'true', 'cwd': cwd, 'evidence': 'TEST prerequisite'}
                (self.path / 'seed-checks.json').write_text(json.dumps(value))
                result = self.sdlc('decide', 'r', '3', 'approved', '--reviewer', 'h', '--source', 's', '--reason', 'r')
                self.assertNotEqual(result.returncode, 0)

    def test_review_f1_hidden_acceptance_repros_refuse_real_intake(self):
        seed = self.prepare([command()])
        self.intake(seed)
        original = seed.read_text()
        for variant in ('emphasized-heading', 'comment-only'):
            with self.subTest(variant=variant):
                # Recorded from Rust status with a matching seed hash: hidden checks still refuse.
                changed(seed, variant)
                self.assertEqual(json.loads((seed.parent / 'seed-confirmation.json').read_text())['seed_sha256'],
                                 hashlib.sha256(seed.read_bytes()).hexdigest())
                before = {p.name: p.read_bytes() for p in seed.parent.iterdir() if p.is_file()}
                self.assertFalse(json.loads(cli('seed', 'status', '--json', seed.parent))['handoff']['verified'])
                self.refuses('r', 1, '--here', '--seed-contract', str(seed), '--tinkery', str(TINKERY),
                             message='Tinkery handoff is not verified')
                self.assertEqual(before, {p.name: p.read_bytes() for p in seed.parent.iterdir() if p.is_file()})
                self.assertEqual((self.path / 'seed-contract.md').read_text(), original)
                self.assertEqual(self.definitions()['items'][0]['cmd'], 'true')

    def test_no_tinkery_is_an_explicit_error(self):
        seed = self.prepare()
        self.refuses('r', 1, '--seed-contract', str(seed), message='--tinkery')
