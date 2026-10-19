"""Real zero-call Seed Me CLI, pinned to Seed Me main c9d766760dbeac0c05c92108165f9e968bec0a66.

session.py is a byte-identical upstream test fixture, not a second runtime implementation.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HELPER = Path(__file__).parent / 'fixtures/seed-me-c9d7667/scripts/session.py'
FRAME = HELPER.parents[1] / 'seed_fixture.py'
_spec = importlib.util.spec_from_file_location('pinned_seed_fixture', FRAME)
_frame = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_frame)
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
    checks = cli('seed', 'checks', 'render', directory)
    decisions = cli('seed', 'decisions', directory)
    text = _frame.contract_text('status: draft', profile='full', checks=checks, decisions=decisions)
    if template is not None:
        # Explicit TEST-only new draft, not a migration: retain original section prose,
        # reorder into the pinned Full frame, and replace checks/decisions/confirmation.
        original = template.decode('utf-8').replace('status: confirmed for intake', 'status: draft', 1)
        preamble, *blocks = original.split('\n## ')
        text = '\n'.join(line for line in preamble.split('\n') if not line.startswith('Confirmed by:')) + '\n\n'
        sections = {}
        for block in blocks:
            title, _, body = block.partition('\n')
            sections['## ' + title] = body
        for heading in _frame.FULL_HEADINGS:
            if heading == '## Acceptance criteria':
                text += checks
            elif heading == '## Decisions (from the ledger)':
                text += decisions + '\n'
            elif heading != '## Assumed (not confirmed by you)':
                text += heading + '\n' + sections[heading].rstrip('\n') + '\n\n'
    seed.write_text(text + extra)
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
