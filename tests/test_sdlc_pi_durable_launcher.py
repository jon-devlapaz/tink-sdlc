"""The bundled Pi Durable launcher, driven through sdlc.py step with the pinned worker and an offline stub provider.

The worker runs the real pinned pi-durable packages. Only the provider is replaced, by
tests/fixtures/pi-durable/stub-models.mjs, so no test opens a connection or spends a call.
The pinned packages are installed once per test process with the launcher's own `install`.
"""
import atexit
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from test_sdlc_stage import StageBase, ROOT

PIN = 'stub/build:low'
STUB = ROOT / 'tests/fixtures/pi-durable/stub-models.mjs'
NODE = shutil.which('node')
NPM = shutil.which('npm')
_installed = None


def installed_home():
    """One install of the pinned bundle for this test process, through the launcher's own command."""
    global _installed
    if _installed is None:
        folder = tempfile.TemporaryDirectory(prefix='tink-sdlc-TEST-pi-durable-')
        atexit.register(folder.cleanup)
        env = {**os.environ, 'TINK_SDLC_PI_DURABLE_HOME': folder.name}
        result = subprocess.run([str(ROOT / 'assets/_system/scripts/pi-durable-launcher'), 'install'],
                                capture_output=True, text=True, env=env, timeout=600)
        assert result.returncode == 0, result.stdout + result.stderr
        [install] = [p for p in Path(folder.name).iterdir() if (p / '.installed').is_file()]
        # The stub imports pi-ai by name, so it must sit beside the installed packages.
        shutil.copyfile(STUB, install / 'TEST-stub-models.mjs')
        _installed = (Path(folder.name), install)
    return _installed


@unittest.skipUnless(NODE and NPM, 'Pi Durable needs Node 22.19 or later and npm')
class PiDurableLauncherTests(StageBase):
    def setUp(self):
        super().setUp()
        home, install = installed_home()
        self.new_run('r')
        self.adapter = self.root / '_system/scripts/pi-durable-launcher'
        agent = self.home / 'TEST-pi-agent'
        agent.mkdir()
        (agent / 'models-store.json').write_text(json.dumps({'stub': {'models': [
            {'id': 'build', 'provider': 'stub', 'reasoning': True}]}}))
        (agent / 'auth.json').write_text(json.dumps({'stub': {'type': 'api_key', 'key': 'TEST-offline'}}))
        tools = self.base / 'node-tools'
        tools.mkdir()
        (tools / 'node').symlink_to(NODE)
        self.ledger = self.base / 'stub-ledger.jsonl'
        self.script = self.base / 'stub-script.json'
        self.report = self.base / 'TEST-notes' / 'build.md'
        self.report.parent.mkdir()
        self.marker = self.root / 'TEST-marker'
        self.env.update({'PATH': f'{tools}:{self.env["PATH"]}', 'PI_CODING_AGENT_DIR': str(agent),
                         'TINK_SDLC_PI_DURABLE_HOME': str(home),
                         'TINK_SDLC_PI_DURABLE_STUB': str(install / 'TEST-stub-models.mjs'),
                         'TEST_STUB_LEDGER': str(self.ledger), 'TEST_STUB_SCRIPT': str(self.script)})

    def stub(self, *responses):
        self.script.write_text(json.dumps(list(responses)))

    def step(self, action, *args, ok=True):
        result = self.sdlc('step', 'r', 'build', action, *args)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def prepare(self, cap):
        self.step('prepare', '--report', str(self.report), '--model', PIN, '--launcher', str(self.adapter),
                  '--max-responses', str(cap))

    def contract(self):
        return json.loads((self.root / 'runs/r/steps/build.json').read_text())

    def requests(self):
        return [json.loads(line) for line in self.ledger.read_text().splitlines()] if self.ledger.exists() else []

    def status_step(self):
        [step] = json.loads(self.sdlc('status', 'r', '--json').stdout)['run']['steps']
        return step

    def wait(self, condition, what, seconds=30):
        deadline = time.monotonic() + seconds
        while not condition():
            if time.monotonic() > deadline:
                self.fail(f'{what} did not happen')
            time.sleep(0.1)

    def gone(self, group):
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return True
        except PermissionError:  # macOS answers this for a group whose members are not yet reaped
            return False
        return False

    def test_a_worker_killed_during_a_shell_command_resumes_its_same_submission_without_rerunning_it(self):
        shell = self.base / 'TEST-shell.pid'
        self.stub({'bash': f'printf "ran\\n" >> {self.marker}; echo $$ > {shell}; sleep 60'},
                  {'text': 'The interrupted command was not repeated.'})
        self.prepare(cap=2)
        self.step('launch')
        launched = self.contract()
        group = launched['process_group']
        self.addCleanup(lambda: self.gone(group) or os.killpg(group, signal.SIGKILL))
        self.wait(lambda: shell.is_file(), 'the shell command')
        self.assertEqual(self.status_step()['turn'], 'not-ended')
        self.assertNotIn('worker_gone', self.status_step(), 'a live worker read as gone')
        # tink-sdlc#225 review: a second worker on a live worker's store would drive its run twice.
        self.assertIn('still running; resume refused', self.step('resume', ok=False).stderr)
        self.assertEqual(self.contract()['state'], 'active')
        self.assertEqual(len(self.requests()), 1, 'a live worker was resumed')
        os.killpg(group, signal.SIGKILL)
        self.wait(lambda: self.gone(group), 'the kill')
        # Pi Durable runs the command in a process group of its own; it must not outlive the worker.
        command = int(shell.read_text())
        def ended():
            try:
                os.kill(command, 0)
            except ProcessLookupError:
                return True
            return subprocess.run(['ps', '-o', 'stat=', '-p', str(command)], capture_output=True,
                                  text=True).stdout.strip().startswith('Z')
        self.wait(ended, 'the end of the interrupted command', seconds=5)
        self.assertTrue(self.status_step().get('worker_gone'))
        self.step('resume')
        resumed = self.contract()
        for field in ('session', 'checkout', 'model', 'max_responses', 'launcher_sha256'):
            self.assertEqual(resumed[field], launched[field], field)
        # The second answer ends the turn and uses the cap of two, so the step reads as capped.
        self.wait(lambda: self.status_step().get('response_cap_reached'), 'the resumed answer')
        self.assertEqual(self.marker.read_text(), 'ran\n', 'the interrupted command ran again')
        requests = self.requests()
        self.assertEqual([r['interrupted'] for r in requests], [False, True])
        directory = Path(Path(self.root / 'runs/r/steps/build.session-dir').read_text())
        admissions = [json.loads(p.read_text()) for p in sorted(directory.glob('*.admitted'))]
        self.assertEqual(len({a['submission'] for a in admissions}), 1, 'resume admitted a second submission')
        self.assertIn('already holds its cap of 2', self.step('resume', ok=False).stderr)
        self.assertEqual(len(self.requests()), 2, 'a refused resume reached the provider')
        self.step('complete', '--stopped-by', 'cap')
        self.assertEqual(self.contract()['stopped_by'], 'cap')

    def test_the_cap_stops_a_live_worker_and_records_its_last_shell_command(self):
        self.stub({'bash': f'printf "one\\n" >> {self.marker}'}, {'bash': 'sleep 60'})
        self.prepare(cap=2)
        self.step('launch')
        group = self.contract()['process_group']
        self.addCleanup(lambda: self.gone(group) or os.killpg(group, signal.SIGKILL))
        self.wait(lambda: self.gone(group), 'the cap stop')
        [log] = (self.root / 'runs/r/steps').glob('build.launch-*.pi.log')
        self.wait(lambda: 'response cap 2 reached' in log.read_text(), 'the cap marker')
        step = self.status_step()
        self.assertTrue(step['response_cap_reached'])
        self.assertEqual(step['cap_stop']['shell']['command'], f'printf "one\\n" >> {self.marker}')
        header = json.loads(log.read_text().splitlines()[0])
        self.assertEqual((header['type'], header['id']), ('session', self.contract()['session']))
        self.step('complete', '--stopped-by', 'cap')
        self.assertEqual(self.contract()['stopped_by'], 'cap')

    def test_refusals_come_before_any_worker_starts(self):
        self.stub({'text': 'TEST'})
        self.step('prepare', '--report', str(self.report), '--model', 'stub/missing:low',
                  '--launcher', str(self.adapter), '--max-responses', '2')
        self.assertIn("not in Pi's local catalog cache", self.step('launch', ok=False).stderr)
        self.assertEqual(self.requests(), [])
        empty = self.base / 'TEST-empty-home'
        result = self.run_cmd([str(self.adapter), 'resolve', '--for-operation', 'launch', '--model', PIN,
                               '--report', str(self.report), '--contract', str(self.root / 'runs/r/steps/build.json')],
                              cwd=self.root, env={**self.env, 'TINK_SDLC_PI_DURABLE_HOME': str(empty)}, ok=False)
        self.assertIn('install once', result.stderr)
        self.assertFalse(empty.exists(), 'resolve installed something')

    def test_a_changed_pi_launcher_or_worker_is_refused(self):
        for name in ('pi-launcher', 'pi-durable/worker.mjs'):
            path = self.root / '_system/scripts' / name
            original = path.read_bytes()
            path.write_bytes(original + b'\n# TEST change\n')
            result = self.run_cmd([str(self.adapter), 'resolve', '--for-operation', 'launch', '--model', PIN,
                                   '--report', str(self.report), '--contract', str(self.root / 'runs/r/steps/build.json')],
                                  cwd=self.root, ok=False)
            self.assertIn('changed', result.stderr, name)
            path.write_bytes(original)


class PinnedDigestTests(unittest.TestCase):
    def test_the_launcher_pins_the_files_it_runs(self):
        scripts = ROOT / 'assets/_system/scripts'
        source = (scripts / 'pi-durable-launcher').read_text()
        expected = {'pi-launcher': scripts / 'pi-launcher', **{name: scripts / 'pi-durable' / name
                    for name in ('tool-shell', 'package.json', 'package-lock.json', 'worker.mjs')}}
        for name, path in expected.items():
            self.assertIn(hashlib.sha256(path.read_bytes()).hexdigest(), source, name)


if __name__ == '__main__':
    unittest.main()
