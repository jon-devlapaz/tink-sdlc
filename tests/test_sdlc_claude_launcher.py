"""The bundled Claude Code launcher adapter, driven through sdlc.py step and its own CLI with a fake Claude Code."""
import json
import os
import subprocess
import time
from pathlib import Path

from test_sdlc_stage import StageBase, PYTHON


# A stand-in for Claude Code: records argv and environment, answers `auth status`, prints the
# stream-json init event and writes transcripts where Claude Code does. It answers on --model
# unless told to start or answer on another model, pause after a tool, or hit a usage limit.
FAKE_CLAUDE = '''#!{python}
import json, os, re, sys, time, uuid
from pathlib import Path
argv = sys.argv[1:]
with open(os.environ['FAKE_CLAUDE_LOG'], 'a') as log:
    log.write(json.dumps({{'argv': argv, 'env': {{k: v for k, v in os.environ.items()
                           if k.startswith(('CLAUDE', 'TINK'))}}}}) + '\\n')
if argv[:2] == ['auth', 'status']:
    print(json.dumps({{'loggedIn': not os.environ.get('FAKE_CLAUDE_SIGNED_OUT'), 'authMethod': 'claude.ai'}}))
    raise SystemExit(0)
Path(os.environ['FAKE_CLAUDE_LOG'] + '.pid').write_text(str(os.getpid()))
def flag(name):
    return argv[argv.index(name) + 1] if name in argv else None
if os.environ.get('FAKE_CLAUDE_EXIT'):
    raise SystemExit(int(os.environ['FAKE_CLAUDE_EXIT']))
session = flag('--session-id') or flag('--resume')
model = flag('--model')
print(json.dumps({{'type': 'system', 'subtype': 'init', 'session_id': session,
                  'model': os.environ.get('FAKE_CLAUDE_START', model)}}), flush=True)
projects = Path(os.environ['CLAUDE_CONFIG_DIR']) / 'projects' / re.sub('[^A-Za-z0-9]', '-', os.getcwd())
projects.mkdir(parents=True, exist_ok=True)
path = projects / (session + '.jsonl')
rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
parent = next((r['uuid'] for r in reversed(rows) if r.get('uuid')), None)
def entry(kind, **fields):
    global parent
    row = {{'type': kind, 'uuid': str(uuid.uuid4()), 'parentUuid': parent, 'sessionId': session,
           'cwd': os.getcwd(), 'isSidechain': False, **fields}}
    parent = row['uuid']
    return row
def write(new):
    with path.open('a') as stream:
        stream.write(''.join(json.dumps(r) + '\\n' for r in new))
write([{{'type': 'queue-operation'}}, entry('user', message={{'role': 'user', 'content': 'TEST'}}),
       entry('attachment')])
time.sleep(float(os.environ.get('FAKE_CLAUDE_THINK', '0.3')))
answer = os.environ.get('FAKE_CLAUDE_ANSWER', model)
if os.environ.get('FAKE_CLAUDE_LIMIT'):
    write([entry('assistant', isApiErrorMessage=True,
                 message={{'role': 'assistant', 'model': '<synthetic>', 'stop_reason': 'stop_sequence'}})])
    print(json.dumps({{'type': 'result', 'subtype': 'success', 'is_error': True}}))
    raise SystemExit(1)
if os.environ.get('FAKE_CLAUDE_PAUSED'):
    # Stopped after a tool ran, before the closing answer: the shape of a killed run.
    write([entry('assistant', message={{'role': 'assistant', 'model': answer, 'stop_reason': 'tool_use'}}),
           entry('user', message={{'role': 'user', 'content': [{{'type': 'tool_result'}}]}})])
else:
    write([entry('assistant', message={{'role': 'assistant', 'model': answer, 'stop_reason': 'end_turn'}}),
           entry('attachment'), {{'type': 'last-prompt'}}])
time.sleep(float(os.environ.get('FAKE_CLAUDE_LINGER', '0')))
'''

PIN = 'anthropic/claude-sonnet-5-5:low'
MODEL = 'claude-sonnet-5-5'


class ClaudeLauncherTests(StageBase):
    def setUp(self):
        super().setUp()
        self.new_run('r')
        self.adapter = self.root / '_system/scripts/claude-launcher'
        self.config = self.home / 'TEST-claude-config'
        self.config.mkdir()
        self.claude = self.bin / 'TEST-fake-claude'
        self.claude.write_text(FAKE_CLAUDE.format(python=PYTHON))
        self.claude.chmod(0o755)
        self.claude_log = self.base / 'fake-claude.log'
        self.env.update({'CLAUDE_CONFIG_DIR': str(self.config), 'TINK_SDLC_CLAUDE': str(self.claude),
                         'FAKE_CLAUDE_LOG': str(self.claude_log),
                         # A caller that is itself a Claude Code session: the child must not adopt it.
                         'CLAUDE_CODE_SESSION_ID': 'TEST-parent-session', 'CLAUDECODE': '1'})
        self.report = self.base / 'TEST-notes' / 'build.md'
        self.report.parent.mkdir()

    def step(self, name, action, *args, ok=True, env=None):
        result = self.sdlc('step', 'r', name, action, *args, env=env)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def prepare(self, name='build', pin=PIN):
        self.step(name, 'prepare', '--report', str(self.report.with_name(name + '.md')), '--model', pin,
                  '--launcher', str(self.adapter))

    def contract(self, name='build'):
        return json.loads((self.root / f'runs/r/steps/{name}.json').read_text())

    def records(self):
        return [json.loads(line) for line in self.claude_log.read_text().splitlines()] if self.claude_log.exists() else []

    def runs(self):
        return [r for r in self.records() if r['argv'][:1] == ['-p']]

    def session_file(self, name='build'):
        [path] = self.config.glob(f"projects/*/{self.contract(name)['session']}.jsonl")
        return path

    def settle(self, name='build', answers=1):
        for _ in range(50):
            if self.session_file(name).read_text().count('"type": "assistant"') >= answers:
                return
            time.sleep(0.1)
        self.fail('the fake Claude Code never answered')

    def launch(self, name='build'):
        self.step(name, 'launch')
        self.settle(name)

    def append(self, **fields):
        rows = self.session_file().read_text().splitlines()
        parent = [json.loads(r)['uuid'] for r in rows if 'uuid' in json.loads(r)][-1]
        row = {'uuid': 'TEST-' + str(len(rows)), 'parentUuid': parent, 'isSidechain': False,
               'cwd': str(self.root), **fields}
        self.session_file().write_text('\n'.join(rows + [json.dumps(row)]) + '\n')

    def resolve(self, pin=PIN, operation='launch', session=None, env=None):
        argv = [str(self.adapter), 'resolve', '--for-operation', operation, '--model', pin,
                '--report', str(self.report), '--contract', str(self.root / 'runs/r/steps/build.json')]
        argv += ['--session', session] if session else []
        return subprocess.run(argv, cwd=self.root, capture_output=True, text=True, env={**self.env, **(env or {})})

    def test_launch_and_resume_pass_the_pin_permissions_and_a_clean_environment(self):
        self.prepare()
        self.launch()
        session = self.contract()['session']
        self.step('build', 'resume')
        self.settle(answers=2)
        launch, resume = self.runs()
        for record in (launch, resume):
            argv = record['argv']
            self.assertEqual(argv[argv.index('--model') + 1], MODEL)
            self.assertEqual(argv[argv.index('--effort') + 1], 'low')
            self.assertEqual(argv[argv.index('--permission-mode') + 1], 'acceptEdits')
            self.assertEqual(argv[argv.index('--permission-prompts') + 1], 'none')
            self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Glob,Grep,Edit,Write,Bash')
            allowed = argv[argv.index('--allowedTools') + 1:argv.index('--output-format')]
            self.assertEqual(allowed, ['Read', 'Glob', 'Grep', 'Edit', 'Write', "Bash(python3 -c pass)"])
            self.assertNotIn('--fallback-model', argv)
            self.assertNotIn('--fork-session', argv)
            self.assertEqual(record['env'].get('TINK_SDLC_STEP_SESSION'), '1')
            self.assertEqual(record['env'].get('CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'), '1')
            self.assertNotIn('CLAUDE_CODE_SESSION_ID', record['env'])
            self.assertNotIn('CLAUDECODE', record['env'])
            prompt = argv[-1]
            self.assertIn(str(self.root / 'runs/r/brief.md'), prompt)
            self.assertIn(str(self.root / 'stages/03-build/CONTEXT.md'), prompt)
            self.assertIn(str(self.report.with_name('build.md')), prompt)
            self.assertIn('Never run sdlc.py step, stage or decide', prompt)
        self.assertEqual(launch['argv'][launch['argv'].index('--session-id') + 1], session)
        self.assertEqual(resume['argv'][resume['argv'].index('--resume') + 1], session)
        self.report.with_name('build.md').write_text('TEST handoff')
        self.step('build', 'complete')
        self.assertEqual(self.contract()['state'], 'completed')

    def test_only_the_runs_own_checks_are_allowlisted_and_unsafe_ones_refuse(self):
        policy = self.root / '_system/verification.json'
        policy.write_text(json.dumps({'require_tink': False, 'checks': [
            {'argv': ['python3', '-m', 'unittest', 'discover'], 'timeout_seconds': 60}]}))
        self.prepare()
        self.launch()
        argv = self.runs()[0]['argv']
        self.assertEqual(argv[argv.index('--allowedTools') + 1:argv.index('--output-format')],
                         ['Read', 'Glob', 'Grep', 'Edit', 'Write', 'Bash(python3 -m unittest discover)',
                          'Bash(python3 -c pass)'])
        policy.write_text(json.dumps({'require_tink': False, 'checks': [
            {'argv': ['sh', '-c', 'echo (x)'], 'timeout_seconds': 60}]}))
        self.prepare('build2')
        self.step('build2', 'launch', ok=False)
        self.assertEqual(self.contract('build2')['state'], 'prepared')
        self.assertIn('cannot be allowlisted', self.resolve().stderr)
        self.assertEqual(len(self.runs()), 1)

    def test_completion_waits_for_a_finished_pinned_turn_and_resume_can_finish_it(self):
        self.prepare()
        self.step('build', 'launch', env={'FAKE_CLAUDE_PAUSED': '1'})
        self.settle()
        self.report.with_name('build.md').write_text('TEST handoff left by a paused run')
        result = self.step('build', 'complete', ok=False)
        self.assertIn('not finished', result.stderr)
        self.assertEqual(self.contract()['state'], 'active')
        self.step('build', 'resume')
        self.settle(answers=2)
        self.step('build', 'complete')
        self.assertEqual(self.contract()['state'], 'completed')

    def test_a_usage_limit_is_not_finished_but_can_be_resumed(self):
        # Claude Code reports subtype "success" here; only the synthetic entry tells the truth.
        self.prepare()
        self.step('build', 'launch', env={'FAKE_CLAUDE_LIMIT': '1'})
        self.settle()
        self.report.with_name('build.md').write_text('TEST handoff')
        self.step('build', 'complete', ok=False)
        self.assertEqual(self.contract()['state'], 'active')
        self.step('build', 'resume')
        self.settle(answers=2)
        self.step('build', 'complete')

    def test_a_final_answer_off_the_pin_cannot_complete_or_resume(self):
        self.prepare()
        self.launch()
        self.append(type='assistant', message={'role': 'assistant', 'model': 'claude-haiku-5-5',
                                               'stop_reason': 'end_turn'})
        self.report.with_name('build.md').write_text('TEST handoff')
        self.step('build', 'complete', ok=False)
        self.step('build', 'resume', ok=False)
        self.assertEqual(self.contract()['state'], 'active')
        self.assertIn('not the pin', self.resolve(operation='resume', session=self.contract()['session']).stderr)
        self.assertEqual(len(self.runs()), 1)

    def test_a_run_starting_on_another_model_is_stopped_and_left_uncertain(self):
        self.prepare()
        result = self.step('build', 'launch', ok=False,
                           env={'FAKE_CLAUDE_START': 'claude-haiku-5-5', 'FAKE_CLAUDE_LINGER': '30'})
        self.assertEqual(self.contract()['state'], 'uncertain', result.stderr)
        pid = int(Path(str(self.claude_log) + '.pid').read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            self.fail('the drifting Claude Code run was not stopped')

    def test_unavailable_implicit_or_unsafe_pins_refuse_before_claude_starts(self):
        self.prepare()
        refusals = {
            'anthropic/sonnet:low': 'aliases are refused',
            'anthropic/claude-unknown-9:low': 'not a reviewed exact model ID',
            'anthropic/claude-sonnet-5-5': 'effort explicitly',
            'openai/claude-sonnet-5-5:low': 'no other provider',
        }
        for index, (pin, reason) in enumerate(refusals.items()):
            with self.subTest(pin=pin):
                self.prepare(f'build{index}', pin)
                result = self.step(f'build{index}', 'launch', ok=False)
                self.assertEqual(self.contract(f'build{index}')['state'], 'prepared', result.stderr)
                self.assertIn(reason, self.resolve(pin).stderr)
        self.assertIn('not signed in', self.resolve(env={'FAKE_CLAUDE_SIGNED_OUT': '1'}).stderr)
        self.assertIn('fallback', self.resolve(env={'ANTHROPIC_FALLBACK_MODEL': 'x'}).stderr)
        (self.config / 'settings.json').write_text(json.dumps({'fallbackModel': 'claude-haiku-5-5'}))
        self.assertIn('fallback', self.resolve().stderr)
        self.assertEqual(self.runs(), [])

    def test_resolve_opens_no_connection_and_runs_only_auth_status(self):
        self.prepare()
        guard = ('import runpy, socket, sys\n'
                 'def refuse(*a, **k): raise AssertionError("resolve tried to reach out")\n'
                 'socket.socket = socket.create_connection = refuse\n'
                 'sys.argv = sys.argv[1:]\n'
                 'runpy.run_path(sys.argv[0], run_name="__main__")\n')
        argv = [PYTHON, '-c', guard, str(self.adapter), 'resolve', '--for-operation', 'launch',
                '--model', PIN, '--report', str(self.report), '--contract', str(self.root / 'runs/r/steps/build.json')]
        dead = {'HTTPS_PROXY': 'http://127.0.0.1:9', 'HTTP_PROXY': 'http://127.0.0.1:9', 'ALL_PROXY': 'http://127.0.0.1:9'}
        result = subprocess.run(argv, cwd=self.root, capture_output=True, text=True, env={**self.env, **dead})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'protocol': 'tink-sdlc-launcher', 'api_version': 1,
                                                      'operation': 'launch', 'model': PIN})
        self.assertEqual([r['argv'] for r in self.records()], [['auth', 'status']])

    def test_missing_or_foreign_sessions_are_refused_not_replaced(self):
        self.prepare()
        self.launch()
        session = self.contract()['session']
        path = self.session_file()
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        path.write_text(''.join(json.dumps({**r, 'cwd': '/TEST-elsewhere'} if 'cwd' in r else r) + '\n' for r in rows))
        self.assertIn('ran outside', self.resolve(operation='resume', session=session).stderr)
        path.unlink()
        self.assertIn('missing', self.resolve(operation='resume', session=session).stderr)
        self.step('build', 'resume', ok=False)
        self.assertEqual(len(self.runs()), 1)

    def test_claude_failing_before_admission_leaves_the_step_uncertain(self):
        self.prepare()
        self.step('build', 'launch', ok=False, env={'FAKE_CLAUDE_EXIT': '3'})
        self.assertEqual(self.contract()['state'], 'uncertain')
        self.step('build', 'launch', ok=False)
        self.assertEqual(len(self.runs()), 1)
