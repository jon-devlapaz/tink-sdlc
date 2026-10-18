"""Record-bound intake regressions; no Markdown acceptance parser remains (#73)."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from seed_me_fixture import FRAME, HELPER, cli, confirmed, command, provisional, DEFAULT
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
        self.stage('r', 1, '--seed-contract', str(seed), '--seed-me-helper', str(HELPER))
        self.path = self.root / 'runs/r'

    def test_helper_fixture_is_exact_pinned_upstream_source(self):
        self.assertEqual(hashlib.sha256(HELPER.read_bytes()).hexdigest(),
                         '22e506bf40018666d6d1b5246299dd0ee88bdebffc7c9a92c165b864bc49111b')
        self.assertEqual(hashlib.sha256(FRAME.read_bytes()).hexdigest(),
                         '24c1fdaf3280200562fb927abe2d73458ab561e185be70de30e3699c17e6e87b')

    def test_historical_seeds_are_readable_but_not_new_approval_or_verification(self):
        self.new_run('r', approve=False)
        path = self.root / 'runs/r'
        seed = path / 'seed-contract.md'
        seed.write_text('## Acceptance criteria\nHandwritten old check\n')
        meta = json.loads((path / 'run.json').read_text())
        meta['seed_contract'] = {'path': 'runs/r/seed-contract.md',
                                 'sha256': hashlib.sha256(seed.read_bytes()).hexdigest()}
        (path / 'run.json').write_text(json.dumps(meta))
        self.assertEqual(self.sdlc('status', 'r').returncode, 0)
        result = self.sdlc('decide', 'r', '3', 'approved', '--reviewer', 'h', '--source', 's', '--reason', 'r')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('record intake', result.stderr)
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)

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
        directory = seed.parent
        names = ['seed-checks.json', 'seed-contract.md', 'ledger.json', 'seed-confirmation.json']
        original = {name: (directory / name).read_bytes() for name in names}
        for mutation in ['records', 'seed', 'decisions', 'v1', 'missing', 'rendered']:
            with self.subTest(mutation=mutation):
                for name, data in original.items():
                    (directory / name).write_bytes(data)
                if mutation == 'records':
                    (directory / 'seed-checks.json').write_text(json.dumps([command('false')]))
                elif mutation == 'seed':
                    seed.write_bytes(original['seed-contract.md'] + b'changed\n')
                elif mutation == 'decisions':
                    ledger = json.loads(original['ledger.json'])
                    ledger['goal'] = 'changed goal'
                    (directory / 'ledger.json').write_text(json.dumps(ledger))
                elif mutation == 'v1':
                    record = json.loads(original['seed-confirmation.json'])
                    record['schema_version'] = 1
                    (directory / 'seed-confirmation.json').write_text(json.dumps(record))
                elif mutation == 'missing':
                    (directory / 'seed-checks.json').unlink()
                else:
                    # Even rebinding the seed hash cannot bless handwritten rendered bytes.
                    seed.write_bytes(original['seed-contract.md'].replace(b'```json', b'```JSON'))
                    record = json.loads(original['seed-confirmation.json'])
                    record['seed_sha256'] = hashlib.sha256(seed.read_bytes()).hexdigest()
                    (directory / 'seed-confirmation.json').write_text(json.dumps(record))
                self.refuses('r', 1, '--seed-contract', str(seed), '--seed-me-helper', str(HELPER), message='Seed Me')

    def test_record_formatting_is_not_content_and_source_changes_do_not_change_lock(self):
        seed = self.prepare([command()])
        checks = seed.parent / 'seed-checks.json'
        checks.write_text(json.dumps(json.loads(checks.read_text()), sort_keys=True, separators=(',', ':')))
        self.intake(seed)
        self.approve_all()
        cli('seed', 'checks', 'set', seed.parent, seed.parent / 'TEST-input.json')
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
        for variant in ('emphasized-heading', 'comment-only'):
            with self.subTest(variant=variant):
                self.setUp()
                seed = self.prepare([command()])
                self.intake(seed)  # real CLI positive control: the sole true record is imported
                original = seed.read_text()
                rendered = cli('seed', 'checks', 'render', seed.parent)
                confirmation_path = seed.parent / 'seed-confirmation.json'
                confirmation = json.loads(confirmation_path.read_text())

                def transform(body):
                    replacement = (rendered + '## **Acceptance checks**\nTEST additional failing requirement\n'
                                   'cmd: false\nexpect: exit zero\ncwd: .\n\n' if variant == 'emphasized-heading'
                                   else '<!--\n' + rendered + '## End example\n-->\n\n')
                    return body.replace(rendered, replacement, 1)

                # New confirmation must refuse both reviewer repros without writing.
                draft = '\n'.join(line for line in original.split('\n') if not line.startswith('Confirmed by:'))
                seed.write_text(transform(draft.replace('status: confirmed for intake', 'status: draft', 1)))
                before = {p.name: p.read_bytes() for p in seed.parent.iterdir() if p.is_file()}
                refused = subprocess.run([sys.executable, str(HELPER), 'seed', 'confirm', str(seed.parent),
                                          '--revision', 'r1', '--source', 'TEST reviewer repro'],
                                         capture_output=True, text=True)
                self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
                self.assertIn('checks', refused.stderr)
                self.assertEqual(before, {p.name: p.read_bytes() for p in seed.parent.iterdir() if p.is_file()})

                # Model a receipt admitted by the old helper: all hashes still match.
                seed.write_text(transform(original))
                confirmation['seed_sha256'] = hashlib.sha256(seed.read_bytes()).hexdigest()
                confirmation_path.write_text(json.dumps(confirmation))
                before = {p.name: p.read_bytes() for p in seed.parent.iterdir() if p.is_file()}
                self.assertFalse(json.loads(cli('status', seed.parent))['handoff']['verified'])
                self.refuses('r', 1, '--here', '--seed-contract', str(seed), '--seed-me-helper', str(HELPER),
                             message='Seed Me handoff is not verified')
                self.assertEqual(before, {p.name: p.read_bytes() for p in seed.parent.iterdir() if p.is_file()})
                self.assertEqual((self.path / 'seed-contract.md').read_text(), original)
                self.assertEqual(self.definitions()['items'][0]['cmd'], 'true')

    def test_no_helper_is_an_explicit_error(self):
        seed = self.prepare()
        self.refuses('r', 1, '--seed-contract', str(seed), message='--seed-me-helper')
