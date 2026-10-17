"""Independent-review regressions for PR #72; execution stays in synthetic TEST roots."""
import json
import subprocess

from test_sdlc_stage import StageBase, PASSING
import test_sdlc_seed_checks as seed_fixtures
from test_sdlc_workflow import workflow


class SeedParserReview(StageBase):
    definitions = seed_fixtures.SeedChecks.definitions
    approve_all = seed_fixtures.SeedChecks.approve_all
    attest = seed_fixtures.SeedChecks.attest

    def intake(self, text):
        self.new_run('r', approve=False)
        seed = self.base / 'TEST-seed-contract.md'
        seed.write_text(text)
        self.stage('r', 1, '--seed-contract', str(seed))
        self.path = self.root / 'runs/r'
        self.set_checks([PASSING])

    def test_nested_heading_after_passing_check_is_refused_without_mutation(self):
        self.new_run('r', approve=False)
        seed = self.base / 'TEST-nested.md'
        seed.write_text('## Acceptance criteria\nFirst check\ncmd: true\nexpect: success\ncwd: .\n'
                        '### Another required check\nMust fail\ncmd: false\nexpect: success\ncwd: .\n')
        self.refuses('r', 1, '--seed-contract', str(seed), message='nested heading')

    def test_nested_heading_first_cannot_downgrade_to_legacy(self):
        self.new_run('r', approve=False)
        seed = self.base / 'TEST-nested-first.md'
        seed.write_text('## Acceptance criteria\n### Required check\nMust fail\n'
                        'cmd: false\nexpect: success\ncwd: .\n')
        self.refuses('r', 1, '--seed-contract', str(seed), message='nested heading')

    def test_fenced_examples_are_ignored_and_real_failure_executes(self):
        # Both Markdown fence styles; a shorter apparent close must stay inside a longer fence.
        for fence in ['```', '~~~', '````']:
            with self.subTest(fence=fence):
                text = ('# TEST seed\n## Problem statement\n' + fence + 'markdown\n'
                        '## Acceptance criteria\nExample only\ncmd: true\nexpect: example\ncwd: .\n## End example\n'
                        + ('```\n' if fence == '````' else '') + fence + '\n'
                        '## Acceptance criteria\nReal required check\ncmd: false\nexpect: success\ncwd: .\n'
                        '## Constraints\nNo extra work.\n')
                value = workflow.seed_definitions(text.encode())
                self.assertEqual([i['cmd'] for i in value['items']], ['false'])
        # Real stage intake / approval / attestation / execution, not just parser output.
        self.intake(text)
        self.approve_all()
        self.attest('seed-1-expect')
        result = self.sdlc('verify', 'r')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Seed check failed: seed-1', result.stderr)
        self.assertEqual(self.definitions()['items'][0]['description'], 'Real required check')

    def test_empty_or_ambiguous_sections_are_refused(self):
        criterion = 'Required\ncmd: false\nexpect: success\ncwd: .\n'
        cases = [
            '## Acceptance criteria\n## Boundaries\n',
            '## Acceptance checks\n',
            '## Acceptance criteria\n' + criterion + '## Acceptance checks\n' + criterion,
            '## Acceptance criteria\n' + criterion + '## Elsewhere\n'
            '## Acceptance criteria\n' + criterion,
            '## Acceptance criteria\n```markdown\n' + criterion + '```\n',
            '## Problem statement\n```markdown\n## Acceptance criteria\n' + criterion,
        ]
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    workflow.seed_definitions(text.encode())
        self.new_run('r', approve=False)
        seed = self.base / 'TEST-empty.md'
        seed.write_text(cases[0])
        self.refuses('r', 1, '--seed-contract', str(seed), message='empty')

    def test_fenced_example_only_is_not_a_real_acceptance_section(self):
        self.intake('## Problem statement\n~~~markdown\n## Acceptance criteria\n'
                    'Example only\ncmd: false\nexpect: success\ncwd: .\n## End example\n~~~\n'
                    '## Goal\nTEST legacy seed with no actual acceptance section.\n')
        self.assertFalse((self.path / 'seed-checks.json').exists())
        self.approve_all()
        self.assertEqual(self.sdlc('verify', 'r').returncode, 0)

    def test_bare_and_wrapped_shell_backticks_roundtrip_and_execute(self):
        commands = [
            ('test -d `pwd`', 'test -d `pwd`'),
            ('``test -d `pwd` && true``', 'test -d `pwd` && true'),
            ('`` test -d `pwd` ``', ' test -d `pwd` '),
            ('test -d "$(pwd)"', 'test -d "$(pwd)"'),
            ('`test -d "$(pwd)"`', 'test -d "$(pwd)"'),
        ]
        checks = ''
        for index, (field, command) in enumerate(commands, 1):
            control = subprocess.run(['bash', '-c', command], cwd=self.root, env=self.env, capture_output=True)
            self.assertEqual(control.returncode, 0, control.stderr)
            checks += f'Check {index}\ncmd: {field}\nexpect: exit zero\ncwd: .\n'
        self.intake('## Acceptance criteria\n' + checks)
        self.approve_all()
        for index in range(1, len(commands) + 1):
            self.attest(f'seed-{index}-expect')
        result = self.sdlc('verify', 'r')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item['cmd'] for item in self.definitions()['items']], [c for _, c in commands])

    def test_inline_wrapped_backticks_and_field_like_text_are_preserved(self):
        command = 'test -d `pwd` && printf "%s" "literal | expect: not a field"'
        self.intake('## Acceptance checks\nInline check\n'
                    f'cmd: ``{command}`` | expect: literal output | cwd: .\n')
        self.approve_all()
        self.attest('seed-1-expect')
        result = self.sdlc('verify', 'r')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.definitions()['items'][0]['cmd'], command)

    def test_bare_standalone_command_cannot_inject_provisional_metadata(self):
        command = 'printf "%s" "literal | provisional: not a prerequisite"'
        self.intake('## Acceptance criteria\nRequired command\n'
                    f'cmd: {command}\nexpect: literal output\ncwd: .\n')
        self.assertEqual(self.definitions()['items'][0]['kind'], 'settled')
        self.assertEqual(self.definitions()['items'][0]['cmd'], command)
        self.approve_all()
        self.attest('seed-1-expect')
        self.assertEqual(self.sdlc('verify', 'r').returncode, 0)

    def test_ambiguous_markdown_delimiters_are_refused(self):
        for field in ['`true', '`true` trailing', '``true`', '`test -d `pwd``']:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    workflow.seed_definitions(('## Acceptance criteria\nCheck\n'
                                               f'cmd: {field}\nexpect: success\ncwd: .\n').encode())

    def test_canonical_shape_three_kinds_still_execute_with_attestations(self):
        self.intake('status: confirmed for intake\n## Acceptance criteria\n'
                    'Existing output is correct.\ncmd: ./TEST-check\nexpect: TEST existing output\ncwd: .\n'
                    'New output is correct.\nprovisional: Once TEST-new-check exists.\n'
                    'cmd: ./TEST-new-check\nexpect: TEST new output\ncwd: .\n'
                    'The result feels calm.\nyou judge: Inspect the result for calmness.\n'
                    '## Suggested first slice\ncmd: ./TEST-new-check\ncwd: .\n')
        self.assertEqual([i['kind'] for i in self.definitions()['items']], ['settled', 'provisional', 'attestation'])
        script = self.root / 'TEST-check'
        script.write_text('#!/bin/sh\nprintf "%s\\n" "TEST existing output"\n')
        script.chmod(0o755)
        self.approve_all()
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)
        self.attest('seed-1-expect')
        self.attest('seed-3')
        result = self.sdlc('verify', 'r')
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((self.path / '04-test/output/verification.json').read_text())
        self.assertEqual(record['seed_checks'], {'seed-1': 'proven', 'seed-1-expect': 'attested',
                                                'seed-2': 'pending', 'seed-3': 'attested'})
