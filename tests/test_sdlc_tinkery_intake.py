"""The executable boundary fails closed before any run, worktree or skill writes."""
import copy
import json
import subprocess
import sys
from unittest.mock import patch

from tinkery_fixture import TINKERY, confirmed, corpus
from test_sdlc_stage import StageBase, PASSING
from test_sdlc_workflow import workflow


class TinkeryIntake(StageBase):
    def setUp(self):
        super().setUp()
        self.new_run('r', approve=False)
        self.seed = confirmed(self.base / 'TEST-sessions')
        self.responses = next(case for case in corpus()['cases']
                              if case['request']['records'][0]['kind'] == 'judge')
        self.calls = self.base / 'TEST-calls.jsonl'

    def verifier(self, responses=None, body=''):
        path = self.base / 'TEST tinkery executable'
        path.write_text(f'#!{sys.executable}\n'
                        'import json, pathlib, sys\n'
                        f'log = pathlib.Path({str(self.calls)!r})\n'
                        'calls = log.read_text().splitlines() if log.exists() else []\n'
                        'args = sys.argv[1:]\n'
                        'with log.open("a") as stream: stream.write(json.dumps(args) + "\\n")\n'
                        f'directory = {str(self.seed.parent)!r}\n'
                        'if args not in (["seed", "status", "--json", directory], ["seed", "checks", directory]):\n'
                        '    sys.exit("TEST wrong verifier routing")\n'
                        + body + '\n'
                        + f'responses = {responses if responses is not None else self.responses!r}\n'
                        'reply = responses["status" if args[1] == "status" else "checks"]\n'
                        'sys.stdout.write(reply["stdout"])\n'
                        'sys.stderr.write(reply["stderr"])\n'
                        'sys.exit(reply["returncode"])\n')
        path.chmod(0o755)
        return path

    def refuse(self, executable, message):
        before = {p.name: p.read_bytes() for p in self.seed.parent.iterdir() if p.is_file()}
        result = self.refuses('r', 1, '--worktree', str(self.base / 'TEST-worktree'),
                              '--seed-contract', str(self.seed), '--tinkery', str(executable), message=message)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.seed.parent.iterdir() if p.is_file()})
        for text in ('Traceback', 'Debug', 'TEST library error', 'JSONDecodeError', 'PermissionError'):
            self.assertNotIn(text, result.stderr)
        self.assertEqual(self.tink_calls(), [])
        return result

    def test_exact_commands_import_unchanged_checks_and_seed(self):
        executable = self.verifier()
        self.stage('r', 1, '--seed-contract', str(self.seed), '--tinkery', str(executable))
        directory = str(self.seed.parent)
        self.assertEqual([json.loads(line) for line in self.calls.read_text().splitlines()],
                         [['seed', 'status', '--json', directory], ['seed', 'checks', directory],
                          ['seed', 'status', '--json', directory]])
        run = self.root / 'runs/r'
        self.assertEqual((run / 'seed-contract.md').read_bytes(), self.seed.read_bytes())
        records = json.loads(self.responses['checks']['stdout'])
        items = json.loads((run / 'seed-checks.json').read_text())['items']
        self.assertEqual([{key: item[key] for key in record} for record, item in zip(records, items)], records)
        self.assertTrue(all(item['locked'] is True for item in items))

    def test_seed_bound_next_action_names_the_confirmed_seed_not_a_blank_brief(self):
        # #116: Approve's exact intake approves stage 3 while brief.md is still the template.
        self.set_checks([PASSING])
        self.assertEqual(self.sdlc('new', 'fresh').returncode, 0)
        self.stage('fresh', 1, '--seed-contract', str(self.seed), '--tinkery', str(self.verifier()))
        view = json.loads(self.sdlc('status', 'fresh', '--json').stdout)['run']
        self.assertEqual(view['gates'][-1]['status'], 'approved')
        self.assertEqual(view['next_action'],
                         'implement the confirmed seed (runs/fresh/seed-contract.md), then run verification.')
        text = self.sdlc('status', 'fresh').stdout
        self.assertIn(f"Next: {view['next_action']}", text)
        self.assertNotIn('approved brief', text)

    def test_missing_nonexecutable_directory_and_bad_executable_refuse(self):
        unavailable = self.base / 'TEST-missing'
        self.refuse(unavailable, 'Tinkery executable is unavailable')
        unavailable.mkdir()
        self.refuse(unavailable, 'Tinkery executable is unavailable')
        executable = self.verifier()
        executable.chmod(0o644)
        self.refuse(executable, 'Tinkery executable is unavailable')
        executable.chmod(0o755)
        executable.write_text('not an executable format\n')
        self.refuse(executable, 'Tinkery could not run')

    def test_failed_status_and_checks_preserve_recorded_refusal_reason(self):
        refusal = corpus()['refusals']['missing']['checks']
        self.assertNotEqual(refusal['returncode'], 0)
        reason = refusal['stderr'].strip()
        self.assertTrue(reason)
        for command in ('status', 'checks'):
            with self.subTest(command=command):
                responses = copy.deepcopy(self.responses)
                responses[command] = refusal
                result = self.refuse(self.verifier(responses), f'Tinkery refused seed intake: {reason}')
                self.assertEqual(result.stderr,
                                 f'Error: Tinkery refused seed intake: {reason}; '
                                 'check the session in Tinkery and retry.\n')

    def test_failed_status_and_checks_without_stderr_keep_plain_next_step(self):
        for command in ('status', 'checks'):
            for stderr in ('', ' \n '):
                with self.subTest(command=command, stderr=stderr):
                    responses = copy.deepcopy(self.responses)
                    responses[command] = {'returncode': 7, 'stdout': '{}', 'stderr': stderr}
                    result = self.refuse(self.verifier(responses), 'Tinkery refused seed intake')
                    self.assertEqual(result.stderr, 'Error: Tinkery refused seed intake; '
                                                   'check the session in Tinkery and retry.\n')

    def test_malformed_json_and_shapes_refuse_plainly(self):
        invalid = {'status': ['{', '[]', 'null', '17', '{}', 'NaN', 'Infinity', '[' * 20000 + '0' + ']' * 20000,
                              '{"handoff":[]}',
                              '{"handoff":{"verified":"true"}}', '{"handoff":{"verified":1}}',
                              '{"handoff":{"verified":true},"seed":null}',
                              '{"handoff":{"verified":true},"seed":{"sha256":7}}',
                              '{"handoff":{"verified":true},"seed":{"sha256":"bad"}}'],
                   'checks': ['{', '{}', 'null', '[]', '[null]', '[{}]', '[{"statement":17}]']}
        for command, values in invalid.items():
            for value in values:
                with self.subTest(command=command, output=value):
                    responses = copy.deepcopy(self.responses)
                    responses[command]['stdout'] = value
                    self.refuse(self.verifier(responses), 'Tinkery returned malformed seed')

    def test_unverified_handoff_is_a_gate_even_with_otherwise_valid_bindings(self):
        responses = copy.deepcopy(self.responses)
        status = json.loads(responses['status']['stdout'])
        status['handoff'] = {'verified': False, 'reason': 'TEST not confirmed'}
        responses['status']['stdout'] = json.dumps(status)
        self.refuse(self.verifier(responses), 'Tinkery handoff is not verified')

    def agent_receipt(self):
        # Synthetic consumer-boundary fixture matching Tinkery's GoalConfirmation::Agent.
        # The stub supplies verification; this is not evidence of Rust verification.
        receipt = self.seed.parent / 'seed-confirmation.json'
        confirmation = json.loads(receipt.read_text())
        confirmation.update(schema_version=3, operator='agent', actor={
            'by': 'agent', 'agent': 'TEST agent', 'authorizing_operator': 'TEST operator',
            'source': 'TEST scoped seed permission', 'class': {
                'door': 'two_way', 'door_reason': 'TEST reversible',
                'blast_radius': 'small', 'blast_radius_reason': 'TEST isolated'}})
        receipt.write_text(json.dumps(confirmation))
        return confirmation

    def test_attributed_v3_is_preserved_as_agent_in_run_and_status(self):
        confirmation = self.agent_receipt()
        result = self.stage('r', 1, '--seed-contract', str(self.seed), '--tinkery', str(self.verifier()))
        self.assertIn('agent-confirmed seed contract', result.stdout)
        self.assertNotIn('operator-confirmed seed contract', result.stdout)
        metadata = json.loads((self.root / 'runs/r/run.json').read_text())
        self.assertEqual(metadata['seed_contract']['confirmation'], confirmation)
        for args, key in [(('status', 'r', '--json'), 'run'), (('status', '--json'), 'runs')]:
            status = self.sdlc(*args)
            self.assertEqual(status.returncode, 0, status.stderr)
            run = json.loads(status.stdout)[key]
            if key == 'runs':
                run = next(item for item in run if item['slug'] == 'r')
            self.assertEqual(run['seed_contract']['confirmation'], confirmation)
            if key == 'run':
                self.assertTrue(all(gate['status'] != 'approved' for gate in run['gates']))

    def test_v3_locked_checks_still_require_human_approval_and_attestation(self):
        self.agent_receipt()
        self.stage('r', 1, '--seed-contract', str(self.seed), '--tinkery', str(self.verifier()))
        items = json.loads((self.root / 'runs/r/seed-checks.json').read_text())['items']
        self.assertTrue(all(item['origin'] == 'agent-confirmed-seed' and item['locked'] for item in items))
        self.set_checks([PASSING])
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)
        self.decide('r', 3)  # Synthetic test approval, not the operator's.
        refused = self.sdlc('mark', 'r', 'seed-1', 'passed', '--evidence', 'TEST worker guess')
        self.assertNotEqual(refused.returncode, 0)
        self.assertNotEqual(self.sdlc('verify', 'r').returncode, 0)
        attested = self.sdlc('mark', 'r', 'seed-1', 'passed', '--evidence', 'TEST observation',
                             '--reviewer', 'TEST human', '--source', 'TEST synthetic review')
        self.assertEqual(attested.returncode, 0, attested.stderr)
        passed = self.sdlc('verify', 'r')
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn('Seed checks (agent-confirmed)', passed.stdout)
        receipt = json.loads((self.root / 'runs/r/04-test/output/verification.json').read_text())
        self.assertEqual(receipt['seed_checks'], {'seed-1': 'attested'})
        self.assertNotIn('seed-1', receipt['checklist_items'])

    def test_v3_missing_blank_malformed_or_ineligible_attribution_refuses(self):
        original = self.agent_receipt()
        mutations = [('actor', None), ('operator', 'human')]
        for key in ('by', 'agent', 'authorizing_operator', 'source', 'class'):
            mutations.extend((f'actor.{key}', value) for value in (None, '', ' \t ', 17, [], {}))
        for key in ('door', 'door_reason', 'blast_radius', 'blast_radius_reason'):
            mutations.extend((f'actor.class.{key}', value) for value in (None, '', ' \t ', 17, [], {}))
        mutations.extend([('actor.by', 'operator'), ('actor.class.door', 'one_way'),
                          ('actor.class.blast_radius', 'large'), ('actor.extra', 'TEST'),
                          ('actor.class.extra', 'TEST')])
        receipt = self.seed.parent / 'seed-confirmation.json'
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                confirmation = copy.deepcopy(original)
                target = confirmation
                parts = field.split('.')
                for part in parts[:-1]:
                    target = target[part]
                if value is None:
                    target.pop(parts[-1])
                else:
                    target[parts[-1]] = value
                receipt.write_text(json.dumps(confirmation))
                self.refuse(self.verifier(), 'agent confirmation needs agent attribution')

    def declared_receipt(self):
        # Synthetic fixture matching Tinkery's terminal `--operator agent` declaration.
        confirmation = self.agent_receipt()
        confirmation['actor'] = {'by': 'terminal', 'declaration': 'declared: agent',
                                 'action': 'typed confirm at this terminal',
                                 'identity': 'declaration is not verified identity'}
        (self.seed.parent / 'seed-confirmation.json').write_text(json.dumps(confirmation))
        return confirmation

    def test_v3_terminal_agent_declaration_is_preserved_as_agent(self):
        confirmation = self.declared_receipt()
        result = self.stage('r', 1, '--seed-contract', str(self.seed), '--tinkery', str(self.verifier()))
        self.assertIn('agent-confirmed seed contract', result.stdout)
        metadata = json.loads((self.root / 'runs/r/run.json').read_text())
        self.assertEqual(metadata['seed_contract']['confirmation'], confirmation)
        items = json.loads((self.root / 'runs/r/seed-checks.json').read_text())['items']
        self.assertTrue(all(item['origin'] == 'agent-confirmed-seed' for item in items))

    def test_v3_terminal_declaration_other_than_agent_refuses(self):
        original = self.declared_receipt()
        receipt = self.seed.parent / 'seed-confirmation.json'
        for key, value in [('declaration', 'declared: human'), ('declaration', 'not declared'),
                           ('identity', 'verified'), ('action', ''), ('extra', 'TEST')]:
            with self.subTest(key=key, value=value):
                confirmation = copy.deepcopy(original)
                confirmation['actor'][key] = value
                receipt.write_text(json.dumps(confirmation))
                self.refuse(self.verifier(), 'agent confirmation needs agent attribution')

    def test_v3_unverified_draft_and_changed_attribution_refuse(self):
        self.agent_receipt()
        responses = copy.deepcopy(self.responses)
        status = json.loads(responses['status']['stdout'])
        status['handoff']['verified'] = False
        responses['status']['stdout'] = json.dumps(status)
        self.refuse(self.verifier(responses), 'Tinkery handoff is not verified')
        body = ('if args[1] == "checks":\n'
                '    path = pathlib.Path(directory) / "seed-confirmation.json"\n'
                '    record = json.loads(path.read_text())\n'
                '    record["actor"]["authorizing_operator"] = "TEST changed operator"\n'
                '    path.write_text(json.dumps(record))\n')
        self.refuses('r', 1, '--seed-contract', str(self.seed), '--tinkery', str(self.verifier(body=body)),
                     message='confirmation binding changed during intake')

    def test_v2_confirmation_and_status_remain_unchanged(self):
        receipt = json.loads((self.seed.parent / 'seed-confirmation.json').read_text())
        self.assertEqual(receipt['schema_version'], 2)
        result = self.stage('r', 1, '--seed-contract', str(self.seed), '--tinkery', str(self.verifier()))
        self.assertIn('operator-confirmed seed contract', result.stdout)
        metadata = json.loads((self.root / 'runs/r/run.json').read_text())
        self.assertEqual(metadata['seed_contract']['confirmation'], receipt)
        status = self.sdlc('status', 'r', '--json')
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(set(json.loads(status.stdout)['run']['seed_contract']), {'path', 'sha256', 'source'})

    def test_changed_handoff_between_commands_refuses(self):
        body = ('if len(calls) == 2:\n'
                f'    status = json.loads({self.responses["status"]["stdout"]!r})\n'
                '    status["handoff"]["verified"] = False\n'
                '    print(json.dumps(status))\n'
                '    sys.exit(0)\n')
        self.refuse(self.verifier(body=body), 'confirmation binding changed during intake')

    def test_seed_and_confirmation_changes_during_intake_refuse(self):
        original_seed = self.seed.read_bytes()
        receipt = self.seed.parent / 'seed-confirmation.json'
        original_receipt = receipt.read_bytes()
        for name in ('seed-contract.md', 'seed-confirmation.json'):
            with self.subTest(name=name):
                mutation = ('path.write_text(path.read_text() + " ")' if name.endswith('.md') else
                            'record = json.loads(path.read_text()); record["source"] = "TEST changed"; '
                            'path.write_text(json.dumps(record))')
                body = ('if args[1] == "checks":\n'
                        f'    path = pathlib.Path(directory) / {name!r}\n'
                        f'    {mutation}\n')
                # The intentional verifier-side change is outside the checkout under test.
                executable = self.verifier(body=body)
                self.refuses('r', 1, '--seed-contract', str(self.seed), '--tinkery', str(executable),
                             message='confirmation binding changed during intake')
                self.seed.write_bytes(original_seed)
                receipt.write_bytes(original_receipt)

    def test_confirmation_malformed_or_missing_refuses_even_if_verifier_claims_success(self):
        path = self.seed.parent / 'seed-confirmation.json'
        original = path.read_bytes()
        for value in ('{', '[]', 'null', '{}', None):
            with self.subTest(value=value):
                if value is None:
                    path.unlink()
                else:
                    path.write_text(value)
                self.refuse(self.verifier(), 'Tinkery')
                path.write_bytes(original)

    def test_checks_digest_cannot_disagree_with_the_verified_receipt(self):
        responses = copy.deepcopy(self.responses)
        records = json.loads(responses['checks']['stdout'])
        records[0]['statement'] = 'TEST altered checks response'
        responses['checks']['stdout'] = json.dumps(records)
        self.refuse(self.verifier(responses), 'confirmation binding changed during intake')

    def test_verifier_timeout_and_invalid_encoding_have_plain_errors(self):
        for error in (subprocess.TimeoutExpired('TEST verifier', 30),
                      UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'TEST library error')):
            with self.subTest(error=type(error).__name__):
                with patch.object(workflow.subprocess, 'run', side_effect=error):
                    with self.assertRaisesRegex(ValueError, 'Tinkery could not run the seed verifier'):
                        workflow.stage_seed_contract(str(self.seed), str(TINKERY))

    def test_tinkery_without_seed_refuses(self):
        self.refuses('r', 1, '--tinkery', str(TINKERY), message='--tinkery requires --seed-contract')

    def test_removed_helper_always_names_tinkery_and_writes_nothing(self):
        for flags in (['--seed-me-helper', '/TEST/old.py'], ['--seed-me-helper=/TEST/old.py'],
                      ['--seed-contract', str(self.seed), '--tinkery', str(TINKERY), '--seed-me-helper', '/TEST/old.py']):
            with self.subTest(flags=flags):
                before = self.snapshot_state()
                result = self.stage('r', 1, *flags, ok=False)
                self.assertIn('--seed-me-helper was removed; use --tinkery PATH', result.stderr)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(before, self.snapshot_state())
                self.assertEqual(self.tink_calls(), [])
