#!/usr/bin/env python3
"""Replay literal TEST sessions and command outputs recorded from Tinkery 785a693.

This is a finite command stub, not a seed verifier. Unknown inputs refuse; JSON
formatting alone is ignored. See fixtures/tinkery-785a693/record.py for provenance.
"""
import json
from pathlib import Path
import subprocess
import sys

FIXTURES = Path(__file__).parent / 'fixtures/tinkery-785a693/recorded.json'
TINKERY = Path(__file__).resolve()
DEFAULT = [{'statement': 'Fixture goal', 'kind': 'judge', 'source': 'TEST operator'}]


def command(cmd='true', cwd='.'):
    return {'statement': 'Independent check', 'kind': 'cmd', 'source': 'TEST operator',
            'cmd': cmd, 'cwd': cwd, 'expect': 'exit zero; output looks right'}


def provisional(cmd=None):
    value = {'statement': 'Future check', 'kind': 'provisional', 'source': 'TEST operator',
             'cwd': '.', 'expect': 'works', 'missing': 'executable exists'}
    if cmd is not None:
        value['cmd'] = cmd
    return value


def corpus():
    return json.loads(FIXTURES.read_text())


def install(directory, files):
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        target = directory / name
        if text is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(text)
    return directory / 'seed-contract.md'


def confirmed(root, records=None, extra='', template=None):
    request = {'records': DEFAULT if records is None else records, 'extra': extra,
               'template': template.decode('utf-8') if template is not None else None}
    for case in corpus()['cases']:
        if case['request'] == request:
            return install(Path(root) / 'TEST-session', case['files'])
    raise AssertionError('No Rust-confirmed TEST fixture for this request; record it first.')


def changed(seed, name):
    """Install a recorded refusal input, retaining its real Rust-produced status."""
    case = corpus()['refusals'][name]
    return install(seed.parent, case['files'])


def cli(*args):
    result = subprocess.run([sys.executable, str(TINKERY), *map(str, args)], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout


def normalized(files):
    return {name: json.loads(text) if text is not None and name.endswith('.json') else text
            for name, text in files.items()}


def main(args):
    if len(args) < 3 or args[:-1] not in (['seed', 'status', '--json'], ['seed', 'checks']):
        sys.exit('TEST stub refuses unexpected command; use exact Tinkery seed commands.')
    directory = Path(args[-1])
    data = corpus()
    cases = data['cases'] + list(data['refusals'].values())
    for case in cases:
        actual = {name: (directory / name).read_text() if (directory / name).is_file() else None
                  for name in case['files']}
        if normalized(actual) == normalized(case['files']):
            result = case['status' if args[1] == 'status' else 'checks']
            sys.stdout.write(result['stdout'])
            sys.stderr.write(result['stderr'])
            return result['returncode']
    sys.exit('TEST stub has no recorded Tinkery response for these session bytes.')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
