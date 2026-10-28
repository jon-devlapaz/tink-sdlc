"""The bundled Pi launcher adapter, driven through sdlc.py step and its own CLI with a fake Pi."""
import json
import os
import subprocess
import time
from pathlib import Path

from test_sdlc_stage import StageBase, PYTHON
from tinkery_fixture import TINKERY, confirmed, command, provisional, DEFAULT


# A stand-in for Pi: records argv, writes session files the way Pi does, and answers
# on --model unless told to drift. Without --model it would use FAKE_PI_DEFAULT.
FAKE_PI = '''#!{python}
import json, os, sys, time, uuid
from pathlib import Path
argv = sys.argv[1:]
with open(os.environ['FAKE_PI_LOG'], 'a') as log:
    log.write(json.dumps({{'argv': argv, 'step_session': os.environ.get('TINK_SDLC_STEP_SESSION')}}) + '\\n')
Path(os.environ['FAKE_PI_LOG'] + '.pid').write_text(str(os.getpid()))
def flag(name):
    return argv[argv.index(name) + 1] if name in argv else None
if os.environ.get('FAKE_PI_EXIT'):
    raise SystemExit(int(os.environ['FAKE_PI_EXIT']))
model = flag('--model') or os.environ['FAKE_PI_DEFAULT']
provider, model_id = model.split('/', 1)
answer = os.environ.get('FAKE_PI_ANSWER', model)
def entry(kind, parent, **fields):
    return {{'type': kind, 'id': uuid.uuid4().hex[:8], 'parentId': parent,
             'timestamp': '2026-10-24T00:00:00.000Z', **fields}}
def message(parent, role, **fields):
    return entry('message', parent, message={{'role': role, 'content': 'TEST', **fields}})
if '--session-id' in argv:
    path = Path(flag('--session-dir')) / ('2026-10-24T00-00-00-000Z_' + flag('--session-id') + '.jsonl')
    rows = [{{'type': 'session', 'version': 3, 'id': flag('--session-id'), 'cwd': os.getcwd()}}]
    rows.append(entry('model_change', None, provider=provider, modelId=model_id))
    rows.append(entry('thinking_level_change', rows[-1]['id'], thinkingLevel=flag('--thinking') or 'medium'))
else:
    path = Path(flag('--session'))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows.append(entry('session_info', rows[-1]['id'], name='TEST'))
rows.append(message(rows[-1]['id'], 'user'))
answered = [message(rows[-1]['id'], 'assistant', provider=answer.split('/')[0], model=answer.split('/', 1)[1],
                    stopReason='toolUse' if os.environ.get('FAKE_PI_PAUSED') else 'stop')]
if os.environ.get('FAKE_PI_PAUSED'):
    # Stopped after a tool ran, before the closing answer: the shape of a paused run.
    answered.append(message(answered[-1]['id'], 'toolResult', toolName='write'))
path.write_text(''.join(json.dumps(r) + '\\n' for r in rows))
time.sleep(float(os.environ.get('FAKE_PI_THINK', '0.3')))
with path.open('a') as stream:
    stream.write(''.join(json.dumps(r) + '\\n' for r in answered))
time.sleep(float(os.environ.get('FAKE_PI_LINGER', '0')))
'''

PIN = 'test-provider/pinned-v1:low'


class PiLauncherTests(StageBase):
    def setUp(self):
        super().setUp()
        self.new_run('r')
        self.adapter = self.root / '_system/scripts/pi-launcher'
        self.agent = self.home / 'TEST-pi-agent'
        self.agent.mkdir()
        self.catalog({'test-provider': {'models': [
            {'id': 'pinned-v1', 'provider': 'test-provider', 'reasoning': True},
            {'id': 'default-v1', 'provider': 'test-provider', 'reasoning': True},
            {'id': 'plain-v1', 'provider': 'test-provider', 'reasoning': False}]},
            'no-auth-provider': {'models': [{'id': 'm', 'reasoning': True}]}})
        (self.agent / 'auth.json').write_text(json.dumps({'test-provider': {'type': 'oauth', 'refresh': 'TEST-refresh'}}))
        (self.agent / 'settings.json').write_text(json.dumps({'defaultProvider': 'test-provider', 'defaultModel': 'default-v1'}))
        self.pi = self.bin / 'TEST-fake-pi'
        self.pi.write_text(FAKE_PI.format(python=PYTHON))
        self.pi.chmod(0o755)
        self.pi_log = self.base / 'fake-pi.log'
        self.env.update({'PI_CODING_AGENT_DIR': str(self.agent), 'TINK_SDLC_PI': str(self.pi),
                         'FAKE_PI_LOG': str(self.pi_log), 'FAKE_PI_DEFAULT': 'test-provider/default-v1'})
        self.report = self.base / 'TEST-notes' / 'build.md'
        self.report.parent.mkdir()
        self.sessions = self.agent / 'sessions' / ('--' + str(self.root).lstrip('/').replace('/', '-') + '--')

    def catalog(self, store):
        (self.agent / 'models-store.json').write_text(json.dumps(store))

    def step(self, name, action, *args, ok=True, env=None):
        result = self.sdlc('step', 'r', name, action, *args, env=env)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def prepare(self, name='build', pin=PIN):
        self.step(name, 'prepare', '--report', str(self.report.with_name(name + '.md')), '--model', pin,
                  '--launcher', str(self.adapter))

    def contract(self, name='build'):
        return json.loads((self.root / f'runs/r/steps/{name}.json').read_text())

    def calls(self):
        return [json.loads(line)['argv'] for line in self.pi_log.read_text().splitlines()] if self.pi_log.exists() else []

    def session_file(self, name='build'):
        [path] = [p for p in self.sessions.glob('*.jsonl') if self.contract(name)['session'] in p.name]
        return path

    def launch(self, name='build'):
        """Launch, then wait for the fake Pi's first answer so later steps see a settled session."""
        self.step(name, 'launch')
        for _ in range(50):
            if '"assistant"' in self.session_file(name).read_text():
                return
            time.sleep(0.1)
        self.fail('the fake Pi never answered')

    def resolve(self, pin=PIN, operation='launch', session=None, env=None):
        argv = [str(self.adapter), 'resolve', '--for-operation', operation, '--model', pin,
                '--report', str(self.report), '--contract', str(self.root / 'runs/r/steps/build.json')]
        argv += ['--session', session] if session else []
        return subprocess.run(argv, cwd=self.root, capture_output=True, text=True, env={**self.env, **(env or {})})

    def test_launch_and_resume_pass_the_pin_explicitly_and_ignore_pi_defaults(self):
        self.prepare()
        self.launch()
        session = self.contract()['session']
        self.assertTrue(session.startswith('tsdlc-'))
        self.step('build', 'resume')
        launch, resume = self.calls()
        for argv in (launch, resume):
            self.assertEqual(argv[:2], ['--mode', 'json'])
            self.assertEqual(argv[argv.index('--model') + 1], 'test-provider/pinned-v1')
            self.assertEqual(argv[argv.index('--thinking') + 1], 'low')
            self.assertEqual(argv[argv.index('--session-dir') + 1], str(self.sessions))
            self.assertIn(str(self.report.with_name('build.md')), argv[-1])
        self.assertEqual(launch[launch.index('--session-id') + 1], session)
        self.assertEqual(resume[resume.index('--session') + 1], str(self.session_file()))
        for call in self.pi_log.read_text().splitlines():
            recorded = json.loads(call)
            self.assertEqual(recorded['step_session'], '1')
            prompt = recorded['argv'][-1]
            self.assertIn(str(self.root / 'runs/r/brief.md'), prompt)
            self.assertTrue((self.root / 'runs/r/brief.md').is_file())
            self.assertIn(str(self.root / 'stages/03-build/CONTEXT.md'), prompt)
            self.assertTrue((self.root / 'stages/03-build/CONTEXT.md').is_file())
            self.assertIn(str(self.report.with_name('build.md')), prompt)
            self.assertIn('Never run sdlc.py step, stage or decide', prompt)
        answers = [json.loads(line)['message']['model'] for line in self.session_file().read_text().splitlines()
                   if '"assistant"' in line]
        self.assertEqual(answers, ['pinned-v1', 'pinned-v1'])
        self.report.with_name('build.md').write_text('TEST handoff')
        self.step('build', 'complete')
        self.assertEqual(self.contract()['state'], 'completed')

    def test_pending_provisional_checks_and_proposals_are_named_on_launch_and_resume(self):
        records = [command(), provisional(), provisional('false'), {**DEFAULT[0], 'how_to_judge': 'Inspect'}]
        seed = confirmed(self.base / 'TEST-sessions', records)
        self.stage('r', 1, '--seed-contract', str(seed), '--tinkery', str(TINKERY))
        target = self.root / 'runs/r/seed-checks.json'
        value = json.loads(target.read_text())
        self.decide('r', 3)
        self.prepare()
        self.launch()
        self.step('build', 'resume')
        prompts = [argv[-1] for argv in self.calls()]
        self.assertEqual(len(prompts), 2)
        for prompt in prompts:
            self.assertIn(f"seed-2: prerequisite: {value['items'][1]['missing']}\n", prompt)
            self.assertIn(f"seed-3: prerequisite: {value['items'][2]['missing']}; proposed: `false` (cwd: .)", prompt)
            self.assertNotIn('seed-1', prompt)
            self.assertNotIn('seed-4', prompt)
            self.assertIn(str(self.root / '_system/SDLC.md') + '#seed-definition-comparison', prompt)
            self.assertIn(str(target), prompt)
            for instruction in ('Once the prerequisite exists', 'keep the original proposal intact',
                                'Activating the proposed cmd in its cwd matches the confirmed seed',
                                "operator's stage-3 approval", 'not proof', 'handoff report'):
                self.assertIn(instruction, prompt)
        for item in value['items']:
            if item['kind'] == 'provisional':
                item['activation'] = {'cmd': 'true', 'cwd': '.', 'evidence': 'TEST ready'}
        target.write_text(json.dumps(value))
        self.decide('r', 3)
        self.step('build', 'resume')
        self.assertNotIn('provisional', self.calls()[-1][-1].lower())

    def test_full_profile_launch_and_resume_name_existing_approved_inputs(self):
        self.new_run('full-run', profile='full')
        run_dir = self.root / 'runs/full-run'
        self.report_full = self.base / 'TEST-notes' / 'full-build.md'
        contract = run_dir / 'steps' / 'build.json'
        prepared = self.sdlc('step', 'full-run', 'build', 'prepare', '--report', str(self.report_full),
                             '--model', PIN, '--launcher', str(self.adapter))
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        launched = self.sdlc('step', 'full-run', 'build', 'launch')
        self.assertEqual(launched.returncode, 0, launched.stdout + launched.stderr)
        session = json.loads(launched.stdout)['session']
        for _ in range(50):
            session_files = [path for path in self.sessions.glob('*.jsonl') if session in path.name]
            if session_files and '"assistant"' in session_files[0].read_text():
                break
            time.sleep(0.1)
        else:
            self.fail('the fake Pi never answered the full-profile launch')
        resumed = self.sdlc('step', 'full-run', 'build', 'resume')
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        prompts = [json.loads(line)['argv'][-1] for line in self.pi_log.read_text().splitlines()]
        self.assertEqual(len(prompts), 2)
        expected = [run_dir / '01-plan/output/intent.md', run_dir / '02-design/output/spec.md',
                    run_dir / '03-build/output/plan.md', self.root / 'stages/03-build/CONTEXT.md']
        for prompt in prompts:
            for path in expected:
                self.assertTrue(path.is_file(), path)
                self.assertIn(str(path), prompt)
            self.assertIn(str(self.report_full), prompt)
            self.assertIn('Never run sdlc.py step, stage or decide', prompt)

    def test_completion_waits_for_a_finished_pinned_turn_and_resume_can_finish_it(self):
        self.prepare()
        self.step('build', 'launch', env={'FAKE_PI_PAUSED': '1'})
        for _ in range(50):
            if '"toolResult"' in self.session_file().read_text():
                break
            time.sleep(0.1)
        report = self.report.with_name('build.md')
        report.write_text('TEST handoff left by a paused run')
        result = self.step('build', 'complete', ok=False)
        self.assertIn('not finished', result.stderr)
        self.assertEqual(self.contract()['state'], 'active')
        self.step('build', 'resume')
        self.step('build', 'complete')
        self.assertEqual(self.contract()['state'], 'completed')
        self.assertEqual(len(self.calls()), 2)

    def test_a_final_answer_off_the_pin_cannot_complete(self):
        self.prepare()
        self.launch()
        rows = self.session_file().read_text().splitlines()
        drift = {'type': 'message', 'id': 'drift002', 'parentId': json.loads(rows[-1])['id'], 'timestamp': 't',
                 'message': {'role': 'assistant', 'provider': 'test-provider', 'model': 'default-v1',
                             'stopReason': 'stop'}}
        self.session_file().write_text('\n'.join(rows + [json.dumps(drift)]) + '\n')
        self.report.with_name('build.md').write_text('TEST handoff')
        self.step('build', 'complete', ok=False)
        self.assertEqual(self.contract()['state'], 'active')
        self.assertEqual(len(self.calls()), 1)

    def test_pi_session_dir_override_is_honored_on_launch_and_resume(self):
        self.sessions = self.base / 'TEST-sessions'
        self.env['PI_CODING_AGENT_SESSION_DIR'] = str(self.sessions)
        self.prepare()
        self.launch()
        self.step('build', 'resume')
        for argv in self.calls():
            self.assertEqual(argv[argv.index('--session-dir') + 1], str(self.sessions))
        self.assertFalse((self.agent / 'sessions').exists())

    def test_resume_refuses_a_session_that_drifted_off_the_pin_without_starting_pi(self):
        self.prepare()
        self.launch()
        rows = self.session_file().read_text().splitlines()
        last = json.loads(rows[-1])['id']
        drift = {'type': 'message', 'id': 'drift001', 'parentId': last, 'timestamp': 't',
                 'message': {'role': 'assistant', 'provider': 'test-provider', 'model': 'default-v1'}}
        self.session_file().write_text('\n'.join(rows + [json.dumps(drift)]) + '\n')
        result = self.step('build', 'resume', ok=False)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.contract()['state'], 'active')
        self.assertIn('pi-launcher', result.stderr + self.resolve(operation='resume', session=self.contract()['session']).stderr)

    def test_missing_or_archived_session_is_refused_not_replaced(self):
        self.prepare()
        self.launch()
        session = self.contract()['session']
        archive = self.agent / 'sessions-archive' / '20261124'
        archive.mkdir(parents=True)
        self.session_file().rename(archive / 'moved.jsonl')
        self.assertIn('archived', self.resolve(operation='resume', session=session).stderr)
        self.step('build', 'resume', ok=False)
        (archive / 'moved.jsonl').unlink()
        self.assertIn('missing', self.resolve(operation='resume', session=session).stderr)
        self.step('build', 'resume', ok=False)
        self.assertEqual(len(self.calls()), 1)

    def test_unavailable_or_implicit_pins_refuse_before_pi_starts(self):
        refusals = {
            'test-provider/unknown-v1:low': "not in Pi's local catalog",
            'no-auth-provider/m:low': 'no stored Pi credential',
            'test-provider/pinned-v1': 'thinking level explicitly',
            'test-provider/plain-v1:high': 'does not reason',
        }
        for index, (pin, reason) in enumerate(refusals.items()):
            with self.subTest(pin=pin):
                self.prepare(f'build{index}', pin)
                result = self.step(f'build{index}', 'launch', ok=False)
                self.assertEqual(self.contract(f'build{index}')['state'], 'prepared', result.stderr)
                self.assertIn(reason, self.resolve(pin).stderr)
        self.assertEqual(self.calls(), [])
        (self.agent / 'auth.json').unlink()
        self.assertIn('missing', self.resolve().stderr)

    def test_resolve_opens_no_connection_and_starts_no_process(self):
        self.prepare()
        guard = ('import runpy, socket, subprocess, sys\n'
                 'def refuse(*a, **k): raise AssertionError("resolve tried to reach out")\n'
                 'socket.socket = socket.create_connection = subprocess.Popen = refuse\n'
                 'sys.argv = sys.argv[1:]\n'
                 'runpy.run_path(sys.argv[0], run_name="__main__")\n')
        argv = [PYTHON, '-c', guard, str(self.adapter), 'resolve', '--for-operation', 'launch',
                '--model', PIN, '--report', str(self.report), '--contract', str(self.root / 'runs/r/steps/build.json')]
        dead = {'HTTPS_PROXY': 'http://127.0.0.1:9', 'HTTP_PROXY': 'http://127.0.0.1:9', 'ALL_PROXY': 'http://127.0.0.1:9'}
        result = subprocess.run(argv, cwd=self.root, capture_output=True, text=True, env={**self.env, **dead})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'protocol': 'tink-sdlc-launcher', 'api_version': 1,
                                                      'operation': 'launch', 'model': PIN})
        self.assertEqual(self.calls(), [])

    def test_a_run_answering_on_another_model_is_stopped_and_left_uncertain(self):
        self.prepare()
        self.launch()
        started = time.monotonic()
        result = self.step('build', 'resume', ok=False,
                           env={'FAKE_PI_ANSWER': 'test-provider/default-v1', 'FAKE_PI_LINGER': '30'})
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(self.contract()['state'], 'uncertain', result.stderr)
        pid = int(Path(str(self.pi_log) + '.pid').read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            self.fail('the drifting Pi run was not stopped')
        self.step('build', 'resume', ok=False)
        self.assertEqual(len(self.calls()), 2)

    def test_a_resumed_turn_still_thinking_is_admitted_on_its_recorded_prompt(self):
        # A long first turn on resume used to strand the step `uncertain` with Pi still working.
        self.prepare()
        self.launch()
        self.step('build', 'resume', env={'FAKE_PI_THINK': '5', 'TINK_SDLC_PI_ADMISSION': '1'})
        self.assertEqual(self.contract()['state'], 'active')
        for _ in range(100):
            if self.session_file().read_text().count('"assistant"') == 2:
                break
            time.sleep(0.1)
        self.report.with_name('build.md').write_text('TEST handoff')
        self.step('build', 'complete')
        self.assertEqual(len(self.calls()), 2)

    def test_a_resume_that_never_records_its_prompt_stays_uncertain(self):
        self.prepare()
        self.launch()
        quiet = self.bin / 'TEST-quiet-pi'
        quiet.write_text(f'#!{PYTHON}\nimport time\ntime.sleep(5)\n')
        quiet.chmod(0o755)
        self.step('build', 'resume', ok=False, env={'TINK_SDLC_PI': str(quiet), 'TINK_SDLC_PI_ADMISSION': '1'})
        self.assertEqual(self.contract()['state'], 'uncertain')

    def test_pi_failing_before_admission_leaves_the_step_uncertain(self):
        self.prepare()
        self.step('build', 'launch', ok=False, env={'FAKE_PI_EXIT': '3'})
        self.assertEqual(self.contract()['state'], 'uncertain')
        self.step('build', 'launch', ok=False)
        self.assertEqual(len(self.calls()), 1)
