"""Real zero-call Seed Me CLI, pinned to PR #17 26e1c3d3f7691d9ae043614f9a30abda5b5bceac.

session.py is a byte-identical upstream test fixture, not a second runtime implementation.
"""
import json
from pathlib import Path
import subprocess
import sys

HELPER = Path(__file__).parent / 'fixtures/seed-me-26e1c3d/scripts/session.py'
DEFAULT = [{'statement': 'Fixture goal', 'kind': 'judge', 'source': 'TEST operator'}]


def cli(*args):
    result = subprocess.run([sys.executable, str(HELPER), *map(str, args)], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout


def confirmed(root, records=None, extra='', template=None):
    directory = Path(json.loads(cli('init', '--root', root, '--operator', 'human'))['session'])
    version = json.loads(cli('read', directory))['version']
    node = {'id': 'goal', 'kind': 'decision', 'status': 'settled', 'prerequisites': [], 'evidence': [],
            'owner': 'TEST operator', 'gate': 'choose', 'answer': 'TEST goal', 'authority': 'user',
            'authority_source': 'TEST explicit approval'}
    state = {'status': 'active', 'draft': {'goal': 'TEST goal', 'outcome': 'TEST outcome', 'options': ['A', 'B']},
             'goal': 'TEST goal', 'origin': 'goal', 'current_question': None, 'nodes': [node], 'operator': 'human'}
    payload = directory / 'TEST-input.json'
    payload.write_text(json.dumps({'state': state, 'expected_version': version, 'reason': 'TEST answer'}))
    cli('publish', directory, payload)
    payload.write_text(json.dumps(records if records is not None else DEFAULT))
    cli('seed', 'checks', 'set', directory, payload)
    seed = directory / 'seed-contract.md'
    seed.write_text('status: draft\nrevision: r1\n# TEST seed\n## You are confirming\nTEST goal\n'
                    + cli('seed', 'checks', 'render', directory)
                    + cli('seed', 'decisions', directory) + extra)
    if template is not None:
        # Explicit TEST-only new draft: retain the real output's other sections, not its old approval.
        text = template.decode('utf-8').replace('status: confirmed for intake', 'status: draft', 1)
        text = '\n'.join(line for line in text.split('\n') if not line.startswith('Confirmed by:'))
        before, acceptance = text.split('## Acceptance criteria\n', 1)
        _, after = acceptance.split('## Affected users and systems\n', 1)
        text = before + cli('seed', 'checks', 'render', directory) + '## Affected users and systems\n' + after
        text = text.split('## Decisions (from the ledger)\n', 1)[0] + cli('seed', 'decisions', directory)
        seed.write_text(text)
    cli('seed', 'confirm', directory, '--revision', 'r1', '--source', 'TEST explicit confirmation')
    return seed


def command(cmd='true', cwd='.'):
    return {'statement': 'Independent check', 'kind': 'cmd', 'source': 'TEST operator',
            'cmd': cmd, 'cwd': cwd, 'expect': 'exit zero; output looks right'}


def provisional(cmd=None):
    value = {'statement': 'Future check', 'kind': 'provisional', 'source': 'TEST operator',
             'cwd': '.', 'expect': 'works', 'missing': 'executable exists'}
    if cmd is not None:
        value['cmd'] = cmd
    return value
