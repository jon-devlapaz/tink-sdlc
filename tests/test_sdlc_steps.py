"""Pinned build-step contracts, exercised through the installed runtime and a fake adapter."""
import json
from pathlib import Path

from test_sdlc_stage import StageBase, PYTHON


FAKE = '''#!{python}
import argparse, json, os
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('operation')
for flag in ('model', 'report', 'contract', 'session', 'for-operation'):
    p.add_argument('--' + flag)
a = p.parse_args()
root = Path(__file__).parent
with (root / 'adapter.log').open('a') as log:
    log.write(json.dumps(vars(a)) + '\\n')
operation = a.for_operation if a.operation == 'resolve' else a.operation
model = os.environ.get('TEST_RESOLVED_MODEL', a.model or os.environ.get('TEST_DEFAULT_MODEL'))
reply = {{'protocol': 'tink-sdlc-launcher', 'api_version': 1, 'model': model,
          'operation': operation, 'session': a.session}}
if os.environ.get('TEST_INCOMPATIBLE'):
    reply['protocol'] = 'unsupported'
if a.operation == 'resolve' and os.environ.get('TEST_RESOLVED_SESSION'):
    reply['session'] = os.environ['TEST_RESOLVED_SESSION']
if a.operation == 'resolve' and os.environ.get('TEST_BAD_JSON'):
    print('not JSON')
    raise SystemExit(0)
if a.operation == 'finished':
    if os.environ.get('TEST_NO_FINISHED'):
        raise SystemExit(2)
    reply['finished'] = not os.environ.get('TEST_UNFINISHED')
elif a.operation != 'resolve':
    with (root / 'requests.log').open('a') as log:
        log.write(a.operation + ' ' + a.model + '\\n')
    reply['session'] = a.session or 'TEST-session'
    if os.environ.get('TEST_ADAPTER_FAIL'):
        raise SystemExit(1)
    if os.environ.get('TEST_WRITE_REPORT'):
        Path(a.report).write_text('TEST handoff; model=' + a.model)
print(json.dumps(reply))
'''


class StepTests(StageBase):
    def setUp(self):
        super().setUp()
        self.new_run('r')
        self.launcher = self.bin / 'TEST-launcher'
        self.launcher.write_text(FAKE.format(python=PYTHON))
        self.launcher.chmod(0o755)
        self.report = self.base / 'TEST-notes' / 'handoff.md'
        self.report.parent.mkdir()
        self.contract = self.root / 'runs/r/steps/build.json'
        self.model = 'test-provider/exact-model'

    def step(self, action, *args, ok=True, env=None, root=None, cwd='/'):
        result = self.sdlc('step', 'r', 'build', action, *args, env=env, root=root, cwd=cwd)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def prepare(self, **kwargs):
        return self.step('prepare', '--report', str(self.report), '--model', self.model,
                         '--launcher', str(self.launcher), **kwargs)

    def requests(self):
        path = self.bin / 'requests.log'
        return path.read_text().splitlines() if path.exists() else []

    def test_absolute_report_and_exact_model_hold_from_another_cwd_and_on_resume(self):
        self.prepare()
        contract = json.loads(self.contract.read_text())
        self.assertEqual(contract['report'], str(self.report))
        self.assertEqual(contract['model'], self.model)
        self.step('launch', env={'TEST_DEFAULT_MODEL': 'test-provider/different-default'})
        self.step('resume', cwd=str(self.base), env={'TEST_DEFAULT_MODEL': 'test-provider/another-default',
                                                  'TEST_WRITE_REPORT': '1'})
        calls = [json.loads(line) for line in (self.bin / 'adapter.log').read_text().splitlines()]
        self.assertEqual([c['operation'] for c in calls], ['resolve', 'launch', 'resolve', 'resume'])
        for call in calls:
            self.assertEqual(call['model'], self.model)
            self.assertEqual(call['report'], str(self.report))
            self.assertEqual(call['contract'], str(self.contract))
        self.assertEqual(calls[-1]['session'], 'TEST-session')
        self.assertEqual(self.requests(), ['launch ' + self.model, 'resume ' + self.model])
        self.step('complete')
        self.assertEqual(json.loads(self.contract.read_text())['state'], 'completed')
        self.assertIn('report_sha256', json.loads(self.contract.read_text()))

    def test_missing_or_wrong_report_refuses_completion_without_root_fallback(self):
        self.prepare()
        self.step('launch')
        (self.root / 'handoff.md').write_text('TEST wrong location')
        before = self.contract.read_bytes()
        result = self.step('complete', ok=False)
        self.assertIn('report is missing', result.stderr)
        self.assertEqual(before, self.contract.read_bytes())
        self.report.write_text('TEST exact destination')
        self.step('complete', cwd=str(self.report.parent))

    def test_mismatch_or_unresolved_pin_never_launches_or_resumes(self):
        self.prepare()
        for resolved in ('test-provider/other-model', '', 'exact-model'):
            with self.subTest(resolved=resolved):
                before = self.contract.read_bytes()
                self.step('launch', ok=False, env={'TEST_RESOLVED_MODEL': resolved})
                self.assertEqual(self.requests(), [])
                self.assertEqual(self.contract.read_bytes(), before)
        self.step('launch')
        before = self.contract.read_bytes()
        self.step('resume', ok=False, env={'TEST_RESOLVED_MODEL': 'test-provider/new-default'})
        self.assertEqual(self.requests(), ['launch ' + self.model])
        self.assertEqual(self.contract.read_bytes(), before)

    def test_incompatible_adapter_refuses_before_provider_request(self):
        self.prepare()
        self.step('launch', ok=False, env={'TEST_INCOMPATIBLE': '1'})
        self.assertEqual(self.requests(), [])

    def test_malformed_resolution_and_wrong_resume_session_never_request(self):
        self.prepare()
        self.step('launch', ok=False, env={'TEST_BAD_JSON': '1'})
        self.assertEqual(self.requests(), [])
        self.step('launch')
        self.step('resume', ok=False, env={'TEST_RESOLVED_SESSION': 'TEST-other-session'})
        self.assertEqual(self.requests(), ['launch ' + self.model])

    def test_changed_launcher_refuses_before_even_resolution(self):
        self.prepare()
        self.launcher.write_text(self.launcher.read_text() + '\n# changed adapter\n')
        self.step('launch', ok=False)
        self.assertFalse((self.bin / 'adapter.log').exists())
        self.assertEqual(self.requests(), [])

    def test_relative_report_and_model_aliases_refused_without_contract(self):
        result = self.step('prepare', '--report', 'handoff.md', '--model', self.model,
                           '--launcher', str(self.launcher), ok=False)
        self.assertIn('absolute', result.stderr)
        self.assertFalse(self.contract.exists())
        for model in ('default', 'test-provider/*', ' test-provider/model', ''):
            self.model = model
            self.prepare(ok=False)
            self.assertFalse(self.contract.exists())
        self.assertEqual(self.requests(), [])

    def test_contract_cannot_be_replaced_or_overridden_on_resume(self):
        self.prepare()
        before = self.contract.read_bytes()
        self.prepare(ok=False)
        self.step('resume', '--model', 'test-provider/other', ok=False)
        self.assertEqual(before, self.contract.read_bytes())
        self.assertEqual(self.requests(), [])

    def test_report_from_an_unfinished_or_unprobed_run_cannot_complete(self):
        self.prepare()
        self.step('launch', env={'TEST_WRITE_REPORT': '1'})
        for env in ({'TEST_UNFINISHED': '1'}, {'TEST_NO_FINISHED': '1'}, {'TEST_INCOMPATIBLE': '1'}):
            with self.subTest(env=env):
                result = self.step('complete', ok=False, env=env)
                self.assertIn('Step adapter' if 'TEST_UNFINISHED' not in env else 'not finished', result.stderr)
                self.assertEqual(json.loads(self.contract.read_text())['state'], 'active')
        self.step('complete')
        calls = [json.loads(line) for line in (self.bin / 'adapter.log').read_text().splitlines()]
        self.assertEqual(calls[-1]['operation'], 'finished')
        self.assertEqual(calls[-1]['session'], 'TEST-session')
        self.assertEqual(self.requests(), ['launch ' + self.model])

    def test_show_says_whether_an_active_steps_turn_ended_without_storing_it(self):
        # tinkery#615: `active` alone cannot tell a running agent from an ended turn.
        self.prepare()
        self.assertNotIn('observed_turn', json.loads(self.step('show').stdout))
        self.step('launch', env={'TEST_WRITE_REPORT': '1'})
        before = self.contract.read_bytes()
        for env, seen in (({}, 'ended: read the report, then complete the step'),
                          ({'TEST_UNFINISHED': '1'}, 'not ended: The agent has not finished'),
                          ({'TEST_NO_FINISHED': '1'}, 'unknown: Step adapter finished failed'),
                          ({'TEST_INCOMPATIBLE': '1'}, 'unknown: Step adapter cannot resolve')):
            with self.subTest(env=env):
                shown = json.loads(self.step('show', env=env).stdout)
                self.assertEqual(shown['state'], 'active')
                self.assertTrue(shown['observed_turn'].startswith(seen), shown['observed_turn'])
        self.launcher.write_text(self.launcher.read_text() + '\n')
        self.assertTrue(json.loads(self.step('show').stdout)['observed_turn'].startswith('unknown: Step launcher changed'))
        self.assertEqual(before, self.contract.read_bytes())
        self.assertEqual(self.requests(), ['launch ' + self.model])

    def test_status_reports_each_step_and_an_active_turn_sets_the_next_action(self):
        # tinkery#614: an imported run read as being worked on while no worker ran.
        def status(env=None):
            result = self.sdlc('status', 'r', '--json', env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)['run']
        idle = status()
        self.assertEqual(idle['steps'], [])
        self.prepare()
        self.assertEqual(status()['steps'], [{'name': 'build', 'state': 'prepared'}])
        self.step('launch')
        before = self.contract.read_bytes()
        command = 'python3 _system/scripts/sdlc.py step r build'
        for env, turn, action in (
                ({'TEST_UNFINISHED': '1'}, 'not ended', f'The build worker has not ended its turn. Check it with {command} show.'),
                ({}, 'ended', f"The build worker's turn ended: read its report, then run {command} complete."),
                ({'TEST_NO_FINISHED': '1'}, 'unknown', 'Whether the build worker is running is unknown: ')):
            with self.subTest(turn=turn):
                run = status(env)
                self.assertEqual(run['steps'][0]['state'], 'active')
                self.assertEqual(run['steps'][0]['turn'], turn.replace(' ', '-'), run['steps'])
                self.assertTrue(run['steps'][0]['observed_turn'].startswith(turn), run['steps'])
                self.assertTrue(run['next_action'].startswith(action), run['next_action'])
        self.assertEqual(before, self.contract.read_bytes())
        self.assertEqual(self.requests(), ['launch ' + self.model])
        self.contract.write_text('not JSON')
        self.assertEqual(status()['steps'], [{'name': 'build', 'state': 'unreadable'}])

    def test_symlink_or_empty_report_cannot_complete(self):
        self.prepare()
        self.step('launch')
        self.report.write_text('')
        self.step('complete', ok=False)
        self.report.unlink()
        elsewhere = self.base / 'elsewhere.md'
        elsewhere.write_text('TEST wrong file')
        self.report.symlink_to(elsewhere)
        self.step('complete', ok=False)
        self.report.unlink()
        self.report.parent.rename(self.base / 'old-notes')
        self.report.parent.symlink_to(self.base / 'old-notes', target_is_directory=True)
        self.step('complete', ok=False)

    def test_existing_report_is_not_reused_as_new_evidence(self):
        self.report.write_text('TEST stale handoff')
        self.prepare(ok=False)
        self.assertFalse(self.contract.exists())

    def test_resume_requires_session_and_completion_requires_launch(self):
        self.prepare()
        self.step('resume', ok=False)
        self.report.write_text('TEST handoff without launch')
        self.step('complete', ok=False)
        self.assertEqual(self.requests(), [])

    def test_stale_approval_blocks_launch_before_provider_request(self):
        self.prepare()
        (self.root / 'runs/r/brief.md').write_text('TEST changed definition')
        self.step('launch', ok=False)
        self.assertEqual(self.requests(), [])
        self.assertFalse((self.bin / 'adapter.log').exists())

    def test_copied_contract_refuses_a_different_checkout(self):
        self.prepare()
        record = json.loads(self.contract.read_text())
        record['checkout'] = str(self.base / 'other-checkout')
        self.contract.write_text(json.dumps(record))
        self.step('launch', ok=False)
        self.assertEqual(self.requests(), [])

    def test_adapter_failure_is_uncertain_and_cannot_silently_retry(self):
        self.prepare()
        self.step('launch', ok=False, env={'TEST_ADAPTER_FAIL': '1'})
        self.assertEqual(json.loads(self.contract.read_text())['state'], 'uncertain')
        self.step('launch', ok=False)
        self.step('resume', ok=False)
        self.assertEqual(self.requests(), ['launch ' + self.model])

    def test_status_names_an_uncertain_step_as_the_next_action(self):
        # tink-sdlc#137: an uncertain step was skipped, so the run read as ready for ordinary work.
        self.prepare()
        self.step('launch', ok=False, env={'TEST_ADAPTER_FAIL': '1'})
        result = self.sdlc('status', 'r', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        run = json.loads(result.stdout)['run']
        self.assertEqual(run['steps'], [{'name': 'build', 'state': 'uncertain'}])
        self.assertTrue(run['next_action'].startswith(
            "The build step's launch or resume did not confirm; a worker may still be running. "
            'Inspect it with python3 _system/scripts/sdlc.py step r build show'), run['next_action'])

    def test_malformed_active_contract_cannot_complete(self):
        self.prepare()
        record = json.loads(self.contract.read_text())
        record.update(state='active', session=None)
        self.contract.write_text(json.dumps(record))
        self.report.write_text('TEST forged active state')
        self.step('complete', ok=False)
        self.assertEqual(self.requests(), [])

    def test_step_session_marker_refuses_mutations_but_allows_show(self):
        result = self.step('prepare', '--report', str(self.report), '--model', self.model,
                           '--launcher', str(self.launcher), ok=False,
                           env={'TINK_SDLC_STEP_SESSION': '1'})
        self.assertIn('inside its agent session', result.stderr)
        self.assertFalse(self.contract.exists())
        self.prepare()
        result = self.step('launch', ok=False, env={'TINK_SDLC_STEP_SESSION': '1'})
        self.assertIn('inside its agent session', result.stderr)
        self.step('launch')
        result = self.step('resume', ok=False, env={'TINK_SDLC_STEP_SESSION': '1'})
        self.assertIn('inside its agent session', result.stderr)
        result = self.step('complete', ok=False, env={'TINK_SDLC_STEP_SESSION': '1'})
        self.assertIn('inside its agent session', result.stderr)
        before = self.contract.read_bytes()
        shown = json.loads(self.step('show', env={'TINK_SDLC_STEP_SESSION': '1'}).stdout)
        self.assertEqual(shown['model'], self.model)
        self.assertEqual(before, self.contract.read_bytes())

    def test_show_is_read_only_and_completed_step_cannot_run_again(self):
        self.prepare()
        before = self.contract.read_bytes()
        shown = json.loads(self.step('show').stdout)
        self.assertEqual(shown['model'], self.model)
        self.assertEqual(before, self.contract.read_bytes())
        self.step('launch', env={'TEST_WRITE_REPORT': '1'})
        self.step('complete')
        self.step('launch', ok=False)
        self.step('resume', ok=False)
        self.assertEqual(self.requests(), ['launch ' + self.model])
